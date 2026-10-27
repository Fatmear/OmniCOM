"""Serial port ownership: open/close, lossless RX, auto-reconnect, recording.

The service owns the port; the browser is a thin client.  That is what lets the
UI reload without dropping the link and what makes unplug/replug recovery
possible (R-005) without any browser permission prompt.

Loss rule (R-017): received bytes are never discarded.  The reader thread either
broadcasts them or queues them for an in-progress protocol transfer; a paused
UI buffers them on the client side.
"""

from __future__ import annotations

import contextlib
import os
import queue
import threading
import time
from collections.abc import Callable

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - reported to the UI instead of crashing
    serial = None
    list_ports = None

DEFAULT_OPTS = {
    "baudrate": 115200,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "rtscts": False,
    "xonxoff": False,
    "dsrdtr": False,
    "dtr": True,
    "rts": True,
    "autoReconnect": True,
}

_PARITY = {"N": "N", "E": "E", "O": "O", "M": "M", "S": "S"}


def available() -> bool:
    return serial is not None


def list_serial_ports() -> list[dict]:
    if list_ports is None:
        return []
    ports = []
    for info in list_ports.comports():
        ports.append({
            "name": info.device,
            "description": info.description or info.device,
            "hwid": info.hwid or "",
            "vid": info.vid,
            "pid": info.pid,
            "serial": info.serial_number or "",
            "manufacturer": info.manufacturer or "",
            "product": info.product or "",
        })
    ports.sort(key=lambda item: item["name"])
    return ports


class PortNotOpen(RuntimeError):
    pass


DEMO_PORT = "DEMO"


