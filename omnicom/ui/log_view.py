"""Received/sent log view.

QPlainTextEdit is used on purpose: block-count capping and appending thousands of
lines per second is cheap there, whereas a per-row widget model would stall the
GUI thread at high baud rates.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QMenu, QPlainTextEdit, QWidget

from . import theme

MAX_BLOCKS = 20000

# "收←" / "发→" read faster than RX/TX in a long log, and the direction column
# keeps a fixed width so the payload starts at the same x on every line.  The
# labels follow the interface language: English mode must never show Chinese.
_DIR_LABELS = {
    "zh": {"RX": "收←", "TX": "发→"},
    "en": {"RX": "RX←", "TX": "TX→"},
}


def dir_label(direction: str) -> str:
    from ..i18n import language

    table = _DIR_LABELS.get(language(), _DIR_LABELS["zh"])
    return table.get(direction, direction)


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
    saveRequested = Signal()
    findRequested = Signal(str)
    clearRequested = Signal()
    latestRequested = Signal()

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
        self.setFont(theme.mono_font(pixel_size))
        # The font *must* also be declared in the widget stylesheet: the global
        # ``*`` selector in app_stylesheet() wins over setFont(), which left the
        # log rendering in the proportional UI face.
        self.setStyleSheet(
            f"QPlainTextEdit#Log {{ background: transparent; border: 0;"
            f' font-family: "{theme.resolve_mono()}"; font-size: {pixel_size}px;'
            " padding: 2px 10px 8px 12px;"
            " selection-background-color: rgba(11, 122, 110, 60); }")
        self.show_ts = True
        self.show_ctrl = False
        self.autoscroll = True
        self._batching = 0
        self._batch_cursor: QTextCursor | None = None
        self._dirty = False
        self.trimmed = 0
        self._pending = False
        self._ts_format = self._make_format(theme.DIM)
        self._mark_format = self._make_format("#0A3F3A", background="#BFF0E6")
        self._marker_format = self._make_format(theme.DIM)
        self._pending_format = self._make_format("#5A6A85")
        self._flash_format = self._make_format(
            theme.INK, background="#FFE9A8", bold=True)

        # The floating "back to latest" pill listens to these.
        self.viewport().installEventFilter(self)

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

    def set_pixel_size(self, size: int) -> None:
        self.setFont(theme.mono_font(size))
        self.setStyleSheet(
            f"QPlainTextEdit#Log {{ background: transparent; border: 0;"
            f' font-family: "{theme.resolve_mono()}"; font-size: {size}px;'
            " padding: 2px 10px 8px 12px;"
            " selection-background-color: rgba(11, 122, 110, 60); }")

    def _at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    # ----------------------------------------------------------------- batch
    def begin_batch(self) -> None:
        """Coalesce the geometry work of many appends into one pass."""
        if self._batching == 0:
            self._batch_cursor = QTextCursor(self.document())
            self._batch_cursor.beginEditBlock()
        self._batching += 1

    def end_batch(self) -> None:
        if self._batching == 0:
            return
        self._batching -= 1
        if self._batching == 0:
            self._batch_cursor.endEditBlock()
            self._batch_cursor = None
            if self._dirty:
                self._dirty = False
                self._follow()

    def _follow(self, _was_at_bottom: bool = True) -> None:
        """自动滚动打开时**无条件**跟随最新一行.

        Earlier this only followed when the view happened to already be at the
        bottom, so scrolling up once silently disabled auto-scroll for good -
        which is exactly what the user reported.  When the switch is off the
        view stays put and the floating pill offers a way back down.
        """
        if self._batching:
            self._dirty = True
            return
        if not self.autoscroll:
            return
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------------ lines
    def append_line(self, ts: float, direction: str, text: str, kind: str = "rx",
                    keywords: Iterable[str] = (), lite: bool = False) -> None:
        self._clear_pending_internal()

        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.End)
        if self.document().blockCount() > 1 or self.document().characterCount() > 1:
            cursor.insertBlock()

        if self.show_ts:
            cursor.insertText(f"[{stamp(ts)}]", self._ts_format)

        if self.show_dir:
            label = dir_label(direction)
            cursor.insertText(label, self._make_format(_DIR_COLORS.get(direction, theme.DIM)))
            if direction in ("RX", "TX"):
                cursor.insertText("◇", self._marker_format)

        body_format = self._make_format(_TEXT_COLORS.get(kind, theme.INK))
        words = [word for word in keywords if word]
        if words and not lite and text:
            self._insert_marked(cursor, text, words, body_format)
        else:
            cursor.insertText(text, body_format)

        self._follow()

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
        self._follow()

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

    # ---------------------------------------------------------------- locate
    def line_texts(self) -> list[str]:
        """Every rendered line, in document order (block index == list index)."""
        return self.toPlainText().split("\n")

    def goto_block(self, block: int, flash: bool = True) -> None:
        """Scroll to a block, put the cursor on it and flash it once."""
        document = self.document()
        if block < 0 or block >= document.blockCount():
            return
        cursor = QTextCursor(document.findBlockByNumber(block))
        cursor.select(QTextCursor.LineUnderCursor)
        self.setTextCursor(cursor)
        self.centerCursor()
        if not flash:
            return
        char_format = self._flash_format
        held = QTextCursor(document.findBlockByNumber(block))
        held.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        QTimer.singleShot(0, lambda: self._apply_flash(held, char_format))

    def _apply_flash(self, cursor: QTextCursor, char_format: QTextCharFormat) -> None:
        if cursor.hasSelection():
            cursor.mergeCharFormat(char_format)

    # ------------------------------------------------------------------ misc
    def direction_labels(self) -> dict[str, str]:
        """Direction markers for the *current* interface language."""
        from ..i18n import language

        return dict(_DIR_LABELS.get(language(), _DIR_LABELS["zh"]))

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
        if event.matches(event.StandardKey.SelectAll):
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key_Backspace, Qt.Key_Delete) or event.text().isprintable():
            event.ignore()
            return
        super().keyPressEvent(event)

    # --------------------------------------------------------------- menu
    def contextMenuEvent(self, event) -> None:
        from ..i18n import tr
        from . import theme

        menu = QMenu(self)
        menu.setStyleSheet(theme.app_stylesheet())

        cursor = self.textCursor()
        copy = menu.addAction(tr("log.menu.copy"))
        copy.setShortcut("Ctrl+C")
        copy.setEnabled(cursor.hasSelection())
        copy_all = menu.addAction(tr("log.menu.copyall"))
        select_all = menu.addAction(tr("log.menu.selectall"))
        menu.addSeparator()
        clear = menu.addAction(tr("log.menu.clear"))
        save = menu.addAction(tr("log.menu.save"))
        menu.addSeparator()
        find = menu.addAction(tr("log.menu.find"))
        latest = menu.addAction(tr("log.menu.gotolatest"))

        picked = menu.exec(event.globalPos())
        if picked is copy:
            super().copy()
        elif picked is copy_all:
            QApplication.clipboard().setText(self.plain_text())
        elif picked is select_all:
            self.selectAll()
        elif picked is clear:
            self.clearRequested.emit()
        elif picked is save:
            self.saveRequested.emit()
        elif picked is find:
            self.findRequested.emit(cursor.selectedText().replace("\u2029", "\n"))
        elif picked is latest:
            self.latestRequested.emit()


class HoverLogView(LogView):
    """Reserved for per-line hover tooltips (copy-as-hex etc.)."""

    hint = Qt.PointingHandCursor
