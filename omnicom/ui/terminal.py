"""Terminal page: an interactive console for a linux serial login.

The log page is a *monitor* (it prints lines and formats them).  This page is a
*terminal*: keystrokes leave immediately, the device's escape sequences are
interpreted, and ``\\r`` overwrites the current line the way a real tty does.
That is what makes ``login:`` / ``Password:`` / shell prompts usable.

Supported escape handling is deliberately small but honest:

* ``\\r`` / ``\\n`` / ``\\b`` / ``\\t`` cursor motion
* SGR colours (``ESC[3x``, ``ESC[9x``, ``ESC[0m``)
* ``ESC[K`` erase-to-end-of-line, ``ESC[2J`` clear screen
* everything else in a CSI sequence is swallowed instead of printed
"""

from __future__ import annotations

import codecs

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from . import theme
from .widgets import Combo, GlyphButton

MAX_BLOCKS = 5000

# ANSI 30..37 / 90..97 on the dark console surface.
_ANSI = {
    30: "#3B4453", 31: "#F87171", 32: "#5EE29B", 33: "#F0C572",
    34: "#7CB0FF", 35: "#C4A6FF", 36: "#63D8E8", 37: "#D6E2F0",
    90: "#6B7688", 91: "#FF9B9B", 92: "#8AF0BB", 93: "#FFD79A",
    94: "#A6C8FF", 95: "#D8C2FF", 96: "#95EAF5", 97: "#FFFFFF",
}
DEFAULT_FG = theme.TERM_INK

KEY_SEQUENCES = {
    Qt.Key_Up: b"\x1b[A",
    Qt.Key_Down: b"\x1b[B",
    Qt.Key_Right: b"\x1b[C",
    Qt.Key_Left: b"\x1b[D",
    Qt.Key_Home: b"\x1b[H",
    Qt.Key_End: b"\x1b[F",
    Qt.Key_PageUp: b"\x1b[5~",
    Qt.Key_PageDown: b"\x1b[6~",
    Qt.Key_Delete: b"\x1b[3~",
    Qt.Key_Insert: b"\x1b[2~",
    Qt.Key_F1: b"\x1bOP",
    Qt.Key_F2: b"\x1bOQ",
    Qt.Key_F3: b"\x1bOR",
    Qt.Key_F4: b"\x1bOS",
}


