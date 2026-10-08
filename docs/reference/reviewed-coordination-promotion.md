# Reviewed coordination promotion and recovery

A promotion moves a Goal's Todo/lease coordination authority from the legacy
source to its selected canonical provider. Preview, writer fencing, provider
commit, and acknowledgement are distinct steps. A successful preview is neither
a grant nor evidence that cutover has happened.

The operator can now save the exact preview, execute that plan, and recover its
original transaction without reconstructing intent from a later Markdown view.
The TypeScript coordination boundary owns plan validation, qualification,
fencing and receipt proof; Python only loads the file and transports the request.

## Preview and execute

### Inspect a cold old source

An unpromoted Markdown Goal can be inventoried before enabling capture:

```bash
loopx --format json coordination-shadow inspect-source \
  --goal-id example-goal > old-source-inventory.json
```

This read-only command includes all supported active and archived Todo records,
not just archives needed by the current dependency graph. It preserves full
archived text and supported metadata through the existing record codec. The
source witness binds the registered Goal, state bytes, registry and every lease
file; TS revalidates it under the existing source/maintenance locks. Retained
leases are returned separately from current graph edges. Every `active` lease
requires settlement, including expired leases and leases for removed Todos:
expiry is not proof that the Host stopped. Missing archive roles, duplicate
identities, invalid historical leases and stale sources reject the inspection.
An existing canonical selector/document or writer fence rejects using Markdown
as a cold import source, including when the selected provider is unavailable.

`source_inventory.capture` also inventories the original management operations,
active outbox, runtime store and identity, this Goal's retained rollback stores,
and legacy observation directory. File witnesses use raw byte hashes; malformed
outbox files remain visible without being drained or rewritten. Existing TS
readers validate present active history. Their compact readback is not a copy
of all receipts: preserve the original witnessed files. Missing or altered
completed rollback archives, invalid history and unsafe file layouts refuse
inspection. Interrupted management remains an unfinished original operation.

Read `source_inventory.active_todo_count`, `archived_todo_count`, `capture`,
`retained_leases` and `leases_requiring_settlement`. `import_ready`,
`writer_stop_verified` and `outbox_reconciliation_verified` remain **false**.
There is no `--execute` switch, writer fence, bootstrap, provider initialization,
lease grant or capture receipt. This is an operator CLI prerequisite, not the
complete import journey or a dashboard/Lark operation. The Goal storage owner
still needs the shared frontend inventory, explicit Host/writer stop, original
outbox reconciliation, backup, reviewed target, confirmation and same-operation
import recovery. Existing shadow qualification below retains its original gates.

Keep the JSON private: it contains full Todo text, identities and local source
paths. It is an observation, not a complete historical backup. Preserve the
original source and supported receipts through [configuration backup](configuration-backup.md).
No capability setting is enabled; stopping this inspection needs no rollback.
Delete the operator-owned JSON when it is no longer needed.

### Qualified shadow promotion

Use an explicitly enabled, bootstrapped and qualified runtime shadow. Its
qualification must cover real mutations and required event classes; an empty
shadow or a saved JSON file cannot substitute for that evidence. Fresh CLI
previews default to `preserve`, retaining the current ownership policy while
changing storage authority. Use `--handoff-mode-migration hard_lease` to review
an explicit claim-preserving upgrade. Historical saved v0 hard-only plans
retain their original contract; saved execution never accepts overrides.
Neither strategy weakens source, capture or transaction qualification.
Already canonical Goals can use the separately backed-up
[handoff-mode migration](handoff-mode.md) without promoting again.

```bash
loopx --format json coordination-shadow promote \
  --goal-id example-goal \
  --minimum-operations 3 \
  --require-event-kind todo_update > reviewed-promotion.json

loopx --format json coordination-shadow promote \
  --goal-id example-goal --reviewed-plan reviewed-promotion.json

loopx --format json coordination-shadow promote \
  --goal-id example-goal --reviewed-plan reviewed-promotion.json --execute
```

