"""Transfer tab: X/Y/ZMODEM sender & receiver with progress and a live log."""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from . import theme
from .log_view import LogView
from .widgets import GlassFrame

PROTOCOLS = [
    ("x", "XMODEM", "128B / 1KB · CRC16"),
    ("y", "YMODEM", "批量 · 带文件名"),
    ("z", "ZMODEM", "窗口流式 · CRC32"),
]


class DropZone(QFrame):
    """Click or drag a file here."""

    fileDropped = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(96)
        self._apply_style(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(5)
        icon = QLabel("⇪")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f"color: {theme.INK2}; font-size: 22px;")
        layout.addWidget(icon)
        self.title = QLabel("点击选择文件，或把文件拖到这里")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet(f"color: {theme.INK2}; font-size: 12px;")
        layout.addWidget(self.title)
        self.detail = QLabel("")
        self.detail.setAlignment(Qt.AlignCenter)
        self.detail.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        layout.addWidget(self.detail)

    def _apply_style(self, active: bool) -> None:
        border = "rgba(14, 148, 132, 150)" if active else theme.LINE_STRONG
        fill = theme.TEAL_SOFT if active else theme.WASH
        self.setStyleSheet(
            f"QFrame {{ border: 1px dashed {border}; border-radius: 14px; background: {fill}; }}"
            "QLabel { border: 0; background: transparent; }")

    def set_file(self, path: str) -> None:
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        self.title.setText(os.path.basename(path))
        self.detail.setText(f"{size / 1024:.1f} KB  ·  {path}")
        self._apply_style(True)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            path, _ = QFileDialog.getOpenFileName(self, "选择要传输的文件")
            if path:
                self.fileDropped.emit(path)
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._apply_style(True)

    def dragLeaveEvent(self, event) -> None:
        self._apply_style(bool(self.detail.text()))

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isfile(path):
                self.fileDropped.emit(path)


