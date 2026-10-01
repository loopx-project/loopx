"""Default-off, goal-scoped Decision Context profile and activation status."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...public_safe_text import COMPACT_TOKEN_PATTERN as _TOKEN_RE
from .providers import decision_source_provider_registered
from .sources import DecisionSourceSpec

DECISION_CONTEXT_PROFILE_SCHEMA_VERSION = "decision_context_profile_v0"
DECISION_CONTEXT_ACTIVATION_STATUS_SCHEMA_VERSION = (
    "decision_context_activation_status_v0"
)
MAX_DECISION_SOURCES = 64
MAX_SOURCE_PROVIDER_BINDINGS = 16

_CONFIG_FIELDS = {
    "schema_version",
    "goal_id",
    "enabled",
    "enabled_agents",
    "source_provider_bindings",
    "sources",
    "context_provider",
    "automation",
}
_PROVIDER_BINDING_FIELDS = {"provider_id", "adapter", "config"}
_SOURCE_FIELDS = {
    "source_id",
    "provider_id",
    "source_kind",
    "priority",
    "evidence_level",
    "objectives",
    "scan_mode",
    "exact_read_policy",
    "freshness_seconds",
    "private_locator",
    "max_changes",
    "enabled",
}
_CONTEXT_PROVIDER_FIELDS = {
    "provider",
    "namespace",
    "scope_ref",
    "max_results",
    "timeout_seconds",
    "config",
}
_AUTOMATION_FIELDS = {
    "automatic_capture",
    "fail_open",
    "source_ids",
    "interval_seconds",
    "max_pending_batches",
    "max_sources_per_tick",
}


def _strict_mapping(
    value: object,
    *,
    label: str,
    allowed: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ValueError(
            f"{label} contains unsupported fields: {', '.join(unexpected)}"
        )
    return value


def _bounded_mappings(
    value: object,
    *,
    label: str,
    maximum: int,
    allow_empty: bool = False,
) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a bounded object list")
    rows = list(value)
    if (not allow_empty and not rows) or len(rows) > maximum:
        raise ValueError(f"{label} must be a bounded object list")
    if any(not isinstance(row, Mapping) for row in rows):
        raise ValueError(f"{label} must be a bounded object list")
    return rows


def _token(value: object, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(f"{field_name} must be a compact public-safe token")
    return text


def _boolean(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _positive_number(
    value: object,
    *,
    field_name: str,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a positive number")
    result = float(value)
    if not 0 < result <= maximum:
        raise ValueError(f"{field_name} must be between 0 and {maximum}")
    return result


def _positive_int(
    value: object,
    *,
    field_name: str,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ValueError(f"{field_name} must be between 1 and {maximum}")
    return value


@dataclass(frozen=True)
class DecisionSourceProviderBinding:
    provider_id: str
    adapter: str
    config: Mapping[str, Any] = field(repr=False)

    def public_record(self, *, runtime_bound: bool = False) -> dict[str, object]:
        adapter_registered = decision_source_provider_registered(self.adapter)
        return {
            "adapter": (
                "runtime-bound"
                if runtime_bound
                else self.adapter
                if adapter_registered
                else "unregistered"
            ),
            "adapter_registered": adapter_registered,
            "runtime_bound": runtime_bound,
            "private_config_captured": False,
        }


@dataclass(frozen=True)
class DecisionContextProfile:
    goal_id: str
    enabled: bool
    enabled_agents: tuple[str, ...]
    provider_bindings: tuple[DecisionSourceProviderBinding, ...]
    sources: tuple[DecisionSourceSpec, ...]
    context_provider: Mapping[str, Any] | None = field(default=None, repr=False)
    automatic_capture: bool = False
    fail_open: bool = True
    capture_source_ids: tuple[str, ...] = ()
    capture_interval_seconds: int = 900
    capture_max_pending_batches: int = 1000
    capture_max_sources_per_tick: int = 8

    def provider_binding_map(self) -> dict[str, Mapping[str, Any]]:
        return {
            binding.provider_id: {
                "provider_id": binding.provider_id,
                "adapter": binding.adapter,
                "config": dict(binding.config),
            }
            for binding in self.provider_bindings
        }


def normalize_decision_context_profile(
    raw: Mapping[str, Any],
) -> DecisionContextProfile:
    config = _strict_mapping(
        raw,
        label="decision-context profile",
        allowed=_CONFIG_FIELDS,
    )
    if config.get("schema_version") != DECISION_CONTEXT_PROFILE_SCHEMA_VERSION:
        raise ValueError(
            "decision-context profile must use "
            + DECISION_CONTEXT_PROFILE_SCHEMA_VERSION
        )
    goal_id = _token(config.get("goal_id"), field_name="goal_id")
    enabled = _boolean(config.get("enabled"), field_name="enabled")

    raw_agents = config.get("enabled_agents")
    if not isinstance(raw_agents, Sequence) or isinstance(raw_agents, (str, bytes)):
        raise ValueError("enabled_agents must be a bounded token list")
    enabled_agents = tuple(
        sorted({_token(value, field_name="enabled_agents[]") for value in raw_agents})
    )
    if enabled and not enabled_agents:
        raise ValueError("an enabled profile requires at least one enabled agent")

    bindings: list[DecisionSourceProviderBinding] = []
    binding_ids: set[str] = set()
    for index, row in enumerate(
        _bounded_mappings(
            config.get("source_provider_bindings"),
            label="source_provider_bindings",
            maximum=MAX_SOURCE_PROVIDER_BINDINGS,
            allow_empty=True,
        )
    ):
        binding = _strict_mapping(
            row,
            label=f"source_provider_bindings[{index}]",
            allowed=_PROVIDER_BINDING_FIELDS,
        )
        provider_id = _token(binding.get("provider_id"), field_name="provider_id")
        if provider_id in binding_ids:
            raise ValueError("source provider ids must be unique")
        adapter = _token(binding.get("adapter"), field_name="adapter")
        private_config = binding.get("config", {})
        if not isinstance(private_config, Mapping):
            raise ValueError("source provider config must be an object")
        bindings.append(
            DecisionSourceProviderBinding(
                provider_id=provider_id,
                adapter=adapter,
                config=dict(private_config),
            )
        )
        binding_ids.add(provider_id)

    sources: list[DecisionSourceSpec] = []
    source_ids: set[str] = set()
    for index, row in enumerate(
        _bounded_mappings(
            config.get("sources"),
            label="sources",
            maximum=MAX_DECISION_SOURCES,
            allow_empty=True,
        )
    ):
        source = _strict_mapping(
            row,
            label=f"sources[{index}]",
            allowed=_SOURCE_FIELDS,
        )
        objectives = source.get("objectives")
        if not isinstance(objectives, Sequence) or isinstance(
            objectives,
            (str, bytes),
        ):
            raise ValueError("source objectives must be a list")
        freshness_seconds = _positive_int(
            source.get("freshness_seconds"),
            field_name="freshness_seconds",
            maximum=365 * 24 * 60 * 60,
        )
        max_changes = _positive_int(
            source.get("max_changes", 20),
            field_name="max_changes",
            maximum=100,
        )
        source_enabled = _boolean(
            source.get("enabled", True),
            field_name="source enabled",
        )
        spec = DecisionSourceSpec(
            source_id=str(source.get("source_id") or ""),
            provider_id=str(source.get("provider_id") or ""),
            source_kind=str(source.get("source_kind") or ""),
            priority=str(source.get("priority") or ""),
            evidence_level=str(source.get("evidence_level") or ""),
            objectives=tuple(str(value) for value in objectives),
            scan_mode=str(source.get("scan_mode") or ""),
            exact_read_policy=str(source.get("exact_read_policy") or ""),
            freshness_seconds=freshness_seconds,
            private_locator=str(source.get("private_locator") or ""),
            max_changes=max_changes,
            enabled=source_enabled,
        )
        if spec.source_id in source_ids:
            raise ValueError("source ids must be unique")
        if spec.provider_id not in binding_ids:
            raise ValueError("every source must reference a declared provider binding")
        sources.append(spec)
        source_ids.add(spec.source_id)
    context_provider: Mapping[str, Any] | None = None
    raw_context_provider = config.get("context_provider")
    if raw_context_provider is not None:
        context = _strict_mapping(
            raw_context_provider,
            label="context_provider",
            allowed=_CONTEXT_PROVIDER_FIELDS,
        )
        private_config = context.get("config", {})
        if not isinstance(private_config, Mapping):
            raise ValueError("context provider config must be an object")
        context_provider = {
            "provider": _token(context.get("provider"), field_name="context provider"),
            "namespace": _token(context.get("namespace"), field_name="namespace"),
            "scope_ref": str(context.get("scope_ref") or "").strip() or None,
            "max_results": _positive_int(
                context.get("max_results", 4),
                field_name="max_results",
                maximum=8,
            ),
            "timeout_seconds": _positive_number(
                context.get("timeout_seconds", 10),
                field_name="timeout_seconds",
                maximum=60,
            ),
            "config": dict(private_config),
        }

    if enabled and not sources and context_provider is None:
        raise ValueError(
            "an enabled profile requires at least one source or context provider"
        )

    automation = _strict_mapping(
        config.get("automation"),
        label="automation",
        allowed=_AUTOMATION_FIELDS,
    )
    automatic_capture = _boolean(
        automation.get("automatic_capture"),
        field_name="automatic_capture",
    )
    fail_open = _boolean(automation.get("fail_open"), field_name="fail_open")
    capture_ids = automation.get("source_ids", [])
    if not isinstance(capture_ids, list) or any(
        not isinstance(value, str) for value in capture_ids
    ):
        raise ValueError("automation source_ids must be a list of source ids")
    eligible_ids = {
        source.source_id
        for source in sources
        if source.enabled
        and source.scan_mode == "incremental"
        and source.exact_read_policy != "never"
    }
    if (
        len(capture_ids) != len(set(capture_ids))
        or not set(capture_ids) <= eligible_ids
    ):
        raise ValueError("capture sources must be unique enabled incremental sources")
    if automatic_capture and not capture_ids:
        raise ValueError("automatic capture requires an explicit source_ids allowlist")
    capture_interval = _positive_int(
        automation.get("interval_seconds", 900),
        field_name="interval_seconds",
        maximum=86400,
    )
    capture_capacity = _positive_int(
        automation.get("max_pending_batches", 1000),
        field_name="max_pending_batches",
        maximum=10000,
    )
    capture_scan_limit = _positive_int(
        automation.get("max_sources_per_tick", 8),
        field_name="max_sources_per_tick",
        maximum=MAX_DECISION_SOURCES,
    )
    if not fail_open:
        raise ValueError("decision-context providers must fail open")

    return DecisionContextProfile(
        goal_id=goal_id,
        enabled=enabled,
        enabled_agents=enabled_agents,
        provider_bindings=tuple(bindings),
        sources=tuple(sources),
        context_provider=context_provider,
        automatic_capture=automatic_capture,
        fail_open=True,
        capture_source_ids=tuple(capture_ids),
        capture_interval_seconds=capture_interval,
        capture_max_pending_batches=capture_capacity,
        capture_max_sources_per_tick=capture_scan_limit,
    )


def load_decision_context_profile(
    path: Path,
) -> DecisionContextProfile:
    try:
        with path.expanduser().open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("decision-context profile is unavailable") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("decision-context profile must be an object")
    return normalize_decision_context_profile(payload)


def resolve_decision_context_activation(
    *,
    goal_id: str,
    agent_id: str,
    profile_path: Path | None,
    available_source_provider_ids: Collection[str] | None = None,
) -> tuple[dict[str, Any], DecisionContextProfile | None]:
    normalized_goal = _token(goal_id, field_name="goal_id")
    normalized_agent = _token(agent_id, field_name="agent_id")
    if isinstance(available_source_provider_ids, (str, bytes)):
        raise TypeError("available_source_provider_ids must be a collection")
    runtime_provider_ids = {
        _token(provider_id, field_name="available_source_provider_ids[]")
        for provider_id in (available_source_provider_ids or ())
    }
    base: dict[str, Any] = {
        "ok": True,
        "schema_version": DECISION_CONTEXT_ACTIVATION_STATUS_SCHEMA_VERSION,
        "capability_id": "decision_context",
        "scope": "goal",
        "default_enabled": False,
        "goal_id": normalized_goal,
        "agent_id": normalized_agent,
        "profile_configured": profile_path is not None,
        "enabled": False,
        "configured_for_agent": False,
        "available": False,
        "source_count": 0,
        "source_provider_count": 0,
        "runtime_bound_provider_count": 0,
        "context_provider_configured": False,
        "automatic_capture": False,
        "fail_open": True,
        "external_writes_performed": False,
        "raw_content_captured": False,
        "private_locator_captured": False,
        "credentials_captured": False,
    }
    if profile_path is None:
        return base | {"status": "disabled"}, None
    try:
        config = load_decision_context_profile(profile_path)
    except ValueError:
        return base | {
            "status": "profile_invalid",
            "reason_code": "profile_unavailable_or_invalid",
        }, None
    if config.goal_id != normalized_goal:
        return base | {
            "status": "profile_invalid",
            "reason_code": "goal_scope_mismatch",
        }, None

    configured_for_agent = normalized_agent in config.enabled_agents
    declared_provider_ids = {
        binding.provider_id for binding in config.provider_bindings
    }
    if not runtime_provider_ids <= declared_provider_ids:
        return base | {
            "status": "profile_invalid",
            "reason_code": "runtime_provider_scope_mismatch",
        }, config
    provider_records = [
        binding.public_record(
            runtime_bound=binding.provider_id in runtime_provider_ids,
        )
        for binding in config.provider_bindings
    ]
    unavailable_adapter_count = sum(
        not bool(record["adapter_registered"]) and not bool(record["runtime_bound"])
        for record in provider_records
    )
    status = base | {
        "enabled": config.enabled,
        "configured_for_agent": configured_for_agent,
        "source_count": len(config.sources),
        "source_provider_count": len(config.provider_bindings),
        "runtime_bound_provider_count": len(runtime_provider_ids),
        "source_providers": provider_records,
        "context_provider_configured": config.context_provider is not None,
        "automatic_capture": config.automatic_capture,
        "capture_source_count": len(config.capture_source_ids),
        "capture_interval_seconds": config.capture_interval_seconds,
    }
    if not config.enabled:
        return status | {"status": "disabled"}, config
    if not configured_for_agent:
        return status | {"status": "agent_not_enabled"}, config
    if unavailable_adapter_count:
        return status | {
            "status": "provider_unavailable",
            "reason_code": "source_provider_adapter_unavailable",
            "unavailable_adapter_count": unavailable_adapter_count,
        }, config
    return status | {"status": "available", "available": True}, config
