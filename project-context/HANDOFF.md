# Current handoff
Updated: 2026-11-25 (source: session that added the product-scope layer)

## Now

- **Objective:** keep the project record trustworthy. There are now eight files in
  `project-context/`, split by job, and each has a size budget.
- **New in this cycle:** `PRODUCT.md` — the target scope (45 `S-x.y` feature rows,
  6 `OUT` rows, append-only scope-change log). It answers "what must this product
  do", which `CAPABILITIES.md` (today's reality) cannot answer.
- **Scope discipline is now enforced** in `AGENTS.md` and the skill:
  a feature the user asks for gets an `S-` row **in that turn**; a feature the user
  cancels has its row **deleted** and one line appended to the scope-change log.
- **Shipped:** app **v1.1.7** (Release #452554311, 191022574 B, `sha256 ec71d3a5…`,
  public HEAD 200, `latest=v1.1.7`); plugin **chart 1.1.5** (387671 B,
  `sha256 7d8a29e0…`) in plugin-store #436347358.
- **Working tree:** the scope-layer change is uncommitted; verify with `git status`.

## In flight

| When | Area | What changed | Status |
| --- | --- | --- | --- |
| 11-25 | docs | Added `PRODUCT.md` with 45 scope rows and a scope-change log | **in-progress** |
| 11-25 | docs | Add/cancel-feature rule written into skill, `AGENTS.md`, `templates.md` | done |
| 11-25 | docs | `PRODUCT.md` wired into README order, report budget, validator | done |
| 11-25 | docs | Context rebuilt into seven layers + `archive/` (earlier this session) | done `8148fd5` |

## Last verified

- `report_context.py`: README 43/60, PRODUCT 180/200, CAPABILITIES 70/120,
  WORKLOG 38/45, LEDGER 145/150, DECISIONS 77/120, HANDOFF 60/100 — all inside
  budget; `REQUIREMENTS.md` is a reference file with no cap.
- `validate_context.py`: 672 defined ids, no duplicate ledger ids, no WORKLOG row
  without a status, no duplicate headings in the snapshot files.
- `PRODUCT.md`: 45 scope rows, no duplicate `S-` ids, 9 change-log rows.
- **What this did not prove:** no test asserts the budgets or the scope rules —
  they are enforced by instruction, not by a failing check.

## Open blockers

- R-320 (dashboard refresh under load) — ~5 batches/s, P95 ~249 ms.
- L-327 — history reported lost before the retention cap; needs a real capture.
- R-331 — wrapped chart frames lose continuation fields.
- R-332 — core-plugin recovery path unverified on a real upgrade.
- 5 pre-existing dangling refs (`R-182`, `R-198`, `R-303`, `L-339`) are prose
  mentions in the inherited text, not missing definitions.

## Environment facts (recheck on a new machine)

- Portable runtime is CPython embeddable; `._pth` disables `site`, so user
  `certifi` is invisible — that is why `assets/cacert.pem` must ship (R-342).
- `AGENTS.md` and `project-context/DECISIONS.md` are **CRLF**; the other
  `project-context/*.md` files are **LF**. Writing with a Python `write_text`
  silently converts CRLF to LF and produces a whole-file diff — write bytes.
- `powercfg /requests` needs elevation; read keep-awake state back through
  `SetThreadExecutionState`.
- `dist/client-1.1.4/` and `.artifacts/plugin-bundle/` are stale 1.1.4 copies with
  no CA file — do not test with them.

## Next safe check

1. `python .artifacts/report_context.py` — every index file inside budget, `CR 0`
   for the LF files.
2. `python .artifacts/verify_scope_rule.py` — the scope rule is present in all
   four places (skill, AGENTS, PRODUCT, templates).
3. Commit, then confirm a fresh session can answer "what must this product do,
   what does it do today, what is still open" from `PRODUCT` + `CAPABILITIES` +
   `HANDOFF` alone.
