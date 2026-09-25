"""Read-only primary guard for the TypeScript-owned shadow management journal.

Call under the writer's primary lock. This module never creates a binding,
repairs a journal, or consults the candidate provider during a primary write.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from .coordination_state_contract_generated import (
    SHADOW_MANAGEMENT_MANIFEST_SCHEMA, SHADOW_MANAGEMENT_STATE_SCHEMA,
)
from .local_authority_shadow_projection import sha256_digest

SHADOW_CAPTURE_PROFILE = "file_outbox_v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_STATE_KEYS = {
    "schema_version", "goal_id", "source_root_digest", "status", "binding",
    "operation", "previous_operation_id", "result",
}
_BINDING_KEYS = {
    "capture_profile", "capture_lineage_id", "source_root_digest", "store_identity",
    "bootstrap_operation_id", "bootstrap_provider_revision",
}
_OPERATION_KEYS = {"kind", "operation_id", "request_digest", "manifest_digest", "phase"}


class ShadowManagementError(RuntimeError):
    """A typed management hold; callers must not swallow it as capture failure."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.reason_code = code
        self.payload: dict[str, Any] = {"status": "blocked", "reason_code": code}


def shadow_management_directory(runtime_root: Path, goal_id: str) -> Path:
    digest = hashlib.sha256(goal_id.encode("utf-8")).hexdigest()[:16]
    return runtime_root / "authority-transition" / "file-v0" / f"shadow-management-{digest}"


def shadow_maintenance_lock_target(runtime_root: Path, goal_id: str) -> Path:
    return shadow_management_directory(runtime_root, goal_id) / "maintenance"


def shadow_management_state_path(runtime_root: Path, goal_id: str) -> Path:
    return shadow_management_directory(runtime_root, goal_id) / "state.json"


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value.strip() == value


def _source_root_digests(runtime_root: Path) -> set[str]:
    roots = {
        os.path.abspath(str(runtime_root)),
        os.path.realpath(str(runtime_root)),
    }
    return {
        "sha256:" + hashlib.sha256(root.encode()).hexdigest()
        for root in roots
    }


def _binding(value: object, root_digest: str) -> bool:
    return (
        isinstance(value, dict) and set(value) == _BINDING_KEYS
        and all(_text(item) for item in value.values())
        and value["capture_profile"] == SHADOW_CAPTURE_PROFILE
        and value["source_root_digest"] == root_digest
        and re.fullmatch(r"file:[0-9a-f]{32}", value["store_identity"]) is not None
        and re.fullmatch(r"file:[1-9][0-9]*:[0-9a-f]{24}", value["bootstrap_provider_revision"]) is not None
    )


def read_shadow_management_state(runtime_root: Path, goal_id: str) -> dict[str, Any] | None:
    """Validate the closed local journal shape without performing any writes."""
    try:
        raw = shadow_management_state_path(runtime_root, goal_id).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeError as exc:
        raise ShadowManagementError("shadow_management_state_invalid") from exc
    except OSError as exc:
        raise ShadowManagementError("shadow_management_state_unavailable") from exc
    root_digests = _source_root_digests(runtime_root)
    try:
        state = json.loads(raw)
        if not isinstance(state, dict) or set(state) != _STATE_KEYS:
            raise ValueError("journal fields differ")
        root_digest = state["source_root_digest"]
        if (state["schema_version"] != SHADOW_MANAGEMENT_STATE_SCHEMA
                or state["goal_id"] != goal_id or root_digest not in root_digests):
            raise ValueError("journal scope differs")
        status = state["status"]
        if status not in {"bootstrapping", "active", "rolling_back", "inactive"}:
            raise ValueError("journal status is invalid")
        operation = state["operation"]
        if not isinstance(operation, dict) or set(operation) != _OPERATION_KEYS:
            raise ValueError("journal operation is invalid")
        if (not _text(operation["operation_id"])
                or not isinstance(operation["request_digest"], str)
                or not _DIGEST.fullmatch(operation["request_digest"])
                or not isinstance(operation["manifest_digest"], str)
                or not _DIGEST.fullmatch(operation["manifest_digest"])):
            raise ValueError("journal operation identity is invalid")
        kind = "bootstrap" if status in {"bootstrapping", "active"} else "rollback"
        phases = {"prepared", "candidate_committed", "outbox_ready"} if kind == "bootstrap" else {
            "prepared", "candidate_archived", "outbox_archived",
        }
        terminal = status in {"active", "inactive"}
        if operation["kind"] != kind or operation["phase"] not in ({"complete"} if terminal else phases):
            raise ValueError("journal phase is invalid")
        if state["previous_operation_id"] is not None and not _text(state["previous_operation_id"]):
            raise ValueError("journal predecessor is invalid")
        if terminal != isinstance(state["result"], dict):
            raise ValueError("journal result is invalid")
        if not terminal and state["result"] is not None:
            raise ValueError("journal result is premature")
        if state["binding"] is not None and not _binding(state["binding"], root_digest):
            raise ValueError("journal binding is invalid")
        if status == "active" and state["binding"] is None:
            raise ValueError("active journal has no binding")
        if status == "inactive" and state["binding"] is not None:
            raise ValueError("inactive journal has a binding")
        return state
    except (ValueError, TypeError, KeyError) as exc:
        raise ShadowManagementError("shadow_management_state_invalid") from exc


