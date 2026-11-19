from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..machine_configuration.contract import MachineConfigurationNamespace

GOAL_CLOSEOUT_CONFIGURATION_SCHEMA = "loopx_goal_closeout_configuration_v0"
DEFAULT_CLOSEOUT_TIMEOUT_SECONDS = 30
MAX_CLOSEOUT_TIMEOUT_SECONDS = 600


def normalize_goal_closeout_configuration(
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    unknown = sorted(set(raw) - {"schema_version", "command", "timeout_seconds"})
    if unknown:
        raise ValueError(
            "goal_closeout contains unsupported fields: " + ", ".join(unknown)
        )
    if raw.get("schema_version") != GOAL_CLOSEOUT_CONFIGURATION_SCHEMA:
        raise ValueError(
            "goal_closeout must use " + GOAL_CLOSEOUT_CONFIGURATION_SCHEMA
        )
    raw_command = raw.get("command", [])
    if not isinstance(raw_command, (list, tuple)):
        raise ValueError("goal_closeout command must be an array of strings")
    command: list[str] = []
    for part in raw_command:
        if not isinstance(part, str) or not part.strip():
            raise ValueError(
                "goal_closeout command entries must be non-empty strings"
            )
        command.append(part)
    raw_timeout = raw.get("timeout_seconds", DEFAULT_CLOSEOUT_TIMEOUT_SECONDS)
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, int):
        raise ValueError("goal_closeout timeout_seconds must be an integer")
    if not 1 <= raw_timeout <= MAX_CLOSEOUT_TIMEOUT_SECONDS:
        raise ValueError(
            "goal_closeout timeout_seconds must be between 1 and "
            f"{MAX_CLOSEOUT_TIMEOUT_SECONDS}"
        )
    return {
        "schema_version": GOAL_CLOSEOUT_CONFIGURATION_SCHEMA,
        "command": command,
        "timeout_seconds": raw_timeout,
    }


def goal_closeout_machine_configuration_namespace() -> MachineConfigurationNamespace:
    return MachineConfigurationNamespace(
        namespace="goal_closeout",
        schema_versions=frozenset({GOAL_CLOSEOUT_CONFIGURATION_SCHEMA}),
        normalize=normalize_goal_closeout_configuration,
        project_public=lambda value: dict(value),
        apply_public_update=lambda _current, update: dict(update),
        title="Terminal Goal closeout command",
        description=(
            "Optional machine-wide command run after a Goal reaches a terminal "
            "transition (stop, retirement or runtime archive). The command is "
            "best effort, receives one bounded JSON payload on stdin, and can "
            "never block the LoopX command that triggered it."
        ),
        default_configuration={
            "schema_version": GOAL_CLOSEOUT_CONFIGURATION_SCHEMA,
            "command": [],
            "timeout_seconds": DEFAULT_CLOSEOUT_TIMEOUT_SECONDS,
        },
    )
