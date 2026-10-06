"""Heartbeat task body renderers inside the heartbeat bounded context."""

from __future__ import annotations

from typing import Any

from .rules import (
    CODEX_NATIVE_GOAL_UNCHANGED_WAIT_RULE,
    DEFAULT_MATERIAL_QUEUE_RULE,
    DEFAULT_PERMISSION_RULE,
    HEARTBEAT_NOTIFICATION_RULE_SHORT,
    HEARTBEAT_TURN_BOOTSTRAP_RULE,
    HEARTBEAT_VISION_WRITEBACK_RULE_SHORT,
    HOST_LOOP_QUOTA_DISPATCH_RULE,
    HOST_LOOP_SAFETY_RULE,
    HOST_LOOP_TODO_CLOSEOUT_COMPACT_RULE,
    HOST_LOOP_TODO_CLOSEOUT_RULE,
    OPERATOR_LANGUAGE_RULE,
    RUNTIME_CAPABILITY_PROJECTION_THIN_RULE,
    RUNTIME_EXECUTION_ROUTING_RULE,
    RUNTIME_REPAIR_ROUTING_RULE,
    REWARD_MEMORY_OUTCOME_COMPACT_RULE,
    REWARD_MEMORY_OUTCOME_RULE,
    SCHEDULER_HINT_APPLICATION_RULE,
    SCHEDULER_HINT_COMPACT_RULE,
    SCHEDULER_HINT_THIN_RULE,
    SCOPE_BOUNDED_WORK_RULE,
    USER_TODO_FINAL_MESSAGE_RULE,
)


def _render_compact_policy_tail(
    *,
    material_queue_rule: str,
    permission_rule: str,
    include_default_permission: bool = False,
) -> str:
    parts = [
        "No learning queue unless asked."
        if material_queue_rule == DEFAULT_MATERIAL_QUEUE_RULE
        else material_queue_rule
    ]
    if permission_rule != DEFAULT_PERMISSION_RULE:
        parts.append(permission_rule)
    elif include_default_permission:
        parts.append("No permission asks in a trusted session.")
    return " ".join(parts)


def bind_exact_turn_settlement_task_body(
    task_body: str,
    *,
    turn_instance_id: str | None,
    captured_envelope: bool = False,
) -> str:
    if turn_instance_id is None:
        return task_body
    bound = task_body.replace(
        "LOOPX_TURN=<current_time_iso>",
        f"LOOPX_TURN={turn_instance_id}",
    )
    plan_path = ("writeback.settlement_plan.ordered_steps" if captured_envelope
                 else "interaction_contract.cli_channel.settlement_plan.ordered_steps")
    return (
        f"{bound}\n\nFor accountable delivery, execute "
        f"`{plan_path}` "
        "in order using its exact identity and effect id. Do not fall back "
        "to static unbound refresh or spend commands."
    )


