"""Continuation-aware Todo authoring for guided goal starts.

A guided `start-goal` that resolves to an existing-Agent takeover must not
re-plan runnable work that already exists in the goal's active state. The
guided packet therefore projects a conditional Todo delta (reuse / update /
link successor / add new) instead of unconditional Todo planning and
authoring. Whenever the runnable frontier cannot be proven, callers keep the
unconditional planning contract — fail-closed.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..coordination.local_authority import read_canonical_todo_fields_if_promoted
from ...history import load_registry
from ...paths import resolve_runtime_root
from ...control_plane.todos.contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
)
from ...control_plane.todos.todo_semantics import todo_item_is_actionable_open
from ...project_prompt import render_cli_command_prefix, shell_arg
from ...registry import registry_goals
from .acceptance import inspect_goal_acceptance

GUIDED_TODO_DELTA_SCHEMA_VERSION = "loopx_guided_todo_delta_v0"

_FRONTIER_PROJECTION_LIMIT = 1


def _connected_goal_todos(
    inspection: Mapping[str, Any],
    *,
    resolved_goal_id: str,
    runtime_root_arg: str | None = None,
    registry_payload: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]] | None:
    if inspection.get("connection_state") != "connected":
        return None
    registry_path = Path(str(inspection.get("registry") or ""))
    if registry_payload is None:
        registry_payload, _registry_error = _read_registry(registry_path)
    registry_goal = next(
        (
            goal
            for goal in registry_goals(registry_payload or {})
            if str(goal.get("id")) == resolved_goal_id
        ),
        None,
    )
    if registry_goal is None:
        return None
    resolved_runtime_root = resolve_runtime_root(
        registry_payload or {}, runtime_root_arg, registry_path=registry_path,
    )
    parsed = read_canonical_todo_fields_if_promoted(
        runtime_root=resolved_runtime_root,
        goal_id=resolved_goal_id,
    )
    return registry_path, registry_goal, resolved_runtime_root, parsed


def existing_runnable_agent_frontier(
    inspection: Mapping[str, Any],
    *,
    resolved_goal_id: str,
    effective_agent_id: str | None,
    runtime_root_arg: str | None = None,
    registry_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    """Runnable advancement agent Todos already present in the goal's state.

    Only open advancement Todos (``status: open``, ``task_class:
    advancement_task``) belonging to the effective agent (or unclaimed)
    whose resume condition is satisfied (or absent) enter the frontier.
    Blocked, deferred, monitor, blocker, resume-blocked, or peer-claimed
    Todos never enter the frontier. Returns ``None`` whenever the frontier
    is not connected, the Goal is unknown, or nothing is runnable, so callers
    keep the unconditional planning contract. Only canonical records count;
    an unmigrated Goal or unavailable provider raises instead of being mistaken
    for an empty frontier. A missing display is safe
    to ignore, not permission to reconstruct its non-Todo narrative.
    When supplied, registry_payload is the decoded registry for this preview.
    """
    connected = _connected_goal_todos(
        inspection,
        resolved_goal_id=resolved_goal_id,
        runtime_root_arg=runtime_root_arg,
        registry_payload=registry_payload,
    )
    if connected is None:
        return None
    _registry_path, _registry_goal, _resolved_runtime_root, parsed = connected
    agent_summary = parsed.get("agent_todos") if isinstance(parsed, dict) else None
    items = (
        agent_summary.get("items", []) if isinstance(agent_summary, dict) else []
    )
    runnable = [
        item
        for item in items
        if isinstance(item, dict)
        and todo_item_is_actionable_open(item)
        and item.get("task_class") == TODO_TASK_CLASS_ADVANCEMENT
    ]
    runnable = [
        item
        for item in runnable
        if not item.get("claimed_by")
        or (effective_agent_id and str(item.get("claimed_by")) == effective_agent_id)
    ]
    return runnable or None


def start_goal_initial_batch_applicable(
    inspection: Mapping[str, Any],
    *,
    resolved_goal_id: str,
    runtime_root_arg: str | None = None,
    registry_payload: dict[str, Any] | None = None,
) -> bool:
    """Whether initial start-goal planning can offer initial plan-batch authoring."""
    connection_state = inspection.get("connection_state")
    if connection_state in {"not_connected", "registry_without_goal"}:
        return True
    connected = _connected_goal_todos(
        inspection,
        resolved_goal_id=resolved_goal_id,
        runtime_root_arg=runtime_root_arg,
        registry_payload=registry_payload,
    )
    if connected is None:
        return False
    registry_path, registry_goal, resolved_runtime_root, parsed = connected
    if registry_goal.get("waiting_on"):
        return False
    for role_key in ("agent_todos", "user_todos"):
        summary = parsed.get(role_key) if isinstance(parsed, dict) else None
        items = summary.get("items", []) if isinstance(summary, dict) else []
        if items:
            return False
    acceptance = inspect_goal_acceptance(
        registry_path=registry_path,
        goal_id=resolved_goal_id,
        runtime_root=str(resolved_runtime_root),
        include_work_details=False,
    )
    outcomes = (
        (acceptance.get("goal_acceptance_contract") or {})
        .get("outcome_requirements", {})
        .get("outcomes", [])
    )
    return not outcomes


def _todo_add_command_template(
    *,
    cli_bin: str,
    runtime_root: str | Path | None,
    goal_id: str,
    agent_id: str | None,
    registry_path: Path | None = None,
) -> str:
    return (
        f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)} "
        + (f"--registry {shell_arg(str(registry_path))} " if registry_path is not None else "")
        + "todo add --goal-id "
        f"{shell_arg(str(goal_id or ''))} "
        "--project . "
        "--role agent "
        + (
            f"--claimed-by {shell_arg(str(agent_id or ''))} "
            if agent_id
            else "--claimed-by <agent-id> "
        )
        + "--task-class advancement_task --action-kind <action_kind> "
        "[--target-key <target_key>] --text '<[P0/P1/P2] ...>'"
    )


def todo_authoring_steps(
    *,
    existing_runnable_frontier: list[dict[str, Any]] | None,
    plan_prompt: str | None,
    fine_grained: bool,
    cli_bin: str,
    runtime_root: str | Path | None,
    goal_id: str,
    agent_id: str | None,
    registry_path: Path | None = None,
    planning_text: str | None = None,
    batch_applicable: bool = False,
) -> list[dict[str, Any]]:
    """Ordered Todo-authoring steps, conditional on the runnable frontier."""
    add_template = _todo_add_command_template(
        cli_bin=cli_bin,
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        registry_path=registry_path,
    )
    prefix = render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)
    if registry_path is not None:
        prefix += f" --registry {shell_arg(str(registry_path))}"
    acceptance = f"{prefix} --format json goal-acceptance"
    author = agent_id or "<agent-id>"
    route = f"--goal-id {shell_arg(str(goal_id))} --agent-id {shell_arg(author)}"
    # The planner supplies intent; goal-acceptance still validates each refinement.
    requirement_authoring = {
        "inspect_command": f"{acceptance} inspect {route}",
        "refine_command_template": (
            f"{acceptance} refine {route} --document '<agent-authored-file>' "
            "--expected-provider-revision '<current-provider-revision>' "
            "--operation-id '<stable-original-operation-id>' --execute"
        ),
        "declaration_document_template": {
            "outcome_id": "<outcome-id>",
            "owner_agent_id": author,
            "objective": "<outcome-objective>",
            "requirements": [
                {"id": "<requirement-id>", "description": "<requirement-description>"}
            ],
            "source": {"ref": "<source-ref>", "summary": "<source-summary>"},
        },
        "link_document_template": {
            "outcome_id": "<same-outcome-id>",
            "todo_ids": ["<actual-todo-id>"],
        },
        "revision_guidance": (
            "Use provider_revision from current acceptance or inspect. Preserve owner and objective "
            "when refining an existing outcome. After Todo writes, inspect again before linking. "
            "Give each refinement its own stable operation id. Retry it unchanged after response loss."
        ),
    }
    # This adapter already owns routed CLI templates for both planning entrypoints.
    readback = {
        "id": "read_back_authored_todos",
        "kind": "operator_or_agent_actions",
        "command_template": (
            f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)} "
            + (f"--registry {shell_arg(str(registry_path))} " if registry_path is not None else "")
            + f"--format json todo list --goal-id {shell_arg(str(goal_id))} "
            + (f"--agent-id {shell_arg(str(agent_id))} " if agent_id else "")
            + "--todo-id '<todo-id>'"
        ),
        "purpose": (
            "Read each authored/reused id: one match, todo_detail_projection.source_complete=true; "
            "compare .todo.text, status/claim. Excerpts cannot verify writes. "
            "Read back the same overall requirements, provenance and work links too; "
            "missing/ambiguous/changed: reinspect before handoff. Readback grants no guard/lease authority."
        ),
    }
    readback["goal_requirements_readback"] = (
        "Repeat goal_requirements.inspect_command from the planning checkpoint after linking."
    )
    if not existing_runnable_frontier:
        batch_prefix = f"{prefix} --format json todo plan-batch"
        text_arg = f" --text={shell_arg(planning_text)}" if planning_text is not None else ""
        write_step: dict[str, Any] = {
            "id": "write_ordered_todos",
            "kind": "operator_or_agent_actions",
            "command_template": add_template,
        }
        if batch_applicable:
            write_step["batch_command_template"] = (
                f"{batch_prefix} {route} --project .{text_arg} "
                "--document '<proposal.json>' --execute"
            )
            write_step["batch_schema_command"] = (
                f"{batch_prefix} --schema {route}{text_arg}"
                if planning_text is not None
                else f"{batch_prefix} --schema"
            )
        write_step["purpose"] = (
            "write only the current checkpoint Todo; the existing replan path "
            "qualifies any successor after completion evidence"
            if fine_grained
            else (
                "write todos in planner order (or submit one initial outcome + Todo batch via "
                "batch_command_template when requirements and Todos are both empty); capability "
                "successors preserve the admitted action_kind and target_key for later quota re-entry"
                if batch_applicable
                else "write todos in planner order; capability successors preserve "
                "the admitted action_kind and target_key for later quota re-entry"
            )
        )
        return [
            {
                "id": "plan_ranked_todos",
                "kind": "model_checkpoint",
                "prompt": plan_prompt,
                "goal_requirements": requirement_authoring,
                "purpose": (
                    "produce one public-safe, small, verifiable checkpoint Todo; keep "
                    "later options as evidence-linked planning notes"
                    if fine_grained
                    else "produce concise public-safe P0/P1/P2 todos before todo writeback"
                ),
            },
            write_step,
            readback,
        ]
    return [
        {
            "id": "compare_planned_todos_with_frontier",
            "kind": "model_checkpoint",
            "prompt": plan_prompt,
            "goal_requirements": requirement_authoring,
            "purpose": (
                "compare the continuation with existing_runnable_frontier "
                "by text/action_kind before any writeback"
            ),
        },
        {
            "id": "apply_todo_delta",
            "kind": "operator_or_agent_actions",
            "todo_delta": {
                "schema_version": GUIDED_TODO_DELTA_SCHEMA_VERSION,
                "rule": (
                    "reuse|update|link_successor|add_new; "
                    "reuse when the frontier covers the request, update instead "
                    "of duplicating, link a successor only after completion "
                    "evidence, add_new only for uncovered work"
                ),
                "runnable_frontier_count": len(existing_runnable_frontier),
                "frontier": [
                    f"{item.get('title') or item.get('text')} "
                    f"[{item.get('todo_id')}, {item.get('claimed_by') or 'unclaimed'}]"
                    for item in existing_runnable_frontier[:_FRONTIER_PROJECTION_LIMIT]
                ],
            },
            "add_new_command_template": add_template,
            "purpose": "takeover continues the frontier; authoring is a delta",
        },
        readback,
    ]


def append_todo_delta_render_line(
    raw_step: Mapping[str, Any],
    step_lines: list[str],
) -> None:
    """Keep planning instructions reachable without repeating routed commands."""
    requirements = raw_step.get("goal_requirements")
    if isinstance(requirements, Mapping):
        step_lines.append(
            "   - overall requirements: reuse existing or declare missing requirements before Todos, link actual ids, then read back; "
            "use this checkpoint's goal_requirements commands and document templates in the JSON detail packet."
        )
    todo_delta = raw_step.get("todo_delta")
    if not isinstance(todo_delta, Mapping):
        return
    step_lines.append(
        f"   - todo_delta: {todo_delta.get('rule')} "
        f"(runnable frontier: {todo_delta.get('runnable_frontier_count')})"
    )


def _read_registry(registry_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not registry_path.is_file():
        return None, "unreadable"
    try:
        payload = load_registry(registry_path)
    except (OSError, ValueError):
        return None, "unreadable"
    return payload, None
