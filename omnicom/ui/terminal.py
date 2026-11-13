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

from PySide6.QtCore import QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextCharFormat, QTextCursor
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
        # Terminal text follows the *log* font setting (R-096), so the interface
        # font sweep has to skip it.
        theme.lock_ui_font(self)
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
        # ---- cursor (R-193)
        # A read-only ``QPlainTextEdit`` paints **no** caret at all: measured, the
        # caret rect of a focused read-only edit is background colour only, which
        # is exactly the "终端模式不显示光标" the user reported.  A serial console
        # needs a cursor anyway - it shows where the device's next character will
        # land - so the widget draws its own block caret at ``_col``.
        self._caret_on = True
        #: Whether the cursor is drawn *solid*.  Mirrors ``hasFocus()`` but is
        #: settable, so the GUI selftest can verify both appearances on an
        #: offscreen platform where the widget is never the active one (L-256).
        self._caret_focused = False
        self._caret_timer = QTimer(self)
        self._caret_timer.setInterval(530)
        self._caret_timer.timeout.connect(self._blink_caret)
        self._caret_timer.start()

    def _blink_caret(self) -> None:
        # Only the caret moves, and a focused terminal is what the eye is on; an
        # unfocused one keeps a hollow block instead of blinking (see
        # ``_paint_caret``), so no repaint is wasted while the page is hidden.
        if not self.isVisible():
            return
        self._caret_on = not self._caret_on
        self.viewport().update()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._caret_focused = True
        self._caret_on = True
        self.viewport().update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._caret_focused = False
        self.viewport().update()

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
        if key == Qt.Key_Backtab:
            # Shift+Tab is "backwards tabulation": a real tty sends CSI Z, and
            # shell completion (bash/zsh) relies on it.
            self.keyBytes.emit(b"\x1b[Z")
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

    def focusNextPrevChild(self, next_child: bool) -> bool:
        """Keep Tab inside the terminal instead of tabbing to the next widget.

        ``QFocusEvent`` / ``QWidget::focusNextPrevChild`` runs **before**
        ``keyPressEvent``: Qt treats Tab as a focus gesture, so a console that
        only handled ``Qt.Key_Tab`` in ``keyPressEvent`` never saw it and Tab did
        nothing at all on a serial console (L-250).  Returning ``False`` makes Qt
        deliver the key, and the ``Qt.Key_Tab`` branch below emits ``\\t``.
        """
        return False

    # ------------------------------------------------------------------ menu
    def contextMenuEvent(self, event) -> None:
        """Copy / paste / select-all / clear, as a terminal is expected to have.

        Paste must go through ``keyBytes`` like a typed character would: the
        device is the destination, not this widget (which is read-only) - a
        clipboard paste that only changed the display would look like it worked
        while nothing reached the port (R-091).
        """
        menu = QMenu(self)
        menu.setStyleSheet(theme.menu_stylesheet())

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
        # The console cursor moved with the write, so it has to be repainted now
        # rather than on the next blink tick (R-193).
        self.viewport().update()

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

    # -- cursor (R-193) -----------------------------------------------------
    def _caret_rect(self) -> QRect:
        """Where the console cursor is: the current write column of the last block."""
        block_no = max(0, self.document().blockCount() - 1)
        cursor = self._cursor_at(block_no, self._col)
        return self.cursorRect(cursor)

    def _paint_caret(self, painter: QPainter) -> None:
        """Draw the terminal cursor: solid while focused, hollow otherwise.

        Drawn by hand because ``QPlainTextEdit`` skips the caret entirely when it
        is read-only (R-193), and because a console cursor marks the *write*
        position rather than a text selection anchor.

        ``self._caret_focused`` exists so the state is testable without stealing
        the window's real focus: the GUI selftest runs on an offscreen platform
        where a widget often never becomes the active one, and keying the
        appearance off ``hasFocus()`` alone would leave the solid state
        unverifiable (L-256).
        """
        rect = self._caret_rect()
        if rect.isNull() or rect.height() <= 0:
            return
        column = QRectF(rect.x(), rect.y(), max(2.0, rect.width()),
                        float(rect.height()))
        painter.save()
        try:
            colour = QColor(theme.TERM_INK)
            if self._caret_focused and self._caret_on:
                colour.setAlpha(215)
                painter.setPen(Qt.NoPen)
                painter.fillRect(column, colour)
            else:
                # Unfocused or in the dark half of the blink: an outline keeps
                # the position visible without looking like a text selection.
                colour.setAlpha(150)
                painter.setPen(QPen(colour, 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(column.adjusted(0.5, 0.5, -0.5, -0.5))
        finally:
            painter.restore()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        try:
            self._paint_caret(painter)
        finally:
            painter.end()

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

        # 清屏并重置终端状态 is a *reset*, so it carries the circular-arrow glyph;
        # it used to be drawn as an expand frame, which made the two actions
        # indistinguishable and left the page with no full-screen control at all
        # (R-188).
        self.btn_reset = GlyphButton(GlyphButton.RESET, self, size=theme.ICON_MD,
                                     object_name="IconBtn")
        self.btn_reset.setToolTip(tr("term.reset"))
        bar.addWidget(self.btn_reset)

        self.btn_full = GlyphButton(GlyphButton.EXPAND, self, size=theme.ICON_MD,
                                    object_name="IconBtn")
        self.btn_full.setToolTip(tr("term.fullscreen"))
        bar.addWidget(self.btn_full)
        card_layout.addLayout(bar)

        self.card = card
        self._overlay: QWidget | None = None

        self.view = TerminalView(self)
        self.view.keyBytes.connect(self.keyBytes.emit)
        card_layout.addWidget(self.view, 1)

        self.hint = QLabel(tr("term.hint"))
        self.hint.setStyleSheet("color: rgba(214, 226, 240, 130); font-size: 11px;")
        self.hint.setWordWrap(True)
        card_layout.addWidget(self.hint)

        layout.addWidget(card, 1)

        self.btn_clear.clicked.connect(self.view.reset)
        self.btn_reset.clicked.connect(self.view.reset)
        self.btn_full.clicked.connect(self.toggle_fullscreen)

    # ------------------------------------------------------------------ api
    def feed(self, data: bytes) -> None:
        self.view.feed(data)

    def reset(self) -> None:
        self.view.reset()

    def apply_font(self) -> None:
        """Terminal text follows the log font setting (R-096)."""
        self.view.apply_font()

    # ------------------------------------------------------------ full screen
    def toggle_fullscreen(self) -> None:
        """Grow the terminal card over the whole window, and back (R-188)."""
        self.set_fullscreen(self._overlay is None)

    def set_fullscreen(self, value: bool) -> None:
        """Move the card into a window-sized overlay, or put it back.

        The button used to be wired to ``view.reset`` with the tooltip 清屏并重置
        ... and the expand glyph, so the page had *no* full-screen action while
        looking like it did.  Full screen reuses the search overlay pattern: the
        card is reparented (not copied), so the console keeps its scrollback and
        keystrokes land in the same widget (R-188).
        """
        window = self.window()
        if value:
            if self._overlay is not None:
                return
            overlay = QWidget(window.centralWidget() or window)
            overlay.setObjectName("TermOverlay")
            overlay.setStyleSheet(
                f"QWidget#TermOverlay {{ background: {theme.TERM_BG};"
                f" border-radius: {theme.RADIUS_CARD}px; }}")
            layout = QVBoxLayout(overlay)
            layout.setContentsMargins(0, 0, 0, 0)
            self.card.setParent(overlay)
            layout.addWidget(self.card)
            rect = self._overlay_rect(window)
            overlay.setGeometry(rect)
            overlay.show()
            overlay.raise_()
            self._overlay = overlay
        else:
            overlay = self._overlay
            if overlay is None:
                return
            self.card.setParent(self)
            self.layout().addWidget(self.card)
            overlay.deleteLater()
            self._overlay = None
        self.btn_full.set_glyph(
            GlyphButton.SHRINK if self._overlay is not None else GlyphButton.EXPAND)
        self.btn_full.setToolTip(
            tr("term.exitfull") if self._overlay is not None else tr("term.fullscreen"))
        self.view.setFocus()

    def _overlay_rect(self, window) -> QRect:
        """Body area of the window: the title bar and the caption row stay put."""
        central = window.centralWidget()
        if central is None:
            return self.rect()
        title = getattr(window, "titlebar", None)
        strip = getattr(window, "status_strip", None)
        top = (title.geometry().bottom() + 10) if title is not None else 10
        bottom = (strip.geometry().top() - 4) if strip is not None else (central.height() - 10)
        area = central.rect()
        return QRect(area.left() + 10, top, max(200, area.width() - 20),
                     max(160, bottom - top))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._overlay is not None:
            self._overlay.setGeometry(self._overlay_rect(self.window()))

    def retranslate(self) -> None:
        self.title.setText(tr("term.title"))
        self.enter_label.setText(tr("term.enter"))
        self.btn_clear.setToolTip(tr("term.clear"))
        self.btn_reset.setToolTip(tr("term.reset"))
        self.btn_full.setToolTip(tr("term.exitfull") if self._overlay is not None
                                 else tr("term.fullscreen"))
        self.hint.setText(tr("term.hint"))

    def _enter_changed(self, _index: int) -> None:
        key = self.enter_box.currentData() or "cr"
        self.view.set_enter({"cr": b"\r", "lf": b"\n", "crlf": b"\r\n"}.get(key, b"\r"))
