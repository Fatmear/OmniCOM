# Current handoff
Updated: 2026-11-03 (1.1.4 published, 1.1.5 Release deleted, source pushed, D: synced)

## Current State

- Source/application version remains **1.1.4**. R-069 now explicitly authorizes
  code commit/push, consolidated 1.1.4 release notes, deletion of the 1.1.5 Release,
  and another D: synchronization. This supersedes L-172's earlier restrictions.
- Code commit **0ac996e0983f960841f56e6880d159f09b7205d4** is pushed to origin/main,
  including the previously untracked `omnicom/update_worker.py` and all tests.
- CHANGELOG and the public release body are one integrated 1.1.4 description,
  without first-release/re-release or numbered-batch narratives (L-181).
- Release https://github.com/Fatmear/OmniCOM/releases/tag/v1.1.4 is public/latest.
  Release 1.1.5 (ID 432391570) is deleted by explicit user request. Releases
  1.1.2/1.1.3 remain public with unchanged bodies/assets; no older release hidden.
- Existing source tags are NOT moved/deleted: v1.1.4 = 33c6d22, v1.1.5 = 8d947ae.
  The existing v1.1.4 tag does not contain these fixes; release notes direct source
  readers to main. Deleting a Release did not delete its Git tag.
- D: contains this exact public portable package (L-182):
  `D:\OmniCOM-1.1.3-win64\OmniCOM`; full backup OmniCOM-backup-20261103-193421.
  289 program files match public ZIP; 1212 data files and 4 plugin files retain
  hashes. Old pending marker disabled only in candidate; original is in backup.
  Program left closed; source path and D: launch path unchanged.
- R-108: original UI stays visible during preparation. Restart confirmation warns
  about time/serial disconnection and defaults to Later; no confirmation, no exit.
- Independent cloned-runtime installer appears before old UI exits, waits for old
  PID, installs/verifies, reaches 100%, hides, THEN launches the target (L-179).
  Branded logo/ICO/taskbar window; `app.exit()`, not guarded `quit()`, ends progress.
- Failure rolls back and never retries forever; user data/plugins protected.
  Legacy downgrade needs no readiness/result acknowledgment; supported targets show
  actual-version result once. Older and same-version ZIPs are distinguished.
- Red NEW, cancellable/retryable download, GitHub web-only version checks remain.

## Verified

- Final build `python tools/make_dist.py --check --zip`: embedded runtime SELFTEST
  OK; 30 real subprocess gates. Genuine Downloads/OmniCOM-1.1.3-win64.zip adds
  downgrade mode: **39 checks**, actual legacy UI visible and user hashes intact.
- Fresh full acceptance: **715 checks / 142 requirements**, 0 failures/uncovered,
  exit 0. Separate GUI rerun **535/535**, protocols **23/23**, contrast **15/15**.
- `python -m ruff check .` and CRLF-aware `git diff --check`: clean.
- Final ZIP/public download **43,038,312 bytes**, SHA-256:
  `fef42ec59435127bbdeec68a2aa875a7c453a6413555347aa8f3b7fbc1f8c1db`.
  Public ZIP CRC valid, no user data, all 38 production modules equal source.
  Asset ID 705371995, filename OmniCOM-1.1.4-win64.zip, empty label.
- API latest AND cache-busted public /releases/latest redirect point to v1.1.4;
  GET Release v1.1.5 returns 404. Existing source tags checked via ls-remote.
- D: program hashes independently compared to the downloaded PUBLIC ZIP; backup
  data/plugins compared again; bundled SELFTEST OK. Active pending marker absent.
- Evidence: `.artifacts/release114-final-{before,published,verified}.json`,
  `sync-latest-result.json`, `acceptance.json`, `update-selftest.json`.
  Pre-replacement/deletion ZIP backups: `.artifacts/release-backup-v1.1.{4,5}.zip`.
  Native installer visuals remain `.artifacts/installer-final-{zh,en}.png`.

## Constraints And Next Check

- No open blocker for this request. Recheck machine paths/processes and remote
  latest before another deployment; `git status --short --branch` checks sync.
- Read REQUIREMENTS and LEDGER first: L-167 (latest/cache), L-171 (interaction),
  L-174/L-175/L-177/L-179 (installer), L-181 (release), L-182 (D: sync).
- R-017 no silent RX loss; R-019 no self-built exe; R-033 bilingual i18n;
  R-069 source 1.1.4; R-108 protected data. Keep remaining old releases public.
- GitHub latest is chronological: verify API plus cache-busted public redirect
  after release edits. Do not hide older releases or force source tags.
- Repeat installer gate with `python tools/acceptance_test.py --only installer
  --bundle dist/OmniCOM --older-package <genuine-old.zip>`; do not run it alongside
  full acceptance, as both wipe the same isolated acceptance data directory.
- Keep `tools/make_screenshots.py` version fixture literal, not derived from
  `__version__`. Use `-B` with bundled runtime probes to avoid changing shipped pyc.
- Physical serial/unplug/real-peer X/Y/ZMODEM still require hardware. Source:
  `start.cmd` or `python run.py --demo`; CLI stdout contract remains binding.
