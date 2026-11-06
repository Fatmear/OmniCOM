"""Non-blocking plugin management dialog."""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..i18n import language, tr
from ..plugin_store import PluginStore, StoreError, configured_catalogs, select_version, version_key
from ..plugins import API_VERSION
from . import theme, widgets


class StoreTask(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, callback, parent=None) -> None:
        super().__init__(parent)
        self.callback = callback
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            self.result.emit(self.callback(self.cancel, self.progress.emit))
        except StoreError as exc:
            self.failed.emit(exc.code)
        except Exception:
            self.failed.emit("internal")


def localized(value) -> str:
    if isinstance(value, dict):
        return str(value.get(language()) or value.get("en") or next(iter(value.values()), ""))
    return str(value or "")


def size_text(entry: dict) -> str:
    size = entry.get("size")
    if not isinstance(size, int) or size <= 0:
        return ""
    return f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} MB"


class VersionPicker(QWidget):
    """Per-plugin version drop-down (R-178).

    Two jobs the plain row could not do:

    * make the installed and latest builds *visible as versions*, so "install a
      specific version" is one gesture instead of a downgrade nobody can find;
    * keep the button honest.  Its caption follows the selection - 安装 / 更新 /
      降级安装 / 已安装 - so the same click never means two different things
      depending on what is installed.
    """

    def __init__(self, row, parent=None) -> None:
        super().__init__(parent)
        self.row = row
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        caption = QLabel(tr("store.version_pick"))
        caption.setObjectName("Dim")
        layout.addWidget(caption)
        self.combo = widgets.Combo()
        self.combo.setMinimumWidth(190)
        layout.addWidget(self.combo)
        layout.addStretch(1)
        self.combo.currentIndexChanged.connect(self._sync_button)
        self.refresh()

    def refresh(self) -> None:
        row = self.row
        wanted = self.combo.currentData() or row.get("picked")
        self.combo.blockSignals(True)
        self.combo.clear()
        for entry in row["item"].get("versions") or []:
            version = str(entry.get("version"))
            notes = [localized(entry["notes"])] if entry.get("notes") else []
            if entry.get("size"):
                notes.append(size_text(entry))
            if version == row["item"]["latest_version"]:
                notes.append(tr("store.tag.latest"))
            if version == row["item"]["installed_version"]:
                notes.append(tr("store.tag.installed"))
            incompat = int(entry.get("api", 1)) > API_VERSION
            if incompat:
                notes.append(tr("store.tag.api"))
            text = version + ("  ·  " + " · ".join(notes) if notes else "")
            self.combo.addItem(text, version)
            if incompat:
                index = self.combo.count() - 1
                self.combo.setItemData(index, tr("store.incompatible"),
                                       Qt.ItemDataRole.ToolTipRole)
        target = self.combo.findData(wanted) if wanted else -1
        if target < 0:
            # Land on the build the user came for: the newest one, unless that
            # is already installed - then the next older package is the only
            # thing a click can do, and selecting it makes that obvious.
            target = 0
            installed = row["item"]["installed_version"]
            if installed and self.combo.count() > 1 and str(self.combo.itemData(0)) == str(installed):
                for index in range(1, self.combo.count()):
                    if not (self.row["item"].get("versions") or [])[index].get("local"):
                        target = index
                        break
        self.combo.setCurrentIndex(target)
        self.combo.blockSignals(False)
        self.combo.setEnabled(self.combo.count() > 1)
        self._sync_button()

    def selected(self) -> dict | None:
        version = self.combo.currentData()
        for entry in self.row["item"].get("versions") or []:
            if str(entry.get("version")) == str(version):
                return entry
        return None

    def _sync_button(self) -> None:
        row = self.row
        entry = self.selected() or {}
        version = str(entry.get("version") or "")
        installed = row["item"]["installed_version"]
        api_ok = int(entry.get("api", 1)) <= API_VERSION
        download = bool(row["item"].get("versions") and version
                        and not entry.get("local"))
        if not version:
            state, caption = "none", tr("store.install")
        elif version == installed:
            state, caption = "same", tr("store.tag.installed")
        elif installed and version_key(version) < version_key(installed):
            state, caption = "downgrade", tr("store.downgrade")
        elif installed:
            state, caption = "update", tr("store.update")
        else:
            state, caption = "install", tr("store.install")
        button = row.get("install")
        if button is None:
            row["picked"] = version
            row["combo"] = self.combo
            row["state"] = state
            return
        button.setText(caption)
        # A local-only entry (an installed build no catalog carries) has nothing
        # to download, and an equal version is a no-op - both stay disabled
        # instead of firing an install that can only fail or re-download.
        button.setEnabled(state not in ("same", "none") and api_ok and download)
        if not api_ok:
            button.setToolTip(tr("store.incompatible"))
        elif state == "same":
            button.setToolTip(tr("store.same_tip"))
        else:
            button.setToolTip("")
        button.setObjectName("Primary" if state in ("install", "update", "downgrade") else "Ghost")
        button.style().unpolish(button)
        button.style().polish(button)
        row["picked"] = version
        row["combo"] = self.combo
        row["state"] = state