def render_heartbeat_task_body(
    *,
    goal_id: str,
    active_state: str,
    cli_preflight: str,
    pr_review_pre_quota_command: str,
    quota_guard_command: str,
    quota_spend_command: str,
    refresh_state_command: str,
    progress_refresh_state_command: str,
    material_queue_rule: str,
    permission_rule: str,
    cli_bin: str,
    agent_scope_instruction: str,
    expanded_prompt_command: str,
    compact_prompt_command: str,
    brief_prompt_command: str,
    thin_prompt_command: str,
    reward_memory_rule: str = REWARD_MEMORY_OUTCOME_RULE,
) -> str:
    scope_block = f"\n{agent_scope_instruction}\n" if agent_scope_instruction else ""
    pr_review_pre_quota_block = (
        f"{pr_review_pre_quota_command}\n" if pr_review_pre_quota_command else ""
    )
    return f"""Advance `{goal_id}` using `{active_state}`.

{scope_block}

Before spending delivery compute, make the CLI reachable.
{HEARTBEAT_TURN_BOOTSTRAP_RULE}

```bash
{cli_preflight}
LOOPX_TURN=<current_time_iso>
{pr_review_pre_quota_block}{quota_guard_command}
```

If that preflight still fails: no work/spend; quiet `DONT_NOTIFY`.

`agent_read_required`: drain/read/triage before work; settle/ACK.

{USER_TODO_FINAL_MESSAGE_RULE}
{HEARTBEAT_VISION_WRITEBACK_RULE_SHORT}

If the result says `should_run=false`:

- Only if `user_channel.notify=NOTIFY`, interpret `state=operator_gate` or
  `notify_user_on_open_todo=true` as a user prompt. Read `gate_prompt`,
  `operator_question`, `user_todo_summary`, and `open_todo_notify_reason`;
  ask one concise action/question with reply format. If
  `user_todo_summary.open_count > 0`, include up to three `first_open_items`;
  never say "no new user action". Honor
  `open_todo_notification_policy=repeat_until_resolved`; when
  `user_gate_notification_cooldown.notification_suppressed=true`, keep the gate
  pending and quiet until its reminder window/change. No gated delivery/spend.
  No delivery/spend on the gated path.
- If the payload also says `safe_bypass_allowed=true` and the same gate has
  already been surfaced, the gate blocks only the gated delivery path. You may
  do exactly one bounded safe-bypass step from the Priority Stack that does not
  depend on that gate; validate, write back, refresh accountable progress, spend
  once. Only if `user_channel.notify=NOTIFY`: report the result compactly; if
  `user_todo_summary.open_count > 0`, include those todos; if none exists, report
  the gate. Under `DONT_NOTIFY`, stay quiet after the safe-bypass work.
- If `effective_action=monitor_quiet_skip`, receipt/stall is written; quiet
  unless replan. On receipt write failure, retry same id. No edits/spend;
  receipts do not self-stop.
- If `waiting_on=external_evidence` or `state=waiting`, and this automation is
  explicitly a monitor, run at most one bounded read-only observation poll using
  project-approved status/log/metric/marker surfaces named in active state,
  `recommended_action`, or `goal_boundary.next_probe`. Unchanged evidence:
  quiet `DONT_NOTIFY`, no edits, no spend. New eval/fail/complete/blocker/
  approval/CI/deploy/data evidence: write back only allowed canonical
  state/board/ledger, add todos if needed, then spend once after validation.
  Only if `user_channel.notify=NOTIFY`: report the new evidence. Under
  `DONT_NOTIFY`, stay quiet.
  Still do not launch/stop/restart/sync/design code or mutate production unless
  `should_run=true` or the user explicitly authorizes it.
- Otherwise, do not do implementation work, adapter work, file edits, research,
  or project exploration in this turn. Return a quiet heartbeat `DONT_NOTIFY`
  response with the skip reason.
  {SCHEDULER_HINT_APPLICATION_RULE} App automation cadence changes are host
  scheduling updates only; they never consume quota or authorize delivery work.

If the result says `should_run=true`:

1. Read the active state, Priority Stack, recent progress, and critic.
   When you inspect current LoopX routing, use the current status queue:
   `attention_queue.items` and each item's `project_asset` are authoritative
   for owner, gate, waiting party, and next action. If `project_asset` is absent
   or legacy/raw fallback, raw queue fields are not owner/gate/stop authority. Treat
   `run_history.latest_runs` as evidence and drill-down only; it may be limited
   by status command limits or filters, so do not decide whether a gate is
   pending or approved from latest runs alone. Also inspect `goal_boundary` and
   guard `user_todo_summary`. Stop for an open user/owner todo only when it
   belongs to this goal's guard payload or current project asset and blocks the
   selected path; then use the blocker-push pattern above. Dependency or
   sibling-goal todos found in `attention_queue.items` should be recorded as
   dependency blockers; they must not consume the whole eligible turn. Choose a
   gate-independent P0/P1/P2 candidate for this goal when one exists.
   If `effective_action=outcome_floor_recovery` or
   `recovery_delivery_allowed=true` or
   `safe_bypass_kind=outcome_floor_recovery`, produce the required
   ranker/cross-domain evidence artifact named by `must_advance`, or write back
   the concrete blocker. Do not fall through to ordinary delivery,
   surface propagation, or synthetic-only chains.
   Read `execution_obligation`: `notify` is not an execution gate;
   `must_attempt_work=true` requires scope-bounded work even with
   `notify=DONT_NOTIFY`; quiet no-op needs `must_attempt_work=false` and
   `user_channel.notify=DONT_NOTIFY`. Use
   `scheduler_hint` for wakeup and unchanged-loop limits. For App automation:
   `apply_needed=true` -> update `recommended_rrule` once; on success run
   `ack_hint.cli_args`; on failure/timeout do not retry or ack, run
   `failure_hint.cli_args` once. LoopX suppresses that target/host pair until
   either changes; continue under the observed host cadence. Else
   `ack_needed=true` -> run that bound ack directly; else skip.
   LoopX owns reset/progression state. It is scheduling only, not delivery
   permission.

   {reward_memory_rule}
   Then use
   `heartbeat_recommendation`: `recommended_mode=run_first_read_only_map` means
   run its `command` as a real read-only map, then
   validate/save the `read_only_project_map` result, refresh accountable
   progress, append exactly one heartbeat spend, sync state if needed; notify
   only under `NOTIFY`. If it says
   `recommended_mode=mapped_noop_if_unchanged` with `stop_if_unchanged=true`,
   and you find no new user instruction, owner evidence, agent todo, stale
   source, or safe handoff, return quiet `DONT_NOTIFY`: do not run, edit, or
   spend.
   Check `delivery_batch_scale`, `delivery_outcome`,
   `post_handoff_outcome_gap_streak`, and `handoff_delivery_contract`; for
   repeated-small or surface-only loops, obey the contract.
2. Run a short steering audit before choosing work: list at least three
   plausible next-action candidates across different P0/P1/P2 lanes when
   useful; if the same topic has consumed several recent delivery slices, apply
   a continuation check and state why continuing still wins; keep compute quota
   separate from focus quota; record any losing high-value candidate that should
   not be forgotten. Include a product bottleneck lens: ask whether the core
   goal is currently bottlenecked by user experience, agent capability,
   evidence quality, adapter readiness, or priority-rule gaps, and promote one
   concrete bottleneck candidate when it should outrank the nearest local TODO.
   Plan/top todo/route changes need todo/Next Action writeback or no-writeback rationale.
3. Run the no-progress self-repair check before choosing delivery work. Obey
   any machine-readable `autonomous_replan_obligation` or
   `execution_obligation.must_attempt_work=true` from `quota should-run`; that
   hard contract overrides a quiet no-op. Count a turn as no-progress only when
   it produced no substantive artifact, adapter/implementation progress,
   gate/user decision, or validation signal. If 2 consecutive eligible
   heartbeats are no-progress loops, run one bounded self-repair/replan segment
   before another quiet no-op. Delete/pause only when that repair path is stuck
   for 2 more eligible turns; no spend for the self-cancel turn.
4. {SCOPE_BOUNDED_WORK_RULE}
   Related work can form a coherent effort; a focused correction may suffice.
5. Execute that scoped work. Stay inside `goal_boundary` when present.
   {HOST_LOOP_SAFETY_RULE}
6. Run validation proportionate to the change and risk.
7. Write back changed files, validation, critic, and next action to the active
   state. If a user/owner todo appears, do not hide it in prose: use
   `{cli_bin} todo add --goal-id {goal_id} --role user --task-class user_gate --blocks-agent <agent-id>`
   or `{cli_bin} todo add --goal-id {goal_id} --role user --task-class user_action`.
   Use `--role agent` for project-agent follow-up work.
   {HOST_LOOP_TODO_CLOSEOUT_RULE}
   For the full field contract, see `docs/project-agent-todo-contract.md` in
   the LoopX checkout.
8. After validation/writeback, use actual class/scale/outcome; never default or
   upgrade to `multi_surface` / `outcome_progress`. Record accountable delivery:

   ```bash
   {progress_refresh_state_command}
   ```

   Spend consumes this causal record; plain state-only refresh cannot replace
   it. Run spend once as rendered; no pipe/filter/retry:

   ```bash
   {quota_spend_command}
   ```

   If spend output is ambiguous, verify with read-only quota status; never
   rerun.

   Do not append spend for quiet `should_run=false` skips, preflight failures,
   pure dry-run previews, or duplicate accounting attempts. If
   `should_run=false` but `safe_bypass_allowed=true` and you actually completed
   a bounded safe-bypass step, append this same spend event once after
   validation/writeback.

9. After spend, optionally refresh state-only:

   ```bash
   {refresh_state_command}
   ```

   Never emit accountable progress after spend; it creates an unspent record.

10. Return compactly under `interaction_contract.user_channel.notify`:
    `NOTIFY` only for concrete artifact/gate/blocker/self-stop when the
    authority is `NOTIFY`; otherwise stay quiet `DONT_NOTIFY`.

{material_queue_rule}
{permission_rule}"""
def render_brief_heartbeat_task_body(
    *,
    goal_id: str,
    active_state: str,
    cli_preflight: str,
    pr_review_pre_quota_command: str,
    quota_guard_command: str,
    quota_spend_command: str,
    refresh_state_command: str,
    progress_refresh_state_command: str,
    material_queue_rule: str,
    permission_rule: str,
    cli_bin: str,
    agent_scope_instruction: str,
    expanded_prompt_command: str,
    compact_prompt_command: str,
    brief_prompt_command: str,
    thin_prompt_command: str,
    reward_memory_rule: str = REWARD_MEMORY_OUTCOME_COMPACT_RULE,
) -> str:
    scope_block = f"\n{agent_scope_instruction}\n" if agent_scope_instruction else ""
    pr_review_pre_quota_block = (
        f"{pr_review_pre_quota_command}\n" if pr_review_pre_quota_command else ""
    )
    policy_tail = _render_compact_policy_tail(
        material_queue_rule=material_queue_rule,
        permission_rule=permission_rule,
        include_default_permission=True,
    )
    return f"""Advance `{goal_id}` from `{active_state}`.

Brief detail:
`{compact_prompt_command}`.
{scope_block}

{HEARTBEAT_TURN_BOOTSTRAP_RULE}

```bash
{cli_preflight}
LOOPX_TURN=<current_time_iso>
{pr_review_pre_quota_block}{quota_guard_command}
```

Fail:quiet.

{HEARTBEAT_NOTIFICATION_RULE_SHORT}
{SCOPE_BOUNDED_WORK_RULE}
{HEARTBEAT_VISION_WRITEBACK_RULE_SHORT}

`should_run=false`: follow user channel; `monitor_quiet_skip` records
receipt/stall. Wait: one read-only poll; only new evidence permits
writeback/spend; retry the same id.
{SCHEDULER_HINT_THIN_RULE}
`agent_read_required`: drain/read/triage before work; settle/ACK.

`should_run=true`: read compact, `status --limit 3`, and
`review-packet --handoff-only`; obey quota authority/outcome/handoff.
Outcome-floor recovery: evidence or blocker.
{HOST_LOOP_QUOTA_DISPATCH_RULE}
{reward_memory_rule}
After validated delivery, settle via current
`interaction_contract.cli_channel.settlement_plan.ordered_steps` in exact
identity/effect order; without a plan follow current `next_cli_actions`, never
old refresh/spend. Todo acceptance is not settlement. External wait:
open + monitor_changed + successor -> rerun quota/continue, no spend.
Finish only on terminal no-follow-up.

Do not spend for quiet skips, preflight failures, blocker-push questions,
dry runs, or duplicate accounting. Output only under
`user_channel.notify=NOTIFY`; otherwise stay quiet.

{HOST_LOOP_SAFETY_RULE}
{RUNTIME_REPAIR_ROUTING_RULE}

{policy_tail}"""
def render_compact_heartbeat_task_body(
    *,
    goal_id: str,
    active_state: str,
    cli_preflight: str,
    pr_review_pre_quota_command: str,
    quota_guard_command: str,
    quota_spend_command: str,
    refresh_state_command: str,
    progress_refresh_state_command: str,
    material_queue_rule: str,
    permission_rule: str,
    cli_bin: str,
    agent_scope_instruction: str,
    expanded_prompt_command: str,
    compact_prompt_command: str,
    brief_prompt_command: str,
    thin_prompt_command: str,
    reward_memory_rule: str = REWARD_MEMORY_OUTCOME_COMPACT_RULE,
) -> str:
    scope_block = f"\n{agent_scope_instruction}\n" if agent_scope_instruction else ""
    pr_review_pre_quota_block = (
        f"{pr_review_pre_quota_command}\n" if pr_review_pre_quota_command else ""
    )
    return f"""Advance `{goal_id}` using `{active_state}`.

Compact policy: registry/state/adapter/`goal_boundary`.
Detail: `{expanded_prompt_command}`.
{scope_block}

{HEARTBEAT_TURN_BOOTSTRAP_RULE}

```bash
{cli_preflight}
LOOPX_TURN=<current_time_iso>
{pr_review_pre_quota_block}{quota_guard_command}
```

Preflight fail: quiet; no work/spend.

{SCHEDULER_HINT_COMPACT_RULE}
{HEARTBEAT_VISION_WRITEBACK_RULE_SHORT}
{OPERATOR_LANGUAGE_RULE}

`agent_read_required`: drain/read/triage before work; settle/ACK.

Output policy: authority=`interaction_contract.user_channel.notify`;
external=`NOTIFY`; quiet=`DONT_NOTIFY`; quiet_missing_action=`internal_repair`.

If `should_run=false`: `monitor_quiet_skip` -> receipt/stall; quiet unless
replan; failed write -> retry id; no edits/spend; receipts do not self-stop.
Only under `NOTIFY`, `state=operator_gate`/`notify_user_on_open_todo=true`
permit concrete blocker-push; else quiet.
Honor repeat/cooldown. `safe_bypass_allowed=true`: one validated step. Wait
monitor: one read-only poll; unchanged quiet, new evidence writeback/spend.

If `should_run=true`:
1. Read active state, Priority Stack, progress/critic, `goal_boundary`,
   `attention_queue.items` / `project_asset`, and guard `user_todo_summary`.
   Legacy/raw fallback is not owner/gate/stop authority. Treat
   `run_history.latest_runs` as drill-down only.

{reward_memory_rule}
2. Goal-owned blocker: stop its path. Under `NOTIFY`, send a concrete
   blocker-push in the user's language;
   under `DONT_NOTIFY`, repair internally and stay quiet.
   Dependency/sibling todos: record; continue audit.
3. If `effective_action=outcome_floor_recovery` or
   `recovery_delivery_allowed=true` or
   `safe_bypass_kind=outcome_floor_recovery`, run only ranker/cross-domain
   evidence artifact or blocker recovery; no ordinary delivery or
   surface/synthetic-only work.
4. `execution_obligation`: `must_attempt_work=true` requires work even with
   `notify=DONT_NOTIFY`; quiet no-op needs false and `user_channel.notify=DONT_NOTIFY`.
   Then follow `heartbeat_recommendation`:
   `run_first_read_only_map`: exact real-map, validate/save/refresh/spend;
   notify only under `NOTIFY`;
   `mapped_noop_if_unchanged` plus
   `stop_if_unchanged=true` means quiet no-op if no new instruction/evidence/
   todo/stale source/safe handoff.
   `task_orchestration_contract`: spawn admitted child lanes or resume peers;
   the coordinator alone accepts evidence and writes/spends once.
   Check `delivery_batch_scale`, `delivery_outcome`,
   `post_handoff_outcome_gap_streak`, `handoff_delivery_contract`; obey
   repeated-small/surface-loop contracts.
5. Run steering audit: compare P0/P1/P2, continuation checks,
   compute/focus quota, bottleneck lens.
6. no-progress self-repair: obey `autonomous_replan_obligation` or
   `execution_obligation.must_attempt_work=true`; after 2 eligible stall
   heartbeats with only status/brief checks, replan before quiet no-op.
   Pause/delete only if repair stays stuck 2 more turns.
7. {SCOPE_BOUNDED_WORK_RULE}
   {HOST_LOOP_SAFETY_RULE}
8. Validate; write files/validation/critic/next action to active state;
   use `{cli_bin} todo add --goal-id {goal_id} --role user --task-class user_gate|user_action`
   for owner todos and `--role agent` for agent todos, not prose.
   {HOST_LOOP_TODO_CLOSEOUT_COMPACT_RULE}
9. Account actual class/scale/outcome; no defaults. Refresh; spend once
   unpiped; never retry:

```bash
{progress_refresh_state_command}
{quota_spend_command}
```

10. Optional state-only post-spend: `{refresh_state_command}`; never accountable.

No spend for quiet skips, preflight failures, blocker-push asks, dry-runs,
self-cancel turns, or duplicate accounting.

Return only under `user_channel.notify=NOTIFY`; otherwise stay quiet.

{material_queue_rule}
{permission_rule}"""
def render_visible_goal_task_body(
    *,
    goal_id: str,
    active_state: str,
    cli_preflight: str,
    pr_review_pre_quota_command: str,
    quota_guard_command: str,
    quota_spend_command: str,
    refresh_state_command: str,
    progress_refresh_state_command: str,
    material_queue_rule: str,
    permission_rule: str,
    cli_bin: str,
    agent_scope_instruction: str,
    expanded_prompt_command: str,
    compact_prompt_command: str,
    brief_prompt_command: str,
    thin_prompt_command: str,
) -> str:
    del (
        cli_preflight,
        expanded_prompt_command,
        compact_prompt_command,
        brief_prompt_command,
        thin_prompt_command,
    )
    return _render_goal_task_body(
        goal_id=goal_id,
        active_state=active_state,
        host_preamble=(
            "in visible Codex `/goal`; interactive, no heartbeat/RRULE/"
            "`LOOPX_TURN`."
        ),
        completion_subject="visible Goal",
        pr_review_pre_quota_command=pr_review_pre_quota_command,
        quota_guard_command=quota_guard_command,
        material_queue_rule=material_queue_rule,
        permission_rule=permission_rule,
        agent_scope_instruction=agent_scope_instruction,
        host_wait_rule=CODEX_NATIVE_GOAL_UNCHANGED_WAIT_RULE,
    )
