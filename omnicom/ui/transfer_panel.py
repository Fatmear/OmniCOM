"""Compatibility shim - the transfer page now lives in ``plugins/transfer``.

The protocol engines stay in :mod:`omnicom.transfer` (pure Python, exercised by
``tools/protocol_selftest.py``); only the *widget* moved into a plugin so the host
no longer has to schedule transfers itself (R-218).
"""

from __future__ import annotations

__all__ = ["PROTOCOLS", "TransferPanel"]  # noqa: F822 - resolved lazily


def _plugin_module():
    """Import the transfer plugin module, or raise a helpful error."""
    import sys

    module = sys.modules.get("omnicom_plugin_transfer")
    if module is None:
        raise RuntimeError(
            "文件传输插件未启用：请在插件列表中启用 transfer（File Transfer）插件")
    return module


class _LazyType:
    """Resolve ``TransferPanel`` from the plugin on first use."""

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
        return f"<transfer plugin {self._name}>"


def __getattr__(name: str):
    """``from .transfer_panel import PROTOCOLS`` must keep working."""
    if name == "PROTOCOLS":
        return _plugin_module().PROTOCOLS
    raise AttributeError(name)


TransferPanel = _LazyType("TransferPanel")
