import datetime as dt
import hashlib
import hmac
import os
import re
import uuid
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, or_, select

from . import notices
from .auth import check_password, hash_password, norm_phone
from .config import Settings
from .db import Tender, User, make_session, now

TZ = ZoneInfo("Africa/Addis_Ababa")
HERE = os.path.dirname(os.path.abspath(__file__))
STAFF = ("admin", "reviewer", "collector")
REVIEWERS = ("admin", "reviewer")
EDITABLE = ("buyer", "title", "category", "region", "place", "grade", "closes", "time", "type",
            "bid_security", "document_fee", "source", "reference", "notice", "url")
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_PHOTO = 15_000_000


def today():
    return dt.datetime.now(TZ).date().isoformat()


MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def days_from(iso, ref):
    try:
        return (dt.date.fromisoformat(iso) - dt.date.fromisoformat(ref)).days
    except ValueError:
        return 999


def short_date(iso):
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.day} {MONTHS[d.month - 1]}"


def long_date(iso):
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.strftime('%A')} {d.day} {MONTHS[d.month - 1]} {d.year}"


def host(url):
    from urllib.parse import urlsplit
    return urlsplit(url).hostname or url


class LoginRequired(Exception):
    pass


def create_app(settings=None, claude=None):
    s = settings or Settings()
    Session = make_session(s.database_url)
    photos = os.path.join(s.data_dir, "notices")
    os.makedirs(photos, exist_ok=True)
    signer = URLSafeTimedSerializer(s.secret, salt="tw-session")
    app = FastAPI(title="Tender Watcher", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
    tpl = Jinja2Templates(directory=os.path.join(HERE, "templates"))
    tpl.env.globals.update(CATEGORIES=notices.CATEGORIES, TYPES=notices.TYPES, REGIONS=notices.REGIONS)
    tpl.env.filters.update(days_from=days_from, short_date=short_date, long_date=long_date, host=host)
    state = {"claude": claude}

    def get_claude():
        if state["claude"] is None:
            import anthropic
            state["claude"] = anthropic.Anthropic(api_key=s.anthropic_key or None)
        return state["claude"]

    # -- plumbing
    def db():
        with Session() as session:
            yield session

    def user_from(request, session):
        raw = request.cookies.get("tw")
        if not raw:
            return None
        try:
            uid = signer.loads(raw, max_age=30 * 86400)
        except BadSignature:
            return None
        u = session.get(User, uid)
        return u if u and u.active else None

    def csrf_token(u):
        return hmac.new(s.secret.encode(), f"csrf:{u.id}".encode(), hashlib.sha256).hexdigest()[:32]

    def check_csrf(u, token):
        if not u or not hmac.compare_digest(csrf_token(u), token or ""):
            raise HTTPException(400, "This form has expired. Go back, reload the page and try again.")

    def page(request, name, u=None, status=200, **ctx):
        ctx.update(request=request, me=u, csrf=csrf_token(u) if u else "", today=today())
        return tpl.TemplateResponse(request, name, ctx, status_code=status)

    def need(request, session, roles):
        u = user_from(request, session)
        if not u:
            raise LoginRequired()
        if u.role not in roles:
            raise HTTPException(403, "You don't have access to this page.")
        return u

    @app.exception_handler(LoginRequired)
    async def _login(request, exc):
        return RedirectResponse("/login?next=" + request.url.path, 303)

    @app.exception_handler(HTTPException)
    async def _http(request, exc):
        with Session() as session:
            u = user_from(request, session)
            return page(request, "message.html", u, status=exc.status_code, title="Something's not right",
                        text=str(exc.detail))

    # -- public pages
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, category: str = "", region: str = "", type: str = "", q: str = "",
             session=Depends(db)):
        u = user_from(request, session)
        query = select(Tender).where(Tender.status == "published",
                                     or_(Tender.closes == "", Tender.closes >= today()))
        if category in notices.CATEGORIES:
            query = query.where(Tender.category == category)
        if region:
            query = query.where(Tender.region == region)
        if type in notices.TYPES:
            query = query.where(Tender.type == type)
        if q.strip():
            like = f"%{q.strip()}%"
            query = query.where(or_(Tender.title.ilike(like), Tender.buyer.ilike(like), Tender.place.ilike(like)))
        rows = session.scalars(query.order_by(Tender.closes == "", Tender.closes, Tender.time)).all()
        return page(request, "list.html", u, rows=rows, f=dict(category=category, region=region, type=type, q=q))

    @app.get("/t/{tid}", response_class=HTMLResponse)
    def tender_page(request: Request, tid: str, session=Depends(db)):
        u = user_from(request, session)
        t = session.get(Tender, tid)
        if not t or (t.status != "published" and not (u and u.role in STAFF)):
            return page(request, "message.html", u, status=404, title="This tender has closed or is no longer listed",
                        text="Tenders leave the list after their closing date.", link=("/", "See all open tenders"))
        return page(request, "tender.html", u, t=t)

    @app.get("/notices/{name}")
    def photo(request: Request, name: str, session=Depends(db)):
        if not re.fullmatch(r"[0-9a-f]{16}\.(jpg|png|webp)", name):
            raise HTTPException(404, "Not found.")
        t = session.scalars(select(Tender).where(Tender.photo == name)).first()
        u = user_from(request, session)
        if not t or (t.status != "published" and not (u and u.role in STAFF)):
            raise HTTPException(404, "Not found.")
        return FileResponse(os.path.join(photos, name))

    # -- sign in
    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/", session=Depends(db)):
        return page(request, "login.html", user_from(request, session), next=next)

    @app.post("/login")
    def login(request: Request, phone: str = Form(""), password: str = Form(""), next: str = Form("/"),
              session=Depends(db)):
        u = session.scalars(select(User).where(User.phone == norm_phone(phone))).first()
        if not u or not u.active or not check_password(password, u.pw_hash):
            return page(request, "login.html", None, status=400, next=next,
                        error="That phone number and password don't match. Check both and try again.")
        if not next.startswith("/") or next.startswith("//"):
            next = "/"
        if next == "/":
            next = "/collect" if u.role in STAFF else "/"
        r = RedirectResponse(next, 303)
        r.set_cookie("tw", signer.dumps(u.id), max_age=30 * 86400, httponly=True, samesite="lax",
                     secure=s.secure_cookies)
        return r

    @app.post("/logout")
    def logout():
        r = RedirectResponse("/", 303)
        r.delete_cookie("tw")
        return r

    # -- collectors
    @app.get("/collect", response_class=HTMLResponse)
    def collect_form(request: Request, session=Depends(db)):
        u = need(request, session, STAFF)
        mine = session.scalars(select(Tender).where(Tender.collector_id == u.id)
                               .order_by(Tender.created_at.desc()).limit(10)).all()
        return page(request, "collect.html", u, mine=mine)

    @app.post("/collect", response_class=HTMLResponse)
    async def collect(request: Request, photo: UploadFile = File(...), source: str = Form(""),
                      csrf: str = Form(""), session=Depends(db)):
        u = need(request, session, STAFF)
        check_csrf(u, csrf)
        body = await photo.read(MAX_PHOTO + 1)
        media = photo.content_type or ""
        if body[:3] == b"\xff\xd8\xff":
            media = "image/jpeg"
        elif body[:8] == b"\x89PNG\r\n\x1a\n":
            media = "image/png"
        if media not in IMAGE_TYPES or not body:
            return page(request, "collect.html", u, status=400, error="Send the notice as a JPG or PNG photo.")
        if len(body) > MAX_PHOTO:
            return page(request, "collect.html", u, status=400, error="That photo is over 15 MB. Take it again at normal size.")
        try:
            out = notices.extract(get_claude(), body, media, source)
        except notices.ExtractError as e:
            return page(request, "collect.html", u, status=422, error=f"{e} Try again with a clearer photo.")
        if not out.get("tenders"):
            why = out.get("problem") or "No tender found in the photo."
            return page(request, "collect.html", u, status=422, error=f"{why} Take a clear photo of one notice.")
        name = uuid.uuid4().hex[:16] + IMAGE_TYPES[media]
        with open(os.path.join(photos, name), "wb") as f:
            f.write(body)
        paper = (out.get("newspaper") or source).strip()[:200]
        ids = []
        for t in out["tenders"]:
            e = notices.to_entry(t, paper, today())
            row = Tender(id="tw-" + uuid.uuid4().hex[:10], status="pending", photo=name, collector_id=u.id,
                         source=paper, extract=t, closes_raw=e["closes_raw"][:120], doubts=e["doubts"],
                         **{k: (e.get(k) or "")[:500] for k in ("buyer", "title", "category", "region", "place",
                                                                 "grade", "closes", "time", "type",
                                                                 "bid_security", "document_fee")})
            dup = duplicate_of(session, row)
            if dup:
                row.doubts = (f"Possibly already listed: {dup.title} ({dup.id}). " + row.doubts).strip()
            session.add(row)
            ids.append(row.id)
        session.commit()
        return RedirectResponse(f"/entry/{ids[0]}?new={len(ids)}", 303)

    def duplicate_of(session, row):
        if not row.closes:
            return None
        for t in session.scalars(select(Tender).where(Tender.closes == row.closes, Tender.status != "rejected",
                                                      Tender.id != row.id)):
            if notices.norm(t.buyer) == notices.norm(row.buyer) and \
                    notices.norm(t.title)[:40] == notices.norm(row.title)[:40]:
                return t
        return None

    def can_edit(u, t):
        return u.role in REVIEWERS or (t.collector_id == u.id and t.status == "pending")

    @app.get("/entry/{tid}", response_class=HTMLResponse)
    def entry(request: Request, tid: str, new: int = 0, session=Depends(db)):
        u = need(request, session, STAFF)
        t = session.get(Tender, tid)
        if not t or not (u.role in REVIEWERS or t.collector_id == u.id):
            raise HTTPException(404, "Not found.")
        siblings = session.scalars(select(Tender).where(Tender.photo == t.photo, Tender.photo != "",
                                                        Tender.id != t.id)).all() if t.photo else []
        return page(request, "entry.html", u, t=t, new=new, siblings=siblings, editable=can_edit(u, t))

    @app.post("/entry/{tid}")
    async def save_entry(request: Request, tid: str, session=Depends(db)):
        u = need(request, session, STAFF)
        form = await request.form()
        check_csrf(u, form.get("csrf"))
        t = session.get(Tender, tid)
        if not t or not can_edit(u, t):
            raise HTTPException(403, "This entry can no longer be changed.")
        for k in EDITABLE:
            if k in form:
                setattr(t, k, str(form[k]).strip()[:8000 if k == "notice" else 500])
        errors = []
        ed, em, ey = (str(form.get(k, "")).strip() for k in ("eth_day", "eth_month", "eth_year"))
        if ed or em or ey:
            try:
                t.closes = notices.ethiopian_to_gregorian(int(ey), int(em), int(ed)).isoformat()
                t.closes_raw = f"{ed}/{em}/{ey} E.C."
            except ValueError:
                errors.append("The Ethiopian date isn't a real date. Check day, month and year.")
        if t.closes and not re.fullmatch(r"\d{4}-\d\d-\d\d", t.closes):
            errors.append("Closing date must be a date.")
        if t.time and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t.time):
            errors.append("Closing time must look like 10:00.")
        if t.category not in notices.CATEGORIES:
            t.category = "other"
        if t.type not in notices.TYPES:
            t.type = "gov"
        if t.category != "construction":
            t.grade = ""
        action = form.get("action", "save")
        if action == "approve" and u.role in REVIEWERS:
            if not t.buyer or not t.title:
                errors.append("Buyer and title are needed before publishing.")
            if not errors:
                t.status, t.reviewer_id, t.published_at = "published", u.id, now()
        elif action == "reject" and u.role in REVIEWERS:
            t.status, t.reviewer_id = "rejected", u.id
        if errors:
            session.rollback()
            t = session.get(Tender, tid)
            return page(request, "entry.html", u, status=400, t=t, new=0, siblings=[], editable=True,
                        errors=errors, form=form)
        session.commit()
        if t.status == "published" and action == "approve":
            try:
                notices.publish_to_board(s.board_json, t, s.site_url, photos)
            except Exception as e:  # the app is the source of truth; the static board is a copy
                print(f"board update failed for {t.id}: {e}", flush=True)
        if action in ("approve", "reject"):
            nxt = session.scalars(select(Tender).where(Tender.status == "pending")
                                  .order_by(Tender.created_at)).first()
            return RedirectResponse(f"/entry/{nxt.id}" if nxt else "/review", 303)
        return RedirectResponse(f"/entry/{t.id}?saved=1", 303)

    @app.get("/mine", response_class=HTMLResponse)
    def mine(request: Request, session=Depends(db)):
        u = need(request, session, STAFF)
        rows = session.scalars(select(Tender).where(Tender.collector_id == u.id)
                               .order_by(Tender.created_at.desc()).limit(200)).all()
        month = today()[:7]
        counts = {"published": 0, "pending": 0, "rejected": 0}
        for r in rows:
            if r.created_at.astimezone(TZ).date().isoformat()[:7] == month:
                counts[r.status] = counts.get(r.status, 0) + 1
        return page(request, "mine.html", u, rows=rows, counts=counts, month=month)

    # -- reviewers
    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request, session=Depends(db)):
        u = need(request, session, REVIEWERS)
        rows = session.scalars(select(Tender).where(Tender.status == "pending").order_by(Tender.created_at)).all()
        return page(request, "review.html", u, rows=rows)

    @app.get("/admin/stats", response_class=HTMLResponse)
    def stats(request: Request, month: str = "", session=Depends(db)):
        u = need(request, session, REVIEWERS)
        month = month if re.fullmatch(r"\d{4}-\d\d", month) else today()[:7]
        start = dt.datetime.fromisoformat(month + "-01").replace(tzinfo=TZ)
        end = (start + dt.timedelta(days=32)).replace(day=1)
        q = (select(User.name, Tender.status, func.count()).join(User, Tender.collector_id == User.id)
             .where(Tender.created_at >= start, Tender.created_at < end).group_by(User.name, Tender.status))
        table = {}
        for name, status, n in session.execute(q):
            table.setdefault(name, {"published": 0, "pending": 0, "rejected": 0})[status] = n
        return page(request, "stats.html", u, table=sorted(table.items()), month=month)

    @app.get("/admin/team", response_class=HTMLResponse)
    def team(request: Request, session=Depends(db)):
        u = need(request, session, ("admin",))
        people = session.scalars(select(User).where(User.role.in_(STAFF)).order_by(User.role, User.name)).all()
        return page(request, "team.html", u, people=people)

    @app.post("/admin/team", response_class=HTMLResponse)
    def add_person(request: Request, name: str = Form(""), phone: str = Form(""), role: str = Form("collector"),
                   password: str = Form(""), csrf: str = Form(""), session=Depends(db)):
        u = need(request, session, ("admin",))
        check_csrf(u, csrf)
        people = session.scalars(select(User).where(User.role.in_(STAFF)).order_by(User.role, User.name)).all()
        p = norm_phone(phone)
        error = ("Enter a name." if not name.strip() else
                 "Enter an Ethiopian mobile number, like 0911 234 567." if not p else
                 "Use a password of at least 8 characters." if len(password) < 8 else
                 "Pick a role." if role not in STAFF else
                 "Someone already uses that phone number." if session.scalars(select(User).where(User.phone == p)).first()
                 else "")
        if error:
            return page(request, "team.html", u, status=400, people=people, error=error,
                        form=dict(name=name, phone=phone, role=role))
        session.add(User(name=name.strip()[:120], phone=p, role=role, pw_hash=hash_password(password)))
        session.commit()
        return RedirectResponse("/admin/team?added=1", 303)

    @app.post("/admin/team/{uid}/toggle")
    def toggle_person(request: Request, uid: int, csrf: str = Form(""), session=Depends(db)):
        u = need(request, session, ("admin",))
        check_csrf(u, csrf)
        p = session.get(User, uid)
        if p and p.id != u.id:
            p.active = not p.active
            session.commit()
        return RedirectResponse("/admin/team", 303)

    app.state.Session = Session
    return app
