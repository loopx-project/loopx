# Controller-related simplification and retirement handoff

- Parent: [controller/executor RFC, contributor handoff](../../controller-executor-unified-contract-v0.md#responsibility-convergence-and-contributor-handoff).
- Source audit: `27991e27ba998439d7faf2fc113efcfe82882144`, 2026-10-27.
- Status: source-backed work decomposition; no implementation, runtime qualification, installation or default change.
- [Chinese mirror](2026-10-27-refactor-handoff.zh-CN.md).

## Read this before claiming work

The requested payoff is fewer independent decisions, fewer places to change a
rule and removal of superseded production paths. Moving Python into TypeScript,
renaming files or introducing another shared class does not by itself deliver it.
This audit is bounded to the controller's existing execution/session/settlement
callers and adjacent retirement seams; it is not an exhaustive repository audit.

This is an option inventory, not a ranked queue. Select the next outcome against
the overall roadmap, current user direction, demonstrated bottlenecks, active
PRs and canonical work. Do not infer global priority from IDs, row order or a
contributor handoff; no Agent is confined to one permanent lane. A current
correctness or contract/authority-delivery gap can outrank any simplification
here. Explain the selected outcome's value and alternatives in its existing task,
then reassess after delivery or new evidence rather than finishing the list.

If C3.1 is selected, C3.2 can share its PR when splitting creates only a facade.
C4.2 needs the applicable qualified C4.1 boundary, not every other package.
C4.3, C7.1, C8.1 and C9.1 need their stated comparison or retirement proof; reject
or narrow candidates with distinct responsibilities. C9.2 accompanies the owning
implementation, not a new framework or prerequisite project. Packages are not
a PR count. Priority advice creates no extra admission or approval gate.

Before claiming, refresh target main, related PRs and canonical Todos. Record
actual ownership there; this document grants no peer assignment, merge or model-
execution authority. The open-PR scope read at audit time identifies these seams:

| Existing work | Coordination consequence |
| --- | --- |
| [#7430](https://github.com/loopx-project/loopx/pull/7430), [#7457](https://github.com/loopx-project/loopx/pull/7457), [#7461](https://github.com/loopx-project/loopx/pull/7461) | Coordinate with active C1/C2 contract composition/cutover, context references and Host-input work. Do not create another composer or rewrite qualified capture/context delivery; this is scope coordination, not a permanent Agent assignment. |
| [#7456](https://github.com/loopx-project/loopx/pull/7456) | Adopt its independently qualified recovery guards; do not expand the bounded fix into all lifecycle consolidation or treat it as automatic reconciliation. |
| [#7462](https://github.com/loopx-project/loopx/pull/7462), [#7411](https://github.com/loopx-project/loopx/pull/7411), [#7298](https://github.com/loopx-project/loopx/pull/7298) | Existing primary-action, Todo-summary and Vision-wait retirement proposals need reconciliation, not parallel Python/TS migrations. |
| [#3412](https://github.com/loopx-project/loopx/pull/3412), [#5677](https://github.com/loopx-project/loopx/pull/5677) | Settlement readback and Host first-delivery semantics overlap C4.3's boundary. Review current diffs before replacing it. |
| [#6838](https://github.com/loopx-project/loopx/pull/6838), [#6900](https://github.com/loopx-project/loopx/pull/6900), [#7252](https://github.com/loopx-project/loopx/pull/7252) | Research-worker launch/admission and lease-scope fixes retain their existing owners. Coordinate only the actual touched mechanics. |

PR scopes are discovery evidence, not approval or a live status registry. Recheck
them before implementation; do not infer authors' adoption or qualification.

## C3.1 — Shared managed-operation protocol

**Confirmed overlap / misplaced shared code.** `loopx/control_plane/turn_driver/`
`codex_operation_host.py::run_codex_operation_host` and
`dsh_operation_host.py::_run_dsh_operation_host` both check planned session versus
binding/profile, recheck confirmed-operation scope, obtain pending handoffs and
report native execution start. DSH already imports the generic
`operation_tool_handler` from the Codex module: the handler is shared, not two
implementations to rewrite.

- **Target and deletion:** give that existing protocol a host-neutral owning
  boundary and migrate both callers. Remove duplicated common orchestration and
  the internal Codex-specific handler entry/import. Reuse current collaboration
  and Goal-admission decisions; adapters supply native facts rather than minting
  actor identity or approval. Do not create a `BaseHost` hierarchy.
- **Preserve:** Codex native acceptance metadata, DSH connection pin/restore
  limits, initialization races, tool isolation and native resource ownership.
  Equal-looking callback code does not prove equal authority.
- **Decisive acceptance:** both actual operation entries preserve original
  authorized session/operation, pending-item filtering and once-only start;
  changed profile/Todo/Goal or missing acceptance rejects before effect dispatch.
  Extend `tests/test_codex_operation_host.py` and
  `tests/control_plane/test_dsh_operation_entry.py`; retain SDK/connection cases.
- **Dependency / rollback:** existing single-session behavior can refactor before
  C1 without changing its schema. Revert caller adoption and common extraction
  together; no state-format migration or new runtime toggle is needed.

## C3.2 — Cohesive session-binding callers

**Already shared storage, scattered caller composition.** `codex_sessions.py`
already supports Codex and DSH schemas through a host parameter. Operation
adapters still use private read/write helpers and assemble binding/profile
checks around them. `driver.py::reconcile_failed_turn_session_request` and
`session_recovery.py` consume the same managed-session identity.

- **Target and deletion:** reuse this persistence owner and expose only the
  cohesive select/verify/store operations needed by real callers. Retire replaced
  caller-side reconstruction and private cross-host imports. A module rename
  alone is not this package; combine it with C3.1 if no independent payoff exists.
- **Decisive acceptance:** new/resume/fresh, changed profile, concurrent binding
  replacement, checkpoint provenance and existing failure recovery preserve the
  exact session and Goal instance. Validate both native adapters and original
  persisted documents; do not introduce a second session database.
- **Dependency / rollback:** keep existing on-disk paths/wire versions for the
  behavior-preserving slice. C3's later role/generation extension requires stable
  C1 and explicit migration/replay/rollback; never overwrite old native IDs or
  borrow continuity between hosts/roles. Read accepted receipts before reverting.

## C4.1 — Typed receipt-to-continuation boundary

**Remaining cross-language decision ownership.** `loop_controller.py` owns
`ValidatedTurnReceipt`, `BoundedTurnBudget` and deterministic disposition rules;
`managed_step.py` reconstructs an execution-shaped receipt from the journal and
also asks `turn_journal.ts` for consistency. `recovery.py` already wraps that TS
journal decision. Journal recovery and deciding the next admitted action are
different questions; their proximity is not proof they should be one state.

- **Target and deletion:** evolve the existing typed Turn/effect domain to
  compose receipt qualification and deterministic continuation over one coherent
  fact request. Keep separate domain decisions where required, but remove the
  replaced Python reconstruction, admission partitions and policy evaluation.
  Preserve the registered controller contract rather than adding a workflow DSL
  or another runtime operation for each field.
- **Decisive acceptance:** the independent semantics in
  `tests/test_loop_turn_loop_controller.py`,
  `tests/test_loopx_turn_managed_step.py` and journal tests preserve causal
  predecessor, selected work, budget, retry ceiling and early-return ordering.
  Later facts must not become mandatory for a branch that legitimately returns
  earlier. Real run-once/managed-step readback on File/SQLite must retain original
  receipts, no redispatch on saved results and one settlement.
- **Dependency / rollback:** absorb C1's stable contract and qualified recovery
  fixes. Characterization and proven local deletion need not wait for full C1
  benefit experiments. Replace real consumers in the delivery slice; preserve
  historical decoding and original effect readback across code rollback.

## C4.2 — Thin outer-worker semantic adoption

**Candidate for bounded policy convergence.**
`scripts/external_scheduler_worker.py::_host_attempt`, `_host_result` and
`_run_worker_locked` combine local IO with Host retry, pending-replan and
continuation interpretation. They already reuse Host failure helpers. Do not
count that reuse as missing, or equate the outer wake with a governed Turn.

- **Target and deletion:** after C4.1, migrate a supported observable Host path
  to qualified semantic observations/decisions; remove only replaced common
  policy branches. Keep polling/backoff execution, command transport, wake
  identity and original-host settlement with their actual scheduler owners.
- **Decisive acceptance:** `tests/test_external_scheduler_worker.py` plus real
  product entry checks cover saved failure, original replan, lost response,
  repeated wake, stop and retry exhaustion. A shell success/ACK cannot become a
  semantic result; unknown/opaque effects remain unknown. Do not delete the
  pending-intent witness until its crash window is covered by the replacement.
- **Dependency / rollback:** C4.1 and a Host that can observe its original
  attempt. Keep opaque commands on an explicit supported/degraded boundary,
  never a blind automatic retry. Reconcile pending original work before reverting.

## C4.3 — Settlement adapters over existing primitives

**Comparison required, not a proven duplicate transaction.**
`host_adapter_settlement.py` decodes and executes an ordered CLI plan and its
conditions; `host_todo_completion.ts` owns its prepare/finalize semantics.
`turn_driver/settlement.py` instead executes the existing TS settlement reducer
and provider readbacks. Both reuse `SettlementIdentity`, but have distinct
entry obligations. `extensions/governed_capability_execution.py` already calls
`execute_turn_driver_settlement`.

- **Target and deletion:** compare identity, validation, guard/vision,
  completion, durable writeback, spend, closeout and recovery obligations first.
  Reuse matching effect/receipt primitives in the existing owner. Delete a
  second condition/step interpreter only after callers adopt an equivalent
  typed boundary; preserve genuinely Host-specific front-door obligations.
- **Decisive acceptance:** original effect IDs and per-phase durable readbacks
  survive failure before/after commit, response loss, repeated completion and
  Todo-less replan. Use existing MCP, Turn-settlement and external-capability
  integration cases. Distinct transaction boundaries remain explicit.
- **Dependency / rollback:** coordinate #3412/#5677 and stable contract inputs.
  Compare complete-path runtime crossings/costs; moving a tiny condition into a
  new RPC per step is not automatically an improvement. Revert after original
  prepared effects are read back, not by discarding journals.

## C7.1 — Worker/process cleanup at the existing runtime boundary

**Mechanics overlap; semantics need qualification.**
`benchmark/runtime/worker.py` has `child_process`, `_reap_child_process` and
`interrupt_checkpoint_process`; product `host_process_transport.py` owns
supervisor records, drain observations and guarded process execution. Shared
POSIX calls alone do not make their stop/ownership contracts interchangeable.

- **Target and deletion:** reuse suitable existing process primitives and make
  benchmark-specific entry adapters thin. Remove a duplicate launch/cleanup
  implementation only after matching its policy; keep task environment,
  checkpoint behavior, native event decoding and outer wake ownership explicit.
- **Decisive acceptance:** real disposable process groups cover normal exit,
  spawn failure, timeout, descendants, interruption and unknown drain; ACK is not
  exit. Preserve signal ordering, deadlines, descriptor/environment handling and
  supported-platform reporting. Native/plain control arms gain no LoopX admission
  dependency, changed scoring, budget or instrumentation merely through reuse.
- **Dependency / rollback:** coordinate existing worker/admission work; no model
  or benchmark jobs are authorized here. Keep distinct owners if policy matching
  fails. Code rollback preserves already-admitted process and receipt ownership.

## C8.1 — One operational fact source, existing readback surfaces

**Projection candidate, not a replacement authority.**
`control_plane/runtime/session_runtime.py`, `status/lifecycle_projection.py` and
dashboard `features/personal-workspace/goal-activity.ts` adapt activity and work
facts. Some already delegate shared owners. A display grouping, a session's
transport state and work admission are not interchangeable status machines.

- **Target and deletion:** when role-aware session facts exist, trace them to
  App/Chat/CLI. Remove only demonstrated duplicate semantic classifications;
  retain presentation grouping/localization. Do not create another controller
  console, provider setting or state store, or merge legacy material-summary
  classification with native session persistence by name.
- **Decisive acceptance:** the existing packaged journey shows actual work,
  unavailable Host, recovering/unknown state, stop scope and late results from
  the same typed facts. Include populated desktop/narrow first screens; unknown
  is not idle or zero, and audience/redaction boundaries remain intact.
- **Dependency / rollback:** C3/C6 and a proven classification overlap. Retain
  old consumers only for their explicit wire/support window; revert projection
  consumers without granting execution authority or changing runtime settings.

## C9.1 — Retire old lease/source execution paths with proof

**Concrete retained path, not dead code by naming.**
`work_items/task_lease_lifecycle.ts::executeTaskLeaseLifecycle` routes canonical
operations through `canonical_task_lease_lifecycle.ts` and retains source-backed
release, fence-close and historical-receipt recovery. Fresh source-backed writes
and separate verification operations are already refused. The canonical
coordination executor and old adapter already share lifecycle decisions.

- **Target and deletion:** use the shared-authority and TS retirement inventory
  to move remaining normal callers to canonical transactions, then delete obsolete
  writer routing, unreachable mutation code and wrappers. Isolate old-format
  parsing in its required migration/recovery boundary; migrate with backup when
  formats change. This is not permission to remove an active fence or receipt
  reader because its name contains `legacy`.
- **Decisive acceptance:** enumerate imports, dispatch registrations, wire callers
  and retained journals/receipts. Rehearse migration and restore in disposable
  runtimes, including release, fence-close, stale owner, response-loss replay and
  new post-migration writes. Qualify supported File/SQLite and real PostgreSQL
  transaction changes under the existing gates. Markdown remains a readable
  projection, not a second truth or a file to delete for this package.
- **Dependency / rollback:** named retirement/support conditions and the owning
  shared-authority checkpoint. Active recovery remains until replaced or its
  explicit support window closes; absence of local samples proves no global
  absence. This cross-RFC work is not a prerequisite for controller launch.

## C9.2 — Contract tests as a companion simplification

- **Sources:** Codex operation tests, DSH entry/SDK/connection tests, managed-step,
  journal and settlement suites named above.
- **Target and deletion:** share useful existing setup and invariant cases;
  keep thin real-adapter and unique integration/negative tests. Remove duplicated
  fixture builders and repeated complete-flow tests only after mapping their
  assertions. No new general test framework or whole-output snapshot quota.
- **Acceptance / rollback:** derive oracles from independent semantics, preserve
  native acceptance, lost-response and authority-race coverage, and keep a
  representative assertion capable of detecting a broken guard. Fewer test
  functions alone is not success. Change tests alongside their owning production
  replacement; retain per-host failures rather than averaging them away.

## Already shared or intentionally different: do not create duplicate work

| Observed source | Disposition |
| --- | --- |
| `decision_capture.py`, context-delivery owners and #7430/#7457 consumers | Reuse existing capture/full/delta; only qualified C1/C2 caller replacement remains. |
| `benchmark/runtime/sessions.py::BenchmarkSessionWake` | Already a thin `CodexAgentSession` adapter; no second benchmark session owner to extract. |
| `recovery.py`, TS journal inspection and `execute_turn_driver_settlement` | Existing shared owners, not missing frameworks. C4 targets remaining caller/decision duplication. |
| `coordination/task_lease_lifecycle.ts` and `work_items/task_lease_lifecycle_decision.ts` | Transaction execution versus shared pure decision; similar names do not justify collapsing them. |
| Root `execution_profile.py`, Turn `execution_profile.py`, manager `machine_profile.py` | Work-granularity policy, provider/model selection and permissions are different axes. Chat already reuses the managed profile; do not merge by filename or create duplicate defaults. |
| App conversation claim, native session binding, Turn journal and outer wake state | Different authority, lifetime and atomicity. Connect through exact identities/receipts, not one generic status or a new database. |

Each implementation reports applied/deferred/rejected candidates, actual retired
callers/rules, remaining supported recovery and the next representative change's
locality. Preserve the RFC's original full-cost/outcome and default-adoption
acceptance. This source audit did not run the listed runtime suites and does not
establish net deletion, equivalence or performance benefit.
