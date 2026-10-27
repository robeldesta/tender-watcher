# Tender Watcher

AI that watches Ethiopian public and NGO tenders, matches them to a company's profile, and sends a daily digest (Telegram/email). Paid tier drafts bid documents.

**Launch date: 31 Dec 2026.** See [ROADMAP.md](ROADMAP.md).

## Layout
- `src/tenderwatch/sources/` one module per tender source (each returns raw `Tender`s)
- `extract.py` LLM turns messy text into structured fields (deadline, buyer, budget, requirements)
- `match.py` cheap keyword score, used as a prefilter
- `llm_match.py` Claude scores each prefiltered tender 0-100 against the profile (sector, regions, certifications, size) with a one-line reason
- `digest.py` builds the daily message
- `bid_draft.py` drafts a bid pack (cover letter, company profile, experience tables, compliance checklist) for one firm and one tender; `bid_profile.py` holds the firm's bid profile and renders the tables; `draft_cli.py` runs it
- `store.py` SQLite storage with de-duplication
- `cli.py` `python -m tenderwatch.cli run|digest`
- `config/companies.example.yaml` copy to `companies.yaml` (not committed)

## Setup
```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # needed for extract/match only
PYTHONPATH=src python -m tenderwatch.cli demo        # offline, keyword match only
PYTHONPATH=src python -m tenderwatch.cli demo --llm  # adds Claude match score + reason
```
The match model defaults to `claude-opus-5-5`; set `TENDERWATCH_MATCH_MODEL` to change it.

## Bid drafting
Fill a firm's profile from the onboarding call into `config/bid_profiles.yaml` (copy the example; not committed). Then:
```
PYTHONPATH=src python -m tenderwatch.draft_cli demo          # offline sample pack, canned model reply
PYTHONPATH=src python -m tenderwatch.draft_cli draft --profile config/bid_profiles.yaml \
    --firm "Example Construction PLC" --tender tender.json --bid-doc section3.txt
```
The pack is written to `out/bids/` as Markdown to paste into the firm's letterhead. Paste the bidding document's instructions and qualification criteria (ITB, Section III) into `--bid-doc`: a World Bank notice alone is too short for a full checklist. Tables, values and licence numbers come only from the profile; Claude writes the prose and the checklist, and anything it can't back up is listed under "Before you send". The model defaults to `claude-opus-5-5`; set `TENDERWATCH_DRAFT_MODEL` to change it.

## Tests
```
pip install -r requirements-dev.txt
python -m pytest     # offline: the Anthropic client is mocked
```

## Rules
- Respect each source's terms and robots.txt; keep request rates low.
- Verify every deadline by showing the source link in the digest. Never present a model guess as fact.
- Tender text is untrusted input to the model: keep it inside the `<tender>` fence, give the model no tools, and clean anything it writes before it reaches a customer.
