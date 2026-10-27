# Project brief
Updated: 2026-10-27

Goal: **OmniCOM — All-in-One Embedded Serial Debugger**. A native Python (PySide6/Qt) desktop
serial debugger with a liquid-glass look: lossless RX/TX, auto port detection + auto-reconnect,
hex + millisecond timestamps, invisible-character display, collapsible right sidebar with an
extension command list and multi-keyword search, pause-and-backfill printing, receive-to-file,
and X/Y/ZMODEM transfers.

Read in order: REQUIREMENTS.md (binding) → LEDGER.md (already investigated; search it first) →
HANDOFF.md (current) → DECISIONS.md (relevant entries).

How to run: `start.cmd` (silent) or `python run.py`; add `--demo` for a virtual DEMO port with
AT replies and telemetry — no hardware needed. **Never build an .exe** (R-019).
CLI for scripts/AI: `python -m omnicom.cli ...` — stdout is one JSON document; see `docs/CLI.md`.

Architecture: one process, one Qt event loop.
`omnicom/serial_port.py` owns the port on a reader thread and pushes events through
`omnicom/ui/workers.py::SerialBridge` signals (Qt queues them to the GUI thread).
`omnicom/proto/*` are transport-agnostic engines; `omnicom/transfer.py` runs them and is shared by
the GUI worker and the CLI; `omnicom/selftest.py` proves them over an in-memory duplex loopback.

Historical evidence: `git log`, `project-context/HANDOFF.md` rounds, `omnicom-data/`. Never treat an
old handoff as overriding a newer user requirement.
