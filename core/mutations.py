"""Write-side, shared by both front-ends.

The workbook had no macros - the work was manual: type the two weigh-ins into
the Data sheet, and when you had missed a few days, paste the readings either
side into the Adjustments sheet and copy its output back. `save_reading()` and
`refresh_estimates()` are those two steps, with the second happening on its own.

How back-filling works
----------------------
Estimated days are derived data, not entries. Every one of them sits between
two *real* readings, and is recomputed from scratch whenever either of those
readings changes - so entering the weigh-in you forgot to record on Tuesday
does not leave Wednesday's estimate hanging off the old straight line. That is
also why they are deleted and rebuilt rather than patched: the flag is what
makes them disposable.

A back-fill writes one reading (slot 1) rather than two. The workbook filled
both weigh-in columns, but interpolating a value and then calling it two
independent measurements overstates what is known: there is one estimate for
that day, and the `estimated` flag says so. The daily average - which is what
every chart and every difference is built on - is identical either way.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Mapping

import config
from core import db, metrics
from core.metrics import InvalidReading  # re-exported: callers catch one thing


class BackfillTooLong(Exception):
    """Raised when a gap is longer than config.MAX_BACKFILL_DAYS."""


READING_COLUMNS = ", ".join(config.METRIC_KEYS)
READING_PLACEHOLDERS = ", ".join("?" * len(config.METRIC_KEYS))


# --------------------------------------------------------------------------- #
# Entering a weigh-in
# --------------------------------------------------------------------------- #
def save_reading(day, slot: int, values: Mapping[str, object],
                 note: str = "") -> dict:
    """Record (or correct) one weigh-in, then rebuild any estimates it affects.

    Returns a summary dict: the parsed values, how many days were back-filled,
    and any gap that was too long to fill.
    """
    when = metrics.as_date(day)
    if slot not in config.SLOTS:
        raise InvalidReading(f"Weigh-in must be {' or '.join(map(str, config.SLOTS))}")
    if when > dt.date.today():
        raise InvalidReading(f"{when:%d/%m/%Y} is in the future")

    parsed = metrics.parse_reading(values)

    with db.transaction() as conn:
        _upsert(conn, when, slot, parsed, estimated=False, note=note)
        db.log(conn, "save_reading", "readings", f"{when.isoformat()}#{slot}",
               ", ".join(f"{k}={v:g}" for k, v in parsed.items()))
        filled, skipped = refresh_estimates(conn, when)

    return {"day": when, "slot": slot, "values": parsed,
            "filled": filled, "skipped_gap": skipped}


def save_day(day, readings: Mapping[int, Mapping[str, object]],
             note: str = "") -> dict:
    """Record both weigh-ins for a day in one transaction.

    `readings` maps slot -> values; a slot left out is left alone, so this can
    be used to add the evening weigh-in without disturbing the morning one.
    """
    when = metrics.as_date(day)
    if when > dt.date.today():
        raise InvalidReading(f"{when:%d/%m/%Y} is in the future")
    if not readings:
        raise InvalidReading("Nothing to save")

    parsed = {}
    for slot, values in readings.items():
        if slot not in config.SLOTS:
            raise InvalidReading(f"Unknown weigh-in number {slot}")
        parsed[slot] = metrics.parse_reading(values)

    with db.transaction() as conn:
        for slot, values in sorted(parsed.items()):
            _upsert(conn, when, slot, values, estimated=False, note=note)
            db.log(conn, "save_reading", "readings", f"{when.isoformat()}#{slot}",
                   ", ".join(f"{k}={v:g}" for k, v in values.items()))
        filled, skipped = refresh_estimates(conn, when)

    return {"day": when, "slots": sorted(parsed), "values": parsed,
            "filled": filled, "skipped_gap": skipped}


def delete_reading(day, slot: int) -> None:
    """Remove one weigh-in. If it was the day's last, the day becomes a gap and
    is re-estimated from the readings either side."""
    when = metrics.as_date(day)
    with db.transaction() as conn:
        conn.execute("DELETE FROM readings WHERE day = ? AND slot = ?",
                     (when.isoformat(), slot))
        db.log(conn, "delete_reading", "readings", f"{when.isoformat()}#{slot}", "")
        _reestimate_neighbourhood(conn, when)


def delete_day(day) -> None:
    """Remove both weigh-ins for a day."""
    when = metrics.as_date(day)
    with db.transaction() as conn:
        conn.execute("DELETE FROM readings WHERE day = ?", (when.isoformat(),))
        db.log(conn, "delete_day", "readings", when.isoformat(), "")
        _reestimate_neighbourhood(conn, when)


def _upsert(conn: sqlite3.Connection, when: dt.date, slot: int,
            values: Mapping[str, float], estimated: bool, note: str = "") -> None:
    conn.execute(
        f"""
        INSERT INTO readings (day, slot, {READING_COLUMNS}, estimated, note)
        VALUES (?, ?, {READING_PLACEHOLDERS}, ?, ?)
        ON CONFLICT (day, slot) DO UPDATE SET
            {', '.join(f'{k} = excluded.{k}' for k in config.METRIC_KEYS)},
            estimated  = excluded.estimated,
            note       = excluded.note,
            updated_at = datetime('now')
        """,
        (when.isoformat(), slot,
         *[values[key] for key in config.METRIC_KEYS],
         int(estimated), note.strip() or None),
    )


# --------------------------------------------------------------------------- #
# Back-filling missed days
# --------------------------------------------------------------------------- #
def refresh_estimates(conn: sqlite3.Connection, day) -> tuple:
    """Rebuild the estimated runs on either side of a real reading.

    Returns (days filled, list of gaps that were too long to fill).
    """
    when = metrics.as_date(day)
    anchor = _real_day(conn, when.isoformat())
    if anchor is None:  # the day itself is not a real reading - nothing anchored
        return 0, []

    filled, skipped = 0, []
    before = _previous_real(conn, when.isoformat())
    after = _next_real(conn, when.isoformat())
    for left, right in ((before, anchor), (anchor, after)):
        if left is None or right is None:
            continue
        try:
            filled += _fill_between(conn, left, right)
        except BackfillTooLong as exc:
            skipped.append(str(exc))
    return filled, skipped


def _reestimate_neighbourhood(conn: sqlite3.Connection, day) -> tuple:
    """After a deletion, re-link the real readings either side of `day`."""
    when = metrics.as_date(day)
    before = _previous_real(conn, when.isoformat())
    after = _next_real(conn, when.isoformat())
    # The day itself may still hold a real reading (one slot deleted of two).
    anchor = _real_day(conn, when.isoformat())
    if anchor is not None:
        return refresh_estimates(conn, when)
    if before is None or after is None:
        # Nothing on one side to interpolate towards: drop any estimates that
        # were leaning on the deleted reading rather than leave them stale.
        _clear_estimates_between(conn, before, after)
        return 0, []
    try:
        return _fill_between(conn, before, after), []
    except BackfillTooLong as exc:
        return 0, [str(exc)]


def backfill_all(rebuild: bool = True) -> dict:
    """Fill every gap in the whole series.

    With `rebuild`, existing estimates are thrown away and recomputed - the
    Admin page's repair button, and the right thing after a run of corrections.

    Without it, only days holding nothing at all are filled, and estimates
    already in the database are left exactly as they are. That is what the
    importer wants: the workbook's own back-fills came across as data, and
    recomputing them would leave the dashboard disagreeing with the
    spreadsheet it was built from over values neither of them can check.
    """
    with db.transaction() as conn:
        if rebuild:
            conn.execute("DELETE FROM readings WHERE estimated = 1")
        anchors = [dict(r) for r in conn.execute(
            f"SELECT day, {READING_COLUMNS} FROM v_daily "
            "WHERE estimated = 0 ORDER BY day")]

        filled, skipped = 0, []
        for left, right in zip(anchors, anchors[1:]):
            try:
                filled += _fill_between(conn, left, right, preserve=not rebuild)
            except BackfillTooLong as exc:
                skipped.append(str(exc))

        detail = f"{filled} day(s) filled"
        if skipped:
            detail += f"; {len(skipped)} gap(s) too long"
        db.log(conn, "backfill", "readings", None, detail)

    return {"filled": filled, "skipped": skipped, "anchors": len(anchors)}


def _fill_between(conn: sqlite3.Connection, left: Mapping, right: Mapping,
                  preserve: bool = False) -> int:
    """Write the interpolated days strictly between two real readings.

    `preserve` leaves any run that already holds something - estimates from an
    earlier tool, or a part-entered day - untouched rather than replacing it.
    """
    start = metrics.as_date(left["day"])
    end = metrics.as_date(right["day"])
    gap = (end - start).days - 1
    if gap < 1:
        return 0

    existing = conn.execute(
        "SELECT COUNT(*) FROM readings WHERE day > ? AND day < ?",
        (start.isoformat(), end.isoformat()),
    ).fetchone()[0]
    if preserve and existing:
        return 0

    if gap > config.MAX_BACKFILL_DAYS:
        raise BackfillTooLong(
            f"{gap} days between {start:%d/%m/%Y} and {end:%d/%m/%Y} - "
            f"longer than the {config.MAX_BACKFILL_DAYS}-day back-fill limit, "
            "so it has been left as a gap"
        )

    # Everything between two consecutive real readings is either empty or a
    # stale estimate, so clearing first is safe and keeps this idempotent.
    conn.execute("DELETE FROM readings WHERE day > ? AND day < ?",
                 (start.isoformat(), end.isoformat()))

    rows = metrics.interpolate(left, right, gap)
    for offset, values in enumerate(rows, start=1):
        _upsert(conn, start + dt.timedelta(days=offset), 1, values,
                estimated=True,
                note=f"Interpolated between {start:%d/%m/%Y} and {end:%d/%m/%Y}")
    db.log(conn, "backfill", "readings",
           f"{start.isoformat()}..{end.isoformat()}", f"{gap} day(s) interpolated")
    return gap


def _clear_estimates_between(conn: sqlite3.Connection, left, right) -> None:
    """Drop estimates that no longer sit between two real readings."""
    low = metrics.as_date(left["day"]).isoformat() if left else "0000-01-01"
    high = metrics.as_date(right["day"]).isoformat() if right else "9999-12-31"
    conn.execute(
        "DELETE FROM readings WHERE estimated = 1 AND day > ? AND day < ?",
        (low, high),
    )


# --------------------------------------------------------------------------- #
# Anchor lookups
# --------------------------------------------------------------------------- #
_ANCHOR_SQL = f"SELECT day, {READING_COLUMNS} FROM v_daily WHERE estimated = 0"


def _real_day(conn: sqlite3.Connection, iso: str):
    row = conn.execute(_ANCHOR_SQL + " AND day = ?", (iso,)).fetchone()
    return None if row is None else dict(row)


def _previous_real(conn: sqlite3.Connection, iso: str):
    row = conn.execute(_ANCHOR_SQL + " AND day < ? ORDER BY day DESC LIMIT 1",
                       (iso,)).fetchone()
    return None if row is None else dict(row)


def _next_real(conn: sqlite3.Connection, iso: str):
    row = conn.execute(_ANCHOR_SQL + " AND day > ? ORDER BY day LIMIT 1",
                       (iso,)).fetchone()
    return None if row is None else dict(row)
