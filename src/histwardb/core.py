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
from importlib import resources
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
    build_coalition_query,
    build_entity_query,
    build_participant_detail_query,
    build_sparql_query,
)
from .relations import (
    assign_sides,
    build_dyads,
    coalition_participations,
    missing_ancestors,
    parse_coalitions,
    parse_participant_details,
    parse_structure,
    resolve_roots,
    summarize_conflicts,
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

# Duration heuristic applied when a conflict has no recorded end date
# (Wikidata P582). Conflicts starting before ``HEURISTIC_CUTOFF_YEAR`` are
# assumed to have lasted ``PRE_CUTOFF_DEFAULT_DURATION_YEARS`` years;
# conflicts starting at or after it are assumed ongoing through the
# dataset's ``end_year``. Both numbers are deliberately named constants
# (rather than inline literals) so callers can see and override them --
# they are rough estimates, not ground truth, and every record built from
# them is flagged via ``duration_estimated`` instead of being silently
# indistinguishable from a sourced end date.
HEURISTIC_CUTOFF_YEAR = 1800
PRE_CUTOFF_DEFAULT_DURATION_YEARS = 5

VALID_VIEWS = ("long", "binary", "names", "list", "countries", "conflicts")

# How a conflict with no recorded end date (P582) is handled:
#   "heuristic"  -- the cutoff rule above (default);
#   "open"       -- ongoing to the dataset end, whatever the start year;
#   "start_only" -- counted for its start year only;
#   "drop"       -- discarded.
# Whatever the policy, estimated records are flagged (``duration_estimated``).
MISSING_END_POLICIES = ("heuristic", "open", "start_only", "drop")

# How conflicts that are part of (P361) another war in the dataset are handled:
#   "keep"    -- both the sub-conflict and its parent are kept (default);
#   "drop"    -- sub-conflicts whose top-level war is in the dataset are
#                dropped, so each state-year is attributed to one war only;
#   "roll_up" -- sub-conflict participations are re-attributed to their
#                top-level war, which also recovers participants that
#                Wikidata lists only on a front or campaign.
SUBCONFLICT_POLICIES = ("keep", "drop", "roll_up")

# Rounds of P361 ancestor look-ups when resolving top-level wars.
MAX_ANCESTOR_ROUNDS = 6

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
    "duration_estimated",
    "casualties",
    "country_start",
    "country_end",
    "conflict_type",
    "parent_conflict_qid",
    "root_conflict_qid",
    "root_conflict_name",
    "side",
    "side_qid",
    "via_coalition",
    "role",
]

# Optional record fields and their defaults, for records built without them
# (older snapshots, hand-made records in tests or offline pipelines).
_RECORD_DEFAULTS = {
    "country_qid": None,
    "conflict_qid": None,
    "iso3": None,
    "duration_estimated": False,
    "casualties": None,
    "conflict_type": None,
    "parent_conflict_qid": None,
    "root_conflict_qid": None,
    "root_conflict_name": None,
    "side": None,
    "side_qid": None,
    "via_coalition": False,
    "role": None,
    "participant_start": None,
    "participant_end": None,
}

LONG_COLUMNS = [
    "Country",
    "Year",
    "War_State",
    "Active_Conflicts",
    "Duration_Estimated",
    "Country_QID",
    "ISO3",
    "COW_Code",
    "GW_Code",
    "State_Exists",
    "War_Imputed",
]

# Correlates of War and Gleditsch-Ward codes per Wikidata QID, shipped with
# the package (built by scripts/build_crosswalk.py).
CROSSWALK_FILE = "crosswalk.csv"
CODE_COLUMNS = ["cow_code", "gw_code"]

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


def impute_end_year(
    start_year: int,
    dataset_end_year: int,
    missing_end: str = "heuristic",
    heuristic_cutoff_year: int = HEURISTIC_CUTOFF_YEAR,
    pre_cutoff_duration_years: int = PRE_CUTOFF_DEFAULT_DURATION_YEARS,
) -> Optional[int]:
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
    if start_year >= heuristic_cutoff_year:
        return dataset_end_year
    return start_year + pre_cutoff_duration_years


def _parse_number(value: Optional[str]) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    casualties: Optional[float],
    dataset_end_year: int,
    missing_end: str,
    heuristic_cutoff_year: int,
    pre_cutoff_duration_years: int,
) -> Optional[dict]:
    if start_year is None:
        return None

    duration_estimated = finish_year is None
    if duration_estimated:
        finish_year = impute_end_year(
            start_year, dataset_end_year, missing_end, heuristic_cutoff_year, pre_cutoff_duration_years
        )
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
        "duration_estimated": duration_estimated,
        "casualties": casualties,
        "country_start": country_start,
        "country_end": country_end,
    }


