from __future__ import annotations

import json
from typing import Any, Callable

from ..runtime.time import now_utc_iso
from ..effect_runtime import MAX_REQUEST_BYTES, effect_runtime_result
from ..coordination.coordination_state_contract import TODO_CANONICAL_READ_RECORD_FIELDS
from ..todos.summary_item import TODO_PLANNING_SOURCE_KEYS


_PUBLIC_TEXT_CAPS = {
    "todo_id": (120,),
    "title": (160,),
    "text": (160, 180),
    "claimed_by": (80,),
    "task_domain": (80,),
    "last_actor_agent_id": (80,),
    "from_agent": (80,),
    "to_agent": (80,),
    "status": (80,),
    "evidence": (180,),
    "note": (180,),
    "successor_todo_ids": (120,),
    "unblocks_todo_id": (240,),
    "superseded_by": (240,),
    "resume_when": (240,),
    "run_id": (120,),
    "generated_at": (80, 120),
    "classification": (120, 160),
    "recommended_action": (160,),
    "delivery_outcome": (160,),
    "delivery_batch_scale": (160,),
    "schema_version": (120,),
    "kind": (120,),
}


def _encode_public_text(
    value: Any,
    texts: dict[str, dict[str, str | None]],
    public_safe_compact_text: Callable[..., str | None],
    caps: tuple[int, ...] = (80, 120, 160, 180, 240),
) -> Any:
    """Encode source text once; native composition consumes these public facts."""
    if isinstance(value, dict):
        return {
            key: _encode_public_text(
                child, texts, public_safe_compact_text, _PUBLIC_TEXT_CAPS.get(key, ())
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            _encode_public_text(child, texts, public_safe_compact_text, caps) for child in value
        ]
    if not isinstance(value, str):
        # Do not stringify opaque metadata before native node admission.
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return {"unsupported_public_text": True}
    if value in texts:
        for cap in caps:
            if str(cap) not in texts[value]:
                texts[value][str(cap)] = public_safe_compact_text(value, limit=cap)
        return value
    public = public_safe_compact_text(value, limit=len(value) + 1)
    token = value if not value.strip() or public else f"redacted:{len(texts)}"
    texts[token] = {str(cap): public_safe_compact_text(value, limit=cap) for cap in caps}
    return token


def build_task_graph_projection(
    item: dict[str, Any],
    *,
    goal: dict[str, Any],
    goal_latest_runs: list[dict[str, Any]] | None = None,
    public_safe_compact_text: Callable[..., str | None],
    max_status_todos_per_role: int,
    latest_run: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any] | None:
    """Public-text and source codecs around the native composer."""
    goal_id = public_safe_compact_text(item.get("goal_id") or goal.get("id"), limit=120)
    if not goal_id:
        return None
    texts: dict[str, dict[str, str | None]] = {}

    summaries = {}
    fields = dict.fromkeys(
        (
            *TODO_CANONICAL_READ_RECORD_FIELDS,
            "index",
            "title",
            "text",
            "done",
            "evidence",
            "note",
            "last_actor_agent_id",
            "handoff",
            "handoff_note",
        )
    )
    for role in ("agent", "user"):
        source = item.get(f"{role}_todos")
        source = source if isinstance(source, dict) else {}
        summaries[role] = {
            key: [
                {
                    field: _encode_public_text(
                        row[field],
                        texts,
                        public_safe_compact_text,
                        _PUBLIC_TEXT_CAPS.get(field, ()),
                    )
                    for field in fields
                    if field in row
                }
                for row in values
                if isinstance(row, dict)
            ]
            for key, values in source.items()
            if key in (*TODO_PLANNING_SOURCE_KEYS, "first_open_items", "gate_open_items")
            and isinstance(values, list)
        }
        for key in ("total_count", "open_count"):
            try:
                summaries[role][key] = int(source.get(key) or 0)
            except (TypeError, ValueError):
                summaries[role][key] = 0
    current = latest_run(goal)
    current = current if isinstance(current, dict) else {}
    state = current.get("state")
    state = state if isinstance(state, dict) else {}
    frontmatter = state.get("frontmatter")
    frontmatter = frontmatter if isinstance(frontmatter, dict) else {}
    warning = item.get("stale_latest_run_warning")
    warning = warning if isinstance(warning, dict) else {}
    replan = item.get("autonomous_replan_obligation")
    replan = replan if isinstance(replan, dict) else {}
    request = {
        "schema_version": "status_task_graph_request_v0",
        "goal_id": goal_id,
        "summaries": summaries,
        "max_status_todos_per_role": max_status_todos_per_role,
        "latest_runs": [
            _encode_public_text(
                {
                    key: run.get(key)
                    for key in (
                        "run_id",
                        "generated_at",
                        "classification",
                        "recommended_action",
                        "delivery_outcome",
                        "delivery_batch_scale",
                    )
                },
                texts,
                public_safe_compact_text,
            )
            for run in goal_latest_runs or []
            if isinstance(run, dict)
        ],
        "current_generated_at": _encode_public_text(
            current.get("generated_at"), texts, public_safe_compact_text, (80,)
        ),
        "warning_updated_at": _encode_public_text(
            warning.get("active_state_updated_at"), texts, public_safe_compact_text, (80,)
        ),
        "state_updated_at": _encode_public_text(
            frontmatter.get("updated_at"), texts, public_safe_compact_text, (80,)
        ),
        "replan": _encode_public_text(
            {key: replan.get(key) for key in ("schema_version", "kind", "recommended_action")},
            texts,
            public_safe_compact_text,
        )
        if replan
        else None,
        "fallback_time": now_utc_iso(),
        "public_texts": texts,
    }
    use_snapshot = (
        len(json.dumps(request, separators=(",", ":")).encode()) > MAX_REQUEST_BYTES - 4096
    )
    result = effect_runtime_result(
        "work_item.task_graph.status", request, large_local_snapshot=use_snapshot
    )
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != "status_task_graph_result_v0"
    ):
        raise RuntimeError("TypeScript status task graph shape mismatch")

    return result["projection"]


