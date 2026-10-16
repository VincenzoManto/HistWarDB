"""Validate a HistWarDB snapshot against Correlates of War and UCDP/PRIO.

Usage::

    python scripts/validate.py SNAPSHOT_DIR CROSSWALK_CSV COW_INTERSTATE_CSV \
        COW_EXTRASTATE_CSV UCDP_ACD_CSV OUT_DIR

Reference data (not redistributed with HistWarDB; download it yourself):

* COW Inter-State War Data v4.0 and Extra-State War Data v4.0,
  https://correlatesofwar.org/data-sets/cow-war/
* UCDP/PRIO Armed Conflict Dataset (tested with v25.1),
  https://ucdp.uu.se/downloads/

Writes ``OUT_DIR/report.md`` and ``OUT_DIR/cow_interstate_wars.csv`` (one
row per COW inter-state war with its best HistWarDB match).

All comparisons are at the level of country-years or wars. The reference
datasets use battle-death thresholds (COW: 1,000 per war; UCDP: 25 per year,
"war" intensity: 1,000 per year) while HistWarDB has none, so disagreement is
expected in both directions; the report separates the two.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from histwardb import HistWarDB

COW_FIRST_YEAR, COW_LAST_YEAR = 1816, 2003
UCDP_FIRST_YEAR = 1946


def load_crosswalk(path):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    by_cow, by_gw = defaultdict(set), defaultdict(set)
    for r in rows:
        if r["cow_code"]:
            by_cow[int(r["cow_code"])].add(r["country_qid"])
        if r["gw_code"]:
            by_gw[int(r["gw_code"])].add(r["country_qid"])
    return rows, by_cow, by_gw


def cow_participations(path):
    """COW war-participant rows with their (up to two) active spells."""
    text = Path(path).read_bytes().decode("latin-1").replace("\r\n", "\n").replace("\r", "\n")
    rows = [{k.strip(): v for k, v in r.items()} for r in csv.DictReader(text.splitlines())]
    out = []
    for r in rows:
        spells = []
        for i in ("1", "2"):
            s, e = int(r[f"StartYear{i}"]), int(r[f"EndYear{i}"])
            if s > 0:
                spells.append((s, e if e > 0 else s))
        # Inter-state rows have one state ("ccode", "Side"); extra-state rows
        # have up to two ("ccode1" for side A, "ccode2" for side B, -8 when
        # that side is not a system member).
        if "ccode" in r:
            members = [(int(r["ccode"]), r.get("Side"))]
        else:
            members = [(int(r["ccode1"]), "1"), (int(r["ccode2"]), "2")]
        for ccode, side in members:
            if ccode > 0:
                out.append({"war": int(r["WarNum"]), "name": r["WarName"], "ccode": ccode,
                            "side": side, "spells": spells})
    return out


def cow_war_years(parts):
    years = set()
    for p in parts:
        for s, e in p["spells"]:
            years |= {(p["ccode"], y) for y in range(s, e + 1)}
    return years


def ucdp_country_years(path, min_intensity):
    df = pd.read_csv(path)
    df = df[df["intensity_level"] >= min_intensity]
    years = set()
    for r in df.itertuples(index=False):
        for col in ("gwno_a", "gwno_a_2nd", "gwno_b", "gwno_b_2nd"):
            value = getattr(r, col)
            if pd.isna(value) or str(value).strip() == "":
                continue
            for code in str(value).split(","):
                code = code.strip()
                if code.isdigit():
                    years.add((int(code), int(r.year)))
    return years


def histwardb_years(grid, by_code, first, last):
    """Map (code, year) -> at war in HistWarDB, for codes that have a crosswalk entry."""
    at_war = set(zip(grid.loc[grid["War_State"] == 1, "Country_QID"], grid.loc[grid["War_State"] == 1, "Year"]))
    covered = {}
    for code, qids in by_code.items():
        for year in range(first, last + 1):
            covered[(code, year)] = any((q, year) in at_war for q in qids)
    return covered


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "n/a"


def compare_years(reference, mapped, first, last):
    """Recall of reference country-years, and the share of HistWarDB war-years the reference lacks."""
    ref = {(c, y) for c, y in reference if first <= y <= last and (c, y) in mapped}
    hits = sum(1 for k in ref if mapped[k])
    ours = {k for k, v in mapped.items() if v and first <= k[1] <= last}
    extra = len(ours - reference)
    return {"reference": len(ref), "found": hits, "ours": len(ours), "not_in_reference": extra}


def match_cow_wars(parts, records, by_cow):
    """Best HistWarDB conflict for each COW war, by number of shared states with overlapping years."""
    by_war = defaultdict(list)
    for p in parts:
        by_war[(p["war"], p["name"])].append(p)
    rec = records[["conflict_qid", "conflict_name", "country_qid", "start_year", "end_year", "side_qid"]]
    by_qid = defaultdict(list)
    for r in rec.itertuples(index=False):
        by_qid[r.country_qid].append(r)

    rows = []
    for (num, name), members in sorted(by_war.items()):
        start = min(s for p in members for s, _ in p["spells"])
        end = max(e for p in members for _, e in p["spells"])
        votes = defaultdict(set)
        sides = defaultdict(dict)
        for p in members:
            for qid in by_cow.get(p["ccode"], ()):
                for r in by_qid.get(qid, ()):
                    if r.start_year <= end + 1 and start - 1 <= r.end_year:
                        votes[(r.conflict_qid, r.conflict_name)].add(p["ccode"])
                        cow_sides, ours = sides[r.conflict_qid].get(p["ccode"], (set(), set()))
                        cow_sides.add(p["side"])
                        if pd.notna(r.side_qid):
                            ours.add(r.side_qid)
                        sides[r.conflict_qid][p["ccode"]] = (cow_sides, ours)
        mapped = sum(1 for p in members if p["ccode"] in by_cow)
        best = max(votes.items(), key=lambda kv: (len(kv[1]), kv[0][0]), default=None)
        side_pairs = side_agreement(sides[best[0][0]]) if best else (0, 0)
        rows.append({
            "cow_war": num,
            "cow_name": name,
            "start": start,
            "end": end,
            "cow_states": len({p["ccode"] for p in members}),
            "cow_states_mapped": mapped,
            "histwardb_conflict_qid": best[0][0] if best else "",
            "histwardb_conflict": best[0][1] if best else "",
            "shared_states": len(best[1]) if best else 0,
            "side_pairs_compared": side_pairs[0],
            "side_pairs_agree": side_pairs[1],
        })
    return pd.DataFrame(rows)


def side_agreement(states):
    """Among state pairs with a single side in both sources, how often do they agree on same/opposite side?

    States that COW codes on both sides (side switchers), or that map to
    several HistWarDB sides, are left out.
    """
    items = []
    for cow_sides, ours in states.values():
        if len(cow_sides) == 1 and len(ours) == 1:
            (ours_side,) = ours
            if ";" not in str(ours_side):
                items.append((next(iter(cow_sides)), ours_side))
    compared = agree = 0
    for i, (cow_a, ours_a) in enumerate(items):
        for cow_b, ours_b in items[i + 1 :]:
            compared += 1
            agree += (cow_a == cow_b) == (ours_a == ours_b)
    return compared, agree


def main(snapshot, crosswalk, cow_inter, cow_extra, ucdp, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db = HistWarDB.from_snapshot(snapshot)
    grid, records = db.df_long, db.participations
    xw_rows, by_cow, by_gw = load_crosswalk(crosswalk)

    inter = cow_participations(cow_inter)
    extra = cow_participations(cow_extra)
    inter_years = cow_war_years(inter)
    extra_years = cow_war_years(extra)
    cow_mapped = histwardb_years(grid, by_cow, COW_FIRST_YEAR, COW_LAST_YEAR)
    c_inter = compare_years(inter_years, cow_mapped, COW_FIRST_YEAR, COW_LAST_YEAR)
    c_both = compare_years(inter_years | extra_years, cow_mapped, COW_FIRST_YEAR, COW_LAST_YEAR)

    wars = match_cow_wars(inter, records, by_cow)
    wars.to_csv(out_dir / "cow_interstate_wars.csv", index=False, lineterminator="\n")
    eligible = wars[wars["cow_states_mapped"] >= 2]
    found2 = int((eligible["shared_states"] >= 2).sum())
    found1 = int((wars["shared_states"] >= 1).sum())

    last_ucdp = int(pd.read_csv(ucdp)["year"].max())
    gw_mapped = histwardb_years(grid, by_gw, UCDP_FIRST_YEAR, last_ucdp)
    u_any = compare_years(ucdp_country_years(ucdp, 1), gw_mapped, UCDP_FIRST_YEAR, last_ucdp)
    u_war = compare_years(ucdp_country_years(ucdp, 2), gw_mapped, UCDP_FIRST_YEAR, last_ucdp)

    by_period = []
    for lo, hi in ((1816, 1899), (1900, 1945), (1946, 2003)):
        sub = eligible[(eligible["start"] >= lo) & (eligible["start"] <= hi)]
        by_period.append((f"{lo}-{hi}", len(sub), int((sub["shared_states"] >= 2).sum())))

    sides_compared = int(wars["side_pairs_compared"].sum())
    sides_agree = int(wars["side_pairs_agree"].sum())
    states_with_codes = sum(1 for r in xw_rows if r["cow_code"])

    lines = [
        "# Validation against Correlates of War and UCDP/PRIO",
        "",
        (f"Snapshot: `{Path(snapshot).name}` (retrieved {db.retrieved_at}). "
        f"Generated by `scripts/validate.py`."),
        "",
        ("HistWarDB has no battle-death threshold; COW requires 1,000 battle deaths per war and UCDP 25 per "
        "year. Agreement is therefore expected to be partial in both directions. \"Not in reference\" "
        "counts HistWarDB war-years that the reference does not code; they are not necessarily errors."),
        "",
        "## Crosswalk coverage",
        "",
        (f"{states_with_codes} of {len(xw_rows)} HistWarDB states have a COW code "
        f"({sum(1 for r in xw_rows if r['gw_code'])} a Gleditsch-Ward code). The others are mostly entities "
        "that were never members of the interstate system (colonies, protectorates, sub-national or pre-1816 "
        "states). Only states with a code enter the comparisons below."),
        "",
        "## COW inter-state wars (1816-2003)",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| COW inter-state wars | {len(wars)} |",
        f"| ... with at least 2 participants that have a HistWarDB code | {len(eligible)} |",
        f"| ... matched to a HistWarDB conflict with >= 2 of those participants | {found2} ({pct(found2, len(eligible))}) |",
        f"| COW wars with at least 1 participant found in a HistWarDB conflict in the same years | {found1} ({pct(found1, len(wars))}) |",
        "",
        "War-level recall by start period (wars with >= 2 coded participants):",
        "",
        "| Period | COW wars | Matched | Recall |",
        "|---|---|---|---|",
        *[f"| {p} | {n} | {m} | {pct(m, n)} |" for p, n, m in by_period],
        "",
        "## Country-years",
        "",
        "| Reference | Reference war-years | Also at war in HistWarDB | Recall | HistWarDB war-years | Not in reference |",
        "|---|---|---|---|---|---|",
        f"| COW inter-state (1816-2003) | {c_inter['reference']} | {c_inter['found']} | {pct(c_inter['found'], c_inter['reference'])} | {c_inter['ours']} | {c_inter['not_in_reference']} ({pct(c_inter['not_in_reference'], c_inter['ours'])}) |",
        f"| COW inter- + extra-state (1816-2003) | {c_both['reference']} | {c_both['found']} | {pct(c_both['found'], c_both['reference'])} | {c_both['ours']} | {c_both['not_in_reference']} ({pct(c_both['not_in_reference'], c_both['ours'])}) |",
        f"| UCDP/PRIO, any intensity ({UCDP_FIRST_YEAR}-{last_ucdp}) | {u_any['reference']} | {u_any['found']} | {pct(u_any['found'], u_any['reference'])} | {u_any['ours']} | {u_any['not_in_reference']} ({pct(u_any['not_in_reference'], u_any['ours'])}) |",
        f"| UCDP/PRIO, war intensity ({UCDP_FIRST_YEAR}-{last_ucdp}) | {u_war['reference']} | {u_war['found']} | {pct(u_war['found'], u_war['reference'])} | {u_war['ours']} | {u_war['not_in_reference']} ({pct(u_war['not_in_reference'], u_war['ours'])}) |",
        "",
        ("COW intra-state wars are not included (the v5.1 file was not available at the time of writing), so "
        "civil-war years in HistWarDB count as \"not in reference\" in the COW rows."),
        "",
        "## Sides",
        "",
        (f"For matched COW wars, pairs of states that have a HistWarDB side (from coalition membership) were "
        f"compared with COW's `Side` coding: {sides_agree} of {sides_compared} pairs agree on whether the two "
        f"states fought on the same or opposite sides ({pct(sides_agree, sides_compared)})."),
        "",
        "## Least-covered COW wars",
        "",
        ("COW inter-state wars with the most coded participants that HistWarDB does not match "
        "(see `cow_interstate_wars.csv` for all):"),
        "",
        "| COW war | Years | COW states | Most states found in one HistWarDB conflict |",
        "|---|---|---|---|",
        *[
            f"| {r.cow_name} | {r.start}-{r.end} | {r.cow_states} | {r.shared_states} |"
            for r in eligible[eligible["shared_states"] < 2].sort_values("cow_states", ascending=False).head(15).itertuples()
        ],
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main(*sys.argv[1:7])