class PluginStoreDialog(QDialog):
    def __init__(self, window, store=None) -> None:
        super().__init__(window)
        self.window_ref = window
        self._external_store = store is not None
        self.store = store or PluginStore(catalog_urls=configured_catalogs(window.settings))
        self.catalog = self.store.cached_catalog()
        self.task = None
        self.rows = {}
        self._mutating = False
        self.setWindowTitle(tr("store.title"))
        self.resize(800, 620)
        self.setMinimumSize(550, 360)
        root = QVBoxLayout(self)
        source_row = QHBoxLayout()
        self.count = QLabel()
        self.count.setObjectName("Dim")
        source_row.addWidget(self.count)
        source_row.addStretch(1)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(tr("store.filter"))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setMinimumWidth(180)
        self.filter_edit.textChanged.connect(self._on_filter)
        source_row.addWidget(self.filter_edit)
        self.refresh_button = QPushButton(tr("store.refresh"))
        self.refresh_button.clicked.connect(self.check)
        source_row.addWidget(self.refresh_button)
        root.addLayout(source_row)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.host = QWidget()
        self.list_layout = QVBoxLayout(self.host)
        self.scroll.setWidget(self.host)
        root.addWidget(self.scroll, 1)
        footer = QHBoxLayout()
        self.status = QLabel(tr("store.cached"))
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.cancel_button = QPushButton(tr("store.cancel"))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        footer.addWidget(self.cancel_button)
        root.addLayout(footer)
        self.rebuild()

    def rebuild(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.rows.clear()
        needle = self.filter_edit.text().strip().lower()
        shown = 0
        entries = self.store.rows(self.catalog)
        for item in entries:
            haystack = " ".join([item["id"], localized(item.get("name")),
                                 localized(item.get("description")),
                                 str(item.get("author") or "")]).lower()
            if needle and needle not in haystack:
                continue
            shown += 1
            row = QFrame()
            row.setObjectName("PluginStoreRow")
            row.setStyleSheet(f"QFrame#PluginStoreRow {{ background: {theme.CARD}; border: 1px solid {theme.LINE}; border-radius: {theme.RADIUS_CTRL}px; }}")
            layout = QVBoxLayout(row)
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(6)
            header = QHBoxLayout()
            name = QLabel(localized(item.get("name")) or item["id"])
            # The plugin name is the row's title: bold and one notch larger, so
            # the eye lands on "数据看板" instead of on the description right
            # below it (R-180).  Set through ``objectName`` so the weight comes
            # from ``theme`` rather than a hardcoded colour/family here.
            name.setObjectName("PluginName")
            name.setTextFormat(Qt.TextFormat.PlainText)
            name.setWordWrap(True)
            header.addWidget(name, 1)
            # The badges are parented to ``row`` up front: a parentless
            # widget is a top-level window, so ``show_text()`` before
            # ``addWidget()`` used to flash a blank little window every
            # time the list was rebuilt (R-181).
            if item.get("author"):
                author = widgets.Badge(str(item["author"]), row)
                author.show_text(str(item["author"]))
                header.addWidget(author, 0, Qt.AlignmentFlag.AlignTop)
            installed_tag = widgets.Badge("", row)
            if item["installed_version"]:
                installed_tag.show_text(tr("store.tag.installed_of",
                                           version=item["installed_version"]))
            header.addWidget(installed_tag, 0, Qt.AlignmentFlag.AlignTop)
            badge = QWidget()
            badge.setObjectName("PluginUpdateDot")
            badge.setFixedSize(8, 8)
            badge.setStyleSheet(f"background: {theme.DANGER}; border-radius: 4px;")
            badge.setVisible(item["update_available"])
            badge.setToolTip(tr("store.update"))
            header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            layout.addLayout(header)
            # A hairline under the title row: with only the (now bolder) name,
            # the header and the description still read as one paragraph
            # (R-180).  It also gives the tags a baseline to sit on.
            rule = QFrame()
            rule.setObjectName("PluginRule")
            rule.setFixedHeight(1)
            rule.setStyleSheet(f"background: {theme.LINE_SOFT}; border: 0;")
            layout.addWidget(rule)
            description = QLabel(localized(item.get("description")))
            description.setObjectName("PluginTagline")
            description.setTextFormat(Qt.TextFormat.PlainText)
            description.setWordWrap(True)
            layout.addWidget(description)
            latest = item["latest_version"] or tr("store.unknown")
            version = QLabel(tr("store.installed", current=item["installed_version"], latest=latest)
                             if item["installed_version"] else tr("store.missing", latest=latest))
            version.setWordWrap(True)
            version.setObjectName("Dim")
            layout.addWidget(version)
            entry = {"item": item, "widget": row, "badge": badge, "version": version,
                     "installed_tag": installed_tag, "picked": None}
            actions = QHBoxLayout()
            picker = VersionPicker(entry)
            actions.addWidget(picker, 1)
            if item["installed_version"]:
                remove = QPushButton(tr("store.uninstall"))
                remove.setObjectName("Danger")
                remove.clicked.connect(lambda _checked=False, data=item: self.confirm_remove(data))
                actions.addWidget(remove)
            install = QPushButton(tr("store.install"))
            install.clicked.connect(lambda _checked=False, data=entry: self.install_selected(data))
            actions.addWidget(install)
            entry["install"] = install
            entry["picker"] = picker
            picker._sync_button()
            layout.addLayout(actions)
            self.list_layout.addWidget(row)
            self.rows[item["id"]] = entry
        if not shown:
            empty = QLabel(tr("store.empty_filter") if needle else tr("store.empty"))
            empty.setObjectName("Dim")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list_layout.addWidget(empty)
        self.list_layout.addStretch(1)
        self.count.setText(tr("store.count", shown=shown, total=len(entries)))

    def busy(self) -> bool:
        return self.task is not None

    def retranslate(self) -> None:
        self.setWindowTitle(tr("store.title"))
        self.refresh_button.setText(tr("store.refresh"))
        self.cancel_button.setText(tr("store.cancel"))
        self.filter_edit.setPlaceholderText(tr("store.filter"))
        self.status.setText(tr("store.working") if self.busy() else tr("store.cached"))
        self.rebuild()

    def _on_filter(self, _text: str) -> None:
        # Rebuild, not hide/show: the row carries a version picker whose
        # selection must not survive a filter that removed its siblings.
        self.rebuild()

    def start(self, callback, *, mutating=False) -> None:
        if self.busy():
            return
        self._mutating = mutating
        if mutating:
            self.window_ref.suspend_plugins()
        self.host.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText(tr("store.working"))
        self.task = StoreTask(callback, self)
        self.task.result.connect(self.received)
        self.task.failed.connect(self.failed)
        self.task.progress.connect(lambda received, total: self.status.setText(
            tr("store.progress", received=received // 1024, total=total // 1024)))
        self.task.finished.connect(self._task_finished)
        self.task.start()

    def check(self) -> None:
        if self.busy():
            return
        if not self._external_store:
            self.set_sources(configured_catalogs(self.window_ref.settings))
        self.start(lambda cancel, progress: self.store.check(cancel, progress))

    def set_sources(self, urls: list[str]) -> None:
        if self.busy() or urls == self.store.catalog_urls:
            return
        self.store = PluginStore(self.store.data, self.store.bundled, catalog_urls=urls)
        self.catalog = self.store.cached_catalog()
        self.status.setText(tr("store.cached"))
        self.rebuild()

    def install(self, item: dict) -> None:
        self.start(lambda cancel, progress: self.store.install(item, cancel, progress), mutating=True)

    def install_selected(self, entry: dict) -> None:
        """Install the version picked in one row's drop-down (R-178).

        ``entry["picked"]`` is the combo's current data, so a downgrade and an
        upgrade travel the exact same verified path: ``select_version`` turns
        the choice back into a catalog record (URL + SHA-256 + api) and
        ``PluginStore.install`` re-validates it.  Nothing here trusts the row.
        """
        item = entry["item"]
        version = entry.get("picked") or ""
        try:
            record = select_version(item, version)
            if int(record.get("api", 1)) > API_VERSION:
                raise StoreError("api")
        except StoreError as exc:
            self.failed(exc.code)
            return
        self.start(lambda cancel, progress: self.store.install(record, cancel, progress),
                   mutating=True)

    def confirm_remove(self, item: dict) -> None:
        answer = QMessageBox.question(self, tr("store.title"), tr("store.confirm", name=localized(item.get("name")) or item["id"]),
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.remove(item["id"])

    def remove(self, identifier: str) -> None:
        self.start(lambda _cancel, _progress: self.store.uninstall(identifier), mutating=True)

    def received(self, result) -> None:
        if isinstance(result, list):
            self.catalog = result
            self.status.setText(tr("store.partial", total=len(self.store.catalog_urls), failed=len(self.store.failures))
                                if self.store.failures else tr("store.checked"))
        else:
            self.status.setText(tr("store.done"))

    def failed(self, code: str) -> None:
        self.status.setText(tr("store.error", error=tr("store.err." + code)))

    def _task_finished(self) -> None:
        task = self.task
        self.task = None
        if self._mutating:
            self.window_ref.reload_plugins()
        self._mutating = False
        self.host.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.rebuild()
        if task is not None:
            task.deleteLater()

    def cancel(self) -> None:
        if self.task:
            self.task.cancel.set()

    def reject(self) -> None:
        if self.busy():
            self.cancel()
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self.busy():
            self.cancel()
            event.ignore()
            return
        super().closeEvent(event)
