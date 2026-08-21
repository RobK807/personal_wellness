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
                  run_options, run_queries, runs)
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


def food_section(client) -> None:
    """The food section: fill a day's grid, answer an alert, plan a week.

    Deliberately not the food workbook - food_test.py reconciles against that.
    This is the routes, the day grid and the two form handlers, on a fixture
    whose answers can be worked out in your head: a food of 100 kcal per 100 g,
    so a quantity is its own number of calories.
    """
    from core import food_mutations as fm, food_queries as fq

    def slot(meal: str, n: int = 0) -> int:
        """The form index of slot `n` of `meal`, mirroring _day_grid()."""
        return (config.MEALS.index(meal) * config.MAX_ENTRIES_PER_MEAL) + n + 1

    def row(meal, n=0, **fields) -> dict:
        index = slot(meal, n)
        return {f"row{index}_{key}": str(value)
                for key, value in fields.items()}

    def bulk(meal: str, offset: int) -> int:
        """The form index of one day's row in one meal's bulk table."""
        return (config.MEALS.index(meal) * 7) + offset + 1

    print("\nfood: the empty state")
    for path in ["/diet/", "/diet/week", "/diet/calculator", "/diet/foods",
                 "/diet/targets", "/diet/admin", "/diet/analysis"]:
        check(f"GET {path}", client.get(path).status_code, 200)
    check("a target is seeded", fq.total_targets() >= 1, True)

    print("\nfood: the catalogue")
    client.post("/diet/foods", data={
        "name": "Test oats", "list": "Items", "grouping": "Meal component",
        "portion": "100", "units": "grams", "calories": "100", "carbs": "60",
        "fat": "8", "protein": "12"}, follow_redirects=True)
    oats = fq.food_by_name("Test oats")
    check("saved", oats is not None, True)
    check("refuses the same name in the same list",
          "flash flash-error" in client.post("/diet/foods", data={
              "name": "Test oats", "list": "Items", "portion": "1",
              "calories": "1", "carbs": "0", "fat": "0", "protein": "0"},
              follow_redirects=True).get_data(as_text=True), True)
    # The workbook has "Mashed potato" as both an Item and a Recipe, so the
    # same name in a different list has to be allowed.
    client.post("/diet/foods", data={
        "name": "Test oats", "list": "Recipes", "portion": "1",
        "units": "Portion", "calories": "250", "carbs": "30", "fat": "5",
        "protein": "10"}, follow_redirects=True)
    check("the same name in another list is fine",
          len(fq.foods(search="Test oats", include_retired=True)), 2)

    print("\nfood: the catalogue filters by list and grouping")
    body = client.get("/diet/foods?list=Items&grouping=Meal+component"
                      ).get_data(as_text=True)
    check("the filtered page finds it", "Test oats" in body, True)
    check("a grouping with nothing in it finds nothing",
          "Test oats" in client.get("/diet/foods?list=Items&grouping=Dessert"
                                    ).get_data(as_text=True), False)

    print("\nfood: a day holds several lines per meal")
    day = TODAY.isoformat()
    form = {}
    # Three in one meal, proving the grid is not one line per meal, plus one
    # in another. 250 g of a per-100 g food is 250 kcal.
    form.update(row("Snacks", 0, name="Test oats", list="Items",
                    grouping="Meal component", quantity="250"))
    form.update(row("Snacks", 1, name="Test oats", list="Items",
                    grouping="Meal component", quantity="100"))
    form.update(row("Snacks", 2, name="Test oats", list="Items",
                    grouping="Meal component", quantity="50"))
    form.update(row("Breakfast", 0, name="Test oats", list="Items",
                    grouping="Meal component", quantity="100"))
    # Wholly blank slots must be skipped rather than stored as zero lines.
    form.update(row("Lunch", 0, name="", quantity=""))
    resp = client.post(f"/diet/day/save?day={day}", data=form,
                       follow_redirects=True)
    check("status", resp.status_code, 200)
    check("the blank slots were skipped", fq.day(day)["entries"], 4)
    check("three lines landed in one meal",
          sum(1 for e in fq.entries(day) if e["meal"] == "Snacks"), 3)
    check("and they are numbered in order",
          [e["position"] for e in fq.entries(day) if e["meal"] == "Snacks"],
          [1, 2, 3])
    check("each was scaled by the food's portion",
          fq.day(day)["calories"], 250.0 + 100.0 + 50.0 + 100.0)
    check("all four are linked to the catalogue",
          all(e["food_id"] == oats["id"] for e in fq.entries(day)), True)

    print("\nfood: every one of the eight slots in a meal works")
    eight = TODAY - dt.timedelta(days=3)
    form = {}
    for index in range(config.MAX_ENTRIES_PER_MEAL):
        form.update(row("Dinner", index, name="Test oats", list="Items",
                        grouping="Meal component", quantity="100"))
    client.post(f"/diet/day/save?day={eight.isoformat()}", data=form,
                follow_redirects=True)
    check("eight lines", fq.day(eight)["entries"],
          config.MAX_ENTRIES_PER_MEAL)
    check("and eight hundred calories", fq.day(eight)["calories"], 800.0)

    print("\nfood: a day is replaced, not added to")
    client.post(f"/diet/day/save?day={day}",
                data=row("Dinner", 0, name="Test oats", list="Items",
                         grouping="Meal component", quantity="100"),
                follow_redirects=True)
    check("saving again replaces the day", fq.day(day)["entries"], 1)

    print("\nfood: a new name is checked against the catalogue first")
    near = TODAY - dt.timedelta(days=4)
    typed = row("Lunch", 0, name="Test oat", list="Items",
                grouping="Meal component", quantity="1", calories="90",
                carbs="50", fat="7", protein="10")
    body = client.post(f"/diet/day/save?day={near.isoformat()}",
                       data=typed).get_data(as_text=True)
    check("the alert is raised", "flash flash-warn" in body, True)
    check("it names the near miss", "Test oats" in body, True)
    check("it offers a way to answer",
          'name="row%d_resolve"' % slot("Lunch") in body, True)
    check("and nothing was saved", fq.day(near) is None, True)
    check("what was typed is still in the box", 'value="Test oat"' in body, True)

    print("\nfood: answering the alert with 'add as new'")
    before = fq.total_foods()
    acknowledged = {**typed, "row%d_seen" % slot("Lunch"): "1",
                    "row%d_resolve" % slot("Lunch"): ""}
    client.post(f"/diet/day/save?day={near.isoformat()}", data=acknowledged,
                follow_redirects=True)
    made = fq.food_by_name("Test oat", "Items")
    check("the food was added", fq.total_foods(), before + 1)
    check("filed under the list showing beside it", made["list"], "Items")
    check("and the grouping", made["grouping"], "Meal component")
    check("its macros are the ones typed", made["calories"], 90.0)
    check("the diary line links to it",
          fq.entries(near)[0]["food_id"], made["id"])

    print("\nfood: answering the alert with 'use that one instead'")
    swap = TODAY - dt.timedelta(days=5)
    before = fq.total_foods()
    client.post(f"/diet/day/save?day={swap.isoformat()}", data={
        **row("Lunch", 0, name="Test oatz", list="Items",
              grouping="Meal component", quantity="1", calories="999",
              carbs="0", fat="0", protein="0"),
        "row%d_seen" % slot("Lunch"): "1",
        "row%d_resolve" % slot("Lunch"): str(oats["id"])},
        follow_redirects=True)
    check("no new food was created", fq.total_foods(), before)
    check("the line is the one chosen",
          fq.entries(swap)[0]["food_id"], oats["id"])
    # The typed "1" meant one of the thing that was typed, not one gram of the
    # thing chosen - so it becomes one portion. See _quantity_for().
    check("and one portion of it, not one gram",
          fq.day(swap)["calories"], 100.0)

    print("\nfood: a name already in the catalogue raises nothing")
    quiet = TODAY - dt.timedelta(days=6)
    body = client.post(f"/diet/day/save?day={quiet.isoformat()}",
                       data=row("Snacks", 0, name="Test oats", list="Items",
                                grouping="Meal component", quantity="100"),
                       follow_redirects=True).get_data(as_text=True)
    check("saved straight through", "flash flash-warn" in body, False)
    check("and recorded", fq.day(quiet)["calories"], 100.0)

    print("\nfood: bad input is refused, and nothing is stored")
    future = (TODAY + dt.timedelta(days=40)).isoformat()
    body = client.post(f"/diet/day/save?day={future}", data=row(
        "Lunch", 0, name="Nonsense thing", list="Items", quantity="1",
        calories="99999", carbs="0", fat="0", protein="0"),
        follow_redirects=True).get_data(as_text=True)
    check("the error is shown", "flash flash-error" in body, True)
    check("and the day was not created", fq.day(future) is None, True)

    print("\nfood: the bulk week planner")
    monday = TODAY - dt.timedelta(days=TODAY.weekday()) + dt.timedelta(days=28)
    # One day of the week is already spoken for; it must survive untouched.
    fm.save_day(monday, [{"meal": "Lunch", "name": "Already eaten",
                          "quantity": 1, "calories": 700, "carbs": 70,
                          "fat": 20, "protein": 40}])
    plan = {"anchor": monday.isoformat(), "starts_on": "0"}
    for offset in range(7):
        index = bulk("Breakfast", offset)
        plan.update({f"row{index}_name": "Test oats",
                     f"row{index}_list": "Items",
                     f"row{index}_grouping": "Meal component",
                     f"row{index}_quantity": "100"})
    body = client.post("/diet/week/plan", data=plan,
                       follow_redirects=True).get_data(as_text=True)
    week = fq.week(monday, 0)
    check("six days were filled",
          sum(1 for r in week["days"] if r["calories"] == 100.0), 6)
    check("the day that had entries was left alone",
          fq.day(monday)["calories"], 700.0)
    check("and it says so", "already had entries" in body, True)

    print("\nfood: the bulk planner never overwrites")
    snapshot = {r["day"]: r["calories"] for r in fq.week(monday, 0)["days"]}
    client.post("/diet/week/plan", data=plan, follow_redirects=True)
    check("running it again changes nothing",
          {r["day"]: r["calories"] for r in fq.week(monday, 0)["days"]},
          snapshot)

    print("\nfood: the bulk planner checks new names too")
    other = monday + dt.timedelta(days=56)
    index = bulk("Snacks", 0)
    body = client.post("/diet/week/plan", data={
        "anchor": other.isoformat(), "starts_on": "0",
        f"row{index}_name": "Test oatss", f"row{index}_list": "Items",
        f"row{index}_grouping": "Meal component", f"row{index}_quantity": "1",
        f"row{index}_calories": "50", f"row{index}_carbs": "5",
        f"row{index}_fat": "1", f"row{index}_protein": "2",
    }).get_data(as_text=True)
    check("the alert is raised there too", "flash flash-warn" in body, True)
    check("and nothing was written", fq.day(other) is None, True)

    print("\nfood: an empty bulk form says so rather than doing nothing quietly")
    body = client.post("/diet/week/plan", data={
        "anchor": (monday + dt.timedelta(days=84)).isoformat(),
        "starts_on": "0"}, follow_redirects=True).get_data(as_text=True)
    check("says there is nothing to copy", "Nothing to copy" in body, True)

    print("\nfood: Admin decides where a new line starts")
    client.post("/diet/admin", data={
        "list:Breakfast": "Items", "grouping:Breakfast": "Meal component",
        "list:Lunch": "Recipes", "grouping:Lunch": "Lunch",
        "list:Dinner": "Recipes", "grouping:Dinner": "Dinner",
        "list:Snacks": "Items", "grouping:Snacks": "Meal component",
        "week_starts_on": "6"}, follow_redirects=True)
    check("the defaults are stored",
          fq.meal_defaults()["Breakfast"], ("Items", "Meal component"))
    check("and the week start", fq.week_starts_on(), 6)
    body = client.get("/diet/").get_data(as_text=True)
    opening = body[body.index('id="row%d_grouping"' % slot("Snacks")):][:900]
    check("a new line opens on them",
          'value="Meal component" selected' in opening, True)
    client.post("/diet/admin", data={"week_starts_on": ""},
                follow_redirects=True)
    check("a blank setting hands it back to the built-in",
          fq.week_starts_on(), config.WEEK_STARTS_ON)

    print("\nfood: the macro calculator")
    body = client.post("/diet/calculator", data={
        "c1_name": "Test oats", "c1_list": "Items", "c1_quantity": "150",
        "c2_name": "Off a packet", "c2_quantity": "200", "c2_calories": "2",
        "c2_carbs": "1", "c2_fat": "0", "c2_protein": "0.5",
        "scale": "0.5"}).get_data(as_text=True)
    # 150 g of oats is 150 kcal; 200 units at 2 kcal each is 400; half of 550
    # is 275. Worked out by hand, because that is the point of the fixture.
    check("the components are shown", "Off a packet" in body, True)
    check("subtotal, then scaled", "550" in body and "275" in body, True)

    print("\nfood: targets are dated, not overwritten")
    client.post("/diet/targets", data={
        "name": "Test cut", "starts_on": TODAY.isoformat(), "calories": "1700",
        "carbs": "150", "fat": "45", "protein": "180"}, follow_redirects=True)
    check("in force today", fq.target_for(TODAY, "Test cut")["calories"], 1700.0)
    client.post("/diet/targets", data={
        "name": "Test cut",
        "starts_on": (TODAY + dt.timedelta(days=7)).isoformat(),
        "calories": "1600", "carbs": "140", "fat": "40", "protein": "185"},
        follow_redirects=True)
    check("today still reads the old version",
          fq.target_for(TODAY, "Test cut")["calories"], 1700.0)
    check("next week reads the new one",
          fq.target_for(TODAY + dt.timedelta(days=7), "Test cut")["calories"],
          1600.0)

    print("\nfood: retiring keeps the history, deleting is refused")
    client.post("/diet/foods", data={"action": "retire",
                                     "food_id": str(oats["id"])},
                follow_redirects=True)
    check("out of the pickers", fq.food_row(oats["id"])["retired"], 1)
    check("but still on its diary lines",
          fq.food_usage().get(oats["id"], 0) >= 1, True)
    body = client.post("/diet/foods", data={"action": "delete",
                                            "food_id": str(oats["id"])},
                       follow_redirects=True).get_data(as_text=True)
    check("deleting a food in use is refused", "flash flash-error" in body, True)
    check("and it is still there", fq.food_row(oats["id"]) is not None, True)

    print("\nfood: the page stays small enough to send a phone")
    # Thirty-two pickers, each of which could have carried the whole catalogue.
    # See the note at the top of the blueprint about what that would cost.
    body = client.get("/diet/").get_data(as_text=True)
    check("the catalogue is written once, not per picker",
          body.count('id="food-catalogue"'), 1)
    check("and the day page stays under 200 KB", len(body) < 200_000, True)


