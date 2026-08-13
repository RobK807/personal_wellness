"""Write-side for the run tracker, shared by both front-ends.

A run and its breakdowns are saved together, in one transaction, because they
are one thing: a 10 km run whose splits failed to write is not a partial record
of a run, it is a wrong one. The same goes for editing - the ladder is replaced
wholesale rather than patched, so a split deleted from the form is deleted from
the database and cannot survive as a leftover.

`save_run()` is an upsert on (day, distance_km, duration_s), which is what the
workbook offers as a run's identity - Final_data has no activity id. Entering
the same run twice therefore corrects it rather than duplicating it, and
re-running the importer is safe.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Mapping, Sequence

from core import db, runs
from core.runs import InvalidRun  # re-exported: callers catch one thing


def save_run(values: Mapping[str, object],
             breakdowns: Mapping[str, object] | None = None,
             run_id: int | None = None, source: str = "manual") -> dict:
    """Record or correct one run, with its ladder of best efforts.

    `values` is the form: day, distance_km, duration_s, run_type, effort_type
    and an optional note. `breakdowns` maps a breakdown label to the time typed
    against it; blanks are skipped.

    `run_id` names the run being edited. Without it this is a new run, and an
    existing run with the same date, distance and time is updated in place.
    """
    parsed = runs.parse_run(values)
    ladder = runs.parse_breakdowns(breakdowns or {}, parsed["distance_km"],
                                   parsed["duration_s"])

    with db.transaction() as conn:
        if run_id is None:
            run_id = _upsert(conn, parsed, source)
            action = "save_run"
        else:
            _update(conn, run_id, parsed, source)
            action = "edit_run"
        _replace_bests(conn, run_id, ladder)
        db.log(conn, action, "runs", str(run_id),
               f"{parsed['day'].isoformat()} {runs.describe(parsed)}, "
               f"{parsed['run_type']}/{parsed['effort_type']}, "
               f"{len(ladder)} split(s)")

    return {"id": run_id, **parsed, "breakdowns": ladder}


def delete_run(run_id: int) -> bool:
    """Remove a run and, by cascade, its splits."""
    with db.transaction() as conn:
        row = conn.execute("SELECT day, distance_km, duration_s FROM runs "
                           "WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        db.log(conn, "delete_run", "runs", str(run_id),
               f"{row['day']} {row['distance_km']} km in "
               f"{runs.fmt_duration(row['duration_s'])}")
    return True


# --------------------------------------------------------------------------- #
# The SQL
# --------------------------------------------------------------------------- #
def _upsert(conn: sqlite3.Connection, parsed: Mapping, source: str) -> int:
    """Insert, or update the run that already has this identity. Returns its id.

    ON CONFLICT ... DO UPDATE rather than INSERT OR REPLACE: the latter deletes
    the conflicting row first, which would take the run's splits with it
    through the cascade and hand back a new id - so an edit would silently drop
    anything the caller was not replacing.
    """
    conn.execute(
        """
        INSERT INTO runs (day, distance_km, duration_s, run_type, effort_type,
                          note, source, interval_type, interval_count,
                          interval_distance_m, interval_split_s)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (day, distance_km, duration_s) DO UPDATE SET
            run_type    = excluded.run_type,
            effort_type = excluded.effort_type,
            note        = COALESCE(excluded.note, runs.note),
            source      = excluded.source,
            interval_type       = excluded.interval_type,
            interval_count      = excluded.interval_count,
            interval_distance_m = excluded.interval_distance_m,
            interval_split_s    = excluded.interval_split_s,
            updated_at  = datetime('now')
        """,
        (parsed["day"].isoformat(), parsed["distance_km"], parsed["duration_s"],
         parsed["run_type"], parsed["effort_type"], parsed["note"], source,
         parsed["interval_type"], parsed["interval_count"],
         parsed["interval_distance_m"], parsed["interval_split_s"]),
    )
    return conn.execute(
        "SELECT id FROM runs WHERE day = ? AND distance_km = ? AND duration_s = ?",
        (parsed["day"].isoformat(), parsed["distance_km"],
         parsed["duration_s"])).fetchone()[0]


def _update(conn: sqlite3.Connection, run_id: int, parsed: Mapping,
            source: str) -> None:
    """Change an existing run, including its identity.

    Correcting a mistyped distance moves the run onto an identity another run
    may already hold, and the unique index would raise sqlite3.IntegrityError
    with a message about an index name. Checking first turns that into a
    sentence about two runs.
    """
    clash = conn.execute(
        "SELECT id FROM runs WHERE day = ? AND distance_km = ? "
        "AND duration_s = ? AND id <> ?",
        (parsed["day"].isoformat(), parsed["distance_km"],
         parsed["duration_s"], run_id)).fetchone()
    if clash is not None:
        raise InvalidRun(
            f"Another run on {parsed['day']:%d/%m/%Y} already covers "
            f"{runs.fmt_distance(parsed['distance_km'])} km in "
            f"{runs.fmt_duration(parsed['duration_s'])}")

    changed = conn.execute(
        """
        UPDATE runs SET day = ?, distance_km = ?, duration_s = ?, run_type = ?,
                        effort_type = ?, note = ?, source = ?,
                        interval_type = ?, interval_count = ?,
                        interval_distance_m = ?, interval_split_s = ?,
                        updated_at = datetime('now')
        WHERE id = ?
        """,
        (parsed["day"].isoformat(), parsed["distance_km"], parsed["duration_s"],
         parsed["run_type"], parsed["effort_type"], parsed["note"], source,
         parsed["interval_type"], parsed["interval_count"],
         parsed["interval_distance_m"], parsed["interval_split_s"], run_id),
    ).rowcount
    if not changed:
        raise InvalidRun(f"No run with id {run_id}")


def _replace_bests(conn: sqlite3.Connection, run_id: int,
                   ladder: Sequence[Mapping]) -> None:
    """Swap a run's whole ladder for the one supplied."""
    conn.execute("DELETE FROM run_bests WHERE run_id = ?", (run_id,))
    conn.executemany(
        "INSERT INTO run_bests (run_id, breakdown, ordinal, km, seconds) "
        "VALUES (?, ?, ?, ?, ?)",
        [(run_id, row["breakdown"], row["ordinal"], row["km"], row["seconds"])
         for row in ladder],
    )


