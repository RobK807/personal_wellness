"""Smoke test for the write path, run against a throwaway database.

    python smoke_test.py

Builds its own data from scratch rather than copying the real database, so
every expected number can be written out in full. The real database is never
opened.

Most of this is about back-filling, because that is the part with real
behaviour in it: a missed day has to be filled from both sides, the fill has to
be redone when a neighbouring reading changes, and it must never quietly
overwrite something you actually recorded.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

TEMP_DB = Path(tempfile.gettempdir()) / "weigh_ins_smoke_test.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)
os.environ["PW_DB_PATH"] = str(TEMP_DB)

from core import db, metrics, mutations, queries  # noqa: E402  (needs the env var)

failures: list = []

# A reading that is easy to do arithmetic with.
BASE = {"weight": 70.0, "bmi": 24.0, "body_fat_pct": 20.0,
        "skeletal_muscle_pct": 40.0, "rm_kcal": 1600, "visceral_fat": 6}

DAY = dt.date(2026, 3, 1)


def check(label: str, actual, expected) -> None:
    ok = actual == expected
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {actual!r}"
          + ("" if ok else f" (expected {expected!r})"))
    if not ok:
        failures.append(label)


def reading(**overrides) -> dict:
    return {**BASE, **overrides}


def main() -> int:
    db.init_db()
    print(f"Testing against {TEMP_DB}\n")

    # --- one reading --------------------------------------------------------
    print("save_reading")
    mutations.save_reading(DAY, 1, reading())
    row = queries.day(DAY)
    check("stored", row is not None, True)
    check("weight", row["weight"], 70.0)
    check("one weigh-in so far", row["readings"], 1)
    check("not estimated", row["estimated"], 0)

    print("\nthe second weigh-in averages with the first")
    mutations.save_reading(DAY, 2, reading(weight=71.0, body_fat_pct=21.0))
    row = queries.day(DAY)
    check("weight is the mean", row["weight"], 70.5)
    check("body fat is the mean", row["body_fat_pct"], 20.5)
    check("two weigh-ins", row["readings"], 2)
    # The workbook derived mass from the averaged weight and the averaged
    # percentage, not from each weigh-in separately.
    check("body fat mass", row["body_fat_kg"], round(70.5 * 20.5 / 100, 6))
    check("muscle mass", row["muscle_kg"], round(70.5 * 40.0 / 100, 6))

    # --- input validation ---------------------------------------------------
    print("\nbad input is refused")
    for label, values in [
        ("missing field", {**reading(), "weight": ""}),
        ("not a number", {**reading(), "bmi": "heavy"}),
        ("out of range", {**reading(), "weight": 715}),
    ]:
        try:
            mutations.save_reading(DAY, 1, values)
            check(label, "accepted", "InvalidReading")
        except metrics.InvalidReading:
            check(label, True, True)

    try:
        mutations.save_reading(dt.date.today() + dt.timedelta(days=1), 1, reading())
        check("future date", "accepted", "InvalidReading")
    except metrics.InvalidReading:
        check("future date", True, True)

    print("\ninput is rounded to what the scale reports")
    mutations.save_reading(DAY, 1, reading(weight=70.04, rm_kcal=1600.6))
    stored = queries.readings_for(DAY)[0]
    check("weight to 1dp", stored["weight"], 70.0)
    check("kcal to a whole number", stored["rm_kcal"], 1601.0)
    mutations.save_reading(DAY, 1, reading())  # put it back

    # --- back-filling a gap -------------------------------------------------
    print("\nfour missed days are filled in on a straight line")
    later = DAY + dt.timedelta(days=5)
    result = mutations.save_reading(
        later, 1, reading(weight=76.0, bmi=30.0, body_fat_pct=26.0,
                          skeletal_muscle_pct=46.0, rm_kcal=1660,
                          visceral_fat=12))
    check("days filled", result["filled"], 4)

    # Day 1 average is 70.5 (two weigh-ins), day 6 is 76.0, so the four days
    # between step by (76.0 - 70.5) / 5 = 1.1 exactly.
    expected_weights = [71.6, 72.7, 73.8, 74.9]
    got = [queries.day(DAY + dt.timedelta(days=n))["weight"] for n in (1, 2, 3, 4)]
    check("weights interpolate evenly", got, expected_weights)
    check("all four are flagged estimated",
          [queries.day(DAY + dt.timedelta(days=n))["estimated"] for n in (1, 2, 3, 4)],
          [1, 1, 1, 1])
    check("visceral fat stays a whole number",
          [queries.day(DAY + dt.timedelta(days=n))["visceral_fat"] for n in (1, 2, 3, 4)],
          [7.0, 8.0, 10.0, 11.0])
    check("the real readings either side are untouched",
          [queries.day(DAY)["estimated"], queries.day(later)["estimated"]], [0, 0])
    check("no gap left", queries.gaps(), [])

    # --- correcting a day inside the run ------------------------------------
    print("\nentering the real reading for a filled day rebuilds both sides")
    middle = DAY + dt.timedelta(days=3)
    mutations.save_reading(middle, 1, reading(weight=80.0))
    check("the corrected day is now real", queries.day(middle)["estimated"], 0)
    check("and holds what was entered", queries.day(middle)["weight"], 80.0)
    # 70.5 -> 80.0 over three steps, then 80.0 -> 76.0 over two.
    check("the days before it were redrawn",
          [queries.day(DAY + dt.timedelta(days=n))["weight"] for n in (1, 2)],
          [73.7, 76.8])
    check("the days after it were redrawn",
          [queries.day(DAY + dt.timedelta(days=n))["weight"] for n in (4,)],
          [78.0])

    # --- deleting ------------------------------------------------------------
    print("\ndeleting a day turns it back into an estimate")
    mutations.delete_day(middle)
    row = queries.day(middle)
    check("still has a value", row is not None, True)
    check("but an estimated one", row["estimated"], 1)
    check("back on the original straight line", row["weight"], 73.8)

    print("\ndeleting one weigh-in of two leaves the day real")
    mutations.delete_reading(DAY, 2)
    row = queries.day(DAY)
    check("one weigh-in left", row["readings"], 1)
    check("still real", row["estimated"], 0)
    check("average is now that one reading", row["weight"], 70.0)

    # --- the back-fill limit -------------------------------------------------
    print("\na gap longer than the limit is left alone, not invented")
    import config
    far = later + dt.timedelta(days=config.MAX_BACKFILL_DAYS + 5)
    result = mutations.save_reading(far, 1, reading(weight=68.0))
    check("nothing filled", result["filled"], 0)
    check("and it says why", len(result["skipped_gap"]), 1)
    check("the gap is reported", len(queries.gaps()), 1)
    check("flagged as too long", queries.gaps()[0]["too_long"], True)

    # --- aggregation ---------------------------------------------------------
    print("\nweekly and monthly averages")
    week_of = DAY - dt.timedelta(days=DAY.weekday())
    weekly = {r["period"]: r for r in queries.series("weekly")}
    daily = {r["period"]: r for r in queries.series("daily")}
    in_week = [v for k, v in daily.items()
               if week_of.isoformat() <= k < (week_of + dt.timedelta(days=7)).isoformat()]
    check("the week averages the days it contains",
          weekly[week_of.isoformat()]["weight"],
          round(sum(d["weight"] for d in in_week) / len(in_week), 6))
    check("and counts them", weekly[week_of.isoformat()]["days"], len(in_week))

    monthly = {r["period"]: r for r in queries.series("monthly")}
    march = [v for k, v in daily.items() if k.startswith("2026-03")]
    check("the month averages its days", monthly["2026-03-01"]["days"], len(march))

    # --- changes -------------------------------------------------------------
    print("\nchanges")
    day_changes = {r["period"]: r for r in queries.changes("daily")}
    first_gap_day = (DAY + dt.timedelta(days=1)).isoformat()
    check("day on day",
          day_changes[first_gap_day]["weight"],
          round(daily[first_gap_day]["weight"] - daily[DAY.isoformat()]["weight"], 6))

    rolling = {r["period"]: r for r in queries.rolling_change(7)}
    check("the rolling change is seven days apart",
          all((metrics.as_date(r["period"]) - metrics.as_date(r["previous"])).days == 7
              for r in rolling.values()), True)

    # --- rebuilding ----------------------------------------------------------
    print("\nrebuilding every estimate is idempotent")
    before = {r["period"]: r["weight"] for r in queries.series("daily")}
    mutations.backfill_all(rebuild=True)
    after = {r["period"]: r["weight"] for r in queries.series("daily")}
    check("nothing moved", after, before)
    check("real readings all survived",
          db.scalar("SELECT COUNT(*) FROM readings WHERE estimated = 0"),
          db.scalar("SELECT COUNT(*) FROM readings WHERE estimated = 0"))

    check("audit log written", db.scalar("SELECT COUNT(*) FROM audit_log") > 0, True)

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    code = main()
    for suffix in ("", "-wal", "-shm"):
        Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)
    sys.exit(code)
