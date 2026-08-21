"""Reads for the food section. Plain lists of dicts, no pandas.

Same contract as the other three sections and for the same reason: the Flask
front-end runs on a NAS with ~150 MB of RAM free, so nothing on this side of the
line may import pandas.

Day totals come out of v_food_days already added up and already paired with the
target that applied on that date - see the note on that view about why the
target is resolved per day rather than joined once.
"""
from __future__ import annotations

import datetime as dt
from typing import Mapping

import config
from core import db, food


# --------------------------------------------------------------------------- #
# The catalogue
# --------------------------------------------------------------------------- #
def foods(list_name: str | None = None, grouping: str | None = None,
          search: str | None = None, include_retired: bool = False,
          limit: int | None = None) -> list:
    where, params = [], []
    if not include_retired:
        where.append("retired = 0")
    if list_name:
        where.append("list = ?")
        params.append(list_name)
    if grouping:
        where.append("grouping = ?")
        params.append(grouping)
    if search:
        where.append("name LIKE ?")
        params.append(f"%{search.strip()}%")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    tail = f" LIMIT {int(limit)}" if limit else ""
    return db.rows(f"SELECT * FROM foods{clause} "
                   f"ORDER BY list, grouping, name{tail}", params)


def food_row(food_id: int) -> dict | None:
    return db.read_one("SELECT * FROM foods WHERE id = ?", (food_id,))


def food_by_name(name: str, list_name: str | None = None) -> dict | None:
    if list_name:
        return db.read_one(
            "SELECT * FROM foods WHERE name = ? COLLATE NOCASE AND list = ?",
            (str(name).strip(), list_name))
    return db.read_one(
        "SELECT * FROM foods WHERE name = ? COLLATE NOCASE ORDER BY id LIMIT 1",
        (str(name).strip(),))


def groupings(list_name: str | None = None) -> list:
    where = " WHERE list = ?" if list_name else ""
    params = (list_name,) if list_name else ()
    return [row["grouping"] for row in db.rows(
        f"SELECT DISTINCT grouping FROM foods{where} "
        f"{'AND' if where else 'WHERE'} grouping IS NOT NULL "
        f"ORDER BY grouping", params)]


def groupings_by_list(include_retired: bool = True) -> dict:
    """List -> the groupings in it, from the catalogue and from config.

    Both sources, because neither alone is right. The catalogue is what the
    filter has to offer or it filters to nothing; config is what a *new* food
    can be filed under before anything of that kind exists. Sorted together so
    the dropdown does not have two halves.
    """
    out = {name: set(values)
           for name, values in config.FOOD_GROUPINGS.items()}
    for row in foods(include_retired=include_retired):
        if row["grouping"]:
            out.setdefault(row["list"], set()).add(row["grouping"])
    return {name: sorted(values) for name, values in out.items()}


def catalogue_index(rows=None) -> list:
    """The catalogue as the pickers need it: id, name, list, grouping, macros.

    One flat list written into a page once and filtered in the browser, rather
    than 32 dropdowns each carrying every food. See the note in the Flask
    blueprint about what that costs.
    """
    return [{"id": row["id"], "name": row["name"], "list": row["list"],
             "grouping": row["grouping"] or "", "portion": row["portion"],
             "units": row["units"],
             **{key: row[key] for key in config.MACRO_KEYS}}
            for row in (foods() if rows is None else rows)]


def food_usage() -> dict:
    """Food id -> how many diary lines use it. What makes retiring safe."""
    return {row["food_id"]: row["uses"] for row in db.rows(
        "SELECT food_id, COUNT(*) AS uses FROM food_entries "
        "WHERE food_id IS NOT NULL GROUP BY food_id")}


def total_foods() -> int:
    return db.scalar("SELECT COUNT(*) FROM foods", default=0)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def settings() -> dict:
    """Everything in food_settings, as plain strings. Missing keys are absent.

    Callers below fold config's defaults in; this is only what has been chosen.
    Kept separate so the Admin page can show which settings have actually been
    set and which are still inherited.
    """
    return {row["key"]: row["value"]
            for row in db.rows("SELECT key, value FROM food_settings")}


