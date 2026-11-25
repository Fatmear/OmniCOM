# Ledger detail, 2026

Verbatim detailed write-ups moved out of `LEDGER.md` when the
ledger became a one-line index. Every section keeps its original `L-xxx` id.

## L-270 行式控制台丢了光标移动，所以"左右键会删字符"
- **症状**：用户在终端里按左右键，字符被删除（不是移动）。
- **根因**：旧 `TerminalView` 是**行式**控制台——`\r` 回列 0，`\n` 开新行，
  **每一个 CSI 光标移动都被吞掉**（注释写的就是 "everything else in a CSI
  sequence is swallowed"）。设备（如 `sh` 的行编辑器）回显 `ESC[D` 时，本地
  `_col` 没动，下一个字符写到已占用的格子，`_write` 便 `cursor.deleteChar()`。
- **修法**：换成真正的**网格模型**（`omnicom/ui/vt100.py`）：`Screen` 持有
  `rows x cols` 的 `Cell` 缓冲 + 光标 + 样式 + 滚动区域 + 回滚，`TerminalView`
  只负责画。输入只发设备，屏幕由回显驱动，与硬件终端一致。
- **判据**：`ESC[D` 两次后写 `X`，必须得到 `aXc`，不是 `ac`。

## L-271 终端接收一批 1100 字节曾要 6.8 ms，两处分配是主因
- `_scroll_up()` 每换行都 `[Cell() for _ in range(cols)]`：一次 2 400 行突发
  要新建约 19 万个 dataclass，占 pitch。改成**行池**（`_spare_rows`）原地
  `blank()` 复用；`Cell` 加 `slots=True` 去掉 `__dict__`；`cell_width()` 加
  `lru_cache`（每个可打印字符都调，`unicodedata` 两次查表）。
- 绘制同理：每格 `QFont(self._font)` + `QPen` 新建，一屏约 2 000 格。改为
  `_font_for(style)` 缓存 bold/italic 四种组合，颜色也 `_colours` 缓存。
- 实测：解析 6.8 ms/批 → 4.5 ms/批，`feed+repaint` median 40.6 → 26.3 ms。

## L-272 `styled.widget.style()` 可能为 None，行直接不画
- `SearchDelegate.paint()` 里 `styled.widget.style().drawControl(...)` 没有
  判空。`QStyleOptionViewItem.widget` 是**可空**的，一旦为空就抛
  `AttributeError: 'QWidgetItem' object has no attribute 'drawControl'`，
  整行不绘制且异常被 Qt 吞掉（R-001 整体失败里唯一的具体线索）。
- 修法：`host is not None and hasattr(host.style(), "drawControl")` 才调用。

## L-273 性能门禁（R-054/R-055）会被整机负载带偏，必须 A/B 对照
- 本机同时跑 `ui_selftest`（8 万次检查量级）时，`log_scroll` / `rx_batch` 的
  p95 常在 40-105 ms 之间跳；`R-055` 心跳还会出现 200-500 ms 停顿。
- **判定方法**：`git stash` 掉本次改动再跑同一项，基线若同样超标，就不是本次
  改动引入的。实测基线 `rx_batch` p95 68.0 ms、`R-055` max 318 ms，与改动后
  同量级 ⇒ 环境问题，不得据此改预算（预算 40 ms / 200 ms 保持不动）。

## L-274 终端 "只能复制整行" 是两个 bug 叠加，而复制本身是对的
- **症状**：在终端里拖选几个字符，看起来整行都选中了，用户判断"只能复制整行"。
- **根因一（显示）**：`_paint_row()` 用 `_row_in_selection()` 判断"这一行在不在
  选区范围内"，然后 `fillRect()` 铺满**整行宽度**。所以哪怕只拖了 4 个字符，屏幕
  上也是一条通栏高亮。复制出来的文本其实是对的（`'3456'`），但没人会相信。
  → 改为 `_row_selection_cols()` 返回 `(first, last)` 列区间，**高亮与
  `selected_text()` 用同一个函数**推导，两者不可能再不一致。
- **根因二（手势）**：`mouseReleaseEvent()` 无条件把 `_sel_active` 设成松手位置，
  于是双击选中的词在松手瞬间被压回一个格子；`Shift+点击` 也因为没有锚点而退化成
  新的单格选区、再被"空点击清空"逻辑抹掉。
  → 按 `_sel_mode`（`char`/`word`/`line`/`extend`）分别处理 release；新增
  `_caret_cell` 记住上一次落点，Shift+点击从那里扩选（xterm 行为）。
- **根因三（Qt 没有三击事件）**：第三个点击是普通 `MouseButtonPress`。用
  `_count_click()`（时间窗 + 4 px 容差）自己数；双击的 press 会**先**于
  `mouseDoubleClickEvent` 到达，所以词选必须在 press 里就完成。
- **判据**：拖 3..6 列 → 复制 `'al 1'`，且高亮像素宽度 == `4 × cell_w`（实测
  28 px / 28 px）。单纯点击必须 `highlight_span() == (-1, -1)`。

## L-275 终端选区还有四个易漏的一致性细节
- **宽字符**：CJK 占两格，选区端点落在续格上会切掉半个字。`_snap_cols()` 把端点
  向外扩到整字边界，否则复制文本会静默少一个字。
- **行尾填充**：终端每行都补空格到整宽，复制必须 `.rstrip()` 掉（xterm 也这样），
  但**不能** strip 左侧——从行中间开始选就该从那个字符开始。
- **反向拖选**：`_selection_range()` 取 min/max 即可，但裁剪每行时仍要按
  "首行从 c1、末行到 c2、中间行整行"的规则，否则倒着拖会得到镜像的后半段。
- **`.strip()` vs `.rstrip()`**：中间行原本用 `.strip()`，把行首空格也吃掉，
  多行复制时缩进会丢；每行只 `rstrip()`，最后再统一去掉末尾空行。

## L-276 `mouseReleaseEvent` 会破坏双击/三击的选区（Qt 事件顺序）
- 一次双击在 Qt 里是：`Press` → `Release` → `DblClick` → `Release`。
  如果 `Release` 里无条件写 `_sel_active = _cell_at(pos)`，紧跟在 `DblClick`
  后面那个 `Release` 会把整词的选区打回一个格子。
- 修法不是"猜是不是双击"，而是**把选区模式存下来**（`_sel_mode`），在
  `Release` 里按模式决定语义。三击同理：`line` 模式下 release 不改选区。

## L-277 R-055 在本机稳定失败，用 `git stash` 单次对比会得出错误结论
- 现象：`R-055 慢驱动开关期间界面心跳无超过 200 ms 的停顿` 反复失败，
  实测 max 在 **148 / 261 / 295 / 316 / 333 / 385 / 411 / 677 / 932 / 1047 ms**
  之间大幅跳动。
- **我踩的坑**：先做了一次 `git stash push -- terminal.py ui_selftest.py`，
  跑出基线 148 ms **PASS**，于是判定"本轮终端改动引入了回归"。这是**错的**：
  单次采样在这台机器上完全是噪声。改成各跑 4 次后，**基线也是 0/4 通过**
  （305/339/305/385 ms），与新版（295/333/932/1047 ms）同量级。
- **插桩证据**：在 R-055 流程里给 `TerminalView.feed` / `paintEvent` 计数，
  12 次开关期间 **feed 0 次、paint 0 次**，而心跳仍有 677 ms 停顿 ⇒
  停顿与终端无关。堆栈只到 `<module>`，说明卡在原生/事件循环层。
- **所以**：判定"是不是我改坏的"必须①多次采样比分布，②用插桩证明代码路径
  是否真的被执行。**单次 A/B 在这类时序指标上没有任何说服力**（L-273 的升级版）。
- 预算 200 ms 与 40 ms 都不动：问题在测量环境，不在产品。

## L-278 `_flush_rx` 里有个 `if console:` —— 终端页不可见时它根本不解析

- 代码是：`console = self.console.isVisible()` … `if console: self.console.feed(data)`。
  也就是说**用户坐在日志页时，终端网格完全不更新**。这在纯 UI 场景没问题（看不见
  就不用画），但一旦要让 CLI 读屏幕就致命：`cli screen` 会在用户没点终端页时返回空白。
- 这也顺手解释了 R-055：那个步骤全程不在终端页，所以 `TerminalView.feed` /
  `paintEvent` 被调用 **0 次**（已插桩证实，L-277）。
- **修法**：`TerminalPage` 持有一个**常驻镜像** `Screen(24, 80)`，由 `_on_rx` 无条件
  喂字节，与页面可见性无关；`TerminalPage.feed()` 同时喂可见视图与镜像。
  宁可多花一次解析（1100 字节约 4.5 ms/批，且已优化），也不要让 CLI 看到空屏。

## L-279 CLI 的窗口命令有两条纪律：白名单要显式扩展，`_control_send` 只吃 hex

- `_emit_window()` 只复制**固定白名单**的字段，新增命令的返回值会被静默丢弃
  （第一次接 `screen` 时 `rows`/`cols`/`cursor` 全没了）。加了 `keep=` 参数显式列出，
  比把白名单越拉越长更可控。
- `_control_send()` 的参数是 `hex=`（不是 `data_b64`），并且**不追加任何行尾**。
  `keys` 正好可以直接复用它 —— 这正是 `send`（面向行，追加 CR/LF）做不到的事。
