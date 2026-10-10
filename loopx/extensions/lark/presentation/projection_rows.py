from __future__ import annotations

import json
import re
from typing import Any, Mapping

from ....control_plane.todos.contract import (
    TODO_STATUS_BLOCKED,
    TODO_STATUS_DEFERRED,
    TODO_STATUS_DONE,
    TODO_STATUS_OPEN,
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_USER_GATE,
    normalize_explicit_todo_task_class,
    normalize_todo_excluded_agents,
    normalize_todo_goal_bound,
    normalize_todo_status,
)

TEXT_LIMIT = 4000


def _compact_text(value: Any, *, limit: int = TEXT_LIMIT) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def todo_matches_agent_scope(block: dict[str, Any], agent_id: str | None) -> bool:
    if not agent_id:
        return True
    if agent_id in normalize_todo_excluded_agents(block.get("excluded_agents")):
        return False
    if normalize_todo_goal_bound(block.get("goal_bound")) is True:
        return True
    for key in ("claimed_by", "bound_agent", "blocks_agent"):
        value = block.get(key)
        if isinstance(value, str) and value.strip() == agent_id:
            return True
        if isinstance(value, list) and agent_id in {
            str(item).strip() for item in value
        }:
            return True
    return False


def _projection_matches_agent_scope(
    block: dict[str, Any], agent_id: str | None
) -> bool:
    if not agent_id:
        return True
    if agent_id in normalize_todo_excluded_agents(block.get("excluded_agents")):
        return False
    if todo_matches_agent_scope(block, agent_id):
        return True
    claimed_by = block.get("claimed_by")
    if isinstance(claimed_by, str) and claimed_by.strip():
        return False
    if isinstance(claimed_by, list) and [
        item for item in claimed_by if str(item).strip()
    ]:
        return False
    blocks_agent = block.get("blocks_agent")
    if isinstance(blocks_agent, str) and blocks_agent.strip():
        return False
    if isinstance(blocks_agent, list) and [
        item for item in blocks_agent if str(item).strip()
    ]:
        return False
    if block.get("projection_agent_id") == agent_id:
        return True
    return str(block.get("role") or "") == "agent"


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _stable_projection_segment(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "-", text).strip("-")
    return (text or fallback)[:160]


