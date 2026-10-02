# Recovery, self-repair, and runtime boundaries

The hard part of a long-running system is not "continue forever." It is deriving a legal next action after
the session, Host, Agent, workspace, or external facts have changed. This chapter separates recovery,
replan, self-repair, and terminal closure, then uses clear runtime responsibilities to prevent an
Extension, Provider, or projection from crossing the authority boundary.

## What you should learn

After this chapter, you should be able to:

- explain which facts to replay and which must be freshly inspected after interruption;
- distinguish continuation, replan, self-repair, and retry;
- recognize projection gaps, stale evidence, and workspace drift;
- distinguish Agent, Provider, Capability, Kernel, and Extension;
- decide when a Goal is terminal rather than only seeing every current Todo checked;
- detect a broken public/private boundary.

## Recover action conditions, not old thoughts

Assume Codex CLI closes after local tests pass and Codex App takes over the next day. The new session does
not need a verbatim transcript. It does need to reconstruct:

- Goal, acceptance, and current per-Agent Vision;
- open Todos, dependencies, claims, and continuation;
- unresolved Gates and decision scopes;
- commands, revisions, and freshness attached to evidence;
- current worktree, Host capabilities, and write scope;
- external handles, readbacks, and monitor due state;
- current interaction contract and stop condition.

Some facts replay from durable project state. Others require a fresh inspection:

| Replayable facts | Facts to inspect again |
| --- | --- |
| Goal identity, Todo lineage, Gate resolution | Current checkout and uncommitted diff |
| Run and evidence references, old receipts | Current CI, PR, Issue, or cloud state |
| Registered Agents and policy | Current Host capability and login state |
| Previous scheduler proposal | Current time, monitor due state, and execution context |

An old receipt proves that an action succeeded for bound input and revision. It does not prove the external
world remains unchanged. An old claim does not prove the Agent is still running.

## Continuation, retry, replan, and self-repair

These four actions solve different failures.

### Continuation

Goal, frontier, and protocol semantics are materially unchanged. A new turn performs another bounded
segment on the existing Todo. Even when a Host session can resume, current guards must still rerun.

### Retry

The action remains legal, but transport, timeout, or temporary environment failure prevented a reliable
result. Retry needs an idempotency boundary, attempt identity, and readback so an already-successful
effect is not executed again after its response was lost.

### Replan

The work semantics must change. Examples include:

- Goal, acceptance, or per-Agent Vision drift;
- an exhausted frontier while acceptance remains open;
- a satisfied dependency whose old Todo needs a successor;
- new evidence that invalidates the plan;
- repeated surface activity without outcome progress;
- a peer whose role scope no longer covers the next step.

Replan must produce an observable delta: Todo, Vision, acceptance, successor, supersession, or
no-follow-up. Writing "reassessed, continuing original plan" does not necessarily satisfy a replan
obligation.

### Self-Repair

The target work may still be correct while the control plane is inconsistent:

- an event source and status projection disagree;
- a User Todo count exists without a concrete Gate payload;
- a stale Next Action points to a completed Todo;
- the wrong worktree remains configured as the delivery workspace;
- a monitor lacks target, cadence, or bounded observation handle;
- writeback and spend lineage is incomplete.

Self-repair fixes state, projection, or boundary. It does not weaken a Gate or invent permission.

### Dreaming vs replan boundary

Both Replan and Dreaming change the imagination of the future, but only one is executable:

- **Replan** is a machine-visible change on the current goal graph: add/remove Todos, change Gates,
  write successors, update acceptance. It produces new facts that quota and frontier can directly read.
- **Dreaming** is exploring future possibilities: a new branch direction, an alternative approach, an
  unverified hypothesis. It can only produce proposals, not replace the current runnable frontier.

The key distinction: an agent writes a set of draft Todos during dreaming, but does not write them into
the current goal's frontier through a lifecycle command. They are not executable tasks at that point, and
the next quota round will not select them. If an agent skips replan and lets a dreaming proposal
impersonate executable tasks, quota will continue running on the wrong frontier.

