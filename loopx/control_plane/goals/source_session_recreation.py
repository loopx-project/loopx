from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...file_lock import exclusive_cross_runtime_file_lock
from ...paths import resolve_runtime_root
from ..coordination.shadow_management import shadow_maintenance_lock_target
from ..effect_runtime import (
    CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
    effect_runtime_result,
)
from ..projects.registry_codec import (
    load_project_registry,
    source_session_registry_transaction,
)
from ..runtime.time import now_local_iso
from .acceptance import transition_goal_acceptance_lifecycle
from .source_session_registry_state import (
    GOAL_INSTANCE_ID,
    alias_digest,
    canonical_digest,
    current_goal_ref,
    exact_goal_ref,
    guard_path,
    lifetime_root,
    prior_operation_receipt,
    required_list,
    session_binding_records,
    write_journal,
)
from .source_session_turn_effects import (
    decide_source_turn_effect_close_locked,
    decide_source_turn_effect_publish_locked,
    decide_source_turn_effect_repair_locked,
    drain_releasable_source_turn_effects,
    write_source_turn_effect_gate_locked,
)


@dataclass(frozen=True, slots=True)
class RecreateGoalRequest:
    registry_path: Path
    goal_id: str
    goal_instance_id: str
    operation_id: str


@dataclass(frozen=True, slots=True)
class _RecreationState:
    registry: dict[str, Any]
    active_goal_ref: dict[str, str]
    reserved_goal_ref: dict[str, str]
    bindings: list[dict[str, Any]]
    session_receipts: list[dict[str, Any]]
    lifetime_receipts: list[dict[str, Any]]
    retiring_bindings: list[dict[str, Any]]
    decision: dict[str, Any]


def _canonical_runtime_root(registry_path: Path) -> Path:
    registry = load_project_registry(registry_path)
    return resolve_runtime_root(registry, registry_path=registry_path).resolve()


def _canonical_writer_guard_path(registry_path: Path, goal_id: str) -> Path:
    return shadow_maintenance_lock_target(
        _canonical_runtime_root(registry_path),
        goal_id,
    )


def _acceptance_lifecycle_operation_id(
    request: RecreateGoalRequest,
    phase: str,
) -> str:
    digest = hashlib.sha256(
        f"{request.operation_id}\0{phase}".encode("utf-8")
    ).hexdigest()
    return f"goal-acceptance-lifecycle:{phase}:{digest}"


def _retire_acceptance_lifecycle(
    request: RecreateGoalRequest,
    *,
    requested_goal_ref: dict[str, str],
) -> None:
    runtime_root = _canonical_runtime_root(request.registry_path)
    transition_goal_acceptance_lifecycle(
        runtime_root=runtime_root,
        goal_id=request.goal_id,
        operation_id=_acceptance_lifecycle_operation_id(request, "bind"),
        transition={"kind": "bind_existing", "goal_ref": requested_goal_ref},
    )
    transition_goal_acceptance_lifecycle(
        runtime_root=runtime_root,
        goal_id=request.goal_id,
        operation_id=_acceptance_lifecycle_operation_id(request, "retire"),
        transition={"kind": "retire", "goal_ref": requested_goal_ref},
    )


def _activate_recreated_acceptance_lifecycle(
    request: RecreateGoalRequest,
    *,
    retired_goal_ref: dict[str, str],
    goal_ref: dict[str, str],
) -> None:
    transition_goal_acceptance_lifecycle(
        runtime_root=_canonical_runtime_root(request.registry_path),
        goal_id=request.goal_id,
        operation_id=_acceptance_lifecycle_operation_id(request, "activate"),
        transition={
            "kind": "reconcile_recreated",
            "retired_goal_ref": retired_goal_ref,
            "goal_ref": goal_ref,
        },
    )


def _recreation_journal_path(
    registry_path: Path,
    *,
    goal_id: str,
    operation_id: str,
) -> Path:
    operation_digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    return (
        lifetime_root(registry_path)
        / "recreations"
        / alias_digest(goal_id)
        / f"{operation_digest}.json"
    )


