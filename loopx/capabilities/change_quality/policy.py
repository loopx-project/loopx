from __future__ import annotations

from typing import Any, cast



CHANGE_QUALITY_POLICY_SCHEMA_VERSION = "change_quality_qualification_policy_v0"


def change_quality_goal_policy(goal: dict[str, Any]) -> dict[str, Any]:
    control_plane = (
        cast(dict[str, Any], goal.get("control_plane"))
        if isinstance(goal.get("control_plane"), dict)
        else {}
    )
    raw = (
        cast(dict[str, Any], control_plane.get("change_quality_qualification"))
        if isinstance(control_plane.get("change_quality_qualification"), dict)
        else {}
    )
    return {
        "schema_version": CHANGE_QUALITY_POLICY_SCHEMA_VERSION,
        "enabled": raw.get("enabled") is True,
        "safe_fix": raw.get("safe_fix") is True,
        "strict_receipt": raw.get("strict_receipt") is True,
    }


def change_quality_goal_policy_summary(goal: dict[str, Any]) -> dict[str, Any]:
    policy = change_quality_goal_policy(goal)
    return {
        "enabled": policy["enabled"],
        "safe_fix": policy["safe_fix"],
        "strict_receipt": policy["strict_receipt"],
    }
