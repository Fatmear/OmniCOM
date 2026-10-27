"""Built-in user manual (zh / en).

The Help → Documentation dialog renders this, and ``tools/make_readme.py``
reuses the same text for README.md / README.en.md so the two can never drift.
"""

from __future__ import annotations

MANUAL: dict[str, list[tuple[str, list[str]]]] = {
    "zh": [
        ("快速开始", [
            "双击 `start.cmd`，或运行 `python run.py`。没有硬件时加 `--demo` 会多出一个虚拟串口 DEMO。",
            "左侧选择串口 → 设置波特率等参数 → 点「打开串口」，接收的数据会立刻出现在「串口日志」页。",
            "底部输入框写内容，Ctrl+Enter 或点「发送」发出。行尾、HEX、定时发送都在输入框右侧。",
        ]),
        ("串口日志页", [
            "每一行的格式是 `[时:分:秒.毫秒]收←◇内容` 或 `发→◇内容`。",
            "「自动滚动到最新」打开时永远跟着最新一行；关掉后可以自由翻阅历史，底部会浮出「回到最新」气泡。",
            "「显示 \\r \\n 等控制字符」对收和发同时生效，能看到真实发出的换行。",
            "「HEX 显示」按 16 字节一行显示偏移 + 十六进制 + ASCII。",
            "「保存视图」把当前日志写到文件；「接收记录」在后台把原始字节写盘，与界面无关，不会漏字节。",
            "日志框右键可以复制、复制全部、保存、查找、跳到最新。",
        ]),
        ("终端模式页", [
            "这是给 linux 串口控制台用的：点一下终端区域，键盘输入直接送进串口。",
            "Enter 发送回车（可选 CR / LF / CRLF），Ctrl+C 发送 0x03，方向键发送 ANSI 序列。",
            "支持 CR 回到行首覆盖、退格、TAB 对齐、ANSI 颜色，以及清屏序列。",
            "复制用 Ctrl+Shift+C，粘贴用 Ctrl+Shift+V（普通 Ctrl+C 会当作中断字符发出）。",
        ]),
        ("文件传输页", [
            "选择 XMODEM / YMODEM / ZMODEM，设置方向与文件，点开始。",
            "接收的文件保存在 `omnicom-data/received/`。",
            "传输期间串口被独占，日志页不打印，避免把协议字节弄脏。",
        ]),
        ("扩展命令", [
            "右侧栏的命令可以点一下直接发送，也可以勾选多条后「依次发送」（每条按自己的延时）。",
            "「分组」用来归类：点文件夹图标新建分组，可以给分组写备注；分组可折叠。",
            "每条命令支持备注、行尾、延时、周期定时发送、HEX。",
            "右键一条命令可以移动到别的分组、单独导出；分组标题右侧的保存图标可整组导出。",
            "导出的 JSON 在另一台电脑上「导入」即可使用，分组会自动合并。",
            "粘贴多行命令会自动拆分成多条。",
        ]),
        ("关键字搜索", [
            "输入关键字回车，会搜索**整个日志（含此前的历史）**，结果按「行号 + 内容」列出。",
            "双击任意一行跳到日志原文并高亮。",
            "匹配方式：普通 / 模糊（字符按顺序出现即可）/ 全词 / 正则表达式；另有「区分大小写」开关。",
            "右上角箭头按钮可把结果全窗口显示，Esc 退出。",
        ]),
        ("插件", [
            "插件是一个独立目录，放在 `plugins/` 或 `omnicom-data/plugins/` 下，每个插件在顶部占一个标签页。",
            "把一个插件目录整个复制到另一台电脑的 `plugins/` 下，点「插件 → 刷新插件」就能用。",
            "自带「快捷启动」插件：把常用软件加进来，图标显示运行中的实例数；已在运行时可以选择再开一个或切到已打开的窗口。",
        ]),
        ("首选项与语言", [
            "「设置 → 首选项」里可以切换界面语言：中文环境只显示中文，英文环境只显示英文，包括右键菜单。",
            "切换立即生效，不需要重启。",
        ]),
        ("命令行（给脚本 / AI 用）", [
            "`python -m omnicom.cli ports` 列出串口；`send` 发送并等待应答；`read` 读取；`monitor` 流式输出。",
            "`session --script 文件.json` 可以跑一段脚本化的收发流程。",
            "所有命令的 stdout 都是一个 JSON 文档，详细说明见 `docs/CLI.md`。",
        ]),
        ("数据放在哪", [
            "`omnicom-data/logs/` 接收记录；`omnicom-data/received/` 协议收到的文件；",
            "`omnicom-data/commands.json` 扩展命令；`omnicom-data/plugins/` 后加的插件。",
        ]),
    ],
    "en": [
        ("Quick start", [
            "Double-click `start.cmd`, or run `python run.py`. Add `--demo` for a virtual DEMO port.",
            "Pick a port on the left, set the parameters, then press *Open port* - data starts "
            "appearing on the *Serial log* page.",
            "Type in the box at the bottom and press Ctrl+Enter or *Send*. Line ending, HEX and "
            "timed send live to its right.",
        ]),
        ("Serial log", [
            "Each line reads `[hh:mm:ss.mmm]RX←◇payload` (received) or "
            "`TX→◇payload` (sent). Switch the language and the two direction "
            "markers change with it.",
            "*Auto-scroll to latest* always follows the newest line. Turn it off and a "
            "*back to latest* bubble appears while you read history.",
            "*Show \\r \\n and other control chars* applies to both directions, so you can see the "
            "line ending that was really sent.",
            "*HEX view* prints 16 bytes per line: offset, hex, ASCII.",
            "*Save view* writes the log to a file. *Capture to file* writes the raw bytes in the "
            "background and never misses a byte, regardless of what the UI shows.",
            "Right-click the log to copy, copy all, save, find or jump to latest.",
        ]),
        ("Terminal", [
            "A real console page for a linux serial login: click it and every keystroke goes out.",
            "Enter sends CR (selectable CR/LF/CRLF), Ctrl+C sends 0x03, arrow keys send ANSI.",
            "Handles CR-overwrite, backspace, TAB, ANSI colours and clear-screen sequences.",
            "Copy with Ctrl+Shift+C, paste with Ctrl+Shift+V (plain Ctrl+C is the interrupt byte).",
        ]),
        ("File transfer", [
            "Pick XMODEM / YMODEM / ZMODEM, choose direction and file, press Start.",
            "Received files land in `omnicom-data/received/`.",
            "The port is exclusive during a transfer, so the log page stays quiet.",
        ]),
        ("Commands", [
            "Click a command to send it, or tick several and press *Send in order* (each keeps its "
            "own delay).",
            "*Groups* organise them: the folder button creates one, groups take a note, and they "
            "collapse.",
            "Every command supports a note, line ending, delay, timed repeat and HEX.",
            "Right-click a command to move it to another group or export just that one; the save "
            "icon on a group header exports the whole group.",
            "The exported JSON can be imported on another machine - groups merge automatically.",
            "Pasting several lines creates several commands.",
        ]),
        ("Search", [
            "Type a keyword and press Enter: it searches **the whole log, history included**, and "
            "lists hits as `line-number  text`.",
            "Double-click any row to jump to that line in the log; it flashes.",
            "Modes: Plain / Fuzzy (characters in order) / Whole word / Regex, plus a *Match case* "
            "switch.",
            "The expand arrow blows the results up to the full window; Esc exits.",
        ]),
        ("Plugins", [
            "A plugin is a folder under `plugins/` or `omnicom-data/plugins/`; each one gets its own "
            "tab in the top bar.",
            "Copy the whole folder to another machine's `plugins/`, press *Plugins → Reload* and it "
            "is installed.",
            "Ships with *Launcher*: add your apps, see a badge with the running instance count, and "
            "choose between opening another window or switching to the existing one.",
        ]),
        ("Preferences and language", [
            "*Settings → Preferences* switches the interface language. Chinese shows only Chinese, "
            "English only English - context menus included.",
            "The change applies immediately, no restart.",
        ]),
        ("Command line (for scripts / AI)", [
            "`python -m omnicom.cli ports` lists ports; `send` sends and waits for a reply; `read` "
            "reads; `monitor` streams.",
            "`session --script file.json` runs a scripted exchange.",
            "Every command writes exactly one JSON document to stdout; see `docs/CLI.md`.",
        ]),
        ("Where the data lives", [
            "`omnicom-data/logs/` captures, `omnicom-data/received/` transferred files,",
            "`omnicom-data/commands.json` commands, `omnicom-data/plugins/` extra plugins.",
        ]),
    ],
}


def manual(lang: str = "zh") -> list[tuple[str, list[str]]]:
    return MANUAL.get(lang) or MANUAL["zh"]


def manual_html(lang: str = "zh") -> str:
    """Render the manual as simple HTML for the help dialog."""
    parts: list[str] = []
    for title, lines in manual(lang):
        parts.append(f"<h3 style='margin:14px 0 6px 0'>{_escape(title)}</h3><ul>")
        for line in lines:
            parts.append(f"<li style='margin:3px 0'>{_escape(line)}</li>")
        parts.append("</ul>")
    return "".join(parts)


def manual_markdown(lang: str = "zh") -> str:
    parts: list[str] = []
    for title, lines in manual(lang):
        parts.append(f"## {title}\n")
        for line in lines:
            parts.append(f"- {line}")
        parts.append("")
    return "\n".join(parts)


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
