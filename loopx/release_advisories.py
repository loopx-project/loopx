"""Version-aware release advisories for known runtime defects.

An advisory names the last release that shipped a known defect and the first
release that fixed it. `loopx doctor` surfaces an advisory when the running
runtime is at or below the last affected release, so an operator sees the
bounded failure mode and the upgrade path. Advisories describe runtime
behavior only; they never interpret the user's project text.
"""

from __future__ import annotations

import re

RELEASE_ADVISORIES_SCHEMA_VERSION = "loopx_release_advisories_v0"

_VERSION_TOKEN = re.compile(r"\d+")


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in _VERSION_TOKEN.findall(version)) or (0,)


_RELEASE_ADVISORIES = (
    {
        "id": "legacy_replan_prose_stall_matcher",
        "last_affected_release": "0.4.5",
        "fixed_in_release": "0.4.6",
        "summary": (
            "Releases through 0.4.5 inferred autonomous-replan stalls from "
            "free-form run summaries with a substring matcher, so successful "
            "records containing words such as 'installed' or 'installation' "
            "could produce a no_progress_streak trigger and an erroneous "
            "autonomous_replan_required decision."
        ),
    },
)


def release_advisories_for(current_version: str) -> tuple[dict[str, str], ...]:
    """Return the advisories that affect the running release."""

    running = _version_tuple(current_version)
    advisories: list[dict[str, str]] = []
    for advisory in _RELEASE_ADVISORIES:
        if running <= _version_tuple(advisory["last_affected_release"]):
            advisories.append(
                {
                    "id": advisory["id"],
                    "current_version": current_version,
                    "last_affected_release": advisory["last_affected_release"],
                    "fixed_in_release": advisory["fixed_in_release"],
                    "summary": advisory["summary"],
                    "guidance": (
                        "Upgrade to "
                        f"{advisory['fixed_in_release']} or newer: run "
                        "`loopx update check` and then `loopx update apply`."
                    ),
                }
            )
    return tuple(advisories)


def build_release_advisories_section(current_version: str) -> dict[str, object]:
    affected = release_advisories_for(current_version)
    return {
        "schema_version": RELEASE_ADVISORIES_SCHEMA_VERSION,
        "current_version": current_version,
        "requires_upgrade": bool(affected),
        "advisories": list(affected),
    }
