# Retire the compaction working memory experiment

- **Date:** 2026-11-17
- **RFC:** [Sparse Working Memory at Context Compaction](../../sparse-compaction-working-memory-v0.md)
- **Language mirror:** [中文版](2026-11-17-retirement.zh-CN.md)
- **Baseline:** `68fe1788518e7edbf912638dcc421e17b0c9bc31` (main).

## Decision and result

The requester withdrew the experiment after its task and complete-cost benefit
remained unqualified. This is a product retirement decision, not evidence that
structured memory is ineffective in every setting. Preserve the original design,
implementation ledger and evaluation evidence; no new model experiment is needed
for this removal and no private evaluation traces are published here.

Remove the TypeScript sparse-view operation, native capture/restore adapter,
CLI hook branch, Workbench editor and switch, activation guide and its obsolete
screenshots. Mark the RFC retired and correct the pending release guide.

Retain a strict saved-settings decoder at the existing machine-configuration
composition boundary. A prior true or false value remains part of the stored
revision and transaction backup but is omitted from active catalogs and public
projections. Namespace updates are rejected; sibling updates and rollback retain
the old document without reactivating the experiment. Reads perform no migration.
Unknown namespaces and malformed old settings remain fail-closed. An older
implementation can still reactivate its saved opt-in after a downgrade.

Keep ordinary native lifecycle/recovery, Progressive/Delta delivery and exact
capture readers. Existing immutable files and sources are not cleaned up.
Reintroduction requires a new proposal and qualification evidence.

## Validation and limits

- Python configuration, API, Goal-default and context tests: 139 passed,
  10 skipped; the independent historical-reference reader test also passed.
- Actual native Codex compaction against loopback inference: two cases passed
  with prior false/true settings. No experimental memory was injected or created;
  original source bytes were retained. No paid model calls were made.
- TypeScript context delivery, native lifecycle and Turn-envelope tests:
  101 passed; control-plane type checking and the frontend build passed.
- Packaged Workbench with the real isolated backend: old enabled value absent
  from the catalog in English/Chinese; sibling saves, reload and stale-write
  rejection/recovery passed at desktop and narrow widths. No Turn was dispatched.
- Digest ownership guard: head has 16 passing checks and one pre-existing
  consumer-registration failure for `settlement_readback.ts`. The same frozen
  main has 15 passing checks and two failures; deleting the retired view removes
  its private digest matcher. This does not certify the unrelated failing check.
- Scoped Python typing reports one existing `no-any-return` error in untouched
  `goal_storage.py:57`; the frozen main reproduces the same error.
- Quick premerge and scoped lint passed. Packaged validation does not establish
  installed behavior; deployment and merge require their existing owners.
