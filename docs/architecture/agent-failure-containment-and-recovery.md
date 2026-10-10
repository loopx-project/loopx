# Agent failure containment and recovery

[中文版](agent-failure-containment-and-recovery.zh-CN.md)

LoopX has substantial mechanisms for execution failure, stale state, lost
responses and uncertain settlement effects. Containing an incorrect conclusion
and restoring the last correct business state remain partial. A durable record
proves what was recorded; correctness also needs relevant, current validation.

This implementation map is based on source revision
`44931b6d22a50b949d43354e6ea498fb6b68d231`. It describes existing owners and their
limits, introduces no new runtime contract, and does not certify an installed
configuration or production failover. The
[overall roadmap](rfcs/loopx-overall-roadmap-v0.md) owns prioritization, especially
S2 durable authority, S10 reliability and S11 evaluation.

## What is implemented

| Required outcome | Existing mechanism | Remaining boundary |
| --- | --- | --- |
| Detect failure early | Typed admission/identity/lease checks; independent task validation; optional diagnostics and progress review | Most task validation follows the Host's bounded Turn. No general check proves every intermediate conclusion correct. |
| Contain propagation | Unknown-effect holds; lease expiry stops the owned process; source/version checks; current dependency and adoption readback; reviewed memory candidates | These cover specific authority and evidence boundaries. They do not retract every context, artifact or downstream completion derived from an incorrect claim. |
| Recover from trusted state | Durable Turn phases and effect readback; canonical state recovery; explicit Agent/Session continuation | Trust covers identity, recorded phases and declared verification scope. Arbitrary workspace rollback, external-effect rollback and automatic semantic failover are not established. |

An Agent can make a wrong assumption and continue calling tools successfully.
Without a relevant validator or review, the transport, journal and lease may all
remain healthy. This is the central distinction between execution reliability
and reasoning correctness.

## Four execution and evidence chains

### 1. Classify failure before deciding to retry

[`host_failure.py`](../../loopx/control_plane/turn_driver/host_failure.py)
classifies Host failures. `managed_step_receipt_from_journal` in
[`managed_step.py`](../../loopx/control_plane/turn_driver/managed_step.py) rejects
non-retryable failures. `decide_loop_disposition` in
[`loop_controller.py`](../../loopx/control_plane/turn_driver/loop_controller.py)
selects bounded retry, wait or repair from the typed result and remaining budget.

Timeout has a two-attempt ceiling; capacity, overload, rate-limit and transport
failures have a three-attempt ceiling. Backoff is exponential, capped at 300
seconds. These are scheduling hints for the outer caller, not an implicit sleep
or a universal retry loop.

`hostRetryPolicyCheck` and `recoveryDecision` in
[`turn_journal.ts`](../../loopx/control_plane/turn_driver/turn_journal.ts) check
failure metadata, attempt consistency and budget. Explicit failed-Turn retry
retains compatibility for some non-retryable terminal failures;
`output_budget_exhausted` is rejected. This explicit repair surface is different
from automatic retry and does not establish that repeating the task will fix it.
See the [Turn loop contract](../reference/protocols/turn-loop-controller-v0.md).

The benefit is bounded handling of known transient failures. A failed validator
or an unknown effect needs repair or reconciliation rather than blind rerun.

### 2. Validate a Turn, then reconcile settlement effects

`run_loopx_turn_once` in
[`executor.py`](../../loopx/control_plane/turn_driver/executor.py) runs the Host,
then `_task_validation_stage` checks the declared task postcondition before
settlement. Validation failure blocks the successful settlement path. A typed
stop result can make validation `not_required`; that is not a passed validator.

`reduceTurnSettlementTransaction` in
[`settlement.ts`](../../loopx/control_plane/turn_driver/settlement.ts) owns the
ordered durable-writeback → quota-spend → terminal-closeout (when required)
decision. `settlementProviderAction` in
[`settlement_provider.ts`](../../loopx/control_plane/turn_driver/settlement_provider.ts)
decides the current provider action.
An effect intent is prepared before provider I/O. Recovery reads its outcome:

- Committed with matching identity: record the missing checkpoint, without
  repeating the effect.
