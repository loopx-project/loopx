from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import cast

from ..goals.goal_vision_policy import COMPLETED_TODO_CHAIN_REPLAN_THRESHOLD
from ..runtime.time import now_utc
from .frontier_deadline import todo_summary_frontier_deadline
from .quota_selection import project_quota_planning
from .summary_item import todo_planning_source_items, todo_summary_source_items

Payload = dict[str, object]
PayloadView = Mapping[str, object]

MONITOR_DUE_ITEM_LIMIT = 1
QUOTA_PAYLOAD_ITEM_TEXT_LIMIT = 180
QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT = 2
QUOTA_PAYLOAD_USER_ACTION_ITEM_LIMIT = 3
QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT = 2
QUOTA_PAYLOAD_COMPACTION_SCHEMA_VERSION = "quota_todo_summary_payload_compaction_v0"
AGENT_LANE_STATUS_TODO_COMPACTION_SCHEMA_VERSION = (
    "agent_lane_status_todo_summary_compaction_v1"
)
AGENT_LANE_STATUS_TODO_REFERENCE_SCHEMA_VERSION = (
    "agent_lane_status_todo_reference_v0"
)
QUOTA_PAYLOAD_ITEM_FIELDS = (
    "goal_acceptance_guard",
    "schema_version",
    "index",
    "text",
    "title",
    "todo_id",
    "status",
    "priority",
    "task_class",
    "action_kind",
    "task_domain",
    "decision_scope",
    "required_decision_scopes",
    "task_repository",
    "continuation_policy",
    "required_capabilities",
    "required_write_scopes",
    "missing_capabilities",
    "claimed_by",
    "bound_agent",
    "goal_bound",
    "blocks_agent",
    "excluded_agents",
    "global_gate",
    "unblocks_todo_id",
    "resume_when",
    "resume_monitor_generation",
    "resume_condition",
    "resume_ready",
    "blocked_retry",
    "blocking_monitor_todo_id",
    "no_followup",
    "successor_todo_ids",
    "completion_continuation",
    "completion_recovery",
    "replan_obligation_id",
    "target_key",
    "cadence",
    "next_due_at",
    "expires_at",
    "watch_only",
    "last_checked_at",
    "result_hash",
    "consecutive_no_change",
    "material_change",
    "material_change_generation",
    "material_change_scope",
    "material_change_scope_generations",
    "max_no_change_before_replan",
    "route_continuation_replan_required",
    "route_continuation_reason",
    "route_id",
    "route_key",
    "completed_at",
    "updated_at",
    "gate_state",
    "reason",
)
QUOTA_PAYLOAD_LANE_LIMITS = {
    "monitor_due_items": MONITOR_DUE_ITEM_LIMIT,
    "watch_only_monitor_due_items": MONITOR_DUE_ITEM_LIMIT,
    "non_watch_only_monitor_due_items": MONITOR_DUE_ITEM_LIMIT,
    "monitor_capability_blocked_due_items": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "monitor_schedule_gap_items": MONITOR_DUE_ITEM_LIMIT,
    "first_open_items": 3,
    "first_executable_items": 3,
    "active_next_action_items": 3,
    "active_next_action_executable_items": 3,
    "monitor_open_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "backlog_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "executable_backlog_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "unclaimed_priority_open_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "claimed_open_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "claimed_advancement_open_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "claimed_monitor_open_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "current_agent_claimed_open_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "current_agent_claimed_advancement_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "current_agent_claimed_monitor_items": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
    "current_agent_blocker_items": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "recent_completed_advancement_items": COMPLETED_TODO_CHAIN_REPLAN_THRESHOLD,
    "claimed_by_others_items": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "other_agent_scoped_items": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "other_agent_bound_user_action_items": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "user_action_items": QUOTA_PAYLOAD_USER_ACTION_ITEM_LIMIT,
    "resume_blocked_items": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "handoff_gates": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "current_agent_handoff_gates": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
    "current_agent_cleared_without_successor_handoff_gates": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
}
AGENT_LANE_STATUS_TODO_ITEM_FIELDS = (
    "schema_version",
    "index",
    "todo_id",
    "text",
    "title",
    "status",
    "priority",
    "task_class",
    "action_kind",
    "task_domain",
    "claimed_by",
    "bound_agent",
    "blocks_agent",
    "global_gate",
    "unblocks_todo_id",
    "required_capabilities",
    "required_write_scopes",
    "missing_capabilities",
    "resume_ready",
    "next_due_at",
)
AGENT_LANE_STATUS_TODO_LANES = {
    "agent": ("items", "current_agent_blocker_items"),
    "user": ("items", "gate_open_items", "user_action_items", "current_agent_blocker_items"),
}
AGENT_LANE_STATUS_TODO_LANE_LIMITS = {"gate_open_items": 3, "user_action_items": 3}
_AGENT_LANE_STATUS_DICT_FIELDS = frozenset(
    {"monitor_writeback", "work_counts", "deferred_work_counts"}
)
_AGENT_LANE_STATUS_REF_FIELDS = (
    "schema_version", "source_section", "open", "done", "total", "deferred_count",
    "claimed_open_count", "unclaimed_open_count", "projection_view", "detail_pointer",
)


