from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .control_plane.projects.registry_codec import (
    load_project_registry,
    mutate_project_registry,
    require_runtime_compatible_project_registry,
)
from .control_plane.runtime.time import now_local_iso
from .public_safe_text import (
    PRIVATE_TEXT_PATTERNS as SHARED_PRIVATE_TEXT_PATTERNS,
    TEXT_OWNER_CATEGORIES,
    classify_private_text,
)


AUTHORITY_SOURCE_REGISTRATION_VERSION = "authority_source_registration_v0"
DOC_REGISTRY_AUTHORITY_IMPORT_VERSION = "doc_registry_authority_import_v0"
AUTHORITY_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
AUTHORITY_SOURCE_BOUNDARIES = {"public", "local_private", "private_redacted"}
# Owned by loopx.public_safe_text so every real validator owner shares one
# contract; re-exported here because callers already import this name.
PRIVATE_TEXT_PATTERNS = SHARED_PRIVATE_TEXT_PATTERNS

AUTHORITY_REGISTRY_SUMMARY_FIELDS = (
    "declared",
    "required",
    "path",
    "path_exists",
    "read_status",
    "default_entry_count",
    "default_entries_checked",
    "default_entries_present",
    "topic_authority_count",
    "project_material_count",
    "project_material_repository_count",
    "project_material_owner_review_required_count",
    "project_material_stale_count",
    "project_material_current_authority_count",
    "deprecated_source_count",
    "conflict_risk",
)

AUTHORITY_REGISTRY_CANONICAL_FIELDS = (
    "default_entry_docs",
    "topic_authority",
    "project_materials",
    "deprecated_sources",
)


def now_local() -> str:
    return now_local_iso()


def validate_authority_source_id(source_id: str) -> str:
    text = source_id.strip()
    if not AUTHORITY_SOURCE_ID_RE.match(text):
        raise ValueError(
            "source_id must start with an alphanumeric character and contain only "
            "letters, numbers, '.', '_' or '-'"
        )
    return text


def source_ref_kind(source_ref: str | None) -> str | None:
    if not source_ref:
        return None
    parsed = urlparse(source_ref)
    if parsed.scheme in {"http", "https"}:
        return "url"
    if parsed.scheme == "file":
        return "file_url"
    if source_ref.startswith("/") or source_ref.startswith("~"):
        return "local_path"
    return "opaque_ref"


def source_ref_digest(source_ref: str | None) -> str | None:
    if not source_ref:
        return None
    return hashlib.sha256(source_ref.encode("utf-8")).hexdigest()


def public_safe_optional(label: str, value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    validate_public_safe_text(label, text)
    return text


def validate_public_safe_text(label: str, value: str | None) -> None:
    # Internal-state policy: a credential *word* is recognized but not rejected
    # here; shapes that carry a value still are (Refs #5136, direction 2).
    if classify_private_text(value, categories=TEXT_OWNER_CATEGORIES) is not None:
        raise ValueError(f"{label} contains a private-looking value; keep raw evidence in private payloads")


def find_goal_index(registry: dict[str, Any], goal_id: str) -> int:
    goals = registry.get("goals")
    if not isinstance(goals, list):
        raise ValueError("registry goals must be a list")
    for index, goal in enumerate(goals):
        if isinstance(goal, dict) and str(goal.get("id") or "") == goal_id:
            return index
    raise ValueError(f"goal id not found in registry: {goal_id}")


def normalize_project_materials(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items() if isinstance(item, dict)}
    if isinstance(value, list):
        result: dict[str, Any] = {}
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("source_id") or f"source_{index + 1}")
            result[item_id] = dict(item)
        return result
    return {}


