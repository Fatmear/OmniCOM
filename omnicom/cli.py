"""OmniCOM command line interface — built for AI agents and scripts.

Design rules (do not break them: agents depend on them):
1. **stdout carries exactly one JSON document** (or NDJSON for ``monitor``).
   Everything human-oriented goes to stderr, so `cmd | jq` never breaks.
2. **Every result has ``ok``** (bool) and, on failure, ``error`` (string).
   Exit code is 0 on success, 1 on failure, 2 on bad usage.
3. **No interactive prompts, no TTY requirements.** Safe to run from a tool call.
4. **``--port DEMO``** runs against the built-in virtual device, so an agent can
   exercise the whole surface with no hardware attached.
5. Units are explicit in the key name: ``*_ms`` for milliseconds, ``*_bytes`` for
   byte counts, ``*_b64`` for base64 payloads.

See ``docs/CLI.md`` for the full reference.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import re
import sys
import time
from collections.abc import Callable, Iterable
from typing import Any

from . import __version__, control, paths
from .commands import LINE_ENDS, decode_escapes, parse_hex
from .serial_port import DEMO_PORT, PortManager, PortNotOpen, Recorder, available, list_serial_ports
from .transfer import run_transfer

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


# --------------------------------------------------------------------- output

def _emit(payload: dict) -> None:
    """Write one JSON document to stdout."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _hint(text: str) -> None:
    """Human channel (stderr) — agents may ignore it."""
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def describe(data: bytes, limit: int = 0) -> dict:
    """Byte-safe description of a payload: length, hex, text, printable text."""
    total = len(data)
    body = data[:limit] if limit and total > limit else data
    text = body.decode("utf-8", "replace")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return {
        "bytes": total,
        "truncated": bool(limit and total > limit),
        "hex": body.hex(" "),
        "text": text,
        "escaped": text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t"),
        "lines": lines,
        "b64": base64.b64encode(body).decode("ascii"),
    }


def fail(message: str, **extra: Any) -> int:
    # ``ok``/``error`` are written **last**: sending them first let a payload
    # that carried ``ok: True`` (send/read echo their result) override the
    # verdict, so a failed command printed ``ok:true`` with exit code 1 and
    # broke the documented contract (L-143).
    _emit({**extra, "error": str(message), "ok": False})
    return EXIT_FAIL


# --------------------------------------------------------------------- session

class PortSession:
    """Owns a port **itself**, directly through pyserial.

    Used when no window is running, or when ``--direct`` asks for exclusive
    access.  :class:`WindowSession` is the default whenever a window exists.
    """

    mode = "direct"

    def __init__(self, port: str, opts: dict, demo_hint: bool = False) -> None:
        self.port = port
        self.opts = opts
        self.buffer = bytearray()
        self.notices: list[str] = []
        self.errors: list[str] = []
        self.demo = port.upper() == DEMO_PORT
        self.manager = PortManager(self._on_event, demo=self.demo or demo_hint)
        self.recorder = Recorder(paths.logs_dir(), self._on_event)

    # -- events -------------------------------------------------------------
    def _on_event(self, event: dict) -> None:
        kind = event.get("t")
        if kind == "rx":
            data = event["d"]
            if self.recorder.active:
                self.recorder.write(data, "rx")
            self.buffer += data
        elif kind == "tx":
            if self.recorder.active:
                self.recorder.write(event.get("d") or b"", "tx")
        elif kind == "notice":
            text = event.get("text", "")
            self.notices.append(text)
            if event.get("level") == "error":
                self.errors.append(text)

    # -- lifecycle ----------------------------------------------------------
    def open(self, timeout: float = 6.0) -> None:
        if self.demo and not list_serial_ports():
            pass  # DEMO works without any physical port
        self.manager.open(self.port, self.opts)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.manager.status()
            if status.get("connected"):
                return
            if status.get("error") and not status.get("connected"):
                raise PortNotOpen(str(status["error"]))
            time.sleep(0.03)
        raise PortNotOpen(f"打开 {self.port} 超时")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.recorder.stop(silent=True)
        self.manager.close()
        self.manager.shutdown()

    def __enter__(self) -> PortSession:
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- io -----------------------------------------------------------------
    def write(self, data: bytes) -> int:
        self.manager.write(data)
        return len(data)

    def send_file(self, path: str = "", data: bytes = b"", name: str = "") -> dict:
        """Send a whole file verbatim (progress goes to whoever listens)."""
        if path and not data:
            try:
                with open(path, "rb") as handle:
                    data = handle.read()
            except OSError as exc:
                raise PortNotOpen(f"读取文件失败：{exc}") from exc
        label = name or (os.path.basename(path) if path else "file.bin")
        return self.manager.send_file(data, label)

    def transfer(self, proto: str, direction: str, name: str = "", path: str = "",
                 data: bytes = b"", save_dir: str | None = None,
                 log: Callable[[str], None] | None = None) -> dict:
        """X/Y/ZMODEM in this process (identical code to the GUI)."""
        if path and not data:
            try:
                with open(path, "rb") as handle:
                    data = handle.read()
            except OSError as exc:
                raise PortNotOpen(f"读取文件失败：{exc}") from exc
        label = name or (os.path.basename(path) if path else "file.bin")
        return run_transfer(self.manager, proto, direction, name=label, data=data,
                            log=log, save_dir=save_dir)

    def stats(self) -> dict:
        return self.manager.stats()

    def take(self) -> bytes:
        data = bytes(self.buffer)
        self.buffer.clear()
        return data

    def read_for(self, duration: float, max_bytes: int = 0,
                 until: Callable[[bytes], bool] | None = None,
                 poll: float = 0.02) -> bytes:
        """Collect bytes for ``duration`` seconds (or until ``until`` is true)."""
        deadline = time.monotonic() + max(0.0, duration)
        while True:
            if until is not None and until(bytes(self.buffer)):
                break
            if max_bytes and len(self.buffer) >= max_bytes:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(poll)
        return self.take()


