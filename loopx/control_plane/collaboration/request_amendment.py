"""Source/Goal IO choreography for one reserved work-graph amendment.

Each owner's typed transaction decides its own transition. No request lock is
held over Goal IO, and no caller-authored terminal outcome enters the linker.
"""
from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Any, Mapping

from .request_control_source import _execute, execute_request_control, request_amendment_snapshot
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..goals.goal_amendment_runtime import _execute_goal_amendment
from ..projects.registry_codec import load_project_registry


def _root(registry: Path, supplied: Path | None) -> Path:
    configured = load_project_registry(registry).get("common_runtime_root")
    if not isinstance(configured, str) or not configured:
        raise ValueError("request effect requires its registered runtime root")
    root = Path(configured).expanduser().resolve()
    if supplied is not None and supplied.expanduser().resolve() != root:
        raise ValueError("request effect runtime differs from its source")
    return root


def request_proposal_context(*, registry_path: Path, runtime_root: Path | None, goal_ref: Mapping[str, str],
                             receiver_agent_id: str, reference: Any) -> tuple[Path, dict[str, Any]]:
    if not isinstance(reference, dict) or set(reference) != {"request_id", "request_revision", "request_digest"}:
        raise ValueError("an exact source request reference is required")
    root = _root(registry_path, runtime_root)
    snapshot = request_amendment_snapshot(registry_path=registry_path, runtime_root=root, goal_ref=goal_ref,
        receiver_agent_id=receiver_agent_id, request_id=reference["request_id"], actor_agent_id=receiver_agent_id,
        requested_basis=reference)
    return root, {"entry": snapshot["_entry"]}


def _reservation(snapshot: Mapping[str, Any], operation_id: Any) -> dict[str, Any] | None:
    view = snapshot["view"]
    records = list(view["terminal_effects"])
    if view["active_reservation"] is not None:
        records.append(view["active_reservation"])
    return next((row for row in records if row["binding"]["operation_id"] == operation_id), None)


def _goal(registry: Path, root: Path, ref: Mapping[str, str], receiver: str,
          operation: str, parameters: Mapping[str, Any], *, context: Mapping[str, Any] | None = None,
          replan_configuration_reader: Callable[[Mapping[str, Any], Path], Mapping[str, Any]],
          execute: bool = False) -> dict[str, Any]:
    return _execute_goal_amendment(registry_path=registry, runtime_root=root, goal_id=ref["goal_id"],
        goal_instance_id=ref["goal_instance_id"], actor_agent_id=None if operation == "receipt" else receiver,
        operation=operation, parameters=parameters, request_context=context, execute=execute,
        replan_configuration_reader=replan_configuration_reader)


