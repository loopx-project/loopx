"""Installation-provider target resolution before the existing activation path.

Kept in the Python bootstrap/OS provider: upgrading or repairing an installation
must not first require a working Node/control-plane runtime. This extends the
existing self_update owner and does not add Goal lifecycle vocabulary.
"""

from __future__ import annotations

from typing import Any

from .update_artifact import resolve_artifact, target_direction
from ..activation_qualification import runtime_activation_qualification


def prepare_target(payload: dict[str, Any], *, freshness: dict, resolve_remote: bool,
                   allow_downgrade: bool) -> dict[str, Any]:
    if payload["install_lifecycle"]["owner"] != "loopx_release_snapshot" or not payload["install_lifecycle"]["loopx_apply_supported"]:
        return payload
    source = payload["source"]
    target = None
    reason = None
    if source["distribution"] == "runtime":
        if resolve_remote:
            try:
                artifact = resolve_artifact(source["repo"], source["ref"])
                source["artifact"] = artifact
                target = artifact["source_revision"]
                version = artifact["package_version"]
                payload["source_version_check"].update(attempted=True, status="available",
                    version=version, version_tag="v" + version,
                    matches_current=version == freshness.get("current_version"),
                    reason="Version and revision come from the same qualified runtime metadata")
            except ValueError as exc:
                reason = str(exc)
        else:
            source["target_status"] = "not_resolved"
    else:
        check = payload.get("source_commit_check", {})
        target = check.get("target_commit")
        if not target and not source.get("explicit_archive") and resolve_remote:
            reason = "Source target could not be locked; retry online or select a full commit SHA"
    source["resolved_revision"] = target
    current = freshness.get("manifest_source_git_commit")
    direction = target_direction(source["repo"], current, target) if target and resolve_remote and (
        source["distribution"] == "runtime" or payload["execute_requested"]) else (
        "identical" if current and current == target else "unknown")
    source["direction"] = direction
    source["allow_downgrade"] = allow_downgrade
    if target:
        source["target_status"] = "resolved"
        payload["source_commit_check"].update(status="available", target_commit=target,
            matches_current=current == target,
            reason="Target revision resolved before installation")
        payload["runtime_activation_qualification"] = runtime_activation_qualification(
            install_freshness=freshness, source=source, resolved_source_commit=target,
            selected_source_unresolved=False)
    # An explicit arbitrary archive remains a developer compatibility path.
    # Never infer direction from SHA ordering, release age or UI timestamps.
    if target and direction not in {"ahead", "identical"} and not allow_downgrade and (
        source["distribution"] == "runtime" or payload["execute_requested"]):
        reason = "Target is older, diverged or its lineage is unverified; inspect it and explicitly use --allow-downgrade if intended"
    if resolve_remote and reason:
        source["target_status"] = "blocked"
        payload["plan"].update(apply_supported=False, blocked_reason=reason, mutates_release_install=False)
        payload["recommended_action"] = reason
        payload["error"] = reason
        payload["ok"] = not payload["execute_requested"]
        payload["next_action"] = {"kind": "review_update_target", "mutating": False,
            "requires_explicit_approval": False, "command": None, "reason": reason}
    elif target and source["distribution"] == "runtime":
        recommendation = "Target revision is already installed; apply revalidates it" if current == target else "Prebuilt target resolved; review the installation and activation result"
        payload["recommended_action"] = recommendation
        payload["next_action"]["reason"] = recommendation
    payload["plan"]["distribution"] = source["distribution"]
    return payload
