from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .field_shapes import REPORT_LANGUAGE_PATTERN, SUPPORTING_CONTENT_KINDS
from .adapters import (
    PeriodicReportSourceAdapter,
    build_periodic_report_source_result,
)


PROJECT_PROGRESS_PROJECTION_SCHEMA = "periodic_report_project_progress_projection_v0"
PROJECT_PROGRESS_SOURCE_ID = "project_progress"
PROJECT_PROGRESS_SOURCE_KIND = "validated_project_progress"
PROJECT_PROGRESS_ADAPTER_ID = "project_progress_v0"

_SECTION_BY_CONTENT_KIND = {
    "outcome": ("progress", 10),
    "decision": ("progress", 10),
    "progress": ("progress", 10),
    "capability_change": ("capability_evolution", 20),
    "risk": ("risks", 30),
    "next_action": ("next_actions", 40),
    "runtime": ("supporting_evidence", 50),
    "delivery_receipt": ("supporting_evidence", 50),
    "coverage": ("supporting_evidence", 50),
}
_SECTION_TITLES = {
    "en": {
        "progress": "Progress and outcomes",
        "capability_evolution": "Capability evolution",
        "risks": "Risks and blockers",
        "next_actions": "Next actions",
        "supporting_evidence": "Supporting evidence",
    },
    "zh": {
        "progress": "进展与成果",
        "capability_evolution": "能力演进",
        "risks": "风险与阻塞",
        "next_actions": "下一步",
        "supporting_evidence": "支撑证据",
    },
}
_MAX_PRIMARY_ITEMS = 8
_MAX_SUPPORTING_ITEMS = 16


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _sequence(value: object, label: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a list")
    return list(value)


def _snapshot_ref(projection: Mapping[str, Any], *, language: str) -> str:
    identity = {
        "goal_id": str(projection.get("goal_id") or "project"),
        "observed_at": str(projection.get("observed_at") or ""),
        "language": language,
        "items": projection.get("items"),
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"loopx-progress:{hashlib.sha256(encoded).hexdigest()[:24]}"


def _language(value: object) -> str:
    language = str(value or "en").strip()
    if not REPORT_LANGUAGE_PATTERN.fullmatch(language):
        raise ValueError(
            "project_progress.language must be a BCP-47-like language tag"
        )
    return language.lower()


def _section_title(section_id: str, *, language: str) -> str:
    vocabulary = "zh" if language.lower().startswith("zh") else "en"
    return _SECTION_TITLES[vocabulary][section_id]


def build_project_progress_periodic_report_source(
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize concise, domain-neutral project progress into report sections."""

    packet = _mapping(projection, "project_progress")
    if packet.get("schema_version") != PROJECT_PROGRESS_PROJECTION_SCHEMA:
        raise ValueError(
            f"project_progress must use {PROJECT_PROGRESS_PROJECTION_SCHEMA}"
        )
    language = _language(packet.get("language"))
    raw_items = _sequence(packet.get("items"), "project_progress.items")
    if not raw_items:
        raise ValueError("project_progress.items must not be empty")

    sections: dict[str, dict[str, Any]] = {}
    seen_item_ids: set[str] = set()
    primary_count = 0
    supporting_count = 0
    for index, raw_item in enumerate(raw_items):
        label = f"project_progress.items[{index}]"
        item = _mapping(raw_item, label)
        item_id = str(item.get("item_id") or "").strip().lower()
        if not item_id:
            raise ValueError(f"{label}.item_id is required")
        if item_id in seen_item_ids:
            raise ValueError(f"duplicate project progress item_id {item_id!r}")
        seen_item_ids.add(item_id)

        content_kind = str(item.get("content_kind") or "").strip().lower()
        section_spec = _SECTION_BY_CONTENT_KIND.get(content_kind)
        if section_spec is None:
            raise ValueError(f"{label}.content_kind is invalid")
        visibility = str(item.get("visibility") or "primary").strip().lower()
        if content_kind in SUPPORTING_CONTENT_KINDS:
            supporting_count += 1
        elif visibility == "supporting":
            supporting_count += 1
        else:
            primary_count += 1

        section_id, order = section_spec
        section = sections.setdefault(
            section_id,
            {
                "section_id": section_id,
                "title": _section_title(section_id, language=language),
                "order": order,
                "items": [],
            },
        )
        section["items"].append(item)

    if primary_count > _MAX_PRIMARY_ITEMS:
        raise ValueError(
            f"project_progress contains {primary_count} primary items; "
            f"summarize to at most {_MAX_PRIMARY_ITEMS}"
        )
    if supporting_count > _MAX_SUPPORTING_ITEMS:
        raise ValueError(
            f"project_progress contains {supporting_count} supporting items; "
            f"keep at most {_MAX_SUPPORTING_ITEMS}"
        )

    warnings = _sequence(packet.get("warnings", []), "project_progress.warnings")
    retryable = packet.get("retryable", bool(warnings))
    if not isinstance(retryable, bool):
        raise ValueError("project_progress.retryable must be a boolean")
    status = str(packet.get("status") or ("partial" if warnings else "complete"))
    return build_periodic_report_source_result(
        source_id=PROJECT_PROGRESS_SOURCE_ID,
        source_kind=PROJECT_PROGRESS_SOURCE_KIND,
        status=status,
        observed_at=str(packet.get("observed_at") or ""),
        sections=list(sections.values()),
        snapshot_ref=_snapshot_ref(packet, language=language),
        retryable=retryable,
    )


def project_progress_periodic_report_source_adapter() -> PeriodicReportSourceAdapter:
    return PeriodicReportSourceAdapter(
        source_id=PROJECT_PROGRESS_SOURCE_ID,
        source_kind=PROJECT_PROGRESS_SOURCE_KIND,
        collect=build_project_progress_periodic_report_source,
    )
