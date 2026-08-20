"""The workout plan and tracker.

Six pages:

    session     one session, picked with three dropdowns - the landing page,
                because it is the one that gets used standing up in a gym
    plan        pick a plan, see its phases, 1RMs and weeks
    week        one week as the workbook lays it out, session by session
    build       the session builder - one session at a time
    tracker     tick sessions off, and see how far through a plan you are
    exercises   the movement catalogue behind every dropdown

The builder takes one session at a time on purpose. A nineteen-week programme is
six week shapes repeated three times, so the way it actually gets built is: make
one session, copy the week it belongs to, change the percentages. Generating a
whole cycle from a form would need every one of those variations expressed as
parameters before you could see any of it.

The form's shape follows the workbook rather than being a generic set editor.
Each exercise slot offers up to three warm-up sets prescribed individually, then
a working block - a number of identical sets - then an accessory block, and
whichever are filled in are what gets created. Every one of the source workbook's
564 sets fits that shape, because the sheet was written by someone doing the same
thing every week.
"""
from __future__ import annotations

import datetime as dt

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

import config
from core import (workout_mutations as wm, workout_queries as wq, workouts)
from web.app import login_required

bp = Blueprint("workouts", __name__)


def _plan_or_404(plan_id: int | None):
    """The plan being looked at: the one asked for, else the current one."""
    if plan_id:
        row = wq.plan(plan_id)
        if row is None:
            abort(404)
        return row
    return wq.current_plan()


def _selected_plan():
    return _plan_or_404(request.args.get("plan", type=int))


def _shell(plan, **extra) -> dict:
    """What every page in this section needs: the plan picker and the plan."""
    return {"plan": plan, "plans": wq.plans(), **extra}


def _pick(rows: list, wanted: int | None, key: str = "id"):
    """The row asked for, else the first. What a dropdown-driven page needs.

    Falling back rather than 404ing, because the id in the query string is
    usually stale rather than wrong - a plan was switched underneath it - and
    showing the first phase of the plan you are looking at beats an error page.
    """
    if not rows:
        return None
    if wanted is not None:
        for row in rows:
            if row[key] == wanted:
                return row
    return rows[0]


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #
@bp.route("/plan")
@login_required
def plan():
    row = _selected_plan()
    if row is None:
        return render_template("workouts/empty.html",
                               workbook=config.GYM_XLSX,
                               exercises=len(wq.exercises()))
    phases = wq.phases(row["id"])
    return render_template(
        "workouts/plan.html",
        **_shell(row),
        phases=phases,
        # One phase at a time, picked from a dropdown. Seven columns of scheme
        # is a table nobody can read on a phone, and the parts of a phase are a
        # list of labelled values rather than a row of anything.
        chosen_phase=_pick(phases, request.args.get("phase", type=int)),
        maxes=wq.maxes(row["id"]),
        weeks=wq.weeks(row["id"]),
        totals=wq.totals(row["id"]),
        without_max=wq.percent_sets_without_a_max(row["id"]),
        catalogue=wq.exercises(),
        rounding_steps=config.ROUNDING_STEPS,
        today=dt.date.today(),
    )


@bp.route("/plan/save", methods=["POST"])
@login_required
def save_plan():
    plan_id = request.form.get("plan_id", type=int)
    try:
        saved = wm.save_plan(request.form.to_dict(), plan_id=plan_id)
    except workouts.InvalidWorkout as exc:
        flash(str(exc), "error")
        return redirect(url_for("workouts.plan", plan=plan_id))
    flash(f"Saved '{saved['name']}'.", "ok")
    return redirect(url_for("workouts.plan", plan=saved["id"]))


@bp.route("/plan/<int:plan_id>/copy", methods=["POST"])
@login_required
def copy_plan(plan_id: int):
    try:
        made = wm.copy_plan(plan_id, request.form.get("name", ""),
                            started_on=request.form.get("started_on") or None,
                            with_maxes=bool(request.form.get("with_maxes")))
    except workouts.InvalidWorkout as exc:
        flash(str(exc), "error")
        return redirect(url_for("workouts.plan", plan=plan_id))
    flash(f"Copied '{made['copied_from']}' into '{made['name']}' - "
          f"{made['weeks']} weeks and {made['sessions']} sessions, with "
          f"nothing ticked off.", "ok")
    return redirect(url_for("workouts.plan", plan=made["id"]))


@bp.route("/plan/<int:plan_id>/delete", methods=["POST"])
@login_required
def delete_plan(plan_id: int):
    try:
        gone = wm.delete_plan(plan_id)
    except workouts.InvalidWorkout as exc:
        flash(str(exc), "error")
    else:
        flash(f"Deleted '{gone['name']}' and its {gone['weeks']} weeks.", "ok")
    return redirect(url_for("workouts.plan"))


