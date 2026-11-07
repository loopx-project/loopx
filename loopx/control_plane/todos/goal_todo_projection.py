"""Compatibility source codecs and transport for native Todo read projections.

Explicit source inspection can project captured Markdown while holding its
source lock. Ordinary Todo reads require canonical authority; these helpers
never turn a display file into fallback authority.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from ..goals.legacy_event_source import require_no_legacy_todo_events
from .active_state_editing import TODO_SECTION_HEADINGS
from .contract import (
    normalize_todo_claimed_by,
    normalize_todo_id,
    normalize_todo_status,
)
from .todo_summary import compact_evaluated_todo_group


def empty_todo_summary(*, role: str) -> dict[str, Any]:
    return {
        "schema_version": "todo_summary_v0",
        "role": role,
        "source_section": TODO_SECTION_HEADINGS[role],
        "total_count": 0,
        "open_count": 0,
        "done_count": 0,
        "items": [],
        "first_open_items": [],
    }

def filtered_todo_summary(
    summary: dict[str, Any] | None,
    *,
    role: str,
    status: str | None = None,
    todo_id: str | None = None,
    agent_id: str | None = None,
    item_limit: int | None = None,
) -> dict[str, Any]:
    items = list((summary or {}).get("items") or [])
    selection = {"role": role, "status": normalize_todo_status(status),
        "todo_id": normalize_todo_id(todo_id) if todo_id else None,
        "agent_id": normalize_todo_claimed_by(agent_id) if agent_id else None}
    source_section = str((summary or {}).get("source_section") or TODO_SECTION_HEADINGS[role])
    return (
        compact_evaluated_todo_group(
            items,
            source_section=source_section,
            role=role,
            item_limit=item_limit,
            selection=selection,
        )
        or empty_todo_summary(role=role)
    )

def summary_items(fields: dict[str, Any], role: str) -> list[dict[str, Any]]:
    summary = fields.get(f"{role}_todos") if isinstance(fields, dict) else None
    if not isinstance(summary, dict):
        return []
    return [item for item in summary.get("items") or [] if isinstance(item, dict)]


def observe_todo_acceptance(
    fields: dict[str, Any], canonical: dict[str, Any], *,
    registry_path: Path | None, goal_id: str,
) -> dict[str, Any]:
    from ..goals.acceptance import observe_canonical_goal_acceptance

    acceptance = observe_canonical_goal_acceptance(
        canonical, registry_path=registry_path, goal_id=goal_id)
    summary = fields.get("agent_todos")
    if isinstance(summary, dict) and "goal_acceptance_contract" in summary:
        from .summary_item import preserve_canonical_todo_source
        return {**fields, "agent_todos": preserve_canonical_todo_source(summary,
            {**summary, "goal_acceptance_contract": acceptance})}
    return fields


class GoalTodoSummaries:
    """Role summaries and todo items projected from one active-state text."""

    __slots__ = (
        "source",
        "summaries",
        "todos",
        "unfiltered_count",
        "uncapped_todo_count",
    )

    def __init__(
        self,
        *,
        source: str,
        summaries: dict[str, dict[str, Any]],
        todos: list[dict[str, Any]],
        unfiltered_count: int,
        uncapped_todo_count: int,
    ) -> None:
        self.source = source
        self.summaries = summaries
        self.todos = todos
        self.unfiltered_count = unfiltered_count
        self.uncapped_todo_count = uncapped_todo_count

def goal_todo_summaries(
    goal: dict[str, Any] | None,
    *,
    state_text: str,
    state_path: Path,
    rollout_events: list[dict[str, Any]],
    roles: list[str],
    status: str | None,
    todo_id: str | None,
    agent_id: str | None,
    limit: int | None,
    include_retained: bool = False,
) -> GoalTodoSummaries:
    from ..runtime.time import now_utc

    require_no_legacy_todo_events(goal or {}, state_path=state_path)
    facts = prepare_todo_source_text(goal, state_text, state_path, todo_id=todo_id, retained=include_retained)
    now = now_utc()
    projected = project_todo_read({"kind": "source_fields", **facts,
        "rollout_events": list(rollout_events), "rollout_event_source": getattr(rollout_events, "source", None),
        "observed_at": now.timestamp(), "evaluated_at": now.isoformat(),
        "projection_query": {"roles": roles, "status": normalize_todo_status(status),
            "todo_id": normalize_todo_id(todo_id) if todo_id else None,
            "agent_id": normalize_todo_claimed_by(agent_id) if agent_id else None,
            "limit": limit, "source_name": "markdown_active_state"}})["projection"]
    return GoalTodoSummaries(**projected)


def todo_summaries_from_fields(
    *,
    fields: dict[str, Any],
    source: str,
    rollout_events: list[dict[str, Any]],
    roles: list[str],
    status: str | None,
    todo_id: str | None,
    agent_id: str | None,
    limit: int | None,
) -> GoalTodoSummaries:
    from ..runtime.time import now_utc

    now = now_utc()
    result = project_todo_read({"kind": "select", "fields": fields,
        "field_evaluations": {f"{item_role}_todos": [getattr(item, "succession_evaluation", None)
            for item in (fields.get(f"{item_role}_todos") or {}).get("items") or []] for item_role in roles},
        "roles": roles, "status": normalize_todo_status(status),
        "todo_id": normalize_todo_id(todo_id) if todo_id else None,
        "agent_id": normalize_todo_claimed_by(agent_id) if agent_id else None,
        "limit": limit, "source_name": source,
        "observed_at": now.timestamp(), "evaluated_at": now.isoformat()})["projection"]
    return GoalTodoSummaries(**result)


def retained_todo_summary_fields(
    items: list[dict[str, Any]],
    *,
    rollout_events: list[dict[str, Any]],
) -> dict[str, Any]:

    return source_todo_fields([
        {"role": role, "source_section": TODO_SECTION_HEADINGS[role],
         "items": [item for item in items if item.get("role") == role],
         "include_empty_source": True, "text_limit": None, "include_content_revision": False}
        for role in ("user", "agent")], lineage=items, rollout_events=rollout_events)


def exact_archived_todo_summaries(
    *,
    archived_items: list[dict[str, Any]],
    source: str,
    rollout_events: list[dict[str, Any]],
    roles: list[str],
    status: str | None,
    todo_id: str,
    agent_id: str | None,
    limit: int | None,
) -> GoalTodoSummaries | None:
    from ..runtime.time import now_utc

    lineage, facts = legacy_lineage_facts(archived_items)
    groups = [{"role": role, "source_section": "Completed Work Archive", "include_empty_source": True,
        "items": [row for row in archived_items if row.get("role") == role]} for role in ("user", "agent")]
    now = now_utc()
    projected = project_todo_read({"kind": "source_fields", "groups": prepare_todo_source_groups(groups, lineage=archived_items),
        "lineage": lineage, "lineage_facts": facts, "rollout_events": list(rollout_events),
        "rollout_event_source": getattr(rollout_events, "source", None),
        "observed_at": now.timestamp(), "evaluated_at": now.isoformat(),
        "projection_query": {"exact_archive": True, "roles": roles, "status": normalize_todo_status(status),
            "todo_id": normalize_todo_id(todo_id), "agent_id": normalize_todo_claimed_by(agent_id) if agent_id else None,
            "limit": limit, "source_name": source}})["projection"]
    return GoalTodoSummaries(**projected) if projected is not None else None

def project_goal_todo_items(
    goal: dict[str, Any] | None,
    *,
    state_text: str,
    state_path: Path,
    rollout_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every user and agent todo item projected from one active-state text.

    Same items ``list_goal_todos`` returns without filters, computed from the
    caller's text instead of the file so it can run inside the writer's lock.
    """

    return goal_todo_summaries(
        goal,
        state_text=state_text,
        state_path=state_path,
        rollout_events=rollout_events,
        roles=["user", "agent"],
        status=None,
        todo_id=None,
        agent_id=None,
        limit=None,
    ).todos


