"""One blueprint per section of the dashboard.

Each module exposes a `bp` whose name matches its key in config.SECTIONS -
that is what web/app.py mounts it by, and what web/nav.py works out the current
section from.
"""
