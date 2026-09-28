# agent_management_projection_v0

`agent_management_projection_v0` is a read-only operator view over existing
LoopX agent, todo, quota, history, and evidence state. It exists so dashboard
and review-packet surfaces can show which agents are active, what each agent is
claimed on, and what evidence makes the next turn safe.

It does not introduce a runtime `task` object. In LoopX, `todo_id` remains the
only durable work-item identity inside a `goal_id`.

## Purpose

The projection helps operators answer:

- which registered agents exist for this goal;
- which todo each agent should see as its current work item;
- whether the agent is running, waiting, blocked, monitoring, or possibly
  stale;
- what evidence, handoff note, workspace, quota, and next action explain that
  state.

The first dashboard implementation should be an observability surface. It may
render a mature agent-console layout or reuse compatible public UI code, but it
must not become a dispatcher, lease manager, workspace manager, or write queue.

## Sources Of Truth

The projection is derived from:

- `loopx status --format json`;
- active-state `Agent Todo` and `User Todo` sections;
- registered-agent and claim metadata;
- quota `agent_lane_next_action`, `interaction_contract`, and scheduler hints;
- compact run history and the agent-scoped evidence ledger;
- task graph, handoff, and review-packet projections when present.
- optional prebuilt `agent_material_frontier_v0` packets on the cold-path
  `agent_material_frontiers` input, consumed only when the current execution
  envelope declares the goal-scoped `material_lifecycle` capability.

The projection is stale after any lifecycle event until recomputed. Consumers
must tolerate missing fields and fall back to existing status/review-packet
payloads.

When available, `loopx status --format json` exposes this view at the top-level
`agent_management_projection` key. Consumers should still treat the key as
optional so older status producers and cached snapshots remain readable.

## Non-Goals

This contract intentionally does not add:

- a new `task_id`;
- a writable Kanban/task table;
- automatic dispatch, cancel, or reclaim behavior;
- a separate comment system;
- a workspace allocation runtime;
- a tool gateway or agent profile runtime.

State changes still go through existing LoopX lifecycle commands such as
`loopx todo ...`, `loopx refresh-state ...`, `loopx quota ...`, evidence-log
writeback, and future APIs that preserve the same event-ledger semantics.

## Shape

```json
{
  "schema_version": "agent_management_projection_v0",
  "mode": "read_only",
  "goal_id": "loopx-meta",
  "generated_at": "2026-07-06T00:00:00Z",
  "style_hint": {
    "preferred": "mature_agent_console_or_loopx_dark_showcase",
    "license_boundary": "reuse_public_compatible_code_only"
  },
  "truth_contract": {
    "todo_is_runtime_work_item": true,
    "projection_is_writable": false,
    "introduces_task_runtime": false,
    "write_api": false
  },
  "agents": []
}
```

## Agent Row

Each agent row is a compact card or table row for one registered agent.

Required fields:

- `agent_id`;
- `agent_model`: `peer_v1`;
- `state`: one of `running`, `waiting`, `blocked`, `monitoring`,
  `scope_wait`, `stale`, `unknown`, `registered`, `addressable`, `bound`,
  `launchable`, `executing`;
- `current_todo`: a `todo_row_v0` object or `null`;
- `next_action`: compact local-control next action text. Private project refs
  are allowed; inline credentials are not. Shareable sinks must redact private
  refs before export;
- `last_activity_at`: best known status, quota, todo, or run timestamp;
- `evidence_refs`: compact evidence ids, doc paths, run ids, or review packet
  refs.

Optional fields:

- `profile_role`: an advisory functional label such as `reviewer`, `monitor`,
  or `runtime-validation`; it is not rank or authority;
- `scope_summary`;
- `quota_state`;
- `scheduler_state`;
- `workspace_ref`;
- `handoff_refs`;
- `handoff_note`;
- `material_frontier`;
- `session_binding_candidates` and `session_binding_count`: present together
  when the Agent has at least one entry in existing
  `run_history.goals[].coordination.thread_agent_bindings`. The list holds up
  to three distinct `{thread_id, host_surface}` candidates in first-seen order
  and `session_binding_count` is the number of distinct bindings observed, so a
  shorter list than the count means candidates were capped, not that the
  remainder is invalid. Distinctness is decided on the full binding identity, not
  on the rendered text: the owner accepts wider identifiers than this projection
  displays, so two counted bindings can render alike. Neither field selects an execution route: absence is
  not evidence of a stopped host, a binding does not prove executable capacity,
  and several candidates for one Agent are resolved by the owning binding
  resolver, not by this display projection;
- `stale_claim_hint`;
- `blocked_on`;
- `recent_events`;
- `display_tone`.

When runnable advancement work exists alongside blocked maintenance, the
projection keeps the runnable todo in `current_todo` and may expose the
highest-priority blocked maintenance todo as a separate `blocked_on`
`todo_row_v0`. The blocker remains visible without changing todo ownership or
making the whole peer appear blocked.

### Worker state refinement and compatibility

