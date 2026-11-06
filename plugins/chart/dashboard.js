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
    theme: {},
    values: {},             // channel -> latest numeric value
    dirty: false,
    bridge: null,
    nextId: 1
  };

  var charts = {};          // widget id -> echarts instance
  var nodes = {};           // widget id -> DOM node
  var canvas = null;
  var toastTimer = null;

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

  function lineOption(widget, points) {
    var axis = axisStyle();
    var color = cardColor(widget.kind);
    var zoom = zoomState(widget);
    var series = [];
    var channels = widget.channels && widget.channels.length ? widget.channels : [widget.channel];
    for (var index = 0; index < channels.length; index += 1) {
      var data = points[channels[index]] || [];
      series.push({
        name: channels[index] || "-",
        type: "line",
        showSymbol: false,
        smooth: false,
        animation: false,
        lineStyle: { width: 1.4, color: index === 0 ? color : null },
        data: data
      });
    }
    return {
      animation: false,
      backgroundColor: "transparent",
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
        top: 8,
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
      series: series
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

  /* Parts of a card that must keep their own meaning instead of starting a drag. */
  var NO_DRAG = ".del, .resize";

  /* R-170: the two axis sliders live inside the plot, so a press on one of them
   * has to reach ECharts.  Only the strips themselves are excluded - the rest of
   * the curve area still drags the card, which is what R-169 asked for. */
  function inZoomStrip(card, event) {
    var plot = card.querySelector(".plot");
    if (!plot || !plot.contains(event.target)) { return false; }
    var box = plot.getBoundingClientRect();
    var x = event.clientX - box.left;
    var y = event.clientY - box.top;
    return y >= box.height - ZOOM_HIT || x >= box.width - ZOOM_HIT;
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
      select(widget.id);
      beginDrag(event, card, widget, false);
    });
    grip.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) { return; }
      event.preventDefault();
      select(widget.id);
      beginDrag(event, card, widget, true);
    });
  }

  function beginDrag(event, card, widget, resizing) {
    var startX = event.clientX;
    var startY = event.clientY;
    var origin = { x: widget.x, y: widget.y, w: widget.w, h: widget.h };
    card.classList.add("dragging");
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
      } else {
        widget.x = Math.max(0, snap(origin.x + dx));
        widget.y = Math.max(0, snap(origin.y + dy));
      }
      place(card, widget);
      resizeChart(widget);
    }

    function up() {
      card.classList.remove("dragging");
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
    if (chart) { chart.resize(); }
  }

  function select(id) {
    state.selected = id;
    Object.keys(nodes).forEach(function (key) {
      nodes[key].classList.toggle("selected", key === id);
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
    if (state.selected === id) { state.selected = null; }
    renderEmpty();
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
    /* The user's zoom choice has to be captured as it happens: reading it back
     * from the option on the next frame is too late, because ``lineOption()``
     * writes ``start``/``end`` again (R-170). */
    bindZoom(chart, widget);
    return chart;
  }

  function paint(widget, values, history) {
    var node = nodes[widget.id];
    if (!node) { return; }
    var current = values[widget.channel];
    if (widget.kind === "line") {
      var chart = ensureChart(widget);
      if (!chart) { return; }
      /* Merge, never replace: a full replace on every frame tears down the
       * tooltip's hover state, so the crosshair readout would flicker away. */
      chart.setOption(lineOption(widget, history));
      return;
    }
    if (widget.kind === "gauge") {
      var dial = ensureChart(widget);
      if (!dial) { return; }
      var option = gaugeOption(widget);
      option.series[0].data[0].value = Number(current || 0);
      dial.setOption(option);
      return;
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
      if (dot) { dot.className = "lamp-dot" + (on ? " on" : ""); }
      if (text) { text.textContent = on ? (widget.onText || "ON") : (widget.offText || "OFF"); }
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
          x: Number(raw.x) || 0,
          y: Number(raw.y) || 0,
          w: Math.max(MIN_W, Number(raw.w) || 320),
          h: Math.max(MIN_H, Number(raw.h) || 200)
        };
      });
      state.nextId = state.widgets.length + 1;
      rebuild();
      return state.widgets.length;
    },

    /* One frame of data: {channel: value} plus the line history. */
    setValues: function (payload) {
      var data = typeof payload === "string" ? JSON.parse(payload) : payload;
      var values = (data && data.values) || {};
      var history = (data && data.history) || {};
      state.values = values;
      var changed = 0;
      state.widgets.forEach(function (widget) {
        paint(widget, values, history);
        changed += 1;
      });
      return changed;
    },

    /* Called from Python when the theme or language changes. */
    setTheme: function (payload) {
      state.theme = typeof payload === "string" ? JSON.parse(payload) : (payload || {});
      rebuild();
      return true;
    },

    addWidget: function (payload) {
      var raw = typeof payload === "string" ? JSON.parse(payload) : payload;
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
        x: snap(raw.x === undefined ? 32 : Number(raw.x)),
        y: snap(raw.y === undefined ? 32 : Number(raw.y)),
        w: Math.max(MIN_W, Number(raw.w) || 360),
        h: Math.max(MIN_H, Number(raw.h) || 220)
      };
      state.nextId += 1;
      state.widgets.push(widget);
      mount(widget);
      renderEmpty();
      persist();
      return widget.id;
    },

    /* Geometry/binding edit of one widget; the palette sends it. */
    updateWidget: function (payload) {
      var data = typeof payload === "string" ? JSON.parse(payload) : payload;
      var widget = state.widgets.filter(function (item) { return item.id === data.id; })[0];
      if (!widget) { return false; }
      Object.keys(data).forEach(function (key) {
        if (key !== "id") { widget[key] = data[key]; }
      });
      var node = nodes[widget.id];
      if (node) {
        place(node, widget);
        var title = node.querySelector(".title");
        if (title) { title.textContent = widget.title || widget.channel || widget.id; }
      }
      paint(widget, state.values, {});
      persist();
      return true;
    },

    removeWidget: function (id) { removeWidget(id); return true; },

    /* Snapshot for Python-side assertions and for the layout file. */
    describe: function () {
      return JSON.stringify({
        selected: state.selected,
        widgets: state.widgets.map(function (widget) {
          return {
            id: widget.id, kind: widget.kind, title: widget.title,
            channel: widget.channel, channels: widget.channels,
            unit: widget.unit, min: widget.min, max: widget.max,
            decimals: widget.decimals,
            onText: widget.onText, offText: widget.offText,
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
    }
  };

  function rebuild() {
    var canvasNode = document.getElementById("canvas");
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
    resizeAll();
  }

  function mount(widget) {
    var card = buildCard(widget);
    document.getElementById("canvas").appendChild(card);
    nodes[widget.id] = card;
    paint(widget, state.values, {});
  }

  function resizeAll() {
    Object.keys(charts).forEach(function (key) {
      if (charts[key]) { charts[key].resize(); }
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
  window.addEventListener("resize", resizeAll);
  document.addEventListener("DOMContentLoaded", function () {
    canvas = document.getElementById("canvas");
    connect();
    renderEmpty();
  });
}());
