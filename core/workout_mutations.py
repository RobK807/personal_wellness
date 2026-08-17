"""Writes for the workout section: the catalogue, plans, weeks and sessions.

Every public function is one transaction and one audit-log entry, the same shape
core/run_mutations.py has.

Two things here are worth knowing before reading the rest:

**A session is saved wholesale.** Its exercises and their sets are replaced by
whatever the form holds, rather than diffed. The alternative - matching rows up
and editing in place - needs stable ids round-tripped through a form that can
hold ten exercises of up to eleven sets each, and gets one of them wrong
eventually. Replacing is dull and cannot half-apply. It does mean the ids change
on every save, which is why nothing outside a session refers to them; the tracker
keys off the session.

**Copying a plan copies structure, not history.** `copy_plan` reproduces phases,
weeks, sessions, exercises and sets, and deliberately does not reproduce the
tick-offs - a template is what you intend to do, and inheriting somebody else's
completed sessions would be a lie about this block.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Mapping, Sequence

import config
from core import db, workout_queries as wq, workouts
from core.workouts import InvalidWorkout  # re-exported: callers catch one thing


# --------------------------------------------------------------------------- #
# The exercise catalogue
# --------------------------------------------------------------------------- #
def save_exercise(values: Mapping, exercise_id: int | None = None) -> dict:
    """Add a movement to the catalogue, or correct one."""
    parsed = workouts.parse_exercise(values)
    with db.transaction() as conn:
        clash = conn.execute(
            "SELECT id FROM exercises WHERE name = ? COLLATE NOCASE "
            "AND id IS NOT ?", (parsed["name"], exercise_id)).fetchone()
        if clash is not None:
            raise InvalidWorkout(
                f"'{parsed['name']}' is already in the catalogue. Two spellings "
                f"of one movement is what the list exists to prevent")

        if exercise_id is None:
            position = (conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM exercises"
            ).fetchone()[0])
            cursor = conn.execute(
                "INSERT INTO exercises (name, reps_mode, weight_mode, "
                "is_bodyweight, position, note) VALUES (?, ?, ?, ?, ?, ?)",
                (parsed["name"], parsed["reps_mode"], parsed["weight_mode"],
                 parsed["is_bodyweight"], position, parsed["note"]))
            exercise_id = cursor.lastrowid
            action = "add_exercise"
        else:
            changed = conn.execute(
                "UPDATE exercises SET name = ?, reps_mode = ?, weight_mode = ?, "
                "is_bodyweight = ?, note = ? WHERE id = ?",
                (parsed["name"], parsed["reps_mode"], parsed["weight_mode"],
                 parsed["is_bodyweight"], parsed["note"], exercise_id)).rowcount
            if not changed:
                raise InvalidWorkout(f"No exercise with id {exercise_id}")
            action = "edit_exercise"

        db.log(conn, action, "exercises", str(exercise_id),
               f"{parsed['name']} ({parsed['reps_mode']}, "
               f"{parsed['weight_mode']}"
               + (", bodyweight" if parsed["is_bodyweight"] else "") + ")")
    return {"id": exercise_id, **parsed}


def retire_exercise(exercise_id: int, retired: bool = True) -> dict:
    """Take a movement out of the dropdown without touching the plans using it.

    Retiring rather than deleting, because a plan from two years ago naming an
    exercise that has since been removed would have a hole in it. The foreign key
    would refuse the delete anyway; this is the thing to do instead.
    """
    row = wq.exercise(exercise_id)
    if row is None:
        raise InvalidWorkout(f"No exercise with id {exercise_id}")
    with db.transaction() as conn:
        conn.execute("UPDATE exercises SET retired = ? WHERE id = ?",
                     (1 if retired else 0, exercise_id))
        db.log(conn, "retire_exercise" if retired else "restore_exercise",
               "exercises", str(exercise_id), row["name"])
    return {**row, "retired": 1 if retired else 0}


def reorder_exercises(ordered_ids: Sequence[int]) -> int:
    """Set the dropdown order. Ids not listed keep their place after these."""
    with db.transaction() as conn:
        for position, exercise_id in enumerate(ordered_ids, start=1):
            conn.execute("UPDATE exercises SET position = ? WHERE id = ?",
                         (position, exercise_id))
        db.log(conn, "reorder_exercises", "exercises", None,
               f"{len(ordered_ids)} moved")
    return len(ordered_ids)


def delete_exercise(exercise_id: int) -> dict:
    """Remove a movement no plan has ever used. Refused otherwise."""
    row = wq.exercise(exercise_id)
    if row is None:
        raise InvalidWorkout(f"No exercise with id {exercise_id}")
    used = wq.exercise_usage().get(exercise_id, 0)
    if used:
        raise InvalidWorkout(
            f"'{row['name']}' is in {used} session{'' if used == 1 else 's'}, so "
            f"deleting it would leave a hole in them. Retire it instead - it "
            f"drops out of the dropdown and the old plans keep reading right")
    with db.transaction() as conn:
        conn.execute("DELETE FROM plan_maxes WHERE exercise_id = ?",
                     (exercise_id,))
        conn.execute("DELETE FROM exercises WHERE id = ?", (exercise_id,))
        db.log(conn, "delete_exercise", "exercises", str(exercise_id),
               row["name"])
    return row


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #
def save_plan(values: Mapping, plan_id: int | None = None,
              source: str = "manual") -> dict:
    """Create a plan or edit its details. Does not touch its weeks."""
    parsed = workouts.parse_plan(values)
    started = parsed["started_on"].isoformat() if parsed["started_on"] else None
    with db.transaction() as conn:
        clash = conn.execute(
            "SELECT id FROM plans WHERE name = ? COLLATE NOCASE AND id IS NOT ?",
            (parsed["name"], plan_id)).fetchone()
        if clash is not None:
            raise InvalidWorkout(
                f"A plan called '{parsed['name']}' already exists. The name is "
                f"how you find it again, so two cannot share one")

        if plan_id is None:
            cursor = conn.execute(
                "INSERT INTO plans (name, started_on, rounding_kg, note, "
                "archived, source) VALUES (?, ?, ?, ?, ?, ?)",
                (parsed["name"], started, parsed["rounding_kg"],
                 parsed["note"], parsed["archived"], source))
            plan_id = cursor.lastrowid
            action = "add_plan"
        else:
            changed = conn.execute(
                "UPDATE plans SET name = ?, started_on = ?, rounding_kg = ?, "
                "note = ?, archived = ?, updated_at = datetime('now')"
                " WHERE id = ?",
                (parsed["name"], started, parsed["rounding_kg"], parsed["note"],
                 parsed["archived"], plan_id)).rowcount
            if not changed:
                raise InvalidWorkout(f"No plan with id {plan_id}")
            action = "edit_plan"

        db.log(conn, action, "plans", str(plan_id),
               f"{parsed['name']}, rounding "
               f"{workouts.fmt_kg(parsed['rounding_kg'])} kg")
    return {"id": plan_id, **parsed}


def delete_plan(plan_id: int) -> dict:
    """Delete a plan and everything under it. Cascades all the way down."""
    row = wq.plan(plan_id)
    if row is None:
        raise InvalidWorkout(f"No plan with id {plan_id}")
    with db.transaction() as conn:
        conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        db.log(conn, "delete_plan", "plans", str(plan_id),
               f"{row['name']}: {row['weeks']} weeks, {row['sessions']} sessions")
    return row


def set_max(plan_id: int, exercise_id: int, one_rm_kg) -> dict:
    """Record or change a 1RM on one plan. A blank one removes it."""
    if one_rm_kg is None or (isinstance(one_rm_kg, str)
                             and not str(one_rm_kg).strip()):
        with db.transaction() as conn:
            conn.execute("DELETE FROM plan_maxes WHERE plan_id = ? "
                         "AND exercise_id = ?", (plan_id, exercise_id))
            db.log(conn, "clear_1rm", "plans", str(plan_id),
                   f"exercise {exercise_id}")
        return {"plan_id": plan_id, "exercise_id": exercise_id,
                "one_rm_kg": None}

    value = workouts.parse_kg(one_rm_kg, "1RM")
    low, high = config.WORKOUT_BOUNDS["one_rm_kg"]
    if not low <= value <= high:
        raise InvalidWorkout(
            f"A 1RM of {workouts.fmt_kg(value)} kg looks wrong - expected "
            f"between {workouts.fmt_kg(low)} and {workouts.fmt_kg(high)}")
    row = wq.exercise(exercise_id)
    if row is None:
        raise InvalidWorkout(f"No exercise with id {exercise_id}")

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO plan_maxes (plan_id, exercise_id, one_rm_kg) "
            "VALUES (?, ?, ?) ON CONFLICT (plan_id, exercise_id) "
            "DO UPDATE SET one_rm_kg = excluded.one_rm_kg",
            (plan_id, exercise_id, value))
        db.log(conn, "set_1rm", "plans", str(plan_id),
               f"{row['name']} = {workouts.fmt_kg(value)} kg")
    return {"plan_id": plan_id, "exercise_id": exercise_id, "one_rm_kg": value}


# --------------------------------------------------------------------------- #
# Phases
# --------------------------------------------------------------------------- #
def save_phase(plan_id: int, values: Mapping,
               phase_id: int | None = None) -> dict:
    parsed = workouts.parse_phase(values)
    columns = ("name", "focus", "warmup_pcts", "working_pcts", "working_sets",
               "working_reps", "accessory_sets", "accessory_reps",
               "rest_warmup", "rest_working", "rest_accessory")
    with db.transaction() as conn:
        if phase_id is None:
            position = conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM phases "
                "WHERE plan_id = ?", (plan_id,)).fetchone()[0]
            placeholders = ", ".join("?" * (len(columns) + 2))
            cursor = conn.execute(
                f"INSERT INTO phases (plan_id, position, "
                f"{', '.join(columns)}) VALUES ({placeholders})",
                (plan_id, position, *(parsed[name] for name in columns)))
            phase_id = cursor.lastrowid
            action = "add_phase"
        else:
            assignments = ", ".join(f"{name} = ?" for name in columns)
            changed = conn.execute(
                f"UPDATE phases SET {assignments} WHERE id = ? AND plan_id = ?",
                (*(parsed[name] for name in columns), phase_id,
                 plan_id)).rowcount
            if not changed:
                raise InvalidWorkout(f"No phase with id {phase_id}")
            action = "edit_phase"
        db.log(conn, action, "phases", str(phase_id),
               f"{parsed['name']} on plan {plan_id}")
    return {"id": phase_id, "plan_id": plan_id, **parsed}


def delete_phase(phase_id: int) -> None:
    """Remove a phase. Its weeks keep their sessions and lose the label."""
    with db.transaction() as conn:
        row = conn.execute("SELECT name, plan_id FROM phases WHERE id = ?",
                           (phase_id,)).fetchone()
        if row is None:
            raise InvalidWorkout(f"No phase with id {phase_id}")
        conn.execute("DELETE FROM phases WHERE id = ?", (phase_id,))
        db.log(conn, "delete_phase", "phases", str(phase_id), row[0])


# --------------------------------------------------------------------------- #
# Weeks
# --------------------------------------------------------------------------- #
def save_week(plan_id: int, values: Mapping, week_id: int | None = None) -> dict:
    """Add a week to a plan, or change its label, phase, type or notes."""
    number = workouts._count(values.get("number"), "Week number")
    if week_id is None and number is None:
        with db.transaction() as conn:
            number = conn.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 FROM weeks "
                "WHERE plan_id = ?", (plan_id,)).fetchone()[0]

    fields = {
        "number": number,
        "label": (str(values.get("label") or "")).strip() or None,
        "phase_id": values.get("phase_id") or None,
        "cycle_type": (str(values.get("cycle_type") or "")).strip() or None,
        "note": (str(values.get("note") or "")).strip() or None,
    }

    with db.transaction() as conn:
        if fields["number"] is not None:
            clash = conn.execute(
                "SELECT id FROM weeks WHERE plan_id = ? AND number = ? "
                "AND id IS NOT ?", (plan_id, fields["number"],
                                    week_id)).fetchone()
            if clash is not None:
                raise InvalidWorkout(
                    f"This plan already has a week {fields['number']}")

        if week_id is None:
            cursor = conn.execute(
                "INSERT INTO weeks (plan_id, number, label, phase_id, "
                "cycle_type, note) VALUES (?, ?, ?, ?, ?, ?)",
                (plan_id, fields["number"], fields["label"],
                 fields["phase_id"], fields["cycle_type"], fields["note"]))
            week_id = cursor.lastrowid
            action = "add_week"
        else:
            sets = ["label = ?", "phase_id = ?", "cycle_type = ?", "note = ?"]
            params = [fields["label"], fields["phase_id"],
                      fields["cycle_type"], fields["note"]]
            if fields["number"] is not None:
                sets.insert(0, "number = ?")
                params.insert(0, fields["number"])
            changed = conn.execute(
                f"UPDATE weeks SET {', '.join(sets)} WHERE id = ? "
                f"AND plan_id = ?", (*params, week_id, plan_id)).rowcount
            if not changed:
                raise InvalidWorkout(f"No week with id {week_id}")
            action = "edit_week"
        db.log(conn, action, "weeks", str(week_id),
               f"plan {plan_id} week {fields['number']}"
               + (f" ({fields['label']})" if fields["label"] else ""))
    return {"id": week_id, "plan_id": plan_id, **fields}


def delete_week(week_id: int) -> None:
    with db.transaction() as conn:
        row = conn.execute("SELECT plan_id, number FROM weeks WHERE id = ?",
                           (week_id,)).fetchone()
        if row is None:
            raise InvalidWorkout(f"No week with id {week_id}")
        conn.execute("DELETE FROM weeks WHERE id = ?", (week_id,))
        db.log(conn, "delete_week", "weeks", str(week_id),
               f"plan {row[0]} week {row[1]}")


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def save_session(week_id: int, values: Mapping, exercises: Sequence[Mapping],
                 session_id: int | None = None) -> dict:
    """The session builder's write path: a session and everything in it.

    `exercises` is a list, in the order they are done, of

        {"exercise_id": 3,
         "reps_mode": None, "weight_mode": None, "note": "",
         "sets": [{"set_type": "warmup", "reps": "5",
                   "load_mode": "percent", "percent_1rm": "50",
                   "rest": "60s", "cue": ""}, ...]}

    Parsed in full before anything is written, so a bad set in the ninth
    exercise does not leave the first eight saved and the session half-built.
    """
    parsed = workouts.parse_session(values)
    plan = _plan_of_week(week_id)

    if len(exercises) > config.MAX_EXERCISES_PER_SESSION:
        raise InvalidWorkout(
            f"{len(exercises)} exercises is more than a session holds "
            f"(limit {config.MAX_EXERCISES_PER_SESSION})")

    prepared = [_prepare_exercise(item, index)
                for index, item in enumerate(exercises, start=1)]
    if not prepared:
        raise InvalidWorkout("A session needs at least one exercise")

    with db.transaction() as conn:
        clash = conn.execute(
            "SELECT id FROM sessions WHERE week_id = ? AND number = ? "
            "AND id IS NOT ?", (week_id, parsed["number"],
                                session_id)).fetchone()
        if clash is not None:
            raise InvalidWorkout(
                f"This week already has a session {parsed['number']}")

        if session_id is None:
            cursor = conn.execute(
                "INSERT INTO sessions (week_id, number, name, note) "
                "VALUES (?, ?, ?, ?)",
                (week_id, parsed["number"], parsed["name"], parsed["note"]))
            session_id = cursor.lastrowid
            action = "add_session"
        else:
            changed = conn.execute(
                "UPDATE sessions SET number = ?, name = ?, note = ? "
                "WHERE id = ? AND week_id = ?",
                (parsed["number"], parsed["name"], parsed["note"], session_id,
                 week_id)).rowcount
            if not changed:
                raise InvalidWorkout(f"No session with id {session_id}")
            action = "edit_session"

        # Replaced wholesale - see the module docstring. The sets go with the
        # exercises through ON DELETE CASCADE.
        conn.execute("DELETE FROM session_exercises WHERE session_id = ?",
                     (session_id,))
        total_sets = _write_exercises(conn, session_id, prepared)

        db.log(conn, action, "sessions", str(session_id),
               f"plan {plan['id']} week {plan['week_number']} "
               f"session {parsed['number']}: {len(prepared)} exercises, "
               f"{total_sets} sets")

    return {"id": session_id, "week_id": week_id, **parsed,
            "exercises": len(prepared), "sets": total_sets}


def _prepare_exercise(item: Mapping, position: int) -> dict:
    """Validate one exercise and its sets, without writing anything."""
    exercise_id = item.get("exercise_id")
    try:
        exercise_id = int(exercise_id)
    except (TypeError, ValueError):
        raise InvalidWorkout(
            f"Exercise {position} has no movement chosen") from None
    row = wq.exercise(exercise_id)
    if row is None:
        raise InvalidWorkout(f"No exercise with id {exercise_id}")

    sets = []
    counters: dict = {}
    for raw in item.get("sets") or []:
        set_type = str(raw.get("set_type") or "working").strip().lower()
        parsed = workouts.parse_set(raw, set_type)
        counters[set_type] = counters.get(set_type, 0) + 1
        parsed["position"] = counters[set_type]

        if parsed["load_mode"] == "percent" and row["is_bodyweight"]:
            raise InvalidWorkout(
                f"{row['name']} has no 1RM to take a percentage of - it is a "
                f"bodyweight movement. Use 'Bodyweight (+ added)' instead")
        if parsed["load_mode"] == "bodyweight" and not row["is_bodyweight"]:
            raise InvalidWorkout(
                f"{row['name']} is not marked as a bodyweight movement, so "
                f"'Bodyweight' is not one of its options. Change it in the "
                f"exercise catalogue if it should be")
        sets.append(parsed)

    if counters.get("warmup", 0) > config.MAX_WARMUP_SETS:
        raise InvalidWorkout(
            f"{row['name']} has {counters['warmup']} warm-up sets - at most "
            f"{config.MAX_WARMUP_SETS} are prescribed individually")
    if not sets:
        raise InvalidWorkout(f"{row['name']} has no sets against it")

    return {
        "exercise_id": exercise_id,
        "position": position,
        "reps_mode": workouts._one_of(item.get("reps_mode"),
                                     config.REPS_MODES, "Reps counted", "")
                     or None,
        "weight_mode": workouts._one_of(item.get("weight_mode"),
                                        config.WEIGHT_MODES,
                                        "Weight counted", "") or None,
        "note": (str(item.get("note") or "")).strip() or None,
        "sets": sets,
    }


def _write_exercises(conn: sqlite3.Connection, session_id: int,
                     prepared: Sequence[Mapping]) -> int:
    total = 0
    for item in prepared:
        cursor = conn.execute(
            "INSERT INTO session_exercises (session_id, exercise_id, position, "
            "reps_mode, weight_mode, note) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, item["exercise_id"], item["position"],
             item["reps_mode"], item["weight_mode"], item["note"]))
        sx_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO exercise_sets (session_exercise_id, set_type, "
            "position, reps_low, reps_high, load_mode, weight_kg, percent_1rm, "
            "added_kg, rest, cue) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(sx_id, s["set_type"], s["position"], s["reps_low"],
              s["reps_high"], s["load_mode"], s["weight_kg"],
              s["percent_1rm"], s["added_kg"], s["rest"], s["cue"])
             for s in item["sets"]])
        total += len(item["sets"])
    return total


def delete_session(session_id: int) -> None:
    with db.transaction() as conn:
        row = conn.execute("SELECT week_id, number FROM sessions WHERE id = ?",
                           (session_id,)).fetchone()
        if row is None:
            raise InvalidWorkout(f"No session with id {session_id}")
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        db.log(conn, "delete_session", "sessions", str(session_id),
               f"week {row[0]} session {row[1]}")


def copy_session(session_id: int, week_id: int,
                 number: int | None = None) -> dict:
    """Duplicate a session into a week - how a rotating cycle gets built.

    The workbook repeats six week shapes three times over, and this is that:
    build week 1, copy it to week 7 and week 13, change the percentages.
    """
    source = wq.session(session_id)
    if source is None:
        raise InvalidWorkout(f"No session with id {session_id}")
    sheet = wq.session_sheet(session_id)
    exercises = [{
        "exercise_id": item["exercise_id"],
        "reps_mode": item["reps_mode"] or "",
        "weight_mode": item["weight_mode"] or "",
        "note": item["note"] or "",
        "sets": [{
            "set_type": s["set_type"],
            "reps": workouts.fmt_reps(s["reps_low"], s["reps_high"]),
            "load_mode": s["load_mode"],
            "weight_kg": s["weight_kg"],
            "percent_1rm": s["percent_1rm"],
            "added_kg": s["added_kg"],
            "rest": s["rest"] or "",
            "cue": s["cue"] or "",
        } for s in item["sets"]],
    } for item in sheet]

    if number is None:
        number = (db.scalar("SELECT COALESCE(MAX(number), 0) + 1 FROM sessions "
                            "WHERE week_id = ?", (week_id,), default=1))
    return save_session(week_id,
                        {"number": number, "name": source["name"],
                         "note": source["note"]},
                        exercises)


def copy_week(week_id: int, plan_id: int, number: int | None = None) -> dict:
    """Duplicate a whole week, sessions and all, into a plan."""
    source = wq.week(week_id)
    if source is None:
        raise InvalidWorkout(f"No week with id {week_id}")
    created = save_week(plan_id, {
        "number": number, "label": source["label"],
        "cycle_type": source["cycle_type"], "note": source["note"]})
    for item in wq.sessions(week_id=week_id):
        copy_session(item["id"], created["id"], item["number"])
    return created


def copy_plan(plan_id: int, name: str,
              started_on=None, with_maxes: bool = True) -> dict:
    """Use a stored plan as the template for a new one.

    Structure only: phases, weeks, sessions, exercises and sets, plus the 1RMs
    unless told otherwise. The tick-offs are not copied - see the module
    docstring.
    """
    source = wq.plan(plan_id)
    if source is None:
        raise InvalidWorkout(f"No plan with id {plan_id}")

    created = save_plan({"name": name, "started_on": started_on,
                         "rounding_kg": source["rounding_kg"],
                         "note": source["note"]},
                        source="manual")
    new_id = created["id"]

    phase_map: dict = {}
    for phase in wq.phases(plan_id):
        made = save_phase(new_id, {
            key: phase[key] for key in
            ("name", "focus", "warmup_pcts", "working_pcts", "working_sets",
             "working_reps", "accessory_sets", "accessory_reps",
             "rest_warmup", "rest_working", "rest_accessory")})
        phase_map[phase["id"]] = made["id"]

    if with_maxes:
        for row in wq.maxes(plan_id):
            set_max(new_id, row["exercise_id"], row["one_rm_kg"])

    weeks = sessions = 0
    for week in wq.weeks(plan_id):
        made = save_week(new_id, {
            "number": week["number"], "label": week["label"],
            "phase_id": phase_map.get(week["phase_id"]),
            "cycle_type": week["cycle_type"], "note": week["note"]})
        weeks += 1
        for item in wq.sessions(week_id=week["id"]):
            copy_session(item["id"], made["id"], item["number"])
            sessions += 1

    with db.transaction() as conn:
        db.log(conn, "copy_plan", "plans", str(new_id),
               f"from '{source['name']}': {weeks} weeks, {sessions} sessions")
    return {**created, "weeks": weeks, "sessions": sessions,
            "copied_from": source["name"]}


# --------------------------------------------------------------------------- #
# The tracker
# --------------------------------------------------------------------------- #
def tick_session(session_id: int, done: bool = True, done_on=None,
                 note: str | None = None) -> dict:
    """Tick a session off, or un-tick it."""
    row = wq.session(session_id)
    if row is None:
        raise InvalidWorkout(f"No session with id {session_id}")

    with db.transaction() as conn:
        if not done:
            conn.execute("DELETE FROM session_log WHERE session_id = ?",
                         (session_id,))
            db.log(conn, "untick_session", "session_log", str(session_id),
                   workouts.session_title(row))
            return {**row, "done": 0, "done_on": None}

        when = workouts.as_date(done_on) if done_on else dt.date.today()
        if when > dt.date.today():
            raise InvalidWorkout(f"{when:%d/%m/%Y} is in the future")
        conn.execute(
            "INSERT INTO session_log (session_id, done_on, note) "
            "VALUES (?, ?, ?) ON CONFLICT (session_id) DO UPDATE SET "
            "done_on = excluded.done_on, note = excluded.note",
            (session_id, when.isoformat(),
             (str(note or "")).strip() or None))
        db.log(conn, "tick_session", "session_log", str(session_id),
               f"{workouts.session_title(row)} on {when:%d/%m/%Y}")
    return {**row, "done": 1, "done_on": when.isoformat()}


def tick_week(week_id: int, done: bool = True, done_on=None) -> int:
    """Tick every session in a week. Returns how many changed."""
    changed = 0
    for item in wq.sessions(week_id=week_id):
        if bool(item["done"]) != bool(done):
            tick_session(item["id"], done, done_on)
            changed += 1
    return changed


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _plan_of_week(week_id: int) -> dict:
    row = db.read_one(
        "SELECT p.id, p.name, p.rounding_kg, w.number AS week_number "
        "FROM weeks w JOIN plans p ON p.id = w.plan_id WHERE w.id = ?",
        (week_id,))
    if row is None:
        raise InvalidWorkout(f"No week with id {week_id}")
    return row


def seed_exercises() -> list:
    """Fill an empty catalogue from config.WORKOUT_EXERCISES.

    Called from core/db.py on first start, the same idea as the run and effort
    type lists: the constants are a seed, the table is the live catalogue.
    """
    added = []
    with db.transaction() as conn:
        if conn.execute("SELECT COUNT(*) FROM exercises").fetchone()[0]:
            return []
        for position, (name, per_side, per_dumbbell, bodyweight) in enumerate(
                config.WORKOUT_EXERCISES, start=1):
            conn.execute(
                "INSERT INTO exercises (name, reps_mode, weight_mode, "
                "is_bodyweight, position) VALUES (?, ?, ?, ?, ?)",
                (name, "per_side" if per_side else "total",
                 "per_dumbbell" if per_dumbbell else "total",
                 1 if bodyweight else 0, position))
            added.append(name)
        db.log(conn, "migrate", "exercises", None,
               f"seeded {len(added)} exercises")
    return added
