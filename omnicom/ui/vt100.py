"""A real VT100/xterm screen model, Qt free.

Why this exists
---------------
The terminal page used to emulate a *line* console: ``\\r`` moved to column 0,
``\\n`` opened a block, and every CSI cursor movement was **swallowed**.  That
works for ``login:`` and for a shell that only ever appends, but it diverges from
the device the moment the far end moves the cursor - a line editor echoing
``ESC[D`` (left arrow) is the everyday case.  The local column then no longer
matches the device's, and the next character overwrites (deletes) whatever was
under the local cursor.  The user saw exactly that: "左右键移动居然会删除字符".

A terminal is a **grid**, not a log.  This module keeps:

* a cell buffer ``rows x cols`` of ``(char, style)`` plus a cursor, so every
  write lands where the device put the cursor;
* the escape sequences a serial console actually receives: CUP/CUU/CUD/CUF/CUB,
  ``ESC[K``/``ESC[J``, ``ESC[2J``, default/256/truecolour SGR, DECAWM wrap,
  ``ESC[6n`` reporting, OSC swallowing, DECSTBM scrolling regions and DECOM;
* scrollback for lines pushed off the top, so history is not lost;
* wide (CJK) characters taking two cells, which is what makes a Chinese shell
  readable.

The model is deliberately free of Qt so ``tools/terminal_selftest.py`` can drive
it directly with thousands of byte sequences and compare against fixed strings.
"""

from __future__ import annotations

import copy
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

#: Palette used when a style carries no explicit colour.  Kept in sync with
#: ``theme.TERM_INK`` by the view, which passes its own default in.
DEFAULT_FG = "#D6E2F0"
#: Internal marker appended to the decoder state when an ESC appears
#: inside an OSC/DCS sequence (it may be the start of an ST terminator).
_ST_SENTINEL = "\x00st"
DEFAULT_BG = "#0F1620"

#: Standard 16 colours (ANSI 30..37 / 90..97) as used on the dark console.
ANSI_COLORS = {
    0: "#3B4453", 1: "#F87171", 2: "#5EE29B", 3: "#F0C572",
    4: "#7CB0FF", 5: "#C4A6FF", 6: "#63D8E8", 7: "#D6E2F0",
    8: "#6B7688", 9: "#FF9B9B", 10: "#8AF0BB", 11: "#FFD79A",
    12: "#A6C8FF", 13: "#D8C2FF", 14: "#95EAF5", 15: "#FFFFFF",
}


@lru_cache(maxsize=4096)
def cell_width(char: str) -> int:
    """Display width of one character: 2 for CJK, 0 for combining marks.

    Cached because it is called for **every** printable byte and ``unicodedata``
    does two table lookups per call - a 1100-byte batch was spending most of its
    time here (L-271).
    """
    if char == "\u200b":
        return 0
    category = unicodedata.category(char)
    if category in ("Mn", "Me", "Cf"):
        return 0
    if unicodedata.east_asian_width(char) in ("W", "F"):
        return 2
    return 1


@dataclass(frozen=True)
class Style:
    """One cell's appearance: this is the minimum a terminal has to carry."""

    fg: str = DEFAULT_FG
    bg: str = DEFAULT_BG
    bold: bool = False
    italic: bool = False
    underline: bool = False
    inverse: bool = False
    dim: bool = False
    strike: bool = False

    def with_attrs(self, **changes) -> Style:
        return Style(**{**self.__dict__, **changes})


@dataclass(slots=True)
class Cell:
    """One grid position.  ``width`` is 2 for the leading cell of a wide char.

    ``slots=True`` matters: a full screen scroll allocates ``cols`` cells per
    line, so 2 400 lines is ~190 000 instances, and the ``__dict__`` a plain
    dataclass carries made that the dominant cost of a big receive burst
    (L-271).
    """

    char: str = " "
    style: Style = field(default_factory=Style)
    width: int = 1
    #: True on the right half of a wide character (never written directly).
    continuation: bool = False

    def blank(self, style: Style) -> Cell:
        self.char = " "
        self.style = style
        self.width = 1
        self.continuation = False
        return self


