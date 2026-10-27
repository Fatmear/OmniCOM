"""Transport helpers shared by the GUI and the CLI.

Keeping this module free of Qt / argparse is what lets the exact same transfer
code run inside the Qt worker thread and inside a headless CLI invocation.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from . import paths
from .proto import xmodem, ymodem, zmodem
from .proto.common import Link, TransferError
from .serial_port import PortManager


class PortLink(Link):
    """Adapts :class:`PortManager` to the protocol engines.

    The manager hands raw bytes to an exclusive reader only while a transfer is
    in progress, so nothing is consumed twice.
    """

    def __init__(self, manager: PortManager) -> None:
        self.manager = manager
        self._stash = b""

    def write(self, data: bytes) -> None:
        self.manager.write(bytes(data))

    def read(self, size: int, timeout: float) -> bytes:
        if not self._stash:
            self._stash = self.manager.read_raw(size, timeout)
        if not self._stash:
            return b""
        chunk, self._stash = self._stash[:size], self._stash[size:]
        return chunk

    def purge(self) -> None:
        self._stash = b""
        self.manager.purge_raw()


def safe_name(name: str, fallback: str = "received.bin") -> str:
    base = os.path.basename(str(name or "").replace("\\", "/"))
    cleaned = "".join(ch for ch in base if ch not in '<>:"/\\|?*').strip(" .")
    return cleaned or fallback


def save_bytes(name: str, data: bytes, directory: Optional[str] = None,
               dedupe: bool = True) -> dict:
    """Write received bytes into the data directory without overwriting."""
    target_dir = directory or paths.received_dir()
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, safe_name(name))
    if dedupe:
        stem, ext = os.path.splitext(path)
        index = 1
        while os.path.exists(path):
            path = f"{stem}-{index}{ext}"
            index += 1
    with open(path, "wb") as handle:
        handle.write(data)
    return {"name": os.path.basename(path), "path": os.path.abspath(path), "bytes": len(data)}


def run_transfer(manager: PortManager, protocol: str, direction: str,
                 name: str = "file.bin", data: bytes = b"",
                 *, log: Optional[Callable[[str], None]] = None,
                 progress: Optional[Callable[[int, int], None]] = None,
                 cancel: Optional[Callable[[], bool]] = None,
                 save_dir: Optional[str] = None) -> dict:
    """Run one X/Y/ZMODEM transfer synchronously on the current thread.

    ``direction`` is ``"send"`` or ``"recv"``.  The caller must already hold the
    port; this function takes/releases exclusive read mode itself so the GUI and
    the CLI behave identically.
    """
    protocol = (protocol or "z").lower()[:1]
    direction = (direction or "send").lower()

    def _log(text: str) -> None:
        if log:
            log(text)

    def _progress(sent: int, total: int) -> None:
        if progress:
            progress(int(sent), int(total))

    manager.begin_exclusive()
    try:
        link = PortLink(manager)
        if protocol == "x":
            if direction == "send":
                return xmodem.send(link, data, name=name, timeout=10.0,
                                   progress=_progress, log=_log, cancel=cancel)
            result = xmodem.receive(link, timeout=10.0, progress=_progress, log=_log,
                                    cancel=cancel, name="xmodem-rx.bin")
            saved = save_bytes(result.get("name") or "xmodem-rx.bin",
                               result.get("data") or b"", save_dir)
            return {"files": [saved], "bytes": saved["bytes"]}

        if protocol == "y":
            if direction == "send":
                return ymodem.send(link, data, name=name, timeout=10.0,
                                   progress=_progress, log=_log, cancel=cancel)
            result = ymodem.receive(link, timeout=10.0, progress=_progress, log=_log,
                                    cancel=cancel)
            files = [save_bytes(item["name"], item["data"], save_dir)
                     for item in result["files"]]
            return {"files": files, "bytes": sum(item["bytes"] for item in files)}

        if protocol == "z":
            engine = zmodem.ZModem(link, log=_log, progress=_progress, cancel=cancel)
            if direction == "send":
                return engine.send(name, data)
            return engine.receive(save_dir=save_dir or paths.received_dir())

        raise TransferError(f"未知协议：{protocol}")
    finally:
        manager.end_exclusive()
