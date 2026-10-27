# Current handoff
Updated: 2026-10-27 (button motion, launcher windows/removal, custom baud; committed)

Current objective: buttons animate on hover/press; launcher removal and per-window binding work;
custom baud accepts real typing. Earlier fixes preserved. Version stays 0.7.0; the whole batch sits
on top of `694365c` and no executable was produced (R-019).
R-058..R-061 cover the last two turns; 61 requirements pass automated verification.
Real hardware remains untested. Do not infer driver timings from DEMO/controlled-delay tests.

Requirement IDs to check before acting: R-019 (never build an exe), R-017 (no silent byte loss),
R-020 (light theme + flat solid background), R-012 (auto-scroll), R-024 (log line shape),
R-026 (terminal), R-027 (find-all), R-028 (drop-downs), R-029 (mono font), R-030 (search modes),
R-031 (groups + export), R-032 (plugins), R-033 (bilingual), R-034 (Help), R-035 (responsive),
R-036 (radii), R-037 (square frame), R-038 (draggable columns), R-039 (labels + custom baud),
R-040 (single export entry), R-041 (instant collapse), R-042 (group indent + add-in-group),
R-043 (resizable composer), R-044 (no duplicated controls), R-045 (checkbox size),
R-046 (context menus work), R-047 (command dialog opens), R-048 (combo follows real port),
R-049 (primary-line controls + visible notes), R-050 (selection search), R-051 (local animations),
R-052 (visuals), R-053 (group visibility after rebuild), R-054 (UI performance and integrity),
R-055 (nonblocking lifecycle), R-056 (unique group names), R-057 (connected-only button colour),
R-058 (button motion), R-059 (launcher removal), R-060 (per-window launcher tiles),
R-061 (custom baud entry).

Verified in repository (2026-10-27, after this round):
- `python -m ruff check .` → **All checks passed**.
- `python tools/acceptance_test.py` → **229 checks, 0 failures, 0 uncovered, 61 requirements,
  ALL REQUIREMENTS VERIFIED**. `.artifacts/acceptance.json`: 2026-10-27 22:38:21, `ok: true`.
- GUI suite gained steps for R-058..R-061: rendered frames and click dispatch, tile context menu,
  two-window binding, and real key events into the baud field.
- `python tools/protocol_selftest.py` → **23/23**; `python tools/contrast_audit.py` → **12/12 ≥ 4.5:1**.
- `python tools/ui_snapshot.py --matrix`: zh/en at 1000/1440/1920 x 900 rendered;
  narrow/default/wide samples and enlarged refresh glyph inspected. READMEs regenerated.

## 本轮反馈与实现

| 反馈 | 根因 | 现在 |
| --- | --- | --- |
| 按钮动作生硬 | 普通按钮只做 QSS 状态瞬切 | `widgets.Button` 140 ms 悬停淡入 + 80 ms 按下 0.93 收缩 + 180 ms 回弹，仅改绘制变换，几何与点击不变 |
| 快捷启动删不掉 | 菜单按字典身份匹配，Qt 传的是副本 | 快捷方式带稳定 id，菜单按 id 删除；旧配置加载时补 id，不动路径 |
| 两个记事本只有一个图标 | 模型是「进程名→数量」，切换只取第一个窗口 | 后台枚举可见窗口，每窗口一个 HWND+PID 绑定图标并稳定排序；失效句柄只提示，不再开新进程 |
| 波特率不能自定义 | 弹窗把输入当搜索；旧绑定逐字重配串口 | 按键转交文本框，Enter/失焦/选中时提交一次；恢复值预登记，避免误提交 |

Snapshot uses explicit Windows fonts, a temporary command store and `-snapshot` settings;
it does not read or alter user commands. New GUI checks click the actual Find menu and verify
actual editor size, row identity, saved state and animation endpoints. Performance checks verify
all 1200 injected lines survive, 200 commands expand, and capped history has a visible notice.

## Performance evidence

