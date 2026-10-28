# C4.3 settlement obligation comparison

- Parent: [controller/executor RFC, C4.3](../2026-10-27-refactor-handoff.md#c43--settlement-adapters-over-existing-primitives).
- Baseline: `e19d94ca0`, 2026-10-29.
- Status: reading-only comparison. No deletion, runtime, schema, wire or default change.
- [Chinese mirror](2026-10-29-c4-3-settlement-obligation-comparison.zh-CN.md).

## Why this entry exists

C4.3 requires comparing `host_adapter_settlement.py`, `turn_driver/host_todo_completion.ts`
and `turn_driver/settlement.py` obligation by obligation *before* deleting a
supposedly duplicated condition/step interpreter. No such comparison was
recorded. This entry supplies it so the next PR can be decided rather than
guessed.

An **obligation** here is a decision or durable effect that must occur exactly
once per transaction, and whose owner must reject, replay or recover it.

## The three owners at this baseline

| Where | Entry | Decision owner | Python role |
| --- | --- | --- | --- |
| A — Host Todo completion | `settle_host_todo_completion` | `host_todo_completion.ts` via `turn.host_todo_completion.evaluate` | Materializes authored vision JSON, decodes the emitted plan, runs CLI steps, evaluates `continue_when` |
| B — Turn settlement | `execute_turn_driver_settlement` | `settlement.ts` via `turn.settlement.reduce` | Invokes provider effects, writes checkpoints, returns observations |
| C — MCP host facade | `goal_mode_mcp.py` | A end-to-end | Loads A's request from the host |

## Obligation matrix

| Obligation | A decides | B decides | Equivalent? |
| --- | --- | --- | --- |
| Transaction identity | TS `turnInstanceId` / `expectedIdentity`, surfaced as `SettlementIdentity` | TS identity plus per-step receipt matching | Same vocabulary, different derivation inputs |
| Request validation | TS `decodeRequest`, `TODO_ID_PATTERN` | TS decode plus `receipt_missing` / `identity_mismatch` | Not equivalent: A validates shape, B validates committed receipts |
| Pre-write admission | `guard` step runs the real `quota should-run` CLI with the turn instance id | `validation` is a pre-committed phase; B throws if it would have to execute it | **Not equivalent** — different obligations sharing no code |
| Lifecycle completion | `lifecycle_completion` step with completion args | No counterpart; completion is part of writeback | A-only |
| Durable writeback | `durable_writeback` step, condition guarded | `durable_writeback` provider effect through the reducer | Same kind, different execution and recovery |
| Quota spend | `quota_spend` step | `quota_spend` provider effect | Same kind, different execution and recovery |
| Terminal closeout | `terminal_closeout` step, emitted last with `continue_when: null` | `terminal_closeout` provider effect plus `terminal_closeout_requirement` | Same kind, B owns the requirement rule |
| Replay / recovery | None: prepare → plan → finalize is single-shot, no journal | Checkpoint per step, `resolve_prepared`, receipt replay, retry ceiling | **Decisive asymmetry** |
| Persistence | Only a temporary vision file | Durable checkpoint plus source journal | B-only |

## Findings

1. **Neither path is a second policy owner.** Both delegate every decision to a
   TypeScript owner and keep Python as transport. `host_adapter_settlement.py`
   carries no lifecycle policy of its own.

2. **The step vocabularies overlap only partially.** A emits
   `guard / lifecycle_completion / durable_writeback / quota_spend /
   terminal_closeout`; B's `SettlementStepKind` is
   `validation / durable_writeback / quota_spend / terminal_closeout`. `guard`
   and `lifecycle_completion` have no B counterpart, and B's `validation` is a
   phase marker it refuses to execute rather than A's admission call.

3. **Recovery is the real difference, not duplication.** A has no journal and no
   replay; it either completes or raises. B persists a checkpoint per provider
   and can resume, replay receipts and reject ambiguous attempts. Deleting A
   into B would require giving the MCP host a journal it does not have today.

4. **At the shipped capability call site they already compose.**
   `extensions/governed_capability_execution.py` calls A's
   `classify_host_guard_snapshot` for admission and then
   `execute_turn_driver_settlement` for the transaction with its own journal.
   The suspicion that these are competing settlement transactions is not
   supported by this call site.

5. **The only Python-side interpreter is small and transport-necessary.**
   `_condition_matches` supports `all`, `any`, `equals`, `nullish`, `object`,
   `not_object` and `normalized_string_equals`; `_decode_provider_steps`
   validates that the plan has four or five steps in the fixed order. TypeScript
   still authors every condition. Evaluating them in TypeScript instead needs
   one runtime call per step, which the RFC explicitly says is not automatically
   an improvement.

## What a deletion would require

None of these is established today:

- the MCP host path gaining a journal and an effect receiver for A's steps; or
- B accepting `ordered_cli_sequence` plans with `continue_when` plus A's `guard`
  and `lifecycle_completion` kinds without a per-step runtime call; or
- evidence that no shipped caller uses A end-to-end.

## Candidate next slices

1. **Vocabulary unification (small).** If any caller adopts both paths, give
   `guard`, `lifecycle_completion` and `validation` one typed owner instead of
   three names in two files. Do not unify names that describe different
   obligations.
2. **Characterization of A's single-shot behavior (medium).** Pin what happens
   when a step succeeds and a later step raises, before any recovery change
   touches it.
3. **Not recommended now.** Merging A and B, or adding a third plan format,
   would add a transaction boundary without deleting an obligation.

## Coordination

Active or recently merged work touching these obligations:
[#3412](https://github.com/loopx-project/loopx/pull/3412) (ambiguous settlement
recovery), [#5677](https://github.com/loopx-project/loopx/pull/5677) (caller-authored
first delivery), [#7580](https://github.com/loopx-project/loopx/pull/7580) (failed
journal with an unresolved prepared effect), [#7523](https://github.com/loopx-project/loopx/pull/7523)
(typed receipts and continuation). Any implementation slice must reconcile those
diffs first.

## What this does not establish

No equivalence proof, no deletion authorization, no runtime qualification, no
benefit measurement, and no claim that either path is redundant. Source facts
are read at the baseline above and may drift.