The current producer emits `registered`, `addressable`, `bound`, `launchable`,
`executing`, `blocked`, `monitoring`, and `waiting`.
`running`, `unknown`, `scope_wait`, and `stale` remain
accepted legacy vocabulary; this producer does not emit them. There is no
parallel `lifecycle_state` field.

This changes the default read projection: registered idle rows previously
reported `unknown` or `waiting`, and open advancement work reported `running`.
The new states refine those observations without adding dispatch authority:

| State | Observed facts |
| --- | --- |
| `registered` | Registered row without current work or session binding |
| `addressable` | Session binding exists, with no current work |
| `bound` | Current open work and a session binding; no recent work update |
| `launchable` | Current open work without a binding or recent work update |
| `executing` | Current open work updated between zero and eight hours ago |
| `blocked` | Current work is blocked or has the blocker task class |
| `monitoring` | Current work is a monitor, regardless of activity age |
| `waiting` | Other non-open current work, such as deferred work |

Blocked, monitor and waiting classification precedes activity/binding refinement.
The eight-hour activity window is inclusive, rejects future timestamps, and
uses the current Todo only; it is independent of the 36-hour stale-claim
warning. `last_activity_at` still summarizes the displayed Todos. Neither an
`executing` observation nor `launchable` proves a live process, configured
runtime, available capacity, lease, or permission to start a worker.

Registered-peer orchestration accepts `executing`, `bound`, and `launchable`
where it accepted legacy `running`, and continues to accept `monitoring` and
cached `running` rows. Its existing stale-claim, observed activation-capability,
and dependency-readiness gates still apply. Idle, blocked, waiting and unknown
rows do not gain admission. Other consumers must tolerate the added strings.

## Todo Row

`todo_row_v0` is the dashboard/review-packet representation of an existing
LoopX todo. It is not a runtime task object.

Required fields:

- `todo_id`;
- `goal_id`;
- `role`;
- `status`;
- `priority`;
- `title`;
- `task_class`;
- `action_kind`;
- `claimed_by`.

Optional fields:

- `required_write_scopes`;
- `required_capabilities`;
- `target_capabilities`;
- `blocks_agent`;
- `unblocks_todo_id`;
- `successor_todo_ids`;
- `resume_when`;
- `evidence_refs`;
- `handoff_refs`;
- `workspace_ref`;
- `updated_at`.

The row may be rendered as a "task" card for operator familiarity, but API and
state names should keep `todo` terminology to avoid implying a second runtime
model.

### Capability prerequisites and repair outputs

The read-only capability gate distinguishes `required_capabilities` (execution
prerequisites) from `target_capabilities` (what a repair task aims to provide).
A target is not its own prerequisite, but declaring it neither enables it nor
grants credentials, production access, write scope, a claim or a lease. A task
repairing `network` can therefore be runnable while another task requiring
`network` stays blocked until availability is observed.

`agents/capability_gate.ts` owns missing-set evaluation, resolution responsibility
and impacted-Todo bindings. Unknown missing capabilities retain Agent repair
routing; `credentials` and `production_access` retain owner routing. Other runnable
work remains available when one candidate is blocked. A shared capability binding
uses the highest-priority affected Todo rather than whichever display row appears
first, while preserving all affected Todo IDs. Display variants of one ID do not
become separate tasks. An explicitly empty evaluated backlog is authoritative;
the older first-item source is used only when the backlog field is absent.

Quota's v1 planning request carries normalized requirement facts, so the same TS
rule partitions due Monitors without per-Todo RPCs or trusting stale precomputed
missing lists. Candidate source/eligibility and profile ranking remain adapters;
this does not claim complete-inventory selection or change Monitor due/writeback
and deferred-replan rules. Use `quota should-run --include-detail agent-todos` to
inspect the existing full capability-gate detail; the default CLI keeps its
compact representation. No configuration, UI control or storage migration is added.

## Handoff Notes

Inter-agent handoff should appear as a typed note attached to existing todo,
history, and evidence refs. Rows without material context retain the existing
`handoff_note_v0` shape:

```json
{
  "schema_version": "handoff_note_v0",
  "handoff_id": "handoff_123",
  "todo_id": "todo_abc",
  "from_agent": "codex-builder",
  "to_agent": "codex-reviewer",
  "intent": "review_before_merge",
  "summary": "Implementation and focused smoke are ready for review.",
  "evidence_refs": ["run_123", "docs/reference/protocols/example.md"],
  "unresolved_decisions": [],
  "blocked_on": null,
  "suggested_next_action": "Read the diff and smoke output before merge."
}
```

LoopX derives this note from existing todo, history, and evidence rows. Current
signals include `blocks_agent`, `claimed_by`, `unblocks_todo_id`,
`successor_todo_ids`, `resume_when`, `note`, `evidence`, and compact rollout
event refs. The same `handoff_note_v0` object can therefore appear inside
`agent_todo_summary` items, `todo_index` rows, or future dashboard rows without
introducing a second task model.

The projection may show the latest handoff note in the agent row, but the note
does not create a chat stream, dispatcher queue, approval mechanism, or runtime
task separate from the source todo.

