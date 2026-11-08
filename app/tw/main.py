import datetime as dt
import hashlib
import json
import hmac
import os
import re
import uuid
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, or_, select

from . import notices
from . import auth
from .auth import check_password, hash_password, norm_phone
from .config import Settings
from .db import AlertSent, AuditLog, Bid, Company, LoginFailure, Payment, Price, Setting, Tender, User, make_session, now
from .sync import BoardSync

TZ = ZoneInfo("Africa/Addis_Ababa")
HERE = os.path.dirname(os.path.abspath(__file__))
STAFF = ("admin", "reviewer", "collector")
REVIEWERS = ("admin", "reviewer")
EDITABLE = ("buyer", "title", "category", "region", "place", "grade", "closes", "time", "type",
            "bid_security", "document_fee", "source", "reference", "notice", "url")
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_PHOTO = 15_000_000
LOCK_MINUTES, MAX_PHONE_FAILS, MAX_IP_FAILS = 15, 5, 20
MIN_PASSWORD = 10
TRIAL_DAYS = 14  # starting value; admins change it on /admin/prices
MAX_SIGNUPS_PER_HOUR = 5
# Prices are set per currency, not converted; admins change them on /admin/prices.
# These are only the starting values for a new database. Foreign firms pay USD by card on Chapa.
DEFAULT_PRICES = {"ETB": {"month": 299, "year": 2400, "win_month": 990, "win_year": 7900},
                  "USD": {"month": 12, "year": 99, "win_month": 39, "win_year": 319}}
PRICE_CACHE_SECONDS = 10
CURRENCIES = {"ETB": "ETB", "USD": "USD"}  # online checkout
BANK_CURRENCIES = ("ETB", "USD", "EUR")  # recorded by an admin
VAGUE_TITLE = re.compile(r"^\s*(subject|title)?\s*(not stated|unknown|n/?a|-+)\s*$", re.I)
# Watch (month, year): every tender, matches and alerts. Win (win_month, win_year): Watch plus Bid Room.
PLAN_DAYS = {"month": 30, "year": 365, "win_month": 30, "win_year": 365}
PLAN_LABEL = {"month": "Watch, monthly", "year": "Watch, yearly", "win_month": "Win, monthly", "win_year": "Win, yearly"}
WIN_PLANS = ("win_month", "win_year")
PAY_METHODS = {"chapa": "Chapa", "telebirr": "Telebirr", "bank": "Bank transfer"}
CHECK_HOURS = 48  # stop asking Chapa about a checkout after this


PER_PAGE = 20
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; form-action 'self' https://checkout.chapa.co; base-uri 'none'; object-src 'none'")


