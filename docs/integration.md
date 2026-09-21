# Integration Guide

LoopX should be used as a shared local base, not copied into every
project.

## Local Base

Clone or symlink one copy:

```bash
git clone <repo-url> ~/loopx
~/loopx/scripts/install-local.sh
loopx doctor
```

The installer publishes the current checkout as a local release snapshot and
links that snapshot into `~/.local/bin/loopx`. It also installs a
`loopx-canary` wrapper that points at the live checkout for selected
gray-rollout goal controllers. This keeps default automations stable while
allowing one canary goal to validate prompt/runtime changes before promotion.
The installer adds the bin directory to the current shell profile when it is
missing from `PATH`, and installs a snapshot of the `loopx-project` Codex
skill into `~/.codex/skills` so future project agents use the same connection
workflow. Use `loopx doctor` from any project folder to inspect the
resolved command path, symlink target, release snapshot, canary wrapper,
installed skill delivery-hint state, wrapper script, and Python import health.

## Global Skill Policy

LoopX product behavior belongs in installed global Codex skills, not in
one repository's `AGENTS.md`. Keep the global skills narrow and versioned:
they should teach LoopX connection, quota/state/todo writeback,
self-repair, and generic product contracts such as todo succession. Project
state, benchmark-specific choices, private material, and one-off operator
decisions stay in the registry, active state, run history, or project docs.
When a recurring behavior should improve every future worker, update the repo
skill source and run `scripts/install-local.sh`; when it applies only to this
repo's contribution hygiene, keep it in `AGENTS.md`.

Gray rollout flow:

```bash
# Generate a heartbeat body for a canary goal controller only.
loopx-canary heartbeat-prompt \
  --brief \
  --cli-bin loopx-canary \
  --goal-id <CANARY_GOAL_ID>

# After canary observation looks healthy, promote the checkout to default.
~/loopx/scripts/install-local.sh
loopx doctor
```

Then projects can call:

```bash
loopx --registry <private-registry> registry
loopx --registry <private-registry> history
loopx --registry <private-registry> status
loopx --registry <private-registry> check --scan-root <project-root>
loopx doctor
```

## Lark Or Feishu Reply Cards

Chat gateways that turn LoopX work into Lark or Feishu replies should keep
message rendering separate from message sending.
The implementation is owned by the explicitly enabled Lark extension under
`loopx.extensions.lark.presentation.message_card`; core does not retain a
compatibility import for provider-owned presentation code:

```python
from loopx.extensions.lark.presentation.message_card import build_lark_markdown_reply_card

card = build_lark_markdown_reply_card(
    "**Done**\n- Validated the bounded change",
    title="LoopX result",
    template="green",
)
```

The helper only builds JSON-compatible card content and extracts a reply
`message_id` from CLI output. It does not call Lark, Feishu, or any external
write API. A gateway can pass the returned payload to its own approved sender
after the relevant LoopX gate permits the write.

## One-Command Project Connect

For a new project, start with:

```bash
cd /path/to/project
loopx bootstrap \
  --goal-id project-goal \
  --objective "Improve this project through bounded, verified goal segments." \
  --goal-doc GOAL.md
```

`loopx connect` is an alias for the same operation. The command is
safe to rerun: by default it keeps an existing state file and existing registry
entry. Connection registers the goal and writes the active state only: it does
not create first-connect onboarding Todos, owner-decision gates, or host-loop
opt-in gates. The caller — or the connected domain adapter — writes the first
delivery Todo it wants, so an autonomously driven project starts from the
caller's own work queue instead of a generated onboarding queue. If the goal
only needs an additional write boundary after connection, prefer the
incremental migration path:

```bash
loopx configure-goal \
  --goal-id project-goal \
  --write-scope "src/**" \
  --execute
```

Pass `--force` only when you intentionally want to replace the registry entry or
active state. If you need a force reconnect but want to keep the current todo
projection, add `--preserve-todos`.

The default files are:

```text
.loopx/registry.json
.codex/goals/<goal-id>/ACTIVE_GOAL_STATE.md
```

The generated registry entry also includes an `execution_profile`. This is the
source-level delivery contract for the project, not a one-off heartbeat hint:

