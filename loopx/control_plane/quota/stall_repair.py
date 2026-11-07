from __future__ import annotations

from typing import Any, cast
from .effective_action import EffectiveAction


from .. import compact_control_plane_policy, control_plane_self_repair_allows
from ..agents.capability_gate import (
    CAPABILITY_OWNER_GATE_HINTS,
    missing_required_capabilities,
)
from ..todos.contract import (
    normalize_required_capabilities,
    normalize_todo_blocks_agent,
    normalize_todo_claimed_by,
    normalize_todo_decision_scope,
    normalize_todo_global_gate,
    normalize_todo_id,
    normalize_todo_required_decision_scopes,
)
from ..todos.decision_scope import (
    build_required_decision_scope_consistency,
    build_required_decision_scope_repair_hint,
    standing_decision_authority_for_agent,
)
from ..todos.user_gate import open_todo_count, open_user_gate_todo_items

STALL_HEALTH_ITEM_COMPACT_FIELDS = (
    "goal_id",
    "status",
    "waiting_on",
    "severity",
    "source",
    "recommended_action",
)
DECISION_SCOPE_REPAIR_TRIGGER = "required_decision_scope_projection_drift"
USER_GATE_SCOPE_REPAIR_TRIGGER = "user_gate_scope_projection_drift"
RUNTIME_CAPABILITY_USER_GATE_REPAIR_TRIGGER = "runtime_capability_user_gate_overreach"
TODO_PROJECTION_REPAIR_TRIGGERS = frozenset(
    {
        DECISION_SCOPE_REPAIR_TRIGGER,
        USER_GATE_SCOPE_REPAIR_TRIGGER,
        RUNTIME_CAPABILITY_USER_GATE_REPAIR_TRIGGER,
    }
)
RUNTIME_RECOVERY_ACTION_TOKENS = frozenset(
    {
        "configure",
        "execute",
        "install",
        "launch",
        "materialize",
        "rebuild",
        "repair",
        "restore",
        "retry",
        "run",
        "start",
    }
)


