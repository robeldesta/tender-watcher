"""The admin console at /admin: the platform's own back office, separate from the customer site.
Admins and reviewers only, with two-step sign-in. Every change is written to the activity log."""
import datetime as dt
import json
import os
from collections import Counter
from urllib.parse import urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import case, func, or_, select

from . import notices
from .db import AuditLog, Company, LoginFailure, Payment, Tender, User

ADMIN, REVIEWERS = ("admin",), ("admin", "reviewer")
PER_PAGE = 50
SOURCE_KINDS = {"tw-": "Collector photo", "web-": "Buyer website", "wb-": "World Bank", "fc-": "Telegram entry"}
STATUS = {"open": "Open", "pending": "Waiting for review", "closed": "Closed", "unlisted": "Taken down",
          "rejected": "Rejected", "all": "All"}


def kind(t):
    return next((v for k, v in SOURCE_KINDS.items() if t.id.startswith(k)), "Other")


def register(app, h):
    """h: helpers from main.create_app (db, need, page, audit, paginate, today, now, credit, prices, TZ)."""
    db, need, page, audit, today, now = h.db, h.need, h.page, h.audit, h.today, h.now

    def side(session):
        return {"pending": session.scalar(select(func.count()).select_from(Tender).where(Tender.status == "pending")),
                "waiting": session.scalar(select(func.count()).select_from(Payment).where(Payment.status == "pending"))}

    def apage(request, session, u, name, nav, status=200, **ctx):
        return page(request, name, u, status=status, admin=True, nav=nav, side=side(session), **ctx)

    h.apage = apage

    def company_state(c, t):
        if c.paid_until and c.paid_until >= t:
            return "paid"
        if c.trial_until and c.trial_until >= t:
            return "trial"
        return "ended"

    # -- overview
    @app.get("/admin", response_class=HTMLResponse)
    def overview(request: Request, session=Depends(db)):
        u = need(request, session, REVIEWERS)
        h.sync(session)
        t = today()
        week = now() - dt.timedelta(days=7)
        month_start = dt.datetime.fromisoformat(t[:7] + "-01").replace(tzinfo=h.TZ)
        count = lambda q: session.scalar(select(func.count()).select_from(q.subquery()))  # noqa: E731
        open_q = select(Tender.id).where(Tender.status == "published", or_(Tender.closes == "", Tender.closes >= t))
        companies = session.scalars(select(Company)).all()
        states = Counter(company_state(c, t) for c in companies)
        revenue = {}
        for cur, amount in session.execute(select(Payment.currency, func.sum(Payment.amount)).where(
                Payment.status == "paid", Payment.paid_at >= month_start).group_by(Payment.currency)):
            revenue[cur] = amount
        k = dict(
            open=count(open_q),
            closing=count(open_q.where(Tender.closes != "", Tender.closes <= (dt.date.fromisoformat(t) + dt.timedelta(days=7)).isoformat())),
            pending=side(session)["pending"],
            new_week=count(select(Tender.id).where(Tender.status == "published", Tender.published_at >= week)),
            companies=len(companies), trials=states["trial"], paid=states["paid"], ended=states["ended"],
            signups=count(select(Company.id).where(Company.created_at >= week)),
            failed=count(select(LoginFailure.id).where(LoginFailure.at >= now() - dt.timedelta(hours=24),
                                                       LoginFailure.key.like("phone:%"))),
            revenue=revenue)
        recent = session.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(8)).all() if u.role == "admin" else []
        pays = session.scalars(select(Payment).order_by(Payment.created_at.desc()).limit(6)).all() if u.role == "admin" else []
        queue = session.scalars(select(Tender).where(Tender.status == "pending").order_by(Tender.created_at).limit(6)).all()
        return apage(request, session, u, "admin/overview.html", "overview", k=k, recent=recent, pays=pays, queue=queue)

    # -- tenders
    @app.get("/admin/tenders", response_class=HTMLResponse)
    def tenders(request: Request, status: str = "open", q: str = "", source: str = "", category: str = "",
                p: int = 1, session=Depends(db)):
        u = need(request, session, REVIEWERS)
        h.sync(session)
        t = today()
        status = status if status in STATUS else "open"
        base = select(Tender)
        by = {"open": [Tender.status == "published", or_(Tender.closes == "", Tender.closes >= t)],
              "closed": [Tender.status == "published", Tender.closes != "", Tender.closes < t],
              "pending": [Tender.status == "pending"], "unlisted": [Tender.status == "unlisted"],
              "rejected": [Tender.status == "rejected"], "all": [Tender.status != "private"]}  # firms' own uploads stay theirs
        counts = {k: session.scalar(select(func.count()).select_from(select(Tender.id).where(*v).subquery()))
                  for k, v in by.items()}
        query = base.where(*by[status])
        if q.strip():
            like = f"%{q.strip()}%"
            query = query.where(or_(Tender.title.ilike(like), Tender.buyer.ilike(like), Tender.reference.ilike(like),
                                    Tender.id == q.strip()))
        if source in SOURCE_KINDS:
            query = query.where(Tender.id.like(source + "%"))
        if category in notices.CATEGORIES:
            query = query.where(or_(Tender.category == category, Tender.categories.like(f"%,{category},%")))
        order = Tender.closes.desc() if status == "closed" else Tender.created_at.desc() if status in ("pending", "rejected", "unlisted", "all") else Tender.closes
        rows = session.scalars(query.order_by(order)).all()
        pg = h.paginate(rows, p, PER_PAGE)
        f = {k: v for k, v in dict(status=status, q=q, source=source, category=category).items() if v}
        return apage(request, session, u, "admin/tenders.html", "tenders", pg=pg, rows=pg["rows"], counts=counts,
                     f=f, qs=urlencode(f), kinds=SOURCE_KINDS, kind=kind, STATUS=STATUS)

    @app.post("/admin/tenders/{tid}/listing")
    def listing(request: Request, tid: str, to: str = Form(""), csrf: str = Form(""), back: str = Form("/admin/tenders"),
                session=Depends(db)):
        u = need(request, session, REVIEWERS)
        h.check_csrf(u, csrf)
        t = session.get(Tender, tid)
        if not t or to not in ("unlisted", "published") or (to == "published" and t.status != "unlisted") \
                or (to == "unlisted" and t.status != "published"):
            raise HTTPException(400, "That change isn't possible for this tender.")
        t.status = to
        try:  # the static board file follows the app
            if to == "unlisted":
                notices.remove_from_board(h.board_json, t.id)
            else:
                notices.publish_to_board(h.board_json, t, h.site_url, h.photos)
        except Exception as e:
            print(f"board update failed for {t.id}: {e}", flush=True)
        audit(session, u, "tender_unlisted" if to == "unlisted" else "tender_relisted", t.id, t.title[:200], request)
        session.commit()
        return RedirectResponse(back if back.startswith("/admin") else "/admin/tenders", 303)

    # -- companies
    @app.get("/admin/companies", response_class=HTMLResponse)
    def companies(request: Request, q: str = "", state: str = "", p: int = 1, session=Depends(db)):
        u = need(request, session, ADMIN)
        t = today()
        query = select(Company).order_by(Company.created_at.desc())
        if q.strip():
            like = f"%{q.strip()}%"
            query = query.where(or_(Company.name.ilike(like), Company.tin.ilike(like),
                                    Company.id.in_(select(User.company_id).where(User.phone.ilike(like)))))
        rows = session.scalars(query).all()
        st = {c.id: company_state(c, t) for c in rows}
        counts = Counter(st.values())
        if state in ("trial", "paid", "ended"):
            rows = [c for c in rows if st[c.id] == state]
        pg = h.paginate(rows, p, PER_PAGE)
        ids = [c.id for c in pg["rows"]]
        users = Counter(cid for (cid,) in session.execute(select(User.company_id).where(User.company_id.in_(ids)))) if ids else {}
        last_pay = {}
        for pay in session.scalars(select(Payment).where(Payment.company_id.in_(ids), Payment.status == "paid")
                                   .order_by(Payment.paid_at)) if ids else []:
            last_pay[pay.company_id] = pay
        f = {k: v for k, v in dict(q=q, state=state).items() if v}
        return apage(request, session, u, "admin/companies.html", "companies", pg=pg, rows=pg["rows"], st=st,
                     counts=counts, users=users, last_pay=last_pay, f=f, qs=urlencode(f))

    @app.get("/admin/companies/{cid}", response_class=HTMLResponse)
    def company(request: Request, cid: int, session=Depends(db)):
        u = need(request, session, ADMIN)
        c = session.get(Company, cid)
        if not c:
            raise HTTPException(404, "No such company.")
        people = session.scalars(select(User).where(User.company_id == c.id).order_by(User.created_at)).all()
        pays = session.scalars(select(Payment).where(Payment.company_id == c.id).order_by(Payment.created_at.desc())).all()
        log = session.scalars(select(AuditLog).where(AuditLog.target == f"company:{c.id}").order_by(AuditLog.at.desc()).limit(20)).all()
        try:
            i = json.loads(c.interests or "{}")
        except ValueError:
            i = {}
        return apage(request, session, u, "admin/company.html", "companies", c=c, people=people, pays=pays, log=log,
                     i=i, state=company_state(c, today()))

    @app.post("/admin/companies/{cid}/extend")
    def extend(request: Request, cid: int, days: int = Form(0), reason: str = Form(""), win: str = Form(""), csrf: str = Form(""),
               session=Depends(db)):
        u = need(request, session, ADMIN)
        h.check_csrf(u, csrf)
        c = session.get(Company, cid)
        if not c or not 1 <= days <= 366 or not reason.strip():
            raise HTTPException(400, "Give 1 to 366 days and a reason.")
        start = max(today(), c.paid_until or "", c.trial_until or "")
        old = c.paid_until or "none"
        c.paid_until = (dt.date.fromisoformat(start) + dt.timedelta(days=days)).isoformat()
        extra = ""
        if win:  # Bid Room too, over the same days
            wstart = max(today(), c.win_until or "", c.trial_until or "")
            c.win_until = (dt.date.fromisoformat(wstart) + dt.timedelta(days=days)).isoformat()
            c.paid_until = max(c.paid_until, c.win_until)
            extra = f", Bid Room to {c.win_until}"
        audit(session, u, "access_extended", f"company:{c.id}", f"+{days} days ({old} -> {c.paid_until}{extra}): {reason.strip()[:300]}", request)
        session.commit()
        return RedirectResponse(f"/admin/companies/{c.id}?extended=1", 303)

    @app.post("/admin/companies/{cid}/users/{uid}/toggle")
    def toggle_member(request: Request, cid: int, uid: int, csrf: str = Form(""), session=Depends(db)):
        u = need(request, session, ADMIN)
        h.check_csrf(u, csrf)
        m = session.get(User, uid)
        if not m or m.company_id != cid or m.role != "member":
            raise HTTPException(404, "No such user.")
        m.active, m.session_ver = not m.active, m.session_ver + 1
        audit(session, u, "member_on" if m.active else "member_off", f"company:{cid}", f"{m.name} {m.phone}", request)
        session.commit()
        return RedirectResponse(f"/admin/companies/{cid}", 303)

    # -- buyers and sources
    @app.get("/admin/directory", response_class=HTMLResponse)
    def directory(request: Request, q: str = "", session=Depends(db)):
        u = need(request, session, REVIEWERS)
        t = today()
        query = (select(Tender.buyer, Tender.type, func.count(), func.max(Tender.published_at),
                        func.sum(case((or_(Tender.closes >= t, Tender.closes == ""), 1), else_=0)))
                 .where(Tender.status == "published", Tender.buyer != "").group_by(Tender.buyer, Tender.type))
        if q.strip():
            query = query.where(Tender.buyer.ilike(f"%{q.strip()}%"))
        buyers = sorted(session.execute(query).all(), key=lambda r: (-r[2], r[0]))
        sources, src_error = [], ""
        path = os.environ.get("SOURCES_JSON", "/opt/tenders/sources.json")
        try:
            with open(path, encoding="utf-8") as fh:
                sources = json.load(fh).get("sources", [])
        except (OSError, ValueError):
            src_error = "The website list isn't readable from the web app on this server."
        finds = Counter(src for (src,) in session.execute(select(Tender.source).where(Tender.id.like("web-%"))))
        return apage(request, session, u, "admin/directory.html", "directory", buyers=buyers, sources=sources,
                     src_error=src_error, finds=finds, q=q, TYPES=notices.TYPES)

    # -- activity log
    @app.get("/admin/activity", response_class=HTMLResponse)
    def activity(request: Request, action: str = "", p: int = 1, session=Depends(db)):
        u = need(request, session, ADMIN)
        query = select(AuditLog).order_by(AuditLog.at.desc())
        if action:
            query = query.where(AuditLog.action == action)
        rows = session.scalars(query.limit(2000)).all()
        pg = h.paginate(rows, p, PER_PAGE)
        actions = [a for (a,) in session.execute(select(AuditLog.action).distinct().order_by(AuditLog.action))]
        f = {"action": action} if action else {}
        return apage(request, session, u, "admin/activity.html", "activity", pg=pg, rows=pg["rows"], actions=actions,
                     action=action, qs=urlencode(f))

    # old addresses keep working
    @app.get("/review")
    def old_review():
        return RedirectResponse("/admin/review", 301)

    @app.get("/admin/stats")
    def old_stats(month: str = ""):
        return RedirectResponse("/admin/collectors" + (f"?month={month}" if month else ""), 301)
