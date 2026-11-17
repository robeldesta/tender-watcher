import json
import os
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tw.auth import hash_password, norm_phone
from tw.config import Settings
from tw.db import Tender, User
from tw.main import create_app

NOTICE = {"newspaper": "Addis Zemen, 5 Nov 2026", "problem": "", "tenders": [
    {"buyer": "Bahir Dar City Roads Authority", "title": "Construction of cobblestone road",
     "region": "Amhara", "place": "Bahir Dar", "grade": "gc 5",
     "closes": {"raw": "ኅዳር 11 ቀን 2019 ዓ.ም", "calendar": "ethiopian", "year": 2019, "month": 3, "day": 11},
     "time": "10:00", "type": "gov", "bid_security": "ETB 50,000", "document_fee": "ETB 300",
     "category": "construction", "doubts": ""},
    {"buyer": "Bahir Dar University", "title": "Supply of laptops", "region": "Amhara", "place": "Bahir Dar",
     "grade": "", "closes": {"raw": "", "calendar": "gregorian", "year": 2026, "month": 11, "day": 30},
     "time": "", "type": "gov", "bid_security": "", "document_fee": "", "category": "it", "doubts": "Time unclear"}]}
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
TOTP = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


class FakeClaude:
    def __init__(self, out=NOTICE):
        self.out, self.calls = out, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(self.out))])


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("tw.main.today", lambda: "2026-11-07")
    board = tmp_path / "tenders.json"
    board.write_text(json.dumps({"updated": "", "tenders": []}))
    s = Settings({"DATABASE_URL": f"sqlite:///{tmp_path}/t.db", "SECRET_KEY": "test", "DATA_DIR": str(tmp_path / "appdata"),
                  "BOARD_JSON": str(board), "SECURE_COOKIES": "0", "INTERNAL_SECRET": "sekret"})
    claude = FakeClaude()
    app = create_app(s, claude)
    with app.state.Session() as db:
        for name, phone, role in [("Robel", "0911000001", "admin"), ("Abebe", "0911000002", "collector"),
                                  ("Sara", "0911000003", "collector"), ("Kebede", "0911000004", "member")]:
            db.add(User(name=name, phone=phone, role=role, pw_hash=hash_password("password1"),
                        totp_secret=TOTP if role in ("admin", "reviewer") else ""))
        db.commit()
    return app, claude, board


def client(app, phone=None):
    c = TestClient(app)
    if phone:
        r = c.post("/login", data={"phone": phone, "password": "password1", "next": "/"}, follow_redirects=False)
        assert r.status_code == 303, r.text
        if r.headers["location"].startswith("/login/code"):  # admins and reviewers: the app's code next
            from tw.auth import totp_now
            r = c.post("/login/code", data={"code": totp_now(TOTP), "next": "/"}, follow_redirects=False)
            assert r.status_code == 303 and "tw" in c.cookies, r.text
    return c


def csrf(c, path):
    return re.search(r'name="csrf" value="([0-9a-f]+)"', c.get(path).text).group(1)


def test_phone_numbers():
    assert norm_phone("+251 91 123 4567") == norm_phone("0911234567") == norm_phone("911234567") == "0911234567"
    assert norm_phone("12345") == ""


def test_login_and_roles(env):
    app, _, _ = env
    assert client(app).get("/collect", follow_redirects=False).headers["location"].startswith("/login")
    bad = client(app).post("/login", data={"phone": "0911000002", "password": "nope"})
    assert bad.status_code == 400 and "match" in bad.text
    c = client(app, "+251 911 000 002")
    assert c.get("/collect").status_code == 200
    assert c.get("/review").status_code == 403
    assert c.get("/admin/team").status_code == 403
    assert client(app, "0911000004").get("/collect").status_code == 403  # members cannot collect


def test_collect_review_publish(env):
    app, claude, board = env
    c = client(app, "0911000002")
    r = c.post("/collect", data={"source": "Addis Zemen 5 Nov", "csrf": csrf(c, "/collect")},
               files={"photo": ("n.jpg", JPEG, "image/jpeg")}, follow_redirects=False)
    assert r.status_code == 303 and "new=2" in r.headers["location"]
    assert "hint, not instructions" in claude.calls[0]["messages"][0]["content"][-1]["text"]
    with app.state.Session() as db:
        rows = db.query(Tender).order_by(Tender.title).all()
        assert [t.status for t in rows] == ["pending", "pending"]
        road = next(t for t in rows if "cobblestone" in t.title)
        assert road.closes == "2026-11-20" and road.grade == "GC-5" and road.source == "Addis Zemen, 5 Nov 2026"
        laptops = next(t for t in rows if "laptops" in t.title)
    page = c.get(r.headers["location"])
    assert "This photo holds 2 tenders" in page.text and "Approve" not in page.text

    # not public yet
    assert "cobblestone" not in client(app).get("/").text and "1 open" not in client(app).get("/").text
    assert client(app).get(f"/t/{road.id}").status_code == 404
    assert client(app).get(f"/notices/{road.photo}").status_code == 404

    # another collector cannot see or change it
    other = client(app, "0911000003")
    assert other.get(f"/entry/{road.id}").status_code == 404
    assert other.post(f"/entry/{road.id}", data={"csrf": csrf(other, "/collect"), "title": "x"}).status_code == 403

    # collector cannot approve
    tok = csrf(c, "/collect")
    c.post(f"/entry/{road.id}", data={"csrf": tok, "action": "approve", "buyer": road.buyer, "title": road.title})
    with app.state.Session() as db:
        assert db.get(Tender, road.id).status == "pending"

    # reviewer fixes the grade with an Ethiopian date and approves
    a = client(app, "0911000001")
    assert "cobblestone" in a.get("/review").text
    r = a.post(f"/entry/{road.id}", data={"csrf": csrf(a, "/collect"), "action": "approve", "buyer": road.buyer,
                                          "title": road.title, "grade": "GC-4", "category": "construction",
                                          "eth_day": "12", "eth_month": "3", "eth_year": "2019"}, follow_redirects=False)
    assert r.headers["location"] == f"/entry/{laptops.id}"  # next pending opens
    with app.state.Session() as db:
        t = db.get(Tender, road.id)
        assert t.status == "published" and t.grade == "GC-4" and t.closes == "2026-11-21"
    home = client(app).get("/").text
    assert "cobblestone" in home and "Supply of laptops" not in home
    anon = client(app).get(f"/t/{road.id}").text
    assert "Saturday 21 Nov 2026" in anon and "ETB 50,000" not in anon and "free trial" in anon
    assert client(app).get(f"/notices/{road.photo}").status_code == 403
    detail = a.get(f"/t/{road.id}").text
    assert "ETB 50,000" in detail
    assert a.get(f"/notices/{road.photo}").status_code == 200
    data = json.loads(board.read_text())["tenders"]
    assert data[0]["id"] == road.id and data[0]["url"] == f"/notices/{road.photo}"
    assert (board.parent / "notices" / road.photo).read_bytes() == JPEG  # photo copied for the static site

    # reject the second; stats count per collector
    a.post(f"/entry/{laptops.id}", data={"csrf": csrf(a, "/collect"), "action": "reject"})
    stats = a.get("/admin/stats?month=2026-11").text
    assert "Abebe" in stats
    mine = c.get("/mine").text
    assert "Rejected" in mine and "Published" in mine


