from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Optional


MAX_ISSUE_META_SURFACE_ITEMS = 8
MAX_ISSUE_META_LABELS = 8

ISSUE_META_SURFACE_SCHEMA_VERSION = "issue_meta_surface_v0"
ISSUE_META_SURFACE_ITEM_SCHEMA_VERSION = "issue_meta_surface_item_v0"
ISSUE_META_SURFACE_SECTION_HEADINGS = ("Issue Meta Surface", "Issue/PR Meta Surface")
ISSUE_META_SURFACE_FIELD_PATTERN = re.compile(
    r"(?P<key>[a-z_][a-z0-9_-]*)=(?P<value>\"[^\"]+\"|'[^']+'|[^\s]+)"
)

PublicSafeText = Callable[..., Optional[str]]
SectionParser = Callable[[str, tuple[str, ...]], dict[str, list[str]]]


def issue_meta_surface_blocks(lines: list[str]) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("- ", "* ")):
            if current:
                blocks.append(" ".join(current))
            current = [stripped[2:].strip()]
            continue
        if current and line.startswith((" ", "\t")):
            current.append(stripped)
    if current:
        blocks.append(" ".join(current))
    return blocks


def issue_meta_public_value(
    value: Any,
    *,
    public_safe_compact_text: PublicSafeText,
    limit: int = 120,
) -> str | None:
    text = public_safe_compact_text(value, limit=limit)
    if not text:
        return None
    if "<" in text or ">" in text:
        return None
    return text.strip("\"'")


def issue_meta_list(
    value: Any,
    *,
    public_safe_compact_text: PublicSafeText,
    limit: int = MAX_ISSUE_META_LABELS,
) -> list[str]:
    if value is None:
        return []
    raw_values = re.split(r"[,;|]", str(value or ""))
    items: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        item = issue_meta_public_value(raw, public_safe_compact_text=public_safe_compact_text, limit=80)
        if not item or item in seen:
            continue
        seen.add(item)
        items.append(item)
        if len(items) >= limit:
            break
    return items


def parse_issue_meta_surface_block(
    block: str,
    *,
    index: int,
    public_safe_compact_text: PublicSafeText,
) -> dict[str, Any] | None:
    fields: dict[str, str] = {}
    for match in ISSUE_META_SURFACE_FIELD_PATTERN.finditer(block):
        key = str(match.group("key") or "").strip().lower().replace("-", "_")
        value: str | None = str(match.group("value") or "").strip().strip("\"'")
        if key and value:
            fields[key] = value
    if not fields:
        return None

    item: dict[str, Any] = {
        "schema_version": ISSUE_META_SURFACE_ITEM_SCHEMA_VERSION,
        "index": index,
    }
    alias_map = {
        "anchor_id": ("anchor_id", "id"),
        "repo_handle": ("repo_handle", "repo"),
        "issue_handle": ("issue_handle", "issue", "issue_or_pr", "issue_or_pr_handle"),
        "owner_route": ("owner_route", "owner"),
        "related_code_hint": ("related_code_hint", "related_code", "code"),
        "validation_surface": ("validation_surface", "validation"),
        "promotion_target": ("promotion_target", "promotion"),
        "status": ("status",),
        "freshness": ("freshness",),
    }
    for output_key, aliases in alias_map.items():
        raw_value = next((fields[alias] for alias in aliases if alias in fields), None)
        value = issue_meta_public_value(raw_value, public_safe_compact_text=public_safe_compact_text)
        if value:
            item[output_key] = value
    labels = issue_meta_list(
        fields.get("labels") or fields.get("label"),
        public_safe_compact_text=public_safe_compact_text,
    )
    if labels:
        item["labels"] = labels

    required = ("repo_handle", "owner_route", "validation_surface", "promotion_target")
    if not all(item.get(key) for key in required):
        return None
    if not item.get("anchor_id"):
        repo = str(item.get("repo_handle") or "")
        issue = str(item.get("issue_handle") or "")
        anchor_basis = "|".join(part for part in (repo, issue, str(index)) if part)
        item["anchor_id"] = f"issue_anchor_{hashlib.sha1(anchor_basis.encode('utf-8')).hexdigest()[:10]}"
    return item


def parse_issue_meta_surface(
    state_text: str,
    *,
    section_parser: SectionParser,
    public_safe_compact_text: PublicSafeText,
) -> dict[str, Any] | None:
    section_map = section_parser(state_text, ISSUE_META_SURFACE_SECTION_HEADINGS)
    for heading in ISSUE_META_SURFACE_SECTION_HEADINGS:
        lines = section_map.get(heading) or []
        blocks = issue_meta_surface_blocks(lines)
        items = [
            item
            for index, block in enumerate(blocks, start=1)
            for item in [
                parse_issue_meta_surface_block(
                    block,
                    index=index,
                    public_safe_compact_text=public_safe_compact_text,
                )
            ]
            if item
        ]
        if items:
            return {
                "schema_version": ISSUE_META_SURFACE_SCHEMA_VERSION,
                "source_section": heading,
                "item_count": len(items),
                "items": items[:MAX_ISSUE_META_SURFACE_ITEMS],
            }
    return None
