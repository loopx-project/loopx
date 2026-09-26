# long_horizon_agent_state_protocol_v0

`long_horizon_agent_state_protocol_v0` is a shared lifecycle map for
long-running LoopX agent work. It separates durable source state from
operator-facing projections, then shows how startup, execution, ending,
evidence, gates, human feedback, handoffs, and rollback should connect.

This is not a new state store. The source of truth remains the registry, active
goal state, todos, run history, rollout events, operator gates, and run-bound
human reward overlays. This protocol gives those existing surfaces one
implementation-oriented frame.

## Product Outcomes

The protocol exists so LoopX can support these outcomes:

1. A new project can connect quickly without overwriting an existing control
   plane.
2. The user sees candidate work and gates before a long loop starts.
3. Multiple agents can work in lanes without losing ownership, evidence, or
   review responsibility.
4. The operator can inspect progress, gates, risk, and next actions from
   projections instead of reading every chat thread.
5. Failed or reverted work becomes a compensating state transition, not erased
   history.

## Source Protocol

Source protocol fields are writable only through LoopX lifecycle commands or
project-owned state files. Dashboards and showcase fixtures must not mutate
them directly.

| Source state | Existing anchor | Purpose |
| --- | --- | --- |
| `goal_identity` | registry, active state, agent profile docs | Stable `goal_id`, repo, registered peers, advisory profiles, and write boundary. |
| `connection_state` | `loopx connect`, `bootstrap`, `doctor`, `sync-global` | Whether the repo is connected, read-only, bootstrapped, stale, or missing local state. |
| `local_state_boundary` | `.gitignore`, `loopx check`, getting-started docs | Keep `.loopx/`, `.loopx/goals/`, `.local/`, raw logs, credentials, and private paths out of public commits. |
| `todo_item_v0` | `loopx todo`, active-state todo sections, `loopx/status.py` | Formal work unit with role, status, task class, action kind, claim, dependency, resume, and evidence metadata. |
| `interaction_contract_v0` | `loopx quota should-run`, `docs/quota-allocation.md` | Splits user, agent, and CLI obligations before an automated turn spends compute. |
| `agent_lane_next_action_v0` | `loopx quota should-run --agent-id ...`, `docs/project-agent-todo-contract.md` | Per-agent selected runnable todo without replacing the goal-level next action. |
| `agent_workspace_guard_v1` | `loopx quota should-run`, selected todo and repository policy | Blocks repository delivery until the current peer uses a compliant worktree/branch. |
| `run_history` | `loopx/history.py`, `refresh-state`, `quota spend-slot` | Compact run classifications, delivery outcome, recommended action, evidence, and spend records. |
| `loopx_rollout_event_v0` | `loopx/rollout_event_log.py` | Append-only public-safe event stream for todo, validation, PR, handoff, quota, repair, and failure events. |
| `operator_gate` | `loopx operator-gate`, `loopx/review_packet.py`, `loopx/status.py` | User/controller decision point with decision, reason, follow-up, and optional handoff command. |
| `human_reward` | `loopx reward`, `loopx/history.py`, `loopx/status.py` | Run-bound human judgment overlay; not generic write-control. |
| `delivery_outcome` | `loopx/delivery_outcome.py`, `loopx/history.py`, `loopx/status.py` | Machine-readable result tier: surface-only, outcome gap, outcome progress, or primary goal outcome. |
| `rollback_packet_v0` | `docs/reference/protocols/rollback-packet-v0.md` | Compensating action record linking todo, commit, event, decision, external resource, and validation plan. |

### Candidate discovery and command retirement

`todo suggest` and its `todo_suggestion_prompt_v0` packet are retired. The
command only emitted instructions for the current agent; it did not inspect
the repository or persist candidates. There is no replacement discovery
command, nested packet, or compatibility alias.

Read existing work with `loopx todo list --goal-id <goal-id> --role agent
--status open --limit 50`. This is a bounded view: an open Todo is not proof of
current execution eligibility, and a limited view is not the complete frontier.
Keep the existing quota/Turn revalidation before selecting work.

Ask the current project agent to analyze uncovered work using the relevant
repository evidence and existing Todos. Proposals remain advisory until the
user or authorized controller selects them for `loopx todo add`; analysis
does not grant execution authority. `todo plan` remains the existing Goal's
caller-invoked planning checkpoint, not a drop-in suggestion replacement.

Scripts that parsed the retired prompt must remove that step and use their
existing agent interaction. The former Todo `--from` and `--trigger` options
are removed; `--limit` retains its existing `todo list` meaning. No persisted
Todo, journal or provider state migration is required.

## Projection Protocol

Projection protocol fields are read-only views. They may summarize, rank, and
compress state, but they do not own truth or grant permission.