def meal_defaults() -> dict:
    """meal -> (list, grouping) for a new line, settings over config.

    The reason the picker is usable: pick the meal and the List and Grouping are
    already narrowed to the kind of thing that meal usually is, so the food
    dropdown is a dozen options rather than 187.
    """
    stored = settings()
    out = {}
    for meal in config.MEALS:
        fallback = config.FOOD_MEAL_DEFAULTS.get(meal, ("Items", ""))
        list_name = stored.get(f"default_list:{meal}") or fallback[0]
        if list_name not in config.FOOD_LISTS:
            list_name = config.FOOD_LISTS[0]
        grouping = stored.get(f"default_grouping:{meal}")
        if grouping is None:
            grouping = fallback[1]
        out[meal] = (list_name, grouping or "")
    return out


def week_starts_on() -> int:
    """Which day a planning week turns over on, settings over config."""
    raw = settings().get("week_starts_on")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return config.WEEK_STARTS_ON
    return value if 0 <= value <= 6 else config.WEEK_STARTS_ON


# --------------------------------------------------------------------------- #
# Targets
# --------------------------------------------------------------------------- #
def targets() -> list:
    """Every target version, newest first within each name."""
    return db.rows("SELECT * FROM macro_targets ORDER BY name, starts_on DESC")


def target_names() -> list:
    return [row["name"] for row in db.rows(
        "SELECT DISTINCT name FROM macro_targets ORDER BY name")]


def target_for(day, name: str | None = None) -> dict | None:
    """The version of a profile in force on a date."""
    day = food.as_date(day).isoformat()
    if name:
        return db.read_one(
            "SELECT * FROM macro_targets WHERE name = ? AND starts_on <= ? "
            "ORDER BY starts_on DESC, id DESC LIMIT 1", (name, day))
    return db.read_one(
        "SELECT * FROM macro_targets WHERE starts_on <= ? "
        "ORDER BY starts_on DESC, id DESC LIMIT 1", (day,))


def total_targets() -> int:
    return db.scalar("SELECT COUNT(*) FROM macro_targets", default=0)


# --------------------------------------------------------------------------- #
# Days and entries
# --------------------------------------------------------------------------- #
def day(when) -> dict | None:
    return db.read_one("SELECT * FROM v_food_days WHERE day = ?",
                       (food.as_date(when).isoformat(),))


def days(start=None, end=None, limit: int | None = None,
         newest_first: bool = True) -> list:
    where, params = [], []
    if start:
        where.append("day >= ?")
        params.append(food.as_date(start).isoformat())
    if end:
        where.append("day <= ?")
        params.append(food.as_date(end).isoformat())
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    order = "DESC" if newest_first else "ASC"
    tail = f" LIMIT {int(limit)}" if limit else ""
    return db.rows(f"SELECT * FROM v_food_days{clause} "
                   f"ORDER BY day {order}{tail}", params)


def entries(when) -> list:
    return db.rows(
        "SELECT * FROM v_food_entries WHERE day = ? ORDER BY "
        "CASE meal " + " ".join(
            f"WHEN '{meal}' THEN {index}"
            for index, meal in enumerate(config.MEALS))
        + " ELSE 99 END, position", (food.as_date(when).isoformat(),))


def day_sheet(when) -> list:
    """A day as the planner lays it out: one block per meal, with its totals."""
    rows = entries(when)
    by_meal: dict = {}
    for row in rows:
        by_meal.setdefault(row["meal"], []).append(row)
    order = [meal for meal in config.MEALS if meal in by_meal]
    order += [meal for meal in by_meal if meal not in config.MEALS]
    return [{"meal": meal, "entries": by_meal[meal],
             "totals": food.add_macros(by_meal[meal])} for meal in order]


def week(anchor, starts_on: int | None = None) -> dict:
    """Seven days from the week `anchor` falls in, whether recorded or not.

    Days with nothing against them come back as zeros rather than being left
    out, because a planning week is seven boxes and an empty Thursday is the
    thing you most need to see.
    """
    dates = food.week_days(anchor, starts_on)
    have = {row["day"]: row for row in days(dates[0], dates[-1])}
    out = []
    for date in dates:
        row = have.get(date.isoformat())
        if row is None:
            target = target_for(date) or {}
            row = {"day": date.isoformat(), "entries": 0, "note": None,
                   "target_name": target.get("name"), "chosen_target": None,
                   **{key: 0.0 for key in config.MACRO_KEYS},
                   **{f"target_{key}": target.get(key)
                      for key in config.MACRO_KEYS}}
        out.append({**row, "date": date, "weekday": food.day_name(date),
                    "planned": bool(row["entries"])})
    return {"start": dates[0], "end": dates[-1], "days": out,
            "label": food.week_label(anchor, starts_on),
            "totals": food.add_macros(out)}


