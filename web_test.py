"""Route-level smoke test for the Flask front-end.

    python web_test.py

Runs against a throwaway database, so the real one is untouched. Exercises
every page and every form handler in both built sections, including the write
paths, and checks the constraint the NAS deployment depends on: that nothing
heavy is imported.

It also checks that the two navigations agree - the sidebar is built from
config.SECTIONS and the tab strip from web.nav, and a section in one but not
the other is a dead link rather than an error, which is exactly the kind of
thing that survives a refactor unnoticed.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import tempfile
from pathlib import Path

TEMP_DB = Path(tempfile.gettempdir()) / "wellness_web_test.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(TEMP_DB) + suffix).unlink(missing_ok=True)
os.environ["PW_DB_PATH"] = str(TEMP_DB)
os.environ["PW_SECRET_KEY_PATH"] = str(TEMP_DB.with_suffix(".key"))
os.environ["PW_EXPORT_DIR"] = str(Path(tempfile.gettempdir()) / "wellness_exports")
os.environ.pop("PW_APP_PASSWORD", None)  # test the app, not the login gate

import config  # noqa: E402
from core import (db, metrics, mutations, queries, run_mutations,  # noqa: E402
                  run_queries, runs)
from web import nav  # noqa: E402
from web.app import create_app  # noqa: E402

# Every weigh-in page now sits behind its section prefix; the run tracker
# behind its own. Stated once here rather than spelled out per request.
WI = "/weigh-in"
RUNS = "/runs"

failures: list = []

BASE = {"weight": "70.0", "bmi": "24.0", "body_fat_pct": "20.0",
        "skeletal_muscle_pct": "40.0", "rm_kcal": "1600", "visceral_fat": "6"}

TODAY = dt.date.today()


def check(label: str, actual, expected) -> None:
    ok = actual == expected
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {actual!r}"
          + ("" if ok else f" (expected {expected!r})"))
    if not ok:
        failures.append(label)


def form(day: dt.date, slot1: dict | None = None, slot2: dict | None = None,
         note: str = "") -> dict:
    data = {"day": day.isoformat(), "note": note}
    for slot, values in ((1, slot1), (2, slot2)):
        for key, value in (values or {}).items():
            data[f"s{slot}_{key}"] = value
    return data


def seed() -> None:
    """A fortnight of readings, so the pages have something to draw."""
    for offset in range(14, 0, -1):
        when = TODAY - dt.timedelta(days=offset)
        mutations.save_reading(when, 1, {
            "weight": 70 + offset * 0.1, "bmi": 24.0, "body_fat_pct": 20.0,
            "skeletal_muscle_pct": 40.0, "rm_kcal": 1600, "visceral_fat": 6})


def seed_runs() -> None:
    """A handful of runs with ladders, enough for every page to have work to do.

    Deliberately not a copy of the real data: the point is that the pages and
    the rankings behave, and a fixture whose answers can be worked out by hand
    is worth more here than seven years of real running.
    """
    fixtures = [
        # days ago, km,    time,      run type,   effort,      splits
        (30, 5.00, "25:00", "Standard", "Base",
         {"400m": "1:50", "1/2 mile": "3:45", "1K": "4:40", "1 mile": "7:40",
          "2 mile": "15:40", "5K": "24:50"}),
        (20, 10.00, "48:00", "Pace", "Threshold",
         {"400m": "1:40", "1K": "4:20", "5K": "23:00", "10K": "47:50"}),
        (10, 21.10, "1:50:00", "Race", "Race",
         {"400m": "1:45", "1K": "4:35", "5K": "24:00", "10K": "49:00",
          "15K": "1:16:00", "20K": "1:44:00", "Half-Marathon": "1:49:50"}),
        (5, 3.00, "12:30", "Sprints", "VO2 max",
         {"400m": "1:20", "1/2 mile": "2:55", "1K": "3:55", "1 mile": "6:30"}),
        # Same day as the one above, to prove two runs on a date stay apart.
        (5, 6.20, "35:00", "Weighted", "Base",
         {"400m": "2:00", "1K": "5:10", "5K": "27:30"}),
    ]
    for offset, km, duration, run_type, effort, splits in fixtures:
        run_mutations.save_run(
            {"day": TODAY - dt.timedelta(days=offset), "distance_km": km,
             "duration_s": duration, "run_type": run_type,
             "effort_type": effort, "note": ""},
            splits)


def run_tracker(client) -> None:
    """Every run tracker page, and the write path behind the input form."""
    print("\nrun tracker: the empty state")
    body = client.get(RUNS + "/").get_data(as_text=True)
    check("says there is nothing yet", "No runs yet" in body, True)
    check("the log page still works",
          client.get(RUNS + "/log").status_code, 200)

    seed_runs()
    check("five runs seeded", run_queries.total_runs(), 5)
    check("two runs on the same day stay apart",
          len(run_queries.runs_on(TODAY - dt.timedelta(days=5))), 2)

    print("\nrun tracker: GET pages")
    for path in ["/", "/log", "/analysis", "/records", "/data", "/admin"]:
        check(f"GET {RUNS}{path}", client.get(RUNS + path).status_code, 200)

    print("\nrun tracker: GET with query parameters")
    for path in ["/analysis?grain=weekly", "/analysis?range=All",
                 "/analysis?run_type=Standard", "/records?top=3",
                 "/records?top=25&flagged=1", "/records?effort_type=Base",
                 "/data?page=2", "/data?range=90d&run_type=Race",
                 # Nonsense in the query string is ignored, not fatal.
                 "/analysis?grain=hourly", "/records?top=0", "/records?top=999",
                 "/data?page=abc", "/data?run_type=Nonexistent",
                 "/records?range=nope"]:
        check(f"GET {RUNS}{path}", client.get(RUNS + path).status_code, 200)

    print("\nrun tracker: pace is derived, not stored")
    row = run_queries.runs_list(limit=1, newest_first=False)[0]
    check("distance over time",
          round(row["pace_s"], 6), round(row["duration_s"] / row["distance_km"], 6))
    check("shown as mm:ss on the page",
          runs.fmt_pace(row["pace_s"]) in
          client.get(RUNS + "/data?range=All").get_data(as_text=True), True)

    print("\nrun tracker: the records rank on the split time")
    tables = run_queries.records(top=5)
    for label, rows in tables.items():
        times = [item["seconds"] for item in rows]
        check(f"{label} is in order", times, sorted(times))
        check(f"{label} has one row per run",
              len(rows), len({item["run_id"] for item in rows}))
    check("the fastest 1K is the sprint session",
          tables["1K"][0]["seconds"], 235)
    check("only runs long enough appear in the 10K",
          {item["distance_km"] for item in tables["10K"]}, {10.0, 21.1})

    print("\nrun tracker: POST /runs/log")
    when = (TODAY - dt.timedelta(days=1)).isoformat()
    resp = client.post(RUNS + "/log", data={
        "day": when, "distance_km": "8.00", "duration_s": "40:00",
        "run_type": "Standard", "effort_type": "Tempo", "note": "Posted",
        "bd_0": "1:35", "bd_2": "4:30", "bd_5": "24:30"},
        follow_redirects=True)
    check("status", resp.status_code, 200)
    check("saved", run_queries.total_runs(), 6)
    saved = run_queries.runs_on(TODAY - dt.timedelta(days=1))[0]
    check("with its ladder", saved["breakdowns"], 3)
    check("and its pace worked out", runs.fmt_pace(saved["pace_s"]), "5:00")

    print("\nrun tracker: POST /runs/log corrects rather than duplicates")
    resp = client.post(RUNS + f"/log?id={saved['id']}", data={
        "day": when, "distance_km": "8.00", "duration_s": "40:00",
        "run_type": "Pace", "effort_type": "Threshold", "note": "Corrected",
        "bd_0": "1:35"}, follow_redirects=True)
    corrected = run_queries.run(saved["id"])
    check("still six runs", run_queries.total_runs(), 6)
    check("reclassified", corrected["run_type"], "Pace")
    check("splits replaced wholesale", corrected["breakdowns"], 1)

    print("\nrun tracker: the form refuses what cannot be true")
    bad = [
        ("a split longer than the run", {"bd_5": "45:00"}),
        ("a rung the run is too short for", {"bd_6": "50:00"}),
        ("a 5K quicker than its 1K", {"bd_2": "25:00", "bd_5": "24:00"}),
        ("a distance that is not a number", {"distance_km": "six"}),
        ("a time that is not a time", {"duration_s": "ages"}),
        ("a date in the future",
         {"day": (TODAY + dt.timedelta(days=1)).isoformat()}),
        ("a blank effort type", {"effort_type": ""}),
    ]
    base = {"day": (TODAY - dt.timedelta(days=2)).isoformat(),
            "distance_km": "6.00", "duration_s": "30:00",
            "run_type": "Standard", "effort_type": "Base"}
    for label, override in bad:
        before = run_queries.total_runs()
        body = client.post(RUNS + "/log", data={**base, **override},
                           follow_redirects=True).get_data(as_text=True)
        check(f"refuses {label}",
              'class="flash flash-error"' in body
              and run_queries.total_runs() == before, True)

    print("\nrun tracker: interval sessions")
    # The 10 km fixture: big enough to hold 8 x 1k, which the 3 km one is not.
    session = [row for row in run_queries.runs_list(newest_first=False)
               if row["distance_km"] == 10.00][0]
    base = {"day": runs.as_date(session["day"]).isoformat(),
            "distance_km": f"{session['distance_km']:g}",
            "duration_s": runs.fmt_duration(session["duration_s"]),
            "run_type": "Intervals", "effort_type": "VO2 max"}
    check("blank to start with", session["interval_type"], None)

    resp = client.post(RUNS + f"/log?id={session['id']}", data={
        **base, "interval_type": "distance", "interval_count": "8",
        "interval_distance_m": "1k", "interval_split_s": "3:50"},
        follow_redirects=True)
    check("saved without complaint",
          'class="flash flash-error"' in resp.get_data(as_text=True), False)
    entered = run_queries.run(session["id"])
    check("count", entered["interval_count"], 8)
    check("distance per rep, from '1k'", entered["interval_distance_m"], 1000.0)
    check("split", runs.fmt_duration(entered["interval_split_s"]), "3:50")
    # Derived, never stored: 230 seconds over one kilometre.
    check("pace is derived", runs.fmt_pace(entered["interval_pace_s"]), "3:50")
    check("reps covered", entered["interval_total_km"], 8.0)
    check("reads back in shorthand",
          runs.interval_summary(entered), "8 x 1k @ 3:50")
    check("shown on the run's page",
          "8 x 1k @ 3:50" in
          client.get(RUNS + f"/run/{session['id']}").get_data(as_text=True), True)
    check("and listed on the analysis page",
          "8 x 1k @ 3:50" in
          client.get(RUNS + "/analysis?range=All").get_data(as_text=True), True)

    # 400m reps: the split and the pace are different figures, which is the
    # case the single-column design had to get right.
    client.post(RUNS + f"/log?id={session['id']}", data={
        **base, "interval_type": "distance", "interval_count": "12",
        "interval_distance_m": "400m", "interval_split_s": "1:32"},
        follow_redirects=True)
    entered = run_queries.run(session["id"])
    check("a 400m rep's split", runs.fmt_duration(entered["interval_split_s"]),
          "1:32")
    check("is a different figure from its pace",
          runs.fmt_pace(entered["interval_pace_s"]), "3:50")

    # A time-based session: the length is the duration, the distance is what
    # happened, and the pace still falls out of the same two columns.
    client.post(RUNS + f"/log?id={session['id']}", data={
        **base, "interval_type": "time", "interval_count": "6",
        "interval_distance_m": "783m", "interval_split_s": "3:00"},
        follow_redirects=True)
    entered = run_queries.run(session["id"])
    check("time-based reads back",
          runs.interval_summary(entered), "6 x 3:00 @ 783 m")
    check("with a pace", runs.fmt_pace(entered["interval_pace_s"]), "3:50")

    print("\nrun tracker: the interval fields refuse what makes no sense")
    for label, override in [
        ("a length with no type", {"interval_count": "8"}),
        ("an unknown type", {"interval_type": "vibes"}),
        ("reps longer than the run", {"interval_type": "distance",
                                      "interval_count": "40",
                                      "interval_distance_m": "1k"}),
        ("a distance that is not one", {"interval_type": "distance",
                                        "interval_distance_m": "ish"}),
        ("a split that is not a time", {"interval_type": "distance",
                                        "interval_split_s": "quick"}),
    ]:
        body = client.post(RUNS + f"/log?id={session['id']}",
                           data={**base, **override},
                           follow_redirects=True).get_data(as_text=True)
        check(f"refuses {label}", 'class="flash flash-error"' in body, True)

    print("\nrun tracker: clearing the interval fields empties them")
    client.post(RUNS + f"/log?id={session['id']}", data=base,
                follow_redirects=True)
    cleared = run_queries.run(session["id"])
    check("all four are null again",
          [cleared[key] for key in ("interval_type", "interval_count",
                                    "interval_distance_m", "interval_split_s")],
          [None, None, None, None])

    print("\nrun tracker: deleting takes the splits with it")
    splits_before = db.scalar("SELECT COUNT(*) FROM run_bests", default=0)
    resp = client.post(RUNS + f"/run/{saved['id']}/delete", follow_redirects=True)
    check("status", resp.status_code, 200)
    check("gone", run_queries.run(saved["id"]), None)
    check("its splits went too",
          db.scalar("SELECT COUNT(*) FROM run_bests", default=0),
          splits_before - 1)

    print("\nrun tracker: a missing run is a 404, not a crash")
    for path in ["/run/999999", "/run/999999/delete", "/log?id=999999"]:
        method = client.post if path.endswith("delete") else client.get
        check(f"{path}", method(RUNS + path).status_code, 404)

    print("\nrun tracker: charts render as inline SVG")
    body = client.get(RUNS + "/analysis?range=All").get_data(as_text=True)
    check("svg present", "<svg" in body, True)
    check("bars drawn", "<rect" in body, True)
    check("no broken coordinates", "nan" in body.lower(), False)


def main() -> int:
    db.init_db()
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    # What a freshly started server has loaded. Captured before any test does
    # something unrepresentative - an export pulls in openpyxl.
    startup_modules = set(sys.modules)

    print(f"Testing against {TEMP_DB}\n")

    # --- the empty state renders before anything is entered -----------------
    print("first run")
    check("GET / redirects to the default section",
          client.get("/").headers.get("Location"), "/weigh-in/")
    body = client.get(WI + "/").get_data(as_text=True)
    check("the weigh-in section on an empty database",
          "Nothing recorded yet" in body, True)
    check("its input page still works",
          client.get(WI + "/input").status_code, 200)

    seed()

    # --- every page renders -------------------------------------------------
    print("\nGET pages")
    for path in ["/", "/input", "/charts", "/changes", "/data", "/admin"]:
        check(f"GET {WI}{path}", client.get(WI + path).status_code, 200)
    check("GET /healthz", client.get("/healthz").status_code, 200)

    print("\nGET with query parameters")
    for path in ["/charts?grain=weekly&range=1y", "/charts?grain=monthly&range=All",
                 "/changes?basis=daily", "/changes?basis=monthly",
                 "/changes?basis=rolling", "/changes?basis=weekly_avg&limit=52",
                 "/data?page=2", "/data?estimated=0",
                 "/data?grain=weekly", "/data?grain=monthly",
                 "/data?grain=weekly&page=2",
                 f"/input?day={(TODAY - dt.timedelta(days=3)).isoformat()}"]:
        check(f"GET {WI}{path}",
              client.get(WI + path).status_code, 200)

    print("\nbad input in the query string is handled, not fatal")
    for path in ["/charts?grain=nonsense&range=nope", "/changes?basis=nope",
                 "/changes?limit=abc", "/data?page=abc", "/data?grain=yearly",
                 "/input?day=banana"]:
        check(f"GET {WI}{path}",
              client.get(WI + path).status_code, 200)

    # --- entering a weigh-in ------------------------------------------------
    print("\nPOST /input - one weigh-in")
    resp = client.post(WI + "/input", data=form(TODAY, BASE), follow_redirects=True)
    check("status", resp.status_code, 200)
    row = queries.day(TODAY)
    check("saved", row is not None, True)
    check("weight", row["weight"], 70.0)
    check("one weigh-in", row["readings"], 1)

    print("\nPOST /input - the second weigh-in averages in")
    client.post(WI + "/input", data=form(TODAY, BASE, {**BASE, "weight": "71.0"}),
                follow_redirects=True)
    row = queries.day(TODAY)
    check("two weigh-ins", row["readings"], 2)
    check("averaged", row["weight"], 70.5)

    print("\nthe form comes back filled in")
    body = client.get(WI + f"/input?day={TODAY.isoformat()}").get_data(as_text=True)
    check("weigh-in 1 pre-filled", 'name="s1_weight"' in body and "70.0" in body, True)
    check("weigh-in 2 pre-filled", "71.0" in body, True)

    print("\nPOST /input rejects a bad value and keeps what you typed")
    resp = client.post(WI + "/input", data=form(TODAY, {**BASE, "weight": "715"}),
                       follow_redirects=True)
    check("flash shown", "looks wrong" in resp.get_data(as_text=True), True)
    check("nothing overwritten", queries.day(TODAY)["weight"], 70.5)

    print("\nPOST /input with everything blank says so")
    resp = client.post(WI + "/input", data=form(TODAY), follow_redirects=True)
    check("flash shown", "Nothing entered" in resp.get_data(as_text=True), True)

    print("\nPOST /input for a future date is refused")
    ahead = TODAY + dt.timedelta(days=2)
    resp = client.post(WI + "/input", data=form(ahead, BASE), follow_redirects=True)
    check("not saved", queries.day(ahead), None)
    check("with a message", "in the future" in resp.get_data(as_text=True), True)

    # --- back-filling through the form --------------------------------------
    print("\nPOST /input after missing three days back-fills them")
    for offset in (1, 2, 3):
        mutations.delete_day(TODAY - dt.timedelta(days=offset + 3))
    # Deleting inside a run re-estimates it, so clear the estimates too and
    # let the next save rebuild them - this is the "came back after a break"
    # path rather than the "corrected a day" one.
    with db.transaction() as conn:
        conn.execute("DELETE FROM readings WHERE day > ? AND day < ?",
                     ((TODAY - dt.timedelta(days=7)).isoformat(),
                      (TODAY - dt.timedelta(days=3)).isoformat()))
    check("the days are gone",
          [queries.day(TODAY - dt.timedelta(days=n)) for n in (4, 5, 6)],
          [None, None, None])

    resp = client.post(WI + "/input",
                       data=form(TODAY - dt.timedelta(days=3), BASE),
                       follow_redirects=True)
    check("says how many it filled",
          "Filled in 3 missed days" in resp.get_data(as_text=True), True)
    check("and they are there",
          [queries.day(TODAY - dt.timedelta(days=n))["estimated"] for n in (4, 5, 6)],
          [1, 1, 1])

    # --- deleting ------------------------------------------------------------
    print("\nPOST /input/delete")
    resp = client.post(WI + "/input/delete",
                       data={"day": TODAY.isoformat(), "slot": "2"},
                       follow_redirects=True)
    check("status", resp.status_code, 200)
    check("one weigh-in left", queries.day(TODAY)["readings"], 1)

    client.post(WI + "/input/delete", data={"day": TODAY.isoformat(), "slot": "all"},
                follow_redirects=True)
    check("day removed", queries.day(TODAY), None)
    client.post(WI + "/input", data=form(TODAY, BASE), follow_redirects=True)

    # --- charts actually draw ------------------------------------------------
    print("\ncharts render as inline SVG")
    body = client.get(WI + "/charts?grain=daily&range=30d").get_data(as_text=True)
    check("svg present", "<svg" in body, True)
    check("a line was drawn", "<polyline" in body, True)
    check("no broken coordinates", "nan" in body.lower(), False)
    check("both axes labelled on a pair chart",
          "Weight" in body and "BMI" in body, True)

    for grain in ("weekly", "monthly"):
        body = client.get(WI + f"/charts?grain={grain}&range=All").get_data(as_text=True)
        check(f"{grain} charts render", "<svg" in body, True)

    # --- the Data page's grain filter --------------------------------------
    # The table and the charts must not be able to disagree about the same
    # week, so both read the same views. That is asserted rather than assumed.
    print("\n/data shows daily, weekly or monthly")
    for grain in queries.GRAINS:
        body = client.get(WI + f"/data?grain={grain}").get_data(as_text=True)
        heading = {"daily": "Date", "weekly": "Week commencing",
                   "monthly": "Month"}[grain]
        check(f"{grain} heading", f"<th>{heading}</th>" in body, True)

        shown = queries.recent_periods(grain, limit=40)
        expected = list(reversed(queries.series(grain)))[:40]
        check(f"{grain} rows are the charts' figures",
              [r["period"] for r in shown], [r["period"] for r in expected])
        check(f"{grain} values match",
              all(abs(a[k] - b[k]) < 1e-9
                  for a, b in zip(shown, expected) for k in config.ALL_KEYS), True)
        check(f"{grain} is newest first",
              shown == sorted(shown, key=lambda r: r["period"], reverse=True), True)

    print("\nthe real-readings filter belongs to the daily view only")
    daily = client.get(WI + "/data?grain=daily").get_data(as_text=True)
    weekly = client.get(WI + "/data?grain=weekly").get_data(as_text=True)
    check("offered on daily", "Real readings only" in daily, True)
    check("hidden on weekly", "Real readings only" in weekly, False)
    check("weekly counts estimated days instead",
          "<th class=\"num\">Estimated</th>" in weekly, True)
    check("filtering still works on daily",
          len(queries.recent_periods("daily", limit=40, include_estimated=False))
          < len(queries.recent_periods("daily", limit=40)), True)

    print("\nthe grain survives paging and filtering")
    # Checked from page 2 rather than page 1: the test database holds a
    # fortnight, so at weekly grain there is no "Older" link to inspect.
    second = client.get(WI + "/data?grain=weekly&page=2").get_data(as_text=True)
    check("paging back keeps the grain", "grain=weekly&amp;page=1" in second, True)
    check("the filter keeps the grain",
          "grain=daily&amp;estimated=0" in daily, True)
    check("the grain selector keeps the filter",
          "grain=weekly&amp;estimated=0" in
          client.get(WI + "/data?grain=daily&estimated=0").get_data(as_text=True), True)

    # --- the hover readout's data ------------------------------------------
    # chart-hover.js only formats and positions; everything it says comes from
    # this payload. If it drifts from what the axes and tables show, the chart
    # starts lying, so the values are checked against core.metrics here rather
    # than trusted to a browser.
    print("\ncharts carry a hover payload that agrees with core.metrics")
    body = client.get(WI + "/charts?grain=daily&range=All").get_data(as_text=True)
    payloads = re.findall(r"data-chart='([^']*)'", body)
    # Counted on class="chart", not on "<svg" - the favicon in base.html is an
    # inline SVG data URI and would otherwise be counted as a thirteenth chart.
    check("every chart has one", len(payloads), body.count('class="chart"'))

    daily = queries.series("daily")
    first = json.loads(html.unescape(payloads[0]))
    check("a point per day", len(first["x"]), len(daily))
    check("dates are labelled", first["d"][-1],
          metrics.period_label("daily", daily[-1]["period"]))

    seen = {}
    for raw in payloads:
        chart = json.loads(html.unescape(raw))
        for series in chart["s"]:
            seen[series["n"]] = series
    check("every metric appears", len(seen), len(config.ALL_METRICS))

    mismatched, outside = [], 0
    for key, label, unit, dp, _ in config.ALL_METRICS:
        series = seen[label]
        if series["p"] != dp or series["u"] != metrics.unit_suffix(key):
            mismatched.append(f"{label}: format spec")
            continue
        for row, value in zip(daily, series["v"]):
            # Pre-rounded server-side, because JS rounds halves the other way.
            if value != metrics.round_metric(key, row[key]):
                mismatched.append(f"{label} on {row['period']}")
                break
        outside += sum(1 for y in series["y"]
                       if y is not None and not 0 <= y <= 260)
    check("values match what the axes show", mismatched, [])
    check("no point plotted outside the chart", outside, 0)

    print("\nthe payload cannot break out of its attribute")
    check("no bare apostrophes", all("'" not in p for p in payloads), True)
    check("no bare ampersands", all("&" not in p.replace("&#39;", "")
                                    .replace("&amp;", "") for p in payloads), True)

    # --- admin ---------------------------------------------------------------
    print("\nPOST /admin/backfill")
    resp = client.post(WI + "/admin/backfill", follow_redirects=True)
    check("status", resp.status_code, 200)
    check("logged", db.scalar(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'backfill'") > 0, True)

    print("\nPOST /admin/export (openpyxl, no pandas)")
    resp = client.post(WI + "/admin/export", follow_redirects=True)
    check("status", resp.status_code, 200)
    check("logged", db.scalar(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'export'") > 0, True)

    print("\ndownload path cannot escape the export directory")
    for name in ["../../config.py", "..%2f..%2fconfig.py", "nope.xlsx"]:
        check(f"GET {WI}/admin/export/{name}",
              client.get(WI + f"/admin/export/{name}").status_code
              in (400, 404), True)

    # --- the run tracker ------------------------------------------------------
    run_tracker(client)

    # --- the sections hang together -------------------------------------------
    # The sidebar is built from config.SECTIONS and the tab strip from web.nav,
    # and a section listed in one and missing from the other is a dead link
    # rather than an error - so it is checked rather than looked at.
    print("\nnavigation")
    body = client.get(WI + "/").get_data(as_text=True)
    for key, label, icon, slug in config.SECTIONS:
        check(f"sidebar offers {label}", f'href="/{slug}/"' in body, True)
    check("the current section is marked",
          body.count('class="active"') >= 1, True)

    for key, pages in nav.PAGES.items():
        for endpoint, short, full in pages:
            check(f"{endpoint} is a real endpoint",
                  endpoint in app.view_functions, True)

    print("\nthe placeholder sections render")
    for path in ["/workouts/", "/workouts/tracker", "/diet/", "/diet/analysis"]:
        response = client.get(path)
        check(f"GET {path}", response.status_code, 200)
        check(f"{path} says it is not built",
              "Not built yet" in response.get_data(as_text=True), True)

    # --- auth gate ------------------------------------------------------------
    print("\npassword gate")
    config.APP_PASSWORD = "test-password"
    gated = create_app().test_client()
    # Checked on a section page rather than "/", which redirects to one whether
    # you are signed in or not - so it cannot tell the two states apart.
    check("redirects when signed out, remembering where to",
          gated.get(WI + "/").headers.get("Location"), "/login?next=/weigh-in/")
    check("every section is gated",
          [gated.get(f"/{slug}/").status_code for _, _, _, slug in config.SECTIONS],
          [302] * len(config.SECTIONS))
    check("login page renders", gated.get("/login").status_code, 200)
    check("wrong password refused",
          "Incorrect" in gated.post("/login", data={"password": "nope"},
                                    follow_redirects=True).get_data(as_text=True), True)
    gated.post("/login", data={"password": "test-password"})
    check("correct password admits", gated.get(WI + "/").status_code, 200)
    check("and admits the run tracker too",
          gated.get(RUNS + "/").status_code, 200)
    config.APP_PASSWORD = None

    # --- the constraint the NAS deployment depends on -------------------------
    # pandas costs 85 MB to import and the NAS has ~76 MB free once the CD
    # dashboard is running. If an edit ever pulls it onto the Flask path, this
    # app stops fitting - and it would do so silently on a development machine.
    print("\nFlask path stays lightweight")
    heavy = [m for m in ("pandas", "numpy", "pyarrow", "streamlit", "altair",
                         "openpyxl")
             if m in startup_modules]
    check("nothing heavy loaded at startup", heavy, [])
    check("openpyxl loads only when exporting", "openpyxl" in sys.modules, True)

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
    TEMP_DB.with_suffix(".key").unlink(missing_ok=True)
    sys.exit(code)
