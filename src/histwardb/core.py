"""Core dataset class for HistWarDB.

The design deliberately separates three concerns so each can be tested in
isolation:

1. Network I/O (:meth:`HistWarDB._request`) -- talks to Wikidata.
2. Pure parsing/filtering (:func:`parse_bindings`, :func:`build_records`,
   :func:`clean_dataframe`) -- no I/O, fully unit-testable with static
   fixtures.
3. Grid construction and the analytical ``__call__`` interface.
"""

from __future__ import annotations

import datetime
import difflib
import hashlib
import json
import re
import time
import warnings
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Optional, Union

import pandas as pd
import requests

from .query import (
    CONFLICT_CLASS,
    COUNTRY_TYPES,
    DEFAULT_USER_AGENT,
    EXCLUDED_CONFLICT_CLASSES,
    QUERY_LIMIT,
    WIKIDATA_SPARQL_ENDPOINT,
    build_entity_query,
    build_sparql_query,
)

# Manual upper bounds on a historical state's lifespan, applied on top of
# Wikidata's own P576/P582 dates (the earlier of the two wins). Only needed
# where Wikidata has no usable dissolution date; participations are clipped
# to the bound, not dropped.
FORCED_MAX_YEARS = {
    "German Empire": 1918,
    "Qing dynasty": 1912,
    "Kingdom of Montenegro": 1918,
    "Principality of Montenegro": 1910,
    "Russian Soviet Federative Socialist Republic": 1991,
}

VALID_VIEWS = ("long", "binary", "names", "list", "countries", "conflicts")

# How a conflict with no recorded end date (P582) is handled:
#   "heuristic"  -- ongoing to the dataset end if it started in 1800 or
#                   later, otherwise assumed to last 5 years (legacy rule);
#   "open"       -- ongoing to the dataset end, whatever the start year;
#   "start_only" -- counted for its start year only;
#   "drop"       -- discarded.
# Whatever the policy, imputed records are flagged (``end_imputed``).
MISSING_END_POLICIES = ("heuristic", "open", "start_only", "drop")

# QIDs per entity query; keeps the GET URL well under server limits.
ENTITY_BATCH_SIZE = 150

RECORD_COLUMNS = [
    "country",
    "country_qid",
    "iso3",
    "conflict_name",
    "conflict_qid",
    "start_year",
    "end_year",
    "war_start_year",
    "war_end_year",
    "end_imputed",
    "country_start",
    "country_end",
]

LONG_COLUMNS = [
    "Country",
    "Year",
    "War_State",
    "Active_Conflicts",
    "Country_QID",
    "ISO3",
    "State_Exists",
    "War_Imputed",
]

SNAPSHOT_DATA_FILE = "participations.csv"
SNAPSHOT_METADATA_FILE = "metadata.json"

_YEAR_RE = re.compile(r"([+-]?\d+)")
_QID_RE = re.compile(r"^Q\d+$")


class FetchError(RuntimeError):
    """A Wikidata query failed after all retries, or returned truncated data."""


def parse_year(date_str: Optional[str]) -> Optional[int]:
    """Extract the leading year integer from a Wikidata date literal.

    Returns ``None`` when the input is falsy/not a string or has no digits.
    """
    if not date_str or not isinstance(date_str, str):
        return None
    match = _YEAR_RE.search(date_str)
    return int(match.group(1)) if match else None


def qid_from_uri(uri: Optional[str]) -> Optional[str]:
    """Return the bare QID from a Wikidata entity URI (``.../entity/Q142``)."""
    if not uri or not isinstance(uri, str):
        return None
    return uri.rsplit("/", 1)[-1]


def impute_end_year(start_year: int, dataset_end_year: int, missing_end: str = "heuristic") -> Optional[int]:
    """End year assigned to a conflict that has no recorded end date.

    Returns ``None`` when the policy is to drop such conflicts.
    """
    if missing_end not in MISSING_END_POLICIES:
        raise ValueError(f"Invalid missing_end {missing_end!r}. Choose from: {', '.join(MISSING_END_POLICIES)}")
    if missing_end == "drop":
        return None
    if missing_end == "start_only":
        return start_year
    if missing_end == "open":
        return dataset_end_year
    return dataset_end_year if start_year >= 1800 else (start_year + 5)


