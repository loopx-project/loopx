"""Heartbeat prompt rule constants inside the heartbeat bounded context."""


DEFAULT_MATERIAL_QUEUE_RULE = "Do not consume the learning material queue unless the user explicitly asks."
DEFAULT_PERMISSION_RULE = "Do not ask for permissions when the current host session is already trusted."
SCOPE_BOUNDED_WORK_RULE = (
    "授权/预算内推进可验证结果；按任务/证据/风险定规模，不按操作/文件数/心跳间隔。"
    "操作/写回不自动结束；遵守停止/重规划。"
)
USER_TODO_FINAL_MESSAGE_RULE = (
    "`interaction_contract.user_channel.notify` controls output: `NOTIFY` -> concrete "
    "action; otherwise quiet. `should_run`/due monitor and other-agent scoped todos "
    "are not user prompts. Only inside `NOTIFY`, `action_required` without an action -> "
    '"具体 user todo 未投影，需修复 LoopX 状态投影"; with `DONT_NOTIFY`, repair '
    "the projection internally and stay quiet."
)
HEARTBEAT_NOTIFICATION_RULE_SHORT = (
    "`user_channel.notify` controls OUTPUT only: NOTIFY=向用户输出动作; "
    "DONT_NOTIFY=安静输出。见 `heartbeat_recommendation.agent_must_attempt`/"
    "`execution_obligation.must_attempt_work`：true须推进并写回，false才可no-op。"
    "Due/peer非用户动作；NOTIFY缺动作→"
    "具体user todo未投影，需修复LoopX状态投影；静默时内部修复。"
)
HEARTBEAT_VISION_WRITEBACK_RULE_SHORT = (
    "writeback: 本轮精确monitor-poll提交→不refresh/spend；"
    "其余no-change=surface_only/no spend；material=outcome+vision；"
    "缺则同轮按返回命令补齐再terminal；unchanged→真实--vision-unchanged-reason。"
)
REWARD_MEMORY_OUTCOME_RULE = (
    "`reward_memory_recall.experiment.automatic_ingest=true`: reusable Todo outcomes "
    "add `--reward-memory-reflection-json <turn_reward_memory_reflection_v0 JSON>` "
    "to refresh. LoopX stages privately; provider ingest needs caller-declared Todo "
    "validator to attest exact reflection digest/evidence, then exact writeback/spend "
    "readback. Missing attestation stays awaiting; zero provider calls. Never include "
    "raw/private material."
)
REWARD_MEMORY_OUTCOME_COMPACT_RULE = (
    "Auto-ingest Todo: add `--reward-memory-reflection-json <reflection JSON>` "
    "to refresh. Private stage; provider write needs Todo validator exact "
    "digest/evidence attestation + refresh/spend readback. Else awaiting/zero "
    "provider calls; no raw/private content."
)
SCHEDULER_HINT_APPLICATION_RULE = (
    "`scheduler_hint` no-spend. host_action=pause_or_delete_current_heartbeat -> "
    "automation_update stop once, verify, end; else apply_needed -> RRULE via "
    "automation_update; unavailable -> use fallback_hint.cli_args only when projected "
    "(SQLite/app API "
    "bypass - fallback only), then ack; further failure -> failure_hint; "
    "ack_needed -> ack."
)
SCHEDULER_HINT_COMPACT_RULE = (
    "host_action=pause_or_delete_current_heartbeat: automation_update stop; "
    "else RRULE apply via automation_update, projected fallback_hint when unavailable, "
    "then ack/fail. No spend."
)
SCHEDULER_HINT_THIN_RULE = (
    "host_action=pause_or_delete_current_heartbeat->automation_update stop(no-spend); "
    "else RRULE/projected-fallback_hint/ack/fail."
)
RUNTIME_CAPABILITY_PROJECTION_THIN_RULE = (
    "Observed capabilities -> `--available-capability`; never user gates."
)
RUNTIME_REPAIR_ROUTING_RULE = (
    "use `loopx-project` for "
    "lifecycle/registry and `loopx-self-repair` for runtime/projection drift."
)
RUNTIME_EXECUTION_ROUTING_RULE = (
    "Normal turns use CLI `interaction_contract`; " + RUNTIME_REPAIR_ROUTING_RULE
)
HOST_LOOP_SAFETY_RULE = (
    "Follow user authority and repository rules. Protect credentials/private material; "
    "publish public-safe evidence. Destructive Git/production requires explicit authorization. "
    "Gate only the affected path; continue independent allowed work."
)
HEARTBEAT_TURN_BOOTSTRAP_RULE = (
    "Per wake, replace `<current_time_iso>` once. Run assignment and guard as separate "
    "statements in one shell, not a command-prefix assignment; reuse the value on retries."
)
HOST_LOOP_QUOTA_DISPATCH_RULE = (
    "Quota: use selection_command when required; "
    "先按指令重新进入，完成获准工作并验证后，再按 next_cli_actions 写回和记账。"
)
HOST_LOOP_TODO_CLOSEOUT_RULE = (
    "Done -> successor first; final -> accountable refresh, spend, then "
    "no-follow-up completion. External wait -> keep open; bind "
    "`monitor_changed:<monitor>` plus an independent successor, rerun quota, "
    "and work it before quiet return; no wait spend."
)
HOST_LOOP_TODO_CLOSEOUT_COMPACT_RULE = (
    "Done->successor; final->refresh/spend/no-follow-up; ext-wait->open+"
    "`monitor_changed:<monitor>`+successor, rerun/work it, no spend."
)
CODEX_NATIVE_GOAL_UNCHANGED_WAIT_RULE = (
    "\n\nNative Codex `/goal` owns blocked state. Recheck quota at the "
    "`scheduler_hint.unchanged_poll` limit. Third identical blocked turn with no "
    "progress: call `update_goal` with `status=blocked`; no spend or LoopX "
    "completion. Only user `/goal resume` reactivates it; rerun quota after resume."
)
