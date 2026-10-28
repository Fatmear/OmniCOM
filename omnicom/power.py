"""Keep the display awake while the user is watching a running board.

Windows ties idle sleep to the calling *thread*, so :func:`set_keep_awake` is
called from the GUI thread (once when the switch is turned on, once when it is
released).  Only the platform call lives here, which keeps the toggle testable
without touching real power state.
"""

from __future__ import annotations

import contextlib
import sys

#: ``SetThreadExecutionState`` flags - see the Win32 documentation.
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_CONTINUOUS = 0x80000000


def flags_for(enabled: bool) -> int:
    """Exact value handed to the OS: continuous, plus both requirements when on."""
    if not enabled:
        # ES_CONTINUOUS *without* the requirement bits clears a previous request.
        return ES_CONTINUOUS
    return ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED


def apply_flags(flags: int) -> bool:
    """Send ``flags`` to the OS.  Returns True when the request was accepted."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
        kernel32.SetThreadExecutionState.restype = ctypes.c_uint
        previous = kernel32.SetThreadExecutionState(ctypes.c_uint(flags))
        # The call returns the *previous* state and 0 on failure; a previous
        # state of "nothing requested" is still a success.
        return previous != 0 or ctypes.get_last_error() == 0
    except Exception:
        return False


def set_keep_awake(enabled: bool) -> bool:
    """Turn the keep-awake request on or off for the calling thread."""
    return apply_flags(flags_for(enabled))


def release() -> bool:
    """Best-effort cleanup, safe to call when nothing was ever requested."""
    with contextlib.suppress(Exception):
        return apply_flags(flags_for(False))
    return False