Inspect `ok`, `promotion.status`, the plan's target provider, source revision,
projection digest and qualification policy before execution. The saved file may
be the entire successful CLI preview or its `promotion.plan.reviewed_plan`
envelope. Keep it in operator-owned local storage: it carries a runtime path and
Goal identity, so it is not a public collaboration artifact.

`--reviewed-plan` owns the operation id and qualification policy. Combining it
with `--minimum-operations` or `--require-event-kind` is an error. A fresh CLI preview defaults to preserving ownership policy.

Execution captures and qualifies the source again under the existing locks. If
the computed plan digest differs, it returns
`local_authority_reviewed_plan_changed` before engaging a writer fence. Review a
new preview after legitimate source changes; do not edit the old digest to force
acceptance. The digest detects changed intent; the durable fence and provider
state establish whether that intent may proceed.

## Registration changes and retry

The Python source adapter binds the current Goal, paths and registered Agent
facts to one registry byte digest. TS rechecks it under the existing cross-runtime
lock and retains that lock through admission/commit. Stale sources return
`source_registry_changed_retry`; changed Agent facts in a saved plan return
`promotion_registration_changed_retry`. Refresh the source and review a new
preview, never edit the digest. A held registry lock returns
`source_registry_busy_retry`, releasing source locks before retry so registry-first
configuration writers cannot deadlock. Ordinary Todo writes gain no new lock.
Project and global registry mutations share the existing marker-plus-kernel lock
protocol, including global sync, Goal activation and deletion. This registry
interoperability applies even without shadow opt-in; read-only previews still
take no mutation lock. A timeout inside the protected operation retains its
original cause instead of being relabeled as registry contention.

This is local source consistency, not an authority grant or cross-host database
transaction. Unrelated registry edits may conservatively require a retry.
Existing persisted receipts/fences remain recoverable without the new witness;
fresh bootstrap, inspect, qualify and promote must recapture it. Pre-promotion
rollback can still quarantine a shadow whose current registration is damaged.

On failure, `legacy_writer_fenced=true` reports an actual retained fence, not
proof this invocation created it. Unknown presence is `null`, never permission
to use legacy writes. Recover with the original plan; do not delete the fence.

## Recover the original cutover

```bash
loopx --format json coordination-shadow recover-promotion \
  --goal-id example-goal --reviewed-plan reviewed-promotion.json

loopx --format json coordination-shadow recover-promotion \
  --goal-id example-goal --reviewed-plan reviewed-promotion.json --execute
```

Recovery resolves the registered Goal and runtime but does not read legacy
Markdown or require the transient shadow opt-in. It requires the exact existing
writer fence. It never creates a missing fence, selects another provider, or
falls back to a legacy source.

| Durable state | Preview | With `--execute` |
| --- | --- | --- |
| No matching writer fence | Reject | Reject |
| Matching fence, no canonical commit, exact qualified shadow retained | `recovery_ready` | Commit and read back |
| Original promotion committed, including a later canonical head | `replayed` | `replayed`; no business write |
| Different canonical initialization or inconsistent receipt lineage | Reject | Reject |
| Provider unavailable | Report provider failure | Report provider failure |

For an uncommitted recovery, the original shadow revision, projection, capture
binding, complete transaction lineage, outbox settlement, operation count and
event coverage must still qualify. Recovery validates these durable facts under
the same maintenance guard used by canonical writers. It does not pretend to
observe fresh source parity after the source has ceased to be authority.

A thrown commit acknowledgement can mean that the provider already committed.
Both promotion paths therefore share one commit/readback implementation. It
attempts the business commit once, then checks the persisted receipt and first
transaction. The receipt body, operation id, cursor, provider revision and
initial projection must agree. A matching proof reports success/recovery even
if later work has advanced the head. A missing or conflicting proof remains a
failure; an unavailable proof read is not silently treated as absence.

