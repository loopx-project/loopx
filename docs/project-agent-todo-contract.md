# Project Agent Todo Contract

Project agents should keep operator-facing work out of long chat replies,
review documents, and overloaded `Next Action` paragraphs. LoopX uses
separate fields so the dashboard and quota guard can show the right work to the
right actor.

## Field Roles

- `Next Action` is one routing sentence for the next bounded step. It is not a
  reading queue, blocker dump, or checklist.
- `User Todo / Owner Review Reading Queue` is the human-facing checklist. Use
  it for concrete user, owner, or controller input that the agent cannot
  complete by itself.
- `Agent Todo` is the project-agent checklist. Use it for safe follow-up work
  the agent can do after health, operator gates, evidence, and quota allow
  execution.
- Production blockers, missing write approvals, and safety risks are gates or
  stop conditions. Do not count them as user todos unless a specific human
  action can clear them.

External boards and management surfaces, including Lark Kanban, are projections
of this contract. They may show critical status, claims, gates, evidence, and
worker handoff fields, but they should not become the place where agents invent
new task identity. A long-running Codex session can claim visible board work and
continue it; when it needs to fan out, split, supersede, or create successor
work, it writes the new task through the LoopX todo lifecycle and lets the board
sync catch up.

## Priority intent

Priority is an explicit scheduling field, with `P0` highest and `P4` lowest.
Create or edit it independently of the task description:

```bash
loopx todo add --goal-id <goal-id> --role agent --priority P1 --text '<agent action>'
loopx todo update --goal-id <goal-id> --todo-id <todo-id> --priority P3
loopx todo update --goal-id <goal-id> --todo-id <todo-id> --clear-priority
```

Pass the registered `--agent-id` on updates when the Goal has multiple Agents.
The existing authoring, ownership, lease and review requirements still apply.
Chat `todo.create` accepts `priority`; reviewed `todo.update` edits accept
`priority` or `clear_priority`. The task management panel uses that reviewed
update path for its priority selector. Priority never grants eligibility,
capabilities, a claim or quota; missing priority is allowed and sorts after P4.
The generated agent authoring hint shows an explicit P1 example, not a global
default or a prerequisite for same-turn binding.

Omitting priority from an ordinary text edit preserves the current priority.
Use `--clear-priority` to remove it. Legacy `--text '[P2] Task'` remains supported;
a supplied prefix edits priority for old callers. An explicit parameter and a
conflicting prefix are rejected before writing, as are simultaneous set/clear
instructions. Words such as `P0` inside ordinary prose have no scheduling meaning.
Historical decorated prefixes such as `[P2-review]` read as P2; an edit renders
the normalized `[P2]` prefix. P3/P4 participate in ordering and repair suggestions
without being silently promoted to P1. Existing repair-suggestion and historical
event-replay defaults retain their own contracts; they do not impose a default
on new unprioritized Todos.

`todos/priority.ts` owns authoring intent and ordering. The generated coordination
contract supplies its vocabulary and legacy grammar to Python read adapters.
Markdown keeps the compatible `[Pn] description` display; native authority records
also carry canonical priority/title. File, SQLite and PostgreSQL updates use the
existing admitted head, CAS and operation receipt. A retry retains the original
intent identity and returns its historical result; changing priority under the
same operation ID is a conflict. No provider promotion or receipt rewrite occurs.

## Write Contract

For a caller-owned runtime that already registered its Goal/Agent, generate the
model planning checkpoint before writing executable task Todos:

```bash
loopx --format json todo plan --goal-id <goal-id> --agent-id <registered-agent> \
  --text '<exact new task or follow-up input>'
```

This read-only command reuses `/loopx`'s planner and Todo-delta contract. It
returns the current frontier, typed result schema and an explicit caller-owned
execution handoff; it does not run a model, create a Goal, write a Todo, activate
a host loop or spend quota. A model consumes the packet and uses the existing
Todo CLI to plan actual task work. No planning/setup Todo is required. Read back
the returned ids and current state before the caller activates its driver and
enters the normal quota guard. A planning result does not authorize execution.
Follow-up input preserves the Goal/Agent and existing waits; reconcile the plan
instead of restarting the Goal. Unrelated peers and their claims remain intact.

The installed `$loopx` skill recognizes this explicit packet as a bounded
planning checkpoint. Without one, its existing startup/continuation behavior is
unchanged. Omit `todo plan` to use that ordinary interactive entry; callers must
not simulate a planning checkpoint by prewriting an advancement Todo.

When read-only analysis, a review packet, a gate checklist, or P0/P1 steering
finds a concrete user or owner action, write it immediately with the todo CLI.
Use `user_gate` only when the item blocks an agent or the whole goal:

```bash
loopx todo add \
  --goal-id <goal-id> \
  --role user \
  --task-class user_gate \
  --blocks-agent <agent-id> \
  --text "<public-safe blocking user or owner decision>"
```

Use `user_action` for owner-visible follow-up that should not stop unrelated
agents:

```bash
loopx todo add \
  --goal-id <goal-id> \
  --role user \
  --task-class user_action \
  --text "<public-safe non-blocking user or owner todo>"
```

Use `--role agent` for project-agent follow-up work:

```bash
loopx todo add \
  --goal-id <goal-id> \
  --role agent \
  --text "<public-safe agent action>"
```

Executable agent work should register its lane instead of relying on text
classification. Use `advancement_task` for a bounded implementation,
validation, benchmark, blocker-writeback, or repair segment:

```bash
loopx todo add \
  --goal-id <goal-id> \
  --role agent \
  --text "<public-safe executable agent action>" \
  --task-class advancement_task \
  --action-kind run_eval
```

Use `continuous_monitor` only for watch-only surfaces where an unchanged poll
must stay quiet:

```bash
loopx todo add \
  --goal-id <goal-id> \
  --role agent \
  --text "<public-safe monitor action>" \
  --task-class continuous_monitor \
  --action-kind monitor
```

`watch_only=true` changes convergence and replan semantics, not schedulability.
A scheduled watch-only monitor remains eligible at `next_due_at`, but it never
creates autonomous replan pressure and never preempts runnable advancement.
When both are present, `interaction_contract` keeps advancement primary and
projects an optional, typed, no-spend `auxiliary_monitor_poll` route.
That CLI route is available only when it is bound to the current Turn. It
requires the caller to place the fresh observation digest in
`LOOPX_MONITOR_RESULT_HASH` and exposes separate unchanged and material-change
commands; omitting either the Turn binding or result digest fails closed before
monitor writeback.
The canonical watch-only/ordinary-due partition is produced inside the existing
TypeScript Todo summary and quota-planning owners after Agent scope and
capability admission; Python compatibility code only adapts legacy facts and
renders the selected CLI/Lark route.

