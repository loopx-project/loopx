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
    lease_proof: Mapping[str, Any] | None = None,
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
    if recovery.get("repair") == "quota_spend" and recovery.get("scope") == "current_turn":
        owed = payload.get("settlement_owed")
        if not isinstance(owed, Mapping) or not isinstance(owed.get("command"), str):
            raise RuntimeError("receipt-bound settlement recovery is missing its exact spend command")
        return [owed["command"]]
    if recovery.get("repair") == "dependency" and recovery.get("scope") == "current_turn":
        bound_turn = shlex.quote(str(recovery["turn_instance_id"]))
        return [
            (f"{command_prefix} todo list --goal-id {goal_id}{lifecycle_actor_args}"
             f" --todo-id {shlex.quote(prior_todo_id)}"),
            (f"{command_prefix} task-lease inspect --goal-id {goal_id}"
             f" --todo-id {shlex.quote(prior_todo_id)}"),
            "Inspect dependency_cycle_todo_ids and current owners. Repair only a reviewed obsolete dependency through todo update or the owning lifecycle, using fresh owner/lease/CAS proof. Follow any returned lifecycle recovery before retrying. If another owner must repair the edge, retain the binding and report that blocker. Do not clear a valid wait, claim completion, spend quota, or switch the bound Todo to escape the cycle.",
            f"{typed_quota_guard} --turn-instance-id {bound_turn}",
        ]
    if recovery.get("repair") == "lifecycle" and recovery.get("scope") == "current_turn":
        bound_turn = shlex.quote(str(recovery["turn_instance_id"]))
        return [
            (f"{command_prefix} todo list --goal-id {goal_id}{lifecycle_actor_args}"
             f" --todo-id {shlex.quote(prior_todo_id)}"),
            "Inspect the blocker and existing effects. Only if the blocker is verified resolved, replace <verified-resolved-blocker-reason> with that verified reason and execute the restore below. Otherwise retain the blocked Todo and report the unresolved cause; do not reset receipts or choose another Turn to bypass it.",
            (f"{command_prefix} todo update --goal-id {goal_id}{lifecycle_actor_args}"
             f" --todo-id {shlex.quote(prior_todo_id)} --status open"
             " --reason '<verified-resolved-blocker-reason>' --clear-resume-when"),
            f"{typed_quota_guard} --turn-instance-id {bound_turn}",
        ]
    if recovery.get("repair") == "blocked_writeback":
        bound_turn = shlex.quote(str(recovery["turn_instance_id"]))
        return [
            "Verify the recorded blocked Todo or dependency wait and inspect existing effects. This projection is not a closeout receipt.",
            (f"{command_prefix} refresh-state --goal-id {goal_id}{lifecycle_actor_args}"
             f" --todo-id {shlex.quote(prior_todo_id)} --turn-instance-id {bound_turn}"
             " --classification blocked_attempt --delivery-batch-scale single_surface"
             " --delivery-outcome outcome_gap --progress-result-class blocked"
             " --progress-blocker-id '<verified-blocker-id>'"
             " --progress-evidence-id '<verified-evidence-ref>'"),
            "Read back the settled blocked closeout. Do not spend a slot or rebind this Turn; the next real host Turn selects independent work.",
        ]
    if recovery.get("repair") == "execution_loss_closeout":
        prior_turn = shlex.quote(str(recovery["prior_turn_instance_id"]))
        scope = f"--goal-id {shlex.quote(goal_id)} --todo-id {shlex.quote(prior_todo_id)}"
        goal_ref = payload.get("goal_ref")
        instance = goal_ref.get("goal_instance_id") if isinstance(goal_ref, Mapping) else None
        instance_arg = f" --goal-instance-id {shlex.quote(instance)}" if isinstance(instance, str) else ""
        proof = recovery.get("execution_loss")
        if not isinstance(proof, Mapping):
            raise RuntimeError("execution-loss recovery is missing its fenced generation observation")
        evidence = shlex.quote(f"lease-generation:{proof['original_prepared_epoch']}:{proof['current_lease_epoch']}")
        closeout = (
            f"{command_prefix} refresh-state --execution-loss-closeout {scope}{instance_arg}{lifecycle_actor_args}"
            f" --turn-instance-id {prior_turn} --classification execution_loss_closeout"
            " --delivery-batch-scale single_surface --delivery-outcome outcome_gap --delivery-boundary semantic_closeout"
            " --progress-result-class blocked --progress-blocker-id original-execution-fenced"
            f" --progress-evidence-id {evidence} --task-lease-idempotency-key '<current-key>'"
            " --task-lease-expected-version '<current-version>' --no-global-sync --suppress-external-sinks"
        )
        return [
            f"{command_prefix} task-lease inspect {scope}",
            "Use the current control-writer proof for the original Agent. When unavailable, recover ownership through the authorized Todo lifecycle before closing.",
            f"{closeout} --dry-run",
            "After the preview establishes a no-spend closeout, execute the same command without --dry-run and verify the original settlement. Resume fresh work only through its own guard.",
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
        lease_args = ""
        if (
            isinstance(lease_proof, Mapping)
            and isinstance(lease_proof.get("idempotency_key"), str)
            and lease_proof["idempotency_key"].strip()
            and isinstance(lease_proof.get("expected_version"), int)
        ):
            lease_args = (
                f" --task-lease-idempotency-key {shlex.quote(lease_proof['idempotency_key'].strip())}"
                f" --task-lease-expected-version {lease_proof['expected_version']}"
            )
        return [
            (
                "If a verified observation was obtained under the original Turn, "
                "record it with monitor-poll below. Never infer external state "
                "from Todo prose, and add "
                "--material-change plus a runnable successor only for a real change"
            ),
            (
                f"{command_prefix} quota monitor-poll --goal-id {goal_id}"
                f"{lifecycle_actor_args} --todo-id {shlex.quote(prior_todo_id)}"
                f"{target_arg} --result-hash '<public-safe-result-hash>'"
                f"{cadence_arg} --turn-instance-id {shlex.quote(prior_turn_id)}"
                f"{lease_args} --execute"
            ),
            (
                "If observation is unavailable, instead close only the original "
                "attempt with the typed blocked writeback below: supply stable "
                "blocker/evidence IDs and a normal vision decision. It preserves "
                "Monitor clocks/hash/generation, creates no poll and spends "
                "nothing. An already committed poll needs neither branch."
            ),
            (
                f"{command_prefix} refresh-state --goal-id {goal_id}{lifecycle_actor_args}"
                f" --todo-id {shlex.quote(prior_todo_id)}"
                f" --turn-instance-id {shlex.quote(prior_turn_id)}"
                f"{lease_args}"
                " --classification monitor_observation_unavailable"
                " --delivery-batch-scale single_surface --delivery-outcome outcome_gap"
                " --progress-result-class blocked --progress-blocker-id '<verified-blocker-id>'"
                " --progress-evidence-id '<verified-evidence-ref>'"
                " --vision-unchanged-reason '<verified-unchanged-vision-reason>'"
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
