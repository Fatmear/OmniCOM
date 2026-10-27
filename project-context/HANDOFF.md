# Current handoff
Updated: 2026-10-27 (UI polish + CLI + full acceptance session)

Current objective: v0.2 — native Qt GUI (**solid colour background**, rebuilt top bar) plus an
AI-facing CLI. All 20 requirements are verified automatically; the only open work is real-hardware
validation.

Requirement IDs to check before acting: R-019 (never build an exe), R-017 (no silent byte loss),
R-020 (solid background, no grid), R-021..R-023 (CLI contract for AI), R-005 (auto-reconnect).

Verified in repository (2026-10-27, after the CLI/polish change):
- `python tools/acceptance_test.py` → **79 checks, 0 failures, 0 uncovered, ALL REQUIREMENTS VERIFIED**
  (protocol 23, CLI 12, GUI 34, visual/delivery 10; 35.8 s). Report: `.artifacts/acceptance.json`.
- `python tools/protocol_selftest.py` → 23/23 (XMODEM 128/1024/empty, YMODEM, ZMODEM 4 KB/70 KB/empty).
- `python tools/ui_selftest.py` → 34/34 ALL GUI SELFTESTS PASSED (adds timed-send and
  `_lost_bytes == 0` checks; the RX check now polls instead of racing a fixed delay).
- `python -m omnicom.cli selftest` → 28/28, `ok: true`.
- `python -m omnicom.cli session --port DEMO --script tests/session-demo.json` → 9/9 steps.
- Background proof: `Aurora` sampled over ~2500 points = a single colour `#04050a`; source has no
  `drawLine(`; window's dominant colour is the same.

Verified on machine (hostname + observation time; recheck on a new machine):
- Windows, Python 3.13.14, PySide6 6.12.0 (Qt 6.12.0), pyserial 3.5, Pillow 12.2.0 — 2026-10-27.
- Serial ports seen: COM1, COM139, COM140 (CH343) + virtual DEMO.

Unknown or blocked:
- Real hardware: unplug/replug auto-reconnect (L-002), actual baud rates, DTR/RTS effects.
- X/Y/ZMODEM interop against real lrzsz / a board bootloader (L-005) — loopback symmetry only.
- ZMODEM *send* to a real `rz` never run against lrzsz.

Working tree: uncommitted changes from this round (run `git status`).

Next safe check: `python tools/acceptance_test.py` → expect `ALL REQUIREMENTS VERIFIED` (79 checks).

Last validation: the acceptance run above. It does NOT prove real-hardware unplug recovery, real
lrzsz interop, or behaviour above ~1 Mbps.

How to run:
- GUI: `start.cmd` or `python run.py` (`--demo` for the virtual port).
- CLI: `python -m omnicom.cli <cmd>` (or `cli.cmd`); documentation: `docs/CLI.md`.
