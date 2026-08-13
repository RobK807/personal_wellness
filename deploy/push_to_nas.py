"""Push code changes from this PC to the NAS.

    python deploy/push_to_nas.py              # code only - the safe default
    python deploy/push_to_nas.py --with-db    # also replace the database
    python deploy/push_to_nas.py --dry-run    # show what would change

**The database is never touched unless you ask for it.** Once the dashboard is
in use, the copy on the NAS is the live one and the copy here is stale - pushing
it would silently destroy every weigh-in entered from your phone. `--with-db` is
for the initial deployment, or after a deliberate re-import from the workbook,
and it refuses to run while the app is up.

Two things a drag-and-drop copy gets wrong, which this handles:

  * line endings - DSM's /bin/sh fails on CRLF with misleading "not found"
    errors, so all text files are written LF
  * the database - wellness.db has -wal/-shm sidecars, so copying the file
    alone can capture an inconsistent snapshot; sqlite3's backup API does it
    properly
"""
from __future__ import annotations

import argparse
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
DST = Path(r"\\SynoRk807\dashboards\personal_wellness")
NAS_HOST, NAS_PORT = "SynoRk807", 8503

TEXT_SUFFIXES = {".sh", ".py", ".md", ".txt", ".html", ".css", ".js", ".sql",
                 ".toml", ".yml", ".yaml", ".cfg", ".ini", ".json"}
TEXT_NAMES = {".gitignore", "Dockerfile"}

# Never pushed: caches, machine-local state, and anything the NAS owns.
#
# strava_webscrape/ is the scraper and its raw output. It runs on a desktop,
# against Strava, and nothing the NAS serves reads it - the runs are in the
# database by the time they get there.
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".venv", "venv", "exports",
             ".streamlit", ".claude", "strava_webscrape"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".db-wal", ".db-shm", ".key", ".log", ".pid"}
SKIP_NAMES = {"password.txt"}

# The workbooks are a couple of MB between them and only the importers read
# them, which happens on a desktop. There is no reason for either to sit on the
# NAS.
SKIP_XLSX = True


def is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES


def app_is_running() -> bool:
    """Best-effort check that the dashboard is up, so we don't yank its data."""
    try:
        with socket.create_connection((NAS_HOST, NAS_PORT), timeout=4):
            return True
    except OSError:
        return False


def push_code(dry_run: bool) -> tuple:
    text = binary = 0
    skipped: list = []

    for src in sorted(SRC.rglob("*")):
        rel = src.relative_to(SRC)
        if any(part in SKIP_DIRS for part in rel.parts) or src.is_dir():
            continue
        if src.suffix.lower() in SKIP_SUFFIXES or src.name in SKIP_NAMES:
            skipped.append(str(rel))
            continue
        if src.suffix.lower() == ".db":
            continue  # handled separately, and only on request
        if SKIP_XLSX and src.suffix.lower() in (".xlsx", ".xlsm"):
            skipped.append(str(rel))
            continue

        dst = DST / rel
        if dry_run:
            marker = "new" if not dst.exists() else "update"
            print(f"    {marker:>6}  {rel}")
            text += 1
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        if is_text(src):
            body = src.read_text(encoding="utf-8").replace("\r\n", "\n")
            dst.write_bytes(body.encode("utf-8"))
            text += 1
        else:
            shutil.copy2(src, dst)
            binary += 1

    return text, binary, skipped


def push_database(force: bool) -> int:
    src_db = SRC / "data" / "wellness.db"
    dst_db = DST / "data" / "wellness.db"

    if not src_db.exists():
        print("  ! no local database to push")
        return 1

    if dst_db.exists() and not force:
        print("  ! the NAS already has a database - refusing to overwrite it.")
        print("    The NAS copy is the live one once the dashboard is in use.")
        print("    Pass --with-db --force if you really mean to replace it.")
        return 1

    if app_is_running():
        print(f"  ! the dashboard is still serving on {NAS_HOST}:{NAS_PORT}.")
        print("    Stop it first, or you risk corrupting the database:")
        print("      sh /volume1/dashboards/personal_wellness/deploy/stop.sh")
        return 1

    dst_db.parent.mkdir(parents=True, exist_ok=True)
    for sidecar in ("-wal", "-shm"):
        leftover = Path(str(dst_db) + sidecar)
        if leftover.exists():
            leftover.unlink()
    if dst_db.exists():
        dst_db.unlink()

    source, target = sqlite3.connect(src_db), sqlite3.connect(dst_db)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    check = sqlite3.connect(dst_db)
    try:
        readings = check.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        days = check.execute("SELECT COUNT(*) FROM v_daily").fetchone()[0]
        last = check.execute("SELECT MAX(day) FROM v_daily").fetchone()[0]
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    print(f"  database: {days} days, {readings} readings, latest {last}, "
          f"integrity={integrity}")
    return 0 if integrity == "ok" else 1


def verify_scripts() -> int:
    """CRLF in a shell script breaks DSM's /bin/sh in a very confusing way."""
    problems = 0
    for script in sorted(DST.rglob("*.sh")):
        crs = script.read_bytes().count(b"\r")
        print(f"  {script.relative_to(DST)}: {'ok' if not crs else f'{crs} CR BYTES'}")
        problems += bool(crs)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Push the dashboard to the NAS")
    parser.add_argument("--with-db", action="store_true",
                        help="also push the database (initial deploy only)")
    parser.add_argument("--force", action="store_true",
                        help="allow --with-db to replace an existing database")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be copied, change nothing")
    args = parser.parse_args()

    if not DST.parent.exists():
        print(f"Cannot reach {DST.parent} - is the NAS online?", file=sys.stderr)
        return 1

    print(f"{'Would copy' if args.dry_run else 'Copying'} {SRC}\n     -> {DST}\n")
    text, binary, skipped = push_code(args.dry_run)

    if args.dry_run:
        print(f"\n  {text} files would be copied")
        if skipped:
            print(f"  would skip: {', '.join(skipped)}")
        return 0

    print(f"  {text} text files (CRLF -> LF), {binary} binary")
    if skipped:
        print(f"  skipped: {', '.join(skipped)}")

    status = 0
    if args.with_db:
        print()
        status |= push_database(args.force)
    else:
        print("  database: left alone (use --with-db to push it)")

    print("\nVerifying shell scripts on the NAS:")
    status |= 1 if verify_scripts() else 0

    if not status:
        print("\nDone. Restart the app to pick up code changes:")
        print("  sh /volume1/dashboards/personal_wellness/deploy/stop.sh")
        print("  sh /volume1/dashboards/personal_wellness/deploy/run.sh")
    return status


if __name__ == "__main__":
    sys.exit(main())