When the cold path also receives a full `agent_material_frontier_v0` for the
same agent and the caller passes `material_lifecycle` in
`available_capabilities`, the management projection may enrich the typed note
as `handoff_note_v1`:

```json
{
  "schema_version": "handoff_note_v1",
  "handoff_id": "handoff_123",
  "todo_id": "todo_abc",
  "from_agent": "codex-builder",
  "to_agent": "codex-reviewer",
  "material_frontier_summary": {
    "required_count": 5,
    "current_count": 1,
    "stale_count": 1,
    "missing_count": 0,
    "inaccessible_count": 1,
    "required_unread_count": 2
  },
  "material_ref_count": 5,
  "material_refs": [
    {
      "material_id": "runtime-contract",
      "relation": "required",
      "purpose": "review the current contract"
    }
  ],
  "material_refs_truncated": true
}
```

`material_frontier` on the agent row uses the same bounded summary/ref shape
with `schema_version=agent_material_handoff_projection_v0`. At most four refs
are exposed. The projection never forwards receipts,
observed or required revisions, boundary availability, gate state, permissions,
authority ownership, or source bodies. A successor rebuilds its own frontier
from current goal authority and its own agent-scoped requirements and receipts.
The optional cold-path input does not create an agent row, claim, or task by
itself.

Material projection is default-off. Without `material_lifecycle`, LoopX
ignores `agent_material_frontiers` and omits `material_frontier`,
`handoff_note_v1`, and `source_summary.material_frontier_count`. Capability
absence is an observed runtime condition, not a user gate: it does not create
a todo, notification, or authority request.

Status-backed CLI entry points preserve that same execution envelope:
`status`, `quota`, and `review-packet` accept repeatable
`--available-capability` values and pass them through status collection.
Projection-cache identity includes the normalized capability set, so a
default-off snapshot cannot satisfy an enabled request and an enabled snapshot
cannot leak material fields into a default request.

The cold-path join is scoped by `(goal_id, agent_id)`, not agent identity alone.
An explicit status goal filter takes precedence, followed by the current todo's
goal and then a single unambiguous row goal. When a multi-goal agent row has no
unique goal context, LoopX omits the material enrichment instead of selecting a
frontier by input order.

## Stale Claim Hint

`stale_claim_hint` is an observability warning, not an automatic reclaim rule.
It means a claimed todo has not received recent activity relative to the
expected cadence, or the projection cannot find fresh evidence for a running
claim.

```json
{
  "state": "suspected_stale",
  "claimed_by": "codex-value-explorer",
  "last_activity_at": "2026-07-06T00:00:00Z",
  "reason": "last activity is older than expected cadence",
  "recommended_operator_action": "inspect evidence or ask the same agent to resume"
}
```

The dashboard may display this as a warning badge. LoopX should not automatically
clear the claim, reassign work, or discard evidence from this projection alone.

## Workspace Ref

`workspace_ref` is a display hint for where work is expected to happen:

```json
{
  "kind": "canonical_checkout|worktree|external|unknown",
  "label": "codex/value-explorer",
  "path_safe": false,
  "branch": "codex/value-explorer-post702",
  "write_scope": ["docs/**", "apps/presentation/dashboard/**"]
}
```

Public or hosted dashboards should avoid local absolute paths. Local loopback
dashboards may show paths when the source payload already exposes them and the
surface is explicitly local/operator-only.

## Frontend Style And Code Reuse

The product surface may borrow from two visual directions:

- a mature agent console style: dense rows, clear owner/state/timestamp
  columns, subdued badges, and fast scanning;
- the LoopX dark showcase style: dark rail, motion-light accents, evidence
  trail, and high-contrast agent lanes.

Before copying implementation code from Hermes or another project, the agent
must verify:

- the source is public or explicitly approved for this repository;
- the license is compatible with LoopX distribution;
- copied code keeps required attribution or notice text;
- private/internal identifiers, comments, screenshots, URLs, and test data are
  removed or generalized;
- the copied code does not import a runtime dispatcher, profile system, tool
  gateway, or task database that violates this projection contract.

If those checks are not satisfied, borrow only the interaction pattern and
write a native LoopX implementation.

## Acceptance Checks

A valid implementation or fixture should prove:

- `schema_version` is exactly `agent_management_projection_v0`;
- `mode` is `read_only`;
- `truth_contract.projection_is_writable=false`;
- `truth_contract.introduces_task_runtime=false`;
- every `current_todo.todo_id` references an existing LoopX todo;
- no writable task, dispatcher, cancel, reclaim, or workspace action is exposed
  by this projection;
- stale claim is rendered as a warning only;
- handoff notes reference existing todo/history/evidence ids;
- material frontier fields are absent without the observed
  `material_lifecycle` capability and retain their bounded shape when it is
  present;
- public fixtures do not include credentials, raw logs, private docs, raw
  trajectories, local absolute paths, or internal-only source material;
- dashboard consumers remain functional when the projection is absent.