class _WindowRecorder:
    """Capture control of the *window*, so a CLI run records the same file."""

    def __init__(self, session: WindowSession) -> None:
        self._session = session
        self._state: dict = {}

    def _call(self, action: str) -> dict:
        reply = self._session.request("record", action=action)
        self._state = dict(reply.get("record") or {})
        return self._state

    @property
    def active(self) -> bool:
        return bool(self._state.get("active"))

    def start(self) -> dict:
        return self._call("start")

    def stop(self) -> dict:
        return self._call("stop")

    def state(self) -> dict:
        return self._call("state")


class _WindowManager:
    """The few ``PortManager`` calls the script runner makes."""

    def __init__(self, session: WindowSession) -> None:
        self._session = session

    def update_opts(self, patch: dict) -> dict:
        reply = self._session.request("opts", opts=dict(patch or {}))
        return reply.get("opts") or {}

    def stats(self) -> dict:
        return self._session.stats()

    def send_file(self, data: bytes, name: str = "file.bin", cancel=None) -> dict:
        return self._session.send_file(data=data, name=name)


class WindowSession:
    """Drive the port **through the running window** (R-112).

    Every call is a request to the window, so the traffic is the window's own:
    the log shows it, the counters count it, and the boot state is shared.  The
    CLI never touches the device, which is what removes the "port busy" failure
    the user hit when the UI already had COM1 open.
    """

    mode = "window"

    def __init__(self, port: str, opts: dict, client: control.Client,
                 endpoint: dict | None = None) -> None:
        self.port = port
        self.opts = dict(opts or {})
        self.client = client
        #: Which window this session is bound to - reported so a user can see
        #: where the traffic went when several windows are open (R-119).
        self.endpoint = dict(endpoint or client.endpoint or {})
        self.buffer = bytearray()
        self.notices: list[str] = []
        self.errors: list[str] = []
        self._seq = 0
        self._opened_here = False
        self.manager = _WindowManager(self)
        self.recorder = _WindowRecorder(self)

    def _check_binding(self, reply: dict) -> None:
        """End the session when the window no longer owns *our* port.

        A CLI that quietly kept writing after the user closed the port (or
        switched the window to another device) would be indistinguishable from
        a working one - so the binding is verified on every reply and a broken
        binding is reported as an error (R-119).
        """
        if "connected" not in reply:
            return
        if not reply.get("connected"):
            # The window closed the port (the user clicked 关闭串口, the device
            # was unplugged, or a reconnect gave up).  The CLI and the window
            # share one session, so the CLI's binding ends with it instead of
            # silently writing into nothing (R-119).
            raise PortNotOpen(f"窗口已关闭 {self.port}，CLI 绑定结束")
        owner = str(reply.get("owned") or reply.get("port") or "")
        if owner and not control.same_port(owner, self.port):
            raise PortNotOpen(f"窗口已切换到 {owner}，与 {self.port} 的绑定结束")

    # -- transport ---------------------------------------------------------
    def request(self, command: str, timeout: float = 0.0, **args: Any) -> dict:
        """One control round trip; a failure is raised as ``PortNotOpen``."""
        client = self.client
        if timeout and timeout != client.timeout:
            client = control.Client(self.client.endpoint, timeout=timeout)
        reply = client.request(command, **args)
        self._seq = int(reply.get("seq") or self._seq)
        # Check the binding *first*: "串口未打开" is what a closed port looks
        # like, and the user needs to read that the binding ended instead of a
        # generic refusal (R-119).
        self._check_binding(reply)
        if not reply.get("ok"):
            raise PortNotOpen(str(reply.get("error") or "窗口拒绝了请求"))
        return reply

    # -- lifecycle ---------------------------------------------------------
    def open(self, timeout: float = 6.0) -> None:
        reply = self.client.request("open", port=self.port, opts=self.opts,
                                    timeout=float(timeout))
        self._seq = int(reply.get("seq") or 0)
        if not reply.get("ok"):
            self.errors.append(str(reply.get("error") or ""))
            raise PortNotOpen(str(reply.get("error") or f"打开 {self.port} 失败"))
        # Only a port *this* command opened is closed on the way out: a send
        # against the user's own session must not tear their session down.
        self._opened_here = not reply.get("already")
        owner = str(reply.get("port") or "")
        if owner and not control.same_port(owner, self.port):
            raise PortNotOpen(
                f"窗口打开了 {owner}，不是 {self.port}：CLI 不会接管别人的串口")

    def close(self) -> None:
        if not self._opened_here:
            return
        self._opened_here = False
        with contextlib.suppress(control.ControlError, PortNotOpen):
            self.client.request("close", timeout=15.0)

    def __enter__(self) -> WindowSession:
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- io ----------------------------------------------------------------
    def write(self, data: bytes) -> int:
        reply = self.request("send", hex=bytes(data).hex(), since=self._seq)
        self._seq = int(reply.get("seq") or self._seq)
        return int(reply.get("sent_bytes") or len(data))

    def stats(self) -> dict:
        return dict(self.request("stats").get("stats") or {})

    def take(self) -> bytes:
        data = bytes(self.buffer)
        self.buffer.clear()
        return data

    def read_for(self, duration: float, max_bytes: int = 0,
                 until: Callable[[bytes], bool] | None = None,
                 poll: float = 0.02) -> bytes:
        """Collect bytes for ``duration`` seconds, in short window-side slices.

        Slices keep every reply cheap and let ``until`` (an arbitrary predicate
        the script supplied) be evaluated here, where the callable lives.
        """
        deadline = time.monotonic() + max(0.0, duration)
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            piece = max(0.05, min(0.25, remaining)) if remaining > 0 else 0.0
            reply = self.request(
                "read", timeout=max(30.0, piece + 20.0), since=self._seq,
                duration=piece,
                max_bytes=max(0, max_bytes - len(self.buffer)))
            self._seq = int(reply.get("seq") or self._seq)
            chunk = bytes.fromhex(str(reply.get("received_hex") or ""))
            if chunk:
                self.buffer += chunk
            if until is not None and until(bytes(self.buffer)):
                break
            if max_bytes and len(self.buffer) >= max_bytes:
                break
            if time.monotonic() >= deadline:
                break
        return self.take()

    def send_file(self, path: str = "", data: bytes = b"", name: str = "") -> dict:
        args: dict = {"name": name or (os.path.basename(path) if path else "file.bin")}
        if path:
            args["path"] = path
        else:
            args["hex"] = bytes(data).hex()
        reply = self.request("send_file", timeout=900.0, **args)
        return {"bytes": int(reply.get("sent_bytes") or 0),
                "name": args["name"], "elapsed_ms": reply.get("elapsed_ms")}

    def transfer(self, proto: str, direction: str, name: str = "", path: str = "",
                 data: bytes = b"", save_dir: str | None = None,
                 log: Callable[[str], None] | None = None) -> dict:
        args: dict = {"proto": proto, "dir": direction, "name": name}
        if path:
            args["path"] = path
        elif data:
            args["hex"] = bytes(data).hex()
        if save_dir:
            args["save_dir"] = save_dir
        reply = self.request("transfer", timeout=900.0, **args)
        for line in reply.get("log") or []:
            if log is not None:
                log(str(line))
        return {"bytes": int(reply.get("bytes") or 0),
                "files": reply.get("files") or []}


