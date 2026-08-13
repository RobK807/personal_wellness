"""The weigh-in tracker, unchanged in behaviour from the standalone app.

Six years of weigh-ins: two a day through the input form, averaged into the
day's figures, with the missed days back-filled on a straight line between the
readings either side. Everything downstream - the charts, the changes, the
weekly and monthly tables - is calculated from that average rather than stored.

The routes below are the standalone tracker's, moved into a blueprint so the
dashboard can hold four sections. The only differences are the url_for names,
which now carry the `weigh_in.` prefix, and the templates, which moved into
templates/weigh_in/.
"""
from __future__ import annotations

import datetime as dt

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   send_file, url_for)

import config
from core import metrics, mutations, queries
from web import charts
from web.app import login_required

bp = Blueprint("weigh_in", __name__)


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #
@bp.route("/")
@login_required
def overview():
    head = queries.headline()
    if head["latest"] is None:
        return render_template("weigh_in/empty.html", workbook=config.SOURCE_XLSX)

    window = queries.series("daily", start=queries.range_start("90d"))
    return render_template(
        "weigh_in/overview.html",
        head=head,
        window=window,
        pair_charts=[(a, b, charts.pair(window, a, b, "daily"))
                     for a, b in config.CHART_PAIRS],
        sparklines={key: charts.sparkline(window, key) for key in config.ALL_KEYS},
        today=dt.date.today().isoformat(),
    )


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
@bp.route("/input", methods=["GET", "POST"])
@login_required
def input_page():
    when = _requested_date()

    if request.method == "POST":
        when = _requested_date(request.form.get("day"))
        readings, problems = {}, []
        for slot in config.SLOTS:
            values = {key: request.form.get(f"s{slot}_{key}", "").strip()
                      for key in config.METRIC_KEYS}
            if not any(values.values()):
                continue  # weigh-in left blank: nothing to save, not an error
            try:
                readings[slot] = metrics.parse_reading(values)
            except metrics.InvalidReading as exc:
                problems.append(f"Weigh-in {slot}: {exc}")

        if problems:
            for line in problems:
                flash(line, "error")
        elif not readings:
            flash("Nothing entered.", "warn")
        else:
            try:
                result = mutations.save_day(when, readings,
                                            note=request.form.get("note", ""))
            except metrics.InvalidReading as exc:
                flash(str(exc), "error")
            else:
                saved = ", ".join(f"weigh-in {s}" for s in result["slots"])
                flash(f"Saved {saved} for {when:%d/%m/%Y}.", "ok")
                if result["filled"]:
                    flash(f"Filled in {result['filled']} missed day"
                          f"{'s' if result['filled'] != 1 else ''} by interpolation.",
                          "ok")
                for line in result["skipped_gap"]:
                    flash(line, "warn")
                return redirect(url_for("weigh_in.input_page",
                                        day=when.isoformat()))

    existing = {row["slot"]: row for row in queries.readings_for(when)}
    return render_template(
        "weigh_in/input.html",
        day=when,
        today=dt.date.today(),
        existing=existing,
        day_row=queries.day(when),
        previous=queries.day(when - dt.timedelta(days=1)),
        recent=queries.recent_days(limit=10),
        form=request.form if request.method == "POST" else None,
    )


@bp.route("/input/delete", methods=["POST"])
@login_required
def delete_entry():
    when = _requested_date(request.form.get("day"))
    slot = request.form.get("slot", "")
    try:
        if slot in ("1", "2"):
            mutations.delete_reading(when, int(slot))
            flash(f"Deleted weigh-in {slot} for {when:%d/%m/%Y}.", "ok")
        else:
            mutations.delete_day(when)
            flash(f"Deleted every reading for {when:%d/%m/%Y}.", "ok")
    except metrics.InvalidReading as exc:
        flash(str(exc), "error")
    return redirect(request.form.get("next")
                    or url_for("weigh_in.input_page", day=when.isoformat()))


