# Autonomous Execution And Settlement

Read when setting up or repairing autonomous delivery, or when the current interaction contract is unavailable or contradictory. Ordinary admitted tasks execute the current contract and ordered settlement directly; this reference does not add a parallel turn or accounting step. For a recurring host, first use the [bootstrap loader](heartbeat-hosts.md); each wake executes only its freshly loaded body. The command details below support diagnosis and recovery within that current contract.

Contents:
- [Before Spending Automatic Compute](#before-spending-automatic-compute)
- [Write Back And Settle Current Work](#write-back-and-settle-current-work)

## Before Spending Automatic Compute

Before autonomous delivery, use the current host activation's registry route
and read the full successful admission packet. A recurring host does this through
its fresh loader body; these diagnostic forms do not replace that body:

```bash
loopx --format json --registry <GLOBAL_REGISTRY> quota should-run --goal-id <STABLE_GOAL_ID>
```

For a registered multi-agent goal, include this agent's identity:

```bash
loopx --format json --registry <GLOBAL_REGISTRY> quota should-run --goal-id <STABLE_GOAL_ID> --agent-id <REGISTERED_AGENT_ID>
```

If a registered goal returns `automation_prompt_upgrade.required=true`, treat
the installed automation prompt as stale. Follow
[heartbeat host recovery](heartbeat-hosts.md) to regenerate and read back the
stable bootstrap; do not install the expanded current-turn body.

If installed runtime behavior contradicts the expected source, first follow
[installation diagnosis](installation-and-diagnosis.md#installed-runtime-drift).

If the response has `state=operator_gate`, read `gate_prompt`,
`operator_question`, `recommended_action`, `next_handoff_condition`,
`missing_gates`, `user_todo_summary` and `agent_todo_summary` when present.
Use the current `interaction_contract.user_channel` to decide whether to notify;
ask its concrete question in Chinese only under `NOTIFY`, respecting the current
repeat/cooldown policy. An open user Todo is not by itself a new delivery gate.
Before repeating a public PR merge-approval gate, reconcile it against current
compact PR lifecycle state. When the todo has a merge-scoped decision such as
`direction:action:merge_pr_<number>` and the public PR URL is unambiguous, run:

```bash
loopx issue-fix pr-gate-reconcile \
  --goal-id <STABLE_GOAL_ID> \
  --todo-id <USER_GATE_TODO_ID> \
  --url <PUBLIC_GITHUB_PR_URL> \
  --fetch-metadata \
  --execute
```

Then rerun `quota should-run`. The command may complete only the matching
`user_gate`, and only after compact public metadata reports `MERGED` or
`CLOSED`; it records no body, comments, logs, provider payload, or external
write. If the URL is ambiguous or the public read fails, preserve the gate and
surface that exact resolution failure instead of claiming the owner still needs
to approve an already-terminal PR.
When a notification is required, name the projected Todo/question rather than
only "owner gate". If required items are missing, report "具体 user todo 未投影，
需修复 LoopX 状态投影"; do not say there is no user action. With no projected user
action, a quiet result is not a projection bug. Never execute the gated path while
awaiting its decision; independent work still needs current agent-channel admission.

Prefer the guard's `interaction_contract` when present. It is the current
machine-readable protocol for the user / agent / LoopX CLI split:
`interaction_contract.user_channel` says whether to ask the user,
`agent_channel` says whether Codex must attempt work or may quiet no-op, and
`cli_channel` says which CLI transition and spend policy apply. Treat older
fields such as `execution_obligation`, `heartbeat_recommendation`,
`work_lane_contract`, and `goal_boundary` as compatibility/drill-down fields
under that contract, not as competing sources of truth.

Use `cli_channel.selection_command` and re-enter when the current contract
requires it. If `should_run=false` and `safe_bypass_allowed` is not true, do not
run implementation or adapter work for that Goal. Preserve the loaded body's
heartbeat turn identity: the guard commits one idempotent receipt on every heartbeat
when called with that identity. Do not invent or persist a timestamp/turn id in
this reference's commands. For `heartbeat_receipt.status=write_failed`, first
read back/recover the uncertain effect, then retry with the same turn id only
when the current recovery contract allows it; never append another poll/spend.
Follow current `autonomous_replan_required` and `execution_obligation` rather
than inferring permission from a quiet skip. Unchanged monitor-only polls are
liveness-preserving no-ops, not self-stop signals; keep the heartbeat active unless
the current Core/host contract directs a terminal stop. If admission is unavailable
or incomplete, recover through `loopx doctor` / `loopx status` within existing
authority; do not execute delivery or account for work before recovery.

If the current contract exposes `safe_bypass_allowed=true`, the gate blocks only
its named path. Select the admitted independent work from the current agent
channel/priority context, validate it, and follow the current settlement plan;
do not stop at one small operation when related authorized work remains useful.
Keep pending user actions visible under the current notification policy and use
projected agent Todos rather than mining old chat for an alternative authority.

For `safe_bypass_kind=outcome_floor_recovery` or the matching recommended mode,
produce the evidence named by `quota.must_advance` or its concrete blocker.
Summary/queue/contract propagation and synthetic-only test chains cannot replace
that result. The current typed settlement determines writeback and whether a
spend is due; a recovery attempt or blocker is not an unconditional spend rule.

This guard is only a compute-allocation check. It does not grant write
permission, bypass operator gates, or replace run-bound human reward. Operator
gates block the gated delivery path, not unrelated safe steering work.
For an eligible current goal, dependency or sibling-goal todos must not consume the whole eligible turn.
Record or surface those todos as dependency blockers,
but continue the steering audit and choose a gate-independent
P0/P1/P2 candidate for the current goal when one exists and is currently admitted.
A user/owner Todo blocks only the selected path covered by its current gate;
explicit user stops, quota and Core/host stop boundaries also remain binding.
Routine public repo publication is a boundary decision, not a standing operator
gate: when existing user authority and repository rules permit the step,
validation passes, and the public/private boundary scan is clean, commit, push,
and PR creation can proceed without asking again. An old chat, trust setting,
status or review command does not grant new execution authority. Keep private/company-internal material and credentials out of public artifacts.
Stop the affected path when it exceeds current authorization or would disclose
protected material; authorized private reads and recovery may continue.
Destructive Git and production retain explicit authorization requirements, and
repository rules that explicitly require review still apply.
Use the shared global registry for this guard so the project agent reads the
same operator gates, user todos, agent todos, and quota state as the dashboard. This does
not mean all project work is global: `todo add`, `refresh-state`, adapter runs,
and project-file reads still use the project-local state/registry and then sync
the public-safe projection back into the global control plane. If two projects
share a `goal_id`, treat that as a registry health bug and fix the id/source
mapping.

When new evidence or a projected review warrants reconsidering selected work,
read the current Priority Stack, recent progress and critic. Run a short steering
audit over relevant alternatives without a fixed candidate count. If the same
topic has consumed several recent delivery slices, apply a continuation check and state
why continuing still wins; keep compute quota separate from focus quota; record
any losing high-value candidate that should not be forgotten. Include a product
bottleneck lens: ask whether the core goal is currently bottlenecked by user
experience, agent capability, evidence quality, adapter readiness, or
priority-rule gaps, and promote one concrete bottleneck candidate when it should
outrank the nearest local TODO. Choose scope-bounded work toward a verifiable
result. Size the work by the task, evidence and risk, not tool calls, file count
or heartbeat interval. Related implementation, research, validation and writeback
may form one coherent effort; a focused correction can also be sufficient.
One operation or writeback alone is not a reason to stop. Budget, scope, explicit
stop conditions, settlement and replan requirements still apply; this guidance
does not authorize a new Todo, lease, direction or external action.

When you tell the user a connected LoopX plan, top-todo list, priority
stack, or route change, treat that as a writeback trigger, not chat memory. If
the plan contains concrete future P0/P1/P2 work, user actions, route decisions,
or deprecations, update active-state todos / Next Action / `refresh-state`
before the final response, or explicitly say why the plan is only speculative
and was not written. Use `loopx todo add` / `todo update` for durable
work items; do not leave them only in prose, a review doc, or a Lark/chat
reply.

For connected delivery goals, also read `goal_boundary` from the
`quota should-run` payload before choosing a step. It carries the registry's
adapter status, allowed write scope, parent-approval scopes, guards, and stop
condition. Treat it as the project-specific boundary contract so automation
prompts can stay short instead of repeating long per-project protected-scope
lists.
When reading status or quota routing, use `attention_queue.items` and
`project_asset` as current authority only when the item is project-asset-backed.
If `project_asset` is absent or the source is legacy/raw fallback, do not infer
owner, gate, or stop-condition authority from raw queue fields.

## Write Back And Settle Current Work

Admitted planning, evidence work and delivery follow the current
`interaction_contract.cli_channel.settlement_plan.ordered_steps`; when no plan
is projected, use the current `next_cli_actions`. Preserve exact identities,
effect ids, selected Todo/lease, flags, actual outcomes and complete command
returns. Do not reconstruct static refresh/spend commands from this skill.
Accountable progress precedes its spend; uncertain effects require readback and
recovery before retry. State-only refresh cannot replace accountable writeback.

The absence of an adapter run or an unchanged artifact does not waive settlement.
Only setup metadata work with no admitted delivery may use a state-only refresh
when needed. After admitted delivery, an optional state-only refresh follows its
settlement; never append another accountable progress record after spend. Exact
receipt-bound monitor settlement uses its projected monitor actions and does not
refresh/spend; an auxiliary poll does not replace admitted work. Quiet skips,
preflight failures and pure dry runs do not spend. Preserve owed settlement
according to the current terminal/recovery contract. Continuation or settlement
guidance must not override an explicit no-spend stop. If execution is not
permitted, retain unsettled state and do not claim settlement is complete.

Keep the same registered `--agent-id` envelope. Use the current plan's authorized
progress scope; `--progress-scope goal` is not a transfer of Goal ownership. Run
accountable writeback from the delivery worktree, or preserve its attribution with
`--delivery-workspace-path <delivery-worktree>` when projecting from elsewhere.
After settlement read back the receipts and current quota; obey the returned
continuation/wait boundary. An already-settled heartbeat cannot claim or spend
an independent successor with its old turn identity.

When the current writeback requests delivery hints, fill these options into its
exact generated command so status, review packets and quota guards do not infer
scale/outcome from the classification name:

- `--classification <PUBLIC_SAFE_PROGRESS_CLASSIFICATION>`
- `--delivery-batch-scale <ACTUAL_DELIVERY_BATCH_SCALE>`
- `--delivery-outcome <ACTUAL_DELIVERY_OUTCOME>`

Replace all three placeholders from the current validated turn. Never default
or upgrade a smaller/preparatory turn to `multi_surface` / `outcome_progress`
just because those values would satisfy a delivery floor.

If the current run may write local LoopX state but its runtime boundary forbids
configured external sink writes, keep `explore_graph.enabled` unchanged and add
`--suppress-external-sinks`. This preserves canonical local graph projection,
records the run-scoped authorization boundary, and leaves sink digests pending
for a later authorized refresh. Do not toggle the goal-level Graph setting as a
one-off workaround for an external-write boundary.

Use `delivery_outcome` as a machine enum, not as prose:

- `surface_only`: docs, contracts, smokes, setup, or preparation moved, but the
  primary product/case result did not.
- `outcome_gap`: the run should have advanced the primary result but ended with
  a concrete blocker or missing outcome.
- `outcome_progress`: primary result evidence advanced materially, but the
  selected stage is not fully complete.
- `primary_goal_outcome`: the selected stage's primary result is complete,
  validated, and written back.

Do not rely on `classification` names such as `*_contract_v0_delivered` to carry
this meaning. `classification` is a human/history label; `delivery_outcome` is
the control-plane signal consumed by quota, status, and review packets.

This fixes stale dashboards where the latest run still shows an old
`ready_for_controller_opt_in` or similar state. It also auto-syncs the project
entry into the global registry. If you do not pass `--recommended-action`, the
refresh run should publish the first local-control-plane item from
`## Next Action` as the compact dashboard action, including wrapped continuation
lines. `recommended_action` may include private/local routing references needed
by the individual operator, but must not include credentials, auth headers, or
inline secrets; shareable/public projections are responsible for redaction.

For complex projects, do not pack a whole user reading queue into
`## Next Action`. Keep the first Next Action item as one routing sentence, then
use the CLI to write explicit checkbox sections instead of hand-editing section
names:

```bash
loopx todo add \
  --goal-id <STABLE_GOAL_ID> \
  --role user \
  --text "Read the short review packet before approving delivery."

loopx todo add \
  --goal-id <STABLE_GOAL_ID> \
  --role agent \
  --text "Build the next read-only worksheet after the user decision is recorded."
```

The CLI creates the canonical section when needed and avoids duplicate exact
todo text. The resulting Markdown shape is:

```md
## User Todo / Owner Review Reading Queue

- [ ] Read the short review packet.
- [ ] Record the owner decision in the worksheet.

## Agent Todo

- [ ] Build the next read-only worksheet after the user decision is recorded.
```

`loopx status` lifts those sections into `user_todos` and `agent_todos`.
The dashboard uses `user_todos` for the first-screen human checklist. Reading
`agent_todos` helps diagnose current work; executing them still requires current
health, gate, evidence and quota admission.

Complete a Todo only when its original deliverable and current acceptance are
satisfied; Todo completion is not Turn settlement. Reuse or create a concrete
successor when real follow-up remains, preserving current continuation and
closeout requirements. Do not invent a successor or no-follow-up declaration
merely to make ordinary completion pass. For a final outcome, use the current
plan's terminal closeout only when its evidence and receipt preconditions hold.

An open feature PR waiting for ordinary review does not by itself block further
project work. Record validated PR evidence and the real review reminder as
`user_action`; continue an admitted agent successor when one exists. Use a
`continuous_monitor` for ongoing PR lifecycle readback when needed. Reserve
`user_gate` for a real authority boundary such as merge, release, benchmark
launch, credentials or protected production; do not turn every review reminder
into a gate or infer merge authority from publication. A stable integration
branch may collect feature PRs, with the aggregate merge subject to its actual
repository/owner authorization.
When the next agent slice is not yet known, keep the bound `user_action`
visible without converting it into a gate. When the current guard projects
`autonomous_replan_required`, produce its typed semantic outcome; an empty
frontier or repeated wakes alone do not create that obligation. If the
projected frontier owns a concrete target, create and claim that runnable agent
todo, binding it to the projected `obligation_id` with
`--replan-obligation-id <exact-id>`, a typed `--action-kind`, and a stable
`--target-key` or Explore node ref. The atomic Todo mutation is the semantic
receipt; do not add a second repair ACK. When no executable target is known,
use the projected typed semantic or coverage-backed terminal writeback rather
than creating a Todo whose task is merely to replan. Only authoritative typed
terminal-closure evidence may replace that Todo writeback with explicit
no-follow-up.
`successor_todo_ids` records lineage only: linking successors does not suspend
an open parent. When splitting a parent into explicit successors, decide whether
the parent still has an independent immediate action. If it does not, explicitly
defer it with a supported `resume_when` or complete it; do not infer aggregate
semantics from successor links alone. Treat `parent_successor_advisory` in the
`todo update` response as the machine-readable authoring reminder for this
decision, not as a new persisted lifecycle state.