def parse_bindings(
    bindings: Iterable[dict],
    end_year: int,
    heuristic_cutoff_year: int = HEURISTIC_CUTOFF_YEAR,
    pre_cutoff_default_duration_years: int = PRE_CUTOFF_DEFAULT_DURATION_YEARS,
    missing_end: str = "heuristic",
) -> list[dict]:
    """Turn flat SPARQL JSON ``bindings`` into conflict/country records.

    "Flat" means one binding carries the labels and the country lifespan
    alongside the dates (``countryLabel``, ``conflictLabel``, ``startDate``,
    and optionally ``endDate``, ``countryStart``, ``countryEnd``,
    ``countryEndFallback``, ``country``, ``conflict``, ``iso3``,
    ``casualties``).

    When a conflict has no recorded end date, its duration is estimated
    according to ``missing_end`` (see :func:`impute_end_year`); such records
    are flagged via ``duration_estimated=True`` rather than silently
    presented as sourced data.
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
            casualties=_parse_number(item["casualties"]["value"]) if "casualties" in item else None,
            dataset_end_year=end_year,
            missing_end=missing_end,
            heuristic_cutoff_year=heuristic_cutoff_year,
            pre_cutoff_duration_years=pre_cutoff_default_duration_years,
        )
        if record is not None:
            records.append(record)
    return records


def parse_entities(bindings: Iterable[dict]) -> dict[str, dict]:
    """Collapse entity-query bindings into one metadata dict per QID.

    Multi-valued properties are reduced deterministically: the earliest
    inception, the latest dissolution (``P576``, falling back to ``P582``
    only when no ``P576`` exists), the alphabetically first ISO code, and
    the largest recorded death toll (``P1120`` often carries several
    estimates; the largest keeps a severity filter conservative). An entity
    with no English label is labelled with its QID.
    """
    raw: dict[str, dict] = {}
    for item in bindings:
        qid = qid_from_uri(item["item"]["value"])
        entry = raw.setdefault(
            qid, {"label": None, "starts": [], "ends": [], "fallback_ends": [], "iso3": [], "casualties": []}
        )
        if "itemLabel" in item and entry["label"] is None:
            entry["label"] = item["itemLabel"]["value"]
        for key, bucket in (("inception", "starts"), ("dissolved", "ends"), ("endTime", "fallback_ends")):
            year = parse_year(item[key]["value"]) if key in item else None
            if year is not None:
                entry[bucket].append(year)
        if "iso3" in item:
            entry["iso3"].append(item["iso3"]["value"])
        casualties = _parse_number(item["casualties"]["value"]) if "casualties" in item else None
        if casualties is not None:
            entry["casualties"].append(casualties)

    entities: dict[str, dict] = {}
    for qid, entry in raw.items():
        ends = entry["ends"] or entry["fallback_ends"]
        entities[qid] = {
            "label": entry["label"] or qid,
            "start": min(entry["starts"]) if entry["starts"] else None,
            "end": max(ends) if ends else None,
            "iso3": min(entry["iso3"]) if entry["iso3"] else None,
            "casualties": max(entry["casualties"]) if entry["casualties"] else None,
        }
    return entities


def build_records(
    participations: Iterable[dict],
    conflicts: dict[str, dict],
    countries: dict[str, dict],
    end_year: int,
    missing_end: str = "heuristic",
    heuristic_cutoff_year: int = HEURISTIC_CUTOFF_YEAR,
    pre_cutoff_duration_years: int = PRE_CUTOFF_DEFAULT_DURATION_YEARS,
    details: Optional[dict] = None,
    sides: Optional[dict] = None,
    roots: Optional[dict] = None,
) -> list[dict]:
    """Join participation-query bindings with entity metadata into records.

    ``conflicts`` may come from :func:`parse_entities` or, with types and
    parents, from :func:`~histwardb.relations.parse_structure`. The optional
    ``details`` (``P710`` qualifiers), ``sides`` (coalition sides) and
    ``roots`` (part-of resolution) enrich each record when given. A binding
    carrying ``viaCoalition`` was synthesized from a coalition's members.
    """
    details = details or {}
    sides = sides or {}
    roots = roots or {}
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
            casualties=conflict.get("casualties"),
            dataset_end_year=end_year,
            missing_end=missing_end,
            heuristic_cutoff_year=heuristic_cutoff_year,
            pre_cutoff_duration_years=pre_cutoff_duration_years,
        )
        if record is None:
            continue

        detail = details.get((conflict_qid, country_qid), {})
        side = sides.get((conflict_qid, country_qid), {})
        root = roots.get(conflict_qid, {})
        root_qid = root.get("root")
        record.update(
            {
                "conflict_type": "; ".join(conflict.get("types", [])) or None,
                "parent_conflict_qid": root.get("parent"),
                "root_conflict_qid": root_qid,
                "root_conflict_name": conflicts.get(root_qid, {}).get("label") if root_qid else None,
                "side": side.get("side"),
                "side_qid": side.get("side_qid"),
                "via_coalition": "viaCoalition" in item,
                "role": "; ".join(detail.get("roles", [])) or None,
                "participant_start": detail.get("start"),
                "participant_end": detail.get("end"),
            }
        )
        records.append(record)
    return records


def _with_record_defaults(df: pd.DataFrame) -> pd.DataFrame:
    """Fill in the optional record columns so older 6-column records still work."""
    df = df.copy()
    for column, default in _RECORD_DEFAULTS.items():
        if column not in df.columns:
            df[column] = default
    # Without a QID the label is the only identity available.
    df["country_qid"] = df["country_qid"].where(df["country_qid"].notna(), df["country"])
    df["conflict_qid"] = df["conflict_qid"].where(df["conflict_qid"].notna(), df["conflict_name"])
    df["duration_estimated"] = df["duration_estimated"].astype(bool)
    df["country_start"] = pd.to_numeric(df["country_start"], errors="coerce")
    df["country_end"] = pd.to_numeric(df["country_end"], errors="coerce")
    df["casualties"] = pd.to_numeric(df["casualties"], errors="coerce")
    df["participant_start"] = pd.to_numeric(df["participant_start"], errors="coerce")
    df["participant_end"] = pd.to_numeric(df["participant_end"], errors="coerce")
    df["via_coalition"] = df["via_coalition"].astype(bool)
    return df


def _join_unique(values: pd.Series) -> Optional[str]:
    """Aggregate "; "-joined strings into one sorted, deduplicated string."""
    parts = {part for value in values.dropna() for part in str(value).split("; ") if part}
    return "; ".join(sorted(parts)) if parts else None


def _apply_subconflict_policy(df: pd.DataFrame, subconflicts: str) -> pd.DataFrame:
    if subconflicts not in SUBCONFLICT_POLICIES:
        raise ValueError(
            f"Invalid subconflicts {subconflicts!r}. Choose from: {', '.join(SUBCONFLICT_POLICIES)}"
        )
    if subconflicts == "keep":
        return df
    is_sub = df["root_conflict_qid"].notna() & (df["root_conflict_qid"] != df["conflict_qid"])
    if subconflicts == "drop":
        return df[~(is_sub & df["root_conflict_qid"].isin(set(df["conflict_qid"])))]
    df = df.copy()
    root_names = df.loc[is_sub, "root_conflict_name"].fillna(df.loc[is_sub, "root_conflict_qid"])
    df.loc[is_sub, "conflict_name"] = root_names
    df.loc[is_sub, "conflict_qid"] = df.loc[is_sub, "root_conflict_qid"]
    df.loc[is_sub, "parent_conflict_qid"] = None
    df.loc[is_sub, "conflict_type"] = None
    return df


def _disambiguate_labels(df: pd.DataFrame, label_col: str, qid_col: str) -> pd.DataFrame:
    """Append the QID to labels shared by more than one entity."""
    shared = df.groupby(label_col)[qid_col].transform("nunique") > 1
    if shared.any():
        df.loc[shared, label_col] = df.loc[shared, label_col] + " (" + df.loc[shared, qid_col].astype(str) + ")"
    return df


def clean_dataframe(
    df_raw: pd.DataFrame,
    start_year: int,
    end_year: int,
    min_casualties: Optional[float] = None,
    subconflicts: str = "keep",
) -> pd.DataFrame:
    """Turn raw records into one clean row per (conflict, country) participation.

    Pure function: no network access, safe to unit test with synthetic
    DataFrames. Steps:

    1. drop countries whose label is an unresolved QID;
    2. when ``min_casualties`` is set, drop conflicts whose *recorded* death
       toll (Wikidata P1120) is below it -- conflicts with no recorded toll
       are kept, since Wikidata's coverage of casualty figures is too sparse
       to treat "unknown" as "zero";
    3. apply the ``subconflicts`` policy (:data:`SUBCONFLICT_POLICIES`);
    4. reduce each country to one lifespan (earliest start, latest end,
       capped by :data:`FORCED_MAX_YEARS`);
    5. reduce each (conflict, country) pair to one interval;
    6. clip that interval to the participant's own dates (``P710``
       qualifiers, when recorded) and to the country's lifespan, and drop it
       if nothing is left;
    7. drop intervals entirely outside ``[start_year, end_year]``.

    ``start_year``/``end_year`` in the result are the clipped participation
    years; ``war_start_year``/``war_end_year`` keep the conflict's own dates.
    """
    if df_raw.empty:
        return df_raw

    df = _with_record_defaults(df_raw)
    df = df[~df["country"].astype(str).str.contains(_QID_RE.pattern, na=False)]
    if df.empty:
        return pd.DataFrame(columns=RECORD_COLUMNS)

    if min_casualties is not None:
        df = df[df["casualties"].isna() | (df["casualties"] >= min_casualties)]
        if df.empty:
            return pd.DataFrame(columns=RECORD_COLUMNS)

    df = _apply_subconflict_policy(df, subconflicts)
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
    df = df.assign(_observed_end=df["end_year"].where(~df["duration_estimated"]))
    parts = (
        df.groupby(["conflict_qid", "country_qid"], sort=True)
        .agg(
            country=("country", "first"),
            iso3=("iso3", "first"),
            conflict_name=("conflict_name", "first"),
            war_start_year=("start_year", "min"),
            _observed_end=("_observed_end", "max"),
            _any_end=("end_year", "max"),
            duration_estimated=("duration_estimated", "all"),
            casualties=("casualties", "max"),
            participant_start=("participant_start", "min"),
            participant_end=("participant_end", "max"),
            conflict_type=("conflict_type", "first"),
            parent_conflict_qid=("parent_conflict_qid", "first"),
            root_conflict_qid=("root_conflict_qid", "first"),
            root_conflict_name=("root_conflict_name", "first"),
            side=("side", _join_unique),
            side_qid=("side_qid", _join_unique),
            via_coalition=("via_coalition", "all"),
            role=("role", _join_unique),
        )
        .reset_index()
    )
    parts["war_end_year"] = parts["_observed_end"].fillna(parts["_any_end"])
    parts = parts.join(lifespans, on="country_qid")

    # Clip to the state's lifespan: a state cannot be at war in a year in
    # which it did not exist. Missing lifespan data imposes no constraint.
    parts["start_year"] = parts[["war_start_year", "participant_start", "country_start"]].max(axis=1)
    parts["end_year"] = parts[["war_end_year", "participant_end", "country_end"]].min(axis=1)
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

    * ``Duration_Estimated`` -- ``True`` when at least one active conflict
      has no recorded end date, so its duration was estimated;
    * ``State_Exists`` -- 0 when the year is outside the state's known
      lifespan, so "no war" can be told apart from "no state";
    * ``War_Imputed`` -- 1 when the cell is at war *only* because a
      conflict with no recorded end date was extended past its start year
      (stricter than ``Duration_Estimated``: a cell also covered by a dated
      conflict, or in the start year itself, is 0).
    """
    if df_clean.empty:
        return pd.DataFrame(columns=LONG_COLUMNS)

    active: dict[tuple, set] = {}
    observed: set[tuple] = set()
    estimated: set[tuple] = set()
    for row in df_clean.itertuples(index=False):
        first = max(row.start_year, start_year)
        last = min(row.end_year, end_year)
        for year in range(first, last + 1):
            key = (row.country_qid, year)
            active.setdefault(key, set()).add(row.conflict_name)
            if row.duration_estimated:
                estimated.add(key)
            if not row.duration_estimated or year == row.war_start_year:
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
                    "Duration_Estimated": key in estimated,
                    "Country_QID": country.country_qid,
                    "ISO3": country.iso3,
                    "COW_Code": getattr(country, "cow_code", pd.NA),
                    "GW_Code": getattr(country, "gw_code", pd.NA),
                    "State_Exists": 1 if exists else 0,
                    "War_Imputed": 1 if names and key not in observed else 0,
                }
            )

    grid = pd.DataFrame(rows, columns=LONG_COLUMNS)
    for column in ("COW_Code", "GW_Code"):
        grid[column] = grid[column].astype("Int64")
    return grid


