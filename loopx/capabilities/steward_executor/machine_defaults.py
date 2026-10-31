"""Own the machine-level executor default for the steward channel.

The steward channel is the surface a person talks to. It resolves which executor
answers, which model runs there, and at which reasoning effort from three typed
sources, in one order:

1. this machine-configuration namespace -- the operator's persistent choice for
   this machine, editable from the Dashboard and read back by
   ``loopx machine-config describe`` / ``inspect``;
2. the Chat service environment (``LOOPX_MANAGER_ENDPOINT`` and its model and
   effort siblings), which stays as the bootstrap and escape-hatch layer;
3. the shipped product default, which is the interactive CLI endpoint.

The namespace stores a decision, never a discovery: it holds no credential, and
nothing here reads one. A configured operator credential authenticates the
selected executor; it never selects one.

Only the endpoints LoopX ships as channel executors are accepted here, so a
machine default cannot name an adapter the channel has no product contract for.
An operator who needs an unlisted adapter still has the environment variable.
"""

from __future__ import annotations

from collections.abc import Mapping
import importlib
from pathlib import Path
from typing import Any

from ...control_plane.turn_driver.host_binding import (
    EXECUTOR_KIND_MANAGED,
    MANAGED_TURN_HOST,
)
from ...reasoning_effort import REASONING_EFFORTS
from ..machine_configuration.contract import (
    MACHINE_CONFIGURATION_SCHEMA,
    MachineConfigurationNamespace,
    machine_configuration_revision,
)

# The endpoint an operator selects to run the steward on the managed executor,
# and the endpoint the channel runs when nothing is selected. Neither name is
# "default" beyond that: the shipped default is stated once, in the channel
# resolution in ``loopx.chat_manager``, so a reader cannot mistake the pair for
# two competing defaults. The vocabulary lives here, beside the namespace that
# validates it, so reading machine configuration never loads the chat layer;
# ``loopx.chat_manager`` re-exports every name unchanged.
MANAGER_ENDPOINT_MANAGED = MANAGED_TURN_HOST
MANAGER_ENDPOINT_INDIVIDUAL = "codex"
# Executor kinds name where this channel's model work is billed and bounded
# rather than which adapter is launched, and they use the same vocabulary as the
# governed Turn surface: an individual executor runs on one person's own CLI
# login, a managed executor on an operator-supplied credential. Which kind an
# endpoint is decides whether an operator credential belongs to it at all; the
# mere presence of a credential decides nothing.
MANAGER_EXECUTOR_KIND_INDIVIDUAL = "individual"
MANAGER_EXECUTOR_KIND_MANAGED = EXECUTOR_KIND_MANAGED
MANAGER_ENDPOINT_KINDS = {
    MANAGER_ENDPOINT_INDIVIDUAL: MANAGER_EXECUTOR_KIND_INDIVIDUAL,
    # The managed host is billed to the operator's own endpoint, not to one
    # person's CLI login, so it is reached only by selecting it.
    MANAGER_ENDPOINT_MANAGED: MANAGER_EXECUTOR_KIND_MANAGED,
}

STEWARD_EXECUTOR_NAMESPACE = "steward_executor"
STEWARD_EXECUTOR_SCHEMA_V0 = "steward_executor_machine_defaults_v0"
STEWARD_EXECUTOR_SCHEMA = "steward_executor_machine_defaults_v1"
STEWARD_EXECUTOR_EFFECTIVE_SCHEMA = "steward_executor_effective_defaults_v0"
EXECUTOR_ENDPOINT_FIELD = "executor_endpoint"
MODEL_FIELD = "executor_model"
REASONING_EFFORT_FIELD = "executor_reasoning_effort"
SELECTION_POLICY_FIELD = "selection_policy"
ELIGIBLE_ENDPOINTS_FIELD = "eligible_endpoints"
PREFERRED_SELECTION_POLICY = "preferred"
PINNED_SELECTION_POLICY = "pinned"
FLEXIBLE_SELECTION_POLICY = "flexible"
STEWARD_SELECTION_POLICIES = (
    PREFERRED_SELECTION_POLICY,
    PINNED_SELECTION_POLICY,
    FLEXIBLE_SELECTION_POLICY,
)
_TEXT_LIMIT = 100


def steward_executor_endpoints() -> frozenset[str]:
    """Return the executor ids this namespace accepts for the steward channel.

    The channel resolution and this namespace read one endpoint vocabulary,
    defined above, instead of restating a second list here.
    """

    return frozenset(MANAGER_ENDPOINT_KINDS)


def steward_reasoning_efforts() -> tuple[str, ...]:
    """Return the reasoning-effort vocabulary a selected executor accepts."""

    return REASONING_EFFORTS


