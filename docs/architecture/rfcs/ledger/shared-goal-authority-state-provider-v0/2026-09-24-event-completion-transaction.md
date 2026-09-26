# Event-owned completion: one publication before capture integration

> Superseded by [Todo event retirement](2026-09-25-todo-event-retirement.md): this historical implementation is removed in #5054.

Baseline: `90f21a5299188d54f984a5313e774c9ac48d6595`. This advances overall
roadmap R5/G2, shared-authority L2/L7 and TS T1/T2. It closes an existing event
writer correctness gap; it does not qualify that writer for shadow capture.

## Reconcile already-delivered work

Transaction-bound Markdown/lease shadow outboxes already exist (#3870).
Complete source assembly moved to TS in #4967; prepared-entry source resolution
and delivery moved to TS in #4968. Those owners must be reused. “Event source
capture is missing” was too broad: the remaining gap is binding the event-log
writer's actual commit to the existing capture lifecycle, then qualifying mixed
writers and whole-Goal recovery. Source assembly is not another remaining PR.
The `event_log_writer_not_bound` hold stays in bootstrap, capture and delivery.

## Behavior and ownership

Previously event-owned Todo completion appended successor add/claim events
before it finished encoding the parent completion. A later error left runnable
successors without a completed parent. Its context check also did not compare
the actual event log under the append lock. Both failures reproduce on the
baseline through the public completion function.

- The event adapter now encodes the existing TS successor proposals, then
  submits successors and completion as one eager batch. Duplicate normalization
  and default/ownership decisions are removed from the Python successor helper.
- `goals/state_event_append.ts` owns whole-batch identity conflicts, replay,
  sequence allocation and source-checksum admission. Python holds the existing
  sibling lock, supplies compact identity/hash facts and retains legacy codecs
  and IO. There is one planning RPC per batch, not per historical event.
- Exact `list`/`tuple` batches validate fully before publication. Atomic replace
  plus file/directory fsync makes the event stream visible as all old or all new
  bytes. Prior bytes and event schemas remain unchanged. Lazy iterables and
  subclasses keep their per-item visibility/reentrancy contract; callers must
  materialize them before requesting an atomic source-bound batch.
- Source drift returns the existing completion validation failure, without a
  successor prefix. A lost publication acknowledgement reports an uncertain
  outcome; read back the original Todo and retry completion. Terminal replay
  re-establishes log durability without generating more successors.

This deliberately changes eager-batch failure/visibility semantics. The log is
logically append-only, but the physical inode is replaced. Consumers must reopen
it; an indefinitely open file descriptor is not a live-tail contract. No event
schema version, capture gate, migration permission or provider default changes.
No frontend setting changes: the public completion result and existing CLI/API
route remain the entrypoints; only failure atomicity and replay are corrected.

## Evidence and cost

Public-entrypoint counterexamples fail on baseline and pass on this change.
Validation also covers late duplicate/invalid events, source drift, concurrent
process batches, pre-replace failure, post-replace fsync failure, exact retry,
historical CRLF/no-final-newline preservation, both successor roles and dry-run.
Existing event-only capture holds and non-Todo supervisor/read consumers remain
covered. A source-projection return-value narrowing fixes an existing mypy
failure without changing its runtime acceptance rules.

The read-only source-copy rehearsal consumed 6,111,476 Markdown bytes and
backfilled 874 events. A disposable registry's real CLI completed a synthetic
Todo with three appended events in 1,531 ms; replay left bytes unchanged and
source digest readback matched. It uses real record variety/volume, not live
Goal configuration, execution or promotion; it does not assert complete archive
capture. `examples/control_plane/event-completion-rehearsal.py` reproduces it.

On the same 707,414-byte detached log, seven warm three-event batches had median
11.33 ms on baseline and 28.94 ms on this change. This pays for admission and
crash durability; it is not a speedup. Existing whole-log reads remain, and
atomic publication adds a whole-file copy. Do not use this legacy adapter as the
future high-throughput provider; retire it with its final caller after migration.
No RPC budget was raised. File/SQLite/PostgreSQL stores are not changed here.

## Remaining local-default delivery program

The conditional estimate remains **5–8 cohesive delivery PRs**, subject to
integration findings and existing open prerequisites. This transaction repair
is a prerequisite within public-writer/capture closure, not grounds to subtract
one complete package. Older 7–9 estimates describe earlier checkpoints.

| Package | PRs | Concrete exit |
| --- | --- | --- |
| Public callers and executor boundaries | 1–2 | Reconcile real CLI/Turn/Chat writers and external-effect consumers; close current-proof/fence gaps and delete replaced Python rules. Reuse current leased handoff/selection/receipt work. |
| Consumer and display closure (D1) | 1 | Integrate merged #4961 projection recovery, #4964 complete-source summary and #4922 snapshot paging; prove missing/stale display recovery through actual clients. Do not reimplement these owners. |
| Selected SQLite profile (D2) | 1–2 | Continue #4224 and coordinate #4931: capacity, crash/restore/upgrade, lag, supported runtime/OS and applicable elapsed soak on the selected profile. Test count is not elapsed soak. |
| Capture continuity and whole-Goal rehearsal (L7/L8, D3) | 1–2 | Bind actual event transactions to prepared/committed outbox identity; prove mixed writers, interruption, drain, old-writer fencing, canonical readback and fenced rollback on File/SQLite. Keep unbound holds until this passes. |
| Default selection and bounded retirement (L9/T4) | 1 | New-Goal creation/settings/install select the qualified profile; migrate approved existing cohorts and delete old business writers only after final callers and compatibility windows close. Markdown import/export/rendering are not obsolete business writers. |

File remains the explicit reference profile and SQLite the long-lived local
candidate. PostgreSQL already has a provider and scoped service factory; real
service authentication, deployment, restore/failover and capacity qualification
remain a separate medium-term package. They are not prerequisites for local
default selection and this PR does not qualify them.
