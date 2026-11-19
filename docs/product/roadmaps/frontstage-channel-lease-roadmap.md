# Frontstage Channel And Lease Roadmap

LoopX should not become a chat product. Its durable value is the
backstage control plane: registry, active state, append-only event history,
quota, gates, leases, and auditable recovery. The missing product layer is a
frontstage projection that lets people understand and coordinate that control
plane without reading raw CLI dumps.

This note frames the product direction as:

```text
frontstage channel UX + backstage LoopX ledger
```

The channel is a view. The ledger is truth.

## Product Boundary

LoopX should borrow collaboration language without moving the source of
truth into chat history:

- A **goal can project as a channel**: one timeline with latest state, next
  action, user todos, agent todos, gates, artifacts, quota, and run events.
- An **agent can project as a workspace member**: controller, executor,
  reviewer, monitor, critic, or dreaming/planning proposer, each with scope and
  last action.
- A **task claim is a soft per-todo route by default**. When a concrete
  contention case needs exclusivity, an optional hard lease adds TTL, write
  scope, idempotency, and conflict handling for one `todo_id`.
- A **chat or channel thread is a projection**: useful for human collaboration,
  but never the only durable authority.

The key product lesson is not "add Slack-like chat". It is that humans think in
channels, members, tasks, and approvals, while agents need registry, state,
history, quota, gates, and leases.

## Minimal Schemas

### `goal_channel_projection_v0`

This is a read-only, human-facing projection over existing LoopX state.
It lets a frontstage render a goal as a channel without making the channel a
new source of truth. The append-only run ledger, active state, and registry
remain authoritative; the projection only carries compact source references and
freshness metadata.

```json
{
  "schema_version": "goal_channel_projection_v0",
  "goal_id": "loopx-meta",
  "display_name": "LoopX Meta",
  "generated_at": "2026-06-20T00:00:00Z",
  "source_refs": {
    "status_generated_at": "2026-06-20T00:00:00Z",
    "active_state_updated_at": "2026-06-20T00:00:00Z",
    "latest_run_generated_at": "2026-06-19T23:55:00Z",
    "review_packet_generated_at": null
  },
  "waiting_on": "codex",
  "latest_status": "terminal_bench_case_running",
  "next_action": "compact-poll the active benchmark job",
  "decision_frame": {
    "user_action_required": false,
    "agent_action_required": true,
    "quiet_noop_allowed": false
  },
  "quota": {
    "state": "eligible",
    "reason": "1 compute quota",
    "spend_policy": "spend after validated writeback"
  },
  "user_todos": [
    {
      "todo_id": "todo_user_1",
      "title": "Review the bounded delivery packet.",
      "status": "open",
      "priority": "P0"
    }
  ],
  "agent_todos": [
    {
      "todo_id": "todo_agent_1",
      "title": "Advance the first executable safe side path.",
      "status": "open",
      "priority": "P1",
      "claimed_by": "codex-side-bypass"
    }
  ],
  "open_gates": [
    {
      "gate_id": "gate_owner_decision",
      "kind": "operator_gate",
      "status": "waiting_on_user",
      "blocks": ["todo_user_1"]
    }
  ],
  "artifacts": [
    {
      "kind": "doc",
      "label": "latest public review packet",
      "path": "docs/showcases/README.md"
    }
  ],
  "active_leases": [
    {
      "todo_id": "todo_agent_1",
      "owner_agent": "codex-side-bypass",
      "lease_until": "2026-06-20T00:30:00Z",
      "write_scope": ["docs/**"]
    }
  ],
  "recent_events": [
    {
      "generated_at": "2026-06-19T23:55:00Z",
      "classification": "validated_progress",
      "summary": "public-safe compact progress event"
    }
  ],
  "source_warnings": []
}
```

The v0 source map should stay boring and inspectable:

| Projection field | Source surface |
| --- | --- |
| `goal_id`, `display_name`, `waiting_on`, `latest_status` | `loopx status` project asset and registry metadata |
| `next_action`, `user_todos`, `agent_todos`, `open_gates` | active state todo/gate sections plus `review-packet` summaries |
| `decision_frame` | `interaction_contract` from `quota should-run` and review-packet routing |
| `quota` | `quota should-run`, including the spend policy and capability/workspace guards when present |
| `artifacts` | public-safe docs, compact run artifacts, review packets, or showcase assets already allowed by `goal_boundary` |
| `active_leases` | current soft claims and explicitly supplied optional `task_lease_v0` rows |
| `recent_events` | compact run-history rows only, not raw logs or transcripts |
| `source_warnings` | stale state, todo projection gaps, private-boundary omissions, or missing authority sources |

The projection must exclude raw chat transcripts, raw benchmark task text, raw
trajectories, credentials, production logs, private document URLs, local
absolute paths, and write-capable commands. If a useful frontstage field would
need one of those sources, emit a compact `source_warnings` item instead of
copying the raw material.

Frontstage consumers should treat this as an input snapshot:

- refresh it from LoopX rather than editing it in the UI;
- render controlled actions as links to CLI/review-packet flows, not as hidden
  write authority;
- show stale or missing-source warnings near the affected card;
- keep event detail drill-downs tied to compact run artifacts; and
- never let the channel view override `goal_boundary`, operator gates, quota,
  required capabilities, workspace guards, or task leases.

The first product-path read model lives in
`loopx/control_plane/goals/goal_channel_projection.py` and is covered by
`examples/project/goal-channel-projection-smoke.py` plus
`examples/project/goal-channel-frontstage-fixture-smoke.py`. It intentionally
stays read-only: callers pass already-compact status, quota, run-history,
review-packet, artifact, and lease/claim payloads; the builder emits
`source_warnings` when raw or private-looking fields appear instead of copying
those values into the channel. The static HTML renderer in
`loopx/presentation/renderers/goal_channel_html.py` and fixture in
`examples/goal-channel-frontstage-fixture.py` renders that projection into
semantic panels with `data-panel` markers, no write controls, and a visible
truth contract. `loopx --format json status` and the loopback
`serve-status` feed now expose the same read-only projection on
`attention_queue.items[].goal_channel_projection`, so a dashboard can render the
channel without recomputing project truth.

### `agent_profile_v1` And `agent_member_v1`

The registry-owned `agent_profile_v1` contract is defined in
[`docs/product/foundations/agent-profile-contract.md`](../foundations/agent-profile-contract.md).
Use it as the source of truth for registered agent id and advisory capability,
scope, and action preferences. Runtime authority still comes from peer identity,
task claims/leases, boundaries, repository policy, and explicit continuation policy.
The channel roadmap only needs the read-only member projection.

This is an identity and activity projection for an actor participating in a goal:

```json
{
  "schema_version": "agent_member_v1",
  "agent_id": "codex-local-controller",
  "agent_model": "peer_v1",
  "profile_role": "reviewer",
  "profile_role_is_advisory": true,
  "goal_id": "loopx-meta",
  "current_claims": ["todo_abc123"],
  "last_action": "refresh_state",
  "handoff_assignment_status": "task_policy_selected"
}
```

Profile roles should stay product-level and portable: executor, reviewer,
monitor, critic, dreaming proposer. They guide UI copy and discovery, not
identity rank or default permission. Concrete authority comes from
`goal_boundary`, claims/leases, typed task policy, and active-state todos.

### `task_lease_v0`

