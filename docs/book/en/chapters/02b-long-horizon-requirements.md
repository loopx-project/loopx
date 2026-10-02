# The four demands of long-running work

A repair can span code edits, tests, CI waiting, review, handoff and delivery. Each step may fit one session; the task crosses sessions, processes and decisions. This chapter supplies four reading questions, not a universal architecture or a claim that every LoopX path already provides the same guarantees.

## Four demands

| Question | What must be retained or judged? | LoopX's main approach |
| --- | --- | --- |
| How does another session continue? | Objective, work, evidence and next entrypoint | Durable state and rebuildable projections |
| How does interruption avoid blind repetition? | Committed, absent and unknown outcomes | Original identity, journals, receipts and provider readback |
| How do executors collaborate? | Assignment, scope and current execution proof | Claims, leases, fences and commit checks |
| How does no-change work consume less? | Budget, wait target, observations and actual execution conditions | Quota, Monitors, scheduler/backoff and Host |

Long-running work needs neither an immortal process nor a delivery on every wake. Legitimate waiting and honest stopping also need representation.

## Demand one: state outlives the current context

A recipient needs the exact PR, commit, validated behavior and outstanding decisions, not merely “I fixed it.” Chat may persist and explain history, but persistence alone supplies neither structure, freshness nor current permission.

Consequential facts need an addressable source, applicable revision and read entrypoint. Maintaining records has a cost; the benefit is reassessing conditions after session change rather than filling gaps from memory. In the [state chapter](state-substrate.md), distinguish source from display under the selected mode.

## Demand two: retain known facts and uncertainty

An external operation can succeed while its local response is lost. A missing local record proves neither remote absence nor completion.

Governed Turns represent confirmed progress through phases and receipts and read unresolved effects under original identity. Legal prefixes constrain records, not the crash window between external commit and checkpoint. The original owner resolves uncertainty; a new key must not bypass it. Only supported outcomes and current permission allow reuse or outstanding execution.

Historical recovery and admission for a new execution answer different questions; see [recovery](04-runtime-boundaries.md#recovery-or-new-execution).

## Demand three: distinguish assignment from permission to commit

Agents can handle different work or both believe the same item belongs to them. Claims express responsibility; applicable leases/fences constrain the current instance. Neither replaces Goal boundaries, Gate scope, capability or workspace conditions.

Default legacy, soft-claim and hard-lease paths have distinct enforcement boundaries. One mode does not establish universal exclusion, and multiple peers do not imply one executor for the entire Goal. Tie takeover and parallelism to a concrete writer and current inputs; see [authority layers](work-graph-and-authority.md#authority-layers).

## Demand four: budget and observation constrain repetition

Budget bounds quantity, but balance alone cannot tell whether another turn is useful. CI waiting requires readback for the correct revision; approval waiting needs an authorized decision. A next-due time without a usable Host is only a recorded time.

Monitors retain targets and observations; scheduler/backoff arranges timing; Hosts or integrated event channels execute. Backoff reduces repeated reads but can delay discovery. Replan reassesses a route rather than applying stronger backoff. Internal no-spend does not make models, tools or networks free; see [observation ownership](04b-budget-and-admission.md#observation-owners).

## How the demands work together

```text
Identify objective and current work
  → read sources and determine allowed action
  → execute, validate and obtain owner acceptance
  → complete outstanding settlement and arrange next action or wait
  → observe changes and choose continuation, replanning or stopping
```

This is a teaching responsibility chain, not a new global API order. No mechanism guarantees success alone. Understand how mechanisms supply one another's evidence and which owner must handle each failure.

## Reading routes and evidence

**First reading:** enter [one complete turn](03-one-turn.md#running-turn), then return to [state](state-substrate.md) and [authority](work-graph-and-authority.md#design-choice) to understand its facts and writers. Continue with [recovery](04-runtime-boundaries.md) and [observation](04b-budget-and-admission.md). This matches the reading guide and requires no memorized state-machine enums.

**Alternative:** experienced control-plane readers can study state and authority before the Turn. The state-machine topic and appendix are references, not mandatory prerequisites to onboarding.

| Boundary | Protocol entrypoint | What it does not establish alone |
| --- | --- | --- |
| Sources and projections | [Long-horizon state](/loopx/docs/reference/protocols/long-horizon-agent-state-protocol-v0/) | Every reader is real-time |
| Turn recovery | [LoopX Turn](/loopx/docs/reference/protocols/loopx-turn-v0/) | Every external effect can be retried automatically |
| Assignment and takeover | [Peer runtime](/loopx/docs/reference/protocols/peer-agent-runtime-v1/) | Every mode has the same fence |
| Budget and waiting | [Quota contract](/loopx/docs/quota-allocation/) | A Host without events detects changes immediately |

Carry the guide's [four-question standard](00-reading-guide.md#judgment-standard): current facts, allow/refuse reasoning, supporting evidence and a legal next entrypoint. Retain unknowns instead of guessing to complete the story.
