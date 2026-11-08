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
            db.add(User(name=name, phone=phone, role=role, pw_hash=hash_password("password1")))
        db.commit()
    return app, claude, board


def client(app, phone=None):
    c = TestClient(app)
    if phone:
        r = c.post("/login", data={"phone": phone, "password": "password1", "next": "/"}, follow_redirects=False)
        assert r.status_code == 303, r.text
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

    def checkout(self, tx_ref, amount, name, return_url):
        self.made.append((tx_ref, amount, name, return_url))
        return "https://checkout.chapa.co/checkout/payment/" + tx_ref

    def paid(self, tx_ref, amount):
        self.asked.append(tx_ref)
        return tx_ref in self.paid_refs


def test_chapa_checkout_and_manual_payments(env, monkeypatch):
    app, _, _ = env
    c = TestClient(app)
    c.post("/signup", data={"company": "Myagta", "name": "M", "phone": "0955000001", "password": "longenough1"})
    tok = csrf(c, "/account")
    assert "Pay ETB" not in c.get("/account").text  # no key: no online button
    assert c.post("/account/pay", data={"csrf": tok, "plan": "year"}).status_code == 503

    chapa = app.state.chapa = FakeChapa()
    assert "Pay ETB 2,400 for a year" in c.get("/account").text
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
    assert "Subscribed" in acc and "2027-11-21" not in acc and "21 Nov 2027" in acc and "payment went through" in acc
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
