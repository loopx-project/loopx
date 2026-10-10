"""Python facade for TypeScript-owned quota settlement workspace causality."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..coordination.coordination_state_contract_generated import (
    DELIVERY_WORKSPACE_CAUSALITY_REQUEST_SCHEMA,
    DELIVERY_WORKSPACE_CAUSALITY_RESULT_SCHEMA,
    DELIVERY_WORKSPACE_CAUSALITY_SCHEMA,
    DELIVERY_WORKSPACE_LEGACY_RECEIPT_EVIDENCE_SCHEMA,
    DELIVERY_WORKSPACE_RESOLUTION_SCHEMA,
    DELIVERY_WORKSPACE_SETTLEMENT_REQUIREMENT_SCHEMA,
)
from ..todos.contract import (
    normalize_required_write_scopes,
    normalize_todo_continuation_policy,
    normalize_todo_id,
    normalize_todo_task_repository,
)

DELIVERY_WORKSPACE_CAUSALITY_SCHEMA_VERSION = DELIVERY_WORKSPACE_CAUSALITY_SCHEMA
DELIVERY_WORKSPACE_RESOLUTION_SCHEMA_VERSION = DELIVERY_WORKSPACE_RESOLUTION_SCHEMA
SETTLEMENT_WORKSPACE_REQUIREMENT_SCHEMA_VERSION = (
    DELIVERY_WORKSPACE_SETTLEMENT_REQUIREMENT_SCHEMA
)
LEGACY_SETTLEMENT_RECEIPT_EVIDENCE_SCHEMA_VERSION = (
    DELIVERY_WORKSPACE_LEGACY_RECEIPT_EVIDENCE_SCHEMA
)
DELIVERY_WORKSPACE_REQUIREMENTS = frozenset({"required", "not_required", "unknown"})


def project_goal_write_scopes(
    project_root: str,
    allowed_scopes: list[str],
) -> dict[str, Any]:
    """Adapt observed local-root facts to the existing typed work owner."""
    result = _runtime_result(
        "goal_write_scopes",
        project_root=project_root,
        allowed_scopes=allowed_scopes,
    ).get("write_scope_projection")
    if (
        not isinstance(result, Mapping)
        or not isinstance(result.get("allowed_write_scopes"), list)
        or any(not isinstance(scope, str) for scope in result["allowed_write_scopes"])
    ):
        raise RuntimeError("TypeScript Goal write scope projection shape mismatch")
    return dict(result)


def _runtime_result(operation: str, **params: Any) -> Mapping[str, Any]:
    try:
        result = effect_runtime_result(
            "quota.delivery_workspace_causality.evaluate",
            {
                "schema_version": DELIVERY_WORKSPACE_CAUSALITY_REQUEST_SCHEMA,
                "operation": operation,
                **params,
            },
        )
    except EffectRuntimeRejected as exc:
        raise RuntimeError(str(exc)) from None
    if not isinstance(result, Mapping):
        raise RuntimeError(
            "TypeScript delivery workspace causality result must be an object"
        )
    if result.get("schema_version") != DELIVERY_WORKSPACE_CAUSALITY_RESULT_SCHEMA:
        raise RuntimeError(
            "TypeScript delivery workspace causality result shape mismatch"
        )
    return result


def _prepared_causality(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        "schema_version": value.get("schema_version"),
        "todo_id": normalize_todo_id(value.get("todo_id")),
        "requirement": str(value.get("requirement") or "").strip(),
        "source": str(value.get("source") or "").strip(),
        "reason": str(value.get("reason") or "").strip(),
    }


def _causality_result(result: Mapping[str, Any]) -> dict[str, str] | None:
    value = result.get("causality")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RuntimeError(
            "TypeScript delivery workspace causality result shape mismatch"
        )
    expected_keys = ("schema_version", "todo_id", "requirement", "source", "reason")
    if set(value) != set(expected_keys) or any(
        not isinstance(value.get(key), str) for key in expected_keys
    ):
        raise RuntimeError(
            "TypeScript delivery workspace causality result shape mismatch"
        )
    if (
        value["schema_version"] != DELIVERY_WORKSPACE_CAUSALITY_SCHEMA_VERSION
        or normalize_todo_id(value["todo_id"]) != value["todo_id"]
        or value["requirement"] not in DELIVERY_WORKSPACE_REQUIREMENTS
        or not value["source"]
        or not value["reason"]
    ):
        raise RuntimeError(
            "TypeScript delivery workspace causality result shape mismatch"
        )
    return {key: str(value[key]) for key in expected_keys}


def build_delivery_workspace_causality(
    todo: Mapping[str, Any] | None,
    *,
    source: str = "selected_todo_contract",
) -> dict[str, str] | None:
    """Classify whether one exact Todo makes a repository workspace causal."""

    if not isinstance(todo, Mapping):
        return None
    todo_id = normalize_todo_id(todo.get("todo_id"))
    if not todo_id:
        return None
    task_repository = normalize_todo_task_repository(todo.get("task_repository"))
    required_write_scopes = normalize_required_write_scopes(
        todo.get("required_write_scopes") or todo.get("required_write_scope")
    )
    capabilities = todo.get("required_capabilities")
    required_capabilities = sorted(
        {
            str(capability).strip()
            for capability in (
                capabilities if isinstance(capabilities, (list, tuple, set)) else []
            )
            if str(capability).strip()
        }
    )
    continuation_policy = normalize_todo_continuation_policy(
        todo.get("continuation_policy")
    )
    return _causality_result(
        _runtime_result(
            "classify",
            expected_todo_id=None,
            normalized_todo_contract={
                "todo_id": todo_id,
                "task_repository": task_repository,
                "required_write_scopes": required_write_scopes,
                "required_capabilities": required_capabilities,
                "continuation_policy": continuation_policy,
                "source": source,
            },
        )
    )


def normalize_delivery_workspace_causality(
    value: Any,
    *,
    todo_id: str | None = None,
) -> dict[str, str] | None:
    prepared = _prepared_causality(value if isinstance(value, Mapping) else None)
    if prepared is None:
        return None
    return _causality_result(
        _runtime_result(
            "normalize",
            expected_todo_id=normalize_todo_id(todo_id),
            causality=prepared,
        )
    )


def delivery_workspace_causality_event_fields(
    causality: Mapping[str, Any] | None,
) -> dict[str, str]:
    prepared = _prepared_causality(causality)
    if prepared is None:
        return {}
    result = _runtime_result(
        "event_fields",
        expected_todo_id=None,
        causality=prepared,
    )
    fields = result.get("fields")
    expected_keys = {
        "delivery_workspace_causality_schema_version",
        "delivery_workspace_causality_todo_id",
        "delivery_workspace_requirement",
        "delivery_workspace_causality_source",
        "delivery_workspace_causality_reason",
    }
    if (
        not isinstance(fields, Mapping)
        or (fields and set(fields) != expected_keys)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in fields.items()
        )
    ):
        raise RuntimeError(
            "TypeScript delivery workspace event fields result shape mismatch"
        )
    return dict(fields)


def delivery_workspace_causality_from_event_details(
    details: Mapping[str, Any] | None,
    *,
    todo_id: str | None,
) -> dict[str, str] | None:
    if not isinstance(details, Mapping):
        return None
    nested = details.get("delivery_workspace_causality")
    return _causality_result(
        _runtime_result(
            "from_event",
            expected_todo_id=normalize_todo_id(todo_id),
            nested_causality=_prepared_causality(
                nested if isinstance(nested, Mapping) else None
            ),
            flat_causality=_prepared_causality(
                {
                    "schema_version": details.get(
                        "delivery_workspace_causality_schema_version"
                    ),
                    "todo_id": details.get("delivery_workspace_causality_todo_id"),
                    "requirement": details.get("delivery_workspace_requirement"),
                    "source": details.get("delivery_workspace_causality_source"),
                    "reason": details.get("delivery_workspace_causality_reason"),
                }
            ),
        )
    )


def missing_delivery_workspace_resolution(
    causality: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the TS-owned admission decision for a missing workspace snapshot."""

    prepared = _prepared_causality(causality)
    if prepared is None:
        return None
    result = _runtime_result(
        "missing_workspace",
        expected_todo_id=None,
        causality=prepared,
    )
    value = result.get("resolution")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RuntimeError(
            "TypeScript delivery workspace resolution result shape mismatch"
        )
    decision = value.get("decision")
    expected = {
        "require_snapshot": (
            "required",
            "declared_workspace_snapshot_required",
            False,
        ),
        "omit_snapshot": (
            "not_required",
            "explicit_non_delivery_contract",
            False,
        ),
        "repair_contract": (
            "unknown",
            "todo_delivery_contract_not_explicit",
            True,
        ),
    }.get(decision)
    if expected is None:
        raise RuntimeError(
            "TypeScript delivery workspace resolution result shape mismatch"
        )
    requirement, reason, has_resolutions = expected
    expected_keys = {
        "schema_version",
        "todo_id",
        "decision",
        "requirement",
        "reason",
    }
    accepted_resolutions = value.get("accepted_resolutions")
    if has_resolutions:
        expected_keys.add("accepted_resolutions")
    if (
        set(value) != expected_keys
        or value.get("schema_version")
        != DELIVERY_WORKSPACE_RESOLUTION_SCHEMA_VERSION
        or normalize_todo_id(value.get("todo_id")) != value.get("todo_id")
        or value.get("requirement") != requirement
        or value.get("reason") != reason
        or (
            has_resolutions
            and accepted_resolutions
            != [
                "declare_repository_or_write_contract",
                "declare_explicit_non_delivery_contract",
            ]
        )
    ):
        raise RuntimeError(
            "TypeScript delivery workspace resolution result shape mismatch"
        )
    return dict(value)


