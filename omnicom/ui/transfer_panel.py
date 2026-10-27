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
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..i18n import tr
from . import theme
from .log_view import LogView
from .widgets import Button as QPushButton
from .widgets import GlassFrame

PROTOCOLS = [
    ("x", "XMODEM", ("128B / 1KB · CRC16", "128B / 1KB · CRC16")),
    ("y", "YMODEM", ("批量 · 带文件名", "Batch · keeps filenames")),
    ("z", "ZMODEM", ("窗口流式 · CRC32", "Streaming window · CRC32")),
]


def _l10n(pair: tuple[str, str]) -> str:
    """Choose the half of a ``(zh, en)`` pair for the current language."""
    from ..i18n import language

    return pair[1] if language() == "en" else pair[0]


def _pick(text: str) -> str:
    """Pick a word from the text."""
    words = text.split()
    return words[0] if words else ""


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
        self.title = QLabel(tr("xfer.dropzone"))
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

        self._sections: list[tuple[QLabel, str]] = []

        self.sec_protocol = self._title(tr("xfer.protocol"))
        self._sections.append((self.sec_protocol, "xfer.protocol"))
        left.addWidget(self.sec_protocol)
        proto_row = QHBoxLayout()
        proto_row.setSpacing(8)
        self.proto_group = QButtonGroup(self)
        self.proto_group.setExclusive(True)
        self._proto_buttons: list[tuple[QPushButton, str, str]] = []
        for key, name, detail in PROTOCOLS:
            button = QPushButton(f"{name}\n{_l10n(detail)}")
            button.setObjectName("Proto")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty("proto", key)
            button.setMinimumHeight(54)
            self.proto_group.addButton(button)
            proto_row.addWidget(button)
            self._proto_buttons.append((button, name, detail))
            if key == "x":
                button.setChecked(True)
        left.addLayout(proto_row)

        self.sec_direction = self._title(tr("xfer.direction"))
        self._sections.append((self.sec_direction, "xfer.direction"))
        left.addWidget(self.sec_direction)
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
        self._dir_buttons: dict[str, QPushButton] = {}
        for key, label in (("send", tr("xfer.send")), ("recv", tr("xfer.recv"))):
            button = QPushButton(label)
            button.setObjectName("Seg")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty("dir", key)
            if key == "send":
                button.setChecked(True)
            self.dir_group.addButton(button)
            dir_layout.addWidget(button)
            self._dir_buttons[key] = button
        self.dir_group.buttonClicked.connect(self._dir_clicked)
        dir_row.addWidget(dir_box)
        left.addLayout(dir_row)

        self.drop = DropZone()
        self.drop.fileDropped.connect(self._set_file)
        left.addWidget(self.drop)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_start = QPushButton(tr("xfer.start"))
        self.btn_start.setObjectName("Primary")
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.clicked.connect(self._start)
        actions.addWidget(self.btn_start, 1)
        self.btn_abort = QPushButton(tr("xfer.abort"))
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
        self.state_label = QLabel(tr("xfer.ready"))
        self.state_label.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        self.meta_label = QLabel("—")
        self.meta_label.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        meta.addWidget(self.state_label)
        meta.addStretch(1)
        meta.addWidget(self.meta_label)
        left.addLayout(meta)

        self.hint = QLabel(tr("xfer.savedir", path=paths.received_dir()))
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
        self.log_title = self._title(tr("xfer.log"))
        head.addWidget(self.log_title)
        head.addStretch(1)
        self.btn_clear_log = QPushButton(tr("xfer.clearlog"))
        clear = self.btn_clear_log
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

    def retranslate(self) -> None:
        for label, key in self._sections:
            label.setText(tr(key).upper())
        for button, name, detail in self._proto_buttons:
            button.setText(f"{name}\n{_l10n(detail)}")
        self._dir_buttons["send"].setText(tr("xfer.send"))
        self._dir_buttons["recv"].setText(tr("xfer.recv"))
        self.btn_start.setText(tr("xfer.start"))
        self.btn_abort.setText(tr("xfer.abort"))
        self.btn_clear_log.setText(tr("xfer.clearlog"))
        self.hint.setText(tr("xfer.savedir", path=paths.received_dir()))
        self.state_label.setText(tr("xfer.ready"))
        self.log_title.setText(tr("xfer.log").upper())

    def protocol(self) -> str:
        button = self.proto_group.checkedButton()
        return button.property("proto") if button else "x"

    def _dir_clicked(self, button) -> None:
        self.direction = button.property("dir")
        is_send = self.direction == "send"
        self.drop.setVisible(is_send)
        self.hint.setVisible(not is_send)
        if not is_send:
            self.state_label.setText(tr("xfer.waiting"))
            self.meta_label.setText("—")
            self.progress.setValue(0)

    def _set_file(self, path: str) -> None:
        self.file_path = path
        self.drop.set_file(path)

    def _start(self) -> None:
        if self.direction == "send" and not self.file_path:
            self.notify(tr("xfer.pickfile"), "warn")
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
            self.state_label.setText(tr("xfer.ready"))

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
        self.state_label.setText(tr("xfer.done") if ok else tr("xfer.failed"))
        self.log(("✔ " if ok else "✘ ") + message)
        if download_path:
            self.hint.setVisible(True)
            self.hint.setText(tr("xfer.saved", path=download_path))


def _pick(text: str) -> str:
    """Pick a word from the text."""
    words = text.split()
    return words[0] if words else ""
