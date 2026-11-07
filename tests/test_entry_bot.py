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
        "is_works": True, "doubts": "",
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
    assert set(new) == {"id", "buyer", "title", "region", "place", "grade", "closes", "time",
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
