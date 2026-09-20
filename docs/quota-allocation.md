# Compute Quota

LoopX should own compute allocation across projects. The first version
should stay deliberately simple: each goal gets one compute quota number, and
automations or controller ticks use that number to decide how often the goal may
consume agent time.

This replaces the current ad hoc pattern where priority is encoded only by
changing automation periods. A timer can wake the executor, but the product
policy should live in LoopX.

## Product Scope

In v0.1, quota means **compute quota only**.

It does not decide human reward, write approval, production permission, or
operator gate outcomes. Those remain separate LoopX states.

Compute quota answers one question:

> Out of the available automatic agent time, how much should this goal be
> allowed to consume?

Examples:

- `1.0`: full duty cycle. If the controller checks hourly, this goal is
  eligible on every healthy check for the full 24-hour window. With the default
  minute-granularity accounting, `1.0` means `24 * 60 = 1440` automatic compute
  minute-slots.
- `0.5`: half duty cycle, roughly 12 hours per day or half of scheduling
  minute-slots.
- `0.3`: 30% duty cycle, roughly 7.2 hours per day or 30% of scheduling
  minute-slots.
- `0`: compute-paused. This is a **Goal-level hard pause**: the goal stays
  visible but receives no automatic Codex turns, and `quota should-run` returns
  a single authoritative paused contract. Because the pause is a typed terminal
  decision evaluated before any selector lane is built, no capability-bridge,
  workspace, replan, monitor, or inbox lane can leave a contradicting execution
  signal underneath it (`should_run=false`, all automatic permissions false,
  `DONT_NOTIFY`, scheduler never `run_now`, no quota spend). Set it with
  `loopx configure-goal --quota-compute 0`; negative values are rejected.

  This is different from a single agent's `monitor_only` work mode.
  `quota.compute=0` pauses the **whole Goal** for every agent and every lane;
  `monitor_only` is a per-agent lane state where one agent still runs bounded
  read-only monitor polls while the Goal itself remains active for other work.

The number can be interpreted as either a duty cycle or a relative weight,
depending on the executor:

- per-goal automations can turn `0.5` into "run on about half the ticks";
- a shared controller loop can use the same number as a weighted selection
  ratio between eligible goals.

## Completed-Todo Review Cadence

`execution_profile.replan_after_completed_todos` is an integer hyperparameter,
default **5** in both standard and fine-grained Turn modes. It may be set as a
live machine default, while an explicit Goal value remains pinned until it is
cleared. Set it to 2 or 3 for earlier review. Supported values are 1–5: the
current agent projection retains five recent completions, so larger values are
rejected.

```bash
# Preview, apply, and read back the Goal setting.
loopx configure-goal --goal-id example --execution-replan-after-todos 3
loopx configure-goal --goal-id example --execution-replan-after-todos 3 --execute
loopx configure-goal --goal-id example

# Restore live machine-default inheritance.
loopx configure-goal --goal-id example --clear-execution-replan-after-todos --execute
```

The Dashboard exposes **Goal review cadence** in both Machine Configuration and
Goal capability settings. Machine changes are revision-locked and affect every
Goal without an explicit override at its next quota/frontier evaluation. A Goal
override is a complete value, not a field merge; clearing it restores live
inheritance. Removing the machine namespace restores the capability default of
5. This is a setting of the built-in control plane, with no provider or plugin
installation. It does not create host turns, spend quota, or grant authority.

The count includes completed advancement Todos claimed by the same Agent, with
valid completion timestamps, after the latest qualifying outcome checkpoint.
Open Todos, other Agents' work, and protocol steps do not count. A checkpoint
must satisfy the existing material-outcome and acceptance-evidence rules;
a generic refresh or an unqualified replan ACK does not reset the window.
A closed Agent vision is excluded by the existing terminal-state rule.

At the threshold, the next `quota should-run --goal-id example --agent-id agent-a`
evaluation contributes a `vision_outcome_checkpoint_required` acceptance gap
with `completed_todo_count` and `completed_todo_threshold`, through the existing
replan obligation path. This is a machine-evaluated review obligation, subject
to existing lane arbitration. The review can retain the current path with
`continue` or `no_change`, or choose `replan`; reaching the threshold does not
require changing a correct plan. No permission or quota boundary changes, and
no external data is published by this setting.

This counter does not interrupt the host or schedule a continuation turn.
For review during a long turn, the executor must write completion state as
work finishes and re-enter quota before starting the next Todo. Completing all
Todos in one batch at closeout or never re-entering quota can defer review until
after implementation. Lowering the threshold alone cannot repair that gap.

Other cadences are independent: the standard profile's two-small-delivery
streak suggests widening work; fine mode's five-small-delivery streak suggests
direction review. Neither is this completed-Todo counter. The periodic review
window of 20 material run records and long-open-Todo-chain triggers also retain
their existing thresholds.

### Governed Turn Execution

`loopx turn plan` / `loopx turn run-once` is a separate execution surface from
fine-grained planning. A new Turn reads live quota, and successful material
execution goes through result validation, durable writeback, quota settlement,
and a fresh scheduler/quota readback. Its host result must declare an unchanged
path with a reason or a material replan with a bounded vision/path packet.
This path declaration is checked at each material Turn boundary; it is not an
automatic change of plan or independent proof of complete Goal acceptance.

The completed-Todo hyperparameter applies to the shared quota frontier and
write-time replan gate used by Turn execution. It does not change the per-Turn
result contract or count partial-progress Turns as completed Todos.

The separate [Turn Loop Controller](reference/protocols/turn-loop-controller-v0.md)
can request replan when a caller-provided budget for continued progress on one
Todo is exhausted. That budget has no default, and the `turn` CLI does not
invoke this controller or maintain that counter. `run-once` executes one Turn;
an outer caller still owns repeated execution. A completed-Todo threshold, a
per-Turn path declaration, and a same-Todo continuation budget are different
controls.

## Minimal Contract

The compact status shape can start with a small object:

```json
{
  "quota": {
    "compute": 0.5,
    "window_hours": 24,
    "slot_minutes": 1,
    "allowed_slots": 720,
    "spent_slots": 240,
    "state": "eligible",
    "next_eligible_at": "2026-06-02T12:00:00+08:00",
    "reason": "0.5 compute quota, 240/720 minute-slots spent in the current window"
  }
}
```

Registry entries may declare the same policy directly:

```json
{
  "quota": {
    "compute": 0.5,
    "window_hours": 24
  },
  "control_plane": {
    "self_repair": {
      "enabled": false,
      "allow_health_blocker_repair": false,
      "allow_waiting_projection_repair": false
    }
  }
}
```

If `quota.compute` is missing, status treats the goal as `1.0` by default so a
newly connected project remains eligible unless a harder gate blocks it.

`slot_minutes` is optional and defaults to `1`. The default `allowed_slots` is
computed as `window_hours * 60 / slot_minutes * compute`, so `compute=1.0` is
already the full available 24-hour duty cycle. Operators should only set
`allowed_slots` explicitly for exceptional overrides such as a temporary burst,
a deliberately stricter experimental cap, or a non-minute scheduler. It should
not be required to express normal full quota.

For the first implementation, `spent_slots` counts compact automatic compute
budget units. Under the default minute granularity, one slot means one minute of
automatic compute budget. Minute-based heartbeat continuations can spend
`--slots 1`; coarser controllers should spend the number of scheduler minutes
they actually reserve. It does not need to count exact tokens yet.

Status derives the current `spent_slots` from compact `quota_slot_spent`
runtime events in the current `window_hours` window. The registry remains the
policy source for `compute`, `window_hours`, and optional `allowed_slots`; it is
not the spend ledger.

Later versions can replace slots with real runtime, token spend, or cost, but
the operator-facing model should remain the same: a project has a simple
compute share.

## State Order

Quota is applied after hard gates:

1. Health and safety gates: broken registry, contract failures, or unsafe
   boundary issues block the goal.
2. Operator gates: read-only opt-in, write-control, reward judgment, and
   production actions still require explicit human decisions.
3. Evidence waits: a goal waiting on external metrics or target-controller
   response should not consume delivery compute just because quota remains.
4. Focus waits: a goal can still be Codex-owned while the current delivery lane
   is saturated or waiting for novelty, owner evidence, external eval, or a
   clean baseline. It should stay visible but should not spend delivery compute
   only because compute quota remains.
5. Compute quota: among goals that are otherwise eligible, quota decides
   whether this one should receive the next automatic turn.

This keeps the model small. Quota does not become a second permission system.

## Control Plane Settings

Registry entries can carry per-goal `control_plane` policy alongside quota.
These settings are the control surface for behavior that would otherwise be
tempting to encode in a heartbeat prompt.

Current self-repair settings:

