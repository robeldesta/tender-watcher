import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "entry_bot", Path(__file__).resolve().parent.parent / "deploy" / "entry_bot.py")
eb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eb)

REVIEWER, COLLECTOR, STRANGER = 100, 200, 300

NOTICE = {
    "newspaper": "Addis Zemen, 5 Nov 2026",
    "problem": "",
    "tenders": [{
        "buyer": "Bahir Dar City Roads Authority",
        "title": "Construction of cobblestone road (Ref BDR/12/2019)",
        "region": "Amhara", "place": "Bahir Dar", "grade": "gc 5",
        "closes": {"raw": "ኅዳር 11 ቀን 2019 ዓ.ም", "calendar": "ethiopian", "year": 2019, "month": 3, "day": 11},
        "time": "10:00", "type": "gov", "bid_security": "ETB 50,000", "document_fee": "ETB 300",
        "category": "construction", "doubts": "",
    }],
}


class FakeClaude:
    def __init__(self, out=NOTICE, stop="end_turn"):
        self.out, self.stop, self.calls = out, stop, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop,
                               content=[SimpleNamespace(type="text", text=json.dumps(self.out))])


class FakeTG:
    def __init__(self):
        self.sent, self.photos, self.n = [], [], 0

    def call(self, method, **p):
        self.sent.append((method, p))
        self.n += 1
        return {"message_id": self.n}

    def download(self, file_id):
        return b"\xff\xd8fakejpeg"

    def send_photo(self, chat_id, img, caption, markup=None):
        self.n += 1
        self.photos.append((chat_id, caption, markup, self.n))
        return {"message_id": self.n}

    def texts(self, chat):
        return [p["text"] for m, p in self.sent if m == "sendMessage" and p["chat_id"] == chat]


