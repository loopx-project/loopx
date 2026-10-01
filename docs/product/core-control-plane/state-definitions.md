# State Definitions

LoopX state should be small, observable, and reusable. A state definition is
valid only when a future agent or product surface can answer: where does this
state come from, who can change it, and what transition does it allow?

This file keeps public-safe definitions for the core state bodies and runtime
states used by the interaction catalog and state machine.

## Canonical State Bodies

| State Body | Source Of Truth | Primary Writer | Meaning | Must Not Mean |
| --- | --- | --- | --- | --- |
| Registry | Project/global registry | `connect`, project setup, registry sync | Goal identity, active-state path, runtime root, registered agents, primary owner, and runtime routing. | A promise that automation has already run. |
| Active State Workbench | Active goal state projection or Markdown workbench | LoopX lifecycle commands and controlled agent writeback | Human-readable current goal, progress, todos, gates, validation notes, and next action. | Canonical truth when an event/todo projection says otherwise. |
| Todo | Todo projection or event stream | `loopx todo` and compatible lifecycle writers | Smallest executable or waiting unit, with role, priority, status, task class, action kind, capability hints, and evidence refs. | A full project plan or a hidden chat reminder. |
| Claim | Todo metadata, lease projection, or event stream | `loopx todo claim`, owner reassignment, lease refresh | Soft ownership and routing signal for one agent or lane. | A lock that permits ignoring better evidence or current gates. |
| Gate / Decision Scope | User todo, operator gate, or quota interaction contract | User/controller decision writers | Concrete authority still needed, including the scope it blocks and how it can be resolved. | A global stop sign unless explicitly scoped as global. |
| Dependency / Resume | Todo metadata and event refs | Todo lifecycle writers | Wait condition, unblock relation, successor relation, or supersession chain. | A reason to lose the original task after waiting. |
| Evidence Bundle | Todo/run/event refs | Agent writeback, reducers, validation tools | Compact proof, artifact refs, source refs, validation result, blocker, or rollback anchor. | Raw private logs, transcripts, credentials, or unsupported claims. |
| Run Snapshot | Run history | Adapter, refresh-state, execution wrappers | What one bounded turn saw, attempted, recommended, and delivered. | The whole project memory. |
| Event Ledger | Append-only events | LoopX lifecycle commands | Ordered lifecycle facts for todos, gates, runs, evidence, quota, projections, and rollbacks. | A mutable note file. |
| Projection | Status, quota, frontstage, review packet, dashboard | Projection builders | Read-only rendering of source facts for one consumer. | A write API or source of truth. |

## State Taxonomy And Ownership

| Category | Examples | Ownership and lifetime |
| --- | --- | --- |
| Persistent domain state | Todo lifecycle, lease generation, gate scope | Its domain writer accepts transitions; storage preserves the accepted facts |
| Derived decision | `eligible`, `successor_replan_required`, final interaction contract | A decision owner computes it from explicit source facts; consumers cannot promote a diagnostic hint into permission |
| Execution or settlement phase | writeback pending, receipt-backed settlement, no-spend closeout | The effect owner advances it only on the required evidence; a plan alone is not a receipt |
| Read projection condition | stale source, `projection_gap`, delivery pending | The read/delivery owner reports observation limits; a stale view does not roll back canonical state |

The same word can occur in different domains. Always identify its owner and
field rather than infer equivalence from spelling. Each changed state contract
names its producing condition, legal exits, required evidence and interpreting
consumers. Generate mechanical tables from existing typed owners where feasible;
review the semantic explanation independently. This document is a map, not a
second enum registry or writer.

Todo completion, Turn settlement, Goal completion and result delivery are
separate facts. A validated in-flight writeback can settle a Turn while its Todo
remains open; a committed monitor observation can close without spending quota.
A historical receipt records what happened and does not grant a new execution.
`Running` remains derived from execution facts, not a new persisted Todo status.
Legacy wire flags retain their codec semantics: for example, the canonical Todo
record currently maps both `done` and `deferred` to `done=true`; callers must use
the owning lifecycle interpretation rather than infer successful completion from
that flag alone.

