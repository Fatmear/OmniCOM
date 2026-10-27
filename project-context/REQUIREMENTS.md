# Current requirements
Updated: 2026-10-27

Source of R-001..R-017: user request on 2026-10-27 (project kickoff). Do not weaken a MUST.

| ID | Priority | Scope | Requirement | Acceptance check | Status |
| --- | --- | --- | --- | --- | --- |
| R-001 | MUST | UI | Feature-complete serial debugger; premium, modern look. Title-bar controls (minimise / maximise-restore / close / sidebar) are **painted**, never font glyphs | `ui_selftest`: each title-bar button grabs >= 12 ink pixels offscreen (no fonts installed) | active |
| R-002 | MUST | UI | Left vertical rail selects the port and exposes every port setting: baud, data bits, stop bits, parity, flow control (none / RTS-CTS / XON-XOFF), DTR, RTS. Every on/off row is clickable **anywhere**, including the switch itself | `ui_selftest`: `QTest.mouseClick` on the switch body flips the state | active |
| R-003 | MUST | UI | Top tabs; tab 1 = serial send/receive terminal, one tab contains X, Y and ZMODEM | tabs rendered, transfer tab performs transfers | active |
| R-004 | MUST | IO | Convenient, accurate, **lossless** RX and TX | byte counters match payload size; no drop path in code | active |
| R-005 | MUST | IO | Detect ports automatically; auto-reconnect with an on/off switch; unplug+replug reopens the same port automatically | unplug/replug with switch on → port reopens, notice shown | active |
| R-006 | MUST | IO | Send a file over the port (raw) | file bytes arrive intact | active |
| R-007 | MUST | IO | Hex send and hex display | both toggles work both directions | active |
| R-008 | MUST | UI | Extensible command list (add / edit / delete / persist) living in the **right sidebar**, which must be **collapsible** | `commands.json` persists; sidebar blocks collapse/expand | active |
| R-009 | MUST | UI | Paste a newline-separated block into a box → auto-fills the command list | paste 5 lines → 5 entries | active |
| R-010 | MUST | UI | Timestamp display with millisecond precision, with an on/off switch | `HH:mm:ss.mmm` column toggles | active |
| R-011 | MUST | UI | Timed/periodic send | interval fires, stop works | active |
| R-012 | MUST | UI | **Auto-scroll to latest** switch is the only print control (the pause/backfill button was withdrawn by the user). With it on, the view always follows the newest line — even after the user scrolls up. With it off the view stays put and a floating pill offers a jump back | `ui_selftest`: follows after a manual scroll-up; pill appears when off; `toggle_pause` must not exist | active |
| R-013 | MUST | IO | Save received data to a file | file grows with received bytes | active |
| R-014 | MUST | UI | Multi-keyword search; hits printed in a right-hand panel; appends live unless printing is paused; resume backfills the pause window | 2 keywords, pause, resume → hits present | active |
| R-015 | MUST | UI | Toggle to print raw/invisible characters (`\r`, `\n`, `\0`, …) | toggling changes rendering only, never the stored bytes | active |
| R-016 | MUST | UI | Command list items support per-item delay and per-item timed send | delay honoured in "send all"; timer repeats | active |
| R-017 | MUST NOT | IO | Never silently drop received bytes, including while printing is paused | code has no discard path; any cap raises a visible notice + counter | active |
| R-018 | MUST | Delivery | Run as a **native Python GUI application** (`python run.py` / `start.cmd`); no browser page, no local web server | window opens from `start.cmd` | active |
| R-019 | MUST NOT | Delivery | Do not ship or depend on a self-built `.exe` — corporate endpoint policy blocks locally built executables | repo contains no `dist/`; `build.py` refuses to run without `--i-understand-policy` | active |
| R-020 | MUST | UI | **Light (white) theme**; window background is a **flat solid colour** — no grid, no dot pattern, no animated blobs. Body text keeps >= 4.5:1 contrast | acceptance visual suite paints the backdrop widget alone: >= 98% of pixels are exactly `theme.BG`; palette audited for contrast | active |
| R-021 | MUST | CLI | Ship a CLI that an AI agent can drive unattended, plus documentation written for AI consumption | `python -m omnicom.cli --help`; `python tools/acceptance_test.py` CLI suite; `docs/CLI.md` exists | active |
| R-022 | MUST | CLI | CLI prints **one JSON document on stdout** (`ok` + `error` on failure), human text on stderr, exit 0/1/2 | acceptance suite: success and failure both parse as JSON; failure exit code is 1 | active |
| R-024 | MUST | UI | Each log line reads `[HH:mm:ss.mmm]发→◇payload` for TX and `[HH:mm:ss.mmm]收←◇payload` for RX | `ui_selftest`: regex match on both directions | active |
| R-025 | MUST | UI | With the invisible-character toggle on, **both** RX and TX render `\r \n \0 \xNN`; TX describes the bytes that actually left the port (line end included) | `ui_selftest`: send `AT+...\r\n` with the toggle on → `\r\n` visible on the 发→ line | active |
| R-026 | MUST | UI | **Terminal mode**: a real serial console page where keystrokes go straight out, CR/LF/BS move the cursor, ANSI SGR colours are honoured and CSI junk is swallowed | `ui_selftest`: `login: ` + `root\r\n` renders on one line; ANSI stripped; a key press increments TX | active |
| R-027 | MUST | UI | **Notepad++ style find-all**: searching matches the whole log (history included), every hit is listed with its line number, the list can be blown up to the full window, and double-clicking a hit jumps the log to that line | `ui_selftest`: rows == hits, block jump lands on the right line, fullscreen toggles | active |
| R-028 | MUST | UI | Every drop-down shares one style (painted chevron, same height). For the editable baud box, clicking the field opens the candidate list **and** manual typing still works | `ui_selftest`: chevron paints ink, popup opens on click, `921600` survives as typed text | active |
| R-029 | MUST | UI | The log, terminal and result list use an installed **monospace** font, never the proportional UI face | `ui_selftest`: `horizontalAdvance("i") == horizontalAdvance("W")` | active |
| R-023 | MUST | CLI | CLI can run against the virtual `DEMO` port so an agent can verify behaviour without hardware | `python -m omnicom.cli selftest` (28 checks) returns `ok:true` | active |