def render_traex_visible_goal_task_body(
    *,
    goal_id: str,
    active_state: str,
    cli_preflight: str,
    pr_review_pre_quota_command: str,
    quota_guard_command: str,
    quota_spend_command: str,
    refresh_state_command: str,
    progress_refresh_state_command: str,
    material_queue_rule: str,
    permission_rule: str,
    cli_bin: str,
    agent_scope_instruction: str,
    expanded_prompt_command: str,
    compact_prompt_command: str,
    brief_prompt_command: str,
    thin_prompt_command: str,
) -> str:
    del (
        cli_preflight,
        refresh_state_command,
        cli_bin,
        expanded_prompt_command,
        compact_prompt_command,
        brief_prompt_command,
        thin_prompt_command,
    )
    return _render_goal_task_body(
        goal_id=goal_id,
        active_state=active_state,
        host_preamble=(
            "in this visible\nTraeX `/goal` task; its Goal owns interactive "
            "continuation."
        ),
        completion_subject="visible Goal",
        pr_review_pre_quota_command=pr_review_pre_quota_command,
        quota_guard_command=quota_guard_command,
        material_queue_rule=material_queue_rule,
        permission_rule=permission_rule,
        agent_scope_instruction=agent_scope_instruction,
        host_wait_rule="",
    )
