from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from .capabilities.configuration_ui import (
    build_capability_configuration_catalog,
)
from .capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from .capabilities.multi_subagent import (
    apply_codex_subagent_capacity,
    plan_codex_subagent_capacity,
    public_codex_host_capacity,
)
from .capabilities.machine_configuration.store import inspect_machine_configuration
from .configuration_transaction import (
    build_configuration_update_plan,
    configuration_payload_revision,
    goal_capability_configuration_revision,
    require_expected_configuration_plan_revision,
)
from .control_plane.goals.configure_goal_service import (
    configure_goal_with_global_sync,
    read_goal_configuration_with_source_route,
)
from .control_plane.goals.goal_vision_policy import (
    normalize_completed_todo_replan_threshold,
)
from .orchestration import subagent_model_configuration_options

CHAT_GOAL_CONFIGURATION_PATH = "/api/chat/goal-configuration"
CHAT_GOAL_CONFIGURATION_PREVIEW_PATH = f"{CHAT_GOAL_CONFIGURATION_PATH}/preview"
CHAT_GOAL_CONFIGURATION_APPLY_PATH = f"{CHAT_GOAL_CONFIGURATION_PATH}/apply"
GOAL_CONFIGURATION_INSPECTION_SCHEMA = "goal_configuration_inspection_v0"
GoalConfigurationReader = Callable[..., dict[str, Any]]
GoalConfigurationWriter = Callable[..., dict[str, Any]]


def _boolean_configuration(
    capability_id: str,
    configuration: Mapping[str, Any],
    key: str,
    *,
    default: bool | None = None,
) -> bool:
    value = configuration.get(key, default)
    if not isinstance(value, bool):
        raise TypeError(f"{capability_id}.{key} must be a boolean")
    return value


def _subagent_model_options(config: Mapping[str, Any]) -> dict[str, Any]:
    if "model" not in config and "reasoning_effort" not in config:
        return {}
    model = config.get("model", "")
    effort = config.get("reasoning_effort", "")
    if not isinstance(model, str) or not isinstance(effort, str):
        raise ValueError("child model and reasoning effort must be strings")
    if not model:
        if effort:
            raise ValueError("child reasoning effort requires a model")
        return subagent_model_configuration_options(None)
    preference = {"model": model}
    if effort:
        preference["reasoning_effort"] = effort
    return subagent_model_configuration_options(preference)


def _multi_subagent_options(config: Mapping[str, Any]) -> dict[str, Any]:
    model_options = _subagent_model_options(config)
    execution_options: dict[str, Any] = {}
    if "execution_config" in config:
        execution_config = config.get("execution_config")
        if execution_config is not None and not isinstance(execution_config, str):
            raise TypeError("multi_subagent.execution_config must be a string")
        execution_config = str(execution_config or "").strip()
        execution_options = (
            {"subagent_execution_config": execution_config}
            if execution_config
            else {"clear_subagent_execution_config": True}
        )
    if not _boolean_configuration("multi_subagent", config, "enabled"):
        return {
            "multi_subagent_feature": "off",
            **model_options,
            **execution_options,
        }
    max_children = config.get("max_children", 4)
    if not isinstance(max_children, int) or isinstance(max_children, bool):
        raise TypeError("multi_subagent.max_children must be an integer")
    domains = config.get("allowed_domains", [])
    if not isinstance(domains, list) or any(
        not isinstance(item, str) for item in domains
    ):
        raise TypeError("multi_subagent.allowed_domains must be a string list")
    return {
        "multi_subagent_feature": "enabled",
        "max_children": max_children,
        "allowed_domains": domains,
        "align_codex_subagent_capacity": True,
        **model_options,
        **execution_options,
    }


def _peer_task_coordination_options(config: Mapping[str, Any]) -> dict[str, Any]:
    coordinator = str(config.get("coordinator_agent_id") or "").strip()
    if coordinator:
        return {"peer_task_coordinator": coordinator}
    return {"clear_peer_task_coordinator": True}


