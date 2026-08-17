"""Reads for the workout section. Plain lists of dicts, no pandas.

Same contract as core/run_queries.py and for the same reason: the Flask
front-end runs on a NAS with ~150 MB of RAM free, so nothing on this side of the
line may import pandas. The Streamlit adapter in views/workout_frames.py turns
these into DataFrames where it needs them.

The prescribed weight is never computed here. It comes out of v_exercise_sets
already worked out, so a percentage becomes kilograms in exactly one place - see
the note on that view in core/schema.sql about rounding half-way cases.
"""
from __future__ import annotations

import datetime as dt

import config
from core import db, workouts

# --------------------------------------------------------------------------- #
# The exercise catalogue
# --------------------------------------------------------------------------- #
def exercises(include_retired: bool = False) -> list:
    """The catalogue, in dropdown order."""
    where = "" if include_retired else " WHERE retired = 0"
    return db.rows(f"SELECT * FROM exercises{where} ORDER BY position, name")


def exercise(exercise_id: int) -> dict | None:
    return db.read_one("SELECT * FROM exercises WHERE id = ?", (exercise_id,))


def exercise_by_name(name: str) -> dict | None:
    return db.read_one("SELECT * FROM exercises WHERE name = ? COLLATE NOCASE",
                       (str(name).strip(),))


def exercise_usage() -> dict:
    """Exercise id -> how many sessions use it. What makes retiring safe."""
    return {row["exercise_id"]: row["sessions"] for row in db.rows(
        "SELECT exercise_id, COUNT(DISTINCT session_id) AS sessions "
        "FROM session_exercises GROUP BY exercise_id")}


def catalogue_with_usage(include_retired: bool = True) -> list:
    counts = exercise_usage()
    return [{**row, "sessions": counts.get(row["id"], 0)}
            for row in exercises(include_retired)]


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #
def plans(include_archived: bool = True) -> list:
    """Every plan, newest first - what the historic-plans dropdown offers."""
    where = "" if include_archived else " WHERE archived = 0"
    return db.rows(
        f"SELECT * FROM v_plans{where} "
        f"ORDER BY COALESCE(started_on, created_at) DESC, id DESC")


def plan(plan_id: int) -> dict | None:
    return db.read_one("SELECT * FROM v_plans WHERE id = ?", (plan_id,))


def plan_by_name(name: str) -> dict | None:
    return db.read_one("SELECT * FROM v_plans WHERE name = ? COLLATE NOCASE",
                       (str(name).strip(),))


def current_plan() -> dict | None:
    """The plan to open on: the most recently trained, else the newest.

    "Most recently trained" beats "newest" because a plan half-built for next
    block should not push aside the one being followed this week.
    """
    return db.read_one(
        "SELECT * FROM v_plans WHERE archived = 0 "
        "ORDER BY last_done DESC NULLS LAST, "
        "         COALESCE(started_on, created_at) DESC, id DESC LIMIT 1")


def total_plans() -> int:
    return db.scalar("SELECT COUNT(*) FROM plans", default=0)


def maxes(plan_id: int) -> list:
    """A plan's 1RMs, with the exercise they belong to."""
    return db.rows(
        "SELECT m.exercise_id, m.one_rm_kg, e.name, e.is_bodyweight "
        "FROM plan_maxes m JOIN exercises e ON e.id = m.exercise_id "
        "WHERE m.plan_id = ? ORDER BY e.position, e.name", (plan_id,))


def max_for(plan_id: int, exercise_id: int):
    return db.scalar(
        "SELECT one_rm_kg FROM plan_maxes WHERE plan_id = ? AND exercise_id = ?",
        (plan_id, exercise_id))


def percent_sets_without_a_max(plan_id: int) -> list:
    """Percentage sets whose lift has no 1RM on this plan.

    The one inconsistency a plan can hold that nothing else catches: the set
    says 87% and there is nothing to take 87% of, so v_exercise_sets honestly
    reports NULL and the week sheet has a blank where a weight should be.
    """
    return db.rows("""
        SELECT exercise_name, COUNT(*) AS sets, MIN(week_number) AS first_week
        FROM v_exercise_sets
        WHERE plan_id = ? AND load_mode = 'percent' AND one_rm_kg IS NULL
        GROUP BY exercise_name ORDER BY exercise_name
    """, (plan_id,))


