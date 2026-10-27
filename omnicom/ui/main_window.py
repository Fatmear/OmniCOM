"""Main window: frameless glass shell, left rail, tabs, log, sidebar."""

from __future__ import annotations

import codecs
import os
import subprocess
import sys
import time

from PySide6.QtCore import QEvent, QPoint, QRect, QSettings, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, paths
from ..commands import CommandStore
from . import theme
from .commands_panel import CommandsPanel
from .log_view import LogView
from .search_panel import SearchPanel
from .send_panel import SendPanel
from .terminal import TerminalPage
from .transfer_panel import TransferPanel
from .widgets import (
    Badge,
    Combo,
    GlassFrame,
    GlyphButton,
    ScrollBottomPill,
    SectionTitle,
    StatusPill,
    SwitchRow,
    Toast,
)
from .workers import SerialBridge, TransferWorker

SETTINGS_ORG = "OmniCOM"
SETTINGS_APP = "OmniCOM"


def ui_settings() -> QSettings:
    """QSettings holding persisted UI state.

    ``OMNICOM_SETTINGS_SUFFIX`` gives test runs (``tools/*.py``) their own
    store, so a harness that deliberately hides the sidebar or resizes the
    window cannot leak that state into the next harness - or into the user's
    real session.
    """
    suffix = os.environ.get("OMNICOM_SETTINGS_SUFFIX", "")
    return QSettings(SETTINGS_ORG, f"{SETTINGS_APP}{suffix}")


BAUD_RATES = ["9600", "19200", "38400", "57600", "115200", "230400", "460800",
              "921600", "1000000", "1500000", "2000000", "3000000"]
PARITIES = [("N", "None"), ("E", "Even"), ("O", "Odd"), ("M", "Mark"), ("S", "Space")]
FLOWS = [("none", "无"), ("rtscts", "硬件 RTS/CTS"), ("xonxoff", "软件 XON/XOFF")]

VIEW_LINES = 20000


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


class Aurora(QWidget):
    """Solid backdrop — flat colour only, no grid, no blobs (R-020)."""

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(theme.BG)))
        painter.drawRoundedRect(self.rect(), 18, 18)