This optional local file-backed concurrency contract is shipped through
`loopx task-lease acquire|renew|transfer|release|inspect`. The
pending key is per todo: `(goal_id, todo_id)`. Do not serialize an entire
goal just because one todo is claimed; independent todos under the same goal
should remain independently claimable when gates and write scopes allow it.
LoopX does not have a separate issue object in this runtime model:
`goal_id` names the control-plane boundary, and `todo_id` names the work item
inside that boundary.
The peer control plane keeps assignment explicit: `claimed_by` or a task lease
owns one todo, and repository-writing peers use isolated worktrees when task or
goal policy requires it. A small AGENTS-eligible change may self-merge with
evidence; otherwise completion creates an independent successor or an explicit
review action over an independent handoff, optionally excluding the author.

```json
{
  "schema_version": "task_lease_v0",
  "goal_id": "loopx-meta",
  "todo_id": "todo_123",
  "owner": "codex-local-controller",
  "idempotency_key": "loopx-meta:todo_123:20260615T1230Z",
  "write_scopes": ["docs/product/roadmaps/frontstage-channel-lease-roadmap.md"],
  "version": 1,
  "lease_epoch": 1,
  "acquired_at": "2026-06-15T12:00:00Z",
  "updated_at": "2026-06-15T12:00:00Z",
  "expires_at": "2026-06-15T12:30:00Z",
  "status": "active"
}
```

The current implementation is local and file-backed, with per-goal locking,
renewal, transfer, release, registered-owner validation, and stale-owner
invalidation. A concrete same-agent/multi-process completion race established
the first lifecycle adoption case: while a lease is effective, `todo complete`
must present the acquire idempotency key and current version, and holds the
lease lock through Todo and successor writeback. The pair fences execution
instances that share one registered `agent_id`; renew, transfer, release, and
terminal writeback all require the current version. `lease_epoch` is the
authority-owned generation: acquire and transfer advance it, while ordinary
renewal only advances `version`.

Release and committed terminal writeback retain an inactive `status=released`
record at the same per-todo path. This single-record tombstone preserves the
last version and epoch, so re-acquire cannot reset to version 1. Reusing the
just-retired execution key is rejected; a new execution key receives the next
version and epoch. Active-lease projections ignore the tombstone, while exact
release retries return its original terminal result. A completed Todo remains
terminal-idempotent, so a stale replay cannot append a second successor after
the canonical completion commits. A later server can own the same schema and
coordination surface.
Conflicts should be detected by `(goal_id, todo_id)` plus overlapping
write-scope checks: another agent may claim a different todo in the same goal,
but a second pending claim on the same todo must fail closed, renew, or
explicitly transfer ownership.

## Priority

P1:

- Keep the shipped optional `task_lease_v0` runtime, lifecycle fence, and
  conflict smoke stable. The proven same-agent completion race justifies
  fencing `todo complete` when a lease is active; do not turn lease acquisition
  into a default quota gate.
- Treat the shipped React `/frontstage` route as the baseline
  `goal_channel_projection_v0` reader. Future work should polish visual
  acceptance, operator onboarding, and local fixture realism while preserving
  the invariant that the CLI/status export is the source and the browser has no
  write authority.

P2:

- Decide whether active hard-lease rows should join the existing agent-member
  status/review projection after an adoption case proves the extra signal useful.
- Build a Raft-style local frontstage view that renders channel timelines and
  member activity from LoopX projections.
- Let dreaming/planning proposals appear as a separate channel lane or badge.
- Add bridge adapters that can post channel summaries to collaboration tools,
  while preserving LoopX as the ledger of record.

## Non-Goals

- Do not make conversation history the only project state.
- Do not let UI membership labels override `goal_boundary`, operator gates, or
  run permissions.
- Do not require a server for the first schema; the CLI must remain a usable
  fallback/client.
- Do not let background dreaming claim delivery work without the normal
  `quota should-run` and lease path.

## Acceptance Frame

The first successful slice should prove that a human can open one goal view and
see:

- what the goal is;
- who or what is currently responsible;
- which task is claimed and until when;
- which files/surfaces that claim may touch;
- which event made the current state true;
- what the next safe action is.

The corresponding agent should be able to read the machine projection and avoid
double-running, double-spending, or writing outside scope.
