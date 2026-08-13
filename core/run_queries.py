"""Read-side for the run tracker, shared by both front-ends.

Returns plain lists of dicts, never DataFrames - see the note in core/db.py.
The Streamlit side converts to pandas in views/run_frames.py; the Flask
templates iterate the dicts directly.

Three things the brief asked for, and one that falls out of them:

    the runs themselves          runs(), run(), recent()
    performance by type          by_run_type(), by_effort_type(),
                                 by_breakdown(), by_month()
    the top five per distance    records()
    what there is                coverage(), anomalies()

Paces are seconds per kilometre and are never rounded here. Rounding is a
display decision and it happens once, in core.runs.fmt_pace().
"""
from __future__ import annotations

import datetime as dt

import config
from core import db, runs
from core.metrics import as_date

# label -> days back from the most recent run. None means everything.
RANGES = [("90d", 90), ("6m", 183), ("1y", 365), ("2y", 730), ("All", None)]
DEFAULT_RANGE = "1y"

# How the analysis page can be split. Column in v_run_bests -> heading.
SPLITS = {
    "run_type":    "Run type",
    "effort_type": "Effort type",
}

GRAINS = ("monthly", "weekly")


def _filters(run_type: str | None = None, effort_type: str | None = None,
             start=None, end=None) -> tuple:
    """The WHERE fragment and parameters shared by every query in this module.

    v_runs and v_run_bests deliberately expose the same four column names, so
    one builder serves both and a filter cannot mean one thing on the analysis
    page and another on the records page.
    """
    clauses, params = [], []
    if run_type:
        clauses.append("run_type = ?")
        params.append(run_type)
    if effort_type:
        clauses.append("effort_type = ?")
        params.append(effort_type)
    if start:
        clauses.append("day >= ?")
        params.append(as_date(start).isoformat())
    if end:
        clauses.append("day <= ?")
        params.append(as_date(end).isoformat())
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


# --------------------------------------------------------------------------- #
# The runs themselves
# --------------------------------------------------------------------------- #
def runs_list(start=None, end=None, run_type: str | None = None,
              effort_type: str | None = None, limit: int | None = None,
              offset: int = 0, newest_first: bool = True) -> list:
    """Runs matching the filters, newest first unless asked otherwise."""
    where, params = _filters(run_type, effort_type, start, end)
    sql = f"SELECT * FROM v_runs WHERE 1 = 1{where} ORDER BY day " \
          f"{'DESC' if newest_first else 'ASC'}, id {'DESC' if newest_first else 'ASC'}"
    if limit:
        sql += " LIMIT ? OFFSET ?"
        params = params + [limit, offset]
    return db.rows(sql, params)


def run(run_id: int) -> dict | None:
    return db.read_one("SELECT * FROM v_runs WHERE id = ?", (run_id,))


def bests_for(run_id: int) -> list:
    """A run's ladder of best efforts, shortest first.

    Suspect splits are included here and flagged, because this is the run's own
    page: the figure that is wrong is part of what was recorded about the run,
    and hiding it is how it never gets fixed.
    """
    return db.rows(
        "SELECT breakdown, ordinal, km, seconds, pace_s, suspect "
        "FROM v_run_bests WHERE run_id = ? ORDER BY ordinal", (run_id,))


def runs_on(day) -> list:
    """Every run recorded on a date - three of them, on 07/06/2026."""
    return db.rows("SELECT * FROM v_runs WHERE day = ? ORDER BY id",
                   (as_date(day).isoformat(),))


def latest() -> dict | None:
    rows = runs_list(limit=1)
    return rows[0] if rows else None


def recent(limit: int = 10) -> list:
    return runs_list(limit=limit)


def total_runs() -> int:
    return db.scalar("SELECT COUNT(*) FROM runs", default=0)


# --------------------------------------------------------------------------- #
# Interval sessions
# --------------------------------------------------------------------------- #
def interval_sessions(**filters) -> list:
    """Runs with interval detail recorded, newest first.

    Ordered by the pace held during the reps rather than by date when ranking
    matters, but returned newest-first here because the page that shows them is
    a log, not a leaderboard.
    """
    where, params = _filters(**filters)
    return db.rows(
        f"SELECT * FROM v_runs WHERE interval_type IS NOT NULL{where} "
        f"ORDER BY day DESC, id DESC", params)


