# State Interaction Model

LoopX should not grow by adding commands one at a time. New capabilities
must fit a clear state model between the goal, the Codex App executor, the
human operator, and the dashboard.

This document is the design gate for future controller, dashboard, reward, and
multi-project work. If a proposed feature cannot name the state it reads, the
state it writes, the owner of that write, and how the dashboard proves it, the
feature is not ready.

For concrete recurring situations, maintain
[Interaction Pattern Catalog](concepts/interaction-pattern-catalog.md). The state model
defines actor boundaries and stores; the pattern catalog records good cases,
bad cases, expected user/agent channels, and validation references.

The compact product graph for this relationship lives in
[`docs/product/core-control-plane/`](product/core-control-plane/). It keeps the
interaction catalog lens, state definitions, and state machine together so new
patterns can be refined without creating a second control-plane vocabulary.

## Actors

### Goal

A goal is the durable work object. It owns the objective, current state,
authority sources, safety guards, validation surfaces, run history, and next
handoff condition.

A goal is not a chat thread. A thread can execute a goal, but the goal must
survive thread reloads, network interruptions, and multiple project agents.

For high-level product language, this is the **lifetime goal** object: a
durable intention that may outlive any specific todo, plan, run, or executor.
The lifetime goal owns continuity, not unlimited autonomy. It should keep the
current authority, boundary, evidence trail, and human corrections visible so
future agents can reinterpret the next bounded step instead of relying on
private model memory.

Goal-owned state:

- project-local registry entry,
- active goal state file,
- compact run index,
- private run payloads,
- optional human reward overlays attached to exact runs,
- optional compute quota and spend ledger for this goal.

### Codex App Executor

The Codex App executor is an actor that can read goal state, run commands, edit
files, spawn or coordinate child work, and write new state through LoopX
commands.

The executor is ephemeral. It should not be the source of truth. Its job is to
convert current context into bounded transitions:

- connect a project,
- inspect or map read-only state,
- perform one verified work segment,
- append a refresh run after state-only work,
- append a compact run after adapter work,
- update active state with progress, critic, and next action.

Executor-owned state should be minimal: current conversation context, local
tool outputs, and temporary execution decisions. Durable state belongs in the
goal stores above.

### User

The user is the operator and reward source. The user supplies high-quality
judgment that the executor cannot infer safely:

- whether a route, result, or tradeoff was good,
- whether a correction should become a durable operating lesson for future
  agents,
- whether a controller may move from observation to advice,
- whether write or production actions are allowed,
- whether a project should stay active, pause, or archive,
- whether a goal should receive more compute quota, less compute quota, or a
  temporary burst.

The user's feedback should be recorded close to the run being judged. A
structured `human_reward` overlay is better than burying the judgment in chat,
because later controller ticks and dashboards can see exactly which decision
was rewarded.

User intent can authorize a transition, but it should still be persisted as a
goal event, state update, or reward overlay before future agents rely on it.
When the user corrects a route, priority, benchmark protocol, or product
assumption, the correction should not live only in chat or model memory. Treat
it as a candidate operating lesson: write it into active state or a compact
run-bound/user-reward event, add or update the concrete agent todo that will
make the lesson executable, and refresh state so `quota should-run` can project
the corrected rule. The model may still use the richer conversational context
to interpret the lesson, but LoopX must carry the durable hook that
future agents can see.

### Dashboard

The dashboard is a local control-plane view. It is not the source of truth.
It is the human-facing product surface, not a dressed-up CLI dump.

By default it reads the status export and optional loopback status server:

- global registry scope,
- attention queue,
- contract health,
- compact run history,
- controller readiness,
- human reward summaries,
- compute quota state,
- artifact availability.

The dashboard can help the user review, filter, and dry-run feedback. Direct
writes from the dashboard must remain opt-in and gated. Browser-side writes
need an explicit capability, preview handshake, exact run target, and loopback
server boundary.

The dashboard should translate agent-facing status fields into operator
questions: "do I need to judge this?", "is an agent ready to work?", "are we
waiting on evidence?", and "is a controller handoff safe yet?" Raw
classifications, paths, and adapter terms should be secondary drill-down
details.

The dashboard may eventually look like a channel workspace: one goal timeline,
agent/member presence, task claims, approvals, and artifacts in one place. That
frontstage view must remain a projection over durable LoopX events. A
channel message can help a person collaborate, but the event ledger decides
what is current, who owns a task, which lease is active, and whether a later
agent may resume work.

The first user-facing view should also make TODO ownership explicit. Before the
operator reads a full action card or run history, the dashboard should surface
the first open `user_todos` item and the highest-priority open `agent_todos`
item per goal. This protects both sides of the loop: the user can see which
human/owner action blocks progress, and the next agent can see the compact
work item without re-reading stale thread context. Detailed action packets,
review materials, run history, and raw adapter fields remain drill-down
surfaces.

In a multi-agent goal, every open user todo has an explicit response binding:
`bound_agent=<registered-agent>` routes reminders and post-response continuation
to one agent lane, while `goal_bound=true` makes the item intentionally visible
to every lane in the goal. This relation is independent of gating. A
`user_action` remains non-blocking; a `user_gate` separately uses
`blocks_agent` or `global_gate=true` to stop work. `claimed_by` remains executor
ownership for agent todos and must not encode a user-todo binding. Agent-scoped
quota projections retain other-lane user todos as diagnostics, but exclude them
from the current lane's `open_count` and user notification channel.

## Three-Actor Interaction Protocol

The current failure mode is not lack of prompt detail. It is ambiguity about
which actor owns the next transition. When that boundary is implicit, an agent
can wait for a thread that was never launched, ask the user for small public
gates, stop a healthy automation because the top lane is blocked, or spend a
turn on a monitor that had no material transition.

