"""The food planner and diary.

Seven pages:

    day         one day, picked by date - the landing page, because the thing
                that actually gets done is checking today against the target
    week        seven days at the width the settings say a week is, plus the
                bulk planner that fills them from one form
    calculator  components in, macros out, with a scaling factor
    foods       the catalogue behind every picker
    targets     the named, dated macro profiles a day is measured against
    admin       where a new line's List and Grouping start, and the week's shape
    analysis    two years of diary, averaged

The day page is the workbook's DailyCheck sheet, and the one thing it does
differently is the thing that was asked for: it is addressed by date rather than
by week-and-day. A diary with 48 of 137 possible weeks in it cannot be navigated
by counting from the start, and "what did I eat on the 12th" is the question
being asked anyway.

Picking a food
--------------
Three controls, in this order: **List**, then **Grouping**, then the food. The
first two narrow the third from 187 options to a dozen, which is the difference
between a dropdown you scroll and one you read. Both start on whatever the Admin
page says that meal usually is.

The food control is an `<input>` with a `<datalist>` rather than a `<select>`,
which is what lets one control do both jobs: pick an existing food, or type one
that does not exist yet. A typed name that matches nothing becomes a free-text
line *and* a new catalogue row, filed under the List and Grouping showing beside
it. Before that happens the name is checked against the catalogue and anything
close is raised as an alert - see `_alerts()`.

It also keeps the page small. Thirty-two rows each carrying a 187-option select
is a 250 KB page; one shared datalist and a JSON index is about 35 KB, and the
browser filters it per row.
"""
from __future__ import annotations

import datetime as dt

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

import config
from core import food, food_mutations as fm, food_queries as fq
from web.app import login_required

bp = Blueprint("diet", __name__)

# How many rows the calculator has. A fixed-size form, the same way the session
# builder is: a slot left empty is skipped, which is what lets the form be one
# shape and the thing being built be any size up to it.
CALCULATOR_ROWS = 10

# How many foods the catalogue page shows at once - see the note in foods().
PER_PAGE = 40


def _date_arg(name: str = "day", default: dt.date | None = None) -> dt.date:
    """A date out of the query string, falling back rather than 404ing.

    A stale or hand-typed date in a URL is not worth an error page when the
    honest reading is "you meant today".
    """
    raw = request.args.get(name) or request.form.get(name)
    if raw:
        try:
            return food.as_date(raw)
        except Exception:
            pass
    return default or dt.date.today()


def _shell(**extra) -> dict:
    """What every page in this section needs."""
    return {
        "today": dt.date.today(),
        "macro_keys": config.MACRO_KEYS,
        "macro_labels": config.MACRO_LABELS,
        "macro_units": config.MACRO_UNITS,
        **extra,
    }


def _picker(**extra) -> dict:
    """What a page with food pickers on it needs, on top of _shell().

    The catalogue goes out once as JSON and the browser filters it per row. See
    the note at the top about what the alternative costs.
    """
    rows = fq.foods()
    return {
        "catalogue": rows,
        "index": fq.catalogue_index(rows),
        "lists": config.FOOD_LISTS,
        "groupings": fq.groupings_by_list(),
        "meal_defaults": fq.meal_defaults(),
        "units": config.FOOD_UNITS,
        "meals": config.MEALS,
        **extra,
    }


# --------------------------------------------------------------------------- #
# One day
# --------------------------------------------------------------------------- #
@bp.route("/")
@login_required
def day():
    """The section's landing page, and it owns /diet/ the way the others do.

    Everything needed to answer "how is today going" is above the form: the four
    totals, what is left of each, and how far through the target that is.
    """
    when = _date_arg()
    return _render_day(when)


