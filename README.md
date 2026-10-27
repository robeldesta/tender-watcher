# Tender Watcher

AI that watches Ethiopian public and NGO tenders, matches them to a company's profile, and sends a daily digest (Telegram/email). Paid tier drafts bid documents.

**Launch date: 31 Dec 2026.** See [ROADMAP.md](ROADMAP.md).

## Layout
- `src/tenderwatch/sources/` one module per tender source (each returns raw `Tender`s)
- `extract.py` LLM turns messy text into structured fields (deadline, buyer, budget, requirements)
- `match.py` scores tenders against a company profile
- `digest.py` builds the daily message
- `store.py` SQLite storage with de-duplication
- `cli.py` `python -m tenderwatch.cli run|digest`
- `dataset.py` record human-checked fields (`review`, `check`) and export notices plus checked labels for training Kyrie, our own model (`export`)
- `config/companies.example.yaml` copy to `companies.yaml` (not committed)

## Setup
```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # needed for extract/match only
PYTHONPATH=src python -m tenderwatch.cli demo
```

## Rules
- Respect each source's terms and robots.txt; keep request rates low.
- Verify every deadline by showing the source link in the digest. Never present a model guess as fact.
