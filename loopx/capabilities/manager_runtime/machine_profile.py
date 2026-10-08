from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..machine_configuration.contract import (
    MACHINE_CONFIGURATION_SCHEMA,
    MachineConfigurationNamespace,
    machine_configuration_revision,
)

if TYPE_CHECKING:
    from ..native_chat.conversation_bindings import ChatConversationBindings

MANAGER_RUNTIME_PROFILE_SCHEMA = "manager_runtime_profile_v0"
MANAGER_RUNTIME_EFFECTIVE_SCHEMA = "manager_runtime_effective_profile_v0"
RESTRICTED_PROFILE = "restricted"
TRUSTED_OWNER_PROFILE = "trusted_owner"
SUPPORTED_MANAGER_RUNTIME_PROFILES = frozenset(
    {RESTRICTED_PROFILE, TRUSTED_OWNER_PROFILE}
)


def normalize_manager_runtime_profile(raw: Mapping[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(raw) - {"schema_version", "runtime_profile"})
    if unknown:
        raise ValueError(
            "manager_runtime contains unsupported fields: " + ", ".join(unknown)
        )
    if raw.get("schema_version") != MANAGER_RUNTIME_PROFILE_SCHEMA:
        raise ValueError(f"manager_runtime must use {MANAGER_RUNTIME_PROFILE_SCHEMA}")
    profile = str(raw.get("runtime_profile") or "").strip()
    if profile not in SUPPORTED_MANAGER_RUNTIME_PROFILES:
        raise ValueError(
            "manager_runtime.runtime_profile must be restricted or trusted_owner"
        )
    return {
        "schema_version": MANAGER_RUNTIME_PROFILE_SCHEMA,
        "runtime_profile": profile,
    }


def manager_runtime_machine_configuration_namespace() -> MachineConfigurationNamespace:
    return MachineConfigurationNamespace(
        namespace="manager_runtime",
        schema_versions=frozenset({MANAGER_RUNTIME_PROFILE_SCHEMA}),
        normalize=normalize_manager_runtime_profile,
        project_public=lambda value: dict(value),
        apply_public_update=lambda _current, update: dict(update),
        title="Manager runtime",
        description=(
            "Selects the effective host-tool profile for owner manager conversations. "
            "The trusted profile is an explicit persistent machine grant; protected "
            "operations and external provider permissions remain separately governed."
        ),
        documentation={
            "path": "docs/architecture/rfcs/manager-runtime-profile-v0.md",
            "url": (
                "https://github.com/loopx-project/loopx/blob/main/"
                "docs/architecture/rfcs/manager-runtime-profile-v0.md"
            ),
        },
        default_configuration={
            "schema_version": MANAGER_RUNTIME_PROFILE_SCHEMA,
            "runtime_profile": RESTRICTED_PROFILE,
        },
    )


def _projection(*, profile: str, source: str, revision: str) -> dict[str, Any]:
    trusted = profile == TRUSTED_OWNER_PROFILE
    return {
        "schema_version": MANAGER_RUNTIME_EFFECTIVE_SCHEMA,
        "runtime_profile": profile,
        "source": source,
        "configuration_revision": revision,
        "standing_grant": "machine_configuration" if trusted else "none",
        "sandbox": "danger-full-access" if trusted else "read-only",
        "approval_policy": "never",
        "tool_classes": (
            [
                "loopx_core",
                "filesystem",
                "shell",
                "git",
                "web",
                "configured_connectors",
            ]
            if trusted
            else ["loopx_core"]
        ),
    }


def effective_manager_runtime_profile(
    machine_configuration: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve a safe public runtime profile from the typed machine document."""

    if machine_configuration is None:
        return _projection(
            profile=RESTRICTED_PROFILE,
            source="capability_default",
            revision="absent",
        )
    if machine_configuration.get("schema_version") != MACHINE_CONFIGURATION_SCHEMA:
        raise ValueError(
            f"machine_configuration must use {MACHINE_CONFIGURATION_SCHEMA}"
        )
    unknown = sorted(set(machine_configuration) - {"schema_version", "namespaces"})
    if unknown:
        raise ValueError(
            "machine_configuration contains unsupported fields: " + ", ".join(unknown)
        )
    namespaces = machine_configuration.get("namespaces")
    if not isinstance(namespaces, Mapping):
        raise TypeError("machine_configuration.namespaces must be an object")
    raw = namespaces.get("manager_runtime")
    if raw is None:
        return _projection(
            profile=RESTRICTED_PROFILE,
            source="capability_default",
            revision="absent",
        )
    if not isinstance(raw, Mapping):
        raise TypeError(
            "machine_configuration.namespaces.manager_runtime must be an object"
        )
    normalized = normalize_manager_runtime_profile(raw)
    return _projection(
        profile=str(normalized["runtime_profile"]),
        source="machine_configuration",
        # The running manager only rotates when its own effective grant changes.
        # Sibling machine-capability updates must not disrupt a healthy chat.
        revision=machine_configuration_revision(normalized),
    )


def load_effective_manager_runtime_profile(
    runtime_root: Path,
    *,
    channel_id: str = "manager",
    owner_manager_audience: bool = False,
) -> dict[str, Any]:
    """Read the selected profile for one audience-bound manager channel.

    The machine choice is sufficient for the local owner conversation. Native
    bindings may also supply a freshly checked Core owner-audience proof. This
    proof alone grants no host tools; the persistent machine grant is required.
    Other external audiences always remain restricted.
    """

    from ..machine_configuration.store import read_stored_machine_configuration

    try:
        # Each namespace owns its runtime effect. A malformed sibling can make
        # whole-document editing unavailable, but it must not silently rewrite
        # an otherwise valid manager grant.
        configuration = read_stored_machine_configuration(runtime_root)
        effective = {
            **effective_manager_runtime_profile(configuration),
            "status": "ready",
        }
        if (
            channel_id != "manager"
            and owner_manager_audience is not True
            and effective["runtime_profile"] == TRUSTED_OWNER_PROFILE
        ):
            return {
                **_projection(
                    profile=RESTRICTED_PROFILE,
                    source="external_audience_boundary",
                    revision=str(effective["configuration_revision"]),
                ),
                "status": "external_audience_restricted",
                "configured_runtime_profile": TRUSTED_OWNER_PROFILE,
            }
        return effective
    except (OSError, TypeError, ValueError):
        return {
            **_projection(
                profile=RESTRICTED_PROFILE,
                source="invalid_configuration_fallback",
                revision="unavailable",
            ),
            "status": "configuration_invalid",
            "repair": "Open machine capability settings and repair Manager runtime.",
        }


def load_bound_manager_runtime_profile(
    runtime_root: Path,
    *,
    channel_id: str,
    bindings: ChatConversationBindings | None,
    steward_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compose fresh Core audience authority with the machine-owned host grant."""

    owner_manager_audience = False
    if steward_context is not None:
        if bindings is None:
            raise ValueError("bound manager authority is unavailable")
        owner_manager_audience = bindings.manager_runtime_owner(steward_context, channel_id)
    return load_effective_manager_runtime_profile(
        runtime_root, channel_id=channel_id,
        owner_manager_audience=owner_manager_audience,
    )


def manager_runtime_session_fields(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Project the effective profile fields persisted with a manager session."""

    return {
        "manager_runtime_profile": str(profile["runtime_profile"]),
        "manager_runtime_configuration_revision": str(
            profile["configuration_revision"]
        ),
        "manager_runtime_status": str(profile["status"]),
        "manager_runtime_sandbox": str(profile["sandbox"]),
        "manager_runtime_standing_grant": str(profile["standing_grant"]),
        "manager_runtime_tool_classes": list(profile["tool_classes"]),
    }


def manager_runtime_capability_projection(
    runtime_controller: object,
    model_configuration: Mapping[str, Any],
    *,
    channel_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the manager section of the shared chat capabilities projection.

    ``channel_binding`` is the resolved steward-channel executor and model
    binding. The caller owns it, including its credential facts; this projection
    only carries it into readback so a frontend can show which executor and model
    the manager channel resolved and why, without re-deriving the rule.
    """

    resolver = getattr(runtime_controller, "manager_runtime_profile", None)
    runtime = (
        resolver()
        if callable(resolver)
        else {**effective_manager_runtime_profile(None), "status": "ready"}
    )
    projection: dict[str, Any] = {
        "scope": "owner_global",
        **dict(model_configuration),
        "runtime": runtime,
    }
    if channel_binding is not None:
        projection["channel_binding"] = dict(channel_binding)
    return projection
