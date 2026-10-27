"""Reusable Qt widgets that carry the OmniCOM glass look."""

from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRectF, QSize,
                            Qt, Property, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath,
                           QPen, QRadialGradient)
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QLayout, QSizePolicy, QVBoxLayout, QWidget)

from . import theme


class GlassFrame(QFrame):
    """Rounded translucent surface with a gradient border and soft shadow."""

    def __init__(self, parent=None, radius: int = 20, strong: bool = False,
                 shadow: bool = True) -> None:
        super().__init__(parent)
        self.radius = radius
        self.strong = strong
        self.setAttribute(Qt.WA_StyledBackground, False)
        if shadow:
            effect = QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(52)
            effect.setOffset(0, 20)
            effect.setColor(QColor(0, 0, 0, 170))
            self.setGraphicsEffect(effect)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)

        fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        top = 34 if self.strong else 22
        fill.setColorAt(0.0, QColor(255, 255, 255, top))
        fill.setColorAt(0.42, QColor(255, 255, 255, 9))
        fill.setColorAt(1.0, QColor(255, 255, 255, 6))
        painter.fillPath(path, QBrush(fill))

        # top specular sheen
        sheen = QRadialGradient(rect.left() + rect.width() * 0.18, rect.top() - 8,
                                max(rect.width() * 0.85, 220))
        sheen.setColorAt(0.0, QColor(255, 255, 255, 34))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillPath(path, QBrush(sheen))

        border = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        border.setColorAt(0.0, QColor(255, 255, 255, 82))
        border.setColorAt(0.35, QColor(255, 255, 255, 34))
        border.setColorAt(1.0, QColor(255, 255, 255, 16))
        pen = QPen(QBrush(border), 1.0)
        painter.setPen(pen)
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
    def isChecked(self) -> bool:  # noqa: N802 - Qt naming
        return self._checked

    def setChecked(self, value: bool, animate: bool = True) -> None:  # noqa: N802
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

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.setChecked(not self._checked)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self._w - 1, self._h - 1)
        radius = rect.height() / 2

        track = QLinearGradient(rect.topLeft(), rect.bottomRight())
        if self._checked:
            track.setColorAt(0.0, QColor(94, 234, 212, 220))
            track.setColorAt(1.0, QColor(167, 139, 250, 220))
        else:
            track.setColorAt(0.0, QColor(255, 255, 255, 26))
            track.setColorAt(1.0, QColor(255, 255, 255, 20))
        painter.setPen(QPen(QColor(255, 255, 255, 70 if self._checked else 34), 1))
        painter.setBrush(QBrush(track))
        painter.drawRoundedRect(rect, radius, radius)

        knob_d = rect.height() - 4
        x = rect.left() + 2 + self._pos * (rect.width() - knob_d - 4)
        knob = QRectF(x, rect.top() + 2, knob_d, knob_d)
        glow = QRadialGradient(knob.center(), knob_d * 0.95)
        glow.setColorAt(0.0, QColor(255, 255, 255, 255))
        glow.setColorAt(1.0, QColor(214, 226, 242, 255))
        painter.setPen(QPen(QColor(0, 0, 0, 70), 1))
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

    def isChecked(self) -> bool:  # noqa: N802
        return self.switch.isChecked()

    def setChecked(self, value: bool) -> None:  # noqa: N802
        self.switch.setChecked(value, animate=False)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
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

    KINDS = {
        "idle": ("#6D7997", "rgba(255, 255, 255, 16)", "rgba(255, 255, 255, 30)"),
        "ok": ("#5EEAD4", "rgba(94, 234, 212, 32)", "rgba(94, 234, 212, 120)"),
        "retry": ("#FBBF24", "rgba(251, 191, 36, 30)", "rgba(251, 191, 36, 110)"),
        "error": ("#F87171", "rgba(248, 113, 113, 30)", "rgba(248, 113, 113, 110)"),
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
        text_color = "#D8FFF6" if kind == "ok" else "#FFE9C2" if kind == "retry" else \
            "#FFD9D9" if kind == "error" else theme.INK2
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
        self.setStyleSheet("background: rgba(255, 255, 255, 14); border: 0;")


class FlowLayout(QLayout):
    """Left-to-right wrapping layout (used by the keyword chips)."""

    def __init__(self, parent=None, spacing: int = 5) -> None:
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._layout(QRectF(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._layout(QRectF(rect), apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
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
        label.setStyleSheet(f"color: #D9FFF8; font-family: {theme.MONO_FONT}; font-size: 11.5px;")
        close = QLabel("×")
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet("color: rgba(217,255,248,150); font-size: 13px;")
        close.mouseReleaseEvent = lambda _event: self.removed.emit(self.text)
        layout.addWidget(label)
        layout.addWidget(close)
        self.setStyleSheet(
            "QWidget { background: rgba(94, 234, 212, 32);"
            " border: 1px solid rgba(94, 234, 212, 82); border-radius: 10px; }")


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
        label.setStyleSheet("color: #F2F6FF; font-size: 12.3px;")
        layout.addWidget(label)
        accent = {"ok": theme.TEAL, "warn": theme.AMBER, "error": theme.DANGER}.get(level, theme.BLUE)
        self.setStyleSheet(
            f"QWidget {{ background: rgba(16, 20, 30, 236);"
            f" border: 1px solid rgba(255, 255, 255, 40);"
            f" border-left: 3px solid {accent}; border-radius: 12px; }}")
        self.adjustSize()
        self.setFixedWidth(min(420, max(240, self.sizeHint().width() + 8)))
        self.adjustSize()
        self.show()
        self.raise_()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