def _as_dict(value: object) -> Payload | None:
    return cast(Payload, value) if isinstance(value, dict) else None


def _to_dict_or_none(value: PayloadView | None) -> Payload | None:
    if value is None:
        return None
    return cast(Payload, value) if isinstance(value, dict) else dict(value)


def _require_dict_list(value: object, *, error: str) -> list[Payload]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise RuntimeError(error)
    return cast(list[Payload], value)


def _summarize_todos_for_quota(
    value: object, *, summary_kind: str, agent_identity: PayloadView | None = None,
    filter_user_gate_blocks_agent: bool = False, available_capabilities: object = None,
    resolve_capacity: bool = False,
) -> Payload | None:
    source = _as_dict(value)
    if source is None:
        return None
    return cast(Payload, project_quota_planning(
        source, all_open_items=[], source_open_count=source.get("open_count"),
        agent_identity=_to_dict_or_none(agent_identity),
        filter_user_gate_blocks_agent=filter_user_gate_blocks_agent,
        available_capabilities=available_capabilities, resolve_capacity=resolve_capacity,
        summary_kind=summary_kind,
    ))


def summarize_user_todos_for_quota(
    value: object, *, agent_identity: PayloadView | None = None,
    filter_user_gate_blocks_agent: bool = False, available_capabilities: object = None,
    resolve_capacity: bool = False,
) -> Payload | None:
    return _summarize_todos_for_quota(
        value, summary_kind="user", agent_identity=agent_identity,
        filter_user_gate_blocks_agent=filter_user_gate_blocks_agent,
        available_capabilities=available_capabilities, resolve_capacity=resolve_capacity,
    )


def _truncate_quota_payload_text(value: object, *, limit: int) -> object:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _compact_payload_item_fields(item: object, *, fields: tuple[str, ...]) -> object:
    mapping = _as_dict(item)
    if mapping is None:
        return item
    compact: Payload = {}
    for key in fields:
        value = mapping.get(key)
        if value is None:
            continue
        if key in {"text", "title"}:
            value = _truncate_quota_payload_text(value, limit=QUOTA_PAYLOAD_ITEM_TEXT_LIMIT)
        compact[key] = value
    return compact


def _compact_quota_payload_item(item: object) -> object:
    compact = _compact_payload_item_fields(item, fields=QUOTA_PAYLOAD_ITEM_FIELDS)
    mapping, compact_dict = _as_dict(item), _as_dict(compact)
    if mapping is not None and compact_dict is not None and "text" not in compact_dict and mapping.get("text") is not None:
        compact_dict["text"] = _truncate_quota_payload_text(
            mapping.get("text"), limit=QUOTA_PAYLOAD_ITEM_TEXT_LIMIT
        )
    return compact


def _compact_quota_payload_item_list(items: object, *, limit: int) -> list[object]:
    if not isinstance(items, list):
        return []
    return [_compact_quota_payload_item(item) for item in items[:limit]]


def _compact_nested_item_lists(value: object, *, items_suffix_only: bool = False) -> object:
    mapping = _as_dict(value)
    if mapping is None:
        return value
    return {
        key: (
            _compact_quota_payload_item_list(child, limit=QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT)
            if isinstance(child, list) and (not items_suffix_only or key.endswith("items"))
            else child
        )
        for key, child in mapping.items()
    }


def _compact_quota_payload_claim_scope(value: object) -> object:
    return _compact_nested_item_lists(value, items_suffix_only=False)


def _compact_quota_payload_nested_warning(value: object) -> object:
    return _compact_nested_item_lists(value, items_suffix_only=True)