def _render_day(when: dt.date, grid=None, alerts=None):
    row = fq.day(when)
    sheet = fq.day_sheet(when)
    totals = food.add_macros(fq.entries(when))
    # `chosen_target` is what this day asked for; `target_name` is what it ends
    # up measured against. Every imported day chose nothing - see v_food_days.
    target = fq.target_for(when, row["chosen_target"] if row else None) \
        or fq.target_for(when)

    return render_template(
        "diet/day.html",
        **_shell(),
        **_picker(),
        day=when,
        row=row,
        sheet=sheet,
        grid=grid if grid is not None else _day_grid(when),
        alerts=alerts or {},
        totals=totals,
        target=target,
        remaining=food.remaining(totals, target or {}),
        pct=food.pct_of_target(totals, target or {}),
        weekday=food.day_name(when),
        week_label=food.week_label(when, fq.week_starts_on()),
        previous=when - dt.timedelta(days=1),
        next_day=when + dt.timedelta(days=1),
        target_names=fq.target_names(),
        per_meal=config.MAX_ENTRIES_PER_MEAL,
    )


def _day_grid(when: dt.date, form=None) -> list:
    """The day as a fixed grid: one block per meal, eight slots in each.

    Eight because that is what was asked for and what the workbook's longest
    block held. The grid is built here rather than in the template so that the
    row numbering - which is what ties a form field back to a slot - is decided
    in one place, and so that redisplaying a rejected form is the same code path
    as displaying a saved day.
    """
    defaults = fq.meal_defaults()
    by_meal: dict = {}
    if form is None:
        for entry in fq.entries(when):
            by_meal.setdefault(entry["meal"], []).append(entry)

    grid, index = [], 0
    for meal in config.MEALS:
        default_list, default_grouping = defaults.get(meal, ("Items", ""))
        entries = by_meal.get(meal, [])
        slots = []
        for slot in range(config.MAX_ENTRIES_PER_MEAL):
            index += 1
            if form is not None:
                slots.append(_slot_from_form(form, index, default_list,
                                             default_grouping))
                continue
            entry = entries[slot] if slot < len(entries) else None
            catalogue = fq.food_row(entry["food_id"]) if entry \
                and entry["food_id"] else None
            slots.append({
                "index": index,
                "name": entry["name"] if entry else "",
                "list": (catalogue or {}).get("list") or default_list,
                "grouping": (catalogue or {}).get("grouping")
                            or (default_grouping if entry is None else ""),
                "quantity": (entry or {}).get("quantity") or "",
                "units": (entry or {}).get("units") or "",
                "seen": "",
                "resolve": "",
                **{key: (entry[key] if entry else "")
                   for key in config.MACRO_KEYS},
            })
        grid.append({"meal": meal, "slots": slots, "list": default_list,
                     "grouping": default_grouping,
                     "filled": len(entries)})
    return grid


def _slot_from_form(form, index: int, default_list: str,
                    default_grouping: str) -> dict:
    return {
        "index": index,
        "name": (form.get(f"row{index}_name") or "").strip(),
        "list": form.get(f"row{index}_list") or default_list,
        "grouping": form.get(f"row{index}_grouping", default_grouping),
        "quantity": (form.get(f"row{index}_quantity") or "").strip(),
        "units": (form.get(f"row{index}_units") or "").strip(),
        "seen": form.get(f"row{index}_seen") or "",
        "resolve": form.get(f"row{index}_resolve") or "",
        **{key: (form.get(f"row{index}_{key}") or "").strip()
           for key in config.MACRO_KEYS},
    }


@bp.route("/day/save", methods=["POST"])
@login_required
def save_day():
    when = _date_arg()
    grid = _day_grid(when, request.form)
    rows = _entries_from_grid(grid)

    alerts = _alerts(rows)
    if alerts:
        one = len(alerts) == 1
        flash(f"{len(alerts)} name{'' if one else 's'} "
              f"{'looks' if one else 'look'} like something already in the "
              f"catalogue. Pick what to do with {'it' if one else 'each'} and "
              f"save again — or leave "
              f"{'it' if one else 'them'} as {'it is' if one else 'they are'} "
              f"to add {'it' if one else 'them'} as new.", "warn")
        return _render_day(when, grid=grid, alerts=alerts)

    try:
        saved = fm.save_day(when, [row["entry"] for row in rows],
                            target_name=request.form.get("target_name") or None,
                            note=request.form.get("note"))
    except food.InvalidFood as exc:
        flash(str(exc), "error")
        return _render_day(when, grid=grid)

    message = (f"Saved {when:%a %d/%m/%Y} — {saved['entries']} "
               f"line{'' if saved['entries'] == 1 else 's'}")
    if saved["added"]:
        message += (f", and added "
                    + ", ".join(f"'{row['name']}'" for row in saved["added"])
                    + " to the catalogue")
    flash(message + ".", "ok")
    return redirect(url_for("diet.day", day=when.isoformat()))


