"""Render every Streamlit page against a throwaway copy of the database.

    python streamlit_test.py

The Streamlit front-end is the one that is not deployed yet, which is exactly
why it needs a test: nothing else would notice it had rotted. Streamlit's own
AppTest harness runs each page's `render()` in a real script context, so an API
that has moved on, a column that no longer exists or a chart built from an
empty frame all surface as an exception here rather than in six months' time.

Every page is rendered twice: once against the real data, and once against an
empty database. The second pass is the one that catches the interesting
failures - an Altair chart built from no rows, a `min()` over an empty list, a
metric formatted from None - and it is the state the app is genuinely in on the
day a new section is added.

It does not check what the pages look like - only that they run, and that the
figures they show come from the same `core` functions the Flask side uses.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REAL_DB = Path(__file__).parent / "data" / "wellness.db"
TEMP_DB = Path(tempfile.gettempdir()) / "wellness_streamlit_test.db"
EMPTY_DB = Path(tempfile.gettempdir()) / "wellness_streamlit_empty.db"

# module path -> what to call it in the output. Both sections' six pages, plus
# one placeholder from each of the two that are not built.
PAGES = [
    ("views.weigh_in.overview",     "render"),
    ("views.weigh_in.input_page",   "render"),
    ("views.weigh_in.charts_page",  "render"),
    ("views.weigh_in.changes_page", "render"),
    ("views.weigh_in.data_page",    "render"),
    ("views.weigh_in.admin_page",   "render"),
    ("views.runs.overview",         "render"),
    ("views.runs.input_page",       "render"),
    ("views.runs.analysis",         "render"),
    ("views.runs.records",          "render"),
    ("views.runs.data_page",        "render"),
    ("views.runs.admin_page",       "render"),
    ("views.placeholders",          "workout_plan"),
    ("views.placeholders",          "workout_tracker"),
    ("views.placeholders",          "diet_log"),
    ("views.placeholders",          "diet_analysis"),
]


def _clear(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def _render_all(db_path: Path, label: str, root: str) -> list:
    """Render every page against one database. Returns the failures."""
    from streamlit.testing.v1 import AppTest

    os.environ["PW_DB_PATH"] = str(db_path)
    # config caches DB_PATH at import time, so anything that already read it
    # has to be reloaded before the new path takes effect.
    for name in [n for n in list(sys.modules)
                 if n == "config" or n.startswith(("core", "views"))]:
        del sys.modules[name]

    from core import db
    db.init_db()

    print(f"\n{label} — {db_path}")
    failures = []
    for module, attribute in PAGES:
        name = f"{module.rsplit('.', 1)[-1]}.{attribute}"
        script = (f"import sys; sys.path.insert(0, {root!r})\n"
                  f"import {module} as page\n"
                  f"page.{attribute}()\n")
        test = AppTest.from_string(script, default_timeout=240)
        test.run()

        if test.exception:
            print(f"  [FAIL] {name}: {test.exception[0].message}")
            failures.append(f"{label}: {name}")
            continue

        drawn = (len(test.markdown) + len(test.dataframe) + len(test.metric)
                 + len(test.title) + len(test.caption) + len(test.code))
        print(f"  [ok ] {name}: {drawn} elements, "
              f"{len(test.warning)} warning(s)")
    return failures


def _check_navigation(root: str) -> list:
    """Run app.py itself, not just the pages it lists.

    Rendering each `render()` in isolation says nothing about whether
    st.navigation will accept the pages - `url_path` has rules, and a slug it
    rejects takes down every page at once while each of them passes on its own.
    This is that gap closed: the app is run the way Streamlit runs it.
    """
    from streamlit.testing.v1 import AppTest

    print("\nThe app itself")
    test = AppTest.from_file(str(Path(root) / "app.py"), default_timeout=240)
    test.run()
    if test.exception:
        print(f"  [FAIL] app.py: {test.exception[0].message}")
        return ["app.py: navigation"]

    print(f"  [ok ] app.py: navigation built, "
          f"{len(test.title) + len(test.caption)} elements on the landing page")
    return []


def main() -> int:
    _clear(TEMP_DB)
    _clear(EMPTY_DB)
    if REAL_DB.exists():
        shutil.copy(REAL_DB, TEMP_DB)

    root = str(Path(__file__).resolve().parent)
    failures = _render_all(TEMP_DB, "With data", root)
    failures += _check_navigation(root)
    failures += _render_all(EMPTY_DB, "Empty database", root)

    print()
    if failures:
        print(f"FAILED: {len(failures)} page(s)")
        for line in failures:
            print(f"  {line}")
        return 1
    print(f"Every page renders, with data and without "
          f"({len(PAGES) * 2} renders), and the navigation builds.")
    return 0


if __name__ == "__main__":
    code = main()
    _clear(TEMP_DB)
    _clear(EMPTY_DB)
    sys.exit(code)
