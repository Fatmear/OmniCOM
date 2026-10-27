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
| D-010 | ~~DWM acrylic backdrop~~ → **flat solid colour** | R-020 forbids grid/blobs and asks for 纯色; a painted flat fill is also cheaper than a blur behind a full-window repaint. `theme.enable_windows_glass` is kept but unused | user asks for translucency again |
| D-011 | Recording writes to `<data>/logs`, commands to `<data>/commands.json` | Both survive app restarts and are easy to find; no registry, no admin rights | user wants per-port profiles |
| D-012 | **CLI stdout is one JSON document; humans live on stderr** | An agent must be able to `json.loads(stdout)` unconditionally, including on failure (R-022). Progress/hints on stderr keep that invariant | an interactive shell front-end is added |
| D-013 | CLI reuses `PortManager` / `run_transfer`, not a second implementation | One code path means a CLI-found bug is a GUI bug (and vice versa); `--port DEMO` gives the agent a hardware-free target (R-023) | never |
| D-014 | `omnicom/selftest.py` owns the protocol loopback | CLI, GUI and the old `tools/protocol_selftest.py` all run the same proof; no duplicated expectations | never |
| D-015 | **Light theme tokens only; no widget hardcodes a colour** | User asked for a white theme. Flipping the palette must not require hunting `rgba(255,255,255,x)` through widgets, so `theme.py` owns every token and `tools/contrast_audit.py` fails the build when a pair drops under 4.5:1 (L-022) | a dark mode toggle is requested |
| D-016 | Title-bar controls are **painted** (`GlyphButton`), not text glyphs | A 28px `QPushButton` with `padding: 7px 13px` has 2px of content box, so the label was elided to nothing — that is why `—  ❐  ✕` looked "gone" while the buttons were still clickable. Painting two lines is font- and size-independent (L-023) | never |
| D-017 | `ToggleSwitch` **accepts** its mouse events | `QWidget` ignores mouse events by default, so the release propagated to `SwitchRow`, which toggled the state straight back — clicking the knob did nothing while clicking the label worked (L-024) | never |
| D-018 | Log prefix is `[HH:mm:ss.mmm]发→◇` / `[HH:mm:ss.mmm]收←◇` | User asked for this exact shape; direction words read faster than RX/TX in a long scroll and the fixed-width prefix keeps payloads aligned (R-024) | user wants a hex-only or minimal mode |
