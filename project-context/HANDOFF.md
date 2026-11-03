# Current handoff
Updated: 2026-11-03 (v1.1.4 fourth batch: usable update dialog, no auto-scroll in
search results)

Current objective: v1.1.4 第四批（两条反馈，全部落地并验证）。① **「软件更新」界面要能看出在做什么**
（R-143）：检查更新点下去之后长期没有任何变化，用户以为功能坏了；现在有进度条、有流程日志、
顶部常显「当前版本 · 最新版本」，失败会写明原因并给出「重新检查」。根因是两个问题叠加 ——
GitHub 未登录 API 按出口 IP 限流（HTTP 403），且 `UpdateWatcher.failed` 是**无载荷、无接收者**
的信号，于是对话框永远停在「正在检查新版本…」（L-168）。`update.check_release()` 在 API 受限时
自动改读发布页 `releases.atom` + `releases/expanded_assets`，返回 `{ok, source, error, ...}`
且永不抛异常；`update.check()` 保持失败返回 `None` 的旧契约（R-107）。② **搜索结果列表不自动
滚到最新**（R-144）：改为和日志窗口同款的浮动「回到最新命中」气泡，只在下面还有内容时出现
（L-169）。注意 `hide_pill()` 是 170 ms 淡出，断言不可见要看 `_visible`，不要看 `isVisible()`。
第三批的成果仍然有效：窗口外圈柔和阴影（8 px 环，**不许再调大**，L-162）、
标题栏 Aero Snap、两个展开分组之间只留一段行距。
Still true from 1.1.3: self-update,
offline local package, instant restore, one-button file send (R-107..R-121).
A window that is idle asks the GitHub release API once, in the
background; a newer release lights a red dot on the menu button, can be downloaded
and staged, and the swap runs **before Qt starts** on the next launch without ever
touching `omnicom-data/` (commands, captures, settings, plugins) - R-107/R-108.
Still true from 1.1.2: self-contained folder, 100 MB log budget, glass panes,
text-only caption row, own tooltip, no-exe packaging, live search, launcher, keep-awake.
**1.1.4 is published**: https://github.com/Fatmear/OmniCOM/releases/tag/v1.1.4
(asset `OmniCOM-1.1.4-win64.zip`, 41 MB, 42 987 769 bytes,
sha256 `5c04d27dcddc05335d3c6e570333d0067cb9688a5b93de085c0b32439d66defd`;
re-uploaded after the in-bundle `AI-GUIDE.md` version example was aligned to 1.1.4,
and the downloaded asset was byte-compared against the local zip).
**All three releases (1.1.2, 1.1.3, 1.1.4) stay public on the release page** - do not
draft the older ones, users must be able to download any of them. Only the *latest*
pointer is managed: it must stay on the newest version, because the in-app updater and
the 从升级包更新 button both read `releases/latest`. Restoring a draft rewrites its
`published_at`, which silently moves `latest` to that older tag (L-167) - fix it with
`PATCH /releases/<id> {"make_latest": "true"}`, never by re-drafting. The 1.1.4 zip is
the very file the updater downloads. Next release: bump the version in the four places
listed under "Next steps", then `python tools/make_dist.py --check --zip` and
`python tools/make_release.py --publish` (no `--hide-old`).
Version **1.1.4**; **141 requirements / 692 checks** pass automated verification.
Real hardware remains untested - never infer driver timings from DEMO runs.

Source run: `start.cmd`, `python run.py`, `python run.py --demo` (虚拟串口).
CLI: `python -m omnicom.cli …` (never spawned automatically).
Package: `python tools/make_dist.py --check --zip` -> `dist/OmniCOM/` +
`dist/OmniCOM-1.1.4-win64.zip` (~99 MB folder / 41 MB zip, no installs needed).
Screenshots: `python tools/make_screenshots.py` -> `docs/images/*.png`.
Manual: `omnicom/docs.py` (18 chapters) -> `python tools/make_readme.py`.


