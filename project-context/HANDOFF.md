# Current handoff

Updated: 2026-11-13（issue #1~#11 → R-184..R-194；追加 #12/#13 → R-195/R-196；
吸顶条背景 → R-197；就绪栏空带 → R-186 修订 + R-199；
`ui_selftest` 785 项，`acceptance_test` 194 需求全 PASS）

## Latest State

- **第一轮（2026-11-12）**：GitHub issue #1 ~ #11 全部修复，新增 R-184..R-194。
- **第二轮（2026-11-13）**：用户对上一轮的两处结果不满意，已重做：
  - **#13 吸顶组头做错了**：上一版另画了一条「简化提示条」，用户明确说
    「不是重新换一种表达方式，是原本那个文件夹变到顶部，就是原本那个栏目，
    包括新建命令那些按钮的」。→ 改为把**真实 `GroupHeader` 对象**搬进吸顶槽，
    文件夹箭头 / 组名 / 计数 / 新增 / 编辑 / 删除 全部保留可点；离开时放回列表
    （占位条保证行不跳）。需求 **R-195**，取代 R-191。
  - **#12 拖动侧栏高度卡顿**：这次是**同步写盘**。每移一个像素就
    `QSettings.setValue("ui/sidebar_split", …)`，而写入 `window-N.ini` 会立刻
    触发整文件重写（实测 ~125 ms），落在紧接着的 `processEvents()` 里。
    → 内存里记值 + 350 ms 防抖落盘。**实测 118-132 ms/move → 10-13 ms/move**。
    需求 **R-196**（R-190 保留拖动/折叠/持久化，流畅度单独成一条）。

- **第三轮（2026-11-13）**：吸顶组头本身做对了，但**背景是一块突兀的纯白**。
  → 侧栏卡片是 `GlassFrame`（半透明白 `255,255,255,244`，有壁纸时叠磨砂玻璃），
  吸顶栏却用了不透明的 `theme.CARD`。改为 `WA_TranslucentBackground` + 自绘
  `paintEvent`，复用 `widgets.paint_glass()` 与同一个 244 alpha，只多画一条底边细线。
  需求 **R-197**。

  顺带修掉一个更要紧的隐性缺陷：吸顶槽里写过 `self.layout = QHBoxLayout(self)`，
  而 `layout` 是 `QWidget.layout()` 的方法名 —— PySide6 上赋值**不生效**，
  `self.layout.addWidget()` 抛 `AttributeError`，但它发生在 `valueChanged`
  的信号处理器里，Qt 只把异常打到 stderr、界面「什么都没发生」，测试也照样绿。
  改名 `self._box` 后吸顶才真正工作（L-257）。

- **第四轮（2026-11-13）**：就绪栏「还是太高」。关键点是**用户量的是整条空带**——
  内容与就绪栏之间的间隙 + 栏本身 + 页面底边距，实测共 **48 px**。第一版只把
  `STATUS_H` 从 22 压到 18，另外 26 px 一点没动，所以看不出变化；随后试的
  「负边距把整行上提」把文字挤变形、位置偏低（用户当场否掉）。
  → 最终只压**外侧间隙**：间隙 10 → 6、底部基础边距 8 → 4、栏定 20 px，
  合计 **38 px**（-21 %），文字仍有 2.5 px 上下余量并保持垂直居中。
  需求 **R-186 修订** + 新增 **R-199**。

  一个数字坑：`QFontMetrics.height()` 对 11 px 中文字形返回 **15**（含行距），
  不是 11。按 11 估算会把栏压到 18 px、只剩 1.5 px 余量，看着就是「挤」（L-259）。

## What changed this round (files)

- `omnicom/ui/theme.py` — `STATUS_H = 20`、`STATUS_GAP = 6`（去掉 `STATUS_PULL`，
  负边距方案已撤销）。
- `omnicom/ui/main_window.py` — `_tighten_status_gap()` 改为
  `root.setSpacing(STATUS_GAP)` + 重算底边距（不再用负 `contentsMargins`）；
  `_SHADOW_BASE_MARGINS` 底部 8 → 4。
- `omnicom/ui/main_window.py` — `_on_sidebar_split` 只更新内存并启动
  `_sidebar_save_timer`（350 ms 单次）；新增 `_save_sidebar_split()`。
- `omnicom/ui/commands_panel.py` — `StickyGroupHeader` 改为透明底 + 自绘
  `paintEvent`（复用 `paint_glass()`，与卡片同为 244 alpha）；布局字段改名
  `_box`（`layout` 是 `QWidget.layout()` 的方法名，赋值静默失效）；
  `StickyGroupHeader` 重写为**承载真实组头的槽**
  （`occupy()` / `take()` / `occupant`）；新增 `_pin_group_header()` /
  `_unpin_group_header()` / `_insert_index_of()`；`_update_sticky_header()`
  改为「移动真实控件 + 占位条保高度」，带 `_sticky_busy` 重入保护；
  `rebuild()` 开头先 `_unpin_group_header()`。
