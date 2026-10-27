# Current handoff
Updated: 2026-10-27 (v0.4.0 — controls/theme polish + terminal mode + Notepad++ find-all)

Current objective: v0.4 — native Qt GUI (light theme) with a **terminal mode** page, a
Notepad++-style find-all panel, unified controls, and a corrected auto-scroll/reconnect model.
All 29 requirements are verified automatically; the only open work is real-hardware validation.

Requirement IDs to check before acting: R-019 (never build an exe), R-017 (no silent byte loss),
R-020 (light theme + flat solid background), R-012 (auto-scroll is the only print control — the
pause button is withdrawn), R-024 (log line shape), R-026 (terminal mode), R-027 (find-all),
R-028 (drop-downs), R-029 (mono log font).

Verified in repository (2026-10-27, after this round):
- `python -m ruff check .` → **All checks passed**.
- `python tools/acceptance_test.py` → **106 checks, 0 failures, 0 uncovered, 29 requirements,
  ALL REQUIREMENTS VERIFIED** (~31 s). Report: `.artifacts/acceptance.json`.
- `python tools/protocol_selftest.py` → 23/23.
- `python tools/ui_selftest.py` → 57/57 ALL GUI SELFTESTS PASSED.
- `python tools/contrast_audit.py` → 12 pairs, all >= 4.5:1.
- `python -m omnicom.cli selftest` → 28/28 `ok:true`.
- Layout audit on the real Windows platform: at 1020/1280/1440/1920 the title bar fits
  (rightmost element < bar width); 0 clipped buttons; every visible button renders at 13 px.
- Font audit: log / terminal / results all report an installed mono family (Cascadia Code here)
  and `advance("i") == advance("W")`.
- Reconnect wording: `_friendly_error(PermissionError, "COM139", present=False)` → "已拔出".

Verified on machine (hostname + observation time; recheck on a new machine):
- Windows, Python 3.13.14, PySide6 6.12.0 (Qt 6.12.0), pyserial 3.5, Pillow 12.2.0, ruff 0.17.3
  — 2026-10-27.
- Serial ports seen: COM1, COM139, COM140 (CH343) + virtual DEMO.

Conventions that must not drift (learned the hard way):
- All colours **and control metrics** live in `omnicom/ui/theme.py`. Never call
  `setFixedWidth/Height` on a button that carries text; the QSS owns `min-height`/`padding`.
- Icon/chevron buttons are `GlyphButton` (QPainter). Text glyphs disappear inside small buttons
  and depend on font coverage (L-023, L-033).
- Drop-downs are `widgets.Combo` — it paints its chevron and opens the list on a click into an
  editable field (L-029).
- The log/terminal/results font comes from `theme.mono_font()` **and** must be pinned in that
  widget's stylesheet; the global `*` selector beats `setFont()` (L-032).
- `LogView.autoscroll` on == unconditional follow. Do not reintroduce "only follow if already at
  the bottom" — that was the bug (L-028).
- Any script that builds `OmniWindow` must set `OMNICOM_SETTINGS_SUFFIX`.
- Every `ui_selftest.py` check must start with its `R-0xx` id; coverage is computed from `TITLES`
  in `tools/acceptance_test.py`, so a new requirement needs both.

Known limitations (documented, not bugs):
- The terminal emulator handles CR/LF/BS/TAB, SGR colours, `ESC[K`, `ESC[2J` and ignores every
  other CSI sequence. It is a line console, not a full VT100 (no scroll regions, no alt screen).
- A 49-character command (`AT+QTXPOWER?AT+QBLENAME=...`) still elides in the 346 px sidebar; the
  tooltip shows the whole string.

Unknown or blocked:
- Real hardware: unplug/replug auto-reconnect against a CH343 (L-002/L-036), DTR/RTS effects.
- X/Y/ZMODEM interop against real lrzsz / a board bootloader (L-005) — loopback symmetry only.
- Terminal mode against a real `getty` on the RK3588 (the AT-echo DEMO port only proves the
  plumbing, not a real login prompt).

Working tree: changes from this round are staged for commit (see `git status`).

Next safe check: `python tools/acceptance_test.py` → expect `ALL REQUIREMENTS VERIFIED` (106 checks).

Last validation: the runs above. They do NOT prove real-hardware unplug recovery, real lrzsz
interop, or a real getty login over the terminal page.

How to run:
- GUI: `start.cmd` or `python run.py` (`--demo` for the virtual port).
- CLI: `python -m omnicom.cli <cmd>` (or `cli.cmd`); documentation: `docs/CLI.md`.
