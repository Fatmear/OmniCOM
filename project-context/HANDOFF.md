# Current handoff
Updated: 2026-10-28 (v1.1.2 release: self-contained portable package)

Current objective: v1.1.2 is released as a **self-contained folder** - the shipped
`dist/OmniCOM/` carries the official CPython embeddable runtime, the trimmed Qt
bindings and `OmniCOM.pyz`, so it starts on a machine that has nothing installed.
Feature set of 1.1.x is complete: 100 MB log budget, glass panes, text-only caption
row, own rounded glass tooltip, no-exe packaging, preferences (folder / accent /
wallpaper / buffer), live search, launcher two areas, keep-awake, RX+TX capture.
Version **1.1.2**; tagged and released.
**84 requirements / 335 checks** pass automated verification.
Real hardware remains untested - never infer driver timings from DEMO runs.

Source run: `start.cmd`, `python run.py`, `python run.py --demo` (虚拟串口).
CLI: `python -m omnicom.cli …` (never spawned automatically).
Package: `python tools/make_dist.py --check --zip` → `dist/OmniCOM/` +
`dist/OmniCOM-1.1.2-win64.zip` (~98 MB folder / 40 MB zip, no installs needed).

Constraint IDs to check before acting: R-019 (no self-built exe), R-017 (no silent byte loss),
R-012 (auto-scroll), R-024 (log line shape), R-033 (bilingual), R-035 (responsive),
R-041 (instant collapse), R-049 (primary line + visible notes), R-054 (UI performance),
R-055 (nonblocking lifecycle), R-063 (launcher two areas), R-066 (capture RX+TX),
R-068 (equal side columns), R-069 (version 1.1.2), R-070 (capture folder),
R-071 (accent + wallpaper), R-072 (live search append), R-076 (no extra console),
R-077 (glass panes), R-078 (records folder), R-079 (note alignment), R-080 (1 s tooltip),
R-081 (size budget + loss count), R-082 (text-only caption row), R-083 (self-contained
package), R-084 (own rounded glass tooltip). Full list: `REQUIREMENTS.md`.

Verified in repository (2026-10-28):
- `python tools/acceptance_test.py` → **335 checks, 0 failures, 0 uncovered,
  84 requirements, ALL REQUIREMENTS VERIFIED** (`.artifacts/acceptance.json`, 2026-10-28 14:54:01),
  reproduced 3× in a row after the harness isolation fix (L-116).
- GUI suite: **286/286**, including the buffer budget, the text-only strip and the tooltip card.
- `python -m ruff check .` → All checks passed; `tools/protocol_selftest.py` → 23/23;
  `tools/contrast_audit.py` → 15/15 ≥ 4.5:1; READMEs regenerated from `docs.py`.
- `python tools/make_dist.py --check --zip` → packaged `--selftest` OK with the bundled
  runtime (`-E -s`), and a real windowed launch from the bundle stays up.

## 本轮 (1.1.2) 内容

| 反馈 | 根因 | 现在 |
| --- | --- | --- |
| 打包要能直接跑，不装依赖 | 只发 `OmniCOM.pyz`，目标机得自己装 Python/PySide6 | 包内自带 CPython embeddable 运行时 + 裁剪后的 PySide6/shiboken6/pyserial，`_pth` 钉死搜索路径 |
| 工程里要有干净的 1.1.2 | 版本号写在 `__init__.py` | 版本、README、需求表、测试断言全部同步到 1.1.2 |

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
- 测试套件必须用自己的数据目录（`OMNICOM_DATA_DIR` → `.artifacts/test-data/…`），
  绝不允许写进仓库的 `omnicom-data/`；否则真实命令库会被污染、套件也不再可重复（L-116）。
