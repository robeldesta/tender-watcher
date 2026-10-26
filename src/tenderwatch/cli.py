import sys
from datetime import date, timedelta

from .digest import build_digest
from .llm_match import make_client
from .models import Company, Tender


def demo(use_llm: bool = False) -> None:
    """Runs offline with fake data so you can see the digest format.

    With --llm it scores the fake tenders with Claude (needs ANTHROPIC_API_KEY).
    """
    tenders = [
        Tender("demo", "https://example.com/1", "Supply of road culvert pipes", "culvert road works",
               buyer="Sample Roads Authority", deadline=date.today() + timedelta(days=12),
               sector="construction", region="Addis Ababa"),
        Tender("demo", "https://example.com/2", "Hospital software license", "software medical",
               sector="IT"),
    ]
    c = Company("Example Construction PLC", "telegram:@example", "construction",
                keywords=["road", "culvert"], regions=["Addis Ababa"], exclude=["software"],
                certifications=["MoUD Grade 3 General Contractor"], size="medium, about 80 staff")
    print(build_digest(c, tenders, client=make_client() if use_llm else None))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if cmd == "demo":
        demo(use_llm="--llm" in sys.argv[2:])
    else:
        print("Only 'demo' exists so far. Real sources come in week 1.")