def standing_decision_authority_from_status_item(
    item: dict[str, Any],
    *,
    project_asset: dict[str, Any] | None,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Read and agent-scope a standing authority receipt from status."""

    authority = item.get("standing_decision_authority")
    if not isinstance(authority, dict):
        authority = (
            cast(dict[str, Any], project_asset.get("standing_decision_authority"))
            if project_asset
            and isinstance(project_asset.get("standing_decision_authority"), dict)
            else None
        )
    return standing_decision_authority_for_agent(authority, agent_id=agent_id)


def standing_decision_authority_payload_from_status_item(
    item: dict[str, Any],
    *,
    project_asset: dict[str, Any] | None,
    agent_id: str | None,
) -> dict[str, Any]:
    authority = standing_decision_authority_from_status_item(
        item,
        project_asset=project_asset,
        agent_id=agent_id,
    )
    return {"standing_decision_authority": authority} if authority else {}


def _compact_health_items(
    items: list[Any],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        payload = {
            field: item.get(field)
            for field in STALL_HEALTH_ITEM_COMPACT_FIELDS
            if item.get(field)
        }
        if payload:
            compact.append(payload)
        if len(compact) >= limit:
            break
    return compact


def _runtime_recovery_action(action_kind: Any) -> bool:
    tokens = {
        token
        for token in str(action_kind or "").strip().lower().replace("-", "_").split("_")
        if token
    }
    return bool(tokens & RUNTIME_RECOVERY_ACTION_TOKENS)


def _source_todo_items(
    summary: dict[str, Any] | None,
    source_items: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    candidates = list(source_items or [])
    if source_items is None and isinstance(summary, dict):
        for key in (
            "items",
            "first_open_items",
            "backlog_items",
            "executable_backlog_items",
        ):
            values = summary.get(key)
            if isinstance(values, list):
                candidates.extend(item for item in values if isinstance(item, dict))
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        identity = (
            str(item.get("todo_id") or "").strip(),
            str(item.get("text") or "").strip(),
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


def build_runtime_capability_user_gate_repair_hint(
    *,
    user_todo_summary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    user_todo_source_items: list[dict[str, Any]] | None,
    agent_todo_source_items: list[dict[str, Any]] | None,
    agent_id: str | None,
    available_capabilities: Any,
) -> dict[str, Any] | None:
    """Detect an execution request misclassified as an owner decision gate."""

    normalized_agent_id = normalize_todo_claimed_by(agent_id)
    if not normalized_agent_id:
        return None
    agent_items = _source_todo_items(
        agent_todo_summary,
        agent_todo_source_items,
    )
    agent_items_by_id = {
        todo_id: item
        for item in agent_items
        for todo_id in [normalize_todo_id(item.get("todo_id"))]
        if todo_id
    }
    user_summary = {
        "items": _source_todo_items(user_todo_summary, user_todo_source_items)
    }
    for gate in open_user_gate_todo_items(user_summary):
        if normalize_todo_global_gate(gate.get("global_gate")):
            continue
        blocks_agent = normalize_todo_blocks_agent(gate.get("blocks_agent"))
        if blocks_agent and blocks_agent != normalized_agent_id:
            continue
        if normalize_todo_decision_scope(gate.get("decision_scope")):
            continue
        if not _runtime_recovery_action(gate.get("action_kind")):
            continue
        target_todo_id = normalize_todo_id(gate.get("unblocks_todo_id"))
        target = agent_items_by_id.get(cast(str, target_todo_id))
        if not target:
            continue
        target_owner = normalize_todo_claimed_by(target.get("claimed_by"))
        if target_owner and target_owner != normalized_agent_id:
            continue
        if normalize_todo_required_decision_scopes(
            target.get("required_decision_scopes")
        ):
            continue
        required_capabilities = normalize_required_capabilities(
            target.get("required_capabilities")
        )
        if not required_capabilities:
            continue
        if set(required_capabilities) & CAPABILITY_OWNER_GATE_HINTS:
            continue
        if missing_required_capabilities(
            target,
            available_capabilities=available_capabilities,
        ):
            continue
        gate_todo_id = normalize_todo_id(gate.get("todo_id"))
        return {
            "source": "quota.should-run",
            "trigger": RUNTIME_CAPABILITY_USER_GATE_REPAIR_TRIGGER,
            "schema_version": "runtime_capability_user_gate_repair_v0",
            "recommended_mode": "repair_user_gate_projection",
            "effective_action": EffectiveAction.RUNTIME_USER_GATE_PROJECTION_REPAIR.value,
            "blocked_action_scope": "user_gate_projection",
            "allowed": True,
            "notify": "DONT_NOTIFY",
            "reason": (
                "an execution-shaped user_gate without decision scope links to "
                "this agent's capability-runnable todo"
            ),
            "repair_focus": (
                "attempt the runtime recovery; on success complete or reclassify "
                "the user_gate and resume the linked agent todo, otherwise write "
                "the concrete capability blocker"
            ),
            "spend_policy": (
                "append exactly one heartbeat spend only after the gate projection "
                "repair or concrete blocker writeback is validated"
            ),
            "gate_todo_id": gate_todo_id,
            "target_todo_id": target_todo_id,
            "required_capabilities": required_capabilities,
            "decision_scope_present": False,
        }
    return None


def build_quota_stall_self_repair_hint(
    item: dict[str, Any],
    *,
    state: str,
    plan_ok: bool,
    health_items: list[Any],
    user_todo_summary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    agent_id: str | None,
    user_todo_source_items: list[dict[str, Any]] | None = None,
    agent_todo_source_items: list[dict[str, Any]] | None = None,
    standing_decision_authority: dict[str, Any] | None = None,
    available_capabilities: Any = None,
) -> dict[str, Any] | None:
    coordination = (
        cast(dict[str, Any], item.get("coordination")) if isinstance(item.get("coordination"), dict) else {}
    )
    decision_scope_consistency = build_required_decision_scope_consistency(
        agent_todo_summary,
        user_todo_summary,
        agent_id=agent_id,
        registered_agent_ids=coordination.get("registered_agents"),
        agent_source_items=agent_todo_source_items,
        user_source_items=user_todo_source_items,
        standing_decision_authority=standing_decision_authority,
    )
    decision_scope_repair = build_required_decision_scope_repair_hint(
        decision_scope_consistency
    )
    if decision_scope_repair:
        return decision_scope_repair
    runtime_user_gate_repair = build_runtime_capability_user_gate_repair_hint(
        user_todo_summary=user_todo_summary,
        agent_todo_summary=agent_todo_summary,
        user_todo_source_items=user_todo_source_items,
        agent_todo_source_items=agent_todo_source_items,
        agent_id=agent_id,
        available_capabilities=available_capabilities,
    )
    if runtime_user_gate_repair:
        return runtime_user_gate_repair

    control_plane = compact_control_plane_policy(item.get("control_plane"))
    if not control_plane:
        return None

    if not plan_ok and control_plane_self_repair_allows(
        control_plane,
        "health_blocker_repair",
    ):
        blockers = _compact_health_items(health_items)
        if blockers:
            return {
                "source": "quota.should-run",
                "trigger": "health_blocker",
                "recommended_mode": "repair_control_plane_health",
                "effective_action": EffectiveAction.CONTROL_PLANE_HEALTH_REPAIR.value,
                "allowed": True,
                "notify": "DONT_NOTIFY",
                "reason": (
                    "status or contract health blocks normal delivery; spend one "
                    "bounded turn on control-plane repair instead of quiet spinning"
                ),
                "repair_focus": (
                    "inspect the compact health blocker, repair registry/status/"
                    "contract projection or public-boundary scan scope, validate, "
                    "write a durable event, then spend once"
                ),
                "spend_policy": (
                    "append exactly one heartbeat spend only after the health blocker "
                    "is repaired, validated, and written back"
                ),
                "control_plane": control_plane,
                "blocking_health_items": blockers,
            }

    waiting_on = str(item.get("waiting_on") or "")
    has_user_todos = open_todo_count(user_todo_summary) > 0
    has_agent_todos = open_todo_count(agent_todo_summary) > 0
    has_next_action = bool(str(item.get("recommended_action") or "").strip())
    has_project_asset = isinstance(item.get("project_asset"), dict)
    unknown_waiting_owner = waiting_on in {"", "none", "unknown", "null"}
    if (
        control_plane_self_repair_allows(control_plane, "waiting_projection_repair")
        and state == "waiting"
        and unknown_waiting_owner
        and not has_user_todos
        and (has_next_action or has_agent_todos or has_project_asset)
    ):
        return {
            "source": "quota.should-run",
            "trigger": "waiting_without_owner_projection",
            "recommended_mode": "repair_waiting_projection",
            "effective_action": EffectiveAction.CONTROL_PLANE_PROJECTION_REPAIR.value,
            "allowed": True,
            "notify": "DONT_NOTIFY",
            "reason": (
                "goal is waiting without a concrete owner/evidence gate while current "
                "action or agent backlog exists"
            ),
            "repair_focus": (
                "rebase from registry, active state, status, and run history; either "
                "project waiting_on=codex for safe agent work or write the concrete "
                "user/controller/evidence blocker"
            ),
            "spend_policy": (
                "append exactly one heartbeat spend only after the projection or "
                "blocker writeback is validated"
            ),
            "control_plane": control_plane,
        }

    return None


def apply_stall_repair_delivery_guard(
    repair: dict[str, Any] | None,
    *,
    normal_delivery_allowed: bool,
    recovery_allowed: bool,
    reason: str,
) -> tuple[bool, bool, str]:
    if not repair or repair.get("trigger") not in TODO_PROJECTION_REPAIR_TRIGGERS:
        return normal_delivery_allowed, recovery_allowed, reason
    return False, False, str(repair.get("reason") or reason)


def stall_repair_blocked_action_scope(repair: dict[str, Any] | None) -> str | None:
    if not repair or repair.get("trigger") not in TODO_PROJECTION_REPAIR_TRIGGERS:
        return None
    value = str(repair.get("blocked_action_scope") or "").strip()
    return value or None


def stall_repair_payload(repair: dict[str, Any] | None) -> dict[str, Any]:
    if not repair or repair.get("trigger") not in TODO_PROJECTION_REPAIR_TRIGGERS:
        return {}
    consistency = repair.get("consistency")
    if not isinstance(consistency, dict):
        return {}
    return {"todo_decision_scope_consistency": consistency}


def stall_repair_suppresses_user_gate_notification(
    repair: dict[str, Any] | None,
) -> bool:
    return bool(
        repair
        and repair.get("trigger")
        in {
            USER_GATE_SCOPE_REPAIR_TRIGGER,
            RUNTIME_CAPABILITY_USER_GATE_REPAIR_TRIGGER,
        }
    )
