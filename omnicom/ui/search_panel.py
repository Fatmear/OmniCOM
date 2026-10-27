"""Right-sidebar panel: multi-keyword search with a live hit feed."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from . import theme
from .log_view import LogView
from .widgets import Badge, Chip, FlowLayout

MAX_HITS = 4000


class SearchPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.keywords: list[str] = []
        self.mode = "any"
        self.hits = 0
        self._collapsed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(9, 8, 10, 10)
        body.setSpacing(7)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入关键字后回车添加，可加多个")
        self.input.returnPressed.connect(lambda: self.add_keyword(self.input.text()))
        row.addWidget(self.input, 1)
        self.mode_box = QComboBox()
        self.mode_box.addItem("任一", "any")
        self.mode_box.addItem("全部", "all")
        self.mode_box.setFixedWidth(68)
        self.mode_box.currentIndexChanged.connect(self._mode_changed)
        row.addWidget(self.mode_box)
        body.addLayout(row)

        chips_host = QWidget()
        self.chip_layout = FlowLayout(chips_host, spacing=5)
        body.addWidget(chips_host)

        self.hits_view = LogView(show_dir=False, pixel_size=12)
        self.hits_view.show_ts = True
        self.hits_view.setStyleSheet(
            "QPlainTextEdit { background: transparent; border: 0; padding: 0;"
            " selection-background-color: rgba(167,139,250,70); }")
        body.addWidget(self.hits_view, 1)

        self.hint = QLabel("添加关键字后，命中的行会实时汇总到这里")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        body.addWidget(self.hint)

        root.addWidget(self.body, 1)

    # ------------------------------------------------------------------ header
    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("SideHeader")
        header.setStyleSheet(
            "QWidget#SideHeader { background: transparent; border: 0;"
            " border-bottom: 1px solid rgba(255,255,255,14); }")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(5, 6, 8, 6)
        layout.setSpacing(6)

        self.chevron = QPushButton("▾")
        self.chevron.setFixedSize(22, 22)
        self.chevron.setCursor(Qt.PointingHandCursor)
        self.chevron.clicked.connect(self.toggle_collapsed)
        layout.addWidget(self.chevron)

        title = QLabel("关键字搜索")
        title.setStyleSheet(f"color: {theme.INK2}; font-size: 12px; font-weight: 600;")
        layout.addWidget(title)
        layout.addStretch(1)

        self.count = Badge("0")
        layout.addWidget(self.count)

        clear = QPushButton("⌫")
        clear.setFixedSize(22, 22)
        clear.setToolTip("清空搜索结果")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self.clear_results)
        layout.addWidget(clear)

        return header

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self.body.setVisible(not self._collapsed)
        self.chevron.setText("▸" if self._collapsed else "▾")
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "relayout_sidebar"):
            parent = parent.parentWidget()
        if parent is not None:
            parent.relayout_sidebar()
        else:
            self.updateGeometry()

    # ----------------------------------------------------------------- chips
    def _mode_changed(self, index: int) -> None:
        self.mode = self.mode_box.itemData(index) or "any"

    def add_keyword(self, raw: str) -> None:
        keyword = (raw or "").strip()
        if not keyword:
            return
        if keyword in self.keywords:
            self.input.clear()
            return
        self.keywords.append(keyword)
        self.input.clear()
        self._rebuild_chips()
        self._refresh_hint()

    def remove_keyword(self, keyword: str) -> None:
        if keyword in self.keywords:
            self.keywords.remove(keyword)
        self._rebuild_chips()
        self._refresh_hint()

    def _rebuild_chips(self) -> None:
        while self.chip_layout.count():
            item = self.chip_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for keyword in self.keywords:
            chip = Chip(keyword)
            chip.removed.connect(self.remove_keyword)
            self.chip_layout.addWidget(chip)

    def _refresh_hint(self) -> None:
        self.hint.setVisible(not self.keywords)

    # ---------------------------------------------------------------- matches
    def match(self, text: str) -> int:
        """Return the number of distinct keywords found (0 means no hit)."""
        if not self.keywords:
            return 0
        lowered = text.lower()
        found = sum(1 for keyword in self.keywords if keyword.lower() in lowered)
        if self.mode == "all":
            return found if found == len(self.keywords) else 0
        return found

    def add_hit(self, ts: float, text: str) -> None:
        if self.hits >= MAX_HITS:
            return
        self.hits += 1
        self.hits_view.append_line(ts, "··", text, "rx", keywords=self.keywords, lite=True)
        if self.hits == MAX_HITS:
            self.count.setText(f"{MAX_HITS}+")
            self.count.show()
            return
        self.count.setText(str(self.hits))
        self.count.show()

    def clear_results(self) -> None:
        self.hits = 0
        self.hits_view.clear_all()
        self.count.hide()