def projection_namespace(
    projection: dict[str, Any], source_id: str | None
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    payload_source = str(projection.get("source_id") or "").strip()
    requested = str(source_id or "").strip()
    if requested and payload_source and requested != payload_source:
        warnings.append(
            f"projection source_id {payload_source!r} does not match requested source_id {requested!r}"
        )
    raw = (
        requested
        or payload_source
        or str(projection.get("schema_version") or "projection")
    )
    return _stable_projection_segment(raw, fallback="projection"), warnings


def _projection_payload_goal_id(projection: dict[str, Any]) -> str:
    for value in (
        projection.get("goal_id"),
        _as_mapping(projection.get("selected")).get("goal_id"),
        _as_mapping(projection.get("goal")).get("id"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _projection_item_text(item: dict[str, Any], *, fallback: str) -> str:
    for key in ("title", "text", "task", "summary", "recommended_action", "reason"):
        value = _compact_text(item.get(key), limit=260)
        if value:
            return value
    return fallback


def _projection_lifecycle_payload(item: dict[str, Any]) -> dict[str, Any]:
    lifecycle = item.get("row_lifecycle")
    if isinstance(lifecycle, Mapping):
        return dict(lifecycle)
    if isinstance(lifecycle, str) and lifecycle.strip():
        return {"state": lifecycle.strip()}
    return {}


def projection_lifecycle_state(item: dict[str, Any]) -> str:
    lifecycle = _projection_lifecycle_payload(item)
    return (
        str(
            lifecycle.get("state")
            or lifecycle.get("status")
            or item.get("row_lifecycle_state")
            or ""
        )
        .strip()
        .lower()
    )


def _projection_item_status(item: dict[str, Any]) -> str:
    raw = str(item.get("status") or item.get("state") or "").strip()
    lifecycle_state = projection_lifecycle_state(item)
    if (
        item.get("done") is True
        or normalize_todo_status(raw) == TODO_STATUS_DONE
        or raw.lower()
        in {
            "closed",
            "complete",
            "completed",
            "resolved",
            "superseded",
            "migrated",
            "retired",
        }
        or lifecycle_state in {"superseded", "migrated", "retired"}
    ):
        return TODO_STATUS_DONE
    if normalize_todo_status(raw) == TODO_STATUS_BLOCKED or raw.lower() in {
        "error",
        "failed",
    }:
        return TODO_STATUS_BLOCKED
    if normalize_todo_status(raw) == TODO_STATUS_DEFERRED:
        return TODO_STATUS_DEFERRED
    return normalize_todo_status(raw) or TODO_STATUS_OPEN


def projection_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [
            _compact_text(item, limit=220)
            for item in value
            if _compact_text(item, limit=220)
        ]
    text = _compact_text(value, limit=220)
    return [text] if text else []


def projection_lifecycle_value(item: dict[str, Any], key: str) -> Any:
    lifecycle = _projection_lifecycle_payload(item)
    if key in lifecycle:
        return lifecycle.get(key)
    return item.get(key)


def projection_lifecycle_parts(item: dict[str, Any], *, source_id: str) -> list[str]:
    parts: list[str] = []
    state = projection_lifecycle_state(item)
    if state:
        parts.append(f"row_lifecycle={state}")
    for key in ("source_id", "source_row_id", "target_row_id", "migration_audit_id"):
        value = projection_lifecycle_value(item, key)
        text = _compact_text(value, limit=220)
        if text:
            parts.append(f"{key}={text}")
    for key in ("supersedes", "superseded_by"):
        values = projection_text_list(projection_lifecycle_value(item, key))
        if values:
            parts.append(f"{key}={','.join(values)}")
    if state and not any(part.startswith("source_id=") for part in parts):
        parts.append(f"source_id={source_id}")
    return parts


def _projection_lifecycle_default_text(item: dict[str, Any], *, index: int) -> str:
    state = projection_lifecycle_state(item) or "updated"
    supersedes = (
        ", ".join(projection_text_list(projection_lifecycle_value(item, "supersedes")))
        or "previous row"
    )
    superseded_by = (
        ", ".join(
            projection_text_list(projection_lifecycle_value(item, "superseded_by"))
        )
        or "current projection"
    )
    return f"[P2] Projection row lifecycle {index}: {state} {supersedes} -> {superseded_by}"


def _projection_lifecycle_events(projection: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for key in ("row_lifecycle_events", "projection_row_lifecycle", "migration_audit"):
        value = projection.get(key)
        if isinstance(value, Mapping):
            if isinstance(value.get("events"), list):
                events.extend(_as_mapping_list(value.get("events")))
            else:
                events.append(dict(value))
        elif isinstance(value, list):
            events.extend(_as_mapping_list(value))
    return events


def _projection_item_priority(item: dict[str, Any], text: str) -> str:
    from ....control_plane.todos.todo_semantics import todo_priority_label

    return todo_priority_label({**item, "text": text}) or "P2"


def _projection_todo_candidates(summary: Any) -> list[dict[str, Any]]:
    if isinstance(summary, list):
        source_groups = [summary]
    elif isinstance(summary, Mapping):
        source_groups = [
            summary.get(key)
            for key in (
                "first_open_items",
                "first_executable_items",
                "executable_backlog_items",
                "backlog_items",
                "claimed_open_items",
                "deferred_resume_candidates",
                "deferred_items",
                "items",
            )
        ]
    else:
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in source_groups:
        for item in _as_mapping_list(group):
            identity = str(
                item.get("todo_id")
                or item.get("gate_id")
                or item.get("title")
                or item.get("text")
                or ""
            )
            if not identity:
                identity = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if identity in seen:
                continue
            seen.add(identity)
            items.append(item)
    return items


def _projection_project_asset(
    projection: dict[str, Any], goal_id: str | None
) -> dict[str, Any]:
    project_asset = _as_mapping(projection.get("project_asset"))
    if project_asset:
        return project_asset
    queue = _as_mapping(projection.get("attention_queue"))
    for item in _as_mapping_list(queue.get("items")):
        item_goal_id = str(item.get("goal_id") or "").strip()
        if goal_id and item_goal_id != goal_id:
            continue
        asset = _as_mapping(item.get("project_asset"))
        if asset:
            return asset
    return {}


def _projection_row(
    *,
    source_id: str,
    goal_id: str,
    kind: str,
    identity: Any,
    role: str,
    item: dict[str, Any],
    fallback_text: str,
    projection_agent_id: str | None = None,
) -> dict[str, Any]:
    text = (
        _compact_text(item["next_step"], limit=260)
        if kind == "next_action" and item.get("next_step")
        else _projection_item_text(item, fallback=fallback_text)
    )
    task_class = normalize_explicit_todo_task_class(item.get("task_class")) or (
        TODO_TASK_CLASS_USER_GATE if role == "user" else TODO_TASK_CLASS_ADVANCEMENT
    )
    todo_id = (
        "projection:"
        f"{source_id}:"
        f"{_stable_projection_segment(kind, fallback='item')}:"
        f"{_stable_projection_segment(identity, fallback='row')}"
    )
    return {
        **item,
        "goal_id": goal_id,
        "role": role,
        "status": _projection_item_status(item),
        "text": text,
        "todo_id": todo_id,
        "original_todo_id": str(item.get("todo_id") or item.get("gate_id") or ""),
        "task_class": task_class,
        "action_kind": str(item.get("action_kind") or kind).strip() or kind,
        "priority": _projection_item_priority(item, text),
        "source_id": source_id,
        "projection_agent_id": projection_agent_id,
    }


def projection_rows_from_payload(
    projection: dict[str, Any],
    *,
    goal_id: str | None,
    agent_id: str | None,
    source_id: str,
    include_done: bool,
    limit: int,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    payload_goal_id = _projection_payload_goal_id(projection)
    resolved_goal_id = str(goal_id or payload_goal_id or "loopx-projection").strip()
    warnings: list[str] = []
    if goal_id and payload_goal_id and goal_id != payload_goal_id:
        warnings.append(
            f"payload goal_id {payload_goal_id!r} does not match requested goal_id {goal_id!r}"
        )
        return resolved_goal_id, [], warnings

    payload_agent_id = str(
        _as_mapping(projection.get("agent_identity")).get("agent_id") or ""
    ).strip()
    rows: list[dict[str, Any]] = []

    def add_row(row: dict[str, Any]) -> None:
        if len(rows) >= limit:
            return
        if (
            row.get("status") == TODO_STATUS_DONE
            and not include_done
            and not row.get("_include_done_by_default")
        ):
            return
        if agent_id and not _projection_matches_agent_scope(row, agent_id):
            return
        rows.append(row)

    project_asset = _projection_project_asset(projection, resolved_goal_id)
    for role in ("user", "agent"):
        group = projection.get(f"{role}_todos")
        if group is None and project_asset:
            group = project_asset.get(f"{role}_todos")
        for index, item in enumerate(_projection_todo_candidates(group), start=1):
            identity = (
                item.get("todo_id") or item.get("title") or item.get("text") or index
            )
            add_row(
                _projection_row(
                    source_id=source_id,
                    goal_id=resolved_goal_id,
                    kind=f"{role}_todo",
                    identity=identity,
                    role=role,
                    item=item,
                    fallback_text=f"{role} todo {index}",
                )
            )

    for index, gate in enumerate(
        _as_mapping_list(projection.get("open_gates")), start=1
    ):
        identity = gate.get("gate_id") or gate.get("id") or index
        gate_text = (
            gate.get("title")
            or gate.get("text")
            or gate.get("kind")
            or "Open user gate"
        )
        add_row(
            _projection_row(
                source_id=source_id,
                goal_id=resolved_goal_id,
                kind="open_gate",
                identity=identity,
                role="user",
                item={
                    **gate,
                    "title": gate_text,
                    "status": TODO_STATUS_OPEN,
                    "task_class": TODO_TASK_CLASS_USER_GATE,
                    "action_kind": "projection_user_gate",
                    "blocks_agent": gate.get("blocks_agent") or gate.get("blocks"),
                    "evidence": gate.get("message") or gate.get("reason"),
                },
                fallback_text="Open user gate",
            )
        )

    for index, outcome in enumerate(
        _as_mapping_list(projection.get("issue_fix_outcomes")), start=1
    ):
        identity = outcome.get("outcome_id") or outcome.get("issue_ref") or index
        add_row(
            _projection_row(
                source_id=source_id,
                goal_id=resolved_goal_id,
                kind="issue_fix_outcome",
                identity=identity,
                role="agent",
                item={
                    **outcome,
                    "action_kind": "issue_fix_outcome",
                    "task_class": outcome.get("task_class") or "continuous_monitor",
                    "_include_done_by_default": True,
                },
                fallback_text=f"Issue-fix outcome {index}",
                projection_agent_id=payload_agent_id or agent_id,
            )
        )

    for index, metric in enumerate(
        _as_mapping_list(projection.get("impact_rows")), start=1
    ):
        identity = metric.get("metric_id") or index
        current = metric.get("current")
        delta = metric.get("delta")
        current_text = "unavailable" if current is None else str(current)
        delta_text = (
            f" ({delta:+g})"
            if isinstance(delta, (int, float)) and not isinstance(delta, bool)
            else ""
        )
        add_row(
            _projection_row(
                source_id=source_id,
                goal_id=resolved_goal_id,
                kind="issue_fix_metric",
                identity=identity,
                role="agent",
                item={
                    **metric,
                    "title": f"{metric.get('metric') or identity}: {current_text}{delta_text}",
                    "action_kind": "issue_fix_metric",
                    "task_class": "continuous_monitor",
                    "priority": "P2",
                    "status": "open",
                    "evidence": metric.get("missing_reason")
                    or metric.get("source_url"),
                },
                fallback_text=f"Issue-fix impact metric {index}",
                projection_agent_id=payload_agent_id or agent_id,
            )
        )

    frontier = _as_mapping(
        projection.get("goal_frontier_projection")
        or project_asset.get("goal_frontier_projection")
    )
    audit = _as_mapping(
        projection.get("vision_continuation_audit")
        or frontier.get("vision_continuation_audit")
    )
    for diagnostic in _as_mapping_list(audit.get("outcome_checkpoint_diagnostics")):
        checks = _as_mapping(diagnostic.get("component_checks"))
        check_summary = ", ".join(
            f"{key}={'pass' if passed else 'fail'}" for key, passed in checks.items()
        )
        add_row(_projection_row(
            source_id=source_id, goal_id=resolved_goal_id,
            kind="outcome_checkpoint", identity=diagnostic.get("reason_code"),
            role="agent", projection_agent_id=audit.get("agent_id") or payload_agent_id,
            item={
                **diagnostic,
                "claimed_by": audit.get("agent_id") or payload_agent_id,
                "text": diagnostic.get("resolution_hint"),
                "evidence": f"{diagnostic.get('reason_code')}: {check_summary}",
                "status": "open", "priority": "P0",
            },
            fallback_text="Resolve the final-outcome checkpoint gap",
        ))

    next_action = _as_mapping(projection.get("agent_lane_next_action"))
    if not next_action and projection.get("next_action"):
        next_action = {
            "text": projection.get("next_action"),
            "action_kind": "projection_next_action",
            "task_class": TODO_TASK_CLASS_ADVANCEMENT,
        }
    if next_action:
        identity = next_action.get("todo_id") or "next_action"
        add_row(
            _projection_row(
                source_id=source_id,
                goal_id=resolved_goal_id,
                kind="next_action",
                identity=identity,
                role="agent",
                item=next_action,
                fallback_text="Projected next action",
                projection_agent_id=payload_agent_id or agent_id,
            )
        )

    capability_gate = _as_mapping(projection.get("capability_gate"))
    for index, item in enumerate(
        _as_mapping_list(capability_gate.get("runnable_candidates")), start=1
    ):
        identity = item.get("todo_id") or item.get("title") or item.get("text") or index
        add_row(
            _projection_row(
                source_id=source_id,
                goal_id=resolved_goal_id,
                kind="runnable_candidate",
                identity=identity,
                role="agent",
                item={
                    **item,
                    "action_kind": item.get("action_kind")
                    or "projection_runnable_candidate",
                    "task_class": item.get("task_class") or TODO_TASK_CLASS_ADVANCEMENT,
                },
                fallback_text=f"Runnable candidate {index}",
                projection_agent_id=payload_agent_id,
            )
        )

    for index, event in enumerate(_projection_lifecycle_events(projection), start=1):
        identity = (
            event.get("row_id")
            or event.get("todo_id")
            or event.get("source_row_id")
            or event.get("supersedes")
            or index
        )
        add_row(
            _projection_row(
                source_id=source_id,
                goal_id=resolved_goal_id,
                kind="row_lifecycle",
                identity=identity,
                role=str(event.get("role") or "agent"),
                item={
                    **event,
                    "title": event.get("title")
                    or event.get("text")
                    or _projection_lifecycle_default_text(event, index=index),
                    "action_kind": event.get("action_kind")
                    or "projection_row_lifecycle",
                    "task_class": event.get("task_class") or "continuous_monitor",
                    "claimed_by": event.get("claimed_by")
                    or event.get("agent_id")
                    or payload_agent_id,
                    "_include_done_by_default": True,
                },
                fallback_text=_projection_lifecycle_default_text(event, index=index),
                projection_agent_id=str(
                    event.get("agent_id") or payload_agent_id or agent_id or ""
                ).strip()
                or None,
            )
        )

    interaction = _as_mapping(projection.get("interaction_contract"))
    user_channel = _as_mapping(interaction.get("user_channel"))
    if user_channel.get("action_required") is True:
        add_row(
            _projection_row(
                source_id=source_id,
                goal_id=resolved_goal_id,
                kind="interaction_gate",
                identity="user_channel",
                role="user",
                item={
                    "title": user_channel.get("reason")
                    or "User channel action required",
                    "status": TODO_STATUS_OPEN,
                    "task_class": TODO_TASK_CLASS_USER_GATE,
                    "action_kind": "projection_interaction_gate",
                    "blocks_agent": agent_id or payload_agent_id,
                    "evidence": user_channel.get("payload")
                    or user_channel.get("reason"),
                },
                fallback_text="User channel action required",
            )
        )

    return resolved_goal_id, rows[:limit], warnings
