"""Bring our own tenders from the static board file (World Bank feed, buyer-website watcher,
Telegram photo entries) into the app's database. Competitor-sourced rows are never imported."""
import datetime as dt
import json
import os
import re

from .db import Tender

OWN = ("wb-", "web-", "fc-", "tw-")


def import_rows(session, rows):
    added = 0
    for r in rows:
        tid = str(r.get("id", ""))
        if not tid.startswith(OWN):
            continue
        place = re.sub(r"\s*[\r\n]+\s*", ", ", r.get("place", "")).strip(", ")
        place = re.sub(r"(\d)([A-Z])", r"\1, \2", place)  # "P.O.Box 62347Addis Ababa" -> "62347, Addis Ababa"
        old = session.get(Tender, tid)
        if old:  # machine sources may correct a row later; keep reviewer-made tw- rows as they are
            if not tid.startswith("tw-") and old.status == "published":
                old.place, old.time, old.closes = place, r.get("time", ""), r.get("closes", "")
            continue
        first = r.get("firstSeen") or dt.date.today().isoformat()
        url = r.get("url", "")
        session.add(Tender(
            id=tid, status="published", buyer=r.get("buyer", ""), title=r.get("title", ""),
            category=r.get("category", "other"), region=r.get("region", ""), place=place,
            grade=r.get("grade", ""), closes=r.get("closes", ""), time=r.get("time", ""),
            type=r.get("type", "gov"), bid_security=r.get("bid_security", ""),
            document_fee=r.get("document_fee", ""), source=r.get("source", ""),
            reference=r.get("reference", ""), notice=r.get("notice", ""),
            photo=url.rsplit("/", 1)[-1] if url.startswith("/notices/") else "",
            url="" if url.startswith("/notices/") else url,
            published_at=dt.datetime.fromisoformat(first + "T06:00:00+03:00")))
        added += 1
    session.commit()
    return added


class BoardSync:
    """Re-reads the board file when it changes; cheap enough to call on page loads."""

    def __init__(self, path):
        self.path, self.mtime = path, None

    def __call__(self, session):
        if not self.path:
            return 0
        try:
            m = os.path.getmtime(self.path)
            if m == self.mtime:
                return 0
            with open(self.path, encoding="utf-8") as f:
                rows = json.load(f).get("tenders", [])
        except (OSError, ValueError):
            return 0
        try:
            n = import_rows(session, rows)
        except Exception:  # another worker imported the same row first
            session.rollback()
            return 0
        self.mtime = m
        return n
