"""Draggable telemetry dashboard ("数据监控大屏") rendered with ECharts.

Design
------
* **Python owns the data.**  :class:`AsciiParser` (untouched) turns the
  ``[OmniCOM-chart] A=1 B=2`` lines into frames; this panel keeps a short
  history per channel and pushes one batched JSON document per animation frame
  into the page.
* **The page owns the presentation.**  Widgets are absolutely positioned cards
  inside a ``QWebEngineView``: 曲线 (line), 仪表盘 (gauge), 数值 (value),
  进度条 (bar) and 指示灯 (lamp).  Dragging, resizing, grid snapping and the
  per-widget ECharts instances all live in ``dashboard.js``, so moving a card
  costs no Python work at all.  The grid is always painted and drags always
  work, so there is no "edit mode" to turn on first (R-173).
* **Qt is the only channel back.**  Drags, deletes and layout changes arrive
  through one :class:`QWebChannel` object named ``omni``; the layout is stored
  as JSON in the plugin's own ``settings.ini`` (R-088: portable, never the
  registry).

Nothing here may raise (R-032): a missing QtWebEngine package, a missing
vendored ECharts build or a dead render process all become a readable message
inside the panel.
"""

from __future__ import annotations

import contextlib
import csv
import json
import time
from collections import deque

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import page
from .protocol import AsciiParser

MAX_CHANNELS = 128
MAX_POINTS = 5000
DEFAULT_WINDOW_S = 0
FRAME_MS = 33

#: Kinds the palette can create, in palette order.
KINDS = ("line", "gauge", "value", "bar", "lamp")

#: Default geometry per kind, in dashboard pixels (multiples of the 16 px grid).
DEFAULT_SIZE = {
    "line": (480, 256),
    "gauge": (256, 240),
    "value": (208, 128),
    "bar": (320, 112),
    "lamp": (208, 128),
}

_KIND_LABELS = {
    "line": ("曲线", "Line"),
    "gauge": ("仪表盘", "Gauge"),
    "value": ("数值", "Value"),
    "bar": ("进度条", "Progress"),
    "lamp": ("指示灯", "Lamp"),
}

#: Canvas strokes need CSS alpha syntax (0-1).  ``theme.LINE_SOFT`` is the Qt
#: stylesheet form (alpha 0-255) and is *not* valid CSS, so these are written
#: out here instead of being passed straight through.
_STROKE = "rgba(18, 26, 43, 0.10)"
_STROKE_SOFT = "rgba(18, 26, 43, 0.07)"


class WheelZoomGuard(QObject):
    """Swallow Ctrl+wheel before Chromium turns it into a **page** zoom (R-170).

    ECharts is not the culprit: Chromium's own zoom controller scales the whole
    document on ``Ctrl`` + wheel, and it runs *below* the page, so a JavaScript
    ``preventDefault()`` is not reliably early enough.  The event therefore has
    to be stopped on the Qt side, on the delegate widget that actually receives
    the wheel (``QWebEngineView.focusProxy()``).

    Zooming a curve is done with its own axis sliders, so nothing is lost; a
    plain wheel still scrolls the dashboard exactly as before.
    """

    def __init__(self, panel) -> None:
        super().__init__(panel)
        self.panel = panel

    def eventFilter(self, watched, event) -> bool:
        try:
            kind = event.type()
        except (RuntimeError, AttributeError):
            return False
        if kind is QEvent.Type.Wheel and event.modifiers() & Qt.ControlModifier:
            # Accept, do not ignore: an ignored wheel would bubble up to the
            # view and be turned into a zoom again.
            event.accept()
            self.panel.wheel_zoom_blocked += 1
            return True
        return False


class DashboardBridge(QObject):
    """The single object JavaScript may call back into."""

    layoutChanged = Signal(str)
    greeted = Signal(str)

    @Slot(str, str)
    def action(self, name: str, payload: str) -> None:
        """Entry point for every JS -> Python call.

        ``payload`` is always a JSON string: QWebChannel's meta-object layer has
        no generic marshalling, and one narrow signature keeps the contract
        obvious from both sides.
        """
        try:
            data = json.loads(payload) if payload else {}
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        if name == "layout":
            self.layoutChanged.emit(json.dumps(data, ensure_ascii=False))
        elif name == "hello":
            self.greeted.emit(str(data.get("tag") or ""))


