import json

import anthropic

from .models import Tender

MODEL = "claude-sonnet-5-5"
PROMPT = """Extract fields from this tender notice. Reply with JSON only:
{"buyer": str|null, "deadline": "YYYY-MM-DD"|null, "budget_etb": number|null,
 "region": str|null, "sector": str|null, "requirements": [str]}
Use null when the text does not say. Never guess a deadline.

Notice:
"""


def extract(t: Tender, client: anthropic.Anthropic | None = None) -> Tender:
    client = client or anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL, max_tokens=800,
        messages=[{"role": "user", "content": PROMPT + t.raw_text[:8000]}],
    )
    data = json.loads(msg.content[0].text)
    from datetime import date
    t.buyer = data.get("buyer")
    t.deadline = date.fromisoformat(data["deadline"]) if data.get("deadline") else None
    t.budget_etb = data.get("budget_etb")
    t.region = data.get("region")
    t.sector = data.get("sector")
    t.requirements = data.get("requirements") or []
    return t
