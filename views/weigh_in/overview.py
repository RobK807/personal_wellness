"""Overview - the latest reading, how it has moved, and the paired charts."""
from __future__ import annotations

import streamlit as st

import config
from core import metrics
from views import altair_charts as ac
from views import frames as queries


def render() -> None:
    st.title("Overview")

    head = queries.headline()
    if head["latest"] is None:
        st.warning("Nothing recorded yet. Enter a weigh-in, or import the "
                   "workbook to bring across the history.")
        st.code("python -m core.excel_import --rebuild", language="bash")
        return

    latest, coverage = head["latest"], head["coverage"]
    st.caption(
        f"Latest reading {metrics.period_label('daily', latest['period'])} · "
        f"{coverage['days']:,} days recorded since "
        f"{metrics.period_label('daily', coverage['first_day'])}, "
        f"{coverage['estimated_days']:,} of them interpolated."
    )

    # Two rows of four: the six entered metrics, then the two derived masses.
    day_change = head["changes"].get("day") or {}
    week_change = head["changes"].get("week") or {}
    for start in (0, 4):
        columns = st.columns(4)
        for column, (key, label, unit, dp, _) in zip(
                columns, config.ALL_METRICS[start:start + 4]):
            column.metric(
                label,
                metrics.fmt(key, latest[key], with_unit=True),
                delta=metrics.fmt_change(key, day_change.get(key)),
                # Streamlit's own colouring assumes up is good; these metrics
                # disagree with each other about that, so it is set per metric.
                delta_color="normal" if config.BETTER[key] == "up" else "inverse",
                help=f"Change since the day before. Over a week: "
                     f"{metrics.fmt_change(key, week_change.get(key))}",
            )

    st.divider()

    st.subheader("How it has moved")
    table = []
    for window, change in head["changes"].items():
        row = {"Over": window}
        for key, label, *_ in config.ALL_METRICS:
            row[label] = None if change is None else change[key]
        table.append(row)
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={
                     label: st.column_config.NumberColumn(label, format=f"%+.{dp}f")
                     for key, label, unit, dp, _ in config.ALL_METRICS
                 })
    st.caption("Compared like for like: a day, seven days and twenty-eight days "
               "back from the latest reading, then the whole history.")

    st.divider()

    st.subheader("Last 90 days")
    window = queries.series("daily", start=queries.range_start("90d"))
    for key_a, key_b in config.CHART_PAIRS:
        chart = ac.pair(window, key_a, key_b, "daily")
        if chart is None:
            continue
        st.markdown(f"**{config.LABELS[key_a]} and {config.LABELS[key_b]}**")
        st.altair_chart(chart, width="stretch")
