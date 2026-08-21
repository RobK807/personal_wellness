"""Personal wellness dashboard - Streamlit entry point.

Run locally:      streamlit run app.py
On the NAS:       don't - see deploy/DEPLOY.md. Streamlit needs more memory
                  than the DS218play has, which is why web/ exists.

The sidebar has two levels, the same two the Flask front-end has: a section -
weigh-in, runs, workouts, diet - and the pages inside it. st.navigation draws
that from a mapping of heading to pages, so the grouping below is the whole of
the implementation.
"""
from __future__ import annotations

import hmac

import streamlit as st

import config
from core import db, metrics, queries, runs
# views/placeholders.py is deliberately not imported: all four sections are
# built now. It and web/templates/placeholder.html are kept as the shape a
# fifth section starts in - see the note at the top of that module.
from views.diet import (admin_page as diet_admin,
                        analysis_page as diet_analysis,
                        calculator_page as diet_calculator,
                        day_page as diet_day, foods_page as diet_foods,
                        targets_page as diet_targets, week_page as diet_week)
from views.runs import (admin_page as runs_admin, analysis as runs_analysis,
                        data_page as runs_data, input_page as runs_input,
                        overview as runs_overview, records as runs_records)
from views.weigh_in import (admin_page, changes_page, charts_page, data_page,
                            input_page, overview)
from views.workouts import (build_page as workouts_build,
                            session_page as workouts_session,
                            exercises_page as workouts_exercises,
                            plan_page as workouts_plan,
                            tracker_page as workouts_tracker)

st.set_page_config(
    page_title=config.APP_TITLE,
    page_icon=config.APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

# The page served at "/". It must not also be given a url_path.
DEFAULT_SLUG = "weigh-in"

# section key -> [(title, icon, url slug, render function), ...]
#
# Every slug carries its section as a prefix, for two reasons. Streamlit
# derives a page's identity from its function and every one of these is called
# `render`, so two pages named "data" in different sections would collide; and
# the slug has to be flat - st.Page rejects a nested path outright, which is
# why these read `runs-analysis` rather than the `runs/analysis` the Flask
# front-end serves. The two front-ends therefore have different URLs for the
# same page, which is a shame but is not worth a redirect layer to fix.
PAGES = {
    "weigh_in": [
        ("Overview", ":material/dashboard:",      "weigh-in",         overview.render),
        ("Input",    ":material/add_circle:",     "weigh-in-input",   input_page.render),
        ("Charts",   ":material/show_chart:",     "weigh-in-charts",  charts_page.render),
        ("Changes",  ":material/trending_up:",    "weigh-in-changes", changes_page.render),
        ("Data",     ":material/table_rows:",     "weigh-in-data",    data_page.render),
        ("Admin",    ":material/settings:",       "weigh-in-admin",   admin_page.render),
    ],
    "runs": [
        ("Overview",  ":material/dashboard:",     "runs",             runs_overview.render),
        ("Log a run", ":material/add_circle:",    "runs-log",         runs_input.render),
        ("Analysis",  ":material/insights:",      "runs-analysis",    runs_analysis.render),
        ("Records",   ":material/trophy:",        "runs-records",     runs_records.render),
        ("Data",      ":material/table_rows:",    "runs-data",        runs_data.render),
        ("Admin",     ":material/settings:",      "runs-admin",       runs_admin.render),
    ],
    "workouts": [
        # Session first, so the sidebar lands on the workout that is due rather
        # than on the machinery for planning one.
        ("Session",   ":material/fitness_center:", "workouts",           workouts_session.render),
        ("Plan",      ":material/calendar_month:", "workouts-plan",      workouts_plan.render),
        ("Build",     ":material/construction:",   "workouts-build",     workouts_build.render),
        ("Tracker",   ":material/check_circle:",   "workouts-track",     workouts_tracker.render),
        ("Exercises", ":material/list:",           "workouts-exercises", workouts_exercises.render),
    ],
    "diet": [
        # Day first, for the same reason Session leads the workouts: it is the
        # page opened to answer a question, and the rest is setup.
        ("Day",        ":material/restaurant:",      "diet",            diet_day.render),
        ("Week",       ":material/calendar_month:",  "diet-week",       diet_week.render),
        ("Calculator", ":material/calculate:",       "diet-calculator", diet_calculator.render),
        ("Catalogue",  ":material/list:",            "diet-foods",      diet_foods.render),
        ("Targets",    ":material/flag:",            "diet-targets",    diet_targets.render),
        ("Analysis",   ":material/insights:",        "diet-analysis",   diet_analysis.render),
        ("Admin",      ":material/settings:",        "diet-admin",      diet_admin.render),
    ],
}


def check_password() -> bool:
    """Shared-password gate.

    This is a speed bump, not real security - it stops a stray visitor editing
    the data. Put the app behind Tailscale for the actual protection; see
    deploy/DEPLOY.md.
    """
    if not config.APP_PASSWORD:
        return True
    if st.session_state.get("authenticated"):
        return True

    st.title(f"{config.APP_ICON} {config.APP_TITLE}")
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            if hmac.compare_digest(entered, config.APP_PASSWORD):
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password")
    return False


def build_navigation() -> dict:
    """Section heading -> list of st.Page, in the order config.SECTIONS gives.

    Every view function is called `render`, so url_path has to be explicit or
    Streamlit would derive the same slug for all of them. The default page is
    the exception: it owns "/", and giving it a slug as well makes that slug
    404 with a "Page not found" banner.
    """
    grouped = {}
    for key, label, icon, _ in config.SECTIONS:
        pages = []
        for title, page_icon, slug, function in PAGES.get(key, []):
            if slug == DEFAULT_SLUG:
                pages.append(st.Page(function, title=title, icon=page_icon,
                                     default=True))
            else:
                pages.append(st.Page(function, title=title, icon=page_icon,
                                     url_path=slug))
        if pages:
            grouped[f"{icon} {label}"] = pages
    return grouped


def sidebar_summary() -> None:
    """One line per section that has anything to say."""
    st.title(f"{config.APP_ICON} {config.APP_TITLE}")

    latest = queries.latest("daily")
    if latest:
        st.caption(
            f"⚖️ {metrics.fmt('weight', latest['weight'], True)} · "
            f"{metrics.period_label('daily', latest['period'])}")

    from core import run_queries

    last_run = run_queries.latest()
    if last_run:
        st.caption(
            f"🏃 {runs.fmt_distance(last_run['distance_km'])} km · "
            f"{runs.fmt_pace(last_run['pace_s'], True)} · "
            f"{metrics.period_label('daily', last_run['day'])}")


def main() -> None:
    if not check_password():
        return

    db.init_db()

    with st.sidebar:
        sidebar_summary()

    st.navigation(build_navigation()).run()


if __name__ == "__main__":
    main()
