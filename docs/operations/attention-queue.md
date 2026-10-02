# Attention Queue

The attention queue is the first-screen status contract for LoopX. It is
designed for Codex goal ticks, heartbeat jobs, and a future UI that needs to
answer one question quickly:

> Which goal needs attention next, and who is it waiting on?

`loopx status` builds the queue from three public-safe surfaces:

- registry goals and adapter declarations,
- compact run-history indexes,
- the public/private contract check.

It does not read private run payloads beyond the compact index fields, does not
inspect project-specific logs, and does not mutate files.

For the full JSON shape intended for dashboards and scripts, see
[status-data-contract.md](../status-data-contract.md).

## Command

```bash
loopx status
loopx --format json status
```

The command intentionally stays generic. Project adapters decide their own
domain-specific classifications, but status maps common classifications into a
small queue model.

## Queue Item Schema

```json
{
  "goal_id": "complex-project-main-control",
  "status": "ready_for_controller_opt_in",
  "lifecycle_phase": "controller_gated",
  "lifecycle_flags": ["controller_gated", "adapter_inspected"],
  "waiting_on": "user_or_controller",
  "severity": "action",
  "recommended_action": "先在 LoopX 完成 operator 判断；同意后项目 Agent 只执行 read-only map dry-run",
  "operator_question": "是否同意 `complex-project-main-control` 先执行 read-only map opt-in？",
  "agent_command": "loopx read-only-map --goal-id complex-project-main-control --dry-run",
  "quota": {
    "compute": 0.5,
    "window_hours": 24,
    "slot_minutes": 1,
    "allowed_slots": 720,
    "spent_slots": 0,
    "state": "operator_gate",
    "reason": "planned goal needs operator opt-in before spending agent turns"
  },
  "source": "latest_run"
}
```

Fields:

- `goal_id`: stable public-safe goal id from registry or runtime.
- `status`: classification or derived state.
- `lifecycle_phase`: derived state-interaction phase for dashboard grouping.
- `lifecycle_flags`: all compact phases that apply to the latest goal state.
- `waiting_on`: one of `user_or_controller`, `codex`, `external_evidence`,
  `monitor_signal`, or `controller`.
- `severity`: `high`, `action`, or `watch`.
- `recommended_action`: exactly one user-facing next action from the adapter or
  status layer.
- `operator_question`: optional human-facing gate to show in the LoopX
  operator view. Dashboard action cards should treat this as the primary
  first-screen question when it is present.
- `agent_command`: optional target-agent command or instruction that becomes
  valid only after the operator gate is approved.
- `quota`: optional compact compute-quota state. It should explain whether a
  goal is eligible, throttled, waiting, paused, or operator-gated before an
  automation spends another agent turn.
- `user_todos`: optional active-state checkbox summary for the human/operator.
  Dashboard consumers should surface the first unfinished item before generic
  gate prose when present.
- `agent_todos`: optional active-state checkbox summary for Codex/project
  agents. This belongs in status/CLI and handoff context; it does not replace a
  user/controller gate.
- `source`: `contract`, `registry`, `run_history`, or `latest_run`.

## Summary Counters

The queue summary keeps controller handoff visible:

- `needs_user_or_controller`: counts both `waiting_on=user_or_controller` and
  `waiting_on=controller`.
- `needs_controller`: counts only goals waiting for a target controller or
  adapter connection.
- `needs_codex`: counts goals ready for Codex action.
- `watching_external_evidence`: counts goals waiting on outside evidence or
  metrics.
- `watching_monitor`: counts monitor-only goals that should stay visible but
  do not require immediate Codex work.

## Classification Mapping

Status treats these as user/controller attention:

- `needs_controller_opt_in`
- `needs_human_reward`
- `needs_user_relay`
- `ready_for_controller_opt_in`
- `ready_for_user_relay`

Status treats these as Codex-ready action:

- `controller_opted_in_waiting_for_run`
- `design_next_experiment`
- `inspect_eval_result`
- `inspect_result`
- `needs_more_read_only_evidence`
- `needs_validation`
- `read_only_project_map`
- `run_validation`
- `state_refreshed`

`state_refreshed` means a controller updated active state, ledger, or planning
docs without running a project adapter. The next Codex action is to inspect the
refreshed state and continue one bounded progress segment.

A registry entry can explicitly override first-screen attention with
`waiting_on`, `attention_status`, `recommended_action`, `operator_question`, and
`next_handoff_condition`. This lets a controller keep a refreshed goal in the
operator lane when the latest run is fresh but the real next step is still a
human or target-controller decision. The override changes status and quota
eligibility, but does not grant project-agent execution. If quota later reports
`safe_bypass_allowed=true`, the target heartbeat may work on another bounded
read-only steering or analysis item from the active state, but it still must not
execute the gated command or any adapter/write/production path.

