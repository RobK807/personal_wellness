"""Build a session - one week at a time, one exercise at a time.

The exercises being added are held in session state until Save, so the page can
show the session taking shape. Flask cannot do that with a form post, which is
why its builder renders ten slots at once; both call the same save_session().
"""
from __future__ import annotations

import streamlit as st

import config
from core import workout_mutations as wm, workout_queries as wq, workouts
from views.workouts import shared

DRAFT = "session_draft"


def render() -> None:
    st.title("Build a session")

    if not wq.total_plans():
        shared.no_plans()
        return

    plan = shared.pick_plan()
    weeks = wq.weeks(plan["id"])
    if not weeks:
        st.warning("This plan has no weeks yet — add one on the Plan page.")
        return

    labels = {row["id"]: shared.week_heading(row) for row in weeks}
    stored = st.session_state.get("week_id")
    ids = list(labels)
    week_id = st.selectbox(
        "Week", ids, index=ids.index(stored) if stored in ids else 0,
        format_func=lambda value: labels[value])
    st.session_state["week_id"] = week_id
    week = wq.week(week_id)
    phase = wq.phase(week["phase_id"]) if week["phase_id"] else None

    if phase:
        st.info(
            f"**{phase['name']}** prescribes warm-ups at "
            f"{workouts.fmt_percent_list(phase['warmup_pcts'])}, working sets "
            f"at {workouts.fmt_percent_list(phase['working_pcts'])}, "
            f"{phase['working_sets'] or '?'} × {phase['working_reps'] or '?'} "
            f"reps, and accessories {phase['accessory_sets'] or '?'} × "
            f"{phase['accessory_reps'] or '?'}. The boxes below start there.")

    _existing(week_id)
    st.divider()
    _builder(plan, week, phase)


def _existing(week_id: int) -> None:
    st.subheader("What is in this week")
    rows = wq.sessions(week_id=week_id)
    if not rows:
        st.caption("Nothing yet.")
        return
    for row in rows:
        done = f" · done {shared.day(row['done_on'])}" if row["done"] else ""
        with st.expander(f"{workouts.session_title(row)}{done}"):
            shared.session_sheet(row["id"])
            left, middle, right = st.columns(3)
            if left.button("Load into the builder", key=f"load_{row['id']}"):
                _load_draft(row["id"])
                st.rerun()
            if middle.button("Tick off" if not row["done"] else "Un-tick",
                             key=f"tick_{row['id']}"):
                wm.tick_session(row["id"], not row["done"])
                st.rerun()
            if right.button("Delete", key=f"del_{row['id']}"):
                wm.delete_session(row["id"])
                st.rerun()


def _load_draft(session_id: int) -> None:
    """Pull an existing session back into the draft, to edit and re-save."""
    row = wq.session(session_id)
    draft = []
    for item in wq.session_sheet(session_id):
        draft.append({
            "exercise_id": item["exercise_id"],
            "name": item["name"],
            "reps_mode": item["reps_mode"] or "",
            "weight_mode": item["weight_mode"] or "",
            "note": item["note"] or "",
            "sets": [{
                "set_type": entry["set_type"],
                "reps": workouts.fmt_reps(entry["reps_low"],
                                          entry["reps_high"]),
                "load_mode": entry["load_mode"],
                "weight_kg": entry["weight_kg"],
                "percent_1rm": entry["percent_1rm"],
                "added_kg": entry["added_kg"],
                "rest": entry["rest"] or "",
                "cue": entry["cue"] or "",
            } for entry in item["sets"]],
        })
    st.session_state[DRAFT] = draft
    st.session_state["editing_session"] = session_id
    st.session_state["draft_number"] = row["number"]


