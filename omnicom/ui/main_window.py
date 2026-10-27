"""Main window: frameless glass shell, left rail, tabs, log, sidebar."""

from __future__ import annotations

import codecs
import os
import subprocess
import sys
import time
from typing import Optional

from PySide6.QtCore import QEvent, QPoint, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QLinearGradient, QPainter,
                           QRadialGradient)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__, paths
from ..commands import CommandStore
from ..serial_port import DEMO_PORT
from . import theme
from .commands_panel import CommandsPanel
from .log_view import LogView
from .search_panel import SearchPanel
from .send_panel import SendPanel
from .transfer_panel import TransferPanel
from .widgets import Badge, GlassFrame, SectionTitle, StatusPill, SwitchRow, Toast
from .workers import SerialBridge, TransferWorker

BAUD_RATES = ["9600", "19200", "38400", "57600", "115200", "230400", "460800",
              "921600", "1000000", "1500000", "2000000", "3000000"]
PARITIES = [("N", "None"), ("E", "Even"), ("O", "Odd"), ("M", "Mark"), ("S", "Space")]
FLOWS = [("none", "无"), ("rtscts", "硬件 RTS/CTS"), ("xonxoff", "软件 XON/XOFF")]

MAX_BACKLOG = 64 * 1024 * 1024     # bytes of pause buffer before we must surface a loss
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

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(theme.BG)))
        painter.drawRoundedRect(self.rect(), 18, 18)


