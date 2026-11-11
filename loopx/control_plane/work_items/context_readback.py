"""Registered-source adapters for the typed interaction context owner.

Read source bodies, not summaries or displayed commands. Selection, fulfillment
and dependent-work policy remain in TypeScript; this module grants no effects.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from ...history import load_registry
from ..effect_runtime import DEFAULT_REQUEST_TIMEOUT_SECONDS, effect_runtime_result
from ..coordination.local_authority import LOCAL_COORDINATION_TODO_LIST_TIMEOUT_SECONDS
from ..goals.acceptance import inspect_goal_acceptance
from ..goals.state_resolution import resolve_goal_state
from ..todos.list_readback import list_goal_todos
from ..todos.machine_region import find_todo_regions, visible_markdown_lines
from ..todos.machine_section_projection import is_sqlite_archive_summary_pointer
from ..scheduler.monitor_batch_receipts import is_monitor_receipt_line


def _source_content(
    read: dict[str, Any], *, registry_path: Path, runtime_root: Path, goal_id: str,
    todo_id: str | None, agent_id: str | None = None, inline_goal_source: bool = False,
) -> dict[str, Any]:
    source = read.get("source")
    if source == "goal_state":
        _goal, _project, state_file = resolve_goal_state(
            registry=load_registry(registry_path), goal_id=goal_id,
            project_override=None, state_file_override=None)
        source_bytes = state_file.read_bytes()
        text = source_bytes.decode("utf-8")
        content: dict[str, Any] = {"state_file": str(state_file),
            "revision": "sha256:" + sha256(source_bytes).hexdigest()}
        # Only marked sources can exclude generated blocks and lines that
        # match the archive or Monitor codec. Unmarked or mixed sources still
        # require the original full read.
        lines = text.splitlines(keepends=True)
        visible = visible_markdown_lines(lines)
        regions = find_todo_regions(lines, visible=visible)
        if regions and all(region.marked for region in regions):
            todo_ranges = [(region.start + 1, region.end, region.role) for region in regions]
            skipped = {index for region in regions for index in range(region.start, region.end)}
            archive_ranges: list[list[int]] = []
            receipt_ranges: list[list[int]] = []
            for index in sorted(visible - skipped):
                if is_sqlite_archive_summary_pointer(lines[index], goal_id=goal_id):
                    ranges = archive_ranges
                elif is_monitor_receipt_line(lines[index], goal_id=goal_id):
                    ranges = receipt_ranges
                else:
                    continue
                line = index + 1
                if ranges and ranges[-1][1] == line - 1:
                    ranges[-1][1] = line
                else:
                    ranges.append([line, line])
                skipped.add(index)
            todo_ranges.extend((start, end, "archive") for start, end in archive_ranges)
            content.update(text="".join(line for index, line in enumerate(lines) if index not in skipped),
                excluded_todo_regions=[{
                    "role": role, "start_line": start, "end_line": end,
                    "revision": "sha256:" + sha256(
                        "".join(lines[start - 1:end]).encode("utf-8")).hexdigest(),
                } for start, end, role in sorted(todo_ranges)])
            if receipt_ranges:
                # The complete source revision already binds every byte and
                # interval. Repeating a separate hash for each historical line
                # grows the current prompt without adding recovery information.
                content["excluded_monitor_receipts"] = {
                    "count": sum(end - start + 1 for start, end in receipt_ranges),
                    "line_ranges": receipt_ranges,
                }
        if inline_goal_source and "text" not in content:
            content.update(text=text, full_source=True)
        return content
    if source == "goal_acceptance":
        # The typed acceptance owner scopes task-binding observations while
        # retaining the complete Goal contract and cumulative outcomes. The
        # selected-Todo read owns its body; do not transport it a second time
        # through the bounded acceptance RPC before discarding it here.
        return inspect_goal_acceptance(registry_path=registry_path, goal_id=goal_id,
            runtime_root=str(runtime_root), todo_id=todo_id, context_agent_id=agent_id, include_work_details=False)
    raise ValueError("unregistered work context source")


def _user_context(
    *, registry_path: Path, runtime_root: Path, goal_id: str, agent_id: str,
) -> dict[str, Any]:
    # Reuse the canonical scope owner (global/blocks_agent/bound_agent and
    # legacy scoping). The typed owner restores full text after scope filtering
    # from this same canonical snapshot, not a separate read for each body.
    inventory = list_goal_todos(registry_path=registry_path, goal_id=goal_id,
        role="user", status="open", agent_id=agent_id,
        runtime_root_arg=str(runtime_root), full_text=True)
    return {"source": inventory["source"], "authority_read": inventory.get("authority_read"),
        "todos": inventory["todos"]}


def attach_work_context(payload: dict[str, Any], *, registry_path: Path,
                        runtime_root: Path, hook_dispatch: dict[str, Any] | None,
                        context_navigation: bool = False,
                        inline_goal_source: bool = False) -> None:
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
    # The typed planner needs the selection inputs, not the admission body
    # that the public reader and final capture continue to own.
    cli = interaction.get("cli_channel")
    orchestration = payload.get("task_orchestration_contract")
    plan = effect_runtime_result("work_item.context.plan", {"packet": {
        "selected_todo": packet_selected,
        "effective_action": payload.get("effective_action"),
        "task_orchestration_contract": {
            key: orchestration.get(key) for key in ("schema_version", "mode", "primary_todo_id")
        } if isinstance(orchestration, dict) else {},
        "interaction_contract": {"cli_channel": {
            "selection_required": cli.get("selection_required"),
        } if isinstance(cli, dict) else {}},
    }})
    selected = plan.get("selected_todo")
    if isinstance(selected, dict) and isinstance(selected_text_snapshot, str):
        selected["_context_text_sha256"] = selected_text_snapshot
    results = [dict(item) for item in (hook_dispatch or {}).get("contexts", [])]
    for read in reads:
        if read.get("source") not in {"goal_state", "goal_acceptance"}:
            continue
        result = {"command": read["command"]}
        try:
            result["content"] = _source_content(read, registry_path=registry_path,
                runtime_root=runtime_root, goal_id=goal_id, todo_id=plan.get("todo_id"),
                agent_id=agent_id if context_navigation else None, inline_goal_source=inline_goal_source)
        except (OSError, ValueError, RuntimeError):
            result["error_code"] = "context_source_unavailable"
        results.append(result)
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
        "source_context": {"runtime_root": str(runtime_root.expanduser().resolve(strict=False)), "goal_id": goal_id},
        **({"require_full_goal_source": True} if inline_goal_source else {}),
    }, large_local_snapshot=True, timeout=(
        LOCAL_COORDINATION_TODO_LIST_TIMEOUT_SECONDS
        if any(read.get("source") == "selected_todo" for read in reads)
        else DEFAULT_REQUEST_TIMEOUT_SECONDS
    ))
    # An older runtime must not silently ignore the requested delivery profile.
    profile = projected.pop("source_delivery_profile", None)
    if inline_goal_source and profile != "complete_goal_v1":
        raise RuntimeError("Complete Goal source delivery is unsupported by this runtime")
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
