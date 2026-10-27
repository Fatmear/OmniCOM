"""Quick launcher plugin: an app grid with instance tracking.

What it does
------------
* Add an app by picking an ``.exe`` (icon is extracted from the file itself).
* One click runs it.  The grid shows how many instances are currently running.
* When an app is already running you get a choice: open **one more** window or
  **focus** an existing one (Windows: ``SetForegroundWindow`` on the window whose
  process id matches).
* Everything is stored in the plugin's own ``data_dir``, so the whole folder can
  be copied to another machine and still work.

The plugin only uses the documented ``api`` object - no imports from the host
internals beyond the public helpers.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

STORE = "apps.json"


# --------------------------------------------------------------------------- #
# process helpers (Windows only, degrade gracefully elsewhere)
# --------------------------------------------------------------------------- #
def _windows_processes() -> dict[int, str]:
    """pid -> exe path, using tasklist (no third-party dependency)."""
    if not sys.platform.startswith("win"):
        return {}
    try:
        out = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, encoding="mbcs", errors="replace",
            timeout=6, creationflags=0x08000000)
    except Exception:
        return {}
    mapping: dict[int, str] = {}
    for line in (out.stdout or "").splitlines():
        parts = [part.strip('"') for part in line.split('","')]
        if len(parts) < 2:
            continue
        try:
            mapping[int(parts[1])] = parts[0]
        except ValueError:
            continue
    return mapping


def _running_count(process_names: set[str]) -> int:
    if not process_names:
        return 0
    procs = _windows_processes()
    targets = {name.lower() for name in process_names}
    return sum(1 for name in procs.values() if name.lower() in targets)


def _focus_window(process_name: str) -> bool:
    """Bring the first top-level window owned by ``process_name`` to the front."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        target = process_name.lower()
        found: list[int] = []

        EnumWindowsProc = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            handle = kernel32.OpenProcess(0x1000, False, pid.value)
            if not handle:
                return True
            try:
                size = wintypes.DWORD(1024)
                buffer = ctypes.create_unicode_buffer(1024)
                ok = kernel32.QueryFullProcessImageNameW(
                    handle, 0, buffer, ctypes.byref(size))
                if ok and os.path.basename(buffer.value).lower() == target:
                    found.append(hwnd)
            finally:
                kernel32.CloseHandle(handle)
            return True

        user32.EnumWindows(EnumWindowsProc(callback), 0)
        if not found:
            return False
        hwnd = found[0]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _extract_icon(path: str, size: int = 48) -> QIcon:
    """Icon straight from the executable / shortcut, else a generated tile."""
    icon = QIcon(path)
    if not icon.isNull() and not icon.pixmap(QSize(size, size)).isNull():
        return icon
    return _letter_icon(os.path.splitext(os.path.basename(path))[0], size)


def _letter_icon(name: str, size: int = 48) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    hue = (sum(ord(char) for char in name) * 37) % 360
    color = QColor.fromHsv(hue, 150, 205)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    radius = size * 0.32
    painter.drawRoundedRect(0, 0, size, size, radius, radius)
    painter.setPen(QPen(QColor("#FFFFFF")))
    font = painter.font()
    font.setPixelSize(int(size * 0.5))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter,
                     (name[:1] or "?").upper())
    painter.end()
    return QIcon(pixmap)


