# Historical outbox recovery correctness

LoopX 2.0 retains the reader and recovery contract for shadow artifacts created
before the Legacy Markdown authority cutoff. It does not retain a producer for
new shadow transactions.

Normal Todo, handoff, Monitor, team-plan, project, and lease operations commit
through canonical File, SQLite, or PostgreSQL authority. An unpromoted Goal is
held with `canonical_authority_required`; it does not change Markdown, create a
lease grant, or append to an old outbox.

## Retained responsibilities

Recovery code may:

- inventory an existing shadow store, outbox, management journal, and rollback
  archive without changing them;
- decode prepared and committed records written by the retired producer;
- compare a retained candidate with its bound source;
- drain or disposition an existing entry from its exact source and receipt
  evidence;
- replay an exact historical management result;
- archive a candidate and its complete outbox under the existing rollback
  contract;
- export source bytes for backup and reviewed cold import;
- inspect and release an exact retained lease or close an exact historical
  fence.

Recovery code may not:

- prepare a new outbox entry for a normal business operation;
- enable a new continuous Markdown capture lineage;
- treat an old candidate as current authority without reviewed migration;
- create, renew, transfer, or broaden an execution lease;
- clear an ambiguous artifact to make inspection pass;
- fall back to Markdown when canonical authority is unavailable.

## Operator commands

Use source inspection before changing any historical material:

```bash
loopx --registry registry.json --format json \
  coordination-shadow inspect-source --goal-id GOAL

loopx --registry registry.json --format json \
  authority-shadow status --goal-id GOAL
```

When status proves that a pre-cutoff outbox has entries, run bounded recovery:

```bash
loopx --registry registry.json --format json \
  authority-shadow drain --goal-id GOAL --max-entries 20 --budget-seconds 30
```

Drain success depends on durable provider and receipt evidence. A cursor is
only a position hint. It cannot authorize deletion by itself. Invalid records,
missing source proof, identity disagreement, or a lost outcome remain explicit
holds.

For an existing candidate, the retained readers remain available:

```bash
loopx --registry registry.json --format json \
  coordination-shadow inspect --goal-id GOAL

loopx --registry registry.json --format json \
  coordination-shadow qualify --goal-id GOAL

loopx --registry registry.json --format json \
  coordination-shadow read-candidate --goal-id GOAL --todo-id TODO_ID
```

These commands report historical evidence. They do not grant current write
authority. New migration uses backup-bound source inspection and reviewed cold
import into an empty canonical provider.

Rollback requires an exact retained selector:

```bash
loopx --registry registry.json --format json \
  coordination-shadow rollback --goal-id GOAL \
  --provider-revision EXACT_REVISION

loopx --registry registry.json --format json \
  coordination-shadow rollback --goal-id GOAL \
  --provider-revision EXACT_REVISION --execute
```

An unfinished historical bootstrap uses its exact operation ID instead of a
provider revision. The selectors are mutually exclusive. Rollback archives the
candidate and the complete Goal outbox before it publishes an inactive result.
It does not start another capture lineage.

## Recovery invariants

### Exact bytes

Inspection and backup hash the original files. Recovery never rewrites a
malformed, unknown, or ambiguous artifact before the operator has exported it.
Symlinks, non-regular files, path escapes, and changed bytes fail closed.

### Exact identity

Every recovery action binds the Goal, runtime root, source path, store identity,
capture lineage, operation ID, and provider revision required by its historical
schema. A result copied from another Goal cannot establish completion.

### Exact receipts

A committed provider transaction and matching receipt can authorize outbox
disposition. A prepared-only entry needs source readback to classify it as
applied or abandoned. Missing or conflicting proof remains pending.

### Idempotent replay

Retrying the same operation returns the durable result. Reusing an operation ID
with different arguments fails. A replay never grants fresh execution
authority and never advances canonical state.

### Bounded work

Drain respects its entry and time budgets. It reports remaining work and can be
resumed. A failed or interrupted batch leaves enough durable evidence for the
next process to classify the exact operation.

### Lock order

Historical management keeps the established cross-runtime lock order. Recovery
must not weaken the canonical writer's maintenance lock or add a second lock
that permits recovery and a canonical commit to race.

## Qualification matrix

| Obligation | Primary coverage |
| --- | --- |
| Producer-free status and drain when no history exists | `test_shadow_drain_native_plan.py` |
| Existing prepared/committed record decoding and exact disposition | `local_authority_shadow_outbox.test.ts`, `shadow_entry_delivery.test.ts` |
| Cursor corruption, replay, and bounded cleanup | `test_shadow_cursor_safety.py`, `shadow_cursor_safety.test.ts` |
| Historical management replay and rollback archive fidelity | `test_shadow_management_variant_e2e.py`, `shadow_management.test.ts` |
| Cold source inventory, original-byte hashing, and import holds | `test_cold_source_inspection.py`, `test_cold_source_import_cli.py` |
| Archive and receipt recovery after producer removal | `test_archive_retry_delivery.py`, `test_archive_dependency_capture.py` |
| Canonical provider business writes and exact replay | `test_canonical_todo_writer_isolation.py`, native File/SQLite/PostgreSQL conformance |
| Installed wheel and source distribution lifecycle | `installed.py` |
| Negative controls for deleted authority paths | `mutants.py` |

Fixtures for recovery tests create the same persisted schemas that old releases
wrote, then invoke only retained readers and disposition owners. A recovery test
must not call a normal business mutation to manufacture its input.

## Retirement boundary

The shared-authority ladder no longer registers Stage 2C capture rows or the
long-running capture soak declaration. Those claims described the deleted
producer, not the current provider architecture.

Historical reports remain valid for the release that produced them. Current
qualification is:

1. provider conformance for File, SQLite, NoKV, and PostgreSQL;
2. public canonical creation and mutation readback;
3. source-free installed-package retirement tests;
4. producer-free recovery tests over existing artifact schemas.

Passing historical shadow inspection does not promote a Goal. Passing
canonical provider qualification does not authorize deleting old backups.
