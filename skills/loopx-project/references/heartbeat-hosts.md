# Heartbeat Hosts

Read before creating, repairing or changing a recurring host loop. Save only the stable loader and read its fresh result on every wake; the current body owns execution, runtime turn identity and settlement.

## Set Up Recurring Heartbeats

When the exact host is Codex App connected to a remote workspace over SSH and
`automation_update` is unavailable because of that host boundary, use the
visible Goal integration instead of reporting a missing-automation blocker:

```bash
loopx --format json agent-onboard \
  --agent-type codex-app-ssh \
  --project . \
  --goal-id <STABLE_GOAL_ID> \
  --agent-id <REGISTERED_AGENT_ID>
```

Run the returned `activation_input_command`, read `task_body`, and set the
current visible task to `/goal <task_body>`. In Codex App, use the available
Goal control directly; do not ask the user to run `heartbeat-prompt` or paste
the body manually when the host exposes Goal control. Surface the exact
pasteable gate only when Goal control is also unavailable.

The generated `codex_app_ssh_goal` body is bounded by the `/goal` 4000-character
limit. It uses an interactive `agent_cli_loop` scheduler context, omits
heartbeat turn receipts, and must not create/update automations, apply RRULE
cadence, or invent `LOOPX_TURN`.

For a recurring Codex App heartbeat, save the stable bootstrap returned by:

```bash
loopx --format json --registry <GLOBAL_REGISTRY> heartbeat-prompt \
  --bootstrap --thin --codex-app --goal-id <STABLE_GOAL_ID> \
  --agent-id <REGISTERED_AGENT_ID>
```

Read the complete JSON and require `ok=true`. Store its `task_body`, headed
`LoopX managed heartbeat bootstrap v2`, with the App's `automation_update` tool.
The saved command must load `heartbeat-prompt --thin --codex-app` on each wake;
it must not include `--bootstrap` recursively. Do not persist the expanded
thin/compact/brief/full execution body: those are current-turn or audit output,
not the installed automation contract. `$loopx` startup follows the returned
host activation command and saves this same bootstrap.

Preserve the exact goal, registered agent, current task, schedule and
notification setting. Never copy the example identity from another task.
Connected goals resolve active state and agent scope from the registry on each
wake; pass `--active-state` or `--agent-scope` only for an explicit override.
An unregistered or missing identity must fail closed before task execution or
accounting. A successful load is not permission to create another goal or take
over another scheduler.

For an existing automation, inspect `loopx automation-prompts plan --codex-home
<ACTIVE_CODEX_HOME>` and apply its reviewed `desired_prompt` through the App
`automation_update` tool. Read back the same automation, including its preserved
binding and scheduling fields. Direct SQLite/TOML migration requires the App to
be closed: a running host can overwrite disk edits from cached state. Do not
claim completion from a changed file or a replaced CLI alone. Never copy
sessions or rebind another Codex home's tasks to make its API reachable.

Each wake reads the full fresh result and follows only its current `task_body`
when `ok=true`. Separate notification from execution, follow the current waiting
contract, and attempt recovery within existing authority when loading fails.
If the contract remains unavailable, do not execute or account for work; report
the blocker. The loaded contract owns quota settlement and runtime turn identity;
never freeze a turn id in the saved bootstrap.

Keep project-specific behavior out of the automation prompt. Encode local
differences in the project registry, its registered active-state file, adapter
output, or narrow public/private boundary rules. New state defaults to
`.loopx/goals/<goal-id>/ACTIVE_GOAL_STATE.md`; preserve a declared legacy path. If a lifecycle rule is
useful across projects, update `loopx heartbeat-prompt` and its smoke
contract rather than hand-editing one heartbeat automation.
For delivery-specific boundaries, prefer the registry fields surfaced in
`quota should-run.goal_boundary`; the automation prompt only needs to say to
obey that payload and stop when useful work falls outside it.

When a LoopX client/prompt contract changes, update the matching smoke
coverage in the same patch. Interface-budget and regression constraints belong
in lightweight smoke scripts such as `examples/control_plane/heartbeat-prompt-smoke.py`,
`examples/control_plane/quota-plan-smoke.py`, or `examples/control_plane/quota-contract-smoke.py`; heavier
Codex CLI plus LoopX end-to-end checks should stay explicit or
low-frequency instead of becoming the default heartbeat path.

The quota guard returns `execution_obligation` and
`heartbeat_recommendation`. It also returns `scheduler_hint`, which controls
the next wakeup cadence and external-loop unchanged-poll self-stop; this is
scheduling policy, not delivery permission. Codex CLI TUI and Claude Code loops
should run the final quota/replan check from `scheduler_hint` before applying
their `after_limit`; if the guard changes or returns `run_now`, follow the new
quota contract instead of stopping. App-hosted heartbeat workers should
search/use `automation_update` when available. A bootstrap RRULE is the initial
host cadence; `min_interval_minutes` is a floor, not a fixed recurring interval.
Keep ACTIVE while applying the current wait/backoff recommendation. Only an
explicit current fixed-cadence instruction overrides that recommendation; record
the conflict rather than silently treating a bootstrap preference as an override. If
`scheduler_hint.action=stop_until_explicit_resume` and
`scheduler_hint.app_automation.host_action=pause_or_delete_current_heartbeat`, call
`automation_update` once to pause the current heartbeat (delete only when the
host cannot pause), verify the host result, spend no quota, and end the turn.
This terminal host action takes precedence over RRULE handling and requires no
scheduler ACK. Otherwise use `automation_update` only when
`scheduler_hint.app_automation.stateful_backoff.apply_needed=true` and
`scheduler_hint.app_automation.recommended_rrule` is present. After a successful
RRULE update, run `loopx` with
`scheduler_hint.app_automation.ack_hint.cli_args` (normally `quota scheduler-ack-current`,
which re-reads the latest scheduler hint instead of hand-copying short-lived
reset tokens). Attempt the host update at most once per hint and turn. If it
fails or times out, do not retry or ACK; run
`scheduler_hint.app_automation.failure_hint.cli_args` once. That no-spend writeback
records the failed target/observed-host pair so later heartbeats suppress the
exact repeat until either value changes. Continue allowed delivery under the
observed host cadence. If
`apply_needed=false` but `ack_needed=true`, a matching host
readback already proves the RRULE; skip `automation_update` and run the bound
ack hint directly. LoopX owns reset/progression state
and omits `recommended_rrule` when the desired RRULE is already applied.
Cadence changes, reset-to-initial updates, final checks, and self-stop changes
do not spend quota.
For a uniquely matched active App heartbeat, `quota should-run`
automatically reconciles the installed RRULE with LoopX's ACK ledger. Treat
`stateful_backoff.host_observation.status=drift_detected` as authoritative for
cadence repair; a stale or premature ACK must not suppress `apply_needed`.
When readback matches a reset RRULE that is not yet bound in scheduler state,
the ack hint carries the exact reset token, identity signature, and CLI route;
missing readback must not use this shortcut.
This readback is cadence-only and never exposes the automation prompt or grants
LoopX permission to edit Codex App files directly.

