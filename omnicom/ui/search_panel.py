"""Right-sidebar panel: Notepad++ style "find all" over the whole log.

Behaviour (R-014 / R-027):

* typing a keyword and pressing Enter searches **the whole log**, including
  lines printed before the search started;
* every hit becomes a row ``line-number  text``; double-clicking a row jumps
  the log to that block and flashes it;
* while new data arrives the result list keeps growing, so a live hunt for
  ``AT`` keeps working;
* the result pane can be blown up to the full window.

Matching modes (R-030)
----------------------
``plain``  substring            ``fuzzy``  all characters in order, any gap
``word``   whole word           ``regex``  Python regular expression
plus an independent ``Match case`` switch that applies to every mode.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from . import theme
from .widgets import Badge, Chip, Combo, FlowLayout, GlyphButton

MAX_HITS = 20000


def fuzzy_match(needle: str, haystack: str, case: bool) -> bool:
    """True when every char of ``needle`` appears in ``haystack`` in order."""
    if not case:
        needle, haystack = needle.lower(), haystack.lower()
    position = 0
    for char in needle:
        found = haystack.find(char, position)
        if found < 0:
            return False
        position = found + 1
    return True


def compile_regex(pattern: str, case: bool) -> re.Pattern[str]:
    flags = 0 if case else re.IGNORECASE
    return re.compile(pattern, flags)


class SearchPanel(QWidget):
    hitActivated = Signal(int)          # document block number
    fullscreenToggled = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.keywords: list[str] = []
        self.mode = "all"
        self.hits = 0
        self._collapsed = False
        self._fullscreen = False
        self._log = None
        self._regex_error = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(10, 9, 10, 10)
        body.setSpacing(7)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText(tr("search.placeholder"))
        self.input.returnPressed.connect(self.submit_input)
        self.input.textChanged.connect(self._on_text_changed)
        row.addWidget(self.input, 1)

        self.btn_find = GlyphButton(GlyphButton.SEARCH, self, size=theme.CTRL_H)
        self.btn_find.setObjectName("IconBtn")
        self.btn_find.setToolTip(tr("search.find"))
        self.btn_find.clicked.connect(self.submit_input)
        row.addWidget(self.btn_find)
        body.addLayout(row)

        options = QHBoxLayout()
        options.setSpacing(6)
        self.match_box = Combo(self)
        self.match_box.addItem(tr("search.match.plain"), "plain")
        self.match_box.addItem(tr("search.match.fuzzy"), "fuzzy")
        self.match_box.addItem(tr("search.match.word"), "word")
        self.match_box.addItem(tr("search.match.regex"), "regex")
        self.match_box.currentIndexChanged.connect(self._options_changed)
        options.addWidget(self.match_box, 1)

        self.mode_box = Combo(self)
        self.mode_box.addItem(tr("search.mode.all"), "all")
        self.mode_box.addItem(tr("search.mode.any"), "any")
        self.mode_box.setToolTip(tr("search.mode.tip"))
        self.mode_box.currentIndexChanged.connect(self._options_changed)
        options.addWidget(self.mode_box, 1)
        body.addLayout(options)

        flags = QHBoxLayout()
        flags.setSpacing(10)
        self.case_box = QCheckBox(tr("search.case"))
        self.case_box.toggled.connect(self._options_changed)
        flags.addWidget(self.case_box)
        self.regex_hint = QLabel("")
        self.regex_hint.setStyleSheet(f"color: {theme.DANGER}; font-size: 11px;")
        flags.addWidget(self.regex_hint, 1)
        body.addLayout(flags)

        chips_host = QWidget()
        self.chip_layout = FlowLayout(chips_host, spacing=5)
        body.addWidget(chips_host)

        self.results = QListWidget()
        self.results.setObjectName("Results")
        self.results.setFont(theme.mono_font(12))
        self.results.setUniformItemSizes(True)
        self.results.setStyleSheet(
            f"QListWidget#Results {{ background: {theme.CARD};"
            f' font-family: "{theme.resolve_mono()}"; font-size: 12px;'
            f" border: 1px solid {theme.LINE_SOFT};"
            f" border-radius: {theme.RADIUS_CARD}px; padding: 4px; }}"
            f"QListWidget#Results::item {{ padding: 3px 6px;"
            f" border-radius: {theme.RADIUS_CTRL}px; }}"
            f"QListWidget#Results::item:hover {{ background: {theme.WASH_HOVER}; }}"
            f"QListWidget#Results::item:selected {{ background: {theme.TEAL_SOFT};"
            f" color: {theme.INK}; }}")
        self.results.itemDoubleClicked.connect(self._activate)
        self.results.itemActivated.connect(self._activate)
        body.addWidget(self.results, 1)

        self.hint = QLabel(tr("search.hint"))
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
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.chevron = GlyphButton(GlyphButton.CHEVRON_DOWN, self, size=theme.ICON_MD)
        self.chevron.setToolTip(tr("search.fold"))
        self.chevron.clicked.connect(self.toggle_collapsed)
        layout.addWidget(self.chevron)

        self.title = QLabel(tr("search.title"))
        self.title.setStyleSheet("color: #121A2B; font-size: 12px; font-weight: 600;")
        layout.addWidget(self.title)
        layout.addStretch(1)

        self.count = Badge("0")
        layout.addWidget(self.count)

        self.btn_full = GlyphButton(GlyphButton.EXPAND, self, size=theme.ICON_MD)
        self.btn_full.setToolTip(tr("search.fullscreen"))
        self.btn_full.clicked.connect(self.toggle_fullscreen)
        layout.addWidget(self.btn_full)

        self.btn_clear = GlyphButton(GlyphButton.CLEAR, self, size=theme.ICON_MD)
        self.btn_clear.setToolTip(tr("search.clear"))
        self.btn_clear.clicked.connect(self.clear_results)
        layout.addWidget(self.btn_clear)

        return header

    # ------------------------------------------------------------------- links
    def attach_log(self, log) -> None:
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
        self.btn_full.setToolTip(tr("search.exitfull") if self._fullscreen
                                 else tr("search.fullscreen"))
        self.fullscreenToggled.emit(self._fullscreen)

    # ------------------------------------------------------------- query mode
    def match_mode(self) -> str:
        return self.match_box.currentData() or "plain"

    def case_sensitive(self) -> bool:
        return self.case_box.isChecked()

    def _options_changed(self, *_args) -> None:
        self.mode = self.mode_box.currentData() or "all"
        self.run_search()

    def search_selection(self, text: str) -> None:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        with QSignalBlocker(self.input), QSignalBlocker(self.match_box), QSignalBlocker(self.mode_box):
            self.clear_all()
            self.match_box.setCurrentIndex(self.match_box.findData("plain"))
            self.mode_box.setCurrentIndex(self.mode_box.findData("any"))
            self.mode = "any"
            if len(lines) > 1:
                self.keywords = list(dict.fromkeys(lines))
                self._rebuild_chips()
            elif lines:
                self.input.setText(lines[0])
        self._refresh_hint()
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
        # Once keywords exist the box is a "add another keyword" field; with no
        # keywords it behaves like incremental search so typing shows results.
        if not self.keywords:
            self.run_search()

    def add_keyword(self, raw: str) -> None:
        keyword = (raw or "").strip()
        if not keyword:
            return
        if keyword not in self.keywords:
            self.keywords.append(keyword)
            self._rebuild_chips()
        self.input.clear()
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

    def _one_matches(self, needle: str, text: str) -> bool:
        mode = self.match_mode()
        case = self.case_sensitive()
        if mode == "regex":
            try:
                return bool(compile_regex(needle, case).search(text))
            except re.error as exc:
                self._regex_error = str(exc)
                return False
        if mode == "fuzzy":
            return fuzzy_match(needle, text, case)
        if mode == "word":
            flags = 0 if case else re.IGNORECASE
            return re.search(r"\b" + re.escape(needle) + r"\b", text, flags) is not None
        if case:
            return needle in text
        return needle.lower() in text.lower()

    def match(self, text: str) -> int:
        """Return the number of distinct keywords found (0 means no hit)."""
        needles = self._needles()
        if not needles:
            return 0
        self._regex_error = ""
        found = sum(1 for needle in needles if self._one_matches(needle, text))
        if self._regex_error:
            self.regex_hint.setText(tr("search.badregex", err=self._regex_error))
        else:
            self.regex_hint.setText("")
        if self.mode == "all":
            return found if found == len(needles) else 0
        return found

    # ---------------------------------------------------------------- results
    def run_search(self) -> None:
        """Search the whole log buffer (history + live tail)."""
        self.clear_results()
        needles = self._needles()
        self._refresh_hint()
        if not needles or self._log is None:
            return
        for block, line in enumerate(self._log.line_texts()):
            if not line:
                continue
            if self.match(line):
                self._append_row(block, line, scroll=False)
        self.results.scrollToTop()
        self._refresh_full_hint()

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
        self.count.setText(f"{MAX_HITS}+" if self.hits >= MAX_HITS else str(self.hits))
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

    # ----------------------------------------------------------- fullscreen UI
    def _refresh_full_hint(self) -> None:
        if not self._fullscreen:
            return
        self.hint.setText(tr("search.hint.hits") if self.keywords
                          else tr("search.hint.emptyfull"))
        self.hint.setVisible(True)

    def show_full_hint(self) -> None:
        self._refresh_full_hint()
        if not self._fullscreen:
            return
        self.hint.setVisible(True)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self._fullscreen:
            self.set_fullscreen(False)
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------ retranslate
    def retranslate(self) -> None:
        self.title.setText(tr("search.title"))
        self.input.setPlaceholderText(tr("search.placeholder"))
        self.btn_find.setToolTip(tr("search.find"))
        self.chevron.setToolTip(tr("search.fold"))
        self.btn_full.setToolTip(tr("search.exitfull") if self._fullscreen
                                 else tr("search.fullscreen"))
        self.btn_clear.setToolTip(tr("search.clear"))
        self.mode_box.setToolTip(tr("search.mode.tip"))
        self.case_box.setText(tr("search.case"))
        for index, key in enumerate(("search.match.plain", "search.match.fuzzy",
                                     "search.match.word", "search.match.regex")):
            self.match_box.setItemText(index, tr(key))
        self.mode_box.setItemText(0, tr("search.mode.all"))
        self.mode_box.setItemText(1, tr("search.mode.any"))
        self.hint.setText(tr("search.hint"))
        self.run_search()
