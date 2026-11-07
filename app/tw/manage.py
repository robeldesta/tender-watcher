"""Admin commands.

    python -m tw.manage create-admin --name "Robel" --phone 0911...   (asks for the password)
    python -m tw.manage import-board /opt/tenders/site/tenders.json   (our own tenders from the static board)
"""
import argparse
import getpass
import json
import os
import sys

from sqlalchemy import select

from .auth import hash_password, norm_phone
from .config import Settings
from .db import User, make_session
from .sync import import_rows


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
            print(f"Imported {import_rows(s, rows)} tenders.")

if __name__ == "__main__":
    main()
