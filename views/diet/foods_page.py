"""The catalogue - the three lists behind every dropdown in this section."""
from __future__ import annotations

import streamlit as st

import config
from core import food, food_mutations as fm, food_queries as fq


def render() -> None:
    st.title("Catalogue")
    st.caption(
        "Not categories of food — a way of finding one: an **Item** is a "
        "single thing, a **Meal** is bought or assembled, a **Recipe** is "
        "cooked. The same name can sit in two lists, and does: the workbook "
        "has 'Mashed potato' as both an Item and a Recipe.")

    _add()
    st.divider()
    _catalogue()


def _add() -> None:
    st.subheader("Add a food")
    with st.form("add_food", clear_on_submit=True):
        left, middle, right = st.columns(3)
        name = left.text_input("Name", placeholder="Granny smith apple")
        list_name = middle.selectbox("List", config.FOOD_LISTS)
        grouping = right.selectbox(
            "Grouping", [None] + sorted({value for values
                                         in config.FOOD_GROUPINGS.values()
                                         for value in values}),
            format_func=lambda value: value or "— none —")
        left, right = st.columns(2)
        portion = left.number_input("Portion", min_value=0.0, value=1.0,
                                    step=1.0)
        units = right.selectbox("Units", config.FOOD_UNITS)
        cells = st.columns(len(config.MACRO_KEYS))
        macros = {key: cells[index].number_input(
                      config.MACRO_LABELS[key], min_value=0.0, value=0.0,
                      step=1.0, key=f"new_food_{key}")
                  for index, key in enumerate(config.MACRO_KEYS)}
        st.caption("The macros are for **one portion** as given above — 266 "
                   "kcal per 75 grams, not per gram. Everything else in the "
                   "section divides by that.")
        if st.form_submit_button("Add", type="primary"):
            try:
                saved = fm.save_food({"name": name, "list": list_name,
                                      "grouping": grouping, "portion": portion,
                                      "units": units, **macros})
            except food.InvalidFood as exc:
                st.error(str(exc))
            else:
                st.success(f"Added '{saved['name']}'.")
                st.rerun()


def _catalogue() -> None:
    st.subheader("The catalogue")
    left, right = st.columns([1, 2])
    chosen_list = left.selectbox(
        "List", [None] + config.FOOD_LISTS,
        format_func=lambda value: value or "All")
    search = right.text_input("Search", placeholder="name")

    rows = fq.foods(list_name=chosen_list, search=search or None,
                    include_retired=True)
    usage = fq.food_usage()
    st.caption(f"{len(rows)} food{'' if len(rows) == 1 else 's'}.")
    st.dataframe([{
        "Name": row["name"],
        "List": row["list"],
        "Grouping": row["grouping"] or "",
        "Portion": food.fmt_quantity(row["portion"], row["units"]),
        **{config.MACRO_LABELS[key]: row[key] for key in config.MACRO_KEYS},
        "Diary lines": usage.get(row["id"], 0),
        "In the dropdown": "no" if row["retired"] else "yes",
        "Note": row["note"] or "",
    } for row in rows], hide_index=True, width="stretch")

    labels = {row["id"]: f"{row['name']} · {row['list']}"
                         + (" (retired)" if row["retired"] else "")
              for row in rows}
    chosen = st.selectbox("Change one", list(labels), index=None,
                          placeholder="Pick a food",
                          format_func=lambda value: labels[value])
    if chosen is None:
        return
    _edit(next(row for row in rows if row["id"] == chosen),
          usage.get(chosen, 0))


def _edit(current: dict, uses: int) -> None:
    with st.form("edit_food"):
        left, middle, right = st.columns(3)
        name = left.text_input("Name", value=current["name"])
        list_name = middle.selectbox(
            "List", config.FOOD_LISTS,
            index=config.FOOD_LISTS.index(current["list"]))
        grouping = right.text_input("Grouping", value=current["grouping"] or "")
        left, right = st.columns(2)
        portion = left.number_input("Portion", min_value=0.0, step=1.0,
                                    value=float(current["portion"]))
        units = right.text_input("Units", value=current["units"])
        cells = st.columns(len(config.MACRO_KEYS))
        macros = {key: cells[index].number_input(
                      config.MACRO_LABELS[key], min_value=0.0, step=1.0,
                      value=float(current[key]), key=f"edit_food_{key}")
                  for index, key in enumerate(config.MACRO_KEYS)}
        note = st.text_input("Note", value=current["note"] or "")
        if st.form_submit_button("Save", type="primary"):
            try:
                fm.save_food({"name": name, "list": list_name,
                              "grouping": grouping, "portion": portion,
                              "units": units, "note": note, **macros},
                             food_id=current["id"])
            except food.InvalidFood as exc:
                st.error(str(exc))
            else:
                st.success("Saved.")
                st.rerun()

    left, middle = st.columns(2)
    if current["retired"]:
        if left.button("Put back in the dropdowns"):
            fm.retire_food(current["id"], False)
            st.rerun()
    else:
        if left.button("Retire"):
            fm.retire_food(current["id"], True)
            st.rerun()
    if middle.button("Delete", disabled=bool(uses)):
        try:
            fm.delete_food(current["id"])
            st.rerun()
        except food.InvalidFood as exc:
            st.error(str(exc))

    if uses:
        st.caption(
            f"On {uses} diary line{'' if uses == 1 else 's'}, so it cannot be "
            f"deleted — retiring takes it out of the dropdowns and leaves that "
            f"history intact. Editing the macros here does **not** restate "
            f"those lines: a diary entry carries its own numbers, because the "
            f"record is of what was eaten.")
