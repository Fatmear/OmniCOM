"""Assemble the dashboard document with every asset inlined.

The page runs inside ``QWebEngineView`` and must work from a **portable folder
with no network access** (R-083), so ECharts, the stylesheet and the runtime are
read from disk and inlined into one ``setHtml()`` string.  Inlining is also what
keeps the layout immune to ``file://`` CORS rules.

Size is fine: ECharts 5.5.1 minified is ~1 MB and the probe measured a
1 030 928-byte body loading and painting normally.

``qwebchannel.js`` must be inlined too (R-165).  ``setHtml()`` serves the page
from a ``data:`` URL, so ``location.origin`` is ``"null"`` and Chromium refuses
every sub-resource request from it -- including ``<script src="qrc:///...">``.
The bridge client then stays ``undefined``, ``connect()`` gives up, and **no**
JavaScript ever calls back into Python: deleting a widget, dragging one, or
clicking a dashboard switch all looked like they worked and were silently
forgotten.  The file lives inside Qt's resource system (``QtWebChannel.dll``),
so there is nothing to ship besides this read.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "vendor" / "echarts.min.js"
STYLE = HERE / "dashboard.css"
SCRIPT = HERE / "dashboard.js"

#: Qt resource path of the bridge client.  Inlined instead of referenced
#: (see the module docstring); ``QFile`` reads it straight out of the library.
CHANNEL_RESOURCE = ":/qtwebchannel/qwebchannel.js"

_EMPTY = """<div id="empty">
<div class="big">{title}</div>
<div>{hint}</div>
</div>"""


def _read(path: Path, fallback: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return fallback


def available() -> bool:
    """Is the vendored charting library present?"""
    return VENDOR.is_file()


def channel_source() -> str:
    """Return ``qwebchannel.js`` read out of Qt's resource system.

    Returns an empty string when the resource is missing (an unusually trimmed
    Qt build); :func:`build` then falls back to the ``src`` reference, which is
    what every earlier release did.
    """
    try:
        from PySide6.QtCore import QFile, QIODevice
    except ImportError:
        return ""
    handle = QFile(CHANNEL_RESOURCE)
    if not handle.exists() or not handle.open(QIODevice.ReadOnly):
        return ""
    try:
        return bytes(handle.readAll()).decode("utf-8")
    finally:
        handle.close()


def build(chinese: bool = True) -> str:
    """Return the whole dashboard document as one self-contained string.

    ``chinese`` only affects the two placeholder sentences; the widget titles
    come from the user's own layout and are never translated (R-033).
    """
    library = _read(VENDOR)
    style = _read(STYLE)
    script = _read(SCRIPT)
    channel = channel_source()
    # Inline when the resource is readable; otherwise keep the old reference so a
    # trimmed Qt build degrades instead of raising (R-032).
    bridge_tag = ("<script>\n" + channel + "\n</script>" if channel
                  else '<script src="qrc:///qtwebchannel/qwebchannel.js"></script>')
    title = "看板还是空的" if chinese else "The dashboard is empty"
    hint = ("在上面选择控件类型和通道，点「添加控件」；"
            "加好之后直接拖动卡片、拖右下角缩放" if chinese else
            "Pick a widget type and a channel above, then press Add; "
            "after that, drag a card or its bottom-right corner to resize")
    empty = _EMPTY.format(title=title, hint=hint)
    return f"""<!DOCTYPE html>
<html lang="{'zh' if chinese else 'en'}">
<head>
<meta charset="utf-8">
<style>
{style}
</style>
</head>
<body>
<div id="canvas">{empty}</div>
<div id="toast"></div>
<script>
{library}
</script>
{bridge_tag}
<script>
{script}
</script>
</body>
</html>
"""