def _entries_from_grid(grid: list) -> list:
    """The grid's filled slots, as the list save_day wants.

    A slot is filled if it names something. Everything else is skipped, which is
    how thirty-two boxes stay harmless when six of them are in use.

    Each row comes back with the slot beside it, because the caller may have to
    redisplay the form and needs to know which box a complaint belongs to.
    """
    rows = []
    for block in grid:
        for slot in block["slots"]:
            if not slot["name"]:
                continue
            rows.append({"slot": slot, "meal": block["meal"],
                         "entry": _entry_from_slot(slot, block["meal"])})
    return rows


def _entry_from_slot(slot: dict, meal: str) -> dict:
    """One slot as a diary line. The rules live in core so both fronts share
    them - see food_queries.resolve_entry()."""
    return fq.resolve_entry(slot, meal)


def _alerts(rows: list) -> dict:
    """Slot index -> near misses. Also core's, for the same reason."""
    return fq.alerts_for(rows)


@bp.route("/day/delete", methods=["POST"])
@login_required
def delete_day():
    when = _date_arg()
    try:
        gone = fm.delete_day(when)
    except food.InvalidFood as exc:
        flash(str(exc), "error")
    else:
        flash(f"Cleared {when:%a %d/%m/%Y} — {gone['entries']} lines.", "ok")
    return redirect(url_for("diet.day", day=when.isoformat()))


@bp.route("/day/copy", methods=["POST"])
@login_required
def copy_day():
    source = _date_arg("source")
    target = _date_arg("target")
    try:
        made = fm.copy_day(source, target)
    except food.InvalidFood as exc:
        flash(str(exc), "error")
        return redirect(url_for("diet.day", day=target.isoformat()))
    flash(f"Copied {source:%d/%m/%Y} into {target:%d/%m/%Y} — "
          f"{made['entries']} lines.", "ok")
    return redirect(url_for("diet.day", day=target.isoformat()))


# --------------------------------------------------------------------------- #
# The week
# --------------------------------------------------------------------------- #
@bp.route("/week")
@login_required
def week():
    """Seven days, plus the bulk planner that fills them.

    The start day comes from the Admin page rather than being a Monday: the
    diary's history is Monday-based and the "w/c" labels say so, but which day a
    planning week turns over on is a habit. `?starts_on` overrides it for one
    look without changing the setting.
    """
    return _render_week(_date_arg(), request.args.get("starts_on", type=int))


def _render_week(anchor: dt.date, starts_on=None, bulk=None, alerts=None):
    if starts_on is not None and not 0 <= starts_on <= 6:
        starts_on = None
    if starts_on is None:
        starts_on = fq.week_starts_on()
    data = fq.week(anchor, starts_on)

    planned = [row for row in data["days"] if row["planned"]]
    averages = {key: round(data["totals"][key] / len(planned), 1)
                for key in config.MACRO_KEYS} if planned else dict(food.ZERO)

    return render_template(
        "diet/week.html",
        **_shell(),
        **_picker(),
        anchor=anchor,
        week=data,
        planned=len(planned),
        averages=averages,
        target=fq.target_for(data["start"]),
        starts_on=starts_on,
        weekday_names=config.WEEKDAY_NAMES,
        previous=data["start"] - dt.timedelta(days=7),
        next_week=data["start"] + dt.timedelta(days=7),
        bulk=bulk if bulk is not None else _bulk_grid(data),
        alerts=alerts or {},
    )