LoopX should therefore expose one machine-readable interaction contract
per selected goal:

```text
loopx --format json quota should-run --goal-id <goal-id>
```

The guard's `interaction_contract` is the first-class protocol. Older fields
such as `execution_obligation`, `heartbeat_recommendation`,
`work_lane_contract`, `external_evidence_observation`, `goal_boundary`, and
`protocol_action_packet` remain compatibility and drill-down fields.
Executors should treat `interaction_contract.agent_channel.primary_action` as
the single action entrypoint for the current turn. If it carries a
`resolution_trace`, that trace is only a compact explanation of which projected
signal the primary action matched and whether `Next Action` / latest-run drift
exists; it is not a second action source and does not authorize state sync by
itself.
When the final contract is a blocking user gate, it also carries a compact
`interaction_response_plan_v0`: `kind=surface_user_gate`,
`decision=ask_user`, ordered `action_sequence=[notify, wait]`, and
`silent_wait_allowed=false`. The same plan
is projected into TurnEnvelope so a real executor and a model-behavior
qualification actor consume one typed behavior source. The automation prompt
remains a thin dispatcher; it does not duplicate this gate rule as prose.
For a registered agent, scoped state and accounting commands in
`interaction_contract.cli_channel.next_cli_actions` preserve the normalized
effective `--available-capability` envelope from the same quota decision.
Capabilities describe observed execution support; they do not grant authority
or replace user, repository-policy, or production gates. Owner-held authority
labels such as credentials and production access are excluded from this CLI
projection.
The adjacent `task_scope=goal_all_read_claimed_run_global_read_v0`
contract defines task discovery without turning visibility into authority. A
peer reads the current goal's ordinary todo backlog, selects only its own
claimed or an eligible unclaimed candidate, and claims before execution.
Other-agent claims remain diagnostic. Cross-goal inventory is available only
through an explicit read-only global-manager command and cannot enter the
goal-local execution queue.
Legacy Markdown parsing is even lower authority: it is a deterministic lint for
unprojected prose in `Next Action`, not a source of gate truth. The hot path
should not call an LLM to decide whether the user is gated, because that adds
latency, cost, nondeterminism, prompt-injection surface, and private-text
handling risk to `quota should-run`. If an LLM is useful, keep it in a cold
proposal lane that suggests structured `User Todo`, `decision_scope`, or
`Agent Todo` edits for a later deterministic promotion step.

### Actor Boundaries

| Actor | Owns | Must not own |
| --- | --- | --- |
| User/operator | Boundary decisions, reward, private material, credentials, paid/cloud resources, destructive git, production actions, public submissions/claims, explicit product-direction changes. | Routine public reads, task-row access, todo splitting, local state writeback, public-safe validation, or choosing among already-authorized P1/P2 work. |
| Agent/Codex executor | One bounded transition per turn: inspect current state, choose the highest safe lane, implement or observe, validate, write back, and spend only after delivery. | Durable truth, implicit approval, unrecorded reward, hidden long-term memory, silent cancellation, or credential copying. |
| LoopX CLI | Projection of goal truth, waiting owner, quota, interaction mode, machine obligations, spend policy, liveness, and compatible next commands. | Human judgment, private evidence interpretation beyond compact projections, or project-specific branching inside automation prompts. |
| Skill | Procedural operator/agent manual for using the CLI safely. | Runtime routing authority or a second state machine that overrides `quota should-run`. |
| Automation prompt | Thin bootstrap: wake, preflight, run the CLI guard, use the skill if available, follow `interaction_contract`, and stop for global safety boundaries. | Long project-specific control flow, stale TODO memory, or handwritten exceptions. |

Agent-visible follow-up work belongs in `Agent Todo`, not in prompt branches.
When the agent knows whether a todo is executable work or watch-only work, it
should register that fact through `loopx todo add --task-class ...`
and optional `--action-kind ...`. The active-state metadata then feeds status,
quota, dashboard, and review-packet consumers through the same CLI projection.
Legacy todo text classification exists only to keep older states readable.

For larger features, prefer todo succession over lifecycle inflation. Goal
Harness should not need many feature states to know whether work remains. The
agent should complete the current implementation slice, then immediately create
the next concrete todo for rollout, product-path audit, docs, telemetry,
benchmark proof, or operator decision. If no follow-up is needed, the completion
note should say why. This keeps LoopX responsible for durable checklist
truth while leaving the semantic judgment about "what should happen next" to
the model/executor.

### Operational Control Loop

The reusable product loop is user / agent / state, not agent / chat alone.
LoopX owns the shared control state; the operator supplies decisions,
reward, and priority; the agent worker turns observation packets into bounded
work; external systems supply evidence; and the guard decides whether the next
transition is delivery, decision, evidence waiting, or boundary repair.

The product taste behind this loop is simple: do not let the agent idle when
safe work exists, do not let it spin when no verified transition is available,
and do not make the human rediscover the important gate from chat history.
Human-in-the-loop means the human controls boundaries, reward, and route
decisions; it does not mean every bounded agent step waits for manual approval.

The runtime lifecycle is intentionally small. LoopX first resolves the registry
and active state, then every heartbeat or manual tick runs the quota guard
before any agent delivery. The guard chooses between human decision, external
evidence waiting, bounded work, quiet no-op, or repair. Only validated
writeback can change durable state or spend quota.

