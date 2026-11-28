from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path

from ...agent_registry import registered_agent_ids_from_registry
from ...turn_identity import normalize_turn_instance_id
from ..agents.capability_gate import runtime_capabilities_for_cli_projection
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..runtime.time import now_local_iso
from ..scheduler.monitor_poll_writeback import (
    resolve_monitor_todo_item,
    write_monitor_poll_todo_state,
)
from ..scheduler.monitor_todo import monitor_todo_is_due
from ..todos.authoring_scope import plan_todo_authoring_scope
from ..todos.contract import (
    TODO_TASK_CLASS_MONITOR,
    normalize_todo_claimed_by,
    normalize_todo_id,
    normalize_todo_resume_when,
)
from ..todos.external_wait_contract import (
    build_monitor_advancement_authoring_contract,
)
from ..todos.todo_semantics import todo_item_task_class
from .accounting_admission import quota_accounting_admission
from .decision_summary import compact_quota_decision, quota_decision_agent_id
from .monitor_poll_readback import (
    QUOTA_MONITOR_POLL_CLASSIFICATION as QUOTA_MONITOR_POLL_CLASSIFICATION,
    find_quota_monitor_poll_turn as find_quota_monitor_poll_turn,
)
from .spend_sources import DEFAULT_SLOT_SPEND_SOURCE

Payload = dict[str, object]
PayloadView = Mapping[str, object]

QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA = (
    "loopx_quota_monitor_poll_commit_request_v0"
)
QUOTA_MONITOR_POLL_COMMIT_RESULT_SCHEMA = (
    "loopx_quota_monitor_poll_commit_result_v0"
)


class _NativeMonitorPollRejected(ValueError):
    def __init__(self, message: str, *, diagnostic_code: str) -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code


def _mapping(value: object) -> Payload:
    return {str(key): entry for key, entry in value.items()} if isinstance(value, Mapping) else {}


def _clean_text(value: object) -> str | None:
    return str(value or "").strip() or None


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _opt_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str_list(value: object) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item) for item in value]
    return []