@bp.route("/plan/<int:plan_id>/max", methods=["POST"])
@login_required
def save_max(plan_id: int):
    try:
        wm.set_max(plan_id, request.form.get("exercise_id", type=int),
                   request.form.get("one_rm_kg"))
    except workouts.InvalidWorkout as exc:
        flash(str(exc), "error")
    else:
        flash("1RM saved - every percentage set on this plan follows it.", "ok")
    return redirect(url_for("workouts.plan", plan=plan_id))


@bp.route("/plan/<int:plan_id>/phase", methods=["POST"])
@login_required
def save_phase(plan_id: int):
    try:
        wm.save_phase(plan_id, request.form.to_dict(),
                      phase_id=request.form.get("phase_id", type=int))
    except workouts.InvalidWorkout as exc:
        flash(str(exc), "error")
    else:
        flash("Phase saved.", "ok")
    return redirect(url_for("workouts.plan", plan=plan_id))


@bp.route("/plan/<int:plan_id>/week", methods=["POST"])
@login_required
def save_week(plan_id: int):
    week_id = request.form.get("week_id", type=int)
    try:
        made = wm.save_week(plan_id, request.form.to_dict(), week_id=week_id)
    except workouts.InvalidWorkout as exc:
        flash(str(exc), "error")
        return redirect(url_for("workouts.plan", plan=plan_id))
    flash(f"Week {made['number']} saved.", "ok")
    return redirect(url_for("workouts.week", week_id=made["id"]))


@bp.route("/week/<int:week_id>/copy", methods=["POST"])
@login_required
def copy_week(week_id: int):
    week = wq.week(week_id)
    if week is None:
        abort(404)
    try:
        made = wm.copy_week(week_id, week["plan_id"],
                            request.form.get("number", type=int))
    except workouts.InvalidWorkout as exc:
        flash(str(exc), "error")
        return redirect(url_for("workouts.week", week_id=week_id))
    flash(f"Copied into week {made['number']}.", "ok")
    return redirect(url_for("workouts.week", week_id=made["id"]))


@bp.route("/week/<int:week_id>/delete", methods=["POST"])
@login_required
def delete_week(week_id: int):
    week = wq.week(week_id)
    if week is None:
        abort(404)
    wm.delete_week(week_id)
    flash(f"Deleted week {week['number']}.", "ok")
    return redirect(url_for("workouts.plan", plan=week["plan_id"]))


# --------------------------------------------------------------------------- #
# One session - the page you open at the gym
# --------------------------------------------------------------------------- #
@bp.route("/")
@login_required
def session():
    """Plan, week, session as three dropdowns, and nothing else on the page.

    The section's landing page - it owns /workouts/ itself, the way every other
    section's landing page owns its root - because it is the one that gets used
    standing up holding a phone. Everything else here is planning, which happens sitting
    down: getting to a session through the plan, then the week, then reading the
    right half of a table was three taps and a horizontal scroll too many.

    It opens on the next session not ticked off. Changing the plan or the week
    re-picks within it rather than 404ing, so the dropdowns can be moved in any
    order without ever landing on nothing - see _pick().
    """
    plan = _selected_plan()
    if plan is None:
        return render_template("workouts/empty.html",
                               workbook=config.GYM_XLSX,
                               exercises=len(wq.exercises()))

    weeks = wq.weeks(plan["id"])
    wanted_session = request.args.get("session", type=int)
    wanted_week = request.args.get("week", type=int)

    # With nothing asked for, open on what is due next. The week follows from
    # the session rather than being chosen separately, so the two cannot
    # disagree about which week is being looked at.
    if wanted_session is None and wanted_week is None:
        due = wq.next_session(plan["id"])
        if due is not None:
            wanted_session, wanted_week = due["id"], due["week_id"]

    week = _pick(weeks, wanted_week)
    if week is None:
        return render_template("workouts/session.html", **_shell(plan),
                               weeks=[], week=None, sessions=[], session=None,
                               sheet=[], today=dt.date.today())

    sessions = wq.sessions(week_id=week["id"])
    chosen = _pick(sessions, wanted_session)
    return render_template(
        "workouts/session.html",
        **_shell(plan),
        weeks=weeks,
        week=week,
        sessions=sessions,
        session=chosen,
        sheet=wq.session_sheet(chosen["id"]) if chosen else [],
        neighbours=_neighbours(plan["id"], chosen),
        today=dt.date.today(),
    )


def _neighbours(plan_id: int, current) -> dict:
    """The session before and after this one, across week boundaries.

    So a session can be stepped through without going back to the dropdowns,
    which is what you want between the last set of one and the warm-up of the
    next.
    """
    if current is None:
        return {"previous": None, "next": None}
    ordered = wq.sessions(plan_id=plan_id)
    index = next((i for i, row in enumerate(ordered)
                  if row["id"] == current["id"]), None)
    if index is None:
        return {"previous": None, "next": None}
    return {"previous": ordered[index - 1] if index > 0 else None,
            "next": ordered[index + 1] if index + 1 < len(ordered) else None}