Constraint IDs to check before acting: R-019 (no self-built exe), R-017 (no silent byte loss),
R-012 (auto-scroll), R-024 (log line shape), R-033 (bilingual), R-035 (responsive),
R-041 (instant collapse), R-049 (primary line + visible notes), R-054 (UI performance),
R-055 (nonblocking lifecycle), R-063 (launcher two areas), R-066 (capture RX+TX),
R-068 (equal side columns), R-069 (version 1.1.4), R-070 (capture folder),
R-071 (accent + wallpaper), R-072 (live search append), R-076 (no extra console),
R-077 (glass panes), R-078 (records folder), R-079 (note alignment), R-080 (1 s tooltip),
R-081 (size budget + loss count), R-082 (text-only caption row), R-083 (self-contained
package), R-084 (own rounded glass tooltip), R-085 (animated minimize/restore), R-086
(shipped CLI docs + MIT licence), R-087 (long log lines wrap), R-088 (file-based settings, independent windows), R-089
(Ctrl+C copies the log), R-090 (send history), R-091 (terminal paste), R-092 (batch
commands), R-093 (import into a group), R-094 (Enter-to-send switch), R-095 (left click only),
R-096 (Ctrl+wheel log zoom), R-097 (one-click desktop shortcut), R-098 (two font pairs),
R-099 (file buttons inside the send column), R-100 (brand block tight + centred),
R-101 (capture mirrors the log), R-102 (clear/save view in the rail), R-103 (file
buttons under 发送), R-104 (one face per group), R-105 (instant restore, no stretch),
R-106 (progress + name row), R-107 (background update check), R-108 (update never
touches user data), R-109 (update is the lowest priority), R-110 (\r\n band with
stacked options), R-113 (发送文件 / 停止 is one button), R-114 (option switches
aligned), R-115 (long lines keep their prefix), R-116 (send history bounded: 50 by
default, 300 max, clearable in Preferences), R-117 (port list shows 端口号 + 设备名),
R-118 (rail 窗口 heading), R-119 (CLI binds to the window that owns the port; several
windows / ports side by side; a closed port ends the CLI binding), R-120 (DTR/RTS off
by default so opening a port never resets a board), R-121 (offline update from a local
release zip), R-122..R-126 (one JSON document per call, UTF-8 streams, settle,
port lock, batch sampling), R-127 (new commands start unticked), R-128 (paste
`#` notes), R-129 (never write AT as the field name), R-130 (drag a command to change
its group and its position), R-131 (the send box keeps its text; consecutive repeats
are recorded once), R-132 (the field reads 命令 / Command), R-133 (no drag hint text),
R-134 (one ↑ reaches the previous entry), R-135 (the order survives a restart),
R-136 (batch scope: all commands / one group / several groups), R-137 (a trash button
on the group header, same flow as the context menu), R-138 (1 px dark window border),
R-139 (a soft drop shadow outside that border), R-140 (drag the title bar to the screen
top to maximise, drag down while maximised to restore), R-141 (only one line of
spacing between two expanded groups).
Full list: `REQUIREMENTS.md`.

Verified in repository (2026-10-31):
- `python tools/acceptance_test.py` -> **671 checks, 0 failures, 0 uncovered,
  138 requirements, ALL REQUIREMENTS VERIFIED**.
- GUI suite: **515/515**, including the window-edge and shadow pixel sampling (10),
  the Aero-Snap state machine (11), the group-gap measurement (6), the batch-range
  matrix, the group trash wiring, the `#`-note paste, the whole drag/drop matrix,
  the send-history revisions and the DEMO port.
- `python -m ruff check .` → All checks passed; `tools/protocol_selftest.py` → 23/23;
  `tools/contrast_audit.py` → 15/15 ≥ 4.5:1; READMEs regenerated from `docs.py`.
- CLI contract probes: one document even on failure, exit code == `ok`, UTF-8
  stdout, `--settle 0.2` returning in 0.29 s instead of 3 s, a held lock failing
  after its timeout, and 5 samples in 0.86 s versus 2.22 s for 5 separate calls.
- `python tools/make_dist.py --check --zip` → packaged `--selftest` OK with the bundled
  runtime (`-E -s`), and a real windowed launch from the bundle stays up.

## 本轮 (1.1.4 第三批) 内容