__all__ = [
    "GoalTodoSummaries",
    "empty_todo_summary",
    "exact_archived_todo_summaries",
    "filtered_todo_summary",
    "goal_todo_summaries",
    "project_goal_todo_items",
    "summary_items",
    "todo_summaries_from_fields",
]



def project_todo_read(params: dict[str, Any]) -> dict[str, Any]:
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    import base64
    import hashlib
    import json
    import os
    import tempfile
    from contextlib import ExitStack
    from pathlib import Path
    from ..effect_runtime import MAX_LOCAL_SNAPSHOT_BYTES
    from ..runtime.private_permissions import protect_private_path

    native_read = None
    if params.get("kind") == "select":
        from .summary_item import canonical_todo_select_read
        native_read = canonical_todo_select_read(params)
    request = {"schema_version": "todo_read_request_v0", **(native_read or params)}
    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    chunks: list[bytes] = []
    identity = None
    try:
        with ExitStack() as stack:
            if len(encoded) > MAX_LOCAL_SNAPSHOT_BYTES - 4096:
                directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="loopx-effect-todo-")))
                protect_private_path(directory, directory=True)
                source = directory / "facts.json"
                fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as file:
                    protect_private_path(source)
                    file.write(encoded)
                request = {"schema_version": "todo_read_request_v0", "facts_ref": {
                    "path": str(source), "byte_count": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}}
            consumed = 0
            while True:
                result = effect_runtime_result("todo.read", request, large_local_snapshot=True,
                    timeout=15.0 if params.get("kind") in {"query", "canonical_fields", "historical_fields"} else 10.0)
                if not isinstance(result, dict) or result.get("schema_version") != "todo_read_result_v0":
                    raise ValueError("invalid typed Todo read result")
                piece = result.get("result_piece")
                if piece is None:
                    if chunks and not (result.get("canonical") or result.get("rejection")):
                        raise ValueError("incomplete Todo read result")
                    break
                if not isinstance(piece, dict) or piece.get("offset") != consumed:
                    raise ValueError("invalid Todo read result page")
                current = (piece.get("sha256"), piece.get("byte_count"),
                           piece.get("content_encoding"), piece.get("decoded_byte_count"))
                if identity is None:
                    identity = current
                if identity != current or type(current[1]) is not int or current[1] <= consumed:
                    raise ValueError("Todo read result identity changed")
                block = base64.b64decode(piece["data"], validate=True)
                if not block:
                    raise ValueError("Todo read result page did not advance")
                chunks.append(block)
                consumed += len(block)
                if consumed > current[1]:
                    raise ValueError("Todo read result exceeds its declared population")
                after = piece.get("next")
                if after is None:
                    complete = b"".join(chunks)
                    if consumed != current[1] or hashlib.sha256(complete).hexdigest() != current[0]:
                        raise ValueError("Todo read result digest mismatch")
                    result = _decode_todo_result_bytes(complete, current[2], current[3])
                    break
                if not isinstance(after, dict) or after.get("offset") != consumed or after.get("sha256") != current[0]:
                    raise ValueError("invalid Todo read result continuation")
                request = {**request, "result_page": after}
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(result, dict) or result.get("schema_version") != "todo_read_result_v0":
        raise ValueError("invalid typed Todo read result")
    if result.get("rejection"):
        raise EffectRuntimeRejected(result["rejection"]["message"])
    if result.get("canonical") is not None and (native_read is not None or params.get("kind") == "quota_projection"):
        from ..coordination.local_authority import validate_canonical_todo_read
        read = native_read or next(request["summary_request"]["source_reference"]["read"]
            for request in params["requests"] if "source_reference" in request["summary_request"])
        validate_canonical_todo_read(result["canonical"], goal_id=read["source"]["goal_id"])
    if params.get("kind") == "group":
        summary = result.get("summary")
        if not isinstance(summary, dict) or summary.get("schema_version") != "todo_summary_v0":
            raise ValueError("invalid typed Todo summary fields")
        if params.get("role") == "agent":
            frontier = summary.get("advancement_frontier_revision_index")
            if not isinstance(frontier, dict) or frontier.get("schema_version") != "todo_frontier_revision_index_v0":
                raise ValueError("invalid typed Todo summary frontier index")
    if params.get("kind") in {"canonical_fields", "query"} or native_read is not None:
        _bind_todo_read_sources(result, native_read or params, native_selection=native_read is not None)
    return result


