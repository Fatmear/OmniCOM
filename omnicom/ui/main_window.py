"""Main window: frameless light shell, dialogue-driven menus, plugin tabs."""

from __future__ import annotations

import codecs
import contextlib
import ctypes
import ctypes.wintypes
import html
import json
import os
import re
import subprocess
import sys
import time

from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSignalBlocker,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QIcon,
    QImage,
    QIntValidator,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .. import __version__, control, paths, power
from ..commands import CommandStore
from ..i18n import language, set_language, tr
from ..plugins import PluginHost
from . import theme
from .commands_panel import CommandsPanel
from .dialogs import AboutDialog, DocsDialog, PreferencesDialog
from .log_view import LogView
from .search_panel import SearchPanel
from .send_panel import SendPanel
from .settings_store import UiSettings, open_settings
from .tooltips import install as install_tooltips
from .update_dialog import UpdateDialog, UpdateWatcher
from .widgets import (
    Badge,
    CaptionStrip,
    Combo,
    GlassFrame,
    GlyphButton,
    ScrollBottomPill,
    SectionTitle,
    StatusPill,
    SwitchRow,
    Toast,
)
from .widgets import Button as QPushButton
from .workers import SerialBridge


def ui_settings() -> UiSettings:
    """Persisted UI state: INI files beside the data folder (R-088).

    Never the registry.  ``HKCU\\Software\\OmniCOM`` is machine-wide, so a freshly
    unzipped portable copy inherited the accent and wallpaper of whatever ran
    before it - exactly what a portable build must not do (L-121).  Preferences
    go to ``settings.ini`` (shared by all windows), per-window state to
    ``window-<n>.ini``, so several windows run independently.
    """
    return open_settings()


BAUD_RATES = ["9600", "19200", "38400", "57600", "115200", "230400", "460800",
              "921600", "1000000", "1500000", "2000000", "3000000"]
PARITIES = [("N", "None"), ("E", "Even"), ("O", "Odd"), ("M", "Mark"), ("S", "Space")]
FLOW_KEYS = [("none", "flow.none"), ("rtscts", "flow.rtscts"), ("xonxoff", "flow.xonxoff")]

# ------------------------------------------------------- native resize border
#: ``WM_NCHITTEST`` and the ``HT*`` results it may return.  A frameless window
#: has no system resize border, so the edge grab has to be reported by hand -
#: which is what gives the same feel as any other application (R-204).
WM_NCHITTEST = 0x0084
#: ``WM_GETMINMAXINFO``: the system asks before a sizing gesture, and a
#: frameless window has to answer or it can be dragged smaller than its layout
#: allows (R-035 needs 980x620).
WM_GETMINMAXINFO = 0x0024
#: ``WM_NCCALCSIZE``: the frame a ``WS_THICKFRAME`` window would draw.  The window
#: needs that style to be *resizable*, but must not show the frame - so the
#: non-client area is recalculated away (R-205).
WM_NCCALCSIZE = 0x0083
#: ``WM_SETCURSOR``: without handling it Qt sets the arrow in the client area and
#: the system never draws the resize cursor, so the edge looked inert even after
#: the hit test was correct (R-205 / L-268).
WM_SETCURSOR = 0x0020
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
#: Standard cursor identifiers (``IDC_*``) for the sizing arrows.  Verified by
#: loading each one and reading the returned handle - mixing these up produces
#: the wrong arrow (a left edge showing a corner cursor):
#:   IDC_SIZEWE 32644 (left-right), IDC_SIZENS 32645 (up-down),
#:   IDC_SIZENWSE 32642, IDC_SIZENESW 32643.
IDC_SIZEWE, IDC_SIZENS, IDC_SIZENWSE, IDC_SIZENESW = 32644, 32645, 32642, 32643


class _MinMaxInfo(ctypes.Structure):
    """``MINMAXINFO`` from ``winuser.h``.

    ``ctypes.wintypes`` does not ship it, and without the real layout the
    ``ptMinTrackSize`` write would land in the wrong offset - the earlier
    implementation silently fell through to ``except`` for that reason (L-265).
    """

    _fields_ = [
        ("ptReserved", ctypes.wintypes.POINT),
        ("ptMaxSize", ctypes.wintypes.POINT),
        ("ptMaxPosition", ctypes.wintypes.POINT),
        ("ptMinTrackSize", ctypes.wintypes.POINT),
        ("ptMaxTrackSize", ctypes.wintypes.POINT),
    ]


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    """``NCCALCSIZE_PARAMS`` from ``winuser.h`` (``rgrc[3]`` + ``WINDOWPOS*``)."""

    _fields_ = [
        ("rgrc", ctypes.wintypes.RECT * 3),
        ("lppos", ctypes.c_void_p),
    ]



def bytes_per_ms(baud: int) -> float:
    return max(0.5, baud / 10.0 / 1000.0)


def _history_from(raw) -> list[str]:
    """Decode the persisted send history; a broken value must not crash startup."""
    text = str(raw or "")
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [str(entry) for entry in data]


#: "USB-SERIAL CH340 (COM139)" / "n/a" / "" -> "USB-SERIAL CH340".  The port
#: name is added back by the caller, so the parenthesis is pure repetition.
_PORT_DETAIL_RE = re.compile(r"\s*\((?:COM|com)\d+\)\s*$")
#: "CH340 - COM139" -> "CH340".
_PORT_NAME_TAIL_RE = re.compile(r"\s*[-–]\s*(?:COM|com)\d+\s*$")


def _port_detail(description) -> str:
    """A description with the port name removed and the noise stripped."""
    text = str(description or "").strip()
    if not text or text.lower() in ("n/a", "na", "none", "unknown"):
        return ""
    text = _PORT_DETAIL_RE.sub("", text).strip()
    # Some drivers put the name after a dash instead: "CH340 - COM139".
    text = _PORT_NAME_TAIL_RE.sub("", text).strip()
    return text


