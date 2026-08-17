"""Workout rules: reps, loads, rounding, validation, formatting.

The workout section's answer to core/runs.py, and the only place that knows how
a rep count is written down or how a prescribed weight is worked out.

Loads
-----
Four ways to prescribe one, because the source workbook uses all four:

    explicit     a number of kilograms
    percent      a fraction of the plan's 1RM for that lift, rounded
    bodyweight   no bar to load; optionally plus added weight
    choose       deliberately unprescribed - the accessories say
                 "Choose weight" and mean it

Rounding
--------
`round_to()` is half-up, and that is not a detail. SQLite's ROUND is
half-away-from-zero and Python's round() is half-to-even, so 61.25 kg at a 2.5
step is 62.5 to one and 60.0 to the other. The weight actually put on the bar is
computed once, in v_exercise_sets; this function reproduces that rule so the
input form can preview it, and workout_test.py asserts the two never disagree.

Reps
----
A low and an optional high, so '10' and '10-12' are one column. Whether those
reps are per side is a property of the movement rather than of the number, which
is why it lives on the exercise and not here.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from typing import Iterable, Mapping, Sequence

import config
from core.metrics import as_date  # re-exported: dates work the same everywhere


class InvalidWorkout(ValueError):
    """A workout field that is missing, unparseable or outside its bounds."""


# --------------------------------------------------------------------------- #
# Rounding
# --------------------------------------------------------------------------- #
def round_to(value, step: float) -> float:
    """`value` to the nearest `step`, halves upwards.

    Half-up rather than Python's half-to-even, to match SQLite's ROUND - see the
    module docstring. Done with floor(x + 0.5) rather than round(), because
    round() is exactly the function whose tie-breaking is wrong here.
    """
    if value is None:
        return None
    step = float(step)
    if step <= 0:
        raise InvalidWorkout(f"A rounding step of {step:g} makes no sense")
    return math.floor(float(value) / step + 0.5) * step


def fmt_kg(kg, dp: int = 1) -> str:
    """'87.5', '90' - trailing zeros dropped, because a plate is not 90.0 kg."""
    if kg is None:
        return "-"
    text = f"{float(kg):.{dp}f}".rstrip("0").rstrip(".")
    return text or "0"


# --------------------------------------------------------------------------- #
# Reps
# --------------------------------------------------------------------------- #
def parse_reps(raw, label: str = "Reps") -> tuple:
    """(low, high) from '10', '10-12', '10 to 12' or a bare number.

    Returns (None, None) for blank, which is allowed: an accessory being sketched
    out may not have a rep target yet. `high` is None for a single number, which
    is what makes '10' and '10-12' the same pair of columns.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        low = int(round(float(raw)))
        return _rep_bounds(low, None, label)

    # An en dash is what a spreadsheet produces and a hyphen is what a person
    # types; both mean a range, and so does the word.
    text = str(raw).strip().lower()
    text = text.replace("–", "-").replace("—", "-").replace(" to ", "-")
    # Decimals are matched whole, not as two numbers. A cell openpyxl hands back
    # as 10.0 would otherwise read as the range 10 to 0, which is how this was
    # first written and what the gym import found within the minute.
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if not numbers:
        raise InvalidWorkout(f"'{raw}' is not a {label.lower()} count "
                             f"(try 10, or 10-12 for a range)")
    if len(numbers) > 2:
        raise InvalidWorkout(f"'{raw}' is not a {label.lower()} count - "
                             f"expected one number or two")
    low = int(round(float(numbers[0])))
    high = int(round(float(numbers[1]))) if len(numbers) == 2 else None
    return _rep_bounds(low, high, label)


def _rep_bounds(low, high, label: str) -> tuple:
    floor, ceiling = config.WORKOUT_BOUNDS["reps"]
    for value in (low, high):
        if value is not None and not floor <= value <= ceiling:
            raise InvalidWorkout(
                f"{value} {label.lower()} looks wrong - expected between "
                f"{floor} and {ceiling}")
    if high is not None and high < low:
        raise InvalidWorkout(
            f"A {label.lower()} range of {low}-{high} runs backwards")
    if high == low:
        high = None      # '10-10' is just 10
    return low, high


def fmt_reps(low, high=None, reps_mode: str = "total") -> str:
    """'10', '10-12', '10 each side'."""
    if low is None and high is None:
        return "-"
    text = f"{low}-{high}" if high else f"{low}"
    return f"{text} each side" if reps_mode == "per_side" else text


