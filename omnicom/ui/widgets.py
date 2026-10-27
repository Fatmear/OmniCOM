"""Reusable Qt widgets that carry the OmniCOM glass look."""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QWidget,
)

from . import theme


class GlassFrame(QFrame):
    """Rounded white card with a hairline border and a soft shadow."""

    def __init__(self, parent=None, radius: int = 20, strong: bool = False,
                 shadow: bool = True) -> None:
        super().__init__(parent)
        self.radius = radius
        self.strong = strong
        self.setAttribute(Qt.WA_StyledBackground, False)
        if shadow:
            effect = QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(34)
            effect.setOffset(0, 10)
            effect.setColor(QColor(18, 26, 43, 46))
            self.setGraphicsEffect(effect)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)

        # Flat white surface; the "strong" variant (title bar) is a touch
        # cooler so it separates from the cards below it.
        fill = QColor(theme.CARD) if self.strong else QColor(255, 255, 255, 244)
        painter.setPen(Qt.NoPen)
        painter.fillPath(path, QBrush(fill))

        # A barely-there top sheen keeps the card from looking dead flat.
        sheen = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 255))
        sheen.setColorAt(0.35, QColor(255, 255, 255, 0))
        painter.fillPath(path, QBrush(sheen))

        border = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        border.setColorAt(0.0, QColor(18, 26, 43, 52))
        border.setColorAt(0.4, QColor(18, 26, 43, 30))
        border.setColorAt(1.0, QColor(18, 26, 43, 20))
        painter.setPen(QPen(QBrush(border), 1.0))
        painter.drawPath(path)
        super().paintEvent(event)


class ToggleSwitch(QWidget):
    """iOS-style animated switch; drop-in replacement for a checkbox."""

    toggled = Signal(bool)

    def __init__(self, parent=None, checked: bool = False, width: int = 38,
                 height: int = 22) -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self._pos = 1.0 if checked else 0.0
        self._w, self._h = width, height
        self.setFixedSize(width, height)
        self.setCursor(Qt.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(190)
        self._anim.setEasingCurve(QEasingCurve.OutBack)

    # -- animation property -------------------------------------------------
    def get_knob(self) -> float:
        return self._pos

    def set_knob(self, value: float) -> None:
        self._pos = value
        self.update()

    knob = Property(float, get_knob, set_knob)

    # -- state --------------------------------------------------------------
    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool, animate: bool = True) -> None:
        value = bool(value)
        if value == self._checked:
            return
        self._checked = value
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(1.0 if value else 0.0)
            self._anim.start()
        else:
            self.set_knob(1.0 if value else 0.0)
        self.toggled.emit(value)

    def mousePressEvent(self, event) -> None:
        # Accept the press/release here: QWidget ignores mouse events by
        # default, which let them propagate to SwitchRow and toggle the state
        # straight back (the switch looked dead when clicked directly).
        if event.button() == Qt.LeftButton and self.isEnabled():
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.setChecked(not self._checked)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self._w - 1, self._h - 1)
        radius = rect.height() / 2

        track = QLinearGradient(rect.topLeft(), rect.bottomRight())
        if self._checked:
            track.setColorAt(0.0, QColor(18, 179, 158))
            track.setColorAt(1.0, QColor(124, 92, 250))
        else:
            track.setColorAt(0.0, QColor(18, 26, 43, 40))
            track.setColorAt(1.0, QColor(18, 26, 43, 30))
        painter.setPen(QPen(QColor(18, 26, 43, 30), 1))
        painter.setBrush(QBrush(track))
        painter.drawRoundedRect(rect, radius, radius)

        knob_d = rect.height() - 4
        x = rect.left() + 2 + self._pos * (rect.width() - knob_d - 4)
        knob = QRectF(x, rect.top() + 2, knob_d, knob_d)
        glow = QRadialGradient(knob.center(), knob_d * 0.95)
        glow.setColorAt(0.0, QColor(255, 255, 255, 255))
        glow.setColorAt(1.0, QColor(248, 250, 253, 255))
        painter.setPen(QPen(QColor(18, 26, 43, 60), 1))
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(knob)


