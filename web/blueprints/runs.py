"""The run tracker.

Seven years of runs scraped from Strava, folded out of the workbook's flattened
(run, breakdown) rows into runs and the ladder of best efforts inside each one.

Six pages:

    overview    the last run, the totals, volume and pace over time
    input       record a new run, or correct an existing one
    analysis    performance split by run type and by effort type
    records     the top five at each breakdown distance
    data        every run as a table, filtered and paged
    admin       what was imported, and what the sheet got wrong
"""
from __future__ import annotations

import datetime as dt

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

import config
from core import run_mutations, run_options, run_queries, runs
from web import run_charts
from web.app import login_required

bp = Blueprint("runs", __name__)

PER_PAGE = 40


# --------------------------------------------------------------------------- #
# Shared request parsing
# --------------------------------------------------------------------------- #
def _selected_filters(default_span: str | None = None) -> dict:
    """The run type, effort type and date range every page can be filtered by.

    Returned as one dict so the templates, the queries and the links that
    preserve the current filter all read the same four names.

    `default_span` is what the page opens on. Most open on the last year, which
    is the window a training decision is actually made over; the records page
    opens on everything, because a personal best that expires after twelve
    months is not what the word means.
    """
    span = request.args.get("range") or default_span or run_queries.DEFAULT_RANGE
    if span not in dict(run_queries.RANGES):
        span = run_queries.DEFAULT_RANGE
    run_type = request.args.get("run_type") or None
    effort_type = request.args.get("effort_type") or None
    return {
        "span": span,
        "run_type": run_type,
        "effort_type": effort_type,
        "start": run_queries.range_start(span),
    }


def _query_args(chosen: dict) -> dict:
    """The filters as keyword arguments for core.run_queries."""
    return {"run_type": chosen["run_type"],
            "effort_type": chosen["effort_type"],
            "start": chosen["start"]}


def _links(chosen: dict) -> dict:
    """The filters as URL parameters, for links that should keep them."""
    return {key: value for key, value in
            (("range", chosen["span"]),
             ("run_type", chosen["run_type"]),
             ("effort_type", chosen["effort_type"])) if value}


def _choices() -> dict:
    """What the filter dropdowns offer - read from the data, not from config."""
    return {"run_types": run_queries.distinct("run_type"),
            "effort_types": run_queries.distinct("effort_type")}


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #
@bp.route("/")
@login_required
def overview():
    coverage = run_queries.coverage()
    if not coverage["runs"]:
        return render_template("runs/empty.html", workbook=config.RUNS_XLSX,
                               sheet=config.RUNS_SHEET)

    chosen = _selected_filters()
    monthly = run_queries.by_period("monthly", **_query_args(chosen))
    last = run_queries.latest()

    return render_template(
        "runs/overview.html",
        coverage=coverage,
        totals=run_queries.totals(**_query_args(chosen)),
        last=last,
        last_bests=run_queries.bests_for(last["id"]) if last else [],
        recent=run_queries.recent(8),
        bests=run_queries.personal_bests(),
        volume_chart=run_charts.volume(monthly, "monthly"),
        pace_chart=run_charts.pace_line(monthly, "monthly"),
        chosen=chosen, links=_links(chosen), ranges=run_queries.RANGES,
        **_choices(),
    )


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
@bp.route("/log", methods=["GET", "POST"])
@login_required
def input_page():
    """Record a new run, or correct one. The fields are the spreadsheet's.

    Pace is not a field. It is distance over time and the form shows it back
    once the run is saved; asking for it as well would let the three disagree.
    Same for the breakdown paces.
    """
    run_id = request.args.get("id", type=int)
    editing = run_queries.run(run_id) if run_id else None
    if run_id and editing is None:
        abort(404)

    if request.method == "POST":
        values = {
            "day": request.form.get("day", ""),
            "distance_km": request.form.get("distance_km", ""),
            "duration_s": request.form.get("duration_s", ""),
            "run_type": request.form.get("run_type", ""),
            "effort_type": request.form.get("effort_type", ""),
            "note": request.form.get("note", ""),
            **{name: request.form.get(name, "")
               for name in runs.INTERVAL_FIELDS},
        }
        breakdowns = {label: request.form.get(f"bd_{index}", "")
                      for index, (label, _) in enumerate(config.BREAKDOWNS)}
        try:
            saved = run_mutations.save_run(values, breakdowns, run_id=run_id)
        except runs.InvalidRun as exc:
            flash(str(exc), "error")
        else:
            splits = len(saved["breakdowns"])
            tail = (f", with {splits} split{'s' if splits != 1 else ''}"
                    if splits else "")
            flash(f"Saved {runs.describe(saved)} on "
                  f"{saved['day']:%d/%m/%Y}{tail}.", "ok")
            return redirect(url_for("runs.detail", run_id=saved["id"]))

    # Always a date object: the form's <input type="date"> needs an ISO string,
    # and the row out of SQLite is already one, which would silently render as
    # itself while `today` rendered as a date.
    when = runs.as_date(editing["day"]) if editing else _requested_date()
    outstanding = run_queries.intervals_outstanding()
    return render_template(
        "runs/input.html",
        editing=editing,
        existing={row["breakdown"]: row for row in
                  (run_queries.bests_for(run_id) if run_id else [])},
        day=when,
        today=dt.date.today(),
        breakdowns=config.BREAKDOWNS,
        run_types=run_options.values('run_type'),
        effort_types=run_options.values('effort_type'),
        interval_types=config.INTERVAL_TYPES,
        interval_type_labels=config.INTERVAL_TYPE_LABELS,
        interval_field_help=config.INTERVAL_FIELD_HELP,
        outstanding=outstanding,
        # A prompt rather than an answer: a session of 1k reps usually has a
        # best 1K close to its average time, which is a useful thing to have
        # in front of you when filling one in from memory.
        best_400m=_best_at(outstanding, "400m"),
        best_1k=_best_at(outstanding, "1K"),
        recent=run_queries.recent(8),
        form=request.form if request.method == "POST" else None,
    )


