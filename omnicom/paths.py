"""Filesystem locations that work both from source and from a frozen exe."""

from __future__ import annotations

import os
import sys

APP_NAME = "OmniCOM"


def archive_dir() -> str | None:
    """Folder of the ``.pyz`` / ``.zip`` we run from, else ``None``.

    A zipapp puts ``__file__`` inside the archive (``…/OmniCOM.pyz/omnicom/x.py``),
    where nothing is a real directory, so resources have to come from next to the
    archive instead (R-083).
    """
    for entry in sys.path:
        lowered = str(entry).lower()
        if lowered.endswith((".pyz", ".zip")) and os.path.isfile(entry):
            return os.path.dirname(os.path.abspath(entry))
    return None


def app_dir() -> str:
    """Folder that holds the code: exe dir, archive dir, or the repo root."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    archive = archive_dir()
    if archive is not None:
        return archive
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir() -> str:
    """Directory that contains bundled resources (``assets`` etc.)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return app_dir()


def resource_file(*parts: str) -> str | None:
    path = os.path.join(resource_dir(), *parts)
    return path if os.path.exists(path) else None


def web_dir() -> str:
    """Legacy helper: the UI is native Qt now, no web assets are shipped."""
    return os.path.join(resource_dir(), "web")


def data_dir() -> str:
    """Writable directory for logs and received files."""
    path = os.path.join(app_dir(), "omnicom-data")
    os.makedirs(path, exist_ok=True)
    return path


#: Folder the user picked in Preferences (empty = the default under data_dir).
_logs_override: str | None = None


def default_logs_dir() -> str:
    """The built-in capture folder, used when no preference is set."""
    path = os.path.join(data_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def set_logs_dir(path: str | None) -> str:
    """Point capture-to-file at ``path``; an empty value restores the default.

    Raises ``OSError`` when the folder cannot be created or written, so the
    caller can keep the previous location instead of silently losing captures.
    """
    global _logs_override
    cleaned = (path or "").strip()
    if not cleaned:
        _logs_override = None
        return default_logs_dir()
    expanded = os.path.abspath(os.path.expanduser(cleaned))
    os.makedirs(expanded, exist_ok=True)
    probe = os.path.join(expanded, ".omnicom-write-test")
    with open(probe, "w", encoding="utf-8") as handle:
        handle.write("ok")
    os.remove(probe)
    _logs_override = expanded
    return expanded


def logs_dir() -> str:
    """Folder where captures are written (the preference, else the default)."""
    if _logs_override:
        os.makedirs(_logs_override, exist_ok=True)
        return _logs_override
    return default_logs_dir()


def logs_dir_is_custom() -> bool:
    return _logs_override is not None


def received_dir() -> str:
    path = os.path.join(data_dir(), "received")
    os.makedirs(path, exist_ok=True)
    return path
