# quota_planning_horizon_v0

`quota_planning_horizon_v0` is the bounded, read-only context that lets an
agent reason beyond one locally selected Todo without inlining LoopX's full
Todo store or task graph. It is present on the default `quota should-run` path
when a planning choice has strategic context. The `planning_horizon_goal`
setting is **on by default**. It carries the current `core_goal`
before work selection, including for a single local Todo or flat backlog.
The bounded work summary remains visible for a flat runnable backlog: missing
declared dependencies or gaps do not establish that work ordering is sound.
Completeness and detail references make omitted work explicit. Action selection
still belongs to `action_portfolio`. When disabled, the previous
strategic-context condition and projection are unchanged.

The problem it addresses is narrower than scheduling. A selected regression
gate can be locally runnable while the strategic path also contains a facts
source, policy decision, runtime admission, and per-target validation. Showing
only the gate, or only two alternative actions, can make a model optimize the
visible leaf. The planning horizon makes that bounded chain visible. It does
not silently replace the selected Todo.

## Ownership

- Existing Todo, claim, capability, goal-frontier, and event-ledger contracts
  remain authoritative.
- Python selects one canonical planning source from Todo domain-state rows:
  `items`, `deferred_items`, `blocker_items`, and `monitor_open_items`. It does
  not rebuild the source from presentation lanes or infer hierarchy from prose.
- TypeScript normalizes that source once as `todo_planning_inventory_v0`, with
  orthogonal planning and claim states, typed relations, action eligibility,
  and source-completeness metadata. `action_portfolio` and `planning_horizon`
  are separate lenses over this inventory.
- `--include-detail agent-todos` exposes a larger planning-only inventory lens
  beside the bounded Todo summary. `task_graph_projection_v0` reuses the same
  canonical selector, extends it with terminal predecessor/evidence rows, and
  remains agent-neutral while adding gate/evidence data.
- Effect Program transports the completed observation into TurnEnvelope. It
  does not own Todo dependency, resume, or action-selection semantics.
- `selected_todo` and `action_portfolio` remain the only action-selection
  authorities. Planning-horizon items are context until the agent performs the
  existing explicit selection/re-entry flow.

The Goal lens reuses the replan source reader and TypeScript `projectCoreGoal`
projection: active-state Objective first, registered objective as fallback,
explicit missing state, and the existing separately scoped acceptance contract.
It does not persist another Goal, promote an Agent vision or Todo into the
original objective, enable stride, or change action eligibility. When
available, `core_goal.read_action` reads the same complete Goal state, with the
source path shell-quoted. A corrected source is read on the next planning
projection; no copied objective needs synchronization.

This changes the default preselection context, including single-Todo and flat
backlog cases; an explicit false retains the previous projection and skips the
extra Goal-document read. Existing Todo selection,
claim/lease, admission, replan precedence and settlement authority remain
unchanged. Goal text is scoped decision input; public export remains a separate
boundary. The complete original objective is not shortened to the Todo display
budget. Transport-size warnings remain visible rather than discarding its tail.

## Default, Read Back And Advanced Opt-Out

The existing **Goal review and stride** capability editor in Settings →
Capabilities → **Advanced settings** exposes **Show Goal in planning horizon**
at machine and Goal scope. Ordinary use requires no configuration step.
Use its preview, apply and readback flow. Its optional boolean is stored in the
existing `execution_profile.planning_horizon_goal`; no new capability or Goal
store is introduced. The v2 machine review configuration accepts the same
optional boolean; omission preserves its stored shape and now resolves to on.
As with stride, a Goal with an explicit review cadence uses its own configuration;
a sparse Goal switch inherits the live interval. An explicit false overrides a
machine value. Clearing a Goal override restores the applicable inherited value.
The form shows effective values without pinning an unchanged inherited horizon
when another field is saved. Explicit JSON true/false remains an override.

```sh
loopx configure-goal --goal-id example --execution-planning-horizon-goal --execute
loopx configure-goal --goal-id example --format json
loopx quota should-run --goal-id example --agent-id researcher --format json
loopx configure-goal --goal-id example --no-execution-planning-horizon-goal --execute
```

The quota readback contains `planning_horizon.core_goal` when this projection
is enabled (the default) and a preselection planning projection is applicable. It is not added
to receipt-bound replays or ineligible work merely to repeat the Goal. Disabling
skips the extra Goal-document read. Replan continues to read its existing Goal
context independently. TurnEnvelope, stride, execution permission and quota are
not enabled by this setting. Context/signature changes are observable input
changes, not evidence of improved model decisions.

