"""The values the Run type and Effort type dropdowns offer.

Read and written here, stored in the `run_options` table, seeded once by
core/db.py from config.py and from whatever the imported runs already use.

The lists are **closed**: core/runs.py `parse_choice` refuses anything that is
not on them, which is the point of the exercise. A free-text box eventually
produces "VO2 max", "VO2 Max" and "VO2max" as three effort types, each owning
a slice of the analysis and none of them telling the truth about the running.

One thing still adds to a list without going through the form, and it does so
by extending it rather than working around it: the importer. The spreadsheet is
where this vocabulary came from, so a word that appears there is a real option
and `register()` records it - see core/strava_import.py. That keeps the
importer's job unchanged (reproduce the sheet) without letting the sheet
introduce values the form would then refuse to edit.

Nothing here deletes a value that runs are using. `runs.run_type` is plain TEXT
rather than a foreign key, deliberately - the runs are the record of what
happened and this is a convenience for a form - so the rule that keeps the two
in step lives in `replace()` instead of in the schema.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Sequence

import config
from core import db

# The two lists, and what each is called on screen.
KINDS = ("run_type", "effort_type")
LABELS = {"run_type": "Run type", "effort_type": "Effort type"}
SEEDS = {"run_type": config.RUN_TYPES, "effort_type": config.EFFORT_TYPES}

# Long enough for "Warm-up / warm down" and short enough that a pasted
# paragraph is caught rather than stored.
MAX_LENGTH = 40


class InvalidOption(ValueError):
    """A dropdown list that cannot be saved as given."""


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise InvalidOption(f"'{kind}' is not one of {' or '.join(KINDS)}")
    return kind


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def values(kind: str) -> list:
    """One list, in the order the dropdown should offer it."""
    return [row["value"] for row in db.rows(
        "SELECT value FROM run_options WHERE kind = ? ORDER BY position, value",
        (_check_kind(kind),))]


def all_values() -> dict:
    """Both lists at once - what the input forms need to draw themselves."""
    return {kind: values(kind) for kind in KINDS}


def usage(kind: str) -> dict:
    """How many runs use each value, including values no longer on the list.

    Counted from `runs` rather than from `run_options`, because the whole point
    of the count is to answer "would removing this orphan anything?".
    """
    _check_kind(kind)
    return {row[kind]: row["runs"] for row in db.rows(
        f"SELECT {kind}, COUNT(*) AS runs FROM runs GROUP BY {kind}")}


def with_usage(kind: str) -> list:
    """The list, each value carrying the number of runs using it."""
    counts = usage(kind)
    return [{"value": value, "runs": counts.get(value, 0)}
            for value in values(kind)]


def orphans(kind: str) -> list:
    """Values in use by runs that the list does not offer.

    Should always be empty: the seed picks up everything already there, the
    importer registers what it meets, and `replace()` will not drop a value in
    use. It is surfaced on the Admin page anyway, because the way to find out
    that one of those three is wrong is to be told, not to notice a run that
    cannot be edited.
    """
    offered = {value.casefold() for value in values(kind)}
    return [{"value": value, "runs": count}
            for value, count in sorted(usage(kind).items())
            if value and value.casefold() not in offered]


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def clean(raw: Iterable) -> list:
    """Tidy a list as typed: strip, drop blanks, drop repeats, keep order.

    Case-insensitive on the repeats, because "Tempo" and "tempo" as two
    options is the problem this whole module exists to prevent.
    """
    cleaned, seen = [], set()
    for item in raw:
        text = " ".join(str(item or "").split())
        if not text or text.casefold() in seen:
            continue
        cleaned.append(text)
        seen.add(text.casefold())
    return cleaned


def replace(kind: str, raw: Iterable) -> dict:
    """Save one list wholesale. Returns what changed.

    Wholesale rather than add/remove/reorder one at a time because the Admin
    page edits it as a list, and one save that either takes or does not is
    easier to reason about than four routes that each half-apply.

    Refused if it would leave a run stranded - a value some run still uses, and
    that the new list does not offer. That covers the rename case as well as
    the delete: changing "VO2 max" to "VO2 Max" reads here as removing one and
    adding the other, and the thirty-two runs saying "VO2 max" would be left
    pointing at nothing.
    """
    _check_kind(kind)
    wanted = clean(raw)
    if not wanted:
        raise InvalidOption(
            f"{LABELS[kind]} needs at least one option - the run form has to "
            f"have something to offer")
    too_long = [value for value in wanted if len(value) > MAX_LENGTH]
    if too_long:
        raise InvalidOption(
            f"'{too_long[0][:MAX_LENGTH]}…' is too long for a {LABELS[kind].lower()} "
            f"(limit {MAX_LENGTH} characters)")

    counts = usage(kind)
    offered = set(wanted)
    stranded = [(value, count) for value, count in sorted(counts.items())
                if value and count and value not in offered]
    if stranded:
        value, count = stranded[0]
        raise InvalidOption(
            f"'{value}' is used by {count} run{'' if count == 1 else 's'}, so "
            f"it cannot be taken off the list. Change those runs to something "
            f"else first, or put it back exactly as spelled"
            + (f" (the list has {len(stranded)} like this)"
               if len(stranded) > 1 else ""))

    before = values(kind)
    with db.transaction() as conn:
        conn.execute("DELETE FROM run_options WHERE kind = ?", (kind,))
        conn.executemany(
            "INSERT INTO run_options (kind, value, position) VALUES (?, ?, ?)",
            [(kind, value, index) for index, value in enumerate(wanted)])
        if before != wanted:
            db.log(conn, "set_options", "run_options", kind,
                   f"{LABELS[kind]}: {', '.join(wanted)}")

    return {"kind": kind, "values": wanted,
            "added": [v for v in wanted if v not in before],
            "removed": [v for v in before if v not in offered],
            "reordered": before != wanted
            and sorted(before) == sorted(wanted)}


def register(conn, kind: str, incoming: Sequence[str]) -> list:
    """Add any values the list does not already offer, at the end of it.

    For the importer, which owns a connection and a transaction of its own.
    Returns what it added.
    """
    _check_kind(kind)
    existing = [row[0] for row in conn.execute(
        "SELECT value FROM run_options WHERE kind = ? ORDER BY position",
        (kind,))]
    seen = {value.casefold() for value in existing}
    added = []
    for value in clean(incoming):
        if value.casefold() not in seen:
            added.append(value)
            seen.add(value.casefold())
    if added:
        conn.executemany(
            "INSERT INTO run_options (kind, value, position) VALUES (?, ?, ?)",
            [(kind, value, len(existing) + index)
             for index, value in enumerate(added)])
    return added


def reset(kind: str) -> list:
    """Put one list back to the config seed, plus anything runs still use.

    The way out of a list edited into uselessness, and the same rule the first
    start follows.
    """
    _check_kind(kind)
    counts = usage(kind)
    in_use = sorted((value for value, count in counts.items() if value and count),
                    key=lambda value: (-counts[value], value))
    replace(kind, list(SEEDS[kind]) + in_use)
    return values(kind)


def parse_form(text) -> list:
    """One option per line, as the Admin page's text box hands it over."""
    return clean(str(text or "").splitlines())


def as_form(kind: str) -> str:
    """The list as the Admin page's text box wants it back."""
    return "\n".join(values(kind))
