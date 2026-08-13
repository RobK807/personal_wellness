"""Read-side, shared by both front-ends.

Returns plain lists of dicts, never DataFrames - see the note in core/db.py.
The Streamlit side converts to pandas in views/frames.py; the Flask templates
iterate the dicts directly.

Between them these functions cover every derived sheet in the workbook:

    Summary                          -> series("daily")
    Weekly Results                   -> series("weekly")
    Monthly average                  -> series("monthly")
    Weekly changes - Daily rolling   -> rolling_change(7)
    Weekly changes - Weekly average  -> weekly_average_change()
    Monthly average change           -> changes("monthly")
    (new) day-on-day, week-on-week   -> changes("daily"), changes("weekly")

The dataset is small - one row a day for six years is ~2,300 rows - so nothing
is cached: correctness after a write matters more than shaving milliseconds.
"""
from __future__ import annotations

import datetime as dt

import config
from core import db, metrics

# grain -> (view, the column holding the period start)
VIEWS = {
    "daily":   ("v_daily", "day"),
    "weekly":  ("v_weekly", "period"),
    "monthly": ("v_monthly", "period"),
}

GRAINS = tuple(VIEWS)

# Every metric column the views expose, entered and derived alike.
VALUE_COLUMNS = ", ".join(config.ALL_KEYS)

# The offsets the overview compares against. 7 and 28 rather than "last week"
# and "last month" so the comparison is like-for-like: 28 days is four whole
# weeks, which a calendar month is not.
COMPARISONS = [("day", 1), ("week", 7), ("4 weeks", 28)]


def _select(grain: str) -> tuple:
    if grain not in VIEWS:
        raise ValueError(f"Unknown grain {grain!r} - expected one of {GRAINS}")
    return VIEWS[grain]


def _window(sql: str, params: list, column: str,
            start=None, end=None) -> tuple:
    if start:
        sql += f" AND {column} >= ?"
        params.append(metrics.as_date(start).isoformat())
    if end:
        sql += f" AND {column} <= ?"
        params.append(metrics.as_date(end).isoformat())
    return sql, params


# --------------------------------------------------------------------------- #
# Series
# --------------------------------------------------------------------------- #
def series(grain: str = "daily", start=None, end=None, limit: int | None = None) -> list:
    """The averaged values for every period, oldest first.

    Each row has `period` (the ISO date the period starts), one key per metric,
    `days` (how many daily rows it covers) and `estimated_days`.
    """
    view, column = _select(grain)
    if grain == "daily":
        sql = (f"SELECT day AS period, {VALUE_COLUMNS}, 1 AS days, "
               f"estimated AS estimated_days FROM {view} WHERE 1 = 1")
    else:
        sql = (f"SELECT period, {VALUE_COLUMNS}, days, estimated_days "
               f"FROM {view} WHERE 1 = 1")

    sql, params = _window(sql, [], "period" if grain != "daily" else "day",
                          start, end)

    if limit:
        # Newest N, but handed back oldest first so charts read left to right.
        sql += " ORDER BY period DESC LIMIT ?"
        params.append(limit)
        return list(reversed(db.rows(sql, params)))

    return db.rows(sql + " ORDER BY period", params)


def latest(grain: str = "daily") -> dict | None:
    rows = series(grain, limit=1)
    return rows[0] if rows else None


def day(when) -> dict | None:
    """One day's averaged figures."""
    iso = metrics.as_date(when).isoformat()
    return db.read_one(
        f"SELECT day AS period, {VALUE_COLUMNS}, readings, estimated "
        "FROM v_daily WHERE day = ?", (iso,))


def readings_for(when) -> list:
    """The raw weigh-ins behind a day - what the input form pre-fills from."""
    iso = metrics.as_date(when).isoformat()
    return db.rows(
        f"SELECT slot, {', '.join(config.METRIC_KEYS)}, estimated, note, updated_at "
        "FROM readings WHERE day = ? ORDER BY slot", (iso,))