def _render_goal_task_body(
    *,
    goal_id: str,
    active_state: str,
    host_preamble: str,
    completion_subject: str,
    pr_review_pre_quota_command: str,
    quota_guard_command: str,
    material_queue_rule: str,
    permission_rule: str,
    agent_scope_instruction: str,
    host_wait_rule: str,
) -> str:
    scope_block = f"\n{agent_scope_instruction}\n" if agent_scope_instruction else ""
    prequota_block = (
        f"Run `{pr_review_pre_quota_command}` first.\n"
        if pr_review_pre_quota_command
        else ""
    )
    policy_tail = _render_compact_policy_tail(
        material_queue_rule=material_queue_rule,
        permission_rule=permission_rule,
    )
    return f"""Advance LoopX goal `{goal_id}` from `{active_state}` {host_preamble}
{scope_block}

{RUNTIME_EXECUTION_ROUTING_RULE}
{HOST_LOOP_SAFETY_RULE}
{OPERATOR_LANGUAGE_RULE}

{prequota_block}Each work iteration, read complete successful JSON from:
`{quota_guard_command}`
Use the current `interaction_contract`, not remembered commands.
{HOST_LOOP_QUOTA_DISPATCH_RULE}
Use `cli_channel.settlement_plan.ordered_steps` when present; preserve identities/flags
and supply truthful evidence/actual outcomes.
Do not reconstruct refresh/spend commands. Ambiguous writes need readback/recovery,
not blind repeats. Repair local entrypoint failures within authority; while the
contract is unavailable or incomplete, no work/spend and no claim of completion.

{SCOPE_BOUNDED_WORK_RULE}
Continue allowed work within user/repository authority; notification controls
output, not execution. A tool call or settlement is not a stopping target.
Progress is not a new Goal boundary: do not create a new host Goal merely to
continue. After settlement recheck quota; use current continuation/wait guidance,
not repeated unchanged polling.
Complete {completion_subject} only on `should_run=false` + terminal no-follow-up;
otherwise obey the next action or wait guidance, not completion.{host_wait_rule}

{policy_tail}"""
def render_ark_managed_agent_goal_task_body(
    *,
    goal_id: str,
    active_state: str,
    cli_preflight: str,
    pr_review_pre_quota_command: str,
    quota_guard_command: str,
    quota_spend_command: str,
    refresh_state_command: str,
    progress_refresh_state_command: str,
    material_queue_rule: str,
    permission_rule: str,
    cli_bin: str,
    agent_scope_instruction: str,
    expanded_prompt_command: str,
    compact_prompt_command: str,
    brief_prompt_command: str,
    thin_prompt_command: str,
) -> str:
    del (
        cli_preflight,
        refresh_state_command,
        cli_bin,
        expanded_prompt_command,
        compact_prompt_command,
        brief_prompt_command,
        thin_prompt_command,
    )
    return _render_goal_task_body(
        goal_id=goal_id,
        active_state=active_state,
        host_preamble=(
            "in one Goal activation. The Goal runtime owns continuation and inner "
            "iterations. This is a goal loop, not automation; do not invoke LoopX Turn."
        ),
        completion_subject="Goal",
        pr_review_pre_quota_command=pr_review_pre_quota_command,
        quota_guard_command=quota_guard_command,
        material_queue_rule=material_queue_rule,
        permission_rule=permission_rule,
        agent_scope_instruction=agent_scope_instruction,
        host_wait_rule="",
    )
