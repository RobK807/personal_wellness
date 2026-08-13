"""Check the run tracker against the spreadsheet it was built from.

    python run_test.py

The weigh-in tracker has reconcile_test.py, which recomputes every derived
series and compares it against the cached values in the workbook. This is the
same idea for the runs: read Final_data directly, fold it into runs and splits
here, and assert that the database holds exactly that - and that everything
derived from it agrees with the sheet's own derived columns.

Four things are checked.

**The fold.** 1,546 rows become 229 runs and 1,542 splits, and every run's
date, distance, elapsed time, run type and effort type match the sheet.

**Pace.** The sheet's *Pace (min/km)* column reproduces exactly, on all 1,546
rows, because it was itself computed from the rounded time and distance. The
*Breakdown pace* column does not, and the test asserts how far off it is rather
than tolerating it - see the note in core/runs.py. If that ever changes, this
fails and says so.

**The impossible splits.** Eighteen of them, in two kinds. They are asserted
rather than tolerated: if the sheet is fixed and one stops being true, this
fails, which is the prompt to update the wording that explains them.

**The records.** The top five at each distance, recomputed here from the sheet
rather than read from the database, and compared.

It runs against a throwaway copy, so the real database is untouched.
"""
from __future__ import annotations

import collections
import datetime as dt
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

TEMP_DB = Path(tempfile.gettempdir()) / "wellness_run_test.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)
os.environ["PW_DB_PATH"] = str(TEMP_DB)

import config  # noqa: E402
from core import db, run_queries, runs, strava_import  # noqa: E402

failures: list = []


def check(label: str, actual, expected) -> None:
    ok = actual == expected
    shown = repr(actual)
    if len(shown) > 300:
        shown = shown[:300] + "..."
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {shown}"
          + ("" if ok else f"\n         expected {expected!r}"))
    if not ok:
        failures.append(label)


def secs(value) -> int:
    """Whatever openpyxl handed back, as whole seconds.

    A bare number is a fraction of a day, because that is how Excel stores a
    duration in a cell whose number format has been lost. Written out here
    rather than borrowed from core.strava_import for the reason in fold().
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(round(float(value) * 86400))
    return runs.parse_duration(value)


def day_of(value) -> dt.date:
    """A date, from a formatted cell or from a bare Excel day count."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(value))
    return runs.as_date(value)


EXCEL_ERRORS = {"#N/A", "#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#NULL!",
                "#NUM!", "#SPILL!", "#CALC!"}


def classify(value) -> str:
    """A run or effort type, with a failed lookup called what it is."""
    text = str(value).strip()
    return "Unclassified" if text in EXCEL_ERRORS else text


def mmss(seconds: float) -> str:
    """The sheet's mm:ss, from a number of seconds per kilometre."""
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def read_sheet() -> list:
    """Final_data as it stands, without going through the importer."""
    import openpyxl

    workbook = openpyxl.load_workbook(config.RUNS_XLSX, data_only=True,
                                      read_only=True)
    try:
        rows = list(workbook[config.RUNS_SHEET].iter_rows(min_row=2,
                                                          max_col=9,
                                                          values_only=True))
    finally:
        workbook.close()
    return [row for row in rows if row[0] is not None]


def fold(rows: list) -> dict:
    """The same fold the importer does, written out again independently.

    Deliberately not a call into core.strava_import: a test that reuses the
    code it is testing to work out the right answer will agree with it however
    wrong they both are.
    """
    grouped: dict = collections.OrderedDict()
    for row in rows:
        key = (day_of(row[0]), round(float(row[1]), 2), secs(row[3]))
        entry = grouped.setdefault(key, {
            "run_type": classify(row[7]),
            "effort_type": classify(row[8]),
            "pace": str(row[2]).strip(),
            "splits": {},
        })
        label = row[4]
        if label in (None, 0) or str(label).strip() in ("", "0"):
            continue
        entry["splits"][str(label).strip()] = secs(row[5])
    return grouped