def coverage() -> dict:
    row = db.read_one("""
        SELECT COUNT(*) AS days, MIN(day) AS first_day, MAX(day) AS last_day,
               (SELECT COUNT(*) FROM food_entries)       AS entries,
               (SELECT COUNT(*) FROM food_entries
                 WHERE food_id IS NOT NULL)              AS linked
        FROM food_days
    """) or {}
    return {"days": 0, "entries": 0, "linked": 0, "first_day": None,
            "last_day": None, **{k: v for k, v in row.items() if v is not None}}


def recent_foods(limit: int = 12) -> list:
    """What has actually been eaten lately - the useful end of the dropdown."""
    return db.rows("""
        SELECT f.*, COUNT(*) AS uses, MAX(e.day) AS last_used
        FROM food_entries e JOIN foods f ON f.id = e.food_id
        WHERE f.retired = 0
        GROUP BY f.id ORDER BY last_used DESC, uses DESC LIMIT ?
    """, (limit,))


def unmatched_names(limit: int = 50) -> list:
    """Diary lines that name nothing in the catalogue, commonest first."""
    return db.rows("""
        SELECT name, COUNT(*) AS uses, MIN(day) AS first_day, MAX(day) AS last_day
        FROM food_entries WHERE food_id IS NULL
        GROUP BY name COLLATE NOCASE ORDER BY uses DESC, name LIMIT ?
    """, (limit,))


GRAINS = {
    "daily": "day",
    "weekly": "date(day, '-6 days', 'weekday 1')",
    "monthly": "date(day, 'start of month')",
}


def by_period(grain: str = "monthly", start=None, end=None) -> list:
    """Average macros per day, grouped. For the analysis page.

    Days with nothing recorded are left out rather than averaged in as zeros -
    a month with four blank days did not average 1,400 calories.
    """
    expression = GRAINS.get(grain, GRAINS["daily"])
    where, params = ["entries > 0"], []
    if start:
        where.append("day >= ?")
        params.append(food.as_date(start).isoformat())
    if end:
        where.append("day <= ?")
        params.append(food.as_date(end).isoformat())

    averages = ",\n               ".join(
        f"ROUND(AVG({key}), 1) AS {key},\n               "
        f"ROUND(AVG(target_{key}), 1) AS target_{key}"
        for key in config.MACRO_KEYS)
    return db.rows(f"""
        SELECT {expression} AS period, COUNT(*) AS days,
               {averages}
        FROM v_food_days
        WHERE {' AND '.join(where)}
        GROUP BY period ORDER BY period
    """, params)


# --------------------------------------------------------------------------- #
# Turning what somebody typed into a diary line
# --------------------------------------------------------------------------- #
def quantity_for(catalogue, slot: Mapping, switched: bool = False):
    """How much of `catalogue` a filled-in row means, given what was typed.

    Straightforward when the name is the food: the number typed is the number
    meant, and the units beside it agree because picking a food fills them.

    `switched` is the case that needs care - an alert answered with "use that
    one instead", where the food has changed underneath the number. Somebody
    writing "Chikken breast, 1" means one chicken breast; the catalogue records
    that per 100 grams, so carrying the 1 across makes it one *gram*, or 1.37
    calories. That is not a thing anybody has eaten, and it is the sort of wrong
    that saves quietly and is never noticed again.

    So on a switch the number survives only if the units say it is the same kind
    of measure. Otherwise it becomes one portion of the food actually chosen,
    which is what "use that one instead" means.
    """
    quantity = slot.get("quantity") or None
    if catalogue is None or not quantity:
        return quantity
    typed = str(slot.get("units") or "").strip().casefold()
    theirs = str(catalogue.get("units") or "").strip().casefold()
    if typed and typed == theirs:
        return quantity            # the units agree; the number means what it says
    if typed:
        return catalogue.get("portion") or 1   # they disagree; the number does not
    # No units given. On an ordinary row that is somebody typing "150" into a box
    # next to a food they picked, and it means 150 of whatever that food is
    # measured in - keep it. On a switch it is a number that was about a
    # different food entirely, so it means nothing here.
    return (catalogue.get("portion") or 1) if switched else quantity


