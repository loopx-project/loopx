from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from ...file_lock import LockAcquisitionPolicy, exclusive_file_lock
from ...turn_identity import normalize_turn_instance_id
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..runtime.time import now_local_iso
from ..scheduler.monitor_poll_writeback import (
    resolve_monitor_todo_item,
    write_monitor_poll_todo_state,
)
from ..scheduler.monitor_todo import monitor_todo_is_due
from ..todos.contract import (
    TODO_TASK_CLASS_MONITOR,
    normalize_todo_claimed_by,
    normalize_todo_id,
)
from ..todos.external_wait_contract import (
    build_monitor_advancement_authoring_contract,
)
from ..todos.todo_semantics import todo_item_task_class
from .decision_summary import compact_quota_decision, quota_decision_agent_id
from .spend_sources import DEFAULT_SLOT_SPEND_SOURCE

QUOTA_MONITOR_POLL_CLASSIFICATION = "quota_monitor_poll"
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


def _now_local() -> str:
    return now_local_iso()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _monitor_candidate(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if todo_item_task_class(value) != TODO_TASK_CLASS_MONITOR:
        return None
    todo_id = normalize_todo_id(value.get("todo_id"))
    target_key = str(value.get("target_key") or "").strip() or None
    if not todo_id and not target_key:
        return None
    return {
        "todo_id": todo_id,
        "target_key": target_key,
        "task_class": TODO_TASK_CLASS_MONITOR,
    }


def _due_monitor_candidates(before: dict[str, Any]) -> list[dict[str, Any]]:
    contract = _mapping(before.get("work_lane_contract"))
    if contract.get("must_attempt_work") is not True:
        return []
    if contract.get("obligation") == "attempt_due_monitor":
        selected = _monitor_candidate(before.get("agent_lane_next_action"))
        if selected:
            return [selected]
        selected_todo_id = normalize_todo_id(contract.get("selected_todo_id"))
        due_items = contract.get("monitor_due_items")
        for value in due_items if isinstance(due_items, list) else []:
            candidate = _monitor_candidate(value)
            if not candidate:
                continue
            if selected_todo_id and candidate["todo_id"] != selected_todo_id:
                continue
            return [candidate]
        return []
    reason_codes = contract.get("reason_codes")
    if not isinstance(reason_codes, list) or "due_monitor_context" not in reason_codes:
        return []
    summary = _mapping(before.get("agent_todo_summary"))
    candidates = []
    due_items = summary.get("monitor_due_items")
    for value in due_items if isinstance(due_items, list) else []:
        candidate = _monitor_candidate(value)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _vision_wait_state(before: dict[str, Any]) -> dict[str, Any]:
    if before.get("schema_version") == "goal_vision_wait_state_v0":
        return dict(before)
    direct = _mapping(before.get("vision_wait_state"))
    if direct:
        return direct
    projection = _mapping(before.get("goal_frontier_projection"))
    return _mapping(projection.get("vision_wait_state"))


def resolve_due_monitor_candidate(
    *,
    registry_path: Path | None,
    runtime_root: Path | None,
    goal_id: str,
    todo_id: str | None,
    target_key: str | None,
) -> dict[str, Any]:
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
    if not monitor_todo_is_due(item):
        return {}
    return {
        "due": True,
        "todo_id": normalize_todo_id(item.get("todo_id")),
        "target_key": str(item.get("target_key") or "").strip() or None,
        "task_class": todo_item_task_class(item),
        "claimed_by": normalize_todo_claimed_by(item.get("claimed_by")),
    }


def _decision_packet(
    before: dict[str, Any],
    *,
    goal_id: str,
    todo_id: str | None,
    target_key: str | None,
    registry_path: Path | None = None,
    runtime_root: Path | None = None,
    authorized_due_monitor_poll: bool | None = None,
) -> dict[str, Any]:
    lane = _mapping(before.get("work_lane_contract"))
    due_candidates = _due_monitor_candidates(before)
    registry_due = resolve_due_monitor_candidate(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=todo_id,
        target_key=target_key,
    )
    if authorized_due_monitor_poll is False:
        due_candidates = []
        registry_due = {}
    elif authorized_due_monitor_poll is True:
        lane.update(must_attempt_work=True, obligation="attempt_due_monitor")
        requested = {
            "todo_id": todo_id,
            "target_key": target_key,
            "task_class": TODO_TASK_CLASS_MONITOR,
        }
        if requested not in due_candidates:
            due_candidates.append(requested)
    return {
        **compact_quota_decision(before),
        "goal_id": goal_id,
        "agent_id": quota_decision_agent_id(before),
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
    }


def _observation_packet(
    *,
    before: dict[str, Any],
    agent_id: str | None,
    settlement_todo_id: str | None,
    reason_summary: str | None,
    todo_id: str | None,
    target_key: str | None,
    result_hash: str | None,
    material_change: bool,
    cadence: str | None,
    next_due_at: str | None,
    next_agent_todo: str | None,
    next_action_kind: str | None,
    next_task_repository: str | None,
    next_required_capabilities: list[str] | None,
    next_continuation_policy: str | None,
    next_target_key: str | None,
    next_user_todo: str | None,
    next_user_task_class: str | None,
    next_claimed_by: str | None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
) -> dict[str, Any]:
    proof = ({"idempotency_key": task_lease_idempotency_key,
              "expected_version": task_lease_expected_version}
             if task_lease_idempotency_key is not None or task_lease_expected_version is not None else None)
    return {
        **({"lease_proof": proof} if proof is not None else {}),
        "actor_agent_id": normalize_todo_claimed_by(agent_id)
        or quota_decision_agent_id(before),
        "settlement_todo_id": settlement_todo_id,
        "reason_summary": reason_summary,
        "todo_id": todo_id,
        "target_key": target_key,
        "result_hash": result_hash,
        "material_change": material_change,
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
    }


def _index_digest(index_path: Path) -> str | None:
    try:
        content = index_path.read_bytes()
    except FileNotFoundError:
        return None
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _native_result(request: Mapping[str, Any]) -> dict[str, Any]:
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
    return dict(result)


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
    decision: dict[str, Any],
    observation: dict[str, Any],
    provider_receipt: Mapping[str, Any] | None = None,
    status_reload_warning: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
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
        "provider_receipt": (
            dict(provider_receipt) if provider_receipt is not None else None
        ),
        "status_reload_warning": (
            dict(status_reload_warning)
            if status_reload_warning is not None
            else None
        ),
    }


