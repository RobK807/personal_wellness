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
        ("interval_split_s", "INTEGER"),
    ],
}


def init_db(db_path: Path | None = None) -> None:
    """Create tables and views if they do not already exist. Safe to re-run."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _add_missing_columns(conn)
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
