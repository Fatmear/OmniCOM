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

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPen, QPixmap
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
TILE_W = 112
ICON_PX = 46
TILE_PAD = 8
TEXT_TOP = TILE_PAD + ICON_PX + 6
TEXT_LINES_MAX = 3


def _window_key(window: dict) -> str:
    """Stable key for one OS window (survives list refreshes)."""
    return f"{window.get('pid')}:{window.get('hwnd')}"


class AppTile(QWidget):
    """One icon: either a saved shortcut or one running window.

    The caption is painted with word wrap, so a long name makes the tile taller
    instead of being covered by the icon or elided to nothing.  Dragging a tile
    is reported to the page, which owns the ordering.
    """

    clicked = Signal(dict)
    menuRequested = Signal(dict, object)
    dragMoved = Signal(object, object)
    dragFinished = Signal(object, object)

    def __init__(self, app: dict, api, title: str, parent=None,
                 instance: dict | None = None, kind: str = "shortcut") -> None:
        super().__init__(parent)
        self.app = app
        self.api = api
        self.title = title
        self.instance = instance
        self.kind = kind
        self._running = 0
        self._hover = False
        self._press = False
        self._drag_from = None
        self._dragging = False
        self.setObjectName("AppTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        # Pin the caption font before measuring: the height computed at
        # construction must match the font used when painting, otherwise the
        # measured wrap height and the real one disagree.
        font = self.font()
        font.setPixelSize(11)
        self.setFont(font)
        self._icon = _extract_icon(app["path"])
        details = [title, instance["title"] if instance else "",
                   instance["path"] if instance else app["path"]]
        self.setToolTip("\n".join(dict.fromkeys(item for item in details if item)))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(TILE_W, self._height_for(title))
        self.set_running(1 if instance else 0)

    # -- geometry ---------------------------------------------------------
    def _text_rect(self) -> QRect:
        return QRect(TILE_PAD, TEXT_TOP, TILE_W - 2 * TILE_PAD,
                     max(14, self.height() - TEXT_TOP - 4))

    def _wrapped_height(self, title: str) -> int:
        metrics = QFontMetrics(self.font())
        bounds = metrics.boundingRect(
            QRect(0, 0, TILE_W - 2 * TILE_PAD, 600),
            Qt.TextWordWrap | Qt.AlignHCenter, title or "")
        return min(max(bounds.height(), metrics.lineSpacing()),
                   metrics.lineSpacing() * TEXT_LINES_MAX)

    def _height_for(self, title: str) -> int:
        return TEXT_TOP + self._wrapped_height(title) + 6

    def sizeHint(self) -> QSize:
        return QSize(TILE_W, self.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(TILE_W, self.height())

    def set_running(self, count: int) -> None:
        if count == self._running:
            return
        self._running = count
        self.update()

    def set_title(self, title: str) -> None:
        self.title = title
        self.setFixedSize(TILE_W, self._height_for(title))
        self.updateGeometry()
        self.update()

    # -- interaction ------------------------------------------------------
    def _target(self) -> dict:
        return {**self.app, "_window": self.instance} if self.instance else self.app

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = self._press = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press = True
            self._drag_from = event.position().toPoint()
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is None:
            return
        if not self._dragging:
            moved = (event.position().toPoint() - self._drag_from).manhattanLength()
            if moved < 8:
                return
            self._dragging = True
        self.dragMoved.emit(self, event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        dragged = self._dragging
        self._dragging = False
        self._drag_from = None
        self._press = False
        self.update()
        if dragged:
            self.dragFinished.emit(self, event.globalPosition().toPoint())
        elif self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self._target())

    def contextMenuEvent(self, event) -> None:
        self.menuRequested.emit(self._target(), event.globalPos())

    # -- painting ---------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self._hover or self._press:
            painter.setPen(QPen(QColor(18, 26, 43, 60 if self._press else 34), 1))
            painter.setBrush(QColor(18, 26, 43, 30 if self._press else 12))
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)
        elif self.property("dropTarget"):
            painter.setPen(QPen(QColor("#0B7A6E"), 2, Qt.DashLine))
            painter.setBrush(QColor(11, 122, 110, 18))
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 12, 12)
        painter.drawPixmap((TILE_W - ICON_PX) // 2, TILE_PAD,
                           self._icon.pixmap(QSize(ICON_PX, ICON_PX)))

        font = painter.font()
        font.setPixelSize(11)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        painter.setPen(QColor("#121A2B"))
        text = self.title or ""
        rect = self._text_rect()
        wrapped = metrics.boundingRect(rect, Qt.TextWordWrap | Qt.AlignHCenter, text)
        if wrapped.height() > rect.height():
            # Only an unreasonably long name loses its tail; the tooltip keeps
            # the whole text, so nothing is silently hidden.
            flags = Qt.AlignHCenter | Qt.AlignTop
            text = metrics.elidedText(text, Qt.ElideRight, rect.width())
        else:
            flags = Qt.TextWordWrap | Qt.AlignHCenter | Qt.AlignTop
        painter.drawText(rect, flags, text)
        if self._running:
            self._paint_badge(painter)

    def _paint_badge(self, painter: QPainter) -> None:
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
        painter.setPen(QColor("#121A2B"))


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
            "上排是快捷方式，点一下就会新开一个窗口；下排是正在运行的窗口，点一下切过去。"
            "两排都支持拖拽排序，右键可以重命名或移除。",
            "The top row holds shortcuts - click one to open a new window. The bottom row lists "
            "running windows - click one to switch to it. Both rows can be reordered by "
            "dragging; right-click to rename or remove."))
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #5F6A7E; font-size: 11.5px;")
        card_layout.addWidget(self.hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.host = QWidget()
        host_layout = QVBoxLayout(self.host)
        host_layout.setContentsMargins(2, 6, 2, 6)
        host_layout.setSpacing(4)

        self.sec_shortcuts = self._section_label(api.t("快捷启动", "Shortcuts"))
        host_layout.addWidget(self.sec_shortcuts)
        self.shortcut_host = QWidget()
        self.shortcut_grid = QGridLayout(self.shortcut_host)
        self.shortcut_grid.setContentsMargins(0, 0, 0, 0)
        self.shortcut_grid.setSpacing(8)
        self.shortcut_grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        host_layout.addWidget(self.shortcut_host)

        self.sec_running = self._section_label(api.t("正在运行", "Running"))
        host_layout.addWidget(self.sec_running)
        self.running_host = QWidget()
        self.running_grid = QGridLayout(self.running_host)
        self.running_grid.setContentsMargins(0, 0, 0, 0)
        self.running_grid.setSpacing(8)
        self.running_grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.running_none = self._section_label(api.t("当前没有匹配的窗口", "No matching windows"))
        self.running_none.setStyleSheet("color: #5F6A7E; font-size: 11.5px; padding: 4px 2px;")
        host_layout.addWidget(self.running_host)
        host_layout.addWidget(self.running_none)
        host_layout.addStretch(1)

        # Older callers/hints referred to `grid`; the shortcut grid is it.
        self.grid = self.shortcut_grid
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
    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #46536B; font-size: 11.5px; font-weight: 650;"
                            " padding: 6px 2px 2px 2px;")
        return label

    def _retranslate(self, _lang: str) -> None:
        self.title.setText(self.api.t("快捷启动", "Launcher"))
        self.btn_add.setText(self.api.t("添加软件", "Add app"))
        self.hint.setText(self.api.t(
            "上排是快捷方式，点一下就会新开一个窗口；下排是正在运行的窗口，点一下切过去。"
            "两排都支持拖拽排序，右键可以重命名或移除。",
            "The top row holds shortcuts - click one to open a new window. The bottom row lists "
            "running windows - click one to switch to it. Both rows can be reordered by "
            "dragging; right-click to rename or remove."))
        self.sec_shortcuts.setText(self.api.t("快捷启动", "Shortcuts"))
        self.sec_running.setText(self.api.t("正在运行", "Running"))
        self.running_none.setText(self.api.t("当前没有匹配的窗口", "No matching windows"))
        self._update_count()
        self._rebuild()

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
                    item.setdefault("aliases", {})
                    item.setdefault("alias_pool", [])
                    item.setdefault("window_order", [])
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
    def _columns_now(self) -> int:
        width = self.host.width() or self.width() or 640
        return max(3, width // (TILE_W + 10))

    @staticmethod
    def _clear_grid(grid: QGridLayout) -> None:
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _bind(self, tile: AppTile) -> None:
        tile.clicked.connect(self.launch)
        tile.menuRequested.connect(self._context_menu)
        tile.dragMoved.connect(self._on_drag_moved)
        tile.dragFinished.connect(self._on_drag_finished)

    def _rebuild(self) -> None:
        for tile in self.tiles:
            tile.setParent(None)
            tile.deleteLater()
        self.tiles.clear()
        self._clear_grid(self.shortcut_grid)
        self._clear_grid(self.running_grid)

        columns = self._columns_now()
        for index, app in enumerate(self.apps):
            tile = AppTile(app, self.api, app.get("name") or os.path.basename(app["path"]),
                           self.shortcut_host, kind="shortcut")
            self._bind(tile)
            tile.set_running(self._running_of(app))
            self.shortcut_grid.addWidget(tile, index // columns, index % columns)
            self.tiles.append(tile)
        self._empty_hint = None

        index = 0
        for app in self.apps:
            for ordinal, instance in enumerate(self._ordered_windows(app)):
                title, _aliased = self._instance_title(app, instance, ordinal)
                tile = AppTile(app, self.api, title, self.running_host,
                               instance=instance, kind="running")
                self._bind(tile)
                self.running_grid.addWidget(tile, index // columns, index % columns)
                self.tiles.append(tile)
                index += 1
        self.running_host.setVisible(index > 0)
        self.running_none.setVisible(index == 0)
        self._update_count()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        columns = self._columns_now()
        if getattr(self, "_columns", None) == columns:
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
        running = sum(self._running_of(app) for app in self.apps)
        self.count_label.setText(self.api.t(
            f"{len(self.apps)} 个快捷方式 · {running} 个窗口",
            f"{len(self.apps)} shortcuts · {running} windows"))

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

    def _ordered_windows(self, app: dict) -> list[dict]:
        """User order first (``window_order``), then stable enumeration order."""
        windows = self._windows_of(app)
        rank = {key: index for index, key in
                enumerate(app.get("window_order") or [])}
        return sorted(windows, key=lambda window: (
            rank.get(_window_key(window), len(rank)),
            window.get("pid") or 0, window.get("hwnd") or 0))

    def _instance_title(self, app: dict, instance: dict, ordinal: int = 0) -> tuple[str, bool]:
        """Caption for one window: its own alias, a spare alias, else the OS title."""
        aliases = app.get("aliases") or {}
        own = aliases.get(_window_key(instance))
        if own:
            return own, True
        others = {aliases.get(_window_key(window)) for window in self._windows_of(app)
                  if _window_key(window) != _window_key(instance)}
        for name in app.get("alias_pool") or []:
            if name and name not in others:
                return name, True
        return instance.get("title") or app.get("name") or "", False

    # ------------------------------------------------------------------ drag
    def _on_drag_moved(self, tile: AppTile, global_pos) -> None:
        """Highlight only: the drop target is resolved once the mouse is released."""
        target = self._drop_target(tile, global_pos)
        for item in self.tiles:
            if item.kind == tile.kind:
                item.setProperty("dropTarget", item is target)
                item.update()

    def _drop_target(self, tile: AppTile, global_pos):
        group = [item for item in self.tiles if item.kind == tile.kind]
        if not group:
            return None
        point = self.mapFromGlobal(global_pos)

        def distance(item: AppTile) -> int:
            center = item.mapTo(self, item.rect().center())
            return (center - point).manhattanLength()

        return min(group, key=distance)

    def _on_drag_finished(self, tile: AppTile, global_pos) -> None:
        target = self._drop_target(tile, global_pos)
        for item in self.tiles:
            if item.kind == tile.kind:
                item.setProperty("dropTarget", False)
                item.update()
        if target is None or target is tile:
            return
        if tile.kind == "shortcut":
            self._move_app(tile, target)
        else:
            self._move_window(tile, target)

    def _move_app(self, source: AppTile, target: AppTile) -> None:
        try:
            start = self.apps.index(source.app)
            end = self.apps.index(target.app)
        except ValueError:
            return
        if start == end:
            return
        item = self.apps.pop(start)
        self.apps.insert(end, item)
        self._save()
        self._rebuild()

    def _move_window(self, source: AppTile, target: AppTile) -> None:
        app = target.app
        if app is not source.app:
            # Cross-app drags would silently re-parent a live window; ignore.
            return
        order = [_window_key(window) for window in self._ordered_windows(app)]
        source_key = _window_key(source.instance)
        target_key = _window_key(target.instance)
        if source_key == target_key or source_key not in order or target_key not in order:
            return
        order.remove(source_key)
        order.insert(order.index(target_key), source_key)
        app["window_order"] = order
        self._save()
        self._rebuild()

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
            "aliases": {},
            "alias_pool": [],
            "window_order": [],
            "added": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self._save()
        self._rebuild()
        self.refresh_running()
        self.api.notify(self.api.t(f"已添加 {os.path.basename(path)}",
                                   f"Added {os.path.basename(path)}"), "ok")

    def launch(self, payload: dict, force_new: bool = False) -> None:
        """A shortcut click always opens a new window; a window tile switches to it."""
        if payload.get("_window") and not force_new:
            self._activate_window(payload["_window"])
            return
        self._spawn(payload)

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
        # Poll harder right after launching so the new tile shows up immediately.
        for delay in (350, 900, 1800):
            QTimer.singleShot(delay, self.refresh_running)

    def _activate_window(self, instance: dict) -> None:
        if not _focus_window(instance):
            self.api.notify(self.api.t("窗口已关闭或无法切换，请等待列表刷新",
                                       "Window closed or unavailable. Wait for the list to refresh."),
                            "warn")
            self.refresh_running()

    def _end_instance(self, instance: dict) -> None:
        """Bottom-row delete: close *that window*, never touch the saved shortcut."""
        title = instance.get("title") or ""
        if _close_window(instance):
            self.api.notify(self.api.t(f"已请求关闭「{title}」", f"Asked “{title}” to close"), "ok")
        else:
            self.api.notify(self.api.t("无法结束该窗口，可能已经关闭",
                                       "Could not end that window - it may already be gone"),
                            "warn")
        for delay in (300, 900, 1600):
            QTimer.singleShot(delay, self.refresh_running)

    # ---------------------------------------------------------------- context
    def _context_menu(self, payload: dict, position) -> None:
        instance = payload.get("_window")
        app = next((item for item in self.apps if item["id"] == payload.get("id")), None)
        if app is None:
            return
        menu = QMenu(self)
        if instance is None:
            launch = menu.addAction(self.api.t("打开一个新窗口", "Open a new window"))
            rename = menu.addAction(self.api.t("重命名…", "Rename…"))
            locate = menu.addAction(self.api.t("打开所在文件夹", "Show in folder"))
            menu.addSeparator()
            remove = menu.addAction(self.api.t("从列表移除（不关闭已开窗口）",
                                              "Remove from list (leaves windows open)"))
            chosen = menu.exec(position)
            if chosen is launch:
                self._spawn(app)
            elif chosen is rename:
                self._rename(app)
            elif chosen is locate:
                with contextlib.suppress(Exception):
                    subprocess.Popen(["explorer", "/select,", app["path"]])
            elif chosen is remove:
                self._remove_app(app)
            self.refresh_running()
            return

        activate = menu.addAction(self.api.t("切换到这个窗口", "Switch to this window"))
        rename = menu.addAction(self.api.t("重命名此窗口…", "Rename this window…"))
        locate = menu.addAction(self.api.t("打开所在文件夹", "Show in folder"))
        menu.addSeparator()
        end = menu.addAction(self.api.t("结束这个窗口（进程）", "End this window (process)"))
        chosen = menu.exec(position)
        if chosen is activate:
            self._activate_window(instance)
        elif chosen is rename:
            self._rename_instance(app, instance)
        elif chosen is locate:
            with contextlib.suppress(Exception):
                subprocess.Popen(["explorer", "/select,", instance.get("path") or app["path"]])
        elif chosen is end:
            self._end_instance(instance)
        self.refresh_running()

    def _remove_app(self, app: dict) -> None:
        self.apps = [item for item in self.apps if item["id"] != app["id"]]
        self._save()
        self._rebuild()

    def _rename(self, app: dict) -> None:
        """Rename the shortcut itself (top row)."""
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

    def _rename_instance(self, app: dict, instance: dict) -> None:
        """Rename one *window* (bottom row), so two Notepads read as A and B."""
        from PySide6.QtWidgets import QInputDialog

        current, _aliased = self._instance_title(app, instance)
        text, ok = QInputDialog.getText(
            self, self.api.t("重命名窗口", "Rename window"),
            self.api.t("这个窗口显示的名称", "Name shown for this window"),
            text=str(current or ""))
        if not (ok and text.strip()):
            return
        name = text.strip()
        app.setdefault("aliases", {})[_window_key(instance)] = name
        pool = app.setdefault("alias_pool", [])
        if name not in pool:
            pool.append(name)
        self._save()
        self._rebuild()


def _close_window(window: dict) -> bool:
    """Ask one window to close (WM_CLOSE). Never kills the whole process tree."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                    ctypes.POINTER(wintypes.DWORD)]
        user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint,
                                        wintypes.WPARAM, wintypes.LPARAM]
        hwnd = int(window.get("hwnd") or 0)
        if not hwnd or not user32.IsWindow(hwnd):
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if window.get("pid") is not None and pid.value != window["pid"]:
            # The handle now belongs to someone else - never close a stranger.
            return False
        user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
        return True
    except Exception:
        return False


def create(api):
    """Plugin entry point."""
    return LauncherPage(api)
