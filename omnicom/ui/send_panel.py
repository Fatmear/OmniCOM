"""Bottom bar: single-line/multi-line send, HEX send, timed send, send file."""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..commands import decode_escapes, parse_hex
from ..i18n import tr
from . import theme
from .widgets import Button as QPushButton
from .widgets import Combo, GlassFrame, ToggleSwitch

LINE_ENDS = {"crlf": b"\r\n", "lf": b"\n", "cr": b"\r", "none": b""}


class SendInput(QPlainTextEdit):
    """Compose box with ↑/↓ command history and an optional Enter-to-send.

    Re-typing the same AT command is the most repeated action in a debug
    session, so ↑ walks back through what was already sent (R-090).  It only
    starts browsing once the cursor is on the **first** line: a multi-line
    command must be navigable with the normal cursor keys first, which is
    exactly what the user asked for.  ↓ walks forward again and finally
    restores the text that was being typed (the draft).

    ``enter_sends`` decides what a bare Enter does (R-094): with it on, Enter
    transmits and Shift+Enter inserts a line break; with it off, Enter inserts a
    line break and Ctrl+Enter transmits (the old behaviour, still available).
    """

    sendTriggered = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Enter inserts a line break by default: a composer that sends on Enter
        # loses a multi-line message to the first keystroke (R-094, revised
        # 2026-10-29).  The switch in the panel turns it on.
        self.enter_sends = False
        self.history: list[str] = []
        self.history_limit = HISTORY_DEFAULT
        self._hist_pos: int | None = None
        self._hist_draft = ""

    # --------------------------------------------------------------- history
    def push_history(self, text: str) -> None:
        """Remember a command that was actually sent."""
        entry = (text or "").strip("\r\n")
        if not entry.strip():
            self._reset_history()
            return
        if self.history and self.history[-1] == entry:
            self._reset_history()
            return
        self.history.append(entry)
        if len(self.history) > self.history_limit:
            del self.history[: len(self.history) - self.history_limit]
        self._reset_history()

    def set_history(self, entries) -> None:
        """Load persisted history (oldest first)."""
        self.history = [str(item) for item in (entries or [])][-self.history_limit:]
        self._reset_history()

    def set_history_limit(self, limit: int) -> int:
        """Bound the history and trim what no longer fits; returns the used value."""
        try:
            wanted = int(limit)
        except (TypeError, ValueError):
            wanted = HISTORY_DEFAULT
        self.history_limit = max(HISTORY_MIN, min(HISTORY_MAX, wanted))
        if len(self.history) > self.history_limit:
            del self.history[: len(self.history) - self.history_limit]
        self._reset_history()
        return self.history_limit

    def clear_history(self) -> None:
        """Forget every remembered command (also clears ↑/↓ state)."""
        self.history.clear()
        self._reset_history()

    def _reset_history(self) -> None:
        self._hist_pos = None
        self._hist_draft = ""

    def _apply(self, value: str) -> None:
        self.setPlainText(value)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.setTextCursor(cursor)

    def at_first_line(self) -> bool:
        """True when ↑ should start browsing instead of moving the cursor."""
        cursor = self.textCursor()
        return not cursor.hasSelection() and cursor.blockNumber() == 0

    def _browse(self, delta: int) -> bool:
        """Step through history; False means "let QPlainTextEdit handle it"."""
        if not self.history:
            return False
        if self._hist_pos is None:
            if delta > 0:                # ↓ at the bottom is just cursor motion
                return False
            self._hist_draft = self.toPlainText()
            self._hist_pos = len(self.history)
        position = max(0, self._hist_pos + delta)
        if position >= len(self.history):
            # Read the draft *before* resetting: _reset_history() clears it, and
            # the previous order left the box empty instead of restoring what the
            # user had been typing (found by probe, R-090).
            draft = self._hist_draft
            self._reset_history()
            self._apply(draft)
            return True
        self._hist_pos = position
        self._apply(self.history[position])
        return True

    # ------------------------------------------------------------------ keys
    def keyPressEvent(self, event) -> None:
        key, modifiers = event.key(), event.modifiers()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if modifiers & Qt.ControlModifier:
                self.sendTriggered.emit()
                return
            if self.enter_sends and not modifiers & (Qt.ShiftModifier | Qt.AltModifier):
                self.sendTriggered.emit()
                return
        plain = not modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier)
        if plain and key == Qt.Key_Up and self.at_first_line() and self._browse(-1):
            event.accept()
            return
        if plain and key == Qt.Key_Down and self._browse(1):
            event.accept()
            return
        super().keyPressEvent(event)


