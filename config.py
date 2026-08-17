"""Runtime configuration for the personal wellness dashboard.

Every path can be overridden with an environment variable so the same code runs
unchanged on this PC (development) and on the NAS (production).

The variables are prefixed PW_ rather than WI_ or CD_ so this dashboard, the
weigh-in tracker it grew out of and the CD one can share a NAS, a venv and a
shell without treading on each other. The weigh-in tracker's WI_ names are
still read as a fallback, so an existing NAS deployment keeps working while it
is being migrated - see `_setting()`.

The file is organised in four parts:

    paths and ports          shared by every section
    the sections             what the sidebar offers
    weigh-in tracker         the metrics, unchanged from the standalone app
    run tracker              the breakdown ladder, run types and effort types
"""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent


def _setting(name: str, default: str | None = None) -> str | None:
    """Read PW_<name>, falling back to the weigh-in tracker's WI_<name>.

    The fallback exists for one reason: the NAS deployment of the weigh-in
    tracker sets WI_DB_PATH and WI_APP_PASSWORD in run.sh, and a half-migrated
    machine silently pointing at the wrong database - or dropping the password
    gate - is worse than either outcome on its own.
    """
    return os.environ.get(f"PW_{name}", os.environ.get(f"WI_{name}", default))


# --------------------------------------------------------------------------- #
# Paths and ports
# --------------------------------------------------------------------------- #
# Where the SQLite database lives. On the NAS this should be a path on local
# NAS storage, NOT a mapped drive letter - SQLite locking over SMB is unsafe.
#
# One database for every section. The weigh-in tables are exactly the ones the
# standalone tracker created, so `data/weigh_ins.db` can be copied to
# `data/wellness.db` and the run tables appear alongside on first start.
DB_PATH = Path(_setting("DB_PATH", str(APP_DIR / "data" / "wellness.db")))

# The original weigh-in workbook. Only read by the importer and by
# reconcile_test.py, and only ever read-only.
#
# It lives in excel_versions/ with the other source workbooks rather than at the
# top of the project. That folder is git-ignored and never pushed to the NAS -
# nothing the dashboard serves reads it, because the readings are in the
# database by then.
EXCEL_DIR = Path(_setting("EXCEL_DIR", str(APP_DIR / "excel_versions")))

SOURCE_XLSX = Path(_setting("SOURCE_XLSX",
                            str(EXCEL_DIR / "Weigh-in Tracker.xlsx")))

# The scraped Strava workbook behind the run tracker. Same deal: importer only.
RUNS_XLSX = Path(
    _setting("RUNS_XLSX", str(APP_DIR / "strava_webscrape" / "strava_runs.xlsx"))
)

# The tab in it that holds the cleaned data. The other two are working sheets.
RUNS_SHEET = _setting("RUNS_SHEET", "Final_data")

# Where on-demand Excel snapshots are written.
EXPORT_DIR = Path(_setting("EXPORT_DIR", str(APP_DIR / "data" / "exports")))

# Shared password gate. Set PW_APP_PASSWORD in the environment (or leave unset
# during local development to disable the gate entirely).
APP_PASSWORD = _setting("APP_PASSWORD")

# Flask session signing key. Generated once and kept next to the database, so
# sessions survive a restart without the key ever being committed to the repo.
SECRET_KEY_PATH = Path(
    _setting("SECRET_KEY_PATH", str(DB_PATH.parent / "secret.key"))
)

# Port the Flask front-end listens on. 8501 is the CD dashboard and 8502 the
# standalone weigh-in tracker, so this - which replaces the latter - gets 8503
# and the two can be run side by side while the switch is made.
WEB_PORT = int(_setting("WEB_PORT", "8503"))

# The longest run of missed weigh-in days that will be back-filled by
# interpolation. Four missed days on a straight line between the readings
# either side is a reasonable estimate; four missed months is not, and silently
# inventing 120 rows of invented data would quietly poison every average and
# chart built on them. Gaps longer than this are left as gaps and noted in the
# audit log.
MAX_BACKFILL_DAYS = int(_setting("MAX_BACKFILL_DAYS", "60"))


def secret_key() -> bytes:
    """Read the session key, creating it on first use."""
    import secrets

    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_bytes()
    SECRET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    SECRET_KEY_PATH.write_bytes(key)
    try:  # best effort - Windows ignores POSIX modes
        SECRET_KEY_PATH.chmod(0o600)
    except OSError:
        pass
    return key


