"""File-transfer protocol engines (X/Y/ZMODEM).

All engines accept any :class:`omnicom.proto.common.Link`, which is what makes
the in-memory loopback self-test possible without hardware.
"""

from . import xmodem, ymodem, zmodem
from .common import Link, TransferAborted, TransferError

__all__ = ["Link", "TransferAborted", "TransferError", "xmodem", "ymodem", "zmodem"]