@bp.route("/run/<int:run_id>")
@login_required
def detail(run_id: int):
    """One run: what it was, its ladder, and any records it holds."""
    row = run_queries.run(run_id)
    if row is None:
        abort(404)
    bests = run_queries.bests_for(run_id)
    positions = {best["breakdown"]: run_queries.is_record(run_id,
                                                          best["breakdown"])
                 for best in bests}
    return render_template(
        "runs/detail.html",
        run=row, bests=bests, positions=positions,
        same_day=[other for other in run_queries.runs_on(row["day"])
                  if other["id"] != run_id],
        top_n=config.TOP_N,
    )


@bp.route("/run/<int:run_id>/delete", methods=["POST"])
@login_required
def delete(run_id: int):
    row = run_queries.run(run_id)
    if row is None:
        abort(404)
    run_mutations.delete_run(run_id)
    flash(f"Deleted the {runs.fmt_distance(row['distance_km'])} km run on "
          f"{runs.as_date(row['day']):%d/%m/%Y}.", "ok")
    return redirect(request.form.get("next") or url_for("runs.data"))


def _best_at(rows: list, breakdown: str) -> dict:
    """run id -> that run's best effort at one distance, formatted."""
    found = {}
    for row in rows:
        for best in run_queries.bests_for(row["id"]):
            if best["breakdown"] == breakdown:
                found[row["id"]] = runs.fmt_duration(best["seconds"])
    return found


def _requested_date(raw: str | None = None):
    raw = raw if raw is not None else request.args.get("day", "")
    if not raw:
        return dt.date.today()
    try:
        return runs.as_date(raw)
    except Exception:
        flash(f"'{raw}' is not a date - showing today instead.", "warn")
        return dt.date.today()


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
@bp.route("/analysis")
@login_required
def analysis():
    """Performance split by run type and by effort type.

    Pace across a group is its total time over its total distance, not the mean
    of each run's pace: a 20 km plod and a 2 km sprint should not count equally
    towards how fast the running was.
    """
    chosen = _selected_filters()
    grain = request.args.get("grain", "monthly")
    if grain not in run_queries.GRAINS:
        grain = "monthly"
    args = _query_args(chosen)

    by_run = run_queries.by_run_type(**args)
    by_effort = run_queries.by_effort_type(**args)
    by_distance = run_queries.by_breakdown(**args)
    periods = run_queries.by_period(grain, **args)

    return render_template(
        "runs/analysis.html",
        totals=run_queries.totals(**args),
        by_run=by_run,
        by_effort=by_effort,
        by_distance=by_distance,
        cross=run_queries.cross_tab(**args),
        sessions=run_queries.interval_sessions(**args),
        session_totals=run_queries.interval_totals(**args),
        sessions_to_add=len(run_queries.intervals_outstanding(**args)),
        periods=periods,
        grain=grain, grains=run_queries.GRAINS,
        volume_chart=run_charts.volume(periods, grain),
        pace_chart=run_charts.pace_line(periods, grain),
        run_pace_chart=_pace_bars(by_run),
        effort_pace_chart=_pace_bars(by_effort),
        run_volume_chart=_volume_bars(by_run),
        effort_volume_chart=_volume_bars(by_effort),
        chosen=chosen, links=_links(chosen), ranges=run_queries.RANGES,
        **_choices(),
    )


