"""Inline SVG charts for the run tracker.

The same constraint as web/charts.py and the same answer: the NAS cannot afford
Altair's dependencies, so these hand-roll plain SVG strings that inherit the
page's theme through CSS variables. The Streamlit side draws the same shapes
with Altair in views/run_charts.py.

Three shapes, because runs ask three questions the weigh-ins do not:

    volume()      how far per month - bars, because it is a total, not a level
    pace_line()   how fast over time, with the y-axis inverted so that up is
                  faster; a pace chart drawn the usual way round reads exactly
                  backwards, and getting quicker should not point downhill
    bars()        one number per category - the analysis page's comparisons
"""
from __future__ import annotations

from html import escape

from core import runs
from core.metrics import as_date, period_label

WIDTH, HEIGHT = 680, 240
PAD_L, PAD_R, PAD_T, PAD_B = 54, 16, 16, 30

COLOUR = "var(--chart-1)"
COLOUR_ALT = "var(--chart-3)"


def _empty(message: str = "No runs yet.") -> str:
    return f'<p class="muted">{escape(message)}</p>'


def _svg(body: str, label: str, height: int = HEIGHT) -> str:
    return (
        f'<svg viewBox="0 0 {WIDTH} {height}" width="100%" '
        f'preserveAspectRatio="xMinYMin meet" role="img" '
        f'aria-label="{escape(label)}" class="chart">{body}</svg>'
    )


def _bounds(values: list, from_zero: bool = False) -> tuple:
    """A low/high pair with headroom, never zero-height."""
    low = 0.0 if from_zero else min(values)
    high = max(values)
    if low == high:
        return low, high + (abs(high) * 0.1 or 1.0)
    if from_zero:
        return 0.0, high * 1.08
    pad = (high - low) * 0.1
    return low - pad, high + pad


def _ticks(low: float, high: float, count: int = 4) -> list:
    step = (high - low) / (count - 1)
    return [low + step * index for index in range(count)]


# --------------------------------------------------------------------------- #
# Distance per period
# --------------------------------------------------------------------------- #
def volume(rows: list, grain: str = "monthly", height: int = HEIGHT) -> str:
    """Kilometres per period as bars.

    Bars rather than a line, and from zero rather than from the lowest month,
    because this is an amount rather than a level: a month with 40 km in it and
    a month with 45 km should look nearly the same, and on a cropped axis they
    would not.
    """
    points = [(as_date(row["period"]), float(row["distance_km"] or 0))
              for row in rows if row.get("distance_km") is not None]
    if not points:
        return _empty()

    plot_w = WIDTH - PAD_L - PAD_R
    plot_h = height - PAD_T - PAD_B
    low, high = _bounds([value for _, value in points], from_zero=True)
    # Bars share the width evenly; the periods are regular, so position in the
    # list is the honest x here - unlike the weigh-in charts, where a missing
    # day has to leave a hole.
    slot = plot_w / len(points)
    width = max(slot * 0.72, 1.0)

    body = [_grid(low, high, plot_h, lambda v: f"{v:,.0f}")]
    for index, (when, value) in enumerate(points):
        x = PAD_L + slot * index + (slot - width) / 2
        y = PAD_T + (1 - (value - low) / (high - low)) * plot_h
        body.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" '
            f'height="{PAD_T + plot_h - y:.1f}" rx="1.5" fill="{COLOUR}">'
            f'<title>{escape(period_label(grain, when))}: '
            f'{value:,.1f} km</title></rect>'
        )
    body.append(_period_labels([when for when, _ in points], grain, slot, height))
    body.append(f'<line x1="{PAD_L}" y1="{PAD_T + plot_h}" x2="{WIDTH - PAD_R}" '
                f'y2="{PAD_T + plot_h}" class="chart-axis"/>')
    return _svg("".join(body), "Distance run per period", height)


# --------------------------------------------------------------------------- #
# Pace over time
# --------------------------------------------------------------------------- #
def pace_line(rows: list, grain: str = "monthly", height: int = HEIGHT) -> str:
    """Average pace per period, with the axis inverted so faster is higher."""
    points = [(as_date(row["period"]), float(row["pace_s"]))
              for row in rows if row.get("pace_s") is not None]
    if len(points) < 1:
        return _empty()

    plot_w = WIDTH - PAD_L - PAD_R
    plot_h = height - PAD_T - PAD_B
    low, high = _bounds([value for _, value in points])
    slot = plot_w / max(len(points), 1)

    def y_of(value: float) -> float:
        # Inverted: a smaller pace - a faster one - sits nearer the top.
        return PAD_T + (value - low) / (high - low) * plot_h

    body = [_grid(low, high, plot_h, runs.fmt_pace, invert=True)]
    coords = [(PAD_L + slot * index + slot / 2, y_of(value))
              for index, (_, value) in enumerate(points)]
    if len(coords) > 1:
        body.append(
            '<polyline points="'
            + " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
            + f'" fill="none" stroke="{COLOUR}" stroke-width="1.8" '
              'stroke-linejoin="round"/>'
        )
    for (x, y), (when, value) in zip(coords, points):
        body.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{COLOUR}">'
            f'<title>{escape(period_label(grain, when))}: '
            f'{runs.fmt_pace(value, True)}</title></circle>'
        )
    body.append(_period_labels([when for when, _ in points], grain, slot, height))
    body.append(f'<line x1="{PAD_L}" y1="{PAD_T + plot_h}" x2="{WIDTH - PAD_R}" '
                f'y2="{PAD_T + plot_h}" class="chart-axis"/>')
    return _svg("".join(body), "Average pace per period, faster towards the top",
                height)


