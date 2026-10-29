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
  `python tools/ui_selftest.py` (300+ GUI acceptance checks incl. DEMO port),
  `python tools/acceptance_test.py` (R-001..R-100 + `.artifacts/acceptance.json`),
  `python tools/ui_snapshot.py` (renders `.artifacts/ui.png` for visual review),
  `python tools/contrast_audit.py` (light-theme text contrast must stay >= 4.5:1).
  GUI checks must start with their `R-0xx` id — an un-mapped check fails the run.
- Lint: `python -m ruff check .` must be clean (config in `pyproject.toml`).
  Both harnesses set `OMNICOM_DATA_DIR` to a wiped `.artifacts/test-data/<suite>` folder, so a run
  never touches the repo `omnicom-data/` (command library, captures) and stays repeatable (L-116).
  Test harnesses use `OMNICOM_SETTINGS_SUFFIX` so they never touch the real UI settings.
- Before ending a session: replace `HANDOFF.md`, append closed items to `LEDGER.md`.
- CLI for agents: `python -m omnicom.cli <ports|info|send|read|monitor|send-file|transfer|session|selftest>`,
  stdout is **one** JSON document (R-022/R-122) and the exit code equals `ok`; full reference in `docs/CLI.md`.
  Use `--port DEMO` without hardware.
- **The CLI contract is a test surface, not a style choice.**  A command writes exactly one document; the
  only NDJSON producers are `monitor`, `send --repeat N` (N>1) and `send --ports A,B`. Never `_emit()`
  a result and then `fail()` it - that is the `Extra data: line 2` bug (L-150). Build one result dict,
  put `ok`/`error` in it, emit once, and return `EXIT_OK if ok else EXIT_FAIL`.
- `main()` must keep `_utf8_streams()` as its first statement (R-123): without it Windows writes the
  Chinese `error` text in cp936 and every agent-side `decode("utf-8")` raises (L-147).
- Anything that opens a port goes through `PortLock` (`<data>/locks/<port>.lock`, system file lock).
  Two CLI processes on one port must queue, never interleave bytes (R-125 / L-149). `--lock-timeout`
  is the only escape hatch - do not "just try once".
- Reads in `send`/`read` go through `collect(session, duration, settle=...)`, whose idle timer starts
  only **after the first byte**. Starting it at t=0 turns "the device has not spoken yet" into "it is
  finished" and silently truncates the answer (L-148).
- **New commands are unticked** (R-127): `commands._defaults()["enabled"]` and `enabled_items()` both
  default to False. 依次发送 must only fire what the user ticked, and the empty-set case has its own
  message (`cmd.sendall.none`).
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
- A command row sends on **left click only** (R-095): `CommandRow._on_label_release` must keep the
  `event.button() == Qt.LeftButton` guard — Qt delivers the release for every button, so without it a
  right-click transmits (L-127).
- Two font pairs (R-098): interface = `ui/font_family` + `ui/font_px` (`theme.set_ui_font_px`), log =
  `display/log_family` + `display/log_px` (`theme.set_log_px`). Ctrl+wheel over the log is the third
  entry point and goes through `LogView.zoom_font()` → `fontZoomed` → `OmniWindow._on_log_zoom`; never
  change the UI size from the log path.
- Brand block (R-100): the title bar name + tagline are pinned to their **glyph** height with
  `QFontMetrics.tightBoundingRect()` (a `QLabel` box is ascent+descent, so caps-only lines carry 4-8 px
  of slack and the pair looks split and high — L-128). Keep `_fit_brand_lines()` called from
  `TitleBar.retranslate()` too, or Chinese/English taglines of different heights re-open the gap.
- Desktop shortcut (R-097): `omnicom/shortcut.py` builds the `.lnk` through `WScript.Shell` COM (no
  pywin32); the target is `<bundle>/runtime/pythonw.exe <bundle>/OmniCOM.pyz`, the icon is
  `assets/omnicom.ico`, the name is `OmniCOM`, and `make_dist` ships `桌面快捷方式.cmd` +
  `Create desktop shortcut.cmd`. Keep it idempotent (one file, overwritten).
- When editing a method that exists in **two** classes (`retranslate`, `sizeHint`, `closeEvent`), quote
  enough of the body in the anchor, and re-run `python tools/ui_selftest.py` right after: a two-line
  anchor once replaced `OmniWindow.retranslate` with the title-bar version and broke language switching
  and three suites (L-129).

