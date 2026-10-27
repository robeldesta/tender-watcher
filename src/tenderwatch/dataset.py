"""Human-checked tender fields, exported as a training set for Kyrie.

Kyrie is our own small model. The plan is for it to take over the narrow,
high-volume steps (pulling deadline, buyer, budget and so on out of notices,
including Amharic ones) so fewer calls go to Claude.

It learns from two things this module exports:
- notices.jsonl: the raw notice text, for pretraining on tender language.
- labeled.jsonl: notices with field values a person has checked.

Model output is never exported as a label. Anthropic's terms restrict using
Claude's output to train a competing model, and a guessed deadline is a bad
label anyway. A value only becomes a label when a person confirms or corrects
it against the notice, with `review` or `check`.

  PYTHONPATH=src python -m tenderwatch.dataset review                # go through unchecked tenders
  PYTHONPATH=src python -m tenderwatch.dataset check KEY deadline 2026-11-18
  PYTHONPATH=src python -m tenderwatch.dataset stats
  PYTHONPATH=src python -m tenderwatch.dataset export --out out/kyrie
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .store import Store

# Fields a person can check, with how to validate a typed value.
FIELDS = {
    "buyer": str,
    "deadline": lambda v: date.fromisoformat(v).isoformat(),
    "budget_etb": lambda v: float(v.replace(",", "")),
    "region": str,
    "sector": str,
}
NONE = "none"  # typed when the notice really doesn't state the field
EVAL_PERCENT = 10  # share of tenders held out for evaluation, chosen by key hash


def parse_value(field: str, raw: str):
    """A typed value made canonical, or None for 'the notice doesn't say'."""
    if field not in FIELDS:
        raise ValueError(f"unknown field {field!r}; one of {', '.join(FIELDS)}")
    raw = raw.strip()
    if raw.lower() == NONE:
        return None
    if not raw:
        raise ValueError("empty value; type 'none' if the notice doesn't state it")
    return FIELDS[field](raw)


class Labels:
    """Checked field values, kept in the same SQLite file as the tenders."""

    def __init__(self, store: Store):
        self.store = store
        self.db = store.db
        self.db.execute("""create table if not exists labels (
            key text, field text, value text, checked_by text, checked_at text,
            primary key (key, field))""")

    def set(self, key: str, field: str, raw: str, who: str = "") -> None:
        value = parse_value(field, raw)
        if not self.db.execute("select 1 from tenders where key = ?", (key,)).fetchone():
            raise ValueError(f"no tender with key {key!r}")
        self.db.execute("insert or replace into labels values (?, ?, ?, ?, ?)",
                        (key, field, json.dumps(value), who,
                         datetime.now(timezone.utc).isoformat(timespec="seconds")))
        self.db.commit()

    def for_key(self, key: str) -> dict:
        rows = self.db.execute("select field, value from labels where key = ?", (key,))
        return {f: json.loads(v) for f, v in rows}

    def all(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for key, f, v in self.db.execute("select key, field, value from labels"):
            out.setdefault(key, {})[f] = json.loads(v)
        return out


def key_of(d: dict) -> str:
    return f"{d['source']}:{d['url']}"


def split_of(key: str) -> str:
    """Stable train/eval split, so a tender never moves between them."""
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    return "eval" if h % 100 < EVAL_PERCENT else "train"


def notice_text(d: dict) -> str:
    return "\n".join(x for x in [d.get("title") or "", d.get("raw_text") or ""] if x.strip())


def export(store: Store, out_dir: str) -> dict:
    """Writes notices.jsonl and labeled.jsonl. Returns counts."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    labels = Labels(store).all()
    counts = {"notices": 0, "labeled": 0, "labeled_eval": 0, "fields": 0}
    seen_text = set()
    with open(out / "notices.jsonl", "w", encoding="utf-8") as fn, \
         open(out / "labeled.jsonl", "w", encoding="utf-8") as fl:
        for d in store.all():
            key, text = key_of(d), notice_text(d)
            if not text:
                continue
            split = split_of(key)
            # Eval notices stay out of pretraining text too, so the eval set is unseen.
            if split == "train" and text not in seen_text:
                seen_text.add(text)
                fn.write(json.dumps({"text": text, "source": d["source"], "url": d["url"]},
                                    ensure_ascii=False) + "\n")
                counts["notices"] += 1
            if key in labels:
                fl.write(json.dumps({"key": key, "split": split, "notice": text,
                                     "fields": labels[key]}, ensure_ascii=False) + "\n")
                counts["labeled"] += 1
                counts["labeled_eval"] += split == "eval"
                counts["fields"] += len(labels[key])
    return counts


def review(store: Store, who: str = "", ask=input, show=print, limit: int = 0) -> int:
    """Shows each tender with unchecked fields and asks for each one.

    Enter accepts the current value, typing replaces it, 'none' means the notice
    doesn't say, 's' skips the field, 'q' stops. Returns how many fields were saved.
    """
    labels, saved, done = Labels(store), 0, 0
    for d in store.all():
        key = key_of(d)
        have = labels.for_key(key)
        todo = [f for f in FIELDS if f not in have]
        if not todo:
            continue
        show(f"\n=== {d.get('title')}\n{d['url']}\n\n{(d.get('raw_text') or '')[:3000]}\n")
        for f in todo:
            current = d.get(f)
            while True:
                hint = f" [{current}]" if current not in (None, "") else " [none]"
                ans = ask(f"{f}{hint}: ").strip()
                if ans.lower() == "q":
                    return saved
                if ans.lower() == "s":
                    break
                try:
                    labels.set(key, f, ans or (str(current) if current not in (None, "") else NONE), who)
                    saved += 1
                    break
                except ValueError as e:
                    show(f"  {e}")
        done += 1
        if limit and done >= limit:
            break
    return saved


def stats(store: Store) -> dict:
    labels = Labels(store).all()
    per_field = {f: sum(f in v for v in labels.values()) for f in FIELDS}
    return {"tenders": len(store.all()), "checked_tenders": len(labels), "checked_fields": per_field}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="tenderwatch.dataset")
    ap.add_argument("--db", default="data/tenders.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review", help="check fields of tenders one by one")
    r.add_argument("--who", default="", help="your name, saved with each check")
    r.add_argument("--limit", type=int, default=0, help="stop after this many tenders")
    c = sub.add_parser("check", help="record one checked field")
    c.add_argument("key", help="source:url")
    c.add_argument("field", choices=list(FIELDS))
    c.add_argument("value", help=f"the value, or '{NONE}' if the notice doesn't state it")
    c.add_argument("--who", default="")
    sub.add_parser("stats", help="how many fields are checked")
    e = sub.add_parser("export", help="write notices.jsonl and labeled.jsonl for Kyrie")
    e.add_argument("--out", default="out/kyrie")
    a = ap.parse_args(argv)

    store = Store(a.db)
    if a.cmd == "review":
        print(f"Saved {review(store, a.who, limit=a.limit)} checked fields.")
    elif a.cmd == "check":
        try:
            Labels(store).set(a.key, a.field, a.value, a.who)
        except ValueError as err:
            sys.exit(str(err))
    elif a.cmd == "stats":
        print(json.dumps(stats(store), indent=2))
    else:
        counts = export(store, a.out)
        print(f"Wrote {counts['notices']} notices and {counts['labeled']} labeled tenders "
              f"({counts['labeled_eval']} held out for eval, {counts['fields']} fields) to {a.out}")


if __name__ == "__main__":
    main()
