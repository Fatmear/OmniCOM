"""CRC helpers used by XMODEM / YMODEM / ZMODEM.

CRC-16/XMODEM is the *unreflected* CRC used by lrzsz ``updcrc`` (poly 0x1021,
init 0, no final xor).  The ZMODEM "CRC32" is the standard reflected CRC-32
table driven straight from 0xFFFFFFFF with **no** final xor, so the running
value over data + transmitted FCS equals 0xDEBB20E3 exactly like lrzsz.
"""

from __future__ import annotations

# ---------------------------------------------------------------- CRC-16/XMODEM

def _make_crc16_table() -> list[int]:
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_CRC16_TABLE = _make_crc16_table()


def crc16(data: bytes, crc: int = 0) -> int:
    """lrzsz ``updcrc`` — CRC-16/XMODEM (poly 0x1021, init 0, MSB first)."""
    for byte in data:
        crc = ((crc << 8) ^ _CRC16_TABLE[((crc >> 8) ^ byte) & 0xFF]) & 0xFFFF
    return crc


def crc16_seal(data: bytes) -> bytes:
    """The two FCS bytes on the wire: big-endian CRC-16/XMODEM.

    lrzsz writes ``crc = updcrc(0, updcrc(0, crc))``; because its ``updcrc``
    keeps the register in a shifted convention, that is byte-for-byte the same
    as transmitting ``crc16(data)`` big-endian — verified by the self-test that
    asserts ``crc16(data + seal) == 0``.
    """
    value = crc16(data)
    return bytes(((value >> 8) & 0xFF, value & 0xFF))


# ------------------------------------------------------------------- CRC-32

def _make_crc32_table() -> list[int]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
        table.append(crc)
    return table


_CRC32_TABLE = _make_crc32_table()


def crc32(data: bytes, crc: int = 0xFFFFFFFF) -> int:
    for byte in data:
        crc = (crc >> 8) ^ _CRC32_TABLE[(crc ^ byte) & 0xFF]
    return crc & 0xFFFFFFFF


def crc32_seal(data: bytes) -> bytes:
    """Little-endian ~crc, as ``zsda32`` emits it."""
    value = (~crc32(data)) & 0xFFFFFFFF
    return bytes((value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF, (value >> 24) & 0xFF))


def crc32_check(data_with_fcs: bytes) -> bool:
    return crc32(data_with_fcs) == 0xDEBB20E3
