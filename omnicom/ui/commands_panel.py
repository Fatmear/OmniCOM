"""Right-sidebar command list: groups, notes, per-item and per-group export."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QMimeData,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
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

#: Sentinel entries of the batch 「范围」 combo (R-136).  Real group ids are
#: UUIDs and the ungrouped bucket is ``""``, so these two cannot collide.
SCOPE_ALL = "__all__"
SCOPE_MULTI = "__multi__"

#: Private drag payload: the ids of the dragged commands, as a JSON list.
#: A private type (not ``text/plain``) keeps a dropped *file* or a piece of
#: selected text from being mistaken for a command drag (R-130).
DRAG_MIME = "application/x-omnicom-command"


def drag_ids(source) -> list[str]:
    """Item ids carried by a drag, or ``[]`` when it is not a command drag."""
    mime = source
    if hasattr(source, "mimeData"):
        mime = source.mimeData()
    if mime is None or not mime.hasFormat(DRAG_MIME):
        return []
    try:
        payload = json.loads(bytes(mime.data(DRAG_MIME)).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [str(entry) for entry in payload]


def _drag_mime(item_ids: list[str]) -> QMimeData:
    mime = QMimeData()
    mime.setData(DRAG_MIME, json.dumps(item_ids).encode("utf-8"))
    return mime


class DropStrip(QWidget):
    """A thin, *invisible* drop target at the end of a bucket (R-130/R-133).

    Dropping *onto* a command row inserts **before** that row, so the very last
    position of a group - the only way to undo "I dragged this one too high" -
    had no target at all.  This strip provides it, and paints the landing line
    while a drag is over it.

    It used to paint a dashed line (and, at the bottom of the list, the hint
    "拖到这里移出分组") even when nothing was being dragged, which made the
    panel look permanently unfinished.  The hint is gone and the idle state is
    fully transparent: dragging already shows a landing indicator, so the user
    can tell where the command will go without any permanent decoration.
    """

    dropRequested = Signal(object, str)      # ids, group id

    def __init__(self, group_id: str, parent=None) -> None:
        super().__init__(parent)
        self.group_id = group_id
        self._hot = False
        self.setFixedHeight(14)
        self.setAcceptDrops(True)

    def set_hot(self, hot: bool) -> None:
        if self._hot != hot:
            self._hot = hot
            self.update()

    def paintEvent(self, event) -> None:
        if not self._hot:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.TEAL), 2))
        painter.drawLine(4, self.height() // 2, self.width() - 4,
                         self.height() // 2)
        painter.end()

    # ------------------------------------------------------------- dragging
    def dragEnterEvent(self, event) -> None:
        if drag_ids(event):
            event.acceptProposedAction()
            self.set_hot(True)
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.set_hot(False)

    def dropEvent(self, event) -> None:
        self.set_hot(False)
        ids = drag_ids(event)
        if not ids:
            event.ignore()
            return
        event.acceptProposedAction()
        self.dropRequested.emit(ids, self.group_id)


class BucketBody(QWidget):
    """The indented area under a group header (R-130).

    Dropping anywhere in this blank area - not just on a command - means "add it
    to this group", so a group with two commands does not require pixel-hunting
    for the strip below them.  It also keeps the drop inside the *right* group
    when the user aims at the empty space under a short list.
    """

    dropRequested = Signal(object, str)      # ids, group id

    def __init__(self, group_id: str, parent=None) -> None:
        super().__init__(parent)
        self.group_id = group_id
        self._drop_hot = False
        self.setAcceptDrops(True)

    def _set_hot(self, hot: bool) -> None:
        if self._drop_hot != hot:
            self._drop_hot = hot
            self.update()

    def dragEnterEvent(self, event) -> None:
        if drag_ids(event):
            event.acceptProposedAction()
            self._set_hot(True)
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_hot(False)

    def dropEvent(self, event) -> None:
        ids = drag_ids(event)
        self._set_hot(False)
        if not ids:
            event.ignore()
            return
        event.acceptProposedAction()
        self.dropRequested.emit(ids, self.group_id)

    def paintEvent(self, event) -> None:
        if not self._drop_hot:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(theme.TEAL), 1.5, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75),
            theme.RADIUS_CTRL, theme.RADIUS_CTRL)
        painter.end()


class ListHost(QWidget):
    """Scroll content: a drop below the last command appends to the last bucket."""

    dropRequested = Signal(object)           # ids

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if drag_ids(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if drag_ids(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:
        ids = drag_ids(event)
        if not ids:
            event.ignore()
            return
        event.acceptProposedAction()
        self.dropRequested.emit(ids)


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
    dragOut = Signal(str)                       # this row asks to start a drag
    dropped = Signal(object, object, bool)      # ids, this row, landed below

    def __init__(self, item: dict, groups: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.groups = groups
        self.setObjectName("CmdRow")
        # Where this row sits in the panel: set by ``CommandsPanel.rebuild()``
        # so a drop can be turned into (group, index) without searching (R-130).
        self.bucket_group = UNGROUPED
        self.bucket_index = -1
        self._drop_hot = False
        self._drop_below = False
        self._drag_origin: QPoint | None = None
        self._suppress_send = False
        self.setAcceptDrops(True)
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
        details.append(tr("cmd.drag.tip"))
        self.setToolTip("\n".join(details))
        self.label.setToolTip(self.toolTip())
        self.label.mouseReleaseEvent = self._on_label_release
        # The label covers most of the row and swallows the mouse events, so it
        # needs the press/move handlers too - otherwise "hold a command and
        # drag it" would only work on the 3 px margins (R-130).
        self.label.mousePressEvent = self._track_press
        self.label.mouseMoveEvent = self._track_move
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
        if self._suppress_send:
            # The press ended as a drag: sending here would transmit a command
            # every time somebody re-ordered the list (R-130).
            self._suppress_send = False
            return
        if event.button() == Qt.LeftButton:
            self.send.emit(self.item)

    # ---------------------------------------------------------------- dragging
    def _track_press(self, event) -> None:
        """Remember where a left press started; a move may promote it to a drag."""
        if event.button() == Qt.LeftButton:
            self._drag_origin = event.position().toPoint()
            self._suppress_send = False
        else:
            self._drag_origin = None

    def _track_move(self, event) -> None:
        if self._drag_origin is None or not (event.buttons() & Qt.LeftButton):
            return
        moved = (event.position().toPoint() - self._drag_origin).manhattanLength()
        if moved < QApplication.startDragDistance():
            return
        self._drag_origin = None
        self._suppress_send = True
        self.dragOut.emit(self.item["id"])

    def mousePressEvent(self, event) -> None:
        self._track_press(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self._track_move(event)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------- drop target
    def _landing_below(self, event) -> bool:
        return event.position().y() > self.height() / 2

    def _accepts(self, event) -> bool:
        ids = drag_ids(event)
        return bool(ids) and self.item["id"] not in ids

    def dragEnterEvent(self, event) -> None:
        if self._accepts(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not self._accepts(event):
            event.ignore()
            return
        below = self._landing_below(event)
        if not self._drop_hot or self._drop_below != below:
            self._drop_hot = True
            self._drop_below = below
            self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self.clear_drop_hint()

    def dropEvent(self, event) -> None:
        ids = drag_ids(event)
        below = self._landing_below(event)
        self.clear_drop_hint()
        if not ids:
            event.ignore()
            return
        event.acceptProposedAction()
        self.dropped.emit(ids, self, below)

    def clear_drop_hint(self) -> None:
        if self._drop_hot:
            self._drop_hot = False
            self.update()

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
        if self._drop_hot:
            # The landing position: a 2 px teal line on the half of the row the
            # pointer is over, so "before" and "after" are never a guess (R-130).
            marker = QColor(theme.TEAL)
            painter.setPen(Qt.NoPen)
            painter.setBrush(marker)
            y = self.height() - 2 if self._drop_below else 0
            painter.drawRoundedRect(QRectF(2, y, self.width() - 4, 2), 1, 1)

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
    dropped = Signal(object, str)          # ids, target group id
    pickAll = Signal(str, bool)            # group id, select its commands

    def __init__(self, group: dict, count: int, parent=None) -> None:
        super().__init__(parent)
        self.group = group
        self._drop_hot = False
        self.setAcceptDrops(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 5, 2, 1)
        layout.setSpacing(5)

        # Batch mode gives every group its own pick box (R-136).  Ticking one
        # group selects exactly its commands, and ticking several of them is
        # the "multiple groups" case - the old bar only knew "everything or
        # nothing", so picking one group meant unticking 30 rows by hand.
        self.pick = QCheckBox()
        self.pick.setTristate(True)
        self.pick.setCheckState(Qt.Unchecked)
        self.pick.setToolTip(tr("cmd.batch.pickgroup"))
        self.pick.stateChanged.connect(self._on_pick_state)
        self.pick.hide()
        layout.addWidget(self.pick)

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

        # Group-level delete (R-137): the same gesture a command row already
        # offers.  It emits the very same ``remove`` signal the context menu
        # uses, so both paths end up in ``CommandsPanel.remove_group()`` and ask
        # the same "keep the commands or drop them too?" question.
        self.trash_btn = GlyphButton(GlyphButton.TRASH, self, size=20)
        self.trash_btn.setToolTip(tr("cmd.deletegroup"))
        self.trash_btn.clicked.connect(lambda: self.remove.emit(group["id"]))
        layout.addWidget(self.trash_btn)

    def _on_label_release(self, event) -> None:
        """Only a left click collapses the group; the right one opens the menu."""
        if event.button() == Qt.LeftButton:
            self.toggled.emit(self.group["id"])

    def set_batch_mode(self, active: bool) -> None:
        """Show the group pick box while picking commands in bulk (R-136)."""
        self.pick.setVisible(active)
        if not active:
            self.set_pick_state(Qt.Unchecked)

    def set_pick_state(self, state) -> None:
        """Sync the tri-state box without emitting ``pickAll`` back."""
        self.pick.blockSignals(True)
        self.pick.setCheckState(state)
        self.pick.blockSignals(False)

    def _on_pick_state(self, state) -> None:
        # ``stateChanged`` hands over a plain ``int`` (2), not a
        # ``Qt.CheckState``, and PySide6 compares those by identity:
        # ``2 == Qt.Checked`` is **False**.  Comparing the raw value therefore
        # reported every tick as "unchecked", so ticking a group did nothing at
        # all (R-136).  Convert first, then compare enums.
        value = Qt.CheckState(state)
        if value == Qt.PartiallyChecked:
            # The half state is a *report*, never an instruction: clicking it
            # arrives as Unchecked/Checked anyway, so ignoring this branch just
            # stops a feedback loop when the panel re-syncs the box.
            return
        self.pickAll.emit(self.group["id"], value == Qt.Checked)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction(tr("cmd.addtogroup"),
                       lambda: self.addCommand.emit(self.group["id"]))
        menu.addAction(tr("cmd.dialog.exportgroup"),
                       lambda: self.exportGroup.emit(self.group["id"]))
        menu.addAction(tr("cmd.edit"), lambda: self.rename.emit(self.group["id"]))
        menu.addSeparator()
        menu.addAction(tr("cmd.deletegroup"),
                       lambda: self.remove.emit(self.group["id"]))
        menu.exec(event.globalPos())

    # ------------------------------------------------------------- drop target
    # Dropping a command on the *header* means "put it in this group", which is
    # the file-explorer gesture of dragging a file onto a folder (R-130).
    def dragEnterEvent(self, event) -> None:
        ids = drag_ids(event)
        if ids:
            event.acceptProposedAction()
            self._set_hot(True)
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_hot(False)

    def dropEvent(self, event) -> None:
        ids = drag_ids(event)
        self._set_hot(False)
        if not ids:
            event.ignore()
            return
        event.acceptProposedAction()
        self.dropped.emit(ids, self.group["id"])

    def _set_hot(self, hot: bool) -> None:
        if self._drop_hot != hot:
            self._drop_hot = hot
            self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._drop_hot:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(theme.TEAL), 1.5, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor(theme.TEAL_SOFT))
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75),
            theme.RADIUS_CTRL, theme.RADIUS_CTRL)
        painter.end()


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
        # The caption is 「命令」/ Command, never AT (R-129): a command may be an
        # AT line, a bare console command (``net sta enable``) or a fragment of
        # either, and the old caption implied the library only holds AT.  It is
        # no longer the abbreviation CMD either - the user asked for the plain
        # word (R-132).
        layout.addWidget(self._field(tr("cmd.field"), self.text))

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
        options.addWidget(self._field(tr("cmd.lineend"), self.line_end))

        self.delay = QSpinBox()
        self.delay.setRange(0, 60000)
        self.delay.setSingleStep(50)
        self.delay.setValue(int(self.item.get("delay", 120)))
        options.addWidget(self._field(tr("cmd.delay"), self.delay))
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
        #: Groups that "select all" applies to.  ``None`` means every command;
        #: a set may hold several group ids (and ``UNGROUPED``), which is what
        #: makes "only the commands inside this group" possible (R-136).
        self._scope: set[str] | None = None
        self._scope_key: tuple | None = None
        self._group_headers: dict[str, GroupHeader] = {}

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
        self.list_host = ListHost()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(5)
        self.list_layout.addStretch(1)
        self.list_host.dropRequested.connect(self._drop_at_end)
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
        # A library that could not be read is *not* reported by replacing it
        # with the defaults in silence - that looked exactly like "my order was
        # reset again" (R-135/L-157).  The message is deferred so it lands after
        # the window is on screen, where a toast can actually be seen.
        if self.store.load_warning:
            message = self.store.load_warning
            self.store.load_warning = ""
            QTimer.singleShot(0, lambda: self.notify(message, "warn"))

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
        """Selection tools: pick many, move them together, delete them together.

        Selecting used to be all-or-nothing, which is useless as soon as the
        library has groups: "delete the four commands in this group" meant
        unticking everything else by hand.  The bar therefore works on a
        **scope** (R-136):

        * ``scope_group`` picks which groups the 全选 button acts on -
          「全部命令」, one group, or several groups (ticked through the group
          headers, shown as 「已选 N 个分组」);
        * 全不选 always clears *everything*, because a button that says "none"
          and leaves half a selection behind is worse than no button.
        """
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
        self.btn_pick_all.setToolTip(tr("cmd.batch.all.tip"))
        self.btn_pick_all.clicked.connect(lambda: self._pick_all(True))
        self.btn_pick_none = QPushButton(tr("cmd.batch.none"))
        self.btn_pick_none.setObjectName("Ghost")
        self.btn_pick_none.setToolTip(tr("cmd.batch.none.tip"))
        self.btn_pick_none.clicked.connect(lambda: self._pick_all(False))
        self.btn_batch_exit = QPushButton(tr("cmd.batch.exit"))
        self.btn_batch_exit.setObjectName("Ghost")
        self.btn_batch_exit.clicked.connect(lambda: self.set_batch_mode(False))
        for button in (self.btn_pick_all, self.btn_pick_none, self.btn_batch_exit):
            button.setCursor(Qt.PointingHandCursor)
            head.addWidget(button)
        layout.addLayout(head)

        scope = QHBoxLayout()
        scope.setSpacing(6)
        self.scope_label = QLabel(tr("cmd.batch.scope"))
        self.scope_label.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        scope.addWidget(self.scope_label)
        self.scope_group = Combo()
        self.scope_group.setToolTip(tr("cmd.batch.scope.tip"))
        self.scope_group.currentIndexChanged.connect(self._on_scope_changed)
        scope.addWidget(self.scope_group, 1)
        layout.addLayout(scope)

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
            self._scope = None
            self._refresh_batch_groups()
            self._sync_scope_combo(force=True)
        else:
            self._scope = None
        for row in self.rows():
            row.set_batch_mode(self._batch)
        for header in getattr(self, "_group_headers", {}).values():
            header.set_batch_mode(self._batch)
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
        self._sync_group_picks()

    # --------------------------------------------------------------- selection
    def _bucket_of(self, row: CommandRow) -> str:
        return row.item.get("group") or UNGROUPED

    def _in_scope(self, row: CommandRow) -> bool:
        """Is this row inside the current 范围 (all / one group / several)?"""
        if self._scope is None:
            return True
        return self._bucket_of(row) in self._scope

    def _pick_all(self, value: bool) -> None:
        """全选 uses the current scope; 全不选 always clears everything.

        Restricting 全不选 to the scope looked broken: the button says "none"
        and the user expects an empty selection, not "empty inside one group"
        (R-136).
        """
        for row in self.rows():
            if not self.row_reachable(row):
                continue
            if value and not self._in_scope(row):
                continue
            row.pick.setChecked(value)
        self._sync_batch_count()

    def _pick_group(self, group_id: str, value: bool) -> None:
        """A group header's tick box: select exactly the commands it holds.

        Ticking one header is the "select this group" gesture; ticking a second
        one adds it to the scope, which is what makes *multiple* groups work
        (R-136).
        """
        bucket = group_id or UNGROUPED
        for row in self.rows():
            if self._bucket_of(row) != bucket or not self.row_reachable(row):
                continue
            row.pick.setChecked(value)
        if value:
            self._scope = (set(self._scope) if self._scope else set()) | {bucket}
        elif self._scope:
            remaining = set(self._scope) - {bucket}
            self._scope = remaining or None
        self._sync_scope_combo()
        self._sync_batch_count()

    def _sync_group_picks(self) -> None:
        """Mirror the row selection onto every group header's tri-state box."""
        headers = getattr(self, "_group_headers", {})
        if not headers:
            return
        picked: dict[str, int] = {}
        total: dict[str, int] = {}
        for row in self.rows():
            bucket = self._bucket_of(row)
            if bucket not in headers or not self.row_reachable(row):
                continue
            total[bucket] = total.get(bucket, 0) + 1
            if row.is_picked():
                picked[bucket] = picked.get(bucket, 0) + 1
        for group_id, header in headers.items():
            have = picked.get(group_id, 0)
            if have == 0:
                header.set_pick_state(Qt.Unchecked)
            elif have >= total.get(group_id, 0):
                header.set_pick_state(Qt.Checked)
            else:
                header.set_pick_state(Qt.PartiallyChecked)

    def _sync_scope_combo(self, force: bool = False) -> None:
        """Rebuild the 范围 list, adding a "several groups" entry when needed."""
        key = (None if self._scope is None else tuple(sorted(self._scope)),
               tuple(group["id"] for group in self.store.groups))
        if not force and key == self._scope_key:
            return
        self._scope_key = key
        scope = self._scope
        self.scope_group.blockSignals(True)
        self.scope_group.clear()
        self.scope_group.addItem(tr("cmd.batch.scope.all"), SCOPE_ALL)
        if scope is not None and len(scope) > 1:
            self.scope_group.addItem(tr("cmd.batch.scope.multi", n=len(scope)),
                                     SCOPE_MULTI)
        for group in self.store.groups:
            self.scope_group.addItem(group.get("name") or group["id"], group["id"])
        if any((item.get("group") or UNGROUPED) == UNGROUPED
               for item in self.store.items):
            self.scope_group.addItem("— " + tr("cmd.ungrouped"), UNGROUPED)
        if scope is None:
            index = 0
        elif len(scope) > 1:
            index = 1
        else:
            index = self.scope_group.findData(next(iter(scope)))
            index = index if index >= 0 else 0
        self.scope_group.setCurrentIndex(index)
        self.scope_group.blockSignals(False)

    def _on_scope_changed(self, _index: int) -> None:
        data = self.scope_group.currentData()
        if data == SCOPE_ALL:
            self._scope = None
        elif data == SCOPE_MULTI:
            # The set is already what the group headers report; the entry is a
            # read-out, not a choice.
            return
        else:
            self._scope = {data if data is not None else UNGROUPED}
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
        if len(self.store.split_entries(text)) > 1:
            self.paste_box.clear()
            self.import_text(text, self.paste_group.currentData() or "")

    def import_text(self, raw: str, group_id: str = "") -> None:
        entries = self.store.split_entries(raw)
        if not entries:
            return
        self.store.add_entries(entries, {"group": group_id} if group_id else None)
        self.hide_drawer()
        self.notify(tr("cmd.imported", n=len(entries)), "ok")

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
                header.dropped.connect(self._drop_in_group)
                header.pickAll.connect(self._pick_group)
                self.list_layout.insertWidget(self.list_layout.count() - 1, header)
                self._group_rows[group["id"]] = []
                self._group_headers[group["id"]] = header
                holder = BucketBody(group["id"])
                holder_layout = QVBoxLayout(holder)
                holder_layout.setContentsMargins(18, 0, 0, 0)
                holder_layout.setSpacing(5)
                holder.dropRequested.connect(
                    lambda ids, gid=group["id"]: self._drop_in_group(ids, gid))
                self.list_layout.insertWidget(self.list_layout.count() - 1, holder)
                holder.setVisible(not group.get("collapsed"))
                self._group_bodies[group["id"]] = holder
            bucket_group = UNGROUPED if group is None else group["id"]
            for index, entry in enumerate(items):
                row = CommandRow(entry, self.store.groups)
                row.bucket_group = bucket_group
                row.bucket_index = index
                row.send.connect(self.send_item)
                row.edit.connect(self.open_editor)
                row.remove.connect(self.remove_item)
                row.toggled.connect(self.set_enabled)
                row.moveTo.connect(self.move_item)
                row.pick.toggled.connect(self._sync_batch_count)
                row.dragOut.connect(self._start_drag)
                row.dropped.connect(self._drop_on_row)
                if group is None:
                    self.list_layout.insertWidget(self.list_layout.count() - 1, row)
                    continue
                self._group_bodies[group["id"]].layout().addWidget(row)
                self._group_rows[group["id"]].append(row)
            # NOTE: no strip is added at the end of a bucket (R-141).  Dropping
            # on the *lower* half of the last row already inserts after it,
            # which is the group's end, so the append target was redundant - and
            # a fixed-height widget there pushed the next group's header 19 px
            # further down (spacing 5 + strip 14), which the user read as "the
            # gap between two expanded groups is far too big".  The tail strip
            # below the last group stays: that one is the only way *out* of a
            # group (R-133).

        # The root bucket gets its own strip too: without it a command could be
        # dragged *into* a group by dropping on the group's header, but never
        # back out again unless an ungrouped command happened to exist to aim
        # at (R-130).  It carries no hint text - the idle panel stays clean and
        # the landing line only appears while dragging (R-133).
        tail = DropStrip(UNGROUPED)
        tail.dropRequested.connect(self._drop_in_group)
        self.list_layout.insertWidget(self.list_layout.count() - 1, tail)

        if self._batch:
            # A rebuild creates new rows, which start un-picked and show the
            # enable switch; the mode has to be re-applied to them (R-092).
            for row in self.rows():
                row.set_batch_mode(True)
            for header in self._group_headers.values():
                header.set_batch_mode(True)
            self._sync_scope_combo()
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

    # --------------------------------------------------------------- dragging
    def _bucket_ids(self, group_id: str) -> list[str]:
        """Ids of one bucket in display order.

        The bucket order inside a group is exactly the order of ``store.items``
        filtered by group (``groups_with_items()`` walks the flat list), so the
        panel can translate "dropped on row X" into a list index without
        keeping a second copy of the ordering.
        """
        target = group_id or UNGROUPED
        return [item["id"] for item in self.store.items
                if (item.get("group") or UNGROUPED) == target]

    def _start_drag(self, item_id: str) -> None:
        """Begin a command drag, carrying the whole batch when one is picked."""
        ids = [item_id]
        if self._batch:
            picked = self.picked_ids()
            if item_id in picked:
                ids = picked
        row = next((entry for entry in self.rows()
                    if entry.item["id"] == item_id), None)
        drag = QDrag(self)
        drag.setMimeData(_drag_mime(ids))
        if row is not None:
            drag.setPixmap(row.grab())
        # ``exec()`` runs a nested event loop and the drop handler is called
        # from inside it.  Rebuilding the list there would reparent - and soon
        # delete - the very widget that owns this drag, so the actual reorder
        # is queued until the loop has unwound (R-130).
        self._in_drag = True
        try:
            drag.exec(Qt.MoveAction)
        finally:
            self._in_drag = False
        self.clear_drop_hints()

    def clear_drop_hints(self) -> None:
        for row in self.rows():
            row.clear_drop_hint()

    def _apply_drop(self, ids, group_id: str, index: int) -> None:
        self.clear_drop_hints()
        if getattr(self, "_in_drag", False):
            QTimer.singleShot(0, lambda: self._apply_drop(ids, group_id, index))
            return
        moved = self.store.reorder(ids, group_id, index)
        if not moved:
            return
        group = self.store.group_by_id(group_id)
        name = (group or {}).get("name") or tr("cmd.ungrouped")
        self.notify(tr("cmd.drag.moved", n=moved, name=name), "ok")

    def _drop_on_row(self, ids, row, below: bool) -> None:
        """Insert before/after the row that received the drop (R-130)."""
        kept = [item_id for item_id in self._bucket_ids(row.bucket_group)
                if item_id not in ids]
        if row.item["id"] not in kept:
            # Dropped on itself: nothing to do, and re-inserting it would look
            # like the list jumped for no reason.
            return
        index = kept.index(row.item["id"]) + (1 if below else 0)
        self._apply_drop(ids, row.bucket_group, index)

    def _drop_in_group(self, ids, group_id: str) -> None:
        """Dropped on a header or in a group's blank area: append to that group."""
        self._apply_drop(ids, group_id, -1)

    def _drop_at_end(self, ids) -> None:
        """Dropped in the blank space below the list: take it out of its group.

        The bottom strip is the root bucket, so "drop in the empty area" is the
        unambiguous way back to 未分组 - the same move as dragging a file out of
        a folder onto the folder's parent (R-130).
        """
        self._apply_drop(ids, UNGROUPED, -1)

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
        self.btn_pick_all.setToolTip(tr("cmd.batch.all.tip"))
        self.btn_pick_none.setText(tr("cmd.batch.none"))
        self.btn_pick_none.setToolTip(tr("cmd.batch.none.tip"))
        self.btn_batch_exit.setText(tr("cmd.batch.exit"))
        self.scope_label.setText(tr("cmd.batch.scope"))
        self.scope_group.setToolTip(tr("cmd.batch.scope.tip"))
        self._scope_key = None
        self._sync_scope_combo(force=True)
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