class TerminalView(QPlainTextEdit):
    """Read-only console widget that forwards keystrokes and emulates a tty."""

    keyBytes = Signal(bytes)

    def __init__(self, parent=None, enter: bytes = b"\r") -> None:
        super().__init__(parent)
        self.setObjectName("Terminal")
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setMaximumBlockCount(MAX_BLOCKS)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setFrameShape(QPlainTextEdit.NoFrame)
        self.setFont(theme.mono_font(theme.log_px()))
        self.setStyleSheet(
            f"QPlainTextEdit#Terminal {{ background: {theme.TERM_BG};"
            f" border: 0; border-radius: 14px; padding: 8px 12px;"
            f' font-family: "{theme.resolve_mono()}";'
            f" font-size: {theme.log_px()}px;"
            f" color: {theme.TERM_INK};"
            " selection-background-color: rgba(94, 226, 155, 60); }")
        self.enter = enter
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._col = 0
        self._fg = DEFAULT_FG
        self._bold = False
        self._esc = ""

    def apply_font(self) -> None:
        """Follow the log font preference (same monospace face, same size)."""
        size = theme.log_px()
        self.setFont(theme.mono_font(size))
        self.setStyleSheet(
            f"QPlainTextEdit#Terminal {{ background: {theme.TERM_BG};"
            f" border: 0; border-radius: 14px; padding: 8px 12px;"
            f' font-family: "{theme.resolve_mono()}"; font-size: {size}px;'
            f" color: {theme.TERM_INK};"
            " selection-background-color: rgba(94, 226, 155, 60); }")

    # ------------------------------------------------------------------ input
    def set_enter(self, enter: bytes) -> None:
        self.enter = enter

    def keyPressEvent(self, event) -> None:
        key = event.key()
        modifiers = event.modifiers()

        # Ctrl+Shift+C / Ctrl+Shift+V stay as copy/paste; plain Ctrl+C is the
        # interrupt byte, which is what a serial console expects.
        if modifiers & Qt.ControlModifier and modifiers & Qt.ShiftModifier:
            if key == Qt.Key_C:
                super().keyPressEvent(event)
                return
            if key == Qt.Key_V:
                from PySide6.QtWidgets import QApplication

                text = QApplication.clipboard().text()
                if text:
                    self.keyBytes.emit(text.encode("utf-8"))
                return
        if key in self.IGNORED_KEYS:
            return

        if key in KEY_SEQUENCES and not modifiers & Qt.AltModifier:
            self.keyBytes.emit(KEY_SEQUENCES[key])
            event.accept()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.keyBytes.emit(self.enter)
            event.accept()
            return
        if key == Qt.Key_Backspace:
            self.keyBytes.emit(b"\x7f")
            event.accept()
            return
        if key == Qt.Key_Tab:
            self.keyBytes.emit(b"\t")
            event.accept()
            return
        if key == Qt.Key_Escape:
            self.keyBytes.emit(b"\x1b")
            event.accept()
            return
        if modifiers & Qt.ControlModifier and Qt.Key_A <= key <= Qt.Key_Z:
            self.keyBytes.emit(bytes((key - Qt.Key_A + 1,)))
            event.accept()
            return

        text = event.text()
        if text and text.isprintable():
            self.keyBytes.emit(text.encode("utf-8"))
            event.accept()
            return
        super().keyPressEvent(event)

    IGNORED_KEYS = (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta,
                    Qt.Key_CapsLock, Qt.Key_NumLock)

    # ------------------------------------------------------------------ menu
    def contextMenuEvent(self, event) -> None:
        """Copy / paste / select-all / clear, as a terminal is expected to have.

        Paste must go through ``keyBytes`` like a typed character would: the
        device is the destination, not this widget (which is read-only) - a
        clipboard paste that only changed the display would look like it worked
        while nothing reached the port (R-091).
        """
        menu = QMenu(self)
        menu.setStyleSheet(theme.app_stylesheet())

        copy = menu.addAction(tr("term.menu.copy"))
        copy.setShortcut("Ctrl+Shift+C")
        copy.setEnabled(self.textCursor().hasSelection())
        paste = menu.addAction(tr("term.menu.paste"))
        paste.setShortcut("Ctrl+Shift+V")
        paste.setEnabled(bool(QApplication.clipboard().text()))
        select_all = menu.addAction(tr("term.menu.selectall"))
        menu.addSeparator()
        clear = menu.addAction(tr("term.menu.clear"))

        picked = menu.exec(event.globalPos())
        if picked is copy:
            self.copy_selection()
        elif picked is paste:
            self.paste_clipboard()
        elif picked is select_all:
            self.selectAll()
        elif picked is clear:
            self.reset()

    def copy_selection(self) -> bool:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return False
        QApplication.clipboard().setText(
            cursor.selectedText().replace("\u2029", "\n"))
        return True

    def paste_clipboard(self) -> None:
        """Send the clipboard text to the device as if it were typed."""
        text = QApplication.clipboard().text()
        if text:
            self.keyBytes.emit(text.encode("utf-8"))

    # ------------------------------------------------------------------ output
    def reset(self) -> None:
        self.clear()
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._col = 0
        self._fg = DEFAULT_FG
        self._esc = ""

    def feed(self, data: bytes) -> None:
        text = self._decoder.decode(data, final=False)
        if not text:
            return
        index = 0
        length = len(text)
        while index < length:
            char = text[index]
            if self._esc:
                index = self._consume_escape(text, index)
                continue
            if char == "\x1b":
                self._esc = "\x1b"
                index += 1
                continue
            if char == "\r":
                self._carriage_return()
                index += 1
                continue
            if char == "\n":
                self._newline()
                index += 1
                continue
            if char == "\b":
                self._backspace()
                index += 1
                continue
            if char == "\t":
                self._write(" " * (8 - self._col % 8))
                index += 1
                continue
            if char < " ":
                index += 1
                continue
            end = index
            while end < length and text[end] >= " " and text[end] != "\x1b":
                end += 1
            self._write(text[index:end])
            index = end
        self._scroll_to_end()

    # -- cursor primitives --------------------------------------------------
    def _fmt(self) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self._fg))
        if self._bold:
            fmt.setFontWeight(QFont.DemiBold)
        return fmt

    def _cursor_at(self, block_no: int, column: int) -> QTextCursor:
        block = self.document().findBlockByNumber(block_no)
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, column)
        return cursor

    def _write(self, text: str) -> None:
        block_no = self.document().blockCount() - 1
        cursor = self._cursor_at(block_no, self._col)
        fmt = self._fmt()
        for char in text:
            if cursor.atBlockEnd():
                cursor.insertText(char, fmt)
            else:
                cursor.deleteChar()
                cursor.insertText(char, fmt)
        self._col += len(text)

    def _newline(self) -> None:
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.End)
        cursor.insertBlock()
        self._col = 0

    def _carriage_return(self) -> None:
        self._col = 0

    def _backspace(self) -> None:
        if self._col <= 0:
            return
        self._col -= 1
        cursor = self._cursor_at(self.document().blockCount() - 1, self._col)
        if not cursor.atBlockEnd():
            cursor.deleteChar()

    def _clear_screen(self) -> None:
        self.clear()
        self._col = 0

    def _erase_line(self) -> None:
        block_no = self.document().blockCount() - 1
        cursor = self._cursor_at(block_no, self._col)
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()

    def _scroll_to_end(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    # -- escape sequences ---------------------------------------------------
    def _consume_escape(self, text: str, index: int) -> int:
        char = text[index]
        if self._esc == "\x1b":
            if char == "[":
                self._esc = "\x1b["
                return index + 1
            if char == "]":
                self._esc = "\x1b]"
                return index + 1
            # Two-character escape (ESC c, ESC 7 ...) - ignore it.
            self._esc = ""
            return index + 1
        if self._esc == "\x1b]":
            # OSC ... terminated by BEL or ST
            if char == "\x07" or char == "\\":
                self._esc = ""
            return index + 1
        # CSI: collect until a final byte in @..~
        if "@" <= char <= "~":
            self._apply_csi(self._esc[2:] + char)
            self._esc = ""
        else:
            self._esc += char
            if len(self._esc) > 24:
                self._esc = ""
        return index + 1

    def _apply_csi(self, sequence: str) -> None:
        final = sequence[-1]
        body = sequence[:-1]
        params = [part for part in body.lstrip("?").split(";") if part != ""]
        numbers = []
        for part in params:
            try:
                numbers.append(int(part))
            except ValueError:
                numbers.append(0)
        if final == "m":
            self._apply_sgr(numbers or [0])
        elif final == "J":
            if (numbers or [0])[0] in (2, 3):
                self._clear_screen()
        elif final == "K":
            self._erase_line()
        # everything else (cursor motion, mode set/reset, ...) is ignored on
        # purpose: a line-oriented console does not need a full VT100.

    def _apply_sgr(self, codes: list[int]) -> None:
        index = 0
        while index < len(codes):
            code = codes[index]
            if code == 0:
                self._fg = DEFAULT_FG
                self._bold = False
            elif code == 1:
                self._bold = True
            elif code == 22:
                self._bold = False
            elif code in _ANSI:
                self._fg = _ANSI[code]
            elif code == 38 or code == 48:
                # 256-colour / truecolour - consume its parameters.
                if index + 1 < len(codes) and codes[index + 1] == 5:
                    index += 2
                elif index + 1 < len(codes) and codes[index + 1] == 2:
                    index += 4
            index += 1


class TerminalPage(QWidget):
    """Toolbar + terminal surface, ready to drop into a stack page."""

    keyBytes = Signal(bytes)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        card = QWidget()
        card.setObjectName("TerminalCard")
        card.setStyleSheet(
            f"QWidget#TerminalCard {{ background: {theme.TERM_BG};"
            " border-radius: 18px; }")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 10)
        card_layout.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.title = QLabel(tr("term.title"))
        self.title.setStyleSheet("color: rgba(214, 226, 240, 190); font-size: 11.5px;")
        bar.addWidget(self.title)

        self.enter_label = QLabel(tr("term.enter"))
        self.enter_label.setStyleSheet("color: rgba(214, 226, 240, 150); font-size: 11px;")
        bar.addWidget(self.enter_label)
        self.enter_box = Combo(self)
        for key, label in (("cr", "CR (\\r)"), ("lf", "LF (\\n)"), ("crlf", "CRLF")):
            self.enter_box.addItem(label, key)
        self.enter_box.setFixedWidth(124)
        self.enter_box.setStyleSheet(
            f"QComboBox {{ background: rgba(255,255,255,18); color: {theme.TERM_INK};"
            " border: 1px solid rgba(255,255,255,40); border-radius: 8px; }")
        self.enter_box.currentIndexChanged.connect(self._enter_changed)
        bar.addWidget(self.enter_box)

        bar.addStretch(1)
        self.btn_clear = GlyphButton(GlyphButton.CLEAR, self, size=theme.ICON_MD,
                                    object_name="IconBtn")
        self.btn_clear.setToolTip(tr("term.clear"))
        bar.addWidget(self.btn_clear)

        self.btn_full = GlyphButton(GlyphButton.EXPAND, self, size=theme.ICON_MD,
                                   object_name="IconBtn")
        self.btn_full.setToolTip(tr("term.reset"))
        bar.addWidget(self.btn_full)
        card_layout.addLayout(bar)

        self.view = TerminalView(self)
        self.view.keyBytes.connect(self.keyBytes.emit)
        card_layout.addWidget(self.view, 1)

        self.hint = QLabel(tr("term.hint"))
        self.hint.setStyleSheet("color: rgba(214, 226, 240, 130); font-size: 11px;")
        self.hint.setWordWrap(True)
        card_layout.addWidget(self.hint)

        layout.addWidget(card, 1)

        self.btn_clear.clicked.connect(self.view.reset)
        self.btn_full.clicked.connect(self.view.reset)

    # ------------------------------------------------------------------ api
    def feed(self, data: bytes) -> None:
        self.view.feed(data)

    def reset(self) -> None:
        self.view.reset()

    def apply_font(self) -> None:
        """Terminal text follows the log font setting (R-096)."""
        self.view.apply_font()

    def retranslate(self) -> None:
        self.title.setText(tr("term.title"))
        self.enter_label.setText(tr("term.enter"))
        self.btn_clear.setToolTip(tr("term.clear"))
        self.btn_full.setToolTip(tr("term.reset"))
        self.hint.setText(tr("term.hint"))

    def _enter_changed(self, _index: int) -> None:
        key = self.enter_box.currentData() or "cr"
        self.view.set_enter({"cr": b"\r", "lf": b"\n", "crlf": b"\r\n"}.get(key, b"\r"))
