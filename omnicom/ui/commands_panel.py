"""Right-sidebar command list: groups, notes, per-item and per-group export."""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..commands import UNGROUPED, CommandStore, payload_for
from ..i18n import tr
from . import theme
from .widgets import Badge, Combo, GlyphButton
from .widgets import Button as QPushButton

FILE_FILTER = "OmniCOM (*.json);;All files (*)"


def payload_of(item: dict) -> bytes:
    return payload_for(item)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class ElidedLabel(QLabel):
    """Label that ellipsises instead of forcing its parent wider."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self._full = text
        self.setText(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setText(text)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setFont(self.font())
        text = self.fontMetrics().elidedText(self._full, Qt.ElideRight,
                                             max(10, self.width()))
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.rect(), Qt.AlignVCenter | Qt.AlignLeft, text)
        painter.end()


class PasteBox(QPlainTextEdit):
    """Paste-aware box: a multi-line paste imports immediately."""

    pasted = Signal(str)

    def insertFromMimeData(self, source) -> None:
        text = source.text()
        if text:
            self.pasted.emit(text)
        super().insertFromMimeData(source)


# --------------------------------------------------------------------------- #
# one command row
# --------------------------------------------------------------------------- #
class CommandRow(QWidget):
    send = Signal(dict)
    edit = Signal(dict)
    remove = Signal(str)
    toggled = Signal(str, bool)
    moveTo = Signal(str, str)       # item id, target group id

    def __init__(self, item: dict, groups: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.groups = groups
        self.setObjectName("CmdRow")
        self._selection = float(bool(item.get("enabled", False)))
        self._selection_anim = QPropertyAnimation(self, b"selection", self)
        self._selection_anim.setDuration(150)
        self._selection_anim.setEasingCurve(QEasingCurve.OutCubic)
        note = str(item.get("note") or "").strip()
        self.setFixedHeight(60 if note else 40)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        top = QHBoxLayout()
        top.setContentsMargins(10, 4, 5, 4)
        top.setSpacing(6)
        outer.addLayout(top, 1)
        # Batch mode swaps the "enabled" switch for a neutral pick box: the same
        # square cannot mean "this command is active" and "this command is
        # selected" at once, and merging them would let a batch delete also
        # change which commands are enabled (R-092).
        self.pick = QCheckBox()
        self.pick.setToolTip(tr("cmd.batch.pick"))
        self.pick.hide()
        top.addWidget(self.pick)

        self.check = QCheckBox()
        self.check.setChecked(bool(item.get("enabled", False)))
        self.check.setToolTip(tr("cmd.enabled.tip"))
        self.check.toggled.connect(self._animate_selection)
        self.check.toggled.connect(lambda value: self.toggled.emit(item["id"], value))
        top.addWidget(self.check)

        text = item.get("text", "")
        display = f"[HEX] {text}" if item.get("hex") else text
        self.label = ElidedLabel(display)
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.label.setCursor(Qt.PointingHandCursor)
        details = [text, f"{item.get('delay', 0)} ms"]
        if note:
            details.append(note)
        self.setToolTip("\n".join(details))
        self.label.setToolTip(self.toolTip())
        self.label.mouseReleaseEvent = self._on_label_release
        top.addWidget(self.label, 1)
        if item.get("timer"):
            timer = QLabel(f"{item.get('interval', 1000)} ms")
            timer.setStyleSheet(f"color: {theme.AMBER}; font-size: 11px;")
            top.addWidget(timer)

        if note:
            self.note_label = ElidedLabel(note)
            self.note_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            self.note_label.setFixedHeight(16)
            self.note_label.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
            self.note_label.setToolTip(note)
            note_row = QHBoxLayout()
            note_row.setContentsMargins(10 + theme.CHECK_SIZE + 6, 0, 5, 4)
            note_row.addWidget(self.note_label)
            outer.addLayout(note_row)
            # The guess above is only a placeholder: the checkbox width is a
            # style hint, so the real x is measured once the row is laid out.
            self.note_row = note_row
            self._note_aligned = False

        for glyph, tip, slot in (
                (GlyphButton.SEND, tr("cmd.send"), lambda: self.send.emit(item)),
                (GlyphButton.EDIT, tr("cmd.edit"), lambda: self.edit.emit(item)),
                (GlyphButton.TRASH, tr("cmd.delete"), lambda: self.remove.emit(item["id"]))):
            button = GlyphButton(glyph, self, size=theme.ICON_SM)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            top.addWidget(button)

    def set_batch_mode(self, active: bool) -> None:
        """Show the pick box instead of the enable switch (R-092)."""
        self.pick.setVisible(active)
        self.check.setVisible(not active)
        if not active:
            self.pick.setChecked(False)

    def is_picked(self) -> bool:
        return self.pick.isVisible() and self.pick.isChecked()

    def _on_label_release(self, event) -> None:
        """Left click sends; right click only opens the context menu (R-095).

        The release handler used to fire for *every* button, so the right-click
        that should merely show "send / edit / move / delete" also transmitted
        the command - the user saw a command go out while only asking for the
        menu (L-127).
        """
        if event.button() == Qt.LeftButton:
            self.send.emit(self.item)

    def _align_note(self) -> None:
        """Start the note exactly under the command text (R-079).

        Runs once per row: the offset only depends on the checkbox width, and
        re-margining on every resize made a 200-command group four times slower
        to lay out (R-054).
        """
        row = getattr(self, "note_row", None)
        if row is None or getattr(self, "_note_aligned", True):
            return
        left = self.label.mapTo(self, QPoint(0, 0)).x()
        if left < 0:
            return
        margins = row.contentsMargins()
        if margins.left() != left:
            row.setContentsMargins(left, margins.top(), margins.right(),
                                   margins.bottom())
        self._note_aligned = True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._align_note()

    def _get_selection(self) -> float:
        return self._selection

    def _set_selection(self, value: float) -> None:
        self._selection = value
        self.update()

    selection = Property(float, _get_selection, _set_selection)

    def _animate_selection(self, enabled: bool) -> None:
        self._selection_anim.stop()
        self._selection_anim.setStartValue(self._selection)
        self._selection_anim.setEndValue(float(enabled))
        self._selection_anim.start()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        fill = QColor(theme.TEAL_SOFT)
        fill.setAlphaF(0.18 + 0.62 * self._selection)
        border = QColor(theme.TEAL)
        border.setAlphaF(0.12 + 0.20 * self._selection)
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                                theme.RADIUS_CTRL, theme.RADIUS_CTRL)
        accent = QColor(theme.TEAL if not self.item.get("timer") else theme.AMBER)
        accent.setAlphaF(0.18 + 0.82 * self._selection)
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(QRectF(1, 8, 3, self.height() - 16), 1.5, 1.5)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction(tr("cmd.send"), lambda: self.send.emit(self.item))
        menu.addAction(tr("cmd.edit"), lambda: self.edit.emit(self.item))
        current = self.item.get("group") or ""
        if self.groups:
            submenu = menu.addMenu(tr("cmd.group"))
            for group in self.groups:
                action = submenu.addAction(group.get("name") or group["id"])
                action.setCheckable(True)
                action.setChecked(group["id"] == current)
                action.triggered.connect(
                    lambda _checked=False, gid=group["id"]:
                    self.moveTo.emit(self.item["id"], gid))
            if current:
                submenu.addSeparator()
                submenu.addAction("— " + tr("cmd.ungrouped"),
                                  lambda: self.moveTo.emit(self.item["id"], ""))
        menu.addSeparator()
        menu.addAction(tr("cmd.delete"), lambda: self.remove.emit(self.item["id"]))
        menu.exec(event.globalPos())


# --------------------------------------------------------------------------- #
# group header
# --------------------------------------------------------------------------- #
class GroupHeader(QWidget):
    toggled = Signal(str)
    rename = Signal(str)
    exportGroup = Signal(str)
    remove = Signal(str)
    addCommand = Signal(str)

    def __init__(self, group: dict, count: int, parent=None) -> None:
        super().__init__(parent)
        self.group = group
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 5, 2, 1)
        layout.setSpacing(5)

        self.chevron = GlyphButton(
            GlyphButton.CHEVRON_RIGHT if group.get("collapsed") else GlyphButton.CHEVRON_DOWN,
            self, size=20)
        self.chevron.setToolTip(tr("cmd.collapse"))
        self.chevron.clicked.connect(lambda: self.toggled.emit(group["id"]))
        layout.addWidget(self.chevron)

        self.label = QLabel(group.get("name") or group["id"])
        self.label.setStyleSheet("color: #121A2B; font-size: 12px; font-weight: 650;")
        self.label.setCursor(Qt.PointingHandCursor)
        self.label.mouseReleaseEvent = self._on_label_release
        layout.addWidget(self.label)

        layout.addWidget(Badge(str(count)))
        layout.addStretch(1)

        note = str(group.get("note") or "").strip()
        if note:
            info = GlyphButton(GlyphButton.INFO, self, size=20)
            info.setToolTip(note)
            layout.addWidget(info)

        self.add_btn = GlyphButton(GlyphButton.PLUS, self, size=20)
        self.add_btn.setToolTip(tr("cmd.addtogroup"))
        self.add_btn.clicked.connect(lambda: self.addCommand.emit(group["id"]))
        layout.addWidget(self.add_btn)

        self.edit_btn = GlyphButton(GlyphButton.EDIT, self, size=20)
        self.edit_btn.setToolTip(tr("cmd.edit"))
        self.edit_btn.clicked.connect(lambda: self.rename.emit(group["id"]))
        layout.addWidget(self.edit_btn)

    def _on_label_release(self, event) -> None:
        """Only a left click collapses the group; the right one opens the menu."""
        if event.button() == Qt.LeftButton:
            self.toggled.emit(self.group["id"])

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction(tr("cmd.addtogroup"),
                       lambda: self.addCommand.emit(self.group["id"]))
        menu.addAction(tr("cmd.dialog.exportgroup"),
                       lambda: self.exportGroup.emit(self.group["id"]))
        menu.addAction(tr("cmd.edit"), lambda: self.rename.emit(self.group["id"]))
        menu.addSeparator()
        menu.addAction(tr("cmd.delete"), lambda: self.remove.emit(self.group["id"]))
        menu.exec(event.globalPos())


# --------------------------------------------------------------------------- #
# dialogs
# --------------------------------------------------------------------------- #
class CommandDialog(QDialog):
    """Create or edit one command, including its note and group."""

    def __init__(self, parent, item: dict | None = None,
                 groups: list[dict] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("cmd.dialog.edittitle") if item else tr("cmd.dialog.title"))
        self.setModal(True)
        self.setMinimumWidth(440)
        self.item = dict(item) if item else {}
        self.groups = groups or []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)

        self.text = QPlainTextEdit()
        self.text.setPlainText(self.item.get("text", ""))
        self.text.setFixedHeight(84)
        layout.addWidget(self._field("AT", self.text))

        self.note = QLineEdit(self.item.get("note", ""))
        self.note.setPlaceholderText(tr("cmd.note"))
        layout.addWidget(self._field(tr("cmd.note"), self.note))

        self.group_box = Combo()
        self.group_box.addItem("— " + tr("cmd.ungrouped"), "")
        for group in self.groups:
            self.group_box.addItem(group.get("name") or group["id"], group["id"])
        index = self.group_box.findData(self.item.get("group") or "")
        self.group_box.setCurrentIndex(max(0, index))
        layout.addWidget(self._field(tr("cmd.group"), self.group_box))

        options = QHBoxLayout()
        options.setSpacing(8)
        self.line_end = Combo()
        for key, label in (("none", "—"), ("cr", "\\r"), ("lf", "\\n"), ("crlf", "\\r\\n")):
            self.line_end.addItem(label, key)
        self.line_end.setCurrentIndex(
            max(0, self.line_end.findData(self.item.get("line_end", "crlf"))))
        options.addWidget(self._field("Line end", self.line_end))

        self.delay = QSpinBox()
        self.delay.setRange(0, 60000)
        self.delay.setSingleStep(50)
        self.delay.setValue(int(self.item.get("delay", 120)))
        options.addWidget(self._field("Delay (ms)", self.delay))
        layout.addLayout(options)

        flags = QHBoxLayout()
        self.hex = QCheckBox("HEX")
        self.hex.setChecked(bool(self.item.get("hex")))
        flags.addWidget(self.hex)
        self.enabled = QCheckBox(tr("cmd.enabled.tip"))
        self.enabled.setChecked(bool(self.item.get("enabled", False)))
        flags.addWidget(self.enabled)
        flags.addStretch(1)
        layout.addLayout(flags)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton(tr("cmd.dialog.cancel"))
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton(tr("cmd.dialog.save") if item else tr("cmd.dialog.add"))
        save.setObjectName("Primary")
        save.clicked.connect(self.accept)
        footer.addWidget(cancel)
        footer.addWidget(save)
        layout.addLayout(footer)

    def _field(self, label: str, widget: QWidget) -> QWidget:
        """A caption above a control, wrapped in a real widget.

        It used to return a ``QVBoxLayout`` while every call site passed it to
        ``addWidget()``.  Opening the add/edit dialog therefore raised
        ``TypeError: addWidget called with wrong argument types`` and the edit
        button looked dead (L-051).
        """
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        caption = QLabel(label)
        caption.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        layout.addWidget(caption)
        layout.addWidget(widget)
        return box

    def result_item(self) -> dict:
        return {
            "text": self.text.toPlainText().strip(),
            "note": self.note.text().strip(),
            "group": self.group_box.currentData() or "",
            "line_end": self.line_end.currentData(),
            "delay": self.delay.value(),
            "hex": self.hex.isChecked(),
            "enabled": self.enabled.isChecked(),
        }


class GroupDialog(QDialog):
    """Create or rename a group (name + note)."""

    def __init__(self, parent, group: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("cmd.addgroup") if not group else tr("cmd.edit"))
        self.setModal(True)
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)

        self.name = QLineEdit((group or {}).get("name", ""))
        self.name.setPlaceholderText(tr("cmd.group.name"))
        layout.addWidget(self.name)

        self.note = QPlainTextEdit()
        self.note.setFixedHeight(76)
        self.note.setPlaceholderText(tr("cmd.note"))
        self.note.setPlainText((group or {}).get("note", ""))
        layout.addWidget(self.note)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton(tr("cmd.dialog.cancel"))
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr("cmd.dialog.save"))
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        footer.addWidget(cancel)
        footer.addWidget(ok)
        layout.addLayout(footer)

    def result_group(self) -> dict:
        return {"name": self.name.text().strip() or tr("cmd.group"),
                "note": self.note.toPlainText().strip()}


class ExportDialog(QDialog):
    """Pick which commands (whole list or one group) to export."""

    def __init__(self, parent, store: CommandStore, preset_group: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("cmd.dialog.export"))
        self.setModal(True)
        self.setMinimumSize(430, 470)
        self.store = store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)

        scope = QHBoxLayout()
        scope.addWidget(QLabel(tr("cmd.group")))
        self.scope = Combo()
        self.scope.addItem("— " + tr("cmd.title"), "")
        for group in store.groups:
            self.scope.addItem(group.get("name") or group["id"], group["id"])
        index = self.scope.findData(preset_group) if preset_group else 0
        self.scope.setCurrentIndex(max(0, index))
        self.scope.currentIndexChanged.connect(self._rebuild)
        scope.addWidget(self.scope, 1)
        layout.addLayout(scope)

        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.list_host)
        layout.addWidget(scroll, 1)

        tools = QHBoxLayout()
        select_all = QPushButton(tr("cmd.dialog.selectall"))
        select_all.setObjectName("Ghost")
        select_all.clicked.connect(lambda: self._check_all(True))
        select_none = QPushButton(tr("cmd.dialog.selectnone"))
        select_none.setObjectName("Ghost")
        select_none.clicked.connect(lambda: self._check_all(False))
        tools.addWidget(select_all)
        tools.addWidget(select_none)
        tools.addStretch(1)
        self.count = QLabel("")
        self.count.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        tools.addWidget(self.count)
        layout.addLayout(tools)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton(tr("cmd.dialog.cancel"))
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr("cmd.export"))
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        footer.addWidget(cancel)
        footer.addWidget(ok)
        layout.addLayout(footer)

        self.checks: list[tuple[QCheckBox, dict]] = []
        self._rebuild()

    def _rebuild(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.checks.clear()

        group_id = self.scope.currentData() or ""
        for item in self.store.items:
            if group_id and (item.get("group") or "") != group_id:
                continue
            box = QCheckBox(item.get("text", ""))
            box.setChecked(True)
            if item.get("note"):
                box.setToolTip(item["note"])
            self.list_layout.addWidget(box)
            self.checks.append((box, item))
        self.list_layout.addStretch(1)
        self._update_count()

    def _check_all(self, value: bool) -> None:
        for box, _item in self.checks:
            box.setChecked(value)
        self._update_count()

    def _update_count(self) -> None:
        chosen = sum(1 for box, _item in self.checks if box.isChecked())
        self.count.setText(f"{chosen} / {len(self.checks)}")

    def selected(self) -> list[dict]:
        return [item for box, item in self.checks if box.isChecked()]


# --------------------------------------------------------------------------- #
# the panel
# --------------------------------------------------------------------------- #
class CommandsPanel(QWidget):
    """Collapsible command list with groups, notes and import/export."""

    sendRequested = Signal(bytes, str)

    def __init__(self, store: CommandStore, notify: Callable[[str, str], None],
                 parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.notify = notify
        self.timers: dict[str, QTimer] = {}
        self._collapsed = False
        self._batch = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = self._build_header()
        root.addWidget(self.header)

        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(9, 8, 10, 10)
        body_layout.setSpacing(7)

        self.drawer = self._build_drawer()
        body_layout.addWidget(self.drawer)
        self.drawer.hide()

        self.batch_bar = self._build_batch_bar()
        body_layout.addWidget(self.batch_bar)
        self.batch_bar.hide()

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(5)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        body_layout.addWidget(self.scroll, 1)

        self.empty = QLabel(tr("empty.commands"))
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        body_layout.addWidget(self.empty)

        root.addWidget(self.body, 1)

        self.store.changed.connect(self.rebuild)
        self.rebuild()

    # ------------------------------------------------------------------ header
    def _build_header(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(5, 6, 6, 6)
        layout.setSpacing(1)

        self.chevron = GlyphButton(GlyphButton.CHEVRON_DOWN, self, size=theme.ICON_MD)
        self.chevron.setToolTip(tr("cmd.collapse"))
        self.chevron.clicked.connect(self.toggle_collapsed)
        layout.addWidget(self.chevron)

        self.title = QLabel(tr("cmd.title"))
        self.title.setStyleSheet(f"color: {theme.INK2}; font-size: 12px; font-weight: 600;")
        layout.addWidget(self.title)

        self.count = Badge("0")
        layout.addWidget(self.count)
        layout.addStretch(1)

        self.btn_paste = self._tool(GlyphButton.PASTE, tr("cmd.paste"))
        self.btn_paste.clicked.connect(self.show_drawer)
        self.btn_group = self._tool(GlyphButton.FOLDER, tr("cmd.addgroup"))
        self.btn_group.clicked.connect(self.new_group)
        self.btn_import = self._tool(GlyphButton.INBOX, tr("cmd.import"))
        self.btn_import.clicked.connect(self.import_file)
        self.btn_export = self._tool(GlyphButton.UPLOAD, tr("cmd.export"))
        self.btn_export.clicked.connect(lambda: self.export_dialog(""))
        self.btn_add = self._tool(GlyphButton.PLUS, tr("cmd.add"))
        self.btn_add.clicked.connect(lambda: self.open_editor(None))
        self.btn_batch = self._tool(GlyphButton.SELECT, tr("cmd.batch"))
        self.btn_batch.clicked.connect(self.toggle_batch)
        for button in (self.btn_paste, self.btn_group, self.btn_import,
                       self.btn_export, self.btn_add, self.btn_batch):
            layout.addWidget(button)

        header.setObjectName("SideHeader")
        header.setStyleSheet(
            "QWidget#SideHeader { background: transparent; border: 0;"
            f" border-bottom: 1px solid {theme.LINE_SOFT}; }}")
        return header

    def _tool(self, glyph: str, tip: str) -> GlyphButton:
        button = GlyphButton(glyph, self, size=theme.ICON_SM)
        button.setToolTip(tip)
        return button

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self.body.setVisible(not self._collapsed)
        self.chevron.set_glyph(
            GlyphButton.CHEVRON_RIGHT if self._collapsed else GlyphButton.CHEVRON_DOWN)
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "relayout_sidebar"):
            parent = parent.parentWidget()
        if parent is not None:
            parent.relayout_sidebar()
        else:
            self.updateGeometry()

    # ------------------------------------------------------------------ drawer
    def _build_drawer(self) -> QWidget:
        drawer = QWidget()
        drawer.setObjectName("PasteDrawer")
        drawer.setStyleSheet(
            f"QWidget#PasteDrawer {{ background: {theme.WASH};"
            f" border: 1px dashed {theme.LINE_STRONG};"
            f" border-radius: {theme.RADIUS_CARD}px; }}")
        layout = QVBoxLayout(drawer)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.paste_box = PasteBox()
        self.paste_box.setPlaceholderText(tr("cmd.paste"))
        self.paste_box.setFixedHeight(92)
        self.paste_box.pasted.connect(self._on_paste)
        layout.addWidget(self.paste_box)

        # Where the pasted commands land (R-093).  Without this the drawer could
        # only import into "ungrouped" and the user had to move them afterwards.
        group_row = QHBoxLayout()
        group_row.setSpacing(6)
        self.paste_group_label = QLabel(tr("cmd.paste.group"))
        self.paste_group_label.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        group_row.addWidget(self.paste_group_label)
        self.paste_group = Combo()
        self.paste_group.setToolTip(tr("cmd.paste.group.tip"))
        group_row.addWidget(self.paste_group, 1)
        layout.addLayout(group_row)

        row = QHBoxLayout()
        self.paste_hint = QLabel("")
        self.paste_hint.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        row.addWidget(self.paste_hint, 1)
        self.btn_cancel = QPushButton(tr("cmd.dialog.cancel"))
        self.btn_cancel.setObjectName("Ghost")
        self.btn_cancel.clicked.connect(self.hide_drawer)
        self.btn_confirm = QPushButton(tr("cmd.dialog.confirmimport"))
        self.btn_confirm.setObjectName("Primary")
        self.btn_confirm.clicked.connect(
            lambda: self.import_text(self.paste_box.toPlainText(),
                                     self.paste_group.currentData() or ""))
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_confirm)
        layout.addLayout(row)
        return drawer

    def _build_batch_bar(self) -> QWidget:
        """Selection tools: pick many, move them together, delete them together."""
        bar = QWidget()
        bar.setObjectName("BatchBar")
        bar.setStyleSheet(
            f"QWidget#BatchBar {{ background: {theme.WASH};"
            f" border: 1px solid {theme.LINE};"
            f" border-radius: {theme.RADIUS_CARD}px; }}")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.batch_count = QLabel(tr("cmd.batch.count", n=0))
        self.batch_count.setStyleSheet(f"color: {theme.INK2}; font-size: 11.5px;")
        head.addWidget(self.batch_count, 1)
        self.btn_pick_all = QPushButton(tr("cmd.batch.all"))
        self.btn_pick_all.setObjectName("Ghost")
        self.btn_pick_all.clicked.connect(lambda: self._pick_all(True))
        self.btn_pick_none = QPushButton(tr("cmd.batch.none"))
        self.btn_pick_none.setObjectName("Ghost")
        self.btn_pick_none.clicked.connect(lambda: self._pick_all(False))
        self.btn_batch_exit = QPushButton(tr("cmd.batch.exit"))
        self.btn_batch_exit.setObjectName("Ghost")
        self.btn_batch_exit.clicked.connect(lambda: self.set_batch_mode(False))
        for button in (self.btn_pick_all, self.btn_pick_none, self.btn_batch_exit):
            button.setCursor(Qt.PointingHandCursor)
            head.addWidget(button)
        layout.addLayout(head)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.batch_group = Combo()
        self.batch_group.setToolTip(tr("cmd.paste.group.tip"))
        tools.addWidget(self.batch_group, 1)
        self.btn_batch_move = QPushButton(tr("cmd.batch.move"))
        self.btn_batch_move.setObjectName("Ghost")
        self.btn_batch_move.clicked.connect(self.batch_move)
        tools.addWidget(self.btn_batch_move)
        self.btn_batch_delete = QPushButton(tr("cmd.batch.delete"))
        self.btn_batch_delete.setObjectName("Danger")
        self.btn_batch_delete.clicked.connect(self.batch_delete)
        tools.addWidget(self.btn_batch_delete)
        for button in (self.btn_batch_move, self.btn_batch_delete):
            button.setCursor(Qt.PointingHandCursor)
        layout.addLayout(tools)
        return bar

    # ------------------------------------------------------------ batch mode
    def rows(self) -> list[CommandRow]:
        return list(self.list_host.findChildren(CommandRow))

    def set_batch_mode(self, active: bool) -> None:
        """Swap every row's enable switch for a pick box (R-092)."""
        self._batch = bool(active)
        self.batch_bar.setVisible(self._batch)
        self.btn_batch.set_glyph(GlyphButton.CLOSE if self._batch
                                 else GlyphButton.SELECT)
        if self._batch:
            self.hide_drawer()
            self._refresh_batch_groups()
        for row in self.rows():
            row.set_batch_mode(self._batch)
        self._sync_batch_count()

    def toggle_batch(self) -> None:
        self.set_batch_mode(not self._batch)

    def row_reachable(self, row: CommandRow) -> bool:
        """True when the row is actually on screen.

        ``isHidden()`` is False for a row whose *group holder* was hidden, so a
        collapsed group would otherwise be silently included in "全选" and then
        moved or deleted without ever being visible (R-092 / L-124).
        """
        widget: QWidget | None = row
        while widget is not None and widget is not self:
            if widget.isHidden():
                return False
            widget = widget.parentWidget()
        return True

    def picked_rows(self) -> list[CommandRow]:
        """Picked and visible rows only."""
        return [row for row in self.rows()
                if self.row_reachable(row) and row.is_picked()]

    def picked_ids(self) -> list[str]:
        return [row.item["id"] for row in self.picked_rows()]

    def _sync_batch_count(self, *_args) -> None:
        chosen = len(self.picked_ids())
        self.batch_count.setText(tr("cmd.batch.count", n=chosen))
        for button in (self.btn_batch_move, self.btn_batch_delete):
            button.setEnabled(chosen > 0)

    def _pick_all(self, value: bool) -> None:
        for row in self.rows():
            if self.row_reachable(row):
                row.pick.setChecked(value)
        self._sync_batch_count()

    def _refresh_batch_groups(self) -> None:
        current = self.batch_group.currentData() or ""
        self.batch_group.blockSignals(True)
        self.batch_group.clear()
        self.batch_group.addItem(tr("cmd.paste.nogroup"), "")
        for group in self.store.groups:
            self.batch_group.addItem(group.get("name") or group["id"], group["id"])
        index = self.batch_group.findData(current)
        self.batch_group.setCurrentIndex(index if index >= 0 else 0)
        self.batch_group.blockSignals(False)

    def batch_move(self) -> None:
        ids = self.picked_ids()
        if not ids:
            return
        group_id = self.batch_group.currentData() or ""
        self.store.move_many(ids, group_id)
        group = self.store.group_by_id(group_id)
        name = (group or {}).get("name") or tr("cmd.ungrouped")
        self.notify(tr("cmd.batch.moved", n=len(ids), name=name), "ok")

    def batch_delete(self) -> None:
        ids = self.picked_ids()
        if not ids:
            return
        removed = self.store.remove_many(ids)
        self.notify(tr("cmd.batch.deleted", n=removed), "ok")

    def show_drawer(self) -> None:
        self.drawer.setVisible(not self.drawer.isVisible())
        if self.drawer.isVisible():
            self._refresh_paste_groups()
            self.paste_box.clear()
            self.paste_box.setFocus()

    def _refresh_paste_groups(self) -> None:
        """Rebuild the target-group list; keeps the current choice if it still exists."""
        current = self.paste_group.currentData() or ""
        self.paste_group.blockSignals(True)
        self.paste_group.clear()
        self.paste_group.addItem(tr("cmd.paste.nogroup"), "")
        for group in self.store.groups:
            self.paste_group.addItem(group.get("name") or group["id"], group["id"])
        index = self.paste_group.findData(current)
        self.paste_group.setCurrentIndex(index if index >= 0 else 0)
        self.paste_group.blockSignals(False)

    def hide_drawer(self) -> None:
        self.paste_box.clear()
        self.paste_hint.setText("")
        self.drawer.hide()

    def _on_paste(self, text: str) -> None:
        if len(self.store.split_lines(text)) > 1:
            self.paste_box.clear()
            self.import_text(text, self.paste_group.currentData() or "")

    def import_text(self, raw: str, group_id: str = "") -> None:
        lines = self.store.split_lines(raw)
        if not lines:
            return
        self.store.add(lines, {"group": group_id} if group_id else None)
        self.hide_drawer()
        self.notify(tr("cmd.imported", n=len(lines)), "ok")

    # ------------------------------------------------------------------- rows
    def rebuild(self) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Detach first: deleteLater() keeps the child visible to
                # findChildren()/the layout until the event loop runs.
                widget.setParent(None)
                widget.deleteLater()

        # ``_group_rows`` lets a collapse flip visibility instead of rebuilding
        # the whole list.  Rebuilding 20+ rows (each with its own buttons) on
        # every chevron click was the visible stutter (R-041).
        self._group_rows: dict[str, list[QWidget]] = {}
        self._group_bodies: dict[str, QWidget] = {}
        self._group_headers: dict[str, GroupHeader] = {}
        for group, items in self.store.groups_with_items():
            if group is not None:
                header = GroupHeader(group, len(items))
                header.toggled.connect(self.toggle_group)
                header.rename.connect(self.edit_group)
                header.exportGroup.connect(self.export_dialog)
                header.remove.connect(self.remove_group)
                header.addCommand.connect(self.add_to_group)
                self.list_layout.insertWidget(self.list_layout.count() - 1, header)
                self._group_rows[group["id"]] = []
                self._group_headers[group["id"]] = header
                holder = QWidget()
                holder_layout = QVBoxLayout(holder)
                holder_layout.setContentsMargins(18, 0, 0, 0)
                holder_layout.setSpacing(5)
                self.list_layout.insertWidget(self.list_layout.count() - 1, holder)
                holder.setVisible(not group.get("collapsed"))
                self._group_bodies[group["id"]] = holder
            for entry in items:
                row = CommandRow(entry, self.store.groups)
                row.send.connect(self.send_item)
                row.edit.connect(self.open_editor)
                row.remove.connect(self.remove_item)
                row.toggled.connect(self.set_enabled)
                row.moveTo.connect(self.move_item)
                row.pick.toggled.connect(self._sync_batch_count)
                if group is None:
                    self.list_layout.insertWidget(self.list_layout.count() - 1, row)
                    continue
                self._group_bodies[group["id"]].layout().addWidget(row)
                self._group_rows[group["id"]].append(row)

        if self._batch:
            # A rebuild creates new rows, which start un-picked and show the
            # enable switch; the mode has to be re-applied to them (R-092).
            for row in self.rows():
                row.set_batch_mode(True)
            self._sync_batch_count()

        count = len(self.store.items)
        self.count.setText(str(count))
        self.count.setVisible(count > 0)
        self.empty.setVisible(count == 0)
        self.scroll.setVisible(count > 0)
        self.sync_timers()

    # ---------------------------------------------------------------- groups
    def toggle_group(self, group_id: str) -> None:
        group = self.store.group_by_id(group_id)
        if group is None:
            return
        collapsed = not group.get("collapsed")
        self.store.update_group(group_id, {"collapsed": collapsed}, notify=False)
        self._group_bodies[group_id].setVisible(not collapsed)
        header = self._group_headers[group_id]
        header.chevron.set_glyph(GlyphButton.CHEVRON_RIGHT if collapsed
                                else GlyphButton.CHEVRON_DOWN)

    def add_to_group(self, group_id: str) -> None:
        """Create a command that lands directly inside ``group_id`` (R-042)."""
        self.open_editor(None, group_id)

    def move_item(self, item_id: str, group_id: str) -> None:
        self.store.move_to_group(item_id, group_id)
        name = ""
        group = self.store.group_by_id(group_id)
        if group is not None:
            name = group.get("name") or group_id
        self.notify(tr("cmd.moved", name=name or tr("cmd.ungrouped")), "ok")

    # ------------------------------------------------------------------- actions
    def new_group(self) -> None:
        dialog = GroupDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        data = dialog.result_group()
        try:
            self.store.add_group(data["name"], data["note"])
        except ValueError as exc:
            self.notify(str(exc), "warn")

    def edit_group(self, group_id: str) -> None:
        group = self.store.group_by_id(group_id)
        if group is None:
            return
        dialog = GroupDialog(self, group)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.store.update_group(group_id, dialog.result_group())
        except ValueError as exc:
            self.notify(str(exc), "warn")

    def remove_group(self, group_id: str) -> None:
        menu = QMenu(self)
        keep = menu.addAction(tr("cmd.group.keep"))
        drop = menu.addAction(tr("cmd.group.drop"))
        chosen = menu.exec(self.cursor().pos())
        if chosen is keep:
            self.store.remove_group(group_id, drop_commands=False)
        elif chosen is drop:
            self.store.remove_group(group_id, drop_commands=True)

    # -------------------------------------------------------------- transfer
    def export_dialog(self, group_id: str = "") -> None:
        if not self.store.items:
            self.notify(tr("empty.commands"), "warn")
            return
        dialog = ExportDialog(self, self.store, group_id)
        if dialog.exec() != QDialog.Accepted:
            return
        chosen = dialog.selected()
        if not chosen:
            return
        default = os.path.join(
            paths.data_dir(), time.strftime("omnicom-commands-%Y%m%d-%H%M%S.json"))
        path, _ = QFileDialog.getSaveFileName(self, tr("cmd.dialog.export"), default,
                                              FILE_FILTER)
        if not path:
            return
        try:
            count = self.store.export_to(path, chosen)
        except OSError as exc:
            self.notify(tr("cmd.importfail", err=exc), "error")
            return
        self.notify(tr("cmd.exported", n=count, name=os.path.basename(path)), "ok")

    def export_one(self, item: dict) -> None:
        safe = "".join(char for char in str(item.get("text", "cmd"))[:20]
                       if char.isalnum()) or "cmd"
        default = os.path.join(paths.data_dir(), f"omnicom-cmd-{safe}.json")
        path, _ = QFileDialog.getSaveFileName(self, tr("cmd.dialog.exportsingle"),
                                              default, FILE_FILTER)
        if not path:
            return
        try:
            count = self.store.export_to(path, [item])
        except OSError as exc:
            self.notify(tr("cmd.importfail", err=exc), "error")
            return
        self.notify(tr("cmd.exported", n=count, name=os.path.basename(path)), "ok")

    def import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("cmd.import"),
                                              paths.data_dir(), FILE_FILTER)
        if not path:
            return
        try:
            count = self.store.import_from(path)
        except (OSError, ValueError) as exc:
            self.notify(tr("cmd.importfail", err=exc), "error")
            return
        self.notify(tr("cmd.imported", n=count), "ok")

    # ----------------------------------------------------------------- timers
    def sync_timers(self) -> None:
        live = {item["id"]: item for item in self.store.items if item.get("timer")}
        for item_id in list(self.timers):
            if item_id not in live:
                self.timers.pop(item_id).stop()
        for item_id, item in live.items():
            interval = max(20, int(item.get("interval", 1000)))
            timer = self.timers.get(item_id)
            if timer is None:
                timer = QTimer(self)
                timer.timeout.connect(lambda iid=item_id: self._fire(iid))
                self.timers[item_id] = timer
            timer.start(interval)

    def _fire(self, item_id: str) -> None:
        for item in self.store.items:
            if item["id"] == item_id:
                self.send_item(item)
                return

    # ---------------------------------------------------------------- actions

    def send_item(self, item: dict) -> None:
        payload = payload_of(item)
        if not payload:
            self.notify(tr("empty.commands"), "warn")
            return
        description = f"[HEX] {item['text']}" if item.get("hex") else item.get("text", "")
        self.sendRequested.emit(payload, description)

    def send_all(self) -> None:
        items = self.store.enabled_items()
        if not items:
            # New commands start unticked, so "I added one and nothing
            # happened" is now the default confusion - say what to do (R-127).
            self.notify(tr("cmd.sendall.none"), "warn")
            return
        self._queue = list(items)
        self._send_next()

    def _send_next(self) -> None:
        if not getattr(self, "_queue", None):
            return
        item = self._queue.pop(0)
        self.send_item(item)
        QTimer.singleShot(max(0, int(item.get("delay", 0))), self._send_next)

    def remove_item(self, item_id: str) -> None:
        timer = self.timers.pop(item_id, None)
        if timer:
            timer.stop()
        self.store.remove(item_id)

    def set_enabled(self, item_id: str, enabled: bool) -> None:
        self.store.update(item_id, {"enabled": enabled}, notify=False)
        if not hasattr(self, "_selection_save"):
            self._selection_save = QTimer(self)
            self._selection_save.setSingleShot(True)
            self._selection_save.setInterval(250)
            self._selection_save.timeout.connect(self.store.flush)
        self._selection_save.start()

    def open_editor(self, item: dict | None, group_id: str = "") -> None:
        preset: dict | None = None
        if group_id and group_id not in ("", UNGROUPED):
            preset = {"group": group_id}
        dialog = CommandDialog(self, item or preset, self.store.groups)
        if dialog.exec() != QDialog.Accepted:
            return
        patch = dialog.result_item()
        if not patch["text"]:
            self.notify(tr("empty.commands"), "warn")
            return
        if item:
            self.store.update(item["id"], patch)
        else:
            self.store.add([patch["text"]], patch)

    # ------------------------------------------------------------ retranslate
    def retranslate(self) -> None:
        self.title.setText(tr("cmd.title"))
        self.chevron.setToolTip(tr("cmd.collapse"))
        self.btn_paste.setToolTip(tr("cmd.paste"))
        self.btn_add.setToolTip(tr("cmd.add"))
        self.btn_group.setToolTip(tr("cmd.addgroup"))
        self.btn_import.setToolTip(tr("cmd.import"))
        self.btn_export.setToolTip(tr("cmd.export"))
        self.btn_batch.setToolTip(tr("cmd.batch"))
        self.btn_pick_all.setText(tr("cmd.batch.all"))
        self.btn_pick_none.setText(tr("cmd.batch.none"))
        self.btn_batch_exit.setText(tr("cmd.batch.exit"))
        self.btn_batch_move.setText(tr("cmd.batch.move"))
        self.btn_batch_delete.setText(tr("cmd.batch.delete"))
        self.batch_group.setToolTip(tr("cmd.paste.group.tip"))
        self.paste_group_label.setText(tr("cmd.paste.group"))
        self.paste_group.setToolTip(tr("cmd.paste.group.tip"))
        self.paste_box.setPlaceholderText(tr("cmd.paste"))
        self.btn_cancel.setText(tr("cmd.dialog.cancel"))
        self.btn_confirm.setText(tr("cmd.dialog.confirmimport"))
        self.empty.setText(tr("empty.commands"))
        self.rebuild()
