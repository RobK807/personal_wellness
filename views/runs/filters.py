"""The filter row shared by the run pages.

Kept in one place for the same reason the Flask side has _filters.html: three
independent choices drawn four times over is three chances for them to drift
apart in what they offer or what they mean.
"""
from __future__ import annotations

import streamlit as st

from views import run_frames as frames

RANGE_LABELS = {"90d": "Last 90 days", "6m": "Last 6 months",
                "1y": "Last year", "2y": "Last 2 years", "All": "Everything"}


def draw(key: str, default_span: str | None = None) -> dict:
    """Draw the three selectors and return what core.run_queries wants.

    `key` namespaces the widgets, because Streamlit keys are global and two
    pages drawing the same filter would otherwise share - and fight over - one
    piece of state.
    """
    spans = [label for label, _ in frames.RANGES]
    default = default_span or frames.DEFAULT_RANGE

    left, middle, right = st.columns(3)
    span = left.selectbox(
        "Since", spans, index=spans.index(default),
        format_func=lambda label: RANGE_LABELS.get(label, label),
        key=f"{key}_span")
    run_type = middle.selectbox(
        "Run type", ["Every run type", *frames.distinct("run_type")],
        key=f"{key}_run_type")
    effort_type = right.selectbox(
        "Effort type", ["Every effort type", *frames.distinct("effort_type")],
        key=f"{key}_effort_type")

    return {
        "span": span,
        "run_type": None if run_type.startswith("Every") else run_type,
        "effort_type": None if effort_type.startswith("Every") else effort_type,
        "start": frames.range_start(span),
    }


def as_args(chosen: dict) -> dict:
    """The chosen filters as keyword arguments for core.run_queries."""
    return {"run_type": chosen["run_type"],
            "effort_type": chosen["effort_type"],
            "start": chosen["start"]}


def describe(chosen: dict) -> str:
    """The filters in a sentence, for a caption under the numbers."""
    parts = [RANGE_LABELS.get(chosen["span"], chosen["span"]).lower()]
    if chosen["run_type"]:
        parts.append(f"{chosen['run_type'].lower()} runs only")
    if chosen["effort_type"]:
        parts.append(f"{chosen['effort_type'].lower()} effort only")
    return "Showing " + ", ".join(parts) + "."
