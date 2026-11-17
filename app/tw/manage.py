"""Admin commands.

    python -m tw.manage create-admin --name "Robel" --phone 0911...   (asks for the password)
    python -m tw.manage import-board /opt/tenders/site/tenders.json   (our own tenders from the static board)
    python -m tw.manage create-member --login jobale --company "Jobale Engineering" --name "Jobale" --categories construction
        (a pilot firm's account on a normal free trial; the password comes from MEMBER_PASSWORD or is asked)
"""
import argparse
import getpass
import json
import os
import sys

from sqlalchemy import select

import datetime as dt

from .auth import hash_password, norm_login, norm_phone
from .config import Settings
from .db import Company, User, make_session
from .sync import import_rows


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tw.manage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--name", required=True)
    a.add_argument("--phone", required=True)
    m = sub.add_parser("create-member")
    m.add_argument("--login", required=True, help="mobile number or username")
    m.add_argument("--company", required=True)
    m.add_argument("--name", required=True)
    m.add_argument("--categories", default="", help="comma-separated interest categories")
    m.add_argument("--trial-days", type=int, default=14)
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
        elif args.cmd == "create-member":
            login = norm_login(args.login)
            if not login:
                sys.exit("Give a mobile number, or a username of 3 to 20 letters and digits.")
            if s.scalars(select(User).where(User.phone == login)).first():
                sys.exit(f"{login} already has an account.")
            pw = os.environ.get("MEMBER_PASSWORD") or getpass.getpass("Password (8+ characters): ")
            if len(pw) < 8:
                sys.exit("Password too short.")
            end = (dt.date.today() + dt.timedelta(days=args.trial_days)).isoformat()
            cats = [c.strip() for c in args.categories.split(",") if c.strip()]
            c = Company(name=args.company.strip()[:200], trial_until=end,
                        interests=json.dumps({"categories": cats, "regions": [], "keywords": []}))
            s.add_all([c, User(name=args.name.strip()[:120], phone=login, role="member", pw_hash=hash_password(pw), company=c)])
            s.commit()
            print(f"Member {login} for {c.name} ready, trial until {end}.")
        elif args.cmd == "import-board":
            with open(args.path, encoding="utf-8") as f:
                rows = json.load(f).get("tenders", [])
            print(f"Imported {import_rows(s, rows)} tenders.")

if __name__ == "__main__":
    main()