`watch_only=true` 改变的是收敛与 replan 语义，而不是可调度性。带
`next_due_at` 的 watch-only monitor 到期后仍可轮询，但不会制造 autonomous
replan 压力，也不会抢占 runnable advancement；二者同时存在时，
`interaction_contract` 保持 advancement 为主，并投影一条可选、typed、no-spend
的 `auxiliary_monitor_poll` 路由。
该 CLI 路由仅在绑定当前 Turn 时可用；调用方必须把本次新鲜 observation digest
写入 `LOOPX_MONITOR_RESULT_HASH`，并在 unchanged 与 material-change 两条命令中
明确选择。缺少 Turn 绑定或 result digest 时，monitor writeback 会在写入前失败关闭。
watch-only／普通 due 的权威分区由既有 TypeScript Todo summary 与 quota-planning
owner 在 Agent scope 和 capability admission 之后生成；Python 兼容层只适配旧事实并
渲染已选中的 CLI／Lark 路由。

`--action-kind` is a public-safe token. Known generic tokens such as
`run_eval`, `validate`, `rebuild`, `writeback`, `monitor`, and `poll` help the
CLI project the lane consistently, but explicit `--task-class` is the authority
when both are present. If an exact todo already exists, `todo add` updates or
inserts the metadata comment instead of creating a duplicate checkbox.
`--task-class user_gate`, `--task-class user_action`, and `--task-class blocker`
are non-executable control lanes; quota/executor code must not treat them as
advancement work. Open user todos must declare either `user_gate` or
`user_action`; a bare `--role user` todo is an authoring error.

For scheduled monitors, keep the contract minimal: `--next-due-at` is the first
eligible time, `--cadence` is the retry interval, `--monitor-target-key` is the
stable idempotency key, and optional `--expires-at` is the hard stop after
which the monitor must not catch up.

Public Todo updates validate the effective waiting state, not just newly supplied
`resume_when`. Changing a Monitor-waiting Todo's status/task class or successor
list must preserve the open advancement-task/independent-successor contract.
To leave that contract, explicitly clear `resume_when` in the same update; this
also clears its Monitor generation fence. Ordinary text/note corrections do not
re-arm a wait, reset its baseline, or demand a new successor after its condition
becomes satisfied. Explicitly re-submitting a satisfied Monitor condition still
requires clearing it before re-arming. These checks are planning constraints,
not permission to claim work, commit to a provider, or execute a successor.

Monitor observations are reduced against the Todo under its existing writer lock.
Callers report a result hash and material-change fact; they must not independently
increment counters. A material observation with a different result hash increments
`material_change_generation`; repeating the same hash does not. An unchanged
same-hash poll increments `consecutive_no_change`; material change or a changed hash
resets that count. Issue-fix grouped membership updates use this same path.
New grouped monitors default to watch-only; subsequent observations preserve
the existing expiration/watch policy rather than silently re-enabling watch-only.

An exact `monitor_effect_id` replay keeps the committed counters. Reusing the ID
with different observation fields fails. Older timestamps fail even without an
effect ID; same-second unkeyed polls remain allowed, while distinct keyed effects
retain strict ordering. Ordering preserves microseconds. Newly written
`material_change_generation` and `consecutive_no_change` values must be
non-negative safe integers. Use ISO timestamps
(for example `2030-01-01T12:00:00.000001+00:00`); invalid calendar dates are
rejected. Existing compact/week-date and timezone-offset-second spellings remain readable.
Existing malformed historical timestamps do not prove an ordering fence.

These rules do not make a Monitor executable delivery work, grant claim/lease
authority, or make Monitor and successor writes atomic. A planning result is
not a durable receipt; provider promotion remains explicitly gated.

Terminology: a `goal_id` is the LoopX control-plane boundary: registry
entry, active-state file, quota lane, status projection, and run-history stream.
A `todo_id` is a structured work item inside that goal. LoopX does not
currently model issues as a separate runtime object.

Multiple agents may share the same project control plane. A todo can carry a
soft owner with `claimed_by`, but ordinary agent todos should not restate the
agent's broad prompt scope. Scope belongs in the automation prompt or sub-agent
handoff; the agent uses that scope to decide which open todo it may claim.
User-gate todos are different: when a user decision only unlocks one registered
agent or lane, record the blocked agent explicitly with `blocks_agent` so quota
does not stop unrelated agents. For convenience, `todo add --role user
--task-class user_gate --agent-id <agent>` defaults `blocks_agent` to that agent
when neither an explicit `--blocks-agent` nor `--global-gate` is supplied.
Updates preserve omitted scope; changing the author does not retarget a gate.
In multi-agent goals, open `user_gate` todos
must have exactly one explicit scope: either `blocks_agent=<registered-agent>`
for a lane-scoped decision or `global_gate=true` / `--global-gate` for a
genuine goal-wide owner gate. Unscoped multi-agent user gates are an authoring
error because every registered agent would otherwise see another lane's
question as its own stop condition.

**Global gates have broad impact: they block every registered agent until
resolved.** Creation or widening to global scope requires explicit
`--global-gate`; it is never inferred from author identity, missing binding,
or `--goal-bound`. `--goal-bound` scopes continuation only and does not itself
block agents. Prefer `--blocks-agent <agent>` for a lane-local decision.
With an explicit global gate, LoopX derives the necessary goal-wide
continuation binding without inventing a single-agent binding from the author.
Explicit contradictory flags are rejected, not silently overwritten.

To narrow an existing global gate atomically, use `todo update` with
`--clear-global-gate --blocks-agent <agent>`. To widen a lane gate deliberately,
use `--clear-blocks-agent --global-gate`. Merely clearing scope in a multi-agent
Goal is rejected; it must not turn an ambiguous gate into a global one.

When a user gate only blocks one concrete action, add the blocked todo id with
`unblocks_todo_id=<todo_id>`. When multiple todos share the same broad
`action_kind`, use the schema-backed decision-scope fields instead of relying
on title/body token overlap:

```bash
loopx todo add \
  --goal-id <goal-id> \
  --role user \
  --task-class user_gate \
  --agent-id codex-main-control \
  --decision-scope direction:action:benchmark_target_choice \
  --text "Choose the benchmark target before running that case."

loopx todo update \
  --goal-id <goal-id> \
  --todo-id <agent-todo-id> \
  --required-decision-scope direction:action:benchmark_target_choice
```

Quota treats a gate as covering an agent todo when `decision_scope` matches or
dominates one of that todo's `required_decision_scopes`; otherwise the todo is
independent and can be selected as a safe fallback when the boundary permits it.

Each shared goal declares `coordination.agent_model=peer_v1` and a
`coordination.registered_agents` set. Registration grants identity, not rank.
Work authority comes from `claimed_by`, task leases, the goal/write boundary,
and typed continuation policy. Functional profile roles and scope summaries are
advisory; they do not make one identity the default reviewer or leader.

Ordinary lifecycle mutations follow todo ownership. A goal may separately
delegate narrow cross-owner actions to an orchestration agent:

```yaml
coordination:
  todo_lifecycle_authority:
    - agent_id: codex-main-control
      actions: [complete, reassign, supersede]
      requires_reason: true
```

Configure the equivalent registry value without hand-editing state:

```bash
loopx configure-goal \
  --goal-id <goal-id> \
  --todo-lifecycle-authority-json \
  '{"agent_id":"codex-main-control","actions":["complete","reassign","supersede"],"requires_reason":true}' \
  --execute
```

The delegated agent must already be registered. Each override is action-scoped
and emits a typed receipt containing the actor, original owner, authority
source, and public-safe `--authority-reason`. Delegation never bypasses an
explicit `excluded_agents` boundary. `coordination.supervisor` remains a
proposal-only observation role and does not imply lifecycle authority.

```bash
loopx todo complete \
  --goal-id <goal-id> \
  --todo-id <todo-id> \
  --agent-id codex-main-control \
  --authority-reason "Verified the result and closed the stalled lane." \
  --evidence "<public-safe evidence>"
```

An agent todo can name a different task repository without copying agent scope
into todo metadata:

```bash
loopx todo update \
  --goal-id <goal-id> \
  --role agent \
  --todo-id <todo-id> \
  --task-repository git:github.com/owner/repo
```

`task_repository` is a first-class, credential-free Git identity. It routes
workspace isolation, not write authority; claim/lease, capabilities, the goal
boundary, and repository policy continue to apply.

Completion validation follows the same routing. If `task_repository` differs
from the Goal repository, LoopX binds the caller-approved command to a
turn-bound delivery-workspace receipt (an existing writeback receipt or the
exact Turn's host-verified pre-completion snapshot) and executes it only from a
clean linked worktree whose canonical origin matches that identity. A missing receipt,
canonical checkout, dirty or deleted worktree, and repository mismatch all
fail closed before command execution with a path-free
`validation_blocked_completion` receipt. When no separate repository is
declared, validation keeps the Goal repository as its default workspace. The
CLI and managed Turn use this shared completion effect; frontend and Lark
consume the same receipt projection and do not own a second cwd setting.

完成校验遵循同一套路由规则。若 `task_repository` 与 Goal 仓库不同，LoopX
会把调用方预先声明的校验命令绑定到当前 Turn 的 delivery-workspace receipt（已有
writeback receipt，或同一 Turn 中 host 在完成前验证的 snapshot），且只在
canonical origin 匹配、状态干净的 linked worktree 中执行。receipt 缺失、使用
canonical checkout、worktree 脏或已删除、仓库身份不匹配时，系统都会在命令执行
前 fail closed，并返回不泄露本地路径的 `validation_blocked_completion` receipt。
未声明独立仓库时，仍以 Goal 仓库作为默认校验 workspace。CLI 与 managed Turn
共享同一个 completion effect；前端与 Lark 只消费同源 receipt 投影，不新增 cwd
配置源。

`quota should-run --agent-id <agent-id>` is the preflight for every peer. When
the selected task writes repository state and the peer is in a non-git,
unrelated, or non-isolated workspace, it returns `workspace_guard` and blocks
normal delivery until that peer moves to an independent worktree and reruns the
guard. Read-only and monitor-only work does not require isolation merely because
of agent identity. When `task_repository` is absent, the registered goal repo is
still the expected repository, so an unrelated worktree cannot bypass the goal
repository rule.

Contributor-facing example:

```bash
loopx --format json quota should-run \
  --goal-id <goal-id> \
  --agent-id codex-peer-b
```

If the response includes
`effective_action=agent_workspace_repair`, the peer should not edit files yet.
Create or switch to a separate worktree and rerun the same guard:

```bash
git worktree add /tmp/<goal-id>-peer-b -b codex/<peer-branch>
cd /tmp/<goal-id>-peer-b
loopx --format json quota should-run \
  --goal-id <goal-id> \
  --agent-id codex-peer-b
```

Only after that rerun returns normal delivery should the peer claim an in-scope
todo and edit repository files. A todo claimed by another peer remains owned by
that peer until explicit transfer. For agent-specific quota payloads, current
agent claims are preferred, unclaimed todos remain selectable, and other-peer
claims are diagnostic context rather than executable work. This reduces
collisions without writing broad prompt scope into todo metadata or pretending
that a soft claim is already a hard lease. When a runnable current-agent or unclaimed
advancement todo exists, quota may also expose
`agent_lane_next_action.schema_version=agent_lane_next_action_v0`. That field is
the peer's current slice for this turn; it does not overwrite the durable
goal-level `Next Action`. `loopx status --agent-id <agent-id>` may attach the same derived field to matching status
queue items for observation, while leaving the project-level route unchanged.
When a candidate has `target_capabilities` and missing target bridge
capabilities, quota may mark it `capability_repair_mode=true`; scoped
next-action selection should prefer that repair-mode candidate over ordinary
runnable work in the same claim/priority bucket so capability-building todos do
not require fragile active-state reordering.

### Machine-readable resume conditions

Deferred todos may carry a machine-readable resume condition with
`resume_when=<token>`. Supported conditions are:

- `resume_when=todo_done:<todo_id>`: the deferred todo becomes a successor
  replan candidate after the referenced todo reaches `status=done`.
- `resume_when=pr_merged:#532` or
  `resume_when=pr_merged:owner/repo#532`: the deferred todo becomes a successor
  candidate after a structured rollout event records that PR merge. An
  unqualified `#532` is bound only to the todo's GitHub `task_repository`; use
  the qualified form for cross-repository dependencies. If LoopX cannot derive
  that binding, it keeps the todo deferred and exposes a machine-readable
  repository ambiguity instead of matching the same PR number in another
  repository.
- `resume_when=capacity_available:<capability>`: the todo becomes ready only
  when the current quota read supplies that runtime capability.
- `resume_when=monitor_changed:<monitor_todo_id>`: an open advancement todo
  resumes only after the referenced `continuous_monitor` records a new typed
  material-change generation. The transition binds the monitor's current
  generation as a baseline, so unchanged polls, note edits, and replay of the
  same material result do not wake the todo.
- `resume_when=resume_at:<timezone-aware-rfc3339-timestamp>`: the todo becomes
  ready at or after one exact instant. A timezone is mandatory; authoring
  normalizes equivalent offsets to UTC. Before that instant the todo remains a
  typed wait. At and after it, the projection exposes generation `1` and the
  same content-addressed `todo_resume_receipt_v0` across repeated reads and
  process restarts.

`resume_at` is a one-shot Todo condition, not a recurring scheduler. Natural
language such as `tomorrow morning` is rejected instead of being interpreted
relative to a host locale. CLI, heartbeat quota reads, and managed Turn consume
the same Todo projection and runtime-clock snapshot. A due receipt makes the
lifecycle replan observable, but it does not reopen the Todo or grant execution
authority by itself.

The monitor and the delivery it discovers are separate work items. A
`continuous_monitor` is observe-only; it never becomes the runnable delivery.
On a material observation, use `quota monitor-poll --material-change
--next-agent-todo ... --next-action-kind ...` to emit an independent open
`advancement_task`. A waiting advancement Todo stays `status=open` and pairs
`resume_when=monitor_changed:<monitor_todo_id>` with that independent Todo via
`--successor-todo-id`. The resume condition—not `status=blocked`—keeps the
waiting Todo out of runnable selection until the monitor generation advances.
Relevant command results expose the compact
`monitor_advancement_authoring_v0` contract so an Agent can recover this
sequence without parsing documentation prose.

Monitor successor routing uses one typed plan for preflight, writeback and
receipt verification. Common Git transport URLs resolve to the same canonical
repository identity, and action/claim/capability aliases are normalized before
comparison. Repository routes must be representable as canonical `git:<host>/<path>`
identities; control characters, backslashes and percent-encoded paths are rejected.
Every supplied capability must be valid: an invalid entry is not silently dropped
from a partly valid list. Follow-ups require `--material-change`; assignment or
other agent-route flags without `--next-agent-todo` are rejected before writeback.
User follow-ups still require explicit `user_action` or `user_gate`, never an
implicit global gate. A route plan is not a claim, approval or atomic commit.
Replay identity continues to bind the original observation, not a rewritten
canonical spelling; retry the same logical observation with the same arguments.

Open todos may also carry `resume_when` when they are visible but not yet
executable. Until the parsed `resume_condition.satisfied` value is true, status
and quota keep the todo out of `first_executable_items`,
`executable_backlog_items`, `capability_gate.runnable_candidates`, and
`agent_lane_next_action`. When `resume_ready=true`, the open todo may enter the
normal executable lane for its claimed agent.

Status and quota expose deferred todos as a visibility lane after sorted open
todo lanes. This is a deferred gate-resume lane: it is not runnable work, and
it is not evidence that the current agent has no todo, until an agent reopens,
supersedes, or records a no-follow-up rationale for the deferred item.

```bash
loopx register-agent \
  --goal-id <goal-id> \
  --agent-id codex-main-control \
  --agent-id codex-side-bypass \
  --execute
```

Then claim through the dedicated command. `--claimed-by` records the durable
owner, while `--agent-id` attributes the peer performing this mutation. For a
self-claim they are both required and must name the same registered agent:

```bash
loopx todo claim \
  --goal-id <goal-id> \
  --todo-id <todo_id> \
  --claimed-by codex-main-control \
  --agent-id codex-main-control
```

Old projects that do not yet have `coordination.registered_agents` are
intentionally blocked when an agent tries to claim work. The CLI error includes
the `register-agent --agent-id <agent-id> --execute` command so the agent or
controller can register its identity before writing ownership metadata. Agent
registration writes the source registry named by the global projection; if the
shared global registry is not writable, it fails before changing that source so
the control plane does not drift into a half-registered state.

Use `--clear-claim` when an owning peer reassigns a todo or releases work.
`claimed_by` is visibility, not a runtime lease: it does not bypass quota, user
gates, write-scope checks, validation, or actor authorization. On registered
multi-agent goals, `todo claim/update/complete/supersede` require `--agent-id`;
the actor must not be excluded and must match the current `claimed_by` owner
when one exists. An exact linked `user_gate` decision scope is the narrow
exception: its typed approve/reject/cancel completion is attributed to the
owner/controller decision instead of an agent actor. Old projects without
`coordination.registered_agents` still fail closed before ownership metadata
can be written.
`todo claim` is non-destructive: if another registered agent already owns the
todo, it fails closed instead of silently replacing `claimed_by`. Transfer
ownership with an explicit `todo update --clear-claim` or
`todo update --claimed-by <agent-id>` decision. The CLI writes claim changes and
completion handoffs under the same active-state file lock as todo
add/update/complete, so concurrent CLI writers re-read the latest state before
editing instead of overwriting a stale snapshot.

The command resolves the active state from the project registry, creates the
canonical section when needed, updates `updated_at`, and avoids duplicate exact
todo text. If a dashboard or controller needs the new checklist immediately,
refresh the status projection after the write:

```bash
loopx refresh-state --goal-id <goal-id> --agent-id <registered-agent>
```

For multi-agent goals, `refresh-state` requires an explicit `--agent-id`.
The default scoped refresh is an agent-lane run: it is useful for keeping the
same turn's writeback/accounting identity intact, but it does not replace the
goal-level status route.

## Lifecycle Contract

Agents should not patch active-state checkboxes directly to move work forward.
Use the lifecycle commands so LoopX can preserve `todo_id`, status,
classification metadata, timestamps, and idempotency in one write.

Todo lifecycle should stay simple. Do not add a separate feature state machine
such as `slice_done`, `rolled_out`, or `proven_in_product_path` unless the
product has a concrete UI/runtime need for it. For ordinary project work, use
**todo succession** instead: complete the implementation slice, then create the
next concrete todo for rollout, product-path audit, benchmark proof, docs,
telemetry, or operator decision.

A non-trivial feature todo is done only as a slice. Before or during completion,
the agent should do one of two things:

- create the next public-safe agent or user todo with `--next-agent-todo`,
  `--next-user-todo`, or a follow-up `todo add`;
- record a compact no-follow-up rationale with `--no-follow-up` plus `--note`
  / `--reason` / `--evidence`, explaining why the feature is truly finished
  and does not need rollout, audit, docs, or product-path proof.

This succession decision is durable Todo state. A later progress observation,
vision ACK, coverage-exhausted result, or rewritten rationale cannot substitute
for it. Every new completion therefore retains an opaque completion identity.
A quota-bound ordinary completion proves the exact admitted identity and Todo
acceptance (including declared validation), not completion of Turn accounting.
It may precede the same-turn writeback and spend. The existing typed replay
phase remains `settlement_pending` until those receipts exist; Todo `done` alone
does not mean the Turn settled. Terminal `--no-follow-up` still requires the
complete matching writeback/spend chain. An ordinary unscoped completion gets a
stable `local_completion_*` identity; if a later `refresh-state` discovers that
the finished Goal has no real successor, its typed rejection may project
`--completion-identity-key` for one direct lifecycle reentry. That command is
valid only for the exact already-completed Todo, its matching local identity,
`active_goal` continuation, no successor, and the authorized lifecycle actor.
It cannot be supplied for an open Todo or used as a quota turn identity.
Otherwise add/link a real successor. Do not create a user gate merely to
silence a succession warning.

Host adapters own the internal sequence: validate/complete, write back, spend,
then terminal closeout if requested. A failed internal step is not a request to
redo accepted task work: retry the same identity and recover the missing receipt.
MCP returns success only after the whole requested sequence succeeds. Task
acceptance must not prescribe LoopX bookkeeping, and delivery work must not be
relabeled `same_agent_non_delivery` to escape a contradictory internal ordering.
This intentionally removes the old task-class-dependent CLI prerequisite while
retaining declared validation, claim/lease checks, identity and terminal fences.

This keeps the active checklist honest without making LoopX a heavyweight
project-management state machine.

Complete the current todo and atomically register the next executable todo:

```bash
loopx todo complete \
  --goal-id <goal-id> \
  --todo-id <todo_id> \
  --agent-id <registered-agent> \
  --evidence "<public-safe artifact or result>" \
  --next-agent-todo "<public-safe next executable action>" \
  --next-task-class advancement_task \
  --next-action-kind run_eval
```

Human review is not automatically an execution gate. When a validated feature
PR can wait for review while independent work continues, atomically derive a
bound reminder and a runnable successor:

```bash
loopx todo complete \
  --goal-id <goal-id> \
  --todo-id <todo_id> \
  --claimed-by <registered-agent> \
  --agent-id <registered-agent> \
  --evidence "<validated PR URL and checks>" \
  --next-user-todo "Review the validated feature PR." \
  --next-user-task-class user_action \
  --next-agent-todo "Continue the next independent feature slice." \
  --next-claimed-by <registered-agent>
```

`--next-user-todo` requires an explicit
`--next-user-task-class user_gate|user_action`. Use `user_action` for a reminder
that stays visible without setting `blocks_agent`; reserve `user_gate` for an
exact owner/controller authority boundary such as merging an aggregate branch
into `main`, release, benchmark launch, credentials, or protected production
action. Omitting the task class fails before writeback so LoopX never guesses
an authorization boundary. Add a separate `continuous_monitor` todo when the
PR lifecycle needs periodic readback.

For an experimental feature stack, a stable integration branch may collect
small feature PRs while review reminders remain open. Each feature still uses a
dedicated worktree and branch; its PR targets the integration branch. The
aggregate integration-branch PR to `main` is the review/merge boundary. This
keeps review latency from suspending unrelated work without weakening the final
delivery gate.

Terminal PR state does not silently complete a review reminder: merged PRs may
still need post-merge review. When the owner explicitly acknowledges that an
exact review action is complete, persist a typed acknowledgement receipt with
the exact bound `user_action` and GitHub PR:

```bash
loopx issue-fix pr-review-ack \
  --url https://github.com/owner/repo/pull/123 \
  --goal-id <goal-id> \
  --todo-id <review-todo-id> \
  --agent-id <bound-agent> \
  --owner-acknowledged
```

The receipt is fail-closed, idempotent per todo revision, and read back after
append. It binds the goal, todo revision, agent, provider, repository, PR
number, and canonical permalink without parsing reminder prose. Reopening or
materially editing the todo invalidates the prior acknowledgement.

Use `pr-review-reconcile` as the single reconciliation path. It may be invoked
explicitly or by a due `continuous_monitor` through any scheduler or host
adapter. Supplying `--owner-acknowledged` first records the same typed receipt.
Reconciliation validates the current todo revision before provider access and
again before completion, then closes the reminder only for the exact terminal
PR. A missing receipt, stale revision, unavailable provider, or unsupported
forge leaves the reminder open. Quota projection has no provider side effects.

Heartbeat hosts with `external_evidence_poll` may run the bounded batch form
before quota:

```bash
loopx heartbeat-prequota -g <goal-id> -a <bound-agent>
```

The batch reads only persisted exact acknowledgement bindings, skips stale or
already reconciled todos before provider access, and performs no quota spend.
Provider failures are reported as degraded results and do not block the
subsequent quota guard. Binding, acknowledgement, and reconciliation are kernel
contracts; `loopx-project` may document the workflow but is not a runtime
dependency.

If an agent takes ownership at completion time, include the claim in the same
locked lifecycle write:

```bash
loopx todo complete \
  --goal-id <goal-id> \
  --todo-id <todo_id> \
  --claimed-by codex-peer-a \
  --agent-id codex-peer-a \
  --evidence "<public-safe artifact or result>" \
  --next-agent-todo "Continue the next bounded task." \
  --next-claimed-by codex-peer-b \
  --next-continuation-policy independent_handoff
```

LoopX does not infer continuation authority from agent identity. `action_kind`
remains an open domain token describing the work. The closed
`continuation_policy` enum describes only the relationship between the completed
task and its successor:

- `independent_handoff` is the default. The successor stays unclaimed unless
  `--next-claimed-by` selects a registered peer;
- `same_agent_non_delivery` keeps an evidence-backed non-delivery continuation
  with the completing peer.

Review, verification, merge, and publication remain ordinary `action_kind`
values. They do not alter scheduler ordering. Use `independent_handoff` for a
successor that another peer may claim, and add one or more `excluded_agents`
only when executor separation is required. `claimed_by` may not name an
excluded peer.

`same_agent_non_delivery` is intentionally structural rather than
review-specific. It covers readiness checks, audits, triage, and other
non-delivery continuations when explicitly selected. LoopX does not infer it
from `action_kind`, and it does not authorize repository delivery or bypass
successor quota/capability checks.

A handoff becomes a blocking dependency only when it carries both
`unblocks_todo_id` and executor exclusions. Ordinary independent successors do
not inherit an owner implicitly. Use `--next-claimed-by` when the next owner is
already known; leave the successor unclaimed when a later claim should select
it.

For small changes that satisfy the repository's self-merge rules, a peer may
self-merge and complete without a successor review todo by making the
exception explicit:

```bash
loopx todo complete \
  --goal-id <goal-id> \
  --todo-id <todo_id> \
  --claimed-by codex-peer-a \
  --agent-id codex-peer-a \
  --self-merged \
  --evidence "<public-safe commit, validation, and self-merge summary>"
```

`--self-merged` requires `--evidence`. Do not use it for runtime,
benchmark, permission, production, destructive git, publication, public
evidence-policy, or broad coordination changes that need an independent
handoff.
After a validated self-merge, write back the real delivery outcome at the
project level when the slice advanced the public product or case path. An
agent-lane refresh with `--agent-id` records peer-local notes; a goal-scope
refresh records the durable goal route. Both require a registered peer. Do
not append a follow-up goal-level `surface_only` sync after a validated
`outcome_progress` slice; either skip the duplicate sync or mirror the product
progress with:

```bash
loopx refresh-state \
  --goal-id <goal-id> \
  --classification <public-safe-progress-classification> \
  --delivery-batch-scale multi_surface \
  --delivery-outcome outcome_progress \
  --agent-id <registered-agent> \
  --progress-scope goal
```

This keeps validated peer product work from being misread as another
surface-only heartbeat turn.
When a self-merged slice has an obvious same-scope continuation, it may also
atomically add that successor todo and claim it back to the same peer:

```bash
loopx todo complete \
  --goal-id <goal-id> \
  --todo-id <todo_id> \
  --claimed-by codex-peer-a \
  --agent-id codex-peer-a \
  --self-merged \
  --evidence "<public-safe commit, validation, and self-merge summary>" \
  --next-agent-todo "Continue the next small docs/productization slice." \
  --next-claimed-by codex-peer-a
```

Without `--self-merged`, no implicit review route is created. For independent
review, use `--next-action-kind review --next-continuation-policy
independent_handoff`; add `--next-excluded-agent <author>` only when the review
must remain open to multiple peers while excluding the author. When that
successor belongs to a specific repository or needs execution capabilities,
set `--next-task-repository <git:host/path>` and repeat
`--next-required-capability <capability>` in the same completion command. The
successor is then fully routable before its executor exclusions take effect.

Use `todo update` for lower-level, non-terminal status changes:

```bash
loopx todo update \
  --goal-id <goal-id> \
  --todo-id <todo_id> \
  --agent-id <registered-agent> \
  --status blocked \
  --reason "<public-safe blocker>" \
  --task-class blocker
```

Agent Todo completion always goes through `todo complete`; `todo update
--status done` is rejected so review, successor, and no-follow-up policy cannot
be bypassed. An evidence-backed peer
`continuous_monitor` with no required write scope may close with
`todo complete --no-follow-up` when its bounded watch ends without a material
transition. This closeout records `self_merged=false` and does not
create a successor review todo for observation-only work.

User-role todos may still write `done` through `todo update --status done`;
when the todo declares `validation_command` (or the `validation_command_argv`
declared via `--validation-command-json`),
that update runs the same completion validation gate as `todo complete` and
fails closed with a typed `validation_blocked_completion` receipt instead of
committing `done`. Todos without a declared command keep the unchanged fast
path.

Use `--resume-when` when deferring a successor that should wake up after a
machine-readable condition instead of living only in prose:

```bash
loopx todo update \
  --goal-id <goal-id> \
  --todo-id <todo_id> \
  --agent-id <registered-agent> \
  --status deferred \
  --resume-when todo_done:<blocking_todo_id> \
  --reason "<public-safe deferred rationale>"
```

When an open advancement slice is waiting for a monitor observation, keep it
visible and atomically bind both the wait and an independent runnable
successor:

```bash
loopx todo update \
  --goal-id <goal-id> \
  --todo-id <waiting_todo_id> \
  --agent-id <registered-agent> \
  --resume-when monitor_changed:<monitor_todo_id> \
  --successor-todo-id <runnable_successor_todo_id> \
  --reason "<public-safe external-wait rationale>"
```

LoopX preserves the waiting todo as `status=open`, excludes it from runnable
candidates while `resume_ready=false`, and automatically restores it to the
runnable lane after the monitor generation advances. Clear a satisfied
condition with `todo update --clear-resume-when` before deliberately re-arming
the same monitor wait.

Use `todo supersede` when the current open todo should be retired and replaced:

```bash
loopx todo supersede \
  --goal-id <goal-id> \
  --todo-id <todo_id> \
  --agent-id <registered-agent> \
  --reason "<public-safe reason>" \
  --next-agent-todo "<replacement executable action>"
```

If the superseded todo was claimed, the replacement inherits that `claimed_by`.
If it carried `blocks_agent` / `unblocks_todo_id`, the replacement inherits
those unblock fields too. Use `--next-claimed-by <agent-id>` to make a handoff
explicit.

`todo complete` is terminal-idempotent for repeated heartbeats: once the source
todo is complete, a later completion returns `changed=false` and cannot append
or relink a different successor. Correct a completed route with an explicit
follow-up todo or lifecycle command instead of replaying `complete` with new
arguments. `todo supersede` keeps its own idempotent successor insertion.
`todo archive-completed` is only a hygiene command:
it moves already-completed active todos into `Completed Work Archive`; it does
not mark open todos as done.

When an open todo has an effective hard task lease, completion must prove the
execution instance, not only the shared agent identity:

```bash
loopx todo complete \
  --goal-id <goal-id> \
  --todo-id <todo_id> \
  --claimed-by <agent-id> \
  --task-lease-idempotency-key <acquire-key> \
  --task-lease-expected-version <lease-version> \
  --evidence "<public-safe evidence>"
```

The key and version are both required while the lease is active. A missing or
mismatched pair fails before Todo or successor state is written, including
when two host processes intentionally share one `agent_id`. `todo supersede`
moves the same todo to done and crosses the same fence with the same two
options; a leased todo cannot be retired with a partial fence through either
verb. Release retains an inactive terminal record, and the next acquisition
must use a new execution key and receives a greater version and
authority-owned `lease_epoch`.

## Parsed Schema

Projects may keep writing ordinary Markdown checkboxes, but readers should use
the structured projection emitted by status/quota when available. Todo summaries
carry `schema_version=todo_summary_v0`; individual items carry
`schema_version=todo_item_v0`, `todo_id`, `role`, `status`, `priority`,
`title`, `archive_state`, `source_section`, `index`, `text`, `task_class`, and
optional `action_kind`, `claimed_by`, `required_capabilities`, and
`target_capabilities`. A capability-admitted agent Todo may also carry an opaque
`capability_binding_ref`; it identifies the capability-owned authority row and
is preserved across generated agent successors. Once set, the binding is
immutable and is distinct from executor `required_capabilities`.
`action_kind` is extensible; optional
`continuation_policy` is limited to `independent_handoff`,
or `same_agent_non_delivery`. Agent todos may also carry `excluded_agents`,
`unblocks_todo_id`, and `no_followup=true` to express executor separation,
dependency lineage, and intentional closeout. `blocks_agent` is reserved for
scoping user gates.

After a hard-cut upgrade, `loopx check` reports agent todos that still carry
removed gate-routing fields. New readers also preserve a read-only
`removed_continuation_policy` diagnostic for legacy `review_handoff` and
`primary_review` records and exclude those records from claim/quota execution.
This fail-closed compatibility marker is not a supported continuation type and
is never written back. Repair legacy review records explicitly:

```bash
loopx todo update \
  --goal-id <goal-id> \
  --todo-id <todo-id> \
  --agent-id <registered-agent> \
  --role agent \
  --continuation-policy independent_handoff \
  --excluded-agent <author>
```

For removed `blocks_agent` routing, use `loopx todo update --todo-id <todo_id>
--role agent --clear-blocks-agent`. LoopX does not infer the excluded author or
rewrite either form automatically.

Deferred successors may carry `resume_when`, `resume_condition`, and
`resume_ready`; `resume_ready=true` means the deferred item should be considered
for a successor replan before any agent-scoped no-candidate wait, not that
normal delivery may skip the todo lifecycle. A ready deferred successor also
preempts a strictly lower-priority open advancement todo for this lifecycle
replan. It does not preempt an equal-priority open todo, and it never enters the
normal executable backlog until a lifecycle command reopens it.
The `todo_id` is first-class when written by the CLI.
`claimed_by` values are normalized public-safe agent ids and should correspond to
`coordination.registered_agents`. Legacy Markdown without metadata still gets a
parser-derived compatibility id from local section/index/text, and the first
lifecycle command will materialize that id back into metadata. Future lease
timestamps, dependency, capability detail, and evidence-link fields should
extend this item shape instead of adding another todo format.
In Markdown, lane metadata is stored as an indented HTML comment directly under
the checkbox, for example:

```markdown
- [ ] Run one validated benchmark case and write back result or blocker.
  <!-- loopx:todo todo_id=todo_8e280be49441 status=open task_class=advancement_task action_kind=run_eval required_capabilities=shell%2Cbenchmark_runner claimed_by=codex-main-control -->
```

Plain checkbox text remains a compatibility fallback. New automation-facing
work should prefer the CLI metadata path so quota and dashboard consumers do
not need project-specific word lists.

Executable agent todos may declare per-todo environment needs with
`--required-capability`. Keep this field near the todo, not in a global agent
profile: the same agent may have shell/filesystem capability for docs work, but
lack `benchmark_runner`, `external_evidence_poll`, `network`, or another bridge
for a specific step.

```bash
loopx todo add \
  --goal-id <goal-id> \
  --role agent \
  --text "<public-safe executable agent action>" \
  --task-class advancement_task \
  --action-kind run_eval \
  --required-capability shell \
  --required-capability benchmark_runner
```

Use `--target-capability` when the todo is meant to build, repair,
materialize, or parity-check a capability rather than use that capability as a
prerequisite. For example, a benchmark product-path parity todo can hard-require
only shell while targeting the benchmark runner bridge:

```bash
loopx todo add \
  --goal-id <goal-id> \
  --role agent \
  --text "<public-safe benchmark parity repair action>" \
  --task-class advancement_task \
  --action-kind benchmark_treatment_product_path_parity \
  --required-capability shell \
  --target-capability benchmark_runner
```

`status` projects `required_capabilities` on every visible todo. `quota
should-run` then derives a read-only `capability_gate` from the visible
executable queue, not from a single preselected todo. With multiple P0 or P1
items, it scans the projected queue in order and exposes the candidate set:
capability-satisfied todos appear in `capability_gate.runnable_candidates`,
while blocked higher-priority candidates remain visible in
`capability_gate.blocked_candidates`. The gate does not choose the final todo;
the agent keeps decision authority and must pick from the runnable set during
its steering audit. If no visible executable candidate can run, the gate returns
`repair_bridge`, `ask_owner`, or `skip` according to the missing capability
class.
`target_capabilities` stay visible in runnable candidate payloads. If a target
bridge is absent, the candidate is annotated with `capability_repair_mode=true`
and `missing_target_capabilities`, but that target is not treated as a hard
execution blocker.

## Execution Order

1. Run the quota guard against the shared global registry before spending
   automatic delivery compute.
2. If the guard or review packet exposes open user todos, surface them to the
   user instead of reporting "no new user action".
3. If the guard sets `notify_user_on_open_todo=true`, treat the open todos as a
   blocker-push notification: ask at most three items, skip delivery work, and
   skip quota spend unless the same blocker was already surfaced recently.
4. Do not execute `agent_command`, adapter work, write-control, or production
   actions while the relevant gate is still unresolved.
5. After the user todo is completed or explicitly deferred, the project agent
   may continue only through the safe path allowed by the current guard or
   review packet.

## Public Smokes

Two dependency-free public fixtures cover this contract:

```bash
python3 examples/control_plane/todo-cli-smoke.py
python3 examples/control_plane/todo-lifecycle-cli-smoke.py
python3 examples/project/project-agent-adoption-smoke.py
python3 examples/control_plane/todo-concurrent-write-lock-smoke.py
python3 examples/capability-gate-smoke.py
```

The first verifies the todo CLI writes canonical active-state sections. The
second verifies lifecycle transitions by `todo_id`, including claimed
completion, supersede, idempotent next-todo insertion, and non-executable
blocker lanes.
The third verifies an executor-facing path from quota guard hint, to user todo
write, to status projection, to approved project-agent handoff.
The fourth verifies concurrent todo writers wait on the active-state lock and
preserve both claim metadata and unrelated updates.
The fifth verifies per-todo `required_capabilities`, including multiple P0/P1
candidate selection, bridge repair, and owner-gated capability misses.

### Lease-fenced canonical text/note updates

After explicit canonical-authority promotion, the active lease holder can edit
`text` and `note` using the existing execution key and current lease version:

```bash
loopx todo update --goal-id <goal> --todo-id <todo> --agent-id <agent> \
  --text 'Correct task description' --note 'Correction context' \
  --task-lease-idempotency-key <execution-key> --task-lease-expected-version <version> \
  --update-operation-id <stable-update-id>
```

Reuse the update id, execution proof and edit intent after a lost response.
The original receipt can be replayed after lease expiry or transfer; it grants no
current execution authority. Changed proof or edit intent with that id conflicts.
Omitting the update id preserves a fresh id per CLI invocation. Preview writes
nothing and does not consume the id. Updates preserve the lease exactly: they
cannot acquire, renew, release or transfer it. Missing, stale or expired proof
fails closed. These options do not enable promotion or a legacy Markdown fallback;
legacy updates without the new options retain their existing behavior.
An empty or Unicode-whitespace-only `--note` is an omitted note update and keeps
the persisted note, before and after promotion. It never means clear; clearing a
note requires a separate explicit contract.

显式切换到 canonical authority 后，当前租约持有者可使用执行 key 和当前租约版本
修改 `text`／`note`。响应丢失后复用相同 `--update-operation-id`、凭证和修改内容；
历史回执可在租约过期或转交后回放，但不授予当前执行权。相同 ID 搭配不同凭证或
内容会冲突；省略 ID 则每次 CLI 调用生成新 ID。Preview 不写入、不消耗 ID。
更新不获取、续期、释放或转交租约；缺失、陈旧或过期凭证拒绝。此入口不自动
promotion，也不回退 Markdown；不带新选项的 legacy 更新保持原行为。
空字符串或仅含 Unicode 空白的 `--note` 视为省略 note 更新，在 promotion 前后都保留
已持久化 note；它不表示清空。清空 note 需要另行定义显式契约。

### Reviewed canonical edits and recovery

For promoted updates, `--authority-reason` now stays on the canonical path.
Registry lifecycle grants can authorize `update` or reassign-only intent;
combining a reassignment with copy/planning changes requires `update` authority.
A reason never bypasses registration, exclusion, binding or lease-proof checks.
Use the `provider_revision` returned by `todo list` to bind a reviewed edit:

```sh
loopx todo update --goal-id <goal> --todo-id <todo> --agent-id <agent> \
  --note 'Reviewed correction' --authority-reason 'Verified the requested correction' \
  --update-expected-provider-revision <reviewed-revision> \
  --update-operation-id <stable-edit-id> --dry-run
```

Remove `--dry-run` to commit. Repeat the **same** revision, operation ID, reason
and intent after a lost response. A matching receipt is historical success;
without it, a stale revision rejects the new write. Reverse a change through a
new reviewed operation, never by editing a stale Markdown projection. The v2
wire fails closed on older runtimes; old v0/v1 receipt identities remain valid.

Chat stores this basis itself; caller context cannot supply authority. Its Todo
and Monitor nonterminal previews run the real dry-run, and pending display never
becomes `projection_verified=true`. Retry the original card to recover its
receipt and project the current head. Completion and run-now keep their separate
owners; these changes neither create lease proof nor enable provider promotion.

晋升后的 `--authority-reason` 保持 canonical 路由。Registry grant 可授权 update
或纯 reassign；重分配夹带其他修改需要 update 权限，理由不替代注册、exclusion、
binding 或 lease proof。上例用 `todo list` 的 provider_revision 绑定审阅基线；
去掉 dry-run 执行，丢响应后原 revision、ID、理由和意图一起重试。匹配回执证明
历史成功；没有回执时，陈旧 revision 拒绝新写入。撤销使用新的审阅操作。
Chat 自行记录基线，不能从 caller context 注入权限。非终态预览执行真实 dry-run；
展示 pending 不冒充验证成功，原卡片重试恢复回执并投影当前 head。完成和立即运行
仍归各自 owner，不补造 lease proof，也不开启 provider promotion。

### Canonical registry source witnesses

Promoted local Todo create, claim, update, Monitor poll and complete/supersede
share one registry-source check. Python captures the registry SHA-256 before
reading registration/grant facts and verifies it after projection. TypeScript
verifies the same witness before admitting new work, issuing a validation effect,
and returning a successful preview or committing. Completion reuses the original
witness after external validation: a successful validator does not authorize a
commit under registration that changed while it ran.

| Local request | Witnessed wire | Retained legacy wires |
| --- | --- | --- |
| `loopx_local_coordination_todo_create_request` | v1 | v0 |
| `loopx_local_coordination_todo_claim_request` | v1 | v0 |
| `loopx_local_coordination_todo_update_request` | v2 | v0, v1 |
| `loopx_coordination_monitor_poll_request` | v2 | v0, v1 |
| `loopx_local_coordination_todo_terminal_lifecycle_request` | v1 | v0 |

Witnessed requests require `registry_source` with an absolute `path` and a
lowercase SHA-256 `sha256`. Legacy versions retain their existing fact contract
and reject this field; Monitor v1 still requires its execution proof, while v2
supports the existing leased and unleased paths. The witness does not enter
immutable operation identity or public receipts. A matching historical
create/update/poll/terminal receipt is recovered before checking current source
contents. Claim replay retains its current acceptance check and rejects a stale
source while preserving `original_receipt`; this adds no new claim grant.

On `authority_source_changed`, inspect current registration and the Todo again
before issuing new work. The rejected attempt writes no canonical head or receipt;
an external validator already executed may have its own effects. This is an
optimistic byte-snapshot check, not a lock, grant, cross-resource transaction or
linearizable revocation guarantee. Unrelated registry changes can also require
retry. Provider CAS still owns atomic head/receipt persistence. Service-owned
PostgreSQL callers retain their authenticated fact boundary; a local selector
cannot manufacture one. Provider defaults and promotion do not change.

晋升后的本地 create、claim、update、Monitor poll 和 complete/supersede 共用同一
registry 来源校验。Python 在采集注册/grant 事实前后比较来源摘要；TS 在新操作准入、
发出 validation effect 和成功预览/提交前复核。外部验证结束后沿用原 witness，
不能刷新注册事实来掩盖验证期间发生的撤权。

上表新 wire 必须携带绝对路径与 SHA-256，旧 wire 保留原合同并拒绝夹带此字段。
来源不是操作身份：create/update/poll/terminal 先恢复匹配的历史回执；claim 仍检查
当前 acceptance，来源失效时拒绝继续，但保留 `original_receipt`。遇到
`authority_source_changed` 应重新检查注册和 Todo；拒绝不会写 canonical head/回执，
但已经运行的外部验证可能有自己的副作用。整个 registry 的无关修改也可能导致重试。
该机制是乐观字节快照校验，不是跨资源原子事务或线性一致的撤权保证；provider CAS
继续负责状态与回执的原子持久化，不改变默认 provider、promotion 或 PostgreSQL 的
service-owned 认证边界。

### Canonical nonterminal planning updates

An explicitly promoted agent Todo also accepts a bounded planning update through
the same transaction: `--status open|blocked|deferred`, `--evidence`, `--reason`,
`--resume-when` / `--clear-resume-when`, `--unblocks-todo-id`, successor links and
`--no-follow-up`. Text/note may be supplied in the same atomic operation.

```bash
loopx todo update --goal-id <goal> --todo-id <todo> --agent-id <agent> \
  --status deferred --resume-when 'pr_merged:#123' --reason 'Await upstream' \
  --update-operation-id <wait-attempt-id>
loopx todo update --goal-id <goal> --todo-id <todo> --agent-id <agent> \
  --status open --clear-resume-when --update-operation-id <resume-attempt-id>
loopx todo list --goal-id <goal>
```

Preview with `--dry-run` before the real attempt. A cleared resume condition also
clears its generation fence; an omitted condition is retained. Empty successor
arrays and explicit `no_followup=false` in API intent remain meaningful values.
Dependency validation sees the complete canonical inventory, not a hot-path
summary or a Markdown buffer. A satisfied Monitor wait is not silently re-armed
by an evidence edit; changing its topology requires clearing that old condition.
Planning uses a versioned request envelope (current transport: v2): an older
runtime rejects the whole request instead of silently applying only its copy patch.
A planning-only update preserves the existing `last_actor_agent_id`, matching the
legacy public planner; a combined raw text/note correction retains its established
copy-edit attribution behavior.

Authority is unchanged: registered, non-excluded peers may edit unclaimed work
without claiming it; another owner's claim is not writable. A lease-bearing edit
requires current execution proof and preserves the entire lease. **Changing a
leased Todo's status is unsupported** until status and lease effects can commit
together. Monitor planning/observations, ownership, routing, capability fields
and terminal operations are outside this update intent. Use the existing
dedicated lifecycle operations where supported; no unsupported update falls
back to Markdown. Missing display files do not block a canonical update; normal
post-commit projection delivery restores the managed Todo display.

显式 promotion 后，agent Todo 可在同一事务中修改上述非终态规划字段，并与 text/note
合并提交。使用 `--dry-run` 预览，重试沿用相同 operation id 与意图；新的修改使用新 ID。
清除 resume 时一起清除 generation fence，省略则保留。校验读取完整 canonical
inventory，不依赖摘要条数或 Markdown。补 evidence 不会重新设置已满足的 Monitor
等待；更改其拓扑需要先清除旧条件。API 中空 successor 数组和 `no_followup=false`
不是省略值。当前规划 transport 使用 v2 请求，旧 runtime 必须拒绝整次请求，不能只提交 text/note。
仅包含规划字段的更新保留既有 `last_actor_agent_id`，与 legacy public planner 一致；
若同时包含 raw text/note 修正，则继续沿用既有文案修正的 actor 归属语义。
权限不扩大：未 claim 的工作仍可由未被排除的注册 agent 修改，不能改写
其他 owner 的工作。带租约的编辑须提供当前 proof，保持租约不变；**暂不支持改变
带租约 Todo 的 status**。Monitor 规划/观察、ownership、routing、capability 与终态
操作不在此 intent 内。缺失 display 不阻止 canonical 更新；提交后的正常投影恢复
托管 Todo 展示。不支持的操作不会回退 Markdown，也不自动切换 provider 或 promotion。