def build_quota_monitor_poll_event(
    before: dict[str, Any],
    *,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    generated_at: str | None = None,
    reason_summary: str | None = None,
    todo_id: str | None = None,
    target_key: str | None = None,
    result_hash: str | None = None,
    material_change: bool = False,
    authorized_due_monitor_poll: bool | None = None,
    turn_instance_id: str | None = None,
) -> dict[str, Any]:
    goal_id = str(before.get("goal_id") or "").strip()
    safe_todo_id = normalize_todo_id(todo_id) if todo_id else None
    safe_target_key = str(target_key or "").strip() or None
    safe_result_hash = str(result_hash or "").strip() or None
    normalized_turn_id = normalize_turn_instance_id(turn_instance_id)
    decision = _decision_packet(
        before,
        goal_id=goal_id,
        todo_id=safe_todo_id,
        target_key=safe_target_key,
        authorized_due_monitor_poll=authorized_due_monitor_poll,
    )
    observation = _observation_packet(
        before=before,
        agent_id=None,
        settlement_todo_id=None,
        reason_summary=reason_summary,
        todo_id=safe_todo_id,
        target_key=safe_target_key,
        result_hash=safe_result_hash,
        material_change=material_change,
        cadence=None,
        next_due_at=None,
        next_agent_todo=None,
        next_action_kind=None,
        next_task_repository=None,
        next_required_capabilities=None,
        next_continuation_policy=None,
        next_target_key=None,
        next_user_todo=None,
        next_user_task_class=None,
        next_claimed_by=None,
    )
    native = _native_result(
        _request(
            phase="event",
            effect_id=f"quota-monitor-poll:event:{uuid.uuid4().hex}",
            runtime_root=None,
            goal_id=goal_id,
            source=source,
            generated_at=generated_at or _now_local(),
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
    return dict(record)


def _find_monitor_poll_turn(
    index_path: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    todo_id: str | None = None,
    target_key: str | None = None,
) -> dict[str, Any] | None:
    normalized_todo_id = normalize_todo_id(todo_id) if todo_id else None
    normalized_target_key = str(target_key or "").strip() or None
    try:
        # LF framing keeps one record one record when a value carries U+0085.
        lines = index_path.read_text(encoding="utf-8").split("\n")
    except OSError:
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if (
            row.get("classification") == QUOTA_MONITOR_POLL_CLASSIFICATION
            and str(row.get("goal_id") or "") == goal_id
            and str(row.get("agent_id") or "") == agent_id
            and str(row.get("turn_instance_id") or "") == turn_instance_id
            and (
                normalized_todo_id is None
                or normalize_todo_id(row.get("todo_id")) == normalized_todo_id
            )
            and (
                normalized_target_key is None
                or str(row.get("target_key") or "").strip()
                == normalized_target_key
            )
        ):
            return row
    return None


def find_quota_monitor_poll_turn(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    todo_id: str | None = None,
    target_key: str | None = None,
) -> dict[str, Any] | None:
    """Return the latest matching monitor observation for one heartbeat turn."""

    normalized_turn_id = normalize_turn_instance_id(turn_instance_id)
    if not normalized_turn_id:
        return None
    return _find_monitor_poll_turn(
        runtime_root / "goals" / goal_id / "runs" / "index.jsonl",
        goal_id=goal_id,
        agent_id=agent_id,
        turn_instance_id=normalized_turn_id,
        todo_id=todo_id,
        target_key=target_key,
    )


def _persisted_monitor_effect_id(record: Mapping[str, Any] | None) -> str | None:
    if not isinstance(record, Mapping):
        return None
    metadata = record.get("quota_monitor_poll_commit")
    if not isinstance(metadata, Mapping):
        return None
    return str(metadata.get("effect_id") or "").strip() or None


def _monitor_poll_effect_id(
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str | None,
    todo_id: str | None,
    target_key: str | None,
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


def _status_with_monitor_poll(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    index_record: dict[str, Any],
) -> dict[str, Any]:
    after_status = deepcopy(status_payload)
    run_history = _mapping(after_status.get("run_history"))
    goals = run_history.get("goals")
    for goal in goals if isinstance(goals, list) else []:
        if not isinstance(goal, dict) or str(goal.get("id") or "") != goal_id:
            continue
        latest = goal.get("latest_runs")
        latest = latest if isinstance(latest, list) else []
        if index_record not in latest:
            goal["latest_runs"] = [index_record, *latest]
        runs = goal.get("runs")
        runs = runs if isinstance(runs, list) else []
        if index_record not in runs:
            goal["runs"] = [index_record, *runs]
    recent = run_history.get("recent_runs")
    recent = recent if isinstance(recent, list) else []
    if index_record not in recent:
        run_history["recent_runs"] = [index_record, *recent]
    after_status["run_history"] = run_history
    return after_status


def _reload_status_after_monitor_writeback(
    status_payload: dict[str, Any],
    *,
    status_reloader: Callable[[], dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if status_reloader is None:
        return deepcopy(status_payload), None
    try:
        reloaded = status_reloader()
        if not isinstance(reloaded, dict):
            raise TypeError("status reloader must return a dictionary")
    except Exception as exc:  # noqa: BLE001
        return deepcopy(status_payload), {
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
    return reloaded, None


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
    reason: str,
    before: dict[str, Any],
    turn_instance_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "ok": False,
        "mode": "monitor-poll",
        "dry_run": not execute,
        "goal_id": goal_id,
        "appended": False,
        "registry_mutated": False,
        "source": str(source or DEFAULT_SLOT_SPEND_SOURCE).strip()
        or DEFAULT_SLOT_SPEND_SOURCE,
        "agent_id": normalize_todo_claimed_by(agent_id),
        "todo_id": todo_id,
        "target_key": target_key,
        "result_hash": result_hash,
        "material_change": material_change,
        "reason": reason,
        "decision_summary": {
            "before": compact_quota_decision(before),
            "after": None,
        },
        "before": before,
        "after": None,
    }
    if turn_instance_id:
        payload["turn_instance_id"] = turn_instance_id
        payload["replayed"] = False
    return payload


def _capability_declaration_retry(before: dict[str, Any]) -> dict[str, Any] | None:
    gate = _mapping(before.get("capability_gate"))
    raw_missing = gate.get("missing")
    missing = [
        str(item).strip()
        for item in raw_missing if str(item).strip()
    ] if isinstance(raw_missing, list) else []
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
    monitor_todo_id: Any,
    successor_todo_ids: list[str],
) -> dict[str, Any]:
    """Attach the optional cross-command recipe outside the hot-path reducer."""

    if not material_change:
        return {}
    return {
        "authoring_contract": build_monitor_advancement_authoring_contract(
            monitor_todo_id=str(monitor_todo_id or "") or None,
            successor_todo_ids=successor_todo_ids,
        )
    }


def _provider_writeback(
    plan: Mapping[str, Any],
    *,
    registry_path: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    result = write_monitor_poll_todo_state(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=str(plan.get("goal_id") or ""),
        generated_at=str(plan.get("generated_at") or ""),
        execute=plan.get("execute") is True,
        monitor_effect_id=str(plan.get("monitor_effect_id") or "") or None,
        todo_id=plan.get("todo_id"),
        target_key=plan.get("target_key"),
        result_hash=plan.get("result_hash"),
        material_change=plan.get("material_change") is True,
        cadence=plan.get("cadence"),
        next_due_at=plan.get("next_due_at"),
        reason_summary=plan.get("reason_summary"),
        next_agent_todo=plan.get("next_agent_todo"),
        next_action_kind=plan.get("next_action_kind"),
        next_task_repository=plan.get("next_task_repository"),
        next_required_capabilities=list(
            plan.get("next_required_capabilities") or []
        ),
        next_continuation_policy=plan.get("next_continuation_policy"),
        next_target_key=plan.get("next_target_key"),
        next_user_todo=plan.get("next_user_todo"),
        next_user_task_class=plan.get("next_user_task_class"),
        next_claimed_by=plan.get("next_claimed_by"),
        agent_id=plan.get("agent_id"),
        task_lease_idempotency_key=(plan.get("lease_proof") or {}).get("idempotency_key"),
        task_lease_expected_version=(plan.get("lease_proof") or {}).get("expected_version"),
    )
    if not isinstance(result, dict):
        raise TypeError("monitor Todo provider returned no writeback receipt")
    return result


def record_quota_monitor_poll_for_decision(
    before: dict[str, Any],
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    after_decision: Callable[[dict[str, Any]], dict[str, Any]],
    render_markdown: Callable[[dict[str, Any]], str],
    registry_path: Path | None = None,
    execute: bool = False,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    reason_summary: str | None = None,
    agent_id: str | None = None,
    settlement_todo_id: str | None = None,
    todo_id: str | None = None,
    target_key: str | None = None,
    result_hash: str | None = None,
    material_change: bool = False,
    cadence: str | None = None,
    next_due_at: str | None = None,
    next_agent_todo: str | None = None,
    next_action_kind: str | None = None,
    next_task_repository: str | None = None,
    next_required_capabilities: list[str] | None = None,
    next_continuation_policy: str | None = None,
    next_target_key: str | None = None,
    next_user_todo: str | None = None,
    next_user_task_class: str | None = None,
    next_claimed_by: str | None = None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
    use_current_task_lease: bool = False,
    turn_instance_id: str | None = None,
    _index_lock_held: bool = False,
    status_reloader: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    del render_markdown
    normalized_turn_id = normalize_turn_instance_id(turn_instance_id)
    safe_todo_id = normalize_todo_id(todo_id) if todo_id else None
    safe_target_key = str(target_key or "").strip() or None
    safe_result_hash = str(result_hash or "").strip() or None
    raw_runtime_root = status_payload.get("runtime_root")
    if not raw_runtime_root:
        raise ValueError("status payload does not include runtime_root")
    runtime_root = Path(str(raw_runtime_root)).expanduser()
    index_path = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    decision_agent_id = quota_decision_agent_id(before)
    effect_id = _monitor_poll_effect_id(
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=decision_agent_id,
        turn_instance_id=normalized_turn_id,
        todo_id=safe_todo_id,
        target_key=safe_target_key,
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
        from ..scheduler.provider_monitor_poll import (
            require_monitor_poll_source_available,
        )

        require_monitor_poll_source_available(
            runtime_root=runtime_root,
            goal_id=goal_id,
        )
    decision = _decision_packet(
        before,
        goal_id=goal_id,
        todo_id=safe_todo_id,
        target_key=safe_target_key,
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    observation = _observation_packet(
        before=before,
        agent_id=agent_id,
        settlement_todo_id=settlement_todo_id,
        reason_summary=reason_summary,
        todo_id=safe_todo_id,
        target_key=safe_target_key,
        result_hash=safe_result_hash,
        material_change=material_change,
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
        task_lease_idempotency_key=task_lease_idempotency_key,
        task_lease_expected_version=task_lease_expected_version,
    )
    generated_at = _now_local()

    def failure(
        reason: str,
        *,
        include_capability_retry: bool = False,
    ) -> dict[str, Any]:
        payload = _monitor_poll_failure(
            goal_id=goal_id,
            execute=execute,
            source=source,
            agent_id=agent_id,
            todo_id=safe_todo_id,
            target_key=safe_target_key,
            result_hash=safe_result_hash,
            material_change=material_change,
            reason=reason,
            before=before,
            turn_instance_id=normalized_turn_id,
        )
        if include_capability_retry:
            retry = _capability_declaration_retry(before)
            if retry:
                payload["capability_retry"] = retry
                payload["reason"] = (
                    f"{reason}; {retry['reason']}: {', '.join(retry['missing'])}"
                )
        return payload

    def transact() -> tuple[dict[str, Any], dict[str, Any]]:
        common = {
            "effect_id": effect_id,
            "runtime_root": runtime_root,
            "goal_id": goal_id,
            "source": source,
            "generated_at": generated_at,
            "execute": execute,
            "expected_index_digest": _index_digest(index_path),
            "turn_instance_id": normalized_turn_id,
            "decision": decision,
            "observation": observation,
        }
        after_status = deepcopy(status_payload)
        provider_needed = bool(safe_todo_id or safe_target_key)
        if not provider_needed:
            return _native_result(_request(phase="commit", **common)), after_status

        native = _native_result(_request(phase="preflight", **common))
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
            )
        except LocalCoordinationAuthorityUnavailable as exc:
            # Transport the owner's typed negative evidence. TypeScript alone
            # decides whether it releases the exact pending reservation. An
            # outage/ambiguous commit carries no no-effect proof and is retained.
            if execute and exc.payload.get("no_effect") is not None:
                _native_result(_request(phase="provider_rejected", provider_receipt=exc.payload, **common))
            raise
        status_warning = None
        if execute:
            after_status, status_warning = _reload_status_after_monitor_writeback(
                status_payload,
                status_reloader=status_reloader,
            )
        native = _native_result(
            _request(
                phase="commit",
                provider_receipt=provider_receipt,
                status_reload_warning=status_warning,
                **common,
            )
        )
        return native, after_status

    try:
        if execute and not _index_lock_held:
            with exclusive_file_lock(
                index_path,
                policy=LockAcquisitionPolicy.MONITOR,
                agent_id=decision_agent_id or agent_id,
                operation="quota_monitor_poll_index",
            ):
                native, after_status = transact()
        else:
            native, after_status = transact()
    except ValueError as exc:
        payload = failure(
            str(exc),
            include_capability_retry=(
                isinstance(exc, _NativeMonitorPollRejected)
                and exc.diagnostic_code == "monitor_poll_admission_rejected"
            ),
        )
        if isinstance(exc, _NativeMonitorPollRejected):
            payload["error_code"] = exc.diagnostic_code
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

    payload = native.get("payload")
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise RuntimeError("TypeScript quota monitor-poll commit omitted its payload")
    result = dict(payload)
    index_record = native.get("index_record")
    if execute and isinstance(index_record, dict):
        after_status = _status_with_monitor_poll(
            after_status,
            goal_id=goal_id,
            index_record=index_record,
        )
    after = after_decision(after_status)
    result["decision_summary"] = {
        "before": compact_quota_decision(before),
        "after": compact_quota_decision(after),
    }
    successor_todo_ids = result.get("successor_todo_ids")
    result.update(
        _monitor_advancement_authoring_fields(
            material_change=result.get("material_change") is True,
            monitor_todo_id=result.get("todo_id"),
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
