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

# Duration heuristic applied when a conflict has no recorded end date
# (Wikidata P582). Conflicts starting before ``HEURISTIC_CUTOFF_YEAR`` are
# assumed to have lasted ``PRE_CUTOFF_DEFAULT_DURATION_YEARS`` years;
# conflicts starting at or after it are assumed ongoing through the
# dataset's ``end_year``. Both numbers are deliberately named constants
# (rather than inline literals) so callers can see and override them --
# they are rough estimates, not ground truth, and every record built from
# them is flagged via the ``Duration_Estimated`` column instead of being
# silently indistinguishable from a sourced end date.
HEURISTIC_CUTOFF_YEAR = 1800
PRE_CUTOFF_DEFAULT_DURATION_YEARS = 5

VALID_VIEWS = ("long", "binary", "names", "list", "countries", "conflicts", "records")

_YEAR_RE = re.compile(r"([+-]?\d+)")
_DATE_RE = re.compile(r"([+-]?\d+)-(\d{2})-(\d{2})")

# Wikidata ``wikibase:timePrecision`` values: 11 = day, 10 = month, 9 = year,
# anything coarser (decade/century/...) is treated as year-only.
TIME_PRECISION_DAY = 11
TIME_PRECISION_MONTH = 10


def parse_year(date_str: Optional[str]) -> Optional[int]:
    """Extract the leading year integer from a Wikidata date literal.

    Returns ``None`` when the input is falsy/not a string or has no digits.
    """
    if not date_str or not isinstance(date_str, str):
        return None
    match = _YEAR_RE.search(date_str)
    return int(match.group(1)) if match else None


