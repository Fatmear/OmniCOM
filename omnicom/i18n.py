"""Tiny translation layer (zh / en).

Design notes
------------
* One table, keyed by a stable dotted key; each entry is ``(中文, English)``.
* ``tr()`` is a plain function so it can be used anywhere, including plugin
  code and Qt-painted widgets.
* ``bind()`` registers a setter together with its key.  A language switch calls
  every setter with freshly translated text, which is what keeps the whole UI
  (including the log context menu) consistent without rebuilding the window.
* Untranslated keys fall back to the Chinese string, then to the key itself, so
  a missing entry is loud but never crashes.
"""

from __future__ import annotations

from collections.abc import Callable

LANGS = ("zh", "en")
LANG_NAMES = {"zh": "中文", "en": "English"}

_TABLE: dict[str, tuple[str, str]] = {
    # ---------------------------------------------------------------- window
    "app.name": ("OmniCOM", "OmniCOM"),
    "app.tagline": ("嵌入式串口调试器", "EMBEDDED SERIAL DEBUGGER"),
    "win.minimize": ("最小化", "Minimise"),
    "win.maximize": ("最大化", "Maximise"),
    "win.restore": ("还原", "Restore"),
    "win.close": ("关闭", "Close"),
    "win.sidebar": ("显示 / 隐藏右侧栏", "Show / hide the right sidebar"),
    "win.tab.terminal": ("串口日志", "Serial log"),
    "win.tab.console": ("终端模式", "Terminal"),
    "win.tab.transfer": ("文件传输", "File transfer"),
    # ------------------------------------------------------------------ rail
    "rail.connection": ("连接", "Connection"),
    "rail.port.tip": ("选择串口；已打开时切换会立即关闭旧串口并连上新串口",
                      "Pick a port. Switching while connected re-opens on the new one"),
    "rail.refresh": ("刷新串口列表", "Refresh the port list"),
    "rail.open": ("打开串口", "Open port"),
    "rail.close": ("关闭串口", "Close port"),
    "rail.cancel": ("取消重连", "Cancel reconnect"),
    "rail.autoreconnect": ("自动重连", "Auto-reconnect"),
    "rail.autoreconnect.tip": ("串口被拔出后，重新插入会自动打开（按路径或序列号识别）",
                               "Re-opens automatically after the device is plugged back in"),
    "rail.params": ("串口参数", "Port parameters"),
    "rail.baud": ("波特率", "Baud rate"),
    "rail.baud.tip": ("点一下弹出常用波特率；也可以直接输入任意数值",
                      "Click for common rates; you can still type any value"),
    "rail.databits": ("数据位", "Data bits"),
    "rail.parity": ("校验位", "Parity"),
    "rail.stopbits": ("停止位", "Stop bits"),
    "rail.flow": ("流控", "Flow control"),
    "flow.none": ("无", "None"),
    "flow.rtscts": ("硬件 (RTS/CTS)", "Hardware (RTS/CTS)"),
    "flow.xonxoff": ("软件 (XON/XOFF)", "Software (XON/XOFF)"),
    "rail.display": ("显示", "Display"),
    "rail.hex": ("HEX 显示", "HEX view"),
    "rail.ts": ("时间戳（毫秒）", "Timestamps (ms)"),
    "rail.ctrl": ("显示 \\r \\n 等控制字符", "Show \\r \\n and other control chars"),
    "rail.ctrl.tip": ("打开后把不可见字符按 \\r \\n \\0 \\xNN 显示为文本",
                      "Renders invisible characters as \\r \\n \\0 \\xNN"),
    "rail.echo": ("回显发送内容", "Echo sent data"),
    "rail.scroll": ("自动滚动到最新", "Auto-scroll to latest"),
    "rail.scroll.tip": ("打开后始终跟着最新一行；关闭后可自由翻阅历史，底部会浮出「回到最新」按钮",
                        "Keeps following the newest line. Turn it off to read history - a "
                        "'back to latest' bubble appears"),
    "rail.awake": ("保持屏幕常亮", "Keep screen awake"),
    "rail.awake.tip": ("打开后系统不会因空闲息屏或睡眠，适合长时间盯着串口日志；"
                       "关闭应用时自动恢复", "Stops the system from sleeping or blanking the screen "
                       "while watching a long capture. Released when the app closes"),
    "rail.awake.failed": ("当前系统不支持阻止息屏，开关未生效",
                          "This system refused the keep-awake request; the switch had no effect"),
    "rail.record": ("收发记录", "Capture RX + TX"),
    "rail.window": ("窗口", "Window"),
    "rail.record.start": ("●  开始记录到文件", "●  Start capturing to a file"),
    "rail.record.stop": ("■  停止记录", "■  Stop capturing"),
    "rail.record.hint": ("与日志窗口一致，每条都带时间与收发方向。",
                         "Mirrors the log window: time and direction on every line."),
    "rail.record.clear": ("清空记录文件", "Clear capture file"),
    "rail.record.clear.tip": ("把当前记录文件清空后继续记录，不中断采集",
                              "Empty the current capture file and keep recording"),
    "rail.record.cleared": ("已清空 {name}，记录继续", "Emptied {name}; capture continues"),
    "rail.record.none": ("还没有记录文件，请先开始记录",
                         "No capture file yet - start capturing first"),
    "rail.record.size": ("{path}\n已记录 {size}",
                         "{path}\nRecorded {size}"),
    "rail.opendir": ("打开目录", "Open folder"),
    "rail.saveview": ("保存视图", "Save view"),
    # -------------------------------------------------------------- terminal
    "rail.log": ("日志", "Log"),
    "log.clear.tip": ("清空日志窗口里的内容（不影响已记录的文件）",
                      "Empty the log window (the capture file is untouched)"),
    "log.save.tip": ("把当前日志内容另存为文本文件", "Save the current log text to a file"),
    "log.clear": ("清空", "Clear"),
    "log.save": ("保存视图", "Save view"),
    # Buttons in the rail act on the *window*, not on the capture file, so they
    # say so (R-102).  ``log.clear`` / ``log.save`` stay: the log itself prints
    # 清空 as a system line, and the log context menu uses its own keys.
    "log.clear.btn": ("清除窗口", "Clear window"),
    "log.save.btn": ("保存窗口", "Save window"),
    "log.latest": ("回到最新", "Back to latest"),
    "log.hint": ("选择串口并打开，接收的数据会实时出现在这里",
                 "Pick a port and open it - incoming data shows up here"),
    "log.trim": ("视图已隐藏较早的 {n} 行", "{n} earlier lines are hidden from the view"),
    "log.trim.status": ("视图已隐藏 {n} 行（建议开启记录到文件）",
                        "{n} lines hidden - consider capturing to a file"),
    "log.menu.copy": ("复制", "Copy"),
    "log.menu.copyall": ("复制全部", "Copy all"),
    "log.menu.selectall": ("全选", "Select all"),
    "log.menu.clear": ("清空日志", "Clear the log"),
    "log.menu.save": ("保存视图到文件…", "Save the view to a file…"),
    "log.menu.find": ("查找…", "Find…"),
    "log.menu.gotolatest": ("跳到最新", "Jump to latest"),
    "log.empty": ("当前视图为空，没有可保存的内容", "The view is empty - nothing to save"),
    "log.saved": ("已保存 {n} 行到 {name}", "Saved {n} lines to {name}"),
    "log.savefail": ("保存失败：{err}", "Save failed: {err}"),
    "log.savedialog": ("保存当前视图", "Save the current view"),
    "log.filter.text": ("日志文件 (*.log);;文本文件 (*.txt);;所有文件 (*)",
                        "Log files (*.log);;Text files (*.txt);;All files (*)"),
    # --------------------------------------------------------------- console
    "console.title": ("终端模式 · 键盘直达串口", "Terminal · keystrokes go straight to the port"),
    "console.enter": ("回车发送", "Enter sends"),
    "console.clearscreen": ("清屏", "Clear screen"),
    "console.reset": ("清屏并重置终端状态", "Clear and reset the terminal"),
    "console.hint": (
        "点击终端区域后直接用键盘输入：Enter 发送回车，Ctrl+C 发送 0x03，方向键发送 ANSI 序列。"
        "复制用 Ctrl+Shift+C。",
        "Click the terminal and type: Enter sends CR, Ctrl+C sends 0x03, arrow keys send ANSI "
        "sequences. Copy with Ctrl+Shift+C."),
    "console.needport": ("请先打开串口再使用终端模式", "Open a port before using the terminal"),
    # ------------------------------------------------------------ status bar
    "status.ready": ("就绪", "Ready"),
    "status.connected": ("已连接 {port} · {baud} bps", "Connected {port} · {baud} bps"),
    "status.reconnecting": ("串口已断开，等待设备重新插入…",
                            "Port lost - waiting for the device to come back…"),
    "status.chip.ok": ("{port} 已连接", "{port} connected"),
    "status.chip.retry": ("正在重新连接…", "Reconnecting…"),
    "status.chip.error": ("连接失败", "Connection failed"),
    "status.chip.idle": ("未连接", "Not connected"),
    "status.datadir": ("记录目录 {path}", "Records {path}"),
    # ---------------------------------------------------------------- search
    "search.title": ("关键字搜索", "Search"),
    "search.placeholder": ("关键字，回车查找全部（含历史）",
                           "Keyword - press Enter to find all (history included)"),
    "search.find": ("查找全部（回车）", "Find all (Enter)"),
    "search.mode.all": ("包含全部", "Match all"),
    "search.mode.any": ("包含任一", "Match any"),
    "search.mode.tip": ("多个关键字之间的关系", "How multiple keywords combine"),
    "search.match.plain": ("普通匹配", "Plain"),
    "search.match.fuzzy": ("模糊匹配", "Fuzzy"),
    "search.match.word": ("全词匹配", "Whole word"),
    "search.match.regex": ("正则表达式", "Regex"),
    "search.case": ("区分大小写", "Match case"),
    "search.fold": ("折叠 / 展开", "Collapse / expand"),
    "search.fullscreen": ("全屏查看搜索结果", "Show results full screen"),
    "search.exitfull": ("退出全屏", "Exit full screen"),
    "search.clear": ("清空搜索结果", "Clear the results"),
    "search.hint": ("输入关键字后回车，会搜索整个日志并列出所有命中行，双击可跳到原文",
                    "Press Enter to search the whole log; double-click a row to jump to it"),
    "search.hint.hits": ("双击任意一行即可跳到日志中的原文位置（Esc 退出全屏）",
                         "Double-click a row to jump to it in the log (Esc to exit)"),
    "search.hint.emptyfull": ("输入关键字后回车：搜索结果会占满整个窗口，双击可跳到日志原文（Esc 退出全屏）",
                              "Press Enter: results fill the window; double-click to jump (Esc to exit)"),
    "search.fulltitle": ("搜索结果 · 全窗口", "Search results · full window"),
    "search.badregex": ("正则表达式无效：{err}", "Invalid regular expression: {err}"),
    # -------------------------------------------------------------- commands
    "cmd.title": ("扩展命令", "Commands"),
    "cmd.collapse": ("折叠 / 展开", "Collapse / expand"),
    "cmd.paste": ("粘贴多行命令，自动拆分导入", "Paste several lines and import them"),
    "cmd.paste.group": ("导入到分组", "Import into"),
    "cmd.paste.group.tip": ("粘贴导入的命令放进这个分组",
                            "Pasted commands are created in this group"),
    "cmd.paste.nogroup": ("不分组", "No group"),
    "cmd.batch": ("批量操作", "Batch"),
    "cmd.batch.pick": ("勾选以批量处理", "Tick to include in the batch"),
    "cmd.batch.count": ("已选 {n} 条", "{n} selected"),
    "cmd.batch.all": ("全选", "All"),
    "cmd.batch.none": ("全不选", "None"),
    "cmd.batch.exit": ("完成", "Done"),
    "cmd.batch.move": ("移动", "Move"),
    "cmd.batch.delete": ("删除", "Delete"),
    "cmd.batch.moved": ("已把 {n} 条命令移动到「{name}」",
                        "Moved {n} commands to \u201c{name}\u201d"),
    "cmd.batch.deleted": ("已删除 {n} 条命令", "Deleted {n} commands"),
    "cmd.add": ("新增命令", "New command"),
    "cmd.addgroup": ("新增分组", "New group"),
    "cmd.addtogroup": ("在此分组中新增命令", "Add a command to this group"),
    "cmd.moved": ("已移动到「{name}」", "Moved to \u201c{name}\u201d"),
    "cmd.sendall": ("依次发送", "Send in order"),
    "cmd.export": ("导出", "Export"),
    "cmd.import": ("导入", "Import"),
    "cmd.send": ("发送", "Send"),
    "cmd.edit": ("编辑：行尾 / 延时 / 定时", "Edit: line end / delay / timer"),
    "cmd.delete": ("删除", "Delete"),
    "cmd.enabled.tip": ("勾选后参与「依次发送」", "Included when sending them in order"),
    "cmd.note": ("备注", "Note"),
    "cmd.group": ("分组", "Group"),
    "cmd.ungrouped": ("未分组", "Ungrouped"),
    "cmd.group.name": ("分组名称", "Group name"),
    "cmd.group.duplicate": ("已存在名为「{name}」的分组，请使用其他名称",
                            "A group named \"{name}\" already exists. Use another name."),
    "cmd.dialog.title": ("新增命令", "New command"),
    "cmd.dialog.edittitle": ("编辑命令", "Edit command"),
    "cmd.dialog.paste": ("粘贴多行命令", "Paste several commands"),
    "cmd.dialog.cancel": ("取消", "Cancel"),
    "cmd.dialog.add": ("添加", "Add"),
    "cmd.dialog.save": ("保存", "Save"),
    "cmd.dialog.confirmimport": ("导入", "Import"),
    "cmd.dialog.export": ("导出命令", "Export commands"),
    "cmd.dialog.exportpick": ("选择要导出的命令", "Choose the commands to export"),
    "cmd.dialog.selectall": ("全选", "Select all"),
    "cmd.dialog.exportsingle": ("导出这一条", "Export this one"),
    "cmd.dialog.exportgroup": ("导出这个分组", "Export this group"),
    "cmd.exported": ("已导出 {n} 条命令到 {name}", "Exported {n} commands to {name}"),
    "cmd.imported": ("已导入 {n} 条命令", "Imported {n} commands"),
    "cmd.importfail": ("导入失败：{err}", "Import failed: {err}"),
    "cmd.sent": ("已发送：{text}", "Sent: {text}"),
    "cmd.group.keep": ("删除分组，保留里面的命令", "Delete the group, keep its commands"),
    "cmd.group.drop": ("删除分组和里面的命令", "Delete the group and its commands"),
    "cmd.dialog.selectnone": ("全不选", "Select none"),
    "empty.commands": ("暂无命令\n点右上角 + 新增，或用粘贴按钮批量导入",
                       "No commands yet\nUse + to add one, or paste a list"),
    "empty.search": ("尚未搜索\n输入关键字后回车", "Nothing searched yet\nType a keyword and press Enter"),
    "empty.plugins": ("还没有插件\n把插件目录复制到 plugins/ 后点「刷新插件」",
                      "No plugins yet\nCopy a plugin folder into plugins/ then Reload"),
    # --------------------------------------------------------------- plugins
    "plugin.menu": ("插件", "Plugins"),
    "plugin.reload": ("刷新插件", "Reload plugins"),
    "plugin.opendir": ("打开插件目录", "Open the plugin folder"),
    "plugin.none": ("没有可用插件", "No plugins available"),
    "plugin.none.hint": ("把插件目录复制到 plugins/ 后点「刷新插件」",
                         "Copy a plugin folder into plugins/ then hit Reload"),
    "plugin.loaded": ("已加载 {n} 个插件", "Loaded {n} plugins"),
    "plugin.failed": ("插件 {name} 加载失败：{err}", "Plugin {name} failed: {err}"),
    "plugin.reloaded": ("插件已刷新（{n} 个）", "Plugins reloaded ({n})"),
    "plugin.folder": ("插件目录", "Plugin folder"),
    # ------------------------------------------------------------ preferences
    "menu.file": ("文件", "File"),
    "menu.help": ("帮助", "Help"),
    "menu.settings": ("设置", "Settings"),
    "menu.preferences": ("首选项…", "Preferences…"),
    # -- software update (R-107)
    "menu.update": ("软件更新", "Software update"),
    "menu.update.new": ("软件更新  NEW", "Software update  NEW"),
    "menu.update.tip": ("有新版本可用", "A newer version is available"),
    "update.title": ("软件更新", "Software update"),
    "update.checking": ("正在检查新版本…", "Checking for updates…"),
    "update.found": ("发现新版本 {version}", "Version {version} is available"),
    "update.current": ("当前版本 {current}。更新内容：", "You have {current}. What's new:"),
    "update.latest": ("已是最新版本 {version}", "You are up to date ({version})"),
    "update.install": ("下载并安装", "Download and install"),
    "update.page": ("打开下载页", "Open the release page"),
    "update.later": ("稍后再说", "Later"),
    "update.close": ("关闭", "Close"),
    "update.downloading": ("正在下载新版本…", "Downloading the new version…"),
    "update.failed": ("下载失败：{err}", "Download failed: {err}"),
    "update.staged": ("新版本 {version} 已就绪，下次启动自动完成更新（命令库、记录、设置都不会动）。",
                      "Version {version} is ready. It is applied on the next start - "
                      "commands, captures and settings are left untouched."),
    "update.applied": ("已更新到 {version}（原有数据和设置都保留了）",
                       "Updated to {version} (your data and settings were kept)"),
    "update.local": ("从升级包更新", "Update from a package"),
    "update.local.tip": ("选择 OmniCOM-<版本>-win64.zip（离线/内网环境用）",
                         "Pick OmniCOM-<version>-win64.zip (offline / intranet use)"),
    "update.local.pick": ("选择升级包", "Choose a package"),
    "update.local.filter": ("OmniCOM 升级包 (*.zip)", "OmniCOM package (*.zip)"),
    "update.local.reading": ("正在读取升级包…", "Reading the package…"),
    "update.local.same": ("这个包就是当前版本 {version}，仍会重新安装一次。",
                          "This package is {version} - the same version you run; it will be reinstalled."),
    "update.local.bad": ("升级包不可用：{err}", "Unusable package: {err}"),
    "menu.exit": ("退出", "Exit"),
    "menu.docs": ("文档", "Documentation"),
    "menu.about": ("关于", "About"),
    "pref.title": ("首选项", "Preferences"),
    "pref.language": ("界面语言", "Interface language"),
    "pref.language.hint": ("切换后立即生效；中文环境只显示中文，英文环境只显示英文。",
                           "Applies immediately. Chinese only shows Chinese, English only English."),
    "pref.logdir": ("日志默认保存目录", "Default capture folder"),
    "pref.logdir.hint": ("「收发记录」和接收文件的默认目录；改动后下一次开始记录生效。",
                         "Folder used by capture-to-file and received files. Takes effect the "
                         "next time you start capturing."),
    "pref.logdir.pick": ("选择目录…", "Choose folder…"),
    "pref.logdir.default": ("恢复默认", "Use default"),
    "pref.logdir.bad": ("目录不可用：{err}", "Folder unusable: {err}"),
    "pref.accent": ("主题色（纯色）", "Accent colour (solid)"),
    "pref.accent.hint": ("只改变强调色与选中高亮，页面依然是纯色背景。",
                         "Only the accent and selection tints change; the page stays a solid "
                         "colour."),
    "pref.wallpaper": ("背景壁纸", "Background wallpaper"),
    "pref.wallpaper.pick": ("选择图片…", "Choose image…"),
    "pref.wallpaper.clear": ("清除壁纸", "Clear wallpaper"),
    "pref.wallpaper.none": ("未设置（纯色背景）", "Not set (solid colour)"),
    "pref.buffer": ("日志缓冲大小", "Log buffer size"),
    "pref.font.ui": ("界面字体（日志以外）", "Interface font (everything but the log)"),
    "pref.font.ui.hint": (
        "改的是窗口里的控件、菜单和对话框文字；日志窗口用下面单独的一组",
        "Affects controls, menus and dialogs; the log has its own pair below"),
    "pref.font.log": ("日志字体（含终端）", "Log font (terminal included)"),
    "pref.font.log.hint": (
        "也可以在日志窗口里按住 Ctrl 滚动滚轮临时缩放，缩放结果会被记住",
        "You can also Ctrl+wheel inside the log; the zoom level is remembered"),
    "pref.history": ("发送历史", "Send history"),
    "pref.history.tip": (
        "发送框里按 ↑ 能回退多少条命令；超出后自动丢弃最早的",
        "How many commands ↑ walks back through; the oldest are dropped"),
    "pref.history.hint": (
        "上限 300 条。历史记录会随设置保存，清除后无法恢复",
        "Capped at 300. The list is saved with the settings; clearing cannot be "
        "undone"),
    "pref.history.clear": ("清除历史记录", "Clear history"),
    "pref.history.count": ("当前 {count} 条", "{count} stored"),
    "pref.history.unit": (" 条", ""),
    "misc.history": ("已把发送历史上限改为 {count} 条",
                     "Send history now keeps {count} commands"),
    "misc.history.cleared": ("发送历史已清除", "Send history cleared"),
    "pref.font.default": ("默认（跟随系统）", "Default (follow the system)"),
    "misc.font": ("界面字体已改为 {size} px", "Interface font is now {size} px"),
    "misc.font.log": ("日志字体已改为 {size} px", "Log font is now {size} px"),
    "pref.buffer.hint": (
        "日志最多保留这么多文字，超出后自动丢弃最早的行（保留最新的），不会卡顿；最大 500 MB。",
        "How much log text is kept. Older lines are dropped once it is full - the newest "
        "are always kept - and the limit is 500 MB."),
    "pref.buffer.tip": (
        "约等于保留的日志正文大小；行越短保留的行数越多",
        "Roughly the amount of log text kept; shorter lines keep more lines"),
    "misc.buffer": ("日志缓冲已设为 {mb} MB", "Log buffer is now {mb} MB"),
    "pref.wallpaper.hint": ("壁纸会铺满页面并加一层浅色蒙版，保证文字仍然清晰。",
                            "The image covers the page behind a light veil so text stays "
                            "readable."),
    "misc.logdir": ("记录目录已改为 {path}", "Capture folder is now {path}"),
    "misc.logdir.next": ("记录目录已改为 {path}；当前正在记录的文件仍在原目录，"
                         "下一次开始记录起生效",
                         "Capture folder is now {path}; the running capture keeps its "
                         "file, the change applies from the next capture"),
    "misc.accent": ("主题色已切换为「{name}」", "Accent changed to “{name}”"),
    "misc.wallpaper": ("壁纸已更新", "Wallpaper updated"),
    "misc.wallpaper.cleared": ("已恢复纯色背景", "Back to the solid colour"),
    "pref.behaviour": ("行为", "Behaviour"),
    "pref.close": ("关闭", "Close"),
    "about.title": ("关于 OmniCOM", "About OmniCOM"),
    "about.blurb": ("一体化的嵌入式串口调试器：日志 / 终端 / X·Y·ZMODEM / 命令 / 插件。",
                    "An all-in-one embedded serial debugger: log, terminal, X/Y/ZMODEM, "
                    "commands and plugins."),
    "about.version": ("版本", "Version"),
    "about.python": ("运行环境", "Runtime"),
    "about.datadir": ("数据目录", "Data folder"),
    "about.copy": ("复制信息", "Copy info"),
    "docs.title": ("OmniCOM 使用文档", "OmniCOM documentation"),
    "docs.close": ("关闭", "Close"),
    # ------------------------------------------------------------------ misc
    "misc.yes": ("确定", "OK"),
    "misc.cancel": ("取消", "Cancel"),
    "misc.close": ("关闭", "Close"),
    "misc.delete": ("删除", "Delete"),
    "misc.rename": ("重命名", "Rename"),
    "misc.noport": ("没有可用串口", "No serial port available"),
    "misc.needport": ("请先打开串口", "Open a port first"),
    "misc.switching": ("正在切换到 {port} …", "Switching to {port} …"),
    "misc.recording": ("开始记录到 {name}", "Capturing to {name}"),
    "misc.recorded": ("已停止记录（{size}）", "Capture stopped ({size})"),
    "misc.sentfile": ("开始发送 {name}（{size}）", "Sending {name} ({size})"),
    "menu.more": ("更多", "More"),
    "menu.title": ("菜单", "Menu"),
    # ---- send bar
    "send.placeholder": (
        "输入要发送的内容，Ctrl+Enter 发送（HEX 模式用空格分隔字节，如 01 03 00 00 00 01）",
        "Type what to send, Ctrl+Enter sends (HEX mode: space-separated bytes, e.g. 01 03 00 00 00 01)"),
    "send.send": ("发送", "Send"),
    "send.eol.none": ("无行尾", "No line end"),
    "send.eol.tip": ("发送时自动追加的行尾", "Line ending appended on send"),
    "send.hex.tip": ("以 HEX 解析输入内容后发送", "Parse the input as HEX before sending"),
    "send.timed": ("定时发送", "Repeat"),
    "send.start": ("开始", "Start"),
    "send.stop": ("停止", "Stop"),
    "send.enter": ("回车发送", "Enter sends"),
    "send.enter.tip": (
        "开启后按回车直接发送（Shift+回车换行）；关闭后回车换行，用「发送」或 Ctrl+回车发送",
        "On: Enter transmits (Shift+Enter inserts a line break). Off: Enter inserts a "
        "line break, use Send or Ctrl+Enter"),
    "send.file": ("发送文件", "Send file"),
    "send.openfile": ("打开文件", "Open file"),
    "send.pickfile": ("选择要发送的文件", "Choose a file to send"),
    "send.nofile": ("请先点「打开文件」选择一个文件",
                    "Choose a file with “Open file” first"),
    "send.progress": ("{name} · {percent}%  ({sent}/{total})",
                      "{name} · {percent}%  ({sent}/{total})"),
    "send.filedone": ("文件发送完成：{name}（{size}）",
                      "File sent: {name} ({size})"),
    # ---- terminal page
    "term.title": ("终端模式 · 键盘直达串口", "Terminal · keystrokes go straight to the port"),
    "term.menu.copy": ("复制", "Copy"),
    "term.menu.paste": ("粘贴到串口", "Paste to port"),
    "term.menu.selectall": ("全选", "Select all"),
    "term.menu.clear": ("清屏", "Clear screen"),
    "term.enter": ("回车发送", "Enter sends"),
    "term.clear": ("清屏", "Clear screen"),
    "term.reset": ("清屏并重置终端状态", "Clear screen and reset terminal state"),
    "term.hint": (
        "点击终端区域后直接用键盘输入：Enter 发送回车，Ctrl+C 发送 0x03，方向键发送 ANSI 序列。"
        "复制用 Ctrl+Shift+C。",
        "Click the terminal and type: Enter sends CR, Ctrl+C sends 0x03, arrow keys send ANSI "
        "sequences. Copy with Ctrl+Shift+C."),
    # ---- transfer page
    "xfer.protocol": ("协议", "Protocol"),
    "xfer.direction": ("方向", "Direction"),
    "xfer.send": ("发送到设备", "Send to device"),
    "xfer.recv": ("从设备接收", "Receive from device"),
    "xfer.start": ("开始传输", "Start transfer"),
    "xfer.abort": ("中止", "Abort"),
    "xfer.ready": ("就绪", "Ready"),
    "xfer.waiting": ("等待设备发起传输…", "Waiting for the device to start…"),
    "xfer.done": ("完成", "Done"),
    "xfer.failed": ("失败", "Failed"),
    "xfer.log": ("传输日志", "Transfer log"),
    "xfer.clearlog": ("清空", "Clear"),
    "xfer.pickfile": ("请先选择要发送的文件", "Choose a file to send first"),
    "xfer.savedir": ("接收到的文件保存到 {path}", "Received files are saved to {path}"),
    "xfer.saved": ("已保存：{path}", "Saved: {path}"),
    "xfer.dropzone": ("点击选择文件，或把文件拖到这里",
                      "Click to choose a file, or drop it here"),
}


