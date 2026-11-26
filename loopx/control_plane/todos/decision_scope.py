"""Legacy input codec for the single typed decision-dependency rule owner."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TypedDict, cast

from ..effect_runtime import effect_runtime_result
from ..quota.effective_action import EffectiveAction
from .contract import (
    normalize_todo_blocks_agent,
    normalize_todo_claimed_by,
    normalize_todo_decision_scope,
    normalize_todo_decision_scope_outcomes,
    normalize_todo_global_gate,
    normalize_todo_id,
    normalize_todo_required_decision_scopes,
)
from .user_gate import is_user_gate_todo_item, open_user_gate_todo_items
from .todo_semantics import todo_projection_sort_key, todo_item_task_class, todo_item_has_removed_continuation_policy

DECISION_SCOPE_CONSISTENCY_SCHEMA_VERSION = "required_decision_scope_consistency_v0"
STANDING_DECISION_AUTHORITY_SCHEMA_VERSION = "standing_decision_authority_v0"
DECISION_SCOPE_RELATION_SCHEMA_VERSION = "decision_scope_relation_v0"
TODO_GATE_RELATION_SCHEMA_VERSION = "todo_gate_relation_v0"
TYPED_DECISION_SCOPE_RESULT_SCHEMA_VERSION = "todo_decision_scope_result_v0"
_RELATION_SCHEMA_VERSIONS = frozenset(
    {DECISION_SCOPE_RELATION_SCHEMA_VERSION, TODO_GATE_RELATION_SCHEMA_VERSION}
)
_AGENT_SUMMARY_ITEM_KEYS = (
    "current_agent_claimed_open_items", "current_agent_claimed_advancement_items",
    "first_executable_items", "executable_backlog_items", "first_open_items", "backlog_items", "items",
)
_USER_SUMMARY_ITEM_KEYS = (
    "gate_open_items", "user_action_open_items", "first_open_items",
    "backlog_items", "items", "other_agent_scoped_items",
)


class _ConsistencyProjection(dict[str, object]):
    __slots__ = ("_repair_signature", "_repair_plan")

    _repair_signature: tuple[str, ...]
    _repair_plan: dict[str, object] | None


class _BlockedFallbackCandidate(TypedDict):
    candidate_index: int
    gate_index: int
    relation: dict[str, object]


class _ScopedGateFallbackSelection(TypedDict):
    schema_version: str
    selected_index: int
    gate_index: int
    blocked: list[_BlockedFallbackCandidate]
    blocked_count: int
    has_blocking_gate: bool
    selected_relation: dict[str, object]
    deferred_replan: bool


def _evaluate(operation: str, **facts: object) -> dict[str, object]:
    result: object = effect_runtime_result("todo.decision_scope.evaluate", {
        "schema_version": "todo_decision_scope_request_v0", "operation": operation, **facts,
    })
    if (
        not isinstance(result, dict)
        or result.get("schema_version")
        != TYPED_DECISION_SCOPE_RESULT_SCHEMA_VERSION
        or "result" not in result
    ):
        detail = " repair_hint" if operation == "repair_hint" else ""
        raise TypeError(f"invalid typed decision scope{detail} projection")
    return result


def _projection(
    operation: str,
    value: object,
    *,
    schema_versions: frozenset[str],
    nullable: bool,
) -> dict[str, object] | None:
    if value is None and nullable:
        return None
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise TypeError(f"invalid typed decision scope {operation} projection")
    projection = {
        key: item for key, item in value.items() if isinstance(key, str)
    }
    if projection.get("schema_version") not in schema_versions:
        raise TypeError(f"invalid typed decision scope {operation} schema")
    return projection


def _required_projection(
    operation: str,
    value: object,
    *,
    schema_version: str,
) -> dict[str, object]:
    projection = _projection(
        operation,
        value,
        schema_versions=frozenset({schema_version}),
        nullable=False,
    )
    if projection is None:  # Defensive narrowing; nullable=False rejects None.
        raise TypeError(f"invalid typed decision scope {operation} projection")
    return projection


def _optional_relation(operation: str, value: object) -> dict[str, object] | None:
    return _projection(
        operation,
        value,
        schema_versions=_RELATION_SCHEMA_VERSIONS,
        nullable=True,
    )


def _relation_matrix(
    value: object,
    *,
    gate_count: int,
    item_count: int,
) -> list[list[dict[str, object] | None]]:
    if not isinstance(value, list) or len(value) != gate_count:
        raise TypeError("invalid typed decision scope relations matrix")
    matrix: list[list[dict[str, object] | None]] = []
    for row in value:
        if not isinstance(row, list) or len(row) != item_count:
            raise TypeError("invalid typed decision scope relations matrix")
        matrix.append([_optional_relation("relations", item) for item in row])
    return matrix


def _facts(item: dict[str, object]) -> dict[str, object]:
    return {
        "todo_id": normalize_todo_id(item.get("todo_id")),
        "status": str(item.get("status") or "open"), "done": item.get("done") is True,
        "claimed_by": normalize_todo_claimed_by(item.get("claimed_by")),
        "blocks_agent": normalize_todo_blocks_agent(item.get("blocks_agent")),
        "global_gate": bool(normalize_todo_global_gate(item.get("global_gate"))),
        "unblocks_todo_id": normalize_todo_id(item.get("unblocks_todo_id")),
        "decision_scope": normalize_todo_decision_scope(item.get("decision_scope")),
        "required_decision_scopes": normalize_todo_required_decision_scopes(item.get("required_decision_scopes")),
        "decision_scope_outcomes": normalize_todo_decision_scope_outcomes(item.get("decision_scope_outcomes")),
        # Preserve the existing legacy classification boundary, not new prose authority.
        "is_gate": is_user_gate_todo_item(item),
    }


def _source(summary: dict[str, object] | None, source: Sequence[dict[str, object]] | None,
            keys: tuple[str, ...]) -> list[dict[str, object]]:
    if source is not None:
        return [_facts(item) for item in source if isinstance(item, dict)]
    if not isinstance(summary, dict):
        return []
    result = []
    seen = set()
    for key in keys:
        values = (summary or {}).get(key)
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("done") is True or str(item.get("status") or "open") not in {"open", "blocked"}:
                continue
            identity = (item.get("todo_id"), item.get("index"), item.get("text"))
            if identity not in seen:
                seen.add(identity)
                result.append(_facts(item))
    return result


def _authority(authority: dict[str, object] | None) -> dict[str, object] | None:
    if not isinstance(authority, dict):
        return None
    entries = authority.get("entries")
    if not isinstance(entries, list):
        return None

    def decoded(entry: dict[str, object]) -> dict[str, object]:
        codecs: dict[str, Callable[[object], object]] = {
            "global_gate": normalize_todo_global_gate,
            "blocks_agent": normalize_todo_blocks_agent,
            "claimed_by": normalize_todo_claimed_by,
            "decision_scope": normalize_todo_decision_scope,
        }
        return {**entry, **{key: codec(entry[key]) for key, codec in codecs.items() if key in entry}}

    conflicts = authority.get("conflicts")
    return {
        **authority,
        "entries": [decoded(entry) for entry in entries if isinstance(entry, dict)],
        "conflicts": [decoded(entry) for entry in conflicts if isinstance(entry, dict)]
        if isinstance(conflicts, list) else [],
    }


def standing_decision_authority_for_agent(authority: dict[str, object] | None, *,
                                          agent_id: str | None) -> dict[str, object] | None:
    normalized_authority = _authority(authority)
    if normalized_authority is None:
        return None
    return _projection(
        "standing",
        _evaluate(
            "standing",
            authority=normalized_authority,
            agent_id=normalize_todo_claimed_by(agent_id),
        )["result"],
        schema_versions=frozenset({STANDING_DECISION_AUTHORITY_SCHEMA_VERSION}),
        nullable=True,
    )


def _repair_signature(consistency: Mapping[str, object]) -> tuple[str, ...]:
    errors = consistency.get("errors")
    if not isinstance(errors, list):
        return ()
    return tuple(
        code for error in errors
        if isinstance(error, dict) and isinstance(code := error.get("reason_code"), str)
    )


def _validate_repair_plan(value: object) -> dict[str, object]:
    text_fields = ("trigger", "recommended_mode", "blocked_action_scope", "reason", "repair_focus", "spend_policy")
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "todo_decision_scope_repair_plan_v0"
        or value.keys() != {"schema_version", "allowed", "notify", *text_fields}
        or value["allowed"] is not True
        or value["notify"] != "DONT_NOTIFY"
        or any(not isinstance(value[key], str) or not value[key] for key in text_fields)
    ):
        raise TypeError("invalid typed decision scope repair_hint projection")
    return value


def build_required_decision_scope_consistency(
    agent_todo_summary: dict[str, object] | None, user_todo_summary: dict[str, object] | None, *,
    agent_id: str | None, registered_agent_ids: Sequence[str] | None = None,
    agent_source_items: Sequence[dict[str, object]] | None = None,
    user_source_items: Sequence[dict[str, object]] | None = None,
    standing_decision_authority: dict[str, object] | None = None,
) -> dict[str, object]:
    envelope = _evaluate(
        "consistency",
        agent_items=_source(agent_todo_summary, agent_source_items, _AGENT_SUMMARY_ITEM_KEYS),
        user_items=_source(user_todo_summary, user_source_items, _USER_SUMMARY_ITEM_KEYS),
        agent_id=normalize_todo_claimed_by(agent_id),
        registered_agents=sorted({
            value for raw in registered_agent_ids or []
            if (value := normalize_todo_claimed_by(raw))
        }),
        standing_authority=_authority(standing_decision_authority),
    )
    projection = _ConsistencyProjection(_required_projection(
        "consistency", envelope["result"],
        schema_version=DECISION_SCOPE_CONSISTENCY_SCHEMA_VERSION,
    ))
    projection._repair_signature = _repair_signature(projection)
    projection._repair_plan = (
        _validate_repair_plan(envelope["repair_plan"]) if "repair_plan" in envelope else None
    )
    return projection


def build_required_decision_scope_repair_hint(
    consistency: Mapping[str, object],
) -> dict[str, object] | None:
    if consistency.get("ok") is not False:
        return None
    signature = _repair_signature(consistency)
    if (
        isinstance(consistency, _ConsistencyProjection)
        and consistency._repair_plan is not None
        and consistency._repair_signature == signature
    ):
        plan = consistency._repair_plan
    else:
        envelope = _evaluate("repair_hint", reason_codes=list(signature))
        plan = _validate_repair_plan(envelope.get("repair_plan"))

    return {
        "source": "quota.should-run",
        "trigger": plan["trigger"],
        "recommended_mode": plan["recommended_mode"],
        "effective_action": EffectiveAction.TODO_DECISION_SCOPE_PROJECTION_REPAIR.value,
        "blocked_action_scope": plan["blocked_action_scope"],
        "allowed": plan["allowed"],
        "notify": plan["notify"],
        "reason": plan["reason"],
        "repair_focus": plan["repair_focus"],
        "spend_policy": plan["spend_policy"],
        "consistency": consistency,
    }


def todo_gate_relations(
    gates: Sequence[dict[str, object]], items: Sequence[dict[str, object]],
) -> list[list[dict[str, object] | None]]:
    """Evaluate a consumer's candidate set in one RPC, retaining positional identity."""
    if not gates or not items:
        return [[] for _ in gates]
    return _relation_matrix(
        _evaluate(
            "relations",
            gates=[_facts(gate) for gate in gates],
            items=[_facts(item) for item in items],
        )["result"],
        gate_count=len(gates),
        item_count=len(items),
    )


