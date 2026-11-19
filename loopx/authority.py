from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import hashlib
from pathlib import Path
import re
from typing import cast
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

Payload = dict[str, object]
PayloadView = Mapping[str, object]

AUTHORITY_SOURCE_REGISTRATION_VERSION = "authority_source_registration_v0"
DOC_REGISTRY_AUTHORITY_IMPORT_VERSION = "doc_registry_authority_import_v0"
AUTHORITY_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
AUTHORITY_SOURCE_BOUNDARIES = {"public", "local_private", "private_redacted"}
# Owned by loopx.public_safe_text so every real validator owner shares one
# contract; re-exported here because callers already import this name.
PRIVATE_TEXT_PATTERNS = SHARED_PRIVATE_TEXT_PATTERNS

_INT_SUMMARY_FIELDS = (
    "default_entry_count",
    "default_entries_checked",
    "default_entries_present",
    "topic_authority_count",
    "project_material_count",
    "project_material_repository_count",
    "project_material_owner_review_required_count",
    "project_material_stale_count",
    "project_material_current_authority_count",
)
AUTHORITY_REGISTRY_SUMMARY_FIELDS = (
    "declared",
    "required",
    "path",
    "path_exists",
    "read_status",
    *_INT_SUMMARY_FIELDS,
    "deprecated_source_count",
    "conflict_risk",
)
AUTHORITY_REGISTRY_CANONICAL_FIELDS = (
    "default_entry_docs",
    "topic_authority",
    "project_materials",
    "deprecated_sources",
)
_COMPACT_SOURCE_FIELDS = (
    "schema_version", "id", "role", "source_kind", "freshness", "boundary",
    "source_ref_kind", "source_ref_sha256", "source_ref_redacted", "owner_status",
    "gate_status", "revision", "conflict_rule", "registered_at",
    "default_entry_count", "topic_authority_count", "imported_topic_count",
)
_COMMON_MARKDOWN_ENTRY_FIELDS = ("source_ref_redacted", "source_ref_kind", "boundary", "freshness")


def _dict_view(value: object) -> PayloadView:
    return cast(PayloadView, value) if isinstance(value, dict) else {}


def _int_value(value: object) -> int:
    if not value:
        return 0
    return int(value) if isinstance(value, (int, float, str, bytes, bytearray)) else int(str(value))


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


def find_goal_index(registry: PayloadView, goal_id: str) -> int:
    goals = registry.get("goals")
    if not isinstance(goals, list):
        raise ValueError("registry goals must be a list")
    for index, goal in enumerate(goals):
        if isinstance(goal, dict) and str(goal.get("id") or "") == goal_id:
            return index
    raise ValueError(f"goal id not found in registry: {goal_id}")


def normalize_project_materials(value: object) -> dict[str, Payload]:
    if isinstance(value, dict):
        return {str(key): cast(Payload, item) for key, item in value.items() if isinstance(item, dict)}
    if isinstance(value, list):
        result: dict[str, Payload] = {}
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("source_id") or f"source_{index + 1}")
            result[item_id] = cast(Payload, dict(item))
        return result
    return {}


def normalize_topic_authority(value: object) -> Payload:
    return {str(key): item for key, item in value.items()} if isinstance(value, dict) else {}


def authority_registry_has_canonical_fields(raw: PayloadView) -> bool:
    """Return whether a registry carries source data richer than a summary."""

    return any(key in raw for key in AUTHORITY_REGISTRY_CANONICAL_FIELDS)


def apply_authority_registry_summary(authority_registry: Payload, summary: PayloadView) -> None:
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
) -> Payload:
    if boundary not in AUTHORITY_SOURCE_BOUNDARIES:
        raise ValueError(f"boundary must be one of: {', '.join(sorted(AUTHORITY_SOURCE_BOUNDARIES))}")
    source_id = validate_authority_source_id(source_id)
    required_fields: dict[str, str] = {}
    for field_name, raw_value in (("source_kind", source_kind), ("role", role), ("freshness", freshness)):
        cleaned = public_safe_optional(field_name, raw_value) or ""
        if not cleaned:
            raise ValueError(f"{field_name} is required")
        required_fields[field_name] = cleaned

    entry: Payload = {
        "schema_version": AUTHORITY_SOURCE_REGISTRATION_VERSION,
        "id": source_id,
        "role": required_fields["role"],
        "source_kind": required_fields["source_kind"],
        "freshness": required_fields["freshness"],
        "boundary": boundary,
        "source_ref_redacted": bool(source_ref),
        "registered_at": registered_at,
    }
    optional_fields = (
        ("source_ref_kind", source_ref_kind(source_ref)),
        ("source_ref_sha256", source_ref_digest(source_ref)),
        ("owner_status", public_safe_optional("owner_status", owner_status)),
        ("gate_status", public_safe_optional("gate_status", gate_status)),
        ("revision", public_safe_optional("revision", revision)),
        ("conflict_rule", public_safe_optional("conflict_rule", conflict_rule)),
    )
    for key, value in optional_fields:
        if value:
            entry[key] = value
    return entry


