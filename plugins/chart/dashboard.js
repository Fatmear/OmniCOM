/* Dashboard runtime: widget model, drag/resize, ECharts rendering, Qt bridge.
 *
 * The page owns the *presentation*; Python owns persistence and the serial
 * data.  Every mutating action is reported to Python through the QWebChannel
 * object "omni" so the layout survives a restart, and Python pushes values in
 * as one batched JSON array per animation frame.
 *
 * Everything here is plain ES5-compatible DOM code on purpose: the bundle runs
 * inside Qt's Chromium with no build step, so there is no transpiler to lean on.
 */

(function () {
  "use strict";

  var GRID = 16;              // snap step, matches the always-on grid background
  var MIN_W = 160;
  var MIN_H = 110;
  var ZOOM_STRIP = 14;        // thickness of one axis zoom slider, in px
  var ZOOM_HIT = 20;          // strip + slack: a press here belongs to ECharts
  var ZOOM_FILL = "rgba(18,26,43,0.10)";
  var KIND_COLORS = {
    line: "#0B7A6E",
    gauge: "#3A2F86",
    value: "#235EB8",
    bar: "#0E6E9C",
    lamp: "#9B2C52"
  };

  var state = {
    widgets: [],            // [{id, kind, title, channel, x, y, w, h, options}]
    selected: null,
    /* R-337: rubber-band multi-selection, like the Windows desktop.  The
     * first entry is the anchor card a group drag measures its delta from. */
    selection: [],
    theme: {},
    values: {},             // channel -> latest numeric value
    /* channel -> [[t, v], ...].  The page keeps its own copy so a frame only
     * has to carry the new tail instead of the whole window. */
    series: {},
    /* 曲线点数 from Python; 0 means "keep everything retained". */
    limit: 5000,
    /* 展示帧数: how many points a curve draws per frame (default 300).
     * Kept apart from ``limit`` (retention) so a long session stays smooth
     * without throwing the history away. */
    visible: 0,
    /* channel -> array handed to ECharts; reused instead of re-sliced. */
    drawn: {},
    /* Channel versions invalidate drawing slices even when history is full. */
    revisions: {},
    drawnRevision: {},
    dirty: false,
    bridge: null,
    nextId: 1
  };

  var charts = {};          // widget id -> echarts instance
  var nodes = {};           // widget id -> DOM node
  var canvas = null;
  var toastTimer = null;
  var activeBatch = null;
  var layoutTimer = null;

  // ------------------------------------------------------------------ helpers
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  function snap(value) { return Math.round(value / GRID) * GRID; }

  function clamp(value, low, high) {
    return value < low ? low : (value > high ? high : value);
  }

  function toast(text) {
    var box = document.getElementById("toast");
    if (!box) { return; }
    box.textContent = text;
    box.className = "show";
    if (toastTimer) { clearTimeout(toastTimer); }
    toastTimer = setTimeout(function () { box.className = ""; }, 1600);
  }

  function theme() { return state.theme || {}; }

  function cardColor(kind) { return KIND_COLORS[kind] || KIND_COLORS.line; }

  /* ------------------------------------------------------------- axis zoom
   * R-170: both axes of a 曲线 card zoom *independently*.  Two sliders are the
   * whole UI, but they are useless unless the chosen range survives the next
   * frame: ``paint()`` runs ``setOption()`` roughly 30 times a second, and an
   * option that carries ``start``/``end`` resets them every time (that is the
   * documented ECharts behaviour, not a bug).  So the range lives on the widget
   * and is fed back into the option on every frame, and the page's own
   * ``dataZoom`` event writes the user's choice into it. */
  function zoomState(widget) {
    if (!widget.zoom) {
      widget.zoom = { x: [0, 100], y: [0, 100] };
    }
    return widget.zoom;
  }

  function bindZoom(chart, widget) {
    if (chart.omniZoomBound) { return; }
    chart.omniZoomBound = true;
    chart.on("dataZoom", function () {
      var zoom = zoomState(widget);
      var list = chart.getOption().dataZoom || [];
      for (var index = 0; index < list.length; index += 1) {
        var item = list[index] || {};
        var x = item.xAxisIndex;
        var y = item.yAxisIndex;
        if (x && x.length) { zoom.x = [Number(item.start), Number(item.end)]; }
        if (y && y.length) { zoom.y = [Number(item.start), Number(item.end)]; }
      }
    });
  }

  function zoomSlider(axis, zoom) {
    var axisStyleValue = axisStyle();
    var option = {
      type: "slider",
      filterMode: "none",
      showDetail: false,
      brushSelect: false,
      showDataShadow: false,
      borderColor: "transparent",
      backgroundColor: "transparent",
      fillerColor: ZOOM_FILL,
      moveHandleSize: 0,
      textStyle: { color: axisStyleValue.labelColor, fontSize: 9 },
      dataBackground: {
        lineStyle: { color: axisStyleValue.lineStyle.color, opacity: 0.35 },
        areaStyle: { color: axisStyleValue.lineStyle.color, opacity: 0.10 }
      },
      start: zoom[0],
      end: zoom[1]
    };
    if (axis === "x") {
      option.xAxisIndex = [0];
      option.height = ZOOM_STRIP;
      option.bottom = 2;
      option.left = 12;
      option.right = 30;
    } else {
      option.yAxisIndex = [0];
      option.width = ZOOM_STRIP;
      option.right = 2;
      option.top = 8;
      option.bottom = ZOOM_STRIP + 10;
    }
    return option;
  }

  // ------------------------------------------------------------------- bridge
  function send(action, payload) {
    if (!state.bridge) { return; }
    try {
      /* QWebChannel publishes each ``@Slot`` on the bridge object as a plain
       * property (``bridge.action``), so this is a direct call.  Writing
       * ``bridge.call("action", ...)`` invokes ``Function.prototype.call``
       * semantics that the plain object does not have, throws TypeError, and
       * the catch below swallows it: every layout change and every delete was
       * silently lost (R-165 / L-226). */
      state.bridge.action(action, JSON.stringify(payload || {}));
    } catch (error) {
      /* A dead bridge must never break the page; Python re-pushes state. */
    }
  }

  // ------------------------------------------------------------------ echarts
  function axisStyle() {
    var t = theme();
    return {
      lineStyle: { color: t.ink2 || "#46536B" },
      labelColor: t.ink2 || "#46536B"
    };
  }

  var VISIBLE_POINTS = 300;

  function visibleLimit() {
    return state.visible > 0 ? state.visible : VISIBLE_POINTS;
  }

  function drawnBuffer(channel, rows) {
    var revision = state.revisions[channel] || 0;
    if (!state.drawn[channel] || state.drawnRevision[channel] !== revision) {
      state.drawn[channel] = rows.slice(-visibleLimit());
      state.drawnRevision[channel] = revision;
    }
    return state.drawn[channel];
  }

  function lineSeries(widget, points) {
    var color = cardColor(widget.kind);
    var series = [];
    var channels = widget.channels && widget.channels.length ? widget.channels : [widget.channel];
    for (var index = 0; index < channels.length; index += 1) {
      var name = channels[index];
      var rows = drawnBuffer(name, points[name] || []);
      var entry = {};
      entry.id = name;
      entry.name = name || "-";
      entry.type = "line";
      entry.showSymbol = false;
      entry.smooth = false;
      entry.animation = false;
      /* ``large`` batches thousands of segments into one path; kept on even with
       * the 600-point window because a filled/zoomed axis can still push many
       * segments through the renderer. */
      entry.large = true;
      entry.largeThreshold = 200;
      entry.sampling = "none";
      /* Progressive rendering splits the drawing across several animation
       * frames instead of doing all of it inside the current one.  For a 5000
       * point curve that turns one long block into a few short ones, so the
       * window stays responsive even when 展示帧数 is raised.
       *
       * NOTE: an adaptive chunk size (``visible / 4``) was measured here and
       * made no reproducible difference - the offscreen numbers swung between
       * 13 ms and 84 ms for the same configuration - so the values stay fixed
       * and R-307 keeps asserting them (R-320). */
      entry.progressive = 800;
      entry.progressiveThreshold = 1500;
      entry.lineStyle = { width: 1.4, color: index === 0 ? color : null };
      entry.data = rows;
      series.push(entry);
    }
    return series;
  }

  function lineOption(widget, points) {
    var axis = axisStyle();
    var zoom = zoomState(widget);
    return {
      animation: false,
      backgroundColor: "transparent",
      legend: {
        show: !!(widget.channels && widget.channels.length > 1),
        type: "scroll",
        top: 2,
        textStyle: { color: theme().ink || "#121A2B", fontSize: 11 }
      },
      /* A crosshair + readout is free measurement: hovering reports the value at
       * that instant instead of forcing the user to eyeball a pixel. */
      tooltip: {
        trigger: "axis",
        confine: true,
        backgroundColor: theme().card || "#FFFFFF",
        borderColor: axis.lineStyle.color,
        borderWidth: 1,
        textStyle: { color: theme().ink || "#121A2B", fontSize: 11 },
        axisPointer: { type: "cross", lineStyle: { color: axis.lineStyle.color } }
      },
      grid: {
        left: 4,
        /* The two sliders are laid out *inside* the grid container, so the plot
         * has to give them room - otherwise they cover the bottom x labels and
         * the right edge of the curve (R-170).  ``containLabel`` already reserves
         * the label band, so the extra space is the strip plus that band. */
        right: ZOOM_STRIP + 6,
        top: widget.channels && widget.channels.length > 1 ? 28 : 8,
        bottom: ZOOM_STRIP + 16,
        containLabel: true
      },
      xAxis: {
        type: "value",
        axisLine: { lineStyle: axis.lineStyle },
        axisLabel: { color: axis.labelColor, fontSize: 10 },
        splitLine: { show: false }
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLine: { lineStyle: axis.lineStyle },
        axisLabel: { color: axis.labelColor, fontSize: 10 },
        splitLine: { lineStyle: { color: theme().lineSoft || "rgba(18,26,43,0.08)" } }
      },
      dataZoom: [zoomSlider("x", zoom.x), zoomSlider("y", zoom.y)],
      series: lineSeries(widget, points)
    };
  }

  function gaugeOption(widget) {
    var color = cardColor(widget.kind);
    var minimum = widget.min === undefined ? 0 : Number(widget.min);
    var maximum = widget.max === undefined ? 100 : Number(widget.max);
    if (!(maximum > minimum)) { maximum = minimum + 1; }
    var unit = widget.unit || "";
    return {
      animation: false,
      backgroundColor: "transparent",
      series: [{
        type: "gauge",
        min: minimum,
        max: maximum,
        startAngle: 210,
        endAngle: -30,
        radius: "92%",
        center: ["50%", "58%"],
        progress: { show: true, width: 8, roundCap: true, itemStyle: { color: color } },
        axisLine: { lineStyle: { width: 8, color: [[1, (theme().lineSoft || "rgba(18,26,43,0.08)")]] } },
        axisTick: { show: false },
        splitLine: { length: 6, lineStyle: { width: 1, color: theme().ink2 || "#46536B" } },
        axisLabel: { distance: 10, fontSize: 9, color: theme().ink2 || "#46536B" },
        pointer: { width: 3, length: "62%", itemStyle: { color: color } },
        anchor: { show: true, size: 6, itemStyle: { color: color } },
        title: { show: false },
        detail: {
          offsetCenter: [0, "38%"],
          fontSize: 15,
          fontWeight: 600,
          color: theme().ink || "#121A2B",
          formatter: function (value) { return Number(value).toFixed(2) + unit; }
        },
        data: [{ value: minimum }]
      }]
    };
  }

  // ------------------------------------------------------------------ widgets
  function body(widget) {
    var host = el("div", "body");
    if (widget.kind === "line") {
      var plot = el("div", "plot");
      host.appendChild(plot);
    } else if (widget.kind === "gauge") {
      var dial = el("div", "plot");
      host.appendChild(dial);
    } else if (widget.kind === "value") {
      var wrap = el("div", "value-body");
      var number = el("div", "value-number", "--");
      number.dataset.role = "number";
      wrap.appendChild(number);
      if (widget.unit) { wrap.appendChild(el("div", "value-unit", widget.unit)); }
      host.appendChild(wrap);
    } else if (widget.kind === "lamp") {
      var lamp = el("div", "lamp-body");
      var dot = el("div", "lamp-dot");
      dot.dataset.role = "dot";
      lamp.appendChild(dot);
      var label = el("div", "lamp-text", widget.onText || "ON");
      label.dataset.role = "lampText";
      lamp.appendChild(label);
      host.appendChild(lamp);
    } else if (widget.kind === "bar") {
      /* 进度条 (R-172): the fill is a plain div whose width *is* the reading,
       * so no ECharts instance is created - a bar that redrew a canvas 30 times
       * a second would cost far more than one style write. */
      var bar = el("div", "bar-body");
      var readout = el("div", "bar-readout");
      var barNumber = el("span", "bar-number", "--");
      barNumber.dataset.role = "number";
      readout.appendChild(barNumber);
      if (widget.unit) { readout.appendChild(el("span", "bar-unit", widget.unit)); }
      bar.appendChild(readout);
      var barTrack = el("div", "bar-track");
      barTrack.dataset.role = "track";
      var barFill = el("div", "bar-fill");
      barFill.dataset.role = "fill";
      barTrack.appendChild(barFill);
      bar.appendChild(barTrack);
      bar.appendChild(el("div", "bar-scale", barScaleText(widget)));
      host.appendChild(bar);
    }
    return host;
  }

  /* ``[min … max]`` under the track; recomputed because the palette may change
   * the range after the card exists (``updateWidget``). */
  function barScaleText(widget) {
    var low = widget.min === undefined || widget.min === null ? 0 : Number(widget.min);
    var high = widget.max === undefined || widget.max === null ? 100 : Number(widget.max);
    return formatValue(low, widget.decimals) + " \u2013 " + formatValue(high, widget.decimals);
  }

  /* Fraction of the track a value fills, clamped to 0..1.
   * ``max <= min`` is a user typo, not a reason to divide by zero: treat it as a
   * full bar instead of ``Infinity``/``NaN`` leaking into a CSS width. */
  function barFraction(widget, value) {
    var low = widget.min === undefined || widget.min === null ? 0 : Number(widget.min);
    var high = widget.max === undefined || widget.max === null ? 100 : Number(widget.max);
    if (!isFinite(low) || !isFinite(high) || !isFinite(Number(value))) { return 0; }
    if (high <= low) { return 1; }
    return clamp((Number(value) - low) / (high - low), 0, 1);
  }

  function buildCard(widget) {
    var card = el("div", "card");
    card.dataset.id = widget.id;
    card.dataset.kind = widget.kind;
    var head = el("div", "head");
    head.appendChild(el("span", "grip", "\u2261"));
    head.appendChild(el("span", "title", widget.title || widget.channel || widget.id));
    head.appendChild(el("span", "kind", widget.kind));
    if (widget.kind === "line" || widget.kind === "lamp") {
      var sources = el("button", "sources", "\u2699");
      sources.type = "button";
      sources.title = widget.kind === "lamp"
        ? (theme().lampColorLabel || "Lamp colors")
        : (theme().sourceLabel || "Curve data sources");
      sources.setAttribute("aria-label", sources.title);
      sources.addEventListener("pointerdown", function (event) {
        event.stopPropagation();
      });
      sources.addEventListener("click", function (event) {
        event.stopPropagation();
        send("sources", { id: widget.id });
      });
      head.appendChild(sources);
    }
    var del = el("button", "del", "\u00d7");
    del.type = "button";
    del.addEventListener("click", function (event) {
      event.stopPropagation();
      removeWidget(widget.id);
    });
    /* The header starts a drag on pointerdown and *captures the pointer*, so the
     * release lands on the card, not on this button - and a click is only
     * dispatched to the element that owns both the press and the release.
     * Swallowing pointerdown here is what actually makes × delete (R-165). */
    del.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) { return; }
      event.stopPropagation();
    });
    head.appendChild(del);
    card.appendChild(head);
    card.appendChild(body(widget));
    var grip = el("div", "resize");
    card.appendChild(grip);
    place(card, widget);
    hookPointer(card, widget, head, grip);
    return card;
  }

  function place(node, widget) {
    node.style.left = widget.x + "px";
    node.style.top = widget.y + "px";
    node.style.width = widget.w + "px";
    node.style.height = widget.h + "px";
  }

  /* Size the inner surface to the furthest card so ``#canvas`` can scroll it.
   *
   * ``#canvas`` is the scroll viewport (fixed to the page) and ``#surface`` is
   * the content box inside it.  Absolutely positioned cards do not contribute
   * to a scroll range, and enlarging the *viewport* just clips against the
   * body - both measured, see the CSS note (R-313). */
  var EXTENT_PAD = 48;

  function updateExtent() {
    if (!canvas) { return; }
    var surface = canvas.querySelector("#surface");
    if (!surface) { return; }
    var maxX = 0;
    var maxY = 0;
    state.widgets.forEach(function (widget) {
      maxX = Math.max(maxX, widget.x + widget.w);
      maxY = Math.max(maxY, widget.y + widget.h);
    });
    surface.style.width = Math.max(canvas.clientWidth, maxX + EXTENT_PAD) + "px";
    surface.style.height = Math.max(canvas.clientHeight, maxY + EXTENT_PAD) + "px";
  }

  /* Where a newly added card should land: the middle of what the user sees.
   *
   * The old behaviour tiled cards by index from the origin, so the tenth card
   * appeared several screens further down and looked like it "flew away" (the
   * user's words).  The visible centre is ``scrollLeft/scrollTop`` plus half the
   * viewport, offset by half the card so it is centred rather than cornered. */
  function centerSpot(width, height) {
    var viewWidth = canvas ? canvas.clientWidth : window.innerWidth;
    var viewHeight = canvas ? canvas.clientHeight : window.innerHeight;
    var left = canvas ? canvas.scrollLeft : 0;
    var top = canvas ? canvas.scrollTop : 0;
    var x = snap(left + Math.max(0, (viewWidth - width) / 2));
    var y = snap(top + Math.max(0, (viewHeight - height) / 2));
    return { x: Math.max(0, x), y: Math.max(0, y) };
  }

  function scheduleLayout() {
    if (layoutTimer !== null) { clearTimeout(layoutTimer); }
    layoutTimer = setTimeout(function () {
      layoutTimer = null;
      if (!canvas || canvas.clientWidth <= 0 || canvas.clientHeight <= 0) { return; }
      var placed = false;
      state.widgets.forEach(function (widget) {
        if (widget.autoPlaced) {
          var spot = centerSpot(widget.w, widget.h);
          widget.x = spot.x;
          widget.y = spot.y;
          widget.autoPlaced = false;
          place(nodes[widget.id], widget);
          placed = true;
        }
      });
      updateExtent();
      resizeAll();
      state.widgets.forEach(function (widget) {
        if (widget.needsPaint && inViewport(widget)) {
          try {
            paint(widget, state.values, state.series, null);
            widget.needsPaint = false;
          } catch (error) {
            console.warn("dashboard layout paint failed:", error);
          }
        }
      });
      if (placed) { persist(); }
    }, 40);
  }

  function inViewport(widget) {
    if (!canvas) { return true; }
    return widget.x + widget.w >= canvas.scrollLeft &&
      widget.y + widget.h >= canvas.scrollTop &&
      widget.x < canvas.scrollLeft + canvas.clientWidth &&
      widget.y < canvas.scrollTop + canvas.clientHeight;
  }

  /* Parts of a card that must keep their own meaning instead of starting a drag. */
  var NO_DRAG = ".del, .resize, .sources";

  /* R-170: the two axis sliders live inside the plot, so a press on one of them
   * has to reach ECharts.  Only the strips themselves are excluded - the rest of
   * the curve area still drags the card, which is what R-169 asked for. */
  function inZoomStrip(card, event) {
    var plot = card.querySelector(".plot");
    if (!plot || !plot.contains(event.target)) { return false; }
    var box = plot.getBoundingClientRect();
    var x = event.clientX - box.left;
    var y = event.clientY - box.top;
    return y >= box.height - ZOOM_HIT || x >= box.width - ZOOM_HIT ||
      (card.dataset.kind === "line" && y < 24);
  }

  function hookPointer(card, widget, head, grip) {
    /* Dragging is **always** available (R-169), and there is no edit mode to
     * enable first (R-173).  It used to require the 编辑 switch to be ticked
     * *and* it only listened on the 26 px header, while the × became visible on
     * plain hover - so the card looked interactive yet a press-and-drag on the
     * body (or on the header with 编辑 unticked) moved nothing at all, and the
     * user concluded the feature had been deleted.  The whole card is the drag
     * handle now, minus the × and the resize corner. */
    card.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) { return; }
      if (event.target.closest && event.target.closest(NO_DRAG)) { return; }
      /* A press on an axis slider must zoom, not drag the card (R-170). */
      if (inZoomStrip(card, event)) { return; }
      event.preventDefault();
      /* R-337: Ctrl toggles membership; a plain press on an already selected
       * card keeps the group so it can be dragged as a whole (desktop rule). */
      if (event.ctrlKey || event.metaKey) {
        toggleSelect(widget.id);
        return;
      }
      if (state.selection.indexOf(widget.id) < 0) { select(widget.id); }
      beginDrag(event, card, widget, false);
    });
    grip.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) { return; }
      event.preventDefault();
      if (state.selection.indexOf(widget.id) < 0) { select(widget.id); }
      beginDrag(event, card, widget, true);
    });
  }

  function beginDrag(event, card, widget, resizing) {
    widget.autoPlaced = false;
    var startX = event.clientX;
    var startY = event.clientY;
    var origin = { x: widget.x, y: widget.y, w: widget.w, h: widget.h };
    /* R-337: dragging a selected card moves the whole selection.  Each member
     * keeps its own origin, and the group delta is clamped so no card crosses
     * the canvas edge - the relative arrangement survives the clamp. */
    var group = [];
    if (!resizing && state.selection.indexOf(widget.id) >= 0) {
      state.widgets.forEach(function (item) {
        if (state.selection.indexOf(item.id) >= 0) {
          group.push({ widget: item, x: item.x, y: item.y });
        }
      });
    }
    var minOriginX = origin.x;
    var minOriginY = origin.y;
    group.forEach(function (member) {
      minOriginX = Math.min(minOriginX, member.x);
      minOriginY = Math.min(minOriginY, member.y);
    });
    card.classList.add("dragging");
    group.forEach(function (member) {
      if (member.widget.id !== widget.id && nodes[member.widget.id]) {
        nodes[member.widget.id].classList.add("dragging");
      }
    });
    /* Pointer capture is what keeps the moves coming once the cursor leaves the
     * card, but it throws when the pointer is already gone (a synthetic event,
     * or a press whose release raced the handler).  A throw here used to abort
     * the whole gesture before a single listener was attached (R-169). */
    try {
      card.setPointerCapture(event.pointerId);
    } catch (error) {
      /* keep dragging without capture; the card still receives the moves */
    }

    function move(moveEvent) {
      var dx = moveEvent.clientX - startX;
      var dy = moveEvent.clientY - startY;
      if (resizing) {
        widget.w = Math.max(MIN_W, snap(origin.w + dx));
        widget.h = Math.max(MIN_H, snap(origin.h + dy));
        place(card, widget);
        resizeChart(widget);
      } else if (group.length > 1) {
        dx = Math.max(dx, -minOriginX);
        dy = Math.max(dy, -minOriginY);
        group.forEach(function (member) {
          member.widget.autoPlaced = false;
          member.widget.x = Math.max(0, snap(member.x + dx));
          member.widget.y = Math.max(0, snap(member.y + dy));
          if (nodes[member.widget.id]) { place(nodes[member.widget.id], member.widget); }
        });
      } else {
        widget.x = Math.max(0, snap(origin.x + dx));
        widget.y = Math.max(0, snap(origin.y + dy));
        place(card, widget);
      }
      scheduleLayout();
    }

    function up() {
      card.classList.remove("dragging");
      group.forEach(function (member) {
        if (nodes[member.widget.id]) {
          nodes[member.widget.id].classList.remove("dragging");
        }
      });
      card.removeEventListener("pointermove", move);
      card.removeEventListener("pointerup", up);
      card.removeEventListener("pointercancel", up);
      persist();
    }

    card.addEventListener("pointermove", move);
    card.addEventListener("pointerup", up);
    card.addEventListener("pointercancel", up);
  }

  function resizeChart(widget) {
    var chart = charts[widget.id];
    var host = nodes[widget.id] && nodes[widget.id].querySelector(".plot");
    if (chart && host && host.clientWidth > 0 && host.clientHeight > 0 &&
        (chart.getWidth() !== host.clientWidth || chart.getHeight() !== host.clientHeight)) {
      chart.resize();
    }
  }

  function select(id) {
    state.selected = id;
    state.selection = id === null ? [] : [id];
    paintSelection();
  }

  /* R-337: Ctrl+click membership toggle; the anchor follows the newest pick. */
  function toggleSelect(id) {
    var index = state.selection.indexOf(id);
    if (index >= 0) {
      state.selection.splice(index, 1);
      state.selected = state.selection.length
        ? state.selection[state.selection.length - 1] : null;
    } else {
      state.selection.push(id);
      state.selected = id;
    }
    paintSelection();
  }

  function setSelection(ids) {
    state.selection = ids.slice();
    state.selected = ids.length ? ids[ids.length - 1] : null;
    paintSelection();
  }

  function paintSelection() {
    Object.keys(nodes).forEach(function (key) {
      nodes[key].classList.toggle("selected", state.selection.indexOf(key) >= 0);
    });
  }

  /* R-337: rubber band on the blank canvas, like the Windows desktop.
   *
   * A left press that lands on ``#canvas``/``#surface`` itself (not a card)
   * starts a marquee in *surface* coordinates so it stays glued to the grid
   * while the canvas scrolls.  Cards whose rectangles intersect the marquee
   * are selected live; Ctrl adds to the previous selection.  A press without
   * movement is a plain click on the background and clears the selection. */
  var MARQUEE_MIN = 4;

  function hookMarquee() {
    if (!canvas) { return; }
    canvas.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) { return; }
      var target = event.target;
      if (target !== canvas && target.id !== "surface" && target.id !== "empty"
          && !target.closest("#empty")) { return; }
      event.preventDefault();
      var surface = document.getElementById("surface");
      var box = surface.getBoundingClientRect();
      var startX = event.clientX - box.left;
      var startY = event.clientY - box.top;
      var additive = event.ctrlKey || event.metaKey;
      var base = additive ? state.selection.slice() : [];
      var band = el("div", "marquee");
      surface.appendChild(band);
      var moved = false;
      try { canvas.setPointerCapture(event.pointerId); } catch (error) { /* synthetic */ }

      function applyBand(currentX, currentY) {
        var left = Math.min(startX, currentX);
        var top = Math.min(startY, currentY);
        var width = Math.abs(currentX - startX);
        var height = Math.abs(currentY - startY);
        band.style.left = left + "px";
        band.style.top = top + "px";
        band.style.width = width + "px";
        band.style.height = height + "px";
        var picked = base.slice();
        state.widgets.forEach(function (widget) {
          var hit = widget.x < left + width && widget.x + widget.w > left &&
            widget.y < top + height && widget.y + widget.h > top;
          if (hit && picked.indexOf(widget.id) < 0) { picked.push(widget.id); }
        });
        setSelection(picked);
      }

      function move(moveEvent) {
        var currentX = moveEvent.clientX - box.left;
        var currentY = moveEvent.clientY - box.top;
        if (Math.abs(currentX - startX) >= MARQUEE_MIN ||
            Math.abs(currentY - startY) >= MARQUEE_MIN) { moved = true; }
        if (moved) { applyBand(currentX, currentY); }
      }

      function up() {
        canvas.removeEventListener("pointermove", move);
        canvas.removeEventListener("pointerup", up);
        canvas.removeEventListener("pointercancel", up);
        if (band.parentNode) { band.parentNode.removeChild(band); }
        if (!moved && !additive) { setSelection([]); }
      }

      canvas.addEventListener("pointermove", move);
      canvas.addEventListener("pointerup", up);
      canvas.addEventListener("pointercancel", up);
    });
  }

  function removeWidget(id) {
    var index = -1;
    for (var i = 0; i < state.widgets.length; i += 1) {
      if (state.widgets[i].id === id) { index = i; }
    }
    if (index < 0) { return; }
    state.widgets.splice(index, 1);
    if (nodes[id]) {
      nodes[id].parentNode.removeChild(nodes[id]);
      delete nodes[id];
    }
    if (charts[id]) {
      charts[id].dispose();
      delete charts[id];
    }
    if (activeBatch) {
      delete activeBatch.waiting[id];
      finishBatch();
    }
    if (state.selected === id) { state.selected = null; }
    var pick = state.selection.indexOf(id);
    if (pick >= 0) { state.selection.splice(pick, 1); }
    renderEmpty();
    scheduleLayout();
    persist();
  }

  function renderEmpty() {
    var box = document.getElementById("empty");
    if (!box) { return; }
    box.classList.toggle("hidden", state.widgets.length > 0);
  }

  // ------------------------------------------------------------------- render
  function ensureChart(widget) {
    if (widget.kind !== "line" && widget.kind !== "gauge") { return null; }
    if (charts[widget.id]) { return charts[widget.id]; }
    var node = nodes[widget.id];
    if (!node) { return null; }
    var host = node.querySelector(".plot");
    if (!host || typeof echarts === "undefined") { return null; }
    var chart = echarts.init(host, null, { renderer: "canvas" });
    charts[widget.id] = chart;
    chart.omniRendered = 0;
    chart.omniFinished = 0;
    chart.on("rendered", function () {
      chart.omniRendered += 1;
      var batch = activeBatch;
      if (!batch || !batch.waiting[widget.id]) { return; }
      if (chart.omniFrame) { cancelAnimationFrame(chart.omniFrame); }
      chart.omniFrame = requestAnimationFrame(function () {
        chart.omniFrame = null;
        if (activeBatch === batch) {
          delete batch.waiting[widget.id];
          finishBatch();
        }
      });
    });
    chart.on("finished", function () {
      chart.omniFinished += 1;
      if (activeBatch && activeBatch.waiting[widget.id]) {
        delete activeBatch.waiting[widget.id];
        finishBatch();
      }
    });
    /* The user's zoom choice has to be captured as it happens: reading it back
     * from the option on the next frame is too late, because ``lineOption()``
     * writes ``start``/``end`` again (R-170). */
    bindZoom(chart, widget);
    if (widget.kind === "line") { applyLineStructure(chart, widget); }
    else { applyGaugeStructure(chart, widget); }
    return chart;
  }

  /* Axes, tooltip and the two zoom sliders are structural: they only have to be
   * sent when a card first appears, when the theme changes, or when the user
   * drags a slider - *not* on every data frame.  This split is what took the
   * live frame from ~21-24 ms per curve to a plain data update. */
  function applyLineStructure(chart, widget) {
    if (!chart) { return; }
    chart.setOption(lineOption(widget, state.series), {
      notMerge: false,
      replaceMerge: ["series"],
      lazyUpdate: false
    });
  }

  /* A gauge's structure (angles, pointer, colours, detail format) is built
   * once; ``paint()`` afterwards only moves the needle. */
  function applyGaugeStructure(chart, widget) {
    if (!chart) { return; }
    chart.setOption(gaugeOption(widget), { notMerge: true });
  }

  function paint(widget, values, history, fresh) {
    var node = nodes[widget.id];
    if (!node) { return; }
    var current = values[widget.channel];
    if (widget.kind === "line") {
      var chart = ensureChart(widget);
      if (!chart) { return; }
      /* Skip a card whose channels did not change in this batch.  A 5000-point
       * ``setOption`` costs ~24 ms; repainting an unchanged curve 30 times a
       * second is what made the dashboard feel like it stuttered while the log
       * kept up (user, 2026-11-19).  A full resync (``fresh`` empty) must still
       * repaint everything, because that is how a layout change lands. */
      var touched = false;
      var channels = (widget.channels && widget.channels.length)
        ? widget.channels : [widget.channel];
      if (fresh) {
        for (var index = 0; index < channels.length; index += 1) {
          if (fresh[channels[index]]) { touched = true; break; }
        }
      } else {
        touched = true;
      }
      if (!touched) { return; }
      var series = channels.map(function (name) {
        return {id: name, data: drawnBuffer(name, history[name] || [])};
      });
      chart.setOption({ series: series }, { lazyUpdate: true });
      return chart;
    }
    if (widget.kind === "gauge") {
      var dial = ensureChart(widget);
      if (!dial) { return; }
      dial.setOption({
        series: [{
          data: [{ value: Number(current || 0) }]
        }]
      }, { lazyUpdate: true });
      return dial;
    }
    if (widget.kind === "value") {
      var number = node.querySelector('[data-role="number"]');
      if (number) {
        number.textContent = current === undefined || current === null
          ? "--" : formatValue(current, widget.decimals);
      }
      return;
    }
    if (widget.kind === "lamp") {
      var dot = node.querySelector('[data-role="dot"]');
      var text = node.querySelector('[data-role="lampText"]');
      var on = current !== undefined && current !== null && Number(current) !== 0;
      var matching = Array.isArray(widget.colorRules);
      var color;
      var known = current !== undefined && current !== null && isFinite(Number(current));
      if (matching) {
        var rule = known ? widget.colorRules.find(function (item) {
          return Number(item.value) === Number(current);
        }) : null;
        color = known ? (rule ? rule.color : widget.fallbackColor) : null;
        on = !!color;
      } else {
        color = on ? (widget.onColor || theme().ok || "#16A34A") : (widget.offColor || "#C8D0DC");
      }
      if (dot) {
        dot.className = "lamp-dot" + (on ? " on" : "");
        dot.style.backgroundColor = color || "transparent";
      }
      if (text) {
        text.textContent = matching ? (known ? formatValue(current, widget.decimals) : "--")
          : (on ? (widget.onText || "ON") : (widget.offText || "OFF"));
      }
      return;
    }
    if (widget.kind === "bar") { paintBar(widget, current); return; }
  }

  function paintBar(widget, current) {
    var node = nodes[widget.id];
    if (!node) { return; }
    var known = current !== undefined && current !== null && isFinite(Number(current));
    var fill = node.querySelector('[data-role="fill"]');
    var number = node.querySelector('[data-role="number"]');
    if (fill) {
      fill.style.width = (known ? barFraction(widget, current) * 100 : 0) + "%";
      /* Over-range is a real reading, not a full bar: say so instead of
       * silently pinning the fill at 100% and looking healthy. */
      var high = widget.max === undefined || widget.max === null ? 100 : Number(widget.max);
      fill.classList.toggle("over", known && isFinite(high) && Number(current) > high);
    }
    if (number) {
      number.textContent = known ? formatValue(current, widget.decimals) : "--";
    }
    var scale = node.querySelector(".bar-scale");
    if (scale) { scale.textContent = barScaleText(widget); }
  }

  function formatValue(value, decimals) {
    var number = Number(value);
    if (!isFinite(number)) { return String(value); }
    var places = decimals === undefined || decimals === null ? 2 : Number(decimals);
    return number.toFixed(clamp(places, 0, 8));
  }

  function finishBatch() {
    if (!activeBatch || activeBatch.submitting || Object.keys(activeBatch.waiting).length) { return; }
    var batch = activeBatch;
    activeBatch = null;
    send("acked", { epoch: batch.epoch, cost: Math.round(performance.now() - batch.started) });
  }

  // ------------------------------------------------------------------- public
  var api = {
    /* Replace the whole layout (initial load and undo). */
    setLayout: function (payload) {
      var data = typeof payload === "string" ? JSON.parse(payload) : payload;
      var list = (data && data.widgets) || [];
      state.widgets = list.map(function (raw, index) {
        return {
          id: raw.id || ("w" + (index + 1)),
          kind: raw.kind || "line",
          title: raw.title || "",
          channel: raw.channel || "",
          channels: raw.channels || null,
          unit: raw.unit || "",
          min: raw.min,
          max: raw.max,
          decimals: raw.decimals,
          onText: raw.onText || "ON",
          offText: raw.offText || "OFF",
          onColor: raw.onColor || null,
          offColor: raw.offColor || null,
          colorRules: Array.isArray(raw.colorRules) ? raw.colorRules : null,
          fallbackColor: raw.fallbackColor || null,
          x: Number(raw.x) || 0,
          y: Number(raw.y) || 0,
          w: Math.max(MIN_W, Number(raw.w) || 320),
          h: Math.max(MIN_H, Number(raw.h) || 200)
        };
      });
      state.nextId = state.widgets.length + 1;
      state.selected = null;
      state.selection = [];
      rebuild();
      return state.widgets.length;
    },

    /* One frame of data: {channel: value} plus the line history.
     *
     * ``append`` means the rows are the *new tail* since the previous batch, so
     * the page extends its own copy instead of replacing it.  Dropping the full
     * window on every frame was the reported lag: 30 frames a second of
     * multi-megabyte JSON could not be consumed, so the queues grew and the
     * picture drifted further and further behind the live data.
     *
     * ``epoch`` is echoed back to Python once this batch has been applied, which
     * is what lets Python skip a frame while the renderer is still busy. */
    setValues: function (payload) {
      var data = typeof payload === "string" ? JSON.parse(payload) : payload;
      var values = (data && data.values) || {};
      var history = (data && data.history) || {};
      var append = !!(data && data.append);
      var previousValues = state.values;
      state.values = values;
      var series = append ? state.series : (state.series = {});
      if (!append) {
        /* A full resync replaces the history, so the reusable draw buffers must
         * start over from the new rows. */
        state.drawn = {};
        state.drawnRevision = {};
        state.revisions = {};
      }
      /* Which channels actually gained samples in this batch?  A curve card
       * whose channels did not change does not need ``setOption`` at all -
       * measured at 24 ms per call for 5000x2 points, two cards at 30 fps
       * saturated the renderer and that was the reported stutter. */
      var fresh = {};
      Object.keys(history).forEach(function (channel) {
        var rows = history[channel] || [];
        state.revisions[channel] = (state.revisions[channel] || 0) + 1;
        if (rows.length) { fresh[channel] = true; }
        if (!append) {
          series[channel] = rows.slice(-state.limit);
          return;
        }
        var target = series[channel];
        if (!target) { series[channel] = rows.slice(-state.limit); return; }
        for (var index = 0; index < rows.length; index += 1) {
          target.push(rows[index]);
        }
        /* Bound the page history to the same retention limit as Python. */
        var limit = state.limit;
        if (limit > 0 && target.length > limit) {
          var dropped = target.length - limit;
          target.splice(0, dropped);
        }
      });
      var start = Number(data && data.start) || 0;
      if (start > 0) {
        Object.keys(series).forEach(function (channel) {
          var rows = series[channel];
          var count = 0;
          while (count < rows.length && rows[count][0] < start) { count += 1; }
          if (count) {
            rows.splice(0, count);
            state.revisions[channel] = (state.revisions[channel] || 0) + 1;
            fresh[channel] = true;
          }
        });
      }
      var started = (typeof performance !== "undefined") ? performance.now() : 0;
      var changed = 0;
      var epoch = Number(data && data.epoch) || 0;
      activeBatch = epoch ? {epoch: epoch, started: started, waiting: {}, submitting: true} : null;
      try {
        state.widgets.forEach(function (widget) {
          if (append && widget.kind !== "line" &&
              previousValues[widget.channel] === values[widget.channel]) { return; }
          if (!inViewport(widget)) { widget.needsPaint = true; return; }
          try {
            if (activeBatch && (widget.kind === "line" || widget.kind === "gauge")) {
              activeBatch.waiting[widget.id] = true;
            }
            var chart = paint(widget, values, series, append ? fresh : null);
            widget.needsPaint = false;
            if (!chart && activeBatch) { delete activeBatch.waiting[widget.id]; }
          } catch (error) {
            if (activeBatch) { delete activeBatch.waiting[widget.id]; }
            /* One broken card must not stop the others.  A chart with a
             * zero-sized container (the page was hidden while a layout change
             * landed) used to throw here, and the ack below was never sent -
             * which froze *every* card, not just this one (L-307). */
            if (typeof console !== "undefined" && console.warn) {
              console.warn("dashboard paint failed:", error);
            }
          }
          changed += 1;
        });
      } finally {
        /* The ack carries how long the paint took, so Python can slow the timer
         * down instead of queueing work the renderer cannot absorb.  It is sent
         * from ``finally``: an exception in one card must never strand Python
         * waiting for an acknowledgement that will not come. */
        if (activeBatch) {
          activeBatch.submitting = false;
          finishBatch();
        }
      }
      return changed;
    },

    /* Called from Python when the theme or language changes. */
    setTheme: function (payload) {
      state.theme = typeof payload === "string" ? JSON.parse(payload) : (payload || {});
      rebuild();
      return true;
    },

    /* 曲线点数 (R-301): cap what the page keeps per channel.  Python enforces
     * the same number when it builds a full resync, so both sides agree. */
    setPoints: function (payload) {
      var data = typeof payload === "string" ? JSON.parse(payload) : payload;
      var limit = Number((data && data.limit) || 0) || 0;
      state.limit = limit > 0 ? clamp(limit, 100, 5000) : 5000;
      /* 展示帧数 arrives in the same call: retention and the drawn window are
       * different knobs and both are persisted on the Python side. */
      var visible = Number((data && data.visible) || 0) || 0;
      state.visible = visible > 0 ? visible : 0;
      state.drawn = {};
      state.drawnRevision = {};
      if (state.limit > 0) {
        Object.keys(state.series).forEach(function (channel) {
          var rows = state.series[channel];
          if (rows && rows.length > state.limit) {
            rows.splice(0, rows.length - state.limit);
          }
        });
      }
      return JSON.stringify({limit: state.limit, visible: state.visible});
    },

    addWidget: function (payload) {
      var raw = typeof payload === "string" ? JSON.parse(payload) : payload;
      var auto = raw.x === undefined || raw.x === null || raw.y === undefined || raw.y === null;
      var spot = auto ? centerSpot(Number(raw.w) || 360, Number(raw.h) || 220) : { x: 0, y: 0 };
      var widget = {
        /* Python owns identity: it needs the same id to persist the layout.
         * Only invent one when it sent none. */
        id: raw.id || ("w" + (Date.now() % 100000) + state.nextId),
        kind: raw.kind || "line",
        title: raw.title || "",
        channel: raw.channel || "",
        channels: raw.channels || null,
        unit: raw.unit || "",
        min: raw.min,
        max: raw.max,
        decimals: raw.decimals,
        onText: raw.onText || "ON",
        offText: raw.offText || "OFF",
        onColor: raw.onColor || null,
        offColor: raw.offColor || null,
        colorRules: Array.isArray(raw.colorRules) ? raw.colorRules : null,
        fallbackColor: raw.fallbackColor || null,
        x: auto ? spot.x : snap(raw.x),
        y: auto ? spot.y : snap(raw.y),
        w: Math.max(MIN_W, Number(raw.w) || 360),
        h: Math.max(MIN_H, Number(raw.h) || 220)
      };
      state.nextId += 1;
      state.widgets.push(widget);
      widget.autoPlaced = auto;
      mount(widget);
      renderEmpty();
      scheduleLayout();
      if (!auto) { persist(); }
      return widget.id;
    },

    /* Geometry/binding edit of one widget; the palette sends it. */
    updateWidget: function (payload) {
      var data = typeof payload === "string" ? JSON.parse(payload) : payload;
      var widget = state.widgets.filter(function (item) { return item.id === data.id; })[0];
      if (!widget) { return false; }
      if (data.x !== undefined || data.y !== undefined) { widget.autoPlaced = false; }
      Object.keys(data).forEach(function (key) {
        if (key !== "id") { widget[key] = data[key]; }
      });
      var node = nodes[widget.id];
      if (node) {
        place(node, widget);
        var title = node.querySelector(".title");
        if (title) { title.textContent = widget.title || widget.channel || widget.id; }
      }
      scheduleLayout();
      if (charts[widget.id]) {
        if (widget.kind === "line") { applyLineStructure(charts[widget.id], widget); }
        if (widget.kind === "gauge") { applyGaugeStructure(charts[widget.id], widget); }
      }
      paint(widget, state.values, state.series);
      persist();
      return true;
    },

    removeWidget: function (id) { removeWidget(id); return true; },

    /* Snapshot for Python-side assertions and for the layout file. */
    describe: function () {
      return JSON.stringify({
        selected: state.selected,
        selection: state.selection.slice(),
        widgets: state.widgets.map(function (widget) {
          return {
            id: widget.id, kind: widget.kind, title: widget.title,
            channel: widget.channel, channels: widget.channels,
            unit: widget.unit, min: widget.min, max: widget.max,
            decimals: widget.decimals,
            onText: widget.onText, offText: widget.offText,
            onColor: widget.onColor, offColor: widget.offColor,
            colorRules: widget.colorRules, fallbackColor: widget.fallbackColor,
            x: widget.x, y: widget.y, w: widget.w, h: widget.h
          };
        }),
        charts: Object.keys(charts).length
      });
    },

    /* Number of painted (non-background) pixels inside a widget, so the GUI
     * self-test can prove ECharts really drew instead of trusting a flag. */
    paintedPixels: function (id) {
      var node = nodes[id];
      if (!node) { return -1; }
      var list = node.querySelectorAll("canvas");
      var best = null;
      var area = 0;
      for (var i = 0; i < list.length; i += 1) {
        var candidate = list[i];
        if (candidate.width * candidate.height > area) {
          area = candidate.width * candidate.height;
          best = candidate;
        }
      }
      if (!best) { return 0; }
      try {
        var context = best.getContext("2d");
        var data = context.getImageData(0, 0, best.width, best.height).data;
        var count = 0;
        for (var index = 0; index < data.length; index += 4) {
          if (data[index + 3] > 8) { count += 1; }
        }
        return count;
      } catch (error) {
        return -2;
      }
    },

    ready: function () {
      return typeof echarts === "object" && typeof echarts.init === "function";
    },

    diagnostics: function () {
      return JSON.stringify({
        epoch: activeBatch ? activeBatch.epoch : 0,
        waiting: activeBatch ? Object.keys(activeBatch.waiting) : [],
        charts: Object.keys(charts).map(function (id) {
          return {id: id, rendered: charts[id].omniRendered, finished: charts[id].omniFinished};
        }),
        retained: Object.keys(state.series).map(function (name) {
          var rows = state.series[name];
          return {channel: name, count: rows.length, last: rows.length ? rows[rows.length - 1] : null};
        })
      });
    },

    /* Re-measure every chart and repaint from the data already here.
     *
     * Called by Python when the dashboard tab becomes visible again.  The page
     * keeps its DOM across a tab switch, but Chromium suspends layout while it
     * is hidden, so ECharts can still hold the box size from before the switch
     * and draw a frozen-looking curve into a stale canvas (R-318). */
    reflow: function () {
      scheduleLayout();
      return state.widgets.length;
    }
  };

  function rebuild() {
    var canvasNode = document.getElementById("surface");
    if (!canvasNode) { return; }
    Object.keys(charts).forEach(function (key) {
      try { charts[key].dispose(); } catch (error) { /* already gone */ }
      delete charts[key];
    });
    Array.prototype.slice.call(canvasNode.querySelectorAll(".card")).forEach(function (node) {
      canvasNode.removeChild(node);
    });
    nodes = {};
    state.widgets.forEach(mount);
    renderEmpty();
    scheduleLayout();
  }
  function mount(widget) {
    var card = buildCard(widget);
    document.getElementById("surface").appendChild(card);
    nodes[widget.id] = card;
    widget.needsPaint = !inViewport(widget);
    if (!widget.needsPaint) { paint(widget, state.values, state.series, null); }
  }

  function resizeAll() {
    state.widgets.forEach(resizeChart);
  }

  function watchSize() {
    if (typeof ResizeObserver === "function" && canvas) {
      try {
        new ResizeObserver(scheduleLayout).observe(canvas);
      } catch (error) {
        /* An old engine without observer support keeps the window listener. */
      }
    }
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) { scheduleLayout(); }
    });
  }

  function persist() {
    send("layout", {
      widgets: state.widgets.map(function (widget) {
        return {
          id: widget.id, kind: widget.kind, title: widget.title,
          channel: widget.channel, channels: widget.channels, unit: widget.unit,
          min: widget.min, max: widget.max, decimals: widget.decimals,
          onText: widget.onText, offText: widget.offText,
          onColor: widget.onColor, offColor: widget.offColor,
          colorRules: widget.colorRules, fallbackColor: widget.fallbackColor,
          x: widget.x, y: widget.y, w: widget.w, h: widget.h
        };
      })
    });
  }

  // ------------------------------------------------------------------- bridge
  function connect() {
    if (typeof QWebChannel === "undefined" || !window.qt || !window.qt.webChannelTransport) {
      return false;
    }
    new QWebChannel(window.qt.webChannelTransport, function (channel) {
      state.bridge = channel.objects.omni || null;
      if (state.bridge) { send("hello", { tag: "dashboard" }); }
    });
    return true;
  }

  window.omniDashboard = api;
  window.addEventListener("resize", scheduleLayout);
  document.addEventListener("DOMContentLoaded", function () {
    canvas = document.getElementById("canvas");
    if (canvas) { canvas.addEventListener("scroll", scheduleLayout); }
    connect();
    renderEmpty();
    watchSize();
    hookMarquee();
  });
}());