def _decode_todo_result_bytes(data: bytes, encoding: Any, size: Any) -> dict[str, Any]:
    import json
    from ..effect_runtime import MAX_LOCAL_SNAPSHOT_BYTES

    if encoding is None:
        if size is not None:
            raise ValueError("invalid Todo read result encoding")
    elif encoding == "gzip" and type(size) is int and 0 < size <= MAX_LOCAL_SNAPSHOT_BYTES:
        import zlib
        try:
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            decoded = decoder.decompress(data, size + 1)
        except zlib.error as error:
            raise ValueError("invalid compressed Todo read result") from error
        if len(decoded) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("invalid compressed Todo read result")
        data = decoded
    else:
        raise ValueError("invalid Todo read result encoding")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("invalid typed Todo read result")
    return result


def _bind_todo_read_sources(result: dict[str, Any], read: dict[str, Any], *, native_selection: bool) -> None:
    from .summary_item import bind_canonical_todo_sources

    bind_canonical_todo_sources(result, read)
    if native_selection and result.get("projection"):
        from .succession_warning import _EvaluatedTodo
        projection = result["projection"]
        for field, decisions in result["field_evaluations"].items():
            summary = projection["summaries"][field]
            summary["items"] = [_EvaluatedTodo(item, decision)
                for item, decision in zip(summary["items"], decisions, strict=True)]
        projection["todos"] = [item for summary in projection["summaries"].values() for item in summary["items"]]