def resolve_entry(slot: Mapping, meal: str) -> dict:
    """One filled-in row as a diary line. Shared by both front-ends.

    Three cases:

      * the alert was answered with "use that one instead" - `resolve` holds a
        food id, and that food is what gets recorded
      * the name is already in the catalogue - the line links to it and the
        macros follow from the quantity
      * the name is new - the line carries the macros as typed, and `remember`
        asks save_day to file it under the List and Grouping chosen beside it
    """
    if slot.get("resolve"):
        chosen = food_row(int(slot["resolve"]))
        return {"meal": meal, "food_id": int(slot["resolve"]),
                "quantity": quantity_for(chosen, slot, switched=True)}

    name = str(slot.get("name") or "").strip()
    existing = food_by_name(name, slot.get("list")) or food_by_name(name)
    if existing is not None:
        return {"meal": meal, "food_id": existing["id"],
                "quantity": quantity_for(existing, slot)}

    return {
        "meal": meal,
        "name": name,
        "quantity": slot.get("quantity"),
        "units": slot.get("units"),
        "remember": True,
        "list": slot.get("list"),
        "grouping": slot.get("grouping"),
        **{key: slot.get(key) for key in config.MACRO_KEYS},
    }


def alerts_for(rows) -> dict:
    """Row index -> near misses, for the names about to become new foods.

    Only for a name that is genuinely new, and only once: a row carrying `seen`
    has already had its alert shown and answered, so saving again goes through.
    That is what makes this an alert rather than a wall - the whole reason free
    text exists is that sometimes the thing you ate really is new.

    `rows` are {"slot": ..., "meal": ..., "entry": ...} as the front-ends build
    them; the return is keyed on the slot's index so a complaint can be put back
    beside the box it came from.
    """
    catalogue = foods(include_retired=True)
    out = {}
    for row in rows:
        slot = row["slot"]
        if slot.get("seen") or slot.get("resolve"):
            continue
        if "remember" not in row["entry"]:
            continue
        matches = food.close_matches(slot["name"], catalogue)
        if matches:
            out[slot["index"]] = {
                "name": slot["name"],
                "meal": row["meal"],
                "matches": matches,
                "message": food.match_alert(slot["name"], matches),
            }
    return out


# --------------------------------------------------------------------------- #
# The macro calculator
# --------------------------------------------------------------------------- #
def resolve_components(rows) -> list:
    """Turn calculator lines into rows `food.combine()` can add up.

    A line is either a catalogue food and a quantity, or a name and four macros
    typed straight in - the workbook's Calculator sheet did both, because half of
    what goes into a recipe is on the Food sheet and the other half is read off a
    packet. Lines with neither a food nor any macros are dropped rather than
    counted as zero, so an empty row at the bottom of the form is not a
    component.

    Each row comes back carrying its own scaled macros as well as feeding the
    total, so the page can show its working - which is the point of a calculator
    over a sum.
    """
    out = []
    for raw in rows:
        quantity = raw.get("quantity")
        row = None
        if raw.get("food_id"):
            row = food_row(int(raw["food_id"]))
        if row is not None:
            amount = float(quantity if quantity not in (None, "") else
                           row["portion"] or 1)
            out.append({**row, "quantity": amount, "units": row["units"],
                        "from_catalogue": True,
                        **food.eaten(row, amount)})
            continue
        name = " ".join(str(raw.get("name") or "").split())
        macros = {key: float(raw.get(key) or 0.0) for key in config.MACRO_KEYS}
        if not name and not any(macros.values()):
            continue
        amount = float(quantity) if quantity not in (None, "") else 1.0
        out.append({"id": None, "name": name or "Untitled", "portion": None,
                    "quantity": amount, "units": raw.get("units") or None,
                    "from_catalogue": False,
                    **{key: value * amount for key, value in macros.items()}})
    return out


def calculate(rows, scale: float = 1.0) -> dict:
    """The calculator's answer: the resolved lines, their total, and the scale.

    `food.combine()` is not used here because these components already carry
    their scaled macros - resolve_components did the portion arithmetic, and
    doing it a second time would square it.
    """
    components = resolve_components(rows)
    scale = float(scale or 1.0)
    raw_total = food.add_macros(components)
    return {
        "components": components,
        "subtotal": raw_total,
        "scale": scale,
        "total": {key: round(value * scale, 2)
                  for key, value in raw_total.items()},
    }


def audit_trail(limit: int = 60) -> list:
    return db.rows(
        "SELECT ts, action, entity, entity_id, detail FROM audit_log "
        "WHERE entity IN ('foods','food_days','food_entries','macro_targets') "
        "ORDER BY id DESC LIMIT ?", (limit,))


MEALS = config.MEALS
MACRO_KEYS = config.MACRO_KEYS
