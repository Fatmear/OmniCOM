"""Loopback control channel between the CLI and the running window.

A serial port has exactly one owner, so the old behaviour - the CLI failing with
"port busy" while the window held COM1 - left the agent with no way to drive the
device the user was looking at.  The window now publishes a loopback endpoint and
executes CLI requests *inside the GUI process*:

* the CLI never opens the port itself, so there is no contention at all;
* everything the CLI does appears in the window (TX lines, RX traffic, open and
  close notices), because the window's own log is the only output;
* the window stays the single source of truth for the port state, so closing
  from the CLI really closes it (R-112).

The module is Qt free on purpose: the CLI imports it without a QApplication, and
the window only needs :class:`Server` plus a timer that calls ``poll()``.

Protocol: one JSON object per line, one request per connection.  The client sends
``{"token": ..., "id": n, "cmd": "send", "args": {...}}`` and reads one JSON
object back.  Requests may be *deferred*: the handler returns ``None`` and the
window replays it on later ``poll()`` calls until it has an answer, which is how
``read`` waits for data without blocking either side.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import socket
import threading
import time
from typing import Any

from . import __version__, paths

ENDPOINT_NAME = "control.json"          # 1.1.3 single-endpoint file (legacy)
ENDPOINT_DIR_NAME = "control"           # one file per window: control/window-<pid>.json
HOST = "127.0.0.1"
#: Client side: a CLI command must never hang on a wedged window.
CLIENT_TIMEOUT = 30.0
#: Window side: how long a request may stay outstanding (``read`` waits inside).
MAX_DEFER = 20.0
#: Largest single request (a chunked send of a big file).
MAX_LINE = 8 * 1024 * 1024


# ----------------------------------------------------------------- endpoints
def endpoint_dir() -> str:
    """Folder holding one endpoint file per running window."""
    return os.path.join(paths.data_dir(), ENDPOINT_DIR_NAME)


def endpoint_path(pid: int | None = None) -> str:
    """``pid``'s endpoint file; without a pid, the legacy single endpoint."""
    if pid:
        return os.path.join(endpoint_dir(), f"window-{int(pid)}.json")
    return os.path.join(paths.data_dir(), ENDPOINT_NAME)


def _pid_alive(pid: int) -> bool:
    """True when ``pid`` is a **running** process.

    ``OpenProcess`` alone is not enough: the parent keeps a handle to a killed
    child, so the call still succeeds and a stale endpoint was treated as live.
    The CLI then kept trying to talk to a dead window and never fell back to
    opening the port itself (L-143).  ``GetExitCodeProcess`` reports
    ``STILL_ACTIVE`` (259) only for a real live process.
    """
    if pid <= 0:
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        process = kernel32.OpenProcess(0x1000, False, int(pid))
        if not process:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(process, ctypes.byref(code)):
                return False
            return code.value == 259                      # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(process)
    except AttributeError:                              # not Windows
        try:
            os.kill(int(pid), 0)
        except OSError:
            return False
        return True
    except Exception:
        return False


def same_port(left: str, right: str) -> bool:
    """Case-insensitive port comparison (``com3`` == ``COM3``)."""
    return str(left or "").strip().upper() == str(right or "").strip().upper()