# ------------------------------------------------------------------- helpers

def window_endpoint(pid: int | None = None) -> dict | None:
    """One window's endpoint (``pid``) or the primary one.

    Honours ``--direct`` and ``OMNICOM_CLI_DIRECT``: both mean "do not touch
    any window, own the port yourself".
    """
    if os.environ.get("OMNICOM_CLI_DIRECT"):
        return None
    with contextlib.suppress(Exception):
        return control.read_endpoint(pid)
    return None


def probe_endpoint(endpoint: dict, timeout: float = 6.0) -> dict | None:
    """Ask one window for its state; ``None`` when it does not answer."""
    try:
        return control.Client(endpoint, timeout=timeout).request("state")
    except control.ControlError:
        return None


def pick_endpoint(port: str, pid: int | None = None) -> tuple[dict | None, str]:
    """Choose the window that should serve ``port``.

    Order of preference (R-119):

    1. ``--window PID`` - the user pinned one, obey it or fail;
    2. the window that **already has this port open** - this is the whole
       point: its log, counters and settings are the ones the user is looking
       at, so the CLI must join it rather than tear it down;
    3. a window with nothing open - harmless to open the port there;
    4. otherwise ``None``: every window is busy with a *different* port,
       so the CLI takes the port itself instead of hijacking a session.

    Returns ``(endpoint, why)``; ``why`` explains a ``None``.
    """
    if pid:
        endpoint = window_endpoint(pid)
        return (endpoint, "") if endpoint else (None, f"没有 pid={pid} 的窗口")
    items = control.endpoints()
    if not items:
        return None, "没有正在运行的窗口"
    states = []
    for endpoint in items:
        reply = probe_endpoint(endpoint)
        if reply is not None:
            states.append((endpoint, reply))
    if not states:
        return None, "窗口没有响应"
    for endpoint, reply in states:
        if reply.get("connected") and control.same_port(reply.get("port"), port):
            return endpoint, "owner"
    for endpoint, reply in states:
        if not reply.get("connected") and not reply.get("reconnecting"):
            return endpoint, "idle"
    return None, "所有窗口都在用别的串口"


def new_session(args, port: str, opts: dict) -> PortSession | WindowSession:
    """A session that owns the port - through the matching window when there is one.

    The routing key is the **port name**, not "the window that started first".
    With two windows on COM3 and COM5, ``--port COM5`` lands in the second one
    and its log stays clean; and because a window is never asked to reopen a
    port another window owns, no session gets torn down underneath the user
    (R-119).
    """
    if getattr(args, "direct", False):
        return PortSession(port, opts)
    if not port:
        return PortSession(port, opts)
    endpoint, _why = pick_endpoint(port, getattr(args, "window", None))
    if endpoint is not None:
        with contextlib.suppress(control.ControlError):
            return WindowSession(port, opts, control.Client(endpoint),
                                 endpoint=endpoint)
    return PortSession(port, opts)


def session_via(session) -> dict:
    """``via`` + the window that served the request, for the JSON output."""
    endpoint = getattr(session, "endpoint", None) or {}
    payload = {"via": session.mode}
    if session.mode == "window":
        payload["window_pid"] = endpoint.get("pid")
        payload["window_slot"] = endpoint.get("slot")
        payload["window_serial"] = endpoint.get("serial") or ""
    return payload


def serial_opts(args) -> dict:
    return {
        "baudrate": int(args.baud),
        "bytesize": int(args.bytesize),
        "stopbits": float(args.stopbits),
        "parity": str(args.parity).upper()[:1],
        "rtscts": args.flow == "rtscts",
        "xonxoff": args.flow == "xonxoff",
        "dtr": bool(getattr(args, "dtr", False))
        and not getattr(args, "no_dtr", False),
        "rts": bool(getattr(args, "rts", False))
        and not getattr(args, "no_rts", False),
        "autoReconnect": not args.no_auto_reconnect,
    }


def payload_from_args(data: str | None, hex_data: str | None,
                      line_end: str, decode: bool = True) -> tuple[bytes, str]:
    """Build the wire payload + a human description of it."""
    if hex_data:
        parsed = parse_hex(hex_data)
        if parsed is None:
            raise ValueError(f"HEX 内容不合法：{hex_data!r}")
        return parsed, f"hex:{len(parsed)}B"
    text = data or ""
    body = decode_escapes(text) if decode else text
    ending = LINE_ENDS.get(line_end, b"")
    return body.encode("utf-8") + ending, f"text:{len(body)}B+{len(ending)}B"