Use products for independent dimensions and sums for dependent alternatives.
Within a selected TS domain, decode wire input into a constrained internal value;
retain runtime source/fence checks because legal shape is not current authority.
See [typed values](../../architecture/rfcs/typescript-control-plane-migration-v0.md#27-legal-domain-values-and-deterministic-decisions)
and [composition verification](../../architecture/rfcs/composable-state-machines-recovery-verification-v0.md).

## Runtime Decisions, Phases, And Read Conditions

The following names span the categories above; they are not members of one
persisted lifecycle. They support status, quota, scheduler, frontstage, review
packets and agent prompts. Their source field and owner determine their role.

| Runtime State | Derived From | Meaning | Agent Behavior | User Behavior |
| --- | --- | --- | --- | --- |
| `eligible` | Registry + active state + todo + quota | There is runnable or repairable work and no active gate covers the selected action. | Deliver one bounded segment, validate, write back. | Usually no interruption. |
| `bounded_delivery` | `eligible` plus selected todo | The current turn should create an artifact, blocker, evidence observation, or state update. | Must attempt; spend only after validated writeback. | Review only if result needs approval. |
| `user_gate` / `operator_gate` | Gate + decision scope + interaction contract | A human/controller decision blocks the selected action. | Ask or notify the concrete gate; do not run the gated path. | Answer, defer, reject, or redirect the decision. |
| `scoped_user_gate_fallback` | Gate + independent todo + decision scope | A gate remains open, but another action is independent and safe. | Surface the gate, run only the independent fallback, validate. | See the gate without being forced to answer before fallback work. |
| `agent_scope_wait` | Todo claims, blocks_agent, handoff gate, agent id | The current agent has no in-scope runnable candidate, or another owner holds the blocker. | Stay active and quiet; wait for reassignment, unblock, or new scoped work. | Usually no interruption. |
| `successor_replan_required` | Dependency / Resume + Handoff + Todo lifecycle | A deferred or handoff gate has cleared, but the current agent still has no stable successor, supersede link, or no-follow-up rationale to run. | Do not run ordinary delivery yet; reopen, supersede, create a successor, or record no-follow-up, then rerun the guard. | Usually no interruption unless the successor decision is user-held. |
| `waiting` / `external_evidence_observation` | Waiting metadata, monitor todo, external handle | Work depends on terminal external evidence or compact observation. | Observe only bounded public-safe handles; write blocker if no handle exists. | Supply missing handle only when asked concretely. |
| `monitor_quiet_skip` | Continuous monitor todo + cadence metadata | The monitor is not due or has no material transition. | Append at most one no-spend poll when contracted, then stay quiet. | No interruption. |
| `focus_wait` | Outcome floor, handoff readiness, delivery outcome | The lane needs outcome-scale evidence, clean baseline, or fresh owner evidence before ordinary delivery. | Recover the named evidence or report a blocker. | Decide only if the blocker is owner-held. |
| `blocked_health` | Registry/projection/boundary checks | Control-plane health is broken enough that delivery would be unsafe. | Repair if allowed; otherwise stop with a concrete blocker. | Review only concrete repair gates. |
| `workspace_guard` | Agent profile + current worktree + requested goal | The agent is in the wrong checkout or missing the required isolated lane. | Relocate or write a concrete workspace blocker before quota work. | No interruption unless relocation needs owner action. |
| `capability_gate` | Todo required capabilities + host/runtime capabilities | Some candidates need capabilities the current host lacks. | Run a runnable candidate, repair a bridge, or ask for owner-held capability. | Provide credentials or protected access only through a concrete gate. |
| `throttled` / `paused` | Quota ledger or explicit pause | The goal should not spend automatic compute now. | Stay quiet. | Resume or add quota if desired. |
| `writeback_spend` | Validated artifact/blocker/evidence + quota contract | The turn produced durable value and may account for one spend. | Write state/history, then spend exactly once. | No interruption. |
| `done` / `archived` | Todo/goal terminal state | No required work remains for that unit. | Stop or create successor only when needed. | Review final summary if surfaced. |
| `projection_gap` | Projection mismatch, stale sink, missing concrete todo | A display or prompt projection is incomplete or conflicts with source state. | Repair source/projection before using the stale view. | No vague gate; ask only for missing concrete user input. |

## Channel Invariant

User and agent channels can disagree without contradiction. For example,
`scoped_user_gate_fallback` intentionally means:

```text
user_channel.action_required = true
agent_channel.must_attempt = true
agent_channel.selected_action = independent_fallback
```

The user channel names the open decision. The agent channel names the safe work
that does not depend on that decision. A projection is wrong when it collapses
these into one boolean and either blocks all work or hides the gate.

## Definition Checklist

Before adding a new state name:

1. Identify the source state body or projection field.
2. Identify who can write the source.
3. Identify the legal next transition.
4. Identify whether the user must be interrupted, notified, or left alone.
5. Identify a public-safe evidence shape and validation path.

If any item is missing, prefer refining a catalog pattern or projection field
over adding a new state.
