"""Two years of diary, averaged - and what is still free text.

Charts here and hand-rolled tables on the Flask side, for the usual reason: the
NAS cannot afford Altair, and this side can.
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

import config
from core import food, food_queries as fq
from views.diet import shared

COLOURS = {"calories": "#1f6f6b", "carbs": "#b5651d",
           "fat": "#8a6100", "protein": "#2f6f43"}


def render() -> None:
    st.title("Analysis")
    if shared.empty_section():
        return

    coverage = fq.coverage()
    cells = st.columns(4)
    cells[0].metric("Days recorded", f"{coverage['days']:,}")
    cells[1].metric("Diary lines", f"{coverage['entries']:,}")
    cells[2].metric("Linked to a food", f"{coverage['linked']:,}",
                    help=f"of {coverage['entries']:,}")
    cells[3].metric("Catalogue", f"{fq.total_foods():,}", help="foods")
    if coverage["first_day"]:
        st.caption(
            f"{food.as_date(coverage['first_day']):%d/%m/%Y} to "
            f"{food.as_date(coverage['last_day']):%d/%m/%Y}. The history has "
            f"gaps — whole weeks the workbook never recorded — and they are "
            f"left as gaps rather than filled in with anything.")

    st.divider()
    grain = st.radio("Grouped", list(fq.GRAINS), index=2, horizontal=True,
                     format_func=str.capitalize)
    periods = fq.by_period(grain)
    if not periods:
        st.info("Nothing recorded yet.")
        return

    _charts(periods, grain)
    _table(periods)
    st.divider()
    _free_text()


def _charts(periods: list, grain: str) -> None:
    """Average day per period, against the target in force.

    Days with nothing recorded are left out of the averages rather than counted
    as zeros - a month with four blank days did not average 1,400 calories.
    """
    st.caption("Per day, over the days that have something against them.")
    df = pd.DataFrame(periods)
    df["period"] = pd.to_datetime(df["period"])

    for row in (config.MACRO_KEYS[:2], config.MACRO_KEYS[2:]):
        for cell, key in zip(st.columns(len(row)), row):
            cell.altair_chart(_chart(df, key, grain), use_container_width=True)


def _chart(df: pd.DataFrame, key: str, grain: str) -> alt.Chart:
    dp = config.MACRO_DP.get(key, 1)
    fmt = ",d" if dp == 0 else f".{dp}f"
    label = config.MACRO_LABELS[key]
    unit = config.MACRO_UNITS[key]

    actual = (
        alt.Chart(df)
        .mark_line(strokeWidth=1.8, color=COLOURS[key], point=len(df) < 40,
                   clip=True)
        .encode(
            x=alt.X("period:T", title=None,
                    axis=alt.Axis(format="%b %y", labelOverlap=True)),
            y=alt.Y(f"{key}:Q", title=f"{label} ({unit})",
                    scale=alt.Scale(zero=False, nice=True),
                    axis=alt.Axis(format=fmt)),
            tooltip=[
                alt.Tooltip("period:T", title="Period",
                            format="%b %Y" if grain == "monthly" else "%d/%m/%Y"),
                alt.Tooltip(f"{key}:Q", title=label, format=fmt),
                alt.Tooltip("days:Q", title="Days recorded"),
            ],
        )
    )
    # The target as a dashed line rather than a second series: it is what the
    # first one is being read against, and it moves in steps when a new version
    # starts rather than being a measurement of anything.
    target = (
        alt.Chart(df)
        .mark_line(strokeWidth=1.2, strokeDash=[4, 3], color="#a8a29a",
                   interpolate="step-after", clip=True)
        .encode(x=alt.X("period:T"), y=alt.Y(f"target_{key}:Q"))
    )
    return (actual + target).properties(height=220).resolve_scale(y="shared")


def _table(periods: list) -> None:
    st.dataframe([{
        "Period": row["period"],
        "Days": row["days"],
        **{config.MACRO_LABELS[key]: row[key] for key in config.MACRO_KEYS},
        "vs target": (round(row["calories"] - row["target_calories"], 1)
                      if row["target_calories"] else None),
    } for row in reversed(periods)], hide_index=True, width="stretch")


def _free_text() -> None:
    rows = fq.unmatched_names(25)
    if not rows:
        st.success("Every diary line names something in the catalogue.")
        return
    st.subheader("Lines with no food behind them")
    st.caption(
        "Free text — eaten out, or something never added to the catalogue. "
        "Their macros count towards every total on this page; what they cannot "
        "do is be looked up, re-used or corrected in one place. Adding the "
        "common ones to the catalogue is the fix.")
    st.dataframe([{
        "Name": row["name"],
        "Lines": row["uses"],
        "First": food.as_date(row["first_day"]).strftime("%d/%m/%Y"),
        "Last": food.as_date(row["last_day"]).strftime("%d/%m/%Y"),
    } for row in rows], hide_index=True, width="stretch")