def _make_record(
    *,
    country: str,
    country_qid: Optional[str],
    iso3: Optional[str],
    conflict: str,
    conflict_qid: Optional[str],
    start_year: Optional[int],
    finish_year: Optional[int],
    country_start: Optional[int],
    country_end: Optional[int],
    dataset_end_year: int,
    missing_end: str,
) -> Optional[dict]:
    if start_year is None:
        return None

    end_imputed = finish_year is None
    if end_imputed:
        finish_year = impute_end_year(start_year, dataset_end_year, missing_end)
        if finish_year is None:
            return None
    else:
        finish_year = max(finish_year, start_year)

    return {
        "country": country,
        "country_qid": country_qid,
        "iso3": iso3,
        "conflict_name": conflict,
        "conflict_qid": conflict_qid,
        "start_year": start_year,
        "end_year": finish_year,
        "end_imputed": end_imputed,
        "country_start": country_start,
        "country_end": country_end,
    }


def parse_bindings(bindings: Iterable[dict], end_year: int, missing_end: str = "heuristic") -> list[dict]:
    """Turn flat SPARQL JSON ``bindings`` into conflict/country records.

    "Flat" means one binding carries the labels and the country lifespan
    alongside the dates (``countryLabel``, ``conflictLabel``, ``startDate``,
    and optionally ``endDate``, ``countryStart``, ``countryEnd``,
    ``countryEndFallback``, ``country``, ``conflict``, ``iso3``).
    """
    records: list[dict] = []
    for item in bindings:
        raw_end = item["endDate"]["value"] if "endDate" in item else None
        if "countryEnd" in item:
            country_end = parse_year(item["countryEnd"]["value"])
        elif "countryEndFallback" in item:
            country_end = parse_year(item["countryEndFallback"]["value"])
        else:
            country_end = None

        record = _make_record(
            country=item["countryLabel"]["value"],
            country_qid=qid_from_uri(item["country"]["value"]) if "country" in item else None,
            iso3=item["iso3"]["value"] if "iso3" in item else None,
            conflict=item["conflictLabel"]["value"],
            conflict_qid=qid_from_uri(item["conflict"]["value"]) if "conflict" in item else None,
            start_year=parse_year(item["startDate"]["value"]),
            finish_year=parse_year(raw_end) if raw_end else None,
            country_start=parse_year(item["countryStart"]["value"]) if "countryStart" in item else None,
            country_end=country_end,
            dataset_end_year=end_year,
            missing_end=missing_end,
        )
        if record is not None:
            records.append(record)
    return records


def parse_entities(bindings: Iterable[dict]) -> dict[str, dict]:
    """Collapse entity-query bindings into one metadata dict per QID.

    Multi-valued properties are reduced deterministically: the earliest
    inception, the latest dissolution (``P576``, falling back to ``P582``
    only when no ``P576`` exists), and the alphabetically first ISO code.
    An entity with no English label is labelled with its QID.
    """
    raw: dict[str, dict] = {}
    for item in bindings:
        qid = qid_from_uri(item["item"]["value"])
        entry = raw.setdefault(qid, {"label": None, "starts": [], "ends": [], "fallback_ends": [], "iso3": []})
        if "itemLabel" in item and entry["label"] is None:
            entry["label"] = item["itemLabel"]["value"]
        for key, bucket in (("inception", "starts"), ("dissolved", "ends"), ("endTime", "fallback_ends")):
            year = parse_year(item[key]["value"]) if key in item else None
            if year is not None:
                entry[bucket].append(year)
        if "iso3" in item:
            entry["iso3"].append(item["iso3"]["value"])

    entities: dict[str, dict] = {}
    for qid, entry in raw.items():
        ends = entry["ends"] or entry["fallback_ends"]
        entities[qid] = {
            "label": entry["label"] or qid,
            "start": min(entry["starts"]) if entry["starts"] else None,
            "end": max(ends) if ends else None,
            "iso3": min(entry["iso3"]) if entry["iso3"] else None,
        }
    return entities


