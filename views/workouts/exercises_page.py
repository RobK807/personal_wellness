"""Exercises - the movement catalogue behind every dropdown in this section."""
from __future__ import annotations

import streamlit as st

import config
from core import workout_mutations as wm, workout_queries as wq, workouts


def render() -> None:
    st.title("Exercises")
    st.caption(
        "The catalogue behind every dropdown in this section. A closed list, "
        "for the same reason the run types are: a movement typed freely three "
        "different ways is three movements as far as any total is concerned.")

    _add()
    st.divider()
    _catalogue()


def _add() -> None:
    st.subheader("Add an exercise")
    with st.form("add_exercise", clear_on_submit=True):
        left, middle, right = st.columns(3)
        name = left.text_input("Name", placeholder="Incline Dumbbell Press")
        reps_mode = middle.selectbox(
            "Reps counted", config.REPS_MODES,
            format_func=lambda value: config.REPS_MODE_LABELS[value])
        weight_mode = right.selectbox(
            "Weight counted", config.WEIGHT_MODES,
            format_func=lambda value: config.WEIGHT_MODE_LABELS[value])
        left, right = st.columns(2)
        bodyweight = left.checkbox("Bodyweight movement — no 1RM, progresses "
                                   "by added weight")
        note = right.text_input("Note")
        if st.form_submit_button("Add", type="primary"):
            try:
                saved = wm.save_exercise({
                    "name": name, "reps_mode": reps_mode,
                    "weight_mode": weight_mode, "is_bodyweight": bodyweight,
                    "note": note})
                st.success(f"Added '{saved['name']}'.")
                st.rerun()
            except workouts.InvalidWorkout as exc:
                st.error(str(exc))
    st.caption("A **bodyweight** movement has no 1RM, so the builder offers it "
               "*Bodyweight (+ added)* instead of a percentage — and refuses "
               "the percentage, which would be a percentage of nothing.")


def _catalogue() -> None:
    st.subheader("The catalogue")
    rows = wq.catalogue_with_usage()
    st.dataframe([{
        "Exercise": row["name"],
        "Reps": config.REPS_MODE_LABELS[row["reps_mode"]],
        "Weight": config.WEIGHT_MODE_LABELS[row["weight_mode"]],
        "Type": "Bodyweight" if row["is_bodyweight"] else "Has a 1RM",
        "Sessions": row["sessions"],
        "In the dropdown": "no" if row["retired"] else "yes",
        "Note": row["note"] or "",
    } for row in rows], hide_index=True, width="stretch")

    labels = {row["id"]: row["name"] + (" (retired)" if row["retired"] else "")
              for row in rows}
    chosen = st.selectbox("Change one", list(labels), index=None,
                          placeholder="Pick an exercise",
                          format_func=lambda value: labels[value])
    if chosen is None:
        return
    current = next(row for row in rows if row["id"] == chosen)

    with st.form("edit_exercise"):
        left, middle, right = st.columns(3)
        name = left.text_input("Name", value=current["name"])
        reps_mode = middle.selectbox(
            "Reps counted", config.REPS_MODES,
            index=config.REPS_MODES.index(current["reps_mode"]),
            format_func=lambda value: config.REPS_MODE_LABELS[value])
        weight_mode = right.selectbox(
            "Weight counted", config.WEIGHT_MODES,
            index=config.WEIGHT_MODES.index(current["weight_mode"]),
            format_func=lambda value: config.WEIGHT_MODE_LABELS[value])
        left, right = st.columns(2)
        bodyweight = left.checkbox("Bodyweight movement",
                                   value=bool(current["is_bodyweight"]))
        note = right.text_input("Note", value=current["note"] or "")
        if st.form_submit_button("Save", type="primary"):
            try:
                wm.save_exercise({"name": name, "reps_mode": reps_mode,
                                  "weight_mode": weight_mode,
                                  "is_bodyweight": bodyweight, "note": note},
                                 exercise_id=chosen)
                st.success("Saved.")
                st.rerun()
            except workouts.InvalidWorkout as exc:
                st.error(str(exc))

    left, middle = st.columns(2)
    if current["retired"]:
        if left.button("Put back in the dropdowns"):
            wm.retire_exercise(chosen, False)
            st.rerun()
    else:
        if left.button("Retire"):
            wm.retire_exercise(chosen, True)
            st.rerun()
    if middle.button("Delete", disabled=bool(current["sessions"])):
        try:
            wm.delete_exercise(chosen)
            st.rerun()
        except workouts.InvalidWorkout as exc:
            st.error(str(exc))

    st.caption(
        "**Retire** takes a movement out of the dropdowns and leaves every plan "
        "using it reading exactly as it did. **Delete** is only available for "
        "the ones no session has ever used — anything else would leave a hole "
        "in a plan from two years ago.")
