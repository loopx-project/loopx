from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .control_plane.projection_envelope_facts import (
    render_projection_envelope_markdown,
    seal_projection_envelope,
    source_fact,
)
from .control_plane.runtime.time import now_utc_iso
from .control_plane.todos.decision_scope import todo_gate_relations
from .control_plane.todos.user_gate import open_user_gate_todo_items
from .presentation.markdown import as_dict, as_list
from .presentation.public_safety import public_safe_boundary, redact_public_text
from .quota import build_quota_should_run
from .status import collect_status


COMMAND = "/loopx-global-todos"
SCHEMA_VERSION = "global_manager_command_response_v0"
SOURCE_WARNING_LIMIT = 8
SOURCE_SURFACES = [
    "status attention queue",
    "quota should-run summaries",
    "active-state todo projections",
    "compact run-history projection consumed by status",
]
_CANDIDATE_LIST_KEYS = (
    "first_open_items",
    "first_executable_items",
    "items",
    "open_items",
    "deferred_items",
    "deferred_resume_candidates",
    "blocker_items",
    "resume_blocked_items",
)
_BLOCKER_SOURCE_KEYS = {"blocker_items", "resume_blocked_items"}
_READINESS_DISPLAY_RANK = {"runnable": 0, "deferred_ready": 1, "blocked": 2}
_READINESS_SAFETY_RANK = {"blocked": 0, "deferred_ready": 1, "runnable": 2}


def _redact_text(value: object, *, limit: int = 260) -> str:
    return redact_public_text(
        value,
        limit=limit,
        replacements={"/loop-global-todos": COMMAND},
        truncation_marker="…",
    )


def _request() -> dict[str, Any]:
    return {
        "schema_version": "global_manager_command_request_v0",
        "command": COMMAND,
        "legacy_aliases": ["/loop-global-todos"],
        "cli_command": "loopx global-todos",
        "include": ["runnable", "blocked", "deferred_ready", "review"],
        "privacy_mode": "public_safe_summary",
        "dry_run": True,
    }


def build_global_todos_error(
    error: object,
    *,
    status_payload: dict[str, Any] | None = None,
    status_read_at: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "request": _request(),
        "generated_at": now_utc_iso(),
        "error": _redact_text(error),
        "omissions": [
            "Raw/private failure details and local paths were intentionally omitted."
        ],
        "projection_envelope": _todos_envelope(
            status_payload or {}, status_read_at=status_read_at,
            quota_read_at=None, quota_evaluated=0, quota_unavailable=0,
            goal_scan_omitted=0, shown_count=0, available_count=0,
        ),
        "boundary": public_safe_boundary(),
    }


def _warning(
    reason_code: str,
    *,
    goal_id: str,
    todo_id: str | None = None,
    detail: object | None = None,
) -> dict[str, Any]:
    warning: dict[str, Any] = {
        "reason_code": _redact_text(reason_code, limit=120),
        "goal_id": _redact_text(goal_id, limit=120),
    }
    if todo_id:
        warning["todo_id"] = _redact_text(todo_id, limit=120)
    if detail:
        warning["detail"] = _redact_text(detail)
    return warning


