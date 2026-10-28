# OmniCOM — Agent entry

Read `project-context/README.md` first, then:
REQUIREMENTS.md (binding) → LEDGER.md (search the reported symptom keywords) → HANDOFF.md (current state).

Rules
- Before diagnosing or changing code, search `project-context/LEDGER.md` for the symptom keywords.
- Requirements live only in `project-context/REQUIREMENTS.md`; update the file in the same turn a requirement changes.
- Received serial bytes must never be silently dropped (R-017). Count and surface any loss in the UI.
- **Do not build or ship a self-built .exe (R-019)**; ship the portable folder instead (R-083).
  Run from source: `start.cmd`, or `python run.py`, or `python run.py --demo` (虚拟串口).
  Package: `python tools/make_dist.py --check --zip` → `dist/OmniCOM/` + `dist/OmniCOM-<ver>-win64.zip`
  (bundled CPython runtime + trimmed Qt bindings, so the user installs nothing).
- Verify commands: `python tools/protocol_selftest.py` (X/Y/ZMODEM loopback, 23 checks),
  `python tools/ui_selftest.py` (286 GUI acceptance checks incl. DEMO port),
  `python tools/acceptance_test.py` (335 checks covering R-001..R-084 + `.artifacts/acceptance.json`),
  `python tools/ui_snapshot.py` (renders `.artifacts/ui.png` for visual review),
  `python tools/contrast_audit.py` (light-theme text contrast must stay >= 4.5:1).
  GUI checks must start with their `R-0xx` id — an un-mapped check fails the run.
- Lint: `python -m ruff check .` must be clean (config in `pyproject.toml`).
  Both harnesses set `OMNICOM_DATA_DIR` to a wiped `.artifacts/test-data/<suite>` folder, so a run
  never touches the repo `omnicom-data/` (command library, captures) and stays repeatable (L-116).
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
- Every user-visible string goes through `omnicom/i18n.py`: add a `(zh, en)` entry to `_TABLE` and call
  `tr()`. Chinese mode must show **only** Chinese and English mode **only** English, context menus
  included (R-033). Never translate user data (command / group names). After touching copy, re-run
  `python tools/ui_selftest.py` (R-033 fails on any CJK left in the English UI).
- The user manual lives in `omnicom/docs.py` and is the single source for both the Help dialog and
  `README.md` / `README.en.md` — regenerate with `python tools/make_readme.py` (R-034).
- Plugins are self-contained folders (`plugin.json` + entry module) under `plugins/` or
  `<data>/plugins`; they talk to the host only through the `PluginApi` object. A plugin must never be
  able to crash the window: `PluginHost.load()` reports instead of raising (R-032).
- Do not call `setFixedWidth` / `setMaximumWidth` outright from `resizeEvent`; compare first, or the
  window resizes itself forever and the GUI suite hangs with `propagateSizeHints()` spam (L-041).
- Every `ui_selftest` step must call its `next_()` callback, otherwise the harness waits forever with
  no failure message (L-042).
- Radii come from `theme`: `RADIUS_CTRL` (5) for boxes/buttons, `CHECK_RADIUS` (6) for the checkbox
  indicator (≈ one third of `CHECK_SIZE`), `RADIUS_CARD` (9) for cards. Text boxes stay *sharper* than
  the checkboxes (R-036).
- The three body columns live in `ui.body_split` (a `QSplitter`). Never set a column width from
  `resizeEvent`, and never call `setSizes()` before the splitter has a real width — both were shipped
  bugs (L-041, L-050). Use `reset_layout()` to restore the defaults.
- Every `Signal` added to a widget must be connected in `OmniWindow._wire()`, otherwise the
  symptom is “点了没反应” with no error at all (L-052). `ui_selftest` R-046 proves a
  connection with `signal.disconnect(slot)` (Qt raises when nothing is connected), never by
  merely emitting — an unconnected signal emits silently.
- Never call `setFixedSize()` *before* `setStyleSheet()` on a widget whose QSS sets
  `min-height`/`padding`: the style is applied afterwards and shrinks the widget back to its
  size hint (L-053). Override `sizeHint()`/`minimumSizeHint()` to pin a size the layout obeys.
- Collapsing a command group must stay off the write path: call
  `store.update_group(..., notify=False)`, flip `setVisible` in place, and let `closeEvent` flush it
  (`R-041` / L-045). A 30 ms `os.replace` on the click path is a visible stutter.
- GUI serial lifecycle calls go through `SerialBridge.open/close/update_opts`, not the manager
  directly; driver calls and reader joins must stay off the GUI thread (R-055 / L-069).
- Launcher process scans also run off-thread (L-070); response tests must span its 4 s refresh.