def main() -> int:
    print(f"Reading {config.RUNS_XLSX.name} [{config.RUNS_SHEET}]")
    rows = read_sheet()
    sheet = fold(rows)
    print(f"{len(rows):,} rows fold into {len(sheet):,} runs\n")

    result = strava_import.run_import(db_path=TEMP_DB, rebuild=True)

    # --- the fold ------------------------------------------------------------
    print("Runs and splits")
    check("runs imported", result["runs"], len(sheet))
    check("runs in the database", run_queries.total_runs(), len(sheet))
    check("splits imported", result["splits"],
          sum(len(entry["splits"]) for entry in sheet.values()))
    check("splits in the database",
          db.scalar("SELECT COUNT(*) FROM run_bests", default=0),
          result["splits"])
    check("runs with no breakdown at all", result["without_splits"], 3)
    # One run's type and effort come through as #N/A: Clean_data looks both up
    # with OFFSET/MATCH over a fixed range and this run's id falls outside it.
    # The run is imported - it happened - with its classification recorded as
    # unknown rather than as the error text.
    check("runs the sheet could not classify",
          result["runs_the_sheet_could_not_classify"], 1)
    check("and which one",
          [(r["day"], r["distance_km"]) for r in
           run_queries.runs_list(run_type="Unclassified")],
          [("2025-09-08", 4.15)])
    # The oldest 51 rows have lost their date and time number formats, so they
    # arrive as bare Excel serials. The values are intact and the importer
    # converts them; this asserts that it is still having to.
    check("rows with no date format", result["rows_missing_a_date_format"], 51)

    stored = {(runs.as_date(row["day"]), row["distance_km"], row["duration_s"]):
              row for row in run_queries.runs_list(newest_first=False)}
    check("every run from the sheet is there", sorted(stored) == sorted(sheet),
          True)

    wrong_type = [key for key, entry in sheet.items()
                  if stored[key]["run_type"] != entry["run_type"]
                  or stored[key]["effort_type"] != entry["effort_type"]]
    check("run and effort types match", wrong_type, [])

    wrong_ladder = []
    for key, entry in sheet.items():
        held = {row["breakdown"]: row["seconds"]
                for row in run_queries.bests_for(stored[key]["id"])}
        if held != entry["splits"]:
            wrong_ladder.append(key)
    check("every ladder matches", wrong_ladder, [])

    # --- pace ----------------------------------------------------------------
    print("\nPace")
    # Column C of Final_data is a pasted value, not a formula, so correcting a
    # run's Time in the sheet leaves the pace beside it reading the old figure.
    # Sixteen runs were corrected and none of their paces was re-pasted. The
    # dashboard derives pace from the distance and the time and never reads
    # this column, so nothing downstream is wrong - but re-pasting it would
    # make the sheet agree with itself, and this says so until it does.
    run_mismatches = sorted(key[0] for key, entry in sheet.items()
                            if mmss(stored[key]["pace_s"]) != entry["pace"])
    check("runs whose pasted pace is stale", len(run_mismatches), 16)
    check("and they are the ones whose time was corrected",
          run_mismatches,
          [dt.date(2019, 9, 29), dt.date(2019, 10, 6), dt.date(2021, 10, 9),
           dt.date(2021, 10, 31), dt.date(2021, 12, 6), dt.date(2023, 2, 4),
           dt.date(2023, 4, 8), dt.date(2023, 4, 15), dt.date(2025, 2, 2),
           dt.date(2025, 3, 9), dt.date(2025, 7, 6), dt.date(2025, 7, 25),
           dt.date(2026, 5, 16), dt.date(2026, 6, 7), dt.date(2026, 7, 11),
           dt.date(2026, 7, 12)])

    off_by = collections.Counter()
    truncated = []
    for row in rows:
        label = row[4]
        if label in (None, 0) or str(label).strip() in ("", "0"):
            continue
        derived = secs(row[5]) / config.BREAKDOWN_KM[str(label).strip()]
        sheet_pace = str(row[6]).strip()
        if mmss(derived) == sheet_pace:
            continue
        minutes, seconds = sheet_pace.split(":")
        # The sheet used to take a fixed four characters out of Strava's
        # "5:16/km", which is one short of what a pace of ten minutes or more
        # needs, so "12:35/km" came out as "12:3" - eleven rows of it, all on
        # the three slowest runs. strava_runs!Q and !U now cut at the "/"
        # instead, and this asserts that they stay that way. The dashboard
        # never reads the column - it derives pace from the split and the
        # distance - so the check is about the sheet, not about the import.
        if len(seconds) != 2:
            truncated.append((row[0].date(), str(label).strip(), sheet_pace,
                              mmss(derived)))
            continue
        off_by[int(minutes) * 60 + int(seconds) - round(derived)] += 1

    # Strava's own figure comes from a time it knows to better than a second;
    # the sheet kept only the whole second. Everything else is within one.
    check("breakdown paces otherwise differ only by rounding",
          sorted(off_by), [-1, 1])
    check("and only on 100 of them", sum(off_by.values()), 100)
    check("no pace is truncated any more", truncated, [])

    # --- the impossible splits ------------------------------------------------
    # Eighteen splits used to be longer than the run they sat inside. The run
    # times were corrected in the sheet on 12/08/2026 and all eighteen are
    # gone. Both rules stay asserted at zero rather than being deleted: they
    # are what the input form enforces, and a future scrape that reintroduces
    # one should surface here rather than at the top of a records table.
    print("\nSplits that cannot be true")
    anomalies = run_queries.anomalies()
    by_reason = collections.Counter(row["reason"] for row in anomalies)
    check("longer than the run they are inside",
          by_reason["Longer than the run it is inside"], 0)
    check("quicker than a shorter split of the same run",
          by_reason["Quicker than a shorter split of the same run"], 0)
    check("nothing flagged at all", run_queries.suspect_count(), 0)
    check("they are kept, not dropped",
          db.scalar("SELECT COUNT(*) FROM run_bests", default=0),
          result["splits"])
    check("and left out of the rankings",
          any(row.get("suspect")
              for table in run_queries.records().values() for row in table),
          False)

    # --- the records ----------------------------------------------------------
    print("\nRecords")
    # Recomputed from the sheet, skipping the same impossible splits.
    suspect = {(row["run_id"], row["breakdown"]) for row in anomalies}
    candidates: dict = collections.defaultdict(list)
    for key, entry in sheet.items():
        run_id = stored[key]["id"]
        for label, seconds in entry["splits"].items():
            if (run_id, label) in suspect:
                continue
            candidates[label].append((seconds, key[0], run_id))

    expected = {}
    for label in config.BREAKDOWN_LABELS:
        if label in candidates:
            expected[label] = sorted(candidates[label])[:config.TOP_N]

    actual = {label: [(row["seconds"], runs.as_date(row["day"]), row["run_id"])
                      for row in table]
              for label, table in run_queries.records().items()}
    check("the same distances have tables", sorted(actual), sorted(expected))
    for label in expected:
        check(f"{label} top {config.TOP_N}", actual.get(label), expected[label])

    check("no run holds two places in one table",
          [label for label, table in run_queries.records().items()
           if len({row["run_id"] for row in table}) != len(table)], [])

    # --- totals ---------------------------------------------------------------
    print("\nTotals")
    coverage = run_queries.coverage()
    check("total distance",
          coverage["distance_km"],
          round(sum(key[1] for key in sheet), 2))
    check("total time", coverage["duration_s"], sum(key[2] for key in sheet))
    check("first day", coverage["first_day"], min(sheet)[0].isoformat())
    check("last day", coverage["last_day"], max(sheet)[0].isoformat())

    grouped = collections.Counter(entry["run_type"] for entry in sheet.values())
    check("runs per run type",
          {row["label"]: row["runs"] for row in run_queries.by_run_type()},
          dict(grouped))
    grouped = collections.Counter(entry["effort_type"]
                                  for entry in sheet.values())
    check("runs per effort type",
          {row["label"]: row["runs"] for row in run_queries.by_effort_type()},
          dict(grouped))

    # --- re-importing is safe --------------------------------------------------
    print("\nRe-importing")
    again = strava_import.run_import(db_path=TEMP_DB, rebuild=False)
    check("no duplicates", run_queries.total_runs(), len(sheet))
    check("same splits", db.scalar("SELECT COUNT(*) FROM run_bests", default=0),
          again["splits"])

    # Interval detail is entered by hand and is nowhere in the sheet, so a
    # --rebuild must not take it away with the rows it deletes and recreates.
    print("\nHand-entered interval detail survives a rebuild")
    target = [row for row in run_queries.runs_list(run_type="Intervals")
              if row["distance_km"] >= 8][0]
    with db.transaction(TEMP_DB) as conn:
        conn.execute(
            "UPDATE runs SET interval_type = 'distance', interval_count = 8, "
            "interval_distance_m = 1000, interval_pace_s = 230 WHERE id = ?",
            (target["id"],))
    check("recorded against a run", runs.interval_summary(
        run_queries.run(target["id"])), "8 x 1k @ 3:50 /km")

    rebuilt = strava_import.run_import(db_path=TEMP_DB, rebuild=True)
    check("put back after a full rebuild", rebuilt["interval_sessions_kept"], 1)
    check("none orphaned", rebuilt["interval_sessions_orphaned"], 0)
    # The rebuild recreates the row, so the id changes - find it by identity.
    same = [row for row in run_queries.runs_on(target["day"])
            if row["distance_km"] == target["distance_km"]][0]
    check("and still reads the same",
          runs.interval_summary(same), "8 x 1k @ 3:50 /km")
    check("with its entered pace",
          runs.fmt_pace(same["interval_pace_s"]), "3:50")

    # The column this replaced. A database that still has it must come forward
    # with the pace worked out from what was there, once, and then be left
    # alone - see core/db.py _convert_interval_splits.
    print("\nThe old interval_split_s column converts on first start")
    legacy = Path(tempfile.gettempdir()) / "wellness_legacy_split.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(legacy) + suffix).unlink(missing_ok=True)
    with sqlite3.connect(legacy) as conn:
        conn.executescript("""
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY, day TEXT, distance_km REAL,
                duration_s INTEGER, run_type TEXT, effort_type TEXT,
                note TEXT, source TEXT DEFAULT 'manual',
                interval_type TEXT, interval_count INTEGER,
                interval_distance_m REAL, interval_split_s INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')));
            INSERT INTO runs (day, distance_km, duration_s, run_type,
                              effort_type, interval_type, interval_count,
                              interval_distance_m, interval_split_s)
            VALUES ('2026-04-04', 9.76, 2810, 'Intervals', 'VO2 max',
                    'distance', 5, 1000, 233),
                   ('2026-04-05', 8.00, 2400, 'Intervals', 'VO2 max',
                    'distance', 12, 400, 92),
                   ('2026-04-06', 8.00, 2401, 'Intervals', 'VO2 max',
                    'time', 6, NULL, 180);
        """)
    db.init_db(legacy)
    with sqlite3.connect(legacy) as conn:
        conn.row_factory = sqlite3.Row
        got = {row["day"]: dict(row) for row in conn.execute(
            "SELECT day, interval_type, interval_time_s, interval_pace_s "
            "FROM runs ORDER BY day")}
        columns = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    # 1k reps: the split was already the pace. 400m reps: 92s over 0.4 km is a
    # 3:50/km, which is the figure that column should always have held.
    check("1k reps keep their number",
          runs.fmt_pace(got["2026-04-04"]["interval_pace_s"]), "3:53")
    check("400m reps convert to a pace",
          runs.fmt_pace(got["2026-04-05"]["interval_pace_s"]), "3:50")
    check("and lose the time, which does not apply",
          got["2026-04-05"]["interval_time_s"], None)
    check("a session set by time keeps its rep length",
          runs.fmt_duration(got["2026-04-06"]["interval_time_s"]), "3:00")
    check("the old column is gone", "interval_split_s" in columns, False)
    # Second start: nothing left to do, and nothing damaged by trying.
    db.init_db(legacy)
    with sqlite3.connect(legacy) as conn:
        again = conn.execute("SELECT interval_pace_s FROM runs "
                             "WHERE day = '2026-04-05'").fetchone()[0]
    check("re-running the migration changes nothing", again, 230)

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("Every figure reconciles with the sheet, deviations included and "
          "accounted for.")
    return 0


if __name__ == "__main__":
    code = main()
    for suffix in ("", "-wal", "-shm"):
        Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)
    sys.exit(code)
