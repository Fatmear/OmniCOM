# Current handoff
Updated: 2026-10-30 (v1.1.4: CLI contract, UTF-8, port lock, batch primitives)

Current objective: v1.1.4 hardens the **CLI as an agent-facing interface**.  Every call
now writes exactly one JSON document (a failed `--expect` used to print the result *and*
an error document, so `json.loads(stdout)` failed with `Extra data: line 2`), `ok` and
the exit code carry the same verdict, stdout/stderr are forced to UTF-8, concurrent
calls on one port queue on a system file lock, and `--settle` / `--ok-if-silent` /
`--repeat` / `--ports` close the "no marker to wait for" and "0.5 Hz polling is all
startup cost" gaps.  `CommandStore` now starts every new command **unticked** (R-127),
so 依次发送 only fires what the user ticked.  Still true from 1.1.3: self-update,
offline local package, instant restore, one-button file send (R-107..R-121).
send panel.  A window that is idle asks the GitHub release API once, in the
background; a newer release lights a red dot on the menu button, can be downloaded
and staged, and the swap runs **before Qt starts** on the next launch without ever
touching `omnicom-data/` (commands, captures, settings, plugins) - R-107/R-108.
Still true from 1.1.2: self-contained folder, 100 MB log budget, glass panes,
text-only caption row, own tooltip, no-exe packaging, live search, launcher, keep-awake.
**1.1.3 is published**: https://github.com/Fatmear/OmniCOM/releases/tag/v1.1.3
(asset `OmniCOM-1.1.3-win64.zip`, 41 MB). 1.1.4 is source-complete and verified but
**not released yet** - publish it with `python tools/make_dist.py --check --zip`
then `python tools/make_release.py --publish --hide-old v1.1.3`.
(asset `OmniCOM-1.1.3-win64.zip`, 41 MB). The 1.1.2 release was turned into a
**draft**, so the release page shows only 1.1.3 - and that zip is the very file
the in-app updater downloads and the 从升级包更新 button accepts. Publish the
next one with `python tools/make_release.py --publish --hide-old v1.1.3`.
Version **1.1.4**; **124 requirements / 584 checks** pass automated verification.
Real hardware remains untested - never infer driver timings from DEMO runs.

Source run: `start.cmd`, `python run.py`, `python run.py --demo` (虚拟串口).
CLI: `python -m omnicom.cli …` (never spawned automatically).
Package: `python tools/make_dist.py --check --zip` → `dist/OmniCOM/` +
`dist/OmniCOM-1.1.3-win64.zip` (~99 MB folder / 41 MB zip, no installs needed).
Screenshots: `python tools/make_screenshots.py` -> `docs/images/*.png`.
Manual: `omnicom/docs.py` (18 chapters) -> `python tools/make_readme.py`.


Constraint IDs to check before acting: R-019 (no self-built exe), R-017 (no silent byte loss),
R-012 (auto-scroll), R-024 (log line shape), R-033 (bilingual), R-035 (responsive),
R-041 (instant collapse), R-049 (primary line + visible notes), R-054 (UI performance),
R-055 (nonblocking lifecycle), R-063 (launcher two areas), R-066 (capture RX+TX),
R-068 (equal side columns), R-069 (version 1.1.3), R-070 (capture folder),
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
release zip).
Full list: `REQUIREMENTS.md`.

Verified in repository (2026-10-30):
- `python tools/acceptance_test.py` → **584 checks, 0 failures, 0 uncovered,
  124 requirements, ALL REQUIREMENTS VERIFIED**.
- GUI suite: **444/444**, including the update flow, instant restore, the one-button
  file send, the long-line wrap and the unticked-by-default command row.
- CLI contract probes: one document even on failure, exit code == `ok`, UTF-8
  stdout, `--settle 0.2` returning in 0.29 s instead of 3 s, a held lock failing
  after its timeout, and 5 samples in 0.86 s versus 2.22 s for 5 separate calls.
- `python -m ruff check .` → All checks passed; `tools/protocol_selftest.py` → 23/23;
  `tools/contrast_audit.py` → 15/15 ≥ 4.5:1; READMEs regenerated from `docs.py`.
- `python tools/make_dist.py --check --zip` → packaged `--selftest` OK with the bundled
  runtime (`-E -s`), and a real windowed launch from the bundle stays up.

## 本轮 (1.1.4) 内容

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

## 本轮 (1.1.2) 内容

| 反馈 | 根因 | 现在 |
| --- | --- | --- |
| 打包要能直接跑，不装依赖 | 只发 `OmniCOM.pyz`，目标机得自己装 Python/PySide6 | 包内自带 CPython embeddable 运行时 + 裁剪后的 PySide6/shiboken6/pyserial，`_pth` 钉死搜索路径 |
| 工程里要有干净的 1.1.2 | 版本号写在 `__init__.py` | 版本、README、需求表、测试断言全部同步到 1.1.2 |

## 本轮 (UI round, 2026-10-28)

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