def _bounded(raw, low: int, high: int, default: int) -> int:
    """Read an int setting, clamped; a missing/broken value falls back (R-116)."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return int(default)
    return max(int(low), min(int(high), value))


def human_bytes(value: float) -> str:
    value = float(value or 0)
    if value < 1024:
        return f"{int(value)} B"
    if value < 1024 ** 2:
        return f"{value / 1024:.1f} KB"
    if value < 1024 ** 3:
        return f"{value / 1048576:.2f} MB"
    return f"{value / 1073741824:.2f} GB"


def human_size(value: float) -> str:
    """Compact byte count for the top bar (3 significant-ish characters)."""
    value = float(value or 0)
    if value < 1024:
        return f"{int(value)} B"
    if value < 1024 ** 2:
        return f"{value / 1024:.1f} K"
    if value < 1024 ** 3:
        return f"{value / 1048576:.2f} M"
    return f"{value / 1073741824:.2f} G"


class Aurora(QWidget):
    """Page backdrop: flat colour by default, optional user wallpaper (R-020).

    Square edges on purpose: a rounded backdrop left the window with a square
    title bar and a rounded bottom, which looked unfinished (R-044).  When the
    user picks a wallpaper it is scaled to cover and then veiled, so captions
    that sit straight on the page stay readable (R-070).

    The page also paints the window's own 1 px dark edge (R-138) and the soft
    drop shadow just inside it (R-139).  The window is frameless and translucent,
    so without them the pale backdrop simply faded into a white desktop and the
    shape of the window was impossible to read.
    """

    def page_rect(self) -> QRect:
        """The pale plate: the widget minus the shadow ring (R-139)."""
        inset = int(getattr(self, "shadow_px", theme.WINDOW_SHADOW_PX))
        if inset <= 0:
            return self.rect()
        page = self.rect().adjusted(inset, inset, -inset, -inset)
        return page if page.isValid() else self.rect()

    def paintEvent(self, event) -> None:
        # ``end()`` in a ``finally`` is not cosmetic: if anything below raises
        # while the painter is still active, Qt destroys the QPaintDevice with
        # a live painter attached and the process dies with
        # ``QPaintDevice: Cannot destroy paint device that is being painted``
        # instead of showing a traceback (L-163).
        painter = QPainter(self)
        try:
            painter.setPen(Qt.NoPen)
            page = self.page_rect()
            if not page.contains(self.rect().center()):
                page = self.rect()
            if page != self.rect():
                self._paint_shadow(painter, page)
            painter.fillRect(page, QColor(theme.BG))
            self._paint_wallpaper(painter, page)
            self._paint_edge(painter, page)
        finally:
            painter.end()

    def _paint_shadow(self, painter: QPainter, page) -> None:
        """Soft drop shadow under the whole shell (R-139).

        Painted, not a ``QGraphicsDropShadowEffect``: an effect on the central
        widget of a translucent top-level window is not composited, and a real
        DWM shadow is unavailable because the window carries
        ``WA_TranslucentBackground``.  Filled rounded rects are stacked from the
        outside in, so a pixel's darkness is proportional to how many rings
        reach it - that reads as a blur instead of a hard band, and it is the
        same trick the tooltip card uses.
        """
        steps = theme.WINDOW_SHADOW_PX
        ink = QColor(theme.WINDOW_SHADOW)
        if steps <= 0 or not ink.isValid():
            return
        image = self._shadow_image(page)
        if image is not None:
            painter.drawImage(0, 0, image)

    def _shadow_image(self, page):
        """Cache the ring bitmap: a window repaint must not re-blend 14 rings."""
        key = (self.width(), self.height(), page.left(), page.top(),
               theme.WINDOW_SHADOW_PX, theme.WINDOW_SHADOW_ALPHA,
               theme.WINDOW_SHADOW)
        cached = getattr(self, "_shadow_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        steps = theme.WINDOW_SHADOW_PX
        ink = QColor(theme.WINDOW_SHADOW)
        image = QImage(max(1, self.width()), max(1, self.height()),
                       QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            # No vertical offset: ``_apply_window_shadow`` reserves exactly
            # ``WINDOW_SHADOW_PX`` on every side, so a downward-biased ring would
            # be clipped at the bottom and leave a transparent band at the top.
            base = QRectF(page)
            for step in range(steps, 0, -1):
                ink.setAlpha(theme.WINDOW_SHADOW_ALPHA)
                painter.setBrush(QBrush(ink))
                painter.drawRoundedRect(base.adjusted(-step, -step, step, step),
                                        step, step)
        finally:
            painter.end()
        self._shadow_cache = (key, image)
        return image

    def _paint_wallpaper(self, painter: QPainter, target) -> None:
        """Blit the prepared page photo; never resample it here (R-163).

        ``theme.page_cover()`` caches the cover-cropped, veiled pixmap per size.
        Doing the crop and the full-page white fill in this method cost ~14 ms at
        1440p *per repaint*, which is what made maximising and restoring the
        window stutter: the whole backdrop is invalidated on both transitions.
        """
        cover = theme.page_cover(target.width(), target.height())
        if cover is None:
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(target.topLeft(), cover)

    def _paint_edge(self, painter: QPainter, target) -> None:
        """Draw the window outline *inside* the page, one pixel wide.

        Painted instead of styled: ``QWidget#Root`` carries no border of its own,
        and a QSS border would eat into the layout margins the content relies
        on.  Half-pixel offsets keep the line crisp on a 1 px pen.

        The rect is built from ``target.topLeft()``, not from (0, 0): with the
        shadow ring the page no longer starts at the widget origin, and an
        absolute rect drew the outline *outside* the page - right on top of the
        shadow (L-161).
        """
        width = theme.WINDOW_EDGE_PX
        if width <= 0:
            return
        painter.setRenderHint(QPainter.Antialiasing, False)
        ink = QColor(theme.WINDOW_EDGE)
        ink.setAlpha(theme.WINDOW_EDGE_ALPHA)
        pen = QPen(ink, width)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        inset = width / 2.0
        painter.drawRect(QRectF(target.left() + inset, target.top() + inset,
                                target.width() - width,
                                target.height() - width))


class BrandMark(QWidget):
    """The little rounded tile that stands in for the app logo.

    Drawn with QPainter so it is font independent, has real antialiasing at any
    size and matches the accent gradient used by primary buttons.
    """

    def __init__(self, size: int = 38, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # No background of its own: only the rounded tile is painted, so the
        # four corners stay transparent instead of showing a square plate.
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0.0, QColor("#10A794"))
            gradient.setColorAt(0.55, QColor("#4884F0"))
            gradient.setColorAt(1.0, QColor("#7455F5"))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(rect, self.width() * 0.28, self.width() * 0.28)

            # White arc + baseline: a compact "serial line" mark.
            pen = QPen(QColor(255, 255, 255, 235), max(1.6, self.width() * 0.075))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            inset = self.width() * 0.27
            painter.drawArc(QRectF(inset, inset, self.width() - inset * 2,
                                   self.height() - inset * 2), 30 * 16, 240 * 16)
            baseline = self.height() * 0.7
            painter.drawLine(QPointF(self.width() * 0.3, baseline),
                             QPointF(self.width() * 0.7, baseline))
        finally:
            painter.end()


class TitleBar(GlassFrame):
    """Brand, page tabs (base + plugins), state, counters, window controls."""

    def __init__(self, window: OmniWindow) -> None:
        super().__init__(None, radius=16, strong=True, shadow=True)
        self.window_ref = window
        self._drag: QPoint | None = None
        self.setFixedHeight(62)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 0, 12, 0)
        layout.setSpacing(12)

        # ---- brand (the real logo, with a transparent background) ----
        brand = QHBoxLayout()
        brand.setSpacing(10)
        # Painted mark, not an image: the generated logo looked bad at 38 px and
        # an image cannot follow the theme (R-045).
        self.logo = BrandMark(38, self)
        brand.addWidget(self.logo)

        names = QVBoxLayout()
        # The two lines must read as one block, vertically centred on the logo
        # (R-100).  A QLabel box is as tall as ascent + descent, so an
        # all-caps line keeps 4-6 px of empty space under its glyphs: the pair
        # looked ~20 px apart and sat ~2 px high.  Pinning each box to the
        # glyph height makes "spacing" the real gap; the two stretches centre
        # the block in the 62 px bar.
        names.setSpacing(2)
        # Glyph ink sits ~1 px below the centre of a Qt line box, so the
        # block would look 1 px low; this margin cancels exactly that.
        names.setContentsMargins(0, 0, 0, 2)
        names.addStretch(1)
        self.title = QLabel(tr("app.name"))
        self.title.setStyleSheet("font-size: 15px; font-weight: 650; letter-spacing: .5px;")
        self.title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        names.addWidget(self.title)
        self.subtitle = QLabel(tr("app.tagline").upper())
        self.subtitle.setStyleSheet(
            f"color: {theme.DIM}; font-size: 8.5px; letter-spacing: 1.2px;")
        self.subtitle.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.subtitle.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        names.addWidget(self.subtitle)
        names.addStretch(1)
        self._fit_brand_lines()
        brand.addLayout(names)
        layout.addLayout(brand)

        layout.addWidget(self._vline())

        # ---- tabs: built-in pages, then one per plugin ----
        self.tabs = QButtonGroup(self)
        self.tabs.setExclusive(True)
        self.tab_box = QFrame()
        self.tab_box.setObjectName("TabBox")
        self.tab_box.setFixedHeight(38)
        self.tab_box.setStyleSheet(
            f"QFrame#TabBox {{ background: {theme.WASH};"
            f" border: 1px solid {theme.LINE_SOFT};"
            f" border-radius: {theme.RADIUS_CARD}px; }}")
        self.tab_layout = QHBoxLayout(self.tab_box)
        self.tab_layout.setContentsMargins(4, 4, 4, 4)
        self.tab_layout.setSpacing(2)
        layout.addWidget(self.tab_box, 0)

        layout.addStretch(1)

        # ---- right cluster ----
        self.status_chip = StatusPill(self, tr("status.chip.idle"))
        layout.addWidget(self.status_chip)

        self.counter = QLabel()
        self.counter.setTextFormat(Qt.RichText)
        self.counter.setStyleSheet(
            f"color: {theme.DIM}; font-size: 11.5px; font-family: {theme.MONO_FONT};")
        layout.addWidget(self.counter)
        self.set_counters(0, 0)

        layout.addWidget(self._vline())

        self.btn_menu = GlyphButton(GlyphButton.MENU, self)
        self.btn_menu.setToolTip(tr("menu.title"))
        self.btn_menu.clicked.connect(self._show_menu)
        layout.addWidget(self.btn_menu)

        self.btn_sidebar = GlyphButton(GlyphButton.SIDEBAR, self)
        self.btn_sidebar.setToolTip(tr("win.sidebar"))
        self.btn_sidebar.clicked.connect(window.toggle_sidebar)
        layout.addWidget(self.btn_sidebar)

        self.btn_min = GlyphButton(GlyphButton.MINIMIZE, self)
        self.btn_min.setToolTip(tr("win.minimize"))
        self.btn_min.clicked.connect(window.minimize_with_motion)
        layout.addWidget(self.btn_min)

        self.btn_max = GlyphButton(GlyphButton.MAXIMIZE, self)
        self.btn_max.setToolTip(tr("win.maximize"))
        self.btn_max.clicked.connect(window.toggle_maximize)
        layout.addWidget(self.btn_max)

        self.btn_close = GlyphButton(GlyphButton.CLOSE, self, danger=True)
        self.btn_close.setToolTip(tr("win.close"))
        self.btn_close.clicked.connect(window.close)
        layout.addWidget(self.btn_close)

        self.tab_buttons: dict[str, QPushButton] = {}

    # ------------------------------------------------------------------ tabs
    def set_tabs(self, entries: list[tuple[str, str, str]]) -> None:
        """Rebuild the tab strip.  ``entries`` = ``(key, label, tooltip)``."""
        for button in list(self.tab_buttons.values()):
            self.tabs.removeButton(button)
            button.setParent(None)
            button.deleteLater()
        self.tab_buttons.clear()
        while self.tab_layout.count():
            item = self.tab_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        for index, (key, label, tip) in enumerate(entries):
            button = QPushButton(label)
            button.setObjectName("Seg")
            button.setFixedHeight(28)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty("page", key)
            button.setToolTip(tip)
            # A squeezed QPushButton elides its own text away, so the tab must
            # refuse to shrink below its natural width (R-035).
            button.setMinimumWidth(button.sizeHint().width())
            if index == 0:
                button.setChecked(True)
            self.tabs.addButton(button)
            self.tab_layout.addWidget(button)
            self.tab_buttons[key] = button

    def select_tab(self, key: str) -> None:
        button = self.tab_buttons.get(key)
        if button is not None:
            button.setChecked(True)

    # ------------------------------------------------------------------ menu
    def _add_update_action(self, menu: QMenu):
        window = self.window_ref
        if not window.update_available():
            return menu.addAction(tr("menu.update"), window.open_update)
        action = QWidgetAction(menu)
        action.setText(tr("menu.update.new"))
        label = QLabel(
            html.escape(tr("menu.update.new")).replace(
                "NEW", f'<span style="color:{theme.DANGER}">NEW</span>'))
        label.setTextFormat(Qt.RichText)
        label.setAttribute(Qt.WA_TransparentForMouseEvents)
        action.setDefaultWidget(label)
        action.setToolTip(tr("menu.update.tip"))
        action.triggered.connect(window.open_update)

        def highlight(active) -> None:
            background = theme.TEAL_SOFT if active is action else "transparent"
            label.setStyleSheet(
                f"padding: 7px 20px; border-radius: {theme.RADIUS_CTRL}px;"
                f"background: {background}; color: {theme.INK};")

        highlight(None)
        menu.hovered.connect(highlight)
        menu.addAction(action)
        return action

    def _show_menu(self) -> None:
        window = self.window_ref
        menu = QMenu(self)
        menu.setStyleSheet(theme.menu_stylesheet())

        menu.addAction(tr("menu.preferences"), window.open_preferences)

        plugin_menu = menu.addMenu(tr("plugin.menu"))
        plugin_menu.addAction(tr("store.title"), window.open_plugin_store)
        plugin_menu.addAction(tr("plugin.reload"), window.reload_plugins)
        plugin_menu.addAction(tr("plugin.opendir"), window.open_plugin_dir)
        if window.plugins.loaded():
            plugin_menu.addSeparator()
            for info in window.plugins.loaded():
                plugin_menu.addAction(
                    info.name(), lambda key=info.id: window.switch_page(f"plugin:{key}"))
        else:
            plugin_menu.addSeparator()
            empty = plugin_menu.addAction(tr("plugin.none"))
            empty.setEnabled(False)

        help_menu = menu.addMenu(tr("menu.help"))
        help_menu.addAction(tr("menu.docs"), window.open_docs)
        help_menu.addAction(tr("menu.about"), window.open_about)

        menu.addSeparator()
        # Opening the menu is the natural moment to ask: if the first background
        # check failed (offline at launch), this retries without the user having
        # to wait for a timer (R-107).
        if window.update_watcher.last is None and not window.update_watcher.busy:
            window.update_watcher.start()
        self._add_update_action(menu)

        menu.addSeparator()
        menu.addAction(tr("menu.exit"), window.close)

        point = self.btn_menu.mapToGlobal(QPoint(0, self.btn_menu.height() + 6))
        menu.exec(point)

    # ------------------------------------------------------------------ parts
    @staticmethod
    def _vline() -> QFrame:
        line = QFrame()
        line.setFixedSize(1, 22)
        line.setStyleSheet(f"background: {theme.LINE}; border: 0;")
        return line

    def set_counters(self, rx: int, tx: int) -> None:
        self.counter.setText(
            f'<span style="color:{theme.DIM}">RX</span> '
            f'<span style="color:{theme.TEAL}">{human_size(rx)}</span>'
            f'<span style="color:{theme.DIM}"> &nbsp;·&nbsp; </span>'
            f'<span style="color:{theme.DIM}">TX</span> '
            f'<span style="color:{theme.VIOLET}">{human_size(tx)}</span>')

    def sync_max_button(self) -> None:
        maximised = self.window_ref.isMaximized()
        self.btn_max.set_glyph(
            GlyphButton.RESTORE if maximised else GlyphButton.MAXIMIZE)
        self.btn_max.setToolTip(tr("win.restore") if maximised else tr("win.maximize"))

    def _fit_brand_lines(self) -> None:
        """Pin each brand line to its glyph height so the pair is truly centred.

        ``QLabel`` reserves ascent + descent, but "OMNICOM" and an upper-case
        tagline have no descender: the spare rows under each line both pushed
        the block visually high and made the two lines look far apart (R-100).
        """
        for label in (self.title, self.subtitle):
            label.ensurePolished()
            metrics = label.fontMetrics()
            # ``capHeight`` is right for "OmniCOM"; the Chinese tagline fills
            # more of the em box, so the glyph outline wins when it is taller.
            ink = metrics.tightBoundingRect(label.text())
            height = max(metrics.capHeight(), ink.height())
            # +1 keeps the antialiased bottom row from being clipped.
            label.setFixedHeight(max(1, height) + 1)

    def retranslate(self) -> None:
        self.title.setText(tr("app.name"))
        self.subtitle.setText(tr("app.tagline").upper())
        self._fit_brand_lines()
        self.btn_menu.setToolTip(tr("menu.title"))
        self.btn_sidebar.setToolTip(tr("win.sidebar"))
        self.btn_min.setToolTip(tr("win.minimize"))
        self.btn_close.setToolTip(tr("win.close"))
        self.sync_max_button()

    # -- window drag -------------------------------------------------------
    #: How close to the top of the screen the pointer must be for the window to
    #: snap to maximized - the same few pixels Windows uses (R-140).
    SNAP_EDGE = 2
    #: Distance the pointer must be below the top of the screen before a
    #: maximized window may be restored.  Without it the snap that just happened
    #: would undo itself on the very next mouse move.
    SNAP_RELEASE = 40

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag = (event.globalPosition().toPoint()
                          - self.window_ref.frameGeometry().topLeft())

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None or not (event.buttons() & Qt.LeftButton):
            return
        window = self.window_ref
        cursor = event.globalPosition().toPoint()
        top = self._snap_top(cursor)
        # "Far enough from the top edge that a restore/snap is unambiguous."
        released = top is None or cursor.y() > top + self.SNAP_RELEASE

        if window.isMaximized() or window.isFullScreen():
            # Maximized: there is nothing to move.  The gesture only becomes a
            # restore once the pointer has travelled down out of the top band,
            # which is exactly how Windows behaves and also stops the snap we
            # just performed from immediately undoing itself (R-140).
            if released:
                self._restore_from_max(cursor)
            return
        if top is not None and cursor.y() <= top + self.SNAP_EDGE:
            self._snap_to_max(cursor)
            return
        window.move(cursor - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.window_ref.toggle_maximize()

    # -- snap helpers ------------------------------------------------------
    def _snap_top(self, cursor: QPoint) -> int | None:
        """Top edge of the available area of the screen under the pointer.

        The *available* rect, not ``geometry()``: the taskbar may sit at the top,
        and maximizing must not put the title bar underneath it.
        """
        screen = QGuiApplication.screenAt(cursor) or self.window_ref.screen()
        if screen is None:
            return None
        return screen.availableGeometry().top()

    def _snap_to_max(self, cursor: QPoint) -> None:
        """Pointer reached the top of the screen: maximize (Aero Snap, R-140)."""
        window = self.window_ref
        # ``showMaximized`` is the same call ``toggle_maximize`` makes, so
        # ``changeEvent`` still syncs the button glyph and the shadow ring.
        window.showMaximized()
        # Keep the grab point inside the title bar, so dragging back down picks
        # the window up where the user is holding it.
        grab_y = self._drag.y() if self._drag is not None else 20
        self._drag = QPoint(window.width() // 2,
                            max(6, min(grab_y, self._title_height())))

    def _restore_from_max(self, cursor: QPoint) -> None:
        """Restore a maximized window *under the pointer* (R-140).

        The horizontal position is kept as a ratio of the screen, so the title
        bar lands where the user is already holding it instead of the window
        teleporting to a corner - the old code jumped to a hard-coded 50 % /
        30 px grab point, which read as "the window lost my mouse".
        """
        window = self.window_ref
        grab_y = self._drag.y() if self._drag is not None else 20
        screen = QGuiApplication.screenAt(cursor) or window.screen()
        ratio = 0.5
        if screen is not None:
            area = screen.availableGeometry()
            if area.width() > 0:
                ratio = (cursor.x() - area.x()) / float(area.width())
        ratio = max(0.0, min(1.0, ratio))
        window.showNormal()
        width = max(1, window.width())
        grab_x = int(width * ratio)
        grab_y = max(6, min(grab_y, self._title_height()))
        window.move(cursor.x() - grab_x, cursor.y() - grab_y)
        self._drag = QPoint(grab_x, grab_y)

    def _title_height(self) -> int:
        """Height of the bar being dragged, so the grab point stays on it."""
        # ``self`` *is* the title bar - reaching for ``window_ref.titlebar``
        # would work too, but reading the own geometry cannot go stale when the
        # window is rebuilt (L-161).
        return max(12, self.height() - 6)


class OmniWindow(QMainWindow):
    def __init__(self, demo: bool = False, command_path: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"OmniCOM {__version__}")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowMinMaxButtonsHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(980, 620)
        self.resize(1440, 900)

        icon_path = paths.resource_file("assets", "omnicom.ico")
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        self.settings = ui_settings()
        set_language(str(self.settings.value("ui/language", "zh")))
        # Theme choices must be in place before any widget is constructed:
        # several widgets read theme tokens while they build their own QSS.
        theme.set_accent(str(self.settings.value("ui/accent", theme.DEFAULT_ACCENT)))
        theme.set_wallpaper(str(self.settings.value("ui/wallpaper", "")) or None)
        # Fonts must be in place *before* the stylesheet is built below, and
        # before the log widget exists (it reads theme.log_px()).
        theme.set_ui_family(str(self.settings.value("ui/font_family", "") or ""))
        theme.set_ui_font_px(int(self.settings.value("ui/font_px", theme.FONT_PX) or 0)
                             or theme.FONT_PX)
        theme.set_log_family(str(self.settings.value("display/log_family", "") or ""))
        theme.set_log_px(int(self.settings.value("display/log_px", theme.FONT_PX) or 0)
                         or theme.FONT_PX)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.app_stylesheet())
        self.bridge = SerialBridge(demo=demo)
        self.command_store = CommandStore(command_path)
        self.demo = demo

        # rx pipeline state
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._pending_text = ""
        self._pending_ts = 0.0
        self._hex_partial = b""
        #: Timestamp of the first byte of the incomplete HEX row, so a column
        #: switch can re-render that row with the time it really arrived (R-201).
        self._hex_ts = 0.0
        #: Raw bytes received while HEX view is on.  ``_on_hex_columns()``
        #: replays them so switching a column keeps the history instead of
        #: wiping it (R-202).  Capped, because the log itself is capped (R-081).
        self._hex_session = bytearray()
        self._hex_session_ts = 0.0
        self.HEX_SESSION_MAX = 1 << 20          # 1 MB, the same budget as the ring
        self._hex_offset = 0
        self._appended = 0
        self._status: dict = {}
        # The transfer worker now lives inside the transfer plugin (R-218); the
        # window only reaches it through the "transfer" service.
        self._search_overlay: QWidget | None = None
        self._toasts: list[Toast] = []
        self._page_keys: list[str] = []
        self._plugin_pages: dict[str, QWidget] = {}
        self._saved_split = False
        self._rx_queue: list[tuple[float, bytes]] = []
        # Window show/hide motion (R-085): None = idle, otherwise the running
        # QParallelAnimationGroup, plus the flag that says "we are on our way
        # out", so a second click cannot start a second fade.
        self._motion: QParallelAnimationGroup | None = None
        #: Software-update state.  The check is deliberately late and cheap: it
        #: must never compete with opening a port (R-109).
        self.update_watcher = UpdateWatcher(self)
        self.update_watcher.found.connect(self._on_update_found)
        self.update_watcher.failed.connect(self._on_update_failed)
        self._update_info: dict | None = None
        self._minimizing = False
        self._minimize_pending = False
        self._was_minimized = False
        #: Set once ``closeEvent`` starts, so a re-entrant close (taskbar menu +
        #: Alt+F4 while ``processEvents()`` pumps) cannot run the save/unload
        #: bookkeeping a second time (R-159).
        self._closing = False
        self._rx_timer = QTimer(self)
        self._rx_timer.setSingleShot(True)
        self._rx_timer.setInterval(28)          # ~36 fps, cheaper than per-chunk
        self._rx_timer.timeout.connect(self._flush_rx)

        # ---- CLI control channel (R-112).  A serial port has one owner, so the
        # CLI does not open it at all: it asks this window to, and every byte it
        # sends or reads is the window's own traffic - the log shows it, the
        # counters count it, and closing from the CLI closes it here too.
        self.control_server = control.Server(slot=self.settings.slot.index)
        self._control_jobs: dict[str, dict] = {}
        #: Rolling copy of everything received, addressed by an absolute byte
        #: counter, so a CLI read that starts a moment after its send still sees
        #: the answer instead of losing the gap between two requests.
        self._control_seq = 0
        self._control_ring = bytearray()
        self.CONTROL_RING = 1 << 20          # 1 MB, ~2 s at 4 Mbaud
        self._control_timer = QTimer(self)
        self._control_timer.setInterval(25)
        self._control_timer.timeout.connect(self._poll_control)
        if self.control_server.start():
            self._control_timer.start()

        # One app-wide tooltip watcher: 1 s of steady hover, never restarted by
        # a mouse move (R-080).
        self.tooltips = install_tooltips()

        self.plugins = PluginHost(notify=self.notify, log_line=self._plugin_log,
                                  send_bytes=self._plugin_send, parent=self,
                                  port=lambda: self.bridge.manager)

        # Plugins are loaded **before** the UI is built (R-218): 终端 and 文件
        # 传输 are plugins now, so the stack has to ask the host for those pages
        # instead of constructing them itself.
        self.plugins.load()
        self._build_ui()
        self._wire()
        self._load_plugins()
        self._restore_settings()
        self._apply_responsive()
        QTimer.singleShot(240, self._push_opts)

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        central = Aurora(self)
        central.setObjectName("Root")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        self._root_layout = root
        self._apply_window_shadow()
        root.setSpacing(10)

        self.titlebar = TitleBar(self)
        root.addWidget(self.titlebar)

        # A splitter, not a fixed-width HBox: the user drags either side column
        # to whatever width they want (R-047).
        self.body_split = QSplitter(Qt.Horizontal)
        self.body_split.setObjectName("BodySplit")
        self.body_split.setChildrenCollapsible(False)
        self.body_split.setHandleWidth(6)
        self.body_split.setStyleSheet(
            "QSplitter#BodySplit::handle { background: transparent; }")
        root.addWidget(self.body_split, 1)

        self.rail = self._build_rail()
        self.body_split.addWidget(self.rail)

        self.stack = QStackedWidget()
        # QStackedWidget.minimumSizeHint() is the widest *page* hint (1243 px
        # here), and a QSplitter treats that as a hard floor - which forced both
        # side columns down to their minimums and made a symmetric default
        # impossible. An explicit minimum overrides the hint.
        self.stack.setMinimumWidth(400)
        self.stack.addWidget(self._build_terminal_page())
        # 终端 and 文件传输 are plugins (R-218).  When a plugin is disabled the
        # tab stays but shows a short explanation instead of vanishing, so the
        # user can see *why* the page is gone and where to turn it back on.
        #
        # NB: do **not** connect ``keyBytes`` here.  The terminal plugin already
        # routes its keys through ``api.send`` itself; a second connection sent
        # every keystroke twice ("ls" arrived as "llss", L-292).
        self.console = self._plugin_page("terminal")
        self.transfer = self._plugin_page("transfer")
        self.stack.addWidget(self.console)
        self.stack.addWidget(self.transfer)
        self.body_split.addWidget(self.stack)

        self.sidebar = self._build_sidebar()
        self.body_split.addWidget(self.sidebar)

        self.body_split.setStretchFactor(0, 0)
        self.body_split.setStretchFactor(1, 1)
        self.body_split.setStretchFactor(2, 0)
        self._split_ready = False

        # The caption row sits closer to the content than the window's general
        # 10 px rhythm: it is a caption, not another card, and the extra air was
        # read as "就绪栏还是太高" (R-198).  ``QVBoxLayout`` has a single spacing
        # for every gap, so the body -> caption gap gets its own spacer.
        self.status_strip = self._build_status_strip()
        root.addWidget(self.status_strip)
        self._tighten_status_gap(theme.STATUS_GAP)

        self._rebuild_tabs()

    def _tighten_status_gap(self, gap: int) -> None:
        """Give the body -> caption gap its own (smaller) size (R-199).

        ``QVBoxLayout`` applies one spacing to *every* gap, so the choice is
        either the roomy 10 px rhythm above the caption (which is what the user
        kept seeing as "还是太高") or a tighter one for the title bar as well.

        A spacer cannot help - Qt clamps a negative size to 0 (measured) - and
        pulling the row up with a negative margin squashed the caption text and
        left it sitting low (R-199, rejected).  What is left, and what actually
        controls the impression, is the *empty band* around the row: the body ->
        caption gap and the page margin under it.  Both are trimmed here while
        the row keeps its own fixed height, so the text stays centred.
        """
        root = self._root_layout
        root.setSpacing(int(gap))
        # Re-apply the bottom page margin at the same time: the row's height is
        # unchanged, so only these outer gaps decide how tall the band looks.
        self._apply_window_shadow()
        root.invalidate()
        root.activate()

    # ------------------------------------------------------------------ rail
    def _build_rail(self) -> QWidget:
        rail = GlassFrame(radius=theme.RADIUS_CARD)
        rail.setMinimumWidth(280)
        rail.setMaximumWidth(560)
        self.rail_box = QVBoxLayout(rail)
        self.rail_box.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(13, 12, 13, 14)
        # 4 px, not 7: with real fonts the rail content is already as tall as the
        # viewport, so a 7 px rhythm pushed 「打开目录 / 清空记录文件」 out of
        # sight (R-102).  Each section heading costs 19 px more, and adding the
        # 窗口 heading overflowed a 5 px rhythm by 7 px in the default window
        # (L-141) - 4 px keeps ~40 px of slack (R-118).
        layout.setSpacing(4)
        self.rail_layout = layout

        # ---- connection
        self.sec_conn = SectionTitle(tr("rail.connection"))
        layout.addWidget(self.sec_conn)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.port_combo = Combo()
        self.port_combo.setToolTip(tr("rail.port.tip"))
        # The label now carries the device name, but the closed box must stay
        # narrow enough to leave room for the refresh button (R-035).  So the
        # *popup* is widened (that is where a long name has to be readable) and
        # the full text goes into the tooltip; the box itself can still shrink.
        self.port_combo.view().setMinimumWidth(300)
        self.port_combo.currentIndexChanged.connect(self._sync_port_tooltip)
        self.port_combo.currentIndexChanged.connect(self._on_port_picked)
        row.addWidget(self.port_combo, 1)
        self.btn_refresh = GlyphButton(GlyphButton.REFRESH, self, size=theme.CTRL_H)
        self.btn_refresh.setToolTip(tr("rail.refresh"))
        row.addWidget(self.btn_refresh)
        layout.addLayout(row)

        self.btn_connect = QPushButton(tr("rail.open"))
        self.btn_connect.setObjectName("Ghost")
        self.btn_connect.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.btn_connect)

        self.row_auto = SwitchRow(tr("rail.autoreconnect"), True,
                                  tooltip=tr("rail.autoreconnect.tip"))
        layout.addWidget(self.row_auto)
        layout.addWidget(self._divider())

        # ---- serial parameters
        self.sec_params = SectionTitle(tr("rail.params"))
        layout.addWidget(self.sec_params)
        grid = QHBoxLayout()
        grid.setSpacing(8)
        left_col = QVBoxLayout()
        left_col.setSpacing(6)
        self.lbl_baud = self._dim(tr("rail.baud"))
        left_col.addWidget(self.lbl_baud)
        self.baud = Combo(editable=True)
        self.baud.setMinimumContentsLength(8)
        self.baud.addItems(BAUD_RATES)
        self.baud.setCurrentText("115200")
        # Custom rates must be safe to type: the validator keeps letters out and
        # `_serial_opts` clamps whatever is left (R-046).
        if self.baud.lineEdit() is not None:
            self.baud.lineEdit().setValidator(QIntValidator(50, 12000000, self))
            self.baud.lineEdit().setPlaceholderText("115200")
        self.baud.setToolTip(tr("rail.baud.tip"))
        left_col.addWidget(self.baud)
        self.lbl_databits = self._dim(tr("rail.databits"))
        left_col.addWidget(self.lbl_databits)
        self.bytesize = Combo()
        for value in ("8", "7", "6", "5"):
            self.bytesize.addItem(value)
        left_col.addWidget(self.bytesize)
        grid.addLayout(left_col, 1)

        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        self.lbl_parity = self._dim(tr("rail.parity"))
        right_col.addWidget(self.lbl_parity)
        self.parity = Combo()
        for key, label in PARITIES:
            self.parity.addItem(label, key)
        right_col.addWidget(self.parity)
        self.lbl_stopbits = self._dim(tr("rail.stopbits"))
        right_col.addWidget(self.lbl_stopbits)
        self.stopbits = Combo()
        for value in ("1", "1.5", "2"):
            self.stopbits.addItem(value)
        right_col.addWidget(self.stopbits)
        grid.addLayout(right_col, 1)
        layout.addLayout(grid)

        self.lbl_flow = self._dim(tr("rail.flow"))
        layout.addWidget(self.lbl_flow)
        self.flow = Combo()
        for key, label_key in FLOW_KEYS:
            self.flow.addItem(tr(label_key), key)
        layout.addWidget(self.flow)

        dtr_row = QHBoxLayout()
        dtr_row.setSpacing(18)
        # compact: label and switch sit next to each other instead of being
        # pushed to opposite ends of the column (R-039).
        # Off by default (R-120): raising DTR on open resets most boards.
        self.row_dtr = SwitchRow("DTR", False, compact=True)
        self.row_rts = SwitchRow("RTS", False, compact=True)
        dtr_row.addWidget(self.row_dtr)
        dtr_row.addWidget(self.row_rts)
        dtr_row.addStretch(1)
        layout.addLayout(dtr_row)
        layout.addWidget(self._divider())

        # ---- display
        self.sec_display = SectionTitle(tr("rail.display"))
        layout.addWidget(self.sec_display)
        self.row_hex = SwitchRow(tr("rail.hex"), False)
        # Two HEX columns, both off by default (R-202).  They are **only shown
        # while HEX view is on**: the left rail has no vertical slack (R-118
        # allows one extra heading line and nothing more, measured 44 px of
        # overflow when these were always visible), and a column switch that
        # does nothing in text mode is noise anyway.
        self.row_hex_offset = SwitchRow(tr("rail.hex.offset"), False,
                                        tooltip=tr("rail.hex.offset.tip"))
        self.row_hex_ascii = SwitchRow(tr("rail.hex.ascii"), False,
                                       tooltip=tr("rail.hex.ascii.tip"))
        self.row_hex_offset.setVisible(False)
        self.row_hex_ascii.setVisible(False)
        self.row_ts = SwitchRow(tr("rail.ts"), True)
        self.row_ctrl = SwitchRow(tr("rail.ctrl"), False, tooltip=tr("rail.ctrl.tip"))
        self.row_echo = SwitchRow(tr("rail.echo"), True)
        self.row_scroll = SwitchRow(tr("rail.scroll"), True, tooltip=tr("rail.scroll.tip"))
        self.row_awake = SwitchRow(tr("rail.awake"), False, tooltip=tr("rail.awake.tip"))
        for row_widget in (self.row_hex, self.row_hex_offset, self.row_hex_ascii,
                           self.row_ts, self.row_ctrl,
                           self.row_echo, self.row_scroll, self.row_awake):
            layout.addWidget(row_widget)
        layout.addWidget(self._divider())
        # ---- capture
        self.sec_record = SectionTitle(tr("rail.record"))
        layout.addWidget(self.sec_record)
        self.btn_record = QPushButton(tr("rail.record.start"))
        self.btn_record.setObjectName("Ghost")
        self.btn_record.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.btn_record)
        self.record_hint = QLabel(tr("rail.record.hint"))
        self.record_hint.setWordWrap(True)
        self.record_hint.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        layout.addWidget(self.record_hint)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.btn_open_dir = QPushButton(tr("rail.opendir"))
        self.btn_open_dir.setObjectName("Ghost")
        self.btn_open_dir.setCursor(Qt.PointingHandCursor)
        row2.addWidget(self.btn_open_dir, 1)
        self.btn_clear_capture = QPushButton(tr("rail.record.clear"))
        self.btn_clear_capture.setObjectName("Ghost")
        self.btn_clear_capture.setCursor(Qt.PointingHandCursor)
        self.btn_clear_capture.setToolTip(tr("rail.record.clear.tip"))
        self.btn_clear_capture.setEnabled(False)
        row2.addWidget(self.btn_clear_capture, 1)
        layout.addLayout(row2)

        # ---- window actions, straight under 打开目录 / 清空记录文件.  A hairline
        # keeps them visually separate from the capture actions above (the log
        # window is not part of the file capture - R-102), and they get their own
        # heading so the pair is not mistaken for more capture buttons (R-118).
        layout.addWidget(self._divider())
        self.sec_window = SectionTitle(tr("rail.window"))
        layout.addWidget(self.sec_window)
        row3 = QHBoxLayout()
        row3.setSpacing(6)
        self.btn_clear = QPushButton(tr("log.clear.btn"))
        self.btn_clear.setObjectName("Ghost")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.setToolTip(tr("log.clear.tip"))
        row3.addWidget(self.btn_clear, 1)
        self.btn_save_log = QPushButton(tr("log.save.btn"))
        self.btn_save_log.setObjectName("Ghost")
        self.btn_save_log.setCursor(Qt.PointingHandCursor)
        self.btn_save_log.setToolTip(tr("log.save.tip"))
        row3.addWidget(self.btn_save_log, 1)
        layout.addLayout(row3)

        # The port / flow / version rows that used to sit here duplicated the
        # status bar, the combo and the About dialog, so they were removed.
        layout.addStretch(1)
        scroll.setWidget(content)
        self.rail_box.addWidget(scroll)
        return rail

    @staticmethod
    def _dim(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        return label

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.LINE_SOFT};")
        return line

    # -------------------------------------------------------------- terminal
    def _build_terminal_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        log_card = GlassFrame(radius=theme.RADIUS_CARD, shadow=False)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(12, 8, 12, 8)
        log_layout.setSpacing(6)

        # Clear / Save view moved to the left rail (R-102): this card keeps only
        # the loss badge, so the log text starts right below the frame.
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addStretch(1)
        self.trim_badge = Badge("")
        toolbar.addWidget(self.trim_badge)
        log_layout.addLayout(toolbar)

        log_host = QWidget()
        log_host_layout = QVBoxLayout(log_host)
        log_host_layout.setContentsMargins(0, 0, 0, 0)
        log_host_layout.setSpacing(0)

        self.log = LogView(pixel_size=theme.log_px())
        log_host_layout.addWidget(self.log)

        self.scroll_pill = ScrollBottomPill(log_host)
        self.scroll_pill.setToolTip(tr("log.latest"))
        self.scroll_pill.clicked.connect(self.jump_to_latest)
        self.log.verticalScrollBar().valueChanged.connect(self._on_log_scrolled)
        self.log.verticalScrollBar().rangeChanged.connect(lambda *_: self._on_log_scrolled())
        log_layout.addWidget(log_host, 1)

        self.log_hint = QLabel(tr("log.hint"))
        self.log_hint.setAlignment(Qt.AlignCenter)
        self.log_hint.setWordWrap(True)
        self.log_hint.setStyleSheet(f"color: {theme.DIM}; font-size: 12px;")
        log_layout.addWidget(self.log_hint)

        self.send_panel = SendPanel()
        #: The send bar's two switches belong to the same "session state" group
        #: as the rail's (R-155).  Exposed under the same ``row_*`` naming so
        #: ``SESSION_SWITCHES`` handles both columns with one loop.
        self.row_enter = self.send_panel.enter_send
        self.row_hex_send = self.send_panel.hex_send

        # Log above, composer below, with a grab handle between them so the
        # send box can be resized (R-043).
        self.main_split = QSplitter(Qt.Vertical)
        self.main_split.setObjectName("MainSplit")
        self.main_split.setChildrenCollapsible(False)
        self.main_split.setHandleWidth(8)
        self.main_split.setStyleSheet(
            "QSplitter#MainSplit::handle { background: transparent; }"
            "QSplitter#MainSplit::handle:hover { background: rgba(11,122,110,40); }")
        self.main_split.addWidget(log_card)
        self.main_split.addWidget(self.send_panel)
        self.main_split.setStretchFactor(0, 1)
        self.main_split.setStretchFactor(1, 0)
        self._saved_send_h = 0
        layout.addWidget(self.main_split, 1)
        return page

    # --------------------------------------------------------------- sidebar
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        # Same minimum as the rail: when the window is too narrow for the
        # preferred widths, Qt squeezes each pane down to its minimum, and
        # unequal minimums were what broke the left/right symmetry (R-068).
        sidebar.setMinimumWidth(280)
        sidebar.setMaximumWidth(680)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.cmd_block = GlassFrame(radius=theme.RADIUS_CARD, shadow=False)
        cmd_layout = QVBoxLayout(self.cmd_block)
        cmd_layout.setContentsMargins(0, 0, 0, 0)
        self.commands_panel = CommandsPanel(self.command_store, self.notify)
        cmd_layout.addWidget(self.commands_panel)

        self.search_block = GlassFrame(radius=theme.RADIUS_CARD, shadow=False)
        search_layout = QVBoxLayout(self.search_block)
        search_layout.setContentsMargins(0, 0, 0, 0)
        self.search_panel = SearchPanel()
        search_layout.addWidget(self.search_panel)

        # A vertical splitter, so the boundary between 扩展命令 and 关键字搜索 is
        # draggable: with two lists in one fixed-height column the user could not
        # give either of them more room (R-190).  The two cards are still
        # ordinary widgets in the sidebar layout *when expanded*, so nothing
        # else has to know about the splitter.
        self.sidebar_split = QSplitter(Qt.Vertical)
        self.sidebar_split.setObjectName("SidebarSplit")
        self.sidebar_split.setChildrenCollapsible(False)
        self.sidebar_split.setHandleWidth(8)
        self.sidebar_split.setStyleSheet(
            "QSplitter#SidebarSplit::handle { background: transparent; }"
            "QSplitter#SidebarSplit::handle:hover { background: rgba(11,122,110,40); }")
        self.sidebar_split.addWidget(self.cmd_block)
        self.sidebar_split.addWidget(self.search_block)
        self.sidebar_split.setStretchFactor(0, 3)
        self.sidebar_split.setStretchFactor(1, 2)
        self.sidebar_split.setSizes([3, 2])
        self._sidebar_split_ready = False
        #: Last sizes the user dragged to (None = default 3:2).  Persisted so a
        #: restart keeps the panel split they chose (R-190).
        self._sidebar_sizes: list[int] | None = None
        self._applying_sidebar_sizes = False
        self.sidebar_split.splitterMoved.connect(self._on_sidebar_split)
        layout.addWidget(self.sidebar_split, 1)
        self.sidebar_box = layout
        self._sidebar_widget = sidebar
        return sidebar

    def relayout_sidebar(self) -> None:
        """Give the remaining expanded block all the vertical space.

        The two cards live in ``sidebar_split`` (R-190), so the collapsed one is
        pinned through the **splitter sizes** rather than a maximum height: the
        splitter owns the geometry and would otherwise stretch a collapsed card
        straight back to its half of the column.
        """
        cmd_open = not self.commands_panel._collapsed
        search_open = not self.search_panel._collapsed
        # ``header.height()`` is **not** usable here: once the body is hidden the
        # header is the only visible child and stretches to the whole card, so
        # measuring it returned 215 px for a 38 px strip (measured, L-253).
        cmd_h = self.commands_panel.header.sizeHint().height() + 6
        search_h = self.search_panel.header.sizeHint().height() + 6
        big = 1 << 20
        # A collapsed card is capped as well as sized: the splitter stretches
        # whatever final size it is given, and an uncapped card turned the
        # collapsed strip into a tall blank plate.
        self.cmd_block.setMaximumHeight(big if cmd_open else cmd_h)
        self.search_block.setMaximumHeight(big if search_open else search_h)

        total = max(1, self.sidebar_split.height())
        if not (cmd_open or search_open):
            # Both collapsed: two header-height strips and nothing to drag.
            self._set_sidebar_sizes([cmd_h, search_h])
            return
        if cmd_open and search_open:
            # Restore the user's own proportion, defaulting to 3:2.
            sizes = self._sidebar_sizes or [3, 2]
            self._set_sidebar_sizes(sizes)
            return
        if cmd_open:
            self._set_sidebar_sizes([max(1, total - search_h), search_h])
        else:
            self._set_sidebar_sizes([cmd_h, max(1, total - cmd_h)])

    def _set_sidebar_sizes(self, sizes: list[int]) -> None:
        """Apply splitter sizes without letting Qt redistribute them back."""
        values = [max(1, int(value)) for value in sizes]
        self._applying_sidebar_sizes = True
        try:
            self.sidebar_split.setSizes(values)
        finally:
            self._applying_sidebar_sizes = False

    def _on_sidebar_split(self, *_args) -> None:
        """Remember what the user dragged to, unless we are the ones moving it.

        The write is **debounced**: ``QSettings.setValue()`` costs ~0.03 ms but
        the ``window-<n>.ini`` rewrite it schedules is not, and inside the
        splitter's own move loop that rewrite lands in the next
        ``processEvents()``.  Measured with the write in place the drag cost
        118-132 ms per move; with it removed, 7.9 ms (L-254).  Dragging emits a
        move for every mouse pixel, so the value is kept in memory while the
        user drags and flushed once they stop.
        """
        if getattr(self, "_applying_sidebar_sizes", False):
            return
        if self.commands_panel._collapsed or self.search_panel._collapsed:
            return
        self._sidebar_sizes = list(self.sidebar_split.sizes())
        timer = getattr(self, "_sidebar_save_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(350)
            timer.timeout.connect(self._save_sidebar_split)
            self._sidebar_save_timer = timer
        timer.start()

    def _save_sidebar_split(self) -> None:
        """Persist the split the user settled on (debounced, see above)."""
        if not self._sidebar_sizes:
            return
        self.settings.setValue(
            "ui/sidebar_split",
            ",".join(str(value) for value in self._sidebar_sizes))

    def toggle_sidebar(self) -> None:
        visible = self._sidebar_widget.isHidden()
        self._sidebar_widget.setVisible(visible)
        self.settings.setValue("ui/sidebar", "1" if visible else "0")
        self._apply_responsive()

    # ---------------------------------------------------------- status strip
    def _build_status_strip(self) -> QWidget:
        """Caption row: **text only** - no plate, no border, no fill (R-082).

        The captions therefore sit straight on the page or the wallpaper.  Two
        things keep them readable there: the primary ink rather than the grey hint
        colour (5.85:1 even on the darkest possible page) and a soft white halo,
        which belongs to the glyphs instead of to a box behind them.
        """
        strip = CaptionStrip()
        # The caption row was pinned to 26 px and read as a spare toolbar under
        # the content; 22 px still left a visible empty band.  What the eye
        # measures is the *whole* stack below the last card - gap above the row,
        # the row, and the page margin below - so the fix is to trim the outer
        # gaps, not to squeeze the row itself: shrinking the row or pulling it up
        # made the text look squashed and sit too low (R-199).
        strip.setFixedHeight(theme.STATUS_H)
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(14)
        self.status_line = QLabel(tr("status.ready"))
        layout.addWidget(self.status_line)
        layout.addStretch(1)
        self.status_trim = QLabel("")
        layout.addWidget(self.status_trim)
        # The records folder, not the install folder: this is what changes in
        # Preferences, so it is what the user expects to see (R-078).
        self.status_dir = QLabel(tr("status.datadir", path=paths.logs_dir()))
        layout.addWidget(self.status_dir)
        self._style_captions()
        self.status_strip_frame = strip
        return strip

    def _style_captions(self) -> None:
        """Ink + white halo for the three captions (see _build_status_strip).

        All three use ``INK`` on purpose: with no plate behind them they can sit
        on the darkest possible page (a black photo under the 59 % veil), where
        ``INK`` still measures 5.88:1 while the grey hint colour drops to 2.49:1
        and amber to 1.70:1.  The amber warning cue still exists where it belongs
        - the trim badge in the log toolbar (L-107).
        """
        for label in (self.status_line, self.status_trim, self.status_dir):
            label.setStyleSheet(f"color: {theme.INK}; font-size: 11px;"
                                " background: transparent;")
            if label.graphicsEffect() is None:
                halo = QGraphicsDropShadowEffect(label)
                halo.setBlurRadius(6)
                halo.setOffset(0, 0)
                halo.setColor(QColor(255, 255, 255, 235))
                label.setGraphicsEffect(halo)

    # --------------------------------------------------------------- tabs
    def _rebuild_tabs(self) -> None:
        entries: list[tuple[str, str, str]] = [
            ("terminal", tr("win.tab.terminal"), tr("win.tab.terminal")),
            ("console", tr("win.tab.console"), tr("win.tab.console")),
            ("transfer", tr("win.tab.transfer"), tr("win.tab.transfer")),
        ]
        self._page_keys = ["terminal", "console", "transfer"]
        for info in self.plugins.loaded():
            if info.id in self.BUILTIN_PLUGIN_PAGES:
                # terminal / transfer already own a fixed tab; adding a
                # `plugin:<id>` tab as well would show the same page twice
                # (R-218).
                continue
            key = f"plugin:{info.id}"
            entries.append((key, info.name(), info.manifest.get("description", "")
                            if isinstance(info.manifest.get("description"), str)
                            else info.name()))
            self._page_keys.append(key)
        self.titlebar.set_tabs(entries)
        self.titlebar.tabs.buttonClicked.connect(
            lambda button: self.switch_page(button.property("page")))

    def switch_page(self, page: str) -> None:
        if page not in self._page_keys:
            return
        index = self._page_keys.index(page)
        self.stack.setCurrentIndex(index)
        self.titlebar.select_tab(page)
        if page == "console":
            view = getattr(self.console, "view", None)
            if view is not None:
                view.setFocus()
        elif page.startswith("plugin:"):
            widget = self.stack.widget(index)
            if widget is not None:
                widget.setFocus()

    def current_page(self) -> str:
        """Key of the page the user is looking at (``log`` / ``console`` / …)."""
        index = self.stack.currentIndex()
        if 0 <= index < len(self._page_keys):
            return self._page_keys[index]
        return ""

    def clear_window(self) -> None:
        """The 清除窗口 button clears **whichever** view is on screen (R-213).

        Clearing was wired straight to ``clear_log``, so pressing it while the
        terminal was showing wiped a log the user could not see and left the
        terminal untouched - which reads as "the button does nothing".  The
        button now follows the *visible* page: log page → log, terminal page →
        terminal screen.
        """
        if self.current_page() == "console":
            clear = getattr(self.console, "clear", None)
            if clear is not None:
                clear()
            return
        self.clear_log()

    # -------------------------------------------------------------- plugins
    #: Plugin ids that own a **built-in tab** instead of getting a
    #: ``plugin:<id>`` tab of their own (R-218).  They are still ordinary
    #: plugins - disabling one leaves an explanation page in its place.
    BUILTIN_PLUGIN_PAGES = ("terminal", "transfer")

    def _plugin_page(self, plugin_id: str) -> QWidget:
        """The widget of a built-in-tab plugin, or a "disabled" placeholder.

        Returns a plain ``QWidget`` either way, so the rest of the window can call
        the page without a ``hasattr`` dance.  The placeholder carries the plugin
        name so the user knows which switch to flip.
        """
        for info in self.plugins.loaded():
            if info.id == plugin_id and info.widget is not None:
                return info.widget
        return self._disabled_page(plugin_id)

    def _disabled_page(self, plugin_id: str) -> QWidget:
        """Placeholder shown in a built-in tab whose plugin is disabled."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)
        title = QLabel(tr("plugin.disabled.title", name=self._plugin_label(plugin_id)))
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)
        hint = QLabel(tr("plugin.disabled.hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.DIM};")
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    @staticmethod
    def _plugin_label(plugin_id: str) -> str:
        return "终端 / Terminal" if plugin_id == "terminal" \
            else "文件传输 / File Transfer"

    def _load_plugins(self) -> None:
        # ``plugins.load()`` already ran before `_build_ui` (R-218) so the built-in
        # tabs could be built from it; calling it again would *re-instantiate*
        # every plugin and the pages in the stack would be the discarded copies.
        if not self.plugins.loaded():
            self.plugins.load()
        for info in self.plugins.loaded():
            widget = info.widget
            if widget is None:
                continue
            if info.id in self.BUILTIN_PLUGIN_PAGES:
                # It already has a fixed tab (built by `_plugin_page`), so it must
                # not also get a `plugin:<id>` one.
                continue
            key = f"plugin:{info.id}"
            self._plugin_pages[key] = widget
            self.stack.addWidget(widget)
        self._rebuild_tabs()

    def reload_plugins(self) -> None:
        """Tear the plugin pages down, then load whatever is on disk now."""
        current = self.stack.currentWidget()
        key_before = next((key for key, widget in self._plugin_pages.items() if widget is current), None)
        self.suspend_plugins()
        self.plugins.load()
        for info in self.plugins.loaded():
            widget = info.widget
            if widget is None:
                continue
            if info.id in self.BUILTIN_PLUGIN_PAGES:
                continue
            key = f"plugin:{info.id}"
            self._plugin_pages[key] = widget
            self.stack.addWidget(widget)
        # terminal / transfer are rebuilt as well: unloading destroyed the old
        # widgets, so `self.console` would otherwise point at a dead C++ object
        # and every later call would raise "already deleted" (R-218).
        self._rebind_builtin_pages()
        self._rebuild_tabs()
        if key_before in self._plugin_pages:
            self.switch_page(key_before)
        self.notify(tr("plugin.reloaded", n=len(self.plugins.loaded())), "ok")

    #: Stack index of each built-in plugin tab, in `_page_keys` order.
    _BUILTIN_SLOTS = (("terminal", 0), ("transfer", 2))

    def _rebind_builtin_pages(self) -> None:
        """Point ``self.console`` / ``self.transfer`` at the live plugin pages.

        Called after a reload (the old widgets were destroyed) and it is also the
        single place that swaps in the "plugin disabled" placeholder.
        """
        for plugin_id, slot in self._BUILTIN_SLOTS:
            old = getattr(self, "console" if plugin_id == "terminal" else "transfer",
                          None)
            widget = self._plugin_page(plugin_id)
            if widget is old:
                continue
            index = -1
            if old is not None:
                with contextlib.suppress(RuntimeError):
                    index = self.stack.indexOf(old)
                    self.stack.removeWidget(old)
            if index < 0:
                index = slot
            self.stack.insertWidget(min(index, self.stack.count()), widget)
            if plugin_id == "terminal":
                # ``console`` is only a reference: the plugin owns the key ->
                # ``api.send`` wiring, so the window must not connect again
                # (a second connection duplicated every keystroke, L-292).
                self.console = widget
            else:
                self.transfer = widget

    def suspend_plugins(self) -> None:
        """Unload every plugin, including the built-in-tab ones (R-218).

        ``self.console`` / ``self.transfer`` point *into* those widgets, so they
        are swapped for placeholders **before** the unload destroys them -
        otherwise the window would keep a reference to a dead C++ object and the
        next ``retranslate()``/``apply_font()`` would raise "already deleted".
        """
        for plugin_id in self.BUILTIN_PLUGIN_PAGES:
            attribute = "console" if plugin_id == "terminal" else "transfer"
            widget = getattr(self, attribute, None)
            if widget is None or not self._is_builtin_page(plugin_id):
                continue
            placeholder = self._disabled_page(plugin_id)
            index = self.stack.indexOf(widget)
            if index >= 0:
                self.stack.removeWidget(widget)
                self.stack.insertWidget(index, placeholder)
            else:
                self.stack.addWidget(placeholder)
            setattr(self, attribute, placeholder)
            widget.setParent(None)
            with contextlib.suppress(RuntimeError):
                widget.deleteLater()
        self.plugins.unload()
        for key in list(self._plugin_pages):
            widget = self._plugin_pages.pop(key)
            index = self.stack.indexOf(widget)
            if index >= 0:
                self.stack.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._rebuild_tabs()

    def _is_builtin_page(self, plugin_id: str) -> bool:
        """True when the current page for ``plugin_id`` is a live plugin widget."""
        for info in self.plugins.loaded():
            if info.id == plugin_id and info.widget is not None:
                attribute = "console" if plugin_id == "terminal" else "transfer"
                return getattr(self, attribute, None) is info.widget
        return False

    def open_plugin_store(self) -> None:
        from .plugin_store import PluginStoreDialog

        dialog = getattr(self, "_plugin_store_dialog", None)
        if dialog is None:
            dialog = PluginStoreDialog(self)
            self._plugin_store_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def open_plugin_dir(self) -> None:
        target = os.path.join(paths.resource_dir(), "plugins")
        os.makedirs(target, exist_ok=True)
        self._open_path(target)

    def _plugin_log(self, text: str) -> None:
        self._log_line(time.time() * 1000, "··", text, "sys")

    def _plugin_send(self, data: bytes) -> bool:
        """Send path for plugins: refuses when closed, prints TX like a manual send.

        A plugin must never touch ``bridge.manager`` directly - it would bypass
        the TX echo, the counters and the "no port" guard, and a dashboard
        switch that silently writes into a closed port is indistinguishable from
        a broken device (L-150 family).
        """
        if not data:
            return False
        # ``self._status`` only updates when a queued ``status`` signal is
        # delivered, so right after a successful open it can still say
        # "closed" and every keystroke was silently dropped while the port was
        # in fact open (measured: ``connected=True`` from the live manager,
        # ``tx=0`` from the stale snapshot).  Ask the manager itself - it is the
        # authoritative answer and a cheap dict read.
        status = self.bridge.state() or self._status
        if not status.get("connected"):
            self.notify(tr("console.needport"), "warn")
            return False
        try:
            written = self.bridge.manager.write(data)
        except Exception as exc:
            self.notify(str(exc), "warn")
            return False
        self._on_tx_print(data)
        self._refresh_stats()
        return written is not False and written != 0

    # --------------------------------------------------------------- wiring
    def _wire(self) -> None:
        bridge = self.bridge
        bridge.rx.connect(self._on_rx, Qt.QueuedConnection)
        bridge.status.connect(self._on_status, Qt.QueuedConnection)
        bridge.ports.connect(self._on_ports, Qt.QueuedConnection)
        bridge.notice.connect(self.notify, Qt.QueuedConnection)
        bridge.stats.connect(self._on_stats, Qt.QueuedConnection)
        bridge.record.connect(self._on_record, Qt.QueuedConnection)
        bridge.upload.connect(self._on_upload, Qt.QueuedConnection)

        self.btn_refresh.clicked.connect(lambda: self._on_ports(self.bridge.manager.ports()))
        self.btn_connect.clicked.connect(self.toggle_connection)

        self.btn_clear.clicked.connect(self.clear_window)
        self.btn_save_log.clicked.connect(self.save_view)

        self.search_panel.attach_log(self.log)
        self.search_panel.hitActivated.connect(self.log.goto_block)
        self.search_panel.fullscreenToggled.connect(self.set_search_fullscreen)

        # The log needs the panel for its context-menu "Find" entry.
        self.log.search_panel = self.search_panel
        # Every entry of the log's context menu must be connected: three of them
        # (clear / find / latest) used to emit into the void, which is why those
        # right-click commands "did nothing" (L-052).
        self.log.saveRequested.connect(self.save_view)
        self.log.clearRequested.connect(self.clear_log)
        self.log.findRequested.connect(self.focus_search)
        self.log.latestRequested.connect(self.jump_to_latest)
        self.log.autoscrollChanged.connect(self._sync_autoscroll)

        self.row_auto.toggled.connect(lambda _: self._push_opts())
        for row in (self.row_dtr, self.row_rts):
            row.toggled.connect(lambda _: self._push_opts())
        for combo in (self.flow, self.parity, self.stopbits, self.bytesize):
            combo.currentIndexChanged.connect(lambda _: self._push_opts())
        self.baud.activated.connect(lambda _: self._commit_baud())
        self.baud.lineEdit().editingFinished.connect(self._commit_baud)

        self.row_hex.toggled.connect(self._on_hex_mode)
        # The two HEX columns change how a row is drawn, so only the tail row is
        # re-rendered - the history stays, which is the point of the switches
        # (R-202).
        self.row_hex_offset.toggled.connect(lambda _value: self._on_hex_columns())
        self.row_hex_ascii.toggled.connect(lambda _value: self._on_hex_columns())
        self.row_ts.toggled.connect(lambda value: setattr(self.log, "show_ts", value))
        self.row_ctrl.toggled.connect(self._on_ctrl_mode)
        # 回显 is session state (R-155), so it no longer writes the settings on
        # every click - only the log options it actually changes.
        self.row_echo.toggled.connect(self._on_echo_mode)
        self.row_scroll.toggled.connect(self._on_autoscroll)
        self.row_awake.toggled.connect(self._on_keep_awake)

        self.btn_record.clicked.connect(self.toggle_record)
        self.btn_clear_capture.clicked.connect(self.clear_capture)
        self.btn_open_dir.clicked.connect(self.open_data_dir)

        self.send_panel.sendRequested.connect(self.send_payload)
        self.bridge.txPrinted.connect(self._on_tx_print)
        self.send_panel.fileChosen.connect(self.remember_file)
        self.send_panel.fileSendRequested.connect(self.send_file)
        self.send_panel.abortRequested.connect(self.abort_file)

        self.commands_panel.sendRequested.connect(self.send_payload)
        self.log.fontZoomed.connect(self._on_log_zoom)
        # The transfer plugin wires its own signals (R-218); the window only
        # calls start_transfer/abort_transfer through the service.

        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self._refresh_stats)
        self.stats_timer.start(500)

        # The capture size has to be readable while recording, so it is polled
        # rather than only updated on start/stop events.
        self.record_timer = QTimer(self)
        self.record_timer.setInterval(500)
        self.record_timer.timeout.connect(self._refresh_record_size)

    # --------------------------------------------------------- screen awake
    def _apply_keep_awake(self, enabled: bool) -> bool:
        """Send the keep-awake request and remember whether the OS accepted it."""
        accepted = power.set_keep_awake(bool(enabled))
        self._awake_accepted = accepted
        return accepted

    def _on_keep_awake(self, enabled: bool) -> None:
        accepted = self._apply_keep_awake(enabled)
        # No ``_save_settings()`` here: the switch is not remembered (R-062), so
        # writing the whole INI just to store nothing was pure disk churn.
        if enabled and not accepted:
            # Never pretend it worked: a silent no-op would look like a bug.
            self.notify(tr("rail.awake.failed"), "warn")
            return
        if enabled and power.display_never_off():
            # Say it up front when the switch cannot change anything: on a
            # managed machine the power plan is often already "never blank the
            # display", and the user then reports "I switched it off but the
            # screen is still on" (L-235).  Measured, never guessed.
            self.notify(tr("rail.awake.policy"), "info")

    # ------------------------------------------------------------- settings
    def _restore_settings(self) -> None:
        s = self.settings
        geometry = s.value("ui/geometry")
        # A ``QByteArray`` stored in an INI file comes back as one only while Qt
        # can decode its ``@ByteArray(...)`` form; a hand-edited or older file
        # yields a plain ``str`` and ``restoreGeometry()`` raises TypeError, which
        # killed window construction outright.  Restoring a window position is
        # never worth a crash (R-159).
        if isinstance(geometry, (QByteArray, bytes, bytearray)):
            self.restoreGeometry(geometry)
        elif geometry is not None:
            self.settings.remove("ui/geometry")
        if s.value("ui/sidebar", "1") == "0":
            self._sidebar_widget.hide()
        with contextlib.suppress(TypeError, ValueError):
            self._saved_send_h = int(s.value("ui/send_h", 0) or 0)
        sizes = s.value("ui/sidebar_split")
        if isinstance(sizes, str) and sizes.strip():
            with contextlib.suppress(TypeError, ValueError):
                parsed = [int(part) for part in sizes.split(",") if part.strip()]
                if len(parsed) == 2 and all(value > 0 for value in parsed):
                    self._sidebar_sizes = parsed
        sizes = s.value("ui/split")
        self._saved_split = False
        if isinstance(sizes, list) and len(sizes) == 3:
            with contextlib.suppress(TypeError, ValueError):
                self.body_split.setSizes([int(value) for value in sizes])
                self._saved_split = True
        try:
            paths.set_logs_dir(str(s.value("paths/log_dir", "")))
        except OSError:
            paths.set_logs_dir(None)
        self.bridge.set_capture_dir(paths.logs_dir())
        # Log budget (R-081): MB, clamped by the view itself.
        from .log_view import DEFAULT_BUFFER_MB

        with contextlib.suppress(TypeError, ValueError):
            self.log.set_buffer_mb(int(s.value("display/buffer_mb",
                                                DEFAULT_BUFFER_MB) or DEFAULT_BUFFER_MB))
        self._refresh_status_dir()
        self.baud.setCurrentText(str(s.value("serial/baud", "115200")))
        self._committed_baud = self.baud.currentText()
        index = self.bytesize.findText(str(s.value("serial/bytesize", "8")))
        self.bytesize.setCurrentIndex(max(0, index))
        index = self.stopbits.findText(str(s.value("serial/stopbits", "1")))
        self.stopbits.setCurrentIndex(max(0, index))
        self.parity.setCurrentIndex(max(0, self.parity.findData(s.value("serial/parity", "N"))))
        self.flow.setCurrentIndex(max(0, self.flow.findData(s.value("serial/flow", "none"))))
        # Every switch in the rail and the send bar is **session state** as of
        # R-155: it opens at its factory default and nothing is remembered.  The
        # old values are purged here, or a downgrade/upgrade cycle would bring a
        # stale "hex on" back from an INI written by an older build.
        self._reset_session_switches()
        self.log.show_ts = self.row_ts.isChecked()
        self.log.autoscroll = self.row_scroll.isChecked()
        self.send_panel.set_enter_sends(self.row_enter.isChecked())
        # Bound the list *before* loading it, so a huge legacy file cannot
        # resurrect thousands of entries (R-116).
        from .send_panel import HISTORY_DEFAULT, HISTORY_MAX, HISTORY_MIN

        self.send_panel.set_history_limit(
            _bounded(s.value("send/history_limit", ""), HISTORY_MIN, HISTORY_MAX,
                     HISTORY_DEFAULT))
        self.send_panel.set_history_entries(_history_from(s.value("send/history", "")))
        self._apply_keep_awake(self.row_awake.isChecked())
        self._on_ports(self.bridge.manager.ports())

    #: Switches that are **session state**, never remembered (R-155).
    #:
    #: ``(row attribute, saved key)``.  The row is forced to the default its
    #: ``SwitchRow`` was constructed with, and the key is deleted from the INI so
    #: an older build's value cannot come back.  Precedent: 保持屏幕常亮 was
    #: already never restored (R-062) for the same reason - a switch that hides
    #: in a collapsed rail must not silently override the next launch.
    SESSION_SWITCHES = (
        ("row_auto", "serial/auto"),
        ("row_dtr", "serial/dtr"),
        ("row_rts", "serial/rts"),
        ("row_hex", "display/hex"),
        ("row_hex_offset", "display/hex_offset"),
        ("row_hex_ascii", "display/hex_ascii"),
        ("row_ts", "display/ts"),
        ("row_ctrl", "display/ctrl"),
        ("row_echo", "display/echo"),
        ("row_scroll", "display/scroll"),
        ("row_awake", "display/awake"),
        ("row_enter", "display/enter_send"),
        ("row_hex_send", "send/hex_send"),
    )

    def _reset_session_switches(self) -> None:
        """Force every rail / send-bar switch to its factory default (R-155).

        Runs in ``_restore_settings``, i.e. on every launch *and* every window
        that opens, which is exactly the "每次开启页面用默认" the user asked for.

        The factory defaults are read off the widgets themselves (``SwitchRow``
        is constructed with its default), so this cannot drift out of sync with
        the layout: 自动重连 on, DTR/RTS off, 时间戳 on, 回显 on, 自动滚动 on,
        HEX off, 控制符 off, 常亮 off, 回车发送 off.
        """
        for attribute, key in self.SESSION_SWITCHES:
            row = getattr(self, attribute, None)
            if row is not None:
                # Blocked: setChecked must not run the persistence handlers that
                # used to write these keys (``row_echo`` called _save_settings).
                with QSignalBlocker(row):
                    row.setChecked(row.default)
            if self.settings.contains(key):
                self.settings.remove(key)

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("serial/baud", self.baud.currentText())
        s.setValue("serial/bytesize", self.bytesize.currentText())
        s.setValue("serial/stopbits", self.stopbits.currentText())
        s.setValue("serial/parity", self.parity.currentData())
        s.setValue("serial/flow", self.flow.currentData())
        # The switches are **not** written (R-155): every launch starts at the
        # factory default, so persisting them would only leave keys behind that
        # the next restore has to purge again.  The drop-downs above *are*
        # remembered - retyping a baud rate every launch is not the same thing.
        for _attribute, key in self.SESSION_SWITCHES:
            if s.contains(key):
                s.remove(key)
        # A JSON string, not a QStringList: the INI writer would otherwise store
        # a comma-joined value that cannot carry a comma inside a command.
        s.setValue("send/history",
                   json.dumps(self.send_panel.history_entries(), ensure_ascii=False))
        s.setValue("send/history_limit", self.send_panel.history_limit())
        s.setValue("display/buffer_mb", self.log.buffer_mb())
        s.setValue("ui/language", language())
        s.setValue("ui/accent", theme.accent())
        s.setValue("ui/wallpaper", theme.wallpaper() or "")
        s.setValue("ui/font_family", theme.ui_family())
        s.setValue("ui/font_px", theme.ui_font_px())
        if self._sidebar_sizes:
            s.setValue("ui/sidebar_split",
                       ",".join(str(value) for value in self._sidebar_sizes))
        s.setValue("display/log_family", theme.log_family())
        s.setValue("display/log_px", self.log.font_px())

    def _serial_opts(self) -> dict:
        try:
            baud = int(self.baud.currentText() or 115200)
        except ValueError:
            baud = 115200
        baud = max(50, min(12_000_000, baud))
        # ``dsrdtr`` has no switch in this window, but it must still be carried:
        # a CLI that turned the handshake on would otherwise have it silently
        # cleared by the next ``_push_opts`` (the window would rewrite the port
        # with its own idea of the options) - R-156 / L-205.
        current = self.bridge.manager.status().get("opts") or {}
        return {
            "baudrate": baud,
            "bytesize": int(self.bytesize.currentText()),
            "stopbits": float(self.stopbits.currentText()),
            "parity": self.parity.currentData(),
            "flow": self.flow.currentData(),
            "rtscts": self.flow.currentData() == "rtscts",
            "xonxoff": self.flow.currentData() == "xonxoff",
            "dsrdtr": bool(current.get("dsrdtr", False)),
            "dtr": self.row_dtr.isChecked(),
            "rts": self.row_rts.isChecked(),
            "autoReconnect": self.row_auto.isChecked(),
        }

    def _commit_baud(self) -> None:
        if not self.baud.lineEdit().hasAcceptableInput():
            return
        value = str(self._serial_opts()["baudrate"])
        if value == getattr(self, "_committed_baud", None):
            return
        self._committed_baud = value
        self.baud.setCurrentText(value)
        self.settings.setValue("serial/baud", value)
        self._push_opts()

    def _push_opts(self) -> None:
        if self._status.get("connected"):
            self.bridge.update_opts(self._serial_opts())

    # ------------------------------------------------------------ connection
    def _on_ports(self, ports: list) -> None:
        current = self.port_combo.currentData()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        if not ports:
            self.port_combo.addItem(tr("misc.noport"), "")
        for port in ports:
            # Always "COM139  USB-Enhanced-SERIAL CH340": the port number is what
            # you click, the device name is what tells you *which* CH340 it is
            # (R-117).  The previous version dropped the name whenever the driver
            # had already embedded it in the description.
            name = str(port.get("name") or "")
            detail = _port_detail(port.get("description"))
            label = f"{name}  {detail}" if detail and detail not in name else name
            self.port_combo.addItem(label, name)
            index = self.port_combo.count() - 1
            hwid = str(port.get("hwid") or "")
            self.port_combo.setItemData(index, f"{label}\n{hwid}".strip(),
                                        Qt.ToolTipRole)
        preferred = self._status.get("port") or current or self.settings.value(
            "serial/lastPort", "")
        if preferred:
            index = self.port_combo.findData(preferred)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)
        self.port_combo.blockSignals(False)
        self._sync_port_tooltip()

    def _sync_port_tooltip(self, _index: int = 0) -> None:
        """Tooltip = full port label + hardware id, so a clipped label is readable."""
        data = self.port_combo.currentData()
        if not data:
            self.port_combo.setToolTip(tr("rail.port.tip"))
            return
        index = self.port_combo.currentIndex()
        detail = str(self.port_combo.itemData(index, Qt.ToolTipRole) or "")
        self.port_combo.setToolTip(
            detail or str(self.port_combo.currentText()) or tr("rail.port.tip"))

    def toggle_connection(self) -> None:
        if self._status.get("busy"):
            return
        if self._status.get("connected") or self._status.get("reconnecting"):
            self.bridge.close()
            return
        port = self.port_combo.currentData()
        if not port:
            self.notify(tr("misc.noport"), "warn")
            return
        self.settings.setValue("serial/lastPort", port)
        self.bridge.open(port, self._serial_opts())

    def _on_port_picked(self, _index: int) -> None:
        """Switching the combo while a port is open re-opens on the new one."""
        port = self.port_combo.currentData()
        if not port:
            return
        self.settings.setValue("serial/lastPort", port)
        status = self._status
        if not (status.get("connected") or status.get("reconnecting")):
            return
        if port == status.get("port"):
            return
        reset = getattr(self.console, "reset", None)
        if reset is not None:
            reset()
        self.notify(tr("misc.switching", port=port), "info")
        self.bridge.open(port, self._serial_opts())

    def _publish_channel(self, status: dict) -> None:
        """Tell the CLI which serial port this window currently owns.

        The endpoint file carries it, so a CLI looking for COM5 picks the window
        that has COM5 open instead of the window that merely started first -
        which is what kept two windows' logs clean (R-119).  ``publish`` is a
        no-op unless something actually changed.
        """
        server = getattr(self, "control_server", None)
        if server is None:
            return
        connected = bool(status.get("connected"))
        port = str(status.get("port") or "")
        if not connected and not status.get("reconnecting"):
            # Keep the name while a reconnect is pending, but once the port is
            # really closed the window owns nothing - a CLI must not think it
            # still owns COM3.
            port = ""
        server.publish(serial=port, connected=connected)


    def _on_status(self, status: dict) -> None:
        self._status = status
        self._publish_channel(status)
        self.btn_connect.setEnabled(not status.get("busy", False))
        chip = self.titlebar.status_chip
        if status.get("connected"):
            chip.set_state("ok", tr("status.chip.ok", port=status.get("port")))
            self.btn_connect.setText(tr("rail.close"))
            self.btn_connect.setObjectName("Primary")
            self.status_line.setText(tr("status.connected", port=status.get("port"),
                                        baud=self.baud.currentText()))
            # An auto-reconnect can land on a different device than the one the
            # combo still shows, so the label follows reality, not the user's
            # last pick (R-048).
            self._sync_port_combo(str(status.get("port") or ""))
        elif status.get("reconnecting"):
            chip.set_state("retry", tr("status.chip.retry"))
            self.btn_connect.setText(tr("rail.cancel"))
            self.btn_connect.setObjectName("Ghost")
            self.status_line.setText(tr("status.reconnecting"))
            if status.get("port"):
                self._sync_port_combo(str(status.get("port")))
        else:
            error = status.get("error")
            if error:
                chip.set_state("error", tr("status.chip.error"))
                self.status_line.setText(str(error))
            else:
                chip.set_state("idle", tr("status.chip.idle"))
                self.status_line.setText(tr("status.ready"))
            self.btn_connect.setText(tr("rail.open"))
            self.btn_connect.setObjectName("Ghost")
        self.btn_connect.style().unpolish(self.btn_connect)
        self.btn_connect.style().polish(self.btn_connect)

    def _sync_port_combo(self, port: str) -> None:
        """Select ``port`` in the combo without triggering a re-open."""
        if not port:
            return
        index = self.port_combo.findData(port)
        if index < 0:
            # The port is open but not (yet) in the list - show it anyway so the
            # user can see which device is really connected.
            self.port_combo.blockSignals(True)
            self.port_combo.insertItem(0, port, port)
            self.port_combo.setCurrentIndex(0)
            self.port_combo.blockSignals(False)
            return
        if self.port_combo.currentIndex() == index:
            return
        self.port_combo.blockSignals(True)
        self.port_combo.setCurrentIndex(index)
        self.port_combo.blockSignals(False)

    def _on_stats(self, stats: dict) -> None:
        self.titlebar.set_counters(stats.get("rx", 0), stats.get("tx", 0))

    def _refresh_stats(self) -> None:
        self._on_stats(self.bridge.manager.stats())

    # ------------------------------------------------------------------- rx
    def _on_rx(self, ts: float, data: bytes) -> None:
        # Nothing may be dropped (R-017), but *printing* can be coalesced: at
        # 921600 baud the port delivers a chunk every few milliseconds and each
        # append relayouts the whole view, which is what made scrolling feel
        # sticky (R-049).  Chunks are accumulated and flushed once per frame.
        if not data:
            return
        self.plugins.publish_rx(ts, data)
        # Mirror the bytes into the control ring before printing: the agent then
        # reads exactly what the user sees, including anything that arrived
        # between two CLI requests (R-112).
        self._control_seq += len(data)
        self._control_ring += data
        overflow = len(self._control_ring) - self.CONTROL_RING
        if overflow > 0:
            del self._control_ring[:overflow]
        # Parse the bytes into the device screen **once**, here, regardless of
        # which page is visible (R-212).  `TerminalView` renders this very screen
        # object, so there is no second grid to drift apart - and `cli screen`
        # keeps working while the user sits on the log page (R-211).
        #
        # The terminal is a **plugin** now (R-218): when it is disabled the tab
        # holds a plain placeholder widget, and a received byte must still be
        # logged instead of raising on every packet (the packaged self-test
        # caught exactly that: `'QWidget' object has no attribute 'absorb'`).
        absorb = getattr(self.console, "absorb", None)
        if absorb is not None:
            absorb(data)
        self._rx_queue.append((ts, data))
        if not self._rx_timer.isActive():
            self._rx_timer.start()

    def _flush_rx(self) -> None:
        if not self._rx_queue:
            return
        queue, self._rx_queue = self._rx_queue, []
        self.log.begin_batch()
        try:
            for ts, data in queue:
                self._feed(ts, data)
        finally:
            self.log.end_batch()
        self._check_trim()

    def _feed(self, ts: float, data: bytes) -> None:
        if not data:
            return
        if self.log_hint.isVisible():
            self.log_hint.hide()
        if self.row_hex.isChecked():
            self._feed_hex(ts, data)
        else:
            self._feed_text(ts, data)

    def _feed_text(self, ts: float, data: bytes) -> None:
        text = self._decoder.decode(data, final=False)
        if not text:
            return
        try:
            baud = int(self.baud.currentText())
        except ValueError:
            baud = 115200
        step = min(400.0, len(data) / bytes_per_ms(baud)) / max(1, len(text))
        start = 0
        keywords = self.search_panel.keywords
        for index, char in enumerate(text):
            if char != "\n":
                continue
            segment = text[start:index]
            line_ts = self._pending_ts if self._pending_text else ts + step * start
            raw = self._pending_text + segment
            self._pending_text = ""
            self._pending_ts = 0.0
            self._print_line(line_ts, raw, "RX", keywords)
            start = index + 1
        tail = text[start:]
        if tail:
            if not self._pending_text:
                self._pending_ts = ts + step * start
            self._pending_text += tail
            self.log.set_pending(self._clean(self._pending_text, pending=True))
        else:
            self.log.clear_pending()

    def _clean(self, raw: str, pending: bool = False) -> str:
        from .log_view import escape_control

        if self.row_ctrl.isChecked():
            return escape_control(raw + ("" if pending else "\n"))
        return raw.rstrip("\r")

    def settle_hex_partial(self) -> None:
        """Turn the incomplete HEX row into a normal line before anything else.

        ``LogView.append_line()`` starts by clearing the pending block, so any
        line written while a HEX row is still half-full (a TX echo, a system
        notice, another RX packet) **deleted those bytes from the view** - the
        reported "收又不显示这个": the tail row silently vanished on the next send
        (R-203).  Bytes may never be dropped (R-017), so the half row is closed
        out with the timestamp it already carries.

        The bytes stay in ``_hex_session`` (the replay buffer), so the next
        packet continues the *same* 16-byte alignment rather than starting a
        fresh row - the offset column therefore keeps counting up.
        """
        if not self._hex_partial or not self.row_hex.isChecked():
            return
        partial, self._hex_partial = self._hex_partial, b""
        ts, self._hex_ts = self._hex_ts, 0.0
        self.log.clear_pending()
        self._log_line(ts or time.time() * 1000, "RX",
                       self._hex_row(self._hex_offset, partial), "rx")

    def _log_line(self, ts: float, direction: str, text: str, kind: str = "rx",
                  keywords=None) -> int:
        """Append a rendered line **and** keep the live search results in step.

        Every path that writes to the log goes through here.  The TX path used
        to append on its own and never told the search panel, so results stopped
        growing while sending - that was the reported bug (L-089).
        """
        # Anything written while a HEX row is still half-full would wipe that
        # row (``append_line`` clears the pending block first), so it is closed
        # out here - one gate for every caller (R-203).
        if kind != "rx":
            self.settle_hex_partial()
        words = self.search_panel.keywords if keywords is None else keywords
        composed = self.log.compose_line(ts, direction, text)
        block = self.log.append_line(ts, direction, text, kind, keywords=words)
        self._appended += 1
        # Capture-to-file is a mirror of the view: the very string just printed
        # (timestamp + direction + payload) goes to disk, so a saved session and
        # a recorded session read the same (R-101).
        recorder = getattr(self.bridge, "recorder", None)
        if recorder is not None and recorder.active:
            recorder.write_line(composed)
        if self.search_panel.match(composed):
            self.search_panel.add_hit(ts, composed, block)
        return block

    def _print_line(self, ts: float, raw: str, direction: str, keywords) -> None:
        text = self._clean(raw)
        self.log.clear_pending()
        self._log_line(ts, direction, text, "rx", keywords)

    def _feed_hex(self, ts: float, data: bytes) -> None:
        merged = self._hex_partial + data
        index = 0
        keywords = self.search_panel.keywords
        # ``_hex_session`` is the replay buffer for a column switch (R-202);
        # it must hold the *original* byte order, so it is appended before the
        # splitting below can consume ``merged``.
        if not self._hex_session_ts:
            self._hex_session_ts = ts
        self._hex_session += data
        overflow = len(self._hex_session) - self.HEX_SESSION_MAX
        if overflow > 0:
            del self._hex_session[:overflow]
            self._hex_session_ts = ts
        while len(merged) - index >= 16:
            row = merged[index:index + 16]
            text = self._hex_row(self._hex_offset, row)
            self.log.clear_pending()
            self._log_line(ts, "RX", text, "rx", keywords)
            self._hex_offset += 16
            index += 16
        self._hex_partial = merged[index:]
        if self._hex_partial:
            if not self._hex_ts:
                self._hex_ts = ts
            self._show_hex_pending(self._hex_ts)
        else:
            self._hex_ts = 0.0
            self.log.clear_pending()

    def _show_hex_pending(self, ts: float) -> None:
        """Show the not-yet-complete row, timestamped like every other line.

        The tail row used to be inserted as bare payload through
        ``set_pending()``, so it had no ``[hh:mm:ss.mmm]`` prefix while every
        finished row did - the user saw one line without a timestamp, and the
        timestamp only appeared once the *next* packet completed the row and
        turned it into a normal line (R-201).  The prefix is composed here with
        the same ``compose_line()`` the finished rows use, so the two cannot
        disagree.
        """
        row = self._hex_row(self._hex_offset, self._hex_partial)
        self.log.set_pending(self.log.compose_line(ts, "RX", row),
                             stamped=True)

    def _hex_row(self, offset: int, row: bytes) -> str:
        """One HEX line: ``offset  bytes  |ascii|``, each column optional (R-202).

        The offset and the ASCII column are separate switches and both default
        **off**: an offset column is noise while a session is only a few rows
        long, and the ``|.U:|`` column only helps when a payload is actually
        text.
        """
        parts: list[str] = []
        if self._hex_option("row_hex_offset"):
            parts.append(f"{offset:08x}")
        hex_part = " ".join(f"{byte:02x}" for byte in row)
        # Every row pads to a full 16-byte line, so a short trailing row keeps
        # the ASCII column of the rows above it aligned.
        parts.append(hex_part.ljust(47))
        if self._hex_option("row_hex_ascii"):
            ascii_part = "".join(
                chr(byte) if 32 <= byte < 127 else "." for byte in row)
            parts.append(f"|{ascii_part}|")
        return "  ".join(parts).rstrip()

    def _hex_option(self, name: str) -> bool:
        """Read one HEX column switch, tolerating a window without it."""
        row = getattr(self, name, None)
        return bool(row is not None and row.isChecked())

    def _check_trim(self) -> None:
        # The log owns the count: it is the only thing that knows how many lines
        # the size budget actually dropped (R-081).
        dropped = self.log.trimmed
        if dropped > 0:
            self.trim_badge.show_text(tr("log.trim", n=dropped), "Badge")
            self.status_trim.setText(tr("log.trim.status", n=dropped))
        else:
            self.trim_badge.hide()
            self.status_trim.setText("")

    # -------------------------------------------------------------- following
    def _on_autoscroll(self, value: bool) -> None:
        self.log.autoscroll = value
        # No settings write: 自动滚动 is session state (R-155).  R-150 still holds
        # - the click path stays a single ``autoscroll = value`` plus the pill
        # refresh, which is what keeps it under 40 ms on a large log.
        self._on_log_scrolled()

    def _sync_autoscroll(self, value: bool) -> None:
        with QSignalBlocker(self.row_scroll):
            self.row_scroll.setChecked(value)
        self._on_log_scrolled()

    def _on_log_scrolled(self) -> None:
        if self.log.autoscroll or self.log._at_bottom():
            self.scroll_pill.hide_pill()
            return
        self.scroll_pill.show_pill(self._pill_anchor())

    def _pill_anchor(self) -> QPoint:
        host = self.scroll_pill.parentWidget()
        return QPoint(host.width() - 34, host.height() - 18)

    def focus_search(self, selected_text: str = "") -> None:
        """Log context menu -> "Find…": expand the panel and focus its box."""
        panel = self.search_panel
        if self._sidebar_widget.isHidden():
            self.toggle_sidebar()
        if panel._collapsed:
            panel.toggle_collapsed()
        if selected_text:
            panel.search_selection(selected_text)
        panel.input.setFocus()
        panel.input.selectAll()

    def jump_to_latest(self) -> None:
        self.log.autoscroll = True
        self.scroll_pill.hide_pill()

    #: Terminal keystrokes are sent by the **terminal plugin** through
    #: ``api.send`` (R-218) - the window used to own ``_on_terminal_keys`` and
    #: connecting it *as well* made every keystroke go out twice ("ls" -> "llss",
    #: L-292).  Nothing in the host sends terminal bytes any more.

    # ------------------------------------------------------- search fullscreen
    def set_search_fullscreen(self, value: bool) -> None:
        """Move the search panel into a window-sized overlay and back."""
        if value:
            if self._search_overlay is not None:
                return
            overlay = QWidget(self.centralWidget())
            overlay.setObjectName("SearchOverlay")
            overlay.setStyleSheet(
                f"QWidget#SearchOverlay {{ background: {theme.BG};"
                f" border-radius: {theme.RADIUS_CARD}px; }}")
            layout = QVBoxLayout(overlay)
            layout.setContentsMargins(16, 14, 16, 14)
            layout.setSpacing(10)

            head = QHBoxLayout()
            title = QLabel(tr("search.fulltitle"))
            title.setStyleSheet("font-size: 14px; font-weight: 650;")
            head.addWidget(title)
            head.addStretch(1)
            close = QPushButton(tr("search.exitfull"))
            close.setObjectName("Ghost")
            close.setCursor(Qt.PointingHandCursor)
            close.clicked.connect(lambda: self.search_panel.set_fullscreen(False))
            head.addWidget(close)
            layout.addLayout(head)

            self.search_block.setParent(overlay)
            self.search_block.setMaximumHeight(1 << 20)
            layout.addWidget(self.search_block, 1)

            overlay.setGeometry(self._overlay_rect())
            overlay.show()
            overlay.raise_()
            self._search_overlay = overlay
            self.search_panel.show_full_hint()
            return

        overlay = self._search_overlay
        if overlay is None:
            return
        self.search_block.setParent(self.sidebar)
        # Back into the splitter, not into the plain layout: re-adding it as a
        # layout item left the card outside the splitter, where it grew to fill
        # the whole column and the divider disappeared (R-190 / L-253).
        self.sidebar_split.addWidget(self.search_block)
        self.sidebar_split.setStretchFactor(0, 3)
        self.sidebar_split.setStretchFactor(1, 2)
        self.relayout_sidebar()
        overlay.deleteLater()
        self._search_overlay = None

    def _overlay_rect(self) -> QRect:
        """Body area only, so the title bar stays reachable."""
        central = self.centralWidget().rect()
        top = self.titlebar.geometry().bottom() + 10
        bottom = self.status_strip.geometry().top() - 4
        return QRect(central.left() + 10, top, central.width() - 20,
                     max(120, bottom - top))

    # ------------------------------------------------------------ responsive
    def _apply_responsive(self) -> None:
        """Only hide decorations on narrow windows.

        Column widths are owned by the splitter the user drags, so this method
        must never set a width: doing that from ``resizeEvent`` fights the
        layout and loops forever (L-041).
        """
        width = self.width()
        show_subtitle = width >= 1120
        if self.titlebar.subtitle.isVisible() != show_subtitle:
            self.titlebar.subtitle.setVisible(show_subtitle)

        # At narrow widths the right cluster gives way, never the tab labels.
        show_counter = width >= 1180
        if self.titlebar.counter.isVisible() != show_counter:
            self.titlebar.counter.setVisible(show_counter)
        show_chip = width >= 1060
        if self.titlebar.status_chip.isVisible() != show_chip:
            self.titlebar.status_chip.setVisible(show_chip)

    def reset_layout(self) -> None:
        """Put the three columns back to their default widths."""
        self._sidebar_widget.show()
        self._apply_default_split()

    def _apply_default_split(self) -> None:
        # showEvent runs before the splitter has a real width; using the 0 it
        # reports then scaled every column down proportionally, which is why the
        # defaults used to land on arbitrary values. Estimate from the window.
        total = self.body_split.width()
        if total < 400:
            total = self.width() - 24
        total = max(600, total)
        # Both columns share one width rule, so the default layout reads as a
        # symmetric frame instead of a narrow rail beside a wide sidebar. The
        # three sizes must add up to the real width: when they do not, Qt shrinks
        # them proportionally and each column hits a *different* minimum, which
        # is what previously left 280 / 408 / 260 instead of an equal pair.
        rail = max(300, min(410, int(total * 0.26)))
        if total - 2 * rail < 360:
            rail = max(280, (total - 360) // 2)
        self.body_split.setSizes([rail, max(360, total - 2 * rail), rail])

    def _split_looks_degenerate(self) -> bool:
        """True when the persisted sizes do not fit the window any more."""
        sizes = self.body_split.sizes()
        if len(sizes) != 3:
            return True
        total = sum(sizes)
        if total < 300:
            return True
        return abs(total - self.body_split.width()) > max(120, total * 0.35)

    def _apply_main_split(self) -> None:
        total = self.main_split.height()
        if total < 200:
            return
        height = self._saved_send_h or self.send_panel.sizeHint().height()
        height = max(96, min(height, int(total * 0.7)))
        self.main_split.setSizes([total - height, height])

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_native_resize_ready", False):
            # The HWND exists once the first show has happened; WS_THICKFRAME has
            # to be added at that point (R-205 / L-267).
            self._native_resize_ready = True
            self._native_resize_ok = self._enable_native_resize()
        if not getattr(self, "_main_split_ready", False):
            self._main_split_ready = True
            self._apply_main_split()
        if not self._split_ready:
            self._split_ready = True
            # The splitter only has a real width once the first show happened;
            # setSizes() in __init__ was silently clamped to the minimums.
            if self._split_looks_degenerate() or not self._saved_split:
                self._apply_default_split()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._search_overlay is not None:
            self._search_overlay.setGeometry(self._overlay_rect())
        if self.scroll_pill.isVisible() and not self.log.autoscroll:
            self.scroll_pill.move(
                self._pill_anchor() - QPoint(self.scroll_pill.width() // 2,
                                             self.scroll_pill.height()))
        self._apply_responsive()

    # ------------------------------------------------------------------- log
    def clear_log(self) -> None:
        self.log.clear_all()
        # "Clear" means the counts displayed in the title bar describe *this*
        # log; leaving old totals behind made the numbers look wrong (R-073).
        self.bridge.manager.reset_stats()
        self.titlebar.set_counters(0, 0)
        self._appended = 0
        # Loss accounting also starts over: the badge reports lines this log
        # dropped, not the previous session's (R-081).
        self.log.trimmed = 0
        self._pending_text = ""
        self._hex_partial = b""
        # The replay buffer goes with the log it mirrors, or a column switch
        # would resurrect rows the user had just cleared (R-202).
        self._hex_session = bytearray()
        self._hex_session_ts = 0.0
        self._hex_ts = 0.0
        self._hex_offset = 0
        self.trim_badge.hide()
        self.status_trim.setText("")
        self.log_hint.show()
        self.scroll_pill.hide_pill()
        self.search_panel.clear_results()

    def _set_hex_column_rows_visible(self, visible: bool) -> None:
        """Show the two HEX column switches only while HEX view is on (R-202).

        The rail has no vertical slack (R-118), and these two rows cost 44 px of
        it - so they appear with the mode they belong to and vanish with it.
        """
        for row in (self.row_hex_offset, self.row_hex_ascii):
            if row.isVisible() != visible:
                row.setVisible(visible)

    def _on_hex_mode(self, enabled: bool) -> None:
        # No settings write: the switch is session state (R-155).  An INI flush
        # on a click was also a needless 30 ms disk hit on a large settings file.
        self._clear_decoder()
        self._set_hex_column_rows_visible(enabled)
        if self._appended > 0:
            self.clear_log()
            self._log_line(time.time() * 1000, "··",
                          tr("log.clear"), "sys")
        self.status_line.setText(tr("rail.hex") + (" ✓" if enabled else " ✗"))

    def _on_hex_columns(self) -> None:
        """Re-render the HEX log after an offset/ASCII column switch (R-202).

        The log is a rendered document, not a byte buffer, so the columns cannot
        be repainted line by line.  The raw bytes of the session are therefore
        kept in ``_hex_session`` while HEX view is on, and a switch replays them
        through the same ``_feed_hex()`` that produced the original rows - which
        is the only way the history keeps the *same* bytes with the new columns
        instead of being thrown away (the earlier attempt simply cleared the log,
        destroying exactly what the user flipped the switch to read).
        """
        if not self.row_hex.isChecked():
            return
        session, last_ts = self._hex_session, self._hex_session_ts
        self.clear_log()
        self._hex_session = bytearray()
        self._hex_session_ts = 0.0
        if not session:
            return
        self._log_line(time.time() * 1000, "··", tr("log.reflow"), "sys")
        self._feed_hex(last_ts or time.time() * 1000, bytes(session))
        self._hex_session = session
        self._hex_session_ts = last_ts

    def _on_ctrl_mode(self, enabled: bool) -> None:
        self._clear_decoder()
        if self._pending_text:
            self.log.set_pending(self._clean(self._pending_text, pending=True))
        self.status_line.setText(tr("rail.ctrl") + (" ✓" if enabled else " ✗"))

    def _on_echo_mode(self, enabled: bool) -> None:
        """回显: whether locally typed text is written into the log (R-155).

        Only the decoder needs resetting - nothing here is persisted, so this
        must not call ``_save_settings``.
        """
        self._clear_decoder()
        self.status_line.setText(tr("rail.echo") + (" ✓" if enabled else " ✗"))

    def _clear_decoder(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._pending_text = ""
        self._hex_partial = b""
        self.log.clear_pending()

    # ------------------------------------------------------------------ send
    def send_payload(self, payload: bytes, description: str = "") -> None:
        if not payload:
            return
        try:
            self.bridge.manager.write(payload)
        except Exception as exc:
            self.notify(str(exc), "warn")
            return
        if self.row_echo.isChecked():
            if self.row_hex.isChecked():
                text = "[HEX] " + " ".join(f"{byte:02x}" for byte in payload)
            else:
                if self.row_ctrl.isChecked():
                    # Render the bytes that actually left the port (including
                    # the appended line end), so TX is as faithful as RX.
                    from .log_view import escape_control

                    text = escape_control(payload.decode("utf-8", "replace"))
                else:
                    text = (description or payload.decode("utf-8", "replace")).rstrip("\r\n")
            self._log_line(time.time() * 1000, "TX", text, "tx")
            self._check_trim()

    def _on_tx_print(self, data: bytes) -> None:
        """Print file bytes exactly like a normal send would print them."""
        if not data or not self.row_echo.isChecked():
            return
        if self.row_hex.isChecked():
            self._log_line(time.time() * 1000, "TX",
                           "[HEX] " + " ".join(f"{byte:02x}" for byte in data), "tx")
            return
        from .log_view import escape_control

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", "replace")
        if self.row_ctrl.isChecked():
            text = escape_control(text)
        for line in text.splitlines() or [""]:
            if line:
                self._log_line(time.time() * 1000, "TX", line, "tx")
        self._check_trim()

    def remember_file(self, path: str) -> None:
        """「打开文件」only remembers the choice; nothing is sent yet (R-074)."""
        self._chosen_file = path

    def send_file(self, path: str) -> None:
        """「发送文件」transmits the remembered file and prints it as normal TX."""
        path = path or getattr(self, "_chosen_file", "")
        if not path:
            self.notify(tr("send.nofile"), "warn")
            return
        if not self._status.get("connected"):
            self.notify(tr("misc.needport"), "warn")
            return
        if getattr(self, "_raw_worker", None) is not None and self._raw_worker.isRunning():
            return
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            self.notify(f"{exc}", "error")
            return
        name = os.path.basename(path)
        self.send_panel.set_file_busy(True, f"{name} …")
        self.send_panel.set_file_progress(0, len(data), name)

        from .workers import RawFileWorker

        self._raw_worker = RawFileWorker(self.bridge.manager, data, name)
        self._raw_worker.finished_ok.connect(lambda: self._raw_done(True, name))
        self._raw_worker.failed.connect(lambda msg: self._raw_done(False, msg))
        self._raw_worker.start()

    def _raw_done(self, ok: bool, message: str) -> None:
        if ok:
            path = getattr(self, "_chosen_file", "") or message
            size = os.path.getsize(path) if os.path.isfile(path) else 0
            text = tr("send.filedone", name=os.path.basename(message),
                      size=human_bytes(size))
            self.send_panel.set_file_progress(size, max(1, size), message)
        else:
            text = message
        self.send_panel.set_file_busy(False, text)
        self.notify(text, "ok" if ok else "error")

    def abort_file(self) -> None:
        worker = getattr(self, "_raw_worker", None)
        if worker is not None:
            worker.cancel()

    # -------------------------------------------------------------- transfer
    def start_transfer(self, protocol: str, direction: str, path: str) -> None:
        """Start a transfer through the **transfer plugin** (R-218).

        The window no longer owns the worker: the plugin does, and this method is
        just the menu/CLI entry point.  When the plugin is disabled the user gets
        an explanation rather than a silent no-op.
        """
        service = self.plugins.service("transfer")
        if service is None:
            self.notify(tr("plugin.disabled.transfer"), "warn")
            return
        if not self._status.get("connected"):
            self.notify(tr("misc.needport"), "warn")
            return
        service.start(protocol, direction, path)

    def abort_transfer(self) -> None:
        service = self.plugins.service("transfer")
        if service is not None:
            service.abort()

    # ---------------------------------------------------------------- record
    def toggle_record(self) -> None:
        recorder = self.bridge.recorder
        if recorder.active:
            state = recorder.stop()
            self.notify(tr("misc.recorded", size=human_bytes(state.get("bytes", 0))), "info")
        else:
            state = recorder.start()
            self.notify(tr("misc.recording",
                           name=os.path.basename(str(state.get("path") or ""))), "ok")

    def clear_capture(self) -> None:
        """Empty the capture file; an active capture keeps writing into it."""
        recorder = self.bridge.recorder
        if not recorder.state().get("path"):
            self.notify(tr("rail.record.none"), "warn")
            return
        state = recorder.clear()
        self._on_record(state)
        self.notify(tr("rail.record.cleared",
                       name=os.path.basename(str(state.get("path") or ""))), "ok")

    def _on_record(self, state: dict) -> None:
        active = bool(state.get("active"))
        self.btn_record.setText(tr("rail.record.stop") if active
                                else tr("rail.record.start"))
        self.btn_record.setObjectName("Danger" if active else "Ghost")
        self.btn_record.style().unpolish(self.btn_record)
        self.btn_record.style().polish(self.btn_record)
        # Clearing must stay available while recording, so it follows the file,
        # not the open/closed state of the capture.
        self.btn_clear_capture.setEnabled(bool(state.get("path")))
        if active:
            self._show_capture_size(state)
            if not self.record_timer.isActive():
                self.record_timer.start()
        else:
            self.record_timer.stop()
            self.record_hint.setText(tr("rail.record.hint"))

    def _show_capture_size(self, state: dict) -> None:
        """Print the captured file size - the only progress figure shown."""
        self.record_hint.setText(tr(
            "rail.record.size",
            path=state.get("path") or "",
            size=human_bytes(state.get("bytes", 0))))

    def _refresh_record_size(self) -> None:
        recorder = self.bridge.recorder
        if not recorder.active:
            self.record_timer.stop()
            return
        state = recorder.state()
        self._show_capture_size(state)
        self.btn_clear_capture.setEnabled(bool(state.get("path")))

    def _on_upload(self, event: dict) -> None:
        # Progress of a raw file send. Completion is reported by RawFileWorker's
        # own signals, so nothing else belongs here: an earlier version compared
        # the whole event dict against the strings "done"/"error" (never true)
        # and referenced an undefined `state` inside that dead branch.
        if event.get("state") == "running":
            self.send_panel.set_file_progress(event.get("sent", 0), event.get("total", 0),
                                              event.get("name", ""))

    def open_data_dir(self) -> None:
        """Open the folder captures go to.

        While a capture is running its file lives where it was started, so that
        folder is opened; otherwise the *configured* folder is opened, which is
        what the user just picked in Preferences (R-078).
        """
        state = self.bridge.recorder.state()
        path = str(state.get("path") or "")
        if state.get("active") and path and os.path.isfile(path):
            self._open_path(os.path.dirname(path))
            return
        self._open_path(paths.logs_dir())

    def _open_path(self, target: str) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(target)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as exc:
            self.notify(f"{exc}", "warn")

    def save_view(self) -> None:
        text = self.log.plain_text()
        if not text.strip():
            self.notify(tr("log.empty"), "warn")
            return
        default = os.path.join(paths.data_dir(),
                               time.strftime("omnicom-view-%Y%m%d-%H%M%S.log"))
        path, _ = QFileDialog.getSaveFileName(self, tr("log.savedialog"), default,
                                              tr("log.filter.text"))
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        except OSError as exc:
            self.notify(tr("log.savefail", err=exc), "error")
            return
        lines = text.count("\n") + 1
        self.notify(tr("log.saved", n=lines, name=os.path.basename(path)), "ok")

    # ----------------------------------------------------------------- toast
    #: The stack of notifications may not stand taller than this fraction of the
    #: *window*, so a burst of messages can never cover most of the screen.  The
    #: user asked for "比一半屏幕少一点" after a dozen messages filled 85 % of it
    #: (R-200).
    TOAST_STACK_FRACTION = 0.45
    #: Gap between two stacked notifications.
    TOAST_GAP = 10
    #: Bottom margin of the stack (clears the caption row).
    TOAST_BOTTOM = 54

    def notify(self, text: str, level: str = "info", timeout: int = 3600) -> None:
        """Show a notification card, newest at the bottom of the stack (R-200).

        The previous version stacked *upwards from the newest*, so every message
        pushed the whole stack higher and a burst covered 85 % of the window with
        the oldest card at the bottom - the newest message, the one being read,
        ended up furthest from the content.  Now the newest sits at the bottom
        and pushes the older ones up, and the stack is capped: when the next card
        would not fit, the oldest is retired to make room.
        """
        if not text:
            return
        if getattr(self, "titlebar", None) is None:
            # Plugins load before the UI is built (R-218); a toast needs a window
            # to hang on, so fall back to stderr until then.
            import sys as _sys

            print(f"[plugin] {text}", file=_sys.stderr)
            return
        if level in ("warn", "error") and getattr(self, "search_panel", None) is not None:
            self._log_line(time.time() * 1000, "!!", text, "evt")
        # A dismissed toast has already been deleteLater()'d, so touching it
        # raises RuntimeError("Internal C++ object already deleted").
        alive = []
        for item in self._toasts:
            try:
                if item.isVisible():
                    alive.append(item)
            except RuntimeError:
                continue
        self._toasts = alive

        # Ask the new card for its size first: its height is the room it needs.
        toast = Toast(self, text, level, timeout)
        limit = max(120, int(self.height() * self.TOAST_STACK_FRACTION))
        # Retire the oldest until the whole stack (new card included) fits inside
        # the cap.  The sums are recomputed from the live list each round: a
        # running total drifts as soon as a card leaves, and a stale total kept
        # more cards on screen than the cap allows (measured, R-200).
        def stack_height() -> int:
            total = toast.height() + self.TOAST_GAP
            for item in self._toasts:
                total += item.height() + self.TOAST_GAP
            return total

        while self._toasts and stack_height() > limit:
            oldest = self._toasts.pop(0)
            with contextlib.suppress(RuntimeError):
                oldest.dismiss()

        # One pass, bottom-up: newest lowest, each older card one step higher.
        #
        # These moves are **not animated**.  ``QPropertyAnimation`` needs the
        # event loop to advance, so with a burst of notifications the next
        # ``notify()`` runs while every card is still mid-slide; measuring or
        # continuing from ``pos()`` then stacked them unevenly and the last two
        # ended up exactly on top of each other (measured, L-261).  A settled
        # stack is also what the user actually wants to read - only the arriving
        # card animates, via ``play_in()``.
        bottom = self.height() - self.TOAST_BOTTOM
        y = max(70, bottom - toast.height())
        toast.move_to(QPoint(max(16, self.width() - toast.width() - 26), y),
                      animate=False)
        toast.play_in()
        cursor = y
        for item in reversed(self._toasts):
            cursor -= item.height() + self.TOAST_GAP
            with contextlib.suppress(RuntimeError):
                item.move_to(QPoint(item.x(), max(70, cursor)), animate=False)
        self._toasts.append(toast)
        QTimer.singleShot(timeout, toast.dismiss)

    def clear_toasts(self) -> None:
        """Drop every notification on screen.

        Used when the window closes (a toast is a child of the central widget
        and would otherwise flash during the fade) and by the GUI selftest, which
        must start from an empty stack instead of inheriting whatever earlier
        steps happened to show (R-200).  Dismissal is skipped: these cards are
        being removed deliberately, not expired.
        """
        toasts, self._toasts = getattr(self, "_toasts", []), []
        for toast in toasts:
            with contextlib.suppress(RuntimeError):
                toast.hide()
                toast.deleteLater()

    # -------------------------------------------------------------- dialogs
    def open_preferences(self) -> None:
        from ..plugin_store import configured_catalogs

        dialog = PreferencesDialog(
            self, language(),
            log_dir=str(self.settings.value("paths/log_dir", "")) or paths.logs_dir(),
            accent=theme.accent(),
            wallpaper=theme.wallpaper() or "",
            buffer_mb=self.log.buffer_mb(),
            ui_family=theme.ui_family(), ui_px=theme.ui_font_px(),
            log_family=theme.log_family(), log_px=self.log.font_px(),
            history_limit=self.send_panel.history_limit(),
            history_count=len(self.send_panel.history_entries()),
            plugin_sources="\n".join(configured_catalogs(self.settings)))
        dialog.languageChanged.connect(self.change_language)
        dialog.logDirChanged.connect(self.apply_log_dir)
        dialog.accentChanged.connect(self.apply_accent)
        dialog.wallpaperChanged.connect(self.apply_wallpaper)
        dialog.bufferChanged.connect(self.apply_log_buffer)
        dialog.uiFontChanged.connect(self.apply_ui_font)
        dialog.logFontChanged.connect(self.apply_log_font)
        dialog.historyLimitChanged.connect(self.apply_history_limit)
        dialog.historyCleared.connect(self.clear_history)
        dialog.pluginSourcesChanged.connect(self.apply_plugin_sources)
        dialog.exec()

    def apply_plugin_sources(self, value: str) -> None:
        from ..plugin_store import catalog_sources

        urls = catalog_sources(value)
        self.settings.setValue("plugins/catalogs", "\n".join(urls))
        self.settings.remove("plugins/catalog")
        store = getattr(self, "_plugin_store_dialog", None)
        if store is not None:
            store.set_sources(urls)

    # ------------------------------------------------------- theme + folders
    def apply_log_dir(self, path: str) -> bool:
        """Point captures at ``path``; keep the old folder when it is unusable."""
        previous = str(self.settings.value("paths/log_dir", ""))
        try:
            resolved = paths.set_logs_dir(path)
        except OSError as exc:
            paths.set_logs_dir(previous)
            self.notify(tr("pref.logdir.bad", err=exc), "warn")
            return False
        self.bridge.set_capture_dir(resolved)
        self.settings.setValue("paths/log_dir", path)
        self._refresh_status_dir()
        if self.bridge.recorder.active:
            # A capture already running keeps its file; say so instead of
            # letting the user wonder why the old folder still grows (R-078).
            self.notify(tr("misc.logdir.next", path=resolved), "warn")
        else:
            self.notify(tr("misc.logdir", path=resolved), "ok")
        return True

    def _refresh_status_dir(self) -> None:
        """Show the folder captures actually land in."""
        path = paths.logs_dir()
        self.status_dir.setText(tr("status.datadir", path=path))
        self.status_dir.setToolTip(path)

    def apply_log_buffer(self, megabytes: int) -> int:
        """Change how much log text is kept; persists and reports the size."""
        used = self.log.set_buffer_mb(megabytes)
        self.settings.setValue("display/buffer_mb", used)
        self.notify(tr("misc.buffer", mb=used), "ok")
        return used

    def _on_log_zoom(self, pixel_size: int) -> None:
        """Remember a Ctrl+wheel zoom so the next window opens the same size."""
        theme.set_log_px(pixel_size)
        self.settings.setValue("display/log_px", int(pixel_size))

    def apply_ui_font(self, family: str, pixel_size: int) -> int:
        """Change the interface face/size; the log keeps its own (R-096)."""
        theme.set_ui_family(family)
        used = theme.set_ui_font_px(pixel_size)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.app_stylesheet())
        self.settings.setValue("ui/font_family", theme.ui_family())
        self.settings.setValue("ui/font_px", used)
        # Widgets that pinned a font in their own stylesheet keep it by design
        # (the log, the terminal); everything else follows the new stylesheet.
        #
        # ``refresh_all_sheets`` is what actually makes the change reach the
        # 40+ inline ``font-size`` literals: a per-widget sheet is applied once
        # and Qt never re-polishes it when the application sheet changes, which
        # is why HEX / 扩展命令 / hints used to stay at the old size (R-187).
        theme.refresh_all_sheets(self)
        for widget in [self, *self.findChildren(QWidget)]:
            widget.update()
        self.notify(tr("misc.font", size=used), "ok")
        return used

    def apply_history_limit(self, limit: int) -> int:
        """Re-cap the send history and persist it (R-116)."""
        used = self.send_panel.set_history_limit(limit)
        self.settings.setValue("send/history_limit", used)
        self.settings.setValue(
            "send/history",
            json.dumps(self.send_panel.history_entries(), ensure_ascii=False))
        self.notify(tr("misc.history", count=used), "ok")
        return used

    def clear_history(self) -> None:
        """Forget every remembered command, on disk too (R-116)."""
        self.send_panel.clear_history()
        self.settings.setValue("send/history", "[]")
        self.notify(tr("misc.history.cleared"), "ok")

    def apply_log_font(self, family: str, pixel_size: int) -> int:
        """Change the log face/size (terminal included) on its own (R-096)."""
        used = self.log.apply_font(pixel_size, family)
        for page in (self.console, self.search_panel):
            if hasattr(page, "apply_font"):
                page.apply_font()
        self.settings.setValue("display/log_family", theme.log_family())
        self.settings.setValue("display/log_px", used)
        self.notify(tr("misc.font.log", size=used), "ok")
        return used

    def apply_accent(self, name: str) -> None:
        key = theme.set_accent(name)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.app_stylesheet())
        self.settings.setValue("ui/accent", key)
        # Painted widgets read the tokens on repaint, so one sweep is enough.
        for widget in [self, *self.findChildren(QWidget)]:
            widget.update()
        self.plugins.apply_theme()
        label = theme.ACCENTS[key][0 if language() == "zh" else 1]
        self.notify(tr("misc.accent", name=label), "ok")

    def apply_wallpaper(self, path: str) -> None:
        resolved = theme.set_wallpaper(path or None)
        self.settings.setValue("ui/wallpaper", resolved or "")
        central = self.centralWidget()
        if central is not None:
            central.update()
        self.notify(tr("misc.wallpaper" if resolved else "misc.wallpaper.cleared"), "ok")

    def change_language(self, lang: str) -> None:
        if lang == language():
            return
        set_language(lang)
        self.settings.setValue("ui/language", lang)
        self.retranslate()
        self.plugins.apply_language()
        store = getattr(self, "_plugin_store_dialog", None)
        if store is not None:
            store.retranslate()

    def open_docs(self) -> None:
        DocsDialog(self).exec()

    def open_about(self) -> None:
        AboutDialog(self).exec()

    # ------------------------------------------------------------- update
    #: Delay before the first update check.  Short enough that the dot appears
    #: while the window is still being looked at, late enough that it never runs
    #: while the first port is opening (R-109).
    UPDATE_DELAY_MS = 2000

    def schedule_update_check(self, delay_ms: int | None = None) -> None:
        """Ask GitHub for the newest release, once, after the window settles."""
        from PySide6.QtCore import QTimer

        delay = self.UPDATE_DELAY_MS if delay_ms is None else int(delay_ms)
        QTimer.singleShot(delay, self.update_watcher.start)

    def check_updates_now(self) -> None:
        self.update_watcher.start()

    def update_available(self) -> bool:
        info = self._update_info or self.update_watcher.last
        return bool(info and info.get("available"))

    def _on_update_found(self, info: dict) -> None:
        """Mark the menu button with a dot when a release is newer."""
        self._update_info = dict(info or {})
        if self.update_available():
            self.titlebar.btn_menu.set_badge(True)
            self.titlebar.btn_menu.setToolTip(tr("menu.update.tip"))
        else:
            self.titlebar.btn_menu.set_badge(False)

    def _on_update_failed(self, message: str) -> None:
        """A failed check never nags: it only clears the dot (R-107/R-143).

        The reason is kept on the watcher, so opening 软件更新 shows it instead
        of an endless "正在检查新版本…".
        """
        self.titlebar.btn_menu.set_badge(False)

    def open_update(self) -> None:
        """Menu -> 软件更新: show the release, or check on demand."""
        self.titlebar.btn_menu.set_badge(False)
        info = self._update_info or self.update_watcher.last
        dialog = UpdateDialog(self, info, watcher=self.update_watcher)
        dialog.finished.connect(dialog.deleteLater)
        if not info:
            # No answer yet (or the last attempt failed): run a check that shows
            # its own progress and its own failure reason (R-143).
            dialog.start_check()
        elif self.update_watcher.error:
            dialog.set_status(tr("update.check.failed",
                                 err=self.update_watcher.error))
        dialog.exec()

    # ---------------------------------------------------------- retranslate
    def retranslate(self) -> None:
        """Re-apply every visible string after a language switch."""
        self.titlebar.retranslate()
        self.sec_conn.setText(tr("rail.connection"))
        self.port_combo.setToolTip(tr("rail.port.tip"))
        self.btn_refresh.setToolTip(tr("rail.refresh"))
        self.btn_connect.setText(tr("rail.close") if self._status.get("connected")
                                 else tr("rail.open"))
        self.row_auto.label.setText(tr("rail.autoreconnect"))
        self.row_auto.setToolTip(tr("rail.autoreconnect.tip"))
        self.sec_params.setText(tr("rail.params"))
        for label, key in ((self.lbl_baud, "rail.baud"),
                           (self.lbl_databits, "rail.databits"),
                           (self.lbl_parity, "rail.parity"),
                           (self.lbl_stopbits, "rail.stopbits"),
                           (self.lbl_flow, "rail.flow")):
            label.setText(tr(key))
        self.baud.setToolTip(tr("rail.baud.tip"))
        for index, (_key, label_key) in enumerate(FLOW_KEYS):
            self.flow.setItemText(index, tr(label_key))
        self.sec_display.setText(tr("rail.display"))
        for row, key in ((self.row_hex, "rail.hex"),
                         (self.row_hex_offset, "rail.hex.offset"),
                         (self.row_hex_ascii, "rail.hex.ascii"),
                         (self.row_ts, "rail.ts"),
                         (self.row_ctrl, "rail.ctrl"), (self.row_echo, "rail.echo"),
                         (self.row_scroll, "rail.scroll"), (self.row_awake, "rail.awake")):
            row.label.setText(tr(key))
        self.row_ctrl.setToolTip(tr("rail.ctrl.tip"))
        self.row_hex_offset.setToolTip(tr("rail.hex.offset.tip"))
        self.row_hex_ascii.setToolTip(tr("rail.hex.ascii.tip"))
        self.row_scroll.setToolTip(tr("rail.scroll.tip"))
        self.row_awake.setToolTip(tr("rail.awake.tip"))
        self.sec_record.setText(tr("rail.record"))
        self.sec_window.setText(tr("rail.window"))
        self.btn_record.setText(tr("rail.record.stop") if self.bridge.recorder.active
                                else tr("rail.record.start"))
        self.btn_open_dir.setText(tr("rail.opendir"))
        self.btn_clear_capture.setText(tr("rail.record.clear"))
        self.btn_clear_capture.setToolTip(tr("rail.record.clear.tip"))
        self.record_hint.setText(tr("rail.record.hint"))
        self.btn_clear.setText(tr("log.clear.btn"))
        self.btn_clear.setToolTip(tr("log.clear.tip"))
        self.btn_save_log.setText(tr("log.save.btn"))
        self.btn_save_log.setToolTip(tr("log.save.tip"))
        self.log_hint.setText(tr("log.hint"))
        self._refresh_status_dir()
        self.commands_panel.retranslate()
        self.search_panel.retranslate()
        self.send_panel.retranslate()
        for page in (self.console, self.transfer):
            retranslate = getattr(page, "retranslate", None)
            if retranslate is not None:
                retranslate()
        self._rebuild_tabs()
        self._on_status(self._status)

    # ------------------------------------------------------- CLI control (R-112)
    #: How often the window answers the CLI.  25 ms keeps a scripted session
    #: feeling synchronous without costing anything measurable.
    CONTROL_POLL_MS = 25

    def control_running(self) -> bool:
        """True when the CLI can reach this window."""
        return self.control_server.running

    def _poll_control(self) -> None:
        self.control_server.poll(self._control_request)

    def _control_request(self, request: dict, reply) -> dict | None:
        """Run one CLI request on the GUI thread.

        Returning ``None`` keeps the request open: a ``read`` waits for data and
        a file transfer runs for seconds, so those answer through ``reply``
        later.  The GUI thread is therefore never blocked by an agent (R-112).
        """
        command = str(request.get("cmd") or "").strip()
        handler = getattr(self, f"_control_{command}", None)
        if handler is None or not command:
            return {"ok": False, "error": f"未知命令：{command or '(空)'}"}
        if not self.control_running():
            return {"ok": False, "error": "窗口未就绪"}
        args = request.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        key = str(request.get("id") or command)
        job = self._control_jobs.get(key)
        if job is None:
            job = {"started": False}
            self._control_jobs[key] = job
        job["reply"] = reply
        job["args"] = args
        job["key"] = key
        answer = handler(args, job)
        if answer is not None:
            self._control_jobs.pop(key, None)
        return answer

    # -- helpers -----------------------------------------------------------
    def _control_fail(self, job: dict, message: str, **extra) -> dict:
        job.pop("started", None)
        # Every failure states which port this window owns: the CLI uses it to
        # tell "the port is gone" from a real refusal, and a refusal must never
        # look like a broken binding (R-119).
        status = self.bridge.state()
        return {
            "ok": False, "error": message,
            "connected": bool(status.get("connected")),
            "owned": str(self.control_server.info.get("serial") or ""),
            "port": str(status.get("port") or status.get("want") or ""),
            **extra,
        }

    def _control_snapshot(self, **extra) -> dict:
        status = self.bridge.state()
        # Publish here as well as on the status signal: the status signal is
        # delivered through a queued connection, so a CLI that opened a port
        # could read the endpoint file a few milliseconds before the write and
        # conclude nobody owns the port (R-119).  ``publish`` is a no-op
        # unless the owned port actually changed.
        self._publish_channel(status)
        recorder = getattr(self.bridge, "recorder", None)
        return {
            "ok": True,
            # Every reply carries the rx counter, so the CLI can continue
            # reading exactly where the previous reply stopped (R-112).
            "seq": self._control_seq,
            "connected": bool(status.get("connected")),
            "port": str(status.get("port") or status.get("want") or ""),
            # What this window *owns* right now - the same value the endpoint
            # file publishes, so a CLI can prove its binding is still the one it
            # was given (R-119).
            "owned": str(self.control_server.info.get("serial") or ""),
            "reconnecting": bool(status.get("reconnecting")),
            "error": str(status.get("error") or ""),
            "opts": status.get("opts") or {},
            "stats": self.bridge.manager.stats(),
            "recording": bool(recorder.active) if recorder is not None else False,
            "version": __version__,
            **extra,
        }

    def _control_bytes_since(self, since: int) -> bytes:
        """Everything received after absolute byte ``since`` (0 = nothing yet)."""
        if since <= 0:
            return b""
        count = self._control_seq - int(since)
        if count <= 0:
            return b""
        count = min(count, len(self._control_ring))
        return bytes(self._control_ring[len(self._control_ring) - count:])

    def _control_apply_opts(self, opts: dict) -> None:
        """Show the settings the CLI asked for in the window before opening.

        The CLI and the user must not diverge: if the agent opens COM1 at 9600,
        the baud control shows 9600 as well, and the next click of 打开 uses the
        same values (R-112).
        """
        if not isinstance(opts, dict):
            return
        baud = opts.get("baudrate")
        if baud:
            text = str(int(baud))
            if self.baud.findText(text) < 0:
                self.baud.insertItem(0, text)
            self.baud.setCurrentText(text)

        def pick(combo, value) -> None:
            if value in (None, ""):
                return
            wanted = str(value)
            for index in range(combo.count()):
                text = str(combo.itemText(index))
                if text == wanted or text.rstrip("0").rstrip(".") == wanted:
                    combo.setCurrentIndex(index)
                    return

        pick(self.bytesize, opts.get("bytesize"))
        pick(self.stopbits, opts.get("stopbits"))
        parity = opts.get("parity")
        if parity:
            index = self.parity.findData(str(parity).upper()[:1])
            if index >= 0:
                self.parity.setCurrentIndex(index)
        if opts.get("rtscts"):
            index = self.flow.findData("rtscts")
        elif opts.get("xonxoff"):
            index = self.flow.findData("xonxoff")
        elif "rtscts" in opts or "xonxoff" in opts:
            index = self.flow.findData("none")
        else:
            index = -1
        if index >= 0:
            self.flow.setCurrentIndex(index)
        for key, row in (("dtr", self.row_dtr), ("rts", self.row_rts),
                         ("autoReconnect", self.row_auto)):
            if key in opts:
                row.setChecked(bool(opts[key]))

    # -- control lines (R-156) ---------------------------------------------
    def _control_lines(self, args: dict, _job: dict) -> dict:
        """Read or drive DTR/RTS through the port this window owns.

        The write path goes through ``set_control_lines`` on the manager, not
        through ``_serial_opts()``: raising DTR is not a settings change, it must
        not reopen the port, and it must be visible in the two switches (R-120
        keeps them off by default, so a CLI raise has to show up or the next
        click of 打开 silently drops it).
        """
        patch = args.get("set") or {}
        if not isinstance(patch, dict):
            return self._control_fail(_job, "set 必须是对象")
        # CTS/DSR belong to the device: they are inputs, so writing them is a
        # script error, not a no-op.  Silently ignoring the key made a typo look
        # like a successful reset (R-156).
        unknown = sorted(key for key in patch if key not in ("dtr", "rts"))
        if unknown:
            return self._control_fail(
                _job, f"控制线只能是 dtr / rts，收到：{', '.join(unknown)}")
        wanted = {key: bool(value) for key, value in patch.items()
                  if value is not None}
        if not wanted:
            lines = self.bridge.manager.control_lines()
            return self._control_snapshot(lines=lines)
        result = self.bridge.set_lines(wanted)
        self._mirror_lines(result.get("lines") or {})
        snapshot = self._control_snapshot(lines=result.get("lines") or {},
                                          applied=wanted)
        if result.get("error"):
            snapshot["ok"] = False
            snapshot["error"] = str(result["error"])
        return snapshot

    def _mirror_lines(self, lines: dict) -> None:
        """Show the CLI's line state in the switches without firing a reopen."""
        for key, row in (("dtr", self.row_dtr), ("rts", self.row_rts)):
            if key in lines and row.isChecked() != bool(lines[key]):
                row.blockSignals(True)
                row.setChecked(bool(lines[key]))
                row.blockSignals(False)

    # -- commands ----------------------------------------------------------
    def _control_ping(self, _args: dict, _job: dict) -> dict:
        return self._control_snapshot()

    def _control_state(self, _args: dict, _job: dict) -> dict:
        # ``lines`` here is the *log* line count (existing contract), so the
        # hardware pins go out under ``control_lines`` - reusing the name would
        # have silently turned a number into a dict for every existing caller.
        return self._control_snapshot(lines=self.log.document().blockCount(),
                                      control_lines=self.bridge.manager.control_lines(),
                                      log_px=self.log.font_px())

    def _control_ports(self, args: dict, _job: dict) -> dict:
        """The window's own port list - already cached, never a fresh enumeration.

        ``list_ports.comports()`` costs 1-3 s on this machine (L-130); the
        monitor thread keeps ``manager.ports()`` warm, so asking it is free.
        """
        ports = list(self.bridge.manager.ports())
        if args.get("demo"):
            ports.append({"name": "DEMO", "description": "虚拟演示串口",
                          "hwid": "DEMO\\OmniCOM"})
        return {"ok": True, "count": len(ports), "ports": ports,
                "connected": bool(self.bridge.state().get("connected"))}

    def _control_open(self, args: dict, job: dict) -> dict | None:
        port = str(args.get("port") or "").strip()
        if not port:
            return self._control_fail(job, "缺少 port")
        status = self.bridge.state()
        if status.get("connected") and control.same_port(status.get("port"), port):
            self._sync_port_combo(port)
            return self._control_snapshot(already=True)
        # Never replace the port the user is watching.  A CLI that was pointed
        # at the wrong window used to reopen its own port here, which closed
        # the session the user was looking at and mixed two devices in one log
        # (R-119).  The CLI routes by port name, so this path is a real error.
        busy = str(status.get("port") or status.get("want") or "")
        if ((status.get("connected") or status.get("reconnecting"))
                and busy and not args.get("takeover")):
            return self._control_fail(
                job, f"本窗口正在使用 {busy}，不能同时打开 {port}",
                port=busy, busy=True)
        if not job.get("started"):
            job["started"] = True
            job["deadline"] = time.monotonic() + float(args.get("timeout") or 8.0)
            job["reply"].touch(float(args.get("timeout") or 8.0) + 4.0)
            self._control_apply_opts(args.get("opts") or {})
            self._sync_port_combo(port)
            self.settings.setValue("serial/lastPort", port)
            self.notify(tr("misc.control.open", port=port), "info")
            self.bridge.open(port, self._serial_opts())
            return None
        status = self.bridge.state()
        if status.get("connected") and str(status.get("port")) == port:
            return self._control_snapshot(already=False)
        if status.get("error"):
            return self._control_fail(job, str(status["error"]), port=port)
        if time.monotonic() > float(job.get("deadline") or 0):
            return self._control_fail(job, f"打开 {port} 超时", port=port)
        return None

    def _control_close(self, _args: dict, job: dict) -> dict | None:
        status = self.bridge.state()
        if not (status.get("connected") or status.get("reconnecting")
                or status.get("want")):
            return self._control_snapshot(already=True)
        if not job.get("started"):
            job["started"] = True
            job["deadline"] = time.monotonic() + 10.0
            job["reply"].touch(14.0)
            self.bridge.close()
            return None
        status = self.bridge.state()
        if not (status.get("connected") or status.get("reconnecting")
                or status.get("want")):
            return self._control_snapshot(already=False)
        if time.monotonic() > float(job.get("deadline") or 0):
            return self._control_fail(job, "关闭串口超时")
        return None

    def _control_send(self, args: dict, job: dict) -> dict | None:
        if not job.get("started"):
            try:
                payload = bytes.fromhex(str(args.get("hex") or ""))
            except ValueError:
                return self._control_fail(job, "hex 数据格式不正确")
            if not payload:
                return self._control_fail(job, "没有要发送的内容")
            if not self.bridge.state().get("connected"):
                return self._control_fail(job, "串口未打开")
            job["started"] = True
            job["sent"] = len(payload)
            job["wait"] = max(0.0, float(args.get("wait") or 0.0))
            job["expect"] = [str(item) for item in (args.get("expect") or [])
                             if str(item)]
            job["started_at"] = time.monotonic()
            job["deadline"] = job["started_at"] + job["wait"] + 2.0
            # The answer is searched from the counter *before* the write, which
            # the CLI sends along; a byte that arrives during the write itself
            # is therefore still part of the response (R-112).
            job["since"] = int(args.get("since") or self._control_seq)
            # Everything the CLI sends is printed by the window's own path, so
            # the user sees the same TX line a manual send would print.
            try:
                self.bridge.manager.write(payload)
            except Exception as exc:
                return self._control_fail(job, str(exc))
            self._on_tx_print(payload)
            self._refresh_stats()
            # The answer is searched from the byte *after* the send, which is
            # what ``since`` records for the caller.
            if not job["wait"] and not job["expect"]:
                return self._control_send_result(job, b"")
            return None
        data = self._control_bytes_since(int(job.get("since") or 0))
        if self._control_expect_hit(job["expect"], data):
            return self._control_send_result(job, data, matched=True)
        if time.monotonic() - job["started_at"] >= job["wait"]:
            if job["expect"]:
                return self._control_send_result(job, data, matched=False)
            return self._control_send_result(job, data)
        if time.monotonic() > float(job.get("deadline") or 0):
            return self._control_fail(job, "等待应答超时")
        return None

    def _control_expect_hit(self, needles: list, buffer: bytes) -> bool:
        if not needles or not buffer:
            return False
        text = buffer.decode("utf-8", "replace")
        return any(needle in text for needle in needles)

    def _control_send_result(self, job: dict, data: bytes, **extra) -> dict:
        return self._control_snapshot(
            sent_bytes=int(job.get("sent") or 0),
            response_hex=data.hex(" "),
            response_text=data.decode("utf-8", "replace"),
            **extra)

    def _control_read(self, args: dict, job: dict) -> dict | None:
        if not job.get("started"):
            if not self.bridge.state().get("connected"):
                return self._control_fail(job, "串口未打开")
            job["started"] = True
            job["since"] = int(args.get("since") or self._control_seq)
            job["duration"] = max(0.0, float(args.get("duration") or 0.0))
            job["max_bytes"] = int(args.get("max_bytes") or 0)
            job["expect"] = [str(item) for item in (args.get("expect") or [])
                             if str(item)]
            job["started_at"] = time.monotonic()
            job["deadline"] = job["started_at"] + job["duration"] + 2.0
            job["reply"].touch(job["duration"] + 6.0)
            return None
        data = self._control_bytes_since(int(job.get("since") or 0))
        if self._control_expect_hit(job["expect"], data):
            return self._control_read_result(job, data, matched=True)
        if job["max_bytes"] and len(data) >= job["max_bytes"]:
            return self._control_read_result(job, data[:job["max_bytes"]])
        if time.monotonic() - job["started_at"] >= job["duration"]:
            return self._control_read_result(job, data)
        if time.monotonic() > float(job.get("deadline") or 0):
            return self._control_read_result(job, data)
        return None

    def _control_read_result(self, job: dict, data: bytes,
                             matched: bool | None = None) -> dict:
        extra: dict = {"received_hex": data.hex(" "),
                       "received_text": data.decode("utf-8", "replace"),
                       "received_bytes": len(data),
                       "elapsed_ms": int((time.monotonic()
                                          - float(job.get("started_at") or 0)) * 1000)}
        if matched is not None:
            extra["matched"] = matched
        return self._control_snapshot(**extra)

    def _control_send_file(self, args: dict, job: dict) -> dict | None:
        path = str(args.get("path") or "").strip()
        if not path:
            return self._control_fail(job, "缺少 path")
        if not job.get("started"):
            if not self.bridge.state().get("connected"):
                return self._control_fail(job, "串口未打开")
            try:
                with open(path, "rb") as handle:
                    data = handle.read()
            except OSError as exc:
                return self._control_fail(job, f"读取文件失败：{exc}")
            job["started"] = True
            job["done"] = None
            job["error"] = ""
            job["started_at"] = time.monotonic()
            job["deadline"] = time.monotonic() + 600.0
            name = str(args.get("name") or os.path.basename(path))
            manager = self.bridge.manager

            def work() -> None:
                try:
                    manager.send_file(data, name)
                except Exception as exc:
                    job["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    job["done"] = True

            import threading

            job["reply"].touch(600.0)
            threading.Thread(target=work, name="omnicom-control-file",
                             daemon=True).start()
            return None
        if job.get("done") is None:
            if time.monotonic() > float(job.get("deadline") or 0):
                return self._control_fail(job, "发送文件超时")
            return None
        if job.get("error"):
            return self._control_fail(job, str(job["error"]))
        return self._control_snapshot(
            file=os.path.abspath(path),
            sent_bytes=os.path.getsize(path) if os.path.isfile(path) else 0,
            elapsed_ms=int((time.monotonic()
                            - float(job.get("started_at") or 0)) * 1000))

    def _control_transfer(self, args: dict, job: dict) -> dict | None:
        protocol = str(args.get("proto") or "x")[:1].lower()
        direction = "recv" if str(args.get("dir") or "send") == "recv" else "send"
        if not job.get("started"):
            if not self.bridge.state().get("connected"):
                return self._control_fail(job, "串口未打开")
            data = b""
            name = str(args.get("name") or "file.bin")
            if direction == "send":
                path = str(args.get("path") or "")
                if not path:
                    return self._control_fail(job, "发送模式缺少 path")
                try:
                    with open(path, "rb") as handle:
                        data = handle.read()
                except OSError as exc:
                    return self._control_fail(job, f"读取文件失败：{exc}")
                name = os.path.basename(path)
            job["started"] = True
            job["done"] = None
            job["error"] = ""
            job["logs"] = []
            job["result"] = {}
            job["started_at"] = time.monotonic()
            job["deadline"] = time.monotonic() + 900.0
            manager = self.bridge.manager
            save_dir = str(args.get("save_dir") or paths.received_dir())
            logs: list[str] = job["logs"]

            def work() -> None:
                from ..transfer import run_transfer

                try:
                    job["result"] = run_transfer(
                        manager, protocol, direction, name=name, data=data,
                        log=logs.append, save_dir=save_dir) or {}
                except Exception as exc:
                    job["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    job["done"] = True

            import threading

            job["reply"].touch(900.0)
            threading.Thread(target=work, name="omnicom-control-xfer",
                             daemon=True).start()
            return None
        if job.get("done") is None:
            if time.monotonic() > float(job.get("deadline") or 0):
                return self._control_fail(job, "传输超时")
            return None
        if job.get("error"):
            return self._control_fail(job, str(job["error"]), log=job.get("logs"))
        result = job.get("result") or {}
        return self._control_snapshot(
            proto=protocol.upper() + "MODEM", dir=direction,
            bytes=int(result.get("bytes") or 0),
            files=result.get("files") or [],
            log=job.get("logs"),
            elapsed_ms=int((time.monotonic()
                            - float(job.get("started_at") or 0)) * 1000))

    def _control_record(self, args: dict, _job: dict) -> dict:
        recorder = getattr(self.bridge, "recorder", None)
        if recorder is None:
            return {"ok": False, "error": "没有记录器"}
        action = str(args.get("action") or "state")
        active = bool(recorder.active)
        # Start/stop go through the button handler so the rail label, the hint
        # and the size-refresh timer all follow what the CLI asked for.
        wanted = {"start": True, "stop": False}.get(action)
        if wanted is not None and wanted != active:
            self.toggle_record()
        elif action == "clear":
            self.clear_capture()
        return {"ok": True, "record": recorder.state()}

    def _control_log(self, args: dict, _job: dict) -> dict:
        """The last rendered lines, so an agent can quote what the user sees."""
        limit = max(1, min(2000, int(args.get("limit") or 40)))
        lines = self.log.plain_text().splitlines()[-limit:]
        needle = str(args.get("grep") or "")
        if needle:
            lines = [line for line in lines if needle.lower() in line.lower()]
        return {"ok": True, "lines": lines, "count": len(lines),
                "blocks": self.log.document().blockCount(),
                "trimmed": self.log.trimmed}

    def _control_stats(self, args: dict, _job: dict) -> dict:
        if args.get("reset"):
            return self._control_snapshot(stats=self.bridge.manager.reset_stats())
        return self._control_snapshot()

    def _control_opts(self, args: dict, _job: dict) -> dict:
        """Apply a live options patch to the port this window owns (L-206).

        The scripted ``set`` step in ``session`` sent ``cmd=opts`` with no
        handler on this side, so any script that ran against a window failed at
        that step with 未知命令：opts - the window mode is the default one, so
        in practice the verb was unreachable.  Only live-safe keys are accepted:
        framing changes reopen the port, which the user must not have happen
        behind their back, so they go through the normal connect path instead.
        """
        patch = args.get("opts") or {}
        if not isinstance(patch, dict) or not patch:
            return self._control_snapshot()
        live = {key: value for key, value in patch.items()
                if key in ("baudrate", "dtr", "rts", "autoReconnect")}
        if not live:
            return self._control_fail(
                _job, "窗口模式下只能在线修改 baudrate / dtr / rts / autoReconnect",
                applied=[])
        lines_patch = {key: bool(live[key]) for key in ("dtr", "rts") if key in live}
        if lines_patch:
            result = self.bridge.set_lines(lines_patch)
            self._mirror_lines(result.get("lines") or {})
            live.pop("dtr", None)
            live.pop("rts", None)
        if live:
            self.bridge.update_opts(live)
        self._control_apply_opts(patch)
        return self._control_snapshot(applied=sorted(patch))

    def _control_notify(self, args: dict, _job: dict) -> dict:
        self.notify(str(args.get("text") or ""), str(args.get("level") or "info"))
        return {"ok": True}

    def _control_screen(self, args: dict, _job: dict) -> dict:
        """The device's VT100 screen, for ``omnicom.cli screen`` (R-211/R-218).

        Read through the ``"terminal"`` **plugin service**: the terminal is an
        optional plugin now, so when it is disabled the CLI must say so instead of
        returning a blank screen that looks like a broken device.
        """
        scrollback = int(args.get("scrollback") or 0)
        service = self.plugins.service("terminal")
        if service is None:
            return {"ok": False,
                    "error": "终端插件未启用：请在插件列表中启用 terminal 插件"}
        try:
            state = service.state(scrollback=max(0, scrollback))
        except Exception as exc:
            return {"ok": False, "error": f"读取终端屏幕失败：{exc}"}
        return {"ok": True, **self._control_snapshot(), **state}

    def _control_keys(self, args: dict, _job: dict) -> dict:
        """Send raw terminal key bytes (escape sequences), not a text line.

        ``send`` appends a line ending and is meant for commands; an agent
        driving a full-screen program needs to press *keys* - ``\\x1b[A`` for Up,
        ``\\t``, ``\\x03`` for Ctrl+C - without anything being added (R-211).
        """
        from ..commands import decode_escapes, parse_hex

        raw = args.get("keys")
        if raw is None:
            return {"ok": False, "error": "缺少 keys"}
        text = str(raw)
        if args.get("hex"):
            data = parse_hex(text)
            if data is None:
                return {"ok": False, "error": f"不是合法的十六进制：{text}"}
        else:
            data = decode_escapes(text).encode("utf-8")
        if not data:
            return {"ok": False, "error": "keys 是空的"}
        # `_control_send` takes hex and adds no line ending of its own, so keys
        # arrive unchanged - that is the whole point of this command (R-211).
        result = self._control_send({
            "hex": data.hex(),
            "wait": float(args.get("wait") or 0.0),
            "expect": [str(item) for item in (args.get("expect") or []) if str(item)],
            "since": int(args.get("since") or 0) or None,
        }, _job)
        if result is not None and result.get("ok"):
            result = {**result, "sent": {"bytes": len(data),
                                         "hex": data.hex(" ")},
                      "wait_ms": int(float(args.get("wait") or 0.0) * 1000)}
        return result

    # -------------------------------------------------------------- window
    def window_shadow_px(self) -> int:
        """Shadow ring thickness for the current window state (R-139).

        Zero while maximised: the ring would waste screen space *and* let the
        desktop show through a transparent band along all four edges, because a
        maximized window covers the whole screen.
        """
        if self.isMaximized() or self.isFullScreen():
            return 0
        return max(0, int(theme.WINDOW_SHADOW_PX))

    def _apply_window_shadow(self) -> None:
        """Size the page margins so the visible padding is unchanged (R-139).

        The ring is carved *out of* the existing margins instead of being added
        to them - otherwise the title bar would move 14 px away from every window
        edge, which reads as "the window grew a fat bezel".
        """
        layout = getattr(self, "_root_layout", None)
        central = self.centralWidget()
        if layout is not None:
            base = self._SHADOW_BASE_MARGINS
            ring = self.window_shadow_px()
            layout.setContentsMargins(base[0] + ring, base[1] + ring,
                                      base[2] + ring, base[3] + ring)
        if isinstance(central, Aurora):
            central.shadow_px = self.window_shadow_px()
            central.update()

    #: Visible padding of the page, *before* the shadow ring is added.  The
    #: bottom was 8 px, which together with the caption row and its gap made the
    #: stack under the content 48 px tall (R-198); 4 px leaves the caption row
    #: sitting just off the window edge without touching it.
    _SHADOW_BASE_MARGINS = (10, 8, 10, 4)

    #: A translucent frameless window gets no DWM minimize animation, so the
    #: taskbar icon used to snap the window in and out of existence.  We fade
    #: the layer alpha instead.  Durations match the button motion in
    #: ``widgets.py`` (150-190 ms).
    RESTORE_MS = 180
    MINIMIZE_MS = 150

    def _stop_motion(self) -> None:
        group, self._motion = getattr(self, "_motion", None), None
        if group is not None:
            group.stop()
            group.deleteLater()

    def toggle_maximize(self) -> None:
        """Maximize or restore, immediately (R-153, reverted 2026-11-02).

        A slide + cross-fade was tried and rejected: any motion on this path
        re-lays out all three columns, and the user reads the transition as the
        window "stretching".  Instant ``showMaximized`` / ``showNormal`` is what
        every earlier version did and what Windows users expect from the button.
        """
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _slide_max_rect(self) -> QRect:
        """Rect a maximized frameless window occupies - its available area.

        Measured (``.artifacts/maxrect_probe.py``): ``geometry(max)`` equals
        ``frameGeometry(max)`` equals ``screen.availableGeometry()`` for this
        window style, so a restore needs no guesswork.
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return self.geometry()
        return QRect(screen.availableGeometry())

    def _restore_target(self) -> QRect:
        """Where a restore lands: the remembered rect, never ``geometry()``.

        ``geometry()`` of a maximized window is the *screen*, so using it would
        make every restore fill the desktop again - and an interrupted motion
        would hand back a half-way rect and shrink the window a little more on
        every toggle (L-117).
        """
        stored = getattr(self, "_still_rect", None)
        if stored is not None:
            return QRect(stored)
        normal = self.normalGeometry()
        if normal.width() > 1 and normal.height() > 1:
            return QRect(normal)
        return QRect(self._slide_max_rect().adjusted(0, 0, -420, -300))

    def _motion_group(self, fade_from: float, fade_to: float,
                      duration: int) -> QParallelAnimationGroup:
        """Fade the layer alpha.  Geometry is never touched (L-142).

        Resizing a translucent window while it fades left its last frame painted
        on the desktop on some machines, which looks exactly like "the minimize
        button does nothing" - and left the window stuck in a state where every
        later request returned early.  A maximized window never resized, which is
        why maximizing appeared to repair the button.
        """
        group = QParallelAnimationGroup(self)
        # Only animate alpha when it really changes: every setWindowOpacity()
        # on a layered window repaints it, so a 1.0 -> 1.0 animation would cost a
        # frame of work and could itself blink (R-105).
        if abs(fade_to - fade_from) > 0.001:
            fade = QPropertyAnimation(self, b"windowOpacity", group)
            fade.setDuration(duration)
            fade.setEasingCurve(QEasingCurve.OutCubic)
            fade.setStartValue(fade_from)
            fade.setEndValue(fade_to)
            group.addAnimation(fade)
        return group

    def minimize_with_motion(self) -> None:
        """Fade out, then really minimize.

        Reached from the title-bar button, the taskbar icon, Alt+Space and
        Win+Down (both go through ``nativeEvent`` -> WM_SYSCOMMAND).
        """
        if not self.isVisible() or self.isMinimized():
            return
        # Never early-return on the flag alone: an animation whose group was
        # stopped before it finished used to leave _minimizing True forever, after
        # which the minimize button did nothing at all (R-103).
        self._minimizing = True
        self._minimize_pending = True
        self._stop_motion()
        # A minimize never resizes the window (L-142) - not even to the settled
        # rect of an interrupted animation.  The rect is only remembered so that
        # the restore can put it back while the window is hidden.
        target = self._settled_rect()
        self._still_rect = target
        # Fade from the *current* alpha, never from a hard-coded 1.0: a minimize
        # that arrives while the restore fade is still running used to jump the
        # window back to fully opaque for one frame before fading - the flash the
        # user saw when toggling show/minimize quickly (R-105).
        current = max(0.0, min(1.0, self.windowOpacity()))
        group = self._motion_group(current, 0.0, self.MINIMIZE_MS)
        group.finished.connect(self._finish_minimize)
        self._motion = group
        group.start()
        # Watchdog: the animation only drives the fade.  If it is stopped or
        # never finishes (a platform that refuses to fade a layered window), the
        # window must still minimize (R-103).
        QTimer.singleShot(self.MINIMIZE_MS + 150, self._ensure_minimized)

    def _ensure_minimized(self) -> None:
        if self._minimize_pending:
            self._minimizing = True
            self._finish_minimize()

    def _finish_minimize(self) -> None:
        # Stop and delete the group *before* putting the geometry back: a
        # QPropertyAnimation on ``geometry`` keeps its end value and Qt may apply
        # it one more time after ``setGeometry()``, which left the window at the
        # shrunken size (L-131).
        self._stop_motion()
        self._minimize_pending = False
        if not self._minimizing:
            self._restore_geometry()
            self.setWindowOpacity(1.0)
            return
        self._minimizing = False
        # Minimize *first*, then touch geometry and alpha: a resize of a still
        # composited layer is what left the stale frame behind (L-142).
        self.showMinimized()
        self._restore_geometry()
        # Never stay translucent while hidden: a restore that does not come
        # through :meth:`animate_show` would otherwise show nothing at all.
        self.setWindowOpacity(1.0)

    def _settled_rect(self) -> QRect:
        """Geometry the window must end up at after any animation.

        While a grow/shrink is running ``geometry()`` is a half-way value, so it
        cannot be used as the target (see ``minimize_with_motion``).
        """
        stored = getattr(self, "_still_rect", None)
        return stored if stored is not None else self.geometry()

    def _restore_geometry(self) -> None:
        target = getattr(self, "_still_rect", None)
        self._still_rect = None
        if target is not None and self.geometry() != target:
            self.setGeometry(target)

    def _cancel_minimize(self) -> None:
        self._minimizing = False
        self._minimize_pending = False
        self._stop_motion()
        self._restore_geometry()
        self.setWindowOpacity(1.0)

    def animate_show(self) -> None:
        """Show the window at its real size, with no motion at all.

        A restore used to *grow* the window from 95.5 % back to 100 %.  That is
        a real resize, so every pane re-laid itself out on each animation step and
        the whole UI visibly stretched - the user rejected it outright, and it is
        the correct call: a restore should look instant.  The minimize keeps its
        fade (it hides the window, so nothing can stretch), and the geometry is
        restored in one step here (R-105, revised 2026-10-28).
        """
        self._stop_motion()
        # A restore cancels any pending minimize bookkeeping and puts back the
        # alpha an interrupted fade may have left behind.
        self._minimizing = False
        self._minimize_pending = False
        self.setWindowOpacity(1.0)
        # An interrupted minimize shrank the real geometry, so the target is the
        # remembered rect, not whatever size the window has right now.
        self._restore_geometry()

    def _finish_show(self) -> None:
        self._motion = None
        self.setWindowOpacity(1.0)
        self._still_rect = None

    def nativeEvent(self, event_type, message):
        """Catch the Windows minimize request so it can animate out.

        Qt hides the window the moment SC_MINIMIZE arrives, and a layered
        (translucent) window gets no DWM animation, so the fade has to happen
        *before* Qt acts - which means intercepting the message and minimizing
        ourselves once the animation finished.  Any surprise in the native
        struct falls through to Qt: a broken animation is acceptable, a broken
        window is not.
        """
        try:
            if bytes(event_type) != b"windows_generic_MSG":
                return super().nativeEvent(event_type, message)
            native = ctypes.wintypes.MSG.from_address(int(message))
        except Exception:
            return super().nativeEvent(event_type, message)
        if native.message == WM_NCCALCSIZE and self._strip_frame(
                int(native.lParam), bool(native.wParam)):
            # The client area now covers the whole window, so the frame that
            # WS_THICKFRAME would draw is gone (R-205).
            return True, 0
        if native.message == WM_NCHITTEST:
            hit = self._resize_hit_test(int(native.lParam))
            if hit is not None:
                return True, hit
        if native.message == WM_SETCURSOR and self._apply_resize_cursor(
                int(native.lParam)):
            # Qt answers WM_SETCURSOR itself (returns TRUE), so Windows never
            # draws the sizing cursor - "都没有那个箭头" (R-205 / L-268).  The
            # cursor is set from the *same* hit test that drives the resize,
            # instead of trusting the default handler's idea of the zone (it was
            # measured returning SIZENWSE for a left edge).
            return True, 1
        if native.message == WM_GETMINMAXINFO and self._apply_minmax_info(
                int(native.lParam)):
            return True, 0
        if native.message == 0x0112:
            command = int(native.wParam) & 0xFFF0
            if command == 0xF020:                      # SC_MINIMIZE
                self.minimize_with_motion()
                return True, 0
            if command == 0xF120 and getattr(self, "_minimize_pending", False):
                # SC_RESTORE while the fade-out is still running: the user wants
                # the window back, so drop the pending minimize instead of
                # vanishing a moment later (R-105).
                self._cancel_minimize()
                return True, 0
        return super().nativeEvent(event_type, message)

    def _enable_native_resize(self) -> bool:
        """Add ``WS_THICKFRAME`` so the system offers a resize border (R-205).

        Answering ``WM_NCHITTEST`` is **not enough**: Windows only hit-tests a
        sizing border for a window whose style contains ``WS_THICKFRAME``.  A
        ``Qt.FramelessWindowHint`` window is created as ``WS_POPUP`` **without**
        it, so the handler was never called during real mouse movement (measured:
        0 invocations while the pointer crossed all four edges, L-267) - which is
        exactly why no resize arrow ever appeared.

        The border is then removed again by ``WM_NCCALCSIZE`` (see
        ``_strip_frame``), so the window stays visually frameless.
        """
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            if style & WS_THICKFRAME:
                return True
            user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_THICKFRAME)
            # A style change only takes effect after a frame recalculation.
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE
                                | SWP_NOZORDER | SWP_NOACTIVATE)
            return bool(user32.GetWindowLongW(hwnd, GWL_STYLE) & WS_THICKFRAME)
        except Exception:
            # A window that cannot resize is still a working window.
            return False

    def _apply_resize_cursor(self, lparam: int) -> bool:
        """Set the cursor for the edge under the pointer; True when it was one.

        ``WM_SETCURSOR`` is the message that decides the pointer shape, and Qt
        answers it itself (measured: returns TRUE), so Windows never got to draw
        a sizing cursor even though the hit test already reported ``HTLEFT`` -
        which is exactly why the user saw "都没有那个箭头" (R-205 / L-268).

        **The ``lParam`` is not a point**: its low word is the ``HT*`` code of the
        message and the high word is the mouse message.  Reading it as a screen
        position (an easy mistake - every other hit-test message *is* a point)
        put every position in the same corner, which showed up as "every edge
        gives the NWSE cursor" (L-268).  The pointer is therefore taken from the
        real cursor position and fed through the same ``_resize_hit_test()`` that
        drives the drag, so the arrow and the gesture cannot disagree.
        """
        if self.isMaximized() or self.isFullScreen():
            return False
        try:
            point = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        except Exception:
            return False
        lparam_point = ((point.y & 0xFFFF) << 16) | (point.x & 0xFFFF)
        hit = self._resize_hit_test(lparam_point)
        if hit is None:
            return False
        # HTLEFT/HTRIGHT are *vertical* edges, whose cursor is the horizontal
        # double arrow (IDC_SIZEWE) and vice versa.
        identifier = {
            HTLEFT: IDC_SIZEWE,
            HTRIGHT: IDC_SIZEWE,
            HTTOP: IDC_SIZENS,
            HTBOTTOM: IDC_SIZENS,
            HTTOPLEFT: IDC_SIZENWSE,
            HTBOTTOMRIGHT: IDC_SIZENWSE,
            HTTOPRIGHT: IDC_SIZENESW,
            HTBOTTOMLEFT: IDC_SIZENESW,
        }.get(hit)
        if identifier is None:
            return False
        try:
            user32 = ctypes.windll.user32
            user32.LoadCursorW.argtypes = [ctypes.wintypes.HINSTANCE,
                                           ctypes.c_void_p]
            user32.SetCursor(user32.LoadCursorW(None,
                                                ctypes.c_void_p(identifier)))
        except Exception:
            return False
        return True

    def _apply_minmax_info(self, lparam: int) -> bool:
        """Tell the system the real minimum track size (R-204).

        ``setMinimumSize()`` is a Qt-side clamp: a native sizing gesture goes
        through ``WM_GETMINMAXINFO``, and when nobody answers it the window can
        be dragged smaller than the layout allows - measured, a direct
        ``SetWindowPos(400, 300)`` on this window really did produce a 400x300
        window even though the minimum is 980x620 (L-266).  Filling
        ``ptMinTrackSize`` is what the system expects from a resizable window.
        """
        try:
            info = _MinMaxInfo.from_address(lparam)
        except Exception:
            return False
        if info.ptMinTrackSize.x > 0:
            # Already constrained (a themed frame, or Qt answered first).
            return False
        info.ptMinTrackSize.x = self.minimumWidth()
        info.ptMinTrackSize.y = self.minimumHeight()
        return True

    def _strip_frame(self, lparam: int, was_calc_valid: bool) -> bool:
        """Reduce the non-client area to zero, hiding the new frame (R-205).

        ``WS_THICKFRAME`` buys the resize border at the cost of a visible frame.
        The standard way to keep the system behaviour (sizing, snap, shadows)
        while the client area covers the whole window is to answer
        ``WM_NCCALCSIZE`` with the client rect set to the window rect; the
        window then still *looks* frameless and the Qt layout is untouched.

        ``was_calc_valid`` is the ``wParam``: 0 means ``lParam`` is a plain
        ``RECT`` (client rect), non-zero means it is ``NCCALCSIZE_PARAMS`` and
        ``rgrc[0]`` is the proposed *window* rect, which is exactly what the
        client area has to become.
        """
        try:
            if not was_calc_valid:
                rect = ctypes.cast(lparam, ctypes.POINTER(ctypes.wintypes.RECT))
                geometry = self.geometry()
                rect.contents.left = geometry.left()
                rect.contents.top = geometry.top()
                rect.contents.right = geometry.right() + 1
                rect.contents.bottom = geometry.bottom() + 1
                return True
            params = ctypes.cast(lparam,
                                 ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
            # ``rgrc[0]`` already holds the proposed window rect: leaving it
            # untouched makes the client area cover the whole window, i.e. no
            # frame is drawn at all.
            rect = params.rgrc[0]
            if rect.right <= rect.left or rect.bottom <= rect.top:
                return False
        except Exception:
            return False
        return True

        """Tell the system the real minimum track size (R-204).

        ``setMinimumSize()`` is a Qt-side clamp: a native sizing gesture goes
        through ``WM_GETMINMAXINFO``, and when nobody answers it the window can
        be dragged smaller than the layout allows - measured, a direct
        ``SetWindowPos(400, 300)`` on this window really did produce a 400x300
        window even though the minimum is 980x620 (L-265).  Filling
        ``ptMinTrackSize`` is what the system expects from a resizable window.
        """
        try:
            info = _MinMaxInfo.from_address(lparam)
        except Exception:
            return False
        if info.ptMinTrackSize.x > 0:
            # Already constrained (a themed frame, or Qt answered first).
            return False
        info.ptMinTrackSize.x = self.minimumWidth()
        info.ptMinTrackSize.y = self.minimumHeight()
        return True

    # ------------------------------------------------------- resize by dragging
    #: How close to an edge the pointer has to be to grab it, in px.  Windows
    #: itself uses the resize border of the frame; this window has no frame (it
    #: is frameless and translucent), so the band has to be supplied by hand.
    RESIZE_BORDER = 6
    #: Corner zones are square, the same way the system's own corners are.
    RESIZE_CORNER = 14

    def _resize_hit_test(self, lparam: int) -> int | None:
        """``HT*`` code for an edge/corner, or ``None`` for "normal client".

        A frameless window has no resize border at all, so the user could not
        drag any edge - "要支持非全屏时使用鼠标拖动设置软件窗口的宽高".  Reporting
        the hit to Windows (``WM_NCHITTEST``) is what makes the gesture behave
        exactly like a native window: the cursor changes, the drag is handled by
        the system's own sizing loop, Aero Snap and double-click keep working,
        and nothing is re-implemented in Qt (R-204).

        Disabled while maximised or full screen, where there is nothing to
        resize.  The hit area starts at the *page* edge, i.e. outside the shadow
        ring, so the whole visible border is grabbable.
        """
        if self.isMaximized() or self.isFullScreen():
            return None
        # ``lParam`` packs the screen point of the hit test.
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        local = self.mapFromGlobal(QPoint(x, y))
        width, height = self.width(), self.height()
        border = self.RESIZE_BORDER
        corner = self.RESIZE_CORNER
        left = local.x() < border
        right = local.x() >= width - border
        top = local.y() < border
        bottom = local.y() >= height - border
        near_left = local.x() < corner
        near_right = local.x() >= width - corner
        if top and near_left:
            return HTTOPLEFT
        if top and near_right:
            return HTTOPRIGHT
        if bottom and near_left:
            return HTBOTTOMLEFT
        if bottom and near_right:
            return HTBOTTOMRIGHT
        if top:
            return HTTOP
        if bottom:
            return HTBOTTOM
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        return None

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        # Cancelling a pending minimize must NOT hang off ActivationChange.
        # Clicking the minimize button of a *background* window activates the
        # window first; Qt then delivers the activation change while the fade is
        # already running, and cancelling there meant "minimize does nothing"
        # exactly when the window had been in the background - which is the
        # state the user reported (L-132).  The window is only kept on screen
        # when the user explicitly asks for it back (SC_RESTORE in
        # ``nativeEvent``) or when the fade is interrupted by another minimize.
        if event.type() != QEvent.WindowStateChange:
            return
        if hasattr(self, "titlebar"):
            self.titlebar.sync_max_button()
        # Maximising must drop the shadow ring and restoring must bring it back
        # (R-139).  Done here, not only in ``toggle_maximize``, because Win+Up,
        # Aero Snap and double-click all change the state behind our back.
        if hasattr(self, "_root_layout"):
            self._apply_window_shadow()
        if self.windowState() & Qt.WindowMinimized:
            self._was_minimized = True
            return
        # Coming back from the taskbar: grow in instead of appearing at once.
        if getattr(self, "_was_minimized", False):
            self._was_minimized = False
            self.animate_show()

    def closeEvent(self, event) -> None:
        store = getattr(self, "_plugin_store_dialog", None)
        if store is not None and store.busy():
            store.cancel()
            event.ignore()
            return
        if getattr(self, "_closing", False):
            # A second close can arrive while ``processEvents()`` below is
            # pumping (taskbar "close window", then Alt+F4).  Re-running the
            # bookkeeping would re-sync the settings files twice and, worse,
            # release the window slot while it is still in use.
            event.accept()
            return
        self._closing = True
        # Hide **first** (R-159).  ``closeEvent`` returns before Qt unmaps the
        # window, so everything below used to be paid while the window was still
        # on screen: ``QSettings.sync()`` alone measured ~227 ms on Windows, and
        # the unchanged frame reads as "关闭要等两秒".  Flipping the opacity back
        # to 1.0 is part of it - an interrupted minimize fade may have left the
        # layer translucent, and hiding a translucent window leaves a ghost.
        self._stop_motion()
        self.setWindowOpacity(1.0)
        # Notifications are children of the central widget; dropping them here
        # avoids a last frame in which they flash over the fading window (R-200).
        self.clear_toasts()
        self.hide()
        QApplication.processEvents()
        self.plugins.unload()
        self.command_store.flush()
        self.settings.setValue("ui/geometry", self.saveGeometry())
        self._save_settings()
        self.settings.setValue("ui/sidebar",
                              "0" if self._sidebar_widget.isHidden() else "1")
        self.settings.setValue("ui/split", self.body_split.sizes())
        self.settings.setValue("ui/send_h", self.send_panel.height())
        # Flush this window's file and hand its slot back, so the next launch
        # (or another window) can claim it immediately (R-088).  This is the
        # ~227 ms ``QSettings.sync()``; it now runs with nothing to show, which
        # is why the delay is invisible to the user (L-216).
        self.settings.release()
        # Ask the transfer plugin to stop its worker (R-218).
        service = self.plugins.service("transfer")
        if service is not None:
            with contextlib.suppress(Exception):
                service.abort()
        # Withdraw the CLI endpoint *before* the port goes away: a CLI request
        # must fail fast instead of waiting for a window that is already gone.
        self._control_timer.stop()
        self.control_server.stop()
        self.bridge.shutdown()
        # A keep-awake request belongs to this thread; release it explicitly so
        # the machine can sleep again the moment OmniCOM is gone.
        power.release()
        super().closeEvent(event)
