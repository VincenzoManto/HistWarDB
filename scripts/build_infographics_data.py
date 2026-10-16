"""Build the JSON data bundle consumed by docs/infographics.html.

Reads the live-fetched participations table (with month precision) and
derives the six chart datasets: radial month barchart, alliance/rivalry
matrix, conflict hierarchy (sunburst), Kaplan-Meier survival curves,
cascading timeline, and duration-vs-casualties scatter.
"""
import json
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

SRC = "scratch_participations_full.csv"
OUT = "docs/assets/infographics_data.json"

df = pd.read_csv(SRC)

# One row per conflict (root-level dedup avoids double counting coalition members).
conflicts = (
    df.sort_values("conflict_qid")
    .drop_duplicates(subset=["conflict_qid"])
    .copy()
)

# ---------------------------------------------------------------------------
# 1. Radial barchart: most common calendar months for war start / end
# ---------------------------------------------------------------------------
start_months = conflicts["war_start_month"].dropna().astype(int)
end_months = conflicts["war_end_month"].dropna().astype(int)
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
radial = {
    "months": MONTH_NAMES,
    "start_counts": [int((start_months == m).sum()) for m in range(1, 13)],
    "end_counts": [int((end_months == m).sum()) for m in range(1, 13)],
    "n_start": int(len(start_months)),
    "n_end": int(len(end_months)),
}

# ---------------------------------------------------------------------------
# 2. Alliance / rivalry matrix: co-occurrence of countries on same vs
#    opposite sides of the same conflict, top 15 most war-active countries
# ---------------------------------------------------------------------------
top_countries = df["country"].value_counts().head(15).index.tolist()
pair_score = defaultdict(int)
for qid, grp in df[df["country"].isin(top_countries)].groupby("conflict_qid"):
    grp = grp.drop_duplicates(subset=["country"])
    rows = grp[["country", "side"]].values.tolist()
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            c1, s1 = rows[i]
            c2, s2 = rows[j]
            if c1 == c2:
                continue
            if pd.notna(s1) and pd.notna(s2):
                score = 2 if s1 == s2 else -1
            else:
                score = 1  # same conflict, side unknown -> weak alliance signal
            key = tuple(sorted((c1, c2)))
            pair_score[key] += score

matrix = {c: {c2: 0 for c2 in top_countries} for c in top_countries}
for (c1, c2), score in pair_score.items():
    matrix[c1][c2] = score
    matrix[c2][c1] = score
alliance_matrix = {
    "countries": top_countries,
    "z": [[matrix[c1][c2] for c2 in top_countries] for c1 in top_countries],
}

# ---------------------------------------------------------------------------
# 3. Sunburst: root conflict -> sub-conflicts (parent/child structure)
# ---------------------------------------------------------------------------
struct = conflicts[["conflict_qid", "conflict_name", "parent_conflict_qid", "root_conflict_qid", "root_conflict_name"]]
struct = struct.drop_duplicates(subset=["conflict_qid"])
name_by_qid = dict(zip(struct["conflict_qid"], struct["conflict_name"]))

ids, labels, parents, values = [], [], [], []
roots_with_children = set(struct.loc[struct["parent_conflict_qid"].notna(), "root_conflict_qid"])
seen = set()
for _, row in struct.iterrows():
    root_qid, root_name = row["root_conflict_qid"], row["root_conflict_name"]
    if root_qid not in seen and root_qid in roots_with_children:
        ids.append(root_qid)
        labels.append(root_name)
        parents.append("")
        values.append(0)
        seen.add(root_qid)
for _, row in struct.iterrows():
    if pd.isna(row["parent_conflict_qid"]):
        continue
    root_qid = row["root_conflict_qid"]
    if root_qid not in seen:
        continue
    cid = row["conflict_qid"]
    if cid in seen:
        continue
    ids.append(cid)
    labels.append(row["conflict_name"])
    parents.append(row["parent_conflict_qid"] if row["parent_conflict_qid"] in seen else root_qid)
    values.append(1)
    seen.add(cid)

