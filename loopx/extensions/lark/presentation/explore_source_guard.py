from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ....control_plane.runtime.runtime_projection_route import (
    compact_goal_source_runtime_route,
    resolve_goal_source_runtime_route,
)


_RESULT_COLLECTIONS = (
    ("nodes", "nodes", "node_id"),
    ("edges", "edges", "edge_id"),
    ("findings", "findings", "finding_id"),
)


def _is_projection_derived_result_key(key: str, *, goal_id: str) -> bool:
    """Return whether a sink row is derived from a mutable display relation.

    ``parent_*`` edges are synthesized from the latest node ``parent_id`` and
    do not exist in the append-only Explore result log. Reparenting or
    detaching a node can therefore retire one without shrinking canonical
    source history. They must not turn the source-history guard into a false
    regression blocker; connector reconciliation may retain or clean up the
    stale display row independently.
    """

    return key.startswith(f"{goal_id}:edges:parent_")


def resolve_explore_source_registry(
    *, registry_path: Path, goal_id: str, runtime_root_override: str | None = None
) -> tuple[Path, dict[str, Any]]:
    route = resolve_goal_source_runtime_route(
        registry_path=registry_path,
        goal_id=goal_id,
        runtime_root_override=runtime_root_override,
    )
    return (
        Path(str(route["source_registry"])),
        compact_goal_source_runtime_route(route),
    )


def _projection_result_keys(
    projection: Mapping[str, Any], *, goal_id: str
) -> set[str]:
    keys: set[str] = set()
    for table_key, collection_key, result_id_key in _RESULT_COLLECTIONS:
        for item in projection.get(collection_key) or []:
            if not isinstance(item, Mapping):
                continue
            result_id = str(item.get(result_id_key) or "").strip()
            if result_id:
                keys.add(f"{goal_id}:{table_key}:{result_id}")
    return keys


def source_projection_coverage_guard(
    *,
    local: Mapping[str, Any],
    projection: Mapping[str, Any],
    goal_id: str,
) -> dict[str, Any]:
    """Fail closed when append-only source results drop registered sink rows."""

    record_map = (
        local.get("result_records")
        if isinstance(local.get("result_records"), Mapping)
        else {}
    )
    all_registered = {
        str(key)
        for key in record_map
        if any(
            str(key).startswith(f"{goal_id}:{table_key}:")
            for table_key, _, _ in _RESULT_COLLECTIONS
        )
    }
    projection_derived_registered = {
        key
        for key in all_registered
        if _is_projection_derived_result_key(key, goal_id=goal_id)
    }
    registered = all_registered - projection_derived_registered
    candidate = _projection_result_keys(projection, goal_id=goal_id)
    missing = sorted(registered - candidate)
    retired_projection_derived = sorted(projection_derived_registered - candidate)
    missing_digest = None
    if missing:
        encoded = json.dumps(missing, ensure_ascii=True, separators=(",", ":"))
        missing_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    return {
        "schema_version": "explore_source_projection_coverage_guard_v0",
        "ok": not missing,
        "status": (
            "no_registered_baseline"
            if not all_registered
            else "covered"
            if not missing and not retired_projection_derived
            else "covered_with_projection_derived_omissions"
            if not missing
            else "registered_results_missing"
        ),
        "candidate_result_count": len(candidate),
        "registered_result_count": len(all_registered),
        "append_only_registered_result_count": len(registered),
        "projection_derived_registered_result_count": len(
            projection_derived_registered
        ),
        "retired_projection_derived_result_count": len(
            retired_projection_derived
        ),
        "missing_result_count": len(missing),
        "missing_result_ids_sha256_16": missing_digest,
        "append_only_contract": True,
    }
