"""Preferences, documentation and about dialogs."""

from __future__ import annotations

import platform
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, docs, i18n, paths
from . import theme
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
    """Language and behaviour switches.  Applies immediately."""

    languageChanged = Signal(str)

    def __init__(self, parent=None, current_language: str = "zh") -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("pref.title"))
        self.setModal(True)
        self.resize(520, 280)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        self.heading = QLabel(i18n.tr("pref.title"))
        self.heading.setStyleSheet("font-size: 16px; font-weight: 650;")
        layout.addWidget(self.heading)

        self.section = QLabel(i18n.tr("pref.language"))
        self.section.setStyleSheet(
            f"color: {theme.DIM}; font-size: 11px; font-weight: 600; letter-spacing: 1px;")
        layout.addWidget(self.section)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.language_box = Combo()
        for lang in i18n.LANGS:
            self.language_box.addItem(i18n.LANG_NAMES[lang], lang)
        index = self.language_box.findData(current_language)
        self.language_box.setCurrentIndex(max(0, index))
        self.language_box.currentIndexChanged.connect(self._on_language)
        row.addWidget(self.language_box, 1)
        layout.addLayout(row)

        self.hint = QLabel(i18n.tr("pref.language.hint"))
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        layout.addWidget(self.hint)
        layout.addStretch(1)

        # The window frame already offers a close button (R-044).

    def _on_language(self, _index: int) -> None:
        lang = self.language_box.currentData() or "zh"
        self.languageChanged.emit(lang)

    def render(self) -> None:
        self.setWindowTitle(i18n.tr("pref.title"))
        self.heading.setText(i18n.tr("pref.title"))
        self.section.setText(i18n.tr("pref.language"))
        self.hint.setText(i18n.tr("pref.language.hint"))


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
