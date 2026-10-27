# Current requirements
Updated: 2026-10-27

Source of R-001..R-017: user request on 2026-10-27 (project kickoff). Do not weaken a MUST.

| ID | Priority | Scope | Requirement | Acceptance check | Status |
| --- | --- | --- | --- | --- | --- |
| R-001 | MUST | UI | Feature-complete serial debugger; liquid-glass, premium, modern look | `python tools/ui_selftest.py` + visual check | active |
| R-002 | MUST | UI | Left vertical rail selects the port and exposes every port setting: baud, data bits, stop bits, parity, flow control (none / RTS-CTS / XON-XOFF), DTR, RTS | all controls present and applied to the open port | active |
| R-003 | MUST | UI | Top tabs; tab 1 = serial send/receive terminal, one tab contains X, Y and ZMODEM | tabs rendered, transfer tab performs transfers | active |
| R-004 | MUST | IO | Convenient, accurate, **lossless** RX and TX | byte counters match payload size; no drop path in code | active |
| R-005 | MUST | IO | Detect ports automatically; auto-reconnect with an on/off switch; unplug+replug reopens the same port automatically | unplug/replug with switch on → port reopens, notice shown | active |
| R-006 | MUST | IO | Send a file over the port (raw) | file bytes arrive intact | active |
| R-007 | MUST | IO | Hex send and hex display | both toggles work both directions | active |
| R-008 | MUST | UI | Extensible command list (add / edit / delete / persist) living in the **right sidebar**, which must be **collapsible** | `commands.json` persists; sidebar blocks collapse/expand | active |
| R-009 | MUST | UI | Paste a newline-separated block into a box → auto-fills the command list | paste 5 lines → 5 entries | active |
| R-010 | MUST | UI | Timestamp display with millisecond precision, with an on/off switch | `HH:mm:ss.mmm` column toggles | active |
| R-011 | MUST | UI | Timed/periodic send | interval fires, stop works | active |
| R-012 | MUST | UI | One pause/resume button: RX continues in background, resuming prints everything captured during the pause | pause → data arrives → resume → backlog printed | active |
| R-013 | MUST | IO | Save received data to a file | file grows with received bytes | active |
| R-014 | MUST | UI | Multi-keyword search; hits printed in a right-hand panel; appends live unless printing is paused; resume backfills the pause window | 2 keywords, pause, resume → hits present | active |
| R-015 | MUST | UI | Toggle to print raw/invisible characters (`\r`, `\n`, `\0`, …) | toggling changes rendering only, never the stored bytes | active |
| R-016 | MUST | UI | Command list items support per-item delay and per-item timed send | delay honoured in "send all"; timer repeats | active |
| R-017 | MUST NOT | IO | Never silently drop received bytes, including while printing is paused | code has no discard path; any cap raises a visible notice + counter | active |
| R-018 | MUST | Delivery | Run as a **native Python GUI application** (`python run.py` / `start.cmd`); no browser page, no local web server | window opens from `start.cmd` | active |
| R-019 | MUST NOT | Delivery | Do not ship or depend on a self-built `.exe` — corporate endpoint policy blocks locally built executables | repo contains no `dist/`; `build.py` is documentation only | active |

Superseded:
- R-018 (old: browser UI + local HTTP/WS service) → R-018 (native Qt GUI). Reason: user required a real
  Python GUI ("不是网页版的，是用 python 图形用户界面做"), 2026-10-27.
- R-020 (old: "package to exe with PyInstaller") → withdrawn into R-019. Reason: the built exe is blocked
  by corporate security policy at run time, 2026-10-27. See LEDGER L-006.
