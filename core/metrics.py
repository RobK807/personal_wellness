"""Metric rules: precision, validation, interpolation and formatting.

This is the module that encodes what the Adjustments sheet did by hand. That
sheet was a scratch pad: you pasted the last reading above and the new one
below, and it produced the missing days in between as a straight line. Its
formulas, for a gap of n days, were

    day i of n  =  ROUND(prev + i * (next - prev) / (n + 1), 1)

for all twelve raw columns - both weigh-ins, six metrics each. `interpolate()`
below is that formula, with one deliberate difference: it rounds each metric to
the precision the scale actually reports rather than to 1dp across the board.
The sheet's blanket ROUND(...,1) produced values like 1667.5 kcal and 6.5
visceral fat, which no reading could ever be. Those values are still in the
imported history - they are what was recorded - but new back-fills are whole
numbers where the metric is a whole number.

The n = 1 case in the sheet used a bare AVERAGE with no rounding at all, which
is why 14% of the imported rows carry two decimal places. New back-fills round
that case too, so everything from here on is a value the scale could produce.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable, Mapping

import config

# Sanity bounds. These are not medical judgements - they are fat-finger guards,
# wide enough that a real reading never trips one but narrow enough to catch
# 715 typed for 71.5.
BOUNDS = {
    "weight":              (20.0, 300.0),
    "bmi":                 (5.0, 80.0),
    "body_fat_pct":        (1.0, 70.0),
    "skeletal_muscle_pct": (5.0, 80.0),
    "rm_kcal":             (500.0, 5000.0),
    "visceral_fat":        (1.0, 60.0),
}


class InvalidReading(ValueError):
    """A value that is missing, unparseable or outside the sanity bounds."""


# --------------------------------------------------------------------------- #
# Precision
# --------------------------------------------------------------------------- #
def round_metric(key: str, value: float) -> float:
    """Round to the precision the scale reports for that metric."""
    dp = config.DP.get(key, 1)
    result = round(float(value), dp)
    return result if dp else float(int(round(result)))


def parse_value(key: str, raw) -> float:
    """Coerce one form field into a stored value, or raise InvalidReading."""
    label = config.LABELS.get(key, key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise InvalidReading(f"{label} is required")
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        raise InvalidReading(f"'{raw}' is not a number for {label}") from None

    low, high = BOUNDS.get(key, (float("-inf"), float("inf")))
    if not low <= value <= high:
        raise InvalidReading(
            f"{label} of {value:g} looks wrong - expected between {low:g} and {high:g}"
        )
    return round_metric(key, value)


def parse_reading(values: Mapping[str, object]) -> dict:
    """Parse a whole weigh-in. Raises on the first problem, listing the field."""
    return {key: parse_value(key, values.get(key)) for key in config.METRIC_KEYS}


# --------------------------------------------------------------------------- #
# Derived values
# --------------------------------------------------------------------------- #
def derive(row: Mapping[str, float]) -> dict:
    """Body fat and skeletal muscle as masses (the workbook's Data!T and U).

    Applied to the averaged weight and the averaged percentage, which is what
    the sheet did - not the average of two separately derived masses.
    """
    weight = row.get("weight")
    if weight is None:
        return {"body_fat_kg": None, "muscle_kg": None}
    return {
        "body_fat_kg": round(weight * row["body_fat_pct"] / 100.0, 6),
        "muscle_kg": round(weight * row["skeletal_muscle_pct"] / 100.0, 6),
    }


# --------------------------------------------------------------------------- #
# Interpolation - the Adjustments sheet
# --------------------------------------------------------------------------- #
def interpolate(previous: Mapping[str, float], following: Mapping[str, float],
                gap: int) -> list:
    """The `gap` readings that sit on a straight line between two real ones.

    `gap` is the number of missing days, so a Monday reading followed by a
    Thursday one is gap = 2. Returns a list of `gap` dicts, first missing day
    first, each rounded to its metric's own precision.
    """
    if gap < 1:
        return []
    filled = []
    for step in range(1, gap + 1):
        row = {}
        for key in config.METRIC_KEYS:
            start, end = float(previous[key]), float(following[key])
            row[key] = round_metric(key, start + step * (end - start) / (gap + 1))
        filled.append(row)
    return filled


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
def as_date(value) -> dt.date:
    """Accept a date, a datetime or an ISO string; reject anything else."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        raise InvalidReading(f"'{value}' is not a date (use YYYY-MM-DD)") from None


def days_between(earlier: dt.date, later: dt.date) -> int:
    return (later - earlier).days


def date_range(first: dt.date, last: dt.date) -> Iterable:
    """Every date from `first` to `last` inclusive."""
    for offset in range((last - first).days + 1):
        yield first + dt.timedelta(days=offset)


# --------------------------------------------------------------------------- #
# Display
# --------------------------------------------------------------------------- #
def unit_suffix(key: str) -> str:
    """What to append to a formatted value: '%' hugs the number, 'kg' does not.

    Its own function because the Flask charts send this to the browser once per
    series and format the values there, and the rule should not end up written
    twice in two languages.
    """
    unit = config.UNITS.get(key, "")
    if unit == "%":
        return "%"
    return f" {unit}" if unit else ""


def fmt(key: str, value, with_unit: bool = False) -> str:
    """Format a value the way this metric should be shown."""
    if value is None:
        return "-"
    dp = config.DP.get(key, 1)
    text = f"{float(value):,.{dp}f}"
    return text + unit_suffix(key) if with_unit else text


def fmt_change(key: str, value, with_unit: bool = False) -> str:
    """Same, but always signed - a change of zero reads as '0.0', not '-0.0'."""
    if value is None:
        return "-"
    dp = config.DP.get(key, 1)
    # Round first, then test. Anything that rounds to zero at this metric's
    # precision is no change, and both "+0.0" and "-0" read as movement that did
    # not happen - the second of which is what a visceral fat change of -0.4
    # would otherwise print as.
    value = round(float(value), dp)
    text = f"{0.0:,.{dp}f}" if value == 0 else f"{value:+,.{dp}f}"
    return text + unit_suffix(key) if with_unit else text


def period_label(grain: str, period, short: bool = False) -> str:
    """Label a period the way that grain should read.

    Daily is a date, weekly is the week commencing, monthly is the month - the
    same three shapes the workbook's derived sheets used in their first column.
    """
    when = as_date(period)
    if grain == "monthly":
        return f"{when:%b %y}" if short else f"{when:%B %Y}"
    if grain == "weekly":
        return f"{when:%d/%m}" if short else f"w/c {when:%d/%m/%Y}"
    return f"{when:%d/%m}" if short else f"{when:%d/%m/%Y}"


def direction(key: str, change) -> str:
    """'good', 'bad' or 'flat' for a change in this metric.

    Only used for colour. Weight, BMI, body fat and visceral fat are better
    going down; skeletal muscle and RM Kcal are better going up.
    """
    if change is None:
        return "flat"
    # Rounded the same way fmt_change() prints it, so a change shown as "0.0"
    # is never coloured as though it moved.
    change = round(float(change), config.DP.get(key, 1))
    if change == 0:
        return "flat"
    improving = change < 0 if config.BETTER.get(key) == "down" else change > 0
    return "good" if improving else "bad"