The returned promotion revision and cursor identify the original cutover, not
the current head. `executed=false` on a replay means this invocation performed no
business write. Inspect `legacy_writer_fenced` and reconciliation evidence when
an execution fails; do not infer that a failure left legacy writers usable.

## Bounded capture proof transport

A long Goal can exceed the existing 2 MiB RPC response budget before promotion:
sequence recovery used to return a full head and full projections for retained
transactions. The `outbox_read` proof read model now keeps full lineage validation
inside TypeScript, while returning progress, receipts, projection digests and
partition markers. Sequence allocation requests no transaction rows; drain uses
the compact rows. Existing full diagnostic reads retain their default contract.
No transport limit, stored population or transaction validation is weakened.
A pending outbox still blocks promotion; use the existing bounded
`authority-shadow drain --goal-id example-goal --budget-seconds 60` operation
and inspect its result before retrying preview.

History replay retains each transaction's validated released Todo field
manifest. An additive field in a newer release must not invalidate an older
transaction or rewrite its receipt. Only recognized historical manifests are
accepted; record digests, counts, unavailable fields and the remaining composed
projection are still checked. New captures use the current field manifest.

## Product and rollout boundary

This is an operator CLI administration journey. It adds no dashboard, Lark or
managed-Turn automatic migration trigger, settings editor, capability grant or
new provider selector. Those surfaces continue to consume canonical data through
the existing routing/projection contracts after a separately authorized cutover.

File and SQLite use their existing local stores. PostgreSQL follows the same
transaction/readback contract through its service-owned factory; a local CLI
selector alone does not provide a PostgreSQL connection or tenant authority.

The merged claim-preserving migration and saved-plan paths are now exercised
together: historical saved v0 plans and fresh preserve/hard CLI strategies on File/SQLite share
the same qualification and recovery owners.

Default-on promotion, SQLite long-duration qualification, post-promotion export
or rollback, and retirement of remaining Python callers retain their RFC gates.
Recovery is a forward completion/readback operation, not rollback. Do not remove
a live fence, reset canonical storage, or replace the source to make recovery
pass. Before execution, abandoning a saved preview needs no runtime mutation.

## Validation contract

Durable tests cover real File/SQLite CLI preview, saved-plan execution, source
drift, policy override rejection, later canonical writes and recovery after
legacy deletion. The provider conformance suite uses the shared production-scale
fixture in legacy and native record shapes, preserving the complete Todo/lease
population through File, SQLite and a real isolated PostgreSQL server.

Negative receipt tests independently corrupt the receipt index and first
transaction. Interrupted-commit tests distinguish failure before commit from a
lost acknowledgement after commit. These are synthetic fault injections, not a
claim of arbitrary process-death or elapsed-soak coverage. Real local rehearsals
must use read-only captured sources and disposable copies; never promote an
active Goal merely to validate this refactor.

## Drain before promotion

`authority-shadow drain` and post-write inline drains use one bounded TS batch.
It verifies the complete retained lineage and exact source byte witnesses before
advancing a cursor or reclaiming outbox files. A cursor is a checkpoint, not
permission to delete; Todo metadata and original transaction receipts remain
part of the existing complete record contract.

```bash
loopx --format json authority-shadow drain --goal-id example-goal \
  --max-entries 64 --budget-seconds 30
```

`budget_exhausted=true` with pending entries means another bounded invocation is
needed. The budget controls admission of further effects; it does not cancel an
in-flight durable commit or replace the transport timeout. A live primary writer returns `primary_writer_busy` without waiting for
it. `shadow_drain_outcome_unknown` means the transport lost a trustworthy batch
result: inspect status and invoke drain again explicitly. The next invocation
uses persisted receipts; it must not recreate the business write, delete the
outbox, or assume that no candidate commit happened. Other proof failures require
repairing their reported cause before continuing.

The runtime shadow is still a File candidate. Draining it does not select a
canonical provider, promote a Goal, bypass qualification, or create a SQLite
shadow. File/SQLite promotion uses the reviewed journey above. Feature-off
writers with no prior capture state do not start the drain runtime.