- `omnicom/ui/terminal.py` — 光标聚焦状态抽成可写字段 `_caret_focused`
  （`focusInEvent` / `focusOutEvent` 维护），`_paint_caret()` 读它，使两种外观可测。
- `tools/ui_selftest.py` — R-195（8 项，对象同一性断言）、R-196（3 项 + 流畅度取
  三次最好值）、R-190 保留 6 项；R-193 改为「接近实心墨色的像素数」区分实心/空心。
- `tools/acceptance_test.py` — `TITLES` 去掉 R-191（被取代）、加入 R-195 / R-196。
- `project-context/` — REQUIREMENTS：R-191 标 SUPERSEDED，新增 R-195 / R-196；
  LEDGER 新增 L-254 / L-255 / L-256。

## Verification (all green)

```
python -m ruff check .                    # All checks passed!
python tools/ui_selftest.py               # 785/785 项通过 / ALL GUI SELFTESTS PASSED
python tools/acceptance_test.py           # 194 需求全 PASS，1152 项检查，失败 0，uncovered 空
python tools/protocol_selftest.py         # 23/23 ALL PROTOCOL SELFTESTS PASSED
python tools/contrast_audit.py            # ALL 15 CONTRAST PAIRS >= 4.5:1
```

关键实测数字：

| 项目 | 修复前 | 修复后 |
|---|---|---|
| 侧栏分隔条拖动 | 118–132 ms/move（`splitterMoved` 未断开时） | **10–13 ms/move** |
| 吸顶组头 | 另画一条简化条 | 真实 `GroupHeader`（对象同一性断言通过） |
| 终端光标断言 | 偶发 6 像素失败 | 30 vs 1 稳定区分 |
| 吸顶条背景 | 不透明纯白 `#FFFFFF` | alpha 244 半透明白 / 有壁纸时玻璃色，与背后卡片同色 |
| 就绪栏空带 | 48 px（间隙 10 + 栏 22 + 底边 16） | **38 px**（间隙 6 + 栏 20 + 底边 12），文字居中 |

## Known open

- 无阻塞项。
- 还没跑 `make_dist --check --zip`（上一轮跑过，本轮只改了 UI 与测试，若发版前
  请按 R-083 再跑一次）。
- 版本仍是 `1.1.5`；下一轮发版时把 R-184..R-196 一起写进 `CHANGELOG.md`。

## Conventions that bit us this round (now in LEDGER)

- **L-254**：`QSettings.setValue()` 本身只要 0.03 ms，代价在**下一次
  `processEvents()`**——写 INI 会触发整个文件重写（~125 ms）。任何「每次鼠标移动
  都会跑」的回调都不得同步写设置，必须防抖。
- **L-255**：`QScrollArea` 只管理一个子控件；把吸顶栏也 `setParent(self.scroll)`
  会让两个子控件互相 negotiation size hint，进入 L-041 同款死循环（窗口卡死 +
  满屏 `propagateSizeHints()`）。吸顶栏要挂在**面板**上，按 viewport 坐标定位。
  另外 pin/unpin 改变内容高度会再次触发 `valueChanged`，必须有重入保护。
- **L-259**：用户说「太高」时先量**整条带**（间隙 + 控件 + 边距），不要只改控件高度。
  另一个数字坑：`QFontMetrics.height()` 对 11 px 中文给 15（含行距），按 11 估会压出
  1.5 px 余量、看着就挤。
- **L-260**：**不要用负边距压布局间隙**。`QSpacerItem.changeSize(0, -4)` 会被 Qt 夹成 0，
  压根不生效；给控件设负 `contentsMargins` 会把文字整体上提、上下留白不对称，
  直接被用户否掉。`QVBoxLayout` 只有一个 spacing，要局部分开只能改结构或压外侧间隙。
- **L-257**：给 `QWidget` 子类加属性时**避开 Qt 已有方法名**（`layout` /
  `width` / `parent` / `event` / `size`…）。`self.layout = QHBoxLayout(self)` 在
  PySide6 上赋值不生效，读回来仍是绑定方法，`addWidget` 抛 `AttributeError`；
  又因为它在信号处理器里，Qt 吞掉异常、界面只是「什么都没发生」，测试还是绿的。
  这类缺陷只能靠**像素断言**（吸顶条真的出现了吗）才能抓住。
- **L-258**：吸顶/浮层的底色不能写 `theme.CARD`——卡片是**半透明白 (244)**，
  还可能有磨砂玻璃。要复用 `widgets.paint_glass()` + 同一个 alpha，否则就是一块
  突兀的白条。
- **L-256**：堆栈里**不可见**的页面 `cursorRect()` 返回被裁到 y=0 的矩形，像素断言
  会量到文字；而实心块与 2 px 宽空心框在同一小块矩形里**总像素数相同**，
  只能按「接近实心墨色的像素数」区分（30 vs 1）。offscreen 下别依赖 `hasFocus()`。