## Agent Decision Authority

Use the horizon to choose a coherent outcome across related work, considering
current user direction, evidence, dependencies, expected value, switching cost
and remaining budget. Priority ranks the default recommendation; a ready
higher-priority deferred successor does not veto an explicitly chosen eligible
open Todo. It stays visible for planning and retains its lifecycle state.
If choosing another route matters to the plan, record why in the existing Todo
note or vision/path-delta writeback. Do not manufacture priority changes or
extra-small Todos merely to satisfy the ordering.

The horizon itself grants no execution authority. Select through the existing
explicit selection/re-entry flow, which still checks scope, capabilities,
claims, resume conditions, workspace and real blocking lanes. A successor link
alone is not a dependency. A selected deferred Todo still requires its resume
transition before execution.

A committed Turn binds one execution identity, not the Todo until completion.
At an appropriate checkpoint, preserve progress, settle that original Turn and
release its lease before selecting another Todo in a new Turn. Re-entry of an
unsettled Turn preserves its original identity; it cannot silently account
unrelated work or restart a completed effect. Unbound suggestions and pending
choices have no such execution identity to protect.

## Shape

```json
{
  "schema_version": "quota_planning_horizon_v0",
  "mode": "read_only",
  "goal_id": "loopx-meta",
  "agent_id": "codex-main-control",
  "selected_todo_id": "todo_regression_gate",
  "core_goal": {
    "objective": "Deliver the complete accepted outcome.",
    "objective_source": "active_state",
    "objective_missing": false,
    "acceptance_contract": null
  },
  "selection_contract": {
    "selected_todo_authority": "$.selected_todo",
    "action_choice_authority": "$.action_portfolio",
    "horizon_changes_selection": false,
    "explicit_selection_required_for_other_work": true
  },
  "work_items": [],
  "relations": [],
  "acceptance_gaps": [],
  "attention_todo_ids": [],
  "completeness": {
    "schema_version": "quota_planning_horizon_completeness_v0",
    "source_context_todo_count": 8,
    "candidate_input_count": 7,
    "source_unrepresented_todo_count": 1,
    "omitted_candidate_todo_count": 2,
    "omitted_relation_count": 1,
    "omitted_acceptance_gap_count": 1,
    "compact_field_truncation_count": 0,
    "complete": false
  },
  "detail_refs": {
    "selected_todo": {
      "schema_version": "todo_detail_ref_v0",
      "goal_id": "loopx-meta",
      "role": "agent",
      "todo_id": "todo_regression_gate",
      "projection": "todo_detail_cold_path_v0"
    },
    "agent_todos": "quota should-run --goal-id loopx-meta --agent-id codex-main-control --include-detail agent-todos",
    "full_todo_list": "todo list --goal-id loopx-meta --role agent --status open --agent-id codex-main-control",
    "task_graph": "status --include-task-graph"
  }
}
```

## Bounds And Ordering

The shared inventory accepts at most 128 rows from each adapter lane, retains
at most 64 normalized Todo items, and reports every overflow as incomplete
instead of rejecting an otherwise read-only quota decision. The horizon then
projects at most:

- 5 Todo context items;
- 8 typed relations;
- 2 goal acceptance gaps;
- 3 `attention_todo_ids`.

The selected Todo is first. Typed relatives of the selected Todo are next,
ordered outward through the relation graph. Unrelated waiting, blocked, or
scheduled context follows before higher-priority and other runnable
alternatives; the action portfolio already owns flat alternative choice. This
ordering helps the agent reconstruct a strategic chain while keeping the
selected Todo identity stable.

`planning_state` is one of `selected`, `runnable`, `waiting`, `blocked`,
`scheduled`, or `context`. It is independent of `claim_state`, which is one of
`current_agent`, `unclaimed`, or `other_agent`. In particular,
`planning_state=runnable` plus `claim_state=unclaimed` means that the work can
advance only after the existing claim flow; the horizon carries
`claim_required_before_work=true` for selected or runnable unclaimed work.
`context_reasons`
explains why a non-selected item was retained, for example
`related_to_selected`, `higher_priority_than_selected`, or
`runnable_alternative`.

Every omitted source, candidate, relation, gap, or compacted text field is
counted. `complete=true` is valid only when all of those counts are zero. A
consumer must follow `detail_refs` when the answer depends on omitted context;
it must not treat the five visible items as the entire Goal.
`source_context_todo_count` counts current open plus deferred Todos; completed
history is intentionally outside this planning view.

