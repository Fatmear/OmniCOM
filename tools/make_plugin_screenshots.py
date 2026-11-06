"""Render the per-plugin screenshots from the real plugin widgets.

    python tools/make_plugin_screenshots.py      # writes docs/images/plugin-*.png

The README advertises two plugins, so each one needs a picture that proves what
it actually looks like.  A hand-made mock-up would be worthless here - the data
dashboard is an ECharts page driven by received bytes and the launcher paints a
grid of real Windows icons - so both images are grabbed from the live widgets:

* ``plugin-chart.png``  - the dashboard with **one card of every kind**
  (line / gauge / value / bar / lamp), each bound to its own channel and each
  showing a number that came out of :meth:`DashboardPanel.feed`, i.e. out of the
  same parser path real serial bytes take.  A screenshot where the value says
  ``--`` would document nothing, so the rendered DOM is read back afterwards
  (:func:`verify_chart`) and a run that finds an empty readout fails instead of
  shipping the picture.
* ``plugin-launcher.png`` - the launcher with a shortcut row plus a *running*
  row.  Two real ``charmap.exe`` processes are started so the lower half is
  populated by the genuine ``EnumWindows`` scan instead of a fake fixture; they
  are terminated again when the shot is taken.  ``notepad.exe`` / ``cmd.exe``
  are deliberately *not* used: those are almost always already open on a
  developer machine, and the scan would then print this machine's own document
  titles (``44.txt - 记事本`` ...) into a public screenshot.

QtWebEngine needs its prerequisites before the first ``QApplication`` exists
(``omnicom/webengine.py``), hence the ``prepare()`` call at import time - the
README screenshot script never opens a plugin page, so it never needed this.

Run ``python tools/make_readme.py`` afterwards to embed the new pictures.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["OMNICOM_SETTINGS_SUFFIX"] = "-plugin-shots"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

#: Throwaway data folder: a run must never inherit the user's installed plugins,
#: their ``plugin-data`` or the real launcher list (L-116).
DATA_DIR = os.path.join(ROOT, ".artifacts", "shot-data", "plugins")
os.environ["OMNICOM_DATA_DIR"] = DATA_DIR

from omnicom import webengine  # noqa: E402

webengine.prepare()

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from omnicom.plugins import PluginHost  # noqa: E402
from omnicom.ui.theme import app_stylesheet  # noqa: E402

OUT_DIR = os.path.join(ROOT, "docs", "images")
CHART_SIZE = (1180, 760)
LAUNCHER_SIZE = (1000, 620)
ERRORS: list[str] = []


def system32(name: str) -> str:
    root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
    return os.path.join(root, "System32", name)


#: The five cards of the dashboard shot.  Geometry is explicit on purpose: the
#: panel's own auto-tiling uses one default size *per kind*, so a 256 px tall
#: line card would be overlapped by the 112 px bar it places below it.  These
#: rectangles fill the 1156x635 canvas a 1180x760 panel produces, 16 px gutter.
CHART_CARDS = (
    {"kind": "line", "channel": "temperature", "title": "温度曲线",
     "x": 16, "y": 16, "w": 700, "h": 300},
    {"kind": "gauge", "channel": "supply", "title": "供电电压", "unit": "V",
     "min": 0, "max": 5, "x": 732, "y": 16, "w": 408, "h": 300},
    {"kind": "value", "channel": "current", "title": "工作电流", "unit": "A",
     "x": 16, "y": 332, "w": 300, "h": 287},
    {"kind": "bar", "channel": "level", "title": "缓冲区占用", "unit": "%",
     "min": 0, "max": 100, "x": 332, "y": 332, "w": 340, "h": 287},
    {"kind": "lamp", "channel": "alarm", "title": "告警指示",
     "x": 688, "y": 332, "w": 452, "h": 287},
)

#: Shortcuts for the launcher shot.  ``path`` is the only required key; the
#: names are Chinese so the picture matches the Chinese README.  Nothing here is
#: expected to be running already, so the running row shows only the two windows
#: this script starts.
LAUNCHER_APPS = (
    ("画图", "mspaint.exe"),
    ("字符映射表", "charmap.exe"),
    ("计算器", "calc.exe"),
    ("远程桌面连接", "mstsc.exe"),
    ("系统信息", "msinfo32.exe"),
    ("注册表编辑器", "regedit.exe"),
    ("放大镜", "magnify.exe"),
    ("屏幕键盘", "osk.exe"),
)
#: Started so the 正在运行 row is filled by the real window enumeration.
RUNNING_APP = "charmap.exe"
RUNNING_COUNT = 2


def guard(label: str, func):
    try:
        return func()
    except Exception:
        ERRORS.append(f"{label}: {traceback.format_exc()}")
        return None


def pump(app, seconds: float) -> None:
    """Let Qt and Chromium work for a while without blocking the event loop."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_for(app, predicate, timeout: float = 30.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


