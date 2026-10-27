"""Filesystem locations that work both from source and from a frozen exe."""

from __future__ import annotations

import os
import sys

APP_NAME = "OmniCOM"


def resource_dir() -> str:
    """Directory that contains bundled resources (``assets`` etc.)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_file(*parts: str) -> str | None:
    path = os.path.join(resource_dir(), *parts)
    return path if os.path.exists(path) else None


def web_dir() -> str:
    """Legacy helper: the UI is native Qt now, no web assets are shipped."""
    return os.path.join(resource_dir(), "web")


def data_dir() -> str:
    """Writable directory for logs and received files."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "omnicom-data")
    os.makedirs(path, exist_ok=True)
    return path


def logs_dir() -> str:
    path = os.path.join(data_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def received_dir() -> str:
    path = os.path.join(data_dir(), "received")
    os.makedirs(path, exist_ok=True)
    return path
