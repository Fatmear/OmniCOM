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
import threading
import time
import uuid

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
    QSizePolicy,
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


def _windows_windows(processes: dict[int, str]) -> list[dict]:
    if not sys.platform.startswith("win"):
        return []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetWindow.restype = wintypes.HWND
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        found = []

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in processes:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, len(title))
            path = processes[pid.value]
            handle = kernel32.OpenProcess(0x1000, False, pid.value)
            if handle:
                try:
                    size = wintypes.DWORD(32768)
                    buffer = ctypes.create_unicode_buffer(size.value)
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                        path = buffer.value
                finally:
                    kernel32.CloseHandle(handle)
            found.append({"hwnd": int(hwnd), "pid": pid.value, "path": path,
                          "title": title.value})
            return True

        user32.EnumWindows(callback_type(callback), 0)
        return sorted(found, key=lambda window: (window["pid"], window["hwnd"]))
    except Exception:
        return []


def _focus_window(window: dict) -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        hwnd = int(window["hwnd"])
        pid = wintypes.DWORD()
        if not user32.IsWindow(hwnd):
            return False
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != window["pid"]:
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        if user32.SetForegroundWindow(hwnd) or user32.GetForegroundWindow() == hwnd:
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        thread = kernel32.GetCurrentThreadId()
        foreground = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        attached = foreground != thread and user32.AttachThreadInput(thread, foreground, True)
        try:
            user32.BringWindowToTop(hwnd)
            return bool(user32.SetForegroundWindow(hwnd) or user32.GetForegroundWindow() == hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(thread, foreground, False)
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
TILE_W = 96
TILE_H = 104


class AppTile(QPushButton):
    """One app: icon, name, running badge."""

    activated = Signal(dict)          # app dict
    menuRequested = Signal(dict, object)   # app dict, global position

    def __init__(self, app: dict, api, parent=None, instance: dict | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.api = api
        self.instance = instance
        self._running = 0
        self.setObjectName("AppTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setIconSize(QSize(46, 46))
        self.setIcon(_extract_icon(app["path"]))
        self.setText(app.get("name") or os.path.basename(app["path"]))
        self.setStyleSheet(
            "QPushButton#AppTile { border: 1px solid transparent; border-radius: 12px;"
            " padding: 6px 2px 4px 2px; text-align: center; }"
            "QPushButton#AppTile:hover { background: rgba(18,26,43,12);"
            " border-color: rgba(18,26,43,34); }"
            "QPushButton#AppTile:pressed { background: rgba(18,26,43,22); }")
        # The QSS below sets min-height/padding, and QStyleSheetStyle applies it
        # *after* this constructor - it silently shrank the tile to its 58 px
        # size hint so the icon and label did not fit (L-053).  A hard sizeHint
        # override is the one thing the layout cannot take back.
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(TILE_W, TILE_H)
        self.setToolTip(f"{instance['title']}\n{app['path']}" if instance else app["path"])
        self.clicked.connect(lambda: self.activated.emit(self._target()))

    def _target(self) -> dict:
        return {**self.app, "_window": self.instance} if self.instance else self.app

    def sizeHint(self) -> QSize:
        return QSize(TILE_W, TILE_H)

    def minimumSizeHint(self) -> QSize:
        return QSize(TILE_W, TILE_H)

    def set_running(self, count: int) -> None:
        if count == self._running:
            return
        self._running = count
        self.update()

    def contextMenuEvent(self, event) -> None:
        self.menuRequested.emit(self._target(), event.globalPos())

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
    processesReady = Signal(dict)

    def __init__(self, api) -> None:
        super().__init__()
        self.api = api
        self.apps: list[dict] = self._load()
        self.tiles: list[AppTile] = []
        self._procs: dict[int, str] = {}
        self._windows: list[dict] = []
        self._refreshing = False
        self.processesReady.connect(self._apply_processes)

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

        # No manual "Refresh" button: the tile badges are refreshed every 4 s
        # anyway, so the button was a duplicate control (R-044).
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
                apps = [item for item in raw if isinstance(item, dict) and item.get("path")]
                for item in apps:
                    item.setdefault("id", uuid.uuid4().hex)
                return apps
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

        width = self.host.width() or self.width() or 640
        columns = max(3, width // (TILE_W + 10))
        entries = [(app, instance) for app in self.apps
                   for instance in (self._windows_of(app) or [None])]
        for index, (app, instance) in enumerate(entries):
            tile = AppTile(app, self.api, self.host, instance=instance)
            tile.activated.connect(self.launch)
            tile.menuRequested.connect(self._context_menu)
            tile.set_running(1 if instance else 0)
            self.grid.addWidget(tile, index // columns, index % columns)
            self.tiles.append(tile)
        self._update_count()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = self.host.width() or self.width() or 640
        columns = max(3, width // (TILE_W + 10))
        if getattr(self, "_columns", None) == columns or not self.apps:
            self._columns = columns
            return
        self._columns = columns
        self._rebuild()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_shown", False):
            self._shown = True
            self._columns = None
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
        return len(self._windows_of(app))

    def _windows_of(self, app: dict) -> list[dict]:
        names = {name.lower() for name in self._processes_of(app)}
        return [window for window in self._windows
                if os.path.basename(window["path"]).lower() in names]

    def refresh_running(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        threading.Thread(target=self._scan_processes, name="omnicom-processes",
                         daemon=True).start()

    def _scan_processes(self) -> None:
        procs = _windows_processes()
        windows = _windows_windows(procs)
        with contextlib.suppress(RuntimeError):
            self.processesReady.emit({"processes": procs, "windows": windows})

    def _apply_processes(self, snapshot: dict) -> None:
        self._refreshing = False
        self._procs = snapshot["processes"]
        windows = snapshot["windows"]
        changed = windows != self._windows
        self._windows = windows
        if changed:
            self._rebuild()
        else:
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
            "id": uuid.uuid4().hex,
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
        if app.get("_window") and not force_new:
            self._activate_window(app["_window"])
            return
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
        actions = {menu.addAction(instance["title"]): instance
                   for instance in self._windows_of(app)}
        chosen = menu.exec(self.cursor().pos())
        if chosen in actions:
            self._activate_window(actions[chosen])
        elif chosen is open_new:
            self._spawn(app)

    def _activate_window(self, instance: dict) -> None:
        if not _focus_window(instance):
            self.api.notify(self.api.t("窗口已关闭或无法切换，请等待列表刷新",
                                       "Window closed or unavailable. Wait for the list to refresh."), "warn")
            self.refresh_running()

    # ---------------------------------------------------------------- context
    def _context_menu(self, app: dict, position) -> None:
        instance = app.get("_window")
        app = next((item for item in self.apps if item["id"] == app.get("id")), None)
        if app is None:
            return
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
            if instance:
                self._activate_window(instance)
            elif self._windows_of(app):
                self._ask_instance(app, self._running_of(app))
            else:
                self.api.notify(self.api.t("该程序当前没有可切换的窗口",
                                           "That app has no window to switch to"), "warn")
        elif chosen is locate:
            with contextlib.suppress(Exception):
                subprocess.Popen(["explorer", "/select,", app["path"]])
        elif chosen is remove:
            self.apps = [item for item in self.apps if item["id"] != app["id"]]
            self._save()
            self._rebuild()
        elif chosen is rename:
            self._rename(app)
        self.refresh_running()

    def _rename(self, app: dict) -> None:
        from PySide6.QtWidgets import QInputDialog

        # getText(parent, title, label, echo, text, ...) - passing the current
        # name positionally landed in `echo`, which Qt rejects, so the dialog
        # never appeared (L-054).
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