```mermaid
stateDiagram-v2
    [*] --> Registered
    Registered --> Ready: registry + active_state loaded
    Ready --> QuotaCheck: heartbeat / manual tick
    QuotaCheck --> UserGate: requires human decision
    QuotaCheck --> AwaitEvidence: external handle not terminal
    QuotaCheck --> Running: eligible + runnable todo
    QuotaCheck --> QuietNoop: no runnable scoped candidate after audit
    QuotaCheck --> Repair: stale projection / boundary drift
    Running --> Writeback: artifact + validation
    Running --> Repair: contract drift / failed invariant
    AwaitEvidence --> Ready: terminal evidence or blocker written
    UserGate --> Ready: owner decision recorded
    Writeback --> Ready: refresh-state + spend
    Writeback --> Done: objective terminal
    Repair --> Ready: projection repaired or blocker written
    QuietNoop --> Ready: no spend
    Done --> [*]
```

```mermaid
flowchart TB
  U["User / operator"] -->|"gate / reward / priority"| GH["LoopX state"]
  GH -->|"operator view / concrete todo"| U
  GH --> C{"can continue?"}
  C -->|"needs decision"| U
  GH -->|"observation packet / interaction_contract"| A["Agent worker"]
  C -->|"bounded delivery"| A
  A -->|"artifact / validation / blocker"| GH
  C -->|"await evidence"| E["External evidence / CI / benchmark"]
  E --> GH
  C -->|"scope mismatch"| B["Boundary repair"]
  B --> GH
```

This diagram is the compact contract behind the dashboard and heartbeat
surfaces:

- the runtime state machine explains which lifecycle transition is allowed;
- if `can continue?` resolves to `bounded_delivery`, the agent must produce a
  validated artifact, blocker, or state writeback before spending;
- if it resolves to `needs decision`, the user-facing surface must show a
  concrete question or todo, not a vague owner wait;
- if it resolves to `await evidence`, the agent may perform bounded read-only
  polling but must not invent delivery work;
- if it resolves to `scope mismatch`, old approval or reward is only an audit
  anchor until a fresh boundary projection grants the required write scope.

### Interaction Modes

`interaction_contract.mode` should make these patterns explicit:

- `bounded_delivery`: Codex owns one validated work segment. It should run the
  steering audit, choose a P0/P1/P2 lane, implement, validate, write back, and
  spend exactly once after delivery.
- `user_gate`: the user/operator owns the next decision. The agent asks a
  concise question and does not run the gated path. If the CLI exposes safe
  bypass, a later turn may do unrelated bounded P1/P2 work after the gate has
  been surfaced.
- `scoped_user_gate_fallback`: a concrete user gate owns one action scope, but
  a non-dependent fallback is executable. The user channel remains
  `NOTIFY/action_required`; the agent channel remains `must_attempt` for the
  selected fallback; the gated action itself must not run.
- `user_todo_blocker_push`: the user owns an open todo. The agent notifies,
  does not spend, and should not describe the turn as "no user action".
  In a multi-agent goal this applies only to the lane selected by
  `bound_agent`, or to every lane when `goal_bound=true`.
- `successor_replan_required`: a deferred todo's resume gate is satisfied, but
  the item is still deferred. The agent does not run ordinary delivery yet; it
  reopens the todo, supersedes it with a current successor, or records a
  public-safe no-follow-up rationale, then reruns the guard. This is a
  gate-resume mode, not an agent-scoped no-candidate wait.
- `external_evidence_observation`: Codex does not run benchmark/model/Docker
  delivery. It must first verify an observable handle such as a thread id, job
  id, marker, or compact writeback channel. This applies both to explicit
  `waiting_on=external_evidence` goals and to already-launched long-running
  work whose current action is compact-result polling. If no handle exists,
  write a compact blocker instead of quiet waiting.
- `monitor_quiet_skip`: no material transition is present. The heartbeat's
  turn-scoped `quota should-run` guard idempotently commits one receipt and its
  no-spend stall observation, then returns the follow-up decision. A retry
  reuses the same turn id and repairs a partial write; a later heartbeat uses a
  new id. The automation stays active.
- `autonomous_replan`: repeated no-progress evidence has crossed the self-repair
  threshold. Codex must run one bounded replan/repair segment or write a
  concrete blocker before another quiet no-op.
- `outcome_floor_recovery`: the current path is allowed only to recover the
  missing outcome-scale evidence or write the blocker; surface-only work is not
  allowed.
- `mapped_noop_if_unchanged` and `quota_throttled`: quiet no-op is allowed only
  after checking the contract's preconditions; it is not an automation cancel
  signal.

### Long-Running Todo Execution

Long-horizon execution should be a series of compact transitions, not an
unbounded "continue the last thing" loop:

1. Run `quota should-run`.
2. Follow `interaction_contract` first.
3. If the contract allows agent work, choose one lane from active `agent_todos`,
   the priority stack, and current blockers.
4. If the top P0 lane is blocked, record or surface the blocker, then continue
   with a verifiable P1/P2 lane only when the CLI contract permits safe
   bypass, recovery, self-repair, or another bounded obligation. Otherwise keep
   automation active without spend, or let the global scheduler pick another
   eligible goal.
5. Validate and write durable state before spending.
6. Spend exactly once after validated delivery, blocker writeback, or material
   transition.
7. Refresh state after spend when the dashboard/control plane needs the new
   compact truth.

This keeps the user's role high-value: the user resolves real boundaries and
reward judgments, while LoopX prevents the agent from stalling on
routine routing choices.

## Agentic RL Boundary Model

LoopX should be the external control plane around an agentic RL-style
worker, not the policy itself. Its job is to turn partial, long-running project
history into a current, auditable observation packet. The model's job is to
turn that packet plus the live workspace context into an internal belief state
and choose the next bounded action.

The runtime split is:

```text
observation_t = project(
  registry,
  active_goal_state,
  event_ledger,
  run_history,
  todos,
  gates,
  quota,
  authority_sources,
  decision_freshness
)

belief_t = model.update(
  observation_t,
  system_prompt,
  current_thread_context,
  current_tool_observations,
  model_memory,
  uncertainty
)

action_t = model.policy(belief_t)
checked_action_t = loopx.guard(action_t, observation_t)
event_or_reward_t+1 = append_after_validation(checked_action_t, outcome_t)
```

