"""Runtime configuration for the personal wellness dashboard.

Every path can be overridden with an environment variable so the same code runs
unchanged on this PC (development) and on the NAS (production).

The variables are prefixed PW_ rather than WI_ or CD_ so this dashboard, the
weigh-in tracker it grew out of and the CD one can share a NAS, a venv and a
shell without treading on each other. The weigh-in tracker's WI_ names are
still read as a fallback, so an existing NAS deployment keeps working while it
is being migrated - see `_setting()`.

The file is organised in five parts:

    paths and ports          shared by every section
    the sections             what the sidebar offers
    weigh-in tracker         the metrics, unchanged from the standalone app
    run tracker              the breakdown ladder, run types and effort types
    workout tracker          set types, load modes and the exercise seed

Two of those lists are seeds rather than live settings, and say so where they
are defined: the run and effort types (now in `run_options`) and the exercise
catalogue (now in `exercises`). Both are edited in the app.
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


# --------------------------------------------------------------------------- #
# Workout plan and tracker
# --------------------------------------------------------------------------- #
# The gym workbook the section was built from. Importer only, and read-only, the
# same as the other two. It sits in EXCEL_DIR with them.
GYM_XLSX = Path(_setting("GYM_XLSX", str(EXCEL_DIR / "2026 Gym Programme.xlsx")))

# A session's exercises, and a week's sessions. Ten each is the brief; the
# workbook uses four and two.
MAX_EXERCISES_PER_SESSION = 10
MAX_SESSIONS_PER_WEEK = 10

# Warm-up sets get their own cap because they are prescribed per set - weight and
# reps for each - rather than as "n sets of this". Three is the brief and the
# workbook uses two, or one in the deload week.
MAX_WARMUP_SETS = 3

# Phase 3 climbs 87/92/97% across its three week-pairs, which is what the
# workbook's Weight 1/2/3 columns hold. It bounds a phase's percentage list.
MAX_WORKING_WEIGHTS = 3

# The three kinds of line on a week sheet. `accessory` is not "a working set on
# a smaller lift" - it is prescribed differently, in sets and a rep range with
# the weight left to the day, which is why it counts as its own type.
SET_TYPES = ["warmup", "working", "accessory"]

SET_TYPE_LABELS = {
    "warmup": "Warm-up",
    "working": "Working",
    "accessory": "Accessory",
}

# What the sheet's Set # column reads: W1, W2 for warm-ups, then 1, 2, 3.
SET_TYPE_PREFIX = {"warmup": "W", "working": "", "accessory": ""}

# The four ways a weight is prescribed. See core/workouts.py parse_load() and
# the note on exercise_sets in core/schema.sql for which column each one uses.
LOAD_MODES = ["explicit", "percent", "bodyweight", "choose"]

LOAD_MODE_LABELS = {
    "explicit": "Weight in kg",
    "percent": "% of 1RM",
    "bodyweight": "Bodyweight (+ added)",
    "choose": "Choose on the day",
}

# Whether a rep count is per side or altogether, and whether a weight is per
# dumbbell or altogether. Properties of the movement - a Bulgarian split squat is
# per leg wherever it appears - so they live on the catalogue entry, with a
# per-session override for the times it is done the other way.
REPS_MODES = ["total", "per_side"]
WEIGHT_MODES = ["total", "per_dumbbell"]

REPS_MODE_LABELS = {
    "total": "Total reps",
    "per_side": "Per side (each leg / arm)",
}
WEIGHT_MODE_LABELS = {
    "total": "Total weight",
    "per_dumbbell": "Per dumbbell",
}

# Rest, defaulted by set type so it is not typed 47 times a week. Free text
# rather than seconds: the workbook says "2-3 min" and a range is the honest
# answer for how long to sit down for.
DEFAULT_REST = {
    "warmup": "60s",
    "working": "2-3 min",
    "accessory": "60-90s",
}

# The offered rest values, in the order the workbook uses them. Free text on the
# way in, so this is a list of suggestions rather than a closed set.
REST_OPTIONS = ["60s", "60-90s", "2-3 min", "3-4 min"]

# The per-set cues from the workbook. Also suggestions, for the same reason.
CUE_OPTIONS = [
    "Log RPE",
    "Log reps completed",
    "Control tempo 3-1-1",
    "Crisp technique, no grinding",
    "Light - just moving",
]

# What a plan's weights round to. 2.5 kg is the workbook's step and reproduces
# every one of its numbers exactly; the rest are here because a gym with 1.25 kg
# plates or a dumbbell rack in 5s is a real gym.
DEFAULT_ROUNDING_KG = 2.5
ROUNDING_STEPS = [1.0, 1.25, 2.5, 5.0]

# The exercise catalogue as first seeded, taken from the gym workbook: the name,
# whether reps are per side, whether the weight is per dumbbell, and whether it
# has a bar to load at all. Only the seed - the catalogue lives in the
# `exercises` table and is edited in the app, exactly as run_options is.
#
# The per-side and per-dumbbell flags are read off the movement rather than the
# sheet, which records them only in passing ("10 each leg") and not at all for
# the weight. They are the reason the flags are asked for: the sheet cannot say
# whether "27.5" on an incline press means two 27.5s or one.
#
#   name,                                per_side, per_dumbbell, bodyweight
WORKOUT_EXERCISES = [
    # The four lifts with a 1RM.
    ("Bench Press",                          False, False, False),
    ("Squats",                               False, False, False),
    ("Deadlift",                             False, False, False),
    ("OHP",                                  False, False, False),
    # Main lifts with no 1RM - these progress by added weight.
    ("Pull-Ups",                             False, False, True),
    ("Tricep Dips",                          False, False, True),
    # Accessories.
    ("Bulgarian Split Squat (Dumbbells)",    True,  True,  False),
    ("Cable Chest Fly",                      False, False, False),
    ("Cable Pull-Through",                   False, False, False),
    ("Dumbbell Bicep Curl",                  False, True,  False),
    ("Dumbbell Lateral Raise",               False, True,  False),
    ("EZ Bar Curl",                          False, False, False),
    ("Face Pull (Cable)",                    False, False, False),
    ("Glute Bridge (Barbell)",               False, False, False),
    ("Goblet Squat",                         False, False, False),
    ("Good Morning (Barbell)",               False, False, False),
    ("Hamstring Curl (Machine)",             False, False, False),
    ("Incline Dumbbell Press",               False, True,  False),
    ("Lat Pulldown (Machine)",               False, False, False),
    ("Leg Press (Machine)",                  False, False, False),
    ("Nordic Hamstring Curl",                False, False, True),
    ("Romanian Deadlift (Barbell)",          False, False, False),
    ("Seated Cable Row",                     False, False, False),
    ("Walking Lunge (Dumbbells)",            True,  True,  False),
]

# Which of them the workbook gives a 1RM for, and the numbers it gives. Seeded
# onto an imported plan; a plan built by hand asks for them.
WORKOUT_ONE_RM_SEED = {
    "Bench Press": 95.0,
    "Squats": 135.0,
    "Deadlift": 225.0,
    "OHP": 55.0,
}

MAX_NAME_LENGTH = 60

# Fat-finger guards, in the same spirit as RUN_BOUNDS. Wide enough that a real
# session never trips one: 2 kg is a light dumbbell, 400 kg is a deadlift nobody
# in this database is doing, and 250% of 1RM is a typo rather than a plan.
WORKOUT_BOUNDS = {
    "reps": (1, 200),
    "weight_kg": (0.5, 400.0),
    "percent_1rm": (0.05, 1.5),
    "rounding_kg": (0.25, 10.0),
    "one_rm_kg": (1.0, 500.0),
}


# --------------------------------------------------------------------------- #
# Food planner and diary
# --------------------------------------------------------------------------- #
# The workbook the section was built from. Importer only, and read-only.
FOOD_XLSX = Path(_setting("FOOD_XLSX", str(EXCEL_DIR / "Food Planner v0.1.xlsx")))

# The four macros, and nothing else. The workbook carried sodium and sugar
# columns; they hold no data and are deliberately not imported.
#
#   key, label, unit, decimal places shown
MACROS = [
    ("calories", "Calories", "kcal", 0),
    ("carbs",    "Carbs",    "g",    1),
    ("fat",      "Fat",      "g",    1),
    ("protein",  "Protein",  "g",    1),
]

MACRO_KEYS = [key for key, *_ in MACROS]
MACRO_LABELS = {key: label for key, label, *_ in MACROS}
MACRO_UNITS = {key: unit for key, _, unit, _ in MACROS}
MACRO_DP = {key: dp for key, _, _, dp in MACROS}

# The workbook's three lookup lists. Not categories of food - a way of finding
# one: an Item is a single thing, a Meal is bought or assembled, a Recipe is
# cooked. `grouping` is the sub-heading inside a list and is a different idea.
FOOD_LISTS = ["Items", "Meals", "Recipes"]

# The groupings each list actually uses, taken from the workbook. Offered by the
# input form; the column is free text, so a new one needs no migration.
FOOD_GROUPINGS = {
    "Items":   ["Meal component", "Snack", "Dessert", "Protein", "Drink"],
    "Meals":   ["Breakfast", "Lunch", "Dinner"],
    "Recipes": ["Breakfast", "Lunch", "Dinner"],
}

# The meals a day is divided into, in the order they are eaten.
MEALS = ["Breakfast", "Lunch", "Dinner", "Snacks"]

# Catalogue rows the workbook's Food sheet does not have, but the corrected diary
# refers to. Loaded alongside the workbook by `--catalogue`, so that a rebuilt
# database still resolves every line of the diary.
#
# Two are ordinary foods that were simply never added to the sheet. The other two
# are placeholders: "Dinner" covers 261 lines and "Lunch" eight, mostly meals
# eaten out and estimated, and the macros here are the standing estimate rather
# than a claim about any one of them. Linking to a placeholder does not restate a
# diary line - a food_entries row carries its own macros, and the link only says
# what kind of thing it was - so the 160 different dinners behind that label keep
# their own figures.
#
#   list, name, grouping, portion, units, calories, carbs, fat, protein, note
FOOD_EXTRA_FOODS = [
    # "Protein", to sit with the four other Grenade bars already on the sheet
    # rather than beside the fruit pastilles.
    ("Items", "Grenade bar - Caramel Chaos", "Protein", 1.0, "Bar",
     203.0, 21.0, 6.9, 21.0, "Eaten 167 times; never on the Food sheet"),
    ("Items", "Jasmine rice", "Meal component", 50.0, "grams",
     174.5, 38.8, 0.3, 3.95, "Eaten twice; never on the Food sheet"),
    ("Meals", "Dinner", "Dinner", 1.0, "Portion",
     830.0, 90.0, 30.0, 50.0, "Placeholder for a dinner out - a standing estimate"),
    ("Meals", "Lunch", "Lunch", 1.0, "Portion",
     665.0, 80.0, 25.0, 30.0, "Placeholder for a lunch out - a standing estimate"),
]

# The units the catalogue uses, commonest first. Free text on the way in - this
# is a list of suggestions, not a closed set.
FOOD_UNITS = ["Portion", "grams", "ml", "Bar", "Pot", "Slice", "Piece", "item",
              "Pint", "mug", "egg", "biscuit", "roll", "sausage", "stick",
              "sweet", "bunny", "Pizza", "Drink"]

# Which day a planning week starts on: 0 is Monday, 6 is Sunday. The workbook
# ran Monday to Sunday and the diary's "W/C" headers are all Mondays, so that is
# the default - but the whole point of parameterising it is that a week is a
# habit rather than a fact.
WEEK_STARTS_ON = int(_setting("WEEK_STARTS_ON", "0"))

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday"]

# The target profile a day uses when it does not name one.
DEFAULT_TARGET = "Base"

# The seed for macro_targets, from the workbook's Planner. Only used on a
# database that has none - after that they are edited in the app, and dated, so
# changing them never restates a day that has already happened.
#
#   name, calories, carbs, fat, protein
FOOD_TARGET_SEED = [
    ("Base", 1890.0, 170.0, 50.0, 190.0),
]

# Fat-finger guards, in the same spirit as RUN_BOUNDS. A day of 12,000 calories
# is a typo; so is a single food with 900 g of protein.
FOOD_BOUNDS = {
    "calories": (0.0, 5000.0),
    "carbs":    (0.0, 1000.0),
    "fat":      (0.0, 1000.0),
    "protein":  (0.0, 1000.0),
    "quantity": (0.0001, 10000.0),
    "portion":  (0.0001, 10000.0),
    "scale":    (0.0001, 100.0),
}

# Where the diary export writes to, and reads back from. The history is two
# years of free text and it is corrected by hand in a spreadsheet, so the round
# trip is a file rather than a form - see core/diary_csv.py.
DIARY_CSV = Path(_setting("DIARY_CSV", str(EXPORT_DIR / "food_diary.csv")))
