/* Hover / drag readout for the SVG charts.
 *
 * web/charts.py draws the chart and attaches its points as JSON in a
 * `data-chart` attribute. This finds the nearest point to the pointer and
 * shows a crosshair plus a box with the date and each series' value.
 *
 * Everything happens in the browser, so it costs the NAS nothing - which is
 * the whole reason the charts are hand-rolled rather than drawn by a library.
 * The chart is fully readable without this file: it adds a readout, it does
 * not draw anything you cannot already see.
 *
 * Pointer events rather than mouse events, so a finger works too. On a phone
 * the readout follows a drag across the chart; a vertical scroll fires
 * pointercancel instead, which hides it and lets the page scroll normally.
 */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var LINE_HEIGHT = 14;

  function make(name, attrs) {
    var node = document.createElementNS(NS, name);
    for (var key in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, key)) {
        node.setAttribute(key, attrs[key]);
      }
    }
    return node;
  }

  /* Values arrive raw and are formatted here, because sending "1,687 kcal" for
     every point of a six-year daily series is most of the payload and the
     number alone is half the size. `p` is the metric's decimal places and `u`
     its unit suffix, both decided by core.metrics and sent once per series -
     nothing about how a metric reads is decided in this file. */
  function format(value, series) {
    return value.toLocaleString("en-GB", {
      minimumFractionDigits: series.p,
      maximumFractionDigits: series.p
    }) + series.u;
  }

  /* Index of the x closest to `value`. Binary search: at the "All" range this
     runs on every pointer move over 2,000-odd points. */
  function nearest(xs, value) {
    var low = 0;
    var high = xs.length - 1;
    while (low < high) {
      var mid = (low + high) >> 1;
      if (xs[mid] < value) { low = mid + 1; } else { high = mid; }
    }
    if (low > 0 && Math.abs(xs[low - 1] - value) < Math.abs(xs[low] - value)) {
      low -= 1;
    }
    return low;
  }

  function setup(svg) {
    var data;
    try {
      data = JSON.parse(svg.getAttribute("data-chart"));
    } catch (err) {
      return;  /* a malformed chart should not take the page down */
    }
    if (!data || !data.x || !data.x.length) { return; }

    var layer = svg.querySelector(".chart-hover");
    var line = svg.querySelector(".hover-line");
    var dots = svg.querySelectorAll(".hover-dot");
    var box = svg.querySelector(".hover-box");
    var background = svg.querySelector(".hover-bg");
    var textGroup = svg.querySelector(".hover-text");
    if (!layer || !line || !box || !textGroup) { return; }

    /* One text element per line: the date, then one per series. Built once
       and re-used, so a pointer move only rewrites textContent. */
    var rows = [];
    var total = data.s.length + 1;
    for (var i = 0; i < total; i++) {
      var text = make("text", {
        x: 7,
        y: 13 + i * LINE_HEIGHT,
        "class": i === 0 ? "hover-date" : "hover-value"
      });
      if (i > 0) { text.setAttribute("fill", data.s[i - 1].c); }
      textGroup.appendChild(text);
      rows.push(text);
    }
    for (var d = 0; d < dots.length; d++) {
      dots[d].setAttribute("fill", data.s[d].c);
    }

    var shown = false;
    var lastIndex = -1;

    function hide() {
      if (!shown) { return; }
      layer.classList.remove("on");
      shown = false;
      lastIndex = -1;
    }

    function show(event) {
      var rect = svg.getBoundingClientRect();
      if (!rect.width) { return; }
      /* Screen pixels -> viewBox units. The SVG is width:100% with a fixed
         viewBox, so the two only agree by accident. */
      var vx = (event.clientX - rect.left) * (data.w / rect.width);
      if (vx < data.l - 6 || vx > data.l + data.pw + 6) { hide(); return; }

      var index = nearest(data.x, vx);
      if (!shown) { layer.classList.add("on"); shown = true; }
      if (index === lastIndex) { return; }
      lastIndex = index;

      var x = data.x[index];
      line.setAttribute("x1", x);
      line.setAttribute("x2", x);

      rows[0].textContent = data.d[index];
      for (var s = 0; s < data.s.length; s++) {
        var series = data.s[s];
        var value = series.v[index];
        rows[s + 1].textContent = value === null
          ? series.n + " —"
          : series.n + "  " + format(value, series);
        if (value === null || series.y[index] === null) {
          dots[s].setAttribute("cx", -99);
          dots[s].setAttribute("cy", -99);
        } else {
          dots[s].setAttribute("cx", x);
          dots[s].setAttribute("cy", series.y[index]);
        }
      }

      /* Size the box to whatever the text turned out to be, rather than
         guessing from character counts - metrics carry units of different
         lengths and the date format changes with the grain. */
      var bounds = textGroup.getBBox();
      background.setAttribute("x", bounds.x - 7);
      background.setAttribute("y", bounds.y - 5);
      background.setAttribute("width", bounds.width + 14);
      background.setAttribute("height", bounds.height + 10);

      /* Sit to the right of the crosshair, and flip when there is no room. */
      var width = bounds.width + 14;
      var left = x + 12;
      if (left + width > data.w - 4) { left = x - 12 - width; }
      if (left < 4) { left = 4; }
      box.setAttribute("transform", "translate(" + left + "," + (data.t + 2) + ")");
    }

    svg.addEventListener("pointermove", show);
    svg.addEventListener("pointerdown", show);
    svg.addEventListener("pointerleave", hide);
    svg.addEventListener("pointercancel", hide);
    svg.addEventListener("pointerup", function (event) {
      /* Leave the readout up after a mouse click, drop it after a tap - on a
         phone there is no pointer left hovering to explain why it is there. */
      if (event.pointerType !== "mouse") { hide(); }
    });
  }

  function init() {
    var charts = document.querySelectorAll("svg.chart[data-chart]");
    for (var i = 0; i < charts.length; i++) { setup(charts[i]); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
