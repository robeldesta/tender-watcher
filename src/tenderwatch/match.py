from datetime import date

from .models import Company, Tender


def score(t: Tender, c: Company) -> int:
    """Cheap rule-based score, 0 means skip. An LLM re-rank can come later."""
    text = f"{t.title} {t.raw_text}".lower()
    if any(x.lower() in text for x in c.exclude):
        return 0
    if t.deadline and t.deadline < date.today():
        return 0
    if t.budget_etb is not None and t.budget_etb < c.min_budget_etb:
        return 0
    s = sum(2 for k in c.keywords if k.lower() in text)
    if t.sector and t.sector.lower() == c.sector.lower():
        s += 3
    if t.region and t.region in c.regions:
        s += 1
    return s
