"""Bid Room: where a company builds its bid for one tender. The checklist comes from the notice
and the bidding document, the prose is drafted from the company profile, and the whole bid
saves as it's typed and prints as a ready-to-stamp document."""
import datetime as dt
import json
import threading

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from . import bids
from .db import Bid, PastBid, Paper, Tender
from . import desk as desk_mod
from . import library

MEMBERS = ("member",)


def age(when):
    if not when:
        return 1e9
    when = when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - when).total_seconds()


def register(app, h):
    db, need, page, today, now = h.db, h.need, h.page, h.today, h.now
    app.state.bid_room = h  # tests set h.sync_drafts

    def member(request, session):
        u = need(request, session, MEMBERS)
        if not h.has_access(u):
            raise HTTPException(402, "Your bids and company profile are kept. Choose Win on your account page "
                                     "to keep building bids: the checklist, the drafted bid and the print-ready copy.")
        return u

    def own_bid(session, u, bid_id):
        b = session.get(Bid, bid_id)
        if not b or b.company_id != u.company_id:
            raise HTTPException(404, "No such bid.")
        return b

    # -- company profile: filled once, used by every bid
    @app.get("/profile", response_class=HTMLResponse)
    def profile(request: Request, session=Depends(db)):
        u = member(request, session)
        d = bids.load_profile(u.company)
        pct, missing = bids.completeness(d)
        return page(request, "profile.html", u, d=d, pct=pct, missing=missing, ROWS=bids.ROWS,
                    saved=request.query_params.get("saved"), back=request.query_params.get("back", ""))

    @app.post("/profile")
    async def save_profile(request: Request, session=Depends(db)):
        u = member(request, session)
        form = await request.form()
        h.check_csrf(u, form.get("csrf"))
        d = bids.profile_from_form(form, u.company)
        u.company.profile = json.dumps(d, ensure_ascii=False)
        session.commit()
        back = str(form.get("back", ""))
        if back.startswith("/bids/"):
            return RedirectResponse(back + "?profile=1", 303)
        return RedirectResponse("/profile?saved=1", 303)

    # -- bids
    @app.get("/bids", response_class=HTMLResponse)
    def my_bids(request: Request, session=Depends(db)):
        u = member(request, session)
        rows = session.scalars(select(Bid).where(Bid.company_id == u.company_id).order_by(Bid.updated_at.desc())).all()
        prog = {b.id: progress(bids.load(b)) for b in rows}
        papers = session.scalars(select(Paper).where(Paper.company_id == u.company_id)).all()
        envs = {b.id: desk_mod.envelope(b.tender, papers, today(), bids.load(b)) for b in rows}
        view = request.query_params.get("view", "")
        t0 = today()
        def bucket(b):
            if b.state == "submitted":
                return "submitted"
            if b.tender.closes and b.tender.closes < t0:
                return "closed"
            return "open"
        counts = {k: sum(1 for b in rows if bucket(b) == k) for k in ("open", "submitted", "closed")}
        shown = [b for b in rows if view not in counts or bucket(b) == view]
        shown.sort(key=lambda b: b.tender.closes or "9999")
        return page(request, "bids.html", u, rows=shown, all_rows=rows, prog=prog, envs=envs, counts=counts,
                    view=view if view in counts else "", bucket=bucket)

    def progress(d):
        items = d.get("checklist", [])
        return {"done": sum(1 for x in items if x.get("done")), "total": len(items),
                "gaps": sum(1 for x in items if x.get("status") == "gap" and not x.get("done"))}

    @app.post("/t/{tid}/bid")
    def start_bid(request: Request, tid: str, csrf: str = Form(""), session=Depends(db)):
        u = member(request, session)
        h.check_csrf(u, csrf)
        t = session.get(Tender, tid)
        if not t or t.status != "published":
            raise HTTPException(404, "This tender isn't listed any more.")
        b = session.scalars(select(Bid).where(Bid.company_id == u.company_id, Bid.tender_id == tid)).first()
        if not b:
            b = Bid(company_id=u.company_id, tender_id=tid, created_by=u.id, data=json.dumps(bids.blank()))
            session.add(b)
            session.commit()
        return RedirectResponse(f"/bids/{b.id}", 303)

    @app.get("/bids/{bid_id}", response_class=HTMLResponse)
    def room(request: Request, bid_id: int, session=Depends(db)):
        u = member(request, session)
        b = own_bid(session, u, bid_id)
        d = bids.load(b)
        prof = bids.load_profile(u.company)
        pct, missing = bids.completeness(prof)
        projects = {f"p{i + 1}": x for i, x in enumerate(prof.get("projects", []))}
        papers = session.scalars(select(Paper).where(Paper.company_id == u.company_id)).all()
        env = desk_mod.envelope(b.tender, papers, today(), d)
        activity = [("Bid started", b.created_at)]
        if d.get("drafted_at"):
            activity.append(("Drafted by Bid Room", d["drafted_at"]))
        activity.append(("Submitted" if b.state == "submitted" else "Last saved", b.updated_at))
        past = [pb for pb, _ in library.ranked(session.scalars(select(PastBid).where(
            PastBid.company_id == u.company_id, PastBid.state == "ready")).all(), b.tender)]
        return page(request, "bid.html", u, b=b, t=b.tender, d=d, prog=progress(d), pct=pct, missing=missing, past=past,
                    confirm=bids.to_confirm(d), total=bids.total(d), projects=projects, STATUS=bids.STATUS_LABEL,
                    drafting=b.state == "drafting", env=env, activity=activity)

    @app.post("/bids/{bid_id}/save")
    async def autosave(request: Request, bid_id: int, session=Depends(db)):
        u = member(request, session)
        h.check_csrf(u, request.headers.get("x-csrf", ""))
        b = own_bid(session, u, bid_id)
        if b.state == "drafting" and age(b.updated_at) < 600:
            raise HTTPException(409, "Bid Room is drafting this bid. Your changes can be made when it finishes.")
        try:
            incoming = await request.json()
        except ValueError:
            raise HTTPException(400, "Bad save.")
        if not isinstance(incoming, dict):
            raise HTTPException(400, "Bad save.")
        d = bids.clean_save(bids.load(b), incoming)
        b.data, b.updated_at = json.dumps(d, ensure_ascii=False), now()
        if incoming.get("state") in ("open", "submitted") and b.state != "drafting":
            b.state = incoming["state"]
        session.commit()
        return {"ok": True, "saved": b.updated_at.astimezone(h.TZ).strftime("%H:%M"), "total": bids.total(d),
                "progress": progress(d), "confirm": bids.to_confirm(d)}

    def drafting_job(bid_id, profile_dict, use_past=False):
        with h.Session() as session:
            b = session.get(Bid, bid_id)
            try:
                own = library.own_wording(session, b.company_id, b.tender) if use_past else ""
                d = bids.run_draft(h.get_claude(), b.tender, profile_dict, bids.load(b),
                                   today=dt.date.fromisoformat(today()), own_wording=own)
                b.data, b.draft_error = json.dumps(d, ensure_ascii=False), ""
            except Exception as e:  # shown to the firm as a retry message; the detail stays in the log
                print(f"bid draft {bid_id} failed: {e!r}", flush=True)
                b.draft_error = "The draft didn't finish. Try again in a minute; your own text is kept."
            b.state, b.updated_at = "open", now()
            session.commit()

    @app.post("/bids/{bid_id}/draft")
    def draft(request: Request, bid_id: int, csrf: str = Form(""), doc_text: str = Form(""), use_past: str = Form(""),
              session=Depends(db)):
        u = member(request, session)
        h.check_csrf(u, csrf)
        b = own_bid(session, u, bid_id)
        if b.state == "drafting" and age(b.updated_at) < 600:  # already drafting
            return RedirectResponse(f"/bids/{b.id}", 303)
        d = bids.load(b)
        if doc_text.strip():
            d["doc_text"] = doc_text[:bids.MAX_DOC_CHARS]
        b.data, b.state, b.draft_error, b.updated_at = json.dumps(d, ensure_ascii=False), "drafting", "", now()
        session.commit()
        prof = bids.load_profile(u.company)
        if h.sync_drafts:  # tests: run in the request
            drafting_job(b.id, prof, bool(use_past))
        else:
            threading.Thread(target=drafting_job, args=(b.id, prof, bool(use_past)), daemon=True).start()
        return RedirectResponse(f"/bids/{b.id}", 303)

    @app.get("/bids/{bid_id}/state")
    def state(request: Request, bid_id: int, session=Depends(db)):
        u = member(request, session)
        b = own_bid(session, u, bid_id)
        return JSONResponse({"state": b.state, "error": b.draft_error})

    @app.get("/bids/{bid_id}/print", response_class=HTMLResponse)
    def printable(request: Request, bid_id: int, session=Depends(db)):
        u = member(request, session)
        b = own_bid(session, u, bid_id)
        d = bids.load(b)
        prof = bids.load_profile(u.company)
        projects = {f"p{i + 1}": x for i, x in enumerate(prof.get("projects", []))}
        chosen = [projects[i] for i in d.get("projects", []) if i in projects] or list(projects.values())
        return page(request, "bid_print.html", u, b=b, t=b.tender, d=d, p=prof, chosen=chosen, total=bids.total(d),
                    confirm=bids.to_confirm(d), STATUS=bids.STATUS_LABEL, num=bids._num)
