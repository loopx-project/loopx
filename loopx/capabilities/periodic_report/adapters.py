from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .core import (
    _normalize_trigger_receipt,
    _reject_raw_keys,
    _SINK_ROLES,
    _SINK_STATUSES,
    _SOURCE_STATUSES,
)


SOURCE_RESULT_SCHEMA = "periodic_report_source_result_v0"
SECTION_SCHEMA = "periodic_report_section_v0"
DOCUMENT_SCHEMA = "periodic_report_document_v0"
EDITORIAL_ORCHESTRATION_SCHEMA = "periodic_report_editorial_orchestration_v0"
ARTIFACT_SCHEMA = "periodic_report_artifact_v0"
SINK_RESULT_SCHEMA = "periodic_report_sink_result_v0"

_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_ITEM_CONTENT_KINDS = {
    "capability_change",
    "coverage",
    "decision",
    "delivery_receipt",
    "next_action",
    "outcome",
    "progress",
    "risk",
    "runtime",
}
_ITEM_VISIBILITIES = {"primary", "supporting"}
_SUPPORTING_CONTENT_KINDS = {"coverage", "delivery_receipt", "runtime"}
_HIGHLIGHT_TONES = {"attention", "neutral", "positive"}
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
SourceCollector = Callable[[Mapping[str, Any]], Mapping[str, Any]]
ArtifactRenderer = Callable[[Mapping[str, Any]], Mapping[str, Any]]
ArtifactSink = Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _sequence(value: object, label: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a list")
    return list(value)


def _text(value: object, label: str, *, maximum: int = 1000) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return text


def _optional_text(
    value: object,
    label: str,
    *,
    maximum: int = 1000,
) -> str | None:
    if value is None:
        return None
    return _text(value, label, maximum=maximum)


def _token(value: object, label: str) -> str:
    token = _text(value, label, maximum=128).lower()
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError(f"{label} must be a lower-snake-like public token")
    return token


def _integer(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = 1000000,
) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        raise ValueError(f"{label} must be an integer")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{label} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return result


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _timestamp(value: object, label: str) -> str:
    raw = _text(value, label, maximum=80)
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_private_fields(value: object, label: str) -> None:
    _reject_raw_keys(value, label)


def _digest(value: object, *, prefix: str) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _normalize_item(raw: object, *, label: str) -> dict[str, Any]:
    item = _mapping(raw, label)
    normalized: dict[str, Any] = {
        "item_id": _token(item.get("item_id"), f"{label}.item_id"),
        "title": _text(item.get("title"), f"{label}.title", maximum=240),
        "summary": _text(item.get("summary"), f"{label}.summary", maximum=1000),
        "value_rank": _integer(
            item.get("value_rank", 500),
            f"{label}.value_rank",
            maximum=10000,
        ),
    }
    for field, maximum in (
        ("status", 80),
        ("source_ref", 500),
        ("next_action", 500),
    ):
        value = _optional_text(item.get(field), f"{label}.{field}", maximum=maximum)
        if value:
            normalized[field] = value
    tags = sorted(
        {
            _token(value, f"{label}.tags[]")
            for value in _sequence(item.get("tags", []), f"{label}.tags")
        }
    )
    if tags:
        normalized["tags"] = tags
    domains = sorted(
        {
            _token(value, f"{label}.domains[]")
            for value in _sequence(item.get("domains", []), f"{label}.domains")
        }
    )
    if domains:
        normalized["domains"] = domains
    raw_tag_labels = _mapping(item.get("tag_labels", {}), f"{label}.tag_labels")
    tag_labels: dict[str, str] = {}
    for raw_tag, raw_label in raw_tag_labels.items():
        tag = _token(raw_tag, f"{label}.tag_labels key")
        if tag not in tags:
            raise ValueError(f"{label}.tag_labels contains an unknown tag {tag!r}")
        tag_labels[tag] = _text(
            raw_label,
            f"{label}.tag_labels[{tag!r}]",
            maximum=64,
        )
    if tag_labels:
        normalized["tag_labels"] = {
            tag: tag_labels[tag] for tag in sorted(tag_labels)
        }
    raw_details = _sequence(item.get("details", []), f"{label}.details")
    if len(raw_details) > 4:
        raise ValueError(f"{label}.details must contain at most 4 items")
    details: list[dict[str, str]] = []
    for detail_index, raw_detail in enumerate(raw_details):
        detail_label = f"{label}.details[{detail_index}]"
        detail = _mapping(raw_detail, detail_label)
        details.append(
            {
                "label": _text(
                    detail.get("label"), f"{detail_label}.label", maximum=40
                ),
                "text": _text(
                    detail.get("text"), f"{detail_label}.text", maximum=360
                ),
            }
        )
    if details:
        normalized["details"] = details
    content_kind = _optional_text(
        item.get("content_kind"), f"{label}.content_kind", maximum=128
    )
    if content_kind:
        content_kind = _token(content_kind, f"{label}.content_kind")
        if content_kind not in _ITEM_CONTENT_KINDS:
            raise ValueError(f"{label}.content_kind is invalid")
        normalized["content_kind"] = content_kind
    visibility = _optional_text(
        item.get("visibility"), f"{label}.visibility", maximum=128
    )
    if visibility:
        visibility = _token(visibility, f"{label}.visibility")
        if visibility not in _ITEM_VISIBILITIES:
            raise ValueError(f"{label}.visibility is invalid")
        normalized["visibility"] = visibility
    if content_kind in _SUPPORTING_CONTENT_KINDS and visibility != "supporting":
        raise ValueError(
            f"{label}.visibility must be supporting for {content_kind} content"
        )
    effective_visibility = visibility or "primary"
    if effective_visibility == "primary" and len(str(normalized["summary"])) > 360:
        raise ValueError(
            f"{label}.summary exceeds the 360-character primary readability limit; "
            "split supporting facts into details"
        )
    if (
        effective_visibility == "primary"
        and content_kind == "capability_change"
        and len(details) < 2
    ):
        raise ValueError(
            f"{label}.details must contain at least 2 items for primary "
            "capability_change content"
        )
    return normalized


def _normalize_editorial(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    editorial = _mapping(raw, "editorial")
    normalized: dict[str, Any] = {}
    if editorial.get("summary") is not None:
        raise ValueError(
            "editorial.summary is compiled from typed primary items; "
            "do not provide authored process narration"
        )
    for field, maximum in (("kicker", 120), ("period_label", 160)):
        value = _optional_text(
            editorial.get(field), f"editorial.{field}", maximum=maximum
        )
        if value:
            normalized[field] = value
    language = _optional_text(
        editorial.get("language"), "editorial.language", maximum=64
    )
    if language:
        if not _LANGUAGE_RE.fullmatch(language):
            raise ValueError("editorial.language must be a BCP-47-like language tag")
        normalized["language"] = language

    raw_highlights = _sequence(editorial.get("highlights", []), "editorial.highlights")
    if len(raw_highlights) > 4:
        raise ValueError("editorial.highlights must contain at most 4 items")
    highlights: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_highlight in enumerate(raw_highlights):
        label = f"editorial.highlights[{index}]"
        highlight = _mapping(raw_highlight, label)
        highlight_id = _token(highlight.get("highlight_id"), f"{label}.highlight_id")
        if highlight_id in seen_ids:
            raise ValueError(f"duplicate editorial highlight_id {highlight_id!r}")
        seen_ids.add(highlight_id)
        normalized_highlight: dict[str, Any] = {
            "highlight_id": highlight_id,
            "value": _text(highlight.get("value"), f"{label}.value", maximum=48),
            "label": _text(highlight.get("label"), f"{label}.label", maximum=80),
        }
        detail = _optional_text(highlight.get("detail"), f"{label}.detail", maximum=160)
        if detail:
            normalized_highlight["detail"] = detail
        tone = _optional_text(highlight.get("tone"), f"{label}.tone", maximum=128)
        if tone:
            tone = _token(tone, f"{label}.tone")
            if tone not in _HIGHLIGHT_TONES:
                raise ValueError(f"{label}.tone is invalid")
            normalized_highlight["tone"] = tone
        highlights.append(normalized_highlight)
    if highlights:
        normalized["highlights"] = highlights
    if not normalized:
        raise ValueError("editorial must contain audience-facing content")
    return normalized


def _normalize_sections(raw: object, *, label: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    seen_sections: set[str] = set()
    for section_index, raw_section in enumerate(_sequence(raw, label)):
        section_label = f"{label}[{section_index}]"
        section = _mapping(raw_section, section_label)
        section_id = _token(section.get("section_id"), f"{section_label}.section_id")
        if section_id in seen_sections:
            raise ValueError(f"duplicate section_id {section_id!r}")
        seen_sections.add(section_id)
        items = [
            _normalize_item(value, label=f"{section_label}.items[{item_index}]")
            for item_index, value in enumerate(
                _sequence(section.get("items", []), f"{section_label}.items")
            )
        ]
        item_ids = [str(item["item_id"]) for item in items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError(f"{section_label}.items contains duplicate item_id")
        sections.append(
            {
                "schema_version": SECTION_SCHEMA,
                "section_id": section_id,
                "title": _text(
                    section.get("title"), f"{section_label}.title", maximum=160
                ),
                "order": _integer(
                    section.get("order", section_index),
                    f"{section_label}.order",
                    maximum=10000,
                ),
                "items": sorted(
                    items,
                    key=lambda item: (int(item["value_rank"]), str(item["item_id"])),
                ),
            }
        )
    return sorted(sections, key=lambda item: (int(item["order"]), item["section_id"]))


def _summary_excerpt(value: object, *, maximum: int = 120) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= maximum:
        return text.rstrip("。.!?；; ")
    shortened = text[: maximum - 1].rstrip("，,、；;:：。.!? ")
    return shortened + "…"


def _ordered_primary_items(
    sections: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    ordered_sections = sorted(
        (_mapping(section, "sections[]") for section in sections),
        key=lambda section: (
            int(section.get("order") or 0),
            str(section.get("section_id") or ""),
        ),
    )
    items: list[tuple[int, str, dict[str, Any]]] = []
    for section in ordered_sections:
        for raw_item in _sequence(section.get("items", []), "sections[].items"):
            item = _mapping(raw_item, "sections[].items[]")
            if str(item.get("visibility") or "primary") == "primary":
                items.append(
                    (
                        int(section.get("order") or 0),
                        str(section.get("section_id") or ""),
                        item,
                    )
                )
    return [
        item
        for _section_order, _section_id, item in sorted(
            items,
            key=lambda entry: (
                int(entry[2].get("value_rank") or 500),
                entry[0],
                entry[1],
                str(entry[2].get("item_id") or ""),
            ),
        )
    ]


def _derive_editorial_summary(
    sections: Sequence[Mapping[str, Any]],
    *,
    language: object,
) -> tuple[str | None, list[dict[str, str]]]:
    items = _ordered_primary_items(sections)

    def first_of(*kinds: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in items
                if str(item.get("content_kind") or "") in kinds
            ),
            None,
        )

    outcome = first_of("outcome", "decision")
    risk = first_of("risk")
    next_item = first_of("next_action")
    parts: list[tuple[str, dict[str, Any], str, str]] = []
    if outcome:
        role = str(outcome.get("content_kind") or "outcome")
        parts.append((role, outcome, "title", str(outcome.get("title") or "")))
    if risk:
        parts.append(("risk", risk, "title", str(risk.get("title") or "")))
    if next_item:
        parts.append(
            (
                "next_action",
                next_item,
                "title",
                str(next_item.get("title") or ""),
            )
        )
    elif risk and risk.get("next_action"):
        parts.append(
            ("next_action", risk, "next_action", str(risk["next_action"]))
        )
    elif outcome and outcome.get("next_action"):
        parts.append(
            ("next_action", outcome, "next_action", str(outcome["next_action"]))
        )

    parts = [part for part in parts if _summary_excerpt(part[3])]
    if not parts:
        return None, []
    chinese = str(language or "").lower().startswith("zh")
    labels = (
        {
            "outcome": "本期进展",
            "decision": "本期结论",
            "risk": "当前风险",
            "next_action": "下一步",
        }
        if chinese
        else {
            "outcome": "Outcome",
            "decision": "Decision",
            "risk": "Risk",
            "next_action": "Next",
        }
    )
    ending = "。" if chinese else "."
    sentences = [
        f"{labels[role]}：{_summary_excerpt(text)}{ending}"
        if chinese
        else f"{labels[role]}: {_summary_excerpt(text)}{ending}"
        for role, _item, _field, text in parts
    ]
    refs = [
        {
            "role": role,
            "source_id": str(item.get("source_id") or "unknown"),
            "item_id": str(item.get("item_id") or "unknown"),
            "field": field,
        }
        for role, item, field, _text_value in parts
    ]
    return " ".join(sentences), refs


def build_periodic_report_editorial(
    *,
    sections: Sequence[Mapping[str, Any]],
    editorial: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Compile audience editorial copy from typed primary report items."""

    normalized = _normalize_editorial(editorial) if editorial is not None else None
    compiled: dict[str, Any] = dict(normalized or {})
    summary, refs = _derive_editorial_summary(
        sections,
        language=compiled.get("language"),
    )
    if summary:
        compiled["summary"] = _text(summary, "editorial.summary", maximum=600)
        compiled["orchestration"] = {
            "schema_version": EDITORIAL_ORCHESTRATION_SCHEMA,
            "summary_source": "typed_primary_items",
            "readability_policy": "audience_v1",
            "summary_item_refs": refs,
        }
    return compiled or None


def validate_periodic_report_editorial(document: Mapping[str, Any]) -> None:
    """Require renderer input to carry the canonical compiled editorial summary."""

    raw_editorial = document.get("editorial")
    actual = dict(raw_editorial) if isinstance(raw_editorial, Mapping) else {}
    profile_fields = {
        key: actual[key]
        for key in ("kicker", "period_label", "language", "highlights")
        if key in actual
    }
    expected = build_periodic_report_editorial(
        sections=[
            _mapping(section, "document.sections[]")
            for section in _sequence(document.get("sections", []), "document.sections")
        ],
        editorial=profile_fields or None,
    ) or {}
    if actual.get("summary") != expected.get("summary"):
        raise ValueError(
            "document.editorial.summary must be compiled from typed primary items"
        )
    if actual.get("orchestration") != expected.get("orchestration"):
        raise ValueError(
            "document.editorial.orchestration does not match the compiled summary"
        )


def build_periodic_report_source_result(
    *,
    source_id: str,
    source_kind: str,
    status: str,
    observed_at: str,
    sections: Sequence[Mapping[str, Any]],
    snapshot_ref: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    """Build one public-safe source snapshot and its report sections."""

    normalized_status = _token(status, "status")
    if normalized_status not in _SOURCE_STATUSES:
        raise ValueError("status is invalid")
    raw_sections = list(sections)
    _reject_private_fields(raw_sections, "sections")
    normalized_sections = _normalize_sections(raw_sections, label="sections")
    result: dict[str, Any] = {
        "schema_version": SOURCE_RESULT_SCHEMA,
        "source_id": _token(source_id, "source_id"),
        "source_kind": _token(source_kind, "source_kind"),
        "status": normalized_status,
        "observed_at": _timestamp(observed_at, "observed_at"),
        "snapshot_digest": _digest(normalized_sections, prefix="snapshot"),
        "item_count": sum(len(section["items"]) for section in normalized_sections),
        "retryable": _boolean(retryable, "retryable"),
        "sections": normalized_sections,
        "boundary": {
            "business_semantics_owned_by_source": True,
            "schedule_policy_owned_by_source": False,
            "external_reads_performed": False,
            "external_writes_performed": False,
            "raw_content_persisted": False,
        },
    }
    if snapshot_ref:
        result["snapshot_ref"] = _text(snapshot_ref, "snapshot_ref", maximum=500)
    _reject_private_fields(result, "source_result")
    return result


def normalize_periodic_report_source_result(
    raw: Mapping[str, Any],
    *,
    expected_source_id: str | None = None,
    expected_source_kind: str | None = None,
) -> dict[str, Any]:
    payload = _mapping(raw, "source_result")
    _reject_private_fields(payload, "source_result")
    if payload.get("schema_version") != SOURCE_RESULT_SCHEMA:
        raise ValueError(f"source_result must use {SOURCE_RESULT_SCHEMA}")
    normalized = build_periodic_report_source_result(
        source_id=str(payload.get("source_id") or ""),
        source_kind=str(payload.get("source_kind") or ""),
        status=str(payload.get("status") or ""),
        observed_at=str(payload.get("observed_at") or ""),
        sections=_sequence(payload.get("sections", []), "source_result.sections"),
        snapshot_ref=_optional_text(
            payload.get("snapshot_ref"), "source_result.snapshot_ref", maximum=500
        ),
        retryable=_boolean(payload.get("retryable", False), "source_result.retryable"),
    )
    supplied_digest = _text(
        payload.get("snapshot_digest"),
        "source_result.snapshot_digest",
        maximum=128,
    )
    if supplied_digest != normalized["snapshot_digest"]:
        raise ValueError("source_result.snapshot_digest does not match sections")
    if expected_source_id and normalized["source_id"] != expected_source_id:
        raise ValueError("source adapter returned a different source_id")
    if expected_source_kind and normalized["source_kind"] != expected_source_kind:
        raise ValueError("source adapter returned a different source_kind")
    return normalized


def build_periodic_report_document(
    *,
    title: str,
    generated_at: str,
    period_window: Mapping[str, Any],
    profile: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    editorial: Mapping[str, Any] | None = None,
    trigger_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge normalized source sections into one renderer-neutral document."""

    start_at = _timestamp(period_window.get("start_at"), "period_window.start_at")
    end_at = _timestamp(period_window.get("end_at"), "period_window.end_at")
    start_value = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
    end_value = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
    if start_value >= end_value:
        raise ValueError("period_window.start_at must be earlier than end_at")
    normalized_sources = [
        normalize_periodic_report_source_result(source) for source in sources
    ]
    source_ids = [str(source["source_id"]) for source in normalized_sources]
    if not source_ids or len(source_ids) != len(set(source_ids)):
        raise ValueError("sources must contain unique source_id values")

    merged: dict[str, dict[str, Any]] = {}
    for source in normalized_sources:
        for section in source["sections"]:
            section_id = str(section["section_id"])
            target = merged.setdefault(
                section_id,
                {
                    "schema_version": SECTION_SCHEMA,
                    "section_id": section_id,
                    "title": section["title"],
                    "order": section["order"],
                    "items": [],
                },
            )
            if target["title"] != section["title"]:
                raise ValueError(
                    f"section {section_id!r} has conflicting titles across sources"
                )
            target["order"] = min(int(target["order"]), int(section["order"]))
            target["items"].extend(
                {**item, "source_id": source["source_id"]} for item in section["items"]
            )
    for section in merged.values():
        identities = [
            (str(item["source_id"]), str(item["item_id"])) for item in section["items"]
        ]
        if len(identities) != len(set(identities)):
            raise ValueError(
                f"section {section['section_id']!r} contains duplicate source items"
            )
        section["items"] = sorted(
            section["items"],
            key=lambda item: (
                int(item["value_rank"]),
                str(item["source_id"]),
                str(item["item_id"]),
            ),
        )

    normalized_trigger = _normalize_trigger_receipt(trigger_receipt)
    normalized_sections = sorted(
        merged.values(),
        key=lambda item: (int(item["order"]), str(item["section_id"])),
    )
    normalized_editorial = build_periodic_report_editorial(
        sections=normalized_sections,
        editorial=editorial,
    )
    normalized_profile = {
        "profile_id": _token(profile.get("profile_id"), "profile.profile_id"),
        "profile_version": _token(
            profile.get("profile_version"), "profile.profile_version"
        ),
    }
    if normalized_trigger is not None and any(
        normalized_trigger["profile"].get(key) != value
        for key, value in normalized_profile.items()
    ):
        raise ValueError("trigger_receipt.profile must match the document profile")
    document = {
        "schema_version": DOCUMENT_SCHEMA,
        "title": _text(title, "title", maximum=200),
        "generated_at": _timestamp(generated_at, "generated_at"),
        "period_window": {"start_at": start_at, "end_at": end_at},
        "profile": normalized_profile,
        "source_snapshots": [
            {
                key: source[key]
                for key in (
                    "source_id",
                    "source_kind",
                    "status",
                    "observed_at",
                    "snapshot_digest",
                    "snapshot_ref",
                    "item_count",
                    "retryable",
                )
                if key in source
            }
            for source in sorted(
                normalized_sources, key=lambda item: str(item["source_id"])
            )
        ],
        "sections": normalized_sections,
        "boundary": {
            "schedule_policy_owned_by_profile": True,
            "business_semantics_owned_by_sources": True,
            "editorial_selection_owned_by_profile": True,
            "editorial_summary_owned_by_orchestrator": True,
            "renderer_owns_business_semantics": False,
            "sink_owns_business_semantics": False,
            "external_writes_performed": False,
        },
    }
    if normalized_editorial:
        document["editorial"] = normalized_editorial
    if normalized_trigger:
        document["trigger_receipt"] = normalized_trigger
    validate_periodic_report_editorial(document)
    _reject_private_fields(document, "document")
    return document


def _normalize_artifact_result(
    raw: Mapping[str, Any],
    *,
    expected_renderer_id: str | None = None,
    expected_renderer_kind: str | None = None,
    expected_document: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    artifact = _mapping(raw, "artifact")
    _reject_private_fields(artifact, "artifact")
    if artifact.get("schema_version") != ARTIFACT_SCHEMA:
        raise ValueError(f"artifact must use {ARTIFACT_SCHEMA}")
    renderer_id = _token(artifact.get("renderer_id"), "artifact.renderer_id")
    renderer_kind = _token(artifact.get("renderer_kind"), "artifact.renderer_kind")
    if expected_renderer_id and renderer_id != expected_renderer_id:
        raise ValueError("renderer returned a different renderer_id")
    if expected_renderer_kind and renderer_kind != expected_renderer_kind:
        raise ValueError("renderer returned a different renderer_kind")
    raw_content = artifact.get("content")
    if not isinstance(raw_content, str) or not raw_content.strip():
        raise ValueError("artifact.content is required")
    if len(raw_content) > 1000000:
        raise ValueError("artifact.content exceeds 1000000 characters")
    content = raw_content
    expected_digest = f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
    if artifact.get("content_digest") != expected_digest:
        raise ValueError("artifact.content_digest does not match content")
    document_digest = _text(
        artifact.get("document_digest"), "artifact.document_digest", maximum=80
    )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", document_digest):
        raise ValueError("artifact.document_digest must use sha256")
    if expected_document is not None:
        expected_document_digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    expected_document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        )
        if document_digest != expected_document_digest:
            raise ValueError("artifact.document_digest does not match document")
    boundary = _mapping(artifact.get("boundary"), "artifact.boundary")
    for field in (
        "schedule_policy_applied",
        "business_evidence_judged",
        "external_writes_performed",
    ):
        if boundary.get(field) is not False:
            raise ValueError(f"artifact.boundary.{field} must be false")
    return {
        **artifact,
        "artifact_id": _token(artifact.get("artifact_id"), "artifact.artifact_id"),
        "renderer_id": renderer_id,
        "renderer_kind": renderer_kind,
        "artifact_ref": _text(
            artifact.get("artifact_ref"), "artifact.artifact_ref", maximum=500
        ),
        "content": content,
        "content_digest": expected_digest,
        "document_digest": document_digest,
        "boundary": boundary,
    }


@dataclass(frozen=True)
class PeriodicReportSourceAdapter:
    source_id: str
    source_kind: str
    collect: SourceCollector

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _token(self.source_id, "source_id"))
        object.__setattr__(self, "source_kind", _token(self.source_kind, "source_kind"))
        if not callable(self.collect):
            raise ValueError("source collect must be callable")


@dataclass(frozen=True)
class PeriodicReportRendererAdapter:
    renderer_id: str
    renderer_kind: str
    render: ArtifactRenderer

    def __post_init__(self) -> None:
        object.__setattr__(self, "renderer_id", _token(self.renderer_id, "renderer_id"))
        object.__setattr__(
            self, "renderer_kind", _token(self.renderer_kind, "renderer_kind")
        )
        if not callable(self.render):
            raise ValueError("renderer render must be callable")


@dataclass(frozen=True)
class PeriodicReportSinkAdapter:
    sink_id: str
    sink_kind: str
    sink_role: str
    deliver: ArtifactSink

    def __post_init__(self) -> None:
        object.__setattr__(self, "sink_id", _token(self.sink_id, "sink_id"))
        object.__setattr__(self, "sink_kind", _token(self.sink_kind, "sink_kind"))
        role = _token(self.sink_role, "sink_role")
        object.__setattr__(self, "sink_role", role)
        if role not in _SINK_ROLES:
            raise ValueError("sink_role must be archive or delivery")
        if not callable(self.deliver):
            raise ValueError("sink deliver must be callable")


class PeriodicReportAdapterRegistry:
    """Register typed adapters without granting them scheduling authority."""

    def __init__(self) -> None:
        self._sources: dict[str, PeriodicReportSourceAdapter] = {}
        self._renderers: dict[str, PeriodicReportRendererAdapter] = {}
        self._sinks: dict[str, PeriodicReportSinkAdapter] = {}

    @staticmethod
    def _register(target: dict[str, Any], identity: str, adapter: Any) -> None:
        if identity in target:
            raise ValueError(f"duplicate periodic report adapter {identity!r}")
        target[identity] = adapter

    def register_source(self, adapter: PeriodicReportSourceAdapter) -> None:
        self._register(self._sources, adapter.source_id, adapter)

    def register_renderer(self, adapter: PeriodicReportRendererAdapter) -> None:
        self._register(self._renderers, adapter.renderer_id, adapter)

    def register_sink(self, adapter: PeriodicReportSinkAdapter) -> None:
        self._register(self._sinks, adapter.sink_id, adapter)

    def collect(self, source_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        canonical_source_id = _token(source_id, "source_id")
        adapter = self._sources.get(canonical_source_id)
        if adapter is None:
            raise ValueError(f"unknown periodic report source {source_id!r}")
        return normalize_periodic_report_source_result(
            adapter.collect(request),
            expected_source_id=adapter.source_id,
            expected_source_kind=adapter.source_kind,
        )

    def render(self, renderer_id: str, document: Mapping[str, Any]) -> dict[str, Any]:
        canonical_renderer_id = _token(renderer_id, "renderer_id")
        adapter = self._renderers.get(canonical_renderer_id)
        if adapter is None:
            raise ValueError(f"unknown periodic report renderer {renderer_id!r}")
        canonical_document = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected_document = json.loads(canonical_document)
        renderer_document = json.loads(canonical_document)
        return _normalize_artifact_result(
            adapter.render(renderer_document),
            expected_renderer_id=adapter.renderer_id,
            expected_renderer_kind=adapter.renderer_kind,
            expected_document=expected_document,
        )

    def deliver(
        self,
        sink_id: str,
        artifact: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        canonical_sink_id = _token(sink_id, "sink_id")
        adapter = self._sinks.get(canonical_sink_id)
        if adapter is None:
            raise ValueError(f"unknown periodic report sink {sink_id!r}")
        normalized_artifact = _normalize_artifact_result(artifact)
        result = _mapping(adapter.deliver(normalized_artifact, context), "sink_result")
        if result.get("schema_version") != SINK_RESULT_SCHEMA:
            raise ValueError(f"sink_result must use {SINK_RESULT_SCHEMA}")
        expected = {
            "sink_id": adapter.sink_id,
            "sink_kind": adapter.sink_kind,
            "sink_role": adapter.sink_role,
        }
        if any(result.get(key) != value for key, value in expected.items()):
            raise ValueError("sink returned a different adapter identity")
        status = str(result.get("status") or "")
        if status not in _SINK_STATUSES:
            raise ValueError("sink_result.status is invalid")
        if result.get("schedule_policy_applied") is not False:
            raise ValueError("sink must not apply schedule policy")
        if result.get("business_evidence_judged") is not False:
            raise ValueError("sink must not judge business evidence")
        if status == "sent":
            expected_idempotency_key = _text(
                context.get("idempotency_key"),
                "context.idempotency_key",
                maximum=128,
            )
            if result.get("idempotency_key") != expected_idempotency_key:
                raise ValueError(
                    "sent sink result idempotency_key must match the delivery context"
                )
            if not (
                result.get("receipt_ref") and result.get("readback_verified") is True
            ):
                raise ValueError("sent sink result requires receipt_ref and readback")
        _reject_private_fields(result, "sink_result")
        return result

    def describe(self) -> dict[str, Any]:
        return {
            "schema_version": "periodic_report_adapter_registry_v0",
            "sources": sorted(self._sources),
            "renderers": sorted(self._renderers),
            "sinks": sorted(self._sinks),
            "schedule_policy_owned": False,
            "business_evidence_judged": False,
        }