def language() -> str:
    """Current language code (``zh`` or ``en``)."""
    return _state["lang"]


_state: dict[str, str] = {"lang": "zh"}

#: ``(setter, key, kwargs)``; a language switch replays all of them.
_BINDINGS: list[tuple[Callable[[str], None], str, dict]] = []


def tr(key: str, **kwargs) -> str:
    """Translate ``key``; formatting placeholders are ``{name}`` style."""
    entry = _TABLE.get(key)
    if entry is None:
        text = key
    elif _state["lang"] == "zh":
        text = entry[0]
    else:
        text = entry[1]
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def bind(setter: Callable[[str], None], key: str, **kwargs) -> Callable[[str], None]:
    """Register ``setter`` so it is re-run on every language switch."""
    _BINDINGS.append((setter, key, dict(kwargs)))
    return setter


def unbind_owner(prefix_widget) -> None:
    """Drop bindings that belong to a destroyed widget."""
    kept = []
    for setter, key, kwargs in _BINDINGS:
        owner = getattr(setter, "__self__", None)
        if owner is not None and owner is prefix_widget:
            continue
        kept.append((setter, key, kwargs))
    _BINDINGS.clear()
    _BINDINGS.extend(kept)


def set_language(lang: str) -> None:
    """Switch language and refresh every bound string."""
    if lang not in LANGS:
        lang = "zh"
    _state["lang"] = lang
    alive: list[tuple[Callable[[str], None], str, dict]] = []
    for setter, key, kwargs in _BINDINGS:
        try:
            setter(tr(key, **kwargs))
            alive.append((setter, key, kwargs))
        except RuntimeError:
            # The C++ widget behind the bound method is gone; drop it.
            continue
    _BINDINGS.clear()
    _BINDINGS.extend(alive)


def apply_language_from(value: str) -> str:
    lang = value if value in LANGS else "zh"
    set_language(lang)
    return lang