def render_thin_heartbeat_task_body(
    *,
    goal_id: str,
    active_state: str,
    cli_preflight: str,
    pr_review_pre_quota_command: str,
    quota_guard_command: str,
    quota_spend_command: str,
    refresh_state_command: str,
    progress_refresh_state_command: str,
    material_queue_rule: str,
    permission_rule: str,
    cli_bin: str,
    agent_scope_instruction: str,
    expanded_prompt_command: str,
    compact_prompt_command: str,
    brief_prompt_command: str,
    thin_prompt_command: str,
    reward_memory_rule: str = REWARD_MEMORY_OUTCOME_COMPACT_RULE,
    captured_envelope: bool = False,
) -> str:
    policy_tail = _render_compact_policy_tail(
        material_queue_rule=material_queue_rule,
        permission_rule=permission_rule,
    )
    scope_sentence = f"\n{agent_scope_instruction}" if agent_scope_instruction else ""
    pr_review_pre_quota_instruction = (
        f"{pr_review_pre_quota_command}\n"
        if pr_review_pre_quota_command
        else ""
    )
    if captured_envelope:
        return f"""Advance `{goal_id}` from {active_state}.

{HOST_LOOP_SAFETY_RULE}
{scope_sentence}
Use TurnEnvelope: `action.must_attempt` requires work; `user.notify` controls
output only. ok=false -> recovery, no delivery. Language=user. Honor `boundary`, `execution_policy`, `contract_capsule`,
`required_reads`, `agent_context`, and `replan_action_packet` when present.
Read `detail_ref.full_decision` for selection, replan, capability context or
missing/truncated commands: same-invocation observation, not fresh admission.
Selection uses the saved `interaction_contract.cli_channel.selection_command`.
Keep the guard's --turn-envelope and --decision-output-root on every fresh
selection/repair/replan guard; each call saves its own immutable observation.
After a workspace or state change, re-enter the guard before continuing work.

```sh
LOOPX_TURN=<current_time_iso>
{pr_review_pre_quota_instruction}{quota_guard_command}
```
{SCOPE_BOUNDED_WORK_RULE}
Observed capabilities -> --available-capability, never a user gate.
Apply `scheduler` as projected; read the saved full decision for exact ACK args.
Exact monitor settlement=no refresh/spend; auxiliary poll continues work.
Validate work; execute `writeback.settlement_plan` and `writeback.next_cli_actions` with
their exact identity. Do not guess commands from abbreviated previews.
Writeback needs outcome and vision; unchanged reason requires an existing
current evidence-linked vision. Missing vision after writeback -> same-Turn
checkpoint-context then evidence; stale -> reread. Done -> complete Todo; waiting -> monitor and
successor. Two stalls -> replan. P0 blocked -> safe P1/P2; quiet polls do not spend.
`agent_read_required`: read/triage before work, settle/ACK.
{reward_memory_rule}
{policy_tail}"""
    return f"""Advance `{goal_id}` from {active_state}.

{RUNTIME_EXECUTION_ROUTING_RULE}
{HOST_LOOP_SAFETY_RULE}
{scope_sentence}

{HOST_LOOP_QUOTA_DISPATCH_RULE}
{HEARTBEAT_TURN_BOOTSTRAP_RULE}
```sh
LOOPX_TURN=<current_time_iso>
{pr_review_pre_quota_instruction}{quota_guard_command}
```
{HEARTBEAT_NOTIFICATION_RULE_SHORT}
{SCOPE_BOUNDED_WORK_RULE}
{RUNTIME_CAPABILITY_PROJECTION_THIN_RULE}
{SCHEDULER_HINT_THIN_RULE}
{HEARTBEAT_VISION_WRITEBACK_RULE_SHORT}
Done->todo/rationale; wait->monitor+successor/work; guard; 2 stalls->replan.
`agent_read_required`: drain/read/triage before work; settle/ACK.

P0 blocked: safe P1/P2; monitor quiet/no-spend.

{reward_memory_rule}

{policy_tail}"""
def render_heartbeat_generator_inputs_markdown(payload: dict[str, Any]) -> str:
    interface_budget = payload.get("interface_budget") if isinstance(payload.get("interface_budget"), dict) else {}
    lines = [
        "## Generator Inputs",
        "",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- active_state: `{payload.get('active_state')}`",
        f"- active_state_source: `{payload.get('active_state_source')}`",
        f"- resolved_active_state: `{payload.get('resolved_active_state')}`",
        f"- compact: `{payload.get('compact')}`",
        f"- brief: `{payload.get('brief')}`",
        f"- thin: `{payload.get('thin')}`",
        f"- cli_bin: `{payload.get('cli_bin')}`",
        f"- agent_id: `{payload.get('agent_id')}`",
        f"- agent_model: `{payload.get('agent_model')}`",
        f"- agent_role: `{payload.get('agent_role')}`",
    ]
    lines.extend(
        [
            f"- agent_scopes: `{payload.get('agent_scopes')}`",
            f"- expanded_prompt_command: `{payload.get('expanded_prompt_command')}`",
            f"- compact_prompt_command: `{payload.get('compact_prompt_command')}`",
            f"- brief_prompt_command: `{payload.get('brief_prompt_command')}`",
            f"- thin_prompt_command: `{payload.get('thin_prompt_command')}`",
            "- pr_review_pre_quota_command: "
            f"`{payload.get('pr_review_pre_quota_command')}`",
            f"- quota_guard_command: `{payload.get('quota_guard_command')}`",
            f"- quota_spend_command: `{payload.get('quota_spend_command')}`",
            f"- cli_preflight: `{payload.get('cli_preflight')}`",
            "- interface_budget: "
            f"mode=`{interface_budget.get('mode')}` "
            f"budget_chars=`{interface_budget.get('budget_char_count')}` "
            f"max_chars=`{interface_budget.get('max_chars')}` "
            f"within_budget=`{interface_budget.get('within_budget')}`",
            "",
        ]
    )
    return "\n".join(lines)