For complex goals, avoid encoding a whole reading queue in one long
`recommended_action`. Keep `recommended_action` as one routing sentence, then
write explicit checkbox sections in the active state. Project agents should
prefer the CLI helper instead of hand-editing section names:

```bash
loopx todo add \
  --goal-id <goal-id> \
  --role user \
  --task-class user_gate \
  --blocks-agent <agent-id> \
  --text "Read the short review packet before approving delivery."

loopx todo add \
  --goal-id <goal-id> \
  --role agent \
  --text "Build the next read-only worksheet after the user decision is recorded."
```

The helper resolves the goal's active state from the registry, creates the
canonical section when needed, and avoids duplicate exact todo text. The
resulting Markdown shape is:

```md
## User Todo / Owner Review Reading Queue

- [ ] Read the short review packet.
- [ ] Record the owner decision in the worksheet.

## Agent Todo

- [ ] Build the next read-only worksheet after the user decision is recorded.
```

Status lifts those checkboxes into `user_todos` and `agent_todos`, so dashboard
attention stays human-readable and agent-facing status remains actionable.

`read_only_project_map` means a connected read-only project now has a standard
map run from `loopx read-only-map`. The next Codex action should use the
map's recommended action or upgrade to a project-specific adapter when needed.

Status treats `blocked_by_safety` as high-severity user/controller attention.

Status treats classifications prefixed with `await_` or `monitor_` as external
evidence watches.

If a connected goal has no saved run yet, status emits `connected_without_run`
so the next Codex action is clear: run the first read-only adapter tick and save
a compact run record.

If a planned high-complexity read-only-map adapter has no saved run yet, status
keeps it in user/controller attention, asks the operator gate in LoopX,
and exposes `loopx read-only-map --goal-id <goal> --dry-run` as
`agent_command`. The command is execution context, not approval. The preview
appends nothing; a real map run still waits for the target controller to move
the adapter to `read-only-map-ready` or `connected-read-only`.
Agent executors should use
`loopx --registry "$HOME/.loopx/registry.global.json" quota should-run --goal-id <goal>`
as the hard compute gate. While the item is still planned, that guard stays
`should_run=false` and omits `agent_command`, even though status displays the
preview command for the human operator.
If the guard also reports `safe_bypass_allowed=true`, the agent can do one
independent read-only steering or analysis step that does not depend on this
operator gate; it cannot run the preview command until the gate is approved.
Before taking that safe-bypass step, the agent should surface the current gate
to the user/controller if the same unresolved question was not already asked in
the recent visible thread. `quota should-run` exposes `gate_prompt`,
`operator_question`, `user_todo_summary`, and `agent_todo_summary` so the agent
can ask one concrete Chinese question and still see its own safe follow-up
checklist instead of silently skipping or forcing the user to inspect the
dashboard manually.
Markdown status output also prints an `operator_gate_dry_run` helper before
`agent_command`, so CLI-facing agents see that the operator gate is a
user-owned dry-run preview before any project-agent handoff.

After the operator answers that gate, record it with `loopx
operator-gate`. Approved gates produce `operator_gate_approved` and move the
next action to Codex with the approved `agent_command`; rejected or deferred
gates produce `operator_gate_rejected` or `operator_gate_deferred` and keep the
goal in the user/controller lane with the recorded reason.

If runtime contains an actionable goal that is not in the registry, status emits
`unregistered_runtime_goal`. This is a controller action: either add the goal to
the registry so it becomes part of the multi-project surface, or archive the
runtime record so old experiments do not look like active work. Watch-only
legacy records such as `await_*` and `monitor_*` stay in run history without
becoming queue items.

Use `loopx archive-runtime --goal-id <goal-id>` to preview cleanup of an
obsolete runtime-only goal. The command only moves files when rerun with
`--execute`.

If the contract check fails, status prepends a high-severity
`loopx-contract` item before project goals.

## Boundary

The queue is safe to show in public docs or a local UI only when goal ids and
recommended actions are sanitized. It should not contain:

- local absolute paths,
- internal task ids,
- raw metric values from private systems,
- document links,
- credentials,
- raw prompts or logs.

Project-specific adapters may keep richer private evidence in their own repo or
runtime payloads, but the status queue should remain compact and public-safe.

Lifecycle phases are derived by the status layer and should stay separate from
adapter classifications. A queue item can keep its domain-specific status while
also saying whether the goal is merely connected, mapped, refreshed,
adapter-inspected, reward-judged, or controller-ready.
