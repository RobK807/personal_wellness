"""Streamlit-side adapter: core queries -> pandas DataFrames.

`core.queries` returns plain lists of dicts so that the Flask front-end can run
on the NAS without pulling in pandas (85 MB). Streamlit genuinely wants
DataFrames - st.dataframe and Altair both take them - so the conversion lives
here, on the Streamlit side of the line.

Every function mirrors the same-named one in core.queries.
"""
from __future__ import annotations

import pandas as pd

import config
from core import queries

COLUMNS = ["period", *config.ALL_KEYS, "days", "estimated_days"]


def _frame(rows: list, columns: list | None = None) -> pd.DataFrame:
    """Build a DataFrame that keeps its columns even when there are no rows."""
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=columns or COLUMNS)
    if "period" in df.columns:
        df["period"] = pd.to_datetime(df["period"])
    return df


def series(grain: str = "daily", **kwargs) -> pd.DataFrame:
    return _frame(queries.series(grain, **kwargs))


def changes(grain: str = "daily", **kwargs) -> pd.DataFrame:
    return _frame(queries.changes(grain, **kwargs))


def rolling_change(window: int = 7, **kwargs) -> pd.DataFrame:
    return _frame(queries.rolling_change(window, **kwargs))


def weekly_average_change(**kwargs) -> pd.DataFrame:
    return _frame(queries.weekly_average_change(**kwargs))


def recent_days(**kwargs) -> pd.DataFrame:
    return _frame(queries.recent_days(**kwargs))


def gaps(**kwargs) -> pd.DataFrame:
    return _frame(queries.gaps(**kwargs), ["after", "before", "days", "too_long"])


def audit_trail(limit: int = 100) -> pd.DataFrame:
    return _frame(queries.audit_trail(limit),
                  ["ts", "action", "entity", "entity_id", "detail"])


def for_display(df: pd.DataFrame, grain: str = "daily") -> pd.DataFrame:
    """Rename the columns to their labels, ready for st.dataframe."""
    if df.empty:
        return df
    label = {"period": {"daily": "Date", "weekly": "Week commencing",
                        "monthly": "Month"}.get(grain, "Period"),
             "days": "Days", "estimated_days": "Estimated",
             "readings": "Weigh-ins", "estimated": "Estimated"}
    label.update(config.LABELS)
    return df.rename(columns=label)


def column_config(grain: str = "daily") -> dict:
    """Number formats matching each metric's precision."""
    import streamlit as st

    formats = {}
    for key, label, unit, dp, _ in config.ALL_METRICS:
        formats[label] = st.column_config.NumberColumn(
            label, format=f"%.{dp}f", help=f"{label}{f' ({unit})' if unit else ''}")
    date_label = {"daily": "Date", "weekly": "Week commencing",
                  "monthly": "Month"}.get(grain, "Period")
    formats[date_label] = st.column_config.DateColumn(
        date_label, format="DD/MM/YYYY" if grain != "monthly" else "MMM YYYY")
    return formats


# Passed straight through - these already return plain values.
headline = queries.headline
coverage = queries.coverage
day = queries.day
readings_for = queries.readings_for
range_start = queries.range_start
