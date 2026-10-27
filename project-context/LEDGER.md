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