In this model, `event replay` is input material for `observation_t`, and
`human_reward` is a later evaluation signal. Neither one is the model's full
execution state. The execution state is the model's current belief, which is
allowed to be richer than the LoopX projection but must not silently
override LoopX boundaries, authority, freshness warnings, or user gates.

| Layer | Owns | Must not own |
| --- | --- | --- |
| LoopX control plane | Durable facts, event ledger, active-state projection, authority source registration, decision freshness, quota, gates, restartability, public/private boundary checks, run-bound reward overlays. | Semantic planning, hidden preference learning, task-specific policy, unrecorded approvals, or model-internal belief. |
| Agentic model / executor | Belief synthesis, uncertainty handling, action selection, semantic rebase of old decisions against current evidence, bounded implementation, validation choice, and asking the user when ambiguity is real. | Durable source of truth, implicit write authorization, permanent user preference storage outside goal events, or treating chat memory as stronger than current status. |
| Human/operator | Reward, approval, private-material access, production/destructive/external-resource decisions, and high-level tradeoff judgment. | Routine public reads, ordinary local validation, or reconstructing current state by hand when LoopX can project it. |

This keeps checkpointed decisions narrow. A checkpointed approval, reward, or
resume contract is an audit anchor with a validity check, not an instruction to
replay the old chat. Before a worker reuses it, LoopX should indicate
whether the decision point needs a rebase against current registry, active
state, quota, policy, repo/run status, and newer evidence. The model then
interprets whether the old decision still applies, asks the user if the answer
is ambiguous, and records the resulting transition as a new event.

For write authority, the checkpoint must become a boundary projection before
execution. `coordination.checkpointed_boundary_authority[]` is the compact
machine shape for this projection: fresh approved entries with public-safe
provenance, `recorded_at`, and `write_scope` compile into
`goal_boundary.write_scope`. Prose in a handoff, chat memory, or an old
approval run can motivate the agent to repair the projection, but it does not
grant write authority by itself. If a selected todo declares
`required_write_scopes` and the compiled boundary does not cover them,
`quota should-run` must route to `boundary_projection_repair` or a concrete
user/controller gate instead of letting the agent perform the protected write.

```mermaid
flowchart TB
  subgraph Harness["LoopX control plane"]
    Registry["Registry and authority sources"]
    ActiveState["Active goal state"]
    Ledger["Append-only event ledger"]
    Runs["Run history and overlays"]
    Gates["Gates, quota, freshness"]
    Observation["Compact observation packet"]
  end

  subgraph Model["Agentic model / executor"]
    Belief["belief_t: awareness of current task"]
    Policy["policy(belief_t)"]
    Action["bounded action proposal"]
  end

  subgraph Operator["Human/operator"]
    Approval["approval or deferral"]
    Reward["run-bound human_reward overlay"]
  end

  Registry --> Observation
  ActiveState --> Observation
  Ledger --> Observation
  Runs --> Observation
  Gates --> Observation
  Observation --> Belief
  Belief --> Policy
  Policy --> Action
  Action --> Gates
  Gates -->|"allowed or needs rebase/user gate"| Action
  Action -->|"validated work, blocker, evidence"| Ledger
  Approval --> Ledger
  Reward --> Runs
  Runs --> Observation
```

### Agent Loop Adapter Depth

Deep agent-loop integration is an upper bound, not a prerequisite. Goal
Harness must still add value when the worker is a black-box CLI, hosted agent,
benchmark runner, or third-party loop that cannot be modified. The control
plane should therefore support three adapter depths:

| Mode | When available | LoopX responsibilities |
| --- | --- | --- |
| `in_loop` | The worker can call LoopX APIs or tools during its own loop. | Inject observation packets, expose freshness/gate/quota checks before actions, require writeback after validated transitions, and let the worker use current status as first-class context. |
| `wrapper` | LoopX can launch or wrap the worker command, prompt, workspace, or environment, but cannot alter the internal loop. | Run pre-flight status/freshness checks, prepend or mount a compact state packet, guard high-risk external actions where hooks exist, collect stdout/artifacts/diffs, and reduce the result into durable events. |
| `passive_posthoc` | LoopX cannot launch or intercept the worker; it can only inspect observable outputs after the fact. | Read repo diffs, logs, run artifacts, benchmark outputs, or user notes; classify work/evidence/blocker/decision targets; append compact events; and produce restart packets for the next run. |

The invariant across all three depths is the same: LoopX produces and
validates control-plane context around the agent loop, whether or not the loop
natively cooperates. If the loop cannot be trusted to stop on a boundary, the
boundary must move outward to the wrapper, submit path, PR gate, benchmark
upload, cloud job launcher, production command, or operator approval surface.

```mermaid
flowchart LR
  Status["status / quota / freshness"] --> Preflight["pre-flight packet"]
  Preflight --> Worker["black-box or cooperative agent loop"]
  Worker --> Artifacts["diffs, logs, stdout, artifacts, tests"]
  Artifacts --> Reducer["post-run reducer"]
  Reducer --> Events["durable events and overlays"]
  Events --> Restart["restart packet"]
  Restart --> Preflight
  Status --> Guard["external gate for risky actions"]
  Guard -->|"approve, block, or ask user"| Worker
```

This makes passive and wrapper modes first-class product surfaces, not
fallbacks. The passive baseline should prove that even a non-cooperative worker
gets better restartability, stale-state avoidance, evidence discipline, and
reward attribution from LoopX before deeper agent-loop cooperation is
treated as required.

## State Stores

