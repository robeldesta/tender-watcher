import json
import sqlite3
from dataclasses import asdict
from datetime import date

from .models import Tender


class Store:
    def __init__(self, path: str = "data/tenders.db"):
        self.db = sqlite3.connect(path)
        self.db.execute("create table if not exists tenders (key text primary key, body text)")

    def add(self, t: Tender) -> bool:
        """Insert if new. Returns True when the tender was not seen before."""
        body = json.dumps(asdict(t), default=lambda o: o.isoformat() if isinstance(o, date) else str(o))
        cur = self.db.execute("insert or ignore into tenders values (?, ?)", (t.key, body))
        self.db.commit()
        return cur.rowcount == 1

    def all(self) -> list[dict]:
        return [json.loads(b) for (b,) in self.db.execute("select body from tenders")]