class TransferPanel(QWidget):
    startRequested = Signal(str, str, str)   # protocol, direction, file path
    abortRequested = Signal()

    def __init__(self, notify: Callable[[str, str], None], parent=None) -> None:
        super().__init__(parent)
        self.notify = notify
        self.file_path = ""
        self.direction = "send"

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # ------------------------------------------------------- left controls
        controls = GlassFrame(radius=18)
        left = QVBoxLayout(controls)
        left.setContentsMargins(14, 13, 14, 13)
        left.setSpacing(9)

        left.addWidget(self._title("协议"))
        proto_row = QHBoxLayout()
        proto_row.setSpacing(8)
        self.proto_group = QButtonGroup(self)
        self.proto_group.setExclusive(True)
        for key, name, detail in PROTOCOLS:
            button = QPushButton(f"{name}\n{detail}")
            button.setObjectName("Proto")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty("proto", key)
            button.setMinimumHeight(54)
            self.proto_group.addButton(button)
            proto_row.addWidget(button)
            if key == "x":
                button.setChecked(True)
        left.addLayout(proto_row)

        left.addWidget(self._title("方向"))
        dir_row = QHBoxLayout()
        dir_row.setSpacing(3)
        dir_box = QFrame()
        dir_box.setStyleSheet(
            f"QFrame {{ background: {theme.WASH};"
            f" border: 1px solid {theme.LINE_SOFT}; border-radius: 11px; }}")
        dir_layout = QHBoxLayout(dir_box)
        dir_layout.setContentsMargins(3, 3, 3, 3)
        dir_layout.setSpacing(3)
        self.dir_group = QButtonGroup(self)
        self.dir_group.setExclusive(True)
        for key, label in (("send", "发送到设备"), ("recv", "从设备接收")):
            button = QPushButton(label)
            button.setObjectName("Seg")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty("dir", key)
            if key == "send":
                button.setChecked(True)
            self.dir_group.addButton(button)
            dir_layout.addWidget(button)
        self.dir_group.buttonClicked.connect(self._dir_clicked)
        dir_row.addWidget(dir_box)
        left.addLayout(dir_row)

        self.drop = DropZone()
        self.drop.fileDropped.connect(self._set_file)
        left.addWidget(self.drop)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_start = QPushButton("开始传输")
        self.btn_start.setObjectName("Primary")
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.clicked.connect(self._start)
        actions.addWidget(self.btn_start, 1)
        self.btn_abort = QPushButton("中止")
        self.btn_abort.setObjectName("Ghost")
        self.btn_abort.setEnabled(False)
        self.btn_abort.setCursor(Qt.PointingHandCursor)
        self.btn_abort.clicked.connect(self.abortRequested.emit)
        actions.addWidget(self.btn_abort)
        left.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        left.addWidget(self.progress)

        meta = QHBoxLayout()
        self.state_label = QLabel("就绪")
        self.state_label.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        self.meta_label = QLabel("—")
        self.meta_label.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        meta.addWidget(self.state_label)
        meta.addStretch(1)
        meta.addWidget(self.meta_label)
        left.addLayout(meta)

        self.hint = QLabel(f"接收到的文件保存到 {paths.received_dir()}")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        left.addWidget(self.hint)
        left.addStretch(1)
        root.addWidget(controls, 4)

        # ---------------------------------------------------------- right log
        log_card = GlassFrame(radius=18, shadow=False)
        right = QVBoxLayout(log_card)
        right.setContentsMargins(12, 11, 12, 11)
        right.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(self._title("传输日志"))
        head.addStretch(1)
        clear = QPushButton("清空")
        clear.setObjectName("Ghost")
        clear.clicked.connect(lambda: self.log_view.clear_all())
        head.addWidget(clear)
        right.addLayout(head)

        self.log_view = LogView(show_dir=False, pixel_size=12)
        self.log_view.show_ts = True
        self.log_view.setStyleSheet(
            "QPlainTextEdit { background: transparent; border: 0; padding: 0; }")
        right.addWidget(self.log_view, 1)
        root.addWidget(log_card, 5)

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _title(text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setObjectName("SectionTitle")
        return label

    def protocol(self) -> str:
        button = self.proto_group.checkedButton()
        return button.property("proto") if button else "x"

    def _dir_clicked(self, button) -> None:
        self.direction = button.property("dir")
        is_send = self.direction == "send"
        self.drop.setVisible(is_send)
        self.hint.setVisible(not is_send)
        if not is_send:
            self.state_label.setText("等待设备发起传输…")
            self.meta_label.setText("—")
            self.progress.setValue(0)

    def _set_file(self, path: str) -> None:
        self.file_path = path
        self.drop.set_file(path)

    def _start(self) -> None:
        if self.direction == "send" and not self.file_path:
            self.notify("请先选择要发送的文件", "warn")
            return
        self.startRequested.emit(self.protocol(), self.direction, self.file_path)

    # -------------------------------------------------------------- feedback
    def log(self, text: str) -> None:
        import time
        self.log_view.append_line(time.time() * 1000, "··", text, "sys")

    def set_running(self, running: bool) -> None:
        self.btn_start.setEnabled(not running)
        self.btn_abort.setEnabled(running)
        if not running:
            self.state_label.setText("就绪")

    def set_progress(self, sent: int, total: int, rate: int = 0) -> None:
        if total:
            self.progress.setValue(int(min(1000, sent / total * 1000)))
        detail = f"{sent / 1024:.1f}"
        detail += f" / {total / 1024:.1f} KB" if total else " KB"
        if rate:
            detail += f"  ·  {rate / 1024:.1f} KB/s"
        self.meta_label.setText(detail)

    def set_state(self, text: str) -> None:
        self.state_label.setText(text)

    def finish(self, ok: bool, message: str, download_path: str = "") -> None:
        self.set_running(False)
        if ok:
            self.progress.setValue(1000)
        self.state_label.setText("完成" if ok else "失败")
        self.log(("✔ " if ok else "✘ ") + message)
        if download_path:
            self.hint.setVisible(True)
            self.hint.setText(f"已保存：{download_path}")
