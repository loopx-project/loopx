"""Codex-provider preferences for new ordinary Goal conversations.

The existing machine configuration transaction owns persistence and rollback.
These preferences neither select a worker's loop nor change an attached Session.
Profile aliases resolve existing native homes; credentials are never copied.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any

from ...reasoning_effort import REASONING_EFFORTS
from ..machine_configuration.contract import MachineConfigurationNamespace

NAMESPACE = "codex_chat"
CODEX_CHAT_DEFAULTS_SCHEMA_VERSION = "codex_chat_machine_defaults_v0"
_FIELDS = ("codex_profile", "model", "reasoning_effort", "service_tier")


def normalize_codex_chat_defaults(raw: Mapping[str, Any]) -> dict[str, Any]:
    if raw.get("schema_version") != CODEX_CHAT_DEFAULTS_SCHEMA_VERSION or set(raw) - {"schema_version", *_FIELDS}:
        raise ValueError("Codex Chat defaults contain an unsupported schema or field")
    result: dict[str, Any] = {"schema_version": CODEX_CHAT_DEFAULTS_SCHEMA_VERSION}
    for key in _FIELDS:
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 160
                                  or any(ord(char) < 32 for char in value)):
            raise ValueError(f"codex_chat.{key} must be bounded visible text")
        result[key] = value.strip() or None if isinstance(value, str) else None
    profile = result["codex_profile"]
    # An alias names an existing native configuration home, never an input path.
    if profile is not None and not re.fullmatch(r"codex(?:-[A-Za-z0-9_-]+)?", profile):
        raise ValueError("codex_chat.codex_profile must name a Codex profile alias")
    if result["reasoning_effort"] is not None and result["reasoning_effort"] not in REASONING_EFFORTS:
        raise ValueError("codex_chat.reasoning_effort is unsupported")
    return result


def codex_profile_options() -> list[str]:
    from ...codex_app_thread_activity import codex_homes
    return sorted({home.name.removeprefix(".") for home in codex_homes()
                   if re.fullmatch(r"\.codex(?:-[A-Za-z0-9_-]+)?", home.name)})


def codex_profile_home(base: Path, configuration: Mapping[str, Any]) -> Path:
    profile = configuration.get("codex_profile")
    if not profile:
        return base
    from ...codex_app_thread_activity import codex_homes
    matches = {home.resolve() for home in codex_homes() if home.name == f".{profile}"}
    if len(matches) != 1:
        raise ValueError("The selected Codex profile is unavailable or ambiguous; repair Codex conversation settings.")
    return matches.pop()


def load_codex_chat_defaults(runtime_root: Path) -> dict[str, Any]:
    from ..machine_configuration.store import read_stored_machine_configuration
    configuration = read_stored_machine_configuration(runtime_root)
    namespaces = (configuration or {}).get("namespaces", {})
    if not isinstance(namespaces, Mapping):
        raise ValueError("Codex conversation settings are unreadable; repair machine settings.")
    raw = namespaces.get(NAMESPACE)
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("Codex conversation settings must be an object.")
    normalized = normalize_codex_chat_defaults(raw)
    return normalized if any(normalized[key] for key in _FIELDS) else {}


def codex_chat_machine_configuration_namespace() -> MachineConfigurationNamespace:
    return MachineConfigurationNamespace(
        namespace=NAMESPACE, schema_versions=frozenset({CODEX_CHAT_DEFAULTS_SCHEMA_VERSION}),
        normalize=normalize_codex_chat_defaults,
        project_public=lambda value: dict(value),
        apply_public_update=lambda _current, update: dict(update),
        title="Codex Goal conversation defaults",
        description=("Choose the native Codex profile, model, reasoning effort and service tier for new Goal conversations. "
                     "Existing conversations keep their original identity and history. This does not configure the steward, "
                     "an attached Agent, or a LoopX Agent Loop; it grants no permissions and stores no credentials."),
        documentation={"path": "docs/integrations/session-runtime-control-plane-adapter.md#projection-boundary"},
        default_configuration={"schema_version": CODEX_CHAT_DEFAULTS_SCHEMA_VERSION, **dict.fromkeys(_FIELDS)},
    )
