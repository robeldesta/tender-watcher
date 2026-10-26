import sys
from datetime import date, timedelta

from .digest import build_digest
from .models import Company, Tender


def demo() -> None:
    """Runs offline with fake data so you can see the digest format."""
    tenders = [
        Tender("demo", "https://example.com/1", "Supply of road culvert pipes", "culvert road works",
               buyer="Sample Roads Authority", deadline=date.today() + timedelta(days=12),
               sector="construction", region="Addis Ababa"),
        Tender("demo", "https://example.com/2", "Hospital software license", "software medical",
               sector="IT"),
    ]
    c = Company("Example Construction PLC", "telegram:@example", "construction",
                keywords=["road", "culvert"], regions=["Addis Ababa"], exclude=["software"])
    print(build_digest(c, tenders))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if cmd == "demo":
        demo()
    else:
        print("Only 'demo' exists so far. Real sources come in week 1.")