def run_js(app, view, code: str, timeout_ms: int = 6000):
    """Run one snippet in the dashboard page and return its value."""
    result = {"value": None}
    loop = QEventLoop()

    def done(value) -> None:
        result["value"] = value
        loop.quit()

    view.page().runJavaScript(code, 0, done)
    arm = QTimer()
    arm.setSingleShot(True)
    arm.timeout.connect(loop.quit)
    arm.start(timeout_ms)
    loop.exec()
    arm.stop()
    app.processEvents()
    return result["value"]


def save(widget, name: str) -> None:
    path = os.path.join(OUT_DIR, name)
    pixmap = widget.grab()
    pixmap.save(path)
    print(f"  {name:22} {pixmap.width()}x{pixmap.height()} "
          f"{os.path.getsize(path) / 1024:.0f} KB")


# ------------------------------------------------------------------ fixtures
def seed_launcher() -> list[dict]:
    """Write the shortcut list *before* the plugin is instantiated."""
    folder = os.path.join(DATA_DIR, "plugin-data", "launcher")
    os.makedirs(folder, exist_ok=True)
    apps = []
    for name, executable in LAUNCHER_APPS:
        path = system32(executable)
        if os.path.isfile(path):
            apps.append({"name": name, "path": path})
    with open(os.path.join(folder, "apps.json"), "w", encoding="utf-8") as handle:
        json.dump(apps, handle, ensure_ascii=False, indent=2)
    return apps


def spawn_running(count: int = RUNNING_COUNT) -> list:
    """Start real windows so the running row is populated by the real scan."""
    executable = system32(RUNNING_APP)
    if not os.path.isfile(executable):
        ERRORS.append(f"{RUNNING_APP} is missing; the running row would be empty")
        return []
    started = []
    for _ in range(count):
        try:
            started.append(subprocess.Popen(
                [executable],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0x08000000))          # CREATE_NO_WINDOW
        except OSError as exc:
            ERRORS.append(f"failed to start {RUNNING_APP}: {exc}")
    time.sleep(2.5)                      # let the windows appear and settle
    return started


# -------------------------------------------------------------- verification
def verify_chart(app, chart) -> None:
    """Refuse to ship a picture whose readouts are still empty.

    Reads the rendered DOM rather than the Python state: a number that never
    reached the page is exactly the failure this script exists to catch.
    """
    raw = run_js(app, chart.view, """(() => {
      const out = {cards: []};
      document.querySelectorAll('#canvas .card').forEach(card => {
        const rect = card.getBoundingClientRect();
        const text = (role) => {
          const node = card.querySelector('[data-role="' + role + '"]');
          return node ? node.textContent.trim() : null;
        };
        const fill = card.querySelector('.bar-fill');
        out.cards.push({
          kind: card.dataset.kind,
          rect: [Math.round(rect.width), Math.round(rect.height)],
          bottom: Math.round(rect.bottom),
          number: text('number'),
          lamp: text('lampText'),
          fill: fill ? fill.style.width : null,
          canvas: card.querySelectorAll('canvas').length
        });
      });
      out.canvas = [document.getElementById('canvas').clientWidth,
                    document.getElementById('canvas').clientHeight];
      return JSON.stringify(out);
    })()""")
    if not raw:
        ERRORS.append("could not read the dashboard DOM")
        return
    state = json.loads(raw)
    cards = state.get("cards") or []
    canvas = state.get("canvas") or ["?", "?"]
    print(f"  dashboard: {len(cards)} cards on a {canvas[0]}x{canvas[1]} canvas")
    if len(cards) != len(CHART_CARDS):
        ERRORS.append(f"expected {len(CHART_CARDS)} cards, page has {len(cards)}")
    missing = {card["kind"] for card in CHART_CARDS} - {c.get("kind") for c in cards}
    if missing:
        ERRORS.append(f"card kinds missing from the page: {sorted(missing)}")
    # A card reading "--", an empty fill or a collapsed rectangle proves nothing,
    # and a card larger than the canvas would be cropped out of the screenshot.
    for card in cards:
        kind = card.get("kind")
        width, height = card.get("rect") or [0, 0]
        if width < 160 or height < 110:
            ERRORS.append(f"{kind} card collapsed to {width}x{height}")
        elif width > canvas[0] or (card.get("bottom") or 0) > canvas[1]:
            ERRORS.append(f"{kind} card overflows the canvas "
                          f"({width}x{height}, bottom {card.get('bottom')})")
        if kind in ("value", "bar") and (card.get("number") in (None, "", "--")):
            ERRORS.append(f"{kind} card shows no value ({card.get('number')!r})")
        if kind == "bar" and (not card.get("fill") or card["fill"] in ("0%", "")):
            ERRORS.append(f"bar card fill is {card.get('fill')!r}")
        if kind == "lamp" and card.get("lamp") not in ("ON", "OFF"):
            ERRORS.append(f"lamp card text is {card.get('lamp')!r}")
        if kind in ("line", "gauge") and not card.get("canvas"):
            ERRORS.append(f"{kind} card has no <canvas> - ECharts never painted")
    readouts = ", ".join(
        f"{card.get('kind')}={card.get('number') or card.get('lamp') or card.get('fill')}"
        for card in cards)
    print(f"  readouts: {readouts}")


