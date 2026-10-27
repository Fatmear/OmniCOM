"""ZMODEM — lrzsz-compatible subset (CRC16 and CRC32, batch receive).

Frame format follows ``lrzsz`` ``zm.c`` exactly:

* hex header   ``* * ZDLE 'B' hex(type) hex(4 hdr bytes) hex(crc16) CR LF|0x80 XON``
* binary header ``* ZDLE 'A' type hdr[4] crc16`` (all ZDLE-escaped)
* 32-bit header ``* ZDLE 'C' type hdr[4] crc32``
* data subpacket ``<escaped data> ZDLE <ZCRCE|G|Q|W> crc``

CRC16 is accumulated until it reaches 0, CRC32 until 0xDEBB20E3.
"""

from __future__ import annotations

import os
import time
from typing import Callable, Optional

from .common import ByteReader, Link, Progress, TransferAborted, TransferError, logger_cb, progress_cb
from .crc import crc16, crc16_seal, crc32

ZPAD = 0x2A
ZDLE = 0x18
ZBIN = 0x41  # 'A' CRC-16 binary header
ZHEX = 0x42  # 'B' hex header
ZBIN32 = 0x43  # 'C' CRC-32 binary header
ZCRCE = 0x68
ZCRCG = 0x69
ZCRCQ = 0x6A
ZCRCW = 0x6B
ZRUB0 = 0x7F
ZRUB1 = 0xFF
CAN_BYTE = 0x18
XON = 0x11
XOFF = 0x13

ZRQINIT, ZRINIT, ZSINIT, ZACK, ZFILE, ZSKIP, ZNAK, ZABORT, ZFIN, ZRPOS, \
    ZDATA, ZEOF, ZFERR, ZCRC, ZCHALLENGE, ZCOMPL, ZCAN, ZFREECNT, ZCOMMAND, ZSTDERR = range(20)

CANFDX = 0x01
CANOVIO = 0x02
CANFC32 = 0x20

RINIT_FLAGS = CANFDX | CANOVIO | CANFC32
WINDOW_BLOCKS = 8
BLOCK = 1024
MAX_FILE = 512 * 1024 * 1024


class ZModemError(TransferError):
    pass


class _Timeout(Exception):
    pass


class _Abort(Exception):
    pass


class _BadFrame(Exception):
    pass


def stohdr(pos: int) -> list[int]:
    return [pos & 0xFF, (pos >> 8) & 0xFF, (pos >> 16) & 0xFF, (pos >> 24) & 0xFF]


def rclhdr(hdr) -> int:
    return hdr[0] | (hdr[1] << 8) | (hdr[2] << 16) | (hdr[3] << 24)


