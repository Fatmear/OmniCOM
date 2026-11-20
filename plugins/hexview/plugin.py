"""HEX file viewer/editor plugin (``hexview``).

The user asked for "类似 winhex 那个功能：打开文件以 hex 方式查看、查找、修改保存、
复制选中内容" (R-303).  The page is deliberately split in two:

* :class:`HexDocument` is pure Python - a ``bytearray`` plus dirty/undo flags and
  the rendering/find helpers.  No Qt, so the tests can exercise the real
  offsets and edits without a window.
* :class:`HexPage` is the widget.  It renders one *page* of the file at a time
  (a few thousand bytes) instead of stuffing a 200 MB file into a text control:
  a huge document is what made every other offset-based editor feel sluggish.

Editing is offset-oriented on purpose.  WinHex-like free-form typing inside a
hex column needs a per-nibble cursor model; an explicit "写入字节" dialog over the
selected offset is predictable, undoable and testable, and it never corrupts an
offset because a key was pressed while the view was still painting.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .text import TEXT

#: Set by ``create()`` so module-level helpers (the theme wrappers below) and
#: every widget can translate without importing ``omnicom.i18n`` (R-322).
_api = None


def tr(key: str, **kwargs) -> str:
    """Translate one of this plugin's own keys through the host API.

    The plugin used to ``from omnicom.i18n import tr``, which tied an installed
    plugin to the host module layout.  The strings now live in ``text.py`` and
    are registered with ``api.register_text()`` (R-322).
    """
    if _api is not None:
        return _api.tr(key, **kwargs)
    entry = TEXT.get(key)
    if entry is None:
        return key
    text = entry[0] if _fallback_lang() == "zh" else entry[1]
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def _fallback_lang() -> str:
    """Language used before ``create()`` runs (tests call helpers directly)."""
    if _api is not None:
        return _api.language()
    return "zh"

#: Bytes per rendered row, like every hex editor.
ROW = 16
#: Rows fetched in one go.  512 rows = 8 KB, which is a couple of screens.
PAGE_ROWS = 512


def _ascii(byte: int) -> str:
    return chr(byte) if 32 <= byte < 127 else "."


class HexDocument:
    """A file as an editable byte buffer plus the text it renders to."""

    def __init__(self) -> None:
        self.path: str = ""
        self.data: bytearray = bytearray()
        self.dirty: bool = False
        #: Set after a successful save; the host asks before closing.
        self.saved_once: bool = False

    # ------------------------------------------------------------------ io
    def open(self, path: str) -> None:
        with open(path, "rb") as handle:
            self.data = bytearray(handle.read())
        self.path = path
        self.dirty = False
        self.saved_once = False

    def save(self, path: str | None = None) -> str:
        target = path or self.path
        if not target:
            raise OSError("no file name")
        with open(target, "wb") as handle:
            handle.write(self.data)
        self.path = target
        self.dirty = False
        self.saved_once = True
        return target

    # -------------------------------------------------------------- render
    def rows(self, start_row: int, count: int) -> list[tuple[int, str, str]]:
        """``[(offset, hex_text, ascii_text)]`` for ``count`` rows from a row."""
        out = []
        total = len(self.data)
        for index in range(count):
            offset = (start_row + index) * ROW
            if offset >= total:
                break
            chunk = bytes(self.data[offset:offset + ROW])
            hex_part = " ".join(f"{byte:02X}" for byte in chunk)
            # Pad so the ASCII column lines up on the last, short row.
            hex_part = hex_part.ljust(ROW * 3 - 1)
            text = "".join(_ascii(byte) for byte in chunk)
            out.append((offset, hex_part, text))
        return out

    def render(self, start_row: int, count: int) -> str:
        return "\n".join(
            f"{offset:08X}  {hex_part}  |{text}|"
            for offset, hex_part, text in self.rows(start_row, count))

    def total_rows(self) -> int:
        return (len(self.data) + ROW - 1) // ROW

    # ---------------------------------------------------------------- edits
    def write(self, offset: int, payload: bytes) -> None:
        """Overwrite ``payload`` at ``offset``, extending the file if needed."""
        if offset < 0:
            raise ValueError("negative offset")
        end = offset + len(payload)
        if end > len(self.data):
            self.data.extend(b"\x00" * (end - len(self.data)))
        self.data[offset:end] = payload
        self.dirty = True

    # ----------------------------------------------------------------- find
    def find(self, needle: bytes, start: int = 0) -> int:
        """Offset of the next occurrence at or after ``start``, or ``-1``."""
        if not needle:
            return -1
        return bytes(self.data).find(needle, max(0, start))


def parse_hex(text: str) -> bytes | None:
    """``"48 65 6C"`` / ``"48656C"`` -> bytes; ``None`` when it is not hex."""
    cleaned = re.sub(r"[\s,]", "", str(text))
    if not cleaned or len(cleaned) % 2:
        return None
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        return None


class HexPage(QWidget):
    """Toolbar + hex view + find/edit/save actions."""

    def __init__(self, api, parent=None) -> None:
        super().__init__(parent)
        self.api = api
        self.doc = HexDocument()
        self.page_index = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.open_button = QPushButton()
        self.open_button.clicked.connect(self.open_dialog)
        bar.addWidget(self.open_button)
        self.save_button = QPushButton()
        self.save_button.clicked.connect(self.save)
        bar.addWidget(self.save_button)
        self.saveas_button = QPushButton()
        self.saveas_button.clicked.connect(self.save_as_dialog)
        bar.addWidget(self.saveas_button)
        bar.addSpacing(12)
        self.find_edit = QLineEdit()
        self.find_edit.setMinimumWidth(180)
        self.find_edit.returnPressed.connect(lambda: self.find_next(0))
        bar.addWidget(self.find_edit)
        self.find_button = QPushButton()
        self.find_button.clicked.connect(lambda: self.find_next(0))
        bar.addWidget(self.find_button)
        self.findnext_button = QPushButton()
        self.findnext_button.clicked.connect(lambda: self.find_next(1))
        bar.addWidget(self.findnext_button)
        bar.addSpacing(12)
        self.goto_edit = QLineEdit()
        self.goto_edit.setMaximumWidth(120)
        self.goto_edit.returnPressed.connect(self.goto_typed)
        bar.addWidget(self.goto_edit)
        self.goto_button = QPushButton()
        self.goto_button.clicked.connect(self.goto_typed)
        bar.addWidget(self.goto_button)
        self.edit_button = QPushButton()
        self.edit_button.clicked.connect(self.edit_dialog)
        bar.addWidget(self.edit_button)
        self.batch_button = QPushButton()
        self.batch_button.clicked.connect(self.batch_dialog)
        bar.addWidget(self.batch_button)
        bar.addStretch(1)
        root.addLayout(bar)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.prev_button = QPushButton()
        self.prev_button.clicked.connect(lambda: self.page_by(-1))
        nav.addWidget(self.prev_button)
        self.next_button = QPushButton()
        self.next_button.clicked.connect(lambda: self.page_by(1))
        nav.addWidget(self.next_button)
        self.status = QLabel()
        nav.addWidget(self.status, 1)
        root.addLayout(nav)

        # 进度条 (user, 2026-11-19): a page-based viewer gave no sense of where
        # you were inside a 200 MB file.  A slider is both the progress bar and
        # the scrubber - dragging it jumps the page, and the label reports the
        # exact percentage.
        progress = QHBoxLayout()
        progress.setSpacing(8)
        self.position = QSlider(Qt.Horizontal)
        self.position.setRange(0, 1000)
        self.position.setPageStep(20)
        self.position.sliderMoved.connect(self._on_position_moved)
        progress.addWidget(self.position, 1)
        self.percent = QLabel()
        self.percent.setMinimumWidth(52)
        self.percent.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        progress.addWidget(self.percent)
        root.addLayout(progress)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        font.setFixedPitch(True)
        self.view.setFont(font)
        # The global ``*`` rule of the application sheet beats ``setFont`` (L-032),
        # so the monospace face has to be pinned in the widget stylesheet too.
        #
        # The colours come from the app palette rather than a hard-coded dark
        # surface: an all-black page next to the white cards read as a different
        # program (user, 2026-11-19).
        self.view.setStyleSheet(
            "QPlainTextEdit { font-family: Consolas, 'Cascadia Mono', monospace;"
            f" font-size: {theme_log_px()}px; background: {theme_card()};"
            f" color: {theme_ink()}; selection-background-color: {theme_sel()};"
            f" selection-color: {theme_ink()}; border: 1px solid {theme_line()};"
            " border-radius: 6px; padding: 6px; }")
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.show_menu)
        # Click a byte to edit it in place (user, 2026-11-19).  A read-only
        # editor that only offered a dialog was not what "像 winhex" means.
        self.view.viewport().installEventFilter(self)
        root.addWidget(self.view, 1)

        self._editor: QLineEdit | None = None
        self._find_needle = b""
        self._find_cursor = 0
        self.retranslate()

    # ------------------------------------------------------------------ ui
    def retranslate(self) -> None:
        self.open_button.setText(tr("hex.open"))
        self.save_button.setText(tr("hex.save"))
        self.saveas_button.setText(tr("hex.saveas"))
        self.find_button.setText(tr("hex.find"))
        self.findnext_button.setText(tr("hex.findnext"))
        self.find_edit.setPlaceholderText(tr("hex.findhint"))
        self.goto_button.setText(tr("hex.goto"))
        self.goto_edit.setPlaceholderText(tr("hex.gotohint"))
        self.edit_button.setText(tr("hex.edit"))
        self.batch_button.setText(tr("hex.batch"))
        self.prev_button.setText(tr("hex.prev"))
        self.next_button.setText(tr("hex.next"))
        self.position.setToolTip(tr("hex.progress"))
        self.refresh()

    def refresh(self) -> None:
        rows_per_page = PAGE_ROWS
        # Offset column is 8 hex digits + 2 spaces; each byte occupies 3 chars.
        self.view.setPlainText(self.doc.render(self.page_index * rows_per_page,
                                               rows_per_page))
        total = len(self.doc.data)
        first = self.page_index * rows_per_page * ROW
        last = min(total, first + rows_per_page * ROW)
        name = self.doc.path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] or tr("hex.nofile")
        pages = max(1, (self.doc.total_rows() + rows_per_page - 1) // rows_per_page)
        self.status.setText(tr(
            "hex.status", name=name + (" *" if self.doc.dirty else ""),
            first=first, last=last, total=total,
            page=self.page_index + 1, pages=pages))
        self.prev_button.setEnabled(self.page_index > 0)
        self.next_button.setEnabled(
            (self.page_index + 1) * rows_per_page * ROW < total)
        self.save_button.setEnabled(self.doc.dirty)
        self._sync_progress(total, last, pages)

    def _sync_progress(self, total: int, last: int, pages: int) -> None:
        """Point the slider at the current page without re-firing its signal."""
        if total <= 0:
            self.position.setValue(0)
            self.percent.setText("0%")
            self.position.setEnabled(False)
            return
        self.position.setEnabled(pages > 1)
        ratio = (self.page_index + 1) / pages
        self.position.blockSignals(True)
        self.position.setValue(round(ratio * 1000))
        self.position.blockSignals(False)
        self.percent.setText(f"{round(100 * last / total)}%")

    def _on_position_moved(self, value: int) -> None:
        """Dragging the progress bar jumps straight to that page."""
        pages = max(1, (self.doc.total_rows() + PAGE_ROWS - 1) // PAGE_ROWS)
        self.page_index = max(0, min(pages - 1, int(value / 1000 * pages)))
        self.refresh()

    # --------------------------------------------------------------- action
    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("hex.open"), "")
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> bool:
        try:
            self.doc.open(path)
        except OSError as exc:
            self.api.notify(str(exc), "error")
            return False
        self.page_index = 0
        self.refresh()
        return True

    def save(self) -> bool:
        if not self.doc.path:
            return self.save_as_dialog()
        try:
            self.doc.save()
        except OSError as exc:
            self.api.notify(str(exc), "error")
            return False
        self.refresh()
        self.api.notify(tr("hex.saved"), "ok")
        return True

    def save_as_dialog(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, tr("hex.saveas"), self.doc.path)
        if not path:
            return False
        try:
            self.doc.save(path)
        except OSError as exc:
            self.api.notify(str(exc), "error")
            return False
        self.refresh()
        return True

    def page_by(self, delta: int) -> None:
        target = self.page_index + delta
        pages = max(1, (self.doc.total_rows() + PAGE_ROWS - 1) // PAGE_ROWS)
        self.page_index = max(0, min(pages - 1, target))
        self.refresh()

    def goto(self, offset: int) -> None:
        if not self.doc.data:
            return
        offset = max(0, min(len(self.doc.data) - 1, offset))
        self.page_index = (offset // ROW) // PAGE_ROWS
        self.refresh()
        self.select_offset(offset)

    def goto_typed(self) -> None:
        text = self.goto_edit.text().strip()
        if not text:
            return
        try:
            offset = int(text, 16) if text.lower().startswith("0x") else int(text, 0)
        except ValueError:
            self.api.notify(tr("hex.badoffset"), "warn")
            return
        self.goto(offset)

    def select_offset(self, offset: int, length: int = 1) -> None:
        """Select ``length`` bytes in the rendered page (used by find/goto)."""
        row = offset // ROW
        start_of_page = self.page_index * PAGE_ROWS
        line = row - start_of_page
        if line < 0:
            return
        col = 10 + (offset % ROW) * 3
        cursor = self.view.textCursor()
        text = self.view.toPlainText().split("\n")
        if line >= len(text):
            return
        base = sum(len(item) + 1 for item in text[:line])
        cursor.setPosition(base + min(col, len(text[line])))
        cursor.setPosition(
            base + min(col + max(1, length) * 3 - 1, len(text[line])),
            cursor.MoveMode.KeepAnchor)
        self.view.setTextCursor(cursor)
        self.view.setFocus()

    def find(self, text: str, start: int = 0) -> int:
        needle = parse_hex(text)
        if needle is None:
            needle = str(text).encode("utf-8")
        self._find_needle = needle
        found = self.doc.find(needle, start)
        if found >= 0:
            self._find_cursor = found + max(1, len(needle))
            self.goto(found)
            self.select_offset(found, len(needle))
        return found

    def find_next(self, start_delta: int) -> None:
        """``start_delta == 0`` searches from the top, otherwise wraps forward.

        ``_find_cursor`` remembers where the last hit ended, so pressing 查找下一个
        repeatedly walks the file instead of re-finding the first match forever.
        """
        text = self.find_edit.text().strip()
        if not text:
            return
        if start_delta == 0 or not self._find_needle:
            found = self.find(text, 0)
        else:
            found = self.doc.find(self._find_needle, self._find_cursor)
            if found < 0:
                found = self.doc.find(self._find_needle, 0)   # wrap around
            if found >= 0:
                self._find_cursor = found + max(1, len(self._find_needle))
                self.goto(found)
                self.select_offset(found, len(self._find_needle))
        if found < 0:
            self.api.notify(tr("hex.notfound"), "warn")

    def edit_dialog(self) -> None:
        offset_text = self.goto_edit.text().strip()
        try:
            offset = int(offset_text, 16) if offset_text.lower().startswith("0x") \
                else int(offset_text, 0)
        except ValueError:
            self.api.notify(tr("hex.badoffset"), "warn")
            return
        payload = parse_hex(self.find_edit.text())
        if payload is None:
            self.api.notify(tr("hex.badbytes"), "warn")
            return
        answer = QMessageBox.question(
            self, tr("hex.edit"),
            tr("hex.editconfirm", offset=offset, n=len(payload)))
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.doc.write(offset, payload)
        except ValueError as exc:
            self.api.notify(str(exc), "warn")
            return
        self.goto(offset)
        self.refresh()

    def copy_selection(self) -> None:
        cursor = self.view.textCursor()
        self.view.copy()
        if cursor.hasSelection():
            self.api.notify(tr("hex.copied"), "ok")

    # ------------------------------------------------------- in-place edit
    def eventFilter(self, watched, event) -> bool:
        """Double-click a byte to edit it right where it is (user, 2026-11-19).

        The previous build only offered a dialog that took an offset from a text
        box, which is not what "像 WinHex" means: the user wants to click the
        byte they see and type over it.  A floating ``QLineEdit`` over the
        clicked cell keeps the model honest - one byte per commit, the same
        ``HexDocument.write`` the batch path uses.
        """
        if watched is self.view.viewport() \
                and event.type() == QEvent.Type.MouseButtonDblClick \
                and event.button() == Qt.LeftButton:
            offset = self.offset_at(event.position())
            if offset is not None:
                self.begin_inline_edit(offset)
                return True
        return super().eventFilter(watched, event)

    def offset_at(self, point) -> int | None:
        """Map a point in the text view to a file offset, or ``None``.

        Only the hex column is editable; a click on the offset or ASCII column
        is ignored instead of silently writing to a guessed byte.
        """
        if not self.doc.data:
            return None
        # Accept both ``QPointF`` (from a Qt event) and ``QPoint`` (from a test
        # or a helper): ``cursorForPosition`` only takes an integer point.
        point = point.toPoint() if hasattr(point, "toPoint") else point
        cursor = self.view.cursorForPosition(point)
        line = cursor.blockNumber()
        column = cursor.positionInBlock()
        # ``00000000  `` = 10 chars, then ``XX `` per byte.
        if column < 10:
            return None
        index = (column - 10) // 3
        if index < 0 or index >= ROW:
            return None
        # Clicking the separating space of a byte pair still targets that byte.
        offset = (self.page_index * PAGE_ROWS + line) * ROW + index
        if offset >= len(self.doc.data):
            return None
        return offset

    #: The inline editor's own padding, in px.  The application stylesheet gives
    #: every ``QLineEdit`` ``padding: 0 10px`` - 20 px of that is invisible to
    #: ``contentsMargins()`` and left only ~20 px for a 26 px wide ``00``, so the
    #: first digit was clipped (measured, L-306).  The editor therefore carries
    #: its own compact rule instead of inheriting the full band.
    EDITOR_PAD = 2

    def _inline_text_width(self) -> int:
        """Width of exactly ``00`` in the view's monospace face."""
        metrics = self.view.fontMetrics()
        return int(metrics.horizontalAdvance("00"))

    def inline_editor_size(self) -> tuple[int, int]:
        """Box the inline editor needs: the two digits plus its own trim."""
        border = 2                          # 1 px each side
        width = self._inline_text_width() + 2 * self.EDITOR_PAD + border
        height = int(self.view.fontMetrics().height()) + 2 + border
        return width, height

    def begin_inline_edit(self, offset: int) -> None:
        """Show a small editor over ``offset`` with the current byte prefilled."""
        self._close_editor()
        column = 10 + (offset % ROW) * 3
        row = offset // ROW - self.page_index * PAGE_ROWS
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.Start)
        for _ in range(row):
            cursor.movePosition(QTextCursor.Down)
        cursor.movePosition(QTextCursor.Right, n=column)
        rect = self.view.cursorRect(cursor)
        viewport = self.view.viewport()
        editor = QLineEdit(viewport)
        editor.setMaxLength(2)
        editor.setText(f"{self.doc.data[offset]:02X}")
        editor.setAlignment(Qt.AlignCenter)
        # Pin the same monospace metrics *and* a compact padding in the widget's
        # own sheet: the global ``QLineEdit`` rule would otherwise win with its
        # 20 px band and clip the digits (L-032 for the same trap on fonts).
        editor.setStyleSheet(
            "QLineEdit { font-family: Consolas, 'Cascadia Mono', monospace;"
            f" font-size: {theme_log_px()}px; padding: 0 {self.EDITOR_PAD}px;"
            " min-height: 0; background: %s; color: %s;"
            " border: 1px solid %s; border-radius: 3px; }"
            % (theme_card(), theme_ink(), theme_sel()))
        width, height = self.inline_editor_size()
        editor.setFixedSize(width, height)
        # Keep the whole box inside the viewport: editing a byte in the last
        # column used to place the editor half outside the visible area.
        x = max(0, min(rect.x() - 1, viewport.width() - width))
        y = max(0, min(rect.y(), viewport.height() - height))
        editor.move(x, y)
        editor.selectAll()
        editor.show()
        editor.setFocus()
        editor.returnPressed.connect(lambda: self.commit_inline_edit(offset))
        editor.editingFinished.connect(lambda: self.commit_inline_edit(offset))
        self._editor = editor

    def commit_inline_edit(self, offset: int) -> bool:
        editor, self._editor = self._editor, None
        if editor is None:
            return False
        text = editor.text().strip()
        editor.deleteLater()
        payload = parse_hex(text)
        if payload is None or len(payload) != 1:
            self.api.notify(tr("hex.badbytes"), "warn")
            return False
        self.doc.write(offset, payload)
        self.refresh()
        self.goto(offset)
        return True

    def _close_editor(self) -> None:
        editor, self._editor = self._editor, None
        if editor is not None:
            editor.deleteLater()

    # ----------------------------------------------------------- batch edit
    def batch_write(self, start: int, payload: bytes, overwrite: bool = True) -> int:
        """Write ``payload`` in one operation, e.g. a pasted block (user, 2026-11-19).

        ``overwrite`` writes in place; otherwise the bytes are inserted at
        ``start`` and the rest of the file shifts right.  The return value is the
        new file length so the caller can report it.  One call is also one undo
        step for the caller - a 4 KB paste must not be 4096 ``write`` calls.
        """
        if start < 0:
            raise ValueError("negative offset")
        if overwrite:
            self.doc.write(start, payload)
        else:
            if start > len(self.doc.data):
                self.doc.write(start - 1, b"")
            self.doc.data[start:start] = bytearray(payload)
            self.doc.dirty = True
        return len(self.doc.data)

    def batch_dialog(self) -> None:
        """Batch modify: write the 字节 field at the 偏移 field (or insert it)."""
        offset_text = self.goto_edit.text().strip()
        try:
            offset = int(offset_text, 16) if offset_text.lower().startswith("0x") \
                else int(offset_text, 0)
        except ValueError:
            self.api.notify(tr("hex.badoffset"), "warn")
            return
        payload = parse_hex(self.find_edit.text())
        if payload is None:
            self.api.notify(tr("hex.badbytes"), "warn")
            return
        box = QMessageBox(self)
        box.setWindowTitle(tr("hex.batch"))
        box.setText(tr("hex.batchconfirm", offset=offset, n=len(payload)))
        overwrite_button = box.addButton(tr("hex.batchoverwrite"),
                                        QMessageBox.AcceptRole)
        insert_button = box.addButton(tr("hex.batchinsert"), QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is overwrite_button:
            overwrite = True
        elif clicked is insert_button:
            overwrite = False
        else:
            return                       # Cancel / window closed
        size = self.batch_write(offset, payload, overwrite=overwrite)
        self.goto(offset)
        self.refresh()
        self.api.notify(tr("hex.batchdone", n=size), "ok")

    def show_menu(self, point) -> None:
        menu = self.api.menu(self.view)
        menu.addAction(tr("hex.copy"), self.copy_selection)
        menu.addAction(tr("hex.selectall"), self.view.selectAll)
        menu.addAction(tr("hex.find"), lambda: self.find_next(0))
        menu.addAction(tr("hex.batch"), self.batch_dialog)
        menu.exec(self.view.mapToGlobal(point))


def _palette():
    """The host palette, through the read-only facade (R-322)."""
    return _api.theme() if _api is not None else None


def theme_log_px() -> int:
    palette = _palette()
    return palette.log_px() if palette is not None else 13


def theme_card() -> str:
    palette = _palette()
    return palette.CARD if palette is not None else "#FFFFFF"


def theme_ink() -> str:
    palette = _palette()
    return palette.INK if palette is not None else "#1F2733"


def theme_line() -> str:
    palette = _palette()
    return palette.LINE_SOFT if palette is not None else "#E4E8EF"


def theme_sel() -> str:
    """Selection highlight that still reads on the light card (R-020)."""
    palette = _palette()
    return palette.LINE_STRONG if palette is not None else "#C9D1DE"


def create(api) -> QWidget:
    global _api
    _api = api
    api.register_text(TEXT)
    page = HexPage(api)
    api.on_language(lambda *_: page.retranslate())
    return page
