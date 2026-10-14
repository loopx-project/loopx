# Registered source treatment at the integrated main baseline

Current runtime baseline: `60d833b13c85135587b725013b247e827663ffb9` (2026-10-13).
This updates the existing #6270 inventory after the typed-failure, selected-work,
legacy-work-basis and reserved-value changes reached main. The production
`loopx/`, `apps/` and `packages/` trees in this source-evidence candidate are
identical to that main; only its existing witnesses and records are added.
The [staged scope revision](https://github.com/loopx-project/loopx/pull/4789)
remains the acceptance boundary. **26 rows inventoried is not 26 formal producer
proofs or tracker closure.** The source-inventory PR still needs integration
and final owner closeout. The original three Host-ACK fixture failures have
already been repaired on main through #6344; #6285 is not an additional merge
prerequisite for those same cases.

The registry still contains 26 entries, including `task_lease_lifecycle_operation`
in place of retired `lease_action`. F1/F2 remain **6/26**: five kernel entries
and the settlement-binding witness. The other twenty cross-runtime entries do
not gain F1/F2 credit from this document or from decoder tests. All owner symbols
and exact admitted values remain in `loopx/semantics/vocabulary_v0.json`; paths
in the second column below are relative to `loopx/control_plane/`.

A runtime witness executes the shipped decision from causal facts. An injected
value witnesses a caller-input/decoder boundary only. Mixed rows retain both
obligations. Legacy acceptance needs a retained reader and an exit condition;
no row is relabeled local-only to avoid a crossing it actually has. These are
bounded source treatments, not whole-program reachability, data-flow or F6 proof.

## Source and compatibility inventory

| Registered entry | Owner / slot | Executed source treatment | Compatibility and remaining boundary |
| --- | --- | --- | --- |
| turn_result_kind | `turn_driver/executor.py`, `transaction.py::_result_kind` → generated result kind | Internal result selection plus caller/Host result input. Driver/executor/transaction tests exercise results and malformed input; the registered fixed decoder witnesses every admitted value. | Host input acceptance is not proof a live Host emitted it; journal/replay readers remain. |
| turn_route | `turn_driver/driver.py`, `loop_controller.py` → plan/envelope route | Internal route selection. Driver and loop-controller tests exercise host-ready, gated, repair, wait and invalid routes; generated route projection is checked. | Keep the three Turn vocabularies and explicit projection; a route is not a stored receipt. |
| loop_disposition | `turn_driver/loop_controller.py::decide_loop_disposition` → disposition | Internal decision from current route/result/iteration facts; loop-controller table and negative/retry tests execute the owner. | Generated controller rules remain distinct from route and result; no spelling-only merge. |
| agent_scope_frontier_action | `agents/agent_scope.py` → frontier action | Internal classification of candidates, dependencies and handoff; agent-scope frontier tests execute exhausted/wait/reassignment/successor branches. | Generated TS binding is a consumer, not another producer; no claim of whole-goal completion from an exhausted lane. |
| effective_action | `quota/decision_summary.py` and registered overlays → final action | Internal decision/overlay output. Quota decision tests execute current paths; the fixed producer scan still has explicit unresolved sites. | The root decision/frontier union and nested replay slots stay distinct. Source treatment does not eliminate scanner unknowns or prove every branch reachable. |
| settlement_binding_kind | `effect_program.ts::settlementIdentity` → binding_kind | Internal normalization of supplied identity. Existing settlement-binding witness executes Todo, autonomous-replan and unbound cases plus contradictions. | Unbound is not a permit to spend; exact identity remains the existing receipt owner’s responsibility. |
| task_lease_lifecycle_operation | `work_items/task_lease_lifecycle_request.ts` → operation | Caller-selected native input; lifecycle tests execute renew/transfer/release, terminal_verify/holder_verify/fence_close, bad input, CAS and retry. | Replaces retired private Python lease_action, not lease behavior. Native persisted fence receipts and omitted legacy optional fields remain readable; no F1/F2 enrollment. |
| settlement_step_kind | `effect_program.ts`, `turn_driver/settlement.ts` → receipts[].step_kind | Mixed internal receipt construction and external envelope input. Stage 2a executes all four steps on committed replay without callbacks, and rejects malformed step input. | Ordered prefixes, effect identity and historical receipts remain; injected steps prove decoding only. |
| settlement_failure_kind | `effect_program.ts`, `quota/settlement_readback.ts`, native lease owners → failure kind | Mixed internal failures and accepted envelope input. Stage 2a now includes all 12 wire values, real unbound-guard readback and native permission denial. | `cancelled` now has the reader-reserved scope and exit condition below; no internal producer or historical-use claim is made. Lease summaries use failure.step, while Effect envelopes use failure.step_kind; no fabricated wire conversion. |
| receipt_bound_monitor_phase | `quota/settlement_readback.ts` → `settlement_phase.ts` → monitor_phase | Internal derivation from exact monitor commit; Stage 2a executes poll_due/settled, wrong identity and legacy monitor effect id. | settlement_pending is retained consumer compatibility only; real monitor builder does not emit it. Remove only after historical work-lane consumers migrate. |
| receipt_bound_replay_phase | `quota/settlement_readback.ts` → `settlement_phase.ts` → replay_phase | Internal open/pending/settled from isolated receipt files and completion/writeback/spend prefixes; repeated reads and identity negatives execute the bridge. | Terminal-to-replay adapter and original binding stay. Phase projection is not proof all durable writers committed correctly. |
| delivery_workspace_kind | `agents/workspace_guard.py::capture_delivery_workspace` → TS snapshot | Mixed filesystem/Git observation and snapshot input. Real primary/linked/local workspace tests plus TS normalization cover three kinds, contradictions and invalid origins. | Keep v0 Git snapshot normalization; do not turn missing/failed Git evidence into a local-goal grant. |
| delivery_workspace_identity_kind | `agents/workspace_guard.py` → `agents/delivery_workspace.ts` → identity_kind | Mixed observed git_repository/local_goal and validated snapshot input. The same real Git/local tests verify identity, ownership and peer isolation. | Local Goal identity cannot satisfy a peer-independent Git requirement; v0 Git snapshots retain their own migration path. |
| delivery_workspace_requirement | `quota/settlement_workspace_causality.ts` → requirement | Internal required/not_required/unknown from explicit work contract. Causality tests execute repository writes, non-delivery, missing metadata and event readback. | Unknown requires repair. Legacy no-workspace settlement exception needs both exact committed validation/writeback receipts; partial or wrong-Todo evidence fails. |
| todo_completion_continuation | `todos/completion_state.ts::selectTodoCompletionState` → continuation | Internal active_goal/successor/no_followup selected from intent and Todo facts, plus persisted metadata input; whole-owner and malformed-authority tests execute it. | Contradictory successor/no-followup intent rejects; retained metadata normalization remains while retired scalar RPC rejects. |
| todo_completion_recovery | `todos/completion_state.ts` → recovery | Internal same_turn_terminal_closeout/lifecycle_reentry_terminal_closeout from completed Todo, closure intent and identity source; pinned and explicit lifecycle cases execute both. | Recovery does not rewrite the original completion identity or create a successor; nullable non-recovery is distinct. |
| todo_decision_scope_kind | `todos/decision_metadata.ts::normalizeTodoDecisionScope` → kind | Author-declared input, not inferred by a producer. Explicit seven-kind input witnesses, malformed membership, CLI mutation and exact-kind dominance tests execute readers. | `other` has the explicit custom-authority authoring rule below, never an unknown-input fallback. Input acceptance does not grant authority; legacy standing-scope codec remains. |
| todo_decision_scope_granularity | `todos/decision_metadata.ts` → granularity; `decision_scope.ts` → dominance | Author-declared action/lane/goal/project/global input; explicit domain and unknown/null/numeric tests plus CLI/dominance cases execute consumers. | Broad scope cannot cross kinds or override exact target/recipient conflict; do not infer missing scope from a bounded view. |
| goal_amendment_class | `goals/goal_amendment_proposal.ts` → proposal.amendment_class | Author-declared four classes; RFC classification table, all-class/unknown-class tests and real submit/list CLI provide input and journal evidence. | Admission is retained proposal evidence with canonical_effect=none, not amendment commit or delegated protected authority. |
| goal_amendment_proposal_admission | `goals/goal_amendment_proposal.ts::admissionOutcome` → admission | Internal admitted/needs_rebase from current basis, sequence and digest; equal/stale/future/canonical/Markdown tests execute real TS owner and CLI journal. | Retain stale proposals for rebase; reject fabricated future history. Admitted never means accepted shared change. |
| goal_amendment_proposal_admission_fact | same admission owner → admission_facts[] | Internal four fact codes for sequence behind, digest mismatch, unverifiable basis and superseded basis; combined-condition and basis-transition cases execute branches. | Mixed facts remain together; Markdown or canonical zero sequence is not fabricated event-log freshness. No change to proposal schema or commit authority. |
| scheduler_cadence_transition | `scheduler/state_transition_rules.ts` → cadence transition | Internal six transitions from identity, elapsed interval, acknowledgement and failure facts; Python decision table and TS coarse-transition tests execute the shared owner. | Keep pinned old-Python characterization and state readback; cadence is not permission to execute or spend. |
| scheduler_host_transition | `scheduler/state_transition_rules.ts` → host transition | Internal apply_required/host_match_ack_required/recorded_failure_suppressed/settled; explicit table/negative tests exercise current observations and recorded failures. | Projection is not an actual host update. Current-host ACK must pass #6259’s separate readback gate; no host-success claim from these fixtures. |
| handoff_mode | `coordination/handoff_mode_vocabulary.ts`, authority/mode-plan readers → mode | Mixed configured input and retained legacy/default read. Real show/set CLI, invalid/frontmatter, quiescence, lease and replay tests exercise source and readers. | New configuration targets are soft_claim/hard_lease; legacy remains readable for upgrade/receipts. Mode parsing is not lease authority. |
| delivery_outcome | `work_items/delivery_outcome.ts`, monitor/Host/replan producers → outcome | Mixed caller declaration and machine selection: monitor/Host paths produce surface_only/progress, replan can downgrade to outcome_gap; primary_goal_outcome is an author claim. History, native monitor, executor, replan and diagnostic tests exercise these boundaries. | Decoder/claim validation does not independently verify a primary outcome. Historical contradictory claims are diagnosed, not rewritten as success. |
| delivery_continuity_preemption | `quota/should_run_packet.py::_delivery_preemptions_for_route` → `turn_driver/delivery_continuity.ts` | Internal current guard facts plus typed input: receipt, blocking lane, replan, repair and denied delivery. Continuity route/selection tests execute preemption and subsequent reselection. | Sticky delivery cannot override these facts. Same strings in a different qualifier are not automatically this vocabulary; no old receipt retargeting. |

## Current disposition of the two previously unresolved meanings

[#6313](https://github.com/loopx-project/loopx/pull/6313) received an exact-head
`APPROVED` review from `loopx-agent` and was merged by `huangruiteng` in
`d3f84dced555b7fa8bdffa33b750de6b1b3ea968`. The following rules are now in the
canonical registry and protocol. This records the actual review/owner integration,
not a retroactive claim of human review, a cancellation implementation or general
tracker acceptance. The [original disposition record](2026-10-12-reserved-value-disposition.md)
retains its proposal baseline.

- **`settlement_failure_kind.cancelled` — Effect/settlement owner.** Retain the
  already admitted failure input as reader-reserved compatibility. It preserves
  prior receipts/details and short-circuits subsequent callbacks; it cannot
  prove that an in-flight provider stopped, erase a commit, or grant retry,
  rollback, resume or quota authority. There is still no identified built-in
  cancellation producer or proof of historical emission. Exit requires an
  owner-reviewed typed cancellation contract with actual causal witnesses, or a
  versioned reader migration with stored/caller compatibility evidence.
- **`todo_decision_scope_kind.other` — Todo decision-scope owner.** Authors select
  it for an explicit custom approval that none of the six named kinds describes;
  uncertainty is not a fallback. Task text explains the approval and a stable
  public-safe key identifies the gate and dependent work. Existing exact-kind,
  key, granularity, recipient and target rules remain. Even `other:global:*`
  cannot cover a named permission; unknown input rejects. The
  [authoring contract](../../../../reference/protocols/decision-scope-v0.md#choosing-kindother)
  supplies the rule and examples.

The prior absence of a usage rule is no longer described as current unknown
meaning. Input/compatibility treatment still does not earn internal producer
credit. Final #4447 closure additionally requires integration of the source
inventory and a passing final revision, not just the merge of this disposition.

## Evidence and reproduction

Use the intended source checkout, `uv sync --extra test`, qualified Node 22.22.3,
and `npm ci --ignore-scripts --no-audit --no-fund`. Python imports must resolve to
that checkout. Evidence groups are existing tests, not a new acceptance database:

- Foundation: `tests/test_loopx_turn_{driver,executor,transaction}.py`,
  `tests/test_loop_turn_loop_controller.py`,
  `tests/control_plane/test_agent_scope_frontier_contract.py`,
  `tests/control_plane/test_quota_run_decision.py`, and
  `tests/architecture/test_semantic_settlement_binding_witness.py`.
- 2a: `tests/architecture/test_settlement_receipt_source_boundaries.py` and native
  `turn_settlement`, `quota_settlement_readback`, `task_lease_acquire` and
  `task_lease_lifecycle` TS suites. The thin source witness adds no decision rule.
  Its new unbound-guard case executes real synthetic-file readback through TS and
  Python; its excluded-owner case executes real native lease rejection with no
  lease write. The explicit external-failure cases now cover twelve values;
  registry comparison only prevents an added value silently missing its witness.
- 2b: Python `test_delivery_workspace.py`, `test_workspace_git_fact_reads.py`,
  `test_delivery_workspace_causality.py`, `test_task_lease_cli_native.py`,
  `test_todo_decision_scope_{cli_validation,consistency}.py`,
  `test_todo_completion_state_runtime.py`; TS `delivery_workspace`,
  `settlement_workspace_causality`, `todo_completion_state`, `decision_metadata`
  and `decision_scope`. Input cases for every scope kind/granularity are distinct
  from runtime classification; custom-kind authoring now follows the current protocol.
- 2c: Python `test_goal_amendment_proposal{,_cli}.py`,
  `test_scheduler_state_{transition_rules,runtime_integration}.py`,
  `test_goal_handoff_mode_cli.py`, `test_handoff_mode_source.py`,
  `test_delivery_continuity_{selection,runtime}.py`, `test_native_monitor_poll.py`,
  `test_replan_writeback_projection.py`, `test_refresh_state_replan_gate.py`,
  `test_final_outcome_diagnostics.py`; TS `goal_amendment_proposal`,
  `scheduler_state_transition_rules`, `handoff_mode_plan`, `delivery_continuity`
  and `delivery_history`.
- Historical/new-output boundary: Python `test_protocol_packet_history.py` and
  `test_protocol_packet_retirement_cli.py`; generated semantic/Turn bindings
  checked with their existing `--check` commands and full drift smoke.

Run Python files with `uv run --extra test python -m pytest -q ...`; TS suite
names above mean `tests/control_plane_ts/<name>.test.ts`, run with
`node --no-warnings --experimental-sqlite --experimental-strip-types --test ...`.
Do not substitute enum iteration for producer witnesses. Expected observations
come from the accepted contracts and causal cases, not an echo of owner output.

### Current execution results

The source-evidence candidate over the main baseline above was executed with
Python 3.11.7 and qualified Node 22.22.3:

| Group | Result | Meaning |
| --- | --- | --- |
| Existing Turn/frontier/workspace/Todo/amendment/scheduler/handoff/continuity/history and local-provider paths (28 Python files listed above) | 610 passed | Actual domain owners, including isolated Git/File/SQLite paths; no live model or Host timer claim. |
| Semantic architecture plus `test_cross_runtime_value_notes.py` | 706 passed | Scanner/registry/owner/value-note/compatibility contracts, not whole-program production proof. |
| Native TypeScript (the listed suites plus action-selection admission and interaction contract) | 448 passed, no skips | Typed source and consumer boundaries; not a PostgreSQL or deployed-App qualification. |
| Full settlement CLI plus decision-scope CLI/consistency/lifecycle and Stage 2a source witnesses | 209 passed, no skips | The three original Host-ACK cases now pass on current main code; source-witness additions remain in this PR. |
| Current-main monitor unavailable-recovery, closeout and target-key replay | 52 passed | Real affected owner paths supplement the changed readback boundary; no live Host timer or deployed recovery claim. |

The original failing tests —
`test_standard_codex_app_settlement_is_receipted_and_idempotent` and both
`test_todoless_autonomous_replan_settles_quota_refresh_spend_chain` variants —
now pass individually and in the full group above. The relevant production and
settlement CLI test files equal main. Commit
`db5a298a54f47179b9be2a3b9cf2f775b71a9274`
([#6344](https://github.com/loopx-project/loopx/pull/6344)) supplied the missing
synthetic Host observation; the Todo-less scenarios also retain absent and
mismatched manifest refusals, no scheduler write and the original single debit.
The ordinary scenario still refuses the superseded Turn before acknowledging
the current one. Original ACK, persisted scheduler state and byte-stable replay
assertions pass without relaxing the Host-readback rule.

[#6285](https://github.com/loopx-project/loopx/pull/6285) remains a separate open
proposal at `f6c8aae7a7e8ba917861992a1ad21eb29477bf26`. Its current diff still adds
shared fixture setup, extra ordinary-Turn absent/mismatched cases and explicit
applied-RRULE readback; it is not byte-identical to main or reported as merged.
Those remaining changes do not make the original three main failures recur.
Do not duplicate its patch here or require another merge merely to count the
same fixture repair twice.

Historical results remain tied to their original snapshots: main
`509c38c502d3d0b4eafb01550efcdc4b5b5a1d17` had 206 passed / 3 failed in this group,
and a separate combination with the then-current #6285 fixture passed the
101-test settlement CLI suite. They are superseded for current acceptance by
the current-main-runtime results above, not rewritten as earlier successes.

The full drift readback retains F1/F2 6/26, F3 0/26, F4 4/4, F5 1/1 and F6 0/0.
The report still names 45 unresolved producer sites, including 17 that cannot
supply producer evidence; no ceiling or scan boundary is relaxed. The cross-runtime value-note cases are included in the 706-case group above,
not an additional coverage count. Generators, typecheck and
scoped quality results are part of the PR validation record. Earlier 66/564/82/706 Python and 421 TS counts
belong to the old `43173c0b8` snapshot; they are not relabeled as current results.

<details>
<summary>Exact selections for the current evidence groups</summary>

```sh
uv run --extra test python -m pytest -q \
  tests/test_loopx_turn_driver.py \
  tests/test_loopx_turn_executor.py \
  tests/test_loopx_turn_transaction.py \
  tests/test_loop_turn_loop_controller.py \
  tests/control_plane/test_agent_scope_frontier_contract.py \
  tests/control_plane/test_quota_run_decision.py \
  tests/control_plane/test_delivery_workspace.py \
  tests/control_plane/test_workspace_git_fact_reads.py \
  tests/control_plane/test_delivery_workspace_causality.py \
  tests/control_plane/test_delivery_workspace_causality_runtime.py \
  tests/control_plane/test_task_lease_cli_native.py \
  tests/control_plane/test_todo_completion_state_runtime.py \
  tests/control_plane/test_goal_amendment_proposal.py \
  tests/control_plane/test_goal_amendment_proposal_cli.py \
  tests/control_plane/test_goal_amendment_proposal_lifecycle.py \
  tests/control_plane/test_scheduler_state_transition_rules.py \
  tests/control_plane/test_scheduler_state_runtime_integration.py \
  tests/control_plane/test_goal_handoff_mode_cli.py \
  tests/control_plane/test_handoff_mode_source.py \
  tests/control_plane/test_delivery_continuity_selection.py \
  tests/control_plane/test_delivery_continuity_runtime.py \
  tests/control_plane/test_native_monitor_poll.py \
  tests/control_plane/test_replan_writeback_projection.py \
  tests/control_plane/test_refresh_state_replan_gate.py \
  tests/control_plane/test_final_outcome_diagnostics.py \
  tests/control_plane/test_protocol_packet_history.py \
  tests/control_plane/test_protocol_packet_retirement_cli.py \
  tests/control_plane/test_local_provider_settlement_journey.py

node --no-warnings --experimental-sqlite --experimental-strip-types --test --test-concurrency=1 \
  tests/control_plane_ts/turn_settlement.test.ts \
  tests/control_plane_ts/quota_settlement_readback.test.ts \
  tests/control_plane_ts/task_lease_acquire.test.ts \
  tests/control_plane_ts/task_lease_lifecycle.test.ts \
  tests/control_plane_ts/delivery_workspace.test.ts \
  tests/control_plane_ts/settlement_workspace_causality.test.ts \
  tests/control_plane_ts/todo_completion_state.test.ts \
  tests/control_plane_ts/decision_metadata.test.ts \
  tests/control_plane_ts/decision_scope.test.ts \
  tests/control_plane_ts/goal_amendment_proposal.test.ts \
  tests/control_plane_ts/scheduler_state_transition_rules.test.ts \
  tests/control_plane_ts/handoff_mode_plan.test.ts \
  tests/control_plane_ts/delivery_continuity.test.ts \
  tests/control_plane_ts/delivery_history.test.ts \
  tests/control_plane_ts/action_selection_admission.test.ts \
  tests/control_plane_ts/interaction_contract.test.ts

uv run --extra test python -m pytest -q \
  tests/control_plane/test_quota_settlement_cli.py \
  tests/control_plane/test_todo_decision_scope_cli_validation.py \
  tests/control_plane/test_todo_decision_scope_consistency.py \
  tests/control_plane/test_todo_decision_scope_lifecycle.py \
  tests/architecture/test_settlement_receipt_source_boundaries.py

uv run --extra test python -m pytest -q \
  tests/architecture/test_semantic*.py \
  tests/architecture/test_cross_runtime_value_notes.py

uv run --extra test python -m pytest -q \
  tests/control_plane/test_monitor_poll_unavailable_recovery.py \
  tests/control_plane/test_monitor_unavailable_closeout_cli.py \
  tests/control_plane/test_monitor_target_key_replay.py
```

</details>

## Integrated state and closure boundary

The main baseline includes #4772/#4777 reporting corrections, #4775 live
composition, #4783 settled replay, #4788 rejected selection, #4786 inbox
composition, overlapping correctness #4764, binding witness #4747 and 2a #4795.
#4794 stopped new `protocol_action_packet` writes while retaining historical
reading/signature projection. The other five legacy fields remain intentionally
visible; all-field retirement is not a closure condition. #5317 supplies the
separate #4743 development-time advisory, so the old “never built/no AGENTS
instruction” claim is obsolete. Advisory findings do not become blocking proof.

The current baseline includes #6251 typed-failure and #6268 canonical-selection
repairs, #6260 shared consistency, #6290 legacy-work-basis repair and #6313 source
disposition. These resolve named code/meaning gaps; they do not extend #4447's
original closure scope. The original Host-ACK blocker is resolved in main;
the additional source witnesses and inventory in #6270 are still proposed.
Stage 3 and the issue remain open for that integration and final owner closeout
on one revision with explicit passed/failed/untested evidence. A remaining
#6285 enhancement is not a new closure condition for this tracker.
Installed frontend/Lark/live-model, general Goal/team qualification and blanket
F6 proof are outside this tracker. No production provider, permission or
persistence format changes here; raw logs and local Goal state stay private.
