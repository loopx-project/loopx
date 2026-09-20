from __future__ import annotations

import json
import math
import shlex
import shutil
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .agent_registry import normalize_registered_agents
from .boundary_authority import (
    build_checkpointed_boundary_authority_entry,
    checkpointed_boundary_authority_summary,
)
from .capabilities.pr_review_queue import goal_configuration as pr_review_config
from .capabilities.change_quality import goal_configuration as change_quality_config
from .capabilities.change_quality.policy import change_quality_goal_policy_summary
from .capabilities.machine_configuration.builtins import (
    builtin_machine_inheritable_goal_overrides,
)
from .capabilities.periodic_report import goal_configuration as periodic_report_config
from .capabilities.reward_memory.configuration import (
    apply_reward_memory_goal_configuration,
    plan_reward_memory_goal_configuration,
    reward_memory_preflight_markdown_lines,
)
from .configuration_catalog import (
    DEFAULT_MULTI_SUBAGENT_MAX_CHILDREN,
    build_goal_configuration_catalog,
)
from .control_plane import compact_control_plane_policy, control_plane_policy_summary
from .control_plane.agents.legacy_migration import (
    completed_peer_agent_runtime_migration,
    legacy_agent_hierarchy_present,
    migrate_coordination_to_peer_v1,
    peer_agent_runtime_migration_completed,
    peer_agent_runtime_migration_id,
)
from .control_plane.agents.profile import normalize_agent_profile
from .control_plane.agents.runtime_model import (
    AgentRuntimeModel,
    agent_runtime_model_for_goal,
)
from .control_plane.agents.supervisor import normalize_peer_supervisor
from .control_plane.agents.work_mode import normalize_agent_work_modes
from .control_plane.coordination import local_authority_shadow_observation as shadow
from .control_plane.coordination.configuration import normalize_goal_write_scope
from .control_plane.operator_inbox_binding import local_private_config_digest
from .control_plane.reward_memory import (
    reward_memory_goal_policy_summary,
)
from .control_plane.todos.contract import normalize_todo_claimed_by
from .control_plane.todos.mutation_authority import (
    normalize_todo_lifecycle_authority,
)
from .execution_profile import (
    apply_goal_execution_profile_change,
    compact_execution_profile,
)
from .explore_graph import compact_explore_graph_policy
from .orchestration import (
    EXPLORE_HARNESS_PROFILES,
    MULTI_SUBAGENT_ORCHESTRATION_MODE,
    compact_orchestration_policy,
    compact_peer_task_coordination_policy,
    orchestration_policy_summary,
    update_spawn_execution_policy,
)
from .quota import goal_quota_config
from .registry import atomic_write_json, read_json, registry_goals

WAITING_ON_CHOICES = (
    "codex",
    "user_or_controller",
    "controller",
    "external_evidence",
)

MULTI_SUBAGENT_FEATURE_CHOICES = ("off", "enabled")
AGENT_MODEL_CHOICES = tuple(model.value for model in AgentRuntimeModel)


def _control_plane(goal: dict[str, Any]) -> dict[str, Any]:
    value = goal.get("control_plane")
    return value if isinstance(value, dict) else {}


def _mutable_control_plane(goal: dict[str, Any]) -> dict[str, Any]:
    control_plane = _control_plane(goal)
    goal["control_plane"] = control_plane
    return control_plane


def _reviewer_notification_config_summary(goal: dict[str, Any]) -> dict[str, bool]:
    control_plane = _control_plane(goal)
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
    return {
        "enabled": reviewer_notification.get("enabled") is True,
        "config_pointer_registered": bool(reviewer_notification.get("config_path")),
    }


def _lark_event_inbox_config_summary(goal: dict[str, Any]) -> dict[str, Any]:
    control_plane = _control_plane(goal)
    inbox = (
        control_plane.get("lark_event_inbox")
        if isinstance(control_plane.get("lark_event_inbox"), dict)
        else {}
    )
    agent_inboxes = (
        control_plane.get("lark_event_inboxes")
        if isinstance(control_plane.get("lark_event_inboxes"), dict)
        else {}
    )
    enabled_agent_inboxes = {
        str(agent_id): value
        for agent_id, value in agent_inboxes.items()
        if isinstance(value, dict) and value.get("enabled") is True
    }
    return {
        "enabled": inbox.get("enabled") is True or bool(enabled_agent_inboxes),
        "config_pointer_registered": bool(inbox.get("config_path"))
        or any(
            bool(value.get("config_path")) for value in enabled_agent_inboxes.values()
        ),
        "agent_scoped_count": len(enabled_agent_inboxes),
        "agent_scoped_bound_count": sum(
            bool(value.get("config_digest")) for value in enabled_agent_inboxes.values()
        ),
    }


def _lark_kanban_heartbeat_config_summary(goal: dict[str, Any]) -> dict[str, bool]:
    control_plane = _control_plane(goal)
    lark_kanban = (
        control_plane.get("lark_kanban")
        if isinstance(control_plane.get("lark_kanban"), dict)
        else {}
    )
    return {"enabled": lark_kanban.get("heartbeat_sync_enabled") is True}


def _local_private_config_path(
    value: str | None, *, label: str = "local-private config"
) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\\", "/")
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or ".." in path.parts
        or len(path.parts) < 3
        or path.parts[:2] != (".loopx", "config")
        or path.suffix != ".json"
    ):
        raise ValueError(
            f"{label} must be a repo-relative JSON path under .loopx/config/"
        )
    return path.as_posix()


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _positive_number(value: float | None, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field} must be a finite number greater than 0")
    return float(value)