# --------------------------------------------------------------------------- #
# The sections
# --------------------------------------------------------------------------- #
# What the sidebar offers, in order. `slug` is the URL prefix in Flask and the
# page-URL prefix in Streamlit; `key` names the blueprint and the views
# subpackage. Keeping the list here rather than in either front-end is what
# stops the two disagreeing about what the app contains.
#
#   key         label            icon  slug        emoji for Streamlit
SECTIONS = [
    ("weigh_in", "Weigh-in tracker", "⚖️", "weigh-in"),
    ("runs",     "Run tracker",      "🏃", "runs"),
    ("workouts", "Workout plan",     "🏋️", "workouts"),
    ("diet",     "Diet tracker",     "🥗", "diet"),
]

SECTION_KEYS = [key for key, *_ in SECTIONS]
SECTION_LABELS = {key: label for key, label, *_ in SECTIONS}
SECTION_ICONS = {key: icon for key, _, icon, _ in SECTIONS}
SECTION_SLUGS = {key: slug for key, _, _, slug in SECTIONS}

# The section shown at "/". The weigh-in tracker is the one with six years of
# data behind it, so it keeps the front door.
DEFAULT_SECTION = "weigh_in"

APP_TITLE = "Personal Wellness"
APP_ICON = "🌱"


# --------------------------------------------------------------------------- #
# Weigh-in tracker: the metrics themselves
# --------------------------------------------------------------------------- #
# key, label, unit, decimal places accepted on input, and whether "up is good".
#
# `dp` is the precision the scale actually reports, and it is what the input
# form enforces and what back-filled values are rounded to. `better` drives
# nothing but the colour of a change arrow: weight going down is good, skeletal
# muscle going up is good, and BMI/body fat/visceral fat follow weight.
METRICS = [
    # key,                   label,                 unit,     dp, better
    ("weight",               "Weight",              "kg",      1, "down"),
    ("bmi",                  "BMI",                 "",        1, "down"),
    ("body_fat_pct",         "Body fat %",          "%",       1, "down"),
    ("skeletal_muscle_pct",  "Skeletal muscle %",   "%",       1, "up"),
    ("rm_kcal",              "RM Kcal",             "kcal",    0, "up"),
    ("visceral_fat",         "Visceral fat",        "",        0, "down"),
]

# Derived from the above rather than entered: mass in kg implied by the
# percentages. The workbook did this in Data!T and Data!U.
DERIVED = [
    ("body_fat_kg",          "Body fat mass",       "kg",      2, "down"),
    ("muscle_kg",            "Skeletal muscle mass", "kg",     2, "up"),
]

ALL_METRICS = METRICS + DERIVED

# Convenience lookups, built once.
METRIC_KEYS = [key for key, *_ in METRICS]
ALL_KEYS = [key for key, *_ in ALL_METRICS]
LABELS = {key: label for key, label, *_ in ALL_METRICS}
UNITS = {key: unit for key, _, unit, *_ in ALL_METRICS}
DP = {key: dp for key, _, _, dp, _ in ALL_METRICS}
BETTER = {key: better for key, _, _, _, better in ALL_METRICS}

# The paired charts from the workbook's Charts sheet: two metrics on one plot,
# each with its own y-axis, because the scales have nothing in common.
CHART_PAIRS = [
    ("weight", "bmi"),
    ("body_fat_pct", "skeletal_muscle_pct"),
    ("rm_kcal", "visceral_fat"),
    ("weight", "body_fat_pct"),
]

# Two weigh-ins a day, averaged. The workbook's Data sheet had exactly these.
SLOTS = (1, 2)


# --------------------------------------------------------------------------- #
# Run tracker
# --------------------------------------------------------------------------- #
# Strava's best-effort ladder, in the order the workbook's Breakdown column
# uses it, with the distance each one actually covers. Those distances are what
# turn a breakdown time into a pace, and they are exact rather than rounded:
# a mile is 1.609344 km by definition, and the half-marathon is 21.0975 km.
#
# The list is the whole vocabulary of the Breakdown column. A run only carries
# the rungs it was long enough to reach, which is why 400m appears against
# every run and the half-marathon against eleven of them.
BREAKDOWNS = [
    # label,           km
    ("400m",           0.4),
    ("1/2 mile",       0.804672),
    ("1K",             1.0),
    ("1 mile",         1.609344),
    ("2 mile",         3.218688),
    ("5K",             5.0),
    ("10K",            10.0),
    ("15K",            15.0),
    ("10 mile",        16.09344),
    ("20K",            20.0),
    ("Half-Marathon",  21.0975),
]

