"""Run rules: durations, paces, the breakdown ladder, validation, formatting.

This is the run tracker's answer to core/metrics.py, and it is the only place
that knows how a time is written down or how a pace is worked out.

Durations
---------
Everything is stored as whole seconds. The workbook held elapsed time as an
Excel time-of-day, which reads fine and sorts fine right up to the point where
a run passes 24 hours or you want to average two of them. Seconds do all three,
and `fmt_duration()` puts mm:ss or h:mm:ss back on at the edge.

Pace
----
Pace is derived, never stored: it is duration over distance and nothing else,
and a stored copy is one edit away from disagreeing with the two columns it
came from.

That leaves one visible difference from the spreadsheet. The run-level Pace
column reproduces exactly - all 1,546 rows - because it was itself computed
from the rounded time and distance. The Breakdown pace column is Strava's own
figure, taken from a time it knows to better than a second, and the sheet kept
only the whole second: a 400m in 121.2s is a 5:03/km to Strava and a 5:02/km to
anyone working from "121". 112 of the 1,542 breakdown rows differ by that one
second. Deriving it is still the right answer here, because a run entered by
hand has no Strava figure to copy, and one rule that is occasionally a second
out beats two rules that disagree about which runs they apply to.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable, Mapping, Sequence

import config
from core.metrics import as_date  # re-exported: dates work the same everywhere


class InvalidRun(ValueError):
    """A run field that is missing, unparseable or outside the sanity bounds."""


# --------------------------------------------------------------------------- #
# Durations
# --------------------------------------------------------------------------- #
def parse_duration(raw, label: str = "Time") -> int:
    """Seconds from 'mm:ss', 'h:mm:ss', a plain number of seconds, or a time.

    Accepts what a person actually types. '41:54' is forty-one minutes, not
    forty-one hours - a run is measured from the minutes up, which is why the
    fields are read right to left rather than left to right.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise InvalidRun(f"{label} is required")

    if isinstance(raw, dt.timedelta):
        return int(round(raw.total_seconds()))
    if isinstance(raw, dt.time):
        return raw.hour * 3600 + raw.minute * 60 + raw.second
    if isinstance(raw, (int, float)):
        return int(round(float(raw)))

    text = str(raw).strip()
    parts = text.split(":")
    if len(parts) > 3:
        raise InvalidRun(f"'{text}' is not a {label.lower()} (use mm:ss or h:mm:ss)")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        raise InvalidRun(
            f"'{text}' is not a {label.lower()} (use mm:ss or h:mm:ss)") from None

    # Right to left: seconds, then minutes, then hours. Only the leading field
    # may run past 60 - '90:00' is a legitimate ninety minutes, '1:90:00' is a
    # typo - so every component except the first is bounded.
    total = 0.0
    leading = len(numbers) - 1
    for power, value in enumerate(reversed(numbers)):
        if power < leading and not 0 <= value < 60:
            raise InvalidRun(f"'{text}' is not a {label.lower()} - "
                             f"{value:g} is not a number of minutes or seconds")
        total += value * (60 ** power)
    if total < 0:
        raise InvalidRun(f"'{text}' is not a {label.lower()}")
    return int(round(total))


def fmt_duration(seconds, force_hours: bool = False) -> str:
    """'41:54' under an hour, '1:10:22' over it."""
    if seconds is None:
        return "-"
    seconds = int(round(float(seconds)))
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours or force_hours:
        return f"{sign}{hours}:{minutes:02d}:{secs:02d}"
    return f"{sign}{minutes}:{secs:02d}"


# --------------------------------------------------------------------------- #
# Pace
# --------------------------------------------------------------------------- #
def pace(seconds, km) -> float | None:
    """Seconds per kilometre, unrounded."""
    if seconds is None or not km:
        return None
    return float(seconds) / float(km)