- 写端到端测试时，**CLI 必须跑在别的线程/进程里**：窗口在自己的 GUI 线程处理请求，
  在 GUI 线程里 `subprocess.run` 会死锁，表现为"窗口繁忙/超时"而不是报错。

## L-280 「按删除键乱行」是两个独立缺陷叠在一起
- **缺陷一：字节被解析了两次。** `_on_rx` 喂 `page.mirror`，`_flush_rx` 又
  `if console: console.feed(data)` 喂 `view.screen`。两份网格各自演进，只要一方
  漏掉一个 chunk 就永久漂移 —— 屏幕显示出重复/错位的字符。
  → 改成**一份屏幕**：`TerminalView.attach_screen(mirror)`，视图只画不解析；
  字节唯一入口是 `TerminalPage.absorb()`（由 `_on_rx` 无条件调用）。
- **缺陷二：`~` 序列被整类丢弃。** `_apply_csi` 的分支只处理字母 final
  （`A`..`u`），而 **`ESC[3~`（Delete）/`ESC[1~`（Home）/`ESC[4~`（End）/
  `ESC[5~6~`（PageUp/Down）的 final 是 `~`**，全部落进末尾那句
  "Everything else is a mode/query we can safely ignore."。设备已经删了字符，
  屏幕没删，下一次回显就落在错位的列上。
- **判据**：`abcdef` + `ESC[D` + `ESC[3~` 必须得到 `abcde` 而不是 `abcdef`。

## L-281 `0x7f` 不是"控制字符"，在串口上它就是退格
- `_consume_char` 原来写 `if char == "\x7f": return index + 1` —— **直接丢弃**。
  真实终端里 DEL 确实不算编辑操作（由 shell 处理并回显），但**很多设备/裸
  `getchar()` 循环会发 0x7f 表示"删掉上一个字符"且自己不回显**。丢掉它 = 屏幕多一个
  字符，之后整行右移一列，看起来就是乱行。
- 修法：退回一列并擦掉该格（光标停在原处，与 xterm 一致），同时清 `wrap_pending`。

## L-282 `ESC[4~`（End）与 `ESC[4h`（IRM）不能共用一个 `first == 4`
- 我第一版在 `_apply_tilde` 里写 `elif first == 4: self.insert_mode = False`，
  结果 End 分支 `elif first in (4, 8)` **永远不可达** —— Home 之后按 End 毫无反应，
  而单独按 End 却"看起来正常"（光标本来就在行尾）。是个只在这种顺序下暴露的 bug。
- 正确分工：`ESC[n~` 只处理"编辑键"语义（2=Insert, 3=Delete, 1/7=Home,
  4/8=End, 5/6=PageUp/Down, 200/201=bracketed paste）；IRM 属于
  **`ESC[4h` / `ESC[4l`**，放进 `_apply_mode` 的 `not private and code == 4`。
- 教训：**编号参数在不同 final 下含义完全不同**，同一个数字绝不能跨 final 复用。

## L-283 `view.reset()` 只清选区、不清屏，会让老测试悄悄失效
- R-212 把屏幕所有权移到 `TerminalPage` 之后，`TerminalView.reset()` 如果不擦屏，
  就变成"重置选区"而不是"重置终端"，R-209/R-210 立刻大面积失败
  （`selected_text()` 返回 `''`，因为屏幕里还留着上一步的内容）。
- 修法：`reset()` 在**原地** `erase_in_display(2)` + 清 `scrollback` + 光标归零，
  不新建 `Screen` —— 新建会让窗口继续画旧网格、CLI 读新网格。

## L-284 「清除窗口」绑死 `clear_log`，在终端页等于没反应
- `btn_clear.clicked.connect(self.clear_log)` 是写死的。用户坐在终端页时按它，
  清掉的是**看不见的日志**，终端纹丝不动 —— 从用户角度就是「这个按钮坏了」。
- 修法：`clear_window()` 读 `current_page()`，`console` 页清终端、其余清日志。
  判据必须两边都测：终端页清完 **log 仍在**，日志页清完 **终端仍在**；
  只测「清掉了」会漏掉"清错对象"。
- 同类风险：任何"动作按钮"接到具体视图前，先问一句"它在别的页上应该做什么"。

## L-285 `ESC[2J` 不清光标，所以清屏后输出从半行开始
- `TerminalView.clear()` 原来只有 `self.screen.erase_in_display(2)`。
  `ESC[2J` 的语义是**只擦字符、不动光标**（真正的终端也是靠
  `ESC[2J ESC[H` 两件事才回到左上角）。
- 于是右键清空后光标停在 `(1, 8)`，下一行输出从第 8 列开始 ——
  用户看到的就是「光标不是从头开始，而是接着清空前的位置」。
- 修法：`erase_in_display(2)` 之后显式把 `cursor_row/cursor_col` 归零、
  清 `wrap_pending`、清选区与回滚偏移。
- 通用教训：凡是"看起来清干净了但位置不对"的 bug，先查**光标是不是独立的第二件事**。

## L-286 `Screen.resize()` 只拷左上角矩形 = 折行内容被截断 + 光标错位
- 旧实现：建一张 `rows x cols` 的新网格，把 `min(rows, old.rows) x min(cols, old.cols)`
  拷过去，然后 `cursor_col = min(cursor_col, cols-1)`。
- 对于**软折行**（每个 shell 都有，我们自己的 `_put` 也是）的屏幕，这意味着
  缩窄时**第 0 行第 cols 列之后的内容直接消失**，而光标还留在原来的行号上 ——
  于是下一次回显/上键/Delete 落在完全不相干的格子，屏幕看起来"折行显示到了前半行"。
  实测 80 列写 100 字符再缩到 40 列：后 60 个字符被删，光标仍停在 `(1,20)`。
- **修法（reflow）**：① 把每行**解包**成逻辑行（去掉续格、去尾部空格）；
  ② 用 `mapping[(src_row, src_col)] -> (new_row, new_col)` 边重排边记光标；
  ③ 光标若在"行尾之后"（未写入的空格），回退到该逻辑行最后一个字符的位置；
  ④ 只在**光标会被挤出底部**时才从顶部丢行进 scrollback，否则**尾部多出来的行
  是折行产生的空白，直接丢弃**。
- 第 ④ 步踩过一次坑：一开始无条件把 `new_grid[:overflow]` 推进 scrollback，
  结果缩窄后 `$ ` 提示符被推到历史里，屏幕从 `yyyy` 开始 —— 内容其实没丢，
  但用户看到的是"行首不见了"。

## L-287 终端 reflow 的正确性判据
- **不能只断言"没崩"**：截断后的屏幕同样不崩。要同时断言
  「字符总数不变」+「首行以提示符开头」+「内容连续」+「光标变了坐标但指向同一字符」。
- **上/下键与 Delete 必须作为 reflow 的下游验证**：它们是"错位"最先暴露的地方
  （上键少一行、Delete 删错位置）。只测文本不测按键，会漏掉光标映射的 bug。
- **宽字符要单独测**：CJK 占两格，reflow 时端点若落在续格上会把字切开，
  复制/显示都会出现替换符（`\ufffd`）。
- 又一次被自己的探针期望骗到：第 4 步我让光标站在行尾空白处按 Delete，
  认为"应该少一个字符"，其实**在空处按 Delete 本来就是无操作**。
  测按键行为前先确认光标下面有没有东西。

## L-288 手画的滚动条不是控件 —— 必须自己实现命中/拖动/跳转
- `_paint_scrollbar()` 只做了 `drawRoundedRect`：**没有任何输入处理**，所以鼠标在上面
  按下等于点正文（结果是被当成选区的起点）。用户说的「滚动条无法拖动」就是这个。
- 修法：把几何抽成 `_scrollbar_geometry() -> (track, thumb)`，**绘制与鼠标共用**这一份
  （两处各算一遍必然漂移）；再补 `_scrollbar_hit()` / `_set_scroll_from_thumb()` /
  `mousePress|Move|Release` 的拖拽分支。
- 两个细节：
  * **命中区要比滑块宽**。滑块只有 4 px，严格按像素命中几乎抓不住 → 向两侧各放宽 4 px。
  * **滚动条优先于选区**。否则在滑块上按下会开始一段文字选区，松手后留下一条高亮。
    press 里先判 `_scrollbar_hit()`，命中就清空选区并进入拖拽态。

## L-289 点击轨道应该"跳过去"，不是"翻一页"
- 我第一版写的是 `_page_scroll()`（按 `screen.rows - 1` 翻页）。问题：如果点在滑块
  附近，翻一页几乎看不出变化，用户会以为"点了没反应"。
- 真实滚动条（Windows/macOS/xterm）的行为是**把滑块中心移到指针处**，然后可以继续拖。
  改成 `_scroll_drag_offset = thumb.height() / 2` + `_set_scroll_from_thumb()`，
  一次点击就能跳到任意位置，且手指不松就能接着拖。
- 自测时也踩了一次：我在 `track.top()+2` 处点击，那里**正是滑块本身**（我们是实时状态，
  滑块在顶部），于是走了 thumb 分支、`_scroll_off` 当然不变。点轨道要挑**滑块之外**的位置。

## L-290 用真值判断"要不要算"，等于把最特殊的那个值留给默认值
- 滑块位置原本写成：
  ```python
  position = 0.0
  if self._scroll_off:                       # ← 短路发生在最常见的那个值上
      position = travel * (1 - self._scroll_off / maximum)
  ```
  `_scroll_off == 0` 是**实时屏幕**（用户 99% 时间都停在这里），却被 `if` 跳过，
  `position` 保持初值 `0.0` ⇒ 滑块画在**顶部**。表现就是「拉到底后滑块跳到最上面」。