# --------------------------------------------------------------------------- #
# Phases and weeks
# --------------------------------------------------------------------------- #
def phases(plan_id: int) -> list:
    return db.rows(
        "SELECT ph.*, "
        "  (SELECT COUNT(*) FROM weeks w WHERE w.phase_id = ph.id) AS weeks "
        "FROM phases ph WHERE ph.plan_id = ? ORDER BY ph.position, ph.name",
        (plan_id,))


def phase(phase_id: int) -> dict | None:
    return db.read_one("SELECT * FROM phases WHERE id = ?", (phase_id,))


def weeks(plan_id: int) -> list:
    """Every week of a plan, with its phase and how far through it you are."""
    return db.rows("""
        SELECT w.*, ph.name AS phase_name, ph.position AS phase_position,
               (SELECT COUNT(*) FROM sessions s WHERE s.week_id = w.id)
                                                            AS sessions,
               (SELECT COUNT(*) FROM sessions s
                 JOIN session_log l ON l.session_id = s.id
                WHERE s.week_id = w.id)                     AS sessions_done
        FROM weeks w
        LEFT JOIN phases ph ON ph.id = w.phase_id
        WHERE w.plan_id = ?
        ORDER BY w.number
    """, (plan_id,))


def week(week_id: int) -> dict | None:
    return db.read_one(
        "SELECT w.*, ph.name AS phase_name, p.name AS plan_name, "
        "       p.rounding_kg "
        "FROM weeks w LEFT JOIN phases ph ON ph.id = w.phase_id "
        "JOIN plans p ON p.id = w.plan_id WHERE w.id = ?", (week_id,))


def week_number(plan_id: int, number: int) -> dict | None:
    return db.read_one("SELECT * FROM weeks WHERE plan_id = ? AND number = ?",
                       (plan_id, number))


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def sessions(plan_id: int | None = None, week_id: int | None = None,
             done: bool | None = None) -> list:
    where, params = [], []
    if plan_id is not None:
        where.append("plan_id = ?")
        params.append(plan_id)
    if week_id is not None:
        where.append("week_id = ?")
        params.append(week_id)
    if done is not None:
        where.append("done = ?")
        params.append(1 if done else 0)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return db.rows(f"SELECT * FROM v_sessions{clause} "
                   f"ORDER BY week_number, number", params)


def session(session_id: int) -> dict | None:
    return db.read_one("SELECT * FROM v_sessions WHERE id = ?", (session_id,))


def session_exercises(session_id: int) -> list:
    """A session's movements in order, each carrying its resolved modes."""
    return db.rows("""
        SELECT sx.*, e.name, e.is_bodyweight,
               COALESCE(sx.reps_mode, e.reps_mode)     AS resolved_reps_mode,
               COALESCE(sx.weight_mode, e.weight_mode) AS resolved_weight_mode
        FROM session_exercises sx JOIN exercises e ON e.id = sx.exercise_id
        WHERE sx.session_id = ? ORDER BY sx.position
    """, (session_id,))


def sets_for(session_exercise_id: int) -> list:
    return db.rows(
        "SELECT * FROM v_exercise_sets WHERE session_exercise_id = ? "
        "ORDER BY CASE set_type WHEN 'warmup' THEN 0 WHEN 'working' THEN 1 "
        "         ELSE 2 END, position", (session_exercise_id,))


def session_sheet(session_id: int) -> list:
    """A session as the week sheet shows it: exercises, each with its sets.

    One query for the sets rather than one per exercise, because a nineteen-week
    plan viewed a week at a time is otherwise a few hundred round trips for no
    reason.
    """
    exercises_ = session_exercises(session_id)
    rows = db.rows(
        "SELECT * FROM v_exercise_sets WHERE session_id = ? "
        "ORDER BY exercise_position, CASE set_type WHEN 'warmup' THEN 0 "
        "         WHEN 'working' THEN 1 ELSE 2 END, position", (session_id,))
    by_exercise: dict = {}
    for row in rows:
        by_exercise.setdefault(row["session_exercise_id"], []).append(row)
    return [{**item, "sets": by_exercise.get(item["id"], [])}
            for item in exercises_]