def _finish(*, registry: Path, root: Path, ref: Mapping[str, str], receiver: str, actor: str | None,
            snapshot: dict[str, Any], reservation: dict[str, Any], execute: bool,
            replan_configuration_reader: Callable[[Mapping[str, Any], Path], Mapping[str, Any]],
            attempt_observation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Re-read the actual Goal receipt, then link it at the request owner."""
    terminal = None
    errors = []
    bound = reservation["binding"]
    try:
        readback = _goal(registry, root, ref, receiver, "receipt", {"receipt_operation_id": bound["operation_id"]},
            replan_configuration_reader=replan_configuration_reader)
        observation = effect_runtime_result("goal.amendment.request_receipt", {
            "entry": snapshot["_entry"], "reservation_digest": reservation["digest"], "receipt": readback})
        terminal = observation["terminal"]
        if terminal is not None and execute:
            _execute(registry_path=registry, runtime_root=root, goal_ref=ref, receiver_agent_id=receiver,
                request_id=bound["request_id"], actor_agent_id=actor, operation="settle",
                parameters={"operation_id": "settle:" + reservation["digest"], "reservation_digest": reservation["digest"]},
                terminal_receipt=terminal, execute=True)
    except (OSError, ValueError, EffectRuntimeRejected) as exc:
        errors.append(str(exc))
    current = True
    try:
        snapshot = request_amendment_snapshot(registry_path=registry, runtime_root=root, goal_ref=ref,
            receiver_agent_id=receiver, request_id=bound["request_id"], actor_agent_id=actor)
    except (OSError, ValueError, EffectRuntimeRejected) as exc:
        current = False
        errors.append(str(exc))
    result = effect_runtime_result("collaboration.request.amendment_readback", {
        "entry": snapshot["_entry"], "reservation_digest": reservation["digest"], "terminal": terminal,
        "legacy_assessment": snapshot["_legacy_assessment"],
        "legacy_assessment_unavailable": snapshot["_legacy_assessment_unavailable"],
        "legacy_read": snapshot["_legacy_read"], "legacy_read_unavailable": snapshot["_legacy_read_unavailable"], "source_current": current})
    if not isinstance(result, dict):
        raise TypeError("request effect owner returned an invalid readback")
    if errors:
        result["readback_errors"] = errors
    if attempt_observation:
        result["attempt_observation"] = dict(attempt_observation)
    return result


def execute_request_amendment(*, registry_path: Path, runtime_root: Path | None, goal_ref: Mapping[str, str],
                              replan_configuration_reader: Callable[[Mapping[str, Any], Path], Mapping[str, Any]],
                              receiver_agent_id: str, parameters: Mapping[str, Any], execute: bool) -> dict[str, Any]:
    if set(parameters) != {"source_request", "operation_id", "proposal_id", "proposal_digest"}:
        raise ValueError("request-derived commit needs the original proposal, source basis and operation id")
    reference = parameters["source_request"]
    if not isinstance(reference, dict) or set(reference) != {"request_id", "request_revision", "request_digest"}:
        raise ValueError("an exact source request reference is required")
    root = _root(registry_path, runtime_root)
    snapshot = request_amendment_snapshot(registry_path=registry_path, runtime_root=root, goal_ref=goal_ref,
        receiver_agent_id=receiver_agent_id, request_id=reference["request_id"], actor_agent_id=receiver_agent_id,
        requested_basis=reference)
    reference = snapshot["_request_reference"]
    reservation = _reservation(snapshot, parameters["operation_id"])
    if reservation is None:
        # Resolve from the real Goal owner, not caller-provided policy/basis.
        goal = _goal(registry_path, root, goal_ref, receiver_agent_id, "request_basis", {},
            replan_configuration_reader=replan_configuration_reader)
        if goal.get("ok") is not True or not isinstance(goal.get("intent"), dict):
            raise ValueError("current Goal amendment authority is unavailable")
        proposal = next((row for row in goal["intent"]["proposals"] if row["proposal_id"] == parameters["proposal_id"]), None)
        if (proposal is None or proposal["digest"] != parameters["proposal_digest"]
                or proposal.get("source_request") != reference or proposal["proposer_agent_id"] != receiver_agent_id):
            raise ValueError("the original source-derived proposal does not match this request")
        amendment = {key: proposal[key] for key in ("proposal_id", "base_goal_revision", "base_intent_digest", "base_work_digest")}
        amendment.update(proposal_digest=proposal["digest"], operation_id=parameters["operation_id"],
            authority_source_sha256=goal["authority_source_sha256"])
        snapshot = _execute(registry_path=registry_path, runtime_root=root, goal_ref=goal_ref,
            receiver_agent_id=receiver_agent_id, request_id=reference["request_id"], actor_agent_id=receiver_agent_id,
            operation="reserve", parameters={"operation_id": parameters["operation_id"],
                "expected_revision": reference["request_revision"], "expected_digest": reference["request_digest"]},
            resolved_amendment=amendment, execute=execute, include_entry=True)
        reservation = snapshot["result"]["reservation"]
    bound = reservation["binding"]
    if (bound["proposal_id"] != parameters["proposal_id"] or bound["proposal_digest"] != parameters["proposal_digest"]
            or {"request_id": bound["request_id"], "request_revision": bound["request_revision"], "request_digest": bound["request_digest"]} != reference):
        raise ValueError("original reserved effect identity conflict")
    context = {"entry": snapshot["_entry"], "reservation_digest": reservation["digest"], "preview": not execute}
    effect = {key: parameters[key] for key in ("operation_id", "proposal_id", "proposal_digest")}
    if not execute:
        preview = _goal(registry_path, root, goal_ref, receiver_agent_id, "commit", effect, context=context,
            replan_configuration_reader=replan_configuration_reader)
        return {**preview, "request_preview": snapshot["view"], "request_reservation_recorded": not snapshot.get("planned", False)}
    try:
        attempted = _goal(registry_path, root, goal_ref, receiver_agent_id, "commit", effect, context=context, execute=True,
            replan_configuration_reader=replan_configuration_reader)
        observation = {key: attempted[key] for key in ("status", "reason_code", "reason") if key in attempted}
    except (OSError, ValueError, EffectRuntimeRejected) as exc:
        # A failed response does not establish no effect. The exact receipt
        # read below owns that conclusion and keeps an absent result pending.
        observation = {"error": str(exc)}
    return _finish(registry=registry_path, root=root, ref=goal_ref, receiver=receiver_agent_id,
        actor=receiver_agent_id, snapshot=snapshot, reservation=reservation, execute=True, attempt_observation=observation,
        replan_configuration_reader=replan_configuration_reader)


def recover_request_amendment(*, registry_path: Path, runtime_root: Path, goal_ref: Mapping[str, str],
                              replan_configuration_reader: Callable[[Mapping[str, Any], Path], Mapping[str, Any]],
                              receiver_agent_id: str, request_id: str, actor_agent_id: str | None,
                              execute: bool = False) -> dict[str, Any]:
    root = _root(registry_path, runtime_root)
    snapshot = request_amendment_snapshot(registry_path=registry_path, runtime_root=root, goal_ref=goal_ref,
        receiver_agent_id=receiver_agent_id, request_id=request_id, actor_agent_id=actor_agent_id)
    reservation = snapshot["view"]["active_reservation"]
    if reservation is None:
        return {key: value for key, value in snapshot.items() if not key.startswith("_")}
    observation = None
    if execute and snapshot["view"]["recovery_operation"] == "abort_request":
        bound = reservation["binding"]
        try:
            attempted = _goal(registry_path, root, goal_ref, receiver_agent_id, "abort_request",
                {key: bound[key] for key in ("operation_id", "proposal_id", "proposal_digest")},
                context={"entry": snapshot["_entry"], "reservation_digest": reservation["digest"]}, execute=True,
                replan_configuration_reader=replan_configuration_reader)
            observation = {key: attempted[key] for key in ("status", "reason_code", "reason") if key in attempted}
        except (OSError, ValueError, EffectRuntimeRejected) as exc:
            observation = {"error": str(exc)}  # Read the receipt; never infer an abort.
    return _finish(registry=registry_path, root=root, ref=goal_ref, receiver=receiver_agent_id,
        actor=actor_agent_id, snapshot=snapshot, reservation=reservation, execute=execute, attempt_observation=observation,
        replan_configuration_reader=replan_configuration_reader)


def control_request(*, registry_path: Path, runtime_root: Path, goal_ref: Mapping[str, str],
                    replan_configuration_reader: Callable[[Mapping[str, Any], Path], Mapping[str, Any]],
                    receiver_agent_id: str, request_id: str, actor_agent_id: str | None,
                    operation: str, parameters: Mapping[str, Any] | None = None, execute: bool = False) -> dict[str, Any]:
    """Shared source UI/CLI path; cancellation settles only through its owner."""
    common: dict[str, Any] = dict(registry_path=registry_path, runtime_root=runtime_root, goal_ref=goal_ref,
        receiver_agent_id=receiver_agent_id, request_id=request_id, actor_agent_id=actor_agent_id)
    if operation == "recover":
        if parameters:
            raise ValueError("request recovery takes its binding from the original source")
        return recover_request_amendment(**common, execute=execute, replan_configuration_reader=replan_configuration_reader)
    if operation not in {"inspect", "revise", "cancel"}:
        raise ValueError("source request control operation is unsupported")
    result = execute_request_control(**common, operation=operation, parameters=parameters, execute=execute)
    if execute and (operation == "cancel" or result["view"].get("recovery_operation") == "abort_request"):
        recovery = recover_request_amendment(**common, execute=True, replan_configuration_reader=replan_configuration_reader)
        return {**result, "ok": recovery["ok"], "view": recovery.get("request_control", recovery.get("view")),
            "effect_recovery": recovery}
    return result
