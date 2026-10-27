# Current handoff
Updated: 2026-10-27 (lint sweep + test-isolation fix session)

Current objective: v0.2 — native Qt GUI (**solid colour background**, rebuilt top bar) plus an
AI-facing CLI. All 20 requirements are verified automatically; the only open work is real-hardware
validation.

Requirement IDs to check before acting: R-019 (never build an exe), R-017 (no silent byte loss),
R-020 (solid background, no grid), R-021..R-023 (CLI contract for AI), R-005 (auto-reconnect),
R-008 (collapsible right sidebar).

Verified in repository (2026-10-27, after the lint/isolation change):
- `python -m ruff check .` → **All checks passed** (216 findings cleared this session).
- `python tools/acceptance_test.py` → **84 checks, 0 failures, 0 uncovered, ALL REQUIREMENTS VERIFIED**
  (protocol 21, CLI 13, GUI 37, visual/delivery 13; ~35 s). Report: `.artifacts/acceptance.json`.
- `python tools/protocol_selftest.py` → 23/23 (XMODEM 128/1024/empty, YMODEM, ZMODEM 4 KB/70 KB/empty).
- `python tools/ui_selftest.py` → 35/35 ALL GUI SELFTESTS PASSED (adds real sidebar hide/restore,
  settings isolation and `isHidden()`-semantics checks; the old `hasattr` check was fake).
- `python -m omnicom.cli selftest` → 28/28, `ok: true`.
- `python -m omnicom.cli session --port DEMO --script tests/session-demo.json` → 9/9 steps.
- Background proof: `Aurora` sampled over ~2500 points = a single colour `#04050a`; source has no
  `drawLine(`; window's dominant colour is the same.

Verified on machine (hostname + observation time; recheck on a new machine):
- Windows, Python 3.13.14, PySide6 6.12.0 (Qt 6.12.0), pyserial 3.5, Pillow 12.2.0, ruff 0.17.3
  — 2026-10-27.
- Serial ports seen: COM1, COM139, COM140 (CH343) + virtual DEMO.

Test-harness rules (learned the hard way, L-019/L-020):
- Any script that builds `OmniWindow` must set `OMNICOM_SETTINGS_SUFFIX` (all four entry points do),
  otherwise a test that hides the sidebar/resizes the window leaks into the next run and into the
  user's real session (registry `HKCU\Software\OmniCOM\OmniCOM`).
- Every `ui_selftest.py` check must start with its `R-0xx` id; `suite_gui` now fails R-001 when a
  `PASS/FAIL` line has no requirement id, so a silently dropped check can no longer look like full
  coverage.

Unknown or blocked:
- Real hardware: unplug/replug auto-reconnect (L-002), actual baud rates, DTR/RTS effects.
- X/Y/ZMODEM interop against real lrzsz / a board bootloader (L-005) — loopback symmetry only.
- ZMODEM *send* to a real `rz` never run against lrzsz.

Working tree: changes from this round are committed (see `git log --oneline -5`).

Next safe check: `python tools/acceptance_test.py` → expect `ALL REQUIREMENTS VERIFIED` (84 checks).