def _builder(plan: dict, week: dict, phase) -> None:
    editing = st.session_state.get("editing_session")
    draft = st.session_state.setdefault(DRAFT, [])

    st.subheader("Edit a session" if editing else "New session")
    if editing:
        st.caption("Saving replaces that session's exercises and sets with "
                   "whatever is below.")

    catalogue = wq.exercises()
    maxes = {row["exercise_id"]: row["one_rm_kg"] for row in wq.maxes(plan["id"])}

    with st.expander("Add an exercise", expanded=not draft):
        _add_form(catalogue, maxes, phase)

    if draft:
        st.markdown("**This session so far**")
        for index, item in enumerate(draft):
            counts: dict = {}
            for entry in item["sets"]:
                counts[entry["set_type"]] = counts.get(entry["set_type"], 0) + 1
            shape = ", ".join(
                f"{count} {config.SET_TYPE_LABELS[kind].lower()}"
                for kind, count in counts.items())
            left, right = st.columns([6, 1])
            left.write(f"{index + 1}. **{item['name']}** — {shape}")
            if right.button("Remove", key=f"rm_{index}"):
                draft.pop(index)
                st.rerun()
        st.caption(f"{len(draft)} exercise"
                   f"{'' if len(draft) == 1 else 's'}, "
                   f"{sum(len(i['sets']) for i in draft)} sets.")
    else:
        st.caption("Nothing added yet.")

    used = {row["number"] for row in wq.sessions(week_id=week["id"])
            if row["id"] != editing}
    default = st.session_state.get("draft_number") or next(
        (n for n in range(1, config.MAX_SESSIONS_PER_WEEK + 1) if n not in used),
        1)

    with st.form("save_session"):
        left, middle = st.columns([1, 3])
        number = left.number_input("Number in the week", min_value=1,
                                   max_value=config.MAX_SESSIONS_PER_WEEK,
                                   value=int(default))
        name = middle.text_input(
            "Name", placeholder="leave blank to name it after its main lifts")
        saved = st.form_submit_button(
            "Save changes" if editing else "Save session", type="primary")

    if saved:
        try:
            result = wm.save_session(week["id"],
                                     {"number": number, "name": name},
                                     draft, session_id=editing)
        except workouts.InvalidWorkout as exc:
            st.error(str(exc))
        else:
            st.success(f"Saved session {result['number']}: "
                       f"{result['exercises']} exercises, {result['sets']} "
                       f"sets.")
            _clear_draft()
            st.rerun()

    if draft or editing:
        if st.button("Start again"):
            _clear_draft()
            st.rerun()


def _clear_draft() -> None:
    for key in (DRAFT, "editing_session", "draft_number"):
        st.session_state.pop(key, None)


