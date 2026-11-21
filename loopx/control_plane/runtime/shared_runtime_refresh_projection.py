from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..goals.goal_vision import compact_goal_vision_packet
from .runtime_projection_writer import write_compact_runtime_projection


SHARED_RUNTIME_PROJECTION_SCHEMA_VERSION = "shared_runtime_refresh_projection_v0"
ISO_LIKE_TIMESTAMP_RE = re.compile(r"[0-9TZ:+.\-]{10,64}")


def build_shared_runtime_projection(
    *,
    record: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the compact allowlisted record consumed by shared status/quota."""

    state = record.get("state") if isinstance(record.get("state"), dict) else {}
    frontmatter = (
        state.get("frontmatter") if isinstance(state.get("frontmatter"), dict) else {}
    )
    updated_at = str(frontmatter.get("updated_at") or "").strip()
    if not ISO_LIKE_TIMESTAMP_RE.fullmatch(updated_at):
        updated_at = ""
    marker = {
        "schema_version": SHARED_RUNTIME_PROJECTION_SCHEMA_VERSION,
        "source": "refresh_state",
        "source_generated_at": record.get("generated_at"),
        "raw_artifacts_copied": False,
        "recommended_action_copied": False,
    }
    route_marker = (
        record.get("runtime_projection_route")
        if isinstance(record.get("runtime_projection_route"), dict)
        else {}
    )
    if route_marker.get("route_id"):
        marker["runtime_projection_route_id"] = route_marker["route_id"]
    projection: dict[str, Any] = {
        "generated_at": record.get("generated_at"),
        "goal_id": record.get("goal_id"),
        "classification": record.get("classification"),
        "health_check": "project-local refresh projected to registered shared runtime",
        "state": {
            "sha256_16": state.get("sha256_16"),
            "frontmatter": {"updated_at": updated_at or None},
        },
        "shared_runtime_projection": marker,
    }
    compact_vision = compact_goal_vision_packet(record.get("agent_vision"))
    if compact_vision:
        projection["agent_vision"] = compact_vision
    for field in (
        "delivery_batch_scale",
        "delivery_outcome",
        "progress_scope",
        "agent_id",
        "agent_lane",
        "vision_checkpoint",
    ):
        if field in record:
            projection[field] = record[field]

    ack = (
        record.get("autonomous_replan_ack")
        if isinstance(record.get("autonomous_replan_ack"), dict)
        else {}
    )
    if ack:
        compact_ack = {
            field: ack[field]
            for field in ("schema_version", "recorded", "source", "requested")
            if field in ack
        }
        delta = ack.get("delta_contract") if isinstance(ack.get("delta_contract"), dict) else {}
        if delta:
            compact_ack["delta_contract"] = {
                field: delta[field]
                for field in (
                    "schema_version",
                    "required",
                    "delta_present",
                    "delta_kinds",
                    "accepted_without_delta",
                )
                if field in delta
            }
        projection["autonomous_replan_ack"] = compact_ack

    marker["source_projection_sha256_16"] = hashlib.sha256(
        json.dumps(projection, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    projected_index = {
        key: projection[key]
        for key in (
            "generated_at",
            "goal_id",
            "classification",
            "health_check",
            "state",
            "delivery_batch_scale",
            "delivery_outcome",
            "progress_scope",
            "agent_id",
            "agent_lane",
            "autonomous_replan_ack",
            "agent_vision",
            "vision_checkpoint",
            "shared_runtime_projection",
        )
        if key in projection
    }
    return projection, projected_index


def _render_projection_markdown(record: dict[str, Any]) -> str:
    marker = record.get("shared_runtime_projection") or {}
    return "\n".join(
        [
            "# LoopX Shared Runtime Refresh Projection",
            "",
            f"- goal_id: `{record.get('goal_id')}`",
            f"- classification: `{record.get('classification')}`",
            f"- generated_at: `{record.get('generated_at')}`",
            f"- agent_id: `{record.get('agent_id')}`",
            f"- raw_artifacts_copied: `{marker.get('raw_artifacts_copied')}`",
            f"- recommended_action_copied: `{marker.get('recommended_action_copied')}`",
            f"- runtime_projection_route_id: `{marker.get('runtime_projection_route_id')}`",
        ]
    )


def write_shared_runtime_projection(
    *,
    shared_runtime_root: Path,
    goal_id: str,
    record: dict[str, Any],
    index_record: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    marker = record["shared_runtime_projection"]
    result = write_compact_runtime_projection(
        target_runtime_root=shared_runtime_root,
        goal_id=goal_id,
        record=record,
        index_record=index_record,
        marker_field="shared_runtime_projection",
        identity_fields=("source_generated_at", "source_projection_sha256_16"),
        markdown_renderer=_render_projection_markdown,
        dry_run=dry_run,
    )
    result["shared_runtime_root"] = str(shared_runtime_root)
    result["raw_artifacts_copied"] = False
    result["recommended_action_copied"] = False
    result["source_generated_at"] = marker.get("source_generated_at")
    return result