class TitleBar(GlassFrame):
    """Single glass bar: brand, page tabs, connection state, counters, controls."""

    def __init__(self, window: OmniWindow) -> None:
        super().__init__(None, radius=16, strong=True, shadow=True)
        self.window_ref = window
        self._drag: QPoint | None = None
        self.setFixedHeight(62)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 0, 12, 0)
        layout.setSpacing(14)

        # ---- brand
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel("◠")
        logo.setFixedSize(38, 38)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet(
            "QLabel { border-radius: 13px; font-size: 21px; color: #FFFFFF;"
            " background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 #12B39E, stop:1 #7C5CFA);"
            " border: 1px solid rgba(18,26,43,40); }")
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(1)
        title = QLabel("OmniCOM")
        title.setStyleSheet("font-size: 15px; font-weight: 650; letter-spacing: .5px;")
        subtitle = QLabel("EMBEDDED SERIAL DEBUGGER")
        subtitle.setStyleSheet(
            f"color: {theme.DIM}; font-size: 8.5px; letter-spacing: 1.3px;")
        names.addWidget(title)
        names.addWidget(subtitle)
        brand.addLayout(names)
        layout.addLayout(brand)

        layout.addWidget(self._vline())

        # ---- page tabs
        self.tabs = QButtonGroup(self)
        self.tabs.setExclusive(True)
        tab_box = QFrame()
        tab_box.setObjectName("TabBox")
        tab_box.setFixedHeight(38)
        tab_box.setStyleSheet(
            f"QFrame#TabBox {{ background: {theme.WASH};"
            f" border: 1px solid {theme.LINE_SOFT}; border-radius: 14px; }}")
        tab_layout = QHBoxLayout(tab_box)
        tab_layout.setContentsMargins(4, 4, 4, 4)
        tab_layout.setSpacing(2)
        for index, (key, label) in enumerate((("terminal", "串口日志"),
                                             ("console", "终端模式"),
                                             ("transfer", "文件传输"))):
            button = QPushButton(label)
            button.setObjectName("Seg")
            button.setFixedHeight(28)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty("page", key)
            if index == 0:
                button.setChecked(True)
            self.tabs.addButton(button)
            tab_layout.addWidget(button)
        self.tabs.buttonClicked.connect(
            lambda button: window.switch_page(button.property("page")))
        layout.addWidget(tab_box)

        layout.addStretch(1)

        # ---- right cluster
        self.status_chip = StatusPill(self, "未连接")
        layout.addWidget(self.status_chip)

        self.counter = QLabel()
        self.counter.setTextFormat(Qt.RichText)
        self.counter.setStyleSheet(
            f"color: {theme.DIM}; font-size: 11.5px; font-family: {theme.MONO_FONT};")
        layout.addWidget(self.counter)
        self.set_counters(0, 0)

        layout.addWidget(self._vline())

        self.btn_sidebar = GlyphButton(GlyphButton.SIDEBAR, self)
        self.btn_sidebar.setToolTip("显示 / 隐藏右侧栏")
        self.btn_sidebar.clicked.connect(window.toggle_sidebar)
        layout.addWidget(self.btn_sidebar)

        self.btn_min = GlyphButton(GlyphButton.MINIMIZE, self)
        self.btn_min.setToolTip("最小化")
        self.btn_min.clicked.connect(window.showMinimized)
        layout.addWidget(self.btn_min)

        self.btn_max = GlyphButton(GlyphButton.MAXIMIZE, self)
        self.btn_max.setToolTip("最大化 / 还原")
        self.btn_max.clicked.connect(window.toggle_maximize)
        layout.addWidget(self.btn_max)
        self.max_button = self.btn_max

        self.btn_close = GlyphButton(GlyphButton.CLOSE, self, danger=True)
        self.btn_close.setToolTip("关闭")
        self.btn_close.clicked.connect(window.close)
        layout.addWidget(self.btn_close)

    # ------------------------------------------------------------------ parts
    @staticmethod
    def _vline() -> QFrame:
        line = QFrame()
        line.setFixedSize(1, 22)
        line.setStyleSheet(f"background: {theme.LINE}; border: 0;")
        return line

    def _tool(self, text: str, tip: str, close: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("TopIconClose" if close else "TopIcon")
        button.setFixedSize(30, 30)
        button.setToolTip(tip)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def set_counters(self, rx: int, tx: int) -> None:
        self.counter.setText(
            f'<span style="color:{theme.DIM}">RX</span> '
            f'<span style="color:{theme.TEAL}">{human_size(rx)}</span>'
            f'<span style="color:{theme.DIM}"> &nbsp;·&nbsp; </span>'
            f'<span style="color:{theme.DIM}">TX</span> '
            f'<span style="color:{theme.VIOLET}">{human_size(tx)}</span>')

    # -- window drag --------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.window_ref.frameGeometry().topLeft()

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


class OmniWindow(QMainWindow):
    def __init__(self, demo: bool = False, command_path: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"OmniCOM {__version__}")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowMinMaxButtonsHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(1020, 640)
        self.resize(1440, 900)

        self.settings = ui_settings()
        self.bridge = SerialBridge(demo=demo)
        self.command_store = CommandStore(command_path)
        self.demo = demo

        # rx pipeline state
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._pending_text = ""
        self._pending_ts = 0.0
        self._hex_partial = b""
        self._hex_offset = 0
        self._search_overlay: QWidget | None = None
        self._toasts: list[Toast] = []
        self._appended = 0
        self._status = {}
        self._transfer: TransferWorker | None = None
        self._snapshot_text = ""

        self._build_ui()
        self._wire()
        self._restore_settings()
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

        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 1)

        self.rail = self._build_rail()
        body.addWidget(self.rail)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_terminal_page())
        self.console = TerminalPage()
        self.console.keyBytes.connect(self._on_terminal_keys)
        self.stack.addWidget(self.console)
        self.transfer = TransferPanel(self.notify)
        self.stack.addWidget(self.transfer)
        body.addWidget(self.stack, 1)

        self.sidebar = self._build_sidebar()
        body.addWidget(self.sidebar)

        self.status_strip = self._build_status_strip()
        root.addWidget(self.status_strip)

    # ------------------------------------------------------------------ rail
    def _build_rail(self) -> QWidget:
        rail = GlassFrame(radius=18)
        rail.setFixedWidth(302)
        outer = QVBoxLayout(rail)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(13, 12, 13, 14)
        layout.setSpacing(7)

        # ---- connection
        layout.addWidget(SectionTitle("连接"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.port_combo = Combo()
        self.port_combo.setToolTip("选择串口；已打开时切换会自动关闭旧串口并连上新串口")
        self.port_combo.currentIndexChanged.connect(self._on_port_picked)
        row.addWidget(self.port_combo, 1)
        self.btn_refresh = GlyphButton(GlyphButton.REFRESH, self, size=theme.CTRL_H,
                                       object_name="IconBtn")
        self.btn_refresh.setToolTip("刷新串口列表")
        row.addWidget(self.btn_refresh)
        layout.addLayout(row)

        self.btn_connect = QPushButton("打开串口")
        self.btn_connect.setObjectName("Primary")
        self.btn_connect.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.btn_connect)

        self.row_auto = SwitchRow("自动重连", True,
                                  tooltip="串口被拔出后，重新插入会自动打开（按路径或序列号识别）")
        layout.addWidget(self.row_auto)
        layout.addWidget(self._divider())

        # ---- serial parameters
        layout.addWidget(SectionTitle("串口参数"))
        grid = QHBoxLayout()
        grid.setSpacing(8)
        left_col = QVBoxLayout()
        left_col.setSpacing(6)
        left_col.addWidget(self._dim("波特率"))
        self.baud = Combo(editable=True)
        self.baud.addItems(BAUD_RATES)
        self.baud.setCurrentText("115200")
        self.baud.setToolTip("点一下弹出常用波特率；也可以直接输入任意数值")
        left_col.addWidget(self.baud)
        left_col.addWidget(self._dim("数据位"))
        self.bytesize = Combo()
        for value in ("8", "7", "6", "5"):
            self.bytesize.addItem(value)
        left_col.addWidget(self.bytesize)
        grid.addLayout(left_col, 1)

        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        right_col.addWidget(self._dim("校验位"))
        self.parity = Combo()
        for key, label in PARITIES:
            self.parity.addItem(label, key)
        right_col.addWidget(self.parity)
        right_col.addWidget(self._dim("停止位"))
        self.stopbits = Combo()
        for value in ("1", "1.5", "2"):
            self.stopbits.addItem(value)
        right_col.addWidget(self.stopbits)
        grid.addLayout(right_col, 1)
        layout.addLayout(grid)

        layout.addWidget(self._dim("流控"))
        self.flow = Combo()
        for key, label in FLOWS:
            self.flow.addItem(label, key)
        layout.addWidget(self.flow)

        dtr_row = QHBoxLayout()
        dtr_row.setSpacing(6)
        self.row_dtr = SwitchRow("DTR", True)
        self.row_rts = SwitchRow("RTS", True)
        dtr_row.addWidget(self.row_dtr, 1)
        dtr_row.addWidget(self.row_rts, 1)
        layout.addLayout(dtr_row)
        layout.addWidget(self._divider())

        # ---- display
        layout.addWidget(SectionTitle("显示"))
        self.row_hex = SwitchRow("HEX 显示", False)
        self.row_ts = SwitchRow("时间戳（毫秒）", True)
        self.row_ctrl = SwitchRow("显示 \\r \\n 等控制字符", False,
                                  tooltip="打开后把不可见字符按 \\r \\n \\0 \\xNN 显示为文本")
        self.row_echo = SwitchRow("回显发送内容", True)
        self.row_scroll = SwitchRow("自动滚动到最新", True,
                                    tooltip="打开后始终跟着最新一行；关闭后可自由翻阅历史，"
                                            "底部会浮出「回到最新」按钮")
        for row_widget in (self.row_hex, self.row_ts, self.row_ctrl, self.row_echo, self.row_scroll):
            layout.addWidget(row_widget)
        layout.addWidget(self._divider())

        # ---- record
        layout.addWidget(SectionTitle("接收记录"))
        self.btn_record = QPushButton("●  开始记录到文件")
        self.btn_record.setObjectName("Ghost")
        self.btn_record.setMinimumHeight(32)
        self.btn_record.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.btn_record)
        self.record_hint = QLabel("记录在后台进行，暂停打印也不会漏字节。")
        self.record_hint.setWordWrap(True)
        self.record_hint.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        layout.addWidget(self.record_hint)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.btn_open_dir = QPushButton("打开目录")
        self.btn_open_dir.setObjectName("Ghost")
        self.btn_open_dir.setMinimumHeight(28)
        self.btn_open_dir.setCursor(Qt.PointingHandCursor)
        row2.addWidget(self.btn_open_dir, 1)
        self.btn_save = QPushButton("保存视图")
        self.btn_save.setObjectName("Ghost")
        self.btn_save.setMinimumHeight(28)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        row2.addWidget(self.btn_save, 1)
        layout.addLayout(row2)
        layout.addWidget(self._divider())

        # ---- meta
        self.meta_port = self._meta_row(layout, "串口")
        self.meta_flow = self._meta_row(layout, "流控")
        self.meta_version = self._meta_row(layout, "版本")
        self.meta_version.setText(__version__)

        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return rail

    def _meta_row(self, layout: QVBoxLayout, name: str) -> QLabel:
        row = QHBoxLayout()
        label = QLabel(name)
        label.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        value = QLabel("—")
        value.setStyleSheet(f"color: {theme.INK2}; font-size: 11px;")
        value.setAlignment(Qt.AlignRight)
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(value)
        layout.addLayout(row)
        return value

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

        log_card = GlassFrame(radius=18, shadow=False)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(12, 8, 12, 8)
        log_layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setObjectName("Ghost")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        toolbar.addWidget(self.btn_clear)

        self.btn_save_log = QPushButton("保存视图")
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
        self.scroll_pill.clicked.connect(self.jump_to_latest)
        self.log.verticalScrollBar().valueChanged.connect(self._on_log_scrolled)
        self.log.verticalScrollBar().rangeChanged.connect(lambda *_: self._on_log_scrolled())
        log_layout.addWidget(log_host, 1)

        self.log_hint = QLabel("选择串口并打开，接收的数据会实时出现在这里")
        self.log_hint.setAlignment(Qt.AlignCenter)
        self.log_hint.setStyleSheet(f"color: {theme.DIM}; font-size: 12px;")
        log_layout.addWidget(self.log_hint)
        layout.addWidget(log_card, 1)

        self.send_panel = SendPanel()
        layout.addWidget(self.send_panel, 0)
        return page

    # --------------------------------------------------------------- sidebar
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(346)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.cmd_block = GlassFrame(radius=16, shadow=False)
        cmd_layout = QVBoxLayout(self.cmd_block)
        cmd_layout.setContentsMargins(0, 0, 0, 0)
        self.commands_panel = CommandsPanel(self.command_store, self.notify)
        cmd_layout.addWidget(self.commands_panel)

        self.search_block = GlassFrame(radius=16, shadow=False)
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
        if cmd_open and search_open:
            self.sidebar_box.setStretchFactor(self.cmd_block, 3)
            self.sidebar_box.setStretchFactor(self.search_block, 2)
            self.cmd_block.setMaximumHeight(1 << 20)
            self.search_block.setMaximumHeight(1 << 20)
        elif cmd_open:
            self.sidebar_box.setStretchFactor(self.cmd_block, 1)
            self.sidebar_box.setStretchFactor(self.search_block, 0)
            self.cmd_block.setMaximumHeight(1 << 20)
            self.search_block.setMaximumHeight(self.search_panel.header.height() + 6)
        elif search_open:
            self.sidebar_box.setStretchFactor(self.cmd_block, 0)
            self.sidebar_box.setStretchFactor(self.search_block, 1)
            self.cmd_block.setMaximumHeight(self.commands_panel.header.height() + 6)
            self.search_block.setMaximumHeight(1 << 20)
        else:
            self.sidebar_box.setStretchFactor(self.cmd_block, 0)
            self.sidebar_box.setStretchFactor(self.search_block, 0)
            self.cmd_block.setMaximumHeight(self.commands_panel.header.height() + 6)
            self.search_block.setMaximumHeight(self.search_panel.header.height() + 6)

    def toggle_sidebar(self) -> None:
        visible = self._sidebar_widget.isHidden()
        self._sidebar_widget.setVisible(visible)
        self.settings.setValue("ui/sidebar", "1" if visible else "0")

    # ---------------------------------------------------------- status strip
    def _build_status_strip(self) -> QWidget:
        strip = QWidget()
        strip.setFixedHeight(26)
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(14)
        self.status_line = QLabel("就绪")
        self.status_line.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        layout.addWidget(self.status_line)
        layout.addStretch(1)
        self.status_trim = QLabel("")
        self.status_trim.setStyleSheet(f"color: {theme.AMBER}; font-size: 11px;")
        layout.addWidget(self.status_trim)
        self.status_dir = QLabel(f"数据目录 {paths.data_dir()}")
        self.status_dir.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        layout.addWidget(self.status_dir)
        return strip

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

        self.row_auto.toggled.connect(lambda _: self._push_opts())
        for row in (self.row_dtr, self.row_rts):
            row.toggled.connect(lambda _: self._push_opts())
        for combo in (self.flow, self.parity, self.stopbits, self.bytesize):
            combo.currentIndexChanged.connect(lambda _: self._push_opts())
        self.baud.currentTextChanged.connect(lambda _: self._push_opts())

        self.row_hex.toggled.connect(self._on_hex_mode)
        self.row_ts.toggled.connect(lambda value: setattr(self.log, "show_ts", value))
        self.row_ctrl.toggled.connect(self._on_ctrl_mode)
        self.row_echo.toggled.connect(self._save_settings)
        self.row_scroll.toggled.connect(self._on_autoscroll)

        self.btn_record.clicked.connect(self.toggle_record)
        self.btn_open_dir.clicked.connect(self.open_data_dir)
        self.btn_save.clicked.connect(self.save_view)

        self.send_panel.sendRequested.connect(self.send_payload)
        self.send_panel.fileSelected.connect(self.send_file)
        self.send_panel.abortRequested.connect(self.abort_file)

        self.commands_panel.sendRequested.connect(self.send_payload)
        self.transfer.startRequested.connect(self.start_transfer)
        self.transfer.abortRequested.connect(self.abort_transfer)

        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self._refresh_stats)
        self.stats_timer.start(500)


    # ------------------------------------------------------------- settings
    def _restore_settings(self) -> None:
        s = self.settings
        geometry = s.value("ui/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        if s.value("ui/sidebar", "1") == "0":
            self._sidebar_widget.hide()
        self.baud.setCurrentText(str(s.value("serial/baud", "115200")))
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
        self.log.show_ts = self.row_ts.isChecked()
        self.log.autoscroll = self.row_scroll.isChecked()
        self.meta_flow.setText(self.flow.currentText())
        self.relayout_sidebar()
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

    def _serial_opts(self) -> dict:
        baud_text = self.baud.currentText().strip()
        try:
            baud = int(baud_text)
        except ValueError:
            baud = 115200
        return {
            "baudrate": baud,
            "bytesize": int(self.bytesize.currentText()),
            "stopbits": float(self.stopbits.currentText()),
            "parity": self.parity.currentData(),
            "rtscts": self.flow.currentData() == "rtscts",
            "xonxoff": self.flow.currentData() == "xonxoff",
            "dtr": self.row_dtr.isChecked(),
            "rts": self.row_rts.isChecked(),
            "autoReconnect": self.row_auto.isChecked(),
        }

    def _push_opts(self) -> None:
        self._save_settings()
        self.meta_flow.setText(self.flow.currentText())
        if self._status.get("connected"):
            self.bridge.manager.update_opts(self._serial_opts())

    # ------------------------------------------------------------ connection
    def _on_ports(self, ports: list) -> None:
        current = self.port_combo.currentData()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        if not ports:
            self.port_combo.addItem("未检测到串口", "")
        for port in ports:
            detail = port.get("description") or ""
            label = port["name"]
            if detail and detail != "n/a" and port["name"] not in detail:
                label = f"{port['name']} · {detail}"
            self.port_combo.addItem(label, port["name"])
            index = self.port_combo.count() - 1
            self.port_combo.setItemData(index, port.get("hwid") or "", Qt.ToolTipRole)
        preferred = self._status.get("port") or current or self.settings.value("serial/lastPort", "")
        if preferred:
            index = self.port_combo.findData(preferred)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)
        self.port_combo.blockSignals(False)

    def toggle_connection(self) -> None:
        if self._status.get("connected") or self._status.get("reconnecting"):
            self.bridge.manager.close()
            return
        port = self.port_combo.currentData()
        if not port:
            self.notify("没有可用串口", "warn")
            return
        self.settings.setValue("serial/lastPort", port)
        self.bridge.manager.open(port, self._serial_opts())

    def _on_port_picked(self, _index: int) -> None:
        """Switching the combo while a port is open re-opens on the new one.

        A serial debugger must not require close -> select -> open: picking a
        different port is an explicit instruction to go there.
        """
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
        self.notify(f"正在切换到 {port} ...", "info")
        self.bridge.manager.open(port, self._serial_opts())

    def _on_status(self, status: dict) -> None:
        self._status = status
        chip = self.titlebar.status_chip
        if status.get("connected"):
            chip.set_state("ok", f"{status.get('port')} 已连接")
            self.btn_connect.setText("关闭串口")
            self.btn_connect.setObjectName("Ghost")
            self.meta_port.setText(status.get("port") or "—")
            self.status_line.setText(f"已连接 {status.get('port')} · {self.baud.currentText()} bps")
        elif status.get("reconnecting"):
            chip.set_state("retry", "正在重新连接…")
            self.btn_connect.setText("取消重连")
            self.btn_connect.setObjectName("Primary")
            self.status_line.setText("串口已断开，等待设备重新插入…")
        else:
            error = status.get("error")
            if error:
                chip.set_state("error", "连接失败")
                self.status_line.setText(str(error))
            else:
                chip.set_state("idle", "未连接")
                self.status_line.setText("就绪")
            self.btn_connect.setText("打开串口")
            self.btn_connect.setObjectName("Primary")
            self.meta_port.setText("—")
        self.btn_connect.style().unpolish(self.btn_connect)
        self.btn_connect.style().polish(self.btn_connect)

    def _on_stats(self, stats: dict) -> None:
        self._stats = stats
        self.titlebar.set_counters(stats.get("rx", 0), stats.get("tx", 0))

    def _refresh_stats(self) -> None:
        stats = self.bridge.manager.stats()
        self._on_stats(stats)

    # ------------------------------------------------------------------- rx
    def _on_rx(self, ts: float, data: bytes) -> None:
        # No pause buffer any more: every chunk is printed immediately, which
        # also means nothing can ever be dropped here (R-017).
        self._feed(ts, data)
        if self.console.isVisible():
            self.console.feed(data)

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

    def _print_line(self, ts: float, raw: str, direction: str, keywords) -> None:
        text = self._clean(raw)
        self.log.clear_pending()
        self.log.append_line(ts, direction, text, "rx", keywords=keywords)
        self._appended += 1
        self._check_trim()
        if self.search_panel.match(raw):
            self.search_panel.add_hit(ts, self._clean(raw).strip())

    def _feed_hex(self, ts: float, data: bytes) -> None:
        merged = self._hex_partial + data
        index = 0
        keywords = self.search_panel.keywords
        while len(merged) - index >= 16:
            row = merged[index:index + 16]
            text = self._hex_row(self._hex_offset, row)
            self.log.clear_pending()
            self.log.append_line(ts, "RX", text, "rx", keywords=keywords)
            self._appended += 1
            if self.search_panel.match(text):
                self.search_panel.add_hit(ts, text.strip())
            self._hex_offset += 16
            index += 16
        self._hex_partial = merged[index:]
        self._check_trim()
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
        if self._appended > VIEW_LINES:
            hidden = self._appended - VIEW_LINES
            self.trim_badge.show_text(f"视图已隐藏较早的 {hidden} 行", "Badge")
            self.status_trim.setText(f"视图已隐藏 {hidden} 行（建议开启记录到文件）")

    # -------------------------------------------------------------- following
    def _on_autoscroll(self, value: bool) -> None:
        self.log.autoscroll = value
        if value:
            self.log.scroll_bottom()
        self._save_settings()
        self._on_log_scrolled()

    def _on_log_scrolled(self) -> None:
        """Show the floating pill whenever the newest line is off screen."""
        if self.log.autoscroll:
            self.scroll_pill.hide_pill()
            return
        if self.log._at_bottom():
            self.scroll_pill.hide_pill()
            return
        self.scroll_pill.show_pill(self._pill_anchor())

    def _pill_anchor(self) -> QPoint:
        host = self.scroll_pill.parentWidget()
        return QPoint(host.width() - 34, host.height() - 18)

    def jump_to_latest(self) -> None:
        self.log.scroll_bottom()
        self.scroll_pill.hide_pill()

    def switch_page(self, page: str) -> None:
        pages = {"terminal": 0, "console": 1, "transfer": 2}
        self.stack.setCurrentIndex(pages.get(page, 0))
        if page == "console":
            self.console.view.setFocus()

    def _on_terminal_keys(self, data: bytes) -> None:
        if not data:
            return
        if not self._status.get("connected"):
            self.notify("请先打开串口再使用终端模式", "warn")
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
                f" border-radius: 18px; }}")
            layout = QVBoxLayout(overlay)
            layout.setContentsMargins(16, 14, 16, 14)
            layout.setSpacing(10)

            head = QHBoxLayout()
            title = QLabel("搜索结果 · 全窗口")
            title.setStyleSheet(f"color: {theme.INK}; font-size: 14px; font-weight: 650;")
            head.addWidget(title)
            head.addStretch(1)
            close = QPushButton("退出全屏")
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
        return QRect(central.left() + 10, top,
                     central.width() - 20, max(120, bottom - top))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._search_overlay is not None:
            self._search_overlay.setGeometry(self._overlay_rect())
        if self.scroll_pill.isVisible() and not self.log.autoscroll:
            self.scroll_pill.move(
                self._pill_anchor() - QPoint(self.scroll_pill.width() // 2,
                                             self.scroll_pill.height()))

    def clear_log(self) -> None:
        self.log.clear_all()
        self._appended = 0
        self._pending_text = ""
        self._hex_partial = b""
        self._hex_offset = 0
        self.trim_badge.hide()
        self.status_trim.setText("")
        self.log_hint.show()
        self.scroll_pill.hide_pill()

    def _on_hex_mode(self, enabled: bool) -> None:
        self._save_settings()
        self._clear_decoder()
        had_content = self._appended > 0
        if had_content:
            self.clear_log()
            self.log.append_line(time.time() * 1000, "··",
                                 "显示模式已切换，视图已清空（串口仍在接收）", "sys")
        self.status_line.setText("HEX 显示已开启" if enabled else "HEX 显示已关闭")

    def _on_ctrl_mode(self, enabled: bool) -> None:
        self._save_settings()
        self._clear_decoder()
        if self._pending_text:
            self.log.set_pending(self._clean(self._pending_text, pending=True))
        self.status_line.setText(
            "控制字符以 \\r \\n 形式显示" if enabled else "控制字符按实际换行显示")

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
                text = description or payload.decode("utf-8", "replace")
                if self.row_ctrl.isChecked():
                    # Render the *bytes that actually left the port* (including
                    # the appended line end), so TX is as faithful as RX.
                    from .log_view import escape_control
                    text = escape_control(payload.decode("utf-8", "replace"))
                else:
                    text = text.rstrip("\r\n")
            self.log.append_line(time.time() * 1000, "TX", text, "tx",
                                 keywords=self.search_panel.keywords)
            self._appended += 1
            self._check_trim()

    def send_file(self, path: str) -> None:
        if not self._status.get("connected"):
            self.notify("请先打开串口", "warn")
            return
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            self.notify(f"读取文件失败：{exc}", "error")
            return
        name = os.path.basename(path)
        self.send_panel.set_file_busy(True, f"发送 {name}…")
        self.notify(f"开始发送 {name}（{human_bytes(len(data))}）", "info")

        from .workers import RawFileWorker
        self._raw_worker = RawFileWorker(self.bridge.manager, data, name)
        self._raw_worker.finished_ok.connect(
            lambda: self._raw_done(True, f"{name} 发送完成"))
        self._raw_worker.failed.connect(lambda msg: self._raw_done(False, msg))
        self._raw_worker.start()

    def _raw_done(self, ok: bool, message: str) -> None:
        self.send_panel.set_file_busy(False, message)
        self.notify(message, "ok" if ok else "error")

    def abort_file(self) -> None:
        worker = getattr(self, "_raw_worker", None)
        if worker is not None:
            worker.cancel()

    # -------------------------------------------------------------- transfer
    def start_transfer(self, protocol: str, direction: str, path: str) -> None:
        if not self._status.get("connected"):
            self.notify("请先打开串口", "warn")
            return
        if self._transfer is not None and self._transfer.isRunning():
            self.notify("已有传输正在进行", "warn")
            return
        data = b""
        name = "file.bin"
        if direction == "send":
            try:
                with open(path, "rb") as handle:
                    data = handle.read()
            except OSError as exc:
                self.notify(f"读取文件失败：{exc}", "error")
                return
            name = os.path.basename(path)
        self.transfer.set_running(True)
        self.transfer.set_state(f"{protocol.upper()}MODEM {'发送' if direction == 'send' else '接收'}中")
        self.transfer.progress.setValue(0)
        self.transfer.log(f"=== {protocol.upper()}MODEM {direction} · {name} ===")
        self.log.append_line(time.time() * 1000, "··",
                             f"开始 {protocol.upper()}MODEM 传输，串口数据由协议引擎处理", "evt")
        self._transfer = TransferWorker(self.bridge.manager, protocol, direction, name, data, self)
        self._transfer.log.connect(self.transfer.log)
        self._transfer.progress.connect(self.transfer.set_progress)
        self._transfer.succeeded.connect(self._transfer_done)
        self._transfer.failed.connect(lambda msg: self._transfer_failed(msg))
        self._transfer.aborted.connect(lambda msg: self._transfer_aborted(msg))
        self._transfer.start()

    def _transfer_done(self, result: dict) -> None:
        files = result.get("files") or []
        if files:
            paths_ = "、".join(item.get("path", item.get("name", "")) for item in files)
            self.transfer.finish(True, f"接收完成，共 {human_bytes(result.get('bytes', 0))}",
                                 paths_)
            self.notify("接收完成，文件已保存到 received 目录", "ok")
        else:
            self.transfer.finish(True, f"发送完成 {human_bytes(result.get('bytes', 0))}")
            self.notify("传输完成", "ok")
        self.transfer.set_running(False)

    def _transfer_failed(self, message: str) -> None:
        self.transfer.finish(False, message)
        self.notify(f"传输失败：{message}", "error")

    def _transfer_aborted(self, message: str) -> None:
        self.transfer.finish(False, message or "已中止")
        self.notify("传输已中止", "warn")

    def abort_transfer(self) -> None:
        if self._transfer is not None:
            self._transfer.cancel()
            self.transfer.set_state("正在中止…")

    # ---------------------------------------------------------------- record
    def toggle_record(self) -> None:
        recorder = self.bridge.recorder
        if recorder.active:
            state = recorder.stop()
            self.notify(f"已停止记录（{human_bytes(state.get('bytes', 0))}）", "info")
        else:
            state = recorder.start()
            self.notify("开始记录接收数据到文件", "ok")

    def _on_record(self, state: dict) -> None:
        active = bool(state.get("active"))
        self.btn_record.setText("■  停止记录" if active else "●  开始记录到文件")
        self.btn_record.setObjectName("Danger" if active else "Ghost")
        self.btn_record.style().unpolish(self.btn_record)
        self.btn_record.style().polish(self.btn_record)
        if active:
            self.record_hint.setText(f"{state.get('path')}\n已写入 {human_bytes(state.get('bytes', 0))}")
        else:
            self.record_hint.setText("记录在后台进行，暂停打印也不会漏字节。")

    def _on_upload(self, event: dict) -> None:
        # Progress of a raw file send. Completion is reported by RawFileWorker's
        # own signals, so nothing else belongs here: an earlier version compared
        # the whole event dict against the strings "done"/"error" (never true)
        # and referenced an undefined `state` inside that dead branch.
        if event.get("state") == "running":
            self.send_panel.set_file_progress(event.get("sent", 0), event.get("total", 0),
                                              event.get("name", ""))

    def open_data_dir(self) -> None:
        path = self.bridge.recorder.state().get("path") or paths.logs_dir()
        target = os.path.dirname(path) if os.path.isfile(path) else paths.data_dir()
        try:
            if sys.platform.startswith("win"):
                os.startfile(target)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as exc:
            self.notify(f"打开目录失败：{exc}", "warn")

    def save_view(self) -> None:
        text = self.log.plain_text()
        if not text.strip():
            self.notify("当前视图为空，没有可保存的内容", "warn")
            return
        default = os.path.join(paths.data_dir(),
                               time.strftime("omnicom-view-%Y%m%d-%H%M%S.log"))
        path, _ = QFileDialog.getSaveFileName(
            self, "保存当前视图", default, "日志文件 (*.log);;文本文件 (*.txt);;所有文件 (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        except OSError as exc:
            self.notify(f"保存失败：{exc}", "error")
            return
        lines = text.count("\n") + 1
        self.notify(f"已保存 {lines} 行到 {os.path.basename(path)}", "ok")

    # ----------------------------------------------------------------- toast
    def notify(self, text: str, level: str = "info", timeout: int = 3600) -> None:
        if not text:
            return
        if level in ("warn", "error"):
            self.log.append_line(time.time() * 1000, "!!", text, "evt")
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

    # -------------------------------------------------------------- window
    def toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.titlebar.sync_max_button()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self.titlebar.sync_max_button()

    def closeEvent(self, event) -> None:
        self.settings.setValue("ui/geometry", self.saveGeometry())
        self._save_settings()
        self.settings.setValue("ui/sidebar", "0" if self._sidebar_widget.isHidden() else "1")
        if self._transfer is not None and self._transfer.isRunning():
            self._transfer.cancel()
            self._transfer.wait(2000)
        self.bridge.shutdown()
        super().closeEvent(event)
