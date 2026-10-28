"""Main window: frameless light shell, dialogue-driven menus, plugin tabs."""

from __future__ import annotations

import codecs
import contextlib
import os
import subprocess
import sys
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QIcon, QIntValidator, QLinearGradient, QPainter, QPen
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
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, paths, power
from ..commands import CommandStore
from ..i18n import language, set_language, tr
from ..plugins import PluginHost
from . import theme
from .commands_panel import CommandsPanel
from .dialogs import AboutDialog, DocsDialog, PreferencesDialog
from .log_view import LogView
from .search_panel import SearchPanel
from .send_panel import SendPanel
from .terminal import TerminalPage
from .tooltips import install as install_tooltips
from .transfer_panel import TransferPanel
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
from .workers import SerialBridge, TransferWorker

SETTINGS_ORG = "OmniCOM"
SETTINGS_APP = "OmniCOM"


def ui_settings() -> QSettings:
    """QSettings holding persisted UI state.

    ``OMNICOM_SETTINGS_SUFFIX`` gives test runs their own store, so a harness
    that hides the sidebar or resizes the window cannot leak that state into the
    next harness - or into the user's real session.
    """
    suffix = os.environ.get("OMNICOM_SETTINGS_SUFFIX", "")
    return QSettings(SETTINGS_ORG, f"{SETTINGS_APP}{suffix}")


BAUD_RATES = ["9600", "19200", "38400", "57600", "115200", "230400", "460800",
              "921600", "1000000", "1500000", "2000000", "3000000"]
PARITIES = [("N", "None"), ("E", "Even"), ("O", "Odd"), ("M", "Mark"), ("S", "Space")]
FLOW_KEYS = [("none", "flow.none"), ("rtscts", "flow.rtscts"), ("xonxoff", "flow.xonxoff")]

