from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any


def recovery_cli_actions(
    payload: Mapping[str, Any],
    *,
    command_prefix: str,
    goal_id: str,
    lifecycle_actor_args: str,
    typed_quota_guard: str,
    turn_instance_id: str | None,
) -> list[str]:
    """Render the bounded typed closeout repair outside the main dispatcher."""

    recovery = payload.get("unsettled_host_turn_recovery")
    recovery = recovery if isinstance(recovery, Mapping) else {}
    prior_todo_id = str(recovery.get("binding_id") or "<prior-todo-id>")
    current_turn_arg = (
        f" --turn-instance-id {shlex.quote(turn_instance_id)}"
        if turn_instance_id
        else " --turn-instance-id <current-turn-id>"
    )
    # The repair lane is a typed fact from the recovery transaction; this
    # renderer only turns it into operator commands.
    if recovery.get("repair") == "lifecycle" and recovery.get("scope") == "current_turn":
        bound_turn = shlex.quote(str(recovery["turn_instance_id"]))
        return [
            (f"{command_prefix} todo list --goal-id {goal_id}{lifecycle_actor_args}"
             f" --todo-id {shlex.quote(prior_todo_id)}"),
            "Inspect the blocker and existing effects. Only if the blocker is verified resolved, execute the restore below. Otherwise retain the blocked Todo and report the unresolved cause; do not reset receipts or choose another Turn to bypass it.",
            (f"{command_prefix} todo update --goal-id {goal_id}{lifecycle_actor_args}"
             f" --todo-id {shlex.quote(prior_todo_id)} --status open"),
            f"{typed_quota_guard} --turn-instance-id {bound_turn}",
        ]
    if recovery.get("repair") == "blocked_writeback":
        bound_turn = shlex.quote(str(recovery["turn_instance_id"]))
        return [
            "Verify the recorded dependency wait and inspect existing effects. This projection is not a closeout receipt.",
            (f"{command_prefix} refresh-state --goal-id {goal_id}{lifecycle_actor_args}"
             f" --todo-id {shlex.quote(prior_todo_id)} --turn-instance-id {bound_turn}"
             " --classification blocked_dependency --delivery-batch-scale single_surface"
             " --delivery-outcome outcome_gap --progress-result-class blocked"
             " --progress-blocker-id '<verified-blocker-id>'"
             " --progress-evidence-id '<verified-evidence-ref>'"),
            "Read back the settled blocked closeout. Do not spend a slot or rebind this Turn; the next real host Turn selects independent work.",
        ]
    if recovery.get("repair") == "resume_prior_turn":
        prior_turn_id = str(recovery["prior_turn_instance_id"])
        return [
            (
                "inspect existing effects and persisted outcomes before retrying; "
                "missing receipts do not establish an external wait or authorize "
                "duplicate execution. Re-enter the original guard and follow its "
                "fresh eligibility and settlement contract; record only verified "
                "outcomes, never fabricate progress to clear recovery"
            ),
            (
                f"{typed_quota_guard} --turn-instance-id "
                f"{shlex.quote(prior_turn_id)} --todo-id "
                f"{shlex.quote(prior_todo_id)}"
            ),
            (
                "after the original Turn is legally settled, rerun the current "
                "Turn below; recovery itself does not spend quota"
            ),
            f"{typed_quota_guard}{current_turn_arg}",
        ]
    if recovery.get("repair") == "monitor_poll":
        prior_turn_id = str(
            recovery.get("prior_turn_instance_id") or "<prior-turn-id>"
        )
        target_key = str(recovery.get("binding_target_key") or "").strip()
        target_arg = (
            f" --target-key {shlex.quote(target_key)}" if target_key else ""
        )
        cadence = str(recovery.get("binding_cadence") or "").strip()
        cadence_arg = f" --cadence {shlex.quote(cadence)}" if cadence else ""
        return [
            (
                "inspect the monitor target and record its exact prior-turn "
                "observation; never infer external state from Todo prose, and add "
                "--material-change plus a runnable successor only for a real change"
            ),
            (
                f"{command_prefix} quota monitor-poll --goal-id {goal_id}"
                f"{lifecycle_actor_args} --todo-id {shlex.quote(prior_todo_id)}"
                f"{target_arg} --result-hash '<public-safe-result-hash>'"
                f"{cadence_arg} --turn-instance-id {shlex.quote(prior_turn_id)} "
                "--execute"
            ),
            f"{typed_quota_guard}{current_turn_arg}",
        ]
    return [
        (
            "inspect unsettled_host_turn_recovery and supply a typed host "
            "observation; never infer external state from Todo prose. Only a "
            "verified external-only wait may use the conditional transition below; "
            "otherwise repair the actual lifecycle or missing binding"
        ),
        (
            f"{command_prefix} todo update --goal-id {goal_id} --todo-id "
            f"{shlex.quote(prior_todo_id)}{lifecycle_actor_args} --status open "
            "--resume-when monitor_changed:<monitor-todo-id> "
            "--successor-todo-id <independent-successor-todo-id>"
        ),
        (
            f"{typed_quota_guard}{current_turn_arg} "
            "--todo-id <independent-successor-todo-id>"
        ),
    ]