def _bulk_grid(data: dict, form=None) -> list:
    """The bulk planner: one table per meal, one row per day.

    Deliberately one line per meal per day rather than eight. This is the "same
    breakfast all week" tool; anything more detailed is a day at a time on the
    Day page, and a 7x4x8 form would be 224 rows nobody would fill in.

    Every row carries its own List and Grouping, pre-filled from the Admin
    page's defaults for that meal - which is what makes a week of breakfasts two
    dropdowns and seven names rather than twenty-eight dropdowns.
    """
    defaults = fq.meal_defaults()
    grid, index = [], 0
    for meal in config.MEALS:
        default_list, default_grouping = defaults.get(meal, ("Items", ""))
        rows = []
        for offset, day in enumerate(data["days"]):
            index += 1
            slot = (_slot_from_form(form, index, default_list, default_grouping)
                    if form is not None else
                    {"index": index, "name": "", "list": default_list,
                     "grouping": default_grouping, "quantity": "", "units": "",
                     "seen": "", "resolve": "",
                     **{key: "" for key in config.MACRO_KEYS}})
            rows.append({**slot, "offset": offset, "date": day["date"],
                         "weekday": day["weekday"], "planned": day["planned"],
                         "entries": day["entries"]})
        grid.append({"meal": meal, "rows": rows, "list": default_list,
                     "grouping": default_grouping})
    return grid


@bp.route("/week/plan", methods=["POST"])
@login_required
def plan_week():
    anchor = _date_arg("anchor")
    starts_on = request.form.get("starts_on", type=int)
    data = fq.week(anchor, starts_on if starts_on is not None
                   else fq.week_starts_on())
    grid = _bulk_grid(data, request.form)

    rows = []
    for block in grid:
        for row in block["rows"]:
            if not row["name"]:
                continue
            rows.append({"slot": row, "meal": block["meal"],
                         "entry": {**_entry_from_slot(row, block["meal"]),
                                   "day_offset": row["offset"]}})
    if not rows:
        flash("Nothing to copy across — fill in at least one row.", "error")
        return _render_week(anchor, starts_on, bulk=grid)

    alerts = _alerts(rows)
    if alerts:
        one = len(alerts) == 1
        flash(f"{len(alerts)} name{'' if one else 's'} "
              f"{'looks' if one else 'look'} like something already in the "
              f"catalogue. Pick what to do with {'it' if one else 'each'} and "
              f"copy again.", "warn")
        return _render_week(anchor, starts_on, bulk=grid, alerts=alerts)

    try:
        made = fm.plan_week(data["start"], [row["entry"] for row in rows],
                            starts_on)
    except food.InvalidFood as exc:
        flash(str(exc), "error")
        return _render_week(anchor, starts_on, bulk=grid)

    flash(_planned_message(made), "ok" if made["days"] else "warn")
    return redirect(url_for("diet.week", day=data["start"].isoformat(),
                            starts_on=starts_on))


def _planned_message(made: dict) -> str:
    """What the bulk planner says it did, including what it left alone."""
    if not made["days"]:
        return ("Nothing copied — every day you filled in already has entries, "
                "and this never overwrites. Clear a day on its own page first.")
    parts = [f"Copied {made['lines']} "
             f"line{'' if made['lines'] == 1 else 's'} into "
             f"{len(made['days'])} day{'' if len(made['days']) == 1 else 's'}"]
    if made["skipped"]:
        parts.append(
            f"left {', '.join(f'{day:%a}' for day in made['skipped'])} alone "
            f"— {'it' if len(made['skipped']) == 1 else 'they'} already had "
            f"entries")
    if made["added"]:
        parts.append("added " + ", ".join(f"'{row['name']}'"
                                          for row in made["added"])
                     + " to the catalogue")
    return "; ".join(parts) + "."


@bp.route("/week/fill", methods=["POST"])
@login_required
def fill_week():
    """Copy one whole day across the rest of its week - the Planner's paste."""
    anchor = _date_arg("anchor")
    starts_on = request.form.get("starts_on", type=int)
    try:
        made = fm.fill_week(_date_arg("source"), anchor, starts_on,
                            overwrite=bool(request.form.get("overwrite")))
    except food.InvalidFood as exc:
        flash(str(exc), "error")
        return redirect(url_for("diet.week", day=anchor.isoformat()))

    message = (f"Copied {made['source']:%d/%m/%Y} into "
               f"{len(made['copied'])} day"
               f"{'' if len(made['copied']) == 1 else 's'}")
    if made["skipped"]:
        message += (f"; left {len(made['skipped'])} alone because "
                    f"{'it' if len(made['skipped']) == 1 else 'they'} already "
                    f"had entries")
    flash(message + ".", "ok")
    return redirect(url_for("diet.week", day=anchor.isoformat(),
                            starts_on=starts_on))


