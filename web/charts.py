"""Inline SVG line charts.

The Streamlit front-end uses Altair. That is not an option here - Altair pulls
in pandas, and the NAS cannot spare the memory - so these hand-roll the charts
as plain SVG strings, inheriting the page's theme through CSS variables.

Two shapes, matching the workbook's two kinds of chart:

  line()  one metric against time, as the Weekly Charts sheet had
  pair()  two metrics on one plot with an axis each, as the Charts sheet had -
          weight against BMI, body fat against skeletal muscle, and so on.
          A shared axis would be meaningless when one series is a percentage
          and the other is 1,700 kcal, which is why the workbook used two.

Points are placed by date rather than by position in the list, so a run of
missing days leaves a real gap rather than quietly compressing the line. Any
gap wider than one period breaks the line instead of drawing a straight segment
across data that does not exist.

Reading values off the chart
----------------------------
Each chart carries its points in a `data-chart` attribute, and
static/chart-hover.js turns that into a crosshair and a readout that follows
the pointer. The constraint this app is built around is the NAS's memory, which
is a server-side constraint: the readout runs in the browser and costs the NAS
nothing. What would not fit is a charting *library* - Altair, Plotly and the
rest all arrive with a Python stack attached.

The charts are complete without it. Turn JavaScript off and you still get the
lines, both axes, the tick labels and the legend; you lose only the readout.
"""
from __future__ import annotations

import datetime as dt
from html import escape

import config
from core import metrics

WIDTH, HEIGHT = 680, 260
PAD_L, PAD_R, PAD_T, PAD_B = 52, 52, 16, 30

# How many days one period covers, used to decide when a gap breaks the line.
STEP_DAYS = {"daily": 1, "weekly": 7, "monthly": 31}

COLOURS = ("var(--chart-1)", "var(--chart-3)")


def _empty(message: str = "No data yet.") -> str:
    return f'<p class="muted">{escape(message)}</p>'


def _nice_bounds(values: list) -> tuple:
    """A low/high pair with a little headroom, never zero-height."""
    low, high = min(values), max(values)
    if low == high:
        pad = abs(low) * 0.05 or 0.5
        return low - pad, high + pad
    pad = (high - low) * 0.08
    return low - pad, high + pad


def _ticks(low: float, high: float, count: int = 4) -> list:
    step = (high - low) / (count - 1)
    return [low + step * i for i in range(count)]


class _Frame:
    """Maps dates and values onto the plot area."""

    def __init__(self, first: dt.date, last: dt.date):
        self.first, self.last = first, last
        self.span = max((last - first).days, 1)
        self.plot_w = WIDTH - PAD_L - PAD_R
        self.plot_h = HEIGHT - PAD_T - PAD_B

    def x(self, when: dt.date) -> float:
        return PAD_L + (when - self.first).days / self.span * self.plot_w

    def y(self, value: float, low: float, high: float) -> float:
        return PAD_T + (1 - (value - low) / (high - low)) * self.plot_h


def _points(rows: list, key: str) -> list:
    """(date, value) for every row that has one, oldest first."""
    out = []
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        out.append((metrics.as_date(row["period"]), float(value)))
    return out


def _path(points: list, frame: _Frame, low: float, high: float,
          grain: str) -> str:
    """One polyline per unbroken run, so gaps in the data show as gaps."""
    limit = STEP_DAYS.get(grain, 1) * 1.5
    runs, current = [], []
    previous = None
    for when, value in points:
        if previous is not None and (when - previous).days > limit:
            runs.append(current)
            current = []
        current.append(f"{frame.x(when):.1f},{frame.y(value, low, high):.1f}")
        previous = when
    runs.append(current)
    return "".join(
        f'<polyline points="{" ".join(run)}" fill="none" '
        f'stroke="{{colour}}" stroke-width="1.8" stroke-linejoin="round"/>'
        for run in runs if len(run) > 1
    ) + "".join(
        # A single orphaned reading would otherwise be invisible.
        f'<circle cx="{run[0].split(",")[0]}" cy="{run[0].split(",")[1]}" '
        f'r="2.4" fill="{{colour}}"/>'
        for run in runs if len(run) == 1
    )


