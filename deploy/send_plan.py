"""Send a workout plan from this PC's database to the NAS's.

    python deploy/send_plan.py                          # what is here, what is there
    python deploy/send_plan.py --plan "2026 Gym Programme"
    python deploy/send_plan.py --plan "..." --replace   # overwrite one of that name
    python deploy/send_plan.py --all
    python deploy/send_plan.py --plan "..." --dry-run

Why this exists
---------------
The other two sections got onto the NAS with their data: the weigh-ins were
adopted from the live database and the runs were imported before it went live.
A plan cannot follow either route. `core.gym_import` reads a workbook, the
workbooks are deliberately never pushed to the NAS, and openpyxl opening one
needs more memory than a DS218play has free - so a plan is always built or
imported on a desktop, and has to be carried across afterwards.

What it touches
---------------
**Only the eight workout tables, and only the rows belonging to the plans named.**
The weigh-ins, the runs, the run option lists and the audit log on the NAS are
never read from the source and never written. Nothing is deleted unless
`--replace` is given, and then only the one plan being replaced.

Ids are **not** carried across. The exercise catalogue exists independently on
both sides - both seeded from config.py, but either can have been edited since -
so movements are matched **by name**, and anything the target has not got is
added to its catalogue with the same flags. Everything else is re-created with
new ids and remapped as it goes. That is what makes this safe to run twice, and
safe against a NAS whose catalogue has drifted.

The risk, stated plainly
------------------------
This writes SQLite over SMB, which config.py warns against for good reason:
locking over a network share is not reliable when there is more than one writer.
Three things make it acceptable here and none of them is optional:

  * the dashboard must be stopped - the script refuses while port 8503 answers,
    so there is exactly one writer;
  * the target is backed up first, with sqlite3's backup API, which handles the
    -wal sidecar properly;
  * every prescribed weight is read back afterwards and compared against the
    source, so a partial write is caught rather than assumed not to have
    happened.

If any of that fails, restore the backup it printed the path of.
"""
from __future__ import annotations

import argparse
import datetime as dt
import socket
import sqlite3
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

import config  # noqa: E402
from core import db  # noqa: E402

TARGET_DB = Path(r"\\SynoRk807\dashboards\personal_wellness\data\wellness.db")
NAS_HOST, NAS_PORT = "SynoRk807", 8503


class TransferError(RuntimeError):
    """The transfer cannot safely go ahead."""


def app_is_running() -> bool:
    """Is the dashboard up? If so, this must not write to its database."""
    try:
        with socket.create_connection((NAS_HOST, NAS_PORT), timeout=4):
            return True
    except OSError:
        return False


def is_the_nas(target: Path) -> bool:
    """Does this target actually live on the NAS?

    The dashboard being up only matters if it is the same database. A UNC path
    is one spelling and the mapped drive is another, so the mapped one is caught
    by asking the filesystem whether the two names are the same file rather than
    by comparing the strings - which would not match, and would wave through a
    write to the live database while it was being served.

    Anything else is a local file: a rehearsal against a copy, which needs no
    permission from anybody.
    """
    if str(target).startswith("\\\\"):
        return True
    try:
        return target.exists() and TARGET_DB.exists() \
            and target.samefile(TARGET_DB)
    except OSError:
        return False


