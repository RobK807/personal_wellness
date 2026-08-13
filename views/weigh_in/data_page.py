"""Data - the daily table, at whichever grain you want to look at it."""
from __future__ import annotations

import streamlit as st

from core import queries as core_queries
from views import frames as queries


def render() -> None:
    st.title("Data")

    coverage = queries.coverage()
    if not coverage["days"]:
        st.info("Nothing recorded yet.")
        return

    row = st.columns(4)
    row[0].metric("Days recorded", f"{coverage['days']:,}")
    row[1].metric("Real weigh-ins", f"{coverage['readings']:,}")
    row[2].metric("Interpolated days", f"{coverage['estimated_days']:,}")
    row[3].metric("Still missing", f"{coverage['missing_days']:,}")

    grain = st.segmented_control("Grain", list(core_queries.GRAINS),
                                 default="daily") or "daily"
    if grain == "daily":
        include = st.toggle("Include interpolated days", value=True)
        rows = queries.recent_days(limit=2000, include_estimated=include)
    else:
        rows = queries.series(grain).iloc[::-1]

    st.dataframe(queries.for_display(rows, grain), hide_index=True,
                 width="stretch", height=520,
                 column_config=queries.column_config(grain))
    st.caption("Newest first. Interpolated days are days that were missed and "
               "filled in on a straight line between the readings either side.")
