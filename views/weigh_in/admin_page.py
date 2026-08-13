"""Admin - snapshots, gaps, rebuilding the estimates, and the audit trail."""
from __future__ import annotations

import streamlit as st

import config
from core import metrics, mutations
from views import frames as queries


def render() -> None:
    st.title("Admin")

    coverage = queries.coverage()
    st.subheader("Where things are")
    st.dataframe(
        [
            {"": "Database", "Path": str(config.DB_PATH)},
            {"": "Workbook", "Path": str(config.SOURCE_XLSX)},
            {"": "Exports", "Path": str(config.EXPORT_DIR)},
            {"": "Back-fill limit", "Path": f"{config.MAX_BACKFILL_DAYS} days"},
        ],
        hide_index=True, width="stretch",
    )
    if coverage["days"]:
        st.caption(
            f"{metrics.period_label('daily', coverage['first_day'])} to "
            f"{metrics.period_label('daily', coverage['last_day'])} — "
            f"{coverage['days']:,} days, {coverage['readings']:,} real "
            f"weigh-ins, {coverage['estimated_days']:,} interpolated."
        )

    st.divider()

    st.subheader("Snapshot")
    st.caption("Writes an .xlsx with the same tabs the original workbook had, "
               "so the data is never locked inside this app.")
    if st.button("Export to Excel", type="primary"):
        from core import excel_export  # lazy: pulls in openpyxl
        with st.spinner("Writing the workbook..."):
            path = excel_export.export()
        st.success(f"Exported to {path}")

    st.divider()

    st.subheader("Gaps")
    gaps = queries.gaps()
    if gaps.empty:
        st.success("No gaps — every day between the first and last reading has "
                   "a value.")
    else:
        st.caption(
            "Stretches with no data at all. A gap is normally filled the moment "
            f"the next reading is entered, so anything here is either longer "
            f"than the {config.MAX_BACKFILL_DAYS}-day limit, or waiting for a "
            "reading on the far side."
        )
        st.dataframe(gaps, hide_index=True, width="stretch")

    st.divider()

    st.subheader("Rebuild the estimates")
    st.caption("Throws away every interpolated day and recomputes it from the "
               "real readings either side. Worth doing after correcting a run "
               "of entries; it never touches a real weigh-in.")
    if st.button("Rebuild"):
        with st.spinner("Recomputing..."):
            result = mutations.backfill_all(rebuild=True)
        st.success(f"{result['filled']} day(s) interpolated between "
                   f"{result['anchors']} real readings.")
        for line in result["skipped"]:
            st.warning(line)

    st.divider()

    st.subheader("Recent activity")
    st.dataframe(queries.audit_trail(100), hide_index=True, width="stretch")
