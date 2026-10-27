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


