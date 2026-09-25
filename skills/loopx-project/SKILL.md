---
name: loopx-project
description: Use when connecting a repository or project goal document to LoopX, maintaining project-local goal state, refreshing stale dashboard status, syncing local projects into the shared global registry, or diagnosing LoopX CLI/PATH/status/history issues across multiple repos. For registering durable project materials such as Lark/wiki/design docs, prefer the narrower loopx-doc-registry skill.
---

# LoopX Project Workflow

Use this skill when the task mentions LoopX, loopx, a project goal
document, multi-project dashboard/status, stale latest run,
`.loopx/registry.json`, `.codex/goals`, `refresh-state`,
`sync-global`, or connecting a new repo. If the task is mainly about reading,
remembering, recording, indexing, or registering a durable project material,
load `loopx-doc-registry` and use that narrower workflow first.

LoopX has two layers:

- **Project-local state**: each repo owns `.loopx/registry.json` and
  `.codex/goals/<goal-id>/ACTIVE_GOAL_STATE.md`.
- **Shared local control plane**: `~/.codex/loopx` stores run history and
  `registry.global.json` for multi-project status.

Do not manually copy one project's registry entry into another project. Local
`connect` and `refresh-state` should sync into the shared global registry
automatically.

## Slash Command Fallback

When asked to upgrade LoopX on a machine with the desktop App, inspect the App
bundle and its runtime as well as the CLI. `loopx doctor` exposes
`desktop_installation` for standard macOS install locations; a paired bundle
still does not prove which App process is running. Upgrade the App and its
bundled runtime together, then restart and verify the App, CLI and service
source revisions. Never claim a desktop upgrade from CLI/HTTP checks alone:
current App builds ask before replacing a different CLI runtime (update the
App, or align the CLI to the App's bundled runtime), while an older build can
still replace a separately upgraded CLI with its bundle.
Keep SSH host verification separate; a host without an App needs no App install.

When the visible user message is exactly a LoopX slash command or starts with a
LoopX slash command plus arguments, do not treat it as ordinary chat.

Recognized project-local goal-start command:

- `/loopx <goal text>`
- `/loopx --capability-route issue-fix <goal text>`

Recognized repo-review commands:

- `/loopx-pr-review`
- `/loopx-pr-review <time window or filter text>`

If the text after `/loopx` begins with the exact optional prefix
`--capability-route issue-fix`, remove only that prefix from the goal text and
pass it as the explicit start-goal route switch. Otherwise every non-whitespace
character after `/loopx` is goal text. Never infer a product capability route
from issue/PR wording, URLs, or semantic similarity. Do not downgrade either
form into a status or inspection turn.

`start-goal --project` keeps the requested project route, including a linked
git worktree, so a fresh task cannot inherit an older worktree's goal.
Lower-level diagnostic command packs may still report a canonical
`canonical_project_alias` / `source_registry` route. Do not manually replace
either route with an unadvertised bootstrap command.

From the target project root, pass the text after `/loopx` as the explicit
goal-start objective before planning or writing project state:

```bash
loopx start-goal --guided --project . --goal-text "<GOAL_TEXT>"
```

Append `--capability-route issue-fix` only when the caller supplied that exact
explicit route switch.

Include `--goal-id <STABLE_GOAL_ID>` when known. Codex App automatically reads
the stable ambient `CODEX_THREAD_ID`, while Trae App reads
`TRAECLI_THREAD_ID`; other hosts that expose a stable opaque thread id should
pass it as `--thread-id <HOST_THREAD_ID>` on every `/loopx`
invocation. If that thread is already bound, reuse the returned
`--agent-id <REGISTERED_AGENT_ID>` on start, heartbeat, quota, refresh-state,
and Todo commands. Include `--agent-id <REGISTERED_AGENT_ID>` only when the
current session already owns that identity, the thread binding resolves to it,
or the user explicitly asks to take over that exact agent's work.

When a stable thread id is present but has no binding, treat it as a new host
session and follow the returned fresh-registration default. Select an existing
lane only when the user explicitly requests takeover of that exact agent, then
bind it with the returned `bind-agent-thread` command. When no thread id is
available, preserve the fail-closed identity gate and never infer takeover from
registry order or the only registered lane; pass `--new-peer` only when the
user explicitly requests fresh onboarding on that unboundable host. Choose a
fresh public-safe id, preview then execute `register-agent`, and
require the `--require-new --execute` result to report `ok=true`, `changed=true`,
`written=true`, successful global sync, and verified registration readback
before rerunning `start-goal` with that new id. A preview is advisory and never
allows continuation. If
`start-goal --guided` is not available, refresh the local LoopX CLI or use the
checked-out LoopX repository CLI for validation; do not silently downgrade
`/loopx <goal text>` into a bare `/loopx` read-only command. Use
`loopx bootstrap-command-pack --project . --goal-text "<GOAL_TEXT>"` only when
implementing or debugging the lower-level host handoff packet.

If the connected goal later needs a wider or corrected write boundary, do not
rerun `loopx bootstrap --force` just to change scope. Use the incremental
configuration path instead:

```bash
loopx configure-goal \
  --goal-id <STABLE_GOAL_ID> \
  --write-scope "<SAFE_WRITE_SCOPE>" \
  --execute
```

If a destructive reconnect is explicitly required, pass `--preserve-todos` with
`--force` unless the user intends to rebuild the active state file and discard
existing todo projection.

`/loopx <goal text>` is an explicit goal-start intent: first produce a concise
ordered plan, then write todos in priority order, using planner order plus
`todo add` write order as the same-priority tie-breaker. For broad or fuzzy
product directions, use a small public-safe planning set; for clear bounded
problems, use the minimum sufficient ordered todo plan and avoid
management-only filler.

Global manager slash commands such as `/loopx-global-summary`,
`/loopx-global-gates`, `/loopx-global-todos`, and `/loopx-global-risks` are not
project bootstrap commands. Route them to the global manager command contract
or status summary surface instead of `bootstrap-command-pack`. Legacy
`/loop-global-*` forms may be treated as aliases, but canonical help and
packets should use `/loopx-global-*`.

Repo-review slash commands are also not project bootstrap commands. If the
visible request starts with `/loopx-pr-review`, stop this project-bootstrap
workflow and load the narrower `loopx-pr-review` skill. That skill owns the
required first command, packet-preservation rules, and five-block per-PR review
contract. Do not handle `/loopx-pr-review` from this broader project skill, and
do not route it to `loopx-pr-merge` unless the user later asks to approve,
comment on, merge, self-merge, or admin-bypass a specific PR.

When the request asks to inventory, prioritize, document, or continuously
monitor a delivery program spanning several PRs or MRs, load the narrower
`loopx-pr-program` skill after the project goal transaction is established.
That skill owns provider-neutral snapshots, grouped monitor state, material
change detection, and roadmap projection. Keep deep per-change review in
`loopx-pr-review` and provider mutations in the separately authorized workflow.

When a user has just connected a project, receives a guided start packet, or
receives a bootstrap command pack for the first time, briefly tell them the
usable commands instead of assuming they will inspect CLI help:

- `/loopx <goal text>`: start a concrete goal with a plan-before-todo-write
  flow.
- `/loopx-global-summary`: read the global progress digest.
- `/loopx-global-gates`, `/loopx-global-todos`, `/loopx-global-risks`: inspect
  manager-level gates, work, and risks.
- `/loopx-pr-review`: use the `loopx-pr-review` skill to run `loopx pr-review`
  and review unmerged/merged PR groups one by one.

For command-line discovery, use:

```bash
loopx slash-commands
```

## Register Project Authority And Material Sources

When a project agent discovers a durable design document, research note,
benchmark paper, owner packet, migration report, or external material that
future agents may need for routing, validation, or conflict resolution, treat
that as a doc-registry skill trigger. Identify the target project and goal
first; do not register material into the current meta goal just because this
worker found it.

For material owned by the current project, update the project-local doc registry
or equivalent authority map first, then register the compact redacted source
contract in the same project's ignored `.loopx/registry.json`:

```bash
loopx register-authority-source --goal-id <STABLE_GOAL_ID> ...
```

For another project's DOC_REGISTRY-style map, import only the compact authority
summary instead of copying raw paths, document ids, URLs, comments, or source
bodies:

```bash
loopx import-doc-registry-authority --goal-id <STABLE_GOAL_ID> ...
```

After registration, refresh status or state so review packets, read-only maps,
and heartbeat workers can find the new authority without relying on chat
memory. Stop and write a project-local todo or blocker when the target project
is ambiguous, the source cannot be represented as public-safe metadata, or the
next step would require reading a gated source body.

## Owner-Facing Explore Views

Treat the canonical Explore topology and an owner-facing decision graph as two
different read models over one evidence source:

- canonical JSON, Mermaid, and Nodes/Edges/Findings tables preserve complete
  public-safe node identity, evidence, and lineage;
- focused status/tag exports are bounded evidence subsets, not executive views
  by default;
- an executive graph applies semantic compression for operator decisions. It
  should show the decision contract, baseline/incumbent, decisive negative
  evidence, active work or capacity, material risk, terminal gate, and next
  decision. Semantic compression may tighten labels and remove true
  duplication, but it must preserve material decision and evidence nodes plus
  their lineage.

Never overwrite an executive whiteboard directly from a full or focused
canonical export unless a declared presentation contract proves that the
export already carries those semantic roles. For a recurring sync, keep a
project-local display contract with required roles, stable canonical ids,
semantic sections or linked subgraphs, and a non-identity guard. The default
cardinality policy is graph growth: do not omit or merge away a material node
because the graph crossed a generic count such as 20 nodes. A hard
`max_nodes` or `max_edges` limit is valid only as an explicit opt-in
presentation policy with declared scope and overflow behavior; absent that
policy, treat those limits as unbounded.

Render with the target renderer, run overlap and text-overflow checks, inspect
the actual preview, then sync and verify the remote source or digest. Repair a
failed readability check by relayout, shorter labels, larger frames, or more
semantic subgraphs, not by deleting material evidence. If any check still
fails, keep the previous owner view; do not publish a structurally valid but
unreadable graph.

This separation does not transfer quota, todo, launch, stop, or promotion
authority into the presentation layer. Record material experiment transitions
in canonical Explore state first, then refresh the executive projection.

## Preflight

Resolve the host command once before project work. On native Windows PowerShell
7, keep the installed `loopx.ps1` directory on the Windows user `PATH` and run:

```powershell
loopx doctor
```

When the current Windows executor is not PowerShell, preserve the same entry by
calling PowerShell 7 explicitly:

```text
pwsh.exe -NoLogo -NoProfile -File "$HOME/.local/bin/loopx.ps1" doctor
```

If the Windows entry is missing, run the trusted checkout installer from
PowerShell 7, then start a fresh host process so it inherits the user `PATH`:

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\install-windows.ps1 -Python (Get-Command python).Source -AddToUserPath
loopx doctor
```

On POSIX hosts, use the existing shell entry:

```bash
export PATH="$HOME/.local/bin:$PATH"
loopx doctor
```

If `loopx` is not on PATH:

```bash
install_script="$HOME/loopx/scripts/install-local.sh"
if [ -x "$install_script" ]; then
  "$install_script"
  export PATH="$HOME/.local/bin:$PATH"
fi
loopx doctor
```

If this still fails, report the exact missing piece and do not fake a successful
connection.

## Diagnose For The User

When the user asks whether LoopX is working, whether a project can
self-drive, why it is stuck, or says "diagnose LoopX", do not hand the
user shell commands. Run the diagnostic surfaces yourself and then reason from
the evidence.

Prefer the agent-facing packet:

```bash
loopx diagnose
```

If the target goal is known:

```bash
loopx diagnose --goal-id <STABLE_GOAL_ID>
```

The `diagnose` command is not the final judge. It returns compact
`status`, `quota should-run`, todo, interaction-contract, and boundary signals
plus a reasoning checklist. Use those signals as evidence, then answer in your
own words:

- whether the project can currently self-drive;
- what evidence supports that conclusion;
- what blocks autonomous delivery or self-repair;
- the exact user/controller question, if one is projected;
- what the agent will do next.

Only claim autonomous readiness when your reasoning confirms that the user gate
does not block the selected path, quota permits a turn, `goal_boundary` allows
the work, and there is a concrete agent todo or recommended action. If
`diagnose` cannot read status/quota, repair installation, PATH, registry path,
or project connection first; do not infer readiness from chat memory.

## Before Spending Automatic Compute

Before a heartbeat, scheduled tick, long-running adapter, or autonomous project
agent spends another delivery turn, ask LoopX whether this goal is
eligible:

```bash
loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota should-run --goal-id <STABLE_GOAL_ID>
```

For a registered multi-agent goal, include this agent's identity:

```bash
loopx --format json --registry "$HOME/.codex/loopx/registry.global.json" quota should-run --goal-id <STABLE_GOAL_ID> --agent-id <REGISTERED_AGENT_ID>
```

If a registered goal returns `automation_prompt_upgrade.required=true`, treat
the installed automation prompt as stale and regenerate it with
`heartbeat-prompt --agent-id ... --agent-scope ...`.

If the default `loopx` payload contradicts the just-merged source checkout or a
`PYTHONPATH=<checkout> python3 -m loopx.cli ...` cross-check, pause delivery and
run `loopx doctor` before trusting quota. The installed command is normally a
release snapshot wrapper, so a self-merged fix may require refreshing the local
install from latest trusted `origin/main` with `loopx update --execute --ref main`
or a clean main checkout's `scripts/install-local.sh`; rerun the default `loopx`
command after the refresh and only spend quota after the runtime payload matches
the repaired source behavior. A dirty or non-main checkout is canary-only by
default. Use `LOOPX_PROMOTE_DEFAULT=1 scripts/install-local.sh` only after the
checkout has passed its promotion validation and the default replacement is an
intentional write.

On native Windows, automatic archive update and rollback are fail-closed. Update
the trusted checkout, rerun `scripts/install-windows.ps1`, and verify the new
release with `loopx doctor --deep` before spending quota.

If the response has `state=operator_gate`, treat it as a user/controller
interaction, not a silent skip. Read `gate_prompt`, `operator_question`,
`recommended_action`, `next_handoff_condition`, `missing_gates`, and
`user_todo_summary` and `agent_todo_summary` when present, then ask the concrete gate in Chinese unless
the same unresolved question was already surfaced in the recent visible thread.
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
Only when `interaction_contract.user_channel.action_required=true` or
`user_todo_summary.open_count > 0`, the notification must name concrete payload
todo(s)/questions, never only "owner gate"; if those required user-facing items
are not projected, say "具体 user todo 未投影，需修复 LoopX 状态投影"; never
say "no new user action" for this case. When
`interaction_contract.user_channel.action_required=false` and
`user_todo_summary.open_count=0`, allow "无用户待办/无需通知" or a quiet
no-notification result; do not imply a state projection bug. Do not run
`agent_command`, adapter work,
write-control, production actions, or the gated path while asking.

Prefer the guard's `interaction_contract` when present. It is the current
machine-readable protocol for the user / agent / LoopX CLI split:
`interaction_contract.user_channel` says whether to ask the user,
`agent_channel` says whether Codex must attempt work or may quiet no-op, and
`cli_channel` says which CLI transition and spend policy apply. Treat older
fields such as `execution_obligation`, `heartbeat_recommendation`,
`work_lane_contract`, and `goal_boundary` as compatibility/drill-down fields
under that contract, not as competing sources of truth.

If the response has `should_run=false` and not `safe_bypass_allowed=true`, do
not run implementation or adapter work for that goal in this turn. When
running from a heartbeat, pass its `<current_time_iso>` as
`quota should-run --turn-instance-id <HEARTBEAT_TURN_ID>` and reuse that id for
same-heartbeat retries. This commits one idempotent receipt on every heartbeat;
when `effective_action=monitor_quiet_skip`, the same guard also commits the
no-spend stall observation and returns the follow-up decision. Follow
`autonomous_replan_required` / `execution_obligation.must_attempt_work=true` if
the guard exposes it. If `heartbeat_receipt.status=write_failed`, retry with the
same turn id rather than manually appending another poll. Otherwise quietly
report or record the public-safe `reason` only when there is no operator gate to
ask. Keep the heartbeat
automation active: unchanged monitor-only polls are liveness-preserving no-ops,
not self-stop signals. If the command exits non-zero, fail closed: run
`loopx doctor` / `loopx status` and fix status collection before
spending compute.

If the response has `state=operator_gate` and `safe_bypass_allowed=true`, the
gate blocks only the gated delivery path. After the gate has already been
surfaced, you may still read the active state and do one bounded safe-bypass
step from the Priority Stack, such as read-only steering analysis,
documentation, or another P0/P1 item that does not depend on that gate. If that
safe-bypass step actually spends automatic compute, validate it, write back
progress/critic/next action, optionally refresh state, and append one quota
spend event. If `user_todo_summary.open_count > 0`, the safe-bypass report must
include those existing open user todos and must not say there is "no new user
action". If `agent_todo_summary.open_count > 0`, use it as the project agent's
safe follow-up checklist instead of mining chat history or an overlong Next
Action.

If the response has `safe_bypass_kind=outcome_floor_recovery` or
`heartbeat_recommendation.recommended_mode=outcome_floor_recovery`, the outcome
floor blocks surface-only delivery but permits one bounded recovery attempt:
produce the required ranker/cross-domain evidence artifact named by
`quota.must_advance`, or write back the concrete blocker that prevents that
artifact. Avoid summary/queue/contract propagation and synthetic-only test
chains. Spend exactly once only after validated evidence/blocker writeback.

This guard is only a compute-allocation check. It does not grant write
permission, bypass operator gates, or replace run-bound human reward. Operator
gates block the gated delivery path, not unrelated safe steering work.
For an eligible current goal, dependency or sibling-goal todos must not consume the whole eligible turn.
Record or surface those todos as dependency blockers,
but continue the steering audit and choose a gate-independent
P0/P1/P2 candidate for the current goal when one exists. Stop before delivery
only when the open user/owner todo belongs to the current goal's guard payload
or project asset and blocks the selected delivery path.
Routine public repo publication is a boundary decision, not a standing operator
gate: when the active state permits the step, validation passes, and the
public/private boundary scan is clean, commit, push, and PR creation can proceed
autonomously. Stop for private or company-internal material, credentials,
destructive git operations, production actions, or repository rules that
explicitly require review.
Use the shared global registry for this guard so the project agent reads the
same operator gates, user todos, agent todos, and quota state as the dashboard. This does
not mean all project work is global: `todo add`, `refresh-state`, adapter runs,
and project-file reads still use the project-local state/registry and then sync
the public-safe projection back into the global control plane. If two projects
share a `goal_id`, treat that as a registry health bug and fix the id/source
mapping.

If `should_run=true`, do not simply continue the nearest previous TODO. Read the
active state's Priority Stack, recent progress, and critic, then run a short
steering audit before choosing work: list at least three plausible next-action
candidates across different P0/P1/P2 lanes when useful; if the same topic has
consumed several recent delivery slices, apply a continuation check and state
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
differences in the project registry, `.codex/goals/<goal-id>/ACTIVE_GOAL_STATE.md`,
adapter output, or narrow public/private boundary rules. If a lifecycle rule is
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
search/use `automation_update` when available. If
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
`execution_obligation.must_attempt_work=false` and no
`notify_user_on_open_todo=true` blocker-push notification. New connected read-only goals
should follow `recommended_mode=run_first_read_only_map`: run one real
`loopx read-only-map --goal-id <STABLE_GOAL_ID>`, validate the saved
`read_only_project_map`, spend exactly once after validation, then sync or
refresh state if needed. Already mapped goals should follow
`recommended_mode=mapped_noop_if_unchanged`: if there is no new instruction,
owner evidence, agent todo, stale source, or safe handoff, return a quiet no-op
without another dry-run, file edit, or quota spend.

The generated task body also carries a no-progress self-repair guard. More
importantly, `quota should-run` may expose a hard
`autonomous_replan_obligation` / `execution_obligation.must_attempt_work=true`
contract when active state or public run history shows repeated typed no-progress
turns. Quiet future-monitor heartbeat receipts are liveness evidence only;
`dead_monitor_repeat` counts unchanged due/external monitor executions with a
concrete Todo or target identity at its declared threshold. Obey that machine
contract before another quiet no-op: run one
bounded self-repair/replan batch through implementation, validation, and
writeback when that boundary is clear, then spend once. Do not stop at the first
tiny substep when the repair has an obvious validation boundary. Cancel or
pause the heartbeat automation only after 2 consecutive stalled turns (the same
repair path stuck for 2 more eligible turns); explain the no-progress loop with
`NOTIFY`, and skip quota spend for that self-cancel turn.
The same generated task body also makes routine public commit, push, and PR
creation autonomous after validation plus a clean public/private boundary scan.
Do not reintroduce a user gate for public-safe publication itself.
It also respects `notify_user_on_open_todo=true`: open user todos in
focus-wait, waiting, or external-evidence lanes should become a compact
blocker-push `NOTIFY` with at most three items, while skipping delivery work
and quota spend for that blocker-push turn. If the payload includes
`open_todo_notification_policy=repeat_until_resolved`, repeat that `NOTIFY`
until the todo is done, deferred, or replaced. When
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
text as the automation body. Across projects, the automation body should be the
same generated lifecycle prompt with only `goal_id`, `active_state`, and narrow
project boundary rules changed. When a project appears to need a custom
automation branch, treat that as a LoopX product gap first, not as a
reason to paste one-off control logic into the scheduler.

## Generate A Review Packet

When a project agent, controller thread, or local shell needs the current
operator packet, prefer the CLI packet over asking the user to find a dashboard
copy button:

```bash
loopx review-packet --goal-id <STABLE_GOAL_ID>
```

For machine-readable inspection, put the global format flag before the
subcommand:

```bash
loopx --format json review-packet --goal-id <STABLE_GOAL_ID>
```

To relay current target-agent context within existing authorization, use the
handoff form. Ordinary handoffs add no new approval; actual operator gates
still apply:

```bash
loopx review-packet --goal-id <STABLE_GOAL_ID> --handoff-only
```

This read-only command assembles agent context directly from current status.
The full Review Packet consumes the same context and adds human presentation.
Neither path grants authority or changes work state. JSON `handoff_text` and
`project_agent_handoff` always contain complete prepared text. On overflow,
`project_agent_handoff_fragments` contains all ordered shards including index 0;
Markdown prints the shard set. The complete text may exceed 16 lines / 1800
characters; each shard fits that budget. In-budget output keeps its old shape. If the selected queue item is legacy/raw
fallback rather than project-asset-backed, do not treat raw queue fields as
owner, gate, or stop-condition authority.

Collect and restore the entire producer output before using a sharded handoff:

```bash
loopx --format json review-packet --goal-id <STABLE_GOAL_ID> --handoff-only > handoff.json
loopx handoff restore --input handoff.json --format json
```

For raw sharded Markdown use `--input-format markdown`; `--input -` reads stdin.
JSON avoids relying on a renderer preserving HTML comment envelopes. A fragment
title without its envelope, or an indented envelope, is rejected rather than
returned as plain text. If both title and envelope disappear, only the original
JSON output can establish completeness. Complete unfragmented handoff-only
Markdown is accepted as plain text without an integrity claim; use JSON for
unfragmented full Review Packets. Missing,
reordered, duplicate, mixed or changed parts fail with a nonzero exit and
`error_code`, without partial text. Obtain the original complete output and
retry. There is no collector or business-request deduplication by content hash.

A successful restore is content recovery only: it does not execute a command,
adopt work, change Todo/claim/lease or start a session. Check current goal,
source freshness, write scope and real gates using existing status/quota and
ownership workflows. `handoff prepare/inspect/adopt` retains its separate
ownership contract; restore is not a replacement for it. Do not infer sender
identity or permission from a checksum.

Read the packet in order:

- `人只需判断`: the user or controller decides in the dashboard/operator view or
  trusted local shell. A target project agent cannot self-approve this section.
- `用户本地 Gate 记录草稿`: a local preview for the user/controller. The target
  project agent must not run this draft as its own command.
- `给项目 Agent`: the executable handoff context only after the packet's
  forwarding condition is already satisfied. If its stop condition fires, stop
  and report the exact blocker instead of continuing.

## Connect A New Project

1. Read the project goal document and inspect the repo narrowly.
2. Extract a stable `goal_id`, one-line `objective`, `domain`, authority
   sources, validation surfaces, first safe action, and public/private
   boundary.
3. Run `connect` from the project root. Prefer read-only until the goal doc
   explicitly authorizes mutation:

```bash
loopx connect \
  --goal-id <STABLE_GOAL_ID> \
  --objective "<OBJECTIVE_FROM_GOAL_DOC>" \
  --domain <DOMAIN> \
  --goal-doc <GOAL_DOC_PATH> \
  --adapter-kind read_only_project_map_v0 \
  --adapter-status connected-read-only
```

`connect` should create or update the local registry/state and auto-sync the
public-safe entry into `~/.codex/loopx/registry.global.json`.

One repository can host multiple peer-owned goals, such as delivery and a
low-conflict validation lane. Run `connect` once per stable `goal_id`; keep one
shared `.loopx/registry.json`, but use one active state per goal:

```text
.codex/goals/<delivery-goal-id>/ACTIVE_GOAL_STATE.md
.codex/goals/<validation-goal-id>/ACTIVE_GOAL_STATE.md
```

Do not reuse one `state_file` for two goal ids. `loopx registry` treats
that as a health error, and `read-only-map` checks the selected goal's own
`.codex/goals/<goal-id>/` directory so one healthy lane does not hide another
lane's missing state.

If the goal state or registry contains private evidence, add `.loopx/`
and `.codex/goals/` to that project's `.gitignore`.

For a generic read-only connection, create the first non-generic map run:

```bash
loopx read-only-map --goal-id <STABLE_GOAL_ID>
```

This reads registry metadata, the active state, and a bounded project-file
inventory, then appends a `read_only_project_map` run. Use it before writing a
project-specific adapter when the dashboard would otherwise stay on
`state_refreshed` or `connected_without_run`.

For a planned high-complexity adapter, preview the same bounded map before
controller opt-in:

```bash
loopx read-only-map --goal-id <STABLE_GOAL_ID> --dry-run
```

If the adapter status is `planned`, only the `--dry-run` preview is allowed and
the result should include `opt_in_required=true`. Do not append a real map until
the user or target controller has moved the adapter to `read-only-map-ready`,
`connected-read-only`, or `connected`. Relay the returned `residual_risks`
labels directly; do not invent a separate free-form risk summary.

When the user or target controller answers the opt-in gate, record that answer
before handing the command to another project agent:

```bash
loopx operator-gate \
  --goal-id <STABLE_GOAL_ID> \
  --decision approve \
  --reason-summary "<PUBLIC_SAFE_CHINESE_REASON>" \
  --dry-run
```

Use `approve`, `reject`, or `defer`. The dry-run writes nothing; the real append
creates an `operator_gate_*` compact run so `loopx status` and the
dashboard can tell whether the project agent may run the approved command. This
is not a human reward signal and does not grant write-control.

## Qualify Non-Trivial Final Diffs

Before a non-trivial delivery or merge, inspect the current goal configuration.
When `change_quality_qualification.enabled` is true, load
`loopx-change-quality` and follow its exact-scope workflow:

```bash
loopx --format json change-quality prepare \
  --goal-id <STABLE_GOAL_ID> \
  --repo-path .
```

The policy keeps two decisions separate: `safe_fix` permits at most one bounded
repair pass, while `strict_receipt` requires a passing receipt for the exact
final diff. Any edit invalidates the old fingerprint. Record and verify the
final receipt, then pass `--goal-id <STABLE_GOAL_ID>` to `canary premerge`.

If the host cannot load skills, use the self-contained prepare packet as the
review contract. Turn may carry the packet or receipt reference, but it does
not own policy or enforcement. Do not invent a receipt, reuse one from an older
diff, or turn subjective style advice into a blocker.

## Refresh State After Non-Adapter Work

If the agent updated `ACTIVE_GOAL_STATE.md`, a progress ledger, a planning doc,
or external coordination state without producing a new adapter run, append a
state-only refresh:

```bash
loopx refresh-state --goal-id <STABLE_GOAL_ID> --agent-id <REGISTERED_AGENT_ID>
```

For multi-agent goals, keep the same `--agent-id` envelope that passed
`quota should-run`. This default is an agent-lane refresh. To update the
goal-level route or durable `## Next Action`, any registered peer may add
`--progress-scope goal`.

If that refresh records a validated progress artifact rather than a pure
state-only note, include a public-safe classification and explicit delivery
hints so status, review packets, and quota guards do not infer scale/outcome
from the classification name:

```bash
loopx refresh-state \
  --goal-id <STABLE_GOAL_ID> \
  --classification <PUBLIC_SAFE_PROGRESS_CLASSIFICATION> \
  --delivery-batch-scale <ACTUAL_DELIVERY_BATCH_SCALE> \
  --delivery-outcome <ACTUAL_DELIVERY_OUTCOME> \
  --agent-id <REGISTERED_AGENT_ID> \
  --progress-scope goal
```

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
The dashboard uses `user_todos` for the first-screen human checklist; project
agents should read `agent_todos` only after health, operator gates, evidence,
and quota allow execution.

For non-trivial feature work, prefer todo succession over extra lifecycle
states. When a slice merges or validates, complete the current todo only after
creating the next concrete agent/user todo for rollout, product-path audit,
docs, telemetry, benchmark proof, or operator decision; if there is truly no
follow-up, write a compact no-follow-up rationale in the completion note.
An open feature PR that is waiting for ordinary human review does not by itself
block further project work. Complete the feature slice with its validated PR
evidence, derive the review reminder with `--next-user-todo ...` and
`--next-user-task-class user_action`, and keep a runnable agent successor. Add
a separate `continuous_monitor` for PR lifecycle readback. Use `user_gate` only
for an exact authority boundary such as merging an aggregate experiment branch
into `main`, release, benchmark launch, credentials, or protected production
action. A stable experiment integration branch may collect feature PRs from
isolated worktrees; keep the aggregate PR to `main` as the final review gate.
When the next agent slice is not yet known, keep the bound `user_action`
visible without converting it into a gate. If bounded no-progress evidence
accumulates while the agent frontier is empty, follow
`autonomous_replan_required` and produce one typed semantic outcome. If the
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

## Record Human Reward

When the user gives a clear reward judgment for an exact run, first validate
the overlay and active-state writeback:

```bash
loopx reward \
  --goal-id <STABLE_GOAL_ID> \
  --run-generated-at <RUN_GENERATED_AT> \
  --decision <DECISION_LABEL> \
  --reward positive \
  --reason-summary "<PUBLIC_SAFE_CHINESE_REASON>" \
  --follow-up "<PUBLIC_SAFE_NEXT_ACTION>" \
  --write-active-state-summary \
  --dry-run
```

The preview may stay anonymous. Only after the user has explicitly approved
recording the reward, rerun without `--dry-run` and add exactly one reviewed
actor classification: `--actor-kind owner` for the owner's judgment or
`--actor-kind controller` for an authorized controller's judgment. Never infer
that classification from the process, Agent id, or command defaults. The
durable source of truth is still the run-bound `human_reward` overlay. The
active-state writeback is a `Progress Ledger` summary for future agents;
project agents should read the reward through the returned
`project_agent_visibility.history_command`.

## Multi-Project Status

Inside a project:

```bash
loopx status
```

To focus the status projection on one goal while preserving global health
fields, pass the goal id:

```bash
loopx --format json status --goal-id <STABLE_GOAL_ID>
```

Outside a project, `loopx status` should fall back to:

```text
~/.codex/loopx/registry.global.json
```

Use explicit sync only for diagnosis or recovery:

```bash
loopx sync-global
```

If status shows `unregistered_runtime_goal`:

- If the project is active, run `loopx sync-global` or reconnect from
  that project's local registry.
- If the runtime record is obsolete, preview cleanup with
  `loopx archive-runtime --goal-id <GOAL_ID>` and only execute cleanup
  when that is clearly intended.

## Validation

After connect or refresh, run the smallest useful set:

```bash
loopx registry
loopx status
loopx check --scan-path <PUBLIC_SAFE_FILE_OR_DIR>
```

For multi-project UI updates, refresh the dashboard status JSON from the global
registry:

```bash
loopx --registry "$HOME/.codex/loopx/registry.global.json" \
  --format json status > <dashboard>/public/status.local.json
```

## Reporting

Report in Chinese when the user is reviewing:

- changed files and whether they are public or local-private;
- validation commands and results;
- how the goal appears in dashboard or attention queue;
- the next safe action;
- any missing gates before decision-advisor or write-controller behavior.

Never include credentials, private docs, raw internal links, production task
ids, or raw local evidence in public repo docs or examples.

## Capability Context And Child Models

Read `interaction_contract.agent_context` at planning time (or
`turn_envelope.agent_context` in LoopX Turn, resolving its detail reference when
compacted). If the host supplies neither, use the read-only
`loopx agent-context --goal-id <goal> --agent-id <agent> --phase before_plan`.
If context is absent, disabled, or the read fails, preserve the existing single
agent workflow: do not seek delegation splits or invoke child tools because of
this capability. Tool availability and installed skills do not activate it.
Only apply delegation guidance from a non-null, current-scope enabled context;
the capability provider owns that policy. Context never grants spawn authority.

When enabled context and separate authorization allow native child tools
outside LoopX Turn, read the same capability
context at each boundary, using the current registry, Goal and Agent:

```bash
loopx agent-context --goal-id <goal> --agent-id <agent> --phase before_delegate
# Launch the bounded child work; continue useful coordinator work; collect results.
loopx agent-context --goal-id <goal> --agent-id <agent> --phase after_delegate_result
```

These are read-only calls, not new turns or quota spends. Read `before_plan`
through this command too if the host did not supply an interaction contract.
LoopX Turn already carries `delegation_context` in its host request and
`agent_context` in its reconciled result. Apply the relevant guidance once per
boundary, not on every poll. Validate returned evidence, explain acceptance or
rejection and link accepted work to the plan/deliverable. A context packet's
`delivery: projected` proves generation only, not model reading, execution or
adoption. Native CLI reads cannot certify host receipts.

For an authorized child-worker task, read
`goal_boundary.orchestration.model_config` when present and explicitly pass
its `model` and optional `reasoning_effort` through the native host's supported
launch arguments. Check host support before launch; report unavailable settings
instead of silently inheriting or substituting the coordinator model. Persist
preferences through `configure-goal --subagent-model <id>
--subagent-reasoning-effort <effort> --execute`, and remove them together with
`--clear-subagent-model-config`. These preferences do not enable spawning or
widen authority. See `docs/integrations/codex-subagent-orchestration.md` for
read-heavy briefs, configuration readback, and the host enforcement boundary.