- 修法：**取消短路**，把 `0` 当成正常输入参与运算：
  `position = travel * (1.0 - min(1.0, self._scroll_off / maximum))`。
- 规律：当某个变量的"零值"本身是**有意义且最常见**的状态时，绝不能用
  `if value:` 当作"是否初始化"的代名词。要么用 `is None`，要么直接算。
  同类陷阱：`if self._scroll_off:` / `if self.count:` / `if self.offset:`。

## L-291 滚动条的几何自检要覆盖"两端 + 单调性"
- 只断言"能滚动"或"有变化"抓不到这类 bug：`_scroll_off` 从 38 变到 0 时滑块确实
  移动了（从 218 跳到 4），看起来"有反应"，只是方向错了。
- 可靠的三组判据：
  ① **端点**：`_scroll_off == 0` → 滑块贴轨道**底**；`_scroll_off == history` → 贴**顶**；
  ② **单调**：取 4 个档位的 `thumb.top`，必须严格（或非严格）递减；
  ③ **行为**：真实拖到底后 `_scroll_off == 0` **且**再读一次几何仍在底部
  （只查 `_scroll_off` 会漏掉"状态对了但画错了"）。

## L-292 同一信号被两个地方各接一次 = 每个按键发两遍（"ls" -> "llss"）
- 终端改成插件后（R-218），`TerminalPlugin` 自己把 `page.keyBytes` 接到 `api.send`，
  而**宿主仍然保留着** `self.console.keyBytes.connect(self._on_terminal_keys)`。
  两条链路都写串口，于是每按一个键，driver 收到两个字节：`ls` 变成 `llss`。
- 为什么回归测试没抓到：R-026 原来断言 `grew == 3`，但它调用的是
  `window._on_terminal_keys(...)`（直接函数调用），**根本没走信号**，所以连接多一条
  也测不出来。测试必须走真实入口（`view.keyBytes.emit` 或 `QTest.keyClick`）。
- 判据写成"统计真正落到 driver 的字节"，并且是 `==` 而不是 `>=`：
  `>=` 对重复发送完全无感。
- 迁移任何"信号→副作用"的接线时，先 `grep` 这个信号名，确认**只有一个**连接点。

## L-293 插件化之后，宿主里那批"转发方法"必须一起删掉
- `_on_terminal_keys` 是插件化之前的发送入口。插件接管后它变成死代码，
  但**留着就会被误接**（正是 L-292 的来源）。
- 规则：迁移完成后，宿主里凡是只做"转发给旧页面"的方法都要删，而不是留着当兼容层——
  留着等于给下一个人（或下一次编辑）提供第二个接线点。
- 连带影响：`window.console` 现在是 `TerminalPlugin` 而不是 `TerminalPage`，
  测试里给 `console.mirror` 赋值会因为 `__getattr__` 转发变成只读而失败，
  要给真实的 `console.page.mirror` 赋值。

## L-294 R-055 的自测轮询与心跳同为 10 ms，会自己造出"停顿"
- R-055 用 `QTimer.singleShot(10, poll)` 轮询串口是否就绪，同时又用 10 ms
  心跳测停顿 —— 两者同频竞争，心跳经常被挤到 20 ms，偶尔叠加成 200-370 ms 的
  "尖峰"。这些尖峰**不是界面被卡住**，是测量器自己抢了事件循环。
- 把轮询放宽到 25 ms 后，尖峰明显减少（心跳仍是 10 ms，真卡顿照样能测出来）。
- **不要为了让门禁变绿而放宽 200 ms 预算**。这里的修法是让"尺子"更准，不是让
  阈值更松。

## L-295 判断 R-055 是否回归的可靠做法：把插件全部停用再测
- 三次 A/B（各 4 次采样）：
  * 插件全启用：median 208 ms，2/4 通过
  * 只停 terminal+transfer：median 167 ms，3/4 通过
  * **全部插件停用（只剩宿主）：median 191 ms，3/4 通过，max 243 ms**
- 最后一行是关键：连插件都不加载时同样会超 200 ms ⇒ 这台机器的虚拟串口枚举
  （`comports()` 走 ctypes，L-130）才是主因，与插件化无关。
- 方法要点：**必须有一个"把被测特性整个关掉"的对照组**。只比较"开/关插件"是不够的
  ——两者都可能被同一个环境噪声压住。

## L-296 「发布包不含 plugins/」这条老规则会静默吃掉新拆出来的核心插件
- 现象：1.1.6 的 zip 构建成功、678 条目、`testzip()` 为 None、打包自检
  `SELFTEST OK`，但解压后**没有** `plugins/terminal`、`plugins/transfer`。
  用户拿到的包会没有终端页和文件传输页 —— 而自检全绿。
- 根因：R-174（发布包不内置任何插件，防止更新覆盖用户自装插件）写死在三处
  **互相独立**的断言/白名单里，R-218 把终端与传输拆成插件时只改了应用代码：
  1. `make_dist.KEEP_PLUGINS` 只列 Qt 子目录（`platforms`/`styles`/…），不列我们的插件；
  2. `make_plugins.verify_runtime()` 里 `assert not (root/'plugins').exists()`；
  3. `make_dist` 的模块 docstring 明写"No `plugins/` folder is shipped"。
- 修法：**把 R-174 的作用域收窄到"商店插件"**，并新增 `CORE_PLUGINS`
  白名单（`terminal`、`transfer`），只复制、不覆盖（用户改过/升级过的保留）。
  断言也改成**点名**商店插件（`chart`、`launcher`）而不是整个目录。
- 判据：`make_dist.verify()` 在任何耗时步骤之前先断言
  `plugins/<name>/plugin.json` 存在 —— 打包少一个核心插件必须**直接失败**，
  而不是等用户报"没有终端"。
- 教训：**规则的作用域会随架构漂移而变宽**。R-174 的原意是"别覆盖用户的插件"，
  但"整个目录都不许有"是它当时的实现细节；拆插件时必须回头审一遍这条实现细节
  是否还在表达同一个意思。三个独立断言点，改一处不算改完。
- 附带踩坑：`_copy_pyside()` 里的 `source` 是 **PySide6 目录**，`plugins` 变量正是
  Qt 找 `platforms/` 的地方。把我们的插件复制塞进那个循环，会连
  `KEEP_PLUGINS` 一起搬走，打包自检立刻报
  `Could not find the Qt platform plugin "offscreen"`（L-296 的第二半）。
  Qt 插件目录和我们的插件目录**同名不同义**，别在同一个函数里处理。


## L-297 「按源单元格索引」找光标，遇到空行就必然失效
- `_reflow` 原来用 `mapping[(src_row, src_col)] -> 新位置` 跟踪光标，可靠的前提是
  「光标一定落在某个**有内容**的格子上」。这个前提不成立：
  * 光标停在**空行**上（提示符刚换行，还没输出）；
  * 光标停在**最后一个字符之后**（`"$ "` 的下一列）。
  两种都查不到条目，旧代码 `target = mapping.get(...)` 一路 fallback 到 `(0, 0)`。
- 后果：每次 resize 都把光标扔到左上角，设备下一段输出就从第 1 列开始覆盖
  ——用户看到的就是「又显示在一行」。
- 正确模型：光标在**逻辑行里的扁平下标**（相对整条未折行文本的偏移），
  用 `行内已收集的单元格数 + _cursor_flat_index(本行, 光标列)` 算出来；
  `dest[len(line)]` 专门存「文本之后」的位置。

## L-298 「这一行满了」不等于「软换行」
- 我用 `_row_is_full()`（判断整行无空格）来识别软换行，想让拉宽窗口时把
  断开的行合并回去。两个错误：
  1. **真实 shell 提示符一定含空格**（`"$ "`、`root@host:/path# `），所以
     "无空格"几乎永不成立，合并功能等于没生效；
  2. 反过来，一行**恰好写满**之后跟 `\r\n`，那是两条互不相干的输出，却被
     当成续行粘成一行（用户：「两行不同的内容叠在同一行里」）。
- 判据必须是**事件**而不是**形状**：只在 `_put` 触发右边界换行时把**目标行**
  标成 `_continued[row] = True`；`\n`/`\r\n` 的 `_line_feed(wrap=False)`
  明确清掉标记。`_scroll_up/_scroll_down/insert_lines/delete_lines` 都要同步
  位移这个并行数组，否则滚动之后会拼错行。
- 教训：**任何时候想用"格子长什么样"推断"发生过什么操作"，先问一句
  "这个形状是不是也能由别的操作产生"**。

## L-299 我擅自让 `\r` 跳到"逻辑行首"，制造了用户报的那个 bug
- 现象是「两行不同的内容叠在同一行」，我推断是 `\r` 只回到当前网格行第 0 列、
  没有回到软换行的起点，于是改了 `_carriage_return()` 去回溯 `_continued`。
- 结果更糟：设备 `\r` 重绘时新文本落在**更早**的一行，旧的尾巴留在下面的
  续行里，正好就是"两行叠一行"。
- 查 xterm 官方 ctlseqs：`CR` 是单字符控制，语义就是「移动光标到**本行**第 0 列」，
  DECAWM 折行产生的续行在终端里**仍然是独立的一行**，标准没有任何"逻辑行"概念。