def _compact_lane_items(
    key: str, value: Sequence[object], *, limit: int,
    item_compactor: Callable[[object], object], compacted_lanes: dict[str, dict[str, int]],
) -> list[object]:
    if len(value) > limit:
        compacted_lanes[key] = {"shown": limit, "total": len(value)}
    return [item_compactor(item) for item in value[:limit]]


def compact_quota_todo_summary_for_payload(summary: PayloadView) -> Payload:
    """Keep quota hot-path todo summaries bounded without changing decision input."""
    frontier_deadline = todo_summary_frontier_deadline(summary, current_time=now_utc())
    compact: Payload = {}
    compacted_lanes: dict[str, dict[str, int]] = {}
    for key, value in summary.items():
        if key == "advancement_frontier_revision_index":
            # Decision input is complete by construction, but it is not an
            # agent-facing diagnostic surface.
            continue
        if key in {"source_completeness", "closure_intent"}:
            continue
        if isinstance(value, list):
            limit = QUOTA_PAYLOAD_LANE_LIMITS.get(key, QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT)
            compact[key] = _compact_lane_items(
                key, value, limit=limit,
                item_compactor=_compact_quota_payload_item, compacted_lanes=compacted_lanes,
            )
        elif key == "claim_scope":
            compact[key] = _compact_quota_payload_claim_scope(value)
        elif isinstance(value, dict):
            compact[key] = _compact_quota_payload_nested_warning(value)
        else:
            compact[key] = value
    if frontier_deadline:
        compact["frontier_deadline"] = frontier_deadline
    compact["payload_compaction"] = {
        "schema_version": QUOTA_PAYLOAD_COMPACTION_SCHEMA_VERSION,
        "item_text_limit": QUOTA_PAYLOAD_ITEM_TEXT_LIMIT,
        "visibility_lane_item_limit": QUOTA_PAYLOAD_VISIBILITY_LANE_LIMIT,
        "diagnostic_lane_item_limit": QUOTA_PAYLOAD_DIAGNOSTIC_LANE_LIMIT,
        "compacted_lanes": compacted_lanes,
        "full_detail_cold_path": "status, todo list, or active state",
    }
    return compact


def _compact_agent_lane_status_todo_item(item: object) -> object:
    return _compact_payload_item_fields(item, fields=AGENT_LANE_STATUS_TODO_ITEM_FIELDS)


def _compact_agent_lane_status_todo_summary(summary: PayloadView, *, role: str) -> Payload:
    retained_lanes = set(AGENT_LANE_STATUS_TODO_LANES[role])
    compact: Payload = {}
    omitted_lanes: list[str] = []
    compacted_lanes: dict[str, dict[str, int]] = {}
    for key, value in summary.items():
        if key == "payload_compaction":
            continue
        if isinstance(value, list):
            if key not in retained_lanes:
                if value:
                    omitted_lanes.append(key)
                continue
            compact[key] = _compact_lane_items(
                key, value, limit=AGENT_LANE_STATUS_TODO_LANE_LIMITS.get(key, 1),
                item_compactor=_compact_agent_lane_status_todo_item, compacted_lanes=compacted_lanes,
            )
            continue
        if isinstance(value, dict):
            if key in _AGENT_LANE_STATUS_DICT_FIELDS:
                compact[key] = _compact_quota_payload_nested_warning(value)
            continue
        compact[key] = value
    compact["payload_compaction"] = {
        "schema_version": AGENT_LANE_STATUS_TODO_COMPACTION_SCHEMA_VERSION,
        "item_text_limit": QUOTA_PAYLOAD_ITEM_TEXT_LIMIT,
        "retained_lanes": sorted(retained_lanes),
        "omitted_nonempty_lane_count": len(omitted_lanes),
        "compacted_lanes": compacted_lanes,
        "full_detail_cold_path": "status without --agent-id, todo list, or active state",
    }
    return compact


def _agent_lane_status_project_asset_todo_reference(summary: PayloadView, *, role: str) -> Payload:
    reference: Payload = {
        key: value
        for key in _AGENT_LANE_STATUS_REF_FIELDS
        if (value := summary.get(key)) is not None
    }
    reference["payload_reference"] = {
        "schema_version": AGENT_LANE_STATUS_TODO_REFERENCE_SCHEMA_VERSION,
        "canonical_path": f"attention_queue.items[].{role}_todos",
        "full_detail_cold_path": "status without --agent-id, todo list, or active state",
    }
    return reference