def connect(path: Path, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def backup(target: Path) -> Path:
    """A snapshot of the target before anything is written to it."""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = target.parent / f"{target.stem}.before-send-{stamp}.db"
    source = connect(target, read_only=True)
    try:
        copy = sqlite3.connect(destination)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    return destination


# --------------------------------------------------------------------------- #
# Reading one plan out of the source
# --------------------------------------------------------------------------- #
def read_plan(conn: sqlite3.Connection, plan_id: int) -> dict:
    """Everything belonging to one plan, as plain dicts."""
    plan = dict(conn.execute("SELECT * FROM plans WHERE id = ?",
                             (plan_id,)).fetchone())
    rows = {
        "phases": _rows(conn, "SELECT * FROM phases WHERE plan_id = ? "
                              "ORDER BY position, id", (plan_id,)),
        "weeks": _rows(conn, "SELECT * FROM weeks WHERE plan_id = ? "
                             "ORDER BY number", (plan_id,)),
        "maxes": _rows(conn,
                       "SELECT m.*, e.name FROM plan_maxes m "
                       "JOIN exercises e ON e.id = m.exercise_id "
                       "WHERE m.plan_id = ?", (plan_id,)),
    }
    week_ids = [row["id"] for row in rows["weeks"]]
    rows["sessions"] = _in(conn, "SELECT * FROM sessions WHERE week_id IN",
                           week_ids, "ORDER BY week_id, number")
    session_ids = [row["id"] for row in rows["sessions"]]
    rows["session_exercises"] = _in(
        conn,
        "SELECT sx.*, e.name, e.reps_mode AS ex_reps_mode, "
        "e.weight_mode AS ex_weight_mode, e.is_bodyweight, e.note AS ex_note "
        "FROM session_exercises sx JOIN exercises e ON e.id = sx.exercise_id "
        "WHERE sx.session_id IN", session_ids, "ORDER BY sx.session_id, "
                                               "sx.position")
    sx_ids = [row["id"] for row in rows["session_exercises"]]
    rows["exercise_sets"] = _in(
        conn, "SELECT * FROM exercise_sets WHERE session_exercise_id IN",
        sx_ids, "ORDER BY session_exercise_id, set_type, position")
    rows["session_log"] = _in(conn, "SELECT * FROM session_log "
                                    "WHERE session_id IN", session_ids, "")
    return {"plan": plan, **rows}


def _rows(conn, sql: str, params=()) -> list:
    return [dict(row) for row in conn.execute(sql, params)]


def _in(conn, sql: str, ids: list, tail: str) -> list:
    """A WHERE ... IN over a list of ids, chunked so SQLite's limit is safe."""
    if not ids:
        return []
    out = []
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ", ".join("?" * len(chunk))
        out += _rows(conn, f"{sql} ({marks}) {tail}", chunk)
    return out


def prescribed(conn: sqlite3.Connection, plan_id: int) -> list:
    """Every prescribed weight, as a comparable list. The proof it arrived.

    The movement's name is compared case-insensitively, because the target's
    spelling of it is allowed to differ and is the one that wins: matching by
    name is what makes this safe against a catalogue that has drifted, so a
    target holding 'ohp' where this database holds 'OHP' has matched correctly
    rather than failed. Everything else - the set's place in the plan, its reps,
    how its weight is prescribed and what that comes to in kilograms - has to be
    identical, and that is what this is checking.
    """
    return [tuple(row) for row in conn.execute("""
        SELECT week_number, session_number, lower(exercise_name), set_type,
               position, reps_low, reps_high, load_mode, prescribed_kg
        FROM v_exercise_sets WHERE plan_id = ?
        ORDER BY week_number, session_number, exercise_position, set_type,
                 position
    """, (plan_id,))]


# --------------------------------------------------------------------------- #
# Writing it into the target
# --------------------------------------------------------------------------- #
def map_exercises(target: sqlite3.Connection, needed: list) -> tuple:
    """Source exercise id -> target exercise id, matching on name.

    Anything the target does not have is added to its catalogue with the flags
    it has here. Names are matched case-insensitively, because that is the rule
    the catalogue itself enforces.
    """
    mapping: dict = {}
    added: list = []
    for row in needed:
        found = target.execute(
            "SELECT id FROM exercises WHERE name = ? COLLATE NOCASE",
            (row["name"],)).fetchone()
        if found is not None:
            mapping[row["exercise_id"]] = found["id"]
            continue
        position = target.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM exercises"
        ).fetchone()[0]
        cursor = target.execute(
            "INSERT INTO exercises (name, reps_mode, weight_mode, "
            "is_bodyweight, position, note) VALUES (?, ?, ?, ?, ?, ?)",
            (row["name"], row["ex_reps_mode"], row["ex_weight_mode"],
             row["is_bodyweight"], position, row["ex_note"]))
        mapping[row["exercise_id"]] = cursor.lastrowid
        added.append(row["name"])
    return mapping, added


