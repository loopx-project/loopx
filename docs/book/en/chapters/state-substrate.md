# Durable State and Read-Only Projections

> Update (2026-09-25): the Todo `events.jsonl` API, replay, backfill and completion
> examples below describe a retired experiment. Current Todos use legacy Markdown
> or the selected File/SQLite authority. See the
> [retirement contract](../../../reference/protocols/event-sourced-state-contract-v0.md).


Long-running work does not recover because a system stores more conversation. It recovers because each
important fact has a stable owner and can be projected into a fresh decision. This chapter establishes
the LoopX state substrate: which surfaces own facts, which surfaces only help readers, and why a page or
Markdown file that "looks like current state" cannot automatically become a write entrypoint.

## What you should learn

After this chapter, you should be able to:

- distinguish the registry, event ledger, active-state workbench, run history, and status projections;
- decide whether a field belongs to canonical state, an external authority, or a derived read model;
- explain how append-only events, replay, idempotency, and freshness relate;
- explain why a dashboard, prompt, or task graph must not become a second state machine;
- when a protocol changes, find the authoritative source rather than relying on a Python function name.

## Goal Identity Does Not Belong to a Chat Thread

The durable identity in LoopX is the **Goal**, not a Host thread:

```text
Goal
├── objective and boundary
├── todos, gates and evidence lineage
├── registered peer identities
└── runtime and projection routes

Session / thread
└── one temporary executor context
```

Codex App, Codex CLI, or another Host can advance the same Goal at different times. One session can also
read more than one Goal. Reading a Goal does not grant write authority, and ending a session does not
delete the Goal.

### Reuse an Exact Goal Instead of Guessing from Text

Goal reuse depends on a stable `goal_id` and registry connection, not fuzzy objective similarity:

```text
one registered goal
  -> reuse that exact goal boundary

multiple registered goals
  -> read-only goal_selection_gate
  -> choose one exact goal_id
  -> rerun before any mutation
```

When a project has several registered Goals, `start-goal --guided` should list their ids, status, and exact
rerun commands. Todo writes, Agent registration, and Host activation wait until the selection is resolved.
Similar objective text, a shared repository, or overlapping acceptance criteria do not authorize LoopX to
merge Goal boundaries silently.

Keep Goal reuse separate from Agent takeover. A new Agent can read the same public frontier and history,
but it registers a fresh `agent_id` by default. Reusing an existing Agent identity requires the user to
select that exact id. This preserves historical lineage without letting a new session inherit execution
responsibility under another identity.

Recovery is therefore not:

```text
restore = replay the old conversation
```

It is:

```text
next decision =
  replay(durable project facts)
  + inspect(fresh workspace and external facts)
```

An old conversation may help interpretation. It cannot outrank current Git state, an unresolved Gate,
current CI, or LoopX canonical lifecycle state.

## Five State Surfaces

### 1. Registry: Identity, Connection, and Durable Policy

The registry answers "which Goal is this, where is it connected, and which runtime routes are allowed?"
It can carry:

- Goal id, repository, and active-state route;
- local or global runtime roots;
- registered Agent identities;
- coordination, write scope, and workspace guards;
- configuration for default-off features.

The registry does not prove that a Host successfully started. It also does not store every Agent result.
It owns connection and policy facts, not execution receipts.

### 2. Event ledger: what happened

