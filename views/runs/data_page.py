"""Data - every run as a table, filtered."""
from __future__ import annotations

import streamlit as st

from core import runs
from views import run_frames as frames
from views.runs import filters


def render() -> None:
    st.title("Data")

    if not frames.total_runs():
        st.warning("No runs recorded yet.")
        return

    chosen = filters.draw("data")
    args = filters.as_args(chosen)
    totals = frames.totals(**args)

    columns = st.columns(4)
    columns[0].metric("Runs", f"{totals['runs']:,}")
    columns[1].metric("Distance", f"{totals['distance_km'] or 0:,.0f} km")
    columns[2].metric("Time", runs.fmt_duration(totals["duration_s"],
                                                force_hours=True))
    columns[3].metric("Longest",
                      f"{totals['longest_km'] or 0:,.2f} km")
    st.caption(filters.describe(chosen))

    # No paging here: the whole filtered set goes into one scrollable frame,
    # which is what st.dataframe is for and what the Flask side cannot do
    # without sending every row to the browser.
    rows = frames.runs_list(**args)
    st.dataframe(frames.for_display(rows), hide_index=True, width="stretch",
                 height=560, column_config=frames.column_config())
    st.caption("*Splits* is how many rungs of the breakdown ladder the run "
               "reached. Pace is worked out from the distance and the time "
               "rather than stored, so the three columns cannot disagree.")

    with st.expander("One run in detail"):
        _detail(rows)


def _detail(rows) -> None:
    if rows.empty:
        return
    labels = {
        f"{row['day']:%d/%m/%Y} · {runs.fmt_distance(row['distance_km'])} km · "
        f"{runs.fmt_duration(row['duration_s'])} · {row['run_type']}":
            int(row["id"])
        for row in rows.head(200).to_dict("records")
    }
    chosen = st.selectbox("Run", list(labels), index=0, key="detail_run")
    run_id = labels[chosen]
    row = frames.run(run_id)

    columns = st.columns(4)
    columns[0].metric("Distance", f"{row['distance_km']:,.2f} km")
    columns[1].metric("Time", runs.fmt_duration(row["duration_s"]))
    columns[2].metric("Pace", runs.fmt_pace(row["pace_s"], True))
    columns[3].metric("Best efforts", row["breakdowns"])
    if row["note"]:
        st.caption(row["note"])

    if row.get("interval_type"):
        st.markdown(f"**Interval session:** {runs.interval_summary(row)}")
        columns = st.columns(4)
        columns[0].metric("Intervals", row["interval_count"] or "—")
        columns[1].metric("Each", runs.interval_length(row) or "—",
                          help="One rep, in the unit this session was set in.")
        columns[2].metric("Pace each",
                          runs.fmt_pace(row["interval_pace_s"], True)
                          if row["interval_pace_s"] else "—",
                          help="The pace held across the reps, as entered. Not "
                               "the time a rep took, unless the reps were "
                               "kilometres.")
        columns[3].metric(
            "Reps totalled",
            f"{row['interval_total_km']:,.2f} km" if row["interval_total_km"]
            else runs.fmt_duration(row["interval_total_s"])
            if row["interval_total_s"] else "—")
    elif row["run_type"] == "Intervals":
        st.info("Flagged as intervals, but nothing recorded about them yet — "
                "the Log page has the form.")

    ladder = frames.bests_for(run_id)
    if ladder.empty:
        st.caption("No best efforts recorded for this run.")
        return

    positions = {label: frames.is_record(run_id, label)
                 for label in ladder["breakdown"]}
    st.dataframe(
        [{"Distance": item["breakdown"],
          "Time": runs.fmt_duration(item["seconds"]),
          "Pace": runs.fmt_pace(item["pace_s"]),
          "Standing": (f"{runs.medal(positions[item['breakdown']])} best"
                       if positions[item["breakdown"]] else "—"),
          "Flagged": "⚑" if item["suspect"] else ""}
         for item in ladder.to_dict("records")],
        hide_index=True, width="stretch")

    # Hands the run to the Log page rather than navigating there. st.switch_page
    # wants the st.Page object, which only app.py has, and threading it down to
    # every view to save one click is not worth the coupling.
    if st.button("Send this run to the Log page to edit"):
        st.session_state["edit_run_id"] = run_id
        st.success("Open **Log a run** — it will open on this one.")
