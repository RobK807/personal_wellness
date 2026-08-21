"""One day, picked by date. The workbook's DailyCheck sheet.

Addressed by date rather than by week-and-day, which is the one thing it does
differently from the sheet it replaces: a diary with 48 of a possible 137 weeks
in it cannot be navigated by counting from the start, and "what did I eat on the
12th" is the question anyway.

Each meal holds up to config.MAX_ENTRIES_PER_MEAL lines, and the picker narrows
by List and then Grouping before it offers a food - see views/diet/shared.py.
A name the catalogue has never heard of becomes a free-text line *and* a new
catalogue row, filed under those two, after a check for anything close.
"""
from __future__ import annotations

import datetime as dt

import streamlit as st

import config
from core import food, food_mutations as fm, food_queries as fq
from views.diet import shared


def render() -> None:
    st.title("Day")
    if shared.empty_section():
        return

    when = _date_row()
    row = fq.day(when)
    # `chosen_target` is what this day asked for; `target_name` is what it ends
    # up measured against. Every imported day chose nothing - see v_food_days.
    target = fq.target_for(when, row["chosen_target"] if row else None) \
        or fq.target_for(when)
    totals = food.add_macros(fq.entries(when))

    st.subheader(f"{food.day_name(when)} {when:%d/%m/%Y}")
    shared.macro_tiles(totals, target)
    shared.target_note(when, target)

    shared.day_table(when)

    st.divider()
    _add_line(when)
    st.divider()
    _edit(when, row)


def _date_row() -> dt.date:
    left, middle, right = st.columns([2, 1, 1])
    with left:
        when = shared.pick_date("Date")
    middle.caption(food.week_label(when, fq.week_starts_on()))
    if right.button("Today", width="stretch"):
        st.session_state["diet_day"] = dt.date.today()
        st.rerun()
    return when


def _add_line(when) -> None:
    """One line at a time, because that is how eating happens.

    Two ways in, and the second is not a fallback: pick a food from the
    narrowed list, or type a name and four numbers off a packet. Two years of
    the imported history are the second kind, and a form that only offered the
    first would not be able to record a meal out.
    """
    st.subheader("Add a line")
    counts = _counts(when)
    room = [meal for meal in config.MEALS
            if counts.get(meal, 0) < config.MAX_ENTRIES_PER_MEAL]
    if not room:
        st.info(f"Every meal already has its {config.MAX_ENTRIES_PER_MEAL} "
                f"lines. Remove one below to add another.")
        return

    meal = st.selectbox(
        "Meal", room,
        format_func=lambda value: f"{value} "
                                  f"({counts.get(value, 0)} of "
                                  f"{config.MAX_ENTRIES_PER_MEAL})")
    list_name, grouping = shared.pick_filters("day_add", meal)
    chosen = shared.pick_food("Food", key="day_add_food",
                              list_name=list_name, grouping=grouping)

    if chosen is not None:
        _add_from_catalogue(when, meal, chosen)
    else:
        _add_free_text(when, meal, list_name, grouping)


def _counts(when) -> dict:
    out: dict = {}
    for entry in fq.entries(when):
        out[entry["meal"]] = out.get(entry["meal"], 0) + 1
    return out


def _add_from_catalogue(when, meal, chosen) -> None:
    with st.form("add_from_catalogue", clear_on_submit=True):
        quantity = st.number_input("Quantity", min_value=0.0, step=1.0,
                                   value=float(chosen["portion"]))
        preview = food.eaten(chosen, quantity or chosen["portion"])
        st.caption(f"{food.fmt_quantity(quantity, chosen['units'])} — "
                   + ", ".join(f"{config.MACRO_LABELS[key]} "
                               f"{food.fmt_macro(key, preview[key])}"
                               for key in config.MACRO_KEYS))
        if st.form_submit_button("Add", type="primary"):
            _append(when, {"meal": meal, "food_id": chosen["id"],
                           "quantity": quantity})


def _add_free_text(when, meal, list_name, grouping) -> None:
    """A name the catalogue has not got - checked, then remembered.

    The check happens before the form is submitted rather than after, because
    Streamlit reruns on every keystroke-ish interaction anyway and an alert that
    appears as you type is one you read. The Flask side has to do the same thing
    on the round trip; both end up calling core's close_matches().
    """
    name = st.text_input("Or a name the list has not got",
                         placeholder="Dinner out", key="day_add_name")
    if not name.strip():
        return

    instead, alerted = shared.match_alert(name, key="day_add_alert")
    if instead is not None:
        st.caption(f"Will record **{instead['name']}** from the catalogue.")

    with st.form("add_free_text", clear_on_submit=True):
        left, right = st.columns(2)
        quantity = left.number_input("Quantity", min_value=0.0, step=1.0,
                                     value=1.0)
        units = right.text_input("Units", value="Portion")
        cells = st.columns(len(config.MACRO_KEYS))
        macros = {key: cells[index].number_input(
                      config.MACRO_LABELS[key], min_value=0.0, step=1.0,
                      value=0.0, key=f"free_{key}")
                  for index, key in enumerate(config.MACRO_KEYS)}
        st.caption(
            f"Saving this adds **{name}** to the catalogue under "
            f"**{list_name}{' / ' + grouping if grouping else ''}**, with these "
            f"macros for {food.fmt_quantity(quantity, units)} of it."
            if instead is None else
            f"Saving this records the catalogue's **{instead['name']}** "
            f"instead, and adds nothing.")
        if st.form_submit_button("Add", type="primary"):
            slot = {"name": name, "quantity": quantity, "units": units,
                    "list": list_name, "grouping": grouping,
                    "resolve": instead["id"] if instead else "",
                    **macros}
            _append(when, fq.resolve_entry(slot, meal))