# --------------------------------------------------------------------------- #
# The macro calculator
# --------------------------------------------------------------------------- #
@bp.route("/calculator", methods=["GET", "POST"])
@login_required
def calculator():
    """Components in, macros out, times a scaling factor.

    The workbook's Calculator sheet: a recipe built from seven rows, then a
    proportion of it taken because the pan made rather more than one portion.
    The scale applies to the total rather than to any row, which is the question
    being asked - "what is two thirds of this".
    """
    rows, scale, result = [], 1.0, None
    if request.method == "POST":
        rows = _components_from_form(request.form)
        try:
            scale = food.parse_number(request.form.get("scale") or 1,
                                      "Scale", "scale")
            result = fq.calculate(rows, scale)
        except food.InvalidFood as exc:
            flash(str(exc), "error")

        if request.form.get("action") == "save" and result:
            try:
                saved = fm.save_food({
                    "list": request.form.get("save_list") or "Recipes",
                    "name": request.form.get("save_name"),
                    "grouping": request.form.get("save_grouping"),
                    "portion": request.form.get("save_portion") or 1,
                    "units": request.form.get("save_units") or "Portion",
                    "note": _built_from(result),
                    **result["total"],
                })
            except food.InvalidFood as exc:
                flash(str(exc), "error")
            else:
                flash(f"Saved '{saved['name']}' to {saved['list']}.", "ok")
                return redirect(url_for("diet.foods", list=saved["list"]))

    return render_template(
        "diet/calculator.html",
        **_shell(),
        **_picker(),
        rows=rows,
        scale=scale,
        result=result,
        slots=range(1, CALCULATOR_ROWS + 1),
        form=request.form if request.method == "POST" else None,
    )


def _built_from(result: dict) -> str:
    """The note a calculated food carries, so its macros can be accounted for."""
    count = len(result["components"])
    note = (f"Built in the calculator from {count} "
            f"component{'' if count == 1 else 's'}")
    if result["scale"] != 1:
        note += f", scaled to {result['scale']:g}"
    return note


def _components_from_form(form) -> list:
    """The calculator's grid, as resolve_components wants it."""
    out = []
    for slot in range(1, CALCULATOR_ROWS + 1):
        name = (form.get(f"c{slot}_name") or "").strip()
        row = {
            "food_id": None,
            "name": name,
            "quantity": (form.get(f"c{slot}_quantity") or "").strip() or None,
            "units": form.get(f"c{slot}_units") or "",
            **{key: (form.get(f"c{slot}_{key}") or "").strip()
               for key in config.MACRO_KEYS},
        }
        if name:
            found = fq.food_by_name(name, form.get(f"c{slot}_list")) \
                or fq.food_by_name(name)
            if found is not None:
                row["food_id"] = found["id"]
        out.append(row)
    return out