def fmt_pace(seconds_per_km, with_unit: bool = False) -> str:
    """A pace as mm:ss, optionally with the /km that makes it a pace.

    Rounded to the whole second here and nowhere else, so the figure on a chart
    axis and the figure in a table are always the same figure.
    """
    if seconds_per_km is None:
        return "-"
    text = fmt_duration(round(float(seconds_per_km)))
    return f"{text} /km" if with_unit else text


def fmt_distance(km, dp: int = 2) -> str:
    if km is None:
        return "-"
    return f"{float(km):,.{dp}f}"


# --------------------------------------------------------------------------- #
# The breakdown ladder
# --------------------------------------------------------------------------- #
def ladder_for(distance_km: float) -> list:
    """The rungs a run of this length could reach: (label, km), in order.

    A 7.83 km run has a fastest 5K in it and no fastest 10K, which is why the
    Breakdown column is sparse. A little tolerance is allowed at the top so a
    21.09 km run still offers the half-marathon it almost certainly contains.
    """
    limit = float(distance_km) * 1.001
    return [(label, km) for label, km in config.BREAKDOWNS if km <= limit]


def ladder_labels(distance_km: float) -> list:
    return [label for label, _ in ladder_for(distance_km)]


def breakdown_km(label: str) -> float:
    """How far a named rung is. Raises rather than guessing at an unknown one."""
    try:
        return config.BREAKDOWN_KM[label]
    except KeyError:
        raise InvalidRun(
            f"'{label}' is not one of the breakdown distances "
            f"({', '.join(config.BREAKDOWN_LABELS)})") from None


def in_ladder_order(labels: Iterable[str]) -> list:
    """Sort a set of breakdown labels back into ladder order."""
    return sorted(labels, key=lambda label: config.BREAKDOWN_ORDER.get(label, 99))


# --------------------------------------------------------------------------- #
# Parsing a whole run
# --------------------------------------------------------------------------- #
def parse_distance(raw) -> float:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise InvalidRun("Distance is required")
    try:
        value = float(str(raw).strip().removesuffix("km").strip())
    except (TypeError, ValueError):
        raise InvalidRun(f"'{raw}' is not a distance in kilometres") from None
    low, high = config.RUN_BOUNDS["distance_km"]
    if not low <= value <= high:
        raise InvalidRun(
            f"A distance of {value:g} km looks wrong - "
            f"expected between {low:g} and {high:g}")
    # Two decimal places is what Strava reports and what the sheet holds.
    return round(value, 2)


def parse_choice(raw, allowed: Sequence[str], label: str) -> str:
    """A run or effort type, which has to be one the list offers.

    The list is a closed one, kept in the database and edited on the Admin page
    - see core/run_options.py for why it is closed. Matching is
    case-insensitive and the stored spelling wins, so a value arriving from
    somewhere other than the dropdown cannot quietly fork an option in two.
    """
    text = " ".join(str(raw or "").split())
    if not text:
        raise InvalidRun(f"{label} is required")
    for option in allowed:
        if option.casefold() == text.casefold():
            return option
    raise InvalidRun(
        f"'{text}' is not one of the {label.lower()} options"
        + (f" ({', '.join(allowed)})" if allowed else "")
        + ". The list is on the Admin page")


def parse_run(values: Mapping[str, object], allowed: Mapping[str, Sequence[str]],
              today: dt.date | None = None) -> dict:
    """Parse the run itself - everything except the breakdowns.

    `allowed` carries the two dropdown lists, keyed 'run_type' and
    'effort_type'. Passed in rather than looked up, so this module stays the
    rules and nothing else: it does not know there is a database.
    """
    when = as_date(values.get("day"))
    if when > (today or dt.date.today()):
        raise InvalidRun(f"{when:%d/%m/%Y} is in the future")

    distance = parse_distance(values.get("distance_km"))
    duration = parse_duration(values.get("duration_s"), "Time")
    low, high = config.RUN_BOUNDS["duration_s"]
    if not low <= duration <= high:
        raise InvalidRun(
            f"A time of {fmt_duration(duration)} looks wrong - expected between "
            f"{fmt_duration(low)} and {fmt_duration(high, force_hours=True)}")

    return {
        "day": when,
        "distance_km": distance,
        "duration_s": duration,
        "run_type": parse_choice(values.get("run_type"),
                                 allowed.get("run_type") or (), "Run type"),
        "effort_type": parse_choice(values.get("effort_type"),
                                    allowed.get("effort_type") or (),
                                    "Effort type"),
        "note": (str(values.get("note") or "")).strip() or None,
        **parse_intervals(values, distance, duration),
    }