# --------------------------------------------------------------------------- #
# Loads
# --------------------------------------------------------------------------- #
def parse_percent(raw, label: str = "% of 1RM") -> float:
    """A fraction from '65', '65%' or '0.65'.

    Both spellings are accepted because both are natural: the workbook stores
    0.65 and a person says 65%. Anything above 2 is read as a percentage, which
    makes 65 and 0.65 the same thing and leaves 1.05 meaning a genuine 105%.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise InvalidWorkout(f"{label} is required for a percentage set")
    text = str(raw).strip().rstrip("%").strip()
    try:
        value = float(text)
    except ValueError:
        raise InvalidWorkout(f"'{raw}' is not a {label} "
                             f"(try 65% or 0.65)") from None
    if value > 2:
        value /= 100.0
    low, high = config.WORKOUT_BOUNDS["percent_1rm"]
    if not low <= value <= high:
        raise InvalidWorkout(
            f"{value * 100:g}% of 1RM looks wrong - expected between "
            f"{low * 100:g}% and {high * 100:g}%")
    return round(value, 4)


def parse_kg(raw, label: str = "Weight", allow_zero: bool = False) -> float:
    """Kilograms from '87.5' or '87.5 kg'."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise InvalidWorkout(f"{label} is required")
    text = str(raw).strip().lower().removesuffix("kg").strip()
    try:
        value = float(text)
    except ValueError:
        raise InvalidWorkout(f"'{raw}' is not a weight in kilograms") from None
    low, high = config.WORKOUT_BOUNDS["weight_kg"]
    if value == 0 and allow_zero:
        return 0.0
    if not low <= value <= high:
        raise InvalidWorkout(
            f"A {label.lower()} of {fmt_kg(value)} kg looks wrong - expected "
            f"between {fmt_kg(low)} and {fmt_kg(high)}")
    return round(value, 2)


def parse_load(values: Mapping[str, object]) -> dict:
    """The load half of a set: the mode, and the one column that mode uses.

    Every mode clears the other two columns rather than leaving whatever was
    typed into them, because a set holding both a weight and a percentage has no
    answer to "which of these is it".
    """
    mode = str(values.get("load_mode") or "").strip().lower()
    if mode not in config.LOAD_MODES:
        raise InvalidWorkout(
            f"'{mode}' is not a way of prescribing a weight "
            f"({', '.join(config.LOAD_MODES)})")

    load = {"load_mode": mode, "weight_kg": None, "percent_1rm": None,
            "added_kg": None}
    if mode == "explicit":
        load["weight_kg"] = parse_kg(values.get("weight_kg"), "Weight")
    elif mode == "percent":
        load["percent_1rm"] = parse_percent(values.get("percent_1rm"))
    elif mode == "bodyweight":
        added = values.get("added_kg")
        if added is not None and str(added).strip():
            load["added_kg"] = parse_kg(added, "Added weight", allow_zero=True)
    return load


def prescribed_kg(load: Mapping, one_rm=None, rounding_kg: float = 2.5):
    """What to put on the bar, or None when there is no answer to give.

    None means one of two honest things: the set is a "choose weight" accessory,
    or it is a percentage of a 1RM that has not been entered for this plan yet.
    Both are better than a number that would be made up. The database works this
    out too, in v_exercise_sets, and the two agree by construction - see the
    module docstring on rounding.
    """
    mode = load.get("load_mode")
    if mode == "explicit":
        return load.get("weight_kg")
    if mode == "bodyweight":
        return load.get("added_kg")
    if mode == "percent":
        if one_rm is None or not load.get("percent_1rm"):
            return None
        return round_to(float(load["percent_1rm"]) * float(one_rm), rounding_kg)
    return None


def fmt_load(row: Mapping) -> str:
    """A set's weight the way the week sheet writes it.

    'Bodyweight', '+10 kg', '87.5 kg', '65% - 62.5 kg', 'Choose weight'. The
    percentage keeps its worked-out weight beside it rather than replacing it:
    the percentage is the instruction and the kilograms are the consequence, and
    seeing only the second makes a programme impossible to check.
    """
    mode = row.get("load_mode")
    kg = row.get("prescribed_kg")
    if mode == "choose":
        return "Choose weight"
    if mode == "bodyweight":
        added = row.get("added_kg")
        if not added:
            return "Bodyweight"
        return f"Bodyweight +{fmt_kg(added)} kg"
    if mode == "explicit":
        return f"{fmt_kg(kg)} kg{_per_dumbbell(row)}" if kg is not None else "-"
    if mode == "percent":
        pct = row.get("percent_1rm")
        shown = f"{float(pct) * 100:g}%" if pct else "?"
        if kg is None:
            return f"{shown} of 1RM (no 1RM set)"
        return f"{shown} - {fmt_kg(kg)} kg{_per_dumbbell(row)}"
    return "-"


