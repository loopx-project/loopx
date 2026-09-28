# Design follow-up: Goal continuity across restart and replacement

- **RFC status:** Accepted (non-normative design follow-up).
- **Supersedes / closes:** none
- **Delivery maturity:** Deferred design note; not an accepted new runtime contract.
- **Origin:** Retains useful questions from [Duang777's #5169](https://github.com/loopx-project/loopx/pull/5169), with its implementation and evidence claims narrowed during review.
- **Semantic mirror:** [中文](goal-immutability-coherence-defense-v0.zh-CN.md).
- **Owning contracts:** [Goal instance identity and orphan recovery](goal-instance-identity-and-orphan-recovery-v0.md), [Goal direction baseline](goal-direction-baseline-v0.md), [governed amendment](shared-goal-alignment-and-governed-amendment-v0.md), [semantic handoff](capable-manager-semantic-handoff-v0.md), and [shared authority](shared-goal-authority-state-provider-v0.md).

This preserves follow-up design value from the original “Goal Immutability as
Coherence Defense” draft. It creates no second roadmap, state owner, acceptance
gate or delivery claim. The owning RFCs decide activation, authority and rollout.
The questions below are proposed qualification scenarios, not evidence of a
remaining defect in every named path.

## What is worth retaining

Durable commitments should outlive a model's working context. A restarted or
replaced Agent should recover the authorized Goal, constraints, accepted work
and unresolved obligations from their existing owners. A same-name replacement
Goal must not inherit an old instance's authority merely because names match.
These are useful long-horizon failure scenarios even when individual storage
and command tests pass.

Three distinctions prevent an overly broad “coherence” guarantee:

| Fact | What it can establish | What it cannot establish |
| --- | --- | --- |
| Exact GoalRef and source-owned instance fence | Which Goal lifetime may admit an action | Whether that action is useful or its output correct |
| Provider revision / CAS | Whether a new write still has its expected storage basis | Current Goal authority if the caller resolves/rebinds the wrong instance; revision tokens are opaque, not ordered counters |
| Operation identity and verified original receipt | Which operation already committed and its original result | Permission to repeat an external effect or attach the result to a replacement Goal |
| Authorized intent / acceptance basis | Which constraints and completion criteria govern this work | Model compliance or outcome correctness without independent evidence |

Immutable **instance identity** does not mean immutable **Goal intent**. Authorized
amendments must remain possible and versioned through their existing owner.
A model can produce a wrong change against a perfectly current CAS revision.
Prompt/context improvements, typed constraints and outcome validation complement
storage fences; none substitutes for all the others.

## Proposed follow-up slices under existing owners

| Slice and owner | Real caller scenario | Decisive acceptance, including recovery |
| --- | --- | --- |
| Instance-qualified continuity — Goal instance RFC; related collaboration/session consumers in [#5106](https://github.com/loopx-project/loopx/pull/5106) and [#5130](https://github.com/loopx-project/loopx/pull/5130) | Retire A through the authorized lifecycle, create same-name B, then deliver A's delayed Todo/result, claim renewal and plan confirmation through their real entrypoints | No mutation or execution authority leaks into B. Typed stale-instance outcomes remain observable; B's legitimate work succeeds. Historical A receipts remain attributable to A where retention/access policy permits. Registry activation and its legacy/off behavior follow the owning RFC. |
| Constraint continuity — direction-baseline and governed-amendment RFCs; roadmap R4 | Resume/rebind an Agent after context loss with stale material/acceptance basis, then repeat with an authorized amendment and refreshed basis | Original constraints and accepted work are recovered from canonical owners; re-evaluation remains Agent-scoped. Unrelated work is not globally blocked. The legitimate amendment can progress; no implicit freeze of all Goal intent. |
| Recoverable late-result disposition — handoff and Effect recovery owners; roadmap R3 | An old request's result arrives after requester/instance replacement or after an external effect has committed but its response was lost | Preserve original request/result lineage and the external effect's durable evidence. Reconcile at the owning ledger; do not silently discard evidence, automatically rebind to B or rerun the effect. An authorized recovery path returns a result or records an explicit terminal disposition. |

Before implementing a slice, inventory current main, related PRs and existing
fixtures. Extend the current owner's missing cases rather than creating a
parallel “semantic certificate” or generic coherence engine. Shared decisions
belong in existing typed TS owners; provider adapters supply physical evidence.
As of this note's 2026-09-27 review, #5106, #5130 and the related App Turn recovery
[#5139](https://github.com/loopx-project/loopx/pull/5139) are open; their merge or
isolated tests alone would not certify the combined journeys above.

## Qualification method and unresolved decisions

Use disposable runtimes, synthetic public-safe Goals and actual supported
backends. Derive expected outcomes from the owning contract before executing:

- Exercise same-instance restart, same-name replacement, authorized amendment,
  delayed input, overlapping invalid conditions and valid post-recovery work.
  A stale rejection alone is not restored progress.
- Exercise both accidental scope expansion and escape: covered old bindings,
  unrelated current work and newly created subjects follow the declared scope.
- For concurrent creation, preserve the registry's declared uniqueness and
  linearization contract. Do not assume that every racing request must create
  a separate writable Goal; distinct successful lifetimes must never share an ID.
- Inject one fault at a time and demonstrate oracle sensitivity: wrong GoalRef,
  missing commit fence, dropped handoff constraint or duplicated effect. Keep
  real effect evidence distinct from simulated adapters and model evaluations.

Open decisions belong to the existing owners: whether each producer already
captures sufficient immutable instance/basis evidence; how a stale requester
receives a recoverable outcome; and whether an explicit new producer/schema is
needed. Never derive the original instance from a mutable “current Goal” lookup.
If a format change is necessary, qualify backup/migration and mixed writers;
this note does not predeclare “no migration needed.”

## Delivered boundary and evidence status

[#5169's operation replay](../../reference/authority-operation-replay.md) verifies
matching historical File/SQLite commits without rewinding current state. It does
not implement or qualify the three follow-up journeys. Tests of stale provider
revisions are not tests of Goal replacement, compaction or semantic correctness.

The original draft's quantitative research/experiment tables are not retained as
accepted evidence: this PR does not supply an independently reviewable public
harness, oracle and source provenance for them. Future evidence must name the
exact revision, real entrypoint/backend, fault, independent oracle and recovery
readback. No numeric success rate or claim that “CAS prevents coherence collapse”
is carried forward. This note neither changes File/SQLite defaults nor adds a
new prerequisite to their existing D1–D3 qualification.
