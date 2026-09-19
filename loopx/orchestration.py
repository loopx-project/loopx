from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
import re

from .agent_registry import normalize_registered_agents

DEFAULT_ORCHESTRATION_MODE = "default"
MULTI_SUBAGENT_ORCHESTRATION_MODE = "multi_subagent"
VALID_ORCHESTRATION_MODES = {
    DEFAULT_ORCHESTRATION_MODE,
    MULTI_SUBAGENT_ORCHESTRATION_MODE,
}
EXPLORE_HARNESS_PROFILES = (
    "generic",
    "adaptive-resilient",
    "moe-router",
)


SUBAGENT_REASONING_EFFORTS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)


def validate_subagent_model_config(value: Any) -> dict[str, str]:
    """Validate launch preferences, without pretending to know host model availability."""
    if not isinstance(value, dict) or set(value) - {"model", "reasoning_effort"}:
        raise ValueError(
            "subagent model config requires model and optional reasoning_effort"
        )
    model = value.get("model")
    if not isinstance(model, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", model
    ):
        raise ValueError("subagent model must be a non-empty model identifier")
    result = {"model": model}
    if "reasoning_effort" in value:
        effort = value["reasoning_effort"]
        if effort not in SUBAGENT_REASONING_EFFORTS:
            raise ValueError("unsupported subagent reasoning effort")
        result["reasoning_effort"] = effort
    return result


def normalize_subagent_execution_config(value: Any) -> str:
    """Normalize the Goal's local-private delegation binding pointer.

    The pointer is safe to persist and project because it is repo-relative;
    the binding file itself remains ignored local state and is never copied
    into Goal context.
    """

    text = str(value or "").strip().replace("\\", "/")
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
            "subagent execution config must be a repo-relative JSON path "
            "under .loopx/config/"
        )
    return path.as_posix()


def subagent_model_configuration_options(value: Any) -> dict[str, Any]:
    """Translate a complete UI preference: null clears, an object replaces."""
    if value is None:
        return {"clear_subagent_model_config": True}
    config = validate_subagent_model_config(value)
    return {
        "subagent_model": config["model"],
        "subagent_reasoning_effort": config.get("reasoning_effort", ""),
    }


