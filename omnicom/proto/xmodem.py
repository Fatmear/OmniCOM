"""XMODEM (128/1024 byte, checksum and CRC16) sender and receiver."""

from __future__ import annotations

from typing import Callable, Optional

from .common import (ACK, CAN, CRC_CHAR, EOT, NAK, SOH, STX, SUB, ByteReader, Link,
                     Progress, TransferAborted, TransferError, logger_cb, progress_cb)
from .crc import crc16_seal


def pad_block(payload: bytes, size: int) -> bytes:
    if len(payload) >= size:
        return payload[:size]
    return payload + bytes((SUB,)) * (size - len(payload))


def build_block(block_no: int, payload: bytes, crc_mode: bool) -> bytes:
    head = bytes(((SOH if len(payload) == 128 else STX), block_no & 0xFF, 0xFF - (block_no & 0xFF)))
    tail = crc16_seal(payload) if crc_mode else bytes((sum(payload) & 0xFF,))
    return head + payload + tail


def _check_cancel(cancel: Optional[Callable[[], bool]]) -> None:
    if cancel and cancel():
        raise TransferAborted("用户取消")


def read_block(reader: ByteReader, timeout: float, crc_mode: bool = True) -> tuple[str, int, bytes]:
    """Return ("block", n, payload) / ("eot", 0, b"") / ("can", 0, b"").

    Any other byte is line noise ("rz\\r\\n", boot banners) and is skipped, exactly
    like the reference implementation hunts for a frame start.
    """
    while True:
        first = reader.getbyte(timeout)
        if first is None:
            return "timeout", 0, b""
        if first in (EOT, EOT + 0x80):
            return "eot", 0, b""
        if first == CAN:
            if reader.getbyte(1.0) == CAN and reader.getbyte(1.0) == CAN:
                return "can", 0, b""
            continue
        if first not in (SOH, STX):
            continue
        size = 128 if first == SOH else 1024
        body = reader.read(2 + size, timeout)
        if len(body) < 2 + size:
            return "timeout", 0, b""
        block_no, complement = body[0], body[1]
        payload = body[2:]
        if (block_no ^ complement) != 0xFF:
            return "bad", block_no, payload
        width = 2 if crc_mode else 1
        fcs = reader.read(width, timeout if not crc_mode else 2.0)
        if len(fcs) < width:
            return "bad", block_no, payload
        if crc_mode:
            if crc16_seal(payload) == fcs[:2]:
                return "block", block_no, payload
            return "bad", block_no, payload
        if fcs[0] == (sum(payload) & 0xFF):
            return "block", block_no, payload
        return "bad", block_no, payload


def send(link: Link, data: bytes, *, block_size: int = 1024, timeout: float = 10.0,
         retries: int = 10, progress: Optional[Progress] = None, log=None,
         cancel: Optional[Callable[[], bool]] = None, name: str = "file") -> dict:
    progress = progress_cb(progress)
    log = logger_cb(log)
    reader = ByteReader(link)
    reader.drain(0.1)
    log(f"XMODEM 发送 {name}（{len(data)} 字节，{block_size}B/块）")

    crc_mode: Optional[bool] = None
    for _ in range(60):
        _check_cancel(cancel)
        byte = reader.getbyte(timeout)
        if byte is None:
            _check_cancel(cancel)
            continue
        if byte == CRC_CHAR:
            crc_mode = True
            break
        if byte == NAK:
            crc_mode = False
            break
        if byte == CAN:
            raise TransferAborted("对端取消")
    if crc_mode is None:
        raise TransferError("等待接收方握手超时（未收到 'C' 或 NAK）")
    log("握手成功：CRC16 模式" if crc_mode else "握手成功：校验和模式")

    block_no = 1
    offset = 0
    total = len(data)
    while offset < total:
        _check_cancel(cancel)
        chunk = pad_block(data[offset:offset + block_size], block_size)
        for attempt in range(retries):
            _check_cancel(cancel)
            link.write(build_block(block_no, chunk, bool(crc_mode)))
            reply = reader.getbyte(timeout)
            if reply == ACK:
                break
            if reply == CAN:
                raise TransferAborted("对端取消")
            log(f"块 {block_no} 重试 {attempt + 1}（应答 0x{(reply or 0):02x}）")
        else:
            raise TransferError(f"块 {block_no} 多次重试后仍未被确认")
        offset += block_size
        block_no = (block_no + 1) & 0xFF
        progress(min(offset, total), total)

    for _ in range(retries):
        _check_cancel(cancel)
        link.write(bytes((EOT,)))
        reply = reader.getbyte(timeout)
        if reply == ACK:
            log("发送完成")
            progress(total, total)
            return {"bytes": total, "name": name}
        if reply == CAN:
            raise TransferAborted("对端取消")
    raise TransferError("EOT 未被确认")


def receive(link: Link, *, timeout: float = 10.0, retries: int = 10,
            progress: Optional[Progress] = None, log=None,
            cancel: Optional[Callable[[], bool]] = None,
            name: str = "received.bin", max_bytes: int = 512 * 1024 * 1024) -> dict:
    progress = progress_cb(progress)
    log = logger_cb(log)
    reader = ByteReader(link)
    reader.drain(0.15)
    log("XMODEM 接收：等待数据块")

    out = bytearray()
    expected = 1
    crc_mode = True
    started = False
    attempts = 0

    while True:
        _check_cancel(cancel)
        if not started:
            link.write(bytes((CRC_CHAR if crc_mode else NAK,)))
        kind, block_no, payload = read_block(reader, timeout, crc_mode)
        if kind == "block":
            if not started:
                started = True
                expected = (block_no + 1) & 0xFF
                out += payload
                link.write(bytes((ACK,)))
                progress(len(out), 0)
                continue
            if block_no == expected:
                out += payload
                expected = (expected + 1) & 0xFF
                if len(out) > max_bytes:
                    raise TransferError("接收数据超过上限")
                link.write(bytes((ACK,)))
                progress(len(out), 0)
            elif block_no == ((expected - 1) & 0xFF):
                link.write(bytes((ACK,)))
            else:
                link.write(bytes((NAK,)))
        elif kind == "bad":
            link.write(bytes((NAK,)))
        elif kind == "eot":
            link.write(bytes((ACK,)))
            log(f"接收结束，共 {len(out)} 字节")
            progress(len(out), len(out))
            return {"bytes": len(out), "name": name, "data": bytes(out)}
        elif kind == "can":
            raise TransferAborted("对端取消")
        else:
            if not started:
                attempts += 1
                if attempts > retries:
                    raise TransferError("握手超时（对端未发送数据）")
                if attempts >= 4 and crc_mode:
                    crc_mode = False
                    log("改用校验和模式重试")
            else:
                link.write(bytes((NAK,)))
                log("等待数据块超时，发送 NAK")