def intervals_outstanding(**filters) -> list:
    """Runs flagged as an interval session with nothing recorded about it yet.

    This is the to-do list: the run tracker knows these were intervals because
    the sheet said so, and knows nothing else about them until someone fills it
    in. It empties itself as they are entered.
    """
    where, params = _filters(**filters)
    return db.rows(
        f"SELECT * FROM v_runs WHERE run_type = 'Intervals' "
        f"AND interval_type IS NULL{where} ORDER BY day DESC, id DESC", params)


def interval_totals(**filters) -> dict:
    """How much of the running was actually intervals."""
    where, params = _filters(**filters)
    row = db.read_one(f"""
        SELECT COUNT(*)                          AS sessions,
               SUM(interval_count)               AS reps,
               ROUND(SUM(interval_total_km), 2)  AS rep_km,
               MIN(interval_pace_s)              AS best_pace_s,
               SUM(interval_total_s)             AS rep_seconds
        FROM v_runs
        WHERE interval_type IS NOT NULL{where}
    """, params) or {}
    return {**{"sessions": 0, "reps": None, "rep_km": None,
               "best_pace_s": None, "rep_seconds": None}, **row}


# --------------------------------------------------------------------------- #
# Performance, split by type
# --------------------------------------------------------------------------- #
def _summary_sql(group_by: str, label_as: str) -> str:
    """Count, distance, time and pace for each value of one column.

    `best_pace` is the quickest whole run in the group, not the quickest split
    inside one, and `pace_s` is distance over time across the group rather than
    the mean of each run's pace - a 20 km plod and a 2 km sprint do not
    contribute equally to how fast the running was.
    """
    return f"""
        SELECT {group_by}                       AS {label_as},
               COUNT(*)                         AS runs,
               ROUND(SUM(distance_km), 2)       AS distance_km,
               SUM(duration_s)                  AS duration_s,
               ROUND(AVG(distance_km), 3)       AS avg_distance_km,
               ROUND(AVG(duration_s), 1)        AS avg_duration_s,
               SUM(duration_s) / SUM(distance_km) AS pace_s,
               MIN(pace_s)                      AS best_pace_s,
               MAX(distance_km)                 AS longest_km,
               MIN(day)                         AS first_day,
               MAX(day)                         AS last_day
        FROM v_runs
        WHERE 1 = 1{{where}}
        GROUP BY {group_by}
        ORDER BY runs DESC, {label_as}
    """


def by_run_type(**filters) -> list:
    where, params = _filters(**filters)
    return db.rows(_summary_sql("run_type", "label").format(where=where), params)


def by_effort_type(**filters) -> list:
    where, params = _filters(**filters)
    return db.rows(_summary_sql("effort_type", "label").format(where=where), params)


def by_split(split: str, **filters) -> list:
    """by_run_type or by_effort_type, chosen by name."""
    if split not in SPLITS:
        raise ValueError(f"Unknown split {split!r} - expected one of {tuple(SPLITS)}")
    where, params = _filters(**filters)
    return db.rows(_summary_sql(split, "label").format(where=where), params)


def cross_tab(**filters) -> list:
    """Run type against effort type: how many runs and how fast, for each pair.

    A "Race / Race" cell is not interesting on its own; the point of the grid is
    what the combinations that are not the obvious ones look like - the
    threshold runs that happen to be weighted, say.
    """
    where, params = _filters(**filters)
    return db.rows(f"""
        SELECT run_type, effort_type,
               COUNT(*)                           AS runs,
               ROUND(SUM(distance_km), 2)         AS distance_km,
               SUM(duration_s) / SUM(distance_km) AS pace_s
        FROM v_runs
        WHERE 1 = 1{where}
        GROUP BY run_type, effort_type
        ORDER BY run_type, effort_type
    """, params)