def _append(when, entry) -> None:
    """Add one line, keeping everything already on the day.

    save_day() replaces a day wholesale, so the lines already there are read
    back and passed through with `_parsed` set - they have been through
    parse_entry once already and re-parsing a stored row would re-scale it.
    """
    existing = [{**row, "_parsed": True} for row in fq.entries(when)]
    day = fq.day(when) or {}
    try:
        result = fm.save_day(when, existing + [entry],
                             target_name=day.get("chosen_target"),
                             note=day.get("note"))
    except food.InvalidFood as exc:
        st.error(str(exc))
        return
    if result["added"]:
        st.success("Added, and put "
                   + ", ".join(f"'{row['name']}'" for row in result["added"])
                   + " in the catalogue.")
    else:
        st.success("Added.")
    st.rerun()


def _edit(when, row) -> None:
    """Correct or remove lines, set the day's target, copy another day in."""
    entries = fq.entries(when)

    if entries:
        st.subheader("Change a line")
        labels = {item["id"]: f"{item['meal']} · {item['name']} · "
                              f"{food.fmt_macro('calories', item['calories'])} kcal"
                  for item in entries}
        chosen = st.selectbox("Line", list(labels), index=None,
                              placeholder="Pick a line",
                              format_func=lambda value: labels[value],
                              key="edit_line")
        if chosen is not None:
            _edit_line(when, next(e for e in entries if e["id"] == chosen))

    st.subheader("The day")
    names = fq.target_names()
    current = (row or {}).get("chosen_target")
    with st.form("day_settings"):
        left, right = st.columns(2)
        picked = left.selectbox(
            "Target profile", [None] + names,
            index=(names.index(current) + 1) if current in names else 0,
            format_func=lambda value: value or "— whichever is in force —")
        note = right.text_input("Note", value=(row or {}).get("note") or "")
        if st.form_submit_button("Save", type="primary"):
            fm.save_day(when, [{**item, "_parsed": True} for item in entries],
                        target_name=picked, note=note)
            st.success("Saved.")
            st.rerun()

    left, right = st.columns(2)
    with left:
        st.markdown("**Copy another day into this one**")
        source = st.date_input("Copy from", value=when - dt.timedelta(days=1),
                               format="DD/MM/YYYY", key="copy_source")
        if st.button("Copy it in", disabled=source == when):
            try:
                made = fm.copy_day(source, when)
            except food.InvalidFood as exc:
                st.error(str(exc))
            else:
                st.success(f"Copied {made['entries']} lines.")
                st.rerun()
        st.caption("Replaces everything on this day. This is how the workbook's "
                   "Planner worked: set one day up properly, then paste it.")
    with right:
        st.markdown("**Clear the day**")
        if st.button("Clear", disabled=not entries):
            fm.delete_day(when)
            st.rerun()
        st.caption("Removes every line. The day itself is forgotten, not left "
                   "as a recorded zero.")


def _edit_line(when, entry) -> None:
    with st.form(f"edit_entry_{entry['id']}"):
        left, middle, right = st.columns(3)
        meal = left.selectbox(
            "Meal", config.MEALS,
            index=config.MEALS.index(entry["meal"])
            if entry["meal"] in config.MEALS else 0)
        name = middle.text_input("Name", value=entry["name"])
        quantity = right.number_input("Quantity", min_value=0.0, step=1.0,
                                      value=float(entry["quantity"] or 1))
        units = st.text_input("Units", value=entry["units"] or "")
        cells = st.columns(len(config.MACRO_KEYS))
        macros = {key: cells[index].number_input(
                      config.MACRO_LABELS[key], min_value=0.0, step=1.0,
                      value=float(entry[key]), key=f"edit_{key}")
                  for index, key in enumerate(config.MACRO_KEYS)}

        st.caption("The macros are the record of what was eaten, so they are "
                   "edited directly rather than recalculated from the "
                   "quantity — changing a recipe should not restate a dinner "
                   "from last year.")
        save, remove = st.columns(2)
        saved = save.form_submit_button("Save", type="primary")
        removed = remove.form_submit_button("Remove this line")

    if not (saved or removed):
        return
    rows = [{**item, "_parsed": True} for item in fq.entries(when)
            if item["id"] != entry["id"]]
    if saved:
        rows.append({"meal": meal, "food_id": entry["food_id"], "name": name,
                     "quantity": quantity, "units": units, **macros})
    day = fq.day(when) or {}
    fm.save_day(when, rows, target_name=day.get("chosen_target"),
                note=day.get("note"))
    st.rerun()