| 反馈 | 根因 | 现在 |
| --- | --- | --- |
| 不仅要有黑边，还要有阴影 | 无边框 + `WA_TranslucentBackground` 的窗口，DWM 不画阴影；`QGraphicsDropShadowEffect` 在半透明顶层窗口上不合成 | `Aurora` 在自己的边距里抠出 8 px 环，由外向内叠 8 圈圆角矩形（越靠外越淡），位图按窗口尺寸缓存（R-139 / L-162 / L-163） |
| 阴影一加，描边画到了阴影最外圈 | `_paint_edge()` 用绝对坐标 `(0,0)` 起算，页面被内缩后就不再是原点 | 改成从 `page_rect().topLeft()` 起算，描边回到页面边界上（L-161） |
| 阴影取多厚？14 px 左栏就出滚动条 | 环是从窗口边距里让出来的，每 1 px 吃掉 2 px 内容高度 | 按真实样式表实测：14 → 滚 10、12 → 6、10 → 2、8 → 0，取 8 px 并把 alpha 提到 11（R-118 不回归 / L-162） |
| 拖标题栏到屏幕顶部要最大，最大化时往下拖要还原 | 无边框窗口没有系统标题栏，Aero Snap 天然不生效 | `TitleBar` 自己做：`_snap_top()` 取鼠标所在屏可用区顶边，`_snap_to_max()` / `_restore_from_max()` 按鼠标横向比例重算抓取点，`SNAP_RELEASE=40` 迟滞（R-140） |
| 两个展开分组之间空隙太大 | 上一批给分组正文末尾加了 `DropStrip`，多出 5 + 14 px | 移除组内那条（落在最后一行下半区即可插到组尾），保留列表末尾的拖出目标；实测 6 px（R-141 / L-166） |

## 上一轮 (1.1.4 第二批：命名 / 拖动 / 历史 / 顺序 / 批量 / 垃圾桶 / 黑边) 内容

| 反馈 | 根因 | 现在 |
| --- | --- | --- |
| 命令字段名 CMD 改成「命令」 | 缩写不像人话，中英混排检查也覆盖不到纯英文缩写 | `cmd.field` = `("命令", "Command")`，对话框与列表都用它（R-132） |
| 别显示「拖到这里移出分组」 | 拖动目标条上写了常驻提示文字，挡视线 | `DropStrip` 平时无文字、无高度占位，只在悬停时画一条落地线（R-133） |
| 框里是 333 时按一次 ↑ 就该是 222 | 输入框保留刚发出的内容（R-131），首次进入浏览正好落在同一条上 | `_browse()` 首次进入浏览时若 `history[-1]` 等于框内草稿，起点直接取 `len-1`（R-134） |
| 顺序重启后要记住 | 固定名 `.tmp` + 各进程整份写回 + 读失败静默 `_seed()` | 文件锁 + `mkstemp` 唯一临时文件 + 读改写合并 + 墓碑 + 坏档改名并提示（R-135 / L-157） |
| 批量选择要能只选组内 | 全选只认"所有行"，没有范围概念 | 新增「范围」下拉（全部 / 单组 / 已选 N 组）+ 分组三态勾选框，全不选永远清空（R-136 / L-158） |
| 分组上也要有垃圾桶 | 分组标题只有编辑与展开 | `GroupHeader.trash_btn` 接到已有的 `remove_group()`，与右键完全等价（R-137） |
| 软件边界要加黑边 | 无边框 + 半透明窗口在白色桌面上没有边界 | `Aurora._paint_edge()` 画 1 px 深色描边；token 必须是 `#RRGGBB`（`QColor` 不认 CSS `rgba()`）（R-138 / L-159） |

## 更早 (1.1.4 第一批：拖动 / 常亮 / 发送框) 内容