def select_scoped_gate_fallback(gates: Sequence[dict[str, object]], items: Sequence[dict[str, object]], *,
                               agent_id: str | None, allow_unrelated_gate: bool,
                               monitor_debt_backoff_active: bool,
                               requested_todo_id: str | None = None) -> _ScopedGateFallbackSelection | None:
    """Decode legacy facts; the typed owner returns positions, never display rows."""
    if not gates or not items:
        return None
    from .contract import normalize_todo_bound_agent, normalize_todo_excluded_agents

    def facts(item: dict[str, object]) -> dict[str, object]:
        priority, index = todo_projection_sort_key(item)
        return {**_facts(item), "action_kind": item.get("action_kind"),
                "archive_state": item.get("archive_state"), "resume_ready": item.get("resume_ready") is True,
                "bound_agent": normalize_todo_bound_agent(item.get("bound_agent")),
                "excluded_agents": normalize_todo_excluded_agents(item.get("excluded_agents")),
                "removed": todo_item_has_removed_continuation_policy(item),
                "task_class": todo_item_task_class(item), "priority_rank": priority, "persisted_index": index}

    selection = _projection(
        "fallback",
        _evaluate("fallback", gates=[facts(gate) for gate in gates], candidates=[facts(item) for item in items],
                  agent_id=normalize_todo_claimed_by(agent_id), allow_unrelated_gate=allow_unrelated_gate,
                  requested_todo_id=normalize_todo_id(requested_todo_id),
                  monitor_debt_backoff_active=monitor_debt_backoff_active)["result"],
        schema_versions=frozenset({"scoped_gate_fallback_selection_v0"}),
        nullable=True,
    )
    return cast(_ScopedGateFallbackSelection | None, selection)