- 教训：**"和主流一致"要用规范原文核对，不是靠对现象的直觉推理**。
  xterm/ECMA-48 的行为定义是权威，改之前先查。

## L-300 重排时为了"安放光标"多生成一行，会把顶行挤进回滚区
- 宽字符用例（30 个「中」，6x40 → 6x20）丢了 5 个字。原因：文本恰好填满
  6 行，光标又在"文本之后"，我 `while len(new_grid) <= row_index + 1:
  start_row(True)` 硬加了一行 ⇒ `overflow = 1` ⇒ 按规则从**顶部**丢一行
  进 scrollback。
- 正确做法：行尾之后没有位置时，光标就留在**最后一行的最后一列**，
  这恰好是真实终端"pending wrap"的状态，不需要额外行。
- 教训：**"为了放光标而增加内容"是危险操作**。光标是个指针，不是内容；
  为它多出来的任何一格，都会被后续的溢出/裁剪规则当成真实数据。

## L-301 Linux 长行/历史重绘仍错乱：字符网格尺寸与远端不一致（FIXED-verified）
- 新证据：COM30只读查询为24x80、TERM=vt220；真实长输入/清行/上历史/下历史回包，在80列重放能恢复提示符和草稿，在52或104列重放产生提示符覆盖、历史残留。此前重排合成用例不能证明实机修复。
- 修法：字符网格与可见视口分开，默认钉死80x24并用滚条查看，另给132x24/自动尺寸（R-300）。不注入stty、不做端口/设备特判（R-300）。R-214/R-297 重排用例改在显式自动尺寸模式下运行，否则默认固定后该用例会静默失效。
- 证据：`.artifacts/terminal-real-rx.json`；用户实机确认界面正常后提交。