| 反馈 | 根因 | 现在 |
| --- | --- | --- |
| 想按住命令拖动来换分组、换顺序（像资源管理器拖文件） | 命令列表完全没有拖动：`QDrag` 全项目一次没用过；`CommandStore` 只有 `move_many()`，它只改 `group` 字段、不动 `items` 里的位置，「换组 + 落到某个位置」表达不出来；先删后加会写两次盘并让命令闪一下 | `CommandStore.reorder(ids, group, index)` 一次算好目标下标再单次提交（R-130）；UI 补齐完整链路：私有 MIME `DRAG_MIME`、行上半/下半 = 插到前/后、组标题 = 追加进该组、虚线空投条 = 移出分组、空白区 = 移出全部；`_in_drag` + `QTimer.singleShot(0, …)` 把重排推迟到 `QDrag.exec()` 的嵌套事件循环退出之后，否则 `rebuild()` 会把拖动源自己 `deleteLater()` |
| 拖完松手不要顺手发一条命令 | `mouseReleaseEvent` 只要不是按下就发，拖动结束同样会补一次 | `_suppress_send` 只吃掉紧接的那一次松手，之后正常左键点击照常发送（R-095 不受影响） |
| 上次开着「保持屏幕常亮」，重开又自己开了 | `_restore_settings()` 读 `display/awake`、`_save_settings()` 写回，等于永久记忆；开关藏在右栏，用户找不到原因 | 每次启动一律 `setChecked(False)`，不写该键，并 `s.remove("display/awake")` 清掉旧值（降级不会复活）；勾开关不再重写整个 INI（R-062 / L-156） |
| 发送完输入框被清空，改一个字重发都得重新敲 | `emit_current()` 末尾 `self.input.clear()` | 不再清空；历史本来就会丢弃「与上一条相同」的重复，于是连续发送只记一条、A-B-A 正常记两条（R-131） |

## 更早 (1.1.4 第零批：CLI 契约 / 命令库粘贴) 内容

| 反馈 | 根因 | 现在 |
| --- | --- | --- |
| `--expect` 未命中时输出两个 JSON 文档 | 失败路径是「先 `_emit(result)` 再 `fail()`」，一次调用写两份文档，代理侧 `json.loads(stdout)` 必然报 `Extra data: line 2` | 收发合成一个 `send_once()`，成功与失败在同一份结果里表态，只写一次（L-150 / R-122） |
| 退出码与 `ok` 不一致 | `fail()` 里 `ok:False` 会被 `extra` 自带的 `ok:True` 覆盖，于是 `ok:true` 却 exit 1 | 判定写在最后，退出码统一取自 `result["ok"]`，`--help` 写明 0/1/2（R-122） |
| 中文错误消息乱码 / `UnicodeDecodeError` | 从没动过 stdout 编码，Windows 上继承 cp936 | `_utf8_streams()` 在 `main()` 第一行强制 UTF-8（L-147 / R-123） |
| 没 marker 的命令只能等满 `--wait` | 读取是纯定时循环 | `--settle T` 在首个字节到达后静默 T 秒返回，结果带 `silent`（L-148 / R-124） |
| "成功但无输出"语义不明 | `--expect` 没命中等同失败，可设备本来就不回显 | `--ok-if-silent` 只放行"完全沉默"，收到字节却不匹配仍然失败（R-124） |
| 同一端口并发调用互相抢占 | 每次调用都是新进程、各开各的口 | `PortLock` 在 `<data>/locks/<port>.lock` 上做系统文件锁，`--lock-timeout` 控制排队（L-149 / R-125） |
| 0.5 Hz 轮询开销远大于数据 | 每次 = 启动 Python + 开串口 | `--repeat N --interval T` 同进程只开一次口、每采样一行 JSON；`--ports A,B` 覆盖多口（实测 5 次 0.86 s vs 2.22 s，L-151 / R-126） |
| 新建命令自动参与依次发送 | `_defaults()["enabled"]` 是 True | 默认 False，行上勾选框同步，`send_all()` 空集提示改为「还没有勾选任何命令」（R-127） |
| 多行粘进带 `#` 注释的命令清单，注释也被当成命令正文 | 旧 `split_lines()` 只按行切，既不认注释也吃不掉 `#` 前那串对齐空格，于是每条命令尾巴都挂着 `# …` | `commands.split_comment()` 在**第一个未转义**的 `#` 处切开、两半各自 `strip()`，尾段写入该条的 `note`；整行注释不生成命令，正文真要 `#` 就写 `\#`（L-152 / R-128） |
| 命令字段名写着「AT」 | 字面量 `"AT"` 直接写死在 `CommandDialog` 里，没有 i18n 键，而且暗示这个库只能放 AT | 改用 `tr("cmd.field")`，中英都是 **CMD**；`net sta enable` 这类控制台命令、AT 行、片段都算命令，AT 只是其中一种（R-129） |