| Projection | Existing anchor | Display use |
| --- | --- | --- |
| `status_contract_v2` | `loopx status`, `docs/status-data-contract.md` | CLI/dashboard status envelope. |
| `goal_channel_projection_v0` | `loopx/control_plane/goals/goal_channel_projection.py`, `loopx/status.py` | First-screen goal card: user todos, agent todos, open gates, active claims, latest event, next action. |
| `todo_index_v0` | `loopx/status.py` | Cross-goal todo index from attention queue and rollout events. |
| `task_graph_projection_v0` | `docs/reference/protocols/task-graph-projection-v0.md` | Optional graph of blocks, validates, repairs, hands off, and supersedes. |
| `review_packet` | `loopx review-packet`, `loopx/review_packet.py` | Operator-facing gate/review/handoff packet. |
| `global_manager_command_v0` | `docs/reference/protocols/global-manager-command-v0.md` | Read-first global command response for progress, gates, todos, risks, and next actions. |
| `frontstage dashboard` | `apps/presentation/dashboard/src/views/frontstage-page.tsx` | Dense operator UI for lanes, gates, todos, recent evidence, and risks. |

Projection truth contract:

```json
{
  "schema_version": "long_horizon_agent_state_protocol_v0",
  "projection_is_writable": false,
  "source_of_truth": [
    "registry",
    "active_state",
    "todo_item_v0",
    "run_history",
    "rollout_event_log",
    "operator_gate",
    "human_reward"
  ],
  "write_apis": [
    "loopx todo",
    "loopx refresh-state",
    "loopx operator-gate",
    "loopx reward",
    "loopx quota spend-slot"
  ]
}
```

## State Partitioning For Concurrent Agents

Concurrent peers share one source-of-truth event stream per goal. LoopX should
not fork truth just because a peer, benchmark case, or UI
view wants a narrower timeline.

Canonical source:

- one registry entry and active goal state for the goal;
- one append-only run history for compact lifecycle records;
- one append-only `loopx_rollout_event_v0` log for todo, validation, PR, handoff,
  quota, repair, and failure events;
- formal todos as durable work units, including `claimed_by`, `task_class`,
  `action_kind`, `resume_when`, and `unblocks_todo_id` when present.

Derived views:

- per-agent views filter by `agent_id`, `lane.lane_id`, `claimed_by`, and
  selected `agent_lane_next_action_v0`;
- per-todo views filter by `todo_id` and lifecycle transition events;
- per-case views filter by `case_id` and validation/result events;
- per-run views filter by `run_id`, `source_event_id`, and compact causality
  refs.

Derived views are read-only indexes. They may cache, rank, or summarize, but
they must carry source refs back to canonical events and must be recomputable
from the registry, active state, run history, rollout events, and reward
overlays. A derived view must not become a separate active-state file, alternate
run history, or dashboard-owned write path.

Use an agent-lane projection when the agent shares the same goal, repo boundary,
todo backlog, and operator policy, and only needs a scoped next action or
timeline. Use an independent state file only when the work has a distinct
`goal_id`, durable objective, protected boundary, owner policy, or backlog that
would be misleading if compressed into the parent goal's `Next Action`.

The goal-level `Next Action` remains a stable tie-breaker among equally ranked
work. When it carries a typed todo binding, adding an executable agent todo may
rebind it only if the new todo has strictly higher priority and belongs to the
same agent lane (or replaces an unclaimed generated route). Manual or untyped
operator text and routes already owned by another agent remain authoritative.
Startup checks that are merely recommended stay below concrete task work;
required connection, permission, or runtime setup must instead be represented
as an explicit high-priority prerequisite or blocker.

Every new rollout event that should participate in concurrent-agent views should
try to include at least one stable join key: `agent_id`, `todo_id`, `run_id`,
`lane.lane_id`, or `case_id`. Events that cannot expose a join
key may still be recorded, but projections should label any bridge from them as
inferred rather than observed.

## Lifecycle

### Startup

Startup answers whether the project is connected, who owns the work, and what
the first safe decision is.

Required source state:

- stable `goal_id`;
- registered peer identities and advisory scope when configured;
- adapter status and project-local state path;
- local-state ignore boundary;
- optional suggested todo queue;
- optional first formal runnable todo.

Projection requirements:

- show goal id, current user gate, top agent todo, and next safe action;
- distinguish candidate todo from formal todo;
- report local-state ignore problems as warnings;
- install heartbeat only after a connected goal exists.

### Execution

Execution answers whether this turn should run, what exactly should be done,
who owns it, and when compute can be spent.

Required source state:

- `interaction_contract_v0.user_channel`;
- `interaction_contract_v0.agent_channel`;
- `interaction_contract_v0.cli_channel`;
- selected `agent_lane_next_action_v0`;
- selected `todo_item_v0`;
- claim or lane ownership when multiple agents are registered;
- validation artifact refs after work;
- compact writeback before quota spend.

Projection requirements:

- user-required work must name concrete user todos or questions;
- no user todo plus `agent_channel.must_attempt=true` must not become a quiet
  no-op;
