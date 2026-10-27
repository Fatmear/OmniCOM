"""YMODEM (batch XMODEM-CRC with a file-name block 0)."""

from __future__ import annotations

import os
from typing import Callable, Optional

from .common import (ACK, CAN, CRC_CHAR, EOT, NAK, SOH, STX, ByteReader, Link, Progress,
                     TransferAborted, TransferError, logger_cb, progress_cb)
from .xmodem import build_block, pad_block, read_block

BLOCK0_SIZE = 128


def _meta_block0(name: str, size: int) -> bytes:
    """Block 0 payload: ``name\\0size mtime mode serial\\0`` padded to 128 bytes."""
    base = os.path.basename(name) or "file.bin"
    info = f"{base}\0{size} 0 100644 0\0".encode("utf-8", "replace")
    return pad_block(info, BLOCK0_SIZE)


def _empty_block0() -> bytes:
    return bytes(BLOCK0_SIZE)


def _parse_block0(payload: bytes) -> tuple[str, int]:
    text = payload.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
    if not text:
        return "", 0  # empty block 0 terminates a batch
    name = os.path.basename(text.replace("\\", "/")) or "received.bin"
    size = 0
    if b"\x00" in payload:
        rest = payload.split(b"\x00", 1)[1].split(b"\x00", 1)[0]
        parts = rest.split()
        if parts:
            try:
                size = int(parts[0])
            except ValueError:
                size = 0
    return name, size


def send(link: Link, data: bytes, *, name: str = "file.bin", block_size: int = 1024,
         timeout: float = 10.0, retries: int = 10, progress: Optional[Progress] = None,
         log=None, cancel: Optional[Callable[[], bool]] = None) -> dict:
    progress = progress_cb(progress)
    log = logger_cb(log)
    reader = ByteReader(link)
    reader.drain(0.1)
    log(f"YMODEM 发送 {name}（{len(data)} 字节）")

    # 1. Wait for the receiver's 'C'
    got_c = False
    for _ in range(60):
        if cancel and cancel():
            raise TransferAborted("用户取消")
        byte = reader.getbyte(timeout)
        if byte == CRC_CHAR:
            got_c = True
            break
        if byte == CAN:
            raise TransferAborted("对端取消")
    if not got_c:
        raise TransferError("等待接收方握手超时")

    # 2. Block 0 with the file header
    for _ in range(retries):
        link.write(build_block(0, _meta_block0(name, len(data)), True))
        reply = reader.getbyte(timeout)
        if reply == ACK:
            break
        if reply == CAN:
            raise TransferAborted("对端取消")
    else:
        raise TransferError("文件头块未被确认")

    # Receiver answers ACK + 'C'
    if reader.getbyte(timeout) != CRC_CHAR:
        log("未收到后续 'C'，继续尝试发送数据")

    # 3. Data blocks
    block_no = 1
    offset = 0
    total = len(data)
    while offset < total:
        if cancel and cancel():
            raise TransferAborted("用户取消")
        chunk = pad_block(data[offset:offset + block_size], block_size)
        for attempt in range(retries):
            link.write(build_block(block_no, chunk, True))
            reply = reader.getbyte(timeout)
            if reply == ACK:
                break
            if reply == CAN:
                raise TransferAborted("对端取消")
            log(f"块 {block_no} 重试 {attempt + 1}")
        else:
            raise TransferError(f"块 {block_no} 重试耗尽")
        offset += block_size
        block_no = (block_no + 1) & 0xFF
        progress(min(offset, total), total)

    # 4. EOT dance: EOT -> NAK -> EOT -> ACK
    link.write(bytes((EOT,)))
    reply = reader.getbyte(timeout)
    if reply == NAK:
        link.write(bytes((EOT,)))
        reply = reader.getbyte(timeout)
    if reply != ACK:
        log(f"EOT 应答异常 0x{(reply or 0):02x}")

    # 5. Empty block 0 closes the batch
    if reader.getbyte(timeout) == CRC_CHAR:
        link.write(build_block(0, _empty_block0(), True))
        reader.getbyte(timeout)

    log("发送完成")
    progress(total, total)
    return {"bytes": total, "name": name}


def receive(link: Link, *, timeout: float = 10.0, retries: int = 10,
            progress: Optional[Progress] = None, log=None,
            cancel: Optional[Callable[[], bool]] = None,
            max_files: int = 32, max_bytes: int = 512 * 1024 * 1024) -> dict:
    progress = progress_cb(progress)
    log = logger_cb(log)
    reader = ByteReader(link)
    reader.drain(0.15)
    log("YMODEM 接收：等待文件头块")

    files: list[dict] = []
    current: Optional[dict] = None
    expected = 0
    attempts = 0
    handshake_sent = False

    while True:
        if cancel and cancel():
            raise TransferAborted("用户取消")
        if not handshake_sent:
            link.write(bytes((CRC_CHAR,)))
            handshake_sent = True

        kind, block_no, payload = read_block(reader, timeout, True)
        if kind == "block":
            if block_no == 0 and (current is None or expected == 0):
                name, size = _parse_block0(payload)
                if not name:
                    # Empty block 0 = end of batch
                    link.write(bytes((ACK,)))
                    log("批次结束")
                    break
                if len(files) >= max_files:
                    raise TransferError("文件数量超过上限")
                current = {"name": name, "size": size, "data": bytearray()}
                files.append(current)
                expected = 1
                link.write(bytes((ACK,)))
                link.write(bytes((CRC_CHAR,)))
                log(f"文件：{name}" + (f"（{size} 字节）" if size else ""))
                continue
            if current is None:
                continue
            if block_no == expected:
                current["data"] += payload
                total = sum(len(f["data"]) for f in files)
                if total > max_bytes:
                    raise TransferError("接收数据超过上限")
                expected = (expected + 1) & 0xFF
                link.write(bytes((ACK,)))
                progress(len(current["data"]), current["size"] or 0)
            elif block_no == ((expected - 1) & 0xFF):
                link.write(bytes((ACK,)))
            else:
                link.write(bytes((NAK,)))
        elif kind == "bad":
            link.write(bytes((NAK,)))
        elif kind == "eot":
            link.write(bytes((ACK,)))
            if current is not None:
                if current["size"]:
                    del current["data"][current["size"]:]  # 裁掉末尾填充
                log(f"文件 {current['name']} 接收完成（{len(current['data'])} 字节）")
                current = None
                expected = 0
            link.write(bytes((CRC_CHAR,)))
        elif kind == "can":
            raise TransferAborted("对端取消")
        else:
            attempts += 1
            if attempts > retries and current is None:
                raise TransferError("握手超时（对端未发送文件头）")
            if current is None:
                link.write(bytes((CRC_CHAR,)))

    result_files = []
    for item in files:
        data = bytes(item["data"])
        result_files.append({"name": item["name"], "bytes": len(data), "data": data})
    total = sum(f["bytes"] for f in result_files)
    progress(total, total)
    return {"files": result_files, "bytes": total}