## 更早 (1.1.2) 内容

| 反馈 | 根因 | 现在 |
| --- | --- | --- |
| 打包要能直接跑，不装依赖 | 只发 `OmniCOM.pyz`，目标机得自己装 Python/PySide6 | 包内自带 CPython embeddable 运行时 + 裁剪后的 PySide6/shiboken6/pyserial，`_pth` 钉死搜索路径 |
| 工程里要有干净的 1.1.2 | 版本号写在 `__init__.py` | 版本、README、需求表、测试断言全部同步到 1.1.2 |

## 更早 (UI round, 2026-10-28) 内容

| 反馈 | 根因 | 现在 |
| --- | --- | --- |
| 右键命令也会发送 | `mouseReleaseEvent` 不看 `event.button()`，右键抬起同样走发送分支 | 只认 `LeftButton`（R-095） |
| 日志要能 Ctrl+滚轮缩放字号 | 日志视图没有滚轮处理 | `wheelEvent` 判 Ctrl → `zoom_font(±1)` 并发 `fontZoomed`，主窗口存 `display/log_px`（R-096） |
| 界面和日志字体要分开改 | 主题里只有一套字体令牌暴露到首选项 | 两套 family+px（`ui/*`、`display/log_*`），首选项两组控件（R-098） |
| 打开/发送文件按钮并入发送列 | 文件区单独一列 | 三个按钮同列、文件名在其左、进度条更左（R-099） |
| 顶部栏品牌两行离太远、想垂直居中 | `QLabel` 高度=行高（ascent+descent），全大写/中文没有下伸部 → 每行下白留 4-8 px | 按字形轮廓 `tightBoundingRect` 钉高度 + 2 px 间距 + 上下弹簧 + 2 px 下边距补偿（R-100） |
| 打包要能一键建桌面快捷方式 | 只有 `.cmd`，用户得手动「发送到 → 桌面」 | `omnicom/shortcut.py` + 包内「桌面快捷方式.cmd」，名字/图标/目标都正确且可重复执行（R-097） |

## Performance evidence

