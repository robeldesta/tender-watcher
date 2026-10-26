from .models import Company, Tender
from .match import score


def build_digest(c: Company, tenders: list[Tender], limit: int = 10) -> str:
    ranked = sorted(((score(t, c), t) for t in tenders), key=lambda x: -x[0])
    ranked = [(s, t) for s, t in ranked if s > 0][:limit]
    if not ranked:
        return f"Hi {c.name}: no new matching tenders today."
    lines = [f"Tenders for {c.name} today:"]
    for _, t in ranked:
        dl = t.deadline.isoformat() if t.deadline else "deadline not stated: check source"
        lines.append(f"\n- {t.title}\n  Buyer: {t.buyer or 'n/a'} | Deadline: {dl}\n  Source: {t.url}")
    return "\n".join(lines)