- Absent: execute the same admitted intent.
- Unknown, unreadable or without a usable resolver: hold later effects.

This prevents a lost response from automatically causing another side effect.
It does not undo file writes or API calls already made inside the Host. It also
does not provide a distributed transaction across arbitrary providers. The
[provider-effect acceptance RFC](rfcs/provider-effect-acceptance-v0.md) remains
design only, with no qualified runtime provider for its stronger guarantees.

`runLeasedHostProcess` in
[`leased_host_process.ts`](../../loopx/control_plane/turn_driver/leased_host_process.ts)
checks execution proof before launch, during renewal and after output. A hung
renewal cannot extend the last proved deadline; expiry aborts and drains the
owned POSIX process group. Windows process-tree termination is explicitly best
effort, so that path cannot inherit the stronger drain guarantee.
This contains lost execution authority, not bad reasoning,
and cannot reverse an already admitted external effect.

### 3. Require current sources and current result use

`_verified_recalled_claims` in
[`Decision Context assembler`](../../loopx/capabilities/decision_context/assembler.py)
matches recalled content to an exact current authority read. Failed reads or
incomplete disposition do not advance the reviewed cursor. This prevents stale
recall from being presented as current source evidence. Exact source agreement
does not prove the source or its interpretation is true.

[`Reward Memory candidate review`](../../loopx/capabilities/reward_memory/candidate_review.py)
checks freshness, conflict and scope during candidate construction. Reviewed
acceptance uses the stored candidate guard; retirement follows the active-record
lifecycle. [`application.py`](../../loopx/capabilities/reward_memory/application.py)
separately gates future recall against the current basis. Retirement does not
retract content already delivered into every
Agent context, undo derived artifacts or reverse effects.

`accepted_result`, `require_dependencies` and `result_relationships` in
[`delegation_results.py`](../../loopx/control_plane/collaboration/delegation_results.py)
bind outputs and consumed inputs to hashes, rerun current validators and
re-evaluate adoption. Changed inputs can make a dependency or adoption
unavailable. Source current-read does not recursively qualify its own incoming
dependencies, so unavailable ancestry can remain hidden from a later consumer.
This readback does not automatically revoke canonical done/accepted state. The separate
[`Todo execution dependency`](../../loopx/control_plane/coordination/todo_execution_dependency.ts)
gate rejects missing, cyclic or unfinished predecessors; `todo_done` is a status
condition, not recursive proof of artifact truth.

The benefit is that old success and message delivery cannot alone establish
current acceptance or actual adoption. General claim refutation and downstream
context retraction are still missing from these inspected owners.

### 4. Resume proved phases or explicitly hand over current work

The Turn journal resumes an incomplete phase from its recorded prefix. It
preserves prepared intents and receipts so that recovery can reconcile an
interrupted settlement instead of dispatching a completed Host again.

`executeTodoContinuation` in
[`todo_continuation.ts`](../../loopx/control_plane/coordination/todo_continuation.ts)
supports explicit Agent/Session handover using current canonical Todo facts,
source session, note fingerprint and acceptance guards. The leased path also
checks execution dependencies; the soft-claim path does not generally provide
that dependency check. Hard-lease handover requires claim transfer and the
receiver's current proof; adoption is
revision-bound and read back. Receiving a note alone grants no execution right.

The note retains attempted approaches and next steps, but its summary is still
sender-provided. Artifact availability is an existence check, not independent
proof of correct content. This is a governed continuation workflow, not an
automatic choice of the last correct workspace followed by rollback and core
replacement.

## What each checkpoint proves

| Checkpoint | Preserved or checked basis | Does not establish |
| --- | --- | --- |
| Turn journal | Goal/Agent/Todo identity, phase prefix, Host result, validation and effect receipts | Correct intermediate reasoning; a snapshot of all files or external effects |
| `checkpoint-context` / vision supplement | Current Goal, Todo, dependencies and source component hashes; unchanged read basis before supplement commit | That the model understood the source or repaired the implementation |
| AuthorityStore checkpoint / reviewed archive restore | Canonical projection, events and lineage; restore to a separate destination | Automatic restoration of the active workspace or all providers |
| Decision Context capture recovery | Reviewed cursor and private capture material within its declared recovery scope | Task or business-state rollback |
| Continuation note and adoption | Current task ownership, note basis, lease and receiver eligibility | That every inherited conclusion is correct |

