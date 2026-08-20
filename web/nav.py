"""What the sidebar and the tab strip contain.

Two levels. `config.SECTIONS` is the sidebar - the four trackers - and this is
the strip of pages inside each one. Keeping them apart is what lets a section
be added without touching any other section's templates: a blueprint, a views
subpackage, one line in config.SECTIONS and one entry here.

Each page is (endpoint, short label, full label). The short one is what a phone
shows; the full one appears from 720px up. The first page of a section is its
landing page, and the sidebar links to it.
"""
from __future__ import annotations

import config

PAGES = {
    "weigh_in": [
        ("weigh_in.overview",    "Home",     "Overview"),
        ("weigh_in.input_page",  "Input",    "Input"),
        ("weigh_in.trends",      "Charts",   "Charts"),
        ("weigh_in.changes",     "Change",   "Changes"),
        ("weigh_in.data",        "Data",     "Data"),
        ("weigh_in.admin",       "Admin",    "Admin"),
    ],
    "runs": [
        ("runs.overview",        "Home",     "Overview"),
        ("runs.input_page",      "Log",      "Log a run"),
        ("runs.analysis",        "Analysis", "Analysis"),
        ("runs.records",         "Records",  "Records"),
        ("runs.data",            "Data",     "Data"),
        ("runs.admin",           "Admin",    "Admin"),
    ],
    "workouts": [
        # Session first, so the sidebar's "Workout plan" lands on the workout
        # that is due rather than on the machinery for planning one.
        ("workouts.session",     "Session",  "Session"),
        ("workouts.plan",        "Plan",     "Plan"),
        ("workouts.build",       "Build",    "Build a session"),
        ("workouts.tracker",     "Track",    "Tracker"),
        ("workouts.exercises",   "Moves",    "Exercises"),
    ],
    "diet": [
        ("diet.log",             "Log",      "Log"),
        ("diet.analysis",        "Analysis", "Analysis"),
    ],
}

# Where a section's name in the sidebar points.
LANDING = {key: pages[0][0] for key, pages in PAGES.items()}


def section_of(endpoint: str | None) -> str | None:
    """Which section an endpoint belongs to - its blueprint name, in practice.

    Returns None for the pages that sit outside every section: the login form,
    the health check and static files. Those render without a tab strip rather
    than arbitrarily highlighting the weigh-in tracker.
    """
    if not endpoint or "." not in endpoint:
        return None
    section = endpoint.split(".", 1)[0]
    return section if section in PAGES else None


def sidebar() -> list:
    """(key, label, icon, landing endpoint) for each section, in order."""
    return [(key, label, icon, LANDING[key])
            for key, label, icon, _ in config.SECTIONS if key in PAGES]
