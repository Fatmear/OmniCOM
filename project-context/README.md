# Project brief
Updated: 2026-11-25

Goal: **OmniCOM — all-in-one embedded serial debugger.** A portable Python/Qt
desktop tool with lossless RX/TX, millisecond timestamps, HEX view, an
independent VT100 terminal, X/Y/ZMODEM transfer, a live dashboard and a plugin
store. A light, flat, frameless window is part of the product identity.

## Read in this order

1. `CAPABILITIES.md`  — what it does today, and what is missing or broken
2. `REQUIREMENTS.md`  — binding rules; IDs are immutable, superseded ones stay listed
3. `WORKLOG.md`       — what the recent sessions changed (newest first, with status)
4. `HANDOFF.md`       — current state, in-flight work, next safe action
5. `LEDGER.md`        — **search** by symptom keyword before investigating anything
6. `DECISIONS.md`     — recurring traps and accepted choices

Do not read `LEDGER.md` end to end; it is an index. Detail lives in `archive/`.

## Archive

- `archive/ledger-detail-2026.md`      — full write-ups for L-270..L-343
- `archive/LEDGER-full-2026-11-25.md`  — the ledger before it was split
- `archive/ledger-index-2026-early.md` — settled rows older than the live window
- `archive/worklog-YYYY.md`            — worklog blocks that left the live window

## Ground rules that shape everything

- Never build or ship a self-built `.exe` (R-019); ship the portable folder (R-083).
- Received serial bytes are never silently dropped (R-017).
- The portable bundle carries its own CA file; a broken build gate blocks release (R-342).
- The theme is light; colours come from `omnicom/ui/theme.py`, never hardcoded.
- Requirements beat an old handoff. A newer user message beats an old requirement.

Historical detail: `git log`, `CHANGELOG.md`, `archive/`.