The correct flow is: dreaming produces a proposal -> operator or autonomous replan decides to accept ->
accepted proposal is written into the goal graph via lifecycle command -> next quota round sees it.
Replan writes into the goal graph; Dreaming writes into the proposal space. The two cannot substitute for
each other.

## Long-horizon convergence: a Turn is not the unit of progress

Long-running work does not approach its Goal merely because it executes more Turns. A Turn may be a legal
wait, or it may produce a large diff without adding evidence that can change the next decision. To judge
convergence, separate four operating states:

| State | Observable property | Correct action |
| --- | --- | --- |
| Legal iteration | Input, revision, or evidence changed, making the next action distinguishable | Execute one new bounded Turn |
| External wait | No current action exists, but recovery condition, target, and next due time are explicit | Monitor, backoff, and quiet |
| Goal drift | A local metric or current Todo begins to replace Goal or Acceptance | Vision checkpoint, acceptance audit, and replan |
| Local loop | The same action family repeats without new information, state delta, or failure discrimination | Stop repeating; diagnose, replan, or self-repair |

Repetition alone is not a loop. Processing a PR again after checks move from pending to failed is legal
iteration. Observing an external training task at its due time is legal waiting. Work is spinning only when
input facts, attributable evidence, and the next plan all remain materially unchanged while the same class
of Turn continues to consume resources.

### Material evidence delta

A Turn that deserves more resource consumption should advance at least one of these:

- a new observation changes the current domain judgment;
- new evidence supports or excludes a testable explanation;
- a validated artifact satisfies an acceptance condition;
- a successor, Gate, blocker, Vision, or no-follow-up changes the machine-visible frontier;
- a Provider effect receives a receipt bound to proposal identity, revision, and readback;
- the system proves that it can only wait and writes the target, cadence, and recovery condition.

More logs, rewritten summaries, a refresh of the same projection, another unchanged poll, or a test result
that cannot bind to the current revision are not material progress. They may be diagnostic steps, but they
must not impersonate Goal advancement.

### Outcome Floor: preventing micro-actions from impersonating progress

A multi-file diff can still be surface-only changes without genuinely advancing acceptance. LoopX uses two
levels of granularity to distinguish "did work" from "advanced the goal":

**Delivery Scale**:

| Value | Meaning |
| --- | --- |
| `test_only` | Only ran tests, no new artifact produced |
| `single_surface` | Modified a single file or surface |
| `multi_surface` | Crossed multiple files/modules |
| `implementation` | Produced a verifiable functional implementation |

**Delivery Outcome**:

| Value | Meaning |
| --- | --- |
| `surface_only` | Artifact exists but did not advance acceptance |
| `outcome_gap` | Advanced a sub-goal but did not close it |
| `outcome_progress` | Advanced an acceptance of the primary goal |
| `primary_goal_outcome` | Directly closed a primary acceptance |

Key rule: a `multi_surface` delivery can still be a `surface_only` outcome. After consecutive
`surface_only` or no-progress deliveries, quota will require the next delivery to produce a genuine
outcome or self-repair. This is not a penalty for "writing a lot," but a guard against substituting surface
activity for goal advancement.

Relationship to material evidence delta: outcome is the semantic classification of evidence delta. A
delivery that neither changes the machine-visible frontier nor advances acceptance has neither material
delta nor outcome.

### Six convergence invariants

Review a long-running chain with six questions:

1. **Direction:** Can the current Todo still be traced to Vision, Goal, and Acceptance?
2. **Authority:** Does the transition affect the correct object under the correct Agent, Gate, or Host
   capability?
3. **Evidence:** Is the observation fresh, and is evidence bound to revision, scope, and evaluator?
4. **Delta:** Did this Turn change replayable facts, the frontier, or a wait condition?
5. **Liveness:** If acceptance remains open and the frontier is empty, did the system create a wait, replan,
   repair, or explicit stop?
6. **Closure:** Does terminal state close Todos, Monitors, Gates, successors, receipts, and acceptance gaps?