def resolve_settlement_workspace_requirement(
    causality: Mapping[str, Any] | None,
    *,
    settlement_binding_kind: str,
    legacy_settlement_evidence: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Resolve workspace policy from the original typed settlement binding."""

    result = _runtime_result(
        "settlement_requirement",
        expected_todo_id=None,
        causality=_prepared_causality(causality),
        settlement_binding_kind=settlement_binding_kind,
        legacy_settlement_evidence=(
            dict(legacy_settlement_evidence)
            if isinstance(legacy_settlement_evidence, Mapping)
            else None
        ),
    )
    value = result.get("settlement_requirement")
    expected_keys = {
        "schema_version",
        "settlement_binding_kind",
        "requirement",
        "source",
        "reason",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != expected_keys
        or value.get("schema_version")
        != SETTLEMENT_WORKSPACE_REQUIREMENT_SCHEMA_VERSION
        or value.get("settlement_binding_kind") != settlement_binding_kind
        or value.get("requirement") not in DELIVERY_WORKSPACE_REQUIREMENTS
        or not str(value.get("source") or "").strip()
        or not str(value.get("reason") or "").strip()
    ):
        raise RuntimeError(
            "TypeScript settlement workspace requirement result shape mismatch"
        )
    return {key: str(value[key]) for key in expected_keys}


def completed_todo_workspace_causality(
    status_payload: Mapping[str, Any],
    *,
    goal_id: str,
    todo_id: str,
    source: str = "completed_todo_contract_fallback",
) -> dict[str, str] | None:
    """Recover an effect's causality from its exact projected Todo contract."""

    queue = status_payload.get("attention_queue")
    queue_items = queue.get("items") if isinstance(queue, Mapping) else []
    for queue_item in queue_items if isinstance(queue_items, list) else []:
        if not isinstance(queue_item, Mapping):
            continue
        if str(queue_item.get("goal_id") or "") != goal_id:
            continue
        project_asset = queue_item.get("project_asset")
        owners = (
            queue_item,
            project_asset if isinstance(project_asset, Mapping) else {},
        )
        for owner in owners:
            summary = owner.get("agent_todos")
            if not isinstance(summary, Mapping):
                continue
            for key in ("items", "recent_completed_advancement_items"):
                items = summary.get(key)
                for item in items if isinstance(items, list) else []:
                    if not isinstance(item, Mapping):
                        continue
                    if normalize_todo_id(item.get("todo_id")) != todo_id:
                        continue
                    return build_delivery_workspace_causality(
                        item,
                        source=source,
                    )
    return None
