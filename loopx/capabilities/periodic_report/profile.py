from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from .audience import normalize_periodic_report_audience_policy
from .bindings import (
    _boolean,
    _mapping,
    _sequence,
    _sha256,
    _text,
    _token,
    _version,
    normalize_periodic_report_sink_bindings,
)
from .core import _reject_raw_keys
from .triggers import normalize_periodic_report_trigger_policy


PROFILE_SCHEMA = "periodic_report_profile_v0"
ACTIVATION_SCHEMA = "periodic_report_activation_v0"
SOURCE_BINDING_SCHEMA = "periodic_report_source_binding_v0"
RENDERER_BINDING_SCHEMA = "periodic_report_renderer_binding_v0"
SCHEDULE_SCHEMA = "periodic_report_schedule_v0"

_PROVIDER_KINDS = {"builtin", "extension"}
_TIMEZONE_RE = re.compile(r"^[A-Za-z_+-]+(?:/[A-Za-z0-9_+-]+)*$")
_PROFILE_FIELDS = {
    "schema_version",
    "enabled",
    "profile_id",
    "profile_version",
    "trigger_policy",
    "schedule",
    "source_bindings",
    "renderer_bindings",
    "sink_bindings",
    "audience_policy",
}


def _reject_unknown_fields(
    value: Mapping[str, Any], *, allowed: set[str], label: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {', '.join(unknown)}")


def _normalize_adapter_bindings(
    raw: Sequence[Mapping[str, Any]], *, kind: str
) -> list[dict[str, Any]]:
    if kind == "source":
        schema = SOURCE_BINDING_SCHEMA
        id_field = "source_id"
        kind_field = "source_kind"
    else:
        schema = RENDERER_BINDING_SCHEMA
        id_field = "renderer_id"
        kind_field = "renderer_kind"
    bindings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_item in enumerate(raw):
        label = f"{kind}_bindings[{index}]"
        item = _mapping(raw_item, label)
        _reject_raw_keys(item, label)
        _reject_unknown_fields(
            item,
            allowed={
                "schema_version",
                id_field,
                kind_field,
                "adapter_id",
                "provider",
            },
            label=label,
        )
        if item.get("schema_version", schema) != schema:
            raise ValueError(f"{label} must use {schema}")
        binding_id = _token(item.get(id_field), f"{label}.{id_field}")
        if binding_id in seen:
            raise ValueError(f"duplicate {id_field} {binding_id!r}")
        seen.add(binding_id)
        provider = _mapping(
            item.get("provider", {"kind": "builtin"}), f"{label}.provider"
        )
        _reject_unknown_fields(
            provider,
            allowed={"kind", "provider_id", "provider_version"},
            label=f"{label}.provider",
        )
        provider_kind = _token(
            provider.get("kind", "builtin"), f"{label}.provider.kind"
        )
        if provider_kind not in _PROVIDER_KINDS:
            raise ValueError(f"{label}.provider.kind must be builtin or extension")
        normalized_provider: dict[str, str] = {"kind": provider_kind}
        if provider_kind == "extension":
            normalized_provider.update(
                {
                    "provider_id": _token(
                        provider.get("provider_id"),
                        f"{label}.provider.provider_id",
                    ),
                    "provider_version": _version(
                        provider.get("provider_version"),
                        f"{label}.provider.provider_version",
                    ),
                }
            )
        elif provider.get("provider_id") or provider.get("provider_version"):
            raise ValueError(
                f"{label}.provider builtin must not declare extension identity"
            )
        bindings.append(
            {
                "schema_version": schema,
                id_field: binding_id,
                kind_field: _token(item.get(kind_field), f"{label}.{kind_field}"),
                "adapter_id": _token(item.get("adapter_id"), f"{label}.adapter_id"),
                "provider": normalized_provider,
            }
        )
    return sorted(bindings, key=lambda item: str(item[id_field]))


def _normalize_schedule(raw: object) -> dict[str, str] | None:
    if raw is None:
        return None
    schedule = _mapping(raw, "schedule")
    _reject_raw_keys(schedule, "schedule")
    _reject_unknown_fields(
        schedule,
        allowed={"schema_version", "schedule_id", "rrule", "timezone", "start_date"},
        label="schedule",
    )
    if schedule.get("schema_version", SCHEDULE_SCHEMA) != SCHEDULE_SCHEMA:
        raise ValueError(f"schedule must use {SCHEDULE_SCHEMA}")
    rrule = _text(schedule.get("rrule"), "schedule.rrule", maximum=500).upper()
    if not rrule.startswith("FREQ="):
        raise ValueError("schedule.rrule must start with FREQ=")
    timezone = _text(schedule.get("timezone"), "schedule.timezone", maximum=80)
    if not _TIMEZONE_RE.fullmatch(timezone):
        raise ValueError("schedule.timezone must be an IANA-like timezone name")
    normalized = {
        "schema_version": SCHEDULE_SCHEMA,
        "schedule_id": _token(schedule.get("schedule_id"), "schedule.schedule_id"),
        "rrule": rrule,
        "timezone": timezone,
    }
    if "start_date" in schedule:
        start = _text(schedule["start_date"], "schedule.start_date", maximum=10)
        try:
            if date.fromisoformat(start).isoformat() != start:
                raise ValueError("noncanonical date")
        except ValueError as exc:
            raise ValueError("schedule.start_date must use YYYY-MM-DD") from exc
        normalized["start_date"] = start
    return normalized


def normalize_periodic_report_profile(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one default-off, domain-neutral project reporting profile."""

    profile = _mapping(raw, "profile")
    _reject_raw_keys(profile, "profile")
    _reject_unknown_fields(profile, allowed=_PROFILE_FIELDS, label="profile")
    if profile.get("schema_version") != PROFILE_SCHEMA:
        raise ValueError(f"profile must use {PROFILE_SCHEMA}")
    enabled = _boolean(profile.get("enabled", False), "profile.enabled")
    sources = _normalize_adapter_bindings(
        _sequence(profile.get("source_bindings", []), "profile.source_bindings"),
        kind="source",
    )
    renderers = _normalize_adapter_bindings(
        _sequence(profile.get("renderer_bindings", []), "profile.renderer_bindings"),
        kind="renderer",
    )
    sinks = normalize_periodic_report_sink_bindings(
        _sequence(profile.get("sink_bindings", []), "profile.sink_bindings")
    )
    if enabled and not sources:
        raise ValueError("enabled profile requires at least one source binding")
    if enabled and not renderers:
        raise ValueError("enabled profile requires at least one renderer binding")
    normalized: dict[str, Any] = {
        "schema_version": PROFILE_SCHEMA,
        "enabled": enabled,
        "profile_id": _token(profile.get("profile_id"), "profile.profile_id"),
        "profile_version": _version(
            profile.get("profile_version"), "profile.profile_version"
        ),
        "trigger_policy": normalize_periodic_report_trigger_policy(
            profile.get("trigger_policy", {})
        ),
        "source_bindings": sources,
        "renderer_bindings": renderers,
        "sink_bindings": sinks,
    }
    schedule = _normalize_schedule(profile.get("schedule"))
    if schedule is not None:
        normalized["schedule"] = schedule
    if profile.get("audience_policy") is not None:
        normalized["audience_policy"] = normalize_periodic_report_audience_policy(
            _mapping(profile["audience_policy"], "profile.audience_policy")
        )
    return normalized


def build_periodic_report_activation(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic activation receipt without invoking adapters."""

    profile = normalize_periodic_report_profile(raw)
    enabled_sinks = [
        item
        for item in profile["sink_bindings"]
        if item["dependency_policy"] != "disabled"
    ]
    required_sinks = [
        item for item in enabled_sinks if item["dependency_policy"] == "required"
    ]
    if not enabled_sinks:
        mode = "portable"
    elif required_sinks:
        mode = "durable"
    else:
        mode = "enhanced"
    return {
        "ok": True,
        "schema_version": ACTIVATION_SCHEMA,
        "status": "enabled" if profile["enabled"] else "disabled",
        "active": profile["enabled"],
        "generation_allowed": profile["enabled"],
        "profile_digest": _sha256(profile),
        "profile": profile,
        "extension_mode": mode,
        "required_extension_count": len(required_sinks),
        "optional_extension_count": sum(
            item["dependency_policy"] == "optional" for item in enabled_sinks
        ),
        "boundary": {
            "default_enabled": False,
            "business_semantics_owned_by_sources": True,
            "schedule_application_owned_by_host": True,
            "extension_effects_performed": False,
            "external_writes_performed": False,
        },
    }