class TitleBar(GlassFrame):
    """Single glass bar: brand, page tabs, connection state, counters, controls."""

    def __init__(self, window: "OmniWindow") -> None:
        super().__init__(None, radius=16, strong=True, shadow=True)
        self.window_ref = window
        self._drag: Optional[QPoint] = None
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
            "QLabel { border-radius: 13px; font-size: 21px; color: #A9F7EA;"
            " background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 rgba(94,234,212,72), stop:1 rgba(167,139,250,58));"
            " border: 1px solid rgba(255,255,255,58); }")
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
            "QFrame#TabBox { background: rgba(255,255,255,10);"
            " border: 1px solid rgba(255,255,255,20); border-radius: 14px; }")
        tab_layout = QHBoxLayout(tab_box)
        tab_layout.setContentsMargins(4, 4, 4, 4)
        tab_layout.setSpacing(2)
        for index, (key, label) in enumerate((("terminal", "终端"), ("transfer", "文件传输"))):
            button = QPushButton(label)
            button.setObjectName("TabSeg")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty("page", key)
            if index == 0:
                button.setChecked(True)
            self.tabs.addButton(button)
            tab_layout.addWidget(button)
        self.tabs.buttonClicked.connect(
            lambda button: window.stack.setCurrentIndex(
                0 if button.property("page") == "terminal" else 1))
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

        self.btn_sidebar = self._tool("▤", "显示 / 隐藏右侧栏")
        self.btn_sidebar.clicked.connect(window.toggle_sidebar)
        layout.addWidget(self.btn_sidebar)

        for text, tip, slot, close in (
                ("—", "最小化", window.showMinimized, False),
                ("❐", "最大化 / 还原", window.toggle_maximize, False),
                ("✕", "关闭", window.close, True)):
            button = self._tool(text, tip, close=close)
            button.clicked.connect(slot)
            layout.addWidget(button)

    # ------------------------------------------------------------------ parts
    @staticmethod
    def _vline() -> QFrame:
        line = QFrame()
        line.setFixedSize(1, 22)
        line.setStyleSheet("background: rgba(255,255,255,24); border: 0;")
        return line

    def _tool(self, text: str, tip: str, close: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("TopIconClose" if close else "TopIcon")
        button.setFixedSize(28, 28)
        button.setToolTip(tip)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def set_counters(self, rx: int, tx: int) -> None:
        self.counter.setText(
            f'<span style="color:{theme.DIM}">RX</span> '
            f'<span style="color:#9DF3E4">{human_size(rx)}</span>'
            f'<span style="color:{theme.DIM}"> &nbsp;·&nbsp; </span>'
            f'<span style="color:{theme.DIM}">TX</span> '
            f'<span style="color:#C4B5FD">{human_size(tx)}</span>')

    # -- window drag --------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.window_ref.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            if self.window_ref.isMaximized():
                self.window_ref.showNormal()
                self._drag = QPoint(self.window_ref.width() // 2, 30)
            self.window_ref.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
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

        self.settings = QSettings("OmniCOM", "OmniCOM")
        self.bridge = SerialBridge(demo=demo)
        self.command_store = CommandStore(command_path)
        self.demo = demo

        # rx pipeline state
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._pending_text = ""
        self._pending_ts = 0.0
        self._hex_partial = b""
        self._hex_offset = 0
        self._paused = False
        self._backlog: list[tuple[float, bytes]] = []
        self._backlog_bytes = 0
        self._lost_bytes = 0
        self._replay_queue: list[tuple[float, bytes]] = []
        self._appended = 0
        self._status = {}
        self._transfer: Optional[TransferWorker] = None
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
        self.transfer = TransferPanel(self.notify)
        self.stack.addWidget(self.transfer)
        body.addWidget(self.stack, 1)

        self.sidebar = self._build_sidebar()
        body.addWidget(self.sidebar)

        root.addWidget(self._build_status_strip())

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
        self.port_combo = QComboBox()
        self.port_combo.setMinimumHeight(32)
        row.addWidget(self.port_combo, 1)
        self.btn_refresh = QPushButton("⟳")
        self.btn_refresh.setObjectName("IconBtn")
        self.btn_refresh.setFixedSize(32, 32)
        self.btn_refresh.setToolTip("刷新串口列表")
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.btn_refresh)
        layout.addLayout(row)

        self.btn_connect = QPushButton("打开串口")
        self.btn_connect.setObjectName("Primary")
        self.btn_connect.setMinimumHeight(34)
        self.btn_connect.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.btn_connect)

        self.row_auto = SwitchRow("自动重连 / 拔插恢复", True,
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
        self.baud = QComboBox()
        self.baud.setEditable(True)
        self.baud.addItems(BAUD_RATES)
        self.baud.setCurrentText("115200")
        left_col.addWidget(self.baud)
        left_col.addWidget(self._dim("数据位"))
        self.bytesize = QComboBox()
        for value in ("8", "7", "6", "5"):
            self.bytesize.addItem(value)
        left_col.addWidget(self.bytesize)
        grid.addLayout(left_col, 1)

        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        right_col.addWidget(self._dim("校验位"))
        self.parity = QComboBox()
        for key, label in PARITIES:
            self.parity.addItem(label, key)
        right_col.addWidget(self.parity)
        right_col.addWidget(self._dim("停止位"))
        self.stopbits = QComboBox()
        for value in ("1", "1.5", "2"):
            self.stopbits.addItem(value)
        right_col.addWidget(self.stopbits)
        grid.addLayout(right_col, 1)
        layout.addLayout(grid)

        layout.addWidget(self._dim("流控"))
        self.flow = QComboBox()
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
        self.row_scroll = SwitchRow("自动滚动", True)
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
        line.setStyleSheet("background: rgba(255,255,255,12);")
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
        toolbar.setSpacing(6)
        self.btn_pause = QPushButton("⏸  暂停打印")
        self.btn_pause.setObjectName("Ghost")
        self.btn_pause.setMinimumHeight(28)
        self.btn_pause.setCursor(Qt.PointingHandCursor)
        self.btn_pause.setToolTip("暂停只影响界面打印；数据在后台继续接收，继续后会把期间的数据全部补印")
        toolbar.addWidget(self.btn_pause)

        self.btn_clear = QPushButton("清空")
        self.btn_clear.setObjectName("Ghost")
        self.btn_clear.setMinimumHeight(28)
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        toolbar.addWidget(self.btn_clear)
        toolbar.addStretch(1)

        self.pause_badge = Badge("")
        self.pause_badge.show_text("已暂停 · 后台仍在接收", "BadgeWarn")
        self.pause_badge.hide()
        toolbar.addWidget(self.pause_badge)

        self.trim_badge = Badge("")
        toolbar.addWidget(self.trim_badge)

        self.btn_bottom = QPushButton("↓")
        self.btn_bottom.setObjectName("IconBtn")
        self.btn_bottom.setFixedSize(26, 26)
        self.btn_bottom.setToolTip("滚动到底部")
        self.btn_bottom.setCursor(Qt.PointingHandCursor)
        toolbar.addWidget(self.btn_bottom)
        log_layout.addLayout(toolbar)

        self.log = LogView()
        log_layout.addWidget(self.log, 1)

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
        visible = not self._sidebar_widget.isVisible()
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

        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_clear.clicked.connect(self.clear_log)
        self.btn_bottom.clicked.connect(self.log.scroll_bottom)

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
        self.row_scroll.toggled.connect(lambda value: setattr(self.log, "autoscroll", value))

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

        self.replay_timer = QTimer(self)
        self.replay_timer.timeout.connect(self._replay_step)

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
        if self._paused:
            self._backlog.append((ts, data))
            self._backlog_bytes += len(data)
            if self._backlog_bytes > MAX_BACKLOG:
                # Surface the loss instead of hiding it (R-017).
                while self._backlog_bytes > MAX_BACKLOG and len(self._backlog) > 1:
                    old_ts, old = self._backlog.pop(0)
                    self._backlog_bytes -= len(old)
                    self._lost_bytes += len(old)
            self._update_pause_badge()
            return
        self._feed(ts, data)

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

    # ----------------------------------------------------------------- pause
    def toggle_pause(self) -> None:
        self._paused = not self._paused
        self.btn_pause.setText("▶  继续打印" if self._paused else "⏸  暂停打印")
        self.btn_pause.setObjectName("Primary" if self._paused else "Ghost")
        self.btn_pause.style().unpolish(self.btn_pause)
        self.btn_pause.style().polish(self.btn_pause)
        if self._paused:
            self.pause_badge.show_text("已暂停 · 后台仍在接收", "BadgeWarn")
        else:
            self.pause_badge.hide()
            self._start_replay()

    def _update_pause_badge(self) -> None:
        extra = f" · 超限已丢弃 {human_bytes(self._lost_bytes)}" if self._lost_bytes else ""
        self.pause_badge.show_text(
            f"已暂停 · 已缓存 {human_bytes(self._backlog_bytes)}{extra}", "BadgeWarn")
        self.status_trim.setText(
            f"暂停缓存 {human_bytes(self._backlog_bytes)}{extra}" if self._paused else "")

    def _start_replay(self) -> None:
        if not self._backlog:
            self._backlog_bytes = 0
            self._lost_bytes = 0
            self.status_trim.setText("")
            return
        self._replay_queue = self._backlog
        self._backlog = []
        self._backlog_bytes = 0
        self.status_line.setText(f"正在补印暂停期间的 {len(self._replay_queue)} 个数据块…")
        self.replay_timer.start(16)

    def _replay_step(self) -> None:
        for _ in range(48):
            if not self._replay_queue:
                self.replay_timer.stop()
                self.status_line.setText("补印完成")
                self._lost_bytes = 0
                self.status_trim.setText("")
                return
            ts, data = self._replay_queue.pop(0)
            self._feed(ts, data)

    def clear_log(self) -> None:
        self.log.clear_all()
        self._appended = 0
        self._pending_text = ""
        self._hex_partial = b""
        self._hex_offset = 0
        self.trim_badge.hide()
        self.status_trim.setText("")
        self.log_hint.show()

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
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), "warn")
            return
        if self.row_echo.isChecked():
            if self.row_hex.isChecked():
                text = "[HEX] " + " ".join(f"{byte:02x}" for byte in payload)
            else:
                text = description or payload.decode("utf-8", "replace")
                if not self.row_ctrl.isChecked():
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
        # progress of a raw file send (PortManager reports it here)
        if event.get("state") == "running":
            self.send_panel.set_file_progress(event.get("sent", 0), event.get("total", 0),
                                              event.get("name", ""))
        elif event in ("done", "error"):
            self._raw_done(state == "done", event.get("message") or "文件发送完成")

    def open_data_dir(self) -> None:
        path = self.bridge.recorder.state().get("path") or paths.logs_dir()
        target = os.path.dirname(path) if os.path.isfile(path) else paths.data_dir()
        try:
            if sys.platform.startswith("win"):
                os.startfile(target)  # noqa: S606 - open the folder for the user
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as exc:  # noqa: BLE001
            self.notify(f"打开目录失败：{exc}", "warn")

    def save_view(self) -> None:
        text = self.log.plain_text()
        if not text.strip():
            self.notify("当前视图为空", "warn")
            return
        default = os.path.join(paths.data_dir(),
                               time.strftime("omnicom-view-%Y%m%d-%H%M%S.log"))
        path, _ = QFileDialog.getSaveFileName(self, "保存视图", default, "日志文件 (*.log *.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            self.notify(f"已保存到 {path}", "ok")
        except OSError as exc:
            self.notify(f"保存失败：{exc}", "error")

    # ----------------------------------------------------------------- toast
    def notify(self, text: str, level: str = "info", timeout: int = 3200) -> None:
        if not text:
            return
        if level in ("warn", "error"):
            self.log.append_line(time.time() * 1000, "!!", text, "evt")
        toast = Toast(self, text, level, timeout)
        x = self.width() - toast.width() - 26
        y = self.height() - toast.height() - 52 - len(self.findChildren(Toast)) * 6
        toast.move(max(16, x), max(60, y))
        QTimer.singleShot(timeout, toast.deleteLater)

    # -------------------------------------------------------------- window
    def toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.settings.setValue("ui/geometry", self.saveGeometry())
        self._save_settings()
        self.settings.setValue("ui/sidebar", "1" if self._sidebar_widget.isVisible() else "0")
        if self._transfer is not None and self._transfer.isRunning():
            self._transfer.cancel()
            self._transfer.wait(2000)
        self.bridge.shutdown()
        super().closeEvent(event)