def _optional_text(value: Any, *, field: str) -> str | None:
    """Normalize one absent-or-selected text field.

    A blank value and an absent value mean the same thing -- this namespace
    does not decide that field, and the channel keeps resolving it from the
    lower layers -- so both normalize to ``None`` instead of storing an empty
    string that reads like a selection.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > _TEXT_LIMIT:
        raise ValueError(f"steward_executor.{field} is too long")
    return text


def normalize_steward_executor_machine_defaults(
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize one typed steward-executor machine default, fail closed."""

    schema_version = str(raw.get("schema_version") or "").strip()
    if schema_version not in {STEWARD_EXECUTOR_SCHEMA_V0, STEWARD_EXECUTOR_SCHEMA}:
        raise ValueError(
            "steward_executor must use "
            f"{STEWARD_EXECUTOR_SCHEMA_V0} or {STEWARD_EXECUTOR_SCHEMA}"
        )
    v1_fields = {SELECTION_POLICY_FIELD, ELIGIBLE_ENDPOINTS_FIELD}
    unknown = sorted(
        set(raw)
        - {
            "schema_version",
            EXECUTOR_ENDPOINT_FIELD,
            MODEL_FIELD,
            REASONING_EFFORT_FIELD,
            *(v1_fields if schema_version == STEWARD_EXECUTOR_SCHEMA else set()),
        }
    )
    if unknown:
        raise ValueError(
            "steward_executor contains unsupported fields: " + ", ".join(unknown)
        )
    endpoint = str(raw.get(EXECUTOR_ENDPOINT_FIELD) or "").strip()
    supported = steward_executor_endpoints()
    if endpoint not in supported:
        raise ValueError(
            "steward_executor.executor_endpoint must be one of "
            + ", ".join(sorted(supported))
        )
    reasoning_effort = _optional_text(
        raw.get(REASONING_EFFORT_FIELD), field=REASONING_EFFORT_FIELD
    )
    supported_efforts = steward_reasoning_efforts()
    if reasoning_effort is not None and reasoning_effort not in supported_efforts:
        raise ValueError(
            "steward_executor.reasoning_effort must be one of "
            + ", ".join(supported_efforts)
        )
    normalized = {
        "schema_version": schema_version,
        EXECUTOR_ENDPOINT_FIELD: endpoint,
        MODEL_FIELD: _optional_text(raw.get(MODEL_FIELD), field=MODEL_FIELD),
        REASONING_EFFORT_FIELD: reasoning_effort,
    }
    if schema_version == STEWARD_EXECUTOR_SCHEMA_V0:
        return normalized

    selection_policy = str(raw.get(SELECTION_POLICY_FIELD) or "").strip()
    if selection_policy not in STEWARD_SELECTION_POLICIES:
        raise ValueError(
            "steward_executor.selection_policy must be preferred, pinned, or flexible"
        )
    eligible_raw = raw.get(ELIGIBLE_ENDPOINTS_FIELD)
    if not isinstance(eligible_raw, list):
        raise TypeError("steward_executor.eligible_endpoints must be a list")
    eligible_endpoints = [str(item).strip() for item in eligible_raw]
    if any(not item for item in eligible_endpoints):
        raise ValueError("steward_executor.eligible_endpoints must not contain blanks")
    if len(set(eligible_endpoints)) != len(eligible_endpoints):
        raise ValueError("steward_executor.eligible_endpoints must not contain duplicates")
    unsupported = sorted(set(eligible_endpoints) - supported)
    if unsupported:
        raise ValueError(
            "steward_executor.eligible_endpoints contains unsupported endpoints: "
            + ", ".join(unsupported)
        )
    if selection_policy == FLEXIBLE_SELECTION_POLICY:
        if not eligible_endpoints:
            raise ValueError(
                "steward_executor.eligible_endpoints must not be empty for flexible selection"
            )
        if endpoint not in eligible_endpoints:
            raise ValueError(
                "steward_executor.executor_endpoint must belong to eligible_endpoints "
                "for flexible selection"
            )
    elif eligible_endpoints:
        raise ValueError(
            "steward_executor.eligible_endpoints is only valid for flexible selection"
        )
    return {
        **normalized,
        SELECTION_POLICY_FIELD: selection_policy,
        ELIGIBLE_ENDPOINTS_FIELD: eligible_endpoints,
    }