class DashboardPanel(QWidget):
    """The plugin page: toolbar, palette, and the ECharts surface."""

    def __init__(self, api) -> None:
        super().__init__()
        self.api = api
        self.parser = AsciiParser()
        self.samples: dict[str, deque[tuple[float, float]]] = {}
        self.values: dict[str, float] = {}
        self.order: list[str] = []
        self._pending_names: list[str] = []
        self.origin: float | None = None
        self.latest = 0.0
        self.evicted = 0
        self.refused = 0
        self.rate = 0.0
        self.arrivals: deque[float] = deque(maxlen=512)
        self.render_count = 0
        self.render_ms: deque[float] = deque(maxlen=240)
        self.page_ready = False
        self.surface_error: tuple[str, str] | None = None
        #: Ctrl+wheel gestures swallowed before Chromium could scale the page
        #: (R-170).  Counted so the self-test can prove the guard is live instead
        #: of trusting that a handler exists.
        self.wheel_zoom_blocked = 0
        self._wheel_guard: WheelZoomGuard | None = None
        self._wheel_guard_target = None

        self.layout_data = self._load_layout()
        self.bridge: DashboardBridge | None = None
        self.view = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        root.addLayout(self._build_toolbar())
        root.addLayout(self._build_palette())
        self.stack = QStackedWidget()
        surface, self.fallback = self._build_surface()
        self.stack.addWidget(surface)
        self.stack.addWidget(self.fallback)
        root.addWidget(self.stack, 1)
        root.addLayout(self._build_footer())

        self.timer = QTimer(self)
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

        api.on_rx(self.feed)
        api.on_language(lambda *_: self.retranslate())
        api.on_theme(self.apply_theme)
        api.on_unload(self.timer.stop)

        self.retranslate()
        self.apply_theme()
        self._load_page()
        self._on_kind_changed()
        self.refresh_status()

    # ------------------------------------------------------------------ build
    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.live = QPushButton()
        self.live.setCheckable(True)
        # Always open running - the saved value is deliberately *not* restored
        # (R-176, revised).  The dashboard refreshes from the moment it is
        # opened, so restoring a stored "0" made a fresh window come up frozen
        # with a grey 继续 button, which reads as "this is switched off" and made
        # the user think the default was wrong (L-240).  Same trap as the
        # keep-awake switch (L-156): a remembered off-state nobody can find.
        self.live.setChecked(True)
        self.live.toggled.connect(self._on_run_toggled)
        bar.addWidget(self.live)
        bar.addStretch(1)
        self.seconds_label = QLabel()
        bar.addWidget(self.seconds_label)
        self.seconds = QSpinBox()
        self.seconds.setRange(0, 3600)
        self.seconds.setValue(self._initial_window())
        self.seconds.valueChanged.connect(lambda *_: self._save_ui())
        bar.addWidget(self.seconds)
        self.clear_widgets_button = QPushButton()
        self.clear_widgets_button.clicked.connect(self.clear_widgets)
        bar.addWidget(self.clear_widgets_button)
        self.image_button = QPushButton()
        self.image_button.clicked.connect(self.save_image_dialog)
        bar.addWidget(self.image_button)
        self.export_button = QPushButton()
        self.export_button.clicked.connect(self.export_dialog)
        bar.addWidget(self.export_button)
        clear = self.api.glyph("clear")()
        clear.clicked.connect(self.clear_data)
        self.clear_button = clear
        bar.addWidget(clear)
        return bar

    def _build_palette(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.kind_label = QLabel()
        bar.addWidget(self.kind_label)
        self.kind_combo = self.api.combo()
        for kind in KINDS:
            self.kind_combo.addItem("", kind)
        self.kind_combo.setMinimumWidth(96)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        bar.addWidget(self.kind_combo)
        self.channel_label = QLabel()
        bar.addWidget(self.channel_label)
        self.channel_combo = self.api.combo(editable=True)
        self.channel_combo.setMinimumWidth(110)
        bar.addWidget(self.channel_combo)
        self.title_edit = QLineEdit()
        self.title_edit.setMinimumWidth(110)
        bar.addWidget(self.title_edit)
        self.unit_label = QLabel()
        bar.addWidget(self.unit_label)
        self.unit_edit = QLineEdit()
        self.unit_edit.setMaximumWidth(72)
        bar.addWidget(self.unit_edit)
        self.range_label = QLabel()
        bar.addWidget(self.range_label)
        self.min_edit = QLineEdit()
        self.min_edit.setMaximumWidth(68)
        bar.addWidget(self.min_edit)
        self.max_edit = QLineEdit()
        self.max_edit.setMaximumWidth(68)
        bar.addWidget(self.max_edit)
        bar.addStretch(1)
        self.add_button = QPushButton()
        self.add_button.clicked.connect(self.add_widget_from_palette)
        bar.addWidget(self.add_button)
        return bar

    def dispose(self) -> None:
        """Destroy the WebEngine surface before the host deletes this widget.

        ``deleteLater()`` alone is not enough: the profile outlives the page and
        Qt aborts the process at exit with ``0xC0000005`` ("Release of profile
        requested but WebEnginePage still not deleted").  Dropping the channel,
        stopping the loader and deleting the view *synchronously* restores the
        order the profile expects; the host then deletes the panel normally
        (L-221).
        """
        with contextlib.suppress(RuntimeError):
            self.timer.stop()
        # The guard targets a child of the view: drop the filter before that
        # child is destroyed, or Qt keeps a pointer to a dead watched object.
        if self._wheel_guard is not None and self._wheel_guard_target is not None:
            with contextlib.suppress(RuntimeError):
                self._wheel_guard_target.removeEventFilter(self._wheel_guard)
        self._wheel_guard_target = None
        view, self.view = self.view, None
        self.channel = None
        self.page_ready = False
        if view is None:
            return
        try:
            view.page().setWebChannel(None)
            view.stop()
        except (RuntimeError, AttributeError):
            pass
        view.setParent(None)
        try:
            import shiboken6

            shiboken6.delete(view)
        except Exception:                      # R-032: report, never raise
            with contextlib.suppress(RuntimeError):
                view.deleteLater()

    def _build_surface(self) -> tuple[QWidget, QWidget]:
        """Return ``(web view, fallback page)``; the fallback explains itself."""
        blank = QWidget()
        fallback = QWidget()
        layout = QVBoxLayout(fallback)
        self.fallback_label = QLabel()
        self.fallback_label.setWordWrap(True)
        layout.addWidget(self.fallback_label)
        layout.addStretch(1)

        if not page.available():
            # A trimmed build without the vendored library must still be usable.
            self.surface_error = ("vendor", "vendor/echarts.min.js")
            return blank, fallback
        try:
            from PySide6.QtWebChannel import QWebChannel
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError as error:                  # R-032: report, never raise
            self.surface_error = ("module", str(error))
            return blank, fallback

        self.surface_error = None
        self.bridge = DashboardBridge(self)
        self.bridge.layoutChanged.connect(self._on_layout_from_page)
        self.bridge.greeted.connect(lambda *_: self._after_greeting())
        view = QWebEngineView()
        # The bridge object must be named "omni": dashboard.js looks it up by
        # exactly that name on the channel.
        self.channel = QWebChannel(view.page())
        self.channel.registerObject("omni", self.bridge)
        view.page().setWebChannel(self.channel)
        view.loadFinished.connect(self._on_page_loaded)
        self.view = view
        self._install_wheel_guard()
        return view, fallback

    def _install_wheel_guard(self) -> None:
        """Attach :class:`WheelZoomGuard` to whatever receives the wheel (R-170).

        Chromium dibbles wheel events to an internal child widget, not to the
        ``QWebEngineView`` itself, so filtering only the view never fires.  The
        internal widget is exposed as ``focusProxy()`` - and it only exists once
        the renderer starts, hence the second call from ``_on_page_loaded()``.
        """
        if self.view is None:
            return
        target = self.view.focusProxy() or self.view
        if target is self._wheel_guard_target:
            return
        if self._wheel_guard is None:
            self._wheel_guard = WheelZoomGuard(self)
        if self._wheel_guard_target is not None:
            with contextlib.suppress(RuntimeError):
                self._wheel_guard_target.removeEventFilter(self._wheel_guard)
        target.installEventFilter(self._wheel_guard)
        self._wheel_guard_target = target

    def _build_footer(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.status = QLabel()
        self.status.setWordWrap(True)
        bar.addWidget(self.status, 1)
        self.hint = QLabel()
        bar.addWidget(self.hint)
        return bar

    # ------------------------------------------------------------- settings
    def _flag(self, key: str, default: bool = True) -> bool:
        text = str(self.api.settings.value(key, "1" if default else "0")).strip().lower()
        return text not in ("", "0", "false", "no", "off")

    def _number(self, key: str, default: int) -> int:
        try:
            return int(str(self.api.settings.value(key, str(default))))
        except (TypeError, ValueError):
            return default

    def _initial_window(self) -> int:
        window = self._number("window", DEFAULT_WINDOW_S)
        if not self._flag("history_window_migrated", False):
            if window == 10:
                window = DEFAULT_WINDOW_S
                self.api.settings.setValue("window", str(window))
            self.api.settings.setValue("history_window_migrated", "1")
        return window

    def _load_layout(self) -> dict:
        raw = str(self.api.settings.value("layout", "") or "")
        if raw:
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
            if isinstance(data, dict) and isinstance(data.get("widgets"), list):
                return data
        return {"widgets": []}

    def _save_layout(self) -> None:
        self.api.settings.setValue(
            "layout", json.dumps(self.layout_data, ensure_ascii=False))

    def _save_ui(self) -> None:
        # ``running`` is never written (R-176, revised): the dashboard always
        # starts refreshing, so a stored pause state would only be a value nobody
        # reads.  An older build *did* write it, hence the explicit removal -
        # leaving a stale ``running=0`` in settings.ini is exactly the kind of
        # forgotten switch that L-156 is about.
        settings = self.api.settings
        settings.remove("running")
        settings.setValue("window", str(self.seconds.value()))

    # ------------------------------------------------------------------- page
    def _load_page(self) -> None:
        if self.surface_error is not None or self.view is None:
            self.fallback_label.setText(self._fallback_text())
            self.stack.setCurrentIndex(1)
            return
        self.view.setHtml(page.build(self.api.language() == "zh"))

    def _fallback_text(self) -> str:
        if self.surface_error is None:
            return ""
        reason = self.surface_error[1]
        return self.api.t(
            "数据看板需要 QtWebEngine 与内置 ECharts（缺失：{reason}）。"
            "接收、统计与 CSV 导出不受影响。",
            "The dashboard needs QtWebEngine and the bundled ECharts ({reason} is missing)."
            " Reception, counters and CSV export are unaffected.").format(reason=reason)

    def _on_page_loaded(self, ok: bool) -> None:
        self.page_ready = bool(ok)
        if not ok:
            self.stack.setCurrentIndex(1)
            self.fallback_label.setText(
                self._fallback_text() if self.surface_error is not None
                else self.api.t("看板页面加载失败，请重启程序。",
                                "The dashboard page failed to load; restart OmniCOM."))
            self.api.notify(self.api.t("数据看板页面加载失败",
                                       "The dashboard page failed to load"), "warn")
            return
        self.stack.setCurrentIndex(0)
        self._install_wheel_guard()
        self._push_layout()

    def _after_greeting(self) -> None:
        """The page proved the bridge works: replay layout and theme."""
        self._push_layout()
        self.apply_theme()

    def _push_layout(self) -> None:
        self._call_js("setLayout", self.layout_data)

    def _call_js(self, name: str, payload) -> None:
        """Call one ``window.omniDashboard`` method with a JSON argument."""
        argument = json.dumps(payload, ensure_ascii=False)
        self._run_js("omniDashboard." + name + "(" + argument + ")")

    def _run_js(self, code: str) -> None:
        """Run script in the page, tolerating a dead or not-yet-loaded renderer."""
        if not self.page_ready or self.view is None:
            return
        try:
            self.view.page().runJavaScript(code)
        except (RuntimeError, AttributeError):
            self.page_ready = False

    # ------------------------------------------------------------------- data
    def feed(self, ts: float, data: bytes) -> None:
        """Receive bytes from the host.  Never touches a widget (may be queued)."""
        frames = self.parser.feed(data)
        if not frames:
            return
        self.arrivals.append(ts)
        while len(self.arrivals) > 1 and ts - self.arrivals[0] > 2000:
            self.arrivals.popleft()
        span = (self.arrivals[-1] - self.arrivals[0]) / 1000.0 if len(self.arrivals) > 1 else 0.0
        self.rate = (len(self.arrivals) - 1) / span if span > 0.05 else 0.0
        for frame in frames:
            if self.origin is None:
                self.origin = ts
            stamp = max(0.0, (ts - self.origin) / 1000.0)
            self.latest = max(self.latest, stamp)
            for name, value in frame.items():
                if name not in self.samples:
                    if len(self.samples) >= MAX_CHANNELS:
                        self.refused += 1
                        continue
                    self.samples[name] = deque(maxlen=MAX_POINTS)
                    self.order.append(name)
                    self._pending_names.append(name)
                samples = self.samples[name]
                if len(samples) == samples.maxlen:
                    self.evicted += 1
                samples.append((stamp, value))
                self.values[name] = value

    def _flush_names(self) -> None:
        """Add newly seen channels to the palette on the GUI thread."""
        while self._pending_names:
            name = self._pending_names.pop(0)
            if self.channel_combo.findText(name) < 0:
                self.channel_combo.addItem(name)

    def history_for_page(self) -> dict[str, list[list[float]]]:
        """Return all retained samples in the selected window for visible lines."""
        wanted: list[str] = []
        seen: set[str] = set()
        for widget in self.layout_data.get("widgets", []):
            if widget.get("kind") != "line":
                continue
            for name in (widget.get("channels") or [widget.get("channel")]):
                if name:
                    text = str(name)
                    if text not in seen:
                        seen.add(text)
                        wanted.append(text)
        window = float(self.seconds.value())
        left = max(0.0, self.latest - window) if window else 0.0

        trimmed: dict[str, list[list[float]]] = {}
        for name in wanted:
            samples = self.samples.get(name)
            if not samples:
                trimmed[name] = []
                continue
            trimmed[name] = [[round(stamp, 4), round(value, 6)]
                             for stamp, value in samples if stamp >= left]
        return trimmed

    def refresh(self) -> None:
        """Push one frame into the page; ECharts is never touched from Python."""
        self._flush_names()
        if self.isVisible() and self.page_ready and self.live.isChecked():
            started = time.perf_counter()
            self.flush()
            self.render_count += 1
            self.render_ms.append((time.perf_counter() - started) * 1000)
        self.refresh_status()

    def flush(self) -> None:
        """Push the current values immediately (also what the tests drive)."""
        if not self.page_ready:
            return
        payload = {"values": self.values, "history": self.history_for_page()}
        self._call_js("setValues", payload)

    # ---------------------------------------------------------------- widgets
    def add_widget_from_palette(self) -> str | None:
        kind = str(self.kind_combo.currentData() or "line")
        channel = self.channel_combo.currentText().strip()
        if not channel:
            self.api.notify(self.api.t("先选择一个通道", "Pick a channel first"), "warn")
            return None
        return self.add_widget({
            "kind": kind,
            "channel": channel,
            "title": self.title_edit.text().strip() or channel,
            "unit": self.unit_edit.text().strip(),
            "min": self._float_or(self.min_edit.text(), 0.0),
            "max": self._float_or(self.max_edit.text(), 100.0),
        })

    def add_widget(self, payload: dict) -> str | None:
        """Add one widget; the geometry is chosen here so cards tile nicely."""
        kind = str(payload.get("kind") or "line")
        width, height = DEFAULT_SIZE.get(kind, (320, 224))
        index = len(self.layout_data.get("widgets", []))
        widget = dict(payload)
        widget.setdefault("id", f"w{index + 1}-{int(time.time() * 1000) % 100000}")
        widget.setdefault("x", 16 + (index % 3) * (width + 16))
        widget.setdefault("y", 16 + (index // 3) * (height + 16))
        widget.setdefault("w", width)
        widget.setdefault("h", height)
        self.layout_data.setdefault("widgets", []).append(widget)
        self._save_layout()
        if self.page_ready:
            self._call_js("addWidget", widget)
        return str(widget["id"])

    @staticmethod
    def _float_or(text: str, default: float) -> float:
        try:
            return float(str(text).strip())
        except (TypeError, ValueError):
            return default

    def _on_layout_from_page(self, payload: str) -> None:
        """A drag, a resize or a delete finished in the page."""
        try:
            data = json.loads(payload)
        except ValueError:
            return
        if isinstance(data, dict) and isinstance(data.get("widgets"), list):
            self.layout_data = data
            self._save_layout()
            self.refresh_status()

    def clear_widgets(self) -> None:
        """Remove every widget (received data is kept)."""
        self.layout_data = {"widgets": []}
        self._save_layout()
        self._push_layout()
        self.refresh_status()

    def _on_kind_changed(self, *_args) -> None:
        kind = str(self.kind_combo.currentData() or "line")
        # A progress bar is meaningless without the range it fills between, so
        # bar and gauge both own 范围; the unit makes the number readable.
        for widget in (self.range_label, self.min_edit, self.max_edit):
            widget.setVisible(kind in ("gauge", "bar"))
        for widget in (self.unit_label, self.unit_edit):
            widget.setVisible(kind in ("gauge", "value", "bar"))

    # -------------------------------------------------------------- commands
    def _on_run_toggled(self, running: bool) -> None:
        self._style_run(bool(running))
        self._save_ui()

    def _style_run(self, running: bool) -> None:
        """Pause/Resume, with the running state as the normal one (R-167/R-176).

        The dashboard refreshes from the moment it is opened, so the button's
        *default* face is 暂停 (the action that stops the refresh) in the accent
        colour - a grey 运行 button on a live dashboard reads as "this is
        switched off".  Pausing freezes the picture only: acquisition, the frame
        counter and the widget values keep running, hence 继续 rather than
        运行/停止.

        The pause is **not remembered** across windows or restarts (R-176,
        revised 2026-11-06): every dashboard opens refreshing and shows 暂停, so
        nobody can be met by a frozen picture with no obvious reason.
        """
        self.live.setText(self.api.t("暂停", "Pause") if running
                          else self.api.t("继续", "Resume"))
        self.live.setObjectName("Primary" if running else "Ghost")
        self.live.style().unpolish(self.live)
        self.live.style().polish(self.live)
        self.live.update()

    def clear_data(self) -> None:
        self.samples.clear()
        self.values.clear()
        self.order.clear()
        self._pending_names.clear()
        self.channel_combo.clear()
        self.parser = AsciiParser()
        self.origin = None
        self.latest = 0.0
        self.arrivals.clear()
        self.rate = 0.0
        self.evicted = self.refused = 0
        self.render_count = 0
        self.render_ms.clear()
        self.refresh_status()

    # ---------------------------------------------------------------- export
    def save_image(self, filename: str) -> None:
        """Grab the surface exactly as the user sees it."""
        target = self.view if (self.view is not None and self.page_ready) else self
        target.grab().save(filename)

    def save_image_dialog(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self, self.api.t("保存看板截图", "Save dashboard image"), "dashboard.png",
            "PNG (*.png);;JPEG (*.jpg)")
        if filename:
            try:
                self.save_image(filename)
            except OSError as exc:
                self.api.notify(str(exc), "error")

    def export_csv(self, filename: str) -> None:
        with open(filename, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("receive_time_s", "channel", "value"))
            for name, samples in self.samples.items():
                for stamp, value in samples:
                    writer.writerow((stamp, name, value))

    def export_dialog(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self, self.api.t("导出看板数据", "Export dashboard data"), "dashboard.csv",
            "CSV (*.csv)")
        if filename:
            try:
                self.export_csv(filename)
            except OSError as exc:
                self.api.notify(str(exc), "error")

    # ----------------------------------------------------------------- theme
    def apply_theme(self) -> None:
        theme = self.api.theme()
        payload = {
            "bg": theme.BG,
            "card": theme.CARD,
            "ink": theme.INK,
            "ink2": theme.INK2,
            "accent": theme.TEAL,
            "danger": theme.DANGER,
            "ok": theme.OK,
            "line": _STROKE,
            "lineSoft": _STROKE_SOFT,
        }
        self._call_js("setTheme", payload)

    # ---------------------------------------------------------------- status
    def refresh_status(self) -> None:
        template = self.api.t(
            "{frames} 帧 · {rate:.1f} 帧/秒 · {channels} 通道 · 控件 {widgets}"
            " · 无效 {invalid} · 超长 {oversized} · 淘汰 {evicted} · 超限 {refused}",
            "{frames} frames · {rate:.1f} fps · {channels} channels · widgets {widgets}"
            " · invalid {invalid} · oversized {oversized}"
            " · evicted {evicted} · refused {refused}")
        self.status.setText(template.format(
            frames=self.parser.frames, rate=self.rate, channels=len(self.samples),
            widgets=len(self.layout_data.get("widgets", [])),
            invalid=self.parser.invalid, oversized=self.parser.oversized,
            evicted=self.evicted, refused=self.refused))

    def retranslate(self) -> None:
        self._style_run(self.live.isChecked())
        self.live.setToolTip(self.api.t(
            "暂停=冻结画面，采集与计数继续；继续=恢复刷新",
            "Pause freezes the picture while acquisition keeps running; "
            "Resume brings the refresh back"))
        self.seconds_label.setText(self.api.t("时间窗 / 秒", "Window / s"))
        self.seconds.setSpecialValueText(self.api.t("全部", "All"))
        self.clear_widgets_button.setText(self.api.t("清空控件", "Clear widgets"))
        self.clear_widgets_button.setToolTip(self.api.t(
            "移除所有卡片，接收的数据保留", "Remove every card; received data is kept"))
        self.image_button.setText(self.api.t("保存截图", "Save image"))
        self.export_button.setText(self.api.t("导出 CSV", "Export CSV"))
        self.clear_button.setToolTip(self.api.t("清空接收数据", "Clear received data"))
        self.kind_label.setText(self.api.t("控件", "Widget"))
        self.channel_label.setText(self.api.t("通道", "Channel"))
        self.title_edit.setPlaceholderText(self.api.t("标题（可空）", "Title (optional)"))
        self.unit_label.setText(self.api.t("单位", "Unit"))
        self.range_label.setText(self.api.t("范围", "Range"))
        self.min_edit.setPlaceholderText(self.api.t("最小", "min"))
        self.max_edit.setPlaceholderText(self.api.t("最大", "max"))
        self.add_button.setText(self.api.t("添加控件", "Add widget"))
        for index, kind in enumerate(KINDS):
            zh, en = _KIND_LABELS[kind]
            self.kind_combo.setItemText(index, zh if self.api.language() == "zh" else en)
        self.hint.setText(self.api.t("格式：[OmniCOM-chart] A=1 B=2",
                                     "Payload: [OmniCOM-chart] A=1 B=2"))
        if self.surface_error is not None:
            self.fallback_label.setText(self._fallback_text())
        self.refresh_status()


def create(api) -> QWidget:
    return DashboardPanel(api)
