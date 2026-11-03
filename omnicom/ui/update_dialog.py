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


def _size(count: int) -> str:
    """``42987769`` -> ``41.0 MB`` - used by the download log only."""
    value = float(max(0, int(count)))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return (f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}")
        value /= 1024
    return f"{value:.1f} GB"


class UpdateWatcher(QObject):
    """Asks GitHub for the newest release without touching the GUI thread."""

    found = Signal(dict)      # update.check() result
    failed = Signal(str)      # offline / rate limited / malformed + the reason
    #: ``(step, total, line)``: progress of one check, for the dialog's log.
    traced = Signal(int, int, str)

    def __init__(self, parent=None, current: str = __version__) -> None:
        super().__init__(parent)
        self._current = current
        self._thread: threading.Thread | None = None
        self._busy = False
        #: Last answer, so the menu can be rebuilt without another request.
        self.last: dict | None = None
        #: Why the last attempt failed ("" when it succeeded).
        self.error = ""
        #: Which route answered: ``"api"``, ``"web"`` or ``""``.
        self.source = ""
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

        def log(line: str) -> None:
            # Progress only exists while somebody is watching; the queued
            # signal is harmless when no dialog is open.
            if not self._muted:
                self.traced.emit(0, 3, line)

        def progress(done: int, total: int) -> None:
            if not self._muted:
                self.traced.emit(int(done), int(total), "")

        def work() -> None:
            try:
                result = update.check_release(self._current, log=log,
                                              progress=progress)
            except Exception as error:          # a check must never crash us
                traceback.print_exc()
                result = {"ok": False, "source": "",
                          "error": f"{type(error).__name__}: {error}"}
            self._busy = False
            self.error = str(result.get("error") or "")
            self.source = str(result.get("source") or "")
            if result.get("ok"):
                self.last = {key: value for key, value in result.items()
                             if key not in ("ok", "source", "error")}
            if self._muted:
                return
            if not result.get("ok"):
                self.failed.emit(self.error or "未知原因")
                return
            self.found.emit(self.last)

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

    #: Dialog-local relays, so a watcher can be shared by several dialogs and
    #: every step still lands on the GUI thread.
    chk_found = Signal(dict)
    chk_failed = Signal(str)
    chk_traced = Signal(int, int, str)

    def __init__(self, parent=None, info: dict | None = None,
                 watcher: UpdateWatcher | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("update.title"))
        self.setModal(True)
        self.resize(600, 520)
        self._info = dict(info or {})
        self._working = False
        self._checking = False
        self._watcher = watcher
        #: Last percentage written to the download log, so it grows by ~10 %.
        self._logged_percent = -10

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # -- which version am I running, and which one is out there (R-143).
        # Always visible: "检查更新为什么没反应" starts with not knowing what
        # the program currently is.
        self.versions = QLabel("")
        self.versions.setWordWrap(True)
        self.versions.setStyleSheet(
            f"color: {theme.INK2}; font-size: 12px;"
            f" font-family: {theme.MONO_FONT};")
        layout.addWidget(self.versions)

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

        # -- one log for both jobs: the version check and the download.  It has
        # to be the same widget, because "为什么没反应" cannot be answered by a
        # progress bar alone - it needs the line that stopped (R-143).
        self.checklog = QTextBrowser()
        self.checklog.setMaximumHeight(118)
        self.checklog.setStyleSheet(
            f"QTextBrowser {{ background: {theme.WASH}; color: {theme.INK2};"
            f" border: 1px solid {theme.LINE_SOFT};"
            f" border-radius: {theme.RADIUS_CTRL}px; padding: 5px 7px;"
            f" font-family: {theme.MONO_FONT}; font-size: 11.5px; }}")
        layout.addWidget(self.checklog)

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
        # 重新检查 is always available - it is the escape hatch when the first
        # attempt was rate limited or the network was down (R-143).
        self.btn_check = QPushButton(tr("update.retry"))
        self.btn_check.setObjectName("Primary")
        self.btn_check.setCursor(Qt.PointingHandCursor)
        self.btn_check.setToolTip(tr("update.retry.tip"))
        self.btn_check.clicked.connect(self.recheck)
        row.addWidget(self.btn_check)

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

        # The relays exist even without a watcher, so the dialog is usable in
        # tests and in the "install from a local zip"-only case.
        self.chk_found.connect(self._on_check_found, Qt.QueuedConnection)
        self.chk_failed.connect(self._on_check_failed, Qt.QueuedConnection)
        self.chk_traced.connect(self._on_check_traced, Qt.QueuedConnection)

        self.render(self._info)
        if watcher is not None:
            watcher.found.connect(self.chk_found)
            watcher.failed.connect(self.chk_failed)
            watcher.traced.connect(self.chk_traced)

    # ------------------------------------------------------------------ view
    def _update_versions(self) -> None:
        """The two version numbers, always on screen (R-143)."""
        current = str(self._info.get("current") or __version__)
        latest = str(self._info.get("version") or "")
        if not latest:
            latest = tr("update.version.unknown")
        elif not self._info.get("available"):
            latest = tr("update.version.same", version=latest)
        self.versions.setText(
            tr("update.version", current=current, latest=latest))

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
        self.btn_check.setEnabled(not self._checking and not self._working)
        self.btn_install.setVisible(available)
        self.btn_install.setEnabled(available and has_asset and not self._working)
        self.btn_page.setVisible(available)
        # Always offered, including when the online check says "up to date":
        # that is exactly the situation where a user wants to install a zip
        # they were given by hand (R-121).
        self.btn_local.setEnabled(not self._working)
        self.btn_later.setText(tr("update.later") if available
                               else tr("update.close"))
        self._update_versions()

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def log_line(self, text: str) -> None:
        """Append one line to the check/download log and keep it visible."""
        if not text:
            return
        self.checklog.append(text)
        bar = self.checklog.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------- the check
    def start_check(self) -> None:
        """Run (or attach to) the online check, with progress and a log."""
        self._checking = True
        self.btn_check.setEnabled(False)
        self.progress.show()
        self.progress.setRange(0, 3)
        self.progress.setValue(0)
        self.heading.setText(tr("update.checking"))
        self.log_line(tr("update.check.start"))
        watcher = self._watcher
        if watcher is None:
            self._on_check_failed("no watcher")
            return
        if not watcher.start():
            # Someone else's check is already in flight: the signals it emits
            # land here anyway, so this only has to say so (R-143).
            self.log_line(tr("update.check.busy"))
            self.progress.setRange(0, 3)
            self.progress.setValue(1)

    def recheck(self) -> None:
        self.checklog.clear()
        self._info = {}
        self.render({})
        self.start_check()

    def _finish_check(self) -> None:
        self._checking = False
        self.btn_check.setEnabled(not self._working)

    def _on_check_traced(self, done: int, total: int, line: str) -> None:
        if line:
            self.log_line(line)
        # A step can arrive for a check that this dialog did not start (the
        # watcher is shared), so the bar is made visible here too (R-143).
        self.progress.show()
        if total > 0:
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(max(0, min(total, done)))
        else:
            self.progress.setRange(0, 0)

    def _on_check_found(self, info: dict) -> None:
        self._finish_check()
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        source = str(getattr(self._watcher, "source", "") or "")
        if source in ("api", "web"):
            # Says which route answered - useful when the API is rate limited
            # and the answer really came from the release page (R-143).
            self.log_line(tr("update.check.via",
                             source=tr("update.via." + source)))
        self.render(info)

    def _on_check_failed(self, message: str) -> None:
        self._finish_check()
        self.progress.hide()
        self.heading.setText(tr("update.check.failed", err=message))
        self.detail.setText(tr("update.check.hint"))
        self.log_line(tr("update.check.failed", err=message))
        self._info = {}
        self._update_versions()

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
        self.btn_check.setEnabled(False)
        self.btn_install.setEnabled(False)
        self._logged_percent = -10
        self.progress.show()
        self.progress.setRange(0, 0)              # busy until the first byte
        self.set_status(tr("update.downloading"))
        self.log_line(tr("update.downloading"))
        self.log_line(tr("update.download.from", url=asset))

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
        self.btn_check.setEnabled(False)
        self.btn_local.setEnabled(False)
        self.progress.show()
        self.progress.setRange(0, 0)
        self.set_status(tr("update.local.reading"))
        self.log_line(tr("update.local.reading"))

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
            percent = max(0, min(100, int(done * 100 / total)))
            self.progress.setValue(percent)
            text = tr("update.download.pct", percent=percent,
                      done=_size(done), total=_size(total))
            self.set_status(text)
            # One log line per 10 % keeps the log readable while still proving
            # that the download is moving (R-143).
            if percent >= self._logged_percent + 10 or percent == 100:
                self._logged_percent = percent
                self.log_line(text)
        else:
            self.progress.setRange(0, 0)
            self.set_status(tr("update.download.pct", percent=0,
                              done=_size(done), total="?"))

    def _on_failed(self, message: str) -> None:
        self._working = False
        self.progress.hide()
        self.btn_check.setEnabled(True)
        self.log_line(tr("update.failed", err=message))
        self.btn_install.setEnabled(bool(self._info.get("asset_url")))
        self.set_status(tr("update.failed", err=message))

    def _on_staged(self, version: str) -> None:
        self._working = False
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.set_status(tr("update.staged", version=version))
        self.log_line(tr("update.staged", version=version))
        self.btn_install.setEnabled(False)
        self.btn_later.setText(tr("update.close"))

    def _on_local_failed(self, message: str) -> None:
        self._working = False
        self.progress.hide()
        self.btn_check.setEnabled(True)
        self.btn_local.setEnabled(True)
        self.log_line(tr("update.local.bad", err=message))
        self.btn_install.setEnabled(bool(self._info.get("asset_url")))
        self.set_status(tr("update.local.bad", err=message))

    def _on_local_staged(self, version: str, same: bool) -> None:
        self._working = False
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.log_line(tr("update.staged", version=version or "?"))
        self.btn_local.setEnabled(False)
        self.btn_check.setEnabled(True)
        self.btn_install.setEnabled(False)
        self.btn_later.setText(tr("update.close"))
        if same:
            # Reinstalling the version already running is allowed but must not
            # look like a normal upgrade.
            self.set_status(tr("update.local.same", version=version) + " "
                            + tr("update.staged", version=version))