# --------------------------------------------------------------------------- #
# Bulk loading - what the importer uses
# --------------------------------------------------------------------------- #
def load_run(conn: sqlite3.Connection, day: dt.date, distance_km: float,
             duration_s: int, run_type: str, effort_type: str,
             ladder: Sequence[Mapping], note: str | None = None,
             source: str = "strava") -> int:
    """Write one already-parsed run inside a transaction the caller owns.

    Deliberately does not go through `runs.parse_breakdowns()`. The importer's
    job is to reproduce the workbook, and the workbook contains seventeen
    splits longer than the run they sit inside - refusing them would leave the
    dashboard quietly holding less than the sheet it was built from. They are
    loaded, and reported by core.run_queries.anomalies() instead.
    """
    conn.execute(
        """
        INSERT INTO runs (day, distance_km, duration_s, run_type, effort_type,
                          note, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (day, distance_km, duration_s) DO UPDATE SET
            run_type    = excluded.run_type,
            effort_type = excluded.effort_type,
            note        = COALESCE(excluded.note, runs.note),
            source      = excluded.source,
            updated_at  = datetime('now')
        """,
        (day.isoformat(), distance_km, duration_s, run_type, effort_type,
         note, source),
    )
    run_id = conn.execute(
        "SELECT id FROM runs WHERE day = ? AND distance_km = ? AND duration_s = ?",
        (day.isoformat(), distance_km, duration_s)).fetchone()[0]
    _replace_bests(conn, run_id, ladder)
    return run_id