def steward_executor_machine_configuration_namespace() -> (
    MachineConfigurationNamespace
):
    return MachineConfigurationNamespace(
        namespace=STEWARD_EXECUTOR_NAMESPACE,
        schema_versions=frozenset(
            {STEWARD_EXECUTOR_SCHEMA_V0, STEWARD_EXECUTOR_SCHEMA}
        ),
        normalize=normalize_steward_executor_machine_defaults,
        project_public=lambda value: dict(value),
        apply_public_update=lambda _current, update: dict(update),
        title="Steward executor",
        description=(
            "Selects the executor, model, reasoning effort, and selection policy "
            "the steward channel runs on this machine, ahead of the Chat service "
            "environment and the shipped default. Preferred permits an explicit "
            "user override, pinned rejects a different route, and flexible limits "
            "automatic fallback to an authorized pool. It selects a provider-billed "
            "runtime; it grants no authority and stores no credential."
        ),
        documentation={
            "path": "docs/architecture/rfcs/harness-selection-dsh-pi-v0.md",
            "url": (
                "https://github.com/loopx-project/loopx/blob/main/"
                "docs/architecture/rfcs/harness-selection-dsh-pi-v0.md"
            ),
        },
        default_configuration={
            "schema_version": STEWARD_EXECUTOR_SCHEMA,
            SELECTION_POLICY_FIELD: PREFERRED_SELECTION_POLICY,
            EXECUTOR_ENDPOINT_FIELD: "codex",
            ELIGIBLE_ENDPOINTS_FIELD: [],
            MODEL_FIELD: None,
            REASONING_EFFORT_FIELD: None,
        },
    )


def _projection(
    *,
    status: str,
    source: str,
    revision: str,
    executor_endpoint: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    selection_policy: str = PREFERRED_SELECTION_POLICY,
    eligible_endpoints: list[str] | None = None,
    repair: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": STEWARD_EXECUTOR_EFFECTIVE_SCHEMA,
        "status": status,
        "source": source,
        "configuration_revision": revision,
        EXECUTOR_ENDPOINT_FIELD: executor_endpoint,
        MODEL_FIELD: model,
        REASONING_EFFORT_FIELD: reasoning_effort,
        SELECTION_POLICY_FIELD: selection_policy,
        ELIGIBLE_ENDPOINTS_FIELD: list(eligible_endpoints or []),
        **({"repair": repair} if repair else {}),
    }


def effective_steward_executor_defaults(
    machine_configuration: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve the steward executor defaults from one machine document.

    ``None``, a document that carries no steward namespace, and a malformed
    document all resolve to "this machine decided nothing" rather than to a
    guessed endpoint, so the channel falls through to its lower layers with the
    reason visible in ``status``.
    """

    if machine_configuration is None:
        return _projection(
            status="absent", source="capability_default", revision="absent"
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
    raw = namespaces.get(STEWARD_EXECUTOR_NAMESPACE)
    if raw is None:
        return _projection(
            status="absent", source="capability_default", revision="absent"
        )
    if not isinstance(raw, Mapping):
        raise TypeError(
            "machine_configuration.namespaces.steward_executor must be an object"
        )
    normalized = normalize_steward_executor_machine_defaults(raw)
    return _projection(
        status="ready",
        source="machine_configuration",
        revision=machine_configuration_revision(normalized),
        executor_endpoint=str(normalized[EXECUTOR_ENDPOINT_FIELD]),
        model=normalized[MODEL_FIELD],
        reasoning_effort=normalized[REASONING_EFFORT_FIELD],
        selection_policy=str(
            normalized.get(SELECTION_POLICY_FIELD) or PREFERRED_SELECTION_POLICY
        ),
        eligible_endpoints=list(normalized.get(ELIGIBLE_ENDPOINTS_FIELD) or []),
    )


def load_effective_steward_executor_defaults(runtime_root: Path) -> dict[str, Any]:
    """Read this machine's steward executor default from the live store.

    Each namespace owns its runtime effect, so a malformed sibling cannot
    silently rewrite a valid steward selection: the document is read
    unnormalized and only this namespace is normalized here. A malformed
    steward value, or an unreadable store, resolves to the shipped default with
    a typed reason instead of failing the channel a person is talking to.
    """

    store = importlib.import_module("loopx.capabilities.machine_configuration.store")

    try:
        configuration = store.read_stored_machine_configuration(runtime_root)
    except (OSError, TypeError, ValueError):
        return _projection(
            status="unavailable",
            source="unreadable_store",
            revision="unavailable",
            repair="Repair the machine-configuration store, then reopen the channel.",
        )
    try:
        return effective_steward_executor_defaults(configuration)
    except (TypeError, ValueError):
        return _projection(
            status="configuration_invalid",
            source="invalid_configuration_fallback",
            revision="unavailable",
            repair="Open machine capability settings and repair Steward executor.",
        )