def test_bad_inputs(env):
    app, claude, _ = env
    c = client(app, "0911000002")
    tok = csrf(c, "/collect")
    assert c.post("/collect", data={"csrf": tok}, files={"photo": ("x.txt", b"hello", "text/plain")}).status_code == 400
    assert c.post("/collect", data={"csrf": "bad"}, files={"photo": ("n.jpg", JPEG, "image/jpeg")}).status_code == 400
    claude.out = {"newspaper": "", "tenders": [], "problem": "It is a menu."}
    r = c.post("/collect", data={"csrf": tok}, files={"photo": ("n.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 422 and "It is a menu." in r.text
    claude.out = NOTICE
    loc = c.post("/collect", data={"csrf": tok}, files={"photo": ("n.jpg", JPEG, "image/jpeg")},
                 follow_redirects=False).headers["location"]
    tid = loc.split("/")[2].split("?")[0]
    r = c.post(f"/entry/{tid}", data={"csrf": tok, "time": "25:00", "eth_day": "31", "eth_month": "3", "eth_year": "2019"})
    assert r.status_code == 400 and "Ethiopian date" in r.text and "10:00" in r.text
    # a second photo of the same notice is flagged as a possible duplicate
    loc2 = c.post("/collect", data={"csrf": tok}, files={"photo": ("n.jpg", JPEG, "image/jpeg")},
                  follow_redirects=False).headers["location"]
    assert "Possibly already listed" in c.get(loc2).text


def test_team_admin(env):
    app, _, _ = env
    a = client(app, "0911000001")
    tok = csrf(a, "/admin/team")
    r = a.post("/admin/team", data={"csrf": tok, "name": "Hana", "phone": "0922 111 222", "role": "collector",
                                    "password": "short"})
    assert r.status_code == 400 and "10 characters" in r.text
    r = a.post("/admin/team", data={"csrf": tok, "name": "Hana", "phone": "0922 111 222", "role": "collector",
                                    "password": "longenough"}, follow_redirects=False)
    assert r.status_code == 303
    h = TestClient(app)
    assert h.post("/login", data={"phone": "0922111222", "password": "longenough"}, follow_redirects=False).status_code == 303
    assert a.post("/admin/team", data={"csrf": tok, "name": "X", "phone": "0922111222", "role": "collector",
                                       "password": "longenough"}).status_code == 400  # same phone
    with app.state.Session() as db:
        hana = db.query(User).filter_by(phone="0922111222").one()
    a.post(f"/admin/team/{hana.id}/toggle", data={"csrf": tok})
    assert TestClient(app).post("/login", data={"phone": "0922111222", "password": "longenough"}).status_code == 400


def test_import_board_own_sources_only(env, tmp_path, monkeypatch):
    app, _, _ = env
    from tw import manage
    path = tmp_path / "b.json"
    path.write_text(json.dumps({"tenders": [
        {"id": "wb-OP1", "buyer": "ESS", "title": "Servers", "closes": "2026-11-20", "category": "it",
         "notice": "Full text", "url": "https://projects.worldbank.org/x", "firstSeen": "2026-11-07"},
        {"id": "mt-abc", "buyer": "X", "title": "Copied", "closes": "2026-11-20"}]}))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("SECRET_KEY", "test")
    manage.main(["import-board", str(path)])
    page = client(app, "0911000001").get("/t/wb-OP1").text
    assert "Full text" in page and "projects.worldbank.org" in page
    assert client(app).get("/t/mt-abc").status_code == 404


def test_login_throttle_and_signout(env):
    app, _, _ = env
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/login", data={"phone": "0911000002", "password": "wrong"}).status_code == 400
    r = c.post("/login", data={"phone": "0911000002", "password": "password1"})
    assert r.status_code == 429 and "Too many tries" in r.text  # even the right password waits
    assert client(app, "0911000003").get("/collect").status_code == 200  # other people are not locked out

    # signing out ends the session on every device
    a, b = client(app, "0911000003"), client(app, "0911000003")
    a.post("/logout")
    assert b.get("/collect", follow_redirects=False).status_code == 303


def test_signup_trial_interests_matches(env, monkeypatch):
    app, _, board = env
    board.write_text(json.dumps({"tenders": [
        {"id": "wb-1", "buyer": "ESS", "title": "Supply of servers", "closes": "2026-11-20", "category": "it",
         "region": "Addis Ababa", "notice": "Full text here"},
        {"id": "web-2", "buyer": "EEU", "title": "Procurement of 4 pickup trucks", "closes": "2026-11-25",
         "category": "vehicles", "region": "Oromia"},
        {"id": "mt-3", "buyer": "X", "title": "Copied servers", "closes": "2026-11-25", "category": "it"}]}))
    c = TestClient(app)
    home = c.get("/").text
    assert "Supply of servers" in home and "Copied servers" not in home  # synced from the board, own sources only
    r = c.post("/signup", data={"company": "Sonador Systems", "name": "Robel", "phone": "0911000001",
                                "password": "longenough1"})
    assert r.status_code == 400 and "already has an account" in r.text
    r = c.post("/signup", data={"company": "Sonador Systems", "name": "Robel D", "phone": "0933 444 555",
                                "password": "longenough1", "tin": "0088565182"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/account?welcome=1"
    acc = c.get("/account?welcome=1").text
    assert "Free trial" in acc and "14 days left" in acc and "Sat 21 Nov" not in acc
    assert "Full text here" in c.get("/t/wb-1").text  # trial sees full notices
    assert "Set your interests" in c.get("/my").text
    tok = re.search(r'name="csrf" value="([0-9a-f]+)"', acc).group(1)
    c.post("/account/interests", data={"csrf": tok, "categories": ["it"], "keywords": "pickup, laptops"})
    my = c.get("/my").text
    assert "Supply of servers" in my and "pickup trucks" in my
    c.post("/account/interests", data={"csrf": tok, "categories": ["it"], "regions": ["Oromia"]})
    my = c.get("/my").text
    assert "Supply of servers" not in my  # Addis Ababa filtered out
    assert c.get("/collect").status_code == 403  # members are not collectors

    # trial over: no full notice
    monkeypatch.setattr("tw.main.today", lambda: "2026-11-22")
    assert "Full text here" not in c.get("/t/wb-1").text
    assert "trial has ended" in c.get("/account").text


def test_date_notes_signup_cap_and_bot_photos(env, monkeypatch):
    app, _, board = env
    (board.parent / "notices").mkdir(exist_ok=True)
    (board.parent / "notices" / "abcdef123456.jpg").write_bytes(JPEG)
    board.write_text(json.dumps({"tenders": [
        {"id": "wb-9", "buyer": "ESS", "title": "Servers", "closes": "2026-11-20", "time": "02:00",
         "place": "P.O.Box 62347\r\nAddis Ababa"},
        {"id": "fc-abcdef123456", "buyer": "AACRA", "title": "Road", "closes": "2026-11-25",
         "url": "/notices/abcdef123456.jpg"}]}))
    anon = TestClient(app)
    page = anon.get("/t/wb-9").text
    assert "08:00 international time" in page and "P.O.Box 62347, Addis Ababa" in page
    a = client(app, "0911000001")
    assert a.get("/notices/abcdef123456.jpg").status_code == 200  # photo published by the Telegram bot
    for i in range(5):
        r = anon.post("/signup", data={"company": f"C{i}", "name": "N", "phone": f"09440000{i:02d}",
                                       "password": "longenough1"}, follow_redirects=False)
        assert r.status_code == 303
    r = anon.post("/signup", data={"company": "C9", "name": "N", "phone": "0944000099", "password": "longenough1"})
    assert r.status_code == 429


def test_telegram_link_and_alerts(env, monkeypatch):
    app, _, board = env
    sent = []
    app.state.tg_send = lambda chat, text: sent.append((chat, text))
    board.write_text(json.dumps({"tenders": [
        {"id": "wb-1", "buyer": "ESS", "title": "Supply of servers", "closes": "2026-11-20", "category": "it"},
        {"id": "wb-2", "buyer": "AU", "title": "Catering services", "closes": "2026-11-20", "category": "services"}]}))
    c = TestClient(app)
    c.post("/signup", data={"company": "Sonador", "name": "R", "phone": "0955000000", "password": "longenough1"})
    acc = c.get("/account").text
    tok = re.search(r'name="csrf" value="([0-9a-f]+)"', acc).group(1)
    c.post("/account/interests", data={"csrf": tok, "categories": ["it"]})
    start = re.search(r"https://t\.me/tender_watcher_entry_bot\?start=([^\"]+)\"", acc).group(1)

    # only the bot (with the shared secret) can link or trigger alerts
    assert c.post("/internal/telegram-link", json={"token": start, "chat_id": 42}).status_code == 403
    h = {"x-internal-secret": "sekret"}
    assert c.post("/internal/telegram-link", json={"token": "forged", "chat_id": 42}, headers=h).json()["ok"] is False
    r = c.post("/internal/telegram-link", json={"token": start, "chat_id": 42}, headers=h).json()
    assert r["ok"] and r["company"] == "Sonador"
    assert "Telegram is connected" in c.get("/account").text

    assert c.post("/internal/send-alerts", headers=h).json() == {"sent": 1}
    (chat, text), = sent
    assert chat == "42" and "Supply of servers" in text and "Catering" not in text and "/t/wb-1" in text
    assert c.post("/internal/send-alerts", headers=h).json() == {"sent": 0}  # never twice

    # trial over: no alerts
    board.write_text(json.dumps({"tenders": [{"id": "wb-3", "buyer": "X", "title": "Laptops", "closes": "2026-12-20",
                                              "category": "it"}]}))
    monkeypatch.setattr("tw.main.today", lambda: "2026-11-25")
    assert c.post("/internal/send-alerts", headers=h).json() == {"sent": 0}


class FakeChapa:
    def __init__(self):
        self.made, self.paid_refs, self.asked = [], set(), []

    def checkout(self, tx_ref, amount, name, return_url, currency="ETB"):
        self.made.append((tx_ref, amount, name, return_url))
        self.currency = currency
        return "https://checkout.chapa.co/checkout/payment/" + tx_ref

    def paid(self, tx_ref, amount, currency="ETB"):
        self.asked.append((tx_ref, amount, currency))
        return tx_ref in self.paid_refs


def test_chapa_checkout_and_manual_payments(env, monkeypatch):
    app, _, _ = env
    c = TestClient(app)
    c.post("/signup", data={"company": "Myagta", "name": "M", "phone": "0955000001", "password": "longenough1"})
    tok = csrf(c, "/account")
    assert 'value="win_year"' not in c.get("/account").text  # no key: no online button
    assert c.post("/account/pay", data={"csrf": tok, "plan": "year"}).status_code == 503

    chapa = app.state.chapa = FakeChapa()
    acc = c.get("/account").text
    assert all(x in acc for x in ("ETB 2,400", "ETB 799", "ETB 1,399", "ETB 7,900", "ETB 2,690", "ETB 4,790", 'value="win_m6"'))
    assert "ETB 266 a month" in acc and "ETB 658 a month" in acc
    assert c.post("/account/pay", data={"csrf": "bad", "plan": "year"}).status_code == 400
    assert c.post("/account/pay", data={"csrf": tok, "plan": "week"}).status_code == 400
    r = c.post("/account/pay", data={"csrf": tok, "plan": "year"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("https://checkout.chapa.co/")
    ref, amount, name, back = chapa.made[-1]
    assert ref.startswith("TW-") and amount == 2400 and name == "Myagta" and back.endswith("/account/paid?ref=" + ref)

    # back from Chapa before it confirms
    r = c.get("/account/paid?ref=" + ref, follow_redirects=False)
    assert r.headers["location"] == "/account?payment=waiting"
    # another company's or a made-up ref tells nothing
    assert c.get("/account/paid?ref=TW-nope", follow_redirects=False).headers["location"] == "/account?payment=unknown"

    chapa.paid_refs.add(ref)
    assert c.get("/account/paid?ref=" + ref, follow_redirects=False).headers["location"] == "/account?payment=paid"
    acc = c.get("/account?payment=paid").text
    # trial ends 21 Nov; a year is added after it
    assert "Watch until" in acc and "2027-11-21" not in acc and "21 Nov 2027" in acc and "payment went through" in acc
    # credited once, even when asked again
    c.get("/account/paid?ref=" + ref)
    h = {"x-internal-secret": "sekret"}
    assert c.post("/internal/check-payments", headers=h).json() == {"credited": 0}
    with app.state.Session() as db:
        from tw.db import Company, Payment
        assert db.query(Company).filter_by(name="Myagta").one().paid_until == "2027-11-21"
        assert db.query(Payment).filter_by(status="paid").count() == 1

    # a checkout paid without coming back is picked up by the bot's call
    c.post("/account/pay", data={"csrf": tok, "plan": "month"})
    ref2 = chapa.made[-1][0]
    assert c.post("/internal/check-payments").status_code == 403
    assert c.post("/internal/check-payments", headers=h).json() == {"credited": 0}
    chapa.paid_refs.add(ref2)
    assert c.post("/internal/check-payments", headers=h).json() == {"credited": 0}  # asked under a minute ago
    with app.state.Session() as db:
        p = db.query(Payment).filter_by(tx_ref=ref2).one()
        p.checked_at = None
        db.commit()
    assert c.post("/internal/check-payments", headers=h).json() == {"credited": 1}
    with app.state.Session() as db:
        assert db.query(Company).filter_by(name="Myagta").one().paid_until == "2027-12-21"

    # admin records a Telebirr payment for another company
    c2 = TestClient(app)
    c2.post("/signup", data={"company": "Delta", "name": "D", "phone": "0955000002", "password": "longenough1"})
    assert client(app, "0911000004").get("/admin/payments").status_code == 403
    a = client(app, "0911000001")
    atok = csrf(a, "/admin/payments")
    with app.state.Session() as db:
        delta = db.query(Company).filter_by(name="Delta").one().id
    bad = a.post("/admin/payments", data={"csrf": atok, "company_id": delta, "plan": "month", "method": "telebirr", "reference": ""})
    assert bad.status_code == 400 and "transaction number" in bad.text
    r = a.post("/admin/payments", data={"csrf": atok, "company_id": delta, "plan": "month", "method": "telebirr",
                                        "reference": "TB123"}, follow_redirects=False)
    assert r.status_code == 303
    again = a.post("/admin/payments", data={"csrf": atok, "company_id": delta, "plan": "month", "method": "telebirr",
                                            "reference": "TB123"})
    assert again.status_code == 400 and "already recorded" in again.text
    page_ = a.get("/admin/payments").text
    assert "Delta" in page_ and "TB123" in page_ and "Myagta" in page_
    with app.state.Session() as db:
        assert db.query(Company).filter_by(name="Delta").one().paid_until == "2026-12-21"

    # an unpaid checkout expires after two days
    c.post("/account/pay", data={"csrf": tok, "plan": "month"})
    ref3 = chapa.made[-1][0]
    import datetime as dt
    with app.state.Session() as db:
        p = db.query(Payment).filter_by(tx_ref=ref3).one()
        p.created_at = p.created_at - dt.timedelta(days=3)
        db.commit()
    c.post("/internal/check-payments", headers=h)
    with app.state.Session() as db:
        assert db.query(Payment).filter_by(tx_ref=ref3).one().status == "expired"

    # a firm abroad pays in US dollars: own price, checked in USD
    assert "USD 99" in c.get("/account?cur=USD").text
    assert c.post("/account/pay", data={"csrf": tok, "plan": "year", "currency": "GBP"}).status_code == 400
    c.post("/account/pay", data={"csrf": tok, "plan": "year", "currency": "USD"})
    ref4, amount4 = chapa.made[-1][:2]
    assert amount4 == 99 and chapa.currency == "USD"
    chapa.paid_refs.add(ref4)
    assert c.get("/account/paid?ref=" + ref4, follow_redirects=False).headers["location"] == "/account?payment=paid"
    assert chapa.asked[-1] == (ref4, 99, "USD")
    assert "USD 99" in c.get("/account").text

    # admin records a EUR bank transfer; the amount is needed, Telebirr stays in birr
    bad = a.post("/admin/payments", data={"csrf": atok, "company_id": delta, "plan": "year", "method": "bank",
                                          "currency": "EUR", "reference": "SW1"})
    assert bad.status_code == 400 and "amount received" in bad.text
    bad = a.post("/admin/payments", data={"csrf": atok, "company_id": delta, "plan": "year", "method": "telebirr",
                                          "currency": "USD", "reference": "TB9"})
    assert bad.status_code == 400 and "in ETB" in bad.text
    a.post("/admin/payments", data={"csrf": atok, "company_id": delta, "plan": "year", "method": "bank",
                                    "currency": "EUR", "amount": "90", "reference": "SW1"})
    assert "EUR 90" in a.get("/admin/payments").text
    with app.state.Session() as db:
        assert db.query(Company).filter_by(name="Delta").one().paid_until == "2027-12-21"


def test_pricing_page_is_public(env):
    app, _, _ = env
    c = TestClient(app)
    r = c.get("/pricing")
    assert r.status_code == 200 and "ETB 299" in r.text and "ETB 2,400" in r.text and "14 days" in r.text
    assert "ETB 799" in r.text and "ETB 1,399" in r.text and "ETB 233 a month" in r.text and "33% off" in r.text
    assert "ETB 990" in r.text and "ETB 2,690" in r.text and "ETB 4,790" in r.text and "19% off" in r.text and "Bid Room" in r.text and "Coming soon" not in r.text and "US dollars" in r.text
    usd = c.get("/pricing?cur=USD").text
    assert "USD 12" in usd and "USD 99" in usd and "USD 32" in usd and "USD 55" in usd and "USD 39" in usd and "ETB 299" not in usd
    assert c.get("/pricing?cur=XYZ").text.count("ETB 299") >= 1
    assert 'href="/pricing"' in c.get("/").text


def test_website_finds_go_to_web_review(env):
    app, _, _ = env
    c = TestClient(app)
    entry = {"buyer": "Ethiopian Airlines", "title": "Supply of ground power units", "category": "vehicles",
             "region": "Addis Ababa", "place": "Bole", "closes": "2026-11-30", "time": "10:00", "type": "company",
             "doubts": "Bid security not stated", "closes_raw": "30 Nov 2026"}
    body = {"source": "Ethiopian Airlines", "url": "https://corporate.ethiopianairlines.com/tenders/123", "entries": [entry]}
    assert c.post("/internal/submit", json=body).status_code == 403
    h = {"x-internal-secret": "sekret"}
    assert c.post("/internal/submit", json=body, headers=h).json() == {"created": 1}
    assert c.post("/internal/submit", json=body, headers=h).json() == {"created": 0}  # same page again
    # the same bank tender read again from its notice PDF, worded differently: one row, with the time it lacked
    cbe = {"buyer": "Commercial Bank of Ethiopia", "title": "Supply of scanner machines (bid no. 103/2026/27)",
           "closes": "2026-12-03", "time": "", "category": "it", "type": "company"}
    assert c.post("/internal/submit", json={"source": "CBE", "url": "https://cbe/list", "entries": [cbe]}, headers=h).json() == {"created": 1}
    pdf = dict(cbe, title="Purchase of 202 scanner machines, bid 103/2026/27", time="10:00")
    assert c.post("/internal/submit", json={"source": "CBE", "url": "https://cbe/a.pdf", "entries": [pdf]}, headers=h).json() == {"created": 0}
    with app.state.Session() as db:
        assert db.query(Tender).filter(Tender.buyer == "Commercial Bank of Ethiopia").one().time == "10:00"
    assert "ground power" not in c.get("/").text  # not public before review
    a = client(app, "0911000001")
    rev = a.get("/review").text
    assert "Supply of ground power units" in rev and "Website watcher: Ethiopian Airlines" in rev
    tid = re.search(r'href="/entry/(web-[0-9a-f]+)"', rev).group(1)
    page = a.get(f"/entry/{tid}").text
    assert "Open corporate.ethiopianairlines.com" in page and "Bid security not stated" in page
    # approve needs a real title and a date (or an explicit "no date")
    vague = dict(entry, title="Subject not stated", closes="")
    c.post("/internal/submit", json={"source": "AACRA", "url": "https://aacra.gov.et/tender", "entries": [vague]}, headers=h)
    vid = re.findall(r'href="/entry/(web-[0-9a-f]+)"', a.get("/review").text)[-1]
    tok = csrf(a, "/collect")
    r = a.post(f"/entry/{vid}", data={"csrf": tok, "action": "approve", "buyer": "AACRA", "title": "Subject not stated"})
    assert r.status_code == 400 and "real title" in r.text and "closing date" in r.text
    r = a.post(f"/entry/{vid}", data={"csrf": tok, "action": "approve", "buyer": "AACRA", "title": "Asphalt overlay, Bole road",
                                      "no_date": "1"}, follow_redirects=False)
    assert r.status_code == 303


def test_open_tenders_are_paged(env):
    app, _, board = env
    board.write_text(json.dumps({"tenders": [{"id": f"wb-{i}", "buyer": "B", "title": f"Item {i:02d}",
                                              "closes": f"2026-12-{i:02d}", "category": "it"} for i in range(1, 26)]}))
    c = TestClient(app)
    one = c.get("/").text
    assert "Item 01" in one and "Item 20" in one and "Item 21" not in one and "1 to 20 of 25" in one
    two = c.get("/?p=2").text
    assert "Item 21" in two and "Item 01" not in two and "21 to 25 of 25" in two and 'rel="prev"' in two
    f = c.get("/?category=it&p=2").text
    assert "Item 25" in f and "category=it&amp;p=1" in f  # filters kept across pages
    assert "Item 21" in c.get("/?p=99").text  # past the end shows the last page


def test_tenders_sit_in_several_categories(env):
    app, _, board = env
    board.write_text(json.dumps({"tenders": [
        {"id": "wb-OP00473353", "buyer": "MoA", "category": "goods", "closes": "2026-11-30",
         "title": "Procurement of Woreda information centre ICT equipment, tablets and laptops for NPCU, RPCU and woreda"},
        {"id": "wb-2", "buyer": "X", "category": "goods", "closes": "2026-11-30", "title": "Supply of office chairs"}]}))
    c = TestClient(app)
    it = c.get("/?category=it").text
    assert "Woreda information centre" in it and "office chairs" not in it
    goods = c.get("/?category=goods").text
    assert "Woreda information centre" in goods and "office chairs" in goods
    assert "IT and telecom" in c.get("/t/wb-OP00473353").text
    # an IT company's matches include it
    c.post("/signup", data={"company": "Sonador", "name": "R", "phone": "0955000009", "password": "longenough1"})
    c.post("/account/interests", data={"csrf": csrf(c, "/account"), "categories": ["it"]})
    assert "Woreda information centre" in c.get("/my").text
    # a reviewer can untick a category the words added
    from tw.notices import tag
    from tw.db import Tender as T
    with app.state.Session() as db:
        assert db.get(T, "wb-OP00473353").categories == ",goods,it,"


def test_admin_sets_prices_and_trial(env):
    app, _, _ = env
    app.state.chapa = FakeChapa()
    m = TestClient(app)
    assert m.get("/admin/prices", follow_redirects=False).status_code in (303, 401, 403)
    a = client(app, "0911000001")
    page_ = a.get("/admin/prices").text
    assert 'value="299"' in page_ and 'value="99"' in page_ and 'value="14"' in page_
    tok = csrf(a, "/admin/prices")
    form = {"csrf": tok, "ETB_month": "349", "ETB_year": "2,900", "USD_month": "12", "USD_year": "65", "trial_days": "7",
            "ETB_win_month": "990", "ETB_win_year": "7,900", "USD_win_month": "39", "USD_win_year": "319",
            "ETB_m3": "799", "ETB_m6": "1399", "USD_m3": "32", "USD_m6": "55",
            "ETB_win_m3": "2690", "ETB_win_m6": "4790", "USD_win_m3": "105", "USD_win_m6": "189"}
    bad = a.post("/admin/prices", data={**form, "USD_year": "free"})
    assert bad.status_code == 400 and "whole number" in bad.text
    assert a.post("/admin/prices", data=form, follow_redirects=False).headers["location"] == "/admin/prices?saved=4"
    pub = m.get("/pricing").text
    assert "ETB 349" in pub and "ETB 2,900" in pub and "7 days" in pub and "ETB 299" not in pub
    assert "USD 65" in m.get("/pricing?cur=USD").text
    # new companies get the new trial; checkout charges the new price
    m.post("/signup", data={"company": "Jobale", "name": "J", "phone": "0955000009", "password": "longenough1"})
    with app.state.Session() as db:
        from tw.db import Company
        assert db.query(Company).filter_by(name="Jobale").one().trial_until == "2026-11-14"
    mt = csrf(m, "/account")
    m.post("/account/pay", data={"csrf": mt, "plan": "year", "currency": "USD"})
    assert app.state.chapa.made[-1][1] == 65
    m.post("/account/pay", data={"csrf": mt, "plan": "month"})
    assert app.state.chapa.made[-1][1] == 349
    assert "Nothing changed" in a.get(a.post("/admin/prices", data=form, follow_redirects=False).headers["location"]).text


def test_two_step_sign_in(env, monkeypatch):
    app, _, _ = env
    from tw import auth
    with app.state.Session() as db:
        db.add(User(name="Rita", phone="0911000007", role="reviewer", pw_hash=hash_password("password1")))
        db.commit()
    c = TestClient(app)
    c.post("/login", data={"phone": "0911000007", "password": "password1", "next": "/"})
    # a reviewer without two-step is sent to set it up before any staff page opens
    r = c.get("/admin", follow_redirects=False)
    assert r.headers["location"] == "/security?required=1"
    page_ = c.get("/security?required=1").text
    assert "<svg" in page_ and "authenticator" in page_
    tok = re.search(r'name="token" value="([^"]+)"', page_).group(1)
    key = re.search(r"this key: <code>([A-Z2-7 ]+)</code>", page_).group(1).replace(" ", "")
    cs = csrf(c, "/security")
    bad = c.post("/security/two-step/on", data={"csrf": cs, "token": tok, "code": "000000"})
    assert bad.status_code == 400
    ok = c.post("/security/two-step/on", data={"csrf": cs, "token": tok, "code": auth.totp_now(key)})
    codes = re.findall(r"<code>([0-9a-f-]{14})</code>", ok.text)
    assert ok.status_code == 200 and len(codes) == 8
    assert c.get("/admin").status_code == 200  # this session carries on

    # next sign-in: password, then the code; a wrong code is refused and counted
    d = TestClient(app)
    r = d.post("/login", data={"phone": "0911000007", "password": "password1", "next": "/"}, follow_redirects=False)
    assert r.headers["location"].startswith("/login/code") and "tw" not in d.cookies
    assert d.get("/admin", follow_redirects=False).headers["location"].startswith("/login")
    assert d.post("/login/code", data={"code": "123456"}).status_code == 400
    r = d.post("/login/code", data={"code": codes[0].upper()}, follow_redirects=False)  # a backup code works once
    assert r.headers["location"] == "/admin"
    e = TestClient(app)
    e.post("/login", data={"phone": "0911000007", "password": "password1"})
    assert e.post("/login/code", data={"code": codes[0]}).status_code == 400
    # reviewers can't turn it off; an admin can reset it
    assert c.post("/security/two-step/off", data={"csrf": cs, "code": auth.totp_now(key)}).status_code == 403
    a = client(app, "0911000001")
    with app.state.Session() as db:
        rid = db.query(User).filter_by(phone="0911000007").one().id
    a.post(f"/admin/team/{rid}/reset-two-step", data={"csrf": csrf(a, "/admin/team")})
    with app.state.Session() as db:
        assert db.query(User).filter_by(phone="0911000007").one().totp_secret == ""
    log = a.get("/admin/activity").text
    assert "Turned on two-step" in log and "Used a backup code" in log and "Wrong code" in log and "Reset two-step" in log


def test_admin_console_is_separate_and_logged(env):
    app, _, board = env
    with app.state.Session() as db:
        db.add(Tender(id="wb-9", status="published", buyer="Ethio Telecom", title="Fibre cable supply",
                      closes="2026-12-01", category="goods"))
        db.commit()
    a = client(app, "0911000001")
    home = a.get("/").text
    assert 'href="/admin' not in home and 'href="/admin/payments"' not in home  # the customer header stays clean
    for path in ("/admin", "/admin/tenders", "/admin/review", "/admin/companies", "/admin/payments", "/admin/prices",
                 "/admin/directory", "/admin/team", "/admin/activity", "/admin/collectors"):
        r = a.get(path)
        assert r.status_code == 200 and 'class="console"' in r.text, path
    assert "Fibre cable supply" in a.get("/admin/tenders").text
    member, collector = client(app, "0911000004"), client(app, "0911000002")
    assert member.get("/admin").status_code == 403 and collector.get("/admin/tenders").status_code == 403
    tok = csrf(a, "/admin/tenders")
    a.post("/admin/tenders/wb-9/listing", data={"csrf": tok, "to": "unlisted"})
    assert TestClient(app).get("/t/wb-9").status_code == 404
    assert "wb-9" not in board.read_text()
    assert "Fibre cable supply" in a.get("/admin/tenders?status=unlisted").text
    a.post("/admin/tenders/wb-9/listing", data={"csrf": tok, "to": "published"})
    assert TestClient(app).get("/t/wb-9").status_code == 200 and "wb-9" in board.read_text()
    # extend a company's access
    m = TestClient(app)
    m.post("/signup", data={"company": "ABC Studio", "name": "A", "phone": "0955000011", "password": "longenough1"})
    with app.state.Session() as db:
        from tw.db import Company
        cid = db.query(Company).filter_by(name="ABC Studio").one().id
    assert "ABC Studio" in a.get("/admin/companies?state=trial").text
    assert a.post(f"/admin/companies/{cid}/extend", data={"csrf": tok, "days": 30, "reason": ""}).status_code == 400
    a.post(f"/admin/companies/{cid}/extend", data={"csrf": tok, "days": 30, "reason": "Pilot firm"})
    with app.state.Session() as db:
        assert db.query(Company).get(cid).paid_until == "2026-12-21"
    log = a.get("/admin/activity").text
    assert "Took down a tender" in log and "Put a tender back" in log and "Pilot firm" in log and "Signed in" in log


def test_security_headers_links_and_uploads(env):
    app, claude, _ = env
    c = TestClient(app)
    r = c.get("/")
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "script-src 'self';" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    assert "<script>" not in r.text  # no inline scripts
    col = client(app, "0911000002")
    tok = csrf(col, "/collect")
    # a file that only claims to be a JPEG is refused
    bad = col.post("/collect", data={"csrf": tok, "source": "x"}, files={"photo": ("a.jpg", b"<html>hi</html>", "image/jpeg")})
    assert bad.status_code == 400
    col.post("/collect", data={"csrf": tok, "source": "Addis Zemen"}, files={"photo": ("n.jpg", JPEG, "image/jpeg")})
    with app.state.Session() as db:
        t = db.query(Tender).filter(Tender.collector_id.isnot(None)).first()
        tid = t.id
    col.post(f"/entry/{tid}", data={"csrf": tok, "url": "javascript:alert(document.cookie)", "action": "save"})
    with app.state.Session() as db:
        assert db.get(Tender, tid).url == ""
    assert "javascript:" not in client(app, "0911000001").get(f"/entry/{tid}").text


DRAFT = {"requirements": [
    {"requirement": "Valid trade licence", "category": "licence", "notice_says": "renewed trade licence",
     "status": "met", "action": "Attach the licence."},
    {"requirement": "Bid security of ETB 50,000", "category": "bid_security", "notice_says": "CPO ETB 50,000",
     "status": "check", "action": "Get a CPO from your bank."}],
    "relevant_project_ids": ["p1", "p9"],
    "cover_letter": ["We submit our bid for the supply of servers.", "Our bid is valid for [TO CONFIRM: validity period]."],
    "profile_section": ["Sonador Systems is a licensed IT firm."],
    "technical_offer": ["We will deliver and install the servers within 30 days."],
    "open_questions": ["Which lot to bid for?"]}


def test_bid_room(env):
    app, claude, board = env
    board.write_text(json.dumps({"tenders": [
        {"id": "wb-1", "buyer": "ESS", "title": "Supply of servers", "closes": "2026-11-20", "category": "it",
         "region": "Addis Ababa", "notice": "Full text here", "bid_security": "ETB 50,000"}]}))
    calls = []
    claude.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: calls.append(kw) or SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(DRAFT))])))
    app.state.bid_room.sync_drafts = True
    c = TestClient(app)
    c.post("/signup", data={"company": "Sonador Systems", "name": "Robel D", "phone": "0933444555",
                            "password": "longenough1", "tin": "0088565182"})
    page = c.get("/t/wb-1").text
    assert "Start this bid" in page and "Coming soon" not in c.get("/pricing").text and "Soon:" not in c.get("/").text
    assert 'href="/bids"' in page  # in the member nav

    # the profile, once
    tok = csrf(c, "/profile")
    r = c.post("/profile", data={"csrf": tok, "legal_name": "Sonador Systems PLC", "tin": "0088565182", "address": "Bole, Woreda 03",
                                 "signatory_name": "Robel Desta", "signatory_title": "General Manager", "work_types": "IT, data centre",
                                 "licences.name": ["Software licence", ""], "licences.number": ["39141", ""],
                                 "licences.issuer": ["MInT", ""], "licences.expires": ["2027-07-07", ""],
                                 "projects.name": ["Bank data centre"], "projects.client": ["CBE"], "projects.value_etb": ["4,500,000"],
                                 "projects.year_completed": ["2025"], "projects.region": [""], "projects.role": ["Main contractor"],
                                 "projects.scope": ["Racks and cooling"]}, follow_redirects=False)
    assert r.headers["location"] == "/profile?saved=1"
    prof = c.get("/profile").text
    assert "Sonador Systems PLC" in prof and "39141" in prof and "Key staff" in prof

    # start the bid (twice opens the same one)
    r = c.post("/t/wb-1/bid", data={"csrf": tok}, follow_redirects=False)
    url = r.headers["location"]
    assert c.post("/t/wb-1/bid", data={"csrf": tok}, follow_redirects=False).headers["location"] == url
    assert c.post("/t/wb-1/bid", data={"csrf": "bad"}).status_code == 400
    bid_id = url.rsplit("/", 1)[1]
    room = c.get(url).text
    assert "Draft this bid" in room and "Supply of servers" in room

    # draft with Claude: checklist, letter, technical offer; facts it lacks are listed to confirm
    r = c.post(f"/bids/{bid_id}/draft", data={"csrf": tok, "doc_text": "ITB 5: bidders submit a CPO."}, follow_redirects=False)
    assert r.status_code == 303 and len(calls) == 1
    sent = calls[0]["messages"][0]["content"]
    assert "ITB 5: bidders submit a CPO." in sent and "Sonador Systems PLC" in sent and "39141" in sent
    room = c.get(url).text
    assert "Valid trade licence" in room and "Get a CPO" in room and "within 30 days" in room
    assert "validity period" in room and "Which lot" in room and "Bank data centre" in room
    assert c.get(f"/bids/{bid_id}/state").json() == {"state": "open", "error": ""}

    # autosave: edits, ticks, prices, submitted
    h = {"x-csrf": tok}
    j = c.post(f"/bids/{bid_id}/save", headers=h, json={"cover_letter": "Our own letter.", "done": [1],
               "financial": [{"item": "Server", "unit": "pcs", "qty": "2", "price": "1,000,000"}, {"item": ""}],
               "state": "submitted", "evil": "x"}).json()
    assert j["ok"] and j["total"] == 2000000 and j["progress"] == {"done": 1, "total": 2, "gaps": 0} and j["confirm"] == []
    assert c.post(f"/bids/{bid_id}/save", headers={"x-csrf": "bad"}, json={}).status_code == 400
    assert c.post(f"/bids/{bid_id}/save", headers=h, content=b"[1]").status_code == 400
    lst = c.get("/bids").text
    assert "Supply of servers" in lst and "Submitted" in lst and "1 of 2 done" in lst

    # drafting again keeps the firm's own letter and ticks
    c.post(f"/bids/{bid_id}/draft", data={"csrf": tok}, follow_redirects=False)
    room = c.get(url).text
    assert "Our own letter." in room and "We submit our bid" not in room and 'data-done="1" checked' in room

    # print: letterhead, letter, tables, prices
    pr = c.get(f"/bids/{bid_id}/print").text
    assert "Sonador Systems PLC" in pr and "TIN 0088565182" in pr and "Our own letter." in pr and "ESS" in pr
    assert "2,000,000.00" in pr and "Bank data centre" in pr and "Software licence" in pr and "Robel Desta, General Manager" in pr

    # other firms and non-members can't reach it
    other = TestClient(app)
    other.post("/signup", data={"company": "Other PLC", "name": "Other", "phone": "0933444666", "password": "longenough1"})
    for path in (url, f"/bids/{bid_id}/print", f"/bids/{bid_id}/state"):
        assert other.get(path).status_code == 404
    assert other.post(f"/bids/{bid_id}/save", headers={"x-csrf": csrf(other, "/profile")}, json={}).status_code == 404
    assert client(app, "0911000002").get("/bids").status_code == 403
    assert TestClient(app).get("/bids", follow_redirects=False).status_code in (303, 307)


def test_win_plan_opens_bid_room(env, monkeypatch):
    app, _, board = env
    board.write_text(json.dumps({"tenders": [{"id": "wb-1", "buyer": "ESS", "title": "Supply of servers",
                                              "closes": "2026-12-20", "category": "it"}]}))
    app.state.chapa = chapa = FakeChapa()
    c = TestClient(app)
    c.post("/signup", data={"company": "Delta", "name": "D", "phone": "0955000002", "password": "longenough1"})
    tok = csrf(c, "/account")
    assert "Start this bid" in c.get("/t/wb-1").text  # the trial has Bid Room
    bid = c.post("/t/wb-1/bid", data={"csrf": tok}, follow_redirects=False).headers["location"]
    # after the trial, a Watch payment opens tenders but not Bid Room
    monkeypatch.setattr("tw.main.today", lambda: "2026-11-25")
    c.post("/account/pay", data={"csrf": tok, "plan": "month"})
    chapa.paid_refs.add(chapa.made[-1][0])
    c.get("/account/paid?ref=" + chapa.made[-1][0])
    page = c.get("/t/wb-1").text
    assert "Choose Win to open your bid" in page and "Start this bid" not in page
    r = c.get(bid)
    assert r.status_code == 402 and "Win plan" in r.text and "kept" in r.text
    assert c.get("/account").text.count("Watch until") == 1
    # Win: Bid Room again, and tenders at least as long
    c.post("/account/pay", data={"csrf": tok, "plan": "win_month"})
    assert chapa.made[-1][1] == 990
    chapa.paid_refs.add(chapa.made[-1][0])
    c.get("/account/paid?ref=" + chapa.made[-1][0])
    assert c.get(bid).status_code == 200 and "Open your bid" in c.get("/t/wb-1").text
    with app.state.Session() as db:
        from tw.db import Company
        co = db.query(Company).filter_by(name="Delta").one()
        assert co.win_until == "2026-12-25" and co.paid_until == "2026-12-25"
        cid = co.id
    # admins can give Bid Room days too
    a = client(app, "0911000001")
    at = csrf(a, f"/admin/companies/{cid}")
    a.post(f"/admin/companies/{cid}/extend", data={"csrf": at, "days": "10", "reason": "pilot", "win": "1"})
    with app.state.Session() as db:
        co = db.get(Company, cid)
        assert co.win_until == "2027-01-04" and co.paid_until == "2027-01-04"


def test_admin_console_on_its_own_host(tmp_path, monkeypatch):
    monkeypatch.setattr("tw.main.today", lambda: "2026-11-07")
    s = Settings({"DATABASE_URL": f"sqlite:///{tmp_path}/t.db", "SECRET_KEY": "test", "DATA_DIR": str(tmp_path / "d"),
                  "SECURE_COOKIES": "0", "SITE_URL": "http://site.test", "ADMIN_URL": "http://admin.site.test"})
    app = create_app(s, FakeClaude())
    with app.state.Session() as db:
        db.add(User(name="Robel", phone="0911000001", role="admin", pw_hash=hash_password("password1"), totp_secret=TOTP))
        db.commit()
    site, adm = TestClient(app, base_url="http://site.test"), TestClient(app, base_url="http://admin.site.test")
    # the customer site doesn't serve the console, and admins can't sign in there
    assert site.get("/admin/prices", follow_redirects=False).headers["location"] == "http://admin.site.test/admin/prices"
    r = site.post("/login", data={"phone": "0911000001", "password": "password1"})
    assert r.status_code == 400 and "admin.site.test" in r.text and "tw" not in site.cookies
    # the console's host has only the console
    assert adm.get("/", follow_redirects=False).headers["location"] == "/admin"
    assert adm.get("/pricing", follow_redirects=False).headers["location"] == "http://site.test/pricing"
    r = adm.post("/login", data={"phone": "0911000001", "password": "password1", "next": "/"}, follow_redirects=False)
    from tw.auth import totp_now
    r = adm.post("/login/code", data={"code": totp_now(TOTP), "next": "/"}, follow_redirects=False)
    assert r.headers["location"] == "/admin"
    assert 'class="console"' in adm.get("/admin").text and 'href="http://site.test/"' in adm.get("/admin").text
    # members sign in on the site only
    site.post("/signup", data={"company": "X PLC", "name": "X", "phone": "0955000003", "password": "longenough1"})
    assert "tw" in site.cookies
    r = TestClient(app, base_url="http://admin.site.test").post("/login", data={"phone": "0955000003", "password": "longenough1"})
    assert r.status_code == 400 and "site.test" in r.text


def test_three_and_six_month_plans(env):
    app, _, _ = env
    app.state.chapa = chapa = FakeChapa()
    c = TestClient(app)
    c.post("/signup", data={"company": "Jobale", "name": "J", "phone": "0955000010", "password": "longenough1"})
    tok = csrf(c, "/account")
    c.post("/account/pay", data={"csrf": tok, "plan": "m3"})
    assert chapa.made[-1][1] == 799
    chapa.paid_refs.add(chapa.made[-1][0])
    c.get("/account/paid?ref=" + chapa.made[-1][0])
    c.post("/account/pay", data={"csrf": tok, "plan": "win_m6", "currency": "USD"})
    assert chapa.made[-1][1] == 189
    chapa.paid_refs.add(chapa.made[-1][0])
    c.get("/account/paid?ref=" + chapa.made[-1][0])
    with app.state.Session() as db:
        from tw.db import Company
        co = db.query(Company).filter_by(name="Jobale").one()
        # trial to 21 Nov, then 91 days of Watch; Win runs 182 days from the trial's end
        assert co.paid_until == "2027-05-22" and co.win_until == "2027-05-22"


def test_bid_desk_company_file_and_envelope(env, tmp_path):
    from tw.main import long_date
    app, claude, board = env
    board.write_text(json.dumps({"tenders": [
        {"id": "wb-1", "buyer": "Awash Bank", "title": "Monitoring platform", "closes": "2026-11-10", "category": "it",
         "notice": "Bidders must present VAT registration and a renewed trade licence.", "bid_security": "ETB 50,000 CPO"},
        {"id": "wb-2", "buyer": "ERA", "title": "Road maintenance", "closes": "2026-12-01", "category": "construction"}]}))
    c = TestClient(app)
    c.post("/signup", data={"company": "Sonador Systems", "name": "Robel D", "phone": "0933444555",
                            "password": "longenough1", "tin": "0088565182"})
    tok = csrf(c, "/profile")

    # empty file: the three papers every bid needs are missing, and the tender page says so
    page = c.get("/file").text
    assert "Three papers would get any bid of yours thrown out today" in page
    t = c.get("/t/wb-1").text
    assert "Missing from your company file" in t and "Bid security" in t and "VAT registration certificate" in t
    assert "Competence" not in t and "Competence or professional licence" in c.get("/t/wb-2").text

    # add papers: a bad file type is refused, a PDF is kept and only its owner can open it
    r = c.post("/file", data={"csrf": tok, "kind": "tin", "number": "0088565182"},
               files={"scan": ("x.exe", b"MZ\x90\x00", "application/pdf")}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert c.post("/file", data={"kind": "tin"}, follow_redirects=False).status_code == 400  # no csrf
    for kind, exp in (("tin", ""), ("trade_licence", "2027-01-09"), ("vat", ""), ("tax_clearance", "2026-11-01")):
        r = c.post("/file", data={"csrf": tok, "kind": kind, "number": "N-1", "expires": exp},
                   files={"scan": ("scan.pdf", b"%PDF-1.4 test", "application/pdf")}, follow_redirects=False)
        assert r.headers["location"] == "/file?saved=1"
    page = c.get("/file").text
    assert "One paper would get any bid" in page and "Expired on" in page and ("Valid until " + long_date("2027-01-09")) in page
    with app.state.Session() as s:
        from tw.db import Paper
        pid = s.query(Paper).filter_by(kind="tin").one().id
    scan = c.get(f"/file/{pid}/scan")
    assert scan.status_code == 200 and scan.headers["content-type"] == "application/pdf" and scan.content.startswith(b"%PDF")
    other = TestClient(app)
    other.post("/signup", data={"company": "Other PLC", "name": "Someone", "phone": "0911000111", "password": "longenough1"})
    assert other.get(f"/file/{pid}/scan").status_code == 404
    assert other.post(f"/file/{pid}/delete", data={"csrf": csrf(other, "/profile")}, follow_redirects=False).status_code == 404
    assert TestClient(app).get("/file", follow_redirects=False).status_code in (302, 303, 307)

    # a valid tax clearance clears the blocker; the bid desk shows the envelope with what is still short
    c.post("/file", data={"csrf": tok, "kind": "tax_clearance", "expires": "2026-12-31"}, follow_redirects=False)
    c.post("/t/wb-1/bid", data={"csrf": tok}, follow_redirects=False)
    desk = c.get("/my").text
    assert "Awash Bank closes in 3 days. Your envelope is three papers short." in desk  # technical, price, security
    assert "Write in Bid Room" in desk and "I have the bid security" in desk and "ጥቅምት 28, 2019" in desk
    with app.state.Session() as s:
        from tw.db import Bid
        bid_id = s.query(Bid).one().id
    c.post(f"/bids/{bid_id}/have", data={"csrf": tok, "key": "security", "on": "1"}, follow_redirects=False)
    assert "two papers short" in c.get("/my").text
    assert c.post(f"/bids/{bid_id}/have", data={"csrf": tok, "key": "technical", "on": "1"}).status_code == 400
    assert other.post(f"/bids/{bid_id}/have", data={"csrf": csrf(other, "/profile"), "key": "security", "on": "1"}).status_code == 404


def test_explorer_filters_saved_and_reports(env, monkeypatch):
    app, _, board = env
    board.write_text(json.dumps({"tenders": [
        {"id": "wb-1", "buyer": "Awash Bank", "title": "Monitoring platform", "closes": "2026-11-09", "category": "it",
         "type": "company", "region": "Addis Ababa"},
        {"id": "wb-2", "buyer": "ERA", "title": "Road maintenance", "closes": "2026-12-20", "category": "construction",
         "type": "gov", "region": "Amhara"},
        {"id": "wb-3", "buyer": "Awash Bank", "title": "Branch furniture", "closes": "2026-11-30", "category": "goods",
         "type": "company", "region": "Oromia"}]}))
    c = TestClient(app)
    # filters: closing window, buyer, buyer type, sort
    assert "Road maintenance" not in c.get("/?within=7").text and "Monitoring platform" in c.get("/?within=7").text
    page = c.get("/?buyer=awash").text
    assert "Branch furniture" in page and "Road maintenance" not in page
    assert "Road maintenance" in c.get("/?type=gov").text and "Branch furniture" not in c.get("/?type=gov").text
    by_buyer = c.get("/?sort=buyer").text
    assert by_buyer.index("Branch furniture") < by_buyer.index("Road maintenance")
    assert "Branch furniture" in c.get("/?within=99&sort=nonsense").text  # bad values are ignored
    # visitors can't save, see recommendations or reports
    assert c.post("/t/wb-1/save", data={"csrf": "x"}, follow_redirects=False).status_code in (303, 307)
    assert c.get("/reports", follow_redirects=False).status_code in (303, 307)
    c.post("/signup", data={"company": "Sonador", "name": "R", "phone": "0955000020", "password": "longenough1"})
    tok = csrf(c, "/account")
    # recommended needs interests; then shows only matches with a fit
    assert "Set your interests to see recommendations" in c.get("/?match=1").text
    c.post("/account/interests", data={"csrf": tok, "categories": ["it"]})
    rec = c.get("/?match=1").text
    assert "Monitoring platform" in rec and "Road maintenance" not in rec and "Good" in rec
    # save and unsave; the back link stays on the site
    assert c.post("/t/wb-2/save", data={"csrf": "bad"}).status_code == 400
    r = c.post("/t/wb-2/save", data={"csrf": tok, "back": "https://evil.example/"}, follow_redirects=False)
    assert r.headers["location"] == "/t/wb-2"
    assert "Road maintenance" in c.get("/saved").text and 'aria-pressed="true"' in c.get("/t/wb-2").text
    other = TestClient(app)
    other.post("/signup", data={"company": "Other", "name": "O", "phone": "0955000021", "password": "longenough1"})
    assert "Road maintenance" not in other.get("/saved").text  # each company has its own list
    c.post("/t/wb-2/save", data={"csrf": tok, "back": "/saved"})
    assert "Nothing saved yet" in c.get("/saved").text
    assert c.post("/t/nope/save", data={"csrf": tok}).status_code == 404
    # reports count open tenders only
    rep = c.get("/reports").text
    assert "Awash Bank" in rep and "Your bid record" in rep and ">2<" in rep
    assert client(app, "0911000002").get("/reports").status_code == 403  # collectors
    # the bell: a paper expiring soon and a bid closing in 3 days
    c.post("/file", data={"csrf": tok, "kind": "tax_clearance", "expires": "2026-11-20"})
    c.post("/t/wb-1/bid", data={"csrf": tok})
    bell = c.get("/my").text
    assert "Tax clearance certificate expires 20 Nov" in bell and "Monitoring platform closes in 2 days" in bell


PAST = {"about": {"title": "Supply of servers to Awash Bank", "buyer": "Awash Bank", "reference": "AB/IT/07/2025", "year": "2025"},
        "company": {"legal_name": "Sonador Systems PLC", "grade": "", "address": "Bole, Woreda 03", "phone": "0911223344",
                    "email": "", "tin": "0088565182", "vat_number": "", "signatory_name": "Robel Desta",
                    "signatory_title": "General Manager", "year_established": "2015", "staff_count": "", "regions": "",
                    "work_types": "", "equipment": "", "notes": "Dell authorised partner http://evil.example"},
        "licences": [{"name": "Data centre licence", "number": "85124", "issuer": "MInT", "expires": "2027-07-07"}],
        "projects": [{"name": "Bank data centre", "client": "CBE", "value_etb": "4500000", "year_completed": "2025",
                      "region": "", "role": "", "scope": ""},
                     {"name": "Branch network refresh", "client": "Awash Bank", "value_etb": "2100000",
                      "year_completed": "2024", "region": "Addis Ababa", "role": "Main contractor", "scope": "120 switches"}],
        "key_personnel": [{"role": "Project manager", "name": "Hana T", "qualification": "MSc CS", "years_experience": "9"}],
        "turnover": [{"year": "2024", "amount_etb": "12500000"}, {"year": "n/a", "amount_etb": "1"}],
        "methodology": [{"heading": "Delivery and installation", "paragraphs": ["We stage every server in our Bole lab before delivery."]},
                        {"heading": "Warranty", "paragraphs": ["Three years on site, next business day."]}],
        "cover_letter": ["We are pleased to submit our bid."], "company_section": ["Sonador builds data centres."],
        "price_lines": [{"item": "Rack server", "unit": "pcs", "qty": "4", "price": "850000"}],
        "price_notes": "ETB, VAT included, valid 90 days"}


def test_past_bids_import_profile_and_reuse(env, tmp_path):
    app, claude, board = env
    board.write_text(json.dumps({"tenders": [
        {"id": "wb-1", "buyer": "ESS", "title": "Supply of rack servers", "closes": "2026-11-20", "category": "it",
         "notice": "Full text here"}]}))
    calls = []
    state = {"out": PAST}

    def create(**kw):
        calls.append(kw)
        out = DRAFT if "own_bids" in kw["system"] else state["out"]
        if out is None:
            raise RuntimeError("API down")
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(out))])
    claude.beta = SimpleNamespace(messages=SimpleNamespace(create=create))
    app.state.library.sync = True
    app.state.bid_room.sync_drafts = True
    c = TestClient(app)
    c.post("/signup", data={"company": "Sonador Systems", "name": "Robel D", "phone": "0933444555",
                            "password": "longenough1", "tin": "0088565182"})
    tok = csrf(c, "/profile")
    assert 'href="/past-bids"' in c.get("/bids").text and "No past bids yet" in c.get("/past-bids").text

    # only PDFs, csrf checked
    r = c.post("/past-bids", data={"csrf": tok}, files={"pdf": ("bid.docx", b"PK\x03\x04", "application/pdf")}, follow_redirects=False)
    assert "error=" in r.headers["location"] and not calls
    assert c.post("/past-bids", data={"csrf": "bad"}, files={"pdf": ("b.pdf", b"%PDF-1.4", "application/pdf")}).status_code == 400

    # a failed read can be retried
    state["out"] = None
    r = c.post("/past-bids", data={"csrf": tok}, files={"pdf": ("awash-bid.pdf", b"%PDF-1.4 the bid", "application/pdf")},
               follow_redirects=False)
    url = r.headers["location"]
    pid = int(url.rsplit("/", 1)[1])
    assert "Read it again" in c.get(url).text and c.get(url + "/state").json()["state"] == "failed"
    state["out"] = PAST
    c.post(url + "/retry", data={"csrf": tok})
    assert c.get(url + "/state").json() == {"state": "ready", "error": ""}
    sent = calls[-1]
    doc = sent["messages"][0]["content"][0]
    assert doc["type"] == "document" and doc["source"]["media_type"] == "application/pdf"
    assert sent["output_config"]["format"]["type"] == "json_schema"

    page = c.get(url).text
    assert "Supply of servers to Awash Bank" in page and "We stage every server in our Bole lab" in page
    assert "Hana T" in page and "Rack server" in page and "evil.example" not in page
    assert "4 methods" not in page and "Only your company" in page
    lst = c.get("/past-bids").text
    assert "2 method sections" in lst and "2 projects" in lst and "new for your profile" in lst

    # add facts to the profile: ticked ones only, rows not doubled
    r = c.post(url + "/profile", data={"csrf": tok, "f": ["legal_name", "signatory_name"],
                                       "r": ["projects:0", "projects:1", "key_personnel:0", "turnover:0", "turnover:1"]},
               follow_redirects=False)
    assert r.headers["location"] == f"/past-bids/{pid}?merged=6"
    prof = c.get("/profile").text
    assert "Sonador Systems PLC" in prof and "Robel Desta" in prof and "Branch network refresh" in prof and "Hana T" in prof
    assert "2024: 12500000" in prof and 'value="85124"' not in prof and 'value="General Manager"' not in prof
    c.post(url + "/profile", data={"csrf": tok, "r": ["projects:0"]})
    with app.state.Session() as s:
        from tw.db import Company
        p = json.loads(s.query(Company).filter_by(name="Sonador Systems").one().profile)
        assert len(p["projects"]) == 2

    # Bid Room: drafting reuses the firm's own wording; parts can be copied in
    assert "Start this bid" in c.get("/t/wb-1").text
    c.post("/t/wb-1/bid", data={"csrf": tok}, follow_redirects=False)
    with app.state.Session() as s:
        from tw.db import Bid
        bid_id = s.query(Bid).one().id
    room = c.get(f"/bids/{bid_id}").text
    assert "Reuse wording from my 1 past bid" in room and "Copy from past bid" in room
    c.post(f"/bids/{bid_id}/draft", data={"csrf": tok, "use_past": "1"})
    content = calls[-1]["messages"][0]["content"]
    assert "<own_bids>" in content and "We stage every server in our Bole lab" in content.split("<tender>")[0]
    c.post(f"/bids/{bid_id}/draft", data={"csrf": tok})
    assert "<own_bids>" not in calls[-1]["messages"][0]["content"]
    r = c.post(f"/bids/{bid_id}/reuse", data={"csrf": tok, "past": pid, "part": "technical"}, follow_redirects=False)
    assert r.headers["location"].endswith("#technical")
    c.post(f"/past-bids/{pid}/use", data={"csrf": tok, "bid": bid_id, "part": "financial"})
    room = c.get(f"/bids/{bid_id}").text
    assert "Delivery and installation" in room and "We will deliver and install the servers" in room
    assert 'value="Rack server"' in room and 'value="850000"' not in room

    # strict isolation: another company sees nothing and can't use it
    other = TestClient(app)
    other.post("/signup", data={"company": "Other PLC", "name": "Other", "phone": "0933444666", "password": "longenough1"})
    otok = csrf(other, "/profile")
    for path in (url, url + "/state", url + "/file"):
        assert other.get(path).status_code == 404
    assert "Supply of servers to Awash Bank" not in other.get("/past-bids").text
    for path, data in ((url + "/profile", {"csrf": otok, "r": ["projects:0"]}), (url + "/retry", {"csrf": otok}),
                       (url + "/delete", {"csrf": otok})):
        assert other.post(path, data=data).status_code == 404
    r = other.post("/t/wb-1/bid", data={"csrf": otok}, follow_redirects=False)
    assert r.status_code == 303, r.text
    with app.state.Session() as s:
        obid = s.query(Bid).filter(Bid.id != bid_id).one().id
    assert other.post(f"/bids/{obid}/reuse", data={"csrf": otok, "past": pid, "part": "technical"}).status_code == 404
    assert other.post(f"/past-bids/{pid}/use", data={"csrf": otok, "bid": obid, "part": "technical"}).status_code == 404
    assert c.post(f"/bids/{obid}/reuse", data={"csrf": tok, "past": pid, "part": "technical"}).status_code == 404
    assert client(app, "0911000002").get("/past-bids").status_code == 403
    assert TestClient(app).get("/past-bids", follow_redirects=False).status_code in (302, 303, 307)

    # the owner can open the PDF and remove it; the file goes too
    f = c.get(url + "/file")
    assert f.status_code == 200 and f.content.startswith(b"%PDF") and f.headers["cache-control"] == "private, no-store"
    stored = list((tmp_path / "appdata" / "past_bids").rglob("*.pdf"))
    assert len(stored) == 1  # the .docx was refused; a retried read keeps its one file
    c.post(url + "/delete", data={"csrf": tok})
    assert c.get(url).status_code == 404 and not list((tmp_path / "appdata" / "past_bids").rglob("*.pdf"))

def test_username_member_from_manage(env, tmp_path, monkeypatch):
    from tw import manage
    from tw.auth import norm_login
    app = env[0]
    assert norm_login(" Jobale ") == "jobale" and norm_login("0911 234 567") == "0911234567"
    assert norm_login("ab") == norm_login("x y") == norm_login("1abc") == ""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("MEMBER_PASSWORD", "password1")
    monkeypatch.setenv("SECRET_KEY", "test")
    manage.main(["create-member", "--login", "Jobale", "--company", "Jobale Engineering", "--name", "Jobale",
                 "--categories", "construction"])
    with pytest.raises(SystemExit):
        manage.main(["create-member", "--login", "jobale", "--company", "Again", "--name", "X"])
    bad = client(app).post("/login", data={"phone": "jobale", "password": "nope"})
    assert bad.status_code == 400 and "or username" in bad.text
    c = client(app, "JOBALE")
    home = c.get("/my")
    assert home.status_code == 200 and "Jobale Engineering" in home.text
    acct = c.get("/account").text
    assert 'value="construction" checked' in acct or "construction" in acct
    assert c.get("/bids").status_code == 200  # the trial includes Bid Room