[`event_sourced_state_contract_v0`](https://github.com/huangruiteng/loopx/blob/main/docs../../../reference/protocols/event-sourced-state-contract-v0.md)
represents Todo, Gate, run, evidence, projection, and quota changes as append-only events.

At least four invariants matter:

| Invariant | Why it matters |
| --- | --- |
| Append-only | New facts extend history instead of rewriting it to hide an earlier action |
| Ordered | Replay reconstructs the same lifecycle sequence |
| Idempotent | Replaying the same `event_id` with the same payload does not duplicate the effect |
| Privacy-partitioned | Public-safe summaries do not mix with local or private payloads in the same public stream |

Marking a Markdown checkbox as `[x]` is not sufficient proof that a Todo completed. A legal transition should keep
the Todo id, producer, completion evidence, time, and event lineage so status, review packets, and the next
quota decision can reuse the same fact.

### 3. Active-State Workbench: A Human-Readable Work Surface

`ACTIVE_GOAL_STATE.md` helps humans and Agents read the Objective, Next Action, User Todos, Agent Todos,
and Progress. It is an important workbench, but "all truth lives in Markdown" is the wrong model.

During migration or compatibility windows, Markdown may still participate in Todo reads. Canonical writes
should still pass through LoopX lifecycle commands and controlled writeback so they form governed events.
Editing a projected paragraph does not automatically perform a lifecycle transition.

[`active_state_structured_projection_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/active-state-structured-projection-v0.md)
defines a typed, read-only view of Todos, Gates, and Next Action from that workbench. The contract preserves
several boundaries:

- a projection can be recomputed;
- a projection grants no write authority;
- a generated compatibility id is not automatically a migration-ready canonical id;
- duplicate ids or missing sections become diagnostics instead of disappearing silently.

### 4. Run History: What One Bounded Turn Observed and Delivered

Run history keeps a compact index for one bounded turn, such as:

- participating Agent, Todo, and Goal;
- whether the run was observation, delivery, or blocker work;
- validation and evidence references;
- delivery scale and outcome;
- successor, replan, or no-follow-up;
- whether spend conditions were satisfied.

A run snapshot is not complete project memory. It answers what this turn saw, attempted, proved, and wrote
back. Goal lifecycle still depends on the combination of Todos, Gates, events, evidence, and acceptance.

Rich logs, raw transcripts, and verifier tails can stay in local or private runtime artifacts. A public
projection should retain only the bounded references required for review and recovery.

### 5. Status and other projections: how a consumer reads now

`loopx status`, `quota should-run`, dashboards, review packets, and task graphs are different read models
for different consumers.

They may:

- aggregate several source facts;
- compress large payloads;
- reorganize state for a user, Agent, CLI, or operator;
- surface staleness, gaps, repair needs, and attention signals.

They must not:

- invent a Todo absent from the source;
- turn display order into lifecycle priority;
- let card or graph edits bypass the write API;
- treat a stale external observation as a current fact.

[`task_graph_projection_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/task-graph-projection-v0.md)
is explicit about this boundary. Relationships such as `blocks`, `validates`, `continues`, and
`hands_off_to` are derived graph edges, not new scheduling commands.

## Three Ledgers: Turn Journal, Goal State, and Run History

Treating "all records" as the same kind of state leads to "phase recorded" being mistaken for "business
transition." LoopX distinguishes three ledgers:

| Ledger | What it owns | Lifecycle | Typical use |
| --- | --- | --- | --- |
| Turn journal | Recovery information for a single transaction | Single transaction | Recover an interrupted bounded segment |
| Goal/event state | Durable lifecycle transitions | Cross-session, cross-Host | Determine current frontier, Gate, acceptance |
| Run history/status | Historical evidence index and projection | Read-only, not rewritable | Context for review, replan, handoff |

**Turn journal** answers "what happened in this turn and how to recover if interrupted." It records
temporary state within a single transaction, not durable business facts. The classic mistake of treating a
journal as goal state: an agent sees "entered phase three" in the journal and assumes the goal has
transitioned to phase three. But the journal only records what the agent intended; only goal/event state
records the actual completed transition.

**Goal/event state** answers "what is the current frontier, and who can do what." It records lifecycle
transitions (Todo completion, Gate resolution, Vision update) through append-only events and supports
cross-session reconstruction. It is the authoritative source for durable lifecycle facts, and quota
compiles it together with registry/boundary, Todo/Gate, capability/workspace, run outcomes/history,
scheduler context, and fresh external facts.

**Run history/status** answers "what happened historically, and what evidence exists." It is read-only and
cannot write back into goal state. A run record saying "tests passed this round" does not mean the
corresponding acceptance in goal state is closed—only a transition written through a lifecycle command
counts.

The practical significance of distinguishing these three: before every write-back, confirm you are writing
to goal/event state (a transition) and not to the turn journal (temporary records); before every decision
read, confirm you are reading goal/event state and not an old projection from run history. For complete
source paths and experiments on the three ledgers, see
[Control-Plane Course Lesson 8](/loopx/docs/development/control-plane-course/08-evidence-refresh-and-self-repair/).

## Canonical state, workbench, projection, and external fact

Keep these four categories separate:

| Layer | Typical content | Who can change it | Can it directly authorize a transition? |
| --- | --- | --- | --- |
| Canonical state | Events, typed Todos, Gate resolution, quota spend | LoopX lifecycle writer | Yes |
| Workbench | Active-state Markdown and human explanation | Controlled writeback or compatibility editing | Only after normalization into governed facts |
| Projection | Status, quota packet, dashboard, task graph | Projection builder | No; it informs a decision |
| External fact | Git commit, PR, CI result, cloud resource | The corresponding external system | Only through fresh readback and evidence |

A page saying "the PR is merged" may be a stale projection. A previous run saying "tests passed" may refer
to an older commit. Inspect the external system and verify revision, freshness, and scope before using that
observation for a current transition.

## Storage Medium Is Not the Authority Contract

The current LoopX control plane is **local-first**: the project registry, active-state workbench, event and
run history, and runtime state live in project-local or user-local storage. This does not make a Markdown
file the authority by itself, and replacing files with a database does not automatically create correct
concurrency or recovery semantics.

[`event_sourced_state_contract_v0`](https://github.com/huangruiteng/loopx/blob/main/docs../../../reference/protocols/event-sourced-state-contract-v0.md)
allows JSONL, SQLite, or another local-first append-only implementation when it preserves:

- stable event ids and ordered replay;
- idempotent append;
- projection head alignment with the event-store head;
- public-safe, local-private, and private-pointer partitions;
- Markdown as a workbench or projection rather than an arbitrary write API.

[`local_state_write_correctness_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/local-state-write-correctness-v0.md)
is currently marked as a public-safe protocol draft. Its stronger write-correctness target separates
`prepare -> preview -> apply -> record -> project`:

- one `idempotency_key` should not duplicate the logical effect;
- an `expected_revision` mismatch should fail closed or recompute a non-overlapping patch from fresh state;
- a foreign or expired lease should never be silently cleared;
- the default target boundary is one Goal, narrowed only for a single order-independent Todo write;
- external writes, credentials, production, and private reads remain behind independent Gates.

Current Todo lifecycle commands reread and write under the active-state file lock, and preview exposes a
write intent. The protocol also states that hard idempotency, uniform optimistic CAS, and lease-conflict
enforcement are promoted writer by writer. Do not assume that every writer already enforces the complete
Draft.

Files, SQLite, and future providers answer "where are the bytes?" Events, revisions, CAS, leases, and
authority answer "which transition is legal?"

### Shipped Boundary Versus Design Boundary

Shared-authority work in `v0.5.4` is more than a paper design: the repository contains a
provider-neutral TypeScript `AuthorityStore` contract plus staged file, NoKV, and PostgreSQL candidates
with conformance evidence. That is still not an enabled shared control plane. The release does not wire
these candidates into the default runtime or ship a ready-to-enable remote authority service. Installing
a Provider does not change a Goal's source of truth.

Post-`v0.5.4` `main` may contain further default-off shadow, parity, fencing, or provider-first cutover
slices. They prove migration mechanics; they are not evidence that `v0.5.4` shipped cloud collaboration.
Use the matching tag and release notes for stable behavior and the current stage in the
[Shared Control-Plane Authority RFC](/loopx/docs/architecture/rfcs/shared-goal-authority-state-provider-v0/)
for experimental status.

You can currently rely on:

- local project state and the global registry projection;
- the Todo lifecycle writer's active-state file lock, preview/readback, and currently implemented
  idempotency behavior;
- registered peers, soft claims, optional task leases, and independent-worktree guards;
- several Hosts reading one registry and Goal through controlled writeback.
- staged file, NoKV, and PostgreSQL candidates for development and qualification; they do not acquire
  runtime authority automatically.

Do not currently promise:

- automatic online authority shared across devices;
- new claims, completion, lease renewal, or protected writes while a device is offline;
- consistent distributed state from putting the project directory in a sync drive;
- NoKV, a database, or IM automatically replacing the LoopX lifecycle owner.

A future cross-device control plane should retain one canonical LoopX authority, revision-bound idempotent
commands and receipts, and separate message delivery, context memory, and state authority. Until the Draft
has an independently reviewed promotion, runtime wiring, and release validation, this book teaches those
boundaries rather than a fictional cloud-mode quickstart.

## Three layers of integrity for historical artifacts

LoopX can prevent research, validation, and decision artifacts from being rewritten silently. That does not
make an old conclusion perpetually applicable. Evaluate historical evidence in three layers:

| Layer | Question | Typical checks |
| --- | --- | --- |
| Lineage integrity | Who produced the artifact, when, and was it appended, corrected, or superseded? | `event_id`, `run_id`, producer, recorded revision, append-only references |
| Current applicability | Do its inputs, scope, and external facts still match the current problem? | Commit, target key, source revision, time window, Gate scope, fresh readback |
| Supersession | Did later evidence or a decision replace, narrow, or revoke it? | `supersedes`, `superseded_by`, compensating event, newer decision, replan delta |

Append-only lineage protects history from silent alteration; it does not prove that an old conclusion is
fresh. Research notes, test results, and PR readbacks need stable join keys and a new applicability check
after material inputs change. When applicability is unknown, retain the artifact as a historical
observation or stale evidence instead of deleting it or treating it as current authority.

[`agent_scoped_evidence_ledger_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/agent-scoped-evidence-ledger-v0.md)
provides a bounded, read-only Agent chronology for replan and handoff. It does not replace current status,
a quota decision, or external-system readback.

## Replay Does Not Preserve an Old Conclusion Forever

Replay aims to reconstruct current state from ordered facts, not to permanently preserve old judgments.

Assume the ledger contains:

```text
todo_added(T1)
todo_claimed(T1, agent-a)
gate_added(G1, scope=public_claim:action:homepage)
run_recorded(R1, tests_passed_at=commit-a)
```

Git later advances to `commit-b`, and the user changes the homepage direction. Replay still proves that R1
and G1 existed. It does not prove:

- R1 remains valid for `commit-b`;
- G1 covers the revised homepage action;
- `agent-a` is still executing in the correct workspace;
- the current frontier is ready to publish.

The recovering executor combines replayed project facts with a fresh environment inspection.

## A Projection Gap Is a Control-Plane Failure

Do not choose whichever surface is most convenient when sources disagree:

- an event contains an open Todo, but status omits it;
- a Gate is resolved, but quota still reports operator wait;
- active state has a Next Action with no corresponding Todo;
- a dashboard reports runnable work while the workspace guard points to another worktree.

These are **projection gaps**. Handle them in order:

1. identify the authoritative source;
2. classify source-write failure, stale projection, migration drift, or stale external observation;
3. repair through the owning lifecycle or writeback path;
4. recompute the projection and verify its source revision;
5. do not run delivery that depends on the disputed state until it is consistent.

Manually editing several displays into agreement only hides the fault.

## Decide Where a New Field Belongs

Ask in this order:

1. Does it describe durable identity, configuration, or routing? Put it in the registry.
2. Does it describe a lifecycle transition? Put it in an event.
3. Does it describe one observation or delivery turn? Put it in a run snapshot or evidence bundle.
4. Does it serve only one reader view? Derive it as a projection from existing facts.
5. Does GitHub, CI, or another system own it? Keep that external authority and store bounded readback.
6. Is it a domain-specific result for Issue-Fix, Explore, or another pack? Keep it in Domain State rather
   than forcing it into generic Todo or quota state.

If one field tries to carry configuration, event, display, and permission semantics, the protocol
boundary probably needs to be split first.

## Protocol reading routes

This chapter owns the learning sequence, not the complete schemas. For state changes, start with:

- [`event_sourced_state_contract_v0`](https://github.com/huangruiteng/loopx/blob/main/docs../../../reference/protocols/event-sourced-state-contract-v0.md)
  for events, replay, ordering, and privacy;
- [`active_state_structured_projection_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/active-state-structured-projection-v0.md)
  for the typed read model over the Markdown workbench;
- [`task_graph_projection_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/task-graph-projection-v0.md)
  for the read-only relation graph;
- [`long_horizon_agent_state_protocol_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/long-horizon-agent-state-protocol-v0.md)
  for source and projection ownership, concurrent Agents, and lifecycle in long-running work;
- [`agent_scoped_evidence_ledger_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/agent-scoped-evidence-ledger-v0.md)
  for the Agent-scoped chronological read model used before replan and handoff;
- the [Status Data Contract](https://github.com/huangruiteng/loopx/blob/main/docs/status-data-contract.md)
  for Agent and operator-facing aggregation.

If you plan to change registry, event, Domain State, replay, or projection builders, continue to
[Control-Plane Course Lesson 4](/loopx/docs/development/control-plane-course/04-state-substrate/). It enters
source paths and experiments through fact ownership in Issue-Fix, Auto ML, and Auto Research. This chapter
remains the external developer's conceptual entrypoint.

The next chapter builds a work graph on this state substrate: who may do what, which condition blocks it, and
how work legally continues, hands off, or ends.
