"""Food planner and diary pages for the Streamlit front-end.

The same six pages the Flask blueprint has - day, week, calculator, catalogue,
targets, analysis - drawn with Streamlit widgets instead of Jinja and a form
post. Both sit on core.food, core.food_queries and core.food_mutations, so the
portion arithmetic, the validation and the error messages are the same on either
front-end.

Two real differences. The day editor adds a line at a time and reruns, where
Flask renders every line plus four blanks and posts the lot, because a form post
is all-or-nothing and a rerun is not; both end up calling save_day() with the
same list of dicts. And the analysis page charts, because Altair is available on
this side and the NAS cannot afford it.
"""
