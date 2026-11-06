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
            "滚动到底部自动跟随最新，上滑离开底部自动停止，开关同步变化；手动开启开关或点「回到最新」恢复跟随。停止跟随不停止接收或打印。",
            "左栏与发送栏的开关每次都从默认状态开始，不记忆上次的选择（因此不会因为上次关了回显就看不到自己的输入）："
            "自动重连、时间戳、回显、自动滚动默认开，HEX 显示、控制字符、保持屏幕常亮、回车发送默认关，DTR / RTS 默认不拉。"
            "波特率、数据位等下拉框仍然记住。",
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
            "这两个开关同样不记忆，每次启动都回到不拉的状态。",
        ]),
        ("文件传输页", [
            "选择 XMODEM / YMODEM / ZMODEM，设置方向与文件，点开始。",
            "接收的文件保存在 `omnicom-data/received/`。",
            "传输期间串口被独占，日志页不打印，避免把协议字节弄脏。",
        ]),
        ("扩展命令", [
            "右侧栏的命令可以点一下直接发送，也可以勾选多条后「依次发送」（每条按自己的延时）。",
            "按住一条命令就能拖动，像在资源管理器里拖文件：拖到别的分组标题或空白处即换组，"
            "拖到某条命令的上/下半区则插到它之前/之后，拖到列表末尾的空条则移出所有分组；"
            "批量模式下拖动其中一条会带走全部勾选项。",
            "「分组」用来归类：点文件夹图标新建分组，可以给分组写备注；分组可折叠。创建和改名不允许与已有分组同名，首尾空格不计。",
            "每条命令与发送、编辑、删除按钮排在同一主行，色条区分选中状态；备注在命令下方以小字显示，"
            "与命令文字左对齐，悬停可查看完整内容和延时。",
            "每条命令支持备注、行尾、延时、周期定时发送、HEX。",
            "右键一条命令可以移动分组；顶部导出按钮统一选择要导出的命令或分组。",
            "导出的 JSON 在另一台电脑上「导入」即可使用，分组会自动合并。",
            "命令里用 `命令` 表示这条内容本身，它不一定是 AT 指令：`net sta enable` 这种控制台命令一样可以存。",
            "粘贴多行会拆成多条命令，`#` 之后的内容自动成为该条命令的备注，行尾多打的空格会被去掉；"
            "整行只有注释（`# ...`）时不会生成命令，所以可以一边贴命令一边写说明。"
            "命令本身确实含 `#` 时写成 `\\#`。",
        ]),
        ("关键字搜索", [
            "输入关键字回车，会搜索**整个日志（含此前的历史）**，结果按「行号 + 内容」列出。",
            "选中日志文字后右键「查找」，会替换旧查询并立即按普通文本搜索；多行选区按行匹配任一项。",
            "双击任意一行跳到日志原文并高亮。",
            "匹配方式：普通 / 模糊（字符按顺序出现即可）/ 全词 / 正则表达式；另有「区分大小写」开关。",
            "勾选「限定时间范围」可以只搜某一段时间：打开时会自动取日志自身的第一条和最后一条时间作为起止，"
            "也可以手动改，或点「今天」/「全部」快速切换。日志时间戳只显示时分秒，所以过滤按**完整日期时间**比较，"
            "跨天的同一时刻不会被混在一起；起止写反会直接提示，没有结果时也会说明是时间段内没有数据。",
            "右上角箭头按钮可把结果全窗口显示，Esc 退出。",
            "侧栏搜索一行一条，长内容可切到全屏完整查看；仅全屏自动按字符换行，不用横向滚动条。全量搜索从首条开始，滚到底自动跟随、上滑停止，搜索结果不提供专用自动滚动开关或最新按钮。全屏与侧栏之间切换不卡顿：行高按字符数直接算并缓存，不再逐行排版。",
        ]),
        ("插件", [
            "「插件 → 插件商店」展示库中全部插件和已安装插件，列出当前与最新版本；可安装、更新、卸载，有更新时条目右上角亮红点。"
            "每个条目带版本下拉框，可选择安装指定的历史版本（含降级安装），降级与更新的校验、回滚流程完全一致；顶部搜索框可按名称/作者/描述筛选。"
            "网络失败保留本地目录，可重试。",
            "插件仓库地址在「首选项」中配置，每行一个，全部查询；同 ID 优先较新版本，同版本优先靠前仓库。单个仓库失败不影响其他来源，并保留该仓库缓存。默认目录是 Git 仓库 plugins/catalog.json，不放 Releases。",
            "曲线插件逐行接收 `[OmniCOM-chart] AA=11 BB=1.1`，支持整数、小数、负数和科学计数；变量自动出现并可逐个勾选。看板打开就在刷新，按钮默认显示「暂停」；点它只暂停画面，接收、帧计数和控件数值继续，再点「继续」恢复刷新。"
            "暂停不记忆：无论上次是暂停还是关闭，重新打开看板一律回到刷新状态，不会一打开就是冻结的画面。",
            "曲线提供时间窗、每通道点数、坐标重置、清空和 CSV 导出；历史淘汰、无效行、超长行和通道上限均显示计数。时间轴为主机接收相对时间，不是设备采样时钟。",
            "每通道保留最近 5000 个点，时间窗默认「全部」；只显示不抽稀，实时刷新不会重置缩放。",
            "控件共五种：曲线、仪表盘、数值、进度条、指示灯。进度条按卡片自己的最小/最大值把当前值折算成填充比例，"
            "下方显示范围和带单位的读数；低于下限为空、高于上限填满并变红，没有数据时显示 `--`。",
            "看板没有「编辑」开关：卡片任何时候都能直接拖动，右下角拖拽缩放，网格背景一直显着对位。",
            "曲线卡片自带横、纵两个缩放滑块，可以只放大时间轴或只放大数值轴；滑条区域按住是缩放，卡片其它位置按住仍然是拖动。看板不做整页 Ctrl+滚轮缩放（那会把卡片和工具栏一起放大，只能重启恢复）。",
            "商店从独立 HTTPS 插件目录取得安装包，校验 SHA-256、清单与接口后安装到数据目录；卸载保留 plugin-data 用户数据。插件是可信 Python 代码，不是沙箱。插件库需单独发布，主程序无需随插件更新。",
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
            "控制线也能完整控制：`lines --line-dtr on --line-rts off` 就地改 DTR / RTS（不重开串口），",
            "`reset --line dtr --low-ms 120` 打一下复位脉冲（低→等待→高，结束时保持高电平）；",
            "`lines` 同时报告 CTS / DSR，驱动看不到时返回 `null` 而不是 `false`，写 CTS / DSR 或填非法值会直接报错。",
            "硬件流控用 `--dsrdtr`（与 `--flow rtscts` 不同）。窗口开着时这些命令走窗口，界面上两个开关会同步。",
            "所有命令的 stdout 都是**一个** JSON 文档（`monitor`、`send --repeat N`、`send --ports A,B` 是每行一个），",
            "退出码与 JSON 里的 `ok` 永远一致：成功 0、失败 1、用法错误 2。stdout 固定 UTF-8，中文不会乱码。",
            "等应答用 `--expect OK --wait 2`；设备不回显的命令用 `--settle 0.4`（收到数据后静默 0.4 秒就返回，不必等满 `--wait`）。",
            "可能沉默也可能报错的命令加 `--ok-if-silent`：完全没收到数据算成功，收到字节却不含预期内容仍然算失败。",
            "高频轮询用 `--repeat 50 --interval 0.2`：同一进程只打开一次串口，每组数据输出一行并带 `sample` 编号，比反复启动快两倍以上。",
            "两个脚本同时操作同一个串口时会自动排队（最多等 `--lock-timeout` 秒，默认 20），不会把字节写串。",
            "详细字段与示例见 `docs/CLI.md`。",
        ]),
        ("软件更新", [
            "顶部菜单「软件更新」会在窗口空闲后直接读取 GitHub 发布页（失败不弹窗）；"
            "有新版本时菜单项后面带红色 NEW 文字、按钮亮红点。",
            "对话框显示当前版本和更新说明，点「下载并安装」会下载到本地，"
            "按钮随即变成「取消」，可以取消下载；下载、解压和文件准备期间**原界面保持打开并显示动态进度**。",
            "准备完成后会提示安装需要一点时间，请你确认「重启安装」；默认「稍后」，选稍后不会安装或退出。"
            "确认后由带 OmniCOM logo 和任务栏图标的独立进度窗接管；"
            "文件安装和校验完成、进度结束后，先关闭进度窗，再打开目标版本。",
            "支持结果提示的目标版本启动后只显示一次「更新成功」和实际版本；"
            "旧版没有结果回传也可以安装。若其他窗口占用程序文件，会保留旧版并说明原因。",
            "更新只覆盖程序文件：`omnicom-data/` 里的命令库、收发记录、设置和自己的插件一律不动。",
            "没有外网也能更新：点「从升级包更新」，选一个 `OmniCOM-<版本>-win64.zip`；"
            "版本号从包内读取（文件被改名也认），支持安装旧版本和重装相同版本；"
            "不是 OmniCOM 包的 zip 会被直接拒绝。",
        ]),

        ("典型工作流", [
            '**抓一段启动日志**：打开串口 → 点「收发记录」→ 给板子复位 → 复现完就点「停止记录」，文件在 `omnicom-data/logs/`，点「打开目录」直达。记录的是原始字节，界面怎么显示都不影响它。',
            '**烧完固件验证**：先在命令库建一组「验证」，把 `AT`、`AT+CGMR`、`AT+CSQ` 存成三条，设置好行尾和延时，之后每次只要点一次「依次发送」，结果直接出现在日志里。',
            '**让 AI 帮忙看**：窗口开着、串口开着，直接跑 `python -m omnicom.cli log --limit 50`，AI 读到的就是屏幕上那段内容。',
        ]),
        ("快捷键一览", [
            '`Ctrl+Enter` 发送（回车发送关闭时，回车只换行，`Shift+Enter` 也是换行）。',
            '`↑` / `↓` 在输入框第一行翻发送历史；历史默认 50 条，首选项可调到 5 ~ 300 条并一键清空。',
            "发送后输入框不会清空，方便连着发同一条命令；连续发同一内容时历史只记一条，中间插了别的内容则照常各记一条。",
            '`Ctrl+C` 复制日志选中内容（终端页是 `Ctrl+Shift+C`，因为 `Ctrl+C` 要当 0x03 发出去）。',
            '`Ctrl+滚轮` 在日志上缩放日志字号，不影响界面其它字号，缩放值会被记住。',
            '日志里右键：复制 / 复制全部 / 保存 / 查找 / 跳到最新。',
        ]),
        ("串口与设备", [
            '端口下拉框显示「端口号 + 设备名」，例如 `COM139  USB-Enhanced-SERIAL CH340`；名字太长时悬浮可看完整名称与硬件 ID。',
            '打开失败常见原因：被别的程序占着（另一个串口助手、虚拟机、厂商烧写工具）；驱动没装；线是充电线没有数据线芯。先点刷新重新枚举，再换一个口试试。',
            '拔插设备时保持「自动重连」打开，OmniCOM 会认回同一个设备，而不是同名的新设备。',
            'DTR / RTS 默认都不拉；如果板子需要拉高才启动，打开开关即可，但**不会记住**——下次启动又回到不拉，避免一开串口就把板子复位。',
        ]),
        ("多窗口", [
            '可以同时开多个窗口，每个窗口各自一个串口、各自一份日志，互不干扰；窗口的尺寸、分栏、发送框高度分别记住，下次启动复原。',
            '双击标题栏（或把窗口拖到屏幕顶部）最大/还原时，画面是滑动过渡而不是一下子跳过去；提示栏按钮、Aero Snap 同样会滑动，反复切换尺寸也不会越缩越小。',
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
            "Reaching the bottom resumes following; scrolling up stops it and synchronizes the switch. "
            "Turn the switch on or use the latest bubble to resume. Receiving and printing continue.",
            "*Keep screen awake* stops Windows from blanking or sleeping on idle - handy during a "
            "long capture. It is released automatically when OmniCOM closes, and always starts "
            "off: like every other rail / send-bar switch it is deliberately not remembered.",
            "Every switch in the left rail and the send bar opens at its default: auto-reconnect, "
            "timestamps, echo and auto-scroll on; HEX view, control chars, keep-awake and "
            "Enter-to-send off; DTR / RTS low. Nothing is remembered, so a switch turned off in a "
            "collapsed rail can never hide your echo on the next launch. The drop-downs (baud, "
            "data bits, parity, flow) are still remembered.",
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
            "DTR and RTS start **low**: many USB-serial boards wire DTR to RESET through a capacitor, so raising it would reboot the device every time the port opens. Flip the switch when you need the line high - but it is not remembered, so the next launch is safe again.",
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
            "The field is called Command, not AT: an entry may be an AT line, a bare console "
            "command such as `net sta enable`, or a fragment of either.",
            "Hold a command to drag it, just like dragging a file in Explorer: drop it on "
            "another group's header or blank area to move it there, on the top or bottom half "
            "of a row to insert before or after it, or on the empty strip at the end of the "
            "list to take it out of every group. In batch mode dragging one row takes all "
            "picked commands with it.",
            "Pasting several lines creates several commands. Text after `#` becomes that "
            "command's note and the padding before `#` is dropped; a line that holds only a "
            "comment creates nothing, so a paste can carry explanations. Write `\\#` when a "
            "command really contains a hash.",
        ]),
        ("Search", [
            "Type a keyword and press Enter: it searches **the whole log, history included**, and "
            "lists hits as `line-number  text`.",
            "Select log text and right-click Find to replace the old query and search literally. "
            "A multi-line selection matches any of its lines.",
            "Double-click any row to jump to that line in the log; it flashes.",
            "Modes: Plain / Fuzzy (characters in order) / Whole word / Regex, plus a *Match case* "
            "switch.",
            "Tick *limit to a time range* to search one window of time. Switching it on seeds the "
            "range from the log's own first and last timestamp, and *Today* / *All* jump to the "
            "common cases. Rendered timestamps carry no date, so the filter compares the full "
            "date-time: the same 08:00 on two different days never mixes. An inverted range is "
            "reported instead of silently returning nothing, and an empty window says so.",
            "The expand arrow blows the results up to the full window; Esc exits.",
            "Sidebar results use one line per hit; fullscreen results display all text with character wrapping and no horizontal scrollbar. A full search starts at the first hit; reaching the bottom resumes following and scrolling up stops it. Search results have no dedicated auto-scroll switch or latest button. Switching between the sidebar and fullscreen does not stall: row heights are computed from the character count and cached instead of laying out every row.",
        ]),
        ("Plugins", [
            "Plugins → Plugin Store lists every catalog entry and installed plugin, with installed/latest versions, install/update/uninstall actions and an upper-right red update dot."
            " Each row carries a version drop-down, so a specific (older) build can be installed; an older build goes through the identical verification and rollback path as an update, and the search box filters by name, author or description."
            " Offline errors retain the local catalog and allow retry.",
            "Configure plugin catalog URLs in Preferences, one per line. All sources are queried; newer versions win for the same ID, with the first source winning ties. One source failing does not block the rest and retains its own cache. The default index is the Git repository's plugins/catalog.json, not a Release asset.",
            "Live Charts receives one ASCII line at a time: `[OmniCOM-chart] AA=11 BB=1.1`. Integers, decimals, negative values and scientific notation are supported. Channels appear dynamically with individual visibility checkboxes. The dashboard refreshes as soon as it opens, so its button reads *Pause*; pausing freezes the picture only - reception, the frame counter and the widget values keep running - and *Resume* brings the refresh back. "
            "The pause is not remembered: whether the last dashboard was paused or closed, reopening always comes up refreshing instead of a frozen picture.",
            "Charts offer a time window, per-channel history limit, axis reset, clear and CSV export. Invalid/oversized frames, evicted history and excess channels are counted. The time axis uses relative host receive time, not the device sample clock.",
            "Each channel keeps its latest 5000 samples and the time window defaults to All; every retained point is drawn, with no down-sampling, and a live refresh never resets the zoom.",
            "There are five widget kinds: line, gauge, value, progress bar and lamp. A progress bar turns the current value into a fill proportion using the card's own minimum and maximum, with the range and a unit-suffixed readout underneath. Below the minimum the bar is empty, above the maximum it is full and turns red, and a card without data shows `--`.",
            "The dashboard has no edit switch: every card is draggable at all times, the lower-right corner resizes it, and the snap grid stays visible as a guide.",
            "Every line card carries two independent zoom sliders - horizontal under the plot, vertical at its right - so you can magnify the time axis alone or the value axis alone. Pressing a strip zooms; pressing anywhere else on the card still drags it. The dashboard has no page-wide Ctrl+wheel zoom, because that scaled the cards and the toolbar together and could only be undone by restarting.",
            "The store uses a separately published HTTPS catalog and verifies package SHA-256, metadata and API compatibility. It installs into the data directory; uninstall preserves plugin-data. Plugins are trusted Python code, not sandboxed. Plugin releases do not require an application update.",
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
            "`session --script file.json` runs a scripted exchange.",            "Control lines are fully scriptable: `lines --line-dtr on --line-rts off` moves DTR / "
            "RTS in place (no reopen), and `reset --line dtr --low-ms 120` emits a reset pulse "
            "(low, wait, high) that ends with the line high. `lines` also reports CTS / DSR; a "
            "driver that cannot see a line reports `null`, not `false`, and writing CTS / DSR or "
            "passing a bad value is an error rather than a silent success.",
            "Use `--dsrdtr` for DSR/DTR hardware handshake (distinct from `--flow rtscts`). With a "
            "window open these commands go through it, so the two switches follow.",            "Every command writes exactly **one** JSON document to stdout - `monitor`, `send --repeat N` and `send --ports A,B` write one per line.",
            "The exit code always matches `ok`: 0 success, 1 failure, 2 bad usage. stdout is UTF-8, so non-ASCII text survives the pipe.",
            "Wait for a fixed answer with `--expect OK --wait 2`; for commands that do not echo, use `--settle 0.4` to return 0.4 s after the last byte instead of burning the whole `--wait`.",
            "Add `--ok-if-silent` when silence is a valid answer: no bytes at all counts as success, but bytes that lack the expected text still fail.",
            "For polling use `--repeat 50 --interval 0.2`: one process, one port open, one JSON line per sample - more than twice as fast as starting the CLI repeatedly.",
            "Two scripts on one port queue automatically (up to `--lock-timeout` seconds, 20 by default) instead of interleaving bytes.",
            "Field-by-field reference and examples: `docs/CLI.md`.",
        ]),
        ("Software update", [
            "The *Software update* menu item reads GitHub release pages directly once the window is idle: a new version adds red NEW text and a dot on the button.",
            "The dialog shows your current version and the release notes; *Download and install* becomes *Cancel* during the download. The original window **stays open with animated progress** while files are extracted and prepared, for both downloads and local packages.",
            "When preparation finishes, a confirmation explains that installation "
            "takes a little time. *Later* is the default and does not install or "
            "exit. Confirm *Restart to install* to hand over to a branded "
            "progress window with the OmniCOM logo and taskbar icon. Installation "
            "and verification finish first; the completed progress window closes "
            "before the target version starts.",
            "Target versions that support result dialogs show success and "
            "their actual version once. Older versions can install without "
            "acknowledging the new installer. If another window locks files, "
            "the old version is kept and the failure is reported.",
            "Only program files are replaced - the command library, captures, settings and your own plugins under `omnicom-data/` are never touched.",
            "No internet needed: press *Update from a package* and pick an "
            "`OmniCOM-<version>-win64.zip`. The version comes from inside "
            "the package, even if renamed. Older versions and same-version "
            "reinstalls are supported; non-OmniCOM ZIPs are refused.",
        ]),
        ("Typical workflows", [
            '**Capture a boot log**: open the port, press *Capture RX + TX*, reset the board, then press *Stop capturing*. The file lands in `omnicom-data/logs/` - raw bytes, unaffected by how the window renders them.',
            '**Verify after flashing**: build a group called *check* with `AT`, `AT+CGMR` and `AT+CSQ`, each with its own line ending and delay; afterwards one press of *Send in order* puts the answers in the log.',
            '**Let an agent read the screen**: with the window open, run `python -m omnicom.cli log --limit 50` - the agent sees exactly what you see.',
        ]),
        ("Keyboard shortcuts", [
            '`Ctrl+Enter` sends (with Enter-to-send off, Enter just inserts a newline, as does `Shift+Enter`).',
            '`↑` / `↓` walk the send history while the cursor is on the first line; the history keeps 50 entries by default (5-300 in Preferences, clearable).',
            "Sending does not clear the box - handy for repeating one command. An immediate repeat is "
            "recorded in the history once; sending something else in between records both.",
            '`Ctrl+C` copies the selected log text (on the terminal page it is `Ctrl+Shift+C`, because `Ctrl+C` must send 0x03).',
            '`Ctrl+wheel` over the log zooms the *log* font only; the rest of the UI keeps its size and the value is remembered.',
            'Right-click in the log: copy / copy all / save / find / jump to newest.',
        ]),
        ("Ports and devices", [
            'The port list shows *port number + device name*, e.g. `COM139  USB-Enhanced-SERIAL CH340`; hover for the full name and hardware ID.',
            'Opening usually fails because another program holds the port (another terminal, a VM, a vendor flasher), the driver is missing, or the cable has no data wires. Refresh the list and try another port.',
            'Keep *auto-reconnect* on and unplugging/replugging re-attaches the same device instead of a same-named one.',
            'DTR / RTS start low (see above); switch them on for boards that need the lines high - the choice is **not** remembered, so a later launch cannot reset the board.',
        ]),
        ("Multiple windows", [
            'Several windows can run at once, each with its own port and its own log. Size, split and composer height are remembered per window (`window-<n>.ini`).',
            'Maximising and restoring (title-bar button, double-click, or dragging to the screen top for Aero Snap) slides instead of jumping, and repeated round trips never shrink the window.',
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
