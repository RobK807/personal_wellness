"""Log a run - the spreadsheet's fields, minus the two it derived.

Pace is not a field. It is distance over time, and the breakdown paces are each
split over its own distance; asking for all three would let them disagree. They
are shown back once the run is saved.
"""
from __future__ import annotations

import datetime as dt

import streamlit as st

import config
from core import metrics, run_mutations, run_queries, runs
from views import run_frames as frames


def render() -> None:
    st.title("Log a run")

    editing = _run_being_edited()
    if editing:
        st.caption(f"Editing the "
                   f"{runs.fmt_distance(editing['distance_km'])} km run on "
                   f"{metrics.period_label('daily', editing['day'])}. Saving "
                   f"replaces its best efforts with whatever is in the form.")
    else:
        st.caption("The fields the spreadsheet holds. Times are mm:ss or "
                   "h:mm:ss; leave a breakdown blank if the run did not reach "
                   "it.")

    _form(editing)
    st.divider()
    if not editing:
        _outstanding()
    _recent()


def _run_being_edited() -> dict | None:
    """The run the Edit button on the Data page sent us to, if any.

    Session state rather than a query parameter: Streamlit's navigation owns
    the URL, and a page that reads ?id= would lose it on the first rerun.
    """
    run_id = st.session_state.get("edit_run_id")
    if not run_id:
        return None
    row = frames.run(run_id)
    if row is None:
        st.session_state.pop("edit_run_id", None)
        return None
    if st.button("← Stop editing, log a new run instead"):
        st.session_state.pop("edit_run_id", None)
        st.rerun()
    return row


def _form(editing: dict | None) -> None:
    today = dt.date.today()
    default_day = runs.as_date(editing["day"]) if editing else today
    existing = ({row["breakdown"]: row["seconds"]
                 for row in run_queries.bests_for(editing["id"])}
                if editing else {})

    with st.form("log_run"):
        left, middle, right = st.columns(3)
        day = left.date_input("Date", value=default_day, max_value=today,
                              format="DD/MM/YYYY")
        distance = middle.text_input(
            "Distance (km)",
            value=f"{editing['distance_km']:g}" if editing else "",
            placeholder="7.83")
        duration = right.text_input(
            "Time",
            value=runs.fmt_duration(editing["duration_s"]) if editing else "",
            placeholder="41:54")

        left, middle, right = st.columns(3)
        run_type = left.selectbox(
            "Run type", _options(config.RUN_TYPES,
                                 editing["run_type"] if editing else None),
            index=_index(config.RUN_TYPES,
                         editing["run_type"] if editing else "Standard"))
        effort_type = middle.selectbox(
            "Effort type", _options(config.EFFORT_TYPES,
                                    editing["effort_type"] if editing else None),
            index=_index(config.EFFORT_TYPES,
                         editing["effort_type"] if editing else "Base"))
        note = right.text_input(
            "Name or note", value=(editing["note"] or "") if editing else "",
            placeholder="8k run (base)")

        intervals = _interval_fields(editing)

        st.markdown("**Best efforts inside the run**")
        st.caption("Strava's ladder. Fill in the rungs the run was long enough "
                   "to reach. Each split must be no slower than the shorter "
                   "ones inside it, and no longer than the whole run.")

        breakdowns = {}
        columns = st.columns(4)
        for index, (label, km) in enumerate(config.BREAKDOWNS):
            breakdowns[label] = columns[index % 4].text_input(
                label, value=runs.fmt_duration(existing[label])
                if label in existing else "",
                placeholder="mm:ss", key=f"bd_{index}",
                help=f"{km:g} km")

        submitted = st.form_submit_button(
            "Save changes" if editing else "Save run", type="primary")

    if not submitted:
        return

    try:
        saved = run_mutations.save_run(
            {"day": day, "distance_km": distance, "duration_s": duration,
             "run_type": run_type, "effort_type": effort_type, "note": note,
             **intervals},
            breakdowns,
            run_id=editing["id"] if editing else None,
        )
    except runs.InvalidRun as exc:
        st.error(str(exc))
        return

    splits = len(saved["breakdowns"])
    st.success(f"Saved {runs.describe(saved)} on {saved['day']:%d/%m/%Y}"
               + (f", with {splits} split{'s' if splits != 1 else ''}."
                  if splits else "."))
    st.session_state.pop("edit_run_id", None)
    _show_records(saved["id"])