- safe-bypass work must keep the blocking gate visible;
- monitor todos stay visible but do not rewrite generic heartbeat prompts;
- `delivery_outcome` is displayed separately from activity count.

### Ending

Ending answers whether work completed, paused, failed, handed off, or must be
rolled back.

Required source state:

- `delivery_outcome`;
- completed, superseded, blocked, or deferred todo status;
- successor todo or explicit no-follow-up rationale;
- validation evidence or blocker evidence;
- PR/commit refs when code changed;
- handoff target and stop condition when another agent should continue;
- future `rollback_packet_v0` when state must be compensated.

Projection requirements:

- show the result tier before showing activity volume;
- keep failed external-resource setup as a partial-success state when a usable
  resource exists;
- show handoff/review ownership rather than treating every open todo as global;
- expose rollback candidates without executing rollback automatically.

## Evidence, Gate, Reward, Handoff

Evidence fields should be compact and public-safe:

- `artifact_refs`: docs, smoke files, fixture files, PRs, commits, dashboards;
- `validation_commands`: command labels or public-safe paths, not raw logs;
- `source_refs`: issue/PR ids, public social ids, redacted private connector
  ids, experiment run handles;
- `boundary`: booleans proving raw logs, credentials, private paths, and raw
  transcripts are absent.

Gate fields must include:

- gate owner class: `user`, `controller`, registered `agent`, or external system;
- blocked todo or scope;
- question or decision required;
- approved/deferred/rejected status;
- unblocked todo or next safe action when resolved.

Reward fields must remain run-bound:

- judged run id or generated timestamp;
- decision label and reward polarity;
- public-safe reason summary;
- follow-up;
- optional active-state summary.

Handoff fields must include:

- source agent and target agent;
- todo ids or scope transferred;
- stop condition;
- evidence refs the target needs;
- whether review or self-merge is allowed.

## Implementation Alignment

Already aligned:

- `loopx quota should-run` emits `interaction_contract_v0` and
  `agent_lane_next_action_v0`.
- `loopx todo` supports formal todos, claims, deferred state,
  `resume_when=todo_done:<todo_id>`, `unblocks_todo_id`, and suggestions.
- `loopx status` emits `goal_channel_projection_v0`, `todo_index_v0`, compact
  operator gates, compact human rewards, delivery outcomes, and rollout-event
  todo indexes.
- `loopx_rollout_event_v0` can carry lane, transition, causality, handoff, and
  code refs for new events.
- `examples/fixtures/long-horizon-self-iteration-rollout.public.json` gives
  frontend and protocol tests a compact public-safe fixture with gate, handoff,
  validation, deferred-resume, evidence, state partitioning, derived views, and
  inferred display-bridge coverage.
- `rollback_packet_v0` defines how rollback, fix-forward, external cleanup,
  support requests, and todo compensation are represented before any protected
  action runs.

Partially aligned:

- Older rollout events may lack before/after, causality, and handoff fields, so
  any animation or timeline must mark inferred bridges explicitly.
- `task_graph_projection_v0` is specified but not yet a stable dashboard input.
- Commit-to-todo linkage is convention-based through PR text, commit messages,
  todo evidence, and rollout refs.
- External-resource partial success is not yet a generic protocol.

Not yet aligned:

- `rollback_packet_v0` is specified and smoke-tested, but no command yet emits
  or executes packets.
- PR lifecycle resume conditions are limited to structured rollout-event
  evidence: `resume_when=pr_merged:#532` and
  `resume_when=pr_merged:owner/repo#532` can wake deferred todos after the
  matching `pr_merge` event appears in the local rollout log. Unqualified refs
  bind to the todo's GitHub `task_repository`; cross-repository dependencies
  must use a qualified ref. Missing repository identity fails closed with an
  explicit ambiguity diagnostic. LoopX does not infer this from prose.
- `global_manager_command_v0` is specified and smoke-tested, but no host
  integration or CLI command emits it yet.
- Historical human-gate impact must be inferred from public-safe evidence.
- Frontstage does not yet render a full multi-lane self-iteration timeline from
  real status plus rollout events.

## Acceptance Checks

A protocol implementation or fixture is acceptable when it proves:

- source and projection state are separated;
- projections are explicitly read-only;
- every formal execution step maps to a todo, gate, run, event, or reward;
- candidate todos are not silently promoted;
- at least one lane-aware next action can be selected for a registered agent;
- per-agent, per-todo, per-case, and per-run views are read-only derived
  indexes over one canonical event stream;
- an agent-lane projection is not confused with an independent state file;
- validation and writeback precede quota spend;
- human gates name what they block and unblock;
- handoffs name source agent, target agent, todo scope, and stop condition;
- rollback is represented as a future compensating action, not as hidden
  deletion;
- public fixtures contain no raw logs, raw transcripts, credentials, or local
  absolute paths.
- `python3 examples/long-horizon-self-iteration-rollout-fixture-smoke.py`
  passes before a fixture is used as UI input.
