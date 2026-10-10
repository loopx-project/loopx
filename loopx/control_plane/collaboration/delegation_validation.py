"""Host IO for delegation's single typed acceptance/validation plan.

The canonical reader resolves Goal scope; TypeScript selects validation effects.
Python resolves private declarations and executes only those authorized effects.
Original failure and validation-to-settlement recovery use the same Turn journal
and typed decisions; the service retains operation admission, locking and effects.
No validator output or successful declaration read completes a canonical Todo.
"""

from pathlib import Path

from ...agent_registry import load_goal_from_registry
from ...materials import goal_state_path
from ..agents.workspace_guard import capture_delivery_workspace
from ..effect_runtime import effect_runtime_result
from . import delegation_results
from ..turn_driver.journal_store import (
    find_loopx_turn_key_by_settlement_identity, load_turn_journal, turn_journal_path,
)
from ..goals.acceptance import (
    inspect_goal_acceptance,
    run_goal_acceptance_validation_effect,
    validation_effect_files_current,
)
from ..todos.completion_validation import resolve_private_completion_validation_declaration


def capture(service, binding: dict) -> dict:
    basis = inspect_goal_acceptance(
        registry_path=service.registry, runtime_root=str(service.root),
        goal_id=service.goal_id, agent_id=binding["agent_id"], todo_id=binding["todo_id"],
    )
    todo = basis.get("todo")
    if not isinstance(todo, dict):
        raise ValueError("delegation canonical Todo unavailable")
    goal = load_goal_from_registry(service.registry, service.goal_id)
    state_file = goal_state_path(goal) if goal is not None else None
    if state_file is None:
        raise ValueError("delegation validation workspace unavailable")
    declaration = resolve_private_completion_validation_declaration(
        canonical_todo=todo, state_file=state_file, runtime_root=service.root,
        registry_path=service.registry, goal_id=service.goal_id,
        todo_id=binding["todo_id"], role=todo.get("role"), persist_if_resolved=False,
    )
    plan = effect_runtime_result("collaboration.delegation.validation_plan", {
        "binding": {key: binding[key] for key in ("id", "agent_id", "todo_id")},
        "basis": basis, "declaration": declaration,
    })
    # The configured worker worktree is the host-owned execution context. The
    # typed plan still owns which validators run; a path-free snapshot lets the
    # ordinary completion runner verify cross-repository Todo validators.
    workspace = Path(binding["workspace"])
    delivery_workspace = (
        capture_delivery_workspace(workspace, peer_independent_worktree_required=True)
        if any(effect.get("task_repository") for effect in plan["effects"])
        else None
    )
    files_current = plan["state"] == "ready" and validation_effect_files_current(
        effects=plan["effects"], registry_path=service.registry, goal_id=service.goal_id,
        delivery_workspace=delivery_workspace, validation_workspace_path=workspace,
    )
    return {"basis": basis, "plan": plan, "files_current": files_current,
            "delivery_workspace": delivery_workspace}


def validate(service, binding: dict) -> dict:
    before = capture(service, binding)
    if before["plan"]["state"] != "ready" or not before["files_current"]:
        raise ValueError("delegation task acceptance rejected")
    results = [run_goal_acceptance_validation_effect(
        effect=effect, registry_path=service.registry, goal_id=service.goal_id,
        delivery_workspace=before["delivery_workspace"],
        validation_workspace_path=Path(binding["workspace"]),
    ) for effect in before["plan"]["effects"]]
    after = capture(service, binding)
    # The TS plan binds this task's work, claim/lifecycle and current rules.
    # A whole-Goal provider revision may advance for unrelated peer progress.
    if (after["plan"] != before["plan"] or not after["files_current"]
            or after["delivery_workspace"] != before["delivery_workspace"]):
        raise ValueError("delegation task acceptance changed during validation")
    if not all(result["passed"] for result in results):
        raise ValueError("delegation task acceptance rejected")
    return after


def task_failure(service, row, binding):
    turn_key = matching_turn_key(service, row, binding)
    if not turn_key:
        return None
    journal = load_turn_journal(turn_journal_path(service.root, goal_id=service.goal_id, turn_key=turn_key))
    if not journal or journal.get("result_kind") != "validation_failed":
        return None
    return effect_runtime_result("turn.task_validation_failure", {
        "result_kind": journal.get("result_kind"), "status": journal.get("status"),
        "receipt": journal.get("receipt"), "validation": journal.get("task_validation"),
        **({"validation_stage": journal["validation_stage"]} if "validation_stage" in journal else {}),
    })["failure"]


