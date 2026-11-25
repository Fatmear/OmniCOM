# Current handoff
Updated: 2026-11-25 (source: session that rebuilt the context files)

## Now

- **Objective:** keep the project record trustworthy. The context files were just
  restructured into global truth (`CAPABILITIES`, `REQUIREMENTS`), local record
  (`WORKLOG`, `LEDGER`) and a snapshot (`HANDOFF`).
- **Shipped:** app **v1.1.7** (Release #452554311, `OmniCOM-1.1.7-win64.zip`
  191022574 B, `sha256 ec71d3a5…`, public HEAD 200, `latest=v1.1.7`) and plugin
  **chart 1.1.5** (`387671 B`, `sha256 7d8a29e0…`) in plugin-store #436347358.
- **Requirement ids to check before touching delivery:** R-019, R-083, R-174,
  R-293, R-335, R-342.
- **Working tree:** the context rebuild is uncommitted; verify with `git status`.

## In flight

| When | Area | What changed | Status |
| --- | --- | --- | --- |
| 11-25 | docs | Rebuilt `project-context/` into seven layered files + `archive/` | **in-progress** |
| 11-25 | docs | Optimised the `project-continuity` skill: three layers, worklog contract, budget rules | done |
| 11-25 | tls | Bundled CA proven in-package; release gate added | done `c05430a` |
| 11-25 | power | Keep-awake proven released on close | done `9102f6d` |

## Last verified

- `make_dist.py --check --zip` passed, including the new in-bundle HTTPS probe
  (`roots 121`, `https 200`) and the 5-cycle installer suite.
- `acceptance_test --only local`: R-342 four checks pass; `ruff check .` clean.
- Extracting the old ledger produced **340 entries, 0 unparsed, no duplicates**
  (L-334/335/339 never existed in the source).
- **What this did not prove:** the rebuilt files were not yet read back by a
  fresh session, and no test asserts the new budgets.

## Open blockers

- R-320 (dashboard refresh rate under load) — still ~5 batches/s, P95 ~249 ms.
- L-327 — history reported lost before the retention cap; direct feed does not
  reproduce it, so a real capture is still needed.
- R-331 — wrapped chart frames lose continuation fields.
- R-332 — core-plugin recovery path not verified on a real upgrade.

## Environment facts (recheck on a new machine)

- Portable runtime is CPython embeddable; `._pth` disables `site`, so user
  `certifi` is invisible. That is why `assets/cacert.pem` must ship.
- `powercfg /requests` needs elevation; read keep-awake state back through
  `SetThreadExecutionState` instead.
- PowerShell 5.1 cannot write UTF-8 without BOM via `Set-Content`; write commit
  messages from Python (`write_bytes`) and keep `project-context/*.md` as LF.
- `dist/client-1.1.4/` and `.artifacts/plugin-bundle/` are stale 1.1.4 copies
  without the CA file — do not test with them.

## Next safe check

1. `python .artifacts/report_context.py` — expects every index file within its
   budget and `CR 0` for the LF files.
2. `git status --short` — expects only `project-context/` and the skill change.
3. Commit, then confirm a fresh session can answer the five self-check questions
   from `CAPABILITIES` + `WORKLOG` + `HANDOFF` alone.
