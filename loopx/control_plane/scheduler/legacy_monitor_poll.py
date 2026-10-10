"""Atomic Markdown storage for the shared TypeScript Monitor batch.

The operation receipt and every business mutation share one replacement.
Parsing, locking and projection delivery stay here; retry and Todo rules stay
in the typed planner. Retained receipts are outside the archivable Todo blocks.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ..coordination.legacy_writer_fence import legacy_todo_write_transaction
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..runtime.local_state_write_correctness import (
    build_local_state_write_correctness_dry_run_packet,
)
from ..runtime.document_io import verify_state_text_durable
from ..todos.active_state_editing import (
    find_todo_block, insert_into_existing_section, insert_new_section,
    replace_updated_at, section_bounds,
)
from ..todos.contract import (
    TODO_METADATA_FIELDS, format_todo_metadata_line, metadata_line_for_todo_block,
    parse_todo_metadata_line,
    todo_marker_for_status,
)
from ..todos.goal_todo_projection import goal_todo_summaries
from ..todos.handoff_mode import goal_handoff_mode
from ..todos.mutation_response import serialize_added_todo_payload, serialize_todo_update_result
from ..todos.mutation_authority import authorize_todo_lifecycle_mutation
from ..todos.next_action_runtime import apply_added_todo_next_action
from ..todos.path_resolution import resolve_todo_state_path
from ..todos.todo_semantics import todo_priority_label


def _receipt_marker(operation_id: str) -> str:
    return f"<!-- loopx:monitor-batch:{hashlib.sha256(operation_id.encode()).hexdigest()} "


def _read_receipt(text: str, operation_id: str) -> dict[str, Any] | None:
    marker = _receipt_marker(operation_id)
    matches = re.findall(re.escape(marker) + r"([A-Za-z0-9+/=]+) -->", text)
    if text.count(marker) != len(matches):
        raise ValueError("Monitor batch operation receipt is malformed")
    if len(matches) > 1:
        raise ValueError("duplicate Monitor batch operation receipts")
    if not matches:
        return None
    receipt = json.loads(base64.b64decode(matches[0], validate=True))
    if not isinstance(receipt, dict):
        raise ValueError("Monitor batch operation receipt must be an object")
    return receipt


def _plan(request: dict[str, Any], **facts: Any) -> dict[str, Any]:
    try:
        result = effect_runtime_result("scheduler.monitor_batch.plan", {**request, **facts})
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from error
    if not isinstance(result, dict) or result.get("schema_version") != "loopx_monitor_batch_plan_result_v0":
        raise TypeError("TypeScript Monitor batch plan shape mismatch")
    return result


def _writeback(planned: dict[str, Any]) -> dict[str, Any]:
    return dict(planned["writeback"])


def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Use the public Markdown codec for the same wire types as Todo add/update."""
    line = format_todo_metadata_line(**{key: record[key] for key in TODO_METADATA_FIELDS
        if key != "role" and key in record and record[key] is not None})
    return parse_todo_metadata_line(line) or {}


