# Handled-items ledger
Updated: 2026-11-25

One line per handled item, append-only by ID. Never delete a `RULED-OUT` or
`REVERTED` row - those are exactly what stop the same dead end being retried.
Status: OPEN | FIXED-verified | FIXED-unverified | REVERTED | RULED-OUT

**How to use this file:** search by keyword (`grep`, Ctrl+F) before investigating
a symptom. Do not read it end to end.

- Full write-ups for L-270..L-343: `archive/ledger-detail-2026.md`.
- Pre-split file, verbatim: `archive/LEDGER-full-2026-11-25.md`.
- Older settled rows: `archive/ledger-index-2026-early.md`.
- Budget: keep this file under ~150 lines. Archive the oldest settled third.

| ID | Symptom keywords | Area | Status | Conclusion (one line) | Evidence |
| --- | --- | --- | --- | --- | --- |
| L-344 | 导入扩展命令 重复 / duplicate import / 命令越导越多 | ui | FIXED-verified | `import_from` 无条件追加，同一文件导两次就翻倍。改为按「正文 + HEX 模式」判定重复并跳过，返回 (added, skipped) 供界面提示；分组/备注/延时/行尾/勾选不参与判定 | R-344；`.artifacts/probe_import_dedupe.py` 15/15；负向去掉判定后失败 |
| L-345 | GUI 自测假通过 / ALL PASSED 但大量步骤没跑 / 只跑 43 步 | build | OPEN | 套件设 `quitOnLastWindowClosed` 默认 True，`step_keep_awake` 关掉最后一个窗口就让 `app.exec()` 返回，150 步只跑 43 步却仍报 ALL PASSED（判定只统计已执行项）。已在创建 QApplication 后设 `setQuitOnLastWindowClosed(False)`；修复后暴露 37 项被掩盖的既有失败（18 项 C++ 对象已删除、16 项数值断言、2 项 NoneType、1 项插件发现），A/B 证明与 R-344 无关（有 R-344 37 项，撤回后 39 项） | R-344 提交；`.artifacts/ab_failures.py` |
| L-002 | 拔插不重连 / replug reconnect / 串口消失 | cli | FIXED-unverified | `PortManager._monitor_loop` reopens the same path (or matches serial/VID-PID); needs a real unplug test | `omnicom/serial_port.py` monitor loop |
| L-005 | zmodem 传不过 / 卡在 ZRINIT / 帧校验失败 | core | FIXED-unverified | CRC16 was implemented reflected (wrong) and the FCS "seal" appended two zero bytes; fixed to plain big-endian `crc16(data)`. Sender now ignores stale ZRINIT instead of r… | `protocol_selftest` 23/23; no lrzsz interop yet |
| L-006 | exe 无法运行 / 拒绝访问 / Access denied / 打包失败 | build | RULED-OUT | PyInstaller build itself succeeded (2.0 MB, no errors) but Windows refuses to launch the produced exe ("程序无法运行: 拒绝访问", `cmd` returns rc=0 with no window). Corporate endp… | R-019, session 2026-10-27 |
| L-012 | QFontDatabase cannot find font directory | core | RULED-OUT | Harmless offscreen-only warning; Qt ships no fonts. Real windows use system fonts | `tools/*selftest*.py` logs |
| L-068 | 透明日志视口疑似导致滚动重绘开销 | log | REVERTED | Opaque-white experiment did not improve scrolling (~8 vs ~7 ms) and worsened RX versus transparent. Restored transparent viewport; keep measured batching improvements on… | same-load `.artifacts/ui-perf-after.json`; do not retry wit… |
| L-073 | 心跳半秒停顿是上一项压力测试的延迟清理 | core | RULED-OUT | Gap occurred at toggles 2 and 10, not initial setup; sampled main-thread stack showed tasklist, not deferred widget deletion. Do not mask it with settling delays or a re… | R-055 watchdog, L-070; full acceptance 208 checks/57 requir… |
| L-083 | 运行 start.cmd 后 CLI 会自动打开吗 | cli | RULED-OUT | `start.cmd` runs `pythonw run.py` (GUI only, no console); the CLI is a separate `python -m omnicom.cli` command. Nothing spawns it automatically | `run.py` imports `omnicom.ui.main` only |
| L-093 | R-054 在完整套件里偶发超过 40ms | core | RULED-OUT | Focused runs were stable (rx_batch p95 24-26 ms over 3 runs) while the same metric read 45 ms inside the long suite. The step now pauses the stats/capture timers while i… | R-054 passes in the full suite at the original 40 ms budget |
| L-102 | 用全局事件过滤器实现提示，把 200 条命令折叠拖慢 4 倍 | core | RULED-OUT | `QApplication` 级 Python 过滤器会收到每个事件：一次折叠 200 行共 55585 个事件，group_action 从 7.4 ms 涨到 53.7 ms、p95 76 ms（R-054 预算 40 ms）。先按事件类型做 frozenset 剪枝后仍有约 2 倍开销，最终改成 100 ms 轮询（不装过滤器）… | 同一台机器 A/B：装过滤器 53.7/76.4 ms，去掉后 9.9/15.6 ms |
| L-120 | R-054 在长套件里偶发越过 40ms（换行改动后被怀疑） | chart | RULED-OUT | 先把换行开关做 A/B：`WidgetWidth` 与 `NoWrap` 各跑三次，group_frame/rx_batch 的 p95 分布完全重叠（32~36 / 20~26 ms），与换行无关。真正原因是 `processEvents()` 会把前面几十个步骤留下的脏控件一起重绘，同一段折叠代码单跑 18 ms、长套件里 42 m… | A/B 见 `.artifacts/ui-perf*.json`；完整套件复跑通过 |
| L-140 | 日志行距做不出来（最后放弃这个需求） | chart | RULED-OUT | `QPlainTextEdit` 完全不支持 行高：`QTextBlockFormat.setLineHeight()` 的 5 种`LineHeightTypes` 全部被忽略（`cursorRect` 行距恒为 17 px，块里存的是设置值）；自定义 `QPlainTextDocumentLayout.blockBoundingRe… | `probe_pitch`/`probe_layout`/`probe_pitch3`/`probe_perf2` 四… |
| L-191 | 间歇失败 / R-054 / R-055 / R-001 波动 | core | RULED-OUT | 三项失败在独立的 `--only gui` 运行中全部通过（649/649），重跑全量也全绿；R-054 的 rx_batch 自带 `retried: True`、R-055 报告的是 430 ms 单次卡顿轮次，均随整机负载出现。判据：同一份代码在降载后稳定全绿 ⇒ 是测量环境抖动，不是产品缺陷。禁止用"反复重跑到绿"当结论，须同时… | `.artifacts/accept.log`（失败）+ `--only gui` 649/649 + 重跑全量 92… |
| L-197 | PowerShell 乱码 / NativeCommandError / 明明通过却… | terminal | FIXED-unverified | 工具链两侧的坑，与产品无关：`Get-Content -Encoding UTF8` 仍会按 GBK 解子进程的 UTF-8 输出（日志看起来是乱码），且 stderr 有内容时 PowerShell 会追加 `NativeCommandError` 并把 `$LASTEXITCODE` 显示得像失败。判据用 `N/N 项通过` 和 `… | 本轮多次复现 |
| L-199 | 离屏测试窗口尺寸不对 / 布局断言失败 / 800x800 | ui | RULED-OUT | offscreen 平台会报 800×800，而窗口最小宽度是 980；凡是断言绝对坐标/尺寸的离屏探针都会得到"不可能"的布局。离屏只验行为与相对关系，绝对几何交给 `ui_snapshot` 渲染 | 本轮探针复现 |
| L-207 | state 里 lines 含义冲突 / 日志行数被当控制线 | log | RULED-OUT | `_control_snapshot` 的 `lines` 已经表示日志行数了，绝不能拿来复用成控制线电平；控制线必须另开 `control_lines` 键，否则两个含义互相覆盖、读的人一定搞错 | `omnicom/ui/main_window.py:_control_state` |
| L-218 | R-153 滑动动画被用户否决（做了又退回） | ui | REVERTED | 上一轮按 R-153 做了 `SlideOverlay` 快照 + 交叉淡入，用户实测后要求「直接恢复成以前的」。任何运动都会重新布局三栏，观感像窗口被拉伸。已整体删除 `SlideOverlay`、`maximize_with_slide`/`restore_with_slide`/`_stop_slide`/`_run_slide`… | `ui_selftest` R-153 14/14；`ruff` 全通过；R-140 Aero Snap 仍绿 |
| L-237 | 看板默认状态与按钮文案（运行/停止 → 继续/暂停） | chart | FIXED-verified | 语义变了而不只是换词：看板一打开就在刷新，所以按钮默认那一面必须是「动作面」= 「暂停」，且沿用原来的运行中主题色；灰色「运行」挂在实时看板上读起来像「这台是关着的」。暂停只冻结画面，采集、帧计数、控件数值继续，所以恢复那一面是「继续」而不是「运行」。改文案必须同一次把测试改掉：`plugin_selftest` 原来的 R-167 断… | `plugin_selftest` R-176 5 项 + R-167 1 项；`acceptance_test` R… |
| L-238 | `plugin_selftest` R-173「网格常显」断言失败（size=`16p… | core | FIXED-verified | 网格是两个 `linear-gradient`（横+竖）叠出来的，`getComputedStyle().backgroundSize` 按层返回，于是字符串是 `"16px 16px, 16px 16px"`；写成 `== "16px 16px"` 实际断的是层数而不是步长，代码没动它却红了。改成按逗号拆开后逐项比对 16px。教训：… | `python tools/plugin_selftest.py` -> exit 0 |
| L-239 | 搜索结果「按时间过滤」开关闪烁 | chart | FIXED-verified | 闪烁 = 同一块东西在相邻两轮事件循环里以两个不同高度各画了一次，不是布局死循环（先查过：连续 12 轮 `processEvents` 采样 `time_fields` / `TimeFilter` / `results.viewport()` 几何，`GEOMETRY changed: []`，没有振荡；只看「有没有来回跳」会漏掉这… | `tools/ui_selftest.py` R-177（`resize` 恰 1 次 / `paint` 高度唯一… |
| L-240 | 「看板默认还是非运行状态」 | chart | FIXED-verified | 不是按钮画错，是存了状态：`omnicom-data/plugin-data/chart/settings.ini` 里有 `running=0`（`window=0`、`points=5000`、`layout={"widgets": []}`），旧 `_flag("running")` 把它恢复成暂停，于是「默认」变成了「上次留下的… | `tools/plugin_selftest.py` R-176 新增 2 项（加载前预置 `running=0`，加… |
| L-241 | 插件商店「界面更丰富 + 版本下拉框装指定版本」 | terminal | FIXED-verified | 两个真 bug 都是"探针替用户手搓界面"时暴露的：① 顶部计数用了 `len(self.rows)`（过滤后），一搜不到就写"共 0 / 0 个插件"；改成先 `entries = self.store.rows(self.catalog)` 再 `len(entries)`。② `entry["picked"]` 初始被预置成 `i… | `tools/plugin_selftest.py` R-178 共 15 项（后端 8 + 界面 7）全 PASS；… |
| L-242 | 搜索结果已有数据时开关「按时间过滤」卡顿 | ui | FIXED-verified | 用户的定位完全正确：「有数据时才卡」这句就是根因的形状。`_on_time_toggled()` 结尾调 `run_search()`，而它第一句是 `clear_results()`，然后把每一条命中重新建一遍；可是开关时范围是从缓冲区自身种下的（`_seed_time_from_log()`），命中集合一个字都没变 —— 2000… | `tools/ui_selftest.py` R-179 16 项（开/关 800 条命中 `addItem==0`… |
| L-243 | 插件商店插件名不突出 / 名字和介绍连成一段 | chart | FIXED-verified | 用户说的不是字号小，而是「分不开」：行的标题和简介都是普通 `QLabel`，同权重、同字号、同颜色，加上 `layout.setSpacing(6)` 的 6 px，整张卡片读起来就是一整段文字。改法分两层——① `theme.py` 新增 `QLabel#PluginName`（`theme.INK` + `font-weight:… | `tools/plugin_selftest.py` R-180 共 6 项（三个 `objectName` 齐备；名… |
| L-244 | 插件商店「检查更新」会弹出弹窗 | chart | FIXED-verified | 用户报的不是对话框，而是一个一闪而过的空白小窗口；先证明刷新链路根本不弹框——全仓 grep 商店相关代码只有 `confirm_remove()` 一个 `QMessageBox`，`check()` 的结果只写进 `self.status` 文本；再把打包好的 `dist/OmniCOM/OmniCOM.pyz` 解出来复查，同样只… | `tools/plugin_selftest.py` R-181 新增 2 项（商店内无 `Badge` 是顶层窗口；… |
| L-245 | 文档里贴的插件截图与「五行控件都有数」都没有测试兜底 | chart | FIXED-verified | 用户要求「给支持的插件也单独开介绍，并附实机图，数据看板要那些控件都有数据」，而这批插图以前只靠肉眼：`plugin-chart.png` / `plugin-launcher.png` 由 `tools/make_plugin_screenshots.py` 生成，README 与 `docs/PLUGINS.md` 都直接引用，但没… | `tools/plugin_selftest.py` 新增 R-182（8 项，读回 DOM：`3.25` / `42… |
| L-246 | `make_dist --check` 在最后一步失败（`StopIteration`… | chart | FIXED-verified | `make_plugins.verify_runtime()` 还留着 `for mode in ("bundled", "native")` 两个模式，而 R-174 改成「发布包不含 `plugins/`」之后，`bundled` 那一遍仍在包内找 `plugins/chart`，于是 `next(info.widget for i… | `python tools/make_dist.py --check` exit 0：`packaged selfte… |
| L-247 | 发布 182 MB 便携包时 `make_release.py --publish`… | build | FIXED-verified | `api()` 里写死 `urlopen(..., timeout=120)`，而这个 timeout 是 socket 级的，管的是每一次 `sendall()` 能否在 120 s 内写完；182 MB 在慢速上行链路上单次写入必然超时，请求体被截断，GitHub 侧留下一个没有资产的新 release。修法：`api()` 增加… | `make_release.py --version 1.1.5 --publish` exit 0，日志出现 `发布… |
| L-248 | 插件商店的默认目录 `plugins/catalog.json` 把每个包指向 `re… | build | FIXED-verified | 缺的不是代码而是资产桶：目录里写死的下载地址必须先有一个叫 `plugin-store` 的公开 release 承载 zip。用 `tools/publish_plugin_store.py` 建桶并上传 `chart-1.1.2.zip` / `chart-1.1.0.zip`（降级目标，不能删）/ `launcher-1.0.0.… | `publish_plugin_store.py` exit 0：四个资产 `state=uploaded`，`lat… |
| L-249 | 搜索栏「折叠 → 全屏 → 退出全屏」之后整个侧栏回不到正常状态（issue #8） | chart | FIXED-verified | 真根因不在全屏，而在 `OmniWindow.relayout_sidebar()`：它读 `self.search_panel.header.height()`，而 `SearchPanel` 根本没有 `header` 属性（只有 `CommandsPanel` 有），于是折叠搜索栏的第一步就抛 `AttributeError: '… | - |
| L-250 | 终端模式按 Tab 没反应（issue #11） | terminal | FIXED-verified | Tab 根本没进 `keyPressEvent`：Qt 把 Tab 当焦点手势，`QWidget::focusNextPrevChild()` 在 `keyPressEvent` 之前就被调用（探针 `.artifacts/probe_tab.py` 实测：只覆盖 keyPressEvent 时，Tab 只走到 `focusNextPr… | - |
| L-251 | 窗口开着、串口关着时用 CLI 打开该串口，窗口不同步显示已连接（issue #1） | plugins | FIXED-verified | 正常路径其实是同步的（真 CLI 子进程探针 `.artifacts/probe_cli_watch.py` 能看到按钮从「打开串口」变「关闭串口」）。真根因在路由回退：`pick_endpoint()` 只要第一个 `state` 探测没答复就返回 `(None, "窗口没有响应")`，而 `new_session()` 把「None… | - |
| L-252 | 命令列表增删改后跳回顶部（issue #2）；改用锚定行恢复时 `setValue`… | core | FIXED-verified | 两个独立坑叠在一起。① `rebuild()` 把每一行 `takeAt`+`deleteLater` 重建，`QScrollArea` 的 `value()` 自然归零——必须自己恢复。② 恢复不能只存 `value()`，也不能存行的绝对内容坐标：增删后面板高度和行高都可能变，实测存绝对坐标时 879 只回到 833。正确锚是「顶层… | - |
| L-253 | 侧栏改成垂直 QSplitter 后折叠只剩一片空白板；退出搜索全屏时卡片脱离分栏 | chart | FIXED-verified | 两处都是「换了父级/几何归属」带来的连带问题。① 折叠高度原来取 `header.height()`：body 隐藏后 header 是唯一可见子控件，会被拉满整张卡片（实测 38 px 的条读成 215 px），所以必须用 `sizeHint().height()`，并给折叠卡片补 `setMaximumHeight`——否则 spl… | - |
| L-254 | 关键字搜索栏的高度分隔条拖动一次要等 100+ ms，非常卡顿 | core | FIXED-verified | 不是重绘的问题，是同步写盘。A/B 实测（`.artifacts/probe_drag5.py`）：`splitterMoved` 连着 handler 时 118-132 ms/move，把 handler 断开后 7.9-8.1 ms/move，裸 `QSplitter` 只要 0.08 ms。而 `QSettings.setVal… | - |
| L-255 | 把「真实组头吸顶」做进 `QScrollArea` 后窗口卡死，日志刷满 `propa… | chart | FIXED-verified | `QScrollArea` 只管理一个子控件（它的 widget）；把吸顶栏也 `setParent(self.scroll)` 之后两个子控件互相 negotiation size hint，进入和 L-041 一模一样的无限循环，事件循环再也不返回。另一个同源坑：pin/unpin 会改变内容高度 → 触发 `valueChange… | - |
| L-256 | R-193 的光标像素断言在完整套件里偶发失败（6 像素） | terminal | FIXED-verified | 两个独立原因。① 终端页在堆栈里不可见时 `QPlainTextEdit.cursorRect()` 返回被裁到 y=0 的矩形，像素探针于是量到的是文字笔画而不是光标（隐藏页实测 11 个 ink 像素、可见页 30 个）。② 实心块与空心框在那一小块矩形里的总像素数恰好都是 30（2 px 宽描边 ≈ 填满），用「像素更多」区分不了… | - |
| L-257 | 把真实组头搬进吸顶槽后，吸顶栏根本不出现（也不报错） | ui | FIXED-verified | 槽类里写了 `self.layout = QHBoxLayout(self)` —— 但 `layout` 是 `QWidget.layout()` 的方法名，PySide6 上给它赋值不会覆盖，读回来仍是绑定的内置方法，于是 `self.layout.addWidget(...)` 抛 `AttributeError: 'builti… | - |
| L-258 | 吸顶组头背景是一块突兀的纯白 | chart | FIXED-verified | 侧栏卡片是 `GlassFrame`：无壁纸时填 `QColor(255, 255, 255, 244)`（半透明白），有壁纸时先画磨砂玻璃再叠一层白。吸顶栏却用了 `background: {theme.CARD}`（不透明 `#FFFFFF`），所以看着像贴上去的白条。修法：吸顶栏改为 `WA_TranslucentBackgrou… | - |
| L-259 | 就绪栏「还是太高」，但把栏压矮后文字看起来挤了 | ui | FIXED-verified | 用户量的和参数量的不是同一个东西。实测内容下方实际叠了 48 px：10 px 间隙 + 22 px 就绪栏 + 16 px 根边距（其中 8 px 是阴影环，不可压）。只把 `STATUS_H` 从 22 改到 18，视觉上几乎没变，因为另外 26 px 还在。正确做法是压外侧间隙：间隙 10 -> 6、底部基础边距 8 -> 4，栏… | - |
| L-260 | 用负 contentsMargins 压间距 → 文字变形、位置偏低 | ui | REVERTED | 想把「内容 -> 就绪栏」的间隙单独压小，先后试了两种做法：① `QSpacerItem.changeSize(0, -4)` —— Qt 会把负高度夹成 0（实测 `sizeHint()` 仍是 0），压根不生效；② 给就绪栏的布局设 `setContentsMargins(14, -2, 14, 0)` —— 栏内文字被整体上提、上… | - |
| L-261 | 通知堆叠改为「新的在最下」后，卡片间距不均匀、最后两条完全重叠 | chart | FIXED-verified | 三个坑连在一起。① 上限判断用了递减的运行总量：弹掉一条后 `height` 与新的上限对不上，结果 12 条都留着（实测跨度 549 px = 61 %，上限本该 405 px）。改成每轮从活列表重算总和。② 位置推进用 `item.pos()`：`move_to()` 是 190 ms 的 `QPropertyAnimation`… | - |
| L-262 | 通知堆叠上限算对了，但视觉高度仍然超过一半 | chart | FIXED-verified | `TOAST_STACK_FRACTION` 判的是 `sum(高度 + 间隙)`，而用户看到的是首尾像素跨度，两者差一个 `TOAST_GAP`（多算了一条间隙）。把上限判据换成「所有卡片高度 + (张数) × 间隙」与「窗口 × 0.45」比较后，实测稳定在 6 张、跨度 371-396 px（41-44 %），落在「比一半少一点」… | - |
| L-263 | HEX 视图里未填满的尾行没有时间戳 | log | FIXED-verified | 尾行走的是 `set_pending()`，而它只插入裸文本；时间戳是 `append_line()` 加的前缀，所以「最后一包没时间戳、等下一包补齐后才出现」——因为补满那一刻它变成了普通行。修法：给 `set_pending(text, stamped=True)`一个开关（整段用正文格式而不是灰色 pending 格式），并在 `… | - |
| L-264 | 发送回显会把未填满的收包行整行删掉（静默丢字节） | log | FIXED-verified | 真根因在 `LogView.append_line()`：它开头就 `_clear_pending_internal()`，于是任何在「HEX 行还没凑够 16 字节」时写入的行（TX 回显、`··` 系统提示、下一包）都会把那半行从视图里抹掉。用户说的「每次发都有 00000180 …，但是收又不显示这个」就是它。修法：在 `_log… | - |
| L-265 | 无边框窗口根本无法用鼠标拖动边缘改变大小 | terminal | FIXED-verified | `Qt.FramelessWindowHint` + `WA_TranslucentBackground` 的窗口没有系统边框，Windows 也就不会提供缩放边。正确做法是应答 `WM_NCHITTEST`（在 `nativeEvent` 里）并返回 `HTLEFT/HTRIGHT/HTTOP/HTBOTTOM/HTxxxCORNER… | - |
| L-266 | 加上边缘缩放后，窗口能被拖得比布局要求还小 | ui | FIXED-verified | `setMinimumSize()` 只是 Qt 侧的夹紧，原生尺寸手势走的是 `WM_GETMINMAXINFO`；没人应答时系统就不知道下限（实测直接 `SetWindowPos(400,300)` 真的得到 400x300 窗口）。另有一个隐蔽 bug：`ctypes.wintypes` 没有 `MINMAXINFO`，第一版实现… | - |
| L-267 | 只应答 `WM_NCHITTEST` 完全没用：真实鼠标移动时处理器一次都不执行 | terminal | FIXED-verified | 根因是窗口样式：`Qt.FramelessWindowHint` 的窗口是 `WS_POPUP`，没有 `WS_THICKFRAME`，而 Windows 只对带该样式的窗口做缩放命中测试。诊断方法很关键：先用 `SendMessage(hwnd, WM_NCHITTEST, …)` 测（能得到正确答案，假阳性），再用 `SendInp… | WS_THICKFRAME)` + `SetWindowPos(SWP_FRAMECHANGED)`，然后用 `WM_… |
| L-268 | 命中已正确，但鼠标移到边缘没有缩放箭头 | terminal | FIXED-verified | `WM_SETCURSOR` 才是决定光标形状的消息，而 Qt 自己应答它并返回 TRUE，所以 Windows 没机会按 `HT*` 画箭头（实测 `SendMessage(WM_SETCURSOR)` 返回 1）。两个连环坑：① 想让默认处理器画的时候，形状全错（左边给 `SIZENWSE`），因为默认处理器用的是系统缓存的区域，不… | - |
| L-269 | R-204 第一次的测试是假阳性，害得问题被判定为已修复 | terminal | FIXED-verified | 最初的断言只直接调用 `window._resize_hit_test()`，那当然返回正确答案 —— 但用户实测仍然没有箭头、拖不动。真正该断言的是原生链路：窗口样式含 `WS_THICKFRAME`、客户区等于窗口（边框被剥）、`SendInput` 后处理器被调用、光标句柄确实是对应的 `IDC_*`。现在 R-205/R-206… | - |
| L-270 | 行式控制台丢了光标移动，所以"左右键会删字符" | terminal | FIXED-verified | 根因：旧 `TerminalView` 是行式控制台——`\r` 回列 0，`\n` 开新行， | archive/ledger-detail-2026.md (L-270) |
| L-271 | 终端接收一批 1100 字节曾要 6.8 ms，两处分配是主因 | terminal | FIXED-verified | `_scroll_up()` 每换行都 `[Cell() for _ in range(cols)]`：一次 2 400 行突发 | archive/ledger-detail-2026.md (L-271) |
| L-272 | `styled.widget.style()` 可能为 None，行直接不画 | core | FIXED-verified | `SearchDelegate.paint()` 里 `styled.widget.style().drawControl(...)` 没有 | archive/ledger-detail-2026.md (L-272) |
| L-273 | 性能门禁（R-054/R-055）会被整机负载带偏，必须 A/B 对照 | core | FIXED-verified | 本机同时跑 `ui_selftest`（8 万次检查量级）时，`log_scroll` / `rx_batch` 的 | archive/ledger-detail-2026.md (L-273) |
| L-274 | 终端 "只能复制整行" 是两个 bug 叠加，而复制本身是对的 | terminal | FIXED-verified | 根因一（显示）：`_paint_row()` 用 `_row_in_selection()` 判断"这一行在不在 | archive/ledger-detail-2026.md (L-274) |
| L-275 | 终端选区还有四个易漏的一致性细节 | terminal | FIXED-verified | 宽字符：CJK 占两格，选区端点落在续格上会切掉半个字。`_snap_cols()` 把端点 | archive/ledger-detail-2026.md (L-275) |
| L-276 | `mouseReleaseEvent` 会破坏双击/三击的选区（Qt 事件顺序） | terminal | FIXED-verified | 一次双击在 Qt 里是：`Press` → `Release` → `DblClick` → `Release`。 | archive/ledger-detail-2026.md (L-276) |
| L-277 | R-055 在本机稳定失败，用 `git stash` 单次对比会得出错误结论 | core | FIXED-verified | 现象：`R-055 慢驱动开关期间界面心跳无超过 200 ms 的停顿` 反复失败， | archive/ledger-detail-2026.md (L-277) |
| L-278 | `_flush_rx` 里有个 `if console:` —— 终端页不可见时它根本… | terminal | FIXED-verified | 代码是：`console = self.console.isVisible()` … `if console: self.console.feed(data)`。 | archive/ledger-detail-2026.md (L-278) |
| L-279 | CLI 的窗口命令有两条纪律：白名单要显式扩展，`_control_send` 只吃… | terminal | FIXED-verified | `_emit_window()` 只复制固定白名单的字段，新增命令的返回值会被静默丢弃 | archive/ledger-detail-2026.md (L-279) |
| L-280 | 「按删除键乱行」是两个独立缺陷叠在一起 | terminal | FIXED-verified | 缺陷一：字节被解析了两次。 `_on_rx` 喂 `page.mirror`，`_flush_rx` 又 | archive/ledger-detail-2026.md (L-280) |
| L-281 | `0x7f` 不是"控制字符"，在串口上它就是退格 | serial | FIXED-verified | `_consume_char` 原来写 `if char == "\x7f": return index + 1` —— 直接丢弃。 | archive/ledger-detail-2026.md (L-281) |
| L-282 | `ESC[4~`（End）与 `ESC[4h`（IRM）不能共用一个 `first =… | core | FIXED-verified | 我第一版在 `_apply_tilde` 里写 `elif first == 4: self.insert_mode = False`， | archive/ledger-detail-2026.md (L-282) |
| L-283 | `view.reset()` 只清选区、不清屏，会让老测试悄悄失效 | terminal | FIXED-verified | R-212 把屏幕所有权移到 `TerminalPage` 之后，`TerminalView.reset()` 如果不擦屏， | archive/ledger-detail-2026.md (L-283) |
| L-284 | 「清除窗口」绑死 `clear_log`，在终端页等于没反应 | terminal | FIXED-verified | `btn_clear.clicked.connect(self.clear_log)` 是写死的。用户坐在终端页时按它， | archive/ledger-detail-2026.md (L-284) |
| L-285 | `ESC[2J` 不清光标，所以清屏后输出从半行开始 | terminal | FIXED-verified | `TerminalView.clear()` 原来只有 `self.screen.erase_in_display(2)`。 | archive/ledger-detail-2026.md (L-285) |
| L-286 | `Screen.resize()` 只拷左上角矩形 = 折行内容被截断 + 光标错位 | terminal | FIXED-verified | 旧实现：建一张 `rows x cols` 的新网格，把 `min(rows, old.rows) x min(cols, old.cols)` | archive/ledger-detail-2026.md (L-286) |
| L-287 | 终端 reflow 的正确性判据 | terminal | FIXED-verified | 不能只断言"没崩"：截断后的屏幕同样不崩。要同时断言 | archive/ledger-detail-2026.md (L-287) |
| L-288 | 手画的滚动条不是控件 —— 必须自己实现命中/拖动/跳转 | chart | FIXED-verified | `_paint_scrollbar()` 只做了 `drawRoundedRect`：没有任何输入处理，所以鼠标在上面 | archive/ledger-detail-2026.md (L-288) |
| L-289 | 点击轨道应该"跳过去"，不是"翻一页" | core | FIXED-verified | 我第一版写的是 `_page_scroll()`（按 `screen.rows - 1` 翻页）。问题：如果点在滑块 | archive/ledger-detail-2026.md (L-289) |
| L-290 | 用真值判断"要不要算"，等于把最特殊的那个值留给默认值 | core | FIXED-verified | 滑块位置原本写成： | archive/ledger-detail-2026.md (L-290) |
| L-291 | 滚动条的几何自检要覆盖"两端 + 单调性" | core | FIXED-verified | 只断言"能滚动"或"有变化"抓不到这类 bug：`_scroll_off` 从 38 变到 0 时滑块确实 | archive/ledger-detail-2026.md (L-291) |
| L-292 | 同一信号被两个地方各接一次 = 每个按键发两遍（"ls" -> "llss"） | terminal | FIXED-verified | 终端改成插件后（R-218），`TerminalPlugin` 自己把 `page.keyBytes` 接到 `api.send`， | archive/ledger-detail-2026.md (L-292) |
| L-293 | 插件化之后，宿主里那批"转发方法"必须一起删掉 | plugins | FIXED-verified | `_on_terminal_keys` 是插件化之前的发送入口。插件接管后它变成死代码， | archive/ledger-detail-2026.md (L-293) |
| L-294 | R-055 的自测轮询与心跳同为 10 ms，会自己造出"停顿" | serial | FIXED-verified | R-055 用 `QTimer.singleShot(10, poll)` 轮询串口是否就绪，同时又用 10 ms | archive/ledger-detail-2026.md (L-294) |
| L-295 | 判断 R-055 是否回归的可靠做法：把插件全部停用再测 | chart | FIXED-verified | 三次 A/B（各 4 次采样）： | archive/ledger-detail-2026.md (L-295) |
| L-296 | 「发布包不含 plugins/」这条老规则会静默吃掉新拆出来的核心插件 | build | FIXED-verified | 现象：1.1.6 的 zip 构建成功、678 条目、`testzip()` 为 None、打包自检 | archive/ledger-detail-2026.md (L-296) |
| L-297 | 「按源单元格索引」找光标，遇到空行就必然失效 | terminal | FIXED-verified | `_reflow` 原来用 `mapping[(src_row, src_col)] -> 新位置` 跟踪光标，可靠的前提是 | archive/ledger-detail-2026.md (L-297) |
| L-298 | 「这一行满了」不等于「软换行」 | ui | FIXED-verified | 我用 `_row_is_full()`（判断整行无空格）来识别软换行，想让拉宽窗口时把 | archive/ledger-detail-2026.md (L-298) |
| L-299 | 我擅自让 `\r` 跳到"逻辑行首"，制造了用户报的那个 bug | core | FIXED-verified | 现象是「两行不同的内容叠在同一行」，我推断是 `\r` 只回到当前网格行第 0 列、 | archive/ledger-detail-2026.md (L-299) |
| L-300 | 重排时为了"安放光标"多生成一行，会把顶行挤进回滚区 | terminal | FIXED-verified | 宽字符用例（30 个「中」，6x40 → 6x20）丢了 5 个字。原因：文本恰好填满 | archive/ledger-detail-2026.md (L-300) |
| L-301 | Linux 长行/历史重绘仍错乱：字符网格尺寸与远端不一致 | core | FIXED-verified | 新证据：COM30只读查询为24x80、TERM=vt220；真实长输入/清行/上历史/下历史回包，在80列重放能恢复提示符和草稿，在52或104列重放产生提示符覆盖、历史残留。此前重排合成用例不能证明实机修复。 | archive/ledger-detail-2026.md (L-301) |
| L-302 | 插入模式切换错误归零光标 | terminal | FIXED-verified | `_apply_mode` 的非私有模式4分支多调用了 `_move_to(0,0)`，且有重复不可达分支；移除后，ESC[4h和ESC[4l单点探针均保持(0,11)。真实捕获中未见该序列，不将它冒认为本次实机错行的主因。 | archive/ledger-detail-2026.md (L-302) |
| L-303 | 刚打开的串口上按键被静默丢弃：发送守卫读了过期的状态快照 | serial | FIXED-verified | 现象：全量 GUI 里 R-292 偶发失败，详情为 `b'' connected=True write_is_manager=False view_focus=False tx=0`，而单跑必过。 | archive/ledger-detail-2026.md (L-303) |
| L-304 | 打开日志后跟随失效、停在终端页 | terminal | FIXED-verified | `_show_log_text` 在 begin_batch 后 clear_all，后者将批量计数归零，end_batch 无法释放 TailFollow 更新锁，后续 follow 全部短路。清屏前移到 begin_batch 之前；真实日志页面是 terminal，不是 console。 | archive/ledger-detail-2026.md (L-304) |
| L-305 | 对端删除扩展命令后上下翻动，组头文字消失只剩色条 | ui | FIXED-verified | 短列表剩余滚动范围 23px，小于组头 26px；旧吸顶条件要求整个组头卷走，文字已被裁切而吸顶永远不触发。改为组头顶部一旦越过视口即借用原组头；不改颜色、不建简化标题。 | archive/ledger-detail-2026.md (L-305) |
| L-306 | HEX 双击编辑框看不清内容、数字被裁掉 | log | FIXED-verified | 编辑框只按 `horizontalAdvance("00") + 14` 定宽，而全局样式表给 `QLineEdit` 的 `padding: 0 10px` 不体现在 `contentsMargins()`：实测 40 px 的框里能画的只有 ~20 px，两字符要 26 px，首字符被裁；末列定位还可能把框推到视口外。 | archive/ledger-detail-2026.md (L-306) |
| L-307 | 看完 HEX 文件后整个看板停止刷新 | chart | FIXED-verified | 两个缺陷叠加：① 页面把 `acked` 发在绘制之后，一张卡的 `paint`/`setOption` 抛错（隐藏标签页里容器尺寸为 0 时会发生）就永远不确认；② Python 端把 `_pending_ack` 当无条件锁，没有超时，于是所有卡片一起停刷，不只是出事的那张。 | archive/ledger-detail-2026.md (L-307) |
| L-308 | 看板大数据/多控件时添加控件很慢或加不上、小屏看不全、顶栏塞满按钮 | chart | FIXED-verified | 三个独立原因：① `add_widget()` 无条件 `request_resync()`，把整个保留历史重新序列化（10 通道 × 5000 点 = 83 KB），页面还要为每张已有曲线重绘一遍；② `#canvas` 是 `overflow: hidden`，绝对定位卡片不产生滚动范围，内容超屏就彻底不可达；③ 顶栏堆了 7 个按… | archive/ledger-detail-2026.md (L-308) |
| L-309 | 顶栏冒出 5000 输入框 / 新卡片飞到最底下 / 仪表带曲线坐标轴 | chart | FIXED-verified | 三个独立缺陷： | archive/ledger-detail-2026.md (L-309) |
| L-310 | 搜索与扩展命令都折叠时，扩展命令被拉大 | core | FIXED-verified | `relayout_sidebar()` 读的是两个面板的 `_collapsed` 标志，而界面实际高度取决于 `body` 的可见性。二者在「搜索面板从全屏恢复」「一边展开时另一边折叠」后会不一致，于是走到“两边都展开”分支，把 `_sidebar_sizes` 恢复成 230/540。 | archive/ledger-detail-2026.md (L-310) |
| L-311 | 曲线窗口跑满后不再刷新（切标签页才被注意到） | chart | FIXED-verified | 2026-11-20 后续：旧 drawnCount 增量合并仍有数组别名和裁头边界；现实现已由 L-313/L-314 的版本切片与绝对序号替代，以下为历史修法，不得照搬恢复。 | archive/ledger-detail-2026.md (L-311) |
| L-312 | 看板刷新卡顿（后备：懒更新） | chart | OPEN | 2026-11-20 更正：用户否定此轮结果，原“渲染帧率不降、卡顿已修复”结论无效。下列42.4/34.3→2.9/1.4ms只测 setOption 提交，不是绘制完成；30轮合并为一次不能证明持续刷新。实际完成背压和负载证据见 L-316。 | archive/ledger-detail-2026.md (L-312) |
| L-313 | 看板失去响应 / 自追加数组 / RangeError | chart | FIXED-verified | 原 drawnBuffer 在历史未满时直接持有 rows；下一帧向 buffer push 的同时以 rows.length 为循环上界，同一个数组无限自增长。Node 执行实际 dashboard.js 可复现 RangeError；修为按通道版本缓存独立尾部切片，多卡共享绘图切片但不与历史数组别名。 | archive/ledger-detail-2026.md (L-313) |
| L-314 | 满缓冲漏尾样本 / 旧确认解锁 / 纯读数停刷 | chart | FIXED-verified | 绝对接收序号不随deque淘汰衰减，确认仅提交发送快照；批次号单调且重同步不复用。新鲜度用样本版本而非仅曲线绑定，纯 value/gauge/bar/lamp 均可刷新。 | archive/ledger-detail-2026.md (L-314) |
| L-315 | 居中错位 / 布局递归 / 多余resize | ui | FIXED-verified | 单一40ms合并布局任务处理新卡居中、内容范围和必要resize；显式用户移动取消autoPlaced。删除settle链和不可达重复添加逻辑；视口外卡懒创建、滚入追上数据。 | archive/ledger-detail-2026.md (L-315) |
| L-316 | 实际绘制背压 / finished不回 / 效率低 / GUI长尾 | chart | OPEN | 已修机制：实时仅传曲线id/data、仪表data；关闭滑块数据阴影。实际rendered后用可被后续绘制重置的rAF屏障确认，finished也可解除等待；仅finished曾真实出现rendered=9/finished=1而锁住。单卡异常/删除不锁全板；保留超时和返回页重同步。确认驱动下批，只补齐从发送起算的目标节拍，最多一个在途… | archive/ledger-detail-2026.md (L-316) |
| L-317 | 看板配置丢多通道 / 精度 / 非法范围 | chart | FIXED-verified | 清洗器保留channels去重列表、decimals与状态文字，拒绝NaN/无限范围和重复widget id，导入失败不得改当前布局。 | archive/ledger-detail-2026.md (L-317) |
| L-318 | 插件并未解耦：有门面却直接 import 宿主内部模块 | plugins | FIXED-verified | 用户质疑「插件没有解耦？」属实。terminal / transfer / hexview 源码里直接 | archive/ledger-detail-2026.md (L-318) |
| L-319 | 帮助菜单显示 menu.help / 翻译缺键 | plugins | FIXED-verified | 根因：标题栏使用 `tr("menu.help")`，宿主表没有该键，回退策略直接显示键名；扫描还发现 `misc.control.open` 缺失。已补齐两键，不更改回退策略。 | archive/ledger-detail-2026.md (L-319) |
| L-320 | 终端与文件传输停用后商店消失 / 无法启用 | terminal | FIXED-verified | 根因：商店行取本地安装与远程目录并集，但 `installed()` 排除了停用插件；terminal/transfer 是随包核心插件，不在远程目录，因此从两边都消失。 | archive/ledger-detail-2026.md (L-320) |
| L-321 | 修复后同版重发 v1.1.7 / 插件商店是否重传 | build | FIXED-verified | `4a5db45` 已推送main；复用Release #452554311并替换同名便携ZIP，发布脚本退出0、latest=v1.1.7。既有标签仍为 `aeee113`，不强制移动；修复源码在main。 | archive/ledger-detail-2026.md (L-321) |
| L-322 | 终端/传输与宿主完全同构 / 第二个窗口清掉第一个窗口文案 | terminal | FIXED-verified | 宿主删掉了 `window.console` / `window.transfer` 与 `BUILTIN_PLUGIN_PAGES`：页键改由清单 | archive/ledger-detail-2026.md (L-322) |
| L-323 | 「卸载」只记标记、永远显示已安装 | terminal | FIXED-verified | 2026-11-23 用户反证：重复多次卸载后仍出现「启用/已安装」；原单次285项验证不覆盖真实按钮循环及双副本，不能视为闭环。来源框也再次被否定，R-325整行交互待修复。 | archive/ledger-detail-2026.md (L-323) |
| L-324 | 多曲线来源框不能整行勾选 / 黑块难辨 | chart | FIXED-verified | 后续用户反证滚轮悬停仍黑底：原生drawControl可重新盖上系统高亮；现背景、文字、复选框均自行绘制。强制Highlight/HighlightedText黑色，滚轮三次选中鼠标下行像素仍浅，滚动不改勾选；Windows字体截图已查看。来源入口只供line，非line多名称只绑定第一项。 | archive/ledger-detail-2026.md (L-324) |
| L-325 | 灯自定义亮灭颜色 / 调色板 / 颜色保存丢失 | build | FIXED-verified | R-328：创建色块、现有灯齿轮与真实Qt调色板，亮灭颜色贯通绘制/describe/persist/配置；旧null颜色兼容。真实回归抓到persist遗漏字段和模态持有旧布局对象，均修复；QSS用对象名限定，避免子调色板按钮染色，内置文案经api.t本地化。 | archive/ledger-detail-2026.md (L-325) |
| L-326 | 保留点数低于展示帧数时只有保留量 | chart | REVERTED | 曾把有效容量改成 max(保留, 展示)，但那会篡改用户的显式「曲线点数」：R-301 要求显式值就是保留上限，points=150 必须保留 150。已回退为 `min(MAX_POINTS, max(100, limit))`，`visible` 只控制每帧绘制尾部。 | archive/ledger-detail-2026.md (L-326) |
| L-327 | 实际5000帧设置未满即历史消失 | chart | OPEN | 只读核对用户settings.ini实际window=0/visible=5000/points=5000。两条曲线同配置在128/1000/3200/4999/5000/5101/6200保持完整首尾，尚未复现；不能把L-326当成其根因。下一步用用户实际布局/原始输入与运行副本证据区分接收、缩放可见范围、旧包；不宣称整个问题修好。 | archive/ledger-detail-2026.md (L-327) |
| L-328 | 多色灯 / 单色灯任意触发数值 / 数值颜色映射 | chart | FIXED-verified | R-329：同一个lamp的colorRules精确数值匹配，单条规则就是单色灯；fallbackColor支持无色或自定义色。创建入口/现有卡片复用LampColorsDialog，可增删规则、调色和指定无色。旧无规则或null字段仍保持零/非零二态，明确启用匹配后迁移。 | archive/ledger-detail-2026.md (L-328) |
| L-329 | 核心插件启用无效 / 商店操作闪出空白小窗 | terminal | FIXED-verified | 用户反证「启用终端启用不了」：根因是 `toggle.clicked.connect(lambda _checked=False, ... on=not item.get("disabled"): self.set_enabled(key, on))`，而 `set_enabled` 内部写 `set_disabled(identifi… | archive/ledger-detail-2026.md (L-329) |
| L-330 | make_release 上传中断后留下空资产列表（FIXED-by-manual-u… | terminal | FIXED-verified | 现象：代理侧 `make_release.py --publish` 在后台终端会话里反复被清理，最后报 `上传失败： 404 Not Found`； | archive/ledger-detail-2026.md (L-330) |
| L-331 | 日志墙钟毫秒 / tick / 无时间戳 | log | FIXED-verified | R-331：支持冒号毫秒、墙钟优先、单位 tick、裸 tick 与无戳 0.1 秒估算；同一行墙钟优先于芯片 tick。 | archive/ledger-detail-2026.md (L-331) |
| L-332 | 升级后核心插件缺失 | plugins | FIXED-unverified | R-332：`PluginHost.load()` 前调用 `restore_core_plugins()`，从 `paths.resource_dir()/plugins` 补齐缺失的 terminal/transfer；已存在目录与 `plugin-data/<id>` 不动。 | archive/ledger-detail-2026.md (L-332) |
| L-333 | 打开日志错图 / WebEngine右键Reload恢复 | log | FIXED-verified | 新证据（user, 2026-11-24）：此前候选仍无效，右键页面Reload后恢复。原clear_data已清_sent，先前“缺少清零”根因撤回；桩测试不能证明真实加载问题。 | archive/ledger-detail-2026.md (L-333) |
| L-336 | 满缓存后曲线边刷新边变形 | chart | OPEN | 2026-11-24 用户反证：同组数据的两张截图波形不同；用户确认没有缩放，但截图时间不同，所以横轴510–690与480–660的差异本身不是故障证据。原 minmax 已彻底解决的结论撤回；下面旧实验只证明特定负载的帧间像素变化。 | archive/ledger-detail-2026.md (L-336) |
| L-337 | 看板框选与整组拖动 | chart | FIXED-verified | R-337：新增 `state.selection`（多选）与 `hookMarquee()`：在 `#canvas`/`#surface` 空白处左键按下开始框选，选框挂在 `#surface` 内容层（随滚动跟随），相交即选中；Ctrl 追加/切换；空白单击清空。 | archive/ledger-detail-2026.md (L-337) |
| L-338 | / L-339 清空数据与清空看板 | chart | FIXED-verified | R-338：顶栏新增「清空数据」，直接调用既有 `clear_data()`（清采样、保留卡片），与「清空看板」互补。证据：点击真实按钮后页面 `retained` 全为 0，卡片数不变。 | archive/ledger-detail-2026.md (L-338) |
| L-340 | v1.1.7 第五次重发 | chart | FIXED-verified | 内容：R-336 minmax 采样、R-337 框选整组拖动、R-338 清空数据、R-339 清空看板入菜单， | archive/ledger-detail-2026.md (L-340) |
| L-341 | 数据来源选择框悬停背景变黑 | ui | FIXED-verified | 根因：`SourceDelegate.paint()` 把 `theme.WASH_HOVER` / `theme.LINE_STRONG` 直接交给 `QColor`。这两个令牌是 Qt 样式表形式 `rgba(18, 26, 43, 14)`（alpha 0-255），`QColor` 完全无法解析，返回 `isValid()==F… | archive/ledger-detail-2026.md (L-341) |
| L-342 | 换电脑后检查更新报 SSL 证书校验失败 | tls | FIXED-verified | 根因：便携包用官方 CPython embeddable 运行时，`python313._pth` 关闭了 `site`，所以用户 site-packages 里的 `certifi` 不可见；运行时只带 `_ssl.pyd` + `libssl-3.dll`，没有任何 CA 证书。开发机能通是因为 Windows 上 OpenSSL… | archive/ledger-detail-2026.md (L-342) |
| L-343 | 关闭软件后屏幕是否仍常亮 | power | FIXED-verified | 用户确认：在软件里开了「保持屏幕常亮」，关掉软件后屏幕不应继续常亮。 | archive/ledger-detail-2026.md (L-343) |

## Open / do-not-retry quick list

Filter the table above instead of reading these; they are repeated here
only because they are the rows a new session must not miss.

Open: **L-312**, **L-316**, **L-327**, **L-336**
Blocked: **L-006**(RULED-O), **L-012**(RULED-O), **L-068**(REVERTE), **L-073**(RULED-O), **L-083**(RULED-O), **L-093**(RULED-O), **L-102**(RULED-O), **L-120**(RULED-O), **L-140**(RULED-O), **L-191**(RULED-O), **L-199**(RULED-O), **L-207**(RULED-O), **L-218**(REVERTE), **L-260**(REVERTE), **L-326**(REVERTE)
