"""Workout plan and tracker pages for the Streamlit front-end.

The same four pages the Flask blueprint has - plan, build, tracker, exercises -
drawn with Streamlit widgets instead of Jinja and a form post. Both sit on
core.workout_queries and core.workout_mutations, so the prescribed weights, the
validation and the error messages are the same on either front-end.

The one real difference is the builder. Flask renders ten exercise slots at once
because a form post is all-or-nothing; here the exercises are added one at a
time and held in session state until the session is saved, which is nicer to use
and impossible to do without a rerun. Both end up calling save_session() with
the same list of dicts.
"""
