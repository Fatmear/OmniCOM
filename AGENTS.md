# OmniCOM — Agent entry

Read `project-context/README.md` first, then:
REQUIREMENTS.md (binding) → LEDGER.md (search the reported symptom keywords) → HANDOFF.md (current state).

Rules
- Before diagnosing or changing code, search `project-context/LEDGER.md` for the symptom keywords.
- Requirements live only in `project-context/REQUIREMENTS.md`; update the file in the same turn a requirement changes.
- Received serial bytes must never be silently dropped (R-017). Count and surface any loss in the UI.
- **Do not build or ship an .exe (R-019)**: corporate endpoint policy blocks self-built executables.
  Run from source: `start.cmd`, or `python run.py`, or `python run.py --demo` (虚拟串口).
- Verify commands: `python tools/protocol_selftest.py` (X/Y/ZMODEM loopback, 23 checks),
  `python tools/ui_selftest.py` (57 GUI acceptance checks incl. DEMO port),
  `python tools/acceptance_test.py` (106 checks covering R-001..R-029 + `.artifacts/acceptance.json`),
  `python tools/ui_snapshot.py` (renders `.artifacts/ui.png` for visual review),
  `python tools/contrast_audit.py` (light-theme text contrast must stay >= 4.5:1).
  GUI checks must start with their `R-0xx` id — an un-mapped check fails the run.
- Lint: `python -m ruff check .` must be clean (config in `pyproject.toml`).
  Test harnesses use `OMNICOM_SETTINGS_SUFFIX` so they never touch the real UI settings.
- Before ending a session: replace `HANDOFF.md`, append closed items to `LEDGER.md`.
- CLI for agents: `python -m omnicom.cli <ports|info|send|read|monitor|send-file|transfer|session|selftest>`,
  stdout is one JSON document (R-022); full reference in `docs/CLI.md`. Use `--port DEMO` without hardware.
- The theme is **light (white)**: `omnicom/ui/theme.py` holds every colour token. Never hardcode a
  light-on-dark colour in a widget — run `python tools/contrast_audit.py` after touching the palette.
- Background is a flat solid colour — do not reintroduce gradients, grids or blobs over the whole window (R-020).
- Title-bar / icon controls are painted by `GlyphButton`, never font glyphs: text inside a small
  `QPushButton` is elided away by its padding, and symbol coverage depends on the font (L-023, L-033).
- Every drop-down must be `widgets.Combo`. Never use `setFixedWidth/Height` on a button that carries text;
  control metrics live in `theme` (`CTRL_H`, `ICON_SM`, `ICON_MD`, `ICON_SIZE`).
- The log/terminal face comes from `theme.mono_font()` **and** must be pinned in the widget stylesheet,
  because the global `*` selector in `app_stylesheet()` beats `setFont()` (L-032).
