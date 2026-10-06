"""Heartbeat prompt rule constants inside the heartbeat bounded context."""


DEFAULT_MATERIAL_QUEUE_RULE = "Do not consume the learning material queue unless the user explicitly asks."
DEFAULT_PERMISSION_RULE = "Do not ask for permissions when the current host session is already trusted."
OPERATOR_LANGUAGE_RULE = "Language=user; fallback=English; mix only if asked/scoped-bilingual."
OPERATOR_LANGUAGE_RULE_THIN = "Lang=user; default=en; mix=asked/scoped."
SCOPE_BOUNDED_WORK_RULE = (
    "Within authority/budget, deliver verifiable results sized by "
    "task/evidence/risk, not ops/files/wakes. Calls/writeback aren't "
    "completion; obey stop/replan."
)
USER_TODO_FINAL_MESSAGE_RULE = (
    f"{OPERATOR_LANGUAGE_RULE} "
    "`interaction_contract.user_channel.notify` controls output: "
    "NOTIFY=concrete action; DONT_NOTIFY=quiet. "
    "`should_run`/due monitor/other-agent todos are not user prompts. "
    "Only under NOTIFY, `action_required` without an action: say the specific "
    "user Todo is not projected and repair LoopX state projection. Under "
    "DONT_NOTIFY, repair the projection internally and stay quiet."
)
HEARTBEAT_NOTIFICATION_RULE_SHORT = (
    f"{OPERATOR_LANGUAGE_RULE_THIN} "
    "`user_channel.notify` controls OUTPUT only: NOTIFY=show; "
    "DONT_NOTIFY=no output. "
    "`heartbeat_recommendation.agent_must_attempt`/"
    "`execution_obligation.must_attempt_work`: true=work+writeback; only "
    "false permits no-op. Due/peer work is not a user prompt. Missing NOTIFY "
    "action: user Todo unprojected; repair LoopX state projection; under "
    "DONT_NOTIFY repair internally."
)
HEARTBEAT_VISION_WRITEBACK_RULE_SHORT = (
    "Exact monitor settlement=no refresh/spend; auxiliary poll: continue work. "
    "Admitted work: settlement_plan even if artifacts unchanged. "
    "Writeback=outcome+vision. "
    "After committed writeback: same-turn checkpoint-context for missing vision, add "
    "evidence; stale->reread; unchanged->truthful --vision-unchanged-reason."
)
REWARD_MEMORY_OUTCOME_RULE = (
    "`reward_memory_recall.experiment.automatic_ingest=true`: reusable Todo outcomes "
    "add `--reward-memory-reflection-json <turn_reward_memory_reflection_v1 JSON>` "
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
    "Bootstrap/min interval is a floor; follow current backoff. "
    "`scheduler_hint` no-spend. host_action=pause_or_delete_current_heartbeat -> "
    "automation_update stop once, verify, end; else apply_needed -> RRULE via "
    "automation_update; unavailable -> use fallback_hint.cli_args only when projected "
    "(SQLite/app API "
    "bypass - fallback only), then ack; further failure -> failure_hint; "
    "ack_needed -> ack."
)
SCHEDULER_HINT_COMPACT_RULE = (
    "Bootstrap/min interval is a floor; follow current backoff. "
    "host_action=pause_or_delete_current_heartbeat: automation_update stop; "
    "else RRULE apply via automation_update, projected fallback_hint when unavailable, "
    "then ack/fail. No spend."
)
SCHEDULER_HINT_THIN_RULE = (
    "Bootstrap/min=floor; backoff. "
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
    "re-enter as instructed, do/verify authorized work, then follow "
    "next_cli_actions for writeback/spend."
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
