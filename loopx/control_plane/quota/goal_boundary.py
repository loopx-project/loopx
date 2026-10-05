from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ...boundary_authority import checkpointed_boundary_authority_summary
from ...execution_profile import execution_profile_outcome_floor
from ...explore_graph import compact_explore_graph_policy
from ..projects.registry_codec import load_registry
from ...orchestration import (
    compact_orchestration_policy,
    compact_peer_task_coordination_policy,
)
from ...repository_identity import resolve_project_identity
from ..operator_inbox_binding import operator_inbox_binding
from ..reward_memory import reward_memory_goal_policy
from ..todos.contract import (
    normalize_required_capabilities,
    normalize_required_write_scopes,
)


def quota_execution_profile_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact: dict[str, Any] = {}
    for field in ("cadence", "minimum_scale", "spend_rule"):
        if value.get(field):
            compact[field] = value[field]
    must_include = value.get("must_include")
    if isinstance(must_include, list) and must_include:
        compact["must_include"] = [str(item) for item in must_include[:3]]
    policy = (
        value.get("degradation_policy")
        if isinstance(value.get("degradation_policy"), dict)
        else {}
    )
    if policy.get("small_scale_streak_threshold") is not None:
        compact["small_scale_streak_threshold"] = policy.get(
            "small_scale_streak_threshold"
        )
    floor = execution_profile_outcome_floor(value)
    if floor:
        outcome_markers = (
            floor.get("outcome_markers")
            if isinstance(floor.get("outcome_markers"), list)
            else []
        )
        surface_hints = (
            floor.get("surface_only_hints")
            if isinstance(floor.get("surface_only_hints"), list)
            else []
        )
        compact["outcome_floor"] = {
            "configured": bool(outcome_markers or surface_hints),
            "surface_streak_threshold": floor.get("surface_streak_threshold"),
            "must_advance": [
                str(item)
                for item in (
                    floor.get("must_advance")
                    if isinstance(floor.get("must_advance"), list)
                    else []
                )[:2]
            ],
        }
    return compact or None


