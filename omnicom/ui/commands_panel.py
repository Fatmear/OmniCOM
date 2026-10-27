"""Right-sidebar panel: extension command list with per-item delay/timer."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..commands import CommandStore, payload_of
from . import theme
from .widgets import Badge, SwitchRow, ToggleSwitch

LINE_END_LABELS = [("crlf", "\\r\\n"), ("lf", "\\n"), ("cr", "\\r"), ("none", "无")]


class ElidedLabel(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self._full = text
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        font = QFont("Cascadia Code")
        font.setStyleHint(QFont.Monospace)
        font.setPixelSize(12)
        self.setFont(font)
        self.setStyleSheet(f"color: {theme.INK2};")
        self.setText(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self.update()

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QPainter
        painter = QPainter(self)
        metrics = self.fontMetrics()
        text = metrics.elidedText(self._full.replace("\n", "\\n"), Qt.ElideRight,
                                  max(10, self.width()))
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.rect(), Qt.AlignVCenter | Qt.AlignLeft, text)


class PasteBox(QPlainTextEdit):
    """Paste-aware box: a multi-line paste imports immediately."""

    pasted = Signal(str)

    def insertFromMimeData(self, source) -> None:
        text = source.text()
        if text:
            self.pasted.emit(text)
        super().insertFromMimeData(source)


class CommandRow(QFrame):
    send = Signal(dict)
    edit = Signal(dict)
    remove = Signal(str)
    toggled = Signal(str, bool)

    def __init__(self, item: dict, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.setObjectName("CmdRow")
        self._apply_style(item.get("enabled", True))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 6, 6)
        layout.setSpacing(7)

        self.check = QCheckBox()
        self.check.setChecked(bool(item.get("enabled", True)))
        self.check.setToolTip("勾选后参与「依次发送」")
        self.check.toggled.connect(lambda value: self.toggled.emit(item["id"], value))
        layout.addWidget(self.check)

        text = item.get("text", "")
        self.label = ElidedLabel(f"[HEX] {text}" if item.get("hex") else text)
        self.label.setCursor(Qt.PointingHandCursor)
        self.label.mouseReleaseEvent = lambda _event: self.send.emit(item)
        layout.addWidget(self.label, 1)

        meta = QHBoxLayout()
        meta.setSpacing(4)
        delay = QLabel(f"{item.get('delay', 0)}ms")
        delay.setObjectName("Badge")
        meta.addWidget(delay)
        if item.get("timer"):
            timer = QLabel(f"↻{item.get('interval', 1000)}ms")
            timer.setObjectName("BadgeWarn")
            meta.addWidget(timer)
        layout.addLayout(meta)

        for icon, tip, slot in (("▶", "发送", lambda: self.send.emit(item)),
                                ("✎", "编辑：行尾 / 延时 / 定时", lambda: self.edit.emit(item)),
                                ("✕", "删除", lambda: self.remove.emit(item["id"]))):
            button = QPushButton(icon)
            button.setObjectName("IconBtn")
            button.setFixedSize(22, 22)
            button.setToolTip(tip)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(slot)
            layout.addWidget(button)

    def _apply_style(self, enabled: bool) -> None:
        border = "rgba(14, 148, 132, 110)" if enabled else theme.LINE
        fill = theme.TEAL_SOFT if enabled else theme.CARD
        hover = "#F0FBF9" if enabled else theme.WASH
        self.setStyleSheet(
            f"QFrame#CmdRow {{ background: {fill}; border: 1px solid {border};"
            f" border-radius: 11px; }}"
            f"QFrame#CmdRow:hover {{ background: {hover};"
            f" border-color: {theme.LINE_STRONG}; }}")


class CommandDialog(QDialog):
    """Create or edit one command."""

    def __init__(self, parent, item: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑命令" if item else "新增命令")
        self.setModal(True)
        self.setMinimumWidth(430)
        self.item = dict(item) if item else {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("编辑命令" if item else "新增命令")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)

        layout.addWidget(self._dim("命令内容（支持 \\r \\n \\t \\xNN 转义）"))
        self.text = QPlainTextEdit()
        self.text.setPlainText(self.item.get("text", "AT"))
        self.text.setFixedHeight(76)
        layout.addWidget(self.text)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addLayout(self._field("行尾", self._line_end_combo()))
        self.hex_switch = ToggleSwitch(self, checked=bool(self.item.get("hex")))
        hex_box = QVBoxLayout()
        hex_box.addWidget(self._dim("HEX 发送"))
        hex_row = QHBoxLayout()
        hex_row.addWidget(self.hex_switch)
        hex_row.addStretch(1)
        hex_box.addLayout(hex_row)
        row.addLayout(hex_box)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.delay = QSpinBox()
        self.delay.setRange(0, 600000)
        self.delay.setSingleStep(10)
        self.delay.setValue(int(self.item.get("delay", 120)))
        row2.addLayout(self._field("依次发送延时 (ms)", self.delay))
        self.interval = QSpinBox()
        self.interval.setRange(20, 3600000)
        self.interval.setSingleStep(50)
        self.interval.setValue(int(self.item.get("interval", 1000)))
        row2.addLayout(self._field("定时发送间隔 (ms)", self.interval))
        layout.addLayout(row2)

        self.timer_row = SwitchRow("启用定时发送", bool(self.item.get("timer")))
        layout.addWidget(self.timer_row)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存" if item else "添加")
        save.setObjectName("Primary")
        save.clicked.connect(self.accept)
        footer.addWidget(cancel)
        footer.addWidget(save)
        layout.addLayout(footer)

        self.setStyleSheet(
            f"QDialog {{ background: {theme.CARD}; }}"
            f"QLabel {{ color: {theme.INK}; }}")

    # -- helpers -----------------------------------------------------------
    def _dim(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        return label

    def _field(self, label: str, widget: QWidget) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(4)
        box.addWidget(self._dim(label))
        box.addWidget(widget)
        return box

    def _line_end_combo(self) -> QComboBox:
        combo = QComboBox()
        current = self.item.get("line_end", "crlf")
        for key, label in LINE_END_LABELS:
            combo.addItem(label, key)
        index = max(0, combo.findData(current))
        combo.setCurrentIndex(index)
        self.line_end = combo
        return combo

    def result_item(self) -> dict:
        return {
            "text": self.text.toPlainText().strip("\n"),
            "line_end": self.line_end.currentData(),
            "hex": self.hex_switch.isChecked(),
            "delay": self.delay.value(),
            "interval": self.interval.value(),
            "timer": self.timer_row.isChecked(),
        }


class CommandsPanel(QWidget):
    """Collapsible command list living in the right sidebar."""

    sendRequested = Signal(bytes, str)   # payload, description for echo

    def __init__(self, store: CommandStore, notify: Callable[[str, str], None],
                 parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.notify = notify
        self.timers: dict[str, QTimer] = {}
        self._collapsed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = self._build_header()
        root.addWidget(self.header)

        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(9, 8, 10, 10)
        body_layout.setSpacing(7)

        self.drawer = self._build_drawer()
        body_layout.addWidget(self.drawer)
        self.drawer.hide()

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        body_layout.addWidget(self.scroll, 1)

        self.empty = QLabel("暂无命令\n点击 ⧉ 粘贴多行命令，或 + 新增")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px; line-height: 1.7;")
        body_layout.addWidget(self.empty)

        root.addWidget(self.body, 1)

        self.store.changed.connect(self.rebuild)
        self.rebuild()

    # ------------------------------------------------------------------ header
    def _build_header(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(5, 6, 8, 6)
        layout.setSpacing(6)

        self.chevron = QPushButton("▾")
        self.chevron.setObjectName("IconBtn")
        self.chevron.setFixedSize(22, 22)
        self.chevron.setCursor(Qt.PointingHandCursor)
        self.chevron.clicked.connect(self.toggle_collapsed)
        layout.addWidget(self.chevron)

        title = QLabel("扩展命令")
        title.setStyleSheet(f"color: {theme.INK2}; font-size: 12px; font-weight: 600;")
        layout.addWidget(title)

        self.count = Badge("0")
        layout.addWidget(self.count)
        layout.addStretch(1)

        self.btn_paste = self._tool("⧉", "粘贴多行命令，自动拆分导入")
        self.btn_paste.clicked.connect(self.show_drawer)
        self.btn_add = self._tool("+", "新增命令")
        self.btn_add.clicked.connect(lambda: self.open_editor(None))
        layout.addWidget(self.btn_paste)
        layout.addWidget(self.btn_add)

        self.btn_all = QPushButton("依次发送")
        self.btn_all.setObjectName("Ghost")
        self.btn_all.setFixedHeight(24)
        self.btn_all.setStyleSheet("font-size: 11px; padding: 0 9px;")
        self.btn_all.setCursor(Qt.PointingHandCursor)
        self.btn_all.clicked.connect(self.send_all)
        layout.addWidget(self.btn_all)

        header.setObjectName("SideHeader")
        header.setStyleSheet(
            "QWidget#SideHeader { background: transparent; border: 0;"
            f" border-bottom: 1px solid {theme.LINE_SOFT}; }}")
        return header

    def _tool(self, text: str, tip: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("IconBtn")
        button.setFixedSize(22, 22)
        button.setToolTip(tip)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self.body.setVisible(not self._collapsed)
        self.chevron.setText("▸" if self._collapsed else "▾")
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "relayout_sidebar"):
            parent = parent.parentWidget()
        if parent is not None:
            parent.relayout_sidebar()
        else:
            self.updateGeometry()

    # ------------------------------------------------------------------ drawer
    def _build_drawer(self) -> QWidget:
        drawer = QFrame()
        drawer.setStyleSheet(
            f"QFrame {{ background: {theme.WASH};"
            f" border: 1px dashed {theme.LINE_STRONG}; border-radius: 11px; }}")
        layout = QVBoxLayout(drawer)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.paste_box = PasteBox()
        self.paste_box.setPlaceholderText("粘贴一列命令，每行一条，粘贴后自动导入…")
        self.paste_box.setFixedHeight(96)
        self.paste_box.pasted.connect(self._on_paste)
        layout.addWidget(self.paste_box)

        row = QHBoxLayout()
        self.paste_hint = QLabel("")
        self.paste_hint.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        row.addWidget(self.paste_hint, 1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.setFixedHeight(24)
        cancel.clicked.connect(self.hide_drawer)
        confirm = QPushButton("导入")
        confirm.setObjectName("Primary")
        confirm.setFixedHeight(24)
        confirm.clicked.connect(lambda: self.import_text(self.paste_box.toPlainText()))
        row.addWidget(cancel)
        row.addWidget(confirm)
        layout.addLayout(row)
        return drawer

    def show_drawer(self) -> None:
        self.drawer.setVisible(not self.drawer.isVisible())
        if self.drawer.isVisible():
            self.paste_box.clear()
            self.paste_hint.setText("支持 Ctrl+V 直接粘贴")
            self.paste_box.setFocus()

    def hide_drawer(self) -> None:
        self.paste_box.clear()
        self.paste_hint.setText("")
        self.drawer.hide()

    def _on_paste(self, text: str) -> None:
        lines = self.store.split_lines(text)
        if len(lines) > 1:
            # multi-line paste: import right away, that is the whole point of the box
            self.paste_box.clear()
            self.import_text(text)

    def import_text(self, raw: str) -> None:
        lines = self.store.split_lines(raw)
        if not lines:
            self.paste_hint.setText("没有可导入的行")
            return
        self.store.add(lines)
        self.hide_drawer()
        self.notify(f"已导入 {len(lines)} 条命令", "ok")

    # ------------------------------------------------------------------- rows
    def rebuild(self) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Detach first: deleteLater() keeps the child visible to
                # findChildren()/layout until the event loop runs.
                widget.setParent(None)
                widget.deleteLater()
        for entry in self.store.items:
            row = CommandRow(entry)
            row.send.connect(lambda item: self.send_item(item))
            row.edit.connect(lambda item: self.open_editor(item))
            row.remove.connect(self.remove_item)
            row.toggled.connect(self.set_enabled)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
        count = len(self.store.items)
        self.count.setText(str(count))
        self.count.setVisible(count > 0)
        self.empty.setVisible(count == 0)
        self.scroll.setVisible(count > 0)
        self.sync_timers()

    # ----------------------------------------------------------------- timers
    def sync_timers(self) -> None:
        live = {item["id"]: item for item in self.store.items if item.get("timer")}
        for item_id in list(self.timers):
            if item_id not in live:
                self.timers.pop(item_id).stop()
        for item_id, item in live.items():
            interval = max(20, int(item.get("interval", 1000)))
            timer = self.timers.get(item_id)
            if timer is None:
                timer = QTimer(self)
                timer.timeout.connect(lambda iid=item_id: self._fire(iid))
                self.timers[item_id] = timer
            timer.start(interval)

    def _fire(self, item_id: str) -> None:
        for item in self.store.items:
            if item["id"] == item_id:
                self.send_item(item)
                return

    # ---------------------------------------------------------------- actions
    def send_item(self, item: dict) -> None:
        payload = payload_of(item)
        if not payload:
            self.notify("命令内容为空或不合法", "warn")
            return
        description = f"[HEX] {item['text']}" if item.get("hex") else item.get("text", "")
        self.sendRequested.emit(payload, description)

    def send_all(self) -> None:
        items = self.store.enabled_items()
        if not items:
            self.notify("没有勾选任何命令", "warn")
            return
        self._queue = list(items)
        self._send_next()

    def _send_next(self) -> None:
        if not getattr(self, "_queue", None):
            return
        item = self._queue.pop(0)
        self.send_item(item)
        delay = max(0, int(item.get("delay", 0)))
        QTimer.singleShot(delay, self._send_next)

    def remove_item(self, item_id: str) -> None:
        timer = self.timers.pop(item_id, None)
        if timer:
            timer.stop()
        self.store.remove(item_id)

    def set_enabled(self, item_id: str, enabled: bool) -> None:
        self.store.update(item_id, {"enabled": enabled})

    def open_editor(self, item: dict | None) -> None:
        dialog = CommandDialog(self, item)
        if dialog.exec() != QDialog.Accepted:
            return
        patch = dialog.result_item()
        if not patch["text"]:
            self.notify("命令内容不能为空", "warn")
            return
        if item:
            self.store.update(item["id"], patch)
        else:
            self.store.add([patch["text"]], patch)