```json
{
  "execution_profile": {
    "cadence": "bounded_progress_segment",
    "minimum_scale": "multi_surface_or_implementation",
    "must_include": [
      "coherent_artifact",
      "targeted_validation",
      "state_writeback"
    ],
    "spend_rule": "spend_only_after_artifact_validation_writeback",
    "outcome_floor": {
      "required_when": "after_surface_progress_streak",
      "surface_streak_threshold": 3,
      "outcome_markers": [
        "eval_metric",
        "experiment",
        "macro_evidence",
        "evidence_segment",
        "adapter_proof"
      ],
      "surface_only_hints": [
        "forecast",
        "runbook",
        "queue",
        "fields"
      ],
      "must_advance": [
        "primary_goal_outcome"
      ],
      "avoid": [
        "surface_only_progress_loop"
      ],
      "if_unavailable": "report_blocker_without_spend"
    },
    "degradation_policy": {
      "small_scale_streak_threshold": 2,
      "on_degradation": "require_blocker_or_expand_next_batch"
    }
  }
}
```

`loopx status`, `quota should-run`, and `review-packet --handoff-only`
all read this same profile through `project_asset`. When recent follow-through
keeps shrinking into test-only, single-surface, or unknown-scale runs, the
handoff delivery contract is generated from the profile: the next agent must
expand to the declared minimum scale with a real artifact, targeted validation,
and state writeback, or report a blocker before spending quota. Override the
profile only when a project has a deliberate different floor; do not patch
automation prompts to compensate for a weak connection contract.

## Multiple Goals In One Repository

One repository may hold a main lane, a side bypass, and other independent goals
at the same time. Connect each lane with a distinct stable `goal_id`:

```bash
cd /path/to/project
loopx connect \
  --goal-id main-control \
  --objective "Run the main project control lane." \
  --adapter-kind read_only_project_map_v0 \
  --adapter-status connected-read-only

loopx connect \
  --goal-id side-bypass \
  --objective "Run the low-conflict side bypass lane." \
  --adapter-kind read_only_project_map_v0 \
  --adapter-status connected-read-only
```

Both entries live in the same local `.loopx/registry.json`, but each goal
must own its own ignored active state under
`.codex/goals/<goal-id>/ACTIVE_GOAL_STATE.md`.
Sharing the same `state_file` across two goal ids is treated as a registry
health error because it lets one lane overwrite or summarize the other's state.
Do not commit the live `ACTIVE_GOAL_STATE.md`; publish a sanitized template or
compact projection instead when a public example is needed.

Use `--goal-id` on every status-changing command:

```bash
loopx read-only-map --goal-id main-control
loopx read-only-map --goal-id side-bypass --dry-run
loopx quota should-run --goal-id side-bypass
```

`read-only-map` is goal-aware for same-repo setups. In addition to the generic
project inventory, it reports whether the selected goal has a local project
registry, a `.codex/goals/<goal-id>/` state directory, and the declared active
state file. A missing side-lane state directory produces
`project_goal_state_dir_not_detected:<goal-id>` plus the legacy
`project_local_goal_state_not_detected` risk, while a healthy main lane in the
same repo does not mask that problem.

If `--goal-doc` is provided, the document path is recorded as a primary
authority source. The receiving Codex should inspect that document first before
choosing a next action.

If another Codex session should perform the connection from a project folder
and a goal document, use [new-project-codex-prompt.md](operations/new-project-codex-prompt.md)
as the handoff prompt.

You can generate that handoff prompt:

```bash
loopx new-project-prompt \
  --project /path/to/project \
  --goal-doc /path/to/project/GOAL.md
```

If the connected project should later run through a recurring Codex App
heartbeat, generate the heartbeat task body instead of hand-copying the quota
guard and spend protocol:

```bash
loopx heartbeat-prompt \
  --goal-id project-goal
```

For connected goals, omit `--active-state`; the CLI resolves the active state
from the registry goal `state_file`. Keep `--active-state` only as an explicit
override for detached state files, migration checks, or compatibility tests.

For live Codex App automations, use the thin form as the local machine-default
dispatcher when the target Codex agent can inspect LoopX state and CLI
output itself:

```bash
loopx heartbeat-prompt --thin \
  --goal-id project-goal
```

Use the compact form after reviewing the full contract when the installed
prompt should carry more lifecycle detail inline:

```bash
loopx heartbeat-prompt --compact \
  --goal-id project-goal
```

If an installed automation still needs to be smaller, use the brief body:

```bash
loopx heartbeat-prompt --brief \
  --goal-id project-goal
```

Copy the generated task body into the heartbeat automation. The timer only
wakes Codex; the task body asks LoopX whether the goal should spend
delivery compute on that tick. The thin body keeps the Codex thread as a
replaceable worker: every wakeup should re-read registry/global quota truth,
active state, status/run history, repo state, and project signals instead of
depending on a stale long prompt. The compact body preserves the quota, gate,
blocker-push, recommendation, steering-audit, writeback, refresh, and spend
lifecycle inline without copying the full audit prompt into every run context.
The brief body is for installed automations that should carry only the
preflight/guard, core invariants, and spend accounting while delegating detailed
branches back to the generated contracts.

The Codex App visible goal text can stay short, such as
`按 ACTIVE_GOAL_STATE.md，基于 LoopX 体系，推进项目`. It is only a label for
the human and the executor. The recurring automation prompt should use the
generated heartbeat body above, so every project shares the same quota, gate,
steering-audit, writeback, refresh, and spend lifecycle.
Project-specific behavior should live in the registry, active-state sections,
adapter output, or narrow boundary rules. Do not hand-edit one-off automation
prompt branches for a single project; when a lifecycle rule is broadly useful,
add it to `loopx heartbeat-prompt` and its smoke contract.
The quota guard's `heartbeat_recommendation` covers the common onboarding
cases: `run_first_read_only_map` for a newly connected read-only goal, and
`mapped_noop_if_unchanged` for an already mapped goal with no new instruction,
owner evidence, agent todo, stale source, or safe handoff.
The same guard's `execution_obligation` is the worker contract: when
`must_attempt_work=true`, a heartbeat should attempt one bounded segment even
if `heartbeat_recommendation.notify=DONT_NOTIFY`; notification is not an
execution gate. A quiet no-op needs an explicit `must_attempt_work=false`
contract such as verified `mapped_noop_if_unchanged`.
That lifecycle treats routine public commit, push, and PR creation as
autonomous after validation and a clean public/private boundary scan; private or
company-internal material, credentials, destructive git, production actions, and
repo rules that explicitly require review still stop on a gate.

In most real projects these files should be private. If they contain current
work state or local evidence, add them to `.gitignore`:

```gitignore
.loopx/
.codex/goals/
```

## Project Adapter

A project adapter should be thin and project-specific. It may read:

- active goal state,
- git status,
- test or experiment status,
- cheap health checks,
- project-specific guards.

It should output:

- `classification`,
- exactly one `recommended_action` for the local control loop,
- relevant warnings,
- hard guards,
- optional run log paths.

`recommended_action` is local project state, not a public artifact by default:
it may include private project refs such as todo ids, branch names, local
aliases, or operator-private routing labels. It must not include AK/SK values,
tokens, auth headers, passwords, or inline credentials. Public/export sinks
must redact or omit private routing refs before rendering shareable surfaces.

By default it should be read-only. Launching jobs, stopping jobs, syncing docs,
or editing production state requires explicit user approval.

The bootstrap command does not create a domain adapter. It creates the minimum
registry and state contract so the first adapter can be added deliberately.

For a large project, prefer a read-only adapter map before any writes. The map
should identify authority sources, work clusters, validation surfaces, proposed
peer task scopes, boundary findings, and a short claim/decision packet. See
[complex-project-readonly-adapter.md](integrations/complex-project-readonly-adapter.md).

## Controller / Sub-Agent Coordination

Some Codex goal runs should use multiple child workers. LoopX should keep that
task-scoped parallelism explicit:

- child runs declare `work_scope` before acting;
- overlapping write scopes require task-coordinator arbitration;
- children default to read-only unless the registry grants a write scope;
- child final reports include changed files, validation, residual risk, and
  next handoff;
- the temporary task coordinator aggregates accepted bundle evidence, while
  merge and state writeback still follow explicit task/repository policy.

Minimal registry fields for this pattern are:

```json
{
  "parent_goal_id": null,
  "spawn_policy": {
    "mode": "multi_subagent",
    "allowed": true,
    "max_children": 3,
    "allowed_domains": ["docs-map", "validation-map"]
  },
  "coordination": {
    "agent_model": "peer_v1",
    "registered_agents": ["codex-main-control", "codex-side-bypass"],
    "write_scope": ["docs/**", "examples/**"],
    "requires_parent_approval": ["write", "publish", "production-action"]
  }
}
```

When `spawn_policy.mode=multi_subagent`, status exposes
`project_asset.orchestration` and quota exposes `goal_boundary.orchestration`.
This makes the selected execution mode visible to dashboards and heartbeat
dispatchers instead of relying on prompt text.

`spawn_policy` governs ephemeral child capacity only. Registering multiple
durable peers does not auto-elect a coordinator and does not project their
claimed lanes to another peer. Hosts that can activate durable peer runtimes
must separately opt in with
`loopx configure-goal --goal-id <goal> --peer-task-coordinator <registered-agent>`
and report `--available-capability peer_agent_activation` at quota time. Clear
the selection with `--clear-peer-task-coordinator`; neither setting widens Todo
ownership or repository authority.

These fields are a public contract, not a runtime lock manager. The current
lightweight runtime surface uses todo `claimed_by` as a soft owner written under
the active-state CLI lock. Claim ids must be listed in
`coordination.registered_agents`; those identities are peers. Task claims,
boundaries, capabilities, typed continuation, and repository policy determine
authority. Repository-writing peers use isolated worktrees when the selected
task requires it. Small AGENTS-eligible validated changes may self-merge with
explicit evidence; broader or higher-risk work uses an explicit
`independent_handoff` with `action_kind=review`, optionally excluding the author
when executor separation is required. Soft claims do not expire. The legacy
bootstrap option `--claim-ttl-minutes` is accepted but ignored; use the optional
`loopx task-lease` CLI when a concrete contention case needs TTL, overlap
checks, transfer, and compare-and-swap behavior. Hard leases remain keyed by
`(goal_id, todo_id)`, so unrelated todos under the same goal can still run in
parallel when scopes permit.

## Shared Runtime

All adapters should save compact run history under:

```text
~/.codex/loopx/goals/<goal-id>/runs/index.jsonl
```

This gives the app, CLI, heartbeats, and future UI one place to inspect goal
history.

Project-local registries should also sync into the shared global registry:

```text
~/.codex/loopx/registry.global.json
```

`loopx connect` and `loopx refresh-state` do this automatically.
The global registry is local-private because it contains project paths, but it
strips raw authority-source details and keeps only enough information for
multi-project status. If a command is run outside any project registry, Goal
Harness falls back to the global registry when it exists.

Use the explicit sync command only for diagnosis or recovery:

```bash
loopx sync-global
```

If a controller updates `ACTIVE_GOAL_STATE.md`, a progress ledger, or an
external planning section without running a project adapter, append a
state-only refresh run so status and dashboards do not keep showing the older
adapter run:

```bash
loopx refresh-state --goal-id project-goal
```

The command reads the registered state file, writes a private JSON/Markdown
refresh payload under the shared runtime root, and appends a compact
`state_refreshed` index record. The compact index should contain only
public-safe classification, action, health-check, and artifact pointers; raw
evidence belongs in the project-local state file or private runtime payload.
When `--recommended-action` is omitted, the command derives the compact action
from the first public-safe item in the active state's `## Next Action`, joining
wrapped continuation lines and falling back to a generic refresh action if that
item contains private-looking content.
If a `refresh-state` text field is rejected as private-looking, the CLI names
the field and suggests using a compact public-safe alias/summary there while
keeping raw local paths, private URLs, task bodies, and logs in evidence or
private payloads.
When the state refresh is also the compact record for a validated progress
artifact, add explicit delivery hints so handoff readiness does not have to
infer scale from a classification name:

```bash
loopx refresh-state \
  --goal-id project-goal \
  --classification dashboard_home_browser_smoke_regression \
  --delivery-batch-scale multi_surface \
  --delivery-outcome outcome_progress
```