def _per_dumbbell(row: Mapping) -> str:
    return " each" if row.get("weight_mode") == "per_dumbbell" else ""


# --------------------------------------------------------------------------- #
# Percentage lists on a phase
# --------------------------------------------------------------------------- #
def parse_percent_list(raw, label: str) -> list:
    """A phase's warm-up or working percentages, from '50, 70' or '0.5 0.7'.

    Stored as a JSON array; see the note in core/schema.sql on why a phase keeps
    a list rather than a table of one number per row.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        text = str(raw).strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                items = json.loads(text)
            except ValueError:
                raise InvalidWorkout(f"{label} is not a list of "
                                     f"percentages") from None
        else:
            items = [part for part in re.split(r"[,;/\s]+", text) if part]
    if len(items) > config.MAX_WORKING_WEIGHTS:
        raise InvalidWorkout(
            f"{label} holds {len(items)} percentages - at most "
            f"{config.MAX_WORKING_WEIGHTS} are used")
    return [parse_percent(item, label) for item in items]


def percent_list(raw) -> list:
    """A stored JSON percentage list back as floats. Never raises."""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return [float(item) for item in raw]
    try:
        loaded = json.loads(str(raw))
    except ValueError:
        return []
    return [float(item) for item in loaded] if isinstance(loaded, list) else []


def dump_percent_list(values: Iterable) -> str | None:
    values = list(values or [])
    return json.dumps(values) if values else None


def fmt_percent_list(raw) -> str:
    """'50%, 70%' for a table cell."""
    values = percent_list(raw)
    return ", ".join(f"{value * 100:g}%" for value in values) if values else "-"


# --------------------------------------------------------------------------- #
# Whole records
# --------------------------------------------------------------------------- #
def parse_exercise(values: Mapping[str, object]) -> dict:
    """A catalogue entry: name, and how its reps and weight are counted."""
    name = " ".join(str(values.get("name") or "").split())
    if not name:
        raise InvalidWorkout("An exercise needs a name")
    if len(name) > config.MAX_NAME_LENGTH:
        raise InvalidWorkout(
            f"'{name[:config.MAX_NAME_LENGTH]}...' is too long for an exercise "
            f"name (limit {config.MAX_NAME_LENGTH} characters)")
    return {
        "name": name,
        "reps_mode": _one_of(values.get("reps_mode"), config.REPS_MODES,
                             "Reps counted", "total"),
        "weight_mode": _one_of(values.get("weight_mode"), config.WEIGHT_MODES,
                               "Weight counted", "total"),
        "is_bodyweight": 1 if _truthy(values.get("is_bodyweight")) else 0,
        "note": (str(values.get("note") or "")).strip() or None,
    }


def parse_plan(values: Mapping[str, object]) -> dict:
    """A plan: a name to find it by again, and the step weights round to."""
    name = " ".join(str(values.get("name") or "").split())
    if not name:
        raise InvalidWorkout("A plan needs a name - it is how you find it again")
    if len(name) > config.MAX_NAME_LENGTH:
        raise InvalidWorkout(
            f"That plan name is too long (limit {config.MAX_NAME_LENGTH} "
            f"characters)")

    started = values.get("started_on")
    started_on = as_date(started) if started and str(started).strip() else None

    rounding = values.get("rounding_kg")
    rounding = config.DEFAULT_ROUNDING_KG if rounding in (None, "") \
        else parse_kg(rounding, "Rounding step")
    if rounding not in config.ROUNDING_STEPS:
        # Not refused - a gym with 1.25 kg plates is a real gym - but bounded,
        # because a step of 40 kg would silently flatten a whole programme.
        low, high = config.WORKOUT_BOUNDS["rounding_kg"]
        if not low <= rounding <= high:
            raise InvalidWorkout(
                f"A rounding step of {fmt_kg(rounding)} kg looks wrong - "
                f"expected between {fmt_kg(low)} and {fmt_kg(high)}")

    return {
        "name": name,
        "started_on": started_on,
        "rounding_kg": rounding,
        "note": (str(values.get("note") or "")).strip() or None,
        "archived": 1 if _truthy(values.get("archived")) else 0,
    }


def parse_phase(values: Mapping[str, object]) -> dict:
    """A phase, with the defaults the session builder pre-fills from."""
    name = " ".join(str(values.get("name") or "").split())
    if not name:
        raise InvalidWorkout("A phase needs a name")
    return {
        "name": name,
        "focus": (str(values.get("focus") or "")).strip() or None,
        "warmup_pcts": dump_percent_list(
            parse_percent_list(values.get("warmup_pcts"), "Warm-up %")),
        "working_pcts": dump_percent_list(
            parse_percent_list(values.get("working_pcts"), "Working %")),
        "working_sets": _count(values.get("working_sets"), "Working sets"),
        "working_reps": _reps_text(values.get("working_reps"), "Working reps"),
        "accessory_sets": _count(values.get("accessory_sets"),
                                 "Accessory sets"),
        "accessory_reps": _reps_text(values.get("accessory_reps"),
                                     "Accessory reps"),
        "rest_warmup": (str(values.get("rest_warmup") or "")).strip() or None,
        "rest_working": (str(values.get("rest_working") or "")).strip() or None,
        "rest_accessory": (str(values.get("rest_accessory") or "")).strip()
                          or None,
    }


def parse_set(values: Mapping[str, object], set_type: str) -> dict:
    """One line of a session: type, reps, load, rest, cue."""
    if set_type not in config.SET_TYPES:
        raise InvalidWorkout(f"'{set_type}' is not a kind of set "
                             f"({', '.join(config.SET_TYPES)})")
    low, high = parse_reps(values.get("reps"), "Reps")
    return {
        "set_type": set_type,
        "reps_low": low,
        "reps_high": high,
        **parse_load(values),
        "rest": (str(values.get("rest") or "")).strip() or None,
        "cue": (str(values.get("cue") or "")).strip() or None,
    }


def parse_session(values: Mapping[str, object]) -> dict:
    """The session itself - its number in the week, and an optional name."""
    number = _count(values.get("number"), "Session number") or 1
    if not 1 <= number <= config.MAX_SESSIONS_PER_WEEK:
        raise InvalidWorkout(
            f"Session {number} is outside 1-{config.MAX_SESSIONS_PER_WEEK}")
    return {
        "number": number,
        "name": " ".join(str(values.get("name") or "").split()) or None,
        "note": (str(values.get("note") or "")).strip() or None,
    }


# --------------------------------------------------------------------------- #
# Display
# --------------------------------------------------------------------------- #
def session_title(row: Mapping) -> str:
    """A session's name, or the movements it is built on if it has none.

    The workbook names its sessions after the lifts that carry working sets -
    "Bench Press + Squats" - and leaves the accessories out, which is why
    v_sessions computes `main_lifts` the way it does.
    """
    name = (row.get("name") or "").strip()
    if name:
        return name
    lifts = (row.get("main_lifts") or "").strip()
    number = row.get("number") or row.get("session_number")
    if lifts:
        return f"Session {number} - {lifts}" if number else lifts
    return f"Session {number}" if number else "Session"


def week_title(row: Mapping) -> str:
    """'Week 7', or 'Week 19 - Deload' when the week has a label."""
    number = row.get("number") or row.get("week_number")
    label = (row.get("label") or row.get("week_label") or "").strip()
    return f"Week {number} - {label}" if label else f"Week {number}"


def set_label(row: Mapping) -> str:
    """'W1' for a warm-up, '3' for the third working set - the sheet's Set #."""
    prefix = config.SET_TYPE_PREFIX.get(row.get("set_type"), "")
    return f"{prefix}{row.get('position')}"