- `control_plane.self_repair.enabled`: default `false`.
- `control_plane.self_repair.allow_health_blocker_repair`: when enabled, a goal
  may spend one bounded turn repairing LoopX health or contract blockers
  that prevent normal delivery.
- `control_plane.self_repair.allow_waiting_projection_repair`: when enabled, a
  goal in `state=waiting` with no concrete waiting owner but with a current
  action or agent backlog may spend one bounded turn repairing the projection or
  writing back the concrete blocker.

Self-repair is deliberately opt-in per goal. A short heartbeat can read the
machine contract from `quota should-run`, but ordinary goals without this
registry policy stay in their existing skip, waiting, or health-blocked lanes.
Independent of opt-in health/projection repair, status may project an
`autonomous_replan_obligation` when active state or public run history shows 2
consecutive stalled monitor/no-progress turns. In that case
`execution_obligation.kind=autonomous_replan_required` and
`must_attempt_work=true`; launchers should run a bounded replan slice before
another quiet no-op, then spend only after validated writeback.

## Allocation Contract

`quota plan` reports an advisory next automatic turn. It does not grant
permission, clear an operator gate, record human reward, or authorize a project
agent to run work that would otherwise be blocked.

The allocation rule is intentionally small:

1. derive quota groups from the same status payload used by `loopx
   status`;
2. keep `blocked_health`, `operator_gate`, `focus_wait`, `waiting`,
   `throttled`, and `paused` goals in their own lanes, even when they have a
   high `quota.compute`;
3. only goals with `state=eligible` enter the eligible lane;
4. sort eligible goals by effective `quota.compute`, highest first;
5. set `summary.next_automatic_turn` to the first eligible goal, or `none` when
   the eligible lane is empty.

Automations and controllers should treat `next_automatic_turn` as a scheduling
hint, then ask `quota should-run --goal-id <goal-id>` immediately before
spending compute. If the guard returns `should_run=false`, the executor should
skip the blocked delivery work and follow the reported health, operator,
evidence, focus-wait, pause, or throttle reason. When `state=operator_gate` also
returns `safe_bypass_allowed=true`, the target heartbeat may do one bounded
read-only steering or analysis step that does not depend on that gate, but it
must not execute `agent_command`, adapter work, write-control, production
actions, or the gated path.
When `state=focus_wait` is caused by a connected-delivery outcome floor and the
payload returns `safe_bypass_kind=outcome_floor_recovery`, the goal is not free
to resume ordinary delivery. It may spend one bounded recovery turn only to
produce the required ranker/cross-domain evidence artifact named by
`quota.must_advance`, or to write back the concrete blocker that prevents that
artifact. Surface-only summary/queue/contract propagation and synthetic-only
test chains remain blocked.
When `control_plane.self_repair.enabled=true` and the selected goal is stalled
by a repairable control-plane condition, `quota should-run` may instead return
`decision=self_repair`, `self_repair_allowed=true`, `stall_self_repair`, and an
`effective_action` such as `control_plane_health_repair` or
`control_plane_projection_repair`. This makes "repair the control plane" a
machine-readable bounded action for the selected goal, not a prompt-specific
branch. Spend is allowed only after validation and durable writeback.

Delivery outcome is a structured enum consumed by quota, not a suffix inferred
from `classification`. New writes should use one of:

| Value | Quota meaning |
| --- | --- |
| `surface_only` | Useful surface work, but not primary result evidence; may trigger follow-through when an advancement todo remains. |
| `outcome_gap` | The run exposed a concrete blocker or missing outcome; follow-up should advance the result or write a precise blocker. |
| `outcome_progress` | Accountable delivery progress that may be eligible for spend after validation/writeback. |
| `primary_goal_outcome` | The selected stage's primary outcome is complete; it satisfies outcome-floor recovery. |

`classification` remains a human/history label. Quota may use old
execution-profile hints only as a compatibility fallback for historical runs;
new control-plane decisions should be driven by the enum above.

An `outcome_gap` does not become delivery progress. It may settle and spend one
exact Todo-bound Turn only when the same writeback includes a
`typed_progress_observation_v0` with `result_class=blocked`, the matching
`work_item_id`, a stable `blocker_id`, and a non-empty array of stable
`evidence_ids`. Missing schemas, prose-only blockers, malformed evidence, and
Todo identity mismatches remain fail-closed.

`quota should-run` also separates long-running observation from work that should
advance the selected goal. When the selected goal's current projection is a
dependency-only observation, the payload includes `work_lane_contract` with
`lane=continuous_monitor`. If open agent todos remain, schema
`work_lane_contract_v1` now classifies todo items as
`task_class=advancement_task` or `task_class=continuous_monitor`. Agents should
register executable work with `loopx todo add --task-class
advancement_task --action-kind <token>` instead of depending on project-specific
phrases in the automation prompt. The guard treats explicit `task_class` as
authoritative, uses recognized generic `action_kind` tokens as the next best
signal, and falls back to conservative text classification only for older
checkboxes. It sets
`next_lane=advancement_task`,
`obligation=advance_unless_material_monitor_transition`,
`must_attempt_work=true`, and reason codes such as `dependency_observation` and
`open_agent_todo` only when at least one open todo is advancement-class work.
If all visible open todos are monitor-class and no open todo is hidden, the
same contract uses `obligation=quiet_until_material_monitor_transition` and
`must_attempt_work=false`. Hidden open todos are treated as advancement work to
avoid false quiet no-ops from a truncated top-N todo projection.
Open agent todos that explicitly say not to run/launch until owner evidence,
credentials, substrate proof, or another prerequisite is present are
monitor-class blockers rather than executable advancement work; with an open
user todo they should keep that todo visible in `user_todo_summary` without
turning an unchanged eligible monitor poll into a blocker-push notification.
If the selected goal's `next_action`/`recommended_action` explicitly points to
an executable chain, such as collecting or aggregating repeats and then
rebuilding labels, rerunning a scorer, or validating an eval gate,
monitor-only todos are not allowed to make the goal quiet. The contract instead uses
`obligation=materialize_advancement_todo_or_blocker` and
`must_attempt_work=true`, so the worker must either materialize the concrete
advancement todo or write the blocker that prevents it.
`heartbeat_recommendation` should then say `follow_work_lane_contract` or
`monitor_quiet_until_material_transition` instead of encoding another
project-specific branch. An executor may still record one material
dependency-state transition when it changes the selected goal decision, but
unchanged monitor polls are quiet no-spend checks with `should_run=false` and
`effective_action=monitor_quiet_skip`. This keeps monitoring useful without
letting it consume every eligible turn, and it keeps the hard routing rule in
one small machine contract.
When the current agent lane has only monitor work and no current, unclaimed, or
other-agent advancement frontier, a valid future `next_due_at` is an explicit
wait state: quota uses the typed `future_monitor_wait` rule, returns
`monitor_quiet_skip`, and schedules the next bounded wake without requiring a
synthetic replan. A due monitor remains executable through
`due_monitor_execution`. Missing or invalid schedules, no-change streaks,
vision/succession gaps, user gates, and real blockers still enter their
higher-priority repair or replan rules. Projected ACKs from a different agent
lane remain diagnostic only and cannot clear a current-lane obligation.

Executable todos can also declare explicit write-scope requirements through
todo metadata, for example `required_write_scopes=runner%2F%2A%2A` or the CLI
flag `loopx todo add --required-write-scope runner/**`. Before normal
delivery, `quota should-run` compares the first executable advancement todo's
`required_write_scopes` with `goal_boundary.write_scope`. If the current
boundary does not cover the selected scope, the guard keeps `should_run=true`
for a bounded repair turn but sets `normal_delivery_allowed=false`,
`effective_action=boundary_projection_repair`, and
`blocked_action_scope=boundary_projection`. The worker must repair the
checkpointed boundary projection, rewrite the todo inside the current boundary,
or write a concrete user/controller gate before attempting the write.

Executable todos can also declare environment capability requirements through
todo metadata, for example `required_capabilities=shell%2Cbenchmark_runner` or
the CLI flag `loopx todo add --required-capability benchmark_runner`.
This is not a global agent profile and not a permission system. It is a
per-todo execution preflight so the guard can distinguish "quota is available"
from "this step can actually run in the current environment."
Use `required_capabilities` for prerequisites, not for the capability the todo
is intended to create. A todo that repairs, develops, materializes, or
parity-checks a bridge capability should declare that output side with
`target_capabilities`. Target capabilities are projected for visibility and
repair-mode routing, but they are not hard execution gates.

`quota should-run` compares the visible executable advancement queue with the
current launcher capabilities. Basic local capabilities such as `shell`,
`filesystem_read`, and `filesystem_write` are assumed by default; launchers can
declare observed capabilities with `--available-capability`, for example:

