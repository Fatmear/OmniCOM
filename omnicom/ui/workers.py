"""Qt threading layer: serial events -> signals, and protocol transfers."""

from __future__ import annotations

import threading
import time
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from .. import paths
from ..proto import xmodem, ymodem, zmodem
from ..proto.common import Link, TransferAborted, TransferError
from ..serial_port import DEMO_PORT, PortManager, Recorder


class SerialBridge(QObject):
    """Owns the port and re-emits its worker-thread events as Qt signals.

    Signal emission itself is thread safe; Qt queues the delivery to the GUI
    thread, which is what keeps the reader thread lock-free with respect to
    widget state.
    """

    rx = Signal(float, bytes)
    status = Signal(dict)
    ports = Signal(list)
    notice = Signal(str, str)
    stats = Signal(dict)
    record = Signal(dict)
    upload = Signal(dict)

    def __init__(self, demo: bool = False) -> None:
        super().__init__()
        self.manager = PortManager(self._dispatch, demo=demo)
        self.recorder = Recorder(paths.logs_dir(), self._dispatch)

    # -- worker thread entry point -----------------------------------------
    def _dispatch(self, event: dict) -> None:
        kind = event.get("t")
        if kind == "rx":
            data = event["d"]
            if self.recorder.active:
                self.recorder.write(data)
            self.rx.emit(time.time() * 1000.0, data)
        elif kind == "status":
            self.status.emit(event)
        elif kind == "ports":
            self.ports.emit(event.get("ports") or [])
        elif kind == "notice":
            self.notice.emit(event.get("text", ""), event.get("level", "info"))
        elif kind == "record":
            self.record.emit(event)
        elif kind == "xf":
            self.upload.emit(event)

    # -- convenience --------------------------------------------------------
    def state(self) -> dict:
        return self.manager.status()

    def shutdown(self) -> None:
        try:
            self.recorder.stop(silent=True)
        except Exception:
            pass
        self.manager.shutdown()


class PortLink(Link):
    """Adapts PortManager to the protocol engines (write / read / purge)."""

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


class TransferWorker(QThread):
    log = Signal(str)
    progress = Signal(int, int)
    succeeded = Signal(dict)
    failed = Signal(str)
    aborted = Signal(str)
    state = Signal(str)

    def __init__(self, manager: PortManager, protocol: str, direction: str,
                 name: str, data: bytes, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.protocol = protocol[:1].lower()
        self.direction = direction
        self.name = name or "file.bin"
        self.data = data
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    # ------------------------------------------------------------------ run
    def run(self) -> None:  # noqa: D401 - QThread entry point
        self.manager.begin_exclusive()
        self.state.emit("running")
        try:
            result = self._run_protocol()
            self.succeeded.emit(result)
        except TransferAborted as exc:
            self.aborted.emit(str(exc) or "已中止")
        except TransferError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface anything to the user
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.manager.end_exclusive()
            self.state.emit("idle")

    def _run_protocol(self) -> dict:
        link = PortLink(self.manager)
        cancel = self._cancel.is_set

        def log(text: str) -> None:
            self.log.emit(text)

        def progress(sent: int, total: int) -> None:
            self.progress.emit(int(sent), int(total))

        if self.protocol == "x":
            if self.direction == "send":
                return xmodem.send(link, self.data, name=self.name, timeout=10.0,
                                   progress=progress, log=log, cancel=cancel)
            result = xmodem.receive(link, timeout=10.0, progress=progress, log=log,
                                    cancel=cancel, name="xmodem-rx.bin")
            return self._save_single(result.get("name") or "xmodem-rx.bin",
                                    result.get("data") or b"")
        if self.protocol == "y":
            if self.direction == "send":
                return ymodem.send(link, self.data, name=self.name, timeout=10.0,
                                   progress=progress, log=log, cancel=cancel)
            result = ymodem.receive(link, timeout=10.0, progress=progress, log=log,
                                    cancel=cancel)
            saved = [self._save_single(item["name"], item["data"]) for item in result["files"]]
            return {"files": saved, "bytes": result.get("bytes", 0)}
        if self.direction == "send":
            return zmodem.ZModem(link, log=log, progress=progress, cancel=cancel).send(
                self.name, self.data)
        return zmodem.ZModem(link, log=log, progress=progress, cancel=cancel).receive(
            save_dir=paths.received_dir())

    @staticmethod
    def _save_single(name: str, data: bytes) -> dict:
        import os
        target_dir = paths.received_dir()
        os.makedirs(target_dir, exist_ok=True)
        safe = "".join(ch for ch in os.path.basename(name) if ch not in '<>:"/\\|?*').strip()
        path = os.path.join(target_dir, safe or "received.bin")
        stem, ext = os.path.splitext(path)
        index = 1
        while os.path.exists(path):
            path = f"{stem}-{index}{ext}"
            index += 1
        with open(path, "wb") as handle:
            handle.write(data)
        return {"name": os.path.basename(path), "path": path, "bytes": len(data)}


class RawFileWorker(QThread):
    """Send a file verbatim; progress arrives through PortManager's 'xf' events."""

    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, manager: PortManager, data: bytes, name: str, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.data = data
        self.name = name or "file.bin"
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            self.manager.send_file(self.data, self.name, cancel=self._cancel.is_set)
            self.finished_ok.emit()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")