# --------------------------------------------------------------------------- #
# One week
# --------------------------------------------------------------------------- #
@bp.route("/week/<int:week_id>")
@login_required
def week(week_id: int):
    row = wq.week(week_id)
    if row is None:
        abort(404)
    plan = wq.plan(row["plan_id"])
    return render_template(
        "workouts/week.html",
        **_shell(plan),
        week=row,
        weeks=wq.weeks(plan["id"]),
        sheet=wq.week_sheet(week_id),
        phases=wq.phases(plan["id"]),
        today=dt.date.today(),
    )


# --------------------------------------------------------------------------- #
# The session builder
# --------------------------------------------------------------------------- #
@bp.route("/build", methods=["GET", "POST"])
@login_required
def build():
    week_id = request.args.get("week", type=int)
    session_id = request.args.get("session", type=int)

    editing = wq.session(session_id) if session_id else None
    if session_id and editing is None:
        abort(404)
    if editing:
        week_id = editing["week_id"]

    week = wq.week(week_id) if week_id else None
    if week is None:
        flash("Pick a week to build a session in.", "error")
        return redirect(url_for("workouts.plan"))
    plan = wq.plan(week["plan_id"])

    if request.method == "POST":
        try:
            saved = wm.save_session(week_id, request.form.to_dict(),
                                    _exercises_from_form(request.form),
                                    session_id=session_id)
        except workouts.InvalidWorkout as exc:
            flash(str(exc), "error")
        else:
            flash(f"Saved session {saved['number']}: {saved['exercises']} "
                  f"exercises, {saved['sets']} sets.", "ok")
            return redirect(url_for("workouts.week", week_id=week_id))

    phase = wq.phase(week["phase_id"]) if week["phase_id"] else None
    return render_template(
        "workouts/build.html",
        **_shell(plan),
        week=week,
        editing=editing,
        sheet=wq.session_sheet(session_id) if session_id else [],
        catalogue=wq.exercises(),
        maxes={row["exercise_id"]: row["one_rm_kg"]
               for row in wq.maxes(plan["id"])},
        phase=phase,
        slots=range(1, config.MAX_EXERCISES_PER_SESSION + 1),
        warmups=range(1, config.MAX_WARMUP_SETS + 1),
        load_modes=config.LOAD_MODES,
        load_mode_labels=config.LOAD_MODE_LABELS,
        rest_options=config.REST_OPTIONS,
        cue_options=config.CUE_OPTIONS,
        default_rest=config.DEFAULT_REST,
        next_number=_next_session_number(week_id, editing),
        form=request.form if request.method == "POST" else None,
    )


def _next_session_number(week_id: int, editing) -> int:
    if editing:
        return editing["number"]
    used = {row["number"] for row in wq.sessions(week_id=week_id)}
    for number in range(1, config.MAX_SESSIONS_PER_WEEK + 1):
        if number not in used:
            return number
    return config.MAX_SESSIONS_PER_WEEK


def _exercises_from_form(form) -> list:
    """Read the builder's fixed grid back into the shape save_session wants.

    Ten exercise slots, each with three individually prescribed warm-up sets and
    two blocks - working and accessory - that each expand into n identical sets.
    A slot with no exercise chosen is skipped, which is what lets the form be a
    fixed size and the sessions be any size up to it.
    """
    exercises = []
    for slot in range(1, config.MAX_EXERCISES_PER_SESSION + 1):
        exercise_id = form.get(f"ex{slot}_id", "").strip()
        if not exercise_id:
            continue

        sets = []
        for index in range(1, config.MAX_WARMUP_SETS + 1):
            prefix = f"ex{slot}_w{index}"
            mode = form.get(f"{prefix}_mode", "").strip()
            reps = form.get(f"{prefix}_reps", "").strip()
            if not mode or (not reps and mode != "choose"):
                continue
            sets.append(_one_set(form, prefix, "warmup", reps, mode,
                                 form.get(f"ex{slot}_w_rest", "")))

        for kind in ("working", "accessory"):
            prefix = f"ex{slot}_{kind}"
            count = form.get(f"{prefix}_sets", "").strip()
            mode = form.get(f"{prefix}_mode", "").strip()
            if not count or not mode:
                continue
            try:
                count = int(count)
            except ValueError:
                raise workouts.InvalidWorkout(
                    f"'{count}' is not a number of {kind} sets") from None
            if count < 1:
                continue
            reps = form.get(f"{prefix}_reps", "").strip()
            rest = form.get(f"{prefix}_rest", "")
            for _ in range(count):
                sets.append(_one_set(form, prefix, kind, reps, mode, rest))

        exercises.append({
            "exercise_id": exercise_id,
            "reps_mode": form.get(f"ex{slot}_reps_mode", ""),
            "weight_mode": form.get(f"ex{slot}_weight_mode", ""),
            "note": form.get(f"ex{slot}_note", ""),
            "sets": sets,
        })
    return exercises