def _axis_labels(frame: _Frame, grain: str) -> str:
    """Four or five dates along the bottom, evenly spaced."""
    parts = []
    count = 4 if frame.span > 60 else 3
    for index in range(count + 1):
        when = frame.first + dt.timedelta(days=round(frame.span * index / count))
        anchor = "start" if index == 0 else ("end" if index == count else "middle")
        parts.append(
            f'<text x="{frame.x(when):.1f}" y="{HEIGHT - 10}" text-anchor="{anchor}" '
            f'class="chart-label">{escape(metrics.period_label(grain, when, short=True))}'
            f'</text>'
        )
    return "".join(parts)


def _y_axis(frame: _Frame, key: str, low: float, high: float,
            side: str, colour: str) -> str:
    """Ticks and gridlines for one axis. `side` is 'left' or 'right'.

    Labels are de-duplicated. Visceral fat moves between 6 and 8, so four evenly
    spaced ticks round to "6, 7, 7, 8" - the repeat makes the axis look broken
    and tells you nothing, so the second 7 is dropped.
    """
    parts = []
    seen = set()
    for value in _ticks(low, high):
        text = metrics.fmt(key, value)
        if text in seen:
            continue
        seen.add(text)
        y = frame.y(value, low, high)
        if side == "left":
            parts.append(
                f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{WIDTH - PAD_R}" '
                f'y2="{y:.1f}" class="chart-grid"/>'
                f'<text x="{PAD_L - 7}" y="{y + 3.5:.1f}" text-anchor="end" '
                f'class="chart-tick" fill="{colour}">{escape(text)}</text>'
            )
        else:
            parts.append(
                f'<text x="{WIDTH - PAD_R + 7}" y="{y + 3.5:.1f}" text-anchor="start" '
                f'class="chart-tick" fill="{colour}">{escape(text)}</text>'
            )
    return "".join(parts)


def _legend(entries: list) -> str:
    """Metric names in their series colour, along the top."""
    parts, x = [], PAD_L
    for key, colour in entries:
        label = config.LABELS.get(key, key)
        parts.append(
            f'<rect x="{x}" y="1" width="9" height="9" rx="2" fill="{colour}"/>'
            f'<text x="{x + 13}" y="9.5" class="chart-label">{escape(label)}</text>'
        )
        x += 26 + len(label) * 6.6
    return "".join(parts)


def _svg(body: str, label: str, payload: str = "") -> str:
    return (
        f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" width="100%" '
        f'preserveAspectRatio="xMinYMin meet" role="img" '
        f'aria-label="{escape(label)}" class="chart"{payload}>{body}</svg>'
    )


def _hover_layer(count: int) -> str:
    """The empty scaffolding chart-hover.js fills in.

    Built server-side rather than by the script so that the elements exist, and
    inherit the stylesheet, before any pointer touches the chart. One dot per
    series; the readout box sizes itself to its text at runtime.
    """
    # Parked off-canvas until the script moves them; a series with no reading
    # on the hovered date is hidden by sending its dot back there.
    dots = '<circle class="hover-dot" r="3.2" cx="-99" cy="-99"/>' * count
    return (
        '<g class="chart-hover" aria-hidden="true">'
        f'<line class="hover-line" x1="0" x2="0" y1="{PAD_T}" '
        f'y2="{HEIGHT - PAD_B}"/>'
        f'<g class="hover-dots">{dots}</g>'
        '<g class="hover-box"><rect class="hover-bg" rx="4"/>'
        '<g class="hover-text"></g></g>'
        '</g>'
    )