def normalize_topic_authority(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def authority_registry_has_canonical_fields(raw: dict[str, Any]) -> bool:
    """Return whether a registry carries source data richer than a summary."""

    return any(key in raw for key in AUTHORITY_REGISTRY_CANONICAL_FIELDS)


def apply_authority_registry_summary(
    authority_registry: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    """Refresh co-located summary fields without replacing canonical source data."""

    for key in AUTHORITY_REGISTRY_SUMMARY_FIELDS:
        if key in summary:
            authority_registry[key] = summary[key]


def compact_registered_authority_source(
    *,
    source_id: str,
    source_ref: str | None,
    source_kind: str,
    role: str,
    freshness: str,
    owner_status: str | None,
    gate_status: str | None,
    boundary: str,
    revision: str | None,
    conflict_rule: str | None,
    registered_at: str,
) -> dict[str, Any]:
    if boundary not in AUTHORITY_SOURCE_BOUNDARIES:
        raise ValueError(f"boundary must be one of: {', '.join(sorted(AUTHORITY_SOURCE_BOUNDARIES))}")
    source_id = validate_authority_source_id(source_id)
    source_kind = public_safe_optional("source_kind", source_kind) or ""
    role = public_safe_optional("role", role) or ""
    freshness = public_safe_optional("freshness", freshness) or ""
    if not source_kind:
        raise ValueError("source_kind is required")
    if not role:
        raise ValueError("role is required")
    if not freshness:
        raise ValueError("freshness is required")

    entry: dict[str, Any] = {
        "schema_version": AUTHORITY_SOURCE_REGISTRATION_VERSION,
        "id": source_id,
        "role": role,
        "source_kind": source_kind,
        "freshness": freshness,
        "boundary": boundary,
        "source_ref_redacted": bool(source_ref),
        "registered_at": registered_at,
    }
    ref_kind = source_ref_kind(source_ref)
    ref_digest = source_ref_digest(source_ref)
    if ref_kind:
        entry["source_ref_kind"] = ref_kind
    if ref_digest:
        entry["source_ref_sha256"] = ref_digest
    for key, value in (
        ("owner_status", public_safe_optional("owner_status", owner_status)),
        ("gate_status", public_safe_optional("gate_status", gate_status)),
        ("revision", public_safe_optional("revision", revision)),
        ("conflict_rule", public_safe_optional("conflict_rule", conflict_rule)),
    ):
        if value:
            entry[key] = value
    return entry


def render_authority_source_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Authority Source Registration",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- written: `{payload.get('written')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- source_id: `{payload.get('source_id')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)
    entry = payload.get("entry") if isinstance(payload.get("entry"), dict) else {}
    lines.extend(
        [
            f"- registry: `{payload.get('registry')}`",
            f"- source_ref_redacted: `{entry.get('source_ref_redacted')}`",
            f"- source_ref_kind: `{entry.get('source_ref_kind')}`",
            f"- boundary: `{entry.get('boundary')}`",
            f"- freshness: `{entry.get('freshness')}`",
            f"- owner_status: `{entry.get('owner_status')}`",
            f"- gate_status: `{entry.get('gate_status')}`",
            f"- authority_registry_path: `{payload.get('authority_registry_path')}`",
            f"- project_material_count: `{payload.get('authority_registry_summary', {}).get('project_material_count') if isinstance(payload.get('authority_registry_summary'), dict) else None}`",
            f"- global_sync_wrote: `{payload.get('global_sync', {}).get('wrote') if isinstance(payload.get('global_sync'), dict) else None}`",
            "",
            "## Write Effect",
            str(payload.get("write_effect") or ""),
        ]
    )
    return "\n".join(lines)


def _strip_yaml_scalar(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if text in {">", ">-", "|", "|-"}:
        return ""
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    return text


def read_doc_registry_contract(doc_registry_path: Path) -> dict[str, Any]:
    """Read the public-safe contract surface from a DOC_REGISTRY-like YAML file."""

    path = doc_registry_path.expanduser()
    text = path.read_text(encoding="utf-8")
    result: dict[str, Any] = {
        "version": None,
        "updated_at": None,
        "default_entry_docs": [],
        "topic_authority": {},
        "status_definition_count": 0,
    }
    current_section: str | None = None
    status_definition_count = 0
    in_status_definitions = False
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if indent == 0 and ":" in stripped:
            key, raw_value = stripped.split(":", 1)
            key = key.strip()
            value = _strip_yaml_scalar(raw_value)
            current_section = key
            in_status_definitions = key == "status_definitions"
            if key in {"version", "updated_at"} and value:
                result[key] = value
            continue
        if current_section == "default_entry_docs" and stripped.startswith("- "):
            entry = _strip_yaml_scalar(stripped[2:])
            if entry:
                result["default_entry_docs"].append(entry)
            continue
        if current_section == "topic_authority" and ":" in stripped:
            key, raw_value = stripped.split(":", 1)
            topic = _strip_yaml_scalar(key)
            authority = _strip_yaml_scalar(raw_value)
            if topic and authority:
                result["topic_authority"][topic] = authority
            continue
        if in_status_definitions and indent > 0 and ":" in stripped:
            key, _raw_value = stripped.split(":", 1)
            if _strip_yaml_scalar(key):
                status_definition_count += 1
    result["status_definition_count"] = status_definition_count
    return result


def render_doc_registry_authority_import_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Doc Registry Authority Import",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- written: `{payload.get('written')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- source_id: `{payload.get('source_id')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)
    entry = payload.get("entry") if isinstance(payload.get("entry"), dict) else {}
    lines.extend(
        [
            f"- source_ref_redacted: `{entry.get('source_ref_redacted')}`",
            f"- source_ref_kind: `{entry.get('source_ref_kind')}`",
            f"- boundary: `{entry.get('boundary')}`",
            f"- freshness: `{entry.get('freshness')}`",
            f"- default_entry_count: `{entry.get('default_entry_count')}`",
            f"- topic_authority_count: `{entry.get('topic_authority_count')}`",
            f"- imported_topic_count: `{entry.get('imported_topic_count')}`",
            f"- authority_registry_path: `{payload.get('authority_registry_path')}`",
            f"- project_material_count: `{payload.get('authority_registry_summary', {}).get('project_material_count') if isinstance(payload.get('authority_registry_summary'), dict) else None}`",
            f"- global_sync_wrote: `{payload.get('global_sync', {}).get('wrote') if isinstance(payload.get('global_sync'), dict) else None}`",
            "",
            "## Write Effect",
            str(payload.get("write_effect") or ""),
        ]
    )
    return "\n".join(lines)


def _apply_authority_entry(
    registry: dict[str, Any],
    *,
    goal_id: str,
    entry: dict[str, Any],
    registered_at: str,
    imported_topics: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    goal_index = find_goal_index(registry, goal_id)
    goals = registry.get("goals")
    if not isinstance(goals, list) or not isinstance(goals[goal_index], dict):
        raise ValueError("registry goal entry must be an object")
    goal = goals[goal_index]

    authority_registry = (
        dict(goal["authority_registry"])
        if isinstance(goal.get("authority_registry"), dict)
        else {}
    )
    materials = normalize_project_materials(
        authority_registry.get("project_materials")
    )
    previous_entry = materials.get(entry["id"])
    materials[str(entry["id"])] = dict(entry)
    authority_registry["project_materials"] = materials
    authority_registry.setdefault("read_status", "registered")
    if imported_topics:
        topic_map = normalize_topic_authority(
            authority_registry.get("topic_authority")
        )
        topic_map.update(imported_topics)
        authority_registry["topic_authority"] = topic_map
    goal["authority_registry"] = authority_registry

    compact_source_fields = (
        "schema_version",
        "id",
        "role",
        "source_kind",
        "freshness",
        "boundary",
        "source_ref_kind",
        "source_ref_sha256",
        "source_ref_redacted",
        "owner_status",
        "gate_status",
        "revision",
        "conflict_rule",
        "registered_at",
        "default_entry_count",
        "topic_authority_count",
        "imported_topic_count",
    )
    compact_source = {
        key: entry[key] for key in compact_source_fields if key in entry
    }
    authority_sources = goal.get("authority_sources")
    if not isinstance(authority_sources, list):
        authority_sources = []
    goal["authority_sources"] = [
        item
        for item in authority_sources
        if not (
            isinstance(item, dict)
            and str(item.get("id") or item.get("source_id") or "") == entry["id"]
        )
    ] + [compact_source]

    registry["updated_at"] = registered_at
    project = Path(str(goal["repo"])).expanduser() if goal.get("repo") else None
    summary = compact_authority_registry(goal, project=project)
    summary.pop("default_entries", None)
    apply_authority_registry_summary(authority_registry, summary)
    return summary, previous_entry


def register_authority_source(
    *,
    registry_path: Path,
    goal_id: str,
    source_id: str,
    source_ref: str | None,
    source_kind: str,
    role: str,
    freshness: str,
    owner_status: str | None,
    gate_status: str | None,
    boundary: str,
    revision: str | None,
    conflict_rule: str | None,
    topic: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    registry_path = registry_path.expanduser()
    registered_at = now_local()
    entry = compact_registered_authority_source(
        source_id=source_id,
        source_ref=source_ref,
        source_kind=source_kind,
        role=role,
        freshness=freshness,
        owner_status=owner_status,
        gate_status=gate_status,
        boundary=boundary,
        revision=revision,
        conflict_rule=conflict_rule,
        registered_at=registered_at,
    )

    imported_topics: dict[str, str] = {}
    if topic:
        topic_text = public_safe_optional("topic", topic)
        if topic_text:
            imported_topics[topic_text] = str(entry["id"])

    def reduce(
        registry: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return _apply_authority_entry(
            registry,
            goal_id=goal_id,
            entry=entry,
            registered_at=registered_at,
            imported_topics=imported_topics,
        )

    if dry_run:
        registry = load_project_registry(registry_path)
        require_runtime_compatible_project_registry(
            registry,
            operation="authority source registration",
        )
        summary, previous_entry = reduce(copy.deepcopy(registry))
    else:
        summary, previous_entry = mutate_project_registry(
            registry_path,
            operation="register_authority_source",
            reducer=reduce,
        )

    action = "would update" if dry_run else "updated"
    write_effect = (
        f"{action} goal `{goal_id}` authority_registry.project_materials[{entry['id']}] "
        "with a redacted source reference digest; raw source_ref is not stored"
    )
    if previous_entry:
        write_effect += "; previous entry with the same source_id is replaced"
    return {
        "ok": True,
        "dry_run": dry_run,
        "written": not dry_run,
        "registry": str(registry_path),
        "goal_id": goal_id,
        "source_id": entry["id"],
        "entry": entry,
        "authority_registry_path": "authority_registry.project_materials",
        "authority_registry_summary": summary,
        "write_effect": write_effect,
        "raw_source_ref_stored": False,
    }


def import_doc_registry_authority(
    *,
    registry_path: Path,
    goal_id: str,
    source_id: str,
    doc_registry_path: Path,
    source_kind: str,
    role: str,
    freshness: str,
    owner_status: str | None,
    gate_status: str | None,
    boundary: str,
    revision: str | None,
    conflict_rule: str | None,
    topics: list[str],
    import_topic_prefix: str | None,
    max_imported_topics: int,
    dry_run: bool,
) -> dict[str, Any]:
    if max_imported_topics < 0:
        raise ValueError("max_imported_topics must be non-negative")
    registry_path = registry_path.expanduser()
    doc_registry_path = doc_registry_path.expanduser()
    contract = read_doc_registry_contract(doc_registry_path)
    registered_at = now_local()
    entry = compact_registered_authority_source(
        source_id=source_id,
        source_ref=str(doc_registry_path),
        source_kind=source_kind,
        role=role,
        freshness=freshness,
        owner_status=owner_status,
        gate_status=gate_status,
        boundary=boundary,
        revision=revision or str(contract.get("updated_at") or ""),
        conflict_rule=conflict_rule,
        registered_at=registered_at,
    )
    entry["schema_version"] = DOC_REGISTRY_AUTHORITY_IMPORT_VERSION
    default_entries = [str(item) for item in contract.get("default_entry_docs") or []]
    topic_authority = contract.get("topic_authority") if isinstance(contract.get("topic_authority"), dict) else {}
    imported_topics: dict[str, str] = {}
    if import_topic_prefix:
        prefix = public_safe_optional("import_topic_prefix", import_topic_prefix) or ""
        for topic in list(topic_authority.keys())[:max_imported_topics]:
            local_topic = f"{prefix}{topic}"
            public_safe_optional("imported_topic", local_topic)
            imported_topics[local_topic] = entry["id"]
    for topic in topics:
        topic_text = public_safe_optional("topic", topic)
        if topic_text:
            imported_topics[topic_text] = entry["id"]
    entry.update(
        {
            "registry_version": contract.get("version"),
            "registry_updated_at": contract.get("updated_at"),
            "default_entry_count": len(default_entries),
            "topic_authority_count": len(topic_authority),
            "status_definition_count": int(contract.get("status_definition_count") or 0),
            "default_entry_sample": default_entries[:8],
            "topic_sample": list(topic_authority.keys())[:12],
            "imported_topic_count": len(imported_topics),
            "imported_topic_prefix": import_topic_prefix,
            "imported_topic_truncated": bool(import_topic_prefix and len(topic_authority) > max_imported_topics),
        }
    )

    def reduce(
        registry: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return _apply_authority_entry(
            registry,
            goal_id=goal_id,
            entry=entry,
            registered_at=registered_at,
            imported_topics=imported_topics,
        )

    if dry_run:
        registry = load_project_registry(registry_path)
        require_runtime_compatible_project_registry(
            registry,
            operation="authority registry import",
        )
        summary, previous_entry = reduce(copy.deepcopy(registry))
    else:
        summary, previous_entry = mutate_project_registry(
            registry_path,
            operation="import_doc_registry_authority",
            reducer=reduce,
        )

    action = "would import" if dry_run else "imported"
    write_effect = (
        f"{action} DOC_REGISTRY summary into goal `{goal_id}` "
        f"authority_registry.project_materials[{entry['id']}] with "
        f"{len(default_entries)} default entries and {len(topic_authority)} topic mappings; "
        "raw doc_registry_path is hashed and not stored"
    )
    if previous_entry:
        write_effect += "; previous entry with the same source_id is replaced"
    return {
        "ok": True,
        "dry_run": dry_run,
        "written": not dry_run,
        "registry": str(registry_path),
        "goal_id": goal_id,
        "source_id": entry["id"],
        "entry": entry,
        "authority_registry_path": "authority_registry.project_materials",
        "authority_registry_summary": summary,
        "write_effect": write_effect,
        "raw_doc_registry_path_stored": False,
        "imported_topics": imported_topics,
    }


def _path_text(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    return text or None


def _resolve_project_path(project: Path | None, path_text: str | None) -> Path | None:
    if not path_text or project is None:
        return None
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else project / path


def _entry_path(value: Any) -> str | None:
    if isinstance(value, str):
        return _path_text(value)
    if isinstance(value, dict):
        return _path_text(value.get("path") or value.get("doc") or value.get("file"))
    return None


def authority_registry_required(goal: dict[str, Any] | None) -> bool:
    if not goal:
        return False
    return bool(goal.get("requires_authority_registry") or goal.get("authority_registry_required"))


def authority_registry_default_entries(raw_entries: Any) -> list[str]:
    entries: list[str] = []
    if isinstance(raw_entries, list):
        for item in raw_entries:
            path = _entry_path(item)
            if path:
                entries.append(path)
    elif isinstance(raw_entries, dict):
        for item in raw_entries.values():
            path = _entry_path(item)
            if path:
                entries.append(path)
    return entries


def authority_registry_topic_count(raw_topics: Any) -> int:
    if isinstance(raw_topics, dict):
        return len(raw_topics)
    if isinstance(raw_topics, list):
        return len(raw_topics)
    return 0


def authority_registry_project_material_stats(raw_materials: Any) -> dict[str, int]:
    if isinstance(raw_materials, dict):
        items = [item for item in raw_materials.values() if isinstance(item, dict)]
    elif isinstance(raw_materials, list):
        items = [item for item in raw_materials if isinstance(item, dict)]
    else:
        items = []

    repository_kinds = {"repository", "repo", "git_repo", "source_repo", "target_repo"}
    owner_review_freshness = {
        "owner_review_required",
        "owner_review_pending",
        "owner_evidence_missing",
        "missing_owner_evidence",
    }
    stale_freshness = {"stale", "outdated", "needs_refresh", "unknown"}
    stats = {
        "project_material_count": len(items),
        "project_material_repository_count": 0,
        "project_material_owner_review_required_count": 0,
        "project_material_stale_count": 0,
        "project_material_current_authority_count": 0,
    }
    for item in items:
        source_kind = str(item.get("source_kind") or item.get("kind") or "").lower()
        role = str(item.get("role") or "").lower()
        freshness = str(item.get("freshness") or item.get("status") or "").lower()
        if source_kind in repository_kinds or source_kind.endswith("_repository"):
            stats["project_material_repository_count"] += 1
        if freshness in owner_review_freshness or item.get("missing_owner_evidence") is True:
            stats["project_material_owner_review_required_count"] += 1
        if freshness in stale_freshness:
            stats["project_material_stale_count"] += 1
        if role == "current_authority" or role.endswith("_authority"):
            stats["project_material_current_authority_count"] += 1
    return stats


def authority_registry_deprecated_count(raw_sources: Any) -> int:
    if isinstance(raw_sources, (dict, list)):
        return len(raw_sources)
    return 0


def compact_authority_registry(goal: dict[str, Any] | None, *, project: Path | None = None) -> dict[str, Any]:
    raw = goal.get("authority_registry") if goal and isinstance(goal.get("authority_registry"), dict) else {}
    if not raw:
        return {
            "declared": False,
            "required": authority_registry_required(goal),
            "path": None,
            "path_exists": None,
            "read_status": None,
            "default_entry_count": 0,
            "default_entries_checked": 0,
            "default_entries_present": 0,
            "topic_authority_count": 0,
            "project_material_count": 0,
            "project_material_repository_count": 0,
            "project_material_owner_review_required_count": 0,
            "project_material_stale_count": 0,
            "project_material_current_authority_count": 0,
            "deprecated_source_count": 0,
            "conflict_risk": "unknown",
            "default_entries": [],
        }

    path_text = _path_text(raw.get("path"))
    resolved_path = _resolve_project_path(project, path_text)
    summary: dict[str, Any] = {
        "declared": True if authority_registry_has_canonical_fields(raw) else bool(raw.get("declared")),
        "required": bool(raw.get("required") or authority_registry_required(goal)),
        "path": path_text,
        "path_exists": resolved_path.exists() if resolved_path else raw.get("path_exists"),
        "read_status": raw.get("read_status"),
        "default_entry_count": int(raw.get("default_entry_count") or 0),
        "default_entries_checked": int(raw.get("default_entries_checked") or 0),
        "default_entries_present": int(raw.get("default_entries_present") or 0),
        "topic_authority_count": int(raw.get("topic_authority_count") or 0),
        "project_material_count": int(raw.get("project_material_count") or 0),
        "project_material_repository_count": int(raw.get("project_material_repository_count") or 0),
        "project_material_owner_review_required_count": int(
            raw.get("project_material_owner_review_required_count") or 0
        ),
        "project_material_stale_count": int(raw.get("project_material_stale_count") or 0),
        "project_material_current_authority_count": int(
            raw.get("project_material_current_authority_count") or 0
        ),
        "deprecated_source_count": int(
            raw.get("deprecated_source_count") or raw.get("deprecated_sources_seen") or 0
        ),
        "conflict_risk": str(raw.get("conflict_risk") or "unknown"),
        "default_entries": [],
    }

    if "default_entry_docs" in raw:
        default_entries = authority_registry_default_entries(raw.get("default_entry_docs"))
        checked_entries: list[dict[str, Any]] = []
        present = 0
        checked = 0
        for entry_path in default_entries:
            resolved_entry = _resolve_project_path(project, entry_path)
            exists = resolved_entry.exists() if resolved_entry else None
            if exists is not None:
                checked += 1
            if exists:
                present += 1
            checked_entries.append({"path": entry_path, "exists": exists})
        summary.update(
            {
                "default_entry_count": len(default_entries),
                "default_entries_checked": checked,
                "default_entries_present": present,
                "default_entries": checked_entries,
            }
        )
    if "topic_authority" in raw:
        summary["topic_authority_count"] = authority_registry_topic_count(raw.get("topic_authority"))
    if "project_materials" in raw:
        summary.update(authority_registry_project_material_stats(raw.get("project_materials")))
    if "deprecated_sources" in raw:
        summary["deprecated_source_count"] = authority_registry_deprecated_count(raw.get("deprecated_sources"))
    return summary


def authority_registry_summary(goal: dict[str, Any] | None) -> dict[str, Any]:
    compact = compact_authority_registry(goal, project=None)
    compact.pop("default_entries", None)
    return compact


def goal_authority_registry_summary(goal: dict[str, Any] | None) -> dict[str, Any]:
    return authority_registry_summary(goal)