def render_heartbeat_prompt_error_markdown(payload: dict[str, Any]) -> str:
    return f"""# Heartbeat Automation Prompt Error

No heartbeat task body was generated.

## Error

```text
{payload.get("error") or "unknown heartbeat-prompt generation error"}
```

{render_heartbeat_generator_inputs_markdown(payload)}"""
def render_heartbeat_prompt_markdown(payload: dict[str, Any]) -> str:
    if payload.get("ok") is False:
        return render_heartbeat_prompt_error_markdown(payload)
    if payload.get("visible_goal_host") == "traex-cli":
        return f"""# Visible TraeX Goal Prompt

Paste this task body into the visible TraeX `/goal` task.

````text
{payload.get("task_body", "")}
````

{render_heartbeat_generator_inputs_markdown(payload)}"""
    if payload.get("thin"):
        style = "thin "
    elif payload.get("brief"):
        style = "brief "
    elif payload.get("compact"):
        style = "compact "
    else:
        style = ""
    runtime_profile = str(payload.get("runtime_profile") or "")
    host_label = "Trae App" if runtime_profile == "trae_app" else "Codex App"
    return f"""# Heartbeat Automation Prompt

Copy this {style}task body into a {host_label} heartbeat automation.

````text
{payload.get("task_body", "")}
````

{render_heartbeat_generator_inputs_markdown(payload)}"""