def _list_items(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _normalized_poll_targets(
    *, todo_id: str | None, target_key: str | None,
    result_hash: str | None, turn_instance_id: str | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    return (
        normalize_todo_id(todo_id) if todo_id else None,
        _clean_text(target_key),
        _clean_text(result_hash),
        normalize_turn_instance_id(turn_instance_id),
    )


def _monitor_candidate(value: object) -> Payload | None:
    if not isinstance(value, dict) or todo_item_task_class(value) != TODO_TASK_CLASS_MONITOR:
        return None
    todo_id = normalize_todo_id(value.get("todo_id"))
    target_key = _clean_text(value.get("target_key"))
    if not todo_id and not target_key:
        return None
    resume_when = normalize_todo_resume_when(value.get("resume_when"))
    return {
        "todo_id": todo_id,
        "target_key": target_key,
        "task_class": TODO_TASK_CLASS_MONITOR,
        **({"resume_when": resume_when} if resume_when else {}),
    }


def _due_monitor_candidates(before: PayloadView) -> list[Payload]:
    contract = _mapping(before.get("work_lane_contract"))
    if contract.get("must_attempt_work") is not True:
        return []
    if contract.get("obligation") == "attempt_due_monitor":
        selected = _monitor_candidate(before.get("agent_lane_next_action"))
        if selected:
            return [selected]
        selected_todo_id = normalize_todo_id(contract.get("selected_todo_id"))
        for value in _list_items(contract.get("monitor_due_items")):
            candidate = _monitor_candidate(value)
            if candidate and (
                not selected_todo_id or candidate["todo_id"] == selected_todo_id
            ):
                return [candidate]
        return []
    reason_codes = contract.get("reason_codes")
    if not isinstance(reason_codes, list) or "due_monitor_context" not in reason_codes:
        return []
    summary = _mapping(before.get("agent_todo_summary"))
    candidates: list[Payload] = []
    for value in _list_items(summary.get("monitor_due_items")):
        candidate = _monitor_candidate(value)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _vision_wait_state(before: PayloadView) -> Payload:
    if before.get("schema_version") == "goal_vision_wait_state_v0":
        return _mapping(before)
    return _mapping(before.get("vision_wait_state")) or _mapping(
        _mapping(before.get("goal_frontier_projection")).get("vision_wait_state")
    )


def resolve_due_monitor_candidate(
    *,
    registry_path: Path | None,
    runtime_root: Path | None,
    goal_id: str,
    todo_id: str | None,
    target_key: str | None,
) -> Payload:
    if registry_path is None or not (todo_id or target_key):
        return {}
    try:
        item = resolve_monitor_todo_item(
            registry_path=registry_path,
            goal_id=goal_id,
            runtime_root=runtime_root,
            todo_id=todo_id,
            target_key=target_key,
        )
    except ValueError:
        return {}
    resume_when = normalize_todo_resume_when(item.get("resume_when"))
    if resume_when and item.get("resume_ready") is None:
        from ..todos.list_readback import list_goal_todos

        # Target selection returns the stored Todo, where readiness may not be
        # materialized. Read the canonical evaluation for that same target.
        selected_id = normalize_todo_id(item.get("todo_id"))
        if selected_id is None:
            return {}
        evaluated = list_goal_todos(
            registry_path=registry_path,
            goal_id=goal_id,
            role="agent",
            todo_id=selected_id,
            runtime_root_arg=str(runtime_root) if runtime_root else None,
        ).get("todo")
        if (
            not isinstance(evaluated, dict)
            or evaluated.get("todo_id") != selected_id
            or evaluated.get("target_key") != item.get("target_key")
            or evaluated.get("archive_state") == "archive"
            or normalize_todo_resume_when(evaluated.get("resume_when")) != resume_when
        ):
            return {}
        item = evaluated
    if not monitor_todo_is_due(item):
        return {}
    return {
        "due": True,
        "todo_id": normalize_todo_id(item.get("todo_id")),
        "target_key": _clean_text(item.get("target_key")),
        "task_class": todo_item_task_class(item),
        "claimed_by": normalize_todo_claimed_by(item.get("claimed_by")),
    }


def _monitor_poll_receipt_path(runtime_root: Path, goal_id: str, effect_id: str) -> Path:
    return (runtime_root / "goals" / goal_id / "runs" / ".transactions" /
            "quota-monitor-poll" / f"{hashlib.sha256(effect_id.encode()).hexdigest()[:24]}.json")


def resolve_monitor_poll_target(
    *, registry_path: Path | None, runtime_root: Path | None, goal_id: str,
    target_key: str | None, before: PayloadView, turn_instance_id: str | None,
    execute: bool, goal_ref: PayloadView | None,
) -> tuple[str | None, str | None]:
    due = resolve_due_monitor_candidate(registry_path=registry_path, runtime_root=runtime_root,
        goal_id=goal_id, todo_id=None, target_key=target_key)
    if due:
        return normalize_todo_id(due["todo_id"]), None
    if not execute or runtime_root is None or not turn_instance_id or not target_key or registry_path is None:
        return None, None
    try:
        item = resolve_monitor_todo_item(registry_path=registry_path, runtime_root=runtime_root,
            goal_id=goal_id, target_key=target_key)
    except ValueError:
        return None, None
    todo_id = normalize_todo_id(item.get("todo_id"))
    agent_id = quota_decision_agent_id(_mapping(before))
    normalized_turn_id = normalize_turn_instance_id(turn_instance_id)
    effect_id = _monitor_poll_effect_id(runtime_root=runtime_root, goal_id=goal_id,
        agent_id=agent_id, turn_instance_id=normalized_turn_id,
        todo_id=todo_id, target_key=target_key, goal_ref=goal_ref)
    expected_effect = f"quota-monitor-poll:{goal_id}:{agent_id}:{normalized_turn_id}:todo:{todo_id}"
    if effect_id != expected_effect:
        # Historical target/turn-only effects can have a null Todo on the wire.
        # The provider's resolved Todo cannot rewrite their frozen fingerprint.
        return None, None
    # Existence is only a transport route to the original operation. TypeScript
    # validates scope, fingerprint and frozen admission before replay/recovery.
    # Without that operation, retain current due admission and the original wire.
    if _monitor_poll_receipt_path(runtime_root, goal_id, effect_id).exists():
        return todo_id, effect_id
    return None, None


def _runtime_capabilities(before: PayloadView) -> list[str] | None:
    values = _mapping(before.get("agent_identity")).get("runtime_available_capabilities")
    return runtime_capabilities_for_cli_projection(values) if isinstance(values, list) else None


def _decision_packet(
    before: PayloadView,
    *,
    goal_id: str,
    todo_id: str | None,
    target_key: str | None,
    registry_path: Path | None = None,
    runtime_root: Path | None = None,
    authorized_due_monitor_poll: bool | None = None,
    todo_id_explicit: bool = False,
) -> Payload:
    before_dict = _mapping(before)
    agent_id = quota_decision_agent_id(before_dict)
    lane = _mapping(before.get("work_lane_contract"))
    due_candidates = _due_monitor_candidates(before)
    registry_due = resolve_due_monitor_candidate(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=todo_id,
        target_key=target_key,
    )
    if registry_due:
        registry_due["todo_id_explicit"] = todo_id_explicit and todo_id is not None
    if authorized_due_monitor_poll is False:
        due_candidates = []
        registry_due = {}
    elif authorized_due_monitor_poll is True:
        lane.update(must_attempt_work=True, obligation="attempt_due_monitor")
        requested: Payload = {
            "todo_id": todo_id,
            "target_key": target_key,
            "task_class": TODO_TASK_CLASS_MONITOR,
        }
        if requested not in due_candidates:
            due_candidates.append(requested)
    gate_scope: Payload | None = None
    if (before.get("requires_user_action") is True and registry_path is not None
        and registry_due.get("todo_id")):
        from ...control_plane.todos.list_readback import list_goal_todos
        from ..todos.decision_scope import todo_gate_scope_projections
        snapshot = list_goal_todos(registry_path=registry_path, goal_id=goal_id,
            runtime_root_arg=str(runtime_root) if runtime_root else None, limit=None)
        records = [item for item in _list_items(snapshot.get("todos")) if isinstance(item, dict)]
        targets = [item for item in records if item.get("todo_id") == registry_due["todo_id"]]
        if len(targets) == 1:
            gate_scope = todo_gate_scope_projections(
                [item for item in records if item.get("role") == "user"], targets,
                agent_id=agent_id)[0]
    return {
        **compact_quota_decision(before_dict),
        "goal_id": goal_id,
        "agent_id": agent_id,
        "recommended_action": before.get("recommended_action"),
        "reason": before.get("reason"),
        "requires_user_action": before.get("requires_user_action") is True,
        "heartbeat_recommendation": _mapping(
            before.get("heartbeat_recommendation")
        ),
        "work_lane_contract": lane,
        "external_evidence_observation": _mapping(
            before.get("external_evidence_observation")
        ),
        "vision_wait_state": _vision_wait_state(before),
        "due_monitor_candidates": due_candidates,
        "registry_due_monitor": registry_due,
        **({"auxiliary_gate_scope": gate_scope} if gate_scope is not None else {}),
    }


def _observation_packet(
    *,
    before: PayloadView,
    reason_summary: str | None,
    todo_id: str | None,
    target_key: str | None,
    result_hash: str | None,
    material_change: bool,
    material_change_scope: str | None,
    observed_task_repository: str | None = None,
    agent_id: str | None = None,
    settlement_todo_id: str | None = None,
    cadence: str | None = None,
    next_due_at: str | None = None,
    next_agent_todo: str | None = None,
    next_action_kind: str | None = None,
    next_task_repository: str | None = None,
    next_required_capabilities: Sequence[str] | None = None,
    next_continuation_policy: str | None = None,
    next_target_key: str | None = None,
    next_user_todo: str | None = None,
    next_user_task_class: str | None = None,
    next_claimed_by: str | None = None,
    next_agent_priority: str | None = None,
    next_user_priority: str | None = None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
) -> Payload:
    proof = ({"idempotency_key": task_lease_idempotency_key,
              "expected_version": task_lease_expected_version}
             if task_lease_idempotency_key is not None or task_lease_expected_version is not None else None)
    return {
        **({"lease_proof": proof} if proof is not None else {}),
        "actor_agent_id": normalize_todo_claimed_by(agent_id)
        or quota_decision_agent_id(_mapping(before)),
        "settlement_todo_id": settlement_todo_id,
        "reason_summary": reason_summary,
        "todo_id": todo_id,
        "target_key": target_key,
        **({"observed_task_repository": observed_task_repository} if observed_task_repository is not None else {}),
        "result_hash": result_hash,
        "material_change": material_change,
        **({"material_change_scope": material_change_scope} if material_change_scope is not None else {}),
        "cadence": cadence,
        "next_due_at": next_due_at,
        "next_agent_todo": next_agent_todo,
        "next_action_kind": next_action_kind,
        "next_task_repository": next_task_repository,
        "next_required_capabilities": list(next_required_capabilities or []),
        "next_continuation_policy": next_continuation_policy,
        "next_target_key": next_target_key,
        "next_user_todo": next_user_todo,
        "next_user_task_class": next_user_task_class,
        "next_claimed_by": next_claimed_by,
        **({"next_agent_priority": next_agent_priority} if next_agent_priority is not None else {}),
        **({"next_user_priority": next_user_priority} if next_user_priority is not None else {}),
    }


def _index_digest(index_path: Path) -> str | None:
    try:
        content = index_path.read_bytes()
    except FileNotFoundError:
        return None
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _native_result(request: PayloadView) -> Payload:
    try:
        result = effect_runtime_result("quota.monitor_poll.commit", request)
    except EffectRuntimeRejected as exc:
        raise _NativeMonitorPollRejected(
            str(exc),
            diagnostic_code=exc.diagnostic_code,
        ) from None
    if (
        not isinstance(result, Mapping)
        or result.get("schema_version")
        != QUOTA_MONITOR_POLL_COMMIT_RESULT_SCHEMA
    ):
        raise RuntimeError("TypeScript quota monitor-poll result shape mismatch")
    return _mapping(result)


def _request(
    *,
    phase: str,
    effect_id: str,
    runtime_root: Path | None,
    goal_id: str,
    source: str,
    generated_at: str,
    execute: bool,
    expected_index_digest: str | None,
    turn_instance_id: str | None,
    decision: Payload,
    observation: Payload,
    provider_receipt: PayloadView | None = None,
    status_reload_warning: PayloadView | None = None,
    goal_ref: PayloadView | None = None,
    source_admission: PayloadView | None = None,
) -> Payload:
    return {
        "schema_version": ("loopx_quota_monitor_poll_commit_request_v1" if observation.get("lease_proof") is not None
                           else QUOTA_MONITOR_POLL_COMMIT_REQUEST_SCHEMA),
        "phase": phase,
        "effect_id": effect_id,
        "runtime_root": str(runtime_root) if runtime_root is not None else None,
        "goal_id": goal_id,
        "source": source,
        "generated_at": generated_at,
        "execute": execute,
        "expected_index_digest": expected_index_digest,
        "turn_instance_id": turn_instance_id,
        "decision": decision,
        "observation": observation,
        "provider_receipt": _mapping(provider_receipt) if provider_receipt is not None else None,
        "status_reload_warning": (
            _mapping(status_reload_warning) if status_reload_warning is not None else None
        ),
        **({"goal_ref": _mapping(goal_ref)} if goal_ref is not None else {}),
        **({"source_admission": _mapping(source_admission)} if source_admission is not None else {}),
    }


def build_quota_monitor_poll_event(
    before: PayloadView,
    *,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    generated_at: str | None = None,
    reason_summary: str | None = None,
    todo_id: str | None = None,
    target_key: str | None = None,
    result_hash: str | None = None,
    material_change: bool = False,
    material_change_scope: str | None = None,
    authorized_due_monitor_poll: bool | None = None,
    turn_instance_id: str | None = None,
) -> Payload:
    goal_id = str(before.get("goal_id") or "").strip()
    safe_todo_id, safe_target_key, safe_result_hash, normalized_turn_id = _normalized_poll_targets(
        todo_id=todo_id, target_key=target_key, result_hash=result_hash,
        turn_instance_id=turn_instance_id,
    )
    decision = _decision_packet(
        before,
        goal_id=goal_id,
        todo_id=safe_todo_id,
        target_key=safe_target_key,
        authorized_due_monitor_poll=authorized_due_monitor_poll,
    )
    observation = _observation_packet(
        before=before,
        reason_summary=reason_summary,
        todo_id=safe_todo_id,
        target_key=safe_target_key,
        result_hash=safe_result_hash,
        material_change=material_change,
        material_change_scope=material_change_scope,
    )
    native = _native_result(
        _request(
            phase="event",
            effect_id=f"quota-monitor-poll:event:{uuid.uuid4().hex}",
            runtime_root=None,
            goal_id=goal_id,
            source=source,
            generated_at=generated_at or now_local_iso(),
            execute=False,
            expected_index_digest=None,
            turn_instance_id=normalized_turn_id,
            decision=decision,
            observation=observation,
        )
    )
    record = native.get("record")
    if not isinstance(record, Mapping):
        raise TypeError("TypeScript quota monitor-poll preview omitted its record")
    return _mapping(record)


def _persisted_monitor_effect_id(record: PayloadView | None) -> str | None:
    if not isinstance(record, Mapping):
        return None
    metadata = record.get("quota_monitor_poll_commit")
    if not isinstance(metadata, Mapping):
        return None
    return _clean_text(metadata.get("effect_id"))


def _monitor_poll_effect_id(
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
    turn_instance_id: str | None,
    todo_id: str | None,
    target_key: str | None,
    goal_ref: PayloadView | None,
) -> str:
    if not turn_instance_id:
        return f"quota-monitor-poll:{goal_id}:{uuid.uuid4().hex}"

    # Reuse a shipped turn-only receipt for an exact monitor identity. This
    # preserves crash recovery across upgrades while allowing later monitors
    # in the same settlement Turn to receive their own effect identity.
    existing = find_quota_monitor_poll_turn(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_instance_id=turn_instance_id,
        todo_id=todo_id,
        target_key=None if todo_id else target_key,
        goal_ref=goal_ref,
    )
    existing_effect_id = _persisted_monitor_effect_id(existing)
    if existing_effect_id:
        return existing_effect_id

    base = f"quota-monitor-poll:{goal_id}:{agent_id}:{turn_instance_id}"
    if todo_id:
        return f"{base}:todo:{todo_id}"
    if target_key:
        target_digest = hashlib.sha256(target_key.encode("utf-8")).hexdigest()
        return f"{base}:target:sha256:{target_digest}"
    return base


def monitor_poll_reservation_exists(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    todo_id: str,
    turn_instance_id: str,
    goal_ref: PayloadView | None = None,
) -> bool:
    """Check whether the original poll still owns a durable reservation."""
    effect_id = _monitor_poll_effect_id(
        runtime_root=runtime_root, goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
        turn_instance_id=turn_instance_id, target_key=None, goal_ref=goal_ref,
    )
    try:
        _monitor_poll_receipt_path(runtime_root, goal_id, effect_id).stat()
    except FileNotFoundError:
        return False
    return True


def _prepend_unique_run(target: Payload, key: str, record: Payload) -> None:
    entries = target.get(key)
    items = entries if isinstance(entries, list) else []
    if record not in items:
        target[key] = [record, *items]


def _status_with_monitor_poll(
    status_payload: PayloadView,
    *,
    goal_id: str,
    index_record: Payload,
) -> Payload:
    after_status = deepcopy(_mapping(status_payload))
    run_history = _mapping(after_status.get("run_history"))
    for goal in _list_items(run_history.get("goals")):
        if isinstance(goal, dict) and str(goal.get("id") or "") == goal_id:
            _prepend_unique_run(goal, "latest_runs", index_record)
            _prepend_unique_run(goal, "runs", index_record)
    _prepend_unique_run(run_history, "recent_runs", index_record)
    after_status["run_history"] = run_history
    return after_status


def _reload_status_after_monitor_writeback(
    status_payload: PayloadView,
    *,
    status_reloader: Callable[[], PayloadView] | None,
) -> tuple[Payload, Payload | None]:
    if status_reloader is None:
        return deepcopy(_mapping(status_payload)), None
    try:
        reloaded = status_reloader()
        if not isinstance(reloaded, dict):
            raise TypeError("status reloader must return a dictionary")
    except Exception as exc:  # noqa: BLE001
        return deepcopy(_mapping(status_payload)), {
            "schema_version": "monitor_poll_status_reload_warning_v0",
            "reason": (
                "monitor todo writeback persisted, but a fresh status "
                "projection could not be collected"
            ),
            "error_type": type(exc).__name__,
            "persisted_writeback": True,
            "after_projection_fresh": False,
            "recommended_action": (
                "rerun quota should-run to select the persisted successor"
            ),
        }
    return _mapping(reloaded), None


def _monitor_poll_failure(
    *,
    goal_id: str,
    execute: bool,
    source: str,
    agent_id: str | None,
    todo_id: str | None,
    target_key: str | None,
    result_hash: str | None,
    material_change: bool,
    material_change_scope: str | None,
    reason: str,
    before: PayloadView,
    turn_instance_id: str | None = None,
) -> Payload:
    payload: Payload = {
        "ok": False,
        "mode": "monitor-poll",
        "dry_run": not execute,
        "goal_id": goal_id,
        "appended": False,
        "registry_mutated": False,
        "source": _clean_text(source) or DEFAULT_SLOT_SPEND_SOURCE,
        "agent_id": normalize_todo_claimed_by(agent_id),
        "todo_id": todo_id,
        "target_key": target_key,
        "result_hash": result_hash,
        "material_change": material_change,
        "material_change_scope": material_change_scope,
        "reason": reason,
        "decision_summary": {
            "before": compact_quota_decision(_mapping(before)),
            "after": None,
        },
        "before": before,
        "after": None,
    }
    if turn_instance_id:
        payload["turn_instance_id"] = turn_instance_id
        payload["replayed"] = False
    return payload


def _capability_declaration_retry(before: PayloadView) -> Payload | None:
    gate = _mapping(before.get("capability_gate"))
    missing = [
        str(item).strip()
        for item in _list_items(gate.get("missing")) if str(item).strip()
    ]
    if not missing:
        return None
    return {
        "schema_version": "monitor_poll_capability_retry_v0",
        "command": "quota monitor-poll",
        "missing": missing,
        "cli_args": [
            arg
            for capability in missing
            for arg in ("--available-capability", capability)
        ],
        "reason": (
            "monitor-poll recomputes should-run; if these capabilities are "
            "present in the current agent environment, repeat the runtime "
            "capability declarations"
        ),
    }


def _monitor_advancement_authoring_fields(
    *,
    material_change: bool,
    monitor_todo_id: object,
    material_change_scope: object = None,
    successor_todo_ids: list[str],
) -> Payload:
    """Attach the optional cross-command recipe outside the hot-path reducer."""

    if not material_change:
        return {}
    return {
        "authoring_contract": build_monitor_advancement_authoring_contract(
            monitor_todo_id=str(monitor_todo_id or "") or None,
            material_change_scope=str(material_change_scope or "") or None,
            successor_todo_ids=successor_todo_ids,
        )
    }


def _provider_writeback(
    plan: PayloadView,
    *,
    registry_path: Path,
    runtime_root: Path,
    available_capabilities: list[str] | None = None,
) -> Payload:
    lease_proof = _mapping(plan.get("lease_proof"))
    observed_repository = plan.get("observed_task_repository")
    result = write_monitor_poll_todo_state(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=str(plan.get("goal_id") or ""),
        generated_at=str(plan.get("generated_at") or ""),
        execute=plan.get("execute") is True,
        monitor_effect_id=str(plan.get("monitor_effect_id") or "") or None,
        todo_id=_opt_str(plan.get("todo_id")),
        target_key=_opt_str(plan.get("target_key")),
        observed_task_repository=observed_repository if isinstance(observed_repository, str) else None,
        result_hash=_opt_str(plan.get("result_hash")),
        material_change=plan.get("material_change") is True,
        material_change_scope=_opt_str(plan.get("material_change_scope")),
        cadence=_opt_str(plan.get("cadence")),
        next_due_at=_opt_str(plan.get("next_due_at")),
        reason_summary=_opt_str(plan.get("reason_summary")),
        next_agent_todo=_opt_str(plan.get("next_agent_todo")),
        next_action_kind=_opt_str(plan.get("next_action_kind")),
        next_task_repository=_opt_str(plan.get("next_task_repository")),
        next_required_capabilities=_str_list(plan.get("next_required_capabilities")),
        next_continuation_policy=_opt_str(plan.get("next_continuation_policy")),
        next_target_key=_opt_str(plan.get("next_target_key")),
        next_user_todo=_opt_str(plan.get("next_user_todo")),
        next_user_task_class=_opt_str(plan.get("next_user_task_class")),
        next_claimed_by=_opt_str(plan.get("next_claimed_by")),
        next_agent_priority=_opt_str(plan.get("next_agent_priority")),
        next_user_priority=_opt_str(plan.get("next_user_priority")),
        agent_id=_opt_str(plan.get("agent_id")),
        task_lease_idempotency_key=_opt_str(lease_proof.get("idempotency_key")),
        task_lease_expected_version=_opt_int(lease_proof.get("expected_version")),
        gate_scope_guard=plan.get("gate_scope_guard") is True,
        available_capabilities=available_capabilities,
        admitted_resume_when=_opt_str(plan.get("admitted_resume_when")),
        legacy_batch_version=_opt_int(plan.get("legacy_batch_version")),
    )
    if not isinstance(result, dict):
        raise TypeError("monitor Todo provider returned no writeback receipt")
    return _mapping(result)


def record_quota_monitor_poll_for_decision(
    before: PayloadView,
    status_payload: PayloadView,
    *,
    goal_id: str,
    after_decision: Callable[[Payload], PayloadView],
    render_markdown: Callable[[Payload], str] | None = None,  # Retained for existing callers.
    registry_path: Path | None = None,
    execute: bool = False,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    reason_summary: str | None = None,
    agent_id: str | None = None,
    settlement_todo_id: str | None = None,
    todo_id: str | None = None,
    target_key: str | None = None,
    observed_task_repository: str | None = None,
    result_hash: str | None = None,
    material_change: bool = False,
    material_change_scope: str | None = None,
    cadence: str | None = None,
    next_due_at: str | None = None,
    next_agent_todo: str | None = None,
    next_action_kind: str | None = None,
    next_task_repository: str | None = None,
    next_required_capabilities: Sequence[str] | None = None,
    next_continuation_policy: str | None = None,
    next_target_key: str | None = None,
    next_user_todo: str | None = None,
    next_user_task_class: str | None = None,
    next_claimed_by: str | None = None,
    next_agent_priority: str | None = None,
    next_user_priority: str | None = None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
    use_current_task_lease: bool = False,
    auxiliary_settlement_todo: PayloadView | None = None,
    turn_instance_id: str | None = None,
    _index_lock_held: bool = False,
    status_reloader: Callable[[], PayloadView] | None = None,
    goal_ref: PayloadView | None = None,
    recovery_effect_id: str | None = None,
    todo_id_explicit: bool = True,
) -> Payload:
    safe_todo_id, safe_target_key, safe_result_hash, normalized_turn_id = _normalized_poll_targets(
        todo_id=todo_id, target_key=target_key, result_hash=result_hash,
        turn_instance_id=turn_instance_id,
    )
    raw_runtime_root = status_payload.get("runtime_root")
    if not raw_runtime_root:
        raise ValueError("status payload does not include runtime_root")
    runtime_root = Path(str(raw_runtime_root)).expanduser()
    index_path = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    decision_agent_id = quota_decision_agent_id(_mapping(before))
    effect_id = _monitor_poll_effect_id(
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=decision_agent_id,
        turn_instance_id=normalized_turn_id,
        todo_id=safe_todo_id,
        target_key=safe_target_key,
        goal_ref=goal_ref,
    )
    if use_current_task_lease:
        from .monitor_poll_lease_transport import current_monitor_lease_proof

        if not safe_todo_id or not normalized_turn_id or not decision_agent_id:
            raise ValueError("current task lease transport requires exact Turn, Todo, and agent identity")
        task_lease_idempotency_key, task_lease_expected_version = current_monitor_lease_proof(
            runtime_root=runtime_root,
            goal_id=goal_id,
            todo_id=safe_todo_id,
            agent_id=decision_agent_id,
            effect_id=effect_id,
        )
    if execute and (safe_todo_id or safe_target_key) and not use_current_task_lease:
        from ..coordination.local_authority import (
            CanonicalAuthorityRequired,
            read_canonical_todos_if_promoted,
        )

        # Selected-provider failure always refuses. Only an existing transaction
        # may reach the typed recovery owner before fresh canonical admission;
        # file existence routes the request, never validates its receipt.
        try:
            read_canonical_todos_if_promoted(
                runtime_root=runtime_root,
                goal_id=goal_id,
            )
        except CanonicalAuthorityRequired:
            if not _monitor_poll_receipt_path(runtime_root, goal_id, effect_id).exists():
                raise
            recovery_effect_id = effect_id
    decision = _decision_packet(
        before,
        goal_id=goal_id,
        todo_id=safe_todo_id,
        target_key=safe_target_key,
        registry_path=registry_path,
        runtime_root=runtime_root,
        todo_id_explicit=todo_id_explicit,
    )
    if auxiliary_settlement_todo is not None:
        decision["auxiliary_settlement_todo"] = {
            key: auxiliary_settlement_todo.get(key)
            for key in ("todo_id", "task_class", "status", "claimed_by", "excluded_agents")
        }
    observation = _observation_packet(
        before=before,
        agent_id=agent_id,
        settlement_todo_id=settlement_todo_id,
        reason_summary=reason_summary,
        todo_id=safe_todo_id,
        target_key=safe_target_key,
        result_hash=safe_result_hash,
        material_change=material_change,
        material_change_scope=material_change_scope,
        observed_task_repository=observed_task_repository,
        cadence=cadence,
        next_due_at=next_due_at,
        next_agent_todo=next_agent_todo,
        next_action_kind=next_action_kind,
        next_task_repository=next_task_repository,
        next_required_capabilities=next_required_capabilities,
        next_continuation_policy=next_continuation_policy,
        next_target_key=next_target_key,
        next_user_todo=next_user_todo,
        next_user_task_class=next_user_task_class,
        next_claimed_by=next_claimed_by,
        next_agent_priority=next_agent_priority,
        next_user_priority=next_user_priority,
        task_lease_idempotency_key=task_lease_idempotency_key,
        task_lease_expected_version=task_lease_expected_version,
    )
    generated_at = now_local_iso()

    def failure(reason: str, *, include_capability_retry: bool = False) -> Payload:
        payload = _monitor_poll_failure(
            goal_id=goal_id, execute=execute, source=source, agent_id=agent_id,
            todo_id=safe_todo_id, target_key=safe_target_key,
            result_hash=safe_result_hash, material_change=material_change,
            material_change_scope=material_change_scope, reason=reason,
            before=before, turn_instance_id=normalized_turn_id,
        )
        if include_capability_retry:
            retry = _capability_declaration_retry(before)
            if retry:
                payload["capability_retry"] = retry
                missing = _str_list(retry.get("missing"))
                payload["reason"] = f"{reason}; {retry['reason']}: {', '.join(missing)}"
        return payload

    provider_aborted_no_effect = False

    def transact(source_admission: PayloadView | None) -> tuple[Payload, Payload]:
        nonlocal provider_aborted_no_effect
        # Recheck the transport precondition inside the same quota admission as
        # the native receipt read. A legal no-effect abort may have removed it
        # since target selection; that must never turn recovery into a new poll.
        if recovery_effect_id is not None and (
            effect_id != recovery_effect_id or
            not _monitor_poll_receipt_path(runtime_root, goal_id, effect_id).exists()
        ):
            raise ValueError("original Monitor poll transaction is unavailable; reconcile its original operation before retrying")
        expected_digest = _index_digest(index_path)

        def request_for(
            phase: str,
            *,
            provider_receipt: PayloadView | None = None,
            status_reload_warning: PayloadView | None = None,
        ) -> Payload:
            return _request(
                phase=phase, effect_id=effect_id, runtime_root=runtime_root,
                goal_id=goal_id, source=source, generated_at=generated_at,
                execute=execute, expected_index_digest=expected_digest,
                turn_instance_id=normalized_turn_id, decision=decision,
                observation=observation, provider_receipt=provider_receipt,
                status_reload_warning=status_reload_warning,
                goal_ref=goal_ref, source_admission=source_admission,
            )

        after_status = deepcopy(_mapping(status_payload))
        if not (safe_todo_id or safe_target_key):
            return _native_result(request_for("commit")), after_status

        # Validate explicit claim routing before quota reserves a pending effect.
        # Exact retries keep their frozen admission if registry membership changed.
        receipt_path = _monitor_poll_receipt_path(runtime_root, goal_id, effect_id)
        if next_claimed_by and registry_path is not None and not receipt_path.exists():
            plan_todo_authoring_scope(
                command="create", role="agent", goal_id=goal_id,
                registered_agents=registered_agent_ids_from_registry(registry_path, goal_id),
                intent={"task_class": "advancement_task", "status": "open",
                        "actor_agent_id": decision_agent_id, "claimed_by": next_claimed_by},
            )

        native = _native_result(request_for("preflight"))
        if native.get("status") != "provider_required":
            return native, after_status
        plan = native.get("provider_plan")
        if not isinstance(plan, Mapping):
            raise TypeError("TypeScript monitor-poll preflight omitted provider plan")
        if registry_path is None:
            raise ValueError("monitor todo writeback requires registry_path")
        from ..coordination.local_authority import LocalCoordinationAuthorityUnavailable

        try:
            provider_receipt = _provider_writeback(
                plan,
                registry_path=registry_path,
                runtime_root=runtime_root,
                available_capabilities=_runtime_capabilities(before),
            )
        except LocalCoordinationAuthorityUnavailable as exc:
            # Transport the owner's typed negative evidence. TypeScript alone
            # decides whether it releases the exact pending reservation. An
            # outage/ambiguous commit carries no no-effect proof and is retained.
            if execute and exc.payload.get("no_effect") is not None:
                aborted = _native_result(request_for("provider_rejected", provider_receipt=exc.payload))
                provider_aborted_no_effect = aborted.get("status") == "aborted"
            raise
        status_warning: Payload | None = None
        if execute:
            after_status, status_warning = _reload_status_after_monitor_writeback(
                status_payload,
                status_reloader=status_reloader,
            )
        native = _native_result(
            request_for("commit", provider_receipt=provider_receipt, status_reload_warning=status_warning)
        )
        return native, after_status

    try:
        if _index_lock_held:
            if goal_ref is not None:
                raise ValueError(
                    "exact GoalRef monitor poll requires quota source admission"
                )
            native, after_status = transact(None)
        else:
            with quota_accounting_admission(
                runtime_root=runtime_root,
                registry_path=registry_path,
                goal_id=goal_id,
                goal_ref=goal_ref,
                operation="quota_monitor_poll_index",
                lock_legacy_index=execute,
            ) as source_admission:
                native, after_status = transact(source_admission)
    except ValueError as exc:
        from ..coordination.local_authority import (
            CanonicalAuthorityRequired,
            LocalCoordinationAuthorityRejection,
        )

        if isinstance(exc, CanonicalAuthorityRequired):
            # Moving fresh admission under the quota lock must preserve the
            # public refusal type and its structured migration/no-write facts.
            raise
        payload = failure(
            str(exc),
            include_capability_retry=(
                isinstance(exc, _NativeMonitorPollRejected)
                and exc.diagnostic_code == "monitor_poll_admission_rejected"
            ),
        )
        if isinstance(exc, _NativeMonitorPollRejected):
            payload["error_code"] = exc.diagnostic_code
        elif isinstance(exc, LocalCoordinationAuthorityRejection):
            payload["error_code"] = exc.code
            if provider_aborted_no_effect:
                payload["provider_aborted_no_effect"] = True
        return payload

    if native.get("status") == "conflict":
        payload = failure(
            str(native.get("reason") or "quota monitor-poll transaction conflict")
        )
        if normalized_turn_id and native.get("reason_code") == "effect_id_conflict":
            payload["error_code"] = "heartbeat_receipt_identity_conflict"
            conflicts = native.get("conflict_fields")
            payload["conflict_fields"] = (
                list(conflicts) if isinstance(conflicts, list) else []
            )
        return payload

    committed_payload = native.get("payload")
    if not isinstance(committed_payload, Mapping) or committed_payload.get("ok") is not True:
        raise RuntimeError("TypeScript quota monitor-poll commit omitted its payload")
    result = _mapping(committed_payload)
    index_record = native.get("index_record")
    if execute and isinstance(index_record, dict):
        after_status = _status_with_monitor_poll(
            after_status,
            goal_id=goal_id,
            index_record=_mapping(index_record),
        )
    after = after_decision(after_status)
    result["decision_summary"] = {
        "before": compact_quota_decision(_mapping(before)),
        "after": compact_quota_decision(_mapping(after)),
    }
    successor_todo_ids = result.get("successor_todo_ids")
    result.update(
        _monitor_advancement_authoring_fields(
            material_change=result.get("material_change") is True,
            monitor_todo_id=result.get("todo_id"),
            material_change_scope=result.get("material_change_scope"),
            successor_todo_ids=(
                [str(item) for item in successor_todo_ids]
                if isinstance(successor_todo_ids, list)
                else []
            ),
        )
    )
    result["before"] = before
    result["after"] = after
    return result
