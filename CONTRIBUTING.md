# Contributing to HistWarDB

Thanks for your interest in improving HistWarDB. This is an academic research
tool, so correctness and reproducibility matter more than feature velocity.

## Development setup

```bash
git clone https://github.com/VincenzoManto/histwardb.git
cd histwardb
pip install -e ".[dev]"
```

## Running tests

```bash
pytest --cov=histwardb
```

By default tests never hit the live Wikidata endpoint: the network layer
(`HistWarDB._request`) is isolated behind an injectable `requests`-like
`session`, and all parsing/filtering logic in `histwardb.core` is pure and
tested against static fixtures in `tests/conftest.py`.

Unit tests on the query text cannot tell whether a QID means what its
comment says, or whether the query returns anything. If you touch
`histwardb/query.py`, also run the opt-in live checks:

```bash
pytest -m network
```

## Publishing a data snapshot

```python
from histwardb import HistWarDB

db = HistWarDB(start_year=1700, end_year=2026)   # strict: raises if any chunk fails
db.save_snapshot("data/snapshots/YYYY-MM-DD")
```

Commit the new directory. `tests/test_snapshot_data.py` checks every shipped
snapshot's checksum and invariants.

## Crosswalk and validation

Both scripts need reference data that HistWarDB does not redistribute
(Correlates of War, Gleditsch-Ward, UCDP/PRIO); download links are in each
script's docstring.

```bash
# Rebuild the COW / Gleditsch-Ward crosswalk for the states in a snapshot
python scripts/build_crosswalk.py data/snapshots/YYYY-MM-DD states2016.csv iisystem.dat     src/histwardb/data/crosswalk.csv

# Regenerate validation/report.md
python scripts/validate.py data/snapshots/YYYY-MM-DD src/histwardb/data/crosswalk.csv     Inter-StateWarData_v4.0.csv Extra-StateWarData_v4.0.csv UcdpPrioConflict_v25_1.csv validation
```

Review every new automatic match in the crosswalk before committing it, and
add hand-checked assignments to `MANUAL` in the builder.

## Linting

```bash
ruff check src tests
```

## Guidelines

- Keep the SPARQL query building (`histwardb/query.py`) separate from
  network I/O and from data cleaning/transformation logic
  (`histwardb/core.py`) — this is what makes the test suite possible without
  live network access.
- If a historical state has no usable dissolution date on Wikidata, fix it on
  Wikidata first. Only if that is not possible, add it to `FORCED_MAX_YEARS`
  in `histwardb/core.py` and cover it with a test.
- Any change to the coding rules must be reflected in `CODEBOOK.md`.
- Add or update a test for any behavior change. PRs that change filtering
  logic without a corresponding test in `tests/test_parsing.py` or
  `tests/test_dataset.py` will not be merged.
- Open an issue before starting large changes (e.g. new analytical views,
  new data sources).