Superseded:
- R-018 (old: browser UI + local HTTP/WS service) → R-018 (native Qt GUI). Reason: user required a real
  Python GUI ("不是网页版的，是用 python 图形用户界面做"), 2026-10-27.
- R-020 (old: "package to exe with PyInstaller") → withdrawn into R-019. Reason: the built exe is blocked
  by corporate security policy at run time, 2026-10-27. See LEDGER L-006.
- Background: earlier "aurora blobs + 46px grid" look → R-020 flat solid colour. Reason: user asked for
  "纯色背景，不要搞网格背景", 2026-10-27. See LEDGER L-013.
- R-020 (dark → light): the flat colour is now a light page tone and all surfaces are white. Reason:
  user asked "改为白色主题", 2026-10-27. See LEDGER L-022.
- R-012 (pause/backfill → auto-scroll): the user withdrew the pause button outright ("暂停打印这个功能不需要了，
  我其实要的就是自动滚动开关那个功能"), 2026-10-27. See LEDGER L-028.
- R-002 (single port per session → live switch): picking another port while one is open now switches
  immediately. Reason: user asked "在串口选择框选择了 140，要求能立刻切到 140", 2026-10-27. See LEDGER L-030.
- R-001 (glyph → painted) / R-002 (whole row clickable): user reported the title-bar symbols were
  invisible and a switch could only be toggled by clicking its label, 2026-10-27. See LEDGER L-023/L-024.
