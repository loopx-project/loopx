# task_graph_projection_v0

`task_graph_projection_v0` is an optional read-only graph view over existing
LoopX state. It helps agents and operators see dependency, gate,
validation, repair, and handoff relationships without creating a second task
store.

The source of truth remains:

- the append-only event ledger and compact run indexes;
- the active goal state and its todos;
- operator gates and user todos;
- leases or todo claims;
- quota and status projections;
- run-history evidence and blocker writebacks.

The graph's Todo nodes are selected from the same canonical domain-state rows
used to build `todo_planning_inventory_v0`: `items`, `deferred_items`,
`blocker_items`, and `monitor_open_items`. The graph enables the selector's
terminal-row extension so completed predecessors and evidence remain visible;
the planning inventory excludes those rows. This prevents graph, horizon, and
portfolio consumers from inventing separate visibility-lane unions without
pretending that every lens has identical scope. It does not make the graph
agent-scoped: the graph may add gates, evidence, validation, and handoff
context, but it never assigns `current_agent` claim meaning or owns action
selection.

The projection may appear under `attention_queue.items[].task_graph_projection`
in `loopx --format json status --include-task-graph`. Default status output
keeps this object on the cold path so the dashboard hot path remains within its
interface budget. Full
`loopx --format json review-packet --goal-id <goal-id>` output may
include the same object for operator review. The handoff-only review-packet
surface should stay compact and omit the graph unless a future interface budget
explicitly allows it.

## Shape

```json
{
  "schema_version": "task_graph_projection_v0",
  "mode": "read_only",
  "goal_id": "loopx-meta",
  "generated_at": "2026-06-21T12:00:00Z",
  "derived_from": {
    "source_of_truth": [
      "event_ledger",
      "active_goal_state",
      "todos",
      "gates",
      "leases",
      "run_history"
    ],
    "status_item_goal_id": "loopx-meta",
    "active_state_updated_at": "2026-06-21T11:55:00Z",
    "run_history_window": "compact_latest_runs"
  },
  "truth_contract": {
    "event_ledger_is_source_of_truth": true,
    "projection_is_writable": false,
    "write_api": false,
    "recompute_rule": "Recompute from status, active state, gates, leases, and run history after each lifecycle event."
  },
  "limits": {
    "user_gate_node_limit": 2,
    "user_gate_open_count": 5,
    "user_gate_truncated_count": 3
  },
  "nodes": [],
  "edges": []
}
```

`limits` explains hot-path truncation. The task graph may expand only the first
`user_gate_node_limit` open user gate nodes. When more user gates are open,
`user_gate_open_count` and `user_gate_truncated_count` must say so. Consumers
that need the complete gate list should use the user todo detail path or full
review packet fields instead of treating the graph as an exhaustive store.

## Nodes

Each node must be compact and must point back to durable LoopX ids.
Allowed `kind` values are:

- `deliverable`: a todo-backed artifact or implementation step;
- `gate`: a user, owner, or operator decision point;
- `gate_summary`: a compact "more gates exist" node used when user gates are
  truncated from the graph hot path;
- `lease`: an active claim or worker ownership signal;
- `validation`: a smoke, check, CI result, or review proof;
- `repair`: a self-repair or blocker-recovery step;
- `handoff`: a transition from one agent or surface to another;
- `evidence`: a compact run-history evidence item.

Required node fields:

- `node_id`: stable inside this projection;
- `kind`;
- `title`;
- `state`: one of `open`, `ready`, `blocked`, `done`, `waiting`, or `unknown`;
- `refs`: compact references such as `todo_ids`, `gate_ids`, `lease_ids`,
  `goal_ids`, `run_ids`, or `review_packet_ids`.

Nodes must not copy raw task text, transcripts, logs, credentials, private file
paths, or large run artifacts. They should summarize only the relationship
needed for dispatch or review.

## Edges

Edges describe why one node affects another. Allowed `relation` values are:

