# Current handoff
Updated: 2026-10-27 (implementation + verification session)

Current objective: OmniCOM v0.1 is feature-complete as a **native PySide6 desktop app** running from
source. Remaining work is hardware validation, not new features.

Requirement IDs to check before acting: R-019 (never build an exe), R-017 (no silent byte loss),
R-012/R-014 (pause backfill), R-005 (auto-reconnect), R-002 (full port config), R-018 (native GUI).

Verified in repository (2026-10-27):
- `python tools/protocol_selftest.py` → **23/23 PASSED** (XMODEM 128/1024, YMODEM, ZMODEM incl.
  70 KB and empty file, CRC vectors, seal-then-verify property).
- `python tools/ui_selftest.py` → **31/31 PASSED** ("ALL GUI SELFTESTS PASSED") against the DEMO port:
  port list, open, RX print, ms timestamp, TX counter, AT reply, `\r\n` escape toggle, HEX view,
  pause (no new rows) + background RX + resume backfill, 2-keyword search + "all" mode, paste-import
  5 commands, per-item timer start/stop, record-to-file byte equality, raw file send 10240/10240,
  sidebar collapse, command persistence.
- `python tools/ui_snapshot.py` → `.artifacts/ui.png` (1440x900) with a sane layout:
  rail 302px / stack 752px / sidebar 346px, command list 402px tall, hits 192px.
- Static: `python -m compileall -q omnicom` clean.

Verified on machine (hostname + observation time; recheck on a new machine):
- Windows, Python 3.13.14, PySide6 6.12.0 (Qt 6.12.0), pyserial 3.5, Pillow 12.2.0 — 2026-10-27.
- Real ports present: COM1, COM139, COM140 (no device opened during the session).

Unknown or blocked:
- Real hardware behaviour: unplug/replug auto-reconnect (L-002), actual baud rates, DTR/RTS effects.
- X/Y/ZMODEM interop against real lrzsz / a board bootloader — only loopback symmetry is proven (L-005).
- ZMODEM *send* to a real `rz` has never run against lrzsz; `receive` was only exercised against our own sender.

Working tree: uncommitted (first commit not yet made). Run `git status`.

Next safe check: `python tools/ui_selftest.py` → expect `ALL GUI SELFTESTS PASSED` (31/31).

Last validation: `python tools/ui_selftest.py`, 31/31, 2026-10-27. It does NOT prove real-hardware
unplug recovery, real lrzsz interop, or behaviour at >1 Mbps.

How to run: `start.cmd` (no console) or `python run.py`; `python run.py --demo` for the virtual port.
