"""Qt half of the software update: a background check and one dialog.

The check runs on a plain thread and reports through a Qt signal, so the GUI
thread never waits on the network (R-107: an update check is the lowest priority
thing the program does).  Downloading and unpacking run the same way, which is
why the dialog only ever shows text and progress - it never blocks the window.
"""

from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from .. import __version__, update
from ..i18n import tr
from . import theme


class UpdateWatcher(QObject):
    """Asks GitHub for the newest release without touching the GUI thread."""

    found = Signal(dict)      # update.check() result
    failed = Signal()         # offline / rate limited / malformed

    def __init__(self, parent=None, current: str = __version__) -> None:
        super().__init__(parent)
        self._current = current
        self._thread: threading.Thread | None = None
        self._busy = False
        #: Last answer, so the menu can be rebuilt without another request.
        self.last: dict | None = None
        self._muted = False

    @property
    def busy(self) -> bool:
        return self._busy

    def mute(self) -> None:
        """Stop reporting: used when a caller does not want the dot at all."""
        self._muted = True

    def start(self) -> bool:
        """Begin a check.  Returns False when one is already running."""
        if self._busy:
            return False
        self._busy = True

        def work() -> None:
            try:
                result = update.check(self._current)
            except Exception:                     # a check must never crash us
                traceback.print_exc()
                result = None
            self._busy = False
            if self._muted:
                return
            if result is None:
                self.failed.emit()
                return
            self.last = result
            self.found.emit(result)

        self._thread = threading.Thread(target=work, name="omnicom-update",
                                        daemon=True)
        self._thread.start()
        return True

    def wait(self, timeout: float = 15.0) -> None:
        """Only for tests: block until the background thread is done."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


class UpdateDialog(QDialog):
    """Shows the release and offers 立即更新 / 打开下载页."""

    #: Emitted from the worker thread; Qt queues them onto the GUI thread.
    downloadProgress = Signal(int, int)
    downloadFailed = Signal(str)
    staged = Signal(str)
    #: A package picked from disk: (version, is_same_version).
    localStaged = Signal(str, bool)
    localFailed = Signal(str)

    def __init__(self, parent=None, info: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("update.title"))
        self.setModal(True)
        self.resize(560, 420)
        self._info = dict(info or {})
        self._working = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.heading = QLabel("")
        self.heading.setStyleSheet("font-size: 16px; font-weight: 650;")
        layout.addWidget(self.heading)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet(f"color: {theme.INK2}; font-size: 12.5px;")
        layout.addWidget(self.detail)

        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setStyleSheet(
            f"QTextBrowser {{ background: {theme.WASH}; color: {theme.INK};"
            f" border: 1px solid {theme.LINE_SOFT};"
            f" border-radius: {theme.RADIUS_CTRL}px; padding: 6px 8px; }}")
        layout.addWidget(self.notes, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {theme.DIM}; font-size: 11.5px;")
        layout.addWidget(self.status)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_install = QPushButton(tr("update.install"))
        self.btn_install.setObjectName("Primary")
        self.btn_install.setCursor(Qt.PointingHandCursor)
        self.btn_install.clicked.connect(self.install)
        row.addWidget(self.btn_install)

        self.btn_page = QPushButton(tr("update.page"))
        self.btn_page.setObjectName("Ghost")
        self.btn_page.setCursor(Qt.PointingHandCursor)
        self.btn_page.clicked.connect(self.open_page)
        row.addWidget(self.btn_page)

        # Offline / intranet installs have no GitHub, and a user may be handed
        # a zip on a USB stick; the same staging code handles both, so this is
        # only a file picker (R-121).
        self.btn_local = QPushButton(tr("update.local"))
        self.btn_local.setObjectName("Ghost")
        self.btn_local.setCursor(Qt.PointingHandCursor)
        self.btn_local.setToolTip(tr("update.local.tip"))
        self.btn_local.clicked.connect(self.install_local)
        row.addWidget(self.btn_local)

        row.addStretch(1)
        self.btn_later = QPushButton(tr("update.later"))
        self.btn_later.setObjectName("Ghost")
        self.btn_later.setCursor(Qt.PointingHandCursor)
        self.btn_later.clicked.connect(self.reject)
        row.addWidget(self.btn_later)
        layout.addLayout(row)

        self.downloadProgress.connect(self._on_progress, Qt.QueuedConnection)
        self.downloadFailed.connect(self._on_failed, Qt.QueuedConnection)
        self.staged.connect(self._on_staged, Qt.QueuedConnection)
        self.localStaged.connect(self._on_local_staged, Qt.QueuedConnection)
        self.localFailed.connect(self._on_local_failed, Qt.QueuedConnection)

        self.render(self._info)

    # ------------------------------------------------------------------ view
    def render(self, info: dict) -> None:
        self._info = dict(info or {})
        available = bool(self._info.get("available"))
        version = str(self._info.get("version") or "")
        current = str(self._info.get("current") or __version__)
        if not self._info:
            self.heading.setText(tr("update.checking"))
            self.detail.setText("")
            self.notes.setPlainText("")
        elif available:
            self.heading.setText(tr("update.found", version=version))
            self.detail.setText(tr("update.current", current=current))
            self.notes.setPlainText(str(self._info.get("notes") or ""))
        else:
            self.heading.setText(tr("update.latest", version=current))
            # No "it will check again later" filler: the dialog answers one
            # question and stops (R-107).
            self.detail.setText("")
            self.notes.setPlainText("")
        has_asset = bool(self._info.get("asset_url"))
        self.btn_install.setVisible(available)
        self.btn_install.setEnabled(available and has_asset and not self._working)
        self.btn_page.setVisible(available)
        # Always offered, including when the online check says "up to date":
        # that is exactly the situation where a user wants to install a zip
        # they were given by hand (R-121).
        self.btn_local.setEnabled(not self._working)
        if not available:
            self.btn_later.setText(tr("update.close"))

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    # ---------------------------------------------------------------- action
    def open_page(self) -> None:
        url = str(self._info.get("url") or update.RELEASES_PAGE)
        QDesktopServices.openUrl(QUrl(url))

    def install(self) -> None:
        """Download + stage the release, then tell the user to restart."""
        if self._working:
            return
        asset = str(self._info.get("asset_url") or "")
        if not asset:
            self.open_page()
            return
        version = str(self._info.get("version") or "new")
        self._working = True
        self.btn_install.setEnabled(False)
        self.progress.show()
        self.progress.setRange(0, 0)              # busy until the first byte
        self.set_status(tr("update.downloading"))

        def work() -> None:
            try:
                target = update.download(
                    asset, update.updates_dir() + f"/OmniCOM-{version}.zip",
                    progress=lambda done, total:
                    self.downloadProgress.emit(done, total))
                update.stage(target, version)
            except Exception as error:            # offline, disk full, bad zip
                self.downloadFailed.emit(str(error))
                return
            self.staged.emit(version)

        threading.Thread(target=work, name="omnicom-update-dl", daemon=True).start()

    def install_local(self) -> None:
        """Stage a release zip the user picked; applied on the next start."""
        if self._working:
            return
        chosen, _filter = QFileDialog.getOpenFileName(
            self, tr("update.local.pick"), "",
            tr("update.local.filter") + ";;*.*")
        if not chosen:
            return
        self._working = True
        self.btn_install.setEnabled(False)
        self.btn_local.setEnabled(False)
        self.progress.show()
        self.progress.setRange(0, 0)
        self.set_status(tr("update.local.reading"))

        def work() -> None:
            try:
                result = update.stage_local(chosen)
            except Exception as error:       # not a bundle, disk full, locked
                self.localFailed.emit(str(error))
                return
            self.localStaged.emit(str(result.get("version") or ""),
                                  bool(result.get("same_version")))

        threading.Thread(target=work, name="omnicom-update-local",
                         daemon=True).start()

    # -- slots for the worker thread ---------------------------------------
    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(max(0, min(100, int(done * 100 / total))))
        else:
            self.progress.setRange(0, 0)

    def _on_failed(self, message: str) -> None:
        self._working = False
        self.progress.hide()
        self.btn_install.setEnabled(bool(self._info.get("asset_url")))
        self.set_status(tr("update.failed", err=message))

    def _on_staged(self, version: str) -> None:
        self._working = False
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.set_status(tr("update.staged", version=version))
        self.btn_install.setEnabled(False)
        self.btn_later.setText(tr("update.close"))

    def _on_local_failed(self, message: str) -> None:
        self._working = False
        self.progress.hide()
        self.btn_local.setEnabled(True)
        self.btn_install.setEnabled(bool(self._info.get("asset_url")))
        self.set_status(tr("update.local.bad", err=message))

    def _on_local_staged(self, version: str, same: bool) -> None:
        self._working = False
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.set_status(tr("update.staged", version=version or "?"))
        self.btn_local.setEnabled(False)
        self.btn_install.setEnabled(False)
        self.btn_later.setText(tr("update.close"))
        if same:
            # Reinstalling the version already running is allowed but must not
            # look like a normal upgrade.
            self.set_status(tr("update.local.same", version=version) + " "
                            + tr("update.staged", version=version))
