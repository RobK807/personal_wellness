"""Run tracker overview - the last run, the totals, volume and pace."""
from __future__ import annotations

import streamlit as st

import config
from core import metrics, runs
from views import run_charts as rc
from views import run_frames as frames
from views.runs import filters


def render() -> None:
    st.title("Overview")

    coverage = frames.coverage()
    if not coverage["runs"]:
        st.warning("No runs recorded yet. Import the scraped workbook to bring "
                   "the history across, or log a run on the next page.")
        st.code("python -m core.strava_import --rebuild", language="bash")
        return

    st.caption(
        f"{coverage['runs']:,} runs from "
        f"{metrics.period_label('daily', coverage['first_day'])} to "
        f"{metrics.period_label('daily', coverage['last_day'])} — "
        f"{coverage['distance_km']:,.0f} km in "
        f"{runs.fmt_duration(coverage['duration_s'], force_hours=True)}, with "
        f"{coverage['splits']:,} best efforts recorded inside them."
    )

    chosen = filters.draw("overview")
    args = filters.as_args(chosen)
    totals = frames.totals(**args)

    columns = st.columns(4)
    columns[0].metric("Runs", f"{totals['runs']:,}")
    columns[1].metric("Distance", f"{totals['distance_km'] or 0:,.0f} km")
    columns[2].metric("Time", runs.fmt_duration(totals["duration_s"],
                                                force_hours=True))
    columns[3].metric("Average pace", runs.fmt_pace(totals["pace_s"], True),
                      help=f"Quickest whole run: "
                           f"{runs.fmt_pace(totals['best_pace_s'], True)}")
    st.caption(filters.describe(chosen))

    st.divider()

    last = frames.latest()
    if last:
        st.subheader("Last run")
        st.markdown(
            f"**{metrics.period_label('daily', last['day'])}** — "
            f"{runs.fmt_distance(last['distance_km'])} km in "
            f"{runs.fmt_duration(last['duration_s'])} "
            f"({runs.fmt_pace(last['pace_s'], True)}) · "
            f"{last['run_type']} · {last['effort_type']}"
        )
        ladder = frames.bests_for(last["id"])
        if not ladder.empty:
            st.dataframe(frames.for_display(ladder, drop=["breakdown"])
                         .assign(Distance=ladder["breakdown"])
                         [["Distance", "Time", "Pace"]],
                         hide_index=True, width="stretch")
        else:
            st.caption("No best efforts recorded for this run.")

    st.divider()

    monthly = frames.by_period("monthly", **args)
    st.subheader("Volume")
    chart = rc.volume(monthly, "monthly")
    if chart is not None:
        st.altair_chart(chart, width="stretch")

    st.subheader("Pace")
    chart = rc.pace_line(monthly, "monthly")
    if chart is not None:
        st.altair_chart(chart, width="stretch")
    st.caption("The axis is inverted: quicker is higher. Each point is the "
               "month's total time over its total distance, so a long slow run "
               "counts for more than a short one.")

    st.divider()

    st.subheader("Personal bests")
    bests = frames.personal_bests()
    st.dataframe(
        [{"Distance": row["breakdown"],
          "Time": runs.fmt_duration(row["seconds"]),
          "Pace": runs.fmt_pace(row["pace_s"]),
          "When": metrics.period_label("daily", row["day"]),
          "In": f"a {runs.fmt_distance(row['distance_km'])} km "
                f"{row['run_type'].lower()} run"}
         for row in bests],
        hide_index=True, width="stretch")
    st.caption(f"The single fastest at each distance, across everything rather "
               f"than the filtered window. Records has the top {config.TOP_N}.")

    st.subheader("Recent runs")
    recent = frames.runs_list(limit=10)
    st.dataframe(frames.for_display(recent, drop=["note", "breakdowns"]),
                 hide_index=True, width="stretch",
                 column_config=frames.column_config())
