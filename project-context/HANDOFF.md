# Current handoff

Updated: 2026-11-13（issue #1~#11 → R-184..R-194；追加 #12/#13 → R-195/R-196；
吸顶条背景 → R-197；就绪栏空带 → R-186 修订 + R-199；通知堆叠 → R-200；
HEX 时间戳 / 可选列 / 不丢半行 → R-201..R-203；边缘拖动缩放 → R-204..R-206；
`ui_selftest` 817 项，`acceptance_test` 201 需求全 PASS）

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

- **第五轮（2026-11-14）**：通知弹窗堆叠方向与高度。
  - 方向：原来**新的往上加**（最老的压在下面），用户要的是**新的在最下面、旧的往上推**。
  - 高度：连发 12 条时整叠 **768 px = 85 % 窗口高**（用户说"快 3/4 屏幕"），
    期望"比一半少一点"。
  - 现在：最新固定在底部，旧的逐张上推，整叠上限 `TOAST_STACK_FRACTION = 0.45`；
    超出时**挤掉最老的一条**。实测 900 px 窗口保留 6 条、跨度 371-396 px（41-44 %），
    720 px 窗口自适应为 5 条 / 270 px（37 %）。

  两个坑：① 布局阶段**不能做动画**——`move_to()` 是 190 ms 的 `QPropertyAnimation`，
  连续 notify 时上一张还在动画途中，读 `pos()` 会拿到半途值，卡片间距变成 27/26/17/14/4/0，
  最后两条完全重叠；改成按”底部往上逐张累加”算出的固定 y 落位，只有新到的那张滑入。
  ② 上限判据曾用递减的运行总量，弹掉一条后与新的上限对不上，
  结果 12 条全留着；改为每轮从活列表重算总和（L-261）。

- **第六轮（2026-11-14）**：HEX 视图三处问题一起报上来。
  * **未填满的最后一包没有时间戳**：尾行走 `set_pending()` 只插裸文本，前缀是
    `append_line()` 加的，所以「补满那一刻才出现时间戳」。→ `set_pending(stamped=True)`
    让尾行用正文格式，并用**同一个 `compose_line()`** 拼前缀（R-201）。
  * **`000000f0` 偏移量不该默认显示**、**`|.U:|` ASCII 列要可选且默认关闭** →
    两个独立开关，默认关；打开后**历史按新列重排**（保留字节与时间，不清空日志）（R-202）。
  * **「每次发都有 00000180…，但收又不显示这个」**：真根因是
    `LogView.append_line()` **开头就** 清 pending —— 任何在 HEX 行未凑满 16 字节时
    写入的行（TX 回显 / 系统提示 / 下一包）都会把那半行**从视图里抹掉**，静默丢字节。
    → 在 `_log_line()` 入口加闸：写非 RX 内容前先 `settle_hex_partial()` 把半行结算成
    正式行；字节仍留在重放缓冲里，所以 16 字节对齐与偏移量照旧连续（R-203）。

  顺带：两个新开关曾让**左栏溢出 44 px**（R-118 不允许滚动），改为**仅在 HEX 模式打开时
  显示**；`row_hex_offset` / `row_hex_ascii` 也补进了 `retranslate()`，否则英文界面残留中文（R-033）。

- **第七轮（2026-11-14）**：无边框窗口**完全不能拖动边缘改变大小**。用户反馈了**三次**
  （「要支持非全屏时鼠标拖动」「还是没法拖动」「都没有那个箭头」），前两次我判错了，真相有三层：

  1. **只应答 `WM_NCHITTEST` 完全不够。** `Qt.FramelessWindowHint` 的窗口是 `WS_POPUP`
     且**没有 `WS_THICKFRAME`**，而 Windows 只对带该样式的窗口做缩放命中测试 ——
     真实鼠标移动时我的处理器被调用 **0 次**。
     诊断的坑：`SendMessage(hwnd, WM_NCHITTEST, …)` 能得到正确答案（**假阳性**），
     必须用 `SendInput` 模拟真实鼠标移动才看得见。→ 补 `WS_THICKFRAME`，
     再用 `WM_NCCALCSIZE` 把客户区撑满，外观仍无边框（R-205）。
  2. **命中正确 ≠ 有箭头。** `WM_SETCURSOR` 才是决定光标形状的消息，而 **Qt 自己应答它
     并返回 TRUE**，系统根本没机会画。→ 自己用 `GetCursorPos()` +
     **同一个** `_resize_hit_test()` 取区域，再 `SetCursor(LoadCursor(IDC_*))`（R-206）。
     两个子坑：让默认处理器画时形状全错（左边给 `SIZENWSE`）；
     `WM_SETCURSOR` 的 `lParam` **不是坐标**（低字是 `HT*`、高字是鼠标消息），
     当成点去读会让所有位置落到同一个角。
  3. **`setMinimumSize()` 只管 Qt 侧**，原生拖动走 `WM_GETMINMAXINFO`，
     必须自己填 `ptMinTrackSize`；而 `ctypes.wintypes` **没有** `MINMAXINFO`，
     引用它会静默掉进 `except` ⇒「写了但没生效」（L-266）。

  最终真实窗口实测：9 个位置光标全部正确（左右 `SIZEWE`、上下 `SIZENS`、四角对角箭头、
  中心 I 形），`SendInput` 后台处理器被调用，客户区等于窗口矩形。