class Screen:
    """A VT100-compatible screen: cells, cursor, scrollback, modes.

    Coordinates are 0-based internally and 1-based in escape sequences, which is
    the usual source of off-by-one bugs - the two helper pairs keep that in one
    place (``_move_to`` takes 0-based, ``_csi_move`` converts).
    """

    def __init__(self, rows: int = 24, cols: int = 80, scrollback: int = 2000) -> None:
        self.rows = max(2, int(rows))
        self.cols = max(8, int(cols))
        self.scrollback_limit = max(0, int(scrollback))
        self.scrollback: list[list[Cell]] = []
        #: Recycled blank rows for the scroll path (see ``_blank_row``).
        self._spare_rows: list[list[Cell]] = []
        self.cursor_row = 0
        self.cursor_col = 0
        self.style = Style()
        #: Pending wrap (DECAWM): a character that lands in the last column leaves
        #: the cursor on that column and only wraps when the *next* one arrives.
        self.wrap_pending = False
        self.autowrap = True
        self.insert_mode = False
        self.origin_mode = False
        self.cursor_visible = True
        self.bracketed_paste = False
        self.app_cursor_keys = False
        self.scroll_top = 0
        self.scroll_bottom = self.rows - 1
        self.title = ""
        self.bell = False
        #: Set when the application asks for the cursor position (ESC[6n); the
        #: view answers through ``take_replies()``.
        self._replies: list[bytes] = []
        self._grid: list[list[Cell]] = [
            [Cell() for _ in range(self.cols)] for _ in range(self.rows)
        ]
        self._decoder_state = ""

    # ------------------------------------------------------------------ grid
    def cell(self, row: int, col: int) -> Cell:
        if 0 <= row < self.rows and 0 <= col < self.cols:
            return self._grid[row][col]
        raise IndexError((row, col))

    def row_text(self, row: int) -> str:
        """One row as text, trailing blanks trimmed and continuations dropped."""
        if not 0 <= row < self.rows:
            return ""
        out = []
        for cell in self._grid[row]:
            if cell.continuation:
                continue
            out.append(cell.char)
        return "".join(out).rstrip()

    def text(self, keep_scrollback: bool = True) -> str:
        """Whole screen as plain text (used by the view and by tests)."""
        rows = []
        if keep_scrollback:
            rows.extend(self._scrollback_text(row) for row in self.scrollback)
        rows.extend(self.row_text(row) for row in range(self.rows))
        return "\n".join(rows).rstrip("\n")

    @staticmethod
    def _scrollback_text(cells: list[Cell]) -> str:
        out = [cell.char for cell in cells if not cell.continuation]
        return "".join(out).rstrip()

    def resize(self, rows: int, cols: int) -> None:
        """Re-shape the grid, **re-flowing** the text to the new width (R-214).

        The old version copied the top-left rectangle and cut everything past the
        new width.  For a device that soft-wraps (every shell, and our own
        ``_put``) that silently **deleted** the tail of a long line while the
        cursor stayed where it was - so after narrowing the window, the following
        Up/Down/Delete landed on the wrong row/column and the screen looked
        scrambled.  That is the reported 「换行显示在这一行前面，上下切换或删除
        就显示异常」.

        Re-flow keeps every cell: rows are unwrapped into one logical stream
        (trailing blanks trimmed), the cursor is tracked through that stream, and
        the stream is re-wrapped at the new width.
        """
        rows = max(2, int(rows))
        cols = max(8, int(cols))
        if rows == self.rows and cols == self.cols:
            return
        self._reflow(rows, cols)

    def _reflow(self, rows: int, cols: int) -> None:
        """Re-wrap the logical lines of the grid into ``rows x cols``."""
        # 1. Unwrap: every grid row becomes a logical line.  Soft-wrapped
        #    continuations are just the next row, which is exactly how our writer
        #    produced them, so joining rows is lossless.
        logical: list[list[Cell]] = []
        for row in self._grid:
            line = [cell for cell in row if not cell.continuation]
            while line and line[-1].char == " " and line[-1].width == 1:
                line.pop()
            logical.append(line)

        # 2. Where is the cursor in that model?
        cursor = (self.cursor_row, self.cursor_col)
        # 3. Re-wrap.
        new_grid: list[list[Cell]] = []
        mapping: dict[tuple[int, int], tuple[int, int]] = {}

        def new_blank() -> list[Cell]:
            return [Cell(style=self.style) for _ in range(cols)]

        current = new_blank()
        new_grid.append(current)
        for src_row, line in enumerate(logical):
            if src_row:
                # A hard line end starts a new grid row.
                current = new_blank()
                new_grid.append(current)
            col = 0
            for src_col, cell in enumerate(line):
                width = max(1, cell.width) if cell.char else 1
                if col + width > cols:
                    current = new_blank()
                    new_grid.append(current)
                    col = 0
                mapping[(src_row, src_col)] = (len(new_grid) - 1, col)
                if col < cols:
                    current[col] = copy.copy(cell)
                    if width == 2 and col + 1 < cols:
                        current[col + 1] = Cell(style=cell.style, width=2,
                                                continuation=True)
                col += width

        # 4. Keep the cursor on the same character (or at the end of its line).
        target = mapping.get(cursor)
        if target is None:
            # Cursor sat just past the last character: land at the end of that
            # logical line's last wrapped row.
            src_row = min(cursor[0], len(logical) - 1)
            last_col = len(logical[src_row]) - 1 if logical[src_row] else 0
            target = mapping.get((src_row, last_col))
        if target is None:
            target = (0, 0)

        # 5. Trim / pad vertically, keeping the cursor visible.
        overflow = len(new_grid) - rows
        if overflow > 0:
            # Re-wrapping a narrow window produces *more* rows than before, and
            # the extra ones belong at the bottom, not the top: pushing the first
            # rows into the scrollback silently removed the very beginning of the
            # line the user was looking at (the `$ ` prompt in the probe, R-214).
            # Only drop from the top when the cursor itself would fall off the
            # bottom, i.e. when there is genuinely more text than the window.
            keep_cursor = target[0] + 1
            drop_top = max(0, min(overflow, keep_cursor - rows))
            if drop_top:
                for row in new_grid[:drop_top]:
                    if self.scrollback_limit:
                        self.scrollback.append(row)
                del new_grid[:drop_top]
                if len(self.scrollback) > self.scrollback_limit:
                    del self.scrollback[:len(self.scrollback)
                                        - self.scrollback_limit]
                target = (target[0] - drop_top, target[1])
                overflow -= drop_top
            if overflow > 0:
                # Still too many rows below the cursor: drop the tail, which is
                # blank space the re-wrap created rather than real content.
                del new_grid[len(new_grid) - overflow:]
        while len(new_grid) < rows:
            new_grid.append(new_blank())

        self._grid = new_grid
        self._spare_rows = []
        self.rows, self.cols = rows, cols
        self.scroll_top = 0
        self.scroll_bottom = rows - 1
        self.cursor_row = max(0, min(rows - 1, target[0]))
        self.cursor_col = max(0, min(cols - 1, target[1]))
        self.wrap_pending = False

    # ---------------------------------------------------------------- output
    def feed(self, data: bytes) -> None:
        """Consume raw device bytes (UTF-8, escape sequences included)."""
        text = data.decode("utf-8", "replace")
        self.feed_text(text)

    def feed_text(self, text: str) -> None:
        index = 0
        length = len(text)
        while index < length:
            char = text[index]
            if self._decoder_state:
                index = self._consume_escape(char, index)
                continue
            if char == "\x1b":
                self._decoder_state = "\x1b"
                index += 1
                continue
            index = self._consume_char(char, index)
        self.wrap_pending = self.wrap_pending and self.autowrap

    # -- printable / control ------------------------------------------------
    def _consume_char(self, char: str, index: int) -> int:
        if char == "\r":
            self.cursor_col = 0
            self.wrap_pending = False
            return index + 1
        if char == "\n":
            self._line_feed()
            self.wrap_pending = False
            return index + 1
        if char == "\b":
            if self.wrap_pending:
                self.wrap_pending = False
            elif self.cursor_col > 0:
                self.cursor_col -= 1
            return index + 1
        if char == "\t":
            self._tab_forward()
            return index + 1
        if char == "\x07":
            self.bell = True
            return index + 1
        if char == "\x0e" or char == "\x0f":
            return index + 1          # SO/SI: charset shifts, ignored
        if char < " ":
            return index + 1
        if char == "\x7f":
            # DEL / Backspace (R-212).  A *terminal* does not edit the buffer on
            # its own - the shell does, and re-echoes.  But a device that has
            # local echo on (or a plain `getchar()` loop) sends 0x7f to mean
            # "erase the previous character" without echoing anything, and that
            # used to be dropped on the floor: the character stayed on screen
            # while the device had already forgotten it, so the next line looked
            # shifted by one column.  Erasing here matches what every xterm-class
            # terminal shows for 0x7f.
            if self.wrap_pending:
                self.wrap_pending = False
            elif self.cursor_col > 0:
                self.cursor_col -= 1
                row = self._grid[self.cursor_row]
                if not row[self.cursor_col].continuation:
                    row[self.cursor_col] = Cell(style=self.style)
            return index + 1
        self._put(char)
        return index + 1

    def _put(self, char: str) -> None:
        width = cell_width(char)
        if width == 0:
            # Combining mark: attach to the previous cell.
            col = self.cursor_col - 1
            if col >= 0:
                cell = self._grid[self.cursor_row][col]
                cell.char = cell.char + char
            return
        if self.wrap_pending:
            if self.autowrap:
                self.cursor_col = 0
                self._line_feed()
            self.wrap_pending = False
        if width == 2 and self.cursor_col == self.cols - 1:
            # A wide glyph cannot straddle the edge, so it starts on the next row
            # *before* it is written - checking ``>= cols`` instead would leave a
            # flicker of the character's first half in the last column (L-270).
            if self.autowrap:
                self.cursor_col = 0
                self._line_feed()
            else:
                self.cursor_col = self.cols - 2
        if self.insert_mode:
            self._shift_right(width)
        row = self._grid[self.cursor_row]
        row[self.cursor_col] = Cell(char=char, style=self.style, width=width)
        if width == 2:
            row[self.cursor_col + 1] = Cell(char="", style=self.style,
                                            width=2, continuation=True)
        self.cursor_col += width
        if self.cursor_col >= self.cols:
            self.cursor_col = self.cols - 1
            self.wrap_pending = self.autowrap

    def _shift_right(self, count: int) -> None:
        row = self._grid[self.cursor_row]
        for col in range(self.cols - 1, self.cursor_col + count - 1, -1):
            row[col] = row[col - count]

    def _tab_forward(self) -> None:
        target = min(self.cols - 1, (self.cursor_col // 8 + 1) * 8)
        self.cursor_col = target
        self.wrap_pending = False

    def _line_feed(self) -> None:
        if self.cursor_row == self.scroll_bottom:
            self._scroll_up(1)
        elif self.cursor_row < self.rows - 1:
            self.cursor_row += 1

    def _reverse_line_feed(self) -> None:
        if self.cursor_row == self.scroll_top:
            self._scroll_down(1)
        elif self.cursor_row > 0:
            self.cursor_row -= 1

    # -------------------------------------------------------------- scrolling
    def _blank_row(self) -> list[Cell]:
        """A blank row, recycled from the pool when possible.

        ``_scroll_up`` runs once per line feed, so allocating ``cols`` fresh
        ``Cell`` objects each time was the single hottest path: a 2 400-line
        burst spent ~190 ms building 192 000 dataclass instances (L-271).
        Pooled rows are blanked in place instead, and the pool is dropped on
        ``resize``/``hard_reset`` because the width may have changed.
        """
        if self._spare_rows:
            row = self._spare_rows.pop()
            for cell in row:
                cell.blank(self.style)
            return row
        return [Cell(style=self.style) for _ in range(self.cols)]

    def _scroll_up(self, count: int) -> None:
        """Move the region up; leave lines pulled out of the *full* screen in the
        scrollback so the history is not lost."""
        top, bottom = self.scroll_top, self.scroll_bottom
        full = top == 0 and bottom == self.rows - 1
        for _ in range(max(0, count)):
            removed = self._grid.pop(top)
            if full and self.scrollback_limit:
                self.scrollback.append(removed)
                overflow = len(self.scrollback) - self.scrollback_limit
                if overflow > 0:
                    # The oldest rows leave the history: keep them as blanks.
                    for row in self.scrollback[:overflow]:
                        for cell in row:
                            cell.blank(self.style)
                        self._spare_rows.append(row)
                    del self.scrollback[:overflow]
                self._grid.insert(bottom, self._blank_row())
            else:
                for cell in removed:
                    cell.blank(self.style)
                self._grid.insert(bottom, removed)

    def _scroll_down(self, count: int) -> None:
        top, bottom = self.scroll_top, self.scroll_bottom
        for _ in range(max(0, count)):
            removed = self._grid.pop(bottom)
            for cell in removed:
                cell.blank(self.style)
            self._grid.insert(top, removed)

    # ------------------------------------------------------------- erase/ins
    def _erase_cells(self, row: int, start: int, end: int) -> None:
        for col in range(max(0, start), min(self.cols, end)):
            self._grid[row][col] = Cell(style=self.style)

    def erase_in_display(self, mode: int) -> None:
        if mode == 0:
            self._erase_cells(self.cursor_row, self.cursor_col, self.cols)
            for row in range(self.cursor_row + 1, self.rows):
                self._erase_cells(row, 0, self.cols)
        elif mode == 1:
            for row in range(0, self.cursor_row):
                self._erase_cells(row, 0, self.cols)
            self._erase_cells(self.cursor_row, 0, self.cursor_col + 1)
        else:                                    # 2 / 3: whole screen
            for row in range(self.rows):
                self._erase_cells(row, 0, self.cols)
            if mode == 3:
                self.scrollback.clear()
        self.wrap_pending = False

    def erase_in_line(self, mode: int) -> None:
        if mode == 0:
            self._erase_cells(self.cursor_row, self.cursor_col, self.cols)
        elif mode == 1:
            self._erase_cells(self.cursor_row, 0, self.cursor_col + 1)
        else:
            self._erase_cells(self.cursor_row, 0, self.cols)
        self.wrap_pending = False

    def insert_lines(self, count: int) -> None:
        if not self.scroll_top <= self.cursor_row <= self.scroll_bottom:
            return
        for _ in range(max(1, count)):
            del self._grid[self.scroll_bottom]
            self._grid.insert(self.cursor_row,
                              [Cell(style=self.style) for _ in range(self.cols)])
        self.cursor_col = 0

    def delete_lines(self, count: int) -> None:
        if not self.scroll_top <= self.cursor_row <= self.scroll_bottom:
            return
        for _ in range(max(1, count)):
            del self._grid[self.cursor_row]
            self._grid.insert(self.scroll_bottom,
                              [Cell(style=self.style) for _ in range(self.cols)])
        self.cursor_col = 0

    def insert_chars(self, count: int) -> None:
        row = self._grid[self.cursor_row]
        count = max(1, count)
        for col in range(self.cols - 1, self.cursor_col + count - 1, -1):
            # ``copy.copy``, not a plain assignment: two grid positions must never
            # share one ``Cell`` object, or editing one silently edits the other
            # (measured: ESC[@ produced "abcdcdef") (L-270).
            row[col] = copy.copy(row[col - count])
        # The vacated cells are blanks, not stale copies of what was there.
        for col in range(self.cursor_col,
                         min(self.cols, self.cursor_col + count)):
            row[col] = Cell(style=self.style)

    def delete_chars(self, count: int) -> None:
        row = self._grid[self.cursor_row]
        count = max(1, count)
        for col in range(self.cursor_col, self.cols):
            source = col + count
            row[col] = (copy.copy(row[source]) if source < self.cols
                        else Cell(style=self.style))

    def _line_end(self) -> int:
        """Column just past the last non-blank cell on the cursor row.

        ``ESC[4~`` (End) must land **after the text**, not at ``cols - 1``: a
        shell that moves to the far right of the window pushes its next echo off
        to the wall and the line reads as scrambled (R-212).
        """
        row = self._grid[self.cursor_row]
        last = 0
        for col, cell in enumerate(row):
            if cell.char and cell.char != " " and not cell.continuation:
                last = col + max(1, cell.width)
        return min(self.cols - 1, last)

    # ---------------------------------------------------------------- cursor
    def _move_to(self, row: int, col: int) -> None:
        """0-based absolute move, honouring DECOM and clamping to the region."""
        if self.origin_mode:
            row += self.scroll_top
        self.cursor_row = max(0, min(self.rows - 1, row))
        self.cursor_col = max(0, min(self.cols - 1, col))
        self.wrap_pending = False

    def _move_rel(self, drow: int, dcol: int) -> None:
        row = self.cursor_row + drow
        if drow:
            top = self.scroll_top if self.origin_mode else 0
            bottom = self.scroll_bottom if self.origin_mode else self.rows - 1
            row = max(top, min(bottom, row))
        else:
            row = self.cursor_row
        self._move_to(row - (self.scroll_top if self.origin_mode else 0),
                      max(0, min(self.cols - 1, self.cursor_col + dcol)))

    # --------------------------------------------------------- escape parsing
    def _consume_escape(self, char: str, index: int) -> int:
        state = self._decoder_state
        if state == "\x1b":
            if char == "[":
                self._decoder_state = "\x1b["
                return index + 1
            if char == "]":
                self._decoder_state = "\x1b]"
                return index + 1
            if char == "P":
                self._decoder_state = "\x1bP"
                return index + 1
            if char in "()#":
                self._decoder_state = "\x1b" + char
                return index + 1
            if char == "7":
                self._saved = (self.cursor_row, self.cursor_col, self.style)
                self._decoder_state = ""
                return index + 1
            if char == "8":
                saved = getattr(self, "_saved", None)
                if saved:
                    self.cursor_row, self.cursor_col, self.style = saved
                self._decoder_state = ""
                return index + 1
            if char == "M":
                self._reverse_line_feed()
                self._decoder_state = ""
                return index + 1
            if char == "D":
                self._line_feed()
                self._decoder_state = ""
                return index + 1
            if char == "E":
                self.cursor_col = 0
                self._line_feed()
                self._decoder_state = ""
                return index + 1
            if char == "c":
                self.hard_reset()
                self._decoder_state = ""
                return index + 1
            self._decoder_state = ""
            return index + 1
        if state in ("\x1b(", "\x1b)", "\x1b#"):
            self._decoder_state = ""
            return index + 1
        # ``startswith``, not ``in``: the state grows while the sequence is being
        # collected (``ESC]`` -> ``ESC]0`` -> ...), so an equality test stops
        # matching after the first character and the rest is fed to the CSI
        # parser - which silently ate one letter of every window title (L-270).
        # Checked **before** the OSC branch below: a sentinel state still
        # starts with ``ESC]``, so the OSC branch would keep collecting it
        # forever and the terminator would never be seen (L-270).
        if _ST_SENTINEL in state:
            # The bytes after the sentinel are what followed the inner ESC.
            base, _, tail = state.partition(_ST_SENTINEL)
            if tail == "" and char == "\\":
                # ESC \\ -> the sequence ends here.
                if base.startswith("\x1b]"):
                    self._apply_osc_body(base)
                self._decoder_state = ""
            else:
                # A lone ESC inside the sequence (not an ST): keep the
                # characters that already arrived and go on collecting.
                self._decoder_state = base + tail + char
            return index + 1
        if state.startswith("\x1b]") or state.startswith("\x1bP"):
            if char == "\x07":
                if state.startswith("\x1b]"):
                    self._apply_osc()
                self._decoder_state = ""
                return index + 1
            if char == "\x1b":
                # Possible ST terminator (ESC \\): mark it with a sentinel
                # suffix the branch below recognises, instead of appending a
                # bare ESC that no branch matches again (L-270).
                self._decoder_state = state + _ST_SENTINEL
                return index + 1
            # The **first** character after ESC] belongs to the OSC body; an
            # earlier version appended it to the *state* string instead, so every
            # title lost its first letter ("my-title" -> "y-title") (L-270).
            self._decoder_state = state + char
            if len(self._decoder_state) > 4096:
                self._decoder_state = ""
            return index + 1
        # CSI: collect until a final byte in @..~
        if "@" <= char <= "~":
            self._apply_csi(state[2:] + char)
            self._decoder_state = ""
            return index + 1
        self._decoder_state += char
        if len(self._decoder_state) > 64:
            self._decoder_state = ""
        return index + 1

    def _apply_osc(self) -> None:
        self._apply_osc_body(self._decoder_state)

    def _apply_osc_body(self, state: str) -> None:
        """Interpret an OSC body given the raw state string (prefix included)."""
        body = state[2:] if state.startswith("\x1b]") else state
        if body.endswith(_ST_SENTINEL):
            body = body[:-len(_ST_SENTINEL)]
        if body.startswith("\x1b"):
            body = body[1:]
        if body.startswith("0;") or body.startswith("2;") or body.startswith("1;"):
            self.title = body[2:]

    def _apply_csi(self, sequence: str) -> None:
        final = sequence[-1]
        body = sequence[:-1]
        private = body[:1] in ("?", ">", "!", "=")
        if private:
            body = body[1:]
        params = []
        for part in body.split(";"):
            part = part.lstrip("?>!=")
            try:
                params.append(int(part) if part else 0)
            except ValueError:
                params.append(0)
        first = params[0] if params else 0
        count = first or 1

        if final == "m":
            self._apply_sgr(params or [0])
        elif final == "A":
            self._move_rel(-count, 0)
        elif final == "B":
            self._move_rel(count, 0)
        elif final == "C":
            self._move_rel(0, count)
        elif final == "D":
            self._move_rel(0, -count)
        elif final == "E":
            self._move_rel(count, 0)
            self.cursor_col = 0
        elif final == "F":
            self._move_rel(-count, 0)
            self.cursor_col = 0
        elif final == "G" or final == "`":
            self._move_to(self.cursor_row, count - 1)
        elif final == "d":
            self._move_to(count - 1, self.cursor_col)
        elif final in ("H", "f"):
            row = (params[0] if params and params[0] else 1) - 1
            col = (params[1] if len(params) > 1 and params[1] else 1) - 1
            self._move_to(row, col)
        elif final == "J":
            self.erase_in_display(first)
        elif final == "K":
            self.erase_in_line(first)
        elif final == "L":
            self.insert_lines(count)
        elif final == "M":
            self.delete_lines(count)
        elif final == "@":
            self.insert_chars(count)
        elif final == "P":
            self.delete_chars(count)
        elif final == "X":
            self._erase_cells(self.cursor_row, self.cursor_col,
                              self.cursor_col + count)
        elif final == "S":
            self._scroll_up(count)
        elif final == "T":
            self._scroll_down(count)
        elif final == "r":
            top = (params[0] if params and params[0] else 1) - 1
            bottom = (params[1] if len(params) > 1 and params[1]
                      else self.rows) - 1
            if 0 <= top < bottom < self.rows:
                self.scroll_top, self.scroll_bottom = top, bottom
                self._move_to(0, 0)
        elif final == "h" or final == "l":
            self._apply_mode(params, final == "h", private)
        elif final == "n":
            if first == 6:
                row = self.cursor_row + 1
                col = self.cursor_col + 1
                self._replies.append(
                    f"\x1b[{row};{col}R".encode("ascii"))
        elif final == "c":
            if first == 0:
                self._replies.append(b"\x1b[?1;2c")
        elif final == "s":
            self._saved = (self.cursor_row, self.cursor_col, self.style)
        elif final == "u":
            saved = getattr(self, "_saved", None)
            if saved:
                self.cursor_row, self.cursor_col, self.style = saved
        elif final == "~":
            self._apply_tilde(first, params)
        # Everything else is a mode/query we can safely ignore.

    def _apply_tilde(self, first: int, params: list[int]) -> None:
        """``ESC[n ~`` — the numbered editing keys (R-212).

        These were **silently dropped**: ``ESC[3~`` (Delete) hit the catch-all
        comment at the end of ``_apply_csi`` and did nothing at all, so pressing
        Delete changed nothing on screen while the device had already removed the
        character.  The next echo then landed on a stale cursor and the line
        looked scrambled - the reported 「按删除键乱行」.

        Also here: Home/End (``1``/``4``/``7``/``8``) and PageUp/PageDown
        (``5``/``6``), which a shell sends when a line is longer than the window.
        """
        if first == 2:                         # Insert
            self.insert_mode = True
        elif first == 3:                       # Delete: pull the row left
            self.delete_chars(1)
        elif first in (1, 7):                  # Home
            self._move_to(self.cursor_row, 0)
        elif first in (4, 8):                  # End
            self._move_to(self.cursor_row, self._line_end())
        elif first == 5:                       # PageUp
            self._move_rel(-max(1, self.rows - 1), 0)
        elif first == 6:                       # PageDown
            self._move_rel(max(1, self.rows - 1), 0)
        elif first == 200:                     # bracketed paste begin
            self.bracketed_paste = True
        elif first == 201:                     # bracketed paste end
            self.bracketed_paste = False
        elif first == 11:                      # ESC[11~ .. : F1..F4 aliases
            pass
        del params                              # reserved for modifier suffixes

    def _apply_mode(self, params: list[int], enabled: bool, private: bool) -> None:
        for code in params:
            if private and code == 7:
                self.autowrap = enabled
            elif private and code == 25:
                self.cursor_visible = enabled
            elif private and code == 1:
                self.app_cursor_keys = enabled
            elif private and code == 6:
                self.origin_mode = enabled
            elif not private and code == 4:
                # Insert/Replace (IRM).  ``ESC[4h`` is what a terminal sends;
                # ``ESC[4~`` is **End** and must not be conflated with it - the
                # first version of the ``~`` table set insert_mode from 4 and the
                # End branch became unreachable (R-212).
                self.insert_mode = enabled
                self._move_to(0, 0)
            elif private and code == 2004:
                self.bracketed_paste = enabled
            elif not private and code == 4:
                self.insert_mode = enabled

    def _apply_sgr(self, codes: list[int]) -> None:
        index = 0
        style = self.style
        while index < len(codes):
            code = codes[index]
            if code == 0:
                style = Style()
            elif code == 1:
                style = style.with_attrs(bold=True)
            elif code == 2:
                style = style.with_attrs(dim=True)
            elif code == 3:
                style = style.with_attrs(italic=True)
            elif code == 4:
                style = style.with_attrs(underline=True)
            elif code == 7:
                style = style.with_attrs(inverse=True)
            elif code == 9:
                style = style.with_attrs(strike=True)
            elif code == 22:
                style = style.with_attrs(bold=False, dim=False)
            elif code == 23:
                style = style.with_attrs(italic=False)
            elif code == 24:
                style = style.with_attrs(underline=False)
            elif code == 27:
                style = style.with_attrs(inverse=False)
            elif code == 29:
                style = style.with_attrs(strike=False)
            elif 30 <= code <= 37:
                style = style.with_attrs(fg=ANSI_COLORS[code - 30])
            elif code == 39:
                style = style.with_attrs(fg=DEFAULT_FG)
            elif 40 <= code <= 47:
                style = style.with_attrs(bg=ANSI_COLORS[code - 40])
            elif code == 49:
                style = style.with_attrs(bg=DEFAULT_BG)
            elif 90 <= code <= 97:
                style = style.with_attrs(fg=ANSI_COLORS[code - 90 + 8])
            elif 100 <= code <= 107:
                style = style.with_attrs(bg=ANSI_COLORS[code - 100 + 8])
            elif code in (38, 48):
                colour, used = self._extended_colour(codes, index + 1)
                if colour is not None:
                    style = style.with_attrs(
                        **{"fg" if code == 38 else "bg": colour})
                index += used
            index += 1
        self.style = style

    @staticmethod
    def _extended_colour(codes: list[int], start: int) -> tuple[str | None, int]:
        """Parse ``5;n`` (256 colours) or ``2;r;g;b``; returns (colour, params used)."""
        if start >= len(codes):
            return None, 0
        mode = codes[start]
        if mode == 5 and start + 1 < len(codes):
            return f"ansi256:{codes[start + 1]}", 2
        if mode == 2 and start + 3 < len(codes):
            red, green, blue = codes[start + 1:start + 4]
            return (f"#{max(0, min(255, red)):02X}"
                    f"{max(0, min(255, green)):02X}"
                    f"{max(0, min(255, blue)):02X}"), 4
        return None, 1

    # ------------------------------------------------------------------ misc
    def take_replies(self) -> bytes:
        """Bytes the terminal owes the device (cursor reports, DA replies)."""
        data = b"".join(self._replies)
        self._replies.clear()
        return data

    def take_title(self) -> str:
        title, self.title = self.title, ""
        return title

    def take_bell(self) -> bool:
        bell, self.bell = self.bell, False
        return bell

    def hard_reset(self) -> None:
        """``ESC c``: everything back to the initial state, grid kept blank."""
        rows, cols = self.rows, self.cols
        scrollback = self.scrollback_limit
        self.__init__(rows, cols, scrollback)

    def selected_text(self, start: tuple[int, int],
                      end: tuple[int, int]) -> str:
        """Plain text between two ``(row, col)`` grid positions, inclusive."""
        (r1, c1), (r2, c2) = start, end
        if (r2, c2) < (r1, c1):
            (r1, c1), (r2, c2) = (r2, c2), (r1, c1)
        lines = []
        row = r1
        while row <= r2:
            first = c1 if row == r1 else 0
            last = c2 if row == r2 else self.cols - 1
            cells = self._grid[row][first:last + 1]
            lines.append("".join(cell.char for cell in cells
                                 if not cell.continuation))
            row += 1
        return "\n".join(lines)