def describe_plan(row: Mapping) -> str:
    """A plan in one line, for a dropdown or a flash message."""
    weeks = row.get("weeks") or 0
    done, total = row.get("sessions_done") or 0, row.get("sessions") or 0
    parts = [f"{weeks} week{'' if weeks == 1 else 's'}"]
    if total:
        parts.append(f"{done}/{total} sessions done")
    return f"{row.get('name')} - {', '.join(parts)}"


def progress(row: Mapping) -> float:
    """How far through a plan you are, 0.0 to 1.0."""
    total = row.get("sessions") or 0
    return (row.get("sessions_done") or 0) / total if total else 0.0


# --------------------------------------------------------------------------- #
# Small shared parsers
# --------------------------------------------------------------------------- #
def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "y")
    return bool(value)


def _one_of(raw, allowed: Sequence[str], label: str, default: str) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return default
    if text not in allowed:
        raise InvalidWorkout(f"'{raw}' is not a {label.lower()} "
                             f"({' or '.join(allowed)})")
    return text


def _count(raw, label: str):
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        value = int(float(str(raw).strip()))
    except ValueError:
        raise InvalidWorkout(f"'{raw}' is not a number of "
                             f"{label.lower()}") from None
    if value <= 0:
        raise InvalidWorkout(f"{label} has to be at least 1")
    return value


def _reps_text(raw, label: str):
    """A rep target kept as text, having been checked it parses."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    low, high = parse_reps(raw, label)
    return f"{low}-{high}" if high else f"{low}"


def today() -> dt.date:
    return dt.date.today()
