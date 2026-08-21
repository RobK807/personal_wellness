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


def pick_filters(key: str, meal: str | None = None, columns=None) -> tuple:
    """The List and Grouping dropdowns that narrow a food picker.

    The point of them: 187 foods in one box is a list you scroll rather than
    read, and picking the corner of the catalogue first cuts it to a dozen. They
    open on whatever the Admin page says that meal usually is.
    """
    defaults = fq.meal_defaults()
    fallback = defaults.get(meal or "", (config.FOOD_LISTS[0], ""))
    groups = fq.groupings_by_list()
    left, right = columns or st.columns(2)

    list_name = left.selectbox(
        "List", config.FOOD_LISTS,
        index=config.FOOD_LISTS.index(fallback[0])
        if fallback[0] in config.FOOD_LISTS else 0,
        key=f"{key}_list")
    available = groups.get(list_name, [])
    grouping = right.selectbox(
        "Grouping", [None] + available,
        index=(available.index(fallback[1]) + 1)
        if fallback[1] in available else 0,
        format_func=lambda value: value or "— any —",
        key=f"{key}_grouping")
    return list_name, grouping


def pick_food(label: str = "Food", key: str = "food", list_name=None,
              grouping=None, catalogue=None):
    """The food dropdown, narrowed by List and Grouping. Returns a row or None.

    Streamlit can afford to keep this a real dropdown - there is no page weight
    to worry about the way there is on the Flask side, and its selectbox filters
    as you type. Free text is a separate box below rather than the same control,
    because Streamlit has no equivalent of an <input> with a datalist.
    """
    rows = catalogue if catalogue is not None else fq.foods()
    narrowed = [row for row in rows
                if (not list_name or row["list"] == list_name)
                and (not grouping or row["grouping"] == grouping)]
    if not narrowed:
        st.caption("Nothing in the catalogue under those two yet — "
                   "type a name below and it will be added there.")
        return None
    labels = {row["id"]: f"{row['name']} · "
                         f"{food.fmt_macro('calories', row['calories'])} kcal "
                         f"per {food.fmt_quantity(row['portion'], row['units'])}"
              for row in narrowed}
    chosen = st.selectbox(
        f"{label} ({len(narrowed)} to choose from)", list(labels), index=None,
        key=key, placeholder="— or type a new one below —",
        format_func=lambda value: labels[value])
    return next((row for row in narrowed if row["id"] == chosen), None)


def match_alert(name: str, key: str):
    """Show the "did you mean" alert, and return what to do about it.

    Returns None for "add it as new", or a catalogue row to use instead. An
    alert rather than a correction: the whole reason free text exists is that
    sometimes the thing you ate really is new.
    """
    matches = food.close_matches(name, fq.foods(include_retired=True))
    if not matches:
        return None, False
    st.warning(food.match_alert(name, matches))
    labels = {0: f"Add '{name}' as a new food"}
    for row in matches:
        labels[row["id"]] = (f"Use '{row['name']}' instead ({row['list']}"
                             + (f" / {row['grouping']}" if row["grouping"]
                                else "") + ")")
    chosen = st.radio("What should this be?", list(labels), key=key,
                      format_func=lambda value: labels[value])
    return (fq.food_row(chosen) if chosen else None), True


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
