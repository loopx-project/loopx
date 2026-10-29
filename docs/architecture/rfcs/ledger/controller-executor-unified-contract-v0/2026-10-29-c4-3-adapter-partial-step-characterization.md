# C4.3 adapter partial-step characterization

- Parent: [controller/executor RFC, C4.3](2026-10-27-refactor-handoff.md#c43--settlement-adapters-over-existing-primitives).
- Baseline: `2a2323756`, 2026-10-29. The settlement owners are unchanged at `fb331f45`.
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
| A step raises after a durable commit | The adapter leaves nothing behind; the CLI's canonical receipt store holds the committed receipt | The readback reports `settled`; the retry replays that receipt (`idempotent_replay`), spends no second slot and reports the same settlement identity |
| A step raises before the lifecycle completion | Nothing is spent and the Todo stays open | No settlement receipt exists to replay |

Two findings follow from these cases.

1. **The adapter makes no classification decision.** A stopped plan is
   classified by the TypeScript owner; a raised step is not classified at all,
   because the owner is never asked. The adapter is transport in both cases.
2. **The recovery boundary is the CLI receipt, not adapter state.** After a
   raise, the only durable evidence is what the CLI already committed. The
   retry succeeds because that receipt replays, and the adapter contributes
   nothing but a re-execution of the plan.

## Evidence

`tests/control_plane/test_host_adapter_settlement_partial_steps.py`:

- `test_an_unreadable_step_output_stops_the_plan_and_the_owner_classifies_it`
- `test_a_raising_step_leaves_no_finalize_and_no_adapter_local_resume`
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

Unchanged by this entry. A deletion of the adapter-side interpreter still
needs the preconditions recorded in the comparison entry, and vocabulary
unification still needs a caller that adopts both paths.
