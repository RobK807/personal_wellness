"""Bits every food page needs: the macro tiles, the food picker, the day table.

The Flask side has the same four things as a Jinja partial. Keeping them here
rather than repeating them per page is what stops the two front-ends drifting
into disagreeing about what "80% of protein" looks like - and every number in
both comes out of core.food, so they cannot disagree about the arithmetic
itself.
"""
from __future__ import annotations

import datetime as dt

import streamlit as st

import config
from core import food, food_queries as fq


def macro_tiles(totals, target=None, columns=None) -> None:
    """The four macros as st.metric, with the shortfall as the delta.

    `delta_color="inverse"` on purpose: for calories and carbs, being over the
    target is the thing to notice, and Streamlit's default paints a positive
    delta green. The delta shown is what is *left*, so under target reads as a
    positive number and green means there is room.
    """
    cells = columns or st.columns(len(config.MACRO_KEYS))
    left = food.remaining(totals, target or {}) if target else None
    for cell, key in zip(cells, config.MACRO_KEYS):
        value = food.fmt_macro(key, totals.get(key), with_unit=True)
        if left is None:
            cell.metric(config.MACRO_LABELS[key], value)
            continue
        room = left[key]
        cell.metric(
            config.MACRO_LABELS[key], value,
            delta=(f"{food.fmt_macro(key, room)} left" if room >= 0
                   else f"{food.fmt_macro(key, -room)} over"),
            delta_color="normal" if room >= 0 else "inverse",
            help=f"Target {food.fmt_macro(key, target.get(key), with_unit=True)}")


def pick_food(label: str = "Food", key: str = "food", allow_none: bool = True,
              catalogue=None):
    """The catalogue dropdown. Returns the food row, or None for free text.

    Grouped by list in the label rather than filtered by it: 187 foods is short
    enough to scroll and Streamlit's selectbox filters as you type, so a second
    dropdown to narrow the first would be one more thing to get wrong.
    """
    rows = catalogue if catalogue is not None else fq.foods()
    labels = {row["id"]: f"{row['name']} · {row['list']}" for row in rows}
    chosen = st.selectbox(
        label, list(labels), index=None, key=key,
        placeholder="— free text —" if allow_none else "Pick a food",
        format_func=lambda value: labels[value])
    return next((row for row in rows if row["id"] == chosen), None)


def day_table(when) -> list:
    """A day as the planner lays it out: one block per meal, with its totals."""
    sheet = fq.day_sheet(when)
    if not sheet:
        st.caption("Nothing recorded on this day yet.")
        return sheet
    for block in sheet:
        st.markdown(f"**{block['meal']}** — "
                    f"{food.fmt_macro('calories', block['totals']['calories'])} kcal")
        st.dataframe([{
            "Food": row["name"],
            "Amount": food.fmt_quantity(row["quantity"], row["units"]),
            **{config.MACRO_LABELS[key]: row[key] for key in config.MACRO_KEYS},
            "From": row["food_list"] if row.get("food_id") else "free text",
        } for row in block["entries"]], hide_index=True, width="stretch")
    return sheet


def pick_date(label: str = "Date", key: str = "diet_day",
              default: dt.date | None = None) -> dt.date:
    """One date box, remembered across pages.

    Session state rather than a fresh default each time, for the same reason the
    workout section remembers which plan you were looking at: moving from the
    day to the week and back should not silently jump to today.
    """
    stored = st.session_state.get(key)
    value = stored or default or dt.date.today()
    chosen = st.date_input(label, value=value, format="DD/MM/YYYY",
                           key=f"{key}_widget")
    st.session_state[key] = chosen
    return chosen


def target_note(when, target) -> None:
    if target is None:
        st.caption("No target profile covers this date — set one on the "
                   "Targets page.")
        return
    st.caption(f"Against **{target['name']}**, in force from "
               f"{food.as_date(target['starts_on']):%d/%m/%Y}.")


def empty_section() -> bool:
    """What the section says before there is anything in it. True if empty."""
    if fq.total_foods():
        return False
    st.warning("The food catalogue is empty.")
    st.caption("Import the workbook's Food sheet, then load the corrected "
               "diary CSV. Run both on a desktop: openpyxl opening a workbook "
               "needs more memory than the NAS has free.")
    st.code('python -m core.food_import --catalogue\n'
            'python -m core.food_import --export\n'
            'python -m core.food_import --load "data/imports/food_diary_cleaned.csv" '
            '--replace', language="bash")
    st.caption(f"Looking for `{config.FOOD_XLSX}`.")
    return True
