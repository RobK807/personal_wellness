"""Records - the top five at each breakdown distance.

Opens on everything rather than the last year, because a personal best that
expires after twelve months is not what the word means.
"""
from __future__ import annotations

import streamlit as st

import config
from core import metrics, runs
from views import run_frames as frames
from views.runs import filters


def render() -> None:
    st.title("Records")
    st.caption(
        f"The fastest {config.TOP_N} at each of the distances the "
        f"spreadsheet's *Breakdown* column tracks. A single run appears in as "
        f"many tables as it reached rungs — a half-marathon holds a fastest "
        f"400m as well as a fastest 20K — and never twice in the same one. A "
        f"time that has been matched but not beaten keeps its place."
    )

    if not frames.total_runs():
        st.warning("No runs recorded yet.")
        return

    chosen = filters.draw("records", default_span="All")

    left, right = st.columns([1, 2])
    top = left.segmented_control("Show", [3, 5, 10, 25],
                                 default=config.TOP_N,
                                 format_func=lambda n: f"Top {n}") or config.TOP_N
    flagged = frames.suspect_count()
    include_suspect = False
    if flagged:
        include_suspect = right.toggle(
            f"Include the {flagged} flagged splits", value=False,
            help="Splits the source sheet contradicts itself about — longer "
                 "than the whole run, or quicker than a shorter split inside "
                 "it. Admin lists them.")

    if flagged and not include_suspect:
        st.info(f"{flagged} splits from the source sheet contradict the run "
                f"they came from, so they are left out of these tables. Admin "
                f"says what and why.")

    tables = frames.records(top=top, include_suspect=include_suspect,
                            **filters.as_args(chosen))
    if not tables:
        st.info("Nothing matches those filters, so there is nothing to rank.")
        return

    labels = list(tables)
    for start in range(0, len(labels), 3):
        columns = st.columns(3)
        for column, label in zip(columns, labels[start:start + 3]):
            _table(column, label, tables[label])


def _table(column, label: str, frame) -> None:
    # Rounded to the metre, as the Flask side shows it: a half mile is
    # 0.804672 km exactly and nobody needs the last three digits of that.
    km = round(config.BREAKDOWN_KM[label], 3)
    column.markdown(f"**{label}** &nbsp; <span style='color:#888;font-size:.8em'>"
                    f"{km:g} km</span>", unsafe_allow_html=True)
    column.dataframe(
        [{"#": runs.medal(int(row["position"])),
          "Time": runs.fmt_duration(row["seconds"]),
          "Pace": runs.fmt_pace(row["pace_s"]),
          "When": metrics.period_label("daily", row["day"]),
          "Run": f"{runs.fmt_distance(row['distance_km'])} km "
                 f"{row['run_type'].lower()}"
                 + (" ⚑" if row.get("suspect") else "")}
         for row in frame.to_dict("records")],
        hide_index=True, width="stretch",
    )