## What changed this round (files)

- `omnicom/ui/widgets.py` — `Toast.move_to(point, animate)`；`play_in()` 不再用
  `pos()` 覆写目标位，`_home` 始终是**目标**位置（L-261）。
- `omnicom/ui/main_window.py` — `notify()` 重写为「新在最下 + 旧的往上推 + 上限挤掉最老」；
  新增 `TOAST_STACK_FRACTION / TOAST_GAP / TOAST_BOTTOM` 与 `clear_toasts()`；
  `closeEvent` 里清理通知，避免淡出时闪一下。
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
python tools/ui_selftest.py               # 817/817 项通过 / ALL GUI SELFTESTS PASSED
python tools/acceptance_test.py           # 201 需求全 PASS，1184 项检查，失败 0，uncovered 空
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
| 通知堆叠 | 新的在上，12 条占 768 px（85%） | **新在最下**，上限 45%，900 px 窗口保留 6 条 / 382 px（42%） |

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
- **L-261**：**异步动画期间不要读 `pos()` 做布局**。`move_to()` 是 190 ms 的
  `QPropertyAnimation`，连续触发时上一张还在动画途中，`pos()` 是半途值，卡片间距变成
  27/26/17/14/4/0 并出现两条完全重叠。改为自己维护目标位、按算出的固定 y 一次性落位，
  只给新到的那张做滑入。上限判断也别用递减的运行总量——弹掉一条后就与上限对不上了。
- **L-262**：`sum(高度 + 间隙)` 与用户看到的**首尾像素跨度**差一个间隙；判"占屏幕多少"
  要按视觉跨度或"卡片数 × 间隙"算，否则会稳定超出目标一点点。
- **L-267**：只应答 `WM_NCHITTEST` 没用——没有 `WS_THICKFRAME` 时系统不做缩放命中
  测试，处理器在真实鼠标移动中 **0 次**被调用。**别用 `SendMessage` 验证消息处理**，
  它绕过真实输入路径会产生假阳性；要用 `SendInput`。
- **L-268**：命中正确也**没有箭头**，因为 Qt 自己应答 `WM_SETCURSOR` 并返回 TRUE。
  另外 `WM_SETCURSOR` 的 `lParam` **不是坐标**（低字 `HT*`、高字鼠标消息）。
- **L-269**：R-204 第一次的测试是**假阳性**（直接调函数当然对）。验收要站在用户那侧：
  断言窗口样式、客户区尺寸、真实输入调用次数、光标句柄。
- **L-265**：无边框窗口没有系统缩放边；正确做法是应答 `WM_NCHITTEST` 返回 `HT*`，
  把尺寸手势交回系统，别在 Qt 里自己实现。`lParam` 里是**屏幕坐标**，
  `mapToGlobal` 之后不要再 `ScreenToClient`（偏移会翻倍）。
- **L-266**：`setMinimumSize()` 只管 Qt 侧；原生拖动走 `WM_GETMINMAXINFO`，
  必须自己填 `ptMinTrackSize`。另外 `ctypes.wintypes` **没有** `MINMAXINFO`，
  引用它会抛 `AttributeError` 被 `except` 吞掉 ⇒「写了但没生效」，要自己定义结构体。
- **L-263**：`set_pending()` 只插裸文本，时间戳是 `append_line()` 的前缀 ——
  所以「最后一包没时间戳、补满后才出现」。修法不是各拼一遍，而是**共用
  `compose_line()`**。
- **L-264**：`LogView.append_line()` **开头就清 pending**，于是任何在「HEX 行未凑满
  16 字节」时写入的行都会把那半行**静默抹掉**（TX 回显 / 系统提示 / 下一包）。
  凡是「pending/占位块」与「正式追加」共用一个控件时，追加路径必须先结算占位块。
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
