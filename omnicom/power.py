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


def _kernel32():
    """The Win32 library entry point, kept behind a seam so tests can fake it.

    Patching :func:`_kernel32` is the only way to exercise the *failure* path:
    a real refused request cannot be provoked on a healthy machine, so the
    "the switch had no effect" warning would otherwise never be tested.
    """
    import ctypes

    library = ctypes.WinDLL("kernel32", use_last_error=True)
    library.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    library.SetThreadExecutionState.restype = ctypes.c_uint
    return library


def apply_flags(flags: int) -> bool:
    """Send ``flags`` to the OS.  Returns True when the request was accepted."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        # Clear any latched error *first*.  ``SetThreadExecutionState`` has no
        # documented error contract of its own, and its "previous state" return
        # is 0 both when "nothing was requested" and when it failed.  Leaving a
        # stale ``last_error`` of 0 in place from any earlier call therefore made
        # a refusal look like success, so the "switch had no effect" warning
        # could never fire (L-235).
        ctypes.set_last_error(0)
        previous = _kernel32().SetThreadExecutionState(ctypes.c_uint(flags))
        # A previous state of "nothing requested" is still a success; a genuine
        # failure returns 0 *and* latches an error code.
        return previous != 0 or ctypes.get_last_error() == 0
    except Exception:
        return False


def set_keep_awake(enabled: bool) -> bool:
    """Turn the keep-awake request on or off for the calling thread."""
    return apply_flags(flags_for(enabled))


def display_never_off() -> bool | None:
    """True when Windows itself is configured to never blank the display.

    ``R-062``'s switch asks for ``ES_DISPLAY_REQUIRED``.  A managed machine can
    have the *policy* already set to "never", in which case the request changes
    nothing and the user reports "I turned the switch off but the screen is
    still on" - which is not a bug in the switch, it is the power scheme
    (L-235).  ``None`` means "could not tell", so callers must not conclude.

    The read goes through the registry rather than ``PowerReadACValueIndex``:
    on a LeagSoft-managed box the API answered ``ERROR_INVALID_PARAMETER`` (87)
    for every GUID layout tried, while the same values read back fine from
    ``...\\PowerSchemes\\<scheme>\\7516b95f-...\\3c0bc021-...\\ACSettingIndex``.
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg

        root = r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as key:
            scheme = winreg.QueryValueEx(key, "ActivePowerScheme")[0]
        path = (rf"{root}\{scheme}"
                r"\7516b95f-f776-4464-8c53-06167f40cc99"
                r"\3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e")
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            ac = winreg.QueryValueEx(key, "ACSettingIndex")[0]
        return int(ac) == 0
    except Exception:
        return None


def release() -> bool:
    """Best-effort cleanup, safe to call when nothing was ever requested."""
    with contextlib.suppress(Exception):
        return apply_flags(flags_for(False))
    return False
