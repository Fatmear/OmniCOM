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
from datetime import datetime

from PySide6.QtCore import (
    QDateTime,
    QEvent,
    QSignalBlocker,
    QSize,
    Qt,
    QTime,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QPalette,
    QTextDocument,
    QTextOption,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDateTimeEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from . import theme
from .widgets import (
    Badge,
    Chip,
    Combo,
    FlowLayout,
    GlyphButton,
    TailFollow,
)

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


class ResultDelegate(QStyledItemDelegate):
    """Row delegate for the result list (R-146 / R-158 / R-175).

    Full screen wraps, so every row has its own height and the view asks for
    *all* of them - twice - every time the wrap mode or the grid changes
    (``setGridSize(QSize())`` then ``doItemsLayout()``).  Measuring a row by
    laying out a real ``QTextDocument`` costs ~0.13 ms; at 2000 hits that is
    seconds of frozen window (measured 2339.7 ms on switch-in, L-235).
    """

    def __init__(self, view: QListWidget) -> None:
        super().__init__(view)
        self.view = view
        self._font_key: tuple | None = None
        self._metrics: QFontMetrics | None = None
        self._cell = 1
        self._line_height = 1
        self._width = 0
        self._advances: dict[str, int] = {}
        self._heights: dict[str, int] = {}

    def document(self, option, index) -> QTextDocument:
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(option.font)
        text_option = QTextOption()
        text_option.setWrapMode(QTextOption.WrapMode.WrapAnywhere)
        document.setDefaultTextOption(text_option)
        document.setPlainText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
        document.setTextWidth(max(1, self.view.viewport().width() - 12))
        return document

    # ------------------------------------------------- row height (R-175)
    def reset_metrics(self) -> None:
        """Forget every measured row (font or wrap width changed)."""
        self._font_key = None
        self._metrics = None
        self._advances.clear()
        self._width = 0
        self._heights.clear()

    def _sync_metrics(self, font) -> None:
        key = (font.family(), font.pixelSize(), font.pointSizeF(), font.weight())
        if key == self._font_key:
            return
        self._font_key = key
        self._metrics = QFontMetrics(font)
        self._cell = max(1, self._metrics.horizontalAdvance("0"))
        self._line_height = max(1, self._metrics.lineSpacing())
        self._advances.clear()
        self._width = 0
        self._heights.clear()

    def _cells(self, text: str) -> int:
        """How many monospaced cells ``text`` occupies.

        The log face is Consolas (``theme.mono_font``), in which *every* glyph -
        CJK, ``←``, ``◇``, ``…`` - advances exactly one cell (measured, L-235),
        so counting characters is enough.  A font whose CJK falls back to a
        double-width face would break that, hence the per-char advance sum
        instead of a hard-coded 1: the cache makes it free on repeat rows.
        """
        if text.isascii():
            return len(text)
        metrics = self._metrics
        total = 0
        for char in text:
            advance = self._advances.get(char)
            if advance is None:
                advance = metrics.horizontalAdvance(char)
                self._advances[char] = advance
            total += advance
        return max(1, -(-total // self._cell))

    def row_height(self, option, text: str) -> int:
        self._sync_metrics(option.font)
        width = max(1, self.view.viewport().width() - 12)
        if width != self._width:
            # Wrapping changed, so every cached height is stale.
            self._width = width
            self._heights.clear()
        cached = self._heights.get(text)
        if cached is not None:
            return cached
        columns = max(1, width // self._cell)
        cells = max(1, self._cells(text))
        # ``columns`` is floored: exact for an unbroken payload, at most one row
        # too tall for prose (a line of slack is invisible, a clipped line is not).
        height = -(-cells // columns) * self._line_height + 6
        self._heights[text] = height
        return height

    def sizeHint(self, option, index) -> QSize:
        if not self.view.wordWrap():
            return super().sizeHint(option, index)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        self._sync_metrics(option.font)
        return QSize(max(1, self.view.viewport().width() - 12) + 12,
                     self.row_height(option, text))

    def paint(self, painter, option, index) -> None:
        if not self.view.wordWrap():
            super().paint(painter, option, index)
            return
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        document = self.document(styled, index)
        styled.text = ""
        styled.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem,
                                         styled, painter, styled.widget)
        painter.save()
        try:
            painter.setClipRect(option.rect)
            painter.translate(option.rect.left() + 6, option.rect.top() + 3)
            context = document.documentLayout().PaintContext()
            context.palette.setColor(QPalette.ColorRole.Text, QColor(theme.INK))
            document.documentLayout().draw(painter, context)
        finally:
            painter.restore()


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
        #: The 开始 / 结束 labels, so ``retranslate`` can relabel them.
        self._time_labels: list[QLabel] = []
        #: True while a batch of rows is being inserted (grid pinned once).
        self._batching = False
        #: Cached ``QFontMetrics`` for the row-width measurement (R-158 / L-242).
        self._metrics: QFontMetrics | None = None

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

        body.addWidget(self._build_time_box())

        chips_host = QWidget()
        self.chip_layout = FlowLayout(chips_host, spacing=5)
        body.addWidget(chips_host)

        self.results = QListWidget()
        self.results.setObjectName("Results")
        # Sidebar rows are as wide as their text (so the horizontal bar can
        # reach the tail of a long line, R-158); full screen wraps instead and
        # lets the rows follow the viewport.  Both settings are switched in
        # ``set_fullscreen`` - these mirror its sidebar branch so the panel is
        # already correct before the first ``set_fullscreen`` call.
        self.results.setWordWrap(False)
        self.results.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.results.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.results.setResizeMode(QListWidget.ResizeMode.Fixed)
        # Every un-wrapped row has exactly the grid height, so the flag's
        # promise holds; it must be on from the start, not only after a
        # ``set_fullscreen(False)`` round trip (R-146).
        self.results.setUniformItemSizes(True)
        self._row_width = 0
        self.results.setItemDelegate(ResultDelegate(self.results))
        self.apply_font()
        self.results.itemDoubleClicked.connect(self._activate)
        self.results.itemActivated.connect(self._activate)
        body.addWidget(self.results, 1)

        self._tail_follow = TailFollow(self.results, self.results.scrollToBottom)
        self.results.viewport().installEventFilter(self)

        self.hint = QLabel(tr("search.hint"))
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        body.addWidget(self.hint)

        root.addWidget(self.body, 1)

    # ------------------------------------------------------------------ header
    def _build_time_box(self) -> QWidget:
        """The 按时间过滤 block (R-154).

        A whole *date and time* is edited, never a bare ``HH:MM:SS``: the log's
        rendered stamp has no date, so ``08:00`` legitimately exists on every day
        a long-lived buffer spans.  Filtering on the bare time-of-day would
        return yesterday's 08:00 for a query about today's - exactly the trap the
        user pointed out.
        """
        box = QWidget()
        box.setObjectName("TimeFilter")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(5)

        self.time_box = QCheckBox(tr("search.time.on"))
        self.time_box.setToolTip(tr("search.time.tip"))
        self.time_box.toggled.connect(self._on_time_toggled)
        layout.addWidget(self.time_box)

        self.time_fields = QWidget()
        fields = QVBoxLayout(self.time_fields)
        fields.setContentsMargins(16, 0, 0, 0)
        fields.setSpacing(4)

        self.time_from = self._make_time_edit()
        fields.addLayout(self._labelled(tr("search.time.from"), self.time_from))
        self.time_to = self._make_time_edit()
        fields.addLayout(self._labelled(tr("search.time.to"), self.time_to))

        quick = QHBoxLayout()
        quick.setSpacing(6)
        self.btn_time_today = GlyphButton(GlyphButton.REFRESH, self,
                                          size=theme.ICON_SM)
        self.btn_time_today.setToolTip(tr("search.time.today"))
        self.btn_time_today.clicked.connect(self._time_today)
        quick.addWidget(self.btn_time_today)

        self.btn_time_all = GlyphButton(GlyphButton.EXPAND, self,
                                        size=theme.ICON_SM)
        self.btn_time_all.setToolTip(tr("search.time.buffered"))
        self.btn_time_all.clicked.connect(self._time_all)
        quick.addWidget(self.btn_time_all)
        quick.addStretch(1)
        self.time_span = QLabel("")
        self.time_span.setStyleSheet(f"color: {theme.DIM}; font-size: 11px;")
        self.time_span.setWordWrap(True)
        quick.addWidget(self.time_span, 2)
        fields.addLayout(quick)

        self.time_fields.setVisible(False)
        layout.addWidget(self.time_fields)
        return box

    def _labelled(self, text: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        label = QLabel(text)
        label.setFixedWidth(34)
        label.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        self._time_labels.append(label)
        row.addWidget(label)
        row.addWidget(widget, 1)
        return row

    def _make_time_edit(self) -> QDateTimeEdit:
        """A date+time box that looks like every other control (R-158).

        The look itself lives in ``theme.app_stylesheet()`` (a shared rule, not a
        per-widget sheet - L-194); what is set here is only behaviour:

        * ``NoButtons`` - three separate click targets stacked in a ~200 px box
          had no arrow glyph *and* ate the space the timestamp needs.
        * ``setButtonSymbols`` leaves the calendar drop-down, which opens the
          popup and is the affordance the user actually needs.
        * the format stays ``tr()``-driven (R-154 asserts it across languages).

        The height is capped to ``CTRL_H``: a ``QAbstractSpinBox`` reports a
        size hint 3 px taller than a ``QLineEdit`` under the *same* rule
        (measured 35 vs 32, with or without buttons - it is the class, not the
        QSS), and one box standing proud of its row is exactly what "太丑"
        looked like.
        """
        edit = QDateTimeEdit(QDateTime.currentDateTime())
        edit.setDisplayFormat(tr("search.time.format"))
        edit.setCalendarPopup(True)
        edit.setButtonSymbols(QDateTimeEdit.ButtonSymbols.NoButtons)
        edit.setMinimumHeight(theme.CTRL_H - 2)
        edit.setMaximumHeight(theme.CTRL_H)
        edit.setToolTip(tr("search.time.tip"))
        edit.dateTimeChanged.connect(self._on_time_edited)
        return edit

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

    @property
    def autoscroll(self) -> bool:
        return self._tail_follow.enabled

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
        """Switch between the sidebar list and the wide overlay list (R-146/158).

        The two modes have different jobs.  Full screen has room, so it wraps
        (``WrapAnywhere`` in the delegate) and needs no horizontal bar.  The
        sidebar is a narrow column in which a whole log line is 2500 px long, so
        the bar is the only way to read the tail of a match - the user asked for
        it explicitly (R-158).  Eliding stays on in the sidebar: the bar reveals
        the rest, and an elided row that can also be scrolled is what every file
        manager does.
        """
        self._fullscreen = bool(value)
        with self._tail_follow.updating():
            # ``uniformItemSizes`` is not what makes the bar work - with it on
            # and no grid, Qt clamps every row to the viewport width and the
            # horizontal range collapses to 0 (measured, L-214).  What keeps the
            # rows wide is ``setGridSize`` below; uniform sizes stay on in the
            # sidebar because every un-wrapped row has the same height, which is
            # exactly what the flag promises.  Full screen wraps, so heights
            # differ and the flag must go off.
            self.results.setUniformItemSizes(not self._fullscreen)
            self.results.setWordWrap(self._fullscreen)
            self.results.setTextElideMode(Qt.TextElideMode.ElideNone if self._fullscreen
                                         else Qt.TextElideMode.ElideRight)
            self.results.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff if self._fullscreen
                else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            if self._fullscreen:
                self.results.setGridSize(QSize())
            else:
                self._apply_grid()
            self.results.doItemsLayout()
        self.btn_full.set_glyph(
            GlyphButton.SHRINK if self._fullscreen else GlyphButton.EXPAND)
        self.btn_full.setToolTip(tr("search.exitfull") if self._fullscreen
                                 else tr("search.fullscreen"))
        self.fullscreenToggled.emit(self._fullscreen)

    # ------------------------------------------------------- row width (R-158)
    def _wanted_width(self, text: str) -> int:
        """Pixel width a sidebar row needs to show ``text`` without wrapping."""
        # ``QFontMetrics`` is built once, not once per row: constructing it 2000
        # times measured 28.5 ms of a 100 ms toggle (L-242).
        if self._metrics is None:
            self._metrics = QFontMetrics(self.results.font())
        # The sheet's item padding (3px 6px) plus slack for the scroll thumb
        # sitting over the final glyphs.
        return self._metrics.horizontalAdvance(text) + 24

    def _apply_grid(self) -> None:
        """Pin the sidebar rows to ``self._row_width`` (cheap: one size)."""
        if self._fullscreen or not self.results.count():
            return
        height = self.results.sizeHintForRow(0)
        if height <= 0:
            height = QFontMetrics(self.results.font()).height() + 6
        width = max(self._row_width, self.results.viewport().width())
        if self.results.gridSize() != QSize(width, height):
            self.results.setGridSize(QSize(width, height))

    def _grow_rows(self, text: str) -> None:
        """Widen every sidebar row if ``text`` needs more room (incremental).

        While a batch insertion is running (``self._batching``) only the wanted
        width is recorded; ``_apply_grid()`` runs once when the batch ends.
        Re-pinning the grid per row relaid the whole list out dozens of times a
        few milliseconds apart, which the eye sees as the list "jumping" while
        the results fill in (R-177 / L-239).
        """
        if self._fullscreen:
            return
        wanted = self._wanted_width(text)
        if wanted <= self._row_width:
            return
        self._row_width = wanted
        if not self._batching:
            self._apply_grid()

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
        self._tail_follow.set_enabled(False, jump=False)
        needles = self._needles()
        self._refresh_hint()
        if not needles or self._log is None:
            self.clear_results()
            return
        self.regex_hint.setText("")
        bounds = self.time_bounds()
        if self.time_inverted():
            # Inverted range: say so instead of silently returning nothing.
            self.clear_results()
            self.regex_hint.setText(tr("search.time.invert"))
            self._refresh_full_hint()
            return
        stamps = self._log.line_stamps()
        keep: list[tuple[int, str]] = []
        for block, line in enumerate(self._log.line_texts()):
            if not line:
                continue
            if not self.match(line):
                continue
            if bounds is not None and not self._in_range(stamps, block, bounds):
                continue
            keep.append((block, line))
        self._show_hits(keep)
        if bounds is not None and not keep:
            self.regex_hint.setText(tr("search.time.none"))
        self.results.scrollToTop()
        self._refresh_full_hint()

    def _show_hits(self, keep: list[tuple[int, str]]) -> None:
        """Put exactly ``keep`` in the result list, rebuilding only if it differs.

        Every caller of :meth:`run_search` re-walks the buffer, but the *rows*
        usually do not change: toggling 按时间过滤 seeds the range from the buffer
        itself, i.e. the filter keeps every hit it already showed.  Clearing and
        re-creating 2000 identical rows cost 100-125 ms (clear 4.6 + ``match``
        13.1 + ``_append_row`` 76.2), which the user felt as a stutter on each
        toggle **once the list held data** - with an empty list the same click
        was free (measured, L-242).  Comparing first makes the no-change case
        cost nothing beyond the scan, and the real rebuild is cheaper too.
        """
        stale = self.results.count() != len(keep)
        if not stale:
            for row, (block, line) in enumerate(keep):
                item = self.results.item(row)
                # ``toolTip`` already carries the raw line, so the comparison
                # needs no extra per-row bookkeeping - and it catches the one
                # case where the block number survives but the text does not
                # (the pending line, rewritten in place).
                if item.data(Qt.UserRole) != block or item.toolTip() != line:
                    stale = True
                    break
        if not stale:
            return
        self.clear_results()
        self._batching = True
        follow = self._tail_follow
        follow.begin_update()
        try:
            for block, line in keep:
                self._append_row(block, line)
        finally:
            self._batching = False
            follow.end_update()
            # One grid pin for the whole batch, and only if a row widened it.
            self._apply_grid()
            self._refresh_count()

    # ------------------------------------------------------------ time filter
    def time_filter_on(self) -> bool:
        return self.time_box.isChecked()

    def time_bounds(self) -> tuple[float, float] | None:
        """The active range in epoch milliseconds, or ``None`` when the filter
        is off.  Callers must check :meth:`time_inverted` first: an inverted
        range has no meaningful bounds, and returning "no results" for it would
        look like an empty log instead of a mistake the user can fix."""
        if not self.time_box.isChecked():
            return None
        low = self._ms(self.time_from.dateTime())
        high = self._ms(self.time_to.dateTime())
        if low > high:
            return None
        return (low, high)

    def time_inverted(self) -> bool:
        """True when the filter is on and 开始 is later than 结束."""
        if not self.time_box.isChecked():
            return False
        return self._ms(self.time_from.dateTime()) > self._ms(self.time_to.dateTime())

    @staticmethod
    def _ms(moment: QDateTime) -> float:
        return float(moment.toMSecsSinceEpoch())

    def _in_range(self, stamps: list[float | None], block: int,
                  bounds: tuple[float, float]) -> bool:
        if block < 0 or block >= len(stamps):
            return False
        value = stamps[block]
        # An unstamped line (the pending line) has no time, so it cannot be
        # proven to fall inside the range and is excluded - never assumed "now".
        if value is None:
            return False
        return bounds[0] <= value <= bounds[1]

    def _on_time_toggled(self, enabled: bool) -> None:
        """Show/hide the 按时间过滤 fields in **one** layout pass (R-177).

        Order matters.  Showing the block first let it be laid out while
        ``time_span`` was still empty (96 px) and then filled the span, which
        relaid the block out 6 px taller (102 px) - two paints at two heights in
        consecutive event loops, i.e. exactly the flicker the user reported
        (measured, L-239).  The range and the span text are therefore prepared
        *before* ``setVisible``, so the first layout is already the final one.
        """
        if enabled:
            self._seed_time_from_log()
        self._refresh_time_span()
        self.time_fields.setVisible(enabled)
        self.regex_hint.setText("")
        self.run_search()

    def _on_time_edited(self, *_args) -> None:
        if not self.time_box.isChecked():
            return
        self.regex_hint.setText("")
        self.run_search()

    def _seed_time_from_log(self) -> None:
        """Open the range at the buffer's own first/last stamp.

        Seeding with "now minus an hour" would silently exclude everything the
        device sent before the window was opened, which is the opposite of what
        someone filtering *history* wants.
        """
        if self._log is None or not hasattr(self._log, "time_span"):
            return
        first, last = self._log.time_span()
        with QSignalBlocker(self.time_from), QSignalBlocker(self.time_to):
            if first is not None:
                self.time_from.setDateTime(
                    QDateTime.fromMSecsSinceEpoch(int(first)))
            if last is not None and last >= (first or last):
                self.time_to.setDateTime(QDateTime.fromMSecsSinceEpoch(int(last)))

    def _time_today(self) -> None:
        today = QDateTime.currentDateTime()
        today.setTime(QTime(0, 0, 0, 0))
        with QSignalBlocker(self.time_from):
            self.time_from.setDateTime(today)
        self._on_time_edited()

    def _time_all(self) -> None:
        self._seed_time_from_log()
        self._on_time_edited()

    def _refresh_time_span(self) -> None:
        """Show the range actually held in the buffer, so an empty result is
        explainable (the buffer is capped and drops its oldest lines)."""
        if not self.time_box.isChecked() or self._log is None:
            self.time_span.setText("")
            return
        if not hasattr(self._log, "time_span"):
            return
        first, last = self._log.time_span()
        if first is None or last is None:
            self.time_span.setText("")
            return
        self.time_span.setText(tr(
            "search.time.span",
            start=datetime.fromtimestamp(first / 1000.0).strftime("%m-%d %H:%M:%S"),
            end=datetime.fromtimestamp(last / 1000.0).strftime("%m-%d %H:%M:%S")))

    def add_hit(self, ts: float, text: str, block: int | None = None) -> None:
        """Append a live hit (called while new lines are printed)."""
        if self.hits >= MAX_HITS:
            return
        bounds = self.time_bounds()
        if self.time_inverted():
            return
        if bounds is not None and not (bounds[0] <= float(ts) <= bounds[1]):
            # A live line outside the chosen range must not appear, otherwise the
            # list shows hits a re-run of the same search would not return.
            return
        if block is None and self._log is not None:
            block = self._log.document().blockCount() - 1
        self._append_row(block if block is not None else -1, text)

    def _append_row(self, block: int, text: str) -> None:
        number = block + 1 if block >= 0 else 0
        row = f"{number:>6}  {text}"
        item = QListWidgetItem(row)
        item.setData(Qt.UserRole, block)
        item.setToolTip(text)
        if self._batching:
            # The caller already opened one ``updating()`` block for the whole
            # batch and has the tail-follow switched off, so a per-row guard is
            # pure overhead - 2000 context managers cost a measurable slice of a
            # full rebuild (L-242).
            self.results.addItem(item)
            self._grow_rows(row)
        else:
            with self._tail_follow.updating():
                self.results.addItem(item)
                # Rows are one shared width (setGridSize), so a longer line
                # widens the whole list instead of being clipped (R-158).
                self._grow_rows(row)
        self.hits += 1
        # One label update per batch, not per row: 2000 identical ``setText``
        # calls were part of the toggle stutter (L-242).
        if not self._batching:
            self._refresh_count()

    def _refresh_count(self) -> None:
        """Show the hit count (``20000+`` once the cap is reached)."""
        self.count.setText(f"{MAX_HITS}+" if self.hits >= MAX_HITS else str(self.hits))
        self.count.setVisible(self.hits > 0)

    # ------------------------------------------------------- back to newest
    def _results_at_bottom(self) -> bool:
        return self._tail_follow.at_bottom()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.results.viewport() and event.type() == QEvent.Resize:
            if self._fullscreen:
                with self._tail_follow.updating():
                    self.results.doItemsLayout()
            else:
                # Keep the rows at least as wide as the viewport, otherwise
                # a widened sidebar leaves a gap and the bar stays enabled.
                with self._tail_follow.updating():
                    self._apply_grid()
        return super().eventFilter(watched, event)

    def _activate(self, item: QListWidgetItem) -> None:
        block = item.data(Qt.UserRole)
        if isinstance(block, int) and block >= 0:
            self.hitActivated.emit(block)

    def clear_results(self) -> None:
        self.hits = 0
        with self._tail_follow.updating():
            self.results.clear()
        # The widest row is gone, so the pinned width must go with it (R-158).
        self._row_width = 0
        if not self._fullscreen:
            self.results.setGridSize(QSize())
        self._refresh_count()

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

    def apply_font(self) -> None:
        """Follow the log font: one notch smaller than the log itself (R-096)."""
        size = max(8, theme.log_px() - 1)
        self.results.setFont(theme.mono_font(size))
        self.results.setStyleSheet(
            f"QListWidget#Results {{ background: {theme.CARD};"
            f' font-family: "{theme.resolve_mono()}"; font-size: {size}px;'
            f" border: 1px solid {theme.LINE_SOFT};"
            f" border-radius: {theme.RADIUS_CARD}px; padding: 4px; }}"
            f"QListWidget#Results::item {{ padding: 3px 6px;"
            f" border-radius: {theme.RADIUS_CTRL}px; }}"
            f"QListWidget#Results::item:hover {{ background: {theme.WASH_HOVER}; }}"
            f"QListWidget#Results::item:selected {{ background: {theme.TEAL_SOFT};"
            f" color: {theme.INK}; }}")
        # A different font means different widths: measure again from scratch.
        # One pass over the rows, then a single ``setGridSize`` - calling it per
        # row would relayout the list thousands of times (R-158).
        # ``apply_font`` also runs from ``__init__`` before ``_tail_follow``
        # exists, hence the ``getattr`` instead of a ``with`` block here.
        follow = getattr(self, "_tail_follow", None)
        if follow is not None:
            follow.begin_update()
        try:
            # The cached metrics belong to the *old* font (L-242).
            self._metrics = QFontMetrics(self.results.font())
            metrics = self._metrics
            widest = 0
            for row in range(self.results.count()):
                widest = max(widest, metrics.horizontalAdvance(
                    self.results.item(row).text()) + 24)
            self._row_width = widest
            self._apply_grid()
            if self._fullscreen:
                self.results.doItemsLayout()
        finally:
            if follow is not None:
                follow.end_update()

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
        self.time_box.setText(tr("search.time.on"))
        self.time_box.setToolTip(tr("search.time.tip"))
        self.time_from.setDisplayFormat(tr("search.time.format"))
        self.time_to.setDisplayFormat(tr("search.time.format"))
        self.time_from.setToolTip(tr("search.time.tip"))
        self.time_to.setToolTip(tr("search.time.tip"))
        self.btn_time_today.setToolTip(tr("search.time.today"))
        self.btn_time_all.setToolTip(tr("search.time.buffered"))
        for label, text in zip(self._time_labels,
                               (tr("search.time.from"), tr("search.time.to")),
                               strict=False):
            label.setText(text)
        self._refresh_time_span()
        self.hint.setText(tr("search.hint"))
        self.run_search()
