"""Take the live weigh-in database over as the base of wellness.db.

    python deploy/adopt_weigh_ins.py --dry-run     # look, change nothing
    python deploy/adopt_weigh_ins.py               # from the NAS
    python deploy/adopt_weigh_ins.py --source PATH # from somewhere else

Why this exists
---------------
The personal wellness dashboard's weigh-in section *is* the standalone tracker,
and `data/wellness.db` was seeded from a copy of `weigh_ins.db` taken when this
was built. Every weigh-in entered since then went into the tracker that is
still running, not into that copy - so the copy is stale, and pushing it to the
NAS would silently destroy however many days of readings have accumulated.

This script goes the other way. It takes the live weigh-in database, makes it
the base of wellness.db, and puts the run tracker back on top of it.

Rebuild rather than merge
-------------------------
The runs are not merged in, they are re-imported. Every run in wellness.db came
from `Final_data` and can be rebuilt from it exactly - the script checks that
before it starts and refuses if it finds a run entered by hand, because that
one would be the thing a rebuild lost.

Merging two databases means reconciling two audit logs, two sets of
back-filled estimates and two ideas of which readings are real. Rebuilding
means copying one file and running one importer. The second is the one that
can be checked by looking at it.

Order of operations
-------------------
Stop the old dashboard *first*. The backup API will happily read a database
that is being written to, and the copy will be consistent - but a weigh-in
entered after the copy and before the switch lands in a database nothing will
read again.

    ssh into the NAS
    sh /volume1/dashboards/weigh_in_tracker/deploy/stop.sh
    (back here) python deploy/adopt_weigh_ins.py
    python run_test.py && python reconcile_test.py
    python deploy/push_to_nas.py --with-db
    sh /volume1/dashboards/personal_wellness/deploy/run.sh
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

import config  # noqa: E402

# Where the standalone tracker keeps its live database. The NAS copy is the
# live one - that is where the phone has been writing.
#
# Both the UNC path and the mapped drive are tried, because which of them works
# depends on whether Windows currently has a session open to the share, and
# that is not something worth having to think about mid-migration.
NAS_CANDIDATES = [
    Path(r"Z:\weigh_in_tracker\data\weigh_ins.db"),
    Path(r"\\SynoRk807\dashboards\weigh_in_tracker\data\weigh_ins.db"),
]
PC_SOURCE = APP.parent / "weigh_in_tracker" / "data" / "weigh_ins.db"


def find_live() -> Path:
    """The first NAS path that resolves, or the last one tried for the error."""
    for candidate in NAS_CANDIDATES:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return NAS_CANDIDATES[-1]

# The old dashboard's port. If something is still answering there, it is still
# running, and a reading entered after the copy would be stranded.
OLD_HOST, OLD_PORT = "SynoRk807", 8502


def old_app_is_running() -> bool:
    try:
        with socket.create_connection((OLD_HOST, OLD_PORT), timeout=2):
            return True
    except OSError:
        return False


def summarise(path: Path) -> dict:
    """What a weigh-in database contains, read without locking it."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "readings" not in tables:
            raise SystemExit(f"{path} has no `readings` table - that is not a "
                             f"weigh-in database")
        days, last = conn.execute(
            "SELECT COUNT(*), MAX(day) FROM v_daily").fetchone()
        return {
            "days": days,
            "last_day": last,
            "readings": conn.execute(
                "SELECT COUNT(*) FROM readings WHERE estimated = 0").fetchone()[0],
            "estimated": conn.execute(
                "SELECT COUNT(*) FROM readings WHERE estimated = 1").fetchone()[0],
            "runs": (conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
                     if "runs" in tables else 0),
            "hand_entered_runs": (conn.execute(
                "SELECT COUNT(*) FROM runs WHERE source <> 'strava'").fetchone()[0]
                if "runs" in tables else 0),
        }
    finally:
        conn.close()


def compare(source: dict, target: dict) -> None:
    print(f"\n{'':<20}{'live tracker':>16}{'wellness.db':>16}")
    for field, label in (("days", "days recorded"), ("readings", "real readings"),
                         ("estimated", "interpolated"), ("last_day", "latest day"),
                         ("runs", "runs")):
        print(f"  {label:<18}{str(source[field]):>16}{str(target[field]):>16}")


