# Agent Management Observability MVP

This note turns `agent_management_projection_v0` into a concrete first product
slice for the LoopX dashboard. It is intentionally an observability MVP, not a
new scheduler, dispatcher, task database, or browser write path.

## Decision

Use the mature agent-console direction for the real ops surface, and keep the
LoopX dark showcase direction for public narrative pages.

The MVP should feel like a dense operator console: rows, narrow badges,
timestamps, evidence links, and stable filters. The dark showcase style can
explain the same model on the public frontstage, but it should not be the
default for live multi-agent operation because live operation needs scanning
more than motion.

## Source Inspiration And Reuse Boundary

The closest public reference is NousResearch Hermes Agent:

- Hermes Kanban documentation:
  <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/kanban.md>
- Hermes delegation documentation:
  <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/delegation.md>
- Hermes license:
  <https://github.com/NousResearch/hermes-agent/blob/main/LICENSE>

Hermes Agent is published under MIT license, so UI code can be considered for
reuse in a later implementation PR if it is actually public, attribution is
kept where required, and the copied code is isolated to presentation. This MVP
does not copy Hermes source code.

The useful ideas to borrow are:

- dense task/agent rows;
- visible assignee, status, workspace, timestamp, and liveness;
- event-tail and attempt-history affordances;
- comments or handoff notes as inspectable context;
- fresh-context delegation as a warning against hidden inherited assumptions.

The ideas not to borrow into LoopX runtime are:

- a second durable task database;
- automatic dispatch, cancel, reclaim, or retry;
- worker profile runtime;
- workspace allocation runtime;
- browser writes that bypass LoopX CLI/API boundaries.

LoopX already has the durable work unit: `todo_id` inside `goal_id`. The
dashboard may render a todo as a task-like card for familiarity, but product
and runtime names should keep `todo` or `work item` to avoid implying a second
state machine.

## MVP Surface

Add an explicit Agent Management section to Personal Workspace after the
projection exists. The deprecated `/deprecated/frontstage/ops` route may keep
rendering the old projection for diagnostics, but must not receive the product
implementation.

The first screen should answer five questions:

1. Which registered agents are active for this goal?
2. What is each agent currently claimed on?
3. Is the agent running, waiting, blocked, monitoring, stale, or unknown?
4. What evidence or handoff makes the next step reviewable?
5. What quota, cadence, or workspace hint should the operator notice?

## Agent Row

Each row should map one `agent_management_projection_v0.agents[]` item into a
compact row or card.

Required visible fields:

- agent id and role;
- state badge;
- current todo title and priority;
- claim owner or "unclaimed";
- next action, one or two lines;
- last activity time;
- evidence/handoff link count;
- quota or scheduler hint when it changes operator behavior.

Optional expandable fields:

- required write scopes;
- workspace hint;
- stale claim hint;
- recent event tail;
- blocked-on decision;
- related user todo count.

## Status Badges

Use a small, stable badge set:

| State | Meaning | Operator posture |
| --- | --- | --- |
| `running` | LoopX expects the agent to keep working. | Watch evidence and quota. |
| `waiting` | The lane is eligible later or waiting for another state transition. | No immediate action. |
| `blocked` | Work cannot proceed without a blocker resolution. | Inspect blocker and decide if it is user-facing. |
| `monitoring` | This is a continuous monitor lane. | Show only material transitions. |
| `scope_wait` | The current item is outside the agent lane or write scope. | Check assignment or handoff. |
| `stale` | The claim lacks fresh activity evidence. | Inspect evidence; do not auto-reclaim. |
| `unknown` | Projection is missing enough data. | Show source warning. |

These badges are read-only. They do not perform lifecycle transitions.

## Evidence And Handoff Links

Evidence links should be visible but thin:

- latest run or refresh-state record;
- linked docs or protocol files;
- validation command labels;
- handoff note ids;
- review packet refs;
- public-safe source warnings.

Do not inline raw logs, raw trajectories, private documents, local absolute
paths, credentials, or status JSON blobs. The row should show that evidence
exists and let the operator drill into safe refs.

Handoff notes should render as typed attachments to todos/history/evidence:

- from agent;
- to agent;
- intent;
- unresolved decision count;
- suggested next action;
- evidence refs.

They are not a chat stream and they are not approval.

## Quota, Cadence, And Workspace Hints

The agent row should show quota/cadence only when it changes the operator's
decision:

- eligible now;
- throttled or waiting;
- no-spend monitor poll;
- scheduler backoff applied;
- stale due to missing activity evidence.

Workspace hints are display-only:

- `canonical_checkout`;
- `worktree`;
- `external`;
- `unknown`.

Hosted or public surfaces should avoid local absolute paths. Local loopback ops
surfaces may show a path only when the status payload already exposes it and
the surface is explicitly local/operator-only.

## Read-Only Operator Actions

The MVP may expose read-only actions:

- copy review packet command;
- copy status/quota command;
- open safe evidence ref;
- filter by agent, state, or priority;
- collapse monitor rows;
- highlight stale or blocked rows;
- switch between table and lane view.

The MVP must not expose:

- claim/reclaim;
- cancel;
- dispatch;
- unblock;
- priority mutation;
- workspace creation;
- reward append;
- external production action.

Future write actions must be introduced behind a separate capability gate and
must call typed LoopX CLI/API transitions.

## Implementation Shape

Prefer a thin projection adapter over a new runtime model:

```text
loopx status/review-packet/evidence ledger
  -> agent_management_projection_v0
  -> dashboard read model
  -> rows/cards/timeline
```

The dashboard should tolerate the projection being absent. If absent, show the
current ops dashboard and a source warning; do not block the rest of the page.

The first frontend PR should be allowed to build only a fixture-backed read
model plus browser-visible anchors. Live status consumption can follow after
the fixture validates the interaction model.

## Acceptance Checks

The first implementation should prove:

- the surface consumes `agent_management_projection_v0` when present;
- the dashboard still works when the projection is absent;
- no browser write affordance is rendered;
- `todo_id` remains the displayed work-item identity;
- stale claim is a warning only;
- evidence and handoff links render as refs, not raw logs;
- public fixtures contain no credentials, private docs, raw trajectories, or
  local absolute paths;
- copied or adapted external UI code carries a license/attribution note, or the
  implementation is native LoopX code.

## Implemented Slice

`apps/presentation/dashboard` now renders the read-only Agent Management panel from
`agent_management_projection_v0` when live status exposes it. The panel shows
agent rows, current todos, evidence refs, typed handoff notes, quota hints, and
display-only workspace/stale-claim warnings. These hints do not expose reclaim,
cancel, dispatch, unblock, or workspace-write actions.

The bundled dashboard example is refreshed from a public-safe live LoopX
`loopx-meta` agent slice by
`examples/control_plane/export-agent-management-status-example.py`. It keeps
real agent ids, todo ids, states, and timestamps where safe, redacts private
local text, and does not invent workspace or handoff fields when the live
projection does not have them. The synthetic smoke still covers workspace,
handoff, and stale-claim rendering as a contract fixture.

Todo rows that exist only in the rollout event log have no task text, so the
index titles them by `todo_id` with
`title_source: "event_audit"` and keeps the rollout audit sentence in
`latest_event_summary`. An audit line is never rendered as a task title; rows
that also exist in the attention queue keep their queue text and have no
`title_source`.

## Next Slice

Add filters for agent state, claim freshness, and workspace kind after the panel
has enough real operator use to justify more controls.