#: Height of one compact option row (label + switch) in the send panel.
OPT_ROW_H = 20
#: Height of the line-ending band: two rows plus their padding (R-110).
OPT_BAND_H = 2 * OPT_ROW_H + 4
#: The options use the compact face, so they do not compete with the buttons.
OPT_LABEL_PX = 11
OPT_SWITCH_W = 32
OPT_SWITCH_H = 16
#: The two option labels are given the same width, so the switches below each
#: other start at the same x instead of being pushed apart by "HEX" being
#: narrower than "回车发送" (R-114).
OPT_LABEL_W = 50
#: Command history is bounded on purpose: an unbounded list grows the settings
#: file forever and makes ↑ mean "scroll through last month" (R-116).
HISTORY_MIN = 5
HISTORY_DEFAULT = 50
HISTORY_MAX = 300


class FileLabel(QLabel):
    """Chosen-file caption that elides instead of pushing the buttons apart.

    ``setText`` keeps the full string (and in the tooltip), so callers - and the
    acceptance checks - still see a complete file name while the visible label
    shrinks to whatever room the row has (R-099).
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(48)
        # ``Ignored`` made the row overflow: Qt ignored the hint but still
        # reserved the label's minimum, pushing the whole row past the panel.
        # ``Preferred`` keeps the elided hint and still lets the label shrink to
        # its minimum, so the row can always be laid out inside the panel.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    def setText(self, text: str) -> None:
        self._full = text or ""
        self.setToolTip(self._full)
        self._refresh()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        if not self._full:
            super().setText("")
            return
        room = max(8, self.width() - 2)
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideMiddle, room))


class SendPanel(GlassFrame):
    sendRequested = Signal(bytes, str)   # payload, echo description
    fileChosen = Signal(str)             # "Open file": remember the path only
    fileSendRequested = Signal(str)      # "Send file": transmit the stored path
    abortRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent, radius=theme.RADIUS_CARD, shadow=False)
        self.setObjectName("SendPanel")
        self._timed = QTimer(self)
        self._timed.timeout.connect(self.emit_current)
        self._sending_file = False
        self._file_path = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 11, 12, 11)
        root.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(10)

        self.input = SendInput()
        # A QPlainTextEdit draws its frame rounded but leaves the *viewport* a
        # square, opaque rectangle on top - the "圆角混直角" the user saw.  The
        # viewport is made transparent so only the styled frame is visible.
        self.input.setObjectName("SendInput")
        self.input.setFrameShape(QPlainTextEdit.NoFrame)
        self.input.setAttribute(Qt.WA_StyledBackground, True)
        self.input.viewport().setAutoFillBackground(False)
        self.input.viewport().setStyleSheet("background: transparent;")
        self.input.setStyleSheet(
            f"QPlainTextEdit#SendInput {{ background: {theme.CARD};"
            f" border: 1px solid {theme.LINE};"
            f" border-radius: {theme.RADIUS_CTRL}px;"
            f" padding: 6px 10px; }}"
            f"QPlainTextEdit#SendInput:focus {{ border: 1px solid {theme.TEAL}; }}")
        self.input.setPlaceholderText(tr("send.placeholder"))
        self.input.setMinimumHeight(64)
        self.input.sendTriggered.connect(self.emit_current)

        left = QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(self.input, 1)
        top.addLayout(left, 1)

        side = QVBoxLayout()
        side.setSpacing(7)
        self.btn_send = QPushButton(tr("send.send"))
        self.btn_send.setObjectName("Primary")
        self.btn_send.setCursor(Qt.PointingHandCursor)
        self.btn_send.clicked.connect(self.emit_current)
        side.addWidget(self.btn_send)

        # The two file actions sit directly under 发送: they are send actions, so
        # the primary column is where they are looked for (R-103).
        self.btn_open_file = QPushButton(tr("send.openfile"))
        self.btn_open_file.setObjectName("Ghost")
        self.btn_open_file.setCursor(Qt.PointingHandCursor)
        self.btn_open_file.clicked.connect(self.pick_file)
        side.addWidget(self.btn_open_file)

        # One button for both jobs: 发送文件 while idle, 停止 while a transfer
        # runs (R-113).  A separate stop button appeared only during a send,
        # pushing the column around and reading like a different action.
        self.btn_file = QPushButton(tr("send.file"))
        self.btn_file.setObjectName("Ghost")
        self.btn_file.setCursor(Qt.PointingHandCursor)
        self.btn_file.clicked.connect(self._on_file_click)
        side.addWidget(self.btn_file)

        side.addStretch(1)

        # ---- line ending plus the two send options, as one band.  The options
        # are stacked to the right of the \r\n box and use the compact face, so
        # the pair is exactly as tall as the box: three controls at one height
        # read as a group instead of as a ragged row (R-110).
        opts = QHBoxLayout()
        opts.setSpacing(8)
        self.line_end = Combo()
        for key, label in (("none", tr("send.eol.none")), ("cr", "\\r"),
                           ("lf", "\\n"), ("crlf", "\\r\\n")):
            self.line_end.addItem(label, key)
        self.line_end.setCurrentIndex(self.line_end.findData("crlf"))
        self.line_end.setToolTip(tr("send.eol.tip"))
        # Fixed on both sides, so a translation cannot pull the band apart.
        self.line_end.setFixedHeight(OPT_BAND_H)
        opts.addWidget(self.line_end, 1)

        stack = QVBoxLayout()
        stack.setSpacing(0)
        stack.setContentsMargins(0, 0, 0, 0)
        self.hex_label, self.hex_send = self._option_row(
            "HEX", tr("send.hex.tip"), checked=False)
        stack.addWidget(self._option_holder)
        self.enter_label, self.enter_send = self._option_row(
            tr("send.enter"), tr("send.enter.tip"), checked=False)
        self.enter_send.toggled.connect(self.set_enter_sends)
        stack.addWidget(self._option_holder)
        opts.addLayout(stack)
        side.addLayout(opts)

        # The EOL / HEX row closes the side column, so it lines up with the
        # bottom edge of the input box instead of floating above a file row.
        top.addLayout(side)
        root.addLayout(top, 1)

        foot = QHBoxLayout()
        # 6 px: the row also carries the progress bar and the file name, and at
        # the window's minimum width every pixel of the row is spoken for
        # (R-106).
        foot.setSpacing(6)

        self.timed_label = QLabel(tr("send.timed"))
        self.timed_label.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        foot.addWidget(self.timed_label)

        # The unit is the spin box's own suffix: a separate "ms" label cost 30 px
        # of a row that now also carries the progress bar and the file name
        # (R-106).
        self.interval = QSpinBox()
        self.interval.setRange(10, 3600000)
        self.interval.setSingleStep(10)
        self.interval.setValue(1000)
        self.interval.setSuffix(" ms")
        self.interval.setFixedWidth(84)
        foot.addWidget(self.interval)

        self.btn_timed = QPushButton(tr("send.start"))
        self.btn_timed.setObjectName("Ghost")
        self.btn_timed.setCursor(Qt.PointingHandCursor)
        self.btn_timed.clicked.connect(self.toggle_timed)
        foot.addWidget(self.btn_timed)

        # Push the file group to the right: the progress bar has to sit against
        # the file name, and a bar that starts immediately after 定时发送 read as
        # part of the timed-send group (R-106, revised 2026-10-29).
        foot.addStretch(1)

        # Progress + chosen name close the timed-send row, as one stretchable
        # group: the group is given every pixel the row has left, so the bar and
        # the name share the spare room instead of pushing the row past the panel
        # (the name used to be cut off outside the frame - R-106).
        self.file_row = QWidget()
        self.file_row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        file_box = QHBoxLayout(self.file_row)
        file_box.setContentsMargins(0, 0, 0, 0)
        file_box.setSpacing(6)

        self.file_progress = QProgressBar()
        self.file_progress.setObjectName("FileProgress")
        self.file_progress.setRange(0, 100)
        self.file_progress.setValue(0)
        self.file_progress.setTextVisible(False)
        self.file_progress.setFixedHeight(8)
        # min/max instead of a fixed width: it shrinks with the window instead
        # of forcing the row wider than the panel.
        self.file_progress.setMinimumWidth(56)
        self.file_progress.setMaximumWidth(120)
        self.file_progress.hide()
        file_box.addWidget(self.file_progress)

        self.file_hint = FileLabel("")
        self.file_hint.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        # Right-aligned: the spare width has to land *left* of the name, so it
        # hugs the inner edge of the panel instead of floating in the middle of
        # the row (R-106, revised 2026-10-28).
        self.file_hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # 0: at a very narrow panel the name gives up its room instead of pushing
        # the row outside the frame; the full name stays in the tooltip.
        self.file_hint.setMinimumWidth(0)
        file_box.addWidget(self.file_hint, 1)

        foot.addWidget(self.file_row, 3)
        root.addLayout(foot)

        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.input)
        shortcut.activated.connect(self.emit_current)

    def _option_row(self, text: str, tip: str, checked: bool):
        """Build one compact "label + switch" row; returns ``(label, switch)``.

        The row is exactly ``OPT_ROW_H`` tall, which is what lets two of them
        match the height of the \r\n box.  The assembled widget is left in
        ``self._option_holder`` for the caller to add to a stack (R-110).
        """
        holder = QWidget(self)
        holder.setFixedHeight(OPT_ROW_H)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        label = QLabel(text, holder)
        label.setStyleSheet(f"color: {theme.DIM}; font-size: {OPT_LABEL_PX}px;")
        # Fixed width, so the switch below starts at the same x as the switch
        # above even though "HEX" is much shorter than "回车发送" (R-114).
        label.setFixedWidth(OPT_LABEL_W)
        row.addWidget(label)
        switch = ToggleSwitch(holder, checked=checked,
                              width=OPT_SWITCH_W, height=OPT_SWITCH_H)
        switch.setToolTip(tip)
        row.addWidget(switch)
        row.addStretch(1)
        self._option_holder = holder
        return label, switch

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(hint.width(), max(150, self.minimumSizeHint().height()))

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
        # Keep the raw text, not the "[HEX] ..." description: ↑ must put back a
        # command that can be edited and sent again.
        self.input.push_history(self.input.toPlainText())
        self.sendRequested.emit(payload, description)
        if not self._timed.isActive():
            self.input.clear()

    # ------------------------------------------------------------ enter/send
    def set_enter_sends(self, enabled: bool) -> None:
        """Enter transmits when True, inserts a line break when False (R-094)."""
        enabled = bool(enabled)
        self.input.enter_sends = enabled
        if self.enter_send.isChecked() != enabled:
            self.enter_send.blockSignals(True)
            self.enter_send.setChecked(enabled)
            self.enter_send.blockSignals(False)

    def enter_sends(self) -> bool:
        return bool(self.input.enter_sends)

    def history_entries(self) -> list[str]:
        return list(self.input.history)

    def set_history_entries(self, entries) -> None:
        self.input.set_history(entries)

    def set_history_limit(self, limit: int) -> int:
        """How many commands ↑ remembers (R-116)."""
        return self.input.set_history_limit(limit)

    def history_limit(self) -> int:
        return int(self.input.history_limit)

    def clear_history(self) -> None:
        self.input.clear_history()

    def toggle_timed(self) -> None:
        if self._timed.isActive():
            self._timed.stop()
            self.btn_timed.setText(tr("send.start"))
            self.btn_timed.setObjectName("Ghost")
            self.btn_send.setEnabled(True)
        else:
            payload, _ = self.payload()
            if not payload:
                return
            self._timed.start(max(10, self.interval.value()))
            self.btn_timed.setText(tr("send.stop"))
            self.btn_timed.setObjectName("Danger")
            self.emit_current()
        self._repolish(self.btn_timed)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def retranslate(self) -> None:
        self.input.setPlaceholderText(tr("send.placeholder"))
        self.btn_send.setText(tr("send.send"))
        self.line_end.setItemText(0, tr("send.eol.none"))
        self.line_end.setToolTip(tr("send.eol.tip"))
        self.hex_send.setToolTip(tr("send.hex.tip"))
        self.timed_label.setText(tr("send.timed"))
        self.enter_label.setText(tr("send.enter"))
        self.enter_send.setToolTip(tr("send.enter.tip"))
        self.btn_timed.setText(tr("send.stop") if self._timed.isActive()
                                else tr("send.start"))
        self.btn_open_file.setText(tr("send.openfile"))
        self.btn_file.setText(tr("send.stop") if self._sending_file
                              else tr("send.file"))
        # Re-render the file row so its hint/prompt follows the new language.
        if self._file_path and not self._sending_file:
            self.file_hint.setText(f"{os.path.basename(self._file_path)}  "
                                   f"({os.path.getsize(self._file_path)} B)")
        elif not self._sending_file:
            self.file_hint.setText("")

    # ------------------------------------------------------------------ file
    def pick_file(self) -> None:
        """Choose the file to send; nothing is transmitted here."""
        path, _ = QFileDialog.getOpenFileName(self, tr("send.pickfile"))
        if not path:
            return
        self._file_path = path
        self.file_hint.setText(f"{os.path.basename(path)}  "
                               f"({os.path.getsize(path)} B)")
        self.fileChosen.emit(path)

    def chosen_file(self) -> str:
        return self._file_path

    def send_chosen_file(self) -> None:
        """Transmit whatever ``pick_file`` selected."""
        if not self._file_path:
            self.file_hint.setText(tr("send.nofile"))
            return
        self.fileSendRequested.emit(self._file_path)

    def _on_file_click(self) -> None:
        """发送文件 while idle, 停止 while a transfer runs (R-113)."""
        if self._sending_file:
            self.abortRequested.emit()
            return
        self.send_chosen_file()

    def set_file_busy(self, busy: bool, text: str = "") -> None:
        """Flip the same button between 发送文件 and 停止 (R-113)."""
        self._sending_file = busy
        self.btn_file.setText(tr("send.stop") if busy else tr("send.file"))
        self.btn_file.setObjectName("Danger" if busy else "Ghost")
        self._repolish(self.btn_file)
        # The stop button must stay clickable while it is a stop button.
        self.btn_file.setEnabled(True)
        self.btn_open_file.setEnabled(not busy)
        self.file_progress.setVisible(busy)
        if not busy:
            self.file_progress.setValue(0)
        self.file_hint.setText(text)

    def set_file_progress(self, sent: int, total: int, name: str) -> None:
        percent = int(sent / total * 100) if total else 0
        self.file_progress.setVisible(True)
        self.file_progress.setValue(max(0, min(100, percent)))
        self.file_hint.setText(tr("send.progress", name=os.path.basename(name),
                                  percent=percent, sent=sent, total=total))
