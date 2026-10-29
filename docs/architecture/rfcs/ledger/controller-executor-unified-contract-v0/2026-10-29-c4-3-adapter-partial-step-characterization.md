# C4.3 adapter partial-step characterization

- Parent: [controller/executor RFC, C4.3](2026-10-27-refactor-handoff.md#c43-settlement-adapters-over-existing-primitives).
- Baseline: `8caaf678e`, 2026-10-29. The settlement owners are unchanged at `1e3401591`.
- Status: characterization only. No runtime, schema, wire or default change.
- Follows: [C4.3 settlement obligation comparison](2026-10-29-c4-3-settlement-obligation-comparison.md).
- [Chinese mirror](2026-10-29-c4-3-adapter-partial-step-characterization.zh-CN.md).

## Why this entry exists

The comparison entry recorded the adapter's lack of a local journal as a fact
about the adapter, and left the adapter's own behavior when a step succeeds and
a later step fails uncharacterized, together with the place where the recovery
boundary between the adapter and the CLI receipts sits. A consolidation slice
that moved this path would otherwise have had to assume both.

This entry pins that behavior with executable cases, so a later recovery change
starts from measured behavior rather than from the comparison's wording.

## What is characterized now

| Surface | Adapter behavior | Owner behavior |
| --- | --- | --- |
| A step returns output the adapter cannot read | `_provider_condition_matches` treats a non-JSON output as a condition that did not match, so the plan stops after that step | The finalize phase still runs and the owner classifies the stop: `blocked`, `failed_stage: guard`, reason `quota guard returned malformed JSON` |
| A step raises | The exception propagates out of the adapter. The finalize phase never runs, so the transaction is never classified. The adapter keeps no record of the step that already succeeded | Nothing is classified, because the owner is never asked |
| A retry after a raise | The adapter re-enters the plan at its first step and never resumes mid-plan | The identity is re-derived from the same intent, so the same settlement identity is used |
| A step raises before the lifecycle completion | Nothing is spent and the Todo stays open | The guard already ran, so the `validation` receipt is committed. The readback is `open` with progress `writeback_required`; there is no lifecycle completion, writeback or spend receipt to replay |
| A step raises after the lifecycle completion | The adapter leaves nothing behind; the CLI's canonical receipt store holds the committed receipt | The readback is `settlement_pending` with progress `writeback_required`: `validation` is committed, the writeback and spend receipts are missing |
| A step raises after the durable writeback | The adapter leaves nothing behind; the CLI's canonical receipt store holds the committed receipt | The readback is `settlement_pending` with progress `spend_required`: `validation` and `durable_writeback` are committed, the spend receipt is missing |
| A step raises after the quota spend | The adapter leaves nothing behind; the CLI's canonical receipt store holds the committed receipt | The readback is `settled`; the retry replays that receipt (`idempotent_replay`), spends no second slot and reports the same settlement identity |

Three findings follow from these cases.

1. **The adapter makes no classification decision.** A stopped plan is
   classified by the TypeScript owner; a raised step is not classified at all,
   because the owner is never asked. The adapter is transport in both cases.
2. **The recovery boundary is the CLI receipt, not adapter state.** After a
   raise, the only durable evidence is what the CLI already committed. The
   retry succeeds because that receipt replays, and the adapter contributes
   nothing but a re-execution of the plan.
3. **The outstanding work is named by the owner, stage by stage.** The
   readback distinguishes an open settlement from a pending one and names the
   step still owed — `writeback_required` after the lifecycle completion,
   `spend_required` after the durable writeback, `settled` after the spend.
   A raise is therefore not a single undifferentiated failure state, and the
   committed receipt kinds tell which stage a retry would resume from.

## Evidence

`tests/control_plane/test_host_adapter_settlement_partial_steps.py`:

- `test_an_unreadable_step_output_stops_the_plan_and_the_owner_classifies_it`
- `test_a_raising_step_leaves_no_finalize_and_no_adapter_local_resume`
- `test_a_raised_step_after_a_durable_commit_leaves_only_that_stage_pending`
  (real CLI, real quota store; parametrized over the guard, lifecycle
  completion, durable writeback and quota spend stages, reading back the
  committed receipt kinds, the replay phase and the settlement progress)
- `test_a_raising_step_after_a_durable_commit_recovers_through_the_cli_receipt`
  (real CLI, real quota store)
- `test_a_raised_step_before_completion_commits_no_settlement` (real CLI)

The existing end-to-end recovery case stays where it was:
`tests/test_goal_mode_mcp_settlement.py::test_real_mcp_completion_recovers_a_lost_mutation_response`
covers a lost response after a commit; the new case covers the same recovery
through a raised transport instead.

## What this does not establish

- No equivalence proof, no deletion authorization, no runtime qualification, no
  benefit measurement, and no claim that either settlement path is redundant.
- The characterization covers the shipped MCP host path through
  `settle_host_todo_completion`. It does not characterize other callers of the
  adapter, and it does not describe the TypeScript-only Turn settlement path,
  which recovers per provider step inside its own transaction.
- It adds no journal, retry policy or resume capability to the adapter. Adding
  one is a new design decision with its own acceptance, not a characterization.
- The observed recovery depends on the CLI's canonical receipt store. Source
  facts were read at the baseline above and may drift.

## Next slices

Unchanged by this entry, with one correction to how the remaining work is
described. A deletion of the adapter-side interpreter still needs the
preconditions recorded in the comparison entry. The vocabulary candidate that
the confirmation entry withdrew — one typed owner for `guard`,
`lifecycle_completion` and `validation` — is not reopened here: that entry
already identified the shipped caller that adopts both paths and recorded the
candidate as withdrawn. The independent registry slice — the shared owner for
the ordered host-adapter step kinds — is completed in
[#7619](https://github.com/loopx-project/loopx/pull/7619). That slice was a
registry change, not a continuation of the withdrawn unification. The work
that remains open is the deletion and adoption preconditions recorded in the
comparison entry.
