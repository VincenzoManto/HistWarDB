"""Relations between conflicts and between belligerents.

Pure functions (no I/O) that turn the structure, participant-detail and
coalition queries of :mod:`histwardb.query` into:

* each conflict's type, war parent and top-level ("root") war, following
  ``P361`` (part of) chains;
* per-participant roles and dates recorded as qualifiers on ``P710``;
* coalition members expanded into participants, and the resulting sides;
* a dyadic table of states on opposing sides of the same conflict.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

import pandas as pd

# "series of wars" (e.g. Napoleonic Wars, German-Russian wars) groups wars
# that are distinct units of analysis, so it is never used as a root.
SERIES_OF_WARS = "Q104212151"

# Hard stop for P361 chains, which occasionally contain cycles.
MAX_PART_OF_DEPTH = 8


def _qid(uri: Optional[str]) -> Optional[str]:
    return uri.rsplit("/", 1)[-1] if isinstance(uri, str) and uri else None


def _year(value: Optional[str]) -> Optional[int]:
    from .core import parse_year

    return parse_year(value)


def parse_structure(bindings: Iterable[dict]) -> dict[str, dict]:
    """Collapse structure-query bindings into one dict per conflict-like item.

    Each entry holds ``label``, ``types`` (sorted English class labels),
    ``type_qids``, ``parents`` (QIDs of ``P361`` targets), ``is_war``,
    ``start`` / ``end`` (earliest start, latest end) and ``casualties``
    (largest recorded ``P1120`` value).
    """
    from .core import _parse_number

    raw: dict[str, dict] = {}
    for item in bindings:
        qid = _qid(item["item"]["value"])
        entry = raw.setdefault(
            qid,
            {
                "label": None,
                "types": set(),
                "type_qids": set(),
                "parents": set(),
                "is_war": False,
                "starts": [],
                "ends": [],
                "casualties": [],
            },
        )
        if "itemLabel" in item and entry["label"] is None:
            entry["label"] = item["itemLabel"]["value"]
        if "typeLabel" in item:
            entry["types"].add(item["typeLabel"]["value"])
        if "type" in item:
            entry["type_qids"].add(_qid(item["type"]["value"]))
        if "parent" in item:
            entry["parents"].add(_qid(item["parent"]["value"]))
        if "isWar" in item and item["isWar"]["value"] in ("true", "1"):
            entry["is_war"] = True
        for key, bucket in (("warStart", "starts"), ("warEnd", "ends")):
            year = _year(item[key]["value"]) if key in item else None
            if year is not None:
                entry[bucket].append(year)
        value = _parse_number(item["casualties"]["value"]) if "casualties" in item else None
        if value is not None:
            entry["casualties"].append(value)

    return {
        qid: {
            "label": e["label"] or qid,
            "types": sorted(e["types"]),
            "type_qids": sorted(e["type_qids"]),
            "parents": sorted(p for p in e["parents"] if p != qid),
            "is_war": e["is_war"],
            "start": min(e["starts"]) if e["starts"] else None,
            "end": max(e["ends"]) if e["ends"] else None,
            "casualties": max(e["casualties"]) if e["casualties"] else None,
        }
        for qid, e in raw.items()
    }


def missing_ancestors(structure: dict[str, dict]) -> list[str]:
    """QIDs referenced as parents but not yet described in ``structure``."""
    return sorted({p for e in structure.values() for p in e["parents"]} - set(structure))


def _can_be_root(entry: Optional[dict]) -> bool:
    return bool(entry) and entry["is_war"] and SERIES_OF_WARS not in entry.get("type_qids", [])


def resolve_roots(structure: dict[str, dict], present: Iterable[str]) -> dict[str, dict]:
    """Find each conflict's war parent and its top-level war ancestor.

    The *parent* is the nearest ``P361`` ancestor that is itself a war. The
    *root* is the topmost such war ancestor (a war with no war above it);
    the conflict itself when it has none. Items typed "series of wars" are
    never parents or roots, and the walk does not continue through them.
    When several candidates tie, one that is ``present`` in the dataset is
    preferred, then the lowest QID, so the result is deterministic.

    Returns ``{qid: {"parent": qid | None, "root": qid}}`` for every item in
    ``present``.
    """
    present = set(present)

    def rank(qid: str) -> tuple:
        return (qid not in present, int(qid[1:]) if qid[1:].isdigit() else 0, qid)

    result: dict[str, dict] = {}
    for qid in present:
        frontier = [qid]
        seen = {qid}
        war_ancestors: list[tuple[int, str]] = []
        depth = 0
        while frontier and depth < MAX_PART_OF_DEPTH:
            depth += 1
            nxt = []
            for node in frontier:
                for parent in structure.get(node, {}).get("parents", []):
                    if parent in seen:
                        continue
                    seen.add(parent)
                    entry = structure.get(parent)
                    if entry and SERIES_OF_WARS in entry.get("type_qids", []):
                        continue
                    if _can_be_root(entry):
                        war_ancestors.append((depth, parent))
                    nxt.append(parent)
            frontier = nxt

        if not war_ancestors:
            result[qid] = {"parent": None, "root": qid}
            continue
        nearest = min(d for d, _ in war_ancestors)
        parent = min((p for d, p in war_ancestors if d == nearest), key=rank)
        # Topmost: a war ancestor none of whose own parents leads to another
        # war ancestor of this conflict.
        ancestors = {p for _, p in war_ancestors}
        tops = [a for a in ancestors if not (set(_war_ancestors_of(a, structure)) & ancestors)]
        root = min(tops or ancestors, key=rank)
        result[qid] = {"parent": parent, "root": root}
    return result


def _war_ancestors_of(qid: str, structure: dict[str, dict]) -> list[str]:
    out, frontier, seen = [], [qid], {qid}
    for _ in range(MAX_PART_OF_DEPTH):
        nxt = []
        for node in frontier:
            for parent in structure.get(node, {}).get("parents", []):
                if parent in seen:
                    continue
                seen.add(parent)
                entry = structure.get(parent)
                if entry and SERIES_OF_WARS in entry.get("type_qids", []):
                    continue
                if _can_be_root(entry):
                    out.append(parent)
                nxt.append(parent)
        frontier = nxt
    return out


def parse_participant_details(bindings: Iterable[dict]) -> dict[tuple, dict]:
    """Collect ``P710`` qualifiers into ``{(conflict, participant): details}``.

    ``details`` holds ``roles`` (sorted labels), ``start`` (earliest
    qualifier start year) and ``end`` (latest qualifier end year).
    """
    raw: dict[tuple, dict] = {}
    for item in bindings:
        key = (_qid(item["conflict"]["value"]), _qid(item["participant"]["value"]))
        entry = raw.setdefault(key, {"roles": set(), "starts": [], "ends": []})
        if "roleLabel" in item:
            entry["roles"].add(item["roleLabel"]["value"])
        for field, bucket in (("participantStart", "starts"), ("participantEnd", "ends")):
            year = _year(item[field]["value"]) if field in item else None
            if year is not None:
                entry[bucket].append(year)
    return {
        key: {
            "roles": sorted(e["roles"]),
            "start": min(e["starts"]) if e["starts"] else None,
            "end": max(e["ends"]) if e["ends"] else None,
        }
        for key, e in raw.items()
    }


def parse_coalitions(bindings: Iterable[dict]) -> list[dict]:
    """Turn coalition-query bindings into ``{conflict, coalition, coalition_label, member}`` rows."""
    rows = {
        (
            _qid(item["conflict"]["value"]),
            _qid(item["coalition"]["value"]),
            item.get("coalitionLabel", {}).get("value") or _qid(item["coalition"]["value"]),
            _qid(item["member"]["value"]),
        )
        for item in bindings
    }
    return [
        {"conflict": c, "coalition": k, "coalition_label": label, "member": m}
        for c, k, label, m in sorted(rows)
    ]


def coalition_participations(
    coalitions: Iterable[dict],
    structure: dict[str, dict],
    participations: Iterable[dict],
) -> list[dict]:
    """Synthesize participation bindings for coalition members.

    A member listed only through its coalition gets the conflict's own dates:
    the structure query's start/end when known, otherwise the earliest start
    and latest end among the conflict's direct participations. Members that
    are already direct participants are not duplicated.
    """
    from .core import parse_year

    entity = "http://www.wikidata.org/entity/"
    direct = set()
    starts: dict[str, int] = {}
    ends: dict[str, int] = {}
    for p in participations:
        conflict = _qid(p["conflict"]["value"])
        direct.add((conflict, _qid(p["country"]["value"])))
        s = parse_year(p["startDate"]["value"])
        if s is not None:
            starts[conflict] = min(s, starts.get(conflict, s))
        e = parse_year(p["endDate"]["value"]) if "endDate" in p else None
        if e is not None:
            ends[conflict] = max(e, ends.get(conflict, e))

    out = []
    added = set()
    for row in coalitions:
        key = (row["conflict"], row["member"])
        if key in direct or key in added:
            continue
        info = structure.get(row["conflict"], {})
        start = info.get("start") or starts.get(row["conflict"])
        if start is None:
            continue
        end = info.get("end") or ends.get(row["conflict"])
        binding = {
            "conflict": {"value": entity + row["conflict"]},
            "country": {"value": entity + row["member"]},
            "startDate": {"value": f"{start}-01-01T00:00:00Z"},
        }
        if end is not None:
            binding["endDate"] = {"value": f"{end}-12-31T00:00:00Z"}
        out.append(binding)
        added.add(key)
    return out


def assign_sides(coalitions: Iterable[dict]) -> dict[tuple, dict]:
    """Map ``(conflict, state)`` to its side, from coalition membership.

    A state that belongs to exactly one coalition in a conflict is on that
    coalition's side. A state listed in several coalitions of the same
    conflict (e.g. one that switched sides) gets all of them, joined with
    ``"; "``, and is left out of the dyads.
    """
    sides: dict[tuple, dict] = {}
    for row in coalitions:
        entry = sides.setdefault((row["conflict"], row["member"]), {})
        entry[row["coalition"]] = row["coalition_label"]
    return {
        key: {
            "side_qid": "; ".join(sorted(entry)),
            "side": "; ".join(entry[q] for q in sorted(entry)),
            "side_ambiguous": len(entry) > 1,
        }
        for key, entry in sides.items()
    }


DYAD_COLUMNS = [
    "conflict_qid",
    "conflict_name",
    "country_a",
    "country_a_qid",
    "side_a",
    "country_b",
    "country_b_qid",
    "side_b",
    "start_year",
    "end_year",
]


def build_dyads(records: pd.DataFrame) -> pd.DataFrame:
    """Pairs of states on opposing, known sides of the same conflict.

    Only participations with a single known side are used. The pair's years
    are the overlap of the two participations; pairs that never overlap are
    dropped. Each pair appears once, ordered by QID.
    """
    if records.empty or "side_qid" not in records.columns:
        return pd.DataFrame(columns=DYAD_COLUMNS)
    known = records[records["side_qid"].notna() & ~records["side_qid"].astype(str).str.contains(";")]
    rows = []
    for conflict, group in known.groupby("conflict_qid", sort=True):
        items = list(group.sort_values("country_qid").itertuples(index=False))
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                if a.side_qid == b.side_qid:
                    continue
                start, end = max(a.start_year, b.start_year), min(a.end_year, b.end_year)
                if start > end:
                    continue
                rows.append(
                    {
                        "conflict_qid": conflict,
                        "conflict_name": a.conflict_name,
                        "country_a": a.country,
                        "country_a_qid": a.country_qid,
                        "side_a": a.side,
                        "country_b": b.country,
                        "country_b_qid": b.country_qid,
                        "side_b": b.side,
                        "start_year": int(start),
                        "end_year": int(end),
                    }
                )
    return pd.DataFrame(rows, columns=DYAD_COLUMNS)


CONFLICT_COLUMNS = [
    "conflict_qid",
    "conflict_name",
    "conflict_type",
    "parent_conflict_qid",
    "root_conflict_qid",
    "war_start_year",
    "war_end_year",
    "duration_estimated",
    "casualties",
    "n_states",
    "n_states_with_side",
]


def summarize_conflicts(records: pd.DataFrame) -> pd.DataFrame:
    """One row per conflict, derived from the participation table."""
    if records.empty:
        return pd.DataFrame(columns=CONFLICT_COLUMNS)
    with_side = records["side_qid"].notna() if "side_qid" in records.columns else False
    df = records.assign(_with_side=with_side)
    out = (
        df.groupby("conflict_qid", sort=True)
        .agg(
            conflict_name=("conflict_name", "first"),
            conflict_type=("conflict_type", "first"),
            parent_conflict_qid=("parent_conflict_qid", "first"),
            root_conflict_qid=("root_conflict_qid", "first"),
            war_start_year=("war_start_year", "min"),
            war_end_year=("war_end_year", "max"),
            duration_estimated=("duration_estimated", "all"),
            casualties=("casualties", "max"),
            n_states=("country_qid", "nunique"),
            n_states_with_side=("_with_side", "sum"),
        )
        .reset_index()
    )
    out["n_states_with_side"] = out["n_states_with_side"].astype(int)
    return out[CONFLICT_COLUMNS]
