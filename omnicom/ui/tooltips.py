"""Hover tooltips: our own rounded popup, shown after one second (R-080/R-084).

Two requirements pull in different directions here, so this module owns both
halves of the behaviour:

*R-080 - timing.*  Qt's tooltip timer restarts on every mouse move inside a
widget, so on a 28 px icon button the hint can be pushed back indefinitely.  A
100 ms poll asks "which widget is under the cursor" instead (a few microseconds
per tick) and shows the text after exactly one second of *steady* hover.
An application-wide event filter was tried first and rejected: it made a
200-row group collapse four times slower (L-102, R-054).

*R-084 - looks.*  ``QToolTip`` is a plain rectangular top-level window, so the
``border-radius`` in the stylesheet only rounds the *inside*: the four corners
still show the platform's white block.  This module paints its own translucent
popup instead - a rounded card with a hairline border and a soft shadow, and
nothing at all outside that card.  With a wallpaper set the card is *liquid
glass* like the panes: it samples the same blurred layer at its own position and
washes it white, so the hint looks like the rest of the window instead of a solid
white bubble.

The only thing still done through the event filter is *suppressing* Qt's native
tooltip (``QEvent.ToolTip``), which otherwise appears 700 ms in and covers our
card with the square-cornered one.  That filter is a single ``isinstance`` test
per event and is only installed when the popup exists.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QHelpEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication, QWidget

from . import theme

#: How long the pointer must rest on a control before its tooltip appears.
DELAY_MS = 1000
#: Poll interval; small enough that a 1 s hover is measured to ~100 ms.
TICK_MS = 100
#: Text metrics of the popup card.
PAD_X = 10
PAD_Y = 7
RADIUS = 8
MAX_WIDTH = 380
#: Painted soft shadow around the card; also the window margin.
SHADOW = 6


class TipPopup(QWidget):
    """A rounded card that shows tooltip text; fully translucent around it."""

    def __init__(self) -> None:
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setObjectName("TipPopup")
        self._text = ""
        self._backdrop = None
        font = QFont(self.font())
        font.setPixelSize(max(11, theme.FONT_PX - 1))
        self.setFont(font)

    # -- content ------------------------------------------------------------
    def _metrics(self) -> QFontMetrics:
        return QFontMetrics(self.font())

    def _text_rect(self, width: int) -> QRectF:
        return QRectF(SHADOW + PAD_X, SHADOW + PAD_Y,
                      width - 2 * (SHADOW + PAD_X), 0.0)

    def _measure(self, text: str) -> tuple[int, int]:
        metrics = self._metrics()
        limit = MAX_WIDTH - 2 * (SHADOW + PAD_X)
        bounds = metrics.boundingRect(0, 0, limit, 10_000,
                                      Qt.TextWordWrap | Qt.AlignLeft, text)
        width = min(limit, max(bounds.width(), metrics.horizontalAdvance("M") * 3))
        height = metrics.boundingRect(0, 0, width, 10_000,
                                      Qt.TextWordWrap | Qt.AlignLeft, text).height()
        return (width + 2 * (SHADOW + PAD_X),
                height + 2 * (SHADOW + PAD_Y))

    def set_tip(self, text: str) -> None:
        self._text = text
        width, height = self._measure(text)
        self.resize(width, height)

    # -- liquid glass -------------------------------------------------------
    def _backdrop_for(self, anchor: QWidget):
        """Blurred wallpaper underneath the card, or ``None`` on the flat theme.

        ``theme.glass_layer()`` is the very layer the panes sample, so the hint
        matches them exactly; only the offset differs.  A card that would reach
        outside the window gets no backdrop - better a plain white card than a
        wrong slice.
        """
        window = anchor.window()
        if window is None or window is self or not theme.wallpaper():
            return None
        layer = theme.glass_layer(window.width(), window.height())
        if layer is None:
            return None
        origin = window.mapToGlobal(QPoint(0, 0))
        offset = QPoint(self.x() - origin.x(), self.y() - origin.y())
        if (offset.x() < 0 or offset.y() < 0
                or offset.x() + self.width() > layer.width()
                or offset.y() + self.height() > layer.height()):
            return None
        return layer.copy(QRect(offset.x(), offset.y(),
                                self.width(), self.height()))

    def set_backdrop(self, pixmap) -> None:
        self._backdrop = pixmap
        self.update()

    # -- painting -----------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        card = QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)

        # Soft shadow: a few expanding rounded rects.  Everything outside the
        # card is translucent, which is exactly what removes the white block the
        # user saw around the native tooltip (R-084).
        painter.setPen(Qt.NoPen)
        for step in range(SHADOW, 0, -1):
            colour = QColor(18, 26, 43, 12)
            painter.setBrush(colour)
            inflated = card.adjusted(-step, -step + 1, step, step + 1)
            painter.drawRoundedRect(inflated, RADIUS + step, RADIUS + step)

        if self._backdrop is not None and not self._backdrop.isNull():
            # Same recipe as the panes: the blurred slice, then a white wash.
            glass = QPainterPath()
            glass.addRoundedRect(card, RADIUS, RADIUS)
            painter.save()
            painter.setClipPath(glass)
            painter.drawPixmap(card.toRect(), self._backdrop)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, theme.GLASS_ALPHA + 30))
            painter.drawPath(glass)
            painter.restore()
        else:
            painter.setBrush(QColor(theme.CARD))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(card, RADIUS, RADIUS)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(18, 26, 43, 46), 1.0))
        painter.drawRoundedRect(card, RADIUS, RADIUS)

        painter.setPen(QColor(theme.INK))
        body = card.adjusted(PAD_X, PAD_Y, -PAD_X, -PAD_Y)
        painter.drawText(body, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter,
                         self._text)


class TooltipWatcher(QObject):
    """Shows ``widget.toolTip()`` after the pointer rests on it for ``delay_ms``."""

    shown = Signal(str)
    hidden = Signal()

    def __init__(self, parent: QObject | None = None, delay_ms: int = DELAY_MS) -> None:
        super().__init__(parent)
        self.delay_ms = int(delay_ms)
        self.popup = TipPopup()
        self._cursor_widget: QWidget | None = None
        self._since = 0.0
        self._elapsed = 0
        self._visible_for: QWidget | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(min(TICK_MS, max(20, self.delay_ms)))
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # -- seams (patched in tests; the only platform-dependent part) ---------
    def _cursor(self):
        return QCursor.pos()

    def _widget_at(self, position):
        return QApplication.widgetAt(position)

    @staticmethod
    def _tip(widget) -> str:
        try:
            return str(widget.toolTip() or "").strip()
        except (AttributeError, RuntimeError):
            return ""

    # -- polling ------------------------------------------------------------
    def _tick(self) -> None:
        widget = self._widget_at(self._cursor())
        text = self._tip(widget) if isinstance(widget, QWidget) else ""
        now = time.monotonic()
        if widget is not self._cursor_widget or not text:
            self._reset()
            if text:
                self._cursor_widget = widget
                self._since = now
            return
        # Real elapsed time, not the tick count: a busy GUI lets Qt coalesce the
        # timer, and counting ticks would then make the tooltip arrive late.
        self._elapsed = int((now - self._since) * 1000)
        if self._visible_for is None and now - self._since >= self.delay_ms / 1000.0:
            self._show(widget, text)

    def _reset(self) -> None:
        self._cursor_widget = None
        self._since = 0.0
        self._elapsed = 0
        if self._visible_for is not None:
            self._visible_for = None
            self.popup.hide()
            self.hidden.emit()

    def _show(self, widget: QWidget, text: str) -> None:
        self._visible_for = widget
        self.popup.set_tip(text)
        self.popup.move(self._place(widget, text))
        # Position first: the glass slice is taken at the card's real place.
        self.popup.set_backdrop(self.popup._backdrop_for(widget))
        self.popup.show()
        self.popup.raise_()
        self.shown.emit(text)

    def _place(self, widget: QWidget, text: str) -> QPoint:
        """Below-right of the cursor, kept inside the screen it is on."""
        point = self._cursor()
        origin = QPoint(point.x() + SHADOW + 12, point.y() + SHADOW + 16)
        screen = QApplication.screenAt(point) or QApplication.primaryScreen()
        if screen is None:
            return origin
        area = screen.availableGeometry()
        width, height = self.popup.width(), self.popup.height()
        x = min(origin.x(), area.right() - width)
        y = min(origin.y(), area.bottom() - height)
        return QPoint(max(area.left(), x), max(area.top(), y))

    def popup_visible(self) -> bool:
        return bool(self._visible_for is not None and self.popup.isVisible())


class TooltipFilter(QObject):
    """Swallow Qt's own tooltip events; our popup is the only one (R-084)."""

    def eventFilter(self, watched, event) -> bool:
        # One isinstance check per event and nothing else: this runs for every
        # Qt event in the process, so it must stay cheaper than the native timer
        # it replaces (L-102).
        return isinstance(event, QHelpEvent)


_watcher: TooltipWatcher | None = None
_filter: TooltipFilter | None = None


def install(app: QApplication | None = None) -> TooltipWatcher:
    """Create the single poller (and the native-tooltip suppressor) per process."""
    global _watcher, _filter
    if _watcher is not None:
        return _watcher
    application = app or QApplication.instance()
    _watcher = TooltipWatcher(application)
    if application is not None:
        _filter = TooltipFilter(application)
        application.installEventFilter(_filter)
    return _watcher


def watcher() -> TooltipWatcher | None:
    """The installed watcher, for tests and the window."""
    return _watcher


def hide() -> None:
    if _watcher is not None:
        _watcher._reset()