def require_shadow_primary_write_allowed(runtime_root: Path, goal_id: str) -> dict[str, Any] | None:
    state = read_shadow_management_state(runtime_root, goal_id)
    if state is None or state["status"] == "inactive":
        return None
    if state["status"] != "active":
        raise ShadowManagementError("shadow_management_in_progress")
    return dict(state["binding"])


def read_shadow_bootstrap_source_path(
    runtime_root: Path, goal_id: str, binding: dict[str, Any],
) -> Path:
    """Read this lineage's immutable source path under the caller's source lock.

    This does not acquire maintenance, consult the provider, or repair files.
    Source contents can evolve; the bootstrap path remains the writer boundary.
    """

    state = read_shadow_management_state(runtime_root, goal_id)
    if state is None or state["status"] == "inactive":
        raise ShadowManagementError("bootstrap_required")
    if state["status"] != "active":
        raise ShadowManagementError("shadow_management_in_progress")
    if (state["binding"] != binding
            or state["operation"]["operation_id"] != binding["bootstrap_operation_id"]):
        raise ShadowManagementError("stale_generation")
    operation_id = binding["bootstrap_operation_id"]
    directory = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    path = shadow_management_directory(runtime_root, goal_id) / "operations" / directory / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict)
                or sha256_digest(manifest) != state["operation"]["manifest_digest"]
                or manifest.get("schema_version") != SHADOW_MANAGEMENT_MANIFEST_SCHEMA
                or manifest.get("kind") != "bootstrap" or manifest.get("goal_id") != goal_id
                or manifest.get("operation_id") != operation_id
                or manifest.get("capture_lineage_id") != binding["capture_lineage_id"]
                or manifest.get("source_root_digest") != binding["source_root_digest"]
                or manifest.get("request_digest") != state["operation"]["request_digest"]):
            raise ValueError("bootstrap manifest binding differs")
        request = manifest.get("request")
        if (not isinstance(request, dict) or request.get("runtime_root") != str(runtime_root)
                or request.get("goal_id") != goal_id or request.get("operation_id") != operation_id
                or sha256_digest(request) != manifest["request_digest"]):
            raise ValueError("bootstrap request binding differs")
        snapshot = request.get("source_snapshot")
        source = snapshot.get("state_path") if isinstance(snapshot, dict) else None
        if not isinstance(source, str) or not _text(source) or "\0" in source or not Path(source).is_absolute():
            raise ValueError("bootstrap source path is invalid")
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise ShadowManagementError("shadow_management_manifest_invalid") from exc
    if read_shadow_management_state(runtime_root, goal_id) != state:
        raise ShadowManagementError("stale_generation")
    return Path(source)


def read_shadow_capture_binding(runtime_root: Path, goal_id: str) -> dict[str, Any]:
    """Observational capture status; primary guards use the throwing API above."""
    try:
        state = read_shadow_management_state(runtime_root, goal_id)
    except ShadowManagementError as error:
        return {"status": "hold", "reason_code": error.code}
    if state is None:
        return {"status": "missing", "reason_code": "bootstrap_required"}
    if state["status"] == "inactive":
        return {"status": "inactive", "reason_code": "bootstrap_required"}
    if state["status"] != "active":
        return {"status": "hold", "reason_code": "shadow_management_in_progress"}
    return {"status": "active", "binding": dict(state["binding"])}
