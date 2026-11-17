"""Bid Room: where a company builds its bid for one tender. The checklist comes from the notice
and the bidding document, the prose is drafted from the company profile, and the whole bid
saves as it's typed and prints as a ready-to-stamp document."""
import datetime as dt
import json
import re
import threading

import os
import time

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select

from . import bidforms, bids, export, stamping
from .db import Bid, PastBid, Paper, Tender
from . import desk as desk_mod
from . import library

MEMBERS = ("member",)
MAX_STAMP = 3_000_000
MAX_BID_PDF = 120_000_000
IMAGE_TYPES = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg"}


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
        if bids.load_profile(u.company).get("stamp_v"):  # the stamp is uploaded on its own; keep it
            d["stamp_v"] = bids.load_profile(u.company)["stamp_v"]
        u.company.profile = json.dumps(d, ensure_ascii=False)
        session.commit()
        back = str(form.get("back", ""))
        if back.startswith("/bids/"):
            return RedirectResponse(back + "?profile=1", 303)
        return RedirectResponse("/profile?saved=1", 303)

    # -- the company stamp: printed in every signature block, and on every page when the buyer asks
    stamps = os.path.join(h.data_dir, "stamps")

    def stamp_path(company_id):
        return os.path.join(stamps, f"{int(company_id)}.img")

    def stamp_bytes(company_id):
        try:
            with open(stamp_path(company_id), "rb") as f:
                return f.read()
        except OSError:
            return None

    @app.post("/profile/stamp")
    async def upload_stamp(request: Request, csrf: str = Form(""), back: str = Form(""),
                           stamp: UploadFile | None = File(None), session=Depends(db)):
        u = member(request, session)
        h.check_csrf(u, csrf)
        data = await stamp.read(MAX_STAMP + 1) if stamp else b""
        if not data or len(data) > MAX_STAMP or not any(data.startswith(k) for k in IMAGE_TYPES):
            raise HTTPException(400, "Choose a PNG or JPG picture of your stamp, under 3 MB. A PNG with a clear background prints best.")
        try:
            data = stamping.clean_stamp(data)
        except stamping.StampError as e:
            raise HTTPException(400, str(e))
        os.makedirs(stamps, exist_ok=True)
        with open(stamp_path(u.company_id), "wb") as f:
            f.write(data)
        d = bids.load_profile(u.company)
        d["stamp_v"] = str(int(time.time()))
        u.company.profile = json.dumps(d, ensure_ascii=False)
        session.commit()
        return RedirectResponse((back if back.startswith("/bids/") else "/profile?saved=1") + "#stamp", 303)

    @app.post("/profile/stamp/delete")
    def delete_stamp(request: Request, csrf: str = Form(""), session=Depends(db)):
        u = member(request, session)
        h.check_csrf(u, csrf)
        try:
            os.remove(stamp_path(u.company_id))
        except OSError:
            pass
        d = bids.load_profile(u.company)
        d.pop("stamp_v", None)
        u.company.profile = json.dumps(d, ensure_ascii=False)
        session.commit()
        return RedirectResponse("/profile?saved=1#stamp", 303)

    @app.get("/profile/stamp")
    def show_stamp(request: Request, session=Depends(db)):
        u = member(request, session)
        data = stamp_bytes(u.company_id)
        if not data:
            raise HTTPException(404, "No stamp yet.")
        return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})

    # -- stamp and number every page of a finished bid
    @app.post("/bids/{bid_id}/stamp")
    async def stamp_bid(request: Request, bid_id: int, session=Depends(db)):
        u = member(request, session)
        form = await request.form(max_files=40, max_part_size=MAX_BID_PDF)
        h.check_csrf(u, form.get("csrf"))
        b = own_bid(session, u, bid_id)
        pdfs, size = [], 0
        for up in form.getlist("pdf"):
            if not hasattr(up, "read"):
                continue
            data = await up.read(MAX_BID_PDF + 1)
            size += len(data)
            if size > MAX_BID_PDF:
                raise HTTPException(413, "The PDFs are over 120 MB together. Stamp the bid in two parts.")
            if data:
                if not data.startswith(b"%PDF"):
                    raise HTTPException(400, f"{up.filename or 'A file'} is not a PDF. Save it as PDF first.")
                pdfs.append(data)
        if form.get("papers"):  # the company file's scans this tender asks for, after the bid itself
            x = bundle(session, u, b)
            folder = os.path.join(h.data_dir, "papers", str(u.company_id))
            for r in x["env"]["items"]:
                p = x["held"].get(r["key"]) if r.get("from") == "file" else None
                if not p or not p.file:
                    continue
                try:
                    with open(os.path.join(folder, os.path.basename(p.file)), "rb") as f:
                        data = f.read()
                except OSError:
                    continue
                pdfs.append(data if data.startswith(b"%PDF") else stamping.image_pdf(data))
        if not pdfs:
            raise HTTPException(400, "Choose the PDF of your bid, or tick the company file papers.")
        prof = bids.load_profile(u.company)
        stamp = stamp_bytes(u.company_id) if form.get("stamp") else None
        footer = prof.get("legal_name", "") + (f" · {b.tender.reference}" if b.tender.reference else "")
        try:
            out, pages = stamping.stamp_pdf(pdfs, stamp, footer=footer if form.get("numbers") else "",
                                            numbers=bool(form.get("numbers")))
        except stamping.StampError as e:
            raise HTTPException(400, str(e))
        name = re.sub(r"[^A-Za-z0-9]+", "-", b.tender.title or "bid")[:50].strip("-") or "bid"
        return Response(out, media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="stamped-{name}.pdf"', "X-Pages": str(pages)})

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

    def bundle(session, u, b):
        """Everything the room and the printed bid show: the bid's forms with their filled text and rows."""
        d = bids.ensure(bids.load(b), b.tender)
        prof = bids.load_profile(u.company)
        papers = session.scalars(select(Paper).where(Paper.company_id == u.company_id)).all()
        env = desk_mod.envelope(b.tender, papers, today(), d)
        total = bids.total(d)
        values = bidforms.tokens(b.tender, prof, d, total, today())
        forms = bidforms.form_list(d)
        for f in forms:
            if f["type"] == "letter":
                f["text"] = bidforms.letter_text(d, f["key"])
                f["standard"] = bidforms.standard_text(f["key"], d["kind"])
                f["filled"] = bidforms.render(f["text"], values)
            elif f["type"] == "table":
                f["rows"] = bidforms.table_rows(d, f["key"], prof)
                f["prefill"] = bidforms.prefill_rows(f["key"], prof, d)
            elif f["type"] == "text":
                f["text"] = d.get(f["field"]) if f["field"] else ((d.get("forms") or {}).get(f["key"]) or {}).get("text", "")
                f["outline_text"] = bidforms.OUTLINES.get(f["outline"], "")
            f["done"] = bidforms.form_done(f, d, prof, values, env)
        det = d["details"]
        if det.get("security_type") == "declaration":
            values["securing_declaration"] = bidforms.render(bidforms.SECURING_DECLARATION, values)
        held = {}
        for r in env["items"]:
            if r.get("from") == "file":
                held[r["key"]] = desk_mod.best(papers, r["key"], today())
        live = [f for f in forms if f["on"] and f["type"] != "details"]
        letters = [f["filled"] for f in live if f["type"] == "letter"]
        return {"d": d, "prof": prof, "env": env, "total": total, "values": values, "forms": forms, "held": held,
                "forms_done": sum(1 for f in live if f["done"]), "forms_total": len(live),
                "confirm": bids.to_confirm(d, letters), "vat": total * bidforms.VAT_RATE
                if det.get("vat") == "add" and prof.get("vat_registered") else 0.0,
                "security_until": bidforms.security_valid_until(b.tender, det)}

    @app.get("/bids/{bid_id}", response_class=HTMLResponse)
    def room(request: Request, bid_id: int, session=Depends(db)):
        u = member(request, session)
        b = own_bid(session, u, bid_id)
        x = bundle(session, u, b)
        d, prof, env = x["d"], x["prof"], x["env"]
        pct, missing = bids.completeness(prof)
        projects = {f"p{i + 1}": p for i, p in enumerate(prof.get("projects", []))}
        activity = [("Bid started", b.created_at)]
        if d.get("drafted_at"):
            activity.append(("Drafted by Bid Room", d["drafted_at"]))
        activity.append(("Submitted" if b.state == "submitted" else "Last saved", b.updated_at))
        past = [pb for pb, _ in library.ranked(session.scalars(select(PastBid).where(
            PastBid.company_id == u.company_id, PastBid.state == "ready")).all(), b.tender)]
        return page(request, "bid.html", u, b=b, t=b.tender, d=d, prog=progress(d), pct=pct, missing=missing, past=past,
                    confirm=x["confirm"], total=x["total"], projects=projects, STATUS=bids.STATUS_LABEL,
                    drafting=b.state in ("drafting", "reading"), env=env, activity=activity, x=x, F=bidforms,
                    label=desk_mod.label, private=b.tender.status == "private")

    @app.post("/bids/{bid_id}/save")
    async def autosave(request: Request, bid_id: int, session=Depends(db)):
        u = member(request, session)
        h.check_csrf(u, request.headers.get("x-csrf", ""))
        b = own_bid(session, u, bid_id)
        if b.state in ("drafting", "reading") and age(b.updated_at) < 600:
            raise HTTPException(409, "Bid Room is reading or drafting this bid. Your changes can be made when it finishes.")
        try:
            incoming = await request.json()
        except ValueError:
            raise HTTPException(400, "Bad save.")
        if not isinstance(incoming, dict):
            raise HTTPException(400, "Bad save.")
        d = bids.clean_save(bids.ensure(bids.load(b), b.tender), incoming)
        b.data, b.updated_at = json.dumps(d, ensure_ascii=False), now()
        if incoming.get("state") in ("open", "submitted") and b.state not in ("drafting", "reading"):
            b.state = incoming["state"]
        session.commit()
        x = bundle(session, u, b)
        return {"ok": True, "saved": b.updated_at.astimezone(h.TZ).strftime("%H:%M"), "total": x["total"],
                "progress": progress(d), "confirm": x["confirm"], "values": x["values"],
                "forms": {f["key"]: f["done"] for f in x["forms"]}, "forms_done": x["forms_done"],
                "forms_total": x["forms_total"], "vat": x["vat"], "security_until": x["security_until"]}

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
        if b.state in ("drafting", "reading") and age(b.updated_at) < 600:  # already busy
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

    def print_page(request, bid_id, session, word=False):  # word: no screen bar or scripts
        u = member(request, session)
        b = own_bid(session, u, bid_id)
        x = bundle(session, u, b)
        only = request.query_params.get("part", "")
        return page(request, "bid_print.html", u, b=b, t=b.tender, d=x["d"], p=x["prof"], x=x, total=x["total"],
                    confirm=x["confirm"], STATUS=bids.STATUS_LABEL, num=bids._num, F=bidforms, word=word,
                    only=only if only in ("T", "F") else "", label=desk_mod.label)

    @app.get("/bids/{bid_id}/print", response_class=HTMLResponse)
    def printable(request: Request, bid_id: int, session=Depends(db)):
        return print_page(request, bid_id, session)

    @app.get("/bids/{bid_id}/download")
    def download(request: Request, bid_id: int, session=Depends(db)):
        """The whole bid as Word or PDF, at any time, to finish offline. prices=blank leaves the rates
        and totals empty for firms that keep their prices off the platform."""
        u = member(request, session)
        b = own_bid(session, u, bid_id)
        x = bundle(session, u, b)
        q = request.query_params
        fmt = "pdf" if q.get("format") == "pdf" else "docx"
        blank = q.get("prices") == "blank" or not x["total"]
        stamp = stamp_bytes(u.company_id) if q.get("stamp") != "0" else None
        pages = export.build(x, b.tender, x["prof"], today(), blank_prices=blank)
        footer = x["prof"].get("legal_name", "") + (f" · {b.tender.reference}" if b.tender.reference else "")
        if fmt == "pdf":
            data = export.to_pdf(pages, x["prof"], stamp, footer, stamp_every=q.get("every") == "1")
            kind = "application/pdf"
        else:
            data = export.to_docx(pages, x["prof"], stamp, footer)
            kind = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        name = re.sub(r"[^A-Za-z0-9]+", "-", b.tender.title or "bid")[:60].strip("-") or "bid"
        return Response(data, media_type=kind,
                        headers={"Content-Disposition": f'attachment; filename="bid-{name}.{fmt}"'})
