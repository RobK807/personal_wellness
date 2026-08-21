"""Seven days, starting on whichever day config names.

The workbook ran Monday to Sunday and every "W/C" header in its diary is a
Monday, so that is the default - but which day a planning week turns over on is
a habit rather than a fact, and the picker here changes the view without
changing the setting.
"""
from __future__ import annotations

import datetime as dt

import streamlit as st

import config
from core import food, food_mutations as fm, food_queries as fq
from views.diet import shared


def render() -> None:
    st.title("Week")
    if shared.empty_section():
        return

    anchor, starts_on = _controls()
    data = fq.week(anchor, starts_on)
    target = fq.target_for(data["start"])
    planned = [row for row in data["days"] if row["planned"]]

    st.subheader(data["label"])
    averages = ({key: round(data["totals"][key] / len(planned), 1)
                 for key in config.MACRO_KEYS} if planned else dict(food.ZERO))
    shared.macro_tiles(averages, target)
    st.caption(
        f"Per day, averaged over the {len(planned)} day"
        f"{'' if len(planned) == 1 else 's'} that have something against them "
        f"— a blank Thursday is not a 0-calorie day, so it is not averaged in "
        f"as one.")

    _table(data, target, len(planned))
    st.divider()
    _fill(data, starts_on)


def _controls():
    left, middle, right = st.columns([2, 2, 1])
    with left:
        anchor = shared.pick_date("Week containing")
    with middle:
        starts_on = st.selectbox(
            "Starting on", range(7), index=config.WEEK_STARTS_ON,
            format_func=lambda value: config.WEEKDAY_NAMES[value],
            key="week_starts_on")
    with right:
        st.caption("")
        if st.button("This week", width="stretch"):
            st.session_state["diet_day"] = dt.date.today()
            st.rerun()
    return anchor, starts_on


def _table(data, target, planned: int) -> None:
    """Seven rows, not seven columns - the day is what gets looked up."""
    st.dataframe([{
        "Day": f"{row['weekday'][:3]} {row['date']:%d/%m}",
        **{config.MACRO_LABELS[key]: (row[key] if row["planned"] else None)
           for key in config.MACRO_KEYS},
        "Lines": row["entries"] if row["planned"] else None,
    } for row in data["days"]], hide_index=True, width="stretch")

    if not (target and planned):
        return
    # Against the days that have something against them, not against seven.
    # Six days measured against a seven-day target reads as 1,500 calories under
    # when the truth is that Sunday has not been planned yet.
    st.dataframe([
        {"": "Week total",
         **{config.MACRO_LABELS[key]: round(data["totals"][key], 1)
            for key in config.MACRO_KEYS}},
        {"": f"Target × {planned} planned day{'' if planned == 1 else 's'}",
         **{config.MACRO_LABELS[key]: round(target[key] * planned, 1)
            for key in config.MACRO_KEYS}},
        {"": "Difference",
         **{config.MACRO_LABELS[key]:
            round(data["totals"][key] - target[key] * planned, 1)
            for key in config.MACRO_KEYS}},
    ], hide_index=True, width="stretch")


def _fill(data, starts_on: int) -> None:
    """The workbook's Planner in one button."""
    st.subheader("Plan the week from one day")
    st.caption(
        "Copies every line of one day into the other six. Days that already "
        "have entries are left alone unless you say otherwise, because the "
        "usual shape of this is planning forward from a Monday that has "
        "already happened.")
    with st.form("fill_week"):
        left, right = st.columns(2)
        source = left.date_input("Copy from", value=data["start"],
                                 format="DD/MM/YYYY")
        overwrite = right.checkbox("Overwrite days that already have entries")
        if not st.form_submit_button("Fill the week", type="primary"):
            return
    try:
        made = fm.fill_week(source, data["start"], starts_on,
                            overwrite=overwrite)
    except food.InvalidFood as exc:
        st.error(str(exc))
        return
    message = (f"Copied {made['source']:%d/%m/%Y} into {len(made['copied'])} "
               f"day{'' if len(made['copied']) == 1 else 's'}")
    if made["skipped"]:
        message += (f"; left {len(made['skipped'])} alone because they already "
                    f"had entries")
    st.success(message + ".")
    st.rerun()
