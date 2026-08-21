"""The food planner and diary.

Six pages:

    day         one day, picked by date - the landing page, because the thing
                that actually gets done is checking today against the target
    week        seven days at the width config says a week is, and the paste
                that plans them from one
    calculator  components in, macros out, with a scaling factor
    foods       the catalogue behind every dropdown
    targets     the named, dated macro profiles a day is measured against
    analysis    two years of diary, averaged

The day page is the workbook's DailyCheck sheet, and the one thing it does
differently is the thing that was asked for: it is addressed by date rather than
by week-and-day. A diary with 48 of 137 possible weeks in it cannot be navigated
by counting from the start, and "what did I eat on the 12th" is the question
being asked anyway.

Entries are saved a whole day at a time - see the note on food_mutations.save_day
about why they are replaced rather than diffed.
"""
from __future__ import annotations

import datetime as dt

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

import config
from core import food, food_mutations as fm, food_queries as fq
from web.app import login_required

bp = Blueprint("diet", __name__)

# How many blank lines the day form offers below whatever is already there, and
# how many rows the calculator has. Fixed-size forms, the same way the session
# builder is: a slot left empty is skipped, which is what lets the form be one
# shape and the day be any length up to it.
BLANK_ROWS = 4
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


def _catalogue_lookup(rows: list) -> dict:
    """id -> what the fill-in script needs, written into the page once.

    The dropdowns carry ids and nothing else; this is the other half. Keeping it
    to one copy is the difference between a 100 KB page and a 580 KB one, which
    over a Tailscale link to a phone is the difference between the page opening
    and the page eventually opening.
    """
    return {row["id"]: {"portion": row["portion"], "units": row["units"],
                        "name": row["name"],
                        **{key: row[key] for key in config.MACRO_KEYS}}
            for row in rows}


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
    row = fq.day(when)
    sheet = fq.day_sheet(when)
    totals = food.add_macros(fq.entries(when))
    # `chosen_target`, not `target_name`: the first is what this day asked for
    # and the second is what it ends up measured against - see v_food_days.
    target = fq.target_for(when, row["chosen_target"] if row else None) \
        or fq.target_for(when)
    catalogue = fq.foods()

    return render_template(
        "diet/day.html",
        **_shell(),
        day=when,
        row=row,
        sheet=sheet,
        totals=totals,
        target=target,
        remaining=food.remaining(totals, target or {}),
        pct=food.pct_of_target(totals, target or {}),
        weekday=food.day_name(when),
        week_label=food.week_label(when),
        previous=when - dt.timedelta(days=1),
        next_day=when + dt.timedelta(days=1),
        catalogue=catalogue,
        lookup=_catalogue_lookup(catalogue),
        recent=fq.recent_foods(12),
        target_names=fq.target_names(),
        meals=config.MEALS,
        units=config.FOOD_UNITS,
        blanks=range(BLANK_ROWS),
    )


@bp.route("/day/save", methods=["POST"])
@login_required
def save_day():
    when = _date_arg()
    try:
        saved = fm.save_day(when, _entries_from_form(request.form),
                            target_name=request.form.get("target_name") or None,
                            note=request.form.get("note"))
    except food.InvalidFood as exc:
        flash(str(exc), "error")
    else:
        flash(f"Saved {when:%a %d/%m/%Y} - {saved['entries']} "
              f"line{'' if saved['entries'] == 1 else 's'}.", "ok")
    return redirect(url_for("diet.day", day=when.isoformat()))


def _entries_from_form(form) -> list:
    """Read the day form back into the list save_day wants.

    Each row is either a catalogue food and a quantity, or a name and its macros
    typed in - the second is what makes a meal out somewhere recordable at all,
    and two years of the imported diary are exactly that. A row with neither is
    skipped, which is how the blank lines at the bottom stay harmless.

    The `_delete` checkbox is honoured here rather than by a separate route
    because the day is saved wholesale: leaving a row out of the list is the
    deletion.
    """
    entries = []
    for index in _row_indexes(form, "row"):
        if form.get(f"row{index}_delete"):
            continue
        food_id = (form.get(f"row{index}_food_id") or "").strip()
        name = " ".join((form.get(f"row{index}_name") or "").split())
        macros = {key: (form.get(f"row{index}_{key}") or "").strip()
                  for key in config.MACRO_KEYS}
        if not food_id and not name and not any(macros.values()):
            continue
        entries.append({
            "meal": form.get(f"row{index}_meal") or config.MEALS[0],
            "food_id": int(food_id) if food_id else None,
            "name": name,
            "quantity": (form.get(f"row{index}_quantity") or "").strip(),
            "units": (form.get(f"row{index}_units") or "").strip(),
            **macros,
        })
    return entries