```bash
loopx --format json quota should-run \
  --goal-id <goal-id> --agent-id <registered-agent-id> \
  --available-capability benchmark_runner
```

A live `quota should-run` or executing `turn run-once` automatically remembers,
through the admitted turn-start capability hook, these five runtime observations
for the registered Agent on this host:
`network`, `benchmark_runner`, `external_evidence_poll`, `worker_bridge`, and
`cli_bridge`. Later decisions, including `quota spend-slot` and `monitor-poll`
rechecks, read that Agent's observations without repeating flags. Goal/project
`available_capabilities` declarations are inherited dynamically; Agent observations
never write back into Goal configuration or propagate to peer Agents.
The hook declares only its Agent-private write scope, returns no private payload,
and fails in isolation; quota selection itself performs no provider or memory write.

The scope is the resolved runtime root, registry path, Goal id and registered
Agent id. Separate hosts/runtimes or registries do not inherit these observations.
A reused Agent id in the same scope refers to the same runtime declaration;
use distinct Agent identities for environments with different tools. Observations
are caller declarations, not independently verified probe receipts. They persist
until corrected or forgotten; omission of a flag does not revoke them.

```bash
# Read current Agent observations and inherited/effective declarations.
loopx --format json agent-capabilities --goal-id <goal-id> --agent-id <agent-id>

# After a real task-facing availability failure, override Goal inheritance locally.
loopx agent-capabilities --goal-id <goal-id> --agent-id <agent-id> \
  --unavailable-capability network --execute

# After successful recovery, record availability again.
loopx agent-capabilities --goal-id <goal-id> --agent-id <agent-id> \
  --available-capability network --execute

# Remove the local observation; Goal inheritance applies again.
loopx agent-capabilities --goal-id <goal-id> --agent-id <agent-id> \
  --forget-capability network --execute
```

Omit `--execute` on the management command to preview a correction without writing.
`turn plan` and non-executing `turn run-once` also remain read-only. Unknown or
permission/enablement capability names retain invocation-only semantics, including
`credentials`, `production_access` and optional feature activation tokens. A local
negative overrides an inherited runtime declaration; a fresh explicit positive
observation replaces that negative. Removing a Goal declaration does not erase an
Agent's independently recorded observation: clear or correct both scopes when needed.
The readback exposes Goal, Agent, invocation, unavailable and effective sources.

This intentionally changes the former session-only default for those five runtime
capabilities. Generated follow-up commands still carry runtime declarations for
compatibility. No credentials, production grant, optional capability enablement,
shared-authority head, lease or provider selection is created by this memory.

The resulting `capability_gate` is a read-only projection:

```json
{
  "schema_version": "capability_gate_v0",
  "action": "run",
  "required": ["shell", "filesystem_write"],
  "missing": [],
  "decision_owner": "agent",
  "selection_policy": "agent_steering_audit_over_runnable_candidates",
  "runnable_candidates": [
    {"todo_id": "todo_docs"}
  ],
  "blocked_candidates": [
    {
      "todo_id": "todo_eval",
      "required_capabilities": ["shell", "benchmark_runner"],
      "missing_capabilities": ["benchmark_runner"]
    }
  ]
}
```

Multiple P0/P1 items are handled as an ordered queue, not as a single selected
todo. The guard scans visible executable candidates in projection order and
projects which candidates are actually runnable in `runnable_candidates`; the
agent then chooses which runnable item to advance during its steering audit. A
runnable P0 remains visible before any P1 fallback, but LoopX does not
turn that ordering into an automatic final choice. Blocked higher-priority
candidates stay visible in `blocked_candidates`. If every visible executable
candidate is missing a capability, the gate returns
`action=repair_bridge` for repairable local bridges such as
`benchmark_runner`/`external_evidence_poll` and runtime provisioning such as
`network`, `action=ask_owner` for owner-held capabilities such as
`credentials`/`production_access`, or
`action=skip` for unsupported capability classes.

When more than one same-agent action is admitted, `quota should-run` makes that
choice legible through `action_portfolio.suggested_actions`: one ordered
recommendation plus at most two alternatives. These suggestions are bounded for
readability and explicitly non-exhaustive; the recommendation is a default, not
a binding or permission list. The first response sets
`interaction_contract.cli_channel.selection_required=true`, returns one typed
`selection_command.command_args_template`, a shared bound `route_prefix`, compact
`candidate_discovery_args`, and creates an identity-less heartbeat receipt.
After its steering audit, the agent may use the discovery command to inspect the
authoritative open agent queue, then renders the template with any currently
authoritative, same-agent, capability-ready Todo, including one not shown in the
bounded suggestions. That request is only a pending selection: the second guard
re-runs current lane arbitration and eligibility checks before upgrading the
receipt. A newly due hard-priority monitor, blocking user gate, or other current
preemption defers the request and leaves the receipt identity-less. Delivery and
quota spend remain disabled until binding succeeds. Deferred/rejected selections
return the TS-owned `recovery_action=reenter_guard_without_selection`: execute
the single command in `interaction_contract.cli_channel.next_cli_actions`, with
the same turn id and no Todo/replan argument. That guard either binds the current
hard lane or returns a refreshed portfolio. No settlement plan is exposed before
reentry, and a previously bound receipt cannot be retargeted. `recommended_action`
retains the human-readable rejection or deferral guidance; the executable recovery
command lives in `next_cli_actions` and `agent_channel.primary_action`.
A single-candidate response
keeps the direct execution path and does not add an extra selection round trip.

When the selected Todo has meaningful strategic context, the same default
response may include `planning_horizon.schema_version=
quota_planning_horizon_v0`. It carries at most five selected/related/runnable
or higher-priority waiting Todos, eight typed lineage/resume/route relations,
two goal acceptance gaps, and explicit completeness counters. The horizon is a
read-only steering input: `selection_contract.horizon_changes_selection=false`
and action authority remains `selected_todo` plus `action_portfolio`. If the
horizon exposes a better runnable item, the agent still uses the existing
candidate discovery and explicit selection re-entry. If it is truncated, the
agent follows its Todo-detail or task-graph cold-path reference before treating
the visible slice as exhaustive. See
[`quota_planning_horizon_v0`](reference/protocols/quota-planning-horizon-v0.md).

Portfolio and horizon do not independently reconstruct the Todo queue. Python
selects canonical Todo domain-state rows and TypeScript normalizes them once as
`todo_planning_inventory_v0`. The inventory keeps `planning_state` independent
from `claim_state`, so an unclaimed runnable item explicitly requires a claim
before work. `--include-detail agent-todos` exposes the larger bounded
planning-only lens and points its item details back to `agent_todo_summary`;
the horizon's `full_todo_list` command reads the complete open agent queue.
Overflow is reported through completeness counters rather than making the
read-only quota guard fail. Task graph uses the same canonical Todo rows, while
extending them with terminal predecessor/evidence rows and remaining an
agent-neutral read model with separate gate/evidence context.

Blocked candidates retain typed `resolution_bindings` even when another todo is
runnable. Each binding names the capability, its resolution owner, and the
exact `blocked_todo_ids`. The interaction contract turns an owner-held binding
into an idempotent scoped `user_gate` write linked by `unblocks_todo_id`, while
an agent-repairable binding becomes an idempotent advancement todo with the
capability in `target_capabilities`. The user gate does not block unrelated
runnable todos. Capability-gate projection does not write Todos; the agent or host
executes the projected todo write before normal writeback. Live should-run may
record the scoped runtime observations described above.

Completing a repair or owner todo is not proof that the runtime capability is
available. The current host must verify the real callsite before declaring the
capability through `--available-capability`. Supported observations are remembered
for that Agent; after a later real failure, mark the capability unavailable rather
than repeating a stale positive flag. This memory never grants operation authority.

Runtime capability absence is not permission authority. A missing `network`
declaration therefore remains in the agent repair lane: the agent should
observe or repair the launcher/runtime bridge, truthfully pass
`--available-capability network` once verified, or write a concrete runtime
blocker. LoopX must not turn that condition into a user approval request.

When a missing repairable bridge is itself the target of the todo, for example
`required_capabilities=shell` plus `target_capabilities=benchmark_runner`, the
candidate remains in `runnable_candidates` with `capability_repair_mode=true`,
`capability_action=repair_bridge`, and candidate-local
`missing_target_capabilities`. This avoids a circular gate where a todo cannot
develop `benchmark_runner` because it does not already have `benchmark_runner`.

Multi-agent handoffs use the same ordering as all other agent todos. Review is
an `action_kind`, not a scheduling rank. `excluded_agents` can prevent named
peers from claiming or executing an otherwise ordinary handoff, while
`unblocks_todo_id` records dependency lineage. Active-next alignment, claims,
priority, capability gates, write scopes, user gates, and validation continue
to determine the executable action.

