"""Altair versions of the charts in web/run_charts.py.

Same shapes, richer rendering: the Flask front-end hand-rolls SVG because the
NAS cannot afford Altair's dependencies, and this side has no such constraint,
so it gets tooltips and interactive panning for free.

The one thing worth knowing is `reverse=True` on every pace scale. A pace chart
drawn the usual way round reads exactly backwards - the line falls as you get
quicker - so the axis is inverted and faster sits at the top, which is what
anyone glancing at it will assume anyway.
"""
from __future__ import annotations

import altair as alt
import pandas as pd

from core import runs

COLOUR = "#1f6f6b"
COLOUR_ALT = "#b5651d"


def _pace_axis(title: str = "Pace (min/km)") -> alt.Y:
    """A pace axis: inverted, and labelled in mm:ss rather than seconds.

    Altair's `labelExpr` runs in Vega expression language, not Python, so the
    mm:ss formatting is written out there. It is the same rule as
    core.runs.fmt_pace(): round to the second, then minutes and seconds.
    """
    return alt.Y(
        "pace_s:Q",
        title=title,
        scale=alt.Scale(zero=False, nice=True, reverse=True),
        axis=alt.Axis(labelExpr="floor(round(datum.value) / 60) + ':' + "
                                "format(round(datum.value) % 60, '02d')"),
    )


def _tooltip_period(grain: str) -> alt.Tooltip:
    return alt.Tooltip("period:T", title="Period",
                       format="%b %Y" if grain == "monthly" else "%d/%m/%Y")


def volume(frame: pd.DataFrame, grain: str = "monthly", height: int = 240):
    """Kilometres per period as bars.

    Bars rather than a line, and from zero: this is an amount, not a level, and
    a cropped axis would make a 40 km month look like half a 45 km one.
    """
    if frame.empty:
        return None
    return (
        alt.Chart(frame)
        .mark_bar(color=COLOUR, cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
        .encode(
            x=alt.X("period:T", title=None,
                    axis=alt.Axis(format="%b %y", labelOverlap=True)),
            y=alt.Y("distance_km:Q", title="Distance (km)"),
            tooltip=[_tooltip_period(grain),
                     alt.Tooltip("distance_km:Q", title="Distance (km)",
                                 format=".1f"),
                     alt.Tooltip("runs:Q", title="Runs"),
                     alt.Tooltip("Pace:N", title="Pace")],
        )
        .properties(height=height)
    )


def pace_line(frame: pd.DataFrame, grain: str = "monthly", height: int = 240):
    """Average pace per period, quicker towards the top."""
    if frame.empty:
        return None
    base = alt.Chart(frame).encode(
        x=alt.X("period:T", title=None,
                axis=alt.Axis(format="%b %y", labelOverlap=True)),
        y=_pace_axis(),
        tooltip=[_tooltip_period(grain),
                 alt.Tooltip("Pace:N", title="Pace"),
                 alt.Tooltip("distance_km:Q", title="Distance (km)",
                             format=".1f"),
                 alt.Tooltip("runs:Q", title="Runs")],
    )
    return (base.mark_line(strokeWidth=1.8, color=COLOUR, clip=True)
            + base.mark_point(size=34, filled=True, color=COLOUR)
            ).properties(height=height)


def scatter(frame: pd.DataFrame, colour_by: str = "effort_type",
            height: int = 320):
    """Every run: how far against how fast, coloured by how hard.

    This is the chart the tables cannot replace. A summary row says threshold
    runs average 4:39/km; the scatter says whether that is a tight cluster or
    two habits averaged into one number that describes neither.
    """
    if frame.empty:
        return None
    return (
        alt.Chart(frame)
        .mark_circle(size=52, opacity=.65)
        .encode(
            x=alt.X("distance_km:Q", title="Distance (km)",
                    scale=alt.Scale(zero=False, nice=True)),
            y=_pace_axis(),
            color=alt.Color(f"{colour_by}:N", title=None,
                            legend=alt.Legend(orient="top", columns=3)),
            tooltip=[alt.Tooltip("day:T", title="Date", format="%d/%m/%Y"),
                     alt.Tooltip("distance_km:Q", title="Distance (km)",
                                 format=".2f"),
                     alt.Tooltip("Time:N", title="Time"),
                     alt.Tooltip("Pace:N", title="Pace"),
                     alt.Tooltip("run_type:N", title="Run type"),
                     alt.Tooltip("effort_type:N", title="Effort type")],
        )
        .properties(height=height)
        .interactive()
    )


def category_bars(frame: pd.DataFrame, value: str, title: str,
                  is_pace: bool = False, height: int | None = None):
    """One number per run type or effort type, as horizontal bars.

    Horizontal because "Warm-up / warm down" does not fit under a vertical bar
    at any font size worth reading.
    """
    if frame.empty:
        return None
    height = height or max(len(frame) * 30 + 20, 90)
    axis = (alt.Axis(labelExpr="floor(round(datum.value) / 60) + ':' + "
                               "format(round(datum.value) % 60, '02d')")
            if is_pace else alt.Axis())
    return (
        alt.Chart(frame)
        .mark_bar(color=COLOUR_ALT if is_pace else COLOUR,
                  cornerRadiusTopRight=3, cornerRadiusBottomRight=3)
        .encode(
            x=alt.X(f"{value}:Q", title=title, axis=axis,
                    scale=alt.Scale(zero=not is_pace, nice=True)),
            y=alt.Y("label:N", title=None, sort="-x"),
            tooltip=[alt.Tooltip("label:N", title="Type"),
                     alt.Tooltip("runs:Q", title="Runs"),
                     alt.Tooltip("distance_km:Q", title="Distance (km)",
                                 format=".1f"),
                     alt.Tooltip("Pace:N", title="Pace")],
        )
        .properties(height=height)
    )


def ladder_bars(frame: pd.DataFrame, height: int = 300):
    """Best against average at each breakdown distance, as a pace.

    Compared as paces rather than times because the distances differ by a
    factor of fifty: a bar chart of 1:12 next to 1:43:02 shows one bar.
    """
    if frame.empty:
        return None
    melted = frame.melt(
        id_vars=["breakdown", "ordinal", "runs"],
        value_vars=["best_pace_s", "avg_pace_s"],
        var_name="which", value_name="seconds_per_km",
    )
    melted["which"] = melted["which"].map(
        {"best_pace_s": "Best", "avg_pace_s": "Average"})
    melted["Pace"] = melted["seconds_per_km"].map(runs.fmt_pace)
    return (
        alt.Chart(melted)
        .mark_bar()
        .encode(
            x=alt.X("breakdown:N", title=None,
                    sort=alt.EncodingSortField(field="ordinal")),
            xOffset=alt.XOffset("which:N"),
            y=alt.Y("seconds_per_km:Q", title="Pace (min/km)",
                    scale=alt.Scale(zero=False, nice=True, reverse=True),
                    axis=alt.Axis(labelExpr="floor(round(datum.value) / 60) "
                                            "+ ':' + format(round(datum.value) "
                                            "% 60, '02d')")),
            color=alt.Color("which:N", title=None,
                            scale=alt.Scale(domain=["Best", "Average"],
                                            range=[COLOUR, COLOUR_ALT]),
                            legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("breakdown:N", title="Distance"),
                     alt.Tooltip("which:N", title=""),
                     alt.Tooltip("Pace:N", title="Pace"),
                     alt.Tooltip("runs:Q", title="Runs reaching it")],
        )
        .properties(height=height)
    )
