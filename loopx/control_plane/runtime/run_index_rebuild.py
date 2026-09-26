from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_run_index_lock
from .run_artifacts import run_file_stem
from .run_index_duplicates import classify_index_duplicate_records, index_identity


COLLISION_REBUILD_PLAN_SCHEMA = "artifact_identity_collision_rebuild_plan_v0"
COLLISION_RECOVERY_ARTIFACT_SCHEMA = "artifact_identity_collision_recovery_v0"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _event_identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "classification",
            "todo_id",
            "target_key",
            "agent_id",
            "material_change",
        )
        if record.get(key) is not None
    }


def read_index_rows(index_path: Path) -> tuple[list[str], list[tuple[int, dict[str, Any]]]]:
    # One JSON document per LF: the writer keeps non-ASCII verbatim, so a value
    # carrying U+0085 must not be treated as a line break here.
    raw_lines = index_path.read_text(encoding="utf-8").split("\n")
    rows: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(raw_lines, start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append((line_number, item))
    return raw_lines, rows


def collision_review_groups(index_path: Path, goal_id: str) -> list[dict[str, Any]]:
    _, rows = read_index_rows(index_path)
    grouped: dict[tuple[str, str, str], list[tuple[int, dict[str, Any]]]] = {}
    for line_number, record in rows:
        grouped.setdefault(index_identity(record), []).append((line_number, record))

    groups: list[dict[str, Any]] = []
    for identity, records in grouped.items():
        if len(records) <= 1:
            continue
        classification = classify_index_duplicate_records(
            [record for _, record in records]
        )
        if classification.get("action") != "blocked_artifact_identity_collision":
            continue
        generated_at, json_path, markdown_path = identity
        groups.append(
            {
                "goal_id": goal_id,
                "index_path": str(index_path),
                "source_identity": {
                    "generated_at": generated_at,
                    "json_path": json_path,
                    "markdown_path": markdown_path,
                },
                "line_numbers": [line_number for line_number, _ in records],
                "rows": [
                    {
                        "line_number": line_number,
                        "row_sha256": _sha256(record),
                        "event_identity": _event_identity(record),
                    }
                    for line_number, record in records
                ],
                "rebuild_contract": "preserve_every_row_with_a_distinct_recovery_artifact",
            }
        )
    return groups


def build_collision_rebuild_plan(
    groups: list[dict[str, Any]],
    *,
    goal_filter: str | None,
    total_collision_group_count: int,
    truncated: bool,
) -> dict[str, Any]:
    plan_body = {
        "schema_version": COLLISION_REBUILD_PLAN_SCHEMA,
        "goal_filter": goal_filter,
        "groups": groups,
        "total_collision_group_count": total_collision_group_count,
        "truncated": truncated,
        "destructive_row_deletion": False,
    }
    return {**plan_body, "plan_sha256": _sha256(plan_body)}


def validate_reviewed_collision_plan(
    reviewed_plan: dict[str, Any],
    current_plan: dict[str, Any],
) -> str:
    if reviewed_plan.get("schema_version") != COLLISION_REBUILD_PLAN_SCHEMA:
        raise ValueError(
            f"review plan must have schema_version={COLLISION_REBUILD_PLAN_SCHEMA}"
        )
    reviewed_body = {
        key: reviewed_plan.get(key)
        for key in (
            "schema_version",
            "goal_filter",
            "groups",
            "total_collision_group_count",
            "truncated",
            "destructive_row_deletion",
        )
    }
    reviewed_digest = _sha256(reviewed_body)
    if reviewed_plan.get("plan_sha256") != reviewed_digest:
        raise ValueError("review plan digest does not match its contents")
    if reviewed_digest != current_plan.get("plan_sha256"):
        raise ValueError(
            "review plan is stale or does not match the current collision groups; regenerate and review it"
        )
    if reviewed_plan.get("destructive_row_deletion") is not False:
        raise ValueError("review plan must explicitly keep destructive_row_deletion=false")
    if reviewed_plan.get("truncated") is not False:
        raise ValueError(
            "review plan is truncated; increase --limit until truncated=false before execution"
        )
    return reviewed_digest


def _write_new_or_verify(path: Path, content: str) -> None:
    encoded = content.encode("utf-8")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if path.read_bytes() != encoded:
            raise ValueError(f"rebuild artifact already exists with different content: {path}")
        return
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded)


