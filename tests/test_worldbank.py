import json
from datetime import date
from pathlib import Path

import httpx

from tenderwatch import cli
from tenderwatch.sources.worldbank import API_URL, WorldBankSource, parse, to_tender

FIXTURE = Path(__file__).parent / "fixtures/worldbank/procnotices_ethiopia.json"


def payload() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parse_keeps_open_notices_and_skips_awards():
    tenders = parse(payload())
    assert len(tenders) == 4
    assert all(t.source == "worldbank" for t in tenders)
    assert not any("Forage Seeds" in t.title for t in tenders)


def test_fields_map_into_tender_model():
    t = parse(payload())[0]
    assert t.title.startswith("Construction Works of Veterinary Clinic")
    assert t.url == "https://projects.worldbank.org/en/projects-operations/procurement-detail/OP00476746"
    assert t.key == "worldbank:" + t.url
    assert t.buyer == "Gambeala RPCU"
    assert t.deadline == date(2026, 11, 18)
    assert t.sector == "construction"
    assert "ET-GAM-RPCU-570832-CW-RFB" in t.raw_text
    assert "<p>" not in t.raw_text


def test_missing_or_bad_deadline_is_none_not_a_guess():
    n = {"id": "X1", "notice_type": "Request for Expression of Interest", "bid_description": "Consultant"}
    assert to_tender(n).deadline is None
    assert to_tender({**n, "submission_deadline_date": "not a date"}).deadline is None


def test_notice_without_id_or_title_is_dropped():
    assert to_tender({"notice_type": "Invitation for Bids", "bid_description": "x"}) is None
    assert to_tender({"id": "X2", "notice_type": "Invitation for Bids"}) is None


def test_fetch_sends_country_filter_and_parses_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        return httpx.Response(200, json=payload())

    src = WorldBankSource(client=httpx.Client(transport=httpx.MockTransport(handler)))
    tenders = src.fetch()
    assert str(seen["url"]).startswith(API_URL)
    assert seen["url"].params["project_ctry_name_exact"] == "Ethiopia"
    assert seen["url"].params["format"] == "json"
    assert len(tenders) == 4


def test_demo_runs_on_fixture(capsys):
    cli.demo()
    out = capsys.readouterr().out
    assert "Tenders for Example Construction PLC" in out
    assert "Gara medium span bridge" in out
    assert "Deadline: 2026-11-13" in out
    assert "procurement-detail/OP00475955" in out