def build_goal_task_map(
    *,
    goal_id: str,
    todos: list[dict[str, Any]],
    source_truncated: bool,
    public_safe_compact_text: Callable[..., str | None],
    normalize_todo_status: Callable[[Any], str | None],
    todo_done_for_status: Callable[[str], bool],
    todo_status_open: str,
    monitor_task_class: str,
    source_read_at: str | None = None,
) -> dict[str, Any]:
    """Adapt shared source/identity codecs to the native Goal-map composer."""

    texts: dict[str, dict[str, str | None]] = {}
    fields = (
        "todo_id",
        "title",
        "text",
        "status",
        "done",
        "role",
        "task_class",
        "unblocks_todo_id",
        "superseded_by",
        "resume_when",
        "successor_todo_ids",
        "claimed_by",
        "task_domain",
    )
    request = {
        "schema_version": "goal_task_map_request_v0",
        "goal_id": goal_id,
        "todos": _encode_public_text(
            [{key: value[key] for key in fields if key in value} for value in todos],
            texts,
            public_safe_compact_text,
        ),
        "monitor_task_class": monitor_task_class,
        "public_texts": texts,
        "source_truncated": source_truncated,
        "read_facts": {
            "observed_at": now_utc_iso(),
            "source_read_at": source_read_at,
            "source_count": len(todos),
        },
    }
    use_snapshot = (
        len(json.dumps(request, separators=(",", ":")).encode()) > MAX_REQUEST_BYTES - 4096
    )
    result = effect_runtime_result(
        "work_item.task_graph.goal_topology",
        request,
        large_local_snapshot=use_snapshot,
    )
    if not isinstance(result, dict) or result.get("schema_version") != "goal_task_map_result_v0":
        raise RuntimeError("TypeScript goal task map shape mismatch")
    return result["projection"]
