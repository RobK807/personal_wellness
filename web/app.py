"""Flask front-end - the version that runs on the NAS.

Deliberately lightweight. It imports sqlite3, Flask and Jinja2 and nothing else
heavy: the DS218play has roughly 149 MB of RAM free and the CD dashboard is
already using ~48 MB of it. The Streamlit front-end in app.py / views/ is the
richer version for a machine with memory to spare; both sit on the same `core`
package, so the schema, the averaging and the interpolation are shared rather
than duplicated.

This module is the shell: the app factory, the password gate, the template
helpers and the context every page needs to draw the sidebar. The pages
themselves live in one blueprint per section, under web/blueprints/, so a
tracker can be added without this file growing.
"""
from __future__ import annotations

import datetime as dt
import hmac
from functools import wraps

from flask import (Flask, flash, redirect, render_template, request, session,
                   url_for)

import config
from core import db, metrics, queries, runs
from web import nav


def login_required(view):
    """Gate a view behind the shared password, if one is set."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if config.APP_PASSWORD and not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = config.secret_key()
    # No uploads anywhere in the app; keep request bodies small.
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

    db.init_db()
    _register_filters(app)
    _register_context(app)
    _register_auth(app)
    _register_blueprints(app)

    @app.route("/healthz")
    def healthz():
        return {"ok": True,
                "weigh_in_days": db.scalar("SELECT COUNT(*) FROM v_daily", default=0),
                "runs": db.scalar("SELECT COUNT(*) FROM runs", default=0),
                "last_weigh_in": db.scalar("SELECT MAX(day) FROM v_daily"),
                "last_run": db.scalar("SELECT MAX(day) FROM runs")}

    @app.route("/")
    def home():
        """The front door. The weigh-in tracker has six years behind it."""
        return redirect(url_for(nav.LANDING[config.DEFAULT_SECTION]))

    return app


# --------------------------------------------------------------------------- #
# Template helpers
# --------------------------------------------------------------------------- #
def _register_filters(app: Flask) -> None:
    """Formatting rules, shared with core so a table and a chart agree.

    The weigh-in filters take a metric key because precision is per metric; the
    run ones do not, because a duration is a duration.
    """

    @app.template_filter("metric")
    def _metric(value, key: str = "weight") -> str:
        return metrics.fmt(key, value, with_unit=False)

    @app.template_filter("metric_unit")
    def _metric_unit(value, key: str = "weight") -> str:
        return metrics.fmt(key, value, with_unit=True)

    @app.template_filter("change")
    def _change(value, key: str = "weight") -> str:
        return metrics.fmt_change(key, value)

    @app.template_filter("direction")
    def _direction(value, key: str = "weight") -> str:
        return metrics.direction(key, value)

    @app.template_filter("period")
    def _period(value, grain: str = "daily") -> str:
        return metrics.period_label(grain, value)

    @app.template_filter("shortperiod")
    def _short_period(value, grain: str = "daily") -> str:
        return metrics.period_label(grain, value, short=True)

    @app.template_filter("duration")
    def _duration(value, force_hours: bool = False) -> str:
        return runs.fmt_duration(value, force_hours)

    @app.template_filter("pace")
    def _pace(value, with_unit: bool = False) -> str:
        return runs.fmt_pace(value, with_unit)

    @app.template_filter("km")
    def _km(value, dp: int = 2) -> str:
        return runs.fmt_distance(value, dp)

    @app.template_filter("ordinal")
    def _ordinal(value) -> str:
        return runs.medal(int(value))

    @app.template_filter("intervaldist")
    def _interval_distance(value) -> str:
        return runs.fmt_interval_distance(value)

    @app.template_filter("intervals")
    def _intervals(row) -> str:
        return runs.interval_summary(row)


def _register_context(app: Flask) -> None:
    """Everything base.html needs, on every page."""

    @app.context_processor
    def inject_nav() -> dict:
        section = nav.section_of(request.endpoint)
        return {
            "sections": nav.sidebar(),
            "section": section,
            "section_label": config.SECTION_LABELS.get(section),
            "section_icon": config.SECTION_ICONS.get(section),
            "pages": nav.PAGES.get(section, []),
            "current": request.endpoint,
            "app_title": config.APP_TITLE,
            "app_icon": config.APP_ICON,
            "summary_line": _summary_line(section),
            "config_password_set": bool(config.APP_PASSWORD),
            "metrics": config.METRICS,
            "all_metrics": config.ALL_METRICS,
            "labels": config.LABELS,
            "one_day": dt.timedelta(days=1),
        }


def _summary_line(section: str | None) -> str | None:
    """The one-line state of the section being looked at, under the title.

    Each section answers "where am I" in its own units, and a section that has
    nothing yet says nothing rather than borrowing another's numbers.
    """
    if section == "weigh_in":
        current = queries.latest("daily")
        if current:
            return (f"{metrics.fmt('weight', current['weight'], True)} · "
                    f"{metrics.period_label('daily', current['period'])}")
    elif section == "runs":
        # Imported here rather than at module level: this is the only thing on
        # the shared path that needs it, and the import is cheap either way.
        from core import run_queries

        last = run_queries.latest()
        if last:
            return (f"{runs.fmt_distance(last['distance_km'])} km · "
                    f"{runs.fmt_pace(last['pace_s'], True)} · "
                    f"{metrics.period_label('daily', last['day'])}")
    return None


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def _register_auth(app: Flask) -> None:
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not config.APP_PASSWORD:
            return redirect(url_for("home"))
        if request.method == "POST":
            if hmac.compare_digest(request.form.get("password", ""),
                                   config.APP_PASSWORD):
                session["authenticated"] = True
                session.permanent = True
                return redirect(request.args.get("next") or url_for("home"))
            flash("Incorrect password", "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.before_request
    def keep_session_alive() -> None:
        app.permanent_session_lifetime = dt.timedelta(days=30)


# --------------------------------------------------------------------------- #
# The sections
# --------------------------------------------------------------------------- #
def _register_blueprints(app: Flask) -> None:
    """One blueprint per section, mounted at the slug config gives it."""
    from web.blueprints import diet, runs as runs_bp, weigh_in, workouts

    for module in (weigh_in, runs_bp, workouts, diet):
        blueprint = module.bp
        app.register_blueprint(
            blueprint, url_prefix=f"/{config.SECTION_SLUGS[blueprint.name]}")
