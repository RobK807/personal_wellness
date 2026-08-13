"""Altair versions of the charts in web/charts.py.

Same shapes, richer rendering: the Flask front-end hand-rolls SVG because the
NAS cannot afford Altair's dependencies, and this side has no such constraint,
so it gets tooltips and interactive panning for free.

The paired charts use `resolve_scale(y="independent")`, which is Altair's way
of saying what the workbook said with a secondary axis: these two series share
an x-axis and nothing else.
"""
from __future__ import annotations

import altair as alt
import pandas as pd

import config

COLOURS = ["#1f6f6b", "#b5651d"]

AXIS_FORMAT = {0: ",d", 1: ".1f", 2: ".2f"}


def _axis_title(key: str) -> str:
    unit = config.UNITS.get(key, "")
    label = config.LABELS.get(key, key)
    return f"{label} ({unit})" if unit and unit != "%" else label


def _time_unit(grain: str) -> str:
    return {"daily": "yearmonthdate", "weekly": "yearmonthdate",
            "monthly": "yearmonth"}.get(grain, "yearmonthdate")


def _line(df: pd.DataFrame, key: str, grain: str, colour: str,
          independent: bool = False) -> alt.Chart:
    dp = config.DP.get(key, 1)
    return (
        alt.Chart(df)
        .mark_line(strokeWidth=1.8, color=colour, clip=True)
        .encode(
            x=alt.X("period:T", title=None,
                    axis=alt.Axis(format="%d %b %y", labelOverlap=True)),
            y=alt.Y(f"{key}:Q",
                    title=_axis_title(key),
                    scale=alt.Scale(zero=False, nice=True),
                    axis=alt.Axis(format=AXIS_FORMAT.get(dp, ".1f"),
                                  titleColor=colour if independent else None,
                                  orient="right" if independent else "left")),
            tooltip=[
                alt.Tooltip("period:T", title="Date",
                            format="%b %Y" if grain == "monthly" else "%d/%m/%Y"),
                alt.Tooltip(f"{key}:Q", title=config.LABELS.get(key, key),
                            format=f".{dp}f"),
            ],
        )
    )


def pair(df: pd.DataFrame, key_a: str, key_b: str | None = None,
         grain: str = "daily", height: int = 260):
    """One or two metrics against time, the second on its own y-axis."""
    if df.empty:
        return None
    first = _line(df, key_a, grain, COLOURS[0]).properties(height=height)
    if not key_b:
        return first
    second = _line(df, key_b, grain, COLOURS[1], independent=True)
    return alt.layer(first, second).resolve_scale(y="independent")


def line(df: pd.DataFrame, key: str, grain: str = "daily", height: int = 240):
    return pair(df, key, None, grain, height)


def change_bars(df: pd.DataFrame, key: str, grain: str = "weekly",
                height: int = 220):
    """A change series as bars, coloured by whether the move is the good one."""
    if df.empty:
        return None
    dp = config.DP.get(key, 1)
    wanted_down = config.BETTER.get(key) == "down"
    good, bad = "#2f6f43", "#9b2c2c"
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("period:T", title=None,
                    axis=alt.Axis(format="%d %b %y", labelOverlap=True)),
            y=alt.Y(f"{key}:Q", title=f"Change in {config.LABELS.get(key, key)}",
                    axis=alt.Axis(format=AXIS_FORMAT.get(dp, ".1f"))),
            color=alt.condition(
                alt.datum[key] < 0,
                alt.value(good if wanted_down else bad),
                alt.value(bad if wanted_down else good),
            ),
            tooltip=[
                alt.Tooltip("period:T", title="Period", format="%d/%m/%Y"),
                alt.Tooltip(f"{key}:Q", title="Change", format=f"+.{dp}f"),
            ],
        )
        .properties(height=height)
    )
