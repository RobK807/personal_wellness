"""Plan - pick a programme, see its phases, 1RMs and weeks, copy it forward."""
from __future__ import annotations

import datetime as dt

import streamlit as st

import config
from core import workout_mutations as wm, workout_queries as wq, workouts
from views.workouts import shared


def render() -> None:
    st.title("Workout plan")

    if not wq.total_plans():
        shared.no_plans()
        _new_plan()
        return

    plan = shared.pick_plan()
    totals = wq.totals(plan["id"])

    columns = st.columns(4)
    columns[0].metric("Weeks", totals["weeks"])
    columns[1].metric("Sessions", totals["sessions"])
    columns[2].metric("Done", f"{totals['sessions_done']}/{totals['sessions']}")
    columns[3].metric("Prescribed sets", f"{totals['sets']:,}")
    st.progress(totals["progress"],
                text=f"{totals['progress'] * 100:.0f}% through")
    st.caption(
        (f"Started {shared.day(plan['started_on'])}. " if plan["started_on"] else "")
        + f"Weights round to {workouts.fmt_kg(plan['rounding_kg'])} kg."
        + (f" {plan['note']}" if plan["note"] else ""))

    without = wq.percent_sets_without_a_max(plan["id"])
    if without:
        st.warning(
            f"{sum(row['sets'] for row in without)} sets are a percentage of a "
            f"1RM this plan does not have: "
            + ", ".join(f"**{row['exercise_name']}** ({row['sets']} sets, from "
                        f"week {row['first_week']})" for row in without)
            + ". They show no weight until a 1RM is entered below.")

    st.divider()
    _maxes(plan)
    st.divider()
    _phases(plan)
    st.divider()
    _weeks(plan)
    st.divider()
    _copy(plan)
    st.divider()
    _settings(plan)


def _maxes(plan: dict) -> None:
    st.subheader("One-rep maxes")
    rows = wq.maxes(plan["id"])
    if rows:
        st.dataframe([{"Lift": row["name"],
                       "1RM": f"{workouts.fmt_kg(row['one_rm_kg'])} kg"}
                      for row in rows], hide_index=True, width="stretch")
    else:
        st.caption("None yet — a percentage set needs one.")

    catalogue = [row for row in wq.exercises() if not row["is_bodyweight"]]
    with st.form("set_max"):
        left, middle = st.columns([3, 2])
        chosen = left.selectbox(
            "Lift", [row["id"] for row in catalogue],
            format_func=lambda value: next(r["name"] for r in catalogue
                                           if r["id"] == value))
        typed = middle.text_input("1RM (kg)", placeholder="95")
        if st.form_submit_button("Save 1RM", type="primary"):
            try:
                wm.set_max(plan["id"], chosen, typed)
                st.success("Saved — every percentage set on this plan follows "
                           "it.")
                st.rerun()
            except workouts.InvalidWorkout as exc:
                st.error(str(exc))
    st.caption("1RMs belong to this plan. Retest, start the next programme with "
               "the new numbers, and this one still says what it said at the "
               "time.")


def _phases(plan: dict) -> None:
    st.subheader("Phases")
    rows = wq.phases(plan["id"])
    if rows:
        st.dataframe([{
            "Phase": row["name"], "Focus": row["focus"] or "",
            "Weeks": row["weeks"],
            "Warm-up": workouts.fmt_percent_list(row["warmup_pcts"]),
            "Working": workouts.fmt_percent_list(row["working_pcts"]),
            "Sets × reps": f"{row['working_sets'] or '-'} × "
                           f"{row['working_reps'] or '-'}",
            "Accessories": f"{row['accessory_sets'] or '-'} × "
                           f"{row['accessory_reps'] or '-'}",
        } for row in rows], hide_index=True, width="stretch")
    else:
        st.caption("No phases yet.")

    with st.expander("Add or change a phase"):
        st.caption("Everything here is a default the session builder pre-fills "
                   "from. It is not a constraint: once a set exists it carries "
                   "its own percentage, and editing the phase afterwards does "
                   "not reach back into sessions already built.")
        options = {0: "New phase", **{row["id"]: row["name"] for row in rows}}
        with st.form("save_phase"):
            target = st.selectbox("Phase", list(options),
                                  format_func=lambda value: options[value])
            existing = next((r for r in rows if r["id"] == target), {})
            left, right = st.columns(2)
            name = left.text_input("Name", value=existing.get("name", ""),
                                   placeholder="Phase 1")
            focus = right.text_input("Focus", value=existing.get("focus") or "",
                                     placeholder="Hypertrophy")
            left, right = st.columns(2)
            warm = left.text_input(
                "Warm-up %", placeholder="50, 70",
                value=workouts.fmt_percent_list(existing.get("warmup_pcts"))
                      .replace("-", ""))
            work = right.text_input(
                "Working %", placeholder="65",
                value=workouts.fmt_percent_list(existing.get("working_pcts"))
                      .replace("-", ""))
            a, b, c, d = st.columns(4)
            ws = a.text_input("Working sets",
                              value=str(existing.get("working_sets") or ""))
            wr = b.text_input("Working reps",
                              value=existing.get("working_reps") or "")
            acs = c.text_input("Accessory sets",
                               value=str(existing.get("accessory_sets") or ""))
            acr = d.text_input("Accessory reps",
                               value=existing.get("accessory_reps") or "")
            if st.form_submit_button("Save phase", type="primary"):
                try:
                    wm.save_phase(plan["id"], {
                        "name": name, "focus": focus, "warmup_pcts": warm,
                        "working_pcts": work, "working_sets": ws,
                        "working_reps": wr, "accessory_sets": acs,
                        "accessory_reps": acr,
                        "rest_warmup": config.DEFAULT_REST["warmup"],
                        "rest_working": config.DEFAULT_REST["working"],
                        "rest_accessory": config.DEFAULT_REST["accessory"],
                    }, phase_id=target or None)
                    st.success("Phase saved.")
                    st.rerun()
                except workouts.InvalidWorkout as exc:
                    st.error(str(exc))


