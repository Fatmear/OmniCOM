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
import math
import re
import time
from collections import deque
from itertools import islice

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from . import page
from .protocol import AsciiParser

MAX_CHANNELS = 128
MAX_POINTS = 5000
DEFAULT_WINDOW_S = 0
FRAME_MS = 33
#: Slowest the live timer may become when the renderer cannot keep up.
MAX_FRAME_MS = 200
#: How many samples one channel may fall behind the page before Python stops
#: waiting for acks and simply sends the retained window again.  A page that is
#: 60 frames behind has already scrolled the live edge off screen, so a full
#: resync costs one large message instead of an ever-growing backlog.
#: How long one batch may wait for its acknowledgement before Python stops
#: trusting it.  A lost ack (the page threw while painting, or Chromium
#: suspended the hidden tab) must degrade into a redraw, never into a dashboard
#: that silently stops refreshing (L-307).  1.5 s is ~45 normal frames - longer
#: than any real paint, far shorter than a user noticing a frozen chart.
ACK_TIMEOUT_S = 1.5

#: Points a curve draws per frame by default.  Measured on this machine for a
#: single new sample per frame: 300 points = 0.84 ms, 600 = 5.1 ms, 5000 =
#: 22 ms.  The cost is super-linear, so 300 keeps several curve cards smooth at
#: 30 fps.  Retention stays at ``MAX_POINTS``; only the drawn window shrinks,
#: and the user can raise it.
DEFAULT_VISIBLE = 300

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


def source_names(text: str) -> list[str]:
    return list(dict.fromkeys(
        name.strip() for name in re.split(r"[,;\s]+", text) if name.strip()
    ))


class SourceDelegate(QStyledItemDelegate):
    def __init__(self, theme, parent) -> None:
        super().__init__(parent)
        self.theme = theme

    def paint(self, painter, option, index) -> None:
        painter.save()
        try:
            theme = self.theme
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
            background = (theme.TEAL_SOFT if selected else
                          theme.WASH_HOVER if hovered else theme.CARD)
            painter.fillRect(option.rect, QColor(background))
            text_rect = option.rect.adjusted(theme.CHECK_SIZE + 20, 0, -8, 0)
            painter.setFont(option.font)
            painter.setPen(QColor(theme.INK))
            text = option.fontMetrics.elidedText(
                str(index.data(Qt.ItemDataRole.DisplayRole)),
                Qt.TextElideMode.ElideRight, text_rect.width(),
            )
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter, text)
            size = theme.CHECK_SIZE
            box = QRectF(option.rect.left() + 8,
                         option.rect.center().y() - size / 2, size, size)
            checked = (index.data(Qt.ItemDataRole.CheckStateRole)
                       == Qt.CheckState.Checked.value)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            border = theme.TEAL if checked else theme.LINE_STRONG
            painter.setPen(QPen(QColor(border), 1))
            painter.setBrush(QColor(theme.TEAL if checked else theme.CARD))
            radius = theme.CHECK_RADIUS
            painter.drawRoundedRect(box, radius, radius)
            if checked:
                painter.setPen(QPen(
                    QColor(theme.CARD), 1.8, Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin,
                ))
                start = QPointF(box.left() + size * 0.23,
                                box.top() + size * 0.51)
                corner = QPointF(box.left() + size * 0.43,
                                 box.top() + size * 0.71)
                end = QPointF(box.left() + size * 0.78,
                              box.top() + size * 0.29)
                painter.drawLine(start, corner)
                painter.drawLine(corner, end)
        finally:
            painter.restore()


class SourceList(QListWidget):
    def mousePressEvent(self, event) -> None:
        self._pressed_item = None
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if item is not None:
                self._pressed_item = item
                self.setCurrentItem(item)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if item is not None and item is getattr(
                    self, "_pressed_item", None):
                checked = item.checkState() == Qt.CheckState.Checked
                item.setCheckState(Qt.CheckState.Unchecked if checked
                                   else Qt.CheckState.Checked)
            self._pressed_item = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SourcesDialog(QDialog):
    def __init__(self, api, available, selected, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(api.t("曲线数据来源", "Curve data sources"))
        self.resize(400, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(api.t("通道", "Channels")))
        self.channels = SourceList(self)
        self.channels.setMouseTracking(True)
        self.channels.setUniformItemSizes(True)
        self.channels.setSpacing(2)
        theme = api.theme()
        self.channels.setItemDelegate(SourceDelegate(theme, self.channels))
        for name in dict.fromkeys([*available, *selected]):
            item = QListWidgetItem(name, self.channels)
            height = max(theme.CTRL_H, self.fontMetrics().height() + 14)
            item.setSizeHint(QSize(0, height))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in selected
                               else Qt.CheckState.Unchecked)
        layout.addWidget(self.channels, 1)
        layout.addWidget(QLabel(api.t("数据来源", "Data sources")))
        self.names = QLineEdit(", ".join(selected), self)
        self.names.setPlaceholderText("AA, BB, CC")
        layout.addWidget(self.names)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton(api.t("取消", "Cancel"), self)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.confirm = QPushButton(api.t("确定", "OK"), self)
        self.confirm.setObjectName("Primary")
        self.confirm.clicked.connect(self.accept)
        actions.addWidget(self.confirm)
        layout.addLayout(actions)
        self.channels.itemChanged.connect(self._selection_changed)
        self.names.textChanged.connect(self._text_changed)
        self._text_changed()

    def sources(self) -> list[str]:
        return source_names(self.names.text())

    def _selection_changed(self, _item) -> None:
        items = [self.channels.item(index) for index in range(self.channels.count())]
        known = {item.text() for item in items}
        selected = [item.text() for item in items
                    if item.checkState() == Qt.CheckState.Checked]
        selected.extend(name for name in self.sources() if name not in known)
        self.names.setText(", ".join(selected))

    def _text_changed(self, *_args) -> None:
        selected = self.sources()
        self.confirm.setEnabled(bool(selected))
        self.channels.blockSignals(True)
        try:
            for index in range(self.channels.count()):
                item = self.channels.item(index)
                item.setCheckState(Qt.CheckState.Checked if item.text() in selected
                                   else Qt.CheckState.Unchecked)
        finally:
            self.channels.blockSignals(False)