# --------------------------------------------------------------------------- #
# One number per category
# --------------------------------------------------------------------------- #
def bars(entries: list, unit: str = "", faster_is_better: bool = False,
         formatter=None) -> str:
    """Horizontal bars: [(label, value, hover text), ...].

    Used for the analysis page's comparisons, where the categories are words
    rather than dates. Horizontal because "Warm-up / warm down" does not fit
    under a vertical bar at any font size worth reading.
    """
    entries = [(label, value, hover) for label, value, hover in entries
               if value is not None]
    if not entries:
        return _empty("Nothing to compare.")

    formatter = formatter or (lambda value: f"{value:,.1f}")
    row_h, gap = 26, 6
    label_w = 132
    height = PAD_T + len(entries) * (row_h + gap) + 8
    plot_w = WIDTH - label_w - 70
    widest = max(value for _, value, _ in entries) or 1.0

    body = []
    for index, (label, value, hover) in enumerate(entries):
        y = PAD_T + index * (row_h + gap)
        width = max(value / widest * plot_w, 1.0)
        colour = COLOUR_ALT if faster_is_better else COLOUR
        body.append(
            f'<text x="{label_w - 8}" y="{y + row_h * 0.68:.1f}" '
            f'text-anchor="end" class="chart-label">{escape(str(label))}</text>'
            f'<rect x="{label_w}" y="{y:.1f}" width="{width:.1f}" '
            f'height="{row_h}" rx="3" fill="{colour}" opacity=".85">'
            f'<title>{escape(hover)}</title></rect>'
            f'<text x="{label_w + width + 7:.1f}" y="{y + row_h * 0.68:.1f}" '
            f'class="chart-tick" fill="var(--muted)">'
            f'{escape(formatter(value))}{escape(unit)}</text>'
        )
    return _svg("".join(body), "Comparison by category", height)


# --------------------------------------------------------------------------- #
# Shared furniture
# --------------------------------------------------------------------------- #
def _grid(low: float, high: float, plot_h: float, formatter,
          invert: bool = False) -> str:
    """Gridlines and left-hand tick labels.

    De-duplicated the same way web/charts.py does it: four evenly spaced ticks
    across a narrow range round to the same text more often than not, and a
    repeated label makes an axis look broken while telling you nothing.
    """
    parts, seen = [], set()
    for value in _ticks(low, high):
        text = formatter(value)
        if text in seen:
            continue
        seen.add(text)
        fraction = (value - low) / (high - low)
        y = PAD_T + (fraction if invert else 1 - fraction) * plot_h
        parts.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{WIDTH - PAD_R}" y2="{y:.1f}" '
            f'class="chart-grid"/>'
            f'<text x="{PAD_L - 7}" y="{y + 3.5:.1f}" text-anchor="end" '
            f'class="chart-tick" fill="var(--muted)">{escape(text)}</text>'
        )
    return "".join(parts)


def _period_labels(dates: list, grain: str, slot: float, height: int) -> str:
    """Four or five period labels along the bottom, evenly spaced."""
    if not dates:
        return ""
    count = min(4, len(dates) - 1) or 1
    parts = []
    for index in range(count + 1):
        position = round((len(dates) - 1) * index / count)
        when = dates[position]
        x = PAD_L + slot * position + slot / 2
        anchor = "start" if index == 0 else ("end" if index == count else "middle")
        parts.append(
            f'<text x="{x:.1f}" y="{height - 10}" text-anchor="{anchor}" '
            f'class="chart-label">'
            f'{escape(period_label(grain, when, short=True))}</text>'
        )
    return "".join(parts)


def sparkbars(rows: list, width: int = 120, height: int = 28) -> str:
    """A tiny volume trend for a tile - no axes, no labels."""
    values = [float(row.get("distance_km") or 0) for row in rows]
    if len(values) < 2:
        return ""
    high = max(values) or 1.0
    slot = width / len(values)
    bar = max(slot * 0.7, 1.0)
    rects = "".join(
        f'<rect x="{slot * index + (slot - bar) / 2:.1f}" '
        f'y="{height - 1 - value / high * (height - 3):.1f}" '
        f'width="{bar:.1f}" height="{max(value / high * (height - 3), 0.8):.1f}" '
        f'fill="currentColor"/>'
        for index, value in enumerate(values)
    )
    return (f'<svg viewBox="0 0 {width} {height}" width="{width}" '
            f'height="{height}" class="sparkline" aria-hidden="true">'
            f'{rects}</svg>')


# Kept next to the charts because it is the same question - what does a period
# look like - answered in words rather than pixels.
def describe_period(row: dict, grain: str) -> str:
    when = as_date(row["period"])
    return (f"{period_label(grain, when)}: {row['runs']} run"
            f"{'s' if row['runs'] != 1 else ''}, "
            f"{runs.fmt_distance(row['distance_km'], 1)} km, "
            f"{runs.fmt_pace(row['pace_s'], True)}")