# --------------------------------------------------------------------------- #
# Interval sessions
# --------------------------------------------------------------------------- #
BLANK_INTERVALS = {"interval_type": None, "interval_count": None,
                   "interval_distance_m": None, "interval_time_s": None,
                   "interval_pace_s": None}


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


# Ways of writing a time that someone might reach for in the distance box on a
# session set by time. Caught by name so the message can point at the right box
# rather than saying '3min' is not a distance and leaving it there.
_TIME_SHAPED = ("min", "mins", "minute", "minutes", "sec", "secs", "second",
                "seconds")


def parse_interval_distance(raw) -> float:
    """Metres per rep, from '400', '400m', '1k' or '1.6km'.

    Accepts the shorthand because that is how the sessions are named - the runs
    already in the database are called things like '8x1k intervals' - and
    making someone type 1000 when they wrote 1k is the sort of friction that
    stops a field being filled in at all.

    Never a time. A session set by time records its rep length in
    `interval_time_s` and leaves this blank; see config.INTERVAL_FIELD_HELP.
    """
    text = str(raw).strip().lower().replace(" ", "")
    if ":" in text or text.endswith(_TIME_SHAPED):
        raise InvalidRun(
            f"'{raw}' is a time, not a distance. A session set by time records "
            f"its reps in 'Time per interval' and leaves this blank; this box "
            f"takes how far each rep was (try 400m, 1k or 1.6km)")
    multiplier = 1.0
    if text.endswith("km"):
        text, multiplier = text[:-2], 1000.0
    elif text.endswith("k"):
        text, multiplier = text[:-1], 1000.0
    elif text.endswith("m"):
        text = text[:-1]
    try:
        metres = float(text) * multiplier
    except ValueError:
        raise InvalidRun(
            f"'{raw}' is not an interval distance (try 400m, 1k or 1.6km)"
        ) from None

    low, high = config.INTERVAL_BOUNDS["interval_distance_m"]
    if not low <= metres <= high:
        raise InvalidRun(
            f"An interval distance of {metres:g} m looks wrong - expected "
            f"between {low:g} and {high:g}")
    return round(metres, 1)


INTERVAL_FIELDS = ("interval_type", "interval_count", "interval_distance_m",
                   "interval_time_s", "interval_pace_s")

# What each length field is called on screen, for messages that need to send
# someone to the right box.
_FIELD_LABEL = {"interval_distance_m": "Distance per interval",
                "interval_time_s": "Time per interval",
                "interval_pace_s": "Pace per interval"}


