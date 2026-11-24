"""Local CLI presentation of registered material metadata; never opens sources."""
from __future__ import annotations

from pathlib import Path
import unicodedata
from typing import cast

from ..authority import find_goal_index, normalize_project_materials, normalize_topic_authority
from ..control_plane.projects.registry_codec import load_project_registry


def _search_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def list_authority_sources(
    registry_path: Path, goal_id: str, *, query: str = "", topic: str | None = None,
    offset: int = 0, limit: int = 20,
) -> dict[str, object]:
    """Filter before paging, retaining registry metadata without interpreting gates."""
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("offset must be non-negative and limit must be between 1 and 100")
    registry = load_project_registry(registry_path)
    goal = registry["goals"][find_goal_index(registry, goal_id)]
    authority = goal.get("authority_registry") or {}
    if not isinstance(authority, dict):
        raise ValueError("authority_registry must be an object")
    materials = normalize_project_materials(authority.get("project_materials"))
    topics = normalize_topic_authority(authority.get("topic_authority"))
    terms = _search_text(query).split()
    rows: list[dict[str, object]] = []
    for source_id, material in sorted(materials.items()):
        source_topics = sorted(key for key, value in topics.items() if value == source_id)
        if topic is not None and topic not in source_topics:
            continue
        # The registered metadata contract, not arbitrary document/registry fields.
        row: dict[str, object] = {"id": source_id, "topics": source_topics}
        for field in ("source_kind", "role", "freshness", "owner_status", "gate_status",
                      "boundary", "revision", "conflict_rule", "registered_at",
                      "source_ref_kind", "source_ref_sha256"):
            value = material.get(field)
            if isinstance(value, str):
                row[field] = value
        if isinstance(material.get("source_ref_redacted"), bool):
            row["source_ref_redacted"] = material["source_ref_redacted"]
        searchable = _search_text(" ".join(
            [*source_topics, *(value for value in row.values() if isinstance(value, str))]
        ))
        if all(term in searchable for term in terms):
            rows.append(row)
    page = rows[offset:offset + limit]
    next_offset = offset + len(page) if offset + len(page) < len(rows) else None
    return {
        "ok": True, "goal_id": goal_id, "read_only": True,
        "query": query, "topic": topic,
        "source_count": len(materials), "matched_count": len(rows),
        "returned_count": len(page), "offset": offset, "limit": limit,
        "next_offset": next_offset, "sources": page,
    }


def render_authority_sources_markdown(payload: dict[str, object]) -> str:
    lines = ["# Registered Goal Materials", ""]
    if not payload.get("ok"):
        return "\n".join([*lines, str(payload.get("error")), ""])
    lines.extend([
        f"Goal: `{payload['goal_id']}`",
        f"Showing {payload['returned_count']} of {payload['matched_count']} matches "
        f"({payload['source_count']} registered sources).", "",
    ])
    for source in cast(list[dict[str, object]], payload.get("sources", [])):
        lines.append(f"## {source['id']}")
        for field, value in source.items():
            if field != "id":
                shown = ", ".join(str(item) for item in value) if isinstance(value, list) else str(value)
                lines.append(f"- {field}: {shown}")
        lines.append("")
    if payload.get("next_offset") is not None:
        lines.append(f"More matches: repeat the same filters with --offset {payload['next_offset']}.")
    lines.append("Metadata only; source contents and current accessibility were not checked.")
    return "\n".join([*lines, ""])
