"""Goal-level coordination configuration, independent of optional producers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .coordination_state_contract_generated import LOCAL_AUTHORITY_SHADOW_CONFIG_SCHEMA


RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION = "loopx_coordination_runtime_shadow_config_v0"


def normalize_goal_write_scope(values: list[str] | None) -> list[str] | None:
    """Split comma-delimited goal scopes and preserve first-seen order.

    Goal configuration intentionally accepts a wider token vocabulary than a
    Todo's ``required_write_scopes`` contract, so its stricter normalizer is
    not interchangeable with this rule.
    """

    if values is None:
        return None
    scopes: list[str] = []
    for value in values:
        for part in str(value).split(","):
            scope = part.strip()
            if scope and scope not in scopes:
                scopes.append(scope)
    return scopes

__all__ = ["normalize_goal_write_scope"]

@dataclass(frozen=True)
class CoordinationRuntimeShadowConfig:
    enabled: bool
    provider: str | None
    reason_code: str


def local_authority_shadow_summary(goal: Mapping[str, Any] | None) -> dict[str, Any]:
    """Recognize retained config without granting it a writer or capture lineage."""
    coordination = goal.get("coordination") if isinstance(goal, Mapping) else None
    if not isinstance(coordination, Mapping) or "authority_shadow" not in coordination:
        return {"enabled": False, "mode": None, "status": "disabled"}
    raw = coordination["authority_shadow"]
    valid = (isinstance(raw, Mapping) and set(raw) == {"schema_version", "mode"}
             and raw.get("schema_version") == LOCAL_AUTHORITY_SHADOW_CONFIG_SCHEMA
             and raw.get("mode") == "file_one_way")
    return {"enabled": False, "mode": raw.get("mode") if isinstance(raw, Mapping) else None,
            "status": "retired" if valid else "invalid", "configured": True,
            "replacement": "coordination_runtime_shadow"}


def validate_local_authority_shadow_change(enable_file: bool, clear: bool) -> None:
    if enable_file and clear:
        raise ValueError("--local-authority-shadow-file cannot be combined with --clear-local-authority-shadow")
    if enable_file:
        raise ValueError(
            "local_authority_shadow_retired: post-commit observation is retired; "
            "clear it with --clear-local-authority-shadow; explicitly configure "
            "--coordination-runtime-shadow-file and run coordination-shadow bootstrap "
            "before transaction-bound capture. Retained observations are not migration evidence."
        )


def apply_local_authority_shadow_change(goal: dict[str, Any], enable_file: bool, clear: bool) -> None:
    validate_local_authority_shadow_change(enable_file, clear)
    if not clear:
        return
    coordination = goal.get("coordination")
    if isinstance(coordination, dict):
        coordination.pop("authority_shadow", None)
        if not coordination:
            goal.pop("coordination", None)


def coordination_shadow_summaries(
    goal: Mapping[str, Any] | None,
) -> dict[str, dict[str, object]]:
    """Report the active capture configuration and any retired observation setting."""

    return {
        "local_authority_shadow": local_authority_shadow_summary(
            goal
        ),
        "coordination_runtime_shadow": coordination_runtime_shadow_summary(goal),
    }


def validate_coordination_shadow_changes(
    local_enable_file: bool,
    local_clear: bool,
    runtime_enable_file: bool,
    runtime_clear: bool,
) -> None:
    """Reject retired activation before any registry mutation."""

    validate_local_authority_shadow_change(
        local_enable_file, local_clear
    )
    validate_coordination_runtime_shadow_change(runtime_enable_file, runtime_clear)


def apply_coordination_shadow_changes(
    goal: dict[str, Any],
    local_enable_file: bool,
    local_clear: bool,
    runtime_enable_file: bool,
    runtime_clear: bool,
) -> None:
    """Clear retired settings and configure the transaction-bound shadow independently."""

    apply_local_authority_shadow_change(
        goal, local_enable_file, local_clear
    )
    apply_coordination_runtime_shadow_change(goal, runtime_enable_file, runtime_clear)


def coordination_runtime_shadow_summary(
    goal: Mapping[str, Any] | None,
) -> dict[str, object]:
    """Project the transaction-bound shadow configuration for operators."""

    config = resolve_coordination_runtime_shadow_config(goal)
    return {
        "enabled": config.enabled,
        "provider": config.provider,
        "status": "enabled" if config.enabled else config.reason_code,
    }


def validate_coordination_runtime_shadow_change(
    enable_file: bool,
    clear: bool,
) -> None:
    if enable_file and clear:
        raise ValueError(
            "--coordination-runtime-shadow-file cannot be combined with "
            "--clear-coordination-runtime-shadow"
        )


def apply_coordination_runtime_shadow_change(
    goal: dict[str, Any],
    enable_file: bool,
    clear: bool,
) -> None:
    """Apply the explicit transaction-bound file-shadow opt-in."""

    if not enable_file and not clear:
        return
    coordination = (
        goal.get("coordination") if isinstance(goal.get("coordination"), dict) else {}
    )
    if clear:
        coordination.pop("runtime_shadow", None)
    else:
        coordination["runtime_shadow"] = {
            "enabled": True,
            "schema_version": RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
            "provider": "file_v0",
        }
    if coordination:
        goal["coordination"] = coordination
    else:
        goal.pop("coordination", None)


def resolve_coordination_runtime_shadow_config(
    goal: Mapping[str, Any] | None,
) -> CoordinationRuntimeShadowConfig:
    """Resolve one explicit file-shadow opt-in without changing legacy defaults."""

    if not isinstance(goal, Mapping):
        return CoordinationRuntimeShadowConfig(False, None, "goal_missing")
    coordination = goal.get("coordination")
    if not isinstance(coordination, Mapping):
        return CoordinationRuntimeShadowConfig(False, None, "configuration_absent")
    configured = coordination.get("runtime_shadow")
    if configured is None:
        return CoordinationRuntimeShadowConfig(False, None, "configuration_absent")
    if not isinstance(configured, Mapping):
        return CoordinationRuntimeShadowConfig(False, None, "configuration_invalid")
    if configured.get("enabled") is not True:
        return CoordinationRuntimeShadowConfig(False, None, "explicitly_disabled")
    if configured.get("schema_version") != RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION:
        return CoordinationRuntimeShadowConfig(False, None, "schema_mismatch")
    provider = configured.get("provider")
    if provider != "file_v0":
        return CoordinationRuntimeShadowConfig(False, None, "provider_unsupported")
    return CoordinationRuntimeShadowConfig(True, provider, "explicit_opt_in")