def parse_intervals(values: Mapping[str, object], run_km: float,
                    run_duration_s: int | None = None) -> dict:
    """The five interval columns, or five Nones if the session was not one.

    Every field is entered and none is worked out from another. That is worth
    stating because the obvious shortcut - deriving the pace from the rep
    length - only exists for a session set by distance. Ten-second sprints are
    0:10 each at something like 3:00/km, and no arithmetic here gets from one
    to the other, so the pace is asked for and kept.

    Everything is optional, since a session filled in from memory may only have
    some of it, but these are refused as misleading rather than incomplete:

      * a count or a length with no type, because the type is what says which
        of the two length boxes applies;
      * the length that belongs to the other type - a distance per rep on a
        session set by time, or the reverse;
      * a session whose reps add up to more than the run they sit inside,
        either in distance or in time.
    """
    kind = values.get("interval_type")
    count = values.get("interval_count")
    entered = {name: values.get(name) for name in
               ("interval_distance_m", "interval_time_s", "interval_pace_s")}

    if _blank(kind) and _blank(count) and all(map(_blank, entered.values())):
        return dict(BLANK_INTERVALS)

    if _blank(kind):
        raise InvalidRun(
            "Choose whether the intervals were set by distance or by time - "
            "without it there is no telling which length box applies")
    kind = str(kind).strip().lower()
    if kind not in config.INTERVAL_TYPES:
        raise InvalidRun(f"'{kind}' is not an interval type "
                         f"({' or '.join(config.INTERVAL_TYPES)})")

    # Exactly one of the two length boxes belongs to this kind of session. The
    # other is refused rather than dropped, because silently discarding
    # something that was typed in is how a form loses an afternoon's entry.
    mine = config.INTERVAL_LENGTH_FIELD[kind]
    theirs = config.INTERVAL_LENGTH_FIELD[
        "time" if kind == "distance" else "distance"]
    if not _blank(entered[theirs]):
        raise InvalidRun(
            f"'{_FIELD_LABEL[theirs]}' does not apply to a session set by "
            f"{kind} - that one records {'how far' if kind == 'time' else 'how long'}"
            f" each rep was, which a session set by {kind} does not fix. Put "
            f"the reps in '{_FIELD_LABEL[mine]}' and leave the other blank")

    parsed = dict(BLANK_INTERVALS, interval_type=kind)

    if not _blank(count):
        try:
            parsed["interval_count"] = int(str(count).strip())
        except ValueError:
            raise InvalidRun(f"'{count}' is not a number of intervals") from None
        low, high = config.INTERVAL_BOUNDS["interval_count"]
        if not low <= parsed["interval_count"] <= high:
            raise InvalidRun(
                f"{parsed['interval_count']} intervals looks wrong - expected "
                f"between {low} and {high}")

    if not _blank(entered["interval_distance_m"]):
        parsed["interval_distance_m"] = parse_interval_distance(
            entered["interval_distance_m"])

    for name, label in (("interval_time_s", "Time per interval"),
                        ("interval_pace_s", "Pace per interval")):
        if _blank(entered[name]):
            continue
        parsed[name] = parse_duration(entered[name], label)
        low, high = config.INTERVAL_BOUNDS[name]
        if not low <= parsed[name] <= high:
            message = (f"{label} of {fmt_duration(parsed[name])} looks wrong - "
                       f"expected between {fmt_duration(low)} and "
                       f"{fmt_duration(high)}")
            if name == "interval_pace_s":
                message += (" per kilometre. A pace is not how long a rep took "
                            "- ten-second sprints are 0:10 each, at something "
                            "like 3:00/km")
            raise InvalidRun(message)

    # The reps cannot add up to more than the run they sit inside - in ground
    # covered for a session set by distance, in time on the clock for one set
    # by time. Same check, whichever unit the session is measured in.
    count, metres, seconds = (parsed["interval_count"],
                              parsed["interval_distance_m"],
                              parsed["interval_time_s"])
    if count and metres and count * metres / 1000.0 > run_km * 1.001:
        raise InvalidRun(
            f"{count} x {fmt_interval_distance(metres)} is "
            f"{count * metres / 1000.0:,.2f} km of intervals inside a "
            f"{fmt_distance(run_km)} km run")
    if count and seconds and run_duration_s and count * seconds > run_duration_s:
        raise InvalidRun(
            f"{count} x {fmt_duration(seconds)} is "
            f"{fmt_duration(count * seconds)} of intervals inside a "
            f"{fmt_duration(run_duration_s)} run")
    return parsed


