"""Target profiles: named, so a training day and a rest day can differ, and
dated, so raising today's protein target does not restate a day from 2024 as a
failure against a number that did not exist then.
"""
from __future__ import annotations

import datetime as dt

import streamlit as st

import config
from core import food, food_mutations as fm, food_queries as fq
from views.diet import shared


def render() -> None:
    st.title("Targets")
    st.caption(
        "What a day is measured against. Each save adds a version from a date "
        "rather than overwriting what came before, so a day keeps being "
        "measured against what was true at the time.")

    in_force = fq.target_for(dt.date.today())
    if in_force:
        st.subheader("In force today")
        shared.macro_tiles(in_force)
        st.caption(f"**{in_force['name']}**, from "
                   f"{food.as_date(in_force['starts_on']):%d/%m/%Y}"
                   + (f" — {in_force['note']}" if in_force["note"] else ""))

    st.divider()
    _set(in_force)
    st.divider()
    _versions(in_force)


def _set(in_force) -> None:
    st.subheader("Set a target")
    names = fq.target_names()
    with st.form("set_target"):
        left, middle, right = st.columns(3)
        name = left.text_input("Profile",
                               value=config.DEFAULT_TARGET,
                               help="Re-using a name adds a version to it.")
        starts_on = middle.date_input("From", value=dt.date.today(),
                                      format="DD/MM/YYYY")
        note = right.text_input("Note", placeholder="optional")
        cells = st.columns(len(config.MACRO_KEYS))
        macros = {key: cells[index].number_input(
                      config.MACRO_LABELS[key], min_value=0.0, step=1.0,
                      value=float(in_force[key]) if in_force else 0.0,
                      key=f"target_{key}")
                  for index, key in enumerate(config.MACRO_KEYS)}
        if st.form_submit_button("Save", type="primary"):
            try:
                saved = fm.save_target({"name": name, "starts_on": starts_on,
                                        "note": note, **macros})
            except food.InvalidFood as exc:
                st.error(str(exc))
            else:
                st.success(f"Saved '{saved['name']}' from "
                           f"{saved['starts_on']:%d/%m/%Y}.")
                st.rerun()
    if names:
        st.caption("Profiles so far: " + ", ".join(names)
                   + ". A day picks the newest version dated on or before that "
                     "day — which is why the seeded one starts in 2000, so the "
                     "imported diary has something to be compared against.")


def _versions(in_force) -> None:
    st.subheader("Every version")
    rows = fq.targets()
    st.dataframe([{
        "Profile": row["name"],
        "From": food.as_date(row["starts_on"]).strftime("%d/%m/%Y"),
        **{config.MACRO_LABELS[key]: row[key] for key in config.MACRO_KEYS},
        "In force": "yes" if in_force and row["id"] == in_force["id"] else "",
        "Note": row["note"] or "",
    } for row in rows], hide_index=True, width="stretch")

    labels = {row["id"]: f"{row['name']} · from "
                         f"{food.as_date(row['starts_on']):%d/%m/%Y}"
              for row in rows}
    chosen = st.selectbox("Change one", list(labels), index=None,
                          placeholder="Pick a version",
                          format_func=lambda value: labels[value])
    if chosen is None:
        return
    current = next(row for row in rows if row["id"] == chosen)

    with st.form("edit_target"):
        left, middle, right = st.columns(3)
        name = left.text_input("Profile", value=current["name"])
        starts_on = middle.date_input(
            "From", value=food.as_date(current["starts_on"]),
            format="DD/MM/YYYY")
        note = right.text_input("Note", value=current["note"] or "")
        cells = st.columns(len(config.MACRO_KEYS))
        macros = {key: cells[index].number_input(
                      config.MACRO_LABELS[key], min_value=0.0, step=1.0,
                      value=float(current[key]), key=f"edit_target_{key}")
                  for index, key in enumerate(config.MACRO_KEYS)}
        if st.form_submit_button("Save", type="primary"):
            try:
                fm.save_target({"name": name, "starts_on": starts_on,
                                "note": note, **macros}, target_id=chosen)
            except food.InvalidFood as exc:
                st.error(str(exc))
            else:
                st.success("Saved.")
                st.rerun()

    if st.button("Delete this version"):
        try:
            fm.delete_target(chosen)
        except food.InvalidFood as exc:
            st.error(str(exc))
        else:
            st.rerun()