def _load_endpoint(path: str) -> dict | None:
    """Parse one endpoint file, removing it when its process is gone."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        port = int(data.get("port") or 0)
    except (TypeError, ValueError):
        return None
    if not port or not data.get("token"):
        return None
    if not _pid_alive(int(data.get("pid") or 0)):
        # Left behind by a crashed window: never make the CLI wait on it.
        with contextlib.suppress(OSError):
            os.remove(path)
        return None
    data["port"] = port
    data.setdefault("host", HOST)
    data["serial"] = str(data.get("serial") or "")
    data["connected"] = bool(data.get("connected"))
    data["slot"] = int(data.get("slot") or 0)
    data["path"] = path
    return data


def endpoints() -> list[dict]:
    """Every live window endpoint, oldest first.

    One file per window is what lets a CLI bind to the window that is already
    connected to the port it was asked about, instead of always hammering the
    first window (R-119).
    """
    found: list[dict] = []
    folder = endpoint_dir()
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    for name in names:
        if name.startswith("window-") and name.endswith(".json"):
            item = _load_endpoint(os.path.join(folder, name))
            if item is not None:
                found.append(item)
        elif name.endswith(".tmp"):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(folder, name))
    # A window started by 1.1.3 published a single file; keep it working.
    legacy = _load_endpoint(endpoint_path())
    if legacy is not None and all(item["port"] != legacy["port"] for item in found):
        found.append(legacy)
    found.sort(key=lambda item: (float(item.get("started") or 0),
                                 int(item.get("pid") or 0)))
    return found


def read_endpoint(pid: int | None = None) -> dict | None:
    """The endpoint of one window (``pid``) or of the oldest window."""
    if pid:
        return _load_endpoint(endpoint_path(pid))
    items = endpoints()
    return items[0] if items else None


def running() -> bool:
    """True when at least one window is hosting a control channel."""
    return bool(endpoints())


# -------------------------------------------------------------------- client
class ControlError(RuntimeError):
    """The window answered with ``ok: false`` or could not be reached."""


class Client:
    """One CLI request to the running window."""

    def __init__(self, endpoint: dict | None = None,
                 timeout: float = CLIENT_TIMEOUT) -> None:
        self.endpoint = endpoint or read_endpoint()
        self.timeout = float(timeout)
        if not self.endpoint:
            raise ControlError("没有正在运行的窗口")

    def request(self, cmd: str, **args: Any) -> dict:
        payload = {"token": self.endpoint["token"], "cmd": cmd, "args": args,
                   "id": int(time.time() * 1000) & 0xFFFFF,
                   "version": __version__}
        line = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            with socket.create_connection(
                    (self.endpoint.get("host", HOST), self.endpoint["port"]),
                    timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(line)
                chunks: list[bytes] = []
                while True:
                    piece = sock.recv(65536)
                    if not piece:
                        break
                    chunks.append(piece)
                    if b"\n" in piece:
                        break
        except OSError as exc:
            raise ControlError(f"无法连接运行中的窗口：{exc}") from exc
        raw = b"".join(chunks).strip()
        if not raw:
            raise ControlError("窗口没有返回任何数据")
        try:
            reply = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as exc:
            raise ControlError(f"窗口返回了无法解析的数据：{exc}") from exc
        if not isinstance(reply, dict):
            raise ControlError("窗口返回了非对象数据")
        return reply


# -------------------------------------------------------------------- server
class _Job:
    """One request waiting for the GUI thread to answer it."""

    __slots__ = ("connection", "deadline", "done", "reply", "request")

    def __init__(self, connection: socket.socket, request: dict) -> None:
        self.connection = connection
        self.request = request
        self.done = threading.Event()
        self.reply: dict | None = None
        self.deadline = time.monotonic() + MAX_DEFER

    def extend(self, seconds: float) -> None:
        """Give a long job (file send, XMODEM) more time to answer."""
        self.deadline = time.monotonic() + max(1.0, float(seconds))

    def finish(self, reply: dict) -> None:
        if self.done.is_set():
            return
        self.reply = reply
        self.done.set()


class Deferred:
    """Reply handle for a request the GUI thread answers later.

    A CLI ``read`` waits for data and a ``send-file`` streams a file; neither can
    answer inside one :meth:`Server.poll` call, and neither may block the GUI
    thread.  The handler therefore returns ``None`` and keeps this handle, then
    calls :meth:`done` from whatever thread/timer produced the answer (R-112).
    """

    __slots__ = ("_job",)

    def __init__(self, job: _Job) -> None:
        self._job = job

    def done(self, **reply: Any) -> None:
        self._job.finish(dict(reply))

    def fail(self, message: str, **extra: Any) -> None:
        self._job.finish({"ok": False, "error": message, **extra})

    def touch(self, seconds: float) -> None:
        """Push the deadline out - a transfer legitimately runs for minutes."""
        self._job.extend(seconds)


class Server:
    """Hosts the endpoint and hands requests to the GUI thread.

    The socket work runs on its own threads; **nothing** here touches a widget.
    The window calls :meth:`poll` from a timer, which runs the handler on the GUI
    thread - the same thread the user's clicks run on - so every CLI command sees
    a consistent window state.
    """

    def __init__(self, slot: int = 0) -> None:
        self.token = secrets.token_hex(16)
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self._queue: list[_Job] = []
        self._open: list[_Job] = []
        self.host = HOST
        self.port = 0
        self.slot = int(slot or 0)
        self._started = time.time()
        self._endpoint = ""
        #: The serial port this window owns right now.  Republished on every
        #: change so a CLI can pick the window that already has the port it was
        #: asked about instead of hijacking another window's session (R-119).
        self.info: dict[str, Any] = {"serial": "", "connected": False}

    @property
    def running(self) -> bool:
        """True once this window hosts the endpoint."""
        return self._running

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> bool:
        """Publish this window's endpoint and listen.

        Every window hosts **its own** channel: the CLI then picks the window
        that is already connected to the port it was asked about, so several
        windows (and several ports) can run side by side without a CLI ever
        landing in the wrong one (R-119).
        """
        if self._running:
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((HOST, 0))
            sock.listen(8)
            sock.settimeout(0.5)
        except OSError:
            return False
        self._socket = sock
        self.port = sock.getsockname()[1]
        self._running = True
        self._endpoint = endpoint_path(os.getpid())
        self._write_endpoint()
        self._thread = threading.Thread(target=self._accept_loop,
                                        name="omnicom-control", daemon=True)
        self._thread.start()
        return True

    def _write_endpoint(self) -> None:
        path = self._endpoint or endpoint_path(os.getpid())
        data = {"host": self.host, "port": self.port, "token": self.token,
                "pid": os.getpid(), "version": __version__,
                "started": self._started, "slot": self.slot,
                "serial": str(self.info.get("serial") or ""),
                "connected": bool(self.info.get("connected"))}
        temp = path + ".tmp"
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.replace(temp, path)
        except OSError:
            with contextlib.suppress(OSError):
                os.remove(temp)

    def publish(self, **fields: Any) -> None:
        """Republish the endpoint when the owned serial port changes.

        Only a real change costs a write: this runs from the status handler,
        which also fires for statistics updates several times a second.
        """
        changed = False
        for key, value in fields.items():
            if self.info.get(key) != value:
                self.info[key] = value
                changed = True
        if changed and self._running:
            self._write_endpoint()

    def stop(self) -> None:
        """Stop listening and withdraw the endpoint (only if it is ours)."""
        self._running = False
        sock, self._socket = self._socket, None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.close()
        # Only this window's own file: the other windows keep their channels.
        with contextlib.suppress(OSError):
            os.remove(self._endpoint or endpoint_path(os.getpid()))
        with self._lock:
            waiting, self._queue = self._queue, []
        for job in waiting + self._open:
            job.finish({"ok": False, "error": "窗口已关闭"})
        self._open = []

    # -- socket threads -----------------------------------------------------
    def _accept_loop(self) -> None:
        while self._running:
            sock = self._socket
            if sock is None:
                return
            try:
                connection, _address = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,),
                             name="omnicom-control-req", daemon=True).start()

    def _serve(self, connection: socket.socket) -> None:
        with connection:
            try:
                connection.settimeout(MAX_DEFER)
                reader = connection.makefile("rb")
                line = reader.readline(MAX_LINE)
                if not line:
                    return
                request = json.loads(line.decode("utf-8", "replace"))
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
            except (OSError, ValueError):
                self._send(connection, {"ok": False, "error": "请求格式不正确"})
                return
            if str(request.get("token") or "") != self.token:
                self._send(connection, {"ok": False,
                                        "error": "控制令牌不匹配（窗口已重启？）"})
                return
            job = _Job(connection, request)
            with self._lock:
                self._queue.append(job)
            # Wait in slices: a long job may ask for more time while it runs.
            while not job.done.wait(0.5):
                if time.monotonic() > job.deadline:
                    job.finish({"ok": False, "error": "窗口繁忙，请求超时"})
                    break
            self._send(connection, job.reply or {"ok": False, "error": "没有结果"})

    @staticmethod
    def _send(connection: socket.socket, reply: dict) -> None:
        with contextlib.suppress(OSError):
            connection.sendall(
                json.dumps(reply, ensure_ascii=False, default=str).encode("utf-8")
                + b"\n")

    # -- GUI thread ---------------------------------------------------------
    def poll(self, handler) -> int:
        """Run pending requests on the caller's thread; returns how many ran.

        ``handler(request, reply)`` returns a reply dict for an immediate answer,
        or ``None`` after keeping ``reply`` - :class:`Deferred` - for a long job
        that answers later.  A deferred job that is never answered times out on
        the client side instead of wedging anything.
        """
        with self._lock:
            fresh, self._queue = self._queue, []
        pending = self._open + fresh
        self._open = []
        handled = 0
        now = time.monotonic()
        for job in pending:
            if now > job.deadline:
                job.finish({"ok": False, "error": "请求超时"})
                continue
            request = dict(job.request)
            request["_deadline"] = job.deadline
            try:
                reply = handler(request, Deferred(job))
            except Exception as exc:            # a CLI call must never break the UI
                reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            handled += 1
            if reply is None:
                self._open.append(job)
            else:
                job.finish(reply)
        return handled

    @property
    def busy(self) -> bool:
        return bool(self._open)
