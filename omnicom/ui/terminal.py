"""Compatibility shim - the terminal page now lives in ``plugins/terminal``.

The VT100 screen model stays in :mod:`omnicom.ui.vt100` (it is pure Python and
the model self-test drives it directly), but the *widgets* moved into the
``terminal`` plugin so the host no longer has to know what a terminal is (R-218).

Anything still importing ``omnicom.ui.terminal`` gets a working widget when the
plugin is loaded, and a clear ``RuntimeError`` when it is not - silently handing
back an empty terminal is what made the CLI answer "blank screen" instead of
"plugin disabled".
"""

from __future__ import annotations

from . import theme
from .vt100 import ANSI_COLORS, DEFAULT_BG, DEFAULT_FG, Screen, cell_width

#: Kept for callers that styled a selection band themselves.
SELECTION_BG = "#1F4E5A"

__all__ = [
    "ANSI_COLORS",
    "DEFAULT_BG",
    "DEFAULT_FG",
    "SELECTION_BG",
    "Screen",
    "TerminalPage",
    "TerminalView",
    "cell_width",
    "theme",
]


def _plugin_module():
    """Import the terminal plugin module, or raise a helpful error."""
    import sys

    module = sys.modules.get("omnicom_plugin_terminal")
    if module is None:
        raise RuntimeError(
            "终端插件未启用：请在插件列表中启用 terminal（Terminal）插件")
    return module


class _LazyType:
    """Resolve ``TerminalView`` / ``TerminalPage`` from the plugin on first use."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *args, **kwargs):
        return getattr(_plugin_module(), self._name)(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(getattr(_plugin_module(), self._name), item)

    def __instancecheck__(self, instance):
        cls = getattr(_plugin_module(), self._name)
        return isinstance(instance, cls)

    def __repr__(self) -> str:
        return f"<terminal plugin {self._name}>"


TerminalView = _LazyType("TerminalView")
TerminalPage = _LazyType("TerminalPage")
