"""Reusable Qt widgets that carry the OmniCOM glass look."""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
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
    QFrame,
    QGraphicsDropShadowEffect,
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
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.MinimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

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

    def __init__(self, glyph: str, parent=None, danger: bool = False) -> None:
        super().__init__(parent)
        self.glyph = glyph
        self.danger = danger
        self.setObjectName("TopIconClose" if danger else "TopIcon")
        self.setFixedSize(30, 30)
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
        painter.setPen(pen)

        cx, cy = self.width() / 2.0, self.height() / 2.0
        half = 4.5
        if self.glyph == self.MINIMIZE:
            painter.drawLine(int(cx - half), int(cy), int(cx + half), int(cy))
        elif self.glyph == self.MAXIMIZE:
            painter.drawRect(QRectF(cx - half, cy - half, half * 2, half * 2))
        elif self.glyph == self.RESTORE:
            painter.drawRect(QRectF(cx - half, cy - half + 2, half * 2 - 2, half * 2 - 2))
            painter.drawLine(int(cx - half + 2), int(cy - half + 2),
                             int(cx - half + 2), int(cy - half))
            painter.drawLine(int(cx - half + 2), int(cy - half),
                             int(cx + half), int(cy - half))
            painter.drawLine(int(cx + half), int(cy - half),
                             int(cx + half), int(cy + half - 2))
        elif self.glyph == self.SIDEBAR:
            painter.drawRoundedRect(
                QRectF(cx - half - 0.5, cy - half, half * 2 + 1, half * 2), 1.5, 1.5)
            painter.drawLine(int(cx + 1), int(cy - half), int(cx + 1), int(cy + half))
        else:  # close
            painter.drawLine(int(cx - half), int(cy - half), int(cx + half), int(cy + half))
            painter.drawLine(int(cx + half), int(cy - half), int(cx - half), int(cy + half))


class Toast(QWidget):
    """Transient message bubble anchored inside the main window."""

    def __init__(self, parent, text: str, level: str = "info", timeout: int = 3200) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._level = level
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {theme.INK}; font-size: 12.3px;")
        layout.addWidget(label)
        accent = {"ok": theme.OK, "warn": theme.AMBER, "error": theme.DANGER}.get(level, theme.BLUE)
        self.setStyleSheet(
            f"QWidget {{ background: {theme.CARD};"
            f" border: 1px solid {theme.LINE};"
            f" border-left: 3px solid {accent}; border-radius: 12px; }}")
        self.adjustSize()
        self.setFixedWidth(min(420, max(240, self.sizeHint().width() + 8)))
        self.adjustSize()
        self.show()
        self.raise_()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