def adopt(source: Path, dry_run: bool) -> int:
    if not source.exists():
        print(f"Cannot read {source}", file=sys.stderr)
        print("Is the NAS online and the share mapped? Pass --source to point "
              "at a copy somewhere else.", file=sys.stderr)
        return 1

    target = Path(config.DB_PATH)
    live = summarise(source)
    current = summarise(target) if target.exists() else {
        "days": 0, "last_day": None, "readings": 0, "estimated": 0,
        "runs": 0, "hand_entered_runs": 0}

    print(f"live tracker : {source}")
    print(f"wellness.db  : {target}")
    compare(live, current)

    # The one thing a rebuild would lose.
    if current["hand_entered_runs"]:
        print(f"\nRefusing: wellness.db holds {current['hand_entered_runs']} run(s) "
              f"that were entered by hand rather than imported. A rebuild would "
              f"lose them. Export or re-enter them first.", file=sys.stderr)
        return 1

    # Going backwards is almost certainly a mistake, and it is the mistake this
    # whole script exists to prevent, so it is checked rather than assumed.
    if (current["last_day"] and live["last_day"]
            and live["last_day"] < current["last_day"]):
        print(f"\nRefusing: the live tracker's latest reading "
              f"({live['last_day']}) is older than wellness.db's "
              f"({current['last_day']}). That is the wrong direction - check "
              f"you are pointing at the right database.", file=sys.stderr)
        return 1

    gained = live["readings"] - current["readings"]
    print(f"\n  adopting the live database would bring across "
          f"{gained:+d} real reading(s)"
          + (f", up to {live['last_day']}" if gained > 0 else ""))

    if old_app_is_running():
        print(f"\nRefusing: something is still answering on "
              f"{OLD_HOST}:{OLD_PORT}, so the old dashboard is still up. Stop "
              f"it first, or a weigh-in entered between now and the switch "
              f"will be stranded in a database nothing reads again:\n"
              f"  sh /volume1/dashboards/weigh_in_tracker/deploy/stop.sh",
              file=sys.stderr)
        return 1

    if dry_run:
        print("\n--dry-run: nothing changed")
        return 0

    # Keep what is being replaced, named so it is obvious what it was.
    if target.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        keep = target.with_name(f"{target.stem}.before-adopt-{stamp}.db")
        shutil.copy2(target, keep)
        print(f"\n  previous wellness.db kept as {keep.name}")

    # The backup API rather than a file copy: WAL means recent commits can live
    # in the -wal sidecar, and copying the .db alone would leave them behind.
    target.parent.mkdir(parents=True, exist_ok=True)
    for sidecar in ("-wal", "-shm"):
        Path(str(target) + sidecar).unlink(missing_ok=True)
    target.unlink(missing_ok=True)

    reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    writer = sqlite3.connect(target)
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()
    print(f"  copied the live weigh-in database to {target.name}")

    # init_db adds the run tables alongside; the importer refills them.
    from core import db, strava_import

    db.init_db()
    print("  run tables created alongside the readings")

    result = strava_import.run_import(rebuild=True)
    print(f"  re-imported {result['runs']} runs and {result['splits']} splits "
          f"from {config.RUNS_SHEET}")

    after = summarise(target)
    compare(live, after)

    conn = sqlite3.connect(target)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    print(f"\n  integrity_check: {integrity}")

    ok = (after["readings"] == live["readings"]
          and after["days"] == live["days"]
          and after["last_day"] == live["last_day"]
          and after["runs"] == result["runs"]
          and integrity == "ok")
    if not ok:
        print("\nSomething does not add up - the previous wellness.db is still "
              "beside this one. Do not push.", file=sys.stderr)
        return 1

    print("\nDone. Every reading came across and the runs are back on top.\n"
          "Next:\n"
          "  python run_test.py && python reconcile_test.py\n"
          "  python deploy/push_to_nas.py --with-db\n"
          "  then start it on the NAS:\n"
          "  sh /volume1/dashboards/personal_wellness/deploy/run.sh")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", type=Path, default=None,
                        help="the live weigh_ins.db (default: find it on the NAS)")
    parser.add_argument("--from-pc", action="store_true",
                        help=f"use {PC_SOURCE} instead of the NAS")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.from_pc:
        source = PC_SOURCE
    else:
        source = args.source or find_live()
    return adopt(source, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