def _interval_fields(editing: dict | None) -> dict:
    """The four interval columns, drawn inside the form that is already open.

    Returned as strings for core.runs.parse_intervals to make sense of, the
    same way the Flask form hands them over - one parser, one set of rules, one
    set of error messages, whichever front-end you happen to be using.
    """
    st.markdown("**Interval session**")
    st.caption(
        "For a session run as reps; leave it blank for anything else. *Type* "
        "says which of the next two was the target: **8 x 1k @ 3:50** sets the "
        "kilometre and the 3:50 is what happened, **6 x 3min @ 783m** sets the "
        "three minutes and the distance is what happened. Both are kept either "
        "way, so the average split and the average pace both follow from them."
    )

    options = ["", *config.INTERVAL_TYPES]
    current = (editing or {}).get("interval_type") or ""
    left, middle, right, far = st.columns(4)
    kind = left.selectbox(
        "Type", options, index=options.index(current),
        format_func=lambda value: ("Not an interval session" if not value
                                   else config.INTERVAL_TYPE_LABELS[value]))
    count = middle.text_input(
        "How many",
        value=str((editing or {}).get("interval_count") or ""),
        placeholder="8")
    distance = right.text_input(
        "Distance per interval",
        value=(runs.fmt_interval_distance((editing or {})["interval_distance_m"])
               if editing and editing.get("interval_distance_m") else ""),
        placeholder="1k, 400m or 1.6km")
    split = far.text_input(
        "Average split",
        value=(runs.fmt_duration((editing or {})["interval_split_s"])
               if editing and editing.get("interval_split_s") else ""),
        placeholder="3:50")

    if editing and editing.get("interval_pace_s"):
        st.caption(
            f"Currently {runs.fmt_duration(editing['interval_split_s'])} per "
            f"interval, which over "
            f"{runs.fmt_interval_distance(editing['interval_distance_m'])} is "
            f"**{runs.fmt_pace(editing['interval_pace_s'], True)}**.")

    return {"interval_type": kind, "interval_count": count,
            "interval_distance_m": distance, "interval_split_s": split}


def _outstanding() -> None:
    """The runs the spreadsheet called intervals that have no detail yet."""
    rows = run_queries.intervals_outstanding()
    if not rows:
        return
    st.subheader("Interval sessions still to fill in")
    st.caption("Flagged as intervals by the spreadsheet, with nothing recorded "
               "about them yet. This list empties itself as you go.")
    st.dataframe(
        [{"Date": metrics.period_label("daily", row["day"]),
          "Distance": f"{row['distance_km']:,.2f} km",
          "Time": runs.fmt_duration(row["duration_s"]),
          "Pace": runs.fmt_pace(row["pace_s"], True),
          "Effort": row["effort_type"],
          "Best 400m": _best_at(row["id"], "400m"),
          "Best 1K": _best_at(row["id"], "1K")}
         for row in rows],
        hide_index=True, width="stretch")
    st.caption("The two best-effort columns are a prompt: a session of 1k reps "
               "usually has a best 1K close to its average split. Pick one "
               "below to fill it in.")

    labels = {
        f"{metrics.period_label('daily', row['day'])} · "
        f"{runs.fmt_distance(row['distance_km'])} km · "
        f"{runs.fmt_duration(row['duration_s'])}": row["id"] for row in rows
    }
    left, right = st.columns([3, 1])
    chosen = left.selectbox("Add interval detail to", list(labels), index=None,
                            placeholder="Pick a session", key="outstanding_pick")
    if right.button("Open", disabled=chosen is None, key="outstanding_open"):
        st.session_state["edit_run_id"] = labels[chosen]
        st.rerun()


def _best_at(run_id: int, breakdown: str) -> str:
    for best in run_queries.bests_for(run_id):
        if best["breakdown"] == breakdown:
            return runs.fmt_duration(best["seconds"])
    return "—"


def _show_records(run_id: int) -> None:
    """Whether anything just entered belongs in the records tables."""
    ladder = run_queries.bests_for(run_id)
    placed = [(row["breakdown"], run_queries.is_record(run_id, row["breakdown"]))
              for row in ladder]
    placed = [(label, position) for label, position in placed if position]
    if placed:
        st.info("That run makes the records at: "
                + ", ".join(f"{label} ({runs.medal(position)})"
                            for label, position in placed))


def _options(known: list, current: str | None) -> list:
    """The known values, plus whatever this run already uses.

    Both columns are free text in the database - a new kind of session should
    not need a migration - so a run imported with a type nobody has offered
    since must still be editable without silently being reclassified.
    """
    if current and current not in known:
        return [*known, current]
    return known


def _index(known: list, current: str | None) -> int:
    options = _options(known, current)
    return options.index(current) if current in options else 0


def _recent() -> None:
    st.subheader("Recent runs")
    recent = frames.runs_list(limit=10)
    if recent.empty:
        st.caption("Nothing recorded yet.")
        return

    st.dataframe(frames.for_display(recent, drop=["note"]), hide_index=True,
                 width="stretch", column_config=frames.column_config())

    labels = {
        f"{metrics.period_label('daily', row['day'])} · "
        f"{runs.fmt_distance(row['distance_km'])} km · "
        f"{runs.fmt_duration(row['duration_s'])}": row["id"]
        for row in frames.recent(10)
    }
    left, right = st.columns([3, 1])
    chosen = left.selectbox("Correct one of them", list(labels),
                            index=None, placeholder="Pick a run to edit")
    if right.button("Edit", disabled=chosen is None):
        st.session_state["edit_run_id"] = labels[chosen]
        st.rerun()
