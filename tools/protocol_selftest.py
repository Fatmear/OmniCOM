"""In-memory loopback self-test for the X/Y/ZMODEM engines.

No hardware and no serial port required: two pipes are cross-connected so the
sender and receiver talk to each other through the real engines.  Chunks are
fragmented into 64-byte pieces to mimic USB-serial packetisation.

    python tools/protocol_selftest.py
"""

from __future__ import annotations

import os
import queue
import random
import sys
import tempfile
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from omnicom.proto import xmodem, ymodem, zmodem  # noqa: E402
from omnicom.proto.common import Link  # noqa: E402
from omnicom.proto.crc import crc16, crc32  # noqa: E402
from omnicom.proto.crc import crc16_seal, crc32_seal  # noqa: E402


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


def run_pair(send_fn, recv_fn, timeout: float = 60.0) -> tuple[dict, dict]:
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


CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def test_crc() -> None:
    print("CRC primitives")
    check("CRC16/XMODEM 向量 123456789 -> 0x31C3", crc16(b"123456789") == 0x31C3, hex(crc16(b"123456789")))
    check("CRC32 (lrzsz 风格) 向量 123456789 -> 0x340BC6D9",
          crc32(b"123456789") == 0x340BC6D9, hex(crc32(b"123456789")))

    from omnicom.proto.crc import crc16_seal, crc32_seal
    sample = b"AT+CGMR\r\n\x18\x11\x13binary"
    check("CRC16 封尾后累加为 0", crc16(sample + crc16_seal(sample)) == 0)
    check("CRC32 封尾后累加为 0xDEBB20E3", crc32(sample + crc32_seal(sample)) == 0xDEBB20E3)


def test_xmodem(payload: bytes, block_size: int, label: str) -> None:
    print(f"XMODEM {block_size}B · {label}")
    sent, received = run_pair(
        lambda link: xmodem.send(link, payload, block_size=block_size, timeout=15.0, name=label),
        lambda link: xmodem.receive(link, timeout=15.0, name=label),
        timeout=90,
    )
    data = received["data"][:len(payload)]
    check(f"XMODEM {block_size}B 内容一致（{len(payload)} 字节）", data == payload,
          f"rx={len(received['data'])}")
    check(f"XMODEM {block_size}B 计数一致", sent["bytes"] == len(payload), str(sent))


def test_ymodem(payload: bytes) -> None:
    print("YMODEM")
    sent, received = run_pair(
        lambda link: ymodem.send(link, payload, name="firmware.bin", timeout=15.0),
        lambda link: ymodem.receive(link, timeout=15.0),
        timeout=90,
    )
    files = received["files"]
    check("YMODEM 收到 1 个文件", len(files) == 1, str(len(files)))
    check("YMODEM 文件名保留", bool(files) and files[0]["name"] == "firmware.bin",
          files[0]["name"] if files else "-")
    check("YMODEM 内容一致", bool(files) and files[0]["data"] == payload,
          f"rx={len(files[0]['data']) if files else 0}")
    check("YMODEM 发送计数", sent["bytes"] == len(payload), str(sent))


def test_zmodem(payload: bytes, label: str) -> None:
    print(f"ZMODEM · {label}（{len(payload)} 字节）")
    with tempfile.TemporaryDirectory() as tmp:
        sent, received = run_pair(
            lambda link: zmodem.ZModem(link, timeout=20.0).send("payload.bin", payload),
            lambda link: zmodem.ZModem(link, timeout=20.0).receive(save_dir=tmp),
            timeout=120,
        )
        files = received["files"]
        check(f"ZMODEM 收到文件（{label}）", len(files) == 1, str(len(files)))
        if files:
            with open(files[0]["path"], "rb") as handle:
                disk = handle.read()
            check(f"ZMODEM 落盘内容一致（{label}）", disk == payload, f"rx={len(disk)}")
        check(f"ZMODEM 发送计数（{label}）", sent["bytes"] == len(payload), str(sent))


def main() -> int:
    started = time.time()
    test_crc()
    random.seed(20261027)
    test_xmodem(bytes(random.getrandbits(8) for _ in range(5000)), 1024, "纯随机 5KB")
    test_xmodem(bytes(range(256)) * 3, 128, "全字节表 128B")
    test_xmodem(b"", 1024, "空文件")
    test_ymodem(tricky_payload(3000))
    test_zmodem(tricky_payload(4096), "特殊字符 4KB")
    test_zmodem(tricky_payload(70000, seed=11), "特殊字符 70KB")
    test_zmodem(b"", "空文件")

    failed = [item for item in CHECKS if not item[1]]
    print()
    print(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} 项通过，用时 {time.time() - started:.1f}s")
    if failed:
        print("失败项：")
        for name, _ok, detail in failed:
            print(f"  - {name} {detail}")
        return 1
    print("ALL PROTOCOL SELFTESTS PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - print the stack for debugging
        traceback.print_exc()
        sys.exit(2)