ROOT_PALETTE = [
    "#e94560", "#4f9bff", "#2dd4bf", "#eab308", "#8b5cf6", "#f97316",
    "#22c55e", "#ec4899", "#06b6d4", "#a3e635", "#f43f5e", "#818cf8",
]


def _mix(hex_color, with_hex, t):
    c1 = tuple(int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    c2 = tuple(int(with_hex[i : i + 2], 16) for i in (1, 3, 5))
    mixed = tuple(round(c1[k] + (c2[k] - c1[k]) * t) for k in range(3))
    return "#%02x%02x%02x" % mixed


root_index = {qid: i for i, qid in enumerate(ids) if parents[ids.index(qid)] == ""}
parent_by_id = dict(zip(ids, parents))


def _root_of(cid):
    seen_path = set()
    while parent_by_id.get(cid):
        if cid in seen_path:
            break
        seen_path.add(cid)
        cid = parent_by_id[cid]
    return cid


colors = []
for cid, par in zip(ids, parents):
    root = cid if par == "" else _root_of(cid)
    base = ROOT_PALETTE[root_index.get(root, 0) % len(ROOT_PALETTE)]
    colors.append(base if par == "" else _mix(base, "#12121a", 0.45))

sunburst = {"ids": ids, "labels": labels, "parents": parents, "values": values, "colors": colors}

# ---------------------------------------------------------------------------
# 4. Kaplan-Meier survival curves of conflict duration, split by era
# ---------------------------------------------------------------------------
conflicts["duration_years"] = (conflicts["war_end_year"] - conflicts["war_start_year"]).clip(lower=0) + 1


def era(year):
    if year < 1800:
        return "18th Century"
    if year < 1900:
        return "19th Century"
    if year < 1945:
        return "Early 20th Century (pre-WWII)"
    return "Modern Era (post-1945)"


conflicts["era"] = conflicts["war_start_year"].apply(era)


def km_curve(durations, max_t=15):
    durations = sorted(durations)
    n = len(durations)
    surv = 1.0
    t_prev = 0
    xs, ys = [0], [1.0]
    at_risk = n
    for t in range(1, max_t + 1):
        d = sum(1 for x in durations if x == t)
        if at_risk > 0 and d > 0:
            surv *= 1 - d / at_risk
        at_risk -= sum(1 for x in durations if x <= t)
        xs.append(t)
        ys.append(surv)
    return xs, ys


survival = {}
for e, grp in conflicts.groupby("era"):
    xs, ys = km_curve(grp["duration_years"].tolist())
    survival[e] = {"x": xs, "y": ys, "n": int(len(grp))}

# ---------------------------------------------------------------------------
# 5. Cascading timeline: major modern conflicts (1900+, duration >= 2y)
# ---------------------------------------------------------------------------
modern = conflicts[(conflicts["war_start_year"] >= 1900) & (conflicts["duration_years"] >= 2)].copy()
modern = modern.sort_values("war_start_year")
cascade = {
    "names": modern["conflict_name"].tolist(),
    "start": modern["war_start_year"].tolist(),
    "end": modern["war_end_year"].tolist(),
    "duration": modern["duration_years"].tolist(),
}

# ---------------------------------------------------------------------------
# 6. Duration vs estimated casualties (log-log)
# ---------------------------------------------------------------------------
casualty_rows = conflicts.dropna(subset=["casualties"])
casualty_rows = casualty_rows[casualty_rows["casualties"] > 0]
scatter = {
    "names": casualty_rows["conflict_name"].tolist(),
    "duration": casualty_rows["duration_years"].tolist(),
    "casualties": casualty_rows["casualties"].tolist(),
}

bundle = {
    "radial": radial,
    "alliance_matrix": alliance_matrix,
    "sunburst": sunburst,
    "survival": survival,
    "cascade": cascade,
    "scatter": scatter,
    "meta": {
        "n_conflicts": int(len(conflicts)),
        "n_participations": int(len(df)),
    },
}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(bundle, f, ensure_ascii=False)

print(f"Wrote {OUT}")
print({k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in bundle.items()})
