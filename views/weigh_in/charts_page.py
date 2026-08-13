"""Charts - the workbook's Charts and Weekly Charts sheets, made adjustable."""
from __future__ import annotations

import streamlit as st

import config
from core import metrics, queries as core_queries
from views import altair_charts as ac
from views import frames as queries


def render() -> None:
    st.title("Charts")
    st.caption("The workbook's Charts and Weekly Charts sheets, with the period "
               "and the averaging both adjustable rather than fixed.")

    left, right = st.columns(2)
    span = left.segmented_control(
        "Range", [label for label, _ in core_queries.RANGES],
        default=core_queries.DEFAULT_RANGE)
    grain = right.segmented_control("Averaged", list(core_queries.GRAINS),
                                    default="daily")
    span = span or core_queries.DEFAULT_RANGE
    grain = grain or "daily"

    rows = queries.series(grain, start=queries.range_start(span))
    if rows.empty:
        st.info("Nothing recorded in this range.")
        return

    period_name = {"daily": "day", "weekly": "week", "monthly": "month"}[grain]
    st.caption(
        f"{len(rows):,} {period_name}{'' if len(rows) == 1 else 's'}, "
        f"{metrics.period_label(grain, rows['period'].iloc[0])} to "
        f"{metrics.period_label(grain, rows['period'].iloc[-1])}."
    )

    st.subheader("Paired")
    for key_a, key_b in config.CHART_PAIRS:
        chart = ac.pair(rows, key_a, key_b, grain)
        if chart is None:
            continue
        st.markdown(f"**{config.LABELS[key_a]} and {config.LABELS[key_b]}**")
        st.altair_chart(chart, width="stretch")
        st.caption(f"{config.LABELS[key_a]} on the left axis, "
                   f"{config.LABELS[key_b]} on the right.")

    st.subheader("Individual")
    for key, label, *_ in config.ALL_METRICS:
        chart = ac.line(rows, key, grain)
        if chart is None:
            continue
        st.markdown(f"**{label}**")
        st.altair_chart(chart, width="stretch")