class SwitchRow(QWidget):
    """Label on the left, switch on the right."""

    toggled = Signal(bool)

    def __init__(self, text: str, checked: bool = False, parent=None,
                 tooltip: str = "") -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 3, 2, 3)
        layout.setSpacing(8)
        self.label = QLabel(text)
        self.label.setStyleSheet(f"color: {theme.INK2}; font-size: 12.5px;")
        layout.addWidget(self.label, 1)
        self.switch = ToggleSwitch(self, checked=checked)
        layout.addWidget(self.switch, 0, Qt.AlignRight)
        self.switch.toggled.connect(self.toggled.emit)
        self.label.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)

    def isChecked(self) -> bool:
        return self.switch.isChecked()

    def setChecked(self, value: bool) -> None:
        self.switch.setChecked(value, animate=False)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.switch.setChecked(not self.switch.isChecked())
        super().mouseReleaseEvent(event)


class SectionTitle(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text.upper(), parent)
        self.setObjectName("SectionTitle")


class Badge(QLabel):
    def __init__(self, text: str = "", parent=None, kind: str = "Badge") -> None:
        super().__init__(text, parent)
        self.setObjectName(kind)
        self.setAlignment(Qt.AlignCenter)
        self.hide()

    def set_kind(self, kind: str) -> None:
        self.setObjectName(kind)
        self.style().unpolish(self)
        self.style().polish(self)

    def show_text(self, text: str, kind: str = "Badge") -> None:
        self.set_kind(kind)
        self.setText(text)
        self.show()


class StatusPill(QFrame):
    """Connection state: coloured dot + short text, one consistent shape."""

    KINDS: ClassVar[dict[str, tuple[str, str, str]]] = {
        "idle": ("#8A94A6", theme.WASH, theme.LINE),
        "ok": (theme.TEAL, theme.OK_SOFT, "rgba(14, 148, 132, 120)"),
        "retry": (theme.AMBER, theme.AMBER_SOFT, "rgba(180, 83, 9, 110)"),
        "error": (theme.DANGER, theme.DANGER_SOFT, "rgba(197, 52, 52, 110)"),
    }

    def __init__(self, parent=None, text: str = "未连接") -> None:
        super().__init__(parent)
        self.setObjectName("StatusPill")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 13, 0)
        layout.setSpacing(7)
        self.dot = QLabel("●")
        self.dot.setFixedWidth(9)
        self.label = QLabel(text)
        self.label.setStyleSheet("border: 0; background: transparent; font-size: 12px;")
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        self.setFixedHeight(30)
        self.set_state("idle", text)

    def set_state(self, kind: str, text: str) -> None:
        dot_color, fill, border = self.KINDS.get(kind, self.KINDS["idle"])
        text_color = theme.OK if kind == "ok" else theme.AMBER if kind == "retry" else \
            theme.DANGER if kind == "error" else theme.INK2
        self.dot.setStyleSheet(
            f"color: {dot_color}; border: 0; background: transparent; font-size: 10px;")
        self.label.setText(text)
        self.label.setStyleSheet(
            f"color: {text_color}; border: 0; background: transparent; font-size: 12px;")
        self.setStyleSheet(
            f"QFrame#StatusPill {{ background: {fill}; border: 1px solid {border};"
            f" border-radius: 15px; }}")


