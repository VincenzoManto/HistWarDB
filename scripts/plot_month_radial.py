"""Radial chart of war start/end months.

Works whether or not the installed histwardb has month/day precision
columns (added after 0.4.0): falls back to a clear message instead of
silently misparsing year-only columns with pd.to_datetime.
"""

import plotly.graph_objects as go

from histwardb import HistWarDB

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def plot_start_end_months(gwd: HistWarDB) -> go.Figure:
    parts = gwd.participations.drop_duplicates("conflict_qid")

    if "war_start_month" not in parts.columns or "war_end_month" not in parts.columns:
        raise RuntimeError(
            "This histwardb version has no month/day precision "
            "(war_start_month/war_end_month). Install from the GitHub "
            "main branch to get it: "
            "pip install git+https://github.com/VincenzoManto/histwardb.git"
        )

    start_months = parts["war_start_month"].dropna().astype(int)
    end_months = parts["war_end_month"].dropna().astype(int)

    coverage = len(start_months) / len(parts) if len(parts) else 0.0
    print(f"{len(start_months)} of {len(parts)} conflicts have a real start month ({coverage:.0%} coverage)")

    start_counts = start_months.value_counts().reindex(range(1, 13), fill_value=0)
    end_counts = end_months.value_counts().reindex(range(1, 13), fill_value=0)

    fig = go.Figure()
    fig.add_trace(
        go.Barpolar(
            r=start_counts.values,
            theta=[m * 360 / 12 for m in range(12)],
            name="War Start",
            marker_color="crimson",
            opacity=0.75,
            hovertext=MONTH_NAMES,
        )
    )
    fig.add_trace(
        go.Barpolar(
            r=end_counts.values,
            theta=[m * 360 / 12 for m in range(12)],
            name="War End",
            marker_color="royalblue",
            opacity=0.75,
            hovertext=MONTH_NAMES,
        )
    )
    fig.update_layout(
        title="Most Common Months for War Start and End (real precision only)",
        polar=dict(angularaxis=dict(tickvals=[m * 360 / 12 for m in range(12)], ticktext=MONTH_NAMES)),
        template="plotly_dark",
    )
    return fig


if __name__ == "__main__":
    gwd = HistWarDB.from_snapshot("data/snapshots/2026-10-14")
    fig = plot_start_end_months(gwd)
    fig.write_html("month_radial.html")
    print("Wrote month_radial.html")
