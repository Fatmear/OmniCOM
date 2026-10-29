"""Received/sent log view.

QPlainTextEdit is used on purpose: block-count capping and appending thousands of
lines per second is cheap there, whereas a per-row widget model would stall the
GUI thread at high baud rates.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QTextCharFormat,
    QTextCursor,
    QTextOption,
)
from PySide6.QtWidgets import QApplication, QMenu, QPlainTextEdit, QWidget

from . import theme

# ---------------------------------------------------------------- buffering
# The log is capped by *size*, not by a line count: 100 MB by default, chosen by
# the user in Preferences, never more than 500 MB (R-081).
#
# QPlainTextEdit only knows a block (line) limit, so the byte budget is converted
# with the measured average line cost: ``chars / blocks + LINE_OVERHEAD``.  A
# document block carries more than its characters, hence the overhead term.
DEFAULT_BUFFER_MB = 100
MIN_BUFFER_MB = 4
MAX_BUFFER_MB = 500
#: Guard for logs made of *very* short lines: 400 k blocks appended in ~5 ms, so
#: 1 M is ~2.5x that and still responsive.  It is enforced by our own trimmer.
MAX_BLOCKS = 1_000_000
#: Qt's own block limit is only a safety net.  Its shrink path costs ~2 s for a
#: large document (measured), so it must never be the thing that trims (L-104).
QT_BLOCK_CEILING = 2 * MAX_BLOCKS
#: Trim down to this share of the budget, so trimming is rare instead of every
#: append.  85 % of 100 MB is 15 MB of headroom.
TRIM_TO = 0.85

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
    #: Emitted with the new pixel size after a Ctrl+wheel zoom, so the window can
    #: persist it (R-096).
    fontZoomed = Signal(int)

    def __init__(self, parent: QWidget | None = None, show_dir: bool = True,
                 pixel_size: int = 13) -> None:
        super().__init__(parent)
        self.setObjectName("Log")
        self.show_dir = show_dir
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        # A line wider than the view wraps instead of scrolling sideways
        # (R-087): a serial payload has no meaningful "column 200", so a
        # horizontal scrollbar only hides the data.
        #
        # ``WrapAnywhere``, not ``WrapAtWordBoundaryOrAnywhere``: the latter moves
        # a whole word to the next line when it does not fit the remainder of the
        # current one.  A serial payload is one enormous "word", so the timestamp
        # and the direction marker stayed alone on the first line and the data
        # started on the second - the user's "发和后面的内容不在一行" (L-138).
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QPlainTextEdit.NoFrame)
        self._font_px = int(pixel_size)
        self._apply_font()
        self.show_ts = True
        self.show_ctrl = False
        self.autoscroll = True
        self._buffer_bytes = DEFAULT_BUFFER_MB * 1024 * 1024
        self._trim_pending = False
        self.setMaximumBlockCount(QT_BLOCK_CEILING)
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

    # ------------------------------------------------------------------ font
    def _apply_font(self) -> None:
        """Push the current family/size into both the QFont and the stylesheet.

        The font *must* also be declared in the widget stylesheet: the global
        ``*`` selector in app_stylesheet() wins over setFont(), which left the log
        rendering in the proportional UI face (L-032).  That is also why zooming
        has to rewrite the stylesheet, not just setFont().
        """
        family = theme.resolve_mono()
        self.setFont(theme.mono_font(self._font_px))
        self.setStyleSheet(
            f"QPlainTextEdit#Log {{ background: transparent; border: 0;"
            f' font-family: "{family}"; font-size: {self._font_px}px;'
            " padding: 2px 10px 8px 12px;"
            " selection-background-color: rgba(11, 122, 110, 60); }")
        # The document keeps its own default font; without this a line already in
        # the buffer would keep the old size until it scrolled out.
        document = self.document()
        document.setDefaultFont(self.font())

    def font_px(self) -> int:
        return self._font_px

    def apply_font(self, pixel_size: int | None = None, family: str | None = None) -> int:
        """Set the log size and/or family; returns the size actually used."""
        if family is not None:
            theme.set_log_family(family)
        if pixel_size is not None:
            self._font_px = max(theme.LOG_FONT_MIN,
                                min(theme.LOG_FONT_MAX, int(pixel_size)))
        else:
            self._font_px = max(theme.LOG_FONT_MIN,
                                min(theme.LOG_FONT_MAX, self._font_px))
        self._apply_font()
        return self._font_px

    def zoom_font(self, steps: int) -> int:
        """One Ctrl+wheel notch: change the size, keep the content and scroll."""
        used = self.apply_font(self._font_px + int(steps))
        self.fontZoomed.emit(used)
        return used

    def wheelEvent(self, event) -> None:
        """Ctrl+wheel zooms the log only; a plain wheel still scrolls (R-096).

        Zooming just the log matters on a 4K panel: the interface can stay
        comfortable while the payload gets louder, or the opposite.
        """
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.zoom_font(1 if delta > 0 else -1)
                event.accept()
                return
        super().wheelEvent(event)

    # ---------------------------------------------------------------- buffer
    def buffer_mb(self) -> int:
        return round(self._buffer_bytes / 1048576)

    def set_buffer_mb(self, megabytes: int) -> int:
        """Set the log budget in MB; returns the value actually used."""
        value = max(MIN_BUFFER_MB, min(MAX_BUFFER_MB, int(megabytes)))
        self._buffer_bytes = value * 1024 * 1024
        self._apply_buffer_limit(force=True)
        return value

    def buffer_chars(self) -> int:
        """Approximate characters held now (1 char ~ 1 byte of ASCII text)."""
        return max(0, self.document().characterCount() - 1)

    def overflow(self) -> bool:
        """True when the document holds more than the configured budget."""
        return (self.buffer_chars() > self._buffer_bytes
                or self.document().blockCount() > MAX_BLOCKS)

    def trim_oldest(self) -> int:
        """Drop leading text until the document fits the budget again.

        One cursor removal for the whole range: measured 49-123 ms for 2-7 Mchars,
        whereas letting ``setMaximumBlockCount()`` do the same took 2-6 s and froze
        the window (L-104).  Returns the number of lines dropped.
        """
        document = self.document()
        if document.blockCount() <= 1:
            return 0
        chars = self.buffer_chars()
        keep = int(self._buffer_bytes * TRIM_TO)
        surplus = chars - keep
        # A document made of very short lines can hit the block guard instead.
        if document.blockCount() > MAX_BLOCKS:
            surplus = max(surplus, chars - int(chars * TRIM_TO))
        if surplus <= 0:
            return 0
        target = document.findBlock(max(1, min(surplus, chars - 1)))
        if target.position() <= 0:
            return 0
        cursor = QTextCursor(document)
        cursor.setPosition(0)
        cursor.setPosition(target.position(), QTextCursor.KeepAnchor)
        dropped = target.blockNumber()
        cursor.removeSelectedText()
        self.trimmed += dropped
        self._pending = False
        return dropped

    def _apply_buffer_limit(self, force: bool = False) -> None:
        """Trim when the budget is exceeded; batching defers it to end_batch.

        ``force`` trims even while a batch is open (the user just shrank the
        budget and wants to see the effect immediately).
        """
        if not self.overflow():
            return
        if self._batching and not force:
            self._trim_pending = True
            return
        self.trim_oldest()
        self._trim_pending = False

    def set_pixel_size(self, size: int) -> None:
        """Kept for callers that only know about a size; one path with apply_font()."""
        self.apply_font(size)

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
            if self._trim_pending:
                self._apply_buffer_limit(force=True)
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
    def compose_line(self, ts: float, direction: str, text: str) -> str:
        """Exactly the characters :meth:`append_line` would insert.

        The search panel compares against this, so a live hit and a hit found in
        the history are the same string - previously live rows showed the bare
        payload while history rows showed the full line (L-089).
        """
        parts: list[str] = []
        if self.show_ts:
            parts.append(f"[{stamp(ts)}]")
        if self.show_dir:
            parts.append(dir_label(direction))
            if direction in ("RX", "TX"):
                parts.append("◇")
        parts.append(text)
        return "".join(parts)

    def append_line(self, ts: float, direction: str, text: str, kind: str = "rx",
                    keywords: Iterable[str] = (), lite: bool = False) -> int:
        """Append one line and return the document block it now occupies."""
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
        self._apply_buffer_limit()
        return self.document().blockCount() - 1

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
        """Read-only log that still answers the clipboard shortcuts.

        ``StandardKey`` lives on ``QKeySequence``; ``QKeyEvent`` has no such
        attribute.  Asking the event for it raised ``AttributeError`` inside this
        override for *every* key press, which PySide reports to stderr and then
        swallows - the visible symptom was "Ctrl+C does nothing, the context menu
        still copies" (L-122).
        """
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
            return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.selectAll()
            return
        if event.key() in (Qt.Key_Backspace, Qt.Key_Delete) or event.text().isprintable():
            event.ignore()
            return
        super().keyPressEvent(event)

    def copy_selection(self) -> bool:
        """Copy the selected text; False when nothing is selected.

        QPlainTextEdit separates visual lines with U+2029, so a multi-line
        selection pasted into Notepad would turn into one line of odd glyphs;
        it is normalised to real newlines here (the context menu does the same).
        """
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return False
        QApplication.clipboard().setText(
            cursor.selectedText().replace("\u2029", "\n"))
        return True

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
            self.copy_selection()
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