def todo_gate_scope_projections(
    gates: Sequence[dict[str, object]], items: Sequence[dict[str, object]], *, agent_id: str | None,
) -> list[dict[str, object]]:
    """Transport one source snapshot to the existing typed dependency owner."""
    def facts(item: dict[str, object]) -> dict[str, object]:
        return {**_facts(item), "action_kind": item.get("action_kind"),
                "archive_state": item.get("archive_state")}
    result = _required_projection("gate_scopes", _evaluate("gate_scopes",
        gates=[facts(gate) for gate in gates], items=[facts(item) for item in items],
        agent_id=normalize_todo_claimed_by(agent_id))["result"], schema_version="todo_gate_scope_projections_v0")
    projections = result.get("items")
    if not isinstance(projections, list) or len(projections) != len(items):
        raise TypeError("invalid typed gate scope projection cardinality")
    return [_required_projection("gate_scope", row, schema_version="todo_gate_scope_projection_v0") for row in projections]


def _readiness_facts(item: dict[str, object]) -> dict[str, object]:
    return {
        **_facts(item),
        "identity_text": str(item.get("todo_id") or ""),
        "status_text": str(item.get("status") or ""),
        "target_text": str(item.get("unblocks_todo_id") or ""),
    }


def _global_readiness_request(payload: Mapping[str, object]) -> dict[str, object]:
    """Bound transport to candidate containers; TS alone selects their list keys."""
    bounded = {
        key: payload.get(key)
        for key in ("selected_todo", "agent_lane_next_action", "normal_delivery_allowed")
    }
    decoded = {
        f"/{key}": _readiness_facts(value)
        for key, value in bounded.items()
        if isinstance(value, dict)
    }
    for container_key in ("agent_todo_summary", "agent_scope_frontier"):
        container = payload.get(container_key)
        lists = {
            key: value for key, value in container.items() if isinstance(value, list)
        } if isinstance(container, dict) else {}
        bounded[container_key] = lists
        for key, values in lists.items():
            for index, item in enumerate(values):
                if isinstance(item, dict):
                    decoded[f"/{container_key}/{key}/{index}"] = _readiness_facts(item)
    user_summary = payload.get("user_todo_summary")
    return {
        "schema_version": "global_todo_readiness_request_v0",
        "payload": bounded,
        "decoded": decoded,
        "gates": [
            _readiness_facts(gate)
            for gate in open_user_gate_todo_items(user_summary if isinstance(user_summary, dict) else None)
        ],
    }


def project_global_todo_readiness(payload: Mapping[str, object]) -> dict[str, object]:
    """Decode persisted fields and reuse the shared Python gate selector."""
    result = effect_runtime_result(
        "todo.global_readiness.project", _global_readiness_request(payload),
    )
    projection = _required_projection(
        "global_readiness", result, schema_version="global_todo_readiness_v0",
    )
    for key in ("warnings", "candidates"):
        rows = projection.get(key)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise TypeError("invalid typed decision scope global readiness rows")
    return projection
