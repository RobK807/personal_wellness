"""Tracker - tick sessions off, and see how far through a plan you are."""
from __future__ import annotations

import datetime as dt

import streamlit as st

from core import workout_mutations as wm, workout_queries as wq, workouts
from views.workouts import shared


def render() -> None:
    st.title("Tracker")

    if not wq.total_plans():
        shared.no_plans()
        return

    plan = shared.pick_plan()
    totals = wq.totals(plan["id"])

    columns = st.columns(4)
    columns[0].metric("Sessions done",
                      f"{totals['sessions_done']}/{totals['sessions']}")
    columns[1].metric("Through", f"{totals['progress'] * 100:.0f}%")
    columns[2].metric("Weeks", totals["weeks"])
    columns[3].metric("Prescribed sets", f"{totals['sets']:,}")
    st.progress(totals["progress"])

    _next_up(plan)
    st.divider()
    _grid(plan)
    st.divider()
    _recent(plan)
    st.divider()
    _volume(plan)


def _next_up(plan: dict) -> None:
    row = wq.next_session(plan["id"])
    if row is None:
        st.success("Every session in this plan is ticked off. Copy it as a "
                   "template on the Plan page to start the next block.")
        return

    st.subheader(f"Next up — week {row['week_number']}, "
                 f"{workouts.session_title(row)}")
    with st.expander("What is in it", expanded=False):
        shared.session_sheet(row["id"])
    with st.form("tick_next"):
        left, right = st.columns([1, 3])
        when = left.date_input("Done on", value=dt.date.today(),
                               max_value=dt.date.today(), format="DD/MM/YYYY")
        note = right.text_input("How it went", placeholder="optional")
        if st.form_submit_button("Tick it off", type="primary"):
            try:
                wm.tick_session(row["id"], True, when, note)
                st.rerun()
            except workouts.InvalidWorkout as exc:
                st.error(str(exc))


def _grid(plan: dict) -> None:
    st.subheader("Every week")
    st.caption("The workbook's Tracker sheet: a row per week, a tick per "
               "session.")
    weeks = wq.week_progress(plan["id"])
    sessions = wq.sessions(plan_id=plan["id"])
    by_week: dict = {}
    for row in sessions:
        by_week.setdefault(row["week_id"], []).append(row)

    st.dataframe([{
        "Week": row["number"],
        "Label": row["label"] or "",
        "Phase": row["phase_name"] or "",
        "Type": row["cycle_type"] or "",
        "Sessions": " ".join(
            ("✓" if item["done"] else "○") + f"S{item['number']}"
            for item in by_week.get(row["week_id"], [])) or "—",
        "Done": f"{row['sessions_done']}/{row['sessions']}",
    } for row in weeks], hide_index=True, width="stretch")

    if not sessions:
        return
    labels = {row["id"]: f"Week {row['week_number']} · "
                         f"{workouts.session_title(row)}"
                         + (" · done" if row["done"] else "")
              for row in sessions}
    left, middle, right = st.columns([3, 1, 1])
    chosen = left.selectbox("Tick a session", list(labels), index=None,
                            placeholder="Pick one",
                            format_func=lambda value: labels[value])
    current = next((row for row in sessions if row["id"] == chosen), None)
    if middle.button("Tick off", disabled=chosen is None
                     or bool(current and current["done"])):
        wm.tick_session(chosen, True)
        st.rerun()
    if right.button("Un-tick", disabled=chosen is None
                    or not (current and current["done"])):
        wm.tick_session(chosen, False)
        st.rerun()

    week_labels = {row["week_id"]: shared.week_heading(
        {"number": row["number"], "label": row["label"],
         "phase_name": row["phase_name"], "cycle_type": row["cycle_type"]})
        for row in weeks}
    left, middle, right = st.columns([3, 1, 1])
    week_choice = left.selectbox("Or a whole week", list(week_labels),
                                 index=None, placeholder="Pick a week",
                                 format_func=lambda value: week_labels[value])
    if middle.button("Tick all", disabled=week_choice is None):
        changed = wm.tick_week(week_choice, True)
        st.success(f"{changed} ticked off.")
        st.rerun()
    if right.button("Clear", disabled=week_choice is None):
        changed = wm.tick_week(week_choice, False)
        st.success(f"{changed} un-ticked.")
        st.rerun()


def _recent(plan: dict) -> None:
    rows = wq.done_log(plan["id"], 15)
    if not rows:
        return
    st.subheader("Recently done")
    st.dataframe([{
        "Date": shared.day(row["done_on"]),
        "Week": row["week_number"],
        "Session": workouts.session_title(row),
        "Note": row["done_note"] or "",
    } for row in rows], hide_index=True, width="stretch")


def _volume(plan: dict) -> None:
    rows = wq.volume_by_exercise(plan["id"], done_only=True)
    if not rows:
        return
    st.subheader("Volume done, by movement")
    st.dataframe([{
        "Exercise": row["exercise_name"],
        "Sets": row["sets"],
        "Reps": row["reps"],
        "Volume (kg)": row["volume_kg"],
        "Sets with no weight": row["sets_without_weight"] or 0,
    } for row in rows], hide_index=True, width="stretch")
    st.caption(
        "Prescribed volume for the sessions ticked off — sets × reps × weight, "
        "doubled where the weight is per dumbbell or the reps are per side. "
        "Warm-ups are left out, because they are not the work. *Sets with no "
        "weight* counts the ones the plan leaves to the day, so the total is "
        "never quietly short without saying so.")
