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
            "自定义波特率可直接点击输入，按回车或移开焦点后应用；也可以从下拉列表选择常用值。",
            "底部输入框写内容，Ctrl+Enter 或点「发送」发出。行尾、HEX、定时发送都在输入框右侧。",
            "拖动日志与发送区之间的分隔条可直接增高输入框，高度会在下次启动时恢复。",
        ]),
        ("串口日志页", [
            "每一行的格式是 `[时:分:秒.毫秒]收←◇内容` 或 `发→◇内容`。",
            "「自动滚动到最新」打开时永远跟着最新一行；关掉后可以自由翻阅历史，底部会浮出「回到最新」气泡。",
            "「显示 \\r \\n 等控制字符」对收和发同时生效，能看到真实发出的换行。",
            "「HEX 显示」按 16 字节一行显示偏移 + 十六进制 + ASCII。",
            "「保存视图」把当前日志写到文件；「接收记录」在后台把原始字节写盘，与界面无关，不会漏字节。",
            "日志框右键可以复制、复制全部、保存、查找、跳到最新。",
            "串口按钮在未连接时为中性颜色，成功打开后才着色；开关在后台执行，期间按钮暂时不可用。",
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
            "「分组」用来归类：点文件夹图标新建分组，可以给分组写备注；分组可折叠。创建和改名不允许与已有分组同名，首尾空格不计。",
            "每条命令与发送、编辑、删除按钮排在同一主行，色条区分选中状态；备注在命令下方以小字显示，悬停可查看完整内容和延时。",
            "每条命令支持备注、行尾、延时、周期定时发送、HEX。",
            "右键一条命令可以移动分组；顶部导出按钮统一选择要导出的命令或分组。",
            "导出的 JSON 在另一台电脑上「导入」即可使用，分组会自动合并。",
            "粘贴多行命令会自动拆分成多条。",
        ]),
        ("关键字搜索", [
            "输入关键字回车，会搜索**整个日志（含此前的历史）**，结果按「行号 + 内容」列出。",
            "选中日志文字后右键「查找」，会替换旧查询并立即按普通文本搜索；多行选区按行匹配任一项。",
            "双击任意一行跳到日志原文并高亮。",
            "匹配方式：普通 / 模糊（字符按顺序出现即可）/ 全词 / 正则表达式；另有「区分大小写」开关。",
            "右上角箭头按钮可把结果全窗口显示，Esc 退出。",
        ]),
        ("插件", [
            "插件是一个独立目录，放在 `plugins/` 或 `omnicom-data/plugins/` 下，每个插件在顶部占一个标签页。",
            "把一个插件目录整个复制到另一台电脑的 `plugins/` 下，点「插件 → 刷新插件」就能用。",
            "自带「快捷启动」插件：同一软件的每个已打开窗口各有一个图标，点击切换到该窗口，悬停查看完整窗口标题。",
            "右键可再开新窗口或从列表移除快捷方式；移除不会关闭正在运行的软件。",
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
            "Click the baud field and type a custom value. Enter or moving focus applies it; "
            "the dropdown still offers common rates.",
            "Type in the box at the bottom and press Ctrl+Enter or *Send*. Line ending, HEX and "
            "timed send live to its right.",
            "Drag the divider above the composer to grow the actual text editor; its height is "
            "restored on the next launch.",
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
            "The port button is neutral while disconnected and coloured only when connected. "
            "Opening and closing run in the background; the button is temporarily disabled.",
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
            "collapse. Creating or renaming a group rejects existing names after trimming "
            "leading and trailing whitespace.",
            "Each command and its send/edit/delete actions share one primary line. A colour strip "
            "shows selection; notes appear below in smaller text. Hover for full text and delay.",
            "Every command supports a note, line ending, delay, timed repeat and HEX.",
            "Right-click a command to move groups. Use the export button at the top to select "
            "commands or a group to export.",
            "The exported JSON can be imported on another machine - groups merge automatically.",
            "Pasting several lines creates several commands.",
        ]),
        ("Search", [
            "Type a keyword and press Enter: it searches **the whole log, history included**, and "
            "lists hits as `line-number  text`.",
            "Select log text and right-click Find to replace the old query and search literally. "
            "A multi-line selection matches any of its lines.",
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
            "Ships with *Launcher*: every open app window gets its own icon. Click to switch to "
            "that exact window; hover to see its full title.",
            "Right-click to open another window or remove the shortcut. Removal does not close "
            "the running app.",
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
