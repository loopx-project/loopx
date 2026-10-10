"""Local/private reference artifacts; semantics live in the shared TS owner."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...control_plane.effect_runtime import EffectRuntimeRejected, EffectRuntimeStartupError, effect_runtime_result
from ..material_lifecycle.inventory import build_material_store_inventory


def content_reference_operation(operation: str, request: Mapping[str, Any]) -> dict[str, Any]:
    if operation not in {"search", "capture", "draft"}:
        raise ValueError("unsupported content reference operation")
    try:
        result = effect_runtime_result(f"content_reference.{operation}", request)
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    except EffectRuntimeStartupError as exc:
        raise ValueError("content reference runtime unavailable; run loopx doctor before retrying") from exc
    if not isinstance(result, dict):
        raise TypeError("content reference runtime must return an object")
    return result


def inspect_reference_materials(*, library_path: Path, goal_id: str, store_id: str, observed_at: str) -> dict[str, Any]:
    """Inventory the original bytes; never infer lifecycle state or claim a backup."""
    source = library_path.read_bytes()
    library = json.loads(source)
    projection = content_reference_operation("search", {"library": library})
    entries = library["entries"]
    counts = Counter(entry["lifecycle_state"] for entry in entries if entry.get("lifecycle_state") is not None)
    # Unknown legacy state remains outside the typed owner's known-state counts.
    digest = hashlib.sha256(source).hexdigest()
    inventory = build_material_store_inventory(
        goal_id=goal_id, store_id=store_id, store_revision=f"sha256:{digest}",
        observed_at=observed_at, source_snapshot_ref=f"sha256:{digest}", backup_ref="backup:unverified",
        source_digest=f"sha256:{digest}", lifecycle_counts=counts,
        stable_ids_verified=True, backup_verified=False,
    )
    return {"ok": True, "schema_version": "content_ops_reference_material_inspection_v0", "visibility": "local_private",
            "inventory": inventory, "legacy_unknown_lifecycle_count": projection["unknown_lifecycle_count"],
            "source_item_count": len(entries), "store_write_performed": False,
            "apply_available": False,
            "reason": "Read-only original catalog. A qualified material-lifecycle source provider and current write authority are required for apply/rollback."}


def write_reference_artifact(path: Path, payload: Mapping[str, Any]) -> None:
    """Create a separate private artifact, refusing canonical catalog overwrite."""
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    # Explicit output only; owner-local permissions and exclusive creation.
    import os
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)


def reference_configuration_descriptor() -> dict[str, Any]:
    """Discover a native operation in the existing workbench, without a switch."""
    return {"feature_id": "content_ops", "display_name": "Content references",
            "availability": "local_artifact_preview",
            "effect": "Import an existing local catalog, retrieve styles and prepare an attributed outline. Source/store writes remain unavailable.",
            "documentation": {"path": "loopx/capabilities/content_ops/README.md"}}