- `depends_on`;
- `blocks`;
- `validates`;
- `repairs`;
- `audits`;
- `continues`;
- `hands_off_to`;
- `supersedes`.

Each edge must name `from_node_id`, `to_node_id`, `relation`, and a compact
public-safe `reason`. Edges may carry the same compact `refs` object as nodes.
An edge does not grant permission to run a command or mutate state.

`repairs`, `audits`, and `continues` are lineage relations, not lifecycle
commands. They are derived from existing run history, todo/gate metadata, and
compact blocker or validation writebacks:

- `repairs` says a repair or replan node is intended to recover a selected
  work lane.
- `audits` says compact run-history evidence reviews, checks, or bounds a
  selected work lane.
- `continues` says compact run-history evidence is a continuation of a selected
  work lane, or a Todo follows a predecessor through explicit successor lineage.

### Typed Todo topology

Planning inventory, horizon and task graph share the TS `planning_relations`
catalog. The graph is a different **read lens**, not another lifecycle reducer:

| Persisted relation | Graph direction and label | Meaning |
| --- | --- | --- |
| A has successor B | B → A, `continues` | Lineage only; does not require A to complete |
| A is superseded by B | B → A, `supersedes` | Lineage only; does not authorize a transition |
| A unblocks B | B → A, `depends_on` | Typed lifecycle link, not the reverse dependency |
| A resumes when B is done | A → B, `depends_on` | Completion condition; the resume evaluator owns readiness |
| A resumes when Monitor M changes | A → M, `depends_on` | Generation-change condition, not Monitor completion |

These are intentional corrections to the old graph, which collapsed successor
lineage into dependencies, reversed unblocks discovery, and omitted Monitor
conditions. Parallel lineage and condition edges are retained. Opaque route,
capability and unknown-condition suffixes must not be interpreted as Todo IDs
by either graph or horizon. Existing read-only node kinds, claim presentation
and evidence/handoff renderers are unchanged.

The predecessor lens expands the selected Todo and completed predecessors;
open predecessors are visible boundaries, not recursive traversal roots.
It admits at most four predecessor nodes, in deterministic breadth-first and
Todo-ID order. Cycles do not duplicate nodes. Reaching the node cap must not
discard another edge between already admitted nodes (including diamond joins).

`limits.missing_predecessor_count` counts unique referenced predecessors absent
from the supplied renderable snapshot. `source_truncated` records upstream
omission; `predecessor_truncated` records display-limit omission. The additive
`topology_complete` flag is true only if none of those conditions applies,
**within this expansion policy**, not for the entire Goal graph. No missing
target creates a phantom node, a provider read, a repair write, or an execution
permission. No extra complete-state read is introduced: status supplies its
existing source, and an incomplete summary stays explicitly incomplete.

中文：谱系不等于依赖；Monitor 的 generation 条件不等于完成 Monitor。
节点上限不应吞掉已展示节点间的边。完整度只针对上述有界展开策略，不能
把缺失、上游裁剪或展示裁剪说成完整 Goal 图；图始终没有写入或准入权限。

These relations may help a dashboard or reviewer explain why a work item is
still active, stale, repaired, or safe to hand off. They must not create a graph
resume command, mutate todo status, or replace freshness checks against current
quota, gates, claims, and run history.

A `deferred` Todo renders as `waiting` in every lens. Deferral ends scheduling,
not delivery, so it must never appear as `done`.

### Goal work map (`goal_task_map_v0`)

The predecessor lens answers "what led to this item". The Goal work map answers
"what is the shape of this Goal's work": one read-only map of the Goal's
non-archived Todos and every typed relation above. It ships beside `graph` in
the cold `GET /api/chat/delivery-review?goal_id=...` response and is never
added to status hot paths.

