"""Food rules: macros, portions, scaling, weeks, validation, formatting.

The food section's answer to core/runs.py and core/workouts.py, and the only
place that knows how a portion turns into macros or how a week is bounded.

Macros
------
Four, and only four: calories, carbs, fat, protein. The workbook carried sodium
and sugar columns that held no data, and they are not here.

Portions
--------
A catalogue row records its macros *for* a portion - 100 grams, 1 Bar, 1
Portion - so eating 50 grams of something recorded per 100 g is half of it.
`scale_macros()` is that division, and it is the only arithmetic in the section
that can silently be wrong, which is why it is one function with one test.

Weeks
-----
A planning week starts on whichever day `config.WEEK_STARTS_ON` names. The
workbook ran Monday to Sunday and every "W/C" header in its diary is a Monday,
so that is the default - but a week is a habit rather than a fact, and the
importer and the planner both go through `week_start()` rather than assuming.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Iterable, Mapping, Sequence

import config
from core.metrics import as_date  # re-exported: dates work the same everywhere

ZERO = {key: 0.0 for key in config.MACRO_KEYS}


class InvalidFood(ValueError):
    """A food or diary field that is missing, unparseable or out of bounds."""


# --------------------------------------------------------------------------- #
# Macro arithmetic
# --------------------------------------------------------------------------- #
def macros_of(row: Mapping) -> dict:
    """Just the four macros out of whatever row this is."""
    return {key: float(row.get(key) or 0.0) for key in config.MACRO_KEYS}


def add_macros(rows: Iterable[Mapping]) -> dict:
    """Total the macros over any number of rows."""
    total = dict(ZERO)
    for row in rows:
        for key in config.MACRO_KEYS:
            total[key] += float(row.get(key) or 0.0)
    return {key: round(value, 4) for key, value in total.items()}


def scale_macros(row: Mapping, factor: float) -> dict:
    """The macros multiplied by `factor`, rounded to two places.

    Two places because a tenth of a gram of fat is below what any label states,
    and carrying binary-float dust into a total that gets compared against a
    target produces "0.30000000000000004 over" - which is not a thing to tell
    somebody about their lunch.
    """
    return {key: round(float(row.get(key) or 0.0) * float(factor), 2)
            for key in config.MACRO_KEYS}


def portion_factor(food: Mapping, quantity) -> float:
    """How many of the catalogue's portions `quantity` is.

    The row records its macros for `portion` of `units` - 100 grams, 1 Bar - so
    50 grams of a per-100 g food is 0.5. A missing or zero portion is treated
    as 1 rather than raising: it is a catalogue that has been half filled in,
    and refusing to show the food is worse than showing it at face value.
    """
    portion = float(food.get("portion") or 0) or 1.0
    return float(quantity) / portion


def eaten(food: Mapping, quantity) -> dict:
    """The macros for `quantity` of `food`."""
    return scale_macros(food, portion_factor(food, quantity))


def remaining(totals: Mapping, target: Mapping) -> dict:
    """Target minus consumed, per macro. Positive means there is room left."""
    return {key: round(float(target.get(key) or 0.0)
                       - float(totals.get(key) or 0.0), 2)
            for key in config.MACRO_KEYS}


def pct_of_target(totals: Mapping, target: Mapping) -> dict:
    """Consumed as a fraction of target, per macro. None where no target."""
    out = {}
    for key in config.MACRO_KEYS:
        goal = float(target.get(key) or 0.0)
        out[key] = (float(totals.get(key) or 0.0) / goal) if goal else None
    return out


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def fmt_macro(key: str, value, with_unit: bool = False) -> str:
    """A macro at the precision it is worth stating: calories whole, grams 1dp."""
    if value is None:
        return "-"
    text = f"{float(value):,.{config.MACRO_DP.get(key, 1)}f}"
    if config.MACRO_DP.get(key, 1):
        text = text.rstrip("0").rstrip(".") if "." in text else text
    return f"{text} {config.MACRO_UNITS.get(key, '')}".strip() if with_unit \
        else text


def fmt_delta(key: str, value) -> str:
    """'+120' over, '-40' under. Signed, because the sign is the whole point."""
    if value is None:
        return "-"
    return ("+" if value > 0 else "") + fmt_macro(key, value)


def fmt_quantity(quantity, units: str | None) -> str:
    """'2 Portion', '50 grams', or nothing at all."""
    if quantity is None:
        return (units or "").strip()
    number = f"{float(quantity):,.2f}".rstrip("0").rstrip(".")
    return f"{number} {units}".strip() if units else number


def describe(entry: Mapping) -> str:
    """One diary line: 'Banana - 1 Portion'.

    The workbook's diary stored exactly this string and nothing else, which is
    why the imported history has a name and no separable quantity. New entries
    keep the parts and this puts them back together the same way.
    """
    name = (entry.get("name") or "").strip()
    detail = fmt_quantity(entry.get("quantity"), entry.get("units"))
    return f"{name} - {detail}" if detail else name


SPLIT = re.compile(r"^(?P<name>.*?)\s+-\s+(?P<qty>[\d.]+)\s*(?P<units>.*)$")


def split_description(text: str) -> dict:
    """Pull 'Banana - 1 Portion' back apart, best effort.

    Used only when reading the historic diary, and deliberately not trusted: a
    name that itself contains " - " is common ("Nature Valley - Salted Caramel
    Nut - 1 Bar") and the quantity is what disambiguates, so the split is
    anchored on the last " - number units" rather than the first separator.
    Returns the whole string as the name when it does not match, which is the
    honest answer rather than a guess.
    """
    text = " ".join(str(text or "").split())
    best = None
    for match in re.finditer(r"\s+-\s+(?P<qty>[\d.]+)\s*(?P<units>[^-]*)$",
                             text):
        best = match
    if best is None:
        return {"name": text, "quantity": None, "units": None}
    try:
        quantity = float(best.group("qty"))
    except ValueError:
        return {"name": text, "quantity": None, "units": None}
    return {"name": text[:best.start()].strip(),
            "quantity": quantity,
            "units": (best.group("units") or "").strip() or None}


# --------------------------------------------------------------------------- #
# "Did you mean"
# --------------------------------------------------------------------------- #
def normalise(name: str) -> str:
    """A food name reduced to what is worth comparing.

    Case, punctuation and runs of whitespace all go, because they are the three
    things that differ between two spellings of the same food and none of them
    changes what it is. "Sainsbury's Basmati Rice" and "sainsburys basmati rice"
    come out identical, which is the point.
    """
    text = str(name or "").casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def close_matches(name: str, candidates: Iterable[Mapping],
                  limit: int | None = None,
                  ratio: float | None = None) -> list:
    """Catalogue rows whose name is nearly `name`, best first.

    For catching "Chiken breast" before it becomes the 188th food in a list that
    already has "Chicken breast" in it. Deliberately an alert rather than a
    correction - the whole reason free text exists is that sometimes the thing
    you ate really is new, and a picker that quietly substituted the nearest
    name would make the diary wrong in a way nobody would ever notice.

    Three ways of being close, in descending order of confidence:

      1.0   the same name once case and punctuation are set aside
      0.95  one name contains the other - "Banana" in "Banana bread", which is
            worth flagging precisely because it is *not* the same food
      -     difflib's ratio, above config.FOOD_MATCH_RATIO

    An exact match is included rather than filtered out, because the caller
    decides what an exact match means: on the day form it is a food being
    chosen, and on the catalogue form it is a duplicate being refused.
    """
    import difflib

    wanted = normalise(name)
    if not wanted:
        return []
    ratio = config.FOOD_MATCH_RATIO if ratio is None else float(ratio)
    limit = config.FOOD_MATCH_LIMIT if limit is None else int(limit)

    scored = []
    for row in candidates:
        other = normalise(row.get("name"))
        if not other:
            continue
        if other == wanted:
            score = 1.0
        elif wanted in other or other in wanted:
            # Guarded by length: "a" inside "banana" is not a near miss, it is
            # a substring, and every food in the catalogue would match it.
            shorter = min(len(wanted), len(other))
            score = 0.95 if shorter >= 4 else 0.0
        else:
            score = difflib.SequenceMatcher(None, wanted, other).ratio()
        if score >= ratio:
            scored.append((score, row))

    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("name") or "")))
    return [{**row, "score": round(score, 3)} for score, row in scored[:limit]]


def match_alert(name: str, matches: Sequence[Mapping]) -> str:
    """The sentence an alert says. One place, so both front-ends say it."""
    if not matches:
        return ""
    first = matches[0]
    exact = first.get("score", 0) >= 1.0
    where = f"{first.get('list')}"
    if first.get("grouping"):
        where += f" / {first['grouping']}"
    lead = (f"'{name}' is already in the catalogue as '{first['name']}'"
            if exact else
            f"'{name}' looks like '{first['name']}'")
    more = (f", and {len(matches) - 1} other"
            f"{'' if len(matches) == 2 else 's'}" if len(matches) > 1 else "")
    return f"{lead} ({where}){more}."


# --------------------------------------------------------------------------- #
# Weeks
# --------------------------------------------------------------------------- #
def week_start(day, starts_on: int | None = None) -> dt.date:
    """The first day of the week `day` falls in.

    `starts_on` is 0 for Monday through 6 for Sunday, defaulting to
    config.WEEK_STARTS_ON. Written as a modulo rather than a table of cases so
    that a week starting on a Wednesday behaves exactly like one starting on a
    Monday, which is the point of parameterising it at all.
    """
    day = as_date(day)
    first = config.WEEK_STARTS_ON if starts_on is None else int(starts_on)
    return day - dt.timedelta(days=(day.weekday() - first) % 7)


def week_days(day, starts_on: int | None = None) -> list:
    """The seven dates of the week `day` falls in, in order."""
    start = week_start(day, starts_on)
    return [start + dt.timedelta(days=offset) for offset in range(7)]


def week_label(day, starts_on: int | None = None) -> str:
    """'w/c 03/08/2026' - the workbook's own way of naming a week."""
    return f"w/c {week_start(day, starts_on):%d/%m/%Y}"


def day_name(day) -> str:
    return config.WEEKDAY_NAMES[as_date(day).weekday()]


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def parse_number(raw, label: str, bounds: str | None = None,
                 required: bool = True):
    """A number, bounded by config.FOOD_BOUNDS if a key is given."""
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        if required:
            raise InvalidFood(f"{label} is required")
        return None
    try:
        value = float(str(raw).strip().replace(",", ""))
    except ValueError:
        raise InvalidFood(f"'{raw}' is not a number ({label.lower()})") from None
    if bounds:
        low, high = config.FOOD_BOUNDS[bounds]
        if not low <= value <= high:
            raise InvalidFood(
                f"{label} of {value:g} looks wrong - expected between "
                f"{low:g} and {high:g}")
    return value


def parse_macros(values: Mapping, required: bool = True) -> dict:
    """The four macros out of a form."""
    out = {}
    for key in config.MACRO_KEYS:
        value = parse_number(values.get(key), config.MACRO_LABELS[key], key,
                             required=required)
        out[key] = 0.0 if value is None else round(value, 2)
    return out


def parse_food(values: Mapping) -> dict:
    """A catalogue row."""
    name = " ".join(str(values.get("name") or "").split())
    if not name:
        raise InvalidFood("A food needs a name")
    if len(name) > config.MAX_NAME_LENGTH * 2:
        raise InvalidFood("That food name is too long")

    list_name = str(values.get("list") or "").strip().title()
    if list_name not in config.FOOD_LISTS:
        raise InvalidFood(f"'{values.get('list')}' is not one of "
                          f"{', '.join(config.FOOD_LISTS)}")

    portion = parse_number(values.get("portion"), "Portion", "portion",
                           required=False)
    return {
        "list": list_name,
        "name": name,
        "grouping": (str(values.get("grouping") or "")).strip() or None,
        "portion": 1.0 if portion is None else portion,
        "units": (str(values.get("units") or "")).strip() or "Portion",
        **parse_macros(values),
        "note": (str(values.get("note") or "")).strip() or None,
    }


def parse_meal(raw) -> str:
    """One of the day's meals."""
    text = " ".join(str(raw or "").split()).title()
    for meal in config.MEALS:
        if meal.casefold() == text.casefold():
            return meal
    if not text:
        raise InvalidFood("Which meal is this?")
    # Not refused: the workbook only ever used four, but a fifth needs no
    # migration and a diary that cannot record what happened is worse.
    return text


def parse_entry(values: Mapping, food: Mapping | None = None) -> dict:
    """One diary line, from the form or from a CSV row.

    With a `food`, the quantity does the work and the macros follow from it.
    Without one - a free-text line, or something eaten out - the macros are
    taken as given, which is the only way the historic diary can be recorded at
    all.
    """
    meal = parse_meal(values.get("meal"))
    quantity = parse_number(values.get("quantity"), "Quantity", "quantity",
                            required=False)

    if food is not None:
        if quantity is None:
            quantity = float(food.get("portion") or 1)
        return {
            "meal": meal,
            "food_id": food.get("id"),
            "name": food["name"],
            "quantity": quantity,
            "units": food.get("units") or "Portion",
            **eaten(food, quantity),
        }

    name = " ".join(str(values.get("name") or "").split())
    if not name:
        raise InvalidFood("A diary line needs a food, or a name of its own")
    return {
        "meal": meal,
        "food_id": values.get("food_id") or None,
        "name": name,
        "quantity": quantity,
        "units": (str(values.get("units") or "")).strip() or None,
        **parse_macros(values, required=False),
    }


def parse_target(values: Mapping) -> dict:
    """A named, dated set of target macros."""
    name = " ".join(str(values.get("name") or "").split())
    if not name:
        raise InvalidFood("A target needs a name - 'Base', 'Workout'")
    starts = values.get("starts_on")
    return {
        "name": name,
        "starts_on": as_date(starts) if starts and str(starts).strip()
                     else dt.date.today(),
        **parse_macros(values),
        "note": (str(values.get("note") or "")).strip() or None,
    }


# --------------------------------------------------------------------------- #
# The macro calculator
# --------------------------------------------------------------------------- #
def combine(components: Sequence[Mapping], scale: float = 1.0) -> dict:
    """Add up components and take a proportion of the result.

    The workbook's Calculator sheet, which built a burrito out of seven rows and
    then took 200 g of a pancake mix that made rather more than that. Each
    component carries its own quantity; `scale` is applied to the total, because
    that is the question being asked - "what is two thirds of this" - rather
    than a property of any one row.
    """
    total = dict(ZERO)
    for item in components:
        factor = float(item.get("quantity") or 1)
        if item.get("portion"):
            factor = portion_factor(item, item.get("quantity") or item["portion"])
        for key in config.MACRO_KEYS:
            total[key] += float(item.get(key) or 0.0) * factor
    scale = float(scale or 1.0)
    return {key: round(value * scale, 2) for key, value in total.items()}
