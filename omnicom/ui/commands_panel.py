"""Right-sidebar command list: groups, notes, per-item and per-group export."""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..commands import CommandStore, payload_for
from ..i18n import tr
from . import theme
from .widgets import Badge, Combo, GlyphButton

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
    exportOne = Signal(dict)
    moveTo = Signal(str, str)       # item id, target group id

    def __init__(self, item: dict, groups: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.groups = groups
        self.setObjectName("CmdRow")
        self._apply_style(item.get("enabled", True))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(9, 6, 7, 6)
        outer.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(7)
        self.check = QCheckBox()
        self.check.setChecked(bool(item.get("enabled", True)))
        self.check.setToolTip(tr("cmd.enabled.tip"))
        self.check.toggled.connect(lambda value: self.toggled.emit(item["id"], value))
        top.addWidget(self.check)

        text = item.get("text", "")
        display = f"[HEX] {text}" if item.get("hex") else text
        self.label = ElidedLabel(display)
        self.label.setCursor(Qt.PointingHandCursor)
        self.label.setToolTip(display)
        self.label.mouseReleaseEvent = lambda _event: self.send.emit(item)
        top.addWidget(self.label, 1)
        outer.addLayout(top)

        note = str(item.get("note") or "").strip()
        if note:
            self.note_label = QLabel(note)
            self.note_label.setWordWrap(True)
            self.note_label.setStyleSheet(
                f"color: {theme.DIM}; font-size: 11px; padding-left: 26px;")
            outer.addWidget(self.note_label)

        bottom = QHBoxLayout()
        bottom.setSpacing(5)
        delay = QLabel(f"{item.get('delay', 0)}ms")
        delay.setObjectName("Badge")
        bottom.addWidget(delay)
        if item.get("timer"):
            timer = QLabel(f"↻{item.get('interval', 1000)}ms")
            timer.setObjectName("BadgeWarn")
            bottom.addWidget(timer)
        if item.get("hex"):
            badge = QLabel("HEX")
            badge.setObjectName("Badge")
            bottom.addWidget(badge)
        bottom.addStretch(1)

        for glyph, tip, slot in (
                (GlyphButton.SEND, tr("cmd.send"), lambda: self.send.emit(item)),
                (GlyphButton.EDIT, tr("cmd.edit"), lambda: self.edit.emit(item)),
                (GlyphButton.SAVE, tr("cmd.export"), lambda: self.exportOne.emit(item)),
                (GlyphButton.TRASH, tr("cmd.delete"), lambda: self.remove.emit(item["id"]))):
            button = GlyphButton(glyph, self, size=theme.ICON_SM)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            bottom.addWidget(button)
        outer.addLayout(bottom)

    def _apply_style(self, enabled: bool) -> None:
        border = "rgba(11, 122, 110, 110)" if enabled else theme.LINE
        fill = theme.TEAL_SOFT if enabled else theme.CARD
        hover = "#F0FBF9" if enabled else theme.WASH
        self.setStyleSheet(
            f"QWidget#CmdRow {{ background: {fill}; border: 1px solid {border};"
            f" border-radius: {theme.RADIUS_CTRL}px; }}"
            f"QWidget#CmdRow:hover {{ background: {hover};"
            f" border-color: {theme.LINE_STRONG}; }}")

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction(tr("cmd.send"), lambda: self.send.emit(self.item))
        menu.addAction(tr("cmd.edit"), lambda: self.edit.emit(self.item))
        menu.addAction(tr("cmd.export"), lambda: self.exportOne.emit(self.item))
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
        self.label.mouseReleaseEvent = lambda _event: self.toggled.emit(group["id"])
        layout.addWidget(self.label)

        layout.addWidget(Badge(str(count)))
        layout.addStretch(1)

        note = str(group.get("note") or "").strip()
        if note:
            info = QLabel("ⓘ")
            info.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
            info.setToolTip(note)
            layout.addWidget(info)

        self.export_btn = GlyphButton(GlyphButton.SAVE, self, size=20)
        self.export_btn.setToolTip(tr("cmd.dialog.exportgroup"))
        self.export_btn.clicked.connect(lambda: self.exportGroup.emit(group["id"]))
        layout.addWidget(self.export_btn)

        self.edit_btn = GlyphButton(GlyphButton.EDIT, self, size=20)
        self.edit_btn.setToolTip(tr("cmd.edit"))
        self.edit_btn.clicked.connect(lambda: self.rename.emit(group["id"]))
        layout.addWidget(self.edit_btn)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
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
        self.enabled.setChecked(bool(self.item.get("enabled", True)))
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

    def _field(self, label: str, widget: QWidget) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(4)
        caption = QLabel(label)
        caption.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        box.addWidget(caption)
        box.addWidget(widget)
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
        for button in (self.btn_paste, self.btn_group, self.btn_import,
                       self.btn_export, self.btn_add):
            layout.addWidget(button)

        header.setObjectName("SideHeader")
        header.setStyleSheet(
            "QWidget#SideHeader { background: transparent; border: 0;"
            f" border-bottom: 1px solid {theme.LINE_SOFT}; }}")
        return header

    def _tool(self, glyph: str, tip: str) -> GlyphButton:
        button = GlyphButton(glyph, self, size=theme.ICON_MD)
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
            lambda: self.import_text(self.paste_box.toPlainText()))
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_confirm)
        layout.addLayout(row)
        return drawer

    def show_drawer(self) -> None:
        self.drawer.setVisible(not self.drawer.isVisible())
        if self.drawer.isVisible():
            self.paste_box.clear()
            self.paste_box.setFocus()

    def hide_drawer(self) -> None:
        self.paste_box.clear()
        self.paste_hint.setText("")
        self.drawer.hide()

    def _on_paste(self, text: str) -> None:
        if len(self.store.split_lines(text)) > 1:
            self.paste_box.clear()
            self.import_text(text)

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

        for group, items in self.store.groups_with_items():
            if group is not None:
                header = GroupHeader(group, len(items))
                header.toggled.connect(self.toggle_group)
                header.rename.connect(self.edit_group)
                header.exportGroup.connect(self.export_dialog)
                header.remove.connect(self.remove_group)
                self.list_layout.insertWidget(self.list_layout.count() - 1, header)
                if group.get("collapsed"):
                    continue
            for entry in items:
                row = CommandRow(entry, self.store.groups)
                row.send.connect(self.send_item)
                row.edit.connect(self.open_editor)
                row.remove.connect(self.remove_item)
                row.toggled.connect(self.set_enabled)
                row.exportOne.connect(self.export_one)
                row.moveTo.connect(self.store.move_to_group)
                self.list_layout.insertWidget(self.list_layout.count() - 1, row)

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
        self.store.update_group(group_id, {"collapsed": not group.get("collapsed")})
        # The rows live in the layout (not in a per-group container), so the
        # panel has to re-emit itself for the collapse to be visible.
        self.rebuild()

    def new_group(self) -> None:
        dialog = GroupDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        data = dialog.result_group()
        self.store.add_group(data["name"], data["note"])

    def edit_group(self, group_id: str) -> None:
        group = self.store.group_by_id(group_id)
        if group is None:
            return
        dialog = GroupDialog(self, group)
        if dialog.exec() != QDialog.Accepted:
            return
        self.store.update_group(group_id, dialog.result_group())

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
            self.notify(tr("cmd.enabled.tip"), "warn")
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
        self.store.update(item_id, {"enabled": enabled})

    def open_editor(self, item: dict | None) -> None:
        dialog = CommandDialog(self, item, self.store.groups)
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
        self.paste_box.setPlaceholderText(tr("cmd.paste"))
        self.btn_cancel.setText(tr("cmd.dialog.cancel"))
        self.btn_confirm.setText(tr("cmd.dialog.confirmimport"))
        self.empty.setText(tr("empty.commands"))
        self.rebuild()