Deferred todo visibility is a separate gate-resume lane, not executable
backlog and not part of the no-candidate quiet-wait semantics. Status/quota may
expose up to eight sorted `deferred_items` and up to eight ready
`deferred_resume_candidates` after the sorted open todo lanes. In agent-scoped
`quota should-run --agent-id <peer>`, all deferred items may remain
visible for diagnosis, but only ready candidates claimed by the current agent
or left unclaimed can wake that peer. If such a candidate exists and no
open current-agent/unclaimed advancement todo exists, quota returns
`effective_action=successor_replan_required`, `normal_delivery_allowed=false`,
and `execution_obligation.contract = deferred_resume_projection`. The worker
must reopen, supersede, or record a public-safe no-follow-up rationale before
ordinary delivery work. Only when no ready current-agent/unclaimed deferred
resume exists should agent-scoped quota fall through to `agent_scope_wait`,
`reassignment_required`, or `scope_exhausted`.

Priority remains authoritative across the resume boundary. When a ready
current-agent or unclaimed deferred successor has strictly higher priority than
the selected open advancement todo, quota also returns
`successor_replan_required` before lower-priority delivery. The deferred item
does not become executable in place: `selected_todo` points to the deferred
successor so the worker can reopen, supersede, or close it explicitly, while
`goal_frontier_projection.deferred_successors.top_ready_todo_id` reports the
same lifecycle target. An equal-priority open todo remains executable, avoiding
unnecessary lifecycle churn within a priority bucket.

An unrelated `user_action` may add a visible `NOTIFY` notice to this turn, but
it cannot replace the selected lifecycle obligation or turn it into a user
wait. `delivery_allowed=false` here forbids ordinary material delivery; it does
not cancel `execution_obligation.must_attempt_work=true`. The final interaction
contract therefore keeps `mode=successor_replan_required`, projects the notice
as `non_blocking=true`, retains the Todo lifecycle CLI action, and leaves the
scheduler on active-work cadence. An explicit `user_gate` still takes
precedence when its decision scope covers the selected lifecycle action.

Open todos with `resume_when` use the same readiness signal before they enter
ordinary execution lanes. Until `resume_ready=true`, quota must not include the
todo in `capability_gate.runnable_candidates` or `agent_lane_next_action`; it
may continue with a lower-priority executable fallback when the interaction
contract allows safe scoped fallback work. The action portfolio keeps a
higher-priority typed wait visible as `availability_reason=resume_condition_pending`
while making the runnable fallback and its bounded continuation context the
default model-facing action.

For `resume_at:<timezone-aware-rfc3339-timestamp>`, the active-state read takes
one runtime-clock snapshot and passes it through the shared Todo reducer. A
future timestamp therefore produces the same `agent_scope_wait` or fallback
selection in CLI and heartbeat paths. Once due, quota selects the deferred Todo
through `successor_replan_required`; the managed Turn sees the same stable
resume receipt and must perform the normal explicit lifecycle update. Repeated
ticks do not create additional material-change generations or receipts.

If an active per-agent vision has no other selectable advancement and its
existing current-agent or unclaimed successor is blocked by an exact supported
`resume_when`, quota projects `vision_wait_state.state=waiting` and
`agent_scope_wait` instead of deriving another autonomous vision replan. The
wait payload preserves the todo id, condition, and automatic-resume contract.
Once `resume_ready=true`, the wait disappears and ordinary open-todo or
deferred-successor selection runs again. Missing vision checkpoints, closed
stage succession, unsupported conditions, and continuous-monitor-as-gate
repairs remain fail-closed and are not hidden by this wait path.

For completed handoff gates, record that rationale structurally as
`no_followup=true`; status projects the gate as `cleared_no_followup` instead
of waking the blocked agent with `successor_replan_required`.

External-evidence waits have an additional CLI-level observation contract. When
the selected goal is `state=waiting`, `waiting_on=external_evidence`, and its
current lane is a continuous monitor, or when the active state says a
long-running external worker was launched and the current action is to poll a
compact result/marker, `quota should-run` returns
`external_evidence_observation.schema_version =
external_evidence_observation_obligation_v0`. The guard keeps
`should_run=false` so ordinary delivery remains blocked, but sets
`effective_action=external_evidence_observe` and
`execution_obligation.kind=external_evidence_observation_required` with
`must_attempt_work=true`. The executor must verify a read-only observable
handle such as a thread id, automation id, job id, lock/result marker, or
compact writeback path before treating the poll as unchanged evidence. If that
handle is missing, stale, or never launched, write back a compact blocker or
launch-readiness fault instead of returning a quiet no-op.

For autonomous heartbeats, unchanged monitor polls can be recorded as
no-spend stall evidence with:

```bash
loopx --registry "$HOME/.codex/loopx/registry.global.json" quota monitor-poll --goal-id <GOAL_ID> --source heartbeat --execute
```

`quota monitor-poll` is valid when the current guard is a quiet monitor skip,
an external-evidence observation, or a due `continuous_monitor` todo selected
by `work_lane_contract.obligation=attempt_due_monitor`. For due monitor todos,
pass `--todo-id` or `--target-key` plus a public `--result-hash`; unchanged
polls update `last_checked_at`, `next_due_at`, and
`consecutive_no_change` without appending `quota_slot_spent`:

```bash
loopx quota monitor-poll --goal-id <GOAL_ID> \
  --todo-id <TODO_ID> --result-hash <HASH> --execute
```

