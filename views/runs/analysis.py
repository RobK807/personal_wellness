"""Analysis - performance split by run type and by effort type.

Pace across a group is its total time over its total distance rather than the
mean of each run's pace: a 20 km plod and a 2 km sprint should not count
equally towards how fast the running was.
"""
from __future__ import annotations

import streamlit as st

from core import metrics, runs
from views import run_charts as rc
from views import run_frames as frames
from views.runs import filters


def _totalled(row: dict) -> str:
    """What the reps came to between them, in the unit the session was set in."""
    if row.get("interval_total_km"):
        return f"{row['interval_total_km']:,.1f} km"
    if row.get("interval_total_s"):
        return runs.fmt_duration(row["interval_total_s"])
    return "—"


def _intervals(args: dict) -> None:
    """The structured sessions, and the ones still waiting to be filled in."""
    sessions = frames.interval_sessions(**args)
    outstanding = frames.intervals_outstanding(**args)
    if sessions.empty and outstanding.empty:
        return

    st.subheader("Interval sessions")
    if not sessions.empty:
        totals = frames.interval_totals(**args)
        st.dataframe(
            [{"Date": metrics.period_label("daily", row["day"]),
              "Session": runs.interval_summary(row),
              "Set by": row["interval_type"],
              "Reps": row["interval_count"] or "—",
              # One column, because only one of the two length boxes applies:
              # '1k' for a session set by distance, '0:10' for one set by time.
              "Each": runs.interval_length(row) or "—",
              "Pace each": (runs.fmt_pace(row["interval_pace_s"])
                            if row["interval_pace_s"] else "—"),
              "Reps totalled": _totalled(row),
              "Whole run": f"{row['distance_km']:,.1f} km at "
                           f"{runs.fmt_pace(row['pace_s'])}"}
             for row in sessions.to_dict("records")],
            hide_index=True, width="stretch")

        line = (f"{totals['sessions']} session"
                f"{'' if totals['sessions'] == 1 else 's'}")
        if totals["reps"]:
            line += f", {totals['reps']} reps"
        if totals["rep_km"]:
            line += f" covering {totals['rep_km']:,.1f} km"
        if totals["rep_seconds"]:
            line += (f"{' and' if totals['rep_km'] else ' totalling'} "
                     f"{runs.fmt_duration(totals['rep_seconds'])}")
        if totals["best_pace_s"]:
            line += f", quickest at {runs.fmt_pace(totals['best_pace_s'], True)}"
        st.caption(
            line + ". *Each* is one rep in the unit its own session was set in "
            "— a distance for **8 x 1k**, a time for **10 x 0:10** — and *Pace "
            "each* is the pace held across them, which is a different figure "
            "from the time a rep took unless the reps were kilometres. *Whole "
            "run* is there for contrast: the gap between the two is the "
            "warm-up, the recoveries and the warm-down."
        )

    if not outstanding.empty:
        st.warning(f"{len(outstanding)} run"
                   f"{'' if len(outstanding) == 1 else 's'} flagged as "
                   f"intervals {'has' if len(outstanding) == 1 else 'have'} no "
                   f"detail recorded yet — the Log page has the form.")
    st.divider()


def render() -> None:
    st.title("Analysis")

    if not frames.total_runs():
        st.warning("No runs recorded yet.")
        return

    chosen = filters.draw("analysis")
    args = filters.as_args(chosen)
    totals = frames.totals(**args)

    if not totals["runs"]:
        st.info("Nothing matches those filters.")
        return

    columns = st.columns(4)
    columns[0].metric("Runs", f"{totals['runs']:,}")
    columns[1].metric("Distance", f"{totals['distance_km'] or 0:,.0f} km")
    columns[2].metric("Time", runs.fmt_duration(totals["duration_s"],
                                                force_hours=True))
    columns[3].metric("Average pace", runs.fmt_pace(totals["pace_s"], True))
    st.caption(filters.describe(chosen))

    st.divider()

    for split, heading in (("run_type", "By run type"),
                           ("effort_type", "By effort type")):
        st.subheader(heading)
        rows = frames.by_split(split, **args)
        st.dataframe(frames.for_display(rows, drop=["first_day"]),
                     hide_index=True, width="stretch",
                     column_config=frames.column_config())

        left, right = st.columns(2)
        chart = rc.category_bars(rows, "pace_s", "Pace (min/km)", is_pace=True)
        if chart is not None:
            left.markdown("**Pace** — further right is quicker")
            left.altair_chart(chart, width="stretch")
        chart = rc.category_bars(rows, "distance_km", "Distance (km)")
        if chart is not None:
            right.markdown("**Distance**")
            right.altair_chart(chart, width="stretch")

    st.divider()

    st.subheader("Every run")
    every = frames.runs_list(**args, newest_first=False)
    colour_by = st.radio("Colour by", ["effort_type", "run_type"],
                         format_func=lambda name: name.replace("_", " ")
                         .capitalize(), horizontal=True, key="scatter_colour")
    chart = rc.scatter(every, colour_by)
    if chart is not None:
        st.altair_chart(chart, width="stretch")
    st.caption("How far against how fast. A summary row says threshold runs "
               "average 4:39/km; this says whether that is one habit or two "
               "averaged into a number that describes neither.")

    st.divider()

    st.subheader("Run type against effort type")
    cross = frames.cross_tab(**args)
    st.dataframe(frames.for_display(cross), hide_index=True, width="stretch",
                 column_config=frames.column_config())
    st.caption("The combinations that are not the obvious ones are the point "
               "of this grid — Race/Race says nothing, but the threshold runs "
               "that happened to be weighted might.")

    st.divider()

    _intervals(args)

    st.subheader("By distance")
    ladder = frames.by_breakdown(**args)
    st.dataframe(frames.for_display(ladder, drop=["last_day"]),
                 hide_index=True, width="stretch",
                 column_config=frames.column_config())
    chart = rc.ladder_bars(ladder)
    if chart is not None:
        st.altair_chart(chart, width="stretch")
    st.caption(
        "Every rung of Strava's ladder: the fastest stretch of that length "
        "found inside each run long enough to contain one. So *5K* is not a 5K "
        "race — it is the best 5K within each run of at least 5 km, which is "
        "why the count falls away as the distances grow. Splits the source "
        "sheet contradicts itself about are set aside; *Set aside* says how "
        "many, and Admin lists them."
    )

    st.divider()

    st.subheader("Over time")
    grain = st.segmented_control("Grouped", list(frames.GRAINS),
                                 default="monthly") or "monthly"
    periods = frames.by_period(grain, **args)
    chart = rc.volume(periods, grain)
    if chart is not None:
        st.altair_chart(chart, width="stretch")
    chart = rc.pace_line(periods, grain)
    if chart is not None:
        st.altair_chart(chart, width="stretch")