These six keep Safety and Liveness in the same loop: Safety prevents an invalid transition; Liveness
prevents a system from remaining cautiously stuck forever. A successor reconnects local completion to the
Goal; Monitor backoff avoids hot polling while waiting; Replan changes a failed route; Self-Repair fixes
control-plane gaps; and terminal audit prevents "all current Todos are checked" from becoming a false
completion claim.

For the complete paired-Showcase replay, evidence-delta criteria, independent oracle, and convergence
experiments, use
[Long-horizon convergence](/loopx/docs/development/control-plane-course/topic-long-horizon-convergence/).
For evidence, refresh, spend, and repair delta source paths, see
[Control-Plane Course Lesson 8](/loopx/docs/development/control-plane-course/08-evidence-refresh-and-self-repair/).

## Handle a projection gap in order

When two surfaces disagree:

```text
detect mismatch
  -> identify authoritative source
  -> classify source-write / projection / migration / freshness failure
  -> repair through the owning protocol
  -> recompute and validate
  -> rerun quota
```

If active-state Markdown marks a Todo complete while the event projection remains open:

1. check whether completion passed through a lifecycle command and formed an event;
2. if only Markdown changed, normalize valid evidence into the canonical transition;
3. if the event exists, repair the projection head or sequence;
4. rerun status and quota;
5. do not execute a dependent successor until the state is consistent.

Do not hand-edit Markdown, a dashboard fixture, and a status cache until they merely look consistent.

## Vision checkpoint and acceptance gaps

