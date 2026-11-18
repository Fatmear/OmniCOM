"""Terminal page: a real VT100-screen console for a serial device.

The log page is a *monitor* (it prints lines and formats them).  This page is a
**terminal**: keystrokes leave immediately, and the device's output is parsed
into a character grid (``omnicom/ui/vt100.py``) instead of being appended to a
document.

That distinction is the whole design.  An earlier version emulated a *line*
console - ``\\r`` returned to column 0, ``\\n`` opened a line, and **every CSI
cursor movement was discarded** - which is fine for ``login:`` but diverges from
the device as soon as the far end moves the cursor: a shell line editor echoing
``ESC[D`` left the local column pointing at the wrong cell, and the next
character overwrote (deleted) whatever was there.  That was the reported
"左右键移动居然会删除字符".

So the screen owns the state - cursor, cells, styles, scroll region, scrollback -
and this widget only paints it.  Input is forwarded to the device, and the
device's own echo drives the screen, exactly like a hardware terminal.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from . import theme
from .vt100 import ANSI_COLORS, DEFAULT_FG, Screen
from .widgets import Combo, GlyphButton

SELECTION_BG = "#1F4E5A"


class TerminalView(QWidget):
    """A VT100 screen renderer: forwards keys, paints the device's grid.

    Read-only in the sense that local typing never edits the screen directly -
    the device echoes it back, exactly like a hardware terminal.  That is what
    makes a line editor behave: press Left, the board sends ``ESC[D``, the screen
    moves its cursor, and the next echo lands in the right cell (R-207).
    """

    keyBytes = Signal(bytes)

    #: Pixels of padding inside the card, so text does not touch the border.
    PAD_X = 10
    PAD_Y = 6
    #: Extra characters xterm counts as part of a word on double click.
    WORD_CHARS = "_-./\\:@%+=~$"

    def __init__(self, parent=None, enter: bytes = b"\r") -> None:
        super().__init__(parent)
        self.setObjectName("Terminal")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget#Terminal {{ background: {theme.TERM_BG}; border: 0; }}")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        theme.lock_ui_font(self)

        self.enter = enter
        self.screen = Screen(rows=24, cols=80, scrollback=5000)
        self.screen.scrollback_limit = 5000
        self._font = theme.mono_font(theme.log_px())
        self._metrics = QFontMetricsF(self._font)
        self._cell_w = max(1.0, self._metrics.horizontalAdvance("M"))
        self._cell_h = max(1.0, self._metrics.lineSpacing())
        #: Caches for the paint path - a frame touches ~2 000 cells (L-271).
        self._fonts: dict[tuple[bool, bool], QFont] = {}
        self._colours: dict[str, QColor] = {}
        self._sel_anchor: tuple[int, int] | None = None
        self._sel_active: tuple[int, int] | None = None
        #: "char" (drag), "word" (double click) or "line" (triple click).
        self._sel_mode = "char"
        #: Cell the user double-clicked; dragging grows from here.
        self._word_origin: tuple[int, int] | None = None
        #: Where the last plain click landed, so Shift+click can extend from it
        #: even after the click cleared the selection (xterm behaviour).
        self._caret_cell: tuple[int, int] | None = None
        #: Triple click has no Qt event, so consecutive presses are counted.
        self._last_click: tuple[float, float, float] | None = None
        self._click_count = 0
        #: Drag-autoscroll while a selection runs past an edge.
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(50)
        self._autoscroll.timeout.connect(self._autoscroll_step)
        self._autoscroll_dir = 0
        #: Column - 0 of the painted region; the grid is sized to the widget and
        #: the scrollback is shown above row 0 (like a real terminal's history).
        self._scroll_off = 0

        self._caret_on = True
        self._caret_focused = False
        self._caret_timer = QTimer(self)
        self._caret_timer.setInterval(530)
        self._caret_timer.timeout.connect(self._blink_caret)
        self._caret_timer.start()

        self._cursor = Qt.IBeamCursor

    # ------------------------------------------------------------------ font
    def apply_font(self) -> None:
        """Follow the log font preference (R-096)."""
        self._font = theme.mono_font(theme.log_px())
        self._metrics = QFontMetricsF(self._font)
        self._cell_w = max(1.0, self._metrics.horizontalAdvance("M"))
        self._cell_h = max(1.0, self._metrics.lineSpacing())
        self._fonts.clear()
        self._resize_grid()
        self.update()

    def _resize_grid(self) -> None:
        """Match the grid to the available area, keeping the cursor on screen."""
        cols = max(20, int((self.width() - 2 * self.PAD_X) // self._cell_w))
        rows = max(4, int((self.height() - 2 * self.PAD_Y) // self._cell_h))
        self.screen.resize(rows, cols)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_grid()

    # ---------------------------------------------------------------- cursor
    def _blink_caret(self) -> None:
        if not self.isVisible() or not self.screen.cursor_visible:
            return
        self._caret_on = not self._caret_on
        self.update()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._caret_focused = True
        self._caret_on = True
        self.update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._caret_focused = False
        self.update()

    def _caret_rect(self) -> QRect:
        """Pixel rect of the console cursor (used by the bitmap selftest)."""
        row, col = self.screen.cursor_row, self.screen.cursor_col
        x = self.PAD_X + col * self._cell_w
        y = self.PAD_Y + row * self._cell_h
        return QRect(int(x), int(y), max(1, int(self._cell_w)),
                     max(1, int(self._cell_h)))

    # ------------------------------------------------------------------ output
    def attach_screen(self, screen: Screen) -> None:
        """Render this ``Screen`` instead of a private grid (R-212).

        There used to be two: `page.mirror` (fed from `_on_rx` for the CLI) and
        `view.screen` (fed again from `_flush_rx` only while visible).  Every byte
        was therefore parsed twice, and the two grids drifted apart as soon as one
        of them missed a chunk - which is exactly what garbled the echo when the
        user pressed Left or Backspace.
        """
        self.screen = screen
        self._resize_grid()
        self.refresh()

    def refresh(self) -> None:
        """Repaint from the shared screen (no parsing)."""
        self._scroll_off = min(self._scroll_off, len(self.screen.scrollback))
        self.update()

    def feed(self, data: bytes) -> None:
        """Consume device bytes when nothing else owns the screen.

        The window path goes through ``TerminalPage.absorb()`` so a byte is parsed
        exactly once (R-212); this direct entry point is for tests and for a view
        used on its own.
        """
        if not data:
            return
        self.screen.feed(data)
        self._scroll_off = 0
        replies = self.screen.take_replies()
        if replies:
            self.keyBytes.emit(replies)
        self.update()

    def reset(self) -> None:
        """Blank the shared screen and drop the view's per-screen bookkeeping.

        The screen object is owned by ``TerminalPage`` (R-212), so this blanks it
        in place instead of allocating a new one - otherwise the window would keep
        rendering the old grid while the CLI read the new one.
        """
        self.screen.erase_in_display(2)
        self.screen.scrollback.clear()
        self.screen.cursor_row = 0
        self.screen.cursor_col = 0
        self.screen.wrap_pending = False
        self.screen.take_replies()
        self._sel_anchor = None
        self._sel_active = None
        # Selection bookkeeping has to go too, or a stale mode/anchor makes the
        # first click after a reset behave like a continuation (R-210).
        self._sel_mode = "char"
        self._word_origin = None
        self._caret_cell = None
        self._click_count = 0
        self._last_click = None
        self._autoscroll_dir = 0
        self._autoscroll.stop()
        self._scroll_off = 0
        self.update()

    def clear(self) -> None:
        """Context-menu 清屏: blank the screen and put the cursor back at (0,0).

        ``erase_in_display(2)`` only blanks the cells; the cursor kept whatever
        column it had, so the next output appeared mid-line - the reported
        「右键清空后光标不是从头开始」(R-213).  ``ESC[2J`` alone does not home the
        cursor in a real terminal either; only ``ESC[2J ESC[H`` does.
        """
        self.screen.erase_in_display(2)
        self.screen.cursor_row = 0
        self.screen.cursor_col = 0
        self.screen.wrap_pending = False
        self._scroll_off = 0
        self._sel_anchor = None
        self._sel_active = None
        self.update()

    def toPlainText(self) -> str:
        """Whole buffer as text (scrollback + screen), for search and tests."""
        return self.screen.text(keep_scrollback=True)

    def plain_text(self) -> str:
        return self.toPlainText()

    # ------------------------------------------------------------------ input
    def set_enter(self, enter: bytes) -> None:
        self.enter = enter

    def _arrow(self, final: bytes) -> bytes:
        # Application cursor keys (DECCKM) make the arrows send SS3 instead of
        # CSI - vim and less depend on it.
        prefix = b"\x1bO" if self.screen.app_cursor_keys else b"\x1b["
        return prefix + final

    def keyPressEvent(self, event) -> None:
        key = event.key()
        modifiers = event.modifiers()
        control = bool(modifiers & Qt.ControlModifier)
        shift = bool(modifiers & Qt.ShiftModifier)
        alt = bool(modifiers & Qt.AltModifier)

        # Ctrl+Shift+C / V are copy/paste; plain Ctrl+C is the interrupt byte.
        if control and shift:
            if key == Qt.Key_C:
                self.copy_selection()
                event.accept()
                return
            if key == Qt.Key_V:
                self.paste_clipboard()
                event.accept()
                return
        if key in self.IGNORED_KEYS:
            return

        # Shift+PageUp/PageDown scroll the history, exactly like xterm; the
        # plain keys keep going to the device (less/more depend on them).
        if shift and key in (Qt.Key_PageUp, Qt.Key_PageDown):
            self._scroll_history(self.screen.rows - 1
                                 if key == Qt.Key_PageUp
                                 else -(self.screen.rows - 1))
            self.update()
            event.accept()
            return

        sequences = {
            Qt.Key_Up: self._arrow(b"A"),
            Qt.Key_Down: self._arrow(b"B"),
            Qt.Key_Right: self._arrow(b"C"),
            Qt.Key_Left: self._arrow(b"D"),
            Qt.Key_Home: self._arrow(b"H"),
            Qt.Key_End: self._arrow(b"F"),
            Qt.Key_PageUp: b"\x1b[5~",
            Qt.Key_PageDown: b"\x1b[6~",
            Qt.Key_Delete: b"\x1b[3~",
            Qt.Key_Insert: b"\x1b[2~",
            Qt.Key_F1: b"\x1bOP", Qt.Key_F2: b"\x1bOQ",
            Qt.Key_F3: b"\x1bOR", Qt.Key_F4: b"\x1bOS",
            Qt.Key_F5: b"\x1b[15~", Qt.Key_F6: b"\x1b[17~",
            Qt.Key_F7: b"\x1b[18~", Qt.Key_F8: b"\x1b[19~",
            Qt.Key_F9: b"\x1b[20~", Qt.Key_F10: b"\x1b[21~",
            Qt.Key_F11: b"\x1b[23~", Qt.Key_F12: b"\x1b[24~",
        }
        if key in sequences and not alt:
            self.keyBytes.emit(sequences[key])
            event.accept()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            # Shift+Enter is a literal newline in every Linux terminal's shell.
            if shift:
                self.keyBytes.emit(b"\r")
            else:
                self.keyBytes.emit(self.enter)
            event.accept()
            return
        if key == Qt.Key_Backspace:
            # Ctrl+Backspace deletes a whole word (readline/xterm convention).
            self.keyBytes.emit(b"\x17" if control else b"\x7f")
            event.accept()
            return
        if key == Qt.Key_Tab:
            self.keyBytes.emit(b"\t")
            event.accept()
            return
        if key == Qt.Key_Backtab:
            self.keyBytes.emit(b"\x1b[Z")
            event.accept()
            return
        if key == Qt.Key_Escape:
            self.keyBytes.emit(b"\x1b")
            event.accept()
            return
        if control and Qt.Key_A <= key <= Qt.Key_Z:
            self.keyBytes.emit(bytes((key - Qt.Key_A + 1,)))
            event.accept()
            return
        if control and key == Qt.Key_Space:
            self.keyBytes.emit(b"\x00")     # Ctrl+Space = NUL, as xterm
            event.accept()
            return
        if alt and event.text():
            # Alt+<key> is ESC <key> on a terminal.
            self.keyBytes.emit(b"\x1b" + event.text().encode("utf-8"))
            event.accept()
            return
        if key == Qt.Key_Insert and shift:
            self.paste_clipboard()
            event.accept()
            return

        text = event.text()
        if text and text.isprintable():
            self.keyBytes.emit(text.encode("utf-8"))
            event.accept()
            return

    IGNORED_KEYS = (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta,
                    Qt.Key_CapsLock, Qt.Key_NumLock)

    def focusNextPrevChild(self, next_child: bool) -> bool:
        """Keep Tab inside the terminal instead of tabbing to the next widget.

        ``QWidget::focusNextPrevChild`` runs **before** ``keyPressEvent``: Qt
        treats Tab as a focus gesture, so a console that only handled
        ``Qt.Key_Tab`` there never saw it (L-250).
        """
        return False

    def paste_clipboard(self) -> None:
        """Send the clipboard to the device as if it were typed."""
        text = QApplication.clipboard().text()
        if not text:
            return
        payload = text.replace("\r\n", "\r").replace("\n", "\r")
        if self.screen.bracketed_paste:
            payload = "\x1b[200~" + payload + "\x1b[201~"
        self.keyBytes.emit(payload.encode("utf-8"))

    # ------------------------------------------------------------- selection
    def _history_rows(self) -> list[list]:
        """The scrollback rows currently painted above the live screen."""
        if not self._scroll_off:
            return []
        return self.screen.scrollback[-self._scroll_off:]

    def _visual_rows(self) -> list[list]:
        """Every painted row, history first, then the live grid.

        Selection is stored in **visual** coordinates - the row the user's eye
        points at - not grid coordinates, so a selection can run from the
        scrolled-back history into the live screen and still be copied (R-207).
        """
        rows = list(self._history_rows())
        rows.extend(self.screen._grid)
        return rows

    def _cell_at(self, point) -> tuple[int, int]:
        col = int((point.x() - self.PAD_X) // self._cell_w)
        row = int((point.y() - self.PAD_Y) // self._cell_h)
        last_row = max(0, len(self._visual_rows()) - 1)
        return (max(0, min(last_row, row)),
                max(0, min(self.screen.cols - 1, col)))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            cell = self._cell_at(event.position())
            clicks = self._count_click(event.position())
            if clicks >= 3:
                self._select_line(cell[0])
            elif clicks == 2:
                # Qt *does* send this press before ``mouseDoubleClickEvent``, so
                # the word selection has to be made here or the release would
                # collapse it back to a single cell (L-274).
                self._select_word(cell, drag=False)
            elif event.modifiers() & Qt.ShiftModifier and self._sel_anchor:
                # Shift+click extends the existing selection (every Linux
                # terminal does this, and it is the only way to adjust a
                # double-clicked word without losing it).
                self._sel_active = cell
                self._sel_mode = "extend"
            elif event.modifiers() & Qt.ShiftModifier and self._caret_cell:
                # The selection was cleared by the previous plain click, but a
                # terminal still remembers where the caret last landed: shift+
                # click extends *from there* (that is what xterm does).  Without
                # this fallback the gesture collapsed into a fresh 1-char
                # selection and the release then wiped it (L-274).
                self._sel_anchor = self._caret_cell
                self._sel_active = cell
                self._sel_mode = "extend"
            else:
                self._sel_anchor = cell
                self._sel_active = cell
                self._sel_mode = "char"
                self._caret_cell = cell
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def _count_click(self, point) -> int:
        """Consecutive clicks at (nearly) the same spot within the system delay."""
        now = time.monotonic()
        same = (self._last_click is not None
                and now - self._last_click[0]
                <= QApplication.doubleClickInterval() / 1000
                and abs(point.x() - self._last_click[1]) <= 4
                and abs(point.y() - self._last_click[2]) <= 4)
        self._click_count = (self._click_count + 1) if same else 1
        self._last_click = (now, point.x(), point.y())
        return self._click_count

    def _select_line(self, row: int) -> None:
        """Whole line - what a triple click does in xterm / MobaXterm."""
        self._sel_anchor = (row, 0)
        self._sel_active = (row, self.screen.cols - 1)
        self._sel_mode = "line"

    def _select_word(self, cell: tuple[int, int], drag: bool = True) -> None:
        """Select the word under ``cell``, growing with the pointer if ``drag``."""
        self._word_origin = cell
        self._sel_anchor = cell
        self._sel_active = cell
        self._sel_mode = "word"
        self._extend_by_word()
        del drag

    def mouseMoveEvent(self, event) -> None:
        if self._sel_anchor is not None and event.buttons() & Qt.LeftButton:
            cell = self._cell_at(event.position())
            if self._sel_mode == "word":
                self._sel_active = cell
                self._extend_by_word()
            else:
                self._sel_active = cell
            self._maybe_autoscroll(event.position())
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._sel_anchor is not None:
            if self._sel_mode == "word":
                # Re-derive from the word instead of snapping to the release
                # cell: closing the drag on a word must keep the whole word
                # ("1024", not "102") - the release used to clobber it (L-274).
                self._sel_active = self._cell_at(event.position())
                self._extend_by_word()
            elif self._sel_mode in ("char", "extend"):
                self._sel_active = self._cell_at(event.position())
                if self._sel_active == self._sel_anchor and \
                        self._sel_mode == "char":
                    # A plain click is a caret gesture, not an empty selection.
                    self._sel_anchor = self._sel_active = None
            self._autoscroll.stop()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        """Double click selects the **word** under the cursor, like xterm.

        It used to select the whole row, which is what a *triple* click does in
        MobaXterm/PuTTY - and it made every double click look like "只能复制
        整行" (R-210).
        """
        if event.button() != Qt.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        self._select_word(self._cell_at(event.position()))
        self.update()
        event.accept()

    def _word_spans(self, cells) -> list[tuple[int, int]]:
        """Word runs in one row: ``[start, end]`` column pairs, inclusive."""
        spans = []
        start = None
        for col, cell in enumerate(cells):
            word = bool(cell.char) and not cell.continuation and \
                (cell.char.isalnum() or cell.char in self.WORD_CHARS)
            if word and start is None:
                start = col
            elif not word and start is not None:
                spans.append((start, col - 1))
                start = None
        if start is not None:
            spans.append((start, len(cells) - 1))
        return spans

    def _extend_by_word(self) -> None:
        """Grow the selection to the word boundaries around the active cell.

        ``_word_origin`` is the cell the user double-clicked.  Without it the
        anchor would be rewritten to the word start on every mouse-move and
        dragging across words could only ever grow the first one (R-210).
        """
        origin = self._word_origin or self._sel_active
        if not self._sel_active:
            return
        rows = self._visual_rows()
        row = max(0, min(len(rows) - 1, self._sel_active[0]))
        cells = rows[row]
        col = max(0, min(len(cells) - 1, self._sel_active[1]))
        spans = self._word_spans(cells)
        span = None
        for start, end in spans:
            if start <= col <= end:
                span = (start, end)
                break
        if span is None:
            # Separator: select just that character so the user still gets
            # feedback instead of a silently empty selection.
            span = (col, col)
        if row == origin[0] and origin[1] < span[0]:
            span = (min(span[0], origin[1]), span[1])
        self._sel_anchor = (row, span[0])
        self._sel_active = (row, span[1])

    def _maybe_autoscroll(self, point) -> None:
        """Dragging past an edge scrolls the history, like a real terminal."""
        if point.y() < self.PAD_Y:
            self._autoscroll_dir = 1
        elif point.y() > self.height() - self.PAD_Y:
            self._autoscroll_dir = -1
        else:
            self._autoscroll_dir = 0
        if self._autoscroll_dir and not self._autoscroll.isActive():
            self._autoscroll.start()
        elif not self._autoscroll_dir:
            self._autoscroll.stop()

    def _autoscroll_step(self) -> None:
        if not self._autoscroll_dir:
            self._autoscroll.stop()
            return
        before = self._scroll_off
        self._scroll_history(self._autoscroll_dir)
        if self._scroll_off == before:
            self._autoscroll.stop()
            return
        if self._sel_anchor and self._sel_active:
            row = self._sel_active[0] - self._autoscroll_dir
            rows = self._visual_rows()
            row = max(0, min(max(0, len(rows) - 1), row))
            self._sel_active = (row, self._sel_active[1])
        self.update()

    def _scroll_history(self, lines: int) -> None:
        """Positive scrolls back into the history, negative returns to live."""
        limit = len(self.screen.scrollback)
        self._scroll_off = max(0, min(limit, self._scroll_off + lines))

    def selectAll(self) -> None:
        rows = self._visual_rows()
        self._sel_anchor = (0, 0)
        self._sel_active = (max(0, len(rows) - 1), self.screen.cols - 1)
        self._sel_mode = "line"
        self.update()

    def has_selection(self) -> bool:
        return self._sel_anchor is not None and self._sel_active is not None

    def selected_text(self) -> str:
        """Text under the selection, joining visual rows with newlines.

        Uses ``_row_selection_cols()`` - the very same span the painter fills in -
        so the clipboard can never disagree with the highlight (R-210).  Leading
        blanks are kept (selecting from mid-line must copy from that character);
        trailing blanks are not, because a terminal line is padded to the full
        width and copying that padding is what xterm/PuTTY strip as well.
        """
        selection = self._selection_range()
        if selection is None:
            return ""
        rows = self._visual_rows()
        (r1, _c1), (r2, _c2) = selection
        out = []
        for row in range(r1, min(r2, len(rows) - 1) + 1):
            cells = rows[row]
            span = self._row_selection_cols(row, selection)
            if span is None:
                continue
            first, last = span
            if last is None:
                last = len(cells) - 1
            first, last = self._snap_cols(cells, first, last)
            out.append("".join(cell.char for cell in cells[first:last + 1]
                               if not cell.continuation).rstrip())
        while out and not out[-1]:
            out.pop()
        return "\n".join(out)

    def copy_selection(self) -> bool:
        if not self.has_selection():
            return False
        text = self.selected_text()
        if not text:
            return False
        QApplication.clipboard().setText(text)
        return True

    def wheelEvent(self, event) -> None:
        """Scroll the history (mouse wheel), like a real terminal's scrollback."""
        lines = int(event.angleDelta().y() / 120)
        if not lines:
            return
        self._scroll_history(lines)
        self.update()
        event.accept()

    # ------------------------------------------------------------------- menu
    def contextMenuEvent(self, event) -> None:
        """Copy / paste / select-all / clear, as a terminal is expected to have."""
        menu = QMenu(self)
        menu.setStyleSheet(theme.menu_stylesheet())
        copy = menu.addAction(tr("term.menu.copy"))
        copy.setShortcut("Ctrl+Shift+C")
        copy.setEnabled(self.has_selection())
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
            self.clear()

    # ------------------------------------------------------------------ paint
    def _colour(self, spec: str) -> QColor:
        """Resolve a style colour (``#RRGGBB`` or ``ansi256:n``) to a QColor."""
        cached = self._colours.get(spec)
        if cached is not None:
            return cached
        colour = self._resolve_colour(spec)
        self._colours[spec] = colour
        return colour

    def _resolve_colour(self, spec: str) -> QColor:
        """Uncached colour math: ``ansi256:n`` palette, then ``QColor`` names."""
        if spec.startswith("ansi256:"):
            try:
                code = int(spec.split(":", 1)[1])
            except ValueError:
                return QColor(DEFAULT_FG)
            if code < 16:
                return QColor(ANSI_COLORS[code])
            if code < 232:
                steps = (0, 95, 135, 175, 215, 255)
                code -= 16
                red, green, blue = (steps[code // 36],
                                    steps[(code // 6) % 6], steps[code % 6])
                return QColor(red, green, blue)
            level = 8 + (code - 232) * 10
            return QColor(level, level, level)
        colour = QColor(spec)
        return colour if colour.isValid() else QColor(DEFAULT_FG)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, False)
            painter.fillRect(self.rect(), QColor(theme.TERM_BG))
            painter.setFont(self._font)
            selection = self._selection_range()

            rows = self._visual_rows()
            visible = max(1, int((self.height() - 2 * self.PAD_Y) // self._cell_h))
            for visual_row, cells in enumerate(rows[:visible]):
                self._paint_row(painter, visual_row, cells, selection)

            self._paint_caret(painter)
            self._paint_scrollbar(painter)
        finally:
            painter.end()

    def _font_for(self, style) -> QFont:
        """Cached bold/italic variants: building a QFont per cell is the hot spot.

        A whole screen is ~2 000 cells, so allocating and configuring a QFont for
        every painted character dominated the frame time (L-271).
        """
        key = (bool(style.bold), bool(style.italic))
        font = self._fonts.get(key)
        if font is None:
            font = QFont(self._font)
            if style.italic:
                font.setItalic(True)
            if style.bold:
                font.setWeight(QFont.DemiBold)
            self._fonts[key] = font
        return font

    def _paint_row(self, painter, visual_row, cells, selection) -> None:
        y = self.PAD_Y + visual_row * self._cell_h
        if y > self.height():
            return
        # The selection highlight must follow the *columns* of this row, not the
        # whole row: filling the full width made a 4-character drag look like the
        # entire line was selected, which is exactly what the user reported as
        # "只能复制整行" (R-210).
        sel_cols = self._row_selection_cols(visual_row, selection)
        if sel_cols is not None:
            start_col, end_col = sel_cols
            if end_col is None:
                end_col = len(cells) - 1
            start_col, end_col = self._snap_cols(cells, start_col, end_col)
            painter.fillRect(
                QRectF(self.PAD_X + start_col * self._cell_w, y,
                       max(1, end_col - start_col + 1) * self._cell_w,
                       self._cell_h),
                QColor(SELECTION_BG))
        col = 0
        while col < len(cells):
            cell = cells[col]
            span = max(1, cell.width)
            x = self.PAD_X + col * self._cell_w
            style = cell.style
            fg = self._colour(style.fg)
            bg = self._colour(style.bg)
            if style.inverse:
                fg, bg = bg, fg
            rect = QRectF(x, y, self._cell_w * span, self._cell_h)
            if bg.name().lower() != theme.TERM_BG.lower():
                painter.fillRect(rect, bg)
            if cell.char and cell.char != " " and not cell.continuation:
                painter.setFont(self._font_for(style))
                painter.setPen(QPen(fg))
                flags = int(Qt.AlignLeft | Qt.AlignVCenter | Qt.TextDontClip)
                painter.drawText(rect, flags, cell.char)
                if style.underline or style.strike:
                    self._paint_decoration(painter, rect, style, fg)
                painter.setFont(self._font)
            col += span

    @staticmethod
    def _paint_decoration(painter, rect, style, fg) -> None:
        pen = QPen(fg, 1)
        painter.setPen(pen)
        if style.underline:
            y = rect.bottom() - 1
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
        if style.strike:
            y = rect.center().y()
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))

    def _selection_range(self):
        if not self.has_selection():
            return None
        anchor, active = self._sel_anchor, self._sel_active
        return (min(anchor, active), max(anchor, active))

    @staticmethod
    def _row_selection_cols(visual_row: int, selection):
        """``(first_col, last_col)`` of one painted row, or ``None``.

        This is the counterpart of ``selected_text()``: both derive the span from
        the same rule (first row starts at ``c1``, last row ends at ``c2``,
        middle rows are full width) so what is highlighted and what is copied can
        never disagree (R-210).  ``last_col`` is ``None`` for the middle rows -
        the caller knows how wide the row is.
        """
        if selection is None:
            return None
        (r1, c1), (r2, c2) = selection
        if visual_row < r1 or visual_row > r2:
            return None
        first = c1 if visual_row == r1 else 0
        last = c2 if visual_row == r2 else None
        return first, last

    @staticmethod
    def _snap_cols(cells, first: int, last: int) -> tuple[int, int]:
        """Widen a span so it never cuts a wide (CJK) glyph in half.

        Half a glyph cannot be highlighted sensibly, and the copied text would
        silently drop the character (R-210).
        """
        cols = len(cells)
        first = max(0, min(first, cols - 1))
        last = max(first, min(last, cols - 1))
        while first > 0 and cells[first].continuation:
            first -= 1
        while last + 1 < cols and cells[last + 1].continuation:
            last += 1
        return first, last

    @staticmethod
    def _row_in_selection(visual_row: int, selection) -> bool:
        """Is any part of this painted row inside the selection?"""
        (r1, _c1), (r2, _c2) = selection
        return r1 <= visual_row <= r2

    def _paint_caret(self, painter) -> None:
        if not self.screen.cursor_visible:
            return
        if self._scroll_off:
            return                     # browsing history: the live cursor is off
        rect = self._caret_rect()
        column = QRectF(rect.x(), rect.y() + 1, max(2.0, self._cell_w),
                        max(2.0, self._cell_h - 2))
        colour = QColor(theme.TERM_INK)
        if self._caret_focused and self._caret_on:
            # A solid block, like MobaXterm: the glyph under it is redrawn in the
            # surface colour so it stays readable instead of being covered.
            painter.setPen(Qt.NoPen)
            painter.fillRect(column, colour)
            cell = self.screen.cell(self.screen.cursor_row,
                                    self.screen.cursor_col)
            if cell.char and cell.char != " " and not cell.continuation:
                painter.setPen(QPen(QColor(theme.TERM_BG)))
                flags = int(Qt.AlignLeft | Qt.AlignVCenter | Qt.TextDontClip)
                painter.drawText(QRectF(rect), flags, cell.char)
        else:
            colour.setAlpha(140)
            painter.setPen(QPen(colour, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(column.adjusted(0.5, 0.5, -0.5, -0.5))

    def _paint_scrollbar(self, painter) -> None:
        total = len(self.screen.scrollback) + self.screen.rows
        if not self.screen.scrollback or total <= self.screen.rows:
            return
        track = self.rect().adjusted(self.width() - 7, 4, -3, -4)
        if not track.isValid():
            return
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 26))
        painter.drawRoundedRect(QRectF(track), 2, 2)
        visible = self.screen.rows / total
        height = max(12, int(track.height() * visible))
        travel = track.height() - height
        position = 0
        if self._scroll_off:
            maximum = max(1, len(self.screen.scrollback))
            position = int(travel * (1 - self._scroll_off / maximum))
        painter.setBrush(QColor(255, 255, 255, 90))
        painter.drawRoundedRect(
            QRectF(track.left(), track.top() + position, track.width(), height),
            2, 2)


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

        #: The **single** screen state for this device (R-212).  The window feeds
        #: it from `_on_rx` even while the page is hidden, so `cli screen` never
        #: depends on which page the user is on.  `TerminalView` renders *this*
        #: object instead of keeping a private grid - two grids meant two parses
        #: per byte and, worse, a mirror that silently drifted from what the user
        #: saw (garbled echo while typing).
        self.mirror = Screen(rows=24, cols=80, scrollback=500)
        self.view.attach_screen(self.mirror)

        self.hint = QLabel(tr("term.hint"))
        self.hint.setStyleSheet("color: rgba(214, 226, 240, 130); font-size: 11px;")
        self.hint.setWordWrap(True)
        card_layout.addWidget(self.hint)

        layout.addWidget(card, 1)

        # 清屏 keeps the scrollback and the terminal's size; 重置 also drops the
        # history and every mode, so the two buttons must not share a slot.
        self.btn_clear.clicked.connect(self.view.clear)
        self.btn_reset.clicked.connect(self.view.reset)
        self.btn_full.clicked.connect(self.toggle_fullscreen)

    # ------------------------------------------------------------------ api
    def absorb(self, data: bytes) -> None:
        """Parse device bytes **once** into the shared screen (R-212).

        This is the only place bytes enter the device screen, and the window
        calls it from `_on_rx` whether or not the page is visible.  Parsing twice
        (mirror + view) duplicated every character and made the echo garble when
        the user pressed Left/Backspace, because the two grids had drifted apart.
        """
        if not data:
            return
        self.mirror.feed(data)
        replies = self.mirror.take_replies()
        if replies:
            self.keyBytes.emit(replies)
        self.view.refresh()

    #: Old name, kept because the tests and the send path call it.
    feed = absorb

    def reset(self) -> None:
        """Wipe the shared screen and the view's per-screen bookkeeping."""
        self.mirror = Screen(rows=self.mirror.rows, cols=self.mirror.cols,
                             scrollback=500)
        self.view.attach_screen(self.mirror)
        self.view.reset()

    def clear(self) -> None:
        """清屏 for the window's 清除窗口 button and the page's own button."""
        self.view.clear()

    def mirror_state(self, scrollback: int = 0) -> dict:
        """The device's screen as plain data, for ``omnicom.cli screen`` (R-211).

        ``screen`` is the live grid only (the part a user would see right now);
        ``text`` adds ``scrollback`` rows of history above it.  Styles are
        intentionally not exported yet - an agent needs the characters and the
        cursor, and colour would triple the payload.
        """
        screen = self.mirror
        lines = [screen.row_text(row) for row in range(screen.rows)]
        history = []
        if scrollback > 0 and screen.scrollback:
            wanted = screen.scrollback[-scrollback:]
            history = [Screen._scrollback_text(cells) for cells in wanted]
        return {
            "rows": screen.rows,
            "cols": screen.cols,
            "cursor": {"row": screen.cursor_row, "col": screen.cursor_col},
            "cursor_visible": bool(screen.cursor_visible),
            "screen": lines,
            "scrollback": history,
            "scrollback_total": len(screen.scrollback),
            "text": "\n".join(lines).rstrip("\n"),
            "title": str(getattr(screen, "title", "") or ""),
        }

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
