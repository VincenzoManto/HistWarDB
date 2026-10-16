"""Build docs/assets/three_centuries.json for docs/three-centuries.html from a snapshot.

Usage::

    python scripts/build_three_centuries.py SNAPSHOT_DIR docs/assets/three_centuries.json

Everything the page draws is derived here from one snapshot, so the page and
the numbers it shows can be regenerated together.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from histwardb import HistWarDB  # noqa: E402

GROUPS = ["Interstate and other wars", "Civil war", "Independence and liberation", "Colonial war", "Series of wars"]
WORLD_WARS = {"Q361": "World War I", "Q362": "World War II"}
CHORD_STATES = 16


def group(conflict_type) -> int:
    t = str(conflict_type or "")
    if "series of wars" in t:
        return 4
    if "civil war" in t:
        return 1
    if any(k in t for k in ("independence", "liberation", "revolution")):
        return 2
    if "colonial" in t:
        return 3
    return 0


def main(snapshot: str, out_path: str) -> None:
    db = HistWarDB.from_snapshot(snapshot)
    parts, grid = db.participations, db.df_long
    conflicts = db.conflicts

    # Conflicts: compact rows for the spiral, the packing and the stream.
    months = None
    if {"war_start_month", "war_end_month"} <= set(parts.columns):
        months = parts.groupby("conflict_qid").agg(sm=("war_start_month", "min"), em=("war_end_month", "max"))
    rows = []
    for c in conflicts.itertuples(index=False):
        row = {
            "q": c.conflict_qid, "n": c.conflict_name, "g": group(c.conflict_type),
            "s": int(c.war_start_year), "e": int(c.war_end_year), "k": int(c.n_states),
            "x": bool(c.duration_estimated),
            "r": c.root_conflict_qid if c.root_conflict_qid != c.conflict_qid else None,
        }
        if months is not None and c.conflict_qid in months.index:
            sm, em = months.loc[c.conflict_qid]
            row["sm"] = None if pd.isna(sm) else int(sm)
            row["em"] = None if pd.isna(em) else int(em)
        rows.append(row)

    # States: lifespan, war-years and the years at war, as runs [start, end, max conflicts].
    # The grid is monthly from 0.5 on: collapse to one row per state-year, keeping the
    # most conflicts active in any month of that year.
    war = grid[grid["War_State"] == 1]
    war = war.assign(n=war["Active_Conflicts"].str.count("; ") + 1)
    war = war.groupby(["Country_QID", "Year"], as_index=False)["n"].max()
    states = []
    first_war = war.groupby("Country_QID")["Year"].min()
    for st in parts.drop_duplicates("country_qid").itertuples(index=False):
        years = war[war["Country_QID"] == st.country_qid].sort_values("Year")
        runs, prev = [], None
        for y, n in zip(years["Year"], years["n"]):
            if prev is not None and y == prev[1] + 1:
                prev[1], prev[2] = y, max(prev[2], int(n))
            else:
                prev = [int(y), int(y), int(n)]
                runs.append(prev)
        states.append({
            "q": st.country_qid, "n": st.country,
            "b": None if pd.isna(st.country_start) else int(st.country_start),
            "d": None if pd.isna(st.country_end) else int(st.country_end),
            "w": int(len(years)), "f": int(first_war.get(st.country_qid, 0)), "runs": runs,
        })

    # States at war per year by conflict type, for the stream.
    ctype = {r["q"]: r["g"] for r in rows}
    per_year = {y: [0] * len(GROUPS) for y in range(db.start_year, db.end_year + 1)}
    for p in parts.itertuples(index=False):
        for y in range(max(p.start_year, db.start_year), min(p.end_year, db.end_year) + 1):
            per_year[y][ctype[p.conflict_qid]] += 1
    stream = [[y, *v] for y, v in sorted(per_year.items())]

    # Co-participation among the most war-active states, for the chord.
    top = [s["q"] for s in sorted(states, key=lambda s: -s["w"])[:CHORD_STATES]]
    by_conflict = parts[parts["country_qid"].isin(top)].groupby("conflict_qid")["country_qid"].apply(set)
    pair = Counter()
    for members in by_conflict:
        for a, b in combinations(sorted(members), 2):
            pair[(a, b)] += 1
    idx = {q: i for i, q in enumerate(top)}
    matrix = [[0] * len(top) for _ in top]
    for (a, b), n in pair.items():
        matrix[idx[a]][idx[b]] = matrix[idx[b]][idx[a]] = n
    names = dict(zip(parts["country_qid"], parts["country"]))

    # World-war networks: states, sides and opposing pairs.
    dy = db.dyads
    networks = {}
    for qid, title in WORLD_WARS.items():
        d = dy[dy["conflict_qid"] == qid]
        nodes = {}
        for a, sa, b, sb in zip(d["country_a"], d["side_a"], d["country_b"], d["side_b"]):
            nodes.setdefault(a, sa)
            nodes.setdefault(b, sb)
        networks[title] = {
            "nodes": [{"id": k, "side": v} for k, v in sorted(nodes.items())],
            "links": [[a, b] for a, b in zip(d["country_a"], d["country_b"])],
        }

    # Three powers by decade: share of years at war and conflicts begun by kind.
    # Lineages follow the COW crosswalk: Russia 365 (Tsardom, Empire, USSR, Federation),
    # China 710 (Qing, Republic, People's Republic), Europe = COW 200-395 minus Russia.
    cow_of = dict(zip(parts["country_qid"], parts["cow_code"]))
    lineages = {
        "Russia": lambda c: c == 365,
        "China": lambda c: c == 710,
        "Europe": lambda c: 200 <= c <= 395 and c != 365,
    }
    decades = list(range(db.start_year // 10 * 10, db.end_year + 1, 10))
    war_years = set(zip(war["Country_QID"], war["Year"]))
    powers = {}
    for name, member in lineages.items():
        qids = {q for q, c in cow_of.items() if pd.notna(c) and member(int(c))}
        per = {d: {"exist": 0, "war": 0, "began": [0] * len(GROUPS)} for d in decades}
        for s in states:
            if s["q"] not in qids:
                continue
            b = max(db.start_year, s["b"] if s["b"] is not None else db.start_year)
            d_ = min(db.end_year, s["d"] if s["d"] is not None else db.end_year)
            for y in range(b, d_ + 1):
                per[y // 10 * 10]["exist"] += 1
                if (s["q"], y) in war_years:
                    per[y // 10 * 10]["war"] += 1
        begun = parts[parts["country_qid"].isin(qids)].drop_duplicates("conflict_qid")
        for c in begun.itertuples(index=False):
            if db.start_year <= c.war_start_year <= db.end_year:
                per[c.war_start_year // 10 * 10]["began"][ctype[c.conflict_qid]] += 1
        powers[name] = {
            "states": sorted(names[q] for q in qids),
            "decades": [[d, per[d]["exist"], per[d]["war"], *per[d]["began"]] for d in decades],
        }

    # Months of war starts and ends, where Wikidata records month precision.
    month_counts = None
    if months is not None:
        month_counts = {
            "start": [int(sum(1 for r in rows if r.get("sm") == m)) for m in range(1, 13)],
            "end": [int(sum(1 for r in rows if r.get("em") == m and not r["x"])) for m in range(1, 13)],
        }

    out = {
        "meta": {
            "retrieved_at": db.retrieved_at, "start_year": db.start_year, "end_year": db.end_year,
            "n_conflicts": len(rows), "n_states": len(states), "n_participations": int(len(parts)),
            "state_years_at_war": int(len(war)), "groups": GROUPS,
        },
        "conflicts": rows,
        "states": states,
        "stream": stream,
        "chord": {"names": [names[q] for q in top], "matrix": matrix},
        "networks": networks,
        "powers": powers,
        "months": month_counts,
    }
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{out_path}: {Path(out_path).stat().st_size:,} bytes; months={'yes' if month_counts else 'no'}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
