"""Bits every workout page needs: the plan picker, and the sheet renderer."""
from __future__ import annotations

import streamlit as st

import config
from core import metrics, workout_queries as wq, workouts


def pick_plan(key: str = "plan_id"):
    """The plan picker. Historic plans are found here - every one ever saved.

    The choice is kept in session state under one key, so moving between the
    four pages does not lose which plan is being looked at.
    """
    plans = wq.plans()
    if not plans:
        return None

    stored = st.session_state.get(key)
    ids = [row["id"] for row in plans]
    index = ids.index(stored) if stored in ids else 0
    labels = {row["id"]: workouts.describe_plan(row)
              + (" · archived" if row["archived"] else "") for row in plans}

    chosen = st.selectbox("Plan", ids, index=index,
                          format_func=lambda value: labels[value],
                          key=f"{key}_widget")
    st.session_state[key] = chosen
    return wq.plan(chosen)


def no_plans() -> None:
    """What the section says before there is anything in it."""
    st.warning("No plans yet.")
    st.caption("A plan is a programme with a name — phases, weeks, sessions and "
               "the sets inside them. Import the gym workbook, or start one by "
               "hand on this page.")
    st.code("python -m core.gym_import              # add it\n"
            "python -m core.gym_import --rebuild   # replace what is there",
            language="bash")
    st.caption(f"Looking for `{config.GYM_XLSX}`. Run it on a desktop: openpyxl "
               f"opening a workbook needs more memory than the NAS has free.")


def session_sheet(session_id: int) -> None:
    """One session as the week sheet lays it out."""
    rows = []
    for item in wq.session_sheet(session_id):
        for index, entry in enumerate(item["sets"]):
            rows.append({
                "Exercise": item["name"] if index == 0 else "",
                "Set": config.SET_TYPE_LABELS.get(entry["set_type"],
                                                  entry["set_type"]),
                "#": workouts.set_label(entry),
                "Reps": workouts.fmt_reps(entry["reps_low"], entry["reps_high"],
                                          entry["reps_mode"]),
                "Weight": workouts.fmt_load(entry),
                "Rest": entry["rest"] or "",
                "Notes": entry["cue"] or "",
            })
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.caption("No exercises in this session.")


def week_heading(week: dict) -> str:
    parts = [workouts.week_title(week)]
    if week.get("phase_name"):
        parts.append(week["phase_name"])
    if week.get("cycle_type"):
        parts.append(f"type {week['cycle_type']}")
    return " · ".join(parts)


def day(value) -> str:
    return metrics.period_label("daily", value) if value else ""