def workout_tracker(client) -> None:
    """The workout section: build a session, read it back, tick it off.

    Deliberately not the gym workbook - workout_test.py reconciles against that.
    This is the routes and the form handler, on a fixture whose answers can be
    worked out by hand: a 100 kg bench press, so every percentage is its own
    number of kilograms.
    """
    from core import (workout_mutations as wm, workout_queries as wq,
                      workouts)

    print("\nworkout tracker: the empty state")
    body = client.get("/workouts/plan").get_data(as_text=True)
    check("says there are no plans", "Nothing here yet" in body, True)
    check("the exercise catalogue is seeded", len(wq.exercises()), 24)
    for path in ["/workouts/", "/workouts/plan", "/workouts/tracker",
                 "/workouts/exercises"]:
        check(f"GET {path}", client.get(path).status_code, 200)

    print("\nworkout tracker: creating a plan")
    resp = client.post("/workouts/plan/save", data={
        "name": "Test block", "rounding_kg": "2.5",
        "started_on": TODAY.isoformat()}, follow_redirects=True)
    check("status", resp.status_code, 200)
    plan = wq.plan_by_name("Test block")
    check("saved", plan is not None, True)
    check("refuses a second plan of the same name",
          "flash flash-error" in client.post(
              "/workouts/plan/save", data={"name": "Test block"},
              follow_redirects=True).get_data(as_text=True), True)

    lookup = {row["name"]: row["id"] for row in wq.exercises()}
    client.post(f"/workouts/plan/{plan['id']}/max",
                data={"exercise_id": lookup["Bench Press"], "one_rm_kg": "100"},
                follow_redirects=True)
    check("1RM saved", wq.max_for(plan["id"], lookup["Bench Press"]), 100.0)

    client.post(f"/workouts/plan/{plan['id']}/phase", data={
        "name": "Phase 1", "focus": "Hypertrophy", "warmup_pcts": "50, 70",
        "working_pcts": "65", "working_sets": "5", "working_reps": "10",
        "accessory_sets": "3", "accessory_reps": "10-12"},
        follow_redirects=True)
    phase = wq.phases(plan["id"])[0]
    check("phase saved", phase["name"], "Phase 1")
    check("its percentages round-trip",
          workouts.percent_list(phase["warmup_pcts"]), [0.5, 0.7])

    client.post(f"/workouts/plan/{plan['id']}/week", data={
        "number": "1", "phase_id": phase["id"], "cycle_type": "A"},
        follow_redirects=True)
    week = wq.week_number(plan["id"], 1)
    check("week saved", week is not None, True)
    check("GET the week",
          client.get(f"/workouts/week/{week['id']}").status_code, 200)
    check("GET the builder",
          client.get(f"/workouts/build?week={week['id']}").status_code, 200)

    print("\nworkout tracker: the session builder")
    form = {
        "number": "1", "name": "",
        "ex1_id": lookup["Bench Press"],
        "ex1_w1_reps": "5", "ex1_w1_mode": "percent", "ex1_w1_pct": "50",
        "ex1_w2_reps": "3", "ex1_w2_mode": "percent", "ex1_w2_pct": "70",
        "ex1_w_rest": "60s",
        "ex1_working_sets": "5", "ex1_working_reps": "10",
        "ex1_working_mode": "percent", "ex1_working_pct": "65",
        "ex1_working_rest": "2-3 min", "ex1_working_cue": "Log RPE",
        "ex2_id": lookup["Pull-Ups"],
        "ex2_working_sets": "3", "ex2_working_reps": "8-10",
        "ex2_working_mode": "bodyweight", "ex2_working_added": "5",
        "ex3_id": lookup["Bulgarian Split Squat (Dumbbells)"],
        "ex3_accessory_sets": "3", "ex3_accessory_reps": "10",
        "ex3_accessory_mode": "choose",
    }
    body = client.post(f"/workouts/build?week={week['id']}", data=form,
                       follow_redirects=True).get_data(as_text=True)
    check("saved without complaint", "flash flash-error" in body, False)
    session = wq.sessions(week_id=week["id"])[0]
    check("three exercises", session["exercises"], 3)
    check("thirteen sets", session["sets"], 13)
    check("named after its working lifts",
          workouts.session_title(session), "Session 1 - Bench Press + Pull-Ups")

    sheet = wq.session_sheet(session["id"])
    check("warm-ups then working sets",
          [row["set_type"] for row in sheet[0]["sets"]],
          ["warmup", "warmup"] + ["working"] * 5)
    # 50, 70 and 65 percent of 100 kg, rounded to 2.5: exact, so the arithmetic
    # is checkable by eye rather than by trusting the same code that wrote it.
    check("percentages became kilograms",
          [row["prescribed_kg"] for row in sheet[0]["sets"]],
          [50.0, 70.0, 65.0, 65.0, 65.0, 65.0, 65.0])
    check("the bodyweight sets carry their added weight",
          {row["prescribed_kg"] for row in sheet[1]["sets"]}, {5.0})
    check("the accessory has no weight to give",
          {row["prescribed_kg"] for row in sheet[2]["sets"]}, {None})
    check("and says so on the page",
          "Choose weight" in
          client.get(f"/workouts/week/{week['id']}").get_data(as_text=True),
          True)
    check("the per-side movement says so too",
          "each side" in
          client.get(f"/workouts/week/{week['id']}").get_data(as_text=True),
          True)

    print("\nworkout tracker: the builder refuses what makes no sense")
    for label, override in [
        ("a percentage of a bodyweight movement",
         {"ex2_working_mode": "percent", "ex2_working_pct": "80"}),
        ("bodyweight on a barbell movement",
         {"ex1_working_mode": "bodyweight", "ex1_working_added": "5"}),
        ("a session with nothing in it",
         {"ex1_id": "", "ex2_id": "", "ex3_id": ""}),
        ("a rep range that runs backwards", {"ex1_working_reps": "12-8"}),
        ("a percentage nobody could lift", {"ex1_working_pct": "400"}),
        ("a rep count that is not a number", {"ex1_working_reps": "lots"}),
    ]:
        body = client.post(f"/workouts/build?week={week['id']}",
                           data={**form, **override, "number": "2"},
                           follow_redirects=True).get_data(as_text=True)
        check(f"refuses {label}", "flash flash-error" in body, True)

    print("\nworkout tracker: ticking off")
    client.post(f"/workouts/session/{session['id']}/tick",
                data={"done": "1", "done_on": TODAY.isoformat()},
                follow_redirects=True)
    check("ticked", wq.session(session["id"])["done"], 1)
    check("the plan says so", wq.totals(plan["id"])["sessions_done"], 1)
    client.post(f"/workouts/session/{session['id']}/tick", data={"done": "0"},
                follow_redirects=True)
    check("un-ticked", wq.session(session["id"])["done"], 0)
    check("a tick in the future is refused",
          "flash flash-error" in client.post(
              f"/workouts/session/{session['id']}/tick",
              data={"done": "1",
                    "done_on": (TODAY + dt.timedelta(days=1)).isoformat()},
              follow_redirects=True).get_data(as_text=True), True)

    print("\nworkout tracker: copying")
    client.post(f"/workouts/week/{week['id']}/copy", data={"number": "2"},
                follow_redirects=True)
    check("the week was copied", len(wq.weeks(plan["id"])), 2)
    check("with its sessions",
          len(wq.sessions(week_id=wq.week_number(plan["id"], 2)["id"])), 1)
    plan = wq.plan(plan["id"])
    body = client.post(f"/workouts/plan/{plan['id']}/copy",
                       data={"name": "Test block II", "with_maxes": "1"},
                       follow_redirects=True).get_data(as_text=True)
    check("the plan was copied", "nothing ticked off" in body, True)
    copy = wq.plan_by_name("Test block II")
    check("same structure", (copy["weeks"], copy["sessions"]),
          (plan["weeks"], plan["sessions"]))
    check("no history", copy["sessions_done"], 0)
    check("the 1RM came too",
          wq.max_for(copy["id"], lookup["Bench Press"]), 100.0)

    print("\nworkout tracker: the exercise catalogue")
    client.post("/workouts/exercises", data={
        "name": "Zercher Squat", "reps_mode": "total",
        "weight_mode": "total"}, follow_redirects=True)
    check("added", wq.exercise_by_name("Zercher Squat") is not None, True)
    check("refuses a duplicate",
          "flash flash-error" in client.post(
              "/workouts/exercises", data={"name": "zercher squat"},
              follow_redirects=True).get_data(as_text=True), True)
    check("refuses deleting one a plan uses",
          "flash flash-error" in client.post(
              "/workouts/exercises",
              data={"action": "delete", "exercise_id": lookup["Bench Press"]},
              follow_redirects=True).get_data(as_text=True), True)
    client.post("/workouts/exercises",
                data={"action": "retire",
                      "exercise_id": lookup["Goblet Squat"]},
                follow_redirects=True)
    check("retiring takes it out of the dropdown",
          lookup["Goblet Squat"] in {row["id"] for row in wq.exercises()},
          False)
    check("but leaves it in the catalogue",
          lookup["Goblet Squat"] in
          {row["id"] for row in wq.exercises(include_retired=True)}, True)


    print("\nworkout tracker: the session page")
    # A second week and session, so "next up" has somewhere to move to.
    client.post(f"/workouts/plan/{plan['id']}/week", data={"number": "3"},
                follow_redirects=True)
    third = wq.week_number(plan["id"], 3)
    client.post(f"/workouts/build?week={third['id']}",
                data={**form, "number": "1"}, follow_redirects=True)

    body = client.get("/workouts/").get_data(as_text=True)
    check("opens without being told which plan", "<h1>" in body, True)
    # With no plan named it opens on current_plan() - the most recently trained,
    # else the newest - which by this point in the test is the copy. Ask for the
    # one whose sessions we know about.
    body = client.get(f"/workouts/?plan={plan['id']}").get_data(as_text=True)
    check("has all three dropdowns",
          all(f'id="{name}"' in body for name in ("plan", "week", "session")),
          True)
    # Nothing is ticked off at this point, so the first session of week 1 is due.
    due = wq.next_session(plan["id"])
    check("opens on the session that is due",
          f'value="{due["id"]}" selected' in body.replace("  ", " "), True)

    wm.tick_session(due["id"], True)
    moved = client.get(
        f"/workouts/?plan={plan['id']}").get_data(as_text=True)
    after = wq.next_session(plan["id"])
    check("and moves on once that one is done", after["id"] != due["id"], True)
    check("to the next one", f'value="{after["id"]}" selected'
          in moved.replace("  ", " "), True)
    wm.tick_session(due["id"], False)

    check("a stale week id falls back rather than 404ing",
          client.get("/workouts/?week=999999").status_code, 200)
    check("so does a stale session id",
          client.get("/workouts/?session=999999").status_code, 200)
    check("and a stale plan id is still a 404",
          client.get("/workouts/?plan=999999").status_code, 404)

    one = client.get(f"/workouts/?plan={plan['id']}&week={week['id']}"
                     f"&session={session['id']}").get_data(as_text=True)
    check("shows the sets", "50 kg" in one and "Bodyweight +5 kg" in one, True)
    check("and only this session's exercises",
          one.count("<h3>Bench Press"), 1)
    check("with the tick above them",
          one.index("Tick it off") < one.index("<h3>Bench Press"), True)
    check("a per-side movement is flagged", "per side" in one, True)
    # Jinja renders None as the string "None" - a set with a rest but no cue
    # once read "60sNone" on the page.
    check("no stray Nones", "None" in one, False)

    print("\nworkout tracker: phases read down, not across")
    plan_body = client.get(
        f"/workouts/plan?plan={plan['id']}").get_data(as_text=True)
    check("the phase picker is a dropdown", 'id="phase"' in plan_body, True)
    check("and its parts are a labelled list",
          all(f"<dt>{label}</dt>" in plan_body
              for label in ("Weeks", "Warm-up", "Working", "Working sets")),
          True)
    check("a stale phase id falls back to the first",
          client.get(f"/workouts/plan?plan={plan['id']}&phase=999999").status_code,
          200)

    print("\nworkout tracker: the catalogue is a stack, not a wide table")
    moves = client.get("/workouts/exercises").get_data(as_text=True)
    check("one card per movement",
          moves.count('class="panel move'), len(wq.exercises(True)))
    check("every name is in its summary",
          all(f'<span class="move-name">{row["name"]}</span>' in moves
              for row in wq.exercises()), True)
    check("the flags read as tags",
          'class="tag">bodyweight</span>' in moves
          and 'class="tag">per side</span>' in moves, True)
    check("and editing still works from there",
          "flash flash-error" in client.post(
              "/workouts/exercises",
              data={"exercise_id": lookup["Bench Press"], "action": "save",
                    "name": "Bench Press", "reps_mode": "total",
                    "weight_mode": "total"},
              follow_redirects=True).get_data(as_text=True), False)

    print("\nworkout tracker: every page with a plan in it")
    for path in ["/workouts/", "/workouts/plan", "/workouts/tracker",
                 "/workouts/exercises",
                 f"/workouts/week/{week['id']}",
                 f"/workouts/build?week={week['id']}",
                 f"/workouts/build?session={session['id']}"]:
        check(f"GET {path}", client.get(path).status_code, 200)
    check("a missing week is a 404",
          client.get("/workouts/week/999999").status_code, 404)
    check("a missing session is a 404",
          client.get("/workouts/build?session=999999").status_code, 404)


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
        # The lists are closed. A value not on one is refused rather than
        # quietly becoming a seventh run type nobody chose.
        ("a run type not on the list", {"run_type": "Fartlek"}),
        ("an effort type not on the list", {"effort_type": "Very hard"}),
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
        "interval_distance_m": "1k", "interval_pace_s": "3:50"},
        follow_redirects=True)
    check("saved without complaint",
          'class="flash flash-error"' in resp.get_data(as_text=True), False)
    entered = run_queries.run(session["id"])
    check("count", entered["interval_count"], 8)
    check("distance per rep, from '1k'", entered["interval_distance_m"], 1000.0)
    check("pace per rep, as entered",
          runs.fmt_pace(entered["interval_pace_s"]), "3:50")
    # Set by distance, so there is no time per rep to hold and none is stored.
    check("no time per rep on a distance session", entered["interval_time_s"],
          None)
    check("reps covered", entered["interval_total_km"], 8.0)
    check("reads back in shorthand",
          runs.interval_summary(entered), "8 x 1k @ 3:50 /km")
    check("shown on the run's page",
          "8 x 1k @ 3:50 /km" in
          client.get(RUNS + f"/run/{session['id']}").get_data(as_text=True), True)
    check("and listed on the analysis page",
          "8 x 1k @ 3:50 /km" in
          client.get(RUNS + "/analysis?range=All").get_data(as_text=True), True)

    # Ten-second sprints: the case that makes the pace its own field. The reps
    # are 0:10 each and the pace is nothing like 0:10, and no arithmetic
    # available here gets from one to the other.
    client.post(RUNS + f"/log?id={session['id']}", data={
        **base, "interval_type": "time", "interval_count": "10",
        "interval_time_s": "0:10", "interval_pace_s": "3:00"},
        follow_redirects=True)
    entered = run_queries.run(session["id"])
    check("a 0:10 rep", runs.fmt_duration(entered["interval_time_s"]), "0:10")
    check("at a pace nothing like it",
          runs.fmt_pace(entered["interval_pace_s"]), "3:00")
    check("no distance per rep on a time session",
          entered["interval_distance_m"], None)
    check("10 x 0:10 comes to", runs.fmt_duration(entered["interval_total_s"]),
          "1:40")
    check("time-based reads back",
          runs.interval_summary(entered), "10 x 0:10 @ 3:00 /km")

    print("\nrun tracker: the interval fields refuse what makes no sense")
    for label, override in [
        ("a length with no type", {"interval_count": "8"}),
        ("an unknown type", {"interval_type": "vibes"}),
        ("reps longer than the run", {"interval_type": "distance",
                                      "interval_count": "40",
                                      "interval_distance_m": "1k"}),
        ("reps that outlast the run", {"interval_type": "time",
                                       "interval_count": "40",
                                       "interval_time_s": "5:00"}),
        ("a distance that is not one", {"interval_type": "distance",
                                        "interval_distance_m": "ish"}),
        ("a time that is not one", {"interval_type": "time",
                                    "interval_time_s": "quick"}),
        # Each length box belongs to one kind of session. The other is refused
        # rather than dropped: silently discarding a typed value is worse.
        ("a distance on a session set by time", {"interval_type": "time",
                                                 "interval_time_s": "3:00",
                                                 "interval_distance_m": "783m"}),
        ("a time on a session set by distance", {"interval_type": "distance",
                                                 "interval_distance_m": "1k",
                                                 "interval_time_s": "3:50"}),
        ("a written time in the distance box", {"interval_type": "distance",
                                                "interval_distance_m": "3:00"}),
        ("'3min' in the distance box", {"interval_type": "distance",
                                        "interval_distance_m": "3min"}),
        # The pace box is the easiest to fill in with the wrong thing: the rep
        # time instead of the pace. 0:10 per kilometre is not a pace.
        ("the rep time in the pace box", {"interval_type": "time",
                                          "interval_count": "10",
                                          "interval_time_s": "0:10",
                                          "interval_pace_s": "0:10"}),
    ]:
        body = client.post(RUNS + f"/log?id={session['id']}",
                           data={**base, **override},
                           follow_redirects=True).get_data(as_text=True)
        check(f"refuses {label}", 'class="flash flash-error"' in body, True)

    # ...and none of that gets in the way of the sessions that are real.
    print("\nrun tracker: real sessions still go in")
    for label, override in [
        ("100m strides", {"interval_type": "distance", "interval_count": "8",
                          "interval_distance_m": "100m",
                          "interval_pace_s": "3:00"}),
        ("hill reps by time", {"interval_type": "time", "interval_count": "8",
                               "interval_time_s": "1:00",
                               "interval_pace_s": "4:10"}),
        ("2k reps", {"interval_type": "distance", "interval_count": "3",
                     "interval_distance_m": "2k", "interval_pace_s": "4:10"}),
        ("a part-entered session", {"interval_type": "distance",
                                    "interval_distance_m": "1k"}),
    ]:
        body = client.post(RUNS + f"/log?id={session['id']}",
                           data={**base, **override},
                           follow_redirects=True).get_data(as_text=True)
        check(f"accepts {label}", 'class="flash flash-error"' in body, False)

    print("\nrun tracker: clearing the interval fields empties them")
    client.post(RUNS + f"/log?id={session['id']}", data=base,
                follow_redirects=True)
    cleared = run_queries.run(session["id"])
    check("all five are null again",
          [cleared[key] for key in runs.INTERVAL_FIELDS],
          [None, None, None, None, None])

    print("\nrun tracker: the dropdown lists, and the Admin page that owns them")
    check("seeded from config plus whatever the runs use",
          run_options.values("run_type"),
          [*config.RUN_TYPES, *(v for v in run_queries.distinct("run_type")
                                if v not in config.RUN_TYPES)])
    check("nothing in use is missing from a list",
          [run_options.orphans(kind) for kind in run_options.KINDS], [[], []])

    admin = client.get(RUNS + "/admin").get_data(as_text=True)
    check("the Admin page offers both boxes",
          all(f'name="options"' in admin and f'value="{kind}"' in admin
              for kind in run_options.KINDS), True)
    check("with the usage counts beside them",
          ">Standard<" in admin and ">Warm-up / warm down<" in admin, True)

    # Adding one: it appears on the list, and the form then accepts it.
    body = client.post(RUNS + "/admin", data={
        "kind": "run_type", "action": "save",
        "options": "\n".join(run_options.values("run_type") + ["Fartlek"])},
        follow_redirects=True).get_data(as_text=True)
    check("saving reports what it did", "added Fartlek" in body, True)
    check("and the list has it", "Fartlek" in run_options.values("run_type"),
          True)
    check("the Log page offers it",
          'value="Fartlek"' in
          client.get(RUNS + "/log").get_data(as_text=True), True)
    accepted = client.post(RUNS + "/log", data={
        **base, "day": (TODAY - dt.timedelta(days=3)).isoformat(),
        "run_type": "Fartlek"}, follow_redirects=True).get_data(as_text=True)
    check("and a run can now be saved as one",
          'class="flash flash-error"' in accepted, False)

    # Removing one that is in use is refused; removing the unused one is not.
    body = client.post(RUNS + "/admin", data={
        "kind": "run_type", "action": "save",
        "options": "\n".join(v for v in run_options.values("run_type")
                             if v != "Standard")},
        follow_redirects=True).get_data(as_text=True)
    check("refuses to drop a type runs are using",
          "cannot be taken off the list" in body, True)
    check("and the list is untouched",
          "Standard" in run_options.values("run_type"), True)

    print("\nrun tracker: the list survives being edited badly")
    for label, options in [("an empty list", "   \n\n  "),
                           ("a wall of text", "x" * 200)]:
        body = client.post(RUNS + "/admin", data={
            "kind": "run_type", "action": "save", "options": options},
            follow_redirects=True).get_data(as_text=True)
        check(f"refuses {label}", 'class="flash flash-error"' in body, True)

    reordered = list(reversed(run_options.values("effort_type")))
    body = client.post(RUNS + "/admin", data={
        "kind": "effort_type", "action": "save",
        "options": "\n".join(reordered)},
        follow_redirects=True).get_data(as_text=True)
    check("reordering is allowed", run_options.values("effort_type"), reordered)
    check("and says so", "reordered" in body, True)
    check("duplicates and blank lines are dropped",
          run_options.clean([" Base ", "base", "", "  ", "Tempo"]),
          ["Base", "Tempo"])

    client.post(RUNS + "/admin", data={"kind": "effort_type", "action": "reset"},
                follow_redirects=True)
    check("reset puts the seed order back",
          run_options.values("effort_type")[:len(config.EFFORT_TYPES)],
          config.EFFORT_TYPES)

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

    workout_tracker(client)

    food_section(client)

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
