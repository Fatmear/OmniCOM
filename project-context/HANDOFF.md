# Current handoff
Updated: 2026-10-27 (v0.6.0 — 交互手感与布局：折叠不再卡、左右栏可拖、直角外框、自绘品牌标记)

Current objective: v0.6 — 打开软件就能用的「手感」版本。上一轮把功能补齐，这一轮修的都是
用户实际用起来会碰到的问题：折叠卡顿、栏宽不能拖、外框圆/直混用、下拉与输入框的直角边、
流控显示变量名、串口框显示错端口、波特率不能自定义、导出按钮满屏、logo 太丑。
42 条需求全部自动验证；剩下的只有真机联调。

Requirement IDs to check before acting: R-019 (never build an exe), R-017 (no silent byte loss),
R-020 (light theme + flat solid background), R-012 (auto-scroll is the only print control),
R-024 (log line shape), R-026 (terminal mode), R-027 (find-all), R-028 (drop-downs),
R-029 (mono log font), R-030 (search modes), R-031 (groups + export), R-032 (plugins),
R-033 (bilingual, never mixed), R-034 (Help section), R-035 (responsive), R-036 (radii),
R-037 (square frame), R-038 (draggable columns), R-039 (readable labels + custom baud),
R-040 (single export entry), R-041 (instant collapse), R-048 (combo follows the real port).

Verified in repository (2026-10-27, after this round):
- `python -m ruff check .` → **All checks passed**.
- `python tools/acceptance_test.py` → **160 checks, 0 failures, 0 uncovered, 42 requirements,
  ALL REQUIREMENTS VERIFIED** (~35 s). Report: `.artifacts/acceptance.json`.
- `python tools/ui_selftest.py` → **ALL GUI SELFTESTS PASSED**.
- `python tools/protocol_selftest.py` → **23/23**; `python tools/contrast_audit.py` → **12/12 ≥ 4.5:1**.
- `python tools/ui_snapshot.py` → `.artifacts/ui.png`, columns 274 / 775 / 359 px at 1440×900.

## What changed this round

| 用户反馈 | 根因 | 现在 |
| --- | --- | --- |
| 折叠分组卡一下 | `update_group` 触发全量重建 + `os.replace` 写盘 30 ms | 只切可见性；写盘延后到关闭。**60.9 → 5.8 ms** |
| 想拖左右栏 | 三栏是 HBox + `setFixedWidth` | `QSplitter`，宽度记忆到 `ui/split` |
| 外框底部还是圆的 | 背景层 `drawRoundedRect(18,18)` | 背景层 `drawRect`，四角全直角 |
| 下拉/发送框有直角边 | 弹层与 viewport 是另一层窗口/视口 | 弹层无边框+透明；发送框 `NoFrame` + 透明视口 |
| 流控显示 `flow.none` | 翻译键根本没进表 | 补 `flow.*` 三个键，中文「无/硬件/软件」 |
| 波特率不能自定义 | 直接 `int()`，非法值抛异常 | `QIntValidator(50..12M)` + 夹取回退 |
| 重连后串口框不对 | 下拉框只由枚举写入 | `_on_status` 回写 `_sync_port_combo` |
| 每个命令都有导出按钮 | 行内 4 个图标含导出 | 行内只留 发送/编辑/删除；顶部一个导出→选择框 |
| logo 太丑 | 位图在 38 px 发糊 | 自绘 `BrandMark`（渐变圆角块 + 串口弧线），四角透明 |
| DTR/RTS 离开关太远 | `SwitchRow` 两端对齐 | 新增 `compact` 模式，标签紧邻开关 |
| 想要动效 | — | 下拉箭头 140 ms 旋转、命令折叠即时、toast 既有淡入不变 |
| 搜索清空图标像保存；刷新图标脏 | `CLEAR` 画成方框+竖线；`REFRESH` 箭头硬编码 | 清空改为「圆圈+斜杠」；刷新箭头用三角函数落在弧端 |

## Next steps (in order)

1. **真机联调**：物理串口跑一遍，验证接收记录字节数与 X/Y/ZMODEM。DEMO 只证明软件路径。
2. **第二个插件**（API 只用一个插件验证过）：建议「AT 脚本批量执行器」，同时把
   「复制整个目录即安装」的叙事走通。
3. **插件错误可见化**：`plugin.json` 写坏目前只写日志，建议在插件菜单项上打红点。
4. 若要第三种语言，`_TABLE` 是 `(zh,en)` 定长元组，先改成按语言分字典。

## Do not do

- 不要打包 exe（R-019，公司终端策略禁止自编译可执行文件）。从源码跑。
- 不要在 widget 里硬编码中文字符串：加 `i18n._TABLE` 键并调用 `tr()`。
- 不要翻译用户数据（命令名/分组名）。
- **不要在 `resizeEvent` 里无条件 `setFixedWidth/setMaximumWidth`**（L-041 死循环；
  现在列宽完全归 `QSplitter` 管）。
- **不要在 `__init__` 里对 0 宽的 splitter 调 `setSizes`**（会被夹到最小值，见 L-050）。
- 新增 `ui_selftest` 步骤必须调用 `next_()`，否则整套静默挂起（L-042）。
- 折叠这类纯视觉变更**不要走 `_commit()`**（会重建 + 写盘，见 L-045）。
