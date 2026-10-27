# Handled-items ledger
Updated: 2026-10-27

One line per item. Append-only by ID; never delete `RULED-OUT` or `REVERTED` lines.
Status: OPEN | FIXED-verified | FIXED-unverified | REVERTED | RULED-OUT

| ID | Symptom keywords | Status | Conclusion (one line) | Evidence |
| --- | --- | --- | --- | --- |
| L-001 | 丢字 / lost bytes / 暂停后缺数据 / 卡顿 | FIXED-verified | Pause buffers raw chunks and replays them on resume; over-cap loss is counted and shown | `ui_selftest` R-012/R-017 checks |
| L-002 | 拔插不重连 / replug reconnect / 串口消失 | FIXED-unverified | `PortManager._monitor_loop` reopens the same path (or matches serial/VID-PID); needs a real unplug test | `omnicom/serial_port.py` monitor loop |
| L-003 | 时间戳重复 / same ms / 多行同时间 / OSError 22 | FIXED-verified | Per-line ts = chunk ts + baud interpolation; `stamp()` takes **milliseconds** (was passing ms to `fromtimestamp`) | `ui/log_view.py:stamp`, `ui_selftest` R-010 |
| L-004 | 换行显示错乱 / \r\n 不可见 | FIXED-verified | Raw bytes stored; `escape_control()` renders `\r \n \0 \xNN` only when the toggle is on | `ui_selftest` R-015 |
| L-005 | zmodem 传不过 / 卡在 ZRINIT / 帧校验失败 | FIXED-unverified | CRC16 was implemented reflected (wrong) and the FCS "seal" appended two zero bytes; fixed to plain big-endian `crc16(data)`. Sender now ignores stale ZRINIT instead of resending ZFILE | `protocol_selftest` 23/23; no lrzsz interop yet |
| L-006 | exe 无法运行 / 拒绝访问 / Access denied / 打包失败 | RULED-OUT | PyInstaller build itself succeeded (2.0 MB, no errors) but Windows refuses to launch the produced exe ("程序无法运行: 拒绝访问", `cmd` returns rc=0 with no window). Corporate endpoint policy blocks self-built binaries → do not retry packaging | R-019, session 2026-10-27 |
| L-007 | DEMO 串口卡住 / 发 AT 无应答 / 遥测停止 | FIXED-verified | `DemoDevice.write()` held the non-reentrant lock while calling `_respond()`, which re-acquires it → self-deadlock. Now collects lines under the lock and answers outside | `omnicom/serial_port.py` DemoDevice.write |
| L-008 | 命令列表行数虚增 / 删除后仍统计到旧行 | FIXED-verified | `deleteLater()` keeps children findable until the event loop runs; `setParent(None)` first | `ui/commands_panel.py::rebuild` |
| L-009 | 窗口位置不恢复 / setGeometry 类型错误 | FIXED-verified | `QMainWindow` needs `restoreGeometry(QByteArray)`, not `setGeometry` | `ui/main_window.py::_restore_settings` |
| L-010 | Could not parse stylesheet / 徽标样式失效 | FIXED-verified | An f-string with `{{ }}` covered only the first line of a multi-line QSS, leaving stray braces | `ui/main_window.py` status chip |
| L-011 | 侧栏标题边框外溢 / 整块变粗线 | FIXED-verified | Unscoped `border-bottom` on a container applied to all children; now scoped to `#SideHeader` | `ui/commands_panel.py`, `ui/search_panel.py` |
| L-012 | QFontDatabase cannot find font directory | RULED-OUT | Harmless offscreen-only warning; Qt ships no fonts. Real windows use system fonts | `tools/*selftest*.py` logs |
| L-013 | 网格背景 / 背景太花 / grid | FIXED-verified | `Aurora` now paints one flat colour; grid loop and radial blobs removed. Proven by pixel sampling of the backdrop widget | `tools/acceptance_test.py --only visual` R-020 4/4 |
| L-014 | 顶部栏难看 / 元素散乱 / 标题重复应用名 | FIXED-verified | Top bar became one `GlassFrame` bar (h=62) with brand tile, segmented tabs, `StatusPill`, rich-text counters and icon buttons; `applicationDisplayName` removed (Qt appended it to the title) | R-001 checks in acceptance report |
| L-015 | GUI 自测首帧偶发 0 字节 / 时好时坏 | FIXED-verified | Test read `rx_bytes` after a fixed 700 ms while DEMO emits every ~700 ms — replaced with a 4 s poll | `tools/ui_selftest.py::step_rx` |
| L-016 | 验收脚本少跑一段检查 / 结果缺 R-001/R-019 | FIXED-verified | An edit replaced the function tail, silently dropping the top-bar/delivery checks; restored and added token-count assertions to the patch script | `tools/acceptance_test.py` 79 checks |
| L-017 | CLI 与 GUI 传输行为可能不一致 | FIXED-verified | Transfer code moved to `omnicom/transfer.py` (Qt free); GUI worker and CLI both call `run_transfer()` | `omnicom/transfer.py`, `omnicom/ui/workers.py` |
| L-018 | F821 undefined name `state` / 上传事件分支永不执行 | FIXED-verified | `_on_upload` had `elif event in ("done", "error")` — comparing a **dict** to strings, always false, and the body referenced an undefined `state`. Dead branch deleted; completion is already reported by `RawFileWorker` signals | `python -m ruff check . --select F821` → clean |
| L-019 | 侧栏启动就没了 / 重启后右侧栏消失 / 验收 R-008 不通过 | FIXED-verified | Two causes. (1) `closeEvent` used `isVisible()`, which is false whenever an **ancestor** is hidden, so closing while the window was not visible persisted `ui/sidebar=0`. Now `isHidden()` (widget's own flag) decides. (2) Test harnesses wrote into the real `QSettings("OmniCOM","OmniCOM")`, so `ui_selftest` hiding the sidebar leaked into the next run. New `ui_settings()` honours `OMNICOM_SETTINGS_SUFFIX`; all four entry points use `-test` | `ui_selftest` 3 new R-008 checks; acceptance 84 checks / 0 failures |
| L-020 | 检查被悄悄删掉但验收仍报全覆盖 | FIXED-verified | `suite_gui` only counted `PASS/FAIL` lines carrying an `R-0xx` id; an untagged check was invisible. Added a guard that fails R-001 when any `PASS/FAIL` line lacks a requirement id — it immediately caught one real untagged check | `tools/acceptance_test.py::suite_gui` |
| L-021 | lint 噪音掩盖真实缺陷 / ruff 大量告警 | FIXED-verified | 216 findings → 0. `E501`/`BLE001`/`RUF00x` are deliberately ignored (CJK strings; long-lived worker threads must catch broad exceptions); config lives in `pyproject.toml`. The sweep also exposed L-018 | `python -m ruff check .` → All checks passed |
