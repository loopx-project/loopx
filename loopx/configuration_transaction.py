from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


CONFIGURATION_REVISION_MISSING = "absent"
_REVISION_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def configuration_payload_revision(value: object) -> str:
    """Return the stable public revision used by every configuration scope."""

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def goal_capability_configuration_revision(
    goal_id: str,
    capability_catalog: Mapping[str, Any],
) -> str:
    """Revision the complete Goal-owned configuration slice of a catalog."""

    normalized_goal_id = str(goal_id or "").strip()
    if not normalized_goal_id:
        raise ValueError("goal_id is required")
    capabilities = capability_catalog.get("capabilities")
    if not isinstance(capabilities, list) or any(
        not isinstance(item, Mapping) for item in capabilities
    ):
        raise ValueError("capability catalog is invalid")
    current: dict[str, Any] = {}
    for item in capabilities:
        scopes = item.get("available_scopes")
        if not isinstance(scopes, list) or "goal" not in scopes:
            continue
        capability_id = str(item.get("capability_id") or "").strip()
        if not capability_id or capability_id in current:
            raise ValueError("capability catalog contains an invalid Goal identity")
        current[capability_id] = item.get("current")
    return configuration_payload_revision(
        {"goal_id": normalized_goal_id, "current": current}
    )


def _validated_revision(value: str, *, label: str) -> str:
    revision = str(value or "").strip()
    if revision != CONFIGURATION_REVISION_MISSING and not _REVISION_RE.fullmatch(
        revision
    ):
        raise ValueError(f"{label} must be absent or a sha256 revision")
    return revision


def build_configuration_update_plan(
    *,
    schema_version: str,
    current_present: bool,
    desired_present: bool,
    current_revision: str,
    desired_revision: str,
    target_identity: Mapping[str, Any],
    changed_units: Mapping[str, Any],
    projected_configuration: Mapping[str, Any] | None,
    projection_field: str,
    additional_write_required: bool = False,
) -> dict[str, Any]:
    """Build one provider-neutral, revision-locked configuration preview.

    Stores retain ownership of normalization, locking, persistence, rollback,
    and public projection.  This kernel owns the common action semantics and
    the exact preview identity that apply endpoints must re-evaluate.
    """

    normalized_schema = str(schema_version or "").strip()
    if not normalized_schema:
        raise ValueError("configuration plan schema_version is required")
    normalized_projection_field = str(projection_field or "").strip()
    if not normalized_projection_field:
        raise ValueError("configuration plan projection_field is required")
    current = _validated_revision(current_revision, label="current_revision")
    desired = _validated_revision(desired_revision, label="desired_revision")
    if current_present == (current == CONFIGURATION_REVISION_MISSING):
        raise ValueError("current presence does not match current_revision")
    if desired_present == (desired == CONFIGURATION_REVISION_MISSING):
        raise ValueError("desired presence does not match desired_revision")

    if not current_present and desired_present:
        action = "create"
    elif current == desired and not additional_write_required:
        action = "unchanged"
    elif not desired_present:
        action = "delete"
    else:
        action = "update"
    identity = {
        "current_revision": current,
        "desired_revision": desired,
        **deepcopy(dict(target_identity)),
        "action": action,
        **deepcopy(dict(changed_units)),
    }
    return {
        "ok": True,
        "schema_version": normalized_schema,
        "status": "preview",
        **identity,
        "plan_revision": configuration_payload_revision(identity),
        "writes_required": int(action != "unchanged"),
        normalized_projection_field: (
            deepcopy(dict(projected_configuration))
            if projected_configuration is not None
            else None
        ),
    }


def require_expected_configuration_plan_revision(
    *,
    expected_plan_revision: str | None,
    actual_plan_revision: str,
    subject: str,
) -> None:
    """Fail closed unless apply targets the exact previewed configuration plan."""

    expected = str(expected_plan_revision or "").strip()
    if not expected:
        raise ValueError("expected_plan_revision is required when execute is true")
    actual = _validated_revision(actual_plan_revision, label="actual_plan_revision")
    if expected != actual:
        normalized_subject = str(subject or "configuration").strip() or "configuration"
        raise ValueError(f"{normalized_subject} plan revision changed; preview again")


__all__ = [
    "CONFIGURATION_REVISION_MISSING",
    "build_configuration_update_plan",
    "configuration_payload_revision",
    "goal_capability_configuration_revision",
    "require_expected_configuration_plan_revision",
]
