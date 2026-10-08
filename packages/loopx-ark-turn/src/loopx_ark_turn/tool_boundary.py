"""Provider-owned tool policy and observations; no work or quota authority."""
from __future__ import annotations

from typing import Any

from arkruntime.types.agent import ToolItem, ToolDefaultConfig, PermissionPolicy
from arkruntime.types.session import (
    EnvironmentWithOverrides, EnvironmentConfigOverride, EnvironmentNetworkingConfig,
    EnvironmentPackagesConfig,
)

from .config import AdapterError, Config, digest
from .receipt import Receipt


REVISION = "sandbox_tools_v1"
NETWORK = {"type": "limited", "allowed_hosts": [],
           "allow_mcp_servers": False, "allow_package_managers": False}


def declarations(config: Config, custom: list[ToolItem]) -> list[ToolItem]:
    return [ToolItem(type="agent_toolset_20260701", default_config=ToolDefaultConfig(
        enabled=config.sandbox_builtins,
        permission_policy=PermissionPolicy(type="always_allow") if config.sandbox_builtins else None,
    )), *custom]


def environment_override(config: Config) -> EnvironmentWithOverrides:
    return EnvironmentWithOverrides(type="environment_with_overrides", id=config.environment_id,
        config=EnvironmentConfigOverride(type="cloud", env={}, setup_script="",
            packages=EnvironmentPackagesConfig(type="packages", pip=[], apt=[], npm=[], cargo=[], gem=[], go=[]),
            networking=EnvironmentNetworkingConfig(**NETWORK)))


def qualify_environment(environment: dict[str, Any], config: Config, *, frozen: bool) -> None:
    # Preflight rejects unsafe inherited startup state before creating a Session.
    # Frozen readback additionally proves that the requested override is visible.
    settings = environment.get("config") or {}
    packages = settings.get("packages") or {}
    if (environment.get("id") != config.environment_id or settings.get("type") != "cloud"
            or any(settings.get(k) for k in ("env", "setup_script", "tos"))
            or any(v for k, v in packages.items() if k != "type")):
        raise AdapterError("provider_sandbox_environment_not_qualified")
    if frozen and settings.get("networking") != NETWORK:
        raise AdapterError("provider_sandbox_networking_not_qualified")


def observe_builtin(receipt: Receipt, config: Config, event: dict[str, Any]) -> None:
    if not config.sandbox_builtins:
        raise AdapterError("provider_builtin_tools_not_enabled")
    # The use event's id is the call identity on SDK 0.8.0 wire responses;
    # results carry that identity in tool_use_id (the use field can be empty).
    call_id = (event.get("tool_use_id") or event.get("id")) if event["type"] == "agent.tool_use" else event.get("tool_use_id")
    if not isinstance(call_id, str) or not call_id:
        raise AdapterError("provider_builtin_identity_missing")
    calls = receipt.data.setdefault("builtin_tools", {})
    if event["type"] == "agent.tool_use":
        if not event.get("name") or event.get("evaluated_permission") != "allow":
            raise AdapterError("provider_builtin_permission_not_qualified")
        fingerprint = digest([event.get("name"), event.get("input")])
        if call_id in calls:
            if calls[call_id]["input_digest"] != fingerprint:
                raise AdapterError("provider_builtin_identity_conflict")
            return
        # This is an observation ceiling, not a pre-effect provider execution cap.
        if len(calls) + len(receipt.data["tools"]) >= config.max_tool_calls:
            raise AdapterError("provider_tool_observation_budget_exhausted")
        calls[call_id] = {"name": event["name"], "input_digest": fingerprint}
    else:
        if call_id not in calls:
            raise AdapterError("provider_builtin_result_without_call")
        fingerprint = digest([event.get("content"), event.get("is_error")])
        prior = calls[call_id].get("result_digest")
        if prior and prior != fingerprint:
            raise AdapterError("provider_builtin_result_conflict")
        calls[call_id].update(result_digest=fingerprint, is_error=bool(event.get("is_error")))
    receipt.save()


def require_builtin_results(receipt: Receipt) -> None:
    if any("result_digest" not in call for call in receipt.data.get("builtin_tools", {}).values()):
        raise AdapterError("provider_builtin_result_missing")