def _pace_bars(rows: list) -> str:
    """Average pace per category. Shorter bar is quicker, and it says so."""
    return run_charts.bars(
        [(row["label"], row["pace_s"],
          f"{row['label']}: {runs.fmt_pace(row['pace_s'], True)} "
          f"across {row['runs']} run{'s' if row['runs'] != 1 else ''}")
         for row in rows],
        faster_is_better=True,
        formatter=runs.fmt_pace,
    )


def _volume_bars(rows: list) -> str:
    return run_charts.bars(
        [(row["label"], row["distance_km"],
          f"{row['label']}: {runs.fmt_distance(row['distance_km'], 1)} km "
          f"across {row['runs']} run{'s' if row['runs'] != 1 else ''}")
         for row in rows],
        unit=" km",
        formatter=lambda value: f"{value:,.0f}",
    )


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
@bp.route("/records")
@login_required
def records():
    """The top five at each breakdown distance.

    Splits the source sheet contradicts itself about are left out by default -
    see v_run_bests in core/schema.sql - and `?flagged=1` puts them back, so
    what has been set aside can be looked at rather than taken on trust.
    """
    chosen = _selected_filters(default_span="All")
    top = min(max(request.args.get("top", type=int) or config.TOP_N, 1), 25)
    include_suspect = request.args.get("flagged") == "1"

    tables = run_queries.records(top=top, include_suspect=include_suspect,
                                 **_query_args(chosen))
    return render_template(
        "runs/records.html",
        tables=tables,
        top=top,
        include_suspect=include_suspect,
        suspect_count=run_queries.suspect_count(),
        breakdown_km=config.BREAKDOWN_KM,
        chosen=chosen, links=_links(chosen), ranges=run_queries.RANGES,
        **_choices(),
    )


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@bp.route("/data")
@login_required
def data():
    chosen = _selected_filters()
    page = max(request.args.get("page", type=int) or 1, 1)
    rows = run_queries.runs_list(limit=PER_PAGE, offset=(page - 1) * PER_PAGE,
                                 **_query_args(chosen))
    return render_template(
        "runs/data.html",
        rows=rows, page=page, per_page=PER_PAGE,
        has_next=len(rows) == PER_PAGE,
        totals=run_queries.totals(**_query_args(chosen)),
        chosen=chosen, links=_links(chosen), ranges=run_queries.RANGES,
        **_choices(),
    )


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #
@bp.route("/admin", methods=["GET", "POST"])
@login_required
def admin():
    if request.method == "POST":
        kind = request.form.get("kind", "")
        try:
            if request.form.get("action") == "reset":
                run_options.reset(kind)
                flash(f"{run_options.LABELS[kind]} list put back to the "
                      f"built-in one, plus anything runs still use.", "ok")
            else:
                result = run_options.replace(
                    kind, run_options.parse_form(request.form.get("options")))
                flash(_options_saved(result), "ok")
        except (run_options.InvalidOption, KeyError) as exc:
            flash(str(exc).strip("'"), "error")
        return redirect(url_for("runs.admin"))

    return render_template(
        "runs/admin.html",
        coverage=run_queries.coverage(),
        anomalies=run_queries.anomalies(),
        trail=run_queries.audit_trail(60),
        by_distance=run_queries.by_breakdown(),
        db_path=config.DB_PATH,
        workbook=config.RUNS_XLSX,
        sheet=config.RUNS_SHEET,
        breakdowns=config.BREAKDOWNS,
        option_labels=run_options.LABELS,
        option_text={kind: run_options.as_form(kind)
                     for kind in run_options.KINDS},
        option_usage={kind: run_options.with_usage(kind)
                      for kind in run_options.KINDS},
        option_orphans={kind: run_options.orphans(kind)
                        for kind in run_options.KINDS},
    )


def _options_saved(result: dict) -> str:
    """Say what the save actually did, rather than just that it happened."""
    label = run_options.LABELS[result["kind"]]
    parts = []
    if result["added"]:
        parts.append("added " + ", ".join(result["added"]))
    if result["removed"]:
        parts.append("removed " + ", ".join(result["removed"]))
    if result["reordered"] and not parts:
        parts.append("reordered")
    return (f"{label}: {'; '.join(parts)}." if parts
            else f"{label} list unchanged.")