def by_breakdown(include_suspect: bool = False, **filters) -> list:
    """Every rung of the ladder: how often it was reached, and how fast.

    The average is over each run's best effort at that distance, so "5K" here
    means "the fastest 5K inside each run that was at least 5 km long" - not
    a 5K race average. `best_seconds` is the record the records table leads on.

    Suspect splits are left out by default, and `set_aside` says how many were,
    so a distance whose numbers are built on less than it looks says so.
    """
    where, params = _filters(**filters)
    keep = "" if include_suspect else " AND suspect = 0"
    return db.rows(f"""
        SELECT breakdown, ordinal, km,
               COUNT(*)              AS runs,
               MIN(seconds)          AS best_seconds,
               ROUND(AVG(seconds), 1) AS avg_seconds,
               MAX(seconds)          AS worst_seconds,
               MIN(seconds) / km     AS best_pace_s,
               AVG(seconds) / km     AS avg_pace_s,
               MAX(day)              AS last_day,
               (SELECT COUNT(*) FROM v_run_bests s
                WHERE s.breakdown = v_run_bests.breakdown AND s.suspect = 1)
                                     AS set_aside
        FROM v_run_bests
        WHERE 1 = 1{where}{keep}
        GROUP BY breakdown, ordinal, km
        ORDER BY ordinal
    """, params)


def by_period(grain: str = "monthly", **filters) -> list:
    """Volume and pace over time - the trend behind the split tables."""
    period = ("date(day, 'start of month')" if grain == "monthly"
              else "date(day, '-6 days', 'weekday 1')")
    where, params = _filters(**filters)
    return db.rows(f"""
        SELECT {period}                           AS period,
               COUNT(*)                           AS runs,
               ROUND(SUM(distance_km), 2)         AS distance_km,
               SUM(duration_s)                    AS duration_s,
               SUM(duration_s) / SUM(distance_km) AS pace_s,
               MIN(pace_s)                        AS best_pace_s
        FROM v_runs
        WHERE 1 = 1{where}
        GROUP BY period
        ORDER BY period
    """, params)


def totals(**filters) -> dict:
    """One row: everything matching the filters, added up."""
    where, params = _filters(**filters)
    row = db.read_one(f"""
        SELECT COUNT(*)                           AS runs,
               ROUND(SUM(distance_km), 2)         AS distance_km,
               SUM(duration_s)                    AS duration_s,
               SUM(duration_s) / SUM(distance_km) AS pace_s,
               MIN(pace_s)                        AS best_pace_s,
               MAX(distance_km)                   AS longest_km,
               MIN(day)                           AS first_day,
               MAX(day)                           AS last_day
        FROM v_runs
        WHERE 1 = 1{where}
    """, params) or {}
    return {**{"runs": 0, "distance_km": None, "duration_s": None,
               "pace_s": None, "best_pace_s": None, "longest_km": None,
               "first_day": None, "last_day": None}, **row}


# --------------------------------------------------------------------------- #
# Top performances, by breakdown distance
# --------------------------------------------------------------------------- #
def records(top: int | None = None, breakdown: str | None = None,
            include_suspect: bool = False, **filters) -> dict:
    """The fastest `top` efforts at each breakdown distance.

    Returns an ordered mapping of breakdown label -> list of rows, each already
    carrying its position, the run it came from and that run's classification.

    One run can appear in several tables and never twice in the same one. A
    21.79 km race holds a fastest 400m, a fastest 1K and a fastest 5K, and all
    three belong where they are; but it holds exactly one of each, because
    run_bests is keyed on (run_id, breakdown). Ties are broken by the earlier
    date - if the 5K has been matched but not beaten, the original stands.

    Splits that cannot be true are excluded; see v_run_bests in core/schema.sql.
    """
    top = config.TOP_N if top is None else top
    where, params = _filters(**filters)
    if not include_suspect:
        where += " AND suspect = 0"
    if breakdown:
        where += " AND breakdown = ?"
        params = params + [breakdown]

    # One query, ranked in SQL, rather than eleven. ROW_NUMBER needs SQLite
    # 3.25 (2018); DSM 7.2's Python 3.9 ships 3.34.
    rows = db.rows(f"""
        SELECT * FROM (
            SELECT run_id, day, breakdown, ordinal, km, seconds, pace_s,
                   distance_km, duration_s, run_type, effort_type, note,
                   ROW_NUMBER() OVER (
                       PARTITION BY breakdown ORDER BY seconds, day, run_id
                   ) AS position
            FROM v_run_bests
            WHERE 1 = 1{where}
        )
        WHERE position <= ?
        ORDER BY ordinal, position
    """, params + [top])

    grouped: dict = {}
    for row in rows:
        grouped.setdefault(row["breakdown"], []).append(row)
    # Ladder order, and only the distances that have been run.
    return {label: grouped[label] for label in config.BREAKDOWN_LABELS
            if label in grouped}


