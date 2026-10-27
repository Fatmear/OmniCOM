# Decisions and traps
Updated: 2026-10-27

| ID | Decision or pitfall | Why / evidence | Revisit when |
| --- | --- | --- | --- |
| D-001 | **Native Qt (PySide6) app, single process, no HTTP/WebSocket server** | User rejected the browser UI twice ("要做成exe的", "不是网页版的，是用 python 图形用户界面做"); also removes port-permission trouble | a headless/remote mode is requested |
| D-002 | **Deliver from source only; no `.exe`** | Corporate policy blocks running self-built executables — the built exe fails with "拒绝访问" (L-006) | policy changes, or IT signs the binary |
| D-003 | `QPlainTextEdit` with `setMaximumBlockCount` for the log | Appending 10k+ lines/s through per-row widgets stalls the GUI thread; a text document is O(1) per append | rows must be individually interactive |
| D-004 | Pause buffers **raw chunks**, never parsed lines | Resume must replay exactly what arrived, including a partial line (R-012, R-017) | memory cap hit (capped at 64 MB, counted, surfaced) |
| D-005 | Formatting (hex / escapes / timestamps) happens at render time | The stored bytes stay pristine, so toggles are reversible without re-reading the port (R-015) | never |
| D-006 | `PortManager` owns the port on a reader thread; Qt signals carry data across threads | Emitting a signal is thread-safe; touching widgets from the reader thread is not | never |
| D-007 | Protocol engines are transport-agnostic (`Link` interface) | Enables the in-memory duplex harness → `python tools/protocol_selftest.py` proves sender/receiver symmetry without hardware; `DEMO` port enables GUI tests | real lrzsz interop test exists |
| D-008 | `DEMO` virtual serial device (`--demo`) | Lets the whole UI (AT echo, telemetry, pause, search, timers) be verified with zero hardware, and gives the user something to demo | real hardware always attached |
| D-009 | Per-line timestamp = chunk ts + baud interpolation | MS precision is required (R-010) but one chunk can carry many lines; interpolation keeps ordering sane | hardware timestamps available |
| D-010 | `Theme.enable_windows_glass` uses DWM acrylic, with a painted aurora fallback | Real translucency needs the DWM accent policy; when it fails (non-Windows / API missing) the painted gradient still looks right | Qt exposes a cross-platform blur |
| D-011 | Recording writes to `<data>/logs`, commands to `<data>/commands.json` | Both survive app restarts and are easy to find; no registry, no admin rights | user wants per-port profiles |