@pytest.fixture
def setup(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    board = site / "tenders.json"
    board.write_text(json.dumps({"updated": "2026-11-07", "tenders": [
        {"id": "mt-1", "buyer": "X", "title": "Old", "closes": "2026-11-30"}]}))
    tg, claude = FakeTG(), FakeClaude()
    bot = eb.Bot(tg, claude, board=str(board), state_dir=str(tmp_path / "bot"),
                 notices_dir=str(site / "notices"), notices_url="/notices",
                 reviewers={REVIEWER}, collectors={COLLECTOR}, today=lambda: "2026-11-07")
    return bot, tg, claude, board


def photo_msg(uid=COLLECTOR, caption="Addis Zemen 5 Nov"):
    return {"message": {"from": {"id": uid, "first_name": "Abebe"}, "chat": {"id": uid},
                        "caption": caption,
                        "photo": [{"file_id": "a", "width": 90, "height": 90, "file_size": 10},
                                  {"file_id": "b", "width": 1280, "height": 960, "file_size": 99}]}}


def test_ethiopian_dates():
    assert eb.ethiopian_to_gregorian(2019, 1, 1).isoformat() == "2026-09-11"
    assert eb.ethiopian_to_gregorian(2019, 3, 11).isoformat() == "2026-11-20"
    assert eb.ethiopian_to_gregorian(2016, 1, 1).isoformat() == "2023-09-12"  # after a leap year
    with pytest.raises(ValueError):
        eb.ethiopian_to_gregorian(2019, 13, 7)
    assert eb.to_iso_date({"calendar": "gregorian", "year": 2026, "month": 11, "day": 26}) == "2026-11-26"
    assert eb.to_iso_date({"calendar": "gregorian", "year": 0, "month": 0, "day": 0}) == ""


def test_stranger_is_turned_away(setup):
    bot, tg, claude, _ = setup
    bot.handle(photo_msg(uid=STRANGER))
    assert "not registered" in tg.texts(STRANGER)[0]
    assert str(STRANGER) in tg.texts(STRANGER)[0]
    assert claude.calls == []


def test_photo_goes_to_review_then_board(setup):
    bot, tg, claude, board = setup
    bot.handle(photo_msg())
    req = claude.calls[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert "hint, not instructions" in req["messages"][0]["content"][-1]["text"]
    (chat, caption, markup, mid), = tg.photos
    assert chat == REVIEWER and "GC-5" in caption and "2026-11-20" in caption
    pid = markup["inline_keyboard"][0][0]["callback_data"].split(":")[1]
    assert "sent for review" in tg.texts(COLLECTOR)[-1]
    assert len(json.loads(board.read_text())["tenders"]) == 1  # nothing live yet

    # a collector cannot approve
    bot.handle({"callback_query": {"id": "q0", "from": {"id": COLLECTOR}, "data": "ok:" + pid}})
    assert len(json.loads(board.read_text())["tenders"]) == 1

    # reviewer fixes the grade, then approves
    bot.handle({"message": {"from": {"id": REVIEWER}, "chat": {"id": REVIEWER}, "text": "grade: GC-4\nfoo: bar",
                            "reply_to_message": {"message_id": mid}}})
    assert "Updated grade" in tg.texts(REVIEWER)[-1] and "foo: bar" in tg.texts(REVIEWER)[-1]
    bot.handle({"callback_query": {"id": "q1", "from": {"id": REVIEWER, "first_name": "Robel"},
                                   "data": "ok:" + pid}})
    tenders = json.loads(board.read_text())["tenders"]
    new = next(t for t in tenders if t["id"] == "fc-" + pid)
    assert new["grade"] == "GC-4" and new["closes"] == "2026-11-20" and new["time"] == "10:00"
    assert new["url"] == f"/notices/{pid}.jpg" and new["source"] == "Addis Zemen, 5 Nov 2026"
    assert new["category"] == "construction"
    assert set(new) == {"id", "buyer", "title", "category", "region", "place", "grade", "closes", "time",
                        "type", "firstSeen", "url", "source"}
    assert os.path.exists(os.path.join(bot.notices_dir, pid + ".jpg"))
    assert "Live on the board" in tg.texts(COLLECTOR)[-1]

    # second press does nothing
    bot.handle({"callback_query": {"id": "q2", "from": {"id": REVIEWER}, "data": "ok:" + pid}})
    assert len(json.loads(board.read_text())["tenders"]) == 2
    assert "Abebe: 1" in bot.stats()


def test_reject_and_bad_ids(setup):
    bot, tg, claude, board = setup
    bot.handle(photo_msg())
    pid = tg.photos[0][2]["inline_keyboard"][0][1]["callback_data"].split(":")[1]
    bot.handle({"callback_query": {"id": "q", "from": {"id": REVIEWER}, "data": "no:" + pid}})
    assert len(json.loads(board.read_text())["tenders"]) == 1
    assert "Not used" in tg.texts(COLLECTOR)[-1]
    # path traversal in callback data is ignored
    bot.handle({"callback_query": {"id": "q", "from": {"id": REVIEWER}, "data": "ok:../../x"}})
    assert ("answerCallbackQuery", {"callback_query_id": "q", "text": "Already handled."}) in tg.sent


def test_refusal_and_empty(setup):
    bot, tg, claude, _ = setup
    bot.claude = FakeClaude(stop="refusal")
    bot.handle(photo_msg())
    assert "declined" in tg.texts(COLLECTOR)[-1]
    bot.claude = FakeClaude(out={"newspaper": "", "tenders": [], "problem": "It is a menu."})
    bot.handle(photo_msg())
    assert "It is a menu." in tg.texts(COLLECTOR)[-1]
    assert tg.photos == []


def test_duplicate_warning(setup):
    bot, tg, claude, board = setup
    data = json.loads(board.read_text())
    data["tenders"].append({"id": "mt-9", "buyer": "Bahir Dar City Roads Authority",
                            "title": "Construction of cobblestone road (Ref BDR/12/2019)",
                            "closes": "2026-11-20", "source": "Modern Tenders"})
    board.write_text(json.dumps(data))
    bot.handle(photo_msg())
    assert "Already on the board (Modern Tenders)" in tg.photos[0][1]


def test_non_construction_and_category_fix(setup):
    bot, tg, claude, board = setup
    out = json.loads(json.dumps(NOTICE))
    out["tenders"][0].update(category="goods", grade="", title="Supply of laptops")
    bot.claude = FakeClaude(out=out)
    bot.handle(photo_msg())
    (chat, caption, markup, mid), = tg.photos
    assert "Category: Goods and supplies" in caption and "Not construction" not in caption
    bot.handle({"message": {"from": {"id": REVIEWER}, "chat": {"id": REVIEWER}, "text": "category: it",
                            "reply_to_message": {"message_id": mid}}})
    bot.handle({"message": {"from": {"id": REVIEWER}, "chat": {"id": REVIEWER}, "text": "category: toys",
                            "reply_to_message": {"message_id": mid}}})
    assert "Could not use" in tg.texts(REVIEWER)[-1]
    pid = markup["inline_keyboard"][0][0]["callback_data"].split(":")[1]
    bot.handle({"callback_query": {"id": "q", "from": {"id": REVIEWER}, "data": "ok:" + pid}})
    new = next(t for t in json.loads(board.read_text())["tenders"] if t["id"] == "fc-" + pid)
    assert new["category"] == "it"


LISTING = """<html><body>
<a href="/news/1">Annual report</a>
<a href="/tenders/road-2026">Invitation to bid: road maintenance</a>
<a href="docs/rfq-laptops.pdf">RFQ laptops</a>
<a href="/vacancy/accountant">Vacancy: accountant tender unit</a>
<a href="/tenders/award-2025">Tender award result</a>
<a href="https://other.example/tender/x">Partner tender</a>
<a href="https://cdn.example/files/bid-doc.pdf">Bid document</a>
<a href="/tenders/road-2026#apply">Invitation to bid: road maintenance</a>
<a href="/ጨረታ/2">ጨረታ ማስታወቂያ</a>
</body></html>"""


def test_tender_links():
    links = eb.tender_links(LISTING, "https://buyer.gov.et/")
    assert links == ["https://buyer.gov.et/tenders/road-2026", "https://buyer.gov.et/docs/rfq-laptops.pdf",
                     "https://cdn.example/files/bid-doc.pdf", "https://buyer.gov.et/ጨረታ/2"]


class FakeWeb:
    def __init__(self, pages):
        self.pages, self.got = pages, []

    def get(self, url):
        self.got.append(url)
        if url not in self.pages:
            raise OSError("404")
        return self.pages[url]


SRC = {"id": "aaroads", "name": "Addis Ababa City Roads Authority", "type": "gov",
       "url": "https://buyer.gov.et/"}


def test_watch_sources_review_and_approve(setup):
    bot, tg, claude, board = setup
    out = json.loads(json.dumps(NOTICE))
    out["tenders"][0]["buyer"] = ""
    bot.claude = FakeClaude(out=out)
    web = FakeWeb({"https://buyer.gov.et/": (LISTING.encode(), "text/html"),
                   "https://buyer.gov.et/tenders/road-2026": (b"<p>Invitation to bid</p>", "text/html")})
    n = eb.watch_sources(bot, [SRC, dict(SRC, id="off", enabled=False)], web=web, log=lambda m: None)
    assert n == 1  # only the road page loaded; the others 404 and are logged
    assert "https://buyer.gov.et/" in web.got and all("off" not in u for u in web.got)
    req = bot.claude.calls[0]["messages"][0]["content"]
    assert req[0]["type"] == "text" and "<page>" in req[0]["text"]
    assert "not a photo" in req[-1]["text"]
    assert tg.photos == []
    (method, msg), = [(m, p) for m, p in tg.sent if m == "sendMessage" and p["chat_id"] == REVIEWER]
    assert "Source: https://buyer.gov.et/tenders/road-2026" in msg["text"]
    pid = msg["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[1]

    # a second run sends nothing new
    assert eb.watch_sources(bot, [SRC], web=web, log=lambda m: None) == 0

    bot.handle({"callback_query": {"id": "q", "from": {"id": REVIEWER}, "data": "ok:" + pid}})
    new = next(t for t in json.loads(board.read_text())["tenders"] if t["id"] == "web-" + pid)
    assert new["url"] == "https://buyer.gov.et/tenders/road-2026"
    assert new["buyer"] == "Addis Ababa City Roads Authority" and new["type"] == "gov"
    assert ("editMessageText" in [m for m, p in tg.sent])


def test_watch_skips_closed_and_pdf(setup):
    bot, tg, claude, board = setup
    out = json.loads(json.dumps(NOTICE))
    out["tenders"][0]["closes"] = {"raw": "1 Nov 2026", "calendar": "gregorian", "year": 2026, "month": 11, "day": 1}
    bot.claude = FakeClaude(out=out)
    web = FakeWeb({"https://buyer.gov.et/": (LISTING.encode(), "text/html"),
                   "https://buyer.gov.et/docs/rfq-laptops.pdf": (b"%PDF-1.4 x", "application/pdf")})
    assert eb.watch_sources(bot, [SRC], web=web, log=lambda m: None) == 0
    block = bot.claude.calls[0]["messages"][0]["content"][0]
    assert block["type"] == "document" and block["source"]["media_type"] == "application/pdf"


def test_sources_file_is_valid():
    path = Path(__file__).resolve().parent.parent / "deploy" / "sources.json"
    sources = eb.load_sources(path)
    ids_ = [s["id"] for s in sources]
    assert len(ids_) == len(set(ids_))
    for s in sources:
        assert s["url"].startswith("https://") and s["name"] and s["type"] in eb.TYPES


def test_keyword_hit():
    kws = eb.DEFAULT_KEYWORDS["vehicles"]
    assert eb.keyword_hit(kws, "procurement of 3 buses and 2 trucks")
    assert eb.keyword_hit(kws, "supply of spare parts for heavy duty vehicles")
    assert eb.keyword_hit(kws, "የተሽከርካሪ ጎማ ግዥ")
    assert eb.keyword_hit(kws, "ተሽከርካሪዎች ግዥ")
    assert not eb.keyword_hit(kws, "business development consultancy")
    assert eb.keyword_hit(["consultan*"], "consultancy service")


def test_subscriber_trial_alerts_and_expiry(setup):
    bot, tg, claude, board = setup
    day = {"d": "2026-11-07"}
    bot.today = lambda: day["d"]
    data = json.loads(board.read_text())
    data["tenders"] += [
        {"id": "mt-2", "buyer": "Ethiopian Roads", "title": "Procurement of 4 pickup trucks", "category": "goods",
         "closes": "2026-11-25", "url": "https://moderntenders.com/tenders/x"},
        {"id": "mt-3", "buyer": "AAU", "title": "Supply of laptops", "category": "it", "closes": "2026-11-25"},
        {"id": "mt-4", "buyer": "City Bus", "title": "Bus tyres", "category": "vehicles", "closes": "2026-11-01"},
        {"id": "fc-5", "buyer": "EEU", "title": "Supply of utility vehicles", "category": "vehicles",
         "closes": "2026-11-30", "url": "/notices/abc.jpg"},
        {"id": "mt-7", "buyer": "INSA", "title": "Bus purchase", "category": "vehicles", "closes": "2028-03-03"}]
    board.write_text(json.dumps(data))
    SUB = 555

    # only reviewers can subscribe someone
    bot.handle({"message": {"from": {"id": COLLECTOR}, "chat": {"id": COLLECTOR},
                            "text": f"/subscribe {SUB} vehicles name=Myagta"}})
    assert bot.load_subs() == {}

    bot.handle({"message": {"from": {"id": REVIEWER}, "chat": {"id": REVIEWER},
                            "text": f"/subscribe {SUB} vehicles name=Myagta_Trading"}})
    assert "trial until 2026-11-21" in tg.texts(REVIEWER)[-1] and "Sent 2" in tg.texts(REVIEWER)[-1]
    first = tg.texts(SUB)
    assert "Welcome" in first[0] and "Free trial until 2026-11-21" in first[0]
    assert "pickup trucks" in first[1] and "https://tenders.sonador.et/#t/fc-5" in first[1]
    assert "moderntenders" not in first[1]
    assert "laptops" not in first[1] and "tyres" not in first[1]  # other sector; already closed

    # the subscriber's own message gets their status, not "not registered"
    bot.handle({"message": {"from": {"id": SUB}, "chat": {"id": SUB}, "text": "/start"}})
    assert "Myagta Trading" in tg.texts(SUB)[-1]

    # nothing new: no message; a new matching tender: one message
    n = len(tg.texts(SUB))
    assert bot.send_alerts() == 0 and len(tg.texts(SUB)) == n
    data["tenders"].append({"id": "web-6", "buyer": "UNICEF", "title": "Ambulance supply",
                            "category": "medical", "closes": "2026-12-05", "url": "https://unicef.org/t"})
    board.write_text(json.dumps(data))
    assert bot.send_alerts() == 1 and "1 new tender for you today" in tg.texts(SUB)[-1]

    # trial ends: one notice with the price, then silence
    day["d"] = "2026-11-22"
    bot.send_alerts()
    assert "ETB 299" in tg.texts(SUB)[-1]
    n = len(tg.texts(SUB))
    bot.send_alerts()
    assert len(tg.texts(SUB)) == n

    # paid: alerts resume
    bot.handle({"message": {"from": {"id": REVIEWER}, "chat": {"id": REVIEWER}, "text": f"/paid {SUB} 2027-11-22"}})
    assert "paid until 2027-11-22" in tg.texts(SUB)[-1]
    assert bot.active(bot.load_subs()[str(SUB)])
    bot.handle({"message": {"from": {"id": REVIEWER}, "chat": {"id": REVIEWER}, "text": "/subs"}})
    assert "Myagta Trading: vehicles" in tg.texts(REVIEWER)[-1]
    bot.handle({"message": {"from": {"id": REVIEWER}, "chat": {"id": REVIEWER}, "text": "/subscribe x toys"}})
    assert "Usage" in tg.texts(REVIEWER)[-1]


def test_alert_chunks_split():
    ts = [{"id": str(i), "title": "T" * 190, "buyer": "B", "closes": "2026-12-01"} for i in range(60)]
    chunks = eb.alert_chunks("head", ts, "https://x")
    assert len(chunks) > 1 and all(len(c) < 4096 for c in chunks)
    assert sum(c.count("• ") for c in chunks) == 60


class FakeChapa:
    def __init__(self):
        self.made, self.paid_refs = [], set()

    def checkout(self, tx_ref, amount, name, return_url):
        self.made.append((tx_ref, amount))
        return "https://checkout.chapa.co/x/" + tx_ref

    def paid(self, tx_ref, amount):
        return tx_ref in self.paid_refs


def test_pay_with_chapa_extends_subscription(setup):
    bot, tg, claude, board = setup
    day = {"d": "2026-11-07"}
    bot.today = lambda: day["d"]
    SUB = 777
    bot.handle({"message": {"from": {"id": REVIEWER}, "chat": {"id": REVIEWER},
                            "text": f"/subscribe {SUB} vehicles name=Myagta"}})

    # no Chapa configured: manual instructions
    bot.handle({"message": {"from": {"id": SUB}, "chat": {"id": SUB}, "text": "/pay"}})
    assert "not set up" in tg.texts(SUB)[-1]

    bot.chapa = FakeChapa()
    bot.handle({"message": {"from": {"id": SUB}, "chat": {"id": SUB}, "text": "/pay"}})
    kb = [p for m, p in tg.sent if m == "sendMessage" and p["chat_id"] == SUB][-1]["reply_markup"]["inline_keyboard"]
    assert [r[0]["callback_data"] for r in kb] == ["pay:month", "pay:year"]

    # a stranger cannot start a payment
    bot.handle({"callback_query": {"id": "s", "from": {"id": STRANGER}, "data": "pay:year"}})
    assert bot.chapa.made == []

    bot.handle({"callback_query": {"id": "p", "from": {"id": SUB}, "data": "pay:year"}})
    (tx_ref, amount), = bot.chapa.made
    assert amount == 2400 and tx_ref.startswith(f"TW-{SUB}-year-")
    link = [p for m, p in tg.sent if m == "sendMessage" and p["chat_id"] == SUB][-1]["reply_markup"]
    assert link["inline_keyboard"][0][0]["url"].endswith(tx_ref)

    assert bot.check_payments() == 0  # not paid yet
    bot.chapa.paid_refs.add(tx_ref)
    assert bot.check_payments() == 0  # checked less than a minute ago: wait
    bot._checked.clear()
    assert bot.check_payments() == 1
    sub = bot.load_subs()[str(SUB)]
    assert sub["paid_until"] == "2027-11-21"  # a year counted from the end of the trial
    assert "runs until 2027-11-21" in tg.texts(SUB)[-1]
    assert "Myagta paid ETB 2400" in tg.texts(REVIEWER)[-1]
    bot._checked.clear()
    assert bot.check_payments() == 0  # counted once
    with open(os.path.join(bot.state, "payments.csv")) as f:
        assert tx_ref in f.read()


def test_warning_before_trial_ends(setup):
    bot, tg, claude, board = setup
    day = {"d": "2026-11-07"}
    bot.today = lambda: day["d"]
    bot.handle({"message": {"from": {"id": REVIEWER}, "chat": {"id": REVIEWER}, "text": "/subscribe 888 it name=S"}})
    day["d"] = "2026-11-18"
    bot.send_alerts()
    assert "trial ends on 2026-11-21" in tg.texts(888)[-1] and "/pay" in tg.texts(888)[-1]
    n = len(tg.texts(888))
    bot.send_alerts()
    assert len(tg.texts(888)) == n  # warned once


def test_chapa_verify_rules(monkeypatch):
    c = eb.Chapa("CHASECK_TEST-x")
    reply = {"status": "success", "data": {"status": "success", "amount": "2400.00", "currency": "ETB", "tx_ref": "TW-1"}}
    monkeypatch.setattr(c, "_req", lambda *a, **k: reply)
    assert c.paid("TW-1", 2400)
    assert not c.paid("TW-1", 299)  # wrong amount
    assert not c.paid("TW-2", 2400)  # someone else's reference
    reply["data"]["status"] = "failed/pending"
    assert not c.paid("TW-1", 2400)


def test_payment_check_does_not_lose_a_new_link(setup):
    """A link created while Chapa is being asked must survive the check's write."""
    bot, tg, claude, board = setup
    bot.handle({"message": {"from": {"id": REVIEWER}, "chat": {"id": REVIEWER}, "text": "/subscribe 999 it name=R"}})
    bot.chapa = FakeChapa()
    bot.handle({"callback_query": {"id": "a", "from": {"id": 999}, "data": "pay:month"}})
    first = bot.chapa.made[0][0]
    real_paid = bot.chapa.paid

    def paid_while_user_taps(tx_ref, amount):
        bot.handle({"callback_query": {"id": "b", "from": {"id": 999}, "data": "pay:year"}})
        return real_paid(tx_ref, amount)
    bot.chapa.paid_refs.add(first)
    bot.chapa.paid = paid_while_user_taps
    assert bot.check_payments() == 1
    pays = bot.load_payments()
    assert pays[first]["status"] == "paid"
    second = bot.chapa.made[1][0]
    assert pays[second]["status"] == "pending"


def test_polling_slows_after_an_hour(setup):
    bot, *_ = setup
    pay = {"created": 0}
    assert bot.due_for_check("x", pay, 1000)
    bot._checked["x"] = 1000
    assert not bot.due_for_check("x", pay, 1030) and bot.due_for_check("x", {"created": 900}, 1060)
    bot._checked["x"] = 5000
    assert not bot.due_for_check("x", pay, 5300) and bot.due_for_check("x", pay, 5900)


def test_refresh_flags_odd_dates():
    rspec = importlib.util.spec_from_file_location(
        "refresh", Path(__file__).resolve().parent.parent / "deploy" / "refresh.py")
    rf = importlib.util.module_from_spec(rspec)
    rspec.loader.exec_module(rf)
    import datetime as dt
    today = dt.date(2026, 11, 7)
    ts = [{"closes": "2028-03-03", "time": "08:30"}, {"closes": "2026-11-18", "time": "04:00"},
          {"closes": "2026-11-25", "time": "10:00", "check": True, "check_why": "old"}, {"closes": "", "time": ""}]
    rf.flag_checks(ts, today)
    assert ts[0]["check"] and ts[0]["check_why"] == "closing date more than 6 months away"
    assert ts[1]["check"] and "Ethiopian clock" in ts[1]["check_why"]
    assert "check" not in ts[2] and "check" not in ts[3]
    chunk = eb.alert_chunks("h", [dict(ts[1], id="x", title="T", buyer="B")], "https://s")[0]
    assert "(to be confirmed)" in chunk


def _refresh():
    rspec = importlib.util.spec_from_file_location(
        "refresh", Path(__file__).resolve().parent.parent / "deploy" / "refresh.py")
    rf = importlib.util.module_from_spec(rspec)
    rspec.loader.exec_module(rf)
    return rf


def test_worldbank_entries_carry_full_notice():
    import datetime as dt
    rf = _refresh()
    payload = json.loads((Path(__file__).resolve().parent / "fixtures" / "worldbank" / "procnotices_ethiopia.json").read_text())
    out = rf.worldbank(payload, dt.date(2026, 11, 1), set())
    assert out and all(t["id"].startswith("wb-") and t["type"] == "donor" for t in out)
    t = next(t for t in out if t["id"] == "wb-OP00476746")
    assert t["closes"] == "2026-11-18" and t["time"] == "10:00" and t["category"] == "construction"
    assert t["reference"] == "ET-GAM-RPCU-570832-CW-RFB" and "veterinary" in t["notice"].lower()
    assert "<p>" not in t["notice"]
    assert rf.worldbank(payload, dt.date(2026, 11, 1), {"wb-OP00476746"}) == [x for x in out if x["id"] != "wb-OP00476746"]


def test_own_copy_replaces_modern_tenders_copy():
    rf = _refresh()
    ts = [{"id": "mt-a", "buyer": "Commercial Bank of Ethiopia", "title": "Purchase of UTP Cable Cat 6A", "closes": "2026-11-17"},
          {"id": "web-b", "buyer": "Commercial Bank of Ethiopia", "title": "Invitation to bid: purchase of UTP cable Cat 6A",
           "closes": "2026-11-17"},
          {"id": "mt-c", "buyer": "Commercial Bank of Ethiopia", "title": "Purchase of UTP Cable Cat 6A", "closes": "2026-11-30"}]
    assert [t["id"] for t in rf.drop_copies(ts)] == ["web-b", "mt-c"]