def restore_todo_field_evaluations(result: dict[str, Any]) -> dict[str, Any]:
    from .succession_warning import _EvaluatedTodo

    fields = cast(dict[str, Any], result["fields"])
    for role, decisions in result["field_evaluations"].items():
        fields[role]["items"] = [_EvaluatedTodo(item, decision)
            for item, decision in zip(fields[role]["items"], decisions, strict=True)]
    return fields


def canonical_todo_fields(todos: Any, *, rollout_events: Any = None, **facts: Any) -> dict[str, Any]:
    from ..runtime.time import now_utc

    now = now_utc()
    if facts.get("available_capabilities") is not None:
        facts["available_capabilities"] = sorted({str(value).strip().lower()
            for value in facts["available_capabilities"] if str(value).strip()})
    return restore_todo_field_evaluations(project_todo_read({
        "kind": "fields", "todos": todos, **facts,
        "rollout_events": list(rollout_events or []),
        "rollout_event_source": getattr(rollout_events, "source", None),
        "observed_at": now.timestamp(), "evaluated_at": now.isoformat(),
    }))


def source_todo_fields(groups: Any, *, lineage: Any, rollout_events: Any = None, available_capabilities: Any = None,
                       preferred_todo_ids: Any = None, item_limit: int | None = None, evaluated_at: str | None = None,
                       archived_items: Any = None, standing_items: Any = None, legacy_source_order: bool = False,
                       vision_runs: Any = None, include_task_orchestration_authority: bool = False) -> dict[str, Any]:
    from ..runtime.time import now_utc

    now = now_utc()
    prepared = prepare_todo_source_groups(groups, lineage=lineage)
    lineage_rows, lineage_facts = legacy_lineage_facts(lineage)
    result = project_todo_read({"kind": "source_fields", "groups": prepared,
        "lineage": lineage_rows, "lineage_facts": lineage_facts, "rollout_events": list(rollout_events or []),
        "rollout_event_source": getattr(rollout_events, "source", None),
        "available_capabilities": available_capabilities,
        "preferred_todo_ids": sorted(preferred_todo_ids or []), "item_limit": item_limit,
        "archived_items": archived_items, "standing_items": standing_items,
        "legacy_source_order": legacy_source_order,
        "include_full_source": bool(vision_runs),
        "include_task_orchestration_authority": include_task_orchestration_authority,
        "observed_at": now.timestamp(), "evaluated_at": evaluated_at or now.isoformat()})
    fields = restore_todo_field_evaluations(result)
    for role in ("user", "agent"):
        summary = fields.get(f"{role}_todos")
        if not summary:
            continue
        if include_task_orchestration_authority:
            from .todo_summary import _task_orchestration_authority
            summary["task_orchestration_authority"] = _task_orchestration_authority(
                result["source_orchestration"][role], role=role)
        if vision_runs:
            from .succession_warning import _EvaluatedTodo
            from ..goals.goal_vision_wait_projection import attach_active_vision_waits
            source: list[dict[str, Any]] = [_EvaluatedTodo(item, decision) for item, decision in zip(
                result["source_items"][role], result["source_evaluations"][role], strict=True)]
            attach_active_vision_waits(summary, vision_runs, role=role, items=source,
                lineage_items=lineage)
    return fields