def _legacy_writeback(
    planned: dict[str, Any], *, request: dict[str, Any], original: str,
    todos: list[dict[str, Any]], state: Path, project: Path | None,
    mutation_authority: dict[str, Any],
) -> dict[str, Any]:
    """Serialize typed batch facts through the existing public Todo result shapes."""
    result = _writeback(planned)
    monitor = next(todo for todo in todos if todo["todo_id"] == result["todo_id"])
    updated = planned["mutations"][0]["todo"]
    original_lines = original.splitlines()
    match = find_todo_block(original_lines, todo_id=result["todo_id"], role="agent")
    if match is None:
        raise ValueError("Monitor Todo disappeared during batch planning")
    section = match[1]
    status_changed = monitor.get("status") != updated.get("status")
    text_changed = monitor.get("text") != updated.get("text")
    metadata_updated = any(monitor.get(key) != updated.get(key)
                           for key in TODO_METADATA_FIELDS if key != "role")
    changed = status_changed or text_changed or metadata_updated
    timestamp = request["observation"]["generated_at"]
    handoff = {"handoff_mode": goal_handoff_mode(original)}
    update = serialize_todo_update_result(
        role="agent", section=section, todo=monitor.get("text"),
        todo_id=result["todo_id"], status=str(updated["status"]),
        priority=todo_priority_label(monitor), status_changed=status_changed,
        text_changed=text_changed, metadata_updated=metadata_updated,
        metadata=_metadata(updated),
        monitor_poll_transition=result["todo_update"]["monitor_poll_transition"],
    )
    result["todo_update"] = {
        "ok": True, "dry_run": request["dry_run"], "changed": changed,
        "goal_id": request["goal_id"], "agent_id": request["actor_agent_id"],
        "mutation_authority": mutation_authority, **handoff, **update,
        "state_file": str(state), "project": str(project) if project else None,
        "updated_at": timestamp if changed else None,
    }
    created_ids = {mutation["todo"]["todo_id"] for mutation in planned["mutations"][1:]}
    successors = []
    for record in result["next_todos"]:
        role = record["role"]
        added = record["todo_id"] in created_ids
        metadata = _metadata(record)
        add_result = {**metadata, "todo_id": record["todo_id"],
            "section": next((match[2] for match in [section_bounds(original_lines, role)] if match),
                            "User Todo / Owner Review Reading Queue" if role == "user" else "Agent Todo"),
            "status": record["status"], "task_class": record.get("task_class"),
            "action_kind": record.get("action_kind"),
            "required_write_scopes": record.get("required_write_scopes") or [],
            "required_capabilities": record.get("required_capabilities") or [],
            "target_capabilities": record.get("target_capabilities") or [],
            "explore_result_node_refs": record.get("explore_result_node_refs") or [],
            "required_decision_scopes": record.get("required_decision_scopes") or [],
            "excluded_agents": record.get("excluded_agents") or [],
            "already_exists": not added, "metadata_updated": False,
            "status_changed": False}
        successors.append(serialize_added_todo_payload(
            add_result=add_result, goal_id=request["goal_id"], role=role,
            todo_text=record["text"],
            agent_id=request["actor_agent_id"] if role == "user" else None,
            state_file=state, project=project, updated_at=timestamp,
            dry_run=request["dry_run"], added=added,
            metadata_updated=False, changed=added, handoff_gate=handoff,
        ))
    result["next_todos"] = successors
    result["successor_receipts"] = [
        {key: todo[key] for key in (
            "todo_id", "role", "task_class", "action_kind", "task_repository",
            "continuation_policy", "required_capabilities", "claimed_by",
            "unblocks_todo_id", "target_key") if todo.get(key) not in (None, "", [])}
        for todo in successors
    ]
    return result


def _attach_batch_shadow(result: dict[str, Any]) -> dict[str, Any]:
    """Expose one real capture through the legacy nested result locations."""
    evidence = result.get("coordination_runtime_shadow")
    if evidence is not None:
        result["todo_update"]["coordination_runtime_shadow"] = evidence
        for todo in result["next_todos"]:
            todo["coordination_runtime_shadow"] = evidence
    return result


def _attach_batch_dry_run(result: dict[str, Any], original: str) -> None:
    """One preview describes the single locked replacement and its Todos."""
    ids = [result["todo_id"], *(todo["todo_id"] for todo in result["next_todos"])]
    packet = build_local_state_write_correctness_dry_run_packet(
        goal_id=result["goal_id"], writer_id="loopx.todo", write_class="todo_update",
        state_text=original,
        target_refs={"state_file_ref": "registry.goal.state_file", "todo_ids": ids},
        patch_summary=f"preview one Monitor poll batch for {len(ids)} Todos: would change active state",
        expected_write_scopes=["active_state"],
        narrower_lock_allowed="not_for_multi_todo_batch",
    )
    result["todo_update"]["local_state_write_correctness"] = packet
    for todo in result["next_todos"]:
        todo["local_state_write_correctness"] = packet


def replay_legacy_monitor_poll(
    *, registry_path: Path, request: dict[str, Any],
) -> dict[str, Any] | None:
    """Read immutable pre-promotion receipts without reopening a legacy writer."""
    _, state = resolve_todo_state_path(registry_path=registry_path, goal_id=request["goal_id"],
                                      require_existing=False)
    if not state.exists():
        return None
    previous = _read_receipt(state.read_text(encoding="utf-8"), request["operation_id"])
    if previous is None:
        return None
    return _writeback(_plan(request, registered_agents=[], todos=[], previous_receipt=previous))