def recent_days(limit: int = 30, offset: int = 0, include_estimated: bool = True) -> list:
    """The daily table, newest first - what the input page's recent list shows."""
    sql = (f"SELECT day AS period, {VALUE_COLUMNS}, readings, estimated "
           "FROM v_daily WHERE 1 = 1")
    params: list = []
    if not include_estimated:
        sql += " AND estimated = 0"
    sql += " ORDER BY day DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return db.rows(sql, params)


def recent_periods(grain: str = "daily", limit: int = 40, offset: int = 0,
                   include_estimated: bool = True) -> list:
    """The Data page's table: newest first, one row per period.

    Weekly and monthly rows are the same averages the charts are drawn from -
    v_weekly and v_monthly - so the table and the chart can never tell
    different stories about the same week.

    `include_estimated` applies at daily grain only. A weekly row is an average
    over its days, and dropping the interpolated ones would quietly change what
    the average means rather than filter the list; `estimated_days` says how
    much of it was filled in instead.
    """
    if grain == "daily":
        return recent_days(limit, offset, include_estimated)

    view, _ = _select(grain)
    return db.rows(
        f"SELECT period, {VALUE_COLUMNS}, days, estimated_days "
        f"FROM {view} ORDER BY period DESC LIMIT ? OFFSET ?", [limit, offset])


# --------------------------------------------------------------------------- #
# Changes
# --------------------------------------------------------------------------- #
def changes(grain: str = "daily", start=None, end=None, limit: int | None = None) -> list:
    """Period-on-period differences: this period's average minus the previous.

    At monthly grain this is the Monthly average change sheet. The row is
    labelled with the *later* period, as the sheet did.
    """
    rows = series(grain, start=start, end=end)
    out = []
    for previous, current in zip(rows, rows[1:]):
        entry = {"period": current["period"], "previous": previous["period"],
                 "days": current["days"], "estimated_days": current["estimated_days"]}
        for key in config.ALL_KEYS:
            entry[key] = _diff(current[key], previous[key])
        out.append(entry)
    return out[-limit:] if limit else out


def rolling_change(window: int = 7, start=None, end=None,
                   limit: int | None = None) -> list:
    """Each day against the same day `window` days earlier.

    With window = 7 this is the "Weekly changes - Daily rolling" sheet: it was
    Summary!B10 - Summary!B3, seven rows apart on a sheet with one row a day.
    Matching on the date rather than the row offset means a gap in the data
    produces no row instead of a wrong one.
    """
    sql = f"""
        SELECT later.day AS period, earlier.day AS previous,
               {', '.join(f'later.{k} - earlier.{k} AS {k}' for k in config.ALL_KEYS)},
               later.estimated + earlier.estimated AS estimated_days
        FROM v_daily later
        JOIN v_daily earlier ON earlier.day = date(later.day, ?)
        WHERE 1 = 1
    """
    params: list = [f"-{int(window)} days"]
    sql, params = _window(sql, params, "later.day", start, end)
    sql += " ORDER BY later.day"
    rows = db.rows(sql, params)
    return rows[-limit:] if limit else rows


def weekly_average_change(start=None, end=None, limit: int | None = None) -> list:
    """The "Weekly changes - Weekly average" sheet.

    The mean of the rolling seven-day change across the seven days of each
    week - a smoothed version of "how much did this week move".
    """
    columns = ", ".join(
        f"ROUND(AVG(later.{k} - earlier.{k}), 6) AS {k}" for k in config.ALL_KEYS)
    sql = f"""
        SELECT date(later.day, '-6 days', 'weekday 1') AS period,
               {columns},
               COUNT(*) AS days
        FROM v_daily later
        JOIN v_daily earlier ON earlier.day = date(later.day, '-7 days')
        WHERE 1 = 1
    """
    sql, params = _window(sql, [], "later.day", start, end)
    sql += " GROUP BY period ORDER BY period"
    rows = db.rows(sql, params)
    return rows[-limit:] if limit else rows


