import datetime as dt
import hashlib
import json
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
from .db import AlertSent, Company, LoginFailure, Payment, Tender, User, make_session, now
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
TRIAL_DAYS = 14
MAX_SIGNUPS_PER_HOUR = 5
PRICES = {"month": 299, "year": 2400}
PLAN_DAYS = {"month": 30, "year": 365}
PAY_METHODS = {"chapa": "Chapa", "telebirr": "Telebirr", "bank": "Bank transfer"}
CHECK_HOURS = 48  # stop asking Chapa about a checkout after this


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
    tg_signer = URLSafeTimedSerializer(s.secret, salt="tw-telegram-link")
    app = FastAPI(title="Tender Watcher", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
    tpl = Jinja2Templates(directory=os.path.join(HERE, "templates"))
    tpl.env.globals.update(CATEGORIES=notices.CATEGORIES, TYPES=notices.TYPES, REGIONS=notices.REGIONS)
    tpl.env.filters.update(days_from=days_from, short_date=short_date, long_date=long_date, host=host)
    tpl.env.globals.update(date_notes=date_notes)
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
        ctx.update(request=request, me=u, csrf=csrf_token(u) if u else "", today=today())
        return tpl.TemplateResponse(request, name, ctx, status_code=status)

    sync = BoardSync(s.board_json)
    board_notices = os.path.join(os.path.dirname(os.path.abspath(s.board_json)), "notices") if s.board_json else ""

    def has_access(u):
        if not u:
            return False
        if u.role in STAFF:
            return True
        c = u.company
        return bool(c) and today() <= max(c.trial_until or "", c.paid_until or "")

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
        sync(session)
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
        sync(session)
        t = session.get(Tender, tid)
        if not t or (t.status != "published" and not (u and u.role in STAFF)):
            return page(request, "message.html", u, status=404, title="This tender has closed or is no longer listed",
                        text="Tenders leave the list after their closing date.", link=("/", "See all open tenders"))
        return page(request, "tender.html", u, t=t, full=has_access(u))

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
            session.add_all([LoginFailure(key=k) for k in keys])
            session.commit()
            return page(request, "login.html", None, status=400, next=next,
                        error="That phone number and password don't match. Check both and try again.")
        session.query(LoginFailure).filter(LoginFailure.key == "phone:" + p).delete()
        session.query(LoginFailure).filter(LoginFailure.at < now() - dt.timedelta(days=1)).delete()
        session.commit()
        if not next.startswith("/") or next.startswith("//"):
            next = "/"
        if next == "/":
            next = "/collect" if u.role in STAFF else "/my"
        r = RedirectResponse(next, 303)
        r.set_cookie("tw", signer.dumps([u.id, u.session_ver]), max_age=30 * 86400, httponly=True,
                     samesite="lax", secure=s.secure_cookies)
        return r

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
        end = (dt.date.fromisoformat(today()) + dt.timedelta(days=TRIAL_DAYS)).isoformat()
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
        return page(request, "account.html", u, c=c, i=interests(c), access=has_access(u), prices=PRICES,
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
        start = max(today(), c.paid_until or "", c.trial_until or "")
        c.paid_until = (dt.date.fromisoformat(start) + dt.timedelta(days=p.days)).isoformat()
        p.status, p.paid_at, p.paid_until = "paid", now(), c.paid_until
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
            ok = chapa.paid(p.tx_ref, p.amount)
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
    def pay(request: Request, plan: str = Form(""), csrf: str = Form(""), session=Depends(db)):
        u = need(request, session, ("member",))
        check_csrf(u, csrf)
        if plan not in PRICES:
            raise HTTPException(400, "Pick a plan.")
        if not app.state.chapa:
            raise HTTPException(503, "Online payment is not open yet. Pay by Telebirr or bank transfer for now.")
        p = Payment(tx_ref="TW-" + uuid.uuid4().hex[:16], company_id=u.company_id, plan=plan,
                    amount=PRICES[plan], days=PLAN_DAYS[plan])
        session.add(p)
        session.commit()
        try:
            url = app.state.chapa.checkout(p.tx_ref, p.amount, u.company.name,
                                           f"{s.site_url}/account/paid?ref={p.tx_ref}")
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
        return page(request, "payments.html", u, status=status, rows=rows, companies=companies, prices=PRICES,
                    methods=PAY_METHODS, chapa_on=bool(app.state.chapa), **ctx)

    @app.post("/admin/payments", response_class=HTMLResponse)
    def record_payment(request: Request, company_id: int = Form(0), plan: str = Form(""), method: str = Form(""),
                       reference: str = Form(""), csrf: str = Form(""), session=Depends(db)):
        u = need(request, session, ("admin",))
        check_csrf(u, csrf)
        c = session.get(Company, company_id)
        error = ("Pick the company." if not c else "Pick a plan." if plan not in PRICES else
                 "Pick Telebirr or bank transfer." if method not in ("telebirr", "bank") else
                 "Enter the Telebirr or bank transaction number." if not reference.strip() else
                 "That transaction number is already recorded." if session.scalars(
                     select(Payment).where(Payment.reference == reference.strip()[:120])).first() else "")
        if error:
            return payments_page(request, session, u, status=400, error=error,
                                 form=dict(company_id=company_id, plan=plan, method=method, reference=reference))
        p = Payment(tx_ref="TW-M-" + uuid.uuid4().hex[:14], company_id=c.id, plan=plan, amount=PRICES[plan],
                    days=PLAN_DAYS[plan], method=method)
        session.add(p)
        session.commit()
        credit(session, p, reference=reference.strip(), by=u.id)
        return RedirectResponse("/admin/payments?added=1", 303)

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
        return t.category in i["categories"] or notices.eb.keyword_hit(i["keywords"], hay)

    @app.get("/my", response_class=HTMLResponse)
    def my_tenders(request: Request, session=Depends(db)):
        u = need(request, session, ("member",))
        sync(session)
        i = interests(u.company)
        rows = []
        if i["categories"] or i["keywords"]:
            q = select(Tender).where(Tender.status == "published", or_(Tender.closes == "", Tender.closes >= today()))
            rows = [t for t in session.scalars(q.order_by(Tender.published_at.desc())) if match(t, i)]
        return page(request, "my.html", u, rows=rows, i=i, access=has_access(u))

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
                 f"Use a password of at least {MIN_PASSWORD} characters." if len(password) < MIN_PASSWORD else
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
            p.session_ver += 1
            session.commit()
        return RedirectResponse("/admin/team", 303)

    app.state.Session = Session
    return app