def build_records(
    participations: Iterable[dict],
    conflicts: dict[str, dict],
    countries: dict[str, dict],
    end_year: int,
    missing_end: str = "heuristic",
) -> list[dict]:
    """Join participation-query bindings with entity metadata into records."""
    records: list[dict] = []
    for item in participations:
        conflict_qid = qid_from_uri(item["conflict"]["value"])
        country_qid = qid_from_uri(item["country"]["value"])
        conflict = conflicts.get(conflict_qid, {})
        country = countries.get(country_qid, {})
        raw_end = item["endDate"]["value"] if "endDate" in item else None

        record = _make_record(
            country=country.get("label") or country_qid,
            country_qid=country_qid,
            iso3=country.get("iso3"),
            conflict=conflict.get("label") or conflict_qid,
            conflict_qid=conflict_qid,
            start_year=parse_year(item["startDate"]["value"]),
            finish_year=parse_year(raw_end) if raw_end else None,
            country_start=country.get("start"),
            country_end=country.get("end"),
            dataset_end_year=end_year,
            missing_end=missing_end,
        )
        if record is not None:
            records.append(record)
    return records


def _with_record_defaults(df: pd.DataFrame) -> pd.DataFrame:
    """Fill in the optional record columns so older 6-column records still work."""
    df = df.copy()
    for column, default in (("country_qid", None), ("conflict_qid", None), ("iso3", None), ("end_imputed", False)):
        if column not in df.columns:
            df[column] = default
    # Without a QID the label is the only identity available.
    df["country_qid"] = df["country_qid"].where(df["country_qid"].notna(), df["country"])
    df["conflict_qid"] = df["conflict_qid"].where(df["conflict_qid"].notna(), df["conflict_name"])
    df["end_imputed"] = df["end_imputed"].astype(bool)
    df["country_start"] = pd.to_numeric(df["country_start"], errors="coerce")
    df["country_end"] = pd.to_numeric(df["country_end"], errors="coerce")
    return df


def _disambiguate_labels(df: pd.DataFrame, label_col: str, qid_col: str) -> pd.DataFrame:
    """Append the QID to labels shared by more than one entity."""
    shared = df.groupby(label_col)[qid_col].transform("nunique") > 1
    if shared.any():
        df.loc[shared, label_col] = df.loc[shared, label_col] + " (" + df.loc[shared, qid_col].astype(str) + ")"
    return df


def clean_dataframe(df_raw: pd.DataFrame, start_year: int, end_year: int) -> pd.DataFrame:
    """Turn raw records into one clean row per (conflict, country) participation.

    Pure function: no network access, safe to unit test with synthetic
    DataFrames. Steps:

    1. drop countries whose label is an unresolved QID;
    2. reduce each country to one lifespan (earliest start, latest end,
       capped by :data:`FORCED_MAX_YEARS`);
    3. reduce each (conflict, country) pair to one interval;
    4. clip that interval to the country's lifespan and drop it if nothing
       is left;
    5. drop intervals entirely outside ``[start_year, end_year]``.

    ``start_year``/``end_year`` in the result are the clipped participation
    years; ``war_start_year``/``war_end_year`` keep the conflict's own dates.
    """
    if df_raw.empty:
        return df_raw

    df = _with_record_defaults(df_raw)
    df = df[~df["country"].astype(str).str.contains(_QID_RE.pattern, na=False)]
    if df.empty:
        return pd.DataFrame(columns=RECORD_COLUMNS)

    # One lifespan per country, even when Wikidata lists several dates.
    lifespans = df.groupby("country_qid").agg(
        country_start=("country_start", "min"),
        country_end=("country_end", "max"),
    )
    forced_end = df.groupby("country_qid")["country"].first().map(FORCED_MAX_YEARS).astype("float64")
    lifespans["country_end"] = pd.concat([lifespans["country_end"], forced_end], axis=1).min(axis=1)

    # One interval per participation. A recorded end date always beats an
    # imputed one.
    df = df.assign(_observed_end=df["end_year"].where(~df["end_imputed"]))
    parts = (
        df.groupby(["conflict_qid", "country_qid"], sort=True)
        .agg(
            country=("country", "first"),
            iso3=("iso3", "first"),
            conflict_name=("conflict_name", "first"),
            war_start_year=("start_year", "min"),
            _observed_end=("_observed_end", "max"),
            _any_end=("end_year", "max"),
            end_imputed=("end_imputed", "all"),
        )
        .reset_index()
    )
    parts["war_end_year"] = parts["_observed_end"].fillna(parts["_any_end"])
    parts = parts.join(lifespans, on="country_qid")

    # Clip to the state's lifespan: a state cannot be at war in a year in
    # which it did not exist. Missing lifespan data imposes no constraint.
    parts["start_year"] = parts[["war_start_year", "country_start"]].max(axis=1)
    parts["end_year"] = parts[["war_end_year", "country_end"]].min(axis=1)
    parts = parts[parts["start_year"] <= parts["end_year"]]

    # Discard participations entirely outside the requested window.
    parts = parts[(parts["end_year"] >= start_year) & (parts["start_year"] <= end_year)].copy()
    if parts.empty:
        return pd.DataFrame(columns=RECORD_COLUMNS)

    for column in ("start_year", "end_year", "war_start_year", "war_end_year"):
        parts[column] = parts[column].astype(int)

    parts = _disambiguate_labels(parts, "country", "country_qid")
    parts = _disambiguate_labels(parts, "conflict_name", "conflict_qid")

    return parts[RECORD_COLUMNS].sort_values(["country", "start_year", "conflict_name"]).reset_index(drop=True)


