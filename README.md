# HistWarDB 🌍⚔️
> *Advanced Historical & Sovereign War Dataset powered by Wikidata*

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**HistoricalWarDB** (HistWarDB) is a dynamic, programmatic Python toolkit designed to extract, clean, and analyze global warfare data from Wikidata spanning from 1700 to the present. Built for researchers, historians, and data scientists, it bridges the gap between crowdsourced knowledge and rigorous historical data pipelines, supporting both modern sovereign states and historical empires.

---

## Key Features

* **Dynamic SPARQL Pipeline:** Fetches conflicts using 25-year temporal chunks to bypass query limits and ensure high data completeness.
* **Rigorous Anti-Noise Filtering:** Automatically excludes peacekeeping missions (`wd:Q35349`), military exercises (`wd:Q1151430`), and non-state actors.
* **Historical State Support:** Seamlessly tracks historical entities (e.g., *German Empire*, *Qing dynasty*, *Kingdom of Montenegro*) with built-in lifespan sanity checks.
* **Flexible Analytical Views:** Instantly transforms data into long formats, binary matrices (`0/1`), conflict name mappings, or custom `LIKE` keyword search results.
* **Interactive Visualizations:** Out-of-the-box Plotly heatmap generation to visualize global conflict intensity over centuries.

---

## Installation & Requirements

Ensure you have the required dependencies installed (especially useful for Google Colab environments):

```bash
pip install pandas requests plotly
```

---

## Quick Start

```python
import datetime
from histwardb import FastWikidataWarDataset

# Initialize the dataset from 1700 to 2026
gwd = FastWikidataWarDataset(start_year=1700, end_year=2026)

# Query specific countries in long format
france_uk_wars = gwd(countries=["France", "United Kingdom"], view="long", only_in_war=True)
print(france_uk_wars.head())

# Get a binary matrix for specific historical periods
binary_view = gwd(countries="German Empire", view="binary", from_date="1900-01-01", to_date="1918-12-31")

# Plot an interactive heatmap for the top 15 conflict-prone states
gwd.plot_heatmap(top_n=15, from_year=1800, to_year=2026)

```

---

## Supported Views (`view` parameter)

The dataset interface supports multiple analytical views via the `gwd(...)` call:

* **`"long"`**: Standard tidy DataFrame containing columns: `Country`, `Year`, `War_State`, and `Active_Conflicts`.
* **`"binary"`**: Wide pivot table with countries as rows, years as columns, and binary values (`1` = at war, `0` = peace).
* **`"names"`**: Wide pivot table containing the literal names of active conflicts per year.
* **`"list"`**: Alphabetical list of all unique conflict names extracted under current filters.
* **`"countries"` / `"conflicts"**`: Sorted lists of active entities or unique conflicts matching the query criteria.

---

## Citation

If you use **HistWarDB** in your academic research, working paper, or data science project, please cite it using the following format:

```bibtex
@software{histwardb2026,
  author = {Vincenzo Manto},
  title = {HistWarDB: A Dynamic Wikidata-Based Historical War Dataset},
  year = {2026},
  url = {[https://github.com/VincenzoManto/histwardb](https://github.com/VincenzoManto/histwardb)}
}

```

---

## License

Distributed under the **MIT License**. See `LICENSE` for more information.


Stai preparando la repository su GitHub per caricarlo ufficialmente?

```
