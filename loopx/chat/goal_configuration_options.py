"""Typed capability configuration option translators for Goal HTTP configuration."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..orchestration import subagent_model_configuration_options

_CLEAR_CAPABILITY_OPTIONS: dict[str, dict[str, object]] = {
    "explore_harness": {"clear_explore_configuration": True},
    "goal_capability_organization": {"clear_capability_improvement_configuration": True},
    "periodic_report": {"clear_periodic_report_configuration": True},
    "todo_replan_cadence": {
        "clear_execution_replan_after_todos": True,
        "clear_execution_replan_after_turns": True,
        "clear_execution_planning_horizon_goal": True,
        "clear_execution_yardstick_enabled": True,
    },
    "pull_request_review": {"clear_pull_request_review_configuration": True},
    "change_quality_qualification": {"clear_change_quality_configuration": True},
    "progress_review": {"clear_progress_review_configuration": True},
    "reward_memory": {"clear_reward_memory_config": True},
}

_ALLOWED_CAPABILITY_FIELDS: dict[str, frozenset[str]] = {
    "goal_capability_organization": frozenset({"mode", "discovery_budget_minutes", "max_trials"}),
    "todo_replan_cadence": frozenset(
        {"completed_todos", "count_unit", "count", "planning_horizon_goal", "yardstick_enabled"}
    ),
    "multi_subagent": frozenset(
        {"enabled", "max_children", "allowed_domains", "model", "reasoning_effort", "execution_config"}
    ),
    "peer_task_coordination": frozenset({"coordinator_agent_id"}),
    "explore_graph": frozenset({"enabled"}),
    "explore_harness": frozenset({"mode", "enabled", "profile", "composition_mode", "composition_scope_id"}),
    "pull_request_review": frozenset({"wait_for_ci", "review_order", "agent_orders", "review_priority", "owner_logins"}),
    "change_quality_qualification": frozenset({"enabled", "safe_fix", "strict_receipt"}),
    "progress_review": frozenset({"mode", "signal", "drift_threshold", "contract_revision"}),
    "local_authority_shadow": frozenset({"enabled"}),
    "coordination_runtime_shadow": frozenset({"enabled"}),
    "lark_kanban_heartbeat_sync": frozenset({"enabled"}),
    "periodic_report": frozenset(
        {"enabled", "profile_preset", "route_ref", "timezone", "schedule", "task", "review_required"}
    ),
    "reward_memory": frozenset({"config_path", "enabled_agents", "agent_id", "enabled"}),
}


def boolean_configuration(
    capability_id: str,
    configuration: Mapping[str, object],
    key: str,
    *,
    default: bool | None = None,
) -> bool:
    """Validate and extract a boolean capability setting."""
    value = configuration.get(key, default)
    if not isinstance(value, bool):
        raise TypeError(f"{capability_id}.{key} must be a boolean")
    return value


def subagent_model_options(config: Mapping[str, object]) -> dict[str, object]:
    """Translate subagent model and reasoning effort settings."""
    if "model" not in config and "reasoning_effort" not in config:
        return {}
    model = config.get("model", "")
    effort = config.get("reasoning_effort", "")
    if not isinstance(model, str) or not isinstance(effort, str):
        raise ValueError("child model and reasoning effort must be strings")
    if not model:
        if effort:
            raise ValueError("child reasoning effort requires a model")
        return dict(subagent_model_configuration_options(None))
    preference: dict[str, object] = {"model": model}
    if effort:
        preference["reasoning_effort"] = effort
    return dict(subagent_model_configuration_options(preference))


def multi_subagent_options(config: Mapping[str, object]) -> dict[str, object]:
    """Translate multi-subagent capacity, domain, and execution configuration options."""
    model_options = subagent_model_options(config)
    execution_options: dict[str, object] = {}
    if "execution_config" in config:
        raw_execution = config.get("execution_config")
        if raw_execution is not None and not isinstance(raw_execution, str):
            raise TypeError("multi_subagent.execution_config must be a string")
        cleaned = str(raw_execution or "").strip()
        execution_options = (
            {"subagent_execution_config": cleaned}
            if cleaned
            else {"clear_subagent_execution_config": True}
        )
    if not boolean_configuration("multi_subagent", config, "enabled"):
        return {
            "multi_subagent_feature": "off",
            **model_options,
            **execution_options,
        }
    max_children = config.get("max_children", 4)
    if not isinstance(max_children, int) or isinstance(max_children, bool):
        raise TypeError("multi_subagent.max_children must be an integer")
    domains = config.get("allowed_domains", [])
    if not isinstance(domains, list) or any(not isinstance(item, str) for item in domains):
        raise TypeError("multi_subagent.allowed_domains must be a string list")
    return {
        "multi_subagent_feature": "enabled",
        "max_children": max_children,
        "allowed_domains": domains,
        "align_codex_subagent_capacity": True,
        **model_options,
        **execution_options,
    }


def peer_task_coordination_options(config: Mapping[str, object]) -> dict[str, object]:
    """Translate peer-task coordinator assignment or clear flag."""
    coordinator = str(config.get("coordinator_agent_id") or "").strip()
    if coordinator:
        return {"peer_task_coordinator": coordinator}
    return {"clear_peer_task_coordinator": True}


def explore_harness_options(config: Mapping[str, object]) -> dict[str, object]:
    """Translate explore harness mode, profile, and composition options."""
    profile = str(config.get("profile") or "").strip() or None
    composition_mode = config.get("composition_mode", "disabled")
    scope = config.get("composition_scope_id")
    if not isinstance(composition_mode, str) or (scope is not None and not isinstance(scope, str)):
        raise TypeError("Explore composition mode and scope must be strings")
    if "mode" in config and "enabled" in config:
        raise ValueError("Use Explore mode or the legacy enabled flag, not both")
    mode_options: dict[str, object] = {}
    if "mode" in config:
        mode_options["explore_mode"] = config["mode"]
    elif "enabled" in config:
        mode_options["explore_harness_enabled"] = boolean_configuration("explore_harness", config, "enabled")
    return {
        **mode_options,
        "explore_harness_profile": profile,
        "clear_explore_harness_profile": profile is None,
        "explore_composition_mode": composition_mode or "disabled",
        "explore_composition_scope_id": scope or "",
    }


def change_quality_options(config: Mapping[str, object]) -> dict[str, object]:
    """Translate change-quality qualification flags."""
    capability_id = "change_quality_qualification"
    return {
        "change_quality_enabled": boolean_configuration(capability_id, config, "enabled"),
        "change_quality_safe_fix": boolean_configuration(capability_id, config, "safe_fix", default=False),
        "change_quality_strict_receipt": boolean_configuration(
            capability_id, config, "strict_receipt", default=False
        ),
    }


def progress_review_options(config: Mapping[str, object]) -> dict[str, object]:
    """Translate progress-review policy configuration options."""
    from ..capabilities.progress_review.policy import (
        normalize_progress_review_contract_revision,
        normalize_progress_review_drift_threshold,
        normalize_progress_review_mode,
        normalize_progress_review_signal,
    )

    mode = config.get("mode")
    signal = config.get("signal")
    threshold = config.get("drift_threshold")
    return {
        "progress_review_contract_revision": (
            normalize_progress_review_contract_revision(config.get("contract_revision"))
            if "contract_revision" in config
            else None
        ),
        "progress_review_mode": normalize_progress_review_mode(mode) if mode is not None else None,
        "progress_review_signal": normalize_progress_review_signal(signal) if signal is not None else None,
        "progress_review_drift_threshold": (
            normalize_progress_review_drift_threshold(threshold) if threshold is not None else None
        ),
    }


def todo_replan_cadence_options(config: Mapping[str, object]) -> dict[str, object]:
    """Translate Goal replan-cadence sparse overrides and horizon/yardstick flags."""
    from ..capabilities.todo_replan_cadence.machine_defaults import normalize_replan_cadence_configuration

    horizon: dict[str, object] = {"clear_execution_planning_horizon_goal": True}
    if "planning_horizon_goal" in config:
        planning_horizon_goal = config["planning_horizon_goal"]
        if not isinstance(planning_horizon_goal, bool):
            raise ValueError("planning_horizon_goal must be a boolean")
        horizon = {"execution_planning_horizon_goal": planning_horizon_goal}
    yardstick: dict[str, object] = {"clear_execution_yardstick_enabled": True}
    if "yardstick_enabled" in config:
        yardstick_enabled = config["yardstick_enabled"]
        if not isinstance(yardstick_enabled, bool):
            raise ValueError("yardstick_enabled must be a boolean")
        yardstick = {"execution_yardstick_enabled": yardstick_enabled}
    if config and set(config) <= {"planning_horizon_goal", "yardstick_enabled"}:
        # The capability editor submits the complete sparse value; omitting
        # the interval restores live inheritance even when this Goal was
        # previously pinned.
        return {
            **horizon,
            **yardstick,
            "clear_execution_replan_after_turns": True,
            "clear_execution_replan_after_todos": True,
        }
    cadence = normalize_replan_cadence_configuration(dict(config))
    option = (
        "execution_replan_after_turns"
        if cadence["count_unit"] == "effective_turns"
        else "execution_replan_after_todos"
    )
    return {option: cadence["count"], **horizon, **yardstick}


def reward_memory_options(config: Mapping[str, object]) -> dict[str, object]:
    """Translate Goal reward-memory configuration and per-agent disable requests."""
    config_path = config.get("config_path")
    if config_path is not None and not isinstance(config_path, str):
        raise TypeError("reward_memory.config_path must be a string")
    enabled_agents = config.get("enabled_agents")
    if enabled_agents is not None and (
        not isinstance(enabled_agents, list) or any(not isinstance(value, str) for value in enabled_agents)
    ):
        raise TypeError("reward_memory.enabled_agents must be a string list")
    raw_agent_id = config.get("agent_id")
    if raw_agent_id is not None and not isinstance(raw_agent_id, str):
        raise TypeError("reward_memory.agent_id must be a string")
    agent_id = str(raw_agent_id or "").strip() or None
    enabled = config.get("enabled")
    if "enabled" in config and not isinstance(enabled, bool):
        raise TypeError("reward_memory.enabled must be boolean")
    if agent_id and enabled is False:
        if config_path or enabled_agents is not None:
            raise ValueError("disabling one Agent accepts only agent_id and enabled=false")
        return {"reward_memory_agent_id": agent_id, "clear_reward_memory_config": True}
    if not agent_id and enabled is not None:
        raise ValueError("reward_memory.enabled requires agent_id; use null to clear all bindings")
    return {
        "reward_memory_config": str(config_path or "").strip() or None,
        "reward_memory_agents": enabled_agents,
        **({"reward_memory_agent_id": agent_id} if agent_id else {}),
    }


def _periodic_report_options(config: Mapping[str, object]) -> dict[str, object]:
    from ..capabilities.periodic_report.goal_configuration import (
        normalize_configuration as normalize_periodic_report_configuration,
    )

    # Validate the payload before the Session guard reads native sources.
    return {"periodic_report_configuration": normalize_periodic_report_configuration(dict(config))}


def _pr_review_options(config: Mapping[str, object]) -> dict[str, object]:
    from ..capabilities.pr_review_queue.goal_configuration import (
        normalize_configuration as normalize_pr_review_configuration,
    )

    return {"pull_request_review_configuration": normalize_pr_review_configuration(dict(config))}


def _enabled_flag_options(option_key: str, *, clear_key: str | None = None) -> Callable[[Mapping[str, object]], dict[str, object]]:
    def translate(capability_id: str, config: Mapping[str, object]) -> dict[str, object]:
        enabled = boolean_configuration(capability_id, config, "enabled")
        if enabled:
            return {option_key: True}
        if clear_key is not None:
            return {clear_key: True}
        return {option_key: False}

    def wrapped(config: Mapping[str, object]) -> dict[str, object]:
        return translate(_FLAG_OWNER_BY_OPTION[option_key], config)

    return wrapped


_FLAG_OWNER_BY_OPTION: dict[str, str] = {
    "explore_graph_enabled": "explore_graph",
    "local_authority_shadow_file": "local_authority_shadow",
    "coordination_runtime_shadow_file": "coordination_runtime_shadow",
    "lark_kanban_heartbeat_sync": "lark_kanban_heartbeat_sync",
}

_CAPABILITY_OPTION_TRANSLATORS: dict[str, Callable[[Mapping[str, object]], dict[str, object]]] = {
    "multi_subagent": multi_subagent_options,
    "goal_capability_organization": lambda config: {"capability_improvement_configuration": dict(config)},
    "todo_replan_cadence": todo_replan_cadence_options,
    "periodic_report": _periodic_report_options,
    "reward_memory": reward_memory_options,
    "peer_task_coordination": peer_task_coordination_options,
    "explore_graph": _enabled_flag_options("explore_graph_enabled"),
    "explore_harness": explore_harness_options,
    "pull_request_review": _pr_review_options,
    "change_quality_qualification": change_quality_options,
    "progress_review": progress_review_options,
    "local_authority_shadow": _enabled_flag_options(
        "local_authority_shadow_file", clear_key="clear_local_authority_shadow"
    ),
    "coordination_runtime_shadow": _enabled_flag_options(
        "coordination_runtime_shadow_file", clear_key="clear_coordination_runtime_shadow"
    ),
    "lark_kanban_heartbeat_sync": _enabled_flag_options("lark_kanban_heartbeat_sync"),
}


def goal_capability_options(
    capability_id: str,
    configuration: Mapping[str, object] | None,
) -> dict[str, object]:
    """Translate one validated UI contract into configure-goal options."""
    if configuration is None:
        if capability_id in _CLEAR_CAPABILITY_OPTIONS:
            return dict(_CLEAR_CAPABILITY_OPTIONS[capability_id])
        raise ValueError(f"Goal capability cannot be cleared: {capability_id}")
    if capability_id not in _ALLOWED_CAPABILITY_FIELDS:
        raise ValueError(f"Goal capability is read-only in Dashboard: {capability_id}")
    if capability_id == "todo_replan_cadence" and "stride_strategy" in configuration:
        from ..capabilities.todo_replan_cadence.machine_defaults import reject_removed_stride_strategy

        reject_removed_stride_strategy(dict(configuration))
    unknown = sorted(set(configuration) - _ALLOWED_CAPABILITY_FIELDS[capability_id])
    if unknown:
        raise ValueError(f"unknown {capability_id} configuration field(s): {', '.join(unknown)}")
    return _CAPABILITY_OPTION_TRANSLATORS[capability_id](configuration)


__all__ = [
    "boolean_configuration",
    "change_quality_options",
    "explore_harness_options",
    "goal_capability_options",
    "multi_subagent_options",
    "peer_task_coordination_options",
    "progress_review_options",
    "reward_memory_options",
    "subagent_model_options",
    "todo_replan_cadence_options",
]