def _read_recreation_journal(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Goal recreation journal must be a JSON object")
    required = {
        "schema_version",
        "operation_id",
        "request_digest",
        "retired_goal_ref",
        "new_goal_ref",
        "reserved_at",
        "phase",
    }
    if set(value) != required:
        raise ValueError("Goal recreation journal shape is invalid")
    if (
        value.get("schema_version") != "loopx_goal_recreation_journal_v1"
        or not isinstance(value.get("operation_id"), str)
        or not isinstance(value.get("request_digest"), str)
        or not isinstance(value.get("reserved_at"), str)
        or value.get("phase") not in {"reserved", "published"}
    ):
        raise ValueError("Goal recreation journal content is invalid")
    for field in ("retired_goal_ref", "new_goal_ref"):
        goal_ref = value.get(field)
        if (
            not isinstance(goal_ref, dict)
            or not isinstance(goal_ref.get("goal_id"), str)
            or not isinstance(goal_ref.get("goal_instance_id"), str)
            or not GOAL_INSTANCE_ID.fullmatch(goal_ref["goal_instance_id"])
        ):
            raise ValueError(f"Goal recreation journal {field} is invalid")
    return value


def _recreation_digest(request: RecreateGoalRequest) -> str:
    return canonical_digest(
        {
            "schema_version": "loopx_goal_recreation_request_v1",
            "operation_id": request.operation_id,
            "goal_ref": exact_goal_ref(
                request.goal_id,
                request.goal_instance_id,
            ),
        }
    )


def _recreation_result(
    request: RecreateGoalRequest,
    *,
    receipt: dict[str, Any],
    replayed: bool,
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": "loopx_goal_recreation_v1",
        "changed": True,
        "replayed": replayed,
        "registry": str(request.registry_path),
        "retired_goal_ref": copy.deepcopy(receipt["retired_goal_ref"]),
        "goal_ref": copy.deepcopy(receipt["new_goal_ref"]),
        "retired_session_ids": list(receipt["retired_session_ids"]),
        "receipt": copy.deepcopy(receipt),
        "execution_authority": False,
    }


def _evaluate_recreation(
    registry: dict[str, Any],
    request: RecreateGoalRequest,
    *,
    requested_goal_ref: dict[str, str],
    request_digest: str,
    journal: dict[str, Any] | None,
) -> _RecreationState:
    active_goal_ref, _goal = current_goal_ref(
        registry,
        goal_id=request.goal_id,
    )
    bindings = session_binding_records(registry)
    session_receipts = required_list(registry, "session_receipts")
    lifetime_receipts = required_list(registry, "lifetime_receipts")
    prior_receipt = prior_operation_receipt(
        lifetime_receipts,
        operation_id=request.operation_id,
    )
    session_operation_receipt = prior_operation_receipt(
        session_receipts,
        operation_id=request.operation_id,
    )
    if session_operation_receipt is not None and (
        session_operation_receipt.get("schema_version")
        != "loopx_source_session_retirement_receipt_v1"
        or session_operation_receipt.get("request_digest") != request_digest
    ):
        raise ValueError(
            "source-session operation_id was reused across lifecycle operations"
        )
    if journal is not None:
        reserved_goal_ref = copy.deepcopy(journal["new_goal_ref"])
    elif prior_receipt is not None:
        candidate = prior_receipt.get("new_goal_ref")
        if not isinstance(candidate, dict):
            raise ValueError("Goal recreation receipt new_goal_ref is invalid")
        reserved_goal_ref = copy.deepcopy(candidate)
    else:
        reserved_goal_ref = {
            "goal_id": request.goal_id,
            "goal_instance_id": f"ginst_{uuid4().hex}",
        }
    retiring_bindings = [
        binding
        for binding in bindings
        if binding.get("foreground_goal_ref") == requested_goal_ref
    ]
    decision = effect_runtime_result(
        "goal.source_session.recreate.decide",
        {
            "profile_id": registry["profile_id"],
            "operation_id": request.operation_id,
            "request_digest": request_digest,
            "requested_goal_ref": requested_goal_ref,
            "current_goal_ref": active_goal_ref,
            "reserved_goal_ref": reserved_goal_ref,
            "prior_receipt": prior_receipt,
            "lifetime_receipt_count": len(lifetime_receipts),
            "session_receipt_count": len(session_receipts),
            "retiring_binding_count": len(retiring_bindings),
        },
    )
    if not isinstance(decision, dict):
        raise RuntimeError("Goal recreation decision must be an object")
    if decision.get("kind") == "reject":
        raise ValueError(f"source-session recreation rejected: {decision.get('code')}")
    if decision.get("kind") not in {"commit", "replay"}:
        raise RuntimeError("Goal recreation decision kind is unsupported")
    return _RecreationState(
        registry=registry,
        active_goal_ref=active_goal_ref,
        reserved_goal_ref=reserved_goal_ref,
        bindings=bindings,
        session_receipts=session_receipts,
        lifetime_receipts=lifetime_receipts,
        retiring_bindings=retiring_bindings,
        decision=decision,
    )


def _reserved_journal(
    request: RecreateGoalRequest,
    *,
    requested_goal_ref: dict[str, str],
    reserved_goal_ref: dict[str, str],
    request_digest: str,
) -> dict[str, Any]:
    return {
        "schema_version": "loopx_goal_recreation_journal_v1",
        "operation_id": request.operation_id,
        "request_digest": request_digest,
        "retired_goal_ref": copy.deepcopy(requested_goal_ref),
        "new_goal_ref": copy.deepcopy(reserved_goal_ref),
        "reserved_at": now_local_iso(),
        "phase": "reserved",
    }


def _drain_required_result(
    request: RecreateGoalRequest,
    *,
    requested_goal_ref: dict[str, str],
    pending_effects: list[dict[str, str]],
    changed: bool,
) -> dict[str, Any]:
    return {
        "ok": False,
        "schema_version": "loopx_goal_recreation_v1",
        "status": "drain_required",
        "changed": changed,
        "replayed": not changed,
        "gate_state": "closing",
        "registry": str(request.registry_path),
        "retired_goal_ref": copy.deepcopy(requested_goal_ref),
        "pending_effects": pending_effects,
        "recovery_action": (
            "Resolve every pending Turn effect, then retry recreate-goal "
            "with the same operation_id."
        ),
        "execution_authority": False,
    }


def recreate_goal_instance(request: RecreateGoalRequest) -> dict[str, Any]:
    """Close A's effect gate, drain admitted work, then publish reserved B."""

    requested_goal_ref = exact_goal_ref(
        request.goal_id,
        request.goal_instance_id,
    )
    request_digest = _recreation_digest(request)
    guard = guard_path(request.registry_path, request.goal_id)
    journal_path = _recreation_journal_path(
        request.registry_path,
        goal_id=request.goal_id,
        operation_id=request.operation_id,
    )

    with exclusive_cross_runtime_file_lock(
        guard,
        operation="source_session_goal_lifetime_close",
    ):
        journal = _read_recreation_journal(journal_path)
        if journal is not None and (
            journal["operation_id"] != request.operation_id
            or journal["request_digest"] != request_digest
            or journal["retired_goal_ref"] != requested_goal_ref
        ):
            raise ValueError("Goal recreation operation_id conflicts with its journal")
        with source_session_registry_transaction(
            request.registry_path,
            operation="source_session_goal_recreate_prepare",
        ) as transaction:
            state = _evaluate_recreation(
                transaction.payload_copy(),
                request,
                requested_goal_ref=requested_goal_ref,
                request_digest=request_digest,
                journal=journal,
            )
        if state.decision["kind"] == "replay":
            replay_receipt = state.decision.get("receipt")
            if not isinstance(replay_receipt, dict):
                raise RuntimeError("Goal recreation replay omitted its receipt")
            next_gate = decide_source_turn_effect_repair_locked(
                registry_path=request.registry_path,
                goal_id=request.goal_id,
                requested_goal_ref=requested_goal_ref,
                current_goal_ref=state.active_goal_ref,
                reserved_goal_ref=state.reserved_goal_ref,
                operation_id=request.operation_id,
                request_digest=request_digest,
            )
            if next_gate is not None:
                write_source_turn_effect_gate_locked(
                    registry_path=request.registry_path,
                    goal_id=request.goal_id,
                    gate=next_gate,
                )
            if journal is None:
                journal = {
                    **_reserved_journal(
                        request,
                        requested_goal_ref=requested_goal_ref,
                        reserved_goal_ref=state.reserved_goal_ref,
                        request_digest=request_digest,
                    ),
                    "reserved_at": replay_receipt["committed_at"],
                }
            write_journal(journal_path, {**journal, "phase": "published"})
            if state.active_goal_ref == state.reserved_goal_ref:
                _activate_recreated_acceptance_lifecycle(
                    request,
                    retired_goal_ref=requested_goal_ref,
                    goal_ref=state.reserved_goal_ref,
                )
            return _recreation_result(
                request,
                receipt=replay_receipt,
                replayed=True,
            )
        closing_gate, gate_changed = decide_source_turn_effect_close_locked(
            registry_path=request.registry_path,
            goal_id=request.goal_id,
            requested_goal_ref=requested_goal_ref,
            current_goal_ref=state.active_goal_ref,
            reserved_goal_ref=state.reserved_goal_ref,
            operation_id=request.operation_id,
            request_digest=request_digest,
        )
        if journal is None:
            journal = _reserved_journal(
                request,
                requested_goal_ref=requested_goal_ref,
                reserved_goal_ref=state.reserved_goal_ref,
                request_digest=request_digest,
            )
            write_journal(journal_path, journal)
        write_source_turn_effect_gate_locked(
            registry_path=request.registry_path,
            goal_id=request.goal_id,
            gate=closing_gate,
        )
        _retire_acceptance_lifecycle(
            request,
            requested_goal_ref=requested_goal_ref,
        )

    drain_result = drain_releasable_source_turn_effects(
        registry_path=request.registry_path,
        goal_id=request.goal_id,
        requested_goal_ref=requested_goal_ref,
    )
    if drain_result.pending_effects:
        return _drain_required_result(
            request,
            requested_goal_ref=requested_goal_ref,
            pending_effects=drain_result.pending_effects,
            changed=gate_changed or drain_result.changed,
        )

    with exclusive_cross_runtime_file_lock(
        guard,
        operation="source_session_goal_lifetime_publish",
    ):
        with exclusive_cross_runtime_file_lock(
            _canonical_writer_guard_path(request.registry_path, request.goal_id),
            operation="source_session_goal_canonical_publish",
            timeout_seconds=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
        ):
            journal = _read_recreation_journal(journal_path)
            if journal is None:
                raise RuntimeError("Goal recreation reservation journal is missing")
            with source_session_registry_transaction(
                request.registry_path,
                operation="source_session_goal_recreate",
            ) as transaction:
                state = _evaluate_recreation(
                    transaction.payload_copy(),
                    request,
                    requested_goal_ref=requested_goal_ref,
                    request_digest=request_digest,
                    journal=journal,
                )
                if state.decision["kind"] == "replay":
                    replay_receipt = state.decision.get("receipt")
                    if not isinstance(replay_receipt, dict):
                        raise RuntimeError("Goal recreation replay omitted its receipt")
                    next_gate = decide_source_turn_effect_repair_locked(
                        registry_path=request.registry_path,
                        goal_id=request.goal_id,
                        requested_goal_ref=requested_goal_ref,
                        current_goal_ref=state.active_goal_ref,
                        reserved_goal_ref=state.reserved_goal_ref,
                        operation_id=request.operation_id,
                        request_digest=request_digest,
                    )
                    if next_gate is not None:
                        write_source_turn_effect_gate_locked(
                            registry_path=request.registry_path,
                            goal_id=request.goal_id,
                            gate=next_gate,
                        )
                    write_journal(journal_path, {**journal, "phase": "published"})
                    result = _recreation_result(
                        request,
                        receipt=replay_receipt,
                        replayed=True,
                    )
                else:
                    next_gate = decide_source_turn_effect_publish_locked(
                        registry_path=request.registry_path,
                        goal_id=request.goal_id,
                        requested_goal_ref=requested_goal_ref,
                        current_goal_ref=state.active_goal_ref,
                        reserved_goal_ref=state.reserved_goal_ref,
                        operation_id=request.operation_id,
                        request_digest=request_digest,
                    )
                    committed_at = now_local_iso()
                    retired_session_ids = sorted(
                        str(binding["session_id"])
                        for binding in state.retiring_bindings
                    )
                    receipt = {
                        "schema_version": "loopx_goal_recreation_receipt_v1",
                        "operation_id": request.operation_id,
                        "request_digest": request_digest,
                        "retired_goal_ref": copy.deepcopy(requested_goal_ref),
                        "new_goal_ref": copy.deepcopy(state.reserved_goal_ref),
                        "retired_session_ids": retired_session_ids,
                        "committed_at": committed_at,
                    }
                    state.registry["goals"] = [
                        {
                            **candidate,
                            "goal_instance_id": (
                                state.reserved_goal_ref["goal_instance_id"]
                            ),
                            "execution_authority": False,
                        }
                        if candidate.get("id") == request.goal_id
                        else candidate
                        for candidate in required_list(state.registry, "goals")
                    ]
                    state.registry["session_bindings"] = [
                        binding
                        for binding in state.bindings
                        if binding.get("foreground_goal_ref") != requested_goal_ref
                    ]
                    session_receipts = state.session_receipts
                    if retired_session_ids:
                        session_receipts = [
                            *session_receipts,
                            {
                                "schema_version": (
                                    "loopx_source_session_retirement_receipt_v1"
                                ),
                                "operation": "retire_bindings",
                                "operation_id": request.operation_id,
                                "request_digest": request_digest,
                                "retired_goal_ref": copy.deepcopy(
                                    requested_goal_ref
                                ),
                                "session_ids": retired_session_ids,
                                "committed_at": committed_at,
                            },
                        ]
                    state.registry["session_receipts"] = session_receipts
                    retired = state.registry.get("retired_goal_instances", [])
                    if not isinstance(retired, list) or any(
                        not isinstance(item, dict) for item in retired
                    ):
                        raise ValueError(
                            "source-session retired_goal_instances must be a list of objects"
                        )
                    state.registry["retired_goal_instances"] = [
                        *retired,
                        {
                            "goal_ref": copy.deepcopy(requested_goal_ref),
                            "successor_goal_ref": copy.deepcopy(
                                state.reserved_goal_ref
                            ),
                            "operation_id": request.operation_id,
                            "retired_at": committed_at,
                        },
                    ]
                    state.registry["lifetime_receipts"] = [
                        *state.lifetime_receipts,
                        receipt,
                    ]
                    state.registry["updated_at"] = committed_at
                    transaction.commit(state.registry)
                    write_source_turn_effect_gate_locked(
                        registry_path=request.registry_path,
                        goal_id=request.goal_id,
                        gate=next_gate,
                    )
                    write_journal(journal_path, {**journal, "phase": "published"})
                    result = _recreation_result(
                        request,
                        receipt=receipt,
                        replayed=False,
                    )

        _activate_recreated_acceptance_lifecycle(
            request,
            retired_goal_ref=requested_goal_ref,
            goal_ref=state.reserved_goal_ref,
        )
        return result