BREAKDOWN_LABELS = [label for label, _ in BREAKDOWNS]
BREAKDOWN_KM = {label: km for label, km in BREAKDOWNS}
# Position in the ladder, for sorting a set of breakdowns back into order.
BREAKDOWN_ORDER = {label: index for index, (label, _) in enumerate(BREAKDOWNS)}

# The two ways a run is classified, as the workbook classifies them.
#
# These are **seeds, not the live lists**. What the dropdowns offer lives in the
# `run_options` table and is edited on the Admin page, because a new kind of
# session should be something you add in the app rather than something that
# needs a code edit, a push to the NAS and a restart. core/db.py
# _seed_run_options fills that table from these lists exactly once - on a
# database that has none - together with anything the imported runs already use,
# which is how 'Unclassified' gets there. Editing these afterwards changes
# nothing; editing them and expecting it to is the mistake this note exists to
# prevent. See core/run_options.py.
RUN_TYPES = ["Standard", "Race", "Weighted", "Pace", "Sprints", "Intervals"]
EFFORT_TYPES = ["Base", "Threshold", "Tempo", "VO2 max", "Race",
                "Warm-up / warm down"]

# How many rows the records page shows per distance. The brief said five.
TOP_N = 5

# --------------------------------------------------------------------------- #
# Interval sessions
# --------------------------------------------------------------------------- #
# A structured session is "N reps of <a length>, at <a pace>", and how it was
# set decides what kind of length that is:
#
#   distance   8 x 1k @ 3:50/km     the rep is a distance, and has no set time
#   time       10 x 0:10 @ 3:00/km  the rep is a duration, and covers no set
#                                   distance
#
# So there are three entered fields and each is entered, never worked out:
#
#   interval_distance_m   how far each rep was     set by distance only
#   interval_time_s       how long each rep was    set by time only
#   interval_pace_s       the average pace held    both
#
# The pace is the only figure that means something for both kinds, and it has
# to be typed rather than divided out. Ten-second sprints are the case that
# settles it: the time is 0:10 and the pace is nothing like 0:10, and there is
# no distance recorded to get from one to the other. Storing it breaks the rule
# the rest of the tracker follows, and does so safely - nothing else holds the
# same fact, so there is no second copy for it to drift from.
INTERVAL_TYPES = ["distance", "time"]

INTERVAL_TYPE_LABELS = {
    "distance": "Distance (e.g. 8 x 1k)",
    "time": "Time (e.g. 10 x 0:10)",
}

# Which length field belongs to which type. One list, read by the parser and
# by both input forms, so they cannot disagree about which box applies.
INTERVAL_LENGTH_FIELD = {
    "distance": "interval_distance_m",
    "time": "interval_time_s",
}

# The wording the input forms use, kept here so Flask and Streamlit cannot
# label the same box two different ways.
INTERVAL_FIELD_HELP = {
    "interval_distance_m": "How far each rep was — 400m, 1k, 1.6km. Only for a "
                           "session set by distance; leave it blank for one set "
                           "by time.",
    "interval_time_s": "How long each rep was — 3:00 for three minutes, 0:10 "
                       "for ten seconds. Only for a session set by time; leave "
                       "it blank for one set by distance.",
    "interval_pace_s": "The average pace held across the reps, in minutes per "
                       "kilometre. Filled in either way, and not the same thing "
                       "as the time per rep: ten-second sprints are 0:10 each "
                       "at something like 3:00/km.",
}

# Fat-finger guards, in the same spirit as RUN_BOUNDS. Wide enough that a real
# session never trips one.
#
# The pace bound is the one that earns its keep, because the pace box is the
# easiest to fill in with the wrong thing: 0:10 typed there for a ten-second
# sprint is ten seconds per kilometre, and 1:00 is a world record twice over.
# 1:30/km is about 11 m/s, quicker than anyone has ever run 100 m.
INTERVAL_BOUNDS = {
    "interval_count": (1, 200),
    "interval_distance_m": (20.0, 100_000.0),
    "interval_time_s": (3, 7200),
    "interval_pace_s": (90, 1200),
}

# Fat-finger guards for the run input form, in the same spirit as the weigh-in
# bounds: wide enough that a real run never trips one.
RUN_BOUNDS = {
    "distance_km": (0.1, 300.0),
    "duration_s":  (30, 48 * 3600),
}
