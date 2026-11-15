"""IO in the existing confirmed Goal-creation saga, not a new execution owner."""
from __future__ import annotations

from typing import Any

from ..agent_registry import load_goal_from_registry
from ..boundary_authority import build_checkpointed_boundary_authority_entry
from ..chat_action_store import ActionConflictError
from ..configure_goal import configure_goal
from ..control_plane.runtime.time import now_utc


def retain_creation_write_approval(service: Any, proposal_id: str, review: dict[str, Any] | None) -> None:
    """Publish an explicit reviewed grant once and recover its exact readback.

    The typed creation review chooses scope. This IO adapter uses the existing
    configuration/authority owners and never refreshes a lost or revoked grant.
    """
    if not review or not review["writeScope"]:
        return
    proposal = service.store.load(proposal_id)
    goal_id = proposal["normalized_parameters"]["goal_id"]
    goal = load_goal_from_registry(service.registry_path, goal_id)
    if goal is None or goal.get("creation_operation_id") != proposal_id:
        raise ActionConflictError("The write approval belongs to the original Goal creation")
    steps = (proposal.get("checkpoint") or {}).get("steps", {})
    prepared = steps.get("boundary_authority_prepared")
    if prepared is None:
        entry = build_checkpointed_boundary_authority_entry(
            write_scopes=review["writeScope"], source="goal_creation_review",
            decision_id=proposal_id, recorded_at=now_utc().isoformat(),
        )
        prepared = {"goal_id": goal_id, "goal_instance_id": goal.get("goal_instance_id"), "authority": entry}
        service.store.save_checkpoint(proposal_id, step="boundary_authority_prepared", receipt=prepared)
    entry = prepared.get("authority")
    if (prepared.get("goal_id") != goal_id or prepared.get("goal_instance_id") != goal.get("goal_instance_id")
            or not isinstance(entry, dict) or entry.get("write_scope") != review["writeScope"]
            or entry.get("decision_id") != proposal_id or entry.get("source") != "goal_creation_review"
            or entry.get("status") != "active" or entry.get("decision") != "approve"):
        raise ActionConflictError("The original reviewed write approval changed; retain its recorded operation")

    def observe() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        current = load_goal_from_registry(service.registry_path, goal_id)
        if (current is None or current.get("creation_operation_id") != proposal_id
                or current.get("goal_instance_id") != prepared["goal_instance_id"]):
            raise ActionConflictError("The original Goal lifetime changed before approval readback")
        coordination = current.get("coordination") or {}
        entries = coordination.get("checkpointed_boundary_authority") or []
        if not isinstance(entries, list):
            raise ActionConflictError("The original boundary authority is unreadable")
        return coordination, [row for row in entries if isinstance(row, dict) and row.get("decision_id") == proposal_id]

    coordination, matches = observe()
    if not matches:
        if steps.get("boundary_authority_write_started") is not None or steps.get("boundary_authority_recorded") is not None:
            raise ActionConflictError("The original approval publication is unconfirmed or revoked; read back that operation before proceeding")
        service.store.save_checkpoint(proposal_id, step="boundary_authority_write_started",
            receipt={"goal_id": goal_id, "decision_id": proposal_id})
        configure_goal(registry_path=service.registry_path, goal_id=goal_id,
            write_scope=review["writeScope"], boundary_authority_scopes=entry["write_scope"],
            boundary_authority_source=entry["source"], boundary_authority_decision_id=entry["decision_id"],
            boundary_authority_recorded_at=entry["recorded_at"], execute=True)
        coordination, matches = observe()
    if (matches != [entry] or any(scope not in (coordination.get("write_scope") or []) for scope in review["writeScope"])):
        raise ActionConflictError("The original write approval or declared scope changed; do not refresh it from a creation replay")
    service.store.save_checkpoint(proposal_id, step="boundary_authority_recorded", receipt={
        "outcome": "boundary_authority_recorded", **prepared,
    })


def retain_original_task_request(service: Any, proposal_id: str, session: dict[str, Any]) -> dict[str, Any] | None:
    """Persist the same reviewed request before queueing, without executing it."""
    from ..capabilities.native_chat.steward_managed_mode import StewardManagedMode
    from ..control_plane.effect_runtime import effect_runtime_result
    from ..control_plane.goals.acceptance import inspect_goal_acceptance
    proposal = service.store.load(proposal_id)
    if proposal["normalized_parameters"].get("start_execution") is not True:
        return None
    controller = service.runtime_controller
    basis = inspect_goal_acceptance(registry_path=service.registry_path,
        runtime_root=str(controller.coordination_runtime_root), goal_id=session["goal_id"],
        agent_id=session["managed_agent_id"])
    selection = effect_runtime_result("goal.task.creation_selection", {"basis": basis})
    request = effect_runtime_result("goal.creation.execution_request.prepare", {
        "proposal": proposal, "session": session,
        "goal": StewardManagedMode(controller, None)._goal(session["goal_id"]),
        "current_original_task": selection,
    })
    prior = ((proposal.get("checkpoint") or {}).get("steps") or {}).get("original_task_requested")
    receipt = {"outcome": "original_task_requested", **request, "source": {
        "request_digest": proposal["request_digest"],
        "expected_state_fingerprint": proposal["expected_state_fingerprint"],
        "upstream_thread_id": session["upstream_thread_id"],
    }}
    if prior is not None and prior != receipt:
        raise ActionConflictError("The retained original Task execution request changed")
    if prior is None:
        service.store.save_checkpoint(proposal_id, step="original_task_requested", receipt=receipt)
    return request