class ZModem:
    def __init__(self, link: Link, *, timeout: float = 10.0, log=None,
                 progress: Optional[Progress] = None,
                 cancel: Optional[Callable[[], bool]] = None) -> None:
        self.link = link
        self.reader = ByteReader(link)
        self.timeout = timeout
        self.log = logger_cb(log)
        self.progress = progress_cb(progress)
        self.cancel = cancel
        self.tx32 = False
        self.rx32 = False
        self._lastsent = 0

    # ------------------------------------------------------------- plumbing
    def _check_cancel(self) -> None:
        if self.cancel and self.cancel():
            raise TransferAborted("用户取消")

    def _getbyte(self, timeout: float) -> int:
        value = self.reader.getbyte(timeout)
        if value is None:
            raise _Timeout()
        return value

    def _put(self, out: bytearray, c: int) -> None:
        """``zsendline``: escape ZDLE/XON/XOFF/^P and CR-after-'@'."""
        c &= 0xFF
        escape = False
        if c & 0x60:
            escape = False
        elif c in (ZDLE, XON, XOFF, XON | 0x80, XOFF | 0x80, 0x10):
            escape = True
        elif c == 0x0D and (self._lastsent & 0x7F) == 0x40:
            escape = True
        if escape:
            out.append(ZDLE)
            out.append(c ^ 0x40)
            self._lastsent = c ^ 0x40
        else:
            out.append(c)
            self._lastsent = c

    def _put_many(self, out: bytearray, data: bytes) -> None:
        for byte in data:
            self._put(out, byte)

    # ----------------------------------------------------------- tx headers
    def _send_hex_header(self, ftype: int, hdr4) -> None:
        ftype &= 0x7F
        hdr4 = bytes(hdr4)
        fcs = crc16_seal(bytes((ftype,)) + hdr4)
        text = f"{ftype:02x}" + hdr4.hex() + fcs.hex()
        out = bytearray(b"**")
        out += bytes((ZDLE, ZHEX))
        out += text.encode("ascii")
        out += bytes((0x0D, 0x0A | 0x80))
        if ftype not in (ZFIN, ZACK):
            out.append(XON)
        self._lastsent = 0x0A
        self.link.write(bytes(out))

    def _send_bin_header(self, ftype: int, hdr4) -> None:
        data = bytes((ftype,)) + bytes(hdr4)
        out = bytearray((ZPAD, ZDLE))
        if self.tx32:
            out.append(ZBIN32)
            for byte in data:
                self._put(out, byte)
            value = (~crc32(data)) & 0xFFFFFFFF
            for index in range(4):
                self._put(out, (value >> (8 * index)) & 0xFF)
        else:
            out.append(ZBIN)
            for byte in data:
                self._put(out, byte)
            for byte in crc16_seal(data):
                self._put(out, byte)
        self.link.write(bytes(out))

    def _send_data(self, payload: bytes, frameend: int) -> None:
        out = bytearray()
        self._put_many(out, payload)
        out.append(ZDLE)
        out.append(frameend)
        self._lastsent = frameend
        body = payload + bytes((frameend,))
        if self.tx32:
            value = (~crc32(body)) & 0xFFFFFFFF
            for index in range(4):
                self._put(out, (value >> (8 * index)) & 0xFF)
        else:
            for byte in crc16_seal(body):
                self._put(out, byte)
        if frameend == ZCRCW:
            out.append(XON)
        self.link.write(bytes(out))

    def _abort(self) -> None:
        try:
            self.link.write(bytes((CAN_BYTE,)) * 8 + b"\x08" * 8)
        except Exception:
            pass

    # ----------------------------------------------------------- rx plumbing
    def _zdlread(self, timeout: float) -> int:
        """Return a data byte, or ``0x100 | frameend``."""
        while True:
            c = self._getbyte(timeout)
            if c & 0x60:
                return c
            if c == ZDLE:
                c2 = self._getbyte(timeout)
                if c2 == CAN_BYTE:
                    run = 1
                    while True:
                        try:
                            nxt = self._getbyte(1.0)
                        except _Timeout:
                            break
                        if nxt != CAN_BYTE:
                            self.reader.unget(nxt)
                            break
                        run += 1
                    if run >= 3:
                        raise _Abort()
                    continue
                if c2 in (ZCRCE, ZCRCG, ZCRCQ, ZCRCW):
                    return 0x100 | c2
                if c2 == ZRUB0:
                    return 0x7F
                if c2 == ZRUB1:
                    return 0xFF
                if (c2 & 0x60) == 0x40:
                    return c2 ^ 0x40
                continue
            if c in (XON, XOFF, XON | 0x80, XOFF | 0x80):
                continue
            return c

    def _read_byte_data(self, timeout: float) -> int:
        value = self._zdlread(timeout)
        if value > 0xFF:
            raise _BadFrame("意外的数据子包结束符")
        return value

    def _read_header(self, timeout: float) -> Optional[tuple[int, bytes]]:
        deadline = time.monotonic() + timeout
        cans = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                c = self._getbyte(remaining)
            except _Timeout:
                return None
            if c == ZPAD:
                try:
                    second = self._getbyte(1.0)
                except _Timeout:
                    continue
                if second == ZPAD:
                    try:
                        second = self._getbyte(1.0)
                    except _Timeout:
                        continue
                if second != ZDLE:
                    continue
                try:
                    indicator = self._getbyte(2.0)
                except _Timeout:
                    continue
                try:
                    if indicator == ZBIN:
                        return self._read_bin_header(self.timeout, False)
                    if indicator == ZBIN32:
                        return self._read_bin_header(self.timeout, True)
                    if indicator == ZHEX:
                        return self._read_hex_header(self.timeout)
                except _BadFrame as exc:
                    raise ZModemError(str(exc))
                continue
            if c == CAN_BYTE:
                cans += 1
                if cans >= 5:
                    raise _Abort()
                continue
            cans = 0

    def _read_bin_header(self, timeout: float, use32: bool) -> tuple[int, bytes]:
        if use32:
            acc = 0xFFFFFFFF
            ftype = self._read_byte_data(timeout)
            acc = crc32(bytes((ftype,)), acc)
            hdr = bytearray()
            for _ in range(4):
                byte = self._read_byte_data(timeout)
                hdr.append(byte)
                acc = crc32(bytes((byte,)), acc)
            for _ in range(4):
                byte = self._read_byte_data(timeout)
                acc = crc32(bytes((byte,)), acc)
            if acc != 0xDEBB20E3:
                raise _BadFrame("ZMODEM 32 位帧校验失败")
        else:
            acc = 0
            ftype = self._read_byte_data(timeout)
            acc = crc16(bytes((ftype,)), acc)
            hdr = bytearray()
            for _ in range(4):
                byte = self._read_byte_data(timeout)
                hdr.append(byte)
                acc = crc16(bytes((byte,)), acc)
            for _ in range(2):
                byte = self._read_byte_data(timeout)
                acc = crc16(bytes((byte,)), acc)
            if acc != 0:
                raise _BadFrame("ZMODEM 帧校验失败")
        self.rx32 = use32
        return ftype, bytes(hdr)

    def _gethex(self, timeout: float) -> int:
        high = self._getbyte(timeout)
        low = self._getbyte(timeout)
        try:
            return int(bytes((high, low)).decode("ascii"), 16)
        except (ValueError, UnicodeDecodeError) as exc:
            raise _BadFrame(f"ZMODEM 十六进制帧损坏: {exc}") from exc

    def _read_hex_header(self, timeout: float) -> tuple[int, bytes]:
        ftype = self._gethex(timeout)
        acc = crc16(bytes((ftype,)))
        hdr = bytearray()
        for _ in range(4):
            byte = self._gethex(timeout)
            hdr.append(byte)
            acc = crc16(bytes((byte,)), acc)
        for _ in range(2):
            byte = self._gethex(timeout)
            acc = crc16(bytes((byte,)), acc)
        if acc != 0:
            raise _BadFrame("ZMODEM 十六进制帧校验失败")
        # Optional CR LF|0x80 epilogue; push back anything else.
        try:
            epilogue = self._getbyte(0.05)
        except _Timeout:
            return ftype, bytes(hdr)
        if epilogue in (0x0D, 0x0D | 0x80):
            try:
                self._getbyte(0.05)
            except _Timeout:
                pass
        elif epilogue == 0x0A or epilogue == (0x0A | 0x80):
            pass
        else:
            self.reader.unget(epilogue)
        self.rx32 = False
        return ftype, bytes(hdr)

    def _read_header_required(self) -> tuple[int, bytes]:
        result = self._read_header(self.timeout)
        if result is None:
            raise ZModemError("等待 ZMODEM 帧超时")
        return result

    def _read_subpacket(self) -> tuple[bytes, int]:
        buf = bytearray()
        use32 = self.rx32
        acc = 0xFFFFFFFF if use32 else 0
        while True:
            value = self._zdlread(self.timeout)
            if value > 0xFF:
                frameend = value & 0xFF
                break
            buf.append(value)
            acc = crc32(bytes((value,)), acc) if use32 else crc16(bytes((value,)), acc)
            if len(buf) > 16 * 1024:
                raise _BadFrame("数据子包过大")
        acc = crc32(bytes((frameend,)), acc) if use32 else crc16(bytes((frameend,)), acc)
        count = 4 if use32 else 2
        for _ in range(count):
            byte = self._read_byte_data(self.timeout)
            acc = crc32(bytes((byte,)), acc) if use32 else crc16(bytes((byte,)), acc)
        if use32:
            if acc != 0xDEBB20E3:
                raise _BadFrame("数据子包 CRC32 校验失败")
        elif acc != 0:
            raise _BadFrame("数据子包 CRC16 校验失败")
        return bytes(buf), frameend

    # ---------------------------------------------------------------- sender
    def send(self, name: str, data: bytes, **_) -> dict:
        self.reader.drain(0.2)
        self.log(f"ZMODEM 发送 {name}（{len(data)} 字节）")

        got_rinit = False
        for _ in range(20):
            self._check_cancel()
            self._send_hex_header(ZRQINIT, [0, 0, 0, 0])
            result = self._read_header(3.0)
            if result is None:
                continue
            ftype, hdr = result
            if ftype == ZRINIT:
                got_rinit = True
                break
            if ftype in (ZRQINIT, ZNAK, ZACK):
                continue
        if not got_rinit:
            raise ZModemError("等待接收方 ZRINIT 超时")
        self.tx32 = bool(hdr[0] & CANFC32)
        self.log(f"对端 ZRINIT flags=0x{hdr[0]:02x}，数据帧使用 {'CRC32' if self.tx32 else 'CRC16'}")

        base = os.path.basename(name) or "file.bin"
        info = f"{base}\0{len(data)} 0 100644 0\0".encode("utf-8", "replace")

        pos = 0
        accepted = False
        for _ in range(10):
            self._check_cancel()
            self._send_bin_header(ZFILE, [0, 0, 0, 0])
            self._send_data(info, ZCRCW)
            # Wait for ZRPOS.  A stale ZRINIT (receivers advertise several
            # times) must NOT trigger a resend of ZFILE, otherwise both sides
            # desynchronise: keep reading until the deadline instead.
            deadline = time.monotonic() + self.timeout
            resend = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    resend = True
                    break
                result = self._read_header(remaining)
                if result is None:
                    resend = True
                    break
                ftype, hdr = result
                if ftype == ZRPOS:
                    pos = rclhdr(hdr)
                    accepted = True
                    break
                if ftype == ZSKIP:
                    raise TransferAborted("对端跳过此文件")
                if ftype in (ZNAK, ZABORT, ZFERR, ZCAN):
                    resend = True
                    break
                # ZRINIT / ZRQINIT / ZACK / anything else: ignore and keep waiting.
            if accepted:
                break
            if not resend:
                break
        if not accepted:
            raise ZModemError("文件头未被接收方接受")

        total = len(data)
        while True:
            pos = self._send_from(data, pos, total)
            if pos < total:
                continue
            done, new_pos = self._send_eof(total)
            if done:
                break
            pos = new_pos

        for _ in range(5):
            self._check_cancel()
            self._send_hex_header(ZFIN, [0, 0, 0, 0])
            result = self._read_header(5.0)
            if result and result[0] == ZFIN:
                break
        try:
            self.link.write(b"OO")
        except Exception:
            pass
        self.progress(total, total)
        self.log("ZMODEM 发送完成")
        return {"bytes": total, "name": base}

    def _send_from(self, data: bytes, pos: int, total: int) -> int:
        while pos < total:
            self._check_cancel()
            window_start = pos
            self._send_bin_header(ZDATA, stohdr(pos))
            blocks = 0
            while pos < total and blocks < WINDOW_BLOCKS:
                piece = data[pos:pos + BLOCK]
                last = (pos + len(piece) >= total) or (blocks + 1 >= WINDOW_BLOCKS)
                self._send_data(piece, ZCRCW if last else ZCRCG)
                pos += len(piece)
                blocks += 1
                self.progress(pos, total)
            retry_window = False
            while True:
                ftype, hdr = self._read_header_required()
                if ftype == ZACK:
                    break
                if ftype == ZRPOS:
                    return rclhdr(hdr)
                if ftype == ZNAK:
                    retry_window = True
                    break
                if ftype == ZRINIT:
                    break
                if ftype in (ZABORT, ZFERR, ZCAN):
                    raise TransferAborted("对端中止传输")
            if retry_window:
                pos = window_start
        return pos

    def _send_eof(self, total: int) -> tuple[bool, int]:
        for _ in range(10):
            self._check_cancel()
            self._send_hex_header(ZEOF, stohdr(total))
            result = self._read_header(self.timeout)
            if result is None:
                continue
            ftype, hdr = result
            if ftype == ZRINIT:
                return True, total
            if ftype == ZRPOS:
                return False, rclhdr(hdr)
        raise ZModemError("ZEOF 未被接收方确认")

    # -------------------------------------------------------------- receiver
    def receive(self, *, save_dir: str = ".", on_log: Optional[Callable[[str], None]] = None,
                dedupe: bool = True) -> dict:
        self.reader.drain(0.2)
        os.makedirs(save_dir, exist_ok=True)
        files: list[dict] = []
        self.log("ZMODEM 接收：等待发送方")

        ftype = None
        hdr = b""
        for _ in range(10):
            self._check_cancel()
            self._send_hex_header(ZRINIT, [RINIT_FLAGS, 0, 0, 0])
            result = self._read_header(3.0)
            if result is None:
                continue
            ftype, hdr = result
            if ftype == ZFILE:
                break
            if ftype == ZRQINIT:
                continue
            if ftype == ZSINIT:
                self._send_bin_header(ZACK, [0, 0, 0, 0])
                ftype = None
                continue
            if ftype == ZFIN:
                self._send_hex_header(ZFIN, [0, 0, 0, 0])
                try:
                    self.link.write(b"OO")
                except Exception:
                    pass
                return {"files": [], "bytes": 0}
        if ftype != ZFILE:
            raise ZModemError("等待发送方 ZFILE 超时")

        while True:
            info, _end = self._read_subpacket()
            name, size = _parse_info(info)
            self.log(f"接收文件 {name}" + (f"（{size} 字节）" if size else ""))
            self._send_bin_header(ZRPOS, stohdr(0))

            buf = bytearray()
            outcome = self._receive_stream(buf, size)
            if outcome == "file":
                # The sender re-sent ZFILE (it did not see our ZRPOS in time).
                # Its file-info subpacket is next; consume it and start over.
                self.log("检测到重复的文件头，重新同步")
                continue
            if outcome == "fin":
                self._finish_receive()
                break

            path = self._save(name, bytes(buf), save_dir, dedupe)
            files.append({"name": name, "bytes": len(buf), "path": path})
            self.log(f"已保存 {path}（{len(buf)} 字节）")

            # ZEOF seen: tell the sender we are ready for the next file.
            self._send_hex_header(ZRINIT, [RINIT_FLAGS, 0, 0, 0])
            nxt = self._read_header(6.0)
            if nxt is None:
                break
            ntype, _nhdr = nxt
            if ntype == ZFILE:
                continue
            if ntype == ZFIN:
                self._finish_receive()
                break
            if ntype in (ZABORT, ZFERR, ZCAN):
                raise TransferAborted("对端中止传输")
            if ntype == ZRINIT:
                continue
            break

        total = sum(item["bytes"] for item in files)
        self.progress(total, total)
        return {"files": files, "bytes": total}

    def _receive_stream(self, buf: bytearray, expected_size: int) -> str:
        while True:
            self._check_cancel()
            ftype, hdr = self._read_header_required()
            if ftype == ZDATA:
                self._receive_blocks(buf, expected_size)
            elif ftype == ZEOF:
                return "eof"
            elif ftype == ZFIN:
                return "fin"
            elif ftype == ZFILE:
                return "file"
            elif ftype == ZSINIT:
                self._send_bin_header(ZACK, [0, 0, 0, 0])
            elif ftype == ZRPOS:
                self._send_bin_header(ZRPOS, stohdr(len(buf)))
            elif ftype in (ZABORT, ZFERR, ZCAN):
                raise TransferAborted("对端中止传输")
            elif ftype == ZNAK:
                self._send_bin_header(ZRPOS, stohdr(len(buf)))

    def _receive_blocks(self, buf: bytearray, expected_size: int) -> None:
        while True:
            chunk, frameend = self._read_subpacket()
            buf += chunk
            if len(buf) > MAX_FILE:
                raise TransferError("接收数据超过上限")
            self.progress(len(buf), expected_size)
            if frameend in (ZCRCQ, ZCRCW):
                self._send_bin_header(ZACK, stohdr(len(buf)))
            if frameend in (ZCRCE, ZCRCW):
                return

    def _finish_receive(self) -> None:
        self._send_hex_header(ZFIN, [0, 0, 0, 0])
        try:
            self.link.write(b"OO")
        except Exception:
            pass
        self.log("ZMODEM 接收结束")

    @staticmethod
    def _save(name: str, data: bytes, save_dir: str, dedupe: bool) -> str:
        safe = "".join(ch for ch in os.path.basename(name) if ch not in '<>:"/\\|?*').strip() or "received.bin"
        path = os.path.join(save_dir, safe)
        if dedupe:
            stem, ext = os.path.splitext(path)
            index = 1
            while os.path.exists(path):
                path = f"{stem}-{index}{ext}"
                index += 1
        with open(path, "wb") as handle:
            handle.write(data)
        return os.path.abspath(path)


def _parse_info(info: bytes) -> tuple[str, int]:
    text = info.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
    name = os.path.basename(text.replace("\\", "/")) or "received.bin"
    size = 0
    if b"\x00" in info:
        rest = info.split(b"\x00", 1)[1].split(b"\x00", 1)[0].split()
        if rest:
            try:
                size = int(rest[0])
            except ValueError:
                size = 0
    return name, size
