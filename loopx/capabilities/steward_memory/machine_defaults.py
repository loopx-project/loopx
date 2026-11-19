"""Machine configuration defaults and runtime owner for isolated steward memory."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..machine_configuration.contract import MachineConfigurationNamespace

STEWARD_MEMORY_NAMESPACE = "steward_memory"
STEWARD_MEMORY_SCHEMA = "steward_memory_machine_defaults_v0"
DEFAULT_USER_SPACE = "steward"
DEFAULT_RECALL_TIMEOUT_SECONDS = 3
MIN_RECALL_TIMEOUT_SECONDS = 1
MAX_RECALL_TIMEOUT_SECONDS = 15


def default_steward_memory_configuration() -> dict[str, Any]:
    """Return the default-off machine configuration for isolated steward memory."""
    return {
        "schema_version": STEWARD_MEMORY_SCHEMA,
        "enabled": False,
        "user_space": DEFAULT_USER_SPACE,
        "auto_recall": True,
        "recall_timeout_seconds": DEFAULT_RECALL_TIMEOUT_SECONDS,
    }


def normalize_steward_memory_configuration(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize machine-level steward memory defaults."""
    from ..context_providers.openviking_scope import OPENVIKING_IDENTITY_RE

    allowed = {
        "schema_version",
        "enabled",
        "user_space",
        "auto_recall",
        "recall_timeout_seconds",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(
            "steward_memory contains unsupported fields: " + ", ".join(unknown)
        )
    if raw.get("schema_version") != STEWARD_MEMORY_SCHEMA:
        raise ValueError(f"steward_memory must use {STEWARD_MEMORY_SCHEMA}")
    if type(raw.get("enabled")) is not bool:
        raise ValueError("steward_memory.enabled must be a boolean")
    user_space = str(raw.get("user_space") or "").strip()
    if not OPENVIKING_IDENTITY_RE.fullmatch(user_space):
        raise ValueError("steward_memory.user_space must use a safe identifier")
    if type(raw.get("auto_recall")) is not bool:
        raise ValueError("steward_memory.auto_recall must be a boolean")
    timeout = raw.get("recall_timeout_seconds")
    if (
        type(timeout) is not int
        or not MIN_RECALL_TIMEOUT_SECONDS <= timeout <= MAX_RECALL_TIMEOUT_SECONDS
    ):
        raise ValueError(
            "steward_memory.recall_timeout_seconds must be an integer between 1 and 15"
        )
    return {
        "schema_version": STEWARD_MEMORY_SCHEMA,
        "enabled": raw["enabled"],
        "user_space": user_space,
        "auto_recall": raw["auto_recall"],
        "recall_timeout_seconds": timeout,
    }


def steward_memory_machine_configuration_namespace() -> MachineConfigurationNamespace:
    """Describe the steward_memory machine configuration owner."""
    return MachineConfigurationNamespace(
        namespace=STEWARD_MEMORY_NAMESPACE,
        schema_versions=frozenset({STEWARD_MEMORY_SCHEMA}),
        normalize=normalize_steward_memory_configuration,
        project_public=lambda value: dict(value),
        apply_public_update=lambda _current, update: dict(update),
        title="Steward OpenViking memory",
        description=(
            "Enables an isolated OpenViking memory namespace for owner private steward "
            "conversations with per-speaker preferences, bounded recall timeouts, and "
            "strict non-leakage to group chats or worker Agents."
        ),
        default_configuration=default_steward_memory_configuration(),
    )


def load_effective_steward_memory_setting(runtime_root: Path) -> dict[str, Any]:
    """Read normalized steward_memory machine configuration or return default-off."""
    from ..machine_configuration.builtins import build_builtin_machine_configuration_registry
    from ..machine_configuration.store import read_machine_configuration

    registry = build_builtin_machine_configuration_registry()
    configuration = read_machine_configuration(
        runtime_root,
        registry=registry,
        namespace_ids=(STEWARD_MEMORY_NAMESPACE,),
    )
    stored = (configuration or {}).get("namespaces", {}).get(STEWARD_MEMORY_NAMESPACE)
    if isinstance(stored, Mapping):
        return normalize_steward_memory_configuration(stored)
    return default_steward_memory_configuration()
