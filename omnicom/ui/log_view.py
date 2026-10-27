"""Received/sent log view.

QPlainTextEdit is used on purpose: block-count capping and appending thousands of
lines per second is cheap there, whereas a per-row widget model would stall the
GUI thread at high baud rates.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from . import theme

MAX_BLOCKS = 20000

# "收←" / "发→" read faster than RX/TX in a long log, and the direction column
# keeps a fixed width so the payload starts at the same x on every line.
_DIR_LABELS = {"RX": "收←", "TX": "发→", "··": "··", "!!": "!!"}
_DIR_COLORS = {
    "RX": theme.TEAL,
    "TX": theme.VIOLET,
    "··": theme.DIM,
    "!!": theme.AMBER,
}
_TEXT_COLORS = {
    "rx": theme.INK,
    "tx": "#3A2F86",
    "sys": theme.DIM,
    "evt": theme.AMBER,
}


def stamp(ms: float) -> str:
    """Format a millisecond epoch timestamp as HH:MM:SS.mmm."""
    return datetime.fromtimestamp(ms / 1000.0).strftime("%H:%M:%S.%f")[:-3]


def escape_control(text: str) -> str:
    """Render invisible characters as literal escapes (R-015)."""
    out: list[str] = []
    for char in text:
        code = ord(char)
        if char == "\r":
            out.append("\\r")
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\x{code:02x}")
        else:
            out.append(char)
    return "".join(out)


class LogView(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None, show_dir: bool = True,
                 pixel_size: int = 13) -> None:
        super().__init__(parent)
        self.setObjectName("Log")
        self.show_dir = show_dir
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setMaximumBlockCount(MAX_BLOCKS)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setFrameShape(QPlainTextEdit.NoFrame)
        self.setWordWrapMode(self.wordWrapMode())
        font = QFont("Cascadia Code")
        font.setStyleHint(QFont.Monospace)
        font.setPixelSize(pixel_size)
        font.setFixedPitch(True)
        self.setFont(font)
        self.setStyleSheet(
            "QPlainTextEdit#Log { background: transparent; border: 0;"
            " padding: 2px 10px 8px 12px;"
            " selection-background-color: rgba(46, 196, 176, 90); }")
        self.show_ts = True
        self.show_ctrl = False
        self.autoscroll = True
        self.trimmed = 0
        self._pending = False
        self._ts_format = self._make_format(theme.DIM)
        self._mark_format = self._make_format("#0A3F3A", background="#BFF0E6")
        self._marker_format = self._make_format(theme.DIM)
        self._pending_format = self._make_format("#5A6A85")

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _make_format(color: str, background: str | None = None, bold: bool = False,
                     italic: bool = False) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if background:
            fmt.setBackground(QColor(background))
        if bold:
            fmt.setFontWeight(QFont.DemiBold)
        fmt.setFontItalic(italic)
        return fmt

    def _at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _follow(self, was_at_bottom: bool) -> None:
        if self.autoscroll and was_at_bottom:
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())

    # ------------------------------------------------------------------ lines
    def append_line(self, ts: float, direction: str, text: str, kind: str = "rx",
                    keywords: Iterable[str] = (), lite: bool = False) -> None:
        at_bottom = self._at_bottom()
        self._clear_pending_internal()

        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.End)
        if self.document().blockCount() > 1 or self.document().characterCount() > 1:
            cursor.insertBlock()

        if self.show_ts:
            cursor.insertText(f"[{stamp(ts)}]", self._ts_format)

        if self.show_dir:
            label = _DIR_LABELS.get(direction, direction)
            cursor.insertText(label, self._make_format(_DIR_COLORS.get(direction, theme.DIM)))
            if direction in ("RX", "TX"):
                cursor.insertText("◇", self._marker_format)

        body_format = self._make_format(_TEXT_COLORS.get(kind, theme.INK))
        words = [word for word in keywords if word]
        if words and not lite and text:
            self._insert_marked(cursor, text, words, body_format)
        else:
            cursor.insertText(text, body_format)

        self._follow(at_bottom)

    def _insert_marked(self, cursor: QTextCursor, text: str, words: list[str],
                       base: QTextCharFormat) -> None:
        lowered = text.lower()
        spans: list[tuple[int, int]] = []
        for word in sorted(set(words), key=len, reverse=True):
            needle = word.lower()
            start = 0
            while needle:
                index = lowered.find(needle, start)
                if index < 0:
                    break
                spans.append((index, index + len(needle)))
                start = index + len(needle)
        if not spans:
            cursor.insertText(text, base)
            return
        spans.sort()
        merged: list[tuple[int, int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        position = 0
        for start, end in merged:
            if start > position:
                cursor.insertText(text[position:start], base)
            cursor.insertText(text[start:end], self._mark_format)
            position = end
        if position < len(text):
            cursor.insertText(text[position:], base)

    # ---------------------------------------------------------------- pending
    def set_pending(self, text: str) -> None:
        at_bottom = self._at_bottom()
        cursor = QTextCursor(self.document())
        if not self._pending:
            cursor.movePosition(QTextCursor.End)
            if self.document().blockCount() > 1 or self.document().characterCount() > 1:
                cursor.insertBlock()
            self._pending = True
        else:
            cursor.movePosition(QTextCursor.End)
            cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
        cursor.insertText(text, self._pending_format)
        self._follow(at_bottom)

    def clear_pending(self) -> None:
        if self._pending:
            self._clear_pending_internal()

    def _clear_pending_internal(self) -> None:
        if not self._pending:
            return
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.End)
        cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        self._pending = False

    # ------------------------------------------------------------------ misc
    def clear_all(self) -> None:
        self.clear()
        self._pending = False
        self.trimmed = 0

    def scroll_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def plain_text(self) -> str:
        return self.toPlainText()

    def note_trimmed(self, count: int) -> None:
        self.trimmed = count

    def keyPressEvent(self, event) -> None:
        # Ctrl+C already copies; block edits so the log stays a faithful view.
        if event.matches(event.StandardKey.Copy):
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key_Backspace, Qt.Key_Delete) or event.text().isprintable():
            event.ignore()
            return
        super().keyPressEvent(event)


class HoverLogView(LogView):
    """Reserved for per-line hover tooltips (copy-as-hex etc.)."""

    hint = Qt.PointingHandCursor