def wait_until_contains(needles: Iterable[str], regex: str | None,
                        buffer: bytes) -> bool:
    text = buffer.decode("utf-8", "replace")
    if regex:
        try:
            if re.search(regex, text):
                return True
        except re.error:
            return False
    return any(needle and needle in text for needle in needles)


# ------------------------------------------------------------------- commands

def cmd_ports(args) -> int:
    ports = list_serial_ports()
    if args.demo:
        ports = [*ports, {
            "name": DEMO_PORT,
            "description": "虚拟演示串口（AT 回环 + 遥测）",
            "hwid": "DEMO\\OmniCOM",
            "vid": None, "pid": None, "serial": "DEMO-0001",
            "manufacturer": "OmniCOM", "product": "Virtual Demo Port",
        }]
    _emit({"ok": True, "count": len(ports), "ports": ports,
           "serial_available": available(), "window": _window_info()})
    return EXIT_OK


def cmd_info(args) -> int:
    import serial
    _emit({
        "ok": True,
        "name": "OmniCOM",
        "version": __version__,
        "python": sys.version.split()[0],
        "pyserial": getattr(serial, "__version__", "unknown"),
        "serial_available": available(),
        "data_dir": paths.data_dir(),
        "logs_dir": paths.logs_dir(),
        "received_dir": paths.received_dir(),
        "demo_port": DEMO_PORT,
        "platform": sys.platform,
        # When a window is running the CLI drives *its* port instead of opening
        # the device itself, so this tells an agent where its traffic will show
        # up (R-112).
        "window": _window_info(),
    })
    return EXIT_OK


def cmd_send(args) -> int:
    try:
        payload, kind = payload_from_args(args.data, args.hex, args.line_end)
    except ValueError as exc:
        return fail(str(exc))
    if not payload:
        return fail("没有要发送的内容")

    session = new_session(args, args.port, serial_opts(args))
    try:
        session.open(args.open_timeout)
        if args.drain_ms:
            session.read_for(args.drain_ms / 1000.0)
            session.take()
        started = time.monotonic()
        sent = session.write(payload)
        response = session.read_for(args.wait, max_bytes=args.max_bytes,
                                    until=(lambda buf: wait_until_contains(
                                        args.expect or [], args.expect_regex, buf))
                                    if (args.expect or args.expect_regex) else None)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stats = session.stats()
        info = describe(response, limit=args.limit) if response else describe(b"")
        result = {
            "ok": True,
            **session_via(session),
            "port": args.port,
            "sent_bytes": sent,
            "sent_hex": payload.hex(" "),
            "payload_kind": kind,
            "elapsed_ms": elapsed_ms,
            "response": info,
            "rx_total_bytes": stats.get("rx", 0),
            "tx_total_bytes": stats.get("tx", 0),
            "notices": session.notices,
        }
        if args.expect or args.expect_regex:
            matched = wait_until_contains(args.expect or [], args.expect_regex, response)
            result["matched"] = matched
            _emit(result)
            if not matched:
                return fail("等待预期内容超时", **result)
            return EXIT_OK
        _emit(result)
        return EXIT_OK
    except (PortNotOpen, OSError) as exc:
        return fail(f"串口错误：{exc}")
    finally:
        session.close()


def cmd_read(args) -> int:
    session = new_session(args, args.port, serial_opts(args))
    try:
        session.open(args.open_timeout)
        if args.record:
            session.recorder.start()
        data = session.read_for(args.timeout, max_bytes=args.max_bytes,
                                until=(lambda buf: wait_until_contains(
                                    args.expect or [], args.expect_regex, buf))
                                if (args.expect or args.expect_regex) else None)
        record_state = session.recorder.state()
        if args.record:
            session.recorder.stop()
        _emit({
            "ok": True,
            **session_via(session),
            "port": args.port,
            "elapsed_ms": int(args.timeout * 1000),
            "received": describe(data, limit=args.limit),
            "stats": session.stats(),
            "notices": session.notices,
            "record": record_state if args.record else None,
        })
        return EXIT_OK
    except (PortNotOpen, OSError) as exc:
        return fail(f"串口错误：{exc}")
    finally:
        session.close()


def cmd_monitor(args) -> int:
    """Stream NDJSON lines while the port is open (until duration or signal)."""
    session = new_session(args, args.port, serial_opts(args))
    keywords = [k.lower() for k in (args.grep or [])]
    lines_seen = 0
    try:
        session.open(args.open_timeout)
        partial = ""
        deadline = time.monotonic() + args.duration if args.duration else None
        stopped = False
        while not stopped:
            if deadline is not None and time.monotonic() >= deadline:
                break
            chunk = session.read_for(0.1)
            if not chunk:
                continue
            text = partial + chunk.decode("utf-8", "replace")
            pieces = text.split("\n")
            partial = pieces.pop()
            for line in pieces:
                clean = line.rstrip("\r")
                if keywords and not any(word in clean.lower() for word in keywords):
                    continue
                lines_seen += 1
                _emit({"ok": True, "t_ms": int(time.time() * 1000), "dir": "rx",
                       "line": clean, "escaped": clean.replace("\r", "\\r")})
                if args.limit_lines and lines_seen >= args.limit_lines:
                    stopped = True
                    break
        if partial and not args.require_newline:
            lines_seen += 1
            _emit({"ok": True, "t_ms": int(time.time() * 1000), "dir": "rx",
                   "line": partial, "partial": True})
        _emit({"ok": True, "done": True, **session_via(session), "lines": lines_seen,
               "stats": session.stats()})
        return EXIT_OK
    except (PortNotOpen, OSError) as exc:
        return fail(f"串口错误：{exc}")
    finally:
        session.close()