Same machine/load: 200-command group, 20000 log lines, 12 batches of 100 received lines.
Before: `.artifacts/ui-perf-before.json`; final: `.artifacts/ui-perf.json`.
- Group action P95: 25.887 → 2.770 ms; including events/paint: 36.395 → 10.793 ms.
- 100-line RX median: 54.487 → 20.670 ms; P95: 59.866 → 22.167 ms.
- Standalone scroll median: 6.775 → 6.343 ms (small/noisy gain); main benefit is less RX blocking.
- Opaque log background trial was REVERTED (L-068); transparent viewport remains.
- Focused command: `python tools/ui_selftest.py --only performance --perf-out .artifacts/ui-perf.json`.
- R-055: 12 × 400 ms slow operations, callback max 0.6 ms; heartbeat max 22.9 ms / 506 ticks.
  Before launcher fix: 554/559 ms gaps with tasklist on main stack. Threshold remains 80 ms.
- Focused lifecycle command: `python tools/ui_selftest.py --only serial_` (8 checks).
- R-061 reverse check: re-binding per keystroke produced **7** option pushes for one typed value;
  the committed-once path produces **1**. Windows-native run confirms typing reaches the editor.
- R-060 needed a native Windows run (offscreen has no real HWND list): two windows map to two
  tiles and each activates its own handle; a stale PID is rejected. Test windows only, no user apps.

## Next steps (in order)

1. Next executable check after changes: `python tools/acceptance_test.py`.
2. Run from `start.cmd` or `python run.py --demo`; physical RX, unplug/reconnect and real peer
  X/Y/ZMODEM still require hardware. DEMO/loopback prove software only.
3. This machine used Windows Python 3.13/PySide6 6.12; reverify runtime/fonts on another machine.

## Do not do

- 不要打包 exe（R-019）。从源码跑：`start.cmd` / `python run.py [--demo]`。
- 不要在 widget 里硬编码中文：加 `i18n._TABLE` 键并 `tr()`。不要翻译用户数据。
- 不要在 `resizeEvent` 里设列宽；列宽归 `QSplitter`（L-041/L-050）。
- 不要在 0 宽时调 `setSizes()`（会被夹到最小值）。
- **不要在 `setStyleSheet()` 之前 `setFixedSize()` 一个会被样式表设 `min-height` 的控件**
  —— 样式表后应用，会把尺寸打回 sizeHint（L-053）。用 `sizeHint()` 覆写兜底。
- **新加一个 `Signal` 就必须在 `_wire()` 里连上**，否则界面表现是「点了没反应」而不是报错
  （L-052）。`ui_selftest` R-046 用 `disconnect(slot)` 抛错与否来验证连接。
- 每个 `ui_selftest` 步骤必须调 `next_()`（L-042），否则整套静默挂起。
- 折叠这类纯视觉变更不要走 `_commit()`（L-045）。
- 主行命令与按钮必须对齐，但备注须在下方可见（修订 R-049 / D-022）；勾选不重建/同步写盘。
- 分组折叠只显隐共享容器；不能跳过折叠组的命令构建，不能逐行隐藏或重启发送定时器。
- 按钮动效只能改绘制变换（`widgets.Button`），不要用改宽高或常驻定时器做动画；禁用/隐藏要复位。
- 快捷启动菜单只能按 id 找目标，不能再用 `is` 比较 Qt 信号里的字典副本（L-075）。
- 启动器图标按「窗口」而不是「进程」计数；窗口枚举必须留在后台线程（L-070/L-076）。
- 波特率只在 Enter/失焦/选中时提交一次，不能回到逐字触发串口重配（L-077）。
- Test settings: explicitly set `OMNICOM_SETTINGS_SUFFIX=-test` when the terminal carries a probe suffix.
- Offscreen Qt has no bundled fonts; load Windows fonts before judging screenshots.
- Two mis-indented test lines required user-authorised mechanical repair + `py_compile` (L-063).
- Do not pump DEMO events between synchronous clear and its assertion (L-064).
- Existing tracked Python files are CRLF; preserve them and use
  `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check`.
- PowerShell pipelines may flag harmless Qt stderr as exit 1; trust actual checks/acceptance JSON.
