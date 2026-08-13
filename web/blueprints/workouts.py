"""The workout plan and tracker - a placeholder.

Nothing is built yet and nothing is stored: there are no workout tables in
core/schema.sql, deliberately. Guessing at a schema for a tracker that has not
been designed is expensive to undo once there is data in it, and the run
tracker next door shows what "designed" looks like - a ladder of best efforts
is a very particular shape, and it came from knowing what the sheet held.

The two pages are the two halves the section is meant to have: the plan (what
is meant to happen) and the tracker (what did). They render the same template,
which says what each is for and lists what has to be decided first.

Building it out means following the run tracker's shape: tables and views in
core/schema.sql, a domain module for parsing and formatting, a queries module
returning plain dicts, a mutations module for writes, then a blueprint here and
a views subpackage on the Streamlit side.
"""
from __future__ import annotations

from flask import Blueprint, render_template

from web.app import login_required

bp = Blueprint("workouts", __name__)


@bp.route("/")
@login_required
def plan():
    return render_template(
        "placeholder.html",
        heading="Workout plan",
        blurb="The sessions you intend to do: a week or a block at a time, "
              "each with its exercises, sets, reps and target load.",
        decisions=[
            "Whether a plan is a repeating weekly template or a dated block "
            "with a start and an end.",
            "Whether exercises come from a fixed list or are typed freely, and "
            "what happens to the history when one is renamed.",
            "How a session that is moved rather than missed should be recorded, "
            "since the run tracker's answer - it simply did not happen - is not "
            "the right one here.",
        ],
    )


@bp.route("/tracker")
@login_required
def tracker():
    return render_template(
        "placeholder.html",
        heading="Workout tracker",
        blurb="What actually happened: the sets and the loads, against the "
              "plan, with the progression that falls out of comparing them.",
        decisions=[
            "What a completed set records - reps and weight, or reps, weight "
            "and how hard it felt.",
            "Whether volume is compared exercise by exercise or by muscle "
            "group, which decides whether exercises need categorising.",
            "Whether the weigh-in tracker's body composition should appear "
            "alongside it, since that is the pair of numbers the section "
            "exists to connect.",
        ],
    )