def _int_number(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _spawn_allowed(policy: dict[str, Any]) -> bool:
    return policy.get("allowed") is True or policy.get("spawn_allowed") is True


def orchestration_mode_from_spawn_policy(spawn_policy: Any) -> str:
    if not isinstance(spawn_policy, dict):
        return DEFAULT_ORCHESTRATION_MODE
    explicit_mode = str(
        spawn_policy.get("mode") or spawn_policy.get("orchestration_mode") or ""
    ).strip()
    if explicit_mode in VALID_ORCHESTRATION_MODES:
        return explicit_mode
    spawn_allowed = _spawn_allowed(spawn_policy)
    if spawn_allowed and _int_number(spawn_policy.get("max_children")) > 0:
        return MULTI_SUBAGENT_ORCHESTRATION_MODE
    return DEFAULT_ORCHESTRATION_MODE


def compact_explore_harness_policy(policy: Any) -> dict[str, Any]:
    """Normalize the per-goal explore-harness opt-in gate.

    Absence of the section means the gate is closed. The opt-in bit is a
    deny-by-default switch, so it fails closed: only a real boolean ``true``
    opens it -- truthy strings like ``"false"`` or ``"true"`` stay closed
    instead of silently inverting a hand-edited deny. ``profile`` is carried
    only when the goal explicitly pins one, so planners can tell a pinned
    profile apart from their own default.
    """

    harness = policy if isinstance(policy, dict) else {}
    compact: dict[str, Any] = {"enabled": harness.get("enabled") is True}
    profile = str(harness.get("profile") or "").strip()
    if profile:
        compact["profile"] = profile
    return compact


def compact_orchestration_policy(spawn_policy: Any) -> dict[str, Any]:
    policy = spawn_policy if isinstance(spawn_policy, dict) else {}
    max_children = max(0, _int_number(policy.get("max_children")))
    allowed_domains = policy.get("allowed_domains")
    if not isinstance(allowed_domains, list):
        allowed_domains = []
    compact: dict[str, Any] = {
        "mode": orchestration_mode_from_spawn_policy(policy),
        "spawn_allowed": _spawn_allowed(policy),
        "max_children": max_children,
    }
    if "model_config" in policy:
        compact["model_config"] = validate_subagent_model_config(policy["model_config"])
    if policy.get("execution_config"):
        compact["execution_config"] = normalize_subagent_execution_config(
            policy["execution_config"]
        )
    compact_domains = [str(value) for value in allowed_domains if str(value).strip()]
    if compact_domains:
        compact["allowed_domains"] = compact_domains
    if isinstance(policy.get("explore_harness"), dict):
        compact["explore_harness"] = compact_explore_harness_policy(
            policy.get("explore_harness")
        )
    return compact


def compact_peer_task_coordination_policy(coordination: Any) -> dict[str, Any] | None:
    """Normalize the explicit registered-peer task coordinator selection.

    Registered peer identity is not coordination authority.  The policy is
    therefore fail-closed: only a non-empty ``coordinator_agent_id`` opts a
    goal into peer task coordination.  Runtime capability admission remains a
    separate, per-turn decision.
    """

    policy = coordination if isinstance(coordination, dict) else {}
    peer_policy = (
        policy.get("peer_task_coordination")
        if isinstance(policy.get("peer_task_coordination"), dict)
        else {}
    )
    coordinator_agent_ids = normalize_registered_agents(
        [peer_policy.get("coordinator_agent_id")]
    )
    if not coordinator_agent_ids:
        return None
    return {
        "enabled": True,
        "coordinator_agent_id": coordinator_agent_ids[0],
    }


def orchestration_policy_summary(policy: dict[str, Any] | None) -> str:
    compact = compact_orchestration_policy(policy)
    summary = (
        f"mode={compact.get('mode')} "
        f"spawn_allowed={compact.get('spawn_allowed')} "
        f"max_children={compact.get('max_children')}"
    )
    harness = compact.get("explore_harness")
    if isinstance(harness, dict):
        state = "on" if harness.get("enabled") else "off"
        profile = str(harness.get("profile") or "")
        if harness.get("enabled") and profile:
            state = f"on({profile})"
        summary += f" explore_harness={state}"
    return summary


def _update_subagent_model_preference(
    spawn_policy: dict[str, Any],
    *,
    subagent_model: str | None,
    subagent_reasoning_effort: str | None,
    clear_subagent_model_config: bool,
) -> None:
    """Update only the model preference; leaving it absent preserves host defaults."""
    if clear_subagent_model_config:
        spawn_policy.pop("model_config", None)
    elif subagent_model is not None or subagent_reasoning_effort is not None:
        model_config = dict(spawn_policy.get("model_config") or {})
        if subagent_model is not None:
            model_config["model"] = subagent_model
        if subagent_reasoning_effort is not None:
            if subagent_reasoning_effort:
                model_config["reasoning_effort"] = subagent_reasoning_effort
            else:
                model_config.pop("reasoning_effort", None)
        spawn_policy["model_config"] = validate_subagent_model_config(model_config)


def update_spawn_execution_policy(
    spawn_policy: dict[str, Any],
    *,
    multi_subagent_feature: str | None,
    orchestration_mode: str | None,
    spawn_allowed: bool | None,
    max_children: int | None,
    subagent_model: str | None,
    subagent_reasoning_effort: str | None,
    clear_subagent_model_config: bool,
    subagent_execution_config: str | None,
    clear_subagent_execution_config: bool,
    allowed_domains: list[str] | None,
    clear_allowed_domains: bool,
    default_max_children: int,
) -> None:
    """Apply validated execution options to the caller's transaction-local policy."""
    _update_subagent_model_preference(
        spawn_policy,
        subagent_model=subagent_model,
        subagent_reasoning_effort=subagent_reasoning_effort,
        clear_subagent_model_config=clear_subagent_model_config,
    )
    if clear_subagent_execution_config:
        spawn_policy.pop("execution_config", None)
    elif subagent_execution_config is not None:
        spawn_policy["execution_config"] = normalize_subagent_execution_config(
            subagent_execution_config
        )
    if multi_subagent_feature == "enabled":
        spawn_policy["mode"] = MULTI_SUBAGENT_ORCHESTRATION_MODE
        spawn_policy["allowed"] = True
        if max_children is None:
            existing_children = int(
                compact_orchestration_policy(spawn_policy).get("max_children") or 0
            )
            spawn_policy["max_children"] = (
                existing_children if existing_children > 0 else default_max_children
            )
    elif multi_subagent_feature == "off":
        spawn_policy["mode"] = DEFAULT_ORCHESTRATION_MODE
        spawn_policy["allowed"] = False
        spawn_policy["max_children"] = 0
        spawn_policy["allowed_domains"] = []
    elif orchestration_mode is not None:
        spawn_policy["mode"] = orchestration_mode
    if spawn_allowed is not None:
        spawn_policy["allowed"] = spawn_allowed
    if max_children is not None:
        spawn_policy["max_children"] = max_children
    if clear_allowed_domains:
        spawn_policy["allowed_domains"] = []
    elif allowed_domains is not None:
        spawn_policy["allowed_domains"] = allowed_domains