| Store | Owner | Reader | Writer | Purpose |
| --- | --- | --- | --- | --- |
| Project registry | Project goal | CLI, executor, status | `connect`, `bootstrap`, narrow project setup | Declares goal identity, repo, adapter, authority, guards. |
| Active goal state | Project goal | Executor, adapters, user review | Eligible peer or operator | Durable context, latest progress, next action, validation surfaces. |
| Shared global registry | Local control plane | Status, dashboard, any project shell | `connect`, `refresh-state`, `sync-global` | Multi-project discovery without manually copying registry entries. |
| Run payloads | Goal runtime | Executor, local reviewer | Adapters, `refresh-state`, `read-only-map` | Rich private evidence for one run. |
| Compact run index | Goal runtime | Status, dashboard, heartbeats | Adapters, reward overlay writer | Public-safe timeline and latest status. |
| Compute quota / spend ledger | Goal runtime or registry | Status, dashboard, automations | `quota` commands, controller writeback, operator decisions | Local duty-cycle or weighted-share policy for automatic agent turns. |
| Status export | CLI/status layer | Dashboard, pre-tick, heartbeats | `loopx status` | Agent-facing machine contract and dashboard input. |
| Dashboard UI state | Browser session | User | Browser URL/search state | Filters, selected goal, selected run; not durable goal truth. |

## Event Ledger Contract

LoopX should treat the compact run index plus reward / quota overlays as
the append-only event ledger for long-running work. Chat threads, browser
filters, and local tool outputs may help a worker decide what to do in the
moment, but they are not the durable source of truth.

The todo/history migration contract is captured in
[`event_sourced_state_contract_v0`](reference/protocols/event-sourced-state-contract-v0.md):
`ACTIVE_GOAL_STATE.md` stays the human/agent workbench, while canonical
todo/history state moves to append-only events with deterministic replay,
idempotent append, privacy partitions, and Markdown-compatible projections.

The control plane should preserve these event classes:

- **work events**: `refresh-state`, read-only maps, adapter ticks, and progress
  classifications that say what changed and how it was validated;
- **decision events**: operator gates, checkpointed resume contracts,
  approvals, deferrals, and `human_reward` overlays tied to exact runs;
- **accounting events**: quota spend rows such as `quota_slot_spent`;
- **evidence events**: eval, CI, artifact, blocker, failure, done, or
  read-only evidence-poll observations.

Current state is a projection over those events plus the active goal state and
registry policy. That projection may compact old detail for prompts and
dashboards, but it should not silently replace or rewrite the event that made a
decision auditable.
`loopx status` exposes this boundary through `event_ledger_summary`: a
compact count of sampled accounting, decision, evidence, state, and work events.
Dashboards and heartbeat prompts can use that projection to understand recent
control-plane shape while still drilling into `run_history` for exact events.

This gives LoopX a durable-execution boundary:

- Codex threads are replaceable workers. They execute bounded transitions, then
  write validated events.
- The LoopX control plane orchestrates task dispatch, quota, gates, and
  latest-state projections from the event ledger.
- Heartbeat prompts should stay thin. They should query status, quota, review
  packets, and active state rather than carrying project-specific history.
- Spend, validation, artifacts, blockers, handoffs, and read-only evidence polls
  should become durable events before later agents rely on them.
- Side-bypass and main-control workers should coordinate through the same
  ledger so they cannot double-spend, hide blockers, or race on stale state.

## Derived Task Graph Projection

Some complex goals need a graph-shaped view: independent deliverables, ordered
dependencies, acceptance gates, repair loops, and handoff points are easier to
reason about as nodes and edges than as a flat todo list. LoopX should
support that view as a derived projection over durable goal truth, not as a
second source of truth.

The durable owner remains the event ledger, active goal state, todos, gates,
leases, quota policy, and run history. A task graph may be rendered from those
stores when it helps an agent or operator answer:

- which deliverables can proceed independently;
- which gate blocks downstream work;
- which failure invalidates later pending work;
- which repair or verification node should run before close-out;
- which user decision or lease owns the next transition.

The projection should also preserve a useful distinction between durable
control state and transient work state:

- **Control state** belongs to LoopX: objective, constraints, task
  dependencies, gates, leases, run summaries, accepted evidence, and current
  dispatch state.
- **Work state** belongs to an executor turn or child worker: code snippets,
  raw tool output, temporary hypotheses, local implementation details, and
  verbose logs.

This lets LoopX borrow graph-native recovery where it matters without
forcing every goal into a multi-agent DAG. Small or linear goals can stay as
ordinary todos. Multi-stage goals can project a graph for dispatch, review, and
repair, audit, and continuation, while the append-only ledger still decides
what happened and which worker may resume.

The first implementation should be read-mostly: expose an optional compact
`task_graph_projection_v0` from status or review packets, backed by existing
todo ids, gate ids, run ids, and lease ids. Writes should continue through the
existing lifecycle commands until a server-backed lease/graph API exists. The
initial protocol and public fixture live in
[`docs/reference/protocols/task-graph-projection-v0.md`](reference/protocols/task-graph-projection-v0.md).

Old user decisions need freshness checks. A reward, steering note, or approval
from seven days ago can remain valuable, but a worker should apply it only after
replaying or rechecking the newer event window that could make it stale. The
current checkpointed gate contract is the first version of that rule: use the old
decision as an audit anchor, then rebase at the decision point against current
registry, active state, quota, policy, repo/run status, and recent evidence.

## Priority Stack And Next Action Selection

`Next Action` should be derived from a goal priority stack, not from the last
thing the previous executor happened to touch.

For the v0.1 control-plane milestone, use this default priority stack:

| Priority | Meaning | Typical surfaces |
| --- | --- | --- |
| P0 | Make the multi-project control loop reliable. | registry, global registry, active state, run history, authority coverage, public/private boundary, operator gate, human reward, project-agent packet, compute quota, real adapter proof |
| P1 | Make the product easier to understand and use. | todo-focus dashboard, dashboard interaction, operator copy, share documents, launch copy, exploration lane design |
| P2 | Extend the platform after the loop works. | deeper scheduling, richer dreaming, refactor proposals, more adapters, benchmark expansion |

Within P0, choose work in this order:

1. state truth and safety;
2. human decision loop;
3. project-agent execution loop;
4. multi-project allocation through compute quota;
5. real adapter proof.

This order prevents two common failures. First, a compute quota planner should
not spend time on a goal whose status is stale, unsafe, or built from the wrong
authority source. Second, dashboard polish should not replace the durable
reward or operator-gate state that later project agents need.

A controller tick should record why its selected next action won over nearby
P0/P1/P2 candidates. The reason can be compact, but it should name the priority
level and the stale-state or operator-cost failure it prevents.

### Steering Audit

`quota should-run` is a compute guard, not a strategy selector. It answers
"may this goal spend another automatic turn now?" It does not answer "is this
topic still the best use of attention?"
Its `heartbeat_recommendation` can cover generic lifecycle mechanics such as
the first saved read-only map or an unchanged mapped no-op, but it still does
not replace the priority-stack steering audit for real delivery work.

Before writing a new `Next Action`, an autonomous goal tick should run a small
steering audit:

1. list at least three plausible candidates from different lanes when they
   exist, such as state/safety, human decision, project-agent execution,
   compute allocation, real adapter proof, product/communication, or
   exploration;
2. choose by the priority stack above, not by the previous tick's adjacent
   critic alone;
3. apply a continuation check when the same topic has consumed several recent
   delivery slices. Large topics may continue, but the tick must re-rank them
   against other P0/P1/P2 candidates and state why continuing is still the
   highest-priority move;
4. separate compute quota from focus quota. Compute quota controls how many
   turns a goal may spend; focus quota controls whether one subtopic deserves
   the next turn at all;
5. include a product bottleneck lens: ask whether the core goal is currently
   bottlenecked by user experience, agent capability, evidence quality, adapter
   readiness, or priority-rule gaps, and promote one concrete bottleneck
   candidate when it should outrank the nearest local TODO;
6. record the losing high-value candidate when it matters, so the next tick can
   resume the broader milestone instead of rediscovering only the nearest
   local gap.

This prevents a chain of individually-correct, easy-to-verify slices from
crowding out a more important milestone such as real project adapter proof,
human reward quality, or dashboard attention reduction.

## State Flow

```mermaid
flowchart LR
  User["User operator"] -->|"intent, approval, reward"| Executor["Codex App executor"]
  Executor -->|"connect / update"| Registry["Project registry"]
  Executor -->|"progress / next action"| ActiveState["Active goal state"]
  Registry -->|"auto sync"| GlobalRegistry["Global registry"]
  ActiveState -->|"refresh-state / adapter read"| RunPayload["Run payload"]
  Executor -->|"adapter tick"| RunPayload
  RunPayload -->|"compact fields"| RunIndex["Run index"]
  User -->|"loopx reward"| RunIndex
  User -->|"compute share, pause, burst"| Quota["Compute quota"]
  GlobalRegistry --> Status["Status export"]
  RunIndex --> Status
  Quota --> Status
  Status --> Dashboard["Dashboard"]
  Dashboard -->|"review / dry-run only by default"| User
```

The CLI status export is for agents and local tools. The dashboard reads that
derived surface, then presents a user-facing interpretation. It should not
reach behind the status layer to reinterpret private files, and it should not
directly mutate goal state unless a future explicit write boundary is enabled.

## Core Transitions

### Connect

Purpose: make a project visible to the local control plane.

Writer: executor through `loopx connect` or `bootstrap`.

Writes:

- project registry,
- initial active state if missing,
- global registry sync.

Dashboard effect: a connected goal appears in global status. If there is no
run yet, status should surface `connected_without_run` so the next action is
clear.

### Read-Only Map

Purpose: turn a generic connection into a useful project map without granting
write authority.

Writer: executor through `loopx read-only-map`.

Writes:

- private map payload,
- compact `read_only_project_map` run.

Dashboard effect: the goal moves from "connected but not inspected" to "Codex
can use the map or build a project-specific adapter." This is a handoff state,
not proof that the project is fully automated.

### State Refresh

Purpose: make state-only work visible when no adapter ran.

Writer: executor through `loopx refresh-state`.

Writes:

- private refresh payload,
- compact `state_refreshed` run.

Dashboard effect: latest dashboard state catches up with active state changes.
This prevents a project from looking stale after the user or executor updated
the goal document, ledger, or next action.

For an accountable, Turn-bound refresh, a successful writeback and a satisfied
vision checkpoint are separate facts. `ok=true` does not imply that an omitted
vision decision was supplied. Inspect `vision_checkpoint.satisfied`.

If the checkpoint is `missing_required`, submit a checkpoint-only refresh with
the **same** Goal, Agent, Todo/obligation, Turn, and delivery fields. Preserve
the original working directory and explicit target (`--registry`, `--runtime-root`,
`--project`, `--state-file`), scope (`--progress-scope`, `--agent-lane`), and
isolation (`--no-global-sync`, `--suppress-external-sinks`) options, with their
original values and presence. Both first-writeback and replay Markdown list
the preserve/remove/add rules; do not add isolation flags absent from the
original command or silently change the delivery target. These instructions
preserve target, scope, and global-sync choices. Turn-bound external delivery
also has a tool-enforced pause/resume handshake:

- `--suppress-external-sinks` records a pause for the exact settlement operation.
  A later send-capable refresh that omits this flag returns
  `external_delivery_resume_required` before new business writes or sends.
- To keep recovering locally, retain `--suppress-external-sinks`. To deliberately
  resume external delivery, retry the same recovery command with
  `--resume-external-sinks <resume_key>` using the key returned in
  `external_delivery.resume_key`. The two flags are mutually exclusive. Preserve
  the original identity and omit already executed mutation and spend arguments.
- A matching acknowledgement lifts only that operation's current pause. A new
  pause invalidates the old key. Repeating an accepted acknowledgement is safe;
  it does not append another business run. Existing provider permissions,
  configuration, and delivery de-duplication still apply. This is explicit intent,
  not a new authorization grant or an exactly-once transport guarantee.
- Receipt-only repair remains local and does not require or consume a resume
  acknowledgement; the next send-capable replay still checks the pause. Direct
  Python callers that request no delivery remain local without creating a pause.
- Historical operations without pause evidence keep the previous per-call
  behavior. Once a new explicit suppression is recorded, later recovery must
  acknowledge it. Non-Turn refresh keeps the existing single-call suppression.
  No permanent permission ceiling or new recovery mode is introduced.

Pause/resume observations are scoped by settlement identity in the existing
local rollout journal and processed by the TypeScript quota owner. A pause is
persisted before a new business writeback; if later validation fails, the pause
remains and can be explicitly resumed. Resume is persisted only after an
accepted recovery, before CLI delivery. Journal failures prevent delivery;
dry-run never persists either transition. Corrupt or unsupported relevant pause
history cannot enable sends, but explicit suppression remains available for
local recovery. Existing original-writeback, one-spend, and sink retry contracts
remain unchanged. Global-sync flags, target migration, general hooks, and
cross-service atomicity are outside this handshake. Older binaries do not enforce
the handshake: rolling back loses this protection and must not erase its journal.

Remove previously executed state-mutation options, even when their values are unchanged: `--next-action`,
`--autonomous-replan-recorded`, `--repair-delta-kind`, `--usage-json`, and
`--usage-codex-session`. Remove any dependent options that would become invalid
without them. Repeating mutations is rejected as
`checkpoint_supplement_must_not_repeat_mutations`; do not simply append vision
arguments to an original command that contains these options.

Add only one vision decision:

- `--vision-unchanged-reason 'Existing scope and acceptance still apply.'` when
  a persisted vision genuinely remains applicable;
- a valid `--agent-vision-json` packet, or the inline `--vision-*` patch fields,
  when the vision needs an update. File inputs must be visible to the CLI
  process; a remote/sandbox bridge owns transporting their contents.

`refresh_recovery.decision=supplement_checkpoint` appends a validated checkpoint
under the original settlement identity without rewriting the original artifact
or reattributing its delivery workspace. An identical retry returns `replay`
with the saved checkpoint; it does not append again. A changed satisfied
decision, changed delivery payload, or a missing checkpoint superseded by a
later same-Agent vision is rejected with no write. Dry-run previews do not
repair receipts or append history. Current receipt-bound monitor Turns close on
the exact committed poll, including material polls that release an independent
successor; they do not require an accountable refresh, workspace supplement or
quota spend. The retained material-monitor refresh recovery branch is
compatibility-only for older phase producers and preserves the original poll
outcome while rejecting unrelated mutations. Other missing-workspace repairs
precede checkpoint supplementation.

Do not repeat implementation, manufacture a successor, or open another Turn
just to repair this checkpoint. Keep the ordinary one-spend settlement order.
This recovery does not certify acceptance, close other Todos, or bypass replan,
identity, or terminal gates. A substantive new vision can still create a real
planning obligation.

### Compute Quota

Purpose: decide how much automatic agent compute a goal may consume.

Writer: user-authorized `quota` command, controller state writeback, or a
derived status planner.

Writes:

- per-goal compute quota such as `1.0`, `0.5`, `0.3`, or `0`,
- optional spend ledger entries for automatic ticks or agent turns,
- compact allocation state such as `eligible`, `throttled`, `waiting`,
  `operator_gate`, `paused`, or `blocked_health`.

Dashboard effect: the operator can see why a project is active, throttled,
waiting, paused, or asking for a burst. Automations should treat timer cadence
as an execution detail and read LoopX compute quota before running work.

See [quota-allocation.md](quota-allocation.md).

### Adapter Tick

Purpose: inspect project-specific evidence and emit a compact decision surface.

Writer: project adapter or executor-controlled pre-tick.

Writes:

- private project evidence payload,
- compact run index row with classification and one recommended action.

Dashboard effect: the goal enters the appropriate lane: user/controller,
Codex-ready, external-watch, or blocked health.

### Human Reward

Purpose: capture high-quality operator judgment near the decision being judged.

Writer: user-authorized `loopx reward`.

Writes:

- compact overlay row in the run index.
- coordination hints for active-state summary and project-agent history lookup.
- optional active-state `Progress Ledger` summary when the operator explicitly
  requests `--write-active-state-summary`.

Dashboard effect: selected runs show whether human judgment exists and what
class of decision it judged. This is the main improvement over bare goal-mode
chat, where feedback is easy to lose.

Human reward also covers explicit operating corrections, not only numeric
score-like approval. A correction such as "Codex stays local; the remote host is
only the execution substrate" should be promoted into a durable operating
lesson before the next benchmark or adapter turn relies on it. The minimal
writeback is:

- a compact summary of the corrected rule;
- the scope it applies to, such as a benchmark family, route, project, or goal;
- the old assumption it supersedes;
- the next agent todo that makes the rule executable;
- validation or freshness checks that will prove future projection.

If the correction changes a safety boundary, it must still go through the
checkpointed boundary projection path before authorizing protected writes or
resource actions.