## L-302 插入模式切换错误归零光标（FIXED-verified）
- `_apply_mode` 的非私有模式4分支多调用了 `_move_to(0,0)`，且有重复不可达分支；移除后，ESC[4h和ESC[4l单点探针均保持(0,11)。真实捕获中未见该序列，不将它冒认为本次实机错行的主因。

## L-303 刚打开的串口上按键被静默丢弃：发送守卫读了过期的状态快照（FIXED-verified）
- 现象：全量 GUI 里 R-292 偶发失败，详情为 `b'' connected=True write_is_manager=False view_focus=False tx=0`，而单跑必过。
- 根因：`_plugin_send` 判 `self._status.get("connected")`。`_status` 只在排队的 `status` 信号送达时更新，紧接在成功 open 之后仍是「未连接」，于是 `api.send` 返回 False，一个字节都没写；而 `bridge.state()` 直读 manager，答 `True`。**串口是开的，按键却丢**，属于真产品缺陷，不是测试假阳。
- 修法：守卫改用 `self.bridge.state() or self._status`（manager 是权威答案，且只是一次字典读）。
- 判据：污染顺序（R-215→R-217→R-292）下必须 `tx>0`；只放宽断言不算修复（对照 L-269/L-274 的假阳教训）。

## L-304 打开日志后跟随失效、停在终端页（FIXED-verified）
- `_show_log_text` 在 begin_batch 后 clear_all，后者将批量计数归零，end_batch 无法释放 TailFollow 更新锁，后续 follow 全部短路。清屏前移到 begin_batch 之前；真实日志页面是 terminal，不是 console。
- R-309：文件选择器仅所有文件；文件加载标记在关闭文件后保留，下一次 toggle_connection 在 bridge.open 前 clear_log 并开启自动跟随。关闭日志不立刻清屏。
- 验证：`python tools/ui_selftest.py --only log_mode_serial_lock` 14/14，包括 .capture 文件、实际页面可见、更新锁归零、重新连接清屏与三批新日志跟随。

## L-312 看板刷新卡顿（后备：懒更新）（OPEN）
- 2026-11-20 更正：用户否定此轮结果，原“渲染帧率不降、卡顿已修复”结论无效。下列42.4/34.3→2.9/1.4ms只测 setOption 提交，不是绘制完成；30轮合并为一次不能证明持续刷新。实际完成背压和负载证据见 L-316。
- 同时把仪表改成“结构只建一次、每帧只挪指针”：实测仪表本身只要 1.4 ms，所以这部分收益很小，已如实记录，不当成主因。
- 两个被实测否掉的方案（不得重试）：
  - `appendData()`：ECharts 不把追加的点反映到 `getOption()`，我们的状态检查与重同步读到旧序列，曲线直接冻住（探针复现：尾部停留 2.99 不再前进）。
  - 把 `progressive` 改成随展示帧数自适应：同一配置在离屏环境下中位在 **13 ms 与 84 ms** 之间摆动，无可复现收益，且 R-307 明确断言 `progressive == 800`，已回退。
- 测量陷阱：最初的“5 s 超时”是因为探针里 `acked.disconnect()` 无法断开 lambda，导致监听器泄漏、测量失真；改用具名槽后恢复正常。任何测确认等待的探针都要用可断开的槽。
- 证据：`.artifacts/probe_chart_ab_baseline.py`；`plugin_selftest` R-320（观察器断言 `lazyUpdate` 全为真）、222 checks、exit 0。
- 历史居中回归曾弱化为“在视口内/最后一张接近中心”并依赖额外 settle，不能代表用户要求；L-315 已撤销此弱化并移除重复 settle 链。

## L-311 曲线窗口跑满后不再刷新（切标签页才被注意到）（FIXED-verified）
- 2026-11-20 后续：旧 drawnCount 增量合并仍有数组别名和裁头边界；现实现已由 L-313/L-314 的版本切片与绝对序号替代，以下为历史修法，不得照搬恢复。
- 真因不在切页：页面 `drawnBuffer()` 用**长度差**（`wanted.length - buffer.length`）推算“这一帧新增了多少行”。一旦源数组与绘图缓冲都到了「展示帧数」上限，差值恒为 0，新样本永不追入——所以曲线在攒够数据的那一刻就停了，用户只是切到 HEX 再回来才看见。
- 修法：新增 `state.drawnCount` 记录已合并的**源行数**，按 `rows.length - seen` 取新增；重同步清空、`setValues` 裁头时同步衰减。
- 排查中发现的第二个真缺陷：`_sent` 在**构造** delta 时提前推到总长度，页面未确认就当成已送达。切页期间渲染器被挂起、批次没到，`has_fresh_data()` 从此恒为 False，永远不会补发。改为发送时记录本批覆盖的样本数（`_last_sent`），`_on_acked` 里才推进 `_sent`。
- 证据：`.artifacts/probe_chart_real_tab.py`（真实主窗口往返，修复前数据尾部停在 4.19，修复后 4.19→5.59）；`plugin_selftest` R-318 `[7.59, 359] -> [8.59, 459]`、219 checks exit 0。

## L-310 搜索与扩展命令都折叠时，扩展命令被拉大（FIXED-verified）
- `relayout_sidebar()` 读的是两个面板的 `_collapsed` 标志，而界面实际高度取决于 `body` 的可见性。二者在「搜索面板从全屏恢复」「一边展开时另一边折叠」后会不一致，于是走到“两边都展开”分支，把 `_sidebar_sizes` 恢复成 230/540。
- 修法：判据改为 `body.isHidden()`，并给折叠的那一栏 `setStretchFactor(0/1, 0)`，不让分隔条把剩余空间分给它。
- 探针需注意：必须先显式把两边重置为展开再折叠，否则 `toggle_collapsed()` 会把状态反过来（首轮探针就是这么误报“仍有 230”的）。
- 证据：`.artifacts/probe_sidebar_both_folded.py` 两种顺序都 `[44, 44]`；`ui_selftest` R-319 两种顺序全过，R-190 折叠/恢复比例仍绿。

## L-309 顶栏冒出 5000 输入框 / 新卡片飞到最底下 / 仪表带曲线坐标轴（FIXED-verified）
- 三个独立缺陷：
  - 三个数字设置以面板为 `parent`，`QWidget` 只要**有父对象就会被 Qt 绘制**，即使不在任何布局里，所以 5000 那个框直接出现在暂停按钮后面（L-309 主机教训：`not in layout` ≠ 不可见）。现在建好即 `hide()`，菜单里改用**新建**的 spin box，避免 `QWidgetAction.setDefaultWidget()` 接管所有权、关菜单时把数据载体一并销毁。
  - 新卡片位置曾由 Python 按序号平铺（`16 + (index%3)*(w+16)`），第 10 张就已经在几屏之外。现在 Python 不再预置 `x/y`，页面用 `scrollLeft/Top + 视口一半 - 卡片一半` 在**可视区正中**放置。
  - `mount()` 曾对 `line || gauge` 都调 `applyLineStructure()`，于是仪表先被套上曲线的 xAxis/yAxis/dataZoom 结构。改为只给 `line`。
- 居中两个实测坑：① 首次 `addWidget` 时 web view 可能只有宽没有高，一次 `setTimeout` 重试仍落在 0 高，卡片得到 x=64/y=0；② 用 `x===0 && y===0` 当作“未放置”判据会把这个半成品状态误判为已完成。最终用显式 `autoPlaced` 标记 + `requestAnimationFrame` 持续重试直到画布两个尺寸都 > 0。
- 证据：`.artifacts/probe_chart_layout_fix.py` 全部 PASS（首卡 x=304/y=144 对 1076x529 视口、仪表 `xAxis=0/yAxis=0/dataZoom=0`、曲线仍 `1/1/2`）；`plugin_selftest` 217 checks、exit 0。

## L-308 看板大数据/多控件时添加控件很慢或加不上、小屏看不全、顶栏塞满按钮（FIXED-verified）
- 三个独立原因：① `add_widget()` 无条件 `request_resync()`，把整个保留历史重新序列化（10 通道 × 5000 点 = 83 KB），页面还要为**每张**已有曲线重绘一遍；② `#canvas` 是 `overflow: hidden`，绝对定位卡片不产生滚动范围，内容超屏就彻底不可达；③ 顶栏堆了 7 个按钮 + 3 个数字框。
- 修法：① 只有绑定**尚未推送**的通道才请求全量，页面侧 `mount()` 用 `fresh=null` 复用 `state.drawn` 立即出图（并把 gauge 也纳入结构绘制）；② 拆成 `#canvas`（滚动视口）+ `#surface`（内容层，`updateExtent()` 按最远卡片定尺寸），网格移到内容层才能跟着卡片滚；③ 顶栏只留 暂停/清空控件/菜单，其余（含三个数字框）进 `build_menu()`，拆出菜单构建方法便于测试而不阻塞在 `QMenu.exec()`。
- 两个方案错误被实测否掉：把 `#canvas` 自身 `min-width` 撑大会被 `body{overflow:hidden}` 裁掉（`clientWidth == scrollWidth == 1536`）；给视口加 `padding` 也不能产生稳定滚动。必须用双层结构。
- 证据：`.artifacts/probe_chart_add_cost.py`（添加 0.02 ms、单次 83 KB 重发）、`.artifacts/probe_chart_add_fast.py`（`['addWidget']`、`resyncs+0`、0.2 ms、`paintedPixels=9737`；小屏 `1240x2496` vs `664x257`）、`.artifacts/probe_chart_toolbar.py`；`plugin_selftest` 213 checks、exit 0。

## L-307 看完 HEX 文件后整个看板停止刷新（FIXED-verified）
- 两个缺陷叠加：① 页面把 `acked` 发在绘制之后，一张卡的 `paint`/`setOption` 抛错（隐藏标签页里容器尺寸为 0 时会发生）就永远不确认；② Python 端把 `_pending_ack` 当无条件锁，没有超时，于是**所有卡片一起停刷**，不只是出事的那张。
- 修法：页面把 ack 放进 `finally`，并给每张卡单独 `try/catch`；Python 端加 `ACK_TIMEOUT_S = 1.5`，超时转为全量重同步并计数 `ack_timeouts`；另记录可见性跳变，从其它标签页切回时主动重发完整窗口。
- 排查坑：`flush()` 发全量帧时也必须记 `_ack_started`，否则超时判断把它当成早就过期的等待（被既有 R-301 背压断言捕获）。
- 证据：`.artifacts/probe_chart_ack_recovery.py` 6/6；`.artifacts/probe_chart_page_ack.py` 在真实 QtWebEngine 里让一张卡抛错，ack 仍回传；`plugin_selftest` 203 checks、exit 0。

## L-306 HEX 双击编辑框看不清内容、数字被裁掉（FIXED-verified）
- 编辑框只按 `horizontalAdvance("00") + 14` 定宽，而全局样式表给 `QLineEdit` 的 `padding: 0 10px` **不体现在 `contentsMargins()`**：实测 40 px 的框里能画的只有 ~20 px，两字符要 26 px，首字符被裁；末列定位还可能把框推到视口外。
- 修法：编辑框带自己的紧凑样式（`padding: 0 2px`、`min-height: 0`、同款等宽字体）、尺寸由 `inline_editor_size()` 按字体度量算，位置按视口夹紧。
- 证据：`.artifacts/probe_hex_editor_fit.py` 修前 `usable - text = -6`，修后首/末列/页末三处 `ink_x=3..26 / width=32`、高 17、均 `in_viewport=True`；`plugin_selftest` R-310 通过，199 checks、exit 0。

## L-305 对端删除扩展命令后上下翻动，组头文字消失只剩色条（FIXED-verified）
- 短列表剩余滚动范围 23px，小于组头 26px；旧吸顶条件要求整个组头卷走，文字已被裁切而吸顶永远不触发。改为组头顶部一旦越过视口即借用原组头；不改颜色、不建简化标题。
- 原生 Qt 合成图对照：对端删除后滚到底，修前组名墨色像素 0、可见高度 2px；修后墨色像素 222、可见高度 20px，多次往返均保持。探针 `.artifacts/probe_command_pixels.py`；正式 R-195 补部分裁切和对端删除期间滚动检查。

## L-313 看板失去响应 / 自追加数组 / RangeError（FIXED-verified）
- 原 drawnBuffer 在历史未满时直接持有 rows；下一帧向 buffer push 的同时以 rows.length 为循环上界，同一个数组无限自增长。Node 执行实际 dashboard.js 可复现 RangeError；修为按通道版本缓存独立尾部切片，多卡共享绘图切片但不与历史数组别名。
- 证据：`node tools/chart_runtime_selftest.js` 3/3，含6200点跨5000上限、共享通道、窗口切换；两端 points=0 上限统一5000。

## L-314 满缓冲漏尾样本 / 旧确认解锁 / 纯读数停刷（FIXED-verified）
- 绝对接收序号不随deque淘汰衰减，确认仅提交发送快照；批次号单调且重同步不复用。新鲜度用样本版本而非仅曲线绑定，纯 value/gauge/bar/lamp 均可刷新。
- 证据：`plugin_selftest` R-321，发送后等待期间溢出10点，下一批必须包含100..109；旧ack不能释放新批/重同步批，四类纯读数确认后才空闲。

## L-315 居中错位 / 布局递归 / 多余resize（FIXED-verified）
- 单一40ms合并布局任务处理新卡居中、内容范围和必要resize；显式用户移动取消autoPlaced。删除settle链和不可达重复添加逻辑；视口外卡懒创建、滚入追上数据。
- 证据：R-316恢复每张24px严格约束，三卡带scroll(0,0)/(200,120)/(400,240)均居中，不手动settle；既有拖动/缩放/轴缩放/滚动回归通过。测试桩恢复真实DOM前必须push_layout，不能拿桩中的id操作旧页面。

## L-316 实际绘制背压 / finished不回 / 效率低 / GUI长尾（OPEN）
- 已修机制：实时仅传曲线id/data、仪表data；关闭滑块数据阴影。实际rendered后用可被后续绘制重置的rAF屏障确认，finished也可解除等待；仅finished曾真实出现rendered=9/finished=1而锁住。单卡异常/删除不锁全板；保留超时和返回页重同步。确认驱动下批，只补齐从发送起算的目标节拍，最多一个在途批次。
- 证据：最终`python tools/plugin_selftest.py --benchmark` 257/257；四组5秒预填5000点、每通道约200点/秒，全部到尾且各组新增确认超时0。4/300、6/300、6/5000、24/5000分别完成39/36/39/27批，完成P95=148.92/197.65/153.91/248.51ms，交互探针P95=27.53/34.34/25.44/55.78ms。
- 未闭环：最重仅约5批/秒，不是30fps；此前GUI最大262.58ms长尾未归因，后续更快不能证明环境原因。当前采样仅落在app.processEvents；未证明写盘是根因。5秒斜线直接feed、部分卡在视口外；探针是JS往返/滚动/添加/菜单构建，不是负载下真鼠标交互，也不等同长期实机。rendered+rAF不是合成器呈现时间。R-320保持开放，下一步固定用户实际布局与数据定位长尾。
- 报告：`.artifacts/chart-sustained.json`、`chart-benchmark.json`；截图`chart-load-*-*.png`。L-312的appendData/自适应progressive回退仍有效，不因本轮重构重试。

## L-317 看板配置丢多通道 / 精度 / 非法范围（FIXED-verified）
- 清洗器保留channels去重列表、decimals与状态文字，拒绝NaN/无限范围和重复widget id，导入失败不得改当前布局。
- 证据：R-311实际临时文件导出与重导入完全往返，8类非法输入拒绝且布局不变；包含最终257项插件回归。

## L-318 插件并未解耦：有门面却直接 import 宿主内部模块（FIXED-verified）
- 用户质疑「插件没有解耦？」属实。terminal / transfer / hexview 源码里直接
  `from omnicom.ui import theme`、`from omnicom.ui.vt100 import Screen`、
  `from omnicom.i18n import tr`、`from omnicom.ui.workers import TransferWorker`。
  PluginApi 早就有 theme()/combo()/glyph()/menu() 门面，但 1.1.6 把那两个页面搬成
  插件时是整块复制代码，原 import 原样留下；hexview 是照抄同一写法。
- 后果不是当前 bug，而是架构债：插件目录会被复制到用户磁盘长期驻留，宿主重命名
  一个文件，已安装插件就坏，且要等用户打开那个标签页才暴露。
- 修法：`api.theme()` / `api.paths()` 用只读门面（赋值或调用 setter 抛异常）、
  `api.widget_class(name)` 返回控件类、`api.terminal_screen()`、`api.transfer_worker()`；
  插件文案各自 `text.py` + `api.register_text()`，宿主表移除三个插件前缀文案，
  卸载时注销；`API_VERSION` 2→3，插件清单声明 `"api": 3`。
- 两个实测坑：① 插件必须用相对导入 `from .text import TEXT`——所有插件目录都会
  进 sys.path，三个都叫 text.py 会按加载顺序互相遮蔽；② 测试自己加载插件时也要
  以包形式注册（`submodule_search_locations` + `sys.modules`）再执行，与
  `_instantiate` 一致，否则相对导入直接 ImportError。
- 另一个空真隐患：文案迁出后 `all(tr(key) for key in ...)` 仍成立（未命中返回键名，
  非空）。GUI 断言改为 `tr(key) not in ("", key)`，否则测试会在文案丢失时继续通过。
- 证据：`plugin_selftest` R-322 五项（静态扫描、文本表存在、宿主表无插件键、
  注册后可解析、卸载后回退）；完整 245 项 / `--benchmark` 262 项均 0 失败；
  真实 `PluginHost` 加载 5 插件零错误；便携包内自带运行时验证通过。
- 顺带修掉既有缺陷：传输页拖放区标题从未跟随语言切换，英文界面残留中文。

## L-319 帮助菜单显示 menu.help / 翻译缺键（FIXED-verified）
- 根因：标题栏使用 `tr("menu.help")`，宿主表没有该键，回退策略直接显示键名；扫描还发现 `misc.control.open` 缺失。已补齐两键，不更改回退策略。
- R-324 新增字面翻译键静态门禁，扫描419个调用键；帮助/文档/关于中英均可解析。实际菜单构建路径已核对，未完成真实弹出菜单像素验证。
- 证据：修复提交 `4a5db45`；完整插件自测253项、Ruff通过；新包内pyz含两键。旧「58条（459→406）」是统计口径混用，不再作为精确迁移数量。

## L-320 终端与文件传输停用后商店消失 / 无法启用（FIXED-verified）
- 根因：商店行取本地安装与远程目录并集，但 `installed()` 排除了停用插件；terminal/transfer 是随包核心插件，不在远程目录，因此从两边都消失。
- 修复：本地清单保留停用状态和已安装版本；商店显示「已停用」和「启用」。`set_disabled()` 只调整标记，复用异步任务与宿主重载，不下载、不覆盖用户数据。
- 证据：插件自测R-323四项及完整253项通过；GUI R-218停用接收6项通过；隔离真实对话框点击启用terminal后，仅transfer保持停用、重载一次；包内运行时复验通过。

## L-321 修复后同版重发 v1.1.7 / 插件商店是否重传（FIXED-verified）
- `4a5db45` 已推送main；复用Release #452554311并替换同名便携ZIP，发布脚本退出0、latest=v1.1.7。既有标签仍为 `aeee113`，不强制移动；修复源码在main。
- 新包190862126字节、692条目、CRC正常；本地SHA-256 `d225b9a9d7bb1a67c2d8a9d2a834b04b1a7250dcf9f67f1aa5c88f196957642e`。此前公开GET仅验证HTTP200和大小，不能据此声称远端哈希一致。
- 2026-11-23补核：GitHub资产metadata的 `digest` 与上述本地SHA-256完全相同，Release公开且含修复说明，API latest仍为v1.1.7；线上raw main插件目录与重建后本地JSON一致。
- 重新构建chart1.1.3、hexview1.0.0、launcher1.0.0成功，哈希与已发布包相同，无需再次上传；核心插件仍通过主程序便携包交付。
- R-320性能边界不变；本次未重跑全工程GUI/总验收，不将插件自测通过宣称为全工程全绿。
## L-322 终端/传输与宿主完全同构 / 第二个窗口清掉第一个窗口文案（FIXED-verified）
- 宿主删掉了 `window.console` / `window.transfer` 与 `BUILTIN_PLUGIN_PAGES`：页键改由清单
  `page` / `page_order` 声明，宿主只按页键挂载与卸载；终端插件用 `api.on_rx` 自己取数据，
  `_on_rx` 不再调 `absorb`。
- 顺带发现并修掉真实缺陷：开第二个窗口时 `PluginHost.unload()` 会注销第一个窗口仍在用的
  插件文案表（终端按钮提示变回 `term.fullscreen`）。文案表改为按宿主对象引用计数。
- 证据：`ui_selftest --only plugins` 全通过（含「宿主不再持有终端专用接收通道」）；探针证明
  全部插件停用时收数据仍进日志、终端只靠自身订阅即可拿到 `prompt$` 且切页后保留。

## L-323 「卸载」只记标记、永远显示已安装（FIXED-verified）
- 2026-11-23 用户反证：重复多次卸载后仍出现「启用/已安装」；原单次285项验证不覆盖真实按钮循环及双副本，不能视为闭环。来源框也再次被否定，R-325整行交互待修复。
- 本次复现：双副本时只移走用户目录，随包副本仍存在且disabled=True。修复为移动全部活动副本并清除disabled；恢复按原来源，重复卸载不清空备份；下载重装清除来源记录，失败恢复目录及两份记录。
- 新证据：`tools/plugin_selftest.py --interaction-only`，用户/随包/双副本各六轮真实按钮、重建Store与隔离PluginHost通过；额外覆盖跨盘复制回退、双副本失败回滚、重装写记录失败。限定为当前源码；旧发行包未更新。
- 旧 `uninstall()` 只写 `plugins-disabled.json` 并把目录挪到 `plugin-updates/`，`installed()`
  仍扫描原路径，于是行永远显示「已安装/已停用」。用户要求真实卸载。
- 现在目录真的离开 `plugins/`（随包核心插件同样移出），宿主立即不加载、页签消失；
  `plugin-sources.json` 记录来源与备份，行标记 `removed=True` 且只给「安装」；再装可从备份
  还原同一版本，`plugin-data/<id>` 不动。
- 跨盘（数据目录与程序目录不在同一盘）从 `Path.rename` 改 `shutil.move`，否则 WinError 17；
  失败时把目录移回并恢复两份记录。
- 旧证据：`plugin_selftest` 285项仅单次流程；永久宿主测试使用隔离PluginHost，不是用户真实窗口，旧「真实窗口」表述不准确。
- 教训：探针不要指向真实安装目录 —— 一次探针真卸载了仓库 `plugins/terminal`，
  用 `git checkout -- plugins/terminal` 恢复，并要重新应用该目录的未提交改动。

## L-324 多曲线来源框不能整行勾选 / 黑块难辨（FIXED-verified）
- 后续用户反证滚轮悬停仍黑底：原生drawControl可重新盖上系统高亮；现背景、文字、复选框均自行绘制。强制Highlight/HighlightedText黑色，滚轮三次选中鼠标下行像素仍浅，滚动不改勾选；Windows字体截图已查看。来源入口只供line，非line多名称只绑定第一项。
- 用户2026-11-23再次否定来源框。原列表行高14px、文字点击不切换，未选中也画成黑块；改为至少32px且适配字体、整行左键释放只切换一次、原生空格键可切换，主题空框与白色勾号明确区分。
- `--interaction-only`覆盖文字/方框/空格、六次连续切换、未知通道保留、空选择禁用确认；卡片真实模态编辑取消不改配置，确认后页面系列从AA/BB变BB/CC、几何不变。Windows字体截图已查看；旧包未更新，用户实机尚未确认。
- 插件动态加载模块与普通import的同名类不是同一类型，测试必须使用实际加载模块的SourcesDialog；没有字体的offscreen截图不能作为视觉验收。

## L-325 灯自定义亮灭颜色 / 调色板 / 颜色保存丢失（FIXED-verified）
- R-328：创建色块、现有灯齿轮与真实Qt调色板，亮灭颜色贯通绘制/describe/persist/配置；旧null颜色兼容。真实回归抓到persist遗漏字段和模态持有旧布局对象，均修复；QSS用对象名限定，避免子调色板按钮染色，内置文案经api.t本地化。
- `python tools/plugin_selftest.py --interaction-only` 362项：颜色选择/取消、亮灭DOM RGB、现有灯编辑/取消、导入导出、非法颜色拒绝通过；`.artifacts/lamp-palette.png`已视觉核对。本轮未打包发布。

## L-326 保留点数低于展示帧数时只有保留量（REVERTED）
- 曾把有效容量改成 max(保留, 展示)，但那会篡改用户的显式「曲线点数」：R-301 要求显式值就是保留上限，points=150 必须保留 150。已回退为 `min(MAX_POINTS, max(100, limit))`，`visible` 只控制每帧绘制尾部。
- 证据：完整插件套件 R-301「曲线点数按设置生效」在错误实现下失败（300≠150），回退后全套 587 项通过。该推测未解释用户实际 5000/5000/0，见 L-327。

## L-343 关闭软件后屏幕是否仍常亮（FIXED-verified）
- 用户确认：在软件里开了「保持屏幕常亮」，关掉软件后屏幕**不应**继续常亮。
- 结论：不会残留。`SetThreadExecutionState` 是**线程作用域**的请求，线程退出（含 `TerminateProcess` 强杀）时 Windows 自动丢弃；`closeEvent` 里另有显式 `power.release()` 做正常关闭的即时释放。
- 实测（不是读文档）：`.artifacts/probe_awake_close.py` 真实创建 `OmniWindow`，开启后向 OS 读回 `0x80000003`，`close()` 后读回 `0x80000000`；`.artifacts/probe_awake_kill.py` 强杀已 latch 的子进程，父线程仍为 `0x80000000`。
- 多窗口不是风险：每个窗口是**独立进程**（`window-<n>.lock` 各自锁定），关一个不会清掉另一个的请求。
- 新增 `ui_selftest` R-343 两项，直接向 OS 读回线程状态，而不是只断言 `release()` 被调用。
- 教训：`powercfg /requests` 需要管理员权限，普通会话读不到；验证常亮要用 `SetThreadExecutionState` 的「返回上一次请求」语义读回，而不是依赖外部命令。

## L-342 换电脑后检查更新报 SSL 证书校验失败（FIXED-verified）
- 症状：另一台电脑点「检查新版本」直接失败：`[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1032)`。
- 根因：便携包用官方 CPython **embeddable** 运行时，`python313._pth` 关闭了 `site`，所以用户 site-packages 里的 `certifi` 不可见；运行时只带 `_ssl.pyd` + `libssl-3.dll`，**没有任何 CA 证书**。开发机能通是因为 Windows 上 OpenSSL 会回退到系统证书库（本机 79 张），而目标机器的系统库缺少根证书（精简镜像 / 新装 VM / 企业代理重签）。
- 修法：新增 `omnicom/tls.py`——随包 `assets/cacert.pem`（certifi 240216 B），`context()` 用该文件建校验上下文并额外 `load_default_certs()` 合并系统库（企业代理根仍受信）；`install()` 装一个模块级 opener，让既有 `urlopen` 调用点全部生效；GUI 与 CLI 入口各调一次。`make_pyz` 把证书复制进 `assets/`。
- 提示改进：`update._reason()` 新增 `_is_certificate_error()` / `_certificate_detail()`，证书失败走 `update.check.tls`（提示查系统时间 / 代理根证书），不再误报「网络不通」。
- 证据：`.artifacts/probe_tls_bundle.py` 仅用随包文件建出 121 张根证书并 HTTPS 200；`.artifacts/probe_packaged_tls.py` 用便携运行时确认 `dist/OmniCOM/assets/cacert.pem` 生效；`acceptance_test --only local` R-342 四项通过；Ruff 干净。
- 注意：`releases.atom` 当时返回 HTTP 500（两种 UA 都一样），是 GitHub 侧临时故障，与证书无关；`raw.githubusercontent.com` 200 证明信任链正常。
- 2026-11-25 固化门禁 + 重发完成（源码 `c05430a`）：`make_dist._verify_tls()` 在 `verify()` 里用包内 runtime 实测——先查 `assets/cacert.pem` 存在且 >100 KB，再用仅该文件建校验上下文（要求 >50 张根证书），最后实际访问 HTTPS；证书错误直接判打包失败，纯网络不通仅警告（根证书验证不依赖网络）。该门禁已写入 AGENTS.md 为发布硬约束。
- 门禁自身修掉两个真实缺陷：`-E` 会忽略环境变量（改用内联绝对路径）；调用方传相对路径时`sys.path` 指向包内而导入失败（改在函数内 `resolve()`）。
- 重发结果：Release #452554311 替换 `OmniCOM-1.1.7-win64.zip` 为 191022574 B `sha256 ec71d3a558228db973e0591f69b7b36560c2de22b0ed0e9a481eb2e1f4bfc25d`；公开 HEAD 200 且长度一致，`latest=v1.1.7`。打包前用 `.artifacts/verify_zip_runtime.py` 实测包内 HTTPS 200。
- 教训：任何「开发机正常、换机器失败」的网络问题，先查**运行时是否自带 CA**，不要先怀疑代理或防火墙；嵌入式 Python 的 `._pth` 会静默屏蔽 site-packages。

## L-341 数据来源选择框悬停背景变黑（FIXED-verified）
- 症状：多曲线「数据来源」对话框里，鼠标悬停的行背景是纯黑，未勾选复选框边框也是黑的。
- 根因：`SourceDelegate.paint()` 把 `theme.WASH_HOVER` / `theme.LINE_STRONG` 直接交给 `QColor`。这两个令牌是 Qt 样式表形式 `rgba(18, 26, 43, 14)`（alpha 0-255），`QColor` **完全无法解析**，返回 `isValid()==False` 的颜色，`fillRect`/`setPen` 于是画成不透明黑。`omnicom/ui/theme.py` 第 59 行早有同样注释（`WINDOW_EDGE` 因此拆成两个令牌）。
- 修法：插件内新增 `_paint_color()`，用正则把 `rgba(r, g, b, a)` 拆成 `QColor(r, g, b)` 并设置 alpha（兼容 0-1 与 0-255 两种 alpha）；`#RRGGBB` 仍直接交给 `QColor`。插件自包含，不依赖宿主新增令牌，旧版 1.1.7 宿主也能正确显示。
- 证据：`.artifacts/probe_source_hover.py` 修复前悬停行 `#000000`，修复后 `#f2f2f3`、未勾选边框 `#e3e4e6`；`.artifacts/probe_source_negative.py` 把转换改回 `QColor` 后断言失败（`#000000`）。`plugin_selftest --chart-only` 316 项通过（新增 R-341 两项）。
- 2026-11-24 重发完成（源码 `f5f55fa`，已推送 `main`）：插件商店 #436347358 用新包替换 `chart-1.1.5.zip`（387671 B，`sha256:7d8a29e02665e25b191153f57910d52bae207c2b94a5b7ff2c37bae1b10aa673`）与 `catalog.json`（4497 B，`sha256:e054ac40793096b2ece54e99d8e1332657a7a259e363c795f0cc05b02c6347d2`）；7 个包 Range 均 206，`/releases/latest` 仍为 v1.1.7，公开 raw 目录 chart=1.1.5 且哈希一致。
- 教训：既有 R-325 只验证「选中」行，从未验证「悬停」行，所以这个缺陷一直没被发现；任何自绘背景都要同时断言 hover 与 selected 两种状态。

## L-336 满缓存后曲线边刷新边变形（OPEN）
- 2026-11-24 用户反证：同组数据的两张截图波形不同；用户确认没有缩放，但截图时间不同，所以横轴510–690与480–660的差异本身不是故障证据。原 minmax 已彻底解决的结论撤回；下面旧实验只证明特定负载的帧间像素变化。
- 本轮已复现机制：真实ECharts 5.5.1中，5000点窗口仅滑动1点，minmax实际绘制834点，重叠历史区间800个已绘制时间点被重新抽掉；历史线段因此不是固定的。`tools/chart_runtime_selftest.js`原配置断言失败，关闭自动采样（sampling=none）后5000点完整绘制，历史遗漏=0。不改容量、增量游标、缩放或现有progressive配置。
- 源码候选已验证：`python tools/plugin_selftest.py --chart-only`314项、Node4项、Ruff通过。真实QtWebEngine四通道各5000复杂波形，320/920px两宽度与0/1/300点滑动，逐个绘制点等于原始数据、画布非空；同步setOption约25.9–64.4ms，不等于持续30fps或合成器呈现耗时。
- 2026-11-24 用户复测确认「解决了」，R-336 关闭。交付路径核实：`make_dist.CORE_PLUGINS` 仅 terminal/transfer，`verify_runtime` 断言包内不得有 `plugins/chart`，所以主便携包不含看板；本轮只发布插件，chart 升 1.1.5（387200 B，sha256 1969ab65…），应用仍为 1.1.7。
- 2026-11-24 发布完成（源码 `6a08467`，已推送 `main`）：插件商店 #436347358 上传 `chart-1.1.5.zip`（387200 B，`sha256:1969ab6513523a2fdedf6c06165533042e0cbb15baa4a24589ebb1636372fd09`）与新的 `catalog.json`（4497 B，`sha256:b6315fb2fe3ef98a7d04762dbb4f9ed59492c2fe533328853f948041f78a1ec7`）；7 个包 Range 请求均 206，`/releases/latest` 仍为 v1.1.7，公开 raw 目录 chart=1.1.5 且哈希一致。
- 发布过程教训：PowerShell `Set-Content -Encoding UTF8` 会给提交信息写 BOM，Git 标题出现 `锘縖`；Python 脚本 `read_text`/`write_text` 把原本 LF 的 `project-context/*.md` 转成 CRLF，导致 LEDGER 出现 1727 行假差异。改提交前必须核对 `git show HEAD~1:<file> | count('\r\n')`，只用 `write_bytes` 写 LF。
- 新症状（2026-11-24）：用户报「帧数超过当前最大缓存时波形变形，边刷新边变形」。不是停刷（L-311/L-313/L-314 已修），而是**形状抖动**。
- 先排除：绘制切片严格单调（`node` 探针 5000 点 0 处非递增）；真实页面刷新中与停止后墨迹像素 0.99~1.00，不存在分帧渲染被截断（`.artifacts/probe_chart_deform.py`）。
- 旧假设/实验：LTTB滑动桶会重新选择代表点。`.artifacts/probe_chart_deform2.py`相邻帧像素差lttb均值4.01%、峰值16.6%；minmax2.26%、峰值2.63%；none0.01%。这不能证明minmax保形或唯一根因。
- 旧候选（已被本轮替代）：minmax仍动态抽点，不能保证历史波形不变；旧仅检查配置字符串的R-307断言已改为R-336完整绘制检查。large/progressive设置保留，性能目标未关闭。

## L-337 看板框选与整组拖动（FIXED-verified）
- R-337：新增 `state.selection`（多选）与 `hookMarquee()`：在 `#canvas`/`#surface` 空白处左键按下开始框选，选框挂在 `#surface` 内容层（随滚动跟随），相交即选中；Ctrl 追加/切换；空白单击清空。
- 整组拖动：`beginDrag()` 在拖动已选卡片时收集全部成员起点，组相对位移一致，并按最左/最上成员夹紧，保证不越过画布边界且相对位置不变。
- 踩坑：顶栏/菜单断言原用 `isVisibleTo(panel)` 判「不在顶栏」不成立（未加入布局的按钮仍算可见），改用 `not in bar_widgets` 直接判断布局成员。
- 证据：`plugin_selftest` R-337 用真实 `PointerEvent` 序列验证选框显示、选中 sel-a/sel-b（不含 sel-c）、整组位移 (+96,+64) 且 sel-c 不动。

## L-338 / L-339 清空数据与清空看板（FIXED-verified）
- R-338：顶栏新增「清空数据」，直接调用既有 `clear_data()`（清采样、保留卡片），与「清空看板」互补。证据：点击真实按钮后页面 `retained` 全为 0，卡片数不变。
- R-339：原「清空控件」改名「清空看板」并移入菜单，顶栏变为 暂停 / 清空数据 / 菜单 三项；`build_menu()` 在清除数据后追加 `clear_widgets_button.text()`。这修订 R-315 的顶栏组成（已登记 Superseded）。
- 本轮回归：`plugin_selftest` 608 项、GUI 980 项、总验收 1785 项 0 失败、Ruff 干净。

## L-340 v1.1.7 第五次重发（FIXED-verified）
- 内容：R-336 minmax 采样、R-337 框选整组拖动、R-338 清空数据、R-339 清空看板入菜单，
  加上前一轮 R-331/R-332/R-333。
- 源码：`3ec0145`（功能）+ `2769c0b`（store catalog 哈希与发布脚本按内容判定，已推送）。
- 应用：Release #452554311 替换 `OmniCOM-1.1.7-win64.zip`，线上 digest
  `sha256:33818bb0694e168df1220bc4c65c967e156e9e0729d311adc879c2526db3cfc4`（190883602 B），
  公开 `releases/latest/download` HEAD 200 且长度一致，`latest=v1.1.7`。
- 插件商店：`chart-1.1.4.zip` 替换为 387450 B `sha256 6322a2dd…`；`catalog.json` 已上传
  新哈希并在仓库内核实（raw CDN 事后也已刷新为 6322a2dd）。
- 发布教训（新增）：`publish_plugin_store.py` 原先只比**字节数**决定跳过，而
  `catalog.json` 换哈希时长度不变，导致目录从未真正更新（L-330 同族）。已改为优先比
  GitHub 返回的 `digest`。
- 另注：`Select-Object -Last` 会缓冲输出，长时间上传在后台会话里看不到进度，
  不能据此判断卡死；完成后必须查 `/releases/<id>` 的 assets 与 digest。

## L-327 实际5000帧设置未满即历史消失（OPEN）
- 只读核对用户settings.ini实际window=0/visible=5000/points=5000。两条曲线同配置在128/1000/3200/4999/5000/5101/6200保持完整首尾，尚未复现；不能把L-326当成其根因。下一步用用户实际布局/原始输入与运行副本证据区分接收、缩放可见范围、旧包；不宣称整个问题修好。

## L-328 多色灯 / 单色灯任意触发数值 / 数值颜色映射（FIXED-verified）
- R-329：同一个lamp的colorRules精确数值匹配，单条规则就是单色灯；fallbackColor支持无色或自定义色。创建入口/现有卡片复用LampColorsDialog，可增删规则、调色和指定无色。旧无规则或null字段仍保持零/非零二态，明确启用匹配后迁移。
- 页面setLayout/addWidget/describe/persist及Python规范化、INI和文件往返均保留规则；重复数值、非有限值、布尔值、缺字段和非法颜色被拒；用repr保留双精度编辑往返。无数据为空心且显示--，不误当零或默认值。
- `python tools/plugin_selftest.py --interaction-only`409项通过：真实创建/调色板、0红/1白/2黄/其它无色、其它自定义色、-3.5单规则、明确无色优先、增删/取消、文件/设置持久化；旧二态灯、来源及曲线回归通过。`.artifacts/lamp-rules.png`已视觉核对。源码候选，未打包发布；L-327仍OPEN。

## L-329 核心插件启用无效 / 商店操作闪出空白小窗（FIXED-verified）
- 用户反证「启用终端启用不了」：根因是 `toggle.clicked.connect(lambda _checked=False, ... on=not item.get("disabled"): self.set_enabled(key, on))`，而 `set_enabled` 内部写 `set_disabled(identifier, not enabled)`，两者叠加后点「启用」实际写入 `disabled=True`，所以永远启用不了。修法：用显式 `want = bool(item.get("disabled"))` 绑定，去掉二次取反。证据：探针抓到 `set_disabled calls: [('terminal', True)]`（点启用却写 True），修后为 `False` 且磁盘 `disabled` 清空、按钮变「禁用」。
- 用户反证「启用/安装/卸载都闪出空白小窗」：每行更新红点是 `QWidget()`，无父控件在 `setVisible(True)` 时成为顶层窗口，出现 8x8 空窗，随后 `addWidget()` 重新挂父，事后检查 `parent()` 已看不出痕迹。修法：`QWidget(row)` 构造即带父。
- **测试方法教训**：事后查 `parent()` 或 `topLevelWidgets()` 都抓不到这个缺陷（必须对比"创建即带父"与"先显示后挂父"两种顺序）。最终用源码级断言 `badge = QWidget(row)` 守门，并做负向验证：改回 `QWidget()` 时断言失败、退出码 1。
- 同时按新契约调整旧断言：R-323「启用后卸载按钮回来」→「启用后只给禁用、永不出现卸载」；R-327 真实卸载回归改用非核心插件（gauge/probe），核心插件 `uninstall()` 改为抛 `core`。
- `tools/acceptance_test.py` R-001 整体通过判据改为按整行匹配收尾横幅：Qt 的 `propagateSizeHints()` 警告会粘在同一行，旧子串判断在全绿时误判失败。
- 证据：完整插件 603 项、GUI 980 项、总验收 1780 项 0 失败、Ruff 通过；负向验证抓红点缺陷。commit `82772c1`。

## L-330 make_release 上传中断后留下空资产列表（FIXED-by-manual-upload）
- 现象：代理侧 `make_release.py --publish` 在后台终端会话里反复被清理，最后报 `上传失败： 404 Not Found`；
  查询 `/releases/452554311` 的 assets 为 `[]` —— 旧包已被删除、新包未上传成功，发布页短暂无任何可下载文件。
- 根因：该脚本遇到 422「同名资产已存在」时会先 DELETE 旧资产再 POST 新包；一旦上传阶段失败，
  资产列表就为空。加之 182 MB 上传耗时数分钟，长任务放在会被清理的后台会话里极易中断。
- 处置：用户手动上传本轮构建的 `dist/OmniCOM-1.1.7-win64.zip`；核对线上 digest
  `sha256:e307e197…`（190874335 B）与本地一致，公开链接 HEAD 200，`latest=v1.1.7`。
- 教训：① 发布必须在前台/独立进程跑完，不要依赖会超时清理的会话；
  ② 发布后必须查 `/releases/<id>` 的 assets 与 `latest`，退出码为 0 也不代表资产还在；
  ③ 任何"先删后传"的流程要么先上传到临时名再改名，要么失败后自动回补。

## L-331 日志墙钟毫秒 / tick / 无时间戳（FIXED-verified）
- R-331：支持冒号毫秒、墙钟优先、单位 tick、裸 tick 与无戳 0.1 秒估算；同一行墙钟优先于芯片 tick。
- 遗留边界：一行图表帧被拆成多条物理行时，只保留含 `[OmniCOM-chart]` 的行，续行字段仍会丢；混合绝对墙钟与相对 tick 的基准跳变未处理。

## L-332 升级后核心插件缺失（FIXED-unverified）
- R-332：`PluginHost.load()` 前调用 `restore_core_plugins()`，从 `paths.resource_dir()/plugins` 补齐缺失的 terminal/transfer；已存在目录与 `plugin-data/<id>` 不动。
- 未验证：旧用户真实升级路径未复现（旧安装仅有 launcher）；商店插件仍不随包（R-174）。

## L-333 打开日志错图 / WebEngine右键Reload恢复（FIXED-verified）
- 新证据（user, 2026-11-24）：此前候选仍无效，右键页面Reload后恢复。原clear_data已清_sent，先前“缺少清零”根因撤回；桩测试不能证明真实加载问题。
- 根因（静态路径＋用户手测）：`_on_page_loaded` 只补布局，点数/主题/历史都依赖更早的 greeting；握手早于 `pageReady` 时被 `_run_js` 静默丢弃，首次显示状态不全；Reload 重跑整套初始化才正常。
- 修法：`loadFinished` 统一补齐点数/主题/布局/全量快照；`end_replay` 重推点数与布局并全量重同步，重建图表实例与缩放状态后再送完整窗口。
- 曾试过在 `refresh()` / `_flush_if_ready` / 定时器入口抑制回放期间的实时帧，均使 R-305、R-318 失败（测试的正向出帧依赖定时器），已全部回退，只保留已验证的两处。
- 验证：单元 8 项；插件 603 项；GUI 980 项；总验收 1780 项 0 失败；Ruff 干净。用户手测确认首次加载正确。
