"""Input - the two weigh-ins for a day, and the back-fill that follows."""
from __future__ import annotations

import datetime as dt

import streamlit as st

import config
from core import metrics, mutations
from views import frames as queries


def render() -> None:
    st.title("Input")
    st.caption("Two weigh-ins a day, averaged. Leave the second blank if you "
               "only took one — the average simply uses what is there.")

    when = st.date_input("Date", value=dt.date.today(),
                         max_value=dt.date.today(), format="DD/MM/YYYY")

    existing = {row["slot"]: row for row in queries.readings_for(when)}
    previous = queries.day(when - dt.timedelta(days=1))
    if any(row["estimated"] for row in existing.values()):
        st.info("This day is currently interpolated. Entering a real weigh-in "
                "replaces it and redraws the estimates either side.")

    with st.form("weigh_in"):
        entered = {}
        for slot, column in zip(config.SLOTS, st.columns(len(config.SLOTS))):
            saved = existing.get(slot)
            with column:
                st.markdown(f"**Weigh-in {slot}**")
                values = {}
                for key, label, unit, dp, _ in config.METRICS:
                    values[key] = st.number_input(
                        f"{label}{f' ({unit})' if unit else ''}",
                        value=float(saved[key]) if saved else None,
                        step=1.0 if dp == 0 else 0.1,
                        format=f"%.{dp}f",
                        placeholder=(metrics.fmt(key, previous[key])
                                     if previous else "—"),
                        key=f"s{slot}_{key}",
                    )
                entered[slot] = values

        note = st.text_input("Note (optional)", max_chars=200,
                             placeholder="Ill, travelling, after a big meal…")
        submitted = st.form_submit_button(
            f"Save {when:%d/%m/%Y}", type="primary", width="stretch")

    if submitted:
        readings = {slot: values for slot, values in entered.items()
                    if any(v is not None for v in values.values())}
        if not readings:
            st.warning("Nothing entered.")
        else:
            try:
                result = mutations.save_day(when, readings, note=note)
            except metrics.InvalidReading as exc:
                st.error(str(exc))
            else:
                saved = ", ".join(f"weigh-in {s}" for s in result["slots"])
                st.success(f"Saved {saved} for {when:%d/%m/%Y}.")
                if result["filled"]:
                    st.success(f"Filled in {result['filled']} missed day"
                               f"{'s' if result['filled'] != 1 else ''} "
                               "by interpolation.")
                for line in result["skipped_gap"]:
                    st.warning(line)
                st.rerun()

    day_row = queries.day(when)
    if day_row:
        st.subheader(f"{when:%d/%m/%Y} as it stands")
        rows = []
        for slot in config.SLOTS:
            saved = existing.get(slot)
            if saved:
                rows.append({"": f"Weigh-in {slot}"
                                 + (" (interpolated)" if saved["estimated"] else ""),
                             **{config.LABELS[k]: saved[k]
                                for k in config.METRIC_KEYS}})
        rows.append({"": "Average",
                     **{config.LABELS[k]: day_row[k] for k in config.ALL_KEYS}})
        st.dataframe(rows, hide_index=True, width="stretch")

        left, right = st.columns(2)
        for column, slot in zip((left, right), config.SLOTS):
            if existing.get(slot):
                if column.button(f"Delete weigh-in {slot}", key=f"del{slot}"):
                    mutations.delete_reading(when, slot)
                    st.rerun()
        if st.button("Delete the whole day", type="secondary"):
            mutations.delete_day(when)
            st.rerun()
        st.caption("Deleting a day leaves a gap, which is re-estimated from the "
                   "readings either side the next time one is entered.")

    st.subheader("Recent days")
    recent = queries.recent_days(limit=14)
    st.dataframe(queries.for_display(recent), hide_index=True, width="stretch",
                 column_config=queries.column_config("daily"))