def project_todo_read_group(items: Any, *, source_section: str | None, role: str | None, preferred_todo_ids: Any = None,
                            lineage: Any = None, rollout_events: Any = None, available_capabilities: Any = None,
                            item_limit: int | None = None, evaluated_at: str | None = None, evaluated: bool = False,
                            full_selection: bool = True, selection: Any = None, resume_lineage: Any = None,
                            include_full_source: bool = False, include_task_orchestration_authority: bool = False) -> dict[str, Any]:
    from .succession_warning import _EvaluatedTodo
    from ..runtime.time import now_utc

    now = now_utc()
    lineage_rows, lineage_facts = legacy_lineage_facts(lineage or [])
    result = project_todo_read({
        "kind": "group", "items": items, "lineage": lineage_rows, "lineage_facts": lineage_facts,
        "resume_lineage": resume_lineage,
        "evaluated": evaluated,
        "evaluations": [item.succession_evaluation if isinstance(item, _EvaluatedTodo) else None
            for item in items] if evaluated else None,
        "source_section": source_section, "role": role,
        "preferred_todo_ids": sorted(preferred_todo_ids or []),
        "rollout_events": list(rollout_events or []),
        "rollout_event_source": getattr(rollout_events, "source", None),
        "available_capabilities": sorted(available_capabilities) if available_capabilities is not None else None,
        "item_limit": item_limit, "evaluated_at": evaluated_at or now.isoformat(),
        "observed_at": now.timestamp(), "full_selection": full_selection,
        "selection": selection,
        "include_full_source": include_full_source,
        "include_task_orchestration_authority": include_task_orchestration_authority,
    })
    # The native owner validates this ephemeral evidence again on filtered reads.
    if "items" in result:
        result["items"] = [_EvaluatedTodo(item, decision)
            for item, decision in zip(result["items"], result["evaluations"], strict=True)]
    result["summary"]["items"] = [
        _EvaluatedTodo(item, decision) for item, decision in zip(
            result["summary"].get("items", []), result["visible_evaluations"], strict=True)
    ]
    return result


def prepare_todo_source_groups(groups: Any, *, lineage: Any) -> list[dict[str, Any]]:
    from .todo_summary import _structured_todo_group_items, _structured_resume_source_items
    from .resume_condition import build_todo_resume_evaluation_request

    prepared = []
    for group in groups:
        items = _structured_todo_group_items(group["items"], source_section=group["source_section"],
            role=group["role"], text_limit=group.get("text_limit", 500),
            include_content_revision=group.get("include_content_revision", True))
        prepared.append({**group, "items": items,
            "resume_lineage": build_todo_resume_evaluation_request([], source_items=_structured_resume_source_items(
                lineage, source_section=group["source_section"]))["source_items"]
                if any(item.get("resume_when") for item in items) else None})
    return prepared


def legacy_lineage_facts(items: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from .succession_warning import succession_facts

    return ([{"todo_id": item.get("todo_id"), "role": item.get("role"), "source_section": item.get("source_section"),
              "archive_state": item.get("archive_state") or "active"} for item in items],
            [succession_facts(item) for item in items])


def prepare_todo_source_text(goal: dict[str, Any] | None, state_text: str, state_path: Path, *, todo_id: str | None = None, retained: bool = False) -> dict[str, Any]:
    from .active_state_todo_parser import parse_todo_source
    from .todo_summary import _structured_resume_source_items
    from .standing_decision import standing_decision_source_items
    from .active_state_editing import TODO_SECTION_HEADINGS
    from .contract import normalize_todo_id

    active, archived, sections = parse_todo_source(state_text, goal=goal, state_path=state_path)
    records = [*active["user"], *active["agent"], *archived]
    groups = [{"role": role, "source_section": TODO_SECTION_HEADINGS[role] if retained else sections[role],
        "items": [item for item in records if item.get("role") == role] if retained else active[role],
        "include_empty_source": retained or sections[role] is not None,
        "text_limit": None if retained else 500, "include_content_revision": not retained}
        for role in ("user", "agent")]
    archived_decisions = [item for item in archived if item.get("role") == "user"]
    archived_query = _structured_resume_source_items(archived, source_section="Completed Work Archive") if todo_id else []
    lineage = records if retained else [*active["user"], *active["agent"], *[item for item in archived if normalize_todo_id(item.get("todo_id"))]]
    lineage_rows, lineage_facts = legacy_lineage_facts(lineage)
    return {"groups": prepare_todo_source_groups(groups, lineage=records), "lineage": lineage_rows, "lineage_facts": lineage_facts,
        "archived_items": None if retained else archived,
        "standing_items": None if retained else standing_decision_source_items([*active["user"], *archived_decisions]),
        "legacy_source_order": not archived_decisions,
        "archived_items_for_query": archived_query,
        "full_bodies": [item for item in records if item.get("todo_id") == todo_id] if todo_id else [],
        "source_count": len(records)}
