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

Tests never hit the live Wikidata endpoint: the network layer
(`HistWarDB._fetch_chunk`) is isolated behind an injectable `requests`-like
`session`, and all parsing/filtering logic in `histwardb.core` is pure and
tested against static fixtures in `tests/conftest.py`.

## Linting

```bash
ruff check src tests
```

## Guidelines

- Keep the SPARQL query building (`histwardb/query.py`) separate from
  network I/O and from data cleaning/transformation logic
  (`histwardb/core.py`) — this is what makes the test suite possible without
  live network access.
- If you add a historical state with an unreliable Wikidata end date, add it
  to `FORCED_MAX_YEARS` in `histwardb/core.py` and cover it with a test.
- Add or update a test for any behavior change. PRs that change filtering
  logic without a corresponding test in `tests/test_parsing.py` or
  `tests/test_dataset.py` will not be merged.
- Open an issue before starting large changes (e.g. new analytical views,
  new data sources).
