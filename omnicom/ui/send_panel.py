"""Bottom bar: single-line/multi-line send, HEX send, timed send, send file."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..commands import decode_escapes, parse_hex
from . import theme
from .widgets import Combo, GlassFrame, ToggleSwitch

LINE_ENDS = {"crlf": b"\r\n", "lf": b"\n", "cr": b"\r", "none": b""}


class SendInput(QPlainTextEdit):
    sendTriggered = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.sendTriggered.emit()
            return
        super().keyPressEvent(event)


class SendPanel(GlassFrame):
    sendRequested = Signal(bytes, str)   # payload, echo description
    fileSelected = Signal(str)
    abortRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent, radius=18, shadow=True)
        self.setObjectName("SendPanel")
        self._timed = QTimer(self)
        self._timed.timeout.connect(self.emit_current)
        self._sending_file = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 11, 12, 11)
        root.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(10)

        self.input = SendInput()
        self.input.setPlaceholderText(
            "输入要发送的内容，Ctrl+Enter 发送（HEX 模式用空格分隔字节，如 01 03 00 00 00 01）")
        self.input.setFixedHeight(64)
        self.input.sendTriggered.connect(self.emit_current)
        top.addWidget(self.input, 1)

        side = QVBoxLayout()
        side.setSpacing(7)
        self.btn_send = QPushButton("发送")
        self.btn_send.setObjectName("Primary")
        self.btn_send.setCursor(Qt.PointingHandCursor)
        self.btn_send.clicked.connect(self.emit_current)
        side.addWidget(self.btn_send, 1)

        opts = QHBoxLayout()
        opts.setSpacing(6)
        self.line_end = Combo()
        for key, label in (("none", "无行尾"), ("cr", "\\r"), ("lf", "\\n"), ("crlf", "\\r\\n")):
            self.line_end.addItem(label, key)
        self.line_end.setCurrentIndex(self.line_end.findData("crlf"))
        self.line_end.setToolTip("发送时自动追加的行尾")
        opts.addWidget(self.line_end, 1)

        hex_box = QHBoxLayout()
        hex_box.setSpacing(5)
        hex_label = QLabel("HEX")
        hex_label.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        self.hex_send = ToggleSwitch(self, checked=False, width=32, height=18)
        self.hex_send.setToolTip("以 HEX 解析输入内容后发送")
        hex_box.addWidget(hex_label)
        hex_box.addWidget(self.hex_send)
        opts.addLayout(hex_box)
        side.addLayout(opts)
        top.addLayout(side)
        root.addLayout(top)

        foot = QHBoxLayout()
        foot.setSpacing(8)

        timed_label = QLabel("定时发送")
        timed_label.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        foot.addWidget(timed_label)

        self.interval = QSpinBox()
        self.interval.setRange(10, 3600000)
        self.interval.setSingleStep(10)
        self.interval.setValue(1000)
        self.interval.setFixedWidth(92)
        foot.addWidget(self.interval)

        unit = QLabel("ms")
        unit.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        foot.addWidget(unit)

        self.btn_timed = QPushButton("开始")
        self.btn_timed.setObjectName("Ghost")
        self.btn_timed.setCursor(Qt.PointingHandCursor)
        self.btn_timed.clicked.connect(self.toggle_timed)
        foot.addWidget(self.btn_timed)

        foot.addStretch(1)

        self.btn_file = QPushButton("发送文件")
        self.btn_file.setObjectName("Ghost")
        self.btn_file.setCursor(Qt.PointingHandCursor)
        self.btn_file.clicked.connect(self.pick_file)
        foot.addWidget(self.btn_file)

        self.btn_abort = QPushButton("停止")
        self.btn_abort.setObjectName("Danger")
        self.btn_abort.setCursor(Qt.PointingHandCursor)
        self.btn_abort.clicked.connect(self.abortRequested.emit)
        self.btn_abort.hide()
        foot.addWidget(self.btn_abort)

        self.file_hint = QLabel("")
        self.file_hint.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        foot.addWidget(self.file_hint)

        root.addLayout(foot)

        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.input)
        shortcut.activated.connect(self.emit_current)

    # ------------------------------------------------------------------ send
    def payload(self) -> tuple[bytes, str]:
        text = self.input.toPlainText()
        if self.hex_send.isChecked():
            parsed = parse_hex(text)
            if parsed is None:
                return b"", ""
            return parsed, f"[HEX] {text.strip()}"
        body = decode_escapes(text).encode("utf-8")
        ending = LINE_ENDS.get(self.line_end.currentData(), b"\r\n")
        description = text
        return body + ending, description

    def emit_current(self) -> None:
        payload, description = self.payload()
        if not payload:
            return
        self.sendRequested.emit(payload, description)
        if not self._timed.isActive():
            self.input.clear()

    def toggle_timed(self) -> None:
        if self._timed.isActive():
            self._timed.stop()
            self.btn_timed.setText("开始")
            self.btn_timed.setObjectName("Ghost")
            self.btn_send.setEnabled(True)
        else:
            payload, _ = self.payload()
            if not payload:
                return
            self._timed.start(max(10, self.interval.value()))
            self.btn_timed.setText("停止")
            self.btn_timed.setObjectName("Danger")
            self.emit_current()
        self._repolish(self.btn_timed)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # ------------------------------------------------------------------ file
    def pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择要发送的文件")
        if path:
            self.fileSelected.emit(path)

    def set_file_busy(self, busy: bool, text: str = "") -> None:
        self.btn_abort.setVisible(busy)
        self.btn_file.setEnabled(not busy)
        self.file_hint.setText(text)

    def set_file_progress(self, sent: int, total: int, name: str) -> None:
        percent = (sent / total * 100) if total else 0
        self.file_hint.setText(f"{os.path.basename(name)} {percent:.0f}%  ({sent}/{total})")