def pair(rows: list, key_a: str, key_b: str | None = None,
         grain: str = "daily") -> str:
    """One or two metrics against time. `key_b` on its own right-hand axis."""
    keys = [k for k in (key_a, key_b) if k]
    plots = [(key, _points(rows, key)) for key in keys]
    plots = [(key, points) for key, points in plots if points]
    if not plots:
        return _empty()

    every = [when for _, points in plots for when, _ in points]
    frame = _Frame(min(every), max(every))

    # One shared x-index across both series. They almost always share their
    # dates, and where they do not, the missing series simply has no point at
    # that index - which is what the nulls below mean.
    dates = sorted(set(every))
    hover = {
        "l": PAD_L, "t": PAD_T, "w": WIDTH,
        "pw": round(frame.plot_w, 1), "ph": round(frame.plot_h, 1),
        "x": [round(frame.x(when), 1) for when in dates],
        "d": [metrics.period_label(grain, when) for when in dates],
        "s": [],
    }

    body = [
        f'<line x1="{PAD_L}" y1="{PAD_T + frame.plot_h}" x2="{WIDTH - PAD_R}" '
        f'y2="{PAD_T + frame.plot_h}" class="chart-axis"/>'
    ]
    lines, legend = [], []
    for index, (key, points) in enumerate(plots):
        colour = COLOURS[index]
        low, high = _nice_bounds([value for _, value in points])
        # Only the first series draws gridlines; a second set would be noise.
        body.append(_y_axis(frame, key, low, high,
                            "left" if index == 0 else "right", colour))
        lines.append(_path(points, frame, low, high, grain).replace("{colour}", colour))
        legend.append((key, colour))

        # Raw values, not formatted ones. "72.4 kg" costs nine bytes a point
        # against five for 72.4, and over six years of daily readings that
        # difference is most of the payload. The decimal places and the unit
        # suffix are sent once and applied in the browser - the rule for both
        # still lives in core.metrics, which is what builds `unit` here.
        found = dict(points)
        hover["s"].append({
            "n": config.LABELS.get(key, key),
            "c": colour,
            "p": config.DP.get(key, 1),
            "u": metrics.unit_suffix(key),
            "y": [None if when not in found
                  else round(frame.y(found[when], low, high), 1) for when in dates],
            "v": [None if when not in found
                  else _payload_value(key, found[when]) for when in dates],
        })

    body.extend(lines)
    body.append(_axis_labels(frame, grain))
    if len(legend) > 1:
        body.append(_legend(legend))
    body.append(_hover_layer(len(plots)))

    title = " and ".join(config.LABELS.get(k, k) for k, _ in legend)
    return _svg("".join(body), f"{title} over time",
                payload=f" data-chart='{_json(hover)}'")


def _payload_value(key: str, value: float):
    """Round to the metric's precision here, so the browser never has to.

    JavaScript's toLocaleString rounds halves away from zero and Python rounds
    them to even, so 1667.5 kcal would read as 1,668 on the axis and 1,668 in
    the readout - but 1668.5 would read as 1,668 and 1,669. Averaging two whole
    numbers produces halves constantly, so this is not a corner case. Rounding
    once, on this side, means there is only ever one answer.
    """
    rounded = metrics.round_metric(key, value)
    return int(rounded) if config.DP.get(key, 1) == 0 else rounded


def _json(payload: dict) -> str:
    """Compact JSON, safe to sit inside a single-quoted HTML attribute.

    Only `'` and `&` can break out of one, and json.dumps has already escaped
    the quotes that would break out of a JSON string, so those two are the
    whole job. Every other value here is a number we generated.
    """
    import json

    return (json.dumps(payload, separators=(",", ":"))
            .replace("&", "&amp;").replace("'", "&#39;"))


def line(rows: list, key: str, grain: str = "daily") -> str:
    """A single metric against time."""
    return pair(rows, key, None, grain)


def sparkline(rows: list, key: str, width: int = 120, height: int = 28) -> str:
    """A tiny trend line for a metric tile - no axes, no labels."""
    points = _points(rows, key)
    if len(points) < 2:
        return ""
    values = [value for _, value in points]
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    first = points[0][0]
    days = max((points[-1][0] - first).days, 1)
    coords = " ".join(
        f"{(when - first).days / days * (width - 2) + 1:.1f},"
        f"{height - 2 - (value - low) / span * (height - 4):.1f}"
        for when, value in points
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'class="sparkline" aria-hidden="true">'
        f'<polyline points="{coords}" fill="none" stroke="currentColor" '
        f'stroke-width="1.4" stroke-linejoin="round"/></svg>'
    )
