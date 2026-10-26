"""World Bank procurement notices for World Bank-financed projects.

Public JSON API, no key needed. Many Ethiopian public works and supply tenders
are funded by the World Bank and published here with a firm deadline.
"""
from datetime import date

import httpx
from bs4 import BeautifulSoup

from ..models import Tender
from .base import Source

API_URL = "https://search.worldbank.org/api/v2/procnotices"
DETAIL_URL = "https://projects.worldbank.org/en/projects-operations/procurement-detail/{id}"
USER_AGENT = "TenderWatcher/0.1 (+https://github.com/robeldesta/tender-watcher)"

# Awards and plans announce who won or what is coming; nothing to bid on.
SKIP_TYPES = {"contract award", "general procurement notice"}

SECTORS = {
    "CW": "construction",
    "GO": "goods",
    "CS": "consulting",
    "NC": "services",
}


class WorldBankSource(Source):
    name = "worldbank"

    def __init__(self, country: str = "Ethiopia", rows: int = 100, client: httpx.Client | None = None):
        self.country = country
        self.rows = rows
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})

    def fetch_raw(self) -> dict:
        """One request for the newest notices. Returns the API's JSON as-is."""
        params = {
            "format": "json",
            "rows": self.rows,
            "project_ctry_name_exact": self.country,
            "srt": "submission_date",
            "order": "desc",
        }
        r = self.client.get(API_URL, params=params)
        r.raise_for_status()
        return r.json()

    def fetch(self) -> list[Tender]:
        return parse(self.fetch_raw())


def parse(payload: dict) -> list[Tender]:
    return [t for n in payload.get("procnotices", []) if (t := to_tender(n))]


def to_tender(n: dict) -> Tender | None:
    if (n.get("notice_type") or "").strip().lower() in SKIP_TYPES:
        return None
    title = (n.get("bid_description") or n.get("project_name") or "").strip()
    if not n.get("id") or not title:
        return None
    body = BeautifulSoup(n.get("notice_text") or "", "html.parser").get_text(" ", strip=True)
    raw_text = "\n".join(x for x in [
        title,
        n.get("project_name"),
        n.get("procurement_method_name"),
        f"Reference: {n['bid_reference_no']}" if n.get("bid_reference_no") else None,
        body,
    ] if x)
    return Tender(
        source="worldbank",
        url=DETAIL_URL.format(id=n["id"]),
        title=title,
        raw_text=raw_text,
        buyer=n.get("contact_organization"),
        deadline=_deadline(n.get("submission_deadline_date")),
        sector=SECTORS.get(n.get("procurement_group") or ""),
        # The notice gives the project's country only; the region is in the text.
        region=None,
    )


def _deadline(value: str | None) -> date | None:
    """'2026-11-18T00:00:00Z' -> date(2026, 11, 18). Missing or malformed -> None."""
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None