def write_plan(target: sqlite3.Connection, data: dict, replace: bool) -> dict:
    """Insert one plan and everything under it. Caller owns the transaction.

    The order below is forced: plans, then the exercise mapping, then
    plan_maxes, phases, weeks, sessions, session_exercises, exercise_sets and
    session_log. Each needs the ids the one above it has just produced, which is
    why every step keeps a map from the source's id to the target's rather than
    carrying ids across.
    """
    plan = data["plan"]
    existing = target.execute(
        "SELECT id FROM plans WHERE name = ? COLLATE NOCASE",
        (plan["name"],)).fetchone()
    if existing is not None:
        if not replace:
            raise TransferError(
                f"'{plan['name']}' is already in the target database. Pass "
                f"--replace to overwrite it, or rename one of the two - the "
                f"name is how a plan is found again, so they cannot share one")
        # Cascades down to weeks, sessions, sets and the tick-offs.
        target.execute("DELETE FROM plans WHERE id = ?", (existing["id"],))

    cursor = target.execute(
        "INSERT INTO plans (name, started_on, rounding_kg, note, archived, "
        "source) VALUES (?, ?, ?, ?, ?, ?)",
        (plan["name"], plan["started_on"], plan["rounding_kg"], plan["note"],
         plan["archived"], plan["source"]))
    plan_id = cursor.lastrowid

    needed = {row["exercise_id"]: row for row in data["session_exercises"]}
    for row in data["maxes"]:
        needed.setdefault(row["exercise_id"], {
            "exercise_id": row["exercise_id"], "name": row["name"],
            "ex_reps_mode": "total", "ex_weight_mode": "total",
            "is_bodyweight": 0, "ex_note": None})
    exercise_map, added = map_exercises(target, list(needed.values()))

    for row in data["maxes"]:
        target.execute(
            "INSERT INTO plan_maxes (plan_id, exercise_id, one_rm_kg) "
            "VALUES (?, ?, ?)",
            (plan_id, exercise_map[row["exercise_id"]], row["one_rm_kg"]))

    phase_map: dict = {}
    for row in data["phases"]:
        cursor = target.execute(
            "INSERT INTO phases (plan_id, name, focus, position, warmup_pcts, "
            "working_pcts, working_sets, working_reps, accessory_sets, "
            "accessory_reps, rest_warmup, rest_working, rest_accessory) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (plan_id, row["name"], row["focus"], row["position"],
             row["warmup_pcts"], row["working_pcts"], row["working_sets"],
             row["working_reps"], row["accessory_sets"], row["accessory_reps"],
             row["rest_warmup"], row["rest_working"], row["rest_accessory"]))
        phase_map[row["id"]] = cursor.lastrowid

    week_map: dict = {}
    for row in data["weeks"]:
        cursor = target.execute(
            "INSERT INTO weeks (plan_id, number, label, phase_id, cycle_type, "
            "note) VALUES (?, ?, ?, ?, ?, ?)",
            (plan_id, row["number"], row["label"],
             phase_map.get(row["phase_id"]), row["cycle_type"], row["note"]))
        week_map[row["id"]] = cursor.lastrowid

    session_map: dict = {}
    for row in data["sessions"]:
        cursor = target.execute(
            "INSERT INTO sessions (week_id, number, name, note) "
            "VALUES (?, ?, ?, ?)",
            (week_map[row["week_id"]], row["number"], row["name"],
             row["note"]))
        session_map[row["id"]] = cursor.lastrowid

    sx_map: dict = {}
    for row in data["session_exercises"]:
        cursor = target.execute(
            "INSERT INTO session_exercises (session_id, exercise_id, position, "
            "reps_mode, weight_mode, note) VALUES (?, ?, ?, ?, ?, ?)",
            (session_map[row["session_id"]], exercise_map[row["exercise_id"]],
             row["position"], row["reps_mode"], row["weight_mode"],
             row["note"]))
        sx_map[row["id"]] = cursor.lastrowid

    target.executemany(
        "INSERT INTO exercise_sets (session_exercise_id, set_type, position, "
        "reps_low, reps_high, load_mode, weight_kg, percent_1rm, added_kg, "
        "rest, cue) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(sx_map[row["session_exercise_id"]], row["set_type"], row["position"],
          row["reps_low"], row["reps_high"], row["load_mode"],
          row["weight_kg"], row["percent_1rm"], row["added_kg"], row["rest"],
          row["cue"]) for row in data["exercise_sets"]])

    target.executemany(
        "INSERT INTO session_log (session_id, done_on, note) VALUES (?, ?, ?)",
        [(session_map[row["session_id"]], row["done_on"], row["note"])
         for row in data["session_log"]])

    return {
        "plan_id": plan_id,
        "name": plan["name"],
        "replaced": existing is not None,
        "exercises_added": added,
        "phases": len(data["phases"]),
        "weeks": len(data["weeks"]),
        "sessions": len(data["sessions"]),
        "sets": len(data["exercise_sets"]),
        "ticked": len(data["session_log"]),
        "maxes": len(data["maxes"]),
    }


