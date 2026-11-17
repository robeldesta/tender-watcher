import logging
from datetime import date

from .llm_match import LLMMatch, llm_score
from .models import Company, Tender
from .match import score

log = logging.getLogger(__name__)

MIN_LLM_SCORE = 40


def rank(c: Company, tenders: list[Tender], limit: int = 10, client=None,
         today: date | None = None) -> list[tuple[Tender, LLMMatch | None]]:
    """Keyword prefilter, then (with a client) an LLM re-rank of the best candidates.

    A tender whose LLM call fails keeps its keyword place with no LLM match,
    so an API outage never empties the digest.
    """
    ranked = sorted(((score(t, c, today), t) for t in tenders), key=lambda x: -x[0])
    ranked = [t for s, t in ranked if s > 0]
    if client is None:
        return [(t, None) for t in ranked[:limit]]
    scored, failed = [], []
    for t in ranked[: limit * 3]:  # cap API calls per company per day
        try:
            m = llm_score(t, c, client)
        except Exception as e:
            log.warning("LLM match failed for %s: %s", t.key, e)
            failed.append((t, None))
            continue
        if m.score >= MIN_LLM_SCORE:
            scored.append((t, m))
    scored.sort(key=lambda x: -x[1].score)
    return (scored + failed)[:limit]


def build_digest(c: Company, tenders: list[Tender], limit: int = 10, client=None,
                 today: date | None = None) -> str:
    ranked = rank(c, tenders, limit, client, today)
    if not ranked:
        return f"Hi {c.name}: no new matching tenders today."
    lines = [f"Tenders for {c.name} today:"]
    for t, m in ranked:
        dl = t.deadline.isoformat() if t.deadline else "deadline not stated: check source"
        lines.append(f"\n- {t.title}\n  Buyer: {t.buyer or 'n/a'} | Deadline: {dl}")
        if m:
            lines.append(f"  Match: {m.score}/100. {m.reason}")
        elif client is not None:
            lines.append("  Match: AI score unavailable, matched on keywords")
        lines.append(f"  Source: {t.url}")
    return "\n".join(lines)
