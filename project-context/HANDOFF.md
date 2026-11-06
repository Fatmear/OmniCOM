# Current handoff

Updated: 2026-11-06（1.1.5 发版收尾：R-181 插件商店空白小窗、R-182 看板控件读数测试、
R-183 快捷启动插件首个测试、插件说明文档与实机图。全部闸门已过，待提交 / 打标签 / 发布）

## Latest State

- 版本 **1.1.5**（`omnicom/__init__.py`），本轮改动**全部未提交**，工作区即发布状态。
- 本轮共 5 件事，全部闭环：

### R-181 插件商店「检查更新」闪一个空白小窗（FIXED）

- 用户原话「插件检查更新怎么会有弹窗」，追问后确认是「一闪而过的空白小窗口」。
- **先证明刷新链路根本不弹框**：全仓 grep 商店相关代码只有 `confirm_remove` 一处对话框。
- 真根因：刷新完成后 `rebuild()` 重建列表，作者徽标与「已安装 vX」徽标用
  `widgets.Badge(...)` 创建时 `parent=None` —— **Qt 里没有父对象的 `QLabel` 本身就是顶层窗口** ——
  而 `show_text()` 在 `addWidget()` 把它们挂进布局**之前**就调了 `show()`，于是每个徽标都
  变成真实小窗口闪现一帧，紧接着被 reparent 才消失。
- 修法：`Badge.show_text()` 在还没有父对象时只记下显示意图，等 `showEvent()`（插入布局之后）
  再真正显示。

### R-182 看板五种控件都必须显示真实数值（测试兜底，FIXED）

- 文档里写「五种控件都有数据」，但这句话**以前只靠肉眼**：`plugin_selftest` 只验证控件挂上去了。
- 新增 `chart_readout_checks()` 8 项：把 `chart.layout_data["widgets"]` 设成五张各绑一个通道的卡片，
  喂一帧 `AA=1 BB=2 CC=3.25 DD=42 EE=1`，然后用 `runJavaScript(..., callback)` 从 DOM 读回：
  五类控件齐全、数值卡片显示 `3.25`、进度条 `42.00` + 宽度 `42%`、指示灯 `ON`、
  **没有任何卡片留着 `--`**、曲线与仪表盘真的画出了像素（9737 / 4167）。
  再喂 `DD=0 EE=0` 证明 **`0` 是读数不是「没有数据」**（`OFF` 而不是 `--`），最后清空控件。
- 两处坑写进了 L-245：`paint()` 读的是 `widget.channel`（单数），而夹具原来只设了 `channels`；
  `0` 被判成「无数据」是这类控件的经典错误。
- 顺带把 `make_plugins.py` 的 `verify_chart()` 装成一票否决：任何卡片 `--`、空 `0%`、
  缺 `lampText`、卡片被压到 `< 160x110` 都**直接让打包失败**。

### R-183 快捷启动插件首个回归测试（FIXED）

- 该插件此前**零测试覆盖**。新增 `launcher_checks()` 9 项：写 3 个快捷方式 → 3 块瓦片 + 计数胶囊
  含「3」；喂一份合成进程快照 → 下排出现 2 块运行中瓦片、空提示隐藏、计数出现「2 个窗口」；
  按窗口改名 A 后下排标题变成 `["甲窗口", "A"]`；`hide()` 停表、`show()` 起表。
- **必须打桩**：真实 `refresh_running()` 在工作线程里枚举本机窗口，会覆盖合成快照，
  所以先 `page.refresh_running = page._scan_processes = lambda: None` 再直接调 `_apply_processes()`。

### 插件说明文档与实机图（本轮新增文件）

- `docs/PLUGINS.md`（约 250 行）：两个插件各自一节，含控件含义、通道绑定、快捷启动两排怎么用、
  catalog / 打包 / 发版说明。
- `README.md` / `README.en.md` 由 `tools/make_readme.py` 重新生成，都多了「插件」章节。
- 实机图 `docs/images/plugin-chart.png`（1180x760）与 `docs/images/plugin-launcher.png`（1000x620），
  由新工具 `tools/make_plugin_screenshots.py` 用**真实插件**跑出来（不是画图），并在写盘前自检：
  `verify_chart()` 逐张卡片确认没有 `--`，`verify_launcher()` 确认下排真有运行中窗口。
- 隐私坑（L-245）：最初用 `notepad.exe` 做运行中示例，截图里把本机
  「编译命令.txt - 记事本」「66.txt」等真实文档标题拍了进去 ⇒ 换成 `charmap.exe`。
- `make_pyz.py` 会把包内 `PLUGINS.md` 的 `](images/` 改写成 `](docs/images/`（该文件落在包根）。

### R-034 守卫扩到「文档里的图不能是坏图」

- `acceptance_test.py` 新增 `broken_images(doc, base)`，对 `README.md`、`README.en.md`、
  `docs/PLUGINS.md` 逐个解析 `![](…)` 与 `<img src=…>`，要求相对路径真实存在；
  另加一条「README 至少展示一张界面截图」，防止把插图删光后守卫静默通过。

### R-174 打包校验回归（FIXED）

- `make_dist --check` 在**最后一步** `StopIteration` 失败，可前面每一行都写着 PASS。
- 根因：`make_plugins.verify_runtime()` 还留着 `for mode in ("bundled", "native")` 两个模式，
  而 R-174 改成「发布包不含 `plugins/`」之后 `bundled` 那一遍仍在包内找 `plugins/chart`。
