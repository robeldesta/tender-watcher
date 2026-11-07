"""Admin commands.

    python -m tw.manage create-admin --name "Robel" --phone 0911...   (asks for the password)
    python -m tw.manage import-board /opt/tenders/site/tenders.json   (our own tenders from the static board)
"""
import argparse
import datetime as dt
import getpass
import json
import os
import sys

from sqlalchemy import select

from .auth import hash_password, norm_phone
from .config import Settings
from .db import Tender, User, make_session

OWN = ("wb-", "web-", "fc-", "tw-")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tw.manage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--name", required=True)
    a.add_argument("--phone", required=True)
    b = sub.add_parser("import-board")
    b.add_argument("path")
    args = ap.parse_args(argv)
    Session = make_session(Settings().database_url)
    with Session() as s:
        if args.cmd == "create-admin":
            phone = norm_phone(args.phone)
            if not phone:
                sys.exit("Not an Ethiopian mobile number.")
            pw = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Password (8+ characters): ")
            if len(pw) < 8:
                sys.exit("Password too short.")
            u = s.scalars(select(User).where(User.phone == phone)).first()
            if u:
                u.role, u.pw_hash, u.active, u.name = "admin", hash_password(pw), True, args.name
            else:
                s.add(User(name=args.name, phone=phone, role="admin", pw_hash=hash_password(pw)))
            s.commit()
            print(f"Admin {args.name} ({phone}) ready.")
        elif args.cmd == "import-board":
            with open(args.path, encoding="utf-8") as f:
                rows = json.load(f).get("tenders", [])
            added = 0
            for r in rows:
                if not str(r.get("id", "")).startswith(OWN) or s.get(Tender, r["id"]):
                    continue  # our own sources only
                first = r.get("firstSeen") or dt.date.today().isoformat()
                url = r.get("url", "")
                s.add(Tender(
                    id=r["id"], status="published", buyer=r.get("buyer", ""), title=r.get("title", ""),
                    category=r.get("category", "other"), region=r.get("region", ""), place=r.get("place", ""),
                    grade=r.get("grade", ""), closes=r.get("closes", ""), time=r.get("time", ""),
                    type=r.get("type", "gov"), bid_security=r.get("bid_security", ""),
                    document_fee=r.get("document_fee", ""), source=r.get("source", ""),
                    reference=r.get("reference", ""), notice=r.get("notice", ""),
                    photo=url.rsplit("/", 1)[-1] if url.startswith("/notices/") else "",
                    url="" if url.startswith("/notices/") else url,
                    published_at=dt.datetime.fromisoformat(first + "T06:00:00+03:00")))
                added += 1
            s.commit()
            print(f"Imported {added} tenders.")


if __name__ == "__main__":
    main()
