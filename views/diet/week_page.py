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
    _bulk(data, starts_on)
    st.divider()
    _fill(data, starts_on)


def _controls():
    left, middle, right = st.columns([2, 2, 1])
    with left:
        anchor = shared.pick_date("Week containing")
    with middle:
        starts_on = st.selectbox(
            "Starting on", range(7), index=fq.week_starts_on(),
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


def _bulk(data, starts_on: int) -> None:
    """The bulk planner: one table per meal, one row per day.

    Deliberately one line per meal per day rather than eight. This is the "same
    breakfast all week" tool; anything more detailed is a day at a time on the
    Day page, and a 7x4x8 grid would be 224 rows nobody would fill in.

    A table rather than 28 forms, because Streamlit has st.data_editor and it is
    exactly the shape of this job. Every row carries its own List and Grouping,
    pre-filled from the Admin page's defaults for that meal.
    """
    st.subheader("Plan the whole week")
    st.caption(
        "One line per meal per day. Fill in as much or as little as you like "
        "and press the button once — anything left blank is skipped. **Days "
        "that already have entries are left alone**, so this can never "
        "overwrite what is already recorded; it says which ones it skipped.")

    groups = fq.groupings_by_list()
    every_grouping = sorted({name for values in groups.values()
                             for name in values})
    names = [row["name"] for row in fq.foods()]
    defaults = fq.meal_defaults()

    edited = {}
    for meal in config.MEALS:
        default_list, default_grouping = defaults.get(meal, ("Items", ""))
        blank = [{
            "Day": f"{row['weekday'][:3]} {row['date']:%d/%m}",
            "Food": "", "Qty": 1.0, "Units": "",
            "List": default_list, "Grouping": default_grouping or "",
            **{config.MACRO_LABELS[key]: 0.0 for key in config.MACRO_KEYS},
        } for row in data["days"]]

        taken = [row["weekday"][:3] for row in data["days"] if row["planned"]]
        with st.expander(
                f"{meal}"
                + (f" — {', '.join(taken)} already planned, will be left alone"
                   if taken else ""),
                expanded=not taken):
            edited[meal] = st.data_editor(
                blank, hide_index=True, width="stretch",
                num_rows="fixed", key=f"bulk_{meal}",
                column_config={
                    "Day": st.column_config.TextColumn(disabled=True),
                    "Food": st.column_config.TextColumn(
                        help="A name from the catalogue, or a new one"),
                    "Qty": st.column_config.NumberColumn(min_value=0.0),
                    "Units": st.column_config.TextColumn(),
                    "List": st.column_config.SelectboxColumn(
                        options=config.FOOD_LISTS, required=True),
                    "Grouping": st.column_config.SelectboxColumn(
                        options=[""] + every_grouping),
                    **{config.MACRO_LABELS[key]:
                       st.column_config.NumberColumn(min_value=0.0)
                       for key in config.MACRO_KEYS},
                })
            st.caption(f"Foods in the catalogue: {len(names)}. A name it has "
                       f"not got is added under the List and Grouping on that "
                       f"row — after a check for anything close to it.")

    if not st.button("Copy this into the week", type="primary"):
        return
    _apply_bulk(data, edited, starts_on)


def _apply_bulk(data, edited: dict, starts_on: int) -> None:
    """Turn the tables into diary lines, check the new names, write them."""
    rows = []
    for meal, table in edited.items():
        for offset, row in enumerate(table):
            name = str(row.get("Food") or "").strip()
            if not name:
                continue
            slot = {
                "index": f"{meal}-{offset}",
                "name": name,
                "quantity": row.get("Qty") or None,
                "units": row.get("Units") or "",
                "list": row.get("List"),
                "grouping": row.get("Grouping") or None,
                "resolve": "",
                "seen": st.session_state.get(f"ack_{meal}_{offset}") is not None,
                **{key: row.get(config.MACRO_LABELS[key]) or 0
                   for key in config.MACRO_KEYS},
            }
            resolved = st.session_state.get(f"ack_{meal}_{offset}")
            if resolved:
                slot["resolve"] = resolved
            rows.append({"slot": slot, "meal": meal,
                         "entry": {**fq.resolve_entry(slot, meal),
                                   "day_offset": offset}})

    if not rows:
        st.warning("Nothing to copy across — fill in at least one row.")
        return

    alerts = fq.alerts_for(rows)
    if alerts:
        # Held rather than written: each new name gets one question, and the
        # answers are kept in session state so pressing the button again goes
        # straight through.
        st.warning(f"{len(alerts)} name{'' if len(alerts) == 1 else 's'} "
                   f"{'looks' if len(alerts) == 1 else 'look'} like something "
                   f"already in the catalogue.")
        for key, entry in alerts.items():
            meal, offset = str(key).rsplit("-", 1)
            st.markdown(f"**{entry['meal']} · {entry['name']}** — "
                        f"{entry['message']}")
            labels = {0: f"Add '{entry['name']}' as a new food"}
            for match in entry["matches"]:
                labels[match["id"]] = f"Use '{match['name']}' instead"
            picked = st.radio("What should this be?", list(labels),
                              key=f"pick_{key}",
                              format_func=lambda value: labels[value],
                              horizontal=True)
            st.session_state[f"ack_{meal}_{offset}"] = picked or ""
        st.info("Press **Copy this into the week** again to go ahead.")
        return

    try:
        made = fm.plan_week(data["start"], [row["entry"] for row in rows],
                            starts_on)
    except food.InvalidFood as exc:
        st.error(str(exc))
        return

    if not made["days"]:
        st.warning("Nothing copied — every day you filled in already has "
                   "entries, and this never overwrites. Clear a day on its own "
                   "page first.")
        return
    message = (f"Copied {made['lines']} line"
               f"{'' if made['lines'] == 1 else 's'} into {len(made['days'])} "
               f"day{'' if len(made['days']) == 1 else 's'}")
    if made["skipped"]:
        message += (f"; left {', '.join(f'{day:%a}' for day in made['skipped'])}"
                    f" alone — already had entries")
    if made["added"]:
        message += ("; added " + ", ".join(f"'{row['name']}'"
                                           for row in made["added"])
                    + " to the catalogue")
    st.success(message + ".")
    st.rerun()


def _fill(data, starts_on: int) -> None:
    """The workbook's Planner in one button."""
    st.subheader("Or repeat one day across the week")
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