- Capture-to-file mirrors the log window (R-101): `SerialBridge` sets `recorder.text_mode = True`
  and `OmniWindow._log_line()` pushes the composed line into `recorder.write_line()`. Never append
  raw bytes from the GUI path (that is the CLI's mode), and never format the line a second way -
  the string that reached `compose_line()` is the string that must reach the file.
- `tools/ui_selftest.py` is only as good as its `runner.add(...)` list: a step that exists but is not
  registered never runs and its checks silently vanish (34 registrations were once lost, taking the
  send / record / sidebar suites with them - L-132). After touching that file, compare the count.

- The distribution documents itself (R-086): `omnicom.ui.main` forwards `argv[0] == "cli"` to
  `omnicom.cli.main`, and `make_pyz`/`make_dist` copy `docs/CLI.md`, `docs/AI-GUIDE.md` and
  `LICENSE` into the bundle. Never let the two CLI surfaces drift.
- Window show/hide must go through `minimize_with_motion()` / `animate_show()`; the motion changes
  the real geometry, so it has to restore `_motion_target` or the window shrinks each cycle (L-117).
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
- UI state lives in INI files via `ui/settings_store.py` (`settings.ini` for preferences,
  `window-<n>.ini` per window, OS-locked slot). Never reintroduce `QSettings(org, app)`: the
  registry is machine-wide, so a portable copy would inherit an older install (R-088 / L-121).
- Batch command edits go through `CommandStore.move_many/remove_many` (one write), and "is this row
  on screen" is `CommandsPanel.row_reachable()` - `isHidden()` is false inside a collapsed group
  (L-124). Batch mode uses the row's own `pick` box, never the enable switch.
- `SendInput` history triggers on ↑ only when the cursor is on the first line; ↓ restores the draft
  *before* the state is reset (L-125). Shortcut matching uses `QKeySequence.StandardKey`.
- Never override `event()` on `OmniWindow`: it crashed the whole GUI suite at teardown (L-123).
- The send panel has **one** file button (R-113): `btn_file` reads 发送文件 while idle
  and 停止 while a transfer runs (`set_file_busy()` swaps the text, the `Danger` object
  name and calls `_repolish`). Never add a second stop button - it pushed the column
  around and the two states have to share one hit area. The stop state must keep the
  button **enabled**, or there is no way to abort.
- The two option rows share `OPT_LABEL_W` (`label.setFixedWidth`), so the HEX switch and
  the 回车发送 switch start at the same x (R-114). Without it "HEX" is 33 px and "回车发送"
  44 px, and the pair looks ragged.
- 回车发送 is **off** by default (R-094, revised): `SendInput.enter_sends = False` and
  `display/enter_send` defaults to "0". Shift+Enter still inserts a newline when it is on,
  and Ctrl+Enter always sends.
- `LogView` wraps with `QTextOption.WrapAnywhere`, never `WrapAtWordBoundaryOrAnywhere`
  (L-139): a serial payload is one enormous "word", so the word-boundary mode keeps the
  timestamp and `发→◇` alone on the first line and pushes the body to the second.
- Restoring from the taskbar is **instantaneous** (`animate_show()` only stops the motion
  and restores the rect; the alpha is set to 1.0). Any geometry animation on the way *in*
  re-lays out all three columns and the whole window visibly stretches (R-105).
- Never hang "cancel the pending minimize" off `ActivationChange` (L-132/L-138): clicking
  the minimize button of a *background* window activates it first, and the cancel path ate
  the legitimate click. `nativeEvent` / `SC_RESTORE` is the only cancel.
- `omnicom/control.py` is the CLI<->window channel: the window hosts a loopback endpoint
  and `cli.py` talks to it (`WindowSession`) instead of opening the port itself, so the CLI
  and the UI can share COM1. `Server.poll()` hands each request to a handler that may
  return `None` and answer later through the `Deferred` (`read`, transfers). Never block
  the GUI thread inside a handler.
- **Every window publishes its own endpoint** (`<data>/control/window-<pid>.json`, with
  `serial`/`connected`/`slot`). Never go back to one shared `control.json`: the window that
  started first used to swallow every CLI request, so two windows / two ports could not be
  driven independently (R-119 / L-144). `Server.publish()` rewrites the file only when the
  owned port changes - call it from `_on_status` **and** from `_control_snapshot`, because the
  status signal is queued and a CLI reply could otherwise outrun the file.
- The CLI routes **by port name**, never by window age: `pick_endpoint()` prefers the window
  that already owns the port, then an idle window, and otherwise lets the CLI take the port
  itself. `--window <PID>` / `--port COMx` pin it. A window must refuse to swap its current
  port for another one (`_control_open` returns busy) - hijacking tore down the session the
  user was watching.
- `WindowSession._check_binding()` runs on **every** reply: when the window has no port open
  (or owns a different one) the CLI raises 绑定结束. A CLI that kept writing into a closed
  port looked perfectly healthy while losing every byte.
- `_pid_alive()` must use `GetExitCodeProcess == STILL_ACTIVE`; `OpenProcess` succeeds for a
  killed child that the parent still holds, which left a stale endpoint looking alive and the
  CLI never falling back to direct mode (L-143).
- **DTR / RTS default to off** (`DEFAULT_OPTS`, both `SwitchRow`s, CLI `--dtr`/`--rts`). A
  raised DTR resets CH340/CP210x boards through the RESET capacitor, so opening a port used to
  reboot the device (R-120 / L-145). Keep the INI fallback at "0".
- Local-package updates go through `update.stage_local()` -> `stage()`, i.e. the same apply
  path as the network download, so `omnicom-data/` and user plugins are equally protected.
  Version comes from `VERSION_RE` inside the zipped `OmniCOM.pyz`, not from the file name;
  anything without `OmniCOM.pyz` is refused before a single file is written (R-121).
- `cli.fail()` writes `ok`/`error` **last**; putting `"ok": False` first let a payload that
  carried `ok: True` override the verdict (L-143).
- Log line spacing is **not implemented** (L-140). `QPlainTextEdit` ignores every
  `LineHeightTypes` value, a custom `QPlainTextDocumentLayout` does nothing, and the only
  working trick (a document font larger than the stylesheet font) scales the glyphs too.
  Do not add a spacing preference without switching the widget to `QTextEdit` first.