- 改成单一 store 模式：先 `assert not (root/'plugins').exists()`（把 R-174 钉进打包校验），
  再把仓库里的 chart 打成 zip、用 `PluginStore().install()` 装进临时数据目录后才断言；
  卸载前写的 `plugin-data` 文件卸载后必须还在（用户数据不能被卸载删掉）。→ L-246。

## Verification

全部闸门绿（本轮顺序跑完，日志在 `.artifacts/`）：

```
python -m ruff check .                    # All checks passed!
python tools/acceptance_test.py           # 1082 项检查，失败 0，未覆盖需求 0；ALL REQUIREMENTS VERIFIED
python tools/ui_selftest.py               # 711/711 项通过 / ALL GUI SELFTESTS PASSED
python tools/plugin_selftest.py           # PLUGIN SELFTEST OK: 174 checks
python tools/contrast_audit.py            # ALL 15 CONTRAST PAIRS >= 4.5:1
python tools/protocol_selftest.py         # 23/23 ALL PROTOCOL SELFTESTS PASSED
python tools/make_dist.py --check --zip   # DIST=0；packaged selftest OK / PLUGIN CYCLE OK / updater acceptance suite OK
```

`acceptance.json` 里 R-182 判 PASS（8 项）、R-183 判 PASS（9 项），`uncovered` 为空。
`make_plugin_screenshots.py` 两次实跑 exit 0。

## What changed (files)

- `omnicom/ui/widgets.py` — R-181：`Badge.show_text()` 延迟到有父对象后再显示。
- `omnicom/ui/plugin_store.py` — R-181 的同源加固（徽标创建顺序）。
- `tools/plugin_selftest.py` — R-182（8 项）、R-183（9 项），共 **174 checks**。
- `tools/acceptance_test.py` — `broken_images()` + R-034 四条新检查；`TITLES` 加 R-182 / R-183。
- `tools/make_plugins.py` — `verify_runtime()` 改单一 store 模式（R-174 回归修复）。
- `tools/make_plugin_screenshots.py`（新）— 真实插件跑图 + 写盘前自检。
- `tools/make_readme.py` / `make_pyz.py` — 插件章节、包内图片链接改写。
- `docs/PLUGINS.md`（新）、`docs/images/plugin-*.png`（新）。
- `CHANGELOG.md` — v1.1.5 段补上插件文档条目并更新验证数字（1082 / 174）。
- `project-context/` — REQUIREMENTS R-182 / R-183、LEDGER L-245 / L-246。

## Known open

- **L-188 仍 OPEN**：公共插件仓库还没发布，默认 `plugins/catalog.json` 指向
  `releases/download/plugin-store/` 这个 tag，但这批资产尚未上传。
  `dist/plugin-store/chart-1.1.0.zip` **要留** —— 它是降级目标，上传资产时别漏。
- `dist/` 里并存 1.1.3 / 1.1.4 历史 zip，仅供回溯；发出去的必须是本轮重打的 1.1.5。
- **`docs/PLUGINS.md` 不要加进 R-142 的随包版本扫描**：它里面的 `"version": "1.1.2"` /
  `"1.1.0"` 是**插件版本**，不是应用版本，加进去必然误报。

## Conventions that bit us this round (now in LEDGER)

- **L-245**：`QLabel` 没有父对象就 `show()` = 真顶层窗口。凡是「先建控件、后挂布局」的代码，
  第一次 `show()` 必须发生在 `addWidget()` 之后；同理 `paint()` 读 `widget.channel`（单数）
  而夹具只设 `channels` 时，界面会「有卡片没数字」，这种缺陷以前完全靠肉眼。
  `0` 是读数，不是「没有数据」。
- **L-246**：**删掉一个发布形态时，要同时删掉所有断言该形态存在的校验**。另外这类
  「只剩最后一步报错」的日志要看**第一个 Traceback**，别被满屏 `[PASS]` 误导。
- **L-197 是工具坑不是 bug**：PowerShell 把子进程 UTF-8 按 GBK 解码并追加 `NativeCommandError`，
  通过的运行看起来像乱码。**重定向到文件时默认是 UTF-16**（`> log 2>&1`），
  Python 侧要用 `open(path, encoding='utf-16')` 才读得到。
- **PowerShell 不支持 `python - <<'PY'` heredoc**，也不吃 `python -c` 里的中文引号；
  一律写脚本文件到 `.artifacts/` 再执行，并用 `& python` 调用避免 `>>>` 进 REPL。

## Next up

1. 富文本提交信息（背景/根因 + 核心改动 + 验证结果），`git add -A` → commit → push。
2. `git tag -f v1.1.5`（本地旧标签指向 `8d947ae`，未推送、无对应 Release，可安全重指）
   → `git push origin v1.1.5`。
3. `python tools/make_release.py --check` → `--publish`；**绝不 `--hide-old`**；
   发布后重读 `/releases/latest`，漂移就 `PATCH /releases/<id> {"make_latest":"true"}`（L-167）。
4. 上传插件商店资产：`catalog.json`（chart 1.1.2 / 1.1.0、launcher 1.0.0）+ 三个 zip 到
   `plugin-store` tag；**不要动** `dist/plugin-store/chart-1.1.0.zip`。