def build_long_grid(df_clean: pd.DataFrame, start_year: int, end_year: int) -> pd.DataFrame:
    """Build the dense Country x Year long-format grid from cleaned records.

    Besides ``War_State`` and ``Active_Conflicts`` each cell carries:

    * ``State_Exists`` -- 0 when the year is outside the state's known
      lifespan, so "no war" can be told apart from "no state";
    * ``War_Imputed`` -- 1 when the cell is at war only because a conflict
      with no recorded end date was extended past its start year.
    """
    if df_clean.empty:
        return pd.DataFrame(columns=LONG_COLUMNS)

    active: dict[tuple, set] = {}
    observed: set[tuple] = set()
    for row in df_clean.itertuples(index=False):
        first = max(row.start_year, start_year)
        last = min(row.end_year, end_year)
        for year in range(first, last + 1):
            key = (row.country_qid, year)
            active.setdefault(key, set()).add(row.conflict_name)
            if not row.end_imputed or year == row.war_start_year:
                observed.add(key)

    countries = df_clean.drop_duplicates("country_qid").sort_values("country")
    rows = []
    for country in countries.itertuples(index=False):
        born = country.country_start
        died = country.country_end
        for year in range(start_year, end_year + 1):
            key = (country.country_qid, year)
            names = active.get(key)
            exists = (pd.isna(born) or year >= born) and (pd.isna(died) or year <= died)
            rows.append(
                {
                    "Country": country.country,
                    "Year": year,
                    "War_State": 1 if names else 0,
                    "Active_Conflicts": "; ".join(sorted(names)) if names else "",
                    "Country_QID": country.country_qid,
                    "ISO3": country.iso3,
                    "State_Exists": 1 if exists else 0,
                    "War_Imputed": 1 if names and key not in observed else 0,
                }
            )

    return pd.DataFrame(rows, columns=LONG_COLUMNS)


