"""Registered-source adapters for the typed interaction context owner.

Read source bodies, not summaries or displayed commands. Selection, fulfillment
and dependent-work policy remain in TypeScript; this module grants no effects.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from ...history import load_registry
from ..effect_runtime import effect_runtime_result
from ..coordination.local_authority import read_canonical_todos_if_promoted
from ..goals.acceptance import inspect_goal_acceptance
from ..goals.state_resolution import resolve_goal_state
from ..todos.active_state_todo_parser import parse_todo_source
from ..todos.list_readback import list_goal_todos
from ..todos.summary_item import todo_text_content_revision


def _source_content(read, *, registry_path, runtime_root, goal_id, todo_id,
                    state_file=None, expected_state_revision=None):
    source = read.get("source")
    if source == "goal_state":
        _goal, _project, state_file = resolve_goal_state(
            registry=load_registry(registry_path), goal_id=goal_id,
            project_override=None, state_file_override=None)
        source_bytes = state_file.read_bytes()
        text = source_bytes.decode("utf-8")
        return {"state_file": str(state_file), "text": text,
            "revision": "sha256:" + sha256(source_bytes).hexdigest()}
    if source == "goal_acceptance":
        return inspect_goal_acceptance(registry_path=registry_path, goal_id=goal_id,
            runtime_root=str(runtime_root))
    if source == "selected_todo":
        if not todo_id:
            raise ValueError("selected Todo identity is unavailable")
        canonical = read_canonical_todos_if_promoted(
            runtime_root=runtime_root, goal_id=goal_id)
        if canonical is not None:
            records = canonical["todos"]
            source_name = canonical["source_authority"]
            authority_read = {
                "source_authority": source_name,
                "provider_revision": canonical.get("provider_revision"),
                "decision_read_from_provider": True,
                "legacy_fallback_used": False,
            }
        else:
            if not isinstance(state_file, Path):
                raise ValueError("selected Todo source requires the current Goal state read")
            source_bytes = state_file.read_bytes()
            revision = "sha256:" + sha256(source_bytes).hexdigest()
            if expected_state_revision and expected_state_revision != revision:
                raise ValueError("selected Todo source changed after the Goal state read")
            state_text = source_bytes.decode("utf-8")
            active, archived, _sections = parse_todo_source(
                state_text, state_path=state_file)
            records = [*active["user"], *active["agent"], *archived]
            source_name = "markdown_active_state"
            authority_read = {
                "source_authority": source_name,
                "provider_revision": revision,
                "decision_read_from_provider": False,
                "legacy_fallback_used": False,
            }
        matches = [item for item in records
            if item.get("todo_id") == todo_id
            and item.get("archive_state", "active") != "archive"]
        todo = dict(matches[0]) if len(matches) == 1 else None
        if isinstance(todo, dict) and "status" not in todo:
            todo["status"] = "done" if todo.get("done") is True else "open"
        if isinstance(todo, dict) and isinstance(todo.get("text"), str):
            revision = todo_text_content_revision(todo["text"])
            if revision:
                todo["content_revision"] = revision
        return {
            "ok": True,
            "matched": len(matches) == 1,
            "ambiguous": len(matches) > 1,
            "todo": todo,
            "source": source_name,
            "authority_read": authority_read,
        }
    raise ValueError("unregistered work context source")


def _user_context(*, registry_path, runtime_root, goal_id, agent_id):
    # Reuse the canonical scope owner (global/blocks_agent/bound_agent and
    # legacy scoping). Exact reads restore full text after scope filtering.
    inventory = list_goal_todos(registry_path=registry_path, goal_id=goal_id,
        role="user", status="open", agent_id=agent_id,
        runtime_root_arg=str(runtime_root))
    records = []
    revision = (inventory.get("authority_read") or {}).get("provider_revision")
    for row in inventory.get("todos", []):
        detail = list_goal_todos(registry_path=registry_path, goal_id=goal_id,
            role="user", status="open", agent_id=agent_id, todo_id=row["todo_id"],
            runtime_root_arg=str(runtime_root))
        if not detail.get("matched") or detail.get("ambiguous"):
            raise ValueError("User context changed during source read; rerun guard")
        if (detail.get("authority_read") or {}).get("provider_revision") != revision:
            raise ValueError("User context source revision changed; rerun guard")
        records.append(detail["todo"])
    return {"source": inventory["source"], "authority_read": inventory.get("authority_read"),
        "todos": records}


def attach_work_context(payload: dict[str, Any], *, registry_path: Path,
                        runtime_root: Path, hook_dispatch: dict[str, Any] | None) -> None:
    packet_selected = payload.get("selected_todo")
    selected_text_snapshot = None
    if isinstance(packet_selected, dict):
        selected_text_snapshot = packet_selected.pop("_context_text_sha256", None)
    interaction = payload.get("interaction_contract")
    if not isinstance(interaction, dict):
        return
    channel = interaction.get("agent_channel") or {}
    reads = channel.get("required_reads") or []
    goal_id = payload.get("goal_id")
    agent_id = (payload.get("agent_identity") or {}).get("agent_id")
    if not goal_id:
        return
    plan = effect_runtime_result("work_item.context.plan", {"packet": payload})
    selected = plan.get("selected_todo")
    if isinstance(selected, dict) and isinstance(selected_text_snapshot, str):
        selected["_context_text_sha256"] = selected_text_snapshot
    results = [dict(item) for item in (hook_dispatch or {}).get("contexts", [])]
    state_file = None
    state_revision = None
    for read in reads:
        if read.get("source") not in {"goal_state", "goal_acceptance", "selected_todo"}:
            continue
        result = {"command": read["command"]}
        try:
            result["content"] = _source_content(read, registry_path=registry_path,
                runtime_root=runtime_root, goal_id=goal_id, todo_id=plan.get("todo_id"),
                state_file=state_file, expected_state_revision=state_revision)
        except (OSError, ValueError, RuntimeError):
            result["error_code"] = "context_source_unavailable"
        results.append(result)
        if read.get("source") == "goal_state" and isinstance(result.get("content"), dict):
            resolved_path = result["content"].get("state_file")
            if isinstance(resolved_path, str):
                state_file = Path(resolved_path)
                state_revision = result["content"].get("revision")
    users = None
    if agent_id and plan.get("read_user_todos"):
        try:
            users = _user_context(registry_path=registry_path, runtime_root=runtime_root,
                goal_id=goal_id, agent_id=agent_id)
        except (OSError, ValueError, RuntimeError):
            users = {"error_code": "context_source_unavailable"}
    projected = effect_runtime_result("work_item.context.project", {
        "required_reads": reads, "source_results": results,
        "selected_todo": plan.get("selected_todo"), "user_todos": users,
        "hook_dispatch": hook_dispatch,
    }, large_local_snapshot=True)
    channel.update(projected)
    selected_todo = payload.get("selected_todo")
    if isinstance(selected_todo, dict):
        selected_todo.pop("content_revision", None)
        selected_todo.pop("_context_text_sha256", None)
        selected_todo.pop("_context_text_display_only", None)
    work_context = channel.get("work_context")
    if isinstance(work_context, dict):
        sources = work_context.get("sources")
        for source in sources if isinstance(sources, list) else []:
            content = source.get("content") if isinstance(source, dict) else None
            todo = content.get("todo") if isinstance(content, dict) else None
            if isinstance(todo, dict):
                todo.pop("content_revision", None)
    if not projected["work_context"]["complete"]:
        channel["delivery_allowed"] = False
    # Bodies have one carrier. Historical pointers are not another instruction
    # to execute the same read; explicit diagnostics retain hook metadata only.
    payload.pop("required_reads", None)
    # The Goal path is needed while constructing the exact progressive read,
    # but is local routing metadata rather than public packet content.
    payload.pop("goal_state_file", None)
