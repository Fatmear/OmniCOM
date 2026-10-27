# OmniCOM — Agent entry

Read `project-context/README.md` first, then:
REQUIREMENTS.md (binding) → LEDGER.md (search the reported symptom keywords) → HANDOFF.md (current state).

Rules
- Before diagnosing or changing code, search `project-context/LEDGER.md` for the symptom keywords.
- Requirements live only in `project-context/REQUIREMENTS.md`; update the file in the same turn a requirement changes.
- Received serial bytes must never be silently dropped (R-017). Count and surface any loss in the UI.
- **Do not build or ship an .exe (R-019)**: corporate endpoint policy blocks self-built executables.
  Run from source: `start.cmd`, or `python run.py`, or `python run.py --demo` (虚拟串口).
- Verify commands: `python tools/protocol_selftest.py` (X/Y/ZMODEM loopback),
  `python tools/ui_selftest.py` (34 GUI acceptance checks incl. DEMO port),
  `python tools/acceptance_test.py` (all requirements R-001..R-023 + `.artifacts/acceptance.json`),
  `python tools/ui_snapshot.py` (renders `.artifacts/ui.png` for visual review).
- Before ending a session: replace `HANDOFF.md`, append closed items to `LEDGER.md`.
- CLI for agents: `python -m omnicom.cli <ports|info|send|read|monitor|send-file|transfer|session|selftest>`,
  stdout is one JSON document (R-022); full reference in `docs/CLI.md`. Use `--port DEMO` without hardware.
- Background is a flat solid colour — do not reintroduce gradients, grids or blobs over the whole window (R-020).