def cmd_send_file(args) -> int:
    try:
        with open(args.file, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return fail(f"读取文件失败：{exc}")
    session = new_session(args, args.port, serial_opts(args))
    try:
        session.open(args.open_timeout)
        started = time.monotonic()
        session.send_file(path=args.file)
        elapsed = max(1e-6, time.monotonic() - started)
        _emit({
            "ok": True,
            **session_via(session),
            "port": args.port,
            "file": os.path.abspath(args.file),
            "sent_bytes": len(data),
            "elapsed_ms": int(elapsed * 1000),
            "rate_bytes_per_s": int(len(data) / elapsed),
            "stats": session.stats(),
        })
        return EXIT_OK
    except (PortNotOpen, OSError) as exc:
        return fail(f"串口错误：{exc}")
    finally:
        session.close()


def cmd_transfer(args) -> int:
    data = b""
    name = args.name or "file.bin"
    if args.dir == "send":
        if not args.file:
            return fail("发送模式必须提供 --file")
        try:
            with open(args.file, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            return fail(f"读取文件失败：{exc}")
        name = os.path.basename(args.file)

    session = new_session(args, args.port, serial_opts(args))
    logs: list[str] = []

    def log(text: str) -> None:
        logs.append(text)
        if args.verbose:
            _hint("  " + text)

    try:
        session.open(args.open_timeout)
        started = time.monotonic()
        result = session.transfer(args.proto, args.dir, name=name,
                                  path=args.file if args.dir == "send" else "",
                                  data=data, save_dir=args.save_dir, log=log)
        elapsed = max(1e-6, time.monotonic() - started)
        files = result.get("files") or []
        _emit({
            "ok": True,
            **session_via(session),
            "proto": args.proto.upper() + "MODEM",
            "dir": args.dir,
            "bytes": result.get("bytes", len(data)),
            "elapsed_ms": int(elapsed * 1000),
            "rate_bytes_per_s": int(result.get("bytes", len(data)) / elapsed),
            "files": files,
            "log": logs,
            "stats": session.stats(),
        })
        return EXIT_OK
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}", log=logs,
                    stats=session.stats())
    finally:
        session.close()


def cmd_session(args) -> int:
    """Run a scripted list of steps against one port — the main AI entry point."""
    script = _load_script(args)
    if script is None:
        return fail("无法读取脚本")

    steps = script["steps"]
    port = args.port or script.get("port")
    if not port:
        return fail("缺少 --port（或脚本里的 port）")
    opts = serial_opts(args)
    for key in ("baudrate", "bytesize", "stopbits", "parity", "dtr", "rts",
                "rtscts", "xonxoff"):
        if key in script:
            opts[key] = script[key]

    session = new_session(args, port, opts)
    results: list[dict] = []
    overall_ok = True
    try:
        session.open(args.open_timeout)
        for index, step in enumerate(steps):
            outcome = _run_step(session, step, args)
            outcome["index"] = index
            results.append(outcome)
            if not outcome.get("ok", True):
                overall_ok = False
                if not args.continue_on_error:
                    break
        _emit({
            "ok": overall_ok,
            **session_via(session),
            "port": port,
            "steps_total": len(steps),
            "steps_run": len(results),
            "results": results,
            "stats": session.stats(),
        })
        return EXIT_OK if overall_ok else EXIT_FAIL
    except (PortNotOpen, OSError) as exc:
        return fail(f"串口错误：{exc}", results=results)
    finally:
        session.close()


def _load_script(args) -> dict | None:
    raw: str
    if args.script in (None, "-"):
        raw = sys.stdin.read()
    else:
        try:
            with open(args.script, encoding="utf-8") as handle:
                raw = handle.read()
        except OSError:
            return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list):
        return {"steps": parsed}
    if isinstance(parsed, dict):
        steps = parsed.get("steps")
        if isinstance(steps, list):
            return parsed
    return None