def registry_goal_by_id(
    status_payload: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return registry goals indexed by goal id from a status payload."""

    registry_value = status_payload.get("registry")
    if not registry_value:
        return {}
    registry_path = Path(str(registry_value)).expanduser()
    try:
        payload = load_registry(registry_path)
    except (OSError, ValueError):
        return {}
    goals = payload.get("goals")
    if not isinstance(goals, list):
        return {}
    return {
        str(goal.get("id") or ""): goal
        for goal in goals
        if isinstance(goal, dict) and goal.get("id")
    }


def quota_execution_profile_boundary_summary(value: Any) -> dict[str, Any] | None:
    summary = quota_execution_profile_summary(value)
    if not summary:
        return None
    compact = {}
    if summary.get("minimum_scale"):
        compact["minimum_scale"] = summary["minimum_scale"]
    return compact or None


def _lark_kanban_post_writeback_projection(
    goal: Mapping[str, Any],
    control_plane: Mapping[str, Any],
    *,
    registry_path: Path | None,
) -> dict[str, Any]:
    lark_kanban = (
        control_plane.get("lark_kanban")
        if isinstance(control_plane.get("lark_kanban"), dict)
        else {}
    )
    if lark_kanban.get("heartbeat_sync_enabled") is not True:
        return {}

    command = ["loopx"]
    if registry_path is not None:
        command.extend(["--registry", str(registry_path.expanduser())])
    command.extend(
        [
            "lark-kanban",
            "sync-loopx-todos",
            "--goal-id",
            str(goal.get("id") or goal.get("goal_id") or ""),
        ]
    )
    project = str(goal.get("repo") or "").strip()
    if project:
        command.extend(["--project", project])
    command.append("--execute")
    return {
        "post_writeback_actions": [
            {
                "action_id": "lark_kanban_sync",
                "trigger": "material_state_change",
                "command": shlex.join(command),
                "failure_policy": "nonblocking_no_p0_preemption",
            }
        ]
    }


def _registry_boundary_projection(goal: Mapping[str, Any]) -> dict[str, Any]:
    boundary: dict[str, Any] = {}
    adapter_kind, adapter_status = (
        goal.get("adapter_kind"),
        goal.get("adapter_status"),
    )
    if adapter_kind or adapter_status:
        boundary["adapter"] = {
            "kind": adapter_kind,
            "status": adapter_status,
        }
    coordination_value = goal.get("coordination")
    coordination: dict[str, Any] = (
        coordination_value if isinstance(coordination_value, dict) else {}
    )
    write_scope_value = coordination.get("write_scope")
    write_scope = write_scope_value if isinstance(write_scope_value, list) else []
    normalized_write_scope: list[str] = []
    for value in write_scope:
        scope = str(value).strip()
        if scope and scope not in normalized_write_scope:
            normalized_write_scope.append(scope)
    boundary_authority = checkpointed_boundary_authority_summary(coordination)
    if boundary_authority:
        for scope in normalize_required_write_scopes(
            boundary_authority.get("active_write_scope")
        ):
            if scope not in normalized_write_scope:
                normalized_write_scope.append(scope)
        boundary["checkpointed_boundary_authority"] = boundary_authority
    if normalized_write_scope:
        boundary["write_scope"] = normalized_write_scope
    available_capabilities = declared_available_capabilities(goal)
    if available_capabilities:
        boundary["available_capabilities"] = available_capabilities
    requires_approval_value = coordination.get("requires_parent_approval")
    requires_approval = (
        requires_approval_value if isinstance(requires_approval_value, list) else []
    )
    if requires_approval:
        boundary["requires_parent_approval"] = [
            str(value) for value in requires_approval if str(value).strip()
        ]
    peer_task_coordination = compact_peer_task_coordination_policy(coordination)
    if peer_task_coordination:
        boundary["peer_task_coordination"] = peer_task_coordination
    guards = goal.get("guards") if isinstance(goal.get("guards"), list) else []
    if guards:
        boundary["guards"] = [str(value) for value in guards if str(value).strip()]
    return boundary


def _reward_memory_enablement_projection(
    status: Mapping[str, Any],
) -> dict[str, Any]:
    fields = (
        "reason_code",
        "repair",
        "isolation_mode",
        "enablement_receipt_status",
        "actor_binding_verified",
        "writability_verified",
        "exact_readback_verified",
    )
    return {field: status[field] for field in fields if field in status}


def _reward_memory_automation_projection(
    status: Mapping[str, Any],
) -> dict[str, Any]:
    automation_intent = status.get("automation_intent")
    host_coverage = status.get("host_coverage")
    return {
        "automation_intent": (
            dict(automation_intent) if isinstance(automation_intent, Mapping) else {}
        ),
        "host_coverage": list(host_coverage) if isinstance(host_coverage, list) else [],
    }


def goal_boundary(
    goal: dict[str, Any],
    item: dict[str, Any] | None = None,
    *,
    agent_id: str | None = None,
    registry_path: Path | None = None,
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]] | None = None,
    reward_memory_experiment_status: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    boundary = _registry_boundary_projection(goal)
    control_plane = (
        goal.get("control_plane") if isinstance(goal.get("control_plane"), dict) else {}
    )
    issue_fix = (
        control_plane.get("issue_fix")
        if isinstance(control_plane.get("issue_fix"), dict)
        else {}
    )
    reviewer_notification = (
        issue_fix.get("reviewer_notification")
        if isinstance(issue_fix.get("reviewer_notification"), dict)
        else {}
    )
    if reviewer_notification.get("enabled") is True:
        boundary.setdefault("capabilities", {})["issue_fix_reviewer_notification"] = {
            "enabled": True,
            "config_pointer_registered": bool(reviewer_notification.get("config_path")),
        }
    agent_inboxes = (
        control_plane.get("lark_event_inboxes")
        if isinstance(control_plane.get("lark_event_inboxes"), dict)
        else {}
    )
    agent_inbox = agent_inboxes.get(agent_id) if agent_id else None
    lark_event_inbox = (
        agent_inbox
        if isinstance(agent_inbox, dict)
        else control_plane.get("lark_event_inbox")
        if isinstance(control_plane.get("lark_event_inbox"), dict)
        else {}
    )
    if lark_event_inbox.get("enabled") is True:
        drain_parts = ["loopx"]
        if registry_path is not None:
            drain_parts.extend(["--registry", str(registry_path.expanduser())])
        drain_parts.extend(
            [
                "lark-inbox",
                "drain",
                "--goal-id",
                str(goal.get("id") or ""),
            ]
        )
        if isinstance(agent_inbox, dict) and agent_id:
            drain_parts.extend(["--agent-id", agent_id])
        drain_command = shlex.join(drain_parts)
        inbox_capability: dict[str, Any] = {
            "enabled": True,
            "config_pointer_registered": bool(lark_event_inbox.get("config_path")),
            "drain_command": drain_command,
        }
        project = Path(str(goal.get("repo") or "")).expanduser()
        config_path = str(lark_event_inbox.get("config_path") or "").strip()
        binding = operator_inbox_binding(
            project=project,
            config_path=config_path,
            expected_digest=lark_event_inbox.get("config_digest"),
        )
        inbox_capability["binding"] = binding
        owner_binding_blocked = bool(
            isinstance(agent_inbox, dict) and binding["attention_required"]
        )
        if owner_binding_blocked:
            inbox_capability.pop("drain_command", None)
            inbox_capability["urgency"] = {
                "schema_version": "lark_event_inbox_urgency_v0",
                "enabled": True,
                "projection_status": "unavailable",
                "blocker": "agent_lark_inbox_config_binding_drift",
                "local_private_content_returned": False,
            }
        elif project.is_dir() and config_path and operator_inbox_urgency_projector:
            try:
                urgency = operator_inbox_urgency_projector(
                    project=project,
                    config_path=config_path,
                )
                inbox_capability["urgency"] = urgency
            except (OSError, ValueError):
                inbox_capability["urgency"] = {
                    "schema_version": "lark_event_inbox_urgency_v0",
                    "enabled": True,
                    "projection_status": "unavailable",
                    "local_private_content_returned": False,
                }
        elif config_path:
            inbox_capability["urgency"] = {
                "schema_version": "lark_event_inbox_urgency_v0",
                "enabled": True,
                "projection_status": "unavailable",
                "local_private_content_returned": False,
            }
        boundary.setdefault("capabilities", {})["lark_event_inbox"] = inbox_capability
    boundary.update(
        _lark_kanban_post_writeback_projection(
            goal,
            control_plane,
            registry_path=registry_path,
        )
    )
    reward_memory = reward_memory_goal_policy(goal)
    if reward_memory["enabled"] and (
        agent_id is None or agent_id in reward_memory["enabled_agents"]
    ):
        reward_capability: dict[str, Any] = {
            "enabled": True,
            "experimental": True,
            "config_pointer_registered": bool(reward_memory["config_path"]),
            "enabled_agents": list(reward_memory["enabled_agents"]),
            "automatic_ingest": False,
            "automatic_recall": False,
            "automation_projection_source": "default_off_unresolved",
        }
        if (
            agent_id is not None
            and isinstance(reward_memory_experiment_status, Mapping)
            and reward_memory_experiment_status.get("goal_id") == goal.get("id")
            and reward_memory_experiment_status.get("agent_id") == agent_id
        ):
            reward_capability.update(
                {
                    "experiment_status": str(
                        reward_memory_experiment_status.get("status") or "unavailable"
                    ),
                    "experiment_available": (
                        reward_memory_experiment_status.get("available") is True
                    ),
                    "automatic_ingest": (
                        reward_memory_experiment_status.get("automatic_ingest") is True
                    ),
                    "automatic_recall": (
                        reward_memory_experiment_status.get("automatic_recall") is True
                    ),
                    "fail_open": reward_memory_experiment_status.get("fail_open")
                    is not False,
                    "automation_projection_source": (
                        "reward_memory_experiment_status_v1"
                    ),
                    **_reward_memory_automation_projection(
                        reward_memory_experiment_status
                    ),
                }
            )
            reward_capability.update(
                _reward_memory_enablement_projection(reward_memory_experiment_status)
            )
            if reward_memory_experiment_status.get("config_schema_version"):
                reward_capability["config_schema_version"] = str(
                    reward_memory_experiment_status["config_schema_version"]
                )
            config_runtime_route = reward_memory_experiment_status.get(
                "config_runtime_route"
            )
            if isinstance(config_runtime_route, Mapping):
                reward_capability["config_runtime_route"] = dict(config_runtime_route)
        if agent_id is not None:
            reward_capability.update(
                {
                    "configured_for_agent": True,
                    "status_command": shlex.join(
                        [
                            "loopx",
                            "reward-memory",
                            "experiment-status",
                            "--goal-id",
                            str(goal.get("id") or ""),
                            "--agent-id",
                            agent_id,
                        ]
                    ),
                    "ingest_command": shlex.join(
                        [
                            "loopx",
                            "reward-memory",
                            "ingest-event",
                            "--goal-id",
                            str(goal.get("id") or ""),
                            "--agent-id",
                            agent_id,
                            "--input",
                            "<compact-event.json>",
                        ]
                    ),
                }
            )
        boundary.setdefault("capabilities", {})["reward_memory"] = reward_capability
    if goal.get("next_probe"):
        boundary["next_probe"] = str(goal.get("next_probe"))
    if isinstance(goal.get("explore_graph"), dict) or (goal.get("spawn_policy") or {}).get("explore_harness"):
        boundary["explore_graph"] = compact_explore_graph_policy(
            goal.get("explore_graph"), (goal.get("spawn_policy") or {}).get("explore_harness")
        )
    spawn_policy = (
        goal.get("spawn_policy") if isinstance(goal.get("spawn_policy"), dict) else None
    )
    if spawn_policy is not None:
        orchestration = compact_orchestration_policy(spawn_policy)
        boundary["orchestration"] = orchestration
        project = str(goal.get("repo") or "").strip()
        if orchestration.get("spawn_allowed") is True and project:
            try:
                repository_identity = resolve_project_identity(
                    project,
                    loopx_project_id=str(goal.get("id") or "").strip() or None,
                )
            except ValueError:
                repository_identity = None
            if repository_identity and repository_identity.startswith("git:"):
                boundary["task_repository"] = repository_identity
    project_asset_source = item if item is not None else goal
    for policy_source in (goal, project_asset_source):
        if not isinstance(policy_source, dict):
            continue
            break
    if isinstance(project_asset_source, dict) and project_asset_source.get(
        "project_asset"
    ):
        project_asset = project_asset_source.get("project_asset")
        if isinstance(project_asset, dict):
            if project_asset.get("stop_condition"):
                boundary["stop_condition"] = project_asset.get("stop_condition")
            if isinstance(project_asset.get("execution_profile"), dict):
                boundary["execution_profile"] = (
                    quota_execution_profile_boundary_summary(
                        project_asset["execution_profile"]
                    )
                )
            if isinstance(project_asset.get("orchestration"), dict):
                boundary["orchestration"] = compact_orchestration_policy(
                    project_asset["orchestration"]
                )
    # Model preferences belong to the current registry, not a stale asset snapshot.
    if spawn_policy is not None and "orchestration" in boundary:
        model_config = compact_orchestration_policy(spawn_policy).get("model_config")
        if model_config is not None:
            boundary["orchestration"]["model_config"] = model_config
        else:
            boundary["orchestration"].pop("model_config", None)
    if boundary:
        boundary["rule"] = "stay_in_scope_or_stop"
        return boundary
    return None


def declared_available_capabilities(source: Any) -> list[str]:
    if not isinstance(source, dict):
        return []
    capabilities: list[str] = []

    def append(raw: Any) -> None:
        for capability in normalize_required_capabilities(raw):
            if capability not in capabilities:
                capabilities.append(capability)

    append(source.get("available_capabilities"))
    coordination = (
        source.get("coordination")
        if isinstance(source.get("coordination"), dict)
        else {}
    )
    append(coordination.get("available_capabilities"))
    project_asset = (
        source.get("project_asset")
        if isinstance(source.get("project_asset"), dict)
        else {}
    )
    append(project_asset.get("available_capabilities"))
    return capabilities
