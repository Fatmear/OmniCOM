"""Qt threading layer: serial events -> signals, and protocol transfers.

All protocol work lives in :mod:`omnicom.transfer` (Qt free) so the CLI and the
GUI run byte-for-byte identical transfer code.  This module only adapts it to
Qt threads and signals.
"""

from __future__ import annotations

import contextlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QThread, Signal

from .. import paths
from ..proto.common import TransferAborted, TransferError
from ..serial_port import PortManager, Recorder
from ..transfer import run_transfer


class SerialBridge(QObject):
    """Owns the port and re-emits its worker-thread events as Qt signals.

    Signal emission itself is thread safe; Qt queues the delivery to the GUI
    thread, which is what keeps the reader thread lock-free with respect to
    widget state.
    """

    rx = Signal(float, bytes)
    #: Every byte that reached the device, whatever path sent it.  The CLI
    #: control channel uses this to show CLI sends in the window (R-112); the
    #: echo setting still decides whether the *view* prints them.
    tx = Signal(float, bytes)
    status = Signal(dict)
    ports = Signal(list)
    notice = Signal(str, str)
    stats = Signal(dict)
    record = Signal(dict)
    upload = Signal(dict)
    txPrinted = Signal(bytes)

    def __init__(self, demo: bool = False) -> None:
        super().__init__()
        self._operation_lock = threading.Lock()
        self._pending = 0
        self._closing = False
        self._operations = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="omnicom-port")
        self.manager = PortManager(self._dispatch, demo=demo)
        self.recorder = Recorder(paths.logs_dir(), self._dispatch)
        # The GUI capture mirrors the log window: lines arrive through
        # ``Recorder.write_line`` from the single log choke point, so the file
        # holds timestamps and directions exactly as printed (R-101).  The CLI
        # keeps the default raw, byte-exact mode.
        self.recorder.text_mode = True

    def open(self, port: str, opts: dict) -> None:
        self._submit(self.manager.open, port, dict(opts))

    def close(self) -> None:
        self._submit(self.manager.close)

    def update_opts(self, opts: dict) -> None:
        self._submit(self.manager.update_opts, dict(opts))

    def set_capture_dir(self, directory: str) -> None:
        """Follow the folder chosen in Preferences (R-070)."""
        self.recorder.set_directory(directory)

    def _submit(self, operation, *args) -> None:
        with self._operation_lock:
            if self._closing:
                return
            self._pending += 1
        self._dispatch({"t": "status", **self.manager.status()})
        self._operations.submit(self._run_operation, operation, args)

    def _run_operation(self, operation, args) -> None:
        try:
            operation(*args)
        except Exception as exc:
            self.notice.emit(str(exc), "error")
        finally:
            with self._operation_lock:
                self._pending -= 1
            self._dispatch({"t": "status", **self.manager.status()})

    # -- worker thread entry point -----------------------------------------
    def _dispatch(self, event: dict) -> None:
        kind = event.get("t")
        if kind == "rx":
            data = event["d"]
            if self.recorder.active:
                self.recorder.write(data, "rx")
            self.rx.emit(time.time() * 1000.0, data)
        elif kind == "tx":
            payload = event.get("d") or b""
            if self.recorder.active:
                self.recorder.write(payload, "tx")
            self.tx.emit(time.time() * 1000.0, payload)
        elif kind == "status":
            with self._operation_lock:
                busy = self._pending > 0
            self.status.emit({**event, "busy": busy})
        elif kind == "ports":
            self.ports.emit(event.get("ports") or [])
        elif kind == "notice":
            self.notice.emit(event.get("text", ""), event.get("level", "info"))
        elif kind == "record":
            self.record.emit(event)
        elif kind == "xf":
            self.upload.emit(event)
        elif kind == "txprint":
            self.txPrinted.emit(event.get("d") or b"")

    def state(self) -> dict:
        return self.manager.status()

    def shutdown(self) -> None:
        with self._operation_lock:
            self._closing = True
        self._operations.shutdown(wait=True, cancel_futures=True)
        with contextlib.suppress(Exception):
            self.recorder.stop(silent=True)
        self.manager.shutdown()


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

    def run(self) -> None:
        self.state.emit("running")
        try:
            result = run_transfer(
                self.manager, self.protocol, self.direction,
                name=self.name, data=self.data,
                log=self.log.emit,
                progress=lambda sent, total: self.progress.emit(int(sent), int(total)),
                cancel=self._cancel.is_set,
            )
            self.succeeded.emit(result)
        except TransferAborted as exc:
            self.aborted.emit(str(exc) or "已中止")
        except TransferError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.state.emit("idle")


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
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