def fmt_interval_distance(metres) -> str:
    """'400 m' under a kilometre, '1k' or '1.6k' above it."""
    if metres is None:
        return "-"
    metres = float(metres)
    if metres < 1000:
        return f"{metres:g} m"
    return f"{metres / 1000:g}k"


def interval_length(run: Mapping) -> str | None:
    """One rep, written the way its own kind of session measures them.

    '1k' for a session set by distance, '0:10' for one set by time. None when
    the box that applies has not been filled in yet.
    """
    kind = run.get("interval_type")
    if kind == "distance" and run.get("interval_distance_m"):
        return fmt_interval_distance(run["interval_distance_m"])
    if kind == "time" and run.get("interval_time_s"):
        return fmt_duration(run["interval_time_s"])
    return None


def interval_summary(run: Mapping) -> str:
    """A session in the shorthand it was named in: '8 x 1k @ 3:50/km'.

    The reps lead, because that is what the session was; the pace follows the
    @, because that is how it went. Carrying the /km is not decoration - it is
    what stops '10 x 0:10 @ 3:00' reading as though the reps took three
    minutes each.
    """
    if not run.get("interval_type"):
        return ""
    count = run.get("interval_count")
    length = interval_length(run)
    rep_pace = run.get("interval_pace_s")

    parts = []
    if count and length:
        parts.append(f"{count} x {length}")
    elif count:
        parts.append(f"{count} intervals")
    elif length:
        parts.append(length)
    if rep_pace:
        parts.append(f"@ {fmt_pace(rep_pace, True)}")
    return " ".join(parts)


def parse_breakdowns(entries: Mapping[str, object], distance_km: float,
                     duration_s: int) -> list:
    """Parse the breakdown times into ladder rows, longest-checked-first.

    `entries` maps a breakdown label to whatever was typed against it; blanks
    are skipped, which is how a run records the four rungs it reached rather
    than being made to account for all eleven.

    Two things are refused outright, because both make the records table lie:
    a rung longer than the run, and a split slower than the whole run it sits
    inside. The imported history contains seventeen of the second kind and they
    are kept - see core/strava_import.py - but nothing new joins them.
    """
    reachable = dict(ladder_for(distance_km))
    rows = []
    for label, raw in entries.items():
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        km = breakdown_km(label)
        if label not in reachable:
            raise InvalidRun(
                f"A {label} split needs a run of at least {km:g} km - "
                f"this one was {fmt_distance(distance_km)} km")
        seconds = parse_duration(raw, f"{label} split")
        if seconds > duration_s:
            raise InvalidRun(
                f"The {label} split ({fmt_duration(seconds)}) is longer than "
                f"the whole run ({fmt_duration(duration_s)})")
        rows.append({
            "breakdown": label,
            "ordinal": config.BREAKDOWN_ORDER[label],
            "km": km,
            "seconds": seconds,
        })

    rows.sort(key=lambda row: row["ordinal"])
    # A 5K cannot be quicker than the 1K inside it. Checked in ladder order so
    # the message names the first pair that disagrees rather than the last.
    for shorter, longer in zip(rows, rows[1:]):
        if longer["seconds"] < shorter["seconds"]:
            raise InvalidRun(
                f"The {longer['breakdown']} split "
                f"({fmt_duration(longer['seconds'])}) cannot be quicker than "
                f"the {shorter['breakdown']} split inside it "
                f"({fmt_duration(shorter['seconds'])})")
    return rows


# --------------------------------------------------------------------------- #
# Display
# --------------------------------------------------------------------------- #
def describe(run: Mapping) -> str:
    """A run in one line, for a flash message or a page title."""
    return (f"{fmt_distance(run['distance_km'])} km in "
            f"{fmt_duration(run['duration_s'])} "
            f"({fmt_pace(pace(run['duration_s'], run['distance_km']), True)})")


def medal(position: int) -> str:
    """1st, 2nd, 3rd, ... for the records table."""
    if 10 <= position % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(position % 10, "th")
    return f"{position}{suffix}"