def _recovery_markdown(record: dict[str, Any]) -> str:
    recovery = record["artifact_rebuild"]
    event_identity = recovery.get("event_identity") or {}
    lines = [
        "# LoopX Recovered Run Index Event",
        "",
        f"- schema_version: `{COLLISION_RECOVERY_ARTIFACT_SCHEMA}`",
        f"- goal_id: `{record.get('goal_id')}`",
        f"- generated_at: `{record.get('generated_at')}`",
        f"- classification: `{record.get('classification')}`",
        f"- source_line_number: `{recovery.get('source_line_number')}`",
        f"- review_plan_sha256: `{recovery.get('review_plan_sha256')}`",
        "- recovery: `compact index event preserved; ambiguous legacy artifact not claimed`",
    ]
    for key in ("todo_id", "target_key", "agent_id", "material_change"):
        if key in event_identity:
            lines.append(f"- {key}: `{event_identity[key]}`")
    return "\n".join(lines) + "\n"


def apply_reviewed_collision_rebuild(
    current_plan: dict[str, Any],
    *,
    plan_sha256: str,
) -> list[dict[str, Any]]:
    groups_by_index: dict[str, list[dict[str, Any]]] = {}
    for group in current_plan.get("groups") or []:
        groups_by_index.setdefault(str(group.get("index_path") or ""), []).append(group)

    rebuilt_indexes: list[dict[str, Any]] = []
    digest_prefix = plan_sha256[:16]
    for raw_index_path, groups in groups_by_index.items():
        index_path = Path(raw_index_path)
        with exclusive_run_index_lock(
            index_path,
            operation="history_index_collision_rebuild",
        ):
            raw_lines, parsed_rows = read_index_rows(index_path)
            rows_by_line = dict(parsed_rows)
            replacements: dict[int, dict[str, Any]] = {}
            recovery_paths: list[str] = []

            for group in groups:
                source_identity = group.get("source_identity") or {}
                generated_at = str(source_identity.get("generated_at") or "")
                stem = run_file_stem(generated_at)
                group_token = _sha256(source_identity)[:8]
                for ordinal, row_spec in enumerate(group.get("rows") or [], start=1):
                    line_number = int(row_spec["line_number"])
                    source_record = rows_by_line.get(line_number)
                    if source_record is None or _sha256(source_record) != row_spec.get(
                        "row_sha256"
                    ):
                        raise ValueError(
                            f"reviewed collision row changed before rebuild: {index_path}:{line_number}"
                        )
                    recovery_stem = f"{stem}-collision-rebuild-{digest_prefix}-{group_token}-{ordinal}"
                    json_path = index_path.parent / f"{recovery_stem}.json"
                    markdown_path = index_path.parent / f"{recovery_stem}.md"
                    rebuilt_record = dict(source_record)
                    rebuilt_record["json_path"] = str(json_path)
                    rebuilt_record["markdown_path"] = str(markdown_path)
                    rebuilt_record["artifact_rebuild"] = {
                        "schema_version": COLLISION_RECOVERY_ARTIFACT_SCHEMA,
                        "review_plan_sha256": plan_sha256,
                        "source_identity": source_identity,
                        "source_line_number": line_number,
                        "event_identity": row_spec.get("event_identity") or {},
                        "ambiguous_legacy_artifact_claimed": False,
                    }
                    recovery_payload = {
                        "schema_version": COLLISION_RECOVERY_ARTIFACT_SCHEMA,
                        "artifact_rebuild": rebuilt_record["artifact_rebuild"],
                        "index_record": source_record,
                    }
                    _write_new_or_verify(
                        json_path,
                        json.dumps(recovery_payload, ensure_ascii=False, indent=2)
                        + "\n",
                    )
                    _write_new_or_verify(
                        markdown_path, _recovery_markdown(rebuilt_record)
                    )
                    replacements[line_number] = rebuilt_record
                    recovery_paths.extend((str(json_path), str(markdown_path)))

            backup_path = index_path.with_name(
                f"index.pre-collision-rebuild-{digest_prefix}.jsonl"
            )
            original_content = "".join(line + "\n" for line in raw_lines)
            _write_new_or_verify(backup_path, original_content)
            rebuilt_lines = [
                json.dumps(replacements[line_number], ensure_ascii=False)
                if line_number in replacements
                else line
                for line_number, line in enumerate(raw_lines, start=1)
            ]
            tmp_path = index_path.with_name(
                f"index.collision-rebuild-{digest_prefix}.tmp"
            )
            tmp_path.write_text(
                "".join(line + "\n" for line in rebuilt_lines),
                encoding="utf-8",
            )
            tmp_path.replace(index_path)
            rebuilt_indexes.append(
                {
                    "index_path": str(index_path),
                    "backup_path": str(backup_path),
                    "preserved_row_count": len(replacements),
                    "recovery_paths": recovery_paths,
                }
            )
    return rebuilt_indexes
