# World Bank fixtures

`procnotices_ethiopia.json` holds real Ethiopian notices from
`https://search.worldbank.org/api/v2/procnotices?format=json&project_ctry_name_exact=Ethiopia`,
read on 26 Oct 2026. Field names and values come from the live API. It is
**not a byte-for-byte download** (the cloud sandbox it was built in could not
reach the API directly):

- `notice_text` is a short summary in HTML, not the full notice body.
- Two `bid_description` values were cut short by the tool that read them.
- The two "Contract Award" rows keep only the fields the parser looks at.

To replace it with a raw response, run from a machine that can reach the API:

```
PYTHONPATH=src python -m tenderwatch.cli fetch-fixture
```

Then set `FIXTURE_DATE` in `cli.py` to the day you fetched it and update the expected values in `tests/test_worldbank.py`.