# --------------------------------------------------------------------------- #
# The catalogue
# --------------------------------------------------------------------------- #
@bp.route("/foods", methods=["GET", "POST"])
@login_required
def foods():
    if request.method == "POST":
        action = request.form.get("action", "save")
        food_id = request.form.get("food_id", type=int)
        try:
            if action == "retire":
                fm.retire_food(food_id, True)
                flash("Retired — it drops out of the pickers and the diary "
                      "lines using it keep reading right.", "ok")
            elif action == "restore":
                fm.retire_food(food_id, False)
                flash("Back in the pickers.", "ok")
            elif action == "delete":
                gone = fm.delete_food(food_id)
                flash(f"Deleted '{gone['name']}'.", "ok")
            else:
                saved = fm.save_food(request.form.to_dict(), food_id=food_id)
                flash(f"Saved '{saved['name']}'.", "ok")
        except food.InvalidFood as exc:
            flash(str(exc), "error")
        return redirect(url_for("diet.foods",
                                list=request.form.get("list") or None))

    chosen = request.args.get("list")
    if chosen not in config.FOOD_LISTS:
        chosen = None
    grouping = request.args.get("grouping") or None
    rows = fq.foods(list_name=chosen, grouping=grouping,
                    search=request.args.get("q"), include_retired=True)

    # Paged, because every card carries a whole edit form: 187 of them is a
    # 630 KB page, which is not a thing to send a phone so that it can correct
    # one portion size. The filters are the other way of getting to a food, and
    # both are faster than turning pages.
    page_count = max(1, -(-len(rows) // PER_PAGE))
    page = min(max(request.args.get("page", type=int) or 1, 1), page_count)
    start = (page - 1) * PER_PAGE

    return render_template(
        "diet/foods.html",
        **_shell(),
        catalogue=rows[start:start + PER_PAGE],
        matched=len(rows),
        page=page,
        page_count=page_count,
        usage=fq.food_usage(),
        chosen=chosen,
        chosen_grouping=grouping,
        search=request.args.get("q") or "",
        lists=config.FOOD_LISTS,
        groupings=fq.groupings_by_list(),
        units=config.FOOD_UNITS,
        counts={name: len(fq.foods(list_name=name, include_retired=True))
                for name in config.FOOD_LISTS},
    )


# --------------------------------------------------------------------------- #
# Targets
# --------------------------------------------------------------------------- #
@bp.route("/targets", methods=["GET", "POST"])
@login_required
def targets():
    """Named profiles, each a stack of dated versions.

    Dated rather than edited in place so that changing today's target does not
    restate a day two years ago as a failure against a number that did not exist
    then - see the note on v_food_days.
    """
    if request.method == "POST":
        target_id = request.form.get("target_id", type=int)
        try:
            if request.form.get("action") == "delete":
                fm.delete_target(target_id)
                flash("Deleted.", "ok")
            else:
                saved = fm.save_target(request.form.to_dict(),
                                       target_id=target_id)
                flash(f"Saved '{saved['name']}' from "
                      f"{saved['starts_on']:%d/%m/%Y}.", "ok")
        except food.InvalidFood as exc:
            flash(str(exc), "error")
        return redirect(url_for("diet.targets"))

    return render_template(
        "diet/targets.html",
        **_shell(),
        targets=fq.targets(),
        in_force=fq.target_for(dt.date.today()),
        names=fq.target_names(),
        default_name=config.DEFAULT_TARGET,
    )


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #
@bp.route("/admin", methods=["GET", "POST"])
@login_required
def admin():
    """Where a new line's List and Grouping start, and the week's shape.

    Preferences rather than data, which is why they live in food_settings and
    why a blank one falls back to config rather than being stored as empty -
    see the note on that table.
    """
    if request.method == "POST":
        values = {}
        for meal in config.MEALS:
            values[f"default_list:{meal}"] = request.form.get(f"list:{meal}")
            values[f"default_grouping:{meal}"] = \
                request.form.get(f"grouping:{meal}")
        values["week_starts_on"] = request.form.get("week_starts_on")
        fm.save_settings(values)
        flash("Saved. New lines start here from now on.", "ok")
        return redirect(url_for("diet.admin"))

    return render_template(
        "diet/admin.html",
        **_shell(),
        lists=config.FOOD_LISTS,
        groupings=fq.groupings_by_list(),
        meals=config.MEALS,
        defaults=fq.meal_defaults(),
        config_defaults=config.FOOD_MEAL_DEFAULTS,
        stored=fq.settings(),
        starts_on=fq.week_starts_on(),
        config_starts_on=config.WEEK_STARTS_ON,
        weekday_names=config.WEEKDAY_NAMES,
        per_meal=config.MAX_ENTRIES_PER_MEAL,
        match_ratio=config.FOOD_MATCH_RATIO,
        coverage=fq.coverage(),
        db_path=config.DB_PATH,
        workbook=config.FOOD_XLSX,
    )


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
@bp.route("/analysis")
@login_required
def analysis():
    grain = request.args.get("grain", "monthly")
    if grain not in fq.GRAINS:
        grain = "monthly"
    periods = fq.by_period(grain)
    return render_template(
        "diet/analysis.html",
        **_shell(),
        grain=grain,
        grains=list(fq.GRAINS),
        periods=list(reversed(periods)),
        coverage=fq.coverage(),
        totals={"foods": fq.total_foods(), "targets": fq.total_targets()},
        unmatched=fq.unmatched_names(15),
        recent=fq.days(limit=14),
        trail=fq.audit_trail(12),
    )