def _explore_harness_options(config: Mapping[str, Any]) -> dict[str, Any]:
    profile = str(config.get("profile") or "").strip() or None
    return {
        "explore_harness_enabled": _boolean_configuration(
            "explore_harness", config, "enabled"
        ),
        "explore_harness_profile": profile,
        "clear_explore_harness_profile": profile is None,
    }


def _change_quality_options(config: Mapping[str, Any]) -> dict[str, Any]:
    capability_id = "change_quality_qualification"
    return {
        "change_quality_enabled": _boolean_configuration(
            capability_id, config, "enabled"
        ),
        "change_quality_safe_fix": _boolean_configuration(
            capability_id, config, "safe_fix", default=False
        ),
        "change_quality_strict_receipt": _boolean_configuration(
            capability_id, config, "strict_receipt", default=False
        ),
    }


def _local_authority_shadow_options(config: Mapping[str, Any]) -> dict[str, Any]:
    if _boolean_configuration("local_authority_shadow", config, "enabled"):
        return {"local_authority_shadow_file": True}
    return {"clear_local_authority_shadow": True}


def _goal_capability_options(
    capability_id: str,
    configuration: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Translate one validated UI contract into configure-goal options."""

    if configuration is None:
        if capability_id == "periodic_report":
            return {"clear_periodic_report_configuration": True}
        if capability_id == "todo_replan_cadence":
            return {"clear_execution_replan_after_todos": True}
        if capability_id == "pull_request_review":
            return {"clear_pull_request_review_configuration": True}
        if capability_id == "change_quality_qualification":
            return {"clear_change_quality_configuration": True}
        if capability_id == "reward_memory":
            return {"clear_reward_memory_config": True}
        raise ValueError(f"Goal capability cannot be cleared: {capability_id}")
    config = dict(configuration)
    allowed: dict[str, set[str]] = {
        "todo_replan_cadence": {"completed_todos"},
        "multi_subagent": {
            "enabled",
            "max_children",
            "allowed_domains",
            "model",
            "reasoning_effort",
            "execution_config",
        },
        "peer_task_coordination": {"coordinator_agent_id"},
        "explore_graph": {"enabled"},
        "explore_harness": {"enabled", "profile"},
        "pull_request_review": {"wait_for_ci", "review_priority"},
        "change_quality_qualification": {"enabled", "safe_fix", "strict_receipt"},
        "local_authority_shadow": {"enabled"},
        "lark_kanban_heartbeat_sync": {"enabled"},
        "periodic_report": {"enabled", "profile_preset", "route_ref", "timezone", "schedule"},
        "reward_memory": {"config_path", "enabled_agents"},
    }
    if capability_id not in allowed:
        raise ValueError(f"Goal capability is read-only in Dashboard: {capability_id}")
    unknown = sorted(set(config) - allowed[capability_id])
    if unknown:
        raise ValueError(
            f"unknown {capability_id} configuration field(s): {', '.join(unknown)}"
        )

    if capability_id == "multi_subagent":
        return _multi_subagent_options(config)
    if capability_id == "todo_replan_cadence":
        return {
            "execution_replan_after_todos": normalize_completed_todo_replan_threshold(
                config.get("completed_todos")
            ),
        }
    if capability_id == "periodic_report":
        return {"periodic_report_configuration": config}
    if capability_id == "reward_memory":
        config_path = config.get("config_path")
        if config_path is not None and not isinstance(config_path, str):
            raise TypeError("reward_memory.config_path must be a string")
        enabled_agents = config.get("enabled_agents")
        if enabled_agents is not None and (
            not isinstance(enabled_agents, list)
            or any(not isinstance(value, str) for value in enabled_agents)
        ):
            raise TypeError("reward_memory.enabled_agents must be a string list")
        return {
            "reward_memory_config": str(config_path or "").strip() or None,
            "reward_memory_agents": enabled_agents,
        }
    if capability_id == "peer_task_coordination":
        return _peer_task_coordination_options(config)
    if capability_id == "explore_graph":
        return {
            "explore_graph_enabled": _boolean_configuration(
                capability_id, config, "enabled"
            )
        }
    if capability_id == "explore_harness":
        return _explore_harness_options(config)
    if capability_id == "pull_request_review":
        from .capabilities.pr_review_queue.goal_configuration import normalize_configuration
        return {"pull_request_review_configuration": normalize_configuration(config)}
    if capability_id == "change_quality_qualification":
        return _change_quality_options(config)
    if capability_id == "local_authority_shadow":
        return _local_authority_shadow_options(config)
    return {
        "lark_kanban_heartbeat_sync": _boolean_configuration(
            capability_id, config, "enabled"
        )
    }


class _GoalConfigurationServer(Protocol):
    registry_path: Path
    runtime_root: Path


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is unavailable")
    return {str(key): item for key, item in value.items()}


def _goal_features_with_machine_context(
    payload: Mapping[str, Any],
    catalog: Mapping[str, Any],
    machine_namespaces: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    features = catalog.get("features")
    if not isinstance(features, list) or any(
        not isinstance(item, Mapping) for item in features
    ):
        raise ValueError("Goal feature catalog is invalid")
    goal_features = [dict(item) for item in features]
    machine_has_periodic_report = any(
        str(item.get("namespace") or "") == "periodic_report"
        for item in machine_namespaces
    )
    goal_has_periodic_report = any(
        str(item.get("feature_id") or "") == "periodic_report" for item in goal_features
    )
    if machine_has_periodic_report and not goal_has_periodic_report:
        after = payload.get("after")
        control_plane = (
            after.get("control_plane")
            if isinstance(after, Mapping)
            and isinstance(after.get("control_plane"), Mapping)
            else {}
        )
        current = control_plane.get("periodic_report")
        periodic_report = {
            "feature_id": "periodic_report",
            "display_name": "Periodic report",
            "availability": "supported_explicit_override",
            "default": {"enabled": False, "timezone": "UTC"},
            "effect": "Use a complete Goal-specific report route instead of the live machine default.",
        }
        if isinstance(current, Mapping):
            periodic_report["current"] = dict(current)
        goal_features.append(periodic_report)
    return goal_features


def _validated_capability_ids(capability_catalog: Mapping[str, Any]) -> list[str]:
    capabilities = capability_catalog.get("capabilities")
    if not isinstance(capabilities, list) or any(
        not isinstance(item, Mapping) for item in capabilities
    ):
        raise ValueError("capability catalog is invalid")
    capability_ids = [
        str(item.get("capability_id") or "").strip() for item in capabilities
    ]
    if not all(capability_ids) or len(set(capability_ids)) != len(capability_ids):
        raise ValueError("capability catalog contains an invalid identity")
    return capability_ids


def _parse_goal_configuration_update(
    body: Mapping[str, Any], *, execute: bool
) -> tuple[str, str, Mapping[str, Any] | None, str]:
    allowed = {"goal_id", "capability_id", "configuration"}
    if execute:
        allowed.add("expected_plan_revision")
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise ValueError(
            "goal configuration request contains unknown fields: " + ", ".join(unknown)
        )
    goal_id = str(body.get("goal_id") or "").strip()
    capability_id = str(body.get("capability_id") or "").strip()
    if "configuration" not in body:
        raise ValueError("configuration is required")
    configuration = body.get("configuration")
    if not goal_id or not capability_id:
        raise ValueError("goal_id and capability_id are required")
    if configuration is not None and not isinstance(configuration, Mapping):
        raise TypeError("configuration must be an object or null")
    expected_revision = str(body.get("expected_plan_revision") or "")
    return goal_id, capability_id, configuration, expected_revision


def _configuration_revision(value: object) -> str:
    if isinstance(value, Mapping):
        return configuration_payload_revision(value)
    return "absent"


def _goal_configuration_update_plan(
    *,
    goal_id: str,
    capability_id: str,
    current_public: Mapping[str, Any],
    desired_public: Mapping[str, Any],
    desired_result: Mapping[str, Any],
) -> tuple[dict[str, Any], object]:
    current_configuration = _capability_entry(current_public, capability_id).get(
        "current"
    )
    desired_configuration = _capability_entry(desired_public, capability_id).get(
        "current"
    )
    host_capacity = (
        public_codex_host_capacity(dict(desired_result))
        if capability_id == "multi_subagent"
        else {}
    )
    plan = build_configuration_update_plan(
        schema_version="goal_configuration_update_plan_v0",
        current_present=isinstance(current_configuration, Mapping),
        desired_present=isinstance(desired_configuration, Mapping),
        current_revision=_configuration_revision(current_configuration),
        desired_revision=_configuration_revision(desired_configuration),
        target_identity={
            "goal_id": goal_id,
            "capability_id": capability_id,
            "base_revision": current_public["revision"],
        },
        changed_units={
            "changed_fields": list(desired_result.get("changed_fields") or []),
            **({"codex_host_capacity": host_capacity} if host_capacity else {}),
        },
        projected_configuration=(
            dict(desired_configuration)
            if isinstance(desired_configuration, Mapping)
            else None
        ),
        projection_field="goal_configuration",
        additional_write_required=bool(host_capacity.get("write_required")),
    )
    return plan, desired_configuration


def _public_goal_configuration(
    payload: Mapping[str, Any],
    *,
    machine_namespaces: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    goal_id = str(payload.get("goal_id") or "").strip()
    if not goal_id:
        raise ValueError("Goal configuration result is missing goal_id")
    catalog = _mapping(payload.get("configuration_catalog"), "configuration catalog")
    capability_catalog = _mapping(
        catalog.get("capability_catalog"), "capability catalog"
    )
    if machine_namespaces is not None:
        goal_features = _goal_features_with_machine_context(
            payload, catalog, machine_namespaces
        )
        capability_catalog = build_capability_configuration_catalog(
            machine_namespaces=machine_namespaces,
            goal_features=goal_features,
        )
    capability_ids = _validated_capability_ids(capability_catalog)
    return {
        "ok": True,
        "schema_version": GOAL_CONFIGURATION_INSPECTION_SCHEMA,
        "status": "configured",
        "goal_id": goal_id,
        "revision": goal_capability_configuration_revision(
            goal_id,
            capability_catalog,
        ),
        "available_capabilities": capability_ids,
        "capability_catalog": capability_catalog,
    }


class GoalConfigurationRequestMixin:
    server: _GoalConfigurationServer
    path: str

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError

    def _send_error(
        self,
        message: str,
        *,
        status: int,
        error_code: str,
        **kwargs: Any,
    ) -> None:
        raise NotImplementedError

    def _goal_configuration_reader(self) -> GoalConfigurationReader:
        return read_goal_configuration_with_source_route

    def _goal_configuration_writer(self) -> GoalConfigurationWriter:
        return configure_goal_with_global_sync

    def _goal_configuration_machine_namespaces(self) -> list[Mapping[str, Any]] | None:
        runtime_root = getattr(self.server, "runtime_root", None)
        if not isinstance(runtime_root, Path):
            return None
        registry = build_builtin_machine_configuration_registry()
        inspection = inspect_machine_configuration(runtime_root, registry=registry)
        current_namespaces = (
            inspection.get("machine_configuration", {}).get("namespaces", {})
            if isinstance(inspection.get("machine_configuration"), Mapping)
            else {}
        )
        return [
            {
                **descriptor,
                **(
                    {"current": current_namespaces[descriptor["namespace"]]}
                    if descriptor["namespace"] in current_namespaces
                    else {}
                ),
            }
            for descriptor in registry.public_catalog()["namespaces"]
        ]

    def _goal_configuration_inspect(self) -> None:
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(query) - {"goal_id"}:
                raise ValueError("goal configuration request contains unknown fields")
            goal_ids = query.get("goal_id") or []
            if len(goal_ids) != 1 or not goal_ids[0].strip():
                raise ValueError("goal_id is required exactly once")
            result = self._goal_configuration_reader()(
                registry_path=self.server.registry_path,
                goal_id=goal_ids[0].strip(),
                execute=False,
            )
            response = _public_goal_configuration(
                result,
                machine_namespaces=self._goal_configuration_machine_namespaces(),
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            self._send_error(
                str(exc),
                status=400,
                error_code="invalid_goal_configuration_request",
            )
            return
        except Exception:  # noqa: BLE001 - sanitize browser-facing failures.
            self._send_error(
                "Goal configuration could not be inspected.",
                status=500,
                error_code="goal_configuration_inspection_failed",
            )
            return
        self._send_json(response)

    def _read_public_goal_configuration(
        self,
        goal_id: str,
        machine_namespaces: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        result = self._goal_configuration_reader()(
            registry_path=self.server.registry_path,
            goal_id=goal_id,
            execute=False,
        )
        return _public_goal_configuration(
            result,
            machine_namespaces=(
                self._goal_configuration_machine_namespaces()
                if machine_namespaces is None
                else machine_namespaces
            ),
        )

    def _prepare_goal_configuration_plan(
        self,
        *,
        goal_id: str,
        capability_id: str,
        machine_namespaces: list[Mapping[str, Any]] | None,
        options: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], object]:
        current_public = self._read_public_goal_configuration(
            goal_id, machine_namespaces
        )
        desired_result = self._goal_configuration_writer()(
            registry_path=self.server.registry_path,
            goal_id=goal_id,
            runtime_root_override=getattr(self.server, "runtime_root_override", None),
            execute=False,
            codex_home_override=getattr(
                getattr(self.server, "runtime_controller", None),
                "codex_home",
                None,
            ),
            codex_host_capacity_planner=plan_codex_subagent_capacity,
            codex_host_capacity_applier=apply_codex_subagent_capacity,
            **options,
        )
        desired_public = _public_goal_configuration(
            desired_result,
            machine_namespaces=machine_namespaces,
        )
        plan, desired_configuration = _goal_configuration_update_plan(
            goal_id=goal_id,
            capability_id=capability_id,
            current_public=current_public,
            desired_public=desired_public,
            desired_result=desired_result,
        )
        return current_public, desired_public, plan, desired_configuration

    def _send_partial_write_receipt(
        self,
        *,
        applied: Mapping[str, Any],
        capability_id: str,
        desired_configuration: object,
        desired_public: Mapping[str, Any],
        goal_id: str,
        plan: Mapping[str, Any],
    ) -> None:
        try:
            readback_public = self._read_public_goal_configuration(goal_id)
            readback_configuration = _capability_entry(
                readback_public, capability_id
            ).get("current")
            readback_verified = readback_configuration == desired_configuration
        except Exception:  # noqa: BLE001 - retain the partial-write fact.
            readback_public = dict(desired_public)
            readback_configuration = None
            readback_verified = False
        global_sync = applied.get("global_sync")
        global_readback = (
            global_sync.get("readback")
            if isinstance(global_sync, Mapping)
            and isinstance(global_sync.get("readback"), Mapping)
            else {}
        )
        host_capacity = public_codex_host_capacity(dict(applied))
        shared_sync_pending = not bool(global_readback.get("verified"))
        host_capacity_pending = host_capacity.get("status") == "apply_failed"
        self._send_json(
            {
                "ok": False,
                "schema_version": "goal_configuration_transaction_v0",
                "status": "partial_write",
                "goal_id": goal_id,
                "capability_id": capability_id,
                "plan_revision": plan["plan_revision"],
                "applied_revision": (
                    readback_public["revision"] if readback_verified else None
                ),
                "source_written": True,
                "shared_sync_pending": shared_sync_pending,
                "host_capacity_pending": host_capacity_pending,
                "readback_verified": readback_verified,
                "changed_fields": list(applied.get("changed_fields") or []),
                "goal_configuration": readback_configuration,
                "capability_catalog": readback_public["capability_catalog"],
                "codex_host_capacity": host_capacity,
                "error": str(
                    applied.get("error")
                    or "Goal configuration shared projection did not synchronize"
                ),
                "recommended_action": str(
                    applied.get("recommended_action")
                    or f"rerun loopx sync-global --goal-id {goal_id}"
                ),
            },
            status=207,
        )

    def _send_applied_goal_configuration(
        self,
        *,
        applied: Mapping[str, Any],
        capability_id: str,
        desired_configuration: object,
        goal_id: str,
        plan: Mapping[str, Any],
    ) -> None:
        readback_public = self._read_public_goal_configuration(goal_id)
        readback_configuration = _capability_entry(readback_public, capability_id).get(
            "current"
        )
        if readback_configuration != desired_configuration:
            raise RuntimeError("Goal configuration readback did not verify")
        self._send_json(
            {
                "ok": True,
                "schema_version": "goal_configuration_transaction_v0",
                "status": "applied" if plan["writes_required"] else "unchanged",
                "goal_id": goal_id,
                "capability_id": capability_id,
                "plan_revision": plan["plan_revision"],
                "applied_revision": readback_public["revision"],
                "readback_verified": True,
                "changed_fields": list(applied.get("changed_fields") or []),
                "goal_configuration": readback_configuration,
                "capability_catalog": readback_public["capability_catalog"],
                "codex_host_capacity": public_codex_host_capacity(dict(applied)),
            }
        )

    def _goal_configuration_update(self, *, execute: bool) -> None:
        try:
            body = self._read_json()
            goal_id, capability_id, configuration, expected_revision = (
                _parse_goal_configuration_update(body, execute=execute)
            )
            options = _goal_capability_options(capability_id, configuration)
            machine_namespaces = self._goal_configuration_machine_namespaces()
            current_public, desired_public, plan, desired_configuration = (
                self._prepare_goal_configuration_plan(
                    goal_id=goal_id,
                    capability_id=capability_id,
                    machine_namespaces=machine_namespaces,
                    options=options,
                )
            )
            if not execute:
                plan["capability_catalog"] = desired_public["capability_catalog"]
                self._send_json(plan, status=201)
                return
            require_expected_configuration_plan_revision(
                expected_plan_revision=expected_revision,
                actual_plan_revision=str(plan["plan_revision"]),
                subject="Goal configuration",
            )
            applied = self._goal_configuration_writer()(
                registry_path=self.server.registry_path,
                goal_id=goal_id,
                runtime_root_override=getattr(
                    self.server, "runtime_root_override", None
                ),
                execute=True,
                expected_goal_configuration_revision=current_public["revision"],
                codex_home_override=getattr(
                    getattr(self.server, "runtime_controller", None),
                    "codex_home",
                    None,
                ),
                codex_host_capacity_planner=plan_codex_subagent_capacity,
                codex_host_capacity_applier=apply_codex_subagent_capacity,
                **options,
            )
            if not applied.get("ok"):
                if not (applied.get("written") and applied.get("partial_write")):
                    raise RuntimeError("Goal configuration apply did not verify")
                self._send_partial_write_receipt(
                    applied=applied,
                    capability_id=capability_id,
                    desired_configuration=desired_configuration,
                    desired_public=desired_public,
                    goal_id=goal_id,
                    plan=plan,
                )
                return
            self._send_applied_goal_configuration(
                applied=applied,
                capability_id=capability_id,
                desired_configuration=desired_configuration,
                goal_id=goal_id,
                plan=plan,
            )
        except (TypeError, ValueError) as exc:
            stale = "preview again" in str(exc)
            self._send_error(
                str(exc),
                status=409 if stale else 400,
                error_code=(
                    "goal_configuration_preview_stale"
                    if stale
                    else "invalid_goal_configuration"
                ),
            )
        except Exception:  # noqa: BLE001 - sanitize browser-facing failures.
            self._send_error(
                "Goal configuration could not be applied; the prior state was preserved.",
                status=500,
                error_code="goal_configuration_apply_failed",
            )


def _capability_entry(payload: Mapping[str, Any], capability_id: str) -> dict[str, Any]:
    catalog = _mapping(payload.get("capability_catalog"), "capability catalog")
    capabilities = catalog.get("capabilities")
    if not isinstance(capabilities, list):
        raise ValueError("capability catalog is invalid")
    matches = [
        item
        for item in capabilities
        if isinstance(item, Mapping)
        and str(item.get("capability_id") or "") == capability_id
    ]
    if len(matches) != 1:
        raise ValueError(f"unknown Goal capability: {capability_id}")
    return dict(matches[0])


__all__ = [
    "CHAT_GOAL_CONFIGURATION_PATH",
    "CHAT_GOAL_CONFIGURATION_PREVIEW_PATH",
    "CHAT_GOAL_CONFIGURATION_APPLY_PATH",
    "GOAL_CONFIGURATION_INSPECTION_SCHEMA",
    "GoalConfigurationRequestMixin",
]