def _apply_mutations(lines: list[str], mutations: list[dict[str, Any]],
                     todos: list[dict[str, Any]]) -> None:
    from ..todos.line_update import upsert_todo_metadata

    sources = {todo["todo_id"]: todo for todo in todos}
    for mutation in mutations:
        record = mutation["todo"]
        role, todo_id = record["role"], record["todo_id"]
        found = find_todo_block(lines, todo_id=todo_id, role=role)
        if found is not None:
            # Preserve narrative and private validation declarations in the
            # original block; only fields changed by the typed owner are edited.
            source = sources[todo_id]
            updates = {key: record.get(key) for key in set(source) | set(record)
                       if key in TODO_METADATA_FIELDS and source.get(key) != record.get(key)}
            block = found[-1]
            upsert_todo_metadata(lines, block, metadata_line_for_todo_block(block, updates))
        else:
            metadata = format_todo_metadata_line(**{key: record[key] for key in TODO_METADATA_FIELDS
                if key != "role" and key in record and record[key] is not None})
            block = f"- [{todo_marker_for_status(record['status'])}] {record['text']}\n{metadata}"
            bounds = section_bounds(lines, role)
            if bounds:
                insert_into_existing_section(lines, bounds[0], bounds[1], block)
            else:
                insert_new_section(lines, role, block)


def apply_legacy_monitor_poll(
    *, registry_path: Path, runtime_root: Path, request: dict[str, Any],
) -> dict[str, Any]:
    goal_id, dry_run = request["goal_id"], request["dry_run"]
    project, state = resolve_todo_state_path(registry_path=registry_path, goal_id=goal_id)
    with legacy_todo_write_transaction(registry_path, goal_id, state,
            request["actor_agent_id"], "todo_update", dry_run, runtime_root=runtime_root):
        original = state.read_text(encoding="utf-8")
        previous = _read_receipt(original, request["operation_id"])
        goal = load_goal_from_registry(registry_path, goal_id)
        todos = goal_todo_summaries(goal, state_text=original, state_path=state,
            rollout_events=[], roles=["user", "agent"], status=None, todo_id=None,
            agent_id=None, limit=None, include_retained=True).todos
        planned = _plan(request, registered_agents=registered_agent_ids_for_goal(goal),
                        todos=todos, previous_receipt=previous)
        if planned["replayed"]:
            verify_state_text_durable(state, original)
            return _writeback(planned)
        from ..coordination.runtime_shadow_writer_adapter import (
            begin_todo_runtime_shadow_capture, settle_todo_runtime_shadow_capture,
            write_captured_todo_state,
        )

        monitor = next(todo for todo in todos if todo["todo_id"] == planned["writeback"]["todo_id"])
        authority = authorize_todo_lifecycle_mutation(registry_path=registry_path, goal_id=goal_id,
            command="update", todo=monitor, actor_agent_id=request["actor_agent_id"])
        result = _legacy_writeback(planned, request=request, original=original,
            todos=todos, state=state, project=project, mutation_authority=authority)
        planned["receipt"]["writeback"] = result
        lines = original.splitlines()
        _apply_mutations(lines, planned["mutations"], todos)
        for todo in result["next_todos"]:
            apply_added_todo_next_action(lines, role=todo["role"],
                add_result={**todo, "changed": todo["added"]})
        if dry_run:
            _attach_batch_dry_run(result, original)
            capture = begin_todo_runtime_shadow_capture(registry_path=registry_path,
                runtime_root=runtime_root, goal_id=goal_id, state_path=state,
                write_class="todo_update", original_text=original)
            return _attach_batch_shadow(settle_todo_runtime_shadow_capture(result,
                registry_path=registry_path, runtime_root=runtime_root,
                goal_id=goal_id, capture=capture))
        capture = begin_todo_runtime_shadow_capture(registry_path=registry_path,
            runtime_root=runtime_root, goal_id=goal_id, state_path=state,
            write_class="todo_update", original_text=original)
        encoded = base64.b64encode(json.dumps(planned["receipt"], ensure_ascii=False,
            separators=(",", ":")).encode()).decode()
        first_heading = next((i for i, line in enumerate(lines) if line.startswith("#")), len(lines))
        lines[first_heading:first_heading] = [_receipt_marker(request["operation_id"]) + encoded + " -->", ""]
        text = replace_updated_at("\n".join(lines) + "\n", request["observation"]["generated_at"])
        write_captured_todo_state(capture, runtime_root=runtime_root, goal_id=goal_id,
                                  state_path=state, text=text)
        verify_state_text_durable(state, text)
    return _attach_batch_shadow(settle_todo_runtime_shadow_capture(result,
        registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
        capture=capture))