def _one_set(form, prefix: str, set_type: str, reps: str, mode: str,
             rest: str) -> dict:
    return {
        "set_type": set_type,
        "reps": reps,
        "load_mode": mode,
        "weight_kg": form.get(f"{prefix}_kg", ""),
        "percent_1rm": form.get(f"{prefix}_pct", ""),
        "added_kg": form.get(f"{prefix}_added", ""),
        "rest": rest or config.DEFAULT_REST.get(set_type, ""),
        "cue": form.get(f"{prefix}_cue", ""),
    }


@bp.route("/session/<int:session_id>/delete", methods=["POST"])
@login_required
def delete_session(session_id: int):
    row = wq.session(session_id)
    if row is None:
        abort(404)
    wm.delete_session(session_id)
    flash(f"Deleted {workouts.session_title(row)}.", "ok")
    return redirect(url_for("workouts.week", week_id=row["week_id"]))


@bp.route("/session/<int:session_id>/copy", methods=["POST"])
@login_required
def copy_session(session_id: int):
    target = request.form.get("week_id", type=int)
    row = wq.session(session_id)
    if row is None:
        abort(404)
    try:
        wm.copy_session(session_id, target or row["week_id"])
    except workouts.InvalidWorkout as exc:
        flash(str(exc), "error")
    else:
        flash("Session copied.", "ok")
    return redirect(url_for("workouts.week", week_id=target or row["week_id"]))


# --------------------------------------------------------------------------- #
# Tracker
# --------------------------------------------------------------------------- #
@bp.route("/tracker")
@login_required
def tracker():
    row = _selected_plan()
    if row is None:
        return render_template("workouts/empty.html",
                               workbook=config.GYM_XLSX,
                               exercises=len(wq.exercises()))
    return render_template(
        "workouts/tracker.html",
        **_shell(row),
        totals=wq.totals(row["id"]),
        progress=wq.week_progress(row["id"]),
        sessions=wq.sessions(plan_id=row["id"]),
        next_up=wq.next_session(row["id"]),
        recent=wq.done_log(row["id"], 15),
        volume=wq.volume_by_exercise(row["id"], done_only=True),
        today=dt.date.today(),
    )


@bp.route("/session/<int:session_id>/tick", methods=["POST"])
@login_required
def tick(session_id: int):
    done = request.form.get("done") != "0"
    try:
        wm.tick_session(session_id, done, request.form.get("done_on") or None,
                        request.form.get("note"))
    except workouts.InvalidWorkout as exc:
        flash(str(exc), "error")
    return redirect(request.form.get("next")
                    or url_for("workouts.tracker"))


@bp.route("/week/<int:week_id>/tick", methods=["POST"])
@login_required
def tick_week(week_id: int):
    done = request.form.get("done") != "0"
    changed = wm.tick_week(week_id, done, request.form.get("done_on") or None)
    flash(f"{changed} session{'' if changed == 1 else 's'} "
          f"{'ticked off' if done else 'un-ticked'}.", "ok")
    return redirect(request.form.get("next") or url_for("workouts.tracker"))


# --------------------------------------------------------------------------- #
# The exercise catalogue
# --------------------------------------------------------------------------- #
@bp.route("/exercises", methods=["GET", "POST"])
@login_required
def exercises():
    if request.method == "POST":
        action = request.form.get("action", "save")
        exercise_id = request.form.get("exercise_id", type=int)
        try:
            if action == "retire":
                wm.retire_exercise(exercise_id, True)
                flash("Retired - it drops out of the dropdowns and the plans "
                      "using it keep reading right.", "ok")
            elif action == "restore":
                wm.retire_exercise(exercise_id, False)
                flash("Back in the dropdowns.", "ok")
            elif action == "delete":
                gone = wm.delete_exercise(exercise_id)
                flash(f"Deleted '{gone['name']}'.", "ok")
            else:
                saved = wm.save_exercise(request.form.to_dict(),
                                         exercise_id=exercise_id)
                flash(f"Saved '{saved['name']}'.", "ok")
        except workouts.InvalidWorkout as exc:
            flash(str(exc), "error")
        return redirect(url_for("workouts.exercises"))

    return render_template(
        "workouts/exercises.html",
        **_shell(_selected_plan()),
        catalogue=wq.catalogue_with_usage(),
        reps_modes=config.REPS_MODES,
        weight_modes=config.WEIGHT_MODES,
        reps_mode_labels=config.REPS_MODE_LABELS,
        weight_mode_labels=config.WEIGHT_MODE_LABELS,
    )
