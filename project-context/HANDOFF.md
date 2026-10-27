# Current handoff
Updated: 2026-10-27 (v0.7.0 — 右键失效、发送框可调高、快捷启动并列、去重复按钮、勾选框尺寸)

Current objective: v0.7 修「点了没反应」这一类问题。上一轮解决手感，这一轮基本全是
**接线断了 / 契约用错**：三个右键菜单项没接槽、编辑对话框构造即崩溃、重命名把参数传错位置、
图标块尺寸被样式表覆盖。功能都写好了，只是没连上或没生效。

47 条需求全部自动验证；剩下的只有真机联调。

Requirement IDs to check before acting: R-019 (never build an exe), R-017 (no silent byte loss),
R-020 (light theme + flat solid background), R-012 (auto-scroll), R-024 (log line shape),
R-026 (terminal), R-027 (find-all), R-028 (drop-downs), R-029 (mono font), R-030 (search modes),
R-031 (groups + export), R-032 (plugins), R-033 (bilingual), R-034 (Help), R-035 (responsive),
R-036 (radii), R-037 (square frame), R-038 (draggable columns), R-039 (labels + custom baud),
R-040 (single export entry), R-041 (instant collapse), R-042 (group indent + add-in-group),
R-043 (resizable composer), R-044 (no duplicated controls), R-045 (checkbox size),
R-046 (context menus work), R-047 (command dialog opens), R-048 (combo follows real port).

Verified in repository (2026-10-27, after this round):
- `python -m ruff check .` → **All checks passed**.
- `python tools/acceptance_test.py` → **171 checks, 0 failures, 0 uncovered, 47 requirements,
  ALL REQUIREMENTS VERIFIED**. Report: `.artifacts/acceptance.json`.
- `python tools/ui_selftest.py` → **ALL GUI SELFTESTS PASSED**.
- `python tools/protocol_selftest.py` → **23/23**; `python tools/contrast_audit.py` → **12/12 ≥ 4.5:1**.

## 本轮 7 条反馈 → 根因

| 反馈 | 根因 | 现在 |
| --- | --- | --- |
| 右键很多键没反应 | `LogView` 有 4 个信号，`_wire()` 只连了 1 个 | 四个全连上，「查找」会展开并聚焦搜索框 |
| 发送框想调高度 | 日志与发送框是 HBox | 改成垂向 `QSplitter`，高度存 `ui/send_h` |
| 快捷启动两个应用叠在一起 | `setFixedSize` 写在 `setStyleSheet` **之前**，样式表把它压回 58px 的 sizeHint | `sizeHint()/minimumSizeHint()` 硬返回 96×104 |
| 右键「重命名」没用 | `QInputDialog.getText` 第 4 个位置参数是 `echo`，不是文本 | 改用关键字参数 `text=` |
| 很多功能重复的按钮 | — | 去掉 关于/文档/首选项 的关闭按钮、右栏「保存视图」、启动器「刷新状态」、每行的导出 |
| 组内命令和组标题平齐 | 所有行直接进同一布局 | 组内行放进带 18px 左边距的 holder |
| 选择框太大 | 指示器 18px vs 正文 13px | 15px / 半径 5，`RADIUS_CTRL` 4 |
| 命令「编辑」按钮无效 | `CommandDialog._field()` 返回**布局**，却被 `addWidget()` 使用 → 构造即 `TypeError` | `_field()` 返回 `QWidget` |

## Next steps (in order)

1. **真机联调**：物理串口跑接收记录与 X/Y/ZMODEM（DEMO 只证明软件路径）。
2. **第二个插件**：建议「AT 脚本批量执行器」，同时验证「复制目录即安装」。
3. **插件错误可见化**：`plugin.json` 写坏目前只写日志，建议菜单项打红点。
4. 若要第三种语言，`_TABLE` 是定长 `(zh,en)` 元组，先改成按语言分字典。

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