def _add_form(catalogue, maxes, phase) -> None:
    """One exercise: its warm-ups, its working block, its accessory block."""
    names = {row["id"]: row["name"] + (" (bodyweight)" if row["is_bodyweight"]
                                       else "")
             + (f" · 1RM {workouts.fmt_kg(maxes[row['id']])} kg"
                if maxes.get(row["id"]) else "")
             for row in catalogue}

    with st.form("add_exercise", clear_on_submit=True):
        chosen = st.selectbox("Movement", list(names),
                              format_func=lambda value: names[value])

        st.markdown("**Warm-up sets** — up to "
                    f"{config.MAX_WARMUP_SETS}, each prescribed on its own")
        warm_defaults = workouts.percent_list(phase["warmup_pcts"]) if phase else []
        warmups = []
        for index in range(config.MAX_WARMUP_SETS):
            a, b, c = st.columns([1, 2, 1])
            reps = a.text_input(f"W{index + 1} reps", key=f"w{index}_reps",
                                value="5" if index < len(warm_defaults) else "")
            mode = b.selectbox(
                f"W{index + 1} weight from", ["", *config.LOAD_MODES],
                key=f"w{index}_mode",
                index=2 if index < len(warm_defaults) else 0,
                format_func=lambda v: "— no set —" if not v
                else config.LOAD_MODE_LABELS[v])
            value = c.text_input(
                f"W{index + 1} value", key=f"w{index}_value",
                value=(f"{warm_defaults[index] * 100:g}"
                       if index < len(warm_defaults) else ""))
            warmups.append((reps, mode, value))

        st.markdown("**Working sets**")
        a, b, c, d = st.columns(4)
        work_pcts = workouts.percent_list(phase["working_pcts"]) if phase else []
        work_sets = a.text_input("How many",
                                 value=str(phase["working_sets"] or "")
                                 if phase else "")
        work_reps = b.text_input("Reps each",
                                 value=(phase["working_reps"] or "")
                                 if phase else "")
        work_mode = c.selectbox("Weight from", ["", *config.LOAD_MODES],
                                index=2,
                                format_func=lambda v: "— none —" if not v
                                else config.LOAD_MODE_LABELS[v])
        work_value = d.text_input("Value",
                                  value=f"{work_pcts[0] * 100:g}"
                                  if work_pcts else "")
        a, b = st.columns(2)
        work_rest = a.text_input("Working rest",
                                 value=(phase["rest_working"] if phase
                                        else config.DEFAULT_REST["working"]))
        work_cue = b.selectbox("Working cue", ["", *config.CUE_OPTIONS])

        st.markdown("**Accessory sets**")
        a, b, c, d = st.columns(4)
        acc_sets = a.text_input("How many ",
                                value=str(phase["accessory_sets"] or "")
                                if phase else "")
        acc_reps = b.text_input("Reps each ",
                                value=(phase["accessory_reps"] or "")
                                if phase else "")
        acc_mode = c.selectbox("Weight from ", ["", *config.LOAD_MODES],
                               index=0,
                               format_func=lambda v: "— none —" if not v
                               else config.LOAD_MODE_LABELS[v])
        acc_value = d.text_input("Value ")
        a, b = st.columns(2)
        acc_rest = a.text_input("Accessory rest",
                                value=(phase["rest_accessory"] if phase
                                       else config.DEFAULT_REST["accessory"]))
        acc_cue = b.selectbox("Accessory cue", ["", *config.CUE_OPTIONS])

        if not st.form_submit_button("Add to the session", type="primary"):
            return

    sets = []
    for reps, mode, value in warmups:
        if mode and (reps or mode == "choose"):
            sets.append(_one(mode, "warmup", reps, value,
                             phase["rest_warmup"] if phase
                             else config.DEFAULT_REST["warmup"], ""))
    for count, reps, mode, value, rest, cue, kind in (
            (work_sets, work_reps, work_mode, work_value, work_rest, work_cue,
             "working"),
            (acc_sets, acc_reps, acc_mode, acc_value, acc_rest, acc_cue,
             "accessory")):
        if not count or not mode:
            continue
        try:
            count = int(str(count).strip())
        except ValueError:
            st.error(f"'{count}' is not a number of {kind} sets")
            return
        for _ in range(max(count, 0)):
            sets.append(_one(mode, kind, reps, value, rest, cue))

    if not sets:
        st.error("That exercise has no sets against it — fill in a warm-up, a "
                 "working block or an accessory block.")
        return

    name = next(row["name"] for row in catalogue if row["id"] == chosen)
    entry = {"exercise_id": chosen, "name": name, "reps_mode": "",
             "weight_mode": "", "note": "", "sets": sets}
    try:                       # fail here rather than at Save
        wm._prepare_exercise(entry, len(st.session_state[DRAFT]) + 1)
    except workouts.InvalidWorkout as exc:
        st.error(str(exc))
        return

    st.session_state[DRAFT].append(entry)
    st.rerun()


def _one(mode: str, set_type: str, reps, value, rest, cue) -> dict:
    """One set. `value` means whatever the chosen mode says it means."""
    entry = {"set_type": set_type, "reps": reps, "load_mode": mode,
             "weight_kg": None, "percent_1rm": None, "added_kg": None,
             "rest": rest, "cue": cue}
    if mode == "percent":
        entry["percent_1rm"] = value
    elif mode == "explicit":
        entry["weight_kg"] = value
    elif mode == "bodyweight":
        entry["added_kg"] = value
    return entry
