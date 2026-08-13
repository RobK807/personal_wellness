"""The diet tracker - a placeholder.

Nothing is built yet and nothing is stored; see the note at the top of
web/blueprints/workouts.py, which applies here word for word.

The questions this section has to answer before it has a schema are mostly
about how much precision is worth the typing. A tracker that wants every
ingredient weighed is abandoned in a fortnight; one that only records "a good
day" cannot be analysed. Where between those two it sits is the design.
"""
from __future__ import annotations

from flask import Blueprint, render_template

from web.app import login_required

bp = Blueprint("diet", __name__)


@bp.route("/")
@login_required
def log():
    return render_template(
        "placeholder.html",
        heading="Diet log",
        blurb="What was eaten and when - however much detail turns out to be "
              "worth the typing.",
        decisions=[
            "Whether the unit is a meal, a food or a day's total, which is the "
            "difference between a food database and six numbers a day.",
            "Whether calories and macros are entered or looked up, and if "
            "looked up, from what - every source worth having is an API call "
            "away, and the NAS is offline behind Tailscale.",
            "Whether recurring meals should be saved and re-used, which is "
            "what makes the difference between a fortnight's use and a year's.",
        ],
    )


@bp.route("/analysis")
@login_required
def analysis():
    return render_template(
        "placeholder.html",
        heading="Diet analysis",
        blurb="Intake against the weigh-ins and the training - the reason for "
              "keeping all four of these in one dashboard rather than four.",
        decisions=[
            "What intake is compared against: the weigh-in tracker's weight "
            "trend, the run tracker's volume, or an estimate of expenditure "
            "built from both.",
            "Over what window, given that a day's intake and a day's weight "
            "have almost no relationship and a fortnight's have a clear one.",
            "Whether RM Kcal from the scale is trustworthy enough to use as "
            "the baseline, or whether it is only worth charting.",
        ],
    )