def _render_authority_markdown(
    title: str,
    payload: PayloadView,
    *,
    entry_fields: tuple[str, ...],
    include_registry: bool = False,
) -> str:
    lines = [
        f"# {title}",
        "",
        *(f"- {field}: `{payload.get(field)}`" for field in ("ok", "dry_run", "written", "goal_id", "source_id")),
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)
    entry = _dict_view(payload.get("entry"))
    summary = _dict_view(payload.get("authority_registry_summary"))
    global_sync = _dict_view(payload.get("global_sync"))
    if include_registry:
        lines.append(f"- registry: `{payload.get('registry')}`")
    lines.extend(f"- {field}: `{entry.get(field)}`" for field in entry_fields)
    lines.extend(
        [
            f"- authority_registry_path: `{payload.get('authority_registry_path')}`",
            f"- project_material_count: `{summary.get('project_material_count') if summary else None}`",
            f"- global_sync_wrote: `{global_sync.get('wrote') if global_sync else None}`",
            "",
            "## Write Effect",
            str(payload.get("write_effect") or ""),
        ]
    )
    return "\n".join(lines)


def render_authority_source_markdown(payload: PayloadView) -> str:
    return _render_authority_markdown(
        "LoopX Authority Source Registration",
        payload,
        entry_fields=(*_COMMON_MARKDOWN_ENTRY_FIELDS, "owner_status", "gate_status"),
        include_registry=True,
    )


def _strip_yaml_scalar(value: str) -> str:
    text = value.strip()
    if not text or text in {">", ">-", "|", "|-"}:
        return ""
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    return text