Read
`execution_obligation` before
deciding on a quiet no-op:
`heartbeat_recommendation.notify` is only the user-notification policy, not an
execution gate. If
`execution_obligation.must_attempt_work=true`, attempt one bounded progress
batch or segment even when `notify=DONT_NOTIFY`; a quiet no-op requires
`execution_obligation.must_attempt_work=false` and the current
`interaction_contract.user_channel.notify=DONT_NOTIFY`. New connected read-only goals
should follow `recommended_mode=run_first_read_only_map`: run one real
`loopx read-only-map --goal-id <STABLE_GOAL_ID>`, validate the saved
`read_only_project_map`, then follow the current settlement plan and read back
its receipts. The recommended mode must be selected by the current guard;
connection alone does not admit the map. Already mapped goals should follow
`recommended_mode=mapped_noop_if_unchanged`: if there is no new instruction,
owner evidence, agent todo, stale source, or safe handoff, return a quiet no-op
without another dry-run, file edit, or quota spend.

The generated task body also carries a no-progress self-repair guard. Its
thresholds and obligations come from Core, not counts invented by the prompt.
`quota should-run` may expose a hard
`autonomous_replan_obligation` / `execution_obligation.must_attempt_work=true`
contract when active state or public run history shows repeated typed no-progress
turns. Quiet future-monitor heartbeat receipts are liveness evidence only;
`dead_monitor_repeat` counts unchanged due/external monitor executions with a
concrete Todo or target identity at its declared threshold. Obey that machine
contract before another quiet no-op: run one
bounded self-repair/replan batch through implementation, validation, and
writeback when that boundary is clear, then follow its current typed settlement. Do not stop at the first
tiny substep when the repair has an obvious validation boundary. Correct the
affected work first; broader replanning needs current goal, path, dependency or
acceptance evidence. A due review can retain the path. Check subsequent work
and authoritative outcomes before claiming that a correction helped. Wake
counts alone never authorize replan or self-pause. Follow explicit stops and
current Core/host stop boundaries and notification policy. Preserve owed
settlement according to the current terminal/recovery contract; continuation or
settlement guidance must not override an explicit no-spend stop. If execution is
not permitted, retain unsettled state and do not claim settlement is complete. Form a bounded working brief from current sources:
evidenced progress/change, blockers, next work, acceptance and stops. Missing
comparison evidence is unknown; a summary cannot replace full requirements.
The generated task body permits routine public commit, push and PR creation
within existing user authority and repository rules after validation and a clean
public/private boundary scan. Do not reintroduce a user gate for public-safe
publication itself. An old trust setting, context or command does not grant new
authority; merge and production retain their separate authorization.
For `notify_user_on_open_todo=true`, follow the current
`interaction_contract.user_channel` for the concrete notice and cooldown.
This notification flag does not decide delivery or accounting:
`interaction_contract.agent_channel` owns admitted work, and
`interaction_contract.cli_channel` owns its settlement. A pure
`user_todo_blocker_push` lane with no admitted work does no delivery or spend;
`bounded_delivery_with_user_notice` or an admitted safe bypass still requires
its work and current settlement. If
`open_todo_notification_policy=repeat_until_resolved`, repeat the concrete
notice only while the current user channel returns `NOTIFY`, until the todo is
done, deferred, or replaced. When
the user confirms that an existing review or action already happened, write
back that exact todo immediately: complete only with an explicit typed decision,
otherwise supersede the stale gate without granting authority; then refresh
state and rerun quota so a successor or autonomous replan can surface. When
`user_gate_notification_cooldown.notification_suppressed=true`, preserve the
pending gate but return quiet `DONT_NOTIFY`; the bounded reminder window or a
material gate/host change reopens the notification. Other blocker-push cases may
still be de-duplicated when the same blocker was already surfaced recently.
Eligible monitor-only no-transition polls keep user todos visible in the
payload but should stay quiet unless a material transition appears.

Keep the Codex App visible goal text short, for example
`按 ACTIVE_GOAL_STATE.md，基于 LoopX 体系，推进项目`. Do not use that short
text as the automation body. Across projects, save the same generated bootstrap
loader with the correct Goal/Agent binding; resolve active state and narrow
project boundary rules from current registry data on each wake. When a project
appears to need a custom automation branch, treat that as a LoopX product gap
first, not as a reason to paste one-off control logic into the scheduler.