class ColorSwatch(QPushButton):
    def __init__(self, api, color, title, parent=None) -> None:
        super().__init__(parent)
        self.api = api
        self.title = title
        self.setObjectName("LampColorSwatch")
        self.setToolTip(title)
        self.setMinimumSize(api.theme().CTRL_H, api.theme().CTRL_H)
        self.clicked.connect(self.pick)
        self.set_color(color)

    def set_color(self, color) -> None:
        value = QColor(color)
        if not value.isValid():
            raise ValueError("invalid lamp color")
        self.color_name = value.name()
        theme = self.api.theme()
        self.setStyleSheet(
            f"QPushButton#LampColorSwatch {{ background: {self.color_name}; "
            f"border: 1px solid {theme.LINE_STRONG}; "
            f"border-radius: {theme.RADIUS_CTRL}px; padding: 0; }}"
            f"QPushButton#LampColorSwatch:hover {{ border: 2px solid {theme.INK}; }}"
        )

    def pick(self) -> None:
        dialog = QColorDialog(QColor(self.color_name), self)
        dialog.setWindowTitle(self.title)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
        labels = {
            "Basic colors": "基本颜色", "Custom colors": "自定义颜色",
            "Hue": "色相", "Sat": "饱和度", "Val": "亮度",
            "Red": "红", "Green": "绿", "Blue": "蓝",
            "Alpha channel": "透明度", "HTML": "颜色代码",
            "Pick Screen Color": "拾取屏幕颜色",
            "Add to Custom Colors": "加入自定义颜色",
            "OK": "确定", "Cancel": "取消",
        }
        for child in dialog.findChildren(QLabel) + dialog.findChildren(QPushButton):
            text = child.text().replace("&", "")
            key = text.removesuffix(":")
            if key in labels:
                child.setText(self.api.t(labels[key], key) + (":" if text.endswith(":") else ""))
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.set_color(dialog.selectedColor())
        finally:
            dialog.deleteLater()


def normalize_lamp_rules(raw: dict) -> dict:
    if raw.get("colorRules") is None:
        return {}
    rules = raw["colorRules"]
    if not isinstance(rules, list) or len(rules) > 128:
        raise ValueError("invalid lamp rules")

    def color_name(value):
        if value is None:
            return None
        if not isinstance(value, str) or not QColor(value).isValid():
            raise ValueError("invalid lamp color")
        return QColor(value).name()

    normalized = []
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict) or isinstance(rule.get("value"), bool):
            raise ValueError("invalid lamp value")
        try:
            value = float(rule["value"])
            color = color_name(rule["color"])
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError("invalid lamp rule") from exc
        if not math.isfinite(value) or value in seen:
            raise ValueError("invalid or duplicate lamp value")
        seen.add(value)
        normalized.append({"value": value, "color": color})
    return {
        "colorRules": normalized,
        "fallbackColor": color_name(raw.get("fallbackColor")),
    }