def paginate(rows, p, per=None):
    per = per or PER_PAGE
    pages = max(1, -(-len(rows) // per))
    p = min(max(1, p), pages)
    near = sorted({1, pages, p - 1, p, p + 1} & set(range(1, pages + 1)))
    shown, last = [], 0
    for n in near:  # 1 … 4 5 6 … 12
        if n - last > 1:
            shown.append(None)
        shown.append(n)
        last = n
    return {"rows": rows[(p - 1) * per: p * per], "page": p, "pages": pages, "shown": shown,
            "first": (p - 1) * per + 1, "last": min(p * per, len(rows)), "total": len(rows)}


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


def date_notes(t, ref):
    """Plain-language warnings about a closing date or time that looks wrong. Never changes the data."""
    notes = []
    if t.closes and days_from(t.closes, ref) > 183:
        notes.append("This closing date is more than 6 months away, which is unusual. Check the notice.")
    m = re.fullmatch(r"(\d\d):(\d\d)", t.time or "")
    if m and 1 <= int(m.group(1)) <= 5:
        notes.append(f"{t.time} may be Ethiopian clock time, which is {int(m.group(1)) + 6:02d}:{m.group(2)} "
                     "international time. Check the notice.")
    elif m and not ("06:00" <= t.time <= "18:00"):
        notes.append("This closing time is outside office hours. Check the notice.")
    return notes


MAGIC = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",), "image/webp": (b"RIFF",)}


def looks_like_image(body, media):
    return body.startswith(MAGIC.get(media, ())) and (media != "image/webp" or body[8:12] == b"WEBP")


def safe_url(url):
    """Only http(s) links reach a page; anything else (javascript:, data:) is dropped."""
    url = str(url or "").strip()
    return url if re.match(r"https?://[^\s\"'<>]+$", url, re.I) else ""


def host(url):
    from urllib.parse import urlsplit
    return urlsplit(url).hostname or url


class LoginRequired(Exception):
    pass


class TwoStepRequired(Exception):
    """Admins and reviewers must turn on two-step sign-in before any staff page opens."""


TWO_STEP_ROLES = ("admin", "reviewer")
ADMIN_LAYOUT = "admin/_layout.html"


def create_app(settings=None, claude=None):
    s = settings or Settings()
    Session = make_session(s.database_url)
    photos = os.path.join(s.data_dir, "notices")
    os.makedirs(photos, exist_ok=True)
    signer = URLSafeTimedSerializer(s.secret, salt="tw-session")
    tg_signer = URLSafeTimedSerializer(s.secret, salt="tw-telegram-link")
    step_signer = URLSafeTimedSerializer(s.secret, salt="tw-two-step")
    setup_signer = URLSafeTimedSerializer(s.secret, salt="tw-two-step-setup")
    app = FastAPI(title="Tender Watcher", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

    @app.middleware("http")
    async def security_headers(request, call_next):
        r = await call_next(request)
        h = r.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        h.setdefault("Content-Security-Policy", CSP)
        if s.secure_cookies:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith(("/admin", "/account", "/entry", "/security", "/login", "/collect", "/mine")):
            h.setdefault("Cache-Control", "no-store")
        return r
    tpl = Jinja2Templates(directory=os.path.join(HERE, "templates"))
    tpl.env.globals.update(CATEGORIES=notices.CATEGORIES, TYPES=notices.TYPES, REGIONS=notices.REGIONS)
    tpl.env.filters.update(days_from=days_from, short_date=short_date, long_date=long_date, host=host,
                           safe_url=safe_url)
    tpl.env.globals.update(date_notes=date_notes, PLAN_LABEL=PLAN_LABEL)
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
            uid, ver = signer.loads(raw, max_age=30 * 86400)
        except (BadSignature, ValueError, TypeError):
            return None
        u = session.get(User, uid)
        return u if u and u.active and u.session_ver == ver else None

    def csrf_token(u):
        return hmac.new(s.secret.encode(), f"csrf:{u.id}".encode(), hashlib.sha256).hexdigest()[:32]

    def check_csrf(u, token):
        if not u or not hmac.compare_digest(csrf_token(u), token or ""):
            raise HTTPException(400, "This form has expired. Go back, reload the page and try again.")

    def page(request, name, u=None, status=200, **ctx):
        ctx.setdefault("layout", ADMIN_LAYOUT if u and u.role in TWO_STEP_ROLES and ctx.pop("admin", False) else "base.html")
        ctx.pop("admin", None)
        if ctx["layout"] == ADMIN_LAYOUT and "side" not in ctx:
            with Session() as ss:
                ctx["side"] = side_counts(ss)
        ctx.update(request=request, me=u, csrf=csrf_token(u) if u else "", today=today(), PRICES=prices(), TRIAL=trial_days())
        return tpl.TemplateResponse(request, name, ctx, status_code=status)

    with Session() as _s:  # tenders saved before multi-category get theirs once
        for _t in _s.scalars(select(Tender).where(or_(Tender.categories == "", Tender.reviewer_id.is_(None)))):
            notices.tag(_t, keep=notices.cats(_t))  # word lists grow; a reviewer's choice is left alone
        _s.commit()
    with Session() as _s:  # prices start from the defaults; admins change them on /admin/prices
        have = {(p.currency, p.plan) for p in _s.scalars(select(Price))}
        for cur, plans in DEFAULT_PRICES.items():
            for plan, amount in plans.items():
                if (cur, plan) not in have:
                    _s.add(Price(currency=cur, plan=plan, amount=amount))
        try:
            _s.commit()
        except Exception:  # another worker seeded them first
            _s.rollback()
    _price_cache = {"at": 0.0, "v": None}

    def prices(fresh=False):
        """{currency: {plan: amount}} from the database, cached briefly across requests."""
        return _load(fresh)["prices"]

    def trial_days(fresh=False):
        return _load(fresh)["trial"]

    def _load(fresh):
        import time
        if fresh or not _price_cache["v"] or time.monotonic() - _price_cache["at"] > PRICE_CACHE_SECONDS:
            with Session() as ps:
                v = {}
                for p in ps.scalars(select(Price)):
                    v.setdefault(p.currency, {})[p.plan] = p.amount
                t = ps.get(Setting, "trial_days")
                trial = int(t.value) if t and t.value.isdigit() else TRIAL_DAYS
            for cur, plans in DEFAULT_PRICES.items():
                for plan, amount in plans.items():
                    v.setdefault(cur, {}).setdefault(plan, amount)
            _price_cache.update(at=time.monotonic(), v={"prices": v, "trial": trial})
        return _price_cache["v"]

    sync = BoardSync(s.board_json)
    board_notices = os.path.join(os.path.dirname(os.path.abspath(s.board_json)), "notices") if s.board_json else ""

    def has_access(u):
        if not u:
            return False
        if u.role in STAFF:
            return True
        c = u.company
        return bool(c) and today() <= max(c.trial_until or "", c.paid_until or "")

    def has_bidroom(u):
        """Bid Room: staff, firms on the free trial, and firms paid on a Win plan."""
        if not u:
            return False
        if u.role in STAFF:
            return True
        c = u.company
        return bool(c) and today() <= max(c.trial_until or "", c.win_until or "")

    def interests(c):
        try:
            d = json.loads(c.interests or "{}")
        except ValueError:
            d = {}
        return {k: list(d.get(k, [])) for k in ("categories", "regions", "keywords")}

    def need(request, session, roles):
        u = user_from(request, session)
        if not u:
            raise LoginRequired()
        if u.role not in roles:
            raise HTTPException(403, "You don't have access to this page.")
        if u.role in TWO_STEP_ROLES and not u.totp_secret:
            raise TwoStepRequired()
        return u

    def side_counts(session):
        return {"pending": session.scalar(select(func.count()).select_from(Tender).where(Tender.status == "pending")),
                "waiting": session.scalar(select(func.count()).select_from(Payment).where(Payment.status == "pending"))}

    def spage(request, session, u, name, nav, status=200, **ctx):
        """A staff page: inside the admin console for admins and reviewers, plain for collectors."""
        if u.role in TWO_STEP_ROLES:
            return page(request, name, u, status=status, admin=True, nav=nav, side=side_counts(session), **ctx)
        return page(request, name, u, status=status, **ctx)

    def audit(session, u, action, target="", detail="", request=None):
        session.add(AuditLog(actor_id=u.id if u else None, action=action, target=str(target)[:120],
                             detail=str(detail)[:500], ip=client_ip(request)[:60] if request else ""))

    @app.exception_handler(LoginRequired)
    async def _login(request, exc):
        return RedirectResponse("/login?next=" + request.url.path, 303)

    @app.exception_handler(TwoStepRequired)
    async def _two_step(request, exc):
        return RedirectResponse("/security?required=1", 303)

    @app.exception_handler(HTTPException)
    async def _http(request, exc):
        with Session() as session:
            u = user_from(request, session)
            if exc.status_code == 402:
                return page(request, "message.html", u, status=402, title="Bid Room is in the Win plan",
                            text=str(exc.detail), link=("/account#plans", "See the Win plan"))
            return page(request, "message.html", u, status=exc.status_code, title="Something's not right",
                        text=str(exc.detail))

    # -- public pages
    def currency_of(request):
        cur = request.query_params.get("cur", "").upper()
        return cur if cur in CURRENCIES else "ETB"

    @app.get("/pricing", response_class=HTMLResponse)
    def pricing(request: Request, session=Depends(db)):
        u = user_from(request, session)
        cur = currency_of(request)
        return page(request, "pricing.html", u, cur=cur, prices=prices()[cur], all_prices=prices(),
                    trial_days=trial_days(), chapa_on=bool(app.state.chapa))

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, category: str = "", region: str = "", type: str = "", q: str = "",
             p: int = 1, session=Depends(db)):
        u = user_from(request, session)
        sync(session)
        query = select(Tender).where(Tender.status == "published",
                                     or_(Tender.closes == "", Tender.closes >= today()))
        if category in notices.CATEGORIES:
            query = query.where(or_(Tender.category == category, Tender.categories.like(f"%,{category},%")))
        if region:
            query = query.where(Tender.region == region)
        if type in notices.TYPES:
            query = query.where(Tender.type == type)
        if q.strip():
            like = f"%{q.strip()}%"
            query = query.where(or_(Tender.title.ilike(like), Tender.buyer.ilike(like), Tender.place.ilike(like)))
        rows = session.scalars(query.order_by(Tender.closes == "", Tender.closes, Tender.time)).all()
        filtered = bool(category or region or type or q.strip())
        stats = None
        pg = paginate(rows, p)
        if not filtered and pg["page"] == 1:
            week = (dt.date.fromisoformat(today()) + dt.timedelta(days=7)).isoformat()
            stats = dict(open=len(rows), buyers=len({r.buyer for r in rows}),
                         week=sum(1 for r in rows if r.closes and r.closes <= week),
                         latest=sorted(rows, key=lambda r: (r.published_at or r.created_at).replace(tzinfo=None), reverse=True)[:4])
        f = dict(category=category, region=region, type=type, q=q)
        return page(request, "list.html", u, rows=pg["rows"], total=len(rows), pg=pg, stats=stats, filtered=filtered,
                    prices=prices()["ETB"], f=f, qs=urlencode({k: v for k, v in f.items() if v}))

    @app.get("/t/{tid}", response_class=HTMLResponse)
    def tender_page(request: Request, tid: str, session=Depends(db)):
        u = user_from(request, session)
        sync(session)
        t = session.get(Tender, tid)
        if not t or (t.status != "published" and not (u and u.role in STAFF)):
            return page(request, "message.html", u, status=404, title="This tender has closed or is no longer listed",
                        text="Tenders leave the list after their closing date.", link=("/", "See all open tenders"))
        bid = None
        if u and u.role == "member" and u.company_id:
            bid = session.scalars(select(Bid).where(Bid.company_id == u.company_id, Bid.tender_id == tid)).first()
        return page(request, "tender.html", u, t=t, full=has_access(u), win=has_bidroom(u), bid=bid)

    @app.get("/notices/{name}")
    def photo(request: Request, name: str, session=Depends(db)):
        if not re.fullmatch(r"[0-9a-f]{12,16}\.(jpg|png|webp)", name):
            raise HTTPException(404, "Not found.")
        t = session.scalars(select(Tender).where(Tender.photo == name)).first()
        u = user_from(request, session)
        if not t or (t.status != "published" and not (u and u.role in STAFF)):
            raise HTTPException(404, "Not found.")
        if not has_access(u):
            raise HTTPException(403, "Start your free trial to see the full notice.")
        for folder in (photos, board_notices):  # app uploads, then photos the Telegram bot published
            path = os.path.join(folder, name)
            if folder and os.path.exists(path):
                return FileResponse(path)
        raise HTTPException(404, "Not found.")

    # -- sign in
    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/", session=Depends(db)):
        return page(request, "login.html", user_from(request, session), next=next)

    def client_ip(request):
        fwd = request.headers.get("x-forwarded-for", "")
        return (fwd.split(",")[-1].strip() if fwd else "") or (request.client.host if request.client else "?")

    @app.post("/login")
    def login(request: Request, phone: str = Form(""), password: str = Form(""), next: str = Form("/"),
              session=Depends(db)):
        p = norm_phone(phone)
        keys = ["ip:" + client_ip(request)[:60]] + (["phone:" + p] if p else [])
        since = now() - dt.timedelta(minutes=LOCK_MINUTES)
        for key in keys:
            n = session.scalar(select(func.count()).select_from(LoginFailure)
                               .where(LoginFailure.key == key, LoginFailure.at >= since))
            if n >= (MAX_PHONE_FAILS if key.startswith("phone:") else MAX_IP_FAILS):
                return page(request, "login.html", None, status=429, next=next,
                            error=f"Too many tries. Wait {LOCK_MINUTES} minutes, then try again.")
        u = session.scalars(select(User).where(User.phone == p)).first() if p else None
        if not u or not u.active or not check_password(password, u.pw_hash):
            if u and u.role in STAFF:
                audit(session, u, "password_wrong", u.phone, "", request)
            session.add_all([LoginFailure(key=k) for k in keys])
            session.commit()
            return page(request, "login.html", None, status=400, next=next,
                        error="That phone number and password don't match. Check both and try again.")
        session.query(LoginFailure).filter(LoginFailure.key == "phone:" + p).delete()
        session.query(LoginFailure).filter(LoginFailure.at < now() - dt.timedelta(days=1)).delete()
        session.commit()
        if not next.startswith("/") or next.startswith("//"):
            next = "/"
        if u.totp_secret:  # password was right; the code from the app comes next
            r = RedirectResponse("/login/code?next=" + next, 303)
            r.set_cookie("tw2", step_signer.dumps([u.id, u.session_ver]), max_age=600, httponly=True,
                         samesite="lax", secure=s.secure_cookies)
            return r
        return finish_login(request, session, u, next)

    def landing(u):
        return "/admin" if u.role in TWO_STEP_ROLES else "/collect" if u.role in STAFF else "/my"

    def finish_login(request, session, u, next):
        if next == "/":
            next = landing(u)
        u.last_login = now()
        if u.role in STAFF:
            audit(session, u, "sign_in", u.phone, "two-step" if u.totp_secret else "password only", request)
        session.commit()
        r = RedirectResponse(next, 303)
        r.delete_cookie("tw2")
        r.set_cookie("tw", signer.dumps([u.id, u.session_ver]), max_age=30 * 86400, httponly=True,
                     samesite="lax", secure=s.secure_cookies)
        return r

    def pending_step(request, session):
        try:
            uid, ver = step_signer.loads(request.cookies.get("tw2", ""), max_age=600)
        except (BadSignature, ValueError, TypeError):
            return None
        u = session.get(User, uid)
        return u if u and u.active and u.session_ver == ver and u.totp_secret else None

    @app.get("/login/code", response_class=HTMLResponse)
    def code_form(request: Request, next: str = "/", session=Depends(db)):
        if not pending_step(request, session):
            return RedirectResponse("/login", 303)
        return page(request, "code.html", None, next=next)

    @app.post("/login/code")
    def code_check(request: Request, code: str = Form(""), next: str = Form("/"), session=Depends(db)):
        u = pending_step(request, session)
        if not u:
            return RedirectResponse("/login", 303)
        if not next.startswith("/") or next.startswith("//"):
            next = "/"
        key = f"code:{u.id}"
        since = now() - dt.timedelta(minutes=LOCK_MINUTES)
        if session.scalar(select(func.count()).select_from(LoginFailure)
                          .where(LoginFailure.key == key, LoginFailure.at >= since)) >= MAX_PHONE_FAILS:
            return page(request, "code.html", None, status=429, next=next,
                        error=f"Too many wrong codes. Wait {LOCK_MINUTES} minutes, then sign in again.")
        step = auth.totp_step(u.totp_secret, code, u.totp_step)
        if step:
            u.totp_step = step
            return finish_login(request, session, u, next)
        left = auth.use_backup_code(code, json.loads(u.totp_backup or "[]"))
        if left is not None:
            u.totp_backup = json.dumps(left)
            audit(session, u, "backup_code_used", u.phone, f"{len(left)} backup codes left", request)
            return finish_login(request, session, u, next)
        session.add(LoginFailure(key=key))
        if u.role in STAFF:
            audit(session, u, "code_wrong", u.phone, "", request)
        session.commit()
        return page(request, "code.html", None, status=400, next=next,
                    error="That code didn't work. Codes change every 30 seconds; use the newest one.")

    @app.post("/logout")
    def logout(request: Request, session=Depends(db)):
        u = user_from(request, session)
        if u:
            u.session_ver += 1  # signs this person out on every device
            session.commit()
        r = RedirectResponse("/", 303)
        r.delete_cookie("tw")
        return r

    # -- companies: sign-up, account, matches
    def set_login(r, u):
        r.set_cookie("tw", signer.dumps([u.id, u.session_ver]), max_age=30 * 86400, httponly=True,
                     samesite="lax", secure=s.secure_cookies)
        return r

    @app.get("/signup", response_class=HTMLResponse)
    def signup_form(request: Request, session=Depends(db)):
        return page(request, "signup.html", user_from(request, session), form={})

    @app.post("/signup", response_class=HTMLResponse)
    def signup(request: Request, company: str = Form(""), name: str = Form(""), phone: str = Form(""),
               password: str = Form(""), tin: str = Form(""), session=Depends(db)):
        p, tin = norm_phone(phone), re.sub(r"\D", "", tin)
        ip_key = "signup:" + client_ip(request)[:60]
        recent = session.scalar(select(func.count()).select_from(LoginFailure).where(
            LoginFailure.key == ip_key, LoginFailure.at >= now() - dt.timedelta(hours=1)))
        if recent >= MAX_SIGNUPS_PER_HOUR:
            return page(request, "signup.html", None, status=429, form=dict(company=company, name=name, phone=phone, tin=tin),
                        error="Too many new accounts from this connection. Try again in an hour.")
        error = ("Enter your company's name." if not company.strip() else
                 "Enter your name." if not name.strip() else
                 "Enter an Ethiopian mobile number, like 0911 234 567." if not p else
                 f"Use a password of at least {MIN_PASSWORD} characters." if len(password) < MIN_PASSWORD else
                 "A TIN has 10 digits." if tin and len(tin) != 10 else
                 "That phone number already has an account. Sign in instead." if
                 session.scalars(select(User).where(User.phone == p)).first() else "")
        if error:
            return page(request, "signup.html", None, status=400, error=error,
                        form=dict(company=company, name=name, phone=phone, tin=tin))
        end = (dt.date.fromisoformat(today()) + dt.timedelta(days=trial_days(fresh=True))).isoformat()
        c = Company(name=company.strip()[:200], tin=tin, trial_until=end)
        u = User(name=name.strip()[:120], phone=p, role="member", pw_hash=hash_password(password), company=c)
        session.add_all([c, u, LoginFailure(key=ip_key)])
        session.commit()
        return set_login(RedirectResponse("/account?welcome=1", 303), u)

    @app.get("/account", response_class=HTMLResponse)
    def account(request: Request, session=Depends(db)):
        u = need(request, session, ("member",))
        c = u.company
        link = "https://t.me/{}?start={}".format(s.telegram_bot, tg_signer.dumps(u.id)) if s.telegram_bot else ""
        pays = session.scalars(select(Payment).where(Payment.company_id == c.id, Payment.status == "paid")
                               .order_by(Payment.paid_at.desc())).all()
        cur = currency_of(request)
        return page(request, "account.html", u, c=c, i=interests(c), access=has_access(u), win=has_bidroom(u),
                    prices=prices()[cur], cur=cur,
                    days_left=days_from(max(c.trial_until or "", c.paid_until or ""), today()), tg_link=link,
                    pays=pays, methods=PAY_METHODS, chapa_on=bool(app.state.chapa),
                    payment=request.query_params.get("payment", ""))

    # -- payments
    app.state.chapa = notices.eb.Chapa(s.chapa_key) if s.chapa_key else None

    def credit(session, p, method=None, reference="", by=None):
        """Mark a payment paid and extend the company's subscription. Safe to call twice:
        the row is locked and a payment already paid is left alone. Returns True when credited now."""
        p = session.scalars(select(Payment).where(Payment.id == p.id).with_for_update().execution_options(populate_existing=True)).one()
        if p.status == "paid":
            return False
        c = session.scalars(select(Company).where(Company.id == p.company_id).with_for_update().execution_options(populate_existing=True)).one()
        if p.plan in WIN_PLANS:  # Win extends Bid Room, and tender access at least as far
            start = max(today(), c.win_until or "", c.trial_until or "")
            c.win_until = (dt.date.fromisoformat(start) + dt.timedelta(days=p.days)).isoformat()
            c.paid_until = max(c.paid_until or "", c.win_until)
            until = c.win_until
        else:
            start = max(today(), c.paid_until or "", c.trial_until or "")
            c.paid_until = until = (dt.date.fromisoformat(start) + dt.timedelta(days=p.days)).isoformat()
        p.status, p.paid_at, p.paid_until = "paid", now(), until
        if method:
            p.method = method
        if reference:
            p.reference = reference[:120]
        if by:
            p.recorded_by = by
        session.commit()
        return True

    def verify(session, p):
        """Ask Chapa about one pending checkout; credit it when paid. Returns True when credited now."""
        chapa = app.state.chapa
        if not chapa or p.status != "pending" or p.method != "chapa":
            return False
        p.checked_at = now()
        session.commit()
        try:
            ok = chapa.paid(p.tx_ref, p.amount, p.currency)
        except Exception as e:
            print(f"chapa verify {p.tx_ref}: {e}", flush=True)
            return False
        return credit(session, p) if ok else False

    def check_pending(session):
        """Verify open checkouts: each at most once a minute in its first hour, then every 15 minutes;
        expire them after CHECK_HOURS. Returns payments credited."""
        n, t = 0, now()
        for p in session.scalars(select(Payment).where(Payment.status == "pending", Payment.method == "chapa")).all():
            created = p.created_at if p.created_at.tzinfo else p.created_at.replace(tzinfo=dt.timezone.utc)
            age = (t - created).total_seconds()
            if age > CHECK_HOURS * 3600:
                p.status = "expired"
                session.commit()
                continue
            last = p.checked_at and (p.checked_at if p.checked_at.tzinfo else p.checked_at.replace(tzinfo=dt.timezone.utc))
            gap = 55 if age < 3600 else 895
            if last and (t - last).total_seconds() < gap:
                continue
            n += verify(session, p)
        return n

    @app.post("/account/pay")
    def pay(request: Request, plan: str = Form(""), currency: str = Form("ETB"), csrf: str = Form(""),
            session=Depends(db)):
        u = need(request, session, ("member",))
        check_csrf(u, csrf)
        if currency not in CURRENCIES:
            raise HTTPException(400, "Pick ETB or USD.")
        price = prices(fresh=True)[currency]
        if plan not in price:
            raise HTTPException(400, "Pick a plan.")
        if not app.state.chapa:
            raise HTTPException(503, "Online payment is not open yet. Pay by Telebirr or bank transfer for now.")
        p = Payment(tx_ref="TW-" + uuid.uuid4().hex[:16], company_id=u.company_id, plan=plan,
                    amount=price[plan], currency=currency, days=PLAN_DAYS[plan])
        session.add(p)
        session.commit()
        try:
            url = app.state.chapa.checkout(p.tx_ref, p.amount, u.company.name,
                                           f"{s.site_url}/account/paid?ref={p.tx_ref}", currency)
        except Exception as e:
            print(f"chapa checkout {p.tx_ref}: {e}", flush=True)
            p.status = "expired"
            session.commit()
            raise HTTPException(502, "Chapa did not open the payment page. Please try again in a minute.")
        return RedirectResponse(url, 303)

    @app.get("/account/paid")
    def paid(request: Request, ref: str = "", session=Depends(db)):
        u = need(request, session, ("member",))
        p = session.scalars(select(Payment).where(Payment.tx_ref == ref, Payment.company_id == u.company_id)).first()
        if p and p.status == "pending":
            verify(session, p)
            session.refresh(p)
        state = "paid" if p and p.status == "paid" else "waiting" if p and p.status == "pending" else "unknown"
        return RedirectResponse(f"/account?payment={state}", 303)

    @app.post("/internal/check-payments")
    def check_payments_now(request: Request, session=Depends(db)):
        internal(request)
        return {"credited": check_pending(session)}

    @app.get("/admin/payments", response_class=HTMLResponse)
    def payments(request: Request, session=Depends(db)):
        u = need(request, session, ("admin",))
        return payments_page(request, session, u)

    def payments_page(request, session, u, status=200, **ctx):
        rows = session.scalars(select(Payment).order_by(Payment.created_at.desc()).limit(200)).all()
        companies = session.scalars(select(Company).order_by(Company.name)).all()
        return spage(request, session, u, "payments.html", "payments", status=status, rows=rows, companies=companies, prices=prices(),
                    bank_currencies=BANK_CURRENCIES, methods=PAY_METHODS, chapa_on=bool(app.state.chapa), **ctx)

    @app.post("/admin/payments", response_class=HTMLResponse)
    def record_payment(request: Request, company_id: int = Form(0), plan: str = Form(""), method: str = Form(""),
                       reference: str = Form(""), currency: str = Form("ETB"), amount: str = Form(""),
                       csrf: str = Form(""), session=Depends(db)):
        u = need(request, session, ("admin",))
        check_csrf(u, csrf)
        c = session.get(Company, company_id)
        amount = amount.replace(",", "").strip()
        listed = prices(fresh=True).get(currency, {}).get(plan)
        paid_amount = int(amount) if amount.isdigit() else listed
        error = ("Pick the company." if not c else "Pick a plan." if plan not in PLAN_DAYS else
                 "Pick ETB, USD or EUR." if currency not in BANK_CURRENCIES else
                 "Enter the amount received." if not paid_amount else
                 "Pick Telebirr or bank transfer." if method not in ("telebirr", "bank") else
                 "Telebirr payments are in ETB." if method == "telebirr" and currency != "ETB" else
                 "Enter the Telebirr or bank transaction number." if not reference.strip() else
                 "That transaction number is already recorded." if session.scalars(
                     select(Payment).where(Payment.reference == reference.strip()[:120])).first() else "")
        if error:
            return payments_page(request, session, u, status=400, error=error,
                                 form=dict(company_id=company_id, plan=plan, method=method, reference=reference,
                                           currency=currency, amount=amount))
        p = Payment(tx_ref="TW-M-" + uuid.uuid4().hex[:14], company_id=c.id, plan=plan, amount=paid_amount,
                    currency=currency,
                    days=PLAN_DAYS[plan], method=method)
        session.add(p)
        session.commit()
        credit(session, p, reference=reference.strip(), by=u.id)
        audit(session, u, "payment_recorded", f"company:{c.id}", f"{currency} {paid_amount} {plan} by {method}, ref {reference.strip()[:60]}", request)
        session.commit()
        return RedirectResponse("/admin/payments?added=1", 303)

    @app.get("/admin/prices", response_class=HTMLResponse)
    def prices_admin(request: Request, session=Depends(db)):
        u = need(request, session, ("admin",))
        return prices_page(request, session, u)

    def prices_page(request, session, u, status=200, **ctx):
        rows = {(p.currency, p.plan): p for p in session.scalars(select(Price))}
        rows["trial"] = session.get(Setting, "trial_days")
        people = {x.id: x.name for x in session.scalars(select(User).where(User.role == "admin"))}
        return spage(request, session, u, "prices.html", "prices", status=status, rows=rows, people=people,
                    currencies=list(CURRENCIES), plans=list(PLAN_DAYS), **ctx)

    @app.post("/admin/prices", response_class=HTMLResponse)
    async def save_prices(request: Request, session=Depends(db)):
        u = need(request, session, ("admin",))
        form = await request.form()
        check_csrf(u, form.get("csrf", ""))
        new, errors = {}, []
        for cur in CURRENCIES:
            for plan in PLAN_DAYS:
                raw = str(form.get(f"{cur}_{plan}", "")).replace(",", "").strip()
                if not raw.isdigit() or not 0 < int(raw) <= 1_000_000:
                    errors.append(f"{cur} price for {PLAN_LABEL[plan]} must be a whole number above 0.")
                else:
                    new[(cur, plan)] = int(raw)
        raw = str(form.get("trial_days", "")).strip()
        if not raw.isdigit() or not 0 <= int(raw) <= 90:
            errors.append("Free trial must be 0 to 90 days.")
        if errors:
            return prices_page(request, session, u, status=400, error=" ".join(errors), form=form)
        changed = 0
        for (cur, plan), amount in new.items():
            p = session.scalars(select(Price).where(Price.currency == cur, Price.plan == plan)).first()
            if not p:
                p = Price(currency=cur, plan=plan, amount=amount)
                session.add(p)
            elif p.amount == amount:
                continue
            audit(session, u, "price_changed", f"{cur} {plan}", f"{p.amount if p.amount else 'none'} -> {amount}", request)
            p.amount, p.updated_by, p.updated_at = amount, u.id, now()
            changed += 1
        t = session.get(Setting, "trial_days")
        if int(raw) != trial_days(fresh=True):
            if not t:
                t = Setting(key="trial_days", value=raw)
                session.add(t)
            audit(session, u, "trial_changed", "trial days", f"{trial_days()} -> {int(raw)}", request)
            t.value, t.updated_by, t.updated_at = str(int(raw)), u.id, now()
            changed += 1
        session.commit()
        prices(fresh=True)
        return RedirectResponse(f"/admin/prices?saved={changed}", 303)

    # -- security: password and two-step sign-in, for every account
    def security_page(request, session, u, status=200, **ctx):
        setup = {}
        if not u.totp_secret:
            secret = auth.new_totp_secret()
            import segno
            uri = auth.otpauth_uri(secret, u.phone)
            setup = dict(token=setup_signer.dumps([u.id, secret]), key=" ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
                         qr=segno.make(uri, error="m").svg_inline(scale=4, dark="#101828", light="#ffffff"))
        left = len(json.loads(u.totp_backup or "[]")) if u.totp_secret else 0
        return page(request, "security.html", u, status=status, setup=setup, backup_left=left,
                    must=u.role in TWO_STEP_ROLES, admin=bool(u.totp_secret), **ctx)

    def signed_in(request, session):
        u = user_from(request, session)
        if not u:
            raise LoginRequired()
        return u

    @app.get("/security", response_class=HTMLResponse)
    def security(request: Request, session=Depends(db)):
        return security_page(request, session, signed_in(request, session))

    @app.post("/security/two-step/on", response_class=HTMLResponse)
    def two_step_on(request: Request, token: str = Form(""), code: str = Form(""), csrf: str = Form(""),
                    session=Depends(db)):
        u = signed_in(request, session)
        check_csrf(u, csrf)
        try:
            uid, secret = setup_signer.loads(token, max_age=1800)
        except BadSignature:
            uid, secret = None, ""
        if uid != u.id or u.totp_secret:
            return RedirectResponse("/security", 303)
        step = auth.totp_step(secret, code)
        if not step:
            return security_page(request, session, u, status=400,
                                 error="That code didn't match. Scan the new QR code below and enter the code your app shows now.")
        codes, hashes = auth.new_backup_codes()
        u.totp_secret, u.totp_step, u.totp_backup = secret, step, json.dumps(hashes)
        u.session_ver += 1  # other devices sign in again, with the code
        audit(session, u, "two_step_on", u.phone, "", request)
        session.commit()
        r = page(request, "backup_codes.html", u, codes=codes)
        return set_login(r, u)

    @app.post("/security/two-step/off")
    def two_step_off(request: Request, code: str = Form(""), csrf: str = Form(""), session=Depends(db)):
        u = signed_in(request, session)
        check_csrf(u, csrf)
        if u.role in TWO_STEP_ROLES:
            raise HTTPException(403, "Admins and reviewers must keep two-step sign-in on.")
        if not u.totp_secret or not auth.totp_step(u.totp_secret, code, u.totp_step):
            return security_page(request, session, u, status=400, error="Enter the current code from your app to turn it off.")
        u.totp_secret, u.totp_step, u.totp_backup = "", 0, ""
        audit(session, u, "two_step_off", u.phone, "", request)
        session.commit()
        return RedirectResponse("/security?off=1", 303)

    @app.post("/security/backup-codes", response_class=HTMLResponse)
    def new_backup(request: Request, code: str = Form(""), csrf: str = Form(""), session=Depends(db)):
        u = signed_in(request, session)
        check_csrf(u, csrf)
        step = u.totp_secret and auth.totp_step(u.totp_secret, code, u.totp_step)
        if not step:
            return security_page(request, session, u, status=400, error="Enter the current code from your app to get new backup codes.")
        codes, hashes = auth.new_backup_codes()
        u.totp_step, u.totp_backup = step, json.dumps(hashes)
        audit(session, u, "backup_codes_new", u.phone, "", request)
        session.commit()
        return page(request, "backup_codes.html", u, codes=codes)

    @app.post("/security/password", response_class=HTMLResponse)
    def change_password(request: Request, current: str = Form(""), new: str = Form(""), csrf: str = Form(""),
                        session=Depends(db)):
        u = signed_in(request, session)
        check_csrf(u, csrf)
        if not check_password(current, u.pw_hash):
            return security_page(request, session, u, status=400, error="Your current password isn't right.")
        if len(new) < MIN_PASSWORD:
            return security_page(request, session, u, status=400, error=f"Use a new password of at least {MIN_PASSWORD} characters.")
        u.pw_hash, u.session_ver = hash_password(new), u.session_ver + 1
        audit(session, u, "password_changed", u.phone, "", request)
        session.commit()
        return set_login(RedirectResponse("/security?password=1", 303), u)

    @app.post("/account/telegram/stop")
    def stop_telegram(request: Request, csrf: str = Form(""), session=Depends(db)):
        u = need(request, session, ("member",))
        check_csrf(u, csrf)
        u.telegram_chat = ""
        session.commit()
        return RedirectResponse("/account", 303)

    # -- called by the Telegram bot on this server only
    def internal(request):
        given = request.headers.get("x-internal-secret", "")
        if not s.internal_secret or not hmac.compare_digest(given, s.internal_secret):
            raise HTTPException(403, "No.")

    @app.post("/internal/telegram-link")
    async def telegram_link(request: Request, session=Depends(db)):
        internal(request)
        body = await request.json()
        try:
            uid = tg_signer.loads(str(body.get("token", "")), max_age=7 * 86400)
        except BadSignature:
            return {"ok": False, "error": "This link has expired. Open your account page and tap Connect again."}
        u = session.get(User, uid)
        if not u or not u.active:
            return {"ok": False, "error": "Account not found."}
        u.telegram_chat = str(int(body.get("chat_id")))
        session.commit()
        return {"ok": True, "name": u.name, "company": u.company.name if u.company else ""}

    @app.post("/internal/submit")
    async def submit_from_bot(request: Request, session=Depends(db)):
        """Tenders the website watcher found: they wait in the web Review list like collectors' entries."""
        internal(request)
        body = await request.json()
        name, url = str(body.get("source", ""))[:150], safe_url(str(body.get("url", ""))[:500])
        created = 0
        for e in body.get("entries", [])[:20]:
            if not isinstance(e, dict) or not str(e.get("title", "")).strip():
                continue
            row = Tender(id="web-" + uuid.uuid4().hex[:12], status="pending", source=f"{name} website",
                         url=url, extract=e, closes_raw=str(e.get("closes_raw", ""))[:120],
                         doubts=str(e.get("doubts", ""))[:4000],
                         **{k: str(e.get(k) or "")[:500] for k in ("buyer", "title", "category", "region", "place",
                                                                    "grade", "closes", "time", "type",
                                                                    "bid_security", "document_fee")})
            if row.category not in notices.CATEGORIES:
                row.category = "other"
            if row.type not in notices.TYPES:
                row.type = "gov"
            notices.tag(row)
            if not re.fullmatch(r"\d{4}-\d\d-\d\d", row.closes):
                row.closes = ""
            dup = duplicate_of(session, row)
            if dup:
                if dup.status == "pending" and dup.url == row.url:
                    continue  # the same page found again
                row.doubts = (f"Possibly already listed: {dup.title} ({dup.id}). " + row.doubts).strip()
            session.add(row)
            session.flush()
            created += 1
        session.commit()
        return {"created": created}

    @app.post("/internal/send-alerts")
    def send_alerts_now(request: Request, session=Depends(db)):
        internal(request)
        return {"sent": send_alerts(session)}

    def tg_send(chat, text):
        import urllib.request
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{s.telegram_token}/sendMessage",
            data=json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    app.state.tg_send = tg_send

    def send_alerts(session):
        """Each member with Telegram connected and an active trial or subscription gets the new open
        tenders that match their interests, once. Returns tenders sent."""
        sync(session)
        open_q = select(Tender).where(Tender.status == "published", or_(Tender.closes == "", Tender.closes >= today()))
        tenders = session.scalars(open_q.order_by(Tender.closes)).all()
        total = 0
        for u in session.scalars(select(User).where(User.role == "member", User.active, User.telegram_chat != "")):
            if not has_access(u):
                continue
            i = interests(u.company)
            if not (i["categories"] or i["keywords"]):
                continue
            done = set(session.scalars(select(AlertSent.tender_id).where(AlertSent.user_id == u.id)))
            new = [t for t in tenders if t.id not in done and match(t, i) and days_from(t.closes or "9999-12-31", today()) <= 183]
            if not new:
                continue
            lines = [f"{len(new)} new tender{'s' if len(new) > 1 else ''} for {u.company.name}:"]
            for t in new[:30]:
                when = (t.closes or "date not given") + (" " + t.time if t.time else "")
                lines.append(f"\n• {t.title[:180]}\n{t.buyer[:90]}\nCloses {when}\n{s.site_url}/t/{t.id}")
            lines.append(f"\nAll your matches: {s.site_url}/my")
            text, chunks = "", []
            for line in lines:
                if len(text) + len(line) > 3900:
                    chunks.append(text)
                    text = ""
                text += line + "\n"
            chunks.append(text)
            try:
                for c in chunks:
                    app.state.tg_send(u.telegram_chat, c)
            except Exception as e:
                print(f"alert to user {u.id} failed: {e}", flush=True)
                continue
            session.add_all([AlertSent(user_id=u.id, tender_id=t.id) for t in new])
            session.commit()
            total += len(new)
        return total

    @app.post("/account/interests")
    async def save_interests(request: Request, session=Depends(db)):
        u = need(request, session, ("member",))
        form = await request.form()
        check_csrf(u, form.get("csrf"))
        cats = [x for x in form.getlist("categories") if x in notices.CATEGORIES]
        regions = [x for x in form.getlist("regions") if x in notices.REGIONS]
        kws = [k.strip().lower()[:40] for k in str(form.get("keywords", "")).split(",") if k.strip()][:30]
        u.company.interests = json.dumps({"categories": cats, "regions": regions, "keywords": kws})
        session.commit()
        return RedirectResponse("/my?saved=1", 303)

    def match(t, i):
        if i["regions"] and t.region and t.region not in i["regions"] and t.region != "Several regions":
            return False
        hay = f"{t.title} {t.buyer}".lower()
        return bool(set(notices.cats(t)) & set(i["categories"])) or notices.eb.keyword_hit(i["keywords"], hay)

    @app.get("/my", response_class=HTMLResponse)
    def my_tenders(request: Request, p: int = 1, session=Depends(db)):
        u = need(request, session, ("member",))
        sync(session)
        i = interests(u.company)
        rows = []
        if i["categories"] or i["keywords"]:
            q = select(Tender).where(Tender.status == "published", or_(Tender.closes == "", Tender.closes >= today()))
            rows = [t for t in session.scalars(q.order_by(Tender.published_at.desc())) if match(t, i)]
        pg = paginate(rows, p)
        return page(request, "my.html", u, rows=pg["rows"], total=len(rows), pg=pg, qs="", i=i, access=has_access(u))

    # -- collectors
    @app.get("/collect", response_class=HTMLResponse)
    def collect_form(request: Request, session=Depends(db)):
        u = need(request, session, STAFF)
        mine = session.scalars(select(Tender).where(Tender.collector_id == u.id)
                               .order_by(Tender.created_at.desc()).limit(10)).all()
        return spage(request, session, u, "collect.html", "collect", mine=mine)

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
        if media not in IMAGE_TYPES or not body or not looks_like_image(body, media):
            return spage(request, session, u, "collect.html", "collect", status=400, error="Send the notice as a JPG or PNG photo.")
        if len(body) > MAX_PHOTO:
            return spage(request, session, u, "collect.html", "collect", status=400, error="That photo is over 15 MB. Take it again at normal size.")
        try:
            out = notices.extract(get_claude(), body, media, source)
        except notices.ExtractError as e:
            return spage(request, session, u, "collect.html", "collect", status=422, error=f"{e} Try again with a clearer photo.")
        if not out.get("tenders"):
            why = out.get("problem") or "No tender found in the photo."
            return spage(request, session, u, "collect.html", "collect", status=422, error=f"{why} Take a clear photo of one notice.")
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
            notices.tag(row)
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
        return spage(request, session, u, "entry.html", "review", t=t, new=new, siblings=siblings, editable=can_edit(u, t))

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
        if t.url and not safe_url(t.url):
            t.url = ""  # only http(s) links: a javascript: link would run in the reviewer's session
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
        if t.category != "construction" and "construction" not in form.getlist("also"):
            t.grade = ""
        if form.get("cats_shown"):  # the reviewer's ticks decide; the main category is always included
            order = list(notices.CATEGORIES)
            keep = {t.category} | {c for c in form.getlist("also") if c in notices.CATEGORIES}
            t.categories = "," + ",".join(sorted(keep, key=order.index)) + ","
        elif "category" in form:
            notices.tag(t, keep=[c for c in notices.cats(t) if c != "other"])
        action = form.get("action", "save")
        if action == "approve" and u.role in REVIEWERS:
            if not t.buyer or not t.title or VAGUE_TITLE.search(t.title):
                errors.append("A buyer and a real title (what is being bought or done) are needed before publishing.")
            if not t.closes and not form.get("no_date"):
                errors.append("Add the closing date, or tick 'The notice gives no closing date'.")
            if not errors:
                t.status, t.reviewer_id, t.published_at = "published", u.id, now()
                audit(session, u, "tender_approved", t.id, t.title[:200], request)
        elif action == "reject" and u.role in REVIEWERS:
            t.status, t.reviewer_id = "rejected", u.id
            audit(session, u, "tender_rejected", t.id, t.title[:200], request)
        if errors:
            session.rollback()
            t = session.get(Tender, tid)
            return spage(request, session, u, "entry.html", "review", status=400, t=t, new=0, siblings=[], editable=True,
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
            return RedirectResponse(f"/entry/{nxt.id}" if nxt else "/admin/review", 303)
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
        return spage(request, session, u, "mine.html", "collect", rows=rows, counts=counts, month=month)

    # -- reviewers
    @app.get("/admin/review", response_class=HTMLResponse)
    def review(request: Request, session=Depends(db)):
        u = need(request, session, REVIEWERS)
        rows = session.scalars(select(Tender).where(Tender.status == "pending").order_by(Tender.created_at)).all()
        return spage(request, session, u, "review.html", "review", rows=rows)

    @app.get("/admin/collectors", response_class=HTMLResponse)
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
        return spage(request, session, u, "stats.html", "collectors", table=sorted(table.items()), month=month)

    @app.get("/admin/team", response_class=HTMLResponse)
    def team(request: Request, session=Depends(db)):
        u = need(request, session, ("admin",))
        people = session.scalars(select(User).where(User.role.in_(STAFF)).order_by(User.role, User.name)).all()
        return spage(request, session, u, "team.html", "team", people=people)

    @app.post("/admin/team", response_class=HTMLResponse)
    def add_person(request: Request, name: str = Form(""), phone: str = Form(""), role: str = Form("collector"),
                   password: str = Form(""), csrf: str = Form(""), session=Depends(db)):
        u = need(request, session, ("admin",))
        check_csrf(u, csrf)
        people = session.scalars(select(User).where(User.role.in_(STAFF)).order_by(User.role, User.name)).all()
        p = norm_phone(phone)
        error = ("Enter a name." if not name.strip() else
                 "Enter an Ethiopian mobile number, like 0911 234 567." if not p else
                 f"Use a password of at least {MIN_PASSWORD} characters." if len(password) < MIN_PASSWORD else
                 "Pick a role." if role not in STAFF else
                 "Someone already uses that phone number." if session.scalars(select(User).where(User.phone == p)).first()
                 else "")
        if error:
            return spage(request, session, u, "team.html", "team", status=400, people=people, error=error,
                        form=dict(name=name, phone=phone, role=role))
        session.add(User(name=name.strip()[:120], phone=p, role=role, pw_hash=hash_password(password)))
        audit(session, u, "staff_added", p, f"{name.strip()[:80]} as {role}", request)
        session.commit()
        return RedirectResponse("/admin/team?added=1", 303)

    @app.post("/admin/team/{uid}/toggle")
    def toggle_person(request: Request, uid: int, csrf: str = Form(""), session=Depends(db)):
        u = need(request, session, ("admin",))
        check_csrf(u, csrf)
        p = session.get(User, uid)
        if p and p.id != u.id:
            p.active = not p.active
            p.session_ver += 1
            audit(session, u, "staff_on" if p.active else "staff_off", p.phone, f"{p.name} ({p.role})", request)
            session.commit()
        return RedirectResponse("/admin/team", 303)

    @app.post("/admin/team/{uid}/reset-two-step")
    def reset_two_step(request: Request, uid: int, csrf: str = Form(""), session=Depends(db)):
        u = need(request, session, ("admin",))
        check_csrf(u, csrf)
        p = session.get(User, uid)
        if p and p.id != u.id and p.totp_secret:
            p.totp_secret, p.totp_step, p.totp_backup, p.session_ver = "", 0, "", p.session_ver + 1
            audit(session, u, "two_step_reset", p.phone, f"{p.name} ({p.role}) sets it up again at next sign-in", request)
            session.commit()
        return RedirectResponse("/admin/team?reset=1", 303)

    from types import SimpleNamespace
    from . import admin as admin_console
    admin_console.register(app, SimpleNamespace(db=db, need=need, page=page, audit=audit, today=today, now=now,
                                                paginate=paginate, check_csrf=check_csrf, TZ=TZ, sync=sync,
                                                board_json=s.board_json, site_url=s.site_url, photos=photos))
    from . import bidroom
    bidroom.register(app, SimpleNamespace(db=db, need=need, page=page, today=today, now=now, check_csrf=check_csrf,
                                          TZ=TZ, has_access=has_bidroom, get_claude=get_claude, Session=Session,
                                          sync_drafts=False))
    app.state.Session = Session
    return app