def personal_bests(**filters) -> list:
    """Just the number one at each distance - the headline strip."""
    return [rows[0] for rows in records(top=1, **filters).values()]


def is_record(run_id: int, breakdown: str, top: int | None = None) -> int | None:
    """Where a run's split sits in its distance's table, or None if outside it.

    Used to badge the run detail page. Ties go to the earlier run, which is the
    same rule `records()` ranks by.
    """
    top = config.TOP_N if top is None else top
    for row in records(top=top, breakdown=breakdown).get(breakdown, []):
        if row["run_id"] == run_id:
            return row["position"]
    return None


# --------------------------------------------------------------------------- #
# What there is
# --------------------------------------------------------------------------- #
def distinct(column: str) -> list:
    """The values actually present in run_type or effort_type, commonest first.

    Read from the data rather than from config, because the columns are free
    text: a run type typed once and never again should still be filterable.
    """
    if column not in SPLITS:
        raise ValueError(f"Cannot list {column!r}")
    return [row["value"] for row in db.rows(
        f"SELECT {column} AS value, COUNT(*) AS n FROM runs "
        f"GROUP BY value ORDER BY n DESC, value")]


def coverage() -> dict:
    """How much running there is, and how much of it has splits."""
    row = db.read_one("""
        SELECT COUNT(*)                   AS runs,
               MIN(day)                   AS first_day,
               MAX(day)                   AS last_day,
               ROUND(SUM(distance_km), 2) AS distance_km,
               SUM(duration_s)            AS duration_s
        FROM runs
    """) or {}
    span = 0
    if row.get("first_day") and row.get("last_day"):
        span = (as_date(row["last_day"]) - as_date(row["first_day"])).days + 1
    return {
        "runs": row.get("runs") or 0,
        "first_day": row.get("first_day"),
        "last_day": row.get("last_day"),
        "distance_km": row.get("distance_km"),
        "duration_s": row.get("duration_s"),
        "span_days": span,
        "splits": db.scalar("SELECT COUNT(*) FROM run_bests", default=0),
        "without_splits": db.scalar(
            "SELECT COUNT(*) FROM runs r WHERE NOT EXISTS "
            "(SELECT 1 FROM run_bests b WHERE b.run_id = r.id)", default=0),
    }


def anomalies() -> list:
    """Every split that cannot be true, with which way it is impossible.

    Normally empty. Eighteen came across from the scrape and were corrected at
    source in August 2026. Anything that does turn up is kept rather than
    quietly dropped - the run happened, and the figure is the figure the scrape
    produced - but held out of the records and the per-distance averages, and
    listed here so it can be fixed at the source. Nothing entered through the
    form can add to the list; see core.runs.parse_breakdowns().
    """
    return db.rows("""
        SELECT b.run_id, b.day, b.breakdown, b.seconds, b.duration_s,
               b.distance_km, b.run_type, b.effort_type,
               CASE WHEN b.seconds > b.duration_s
                    THEN 'Longer than the run it is inside'
                    ELSE 'Quicker than a shorter split of the same run'
               END AS reason
        FROM v_run_bests b
        WHERE b.suspect = 1
        ORDER BY b.day DESC, b.ordinal
    """)


def suspect_count() -> int:
    return db.scalar("SELECT COUNT(*) FROM v_run_bests WHERE suspect = 1",
                     default=0)


# --------------------------------------------------------------------------- #
# Ranges, for the period selector
# --------------------------------------------------------------------------- #
def range_start(label: str):
    """The ISO date a named range starts at, relative to the most recent run."""
    days = dict(RANGES).get(label, dict(RANGES)[DEFAULT_RANGE])
    if days is None:
        return None
    last = db.scalar("SELECT MAX(day) FROM runs")
    if last is None:
        return None
    return (as_date(last) - dt.timedelta(days=days - 1)).isoformat()


def audit_trail(limit: int = 100) -> list:
    return db.rows(
        "SELECT ts, action, entity, entity_id, detail FROM audit_log "
        "WHERE entity IN ('runs', 'run_bests') ORDER BY id DESC LIMIT ?", (limit,))
