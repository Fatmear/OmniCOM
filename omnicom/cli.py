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
import json
import os
import re
import sys
import time
from typing import Any, Callable, Iterable, Optional

from . import __version__, paths
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
    _emit({"ok": False, "error": str(message), **extra})
    return EXIT_FAIL


# --------------------------------------------------------------------- session

class PortSession:
    """Owns a port for the duration of one CLI command."""

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
                self.recorder.write(data)
            self.buffer += data
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
        try:
            self.recorder.stop(silent=True)
        except Exception:
            pass
        self.manager.close()
        self.manager.shutdown()

    def __enter__(self) -> "PortSession":
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- io -----------------------------------------------------------------
    def write(self, data: bytes) -> int:
        self.manager.write(data)
        return len(data)

    def stats(self) -> dict:
        return self.manager.stats()

    def take(self) -> bytes:
        data = bytes(self.buffer)
        self.buffer.clear()
        return data

    def read_for(self, duration: float, max_bytes: int = 0,
                 until: Optional[Callable[[bytes], bool]] = None,
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


# ------------------------------------------------------------------- helpers

def serial_opts(args) -> dict:
    return {
        "baudrate": int(args.baud),
        "bytesize": int(args.bytesize),
        "stopbits": float(args.stopbits),
        "parity": str(args.parity).upper()[:1],
        "rtscts": args.flow == "rtscts",
        "xonxoff": args.flow == "xonxoff",
        "dtr": not args.no_dtr,
        "rts": not args.no_rts,
        "autoReconnect": not args.no_auto_reconnect,
    }


def payload_from_args(data: Optional[str], hex_data: Optional[str],
                      line_end: str, decode: bool = True) -> tuple[bytes, str]:
    """Build the wire payload + a human description of it."""
    if hex_data:
        parsed = parse_hex(hex_data)
        if parsed is None:
            raise ValueError(f"HEX 内容不合法：{hex_data!r}")
        return parsed, f"hex:{len(parsed)}B"
    text = data or ""
    if decode:
        body = decode_escapes(text)
    else:
        body = text
    ending = LINE_ENDS.get(line_end, b"")
    return body.encode("utf-8") + ending, f"text:{len(body)}B+{len(ending)}B"


def wait_until_contains(needles: Iterable[str], regex: Optional[str],
                        buffer: bytes) -> bool:
    text = buffer.decode("utf-8", "replace")
    if regex:
        try:
            if re.search(regex, text):
                return True
        except re.error:
            return False
    for needle in needles:
        if needle and needle in text:
            return True
    return False


# ------------------------------------------------------------------- commands

def cmd_ports(args) -> int:
    ports = list_serial_ports()
    if args.demo:
        ports = ports + [{
            "name": DEMO_PORT,
            "description": "虚拟演示串口（AT 回环 + 遥测）",
            "hwid": "DEMO\\OmniCOM",
            "vid": None, "pid": None, "serial": "DEMO-0001",
            "manufacturer": "OmniCOM", "product": "Virtual Demo Port",
        }]
    _emit({"ok": True, "count": len(ports), "ports": ports,
           "serial_available": available()})
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
    })
    return EXIT_OK


def cmd_send(args) -> int:
    try:
        payload, kind = payload_from_args(args.data, args.hex, args.line_end)
    except ValueError as exc:
        return fail(str(exc))
    if not payload:
        return fail("没有要发送的内容")

    session = PortSession(args.port, serial_opts(args))
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
    session = PortSession(args.port, serial_opts(args))
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
    session = PortSession(args.port, serial_opts(args))
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
        _emit({"ok": True, "done": True, "lines": lines_seen, "stats": session.stats()})
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
    session = PortSession(args.port, serial_opts(args))
    try:
        session.open(args.open_timeout)
        started = time.monotonic()
        session.manager.send_file(data, os.path.basename(args.file))
        elapsed = max(1e-6, time.monotonic() - started)
        _emit({
            "ok": True,
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

    session = PortSession(args.port, serial_opts(args))
    logs: list[str] = []

    def log(text: str) -> None:
        logs.append(text)
        if args.verbose:
            _hint("  " + text)

    try:
        session.open(args.open_timeout)
        started = time.monotonic()
        result = run_transfer(session.manager, args.proto, args.dir, name=name, data=data,
                              log=log, save_dir=args.save_dir)
        elapsed = max(1e-6, time.monotonic() - started)
        files = result.get("files") or []
        _emit({
            "ok": True,
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
    except Exception as exc:  # noqa: BLE001 - report every protocol failure as JSON
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

    session = PortSession(port, opts)
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


def _load_script(args) -> Optional[dict]:
    raw: str
    if args.script in (None, "-"):
        raw = sys.stdin.read()
    else:
        try:
            with open(args.script, "r", encoding="utf-8") as handle:
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
        session.manager.send_file(data, os.path.basename(step["send_file"]))
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
            result = run_transfer(session.manager, proto, direction, name=name, data=data,
                                  log=logs.append, save_dir=spec.get("save_dir"))
        except Exception as exc:  # noqa: BLE001
            return {"step": "transfer", "ok": False,
                    "error": f"{type(exc).__name__}: {exc}", "log": logs}
        return {"step": "transfer", "ok": True, "result": result, "log": logs}

    if "reset_input" in step:
        session.take()
        return {"step": "reset_input", "ok": True}

    return {"step": "unknown", "ok": False,
            "error": f"无法识别的步骤：{sorted(step)}"}


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
    except Exception as exc:  # noqa: BLE001
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

    def add_serial(p, default_port: Optional[str] = None) -> None:
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
        p.add_argument("--no-dtr", action="store_true", help="打开后拉低 DTR")
        p.add_argument("--no-rts", action="store_true", help="打开后拉低 RTS")
        p.add_argument("--no-auto-reconnect", action="store_true", help="关闭拔插自动重连")
        p.add_argument("--open-timeout", type=float, default=6.0, help="打开超时秒数")

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


def main(argv: Optional[list[str]] = None) -> int:
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
    except Exception as exc:  # noqa: BLE001 - never crash without JSON
        return fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
