# Current handoff
Updated: 2026-10-27 (white theme + log format + switch/icon fixes session)

Current objective: v0.3 — native Qt GUI in a **light (white) theme**, `[HH:mm:ss.mmm]发→◇` log
layout, painted title-bar controls, plus the AI-facing CLI. All 25 requirements are verified
automatically; the only open work is real-hardware validation.

Requirement IDs to check before acting: R-019 (never build an exe), R-017 (no silent byte loss),
R-020 (light theme + flat solid background), R-024 (log line shape), R-025 (TX shows invisible
chars too), R-005 (auto-reconnect), R-008 (collapsible right sidebar).

Verified in repository (2026-10-27, after this round):
- `python -m ruff check .` → **All checks passed**.
- `python tools/acceptance_test.py` → **90 checks, 0 failures, 0 uncovered, 25 requirements,
  ALL REQUIREMENTS VERIFIED** (protocol 21, CLI 14, GUI 43, visual/delivery 12; ~36 s).
  Report: `.artifacts/acceptance.json`.
- `python tools/protocol_selftest.py` → 23/23 (XMODEM 128/1024/empty, YMODEM, ZMODEM 4 KB/70 KB/empty).
- `python tools/ui_selftest.py` → 41/41 ALL GUI SELFTESTS PASSED.
- `python tools/contrast_audit.py` → 12 pairs, all >= 4.5:1 (lowest 4.61).
- `python -m omnicom.cli selftest` → 28/28, `ok: true`.
- `python -m omnicom.cli session --port DEMO --script tests/session-demo.json` → 9/9 steps.
- White-theme proof: backdrop widget alone is 100% `#EDF1F7`; a real-platform window render has
  mean brightness 246/255 with 0.26% dark pixels.
- Icon proof: offscreen (no fonts installed at all) the four title-bar buttons still paint
  22/104/57/104 ink pixels — they are `QPainter`-drawn, not glyphs.

Verified on machine (hostname + observation time; recheck on a new machine):
- Windows, Python 3.13.14, PySide6 6.12.0 (Qt 6.12.0), pyserial 3.5, Pillow 12.2.0, ruff 0.17.3
  — 2026-10-27.
- Serial ports seen: COM1, COM139, COM140 (CH343) + virtual DEMO.

Conventions that must not drift (learned the hard way):
- All colours live in `omnicom/ui/theme.py`; never hardcode one in a widget. Run
  `python tools/contrast_audit.py` after touching the palette (it is also part of acceptance).
- Title-bar / icon buttons: use `GlyphButton`. Text inside a 30px button is elided away by
  `QPushButton` padding (L-023).
- Custom `QWidget` subclasses that react to clicks must `event.accept()`, otherwise the event
  propagates to the parent and the state flips twice (L-024).
- Any script that builds `OmniWindow` must set `OMNICOM_SETTINGS_SUFFIX`, or it writes into the
  user's real settings (`HKCU\Software\OmniCOM\OmniCOM`).
- Every `ui_selftest.py` check must start with its `R-0xx` id; `suite_gui` fails R-001 when a
  `PASS/FAIL` line has no requirement id.
- Coverage is computed from `TITLES` in `tools/acceptance_test.py`: add a requirement there **and**
  at least one tagged check, or it shows up as "未覆盖" (L-027).

Unknown or blocked:
- Real hardware: unplug/replug auto-reconnect (L-002), actual baud rates, DTR/RTS effects.
- X/Y/ZMODEM interop against real lrzsz / a board bootloader (L-005) — loopback symmetry only.
- ZMODEM *send* to a real `rz` never run against lrzsz.
- The `◇` after `发→`/`收←` is a deliberate data-start marker; drop it if the user prefers.

Working tree: changes from this round are staged for commit (see `git status`).

Next safe check: `python tools/acceptance_test.py` → expect `ALL REQUIREMENTS VERIFIED` (90 checks).

Last validation: the acceptance run above. It does NOT prove real-hardware unplug recovery, real
lrzsz interop, or behaviour above ~1 Mbps.

How to run:
- GUI: `start.cmd` or `python run.py` (`--demo` for the virtual port).
- CLI: `python -m omnicom.cli <cmd>` (or `cli.cmd`); documentation: `docs/CLI.md`.