def _diff(current, previous):
    if current is None or previous is None:
        return None
    return round(float(current) - float(previous), 6)


# --------------------------------------------------------------------------- #
# Headline numbers
# --------------------------------------------------------------------------- #
def headline() -> dict:
    """Latest day, plus how each metric has moved over the standard windows.

    `changes` maps a window label to {metric: difference}. A window with no
    matching day - the very first week of tracking, say - maps to None.
    """
    current = latest("daily")
    if current is None:
        return {"latest": None, "changes": {}, "coverage": coverage()}

    today = metrics.as_date(current["period"])
    windows = {}
    for label, days in COMPARISONS:
        past = day(today - dt.timedelta(days=days))
        windows[label] = (
            None if past is None
            else {key: _diff(current[key], past[key]) for key in config.ALL_KEYS}
        )

    first = db.scalar("SELECT MIN(day) FROM v_daily")
    start_row = day(first) if first else None
    windows["all time"] = (
        None if start_row is None or start_row["period"] == current["period"]
        else {key: _diff(current[key], start_row[key]) for key in config.ALL_KEYS}
    )

    return {"latest": current, "changes": windows, "coverage": coverage()}


def coverage() -> dict:
    """How much data there is, and how much of it is interpolated."""
    row = db.read_one(
        """
        SELECT MIN(day)              AS first_day,
               MAX(day)              AS last_day,
               COUNT(*)              AS days,
               SUM(estimated)        AS estimated_days
        FROM v_daily
        """
    ) or {}
    first, last = row.get("first_day"), row.get("last_day")
    span = 0
    if first and last:
        span = (metrics.as_date(last) - metrics.as_date(first)).days + 1
    return {
        "first_day": first,
        "last_day": last,
        "days": row.get("days") or 0,
        "estimated_days": row.get("estimated_days") or 0,
        "span_days": span,
        "missing_days": max(span - (row.get("days") or 0), 0),
        "readings": db.scalar("SELECT COUNT(*) FROM readings WHERE estimated = 0",
                              default=0),
    }


def gaps(minimum: int = 1) -> list:
    """Runs of days with no data at all, newest first.

    Normally empty: a gap is back-filled as soon as the next reading is entered.
    Anything left here is a run longer than config.MAX_BACKFILL_DAYS, or a
    stretch that has not been closed by a later reading yet.
    """
    days = db.rows("SELECT day FROM v_daily ORDER BY day")
    found = []
    for previous, current in zip(days, days[1:]):
        start = metrics.as_date(previous["day"])
        end = metrics.as_date(current["day"])
        missing = (end - start).days - 1
        if missing >= minimum:
            found.append({
                "after": previous["day"],
                "before": current["day"],
                "days": missing,
                "too_long": missing > config.MAX_BACKFILL_DAYS,
            })
    return list(reversed(found))


def audit_trail(limit: int = 100) -> list:
    return db.rows(
        "SELECT ts, action, entity, entity_id, detail FROM audit_log "
        "ORDER BY id DESC LIMIT ?", (limit,))


# --------------------------------------------------------------------------- #
# Ranges, for the period selector on the charts
# --------------------------------------------------------------------------- #
# label -> days back from the latest reading. None means everything.
RANGES = [("30d", 30), ("90d", 90), ("6m", 183), ("1y", 365), ("2y", 730),
          ("All", None)]
DEFAULT_RANGE = "90d"


def range_start(label: str):
    """The ISO date a named range starts at, relative to the latest reading."""
    days = dict(RANGES).get(label, dict(RANGES)[DEFAULT_RANGE])
    if days is None:
        return None
    last = db.scalar("SELECT MAX(day) FROM v_daily")
    if last is None:
        return None
    return (metrics.as_date(last) - dt.timedelta(days=days - 1)).isoformat()
