"""Changes - the workbook's three change sheets, plus day-on-day."""
from __future__ import annotations

import streamlit as st

import config
from views import altair_charts as ac
from views import frames as queries

# label -> (how to fetch it, the grain its periods are in, what it means)
BASES = {
    "Day on day": ("daily", "daily", "Each day against the one before."),
    "Week on week": ("weekly", "weekly",
                     "Each week's average against the previous week's."),
    "Month on month": ("monthly", "monthly",
                       "Each month's average against the previous month's — "
                       "the workbook's Monthly average change sheet."),
    "Rolling 7 days": ("rolling", "daily",
                       "Each day against the same day a week earlier — the "
                       "workbook's Weekly changes, daily rolling."),
    "Weekly average of the rolling change": (
        "weekly_avg", "weekly",
        "The mean of the rolling seven-day change across each week."),
}


def render() -> None:
    st.title("Changes")

    choice = st.segmented_control("Basis", list(BASES), default="Week on week")
    choice = choice or "Week on week"
    basis, grain, caption = BASES[choice]
    limit = st.slider("Periods shown", min_value=5, max_value=200, value=26,
                      step=1)

    if basis == "rolling":
        rows = queries.rolling_change(7, limit=limit)
    elif basis == "weekly_avg":
        rows = queries.weekly_average_change(limit=limit)
    else:
        rows = queries.changes(basis, limit=limit)

    st.caption(caption)
    if rows.empty:
        st.info("Not enough data for this comparison yet.")
        return

    metric = st.selectbox("Chart", config.ALL_KEYS, index=0,
                          format_func=lambda key: config.LABELS[key])
    chart = ac.change_bars(rows, metric, grain)
    if chart is not None:
        st.altair_chart(chart, width="stretch")
        direction = "down" if config.BETTER[metric] == "down" else "up"
        st.caption(f"Green is movement in the direction you want — "
                   f"{direction} for {config.LABELS[metric].lower()}.")

    display = queries.for_display(
        rows.drop(columns=[c for c in ("previous", "estimated_days")
                           if c in rows.columns]),
        grain,
    ).iloc[::-1]
    st.dataframe(display, hide_index=True, width="stretch",
                 column_config={
                     **queries.column_config(grain),
                     **{label: st.column_config.NumberColumn(
                         label, format=f"%+.{dp}f")
                        for key, label, unit, dp, _ in config.ALL_METRICS},
                 })
