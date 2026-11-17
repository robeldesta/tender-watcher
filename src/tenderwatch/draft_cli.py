"""Bid pack drafting from the command line.

  PYTHONPATH=src python -m tenderwatch.draft_cli demo          # offline, canned model reply
  PYTHONPATH=src python -m tenderwatch.draft_cli demo --llm    # real Claude call (ANTHROPIC_API_KEY)
  PYTHONPATH=src python -m tenderwatch.draft_cli draft \\
      --profile config/bid_profiles.yaml --firm "Example Construction PLC" \\
      --tender tender.json [--bid-doc section3.txt] [--out out/bids]
  PYTHONPATH=src python -m tenderwatch.draft_cli draft --profile ... --firm ... --key "worldbank:https://..."

--tender takes a tender as JSON (the same shape the store saves). --key reads it
from data/tenders.db instead. --bid-doc is plain text of the bidding document
(instructions to bidders, qualification criteria), copied out of the PDF; without
it the checklist only covers what the short notice says.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .bid_draft import build_pack, draft, render, slug
from .bid_profile import load_profiles
from .llm_match import make_client
from .models import Tender

ROOT = Path(__file__).resolve().parents[2]
DEMO_PROFILES = ROOT / "config/bid_profiles.example.yaml"
DEMO_TENDER = ROOT / "tests/fixtures/bid/sample_tender.json"
DEMO_REPLY = ROOT / "tests/fixtures/bid/sample_reply.json"
DEMO_DATE = date(2026, 10, 27)


def tender_from_dict(d: dict) -> Tender:
    fields = {k: d.get(k) for k in Tender.__dataclass_fields__ if k in d}
    if fields.get("deadline"):
        fields["deadline"] = date.fromisoformat(str(fields["deadline"])[:10])
    fields["requirements"] = fields.get("requirements") or []
    return Tender(**fields)


def tender_from_store(key: str, db: str = "data/tenders.db") -> Tender:
    from .store import Store
    for d in Store(db).all():
        if f"{d['source']}:{d['url']}" == key:
            return tender_from_dict(d)
    sys.exit(f"No tender with key {key!r} in {db}.")


def write(text: str, out_dir: str, firm: str, title: str) -> Path:
    path = Path(out_dir) / f"{slug(firm, 30)}--{slug(title)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="tenderwatch.draft_cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="draft the sample tender for the example firm")
    d.add_argument("--llm", action="store_true", help="call Claude instead of using the canned reply")
    r = sub.add_parser("draft", help="draft a bid pack for one firm and one tender")
    r.add_argument("--profile", required=True, help="YAML with a firms: list")
    r.add_argument("--firm", required=True, help="legal_name of the firm in the profile file")
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--tender", help="tender JSON file")
    src.add_argument("--key", help="tender key in the store, source:url")
    r.add_argument("--bid-doc", help="plain-text bidding document sections")
    r.add_argument("--out", default="out/bids")
    a = ap.parse_args(argv)

    if a.cmd == "demo":
        profile = load_profiles(DEMO_PROFILES)["Example Construction PLC"]
        tender = tender_from_dict(json.loads(DEMO_TENDER.read_text()))
        if a.llm:
            pack = draft(tender, profile, make_client(), today=DEMO_DATE)
        else:
            # The canned reply contains a made-up project id, a link and an
            # unsupported figure on purpose, so the demo shows the checks.
            pack = build_pack(tender, profile, json.loads(DEMO_REPLY.read_text()), today=DEMO_DATE)
        print(render(pack))
        return

    profiles = load_profiles(a.profile)
    if a.firm not in profiles:
        sys.exit(f"No firm {a.firm!r} in {a.profile}. Firms there: {', '.join(profiles) or 'none'}")
    tender = (tender_from_dict(json.loads(Path(a.tender).read_text())) if a.tender
              else tender_from_store(a.key))
    extra = Path(a.bid_doc).read_text() if a.bid_doc else ""
    pack = draft(tender, profiles[a.firm], make_client(), extra_text=extra)
    path = write(render(pack), a.out, a.firm, tender.title)
    flagged = len(pack.warnings) + sum(r.status == "gap" for r in pack.requirements)
    print(f"Wrote {path} ({len(pack.requirements)} requirements, {flagged} flagged before sending)")


if __name__ == "__main__":
    main()
