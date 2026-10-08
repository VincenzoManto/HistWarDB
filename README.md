# HistWarDB 🌍⚔️
> *Advanced Historical & Sovereign War Dataset powered by Wikidata*

[![Tests](https://github.com/VincenzoManto/histwardb/actions/workflows/tests.yml/badge.svg)](https://github.com/VincenzoManto/histwardb/actions/workflows/tests.yml)
[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**HistoricalWarDB** (HistWarDB) is a dynamic, programmatic Python toolkit designed to extract, clean, and analyze global warfare data from Wikidata spanning from 1700 to the present. Built for researchers, historians, and data scientists, it bridges the gap between crowdsourced knowledge and rigorous historical data pipelines, supporting both modern sovereign states and historical empires.

---

## Key Features

* **Dynamic SPARQL Pipeline:** Fetches conflicts using 25-year temporal chunks to bypass query limits and ensure high data completeness.
* **Rigorous Anti-Noise Filtering:** Automatically excludes peacekeeping missions (`wd:Q35349`), military exercises (`wd:Q1151430`), and non-state actors.
* **Historical State Support:** Seamlessly tracks historical entities (e.g., *German Empire*, *Qing dynasty*, *Kingdom of Montenegro*) with built-in lifespan sanity checks.
* **Flexible Analytical Views:** Instantly transforms data into long formats, binary matrices (`0/1`), conflict name mappings, or custom `LIKE` keyword search results.
* **Interactive Visualizations:** Out-of-the-box Plotly heatmap generation to visualize global conflict intensity over centuries.
* **Tested and reproducible:** Network I/O, SPARQL query construction, and data-cleaning logic are separated into independent, unit-tested modules — no live Wikidata access required to run the test suite.

---

## Project layout

```
src/histwardb/
    query.py   # SPARQL query construction (pure, no I/O)
    core.py    # HistWarDB class: fetch, parse, clean, analytical views
tests/         # pytest suite (parsing, filtering, views, mocked network)
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

# Initialize the dataset from 1700 to 2026
gwd = HistWarDB(start_year=1700, end_year=2026)

# Query specific countries in long format
france_uk_wars = gwd(countries=["France", "United Kingdom"], view="long", only_in_war=True)
print(france_uk_wars.head())

# Get a binary matrix for specific historical periods
binary_view = gwd(countries="German Empire", view="binary", from_date="1900-01-01", to_date="1918-12-31")

# Plot an interactive heatmap for the top 15 conflict-prone states
gwd.plot_heatmap(top_n=15, from_year=1800, to_year=2026).show()
```

---

## Supported Views (`view` parameter)

The dataset interface supports multiple analytical views via the `gwd(...)` call:

* **`"long"`**: Standard tidy DataFrame containing columns: `Country`, `Year`, `War_State`, and `Active_Conflicts`.
* **`"binary"`**: Wide pivot table with countries as rows, years as columns, and binary values (`1` = at war, `0` = peace).
* **`"names"`**: Wide pivot table containing the literal names of active conflicts per year.
* **`"list"`**: Alphabetical list of all unique conflict names extracted under current filters.
* **`"countries"` / `"conflicts"`**: Sorted lists of active entities or unique conflicts matching the query criteria.

---

## Testing

The test suite never queries the live Wikidata endpoint. The `HistWarDB`
class accepts an injectable `session` object for its HTTP calls, and all
parsing/filtering logic (`histwardb.core.parse_bindings`,
`clean_dataframe`, `build_long_grid`) is pure and tested against static
fixtures:

```bash
pip install -e ".[dev]"
pytest --cov=histwardb
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

---

## Citation

If you use **HistWarDB** in your academic research, working paper, or data science project, please cite it using the following format (also available in machine-readable form in [`CITATION.cff`](CITATION.cff)):

```bibtex
@software{histwardb2026,
  author = {Vincenzo Manto},
  title = {HistWarDB: A Dynamic Wikidata-Based Historical War Dataset},
  year = {2026},
  url = {https://github.com/VincenzoManto/histwardb}
}
```

---

## License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for more information.