def _row_indexes(form, prefix: str) -> list:
    """The row numbers present in a form, in order.

    Read off the form rather than assumed, so the existing lines and the blank
    ones below them can be numbered in one sequence without the template and the
    parser having to agree in advance on how many there are.
    """
    found = set()
    for key in form.keys():
        if not key.startswith(prefix):
            continue
        number = key[len(prefix):].split("_", 1)[0]
        if number.isdigit():
            found.add(int(number))
    return sorted(found)


@bp.route("/day/delete", methods=["POST"])
@login_required
def delete_day():
    when = _date_arg()
    try:
        gone = fm.delete_day(when)
    except food.InvalidFood as exc:
        flash(str(exc), "error")
    else:
        flash(f"Cleared {when:%a %d/%m/%Y} - {gone['entries']} lines.", "ok")
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
    flash(f"Copied {source:%d/%m/%Y} into {target:%d/%m/%Y} - "
          f"{made['entries']} lines.", "ok")
    return redirect(url_for("diet.day", day=target.isoformat()))


# --------------------------------------------------------------------------- #
# The week
# --------------------------------------------------------------------------- #
@bp.route("/week")
@login_required
def week():
    """Seven days, starting on whichever day config names.

    The start day is a parameter rather than a Monday: the diary's history is
    Monday-based and the "w/c" labels say so, but which day a planning week turns
    over on is a habit, and changing it should not need a migration. `?starts_on`
    overrides it for one look without changing the setting.
    """
    anchor = _date_arg()
    starts_on = request.args.get("starts_on", type=int)
    if starts_on is not None and not 0 <= starts_on <= 6:
        starts_on = None
    data = fq.week(anchor, starts_on)

    planned = [row for row in data["days"] if row["planned"]]
    averages = {key: round(data["totals"][key] / len(planned), 1)
                for key in config.MACRO_KEYS} if planned else dict(food.ZERO)

    return render_template(
        "diet/week.html",
        **_shell(),
        anchor=anchor,
        week=data,
        planned=len(planned),
        averages=averages,
        target=fq.target_for(data["start"]),
        starts_on=config.WEEK_STARTS_ON if starts_on is None else starts_on,
        weekday_names=config.WEEKDAY_NAMES,
        previous=data["start"] - dt.timedelta(days=7),
        next_week=data["start"] + dt.timedelta(days=7),
    )


@bp.route("/week/fill", methods=["POST"])
@login_required
def fill_week():
    anchor = _date_arg("anchor")
    starts_on = request.form.get("starts_on", type=int)
    try:
        made = fm.fill_week(_date_arg("source"), anchor, starts_on,
                            overwrite=bool(request.form.get("overwrite")))
    except food.InvalidFood as exc:
        flash(str(exc), "error")
        return redirect(url_for("diet.week", day=anchor.isoformat()))

    message = (f"Copied {made['source']:%d/%m/%Y} into "
               f"{len(made['copied'])} day{'' if len(made['copied']) == 1 else 's'}")
    if made["skipped"]:
        message += (f"; left {len(made['skipped'])} alone because "
                    f"{'it already had' if len(made['skipped']) == 1 else 'they already had'} "
                    f"entries")
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

    A GET is a blank sheet. Nothing is stored unless the answer is saved to the
    catalogue, because most of what gets worked out here is a one-off.
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

    catalogue = fq.foods()
    return render_template(
        "diet/calculator.html",
        **_shell(),
        catalogue=catalogue,
        lookup=_catalogue_lookup(catalogue),
        rows=rows,
        scale=scale,
        result=result,
        slots=range(1, CALCULATOR_ROWS + 1),
        lists=config.FOOD_LISTS,
        units=config.FOOD_UNITS,
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
    return [{
        "food_id": (form.get(f"c{slot}_food_id") or "").strip() or None,
        "name": form.get(f"c{slot}_name") or "",
        "quantity": (form.get(f"c{slot}_quantity") or "").strip() or None,
        "units": form.get(f"c{slot}_units") or "",
        **{key: (form.get(f"c{slot}_{key}") or "").strip()
           for key in config.MACRO_KEYS},
    } for slot in range(1, CALCULATOR_ROWS + 1)]


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
                flash("Retired - it drops out of the dropdowns and the diary "
                      "lines using it keep reading right.", "ok")
            elif action == "restore":
                fm.retire_food(food_id, False)
                flash("Back in the dropdowns.", "ok")
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
    rows = fq.foods(list_name=chosen, search=request.args.get("q"),
                    include_retired=True)

    # Paged, because every card carries a whole edit form: 187 of them is a
    # 630 KB page, which is not a thing to send a phone so that it can correct
    # one portion size. The filter and the search are the other two ways of
    # getting to a food, and both are faster than turning pages.
    # `page_count`, not `pages`: base.html already binds `pages` to the tab
    # strip, and a template variable that quietly shadows the navigation is a
    # blank nav bar on one page and nobody knowing why.
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
        search=request.args.get("q") or "",
        lists=config.FOOD_LISTS,
        groupings=config.FOOD_GROUPINGS,
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