def read_doc_registry_contract(doc_registry_path: Path) -> Payload:
    """Read the public-safe contract surface from a DOC_REGISTRY-like YAML file."""

    path = doc_registry_path.expanduser()
    text = path.read_text(encoding="utf-8")
    default_entry_docs: list[str] = []
    topic_authority: dict[str, str] = {}
    result: Payload = {
        "version": None,
        "updated_at": None,
        "default_entry_docs": default_entry_docs,
        "topic_authority": topic_authority,
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
                default_entry_docs.append(entry)
            continue
        if current_section == "topic_authority" and ":" in stripped:
            key, raw_value = stripped.split(":", 1)
            topic = _strip_yaml_scalar(key)
            authority = _strip_yaml_scalar(raw_value)
            if topic and authority:
                topic_authority[topic] = authority
            continue
        if in_status_definitions and indent > 0 and ":" in stripped:
            key, _raw_value = stripped.split(":", 1)
            if _strip_yaml_scalar(key):
                status_definition_count += 1
    result["status_definition_count"] = status_definition_count
    return result


def render_doc_registry_authority_import_markdown(payload: PayloadView) -> str:
    return _render_authority_markdown(
        "LoopX Doc Registry Authority Import",
        payload,
        entry_fields=(*_COMMON_MARKDOWN_ENTRY_FIELDS, "default_entry_count", "topic_authority_count", "imported_topic_count"),
    )


def _apply_authority_entry(
    registry: Payload,
    *,
    goal_id: str,
    entry: Payload,
    registered_at: str,
    imported_topics: dict[str, str] | None = None,
) -> tuple[Payload, Payload | None]:
    goal_index = find_goal_index(registry, goal_id)
    goals = registry.get("goals")
    if not isinstance(goals, list) or not isinstance(goals[goal_index], dict):
        raise ValueError("registry goal entry must be an object")
    goal = cast(Payload, goals[goal_index])

    raw_authority_registry = goal.get("authority_registry")
    authority_registry: Payload = (
        {str(key): value for key, value in raw_authority_registry.items()}
        if isinstance(raw_authority_registry, dict)
        else {}
    )
    source_key = str(entry["id"])
    materials = normalize_project_materials(authority_registry.get("project_materials"))
    previous_entry = materials.get(source_key)
    materials[source_key] = dict(entry)
    authority_registry["project_materials"] = materials
    authority_registry.setdefault("read_status", "registered")
    if imported_topics:
        topic_map = normalize_topic_authority(authority_registry.get("topic_authority"))
        topic_map.update(imported_topics)
        authority_registry["topic_authority"] = topic_map
    goal["authority_registry"] = authority_registry

    compact_source = {key: entry[key] for key in _COMPACT_SOURCE_FIELDS if key in entry}
    authority_sources = goal.get("authority_sources")
    if not isinstance(authority_sources, list):
        authority_sources = []
    goal["authority_sources"] = [
        item
        for item in authority_sources
        if not (isinstance(item, dict) and str(item.get("id") or item.get("source_id") or "") == source_key)
    ] + [compact_source]

    registry["updated_at"] = registered_at
    project = Path(str(goal["repo"])).expanduser() if goal.get("repo") else None
    summary = compact_authority_registry(goal, project=project)
    summary.pop("default_entries", None)
    apply_authority_registry_summary(authority_registry, summary)
    return summary, previous_entry


def _commit_authority_entry(
    *,
    registry_path: Path,
    goal_id: str,
    entry: Payload,
    registered_at: str,
    imported_topics: dict[str, str],
    dry_run: bool,
    dry_run_operation: str,
    mutate_operation: str,
    write_effect: str,
    raw_ref_field: str,
    extra: Payload | None = None,
) -> Payload:
    def reduce(registry: Payload) -> tuple[Payload, Payload | None]:
        return _apply_authority_entry(
            registry, goal_id=goal_id, entry=entry,
            registered_at=registered_at, imported_topics=imported_topics,
        )

    if dry_run:
        registry = load_project_registry(registry_path)
        require_runtime_compatible_project_registry(registry, operation=dry_run_operation)
        summary, previous_entry = reduce(copy.deepcopy(registry))
    else:
        summary, previous_entry = mutate_project_registry(registry_path, operation=mutate_operation, reducer=reduce)

    if previous_entry:
        write_effect += "; previous entry with the same source_id is replaced"
    payload: Payload = {
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
        raw_ref_field: False,
    }
    if extra is not None:
        payload.update(extra)
    return payload


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
) -> Payload:
    registry_path = registry_path.expanduser()
    registered_at = now_local()
    entry = compact_registered_authority_source(
        source_id=source_id, source_ref=source_ref, source_kind=source_kind, role=role,
        freshness=freshness, owner_status=owner_status, gate_status=gate_status,
        boundary=boundary, revision=revision, conflict_rule=conflict_rule, registered_at=registered_at,
    )

    imported_topics: dict[str, str] = {}
    if topic:
        topic_text = public_safe_optional("topic", topic)
        if topic_text:
            imported_topics[topic_text] = str(entry["id"])

    action = "would update" if dry_run else "updated"
    write_effect = (
        f"{action} goal `{goal_id}` authority_registry.project_materials[{entry['id']}] "
        "with a redacted source reference digest; raw source_ref is not stored"
    )
    return _commit_authority_entry(
        registry_path=registry_path, goal_id=goal_id, entry=entry,
        registered_at=registered_at, imported_topics=imported_topics, dry_run=dry_run,
        dry_run_operation="authority source registration", mutate_operation="register_authority_source",
        write_effect=write_effect, raw_ref_field="raw_source_ref_stored",
    )


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
    topics: Sequence[str],
    import_topic_prefix: str | None,
    max_imported_topics: int,
    dry_run: bool,
) -> Payload:
    if max_imported_topics < 0:
        raise ValueError("max_imported_topics must be non-negative")
    registry_path = registry_path.expanduser()
    doc_registry_path = doc_registry_path.expanduser()
    contract = read_doc_registry_contract(doc_registry_path)
    registered_at = now_local()
    entry = compact_registered_authority_source(
        source_id=source_id, source_ref=str(doc_registry_path), source_kind=source_kind, role=role,
        freshness=freshness, owner_status=owner_status, gate_status=gate_status, boundary=boundary,
        revision=revision or str(contract.get("updated_at") or ""), conflict_rule=conflict_rule,
        registered_at=registered_at,
    )
    entry["schema_version"] = DOC_REGISTRY_AUTHORITY_IMPORT_VERSION
    raw_default_entries = contract.get("default_entry_docs")
    default_entries = [str(item) for item in raw_default_entries] if isinstance(raw_default_entries, list) else []
    topic_authority = _dict_view(contract.get("topic_authority"))
    source_key = str(entry["id"])
    imported_topics: dict[str, str] = {}
    if import_topic_prefix:
        prefix = public_safe_optional("import_topic_prefix", import_topic_prefix) or ""
        for topic in list(topic_authority.keys())[:max_imported_topics]:
            local_topic = f"{prefix}{topic}"
            public_safe_optional("imported_topic", local_topic)
            imported_topics[local_topic] = source_key
    for topic in topics:
        topic_text = public_safe_optional("topic", topic)
        if topic_text:
            imported_topics[topic_text] = source_key
    entry.update(
        {
            "registry_version": contract.get("version"),
            "registry_updated_at": contract.get("updated_at"),
            "default_entry_count": len(default_entries),
            "topic_authority_count": len(topic_authority),
            "status_definition_count": _int_value(contract.get("status_definition_count")),
            "default_entry_sample": default_entries[:8],
            "topic_sample": list(topic_authority.keys())[:12],
            "imported_topic_count": len(imported_topics),
            "imported_topic_prefix": import_topic_prefix,
            "imported_topic_truncated": bool(import_topic_prefix and len(topic_authority) > max_imported_topics),
        }
    )

    action = "would import" if dry_run else "imported"
    write_effect = (
        f"{action} DOC_REGISTRY summary into goal `{goal_id}` "
        f"authority_registry.project_materials[{entry['id']}] with "
        f"{len(default_entries)} default entries and {len(topic_authority)} topic mappings; "
        "raw doc_registry_path is hashed and not stored"
    )
    return _commit_authority_entry(
        registry_path=registry_path, goal_id=goal_id, entry=entry,
        registered_at=registered_at, imported_topics=imported_topics, dry_run=dry_run,
        dry_run_operation="authority registry import", mutate_operation="import_doc_registry_authority",
        write_effect=write_effect, raw_ref_field="raw_doc_registry_path_stored",
        extra={"imported_topics": imported_topics},
    )