def parse_date_parts(
    date_str: Optional[str], precision: Optional[int]
) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Split a Wikidata date literal into ``(year, month, day)`` per its real precision.

    Wikidata always serializes a full ``+YYYY-MM-DDTHH:MM:SSZ`` value even
    when the underlying statement only asserts a year or a month -- the
    unused components are filled with ``01``. ``precision`` (the statement's
    ``wikibase:timePrecision``) is what actually tells us which components
    are real: 11 = day, 10 = month, 9 or coarser = year only. ``None``
    (no precision available, e.g. hand-built test fixtures) is treated as
    day precision for backward compatibility.
    """
    year = parse_year(date_str)
    if year is None:
        return None, None, None

    if precision is not None and precision <= TIME_PRECISION_MONTH - 1:
        return year, None, None

    match = _DATE_RE.search(date_str)
    if not match:
        return year, None, None
    month = int(match.group(2))

    if precision is not None and precision < TIME_PRECISION_DAY:
        return year, month, None

    day = int(match.group(3))
    return year, month, day


def _as_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_bindings(
    bindings: Iterable[dict],
    end_year: int,
    heuristic_cutoff_year: int = HEURISTIC_CUTOFF_YEAR,
    pre_cutoff_default_duration_years: int = PRE_CUTOFF_DEFAULT_DURATION_YEARS,
) -> list[dict]:
    """Turn raw SPARQL JSON ``bindings`` into flat conflict/country records.

    When a conflict has no recorded end date, its duration is estimated
    (see module-level ``HEURISTIC_CUTOFF_YEAR`` /
    ``PRE_CUTOFF_DEFAULT_DURATION_YEARS`` for the rule); such records are
    flagged via ``duration_estimated=True`` rather than silently presented
    as sourced data.
    """
    records: list[dict] = []
    for item in bindings:
        country = item["countryLabel"]["value"]
        conflict = item["conflictLabel"]["value"]

        start_precision = _as_int(item.get("startDatePrecision", {}).get("value"))
        start_year, start_month, start_day = parse_date_parts(
            item["startDate"]["value"], start_precision
        )

        raw_end = item["endDate"]["value"] if "endDate" in item else None
        end_precision = _as_int(item.get("endDatePrecision", {}).get("value"))
        finish_year, finish_month, finish_day = (
            parse_date_parts(raw_end, end_precision) if raw_end else (None, None, None)
        )

        if start_year is None:
            continue

        duration_estimated = finish_year is None
        if finish_year is None:
            finish_year = (
                end_year
                if start_year >= heuristic_cutoff_year
                else (start_year + pre_cutoff_default_duration_years)
            )
            finish_month = finish_day = None
        elif finish_year < start_year:
            # A data-entry error produced an end before the start; clamp the
            # year but drop the now-meaningless month/day of that end date.
            finish_year = start_year
            finish_month = finish_day = None

        country_start = parse_year(item["countryStart"]["value"]) if "countryStart" in item else None
        if "countryEnd" in item:
            country_end = parse_year(item["countryEnd"]["value"])
        elif "countryEndFallback" in item:
            country_end = parse_year(item["countryEndFallback"]["value"])
        else:
            country_end = None

        casualties = None
        if "casualties" in item:
            try:
                casualties = float(item["casualties"]["value"])
            except (TypeError, ValueError):
                casualties = None

        records.append(
            {
                "country": country,
                "conflict_name": conflict,
                "start_year": start_year,
                "start_month": start_month,
                "start_day": start_day,
                "end_year": finish_year,
                "end_month": finish_month,
                "end_day": finish_day,
                "country_start": country_start,
                "country_end": country_end,
                "casualties": casualties,
                "duration_estimated": duration_estimated,
            }
        )
    return records


def _is_forced_dead(row: pd.Series) -> bool:
    dead_year = FORCED_MAX_YEARS.get(row["country"])
    if dead_year is None:
        return False
    return row["start_year"] > dead_year or row["end_year"] > dead_year


def clean_dataframe(
    df_raw: pd.DataFrame,
    start_year: int,
    end_year: int,
    min_casualties: Optional[int] = None,
) -> pd.DataFrame:
    """Apply anachronism, chronological, and severity sanity filters to raw records.

    Pure function: no network access, safe to unit test with synthetic
    DataFrames.

    Parameters
    ----------
    min_casualties:
        When set, conflicts with a *recorded* death toll (Wikidata P1120)
        below this value are dropped -- e.g. to exclude minor skirmishes
        from a dataset meant to track macro-level conflicts. Conflicts
        with no recorded death toll are kept regardless, since Wikidata's
        coverage of casualty figures is too sparse to treat "unknown" as
        "zero".
    """
    if df_raw.empty:
        return df_raw

    df = df_raw.drop_duplicates()
    df = df[~df["country"].str.contains(r"^Q\d+$", na=False)]
    if df.empty:
        return df

    if min_casualties is not None and "casualties" in df.columns:
        casualties = df["casualties"].astype("Float64")
        df = df[casualties.isna() | (casualties >= min_casualties)]
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


def _month_key(year, month):
    """Sortable (Year, Month) -> single comparable key. Works on scalars or Series."""
    return year * 12 + (month - 1)


def _parse_period(value: Union[str, int], default_month: int) -> tuple[int, Optional[int]]:
    """Parse a ``from_date``/``to_date`` argument: ``"YYYY"``, ``"YYYY-MM"``, or an int year."""
    text = str(value)
    parts = text.split("-")
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 and parts[1] else None
    return year, (month if month is not None else default_month)


def _period_label(years: pd.Series, months: pd.Series) -> pd.Series:
    """Format (Year, Month) columns as sortable ``"YYYY-MM"`` labels."""
    return years.astype(int).astype(str).str.zfill(4) + "-" + months.astype(int).astype(str).str.zfill(2)


def build_long_grid(df_clean: pd.DataFrame, start_year: int, end_year: int) -> pd.DataFrame:
    """Build the dense Country x Year x Month long-format grid from cleaned records.

    Month resolution uses the real month/day precision captured from
    Wikidata when a record has it (``start_month``/``end_month``); when a
    record only carries year precision, it is treated as covering the whole
    year (months 1-12) rather than fabricating a specific month.
    """
    countries = sorted(df_clean["country"].unique()) if not df_clean.empty else []
    years = range(start_year, end_year + 1)
    months = range(1, 13)

    grid = pd.DataFrame(
        [
            {
                "Country": c,
                "Year": y,
                "Month": m,
                "War_State": 0,
                "Active_Conflicts": "",
                "Duration_Estimated": False,
            }
            for c in countries
            for y in years
            for m in months
        ]
    )
    if grid.empty:
        return grid

    grid = grid.set_index(["Country", "Year", "Month"])

    def _month_or(row: pd.Series, col: str, default: int) -> int:
        value = row.get(col)
        return default if value is None or pd.isna(value) else int(value)

    for _, row in df_clean.iterrows():
        c = row["country"]
        start_k = max(
            _month_key(row["start_year"], _month_or(row, "start_month", 1)), _month_key(start_year, 1)
        )
        end_k = min(_month_key(row["end_year"], _month_or(row, "end_month", 12)), _month_key(end_year, 12))
        name = row["conflict_name"]
        estimated = bool(row.get("duration_estimated", False))

        for k in range(start_k, end_k + 1):
            y, m = divmod(k, 12)
            m += 1
            key = (c, y, m)
            if key not in grid.index:
                continue
            grid.at[key, "War_State"] = 1
            existing = grid.at[key, "Active_Conflicts"]
            grid.at[key, "Active_Conflicts"] = f"{existing}; {name}".strip("; ") if existing else name
            if estimated:
                grid.at[key, "Duration_Estimated"] = True

    return grid.reset_index()


def build_records_view(df_clean: pd.DataFrame) -> pd.DataFrame:
    """Build the one-row-per-conflict records table with explicit date-part columns."""
    columns = [
        "Country",
        "Conflict_Name",
        "Start_Year",
        "Start_Month",
        "Start_Day",
        "End_Year",
        "End_Month",
        "End_Day",
        "Duration_Estimated",
        "Casualties",
    ]
    if df_clean.empty:
        return pd.DataFrame(columns=columns)

    records = df_clean.rename(
        columns={
            "country": "Country",
            "conflict_name": "Conflict_Name",
            "start_year": "Start_Year",
            "start_month": "Start_Month",
            "start_day": "Start_Day",
            "end_year": "End_Year",
            "end_month": "End_Month",
            "end_day": "End_Day",
            "duration_estimated": "Duration_Estimated",
            "casualties": "Casualties",
        }
    )
    for col in columns:
        if col not in records.columns:
            records[col] = None
    return records[columns].reset_index(drop=True)


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
    min_casualties:
        Optional minimum recorded death toll (Wikidata P1120). Conflicts
        with a recorded toll below this value are excluded, which is the
        knob for not treating a two-day skirmish the same as a world war.
        Conflicts with no recorded toll are kept (``None`` by default:
        no severity filtering, matching prior behavior).
    heuristic_cutoff_year, pre_cutoff_duration_years:
        Tune the fallback applied when a conflict has no recorded end
        date: conflicts starting before ``heuristic_cutoff_year`` are
        assumed to last ``pre_cutoff_duration_years`` years; conflicts
        starting on/after it are assumed to run through ``end_year``.
        Records built from this fallback are flagged in the
        ``Duration_Estimated`` column of the resulting grid.
    """

    def __init__(
        self,
        start_year: int = 1700,
        end_year: Optional[int] = None,
        chunk_size: int = 25,
        session: Optional[requests.Session] = None,
        auto_fetch: bool = True,
        min_casualties: Optional[int] = None,
        heuristic_cutoff_year: int = HEURISTIC_CUTOFF_YEAR,
        pre_cutoff_duration_years: int = PRE_CUTOFF_DEFAULT_DURATION_YEARS,
    ) -> None:
        self.start_year = start_year
        self.end_year = (
            end_year if end_year is not None else datetime.datetime.now(tz=datetime.timezone.utc).year
        )
        self.chunk_size = chunk_size
        self.session = session or requests
        self.min_casualties = min_casualties
        self.heuristic_cutoff_year = heuristic_cutoff_year
        self.pre_cutoff_duration_years = pre_cutoff_duration_years
        self.df_long: Optional[pd.DataFrame] = None
        self.df_records: Optional[pd.DataFrame] = None

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
            # The query filter's upper bound is exclusive; the last chunk must
            # include wars starting in end_year itself, not just before it.
            query_end = chunk_end + 1 if chunk_end >= self.end_year else chunk_end
            try:
                bindings = self._fetch_chunk(chunk_start, query_end)
                all_records.extend(
                    parse_bindings(
                        bindings,
                        self.end_year,
                        heuristic_cutoff_year=self.heuristic_cutoff_year,
                        pre_cutoff_default_duration_years=self.pre_cutoff_duration_years,
                    )
                )
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
            self.df_long = pd.DataFrame(
                columns=["Country", "Year", "Month", "War_State", "Active_Conflicts", "Duration_Estimated"]
            )
            self.df_records = build_records_view(pd.DataFrame())
            return

        df_raw = pd.DataFrame(records)
        df_clean = clean_dataframe(
            df_raw, self.start_year, self.end_year, min_casualties=self.min_casualties
        )
        self.df_long = build_long_grid(df_clean, self.start_year, self.end_year)
        self.df_records = build_records_view(df_clean)

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

        if view == "records":
            return self._query_records(countries, from_date, to_date, keyword)

        df = self.df_long.copy()

        if countries:
            c_list = [countries] if isinstance(countries, str) else list(countries)
            wanted = {c.casefold() for c in c_list}
            df = df[df["Country"].str.casefold().isin(wanted)]

        if from_date is not None:
            from_y, from_m = _parse_period(from_date, default_month=1)
            df = df[_month_key(df["Year"], df["Month"]) >= _month_key(from_y, from_m)]
        if to_date is not None:
            to_y, to_m = _parse_period(to_date, default_month=12)
            df = df[_month_key(df["Year"], df["Month"]) <= _month_key(to_y, to_m)]
        if only_in_war:
            df = df[df["War_State"] == 1]
        if keyword:
            df = df[df["Active_Conflicts"].str.contains(keyword, case=False, na=False, regex=False)]

        return self._render_view(df, view)

    def _query_records(
        self,
        countries: Optional[Union[str, Sequence[str]]],
        from_date: Optional[Union[str, int]],
        to_date: Optional[Union[str, int]],
        keyword: Optional[str],
    ) -> pd.DataFrame:
        df = self.df_records.copy()

        if countries:
            c_list = [countries] if isinstance(countries, str) else list(countries)
            wanted = {c.casefold() for c in c_list}
            df = df[df["Country"].str.casefold().isin(wanted)]

        if from_date is not None:
            from_y, from_m = _parse_period(from_date, default_month=1)
            from_k = _month_key(from_y, from_m or 1)
            end_k = _month_key(df["End_Year"], df["End_Month"].fillna(12))
            df = df[end_k >= from_k]
        if to_date is not None:
            to_y, to_m = _parse_period(to_date, default_month=12)
            to_k = _month_key(to_y, to_m or 12)
            start_k = _month_key(df["Start_Year"], df["Start_Month"].fillna(1))
            df = df[start_k <= to_k]
        if keyword:
            df = df[df["Conflict_Name"].str.contains(keyword, case=False, na=False, regex=False)]

        return df.reset_index(drop=True)

    @staticmethod
    def _render_view(df: pd.DataFrame, view: str):
        if view == "long":
            return df
        if view == "binary":
            return (
                df.assign(Period=_period_label(df["Year"], df["Month"]))
                .pivot(index="Country", columns="Period", values="War_State")
                .fillna(0)
                .astype(int)
            )
        if view == "names":
            return (
                df.assign(Period=_period_label(df["Year"], df["Month"]))
                .pivot(index="Country", columns="Period", values="Active_Conflicts")
                .fillna("")
            )
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

        df_range = self.df_long[
            (self.df_long["Year"] >= from_year) & (self.df_long["Year"] <= to_year)
        ].assign(Period=lambda d: _period_label(d["Year"], d["Month"]))
        top_countries = df_range.groupby("Country")["War_State"].sum().nlargest(top_n).index.tolist()
        df_filtered = df_range[df_range["Country"].isin(top_countries)]

        matrix_val = df_filtered.pivot(index="Country", columns="Period", values="War_State").fillna(0)
        matrix_txt = df_filtered.pivot(index="Country", columns="Period", values="Active_Conflicts").fillna(
            "No recorded conflicts"
        )

        fig = go.Figure(
            data=go.Heatmap(
                z=matrix_val.values,
                x=matrix_val.columns,
                y=matrix_val.index,
                text=matrix_txt.values,
                hovertemplate="<b>Country:</b> %{y}<br><b>Period:</b> %{x}<br><b>Conflict(s):</b> %{text}<extra></extra>",
                colorscale=[[0, "#ffffff"], [1, "#d9534f"]],
                showscale=False,
            )
        )
        fig.update_layout(
            title=f"Global War State ({from_year}-{to_year})",
            xaxis_title="Year-Month",
            yaxis_title="Country / Historical State",
            xaxis={'dtick': 120},
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