def _run_step(session: PortSession, step: dict, args) -> dict:
    """Execute one script step and return its JSON outcome."""
    if not isinstance(step, dict):
        return {"step": "invalid", "ok": False, "error": "步骤必须是对象"}

    if "note" in step:
        return {"step": "note", "ok": True, "note": step["note"]}

    if "delay" in step:
        seconds = float(step["delay"])
        if seconds > 10:
            seconds /= 1000.0        # tolerate ms being passed by mistake
        time.sleep(max(0.0, seconds))
        return {"step": "delay", "ok": True, "seconds": round(seconds, 3)}

    if "set" in step and isinstance(step["set"], dict):
        patch = dict(step["set"])
        session.manager.update_opts(patch)
        return {"step": "set", "ok": True, "applied": patch}

    if "send" in step or "send_hex" in step:
        try:
            payload, kind = payload_from_args(
                step.get("send"), step.get("send_hex"),
                step.get("line_end", "crlf"), decode=not step.get("raw", False))
        except ValueError as exc:
            return {"step": "send", "ok": False, "error": str(exc)}
        if step.get("drain_ms"):
            session.read_for(float(step["drain_ms"]) / 1000.0)
            session.take()
        sent = session.write(payload)
        response = session.read_for(
            float(step.get("wait", 0.0)),
            until=(lambda buf: wait_until_contains(step.get("expect") or [],
                                                   step.get("expect_regex"), buf))
            if (step.get("expect") or step.get("expect_regex")) else None)
        outcome = {
            "step": "send",
            "ok": True,
            "sent_bytes": sent,
            "sent_hex": payload.hex(" "),
            "payload_kind": kind,
            "response": describe(response, limit=int(step.get("limit", 0))),
        }
        if step.get("expect") or step.get("expect_regex"):
            matched = wait_until_contains(step.get("expect") or [],
                                          step.get("expect_regex"), response)
            outcome["matched"] = matched
            if not matched:
                outcome["ok"] = False
                outcome["error"] = "未收到预期内容"
        return outcome

    if "expect" in step or "expect_regex" in step:
        timeout = float(step.get("timeout", 3.0))
        found = session.read_for(
            timeout,
            until=lambda buf: wait_until_contains(step.get("expect") or [],
                                                  step.get("expect_regex"), buf))
        matched = wait_until_contains(step.get("expect") or [],
                                      step.get("expect_regex"), found)
        outcome = {
            "step": "expect",
            "ok": matched,
            "matched": matched,
            "response": describe(found, limit=int(step.get("limit", 0))),
        }
        if not matched:
            outcome["error"] = f"{timeout}s 内未收到预期内容"
        return outcome

    if "drain" in step:
        seconds = float(step["drain"])
        data = session.read_for(seconds, max_bytes=int(step.get("max_bytes", 0)))
        return {"step": "drain", "ok": True,
                "response": describe(data, limit=int(step.get("limit", 0)))}

    if "record" in step:
        if str(step["record"]).lower() in ("start", "on", "1", "true"):
            state = session.recorder.start()
        else:
            state = session.recorder.stop()
        return {"step": "record", "ok": True, "record": state}

    if "send_file" in step:
        try:
            with open(step["send_file"], "rb") as handle:
                data = handle.read()
        except OSError as exc:
            return {"step": "send_file", "ok": False, "error": str(exc)}
        session.send_file(path=step["send_file"])
        return {"step": "send_file", "ok": True, "sent_bytes": len(data)}

    if "transfer" in step and isinstance(step["transfer"], dict):
        spec = step["transfer"]
        proto = spec.get("proto", "x")
        direction = spec.get("dir", "send")
        name = spec.get("name") or "file.bin"
        data = b""
        if direction == "send":
            path = spec.get("file")
            if not path:
                return {"step": "transfer", "ok": False, "error": "缺少 file"}
            try:
                with open(path, "rb") as handle:
                    data = handle.read()
            except OSError as exc:
                return {"step": "transfer", "ok": False, "error": str(exc)}
            name = os.path.basename(path)
        logs: list[str] = []
        try:
            result = session.transfer(proto, direction, name=name,
                                      path=spec.get("file") if direction == "send" else "",
                                      data=data, save_dir=spec.get("save_dir"),
                                      log=logs.append)
        except Exception as exc:
            return {"step": "transfer", "ok": False,
                    "error": f"{type(exc).__name__}: {exc}", "log": logs}
        return {"step": "transfer", "ok": True, "result": result, "log": logs}

    if "reset_input" in step:
        session.take()
        return {"step": "reset_input", "ok": True}

    return {"step": "unknown", "ok": False,
            "error": f"无法识别的步骤：{sorted(step)}"}


# -------------------------------------------------------- window operations

def _need_window(timeout: float = 20.0, pid: int | None = None,
                 port: str = ""):
    """Return ``(client, "")`` for a running window, or ``(None, why)``.

    These commands act on the *window's* port; without a window there is no
    port to act on, because the CLI never holds one between two processes.
    With several windows open, ``port``/``pid`` decide which one: the window
    that already has the port wins, the way it does for ``send`` (R-119).
    """
    if pid or port:
        endpoint, why = pick_endpoint(port, pid)
    else:
        endpoint, why = window_endpoint(), ""
    if endpoint is None:
        return None, (why or "没有正在运行的窗口：先启动 OmniCOM，CLI 就能直接操作它的串口"
                      "（R-112）")
    try:
        return control.Client(endpoint, timeout=timeout), ""
    except control.ControlError as exc:
        return None, str(exc)


def _emit_window(reply: dict, client: control.Client | None = None,
                 **extra: Any) -> int:
    payload = {"ok": bool(reply.get("ok")), "via": "window", **extra}
    if client is not None and client.endpoint:
        payload.setdefault("window_pid", client.endpoint.get("pid"))
        payload.setdefault("window_slot", client.endpoint.get("slot"))
    for key in ("connected", "port", "reconnecting", "error", "opts", "stats",
                "recording", "received_hex", "received_text", "received_bytes",
                "lines", "count", "blocks", "trimmed", "seq", "version"):
        if key in reply:
            payload[key] = reply[key]
    if not reply.get("ok"):
        payload["error"] = str(reply.get("error") or "窗口拒绝了请求")
        _emit(payload)
        return EXIT_FAIL
    _emit(payload)
    return EXIT_OK


def cmd_state(args) -> int:
    client, error = _need_window(pid=getattr(args, "window", None),
                                 port=str(getattr(args, "port", None) or ""))
    if client is None:
        return fail(error)
    with contextlib.suppress(control.ControlError):
        return _emit_window(client.request("state"), client)
    return fail("窗口没有响应")


def cmd_open(args) -> int:
    client, error = _need_window(float(args.open_timeout) + 5.0,
                                 pid=getattr(args, "window", None),
                                 port=str(getattr(args, "port", None) or ""))
    if client is None:
        return fail(error)
    try:
        reply = client.request("open", port=args.port, opts=serial_opts(args),
                               timeout=args.open_timeout)
    except control.ControlError as exc:
        return fail(str(exc))
    return _emit_window(reply, client)


def cmd_close(args) -> int:
    client, error = _need_window(15.0, pid=getattr(args, "window", None),
                                 port=str(getattr(args, "port", None) or ""))
    if client is None:
        return fail(error)
    try:
        reply = client.request("close")
    except control.ControlError as exc:
        return fail(str(exc))
    return _emit_window(reply, client)


def cmd_log(args) -> int:
    client, error = _need_window(pid=getattr(args, "window", None),
                                 port=str(getattr(args, "port", None) or ""))
    if client is None:
        return fail(error)
    try:
        reply = client.request("log", limit=args.limit, grep=args.grep or "")
    except control.ControlError as exc:
        return fail(str(exc))
    return _emit_window(reply, client, lines=reply.get("lines") or [])


