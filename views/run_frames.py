"""Streamlit-side adapter for the run tracker: core queries -> DataFrames.

`core.run_queries` returns plain lists of dicts so that the Flask front-end can
run on the NAS without pulling in pandas (85 MB). Streamlit genuinely wants
DataFrames - st.dataframe and Altair both take them - so the conversion lives
here, on the Streamlit side of the line.

Every function mirrors the same-named one in core.run_queries, and adds the
columns that only make sense once something is going to be displayed: a
formatted duration next to the seconds, a formatted pace next to the float.
The unformatted columns stay, because Altair needs to sort and scale on them.
"""
from __future__ import annotations

import pandas as pd

import config
from core import run_queries, runs


def _frame(rows: list, columns: list | None = None) -> pd.DataFrame:
    """Build a DataFrame that keeps its columns even when there are no rows."""
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=columns or [])
    for name in ("day", "period", "first_day", "last_day"):
        if name in frame.columns:
            frame[name] = pd.to_datetime(frame[name])
    return frame


def _formatted(frame: pd.DataFrame) -> pd.DataFrame:
    """Add the human-readable twin of every duration and pace column.

    Durations are minutes and seconds, and neither a number of seconds nor a
    decimal of a minute is a thing anyone reads. Altair keeps the raw column to
    scale on; the tables show the twin.
    """
    if frame.empty:
        return frame
    for source, target, hours in (("duration_s", "Time", True),
                                  ("seconds", "Time", False),
                                  ("avg_seconds", "Average", False),
                                  ("best_seconds", "Best", False),
                                  ("worst_seconds", "Slowest", False),
                                  ("avg_duration_s", "Average time", True)):
        if source in frame.columns:
            frame[target] = frame[source].map(
                lambda value: runs.fmt_duration(value, hours))
    for source, target in (("pace_s", "Pace"),
                           ("best_pace_s", "Best pace"),
                           ("avg_pace_s", "Average pace")):
        if source in frame.columns:
            frame[target] = frame[source].map(runs.fmt_pace)
    if "interval_type" in frame.columns:
        # One readable column instead of four raw ones. A run flagged as
        # intervals with nothing entered says so, rather than showing blank
        # next to a run that was never a session at all.
        frame["Session"] = [
            runs.interval_summary(row) or
            ("to add" if row.get("run_type") == "Intervals" else "")
            for row in frame.to_dict("records")
        ]
        frame["Interval pace"] = frame["interval_pace_s"].map(
            lambda value: runs.fmt_pace(value) if value else "")
    return frame


# --------------------------------------------------------------------------- #
# Mirrors of core.run_queries
# --------------------------------------------------------------------------- #
def runs_list(**kwargs) -> pd.DataFrame:
    return _formatted(_frame(run_queries.runs_list(**kwargs)))


def by_run_type(**kwargs) -> pd.DataFrame:
    return _formatted(_frame(run_queries.by_run_type(**kwargs)))


def by_effort_type(**kwargs) -> pd.DataFrame:
    return _formatted(_frame(run_queries.by_effort_type(**kwargs)))


def by_split(split: str, **kwargs) -> pd.DataFrame:
    return _formatted(_frame(run_queries.by_split(split, **kwargs)))


def by_breakdown(**kwargs) -> pd.DataFrame:
    return _formatted(_frame(run_queries.by_breakdown(**kwargs)))


def by_period(grain: str = "monthly", **kwargs) -> pd.DataFrame:
    return _formatted(_frame(run_queries.by_period(grain, **kwargs)))


def cross_tab(**kwargs) -> pd.DataFrame:
    return _formatted(_frame(run_queries.cross_tab(**kwargs)))


def bests_for(run_id: int) -> pd.DataFrame:
    return _formatted(_frame(run_queries.bests_for(run_id)))


def anomalies() -> pd.DataFrame:
    return _formatted(_frame(run_queries.anomalies()))


def interval_sessions(**kwargs) -> pd.DataFrame:
    return _formatted(_frame(run_queries.interval_sessions(**kwargs)))


def intervals_outstanding(**kwargs) -> pd.DataFrame:
    return _formatted(_frame(run_queries.intervals_outstanding(**kwargs)))


def records(**kwargs) -> dict:
    """Breakdown label -> DataFrame, in ladder order."""
    return {label: _formatted(_frame(rows))
            for label, rows in run_queries.records(**kwargs).items()}


def audit_trail(limit: int = 100) -> pd.DataFrame:
    return _frame(run_queries.audit_trail(limit),
                  ["ts", "action", "entity", "entity_id", "detail"])


# --------------------------------------------------------------------------- #
# Column labels and formats
# --------------------------------------------------------------------------- #
# The raw columns a table should not show, because `_formatted` has already
# produced the readable twin. Kept as one list so a table cannot show seconds
# in one place and mm:ss in another.
RAW = ["duration_s", "seconds", "avg_seconds", "best_seconds", "worst_seconds",
       "avg_duration_s", "pace_s", "best_pace_s", "avg_pace_s", "ordinal",
       "run_id", "id", "km", "source", "suspect",
       # The interval columns are shown through `Session`, which reads as
       # "8 x 1k @ 3:50" rather than as four columns of raw numbers.
       "interval_type", "interval_count", "interval_distance_m",
       "interval_split_s", "interval_pace_s", "interval_total_km"]

LABELS = {
    "day": "Date",
    "period": "Period",
    "label": "Type",
    "breakdown": "Distance",
    "distance_km": "Distance (km)",
    "avg_distance_km": "Average run (km)",
    "longest_km": "Longest (km)",
    "runs": "Runs",
    "run_type": "Run type",
    "effort_type": "Effort type",
    "breakdowns": "Splits",
    "note": "Note",
    "first_day": "First",
    "last_day": "Last",
    "position": "#",
    "set_aside": "Set aside",
    "reason": "Why it cannot be",
}

interval_totals = run_queries.interval_totals


def for_display(frame: pd.DataFrame, drop: list | None = None) -> pd.DataFrame:
    """Drop the raw numeric twins and rename what is left to its label."""
    if frame.empty:
        return frame
    unwanted = set(RAW) | set(drop or [])
    keep = [name for name in frame.columns if name not in unwanted]
    return frame[keep].rename(columns=LABELS)


def column_config() -> dict:
    """Formats for the columns that are still numbers when displayed."""
    import streamlit as st

    return {
        "Date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
        "First": st.column_config.DateColumn("First", format="DD/MM/YYYY"),
        "Last": st.column_config.DateColumn("Last", format="DD/MM/YYYY"),
        "Period": st.column_config.DateColumn("Period", format="MMM YYYY"),
        "Distance (km)": st.column_config.NumberColumn(
            "Distance (km)", format="%.2f"),
        "Average run (km)": st.column_config.NumberColumn(
            "Average run (km)", format="%.2f"),
        "Longest (km)": st.column_config.NumberColumn(
            "Longest (km)", format="%.2f"),
    }


# Passed straight through - these already return plain values.
coverage = run_queries.coverage
totals = run_queries.totals
run = run_queries.run
latest = run_queries.latest
recent = run_queries.recent
personal_bests = run_queries.personal_bests
is_record = run_queries.is_record
distinct = run_queries.distinct
range_start = run_queries.range_start
suspect_count = run_queries.suspect_count
runs_on = run_queries.runs_on
total_runs = run_queries.total_runs

RANGES = run_queries.RANGES
DEFAULT_RANGE = run_queries.DEFAULT_RANGE
GRAINS = run_queries.GRAINS
BREAKDOWNS = config.BREAKDOWNS