## Dashboard Architecture

The dashboard should optimize for operator decisions, not decorative reporting.
It should not expose the CLI status contract as the primary mental model.

First screen:

- compute quota summary: which goals are eligible, throttled, waiting, paused,
  or over budget;
- user actions that need the operator before auxiliary source controls or raw
  status drill-down,
- selected action share controls next to those actions, so review links,
  user judgment, project-agent instructions, and dry-run preview are visible in
  one canonical packet without hunting through the page,
- contract health and global registry health,
- lanes by `waiting_on`: user/controller, Codex-ready, external evidence,
  blocking health,
- a user review map that translates lifecycle phases into "needs first run",
  "state changed", "agent inspected", "reward recorded", and "controller
  readiness or controller-gated" states;
- compact goal rows with user-facing phase, latest classification as a
  secondary detail, last run time, recommended action, reward presence, and
  controller readiness.

Goal detail:

- goal identity and authority sources,
- operator decision: review or authorize, let Codex continue, wait for
  evidence, or fix health first,
- active state freshness,
- run timeline,
- controller readiness gates,
- human reward timeline,
- artifact availability,
- project map or adapter-specific compact panels.

User review surface:

- show first-screen operator actions before raw goal detail: reward gates,
  controller opt-ins, compute quota changes, evidence watches, Codex handoffs,
  and blocking health items,
- include the safe local CLI path or reward-draft hint on first-screen action
  cards when it helps the user move from judgment to an agent-facing command,
- allow local action-kind focus such as reward, controller, Codex, evidence,
  or health while treating that filter as dashboard UI state rather than
  durable goal truth,
- keep that action-kind focus URL-backed when useful, so a human can reload or
  share the current review lane without mutating goal, run, or status state,
- keep selected goal detail URL-backed when useful, while treating it as
  browser review state rather than a durable goal transition,
- expose a compact review link affordance for the current action-kind focus,
  selected goal, status source, and queue filters; copying that link is still
  dashboard UI state, not reward, approval, or controller opt-in,
- expose one copyable Review Packet for the selected action rather than several
  competing copy formats. The packet should combine the review link, Chinese
  agree/disagree/reason/next-step prompt, project-agent instructions,
  reward/default hint, and local dry-run preview. For reward actions, the
  project-agent section should provide the history lookup for a recorded
  run-bound reward rather than asking the project agent to write reward. It is
  for user-to-agent collaboration and must not be parsed as durable reward,
  approval, controller opt-in, or write-control,
- show the run being judged,
- show why the system thinks a human decision is needed,
- show the selected goal's current operator stance before raw run history,
- show a safe CLI path for the stance: status/history inspection,
  read-only-map or refresh-state dry-run, or reward dry-run through the Reward
  CLI Draft,
- generate a CLI reward draft or dry-run request whose defaults derive from
  the selected operator stance and missing gates,
- never imply that reward equals write authorization.
- keep schemas, routes, and component structure stable in English, but allow
  operator-facing review summaries and handoff judgments to be localized for
  the human reviewer.

Executor surface:

- show the next allowed transition,
- show whether the goal is eligible under compute quota,
- show missing gates,
- show whether the next action is read-only, state refresh, adapter tick,
  reward capture, controller opt-in, or explicit write approval.

CLI surface:

- keep fields terse, stable, and machine-readable;
- prefer classifications, lifecycle phases, gate ids, and one recommended
  action over user-facing prose;
- avoid local private evidence and UI-only copy.

## Invariants

- The active goal state is the durable context; chat is only execution context.
- The compact run index is the dashboard timeline; private payloads are not the
  dashboard contract.
- Every meaningful state-only update needs a refresh run if the dashboard is
  expected to reflect it.
- A read-only map does not authorize mutation, decision advice, or production
  control.
- Human reward does not authorize writes unless the reward explicitly records a
  separate approval and the target transition supports it.
- Durable reward belongs in the run-bound `human_reward` overlay. Active goal
  state can summarize that a reward was recorded, but it should not become the
  only reward source that other project agents rely on.
- Explicit user corrections that change operating policy should be promoted to
  a durable operating lesson, successor todo, or compact reward/gate event
  before future agents rely on them. Chat memory alone is not a replayable
  control-plane signal.
- The global registry is synced from project-local registries; agents should
  not manually paste project entries into a separate queue.
- Automation cadence is not the compute quota source of truth. It may wake an
  executor, but LoopX should decide whether the goal is eligible,
  throttled, paused, or waiting.
- UI filters and selected rows are browser state, not goal state.
- Unknown status fields are additive; changing the meaning of existing compact
  fields requires a contract update.
- Public examples and docs must stay sanitized even when local status exports
  contain private machine paths.

## Feature Gate Checklist

Before adding a new command, dashboard widget, adapter field, or controller
stage, answer:

- Which actor owns the state being changed?
- Which store is the source of truth after the transition?
- Is the transition read-only, advisory, reward capture, or write control?
- What compact field will status export?
- What should the dashboard show on the first screen?
- Does this transition spend or change compute quota?
- What private evidence must stay out of compact history?
- What validation proves the state changed correctly?
- What stale-state failure does this prevent?

If these answers are unclear, improve the design before adding the capability.

## Near-Term Product Implication

The next milestone should not be another isolated adapter command. It should
make the dashboard and status contract reflect this model:

- show whether a goal is merely connected, mapped, refreshed, adapter-inspected,
  reward-judged, controller-gated, or controller-ready;
- make the user/controller lane distinct from Codex-ready work;
- make human reward capture a first-class review action;
- make compute quota visible so project priority is not hidden inside
  automation intervals;
- make stale dashboard state obvious and recoverable;
- make multi-project management possible without asking each project agent to
  manually maintain a global queue.
