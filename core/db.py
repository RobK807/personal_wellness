"""SQLite plumbing: connection handling, schema creation, small helpers.

Deliberately free of pandas, Streamlit and any other heavy import. Both
front-ends share this module, and the Flask one runs on a NAS with about
150 MB of RAM to spare - pandas alone costs 85 MB, so it must not be pulled in
by the data layer. Everything here returns plain Python types; the Streamlit
side wraps them in DataFrames itself (see views/frames.py).

This is the same module as the CD dashboard's, pointed at a different database.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence, Union

import config

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Union rather than `A | B`: this is a plain assignment, so it is evaluated at
# import time, and PEP 604 unions need Python 3.10. The NAS runs 3.9. The same
# does not apply to annotations, which `from __future__ import annotations`
# defers - see py39_check.py.
Params = Union[Sequence[Any], dict]
Row = dict


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with the settings this app relies on.

    WAL keeps a phone reading a chart from blocking the morning weigh-in being
    saved, and the busy timeout absorbs the brief contention when two tabs
    submit at once.
    """
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


# Columns added to a table after it was first created. SQLite has no
# "ADD COLUMN IF NOT EXISTS", and CREATE TABLE IF NOT EXISTS does nothing at all
# to a table that already exists - so a column added to schema.sql would appear
# on a fresh database and never on the one already running on the NAS.
#
# Each entry is the column definition exactly as schema.sql declares it, minus
# any CHECK: SQLite will not accept a CHECK in ADD COLUMN. The constraint is
# enforced on new databases and by the parsing in core/runs.py on every write,
# which is where a bad value would actually come from.
MIGRATIONS = {
    "runs": [
        ("interval_type", "TEXT"),
        ("interval_count", "INTEGER"),
        ("interval_distance_m", "REAL"),
        ("interval_time_s", "INTEGER"),
        ("interval_pace_s", "INTEGER"),
    ],
}


def init_db(db_path: Path | None = None) -> None:
    """Create tables and views if they do not already exist. Safe to re-run."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _add_missing_columns(conn)
        _convert_interval_splits(conn)
        _seed_run_options(conn)
        _seed_exercises(conn)
        _seed_targets(conn)
    finally:
        conn.close()


def _add_missing_columns(conn: sqlite3.Connection) -> list:
    """Bring an existing database up to the current schema. Returns what it did.

    Only ever adds nullable columns, so it cannot fail on existing rows and
    cannot lose anything: every row already there simply reads NULL for the new
    column, which for these is exactly right - a run recorded before intervals
    were tracked has no interval data, and saying so is the honest answer.
    """
    applied = []
    for table, columns in MIGRATIONS.items():
        existing = {row["name"] for row in
                    conn.execute(f"PRAGMA table_info({table})")}
        if not existing:  # the table itself is not there yet
            continue
        for name, declaration in columns:
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
            applied.append(f"{table}.{name}")
    if applied:
        log(conn, "migrate", "schema", None, f"added {', '.join(applied)}")
    return applied


def _convert_interval_splits(conn: sqlite3.Connection) -> int:
    """Carry the short-lived `interval_split_s` column over to its replacements.

    The first cut of interval tracking held a time per rep and worked the pace
    out from it. That only works for a session set by distance, so the pace is
    now entered and the time per rep is kept only for sessions set by time -
    see core/schema.sql. This moves what was already recorded across:

        set by distance   the pace, which is the split over the rep distance
        set by time       the time per rep, unchanged; no pace to recover

    Both directions are arithmetic on that row alone, so nothing is guessed.
    Runs entered as 1k reps come out with the same number in the pace column
    they had in the split column, which is right - over a kilometre the two
    are the same figure.

    Idempotent: it only touches rows whose replacements are still empty, so a
    second start finds nothing to do. Returns how many rows it converted.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
    if "interval_split_s" not in columns:
        return 0

    converted = conn.execute("""
        UPDATE runs
           SET interval_pace_s = CASE
                   WHEN interval_type = 'distance' AND interval_distance_m > 0
                   THEN CAST(ROUND(interval_split_s
                                   / (interval_distance_m / 1000.0)) AS INTEGER)
               END,
               interval_time_s = CASE
                   WHEN interval_type = 'time' THEN interval_split_s
               END
         WHERE interval_split_s IS NOT NULL
           AND interval_pace_s IS NULL
           AND interval_time_s IS NULL
    """).rowcount

    # The old column has no meaning now. Dropping it needs SQLite 3.35, and an
    # older one simply keeps an unused column - untidy, never wrong, and not
    # worth rebuilding the table over.
    dropped = False
    try:
        conn.execute("ALTER TABLE runs DROP COLUMN interval_split_s")
        dropped = True
    except sqlite3.OperationalError:
        pass

    if converted or dropped:
        log(conn, "migrate", "schema", None,
            f"interval_split_s: {converted} row(s) converted"
            + (", column dropped" if dropped else ", column left in place"))
    return converted


