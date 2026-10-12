from __future__ import annotations

from typing import Any
from copy import deepcopy

from .vision_checkpoint import (
    GOAL_VISION_BUDGET_ERROR as GOAL_VISION_BUDGET_ERROR,
    GoalVisionBudgetError as GoalVisionBudgetError,
    prepare_vision_refresh,
)


GOAL_VISION_REPLAN_SCHEMA_VERSION = "goal_vision_replan_contract_v0"
GOAL_PATH_DELTA_SCHEMA_VERSION = "goal_path_delta_v0"


GOAL_VISION_FIELD_LIMITS: dict[str, int] = {
    "vision_summary": 420,
    "role_scope": 280,
    "acceptance_summary": 420,
    "advancement_policy": 32,
    "replan_trigger_summary": 240,
    "dreaming_policy": 240,
    "last_patch_summary": 240,
}
GOAL_PATH_DELTA_OUTCOMES = frozenset(
    {"continue", "replan", "wait", "no_change", "ask_human", "stop"}
)
GOAL_PATH_DELTA_SCALAR_LIMITS: dict[str, int] = {
    "prior_assumption": 320,
    "observed_reality": 320,
    "reentry_condition": 180,
}
GOAL_PATH_DELTA_LIST_LIMITS: dict[str, tuple[int, int]] = {
    "retained": (3, 120),
    "changed": (3, 120),
    "stopped": (3, 120),
    "unresolved_questions": (2, 140),
    "evidence_refs": (4, 140),
}
GOAL_VISION_BUDGET_COMPACT_FIELDS = (
    "schema_version",
    "status",
    "field_usage",
    "total_limit",
    "total_usage",
)
# Mirrors the TS-owned prepare contract for bounded typed fallback
# declarations so compaction cannot drop a declared fallback direction.
GOAL_VISION_FALLBACK_DECLARATION_ENTRY_LIMIT = 4
GOAL_VISION_FALLBACK_DECLARATION_ID_LIMIT = 120
GOAL_VISION_FALLBACK_DECLARATION_FIELDS = ("target_todo_id", "successor_todo_id")


def _compact_public_text(value: Any, *, limit: int) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None
    return text[:limit]


def _compact_goal_path_delta(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    schema_version = (
        _compact_public_text(value.get("schema_version"), limit=80)
        or GOAL_PATH_DELTA_SCHEMA_VERSION
    )
    compact: dict[str, Any] = {"schema_version": schema_version}
    outcome = _compact_public_text(value.get("outcome"), limit=32)
    if outcome in GOAL_PATH_DELTA_OUTCOMES:
        compact["outcome"] = outcome
    for field, limit in GOAL_PATH_DELTA_SCALAR_LIMITS.items():
        text = _compact_public_text(value.get(field), limit=limit)
        if text:
            compact[field] = text
    for field, (max_items, item_limit) in GOAL_PATH_DELTA_LIST_LIMITS.items():
        raw_items = value.get(field)
        if not isinstance(raw_items, list):
            continue
        items = [
            text
            for item in raw_items[:max_items]
            if (text := _compact_public_text(item, limit=item_limit))
        ]
        if items:
            compact[field] = items
    return compact if len(compact) > 1 else None


def _compact_fallback_declarations(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    declarations: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in value[:GOAL_VISION_FALLBACK_DECLARATION_ENTRY_LIMIT]:
        if not isinstance(raw, dict):
            continue
        declaration_id = _compact_public_text(
            raw.get("declaration_id"),
            limit=GOAL_VISION_FALLBACK_DECLARATION_ID_LIMIT,
        )
        if not declaration_id or declaration_id in seen:
            continue
        seen.add(declaration_id)
        entry = {"declaration_id": declaration_id}
        for field in GOAL_VISION_FALLBACK_DECLARATION_FIELDS:
            text = _compact_public_text(
                raw.get(field),
                limit=GOAL_VISION_FALLBACK_DECLARATION_ID_LIMIT,
            )
            if text:
                entry[field] = text
        declarations.append(entry)
    return declarations


def compact_goal_vision_packet(value: Any) -> dict[str, Any] | None:
    """Return the public read-path shape of an agent goal-vision packet."""

    if not isinstance(value, dict):
        return None
    compact: dict[str, Any] = {}
    for field in ("schema_version", "goal_id", "agent_id", "state"):
        text = _compact_public_text(value.get(field), limit=120)
        if text:
            compact[field] = text

    patch = (
        value.get("vision_patch") if isinstance(value.get("vision_patch"), dict) else {}
    )
    compact_patch: dict[str, str] = {}
    for field, limit in GOAL_VISION_FIELD_LIMITS.items():
        text = _compact_public_text(patch.get(field), limit=limit)
        if text:
            compact_patch[field] = text
    if compact_patch:
        compact["vision_patch"] = compact_patch

    path_delta = _compact_goal_path_delta(value.get("path_delta"))
    if path_delta:
        compact["path_delta"] = path_delta

    todo_delta: list[str] = []
    raw_todo_delta = value.get("todo_delta")
    if isinstance(raw_todo_delta, list):
        for item in raw_todo_delta[:8]:
            text = _compact_public_text(item, limit=80)
            if text:
                todo_delta.append(text)
    if todo_delta:
        compact["todo_delta"] = todo_delta

    # Preserve the TS-normalized, bounded control proposal; no Python policy.
    if isinstance(value.get("stride_decision"), dict):
        compact["stride_decision"] = deepcopy(value["stride_decision"])

    declarations = _compact_fallback_declarations(value.get("fallback_declarations"))
    if declarations:
        compact["fallback_declarations"] = declarations

    budget = (
        value.get("vision_budget")
        if isinstance(value.get("vision_budget"), dict)
        else {}
    )
    compact_budget = {
        field: budget[field]
        for field in GOAL_VISION_BUDGET_COMPACT_FIELDS
        if field in budget
    }
    if compact_budget:
        compact["vision_budget"] = compact_budget

    validation = (
        value.get("validation") if isinstance(value.get("validation"), dict) else {}
    )
    compact_validation = {
        field: validation[field]
        for field in ("budget_checked", "budget_status", "write_correctness_checked")
        if field in validation
    }
    if compact_validation:
        compact["validation"] = compact_validation

    return compact or None


def normalize_goal_vision_packet(
    packet: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any]:
    return prepare_vision_refresh(
        packet,
        goal_id=goal_id,
        agent_id=agent_id,
        existing_agent_vision=None,
        merge_patch=False,
        require_path_delta_for_durable_change=False,
    )


def normalize_goal_vision_update(
    packet: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
    existing_agent_vision: dict[str, Any] | None,
    merge_patch: bool,
    require_path_delta_for_durable_change: bool,
) -> dict[str, Any]:
    """Compatibility entry point for the TS-owned Vision preflight."""

    return prepare_vision_refresh(
        packet,
        goal_id=goal_id,
        agent_id=agent_id,
        existing_agent_vision=existing_agent_vision,
        merge_patch=merge_patch,
        require_path_delta_for_durable_change=require_path_delta_for_durable_change,
    )