def _requested_date(raw: str | None = None):
    """The date a page is about: the `day` parameter, or today."""
    raw = raw if raw is not None else request.args.get("day", "")
    if not raw:
        return dt.date.today()
    try:
        return metrics.as_date(raw)
    except metrics.InvalidReading:
        flash(f"'{raw}' is not a date - showing today instead.", "warn")
        return dt.date.today()


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
@bp.route("/charts")
@login_required
def trends():
    grain = request.args.get("grain", "daily")
    if grain not in queries.GRAINS:
        grain = "daily"
    span = request.args.get("range", queries.DEFAULT_RANGE)
    if span not in dict(queries.RANGES):
        span = queries.DEFAULT_RANGE

    rows = queries.series(grain, start=queries.range_start(span))
    return render_template(
        "weigh_in/charts.html",
        grain=grain, span=span, rows=rows,
        grains=queries.GRAINS, ranges=[label for label, _ in queries.RANGES],
        pair_charts=[(a, b, charts.pair(rows, a, b, grain))
                     for a, b in config.CHART_PAIRS],
        single_charts=[(key, charts.line(rows, key, grain))
                       for key in config.ALL_KEYS],
    )


# --------------------------------------------------------------------------- #
# Changes
# --------------------------------------------------------------------------- #
CHANGE_BASES = {
    "daily": ("Day on day", "Each day against the one before."),
    "weekly": ("Week on week", "Each week's average against the previous week's."),
    "monthly": ("Month on month",
                "Each month's average against the previous month's - the "
                "workbook's Monthly average change sheet."),
    "rolling": ("Rolling 7 days",
                "Each day against the same day a week earlier - the workbook's "
                "Weekly changes, daily rolling."),
    "weekly_avg": ("Weekly average of the rolling change",
                   "The mean of the rolling seven-day change across each week."),
}


@bp.route("/changes")
@login_required
def changes():
    basis = request.args.get("basis", "weekly")
    if basis not in CHANGE_BASES:
        basis = "weekly"
    limit = min(max(request.args.get("limit", type=int) or 26, 5), 200)

    if basis == "rolling":
        rows, grain = queries.rolling_change(7, limit=limit), "daily"
    elif basis == "weekly_avg":
        rows, grain = queries.weekly_average_change(limit=limit), "weekly"
    else:
        rows, grain = queries.changes(basis, limit=limit), basis

    title, caption = CHANGE_BASES[basis]
    return render_template(
        "weigh_in/changes.html",
        rows=list(reversed(rows)), grain=grain, basis=basis, limit=limit,
        title=title, caption=caption, bases=CHANGE_BASES,
    )


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@bp.route("/data")
@login_required
def data():
    grain = request.args.get("grain", "daily")
    if grain not in queries.GRAINS:
        grain = "daily"
    page = max(request.args.get("page", type=int) or 1, 1)
    per_page = 40
    # Only meaningful at daily grain - see queries.recent_periods.
    include = request.args.get("estimated", "1") == "1"
    rows = queries.recent_periods(grain, limit=per_page,
                                  offset=(page - 1) * per_page,
                                  include_estimated=include)
    return render_template(
        "weigh_in/data.html",
        rows=rows, grain=grain, grains=queries.GRAINS,
        page=page, per_page=per_page, include_estimated=include,
        coverage=queries.coverage(), has_next=len(rows) == per_page,
    )


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #
@bp.route("/admin")
@login_required
def admin():
    exports = []
    if config.EXPORT_DIR.exists():
        exports = sorted(config.EXPORT_DIR.glob("*.xlsx"),
                         key=lambda p: p.stat().st_mtime, reverse=True)[:10]
    return render_template(
        "weigh_in/admin.html",
        coverage=queries.coverage(),
        gaps=queries.gaps()[:20],
        trail=queries.audit_trail(80),
        exports=[p.name for p in exports],
        db_path=config.DB_PATH,
        workbook=config.SOURCE_XLSX,
        export_dir=config.EXPORT_DIR,
        max_backfill=config.MAX_BACKFILL_DAYS,
    )


@bp.route("/admin/export", methods=["POST"])
@login_required
def export_now():
    from core import excel_export  # lazy: pulls in openpyxl

    path = excel_export.export()
    flash(f"Exported to {path.name}.", "ok")
    return redirect(url_for("weigh_in.admin"))


@bp.route("/admin/export/<name>")
@login_required
def download_export(name: str):
    # Resolve inside the export directory so a crafted name cannot escape it.
    target = (config.EXPORT_DIR / name).resolve()
    if target.parent != config.EXPORT_DIR.resolve() or not target.is_file():
        abort(404)
    return send_file(target, as_attachment=True)


@bp.route("/admin/backfill", methods=["POST"])
@login_required
def rebuild_estimates():
    result = mutations.backfill_all(rebuild=True)
    flash(f"Rebuilt every estimate: {result['filled']} day(s) interpolated "
          f"between {result['anchors']} real readings.", "ok")
    for line in result["skipped"]:
        flash(line, "warn")
    return redirect(url_for("weigh_in.admin"))
