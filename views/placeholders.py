"""Placeholder pages for the trackers that are not built yet.

The Streamlit twin of web/templates/placeholder.html, and the wording is the
same on purpose: two front-ends telling a different story about what exists is
worse than either story.

`page()` returns a render function, so app.py can list these beside the real
pages without a module each.
"""
from __future__ import annotations

import streamlit as st

SCAFFOLD = """\
core/schema.sql              tables and views
core/<section>.py            parsing, validation, formatting
core/<section>_queries.py    reads, returning plain dicts
core/<section>_mutations.py  writes
web/blueprints/<section>.py  the Flask pages
views/<section>/             the Streamlit pages
"""


def page(heading: str, blurb: str, decisions: list):
    """Build a render function for one placeholder page."""

    def render() -> None:
        st.title(heading)
        st.markdown(blurb)

        st.subheader("Not built yet")
        st.markdown(
            "There are no tables for this behind the page — deliberately. "
            "Guessing at a schema for a tracker that has not been designed is "
            "expensive to undo once there is data in it, and the run tracker "
            "next door shows what designed looks like: a ladder of best "
            "efforts is a very particular shape, and it came from knowing what "
            "the sheet held."
        )
        st.markdown("**What has to be decided first:**")
        for index, line in enumerate(decisions, start=1):
            st.markdown(f"{index}. {line}")

        st.subheader("How it gets built")
        st.caption("The same shape as the run tracker, in this order:")
        st.code(SCAFFOLD, language="text")
        st.caption("The section already exists in `config.SECTIONS`, which is "
                   "where both front-ends read their navigation from — so the "
                   "sidebar entry, this page and the Flask equivalent all come "
                   "from one line.")

    return render


# --------------------------------------------------------------------------- #
# The four pages
# --------------------------------------------------------------------------- #
workout_plan = page(
    "Workout plan",
    "The sessions you intend to do: a week or a block at a time, each with its "
    "exercises, sets, reps and target load.",
    [
        "Whether a plan is a repeating weekly template or a dated block with a "
        "start and an end.",
        "Whether exercises come from a fixed list or are typed freely, and what "
        "happens to the history when one is renamed.",
        "How a session that is moved rather than missed should be recorded, "
        "since the run tracker's answer — it simply did not happen — is not the "
        "right one here.",
    ],
)

workout_tracker = page(
    "Workout tracker",
    "What actually happened: the sets and the loads, against the plan, with the "
    "progression that falls out of comparing them.",
    [
        "What a completed set records — reps and weight, or reps, weight and "
        "how hard it felt.",
        "Whether volume is compared exercise by exercise or by muscle group, "
        "which decides whether exercises need categorising.",
        "Whether the weigh-in tracker's body composition should appear "
        "alongside it, since that is the pair of numbers the section exists to "
        "connect.",
    ],
)

diet_log = page(
    "Diet log",
    "What was eaten and when — however much detail turns out to be worth the "
    "typing. A tracker that wants every ingredient weighed is abandoned in a "
    "fortnight; one that only records *a good day* cannot be analysed.",
    [
        "Whether the unit is a meal, a food or a day's total, which is the "
        "difference between a food database and six numbers a day.",
        "Whether calories and macros are entered or looked up, and if looked "
        "up, from what — every source worth having is an API call away, and the "
        "NAS is offline behind Tailscale.",
        "Whether recurring meals should be saved and re-used, which is what "
        "makes the difference between a fortnight's use and a year's.",
    ],
)

diet_analysis = page(
    "Diet analysis",
    "Intake against the weigh-ins and the training — the reason for keeping all "
    "four of these in one dashboard rather than four.",
    [
        "What intake is compared against: the weigh-in tracker's weight trend, "
        "the run tracker's volume, or an estimate of expenditure built from "
        "both.",
        "Over what window, given that a day's intake and a day's weight have "
        "almost no relationship and a fortnight's have a clear one.",
        "Whether RM Kcal from the scale is trustworthy enough to use as the "
        "baseline, or whether it is only worth charting.",
    ],
)