The detail paths have deliberately different scopes:

- `planning_horizon` is the default hot-path summary;
- `quota should-run ... --include-detail agent-todos` adds
  `agent_todo_planning_inventory.schema_version=
  todo_planning_inventory_detail_v0`. It exposes planning/claim state,
  eligibility flags, relations, and completeness for the larger bounded
  inventory, while `item_detail_ref=$.agent_todo_summary` avoids duplicating
  Todo text, scopes, and capabilities;
- `todo list ... --role agent --status open` reads the complete current Todo
  source rather than pretending a bounded quota packet is exhaustive;
- `status --include-task-graph` adds agent-neutral graph, gate, and evidence
  context.

When the same observation is nested in `loopx_turn_envelope_v0`, the transport
may replace the repeated `detail_refs` object with
`detail_refs_ref="$.detail_ref"`. The Turn envelope's top-level cold path then
owns those reads. This is transport compaction only: it does not change the
TypeScript reducer output, completeness accounting, or action authority.
If the exact same acceptance contract is already inline in
`work_context.sources`, `core_goal.acceptance_contract_ref` points to that source
instead of repeating its body. The referenced content remains signed. Different
scopes/revisions and unavailable sources retain the full inline contract.
For the Goal-bearing horizon, an identical selected-work description may use
`text_ref` to the envelope's selected Todo text or recommended action when
that reference is smaller. Other work, completeness and mismatched text stay
inline; the reference target remains covered by the action signature.

## Relation Semantics

The v0 relation kinds are:

- `successor`: derived from `successor_todo_ids`, with
  `enforcement=lineage_only`;
- `unblocks`: derived from `unblocks_todo_id`, with typed lifecycle semantics;
- `resumes_when`: derived from `resume_when`, with typed condition semantics;
- `superseded_by`: persisted lineage only;
- `routes_via`: a read-only reference to the existing route id/key.

The distinction between lineage and enforcement is intentional. A successor
link is not upgraded into a hard dependency merely because the planning view
can draw a chain. v0 does not infer `depends_on` from phrases such as "after",
"requires", or "blocked by". A future hard dependency requires a separate
typed Todo contract and transition policy.

## Effect Program Boundary

Effect Program gains one nullable `planning_horizon` observation so every host
that consumes TurnEnvelope can receive the same domain-owned projection. This
is a transport extension, not a generic planning reducer. Todo resume remains
Todo-local reducer/ACK semantics, and quota selection remains in its existing
typed boundaries.

TurnEnvelope action-signature coverage advances to
`turn_envelope_action_dimensions_v3` when the horizon is present. CLI
differential qualification recognizes only the paired migration
`none -> quota_planning_horizon_v0` and v0/v1/v2 action coverage to v3. That
transition receives one bounded JSON growth allowance. Once v0 is in the base,
ordinary hot-path growth limits apply again.

## Acceptance Checks

A conforming implementation must prove:

- the TypeScript reducer owns validation, ordering, de-duplication, and bounds;
- the Python adapter consumes canonical Todo state rows and creates no second
  store or presentation-lane union;
- portfolio, horizon, and agent-Todo detail consume the same typed inventory;
- task graph consumes the same canonical Todo selector, plus terminal evidence
  rows, without acquiring agent-scoped selection or claim authority;
- omitted settings expose the original Goal; explicit false retains the prior
  horizon and skips extra Goal reads;
- when enabled, the same current Goal reaches preselection horizon and replan, with original
  trailing constraints and source readback intact;
- missing, corrected and fallback sources stay explicit; scoped acceptance and
  latest Agent vision do not replace the original Goal;
- the horizon never changes `selected_todo` or action-portfolio eligibility;
- runnable unclaimed work preserves `claim_required_before_work=true`;
- typed successor/resume/route relations survive the default compact path;
- prose-only dependency claims create no relation;
- source sizes above legacy decoder limits degrade to explicit incompleteness
  rather than failing quota;
- truncation and source incompleteness are explicit on both inventory and
  horizon;
- the selected Todo detail and optional full task graph remain reachable;
- full quota and TurnEnvelope action signatures cover the same horizon;
- deterministic mutation tests reject a missing or producer-drifted horizon
  before live-model spend;
- live model evidence is retained only as bounded receipts, never raw prompts,
  responses, credentials, or local paths.
