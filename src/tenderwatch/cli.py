import json
import sys
from datetime import date
from pathlib import Path

from .digest import build_digest
from .llm_match import make_client
from .models import Company
from .sources.worldbank import WorldBankSource, parse

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/worldbank/procnotices_ethiopia.json"
FIXTURE_DATE = date(2026, 10, 26)  # when the fixture was captured, so the demo never goes stale


def demo(use_llm: bool = False) -> None:
    """Runs offline on saved World Bank notices so you can see the digest format.

    With --llm it scores the tenders with Claude (needs ANTHROPIC_API_KEY).
    """
    tenders = parse(json.loads(FIXTURE.read_text()))
    c = Company("Example Construction PLC", "telegram:@example", "construction",
                keywords=["road", "culvert", "bridge", "irrigation"], exclude=["software"],
                certifications=["MoUD Grade 3 General Contractor"], size="medium, about 80 staff")
    print(build_digest(c, tenders, client=make_client() if use_llm else None, today=FIXTURE_DATE))


def run() -> None:
    """Fetch live notices and store the new ones."""
    from .store import Store
    Path("data").mkdir(exist_ok=True)
    store = Store()
    tenders = WorldBankSource().fetch()
    new = sum(store.add(t) for t in tenders)
    print(f"worldbank: {len(tenders)} open notices fetched, {new} new")


def fetch_fixture() -> None:
    """Replace the saved fixture with a raw live response (needs network)."""
    FIXTURE.write_text(json.dumps(WorldBankSource(rows=20).fetch_raw(), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {FIXTURE}")


COMMANDS = {"demo": demo, "run": run, "fetch-fixture": fetch_fixture}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if cmd not in COMMANDS:
        sys.exit(f"Unknown command {cmd!r}. Use one of: {', '.join(COMMANDS)}")
    if cmd == "demo":
        demo(use_llm="--llm" in sys.argv[2:])
    else:
        COMMANDS[cmd]()