def week_sheet(week_id: int) -> list:
    """A whole week: its sessions, each with the sheet above."""
    return [{**item, "sheet": session_sheet(item["id"])}
            for item in sessions(week_id=week_id)]


# --------------------------------------------------------------------------- #
# The tracker
# --------------------------------------------------------------------------- #
def next_session(plan_id: int) -> dict | None:
    """The first session not yet ticked off - what the tracker opens on."""
    return db.read_one(
        "SELECT * FROM v_sessions WHERE plan_id = ? AND done = 0 "
        "ORDER BY week_number, number LIMIT 1", (plan_id,))


def done_log(plan_id: int, limit: int = 50) -> list:
    return db.rows(
        "SELECT * FROM v_sessions WHERE plan_id = ? AND done = 1 "
        "ORDER BY done_on DESC, week_number DESC, number DESC LIMIT ?",
        (plan_id, limit))


def week_progress(plan_id: int) -> list:
    """Per week: how many sessions there are and how many are done.

    What the tracker grid is drawn from, and the same shape the workbook's
    Tracker sheet had - a row per week, a tick per workout.
    """
    return [{"week_id": row["id"], "number": row["number"],
             "label": row["label"], "phase_name": row["phase_name"],
             "cycle_type": row["cycle_type"],
             "sessions": row["sessions"], "sessions_done": row["sessions_done"],
             "complete": bool(row["sessions"])
                         and row["sessions"] == row["sessions_done"]}
            for row in weeks(plan_id)]


def totals(plan_id: int) -> dict:
    """Headline numbers for a plan: sessions, sets, and how far through."""
    row = db.read_one("""
        SELECT (SELECT COUNT(*) FROM v_sessions WHERE plan_id = ?)          AS sessions,
               (SELECT COUNT(*) FROM v_sessions WHERE plan_id = ? AND done = 1)
                                                                           AS sessions_done,
               (SELECT COUNT(*) FROM v_exercise_sets WHERE plan_id = ?)     AS sets,
               (SELECT COUNT(*) FROM weeks WHERE plan_id = ?)               AS weeks
    """, (plan_id, plan_id, plan_id, plan_id)) or {}
    base = {"sessions": 0, "sessions_done": 0, "sets": 0, "weeks": 0}
    merged = {**base, **{k: v or 0 for k, v in row.items()}}
    merged["progress"] = workouts.progress(merged)
    return merged


def volume_by_exercise(plan_id: int, done_only: bool = False) -> list:
    """Prescribed working volume per movement: sets, and kg lifted where known.

    Warm-ups are left out - they are not the work - and so are 'choose weight'
    accessories, which have no number to add up. `sets_without_weight` says how
    many were skipped, so a total is never quietly short.
    """
    clause = " AND s.done = 1" if done_only else ""
    return db.rows(f"""
        SELECT v.exercise_name,
               COUNT(*)                                        AS sets,
               SUM(COALESCE(v.reps_low, 0))                     AS reps,
               SUM(CASE WHEN v.prescribed_kg IS NULL THEN 1 ELSE 0 END)
                                                               AS sets_without_weight,
               ROUND(SUM(COALESCE(v.reps_low, 0) * COALESCE(v.prescribed_kg, 0)
                         * CASE WHEN v.weight_mode = 'per_dumbbell' THEN 2 ELSE 1 END
                         * CASE WHEN v.reps_mode = 'per_side' THEN 2 ELSE 1 END), 1)
                                                               AS volume_kg
        FROM v_exercise_sets v
        JOIN v_sessions s ON s.id = v.session_id
        WHERE v.plan_id = ? AND v.set_type <> 'warmup'{clause}
        GROUP BY v.exercise_name ORDER BY volume_kg DESC, v.exercise_name
    """, (plan_id,))


def audit_trail(limit: int = 60) -> list:
    return db.rows(
        "SELECT ts, action, entity, entity_id, detail FROM audit_log "
        "WHERE entity IN ('plans','weeks','sessions','exercises','phases',"
        "'session_log','workouts') ORDER BY id DESC LIMIT ?", (limit,))


# Passed straight through.
SET_TYPES = config.SET_TYPES
LOAD_MODES = config.LOAD_MODES