class LampColorsDialog(QDialog):
    def __init__(self, api, widget, parent=None) -> None:
        super().__init__(parent)
        self.api = api
        self.setWindowTitle(api.t("灯数值与颜色", "Lamp values and colors"))
        self.resize(460, 380)
        layout = QVBoxLayout(self)
        self.match_values = QCheckBox(
            api.t("按数值匹配", "Match values"), self
        )
        self.match_values.setChecked(
            isinstance(widget.get("colorRules"), list)
        )
        layout.addWidget(self.match_values)
        self.legacy_panel = QWidget(self)
        legacy = QVBoxLayout(self.legacy_panel)
        legacy.setContentsMargins(0, 0, 0, 0)
        self.swatches = {}
        for key, label, default in (
            ("onColor", api.t("亮", "On"), api.theme().TEAL),
            ("offColor", api.t("灭", "Off"), "#C8D0DC"),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            swatch = ColorSwatch(api, widget.get(key) or default, label, self)
            self.swatches[key] = swatch
            row.addWidget(swatch)
            row.addStretch(1)
            legacy.addLayout(row)
        layout.addWidget(self.legacy_panel)
        self.rule_panel = QWidget(self)
        rule_layout = QVBoxLayout(self.rule_panel)
        rule_layout.setContentsMargins(0, 0, 0, 0)
        headings = QHBoxLayout()
        headings.addWidget(QLabel(api.t("数值", "Value")))
        headings.addWidget(QLabel(api.t("颜色", "Color")))
        rule_layout.addLayout(headings)
        scroll = QScrollArea(self.rule_panel)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(140)
        self.rule_host = QWidget(scroll)
        self.rule_layout = QVBoxLayout(self.rule_host)
        self.rule_layout.setContentsMargins(0, 0, 0, 0)
        self.rule_layout.addStretch(1)
        scroll.setWidget(self.rule_host)
        rule_layout.addWidget(scroll)
        self.rule_rows = []
        self.add_rule_button = QPushButton(
            api.t("添加数值", "Add value"), self
        )
        self.add_rule_button.clicked.connect(self.add_rule)
        rule_layout.addWidget(self.add_rule_button)
        fallback = QHBoxLayout()
        fallback.addWidget(QLabel(api.t("其它值", "Other values")))
        fallback_color = (
            widget.get("fallbackColor")
            if self.match_values.isChecked()
            else widget.get("offColor", "#C8D0DC")
        )
        self.fallback_swatch = ColorSwatch(
            api,
            fallback_color or "#C8D0DC",
            api.t("其它值颜色", "Other values color"),
            self,
        )
        self.fallback_none = QCheckBox(api.t("无色", "No color"), self)
        self.fallback_none.setChecked(fallback_color is None)
        self.fallback_none.toggled.connect(self._validate)
        fallback.addWidget(self.fallback_swatch)
        fallback.addWidget(self.fallback_none)
        fallback.addStretch(1)
        rule_layout.addLayout(fallback)
        layout.addWidget(self.rule_panel)
        self.error = QLabel(
            api.t("数值必须有效且不能重复", "Values must be valid and unique"),
            self,
        )
        layout.addWidget(self.error)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton(api.t("取消", "Cancel"), self)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.confirm = QPushButton(api.t("确定", "OK"), self)
        self.confirm.clicked.connect(self.accept)
        actions.addWidget(self.confirm)
        layout.addLayout(actions)
        self.match_values.toggled.connect(self._validate)
        rules = widget.get("colorRules")
        if not isinstance(rules, list):
            rules = [
                {
                    "value": 1,
                    "color": widget.get("onColor") or api.theme().TEAL,
                }
            ]
        for rule in rules:
            self._append_rule(rule["value"], rule["color"])
        self._validate()

    def _append_rule(self, value, color) -> None:
        holder = QWidget(self.rule_host)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        editor = QLineEdit(repr(float(value)), holder)
        editor.setMinimumWidth(80)
        editor.setAccessibleName(self.api.t("数值", "Value"))
        swatch = ColorSwatch(
            self.api,
            color or self.api.theme().TEAL,
            self.api.t("匹配颜色", "Matched color"),
            holder,
        )
        no_color = QCheckBox(self.api.t("无色", "No color"), holder)
        no_color.setChecked(color is None)
        remove = self.api.glyph("close")(parent=holder)
        remove.setToolTip(self.api.t("删除数值", "Remove value"))
        remove.clicked.connect(lambda: self.remove_rule(holder))
        row.addWidget(editor, 1)
        row.addWidget(swatch)
        row.addWidget(no_color)
        row.addWidget(remove)
        self.rule_rows.append((holder, editor, swatch, no_color))
        self.rule_layout.insertWidget(self.rule_layout.count() - 1, holder)
        editor.textChanged.connect(self._validate)
        no_color.toggled.connect(self._validate)
        self._validate()

    def add_rule(self) -> None:
        if len(self.rule_rows) >= 128:
            return
        used = set()
        for _, editor, _, _ in self.rule_rows:
            with contextlib.suppress(ValueError):
                used.add(float(editor.text()))
        value = 0
        while value in used:
            value += 1
        self._append_rule(value, self.api.theme().TEAL)

    def remove_rule(self, holder) -> None:
        self.rule_rows = [
            row for row in self.rule_rows if row[0] is not holder
        ]
        self.rule_layout.removeWidget(holder)
        holder.hide()
        holder.deleteLater()
        self._validate()

    def _rules_payload(self) -> dict:
        return normalize_lamp_rules(
            {
                "colorRules": [
                    {
                        "value": editor.text(),
                        "color": None
                        if no_color.isChecked()
                        else swatch.color_name,
                    }
                    for _, editor, swatch, no_color in self.rule_rows
                ],
                "fallbackColor": None
                if self.fallback_none.isChecked()
                else self.fallback_swatch.color_name,
            }
        )

    def _validate(self, *_args) -> None:
        matching = self.match_values.isChecked()
        self.legacy_panel.setVisible(not matching)
        self.rule_panel.setVisible(matching)
        self.fallback_swatch.setEnabled(not self.fallback_none.isChecked())
        for _, _, swatch, no_color in self.rule_rows:
            swatch.setEnabled(not no_color.isChecked())
        valid = True
        if matching:
            try:
                self._rules_payload()
            except (ValueError, TypeError, OverflowError):
                valid = False
        self.error.setVisible(not valid)
        self.confirm.setEnabled(valid)
        self.add_rule_button.setEnabled(len(self.rule_rows) < 128)

    def colors(self) -> dict:
        if self.match_values.isChecked():
            return self._rules_payload()
        return {
            "colorRules": None,
            "fallbackColor": None,
            **{
                key: swatch.color_name for key, swatch in self.swatches.items()
            },
        }


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
    #: Emitted when the page confirms it consumed batch ``epoch``.
    acked = Signal(int)
    #: The paint cost the page reported for that batch, in milliseconds.
    painted = Signal(float)
    sourcesRequested = Signal(str)

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
        elif name == "sources":
            self.sourcesRequested.emit(str(data.get("id") or ""))
        elif name == "acked":
            # The page finished the batch we handed it.  Acking is what turns
            # "send everything again" into "send only what is new": without it
            # Python cannot tell a busy renderer from an idle one and has to
            # re-send the whole window every frame.
            try:
                epoch = int(data.get("epoch") or 0)
            except (TypeError, ValueError):
                return
            self.acked.emit(epoch)
            try:
                self.painted.emit(float(data.get("cost") or 0.0))
            except (TypeError, ValueError):
                return


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
        #: Heading shown above the three number boxes inside the menu (R-315).
        self.options_title = ""
        self.bridge: DashboardBridge | None = None
        self.view = None
        #: Incremental delivery state.  Sending the whole retained window on every
        #: frame was the reported "看板还在展示很旧的数据": a 33 ms timer kept
        #: queueing a multi-megabyte ``runJavaScript`` while a slow renderer was
        #: still busy with the previous one, so the page fell further and further
        #: behind and eventually painted ancient samples.  Python now remembers
        #: which samples the page already has and only ships the new tail.
        self._sent: dict[str, int] = {}
        self._received: dict[str, int] = {}
        self._epoch = 0
        self._revision = 0
        self._sent_revision = 0
        self._last_revision = 0
        self._pending_ack = 0
        self._acked_epoch = 0
        self._resync_needed = True
        #: ``{channel: sample count}`` covered by the batch currently in flight.
        #: The page's ``acked`` reply promotes it into ``_sent``; only then are
        #: those samples known to be on screen (R-318).
        self._last_sent: dict[str, int] = {}
        #: When the batch currently being awaited was handed to the page.
        #: Back-pressure is only safe with a deadline: an acknowledgement that
        #: never arrives (a card threw while painting, the renderer was frozen
        #: while the tab was hidden) used to stop *every* curve for good
        #: (L-307).  Past this deadline the next frame is a full resync.
        self._ack_started = 0.0
        self.ack_timeouts = 0
        #: Whether the page was visible during the previous timer tick.  Coming
        #: back from another tab (HEX, terminal) needs a full redraw: the page
        #: may have missed frames while Chromium was not compositing it.
        self._was_visible = False
        #: Full-window redraws done because the page fell too far behind.
        self.resyncs = 0
        #: Frames dropped/delayed because the previous batch was not acked yet.
        self.throttled = 0
        #: Chart lines received through a log replay (R-302).
        self.replayed_lines = 0
        self._replay_active = False
        #: Milliseconds the page reported for its last paint.  Used to slow the
        #: timer down instead of queueing frames the renderer cannot absorb.
        self.paint_ms = 0.0
        #: Current live interval; 33 ms is the target, raised up to MAX_FRAME_MS.
        self.frame_ms = FRAME_MS

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
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_if_ready)
        self.timer.start()

        api.on_rx(self.feed)
        api.on_replay(self.replay_line)
        api.on_language(lambda *_: self.retranslate())
        api.on_theme(self.apply_theme)
        api.on_unload(self.timer.stop)
        api.on_unload(self._flush_timer.stop)

        self.retranslate()
        self.apply_theme()
        self._load_page()
        self._on_kind_changed()
        self.refresh_status()

    # ------------------------------------------------------------------ build
    def _build_toolbar(self) -> QHBoxLayout:
        """Only the two controls used all the time, plus one menu (R-315).

        The bar used to carry 暂停 / three labelled spin boxes / 清空控件 /
        保存截图 / 导出 CSV / 导出看板 / 导入看板 / 清除数据.  On a small window
        that wrapped or squeezed, and the numbers were settings rather than
        actions.  Everything except 暂停/继续 and 清除控件 now lives in 菜单.
        """
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

        # The three numbers stay real widgets with their saved settings and
        # signal wiring; they are simply placed inside the menu now.
        self.seconds_label = QLabel()
        self.seconds = QSpinBox(self)
        self.seconds.setRange(0, 3600)
        self.seconds.setValue(self._initial_window())
        self.seconds.valueChanged.connect(lambda *_: self._on_window_changed())
        self.points_label = QLabel()
        self.points = QSpinBox(self)
        self.points.setRange(0, MAX_POINTS)
        self.points.setSingleStep(100)
        self.points.setValue(self._number("points", 0))
        self.points.valueChanged.connect(lambda *_: self._on_points_changed())
        self.visible_label = QLabel()
        self.drawn = QSpinBox(self)
        self.drawn.setRange(100, MAX_POINTS)
        self.drawn.setSingleStep(100)
        self.drawn.setValue(self._visible_points())
        self.drawn.valueChanged.connect(lambda *_: self._on_points_changed())
        # These three are data holders for the settings that live in the menu.
        # They must never be shown on their own: a child widget with a parent is
        # painted by Qt even when it is not in a layout, which is why a stray
        # "5000" box appeared right after the pause button (R-316).
        for box in (self.seconds, self.points, self.drawn):
            box.hide()

        self.clear_widgets_button = QPushButton()
        self.clear_widgets_button.clicked.connect(self.clear_widgets)
        bar.addWidget(self.clear_widgets_button)
        # Every other action is created here but only shown from the menu, so
        # the translations and the existing connections keep working.
        self.menu_button = QPushButton()
        self.menu_button.clicked.connect(self._show_menu)
        bar.addWidget(self.menu_button)
        self.export_config_button = QPushButton()
        self.export_config_button.clicked.connect(self.export_config_dialog)
        self.import_config_button = QPushButton()
        self.import_config_button.clicked.connect(self.import_config_dialog)
        self.image_button = QPushButton()
        self.image_button.clicked.connect(self.save_image_dialog)
        self.export_button = QPushButton()
        self.export_button.clicked.connect(self.export_dialog)
        clear = self.api.glyph("clear")()
        clear.clicked.connect(self.clear_data)
        self.clear_button = clear
        return bar

    def _show_menu(self) -> None:
        """Every action that is not used constantly (R-315)."""
        menu = self.build_menu()
        menu.exec(self.menu_button.mapToGlobal(
            self.menu_button.rect().bottomLeft()))

    def build_menu(self, parent=None):
        """Build (but do not show) the dashboard menu.

        Split from ``_show_menu`` so the menu can be inspected without entering
        ``QMenu.exec()``, which runs a nested event loop and blocks a test.
        """
        menu = self.api.menu(parent or self)
        menu.addAction(self.image_button.text(), self.save_image_dialog)
        menu.addAction(self.export_button.text(), self.export_dialog)
        menu.addSeparator()
        menu.addAction(self.export_config_button.text(), self.export_config_dialog)
        menu.addAction(self.import_config_button.text(), self.import_config_dialog)
        menu.addSeparator()
        menu.addAction(self.clear_button.toolTip(), self.clear_data)
        menu.addSeparator()
        title = menu.addAction(self.options_title or "")
        title.setEnabled(False)
        for label, box in ((self.seconds_label, self.seconds),
                           (self.points_label, self.points),
                           (self.visible_label, self.drawn)):
            action = QWidgetAction(menu)
            holder = QWidget(menu)
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(18, 2, 12, 2)
            text = QLabel(label.text(), holder)
            text.setMinimumWidth(110)
            layout.addWidget(text)
            # A *fresh* spin box per menu: ``QWidgetAction.setDefaultWidget()``
            # hands ownership to the action, so the value-holding widget used by
            # the panel would be destroyed when the menu closes (R-316).
            editor = QSpinBox(holder)
            editor.setRange(box.minimum(), box.maximum())
            editor.setSingleStep(box.singleStep())
            editor.setSpecialValueText(box.specialValueText())
            editor.setValue(box.value())
            editor.valueChanged.connect(box.setValue)
            layout.addWidget(editor)
            action.setDefaultWidget(holder)
            menu.addAction(action)
        return menu

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
        self.sources_button = self.api.glyph("edit")(parent=self)
        self.sources_button.clicked.connect(self.choose_palette_sources)
        bar.addWidget(self.sources_button)
        self.lamp_on_color = ColorSwatch(
            self.api, self.api.theme().TEAL,
            self.api.t("亮灯颜色", "Lamp on color"), self,
        )
        self.lamp_off_color = ColorSwatch(
            self.api, "#C8D0DC", self.api.t("灭灯颜色", "Lamp off color"), self,
        )
        bar.addWidget(self.lamp_on_color)
        bar.addWidget(self.lamp_off_color)
        self.lamp_rule_config = None
        self.lamp_rules_button = self.api.glyph("edit")(parent=self)
        self.lamp_rules_button.clicked.connect(self.choose_palette_lamp_colors)
        bar.addWidget(self.lamp_rules_button)
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
            self._flush_timer.stop()
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
        self.bridge.sourcesRequested.connect(self.edit_widget_sources)
        self.bridge.greeted.connect(lambda *_: self._after_greeting())
        self.bridge.acked.connect(self._on_acked)
        self.bridge.painted.connect(self._on_paint_cost)
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
        settings.setValue("points", str(self.points.value()))
        settings.setValue("visible", str(self.drawn.value()))

    def _on_window_changed(self) -> None:
        self._save_ui()
        self.request_resync()

    def _on_points_changed(self) -> None:
        """Re-cap every channel and redraw from the new window (R-301)."""
        self._save_ui()
        limit = self._capacity()
        for name, samples in list(self.samples.items()):
            if samples.maxlen != limit:
                # ``deque.maxlen`` is read-only, so re-cap by rebuilding: keep
                # the newest ``limit`` samples instead of mutating internals.
                kept = list(samples)[-limit:]
                self.evicted += len(samples) - len(kept)
                self.samples[name] = deque(kept, maxlen=limit)
        self._push_points()
        self.request_resync()

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
        self._after_greeting()

    def _after_greeting(self) -> None:
        """Replay the complete page state once JavaScript can receive it."""
        if not self.page_ready:
            return
        self._push_points()
        self.apply_theme()
        self._push_layout()
        self.request_resync()
        if not self._replay_active:
            self.flush()

    def _on_acked(self, epoch: int) -> None:
        """The page consumed batch ``epoch``; release the next one."""
        if self._pending_ack and epoch == self._pending_ack:
            self._acked_epoch = max(self._acked_epoch, epoch)
            self._pending_ack = 0
            # Only now do these samples count as delivered: a batch built while
            # the renderer was suspended must be re-sent, not remembered as
            # already on screen (R-318).
            self._remember_sent(self._last_sent)
            self._sent_revision = self._last_revision
            if self.has_fresh_data() and self.isVisible() and self.live.isChecked():
                self._flush_timer.start(0)

    def _flush_if_ready(self) -> None:
        if self._pending_ack or not self.page_ready or not self.isVisible() or not self.live.isChecked():
            return
        if not self.has_fresh_data():
            return
        elapsed_ms = (time.perf_counter() - self._ack_started) * 1000
        remaining_ms = self.frame_ms - elapsed_ms
        if remaining_ms > 1:
            self._flush_timer.start(int(remaining_ms) + 1)
        else:
            self.flush_live()

    def _on_paint_cost(self, cost_ms: float) -> None:
        """Set total batch pacing, including time already spent rendering."""
        self.paint_ms = max(0.0, float(cost_ms))
        target = FRAME_MS
        if self.paint_ms > 0:
            target = max(FRAME_MS, min(MAX_FRAME_MS, int(self.paint_ms) + 8))
        if target != self.frame_ms:
            self.frame_ms = target
            self.timer.setInterval(target)

    def _push_layout(self) -> None:
        self._call_js("setLayout", self.layout_data)

    def _push_points(self) -> None:
        """Tell the page the retention cap and the drawn window (R-301)."""
        self._call_js("setPoints", {"limit": self._capacity(),
                                    "visible": self._visible_points()})

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
                    self.samples[name] = deque(maxlen=self._capacity())
                    self.order.append(name)
                    self._pending_names.append(name)
                samples = self.samples[name]
                if len(samples) == samples.maxlen:
                    self.evicted += 1
                samples.append((stamp, value))
                self._received[name] = self._received.get(name, 0) + 1
                self.values[name] = value
                self._revision += 1

    def _capacity(self) -> int:
        """Samples kept per channel.

        The user asked for a configurable number of points per curve (R-301): a
        long session otherwise either loses detail (small cap) or pushes an
        ever-growing document into the renderer (large cap).  0 means "the
        historical 5000", so old settings and every existing test keep working.
        """
        limit = self._number("points", 0)
        if limit <= 0:
            return MAX_POINTS
        return min(MAX_POINTS, max(100, limit))

    def _visible_points(self) -> int:
        """How many points one curve hands to ECharts per frame.

        Measured on this machine: 5000 points = 22 ms/card/frame, 600 points =
        6 ms.  Two curves at 30 fps therefore need the smaller number; the
        retained history is untouched, so 曲线点数 can still be raised.
        """
        value = self._number("visible", DEFAULT_VISIBLE)
        if value <= 0:
            return DEFAULT_VISIBLE
        return max(100, min(MAX_POINTS, value))

    def replay_line(self, seconds: float, payload: str) -> None:
        """Host replay: one saved log line carrying a chart frame (R-302).

        The payload goes through the *same* ``parse_line`` the port uses, and the
        samples land in the *same* per-channel buffers, so a replayed session
        shows up with exactly the cards and channels it had live - no second
        parser and no second view.
        """
        values = self.parser.parse_line(str(payload).encode("utf-8", "replace"))
        if not values:
            return
        stamp = max(0.0, float(seconds))
        self.latest = max(self.latest, stamp)
        self.replayed_lines += 1
        for name, value in values.items():
            if name not in self.samples:
                if len(self.samples) >= MAX_CHANNELS:
                    self.refused += 1
                    continue
                self.samples[name] = deque(maxlen=self._capacity())
                self.order.append(name)
                self._pending_names.append(name)
            samples = self.samples[name]
            if len(samples) == samples.maxlen:
                self.evicted += 1
            samples.append((stamp, value))
            self._received[name] = self._received.get(name, 0) + 1
            self.values[name] = value
            self._revision += 1

    def _flush_names(self) -> None:
        """Add newly seen channels to the palette on the GUI thread."""
        while self._pending_names:
            name = self._pending_names.pop(0)
            if self.channel_combo.findText(name) < 0:
                self.channel_combo.addItem(name)

    def _visible_channels(self) -> list[str]:
        """Channels any line card actually draws (only those need shipping)."""
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
        return wanted

    def _window_start(self) -> float:
        window = float(self.seconds.value())
        return max(0.0, self.latest - window) if window else 0.0

    def history_for_page(self) -> dict[str, list[list[float]]]:
        """The whole retained window; only used for the first frame or a resync.

        ``points`` (R-301) caps how many samples a curve shows: 0 keeps every
        retained sample, a positive number keeps the newest N.  This is the
        *live* dashboard knob; replay of a log file uses its own window.
        """
        left = self._window_start()
        limit = self._capacity()
        trimmed: dict[str, list[list[float]]] = {}
        for name in self._visible_channels():
            samples = self.samples.get(name)
            if not samples:
                trimmed[name] = []
                continue
            rows = [[round(stamp, 4), round(value, 6)]
                    for stamp, value in samples if stamp >= left]
            if limit > 0 and len(rows) > limit:
                rows = rows[-limit:]
            # The *retention* window is sent whole (R-164: 5000 points must reach
            # the page).  The drawing cap (展示帧数) is applied by the page when
            # it fills a chart, so a full resync does not silently lose history.
            trimmed[name] = rows
        return trimmed

    def delta_for_page(self) -> tuple[dict, dict]:
        """Samples the page does not have yet, plus the resulting counts.

        This is the fix for "log 早刷到百年之后了，看板还在展示很旧的数据":
        instead of re-serialising the full 5000-sample window 30 times a second
        (which the renderer could never keep up with, so the backlog grew until
        it painted minutes-old data), only the new tail is shipped.

        The second value is ``{channel: samples covered by this batch}``.  The
        page's own copy is only known to be complete after it acknowledges, so
        the counts are held until :meth:`_on_acked` - advancing them here was
        the bug that froze the charts after a tab switch (R-318).
        """
        delta: dict[str, list[list[float]]] = {}
        counts: dict[str, int] = {}
        left = self._window_start()
        for name in self._visible_channels():
            samples = self.samples.get(name)
            if not samples:
                continue
            sent = self._sent.get(name, 0)
            total = self._received.get(name, 0)
            offset = max(0, sent - (total - len(samples)))
            fresh = islice(samples, offset, None)
            counts[name] = total
            rows = [[round(stamp, 4), round(value, 6)]
                    for stamp, value in fresh if stamp >= left]
            if rows:
                delta[name] = rows
        return delta, counts

    def _remember_sent(self, counts: dict) -> None:
        """Record which sample counts the page has now really received."""
        for name, total in (counts or {}).items():
            self._sent[name] = total

    def refresh(self) -> None:
        """Push one frame into the page; ECharts is never touched from Python."""
        self._flush_names()
        visible = self.isVisible()
        if visible and not self._was_visible and self.page_ready:
            # Coming back from another tab (HEX / terminal): the page may have
            # been suspended while it was hidden, so redraw the retained window
            # once instead of trusting a deltas-only frame (L-307).  The charts
            # also have to be re-measured: Chromium pauses the hidden page's
            # layout, and ECharts would otherwise keep drawing into the canvas
            # size it had when the tab was left (R-318).
            self.request_resync()
            self._run_js("omniDashboard.reflow()")
        self._was_visible = visible
        if visible and self.page_ready and self.live.isChecked():
            if not self.has_fresh_data():
                self.refresh_status()
                return
            started = time.perf_counter()
            self.flush_live()
            self.render_count += 1
            self.render_ms.append((time.perf_counter() - started) * 1000)
        self.refresh_status()

    def has_fresh_data(self) -> bool:
        """True when any visible channel has samples the page does not have.

        Sending an empty frame 30 times a second still calls ``setOption`` on
        every card (measured 24 ms for a 5000-point curve), so an idle or slow
        stream would stutter for no reason.  This is the cheapest possible
        guard: a dictionary length comparison.
        """
        if self._resync_needed:
            return True
        return bool(self.layout_data.get("widgets")) and self._revision > self._sent_revision

    def flush(self) -> None:
        """Send the whole retained window, unconditionally.

        This is the explicit "redraw everything" call: it is what the tests and
        the palette use, and what a layout / points / window change needs.  The
        live path uses :meth:`flush_live` instead, which only ships the new tail
        and therefore cannot fall behind (R-301).
        """
        if not self.page_ready:
            return
        history = self.history_for_page()
        payload = {"values": self.values, "history": history,
                   "append": False, "epoch": self._next_epoch(),
                   "start": self._window_start()}
        self._resync_needed = False
        self.resyncs += 1
        # Every send that expects an ack must stamp its own start time, or the
        # deadline in ``flush_live`` compares against 0 and every frame looks
        # overdue (caught by the R-301 back-pressure check).
        self._ack_started = time.perf_counter()
        # A full window replaces the page's history, so the counts are exact
        # only once the page confirms; store them for ``_on_acked``.
        self._last_sent = {name: self._received.get(name, 0)
                           for name in history}
        self._last_revision = self._revision
        self._call_js("setValues", payload)

    def flush_live(self) -> None:
        """One live frame: the new tail per channel, with back-pressure.

        Back-pressure matters as much as the delta itself.  Before it, a slow
        renderer accumulated 33 ms batches without bound, so the picture drifted
        further and further behind the log and adding a card became slower and
        slower.  While a batch is unacknowledged this simply skips the frame and
        counts it; the next acked frame carries everything that accumulated.
        """
        if not self.page_ready:
            return
        if self._resync_needed:
            self.flush()
            return
        if self._pending_ack:
            # A batch is still outstanding.  Waiting is what keeps the picture
            # from drifting, but waiting *forever* turns one lost
            # acknowledgement into "the dashboard stopped refreshing" - the
            # reported symptom after opening a HEX file in another tab.  Past
            # the deadline, redraw the retained window instead of stalling.
            if time.perf_counter() - self._ack_started < ACK_TIMEOUT_S:
                self.throttled += 1
                return
            self.ack_timeouts += 1
            self.request_resync()
            self.flush()
            return
        delta, counts = self.delta_for_page()
        payload = {"values": self.values, "history": delta,
                   "append": True, "epoch": self._next_epoch(),
                   "start": self._window_start()}
        self._ack_started = time.perf_counter()
        self._last_sent = counts
        self._last_revision = self._revision
        self._call_js("setValues", payload)

    def _next_epoch(self) -> int:
        self._epoch += 1
        self._pending_ack = self._epoch
        return self._pending_ack

    def request_resync(self) -> None:
        """Make the next live frame a full window (layout/theme/points changes)."""
        self._resync_needed = True
        self._sent.clear()
        self._sent_revision = -1
        self._last_sent = {}
        self._pending_ack = 0
        self._ack_started = 0.0

    # ---------------------------------------------------------------- widgets
    def add_widget_from_palette(self) -> str | None:
        channel = self.channel_combo.currentText().strip()
        sources = source_names(channel)
        kind = str(self.kind_combo.currentData() or "line")
        channels = sources if kind == "line" else sources[:1]
        if not channel or not sources:
            self.api.notify(self.api.t("先选择一个通道", "Pick a channel first"), "warn")
            return None
        return self.add_widget({
            "kind": kind,
            "channel": channels[0],
            **({"channels": channels} if kind == "line" else {}),
            **(self._palette_lamp_colors() if kind == "lamp" else {}),
            "title": self.title_edit.text().strip() or channel,
            "unit": self.unit_edit.text().strip(),
            "min": self._float_or(self.min_edit.text(), 0.0),
            "max": self._float_or(self.max_edit.text(), 100.0),
        })

    def _pick_sources(self, selected: list[str]) -> list[str] | None:
        dialog = SourcesDialog(self.api, self.order, selected, self)
        try:
            return dialog.sources() if dialog.exec() == QDialog.DialogCode.Accepted else None
        finally:
            dialog.deleteLater()

    def choose_palette_sources(self) -> None:
        if self.kind_combo.currentData() != "line":
            return
        sources = self._pick_sources(source_names(self.channel_combo.currentText()))
        if sources:
            self.channel_combo.setEditText(", ".join(sources))
            self.kind_combo.setCurrentIndex(self.kind_combo.findData("line"))

    def choose_palette_lamp_colors(self) -> None:
        if self.kind_combo.currentData() != "lamp":
            return
        dialog = LampColorsDialog(self.api, self._palette_lamp_colors(), self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.lamp_rule_config = dialog.colors()
                self._on_kind_changed()
        finally:
            dialog.deleteLater()

    def _palette_lamp_colors(self) -> dict:
        if self.lamp_rule_config is not None:
            return dict(self.lamp_rule_config)
        return {"colorRules": [{"value": 1, "color": self.lamp_on_color.color_name}],
                "fallbackColor": self.lamp_off_color.color_name}

    def edit_widget_sources(self, identifier: str) -> None:
        lamp = next((item for item in self.layout_data.get("widgets", [])
                     if item.get("id") == identifier
                     and item.get("kind") == "lamp"), None)
        if lamp is not None:
            dialog = LampColorsDialog(self.api, lamp, self)
            try:
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    lamp = next((item for item in self.layout_data.get("widgets", [])
                                 if item.get("id") == identifier
                                 and item.get("kind") == "lamp"), None)
                    if lamp is None:
                        return
                    lamp.update(dialog.colors())
                    self._save_layout()
                    self._call_js("updateWidget", {"id": identifier,
                                                   **dialog.colors()})
            finally:
                dialog.deleteLater()
            return
        widget = next((item for item in self.layout_data.get("widgets", [])
                       if item.get("id") == identifier and item.get("kind") == "line"), None)
        if widget is None:
            return
        sources = self._pick_sources(widget.get("channels") or [widget["channel"]])
        if sources:
            self.update_widget_sources(identifier, sources)

    def update_widget_sources(self, identifier: str, sources: list[str]) -> bool:
        sources = list(dict.fromkeys(name.strip() for name in sources if name.strip()))
        widget = next((item for item in self.layout_data.get("widgets", [])
                       if item.get("id") == identifier and item.get("kind") == "line"), None)
        if widget is None or not sources:
            return False
        widget.update(channel=sources[0], channels=sources)
        self._save_layout()
        self.request_resync()
        if self.page_ready:
            self._call_js("updateWidget", {
                "id": identifier, "channel": sources[0], "channels": sources,
            })
        return True

    def add_widget(self, payload: dict) -> str | None:
        """Add one widget; the page chooses where it lands.

        The position is deliberately **not** computed here.  Tiling by index
        from the top-left meant the tenth card appeared several screens further
        down, which the user described as the card "flying away"; the page knows
        the visible rectangle and the scroll offset, so it centres the new card
        in what is actually on screen (R-316).
        """
        kind = str(payload.get("kind") or "line")
        width, height = DEFAULT_SIZE.get(kind, (320, 224))
        index = len(self.layout_data.get("widgets", []))
        widget = dict(payload)
        widget.setdefault("id", f"w{index + 1}-{int(time.time() * 1000) % 100000}")
        widget.setdefault("w", width)
        widget.setdefault("h", height)
        # ``x``/``y`` stay absent unless the caller supplied them: the page treats
        # a missing pair as "put it in the middle of the view".
        # Channels already being shipped to the page hold their history there,
        # so a new card can be drawn from it immediately (the page reuses its
        # own drawn buffers).  Only a *newly bound* channel needs the retained
        # window, and asking for it unconditionally re-serialised the whole
        # history - ten channels at 5000 points is 83 KB - on every add.  That
        # was the reported "添加控件要很久或者直接就添加不了" (R-314).
        shipped = set(self._visible_channels())
        self.layout_data.setdefault("widgets", []).append(widget)
        if self.page_ready:
            # The page centres the card, then reports the resulting layout back
            # through ``layoutChanged``, which is where the coordinates are saved.
            self._call_js("addWidget", widget)
        else:
            # No page to ask: fall back to a tiled spot so the stored layout is
            # still usable (and a test without WebEngine gets real coordinates).
            widget.setdefault("x", 16 + (index % 3) * (width + 16))
            widget.setdefault("y", 16 + (index // 3) * (height + 16))
            self._save_layout()
        if set(self._visible_channels()) - shipped:
            self.request_resync()
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

    # ------------------------------------------------- layout import/export
    #: Written into every exported file so a stray JSON cannot be mistaken for a
    #: dashboard (and so a future format can be migrated instead of guessed).
    CONFIG_FORMAT = "omnicom-dashboard"
    CONFIG_VERSION = 1
    #: Hard ceiling on imported cards: an untrusted file must not be able to
    #: create thousands of WebEngine cards and take the window down.
    MAX_WIDGETS = 200

    @staticmethod
    def _sanitize_widget(raw) -> dict | None:
        """Validate one imported card, or ``None`` when it cannot be placed.

        Only the fields the page understands are kept, each coerced to its real
        type: a config file is user data and may be hand-edited or come from an
        older build, so a bad value must degrade to a default instead of
        breaking every later drag/save.
        """
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind") or "line")
        if kind not in KINDS:
            return None
        channel = str(raw.get("channel") or "").strip()
        if not channel:
            return None
        widget: dict = {"kind": kind, "channel": channel}
        widget["id"] = str(raw.get("id") or "").strip() or f"w{int(time.time() * 1000) % 100000}"
        widget["title"] = str(raw.get("title") or channel)
        widget["unit"] = str(raw.get("unit") or "")
        channels = raw.get("channels")
        if channels is not None:
            if not isinstance(channels, list) or not all(isinstance(name, str) and name.strip() for name in channels):
                return None
            widget["channels"] = list(dict.fromkeys(name.strip() for name in channels))
        if "decimals" in raw:
            try:
                widget["decimals"] = max(0, min(8, int(raw["decimals"])))
            except (TypeError, ValueError, OverflowError):
                return None
        for key in ("onText", "offText"):
            if key in raw:
                widget[key] = str(raw[key])
        if kind == "lamp":
            try:
                widget.update(normalize_lamp_rules(raw))
            except (ValueError, TypeError, OverflowError):
                return None
            for key in ("onColor", "offColor"):
                if raw.get(key) is not None:
                    color = QColor(str(raw[key]))
                    if not color.isValid():
                        return None
                    widget[key] = color.name()
        for key, default in (("min", 0.0), ("max", 100.0)):
            value = raw.get(key, default)
            try:
                widget[key] = float(value)
            except (TypeError, ValueError):
                widget[key] = default
            if not math.isfinite(widget[key]):
                return None
        size_w, size_h = DEFAULT_SIZE.get(kind, (320, 224))
        for key, default, low in (("x", 16, 0), ("y", 16, 0),
                                  ("w", size_w, 64), ("h", size_h, 48)):
            try:
                widget[key] = max(low, int(float(raw.get(key, default))))
            except (TypeError, ValueError, OverflowError):
                widget[key] = default
        return widget

    def config_payload(self) -> dict:
        """The portable document exported by 导出看板 (R-311)."""
        widgets = [widget for widget in
                   (self._sanitize_widget(raw) for raw
                    in self.layout_data.get("widgets", [])) if widget]
        return {
            "format": self.CONFIG_FORMAT,
            "version": self.CONFIG_VERSION,
            "widgets": widgets,
            # Display preferences travel with the layout: loading a tuned
            # dashboard should also restore the window/points it was tuned for.
            "window": self.seconds.value(),
            "points": self.points.value(),
            "drawn": self.drawn.value(),
        }

    def parse_config(self, data) -> tuple[dict | None, str]:
        """Validate an imported document: ``(layout, error)``.

        Returns ``(None, reason)`` for anything that is not a dashboard file.
        The caller must leave the current layout untouched in that case - a
        failed import may not wipe what the user already arranged.
        """
        if not isinstance(data, dict):
            return None, "not an object"
        if str(data.get("format") or "") != self.CONFIG_FORMAT:
            return None, "not a dashboard file"
        try:
            version = int(data.get("version") or 0)
        except (TypeError, ValueError):
            return None, "bad version"
        if version < 1 or version > self.CONFIG_VERSION:
            return None, f"unsupported version {version}"
        raw_widgets = data.get("widgets")
        if not isinstance(raw_widgets, list):
            return None, "missing widgets"
        widgets = [widget for widget in
                   (self._sanitize_widget(raw) for raw in raw_widgets) if widget]
        if raw_widgets and not widgets:
            return None, "no usable widgets"
        if len(widgets) > self.MAX_WIDGETS:
            return None, f"too many widgets ({len(widgets)})"
        if len({widget["id"] for widget in widgets}) != len(widgets):
            return None, "duplicate widget identities"
        return {"widgets": widgets}, ""

    def apply_config(self, data, *, restore_display: bool = True) -> dict:
        """Adopt a validated document; returns what was applied."""
        layout, error = self.parse_config(data)
        if layout is None:
            raise ValueError(error)
        self.layout_data = layout
        self._save_layout()
        applied: dict = {"widgets": len(layout["widgets"])}
        if restore_display:
            for key, box in (("window", self.seconds), ("points", self.points),
                             ("drawn", self.drawn)):
                value = data.get(key)
                if value is None:
                    continue
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    continue
                if box.minimum() <= number <= box.maximum():
                    box.setValue(number)
                    applied[key] = number
        self._push_layout()
        # The page holds no series for the imported cards, so the next frame must
        # be a full window rather than a (possibly empty) tail.
        self.request_resync()
        self.refresh_status()
        return applied

    def export_config(self, filename: str) -> int:
        """Write the layout document; returns the number of cards exported."""
        payload = self.config_payload()
        with open(filename, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return len(payload["widgets"])

    def export_config_dialog(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self, self.api.t("导出看板", "Export dashboard"),
            "dashboard.json", "OmniCOM dashboard (*.json);;All files (*)")
        if not filename:
            return
        try:
            count = self.export_config(filename)
        except OSError as exc:
            self.api.notify(str(exc), "error")
            return
        self.api.notify(self.api.t(f"已导出 {count} 个控件", f"{count} widgets exported"),
                        "ok")

    def import_config_dialog(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, self.api.t("导入看板", "Import dashboard"),
            "", "OmniCOM dashboard (*.json);;All files (*)")
        if not filename:
            return
        try:
            with open(filename, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            self.api.notify(str(exc), "error")
            return
        try:
            applied = self.apply_config(data)
        except ValueError as exc:
            # Leave the current dashboard exactly as it was.
            self.api.notify(self.api.t(f"不是有效的看板文件：{exc}",
                                       f"Not a valid dashboard file: {exc}"), "warn")
            return
        self.api.notify(self.api.t(f"已导入 {applied['widgets']} 个控件",
                                   f"{applied['widgets']} widgets imported"), "ok")

    def clear_widgets(self) -> None:
        """Remove every widget (received data is kept)."""
        self.layout_data = {"widgets": []}
        self._save_layout()
        self._push_layout()
        self.refresh_status()

    def _on_kind_changed(self, *_args) -> None:
        kind = str(self.kind_combo.currentData() or "line")
        self.sources_button.setVisible(kind == "line")
        self.lamp_on_color.setVisible(kind == "lamp")
        self.lamp_off_color.setVisible(kind == "lamp")
        self.lamp_rules_button.setVisible(kind == "lamp")
        if self.lamp_rule_config is not None:
            self.lamp_on_color.hide()
            self.lamp_off_color.hide()
        if kind != "line":
            sources = source_names(self.channel_combo.currentText())
            if len(sources) > 1:
                self.channel_combo.setEditText(sources[0])
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
        self._received.clear()
        self._revision = 0
        self._sent_revision = 0
        self._last_revision = 0
        self._last_sent = {}
        self._resync_needed = True
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
        self._sent.clear()
        self._pending_ack = 0
        self._acked_epoch = 0
        self.resyncs = 0
        self.throttled = 0
        self.replayed_lines = 0
        self.request_resync()
        self.refresh_status()

    # ---------------------------------------------------------------- replay
    def begin_replay(self, name: str = "") -> None:
        """Start from a clean slate so old live data cannot mix into the replay.

        A replay is a finished measurement, not more traffic for the live graph
        (R-302), so the retained samples are dropped first.  The live dashboard
        keeps refreshing afterwards with whatever the port sends next.
        """
        self.replay_name = str(name)
        self._replay_active = True
        self.clear_data()
        self.live.setChecked(True)
        self.refresh_status()

    def end_replay(self) -> None:
        """Push the last replayed window to the page and say how much arrived."""
        self._flush_names()
        self._replay_active = False
        self._push_points()
        self._push_layout()
        self.request_resync()
        self.flush()
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
            "sourceLabel": self.api.t("曲线数据来源", "Curve data sources"),
            "lampColorLabel": self.api.t("灯数值与颜色", "Lamp values and colors"),
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
        self.status.setToolTip(self.api.t(
            "重同步 {resyncs} 次 · 背压跳过 {throttled} 帧",
            "resyncs {resyncs} · back-pressure skips {throttled}").format(
                resyncs=self.resyncs, throttled=self.throttled))

    def retranslate(self) -> None:
        self._style_run(self.live.isChecked())
        self.live.setToolTip(self.api.t(
            "暂停=冻结画面，采集与计数继续；继续=恢复刷新",
            "Pause freezes the picture while acquisition keeps running; "
            "Resume brings the refresh back"))
        self.seconds_label.setText(self.api.t("时间窗 / 秒", "Window / s"))
        self.seconds.setSpecialValueText(self.api.t("全部", "All"))
        self.points_label.setText(self.api.t("曲线点数", "Points"))
        self.points.setSpecialValueText(self.api.t("全部", "All"))
        self.points.setToolTip(self.api.t(
            "每个通道保留的采样点数（0=全部 5000）",
            "Samples retained per channel (0 = all 5000)"))
        self.visible_label.setText(self.api.t("展示帧数", "Drawn"))
        self.drawn.setToolTip(self.api.t(
            "每条曲线每帧实际绘制的点数，越小越流畅；不影响保留的历史",
            "Points a curve draws per frame; smaller is smoother and does not "
            "affect the history that is kept"))
        self.clear_widgets_button.setText(self.api.t("清空控件", "Clear widgets"))
        self.clear_widgets_button.setToolTip(self.api.t(
            "移除所有卡片，接收的数据保留", "Remove every card; received data is kept"))
        self.menu_button.setText(self.api.t("菜单", "Menu"))
        self.menu_button.setToolTip(self.api.t(
            "截图、导出 CSV、导入导出看板、显示设置、清除数据",
            "Image, CSV export, layout import/export, display settings, clear data"))
        self.options_title = self.api.t("显示设置", "Display settings")
        self.export_config_button.setText(self.api.t("导出看板", "Export layout"))
        self.export_config_button.setToolTip(self.api.t(
            "把当前卡片布局、绑定与显示设置保存成文件，之后可以导入",
            "Save the cards, bindings and display settings to a file for later import"))
        self.import_config_button.setText(self.api.t("导入看板", "Import layout"))
        self.import_config_button.setToolTip(self.api.t(
            "从文件恢复卡片布局；无效文件不会改动当前看板",
            "Restore a dashboard from a file; an invalid file leaves the current one untouched"))
        self.image_button.setText(self.api.t("保存截图", "Save image"))
        self.export_button.setText(self.api.t("导出 CSV", "Export CSV"))
        self.clear_button.setToolTip(self.api.t("清空接收数据", "Clear received data"))
        self.kind_label.setText(self.api.t("控件", "Widget"))
        self.channel_label.setText(self.api.t("通道", "Channel"))
        self.sources_button.setToolTip(self.api.t("曲线数据来源", "Curve data sources"))
        self.lamp_rules_button.setToolTip(self.api.t("灯数值与颜色", "Lamp values and colors"))
        for swatch, zh, en in ((self.lamp_on_color, "数值1颜色", "Value 1 color"),
                      (self.lamp_off_color, "其它值颜色", "Other values color")):
            swatch.title = self.api.t(zh, en)
            swatch.setToolTip(swatch.title)
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

        if self.page_ready:
            self.apply_theme()


def create(api) -> QWidget:
    panel = DashboardPanel(api)
    api.provide("dashboard", lambda: panel)
    return panel