When a poll sees a material transition, add `--material-change` and optionally
`--next-agent-todo` or `--next-user-todo` so the monitor produces a concrete
follow-up instead of staying as an opaque watch. A user follow-up must declare
`--next-user-task-class user_gate|user_action` explicitly: use `user_gate` for
a blocking owner decision and `user_action` for a visible reminder that must
not block the bound agent lane. Omitting the task class fails before writeback.
The monitor remains an observe-only `continuous_monitor`; `--next-agent-todo`
creates a separate runnable `advancement_task`. For the corresponding
generation-fenced waiting-Todo recipe, see
[Project Agent Todo Contract](project-agent-todo-contract.md#machine-readable-resume-conditions):

```bash
loopx quota monitor-poll --goal-id <GOAL_ID> \
  --target-key <TARGET_KEY> --result-hash <HASH> --material-change \
  --next-user-todo "<PUBLIC_SAFE_REVIEW_REMINDER>" \
  --next-user-task-class user_action \
  --next-agent-todo "<PUBLIC_SAFE_FOLLOW_UP>" --execute
```

The command appends a `quota_monitor_poll` run record, does not mutate the
registry, and does not append `quota_slot_spent`. The run includes
`quota_monitor_target_v0`, a compact hash of the public monitor identity.
The versioned `quota.monitor_poll.commit` TypeScript transaction owns admission
revalidation, that target/event construction, same-effect replay, index CAS,
and the repairable JSON/Markdown/index write set. Polls without a Todo target
cross the managed runtime once. A Todo target uses a fail-closed provider
preflight followed by one final commit; the retained Todo writer persists the
monitor effect identity so a crash retry cannot advance counters twice or let
an older observation overwrite a newer one.

Six consecutive unchanged executions of a concrete due/external monitor Todo or
target with the same target feed
`autonomous_replan_obligation` as
`dead_monitor_repeat`, so the next independent `quota should-run` may flip to
`autonomous_replan_required` /
`execution_obligation.must_attempt_work=true`; the executor should then record a
watch-lane expiry, concrete blocker, todo supersede, or successor runnable todo
instead of another quiet skip. Automatic `quota should-run` heartbeat liveness
receipts have no concrete executed-monitor identity and do not count toward
this threshold; distinct heartbeat turn ids are idempotency evidence, not
monitor execution evidence.

After that bounded replan slice is acknowledged by a compact state run carrying
`autonomous_replan_ack_v0` and `repair_delta_contract_v0` with
`delta_present=true`, later empty monitor polls for the same acknowledged wait
do not repeatedly retrigger replan. A plain classification such as
`monitor_poll_autonomous_replan_recorded_v0` is not enough. Accounting-only
runs such as `delivery_completion_spend_accounted_v0` remain neutral liveness
records; they do not close a replan obligation by themselves. Empty polls stay
no-spend liveness checks until a material monitor transition, regression, or
concrete blocker appears.

The same guard exposes `automation_liveness`. For
`monitor_quiet_skip`, it must say `automation_action=keep_active_quiet`,
`keep_active=true`, and `pause_allowed=false`: unchanged monitor-only polls are
not a reason to cancel recurring automation. Pausing/deleting is reserved for
either a read-only terminal state derived by LoopX from complete, valid todo
sources, no-follow-up closure evidence, and an empty normalized frontier, or a
bounded self-repair/replan path that is itself stuck for two more eligible
turns. The terminal case projects
`automation_action=stop_terminal_no_followup`, `keep_active=false`, and
`pause_allowed=true`; raw status/attention fields are not terminal authority.
Missing or malformed sources and any open todo, monitor, successor, acceptance
gap, autonomy blocker, or replan obligation fail closed. This keeps recurring
controllers alive during ordinary waits while honoring an explicit completed
goal shutdown without another quota-spending turn.

An individual registered peer can instead be put in `monitor_only` work mode:

```bash
loopx configure-goal --goal-id <goal-id> \
  --agent-work-mode <agent-id>=monitor_only --execute
```

This suppresses that peer's advancement, autonomous replan, repair, fallback,
and new-topic lanes while preserving due `continuous_monitor` todos and verified
direct operator replies. A future monitor stays quiet; a committed monitor poll
is the Turn's no-spend closeout whether unchanged or material. A material poll
may atomically release an independent advancement successor, whose later
delivery has its own quota identity. Same-Turn readback and prior-Turn recovery
accept the same exact committed effect, including the shipped turn-only receipt;
a preview or a row with missing or mismatched commit metadata cannot close the
Turn. Other peers remain active. Use
`--clear-agent-work-mode <agent-id>` (or set `=active`) to resume ordinary
advancement.

The read model exposes that derivation as
`goal_frontier_projection.terminal_state={kind:no_followup, derived:true,
source:validated_goal_closure}` plus
`source_completeness.user_todos=valid` and
`source_completeness.agent_todos=valid`. Producers derive the source proof and
closure intent from structured todo items; callers cannot authorize shutdown by
writing `attention_queue.status` or registry attention text.

`automation_liveness` deliberately does not set the polling cadence. The guard
also exposes `scheduler_hint`, which is the host-runtime scheduling contract:
Codex App automations should progressively back off toward the recommended
interval/max during long waits, while Codex CLI TUI and Claude Code loops should
run one final `quota should-run` replan check after their unchanged-poll limit
and exit or stop only if the guard is still unchanged. Cadence changes, final
checks, and self-stop turns never spend quota; only validated delivery or
allowed writeback does.

Scheduler ownership is explicit. A bare `quota should-run` can still expose the
quota decision, but its `scheduler_hint` fails closed with
`repair_scheduler_execution_context`; it never assumes Codex App. Generated
Codex App heartbeats pass the explicit `codex_app_heartbeat` profile; generated
commands use its compact alias `--codex-app`. Other hosts
pass the typed `--host-surface`, `--scheduler-owner`, and `--execution-mode`
triple that describes the runtime which will consume the hint.

## Compute States

Recommended compact states:

- `eligible`: the goal can consume the next automatic agent turn.
- `focus_wait`: the goal is Codex-addressable in principle, but the current
  delivery focus is intentionally paused by a continuation boundary or missing
  novelty, owner evidence, external eval, or clean baseline.
- `throttled`: the goal is healthy but has spent its current compute quota.
- `waiting`: the goal is waiting on external evidence or a target controller,
  so compute should not be spent yet.
- `operator_gate`: the goal needs a human decision before more compute is
  useful.
- `paused`: compute quota is `0` or the operator paused the goal.
- `blocked_health`: the goal must fix registry, contract, or boundary issues
  before more work runs.

These are product states for the dashboard. Adapter classifications remain
drill-down details.

## Dashboard Implication

The dashboard should show compute quota as a compact control surface:

- quota chips: `1.0`, `0.5`, `0.3`, `0`;
- spent/allowed minute-slots for the current window;
- a visible explicit-override marker only when `allowed_slots` is manually set
  away from the window-derived default;
- next eligible time when throttled;
- simple operator actions: set quota, pause, resume, or grant a temporary
  burst;
- next-turn view sorted by "should receive the next automatic turn."

The first screen should make it obvious why a project is quiet:

- it is waiting on evidence;
- it is gated by the operator;
- it is in focus wait because the current lane needs novelty, evidence, or a
  clean baseline before another delivery turn;
- it is throttled by compute quota;
- it is paused;
- or it is eligible and should run next.

## CLI Surface

The first read-only or preview commands are:

```bash
loopx quota status
loopx quota plan
loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota should-run --goal-id <goal-id> --runtime-profile codex_app_heartbeat
loopx --registry "$HOME/.codex/loopx/registry.global.json" quota spend-slot --goal-id <goal-id> --slots 1
loopx --registry "$HOME/.codex/loopx/registry.global.json" quota spend-slot --goal-id <goal-id> --slots 1 --execute
```

These commands reuse the status contract, including contract health, global
registry health, attention queue, run history, and derived quota state. They do
not mutate registry, runtime history, reward overlays, or operator gates.
Project heartbeat prompts should use the shared global registry for
`should-run` and `spend-slot` so they see the same operator gates, user todos,
and quota state as the dashboard. Project-local state writes such as
`refresh-state` and `todo add` still happen in the source project and sync their
public-safe projection back to the global registry.

`quota status` is the broad inventory for agents: it shows every registered
goal under `blocked_health`, `operator_gate`, `focus_wait`, `eligible`,
`waiting`, `throttled`, or `paused`.

`quota plan` is the next-turn view for automations: it hides empty groups in
markdown output and highlights `next_automatic_turn` when a goal is eligible.
If that value is `none`, the automation should skip delivery compute and follow
the displayed gate, evidence, or health reason.

`quota should-run` is the per-goal guard for heartbeat jobs. It returns a small
JSON or Markdown decision:

Its health gate is also per-goal: global contract errors and errors owned by
the selected goal fail closed, while errors owned by other goals remain visible
in broad status inventory without blocking this decision. Error ownership comes
from structured contract diagnostics, never from parsing a goal-id prefix out
of an error string.

```json
{
  "goal_id": "project-main-control",
  "decision": "skip",
  "should_run": false,
  "state": "operator_gate",
  "reason": "operator gate blocks gated delivery; safe non-gated steering may continue",
  "blocked_action_scope": "gated_delivery",
  "safe_bypass_allowed": true,
  "heartbeat_recommendation": {
    "source": "quota.should-run",
    "recommended_mode": "ask_operator_gate",
    "notify": "NOTIFY",
    "spend_policy": "do not append quota spend while asking the operator gate"
  },
  "scheduler_hint": {
    "schema_version": "scheduler_hint_v0",
    "action": "backoff_waiting_for_user",
    "codex_app": {
      "recommended_interval_minutes": 30,
      "example_progression_minutes": [30, 60]
    },
    "unchanged_poll": {
      "limits": {
        "local_scheduler": 3,
        "codex_cli_tui": 3,
        "codex_app_ssh_goal": 3,
        "claude_code_loop": 3
      },
      "after_limits": {
        "local_scheduler": "stop_tick_loop",
        "codex_cli_tui": "update_goal_blocked_keep_loopx_active",
        "codex_app_ssh_goal": "update_goal_blocked_keep_loopx_active",
        "claude_code_loop": "stop_loop"
      },
      "final_quota_replan_check_enabled": true,
      "final_quota_replan_check_action": "rerun_quota_should_run_once",
      "spend_policy": "no quota spend for final replan check or loop stop"
    },
    "detail_ref": {
      "schema_version": "scheduler_hint_detail_v0",
      "omitted_by_default": true,
      "execution_required": false,
      "request": "loopx quota should-run --include-detail scheduler",
      "hot_path_runtime_fields": [
        "app_automation",
        "unchanged_poll",
        "reset_policy"
      ],
      "contains": [
        "local_scheduler",
        "codex_cli_tui",
        "codex_app_ssh_goal",
        "claude_code_loop",
        "final_quota_replan_check",
        "reset_policy_detail",
        "stateful_backoff_detail"
      ]
    },
    "reset_policy": {
      "reset_token": "0123456789abcdef",
      "host_state_key": "scheduler_hint.reset_policy.reset_token",
      "app_automation_initial_interval_minutes": 30,
      "app_automation_initial_rrule": "FREQ=MINUTELY;INTERVAL=30",
      "identity_signature": "123456789abc"
    }
  },
  "operator_question": "是否同意 project-main-control 先做 read-only map dry-run？",
  "gate_prompt": "请用户/控制器确认当前 gate：..."
}
```

Only `state=eligible`, outcome-floor recovery safe-bypass, or registry-enabled
control-plane self-repair returns `should_run=true`. Known goals that are
gated, in focus wait, waiting, throttled, paused, or health-blocked return
`ok=true` only when the status export itself is healthy, but otherwise return
`should_run=false`.
`safe_bypass_allowed=true` is not permission to clear the gate; it only says the
agent can spend a bounded turn on independent read-only steering/analysis.
For `state=operator_gate`, `quota should-run` should also surface
`gate_prompt`, `operator_question`, `next_handoff_condition`, `missing_gates`,
`user_todo_summary`, or `agent_todo_summary` when those fields are available.
A heartbeat should use that prompt to ask the user or target controller the
concrete gate question instead of silently skipping, unless the same unresolved
gate was already asked in the recent visible thread. If
`user_todo_summary.open_count > 0`, existing open user todos are themselves
user-visible action; do not report "no new user action" while those todos
remain open. This also applies after a bounded safe-bypass step: the compact
report must still list the existing open user todos instead of saying that
there is no user action. If `agent_todo_summary.open_count > 0`, the agent
should use that summary as its next safe follow-up checklist instead of mining
chat history or an overlong `Next Action`.

For an agent-scoped quota request, `user_todo_summary.open_count` counts only
user todos bound to that agent plus explicit `goal_bound=true` items. Todos
bound to another agent remain in
`other_agent_bound_user_action_items` for diagnosis, but they must not preserve
an inherited `operator_gate`, enter `interaction_contract.user_channel`, or
trigger a heartbeat notification. In multi-agent goals, todo authoring must use
`--bound-agent <registered-agent>` (or `--agent-id` when the author and bound
lane are the same) or `--goal-bound`; `claimed_by` is not a user-todo routing
field.

For `state=focus_wait`, `state=waiting`, or
`waiting_on=external_evidence`, an open user todo can be the smallest unlock
for a quiet project. In that case, `quota should-run` should set
`notify_user_on_open_todo=true` and include `open_todo_notify_reason`. The
target heartbeat should return a compact `NOTIFY` listing at most three open
user todos and the expected reply (`done`, `defer/not now`, or new evidence
link/date/conclusion), while skipping delivery work and quota spend for that
blocker-push turn. If quota also sets
`open_todo_notification_policy=repeat_until_resolved`, repeat that notification
until the todo is done, deferred, or replaced. If a failed host cadence update
leaves a tighter poll, `user_gate_notification_cooldown_v0` keeps the gate open
but suppresses duplicate notices outside a bounded reminder window. Otherwise,
blocker-push cases may still be de-duplicated when the same blocker was already
surfaced recently. Eligible monitor-only polls with no material transition keep
the open user todo visible in `user_todo_summary`, but do not force a repeated
notification, make the turn a user-action gate, or leave the top-level
`should_run` set for an otherwise quiet no-op.

Cooldown suppression has precedence over both blocking asks and non-blocking
`user_action` notices: the final `interaction_contract.user_channel` must be
`action_required=false, notify=DONT_NOTIFY` and must not retain an action list.
Goal Channel delivery adds a sink-local replay fence keyed by the gate identity,
its material state generation, and (only when due) the explicit reminder-window
generation. Copy changes, recommendation churn, and provider readback misses do
not create a new send generation; a material Todo transition or a new explicit
reminder window does.

Monitor catch-up is also bounded across runnable lanes. After two consecutive
unchanged monitor-only work turns, `monitor_debt_arbitration_v0` prefers a
same- or higher-priority advancement todo over another overdue monitor,
including inside scoped user-gate fallback selection. Scheduler/accounting and
state-refresh rows do not break the streak; real advancement or a material
monitor transition does. Direct Lark inbox `reply_due` work remains a stronger
preemption and is never delayed by this fairness backoff.

For every registered goal, `quota should-run` also includes a `todo_write_hint`
so agent executors know to write newly discovered user/owner work with
`loopx todo add --role user --task-class user_gate|user_action` instead of
hiding it in `Next Action`, review docs, or chat. Multi-agent writes must also
name `--bound-agent <registered-agent>` or `--goal-bound`; agent-scoped
`user_gate` writes bind to the same agent named by `--blocks-agent`.
When available, `quota should-run` also keeps next-action signals separate:
`active_state_next_action` is the durable `## Next Action`,
`latest_run_recommended_action` is the latest non-agent-lane run's
recommendation, and `agent_lane_next_action` is the current `--agent-id`
slice. Agent-scoped payloads may also include
`goal_route_hint.schema_version=goal_route_hint_v0`, a compact read-only
synthesis that says whether the current lane should run, claim, wait, or
reassign while preserving `## Next Action` as durable goal-level guidance. It
is an advisory routing hint, not a writeback instruction and not a replacement
for `agent_todo_summary`.
When projected, `goal_frontier_projection.schema_version =
goal_frontier_projection_v0` is the per-goal progress/frontier view used before
lane-local quiet or wait decisions. Its `autonomous_replan_decision` says that a
required replan must be selected independently of `monitor_quiet_skip` or
`agent_scope_wait`; the policy lives in `loopx.control_plane.goals.goal_frontier`, while
quota only wires the selected mode into `interaction_contract`.
If the active-state and latest-run actions differ,
`next_action_projection_warning` asks the executor to explicitly write back the
intended durable route with a primary goal-scope `refresh-state --next-action`
or keep treating the signals as distinct.
`refresh-state` records `recommended_action_source` so hosts can tell whether a
run recommendation came from an explicit argument, durable `## Next Action`, an
Agent Todo compatibility fallback, or the generic default. Dispatch still comes
from `agent_lane_next_action` / todo projection, not from shared `## Next Action`
alone.
When `agent_lane_next_action.selected_by=unclaimed_todo`, the payload marks
`claim_required_before_work=true`; executors must claim the todo before editing
or launching delivery work.
For goals with `coordination.registered_agents`, `quota should-run` accepts an
optional `--agent-id <registered-agent>`. Identity-aware heartbeat prompts pass
that flag through their quota guard. If a registered goal is checked without an
agent id, the payload includes `automation_prompt_upgrade.required=true` and a
recommended `heartbeat-prompt --agent-id ... --agent-scope ...` command. This
does not flip `should_run`; it is a lightweight migration signal for stale
installed automations.
When the status payload has same-goal checkpointed decisions that are stale or
have newer sampled events, `quota should-run` includes
`decision_freshness_warning`. This warning does not make an eligible goal skip;
it prevents a worker from treating an old reward, steering note, or operator
gate as current authority. Before reusing that decision, the worker must rebase
the decision point against the latest registry, ACTIVE_GOAL_STATE, quota,
policy, and run status. This is not a repo rollback and does not carry the whole
old chat state forward.
When the status payload has missing, stale, or unknown canary promotion
readiness evidence, `quota should-run` also includes
`promotion_readiness_warning`. This warning is additive: it does not change
`should_run`, but it lets heartbeat workers and dashboards report release
readiness blockers from the shared runtime release ledger without parsing
`doctor`, dashboard copy, or chat reports. New evidence is stored there rather
than in a project Goal. Before promoting the local release
snapshot, run `python3 examples/canary/canary-promotion-readiness-smoke.py` and
confirm fresh evidence in status or doctor output. The
`--no-write-evidence` form remains the non-mutating validation path and therefore
does not clear this warning.
Connected delivery goals also include `goal_boundary` when the registry has
boundary data. That field carries the adapter status, allowed write scope,
parent-approval scopes, registry guards, next probe, and stop condition. It is
the preferred place for project-specific delivery boundaries; automation
prompts should say to obey `goal_boundary` instead of repeating long protected
file or action lists.
It also includes `heartbeat_recommendation`, which keeps generic heartbeat
lifecycle decisions in LoopX instead of one-off automation prompts. The
common modes are:

- `run_first_read_only_map`: a connected read-only goal has no saved compact run
  yet; run one real `loopx read-only-map --goal-id <goal-id>`, validate
  and save the `read_only_project_map`, then append exactly one heartbeat spend.
- `mapped_noop_if_unchanged`: the latest compact read-only map already exists;
  if there is no new user instruction, owner evidence, agent todo, stale source,
  or safe handoff, return a quiet no-op without another dry-run or quota spend.
- `steering_audit_then_one_step`: this compatibility mode name does not impose
  a one-operation limit. The eligible goal needs a steering audit before
  scope-bounded work toward a verifiable result. Work size follows task,
  evidence and risk, not calls, files or wake cadence. Related implementation,
  research, tests and writeback may form one coherent effort; a focused
  correction can also be sufficient. Explicit stops, budgets, Todo/lease
  boundaries, settlement and replan requirements remain authoritative.

All heartbeat prompt sizes, including the default thin prompt, carry this
scope-bounded work guidance. This is a prompt clarification, not a change to
execution-profile thresholds, scheduler cadence, permissions or spend rules.
Fine-grained mode still plans independently verifiable checkpoints and checks
fresh evidence before a successor; checkpoint granularity is not a tool-call
limit. Read-only monitor polls, bounded recovery attempts and explicit
`end_current_heartbeat` transitions retain their existing limits. Do not turn
this guidance into a minimum batch size or a reason to bypass those limits.

The same response includes `interaction_contract.schema_version =
loopx_interaction_contract_v0`, the top-level user/agent/CLI protocol
for the selected turn. It tells a worker whether this is a user gate, blocker
push, bounded delivery, external-evidence observation, monitor quiet skip,
autonomous replan, outcome-floor recovery, mapped no-op, or quota throttle. It
also names whether the user must be interrupted, whether Codex must attempt
work, whether delivery is allowed, whether quiet no-op is allowed, and whether
quota spend is allowed only after validation. Executors should read this object
first.
The response also includes `scheduler_hint.schema_version=scheduler_hint_v0`.
That hint is not a delivery permission. It is the cross-runtime wait policy:
`run_now` keeps the active cadence for required work; `backoff_waiting_for_user`
slows Codex App and stops CLI/Claude loops after repeated unchanged polls;
`backoff_until_reassigned` handles peer reassignment waits without dropping
agent-to-agent handoff cadence too quickly;
`backoff_until_material_transition` handles monitor-only quiet polls; and
`backoff_until_fresh_evidence` handles mapped or post-handoff no-op waits.
For Codex App and local schedulers, `recommended_interval_minutes` is the next
target interval. For Codex App heartbeats, `recommended_rrule` is emitted only
when `app_automation.stateful_backoff.apply_needed=true`; if the desired RRULE is
already applied, it is omitted so the agent does not call a host tool again.
If that match still needs a reset-token/identity binding,
`stateful_backoff.ack_needed=true` and the bound ack runs without a host update.
When an apply is required but `automation_update` is unavailable in the
session, `app_automation.fallback_hint` carries the bounded `loopx-apply-rrule`
command for the resolved automation (backup `codex-dev.db`, sync TOML+SQLite,
run the bound ACK). Direct SQLite edits bypass the app API, so the fallback is
projected only for this gap and never as the routine path; an unresolved
automation id projects `available=false` and requires the pasteable heartbeat
gate instead of guessing.
After a successful host RRULE update, the agent records that fact with
`loopx` plus `app_automation.ack_hint.cli_args`; current payloads use
`quota scheduler-ack-current` to re-read the latest scheduler hint before LoopX
advances the per goal/agent scheduler state without spending quota. Human gates
can move Codex App heartbeats through `[30, 60]` after the concrete user todo
has been surfaced. LoopX caps the Codex App integration at 60 minutes; coarser
waits remain available to the local scheduler instead of being emitted as App
heartbeat RRULEs.
CLI-produced ACK hints bind that argument vector to the exact registry and
effective runtime root that produced `quota should-run`. Hosts must execute the
complete vector; stripping its leading global options can route a
project-launched ACK into project-local scheduler state instead of the shared
control plane. When the decision is heartbeat-receipted, ACK and failure hints
also bind the originating `turn_instance_id`. The follow-up rebuilds the same
receipt-bound live decision before validating its reset token and identity;
later Todos or a different unscoped wait lane cannot replace the decision that
requested the host action.
Monitor-only quiet waits move through `[15, 30, 60]` while preserving the
same no-spend monitor-poll contract, unless a monitor cadence or due time caps
the progression earlier. Fifteen minutes is only the default quiet-monitor
floor: an explicit cadence or due horizon below 15 minutes becomes the host
initial interval so the next wake cannot occur after the monitor is due.
The same deadline cap applies while a human gate owns notification semantics:
the user action remains a human gate and ordinary gates still use `[30, 60]`,
but a tighter continuous-monitor wakeup remains authoritative for host cadence.
Notification-only cooldown continues to use the human-gate interval rather
than turning the monitor deadline into a three-minute reminder policy.
Agent-scope waits use a more conservative adjustment curve such as
`[10, 20, 30, 60]`, so a 600-second local tick stays close to the existing
agent-to-agent interaction cadence before cooling further.
The compact hot path carries only the reset fields hosts need to act:
`reset_policy.reset_token`, `host_state_key`,
`app_automation_initial_interval_minutes`, `app_automation_initial_rrule`, and the short
`identity_signature`. Hosts should cache and compare `reset_token` across
unchanged polls and reset the unchanged streak whenever the token changes. The
token is derived from scheduler action plus the current identity/profile inputs;
the explanatory reset profile, profile signature, reset condition summary, and
stateful-backoff policy live in `scheduler_hint.cold_path_detail` when callers
request `loopx quota should-run --include-detail scheduler`. Hosts should also
reset when an external event makes the goal actionable again, such as user
feedback in the thread, a new or reassigned todo, a resolved gate, or material
evidence transition. A reset applies `app_automation_initial_interval_minutes` (and
the matching local scheduler initial interval) before starting unchanged
backoff again; it never spends quota.
For Codex App heartbeats, hosts and agents should use `automation_update` only
when `app_automation.stateful_backoff.apply_needed=true` and
`app_automation.recommended_rrule` is present. After `automation_update` succeeds,
the agent must run `app_automation.ack_hint.cli_args`. Current payloads use
`quota scheduler-ack-current`, so LoopX then persists `reset_token`,
`identity_signature`, `progression_index`, and
`last_applied_rrule` under the runtime root. Repeated unchanged identity
advances through `progression_minutes` only after the applied RRULE has had one
real interval to run. An immediate post-ACK reconciliation therefore verifies
the settled target instead of manufacturing the next backoff target in the
same turn. A changed `reset_policy.reset_token`
returns to the current profile's initial interval. This gives hosts a compact
post-update ack protocol instead of requiring them to own or diff the whole
quota state. If `apply_needed=false` and `ack_needed=true`, the same command
records an exact matching host readback without calling `automation_update`.
If `automation_update` fails or times out, the agent must not ACK. LoopX keeps
the observed host RRULE authoritative. The agent runs
`app_automation.failure_hint.cli_args` once to persist the failed target/observed-host
pair without quota spend. LoopX retains up to four distinct pairs for 24 hours,
so active-work and monitor-wait targets cannot overwrite one another while the
host RRULE remains unchanged. Later heartbeats expose `apply_needed=false` and
`state_status=host_update_failure_suppressed` for every retained exact pair.
A changed host observation invalidates failures recorded against the old host,
and a successful scheduler ACK clears only failures whose target is the
acknowledged RRULE. A fallback ACK therefore preserves failures for other
targets observed against the same unchanged host, preventing the next turn
from retrying a known-bad target/host pair. The legacy
`host_update_failure` scalar remains as the latest compatibility projection;
new hosts should consume `host_update_failures`. LoopX never treats an intended
cadence as an applied cadence.
For a human gate whose observed host interval is tighter than the failed target,
the same packet also projects `user_gate_notification_cooldown_v0`. The first
notice is preserved; short host polls are quiet, one host-sized window opens at
each target cadence, and a changed gate identity or host RRULE bypasses the old
cooldown. This changes notification delivery only, not the underlying user todo.
`scheduler-ack` is not a second `should-run`: it confirms the host update or
matching readback and does not emit or make immediately due a successor RRULE
in the same turn. User
feedback, newly runnable work, reassignment, or material evidence therefore
restores the automation to the current profile's initial interval before
backoff resumes.

`quota should-run` also observes the RRULE of a uniquely matched active Codex
App heartbeat (goal + agent + current thread). The observed host RRULE takes
precedence over `last_applied_rrule` when computing `apply_needed`, and the
compact result is exposed as `stateful_backoff.host_observation`. A mismatch is
`drift_detected`, so an ACK written before the host update—or a later host-side
cadence regression—cannot permanently suppress the repair. This observation
contains only cadence metadata. If a reset RRULE already matches but its new
reset token/identity is not persisted, `apply_needed=false`, `ack_needed=true`,
and the bound `ack_hint.cli_args` records that exact readback without a no-op
host write. Missing or mismatched readback still requires `automation_update`;
LoopX never edits the App manifest directly.
For Codex App SSH Goal, Codex CLI TUI, and Claude Code loops, the default hot path reads
`scheduler_hint.unchanged_poll.limits.<runtime>`. A value of `3` means the third
unchanged poll triggers the compact final quota/replan check named by
`scheduler_hint.unchanged_poll.final_quota_replan_check_action`; if the rerun is
still unchanged, the loop applies
`scheduler_hint.unchanged_poll.after_limits.<runtime>`. Hosts that need the
older per-runtime detail objects must opt in with
`quota should-run --include-detail scheduler` and read
`scheduler_hint.cold_path_detail.local_scheduler`,
`scheduler_hint.cold_path_detail.codex_cli_tui`,
`scheduler_hint.cold_path_detail.codex_app_ssh_goal`, or
`scheduler_hint.cold_path_detail.claude_code_loop`. That opt-in is diagnostic
and migration support only: a host or agent that forgets
`--include-detail scheduler` must still retain the core scheduling abilities by
reading the default hot-path fields named in
`scheduler_hint.detail_ref.hot_path_runtime_fields`.
For native Codex `/goal` runtimes (`codex_cli_tui` and
`codex_app_ssh_goal`), the after-limit action calls `update_goal` with
`status=blocked` only after the same blocked condition has repeated for three
consecutive Goal turns. The registered LoopX goal stays active, the user resumes
the native Goal with `/goal resume`, and neither the final check nor the blocked
transition spends LoopX quota.
The response also includes `execution_obligation`, which is the compatibility
field that separates worker execution from user-facing notification.
`heartbeat_recommendation.notify` answers "should this heartbeat interrupt the
user?", not "may the worker skip work?" When
`execution_obligation.kind=work_lane_contract`, the hard routing rule is
`work_lane_contract.obligation`; `execution_obligation` should merely point the
worker to that object. When
`execution_obligation.kind=external_evidence_observation_required`, delivery is
still blocked but a read-only observation or compact blocker writeback is
mandatory before quiet no-op. When
`execution_obligation.must_attempt_work=true`, a short heartbeat must attempt
one bounded segment, validate it, write durable state/events, and spend once
after successful delivery. A quiet no-op is only allowed when the machine
contract explicitly says `must_attempt_work=false` and no blocker-push
notification is required. If `notify_user_on_open_todo=true`, the turn should
notify the user and skip spend instead of disappearing silently; verified
`mapped_noop_if_unchanged` after confirming the mapped source is unchanged
remains a quiet no-op case.

Unknown goals or status collection failures return non-zero so automations fail
closed.

`quota spend-slot` is an accounting helper. By default it is dry-run only: it
shows the before and after `should-run` decision for consuming slots and writes
nothing. With `--execute`, it appends one compact `quota_slot_spent` runtime
event. It never mutates registry, reward overlays, operator gates, write
control, private evidence, or production state. The event is status-neutral:
it remains in run history for audit and quota counting, but it should not
become the current work classification in status, quota `should-run`, or the
dashboard attention queue.

The displayed before -> after transition is a same-status-payload projection
for the slot being written. Later `quota status` and `quota should-run`
commands recompute `spent_slots` from `quota_slot_spent` events still inside
the rolling `window_hours`; if an older spend expires between the preview and
the next check, the visible total can stay flat or drop even though the new
event was appended. Treat the appended event path as the write receipt, and
treat `spent_slots` as the current rolling-window total rather than a monotonic
counter.

Post-turn accounting protocol:

- call `quota should-run` before spending delivery compute;
- do the bounded automatic turn, validation, and state writeback;
- append an accountable `refresh-state` with `delivery_outcome=outcome_progress`
  or `primary_goal_outcome` before spend. This run is the causal delivery record
  consumed by `spend-slot`; a plain `state_refreshed` run without delivery
  outcome is quota-neutral and cannot replace it;
- append exactly one `quota spend-slot --execute` event for that completed
  turn after validated writeback. When the writeback is a state refresh that
  moves the guard from eligible/replan to waiting, `spend-slot` may still
  account the latest unspent `outcome_progress` delivery run once; a later
  duplicate spend is rejected because the latest run is then the spend event,
  not the delivery run.
- a quota-neutral `refresh-state` record may use a custom classification. Its
  refresh provenance, rather than the literal `state_refreshed` classification,
  keeps it from hiding the prior delivery. Explicit non-accountable delivery
  outcomes and unrelated events remain fail-closed.
- after spend, an optional plain state-only refresh may update dashboard or
  controller state. Do not append another accountable progress refresh after
  spend, because it would become a new unspent delivery record;
- keep accountable delivery attribution on the worktree that produced it. If
  `refresh-state` must run from a separate registry checkout, pass
  `--delivery-workspace-path <delivery-worktree>`; the path is validated locally
  and omitted from persisted history. Do not point this option at the canonical
  checkout for peer work.
- delivery attribution is not synonymous with Git. A registered single-agent
  goal whose project has no Git origin records a path-free `local_goal`
  workspace identity (`loopx:<goal-id>`) when refresh runs inside that
  registered project root. This lets validated non-repository work settle
  without inventing a repository. It does not weaken peer isolation: a peer
  repository write still requires an `independent_git_worktree`, and a local
  goal workspace is rejected when that requirement is active.
- autonomous replans follow the same accountable-outcome rule: spend after a
  concrete successor, blocker, or `outcome_progress`/`primary_goal_outcome`
  writeback, but do not spend for a `surface_only` watch-lane continuation or
  no-follow-up rationale that closes into `monitor_quiet_skip`. A replan that
  only changes a user gate, monitor target, watch continuation, or durable Next
  Action is normalized to `outcome_gap` even if the caller requests an
  accountable outcome; it must also change the executable, blocked, or terminal
  frontier before it can count as delivery progress.
- give each heartbeat a stable turn id and pass it to `quota should-run`; the
  guard commits one idempotent receipt, and for unchanged `monitor_quiet_skip`
  it idempotently appends the no-spend stall observation before returning quiet
  no-op versus autonomous replan;
- do not append spend for quiet `should_run=false` skips, preflight failures,
  pure dry-run previews, or duplicate accounting attempts;
- if `should_run=false` but `safe_bypass_allowed=true` and the agent actually
  completes bounded safe-bypass work, append one spend event for that work. For
  `safe_bypass_kind=outcome_floor_recovery`, spend only after validated
  ranker/cross-domain evidence or concrete blocker writeback, not for another
  surface-only report. Every safe-bypass spend must have a latest unspent
  accountable delivery writeback; the bypass decision alone never authorizes
  accounting.
- if `should_run=true` with `effective_action=control_plane_health_repair` or
  `control_plane_projection_repair`, append one spend event only after the
  control-plane projection or blocker writeback is validated.

## Slot Spend Event Contract

A real spend write path appends one compact runtime event after an automatic
Codex turn actually consumes quota. The smallest public-safe event is
`classification=quota_slot_spent` with a nested `quota_event` object:

```json
{
  "goal_id": "project-main-control",
  "classification": "quota_slot_spent",
  "quota_event": {
    "event_type": "quota_slot_spent",
    "source": "heartbeat",
    "slots": 1,
    "reason_summary": "one automatic Codex turn completed under an eligible quota guard",
    "delivery_run_generated_at": null,
    "delivery_run_classification": null,
    "before": {
      "should_run": true,
      "state": "eligible",
      "compute": 0.5,
      "window_hours": 24,
      "slot_minutes": 1,
      "spent_slots": 719,
      "allowed_slots": 720
    },
    "after": {
      "should_run": false,
      "state": "throttled",
      "compute": 0.5,
      "window_hours": 24,
      "slot_minutes": 1,
      "spent_slots": 720,
      "allowed_slots": 720
    }
  }
}
```

The public fixture is
[`examples/quota-slot-spend-event.example.json`](https://github.com/huangruiteng/loopx/blob/main/examples/quota-slot-spend-event.example.json).

Validation rule:

- write only after a fresh `quota should-run` returned `should_run=true`, or
  after it returned `safe_bypass_allowed=true` and the agent completed one
  bounded safe-bypass step;
- for `effective_action=control_plane_health_repair` or
  `control_plane_projection_repair`, treat the spend as control-plane
  self-repair accounting, not ordinary delivery;
- `slots` must be positive, and `after.spent_slots` must equal
  `before.spent_slots + slots`;
- if `after.spent_slots >= after.allowed_slots`, `after.state` should be
  `throttled` and `after.should_run` should be `false`;
- do not include human reward, operator-gate approval, write-control, private
  evidence, internal links, raw logs, or production identifiers in the quota
  event.

This event is accounting, not permission. It records that compute was spent
after the normal health/evidence/quota checks allowed the turn, or after an
operator gate explicitly scoped its block to the gated delivery path and allowed
one independent safe-bypass step.

Other write commands can stay behind explicit operator approval:

```bash
loopx quota set --goal-id <goal-id> --compute 0.5
loopx quota pause --goal-id <goal-id>
loopx quota burst --goal-id <goal-id> --slots 2 --dry-run
```

The important behavior is that automations ask LoopX whether a goal is
eligible before spending compute. They should not rely only on their own cron
period as the priority model.

## Acceptance Criteria

- Each active goal can expose a compute quota such as `1.0`, `0.5`, `0.3`, or
  `0`.
- `loopx status` can explain whether the goal is eligible, throttled,
  waiting, paused, or blocked before an automation spends another turn.
- A shared controller can rank eligible goals by compute quota without opening
  every project-agent thread.
- Per-goal automations can use the same quota to skip some ticks with a compact
  reason.
- Changing automation cadence is no longer the primary way to express project
  priority.
