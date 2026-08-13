"""Run tracker pages for the Streamlit front-end.

The same six pages the Flask blueprint has, drawn with Altair and st.dataframe
instead of hand-rolled SVG and Jinja. Both read core.run_queries, so the
figures cannot differ - see views/run_frames.py for where pandas enters.
"""