def bytes_per_ms(baud: int) -> float:
    return max(0.5, baud / 10.0 / 1000.0)


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
    """

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(Qt.NoPen)
        target = self.rect()
        painter.fillRect(target, QColor(theme.BG))
        pixmap = theme.wallpaper_pixmap()
        if pixmap is None or pixmap.isNull():
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # Same cover-crop the panes sample from, so the glass content lines up
        # with the photo painted here (R-077).
        cover = theme.cover_pixmap(pixmap, target.width(), target.height())
        if cover is not None:
            painter.drawPixmap(target.topLeft(), cover)
        # A white veil keeps the photo muted enough for the panes to read as
        # frosted glass on top of it; the text that sits straight on the page
        # (the status strip) additionally gets its own plate (R-077).
        painter.fillRect(target, QColor(255, 255, 255, theme.PAGE_VEIL))


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
        names.setSpacing(1)
        self.title = QLabel(tr("app.name"))
        self.title.setStyleSheet("font-size: 15px; font-weight: 650; letter-spacing: .5px;")
        names.addWidget(self.title)
        self.subtitle = QLabel(tr("app.tagline").upper())
        self.subtitle.setStyleSheet(
            f"color: {theme.DIM}; font-size: 8.5px; letter-spacing: 1.2px;")
        names.addWidget(self.subtitle)
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
        self.btn_min.clicked.connect(window.showMinimized)
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
    def _show_menu(self) -> None:
        window = self.window_ref
        menu = QMenu(self)

        menu.addAction(tr("menu.preferences"), window.open_preferences)

        plugin_menu = menu.addMenu(tr("plugin.menu"))
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

    def retranslate(self) -> None:
        self.title.setText(tr("app.name"))
        self.subtitle.setText(tr("app.tagline").upper())
        self.btn_menu.setToolTip(tr("menu.title"))
        self.btn_sidebar.setToolTip(tr("win.sidebar"))
        self.btn_min.setToolTip(tr("win.minimize"))
        self.btn_close.setToolTip(tr("win.close"))
        self.sync_max_button()

    # -- window drag -------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag = (event.globalPosition().toPoint()
                          - self.window_ref.frameGeometry().topLeft())

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            if self.window_ref.isMaximized():
                self.window_ref.showNormal()
                self._drag = QPoint(self.window_ref.width() // 2, 30)
            self.window_ref.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.window_ref.toggle_maximize()


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
        self._hex_offset = 0
        self._appended = 0
        self._status: dict = {}
        self._transfer: TransferWorker | None = None
        self._search_overlay: QWidget | None = None
        self._toasts: list[Toast] = []
        self._page_keys: list[str] = []
        self._plugin_pages: dict[str, QWidget] = {}
        self._saved_split = False
        self._rx_queue: list[tuple[float, bytes]] = []
        self._rx_timer = QTimer(self)
        self._rx_timer.setSingleShot(True)
        self._rx_timer.setInterval(28)          # ~36 fps, cheaper than per-chunk
        self._rx_timer.timeout.connect(self._flush_rx)

        # One app-wide tooltip watcher: 1 s of steady hover, never restarted by
        # a mouse move (R-080).
        self.tooltips = install_tooltips()

        self.plugins = PluginHost(notify=self.notify, log_line=self._plugin_log,
                                  parent=self)

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
        root.setContentsMargins(10, 8, 10, 8)
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
        self.console = TerminalPage()
        self.console.keyBytes.connect(self._on_terminal_keys)
        self.stack.addWidget(self.console)
        self.transfer = TransferPanel(self.notify)
        self.stack.addWidget(self.transfer)
        self.body_split.addWidget(self.stack)

        self.sidebar = self._build_sidebar()
        self.body_split.addWidget(self.sidebar)

        self.body_split.setStretchFactor(0, 0)
        self.body_split.setStretchFactor(1, 1)
        self.body_split.setStretchFactor(2, 0)
        self._split_ready = False

        self.status_strip = self._build_status_strip()
        root.addWidget(self.status_strip)

        self._rebuild_tabs()

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
        layout.setSpacing(7)
        self.rail_layout = layout

        # ---- connection
        self.sec_conn = SectionTitle(tr("rail.connection"))
        layout.addWidget(self.sec_conn)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.port_combo = Combo()
        self.port_combo.setToolTip(tr("rail.port.tip"))
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
        self.row_dtr = SwitchRow("DTR", True, compact=True)
        self.row_rts = SwitchRow("RTS", True, compact=True)
        dtr_row.addWidget(self.row_dtr)
        dtr_row.addWidget(self.row_rts)
        dtr_row.addStretch(1)
        layout.addLayout(dtr_row)
        layout.addWidget(self._divider())

        # ---- display
        self.sec_display = SectionTitle(tr("rail.display"))
        layout.addWidget(self.sec_display)
        self.row_hex = SwitchRow(tr("rail.hex"), False)
        self.row_ts = SwitchRow(tr("rail.ts"), True)
        self.row_ctrl = SwitchRow(tr("rail.ctrl"), False, tooltip=tr("rail.ctrl.tip"))
        self.row_echo = SwitchRow(tr("rail.echo"), True)
        self.row_scroll = SwitchRow(tr("rail.scroll"), True, tooltip=tr("rail.scroll.tip"))
        self.row_awake = SwitchRow(tr("rail.awake"), False, tooltip=tr("rail.awake.tip"))
        for row_widget in (self.row_hex, self.row_ts, self.row_ctrl,
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
        # "Save view" lives in the log toolbar; repeating it here was one of the
        # duplicated controls the user asked to remove (R-044).
        layout.addLayout(row2)

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

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.btn_clear = QPushButton(tr("log.clear"))
        self.btn_clear.setObjectName("Ghost")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        toolbar.addWidget(self.btn_clear)

        self.btn_save_log = QPushButton(tr("log.save"))
        self.btn_save_log.setObjectName("Ghost")
        self.btn_save_log.setCursor(Qt.PointingHandCursor)
        toolbar.addWidget(self.btn_save_log)

        toolbar.addStretch(1)
        self.trim_badge = Badge("")
        toolbar.addWidget(self.trim_badge)
        log_layout.addLayout(toolbar)

        log_host = QWidget()
        log_host_layout = QVBoxLayout(log_host)
        log_host_layout.setContentsMargins(0, 0, 0, 0)
        log_host_layout.setSpacing(0)

        self.log = LogView()
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

        layout.addWidget(self.cmd_block, 3)
        layout.addWidget(self.search_block, 2)
        self.sidebar_box = layout
        self._sidebar_widget = sidebar
        return sidebar

    def relayout_sidebar(self) -> None:
        """Give the remaining expanded block all the vertical space."""
        cmd_open = not self.commands_panel._collapsed
        search_open = not self.search_panel._collapsed
        big = 1 << 20
        if cmd_open and search_open:
            self.sidebar_box.setStretchFactor(self.cmd_block, 3)
            self.sidebar_box.setStretchFactor(self.search_block, 2)
            self.cmd_block.setMaximumHeight(big)
            self.search_block.setMaximumHeight(big)
        elif cmd_open:
            self.sidebar_box.setStretchFactor(self.cmd_block, 1)
            self.sidebar_box.setStretchFactor(self.search_block, 0)
            self.cmd_block.setMaximumHeight(big)
            self.search_block.setMaximumHeight(self.search_panel.header.height() + 6)
        elif search_open:
            self.sidebar_box.setStretchFactor(self.cmd_block, 0)
            self.sidebar_box.setStretchFactor(self.search_block, 1)
            self.cmd_block.setMaximumHeight(self.commands_panel.header.height() + 6)
            self.search_block.setMaximumHeight(big)
        else:
            self.sidebar_box.setStretchFactor(self.cmd_block, 0)
            self.sidebar_box.setStretchFactor(self.search_block, 0)
            self.cmd_block.setMaximumHeight(self.commands_panel.header.height() + 6)
            self.search_block.setMaximumHeight(self.search_panel.header.height() + 6)

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
        strip.setFixedHeight(26)
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
            self.console.view.setFocus()
        elif page.startswith("plugin:"):
            widget = self.stack.widget(index)
            if widget is not None:
                widget.setFocus()

    # -------------------------------------------------------------- plugins
    def _load_plugins(self) -> None:
        self.plugins.load()
        for info in self.plugins.loaded():
            widget = info.widget
            if widget is None:
                continue
            key = f"plugin:{info.id}"
            self._plugin_pages[key] = widget
            self.stack.addWidget(widget)
        self._rebuild_tabs()

    def reload_plugins(self) -> None:
        """Tear the plugin pages down, then load whatever is on disk now."""
        for key in list(self._plugin_pages):
            widget = self._plugin_pages.pop(key)
            index = self.stack.indexOf(widget)
            if index >= 0:
                self.stack.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self.plugins.reload()
        for info in self.plugins.loaded():
            widget = info.widget
            if widget is None:
                continue
            key = f"plugin:{info.id}"
            self._plugin_pages[key] = widget
            self.stack.addWidget(widget)
        self._rebuild_tabs()
        self.notify(tr("plugin.reloaded", n=len(self.plugins.loaded())), "ok")

    def open_plugin_dir(self) -> None:
        target = os.path.join(paths.resource_dir(), "plugins")
        os.makedirs(target, exist_ok=True)
        self._open_path(target)

    def _plugin_log(self, text: str) -> None:
        self._log_line(time.time() * 1000, "··", text, "sys")

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

        self.btn_clear.clicked.connect(self.clear_log)
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

        self.row_auto.toggled.connect(lambda _: self._push_opts())
        for row in (self.row_dtr, self.row_rts):
            row.toggled.connect(lambda _: self._push_opts())
        for combo in (self.flow, self.parity, self.stopbits, self.bytesize):
            combo.currentIndexChanged.connect(lambda _: self._push_opts())
        self.baud.activated.connect(lambda _: self._commit_baud())
        self.baud.lineEdit().editingFinished.connect(self._commit_baud)

        self.row_hex.toggled.connect(self._on_hex_mode)
        self.row_ts.toggled.connect(lambda value: setattr(self.log, "show_ts", value))
        self.row_ctrl.toggled.connect(self._on_ctrl_mode)
        self.row_echo.toggled.connect(self._save_settings)
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
        self.transfer.startRequested.connect(self.start_transfer)
        self.transfer.abortRequested.connect(self.abort_transfer)

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
        self._save_settings()
        if enabled and not accepted:
            # Never pretend it worked: a silent no-op would look like a bug.
            self.notify(tr("rail.awake.failed"), "warn")

    # ------------------------------------------------------------- settings
    def _restore_settings(self) -> None:
        s = self.settings
        geometry = s.value("ui/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        if s.value("ui/sidebar", "1") == "0":
            self._sidebar_widget.hide()
        with contextlib.suppress(TypeError, ValueError):
            self._saved_send_h = int(s.value("ui/send_h", 0) or 0)
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
        self.row_auto.setChecked(s.value("serial/auto", "1") == "1")
        self.row_dtr.setChecked(s.value("serial/dtr", "1") == "1")
        self.row_rts.setChecked(s.value("serial/rts", "1") == "1")
        self.row_hex.setChecked(s.value("display/hex", "0") == "1")
        self.row_ts.setChecked(s.value("display/ts", "1") == "1")
        self.row_ctrl.setChecked(s.value("display/ctrl", "0") == "1")
        self.row_echo.setChecked(s.value("display/echo", "1") == "1")
        self.row_scroll.setChecked(s.value("display/scroll", "1") == "1")
        self.row_awake.setChecked(s.value("display/awake", "0") == "1")
        self.log.show_ts = self.row_ts.isChecked()
        self.log.autoscroll = self.row_scroll.isChecked()
        self._apply_keep_awake(self.row_awake.isChecked())
        self._on_ports(self.bridge.manager.ports())

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("serial/baud", self.baud.currentText())
        s.setValue("serial/bytesize", self.bytesize.currentText())
        s.setValue("serial/stopbits", self.stopbits.currentText())
        s.setValue("serial/parity", self.parity.currentData())
        s.setValue("serial/flow", self.flow.currentData())
        s.setValue("serial/auto", "1" if self.row_auto.isChecked() else "0")
        s.setValue("serial/dtr", "1" if self.row_dtr.isChecked() else "0")
        s.setValue("serial/rts", "1" if self.row_rts.isChecked() else "0")
        s.setValue("display/hex", "1" if self.row_hex.isChecked() else "0")
        s.setValue("display/ts", "1" if self.row_ts.isChecked() else "0")
        s.setValue("display/ctrl", "1" if self.row_ctrl.isChecked() else "0")
        s.setValue("display/echo", "1" if self.row_echo.isChecked() else "0")
        s.setValue("display/scroll", "1" if self.row_scroll.isChecked() else "0")
        s.setValue("display/awake", "1" if self.row_awake.isChecked() else "0")
        s.setValue("display/buffer_mb", self.log.buffer_mb())
        s.setValue("ui/language", language())
        s.setValue("ui/accent", theme.accent())
        s.setValue("ui/wallpaper", theme.wallpaper() or "")

    def _serial_opts(self) -> dict:
        try:
            baud = int(self.baud.currentText() or 115200)
        except ValueError:
            baud = 115200
        baud = max(50, min(12_000_000, baud))
        return {
            "baudrate": baud,
            "bytesize": int(self.bytesize.currentText()),
            "stopbits": float(self.stopbits.currentText()),
            "parity": self.parity.currentData(),
            "flow": self.flow.currentData(),
            "rtscts": self.flow.currentData() == "rtscts",
            "xonxoff": self.flow.currentData() == "xonxoff",
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
            detail = port.get("description") or ""
            label = port["name"]
            if detail and detail != "n/a" and port["name"] not in detail:
                label = f"{port['name']} · {detail}"
            self.port_combo.addItem(label, port["name"])
            index = self.port_combo.count() - 1
            self.port_combo.setItemData(index, port.get("hwid") or "", Qt.ToolTipRole)
        preferred = self._status.get("port") or current or self.settings.value(
            "serial/lastPort", "")
        if preferred:
            index = self.port_combo.findData(preferred)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)
        self.port_combo.blockSignals(False)

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
        self.console.reset()
        self.notify(tr("misc.switching", port=port), "info")
        self.bridge.open(port, self._serial_opts())

    def _on_status(self, status: dict) -> None:
        self._status = status
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
        self._rx_queue.append((ts, data))
        if not self._rx_timer.isActive():
            self._rx_timer.start()

    def _flush_rx(self) -> None:
        if not self._rx_queue:
            return
        queue, self._rx_queue = self._rx_queue, []
        console = self.console.isVisible()
        self.log.begin_batch()
        try:
            for ts, data in queue:
                self._feed(ts, data)
                if console:
                    self.console.feed(data)
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

    def _log_line(self, ts: float, direction: str, text: str, kind: str = "rx",
                  keywords=None) -> int:
        """Append a rendered line **and** keep the live search results in step.

        Every path that writes to the log goes through here.  The TX path used
        to append on its own and never told the search panel, so results stopped
        growing while sending - that was the reported bug (L-089).
        """
        words = self.search_panel.keywords if keywords is None else keywords
        composed = self.log.compose_line(ts, direction, text)
        block = self.log.append_line(ts, direction, text, kind, keywords=words)
        self._appended += 1
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
        while len(merged) - index >= 16:
            row = merged[index:index + 16]
            text = self._hex_row(self._hex_offset, row)
            self.log.clear_pending()
            self._log_line(ts, "RX", text, "rx", keywords)
            self._hex_offset += 16
            index += 16
        self._hex_partial = merged[index:]
        if self._hex_partial:
            self.log.set_pending(self._hex_row(self._hex_offset, self._hex_partial))
        else:
            self.log.clear_pending()

    @staticmethod
    def _hex_row(offset: int, row: bytes) -> str:
        hex_part = " ".join(f"{byte:02x}" for byte in row)
        hex_part = hex_part.ljust(47)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in row)
        return f"{offset:08x}  {hex_part}  |{ascii_part}|"

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
        if value:
            self.log.scroll_bottom()
        self._save_settings()
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
        self.log.scroll_bottom()
        self.scroll_pill.hide_pill()

    def _on_terminal_keys(self, data: bytes) -> None:
        if not data:
            return
        if not self._status.get("connected"):
            self.notify(tr("console.needport"), "warn")
            return
        try:
            self.bridge.manager.write(data)
        except Exception as exc:
            self.notify(str(exc), "warn")

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
        self.sidebar_box.addWidget(self.search_block, 2)
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
        self._hex_offset = 0
        self.trim_badge.hide()
        self.status_trim.setText("")
        self.log_hint.show()
        self.scroll_pill.hide_pill()
        self.search_panel.clear_results()

    def _on_hex_mode(self, enabled: bool) -> None:
        self._save_settings()
        self._clear_decoder()
        if self._appended > 0:
            self.clear_log()
            self._log_line(time.time() * 1000, "··",
                          tr("log.clear"), "sys")
        self.status_line.setText(tr("rail.hex") + (" ✓" if enabled else " ✗"))

    def _on_ctrl_mode(self, enabled: bool) -> None:
        self._save_settings()
        self._clear_decoder()
        if self._pending_text:
            self.log.set_pending(self._clean(self._pending_text, pending=True))
        self.status_line.setText(tr("rail.ctrl") + (" ✓" if enabled else " ✗"))

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
        if not self._status.get("connected"):
            self.notify(tr("misc.needport"), "warn")
            return
        if self._transfer is not None and self._transfer.isRunning():
            return
        data = b""
        name = "file.bin"
        if direction == "send":
            try:
                with open(path, "rb") as handle:
                    data = handle.read()
            except OSError as exc:
                self.notify(f"{exc}", "error")
                return
            name = os.path.basename(path)
        self.transfer.set_running(True)
        self.transfer.set_state(f"{protocol.upper()}MODEM")
        self.transfer.progress.setValue(0)
        self.transfer.log(f"=== {protocol.upper()}MODEM {direction} · {name} ===")
        self._log_line(time.time() * 1000, "··",
                       f"{protocol.upper()}MODEM {direction} · {name}", "evt")
        self._transfer = TransferWorker(self.bridge.manager, protocol, direction, name,
                                        data, self)
        self._transfer.log.connect(self.transfer.log)
        self._transfer.progress.connect(self.transfer.set_progress)
        self._transfer.succeeded.connect(self._transfer_done)
        self._transfer.failed.connect(self._transfer_failed)
        self._transfer.aborted.connect(self._transfer_aborted)
        self._transfer.start()

    def _transfer_done(self, result: dict) -> None:
        files = result.get("files") or []
        if files:
            listing = ", ".join(item.get("path", item.get("name", "")) for item in files)
            self.transfer.finish(True, human_bytes(result.get("bytes", 0)), listing)
        else:
            self.transfer.finish(True, human_bytes(result.get("bytes", 0)))
        self.notify(human_bytes(result.get("bytes", 0)), "ok")
        self.transfer.set_running(False)

    def _transfer_failed(self, message: str) -> None:
        self.transfer.finish(False, message)
        self.notify(message, "error")

    def _transfer_aborted(self, message: str) -> None:
        self.transfer.finish(False, message or "—")
        self.notify(message or "—", "warn")

    def abort_transfer(self) -> None:
        if self._transfer is not None:
            self._transfer.cancel()
            self.transfer.set_state("…")

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
    def notify(self, text: str, level: str = "info", timeout: int = 3600) -> None:
        if not text:
            return
        if level in ("warn", "error"):
            self._log_line(time.time() * 1000, "!!", text, "evt")
        toast = Toast(self, text, level, timeout)
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
        offset = sum(item.height() + 10 for item in self._toasts)
        x = max(16, self.width() - toast.width() - 26)
        y = max(70, self.height() - toast.height() - 54 - offset)
        toast.move(x, y)
        toast.play_in()
        self._toasts.append(toast)
        QTimer.singleShot(timeout, toast.dismiss)

    # -------------------------------------------------------------- dialogs
    def open_preferences(self) -> None:
        dialog = PreferencesDialog(
            self, language(),
            log_dir=str(self.settings.value("paths/log_dir", "")) or paths.logs_dir(),
            accent=theme.accent(),
            wallpaper=theme.wallpaper() or "",
            buffer_mb=self.log.buffer_mb())
        dialog.languageChanged.connect(self.change_language)
        dialog.logDirChanged.connect(self.apply_log_dir)
        dialog.accentChanged.connect(self.apply_accent)
        dialog.wallpaperChanged.connect(self.apply_wallpaper)
        dialog.bufferChanged.connect(self.apply_log_buffer)
        dialog.exec()

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

    def apply_accent(self, name: str) -> None:
        key = theme.set_accent(name)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.app_stylesheet())
        self.settings.setValue("ui/accent", key)
        # Painted widgets read the tokens on repaint, so one sweep is enough.
        for widget in [self, *self.findChildren(QWidget)]:
            widget.update()
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

    def open_docs(self) -> None:
        DocsDialog(self).exec()

    def open_about(self) -> None:
        AboutDialog(self).exec()

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
        for row, key in ((self.row_hex, "rail.hex"), (self.row_ts, "rail.ts"),
                         (self.row_ctrl, "rail.ctrl"), (self.row_echo, "rail.echo"),
                         (self.row_scroll, "rail.scroll"), (self.row_awake, "rail.awake")):
            row.label.setText(tr(key))
        self.row_ctrl.setToolTip(tr("rail.ctrl.tip"))
        self.row_scroll.setToolTip(tr("rail.scroll.tip"))
        self.row_awake.setToolTip(tr("rail.awake.tip"))
        self.sec_record.setText(tr("rail.record"))
        self.btn_record.setText(tr("rail.record.stop") if self.bridge.recorder.active
                                else tr("rail.record.start"))
        self.btn_open_dir.setText(tr("rail.opendir"))
        self.btn_clear_capture.setText(tr("rail.record.clear"))
        self.btn_clear_capture.setToolTip(tr("rail.record.clear.tip"))
        self.record_hint.setText(tr("rail.record.hint"))
        self.btn_clear.setText(tr("log.clear"))
        self.btn_save_log.setText(tr("log.save"))
        self.log_hint.setText(tr("log.hint"))
        self._refresh_status_dir()
        self.commands_panel.retranslate()
        self.search_panel.retranslate()
        self.console.retranslate()
        self.send_panel.retranslate()
        self.transfer.retranslate()
        self._rebuild_tabs()
        self._on_status(self._status)

    # -------------------------------------------------------------- window
    def toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.titlebar.sync_max_button()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange and hasattr(self, "titlebar"):
            self.titlebar.sync_max_button()

    def closeEvent(self, event) -> None:
        self.command_store.flush()
        self.settings.setValue("ui/geometry", self.saveGeometry())
        self._save_settings()
        self.settings.setValue("ui/sidebar",
                              "0" if self._sidebar_widget.isHidden() else "1")
        self.settings.setValue("ui/split", self.body_split.sizes())
        self.settings.setValue("ui/send_h", self.send_panel.height())
        if self._transfer is not None and self._transfer.isRunning():
            self._transfer.cancel()
            self._transfer.wait(2000)
        self.bridge.shutdown()
        # A keep-awake request belongs to this thread; release it explicitly so
        # the machine can sleep again the moment OmniCOM is gone.
        power.release()
        super().closeEvent(event)
