# Current handoff
Updated: 2026-10-27 (v0.5.0 — i18n, plugins, command groups/export, search modes, help, logo)

Current objective: v0.5 — the product should *feel finished and extensible*: two interface
languages, a plugin system with per-plugin tabs, grouped/exportable commands, richer search,
Help → Documentation/About, the real transparent logo, and a layout that never clips.
All 36 requirements are verified automatically; the only open work is real-hardware validation.

Requirement IDs to check before acting: R-019 (never build an exe), R-017 (no silent byte loss),
R-020 (light theme + flat solid background), R-012 (auto-scroll is the only print control),
R-024 (log line shape), R-026 (terminal mode), R-027 (find-all), R-028 (drop-downs),
R-029 (mono log font), R-030 (search modes), R-031 (groups + export), R-032 (plugins),
R-033 (bilingual, never mixed), R-034 (Help section), R-035 (responsive), R-036 (logo + radii).

Verified in repository (2026-10-27, after this round):
- `python -m ruff check .` → **All checks passed**.
- `python tools/acceptance_test.py` → **145 checks, 0 failures, 0 uncovered, 36 requirements,
  ALL REQUIREMENTS VERIFIED** (~35 s). Report: `.artifacts/acceptance.json`.
- `python tools/ui_selftest.py` → **ALL GUI SELFTESTS PASSED** (96 checks, 0 failures).
- `python tools/protocol_selftest.py` → **23/23**; `python tools/contrast_audit.py` → **12/12 pairs ≥ 4.5:1**.
- `python tools/ui_snapshot.py` → `.artifacts/ui.png` 1440×900, rail 302 / stack 724 / sidebar 374.
- Launch proof: `Start-Process pythonw run.py --demo` stays alive (start.cmd path works).

## What changed this round

| Area | Where | Note |
| --- | --- | --- |
| i18n | `omnicom/i18n.py` | `(zh,en)` table, `tr()`, `set_language()`; language persisted in `ui/language` |
| Dialogs | `omnicom/ui/dialogs.py` | Preferences (language), Docs (manual browser), About (version + copy) |
| Manual | `omnicom/docs.py` | 10 chapters × zh/en; also the source of both READMEs |
| Plugins | `omnicom/plugins.py` | `PluginHost` + `PluginApi`; search paths = repo `plugins/` + `<data>/plugins` |
| Launcher plugin | `plugins/launcher/` | app grid, running-instance badge, focus-or-new choice, own `apps.json` |
| Commands | `omnicom/commands.py`, `omnicom/ui/commands_panel.py` | groups (note/collapse), per-item + per-group export/import |
| Search | `omnicom/ui/search_panel.py` | plain / fuzzy / whole-word / regex + match-case switch |
| Shell | `omnicom/ui/main_window.py` | menu (Settings/Plugins/Help), plugin tabs, responsive metrics |
| Theme | `omnicom/ui/theme.py` | `RADIUS_CTRL=5`, `CHECK_RADIUS=6`, `RADIUS_CARD=9` |
| Assets | `tools/make_logo.py`, `assets/*` | transparent PNG + multi-size ICO, generated from `logo.png` |
| Docs | `tools/make_readme.py`, `README.md`, `README.en.md` | READMEs regenerated from `docs.py` |

## Next steps (in order)

1. **Real hardware pass**: open a physical port, verify capture-to-file byte count and X/Y/ZMODEM
   against a device; the DEMO port only proves the software path.
2. **Second plugin** (the API is proven with one): a candidate is a "script runner" that sends a
   file of AT commands line by line. Keep it in `plugins/` so the copy-a-folder story is exercised.
3. **Plugin error surface**: a broken `plugin.json` currently only logs; consider a red badge on the
   plugin menu entry so a user who copies a bad folder notices immediately.
4. If a third language is ever wanted, `_TABLE` entries are fixed-shape `(zh,en)` tuples — turn them
   into dicts per language first (see `project-context/DECISIONS.md`).

## Do not do

- Do not build or ship an `.exe` (R-019): corporate endpoint policy blocks it. Run from source.
- Do not hardcode a Chinese string in a widget: add a key to `i18n._TABLE` and call `tr()`.
- Do not translate user data (command/group names) — R-033 covers UI copy only.
- Do not call `setFixedWidth`/`setMaximumWidth` unconditionally from `resizeEvent` (L-041).
- Do not forget the `next_()` callback in a new `ui_selftest` step (L-042) — the suite hangs silently.