# --------------------------------------------------------------------------- #
# the grid tile
# --------------------------------------------------------------------------- #
class AppTile(QPushButton):
    """One app: icon, name, running badge."""

    activated = Signal(dict)          # app dict
    menuRequested = Signal(dict, object)   # app dict, global position

    def __init__(self, app: dict, api, parent=None) -> None:
        super().__init__(parent)
        self.app = app
        self.api = api
        self._running = 0
        self.setObjectName("AppTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setFixedSize(96, 104)
        self.setIconSize(QSize(46, 46))
        self.setIcon(_extract_icon(app["path"]))
        self.setText(app.get("name") or os.path.basename(app["path"]))
        self.setStyleSheet(
            "QPushButton#AppTile { border: 1px solid transparent; border-radius: 12px;"
            " padding: 6px 2px 4px 2px; text-align: center; }"
            "QPushButton#AppTile:hover { background: rgba(18,26,43,12);"
            " border-color: rgba(18,26,43,34); }"
            "QPushButton#AppTile:pressed { background: rgba(18,26,43,22); }")
        self.setToolTip(app["path"])
        self.clicked.connect(lambda: self.activated.emit(self.app))

    def set_running(self, count: int) -> None:
        if count == self._running:
            return
        self._running = count
        self.update()

    def contextMenuEvent(self, event) -> None:
        self.menuRequested.emit(self.app, event.globalPos())

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._running:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#0B7A6E"))
        text = str(self._running) if self._running < 10 else "9+"
        box = 18 if len(text) == 1 else 22
        rect = self.rect()
        painter.drawEllipse(rect.right() - box - 6, 6, box, box)
        painter.setPen(QColor("#FFFFFF"))
        font = painter.font()
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect.right() - box - 6, 6, box, box, Qt.AlignCenter, text)


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
class LauncherPage(QWidget):
    def __init__(self, api) -> None:
        super().__init__()
        self.api = api
        self.apps: list[dict] = self._load()
        self.tiles: list[AppTile] = []
        self._procs: dict[int, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        card = QWidget()
        card.setObjectName("LauncherCard")
        card.setStyleSheet(
            "QWidget#LauncherCard { background: #FFFFFF; border-radius: 12px; }")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 14)
        card_layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.title = QLabel(api.t("快捷启动", "Launcher"))
        self.title.setStyleSheet("font-size: 14px; font-weight: 650;")
        bar.addWidget(self.title)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(
            "color: #46536B; font-size: 11.5px; background: rgba(18,26,43,8);"
            " border-radius: 8px; padding: 2px 9px;")
        bar.addWidget(self.count_label)
        bar.addStretch(1)

        self.btn_add = QPushButton(api.t("添加软件", "Add app"))
        self.btn_add.setObjectName("Primary")
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.clicked.connect(self.add_app)
        bar.addWidget(self.btn_add)

        self.btn_refresh = QPushButton(api.t("刷新状态", "Refresh"))
        self.btn_refresh.setObjectName("Ghost")
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.refresh_running)
        bar.addWidget(self.btn_refresh)
        card_layout.addLayout(bar)

        self.hint = QLabel(api.t(
            "点击图标打开；已在运行时可以选择「再开一个」或「切到已打开的窗口」。右键更多操作。",
            "Click a tile to launch. If it is already running you can open another window or "
            "switch to it. Right-click for more."))
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #5F6A7E; font-size: 11.5px;")
        card_layout.addWidget(self.hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.host = QWidget()
        self.grid = QGridLayout(self.host)
        self.grid.setContentsMargins(2, 6, 2, 6)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        scroll.setWidget(self.host)
        card_layout.addWidget(scroll, 1)

        root.addWidget(card, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_running)
        self._timer.start(4000)

        api.on_language(self._retranslate)
        self._rebuild()
        self.refresh_running()

    # ------------------------------------------------------------------ i18n
    def _retranslate(self, _lang: str) -> None:
        self.title.setText(self.api.t("快捷启动", "Launcher"))
        self.btn_add.setText(self.api.t("添加软件", "Add app"))
        self.btn_refresh.setText(self.api.t("刷新状态", "Refresh"))
        self.hint.setText(self.api.t(
            "点击图标打开；已在运行时可以选择「再开一个」或「切到已打开的窗口」。右键更多操作。",
            "Click a tile to launch. If it is already running you can open another window or "
            "switch to it. Right-click for more."))
        self._update_count()

    # --------------------------------------------------------------- storage
    def _store_path(self) -> str:
        return os.path.join(self.api.data_dir, STORE)

    def _load(self) -> list[dict]:
        try:
            with open(self._store_path(), encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict) and item.get("path")]
        except (OSError, json.JSONDecodeError):
            pass
        return []

    def _save(self) -> None:
        try:
            with open(self._store_path(), "w", encoding="utf-8") as handle:
                json.dump(self.apps, handle, ensure_ascii=False, indent=2)
        except OSError as exc:
            self.api.notify(f"{exc}", "error")

    # ------------------------------------------------------------------ grid
    def _rebuild(self) -> None:
        for tile in self.tiles:
            tile.setParent(None)
            tile.deleteLater()
        self.tiles.clear()

        if not self.apps:
            empty = QLabel(self.api.t(
                "还没有添加软件。点右上角「添加软件」选择一个 .exe 或快捷方式。",
                "Nothing added yet. Use “Add app” to pick an .exe or a shortcut."))
            empty.setStyleSheet("color: #5F6A7E; font-size: 12px; padding: 18px;")
            self.grid.addWidget(empty, 0, 0)
            self.tiles.append(empty)
            self._update_count()
            return

        columns = max(1, self.host.width() // 104) if self.host.width() else 6
        for index, app in enumerate(self.apps):
            tile = AppTile(app, self.api, self.host)
            tile.activated.connect(self.launch)
            tile.menuRequested.connect(self._context_menu)
            self.grid.addWidget(tile, index // columns, index % columns)
            self.tiles.append(tile)
        self._update_count()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        columns = max(1, (self.host.width() or self.width()) // 104)
        if getattr(self, "_columns", None) == columns or not self.apps:
            self._columns = columns
            return
        self._columns = columns
        self._rebuild()

    def _update_count(self) -> None:
        running = sum(1 for app in self.apps if self._running_of(app))
        self.count_label.setText(self.api.t(
            f"{len(self.apps)} 个应用 · 运行中 {running}",
            f"{len(self.apps)} apps · {running} running"))

    # ------------------------------------------------------------------ state
    @staticmethod
    def _processes_of(app: dict) -> set[str]:
        names = app.get("processes")
        if names:
            return set(names)
        base = os.path.basename(str(app.get("path") or ""))
        if base:
            return {base}
        return set()

    def _running_of(self, app: dict) -> int:
        names = self._processes_of(app)
        if not names:
            return 0
        lowered = {name.lower() for name in names}
        return sum(1 for name in self._procs.values() if name.lower() in lowered)

    def refresh_running(self) -> None:
        procs = _windows_processes()
        if procs or not self._procs:
            self._procs = procs
        for tile in self.tiles:
            if isinstance(tile, AppTile):
                tile.set_running(self._running_of(tile.app))
        self._update_count()

    # ---------------------------------------------------------------- launch
    def add_app(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.api.t("选择要添加的程序", "Pick an application"),
            "", self.api.t("程序 (*.exe *.bat *.cmd *.lnk);;所有文件 (*)",
                           "Programs (*.exe *.bat *.cmd *.lnk);;All files (*)"))
        if not path:
            return
        self.apps.append({
            "path": path,
            "name": os.path.splitext(os.path.basename(path))[0],
            "args": "",
            "cwd": os.path.dirname(path),
            "processes": [os.path.basename(path)],
            "added": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self._save()
        self._rebuild()
        self.refresh_running()
        self.api.notify(self.api.t(f"已添加 {os.path.basename(path)}",
                                   f"Added {os.path.basename(path)}"), "ok")

    def launch(self, app: dict, force_new: bool = False) -> None:
        running = self._running_of(app)
        if running and not force_new:
            self._ask_instance(app, running)
            return
        self._spawn(app)

    def _spawn(self, app: dict) -> None:
        path = app["path"]
        args = app.get("args") or ""
        cwd = app.get("cwd") or os.path.dirname(path)
        try:
            if path.lower().endswith((".bat", ".cmd")):
                subprocess.Popen(["cmd", "/c", path, *args.split()], cwd=cwd)
            else:
                command = [path, *args.split()] if args else [path]
                subprocess.Popen(command, cwd=cwd, close_fds=True)
        except Exception as exc:
            self.api.notify(self.api.t(f"打开失败：{exc}", f"Failed to launch: {exc}"), "error")
            return
        self.api.notify(self.api.t(f"已打开 {app.get('name')}", f"Launched {app.get('name')}"),
                        "ok")
        # Poll harder right after launching so the badge appears immediately.
        for delay in (350, 900, 1800):
            QTimer.singleShot(delay, self.refresh_running)

    def _ask_instance(self, app: dict, running: int) -> None:
        menu = QMenu(self)
        label = self.api.t(f"{app.get('name')} 已运行 {running} 个实例",
                           f"{app.get('name')} is already running ({running})")
        header = menu.addAction(label)
        header.setEnabled(False)
        menu.addSeparator()
        open_new = menu.addAction(self.api.t("再打开一个新窗口", "Open another window"))
        focus = menu.addAction(self.api.t("切换到已打开的窗口", "Switch to the open window"))
        chosen = menu.exec(self.cursor().pos())
        if chosen is focus:
            names = self._processes_of(app)
            for name in names:
                if _focus_window(name):
                    self.api.notify(self.api.t(f"已切换到 {app.get('name')}",
                                               f"Switched to {app.get('name')}"), "ok")
                    return
            self.api.notify(self.api.t("没找到可切换的窗口，已为你打开新的",
                                       "No focusable window found - opening a new one"), "warn")
            self._spawn(app)
        elif chosen is open_new:
            self._spawn(app)

    # ---------------------------------------------------------------- context
    def _context_menu(self, app: dict, position) -> None:
        menu = QMenu(self)
        launch = menu.addAction(self.api.t("打开", "Open"))
        focus = menu.addAction(self.api.t("切换到已打开窗口", "Switch to window"))
        menu.addSeparator()
        rename = menu.addAction(self.api.t("重命名…", "Rename…"))
        locate = menu.addAction(self.api.t("打开所在文件夹", "Show in folder"))
        menu.addSeparator()
        remove = menu.addAction(self.api.t("从列表移除", "Remove from list"))
        chosen = menu.exec(position)
        if chosen is launch:
            self.launch(app, force_new=True)
        elif chosen is focus:
            for name in self._processes_of(app):
                if _focus_window(name):
                    return
            self.api.notify(self.api.t("该程序当前没有可切换的窗口",
                                       "That app has no window to switch to"), "warn")
        elif chosen is locate:
            with contextlib.suppress(Exception):
                subprocess.Popen(["explorer", "/select,", app["path"]])
        elif chosen is remove:
            self.apps = [item for item in self.apps if item is not app]
            self._save()
            self._rebuild()
        elif chosen is rename:
            self._rename(app)
        self.refresh_running()

    def _rename(self, app: dict) -> None:
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(
            self, self.api.t("重命名", "Rename"), self.api.t("名称", "Name"),
            text=str(app.get("name") or ""))
        if ok and text.strip():
            app["name"] = text.strip()
            self._save()
            self._rebuild()


def create(api):
    """Plugin entry point."""
    return LauncherPage(api)