def verify_launcher(page) -> None:
    shortcuts = [tile for tile in page.tiles if tile.kind == "shortcut"]
    running = [tile for tile in page.tiles if tile.kind == "running"]
    print(f"  launcher: {len(shortcuts)} shortcuts, {len(running)} running "
          f"({page.count_label.text()})")
    if len(shortcuts) != len(page.apps):
        ERRORS.append(f"launcher drew {len(shortcuts)} of {len(page.apps)} shortcuts")
    if not running:
        ERRORS.append("launcher running row is empty - the shot would show a hole")


# --------------------------------------------------------------------- shots
def chart_shot(app, host) -> None:
    info = next((item for item in host.plugins if item.id == "chart"), None)
    if info is None or info.widget is None:
        ERRORS.append(f"chart plugin unavailable: "
                      f"{getattr(info, 'error', 'not loaded')}")
        return
    chart = info.widget
    chart.resize(*CHART_SIZE)
    chart.show()
    if not wait_for(app, lambda: chart.page_ready, 40):
        ERRORS.append(f"dashboard page never loaded: {chart.surface_error}")
        return

    chart.clear_widgets()
    chart.clear_data()
    chart.channel_combo.clear()
    pump(app, 0.3)
    # ``add_widget`` is the same entry point the palette button uses; passing the
    # geometry explicitly only skips the auto-tiling.
    for card in CHART_CARDS:
        chart.add_widget(dict(card))
    pump(app, 0.5)

    # Real telemetry: every channel rides the same ``[OmniCOM-chart]`` line, so
    # the picture is produced by the parser and not by a value injected into the
    # page.  160 frames at 40 ms = 6.4 s of history behind the curve.
    for index in range(160):
        stamp = 1000.0 + index * 40.0
        line = (
            f"[OmniCOM-chart] temperature={24 + 6 * math.sin(index / 16):.3f} "
            f"supply={4.90 - 0.03 * math.sin(index / 9):.3f} "
            f"current={0.42 + 0.07 * math.sin(index / 11):.3f} "
            f"level={68 + 9 * math.sin(index / 13):.1f} "
            f"alarm={1 if index >= 100 else 0}\n")
        chart.feed(stamp, line.encode())
    chart.refresh()
    chart.flush()
    pump(app, 1.2)
    verify_chart(app, chart)
    # The panel grab (not the view) is deliberate: the toolbar, the widget picker
    # and the counter footer are part of what this plugin *is*, and the Chromium
    # surface is composited into it just the same (probed: 105 distinct colours on
    # an empty canvas, 462 with the five cards).
    save(chart, "plugin-chart.png")


def launcher_shot(app, host) -> None:
    info = next((item for item in host.plugins if item.id == "launcher"), None)
    if info is None or info.widget is None:
        ERRORS.append(f"launcher plugin unavailable: "
                      f"{getattr(info, 'error', 'not loaded')}")
        return
    page = info.widget
    page.resize(*LAUNCHER_SIZE)
    page.show()
    # One full process/window scan takes a moment; the panel re-runs it from a
    # worker thread and rebuilds the tiles when the answer arrives.
    pump(app, 1.2)
    page.refresh_running()
    pump(app, 3.0)
    verify_launcher(page)
    save(page, "plugin-launcher.png")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.isdir(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    apps = seed_launcher()
    if not apps:
        ERRORS.append("no shortcut could be seeded (System32 exes missing)")
    print(f"seeded {len(apps)} shortcuts, starting {RUNNING_APP} x{RUNNING_COUNT} ...")
    started = spawn_running()

    app = QApplication(sys.argv)
    for filename in ("segoeui.ttf", "msyh.ttc", "consola.ttf"):
        font_path = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts",
                                 filename)
        if os.path.isfile(font_path):
            QFontDatabase.addApplicationFont(font_path)
    app.setStyle("Fusion")
    app.setStyleSheet(app_stylesheet())
    print(f"webengine ready: {webengine.ready()}")

    host = PluginHost(notify=lambda text, level="info": None,
                      log_line=lambda text: None)
    guard("host.load", host.load)
    print("saving:")
    guard("chart", lambda: chart_shot(app, host))
    guard("launcher", lambda: launcher_shot(app, host))
    guard("host.unload", host.unload)
    pump(app, 0.3)

    for process in started:
        with contextlib.suppress(OSError):
            process.terminate()
    pump(app, 0.5)

    if ERRORS:
        print("\n".join(ERRORS))
        return 1
    print("plugin screenshots written to docs/images/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
