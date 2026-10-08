"""Core dataset class for HistWarDB.

The design deliberately separates three concerns so each can be tested in
isolation:

1. Network I/O (:meth:`HistWarDB._fetch_chunk`) -- talks to Wikidata.
2. Pure parsing/filtering (:func:`parse_bindings`, :func:`clean_dataframe`)
   -- no I/O, fully unit-testable with static fixtures.
3. Grid construction and the analytical ``__call__`` interface.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Iterable, Sequence
from typing import Optional, Union

import pandas as pd
import requests

from .query import DEFAULT_USER_AGENT, WIKIDATA_SPARQL_ENDPOINT, build_sparql_query

# Historical states whose real-world dissolution year is not reliably
# captured by Wikidata's P576/P582 properties. Used to discard anachronistic
# records (e.g. a "German Empire" war recorded after 1918).
FORCED_MAX_YEARS = {
    "German Empire": 1918,
    "Qing dynasty": 1912,
    "Kingdom of Montenegro": 1918,
    "Principality of Montenegro": 1910,
    "Russian Soviet Federative Socialist Republic": 1991,
}

VALID_VIEWS = ("long", "binary", "names", "list", "countries", "conflicts")

_YEAR_RE = re.compile(r"([+-]?\d+)")


def parse_year(date_str: Optional[str]) -> Optional[int]:
    """Extract the leading year integer from a Wikidata date literal.

    Returns ``None`` when the input is falsy/not a string or has no digits.
    """
    if not date_str or not isinstance(date_str, str):
        return None
    match = _YEAR_RE.search(date_str)
    return int(match.group(1)) if match else None


def parse_bindings(bindings: Iterable[dict], end_year: int) -> list[dict]:
    """Turn raw SPARQL JSON ``bindings`` into flat conflict/country records."""
    records: list[dict] = []
    for item in bindings:
        country = item["countryLabel"]["value"]
        conflict = item["conflictLabel"]["value"]

        start_year = parse_year(item["startDate"]["value"])
        raw_end = item["endDate"]["value"] if "endDate" in item else None
        finish_year = parse_year(raw_end) if raw_end else None

        if start_year is None:
            continue

        if finish_year is None:
            finish_year = end_year if start_year >= 1800 else (start_year + 5)
        else:
            finish_year = max(finish_year, start_year)

        country_start = parse_year(item["countryStart"]["value"]) if "countryStart" in item else None
        if "countryEnd" in item:
            country_end = parse_year(item["countryEnd"]["value"])
        elif "countryEndFallback" in item:
            country_end = parse_year(item["countryEndFallback"]["value"])
        else:
            country_end = None

        records.append(
            {
                "country": country,
                "conflict_name": conflict,
                "start_year": start_year,
                "end_year": finish_year,
                "country_start": country_start,
                "country_end": country_end,
            }
        )
    return records


def _is_forced_dead(row: pd.Series) -> bool:
    dead_year = FORCED_MAX_YEARS.get(row["country"])
    if dead_year is None:
        return False
    return row["start_year"] > dead_year or row["end_year"] > dead_year


def clean_dataframe(df_raw: pd.DataFrame, start_year: int, end_year: int) -> pd.DataFrame:
    """Apply anachronism and chronological sanity filters to raw records.

    Pure function: no network access, safe to unit test with synthetic
    DataFrames.
    """
    if df_raw.empty:
        return df_raw

    df = df_raw.drop_duplicates()
    df = df[~df["country"].str.contains(r"^Q\d+$", na=False)]
    if df.empty:
        return df

    # Drop records for historical states active after their real dissolution.
    df = df[~df.apply(_is_forced_dead, axis=1)]
    if df.empty:
        return df

    # Discard wars entirely outside the requested [start_year, end_year] window.
    df = df[(df["end_year"] >= start_year) & (df["start_year"] <= end_year)]

    # State lifespan consistency: a war can't predate/postdate the state,
    # when Wikidata provides those dates. Compare via nullable Float64 so
    # NaN comparisons resolve to False instead of raising (pandas evaluates
    # both sides of `|` eagerly, so a plain None/int comparison would blow
    # up even behind an `isna()` guard).
    country_start = df["country_start"].astype("Float64")
    country_end = df["country_end"].astype("Float64")
    end_year_f = df["end_year"].astype("Float64")
    start_year_f = df["start_year"].astype("Float64")

    consistent_start = (country_start.isna() | (end_year_f >= country_start)).fillna(False)
    consistent_end = (country_end.isna() | (start_year_f <= country_end)).fillna(False)
    df = df[consistent_start & consistent_end]

    return df


def build_long_grid(df_clean: pd.DataFrame, start_year: int, end_year: int) -> pd.DataFrame:
    """Build the dense Country x Year long-format grid from cleaned records."""
    countries = sorted(df_clean["country"].unique()) if not df_clean.empty else []
    years = range(start_year, end_year + 1)

    grid = pd.DataFrame(
        [
            {"Country": c, "Year": y, "War_State": 0, "Active_Conflicts": ""}
            for c in countries
            for y in years
        ]
    )
    if grid.empty:
        return grid

    grid = grid.set_index(["Country", "Year"])

    for _, row in df_clean.iterrows():
        c = row["country"]
        in_y = max(row["start_year"], start_year)
        fi_y = min(row["end_year"], end_year)
        name = row["conflict_name"]

        for y in range(in_y, fi_y + 1):
            key = (c, y)
            if key not in grid.index:
                continue
            grid.at[key, "War_State"] = 1
            existing = grid.at[key, "Active_Conflicts"]
            grid.at[key, "Active_Conflicts"] = f"{existing}; {name}".strip("; ") if existing else name

    return grid.reset_index()


class HistWarDB:
    """Dynamic, programmatic historical war dataset built on top of Wikidata.

    Supports sovereign and historical states, anti-noise filtering of
    non-conflict events, keyword search, and multiple analytical views.

    Parameters
    ----------
    start_year, end_year:
        Inclusive temporal window of the dataset. ``end_year`` defaults to
        the current year.
    chunk_size:
        Size (in years) of the temporal windows used to page around
        Wikidata's query result limits.
    session:
        Optional :class:`requests.Session`-like object used for HTTP calls;
        override in tests to avoid real network access.
    auto_fetch:
        When ``True`` (default), data is fetched immediately on
        construction. Set to ``False`` to construct an empty instance and
        call :meth:`fetch_data` (or :meth:`load_records`) manually -- handy
        for tests and offline usage.
    """

    def __init__(
        self,
        start_year: int = 1700,
        end_year: Optional[int] = None,
        chunk_size: int = 25,
        session: Optional[requests.Session] = None,
        auto_fetch: bool = True,
    ) -> None:
        self.start_year = start_year
        self.end_year = end_year if end_year else datetime.datetime.now(tz=datetime.timezone.utc).year
        self.chunk_size = chunk_size
        self.session = session or requests
        self.df_long: Optional[pd.DataFrame] = None

        if auto_fetch:
            self.fetch_data()

    # -- Network layer -----------------------------------------------------

    def _fetch_chunk(self, chunk_start: int, chunk_end: int) -> list[dict]:
        """Fetch and parse a single temporal chunk. Returns raw bindings."""
        query = build_sparql_query(chunk_start, chunk_end, self.end_year)
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        response = self.session.get(
            WIKIDATA_SPARQL_ENDPOINT,
            params={"query": query, "format": "json"},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["results"]["bindings"]

    def fetch_data(self) -> None:
        """Fetch all chunks from Wikidata and build the long-format grid."""
        all_records: list[dict] = []
        for chunk_start in range(self.start_year, self.end_year, self.chunk_size):
            chunk_end = min(chunk_start + self.chunk_size, self.end_year)
            try:
                bindings = self._fetch_chunk(chunk_start, chunk_end)
                all_records.extend(parse_bindings(bindings, self.end_year))
            except requests.RequestException as exc:
                print(f"Block {chunk_start}-{chunk_end} temporarily skipped: {exc}")
                continue

        self.load_records(all_records)

    def load_records(self, records: list[dict]) -> None:
        """Build the dataset from a list of flat conflict/country records.

        Bypasses the network layer entirely -- used by :meth:`fetch_data`
        and directly in tests/offline pipelines.
        """
        if not records:
            self.df_long = pd.DataFrame(columns=["Country", "Year", "War_State", "Active_Conflicts"])
            return

        df_raw = pd.DataFrame(records)
        df_clean = clean_dataframe(df_raw, self.start_year, self.end_year)
        self.df_long = build_long_grid(df_clean, self.start_year, self.end_year)

    # -- Query interface -----------------------------------------------------

    def __call__(
        self,
        countries: Optional[Union[str, Sequence[str]]] = None,
        view: str = "long",
        from_date: Optional[Union[str, int]] = None,
        to_date: Optional[Union[str, int]] = None,
        only_in_war: bool = False,
        keyword: Optional[str] = None,
    ):
        """Query the dataset. See module docstring for supported ``view`` values."""
        if self.df_long is None:
            raise ValueError("Dataset not initialized. Call fetch_data() or load_records() first.")
        if view not in VALID_VIEWS:
            raise ValueError(f"Invalid view {view!r}. Choose from: {', '.join(VALID_VIEWS)}")

        df = self.df_long.copy()

        if countries:
            c_list = [countries] if isinstance(countries, str) else list(countries)
            regex_pattern = "|".join(re.escape(c) for c in c_list)
            df = df[df["Country"].str.contains(regex_pattern, case=False, na=False)]

        if from_date is not None:
            df = df[df["Year"] >= int(str(from_date).split("-")[0])]
        if to_date is not None:
            df = df[df["Year"] <= int(str(to_date).split("-")[0])]
        if only_in_war:
            df = df[df["War_State"] == 1]
        if keyword:
            df = df[df["Active_Conflicts"].str.contains(keyword, case=False, na=False)]

        return self._render_view(df, view)

    @staticmethod
    def _render_view(df: pd.DataFrame, view: str):
        if view == "long":
            return df
        if view == "binary":
            return df.pivot(index="Country", columns="Year", values="War_State").fillna(0).astype(int)
        if view == "names":
            return df.pivot(index="Country", columns="Year", values="Active_Conflicts").fillna("")
        if view == "list":
            return sorted({item for s in df["Active_Conflicts"].dropna() for item in s.split("; ") if item})
        if view == "countries":
            active_df = df[df["War_State"] == 1]
            return sorted(active_df["Country"].unique())
        if view == "conflicts":
            active_df = df[df["War_State"] == 1]
            unique_conflicts = set()
            for s in active_df["Active_Conflicts"].dropna():
                for item in s.split("; "):
                    if item:
                        unique_conflicts.add(item)
            return sorted(unique_conflicts)
        raise AssertionError(f"Unhandled view: {view}")  # pragma: no cover

    @property
    def available_countries(self) -> list[str]:
        if self.df_long is None or self.df_long.empty:
            return []
        return sorted(self.df_long["Country"].unique())

    # -- Visualization / export ---------------------------------------------

    def plot_heatmap(self, top_n: int = 20, from_year: Optional[int] = None, to_year: Optional[int] = None):
        """Build (and return) a Plotly heatmap figure of war intensity over time.

        Does not call ``.show()`` so it can be used headlessly / tested.
        """
        import plotly.graph_objects as go

        from_year = self.start_year if from_year is None else from_year
        to_year = self.end_year if to_year is None else to_year

        df_range = self.df_long[(self.df_long["Year"] >= from_year) & (self.df_long["Year"] <= to_year)]
        top_countries = df_range.groupby("Country")["War_State"].sum().nlargest(top_n).index.tolist()
        df_filtered = df_range[df_range["Country"].isin(top_countries)]

        matrix_val = df_filtered.pivot(index="Country", columns="Year", values="War_State").fillna(0)
        matrix_txt = df_filtered.pivot(index="Country", columns="Year", values="Active_Conflicts").fillna(
            "No recorded conflicts"
        )

        fig = go.Figure(
            data=go.Heatmap(
                z=matrix_val.values,
                x=matrix_val.columns,
                y=matrix_val.index,
                text=matrix_txt.values,
                hovertemplate="<b>Country:</b> %{y}<br><b>Year:</b> %{x}<br><b>Conflict(s):</b> %{text}<extra></extra>",
                colorscale=[[0, "#ffffff"], [1, "#d9534f"]],
                showscale=False,
            )
        )
        fig.update_layout(
            title=f"Global War State from Wikidata (Sovereign + Historical States) ({from_year}-{to_year})",
            xaxis_title="Year",
            yaxis_title="Country / Historical State",
            xaxis={'dtick': 10},
            height=700,
        )
        return fig

    def export_csv(self, query_result: pd.DataFrame, filename: str = "histwardb_export.csv") -> str:
        """Export a query result to CSV and return the written path.

        In a Colab environment, also triggers a browser download.
        """
        query_result.to_csv(filename)
        try:
            from google.colab import files  # type: ignore

            files.download(filename)
        except ImportError:
            pass
        return filename