def _compact_role_todos(
    container: Payload, *, transform: Callable[[PayloadView, str], Payload]
) -> int:
    count = 0
    for key, role in (("user_todos", "user"), ("agent_todos", "agent")):
        summary = _as_dict(container.get(key))
        if summary is not None:
            container[key] = transform(summary, role)
            count += 1
    return count


def compact_agent_lane_todos_for_status_display(payload: Payload) -> None:
    queue = _as_dict(payload.get("attention_queue"))
    items = queue.get("items") if queue is not None else None
    if not isinstance(items, list):
        return
    compacted = references = 0
    for item in items:
        item_dict = _as_dict(item)
        if item_dict is None:
            continue
        compacted += _compact_role_todos(
            item_dict,
            transform=lambda summary, role: _compact_agent_lane_status_todo_summary(summary, role=role),
        )
        project_asset = _as_dict(item_dict.get("project_asset"))
        if project_asset is not None:
            references += _compact_role_todos(
                project_asset,
                transform=lambda summary, role: _agent_lane_status_project_asset_todo_reference(summary, role=role),
            )
    if compacted:
        payload["agent_lane_todo_summary_compaction"] = {
            "schema_version": AGENT_LANE_STATUS_TODO_COMPACTION_SCHEMA_VERSION,
            "compacted_summary_count": compacted,
            "project_asset_reference_count": references,
            "reason": (
                "status --agent-id keeps one compact todo summary per role and "
                "replaces nested project-asset duplicates with references"
            ),
        }


def summarize_project_asset_todos_for_quota(
    value: object, *, agent_identity: PayloadView | None = None,
    filter_user_gate_blocks_agent: bool = False, available_capabilities: object = None,
    resolve_capacity: bool = False,
) -> Payload | None:
    return _summarize_todos_for_quota(
        value, summary_kind="project_asset", agent_identity=agent_identity,
        filter_user_gate_blocks_agent=filter_user_gate_blocks_agent,
        available_capabilities=available_capabilities, resolve_capacity=resolve_capacity,
    )


def is_canonical_attention_todo_summary(value: object) -> bool:
    mapping = _as_dict(value)
    if mapping is None:
        return False
    if mapping.get("schema_version") == "todo_summary_v0":
        return True
    source_section = str(mapping.get("source_section") or "").strip().lower()
    if source_section.startswith("raw "):
        return False
    return source_section in {"agent todo", "user todo"}


def select_quota_todo_summary(
    canonical_value: object, project_asset_value: object, *,
    agent_identity: PayloadView | None = None, filter_user_gate_blocks_agent: bool = False,
    available_capabilities: object = None,
) -> Payload | None:
    sources: tuple[tuple[Callable[..., Payload | None], object], ...] = (
        (summarize_user_todos_for_quota, canonical_value),
        (summarize_project_asset_todos_for_quota, project_asset_value),
    )
    if not is_canonical_attention_todo_summary(canonical_value):
        sources = tuple(reversed(sources))
    # Source precedence is decided before projecting Agent lanes/capacity.
    # The fallback is needed only when the preferred source has no summary.
    for summarize, value in sources:
        summary = summarize(
            value, agent_identity=agent_identity,
            filter_user_gate_blocks_agent=filter_user_gate_blocks_agent,
            available_capabilities=available_capabilities, resolve_capacity=True,
        )
        if summary:
            return summary
    return None


def _quota_projection_request(
    source: Payload, *, agent_identity: PayloadView | None,
    filter_user_gate_blocks_agent: bool, available_capabilities: object = None,
    resolve_capacity: bool = False, summary_kind: str = "user",
    consume_display_views: bool = False, display_view_names: list[str] | None = None,
) -> Payload:
    return cast(Payload, project_quota_planning(
        source, all_open_items=[], source_open_count=source.get("open_count"),
        agent_identity=_to_dict_or_none(agent_identity),
        filter_user_gate_blocks_agent=filter_user_gate_blocks_agent,
        available_capabilities=available_capabilities, resolve_capacity=resolve_capacity,
        summary_kind=summary_kind, consume_display_views=consume_display_views,
        request_only=True, display_view_names=display_view_names,
    ))


