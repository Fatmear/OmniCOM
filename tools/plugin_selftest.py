"""R-146/147/148 regression and optional real chart throughput benchmark."""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import math
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["OMNICOM_DATA_DIR"] = str(ROOT / ".artifacts" / "test-data" / "plugins")
os.environ["OMNICOM_SETTINGS_SUFFIX"] = "-plugin-selftest"

from omnicom.plugin_store import (  # noqa: E402
    Cancelled,
    PluginStore,
    StoreError,
    atomic_json,
    select_version,
    validate_catalog,
)

#: Widget kinds the dashboard must be able to place (mirrors the plugin's
#: own palette order; the panel sorts nothing, so order matters).
KINDS = ("line", "gauge", "value", "bar", "lamp")

CHECKS = []


def check(name, condition) -> None:
    CHECKS.append({"name": name, "ok": bool(condition)})
    if not condition:
        raise AssertionError(name)
    print("PASS", name)


def package(root, version="1.0.0", extra=None):
    path = root / ("demo-" + version + ".zip")
    manifest = {"id": "demo", "version": version, "api": 2, "entry": "plugin.py"}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("demo/plugin.json", json.dumps(manifest))
        archive.writestr("demo/plugin.py", "def create(api): return None\n")
        if extra:
            archive.writestr(*extra)
    return {**manifest, "url": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def store_checks() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        bundle = root / "bundled"
        bundle.mkdir()
        first = package(root)
        latest = package(root, "1.1.0")
        catalog = {"schema": 1, "plugins": [first, {"id": "extra", "version": "1.0.0", "api": 2}]}
        atomic_json(root / "catalog.json", catalog)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/"
        store = PluginStore(root / "data", bundle, base + "catalog.json")
        try:
            entries = store.check()
            check("R-148 HTTP catalog includes uninstalled plugins", len(store.rows(entries)) == 2)
            store.install(entries[0])
            check("R-148 HTTP download and install", store.installed()["demo"]["version"] == "1.0.0")
            saved = store.data / "plugin-data" / "demo" / "settings.ini"
            saved.parent.mkdir(parents=True)
            saved.write_bytes(b"user-data")
            latest["url"] = base + latest["url"]
            row = next(item for item in store.rows([latest]) if item["id"] == "demo")
            check("R-148 installed/latest and update availability", row["update_available"] and row["installed_version"] == "1.0.0" and row["latest_version"] == "1.1.0")
            store.install(latest)
            check("R-148 update retains user settings", store.installed()["demo"]["version"] == "1.1.0" and saved.read_bytes() == b"user-data")

            def reject(item, expected):
                try:
                    store.install(item)
                except StoreError as exc:
                    check("R-148 rejects " + expected, exc.code == expected)
                else:
                    raise AssertionError("unsafe installation accepted: " + expected)
                check("R-148 previous version survives " + expected, store.installed()["demo"]["version"] == "1.1.0")

            reject(dict(latest, sha256="0" * 64), "hash")
            reject(dict(latest, api=999), "api")
            reject(dict(latest, version="9.0.0"), "package")
            traversal = package(root, "2.0.0", ("demo/../../escaped", "bad"))
            traversal["url"] = base + traversal["url"]
            reject(traversal, "path")
            link = zipfile.ZipInfo("demo/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            symbolic = package(root, "3.0.0", (link, "../../escaped"))
            symbolic["url"] = base + symbolic["url"]
            reject(symbolic, "path")
            check("R-148 extraction never escapes staging", not (root / "escaped").exists())
            cancelled = threading.Event()
            cancelled.set()
            try:
                store.install(latest, cancelled)
            except Cancelled:
                check("R-148 cancellation retains installed plugin", store.installed()["demo"]["version"] == "1.1.0")
            else:
                raise AssertionError("cancel ignored")
            real_replace = os.replace

            def fail_disabled(source, destination):
                if Path(destination) == store.disabled_path:
                    raise OSError("simulated metadata commit failure")
                return real_replace(source, destination)

            try:
                with patch("omnicom.plugin_store.os.replace", side_effect=fail_disabled):
                    store.install(entries[0])
            except OSError:
                check("R-148 commit failure restores prior package", store.installed()["demo"]["version"] == "1.1.0")
            else:
                raise AssertionError("commit failure fixture did not execute")
            store.uninstall("demo")
            check("R-148 uninstall preserves plugin data", "demo" not in store.installed() and saved.read_bytes() == b"user-data")
            store.install(entries[0])
            check("R-148 retry reinstalls plugin", "demo" in store.installed())
            check("R-148 offline cache retains catalog", len(store.cached_catalog()) == 2)

            # ------------------------------------------------- R-178 versions
            older = package(root, "1.0.5")
            older["url"] = base + older["url"]
            # ``latest["url"]`` was absolutised earlier in this test, so use it
            # as-is: prefixing again produced a 404 that looked like a bug here.
            history = dict(latest, url=latest["url"],
                           versions=[{"version": "1.1.0", "api": 2, "url": latest["url"],
                                      "sha256": latest["sha256"]},
                                     {"version": "1.0.5", "api": 2, "url": older["url"],
                                      "sha256": older["sha256"]}])
            parsed = validate_catalog({"schema": 1, "plugins": [dict(history)]}, base)[0]
            check("R-178 multi-version catalog parses newest first",
                  [record["version"] for record in parsed["versions"]] == ["1.1.0", "1.0.5"])
            flat = validate_catalog({"schema": 1, "plugins": [{"id": "solo", "version": "2.0.0",
                                                             "api": 1}]}, base)[0]
            check("R-178 a flat entry still yields one version",
                  [record["version"] for record in flat["versions"]] == ["2.0.0"])
            for label, broken in (
                ("a head that is not the newest version",
                 dict(history, version="1.0.5")),
                ("an older record without a package url",
                 dict(history, versions=[{"version": "1.1.0", "api": 2, "sha256": latest["sha256"]},
                                         {"version": "1.0.5", "api": 2, "sha256": older["sha256"]}])),
                ("duplicate version records",
                 dict(history, versions=[{"version": "1.1.0", "api": 2, "url": base + latest["url"],
                                          "sha256": latest["sha256"]}] * 2)),
            ):
                try:
                    validate_catalog({"schema": 1, "plugins": [broken]}, base)
                except StoreError as exc:
                    check("R-178 rejects " + label, exc.code == "catalog")
                else:
                    raise AssertionError("inconsistent history accepted: " + label)
            chosen = select_version(parsed, "1.0.5")
            check("R-178 select_version picks the requested build",
                  chosen["version"] == "1.0.5" and chosen["sha256"] == older["sha256"])
            try:
                select_version(parsed, "0.0.1")
            except StoreError as exc:
                check("R-178 select_version refuses an unknown version", exc.code == "package")
            else:
                raise AssertionError("unknown version accepted")
            try:
                select_version(
                    {"id": "demo", "version": "1.1.0",
                     "versions": [{"version": "1.1.0", "api": 2},
                                  {"version": "0.9.0", "api": 2, "local": True}]}, "0.9.0")
            except StoreError as exc:
                check("R-178 a local-only build offers nothing to download", exc.code == "package")
            else:
                raise AssertionError("local build accepted for install")
            rows = store.rows([parsed])
            check("R-178 the picker history carries every catalog version",
                  [record["version"] for record in rows[0]["versions"]][:2] == ["1.1.0", "1.0.5"]
                  and rows[0]["versions"][-1].get("local"))
            store.install(select_version(parsed, "1.1.0"))
            rows = store.rows([parsed])
            check("R-178 an installed newest build still offers an older one",
                  rows[0]["installed_version"] == "1.1.0" and rows[0]["installed_downgradable"])
            store.install(select_version(parsed, "1.0.5"))
            check("R-178 downgrade install reaches the older build "
                  f"({store.installed()['demo']['version']})",
                  store.installed()["demo"]["version"] == "1.0.5")
            check("R-178 downgrade keeps user data", saved.read_bytes() == b"user-data")
            upgraded = store.rows(validate_catalog({"schema": 1, "plugins": [
                dict(history)]}, base))
            check("R-178 after a downgrade an update is offered again",
                  upgraded[0]["installed_version"] == "1.0.5"
                  and upgraded[0]["update_available"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def chart_checks(app, kinds):
    """R-147 protocol and R-161 dashboard behaviour, every number printed."""
    from omnicom import webengine
    from omnicom.plugins import PluginHost

    check(f"R-161 QtWebEngine package present (available={webengine.available()})",
          webengine.available())

    sent = []
    notes = []
    # Seed the *stored* pause state before the plugin is loaded, so the
    # "fresh dashboard is already running" check below is a real regression
    # test: the build that broke R-176 read ``running=0`` here and opened on a
    # frozen picture with a grey 继续 button (L-240).
    from omnicom import paths
    seeded = os.path.join(paths.data_dir(), "plugin-data", "chart", "settings.ini")
    os.makedirs(os.path.dirname(seeded), exist_ok=True)
    with open(seeded, "w", encoding="utf-8") as handle:
        handle.write("[General]\nrunning=0\n")
    host = PluginHost(notify=lambda text, level="info": notes.append((text, level)),
                      log_line=lambda text: None,
                      send_bytes=lambda data: sent.append(bytes(data)) or True)
    host.load()
    info = next(item for item in host.loaded() if item.id == "chart")
    chart = info.widget
    chart_mod = info.module           # the plugin module: constants + parser
    chart.resize(1050, 650)
    chart.show()
    parser = info.module.AsciiParser()
    check("R-147 split and coalesced frames",
          parser.feed(b"[OmniCOM-cha") == []
          and parser.feed(b"rt] AA=11 BB=1.1\r\n[OmniCOM-chart] CC=-3e-4\n")
          == [{"AA": 11, "BB": 1.1}, {"CC": -0.0003}])
    # The device prints the payload through its own log macro, so the real line
    # is decorated in front of the marker.  Requiring the marker at column 0
    # discarded every frame from real firmware (L-192).
    decorated = (b"csi_demo:I(266466):[OmniCOM-chart] T1=2.07 NEEDE=10.00\r\n"
                 b"csi_demo:I(266466):[CSI-DBG] result=NOBODY src=t1 raw=1778\r\n")
    check("R-147 frame parses behind a firmware prefix",
          parser.feed(decorated) == [{"T1": 2.07, "NEEDE": 10.0}])
    parser.feed(b"[OmniCOM-chart] AA=nan\n[OmniCOM-chart] AA=1 AA=2\n"
                b"[OmniCOM-chart] AA=1e999\n")
    check("R-147 invalid and nonfinite frames counted", parser.invalid == 3)
    parser.feed(b"[OmniCOM-chart] " + b"A" * 70000)
    recovery = parser.feed(b"discarded\n[OmniCOM-chart] NEW=4\n")
    check("R-147 overlong input bounded and recovers",
          parser.oversized == 1 and not parser.pending and recovery == [{"NEW": 4}])

    # ---------------------------------------------------------------- channels
    host.publish_rx(1000, b"[OmniCOM-chart] AA=11 BB=1.1\n")
    host.publish_rx(1100, b"[OmniCOM-chart] AA=12 CC=3\n")
    chart.refresh()
    app.processEvents()
    check(f"R-147 dynamic channel discovery reaches the palette "
          f"({sorted(chart.samples)})",
          sorted(chart.samples) == ["AA", "BB", "CC"]
          and chart.channel_combo.count() == 3)

    # --------------------------------------------------------------- the page
    deadline = time.perf_counter() + 30
    while not chart.page_ready and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.02)
    check(f"R-161 dashboard page loads inside QtWebEngine "
          f"(ready={chart.page_ready}, error={chart.surface_error})",
          chart.page_ready)
    js_ready = _js(app, chart, "omniDashboard.ready()")
    check(f"R-161 bundled ECharts runtime is usable offline (ready={js_ready})",
          js_ready is True)

    ids = _add_all_kinds(chart, kinds)
    _wait(app, chart, len(kinds))
    described = _describe(app, chart)
    check(f"R-161 every widget kind is mounted "
          f"({[(w['kind'], w['id']) for w in described['widgets']]})",
          [w["id"] for w in described["widgets"]] == ids
          and {w["kind"] for w in described["widgets"]} == set(kinds))
    check(f"R-161 only line and gauge create ECharts instances "
          f"(charts={described['charts']})", described["charts"] == 2)
    zoom_config = _js(app, chart, "JSON.stringify(echarts.getInstanceByDom("
                      "document.querySelector('.card[data-kind=line] .plot'))"
                      ".getOption().dataZoom || [])")
    zooms = json.loads(zoom_config)
    check("R-170 curve exposes independent horizontal and vertical zoom controls",
          len(zooms) == 2 and any(item.get("xAxisIndex") == [0] for item in zooms)
          and any(item.get("yAxisIndex") == [0] for item in zooms))
    # Pick a range on each axis, then let a data frame repaint the card: the
    # option is rewritten ~30 times a second, and an option that carries
    # ``start``/``end`` resets them on every frame unless the range is owned by
    # the page (R-170).
    applied = _js(app, chart, """(function () {
      var host = document.querySelector('.card[data-kind=line] .plot');
      var instance = echarts.getInstanceByDom(host);
      if (!instance) { return 'no-instance'; }
      instance.dispatchAction({ type: 'dataZoom', dataZoomIndex: 0,
                                start: 20, end: 60 });
      instance.dispatchAction({ type: 'dataZoom', dataZoomIndex: 1,
                                start: 30, end: 70 });
      return 'ok';
    })()""")
    _js(app, chart, "omniDashboard.setValues({values:{}, history:{}})")
    kept = json.loads(_js(app, chart, "JSON.stringify(echarts.getInstanceByDom("
                          "document.querySelector('.card[data-kind=line] .plot'))"
                          ".getOption().dataZoom.map("
                          "z => [z.xAxisIndex ? 'x' : 'y', z.start, z.end]))") or "[]")
    ranges = {item[0]: (item[1], item[2]) for item in kept}
    check(f"R-170 each axis keeps the range the user chose across a refresh "
          f"({applied}, {kept})",
          applied == "ok" and len(ranges) == 2
          and abs(ranges.get("x", (0, 100))[0] - 20) < 1.5
          and abs(ranges.get("x", (0, 100))[1] - 60) < 1.5
          and abs(ranges.get("y", (0, 100))[0] - 30) < 1.5
          and abs(ranges.get("y", (0, 100))[1] - 70) < 1.5)
    # Pressing on a zoom strip must zoom, not start a card drag: the sliders sit
    # inside the plot, which is part of the drag handle since R-169.  The press
    # is dispatched on the renderer canvas, which is what a real mouse hits.
    strip = _js(app, chart, """(function () {
      var card = document.querySelector('.card[data-kind=line]');
      var plot = card.querySelector('.plot');
      var target = plot.querySelector('canvas') || plot;
      var box = plot.getBoundingClientRect();
      var init = { bubbles: true, cancelable: true, button: 0, pointerId: 7,
                   clientX: box.left + 24, clientY: box.top + box.height - 4,
                   pointerType: 'mouse', isPrimary: true };
      target.dispatchEvent(new PointerEvent('pointerdown', init));
      var dragging = card.classList.contains('dragging');
      target.dispatchEvent(new PointerEvent('pointercancel', init));
      card.classList.remove('dragging');
      return JSON.stringify({ dragging: dragging,
                              onCanvas: target.tagName.toLowerCase(),
                              height: Math.round(box.height) });
    })()""")
    pressed = json.loads(strip or "{}")
    check(f"R-170 a press on an axis slider does not drag the card ({strip})",
          pressed.get("dragging") is False and pressed.get("onCanvas") == "canvas")
    # The sliders are positioned relative to the card body, not to the grid, so
    # the grid has to be inset by hand - otherwise the x slider sits on top of
    # the axis labels.  Measure the plot rect instead of trusting the numbers.
    layout = json.loads(_js(app, chart, """(function () {
      var host = document.querySelector('.card[data-kind=line] .plot');
      var instance = echarts.getInstanceByDom(host);
      var rect = instance.getModel().getComponent('grid').coordinateSystem.getRect();
      return JSON.stringify({ plotBottom: Math.round(rect.y + rect.height),
                              plotRight: Math.round(rect.x + rect.width),
                              height: host.clientHeight, width: host.clientWidth });
    })()""") or "{}")
    check(f"R-170 the axis sliders do not cover the plot itself ({layout})",
          layout.get("plotBottom", 1e9) <= layout.get("height", 0) - 14
          and layout.get("plotRight", 1e9) <= layout.get("width", 0) - 12)
    # Ctrl+wheel must not scale the whole dashboard any more (R-171).  Chromium
    # owns that gesture below the page, so the proof is the Qt-side guard: the
    # counter grows *and* the view keeps zoom factor 1.0.
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    before_blocked = chart.wheel_zoom_blocked
    target = chart.view.focusProxy() or chart.view
    centre = QPointF(target.width() / 2.0, target.height() / 2.0)
    for _ in range(2):
        app.sendEvent(target, QWheelEvent(
            centre, target.mapToGlobal(QPoint(int(centre.x()), int(centre.y()))),
            QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.ControlModifier,
            Qt.NoScrollPhase, False))
    app.processEvents()
    zoom_factor = round(float(chart.view.zoomFactor()), 4)
    check(f"R-171 Ctrl+wheel no longer zooms the whole page "
          f"(blocked={chart.wheel_zoom_blocked - before_blocked}, "
          f"factor={zoom_factor})",
          chart.wheel_zoom_blocked - before_blocked == 2 and zoom_factor == 1.0)
    # A plain wheel must still scroll the dashboard: the guard watches the
    # modifier, not the widget, so an unmodified notch has to pass through.
    steady = chart.wheel_zoom_blocked
    app.sendEvent(target, QWheelEvent(
        centre, target.mapToGlobal(QPoint(int(centre.x()), int(centre.y()))),
        QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
        Qt.NoScrollPhase, False))
    app.processEvents()
    check(f"R-171 a plain wheel still scrolls (blocked={chart.wheel_zoom_blocked})",
          chart.wheel_zoom_blocked == steady)

    # Real pixels, not a flag: the two chart kinds must have drawn something.
    for widget_id, kind in zip(ids, kinds, strict=True):
        if kind not in ("line", "gauge"):
            continue
        pixels = _painted(app, chart, widget_id, floor=100)
        check(f"R-161 {kind} widget paints real pixels (painted={pixels})",
              pixels > 100)

    # ------------------------------------------------------- drag persistence
    before = {w["id"]: [w["x"], w["y"], w["w"], w["h"]] for w in described["widgets"]}
    # R-173: the 16 px grid belongs to the canvas itself, so it is painted from
    # the moment the page mounts.  There is deliberately no edit switch any
    # more - dragging and the × always work - so the page must not even expose
    # ``setEditing``.
    grid = json.loads(_js(app, chart, """(function () {
      var cs = getComputedStyle(document.getElementById('canvas'));
      return JSON.stringify({ image: cs.backgroundImage,
                              size: cs.backgroundSize });
    })()""") or "{}")
    # ``background-size`` reports one value per painted layer, and the grid is
    # drawn by two gradients (vertical + horizontal), so the computed value is
    # "16px 16px, 16px 16px" - comparing against a single "16px 16px" asserted
    # the *layer count*, not the step (L-235).
    grid_size = [part.strip() for part in str(grid.get("size")).split(",")]
    check(f"R-173 the snap grid is always painted (size={grid.get('size')})",
          bool(grid_size) and all(part == "16px 16px" for part in grid_size)
          and "linear-gradient" in str(grid.get("image")))
    check("R-173 there is no edit mode left to toggle",
          _js(app, chart, "typeof omniDashboard.setEditing") == "undefined")
    first = ids[0]
    _js(app, chart, "omniDashboard.updateWidget("
                    + json.dumps({"id": first, "x": 128, "y": 96,
                                  "w": 512, "h": 256}) + ")")
    moved = None
    deadline = time.perf_counter() + 10
    while time.perf_counter() < deadline:
        app.processEvents()
        moved = {w["id"]: [w["x"], w["y"], w["w"], w["h"]]
                 for w in _describe(app, chart)["widgets"]}
        if moved.get(first) == [128, 96, 512, 256]:
            break
        time.sleep(0.02)
    check(f"R-161 geometry change is reported back "
          f"({before[first]} -> {moved.get(first)})",
          moved is not None and moved.get(first) == [128, 96, 512, 256])
    persisted = json.loads(str(chart.api.settings.value("layout", "{}")))
    check(f"R-161 layout persists to the plugin INI "
          f"(widgets={len(persisted.get('widgets') or [])})",
          len(persisted.get("widgets") or []) == len(kinds))

    # ------------------------------------------ R-169 a real drag always works
    # ``updateWidget`` above only proves the *API* moves a card.  The user's
    # complaint was that a press-and-drag on the card did nothing, so drive the
    # actual Qt mouse sequence through the real widget and require the geometry
    # to change - there is no edit mode at all any more (R-173), so dragging
    # must never be gated on a switch.
    # The probe installs its own one-card layout, so the real one is parked here
    # and pushed back afterwards: ``persist()`` would otherwise overwrite the
    # five widgets the following checks still need.
    parked_layout = chart.layout_data
    area = chart.view.focusProxy() or chart.view
    probe = {"id": "dragprobe", "kind": "value", "channel": "AA", "title": "drag",
             "x": 16, "y": 16, "w": 208, "h": 128}
    _js(app, chart, "omniDashboard.setLayout(" + json.dumps({"widgets": [probe]}) + ")")
    _wait(app, chart, 1)
    start_geometry = _geometry_of(app, chart, "dragprobe")

    def _mouse(kind, x, y):
        """Send one real Qt mouse event to the page surface.

        ``button`` and ``buttons`` are *not* the same thing: a release carries
        ``button == LeftButton`` with ``buttons == NoButton``.  Passing
        ``NoButton`` for both makes Chromium drop the ``pointerup`` entirely, so
        the card visibly moves but the drag never ends, ``persist()`` is never
        called and nothing is stored - which looks exactly like "the drag does
        nothing" (L-235).
        """
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        point = QPointF(float(x), float(y))
        action, button, buttons = {
            "press": (QEvent.MouseButtonPress, Qt.LeftButton, Qt.LeftButton),
            "move": (QEvent.MouseMove, Qt.NoButton, Qt.LeftButton),
            "release": (QEvent.MouseButtonRelease, Qt.LeftButton, Qt.NoButton),
        }[kind]
        app.sendEvent(area, QMouseEvent(action, point, point, point,
                                        button, buttons, Qt.NoModifier))
        app.processEvents()

    _mouse("press", 90, 100)            # the card *body*, not the header
    for step in range(1, 7):
        _mouse("move", 90 + step * 16, 100 + step * 16)
        time.sleep(0.02)
    _mouse("release", 186, 196)
    app.processEvents()
    deadline = time.perf_counter() + 10
    dragged = start_geometry
    while time.perf_counter() < deadline:
        app.processEvents()
        dragged = _geometry_of(app, chart, "dragprobe")
        if dragged and start_geometry and dragged[:2] != start_geometry[:2]:
            break
        time.sleep(0.02)
    check(f"R-169 a mouse drag moves the card with no edit mode at all "
          f"({start_geometry} -> {dragged})",
          bool(dragged) and start_geometry is not None
          and dragged[:2] != start_geometry[:2])
    check(f"R-169 the drag keeps the card's size "
          f"({start_geometry} -> {dragged})",
          bool(dragged) and start_geometry is not None
          and dragged[2:] == start_geometry[2:])
    # ``persist()`` goes through QWebChannel, which is asynchronous: poll the INI
    # instead of reading it on the next line.
    deadline = time.perf_counter() + 10
    stored = []
    while time.perf_counter() < deadline:
        app.processEvents()
        saved = json.loads(str(chart.api.settings.value("layout", "{}")))
        stored = [(w.get("id"), w.get("x"), w.get("y")) for w in saved.get("widgets") or []]
        if stored and stored[0][0] == "dragprobe" and stored[0][1] != 16:
            break
        time.sleep(0.02)
    check(f"R-169 the drag is reported back through the bridge and stored "
          f"({stored})",
          bool(stored) and stored[0][0] == "dragprobe" and stored[0][1] != 16)
    cursor = _js(app, chart, "getComputedStyle("
                 "document.querySelector('.card')).cursor")
    check(f"R-169 the card advertises that it can be dragged (cursor={cursor})",
          cursor == "move")
    chart.layout_data = parked_layout
    chart._save_layout()
    _js(app, chart, "omniDashboard.setLayout("
        + json.dumps(parked_layout, ensure_ascii=False) + ")")
    _wait(app, chart, len(kinds))

    # ------------------------------------------------- R-165 × really deletes
    # The × sits inside the card header, and the header starts a drag on
    # pointerdown *and captures the pointer*, so a plain click listener is not
    # proof: drive the real DOM sequence (pointerdown -> pointerup -> click) on
    # the button and prove the widget disappears and the layout shrinks.
    # The victim is the ``value`` card, not ``ids[-1]``: the lamp (R-166) and
    # the bar (R-172) are measured further down and must survive this check.
    victim = ids[kinds.index("value")]
    before_count = len(_describe(app, chart)["widgets"])
    fired = _js(app, chart, """(function () {
      var card = document.querySelector('.card[data-id="' + __ID__ + '"]');
      if (!card) { return 'no-card'; }
      var button = card.querySelector('.del');
      if (!button) { return 'no-button'; }
      var box = button.getBoundingClientRect();
      var init = { bubbles: true, cancelable: true, button: 0, pointerId: 1,
                   clientX: box.left + box.width / 2,
                   clientY: box.top + box.height / 2,
                   pointerType: 'mouse', isPrimary: true };
      button.dispatchEvent(new PointerEvent('pointerdown', init));
      button.dispatchEvent(new PointerEvent('pointerup', init));
      button.dispatchEvent(new MouseEvent('click', init));
      return 'clicked';
    })()""".replace("__ID__", json.dumps(victim)))
    deadline = time.perf_counter() + 10
    after_ids = [w["id"] for w in _describe(app, chart)["widgets"]]
    while time.perf_counter() < deadline and victim in after_ids:
        app.processEvents()
        time.sleep(0.02)
        after_ids = [w["id"] for w in _describe(app, chart)["widgets"]]
    check(f"R-165 the × button removes its widget ({fired})",
          fired == "clicked" and victim not in after_ids
          and len(after_ids) == before_count - 1)
    # The page persists through QWebChannel, which is asynchronous: poll the INI
    # instead of reading it on the next line, or the check races the bridge.
    saved_ids = []
    deadline = time.perf_counter() + 10
    while time.perf_counter() < deadline:
        app.processEvents()
        saved = json.loads(str(chart.api.settings.value("layout", "{}")))
        saved_ids = [w.get("id") for w in (saved.get("widgets") or [])]
        if victim not in saved_ids:
            break
        time.sleep(0.02)
    check(f"R-165 the deletion is persisted (saved={len(saved_ids)})",
          victim not in saved_ids)

    # ------------------------------------------------- R-166 lamp geometry
    # ``runJavaScript`` only marshals primitives back reliably, so the page
    # returns a JSON string here; a bare object arrives as ``None``.
    lamp_id = next((widget["id"] for widget in chart.layout_data["widgets"]
                    if widget.get("kind") == "lamp"), None)
    measured = None
    if lamp_id:
        raw = _js(app, chart, """(function () {
          var card = document.querySelector('.card[data-id="' + __ID__ + '"]');
          var dot = card && card.querySelector('.lamp-dot');
          if (!dot) { return ''; }
          var cs = getComputedStyle(dot);
          return JSON.stringify({ w: parseFloat(cs.width),
                                  h: parseFloat(cs.height),
                                  radius: cs.borderRadius });
        })()""".replace("__ID__", json.dumps(lamp_id)))
        measured = json.loads(raw) if raw else None
    check(f"R-166 the lamp indicator is twice the old size ({measured})",
          bool(measured) and measured["w"] >= 44 and measured["h"] >= 44)
    check("R-166 the enlarged indicator stays round",
          bool(measured) and "50%" in str(measured["radius"]))

    # ------------------------------------------------- R-172 progress bar card
    # The bar's whole job is to turn a number into a proportion, so the proof
    # has to be the *measured* pixel width of the fill, not a flag: publish
    # 7.5 on a 0..10 range and require the fill to cover three quarters of the
    # track.  ``formatValue`` is exercised by the readout text in the same
    # snapshot, so a bar that draws correctly but prints the wrong number fails.
    bar_id = next((widget["id"] for widget in chart.layout_data["widgets"]
                   if widget.get("kind") == "bar"), None)

    def _bar():
        """Measured state of the progress-bar card, or ``None``."""
        if not bar_id:
            return None
        raw = _js(app, chart, """(function () {
          var card = document.querySelector('.card[data-id="' + __ID__ + '"]');
          var fill = card && card.querySelector('.bar-fill');
          var track = card && card.querySelector('.bar-track');
          var number = card && card.querySelector('.bar-number');
          if (!fill || !track) { return ''; }
          var cs = getComputedStyle(fill);
          var box = track.getBoundingClientRect();
          return JSON.stringify({
            track: box.width,
            fill: parseFloat(cs.width),
            inline: fill.style.width,
            over: fill.classList.contains('over'),
            text: number ? number.textContent : '',
            scale: (card.querySelector('.bar-scale') || {}).textContent || ''
          });
        })()""".replace("__ID__", json.dumps(bar_id)))
        return json.loads(raw) if raw else None

    def _publish_bar(value):
        """Push one frame and wait for the 120 ms width transition to settle.

        The value travels through ``chart.values`` and ``chart.flush()``, not a
        bare ``omniDashboard.setValues()`` call: the panel repaints from its own
        state every ``FRAME_MS``, so a direct page call would be overwritten by
        the very next timer tick and the measurement would race the repaint.
        """
        if value is None:
            chart.values.pop("AA", None)
        else:
            chart.values["AA"] = float(value)
        chart.flush()
        deadline = time.perf_counter() + 5
        while time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.02)
            state = _bar()
            if state and state["track"] > 0:
                expected = None if value is None else max(
                    0.0, min(1.0, (value - 0.0) / (10.0 - 0.0)))
                want = 0.0 if expected is None else expected * state["track"]
                if abs(state["fill"] - want) <= max(2.0, state["track"] * 0.015):
                    return state
        return _bar()

    chart.live.setChecked(True)
    mounted = _bar()
    check(f"R-172 the progress bar card builds a track and a fill ({mounted})",
          bool(mounted) and mounted["track"] > 0 and mounted["inline"] != "")
    check(f"R-172 an unbound channel shows an empty bar and '--' ({mounted})",
          bool(mounted) and mounted["text"] == "--"
          and abs(mounted["fill"]) < 1.0)
    half = _publish_bar(5.0)
    check(f"R-172 half of the range fills half of the track ({half})",
          bool(half) and half["track"] > 0
          and abs(half["fill"] / half["track"] - 0.5) <= 0.02)
    full = _publish_bar(7.5)
    check(f"R-172 the fill is proportional to the reading "
          f"(7.5 of 0..10 -> {full})",
          bool(full) and full["track"] > 0
          and abs(full["fill"] / full["track"] - 0.75) <= 0.02)
    check(f"R-172 the readout and the range captions are formatted "
          f"(text={full.get('text') if full else None}, "
          f"scale={full.get('scale') if full else None})",
          bool(full) and full["text"] == "7.50" and full["scale"].startswith("0.00"))
    under = _publish_bar(-4.0)
    check(f"R-172 a reading below the minimum clamps to an empty bar ({under})",
          bool(under) and abs(under["fill"]) < 1.0 and under["text"] == "-4.00")
    over = _publish_bar(13.0)
    check(f"R-172 a reading above the maximum clamps but is flagged red ({over})",
          bool(over) and over["track"] > 0
          and abs(over["fill"] / over["track"] - 1.0) <= 0.02
          and over["over"] is True and over["text"] == "13.00")
    check(f"R-172 the bar needs no ECharts instance ({_bar()})",
          _describe(app, chart)["charts"] == 2)
    chart.clear_data()
    chart.refresh()

    # ------------------------------------------------- R-167/R-176 run button
    # The dashboard refreshes from the moment it opens, so the accent 暂停 face
    # is what a fresh window shows; a check that asserted "idle means Ghost"
    # would now be asserting the bug (R-176, re-based here in the same edit).
    app.processEvents()
    default_text, default_object = chart.live.text(), chart.live.objectName()
    check(f"R-176 a fresh dashboard is already running "
          f"({default_text!r}/{default_object!r}/{chart.live.isChecked()})",
          chart.live.isChecked() and default_object == "Primary")
    check(f"R-176 the running face is 暂停 ({default_text!r})",
          default_text == "暂停")

    chart.live.setChecked(False)
    app.processEvents()
    idle_text, idle_object = chart.live.text(), chart.live.objectName()
    check(f"R-176 paused face is 继续 in the neutral style "
          f"({idle_text!r}/{idle_object!r})",
          idle_object == "Ghost" and idle_text == "继续")
    check(f"R-176 pausing and resuming swap the face ({default_text!r}/{idle_text!r})",
          idle_text != default_text)

    chart.live.setChecked(True)
    app.processEvents()
    check("R-176 resuming returns to the accent 暂停 face",
          chart.live.objectName() == "Primary" and chart.live.text() == "暂停")
    check("R-167 the two run button faces stay visually distinct",
          chart.live.text() != idle_text)

    # A stale ``running=0`` in settings.ini is exactly what made a fresh window
    # open frozen (R-176, revised): the old build restored it, so "default" was
    # whatever the last session left behind.  The seed above proved the load
    # path ignores it; saving must also drop the key (L-240).
    run_settings = chart.api.settings
    check(f"R-176 a stored running=0 is not restored (checked="
          f"{chart.live.isChecked()}, group={run_settings.group()!r})",
          chart.live.isChecked())
    run_settings.setValue("running", "0")
    chart._save_ui()
    run_settings.sync()
    check(f"R-176 saving drops the stale running key "
          f"(keys={run_settings.allKeys()})",
          "running" not in run_settings.allKeys())

    # --------------------------------------------------------------- run/stop
    chart.live.setChecked(False)
    count = chart.render_count
    host.publish_rx(1200, b"[OmniCOM-chart] AA=13\n")
    chart.refresh()
    check("R-147 stop freezes rendering only",
          chart.render_count == count and chart.samples["AA"][-1][1] == 13)
    chart.live.setChecked(True)
    chart.refresh()
    check("R-147 run resumes rendering retained samples",
          chart.render_count > count)

    # ------------------------------------------------------------------ limits
    chart.clear_data()
    chart.clear_widgets()
    app.processEvents()
    host.publish_rx(1300, b"[OmniCOM-chart] AA=1\n" * 5100)
    check(f"R-147 history cap is bounded and counted "
          f"(points={len(chart.samples['AA'])}, evicted={chart.evicted})",
          len(chart.samples["AA"]) == 5000 and chart.evicted > 0)

    # ------------------------------------------------- R-164 history depth
    # Verify both the retained history and the data reaching the page.
    check(f"R-164 history keeps at least 5000 frames "
          f"(MAX_POINTS={chart_mod.MAX_POINTS}, held={len(chart.samples['AA'])})",
          chart_mod.MAX_POINTS >= 5000
            and len(chart.samples["AA"]) == chart_mod.MAX_POINTS
            and chart_mod.DEFAULT_WINDOW_S == 0)
    oldest = chart.samples["AA"][0][1]
    newest = chart.samples["AA"][-1][1]
    host.publish_rx(1301, b"[OmniCOM-chart] AA=777\n")
    check(f"R-164 the newest sample is the one kept at the cap "
          f"(newest={chart.samples['AA'][-1][1]:.0f}, "
          f"oldest_in_ring={oldest:.0f}, before={newest:.0f})",
          chart.samples["AA"][-1][1] == 777 and newest == 1)
    # One line widget on screen: it must receive the full history, not a
    # down-sampled subset (that thinning is what lost the detail).
    chart.channel_combo.clear()
    chart.channel_combo.addItem("AA")
    chart.channel_combo.setCurrentText("AA")
    chart.kind_combo.setCurrentIndex(chart.kind_combo.findData("line"))
    chart.title_edit.setText("history-line")
    chart.unit_edit.setText("V")
    chart.min_edit.setText("0")
    chart.max_edit.setText("10")
    line_id = chart.add_widget_from_palette()
    app.processEvents()
    history = chart.history_for_page().get("AA") or []
    check(f"R-164 a single visible curve receives the whole history (pushed={len(history)})",
          len(history) >= 5000)
    check(f"R-164 the pushed history ends on the newest frame "
          f"(last={history[-1] if history else None})",
          bool(history) and history[-1][1] == 777)
    chart.clear_data()
    chart.layout_data["widgets"] = [
        {"id": "history-multi", "kind": "line", "channels": ["AA", "BB"]}]
    chart.seconds.setValue(0)
    for index in range(5000):
        host.publish_rx(2000 + index * 1000,
                        f"[OmniCOM-chart] AA={index} BB={-index}\n".encode())
    history = chart.history_for_page()
    check("R-164 all history shows 5000 distinct timestamps per channel",
          all(len(history.get(name, [])) == 5000
              and history[name][0][0] == 0
              and history[name][-1][0] == 4999 for name in ("AA", "BB")))
    multi_layout = {"widgets": [{"id": "history-multi", "kind": "line",
                                 "channels": ["AA", "BB"]}]}
    _js(app, chart, "omniDashboard.setLayout(" + json.dumps(multi_layout) + ")")
    chart.layout_data = multi_layout
    started = time.perf_counter()
    chart.flush()
    flush_ms = (time.perf_counter() - started) * 1000
    rendered = _js(app, chart, "JSON.stringify(echarts.getInstanceByDom("
                   "document.querySelector('.plot')).getOption().series.map("
                   "s => [s.data.length, s.data[0], s.data[s.data.length-1]]))")
    series = json.loads(rendered)
    check(f"R-164 ECharts receives both full histories (flush={flush_ms:.1f} ms, series={series})",
          len(series) == 2 and all(item[0] == 5000 and item[1][0] == 0
                                   and item[2][0] == 4999 for item in series))
    chart.seconds.setValue(10)
    history = chart.history_for_page()
    check("R-164 selected time window still filters history",
          all(len(history.get(name, [])) == 11 for name in ("AA", "BB")))
    settings = chart.api.settings
    settings.remove("history_window_migrated")
    settings.setValue("window", "10")
    check("R-164 legacy 10 second window migrates to all",
          chart._initial_window() == 0 and str(settings.value("window")) == "0")
    settings.setValue("window", "10")
    check("R-164 an explicitly selected 10 second window remains after migration",
          chart._initial_window() == 10)
    settings.remove("history_window_migrated")
    settings.setValue("window", "60")
    check("R-164 other existing time windows are preserved",
          chart._initial_window() == 60)
    if line_id:
        chart.layout_data["widgets"] = [widget for widget
                                        in chart.layout_data["widgets"]
                                        if widget.get("id") != line_id]
    chart.clear_data()
    chart.clear_widgets()
    app.processEvents()
    for index in range(130):
        host.publish_rx(1400, f"[OmniCOM-chart] CHANNEL_{index}=1\n".encode())
    check(f"R-147 channel cap is visible and counted "
          f"(channels={len(chart.samples)}, refused={chart.refused})",
          len(chart.samples) == 128 and chart.refused > 0)

    # ------------------------------------------------------------------ export
    chart.clear_data()
    for index in range(250):
        host.publish_rx(1000 + index * 10,
                        f"[OmniCOM-chart] AA={math.sin(index / 15):.4f} "
                        f"BB={math.cos(index / 20):.4f}\n".encode())
    chart.refresh()
    app.processEvents()
    host.apply_theme()
    with tempfile.TemporaryDirectory() as temporary:
        export = Path(temporary) / "chart.csv"
        chart.export_csv(str(export))
        lines = export.read_text(encoding="utf-8-sig").splitlines()
        check(f"R-147 CSV exports retained values (rows={len(lines)})",
              len(lines) == 501)
        shot = Path(temporary) / "dashboard.png"
        chart.save_image(str(shot))
        size = shot.stat().st_size if shot.exists() else 0
        check(f"R-161 dashboard image export ({size} bytes)", size > 1000)
    chart.resize(1050, 650)
    app.processEvents()
    chart.grab().save(str(ROOT / ".artifacts" / "chart.png"))
    chart_scope_checks(app, chart, kinds)
    return host, chart, info.module.AsciiParser


def _js(app, chart, code):
    """Run JavaScript in the dashboard page synchronously and return its value."""
    from PySide6.QtCore import QEventLoop, QTimer

    if chart.view is None or not chart.page_ready:
        return None
    result = {"value": None}
    loop = QEventLoop()

    def done(value):
        result["value"] = value
        loop.quit()

    chart.view.page().runJavaScript(code, 0, done)
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(5000)
    loop.exec()
    guard.stop()
    app.processEvents()
    return result["value"]


def _describe(app, chart) -> dict:
    raw = _js(app, chart, "omniDashboard.describe()")
    if not raw:
        return {"selected": None, "widgets": [], "charts": 0}
    return json.loads(raw)


def _painted(app, chart, widget_id, floor=100, tries=60) -> int:
    """Poll ``paintedPixels`` until the widget drew something (or we give up).

    ``runJavaScript`` hands every JS number back as a Python ``float``, so the
    comparison must never assume ``int`` -- ``-1`` (no node) and ``-2``
    (``getImageData`` refused) are the only negative answers.
    """
    pixels = -1.0
    for _ in range(tries):
        app.processEvents()
        value = _js(app, chart, "omniDashboard.paintedPixels("
                    + json.dumps(widget_id) + ")")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            pixels = float(value)
            if pixels > floor:
                break
        time.sleep(0.05)
    return int(pixels)


def _wait(app, chart, count) -> None:
    deadline = time.perf_counter() + 15
    while time.perf_counter() < deadline:
        app.processEvents()
        if len(_describe(app, chart)["widgets"]) >= count:
            return
        time.sleep(0.02)


def _geometry_of(app, chart, widget_id):
    """``[x, y, w, h]`` of one card as the page currently has it, or ``None``."""
    for widget in _describe(app, chart)["widgets"]:
        if widget.get("id") == widget_id:
            return [int(widget.get("x") or 0), int(widget.get("y") or 0),
                    int(widget.get("w") or 0), int(widget.get("h") or 0)]
    return None


def _add_all_kinds(chart, kinds) -> list:
    """Add one widget of every kind through the panel's own palette path."""
    chart.clear_widgets()
    chart.clear_data()
    chart.channel_combo.clear()
    for name in ("AA", "BB"):
        chart.channel_combo.addItem(name)
    chart.channel_combo.setCurrentText("AA")
    ids = []
    for kind in kinds:
        chart.kind_combo.setCurrentIndex(chart.kind_combo.findData(kind))
        chart.title_edit.setText("widget-" + kind)
        chart.unit_edit.setText("V")
        chart.min_edit.setText("0")
        chart.max_edit.setText("10")
        ids.append(chart.add_widget_from_palette())
    chart.refresh()
    return ids


def chart_scope_checks(app, chart, kinds) -> None:
    """R-151 / R-161: pixels must come from the received data.

    R-151 is the older promise "the trace is really drawn: a firmware-prefixed
    line parses, and the curve is not hidden behind the grid"; R-161 is the
    drag-and-drop dashboard that superseded the pyqtgraph panel.  The pixel
    proof is shared, so both requirements are asserted here instead of R-151
    being left without any emitting check (which makes the acceptance suite
    report it as uncovered and exit non-zero).
    """
    chart.clear_widgets()
    chart.clear_data()
    widget_id = _add_all_kinds(chart, kinds)[kinds.index("line")]
    _wait(app, chart, len(kinds))
    chart.feed(1000, b"[OmniCOM-chart] AA=1\n")
    chart.flush()
    # The line arrives with a firmware banner in front of it; parsing it at all
    # is the first half of R-151.
    check(f"R-151 firmware-prefixed line reaches the channel store "
          f"(channels={sorted(chart.samples)})",
          "AA" in chart.samples and chart.values.get("AA") == 1.0)
    empty = _painted(app, chart, widget_id, floor=10 ** 6, tries=20)
    for index in range(120):
        chart.feed(1000 + index * 20,
                   f"[OmniCOM-chart] AA={5 + 4 * math.sin(index / 8):.4f}\n".encode())
    chart.flush()
    filled = _painted(app, chart, widget_id, floor=100)
    check(f"R-161 trace pixels grow with the data (empty={empty}, "
          f"with_data={filled})",
          isinstance(empty, int) and isinstance(filled, int) and filled > empty)
    # The trace is painted on top of the grid: a real curve adds hundreds of
    # pixels that the empty grid does not have (R-151, second half).
    check(f"R-151 curve is drawn above the grid (empty={empty}, "
          f"with_data={filled})",
          isinstance(empty, int) and isinstance(filled, int)
          and filled - empty > 200)

    # A channel whose value never changes must still be visible (L-191).
    chart.clear_widgets()
    chart.clear_data()
    widget_id = _add_all_kinds(chart, kinds)[kinds.index("line")]
    _wait(app, chart, len(kinds))
    for index in range(30):
        chart.feed(1000 + index * 100, b"[OmniCOM-chart] AA=3.87\n")
    chart.flush()
    flat = _painted(app, chart, widget_id, floor=100)
    check(f"R-161 flat trace stays visible (painted={flat})",
          isinstance(flat, int) and flat > 100)

def launcher_checks(app) -> None:
    """R-183: the launcher's two rows come from data, not from an empty grid.

    The launcher had no test coverage at all, and its screenshot is quoted in
    both READMEs, so the lower row silently going empty (no window matched any
    shortcut) would have shipped unseen.
    """
    from omnicom import paths
    from omnicom.plugins import PluginHost

    folder = Path(paths.data_dir()) / "plugin-data" / "launcher"
    folder.mkdir(parents=True, exist_ok=True)
    apps = [
        {"name": "甲", "path": "C:/Windows/System32/notepad.exe"},
        {"name": "乙", "path": "C:/Windows/System32/calc.exe"},
        {"name": "丙", "path": "C:/Windows/System32/mspaint.exe"},
    ]
    (folder / "apps.json").write_text(json.dumps(apps, ensure_ascii=False),
                                      encoding="utf-8")
    host = PluginHost(notify=lambda text, level="info": None,
                      log_line=lambda text: None)
    host.load()
    info = next((item for item in host.loaded() if item.id == "launcher"), None)
    check(f"R-183 launcher plugin loads (found={info is not None})", info is not None)
    if info is None:
        host.unload()
        return
    page = info.widget
    # The real scan enumerates this machine's windows on a worker thread and
    # would overwrite the synthetic snapshot below at an unpredictable time.
    # The timer start/stop behaviour is still exercised, only the scan is
    # replaced, so the assertions stay deterministic.
    page.refresh_running = lambda: None
    page._scan_processes = lambda: None
    page.resize(1000, 620)
    page.show()
    for _ in range(20):
        app.processEvents()
        time.sleep(0.02)
    shortcuts = [tile for tile in page.tiles if tile.kind == "shortcut"]
    check(f"R-183 every stored shortcut is drawn (tiles={len(shortcuts)}, "
          f"apps={len(page.apps)})",
          len(page.apps) == 3 and len(shortcuts) == len(page.apps))
    check(f"R-183 the count pill summarises both rows ({page.count_label.text()!r})",
          "3" in page.count_label.text() and "·" in page.count_label.text())

    # The running row must react to the scanned windows, not to the store: feed
    # a fake snapshot through the same slot the worker thread emits to.
    page._apply_processes({
        "processes": {"notepad.exe": 2},
        "windows": [
            {"path": "C:/Windows/System32/notepad.exe", "title": "甲窗口",
             "pid": 101, "hwnd": 5001},
            {"path": "C:/Windows/System32/notepad.exe", "title": "乙窗口",
             "pid": 102, "hwnd": 5002},
        ],
    })
    running = [tile for tile in page.tiles if tile.kind == "running"]
    check(f"R-183 running windows become tiles (tiles={len(running)})",
          len(running) == 2)
    check("R-183 the running row replaces the empty hint",
          page.running_host.isVisible() and not page.running_none.isVisible())
    check("R-183 the count pill counts the running windows too "
          f"({page.count_label.text()!r})",
          "2 个窗口" in page.count_label.text())

    # Two windows of one program must be tellable apart by their own labels:
    # the alias is keyed by the window, not by the process (R-095's cousin).
    window_key = info.module._window_key
    app_entry = page.apps[0]
    windows = page._windows_of(app_entry)
    app_entry.setdefault("aliases", {})[window_key(windows[1])] = "A"
    page._rebuild()
    captions = [tile.title for tile in page.tiles if tile.kind == "running"]
    check(f"R-183 a renamed window keeps its own caption (captions={captions})",
          captions == ["甲窗口", "A"])

    # Everything the picture shows has to survive the panel being hidden: the
    # 4 s scan stops when the page is off screen (L-130) and must restart.
    page.hide()
    app.processEvents()
    check("R-183 hiding the page stops the window scan", not page._timer.isActive())
    page.show()
    app.processEvents()
    check("R-183 showing it again restarts the scan", page._timer.isActive())
    page.hide()
    host.unload()


def chart_readout_checks(app, chart) -> None:
    """R-182: every widget kind renders a real value, never ``--``.

    Both READMEs print these five cards side by side (``plugin-chart.png``), so
    a kind that stops receiving its value would make the documentation lie while
    every existing test still passed - the earlier checks asserted *pixels* on
    the line and gauge cards only.
    """
    chart.clear_widgets()
    chart.clear_data()
    chart.channel_combo.clear()
    for name in ("AA", "BB", "CC", "DD", "EE"):
        chart.channel_combo.addItem(name)
    chart.layout_data["widgets"] = [
        {"id": "r182-line", "kind": "line", "channel": "AA", "channels": ["AA"],
         "x": 16, "y": 16, "w": 300, "h": 200},
        {"id": "r182-gauge", "kind": "gauge", "channel": "BB",
         "min": 0, "max": 10, "x": 332, "y": 16, "w": 200, "h": 200},
        {"id": "r182-value", "kind": "value", "channel": "CC",
         "unit": "A", "x": 16, "y": 232, "w": 200, "h": 150},
        {"id": "r182-bar", "kind": "bar", "channel": "DD",
         "min": 0, "max": 100, "unit": "%", "x": 232, "y": 232,
         "w": 300, "h": 150},
        {"id": "r182-lamp", "kind": "lamp", "channel": "EE",
         "x": 548, "y": 232, "w": 200, "h": 150},
    ]
    chart._push_layout()
    _wait(app, chart, len(KINDS))
    chart.feed(1000, b"[OmniCOM-chart] AA=1 BB=2 CC=3.25 DD=42 EE=1\n")
    chart.flush()

    code = """(() => {
      const out = {};
      document.querySelectorAll('#canvas .card').forEach(card => {
        const role = (name) => {
          const node = card.querySelector('[data-role="' + name + '"]');
          return node ? node.textContent.trim() : null;
        };
        const fill = card.querySelector('.bar-fill');
        out[card.dataset.kind] = {
          number: role('number'),
          lamp: role('lampText'),
          fill: fill ? fill.style.width : null
        };
      });
      return JSON.stringify(out);
    })()"""
    raw = _js(app, chart, code)
    state = json.loads(raw) if raw else {}
    check(f"R-182 all five kinds are on the page (kinds={sorted(state)})",
          sorted(state) == sorted(KINDS))
    check(f"R-182 the value card shows the parsed number "
          f"({state.get('value', {}).get('number')!r})",
          state.get("value", {}).get("number") == "3.25")
    check(f"R-182 the bar card shows number and fill "
          f"({state.get('bar')!r})",
          state.get("bar", {}).get("number") == "42.00"
          and state.get("bar", {}).get("fill") == "42%")
    check(f"R-182 the lamp card reads ON for a non-zero value "
          f"({state.get('lamp', {}).get('lamp')!r})",
          state.get("lamp", {}).get("lamp") == "ON")
    check("R-182 no card is left showing --",
          all((card.get("number") or "-") != "--" for card in state.values())
          and all((card.get("lamp") or "-") != "--" for card in state.values()))
    check(f"R-182 the line and gauge cards painted "
          f"(line={_painted(app, chart, 'r182-line')}, "
          f"gauge={_painted(app, chart, 'r182-gauge')})",
          _painted(app, chart, "r182-line") > 100
          and _painted(app, chart, "r182-gauge") > 100)

    # Zero is data, not absence: a lamp must read OFF rather than -- (L-192).
    chart.feed(1100, b"[OmniCOM-chart] AA=1 BB=2 CC=3.25 DD=0 EE=0\n")
    chart.flush()
    raw = _js(app, chart, code)
    zero = json.loads(raw) if raw else {}
    check(f"R-182 a zeroed lamp reads OFF, not -- "
          f"({zero.get('lamp', {}).get('lamp')!r})",
          zero.get("lamp", {}).get("lamp") == "OFF")
    check(f"R-182 a zeroed bar is empty but still numbered "
          f"({zero.get('bar')!r})",
          zero.get("bar", {}).get("number") == "0.00"
          and zero.get("bar", {}).get("fill") == "0%")
    chart.clear_widgets()


def search_checks(app) -> None:
    from PySide6.QtCore import Qt

    from omnicom.ui.search_panel import SearchPanel

    panel = SearchPanel()
    panel.resize(700, 500)
    panel.show()
    text = "Long search result " + "A1B2" * 400 + " FINAL-END-MARKER"
    panel._append_row(42, text)
    panel._append_row(43, "Short result")
    app.processEvents()
    check("R-146 sidebar results stay single-line", not panel.results.wordWrap() and panel.results.uniformItemSizes() and panel.results.visualItemRect(panel.results.item(0)).height() == panel.results.visualItemRect(panel.results.item(1)).height())
    check("R-144 search has no follow switch or latest button", not hasattr(panel, "row_scroll") and not hasattr(panel, "scroll_pill"))
    panel.set_fullscreen(True)
    app.processEvents()
    delegate = panel.results.itemDelegate()
    from PySide6.QtWidgets import QStyleOptionViewItem

    option = QStyleOptionViewItem()
    option.font = panel.results.font()
    index = panel.results.model().index(0, 0)
    document = delegate.document(option, index)
    wide = document.size().height()
    panel.resize(340, 500)
    app.processEvents()
    document = delegate.document(option, index)
    check("R-146 full result text retained", panel.results.item(0).text().endswith("FINAL-END-MARKER"))
    check("R-146 narrow resize reflows height", document.size().height() > wide)
    # Scoped to full screen on purpose: the sidebar deliberately gets a
    # horizontal bar instead (R-158), because a log line is far wider than the
    # column and wrapping there was rejected (R-146).
    check("R-146 full screen has no ellipsis or horizontal scroll", panel.results.textElideMode() == Qt.TextElideMode.ElideNone and panel.results.horizontalScrollBar().maximum() == 0)
    check("R-146 row contains whole wrapped document", panel.results.visualItemRect(panel.results.item(0)).height() >= document.size().height())
    panel.grab().save(str(ROOT / ".artifacts" / "search-wrap.png"))
    panel.set_fullscreen(False)
    app.processEvents()
    check("R-146 returning to sidebar restores single-line without data loss", not panel.results.wordWrap() and panel.results.visualItemRect(panel.results.item(0)).height() == panel.results.visualItemRect(panel.results.item(1)).height() and panel.results.item(0).text().endswith("FINAL-END-MARKER") and panel.results.item(0).data(Qt.ItemDataRole.UserRole) == 42)
    # ------------------------------------------------ R-158 sidebar scrolling
    bar = panel.results.horizontalScrollBar()
    check("R-158 sidebar shows a horizontal scrollbar for over-long rows "
          f"(policy={panel.results.horizontalScrollBarPolicy().name} "
          f"max={bar.maximum()})",
          panel.results.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
          and bar.maximum() > 0)
    bar.setValue(bar.maximum())
    app.processEvents()
    check("R-158 the bar really reaches the tail of the text "
          f"({bar.value()}/{bar.maximum()})",
          bar.value() == bar.maximum() > 0)
    panel.set_fullscreen(True)
    app.processEvents()
    check("R-146 full screen keeps the horizontal bar at zero "
          f"({panel.results.horizontalScrollBar().maximum()})",
          panel.results.horizontalScrollBar().maximum() == 0)
    panel.set_fullscreen(False)
    app.processEvents()
    bar.setValue(0)
    app.processEvents()
    panel.grab().save(str(ROOT / ".artifacts" / "search-sidebar.png"))
    panel.search_selection("FINAL-END-MARKER")
    check("R-050 selected-text search works without scroll controls", panel.input.text() == "FINAL-END-MARKER" and panel._needles() == ["FINAL-END-MARKER"] and panel.match_mode() == "plain")
    panel.close()


def multi_source_checks() -> None:
    from omnicom.plugin_store import CATALOG_URL, catalog_sources

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        urls = []
        for number in (1, 2):
            folder = root / str(number)
            folder.mkdir()
            atomic_json(folder / "catalog.json", {"schema": 1, "plugins": [
                {"id": "shared", "version": f"1.{number}.0", "url": "package.zip", "sha256": "0" * 64},
                {"id": "tie", "version": "1.0.0", "name": str(number)},
                {"id": f"only{number}", "version": "1.0.0"}]})
            urls.append((folder / "catalog.json").as_uri())
        missing = (root / "missing.json").as_uri()
        store = PluginStore(root / "data", root / "bundle", catalog_urls=[missing, *urls])
        result = {item["id"]: item for item in store.check()}
        check("R-149 queries every catalog with failure isolation", set(result) == {"shared", "tie", "only1", "only2"} and len(store.failures) == 1)
        check("R-149 newer same-ID version wins", result["shared"]["version"] == "1.2.0")
        check("R-149 relative package URL belongs to originating catalog", result["shared"]["url"] == (root / "2/package.zip").as_uri())
        check("R-149 equal versions prefer first configured source", result["tie"]["name"] == "1")
        (root / "2/catalog.json").unlink()
        check("R-149 failed source retains only its own cache", any(item["id"] == "only2" for item in store.check()) and len(store.failures) == 2)
        reduced = PluginStore(root / "data", root / "bundle", catalog_urls=urls[:1])
        check("R-149 removed sources never leak through cache", {item["id"] for item in reduced.cached_catalog()} == {"shared", "tie", "only1"})
        check("R-149 source normalization preserves order and deduplicates", catalog_sources(f" {urls[0]}\n\n{urls[0]}\n{urls[1]} ") == urls)
        check("R-149 default JSON lives in repository plugins directory", CATALOG_URL.endswith("/main/plugins/catalog.json") and "/releases/" not in CATALOG_URL)
        legacy = PluginStore(root / "legacy", root / "bundle", catalog_urls=urls[:1])
        atomic_json(legacy.cache, {"schema": 1, "plugins": [{"id": "unknownsource", "version": "9.0.0"}]})
        check("R-149 legacy originless cache cannot impersonate configured source", legacy.cached_catalog() == [])
        cancelled = threading.Event()
        cancelled.set()
        try:
            store.check(cancelled)
        except Cancelled:
            check("R-149 cancelling multi-source refresh aborts remaining sources", True)
        else:
            check("R-149 cancelling multi-source refresh aborts remaining sources", False)


def catalog_build_checks() -> None:
    import make_plugins

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        destination = root / "repository" / "plugins" / "catalog.json"
        catalog = make_plugins.build(root / "packages", "https://example.test/packages/", destination)
        check("R-149 catalog can be generated separately from release ZIPs", json.loads(destination.read_text(encoding="utf-8")) == catalog and not (root / "packages" / "catalog.json").exists())
        with patch.object(sys, "argv", ["make_plugins.py", "--out", str(root / "packages")]), patch.object(make_plugins, "build") as build:
            make_plugins.main()
        check("R-149 builder defaults JSON to repository plugins directory", build.call_args.args[2] == ROOT / "plugins" / "catalog.json")


def store_ui_checks(app) -> None:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QLineEdit, QWidget

    from omnicom.plugin_store import CATALOG_URL, configured_catalogs
    from omnicom.ui.dialogs import PreferencesDialog
    from omnicom.ui.main_window import OmniWindow
    from omnicom.ui.plugin_store import PluginStoreDialog

    class Window(QWidget):
        apply_plugin_sources = OmniWindow.apply_plugin_sources

        def suspend_plugins(self):
            pass

        def reload_plugins(self):
            pass

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        bundled = root / "bundle"
        (bundled / "demo").mkdir(parents=True)
        atomic_json(bundled / "demo" / "plugin.json", {"id": "demo", "version": "1.0.0", "name": "Demo"})
        atomic_json(root / "catalog.json", {"schema": 1, "plugins": [{"id": "demo", "version": "1.1.0", "name": "Demo"}, {"id": "extra", "version": "1.0.0", "name": "Extra"}]})
        window = Window()
        window.settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        dialog = PluginStoreDialog(window, PluginStore(root / "data", bundled, (root / "catalog.json").as_uri()))
        dialog.show()
        dialog.check()
        deadline = time.perf_counter() + 10
        while dialog.busy() and time.perf_counter() < deadline:
            app.processEvents()
        check("R-148 asynchronous catalog completion", not dialog.busy())
        check("R-148 UI includes available uninstalled entries", set(dialog.rows) == {"demo", "extra"} and dialog.rows["extra"]["item"]["installed_version"] is None)
        check("R-148 update red dot at top right", not dialog.rows["demo"]["badge"].isHidden())
        check("R-148 installed and latest versions are explicit", "1.0.0" in dialog.rows["demo"]["version"].text() and "1.1.0" in dialog.rows["demo"]["version"].text())
        app.processEvents()
        dialog.grab().save(str(ROOT / ".artifacts" / "plugin-store.png"))
        from omnicom.i18n import set_language

        set_language("en")
        dialog.retranslate()
        check("R-148 store language refresh includes version and actions", "Installed" in dialog.rows["demo"]["version"].text() and dialog.refresh_button.text() == "Check updates")
        set_language("zh")
        dialog.retranslate()
        # R-178 added a *filter* box, so "no QLineEdit at all" is no longer the
        # intent: the dialog must never carry a catalog-address editor.  Re-based
        # on the actual requirement (R-149) instead of on the widget class.
        from omnicom.i18n import tr

        check("R-149 store has no catalog address editor",
              [edit for edit in dialog.findChildren(QLineEdit)
               if edit is not dialog.filter_edit] == []
              and dialog.filter_edit.placeholderText() == tr("store.filter")
              and not hasattr(dialog, "source"))
        window.settings.setValue("plugins/catalog", (root / "legacy.json").as_uri())
        check("R-149 legacy custom single source is preserved", configured_catalogs(window.settings) == [(root / "legacy.json").as_uri()])
        window.settings.setValue("plugins/catalog", "https://github.com/Fatmear/OmniCOM/releases/download/plugin-store/catalog.json")
        check("R-149 legacy official Release JSON migrates to repository", configured_catalogs(window.settings) == [CATALOG_URL])
        window._plugin_store_dialog = dialog
        sources = [(root / "catalog.json").as_uri(), (root / "other.json").as_uri()]
        preferences = PreferencesDialog(window, plugin_sources="\n".join(configured_catalogs(window.settings)))
        preferences.pluginSourcesChanged.connect(window.apply_plugin_sources)
        preferences.plugin_sources.setPlainText("\n".join(sources))
        window.settings.sync()
        check("R-149 preferences saves multiline sources and removes old key", configured_catalogs(window.settings) == sources and not window.settings.contains("plugins/catalog"))
        check("R-149 open store receives preference changes without network", dialog.store.catalog_urls == sources)
        reopened = PreferencesDialog(window, plugin_sources="\n".join(configured_catalogs(window.settings)))
        check("R-149 preferences restores both sources", reopened.plugin_sources.toPlainText().splitlines() == sources)
        preferences.show()
        preferences.resize(540, 500)
        app.processEvents()
        preferences.preferences_scroll.ensureWidgetVisible(preferences.plugin_sources)
        app.processEvents()
        check("R-149 preferences can reach sources in a small window", preferences.preferences_scroll.verticalScrollBar().maximum() > 0)
        preferences.grab().save(str(ROOT / ".artifacts" / "plugin-preferences.png"))
        set_language("en")
        preferences.render()
        check("R-149 preferences source label translates", preferences.plugin_sources_section.text() == "Plugin catalogs (one URL per line)")
        set_language("zh")
        preferences.close()
        reopened.close()
        dialog.check()
        deadline = time.perf_counter() + 10
        while dialog.busy() and time.perf_counter() < deadline:
            app.processEvents()
        check("R-149 store reports partial failures without hiding successful catalog", not dialog.busy() and len(dialog.store.failures) == 1 and "1 个失败" in dialog.status.text() and "extra" in dialog.rows)
        dialog.close()

        # ------------------------------------------------------- R-178 picker
        history_root = root / "versions"
        history_root.mkdir()
        # A *fresh* bundle: the shared ``bundled`` folder already carries demo
        # 1.0.0, and an installed build is injected into the history as a
        # ``local`` record, which would make the drop-down 3 items instead of 2.
        (history_root / "bundle").mkdir()
        source = root / "demo-catalog.json"
        atomic_json(source, {"schema": 1, "plugins": [{
            "id": "demo", "version": "1.1.0", "api": 2, "name": "Demo",
            "versions": [{"version": "1.1.0", "api": 2, "url": "http://127.0.0.1:9/demo-1.1.0.zip",
                          "sha256": "0" * 64},
                         {"version": "1.0.5", "api": 2, "url": "http://127.0.0.1:9/demo-1.0.5.zip",
                          "sha256": "1" * 64}]}]})
        picker_dialog = PluginStoreDialog(
            window, PluginStore(history_root / "data", history_root / "bundle",
                                source.as_uri()))
        picker_dialog.show()
        picker_dialog.check()
        deadline = time.perf_counter() + 10
        while picker_dialog.busy() and time.perf_counter() < deadline:
            app.processEvents()
        entry = picker_dialog.rows["demo"]
        combo = entry["picker"].combo
        check("R-178 every row carries a version drop-down",
              combo.count() == 2
              and [combo.itemData(index) for index in range(combo.count())]
              == ["1.1.0", "1.0.5"])
        check("R-178 the newest build is labelled and pre-selected",
              "最新" in combo.itemText(0) and entry["picked"] == "1.1.0"
              and entry["state"] == "install" and entry["install"].text() == "安装")
        combo.setCurrentIndex(combo.findData("1.0.5"))
        check("R-178 choosing the latest first keeps a plain install",
              entry["picked"] == "1.0.5" and entry["state"] == "install"
              and entry["install"].isEnabled())
        # Pretend 1.1.0 is installed: the button must become a downgrade, and
        # picking the installed build must disable it rather than re-download.
        atomic_json(history_root / "data" / "plugins" / "demo" / "plugin.json",
                    {"id": "demo", "version": "1.1.0", "api": 2, "entry": "plugin.py"})
        (history_root / "data" / "plugins" / "demo" / "plugin.py").write_text(
            "def create(api):\n    return None\n", encoding="utf-8")
        picker_dialog.rebuild()
        entry = picker_dialog.rows["demo"]
        combo = entry["picker"].combo
        check("R-178 only the older downloadable build stays actionable",
              entry["picked"] == "1.0.5" and entry["state"] == "downgrade"
              and entry["install"].text() == "降级安装" and entry["install"].isEnabled())
        combo.setCurrentIndex(combo.findData("1.1.0"))
        check("R-178 the installed version disables the action",
              entry["state"] == "same" and not entry["install"].isEnabled()
              and entry["install"].text() == "已安装"
              and entry["install"].toolTip() == "已安装这个版本，无需操作")
        combo.setCurrentIndex(combo.findData("1.0.5"))
        check("R-178 the action follows the selection",
              entry["state"] == "downgrade" and entry["install"].isEnabled())
        picker_dialog.filter_edit.setText("dem")
        check("R-178 the filter keeps matching plugins",
              list(picker_dialog.rows) == ["demo"] and picker_dialog.count.text() == "共 1 / 1 个插件")
        picker_dialog.filter_edit.setText("nope")
        check("R-178 an empty filter result says so",
              not picker_dialog.rows and picker_dialog.count.text() == "共 0 / 1 个插件")
        picker_dialog.filter_edit.setText("")
        check("R-178 clearing the filter restores every row", list(picker_dialog.rows) == ["demo"])
        entry = picker_dialog.rows["demo"]
        set_language("en")
        picker_dialog.retranslate()
        entry = picker_dialog.rows["demo"]
        check("R-178 the picker and filter translate",
              "latest" in entry["picker"].combo.itemText(0)
              # 1.1.0 is both the newest and the installed build, so it carries
              # both tags; 1.0.5 is the plain older one.
              and "installed" in entry["picker"].combo.itemText(0)
              and "1.0.5" in entry["picker"].combo.itemText(1)
              and picker_dialog.filter_edit.placeholderText() == "Search plugins"
              and entry["install"].text() == "Install older")
        set_language("zh")
        picker_dialog.retranslate()
        entry = picker_dialog.rows["demo"]

        # ------------------------------------------- R-180 row title hierarchy
        # The name and the description used to be two plain labels of the same
        # weight and colour, so a card read as one run-on paragraph.  The name
        # is now ``PluginName`` (bold, one pixel larger, INK) and a hairline
        # ``PluginRule`` separates it from the ``PluginTagline`` description.
        from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

        from omnicom.ui import theme

        name_label = entry["widget"].findChild(QLabel, "PluginName")
        tagline = entry["widget"].findChild(QLabel, "PluginTagline")
        rule = entry["widget"].findChild(QFrame, "PluginRule")
        check("R-180 store rows tag the name, the rule and the description",
              name_label is not None and tagline is not None and rule is not None)
        # A QSS ``font-weight`` only reaches ``QFont`` once the widget has been
        # polished; reading the font here therefore proves the style applies,
        # not merely that the rule exists in a string.
        check("R-180 the plugin name is bold and a step larger",
              name_label.font().weight() == 700
              and tagline.font().weight() < 600
              and name_label.font().pixelSize() > tagline.font().pixelSize())
        row_layout = entry["widget"].layout()
        order = [row_layout.itemAt(index).widget().objectName()
                 if row_layout.itemAt(index).widget() else "layout"
                 for index in range(row_layout.count())]
        # The name lives in the header QHBoxLayout, so the top level never names
        # it: assert that the header *itself* is the first thing in the row and
        # that the rule and the tagline follow it in that order.
        header_item = row_layout.itemAt(0)
        header_holds_name = (header_item.layout() is not None
                             and header_item.layout().indexOf(name_label) >= 0)
        check("R-180 the name is separated from the description",
              header_holds_name
              and order.index("PluginRule") < order.index("PluginTagline"))
        check("R-180 the separator is a 1 px hairline in the theme line colour",
              rule.height() == 1
              and rule.minimumHeight() == 1
              and rule.maximumHeight() == 1
              and theme.LINE_SOFT in rule.styleSheet())
        # Order in the layout is not the same thing as separation on screen: a
        # zero-height header or a collapsed gap would still "pass" the index
        # test.  Measure the rendered boxes instead.
        from PySide6.QtCore import QPoint

        picker_dialog.resize(620, 620)
        app.processEvents()
        row = entry["widget"]
        name_bottom = name_label.mapTo(row, QPoint(0, name_label.height())).y()
        rule_top = rule.mapTo(row, QPoint(0, 0)).y()
        tagline_top = tagline.mapTo(row, QPoint(0, 0)).y()
        check("R-180 the rendered boxes leave a gap above and below the rule",
              name_bottom <= rule_top <= tagline_top
              and tagline_top - name_bottom >= 2 * rule.height()
              and name_label.height() > 0 and tagline.height() > 0)
        sheet = theme.app_stylesheet()
        name_rule = sheet.split("QLabel#PluginName", 1)[-1].split("}", 1)[0]
        tagline_rule = sheet.split("QLabel#PluginTagline", 1)[-1].split("}", 1)[0]
        check("R-180 the title hierarchy is a theme token, not a local colour",
              "font-weight: 700" in name_rule
              and theme.INK in name_rule
              and theme.DIM in tagline_rule)

        # -------------------------------------------------- R-181 no stray window
        # 检查更新 used to flash a blank little window.  Nothing opened a dialog:
        # the row badges were built with ``parent=None`` and ``show_text()``
        # called ``show()`` *before* ``addWidget()`` reparented them, so each
        # one became a real top-level window for a frame (R-181).  Assert both
        # halves of the contract: no badge inside the store is a window, and a
        # badge that is shown before it has a parent stays hidden until then.
        from PySide6.QtWidgets import QWidget

        from omnicom.ui import widgets as widgets_module

        stray = [badge for badge in picker_dialog.findChildren(widgets_module.Badge)
                 if badge.isWindow() or badge.parent() is None]
        check("R-181 no store badge is a top-level window",
              not stray)
        detached = widgets_module.Badge()
        detached.show_text("R-181")
        never_shown = not detached.isVisible() and detached.isWindow()
        detached_row = QFrame()
        detached_layout = QVBoxLayout(detached_row)
        detached_layout.addWidget(detached)
        deferred = detached._wants_showing and not detached.isWindow()
        check("R-181 a parentless badge defers show() until it is inserted",
              never_shown and deferred)
        detached_row.deleteLater()
        picker_dialog.close()


def benchmark(app, chart, parser_class) -> dict:
    """Parser throughput plus the Python-side cost of one dashboard frame.

    The ``push`` numbers cover parsing, history trimming and the JSON hand-off to
    the renderer -- they deliberately exclude the renderer's paint, which is not
    observable from this process (ECharts owns that clock).
    """
    results = {"python": sys.version, "platform": sys.platform,
               "parser": [], "push": []}
    for channels in (2, 8, 32):
        line = ("[OmniCOM-chart] "
                + " ".join(f"CH{index}={index + 0.125}" for index in range(channels))
                + "\n").encode()
        payload = line * 1000
        parser = parser_class()
        started = time.perf_counter()
        for _ in range(20):
            parser.feed(payload)
        elapsed = time.perf_counter() - started
        results["parser"].append({"channels": channels, "line_bytes": len(line),
                                  "frames_per_s": 20000 / elapsed,
                                  "MB_per_s": len(payload) * 20 / elapsed / 1e6})
    for channels in (2, 8, 32):
        chart.clear_widgets()
        chart.clear_data()
        chart.channel_combo.clear()
        chart.channel_combo.addItem("CH0")
        chart.kind_combo.setCurrentIndex(chart.kind_combo.findData("line"))
        chart.add_widget_from_palette()
        app.processEvents()

        def frame(index, channels=channels):
            body = " ".join(f"CH{number}={math.sin(index / 10 + number)}"
                            for number in range(channels))
            chart.feed(1000 + index * 20, f"[OmniCOM-chart] {body}\n".encode())

        for index in range(5000):
            frame(index)
        chart.flush()
        app.processEvents()
        timings = []
        for index in range(60):
            started = time.perf_counter()
            frame(5000 + index)
            chart.flush()
            app.processEvents()
            timings.append((time.perf_counter() - started) * 1000)
        ordered = sorted(timings)
        results["push"].append({
            "channels": channels, "points_per_channel": 5000, "frames": 60,
            "mean_ms": sum(timings) / len(timings),
            "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
            "measured_max_fps": 1000 * len(timings) / sum(timings),
            "timer_target_fps": 1000 / 33})
    return results

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    data = Path(os.environ["OMNICOM_DATA_DIR"])
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    from omnicom import webengine
    from omnicom.ui import theme

    # QtWebEngine needs its Chromium switches and AA_ShareOpenGLContexts *before*
    # the first QApplication exists; without them the dashboard page silently
    # never finishes loading and every R-161 check would fail for the wrong
    # reason (L-221).
    webengine.prepare()
    app = QApplication([])
    for font in ("segoeui.ttf", "msyh.ttc", "consola.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.app_stylesheet())
    store_checks()
    multi_source_checks()
    catalog_build_checks()
    host, chart, parser_class = chart_checks(app, KINDS)
    chart_readout_checks(app, chart)
    launcher_checks(app)
    search_checks(app)
    store_ui_checks(app)
    if args.benchmark:
        results = benchmark(app, chart, parser_class)
        atomic_json(ROOT / ".artifacts" / "chart-benchmark.json", results)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    timer = chart.timer
    view = chart.view
    host.unload()
    check("R-147 unload stops chart timer", not timer.isActive())
    # The panel owns a QWebEngineView, and deleting that widget without first
    # releasing the page/profile aborts the process at exit (0xC0000005, "Release
    # of profile requested but WebEnginePage still not deleted").  The host calls
    # the optional ``dispose()`` hook before the generic delete; it must clear
    # ``view``/``page_ready`` and destroy the view *synchronously* (L-221).
    import shiboken6

    check("R-161 unload disposes the WebEngine surface",
          chart.view is None and chart.page_ready is False and not chart.timer.isActive())
    check("R-161 WebEngine view destroyed synchronously, not left to deleteLater",
          not shiboken6.isValid(view))
    atomic_json(ROOT / ".artifacts" / "plugin-selftest.json",
                {"checks": CHECKS, "ok": all(item["ok"] for item in CHECKS)})
    print(f"PLUGIN SELFTEST OK: {len(CHECKS)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