def load_crosswalk() -> pd.DataFrame:
    """The bundled QID -> COW / Gleditsch-Ward code table (empty if not shipped)."""
    try:
        source = resources.files("histwardb").joinpath("data", CROSSWALK_FILE)
        with source.open("r", encoding="utf-8") as f:
            table = pd.read_csv(f, dtype={"country_qid": str})
    except (FileNotFoundError, ModuleNotFoundError):
        return pd.DataFrame(columns=["country_qid", *CODE_COLUMNS])
    for column in CODE_COLUMNS:
        table[column] = pd.to_numeric(table[column], errors="coerce").astype("Int64")
    return table[["country_qid", *CODE_COLUMNS]]


def attach_codes(records: pd.DataFrame, crosswalk: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Add ``cow_code`` / ``gw_code`` to participation records by country QID."""
    crosswalk = load_crosswalk() if crosswalk is None else crosswalk
    base = records.drop(columns=[c for c in CODE_COLUMNS if c in records.columns])
    if base.empty:
        return base.assign(**{c: pd.Series(dtype="Int64") for c in CODE_COLUMNS})
    merged = base.merge(crosswalk, on="country_qid", how="left")
    for column in CODE_COLUMNS:
        merged[column] = merged[column].astype("Int64")
    return merged


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
    heuristic_cutoff_year, pre_cutoff_duration_years:
        Tune the ``"heuristic"`` policy: conflicts starting before
        ``heuristic_cutoff_year`` are assumed to last
        ``pre_cutoff_duration_years`` years; conflicts starting on/after it
        are assumed to run through ``end_year``. Records built from this
        fallback are flagged (``duration_estimated`` / ``Duration_Estimated``).
    min_casualties:
        Optional minimum recorded death toll (Wikidata P1120). Conflicts
        with a recorded toll below this value are excluded, which is the
        knob for not treating a two-day skirmish the same as a world war.
        Conflicts with no recorded toll are kept (``None`` by default:
        no severity filtering).
    subconflicts:
        How conflicts that are part of another war in the dataset (e.g. a
        front of World War II) are handled; one of
        :data:`SUBCONFLICT_POLICIES`.
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
        min_casualties: Optional[float] = None,
        heuristic_cutoff_year: int = HEURISTIC_CUTOFF_YEAR,
        pre_cutoff_duration_years: int = PRE_CUTOFF_DEFAULT_DURATION_YEARS,
        missing_end: str = "heuristic",
        subconflicts: str = "keep",
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
        if subconflicts not in SUBCONFLICT_POLICIES:
            raise ValueError(
                f"Invalid subconflicts {subconflicts!r}. Choose from: {', '.join(SUBCONFLICT_POLICIES)}"
            )
        self.start_year = start_year
        self.end_year = (
            end_year if end_year is not None else datetime.datetime.now(tz=datetime.timezone.utc).year
        )
        self.chunk_size = chunk_size
        self.session = session or requests
        self.min_casualties = min_casualties
        self.heuristic_cutoff_year = heuristic_cutoff_year
        self.pre_cutoff_duration_years = pre_cutoff_duration_years
        self.missing_end = missing_end
        self.subconflicts = subconflicts
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

    def _fetch_entities(
        self, qids: Sequence[str], with_lifespan: bool, with_casualties: bool = False
    ) -> dict[str, dict]:
        """Fetch labels (and lifespans or death tolls) for ``qids`` in batches."""
        bindings: list[dict] = []
        for i in range(0, len(qids), ENTITY_BATCH_SIZE):
            batch = qids[i : i + ENTITY_BATCH_SIZE]
            query = build_entity_query(batch, with_lifespan=with_lifespan, with_casualties=with_casualties)
            bindings.extend(self._request(query))
        return parse_entities(bindings)

    def _fetch_batched(self, build, qids: Sequence[str]) -> list[dict]:
        bindings: list[dict] = []
        for i in range(0, len(qids), ENTITY_BATCH_SIZE):
            bindings.extend(self._request(build(qids[i : i + ENTITY_BATCH_SIZE])))
        return bindings

    def _fetch_structure(self, conflict_qids: Sequence[str]) -> dict[str, dict]:
        """Fetch conflict types, parents and dates, then walk up P361 chains."""

        def build(batch):
            return build_entity_query(batch, with_lifespan=False, with_casualties=True, with_structure=True)

        structure = parse_structure(self._fetch_batched(build, conflict_qids))
        for _ in range(MAX_ANCESTOR_ROUNDS):
            todo = missing_ancestors(structure)
            if not todo:
                break
            structure.update(parse_structure(self._fetch_batched(build, todo)))
        return structure

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
        structure = self._fetch_structure(conflict_qids)
        roots = resolve_roots(structure, conflict_qids)
        details = parse_participant_details(self._fetch_batched(build_participant_detail_query, conflict_qids))
        coalitions = parse_coalitions(self._fetch_batched(build_coalition_query, conflict_qids))

        extra = coalition_participations(coalitions, structure, participations)
        for binding in extra:
            binding["viaCoalition"] = {"value": "true"}
        participations = participations + extra

        country_qids = sorted({qid_from_uri(p["country"]["value"]) for p in participations})
        countries = self._fetch_entities(country_qids, with_lifespan=True)

        self.retrieved_at = datetime.datetime.now(tz=datetime.timezone.utc).isoformat(timespec="seconds")
        records = build_records(
            participations,
            structure,
            countries,
            self.end_year,
            missing_end=self.missing_end,
            heuristic_cutoff_year=self.heuristic_cutoff_year,
            pre_cutoff_duration_years=self.pre_cutoff_duration_years,
            details=details,
            sides=assign_sides(coalitions),
            roots=roots,
        )
        self.load_records(records)

    def load_records(self, records: list[dict]) -> None:
        """Build the dataset from a list of flat conflict/country records.

        Bypasses the network layer entirely -- used by :meth:`fetch_data`
        and directly in tests/offline pipelines.
        """
        if not records:
            self.df_records = pd.DataFrame(columns=RECORD_COLUMNS + CODE_COLUMNS)
            self.df_long = pd.DataFrame(columns=LONG_COLUMNS)
            return

        df_raw = pd.DataFrame(records)
        self.df_records = clean_dataframe(
            df_raw,
            self.start_year,
            self.end_year,
            min_casualties=self.min_casualties,
            subconflicts=self.subconflicts,
        )
        self.df_records = attach_codes(self.df_records)
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
            "heuristic_cutoff_year": self.heuristic_cutoff_year,
            "pre_cutoff_duration_years": self.pre_cutoff_duration_years,
            "min_casualties": self.min_casualties,
            "subconflicts": self.subconflicts,
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
            "n_duration_estimated": int(self.df_records["duration_estimated"].sum()),
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
            heuristic_cutoff_year=metadata.get("heuristic_cutoff_year", HEURISTIC_CUTOFF_YEAR),
            pre_cutoff_duration_years=metadata.get("pre_cutoff_duration_years", PRE_CUTOFF_DEFAULT_DURATION_YEARS),
            min_casualties=metadata.get("min_casualties"),
            subconflicts=metadata.get("subconflicts", "keep"),
            auto_fetch=False,
        )
        db.retrieved_at = metadata.get("retrieved_at")
        db.failed_chunks = [tuple(c) for c in metadata.get("failed_chunks", [])]
        records = pd.read_csv(data_path, dtype={"country_qid": str, "conflict_qid": str, "iso3": str})
        records["iso3"] = records["iso3"].where(records["iso3"].notna(), None)
        # Snapshots written before a column existed load with its default.
        for column in RECORD_COLUMNS:
            if column not in records.columns:
                records[column] = _RECORD_DEFAULTS.get(column)
        if all(c in records.columns for c in CODE_COLUMNS):
            # The snapshot's own codes are part of the frozen data.
            for column in CODE_COLUMNS:
                records[column] = pd.to_numeric(records[column], errors="coerce").astype("Int64")
            records = records[RECORD_COLUMNS + CODE_COLUMNS]
        else:
            records = attach_codes(records[RECORD_COLUMNS])
        db.df_records = records
        db.df_long = build_long_grid(db.df_records, db.start_year, db.end_year)
        return db

    # -- Query interface -----------------------------------------------------

    @property
    def participations(self) -> pd.DataFrame:
        """One row per (conflict, country) participation, with QIDs and dates."""
        if self.df_records is None:
            raise ValueError("Dataset not initialized. Call fetch_data() or load_records() first.")
        return self.df_records.copy()

    @property
    def conflicts(self) -> pd.DataFrame:
        """One row per conflict: type, parent and root war, dates, death toll, state counts."""
        return summarize_conflicts(self.participations)

    @property
    def dyads(self) -> pd.DataFrame:
        """Pairs of states on opposing known sides of the same conflict, with overlapping years.

        Sides are only known where Wikidata lists a coalition (e.g. "Axis
        Powers") as a participant, so this table covers a small subset of
        conflicts; see the codebook.
        """
        return build_dyads(self.participations)

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
