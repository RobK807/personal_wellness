"""The macro calculator: components in, macros out, times a scaling factor.

The workbook's Calculator sheet. Seven rows make a burrito; the pan of pancake
mix makes rather more than the 200 g that gets eaten, so the total is scaled
after it is added up. The scale applies to the total rather than to any one row,
because that is the question being asked - "what is two thirds of this".

The components live in session state rather than in the database. Most of what
gets worked out here is a one-off, and the only thing worth keeping is the
answer - which is what the "save to the catalogue" button is for.
"""
from __future__ import annotations

import streamlit as st

import config
from core import food, food_mutations as fm, food_queries as fq
from views.diet import shared

STATE = "calculator_rows"


def render() -> None:
    st.title("Macro calculator")
    st.caption(
        "Build something out of its components, then take a proportion of it. "
        "Nothing here is stored unless you save the answer to the catalogue.")

    rows = st.session_state.setdefault(STATE, [])
    _add(rows)
    st.divider()

    if not rows:
        st.info("No components yet. Add one above — a catalogue food and a "
                "quantity, or a name and the four numbers off a packet.")
        return

    scale = st.number_input(
        "Scaling factor", min_value=0.0, value=1.0, step=0.1,
        help="Applied to the total, not to any one row — 0.5 for half of it.")
    result = fq.calculate(rows, scale)
    _components(result, rows)
    _total(result)
    st.divider()
    _save(result)


def _add(rows: list) -> None:
    st.subheader("Add a component")
    catalogue = fq.foods()
    chosen = shared.pick_food("Food", key="calc_food", catalogue=catalogue)

    with st.form("add_component", clear_on_submit=True):
        left, right = st.columns(2)
        quantity = left.number_input(
            "Quantity", min_value=0.0, step=1.0,
            value=float(chosen["portion"]) if chosen else 1.0)
        units = right.text_input("Units",
                                 value=chosen["units"] if chosen else "",
                                 disabled=chosen is not None)
        if chosen is None:
            name = st.text_input("Name", placeholder="something off a packet")
            cells = st.columns(len(config.MACRO_KEYS))
            macros = {key: cells[index].number_input(
                          config.MACRO_LABELS[key], min_value=0.0, step=1.0,
                          value=0.0, key=f"calc_{key}")
                      for index, key in enumerate(config.MACRO_KEYS)}
            st.caption("Macros typed here are **per unit** and multiplied by "
                       "the quantity. A component from the catalogue is scaled "
                       "by its own portion instead.")
        else:
            name, macros = chosen["name"], {}

        if not st.form_submit_button("Add", type="primary"):
            return

    if chosen is not None:
        rows.append({"food_id": chosen["id"], "quantity": quantity})
    elif name or any(macros.values()):
        rows.append({"name": name, "quantity": quantity, "units": units,
                     **macros})
    else:
        st.warning("Nothing to add — pick a food, or give a name and macros.")
        return
    st.rerun()


def _components(result: dict, rows: list) -> None:
    st.subheader(f"{len(result['components'])} component"
                 f"{'' if len(result['components']) == 1 else 's'}")
    st.dataframe([{
        "Component": row["name"],
        "Amount": food.fmt_quantity(row["quantity"], row["units"]),
        **{config.MACRO_LABELS[key]: row[key] for key in config.MACRO_KEYS},
        "Source": "catalogue" if row["from_catalogue"] else "typed in",
    } for row in result["components"]], hide_index=True, width="stretch")

    labels = {index: row["name"]
              for index, row in enumerate(result["components"])}
    left, right = st.columns(2)
    with left:
        drop = st.selectbox("Remove one", list(labels), index=None,
                            placeholder="Pick a component",
                            format_func=lambda value: labels[value],
                            key="calc_drop")
        if drop is not None and st.button("Remove"):
            rows.pop(drop)
            st.rerun()
    with right:
        st.caption("")
        if st.button("Start again"):
            st.session_state[STATE] = []
            st.rerun()


def _total(result: dict) -> None:
    if result["scale"] != 1:
        st.subheader(f"Subtotal, before scaling")
        shared.macro_tiles(result["subtotal"])
        st.subheader(f"Total, scaled to {result['scale']:g}")
    else:
        st.subheader("Total")
    shared.macro_tiles(result["total"])


def _save(result: dict) -> None:
    """Worth keeping when it is a recipe rather than a one-off."""
    st.subheader("Save this to the catalogue")
    with st.form("save_calculated"):
        left, middle, right = st.columns(3)
        name = left.text_input("Name")
        list_name = middle.selectbox("List", config.FOOD_LISTS,
                                     index=config.FOOD_LISTS.index("Recipes"))
        grouping = right.text_input("Grouping", placeholder="optional")
        left, right = st.columns(2)
        portion = left.number_input("Portion", min_value=0.0, value=1.0,
                                    step=1.0)
        units = right.text_input("Units", value="Portion")
        st.caption("The total above becomes this food's macros for one portion "
                   "of it.")
        if not st.form_submit_button("Save to the catalogue"):
            return

    count = len(result["components"])
    note = (f"Built in the calculator from {count} "
            f"component{'' if count == 1 else 's'}")
    if result["scale"] != 1:
        note += f", scaled to {result['scale']:g}"
    try:
        saved = fm.save_food({"name": name, "list": list_name,
                              "grouping": grouping, "portion": portion,
                              "units": units, "note": note,
                              **result["total"]})
    except food.InvalidFood as exc:
        st.error(str(exc))
        return
    st.success(f"Saved '{saved['name']}' to {saved['list']}.")