def _sha256_normalized(path: Path) -> str:
    """SHA-256 of a text file with line endings normalised to LF.

    Normalising keeps the checksum stable across git's CRLF conversion.
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


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
    missing_end:
        Policy for conflicts with no recorded end date; one of
        :data:`MISSING_END_POLICIES`.
    strict:
        When ``True`` (default) a chunk that still fails after all retries
        raises :class:`FetchError`. When ``False`` the chunk is skipped with
        a warning and recorded in :attr:`failed_chunks`, so an incomplete
        dataset is never mistaken for a complete one.
    max_retries, retry_backoff:
        Number of retries per query and the base delay (seconds) of the
        exponential backoff between them. A ``Retry-After`` header is
        honoured.
    timeout:
        Per-request timeout in seconds.
    request_pause:
        Pause (seconds) between consecutive queries, to stay under the
        public endpoint's rate limit.
    """

    def __init__(
        self,
        start_year: int = 1700,
        end_year: Optional[int] = None,
        chunk_size: int = 25,
        session: Optional[requests.Session] = None,
        auto_fetch: bool = True,
        missing_end: str = "heuristic",
        strict: bool = True,
        max_retries: int = 3,
        retry_backoff: float = 5.0,
        timeout: float = 60.0,
        request_pause: float = 1.0,
    ) -> None:
        if missing_end not in MISSING_END_POLICIES:
            raise ValueError(
                f"Invalid missing_end {missing_end!r}. Choose from: {', '.join(MISSING_END_POLICIES)}"
            )
        self.start_year = start_year
        self.end_year = (
            end_year if end_year is not None else datetime.datetime.now(tz=datetime.timezone.utc).year
        )
        self.chunk_size = chunk_size
        self.session = session or requests
        self.missing_end = missing_end
        self.strict = strict
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.timeout = timeout
        self.request_pause = request_pause
        self.failed_chunks: list[tuple[int, int]] = []
        self.retrieved_at: Optional[str] = None
        self.df_records: Optional[pd.DataFrame] = None
        self.df_long: Optional[pd.DataFrame] = None

        if auto_fetch:
            self.fetch_data()

    # -- Network layer -----------------------------------------------------

    @staticmethod
    def _retry_after(exc: Exception) -> float:
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        try:
            return min(float(headers.get("Retry-After", 0)), 120.0)
        except (TypeError, ValueError):
            return 0.0

    def _request(self, query: str) -> list[dict]:
        """Run one SPARQL query with retries and return its bindings."""
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(
                    WIKIDATA_SPARQL_ENDPOINT,
                    params={"query": query, "format": "json"},
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                bindings = response.json()["results"]["bindings"]
            except (requests.RequestException, ValueError, KeyError) as exc:
                # ValueError/KeyError: the endpoint cut the response short
                # or answered with an HTML error page instead of JSON.
                last_exc = exc
                if attempt < self.max_retries:
                    delay = max(self.retry_backoff * (2**attempt), self._retry_after(exc))
                    if delay > 0:
                        time.sleep(delay)
                continue
            if self.request_pause > 0:
                time.sleep(self.request_pause)
            return bindings
        raise FetchError(f"Wikidata query failed after {self.max_retries + 1} attempts: {last_exc}") from last_exc

    def _fetch_chunk(self, chunk_start: int, chunk_end: int) -> list[dict]:
        """Fetch a single temporal chunk. Returns raw participation bindings."""
        bindings = self._request(build_sparql_query(chunk_start, chunk_end, self.end_year))
        if len(bindings) >= QUERY_LIMIT:
            raise FetchError(
                f"Chunk {chunk_start}-{chunk_end} returned {len(bindings)} rows, the query LIMIT: "
                "the result is truncated. Use a smaller chunk_size."
            )
        return bindings

    def _fetch_entities(self, qids: Sequence[str], with_lifespan: bool) -> dict[str, dict]:
        """Fetch labels (and lifespans) for ``qids`` in batches."""
        bindings: list[dict] = []
        for i in range(0, len(qids), ENTITY_BATCH_SIZE):
            batch = qids[i : i + ENTITY_BATCH_SIZE]
            bindings.extend(self._request(build_entity_query(batch, with_lifespan=with_lifespan)))
        return parse_entities(bindings)

    def fetch_data(self) -> None:
        """Fetch all chunks from Wikidata and build the long-format grid."""
        participations: list[dict] = []
        self.failed_chunks = []
        for chunk_start in range(self.start_year, self.end_year, self.chunk_size):
            chunk_end = min(chunk_start + self.chunk_size, self.end_year)
            # The query filter's upper bound is exclusive; the last chunk must
            # include wars starting in end_year itself, not just before it.
            query_end = chunk_end + 1 if chunk_end >= self.end_year else chunk_end
            try:
                participations.extend(self._fetch_chunk(chunk_start, query_end))
            except FetchError as exc:
                if self.strict:
                    raise
                self.failed_chunks.append((chunk_start, chunk_end))
                warnings.warn(
                    f"Chunk {chunk_start}-{chunk_end} skipped, dataset is incomplete: {exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )

        conflict_qids = sorted({qid_from_uri(p["conflict"]["value"]) for p in participations})
        country_qids = sorted({qid_from_uri(p["country"]["value"]) for p in participations})
        conflicts = self._fetch_entities(conflict_qids, with_lifespan=False)
        countries = self._fetch_entities(country_qids, with_lifespan=True)

        self.retrieved_at = datetime.datetime.now(tz=datetime.timezone.utc).isoformat(timespec="seconds")
        self.load_records(build_records(participations, conflicts, countries, self.end_year, self.missing_end))

    def load_records(self, records: list[dict]) -> None:
        """Build the dataset from a list of flat conflict/country records.

        Bypasses the network layer entirely -- used by :meth:`fetch_data`
        and directly in tests/offline pipelines.
        """
        if not records:
            self.df_records = pd.DataFrame(columns=RECORD_COLUMNS)
            self.df_long = pd.DataFrame(columns=LONG_COLUMNS)
            return

        df_raw = pd.DataFrame(records)
        self.df_records = clean_dataframe(df_raw, self.start_year, self.end_year)
        self.df_long = build_long_grid(self.df_records, self.start_year, self.end_year)

    # -- Snapshots -----------------------------------------------------------

    def save_snapshot(self, directory: Union[str, Path]) -> Path:
        """Write a frozen, citable copy of the dataset to ``directory``.

        Produces ``participations.csv`` (one row per conflict x country) and
        ``metadata.json`` (retrieval time, parameters, query text, row
        counts, checksum). The Country x Year grid is not stored: it is
        derived deterministically from the participation table.
        """
        if self.df_records is None:
            raise ValueError("Dataset not initialized. Call fetch_data() or load_records() first.")
        from . import __version__

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        data_path = directory / SNAPSHOT_DATA_FILE
        self.df_records.to_csv(data_path, index=False, lineterminator="\n")

        metadata = {
            "histwardb_version": __version__,
            "retrieved_at": self.retrieved_at,
            "source": WIKIDATA_SPARQL_ENDPOINT,
            "start_year": self.start_year,
            "end_year": self.end_year,
            "chunk_size": self.chunk_size,
            "missing_end": self.missing_end,
            "failed_chunks": [list(c) for c in self.failed_chunks],
            "conflict_class": CONFLICT_CLASS,
            "excluded_conflict_classes": list(EXCLUDED_CONFLICT_CLASSES),
            "country_types": list(COUNTRY_TYPES),
            "forced_max_years": FORCED_MAX_YEARS,
            "participation_query_template": build_sparql_query(
                self.start_year, min(self.start_year + self.chunk_size, self.end_year), self.end_year
            ),
            "n_participations": len(self.df_records),
            "n_conflicts": int(self.df_records["conflict_qid"].nunique()),
            "n_countries": int(self.df_records["country_qid"].nunique()),
            "n_end_imputed": int(self.df_records["end_imputed"].sum()),
            "files": {SNAPSHOT_DATA_FILE: {"sha256_lf": _sha256_normalized(data_path)}},
        }
        (directory / SNAPSHOT_METADATA_FILE).write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return directory

    @classmethod
    def from_snapshot(cls, directory: Union[str, Path], verify: bool = True) -> HistWarDB:
        """Load a dataset written by :meth:`save_snapshot`, with no network access.

        With ``verify=True`` the data file's checksum is compared with the
        one recorded in ``metadata.json``.
        """
        directory = Path(directory)
        metadata = json.loads((directory / SNAPSHOT_METADATA_FILE).read_text(encoding="utf-8"))
        data_path = directory / SNAPSHOT_DATA_FILE
        if verify:
            expected = metadata["files"][SNAPSHOT_DATA_FILE]["sha256_lf"]
            actual = _sha256_normalized(data_path)
            if actual != expected:
                raise ValueError(f"Snapshot checksum mismatch for {data_path}: expected {expected}, got {actual}")

        db = cls(
            start_year=metadata["start_year"],
            end_year=metadata["end_year"],
            chunk_size=metadata["chunk_size"],
            missing_end=metadata["missing_end"],
            auto_fetch=False,
        )
        db.retrieved_at = metadata.get("retrieved_at")
        db.failed_chunks = [tuple(c) for c in metadata.get("failed_chunks", [])]
        records = pd.read_csv(data_path, dtype={"country_qid": str, "conflict_qid": str, "iso3": str})
        records["iso3"] = records["iso3"].where(records["iso3"].notna(), None)
        db.df_records = records[RECORD_COLUMNS]
        db.df_long = build_long_grid(db.df_records, db.start_year, db.end_year)
        return db

    # -- Query interface -----------------------------------------------------

    @property
    def participations(self) -> pd.DataFrame:
        """One row per (conflict, country) participation, with QIDs and dates."""
        if self.df_records is None:
            raise ValueError("Dataset not initialized. Call fetch_data() or load_records() first.")
        return self.df_records.copy()

    def __call__(
        self,
        countries: Optional[Union[str, Sequence[str]]] = None,
        view: str = "long",
        from_date: Optional[Union[str, int]] = None,
        to_date: Optional[Union[str, int]] = None,
        only_in_war: bool = False,
        keyword: Optional[str] = None,
        only_existing: bool = False,
    ):
        """Query the dataset. See module docstring for supported ``view`` values.

        ``countries`` matches exactly (case-insensitive) on the country
        label, its Wikidata QID or its ISO 3166-1 alpha-3 code.
        ``only_existing`` drops country-years outside the state's known
        lifespan; in the ``binary`` view those cells then read ``<NA>``
        instead of ``0``.
        """
        if self.df_long is None:
            raise ValueError("Dataset not initialized. Call fetch_data() or load_records() first.")
        if view not in VALID_VIEWS:
            raise ValueError(f"Invalid view {view!r}. Choose from: {', '.join(VALID_VIEWS)}")

        df = self.df_long.copy()

        if countries:
            c_list = [countries] if isinstance(countries, str) else list(countries)
            wanted = {c.casefold() for c in c_list}
            keys = [df[col].astype(str).str.casefold() for col in ("Country", "Country_QID", "ISO3")]
            matched = set().union(*(set(k[k.isin(wanted)]) for k in keys))
            for name in c_list:
                if name.casefold() not in matched:
                    close = difflib.get_close_matches(name, self.available_countries, n=5, cutoff=0.6)
                    hint = f" Did you mean: {', '.join(close)}?" if close else ""
                    warnings.warn(f"No country matches {name!r}.{hint}", UserWarning, stacklevel=2)
            df = df[keys[0].isin(wanted) | keys[1].isin(wanted) | keys[2].isin(wanted)]

        if from_date is not None:
            df = df[df["Year"] >= int(str(from_date).split("-")[0])]
        if to_date is not None:
            df = df[df["Year"] <= int(str(to_date).split("-")[0])]
        if only_existing:
            df = df[df["State_Exists"] == 1]
        if only_in_war:
            df = df[df["War_State"] == 1]
        if keyword:
            df = df[df["Active_Conflicts"].str.contains(keyword, case=False, na=False, regex=False)]

        return self._render_view(df, view, only_existing=only_existing)

    @staticmethod
    def _render_view(df: pd.DataFrame, view: str, only_existing: bool = False):
        if view == "long":
            return df
        if view == "binary":
            wide = df.pivot(index="Country", columns="Year", values="War_State")
            return wide.astype("Int64") if only_existing else wide.fillna(0).astype(int)
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
        """Build (and return) a Plotly heatmap figure of war state over time.

        Three cell states are drawn: at war (red), no recorded war (white)
        and state did not exist (grey). Does not call ``.show()`` so it can
        be used headlessly / tested.
        """
        import plotly.graph_objects as go

        from_year = self.start_year if from_year is None else from_year
        to_year = self.end_year if to_year is None else to_year

        df_range = self.df_long[(self.df_long["Year"] >= from_year) & (self.df_long["Year"] <= to_year)]
        top_countries = df_range.groupby("Country")["War_State"].sum().nlargest(top_n).index.tolist()
        df_filtered = df_range[df_range["Country"].isin(top_countries)].copy()

        missing = df_filtered["State_Exists"] == 0
        df_filtered["_cell"] = df_filtered["War_State"].where(~missing, -1)
        df_filtered["_text"] = df_filtered["Active_Conflicts"].where(
            df_filtered["Active_Conflicts"] != "", "No recorded conflicts"
        )
        df_filtered["_text"] = df_filtered["_text"].where(~missing, "State did not exist")

        matrix_val = df_filtered.pivot(index="Country", columns="Year", values="_cell").fillna(0)
        matrix_txt = df_filtered.pivot(index="Country", columns="Year", values="_text").fillna(
            "No recorded conflicts"
        )

        fig = go.Figure(
            data=go.Heatmap(
                z=matrix_val.values,
                x=matrix_val.columns,
                y=matrix_val.index,
                text=matrix_txt.values,
                hovertemplate="<b>Country:</b> %{y}<br><b>Year:</b> %{x}<br><b>Conflict(s):</b> %{text}<extra></extra>",
                colorscale=[[0, "#e9ecef"], [0.5, "#ffffff"], [1, "#d9534f"]],
                zmin=-1,
                zmax=1,
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
