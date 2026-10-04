"""Provider-neutral periodic report contracts and adapter registry."""
from __future__ import annotations

from importlib import import_module
from typing import Any

# Reading a subscription or pending intent does not select report generation.
# Keep compatibility exports identical to their owners without importing all
# render/archive/delivery adapters when Python initializes a read submodule.
_EXPORTS = {
    "PeriodicReportAdapterRegistry": "adapters",
    "PeriodicReportRendererAdapter": "adapters",
    "PeriodicReportSinkAdapter": "adapters",
    "PeriodicReportSourceAdapter": "adapters",
    "build_periodic_report_document": "adapters",
    "build_periodic_report_editorial": "adapters",
    "build_periodic_report_source_result": "adapters",
    "build_periodic_report_archive_bundle": "archive",
    "verify_periodic_report_archive_receipts": "archive",
    "build_periodic_report_announcement_plan": "audience",
    "normalize_periodic_report_audience_policy": "audience",
    "build_periodic_report_delivery_receipt": "bindings",
    "build_periodic_report_extension_readiness": "bindings",
    "build_periodic_report_generation_bundle": "bindings",
    "normalize_periodic_report_sink_bindings": "bindings",
    "build_periodic_report_run": "core",
    "build_goal_periodic_report_delivery_identity": "machine_defaults",
    "build_goal_periodic_report_delivery_plan": "machine_defaults",
    "build_periodic_report_delivery_authority": "machine_defaults",
    "normalize_loopx_machine_defaults": "machine_defaults",
    "normalize_periodic_report_machine_defaults": "machine_defaults",
    "normalize_periodic_report_delivery_authority": "machine_defaults",
    "periodic_report_machine_configuration_namespace": "machine_defaults",
    "resolve_goal_periodic_report_subscription": "machine_defaults",
    "select_goal_periodic_report_executor": "machine_defaults",
    "PERIODIC_REPORT_PROFILE_PRESET_ALIASES": "presets",
    "PERIODIC_REPORT_PROFILE_PRESET_IDS": "presets",
    "WEEKLY_PROGRESS_PRESET_ID": "presets",
    "build_periodic_report_preset_activation": "presets",
    "resolve_periodic_report_profile_preset": "presets",
    "build_periodic_report_activation": "profile",
    "normalize_periodic_report_profile": "profile",
    "PROJECT_PROGRESS_PROJECTION_SCHEMA": "project_progress",
    "build_project_progress_periodic_report_source": "project_progress",
    "project_progress_periodic_report_source_adapter": "project_progress",
    "build_periodic_report_trigger_decision": "triggers",
    "normalize_periodic_report_trigger_policy": "triggers",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

__all__ = [
    "PeriodicReportAdapterRegistry",
    "PeriodicReportRendererAdapter",
    "PeriodicReportSinkAdapter",
    "PeriodicReportSourceAdapter",
    "PERIODIC_REPORT_PROFILE_PRESET_ALIASES",
    "PERIODIC_REPORT_PROFILE_PRESET_IDS",
    "PROJECT_PROGRESS_PROJECTION_SCHEMA",
    "WEEKLY_PROGRESS_PRESET_ID",
    "build_periodic_report_activation",
    "build_periodic_report_announcement_plan",
    "build_periodic_report_document",
    "build_periodic_report_delivery_receipt",
    "build_periodic_report_editorial",
    "build_periodic_report_extension_readiness",
    "build_periodic_report_generation_bundle",
    "build_periodic_report_preset_activation",
    "build_project_progress_periodic_report_source",
    "build_periodic_report_archive_bundle",
    "build_goal_periodic_report_delivery_identity",
    "build_goal_periodic_report_delivery_plan",
    "build_periodic_report_delivery_authority",
    "build_periodic_report_run",
    "build_periodic_report_source_result",
    "build_periodic_report_trigger_decision",
    "normalize_periodic_report_profile",
    "normalize_loopx_machine_defaults",
    "normalize_periodic_report_machine_defaults",
    "normalize_periodic_report_delivery_authority",
    "periodic_report_machine_configuration_namespace",
    "normalize_periodic_report_audience_policy",
    "normalize_periodic_report_sink_bindings",
    "normalize_periodic_report_trigger_policy",
    "project_progress_periodic_report_source_adapter",
    "resolve_periodic_report_profile_preset",
    "resolve_goal_periodic_report_subscription",
    "select_goal_periodic_report_executor",
    "verify_periodic_report_archive_receipts",
]
