# Retiring post-commit authority observation

The `coordination.authority_shadow` / `file_one_way` observer is retired.
Todo, handoff-mode and task-lease writes no longer re-read the Goal after commit
or create a second observation store. Their primary semantics are unchanged.
The transaction-bound `coordination.runtime_shadow` outbox remains the only
writable shadow lineage; its existing bootstrap, source locks, byte witnesses,
recovery and promotion checks remain mandatory.

This is an intentional behavior change for Goals that opted into the old
observer. Unconfigured Goals remain default-off. A retained old setting is
reported as `enabled=false, status=retired`; malformed settings remain `invalid`
and can also be cleared. The settings catalog shows a read-only retired entry,
without an enable action. A stale settings client or the old CLI enable flag
is rejected before registry writes. An old runtime `.record` RPC returns
`request_rejected / local_authority_shadow_retired` before opening any store.
It must not be retried as a transient storage error.

## Operator transition

Inspect before changing configuration:

```bash
loopx authority-shadow status --goal-id GOAL
loopx configure-goal --goal-id GOAL --clear-local-authority-shadow
loopx configure-goal --goal-id GOAL --clear-local-authority-shadow --execute
```

The clear command removes only the obsolete setting. It neither deletes
retained observations nor changes an existing runtime-shadow capture binding,
provider default, lease, Todo or writer fence. `authority-shadow status` can
still read the retained observer while that old configuration is present and
no runtime-shadow lineage is selected. Explicit legacy-observation reads remain
available through the existing read contract. After clear, status selects the
runtime-shadow candidate; the historical files remain untouched.

To begin transaction-bound capture, explicitly configure and bootstrap it:

```bash
loopx configure-goal --goal-id GOAL --coordination-runtime-shadow-file
loopx configure-goal --goal-id GOAL --coordination-runtime-shadow-file --execute
loopx coordination-shadow bootstrap --goal-id GOAL --execute
loopx authority-shadow status --goal-id GOAL
loopx coordination-shadow inspect --goal-id GOAL
```

Bootstrap can reject unsupported sources, source drift or a previous binding.
Resolve the reported boundary; never copy observation records into the outbox
or remove a promotion fence. A configuration update does not prove bootstrap
or qualify promotion. Runtime-shadow disable/rollback continues to use its
existing management contract; clearing the retired setting is not a rollback
of an active runtime shadow.

State-directory migration continues to exclude historical observation-store
identity and bytes. It no longer seeds a new observer at the destination. The
retained `authority_shadow_seeds` response field reports `outcome=retired` and
`attempted=false` in preview and execution, without auto-enabling a replacement.

Retained data remains readable by its existing format. Reverting this code
can restart the old observer if its setting is still present, so clear the
setting on installations that must not resume observation after rollback.
No format rewrite or destructive data cleanup accompanies retirement.

## Retained readback without source producers

`authority-shadow status` reads retained outbox entries and candidate history
through independent read-only modules. It remains available when the old source
capture producers are absent. Pending prepared/committed files, cursor residue,
invalid evidence and the original management binding remain visible. Residue
counts are diagnostic hints: they do not authorize cleanup or prove delivery.
A missing candidate stays missing; status never creates a replacement identity,
repairs bytes, drains an outbox or grants a writer permission.

The enabled `coordination-shadow inspect`, `qualify` and `read-candidate`
commands likewise call `local_authority_shadow_readback.py` directly. Their
complete source snapshot, default-off gate, bounded qualification and refusal
results retain the same TS owners. Source and normal-wheel checks remove all
four old producers and compare byte-stable observations, insufficient coverage,
missing Todo, source drift, unreadable/missing store and subsequent recovery.
The historical `runtime_shadow.py` imports remain aliases to the same functions.
The retained reader loads inside the selected observation's error boundary,
after the shadow opt-in check. Command registration, disabled shadow and
independent cold-source inspection/import/recovery do not require that reader.
If a required observation reader is absent, its command reports a non-executed
structured error; it does not abort unrelated command registration or substitute
a source producer. Reader availability does not enable capture.
The shared TS qualifier now preserves `provider_protocol_violation` and
`provider_read_unavailable` rather than mislabelling a failed provider read as
`bootstrap_required`. Consequently a corrupt journal makes inspection fail,
instead of reporting a missing baseline; qualification and candidate reads
retain the provider refusal too. Missing and valid stores retain their existing
behavior. Recovery restores the original bytes, without creating a new lineage.
Bootstrap, rollback and promotion still load their supported management adapter;
this reader exit grants no writer cutoff, import or promotion authority.

The retained Python filesystem codecs preserve the existing wire and errors;
candidate proof and drain effects remain in their TypeScript owners. Supported
unpromoted writes and `authority-shadow drain` still need their current producer
and orchestration modules. This separation permits eventual producer retirement
without discarding historical readback, but does not itself authorize deletion
or disposition of pending originals. Code rollback restores the previous import
layout without changing data or restoring retired write authority.

## Why two matching reads were not a capture guarantee

The observer sampled after the writer released its lock. Another writer could
commit first, so the snapshot did not identify the triggering transaction.
A crash between the primary write and the observer could omit that transaction.
Two equal samples only showed temporary stability; they did not close either
window. The existing outbox instead prepares an entry under the source writer's
lock, marks durability, then delivers/reconciles that exact entry identity.
Retiring the observer removes repeated parsing, hashing, retries and a second
candidate history without discarding that stronger capture contract.

## Source lease-reader cleanup

Source capture reads lease bytes once through the snapshot assembler, retaining
the complete record and byte inventory for TypeScript verification. The unused
internal lease-reader helper and its test-only CLI export are removed. The
terminal-record regression now runs through actual source capture, bootstrap
and persisted File history, including unknown fields, absent/null/false values,
cross-Goal rejection and changed-byte rejection before publication.
Public CLI cutover and original-receipt recovery also retain the complete lease
and later canonical work through both File and SQLite providers.

This cleanup preserves live source IO, orphan-history inventory, outbox readers
and codecs, backup import/export and original receipt recovery. It changes no
provider default or supported migration path and performs no data rewrite.
Whole-writer retirement still requires its own last-caller and recovery proof.

## Frontier source-adapter cleanup

The unused internal Python standalone frontier-index builder is removed.
Quota and replan already obtain the complete index from the existing typed
Todo summary transaction. Their tests now enter through that same producer,
including exclusion-only Agent lanes, duplicate identities, Unicode and
microsecond chronology, a complete multi-megabyte source and tail edits beyond
the display cap. Malformed or missing batched indices fail without a second
index request. This removes a test-only transport seam; it changes no shipped
CLI command, checkpoint format or default.

The lossless Python source codec remains: summary composition and long-chain
replan still use its complete material bytes. The TypeScript frontier owner
and its versioned wire behavior remain, including historical revision hashes.
No backup, outbox, receipt or Markdown business writer is deleted. Reverting
the source change restores the internal import without rewriting stored data;
whole-writer retirement retains its separate migration and recovery gates.