def _weeks(plan: dict) -> None:
    st.subheader("Weeks")
    rows = wq.weeks(plan["id"])
    if rows:
        st.dataframe([{
            "#": row["number"], "Label": row["label"] or "",
            "Phase": row["phase_name"] or "", "Type": row["cycle_type"] or "",
            "Sessions": row["sessions"],
            "Done": f"{row['sessions_done']}/{row['sessions']}",
        } for row in rows], hide_index=True, width="stretch")

        labels = {row["id"]: shared.week_heading(row) for row in rows}
        left, right = st.columns([3, 1])
        chosen = left.selectbox("Open a week", list(labels), index=0,
                                format_func=lambda value: labels[value])
        if right.button("Open", key="open_week"):
            st.session_state["week_id"] = chosen
            st.session_state["session_id"] = None
            st.switch_page("workouts-build")
    else:
        st.caption("No weeks yet.")

    with st.expander("Add a week"):
        phases = wq.phases(plan["id"])
        options = {0: "None", **{row["id"]: row["name"] for row in phases}}
        with st.form("add_week"):
            a, b, c = st.columns(3)
            number = a.text_input("Number", value=str(len(rows) + 1))
            phase = b.selectbox("Phase", list(options),
                                format_func=lambda value: options[value])
            cycle = c.text_input("Cycle type", placeholder="A", max_chars=2)
            label = st.text_input("Label", placeholder="Deload")
            if st.form_submit_button("Add week", type="primary"):
                try:
                    wm.save_week(plan["id"], {
                        "number": number, "phase_id": phase or None,
                        "cycle_type": cycle, "label": label})
                    st.success("Week added.")
                    st.rerun()
                except workouts.InvalidWorkout as exc:
                    st.error(str(exc))


def _copy(plan: dict) -> None:
    st.subheader("This plan as a template")
    st.caption("Copies the phases, weeks, sessions and every set into a new "
               "plan. The tick-offs are **not** copied — a template is what you "
               "intend to do, and inheriting last block's completed sessions "
               "would be a lie about this one.")
    with st.form("copy_plan"):
        left, middle, right = st.columns(3)
        name = left.text_input("New plan name",
                               placeholder=f"{plan['name']} (copy)")
        start = middle.date_input("Starting", value=dt.date.today(),
                                  format="DD/MM/YYYY")
        keep = right.selectbox("1RMs", [True, False],
                               format_func=lambda value: "Copy them over"
                               if value else "Start with none — I will retest")
        if st.form_submit_button("Copy into a new plan", type="primary"):
            try:
                made = wm.copy_plan(plan["id"], name, started_on=start,
                                    with_maxes=keep)
                st.session_state["plan_id"] = made["id"]
                st.success(f"Copied into '{made['name']}' — {made['weeks']} "
                           f"weeks, {made['sessions']} sessions, nothing "
                           f"ticked off.")
                st.rerun()
            except workouts.InvalidWorkout as exc:
                st.error(str(exc))


def _new_plan() -> None:
    with st.expander("Start a plan by hand", expanded=not wq.total_plans()):
        with st.form("new_plan"):
            left, middle, right = st.columns(3)
            name = left.text_input("Name", placeholder="2026 Gym Programme")
            start = middle.date_input("Started", value=dt.date.today(),
                                      format="DD/MM/YYYY")
            rounding = right.selectbox("Round weights to",
                                       config.ROUNDING_STEPS,
                                       index=config.ROUNDING_STEPS.index(
                                           config.DEFAULT_ROUNDING_KG),
                                       format_func=lambda v: f"{v:g} kg")
            if st.form_submit_button("Create plan", type="primary"):
                try:
                    made = wm.save_plan({"name": name, "started_on": start,
                                         "rounding_kg": rounding})
                    st.session_state["plan_id"] = made["id"]
                    st.success(f"Created '{made['name']}'.")
                    st.rerun()
                except workouts.InvalidWorkout as exc:
                    st.error(str(exc))


def _settings(plan: dict) -> None:
    with st.expander("Plan details, and deleting it"):
        with st.form("plan_settings"):
            left, middle, right = st.columns(3)
            name = left.text_input("Name", value=plan["name"])
            note = middle.text_input("Note", value=plan["note"] or "")
            rounding = right.selectbox(
                "Round weights to", config.ROUNDING_STEPS,
                index=(config.ROUNDING_STEPS.index(plan["rounding_kg"])
                       if plan["rounding_kg"] in config.ROUNDING_STEPS else 2),
                format_func=lambda v: f"{v:g} kg")
            archived = st.checkbox("Archived — keep it, hide it from the top of "
                                   "the list", value=bool(plan["archived"]))
            if st.form_submit_button("Save plan", type="primary"):
                try:
                    wm.save_plan({"name": name, "note": note,
                                  "rounding_kg": rounding,
                                  "started_on": plan["started_on"],
                                  "archived": archived}, plan_id=plan["id"])
                    st.success("Saved.")
                    st.rerun()
                except workouts.InvalidWorkout as exc:
                    st.error(str(exc))

        st.caption("Deleting a plan takes its weeks, sessions and sets with it. "
                   "There is no undo.")
        if st.checkbox(f"Yes, delete '{plan['name']}'", key="confirm_delete"):
            if st.button("Delete this plan", type="primary"):
                wm.delete_plan(plan["id"])
                st.session_state.pop("plan_id", None)
                st.rerun()
