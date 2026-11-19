from __future__ import annotations

from typing import Any, cast

from ..agents.runtime_model import normalized_peer_agent_ids
from ..effect_runtime import effect_runtime_result
from ..todos.contract import (
    normalize_required_capabilities,
    normalize_required_write_scopes,
    normalize_target_capabilities,
    normalize_todo_claimed_by,
    normalize_todo_excluded_agents,
    normalize_todo_resume_when,
    normalize_todo_status,
    normalize_todo_task_domain,
    normalize_todo_task_repository,
)
from ..work_items.primary_action import protocol_action_text


def _source_items(items: Any, summary: Any) -> list[dict[str, Any]]:
    if items is not None:
        values = items
    elif isinstance(summary, dict):
        values = summary.get("items", [])
    else:
        values = []
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, dict)]


def _optional_text(value: Any) -> str | None:
    return str(value or "").strip() or None


def build_adaptive_task_orchestration_contract(
    *,
    agent_id: str,
    agent_identity: dict[str, Any],
    goal_boundary: dict[str, Any],
    orchestration: dict[str, Any],
    raw_agent_todo_summary: dict[str, Any] | None,
    raw_user_todo_summary: dict[str, Any] | None,
    agent_todo_source_items: list[dict[str, Any]] | None,
    user_todo_source_items: list[dict[str, Any]] | None,
    available_capabilities: list[str],
    parent_goal_id: str | None,
    max_children: int,
) -> dict[str, Any] | None:
    items = []
    for item in _source_items(agent_todo_source_items, raw_agent_todo_summary):
        items.append(
            {
                **{
                    key: item.get(key)
                    for key in (
                        "done",
                        "resume_ready",
                        "goal_acceptance_guard",
                        "validation_command",
                        "validation_command_argv",
                        "completion_validation_required",
                    )
                },
                **{
                    key: _optional_text(item.get(key))
                    for key in (
                        "todo_id",
                        "action_kind",
                        "continuation_policy",
                        "target_key",
                        "validation_label",
                    )
                },
                "dependency_todo_id": str(item.get("todo_id") or ""),
                "task_class": str(item.get("task_class") or ""),
                "status": normalize_todo_status(item.get("status")) or "open",
                "resume_when": normalize_todo_resume_when(item.get("resume_when")),
                "claimed_by": normalize_todo_claimed_by(item.get("claimed_by")),
                "excluded_agents": normalize_todo_excluded_agents(
                    item.get("excluded_agents")
                ),
                "task_domain": normalize_todo_task_domain(item.get("task_domain")),
                "task_repository": normalize_todo_task_repository(
                    item.get("task_repository")
                ),
                "repository_declared": bool(item.get("task_repository")),
                "required_write_scopes": normalize_required_write_scopes(
                    item.get("required_write_scopes")
                ),
                "required_capabilities": normalize_required_capabilities(
                    item.get("required_capabilities")
                ),
                "target_capabilities": normalize_target_capabilities(
                    item.get("target_capabilities")
                ),
                "objective": protocol_action_text(
                    item.get("title") or item.get("text"), limit=180
                ),
            }
        )
    raw_domains = orchestration.get("allowed_domains")
    allowed_domains = None
    if isinstance(raw_domains, list) and raw_domains:
        normalized_domains = [
            normalize_todo_task_domain(value) for value in raw_domains
        ]
        allowed_domains = [domain for domain in normalized_domains if domain]
    contract = effect_runtime_result(
        "quota.task_orchestration.admit",
        {
            "agent_id": agent_id,
            "items": items,
            "user_items": [
                {
                    "unblocks_todo_id": str(item.get("unblocks_todo_id") or ""),
                    "done": item.get("done"),
                    "status": str(item.get("status") or "open"),
                }
                for item in _source_items(user_todo_source_items, raw_user_todo_summary)
            ],
            "registered_agents": normalized_peer_agent_ids(
                agent_identity.get("registered_agents") or [agent_id]
            ),
            "goal_repository": normalize_todo_task_repository(
                goal_boundary.get("task_repository")
            ),
            "goal_write_scope": normalize_required_write_scopes(
                goal_boundary.get("write_scope")
            ),
            "allowed_domains": allowed_domains,
            "available_capabilities": available_capabilities,
            "parent_goal_id": parent_goal_id,
            "max_children": int(max_children),
        },
    )
    return cast("dict[str, Any] | None", contract)
