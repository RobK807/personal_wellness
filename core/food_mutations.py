"""Writes for the food section: the catalogue, targets, days and their entries.

Every public function is one transaction and one audit-log entry, the same shape
the other three sections have.

A day is saved wholesale, the way a session is in the workout tracker: its
entries are replaced by whatever the form holds rather than diffed. A day is a
handful of lines that get shuffled between meals, and matching them up by id
through a form that can hold thirty of them gets one wrong eventually.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Mapping, Sequence

import config
from core import db, food, food_queries as fq
from core.food import InvalidFood  # re-exported: callers catch one thing


# --------------------------------------------------------------------------- #
# The catalogue
# --------------------------------------------------------------------------- #
def save_food(values: Mapping, food_id: int | None = None) -> dict:
    """Add a food, or correct one."""
    parsed = food.parse_food(values)
    with db.transaction() as conn:
        clash = conn.execute(
            "SELECT id FROM foods WHERE list = ? AND name = ? COLLATE NOCASE "
            "AND id IS NOT ?",
            (parsed["list"], parsed["name"], food_id)).fetchone()
        if clash is not None:
            raise InvalidFood(
                f"'{parsed['name']}' is already in {parsed['list']}. The same "
                f"name in a different list is fine - the workbook has 'Mashed "
                f"potato' as both an Item and a Recipe - but not twice in one")

        columns = ("list", "name", "grouping", "portion", "units", "calories",
                   "carbs", "fat", "protein", "note")
        if food_id is None:
            marks = ", ".join("?" * len(columns))
            cursor = conn.execute(
                f"INSERT INTO foods ({', '.join(columns)}) VALUES ({marks})",
                tuple(parsed[name] for name in columns))
            food_id = cursor.lastrowid
            action = "add_food"
        else:
            assignments = ", ".join(f"{name} = ?" for name in columns)
            changed = conn.execute(
                f"UPDATE foods SET {assignments}, updated_at = datetime('now') "
                f"WHERE id = ?",
                (*(parsed[name] for name in columns), food_id)).rowcount
            if not changed:
                raise InvalidFood(f"No food with id {food_id}")
            action = "edit_food"

        db.log(conn, action, "foods", str(food_id),
               f"{parsed['list']}/{parsed['name']}: "
               f"{parsed['calories']:g} kcal per {parsed['portion']:g} "
               f"{parsed['units']}")
    return {"id": food_id, **parsed}


def retire_food(food_id: int, retired: bool = True) -> dict:
    """Take a food out of the dropdowns without touching the diary."""
    row = fq.food_row(food_id)
    if row is None:
        raise InvalidFood(f"No food with id {food_id}")
    with db.transaction() as conn:
        conn.execute("UPDATE foods SET retired = ?, "
                     "updated_at = datetime('now') WHERE id = ?",
                     (1 if retired else 0, food_id))
        db.log(conn, "retire_food" if retired else "restore_food", "foods",
               str(food_id), row["name"])
    return {**row, "retired": 1 if retired else 0}


def delete_food(food_id: int) -> dict:
    """Remove a food nothing in the diary uses. Refused otherwise."""
    row = fq.food_row(food_id)
    if row is None:
        raise InvalidFood(f"No food with id {food_id}")
    uses = fq.food_usage().get(food_id, 0)
    if uses:
        raise InvalidFood(
            f"'{row['name']}' is on {uses} diary line{'' if uses == 1 else 's'}, "
            f"so deleting it would cut them loose from the catalogue. Retire it "
            f"instead - it drops out of the dropdowns and the history is intact")
    with db.transaction() as conn:
        conn.execute("DELETE FROM foods WHERE id = ?", (food_id,))
        db.log(conn, "delete_food", "foods", str(food_id), row["name"])
    return row


def load_foods(rows: Sequence[Mapping], source: str = "xlsx") -> dict:
    """Bulk-load the catalogue. Updates by (list, name), never deletes.

    Re-runnable on purpose: the workbook is edited outside the dashboard and
    re-importing it should bring the changes over without disturbing anything
    added here since.
    """
    added = updated = unchanged = 0
    by_list: dict = {}
    with db.transaction() as conn:
        for raw in rows:
            parsed = food.parse_food(raw)
            existing = conn.execute(
                "SELECT * FROM foods WHERE list = ? AND name = ? COLLATE NOCASE",
                (parsed["list"], parsed["name"])).fetchone()
            by_list[parsed["list"]] = by_list.get(parsed["list"], 0) + 1
            if existing is None:
                conn.execute(
                    "INSERT INTO foods (list, name, grouping, portion, units, "
                    "calories, carbs, fat, protein, note) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (parsed["list"], parsed["name"], parsed["grouping"],
                     parsed["portion"], parsed["units"], parsed["calories"],
                     parsed["carbs"], parsed["fat"], parsed["protein"],
                     parsed.get("note")))
                added += 1
                continue
            same = all(
                abs(float(existing[key] or 0) - float(parsed[key] or 0)) < 1e-9
                for key in config.MACRO_KEYS + ["portion"]) \
                and (existing["units"] or "") == (parsed["units"] or "") \
                and (existing["grouping"] or "") == (parsed["grouping"] or "")
            if same:
                unchanged += 1
                continue
            conn.execute(
                "UPDATE foods SET grouping = ?, portion = ?, units = ?, "
                "calories = ?, carbs = ?, fat = ?, protein = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (parsed["grouping"], parsed["portion"], parsed["units"],
                 parsed["calories"], parsed["carbs"], parsed["fat"],
                 parsed["protein"], existing["id"]))
            updated += 1
        db.log(conn, "import", "foods", None,
               f"{source}: {added} added, {updated} updated, "
               f"{unchanged} unchanged")
    return {"added": added, "updated": updated, "unchanged": unchanged,
            "by_list": by_list}


def remember_food(values: Mapping) -> dict | None:
    """Add a food typed as free text to the catalogue, if it is not there.

    What turns "I ate a thing the list has never heard of" into a catalogue
    entry without a second trip to the Catalogue page. Called on the way through
    save_day(), so the food exists by the time the diary line pointing at it is
    written.

    Returns the row it made, or None if that food was already there - so the
    caller can say "and added it to the catalogue" only when it did.

    Deliberately *not* fuzzy. The near-miss check is an alert shown to somebody
    before they save; this runs after they have decided, and quietly folding
    "Chiken breast" into "Chicken breast" here would overrule that decision
    without saying so. Exact name in the same list, or it is a new food.
    """
    name = " ".join(str(values.get("name") or "").split())
    list_name = str(values.get("list") or "").strip().title()
    if not name or list_name not in config.FOOD_LISTS:
        return None

    existing = fq.food_by_name(name, list_name)
    if existing is not None:
        return None

    # The macros are for the portion that was eaten, so that is the portion the
    # catalogue row records - 600 kcal for 3 Slice, not a guess at what one
    # slice comes to. Eating one later divides by three and gets 200.
    quantity = food.parse_number(values.get("quantity"), "Quantity", "quantity",
                                 required=False)
    return save_food({
        "name": name,
        "list": list_name,
        "grouping": values.get("grouping"),
        "portion": quantity or 1,
        "units": values.get("units") or "Portion",
        "note": "Added from the diary",
        **{key: values.get(key) or 0 for key in config.MACRO_KEYS},
    })


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def save_settings(values: Mapping) -> dict:
    """Write the food section's Admin settings. Only the keys given.

    A blank value deletes the row rather than storing an empty string, so a
    setting can be handed back to config's default instead of being pinned to
    "nothing" - see the note on the food_settings table.
    """
    written, cleared = {}, []
    with db.transaction() as conn:
        for key, value in values.items():
            text = "" if value is None else str(value).strip()
            if text:
                conn.execute(
                    "INSERT INTO food_settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
                    "updated_at = datetime('now')", (key, text))
                written[key] = text
            else:
                conn.execute("DELETE FROM food_settings WHERE key = ?", (key,))
                cleared.append(key)
        db.log(conn, "settings", "food_settings", None,
               ", ".join(f"{key}={value}" for key, value in written.items())
               + (f"; cleared {', '.join(cleared)}" if cleared else ""))
    return {"written": written, "cleared": cleared}


# --------------------------------------------------------------------------- #
# Targets
# --------------------------------------------------------------------------- #
def save_target(values: Mapping, target_id: int | None = None) -> dict:
    """Record a target profile, or a new version of one from a date."""
    parsed = food.parse_target(values)
    starts = parsed["starts_on"].isoformat()
    with db.transaction() as conn:
        clash = conn.execute(
            "SELECT id FROM macro_targets WHERE name = ? AND starts_on = ? "
            "AND id IS NOT ?", (parsed["name"], starts, target_id)).fetchone()
        if clash is not None:
            raise InvalidFood(
                f"'{parsed['name']}' already has a version starting "
                f"{parsed['starts_on']:%d/%m/%Y}. Edit that one, or pick "
                f"another date")
        columns = ("name", "starts_on", *config.MACRO_KEYS, "note")
        row = {**parsed, "starts_on": starts}
        if target_id is None:
            marks = ", ".join("?" * len(columns))
            cursor = conn.execute(
                f"INSERT INTO macro_targets ({', '.join(columns)}) "
                f"VALUES ({marks})", tuple(row[name] for name in columns))
            target_id = cursor.lastrowid
            action = "add_target"
        else:
            assignments = ", ".join(f"{name} = ?" for name in columns)
            changed = conn.execute(
                f"UPDATE macro_targets SET {assignments} WHERE id = ?",
                (*(row[name] for name in columns), target_id)).rowcount
            if not changed:
                raise InvalidFood(f"No target with id {target_id}")
            action = "edit_target"
        db.log(conn, action, "macro_targets", str(target_id),
               f"{parsed['name']} from {parsed['starts_on']}: "
               f"{parsed['calories']:g} kcal")
    return {"id": target_id, **parsed}


def delete_target(target_id: int) -> None:
    with db.transaction() as conn:
        row = conn.execute("SELECT name, starts_on FROM macro_targets "
                           "WHERE id = ?", (target_id,)).fetchone()
        if row is None:
            raise InvalidFood(f"No target with id {target_id}")
        conn.execute("DELETE FROM macro_targets WHERE id = ?", (target_id,))
        db.log(conn, "delete_target", "macro_targets", str(target_id),
               f"{row[0]} from {row[1]}")


def seed_targets() -> list:
    """Fill macro_targets on first start, from config. Only ever once."""
    with db.transaction() as conn:
        if conn.execute("SELECT COUNT(*) FROM macro_targets").fetchone()[0]:
            return []
        added = []
        for name, calories, carbs, fat, protein in config.FOOD_TARGET_SEED:
            conn.execute(
                "INSERT INTO macro_targets (name, starts_on, calories, carbs, "
                "fat, protein, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, "2000-01-01", calories, carbs, fat, protein,
                 "Seeded from the workbook"))
            added.append(name)
        db.log(conn, "migrate", "macro_targets", None,
               f"seeded {', '.join(added)}")
    return added


# --------------------------------------------------------------------------- #
# Days
# --------------------------------------------------------------------------- #
def save_day(when, entries: Sequence[Mapping], target_name: str | None = None,
             note: str | None = None, source: str = "manual") -> dict:
    """Record or correct one day, entries and all.

    Parsed in full before anything is written, so a bad line in the middle does
    not leave half a day saved.
    """
    day = food.as_date(when)
    prepared = []
    counters: dict = {}
    added: list = []
    for raw in entries:
        row = dict(raw)
        if row.get("food_id") and not row.get("_parsed"):
            catalogue = fq.food_row(int(row["food_id"]))
            if catalogue is None:
                raise InvalidFood(f"No food with id {row['food_id']}")
            row = food.parse_entry(row, catalogue)
        elif not row.get("_parsed"):
            # A free-text line asking to be remembered. Done here rather than
            # inside the transaction below because save_food opens its own, and
            # done *before* parse_entry so the line ends up linked to the food
            # it just created rather than dangling as text beside it.
            made = remember_food(row) if row.get("remember") else None
            if made is not None:
                added.append(made)
                row = food.parse_entry({**row, "food_id": made["id"]},
                                       fq.food_row(made["id"]))
            else:
                existing = (fq.food_by_name(row.get("name"), row.get("list"))
                            if row.get("remember") else None)
                row = food.parse_entry(
                    {**row, "food_id": existing["id"] if existing else
                     row.get("food_id")})
        counters[row["meal"]] = counters.get(row["meal"], 0) + 1
        row["position"] = counters[row["meal"]]
        prepared.append(row)

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO food_days (day, target_name, note) VALUES (?, ?, ?) "
            "ON CONFLICT (day) DO UPDATE SET target_name = excluded.target_name,"
            " note = excluded.note, updated_at = datetime('now')",
            (day.isoformat(), target_name or None,
             (str(note or "")).strip() or None))
        conn.execute("DELETE FROM food_entries WHERE day = ?",
                     (day.isoformat(),))
        _write_entries(conn, day, prepared, source)
        db.log(conn, "save_day", "food_days", day.isoformat(),
               f"{len(prepared)} entries, "
               f"{sum(row['calories'] for row in prepared):.0f} kcal"
               + (f"; added {len(added)} to the catalogue" if added else ""))
    return {"day": day, "entries": len(prepared), "added": added}


def _write_entries(conn: sqlite3.Connection, day: dt.date,
                   rows: Sequence[Mapping], source: str) -> None:
    conn.executemany(
        "INSERT INTO food_entries (day, meal, position, food_id, name, "
        "quantity, units, calories, carbs, fat, protein, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(day.isoformat(), row["meal"], row["position"], row.get("food_id"),
          row["name"], row.get("quantity"), row.get("units"),
          row["calories"], row["carbs"], row["fat"], row["protein"], source)
         for row in rows])


def delete_day(when) -> dict:
    day = food.as_date(when)
    row = fq.day(day)
    if row is None:
        raise InvalidFood(f"Nothing recorded on {day:%d/%m/%Y}")
    with db.transaction() as conn:
        conn.execute("DELETE FROM food_days WHERE day = ?", (day.isoformat(),))
        db.log(conn, "delete_day", "food_days", day.isoformat(),
               f"{row['entries']} entries")
    return row


def copy_day(source_day, target_day) -> dict:
    """Use one day as the template for another - how a week gets planned."""
    source = fq.day(source_day)
    if source is None:
        raise InvalidFood(f"Nothing recorded on "
                          f"{food.as_date(source_day):%d/%m/%Y}")
    rows = [{**row, "_parsed": True} for row in fq.entries(source_day)]
    return save_day(target_day, rows, target_name=source["target_name"])


def fill_week(source_day, anchor, starts_on: int | None = None,
              overwrite: bool = False) -> dict:
    """Copy one day across the week `anchor` falls in - how a week gets planned.

    The workbook's Planner was a single day's setup that got pasted into seven
    columns, and this is that paste. Days that already have something against
    them are left alone unless `overwrite` says otherwise, because the usual
    shape of the job is planning Tuesday to Sunday around a Monday that has
    already happened.

    The source day is skipped whether or not it falls inside the week, so
    planning the week from its own Monday does not rewrite that Monday.
    """
    source = fq.day(source_day)
    if source is None:
        raise InvalidFood(f"Nothing recorded on "
                          f"{food.as_date(source_day):%d/%m/%Y} to copy")
    origin = food.as_date(source_day)
    rows = [{**row, "_parsed": True} for row in fq.entries(origin)]
    if not rows:
        raise InvalidFood(f"{origin:%d/%m/%Y} has no entries to copy")

    copied, skipped = [], []
    for date in food.week_days(anchor, starts_on):
        if date == origin:
            continue
        existing = fq.day(date)
        if existing and existing["entries"] and not overwrite:
            skipped.append(date)
            continue
        save_day(date, rows, target_name=source["target_name"], source="planner")
        copied.append(date)
    return {"source": origin, "copied": copied, "skipped": skipped,
            "entries": len(rows)}


def plan_week(anchor, rows: Sequence[Mapping], starts_on: int | None = None,
              overwrite: bool = False) -> dict:
    """Write a whole week at once, from one line per meal per day.

    The bulk planner behind the Week page's tables. `rows` carry a `day_offset`
    (0-6 from the start of the week) and everything a diary line needs; a row
    with no food and no name is skipped, which is what lets the form be a fixed
    7x4 grid and the week be however much of it you filled in.

    **Days that already have entries are left alone.** Not refused - filled, and
    named in the result - because the shape of the job is planning Tuesday to
    Sunday around a Monday that has already happened, and a button that gave up
    on the whole week because of Monday would be no use on any day but Sunday
    night. Nothing here can overwrite: `overwrite` exists for the caller that
    asks for it explicitly and is off by default.
    """
    dates = food.week_days(anchor, starts_on)
    by_day: dict = {}
    for raw in rows:
        try:
            offset = int(raw.get("day_offset"))
        except (TypeError, ValueError):
            continue
        if not 0 <= offset < len(dates):
            continue
        if not (str(raw.get("name") or "").strip() or raw.get("food_id")):
            continue
        by_day.setdefault(dates[offset], []).append(raw)

    written, skipped, added = [], [], []
    for date in dates:
        lines = by_day.get(date)
        if not lines:
            continue
        existing = fq.day(date)
        if existing and existing["entries"] and not overwrite:
            skipped.append(date)
            continue
        # Whatever is on the day already is kept and the new lines appended, so
        # this fills an empty day and tops up a started one rather than
        # replacing either.
        keep = [{**row, "_parsed": True} for row in fq.entries(date)]
        result = save_day(date, keep + list(lines),
                          target_name=(existing or {}).get("chosen_target"),
                          note=(existing or {}).get("note"), source="planner")
        added += result.get("added", [])
        written.append({"day": date, "entries": result["entries"],
                        "added": len(lines)})

    return {"start": dates[0], "end": dates[-1], "days": written,
            "skipped": skipped, "added": added,
            "lines": sum(len(lines) for lines in by_day.values())}


def load_entries(rows: Sequence[Mapping], replace: bool = False,
                 source: str = "csv") -> dict:
    """Bulk-load diary lines, grouped into days. For the CSV round trip.

    `replace` clears the days the file covers before writing, which is what
    makes a corrected export safe to load twice.
    """
    by_day: dict = {}
    for row in rows:
        by_day.setdefault(food.as_date(row["day"]), []).append(row)

    linked = free_text = 0
    with db.transaction() as conn:
        for day, entries in sorted(by_day.items()):
            if replace:
                conn.execute("DELETE FROM food_entries WHERE day = ?",
                             (day.isoformat(),))
            conn.execute(
                "INSERT INTO food_days (day) VALUES (?) "
                "ON CONFLICT (day) DO UPDATE SET updated_at = datetime('now')",
                (day.isoformat(),))
            counters: dict = {}
            prepared = []
            for row in sorted(entries,
                              key=lambda item: (item.get("position") or 0)):
                counters[row["meal"]] = counters.get(row["meal"], 0) + 1
                prepared.append({**row, "position": counters[row["meal"]]})
                linked += bool(row.get("food_id"))
                free_text += not row.get("food_id")
            _write_entries(conn, day, prepared, source)
        db.log(conn, "import", "food_entries", None,
               f"{source}: {len(by_day)} days, {len(rows)} entries, "
               f"{linked} linked to the catalogue")
    return {"days": len(by_day), "entries": len(rows), "linked": linked,
            "free_text": free_text}