Same machine/load: 200-command group collapse, 100-line RX batches, log buffer stress.
Before: `.artifacts/ui-perf-before.json`; final: `.artifacts/ui-perf.json`.
- Group action P95: 25.887 → 3.888 ms; with events/paint: 36.395 → 18.093 ms.
- 100-line RX median: 54.487 → 30.787 ms; P95: 59.866 → 32.979 ms.
- Log buffer (R-081): 40 Mchars / 356 k blocks still 26 ms per 100-line flush; one trim of
  a 20 MB document costs 245 ms (Qt's own block trimming cost 6 374 ms - L-105).
- Package (R-083): 98 MB folder / 40 MB zip; PySide6 trimmed 171.5 → 77 MB.
- R-055: 12 × 400 ms slow operations, callback max 0.6 ms; heartbeat max 22.9 ms.

## Next steps (in order)

1. After any change: `python tools/acceptance_test.py`; before a release also
   `python tools/make_dist.py --check --zip`.
2. Run from `start.cmd` / `python run.py --demo`; physical RX, unplug/reconnect and a real peer
   X/Y/ZMODEM still need hardware. A 100 MB buffer on real hardware is also untested.
3. Reverify on a clean machine: unzip the release, double-click `OmniCOM.cmd`.
4. Bump `omnicom/__init__.py`, the R-069 row, `tools/acceptance_test.py` (`TITLES` + version
   check) and `tools/ui_selftest.py` together - a missed one fails the suite.

## Do not do

- 不要打包自编译 exe（R-019）；发布就 `python tools/make_dist.py --check --zip`。
- 不要在 widget 里硬编码中文：加 `i18n._TABLE` 键并 `tr()`；不要翻译用户数据。
- 不要用 PowerShell 的 `Set-Content -Encoding UTF8` 改源码（PS5.1 按 ANSI 读、写 BOM，中文会
  不可逆损坏 - L-106）；改源码用 Python 脚本并保留 CRLF。
- 不要让 `setMaximumBlockCount()` 去裁剪大文档（2~6 秒冻结，L-105）；用 `LogView.trim_oldest()`。
- 不要再用 `QToolTip`（矩形窗口会露白边，R-084）；提示统一走 `ui/tooltips.py` 的 `TipPopup`。
- 底部说明行**不可以**再加底板/边框/背景（R-082）；可读性靠 `INK` + 白色光晕。
- 全局事件过滤器只能做「一次 isinstance 判断」这种量级的工作（L-102）。
- 打包依赖清单改动后必须重跑 `make_dist.py --check`：漏一个 Qt 模块只有跑起来才会发现（D-062）。
- 不要在 `resizeEvent` 里设列宽或在 0 宽时 `setSizes()`（L-041/L-050）。
- 不要在 `setStyleSheet()` 之前 `setFixedSize()` 会被样式表设 `min-height` 的控件（L-053）。
- 新加 `Signal` 必须在 `_wire()` 连上（L-052）；每个 `ui_selftest` 步骤必须调 `next_()`（L-042）。
- 折叠等纯视觉变更不走 `_commit()`（L-045）；分组折叠只显隐共享容器。
- 快捷启动菜单按 id 找目标，不能 `is` 比较 Qt 字典副本（L-075）。
- 启动器按「窗口」计数，枚举留在后台线程（L-070/L-076）。
- 顺序归模型所有（`apps` / `window_order`），不依赖控件创建顺序（L-081）。
- 上排删除不关进程，下排删除不动配置，只 WM_CLOSE 校验过 PID 的句柄（L-082）。
- 记录文件是裸字节流：不要往里塞方向标签（L-084）；提示只显示文件大小（L-088）。
- **不要绕过 `_log_line()` 直接 `self.log.append_line()`**（L-089）。
- 强调色只能用 `theme.set_accent()` 改令牌，不要在 QSS 里重新写死颜色（L-091）。
- 面板玻璃只能走 `widgets.paint_glass()`，不要在 QSS 里写死白底（L-098/L-107）。
- 三栏默认宽度必须等宽（L-086）。
- 窗口显示/隐藏只能走 `minimize_with_motion()` / `animate_show()`：动画改的是真实几何，
  结束后必须用 `_motion_target` 还原，否则每来一次窗口小一圈（L-117）。
- 发行包必须自带 `CLI.md` / `AI-GUIDE.md` / `LICENSE`；`OmniCOM.cmd cli …` 与
  `python -m omnicom.cli` 必须是同一个契约（R-086）。
- 日志必须保持 `WidgetWidth` + `WrapAtWordBoundaryOrAnywhere` 且关闭横向滚动条；
  不要为了 HEX 对齐改回 `NoWrap`（R-087/L-119）。
- 界面状态只能走 `ui/settings_store.py`（INI 文件）；**不要再用 `QSettings(org, app)`**，那会写注册表并让便携包继承旧配置（R-088/L-121）。
- 键盘快捷键判定用 `QKeySequence.StandardKey`，`QKeyEvent` 没有这个属性（L-122）。
- 不要在 `OmniWindow` 上覆盖 `event()`：曾导致整套验收在退出时崩溃（L-123）。
- 批量选择用行内独立的 `pick` 框（`set_batch_mode`），不要复用「启用」开关；判断行是
  否可见要走 `row_reachable()`（折叠分组里 `isHidden()` 为假，L-124）。
- 批量改动走 `CommandStore.move_many/remove_many`，不要循环调用单条 API（L-126）。
- 发送框的 ↑ 历史只在光标位于首行时触发，多行命令必须先能在框内移动光标（R-090）。
- 测试套件必须用自己的数据目录（`OMNICOM_DATA_DIR` → `.artifacts/test-data/…`），
  绝不允许写进仓库的 `omnicom-data/`；否则真实命令库会被污染、套件也不再可重复（L-116）。
- **`QCheckBox.stateChanged` 交出来的是纯 `int`，不是 `Qt.CheckState`**：`2 == Qt.Checked` 是
  **False**，`int(Qt.Checked)` 还会抛 `TypeError`。凡自己接 `stateChanged`（而不是 `toggled`）
  的地方必须先 `value = Qt.CheckState(state)` 再比较枚举，否则症状是「勾了没反应」且毫无报错（L-158）。
- **`QColor` 不解析 CSS 的 `rgba(r, g, b, a)`**：它返回 `isValid() == False` 的无效颜色，Qt 于是
  按纯黑画。QSS 里写 `rgba()` 合法，但交给 `QColor` 的令牌必须是 `#RRGGBB` + `setAlpha()`（L-159）。
- 命令库落盘必须走 `_library_lock` + `tempfile.mkstemp` + `_merge_with_disk()`：固定名 `.tmp` 会在
  两个窗口/进程同时保存时互相覆盖，整份写回会把别人排好的顺序压掉（L-157）。
- 读失败不能静默 `_seed()` 重建：坏文件改名 `*.bad-<stamp>` 并设 `load_warning`，界面要提示一次。
- 批量选择的范围是 `_scope`（`None` = 全部命令），"全不选"永远清空全部；判断行是否可见仍走
  `row_reachable()`（折叠组里 `isHidden()` 为假，L-124）。分组垃圾桶必须接已有的
  `remove_group()`，不要另做一个确认框（用户明确要求与右键一致，R-137）。
- GUI 自测里同一控件连续多段断言时，**必须显式复位上一段留下的模式状态**（历史浏览位置、
  拖动标记、批量模式），否则新功能会以「旧检查失败」的形式出现（L-160）。
- 窗口阴影只能**画进窗口自己让出的边距环**里（`theme.WINDOW_SHADOW_PX` 由
  `_apply_window_shadow()` 加进根布局边距）：半透明无边框顶层窗口既拿不到 DWM 阴影，
  `QGraphicsDropShadowEffect` 也不会被合成（R-139）。**`WINDOW_SHADOW_PX` 不许再调大**——
  每 1 px 吃掉 2 px 内容高度，超过 8 px 左栏就要出滚动条（R-118 / L-162）。
- 最大化/全屏必须让阴影归零并在 `changeEvent` 里重新应用 `_apply_window_shadow()`，
  否则屏幕边缘会留一条灰边。
- 画布一旦被内缩过，所有"从原点到边"的绝对坐标都要重算：`_paint_edge()` 用绝对 `(0,0)`
  时描边跑到了阴影最外层（L-161）。
- `paintEvent` / 位图构建里的 `QPainter` 必须 `try: ... finally: painter.end()`：中途抛异常会
  让进程以 `QPaintDevice: Cannot destroy paint device that is being painted` 直接死掉，
  **连 traceback 都没有**，验收只会看到一句"整体未通过"（L-163）。
- 无边框窗口没有系统标题栏，Aero Snap 得自己做：`TitleBar._snap_top()` 取**鼠标所在屏**的
  `availableGeometry()`，往下拖要留 `SNAP_RELEASE`（40 px）迟滞，否则刚吸附就被解除（R-140）。
- 不要再往分组正文末尾塞 `DropStrip`：落在最后一行下半区即可插到组尾，多出来的那条会把下一个
  分组标题往下推 19 px（R-141）；列表末尾那条 `tail = DropStrip(UNGROUPED)` **必须保留**，
  那是唯一的"拖出所有分组"落点（R-133）。
- 任何"全域像素"断言在画布变动后都要重新确认采样范围（阴影把 R-020 的纯色占比打到 90.25%，
  实际页面仍是 100% 纯色 —— 断言对象错了，不是产品坏了，L-165）。
- 量布局尺寸的探针**必须复刻 `main()` 的字体 + Fusion 样式 + `app_stylesheet()`**，否则量的是
  另一个程序的布局（无样式表的探针给出"任何厚度都不滚"的错误结论，L-164）。
