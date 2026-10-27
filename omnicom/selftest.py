"""Hardware-free verification owned by the package.

Both the CLI (``omnicom-cli selftest``) and the GUI acceptance test import these
checks, so the same proof runs everywhere.  Qt is *not* imported here.

Protocol checks cross-connect two in-memory pipes: the real sender engine talks
to the real receiver engine, with 64-byte fragmentation to mimic USB-serial
packetisation and a payload stuffed with ZMODEM-special bytes.
"""

from __future__ import annotations

import queue
import random
import tempfile
import threading
from typing import Callable, Optional

from .proto import xmodem, ymodem, zmodem
from .proto.common import Link
from .proto.crc import crc16, crc16_seal, crc32, crc32_seal


# ------------------------------------------------------------------ harness

class Pipe:
    """One-way byte pipe with 64-byte fragmentation."""

    def __init__(self) -> None:
        self.queue: "queue.Queue[bytes]" = queue.Queue()

    def put(self, data: bytes) -> None:
        if not data:
            return
        for start in range(0, len(data), 64):
            self.queue.put(bytes(data[start:start + 64]))

    def get(self, timeout: float) -> bytes:
        try:
            return self.queue.get(timeout=max(0.001, timeout))
        except queue.Empty:
            return b""


class PipeLink(Link):
    def __init__(self, rx: Pipe, tx: Pipe) -> None:
        self.rx = rx
        self.tx = tx
        self._leftover = b""

    def write(self, data: bytes) -> None:
        self.tx.put(bytes(data))

    def read(self, size: int, timeout: float) -> bytes:
        if self._leftover:
            data, self._leftover = self._leftover, b""
        else:
            data = self.rx.get(timeout)
        if len(data) > size:
            self._leftover = data[size:]
            return data[:size]
        return data

    def purge(self) -> None:
        self._leftover = b""
        while True:
            try:
                self.rx.queue.get_nowait()
            except queue.Empty:
                return


def run_pair(send_fn: Callable[[Link], dict], recv_fn: Callable[[Link], dict],
             timeout: float = 120.0) -> tuple[dict, dict]:
    """Run sender and receiver concurrently over a cross-connected pipe pair."""
    ab, ba = Pipe(), Pipe()
    sender_link = PipeLink(rx=ba, tx=ab)
    receiver_link = PipeLink(rx=ab, tx=ba)
    result: dict = {}
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            result.update(send_fn(sender_link) or {})
        except BaseException as exc:  # noqa: BLE001 - report anything
            errors.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        received = recv_fn(receiver_link)
    finally:
        thread.join(timeout)
    if errors:
        raise errors[0]
    if thread.is_alive():
        raise TimeoutError("发送线程未在超时内结束（协议死锁）")
    return result, received


def tricky_payload(size: int, seed: int = 7) -> bytes:
    """Data packed with ZMODEM-special bytes (ZDLE, XON, XOFF, CR, 0x10, 0xFF)."""
    special = bytes((0x18, 0x11, 0x13, 0x0D, 0x0A, 0x10, 0x7F, 0xFF, 0x00, 0x40, 0x91, 0x93))
    rng = random.Random(seed)
    out = bytearray()
    while len(out) < size:
        if rng.random() < 0.5:
            out += special
        else:
            out += bytes(rng.getrandbits(8) for _ in range(rng.randrange(1, 32)))
    return bytes(out[:size])


# -------------------------------------------------------------------- checks

def _add(checks: list[dict], name: str, ok: bool, detail: object = None) -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})


def run_protocol_checks(verbose: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Return a list of ``{name, ok, detail}`` for the X/Y/ZMODEM engines."""
    checks: list[dict] = []

    def say(text: str) -> None:
        if verbose:
            verbose(text)

    # -- CRC primitives
    say("CRC primitives")
    _add(checks, "CRC16/XMODEM 向量 123456789 -> 0x31C3",
         crc16(b"123456789") == 0x31C3, hex(crc16(b"123456789")))
    _add(checks, "CRC32 向量 123456789 -> 0x340BC6D9",
         crc32(b"123456789") == 0x340BC6D9, hex(crc32(b"123456789")))
    sample = b"AT+CGMR\r\n\x18\x11\x13binary"
    _add(checks, "CRC16 封尾后累加为 0", crc16(sample + crc16_seal(sample)) == 0)
    _add(checks, "CRC32 封尾后累加为 0xDEBB20E3",
         crc32(sample + crc32_seal(sample)) == 0xDEBB20E3)

    # -- XMODEM
    random.seed(20261027)
    payload = bytes(random.getrandbits(8) for _ in range(5000))
    for block_size, data, label in ((1024, payload, "5KB"),
                                    (128, bytes(range(256)) * 3, "全字节表"),
                                    (1024, b"", "空文件")):
        name = f"XMODEM {block_size}B · {label}"
        say(name)
        try:
            sent, received = run_pair(
                lambda link, bs=block_size, d=data, lb=label:
                    xmodem.send(link, d, block_size=bs, timeout=15.0, name=lb),
                lambda link: xmodem.receive(link, timeout=15.0),
            )
            got = (received.get("data") or b"")[:len(data)]
            _add(checks, f"{name} 内容一致", got == data,
                 f"rx={len(received.get('data') or b'')}")
            _add(checks, f"{name} 计数一致", sent.get("bytes") == len(data), str(sent.get("bytes")))
        except Exception as exc:  # noqa: BLE001
            _add(checks, f"{name} 内容一致", False, f"{type(exc).__name__}: {exc}")

    # -- YMODEM
    say("YMODEM")
    ydata = tricky_payload(3000)
    try:
        sent, received = run_pair(
            lambda link: ymodem.send(link, ydata, name="firmware.bin", timeout=15.0),
            lambda link: ymodem.receive(link, timeout=15.0),
        )
        files = received.get("files") or []
        _add(checks, "YMODEM 收到 1 个文件", len(files) == 1, len(files))
        _add(checks, "YMODEM 文件名保留",
             bool(files) and files[0]["name"] == "firmware.bin",
             files[0]["name"] if files else "-")
        _add(checks, "YMODEM 内容一致", bool(files) and files[0]["data"] == ydata,
             f"rx={len(files[0]['data']) if files else 0}")
        _add(checks, "YMODEM 发送计数", sent.get("bytes") == len(ydata), sent.get("bytes"))
    except Exception as exc:  # noqa: BLE001
        _add(checks, "YMODEM 内容一致", False, f"{type(exc).__name__}: {exc}")

    # -- ZMODEM
    for data, label in ((tricky_payload(4096), "特殊字符 4KB"),
                        (tricky_payload(70000, seed=11), "特殊字符 70KB"),
                        (b"", "空文件")):
        name = f"ZMODEM · {label}"
        say(name)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                sent, received = run_pair(
                    lambda link, d=data: zmodem.ZModem(link, timeout=20.0).send("payload.bin", d),
                    lambda link: zmodem.ZModem(link, timeout=20.0).receive(save_dir=tmp),
                )
                files = received.get("files") or []
                _add(checks, f"{name} 收到文件", len(files) == 1, len(files))
                if files:
                    with open(files[0]["path"], "rb") as handle:
                        disk = handle.read()
                    _add(checks, f"{name} 落盘内容一致", disk == data, f"rx={len(disk)}")
                _add(checks, f"{name} 发送计数", sent.get("bytes") == len(data), sent.get("bytes"))
        except Exception as exc:  # noqa: BLE001
            _add(checks, f"{name} 落盘内容一致", False, f"{type(exc).__name__}: {exc}")

    return checks