def _path_text(value: object) -> str | None:
    if not value:
        return None
    text = str(value)
    return text or None


def _resolve_project_path(project: Path | None, path_text: str | None) -> Path | None:
    if not path_text or project is None:
        return None
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else project / path


def _entry_path(value: object) -> str | None:
    if isinstance(value, str):
        return _path_text(value)
    if isinstance(value, dict):
        return _path_text(value.get("path") or value.get("doc") or value.get("file"))
    return None


def authority_registry_required(goal: PayloadView | None) -> bool:
    if not goal:
        return False
    return bool(goal.get("requires_authority_registry") or goal.get("authority_registry_required"))


def authority_registry_default_entries(raw_entries: object) -> list[str]:
    values = raw_entries if isinstance(raw_entries, list) else raw_entries.values() if isinstance(raw_entries, dict) else ()
    return [path for item in values if (path := _entry_path(item))]


def authority_registry_topic_count(raw_topics: object) -> int:
    return len(raw_topics) if isinstance(raw_topics, (dict, list)) else 0


def authority_registry_project_material_stats(raw_materials: object) -> dict[str, int]:
    raw_items = (
        raw_materials.values()
        if isinstance(raw_materials, dict)
        else raw_materials
        if isinstance(raw_materials, list)
        else ()
    )
    items = [cast(PayloadView, item) for item in raw_items if isinstance(item, dict)]

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


def authority_registry_deprecated_count(raw_sources: object) -> int:
    return len(raw_sources) if isinstance(raw_sources, (dict, list)) else 0


def compact_authority_registry(goal: PayloadView | None, *, project: Path | None = None) -> Payload:
    raw = _dict_view(goal.get("authority_registry")) if goal else {}
    path_text = _path_text(raw.get("path")) if raw else None
    resolved_path = _resolve_project_path(project, path_text)
    summary: Payload = {
        "declared": (True if authority_registry_has_canonical_fields(raw) else bool(raw.get("declared"))) if raw else False,
        "required": bool(raw.get("required") or authority_registry_required(goal)) if raw else authority_registry_required(goal),
        "path": path_text,
        "path_exists": (resolved_path.exists() if resolved_path else raw.get("path_exists")) if raw else None,
        "read_status": raw.get("read_status") if raw else None,
        **{field: (_int_value(raw.get(field)) if raw else 0) for field in _INT_SUMMARY_FIELDS},
        "deprecated_source_count": _int_value(raw.get("deprecated_source_count") or raw.get("deprecated_sources_seen")) if raw else 0,
        "conflict_risk": str(raw.get("conflict_risk") or "unknown") if raw else "unknown",
        "default_entries": [],
    }
    if not raw:
        return summary

    if "default_entry_docs" in raw:
        default_entries = authority_registry_default_entries(raw.get("default_entry_docs"))
        checked_entries: list[Payload] = []
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


def authority_registry_summary(goal: PayloadView | None) -> Payload:
    compact = compact_authority_registry(goal, project=None)
    compact.pop("default_entries", None)
    return compact


def goal_authority_registry_summary(goal: PayloadView | None) -> Payload:
    return authority_registry_summary(goal)
