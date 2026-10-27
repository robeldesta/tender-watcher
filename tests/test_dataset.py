import json
from datetime import date

import pytest

from tenderwatch.dataset import Labels, export, parse_value, review, split_of, stats
from tenderwatch.models import Tender
from tenderwatch.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    for i in range(30):
        s.add(Tender("demo", f"https://example.com/{i}", f"Tender {i}", f"Notice text {i}\nline two",
                     buyer="Sample Bureau", deadline=date(2026, 11, 18)))
    s.add(Tender("demo", "https://example.com/empty", "", ""))
    return s


def test_parse_value():
    assert parse_value("deadline", "2026-11-18") == "2026-11-18"
    assert parse_value("budget_etb", "1,500,000") == 1500000.0
    assert parse_value("buyer", "none") is None
    for field, raw in [("deadline", "18/11/2026"), ("buyer", ""), ("colour", "red")]:
        with pytest.raises(ValueError):
            parse_value(field, raw)


def test_labels_need_a_known_tender(store):
    labels = Labels(store)
    labels.set("demo:https://example.com/1", "deadline", "2026-11-20", who="robel")
    assert labels.for_key("demo:https://example.com/1") == {"deadline": "2026-11-20"}
    with pytest.raises(ValueError):
        labels.set("demo:https://nowhere", "deadline", "2026-11-20")


def test_review_accepts_corrects_skips_and_quits(store):
    answers = iter(["", "2026-11-25", "none", "s", "construction", "q"])
    saved = review(store, ask=lambda _: next(answers), show=lambda _: None)
    assert saved == 4
    got = Labels(store).for_key("demo:https://example.com/0")
    # Enter kept the shown buyer, a typed date replaced the extracted one, "none" is stored as null.
    assert got == {"buyer": "Sample Bureau", "deadline": "2026-11-25", "budget_etb": None,
                   "sector": "construction"}
    assert stats(store)["checked_fields"]["region"] == 0


def test_export_only_human_labels_and_holds_out_eval(store, tmp_path):
    labels = Labels(store)
    keys = [f"demo:https://example.com/{i}" for i in range(30)]
    for k in keys:
        labels.set(k, "deadline", "2026-11-18")
    counts = export(store, str(tmp_path / "out"))

    notices = [json.loads(x) for x in (tmp_path / "out/notices.jsonl").read_text().splitlines()]
    labeled = [json.loads(x) for x in (tmp_path / "out/labeled.jsonl").read_text().splitlines()]
    n_eval = sum(split_of(k) == "eval" for k in keys)
    assert counts["labeled"] == 30 and counts["labeled_eval"] == n_eval
    assert len(notices) == 30 - n_eval  # eval notices and the empty one are left out
    assert "line two" in notices[0]["text"]
    # Only the checked field is exported, never the extracted buyer.
    assert all(x["fields"] == {"deadline": "2026-11-18"} for x in labeled)
    assert {x["split"] for x in labeled} <= {"train", "eval"}