[`goal_vision_replan_contract_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/goal-vision-replan-contract-v0.md)
requires an Agent that uses Vision to record one of these outcomes after material refresh:

- Vision was patched;
- Vision remains unchanged, with a reason;
- Vision is satisfied and retired;
- a successor supersedes it;
- the current role does not require Vision.

A missing required checkpoint can produce a `vision_checkpoint_missing` acceptance gap. The purpose is not
to make an Agent write more visionary prose. It is to prove that local delivery did not move the Agent's
lane away from the Goal.

Goal-level replan takes precedence over monitor quiet or agent-scope wait. Otherwise the system can remain
quiet because no current Todo is runnable while acceptance still has an open gap.

### Vision unchanged honesty condition

Claiming "Vision unchanged" is not always safe. On the first material closeout, there is no baseline, so
claiming unchanged is judged as `missing_required`: the system cannot distinguish "truly unchanged" from
"never checked." Therefore the first round must write a vision patch; it cannot bypass with "unchanged."

For subsequent rounds, claiming unchanged requires:

- a comparable baseline exists (the vision written in the previous round);
- this round's delivery did not genuinely change any vision premise;
- the writeback explicitly references the baseline revision and the "unchanged" reason.

If the baseline is missing but the agent still claims unchanged, quota will produce a
`vision_checkpoint_missing` gap. This is not a punishment, but a guard against the agent accumulating
wrong assumptions on a never-checked state. For the full failure replay, see
[Control-Plane Course Lesson 8](/loopx/docs/development/control-plane-course/08-evidence-refresh-and-self-repair/).

## Terminal closure

Every current Todo being done proves only that the list ended. A terminal audit also checks:

```text
open todos = 0
due monitors = 0
unresolved blocking gates = 0
pending successors = 0
replan obligations = 0
acceptance gaps = 0
retryable postconditions = 0
required external readbacks are fresh
```

If acceptance is satisfied and no follow-up is needed, record structured no-follow-up. If work remains,
create a successor. If an external result is still pending, preserve a monitor or blocker. Do not delete
open state to make the Goal look complete.

## Four runtime responsibilities

Long-running Agent systems often call every component a "tool" or "plugin." LoopX uses four runtime
responsibilities:

| Responsibility | Contract |
| --- | --- |
| Agent / Executor | Plans and executes one allowed bounded action in a Host |
| Provider | Calls an external system and returns an observation, effect result, or readback |
| Capability | Defines a caller outcome, normalizes Provider output, and applies domain policy |
| LoopX Kernel | Accepts or rejects a proposal and owns generic Goal, Todo, Gate, quota, and recovery state |

The normal flow is not "the Agent called a tool, therefore the Todo is done":

```text
Agent -> Capability -> Provider -> external system
Provider readback -> Capability validation/proposal -> LoopX transition
```

A Capability is the outcome contract a caller can depend on. A Provider implements or accesses an external
system. The Kernel owns cross-domain lifecycle. Domain results such as Issue-Fix or Explore can own their
Domain State, but they must not own generic quota, Gates, or permission in reverse.

## Extension is a delivery and lifecycle boundary

An **Extension** has independent:

- packaging;
- installation;
- enable and disable;
- upgrade and rollback;
- compatibility;
- provider ownership.

It is not a fifth runtime responsibility and does not automatically gain domain authority:

```text
Extension package
└── delivers Provider
      └── participates in Agent -> Capability -> Provider -> Kernel flow
```

For a deterministic, zero-permission standalone Extension, LoopX can call a bounded request/response
command through the managed runtime. As soon as an operation needs read, write, send, publish, or manage
authority, it must enter a Capability or domain command that can enforce permission, decision scope, and
domain policy.

"Installed," "doctor-ready," and "authorized for this effect" are three different states.

## Who owns each fact

### LoopX canonical state

LoopX owns work-lifecycle facts:

- Goal, Todo, and Gate;
- claim, lease, dependency, and successor;
- quota, monitor, and scheduler hint;
- accepted evidence pointer and receipt;
- event lineage, Vision checkpoint, and projection inputs.

### External systems

External systems remain authoritative for their facts:

- Git owns commits and branches;
- GitHub owns current PR, Issue, and check state;
- CI owns job results;
- a cloud service owns actual resource state;
- the Host owns its session and actual wake-up effect.

LoopX may retain bounded observations, readbacks, and evidence pointers. A stale copy must not replace the
external authority.

### Host and Agent

The Host owns sessions, model turns, tool surfaces, and actual wake-up mechanisms. The Agent owns current
reasoning and a temporary plan. Neither can be the sole owner of project Goal state.

The Host follows the current `interaction_contract` and `scheduler_hint`. It must not preserve
project-specific control logic indefinitely in a heartbeat prompt. The Agent cannot infer current
authority merely because a similar action was legal in a previous turn.

## Public/private boundary

Project control state commonly contains material that must not be committed publicly:

- local registry and active Goal state;
- task leases and Host session handles;
- raw transcripts, trajectories, and verifier tails;
- credentials and private Provider configuration;
- machine paths, internal links, and private organizational narrative;
- unredacted external evidence.

The project-onboarding chapter requires these directories to stay outside Git:

```text
.loopx/
.loopx/goals/
.local/
```

Ignore rules are only one defense. Before publication, still scan for credentials, absolute paths, raw
logs, private links, and runtime artifacts. Durable public conclusions should first become public-safe
behavior, schema, fixtures, or evidence pointers.

A handoff must not copy private material into a public packet. It transfers stable ids, bounded references,
freshness, omission notes, and legal routes for reacquiring material.

## What LoopX does not replace

LoopX does not replace:

- Agent runtime: the model still performs reasoning;
- Host scheduler: the Host still performs actual wake-up;
- Git: code history and branches are still managed by Git;
- CI: test execution and check state are still managed by CI;
- external service authentication: TurnEnvelope and receipt are not security tokens;
- domain system: LoopX does not fabricate external resource facts;
- independent validator: an Executor's own completion claim is not sufficient proof.

These boundaries support two later practice paths:

1. **Project onboarding**: reuse these protocols without modifying LoopX source.
2. **Developer contributions**: locate the owning boundary from the caller outcome and protocol, and
   deliver Control Plane, Capability/Domain State, Provider, Host/Runner,
   Projection/Dashboard, Docs/fixtures, or Extension.

Extension is an independent packaging/lifecycle path within developer contributions, not the unified
abstraction for all contributions. The two paths share the same control-plane model and do not require one
another. The next part starts with the most common job: project onboarding.