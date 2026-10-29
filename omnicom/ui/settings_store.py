"""Where the UI keeps its state: INI files on disk, never the registry.

Two kinds of state, two files:

``<data>/settings.ini``
    Preferences shared by every window and every launch: accent colour,
    wallpaper, language, capture folder, log budget, serial defaults.  Set the
    accent once and every window shows it.

``<data>/window-<n>.ini``
    State that belongs to *one* window: geometry, splitter positions, sidebar
    visibility, send-box height.  Each running process reserves a slot ``n`` with
    a locked ``window-<n>.lock`` file, so several OmniCOM windows run fully
    independently - closing one writes only its own file, and one window can
    never move, resize or hide the parts of another.

Why not the registry (R-088): ``HKCU\\Software\\OmniCOM`` is machine-wide and
survives deletion of the program folder, so a freshly unzipped portable copy
inherited whatever the previously installed run had set - the user's test
wallpaper and accent came back on a brand-new download (L-121).  Files beside the
data folder also make the portable folder self-contained: copy it to a USB stick
and both the program and its settings travel with it.
"""

from __future__ import annotations

import contextlib
import os

from PySide6.QtCore import QSettings

from .. import paths

SETTINGS_NAME = "settings"
WINDOW_NAME = "window"
#: Upper bound on simultaneously reserved window slots; beyond this a window
#: falls back to a pid-named file instead of sharing somebody else's state.
MAX_WINDOWS = 32

#: Keys that describe a *preference* rather than one window: they live in the
#: shared file so every window and the next launch sees the same value.
SHARED_KEYS = ("ui/accent", "ui/wallpaper", "ui/language")
SHARED_PREFIXES = ("paths/", "serial/", "display/")

SETTINGS_APP = "OmniCOM"


def suffix() -> str:
    """Test runs get their own files (``OMNICOM_SETTINGS_SUFFIX``)."""
    return os.environ.get("OMNICOM_SETTINGS_SUFFIX", "")


class _Slot:
    """A locked file that reserves one window slot for this process.

    The lock is advisory and released by the OS when the process ends, so a
    crashed window never blocks its slot forever - unlike a pid file.
    """

    def __init__(self, index: int, handle, path: str) -> None:
        self.index = index
        self._handle = handle
        self._path = path

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        _unlock(handle)
        with contextlib.suppress(OSError):
            handle.close()


def _try_lock(handle) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def claim_slot(folder: str) -> _Slot:
    """Reserve the lowest free window slot, or a pid-named one as a last resort."""
    tag = suffix()
    for index in range(1, MAX_WINDOWS + 1):
        path = os.path.join(folder, f"{WINDOW_NAME}-{index}{tag}.lock")
        try:
            handle = open(path, "a+b")  # noqa: SIM115 - held for the process lifetime
        except OSError:
            continue
        if _try_lock(handle):
            return _Slot(index, handle, path)
        handle.close()
    path = os.path.join(folder, f"{WINDOW_NAME}-pid{os.getpid()}{tag}.lock")
    handle = open(path, "a+b")  # noqa: SIM115
    _try_lock(handle)
    return _Slot(MAX_WINDOWS + 1, handle, path)


class UiSettings:
    """``QSettings``-compatible facade over the shared and per-window files.

    Everything that already talks to ``window.settings`` keeps working: the
    routing behind ``value``/``setValue`` is the only difference.
    """

    def __init__(self, shared: QSettings, local: QSettings, slot: _Slot) -> None:
        self._shared = shared
        self._local = local
        self.slot = slot

    # ------------------------------------------------------------- routing
    @staticmethod
    def is_shared(key: object) -> bool:
        name = str(key)
        return name in SHARED_KEYS or name.startswith(SHARED_PREFIXES)

    def _store(self, key: object) -> QSettings:
        return self._shared if self.is_shared(key) else self._local

    # ------------------------------------------------------------ QSettings
    def value(self, key: object, defaultValue=None, type=None):
        store = self._store(key)
        if type is None:
            return store.value(key, defaultValue)
        return store.value(key, defaultValue, type)

    def setValue(self, key: object, value) -> None:
        self._store(key).setValue(key, value)

    def remove(self, key: object) -> None:
        self._store(key).remove(key)

    def contains(self, key: object) -> bool:
        return self._store(key).contains(key)

    def allKeys(self) -> list[str]:
        keys = list(self._shared.allKeys())
        keys += [key for key in self._local.allKeys() if key not in keys]
        return keys

    def sync(self) -> None:
        self._shared.sync()
        self._local.sync()

    def fileName(self) -> str:
        return self._local.fileName()

    def sharedFileName(self) -> str:
        return self._shared.fileName()

    def format(self):
        return QSettings.IniFormat

    def applicationName(self) -> str:
        return f"{SETTINGS_APP}{suffix()}"

    # ------------------------------------------------------------- lifetime
    def release(self) -> None:
        """Flush and give the window slot back (called from ``closeEvent``)."""
        self.sync()
        self.slot.release()


def open_settings() -> UiSettings:
    """Open the shared + per-window stores under the data folder."""
    folder = paths.data_dir()
    os.makedirs(folder, exist_ok=True)
    tag = suffix()
    shared = QSettings(os.path.join(folder, f"{SETTINGS_NAME}{tag}.ini"),
                       QSettings.IniFormat)
    slot = claim_slot(folder)
    local = QSettings(os.path.join(folder, f"{WINDOW_NAME}-{slot.index}{tag}.ini"),
                      QSettings.IniFormat)
    return UiSettings(shared, local, slot)