class Divider(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet(f"background: {theme.LINE_SOFT}; border: 0;")


class FlowLayout(QLayout):
    """Left-to-right wrapping layout (used by the keyword chips)."""

    def __init__(self, parent=None, spacing: int = 5) -> None:
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(QRectF(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._layout(QRectF(rect), apply=True)

    def sizeHint(self) -> QSize:
        # NOTE: this used to call a hand-written ``MinimumSize()`` which in turn
        # called ``QWidgetItem.MinimumSize()`` - a method that does not exist in
        # PySide6.  It only ever surfaced the first time a chips layout was
        # asked for its size hint (i.e. when the search panel was resized).
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.sizeHint())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    def minimumSize(self) -> QSize:
        return self.sizeHint()

    def _layout(self, rect: QRectF, apply: bool) -> int:
        margins = self.contentsMargins()
        x = rect.x() + margins.left()
        y = rect.y() + margins.top()
        line_height = 0
        right = rect.right() - margins.right()
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > right and line_height > 0:
                x = rect.x() + margins.left()
                y += line_height + self._spacing
                line_height = 0
            if apply:
                item.setGeometry(QRectF(x, y, hint.width(), hint.height()).toRect())
            x += hint.width() + self._spacing
            line_height = max(line_height, hint.height())
        return int(y + line_height + margins.bottom() - rect.y())


class Chip(QWidget):
    """Removable keyword pill."""

    removed = Signal(str)

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.text = text
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 2, 6, 2)
        layout.setSpacing(5)
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {theme.TEAL}; font-family: {theme.MONO_FONT}; font-size: 11.5px;")
        close = QLabel("×")
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet(f"color: {theme.TEAL}; font-size: 13px;")
        close.mouseReleaseEvent = lambda _event: self.removed.emit(self.text)
        layout.addWidget(label)
        layout.addWidget(close)
        self.setStyleSheet(
            f"QWidget {{ background: {theme.TEAL_SOFT};"
            f" border: 1px solid rgba(14, 148, 132, 82); border-radius: 10px; }}")


class GlyphButton(QPushButton):
    """Title-bar button that draws its icon with QPainter.

    Text glyphs such as ``✕`` depend on a font that actually carries them; when
    the fallback chain lacks the character the button looks empty.  Painting two
    lines is deterministic on every machine, so the window controls are always
    visible regardless of theme or font.
    """

    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    RESTORE = "restore"
    CLOSE = "close"
    SIDEBAR = "sidebar"
    CHEVRON_DOWN = "chevron_down"
    CHEVRON_RIGHT = "chevron_right"
    DOWN = "down"
    CLEAR = "clear"
    EXPAND = "expand"
    SHRINK = "shrink"
    REFRESH = "refresh"
    FOLDER = "folder"
    SAVE = "save"
    SEARCH = "search"
    SEND = "send"
    EDIT = "edit"
    TRASH = "trash"
    PLUS = "plus"
    PASTE = "paste"

    def __init__(self, glyph: str, parent=None, danger: bool = False,
                 size: int = 30, object_name: str | None = None) -> None:
        super().__init__(parent)
        self.glyph = glyph
        self.danger = danger
        if object_name is None:
            object_name = "TopIconClose" if danger else "TopIcon"
        self.setObjectName(object_name)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)

    def set_glyph(self, glyph: str) -> None:
        if glyph != self.glyph:
            self.glyph = glyph
            self.update()

    def _ink(self) -> QColor:
        if self.danger and self.underMouse():
            return QColor("#FFFFFF")
        if not self.isEnabled():
            return QColor(theme.DIM)
        return QColor(theme.INK2)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)          # keeps the QSS hover/pressed wash
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(self._ink(), 1.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        cx, cy = self.width() / 2.0, self.height() / 2.0
        # The icon box scales with the button so all icons look the same weight.
        half = min(self.width(), self.height()) * 0.17
        glyph = self.glyph
        if glyph == self.MINIMIZE:
            painter.drawLine(int(cx - half), int(cy), int(cx + half), int(cy))
        elif glyph == self.MAXIMIZE:
            painter.drawRect(QRectF(cx - half, cy - half, half * 2, half * 2))
        elif glyph == self.RESTORE:
            painter.drawRect(QRectF(cx - half, cy - half + 2, half * 2 - 2, half * 2 - 2))
            painter.drawLine(int(cx - half + 2), int(cy - half + 2),
                             int(cx - half + 2), int(cy - half))
            painter.drawLine(int(cx - half + 2), int(cy - half),
                             int(cx + half), int(cy - half))
            painter.drawLine(int(cx + half), int(cy - half),
                             int(cx + half), int(cy + half - 2))
        elif glyph == self.SIDEBAR:
            painter.drawRoundedRect(
                QRectF(cx - half - 0.5, cy - half, half * 2 + 1, half * 2), 1.5, 1.5)
            painter.drawLine(int(cx + 1), int(cy - half), int(cx + 1), int(cy + half))
        elif glyph in (self.CHEVRON_DOWN, self.DOWN):
            painter.drawLine(int(cx - half), int(cy - half * 0.55),
                             int(cx), int(cy + half * 0.55))
            painter.drawLine(int(cx), int(cy + half * 0.55),
                             int(cx + half), int(cy - half * 0.55))
            if glyph == self.DOWN:
                painter.drawLine(int(cx), int(cy - half * 1.15), int(cx), int(cy + half * 0.3))
        elif glyph == self.CHEVRON_RIGHT:
            painter.drawLine(int(cx - half * 0.55), int(cy - half),
                             int(cx + half * 0.55), int(cy))
            painter.drawLine(int(cx + half * 0.55), int(cy),
                             int(cx - half * 0.55), int(cy + half))
        elif glyph == self.CLEAR:
            box = QRectF(cx - half, cy - half, half * 2, half * 2)
            painter.drawRoundedRect(box, 2.5, 2.5)
            painter.drawLine(int(cx - half * 0.5), int(cy + half),
                             int(cx - half * 0.5), int(cy + half * 0.1))
            painter.drawLine(int(cx + half * 0.5), int(cy + half),
                             int(cx + half * 0.5), int(cy + half * 0.1))
            painter.drawLine(int(cx - half), int(cy + half * 0.1),
                             int(cx + half), int(cy + half * 0.1))
        elif glyph in (self.EXPAND, self.SHRINK):
            outward = glyph == self.EXPAND
            inner = half * 0.45
            for dx, dy in ((-1, -1), (1, 1)):
                sx = cx + dx * (half if outward else inner)
                sy = cy + dy * (half if outward else inner)
                ex = cx + dx * (inner if outward else half)
                ey = cy + dy * (inner if outward else half)
                painter.drawLine(int(sx), int(sy), int(ex), int(sy))
                painter.drawLine(int(sx), int(sy), int(sx), int(ey))
        elif glyph == self.REFRESH:
            rect = QRectF(cx - half, cy - half, half * 2, half * 2)
            painter.drawArc(rect, 40 * 16, 260 * 16)
            painter.drawLine(int(cx + half * 0.75), int(cy - half * 0.95),
                             int(cx + half * 1.05), int(cy - half * 0.25))
            painter.drawLine(int(cx + half * 0.75), int(cy - half * 0.95),
                             int(cx + half * 0.1), int(cy - half * 0.62))
        elif glyph == self.FOLDER:
            path = QPainterPath()
            path.moveTo(cx - half, cy - half * 0.7)
            path.lineTo(cx - half * 0.15, cy - half * 0.7)
            path.lineTo(cx + half * 0.1, cy - half * 0.35)
            path.lineTo(cx + half, cy - half * 0.35)
            path.lineTo(cx + half, cy + half * 0.8)
            path.lineTo(cx - half, cy + half * 0.8)
            path.closeSubpath()
            painter.drawPath(path)
        elif glyph == self.SAVE:
            painter.drawRoundedRect(
                QRectF(cx - half, cy - half, half * 2, half * 2), 2, 2)
            painter.drawRect(QRectF(cx - half * 0.5, cy - half, half, half * 0.8))
            painter.drawLine(int(cx - half * 0.6), int(cy + half * 0.9),
                             int(cx + half * 0.6), int(cy + half * 0.9))
        elif glyph == self.SEARCH:
            painter.drawEllipse(
                QRectF(cx - half, cy - half, half * 1.6, half * 1.6))
            painter.drawLine(int(cx + half * 0.35), int(cy + half * 0.35),
                             int(cx + half), int(cy + half))
        elif glyph == self.SEND:
            path = QPainterPath()
            path.moveTo(cx - half, cy - half * 0.9)
            path.lineTo(cx + half, cy)
            path.lineTo(cx - half, cy + half * 0.9)
            path.lineTo(cx - half * 0.45, cy)
            path.closeSubpath()
            painter.drawPath(path)
        elif glyph == self.EDIT:
            painter.drawLine(int(cx - half), int(cy + half),
                             int(cx + half * 0.5), int(cy - half))
            painter.drawLine(int(cx - half * 0.15), int(cy + half * 1.05),
                             int(cx - half * 0.75), int(cy + half * 1.05))
            painter.drawLine(int(cx - half * 0.75), int(cy + half * 1.05),
                             int(cx - half * 0.75), int(cy + half * 0.45))
        elif glyph == self.TRASH:
            painter.drawLine(int(cx - half), int(cy - half * 0.7),
                             int(cx + half), int(cy - half * 0.7))
            painter.drawLine(int(cx - half * 0.35), int(cy - half),
                             int(cx + half * 0.35), int(cy - half))
            painter.drawLine(int(cx - half * 0.65), int(cy - half * 0.7),
                             int(cx - half * 0.5), int(cy + half))
            painter.drawLine(int(cx + half * 0.65), int(cy - half * 0.7),
                             int(cx + half * 0.5), int(cy + half))
            painter.drawLine(int(cx - half * 0.5), int(cy + half),
                             int(cx + half * 0.5), int(cy + half))
        elif glyph == self.PLUS:
            painter.drawLine(int(cx - half), int(cy), int(cx + half), int(cy))
            painter.drawLine(int(cx), int(cy - half), int(cx), int(cy + half))
        elif glyph == self.PASTE:
            painter.drawRoundedRect(
                QRectF(cx - half * 0.9, cy - half, half * 1.8, half * 2), 1.5, 1.5)
            painter.drawLine(int(cx - half * 0.35), int(cy - half * 0.25),
                             int(cx + half * 0.4), int(cy - half * 0.25))
            painter.drawLine(int(cx - half * 0.35), int(cy + half * 0.35),
                             int(cx + half * 0.4), int(cy + half * 0.35))
        else:  # close
            painter.drawLine(int(cx - half), int(cy - half), int(cx + half), int(cy + half))
            painter.drawLine(int(cx + half), int(cy - half), int(cx - half), int(cy + half))


class Combo(QComboBox):
    """Combo box with a painted chevron, and click-anywhere-to-open.

    Two problems this fixes:

    * the platform arrow was drawn with the *style* font and looked nothing
      like the rest of the icons, so every combo now paints its own chevron;
    * on an **editable** combo (the baud rate) clicking the field only placed
      the text cursor - the list never appeared.  Clicking now opens the list
      while typing still works, which is the behaviour a serial tool needs.
    """

    def __init__(self, parent=None, editable: bool = False) -> None:
        super().__init__(parent)
        self.setEditable(editable)
        self.setCursor(Qt.PointingHandCursor)
        self.setMaxVisibleItems(14)
        view = self.view()
        view.setUniformItemSizes(True)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._chevron = _Chevron(self)
        if editable and self.lineEdit() is not None:
            self.lineEdit().installEventFilter(self)
            self.lineEdit().setCursor(Qt.IBeamCursor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        height = max(10, self.height() - 10)
        self._chevron.setGeometry(self.width() - 26, 5, 20, height)
        self._chevron.raise_()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.lineEdit() and event.type() == QEvent.MouseButtonRelease:
            # Click selects the whole value and opens the list: typing then
            # replaces the number instead of appending to it, which is what a
            # baud field needs ("不影响手动输入").
            self.lineEdit().setFocus()
            self.lineEdit().selectAll()
            self.showPopup()
            return True
        if obj is self.lineEdit() and event.type() == QEvent.KeyPress:
            # Once the user starts typing, get the list out of the way: with an
            # open popup Enter would activate the highlighted row and throw the
            # typed value away.  Custom baud rates must be safe to type.
            if self.view().isVisible() and event.text():
                self.hidePopup()
            return False
        return super().eventFilter(obj, event)

    def showPopup(self) -> None:
        super().showPopup()
        self._chevron.set_open(True)

    def hidePopup(self) -> None:
        super().hidePopup()
        self._chevron.set_open(False)


class _Chevron(QWidget):
    """The little arrow inside a Combo, painted like every other icon."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._open = False

    def set_open(self, value: bool) -> None:
        if value != self._open:
            self._open = value
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(theme.INK2), 1.5)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        half = 3.6
        if self._open:
            painter.drawLine(int(cx - half), int(cy + half * 0.55),
                             int(cx), int(cy - half * 0.55))
            painter.drawLine(int(cx), int(cy - half * 0.55),
                             int(cx + half), int(cy + half * 0.55))
        else:
            painter.drawLine(int(cx - half), int(cy - half * 0.55),
                             int(cx), int(cy + half * 0.55))
            painter.drawLine(int(cx), int(cy + half * 0.55),
                             int(cx + half), int(cy - half * 0.55))


class ScrollBottomPill(QPushButton):
    """Floating "jump to latest" bubble, like the one web chat UIs show.

    Appears only while the view is scrolled away from the bottom, then fades +
    slides in.  It is deliberately not an arrow glyph in a toolbar: the user
    asked for the animated bubble used on web pages.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ScrollPill")
        self.setFixedSize(38, 38)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("回到底部 / 最新")
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(160)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(180)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)
        self._visible = False
        self.hide()

    # ------------------------------------------------------------------ state
    def show_pill(self, anchor: QPoint) -> None:
        target = anchor - QPoint(self.width() // 2, self.height())
        if self._visible:
            self.move(target)
            return
        self._visible = True
        self.show()
        self.raise_()
        self.move(target + QPoint(0, 10))
        self._slide.stop()
        self._slide.setStartValue(target + QPoint(0, 10))
        self._slide.setEndValue(target)
        self._slide.start()
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(1.0)
        self._fade.start()

    def hide_pill(self) -> None:
        if not self._visible:
            return
        self._visible = False
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()
        QTimer.singleShot(170, self._finish_hide)

    def _finish_hide(self) -> None:
        if not self._visible:
            self.hide()

    # ------------------------------------------------------------------ paint
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)

        # soft shadow (a couple of translucent rings)
        hovered = self.underMouse()
        for index, alpha in ((3.0, 12), (2.0, 18)):
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(18, 26, 43, alpha))
            painter.drawEllipse(rect.adjusted(-index, -index + 2, index, index + 2))

        painter.setBrush(QColor(theme.CARD if not hovered else "#F4F8FD"))
        painter.setPen(QPen(QColor(theme.INK2 if hovered else "#C7D0DE"), 1.0))
        painter.drawEllipse(rect)

        pen = QPen(QColor(theme.TEAL if hovered else theme.INK2), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        half = 4.6
        painter.drawLine(int(cx - half), int(cy - half * 0.5),
                         int(cx), int(cy + half * 0.55))
        painter.drawLine(int(cx), int(cy + half * 0.55),
                         int(cx + half), int(cy - half * 0.5))


class Toast(QWidget):
    """Transient message card anchored inside the main window."""

    LEVELS: ClassVar[dict[str, tuple[str, str, str]]] = {
        "ok": ("成功", theme.OK, theme.OK_SOFT),
        "info": ("提示", theme.BLUE, "#E8F0FE"),
        "warn": ("警告", theme.AMBER, theme.AMBER_SOFT),
        "error": ("错误", theme.DANGER, theme.DANGER_SOFT),
    }

    def __init__(self, parent, text: str, level: str = "info", timeout: int = 3200) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._level = level
        title, accent, soft = self.LEVELS.get(level, self.LEVELS["info"])
        self._accent = accent
        self._soft = soft

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 12, 12)
        layout.setSpacing(11)

        self.badge = QLabel(title)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setFixedSize(48, 22)
        self.badge.setStyleSheet(
            f"color: {accent}; background: {soft}; border-radius: 11px;"
            f" font-size: 11.5px; font-weight: 650;")
        layout.addWidget(self.badge, 0, Qt.AlignTop)

        self.message = QLabel(text)
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"color: {theme.INK}; font-size: 12.5px;")
        layout.addWidget(self.message, 1)

        self.adjustSize()
        self.setFixedWidth(min(460, max(280, self.sizeHint().width() + 10)))
        self.adjustSize()

        # Slide-in instead of a QGraphicsOpacityEffect: a graphics effect makes
        # QWidget.grab() return a blank pixmap, so the toast would be invisible
        # in screenshots and in the acceptance artefacts.
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(190)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)
        self._home: QPoint | None = None
        self.show()
        self.raise_()

    def play_in(self) -> None:
        """Slide up from a few pixels below the final position."""
        self._home = self.pos()
        self._slide.stop()
        self._slide.setStartValue(self._home + QPoint(0, 14))
        self._slide.setEndValue(self._home)
        self._slide.start()

    def dismiss(self) -> None:
        """Slide down and delete."""
        if self._home is not None:
            self._slide.stop()
            self._slide.setStartValue(self.pos())
            self._slide.setEndValue(self._home + QPoint(0, 12))
            self._slide.start()
        QTimer.singleShot(150, self.deleteLater)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)

        # soft drop shadow
        painter.setPen(Qt.NoPen)
        for offset, alpha in ((4, 10), (2, 16)):
            painter.setBrush(QColor(18, 26, 43, alpha))
            painter.drawRoundedRect(rect.adjusted(-1, offset - 1, 1, offset + 1), 15, 15)

        painter.setBrush(QColor(theme.CARD))
        painter.setPen(QPen(QColor("#D7DEE9"), 1.0))
        painter.drawRoundedRect(rect, 14, 14)

        accent = QColor(self._accent)
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(QRectF(rect.left(), rect.top() + 12, 3.5,
                                       rect.height() - 24), 1.75, 1.75)
