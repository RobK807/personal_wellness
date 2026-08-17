"""Admin - what was imported, and what the source sheet got wrong."""
from __future__ import annotations

import streamlit as st

import config
from core import metrics, run_options, runs
from views import run_frames as frames


def _option_lists() -> None:
    """The two dropdown lists, edited one option per line.

    A text box rather than a row of add/remove/move buttons, because that is
    one control that does all four things and one save that either takes or
    does not. The rule that stops it going wrong is in core/run_options.py: a
    value some run still uses cannot be dropped, and a rename reads as a drop.
    """
    st.subheader("Run type and effort type")
    st.caption(
        "What the two dropdowns on the Log page offer, one per line, in the "
        "order they offer it. Both lists are closed — the form will not accept "
        "anything that is not on them, which is what stops *VO2 max*, *VO2 "
        "Max* and *VO2max* becoming three effort types that each own a slice "
        "of the analysis."
    )

    for column, kind in zip(st.columns(len(run_options.KINDS)),
                            run_options.KINDS):
        label = run_options.LABELS[kind]
        with column:
            with st.form(f"options_{kind}"):
                typed = st.text_area(label, value=run_options.as_form(kind),
                                     height=230, key=f"options_text_{kind}")
                left, right = st.columns(2)
                saved = left.form_submit_button("Save", type="primary")
                reset = right.form_submit_button(
                    "Reset", help="Back to the built-in list, plus anything "
                                  "runs still use")
            if saved or reset:
                try:
                    if reset:
                        run_options.reset(kind)
                        st.success(f"{label} list put back to the built-in "
                                   f"one, plus anything runs still use.")
                    else:
                        run_options.replace(kind,
                                            run_options.parse_form(typed))
                        st.success(f"{label} list saved.")
                    # The text box holds the old value until its key is
                    # cleared, and a list that reads differently from the one
                    # that was saved is worse than a rerun.
                    st.session_state.pop(f"options_text_{kind}", None)
                    st.rerun()
                except run_options.InvalidOption as exc:
                    st.error(str(exc))

            counts = {row["value"]: row["runs"]
                      for row in run_options.with_usage(kind)}
            stranded = run_options.orphans(kind)
            st.dataframe(
                [{label: value, "Runs": count or 0}
                 for value, count in counts.items()]
                + [{label: row["value"], "Runs": row["runs"]}
                   for row in stranded],
                hide_index=True, width="stretch")
            if stranded:
                st.warning(
                    f"{', '.join(row['value'] for row in stranded)} "
                    f"{'is' if len(stranded) == 1 else 'are'} used by runs but "
                    f"not offered by the list, so those runs cannot be saved "
                    f"from the form until the spelling is added back.")
            else:
                st.caption("A value with runs against it cannot be removed — "
                           "change those runs first. Everything here is safe "
                           "to reorder.")


def render() -> None:
    st.title("Admin")
    st.caption("What was imported, what the source sheet got wrong, and where "
               "everything lives.")

    coverage = frames.coverage()
    if not coverage["runs"]:
        st.warning("No runs recorded yet.")
        st.code("python -m core.strava_import --rebuild", language="bash")
        return

    columns = st.columns(4)
    columns[0].metric("Runs", f"{coverage['runs']:,}")
    columns[1].metric("Distance", f"{coverage['distance_km']:,.0f} km")
    columns[2].metric("Best efforts", f"{coverage['splits']:,}")
    columns[3].metric("Runs without any", coverage["without_splits"])
    st.caption(
        f"{metrics.period_label('daily', coverage['first_day'])} to "
        f"{metrics.period_label('daily', coverage['last_day'])} — "
        f"{coverage['span_days']:,} days, "
        f"{runs.fmt_duration(coverage['duration_s'], force_hours=True)} of "
        f"running."
    )

    st.divider()

    _option_lists()

    st.divider()

    st.subheader("Splits that cannot be true")
    anomalies = frames.anomalies()
    if anomalies.empty:
        st.success("None — every split is consistent with the run it came "
                   "from. Eighteen were not, until the runs' elapsed times "
                   "were corrected at source in August 2026.")
    else:
        st.caption(
            f"{len(anomalies)} of the imported splits contradict the run they "
            f"came from. They are kept — the run happened, and the figure is "
            f"the figure the scrape produced — but they are left out of the "
            f"records and the per-distance averages, because a table of bests "
            f"should only hold times that could have happened. Nothing entered "
            f"through the form can add to this list."
        )
        st.dataframe(
            [{"Date": metrics.period_label("daily", row["day"]),
              "Split": row["breakdown"],
              "Split time": runs.fmt_duration(row["seconds"]),
              "The run": f"{runs.fmt_distance(row['distance_km'])} km in "
                         f"{runs.fmt_duration(row['duration_s'])}",
              "Why it cannot be": row["reason"]}
             for row in anomalies.to_dict("records")],
            hide_index=True, width="stretch")
        st.caption(
            "Fixing these means fixing the sheet rather than the dashboard — "
            "correct the run's distance or elapsed time, re-import, and they "
            "disappear."
        )

    st.divider()

    st.subheader("The breakdown ladder")
    reached = {row["breakdown"]: row
               for row in frames.by_breakdown().to_dict("records")}
    # Every cell in a column has to be the same type, or Arrow refuses to
    # serialise the frame - which is why "Set aside" is a string throughout
    # rather than a count that becomes "" when it is zero.
    st.dataframe(
        [{"Distance": label,
          "km": round(km, 3),
          "Runs reaching it": reached[label]["runs"] if label in reached else 0,
          "Best": (runs.fmt_duration(reached[label]["best_seconds"])
                   if label in reached else "—"),
          "Set aside": str(reached[label]["set_aside"] or "")
                       if label in reached else ""}
         for label, km in config.BREAKDOWNS],
        hide_index=True, width="stretch")
    st.caption("The rungs are Strava's, and the whole vocabulary of the "
               "*Breakdown* column. A run only carries the ones it was long "
               "enough to reach.")

    st.divider()

    st.subheader("Re-importing")
    st.caption("Runs are matched on date, distance and elapsed time, so "
               "re-running the importer updates what has changed and adds what "
               "is new without duplicating anything or touching runs entered "
               "by hand.")
    st.code("python -m core.strava_import            # update from the sheet\n"
            "python -m core.strava_import --rebuild  # discard imported runs "
            "and start again", language="bash")
    st.caption("Run it on a desktop: openpyxl opening the workbook needs more "
               "memory than the NAS has free.")

    st.subheader("Where things are")
    st.dataframe([{"What": "Database", "Where": str(config.DB_PATH)},
                  {"What": "Workbook", "Where": str(config.RUNS_XLSX)},
                  {"What": "Tab", "Where": config.RUNS_SHEET}],
                 hide_index=True, width="stretch")

    st.subheader("Recent changes")
    trail = frames.audit_trail(60)
    if trail.empty:
        st.caption("Nothing yet.")
    else:
        st.dataframe(trail[["ts", "action", "detail"]]
                     .rename(columns={"ts": "When", "action": "Action",
                                      "detail": "Detail"}),
                     hide_index=True, width="stretch", height=320)