def committed_revalidation_key(service, row, binding):
    """A retained recheck intent can outlive the original failed journal."""
    intent = row.get("validation_failure")
    if not isinstance(intent, dict):
        return None
    turn_key = matching_turn_key(service, row, binding)
    if not turn_key or intent.get("turn_key") != turn_key:
        return None
    journal = load_turn_journal(turn_journal_path(service.root, goal_id=service.goal_id, turn_key=turn_key))
    if journal and journal.get("status") == "committed" and journal.get("result_kind") == "validated_progress":
        return turn_key
    return None


def record_revalidation_result(service, path, row, binding, result):
    from ..turn_driver.loop_controller import ValidatedTurnReceipt
    receipt = ValidatedTurnReceipt.from_execution(result)
    intent = row.get("validation_failure") or {}
    decision = effect_runtime_result("collaboration.delegation.revalidated", {
        "from": row["status"],
        "original_task_failure": intent.get("stage") == "task_postcondition"
            and intent.get("turn_key") == receipt.turn_key
            and receipt.turn_key == matching_turn_key(service, row, binding)
            and receipt.lineage == {"goal_id": service.goal_id, "agent_id": binding["agent_id"], "todo_id": binding["todo_id"]},
        "committed_progress": receipt.result_kind.value == "validated_progress",
        "host_reinvoked": result.get("effects", {}).get("host_invoked"),
    })
    service._record_turn_result(path, row, result, publish=False)
    row["status"] = decision["status"]
    row.pop("error", None)
    service._fenced_write(path, row)


def matching_turn_key(service, row: dict, binding: dict) -> str | None:
    """Find only the journal bound to this operation's settlement identity."""

    return find_loopx_turn_key_by_settlement_identity(
        service.root,
        goal_id=service.goal_id,
        agent_id=binding["agent_id"],
        todo_id=binding["todo_id"],
        turn_instance_id=service._turn_instance_id(row),
    )


def validated_turn_journal(service, row: dict, binding: dict) -> dict | None:
    turn_key = matching_turn_key(service, row, binding)
    if turn_key is None:
        return None
    journal = load_turn_journal(
        turn_journal_path(service.root, goal_id=service.goal_id, turn_key=turn_key)
    )
    if journal is None:
        return None
    phases = journal.get("completed_phases")
    validation = journal.get("task_validation")
    host_result = journal.get("host_result")
    if (
        journal.get("status") != "in_progress"
        or journal.get("result_kind") != "validated_progress"
        or phases != ["host_execute", "typed_result", "validation"]
        or not isinstance(validation, dict)
        or validation.get("ok") is not True
        or not isinstance(host_result, dict)
        or host_result.get("turn_key") != turn_key
        or host_result.get("result_kind") != "validated_progress"
    ):
        return None
    row["turn_key"] = turn_key
    return journal


def recover_validated_settlement(
    service, path: Path, row: dict, binding: dict
) -> bool:
    """Reopen only an exact, independently validated settlement boundary."""

    committed_key = committed_revalidation_key(service, row, binding)
    if committed_key is not None:
        service._bound(row, require_active=True)
        delegation_results.require_dependencies(service, binding, delegation_results.operation_brief(service, row))
        # Read the original committed result through the Turn replay owner;
        # do not fabricate durable effect flags from a saved status string.
        result = service._cli(binding, "turn", "run-once", "--goal-id", service.goal_id,
            "--agent-id", binding["agent_id"], "--resume-turn-key", committed_key,
            *service._execution_arguments(binding, row["identity"]["operation_id"]), "--execute",
            timeout=binding["timeout_seconds"] + 60,
            host_record=service._host_process_record(path))
        record_revalidation_result(service, path, row, binding, result)
        return True
    journal = validated_turn_journal(service, row, binding)
    if journal is None:
        return False
    decision = effect_runtime_result(
        "collaboration.delegation.recover_validated_settlement",
        {
            "from": row["status"],
            "identity_matched": True,
            "journal_status": journal.get("status"),
            "result_kind": journal.get("result_kind"),
            "completed_phases": journal.get("completed_phases"),
            "task_validation_passed": (
                isinstance(journal.get("task_validation"), dict)
                and journal["task_validation"].get("ok") is True
            ),
        },
    )
    row["status"] = decision["status"]
    row["turn_result"] = {
        "status": journal.get("status"),
        "result_kind": journal.get("result_kind"),
        "resume_turn_key": row["turn_key"],
        "reason": "validated Turn settlement requires same-operation recovery",
        "host_failure": None,
        "error": None,
    }
    row.pop("error", None)
    service._fenced_write(path, row)
    return True
