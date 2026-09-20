from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from ..configuration_transaction import configuration_payload_revision

CAPABILITY_CONFIGURATION_CATALOG_SCHEMA = "capability_configuration_catalog_v0"
CAPABILITY_CONFIGURATION_EDITOR_SCHEMA = "capability_configuration_editor_v0"
CAPABILITY_CONFIGURATION_RESOLUTION_SCHEMA = "capability_configuration_resolution_v0"


def _configuration_value(
    value: Mapping[str, Any] | None,
    *,
    label: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object or null")
    return deepcopy({str(key): item for key, item in value.items()})


def _steward_executor_editor_options() -> tuple[list[str], list[str]]:
    """Return the steward editor's option lists from their owning namespace."""

    from .steward_executor.machine_defaults import (
        steward_executor_endpoints,
        steward_reasoning_efforts,
    )

    return sorted(steward_executor_endpoints()), list(steward_reasoning_efforts())


def _field(
    key: str,
    label: str,
    input_kind: str,
    *,
    description: str = "",
    required: bool = False,
    nullable: bool = False,
    minimum: int | None = None,
    maximum: int | None = None,
    options: Sequence[str] = (),
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "key": key,
        "label": label,
        "description": description,
        "input_kind": input_kind,
        "required": required,
    }
    if minimum is not None:
        field["minimum"] = minimum
    if maximum is not None:
        field["maximum"] = maximum
    if options:
        field["options"] = list(options)
    if nullable:
        field["nullable"] = True
    return field


def capability_configuration_editor(
    capability_id: str,
    *,
    explore_harness_profiles: Sequence[str] = (),
) -> dict[str, Any]:
    """Return the provider-neutral editor contract consumed by every UI scope."""

    # The steward namespace owns its closed vocabulary, so the editor offers the
    # same values instead of restating them: a form cannot submit an executor or
    # effort the owning namespace would reject.
    steward_endpoints, steward_efforts = _steward_executor_editor_options()
    definitions: dict[str, dict[str, Any]] = {
        "todo_replan_cadence": {
            "supported_scopes": ["machine", "goal"],
            "writable_scopes": ["machine", "goal"],
            "fields": [
                _field(
                    "completed_todos",
                    "Completed Todos between Goal reviews",
                    "number",
                    minimum=1,
                    maximum=5,
                    required=True,
                    description=(
                        "Default 5 in both turn modes. Use 2 or 3 for earlier review; "
                        "the Goal editor writes an explicit override. Counts this "
                        "Agent's advancement work."
                    ),
                ),
            ],
        },
        "periodic_report": {
            "supported_scopes": ["machine", "goal"],
            "writable_scopes": ["machine", "goal"],
            "fields": [
                _field("enabled", "Enabled", "boolean"),
                _field(
                    "profile_preset",
                    "Profile preset",
                    "text",
                    description="Capability-owned report profile, such as weekly-progress.",
                ),
                _field(
                    "route_ref",
                    "Goal Channel route",
                    "text",
                    description="Public route alias only; credentials stay outside this form.",
                ),
                _field(
                    "timezone",
                    "Timezone",
                    "text",
                    description="IANA timezone, for example Asia/Shanghai.",
                    required=True,
                ),
                _field(
                    "schedule",
                    "Calendar reports",
                    "periodic_report_schedule",
                    nullable=True,
                    description="Optional daily or weekly schedule. Unset preserves stage-only reports.",
                ),
            ],
        },
        "manager_runtime": {
            "supported_scopes": ["machine"],
            "writable_scopes": ["machine"],
            "fields": [
                _field(
                    "runtime_profile",
                    "Runtime profile",
                    "select",
                    options=["restricted", "trusted_owner"],
                    required=True,
                    description=(
                        "Restricted uses only the scoped LoopX read model. Trusted owner "
                        "enables normal host tools under this persistent machine grant; "
                        "protected operations keep their own authority checks."
                    ),
                ),
            ],
        },
        "steward_executor": {
            "supported_scopes": ["machine"],
            "writable_scopes": ["machine"],
            "fields": [
                _field(
                    "executor_endpoint",
                    "Steward executor",
                    "select",
                    options=steward_endpoints,
                    required=True,
                    description=(
                        "The executor this machine's steward channel answers on: the "
                        "interactive CLI login, or the operator-billed managed host. "
                        "The choice outranks the Chat service environment."
                    ),
                ),
                _field(
                    "executor_model",
                    "Model",
                    "text",
                    nullable=True,
                    description=(
                        "Optional model for the selected executor. Leave blank to keep "
                        "the executor's own default."
                    ),
                ),
                _field(
                    "executor_reasoning_effort",
                    "Reasoning effort",
                    "select",
                    options=steward_efforts,
                    nullable=True,
                    description=(
                        "Optional reasoning effort for the selected executor. Leave "
                        "blank to keep the executor's own default."
                    ),
                ),
            ],
        },
        "multi_subagent": {
            "supported_scopes": ["goal"],
            "writable_scopes": ["goal"],
            "fields": [
                _field("enabled", "Enabled", "boolean"),
                _field(
                    "model",
                    "Child model",
                    "text",
                    description="For example gpt-5.6-luna. Blank clears the child model preference.",
                ),
                _field(
                    "reasoning_effort",
                    "Child reasoning effort",
                    "text",
                    description="For example max. Host support is checked at launch; requires a child model.",
                ),
                _field(
                    "max_children",
                    "Maximum children",
                    "number",
                    minimum=1,
                    maximum=32,
                ),
                _field(
                    "allowed_domains",
                    "Allowed responsibility domains",
                    "string_list",
                    description="One bounded, public-safe domain per line.",
                ),
                _field(
                    "execution_config",
                    "Delegation bindings",
                    "text",
                    nullable=True,
                    description=(
                        "Repo-relative ignored JSON under .loopx/config/. It exposes "
                        "only authorized, public-safe route status to planning; it "
                        "does not grant a binding."
                    ),
                ),
            ],
        },
        "peer_task_coordination": {
            "supported_scopes": ["goal"],
            "writable_scopes": ["goal"],
            "fields": [
                _field(
                    "coordinator_agent_id",
                    "Coordinator Agent",
                    "text",
                    description="An already registered Agent id; blank disables coordination.",
                )
            ],
        },
        "explore_graph": {
            "supported_scopes": ["goal"],
            "writable_scopes": ["goal"],
            "fields": [_field("enabled", "Enabled", "boolean")],
        },
        "explore_harness": {
            "supported_scopes": ["goal"],
            "writable_scopes": ["goal"],
            "fields": [
                _field("enabled", "Enabled", "boolean"),
                _field(
                    "profile",
                    "Planner profile",
                    "select",
                    options=explore_harness_profiles,
                ),
            ],
        },
        "change_quality_qualification": {
            "supported_scopes": ["machine", "goal"],
            "writable_scopes": ["machine", "goal"],
            "fields": [
                _field("enabled", "Enabled", "boolean"),
                _field("safe_fix", "Allow one bounded safe-fix pass", "boolean"),
                _field("strict_receipt", "Require an exact-diff receipt", "boolean"),
            ],
        },
        "pull_request_review": {
            "supported_scopes": ["machine", "goal"],
            "writable_scopes": ["machine", "goal"],
            "fields": [
                _field("wait_for_ci", "Wait for CI", "boolean", description="When disabled, use local validation without querying or waiting for CI. This grants no merge authority."),
                _field(
                    "review_priority",
                    "Review priority",
                    "select",
                    options=("other-developers-first", "owner-first"),
                    required=True,
                    description=(
                        "Default ranks actionable PRs whose author differs from the "
                        "authenticated reviewer before the reviewer's own PRs."
                    ),
                ),
            ],
        },
        "local_authority_shadow": {
            "supported_scopes": ["goal"],
            "writable_scopes": ["goal"],
            "fields": [_field("enabled", "Enabled", "boolean")],
        },
        "lark_kanban_heartbeat_sync": {
            "supported_scopes": ["goal"],
            "writable_scopes": ["goal"],
            "fields": [_field("enabled", "Enabled", "boolean")],
        },
        "reward_memory": {
            "supported_scopes": ["goal"],
            "writable_scopes": ["goal"],
            "fields": [
                _field(
                    "config_path",
                    "Local-private configuration path",
                    "text",
                    description=(
                        "Repo-relative ignored JSON under .loopx/config/. The path "
                        "is accepted only as write input and is never returned. Leave "
                        "blank to retain an existing binding."
                    ),
                ),
                _field(
                    "enabled_agents",
                    "Enabled Goal Agents",
                    "string_list",
                    description=(
                        "One already registered Goal-local Agent id per line. Private "
                        "bindings currently accept exactly one Agent."
                    ),
                ),
            ],
        },
        "lark_event_inbox": {
            "supported_scopes": ["goal"],
            "writable_scopes": [],
            "fields": [],
            "read_only_reason": (
                "This capability requires a local-private inbox binding. Manage the "
                "binding in Lark settings or through the capability CLI."
            ),
        },
    }
    definition = definitions.get(capability_id)
    if definition is None:
        return {
            "schema_version": CAPABILITY_CONFIGURATION_EDITOR_SCHEMA,
            "editable": False,
            "supported_scopes": [],
            "writable_scopes": [],
            "fields": [],
            "read_only_reason": "No Dashboard editor is registered for this capability.",
        }
    fields = deepcopy(definition["fields"])
    return {
        "schema_version": CAPABILITY_CONFIGURATION_EDITOR_SCHEMA,
        "editable": bool(fields),
        "supported_scopes": list(definition["supported_scopes"]),
        "writable_scopes": list(definition.get("writable_scopes") or []),
        "fields": fields,
        **(
            {"read_only_reason": definition["read_only_reason"]}
            if definition.get("read_only_reason")
            else {}
        ),
    }


def resolve_capability_configuration(
    capability_id: str,
    *,
    goal_override: Mapping[str, Any] | None = None,
    machine_default: Mapping[str, Any] | None = None,
    capability_default: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one already-normalized capability value without mutating a source.

    Capability owners remain responsible for schema normalization.  This shared
    kernel owns only precedence and provenance, so machine and Goal editors can
    use the same fail-closed rule without learning about each other's stores.
    A Goal override is deliberately atomic: when present, it wins as a complete
    value and is never field-merged with a machine default.
    """

    normalized_capability_id = str(capability_id or "").strip()
    if not normalized_capability_id:
        raise ValueError("capability_id is required")
    editor = capability_configuration_editor(normalized_capability_id)
    supported_scopes = set(editor["supported_scopes"])
    goal_value = _configuration_value(goal_override, label="goal_override")
    machine_value = _configuration_value(machine_default, label="machine_default")
    default_value = _configuration_value(
        capability_default,
        label="capability_default",
    )
    if goal_value is not None and "goal" not in supported_scopes:
        raise ValueError(
            f"capability does not support Goal configuration: {normalized_capability_id}"
        )
    if machine_value is not None and "machine" not in supported_scopes:
        raise ValueError(
            "capability does not support machine configuration: "
            + normalized_capability_id
        )

    if goal_value is not None:
        value, source = goal_value, "goal_override"
    elif machine_value is not None:
        value, source = machine_value, "machine_default"
    elif default_value is not None:
        value, source = default_value, "capability_default"
    else:
        value, source = None, "not_configured"
    resolution_identity = {
        "capability_id": normalized_capability_id,
        "source": source,
        "configuration": value,
    }
    return {
        "schema_version": CAPABILITY_CONFIGURATION_RESOLUTION_SCHEMA,
        **resolution_identity,
        "inherited": source == "machine_default",
        "goal_override_present": goal_value is not None,
        "machine_default_present": machine_value is not None,
        "effective_revision": configuration_payload_revision(resolution_identity),
    }


def _machine_catalog_entry(
    namespace: Mapping[str, Any],
    *,
    explore_harness_profiles: Sequence[str],
) -> tuple[str, dict[str, Any]]:
    capability_id = str(
        namespace.get("capability_id") or namespace.get("namespace") or ""
    ).strip()
    if not capability_id:
        raise ValueError("machine capability descriptor requires a namespace")
    entry: dict[str, Any] = {
        "capability_id": capability_id,
        "display_name": str(namespace.get("title") or capability_id),
        "description": str(namespace.get("description") or ""),
        "available_scopes": ["machine"],
        "machine_namespace": str(namespace.get("namespace") or capability_id),
        "configuration_editor": capability_configuration_editor(
            capability_id,
            explore_harness_profiles=explore_harness_profiles,
        ),
    }
    if isinstance(namespace.get("current"), Mapping):
        entry["machine_current"] = deepcopy(namespace["current"])
    if isinstance(namespace.get("configuration_template"), Mapping):
        entry["default"] = deepcopy(namespace["configuration_template"])
    if isinstance(namespace.get("documentation"), Mapping):
        entry["documentation"] = deepcopy(namespace["documentation"])
    return capability_id, entry


def _merge_goal_feature(
    entries: dict[str, dict[str, Any]],
    feature: Mapping[str, Any],
    *,
    explore_harness_profiles: Sequence[str],
) -> None:
    capability_id = str(feature.get("feature_id") or "").strip()
    if not capability_id:
        raise ValueError("Goal capability descriptor requires a feature_id")
    entry = entries.setdefault(
        capability_id,
        {
            "capability_id": capability_id,
            "display_name": str(feature.get("display_name") or capability_id),
            "description": str(
                feature.get("effect") or feature.get("consider_when") or ""
            ),
            "available_scopes": [],
        },
    )
    if "goal_feature_id" in entry:
        raise ValueError(f"duplicate Goal capability descriptor: {capability_id}")
    entry["available_scopes"] = [*entry["available_scopes"], "goal"]
    entry["goal_feature_id"] = capability_id
    entry["availability"] = feature.get("availability")
    for field in ("default", "current", "documentation", "context_contribution"):
        if isinstance(feature.get(field), Mapping):
            entry[field] = deepcopy(feature[field])
    entry["configuration_editor"] = capability_configuration_editor(
        capability_id,
        explore_harness_profiles=explore_harness_profiles,
    )
    if "machine" in entry["available_scopes"]:
        entry["effective_value_policy"] = "goal_override_over_live_machine_default"


def _attach_effective_configuration(entry: dict[str, Any]) -> None:
    available_scopes = entry["available_scopes"]
    goal_override = (
        entry.get("current")
        if "goal" in available_scopes and isinstance(entry.get("current"), Mapping)
        else None
    )
    machine_default = (
        entry.get("machine_current")
        if isinstance(entry.get("machine_current"), Mapping)
        else None
    )
    capability_default = (
        entry.get("default") if isinstance(entry.get("default"), Mapping) else None
    )
    entry["effective_configuration"] = resolve_capability_configuration(
        str(entry["capability_id"]),
        goal_override=goal_override,
        machine_default=machine_default,
        capability_default=capability_default,
    )


def build_capability_configuration_catalog(
    *,
    machine_namespaces: Sequence[Mapping[str, Any]] = (),
    goal_features: Sequence[Mapping[str, Any]] = (),
    explore_harness_profiles: Sequence[str] = (),
) -> dict[str, Any]:
    """Merge machine and Goal descriptors into one stable public catalog."""

    entries: dict[str, dict[str, Any]] = {}
    for namespace in machine_namespaces:
        capability_id, entry = _machine_catalog_entry(
            namespace,
            explore_harness_profiles=explore_harness_profiles,
        )
        if capability_id in entries:
            raise ValueError(
                f"duplicate machine capability descriptor: {capability_id}"
            )
        entries[capability_id] = entry
    for feature in goal_features:
        _merge_goal_feature(
            entries,
            feature,
            explore_harness_profiles=explore_harness_profiles,
        )
    for entry in entries.values():
        _attach_effective_configuration(entry)
    return {
        "schema_version": CAPABILITY_CONFIGURATION_CATALOG_SCHEMA,
        "capabilities": [entries[key] for key in sorted(entries)],
    }


__all__ = [
    "CAPABILITY_CONFIGURATION_CATALOG_SCHEMA",
    "CAPABILITY_CONFIGURATION_EDITOR_SCHEMA",
    "CAPABILITY_CONFIGURATION_RESOLUTION_SCHEMA",
    "build_capability_configuration_catalog",
    "capability_configuration_editor",
    "resolve_capability_configuration",
]
