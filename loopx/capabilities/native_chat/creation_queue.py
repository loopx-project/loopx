"""IO for the original creation request; the existing Session queue executes it."""
from pathlib import Path
from typing import Any
import logging

def creation_request_facts(controller: Any, session_id: str, *, proposal_id: str | None = None,
                           basis: dict[str, Any] | None = None) -> dict[str, Any] | None:
    actions = getattr(controller, "original_task_action_store", None)
    if actions is None:
        return None
    session = controller.store.load_session(session_id)
    if not session or not session.get("managed_agent_id"):
        return None
    candidates = [actions.load(proposal_id)] if proposal_id is not None else actions.list()
    candidates = [row for row in candidates if isinstance(row, dict)
        and row.get("action_kind") == "goal.create"
        and (row.get("normalized_parameters") or {}).get("start_execution") is True
        and (row.get("normalized_parameters") or {}).get("goal_id") == session.get("goal_id")
        and (((row.get("checkpoint") or {}).get("steps") or {}).get("first_session_opened") or {}).get(
            "session_id") == session_id]
    if not candidates:
        return None
    # Candidate filtering is IO narrowing, not authorization. Only explicit
    # same-Session source candidates load native/canonical owners below.
    from .steward_managed_mode import StewardManagedMode
    from ...control_plane.effect_runtime import effect_runtime_result, EffectRuntimeRejected
    from ...control_plane.goals.acceptance import inspect_goal_acceptance

    goal = StewardManagedMode(controller, None)._goal(session.get("goal_id"))
    if goal is None:
        return None
    # This registered creation identity is the action owner's proposal key,
    # not a label or a parsed operation prefix.
    source = proposal_id if proposal_id is not None else goal.get("creation_operation_id")
    if not isinstance(source, str) or not source:
        return None
    proposal = next((row for row in candidates if row["proposal_id"] == source), None)
    if (proposal is None or proposal.get("action_kind") != "goal.create"
            or (proposal.get("normalized_parameters") or {}).get("start_execution") is not True):
        return None
    if basis is None:
        basis = inspect_goal_acceptance(registry_path=controller.registry_path,
            runtime_root=str(controller.coordination_runtime_root), goal_id=goal["goal_id"],
            agent_id=session["managed_agent_id"], validation_workspace_path=Path(goal["workspace_path"]))
    try:
        selected = effect_runtime_result("goal.task.creation_selection", {"basis": basis})
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    return {"proposal": proposal, "session": session, "goal": goal, "current_original_task": selected}


def project_creation_request(facts: dict[str, Any] | None) -> dict[str, Any] | None:
    if facts is None:
        return None
    from ...control_plane.effect_runtime import effect_runtime_result, EffectRuntimeRejected
    try:
        request = effect_runtime_result("goal.creation.execution_request", facts)
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    if request is not None and not isinstance(request, dict):
        raise ValueError("original creation request projection returned an invalid result")
    return request


def consume_creation_request(controller: Any, session_id: str) -> bool:
    """Return whether this queue still retains work; never retry a terminal Turn."""
    facts = creation_request_facts(controller, session_id)
    if facts is None or facts["proposal"].get("status") not in ("applying", "applied"):
        return False
    if not isinstance(((facts["proposal"].get("checkpoint") or {}).get("steps") or {}).get(
            "original_task_requested"), dict):
        return False
    try:
        request = project_creation_request(facts)
        if request is None:
            return True  # The same creation has not committed its request yet.
        from .steward_managed_mode import ManagedSessionTurn
        return ManagedSessionTurn(controller).consume_creation_request(request)
    except (ValueError, RuntimeError) as exc:
        # Missing/changed checks or source authority leave the saved request
        # open. A content-free action/Turn signal or restart rechecks it.
        logging.getLogger(__name__).debug("Original creation request retained: %s", exc)
        return True


def queued_turn_unstarted(turn: dict[str, Any]) -> bool:
    """Unknown native publication facts cannot authorize a queue dispatch."""
    return (turn.get("status") == "queued" and "_acceptance" not in turn
        and "started_at" in turn and turn["started_at"] is None
        and "upstream_turn_id" in turn and turn["upstream_turn_id"] is None
        and type(turn.get("delta_count")) is int and turn["delta_count"] == 0)


def resume_creation_requests(controller: Any) -> None:
    """Discover only saved requests at service setup; hints grant no execution."""
    actions = getattr(controller, "original_task_action_store", None)
    if actions is None or controller.closed.is_set():
        return
    for proposal in actions.list():
        if (proposal.get("action_kind") != "goal.create"
                or (proposal.get("normalized_parameters") or {}).get("start_execution") is not True):
            continue
        request = (proposal.get("receipt") or {}).get("original_task_request")
        if not isinstance(request, dict):
            continue
        session_id = request.get("session_id")
        if not isinstance(session_id, str):
            continue
        try:
            facts = creation_request_facts(controller, session_id, proposal_id=proposal["proposal_id"])
            qualified = project_creation_request(facts)
            if qualified is None or qualified != request:
                continue
            existing = controller.store.turn_for_client(session_id, "managed." + qualified["operation_id"])
            if existing is not None:
                if not queued_turn_unstarted(existing):
                    continue
                from .steward_managed_mode import ManagedSessionTurn
                expected = ManagedSessionTurn(controller)._request(session_id, qualified["agent_id"],
                    qualified["operation_id"], qualified["todo_id"])
                expected["creation_request"] = qualified
                if existing.get("loopx_request") != expected:
                    continue
            session = facts["session"]
        except (KeyError, OSError, ValueError, RuntimeError):
            # A malformed candidate cannot wake another Session's queue. Its
            # current-source refusal remains available through the Task GET.
            continue
        controller.resume_session_queue(session_id=session_id,
            work_dir=Path(session["managed_workspace_path"]),
            objective="Continue the original Goal's requested Task.")
