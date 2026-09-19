"""Deterministic active-Todo Markdown projection.

The user/agent Todo sections are machine-owned after authority promotion.
Everything outside those sections remains opaque human-authored Markdown and
is preserved byte-for-byte. Canonical provider records remain the truth; the
readable Markdown syntax is regenerated and then parsed back for parity.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..coordination.coordination_state_contract import (
    TODO_CANONICAL_READ_RECORD_FIELDS,
    TODO_CANONICAL_REQUIRED_READ_FIELDS,
    TODO_DOMAIN_ITEM_SCHEMA_VERSION,
    TODO_DOMAIN_RECORD_FIELDS,
    TODO_DOMAIN_REQUIRED_FIELDS,
    TODO_ITEM_SCHEMA_VERSION,
    canonical_record_fields,
)
from .active_state_editing import (
    COMPLETED_WORK_ARCHIVE_HEADING,
    TODO_SECTION_HEADINGS,
    archive_section_bounds,
    todo_blocks,
)
from .machine_region import todo_region_marker
from .projection_document import TodoProjectionDocument
from .todo_block_codec import decode_todo_blocks
from .completion_validation_projection import (
    completion_validation_declaration,
    completion_validation_declaration_sha256,
    project_completion_validation_authority,
)
from .active_state_todo_parser import parse_active_state_todos
from .contract import (
    TODO_DECISION_SCOPE_SCHEMA_VERSION,
    TODO_METADATA_FIELDS,
    TODO_STATUS_OPEN,
    format_todo_metadata_line,
    normalize_todo_id,
    normalize_todo_status,
    require_todo_decision_scope,
    todo_marker_for_status,
)
from .todo_summary import canonical_todo_read_record, todo_priority_parts, normalize_todo_text


TODO_SECTION_PROJECTION_SCHEMA_VERSION = "loopx_todo_section_projection_v0"
_MARKER_PATTERN = re.compile(
    r"(?m)^<!-- loopx:todo-section-projection-v0 "
    r"role=(?P<role>user|agent|archive) "
    r"provider_revision=(?P<revision>[A-Za-z0-9_.:-]+) "
    r"records_sha256=(?P<digest>[a-f0-9]{64}) -->\r?$"
)


class TodoSectionProjectionError(ValueError):
    """Canonical records or Markdown violate the projection contract."""


@dataclass(frozen=True)
class TodoSectionProjectionResult:
    markdown: str
    changed: bool
    source_sha256: str
    rendered_sha256: str
    narrative_sha256: str
    provider_revision: str
    todo_count: int
    section_record_sha256: dict[str, str]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_records(records: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    canonical: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, value in enumerate(records):
        native = value.get("schema_version") == TODO_DOMAIN_ITEM_SCHEMA_VERSION
        record = canonical_record_fields(
            value,
            fields=(
                TODO_DOMAIN_RECORD_FIELDS
                if native
                else TODO_CANONICAL_READ_RECORD_FIELDS
            ),
            required_fields=(
                TODO_DOMAIN_REQUIRED_FIELDS
                if native
                else TODO_CANONICAL_REQUIRED_READ_FIELDS
            ),
            label=f"canonical Todo projection record {index}",
            reject_unknown=True,
        )
        todo_id = str(record.get("todo_id") or "")
        if todo_id in seen:
            raise TodoSectionProjectionError(f"duplicate canonical Todo id: {todo_id}")
        seen.add(todo_id)
        if record.get("role") not in TODO_SECTION_HEADINGS:
            raise TodoSectionProjectionError(f"Todo {todo_id!r} has invalid role")
        if record.get("archive_state") not in {"active", "archive"}:
            raise TodoSectionProjectionError(
                f"Todo {todo_id!r} has an unsupported archive state"
            )
        _validate_projection_decision_scope(record, todo_id=todo_id)
        canonical_todo_read_record(
            {
                **record,
                "schema_version": TODO_ITEM_SCHEMA_VERSION,
                "source_section": (
                    COMPLETED_WORK_ARCHIVE_HEADING
                    if record.get("archive_state") == "archive"
                    else TODO_SECTION_HEADINGS[str(record["role"])]
                ),
                "index": record.get("index", index + 1),
            },
            reject_unknown=True,
        )
        canonical.append(record)
    return canonical


def _validate_projection_decision_scope(
    record: Mapping[str, object],
    *,
    todo_id: str,
) -> None:
    """Reject explicit scope data that the Markdown projection cannot preserve."""

    if "decision_scope" not in record:
        return
    value = record["decision_scope"]
    if not isinstance(value, Mapping):
        raise TodoSectionProjectionError(
            f"Todo {todo_id!r} decision_scope must be an object"
        )
    allowed_fields = {
        "schema_version",
        "kind",
        "granularity",
        "scope_key",
        "decision_id",
    }
    unknown_fields = sorted(str(field) for field in set(value) - allowed_fields)
    if unknown_fields:
        raise TodoSectionProjectionError(
            f"Todo {todo_id!r} decision_scope has unsupported fields: "
            + ", ".join(unknown_fields)
        )
    if (
        "schema_version" in value
        and value["schema_version"] != TODO_DECISION_SCOPE_SCHEMA_VERSION
    ):
        raise TodoSectionProjectionError(
            f"Todo {todo_id!r} decision_scope.schema_version must be "
            f"{TODO_DECISION_SCOPE_SCHEMA_VERSION!r} when present"
        )
    if "decision_id" in value:
        decision_id = value["decision_id"]
        if (
            not isinstance(decision_id, str)
            or normalize_todo_id(decision_id) != decision_id
        ):
            raise TodoSectionProjectionError(
                f"Todo {todo_id!r} decision_scope.decision_id must be a "
                "public-safe Todo id"
            )
        raise TodoSectionProjectionError(
            f"Todo {todo_id!r} decision_scope.decision_id cannot be represented "
            "by the current Markdown projection"
        )
    try:
        require_todo_decision_scope(value)
    except ValueError as exc:
        raise TodoSectionProjectionError(
            f"Todo {todo_id!r} has invalid decision_scope: {exc}"
        ) from exc


def _record_sort_key(record: Mapping[str, object]) -> tuple[int, str]:
    raw_index = record.get("index")
    try:
        index = int(raw_index) if isinstance(raw_index, (str, int)) else 2**31 - 1
    except (TypeError, ValueError):
        index = 2**31 - 1
    return index, str(record.get("todo_id") or "")


def _render_record(
    record: Mapping[str, object],
    *,
    include_role: bool = False,
    private_validation: Mapping[str, object] | None = None,
) -> list[str]:
    status = normalize_todo_status(record.get("status")) or TODO_STATUS_OPEN
    text = " ".join(str(record.get("text") or "").strip().split())
    if not text:
        raise TodoSectionProjectionError(
            f"Todo {record.get('todo_id')!r} has empty projection text"
        )
    metadata_values = {
        field: record[field]
        for field in TODO_METADATA_FIELDS
        if field in record and record[field] is not None
    }
    if private_validation is not None:
        metadata_values.update(private_validation)
    if not include_role:
        metadata_values.pop("role", None)
    metadata = format_todo_metadata_line(**metadata_values)
    return [
        f"- [{todo_marker_for_status(status)}] {text}",
        *([metadata] if metadata else []),
    ]


def _render_section(
    *,
    role: str,
    records: list[dict[str, object]],
    provider_revision: str,
    newline: str,
    private_validation: Mapping[str, Mapping[str, object]],
) -> tuple[str, str]:
    digest = _sha256_text(_canonical_json(records))
    heading = (
        COMPLETED_WORK_ARCHIVE_HEADING
        if role == "archive"
        else TODO_SECTION_HEADINGS[role]
    )
    lines = [
        f"## {heading}",
        todo_region_marker(role, "begin"),
        f"<!-- loopx:todo-section-projection-v0 role={role} "
        f"provider_revision={provider_revision} records_sha256={digest} -->",
        "",
    ]
    for record in records:
        lines.extend(_render_record(
            record,
            include_role=role == "archive",
            private_validation=private_validation.get(str(record.get("todo_id") or "")),
        ))
    lines.append(todo_region_marker(role, "end"))
    lines.append("")
    return newline.join(lines), digest


def _parsed_active_records(markdown: str) -> list[dict[str, Any]]:
    fields = parse_active_state_todos(markdown, item_limit=None)
    records: list[dict[str, Any]] = []
    for role in TODO_SECTION_HEADINGS:
        summary = fields.get(f"{role}_todos")
        items = summary.get("items") if isinstance(summary, dict) else []
        for item in sorted(items or [], key=_record_sort_key):
            if isinstance(item, dict) and item.get("archive_state") == "active":
                records.append(canonical_todo_read_record(item, reject_unknown=False))
    return records


def _parsed_archive_records(markdown: str) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    bounds = archive_section_bounds(lines)
    if bounds is None:
        return []
    records: list[dict[str, Any]] = []
    for item in todo_blocks(
        lines,
        bounds[0],
        bounds[1],
        source_section=COMPLETED_WORK_ARCHIVE_HEADING,
    ):
        if item.get("role") not in TODO_SECTION_HEADINGS:
            raise TodoSectionProjectionError(
                f"archived Todo {item.get('todo_id')!r} omits its source role"
            )
        priority, title = todo_priority_parts(str(item.get("text") or ""))
        if priority:
            item.update(priority=priority, title=normalize_todo_text(title))
        records.append(
            canonical_todo_read_record(
                project_completion_validation_authority({
                    **item,
                    "schema_version": TODO_ITEM_SCHEMA_VERSION,
                    "archive_state": "archive",
                    "source_section": COMPLETED_WORK_ARCHIVE_HEADING,
                }),
                reject_unknown=False,
            )
        )
    return records


def _projection_record(
    record: Mapping[str, object],
    *,
    display_index: int,
) -> dict[str, object]:
    projected = dict(canonical_todo_read_record(
        {
            **record,
            "schema_version": TODO_ITEM_SCHEMA_VERSION,
            "source_section": (
                COMPLETED_WORK_ARCHIVE_HEADING
                if record.get("archive_state") == "archive"
                else TODO_SECTION_HEADINGS[str(record["role"])]
            ),
            # Stored import ordinals determine ordering; the new display has its own contiguous ordinals.
            "index": display_index,
        },
        reject_unknown=True,
    ))
    if "decision_scope" in projected:
        # The Markdown codec spells out the optional scope schema version on
        # readback. Normalize that display representation, never the provider
        # record or its digest, before comparing the same semantic scope.
        projected["decision_scope"] = require_todo_decision_scope(projected["decision_scope"])
    # Native authors may retain only the full text. The permanent Markdown
    # reader derives its title/priority; compare that same display view without
    # inventing persisted domain fields or masking an explicit disagreement.
    priority, title = todo_priority_parts(str(projected.get("text") or ""))
    if priority:
        projected.setdefault("priority", priority)
        projected.setdefault("title", normalize_todo_text(title))
    return projected


_DERIVED_READ_MODEL_FIELDS = {
    "created_by",
    "last_actor_agent_id",
    "resume_condition",
    "resume_ready",
    "handoff_note",
}


def _private_validation_metadata(
    document: TodoProjectionDocument,
) -> dict[str, tuple[dict[str, Any], dict[str, object]]]:
    lines = [line.rstrip("\r\n") for line in document.lines]
    private: dict[str, tuple[dict[str, Any], dict[str, object]]] = {}
    for region in document.regions:
        for item in decode_todo_blocks(lines, region.start, region.body_end, visible=document.visible):
            todo_id = str(item.get("todo_id") or "")
            declaration = completion_validation_declaration(item)
            if not todo_id or declaration is None:
                continue
            private[todo_id] = _private_validation_entry(declaration)
    return private


def _private_validation_entry(
    declaration: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, object]]:
    normalized = completion_validation_declaration(dict(declaration))
    if normalized is None:
        raise TodoSectionProjectionError("private validation declaration is empty")
    metadata: dict[str, object] = {
        key: value for key, value in normalized.items() if value is not None
    }
    argv = metadata.get("validation_command_argv")
    if isinstance(argv, list):
        metadata["validation_command_argv"] = json.dumps(
            argv,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return normalized, metadata


def _parity_records(records: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            field: record[field]
            for field in TODO_CANONICAL_READ_RECORD_FIELDS
            if field in record
            and field not in _DERIVED_READ_MODEL_FIELDS
            and record[field] != []
        }
        for record in records
    ]


def render_canonical_todo_sections(
    markdown: str,
    records: Sequence[Mapping[str, object]],
    *,
    provider_revision: str,
    private_validation_declarations: Mapping[str, Mapping[str, Any]] | None = None,
) -> TodoSectionProjectionResult:
    """Replace only Todo sections and verify deterministic parse/render parity."""

    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", provider_revision):
        raise TodoSectionProjectionError("provider_revision must be a public-safe token")
    canonical = _canonical_records(records)
    try:
        source_document = TodoProjectionDocument.parse(markdown)
    except ValueError as error:
        raise TodoSectionProjectionError(str(error)) from error
    private_source = _private_validation_metadata(source_document)
    for todo_id, declaration in (private_validation_declarations or {}).items():
        external = _private_validation_entry(declaration)
        existing = private_source.get(todo_id)
        if existing is not None and (
            completion_validation_declaration_sha256(existing[0])
            != completion_validation_declaration_sha256(external[0])
        ):
            raise TodoSectionProjectionError(
                f"Todo {todo_id!r} has divergent private validation declarations"
            )
        private_source[todo_id] = external
    private_validation: dict[str, Mapping[str, object]] = {}
    for record in canonical:
        todo_id = str(record.get("todo_id") or "")
        required = record.get("completion_validation_required") is True
        digest = record.get("completion_validation_sha256")
        if not required:
            if digest is not None:
                raise TodoSectionProjectionError(
                    f"Todo {todo_id!r} has a validation digest without authority"
                )
            continue
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise TodoSectionProjectionError(
                f"Todo {todo_id!r} requires validation but omits its declaration digest"
            )
        source = private_source.get(todo_id)
        if (
            source is None
            or completion_validation_declaration_sha256(source[0]) != digest
        ):
            raise TodoSectionProjectionError(
                f"Todo {todo_id!r} private validation declaration does not match authority"
            )
        private_validation[todo_id] = source[1]
    by_role = {
        role: sorted(
            [
                record
                for record in canonical
                if record.get("role") == role
                and record.get("archive_state") == "active"
            ],
            key=_record_sort_key,
        )
        for role in TODO_SECTION_HEADINGS
    }
    archived = sorted(
        [record for record in canonical if record.get("archive_state") == "archive"],
        key=_record_sort_key,
    )
    newline = "\r\n" if "\r\n" in markdown else "\n"
    rendered_sections: dict[str, str] = {}
    section_digests: dict[str, str] = {}
    for role in TODO_SECTION_HEADINGS:
        rendered_sections[role], section_digests[role] = _render_section(
            role=role,
            records=by_role[role],
            provider_revision=provider_revision,
            newline=newline,
            private_validation=private_validation,
        )
    if archived or "archive" in source_document.spans:
        rendered_sections["archive"], section_digests["archive"] = _render_section(
            role="archive",
            records=archived,
            provider_revision=provider_revision,
            newline=newline,
            private_validation=private_validation,
        )

    try:
        rendered = source_document.replace(rendered_sections)
        rendered_document = TodoProjectionDocument.parse(rendered)
    except ValueError as error:
        raise TodoSectionProjectionError(str(error)) from error
    before_narrative = source_document.narrative
    after_narrative = rendered_document.narrative
    if before_narrative != after_narrative:
        raise TodoSectionProjectionError("render changed Markdown outside Todo sections")

    expected_records = [
        _projection_record(record, display_index=index)
        for records_for_section in (by_role["user"], by_role["agent"], archived)
        for index, record in enumerate(records_for_section, 1)
    ]
    expected = _parity_records(expected_records)
    # Validate the generated payload, independently of unrelated document text.
    rendered_payload = "\n".join(rendered_sections.values())
    actual = _parity_records(
        [
            *_parsed_active_records(rendered_payload),
            *_parsed_archive_records(rendered_payload),
        ]
    )
    if _canonical_json(actual) != _canonical_json(expected):
        raise TodoSectionProjectionError("Todo section parse/render parity mismatch")
    second = rendered_document.replace(rendered_sections)
    if second != rendered:
        raise TodoSectionProjectionError("Todo section projection is not idempotent")

    return TodoSectionProjectionResult(
        markdown=rendered,
        changed=rendered != markdown,
        source_sha256=_sha256_text(markdown),
        rendered_sha256=_sha256_text(rendered),
        narrative_sha256=_sha256_text(after_narrative),
        provider_revision=provider_revision,
        todo_count=len(canonical),
        section_record_sha256=section_digests,
    )


def inspect_todo_section_projection(markdown: str) -> dict[str, object]:
    """Return compact marker diagnostics without treating Markdown as truth."""

    document = TodoProjectionDocument.parse(markdown)
    markers = []
    for region in document.regions:
        for line in document.lines[region.start:region.body_end]:
            match = _MARKER_PATTERN.fullmatch(line.rstrip("\r\n"))
            if match is not None and match["role"] == region.role:
                markers.append(match.groupdict())
    return {
        "schema_version": TODO_SECTION_PROJECTION_SCHEMA_VERSION,
        "section_count": len(markers),
        "sections": markers,
    }


__all__ = [
    "TODO_SECTION_PROJECTION_SCHEMA_VERSION",
    "TodoSectionProjectionError",
    "TodoSectionProjectionResult",
    "inspect_todo_section_projection",
    "render_canonical_todo_sections",
]