Use `--delivery-batch-scale` for `test_only`, `single_surface`,
`multi_surface`, or `implementation`. New `refresh-state` writes require one of
those canonical values: historical `single_segment` and `bounded_segment`
records remain readable as `single_surface`, but the legacy names are rejected
for new writes because a bounded segment does not prove how many surfaces it
changed. `--delivery-outcome` is a structured enum, not a classification string:

| Value | Meaning |
| --- | --- |
| `surface_only` | Contract, docs, smoke, setup, or preparation moved, but the primary product/case result did not. |
| `outcome_gap` | The run should have advanced the primary result, but ended with a concrete blocker or missing outcome. |
| `outcome_progress` | The primary result has materially advanced, but the stage is not fully complete. |
| `primary_goal_outcome` | The selected stage's primary result is complete, validated, and written back. |

This keeps quota guards, review packets, and dashboards truthful after a
coherent artifact without exposing raw evidence. Do not encode this decision in
`classification`; classification is for human indexing, while delivery outcome
is the machine decision signal.

For a newly connected read-only project, append a generic map run before
building a custom adapter:

```bash
loopx read-only-map --goal-id project-goal
```

The command accepts goals whose adapter kind is `read_only_project_map_v0` or a
compatible `*_read_only_map_v0` variant, and whose adapter status is connected
for read-only work. It inspects only registry metadata, the active state
sections, and a bounded file-existence inventory. The compact run index records
`classification=read_only_project_map`, local-control-plane
`recommended_action`, artifact availability, map counts, and compact
`residual_risks`; raw project evidence stays in the local private runtime
payload, while public/export sinks redact local-private references before
rendering shareable views.

For planned high-complexity adapters, `read-only-map --dry-run` is allowed as
the opt-in preview path. It returns `opt_in_required=true` and appends nothing,
so a controller can inspect the bounded map shape before moving the adapter to
`read-only-map-ready`, `connected-read-only`, or `connected`. Running the same
command without `--dry-run` still fails until that opt-in status change happens.

Record the operator's answer as a durable gate decision before treating the
handoff as approved:

```bash
loopx operator-gate \
  --goal-id project-goal \
  --decision approve \
  --reason-summary "同意先执行 read-only map opt-in" \
  --dry-run
```

The dry-run writes nothing. A real append creates an `operator_gate_approved`,
`operator_gate_rejected`, or `operator_gate_deferred` compact run with JSON and
Markdown artifacts. Approval makes the goal Codex-ready and exposes the
approved `agent_command`; reject/defer keeps the goal gated with the recorded
reason. This records operator gate decisions separately from `human_reward`,
which remains reserved for judging an exact run or route outcome.

After approval, use the minimal handoff form when the only remaining action is
to relay the target project-agent instruction:

```bash
loopx review-packet --goal-id project-goal --handoff-only
```

This is still read-only packaging. It strips the human decision wrapper from
markdown output so the receiving agent sees only the goal guard, forwarding
condition, execution boundary, stop condition, and command. It does not append a
gate decision, refresh state, spend quota, grant write-control, or authorize
production action.

If a runtime directory belongs to an old goal that is no longer in the registry,
preview archive cleanup before changing anything:

```bash
loopx archive-runtime --goal-id old-experiment-goal
```

The command defaults to dry-run. After review, pass `--execute` to move the
directory into `<runtime-root>/archived-goals/`. Goals still present in the
registry are protected by default; archiving one requires the explicit
`--allow-registered` flag.

## Human Reward Overlays

When an operator judges a run, append a compact reward overlay instead of
editing the run JSON by hand:

```bash
loopx reward \
  --goal-id project-goal \
  --actor-kind owner \
  --decision continue_route \
  --reward positive \
  --reason-summary "comparable validation improved and the route is worth extending" \
  --follow-up "promote to the next longer-window check"
```

Durable reward writes require an explicit `--actor-kind owner` or
`--actor-kind controller`; `--dry-run` remains available without an actor.
The selected kind is stored with the run-bound overlay.

By default the command attaches feedback to the latest compact run for the
goal. Pass `--run-generated-at <timestamp>` to target an older run. The writer
appends a JSONL overlay to the same `index.jsonl`; it does not mutate private
run payloads. `loopx status` exports only the compact `human_reward`
fields, so raw evidence should stay in private artifacts.

`loopx reward --dry-run` and the real append response both include two
coordination fields:

- `active_state_summary`: a short Chinese summary Codex can copy into the
  active goal state after the operator judgment is recorded.
- `project_agent_visibility`: the standard way another project agent should
  find the reward, including the `loopx history --goal-id ... --limit 3`
  command.

The run-bound `human_reward` overlay remains the source of truth. Active state
is only the human-readable pointer and next-action summary; a dashboard Review
Packet is only an immediate handoff artifact.

For explicit user corrections, add a compact lesson to the same run-bound
overlay instead of creating a separate memory store:

```bash
loopx reward \
  --goal-id project-goal \
  --actor-kind owner \
  --decision route_correction \
  --reward mixed \
  --reason-summary "fix lifecycle counters before adding more benchmark cases" \
  --lesson-kind benchmark_protocol \
  --lesson-summary "Do not expand cases until lifecycle counters are validated" \
  --lesson-avoid "expand cases before lifecycle counters" \
  --lesson-prefer "validate lifecycle counters first" \
  --write-active-state-summary
```

The lesson is advisory. `loopx status` exposes it under `human_reward`, and
`loopx quota should-run` warns when a future `recommended_action` appears to
contradict the lesson. It does not authorize writes, launch benchmarks, or
replace formal todo/Next Action updates.

The Markdown output includes a `Write Effect` section that summarizes the
selected run, run-overlay write or preview state, active-state writeback state,
and the project-agent history lookup before the detailed reward fields.

When the operator has explicitly approved recording the reward, Codex can close
the durable loop in one CLI call:

```bash
loopx reward \
  --goal-id project-goal \
  --actor-kind owner \
  --decision continue_route \
  --reward positive \
  --reason-summary "comparable validation improved and the route is worth extending" \
  --follow-up "promote to the next longer-window check" \
  --write-active-state-summary
```

The state write is opt-in. With `--dry-run --write-active-state-summary`, the
command reports `active_state_update.would_write=true` but does not append the
reward overlay or edit the active state. Without `--write-active-state-summary`,
the command records only the run-bound reward overlay.

## First-Screen Status

Use `loopx status` as the entrypoint for the next controller tick or UI
refresh:

```bash
loopx --format json status
loopx status --scan-path README.md --scan-path docs/
```

The default contract scan uses the LoopX install root, so running status
from a private project directory does not accidentally scan local `.local`
state. Pass `--scan-root` or `--scan-path` for a project only when that path is
intended to be public-safe.

For the React dashboard, serve that same status contract over loopback HTTP:

```bash
loopx serve-status --global-registry --port 8766 --limit 80
```

Then load `http://127.0.0.1:8766/status.json` from the dashboard source
control. `--global-registry` keeps the multi-project dashboard on the shared
registry even when the server is launched from a project checkout. For
project-local debugging, omit the flag or pass an explicit project
`--registry`. The command binds to `127.0.0.1` by default and is meant for
local operator dashboards, not public hosting.

The same loopback server exposes `POST /reward/dry-run` so the dashboard can
validate a selected goal/run reward draft. The dry-run response is compact and
does not append to `index.jsonl`; it returns a `preview_id` for the exact
goal/run/reward payload and current raw index count.

Direct dashboard reward submission is an explicit opt-in capability. Start the
status server with `--enable-reward-write-api` to expose `POST /reward/append`
on loopback only. The dashboard can then submit the dry-run `preview_id`; a
successful append writes one run-bound `human_reward` overlay and refreshes
status, so future project agents can discover the feedback through
`loopx status` or `loopx history`.

The status command combines contract health and run history into an attention
queue. Each queue item says which goal needs attention, who it is waiting on,
how severe the item is, and exactly one recommended action.

For dashboards, heartbeat summaries, or any script that reads JSON output, use
the [status data contract](status-data-contract.md).

Keep adapter output sanitized before it enters the compact index. The status
queue is meant for control-plane display, not for raw private evidence.

## Public Repo vs Project Repo

Put generic code here:

- registry and history readers,
- contract checker,
- generic schema and docs,
- sanitized adapter examples,
- peer task and ephemeral worker lifecycle examples.

Keep in the project repo:

- project-specific adapter code,
- active goal state,
- private registry,
- domain-specific health checks.

This split lets many local projects share one stable LoopX base while
keeping their real evidence and safety policies local.
