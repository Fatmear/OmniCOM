"""Right-sidebar panel: Notepad++ style "find all" over the whole log.

Behaviour (R-014 / R-027):

* typing a keyword and pressing Enter searches **the whole log**, including
  lines that were printed before the search started;
* every hit becomes a row ``line-number  text``; double-clicking a row jumps
  the log to that block and flashes it;
* while new data arrives the result list keeps growing (unless the log was
  cleared), so a live hunt for ``AT`` keeps working;
* the result pane can be blown up to the full window.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .widgets import Badge, Chip, Combo, FlowLayout, GlyphButton

MAX_HITS = 20000


class SearchPanel(QWidget):
    hitActivated = Signal(int)          # document block number
    fullscreenToggled = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.keywords: list[str] = []
        self.mode = "any"
        self.hits = 0
        self._collapsed = False
        self._fullscreen = False
        self._log = None
        self._whole_word = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(10, 9, 10, 10)
        body.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText("关键字，回车查找全部（含历史）")
        self.input.returnPressed.connect(self.submit_input)
        self.input.textChanged.connect(self._on_text_changed)
        row.addWidget(self.input, 1)

        self.btn_find = GlyphButton(GlyphButton.SEARCH, self, size=theme.CTRL_H)
        self.btn_find.setObjectName("IconBtn")
        self.btn_find.setToolTip("查找全部（回车）")
        self.btn_find.clicked.connect(self.submit_input)
        row.addWidget(self.btn_find)
        body.addLayout(row)

        opts = QHBoxLayout()
        opts.setSpacing(6)
        self.mode_box = Combo(self)
        self.mode_box.addItem("包含全部", "all")
        self.mode_box.addItem("包含任一", "any")
        self.mode_box.currentIndexChanged.connect(self._mode_changed)
        self.mode_box.setToolTip("多个关键字之间的关系")
        opts.addWidget(self.mode_box, 1)

        self.opts_box = Combo(self)
        self.opts_box.addItem("普通匹配", "plain")
        self.opts_box.addItem("全词匹配", "word")
        self.opts_box.addItem("正则表达式", "regex")
        self.opts_box.currentIndexChanged.connect(self._mode_changed)
        opts.addWidget(self.opts_box, 1)
        body.addLayout(opts)

        chips_host = QWidget()
        self.chip_layout = FlowLayout(chips_host, spacing=5)
        body.addWidget(chips_host)

        self.results = QListWidget()
        self.results.setObjectName("Results")
        self.results.setFont(theme.mono_font(12))
        self.results.setAlternatingRowColors(False)
        self.results.setUniformItemSizes(True)
        self.results.setStyleSheet(
            f"QListWidget#Results {{ background: {theme.CARD};"
            f' font-family: "{theme.resolve_mono()}"; font-size: 12px;'
            f" border: 1px solid {theme.LINE_SOFT}; border-radius: 10px;"
            " padding: 4px; }"
            f"QListWidget#Results::item {{ padding: 3px 6px; border-radius: 6px; }}"
            f"QListWidget#Results::item:hover {{ background: {theme.WASH_HOVER}; }}"
            f"QListWidget#Results::item:selected {{ background: {theme.TEAL_SOFT};"
            f" color: {theme.INK}; }}")
        self.results.itemDoubleClicked.connect(self._activate)
        self.results.itemActivated.connect(self._activate)
        body.addWidget(self.results, 1)

        self.hint = QLabel("输入关键字后回车，会搜索整个日志并列出所有命中行，双击可跳到原文")
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
            f" border-bottom: 1px solid {theme.LINE_SOFT}; }}")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(6, 6, 8, 6)
        layout.setSpacing(6)

        self.chevron = GlyphButton(GlyphButton.CHEVRON_DOWN, self, size=theme.ICON_MD,
                                   object_name="IconBtn")
        self.chevron.setToolTip("折叠 / 展开")
        self.chevron.clicked.connect(self.toggle_collapsed)
        layout.addWidget(self.chevron)

        self.title = QLabel("关键字搜索")
        self.title.setStyleSheet(f"color: {theme.INK2}; font-size: 12px; font-weight: 600;")
        layout.addWidget(self.title)
        layout.addStretch(1)

        self.count = Badge("0")
        layout.addWidget(self.count)

        self.btn_full = GlyphButton(GlyphButton.EXPAND, self, size=theme.ICON_MD,
                                    object_name="IconBtn")
        self.btn_full.setToolTip("全屏查看搜索结果")
        self.btn_full.clicked.connect(self.toggle_fullscreen)
        layout.addWidget(self.btn_full)

        self.btn_clear = GlyphButton(GlyphButton.CLEAR, self, size=theme.ICON_MD,
                                     object_name="IconBtn")
        self.btn_clear.setToolTip("清空搜索结果")
        self.btn_clear.clicked.connect(self.clear_results)
        layout.addWidget(self.btn_clear)

        return header

    # ------------------------------------------------------------------- links
    def attach_log(self, log) -> None:
        """Give the panel the LogView so it can search history."""
        self._log = log

    # ------------------------------------------------------------------ layout
    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self.body.setVisible(not self._collapsed)
        self.chevron.set_glyph(
            GlyphButton.CHEVRON_RIGHT if self._collapsed else GlyphButton.CHEVRON_DOWN)
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "relayout_sidebar"):
            parent = parent.parentWidget()
        if parent is not None:
            parent.relayout_sidebar()
        else:
            self.updateGeometry()

    def toggle_fullscreen(self) -> None:
        self.set_fullscreen(not self._fullscreen)

    def set_fullscreen(self, value: bool) -> None:
        self._fullscreen = bool(value)
        self.btn_full.set_glyph(
            GlyphButton.SHRINK if self._fullscreen else GlyphButton.EXPAND)
        self.btn_full.setToolTip("退出全屏" if self._fullscreen else "全屏查看搜索结果")
        self.fullscreenToggled.emit(self._fullscreen)

    # -------------------------------------------------------------- query mode
    def _mode_changed(self, _index: int) -> None:
        self.mode = self.mode_box.currentData() or "all"
        self._whole_word = self.opts_box.currentData() == "word"
        self.run_search()

    def submit_input(self) -> None:
        raw = self.input.text().strip()
        if not raw:
            self.run_search()
            return
        if raw not in self.keywords:
            self.keywords.append(raw)
            self._rebuild_chips()
        self.input.clear()
        self._refresh_hint()
        self.run_search()

    def _on_text_changed(self, _text: str) -> None:
        # Live feedback: an unsubmitted query searches immediately, the way
        # Notepad++'s incremental find behaves.
        if self.keywords:
            return

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
        self.run_search()

    def remove_keyword(self, keyword: str) -> None:
        if keyword in self.keywords:
            self.keywords.remove(keyword)
        self._rebuild_chips()
        self._refresh_hint()
        self.run_search()

    def _rebuild_chips(self) -> None:
        while self.chip_layout.count():
            item = self.chip_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for keyword in self.keywords:
            chip = Chip(keyword)
            chip.removed.connect(self.remove_keyword)
            self.chip_layout.addWidget(chip)

    def _refresh_hint(self) -> None:
        self.hint.setVisible(not self.keywords)

    # ---------------------------------------------------------------- matching
    def _needles(self) -> list[str]:
        """Keywords plus whatever is currently typed in the box."""
        pending = self.input.text().strip()
        needles = list(self.keywords)
        if pending and pending not in needles:
            needles.append(pending)
        return needles

    def match(self, text: str) -> int:
        """Return the number of distinct keywords found (0 means no hit)."""
        needles = self._needles()
        if not needles:
            return 0
        lowered = text.lower()
        found = 0
        for keyword in needles:
            if self._whole_word:
                pattern = r"\b" + re.escape(keyword) + r"\b"
                if re.search(pattern, text, re.IGNORECASE):
                    found += 1
            elif keyword.lower() in lowered:
                found += 1
        if self.mode == "all":
            return found if found == len(needles) else 0
        return found

    # ---------------------------------------------------------------- results
    def run_search(self) -> None:
        """Search the whole log buffer (history + live tail)."""
        self.clear_results()
        needles = self._needles()
        self._refresh_hint()
        if not needles:
            return
        if self._log is None:
            return
        for block, line in enumerate(self._log.line_texts()):
            if not line:
                continue
            if self.match(line):
                self._append_row(block, line, scroll=False)
        self.results.scrollToTop()

    def add_hit(self, ts: float, text: str, block: int | None = None) -> None:
        """Append a live hit (called while new lines are printed)."""
        if self.hits >= MAX_HITS:
            return
        if block is None and self._log is not None:
            block = self._log.document().blockCount() - 1
        self._append_row(block if block is not None else -1, text)

    def _append_row(self, block: int, text: str, scroll: bool = True) -> None:
        number = block + 1 if block >= 0 else 0
        item = QListWidgetItem(f"{number:>6}  {text}")
        item.setData(Qt.UserRole, block)
        item.setToolTip(text)
        self.results.addItem(item)
        self.hits += 1
        if self.hits >= MAX_HITS:
            self.count.setText(f"{MAX_HITS}+")
        else:
            self.count.setText(str(self.hits))
        self.count.show()
        if scroll:
            self.results.scrollToBottom()

    def _activate(self, item: QListWidgetItem) -> None:
        block = item.data(Qt.UserRole)
        if isinstance(block, int) and block >= 0:
            self.hitActivated.emit(block)

    def clear_results(self) -> None:
        self.hits = 0
        self.results.clear()
        self.count.setText("0")
        self.count.hide()

    def clear_all(self) -> None:
        """Drop keywords and results (used on log clear)."""
        self.keywords.clear()
        self.input.clear()
        self._rebuild_chips()
        self.clear_results()
        self._refresh_hint()

    def show_full_hint(self) -> None:
        """Hint text used while the panel is blown up to the full window."""
        if self.keywords:
            self.hint.setText("双击任意一行即可跳到日志中的原文位置（Esc 退出全屏）")
        else:
            self.hint.setText(
                "输入关键字后回车：搜索结果会占满整个窗口，双击可跳到日志原文（Esc 退出全屏）")
        self.hint.setVisible(True)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self._fullscreen:
            self.set_fullscreen(False)
            return
        super().keyPressEvent(event)
