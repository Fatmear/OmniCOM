"""Built-in user manual (zh / en).

The Help → Documentation dialog renders this, and ``tools/make_readme.py``
reuses the same text for README.md / README.en.md so the two can never drift.
"""

from __future__ import annotations

MANUAL: dict[str, list[tuple[str, list[str]]]] = {
    "zh": [
        ("快速开始", [
            "双击 `start.cmd`，或运行 `python run.py`。没有硬件时加 `--demo` 会多出一个虚拟串口 DEMO。",
            "启动时不会另外弹出命令行窗口；想保留控制台输出，加 `--keep-console`。",
            "要拷给别人用，运行 `python tools/make_dist.py --zip`，把生成的 `dist/OmniCOM/` 目录"
            "（或同名 zip）整个发过去，对方双击里面的 `OmniCOM.cmd` 即可：包内自带 Python 运行时与全部"
            "依赖，不需要另外安装任何东西。",
            "左侧选择串口 → 设置波特率等参数 → 点「打开串口」，接收的数据会立刻出现在「串口日志」页。",
            "自定义波特率可直接点击输入，按回车或移开焦点后应用；也可以从下拉列表选择常用值。",
            "底部输入框写内容，Ctrl+Enter 或点「发送」发出。行尾、HEX、定时发送都在输入框右侧。",
            "拖动日志与发送区之间的分隔条可直接增高输入框，高度会在下次启动时恢复。",
        ]),
        ("串口日志页", [
            "每一行的格式是 `[时:分:秒.毫秒]收←◇内容` 或 `发→◇内容`。",
            "「自动滚动到最新」打开时永远跟着最新一行；关掉后可以自由翻阅历史，底部会浮出「回到最新」气泡。",
            "「保持屏幕常亮」打开后系统不会因空闲息屏或睡眠，适合长时间盯串口；关闭应用会自动恢复。",
            "「显示 \\r \\n 等控制字符」对收和发同时生效，能看到真实发出的换行。",
            "「HEX 显示」按 16 字节一行显示偏移 + 十六进制 + ASCII。",
            "「保存视图」把当前日志写到文件；「收发记录」在后台把收发两个方向的原始字节写进同一个文件，"
            "与界面显示无关，不会漏字节；「清空记录文件」只清掉已写的内容，记录继续。",
            "清空日志时，顶部栏的 RX / TX 计量也一起归零，之后从零重新统计。",
            "日志按大小缓冲：默认保留 100 MB 文字（首选项可改成 4 ~ 500 MB），满了就丢弃最早的行、"
            "始终保留最新的内容，被丢弃的行数会显示在日志工具栏上，接再快也不会卡住界面。",
            "发送文件分两步：先「打开文件」选好待发文件，再「发送文件」；发送的内容按普通发送打印到日志，"
            "下方进度条显示传输进度。",
            "日志框右键可以复制、复制全部、保存、查找、跳到最新。",
            "串口按钮在未连接时为中性颜色，成功打开后才着色；开关在后台执行，期间按钮暂时不可用。",
        ]),
        ("终端模式页", [
            "这是给 linux 串口控制台用的：点一下终端区域，键盘输入直接送进串口。",
            "Enter 发送回车（可选 CR / LF / CRLF），Ctrl+C 发送 0x03，方向键发送 ANSI 序列。",
            "支持 CR 回到行首覆盖、退格、TAB 对齐、ANSI 颜色，以及清屏序列。",
            "复制用 Ctrl+Shift+C，粘贴用 Ctrl+Shift+V（普通 Ctrl+C 会当作中断字符发出）。",
        ]),
        ("串口参数", [
            "左侧的 DTR / RTS 默认**都不拉**：很多 USB 串口板子把 DTR 经电容接到 RESET，"
            "拉高等于一开串口就复位；需要时再打开开关。",
        ]),
        ("文件传输页", [
            "选择 XMODEM / YMODEM / ZMODEM，设置方向与文件，点开始。",
            "接收的文件保存在 `omnicom-data/received/`。",
            "传输期间串口被独占，日志页不打印，避免把协议字节弄脏。",
        ]),
        ("扩展命令", [
            "右侧栏的命令可以点一下直接发送，也可以勾选多条后「依次发送」（每条按自己的延时）。",
            "「分组」用来归类：点文件夹图标新建分组，可以给分组写备注；分组可折叠。创建和改名不允许与已有分组同名，首尾空格不计。",
            "每条命令与发送、编辑、删除按钮排在同一主行，色条区分选中状态；备注在命令下方以小字显示，"
            "与命令文字左对齐，悬停可查看完整内容和延时。",
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
            "自带「快捷启动」插件：上方是快捷方式（点一下新开一个窗口），下方是正在运行的窗口（点一下切过去）；",
            "两排都支持拖拽排序，右键可以重命名——下排按窗口命名，所以两个记事本可以分别叫 A、B，长名字会自动换行不遮挡。",
            "上排「从列表移除」只删快捷方式，不关闭已开窗口；下排「结束这个窗口」只关那一个窗口。",
        ]),
        ("首选项与语言", [
            "「设置 → 首选项」里可以切换界面语言：中文环境只显示中文，英文环境只显示英文，包括右键菜单。",
            "同一页还能设置日志缓冲大小（4 ~ 500 MB，默认 100 MB）和「收发记录」的默认保存目录、"
            "切换主题色（只改强调色与选中高亮），"
            "或选一张图片当壁纸：有壁纸时各面板会变成液态玻璃（取面板后方的模糊壁纸再叠一层半透明白），"
            "鼠标提示框也是同款玻璃；没有壁纸就是原来的纯白面板。底部的状态文字不加框也不加底色，直接压在背景上。",
            "切换立即生效，不需要重启。",
        ]),
        ("命令行（给脚本 / AI 用）", [
            "`python -m omnicom.cli ports` 列出串口；`send` 发送并等待应答；`read` 读取；`monitor` 流式输出。",
            "`session --script 文件.json` 可以跑一段脚本化的收发流程。",
            "所有命令的 stdout 都是**一个** JSON 文档（`monitor`、`send --repeat N`、`send --ports A,B` 是每行一个），",
            "退出码与 JSON 里的 `ok` 永远一致：成功 0、失败 1、用法错误 2。stdout 固定 UTF-8，中文不会乱码。",
            "等应答用 `--expect OK --wait 2`；设备不回显的命令用 `--settle 0.4`（收到数据后静默 0.4 秒就返回，不必等满 `--wait`）。",
            "可能沉默也可能报错的命令加 `--ok-if-silent`：完全没收到数据算成功，收到字节却不含预期内容仍然算失败。",
            "高频轮询用 `--repeat 50 --interval 0.2`：同一进程只打开一次串口，每组数据输出一行并带 `sample` 编号，比反复启动快两倍以上。",
            "两个脚本同时操作同一个串口时会自动排队（最多等 `--lock-timeout` 秒，默认 20），不会把字节写串。",
            "详细字段与示例见 `docs/CLI.md`。",
        ]),
        ("软件更新", [
            "顶部菜单「软件更新」会在窗口空闲后安静地查一次 GitHub（失败不弹窗）；"
            "有新版本时菜单项后面带 NEW、按钮亮红点。",
            "对话框显示当前版本和更新说明，点「下载并安装」会下载到本地，"
            "**下次启动**在窗口出现前完成替换。",
            "更新只覆盖程序文件：`omnicom-data/` 里的命令库、收发记录、设置和自己的插件一律不动。",
            "没有外网也能更新：点「从升级包更新」，选一个 `OmniCOM-<版本>-win64.zip`；"
            "版本号从包内读取（文件被改名也认），不是 OmniCOM 包的 zip 会被直接拒绝。",
        ]),

        ("典型工作流", [
            '**抓一段启动日志**：打开串口 → 点「收发记录」→ 给板子复位 → 复现完就点「停止记录」，文件在 `omnicom-data/logs/`，点「打开目录」直达。记录的是原始字节，界面怎么显示都不影响它。',
            '**烧完固件验证**：先在命令库建一组「验证」，把 `AT`、`AT+CGMR`、`AT+CSQ` 存成三条，设置好行尾和延时，之后每次只要点一次「依次发送」，结果直接出现在日志里。',
            '**让 AI 帮忙看**：窗口开着、串口开着，直接跑 `python -m omnicom.cli log --limit 50`，AI 读到的就是屏幕上那段内容。',
        ]),
        ("快捷键一览", [
            '`Ctrl+Enter` 发送（回车发送关闭时，回车只换行，`Shift+Enter` 也是换行）。',
            '`↑` / `↓` 在输入框第一行翻发送历史；历史默认 50 条，首选项可调到 5 ~ 300 条并一键清空。',
            '`Ctrl+C` 复制日志选中内容（终端页是 `Ctrl+Shift+C`，因为 `Ctrl+C` 要当 0x03 发出去）。',
            '`Ctrl+滚轮` 在日志上缩放日志字号，不影响界面其它字号，缩放值会被记住。',
            '日志里右键：复制 / 复制全部 / 保存 / 查找 / 跳到最新。',
        ]),
        ("串口与设备", [
            '端口下拉框显示「端口号 + 设备名」，例如 `COM139  USB-Enhanced-SERIAL CH340`；名字太长时悬浮可看完整名称与硬件 ID。',
            '打开失败常见原因：被别的程序占着（另一个串口助手、虚拟机、厂商烧写工具）；驱动没装；线是充电线没有数据线芯。先点刷新重新枚举，再换一个口试试。',
            '拔插设备时保持「自动重连」打开，OmniCOM 会认回同一个设备，而不是同名的新设备。',
            'DTR / RTS 默认都不拉；如果板子需要拉高才启动，打开开关即可，这个选择会记住。',
        ]),
        ("多窗口", [
            '可以同时开多个窗口，每个窗口各自一个串口、各自一份日志，互不干扰；窗口的尺寸、分栏、发送框高度分别记住，下次启动复原。',
            '「快捷启动」插件的上排是快捷方式，点一下就是新开一个窗口——同时调两块板子时，一块板一个窗口，抓日志不会串。',
            '命令行同样能区分：它按**串口名**找窗口。`cli state --port COM5` 只会问开着 COM5 的那个窗口；想指定某个窗口还可以 `--window <PID>`。',
        ]),
        ("备份与迁移", [
            '整个 `omnicom-data/` 目录就是全部用户数据：命令库、收发记录、收到的文件、设置、自己的插件。换电脑时把这个目录拷过去即可（也可以用 `OMNICOM_DATA_DIR` 把它放到同步盘里）。',
            '升级（在线或本地升级包）只替换程序文件，`omnicom-data/` 一律不动，你自己放在 `plugins/` 里的插件也不会被包里的同名插件覆盖。',
            '命令库还可以单独导出成 JSON，只带某几条或某一组，方便同事之间传。',
        ]),
        ("遇到问题先看这里", [
            '**日志不刷新 / 停在某一行**：检查「自动滚动到最新」是否被关掉；关掉时底部会浮出「回到最新」气泡。',
            '**收到一半乱码**：多半是波特率和设备不一致，或者开了 `HEX 显示` 却在看文本；也可能是控制字符被隐藏，打开「显示控制字符」就能看清楚。',
            '**发送没反应**：确认串口已打开、行尾设置和设备要求一致（很多 AT 设备必须要 CRLF）；开了「HEX 发送」时输入框里必须是十六进制，纯文本不会发出去。',
            '**文件传输卡住**：协议传输期间串口被独占，两端必须同时进入同一个协议；接收端要先进入接收模式，再点发送。',
            '**界面被拉坏**：`设置 → 重置布局` 恢复默认三栏，窗口大小和分栏都会回到初始值。',
            '**想确认有没有丢日志**：日志工具栏显示「已丢弃 N 行」，N 一直是 0 就说明一条都没丢。',
        ]),
        ("数据放在哪", [
            "`omnicom-data/logs/` 收发记录（首选项改了目录后按新目录）；`omnicom-data/received/` 协议收到的文件；",
            "右下角「记录目录」显示的就是当前实际保存收发记录的位置，点「打开目录」会直接打开它。",
            "`omnicom-data/commands.json` 扩展命令；`omnicom-data/plugins/` 后加的插件。",
        ]),
    ],
    "en": [
        ("Quick start", [
            "Double-click `start.cmd`, or run `python run.py`. Add `--demo` for a virtual DEMO port.",
            "No extra console window opens on launch; add `--keep-console` to keep one.",
            "To hand the app to someone else run `python tools/make_dist.py --zip` and send the "
            "`dist/OmniCOM/` folder (or the zip of the same name): it bundles the Python runtime "
            "and every dependency, so nothing has to be installed - `OmniCOM.cmd` starts the app.",
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
            "*Keep screen awake* stops Windows from blanking or sleeping on idle - handy during a "
            "long capture. It is released automatically when OmniCOM closes.",
            "*Show \\r \\n and other control chars* applies to both directions, so you can see the "
            "line ending that was really sent.",
            "*HEX view* prints 16 bytes per line: offset, hex, ASCII.",
            "Clearing the log also resets the RX/TX counters in the title bar.\n"
            "The log is buffered by size: 100 MB of text by default (4-500 MB in Preferences).\n"
            "When it is full the oldest lines are dropped and the newest are always kept; the log\n"
            "toolbar reports how many lines went, and a fast peer never stalls the window.\n"
            "File sending is two steps: *Open file* chooses the pending file, *Send file* transmits it;\n"
            "the bytes are printed as normal send lines and a progress bar tracks the transfer.",
            "*Save view* writes the log to a file. *Capture RX + TX* writes every byte of both "
            "directions to one file in the background and never misses a byte, regardless of "
            "what the UI shows. *Clear capture file* empties it and keeps recording.",
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
        ("Serial parameters", [
            "DTR and RTS start **low**: many USB-serial boards wire DTR to RESET through a capacitor, so raising it would reboot the device every time the port opens. Flip the switch when you need the line high.",
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
            "Ships with *Launcher*: the top row holds shortcuts (click opens a new window) and the "
            "bottom row holds running windows (click to switch to that window).",
            "Both rows can be reordered by dragging. Rename a running window to tell two instances "
            "apart (A and B); long names wrap instead of being covered.",
            "Removing a shortcut keeps its windows open; *End this window* closes just that one window.",
        ]),
        ("Preferences and language", [
            "*Settings → Preferences* switches the interface language. Chinese shows only Chinese, "
            "English only English - context menus included.",
            "The same page sets the log buffer size (4-500 MB, 100 MB by default), the default "
            "capture folder, a solid accent colour, or a wallpaper "
            "image. With a wallpaper the panes - and the hover tooltip - become liquid glass: the "
            "blurred photo behind them plus a translucent white wash; without one they stay plain "
            "white. The bottom status captions have no frame or fill at all, just text.",
            "The change applies immediately, no restart.",
        ]),
        ("Command line (for scripts / AI)", [
            "`python -m omnicom.cli ports` lists ports; `send` sends and waits for a reply; `read` "
            "reads; `monitor` streams.",
            "`session --script file.json` runs a scripted exchange.",
            "Every command writes exactly **one** JSON document to stdout - `monitor`, `send --repeat N` and `send --ports A,B` write one per line.",
            "The exit code always matches `ok`: 0 success, 1 failure, 2 bad usage. stdout is UTF-8, so non-ASCII text survives the pipe.",
            "Wait for a fixed answer with `--expect OK --wait 2`; for commands that do not echo, use `--settle 0.4` to return 0.4 s after the last byte instead of burning the whole `--wait`.",
            "Add `--ok-if-silent` when silence is a valid answer: no bytes at all counts as success, but bytes that lack the expected text still fail.",
            "For polling use `--repeat 50 --interval 0.2`: one process, one port open, one JSON line per sample - more than twice as fast as starting the CLI repeatedly.",
            "Two scripts on one port queue automatically (up to `--lock-timeout` seconds, 20 by default) instead of interleaving bytes.",
            "Field-by-field reference and examples: `docs/CLI.md`.",
        ]),
        ("Software update", [
            "The *Software update* menu item checks GitHub once the window is idle: a new version marks the item with NEW and puts a dot on the button.",
            "The dialog shows your current version and the release notes; *Download and install* stages the release and **the next start** replaces the program before the window appears.",
            "Only program files are replaced - the command library, captures, settings and your own plugins under `omnicom-data/` are never touched.",
            "No internet needed: press *Update from a package* and pick an `OmniCOM-<version>-win64.zip`. The version is read from inside the package (a renamed file still reports its real version), and a zip that is not an OmniCOM bundle is refused.",
        ]),
        ("Typical workflows", [
            '**Capture a boot log**: open the port, press *Capture RX + TX*, reset the board, then press *Stop capturing*. The file lands in `omnicom-data/logs/` - raw bytes, unaffected by how the window renders them.',
            '**Verify after flashing**: build a group called *check* with `AT`, `AT+CGMR` and `AT+CSQ`, each with its own line ending and delay; afterwards one press of *Send in order* puts the answers in the log.',
            '**Let an agent read the screen**: with the window open, run `python -m omnicom.cli log --limit 50` - the agent sees exactly what you see.',
        ]),
        ("Keyboard shortcuts", [
            '`Ctrl+Enter` sends (with Enter-to-send off, Enter just inserts a newline, as does `Shift+Enter`).',
            '`↑` / `↓` walk the send history while the cursor is on the first line; the history keeps 50 entries by default (5-300 in Preferences, clearable).',
            '`Ctrl+C` copies the selected log text (on the terminal page it is `Ctrl+Shift+C`, because `Ctrl+C` must send 0x03).',
            '`Ctrl+wheel` over the log zooms the *log* font only; the rest of the UI keeps its size and the value is remembered.',
            'Right-click in the log: copy / copy all / save / find / jump to newest.',
        ]),
        ("Ports and devices", [
            'The port list shows *port number + device name*, e.g. `COM139  USB-Enhanced-SERIAL CH340`; hover for the full name and hardware ID.',
            'Opening usually fails because another program holds the port (another terminal, a VM, a vendor flasher), the driver is missing, or the cable has no data wires. Refresh the list and try another port.',
            'Keep *auto-reconnect* on and unplugging/replugging re-attaches the same device instead of a same-named one.',
            'DTR / RTS start low (see above); switch them on for boards that need the lines high - the choice is remembered per machine.',
        ]),
        ("Multiple windows", [
            'Several windows can run at once, each with its own port and its own log. Size, split and composer height are remembered per window (`window-<n>.ini`).',
            'The launcher plugin opens a new window per shortcut, so two boards never share a log.',
            'The CLI routes by **port name**: `cli state --port COM5` asks the window that owns COM5 and nothing else; `--window <PID>` pins a specific window.',
        ]),
        ("Backup and migration", [
            '`omnicom-data/` is the whole user state: command library, captures, received files, settings and your own plugins. Copy it to move machines, or point `OMNICOM_DATA_DIR` at a synced folder.',
            'An upgrade (online or from a local package) replaces program files only; `omnicom-data/` is untouched and your plugins are never overwritten by same-named ones from the package.',
            'The command library also exports to JSON - one command or a whole group - which is the easy way to hand a set to a colleague.',
        ]),
        ("Troubleshooting", [
            '**The log stops updating**: check *auto-scroll*; when it is off a *jump to newest* bubble appears at the bottom.',
            '**Garbled text**: usually a baud-rate mismatch, or *HEX display* being on while reading a text protocol; also enable *show control characters* to see what actually arrived.',
            '**Nothing is sent**: the port must be open, the line ending must match the device (most AT devices need CRLF), and with *HEX send* on the box must contain hex.',
            '**A transfer hangs**: both sides must enter the same protocol; the receiver has to be in receive mode before you start sending.',
            '**The layout looks wrong**: *Settings -> Reset layout* restores the three columns and the window size.',
            '**Did the log drop anything?**: the log toolbar shows *dropped N lines*; a steady 0 means nothing was lost.',
        ]),
        ("Where the data lives", [
            "`omnicom-data/logs/` captures (or the folder chosen in Preferences), "
            "`omnicom-data/received/` transferred files,",
            "`omnicom-data/commands.json` commands, `omnicom-data/plugins/` extra plugins. The "
            "*Records* label in the bottom-right corner shows the folder captures really go to.",
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