def cmd_stats(args) -> int:
    client, error = _need_window(pid=getattr(args, "window", None),
                                 port=str(getattr(args, "port", None) or ""))
    if client is None:
        return fail(error)
    try:
        reply = client.request("stats", reset=bool(args.reset))
    except control.ControlError as exc:
        return fail(str(exc))
    return _emit_window(reply, client)


# ------------------------------------------------------------------ selftest

def cmd_selftest(args) -> int:
    """End-to-end check that needs no hardware.

    Two layers:
    * protocol engines proven over an in-memory loopback (real symmetry proof);
    * a live DEMO-port session proving open / send / expect / record / file send.
    """
    from .selftest import run_protocol_checks

    checks: list[dict] = []

    def record(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    # ---- layer 1: protocol engines (no port required)
    for item in run_protocol_checks():
        record("protocol: " + item["name"], item["ok"], item.get("detail"))

    # ---- layer 2: live DEMO port
    opts = {"baudrate": 115200, "bytesize": 8, "stopbits": 1, "parity": "N",
            "rtscts": False, "xonxoff": False, "dtr": True, "rts": True,
            "autoReconnect": True}

    session = PortSession(DEMO_PORT, opts)
    try:
        session.open()
        record("demo: 打开 DEMO 端口", session.manager.status().get("connected"))

        session.write(b"AT\r\n")
        reply = session.read_for(1.5)
        record("demo: AT -> OK", b"OK" in reply, describe(reply)["escaped"][:80])

        session.write(b"AT+CGMR\r\n")
        reply = session.read_for(1.5)
        record("demo: AT+CGMR -> 版本号", b"DEMO" in reply, describe(reply)["escaped"][:80])

        payload = bytes(range(256)) * 8
        before = session.stats().get("tx", 0)
        session.manager.send_file(payload, "selftest.bin")
        after = session.stats().get("tx", 0)
        record("demo: 原始文件发送", after - before == len(payload),
               {"sent": after - before, "expected": len(payload)})

        session.recorder.start()
        session.read_for(1.0)
        state = session.recorder.stop()
        record("demo: 接收记录到文件", state.get("bytes", 0) > 0, state.get("path"))
    except Exception as exc:
        record("demo: 会话", False, f"{type(exc).__name__}: {exc}")
    finally:
        session.close()

    ok = all(item["ok"] for item in checks) and len(checks) >= 10
    _emit({
        "ok": ok,
        "checks": checks,
        "passed": sum(1 for item in checks if item["ok"]),
        "total": len(checks),
        "note": "协议传输由内存环回证明；DEMO 端口只验证收发/记录，不充当协议对端",
    })
    return EXIT_OK if ok else EXIT_FAIL


# ---------------------------------------------------------------------- parser

def _window_info() -> dict:
    """Where this CLI may send its traffic: every window, plus the primary.

    Each window publishes its own endpoint and the serial port it holds, so
    the caller can see exactly which window a port belongs to (R-119).
    """
    if os.environ.get("OMNICOM_CLI_DIRECT"):
        return {"running": False, "mode": "direct", "windows": []}
    items = control.endpoints()
    if not items:
        return {"running": False, "mode": "direct", "windows": []}
    first = items[0]
    return {"running": True, "mode": "window", "pid": first.get("pid"),
            "version": first.get("version"),
            "port": first.get("serial") or "",
            "windows": [{"pid": item.get("pid"), "slot": item.get("slot"),
                         "serial": item.get("serial") or "",
                         "connected": bool(item.get("connected"))}
                        for item in items]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omnicom-cli",
        description="OmniCOM CLI — 面向 AI 的串口调试接口（stdout 只输出 JSON）",
        epilog="完整说明见 docs/CLI.md。示例：\n"
               "  python -m omnicom.cli ports --demo\n"
               "  python -m omnicom.cli send --port DEMO --data AT --expect OK\n"
               "  python -m omnicom.cli session --port DEMO --script steps.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"OmniCOM CLI {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_serial(p, default_port: str | None = None) -> None:
        p.add_argument("--port", default=default_port,
                       help="串口名，如 COM3；用 DEMO 走虚拟设备")
        p.add_argument("--baud", type=int, default=115200, help="波特率（默认 115200）")
        p.add_argument("--bytesize", type=int, default=8, choices=[5, 6, 7, 8],
                       help="数据位（默认 8）")
        p.add_argument("--stopbits", default="1", choices=["1", "1.5", "2"],
                       help="停止位（默认 1）")
        p.add_argument("--parity", default="N", choices=["N", "E", "O", "M", "S"],
                       help="校验位（默认 N）")
        p.add_argument("--flow", default="none", choices=["none", "rtscts", "xonxoff"],
                       help="流控（默认 none）")
        p.add_argument("--dtr", action="store_true",
                       help="打开后拉高 DTR（默认关闭，避免复位板子）")
        p.add_argument("--rts", action="store_true",
                       help="打开后拉高 RTS（默认关闭）")
        p.add_argument("--no-dtr", action="store_true",
                       help="保持 DTR 关闭（默认行为，兼容旧脚本）")
        p.add_argument("--no-rts", action="store_true",
                       help="保持 RTS 关闭（默认行为，兼容旧脚本）")
        p.add_argument("--no-auto-reconnect", action="store_true", help="关闭拔插自动重连")
        p.add_argument("--open-timeout", type=float, default=6.0, help="打开超时秒数")
        p.add_argument("--direct", action="store_true",
                       help="不经过运行中的窗口，自己独占串口")
        p.add_argument("--window", type=int, default=None, metavar="PID",
                       help="指定由哪个窗口执行（默认：已打开该串口的窗口，"
                            "其次是没有占用串口的窗口）")

    p_ports = sub.add_parser("ports", help="列出串口（JSON）")
    p_ports.add_argument("--demo", action="store_true", help="额外列出虚拟 DEMO 端口")
    p_ports.set_defaults(func=cmd_ports)

    p_info = sub.add_parser("info", help="环境与路径信息")
    p_info.set_defaults(func=cmd_info)

    p_send = sub.add_parser("send", help="发送一行/一段数据，可选等待应答")
    add_serial(p_send)
    p_send.add_argument("--data", help="要发送的文本，支持 \\r \\n \\t \\xNN 转义")
    p_send.add_argument("--hex", help="要发送的 HEX 字节，如 \"01 03 00 00 00 01\"")
    p_send.add_argument("--line-end", default="crlf", choices=list(LINE_ENDS),
                        help="追加的行尾（默认 crlf）")
    p_send.add_argument("--wait", type=float, default=0.0,
                        help="发送后读取应答的秒数（默认 0）")
    p_send.add_argument("--expect", action="append",
                        help="等待应答包含该子串，可重复；未命中则返回失败")
    p_send.add_argument("--expect-regex", help="等待应答匹配该正则")
    p_send.add_argument("--max-bytes", type=int, default=262144, help="应答读取上限")
    p_send.add_argument("--limit", type=int, default=65536,
                        help="JSON 里 text/hex 的最大字节数（0=不限）")
    p_send.add_argument("--drain-ms", type=int, default=0,
                        help="发送前先丢弃已缓存的数据（毫秒）")
    p_send.set_defaults(func=cmd_send)

    p_read = sub.add_parser("read", help="持续读取指定时长")
    add_serial(p_read)
    p_read.add_argument("--timeout", type=float, default=2.0, help="读取秒数（默认 2）")
    p_read.add_argument("--max-bytes", type=int, default=262144)
    p_read.add_argument("--expect", action="append", help="读到该子串就提前返回")
    p_read.add_argument("--expect-regex", help="读到匹配该正则就提前返回")
    p_read.add_argument("--record", action="store_true", help="同时写入记录文件")
    p_read.add_argument("--limit", type=int, default=65536)
    p_read.set_defaults(func=cmd_read)

    def add_window(p, with_port: bool = False) -> None:
        """``--window PID`` (+ optional ``--port``) for a window-only command."""
        p.add_argument("--window", type=int, default=None, metavar="PID",
                       help="指定窗口（默认：当前串口所属的窗口，否则是最早打开的）")
        if with_port:
            p.add_argument("--port", default=None,
                           help="按串口挑窗口：哪个窗口开着这个口就发给它")

    p_state = sub.add_parser("state", help="查看窗口中串口的状态（JSON）")
    add_window(p_state, with_port=True)
    p_state.set_defaults(func=cmd_state)

    p_open = sub.add_parser("open", help="让窗口打开串口（CLI 与界面共享同一个口）")
    add_serial(p_open)
    p_open.set_defaults(func=cmd_open)

    p_close = sub.add_parser("close", help="让窗口关闭串口")
    add_window(p_close, with_port=True)
    p_close.set_defaults(func=cmd_close)

    p_log = sub.add_parser("log", help="读取窗口日志的最后若干行")
    p_log.add_argument("--limit", type=int, default=40, help="最多返回多少行（默认 40）")
    p_log.add_argument("--grep", default="", help="只返回包含该子串的行")
    add_window(p_log, with_port=True)
    p_log.set_defaults(func=cmd_log)

    p_stats = sub.add_parser("stats", help="窗口的收发字节计数")
    p_stats.add_argument("--reset", action="store_true", help="清零后再返回")
    add_window(p_stats, with_port=True)
    p_stats.set_defaults(func=cmd_stats)

    p_mon = sub.add_parser("monitor", help="按行流式输出（NDJSON）")
    add_serial(p_mon)
    p_mon.add_argument("--duration", type=float, default=5.0, help="监听秒数（默认 5）")
    p_mon.add_argument("--grep", action="append", help="只输出包含该关键字的行，可重复")
    p_mon.add_argument("--limit-lines", type=int, default=0, help="最多输出多少行")
    p_mon.add_argument("--require-newline", action="store_true",
                       help="不输出未以换行结尾的残留行")
    p_mon.set_defaults(func=cmd_monitor)

    p_sf = sub.add_parser("send-file", help="原样发送一个文件（不走协议）")
    add_serial(p_sf)
    p_sf.add_argument("--file", required=True)
    p_sf.set_defaults(func=cmd_send_file)

    p_xf = sub.add_parser("transfer", help="X/Y/ZMODEM 传输")
    add_serial(p_xf)
    p_xf.add_argument("--proto", default="x", choices=["x", "y", "z"])
    p_xf.add_argument("--dir", default="send", choices=["send", "recv"])
    p_xf.add_argument("--file", help="发送模式必填")
    p_xf.add_argument("--name", help="覆盖发送时的文件名")
    p_xf.add_argument("--save-dir", help="接收保存目录（默认 omnicom-data/received）")
    p_xf.add_argument("--verbose", action="store_true", help="协议日志打到 stderr")
    p_xf.set_defaults(func=cmd_transfer)

    p_sess = sub.add_parser("session", help="执行脚本化步骤序列（AI 主入口）")
    add_serial(p_sess)
    p_sess.add_argument("--script", default="-",
                        help="脚本 JSON 路径，- 表示从 stdin 读取")
    p_sess.add_argument("--continue-on-error", action="store_true",
                        help="某一步失败后继续执行")
    p_sess.set_defaults(func=cmd_session)

    p_st = sub.add_parser("selftest", help="无硬件自检（DEMO 端口）")
    p_st.set_defaults(func=cmd_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "port", None) is None and args.command in (
            "send", "read", "monitor", "send-file", "transfer", "session"):
        if args.command == "session":
            pass  # session may take the port from the script
        else:
            return fail("缺少 --port")
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return fail("用户中断")
    except Exception as exc:
        return fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