```json
{
  "schema_version": "goal_task_map_v0",
  "mode": "read_only",
  "goal_id": "community-day",
  "limits": {
    "node_limit": 120, "emitted_node_count": 22, "omitted_node_count": 0,
    "source_truncated": false, "missing_endpoint_count": 0,
    "cycle_edge_count": 0, "topology_complete": true
  },
  "nodes": [{ "node_id": "node_todo_...", "kind": "deliverable", "title": "Reserve the hall",
              "state": "blocked", "depth": 1, "refs": { "todo_ids": ["todo_..."] },
              "owner_agent": "logistics", "task_domain": "booking" }],
  "edges": [{ "edge_id": "edge_unblocks_...", "from_node_id": "node_todo_...",
              "to_node_id": "node_gate_...", "relation": "depends_on",
              "enforcement": "typed_lifecycle", "reason": "..." }]
}
```

- Node `kind` is `deliverable`, `gate` (a user Todo) or `monitor` (a
  `continuous_monitor` Todo). Gates carry no owner. Nodes carry no note,
  evidence or raw Todo text beyond the compact public-safe title.
- Edges use the relation mapping in the table above. `enforcement` reuses the
  `planning_relations` catalog (`typed_lifecycle`, `typed_condition`,
  `lineage_only`), so a reader can tell a lifecycle link from lineage.
- The TS effect `work_item.task_graph.goal_topology` owns admission, edges and
  `depth` (the longest recorded prerequisite path). Admission is deterministic:
  unfinished work first, then direct completed prerequisites of unfinished
  work, then remaining history, up to `node_limit` (at most 200).
- `depends_on` edges order the map. Lineage (`continues`, `supersedes`)
  carries no ordering obligation: it adds depth only where it agrees with the
  dependency order. A follow-up recorded opposite to a dependency, such as a
  gate spawned by the work it later unblocks, is drawn but is not a cycle.
- `cycle_edge_count` counts dependency loops only; their back edges are
  counted, not drawn as an order. `topology_complete` is true only when nothing
  was omitted, the source list was not truncated, no relation names an absent
  Todo, and no dependency loop exists. Missing endpoints never create phantom
  nodes.
- An unreadable Todo source returns `goal_map: null` and leaves the rest of the
  delivery review available.

Like the predecessor lens, the map has no write authority. A drawn line is a
recorded relation, not a readiness verdict; the resume evaluator and lifecycle
commands keep that authority.

中文：工作地图只读展示一个 Goal 的全部未归档 Todo 和已记录的类型化关系；
连线来自记录而非推断，不代表“可以执行”。顺序只由依赖决定，延续/替代仅在不与依赖
冲突时加深层级，因此反向记录的后续不算成环；`cycle_edge_count` 只统计依赖环。截断、
缺失端点和依赖环都必须显式计入 `limits`，不能把不完整的图画成完整顺序。

## Write Boundary

`task_graph_projection_v0` has no write authority. It must never expose a graph
write command, browser write affordance, hidden scheduler, or alternate lease
store. State changes continue through existing LoopX lifecycle commands:

- `loopx todo ...`;
- `loopx operator-gate ...`;
- `loopx reward ...`;
- `loopx refresh-state ...`;
- `loopx quota spend-slot ...`;
- future server/MCP write APIs that preserve the same event-ledger semantics.

Consumers should treat the graph as stale after any lifecycle event until it is
recomputed from the current status and run-history window.

## Acceptance Checks

A valid public fixture or implementation must prove:

- `schema_version` is exactly `task_graph_projection_v0`;
- `mode` is `read_only`;
- `truth_contract.projection_is_writable=false`;
- `truth_contract.write_api=false`;
- `limits.user_gate_node_limit` is present;
- `limits.user_gate_open_count` is present;
- `limits.user_gate_truncated_count` is present;
- every node id is unique;
- every edge endpoint references an existing node;
- every node and edge references existing LoopX ids rather than raw
  private material;
- repair, audit, and continuation relations are rendered only as derived
  read-only lineage over existing todos, gates, leases, and compact run ids;
- no local absolute paths, credentials, raw transcripts, or raw logs are
  projected;
- status/review-packet consumers can safely ignore the field when absent.