def _project_quota_wires(requests: list[Payload], *, error: str) -> list[object]:
    from .goal_todo_projection import project_todo_read

    result = project_todo_read({"kind": "quota_projection", "requests": requests})
    wires = result.get("quota_wires")
    if not isinstance(wires, list) or len(wires) != len(requests):
        raise RuntimeError(error)
    return cast(list[object], wires)


def select_quota_todo_summaries(
    user_source: object, agent_source: object, *, agent_identity: PayloadView | None,
) -> tuple[Payload | None, Payload | None]:
    """Compose the refresh caller's two native summaries in one transaction.

    Supplied and expired source references retain the standalone adapters.
    The native owner still validates every source and independent request.
    """
    from .quota_selection import _decode_quota_summary_wire
    from .summary_item import _CanonicalTodoSummary

    if isinstance(user_source, _CanonicalTodoSummary) and isinstance(agent_source, _CanonicalTodoSummary):
        requests = [
            _quota_projection_request(
                source, agent_identity=agent_identity, filter_user_gate_blocks_agent=index == 0
            )
            for index, source in enumerate((user_source, agent_source))
        ]
        if all(
            (summary_req := _as_dict(request.get("summary_request"))) is not None
            and "source_reference" in summary_req
            for request in requests
        ):
            wires = _project_quota_wires(requests, error="invalid complete quota summary response")
            return (
                cast(Payload, _decode_quota_summary_wire(wires[0], user_source)),
                cast(Payload, _decode_quota_summary_wire(wires[1], agent_source)),
            )
    return (
        select_quota_todo_summary(
            user_source, None, agent_identity=agent_identity, filter_user_gate_blocks_agent=True
        ),
        select_quota_todo_summary(agent_source, None, agent_identity=agent_identity),
    )


def select_quota_todo_summaries_with_terminal(
    user_source: object, agent_source: object, *, agent_identity: PayloadView | None,
) -> tuple[Payload | None, Payload | None, list[Payload]]:
    """Read two summaries and complete Agent terminal rows in one transaction.

    Reuse the typed quota and display owners while requesting only consumed
    views. Ordinary quota retains its own full displays and prepared wait facts.
    """
    from .quota_selection import _decode_quota_summary_wire

    sources: list[tuple[str, Payload]] = [
        (role, cast(Payload, value))
        for role, value in (("user", user_source), ("agent", agent_source))
        if isinstance(value, dict)
    ]
    requests = [
        _quota_projection_request(
            value, agent_identity=agent_identity, filter_user_gate_blocks_agent=role == "user",
            consume_display_views=role == "agent",
            display_view_names=["terminal"] if role == "agent" else None,
        )
        for role, value in sources
    ]
    summaries: dict[str, Payload] = {}
    terminal: list[Payload] = []
    if requests:
        wires = _project_quota_wires(requests, error="invalid complete quota projection response")
        for (role, source), wire in zip(sources, wires, strict=True):
            summaries[role] = cast(Payload, _decode_quota_summary_wire(wire, source))
            if role == "agent":
                wire_dict = _as_dict(wire)
                views = _as_dict(wire_dict.get("display_views")) if wire_dict is not None else None
                if views is None or set(views) != {"terminal"}:
                    raise RuntimeError("invalid consumed Agent terminal view")
                terminal = _require_dict_list(
                    views["terminal"], error="invalid Agent terminal source rows"
                )
    return summaries.get("user"), summaries.get("agent"), terminal