# --------------------------------------------------------------------------- #
# Driving it
# --------------------------------------------------------------------------- #
def send(names: list, target_path: Path, replace: bool,
         dry_run: bool, do_backup: bool = True) -> list:
    source = connect(config.DB_PATH, read_only=True)
    try:
        wanted = []
        for name in names:
            row = source.execute(
                "SELECT id, name FROM plans WHERE name = ? COLLATE NOCASE",
                (name,)).fetchone()
            if row is None:
                raise TransferError(f"No plan called '{name}' in {config.DB_PATH}")
            wanted.append((row["id"], row["name"]))

        payloads = [(read_plan(source, plan_id), prescribed(source, plan_id))
                    for plan_id, _ in wanted]
    finally:
        source.close()

    for (data, _), (_, name) in zip(payloads, wanted):
        print(f"  {name}: {len(data['weeks'])} weeks, "
              f"{len(data['sessions'])} sessions, "
              f"{len(data['exercise_sets'])} sets, "
              f"{len(data['session_log'])} ticked off")

    if dry_run:
        print("\n--dry-run: nothing written.")
        return []

    if do_backup:
        saved = backup(target_path)
        print(f"\nBacked up the target -> {saved} "
              f"({saved.stat().st_size / 1024 / 1024:.1f} MB)")

    # Brings the target up to the current schema, so this works whether or not
    # the NAS has been restarted since the code was pushed.
    db.init_db(target_path)

    results = []
    target = connect(target_path)
    try:
        target.execute("BEGIN IMMEDIATE")
        for data, _ in payloads:
            results.append(write_plan(target, data, replace))
        db.log(target, "send_plan", "plans", None,
               "from " + str(config.DB_PATH) + ": "
               + ", ".join(f"{r['name']} ({r['weeks']} weeks, {r['sets']} sets)"
                           for r in results))
        target.execute("COMMIT")
    except Exception:
        target.execute("ROLLBACK")
        target.close()
        raise
    target.close()

    # --- the proof -------------------------------------------------------
    print("\nReading it back")
    target = connect(target_path, read_only=True)
    try:
        for result, (_, expected) in zip(results, payloads):
            got = prescribed(target, result["plan_id"])
            result["verified"] = got == expected
            if got == expected:
                print(f"  {result['name']}: all {len(got)} sets match, "
                      f"prescribed weights included")
            else:
                print(f"  {result['name']}: MISMATCH - {len(expected)} sets "
                      f"here, {len(got)} there")
                for mine, theirs in zip(expected, got):
                    if mine != theirs:
                        print(f"     first difference:\n"
                              f"       here  {mine}\n       there {theirs}")
                        break
    finally:
        target.close()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a workout plan from this PC's database to the NAS's")
    parser.add_argument("--plan", action="append", default=[],
                        help="plan name; repeat for more than one")
    parser.add_argument("--all", action="store_true",
                        help="send every plan in the local database")
    parser.add_argument("--target", type=Path, default=TARGET_DB)
    parser.add_argument("--replace", action="store_true",
                        help="overwrite a plan of the same name on the NAS")
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
        here = [dict(row) for row in local.execute(
            "SELECT p.id, p.name, "
            "  (SELECT COUNT(*) FROM weeks w WHERE w.plan_id = p.id) AS weeks "
            "FROM plans p ORDER BY p.id")]
    except sqlite3.OperationalError:
        here = []
    finally:
        local.close()

    if not here:
        print("There are no plans in the local database to send.")
        print("  python -m core.gym_import      # import the gym workbook first")
        return 1

    names = [row["name"] for row in here] if args.all else args.plan
    if not names:
        print(f"Plans in {config.DB_PATH}:\n")
        for row in here:
            print(f"  {row['name']}  ({row['weeks']} weeks)")
        print(f"\nTarget: {args.target}")
        print("\nPick one:")
        print(f'  python deploy/send_plan.py --plan "{here[0]["name"]}"')
        return 0

    remote = is_the_nas(args.target)
    if remote and app_is_running():
        print(f"The dashboard is answering on {NAS_HOST}:{NAS_PORT}.\n\n"
              f"This writes SQLite over SMB, which is only safe with exactly "
              f"one writer. Stop it first:\n"
              f"  sh /volume1/dashboards/personal_wellness/deploy/stop.sh",
              file=sys.stderr)
        return 1

    print(f"Sending from {config.DB_PATH}\n          -> {args.target}\n")
    try:
        results = send(names, args.target, args.replace, args.dry_run,
                       do_backup=not args.no_backup)
    except TransferError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    if not results:
        return 0

    print()
    failed = [row for row in results if not row["verified"]]
    for row in results:
        print(f"{'Replaced' if row['replaced'] else 'Added'} '{row['name']}' "
              f"(plan {row['plan_id']}): {row['weeks']} weeks, "
              f"{row['sessions']} sessions, {row['sets']} sets, "
              f"{row['maxes']} 1RMs, {row['ticked']} ticked off")
        if row["exercises_added"]:
            print(f"  exercises added to the target catalogue: "
                  f"{', '.join(row['exercises_added'])}")
    if failed:
        print("\nSomething did not arrive intact. Restore the backup above.",
              file=sys.stderr)
        return 1

    if remote:
        print(f"\nStart the dashboard again:\n"
              f"  sh /volume1/dashboards/personal_wellness/deploy/run.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