def _seed_run_options(conn: sqlite3.Connection) -> list:
    """Fill the dropdown lists on first start. Never touches them again.

    Two sources, in this order: the lists in config.py, which are the
    vocabulary the spreadsheet was written in, and then anything the runs
    already in the database use that those lists do not mention. The second
    half is what matters on an existing deployment - the imported history
    contains an 'Unclassified' run, from a row the source sheet could not
    classify, and a dropdown that omitted it would make that run uneditable.

    Only ever runs against an empty table, so editing the lists on the Admin
    page is not undone by the next restart, and removing an option does not
    bring it back.
    """
    if conn.execute("SELECT COUNT(*) FROM run_options").fetchone()[0]:
        return []

    seeded = []
    for kind, defaults in (("run_type", config.RUN_TYPES),
                           ("effort_type", config.EFFORT_TYPES)):
        # Busiest first among the extras, so a type used by forty runs is not
        # left below one used by a single run.
        used = [row[0] for row in conn.execute(
            f"SELECT {kind} FROM runs WHERE {kind} IS NOT NULL AND {kind} <> '' "
            f"GROUP BY {kind} ORDER BY COUNT(*) DESC, {kind}")]
        ordered, seen = [], set()
        for value in list(defaults) + used:
            if value.casefold() not in seen:
                ordered.append(value)
                seen.add(value.casefold())
        conn.executemany(
            "INSERT INTO run_options (kind, value, position) VALUES (?, ?, ?)",
            [(kind, value, index) for index, value in enumerate(ordered)])
        seeded.append(f"{kind}: {len(ordered)}")

    log(conn, "migrate", "run_options", None, "seeded " + ", ".join(seeded))
    return seeded


def _seed_exercises(conn: sqlite3.Connection) -> list:
    """Fill the exercise catalogue on first start, from config.

    Same rule as the dropdown lists above: only ever against an empty table, so
    editing the catalogue in the app is not undone by the next restart and a
    retired movement does not come back. The seed is the vocabulary of the gym
    workbook - see config.WORKOUT_EXERCISES.
    """
    if conn.execute("SELECT COUNT(*) FROM exercises").fetchone()[0]:
        return []
    added = []
    for position, (name, per_side, per_dumbbell, bodyweight) in enumerate(
            config.WORKOUT_EXERCISES, start=1):
        conn.execute(
            "INSERT INTO exercises (name, reps_mode, weight_mode, "
            "is_bodyweight, position) VALUES (?, ?, ?, ?, ?)",
            (name, "per_side" if per_side else "total",
             "per_dumbbell" if per_dumbbell else "total",
             1 if bodyweight else 0, position))
        added.append(name)
    log(conn, "migrate", "exercises", None, f"seeded {len(added)} exercises")
    return added


def _seed_targets(conn: sqlite3.Connection) -> list:
    """Fill macro_targets on first start, from config.FOOD_TARGET_SEED.

    Same rule as the other seeds: only ever against an empty table, so a target
    edited in the app is not undone by the next restart. Dated from 2000 so that
    every imported day - the diary starts in 2024 - has something in force to be
    compared against.
    """
    if conn.execute("SELECT COUNT(*) FROM macro_targets").fetchone()[0]:
        return []
    added = []
    for name, calories, carbs, fat, protein in config.FOOD_TARGET_SEED:
        conn.execute(
            "INSERT INTO macro_targets (name, starts_on, calories, carbs, fat, "
            "protein, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, "2000-01-01", calories, carbs, fat, protein,
             "Seeded from the workbook"))
        added.append(name)
    log(conn, "migrate", "macro_targets", None, f"seeded {', '.join(added)}")
    return added


@contextmanager
def transaction(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Run a block of writes atomically."""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def rows(sql: str, params: Params = ()) -> list:
    """Run a SELECT and return a list of plain dicts."""
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def read_one(sql: str, params: Params = ()) -> Row | None:
    conn = connect()
    try:
        row = conn.execute(sql, params).fetchone()
        return None if row is None else dict(row)
    finally:
        conn.close()


def scalar(sql: str, params: Params = (), default: Any = None) -> Any:
    conn = connect()
    try:
        row = conn.execute(sql, params).fetchone()
    finally:
        conn.close()
    if row is None or row[0] is None:
        return default
    return row[0]


def log(conn: sqlite3.Connection, action: str, entity: str, entity_id: str | None,
        detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log (action, entity, entity_id, detail) VALUES (?, ?, ?, ?)",
        (action, entity, entity_id, detail),
    )


def db_exists() -> bool:
    return Path(config.DB_PATH).exists()
