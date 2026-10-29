"""Preferences, documentation and about dialogs."""

from __future__ import annotations

import platform
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, docs, i18n, paths
from . import theme
from .log_view import DEFAULT_BUFFER_MB, MAX_BUFFER_MB, MIN_BUFFER_MB
from .send_panel import HISTORY_DEFAULT, HISTORY_MAX, HISTORY_MIN
from .widgets import Button as QPushButton
from .widgets import Combo, GlyphButton


def _dialog_shell(parent, title: str, width: int, height: int) -> tuple[QDialog, QVBoxLayout]:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    dialog.resize(width, height)
    dialog.setMinimumSize(min(width, 420), min(height, 320))
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(12)
    return dialog, layout


class PreferencesDialog(QDialog):
    """Language, capture folder, accent colour and wallpaper.  Applies at once."""

    languageChanged = Signal(str)
    logDirChanged = Signal(str)
    accentChanged = Signal(str)
    wallpaperChanged = Signal(str)
    bufferChanged = Signal(int)
    uiFontChanged = Signal(str, int)     # family ("" = built-in stack), pixel size
    logFontChanged = Signal(str, int)
    historyLimitChanged = Signal(int)    # how many typed commands ↑ remembers
    historyCleared = Signal()

    def __init__(self, parent=None, current_language: str = "zh",
                 log_dir: str = "", accent: str = "",
                 wallpaper: str = "", buffer_mb: int = DEFAULT_BUFFER_MB,
                 ui_family: str = "", ui_px: int = 13,
                 log_family: str = "", log_px: int = 13,
                 history_limit: int = 50, history_count: int = 0) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("pref.title"))
        self.setModal(True)
        self.resize(560, 566)
        self.setMinimumWidth(460)
        self._log_dir = log_dir or paths.logs_dir()
        self._history_entries = int(history_count or 0)
        self._wallpaper = wallpaper or ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.heading = QLabel(i18n.tr("pref.title"))
        self.heading.setStyleSheet("font-size: 16px; font-weight: 650;")
        layout.addWidget(self.heading)

        # ---- language
        self.language_section = self._section(layout, "pref.language")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.language_box = Combo()
        for lang in i18n.LANGS:
            self.language_box.addItem(i18n.LANG_NAMES[lang], lang)
        self.language_box.setCurrentIndex(
            max(0, self.language_box.findData(current_language)))
        self.language_box.currentIndexChanged.connect(self._on_language)
        row.addWidget(self.language_box, 1)
        layout.addLayout(row)
        self.language_hint = self._hint(layout, "pref.language.hint")

        # ---- capture folder
        self.logdir_section = self._section(layout, "pref.logdir")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.logdir_value = QLabel(self._log_dir)
        self.logdir_value.setWordWrap(True)
        self.logdir_value.setStyleSheet(f"color: {theme.INK2}; font-size: 11.5px;")
        row.addWidget(self.logdir_value, 1)
        self.btn_logdir = QPushButton(i18n.tr("pref.logdir.pick"))
        self.btn_logdir.setObjectName("Ghost")
        self.btn_logdir.clicked.connect(self._pick_log_dir)
        row.addWidget(self.btn_logdir)
        self.btn_logdir_reset = QPushButton(i18n.tr("pref.logdir.default"))
        self.btn_logdir_reset.setObjectName("Ghost")
        self.btn_logdir_reset.clicked.connect(self._reset_log_dir)
        row.addWidget(self.btn_logdir_reset)
        layout.addLayout(row)
        self.logdir_hint = self._hint(layout, "pref.logdir.hint")

        # ---- accent colour
        self.accent_section = self._section(layout, "pref.accent")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.accent_box = Combo()
        for key, zh, en in theme.accent_choices():
            self.accent_box.addItem(zh if i18n.language() == "zh" else en, key)
        chosen = accent or theme.accent()
        self.accent_box.setCurrentIndex(max(0, self.accent_box.findData(chosen)))
        self.accent_box.currentIndexChanged.connect(self._on_accent)
        row.addWidget(self.accent_box, 1)
        self.accent_swatch = QLabel("")
        self.accent_swatch.setFixedSize(46, 22)
        row.addWidget(self.accent_swatch)
        layout.addLayout(row)
        self._paint_swatch(chosen)
        self.accent_hint = self._hint(layout, "pref.accent.hint")

        # ---- wallpaper
        self.wallpaper_section = self._section(layout, "pref.wallpaper")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.wallpaper_value = QLabel("")
        self.wallpaper_value.setWordWrap(True)
        self.wallpaper_value.setStyleSheet(f"color: {theme.INK2}; font-size: 11.5px;")
        row.addWidget(self.wallpaper_value, 1)
        self.btn_wallpaper = QPushButton(i18n.tr("pref.wallpaper.pick"))
        self.btn_wallpaper.setObjectName("Ghost")
        self.btn_wallpaper.clicked.connect(self._pick_wallpaper)
        row.addWidget(self.btn_wallpaper)
        self.btn_wallpaper_clear = QPushButton(i18n.tr("pref.wallpaper.clear"))
        self.btn_wallpaper_clear.setObjectName("Ghost")
        self.btn_wallpaper_clear.clicked.connect(self._clear_wallpaper)
        row.addWidget(self.btn_wallpaper_clear)
        layout.addLayout(row)
        self.wallpaper_hint = self._hint(layout, "pref.wallpaper.hint")
        self._show_wallpaper(self._wallpaper)

        # ---- log buffer size (R-081)
        self.buffer_section = self._section(layout, "pref.buffer")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.buffer_value = QSpinBox()
        self.buffer_value.setRange(MIN_BUFFER_MB, MAX_BUFFER_MB)
        self.buffer_value.setSingleStep(10)
        self.buffer_value.setSuffix(" MB")
        self.buffer_value.setValue(max(MIN_BUFFER_MB, min(MAX_BUFFER_MB,
                                                          int(buffer_mb or 0)
                                                          or DEFAULT_BUFFER_MB)))
        self.buffer_value.setToolTip(i18n.tr("pref.buffer.tip"))
        self.buffer_value.valueChanged.connect(self._on_buffer)
        row.addWidget(self.buffer_value)
        row.addStretch(1)
        layout.addLayout(row)
        self.buffer_hint = self._hint(layout, "pref.buffer.hint")

        # ---- fonts (R-096/R-098).  Two independent pairs: the interface and the
        # log are read for different reasons, so a comfortable UI with a loud log
        # (or the reverse) has to be possible.
        self.ui_font_section = self._section(layout, "pref.font.ui")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.ui_family_box = self._family_combo(ui_family)
        row.addWidget(self.ui_family_box, 1)
        self.ui_size = QSpinBox()
        self.ui_size.setRange(theme.UI_FONT_MIN, theme.UI_FONT_MAX)
        self.ui_size.setSuffix(" px")
        self.ui_size.setValue(max(theme.UI_FONT_MIN,
                                  min(theme.UI_FONT_MAX, int(ui_px or 13))))
        row.addWidget(self.ui_size)
        layout.addLayout(row)
        self.ui_font_hint = self._hint(layout, "pref.font.ui.hint")

        self.log_font_section = self._section(layout, "pref.font.log")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.log_family_box = self._family_combo(log_family)
        row.addWidget(self.log_family_box, 1)
        self.log_size = QSpinBox()
        self.log_size.setRange(theme.LOG_FONT_MIN, theme.LOG_FONT_MAX)
        self.log_size.setSuffix(" px")
        self.log_size.setValue(max(theme.LOG_FONT_MIN,
                                   min(theme.LOG_FONT_MAX, int(log_px or 13))))
        row.addWidget(self.log_size)
        layout.addLayout(row)
        self.log_font_hint = self._hint(layout, "pref.font.log.hint")

        # ---- send history (R-116).  Bounded on purpose: an unbounded list grows
        # the settings file forever, and ↑ stops being a shortcut.  The count is
        # shown so 清除历史记录 has something to report.
        self.history_section = self._section(layout, "pref.history")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.history_value = QSpinBox()
        self.history_value.setRange(HISTORY_MIN, HISTORY_MAX)
        self.history_value.setSingleStep(5)
        self.history_value.setSuffix(i18n.tr("pref.history.unit"))
        self.history_value.setValue(
            max(HISTORY_MIN, min(HISTORY_MAX, int(history_limit or HISTORY_DEFAULT))))
        self.history_value.setToolTip(i18n.tr("pref.history.tip"))
        self.history_value.valueChanged.connect(self._on_history_limit)
        row.addWidget(self.history_value)
        self.history_count = QLabel(
            i18n.tr("pref.history.count", count=int(history_count or 0)))
        self.history_count.setStyleSheet(f"color: {theme.INK2}; font-size: 11.5px;")
        row.addWidget(self.history_count)
        row.addStretch(1)
        self.btn_clear_history = QPushButton(i18n.tr("pref.history.clear"))
        self.btn_clear_history.setObjectName("Ghost")
        self.btn_clear_history.setCursor(Qt.PointingHandCursor)
        self.btn_clear_history.setEnabled(bool(history_count))
        self.btn_clear_history.clicked.connect(self._on_clear_history)
        row.addWidget(self.btn_clear_history)
        layout.addLayout(row)
        self.history_hint = self._hint(layout, "pref.history.hint")

        self.ui_family_box.currentIndexChanged.connect(self._on_ui_font)
        self.ui_size.valueChanged.connect(self._on_ui_font)
        self.log_family_box.currentIndexChanged.connect(self._on_log_font)
        self.log_size.valueChanged.connect(self._on_log_font)

        layout.addStretch(1)
        # The window frame already offers a close button (R-044).

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _section(layout: QVBoxLayout, key: str) -> QLabel:
        label = QLabel(i18n.tr(key))
        label.setStyleSheet(
            f"color: {theme.DIM}; font-size: 11px; font-weight: 600; letter-spacing: 1px;")
        layout.addWidget(label)
        return label

    @staticmethod
    def _hint(layout: QVBoxLayout, key: str) -> QLabel:
        label = QLabel(i18n.tr(key))
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        layout.addWidget(label)
        return label

    @staticmethod
    def _family_combo(current: str) -> Combo:
        """Family picker: first entry follows the built-in stack, then the system."""
        from PySide6.QtGui import QFontDatabase

        box = Combo()
        box.addItem(i18n.tr("pref.font.default"), "")
        families = sorted(set(QFontDatabase.families()), key=str.lower)
        for family in families:
            box.addItem(family, family)
        index = box.findData((current or "").strip())
        box.setCurrentIndex(index if index >= 0 else 0)
        return box

    def _on_history_limit(self, value: int = 0) -> None:
        self.historyLimitChanged.emit(int(value))

    def _on_clear_history(self) -> None:
        self.history_count.setText(i18n.tr("pref.history.count", count=0))
        self.btn_clear_history.setEnabled(False)
        self.historyCleared.emit()

    def _on_ui_font(self, _index: int = 0) -> None:
        self.uiFontChanged.emit(self.ui_family_box.currentData() or "",
                                int(self.ui_size.value()))

    def _on_log_font(self, _index: int = 0) -> None:
        self.logFontChanged.emit(self.log_family_box.currentData() or "",
                                 int(self.log_size.value()))

    def _paint_swatch(self, key: str) -> None:
        ink = theme.ACCENTS.get(key, theme.ACCENTS[theme.DEFAULT_ACCENT])[2]
        self.accent_swatch.setStyleSheet(
            f"background: {ink}; border-radius: {theme.RADIUS_CTRL}px;")

    def _show_wallpaper(self, path: str) -> None:
        self._wallpaper = path or ""
        self.wallpaper_value.setText(self._wallpaper or i18n.tr("pref.wallpaper.none"))

    # -- slots ------------------------------------------------------------
    def _on_language(self, _index: int) -> None:
        lang = self.language_box.currentData() or "zh"
        self.languageChanged.emit(lang)
        self.render()

    def _pick_log_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, i18n.tr("pref.logdir"),
                                                  self._log_dir)
        if not chosen:
            return
        self._log_dir = chosen
        self.logdir_value.setText(chosen)
        self.logDirChanged.emit(chosen)

    def _reset_log_dir(self) -> None:
        self._log_dir = ""
        self.logdir_value.setText(paths.default_logs_dir())
        self.logDirChanged.emit("")

    def _on_buffer(self, value: int) -> None:
        self.bufferChanged.emit(int(value))

    def _on_accent(self, _index: int) -> None:
        key = self.accent_box.currentData() or theme.DEFAULT_ACCENT
        self._paint_swatch(key)
        self.accentChanged.emit(key)

    def _pick_wallpaper(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(
            self, i18n.tr("pref.wallpaper"), "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp);;All files (*)")
        if not chosen:
            return
        self._show_wallpaper(chosen)
        self.wallpaperChanged.emit(chosen)

    def _clear_wallpaper(self) -> None:
        self._show_wallpaper("")
        self.wallpaperChanged.emit("")

    # -- language refresh -------------------------------------------------
    def render(self) -> None:
        self.setWindowTitle(i18n.tr("pref.title"))
        self.heading.setText(i18n.tr("pref.title"))
        self.language_section.setText(i18n.tr("pref.language"))
        self.language_hint.setText(i18n.tr("pref.language.hint"))
        self.logdir_section.setText(i18n.tr("pref.logdir"))
        self.logdir_hint.setText(i18n.tr("pref.logdir.hint"))
        self.btn_logdir.setText(i18n.tr("pref.logdir.pick"))
        self.btn_logdir_reset.setText(i18n.tr("pref.logdir.default"))
        self.accent_section.setText(i18n.tr("pref.accent"))
        self.accent_hint.setText(i18n.tr("pref.accent.hint"))
        self.wallpaper_section.setText(i18n.tr("pref.wallpaper"))
        self.wallpaper_hint.setText(i18n.tr("pref.wallpaper.hint"))
        self.btn_wallpaper.setText(i18n.tr("pref.wallpaper.pick"))
        self.btn_wallpaper_clear.setText(i18n.tr("pref.wallpaper.clear"))
        self.buffer_section.setText(i18n.tr("pref.buffer"))
        self.buffer_hint.setText(i18n.tr("pref.buffer.hint"))
        self.buffer_value.setToolTip(i18n.tr("pref.buffer.tip"))
        self.ui_font_section.setText(i18n.tr("pref.font.ui"))
        self.ui_font_hint.setText(i18n.tr("pref.font.ui.hint"))
        self.log_font_section.setText(i18n.tr("pref.font.log"))
        self.log_font_hint.setText(i18n.tr("pref.font.log.hint"))
        self.history_section.setText(i18n.tr("pref.history"))
        self.history_value.setToolTip(i18n.tr("pref.history.tip"))
        self.history_value.setSuffix(i18n.tr("pref.history.unit"))
        self.btn_clear_history.setText(i18n.tr("pref.history.clear"))
        self.history_hint.setText(i18n.tr("pref.history.hint"))
        self.history_count.setText(
            i18n.tr("pref.history.count", count=self._history_entries))
        family_default = i18n.tr("pref.font.default")
        self.ui_family_box.setItemText(0, family_default)
        self.log_family_box.setItemText(0, family_default)
        for index, (_key, zh, en) in enumerate(theme.accent_choices()):
            self.accent_box.setItemText(index, zh if i18n.language() == "zh" else en)
        self._show_wallpaper(self._wallpaper)


class DocsDialog(QDialog):
    """Renders the built-in manual (same source as the READMEs)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("docs.title"))
        self.setModal(True)
        self.resize(760, 640)
        self.setMinimumSize(480, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        self.title = QLabel(i18n.tr("docs.title"))
        self.title.setStyleSheet("font-size: 15px; font-weight: 650;")
        head.addWidget(self.title)
        head.addStretch(1)
        # No close button here: the dialog already has a frame with an X, and a
        # second way to do the same thing is clutter (R-044).
        layout.addLayout(head)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setStyleSheet(
            f"QTextBrowser {{ background: {theme.CARD}; border: 1px solid {theme.LINE};"
            f" border-radius: {theme.RADIUS_CARD}px; padding: 12px 14px;"
            f" font-size: 13px; color: {theme.INK}; }}")
        layout.addWidget(self.view, 1)

        self.render()

    def render(self) -> None:
        self.setWindowTitle(i18n.tr("docs.title"))
        self.title.setText(i18n.tr("docs.title"))
        self.view.setHtml(
            "<style>"
            f"body {{ color: {theme.INK}; font-size: 13px; line-height: 1.55; }}"
            f"h3 {{ color: {theme.TEAL}; }}"
            f"li {{ margin: 3px 0; }}"
            f"code {{ background: {theme.WASH}; }}"
            "</style>" + docs.manual_html(i18n.language()))


class AboutDialog(QDialog):
    """Version, runtime and data folder, with a copy button."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("about.title"))
        self.setModal(True)
        self.resize(520, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(14)
        self.logo = QLabel()
        self.logo.setFixedSize(64, 64)
        self.logo.setAlignment(Qt.AlignCenter)
        path = paths.resource_file("assets", "logo-64.png") or paths.resource_file(
            "assets", "omnicom.png")
        if path:
            pixmap = QPixmap(path).scaled(64, 64, Qt.KeepAspectRatio,
                                          Qt.SmoothTransformation)
            self.logo.setPixmap(pixmap)
        head.addWidget(self.logo)

        names = QVBoxLayout()
        names.setSpacing(2)
        self.app_title = QLabel(f"OmniCOM {__version__}")
        self.app_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        names.addWidget(self.app_title)
        self.blurb = QLabel(i18n.tr("about.blurb"))
        self.blurb.setWordWrap(True)
        self.blurb.setStyleSheet(f"color: {theme.INK2}; font-size: 12px;")
        names.addWidget(self.blurb)
        head.addLayout(names, 1)
        layout.addLayout(head)

        self.detail = QLabel()
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet(
            f"color: {theme.INK2}; font-size: 12px; background: {theme.WASH};"
            f" border-radius: {theme.RADIUS_CTRL}px; padding: 10px 12px;")
        layout.addWidget(self.detail)
        layout.addStretch(1)

        self.info_text = ""

        # No "Close" button: the dialog already has a window frame with an X,
        # and two ways to do the same thing is clutter (R-044).
        footer = QHBoxLayout()
        self.copy_btn = QPushButton(i18n.tr("about.copy"))
        self.copy_btn.setObjectName("Ghost")
        self.copy_btn.clicked.connect(self._copy)
        footer.addWidget(self.copy_btn)
        footer.addStretch(1)
        layout.addLayout(footer)

        self.render()

    def render(self) -> None:
        self.setWindowTitle(i18n.tr("about.title"))
        self.blurb.setText(i18n.tr("about.blurb"))
        self.copy_btn.setText(i18n.tr("about.copy"))
        runtime = (f"Python {platform.python_version()} · "
                   f"Qt {_qt_version()} · {platform.system()} {platform.release()}")
        self.info_text = "\n".join((
            f"OmniCOM {__version__}",
            f"{i18n.tr('about.version')}: {__version__}",
            f"{i18n.tr('about.python')}: {runtime}",
            f"{i18n.tr('about.datadir')}: {paths.data_dir()}",
        ))
        self.detail.setText(self.info_text)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.info_text)


def _qt_version() -> str:
    try:
        from PySide6 import __version__ as pyside_version

        return pyside_version
    except Exception:
        return "?"


class PluginInfoBar(QWidget):
    """Small banner used when a plugin folder fails to load."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 10, 8)
        layout.setSpacing(8)
        icon = GlyphButton(GlyphButton.INFO, self, size=18)
        layout.addWidget(icon)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {theme.AMBER}; font-size: 12px;")
        layout.addWidget(label, 1)
        self.setStyleSheet(
            f"QWidget {{ background: {theme.AMBER_SOFT};"
            f" border: 1px solid rgba(180, 83, 9, 80);"
            f" border-radius: {theme.RADIUS_CTRL}px; }}")


def scrollable(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setWidget(widget)
    return area


def python_bits() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