class DemoDevice:
    """A virtual serial device so the UI can be exercised without hardware.

    It answers a few AT commands, echoes anything else, and streams a telemetry
    line every ~700 ms.  Enabled by ``--demo``; never touches a real port.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._open = True
        self._line = bytearray()
        self._tick = 0
        self.dtr = True
        self.rts = True
        self.baudrate = 115200
        self.is_open = True
        self._stop = threading.Event()
        self._threads = [
            threading.Thread(target=self._telemetry, daemon=True),
            threading.Thread(target=self._boot, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    # -- deterministic but lively telemetry --------------------------------
    def _push(self, data: bytes) -> None:
        with self._cond:
            self._buffer += data
            self._cond.notify_all()

    def _boot(self) -> None:
        self._push(b"\r\nOmniCOM DEMO bootloader v1.0\r\n"
                   b"[boot] flash 8MB  sram 320KB  clock 168MHz\r\n"
                   b"[boot] sensor bus ready\r\n"
                   b"[boot] type AT to talk, or just watch the stream\r\n")

    def _telemetry(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.7)
            if self._stop.is_set():
                return
            self._tick += 1
            temp = 25.0 + (self._tick % 17) * 0.13
            volts = 3.28 + (self._tick % 11) * 0.004
            rssi = -62 - (self._tick % 9)
            line = (f"[{self._tick:06d}] temp={temp:.2f}C vbat={volts:.3f}V rssi={rssi}dBm "
                    f"uptime={self._tick * 700}ms stat=0x{(self._tick * 2654435761) & 0xffff:04x}\r\n")
            self._push(line.encode())

    def _respond(self, line: str) -> None:
        cmd = line.strip()
        if not cmd:
            self._push(b"\r\nOK\r\n")
            return
        upper = cmd.upper()
        if upper == "AT":
            self._push(b"\r\nOK\r\n")
        elif upper == "ATI":
            self._push(b"\r\nOmniCOM-DEMO\r\nREV 0.1.0\r\nOK\r\n")
        elif upper == "AT+CGMR":
            self._push(b"\r\n+CGMR: DEMO-0.1.0\r\n\r\nOK\r\n")
        elif upper == "AT+CSQ":
            self._push(b"\r\n+CSQ: 24,99\r\n\r\nOK\r\n")
        elif upper.startswith("AT+"):
            self._push(f"\r\n{upper}\r\nERROR\r\n".encode())
        else:
            self._push(b"OK\r\n")

    # -- serial-like surface ------------------------------------------------
    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._buffer)

    def read(self, size: int = 1) -> bytes:
        with self._cond:
            if not self._buffer:
                self._cond.wait(0.05)
            if not self._buffer:
                return b""
            take = min(len(self._buffer), max(1, size))
            data = bytes(self._buffer[:take])
            del self._buffer[:take]
            return data

    def write(self, data) -> int:
        payload = bytes(data)
        # Collect complete lines under the lock, then answer outside it:
        # _respond() -> _push() takes the same non-reentrant lock, so calling it
        # while holding the lock deadlocks this thread (and the telemetry one).
        lines: list[str] = []
        with self._lock:
            self._line += payload
            while b"\n" in self._line:
                head, _, rest = bytes(self._line).partition(b"\n")
                self._line = bytearray(rest)
                lines.append(head.decode("utf-8", "replace").strip("\r"))
        for text in lines:
            self._respond(text)
        return len(payload)

    def flush(self) -> None:
        return None

    def reset_input_buffer(self) -> None:
        with self._lock:
            self._buffer.clear()

    def close(self) -> None:
        self.is_open = False
        self._stop.set()
        with self._cond:
            self._cond.notify_all()


class PortManager:
    def __init__(self, emit: Callable[[dict], None], demo: bool = False) -> None:
        self._emit = emit
        self.demo = demo
        self._lock = threading.RLock()
        self._open_lock = threading.Lock()
        self._ser = None
        self._reader: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._want: str | None = None
        self._port: str | None = None
        self._opts = dict(DEFAULT_OPTS)
        self._identity: tuple | None = None
        self._raw: queue.Queue[bytes] = queue.Queue()
        self._exclusive = False
        self._stopping = False
        self._ports: list[dict] = []
        self._ports_sig: tuple | None = None
        self._reconnecting = False
        self._last_error: str | None = None
        self._retry_notice = 0.0
        self._opened_at = 0.0

        self.rx_bytes = 0
        self.rx_chunks = 0
        self.tx_bytes = 0
        self.tx_ops = 0

        self._monitor = threading.Thread(target=self._monitor_loop, name="omnicom-monitor", daemon=True)
        self._monitor.start()

    # ------------------------------------------------------------------ state
    def status(self) -> dict:
        with self._lock:
            return {
                "port": self._port,
                "want": self._want,
                "connected": self._ser is not None,
                "reconnecting": self._reconnecting,
                "error": self._last_error,
                "opts": dict(self._opts),
                "exclusive": self._exclusive,
            }

    def stats(self) -> dict:
        return {
            "rx": self.rx_bytes,
            "tx": self.tx_bytes,
            "chunks": self.rx_chunks,
            "txOps": self.tx_ops,
        }

    def ports(self) -> list[dict]:
        return self._ports

    def _emit_status(self) -> None:
        self._emit({"t": "status", **self.status()})

    # ------------------------------------------------------------- open/close
    def open(self, port: str, opts: dict | None = None) -> None:
        merged = dict(self._opts)
        if opts:
            merged.update({key: value for key, value in opts.items() if value is not None})
        merged = _normalise(merged)
        identity = None
        for item in self._ports:
            if item["name"] == port:
                identity = (item.get("serial") or "", item.get("vid"), item.get("pid"))
                break
        with self._lock:
            self._want = port
            self._opts = merged
            self._identity = identity
            self._last_error = None
            self._reconnecting = False
        self._close_hardware(silent=True)
        self._open_now(port, merged, announce=True)

    def close(self) -> None:
        with self._lock:
            self._want = None
            self._reconnecting = False
            self._identity = None
        self._close_hardware(silent=False)
        self._emit_status()

    def _close_hardware(self, silent: bool) -> None:
        self._reader_stop.set()
        with self._lock:
            ser, self._ser = self._ser, None
            self._port = None
        if ser is not None:
            with contextlib.suppress(Exception):
                ser.close()
        reader = self._reader
        if reader is not None and reader.is_alive() and reader is not threading.current_thread():
            reader.join(0.5)
        self._reader = None
        if not silent:
            self._drain_raw()

    def _open_now(self, port: str, opts: dict, announce: bool) -> bool:
        if port != DEMO_PORT and serial is None:
            self._last_error = "缺少 pyserial，请执行 pip install -r requirements.txt"
            self._emit({"t": "notice", "level": "error", "text": self._last_error})
            return False
        if not self._open_lock.acquire(blocking=False):
            return False
        try:
            if port == DEMO_PORT:
                handle = DemoDevice()
            else:
                handle = serial.Serial(
                    port=port,
                    baudrate=int(opts["baudrate"]),
                    bytesize=int(opts["bytesize"]),
                    parity=_PARITY.get(str(opts["parity"]).upper(), "N"),
                    stopbits=float(opts["stopbits"]),
                    timeout=0.05,
                    write_timeout=5.0,
                    rtscts=bool(opts.get("rtscts")),
                    xonxoff=bool(opts.get("xonxoff")),
                    dsrdtr=bool(opts.get("dsrdtr")),
                    exclusive=True,
                )
            try:
                handle.dtr = bool(opts.get("dtr", True))
                handle.rts = bool(opts.get("rts", True))
            except Exception:
                pass
            with contextlib.suppress(Exception):
                handle.reset_input_buffer()
        except Exception as exc:
            self._last_error = _friendly_error(exc, port)
            self._emit({"t": "notice", "level": "warn", "text": self._last_error})
            self._emit_status()
            return False
        finally:
            self._open_lock.release()

        self._reader_stop = threading.Event()
        with self._lock:
            self._ser = handle
            self._port = port
            self._last_error = None
            self._reconnecting = False
            self._opened_at = time.time()
            self._drain_raw()
        reader = threading.Thread(target=self._reader_loop, args=(handle, self._reader_stop),
                                  name="omnicom-reader", daemon=True)
        self._reader = reader
        reader.start()
        if announce:
            self._emit({"t": "notice", "level": "ok",
                        "text": f"已打开 {port} @ {opts['baudrate']}"})
        self._emit_status()
        return True

    # ------------------------------------------------------------------ reader
    def _reader_loop(self, handle, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                waiting = handle.in_waiting
                data = handle.read(waiting if waiting else 1)
            except Exception as exc:
                self._handle_loss(exc)
                return
            if not data:
                continue
            self.rx_bytes += len(data)
            self.rx_chunks += 1
            if self._exclusive:
                self._raw.put(data)
            else:
                self._emit({"t": "rx", "d": data})

    def _handle_loss(self, exc: Exception) -> None:
        port = self._want or "串口"
        present = self._port_present(port)
        with self._lock:
            ser, self._ser = self._ser, None
            self._port = None
            self._last_error = _friendly_error(exc, port, present)
            auto = bool(self._opts.get("autoReconnect", True))
            # Enter the retry state immediately, otherwise the UI flashes
            # "连接失败" for the whole time the device is being re-plugged.
            self._reconnecting = auto
        if ser is not None:
            with contextlib.suppress(Exception):
                ser.close()
        self._drain_raw()
        detail = "设备已拔出，" if not present else ""
        self._emit({"t": "notice", "level": "warn",
                    "text": f"串口断开：{detail}{self._last_error}"
                            + ("（自动重连已开启，插回后会自动恢复）" if auto else "")})
        self._emit_status()

    def _port_present(self, port: str) -> bool:
        if port == DEMO_PORT:
            return True
        return any(item["name"] == port for item in self._ports)

    # ----------------------------------------------------------------- monitor
    def _monitor_loop(self) -> None:
        while not self._stopping:
            try:
                ports = list_serial_ports()
                if self.demo:
                    ports = [*ports, {
                        "name": DEMO_PORT,
                        "description": "虚拟演示串口（AT 回环 + 遥测）",
                        "hwid": "DEMO\\OmniCOM",
                        "vid": None, "pid": None,
                        "serial": "DEMO-0001",
                        "manufacturer": "OmniCOM",
                        "product": "Virtual Demo Port",
                    }]
                signature = tuple(sorted((item["name"], item["hwid"]) for item in ports))
                if signature != self._ports_sig:
                    self._ports_sig = signature
                    self._ports = ports
                    self._emit({"t": "ports", "ports": ports})
                with self._lock:
                    want = self._want
                    connected = self._ser is not None
                    auto = bool(self._opts.get("autoReconnect", True))
                if want and not connected and auto:
                    target = self._resolve_target(want, ports)
                    if target:
                        with self._lock:
                            self._reconnecting = True
                        if self._open_now(target, self._opts, announce=False):
                            self._retry_notice = 0.0
                        else:
                            self._notify_retry_throttled()
                elif want and not connected:
                    with self._lock:
                        self._reconnecting = False
            except Exception:
                pass
            time.sleep(0.4)

    def _resolve_target(self, want: str, ports: list[dict]) -> str | None:
        names = {item["name"] for item in ports}
        if want in names:
            return want
        identity = self._identity
        if not identity:
            return None
        serial_no, vid, pid = identity
        for item in ports:
            if serial_no and item.get("serial") == serial_no:
                return item["name"]
        if vid is not None:
            for item in ports:
                if item.get("vid") == vid and item.get("pid") == pid:
                    return item["name"]
        return None

    # ------------------------------------------------------------------ writes
    def write(self, data: bytes) -> int:
        if not data:
            return 0
        with self._lock:
            handle = self._ser
        if handle is None:
            raise PortNotOpen("串口未打开")
        try:
            written = handle.write(data)
            handle.flush()
        except Exception as exc:
            self._handle_loss(exc)
            raise PortNotOpen(str(exc)) from exc
        self.tx_bytes += len(data)
        self.tx_ops += 1
        self._emit({"t": "tx", "n": len(data)})
        return written

    def send_file(self, data: bytes, name: str = "file", cancel=None) -> dict:
        total = len(data)
        sent = 0
        chunk = 4096
        started = time.monotonic()
        while sent < total:
            if cancel and cancel():
                raise PortNotOpen("已取消")
            piece = data[sent:sent + chunk]
            self.write(piece)
            sent += len(piece)
            elapsed = max(time.monotonic() - started, 1e-6)
            self._emit({"t": "xf", "proto": "raw", "dir": "send", "state": "running",
                        "sent": sent, "total": total, "name": name,
                        "rate": int(sent / elapsed)})
        self._emit({"t": "xf", "proto": "raw", "dir": "send", "state": "done",
                    "sent": total, "total": total, "name": name})
        return {"bytes": total, "name": name}

    # --------------------------------------------------------------- transfers
    @property
    def exclusive(self) -> bool:
        return self._exclusive

    def begin_exclusive(self) -> None:
        self._drain_raw()
        self._exclusive = True
        self._emit_status()

    def end_exclusive(self) -> None:
        self._exclusive = False
        self._drain_raw()
        self._emit_status()

    def read_raw(self, size: int, timeout: float) -> bytes:
        try:
            return self._raw.get(timeout=max(0.001, timeout))
        except queue.Empty:
            return b""

    def purge_raw(self) -> int:
        return self._drain_raw()

    def _drain_raw(self) -> int:
        count = 0
        while True:
            try:
                self._raw.get_nowait()
                count += 1
            except queue.Empty:
                return count

    # ------------------------------------------------------------- live options
    def update_opts(self, patch: dict) -> None:
        with self._lock:
            merged = _normalise({**self._opts, **patch})
            before = dict(self._opts)
            self._opts = merged
            handle = self._ser
        if handle is not None:
            try:
                if merged["baudrate"] != before["baudrate"]:
                    handle.baudrate = int(merged["baudrate"])
                if "dtr" in patch:
                    handle.dtr = bool(merged["dtr"])
                if "rts" in patch:
                    handle.rts = bool(merged["rts"])
            except Exception as exc:
                self._emit({"t": "notice", "level": "warn", "text": f"参数应用失败：{exc}"})
            hot = ("bytesize", "parity", "stopbits", "rtscts", "xonxoff", "dsrdtr")
            if any(merged[key] != before[key] for key in hot):
                port = self._port
                if port:
                    self._emit({"t": "notice", "level": "info",
                                "text": "帧格式/流控变化，正在重新打开串口"})
                    self._close_hardware(silent=True)
                    self._open_now(port, merged, announce=False)
        self._emit_status()

    def shutdown(self) -> None:
        self._stopping = True
        self._close_hardware(silent=True)


def _normalise(opts: dict) -> dict:
    out = dict(DEFAULT_OPTS)
    out.update(opts)
    out["baudrate"] = max(1, int(out.get("baudrate") or 115200))
    out["bytesize"] = int(out.get("bytesize") or 8)
    out["parity"] = str(out.get("parity") or "N").upper()[:1]
    out["stopbits"] = float(out.get("stopbits") or 1)
    for key in ("rtscts", "xonxoff", "dsrdtr", "dtr", "rts", "autoReconnect"):
        out[key] = bool(out.get(key))
    return out


def _friendly_error(exc: Exception, port: str, present: bool | None = None) -> str:
    """Human wording for a pyserial failure.

    On Windows an unplugged USB-serial adapter raises ``PermissionError`` with
    "拒绝访问" - exactly the same exception a genuinely occupied port raises.
    Telling the user "被其他程序占用" when the device is simply gone sends them
    hunting for a phantom program, so the caller passes whether the port is
    still enumerated and the message follows that fact instead of the errno.
    """
    text = str(exc)
    denied = ("PermissionError" in type(exc).__name__
              or "拒绝访问" in text or "Access is denied" in text)
    missing = ("FileNotFoundError" in type(exc).__name__
               or "could not open port" in text.lower()
               or "系统找不到指定的文件" in text)
    if denied:
        if present is False:
            return f"{port} 已拔出（设备不在串口列表中）"
        return f"{port} 被其他程序占用"
    if missing:
        if present is False:
            return f"{port} 已拔出"
        return f"无法打开 {port}（设备可能已拔出）"
    return text or type(exc).__name__


class Recorder:
    """Streams every received byte to disk, independent of any UI pause state."""

    def __init__(self, directory: str, emit: Callable[[dict], None]) -> None:
        self.directory = directory
        self._emit = emit
        self._lock = threading.Lock()
        self._handle = None
        self._path: str | None = None
        self._bytes = 0

    @property
    def active(self) -> bool:
        return self._handle is not None

    def state(self) -> dict:
        return {"active": self.active, "path": self._path, "bytes": self._bytes}

    def start(self) -> dict:
        with self._lock:
            if self._handle is not None:
                return self.state()
            os.makedirs(self.directory, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self._path = os.path.join(self.directory, f"rx-{stamp}.log")
            # Long-lived streaming handle by design (closed in stop()), so it
            # cannot use a context manager.  SIM115 is about leaks; this is not one.
            self._handle = open(self._path, "ab", buffering=0)  # noqa: SIM115
            self._bytes = 0
        self._emit({"t": "record", **self.state()})
        return self.state()

    def write(self, data: bytes) -> None:
        with self._lock:
            if self._handle is None:
                return
            try:
                self._handle.write(data)
                self._bytes += len(data)
            except Exception:
                self.stop(silent=True)

    def flush(self) -> None:
        with self._lock:
            if self._handle is not None:
                with contextlib.suppress(Exception):
                    self._handle.flush()

    def stop(self, silent: bool = False) -> dict:
        with self._lock:
            handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except Exception:
                pass
        if not silent:
            self._emit({"t": "record", **self.state()})
        return self.state()

    def _notify_retry_throttled(self) -> None:
        """Report a failed reconnect attempt at most once every 4 seconds.

        The monitor retries every 0.4 s; without this the notice feed would be
        flooded while a device is unplugged.
        """
        now = time.time()
        if now - self._retry_notice < 4.0:
            return
        self._retry_notice = now
        self._emit({"t": "notice", "level": "warn",
                    "text": f"重连失败：{self._last_error}（仍在重试）"})
