# C4.3 settlement vocabulary confirmation

- Parent: [controller/executor RFC, C4.3](2026-10-27-refactor-handoff.md#c43--settlement-adapters-over-existing-primitives).
- Follows: the [C4.3 settlement obligation comparison entry](2026-10-29-c4-3-settlement-obligation-comparison.md)
  of 2026-10-29 in this directory. It settles that entry's first candidate slice.
- Baseline: `0a9a3a388`, 2026-10-29. The settlement owners are unchanged since
  `544aa0fee`.
- Status: read-only confirmation. No code, schema, wire, registry or default change.
- [Chinese mirror](2026-10-29-c4-3-settlement-vocabulary-confirmation.zh-CN.md).

## Why this entry exists

The C4.3 settlement obligation comparison listed a candidate next slice:
*vocabulary unification (small). If any caller adopts both paths, give `guard`,
`lifecycle_completion` and `validation` one typed owner instead of three names in
two files. Do not unify names that describe different obligations.*

That candidate was conditional on a question this entry answers: do the three
names describe one obligation? The comparison already hinted that they do not
all match, but it did not settle it, and a guess here would have produced either
a wrong merge or a wrong refusal to merge.

## The three names

| Name | Owner | Position | What it does |
| --- | --- | --- | --- |
| `guard` | `turn_driver/host_todo_completion.ts` `STEP_KINDS` | first step of the host provider plan | runs `quota should-run` with the turn instance id; its `continue_when` decides whether the rest of the plan runs |
| `lifecycle_completion` | same | second step | runs `todo complete` with the completion arguments |
| `validation` | `effect_program.ts` `SETTLEMENT_STEP_KINDS`, and a phase of `turn_transaction_contract.json` | a transaction phase the caller must already have completed | asserts the admission already happened; it is never executed |

## Evidence

1. **`validation` is not an executable effect in either runtime.**
   `effect_program.ts` defines
   `TurnProviderStepKind = Exclude<SettlementStepKind, "validation">`;
   `effect_program.py` mirrors that exclusion and
   `require_turn_provider_step_kind` raises `validation is not a provider effect
   step`. `settlement.ts` throws `unsupported base settlement step validation`
   if the reducer ever asks to execute it, and it receives
   `committed_payloads.validation = {}` together with `require_validation: true`,
   so the caller discharges the phase and B never runs it. The registry already
   says the same in its own words: the `validation` value note in
   `loopx/semantics/vocabulary_v0.json` records that it "is the one step the
   dispatcher never executes", that it "carries no command template", and that it
   "is seeded, so its receipt is emitted directly when the completed phases
   already contain it".

2. **`guard` and `lifecycle_completion` are executed steps.**
   `host_adapter_settlement.py` decodes an `ordered_cli_sequence` of four or five
   steps in the fixed order `guard → lifecycle_completion → durable_writeback →
   quota_spend → [terminal_closeout]`, then calls `run_cli(step.args)` for each
   one. Every non-final step carries a `continue_when` that decides whether the
   following steps run.

3. **The two value sets differ exactly where the question is asked.** A emits
   `guard`, `lifecycle_completion`, `durable_writeback`, `quota_spend`,
   `terminal_closeout`. B's `SettlementStepKind` is `validation`,
   `durable_writeback`, `quota_spend`, `terminal_closeout`. `guard` and
   `lifecycle_completion` occur only in A; `validation` only in B. The
   repository's own advisory (`scripts/generate_semantic_inventory.py --report`)
   does not list the two sets as a merge candidate, because their value sets
   differ.

4. **`lifecycle_completion` has no counterpart in B.** B carries the completion
   obligation inside `durable_writeback` — the writeback payload has to satisfy
   the completion condition when the result is `validated_completion` — and in
   `terminal_closeout_requirement`.

5. **One shipped caller already uses both vocabularies for one admission
   decision.** `extensions/governed_capability_execution.py` classifies the
   caller's admission snapshot with A's `classify_host_guard_snapshot` (the
   `classify_guard` phase of `host_todo_completion.ts`), asserts `should_run`
   and the typed settlement identity, and then calls
   `execute_turn_driver_settlement` with `completed_phases` that already
   contain `validation`. The MCP host path (`goal_mode_mcp.py` →
   `settle_host_todo_completion`) instead executes the `guard` step itself.

## Verdict

- `guard` and `validation` name one obligation seen from two positions: the
  producer that runs the admission, and the consumer that refuses to run it and
  requires it to have been discharged. They are not two spellings of one value.
  Renaming them would hide that asymmetry and would break B's "a phase is never
  executed" invariant.
- `lifecycle_completion` is a different obligation and keeps its own name.
- The candidate *one typed owner for the three names* is therefore **withdrawn**.
  The names are already unambiguous once read at their owners.

## What the confirmation leaves open

The vocabulary that does exist has no typed owner, and its literal is written
twice:

- `loopx/control_plane/turn_driver/host_todo_completion.ts` defines a local
  `STEP_KINDS`;
- `loopx/control_plane/host_adapter_settlement.py` defines a private
  `_STEP_KINDS` and validates the TypeScript-produced plan order against it.

Neither symbol is exported, neither is registered in
`loopx/semantics/vocabulary_v0.json` — 28 vocabularies are registered, and
`settlement_step_kind` covers only B's set — and the repository's inventory does
not see either carrier. The TypeScript scan matches
`export const NAME = [...] as const;` and the Python scan matches module-level
`UPPER_NAME` assignments, so a private, unexported pair stays invisible to the
drift smoke that the registry policy relies on. A hand-kept Python mirror of a
TypeScript literal can drift, and the failure would surface at runtime as
`TypeScript host provider step order mismatch`.

That gap is a separate slice: one shared owner for the ordered host-adapter step
kinds, registered in the vocabulary registry so that the guard/validation
distinction becomes machine-readable instead of prose. It must first reconcile
the in-flight settlement work: #3412, #5677, #7580, #7523. That slice is
proposed in [#7619](https://github.com/loopx-project/loopx/pull/7619): it
registers the ordered host-adapter step kinds as their own vocabulary with both
runtimes as owners, so the two carriers are compared on every drift scan instead
of hand-kept.

## What this does not establish

No equivalence proof, no deletion authorization, no registry edit, no runtime
qualification and no claim that either path is redundant. Source facts are read
at the baseline above and may drift.
