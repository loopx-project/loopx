from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...public_safe_text import PUBLIC_SAFE_SLUG_PATTERN
from ..periodic_report.adapters import (
    PeriodicReportSourceAdapter,
    build_periodic_report_source_result,
)
from .outcome_projection import (
    ISSUE_FIX_OUTCOME_COLLECTION_PROJECTION_SCHEMA_VERSION,
    ISSUE_FIX_OUTCOME_PROJECTION_SCHEMA_VERSION,
)


_PRIORITY_RANK = {"P0": 0, "P1": 100, "P2": 200, "P3": 300}


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_text(value: object, *, maximum: int) -> str:
    return (public_safe_compact_text(value, limit=maximum) or "").strip()


def _item_id(outcome_id: object, *, suffix: str = "case") -> str:
    digest = hashlib.sha256(str(outcome_id or "unknown").encode("utf-8")).hexdigest()
    return f"issue_{digest[:20]}_{suffix}"


def _value_rank(outcome: Mapping[str, Any]) -> int:
    explicit = outcome.get("value_rank")
    if isinstance(explicit, int) and not isinstance(explicit, bool):
        return min(10000, max(0, explicit))
    priority = str(outcome.get("priority") or "P3").upper()
    base = _PRIORITY_RANK.get(priority, 400)
    result = _mapping(outcome.get("result"))
    result_kind = str(result.get("kind") or "").lower()
    stage = str(outcome.get("stage") or "").lower()
    if result_kind in {"merged", "completed", "fixed"} or stage in {
        "merged",
        "completed",
    }:
        return base
    if result_kind in {"blocked", "failed"} or "block" in stage:
        return base + 10
    return base + 20


def _tags(outcome: Mapping[str, Any]) -> list[str]:
    values = [outcome.get("route"), outcome.get("stage"), outcome.get("priority")]
    tags: list[str] = []
    for value in values:
        candidate = str(value or "").strip().lower().replace("_", "-")
        if PUBLIC_SAFE_SLUG_PATTERN.fullmatch(candidate):
            tags.append(candidate)
    return sorted(set(tags))


def _source_ref(outcome: Mapping[str, Any]) -> str | None:
    pull_request = _mapping(outcome.get("pull_request"))
    issue = _mapping(outcome.get("issue"))
    for value in (pull_request.get("url"), issue.get("url")):
        text = _safe_text(value, maximum=500)
        if text:
            return text
    return None


def _state_section(outcome: Mapping[str, Any]) -> tuple[str, str, int, str]:
    status = str(outcome.get("status") or "").lower()
    stage = str(outcome.get("stage") or "").lower()
    result_kind = str(_mapping(outcome.get("result")).get("kind") or "").lower()
    if status == "done" or result_kind in {"merged", "completed", "fixed"}:
        return "completed", "Completed", 10, "outcome"
    if result_kind in {"blocked", "failed"} or "block" in stage:
        return "risks", "Risks and blockers", 30, "risk"
    return "in_progress", "In progress", 20, "progress"


def build_issue_fix_periodic_report_source(
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize public issue-fix outcomes into reusable report sections."""

    schema = projection.get("schema_version")
    if schema not in {
        ISSUE_FIX_OUTCOME_PROJECTION_SCHEMA_VERSION,
        ISSUE_FIX_OUTCOME_COLLECTION_PROJECTION_SCHEMA_VERSION,
    }:
        raise ValueError("issue-fix report source requires an outcome projection")
    raw_outcomes = projection.get("issue_fix_outcomes")
    if not isinstance(raw_outcomes, Sequence) or isinstance(raw_outcomes, (str, bytes)):
        raise ValueError("issue_fix_outcomes must be a list")

    sections: dict[str, dict[str, Any]] = {}
    for raw_outcome in raw_outcomes:
        if not isinstance(raw_outcome, Mapping):
            raise ValueError("every issue-fix outcome must be an object")
        outcome = dict(raw_outcome)
        outcome_id = _safe_text(outcome.get("outcome_id"), maximum=300)
        title = _safe_text(outcome.get("title") or outcome_id, maximum=240)
        summary = _safe_text(
            outcome.get("summary") or outcome.get("evidence") or "No summary.",
            maximum=1000,
        )
        if not outcome_id or not title or not summary:
            raise ValueError("issue-fix outcomes require public-safe identity and text")
        section_id, section_title, section_order, content_kind = _state_section(outcome)
        section = sections.setdefault(
            section_id,
            {
                "section_id": section_id,
                "title": section_title,
                "order": section_order,
                "items": [],
            },
        )
        item: dict[str, Any] = {
            "item_id": _item_id(outcome_id),
            "title": title,
            "summary": summary,
            "value_rank": _value_rank(outcome),
            "status": _safe_text(
                outcome.get("stage") or outcome.get("status") or "unknown",
                maximum=80,
            ),
            "tags": _tags(outcome),
            "content_kind": content_kind,
        }
        source_ref = _source_ref(outcome)
        next_action = _safe_text(outcome.get("next_action"), maximum=500)
        if source_ref:
            item["source_ref"] = source_ref
        if next_action:
            item["next_action"] = next_action
        section["items"].append(item)
        if next_action:
            actions = sections.setdefault(
                "next_actions",
                {
                    "section_id": "next_actions",
                    "title": "Next actions",
                    "order": 40,
                    "items": [],
                },
            )
            actions["items"].append(
                {
                    "item_id": _item_id(outcome_id, suffix="action"),
                    "title": _safe_text(next_action, maximum=240),
                    "summary": next_action,
                    "value_rank": _value_rank(outcome),
                    "status": "planned",
                    "content_kind": "next_action",
                    "source_ref": source_ref,
                    "tags": _tags(outcome),
                }
            )

    source_counts = _mapping(projection.get("source_counts"))
    warnings = projection.get("warnings")
    warning_count = len(warnings) if isinstance(warnings, Sequence) else 0
    unprojected = int(source_counts.get("unprojected_pr_lifecycle") or 0)
    status = "partial" if warning_count or unprojected else "complete"
    generated_at = _safe_text(projection.get("generated_at"), maximum=80)
    goal_id = _safe_text(projection.get("goal_id") or "goal", maximum=120)
    return build_periodic_report_source_result(
        source_id="issue_fix",
        source_kind="issue_fix_outcomes",
        status=status,
        observed_at=generated_at,
        sections=list(sections.values()),
        snapshot_ref=f"issue-fix-outcomes:{goal_id}:{generated_at}",
        retryable=status == "partial",
    )


def issue_fix_periodic_report_source_adapter() -> PeriodicReportSourceAdapter:
    return PeriodicReportSourceAdapter(
        source_id="issue_fix",
        source_kind="issue_fix_outcomes",
        collect=build_issue_fix_periodic_report_source,
    )