def _quota_for_goal(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    try:
        payload = build_quota_should_run(
            status_payload,
            goal_id=goal_id,
            agent_id=agent_id,
        )
    except Exception as exc:
        warnings.append(
            _warning(
                "quota_unavailable",
                goal_id=goal_id,
                detail=exc,
            )
        )
        return None
    if payload.get("ok") is False:
        warnings.append(
            _warning(
                "quota_unavailable",
                goal_id=goal_id,
                detail=payload.get("error") or payload.get("reason"),
            )
        )
        return None
    return payload


def _quota_matches_agent(payload: dict[str, Any], *, agent_id: str | None) -> bool:
    if not agent_id:
        return True
    if payload.get("ok") is not True:
        return False
    identity = as_dict(payload.get("agent_identity"))
    return str(identity.get("agent_id") or "").strip() == agent_id


def _candidate_sources(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    sources: list[tuple[str, dict[str, Any]]] = []

    def add(source: str, value: object) -> None:
        if isinstance(value, dict):
            sources.append((source, value))

    add("selected_todo", payload.get("selected_todo"))
    add("agent_lane_next_action", payload.get("agent_lane_next_action"))
    for container_key in ("agent_todo_summary", "agent_scope_frontier"):
        container = as_dict(payload.get(container_key))
        for list_key in _CANDIDATE_LIST_KEYS:
            for item in as_list(container.get(list_key)):
                add(f"{container_key}.{list_key}", item)
    return sources


def _candidate_records(
    payload: dict[str, Any],
    *,
    goal_id: str,
    warnings: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for source, item in _candidate_sources(payload):
        todo_id = str(item.get("todo_id") or "").strip()
        if not todo_id:
            warnings.append(
                _warning(
                    "todo_identity_missing",
                    goal_id=goal_id,
                    detail=f"Structured todo candidate from {source} has no todo_id.",
                )
            )
            continue
        record = records.setdefault(
            todo_id,
            {"item": dict(item), "sources": set(), "statuses": set()},
        )
        record_sources = record["sources"]
        if isinstance(record_sources, set):
            record_sources.add(source)
        status = str(item.get("status") or "").strip().lower()
        record_statuses = record["statuses"]
        if status and isinstance(record_statuses, set):
            record_statuses.add(status)
        merged = as_dict(record.get("item"))
        for key, value in item.items():
            if merged.get(key) in (None, "", []) and value not in (None, "", []):
                merged[key] = value
    return records


def _verified_blocked_ids(
    payload: dict[str, Any],
    records: dict[str, dict[str, Any]],
) -> set[str]:
    blocked_ids: set[str] = set()
    for todo_id, record in records.items():
        sources = record.get("sources")
        if isinstance(sources, set) and any(
            source.rsplit(".", 1)[-1] in _BLOCKER_SOURCE_KEYS
            for source in sources
        ):
            blocked_ids.add(todo_id)

    user_summary = as_dict(payload.get("user_todo_summary"))
    gates = open_user_gate_todo_items(user_summary)
    relations = todo_gate_relations(gates, [as_dict(record.get("item")) for record in records.values()])
    for gate, gate_relations in zip(gates, relations, strict=True):
        exact_todo_id = str(gate.get("unblocks_todo_id") or "").strip()
        if exact_todo_id in records:
            blocked_ids.add(exact_todo_id)
        for todo_id, relation in zip(records, gate_relations, strict=True):
            if relation and relation.get("state") in {
                "gate_targets_todo",
                "gate_covers_action",
            }:
                blocked_ids.add(todo_id)
    return blocked_ids


def _deferred_ready_ids(records: dict[str, dict[str, Any]]) -> set[str]:
    ready_ids: set[str] = set()
    for todo_id, record in records.items():
        item = as_dict(record.get("item"))
        statuses = record.get("statuses")
        sources = record.get("sources")
        structured_candidate = isinstance(sources, set) and any(
            source.endswith(".deferred_resume_candidates") for source in sources
        )
        explicitly_ready = (
            isinstance(statuses, set)
            and "deferred" in statuses
            and item.get("resume_ready") is True
        )
        if structured_candidate or explicitly_ready:
            ready_ids.add(todo_id)
    return ready_ids


def _work_kind(item: dict[str, Any]) -> str:
    action_kind = str(item.get("action_kind") or "").strip().lower()
    tokens = {token for token in action_kind.split("_") if token}
    return "review" if tokens & {"review", "reviewer"} else "ordinary"


def _normalize_todo(
    item: dict[str, Any],
    *,
    goal_id: str,
    readiness: str,
    recommended_action: object,
) -> dict[str, Any]:
    return {
        "todo_id": _redact_text(item.get("todo_id"), limit=120),
        "goal_id": _redact_text(goal_id, limit=120),
        "role": _redact_text(item.get("role") or "agent", limit=80),
        "status": _redact_text(item.get("status") or "open", limit=80),
        "priority": _redact_text(item.get("priority"), limit=40),
        "title": _redact_text(item.get("title") or item.get("text")),
        "claimed_by": _redact_text(item.get("claimed_by"), limit=120) or None,
        "action_kind": _redact_text(item.get("action_kind"), limit=120),
        "readiness": readiness,
        "work_kind": _work_kind(item),
        "next_safe_action": _redact_text(recommended_action),
    }


def _classify_goal_todos(
    payload: dict[str, Any],
    *,
    goal_id: str,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records = _candidate_records(payload, goal_id=goal_id, warnings=warnings)
    blocked_ids = _verified_blocked_ids(payload, records)
    deferred_ready_ids = _deferred_ready_ids(records)
    selected_todo_id = str(
        as_dict(payload.get("selected_todo")).get("todo_id") or ""
    ).strip()
    normal_delivery_allowed = payload.get("normal_delivery_allowed") is True
    todos: list[dict[str, Any]] = []

    for todo_id, record in records.items():
        statuses = record.get("statuses")
        signals: set[str] = set()
        if todo_id in blocked_ids or (
            isinstance(statuses, set) and "blocked" in statuses
        ):
            signals.add("blocked")
        if todo_id in deferred_ready_ids:
            signals.add("deferred_ready")
        if todo_id == selected_todo_id and normal_delivery_allowed:
            signals.add("runnable")
        if not signals:
            continue
        if len(signals) > 1:
            warnings.append(
                _warning(
                    "conflicting_readiness_projection",
                    goal_id=goal_id,
                    todo_id=todo_id,
                    detail="Multiple structured readiness signals were projected.",
                )
            )
        readiness = min(signals, key=_READINESS_SAFETY_RANK.__getitem__)
        todos.append(
            _normalize_todo(
                as_dict(record.get("item")),
                goal_id=goal_id,
                readiness=readiness,
                recommended_action=payload.get("recommended_action"),
            )
        )
    return todos


def _priority_rank(value: object) -> int:
    match = re.fullmatch(r"[Pp](\d+)", str(value or "").strip())
    return int(match.group(1)) if match else 1_000_000


def _todo_sort_key(todo: dict[str, Any]) -> tuple[int, int, str, str]:
    readiness = str(todo.get("readiness") or "")
    return (
        _READINESS_DISPLAY_RANK.get(readiness, 3),
        _priority_rank(todo.get("priority")),
        str(todo.get("goal_id") or ""),
        str(todo.get("todo_id") or ""),
    )


def _deduplicate_todos(
    todos: list[dict[str, Any]],
    *,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    for todo in todos:
        key = (str(todo.get("goal_id") or ""), str(todo.get("todo_id") or ""))
        existing = deduplicated.get(key)
        if existing is None:
            deduplicated[key] = todo
            continue
        existing_readiness = str(existing.get("readiness") or "")
        incoming_readiness = str(todo.get("readiness") or "")
        if existing_readiness != incoming_readiness:
            warnings.append(
                _warning(
                    "conflicting_readiness_projection",
                    goal_id=key[0],
                    todo_id=key[1],
                    detail="Duplicate todo rows projected different readiness states.",
                )
            )
            if _READINESS_SAFETY_RANK.get(incoming_readiness, 3) < (
                _READINESS_SAFETY_RANK.get(existing_readiness, 3)
            ):
                deduplicated[key] = todo
        if todo.get("work_kind") == "review":
            deduplicated[key]["work_kind"] = "review"
    return list(deduplicated.values())


def _queue_goal_ids(
    status_payload: dict[str, Any],
    *,
    scan_limit: int,
) -> tuple[list[str], int]:
    queue = as_dict(status_payload.get("attention_queue"))
    unique_goal_ids: list[str] = []
    seen: set[str] = set()
    for item in as_list(queue.get("items")):
        if not isinstance(item, dict):
            continue
        goal_id = str(item.get("goal_id") or "").strip()
        if not goal_id or goal_id in seen:
            continue
        seen.add(goal_id)
        unique_goal_ids.append(goal_id)
    try:
        reported_count = max(0, int(queue.get("item_count") or 0))
    except (TypeError, ValueError):
        reported_count = 0
    available_count = max(len(unique_goal_ids), reported_count)
    return unique_goal_ids[:scan_limit], available_count


def _todos_envelope(
    status_payload: dict[str, Any],
    *,
    status_read_at: str | None,
    quota_read_at: str | None,
    quota_evaluated: int,
    quota_unavailable: int,
    goal_scan_omitted: int,
    shown_count: int,
    available_count: int,
) -> dict[str, Any]:
    global_registry = as_dict(status_payload.get("global_registry"))
    status_envelope = as_dict(status_payload.get("projection_envelope"))
    global_source = next(
        (row for row in as_list(status_envelope.get("sources"))
         if isinstance(row, dict) and row.get("source_id") == "global_registry"),
        {},
    )
    available = global_registry.get("available") is True
    count = global_registry.get("global_goal_count")
    expected = count if available and isinstance(count, int) and not isinstance(count, bool) else None
    excluded = int(global_registry.get("current_registry_excluded_goal_count") or 0) if available else 0
    omitted = []
    if excluded:
        omitted.append({
            "reason": "outside_current_registry", "count": excluded,
            "refs": [str(ref) for ref in as_list(global_registry.get("current_registry_excluded_goal_ids"))[:8]],
        })
    if goal_scan_omitted:
        omitted.append({"reason": "attention_goals_not_scanned", "count": goal_scan_omitted})
    if status_payload and status_payload.get("ok") is not True:
        omitted.append({"reason": "status_unavailable", "count": 1})
    queue = status_payload.get("attention_queue")
    queue_read = isinstance(queue, dict) and isinstance(queue.get("items"), list)
    return seal_projection_envelope(
        projection="global_todos",
        observed_at=now_utc_iso(),
        sources=[
            source_fact(
                "global_registry",
                read_status=(
                    str(global_source.get("read_status") or "read") if available
                    else str(global_registry.get("read_status") or ("missing" if status_read_at else "not_read"))
                ),
                last_read_at=global_source.get("last_read_at") or (status_read_at if available else None),
                item_count=expected,
            ),
            source_fact(
                "goal_quota",
                read_status="read" if quota_read_at else "not_read",
                last_read_at=quota_read_at,
                item_count=quota_evaluated,
                unreadable_count=quota_unavailable,
            ),
            source_fact(
                "attention_queue",
                read_status="read" if queue_read else "not_read",
                last_read_at=status_read_at if queue_read else None,
                item_count=len(queue["items"]) if queue_read else None,
            ),
            *([] if status_envelope else [source_fact("status", read_status="not_read")]),
        ],
        coverage={
            "scope": "global", "expected_count": expected,
            "included_count": max(0, expected - excluded - goal_scan_omitted) if expected is not None else 0,
            "omitted": omitted,
            "shown_count": shown_count, "available_count": available_count,
        },
        upstream=[status_envelope] if status_envelope else [],
    )


def _groups(todos: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "runnable": [todo for todo in todos if todo.get("readiness") == "runnable"],
        "deferred_ready": [
            todo for todo in todos if todo.get("readiness") == "deferred_ready"
        ],
        "blocked": [todo for todo in todos if todo.get("readiness") == "blocked"],
        "review": [todo for todo in todos if todo.get("work_kind") == "review"],
    }


def build_global_todos(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    scan_roots: list[Path],
    agent_id: str | None,
    limit: int,
) -> dict[str, Any]:
    normalized_limit = max(1, limit)
    scan_limit = max(normalized_limit * 4, 40)
    status_payload = collect_status(
        registry_path=registry_path,
        runtime_root_override=runtime_root_override,
        scan_roots=scan_roots,
        limit=scan_limit,
    )
    status_read_at = now_utc_iso()
    if status_payload.get("ok") is not True:
        return build_global_todos_error(
            "Global status source unavailable.",
            status_payload=status_payload, status_read_at=status_read_at,
        )

    goal_ids, available_goal_count = _queue_goal_ids(
        status_payload,
        scan_limit=scan_limit,
    )
    warnings: list[dict[str, Any]] = []
    classified: list[dict[str, Any]] = []
    quota_read_at = now_utc_iso()
    quota_unavailable = 0
    for goal_id in goal_ids:
        quota_payload = _quota_for_goal(
            status_payload,
            goal_id=goal_id,
            agent_id=agent_id,
            warnings=warnings,
        )
        quota_unavailable += int(quota_payload is None)
        if quota_payload is None or not _quota_matches_agent(
            quota_payload,
            agent_id=agent_id,
        ):
            continue
        classified.extend(
            _classify_goal_todos(
                quota_payload,
                goal_id=goal_id,
                warnings=warnings,
            )
        )

    matched = _deduplicate_todos(classified, warnings=warnings)
    matched.sort(key=_todo_sort_key)
    retained = matched[:normalized_limit]
    full_groups = _groups(matched)
    retained_groups = _groups(retained)
    matched_count = len(matched)
    returned_count = len(retained)
    warning_count = len(warnings)

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "request": _request(),
        "generated_at": now_utc_iso(),
        "summary": {
            "headline": (
                f"{returned_count} of {matched_count} classified todos returned."
            ),
            "matched_todo_count": matched_count,
            "returned_todo_count": returned_count,
            "runnable_count": len(full_groups["runnable"]),
            "blocked_count": len(full_groups["blocked"]),
            "deferred_ready_count": len(full_groups["deferred_ready"]),
            "review_count": len(full_groups["review"]),
            "source_warning_count": warning_count,
            "source_surfaces": SOURCE_SURFACES,
            "truncated": matched_count > returned_count,
            "goal_scan_limit": scan_limit,
            "goal_scan_truncated": available_goal_count > len(goal_ids),
        },
        "groups": retained_groups,
        "todos": retained,
        "source_warnings": warnings[:SOURCE_WARNING_LIMIT],
        "source_warnings_truncated": warning_count > SOURCE_WARNING_LIMIT,
        "projection_envelope": _todos_envelope(
            status_payload, status_read_at=status_read_at,
            quota_read_at=quota_read_at, quota_evaluated=len(goal_ids),
            quota_unavailable=quota_unavailable,
            goal_scan_omitted=max(0, available_goal_count - len(goal_ids)),
            shown_count=returned_count, available_count=matched_count,
        ),
        "omissions": [
            (
                "Raw logs, raw transcripts, connector payloads, credential values, "
                "local paths, and private source bodies were intentionally omitted."
            ),
            "Recent progress, gates, and unrelated risks are outside this focused command.",
        ],
        "boundary": public_safe_boundary(),
    }


def _render_todo_line(todo: dict[str, Any]) -> str:
    owner = todo.get("claimed_by") or todo.get("role") or "unclaimed"
    line = (
        f"- goal=`{_redact_text(todo.get('goal_id'), limit=120)}` "
        f"todo=`{_redact_text(todo.get('todo_id'), limit=120)}` "
        f"priority=`{_redact_text(todo.get('priority'), limit=40) or 'unknown'}` "
        f"owner=`{_redact_text(owner, limit=120)}`: "
        f"{_redact_text(todo.get('title')) or 'Untitled todo.'}"
    )
    next_action = _redact_text(todo.get("next_safe_action"))
    return f"{line} Next: {next_action}" if next_action else line


def render_global_todos_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        lines = [
            "# LoopX Global Todos Unavailable",
            "",
            "- ok: `False`",
            f"- error: {_redact_text(payload.get('error'))}",
            *render_projection_envelope_markdown(payload.get("projection_envelope")),
        ]
        omissions = [
            _redact_text(item)
            for item in as_list(payload.get("omissions"))
            if _redact_text(item)
        ]
        if omissions:
            lines.extend(["", "## Omissions"])
            lines.extend(f"- {item}" for item in omissions)
        return "\n".join(lines)

    summary = as_dict(payload.get("summary"))
    request = as_dict(payload.get("request"))
    lines = [
        "# LoopX Global Todos",
        "",
        f"- command: `{_redact_text(request.get('command'), limit=120)}`",
        f"- matched: `{summary.get('matched_todo_count')}`",
        f"- returned: `{summary.get('returned_todo_count')}`",
        f"- truncated: `{bool(summary.get('truncated'))}`",
        f"- goal_scan_truncated: `{bool(summary.get('goal_scan_truncated'))}`",
        *render_projection_envelope_markdown(payload.get("projection_envelope")),
        "",
        "Review is an overlapping work-kind facet of the readiness groups.",
    ]
    groups = as_dict(payload.get("groups"))
    for key, heading in (
        ("runnable", "Runnable"),
        ("deferred_ready", "Deferred Ready"),
        ("blocked", "Blocked"),
        ("review", "Review"),
    ):
        lines.extend(["", f"## {heading}"])
        items = [item for item in as_list(groups.get(key)) if isinstance(item, dict)]
        if not items:
            lines.append("- None.")
        else:
            lines.extend(_render_todo_line(item) for item in items)

    warnings = [
        item
        for item in as_list(payload.get("source_warnings"))
        if isinstance(item, dict)
    ]
    if warnings:
        lines.extend(["", "## Source Warnings"])
        for warning in warnings:
            detail = _redact_text(warning.get("detail"))
            line = (
                f"- `{_redact_text(warning.get('reason_code'), limit=120)}` "
                f"goal=`{_redact_text(warning.get('goal_id'), limit=120)}`"
            )
            if warning.get("todo_id"):
                line += (
                    f" todo=`{_redact_text(warning.get('todo_id'), limit=120)}`"
                )
            lines.append(f"{line}: {detail}" if detail else line)
    lines.extend(
        [
            "",
            "## Boundary",
            "- Raw/private material omitted; local paths are not recorded.",
        ]
    )
    return "\n".join(lines)