def select_quota_todo_contexts(
    user_canonical: object, user_asset: object, agent_canonical: object, agent_asset: object,
    *, agent_identity: PayloadView | None, available_capabilities: object,
) -> tuple[Payload | None, Payload | None, dict[str, list[Payload]], list[Payload]]:
    """The quota caller explicitly consumes both summaries and Agent displays.

    One native read transaction materializes each complete source. A supplied
    compatibility source retains its full codec and cannot acquire authority.
    """
    from .quota_selection import _decode_quota_summary_wire
    from .summary_item import _QuotaProjectedSummary

    values: list[Payload] = []
    roles: list[str] = []
    requests: list[Payload] = []
    for role, canonical, asset in (
        ("user", user_canonical, user_asset),
        ("agent", agent_canonical, agent_asset),
    ):
        selected = _select_todo_source(canonical, asset)
        if selected is None:
            continue
        kind, source = selected
        values.append(source)
        roles.append(role)
        request = _quota_projection_request(
            source, agent_identity=agent_identity, filter_user_gate_blocks_agent=role == "user",
            available_capabilities=available_capabilities, resolve_capacity=True,
            summary_kind=kind, consume_display_views=role == "agent",
        )
        summary_req = _as_dict(request.get("summary_request"))
        if summary_req is not None:
            summary_req["consume_source_summary"] = role == "user"
            summary_req["consume_vision_wait_basis"] = role == "agent"
        requests.append(request)
    summaries: dict[str, Payload] = {}
    views: dict[str, list[Payload]] = {"summary": [], "planning": [], "terminal": []}
    user_rows: list[Payload] = []
    if requests:
        wires = _project_quota_wires(requests, error="invalid complete quota projection response")
        for role, source, wire in zip(roles, values, wires, strict=True):
            summaries[role] = cast(Payload, _decode_quota_summary_wire(wire, source))
            wire_dict = _as_dict(wire)
            if role == "user":
                user_rows = _require_dict_list(
                    wire_dict.get("source_summary") if wire_dict is not None else None,
                    error="invalid User quota source display",
                )
            if role == "agent":
                raw = _as_dict(wire_dict.get("display_views")) if wire_dict is not None else None
                if raw is None or set(raw) != set(views):
                    raise RuntimeError("invalid explicit Agent quota display views")
                views = {
                    name: _require_dict_list(
                        rows, error="invalid explicit Agent quota display views"
                    )
                    for name, rows in raw.items()
                }
                basis = _as_dict(wire_dict.get("vision_wait_basis")) if wire_dict is not None else None
                if basis is None:
                    raise RuntimeError("invalid prepared quota vision wait basis")
                summaries[role] = cast(Payload, _QuotaProjectedSummary(
                    summaries[role], basis, views["terminal"],
                    (agent_identity or {}).get("agent_id"),
                ))
    return summaries.get("user"), summaries.get("agent"), views, user_rows


def _select_todo_source(canonical_value: object, project_asset_value: object) -> tuple[str, Payload] | None:
    """Keep the projection kind selected by precedence, including aliased values."""
    sources: tuple[tuple[str, object], ...] = (
        ("user", canonical_value),
        ("project_asset", project_asset_value),
    )
    if not is_canonical_attention_todo_summary(canonical_value):
        sources = tuple(reversed(sources))
    return next(
        ((kind, cast(Payload, source)) for kind, source in sources if isinstance(source, dict)),
        None,
    )


def select_quota_todo_source_items(canonical_value: object, project_asset_value: object) -> list[Payload]:
    """Return the authoritative pre-hot-path todo source for semantic checks."""
    selected = _select_todo_source(canonical_value, project_asset_value)
    return cast(list[Payload], todo_summary_source_items(selected[1])) if selected is not None else []


def select_planning_inventory_source_items(
    canonical_value: object, project_asset_value: object, *, include_terminal: bool = False,
) -> list[Payload]:
    """Select the canonical non-terminal rows shared by planning read models."""
    selected = _select_todo_source(canonical_value, project_asset_value)
    return (
        cast(list[Payload], todo_planning_source_items(selected[1], include_terminal=include_terminal))
        if selected is not None
        else []
    )


def _task_orchestration_authority_items(value: object, *, role: str) -> list[Payload] | None:
    mapping = _as_dict(value)
    if mapping is None:
        return None
    authority = _as_dict(mapping.get("task_orchestration_authority"))
    if (
        authority is None
        or authority.get("schema_version") != "task_orchestration_authority_v0"
        or authority.get("role") != role
    ):
        return None
    key = "candidate_items" if role == "agent" else "user_blocker_items"
    items = authority.get(key)
    if not isinstance(items, list):
        return None
    return [cast(Payload, item) for item in items if isinstance(item, dict)]


def select_task_orchestration_authority_items(
    canonical_value: object, project_asset_value: object, *, role: str,
    fallback_source_items: list[Payload] | None = None,
) -> list[Payload]:
    canonical_items = _task_orchestration_authority_items(canonical_value, role=role)
    project_asset_items = _task_orchestration_authority_items(project_asset_value, role=role)
    if is_canonical_attention_todo_summary(canonical_value):
        selected = canonical_items if canonical_items is not None else project_asset_items
    else:
        selected = project_asset_items if project_asset_items is not None else canonical_items
    if selected is not None:
        return selected
    if fallback_source_items is not None:
        # Preserve the independent mutable display returned by the old call.
        return deepcopy(fallback_source_items)
    return select_quota_todo_source_items(canonical_value, project_asset_value)