Relevant sources: [checkpoint context](../../loopx/control_plane/goals/checkpoint_context_io.py),
[read-context snapshot](../../loopx/control_plane/goals/checkpoint_read_context.ts),
[authority archive](../reference/authority-archive.md),
[capture recovery](../../loopx/capabilities/decision_context/capture_recovery.py).

## Detection signals are scoped evidence

[`Reliability diagnostics`](../../loopx/capabilities/reliability_diagnostics/README.md)
is an opt-in, default-off L1 observer. Its projection has no execution authority.
An active stage with at least five minutes of silence is a stall suspicion;
current liveness needs an explicit current `--as-of` value. Without it, status
uses the last event time for historical replay. Repetition compares consecutive
tool names, with a threshold of three, rather than parameters or business meaning.
Its recovered-error count means later step/Turn progress was observed; it does
not prove restored business correctness.

The existing execution path uses
[`replan_history.ts`](../../loopx/control_plane/work_items/replan_history.ts) and
[`replan_semantics.ts`](../../loopx/control_plane/work_items/replan_semantics.ts).
Repeated typed progress across distinct Turns can require replan; a new ACK or
evidence name alone cannot qualify a semantic delta. These rules depend on
reported observations and their evidence owners. They are not a general
correctness oracle. Optional external review is also separate from L1 observation.

## Validation and remaining delivery

Existing regression surfaces include:

- [Host failure](../../tests/test_loopx_turn_host_failure.py),
  [loop controller](../../tests/test_loop_turn_loop_controller.py),
  [journal](../../tests/control_plane_ts/turn_journal.test.ts) and
  [settlement recovery](../../tests/test_loopx_turn_settlement_recovery.py).
- [Decision Context](../../tests/capabilities/test_decision_context_assembler.py),
  [memory recall](../../tests/capabilities/test_reward_memory_agent_scoped_recall.py)
  and [delegation result use](../../tests/test_delegation_result_use.py).
- [Diagnostics](../../tests/capabilities/test_reliability_diagnostics.py) and
  [typed progress](../../tests/control_plane/test_progress_observation.py).

These test designs include failures, stale sources, changed inputs and interrupted
effects. Fixture providers and disposable local state qualify their named
contracts; passing them does not establish live model correction, arbitrary
remote exactly-once effects or production recovery time. Diagnostics deployment,
overhead and long-running non-interference need separate evidence.

The RFCs specify three remaining integration gaps in these partially implemented
paths:

1. [Current-use checks for explicit delegation ancestry](rfcs/shared-goal-alignment-and-governed-amendment-v0.md#38-invalid-evidence-and-affected-consumers):
   refuse new dependent use when its declared basis is unavailable, preserving
   historical completion and bounding traversal/verification cost.
2. [Original-task repair and revalidation](rfcs/composable-state-machines-recovery-verification-v0.md#source-grounded-implementation-sequence):
   carry independent failure evidence to an actionable recovery path, recheck
   the current criterion and reconcile the original effects.
3. [Criterion-bound shadow assessment](rfcs/optional-semantic-assistance-jev-v0.md#detection-result-and-handoff-to-the-owning-rule):
   expose actual evidence coverage, missing basis and separate judgment
   dimensions, without promoting a non-triggering signal to correctness.

The [composable recovery RFC](rfcs/composable-state-machines-recovery-verification-v0.md)
retains the open full M2 recovery boundary. The
[reliability diagnostics RFC](rfcs/long-running-agent-reliability-diagnostics-governed-delivery-v0.md)
owns observer qualification and governed intervention. The
[alignment RFC §3.7](rfcs/shared-goal-alignment-and-governed-amendment-v0.md#37-current-work-and-goal-requirements)
retains the open full-requirement ledger and global-closeout boundary. This map
does not close those acceptances or introduce a parallel roadmap.
