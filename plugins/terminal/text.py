"""Plugin-owned translations (R-322).

The host table used to carry these strings; a plugin now ships its own
copy and registers it through ``api.register_text()``, so uninstalling
the plugin leaves no orphaned text behind and the host table stays
about the host.
"""

from __future__ import annotations

TEXT: dict[str, tuple[str, str]] = {
    'term.clear': ('清屏', 'Clear screen'),
    'term.enter': ('回车发送', 'Enter sends'),
    'term.exitfull': ('退出全屏', 'Exit full screen'),
    'term.fullscreen': ('终端全屏', 'Full-screen terminal'),
    'term.hint': ('点击终端区域后直接用键盘输入：Enter 发送回车，Ctrl+C 发送 0x03，方向键发送 ANSI 序列。复制用 Ctrl+Shift+C。', 'Click the terminal and type: Enter sends CR, Ctrl+C sends 0x03, arrow keys send ANSI sequences. Copy with Ctrl+Shift+C.'),
    'term.menu.clear': ('清屏', 'Clear screen'),
    'term.menu.copy': ('复制', 'Copy'),
    'term.menu.paste': ('粘贴到串口', 'Paste to port'),
    'term.menu.selectall': ('全选', 'Select all'),
    'term.reset': ('清屏并重置终端状态', 'Clear screen and reset terminal state'),
    'term.size': ('终端尺寸', 'Terminal size'),
    'term.size.auto': ('自动尺寸', 'Automatic size'),
    'term.needport': ('请先打开串口再使用终端模式', 'Open a port before using the terminal'),
    'term.title': ('终端模式', 'Terminal'),
}
