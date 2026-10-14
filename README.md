# HistWarDB 🌍⚔️
> *Historical & Sovereign War Dataset powered by Wikidata*

[![Tests](https://github.com/VincenzoManto/histwardb/actions/workflows/tests.yml/badge.svg)](https://github.com/VincenzoManto/histwardb/actions/workflows/tests.yml)
[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![DOI](https://zenodo.org/badge/1410596751.svg)](https://doi.org/10.5281/zenodo.23243673)

**HistWarDB** is a Python toolkit that extracts, cleans and analyses war participation data from Wikidata, from 1700 to the present, for both modern sovereign states and historical states. It produces a table of who took part in which war and a Country × Year panel derived from it.

The coding rules are written down in the [codebook](CODEBOOK.md), and agreement with Correlates of War and UCDP/PRIO is measured in the [validation report](validation/report.md). Please read both before using the data for research: coverage is bounded by Wikidata.

---

## Key Features

* **Wars and their subclasses:** every Wikidata item typed as `war` or a subclass (world war, civil war, proxy war, ...), minus peacekeeping missions, military exercises and fictional wars.
* **Conflict structure:** each conflict's Wikidata type, the war it is part of, and its top-level war; fronts and campaigns can be kept, dropped or rolled up into their war.
* **Sides where Wikidata has them:** coalition participants (Allies, Axis, Central Powers, ...) are expanded into member states with a side, giving a dyadic table of opposing states.
* **State belligerents only:** participants must be typed as sovereign state, historical country or country.
* **Stable identifiers:** every conflict and country carries its Wikidata QID; countries also carry Correlates of War and Gleditsch–Ward codes from a bundled crosswalk, and an ISO 3166-1 alpha-3 code where Wikidata has one.
* **Lifespan-aware:** a state is never counted at war in a year in which it did not exist, and the panel says which country-years fall outside a state's lifespan.
* **Explicit uncertainty:** conflicts with no recorded end date are flagged, and the rule used to extend them is a parameter.
* **Fails loudly:** queries are retried with backoff; a chunk that cannot be fetched raises an error instead of silently leaving a gap.
* **Frozen snapshots:** save and reload a dataset with retrieval metadata and a checksum, so published results can be reproduced.
* **Analytical views and plots:** long format, binary matrix, conflict names, lists, and a Plotly heatmap.

---

## Project layout

```
src/histwardb/
    query.py      # SPARQL query construction (pure, no I/O)
    core.py       # HistWarDB class: fetch, parse, clean, analytical views
    relations.py  # part-of chains, coalitions, sides, dyads (pure)
    data/         # COW / Gleditsch-Ward crosswalk
data/snapshots/  # frozen, checksummed copies of the dataset
scripts/       # crosswalk builder, validation against COW and UCDP
validation/    # validation report
tests/         # pytest suite (parsing, filtering, views, mocked network)
CODEBOOK.md    # coding rules and column definitions
HistWarDB.ipynb  # usage demo notebook (Colab-compatible)
```

## Installation

```bash
pip install git+https://github.com/VincenzoManto/histwardb.git
```

For local development (editable install + test/lint tooling):

```bash
git clone https://github.com/VincenzoManto/histwardb.git
cd histwardb
pip install -e ".[dev]"
```

---

## Quick Start

```python
from histwardb import HistWarDB

# Fetch live from Wikidata, 1700 to 2026 (about 20 queries, 2-3 minutes)
gwd = HistWarDB(start_year=1700, end_year=2026)

# Who took part in what: one row per conflict x country, with QIDs and dates
print(gwd.participations.head())

# Country x Year panel for specific countries (exact match on name, QID or ISO code)
france_uk_wars = gwd(countries=["France", "United Kingdom"], view="long", only_in_war=True)

# Binary matrix for a period; years outside the state's lifespan read <NA>
binary_view = gwd(countries="German Empire", view="binary", from_date=1900, to_date=1925, only_existing=True)

# Heatmap for the 15 states with most war-years
gwd.plot_heatmap(top_n=15, from_year=1800, to_year=2026).show()
```

### Reproducible use: snapshots

Wikidata changes every day, so two live fetches can differ. For anything you intend to publish, work from a frozen snapshot and cite it:

```python
# Load the snapshot shipped with the repository (no network access)
gwd = HistWarDB.from_snapshot("data/snapshots/2026-10-14")

# Or freeze your own fetch
gwd = HistWarDB(start_year=1700, end_year=2026)
gwd.save_snapshot("my_snapshot")   # participations.csv + metadata.json
```

`metadata.json` records the retrieval time, parameters, query text, row counts and a SHA-256 checksum that `from_snapshot` verifies.

### Wars, fronts and sides

```python
gwd = HistWarDB.from_snapshot("data/snapshots/2026-10-14")
gwd.conflicts                       # one row per conflict: type, parent/root war, dates, death toll
gwd.dyads                           # pairs of states on opposing coalition sides
HistWarDB(subconflicts="roll_up")   # attribute fronts and campaigns to their top-level war
```

Sides come only from coalitions that Wikidata lists as participants, so most conflicts have none; see the [codebook](CODEBOOK.md#24-sides-coalitions-and-roles).

### Conflicts with no end date

Some Wikidata conflicts have a start date and no end date. The `missing_end` parameter decides how far they are extended:

```python
HistWarDB(missing_end="heuristic")   # default: to the present if started 1800 or later, else 5 years
HistWarDB(missing_end="start_only")  # conservative: start year only
HistWarDB(missing_end="open")        # always to the present
HistWarDB(missing_end="drop")        # discard them
```

Whatever the rule, affected rows are flagged (`duration_estimated` in the participation table, `Duration_Estimated` and `War_Imputed` in the panel), so you can filter them out. The cutoff year and pre-cutoff duration of the default rule are parameters too (`heuristic_cutoff_year`, `pre_cutoff_duration_years`).

### Severity threshold

```python
HistWarDB(min_casualties=1000)  # drop conflicts with a recorded death toll below 1,000
```

Conflicts without a recorded toll (Wikidata `P1120`) are kept. Few conflicts have one, so this filter currently changes little; see the [codebook](CODEBOOK.md#12-optional-severity-threshold-min_casualties).

---

## Supported Views (`view` parameter)

* **`"long"`**: tidy DataFrame with `Country`, `Year`, `War_State`, `Active_Conflicts`, `Duration_Estimated`, `Country_QID`, `ISO3`, `State_Exists`, `War_Imputed`.
* **`"binary"`**: countries as rows, years as columns, `1` = at war, `0` = no war recorded.
* **`"names"`**: same shape, with the names of the active conflicts.
* **`"list"`**: alphabetical list of the conflict names under the current filters.
* **`"countries"` / `"conflicts"`**: sorted lists of countries at war, or of conflicts, under the current filters.

`War_State = 0` means "no war recorded in Wikidata", not "peace". Use `State_Exists` (or `only_existing=True`) to separate years in which the state did not exist. See the [codebook](CODEBOOK.md#72-country--year-grid-viewlong).

---

## Testing

The default test suite never queries the live Wikidata endpoint. The `HistWarDB`
class accepts an injectable `session` object for its HTTP calls, and all
parsing/filtering logic is pure and tested against static fixtures:

```bash
pip install -e ".[dev]"
pytest --cov=histwardb
```

A small opt-in suite checks the real endpoint: that every QID constant means
what the code says, and that well-known wars, states and sides come back. It
also runs weekly in CI (`.github/workflows/live-tests.yml`).

```bash
pytest -m network
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

---

## Citation

If you use **HistWarDB** in your academic research, working paper, or data science project, please cite it using the following format (also available in machine-readable form in [`CITATION.cff`](CITATION.cff)), and state which snapshot you used:

```bibtex
@software{histwardb2026,
  author = {Manto, Vincenzo and Dal Cero, Riccardo},
  title = {HistWarDB: A Dynamic Wikidata-Based Historical War Dataset},
  year = {2026},
  url = {https://github.com/VincenzoManto/histwardb}
}
```

---

## License

Code is distributed under the **MIT License**. See [`LICENSE`](LICENSE) for more information. The data comes from [Wikidata](https://www.wikidata.org/), which is released under CC0.
