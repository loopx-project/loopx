"""Readable local projections for managed material rankings."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from .ownership import MaterialProjectScope, material_owner_fields

from ._validation import (
    capability_contract,
    check_record_keys,
    compact_token,
    iso_timestamp,
    material_catalog_entry_limit,
    packet_ref,
    positive_int,
)

MATERIAL_READABLE_PROJECTION_RECEIPT_SCHEMA_VERSION = (
    "material_readable_projection_receipt_v0"
)

_ENTRY_FIELDS = {
    "action",
    "entry_ref",
    "judgment",
    "labels",
    "materials",
    "rank",
    "stage",
    "title",
}
_REQUIRED_ENTRY_FIELDS = {
    "action",
    "entry_ref",
    "judgment",
    "materials",
    "rank",
    "stage",
    "title",
}
_MATERIAL_FIELDS = {
    "action",
    "audit_ref",
    "display_label",
    "judgment",
    "material_ref",
    "primary_url",
    "source_note",
    "source_status",
    "summary",
    "title",
}
_REQUIRED_MATERIAL_FIELDS = {
    "action",
    "audit_ref",
    "judgment",
    "material_ref",
    "source_status",
    "summary",
    "title",
}
_LABEL_KEYS = {
    "action",
    "audit_reference",
    "backlog_intro",
    "backlog_section",
    "current_judgment",
    "entry_materials",
    "position",
    "source_note",
    "source_status",
    "summary",
    "top_section",
    "why",
}
_DEFAULT_LABELS = {
    "action": "Action",
    "audit_reference": "Audit reference",
    "backlog_intro": (
        "These entries remain independently ranked outside the active window."
    ),
    "backlog_section": "Ranked Backlog",
    "current_judgment": "Current judgment",
    "entry_materials": "Materials",
    "position": "Position",
    "source_note": "Source",
    "source_status": "Source status",
    "summary": "Summary",
    "top_section": "Top Materials",
    "why": "Why here",
}


def _display_text(value: Any, *, field: str, max_len: int = 2_000) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError(f"{field} must be non-empty")
    if "\x00" in text:
        raise ValueError(f"{field} must not contain NUL")
    if len(text) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters")
    return text


def _optional_display_text(
    value: Any,
    *,
    field: str,
    max_len: int = 2_000,
) -> str | None:
    if value is None:
        return None
    return _display_text(value, field=field, max_len=max_len)


def _display_list(
    values: Sequence[Any] | None,
    *,
    field: str,
    max_items: int = 20,
) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{field} must be a sequence")
    if len(values) > max_items:
        raise ValueError(f"{field} must contain at most {max_items} items")
    return [_display_text(value, field=f"{field}[]", max_len=160) for value in values]


def _primary_url(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    url = _display_text(value, field=field, max_len=2_048)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field} must not contain URL credentials")
    if any(character in url for character in "<>\n\r"):
        raise ValueError(f"{field} contains unsupported characters")
    return url


def _markdown_title(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _normalize_material(
    value: Mapping[str, Any],
    *,
    field: str,
) -> dict[str, Any]:
    check_record_keys(
        value,
        field=field,
        allowed=_MATERIAL_FIELDS,
        required=_REQUIRED_MATERIAL_FIELDS,
    )
    return {
        "material_ref": compact_token(
            value["material_ref"],
            field=f"{field}.material_ref",
        ),
        "display_label": _optional_display_text(
            value.get("display_label"),
            field=f"{field}.display_label",
            max_len=160,
        ),
        "title": _display_text(
            value["title"],
            field=f"{field}.title",
            max_len=500,
        ),
        "primary_url": _primary_url(
            value.get("primary_url"),
            field=f"{field}.primary_url",
        ),
        "summary": _display_text(
            value["summary"],
            field=f"{field}.summary",
        ),
        "judgment": _display_text(
            value["judgment"],
            field=f"{field}.judgment",
        ),
        "action": _display_text(
            value["action"],
            field=f"{field}.action",
        ),
        "source_status": _display_text(
            value["source_status"],
            field=f"{field}.source_status",
        ),
        "audit_ref": compact_token(
            value["audit_ref"],
            field=f"{field}.audit_ref",
        ),
        "source_note": _optional_display_text(
            value.get("source_note"),
            field=f"{field}.source_note",
        ),
    }


def _normalize_entries(
    entries: Sequence[Mapping[str, Any]],
    *,
    expected_material_refs: Sequence[str],
    top_window_size: int,
    max_materials_per_entry: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise TypeError("entries must be a sequence of objects")
    if not entries:
        raise ValueError("entries must contain at least one entry")
    if top_window_size > len(entries):
        raise ValueError("top_window_size cannot exceed entry count")

    normalized: list[dict[str, Any]] = []
    entry_refs: set[str] = set()
    ranks: set[int] = set()
    material_refs: list[str] = []
    for index, value in enumerate(entries):
        field = f"entries[{index}]"
        check_record_keys(
            value,
            field=field,
            allowed=_ENTRY_FIELDS,
            required=_REQUIRED_ENTRY_FIELDS,
        )
        entry_ref = compact_token(value["entry_ref"], field=f"{field}.entry_ref")
        rank = positive_int(value["rank"], field=f"{field}.rank")
        if entry_ref in entry_refs:
            raise ValueError("entry_ref values must be unique")
        if rank in ranks:
            raise ValueError("rank values must be unique")
        materials_value = value["materials"]
        if isinstance(materials_value, (str, bytes)) or not isinstance(
            materials_value,
            Sequence,
        ):
            raise TypeError(f"{field}.materials must be a sequence")
        if not 1 <= len(materials_value) <= max_materials_per_entry:
            raise ValueError(
                f"{field}.materials must contain between 1 and "
                f"{max_materials_per_entry} items"
            )
        materials = [
            _normalize_material(item, field=f"{field}.materials[{material_index}]")
            for material_index, item in enumerate(materials_value)
        ]
        current_refs = [str(item["material_ref"]) for item in materials]
        duplicates = set(material_refs).intersection(current_refs)
        if duplicates or len(current_refs) != len(set(current_refs)):
            raise ValueError("material_ref values must be globally unique")
        material_refs.extend(current_refs)
        entry_refs.add(entry_ref)
        ranks.add(rank)
        normalized.append(
            {
                "entry_ref": entry_ref,
                "rank": rank,
                "title": _display_text(
                    value["title"],
                    field=f"{field}.title",
                    max_len=500,
                ),
                "stage": _display_text(
                    value["stage"],
                    field=f"{field}.stage",
                    max_len=320,
                ),
                "judgment": _display_text(
                    value["judgment"],
                    field=f"{field}.judgment",
                ),
                "action": _display_text(
                    value["action"],
                    field=f"{field}.action",
                ),
                "labels": _display_list(
                    value.get("labels"),
                    field=f"{field}.labels",
                ),
                "materials": materials,
            }
        )

    if sorted(ranks) != list(range(1, len(entries) + 1)):
        raise ValueError("entry ranks must be contiguous from 1")
    expected = [
        compact_token(value, field="expected_material_refs[]")
        for value in expected_material_refs
    ]
    if len(expected) != len(set(expected)):
        raise ValueError("expected_material_refs values must be unique")
    if set(material_refs) != set(expected):
        missing = sorted(set(expected) - set(material_refs))
        unexpected = sorted(set(material_refs) - set(expected))
        raise ValueError(
            "entries must cover expected_material_refs exactly"
            f"; missing={missing}; unexpected={unexpected}"
        )
    return sorted(normalized, key=lambda item: int(item["rank"])), material_refs


def _labels(overrides: Mapping[str, Any] | None) -> dict[str, str]:
    result = dict(_DEFAULT_LABELS)
    if overrides is None:
        return result
    unexpected = sorted(set(overrides) - _LABEL_KEYS)
    if unexpected:
        raise ValueError(f"labels contains unsupported keys: {', '.join(unexpected)}")
    for key, value in overrides.items():
        result[str(key)] = _display_text(
            value,
            field=f"labels.{key}",
            max_len=500,
        )
    return result


def _markdown_lines(
    values: Sequence[str] | None,
    *,
    field: str,
) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{field} must be a sequence of Markdown lines")
    return [str(value) for value in values]


def build_material_readable_projection(
    *,
    goal_id: str | None = None,
    project_scope: MaterialProjectScope | None = None,
    projection_id: str,
    authority_revision: str,
    observed_at: str,
    entries: Sequence[Mapping[str, Any]],
    expected_material_refs: Sequence[str],
    canonical_item_count: int,
    top_window_size: int = 30,
    max_materials_per_entry: int | None = None,
    catalog: Mapping[str, Any] | None = None,
    document_title: str = "Current Material Priorities",
    intro_lines: Sequence[str] | None = None,
    footer_lines: Sequence[str] | None = None,
    labels: Mapping[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Render a local-readable view and a content-free public-safe receipt.

    When ``max_materials_per_entry`` is omitted, the cap is read from
    ``catalog["rankings"]["ranked_entries"]["constraints"]``; without a catalog
    it falls back to the default of 3.
    """

    window_size = positive_int(top_window_size, field="top_window_size")
    entry_limit = material_catalog_entry_limit(
        explicit=max_materials_per_entry,
        catalog=catalog,
    )
    item_count = positive_int(canonical_item_count, field="canonical_item_count")
    if isinstance(expected_material_refs, (str, bytes)) or not isinstance(
        expected_material_refs,
        Sequence,
    ):
        raise TypeError("expected_material_refs must be a sequence")
    normalized, material_refs = _normalize_entries(
        entries,
        expected_material_refs=expected_material_refs,
        top_window_size=window_size,
        max_materials_per_entry=entry_limit,
    )
    if item_count < len(material_refs):
        raise ValueError(
            "canonical_item_count cannot be smaller than ranked material count"
        )
    vocabulary = _labels(labels)
    intro = _markdown_lines(intro_lines, field="intro_lines")
    footer = _markdown_lines(footer_lines, field="footer_lines")
    lines = [
        f"# {_display_text(document_title, field='document_title', max_len=500)}",
        "",
        f"> Authority revision: `{compact_token(authority_revision, field='authority_revision')}`",
        "",
    ]
    lines.extend(intro)
    if intro:
        lines.append("")
    lines.extend([f"## {vocabulary['top_section']}", ""])

    for entry in normalized:
        rank = int(entry["rank"])
        if rank == window_size + 1:
            lines.extend(
                [
                    f"## {vocabulary['backlog_section']}",
                    "",
                    vocabulary["backlog_intro"],
                    "",
                ]
            )
        entry_labels = "/".join(str(value) for value in entry["labels"])
        lines.extend(
            [
                f"### {rank}. {entry['title']}",
                "",
            ]
        )
        if entry_labels:
            lines.append(f"**{vocabulary['entry_materials']}:** `{entry_labels}`  ")
        lines.extend(
            [
                f"**{vocabulary['position']}:** {entry['stage']}  ",
                f"**{vocabulary['why']}:** {entry['judgment']}  ",
                f"**{vocabulary['action']}:** {entry['action']}",
                "",
            ]
        )
        for material in entry["materials"]:
            title = _markdown_title(str(material["title"]))
            primary_url = material["primary_url"]
            rendered_title = f"[{title}](<{primary_url}>)" if primary_url else title
            display_label = material["display_label"]
            heading = (
                f"{display_label} · {rendered_title}"
                if display_label
                else rendered_title
            )
            lines.extend(
                [
                    f"#### {heading}",
                    "",
                    f"- **{vocabulary['summary']}:** {material['summary']}",
                    (f"- **{vocabulary['current_judgment']}:** {material['judgment']}"),
                    f"- **{vocabulary['action']}:** {material['action']}",
                    (
                        f"- **{vocabulary['source_status']}:** "
                        f"{material['source_status']}"
                    ),
                    (
                        f"- **{vocabulary['audit_reference']}:** "
                        f"`{material['audit_ref']}`"
                    ),
                ]
            )
            if material["source_note"] is not None:
                lines.append(
                    f"- **{vocabulary['source_note']}:** {material['source_note']}"
                )
            lines.append("")

    lines.extend(footer)
    if footer:
        lines.append("")
    projection = "\n".join(lines).encode("utf-8")
    projection_digest = hashlib.sha256(projection).hexdigest()
    projection_ref = f"material-readable-projection-{projection_digest[:20]}"
    receipt: dict[str, Any] = {
        "schema_version": MATERIAL_READABLE_PROJECTION_RECEIPT_SCHEMA_VERSION,
        **material_owner_fields(goal_id=goal_id, project_scope=project_scope),
        "projection_id": compact_token(projection_id, field="projection_id"),
        "authority_revision": compact_token(
            authority_revision,
            field="authority_revision",
        ),
        "observed_at": iso_timestamp(observed_at, field="observed_at"),
        "projection_ref": projection_ref,
        "projection_digest": f"sha256:{projection_digest}",
        "summary": {
            "canonical_item_count": item_count,
            "ranked_entry_count": len(normalized),
            "top_window_entry_count": window_size,
            "ranked_backlog_entry_count": len(normalized) - window_size,
            "ranked_material_count": len(material_refs),
            "unique_ranked_material_count": len(set(material_refs)),
        },
        "constraints": {
            "max_materials_per_entry": entry_limit,
        },
        "verification": {
            "contiguous_rank_sequence_verified": True,
            "entry_budget_verified": True,
            "complete_coverage_verified": True,
            "unique_membership_verified": True,
            "content_backing_required": True,
        },
        "visibility": "public_safe",
        "projection_visibility": "local_private_allowed",
        "capability": capability_contract(packet_role="readable_projection_receipt", project_scoped=project_scope is not None),
        "raw_content_captured": False,
        "private_locations_captured": False,
    }
    receipt["receipt_ref"] = packet_ref("material-readable-projection-receipt", receipt)
    return projection, receipt
