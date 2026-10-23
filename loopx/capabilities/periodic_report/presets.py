from __future__ import annotations

from copy import deepcopy
from typing import Any

from .profile import (
    build_periodic_report_activation,
    normalize_periodic_report_profile,
)
from .project_progress import (
    PROJECT_PROGRESS_ADAPTER_ID,
    PROJECT_PROGRESS_SOURCE_ID,
    PROJECT_PROGRESS_SOURCE_KIND,
)


WEEKLY_PROGRESS_PRESET_ID = "weekly-progress"
PORTFOLIO_PROGRESS_PRESET_ID = "portfolio-progress"
PERIODIC_REPORT_PROFILE_PRESET_IDS = (WEEKLY_PROGRESS_PRESET_ID, PORTFOLIO_PROGRESS_PRESET_ID)
PERIODIC_REPORT_PROFILE_PRESET_ALIASES = {
    "weekly": WEEKLY_PROGRESS_PRESET_ID,
    "weekly-progress": WEEKLY_PROGRESS_PRESET_ID,
    "weekly-report": WEEKLY_PROGRESS_PRESET_ID,
    "portfolio-progress": PORTFOLIO_PROGRESS_PRESET_ID,
}

_WEEKLY_PROGRESS_PROFILE: dict[str, Any] = {
    "schema_version": "periodic_report_profile_v0",
    "enabled": True,
    "profile_id": "weekly_progress",
    "profile_version": "v1",
    "trigger_policy": {
        "enabled_kinds": [
            "manual",
            "bounded_segment_milestone",
            "cadence_due",
            "primary_goal_outcome",
            "vision_closed",
            "material_decision",
            "material_blocker",
            "material_recovery",
        ],
        "minimum_interval_seconds": 0,
    },
    "source_bindings": [
        {
            "source_id": PROJECT_PROGRESS_SOURCE_ID,
            "source_kind": PROJECT_PROGRESS_SOURCE_KIND,
            "adapter_id": PROJECT_PROGRESS_ADAPTER_ID,
            "provider": {"kind": "builtin"},
        }
    ],
    "renderer_bindings": [
        {
            "renderer_id": "html",
            "renderer_kind": "html",
            "adapter_id": "html_artifact_v0",
            "provider": {"kind": "builtin"},
        },
        {
            "renderer_id": "markdown",
            "renderer_kind": "markdown",
            "adapter_id": "markdown_v0",
            "provider": {"kind": "builtin"},
        },
    ],
    "sink_bindings": [],
}


def resolve_periodic_report_profile_preset(preset_id: str) -> dict[str, Any]:
    """Resolve a short user-facing alias into one normalized built-in profile."""

    requested = str(preset_id or "").strip().lower().replace("_", "-")
    canonical = PERIODIC_REPORT_PROFILE_PRESET_ALIASES.get(requested)
    if canonical not in PERIODIC_REPORT_PROFILE_PRESET_IDS:
        supported = ", ".join(PERIODIC_REPORT_PROFILE_PRESET_IDS)
        raise ValueError(
            f"unknown periodic report profile preset {preset_id!r}; "
            f"supported presets: {supported}"
        )
    profile = deepcopy(_WEEKLY_PROGRESS_PROFILE)
    if canonical == PORTFOLIO_PROGRESS_PRESET_ID:
        profile["profile_id"] = "portfolio_progress"
        profile["trigger_policy"]["enabled_kinds"] = ["cadence_due"]
        profile["source_bindings"] = [{"source_id": "manager_portfolio",
            "source_kind": "authorized_portfolio_evidence", "adapter_id": "manager_portfolio_v0",
            "provider": {"kind": "builtin"}}]
    return normalize_periodic_report_profile(profile)


def build_periodic_report_preset_activation(preset_id: str) -> dict[str, Any]:
    """Return an agent-readable activation packet for one built-in preset."""

    requested = str(preset_id or "").strip().lower().replace("_", "-")
    profile = resolve_periodic_report_profile_preset(requested)
    activation = build_periodic_report_activation(profile)
    activation["profile_preset"] = {
        "requested_id": requested,
        "resolved_id": PERIODIC_REPORT_PROFILE_PRESET_ALIASES[requested],
        "aliases": sorted(PERIODIC_REPORT_PROFILE_PRESET_ALIASES),
    }
    activation["interaction_contract"] = {
        "mode": "in_session_local_preview",
        "explicit_user_request_sufficient": True,
        "project_profile_file_required": False,
        "automation_required": False,
        "schedule_created": False,
        "source_scope": "current_project_public_safe_loopx_state",
        "renderer_adapter_ids": ["markdown_v0", "html_artifact_v0"],
        "external_write_allowed": False,
    }
    if profile["profile_id"] == "portfolio_progress":
        activation["interaction_contract"].update(mode="original_steward_private_chat",
            source_scope="original_authorized_local_portfolio",
            original_conversation_required=True,
            source_and_destination_from_original_binding=True)
    return activation
