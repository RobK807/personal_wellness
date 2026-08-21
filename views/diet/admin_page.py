"""Where a new line's List and Grouping start, and the week's shape.

Preferences rather than data, which is why they live in food_settings and why a
blank one falls back to config rather than being stored as empty - see the note
on that table in core/schema.sql.
"""
from __future__ import annotations

import datetime as dt

import streamlit as st

import config
from core import food_mutations as fm, food_queries as fq


def render() -> None:
    st.title("Admin")
    st.caption(
        "Preferences rather than data. Everything here has a built-in default, "
        "so clearing a setting hands it back rather than pinning it to nothing.")

    _defaults()
    st.divider()
    _fixed()
    st.divider()
    _coverage()


def _defaults() -> None:
    st.subheader("Where a new line starts")
    st.caption(
        "The **List** and **Grouping** each meal's rows open on, on the Day "
        "page and in the week planner. This is what stops the food picker "
        "being 187 items long — pick the meal and it is already narrowed to the "
        "kind of thing that meal usually is. Every row can still be changed, "
        "and a food typed into a row is filed under whatever those two are "
        "showing at the time.")

    groups = fq.groupings_by_list()
    current = fq.meal_defaults()
    stored = fq.settings()

    with st.form("food_settings"):
        chosen = {}
        for meal in config.MEALS:
            was_list, was_grouping = current[meal]
            left, middle, right = st.columns([1, 1, 1])
            list_name = left.selectbox(
                f"{meal} — List", config.FOOD_LISTS,
                index=config.FOOD_LISTS.index(was_list)
                if was_list in config.FOOD_LISTS else 0,
                key=f"admin_list_{meal}")
            # Every grouping, not only this list's: the List box above is inside
            # the same form, so its value here is the one from the last rerun
            # and filtering to it would offer the wrong set for one submit.
            every = sorted({name for values in groups.values()
                            for name in values})
            options = [""] + every
            grouping = middle.selectbox(
                f"{meal} — Grouping", options,
                index=options.index(was_grouping)
                if was_grouping in options else 0,
                format_func=lambda value: value or "— any —",
                key=f"admin_grouping_{meal}")
            has_own = (stored.get(f"default_list:{meal}")
                       or stored.get(f"default_grouping:{meal}"))
            built_in = config.FOOD_MEAL_DEFAULTS.get(meal, ("Items", ""))
            right.caption(
                "Set here."
                if has_own else
                f"Built-in: {built_in[0]}"
                + (f" / {built_in[1]}" if built_in[1] else ""))
            chosen[meal] = (list_name, grouping)

        st.markdown("**The week**")
        left, right = st.columns([1, 2])
        starts_on = left.selectbox(
            "A planning week starts on", range(7),
            index=fq.week_starts_on(),
            format_func=lambda value: config.WEEKDAY_NAMES[value],
            key="admin_week_starts_on")
        right.caption(
            "The imported history is Monday-based and its \"w/c\" labels say "
            "so, but which day a *planning* week turns over on is a habit. "
            "Changing this does not touch a single recorded day — it changes "
            "which seven the Week page puts side by side. Built-in: "
            f"{config.WEEKDAY_NAMES[config.WEEK_STARTS_ON]}.")

        if st.form_submit_button("Save", type="primary"):
            values = {}
            for meal, (list_name, grouping) in chosen.items():
                values[f"default_list:{meal}"] = list_name
                values[f"default_grouping:{meal}"] = grouping
            values["week_starts_on"] = str(starts_on)
            fm.save_settings(values)
            st.success("Saved. New lines start here from now on.")
            st.rerun()

    if st.button("Put them all back to the built-in defaults"):
        fm.save_settings({key: "" for key in
                          [f"default_list:{meal}" for meal in config.MEALS]
                          + [f"default_grouping:{meal}" for meal in config.MEALS]
                          + ["week_starts_on"]})
        st.success("Back to the built-in defaults.")
        st.rerun()


def _fixed() -> None:
    st.subheader("Fixed here, not settable")
    st.markdown(
        f"- **{config.MAX_ENTRIES_PER_MEAL} lines per meal** on the Day page — "
        f"the shape of the workbook's own day block, and its longest one.\n"
        f"- **Near-miss sensitivity {config.FOOD_MATCH_RATIO:g}** — how alike "
        f"two names have to be before one is offered as \"did you mean\". Lower "
        f"it and it starts suggesting a pear is an apple, which trains you to "
        f"dismiss the alert without reading it. Set `PW_FOOD_MATCH_RATIO` to "
        f"change it.")


def _coverage() -> None:
    st.subheader("What is in here")
    coverage = fq.coverage()
    cells = st.columns(4)
    cells[0].metric("Days recorded", f"{coverage['days']:,}")
    cells[1].metric("Diary lines", f"{coverage['entries']:,}")
    cells[2].metric("Linked to a food", f"{coverage['linked']:,}",
                    help=f"of {coverage['entries']:,}")
    cells[3].metric("Catalogue", f"{fq.total_foods():,}", help="foods")
    st.caption(f"Database `{config.DB_PATH}`  \n"
               f"Workbook `{config.FOOD_XLSX}` — read by the importer only, "
               f"and only on a desktop.")