def _non_negative_number(value: float | None, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite number greater than or equal to 0")
    return float(value)


def _non_negative_int(value: int | None, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _clean_domains(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    domains: list[str] = []
    for value in values:
        for part in str(value).split(","):
            domain = part.strip()
            if domain and domain not in domains:
                domains.append(domain)
    return domains


def _clean_registered_agents(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    agents: list[str] = []
    for value in values:
        for part in str(value).split(","):
            raw_agent = part.strip()
            if not raw_agent:
                continue
            agent = normalize_todo_claimed_by(raw_agent)
            if not agent:
                raise ValueError(
                    "registered agents must be public-safe tokens such as codex-main-control"
                )
            if agent not in agents:
                agents.append(agent)
    return agents


def _settings_summary(goal: dict[str, Any]) -> dict[str, Any]:
    quota = goal_quota_config(goal)
    control_plane = compact_control_plane_policy(goal.get("control_plane"))
    orchestration = compact_orchestration_policy(goal.get("spawn_policy"))
    coordination = (
        goal.get("coordination") if isinstance(goal.get("coordination"), dict) else {}
    )
    agent_model = agent_runtime_model_for_goal(goal)
    registered_agents = normalize_registered_agents(
        coordination.get("registered_agents")
    )
    summary = {
        "execution_profile": compact_execution_profile(goal.get("execution_profile")),
        "quota": {
            "compute": quota.get("compute"),
            "window_hours": quota.get("window_hours"),
        },
        "control_plane": control_plane,
        "periodic_report": periodic_report_config.configuration_summary(goal),
        "issue_fix_reviewer_notification": _reviewer_notification_config_summary(goal),
        "lark_event_inbox": _lark_event_inbox_config_summary(goal),
        "lark_kanban_heartbeat_sync": _lark_kanban_heartbeat_config_summary(goal),
        "reward_memory": reward_memory_goal_policy_summary(goal),
        "pull_request_review": pr_review_config.configuration_summary(goal),
        "change_quality_qualification": change_quality_goal_policy_summary(goal),
        "explore_graph": compact_explore_graph_policy(goal.get("explore_graph")),
        "orchestration": orchestration,
        "waiting_on": goal.get("waiting_on"),
        "write_scope": normalize_goal_write_scope(coordination.get("write_scope") or [])
        or [],
        "local_authority_shadow": shadow.local_authority_shadow_summary(goal),
        "checkpointed_boundary_authority": checkpointed_boundary_authority_summary(
            coordination
        ),
        "registered_agents": registered_agents,
        "peer_task_coordination": deepcopy(
            compact_peer_task_coordination_policy(coordination)
        ),
        "todo_lifecycle_authority": normalize_todo_lifecycle_authority(
            coordination.get("todo_lifecycle_authority"),
            registered_agents=registered_agents,
        ),
        "agent_profiles": deepcopy(
            coordination.get("agent_profiles")
            if isinstance(coordination.get("agent_profiles"), dict)
            else {}
        ),
        "agent_work_modes": normalize_agent_work_modes(
            coordination.get("agent_work_modes"),
            registered_agents=registered_agents,
        ),
        "agent_model": agent_model.value,
        "configured_agent_model": coordination.get("agent_model"),
        "legacy_hierarchy_present": legacy_agent_hierarchy_present(goal),
        "peer_runtime_migration": deepcopy(
            completed_peer_agent_runtime_migration(goal)
        ),
        "supervisor": deepcopy(
            normalize_peer_supervisor(
                coordination.get("supervisor"),
                registered_agents=normalize_registered_agents(
                    coordination.get("registered_agents")
                ),
            )
        ),
    }
    return summary


def _multi_subagent_feature_status(orchestration: dict[str, Any]) -> str:
    if (
        orchestration.get("mode") == MULTI_SUBAGENT_ORCHESTRATION_MODE
        and orchestration.get("spawn_allowed") is True
        and int(orchestration.get("max_children") or 0) > 0
    ):
        return "enabled"
    return "off"


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for group, before_value in before.items():
        after_value = after.get(group)
        if before_value != after_value:
            changed.append(group)
    return changed


def _heartbeat_scope_hint(
    agent_id: str,
) -> str:
    del agent_id
    return "peer task claims, leases, evidence, and bounded delivery"


def _build_heartbeat_prompt_migration(
    *,
    goal_id: str,
    changed_fields: list[str],
    after: dict[str, Any],
    migration_id: str | None = None,
    migration_acknowledged: bool = False,
) -> dict[str, Any] | None:
    if not migration_id and not any(
        field in changed_fields
        for field in (
            "registered_agents",
            "configured_agent_model",
            "legacy_hierarchy_present",
        )
    ):
        return None
    registered_agents = [
        str(agent).strip()
        for agent in after.get("registered_agents") or []
        if str(agent).strip()
    ]
    if not registered_agents:
        return None
    agent_model = AgentRuntimeModel.PEER_V1.value
    ordered_agents = list(registered_agents)
    commands = []
    for agent in ordered_agents:
        scope = _heartbeat_scope_hint(agent)
        command = {
            "agent_id": agent,
            "command": (
                "loopx heartbeat-prompt --thin "
                f"--goal-id {shlex.quote(goal_id)} "
                f"--agent-id {shlex.quote(agent)} "
                f"--agent-scope {shlex.quote(scope)}"
            ),
        }
        commands.append(command)
    payload = {
        "schema_version": "heartbeat_prompt_migration_v1",
        "agent_model": agent_model,
        "migration_id": migration_id,
        "host_update_idempotency_key": migration_id,
        "status": "completed" if migration_acknowledged else "required",
        "reason": (
            "the host automation update was acknowledged and the registry hard cut completed"
            if migration_acknowledged
            else "coordination agent identity changed; installed heartbeats should be "
            "regenerated with identity-aware prompt args"
        ),
        "action": (
            "none; this migration id is complete and will not be projected again"
            if migration_acknowledged
            else "update each installed host automation once using migration_id as the "
            "idempotency key, then run completion_command"
        ),
        "commands": [] if migration_acknowledged else commands,
    }
    if migration_id and not migration_acknowledged:
        payload["completion_command"] = (
            "loopx configure-goal "
            f"--goal-id {shlex.quote(goal_id)} "
            f"--ack-automation-prompt-migration {shlex.quote(migration_id)} --execute"
        )
    return payload


def _build_supervisor_prompt_setup(
    *,
    goal_id: str,
    changed_fields: list[str],
    after: dict[str, Any],
) -> dict[str, Any] | None:
    if "supervisor" not in changed_fields:
        return None
    supervisor = after.get("supervisor")
    if not isinstance(supervisor, dict):
        return {
            "schema_version": "supervisor_prompt_setup_v0",
            "status": "disabled",
            "command": None,
        }
    agent_id = str(supervisor.get("agent_id") or "")
    return {
        "schema_version": "supervisor_prompt_setup_v0",
        "status": "ready",
        "agent_id": agent_id,
        "command": (
            "loopx supervisor-prompt "
            f"--goal-id {shlex.quote(goal_id)} "
            f"--agent-id {shlex.quote(agent_id)}"
        ),
    }


def configure_goal(
    *,
    registry_path: Path,
    goal_id: str,
    quota_compute: float | None = None,
    quota_window_hours: float | None = None,
    execution_turn_granularity: str | None = None,
    execution_replan_after_todos: int | None = None,
    clear_execution_replan_after_todos: bool = False,
    self_repair_enabled: bool | None = None,
    self_repair_health: bool | None = None,
    self_repair_waiting_projection: bool | None = None,
    periodic_report_configuration: Mapping[str, Any] | None = None, clear_periodic_report_configuration: bool = False,
    pull_request_review_configuration: Mapping[str, Any] | None = None,
    clear_pull_request_review_configuration: bool = False,
    change_quality_enabled: bool | None = None,
    change_quality_safe_fix: bool | None = None,
    change_quality_strict_receipt: bool | None = None,
    clear_change_quality_configuration: bool = False,
    multi_subagent_feature: str | None = None,
    orchestration_mode: str | None = None,
    spawn_allowed: bool | None = None,
    max_children: int | None = None,
    subagent_model: str | None = None,
    subagent_reasoning_effort: str | None = None,
    clear_subagent_model_config: bool = False,
    subagent_execution_config: str | None = None,
    clear_subagent_execution_config: bool = False,
    allowed_domains: list[str] | None = None,
    clear_allowed_domains: bool = False,
    explore_harness_enabled: bool | None = None,
    explore_harness_profile: str | None = None,
    clear_explore_harness_profile: bool = False,
    explore_graph_enabled: bool | None = None,
    registered_agents: list[str] | None = None,
    clear_registered_agents: bool = False,
    peer_task_coordinator: str | None = None,
    clear_peer_task_coordinator: bool = False,
    agent_profiles: list[dict[str, Any]] | None = None,
    clear_agent_profiles: list[str] | None = None,
    agent_work_modes: dict[str, str] | None = None,
    clear_agent_work_modes: list[str] | None = None,
    todo_lifecycle_authority: list[dict[str, Any]] | None = None,
    clear_todo_lifecycle_authority: list[str] | None = None,
    agent_model: str | None = None,
    automation_prompt_migration_ack: str | None = None,
    supervisor_agent: str | None = None,
    supervised_agents: list[str] | None = None,
    clear_supervisor: bool = False,
    write_scope: list[str] | None = None,
    replace_write_scope: bool = False,
    clear_write_scope: bool = False,
    local_authority_shadow_file: bool = False,
    clear_local_authority_shadow: bool = False,
    waiting_on: str | None = None,
    clear_waiting_on: bool = False,
    boundary_authority_scopes: list[str] | None = None,
    boundary_authority_source: str | None = None,
    boundary_authority_decision_id: str | None = None,
    boundary_authority_recorded_at: str | None = None,
    boundary_authority_expires_at: str | None = None,
    clear_boundary_authority: bool = False,
    issue_fix_reviewer_notification_config: str | None = None,
    clear_issue_fix_reviewer_notification_config: bool = False,
    lark_event_inbox_config: str | None = None,
    lark_event_inbox_agent_id: str | None = None,
    clear_lark_event_inbox_config: bool = False,
    lark_kanban_heartbeat_sync: bool | None = None,
    reward_memory_config: str | None = None,
    reward_memory_agents: list[str] | None = None,
    clear_reward_memory_config: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    if not registry_path.exists():
        raise FileNotFoundError(f"registry file does not exist: {registry_path}")
    if clear_allowed_domains and allowed_domains:
        raise ValueError(
            "--clear-allowed-domains cannot be combined with --allowed-domain"
        )
    if clear_explore_harness_profile and explore_harness_profile:
        raise ValueError(
            "--clear-explore-harness-profile cannot be combined with --explore-harness-profile"
        )
    if clear_registered_agents and registered_agents:
        raise ValueError(
            "--clear-registered-agents cannot be combined with --registered-agent"
        )
    if clear_peer_task_coordinator and peer_task_coordinator:
        raise ValueError(
            "--clear-peer-task-coordinator cannot be combined with "
            "--peer-task-coordinator"
        )
    if agent_model is not None:
        agent_model = str(agent_model).strip().lower()
        if agent_model not in AGENT_MODEL_CHOICES:
            raise ValueError(
                "--agent-model must be one of: " + ", ".join(AGENT_MODEL_CHOICES)
            )
    if automation_prompt_migration_ack is not None:
        automation_prompt_migration_ack = str(automation_prompt_migration_ack).strip()
        if not automation_prompt_migration_ack:
            raise ValueError(
                "--ack-automation-prompt-migration requires a migration id"
            )
        if registered_agents is not None or clear_registered_agents:
            raise ValueError(
                "--ack-automation-prompt-migration cannot change registered agents; "
                "complete the runtime cutover first, then update the peer set separately"
            )
    if clear_supervisor and (supervisor_agent or supervised_agents):
        raise ValueError(
            "--clear-supervisor cannot be combined with --supervisor-agent or "
            "--supervised-agent"
        )
    if supervised_agents and not supervisor_agent:
        raise ValueError("--supervised-agent requires --supervisor-agent")
    if clear_write_scope and write_scope:
        raise ValueError("--clear-write-scope cannot be combined with --write-scope")
    if replace_write_scope and not write_scope:
        raise ValueError("--replace-write-scope requires --write-scope")
    if clear_write_scope and replace_write_scope:
        raise ValueError(
            "--clear-write-scope cannot be combined with --replace-write-scope"
        )
    shadow.validate_local_authority_shadow_change(
        local_authority_shadow_file, clear_local_authority_shadow
    )
    if clear_waiting_on and waiting_on:
        raise ValueError("--clear-waiting-on cannot be combined with --waiting-on")
    adding_boundary_authority = any(
        value
        for value in (
            boundary_authority_scopes,
            boundary_authority_source,
            boundary_authority_decision_id,
            boundary_authority_recorded_at,
            boundary_authority_expires_at,
        )
    )
    if clear_boundary_authority and adding_boundary_authority:
        raise ValueError(
            "--clear-boundary-authority cannot be combined with boundary authority fields"
        )
    if (
        clear_issue_fix_reviewer_notification_config
        and issue_fix_reviewer_notification_config
    ):
        raise ValueError(
            "--clear-issue-fix-reviewer-notification-config cannot be combined "
            "with --issue-fix-reviewer-notification-config"
        )
    if clear_lark_event_inbox_config and lark_event_inbox_config:
        raise ValueError(
            "--clear-lark-event-inbox-config cannot be combined with "
            "--lark-event-inbox-config"
        )
    if lark_event_inbox_agent_id and not (
        lark_event_inbox_config or clear_lark_event_inbox_config
    ):
        raise ValueError(
            "--lark-event-inbox-agent-id requires --lark-event-inbox-config "
            "or --clear-lark-event-inbox-config"
        )
    if clear_reward_memory_config and (reward_memory_config or reward_memory_agents):
        raise ValueError(
            "--clear-reward-memory-config cannot be combined with "
            "--reward-memory-config or --reward-memory-agent"
        )
    if waiting_on and waiting_on not in WAITING_ON_CHOICES:
        raise ValueError(
            "--waiting-on must be one of: " + ", ".join(WAITING_ON_CHOICES)
        )
    if (
        multi_subagent_feature is not None
        and multi_subagent_feature not in MULTI_SUBAGENT_FEATURE_CHOICES
    ):
        raise ValueError(
            "--multi-subagent-feature must be one of: "
            + ", ".join(MULTI_SUBAGENT_FEATURE_CHOICES)
        )
    if multi_subagent_feature is not None and (
        orchestration_mode is not None or spawn_allowed is not None
    ):
        raise ValueError(
            "--multi-subagent-feature cannot be combined with --orchestration-mode or --spawn-allowed; "
            "use --max-children/--allowed-domain for bounded feature settings"
        )
    if clear_subagent_model_config and (
        subagent_model is not None or subagent_reasoning_effort is not None
    ):
        raise ValueError(
            "--clear-subagent-model-config cannot be combined with model settings"
        )
    if clear_subagent_execution_config and subagent_execution_config:
        raise ValueError(
            "--clear-subagent-execution-config cannot be combined with "
            "--subagent-execution-config"
        )
    if explore_harness_profile is not None:
        explore_harness_profile = (
            str(explore_harness_profile).strip().lower().replace("_", "-")
        )
        if explore_harness_profile not in EXPLORE_HARNESS_PROFILES:
            raise ValueError(
                "--explore-harness-profile must be one of: "
                + ", ".join(EXPLORE_HARNESS_PROFILES)
            )

    quota_compute = _non_negative_number(quota_compute, field="quota_compute")
    quota_window_hours = _positive_number(
        quota_window_hours, field="quota_window_hours"
    )
    max_children = _non_negative_int(max_children, field="max_children")
    allowed_domains = _clean_domains(allowed_domains)
    if multi_subagent_feature == "off":
        if max_children not in (None, 0):
            raise ValueError(
                "--multi-subagent-feature off cannot be combined with --max-children greater than 0"
            )
        if allowed_domains:
            raise ValueError(
                "--multi-subagent-feature off cannot be combined with --allowed-domain"
            )
    registered_agents = _clean_registered_agents(registered_agents)
    if peer_task_coordinator is not None:
        peer_task_coordinator = normalize_todo_claimed_by(peer_task_coordinator)
        if not peer_task_coordinator:
            raise ValueError(
                "--peer-task-coordinator must be a public-safe registered agent id"
            )
    reward_memory_agents = _clean_registered_agents(reward_memory_agents)
    clear_agent_profiles = _clean_registered_agents(clear_agent_profiles)
    clear_agent_work_modes = _clean_registered_agents(clear_agent_work_modes)
    clear_todo_lifecycle_authority = _clean_registered_agents(
        clear_todo_lifecycle_authority
    )
    supervised_agents = _clean_registered_agents(supervised_agents)
    write_scope = normalize_goal_write_scope(write_scope)
    issue_fix_reviewer_notification_config = _local_private_config_path(
        issue_fix_reviewer_notification_config,
        label="reviewer notification config",
    )
    lark_event_inbox_config = _local_private_config_path(
        lark_event_inbox_config,
        label="lark event inbox config",
    )
    normalized_lark_inbox_agent = normalize_todo_claimed_by(lark_event_inbox_agent_id)
    if lark_event_inbox_agent_id and not normalized_lark_inbox_agent:
        raise ValueError(
            "--lark-event-inbox-agent-id must be a public-safe registered agent id"
        )
    reward_memory_config = _local_private_config_path(
        reward_memory_config,
        label="reward memory experiment config",
    )
    subagent_execution_config = _local_private_config_path(
        subagent_execution_config,
        label="subagent execution config",
    )
    periodic_report_change = periodic_report_config.normalize_change(
        periodic_report_configuration, clear=clear_periodic_report_configuration
    )
    change_quality_change = change_quality_config.normalize_change(
        change_quality_enabled,
        change_quality_safe_fix,
        change_quality_strict_receipt,
        clear=clear_change_quality_configuration,
    )
    payload = read_json(registry_path)
    goals = registry_goals(payload)
    goal = next((item for item in goals if str(item.get("id")) == goal_id), None)
    if goal is None:
        raise ValueError(f"goal_id not found in registry: {goal_id}")
    existing_coordination = (
        goal.get("coordination") if isinstance(goal.get("coordination"), dict) else {}
    )
    effective_registered_agents = (
        []
        if clear_registered_agents
        else registered_agents
        if registered_agents is not None
        else normalize_registered_agents(existing_coordination.get("registered_agents"))
    )
    if (
        normalized_lark_inbox_agent
        and normalized_lark_inbox_agent not in effective_registered_agents
    ):
        raise ValueError(
            "--lark-event-inbox-agent-id must name an agent registered for the goal"
        )
    if (
        peer_task_coordinator is not None
        and peer_task_coordinator not in effective_registered_agents
    ):
        raise ValueError(
            "--peer-task-coordinator must name an agent already registered for "
            f"this goal: {peer_task_coordinator}"
        )
    reward_memory_plan = plan_reward_memory_goal_configuration(
        goal=goal,
        goal_id=goal_id,
        registered_agents=effective_registered_agents,
        requested_config_path=reward_memory_config,
        requested_agents=reward_memory_agents,
        clear=clear_reward_memory_config,
        observed_at=_now_iso(),
        execute=execute,
    )
    normalized_agent_profiles: dict[str, dict[str, Any]] = {}
    for raw_profile in agent_profiles or []:
        if not isinstance(raw_profile, Mapping):
            raise ValueError("--agent-profile-json must contain a JSON object")
        profile = normalize_agent_profile(
            raw_profile,
            registered_agents=effective_registered_agents,
        )
        profile_agent_id = str(profile["agent_id"])
        if profile_agent_id in normalized_agent_profiles:
            raise ValueError(f"duplicate agent profile for {profile_agent_id}")
        normalized_agent_profiles[profile_agent_id] = profile
    profile_conflicts = sorted(
        set(normalized_agent_profiles) & set(clear_agent_profiles or [])
    )
    if profile_conflicts:
        raise ValueError(
            "cannot write and clear the same agent profile: "
            + ", ".join(profile_conflicts)
        )
    normalized_agent_work_modes = normalize_agent_work_modes(
        agent_work_modes,
        registered_agents=effective_registered_agents,
    )
    work_mode_conflicts = sorted(
        set(normalized_agent_work_modes) & set(clear_agent_work_modes or [])
    )
    if work_mode_conflicts:
        raise ValueError(
            "cannot write and clear the same agent work mode: "
            + ", ".join(work_mode_conflicts)
        )
    normalized_todo_lifecycle_authority = normalize_todo_lifecycle_authority(
        todo_lifecycle_authority,
        registered_agents=effective_registered_agents,
    )
    authority_conflicts = sorted(
        {entry["agent_id"] for entry in normalized_todo_lifecycle_authority}
        & set(clear_todo_lifecycle_authority or [])
    )
    if authority_conflicts:
        raise ValueError(
            "cannot write and clear the same todo lifecycle authority grant: "
            + ", ".join(authority_conflicts)
        )

    before_goal = deepcopy(goal)
    before = _settings_summary(before_goal)
    apply_goal_execution_profile_change(
        goal,
        turn_granularity=execution_turn_granularity,
        replan_after_completed_todos=execution_replan_after_todos,
        clear_replan_after_completed_todos=clear_execution_replan_after_todos,
    )
    legacy_hierarchy_before = legacy_agent_hierarchy_present(before_goal)
    expected_migration_id = peer_agent_runtime_migration_id(goal_id, before_goal)
    completed_migration_before = completed_peer_agent_runtime_migration(before_goal)
    migration_completed_before = peer_agent_runtime_migration_completed(before_goal)
    migration_already_completed = bool(
        completed_migration_before
        and completed_migration_before.get("migration_id")
        == automation_prompt_migration_ack
        and migration_completed_before
    )
    if automation_prompt_migration_ack is not None:
        if migration_already_completed:
            pass
        elif not legacy_hierarchy_before:
            raise ValueError(
                "no pending peer runtime automation migration matches this goal"
            )
        elif automation_prompt_migration_ack != expected_migration_id:
            raise ValueError(
                "automation prompt migration id does not match the current goal state; "
                f"expected {expected_migration_id}"
            )
    elif (
        execute
        and legacy_hierarchy_before
        and not migration_completed_before
        and (
            agent_model is not None
            or registered_agents is not None
            or peer_task_coordinator is not None
            or clear_peer_task_coordinator
            or clear_registered_agents
        )
    ):
        raise ValueError(
            "legacy agent hierarchy requires the one-time host automation migration first; "
            "regenerate/update the installed peer heartbeat, then run "
            f"`loopx configure-goal --goal-id {goal_id} "
            f"--ack-automation-prompt-migration {expected_migration_id} --execute`"
        )

    if quota_compute is not None or quota_window_hours is not None:
        quota = goal.get("quota") if isinstance(goal.get("quota"), dict) else {}
        if quota_compute is not None:
            quota["compute"] = quota_compute
        if quota_window_hours is not None:
            quota["window_hours"] = quota_window_hours
        goal["quota"] = quota

    if (
        self_repair_enabled is not None
        or self_repair_health is not None
        or self_repair_waiting_projection is not None
    ):
        control_plane = _mutable_control_plane(goal)
        self_repair = (
            control_plane.get("self_repair")
            if isinstance(control_plane.get("self_repair"), dict)
            else {}
        )
        if self_repair_enabled is not None:
            self_repair["enabled"] = self_repair_enabled
        if self_repair_health is not None:
            self_repair["allow_health_blocker_repair"] = self_repair_health
        if self_repair_waiting_projection is not None:
            self_repair["allow_waiting_projection_repair"] = (
                self_repair_waiting_projection
            )
        control_plane["self_repair"] = self_repair
    periodic_report_config.apply_change(goal, periodic_report_change)
    pr_review_config.apply_change(goal, pull_request_review_configuration, clear=clear_pull_request_review_configuration)
    change_quality_config.apply_change(goal, change_quality_change)
    if (
        issue_fix_reviewer_notification_config is not None
        or clear_issue_fix_reviewer_notification_config
    ):
        control_plane = _mutable_control_plane(goal)
        issue_fix = (
            control_plane.get("issue_fix")
            if isinstance(control_plane.get("issue_fix"), dict)
            else {}
        )
        if clear_issue_fix_reviewer_notification_config:
            issue_fix.pop("reviewer_notification", None)
        else:
            issue_fix["reviewer_notification"] = {
                "enabled": True,
                "config_path": issue_fix_reviewer_notification_config,
            }
        if issue_fix:
            control_plane["issue_fix"] = issue_fix
        else:
            control_plane.pop("issue_fix", None)
    if lark_event_inbox_config is not None or clear_lark_event_inbox_config:
        control_plane = _mutable_control_plane(goal)
        if normalized_lark_inbox_agent:
            agent_inboxes = (
                dict(control_plane.get("lark_event_inboxes"))
                if isinstance(control_plane.get("lark_event_inboxes"), dict)
                else {}
            )
            if clear_lark_event_inbox_config:
                agent_inboxes.pop(normalized_lark_inbox_agent, None)
            else:
                agent_inbox_binding: dict[str, Any] = {
                    "enabled": True,
                    "config_path": lark_event_inbox_config,
                }
                config_digest = local_private_config_digest(
                    project=str(goal.get("repo") or ""),
                    config_path=str(lark_event_inbox_config or ""),
                )
                if config_digest:
                    agent_inbox_binding["config_digest"] = config_digest
                agent_inboxes[normalized_lark_inbox_agent] = agent_inbox_binding
            if agent_inboxes:
                control_plane["lark_event_inboxes"] = agent_inboxes
            else:
                control_plane.pop("lark_event_inboxes", None)
        elif clear_lark_event_inbox_config:
            control_plane.pop("lark_event_inbox", None)
        else:
            control_plane["lark_event_inbox"] = {
                "enabled": True,
                "config_path": lark_event_inbox_config,
            }
    if lark_kanban_heartbeat_sync is not None:
        control_plane = _mutable_control_plane(goal)
        lark_kanban = (
            control_plane.get("lark_kanban")
            if isinstance(control_plane.get("lark_kanban"), dict)
            else {}
        )
        lark_kanban["heartbeat_sync_enabled"] = lark_kanban_heartbeat_sync
        control_plane["lark_kanban"] = lark_kanban

    apply_reward_memory_goal_configuration(goal, reward_memory_plan)

    if explore_graph_enabled is not None:
        goal["explore_graph"] = {"enabled": explore_graph_enabled}

    if (
        multi_subagent_feature is not None
        or orchestration_mode is not None
        or spawn_allowed is not None
        or max_children is not None
        or subagent_model is not None
        or subagent_reasoning_effort is not None
        or clear_subagent_model_config
        or subagent_execution_config is not None
        or clear_subagent_execution_config
        or allowed_domains is not None
        or clear_allowed_domains
        or explore_harness_enabled is not None
        or explore_harness_profile is not None
        or clear_explore_harness_profile
    ):
        spawn_policy = (
            goal.get("spawn_policy")
            if isinstance(goal.get("spawn_policy"), dict)
            else {}
        )
        update_spawn_execution_policy(
            spawn_policy,
            multi_subagent_feature=multi_subagent_feature,
            orchestration_mode=orchestration_mode,
            spawn_allowed=spawn_allowed,
            max_children=max_children,
            subagent_model=subagent_model,
            subagent_reasoning_effort=subagent_reasoning_effort,
            clear_subagent_model_config=clear_subagent_model_config,
            subagent_execution_config=subagent_execution_config,
            clear_subagent_execution_config=clear_subagent_execution_config,
            allowed_domains=allowed_domains,
            clear_allowed_domains=clear_allowed_domains,
            default_max_children=DEFAULT_MULTI_SUBAGENT_MAX_CHILDREN,
        )
        if (
            explore_harness_enabled is not None
            or explore_harness_profile is not None
            or clear_explore_harness_profile
        ):
            explore_harness = (
                spawn_policy.get("explore_harness")
                if isinstance(spawn_policy.get("explore_harness"), dict)
                else {}
            )
            if explore_harness_enabled is not None:
                explore_harness["enabled"] = explore_harness_enabled
            if clear_explore_harness_profile:
                explore_harness.pop("profile", None)
            elif explore_harness_profile is not None:
                explore_harness["profile"] = explore_harness_profile
            if explore_harness:
                spawn_policy["explore_harness"] = explore_harness
            else:
                spawn_policy.pop("explore_harness", None)
        goal["spawn_policy"] = spawn_policy

    if waiting_on is not None:
        goal["waiting_on"] = waiting_on
    elif clear_waiting_on:
        goal.pop("waiting_on", None)

    if (
        clear_registered_agents
        or registered_agents is not None
        or peer_task_coordinator is not None
        or clear_peer_task_coordinator
        or normalized_agent_profiles
        or clear_agent_profiles
        or normalized_agent_work_modes
        or clear_agent_work_modes
        or normalized_todo_lifecycle_authority
        or clear_todo_lifecycle_authority
        or agent_model is not None
        or automation_prompt_migration_ack is not None
        or supervisor_agent is not None
        or supervised_agents is not None
        or clear_supervisor
        or write_scope is not None
        or clear_write_scope
        or clear_boundary_authority
        or adding_boundary_authority
    ):
        coordination = (
            goal.get("coordination")
            if isinstance(goal.get("coordination"), dict)
            else {}
        )
        effective_agent_model = AgentRuntimeModel.PEER_V1.value
        if clear_registered_agents:
            coordination.pop("registered_agents", None)
            coordination.pop("agent_model", None)
            coordination.pop("agent_profiles", None)
            coordination.pop("agent_work_modes", None)
            coordination.pop("supervisor", None)
            coordination.pop("todo_lifecycle_authority", None)
            coordination.pop("peer_task_coordination", None)
        elif registered_agents is not None:
            coordination["registered_agents"] = registered_agents
            current_peer_task_coordination = (
                coordination.get("peer_task_coordination")
                if isinstance(coordination.get("peer_task_coordination"), dict)
                else {}
            )
            current_peer_coordinator = normalize_todo_claimed_by(
                current_peer_task_coordination.get("coordinator_agent_id")
            )
            if (
                current_peer_coordinator
                and current_peer_coordinator not in registered_agents
            ):
                coordination.pop("peer_task_coordination", None)
            existing_profiles = (
                coordination.get("agent_profiles")
                if isinstance(coordination.get("agent_profiles"), dict)
                else {}
            )
            retained_profiles = {
                agent: profile
                for agent, profile in existing_profiles.items()
                if agent in registered_agents
            }
            if retained_profiles:
                coordination["agent_profiles"] = retained_profiles
            else:
                coordination.pop("agent_profiles", None)
            raw_work_modes = (
                coordination.get("agent_work_modes")
                if isinstance(coordination.get("agent_work_modes"), Mapping)
                else {}
            )
            retained_work_modes = normalize_agent_work_modes(
                {
                    agent: mode
                    for agent, mode in raw_work_modes.items()
                    if agent in registered_agents
                },
                registered_agents=registered_agents,
            )
            if retained_work_modes:
                coordination["agent_work_modes"] = retained_work_modes
            else:
                coordination.pop("agent_work_modes", None)
            retained_authority = [
                entry
                for entry in coordination.get("todo_lifecycle_authority") or []
                if isinstance(entry, Mapping)
                and normalize_todo_claimed_by(entry.get("agent_id"))
                in registered_agents
            ]
            if retained_authority:
                coordination["todo_lifecycle_authority"] = (
                    normalize_todo_lifecycle_authority(
                        retained_authority,
                        registered_agents=registered_agents,
                    )
                )
            else:
                coordination.pop("todo_lifecycle_authority", None)
        if not clear_registered_agents:
            coordination["agent_model"] = effective_agent_model
        if not clear_registered_agents:
            if clear_peer_task_coordinator:
                coordination.pop("peer_task_coordination", None)
            elif peer_task_coordinator is not None:
                coordination["peer_task_coordination"] = {
                    "coordinator_agent_id": peer_task_coordinator,
                }
        if not clear_registered_agents and (
            normalized_agent_profiles or clear_agent_profiles
        ):
            profiles = (
                dict(coordination.get("agent_profiles"))
                if isinstance(coordination.get("agent_profiles"), dict)
                else {}
            )
            for profile_agent_id in clear_agent_profiles or []:
                profiles.pop(profile_agent_id, None)
            profiles.update(normalized_agent_profiles)
            if profiles:
                coordination["agent_profiles"] = profiles
            else:
                coordination.pop("agent_profiles", None)
        if not clear_registered_agents and (
            normalized_agent_work_modes or clear_agent_work_modes
        ):
            work_modes = normalize_agent_work_modes(
                coordination.get("agent_work_modes"),
                registered_agents=normalize_registered_agents(
                    coordination.get("registered_agents")
                ),
            )
            for work_mode_agent_id in clear_agent_work_modes or []:
                work_modes.pop(work_mode_agent_id, None)
            work_modes.update(normalized_agent_work_modes)
            if work_modes:
                coordination["agent_work_modes"] = dict(sorted(work_modes.items()))
            else:
                coordination.pop("agent_work_modes", None)
        if not clear_registered_agents and (
            normalized_todo_lifecycle_authority or clear_todo_lifecycle_authority
        ):
            current_authority = normalize_todo_lifecycle_authority(
                coordination.get("todo_lifecycle_authority"),
                registered_agents=normalize_registered_agents(
                    coordination.get("registered_agents")
                ),
            )
            authority_by_agent = {
                entry["agent_id"]: entry for entry in current_authority
            }
            for authority_agent_id in clear_todo_lifecycle_authority or []:
                authority_by_agent.pop(authority_agent_id, None)
            authority_by_agent.update(
                {
                    entry["agent_id"]: entry
                    for entry in normalized_todo_lifecycle_authority
                }
            )
            if authority_by_agent:
                coordination["todo_lifecycle_authority"] = list(
                    authority_by_agent.values()
                )
            else:
                coordination.pop("todo_lifecycle_authority", None)
        if (
            automation_prompt_migration_ack is not None
            and not migration_already_completed
        ):
            coordination = migrate_coordination_to_peer_v1(
                coordination,
                migration_id=automation_prompt_migration_ack,
                completed_at=_now_iso() if execute else None,
            )
        if clear_supervisor:
            coordination.pop("supervisor", None)
        elif supervisor_agent is not None:
            normalized_supervisor_agent = normalize_todo_claimed_by(supervisor_agent)
            if not normalized_supervisor_agent:
                raise ValueError("--supervisor-agent must be a registered agent id")
            coordination["supervisor"] = normalize_peer_supervisor(
                {
                    "agent_id": normalized_supervisor_agent,
                    "supervised_agents": supervised_agents,
                },
                registered_agents=normalize_registered_agents(
                    coordination.get("registered_agents")
                ),
            )
        if clear_write_scope:
            coordination["write_scope"] = []
        elif write_scope is not None:
            if replace_write_scope:
                coordination["write_scope"] = write_scope
            else:
                existing_write_scope = (
                    normalize_goal_write_scope(coordination.get("write_scope") or [])
                    or []
                )
                coordination["write_scope"] = (
                    normalize_goal_write_scope([*existing_write_scope, *write_scope])
                    or []
                )
        if clear_boundary_authority:
            coordination.pop("checkpointed_boundary_authority", None)
        if adding_boundary_authority:
            entry = build_checkpointed_boundary_authority_entry(
                write_scopes=boundary_authority_scopes or [],
                source=boundary_authority_source or "",
                decision_id=boundary_authority_decision_id,
                recorded_at=boundary_authority_recorded_at,
                expires_at=boundary_authority_expires_at,
            )
            entries = (
                coordination.get("checkpointed_boundary_authority")
                if isinstance(coordination.get("checkpointed_boundary_authority"), list)
                else []
            )
            coordination["checkpointed_boundary_authority"] = [*entries, entry]
        goal["coordination"] = coordination

    shadow.apply_local_authority_shadow_change(
        goal, local_authority_shadow_file, clear_local_authority_shadow
    )
    after = _settings_summary(goal)
    changed_fields = _changed_fields(before, after)
    if goal != before_goal and not changed_fields:
        # Some local-private control-plane bindings intentionally project only
        # counts and booleans. Rebinding one enabled provider to another can
        # therefore preserve the public summary while still requiring a write.
        changed_fields = [
            field
            for field in ("execution_profile", "control_plane")
            if before_goal.get(field) != goal.get(field)
        ] or ["control_plane"]
    dry_run = not execute
    model_changed = bool(
        before.get("legacy_hierarchy_present")
        or before.get("agent_model") != after.get("agent_model")
        or automation_prompt_migration_ack is not None
    )
    backup_path = None

    if execute and changed_fields:
        payload["updated_at"] = _now_iso()
        if model_changed:
            stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
            backup_path = registry_path.with_name(
                f"{registry_path.name}.before-agent-model-{stamp}.bak"
            )
            shutil.copy2(registry_path, backup_path)
        atomic_write_json(registry_path, payload)

    feature_summary = {
        "multi_subagent": _multi_subagent_feature_status(
            after.get("orchestration") or {}
        ),
        "explore_graph": deepcopy(after.get("explore_graph") or {"enabled": False}),
        "explore_harness": deepcopy(
            (after.get("orchestration") or {}).get("explore_harness")
            or {"enabled": False}
        ),
        "peer_supervisor": deepcopy(after.get("supervisor") or {"enabled": False}),
        "peer_task_coordination": deepcopy(
            after.get("peer_task_coordination") or {"enabled": False}
        ),
        "local_authority_shadow": deepcopy(after["local_authority_shadow"]),
        "lark_event_inbox": _lark_event_inbox_config_summary(goal),
        "lark_kanban_heartbeat_sync": _lark_kanban_heartbeat_config_summary(goal),
        "reward_memory": reward_memory_goal_policy_summary(goal),
        "pull_request_review": pr_review_config.configuration_summary(goal),
        "change_quality_qualification": change_quality_goal_policy_summary(goal),
        "default": "off",
        "configuration_entry": "multi_subagent_feature",
    }

    return {
        "ok": reward_memory_plan["preflight"] is None
        or reward_memory_plan["preflight"].get("ok") is True,
        "dry_run": dry_run,
        "execute": execute,
        "registry": str(registry_path),
        "goal_id": goal_id,
        "changed": bool(changed_fields),
        "changed_fields": changed_fields,
        "backup_path": str(backup_path) if backup_path else None,
        "before": before,
        "after": after,
        "written": bool(execute and changed_fields),
        "reward_memory_enablement_preflight": deepcopy(reward_memory_plan["preflight"]),
        "automation_prompt_migration": {
            "migration_id": automation_prompt_migration_ack,
            "status": (
                "already_completed"
                if migration_already_completed
                else "completed"
                if automation_prompt_migration_ack is not None
                else "pending"
                if legacy_hierarchy_before
                else "not_required"
            ),
            "exactly_once_effect": True,
        },
        "control_plane_summary": control_plane_policy_summary(
            after.get("control_plane")
        ),
        "orchestration_summary": orchestration_policy_summary(
            after.get("orchestration")
        ),
        "feature_summary": feature_summary,
        "configuration_catalog": build_goal_configuration_catalog(
            goal_id=goal_id,
            settings=after,
            feature_summary=feature_summary,
            default_multi_subagent_max_children=DEFAULT_MULTI_SUBAGENT_MAX_CHILDREN,
            explore_harness_profiles=EXPLORE_HARNESS_PROFILES,
            machine_inheritable_goal_overrides=(
                builtin_machine_inheritable_goal_overrides(goal)
            ),
        ),
        "heartbeat_prompt_migration": _build_heartbeat_prompt_migration(
            goal_id=goal_id,
            changed_fields=changed_fields,
            after=after,
            migration_id=(
                automation_prompt_migration_ack
                if automation_prompt_migration_ack is not None
                else expected_migration_id
                if legacy_hierarchy_before and not migration_completed_before
                else None
            ),
            migration_acknowledged=automation_prompt_migration_ack is not None,
        ),
        "supervisor_prompt": _build_supervisor_prompt_setup(
            goal_id=goal_id,
            changed_fields=changed_fields,
            after=after,
        ),
    }


def render_configure_goal_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Goal Configuration",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- registry: `{payload.get('registry')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- changed: `{payload.get('changed')}`",
        f"- written: `{payload.get('written')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)
    fields = payload.get("changed_fields") or []
    lines.append(f"- changed_fields: `{', '.join(fields) if fields else 'none'}`")
    lines.extend(
        reward_memory_preflight_markdown_lines(
            payload.get("reward_memory_enablement_preflight")
        )
    )
    global_sync = payload.get("global_sync")
    if isinstance(global_sync, dict):
        selected_target = (
            global_sync.get("selected_target")
            if isinstance(global_sync.get("selected_target"), dict)
            else {}
        )
        readback = (
            global_sync.get("readback")
            if isinstance(global_sync.get("readback"), dict)
            else {}
        )
        lines.extend(
            [
                f"- global_sync_enabled: `{global_sync.get('enabled')}`",
                f"- global_sync_executed: `{global_sync.get('executed')}`",
                f"- global_sync_target: `{selected_target.get('global_registry')}`",
                f"- global_sync_readback: `{readback.get('status')}`",
            ]
        )
    if payload.get("control_plane_summary"):
        lines.append(f"- control_plane: {payload.get('control_plane_summary')}")
    if payload.get("orchestration_summary"):
        lines.append(f"- orchestration: {payload.get('orchestration_summary')}")
    codex_capacity = payload.get("codex_host_capacity")
    if isinstance(codex_capacity, dict):
        lines.extend(
            [
                f"- codex_host_capacity: `{codex_capacity.get('status')}`",
                f"- codex_required_children: `{codex_capacity.get('required_children')}`",
                f"- codex_configured_children: `{codex_capacity.get('configured_children')}`",
                f"- codex_alignment_requested: `{codex_capacity.get('alignment_requested')}`",
                f"- codex_capacity_written: `{codex_capacity.get('written', False)}`",
                f"- codex_new_session_required: `{codex_capacity.get('new_session_required', False)}`",
            ]
        )
    feature_summary = payload.get("feature_summary")
    if isinstance(feature_summary, dict):
        lines.append(
            f"- feature_multi_subagent: `{feature_summary.get('multi_subagent')}`"
        )
        graph = feature_summary.get("explore_graph")
        if isinstance(graph, dict):
            lines.append(
                f"- feature_explore_graph: `{'on' if graph.get('enabled') else 'off'}`"
            )
        harness = feature_summary.get("explore_harness")
        if isinstance(harness, dict):
            harness_state = "on" if harness.get("enabled") else "off"
            if harness.get("profile"):
                harness_state += f"({harness.get('profile')})"
            lines.append(f"- feature_explore_harness: `{harness_state}`")
        supervisor = feature_summary.get("peer_supervisor")
        if isinstance(supervisor, dict):
            supervisor_state = "on" if supervisor.get("enabled") else "off"
            if supervisor.get("agent_id"):
                supervisor_state += f"({supervisor.get('agent_id')})"
            lines.append(f"- feature_peer_supervisor: `{supervisor_state}`")
    catalog = payload.get("configuration_catalog")
    if isinstance(catalog, dict):
        disclosure = catalog.get("disclosure_policy") or {}
        lines.extend(
            [
                "",
                "## Optional Features (On Demand)",
                "",
                "First run requires no optional feature configuration.",
                "",
                f"- inspect: `{disclosure.get('inspect_command')}`",
                f"- all_settings_help: `{catalog.get('all_settings_help_command')}`",
                "- apply_policy: preview first; add `--execute` only after review",
            ]
        )
        for feature in catalog.get("features") or []:
            if not isinstance(feature, dict):
                continue
            current = feature.get("current") or {}
            state = "on" if current.get("enabled") else "off"
            commands = feature.get("commands") or {}
            verify_commands = commands.get("verify") or []
            documentation = feature.get("documentation") or {}
            lines.extend(
                [
                    f"- `{feature.get('feature_id')}` ({feature.get('display_name')}): `{state}`",
                    f"  - consider: {feature.get('consider_when')}",
                    f"  - preview: `{commands.get('preview_enable')}`",
                    f"  - apply: `{commands.get('apply_enable')}`",
                    f"  - preview_disable: `{commands.get('preview_disable')}`",
                    f"  - apply_disable: `{commands.get('apply_disable')}`",
                    f"  - verify: {'; '.join(f'`{command}`' for command in verify_commands)}",
                    f"  - docs: [{documentation.get('path')}]({documentation.get('url')})",
                ]
            )
    migration = payload.get("heartbeat_prompt_migration")
    if isinstance(migration, dict):
        lines.append(f"- heartbeat_prompt_migration: {migration.get('action')}")
        for command in migration.get("commands") or []:
            if not isinstance(command, dict):
                continue
            lines.append(f"  - {command.get('agent_id')}: `{command.get('command')}`")
    supervisor_prompt = payload.get("supervisor_prompt")
    if isinstance(supervisor_prompt, dict):
        lines.append(f"- supervisor_prompt_status: `{supervisor_prompt.get('status')}`")
        if supervisor_prompt.get("command"):
            lines.append(f"- supervisor_prompt: `{supervisor_prompt.get('command')}`")
    activation = payload.get("host_loop_activation")
    if isinstance(activation, dict):
        lines.append(
            f"- host_loop_activation: `{activation.get('host_surface')}` "
            f"status=`{activation.get('status')}` "
            f"activated=`{activation.get('activated')}`"
        )
        if activation.get("activated") is not True:
            lines.append(f"- host_loop_action: {activation.get('recommended_action')}")
    if payload.get("changed"):
        lines.extend(
            [
                "",
                "## Before",
                "",
                "```json",
                json.dumps(payload.get("before") or {}, ensure_ascii=False, indent=2),
                "```",
                "",
                "## After",
                "",
                "```json",
                json.dumps(payload.get("after") or {}, ensure_ascii=False, indent=2),
                "```",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## Current Settings",
                "",
                "```json",
                json.dumps(payload.get("after") or {}, ensure_ascii=False, indent=2),
                "```",
            ]
        )
    return "\n".join(lines)
