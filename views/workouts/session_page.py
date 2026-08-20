"""Session - plan, week and session as three dropdowns, and nothing else.

The section's landing page, and the one that gets used standing up holding a
phone. Everything else here is planning, which happens sitting down.

It opens on the next session not ticked off. Changing the plan or the week
re-picks within it rather than showing nothing, so the dropdowns can be moved in
any order and always land somewhere.
"""
from __future__ import annotations

import datetime as dt

import streamlit as st

from core import workout_mutations as wm, workout_queries as wq, workouts
from views.workouts import shared


def render() -> None:
    if not wq.total_plans():
        st.title("Session")
        shared.no_plans()
        return

    plan = shared.pick_plan()
    weeks = wq.weeks(plan["id"])
    if not weeks:
        st.title("Session")
        st.warning("This plan has no weeks yet — add one on the Plan page.")
        return

    chosen = _choose(plan, weeks)
    if chosen is None:
        return

    st.title(workouts.session_title(chosen))
    st.caption(
        f"{plan['name']} · Week {chosen['week_number']}"
        + (f" · {chosen['phase_name']}" if chosen["phase_name"] else "")
        + (f" · **done {shared.day(chosen['done_on'])}**"
           if chosen["done"] else ""))

    _tick(chosen)
    st.divider()
    _sheet(chosen)


def _choose(plan: dict, weeks: list):
    """The three dropdowns. Returns the session to show, or None."""
    # With nothing picked yet, open on what is due next. The week follows from
    # the session rather than being chosen separately, so the two cannot
    # disagree about which week is being looked at.
    if "session_week_id" not in st.session_state:
        due = wq.next_session(plan["id"])
        if due is not None:
            st.session_state["session_week_id"] = due["week_id"]
            st.session_state["session_session_id"] = due["id"]

    labels = {row["id"]: f"Week {row['number']}"
                         + (f" · {row['label']}" if row["label"] else "")
                         + (f" · {row['phase_name']}" if row["phase_name"] else "")
                         + f" ({row['sessions_done']}/{row['sessions']})"
              for row in weeks}
    ids = list(labels)
    stored = st.session_state.get("session_week_id")
    week_id = st.selectbox("Week", ids,
                           index=ids.index(stored) if stored in ids else 0,
                           format_func=lambda value: labels[value])
    st.session_state["session_week_id"] = week_id

    sessions = wq.sessions(week_id=week_id)
    if not sessions:
        st.info("Nothing planned for that week yet — the Build page has the "
                "form.")
        return None

    titles = {row["id"]: ("✓ " if row["done"] else "")
                         + workouts.session_title(row) for row in sessions}
    session_ids = list(titles)
    stored = st.session_state.get("session_session_id")
    session_id = st.selectbox(
        "Session", session_ids,
        index=session_ids.index(stored) if stored in session_ids else 0,
        format_func=lambda value: titles[value])
    st.session_state["session_session_id"] = session_id
    return wq.session(session_id)


def _tick(session: dict) -> None:
    """The tick, above the session rather than below it."""
    if session["done"]:
        left, right = st.columns([3, 1])
        left.success(f"Ticked off {shared.day(session['done_on'])}"
                     + (f" — {session['done_note']}" if session["done_note"]
                        else ""))
        if right.button("Un-tick"):
            wm.tick_session(session["id"], False)
            st.rerun()
        return

    with st.form("tick_session"):
        left, right = st.columns([1, 3])
        when = left.date_input("Done on", value=dt.date.today(),
                               max_value=dt.date.today(), format="DD/MM/YYYY")
        note = right.text_input("How it went", placeholder="optional")
        if st.form_submit_button("Tick it off", type="primary"):
            try:
                wm.tick_session(session["id"], True, when, note)
                st.rerun()
            except workouts.InvalidWorkout as exc:
                st.error(str(exc))


def _sheet(session: dict) -> None:
    """One table per exercise, not one for the session.

    A set has five things to say about it and a phone is 375px wide, so the
    exercise name is a heading and only the sets are tabular - which is what
    stops the whole thing needing a sideways scroll to find out what you are
    meant to be lifting.
    """
    for item in wq.session_sheet(session["id"]):
        tags = []
        if item["resolved_reps_mode"] == "per_side":
            tags.append("per side")
        if item["resolved_weight_mode"] == "per_dumbbell":
            tags.append("per dumbbell")
        st.markdown(f"**{item['name']}**"
                    + (f" · {' · '.join(tags)}" if tags else ""))
        st.dataframe(
            [{"Set": f"{shared.set_type_label(row)} "
                     f"{workouts.set_label(row)}",
              "Reps": workouts.fmt_reps(row["reps_low"], row["reps_high"],
                                        row["reps_mode"]),
              "Weight": workouts.fmt_load(row),
              "Rest": row["rest"] or "",
              "Notes": row["cue"] or ""}
             for row in item["sets"]],
            hide_index=True, width="stretch")
        if item["note"]:
            st.caption(item["note"])

    if session["week_note"]:
        st.divider()
        st.subheader(f"Week {session['week_number']} notes")
        for line in session["week_note"].split("\n"):
            st.markdown(f"- {line}")
