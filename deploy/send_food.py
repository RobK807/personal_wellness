"""Send the food catalogue and diary from this PC's database to the NAS's.

    python deploy/send_food.py                     # what is here, what is there
    python deploy/send_food.py --catalogue         # the foods and the targets
    python deploy/send_food.py --diary             # the days and their entries
    python deploy/send_food.py --all               # both
    python deploy/send_food.py --all --dry-run
    python deploy/send_food.py --diary --from 2026-01-01 --to 2026-08-08

Why this exists
---------------
The same problem `send_plan.py` solves, for the same reason. `core.food_import`
reads a workbook, the workbooks are deliberately never pushed to the NAS, and
openpyxl opening one needs more memory than a DS218play has free - so the
catalogue and the two and a half years of diary behind it are always imported on
a desktop and carried across afterwards.

Pushing the whole database instead is not an option once the dashboard is in
use: the NAS copy is the live one, and it already holds weigh-ins and runs
entered from a phone that this PC has never seen. Overwriting it would destroy
them.

What it touches
---------------
**Only the five food tables.** The weigh-ins, the runs, the run option lists and
every workout table on the NAS are never read from the source and never written.

Ids are **not** carried across. The catalogue exists independently on both sides,
so foods are matched by `(list, name)` - the pair the table is unique on, because
the workbook has "Mashed potato" as both an Item and a Recipe - and a diary line
is re-linked to whatever the target's id for that food turns out to be. Anything
the target has not got is added. That is what makes this safe to run twice, and
safe against a catalogue that has been edited on the NAS since.

Two rules about overwriting, and they differ on purpose:

  * a **food or a target** already there is left exactly as it is unless
    `--overwrite-foods` is given. The NAS copy is the live one, and a portion
    size corrected on a phone should not be undone by a push from a stale
    desktop.
  * a **day** already there is replaced, because a day is saved wholesale
    everywhere else in this section and half a day merged from two sources is
    not a day anybody ate. `--skip-existing-days` keeps the target's instead.

The risk, stated plainly
------------------------
This writes SQLite over SMB, which config.py warns against for good reason.
Three things make it acceptable and none is optional: the dashboard must be
stopped, the target is backed up first, and every day's four macro totals are
read back afterwards and compared against the source. If any of that fails,
restore the backup whose path it printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

import config  # noqa: E402
from core import db  # noqa: E402

# The connection, path and safety helpers are send_plan's. They are not
# duplicated here: the URI quirk, the samefile() check and the DELETE-journal
# backup were each worked out against the live NAS once, and a second copy is a
# second thing to get wrong.
from deploy.send_plan import (NAS_HOST, NAS_PORT, TARGET_DB,  # noqa: E402
                              TransferError, app_is_running, backup, connect,
                              is_the_nas)

FOOD_TABLES = ("foods", "macro_targets", "food_days", "food_entries",
               "food_settings")


# --------------------------------------------------------------------------- #
# Reading it out of the source
# --------------------------------------------------------------------------- #
def read_food(conn, start=None, end=None, diary: bool = True) -> dict:
    """The catalogue, the targets and the diary, as plain dicts.

    Diary lines carry the *name and list* of the food they point at rather than
    its id, because the id means nothing on the other side - see the note at the
    top about matching by (list, name).
    """
    where, params = [], []
    if start:
        where.append("d.day >= ?")
        params.append(str(start))
    if end:
        where.append("d.day <= ?")
        params.append(str(end))
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    data = {
        "foods": _rows(conn, "SELECT * FROM foods ORDER BY list, name"),
        "targets": _rows(conn, "SELECT * FROM macro_targets "
                               "ORDER BY name, starts_on"),
        "settings": _rows(conn, "SELECT key, value FROM food_settings"),
        "days": [],
        "entries": [],
    }
    if not diary:
        return data

    data["days"] = _rows(conn, f"SELECT d.* FROM food_days d{clause} "
                               f"ORDER BY d.day", params)
    data["entries"] = _rows(conn, f"""
        SELECT e.*, f.name AS food_name, f.list AS food_list
        FROM food_entries e
        LEFT JOIN foods f ON f.id = e.food_id
        JOIN food_days d ON d.day = e.day
        {clause}
        ORDER BY e.day, e.meal, e.position
    """, params)
    return data


def _rows(conn, sql: str, params=()) -> list:
    return [dict(row) for row in conn.execute(sql, params)]


def totals(conn, start=None, end=None) -> list:
    """Every day's four macro totals, as a comparable list. The proof.

    Read out of v_food_days rather than recomputed, so what is compared is what
    the dashboard will actually show. Rounded to two places because that is the
    precision the view itself stores at, and comparing raw floats across two
    SQLite builds would fail on the last bit for no reason anybody cares about.
    """
    where, params = [], []
    if start:
        where.append("day >= ?")
        params.append(str(start))
    if end:
        where.append("day <= ?")
        params.append(str(end))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return [(row["day"], row["entries"],
             round(row["calories"], 2), round(row["carbs"], 2),
             round(row["fat"], 2), round(row["protein"], 2))
            for row in conn.execute(
                f"SELECT day, entries, calories, carbs, fat, protein "
                f"FROM v_food_days{clause} ORDER BY day", params)]


# --------------------------------------------------------------------------- #
# Writing it into the target
# --------------------------------------------------------------------------- #
def map_foods(target, rows: list, overwrite: bool) -> tuple:
    """Source food id -> target food id, matching on (list, name).

    A food the target already has is left alone unless `overwrite` says
    otherwise: the NAS copy is the live one, and a portion corrected on a phone
    should survive a push from a desktop that has not seen the correction.
    """
    mapping, added, updated = {}, [], []
    for row in rows:
        found = target.execute(
            "SELECT id FROM foods WHERE list = ? AND name = ? COLLATE NOCASE",
            (row["list"], row["name"])).fetchone()
        if found is not None:
            mapping[row["id"]] = found["id"]
            if overwrite:
                target.execute(
                    "UPDATE foods SET grouping = ?, portion = ?, units = ?, "
                    "calories = ?, carbs = ?, fat = ?, protein = ?, note = ?, "
                    "updated_at = datetime('now') WHERE id = ?",
                    (row["grouping"], row["portion"], row["units"],
                     row["calories"], row["carbs"], row["fat"], row["protein"],
                     row["note"], found["id"]))
                updated.append(row["name"])
            continue
        cursor = target.execute(
            "INSERT INTO foods (list, name, grouping, portion, units, calories,"
            " carbs, fat, protein, note, retired) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["list"], row["name"], row["grouping"], row["portion"],
             row["units"], row["calories"], row["carbs"], row["fat"],
             row["protein"], row["note"], row["retired"]))
        mapping[row["id"]] = cursor.lastrowid
        added.append(row["name"])
    return mapping, added, updated


def write_targets(target, rows: list, overwrite: bool) -> tuple:
    """Target profiles, keyed on (name, starts_on) the way the table is."""
    added, updated = [], []
    for row in rows:
        found = target.execute(
            "SELECT id FROM macro_targets WHERE name = ? AND starts_on = ?",
            (row["name"], row["starts_on"])).fetchone()
        if found is not None:
            if overwrite:
                target.execute(
                    "UPDATE macro_targets SET calories = ?, carbs = ?, fat = ?,"
                    " protein = ?, note = ? WHERE id = ?",
                    (row["calories"], row["carbs"], row["fat"], row["protein"],
                     row["note"], found["id"]))
                updated.append(f"{row['name']} from {row['starts_on']}")
            continue
        target.execute(
            "INSERT INTO macro_targets (name, starts_on, calories, carbs, fat, "
            "protein, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row["name"], row["starts_on"], row["calories"], row["carbs"],
             row["fat"], row["protein"], row["note"]))
        added.append(f"{row['name']} from {row['starts_on']}")
    return added, updated


def write_settings(target, rows: list, overwrite: bool) -> list:
    """The Admin page's preferences - where a new line's List and Grouping start.

    Same rule as a food: one the target already has is left alone. These are set
    on whichever front-end is actually used, which is the NAS, so a push from a
    desktop should seed them on a database that has none and then keep quiet.
    """
    written = []
    for row in rows:
        found = target.execute("SELECT key FROM food_settings WHERE key = ?",
                               (row["key"],)).fetchone()
        if found is not None and not overwrite:
            continue
        target.execute(
            "INSERT INTO food_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
            "updated_at = datetime('now')", (row["key"], row["value"]))
        written.append(row["key"])
    return written


def write_food(target, data: dict, overwrite_foods: bool,
               skip_existing_days: bool) -> dict:
    """Insert the catalogue, the targets and the diary. Caller owns the
    transaction.

    Order is forced: foods first, because a diary line needs the target's id for
    the food it names; then targets, which nothing depends on; then days and
    their entries.
    """
    food_map, added, updated = map_foods(target, data["foods"], overwrite_foods)
    targets_added, targets_updated = write_targets(target, data["targets"],
                                                   overwrite_foods)
    settings_added = write_settings(target, data.get("settings", []),
                                    overwrite_foods)

    days_written, days_skipped, unlinked = 0, 0, []
    by_day: dict = {}
    for row in data["entries"]:
        by_day.setdefault(row["day"], []).append(row)

    for day in data["days"]:
        existing = target.execute(
            "SELECT day FROM food_days WHERE day = ?", (day["day"],)).fetchone()
        if existing is not None and skip_existing_days:
            days_skipped += 1
            continue

        target.execute(
            "INSERT INTO food_days (day, target_name, note) VALUES (?, ?, ?) "
            "ON CONFLICT (day) DO UPDATE SET target_name = excluded.target_name,"
            " note = excluded.note, updated_at = datetime('now')",
            (day["day"], day["target_name"], day["note"]))
        # A day is replaced wholesale, the same as everywhere else in this
        # section: half a day merged from two sources is not a day anybody ate.
        target.execute("DELETE FROM food_entries WHERE day = ?", (day["day"],))

        for row in by_day.get(day["day"], []):
            food_id = food_map.get(row["food_id"]) if row["food_id"] else None
            if row["food_id"] and food_id is None:
                # Cannot happen from a consistent source, but a line silently
                # losing its link is exactly the kind of thing worth saying out
                # loud rather than discovering in a total six months later.
                unlinked.append(row["name"])
            target.execute(
                "INSERT INTO food_entries (day, meal, position, food_id, name, "
                "quantity, units, calories, carbs, fat, protein, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row["day"], row["meal"], row["position"], food_id, row["name"],
                 row["quantity"], row["units"], row["calories"], row["carbs"],
                 row["fat"], row["protein"], row["source"]))
        days_written += 1

    return {
        "foods_added": added, "foods_updated": updated,
        "targets_added": targets_added, "targets_updated": targets_updated,
        "settings": settings_added,
        "days": days_written, "days_skipped": days_skipped,
        "entries": sum(len(by_day.get(day["day"], [])) for day in data["days"]),
        "unlinked": unlinked,
    }


# --------------------------------------------------------------------------- #
# Driving it
# --------------------------------------------------------------------------- #
def send(target_path: Path, diary: bool, start, end, overwrite_foods: bool,
         skip_existing_days: bool, dry_run: bool,
         do_backup: bool = True) -> dict:
    source = connect(config.DB_PATH, read_only=True)
    try:
        data = read_food(source, start, end, diary=diary)
        expected = totals(source, start, end) if diary else []
    finally:
        source.close()

    print(f"  catalogue : {len(data['foods'])} foods, "
          f"{len(data['targets'])} target versions, "
          f"{len(data.get('settings', []))} settings")
    if diary:
        print(f"  diary     : {len(data['days'])} days, "
              f"{len(data['entries'])} entries"
              + (f", {data['days'][0]['day']} to {data['days'][-1]['day']}"
                 if data["days"] else ""))
    else:
        print("  diary     : not included (pass --diary or --all)")

    if dry_run:
        print("\n--dry-run: nothing written.")
        return {}

    if do_backup:
        saved = backup(target_path)
        print(f"\nBacked up the target -> {saved} "
              f"({saved.stat().st_size / 1024 / 1024:.1f} MB)")

    # Brings the target up to the current schema, so this works whether or not
    # the NAS has been restarted since the code was pushed. On a NAS that has
    # never had the food tables this is what creates them.
    db.init_db(target_path)

    target = connect(target_path)
    try:
        target.execute("BEGIN IMMEDIATE")
        result = write_food(target, data, overwrite_foods, skip_existing_days)
        db.log(target, "send_food", "foods", None,
               f"from {config.DB_PATH}: {len(result['foods_added'])} foods "
               f"added, {result['days']} days, {result['entries']} entries")
        target.execute("COMMIT")
    except Exception:
        target.execute("ROLLBACK")
        target.close()
        raise
    target.close()

    # --- the proof --------------------------------------------------------
    if diary and not skip_existing_days:
        print("\nReading it back")
        target = connect(target_path, read_only=True)
        try:
            got = totals(target, start, end)
            result["verified"] = got == expected
            if result["verified"]:
                print(f"  all {len(got)} days match, every macro total included")
            else:
                print(f"  MISMATCH - {len(expected)} days here, {len(got)} there")
                for mine, theirs in zip(expected, got):
                    if mine != theirs:
                        print(f"     first difference:\n"
                              f"       here  {mine}\n       there {theirs}")
                        break
        finally:
            target.close()
    else:
        # Nothing to compare against: the target's days are its own.
        result["verified"] = True
    return result


def _date(text: str):
    return dt.date.fromisoformat(text) if text else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send the food catalogue and diary to the NAS")
    parser.add_argument("--catalogue", action="store_true",
                        help="the foods and the target profiles")
    parser.add_argument("--diary", action="store_true",
                        help="the days and their entries (implies --catalogue)")
    parser.add_argument("--all", action="store_true", help="both")
    parser.add_argument("--target", type=Path, default=TARGET_DB)
    parser.add_argument("--from", dest="start", type=_date, default=None,
                        help="earliest day to send, YYYY-MM-DD")
    parser.add_argument("--to", dest="end", type=_date, default=None,
                        help="latest day to send, YYYY-MM-DD")
    parser.add_argument("--overwrite-foods", action="store_true",
                        help="update foods and targets the target already has")
    parser.add_argument("--skip-existing-days", action="store_true",
                        help="keep the target's version of a day it already has")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true",
                        help="skip the snapshot. Do not.")
    args = parser.parse_args()

    if not config.DB_PATH.exists():
        print(f"No local database at {config.DB_PATH}", file=sys.stderr)
        return 1
    if not args.target.parent.exists():
        print(f"Cannot reach {args.target.parent} - is the NAS online, and is "
              f"the share mapped?", file=sys.stderr)
        return 1

    local = connect(config.DB_PATH, read_only=True)
    try:
        here = local.execute(
            "SELECT (SELECT COUNT(*) FROM foods)        AS foods, "
            "       (SELECT COUNT(*) FROM food_days)    AS days, "
            "       (SELECT COUNT(*) FROM food_entries) AS entries").fetchone()
    finally:
        local.close()

    if not here["foods"]:
        print("There is no food in the local database to send.")
        print("  python -m core.food_import --catalogue")
        return 1

    if not (args.catalogue or args.diary or args.all):
        print(f"Here  ({config.DB_PATH}):")
        print(f"  {here['foods']} foods, {here['days']} days, "
              f"{here['entries']} diary entries")
        there = connect(args.target, read_only=True)
        try:
            counts = there.execute(
                "SELECT (SELECT COUNT(*) FROM foods)        AS foods, "
                "       (SELECT COUNT(*) FROM food_days)    AS days, "
                "       (SELECT COUNT(*) FROM food_entries) AS entries"
            ).fetchone()
            print(f"\nThere ({args.target}):")
            print(f"  {counts['foods']} foods, {counts['days']} days, "
                  f"{counts['entries']} diary entries")
        except Exception:
            print(f"\nThere ({args.target}):\n  no food tables yet")
        finally:
            there.close()
        print("\nSend it:\n  python deploy/send_food.py --all")
        return 0

    diary = args.diary or args.all
    remote = is_the_nas(args.target)
    # A dry run reads the source, prints what it would do and returns before it
    # opens the target for writing, so there is no reason to make somebody take
    # the dashboard down to find out what a transfer would move.
    if remote and not args.dry_run and app_is_running():
        print(f"The dashboard is answering on {NAS_HOST}:{NAS_PORT}.\n\n"
              f"This writes SQLite over SMB, which is only safe with exactly "
              f"one writer. Stop it first:\n"
              f"  sh /volume1/dashboards/personal_wellness/deploy/stop.sh",
              file=sys.stderr)
        return 1

    print(f"Sending from {config.DB_PATH}\n          -> {args.target}\n")
    try:
        result = send(args.target, diary, args.start, args.end,
                      args.overwrite_foods, args.skip_existing_days,
                      args.dry_run, do_backup=not args.no_backup)
    except TransferError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    if not result:
        return 0

    print()
    print(f"Catalogue: {len(result['foods_added'])} foods added, "
          f"{len(result['foods_updated'])} updated, "
          f"{len(result['targets_added'])} target versions added, "
          f"{len(result['settings'])} settings seeded")
    if diary:
        print(f"Diary    : {result['days']} days written, "
              f"{result['entries']} entries"
              + (f", {result['days_skipped']} days left alone"
                 if result["days_skipped"] else ""))
    if result["unlinked"]:
        print(f"  {len(result['unlinked'])} entries lost their link to the "
              f"catalogue: {', '.join(sorted(set(result['unlinked']))[:5])}")
    if not result["verified"]:
        print("\nSomething did not arrive intact. Restore the backup above.",
              file=sys.stderr)
        return 1

    if remote:
        print(f"\nStart the dashboard again:\n"
              f"  sh /volume1/dashboards/personal_wellness/deploy/run.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
