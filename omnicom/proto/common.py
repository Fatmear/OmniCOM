"""Transport abstraction shared by every file-transfer protocol.

A ``Link`` is anything that can push bytes out and pull bytes in with a
timeout: the real serial port, or an in-memory pipe for ``npm run selftest``
(now ``python -m tools.protocol_selftest``).  Keeping the engines transport
agnostic is what makes the loopback harness possible without hardware.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

# Shared control bytes
SOH = 0x01
STX = 0x02
EOT = 0x04
ACK = 0x06
DLE = 0x10
NAK = 0x15
CAN = 0x18
SUB = 0x1A
CRC_CHAR = ord("C")


class TransferError(Exception):
    """Protocol failure that should be reported to the user."""


class TransferAborted(TransferError):
    """Peer cancelled or the local side aborted."""


class Link:
    """Minimal byte pipe."""

    def write(self, data: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def read(self, size: int, timeout: float) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def purge(self) -> None:  # pragma: no cover - optional
        pass

    def close(self) -> None:  # pragma: no cover - optional
        pass


class ByteReader:
    """Buffered, timeout-aware byte source on top of a :class:`Link`."""

    def __init__(self, link: Link, chunk: int = 4096) -> None:
        self.link = link
        self.chunk = chunk
        self._buf = b""
        self._pos = 0
        self._pushback: list[int] = []

    # -- internals ---------------------------------------------------------
    def _fill(self, timeout: float) -> bool:
        if self._pushback:
            return True
        if self._pos < len(self._buf):
            return True
        data = self.link.read(self.chunk, timeout)
        if not data:
            return False
        self._buf = data
        self._pos = 0
        return True

    def buffered(self) -> int:
        return len(self._pushback) + (len(self._buf) - self._pos)

    # -- public ------------------------------------------------------------
    def getbyte(self, timeout: float = 1.0) -> Optional[int]:
        if self._pushback:
            return self._pushback.pop()
        if not self._fill(timeout):
            return None
        value = self._buf[self._pos]
        self._pos += 1
        return value

    def unget(self, byte: int) -> None:
        self._pushback.append(byte & 0xFF)

    def read(self, size: int, timeout: float) -> bytes:
        """Read up to ``size`` bytes, waiting at most ``timeout`` for the first."""
        out = bytearray()
        deadline = time.monotonic() + timeout
        while len(out) < size:
            while self._pushback and len(out) < size:
                out.append(self._pushback.pop())
            if self._pos < len(self._buf):
                take = min(size - len(out), len(self._buf) - self._pos)
                out += self._buf[self._pos:self._pos + take]
                self._pos += take
                continue
            remaining = max(0.0, deadline - time.monotonic())
            if not self._fill(remaining):
                break
            deadline = time.monotonic() + timeout
        return bytes(out)

    def expect(self, timeout: float = 1.0) -> int:
        value = self.getbyte(timeout)
        if value is None:
            raise TransferError("对端无响应（读取超时）")
        return value

    def drain(self, settle: float = 0.12, limit: float = 2.0) -> int:
        """Discard input until the line is quiet."""
        total = 0
        deadline = time.monotonic() + limit
        self._buf = b""
        self._pos = 0
        self._pushback.clear()
        while time.monotonic() < deadline:
            data = self.link.read(4096, settle)
            if not data:
                break
            total += len(data)
        return total


Progress = Callable[[int, int], None]
Logger = Callable[[str], None]


def _noop(*_args, **_kwargs) -> None:
    return None


def progress_cb(value: Optional[Progress]) -> Progress:
    return value or _noop


def logger_cb(value: Optional[Logger]) -> Logger:
    return value or _noop
