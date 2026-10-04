"""Read-only Personal Workspace projection for verified periodic-report publications."""

from __future__ import annotations

import heapq
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from ...registry import atomic_write_json, read_json
from ...control_plane.digest_envelope import sha256_envelope
from .incremental import read_periodic_report_publication_cursor
from .incremental import normalize_periodic_report_publication_cursor


PROJECTION_SCHEMA = "periodic_report_workspace_projection_v0"
INDEX_SCHEMA = "periodic_report_workspace_index_v0"
DEFAULT_WORKSPACE_INDEX_LIMIT = 100
MAX_WORKSPACE_INDEX_LIMIT = 200
MAX_WORKSPACE_INDEX_OFFSET = 10_000
_PROJECTION_FILENAME = "workspace-projection.json"


def _delivered_at_order(value: object) -> datetime:
    """Compare normalized UTC timestamps chronologically, not lexically."""

    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_envelope(encoded)


def _text(value: object, label: str, *, maximum: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return text


def build_periodic_report_workspace_projection(
    *,
    goal_id: str,
    agent_id: str,
    generation_id: str,
    document: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Freeze the compact, typed report delta used by the local dashboard."""

    items: list[dict[str, Any]] = []
    seen_source_refs: set[str] = set()
    for fact in facts:
        source_ref = _text(fact.get("source_ref"), "fact.source_ref", maximum=500)
        if source_ref in seen_source_refs:
            raise ValueError("workspace facts contain duplicate source_ref values")
        seen_source_refs.add(source_ref)
        status = _text(fact.get("status"), "fact.status", maximum=80)
        change_kind = str(fact.get("change_kind") or "added").strip()
        if change_kind not in {"added", "changed"}:
            raise ValueError("fact.change_kind must be added or changed")
        item = {
            "fact_id": _text(fact.get("fact_id"), "fact.fact_id", maximum=128),
            "source_ref": source_ref,
            "title": _text(fact.get("title"), "fact.title", maximum=500),
            "summary": _text(fact.get("summary"), "fact.summary", maximum=1000),
            "status": status,
            "content_kind": _text(
                fact.get("content_kind")
                or ("next_action" if status == "open" else "outcome"),
                "fact.content_kind",
                maximum=80,
            ),
            "change_kind": change_kind,
        }
        previous_status = str(fact.get("previous_status") or "").strip()
        if previous_status:
            item["previous_status"] = _text(
                previous_status, "fact.previous_status", maximum=80
            )
        items.append(item)
    if not items:
        raise ValueError("workspace projection requires at least one item")

    editorial = document.get("editorial")
    if not isinstance(editorial, Mapping):
        raise ValueError("document.editorial is required")
    projection: dict[str, Any] = {
        "schema_version": PROJECTION_SCHEMA,
        "goal_id": _text(goal_id, "goal_id", maximum=160),
        "agent_id": _text(agent_id, "agent_id", maximum=160),
        "generation_id": _text(generation_id, "generation_id", maximum=160),
        "generated_at": _text(
            document.get("generated_at"), "document.generated_at", maximum=80
        ),
        "title": _text(document.get("title"), "document.title", maximum=200),
        "summary": _text(editorial.get("summary"), "editorial.summary", maximum=600),
        "period_window": dict(document.get("period_window") or {}),
        "interaction": {
            "attention_kind": "progress",
            "interaction": "inform",
            "delivery": "surface",
            "form": "milestone_report",
            "writable": False,
        },
        "delta": {
            "added_count": sum(item["change_kind"] == "added" for item in items),
            "changed_count": sum(item["change_kind"] == "changed" for item in items),
            "item_count": len(items),
            "items": items,
        },
        "truth_contract": {
            "published_cursor_is_source_of_truth": True,
            "generation_receipt_is_delivery_receipt": False,
            "projection_is_writable": False,
            "browser_write_api": False,
        },
    }
    projection["content_sha256"] = _canonical_digest(projection)
    return projection


def write_periodic_report_workspace_projection(
    *, path: Path, projection: Mapping[str, Any]
) -> None:
    normalized = normalize_periodic_report_workspace_projection(projection)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, normalized)


def normalize_periodic_report_workspace_projection(
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    if raw.get("schema_version") != PROJECTION_SCHEMA:
        raise ValueError(f"workspace projection must use {PROJECTION_SCHEMA}")
    supplied = dict(raw)
    content_sha256 = str(supplied.pop("content_sha256", ""))
    if content_sha256 != _canonical_digest(supplied):
        raise ValueError("workspace projection content_sha256 does not match contents")
    for field, maximum in (
        ("goal_id", 160),
        ("agent_id", 160),
        ("generation_id", 160),
        ("generated_at", 80),
        ("title", 200),
        ("summary", 600),
    ):
        _text(supplied.get(field), f"workspace_projection.{field}", maximum=maximum)
    interaction = supplied.get("interaction")
    if interaction != {
        "attention_kind": "progress",
        "interaction": "inform",
        "delivery": "surface",
        "form": "milestone_report",
        "writable": False,
    }:
        raise ValueError("workspace projection interaction contract is invalid")
    truth = supplied.get("truth_contract")
    if truth != {
        "published_cursor_is_source_of_truth": True,
        "generation_receipt_is_delivery_receipt": False,
        "projection_is_writable": False,
        "browser_write_api": False,
    }:
        raise ValueError("workspace projection truth contract is invalid")
    delta = supplied.get("delta")
    if not isinstance(delta, Mapping) or not isinstance(delta.get("items"), list):
        raise ValueError("workspace projection delta is invalid")
    added = sum(item.get("change_kind") == "added" for item in delta["items"])
    changed = sum(item.get("change_kind") == "changed" for item in delta["items"])
    if (
        not delta["items"]
        or delta.get("item_count") != len(delta["items"])
        or delta.get("added_count") != added
        or delta.get("changed_count") != changed
    ):
        raise ValueError("workspace projection delta counts do not match items")
    supplied["content_sha256"] = content_sha256
    return supplied


def _projection_matches(
    *, path: Path, generation_id: str, content_sha256: str
) -> dict[str, Any] | None:
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise ValueError("periodic-report workspace projection must be an object")
    if (
        value.get("generation_id") != generation_id
        or value.get("content_sha256") != content_sha256
    ):
        return None
    return normalize_periodic_report_workspace_projection(value)


def read_published_periodic_report_workspace_projection(
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    generation_id: str,
    content_sha256: str,
) -> dict[str, Any]:
    """Read only the projection named by the current verified publication cursor."""

    cursor = read_periodic_report_publication_cursor(
        runtime_root=runtime_root, goal_id=goal_id, agent_id=agent_id
    )
    if cursor is None or any(
        cursor.get(key) != expected
        for key, expected in (
            ("generation_id", generation_id),
            ("workspace_projection_sha256", content_sha256),
        )
    ):
        raise ValueError("workspace projection ref is not the current publication")
    root = runtime_root.expanduser().resolve() / "goals" / goal_id / "periodic_reports"
    matches = [
        projection
        for path in root.glob(f"**/{_PROJECTION_FILENAME}")
        if (
            projection := _projection_matches(
                path=path,
                generation_id=generation_id,
                content_sha256=content_sha256,
            )
        )
        is not None
    ]
    if len(matches) != 1:
        raise ValueError("workspace projection ref does not resolve uniquely")
    return {
        **matches[0],
        "publication": {
            "publication_id": cursor["publication_id"],
            "delivered_at": cursor["delivered_at"],
            "predecessor_publication_id": cursor.get("predecessor_publication_id"),
            "cursor_id": cursor["cursor_id"],
        },
    }


def collect_periodic_report_workspace_index(
    *,
    runtime_root: Path,
    goal_id: str | None = None,
    limit: int = DEFAULT_WORKSPACE_INDEX_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Project a bounded latest-published report index without report prose."""

    normalized_limit = min(max(0, int(limit)), MAX_WORKSPACE_INDEX_LIMIT)
    normalized_offset = min(max(0, int(offset)), MAX_WORKSPACE_INDEX_OFFSET)
    root = runtime_root.expanduser().resolve() / "goals"
    total_count = 0
    selected: list[tuple[datetime, str, dict[str, Any]]] = []
    selection_size = normalized_offset + normalized_limit
    pattern = (
        f"{goal_id}/periodic_reports/publication-cursors/*.json"
        if goal_id
        else "*/periodic_reports/publication-cursors/*.json"
    )
    for path in sorted(root.glob(pattern)):
        value = read_json(path)
        if not isinstance(value, Mapping):
            continue
        cursor = normalize_periodic_report_publication_cursor(value)
        digest = str(cursor.get("workspace_projection_sha256") or "")
        if not digest:
            continue
        total_count += 1
        item = {
            "goal_id": cursor["goal_id"],
            "agent_id": cursor["agent_id"],
            "generation_id": cursor["generation_id"],
            "publication_id": cursor["publication_id"],
            "delivered_at": cursor["delivered_at"],
            "predecessor_publication_id": cursor.get("predecessor_publication_id"),
            "detail_ref": {
                "goal_id": cursor["goal_id"],
                "agent_id": cursor["agent_id"],
                "generation_id": cursor["generation_id"],
                "content_sha256": digest,
            },
        }
        if selection_size:
            candidate = (
                _delivered_at_order(item["delivered_at"]),
                str(path),
                item,
            )
            if len(selected) < selection_size:
                heapq.heappush(selected, candidate)
            elif candidate[:2] > selected[0][:2]:
                heapq.heapreplace(selected, candidate)
    selected.sort(key=lambda candidate: candidate[:2], reverse=True)
    items = [
        item
        for _, _, item in selected[
            normalized_offset : normalized_offset + normalized_limit
        ]
    ]
    return {
        "schema_version": INDEX_SCHEMA,
        "count": len(items),
        "returned_count": len(items),
        "total_count": total_count,
        "limit": normalized_limit,
        "offset": normalized_offset,
        "truncated": normalized_offset + len(items) < total_count,
        "items": items,
    }


__all__ = [
    "INDEX_SCHEMA",
    "PROJECTION_SCHEMA",
    "DEFAULT_WORKSPACE_INDEX_LIMIT",
    "MAX_WORKSPACE_INDEX_LIMIT",
    "MAX_WORKSPACE_INDEX_OFFSET",
    "build_periodic_report_workspace_projection",
    "collect_periodic_report_workspace_index",
    "normalize_periodic_report_workspace_projection",
    "read_published_periodic_report_workspace_projection",
    "write_periodic_report_workspace_projection",
]
