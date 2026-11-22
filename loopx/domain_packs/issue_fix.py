from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TypeGuard

from ..control_plane.content_digest import ENVELOPED_SHA256_PATTERN
from ..domain_state import default_domain_state_file_path, upsert_domain_state_jsonl
from ..file_lock import exclusive_file_lock


ISSUE_FIX_DOMAIN_STATE_LEDGER_FILENAME = "pr-lifecycle.jsonl"
ISSUE_FIX_FEASIBILITY_LEDGER_FILENAME = "feasibility.jsonl"
ISSUE_FIX_CANDIDATE_PREFLIGHT_LEDGER_FILENAME = "candidate-preflight.jsonl"
ISSUE_FIX_REPOSITORY_SNAPSHOT_LEDGER_FILENAME = "repository-snapshots.jsonl"
REVIEWER_NOTIFICATION_RECEIPT_PATTERN = ENVELOPED_SHA256_PATTERN
REVIEWER_NOTIFICATION_QUEUE_RECEIPT_SCHEMA_VERSION = (
    "issue_fix_reviewer_notification_queue_receipt_v1"
)
REVIEWER_NOTIFICATION_LOCAL_TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
REVIEWER_NOTIFICATION_HANDLE_PATTERN = re.compile(r"@?[A-Za-z0-9_.-]{1,100}")

_FEASIBILITY_CAPTURE_BOUNDARY_FIELDS = (
    "issue_body_captured",
    "comment_bodies_captured",
    "response_payloads_captured",
    "raw_logs_captured",
    "local_paths_captured",
)
_PR_LIFECYCLE_CAPTURE_BOUNDARY_FIELDS = (
    "issue_body_captured",
    "comment_bodies_captured",
    "maintainer_correction_body_captured",
    "response_payloads_captured",
    "raw_check_logs_captured",
    "local_paths_captured",
)


def _is_str_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _object_list(value: object) -> Sequence[object]:
    return value if _is_object_list(value) else []


def _require_false_capture_flags(
    payload: Mapping[str, object], fields: Sequence[str]
) -> None:
    for key in fields:
        if payload.get(key) is not False:
            raise ValueError(f"issue-fix domain-state payload must keep {key}=false")


def _ledger_key(
    payload: Mapping[str, object],
    *,
    label: str,
    ref_field: str,
    nested: str | None = None,
) -> dict[str, object]:
    source: Mapping[str, object] = payload
    if nested is not None:
        inner = payload.get(nested)
        if not _is_str_dict(inner):
            raise ValueError(f"{label} must include {nested}")
        source = inner
    repo = str(source.get("repo") or "").strip()
    ref_value = str(source.get(ref_field) or "").strip()
    if not repo or not ref_value:
        raise ValueError(f"{label} must include repo and {ref_field}")
    return {"repo": repo, ref_field: ref_value}


def _read_jsonl_dicts(
    path: Path, *, strict_label: str | None = None
) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (TypeError, ValueError) as exc:
            if strict_label is not None:
                raise ValueError(f"invalid JSONL row {index} in {strict_label}") from exc
            continue
        if _is_str_dict(row):
            rows.append(row)
        elif strict_label is not None:
            raise ValueError(f"{strict_label} row {index} must be an object")
    return rows


def _upsert_issue_fix_payload(
    ledger_path: str | Path,
    payload: dict[str, object],
    *,
    key: dict[str, object],
    existing_key_fn: Callable[[dict[str, object]], dict[str, object] | None],
    unchanged_fn: Callable[[dict[str, object], dict[str, object]], bool] | None = None,
    merge_existing_fn: Callable[
        [dict[str, object], dict[str, object]], dict[str, object]
    ]
    | None = None,
) -> dict[str, object]:
    projection = payload.get("domain_state_projection")
    if not _is_str_dict(projection):
        raise ValueError("issue-fix payload must include domain_state_projection")

    projection.pop("write_result", None)
    projection.pop("write_skipped_reason", None)
    projection["write_performed"] = True
    try:
        result = upsert_domain_state_jsonl(
            ledger_path,
            payload,
            key=key,
            existing_key_fn=existing_key_fn,
            unchanged_fn=unchanged_fn,
            merge_existing_fn=merge_existing_fn,
        )
    except Exception:
        projection["write_performed"] = False
        raise
    if result.get("write_performed") is False:
        projection["write_performed"] = False
        projection["write_skipped_reason"] = "observation_fingerprint_unchanged"
    projection["write_result"] = result
    return result


def issue_fix_feasibility_ledger_key(payload: Mapping[str, object]) -> dict[str, object]:
    return _ledger_key(
        payload,
        label="issue-fix feasibility payload",
        ref_field="issue_ref",
        nested="observation",
    )


def upsert_issue_fix_feasibility_ledger_jsonl(
    ledger_path: str | Path,
    payload: dict[str, object],
) -> dict[str, object]:
    """Upsert one compact issue-fix feasibility decision into domain state."""

    if payload.get("ok") is not True:
        raise ValueError(
            "only successful issue-fix feasibility payloads can be written"
        )
    _require_false_capture_flags(payload, _FEASIBILITY_CAPTURE_BOUNDARY_FIELDS)
    return _upsert_issue_fix_payload(
        ledger_path,
        payload,
        key=issue_fix_feasibility_ledger_key(payload),
        existing_key_fn=issue_fix_feasibility_ledger_key,
    )


def issue_fix_candidate_preflight_ledger_key(
    payload: Mapping[str, object],
) -> dict[str, object]:
    return _ledger_key(
        payload,
        label="issue-fix candidate preflight payload",
        ref_field="issue_ref",
    )


def upsert_issue_fix_candidate_preflight_ledger_jsonl(
    ledger_path: str | Path,
    payload: dict[str, object],
) -> dict[str, object]:
    """Upsert one source-qualified candidate admission receipt."""

    if payload.get("ok") is not True:
        raise ValueError(
            "only successful issue-fix candidate preflight payloads can be written"
        )
    if payload.get("schema_version") != "issue_fix_candidate_preflight_v0":
        raise ValueError(
            "issue-fix candidate preflight payload has an unsupported schema"
        )
    if payload.get("external_writes_performed") is not False:
        raise ValueError(
            "issue-fix candidate preflight payload must not perform external writes"
        )
    if payload.get("raw_provider_payload_captured") is not False:
        raise ValueError(
            "issue-fix candidate preflight payload must not capture raw provider data"
        )
    return _upsert_issue_fix_payload(
        ledger_path,
        payload,
        key=issue_fix_candidate_preflight_ledger_key(payload),
        existing_key_fn=issue_fix_candidate_preflight_ledger_key,
    )


def promote_issue_fix_feasibility_ledger_jsonl(
    ledger_path: str | Path,
    payload: dict[str, object],
    *,
    source_issue_ref: str,
) -> dict[str, object]:
    """Atomically replace a discovered placeholder with one canonical issue row."""

    if payload.get("ok") is not True:
        raise ValueError(
            "only successful issue-fix feasibility payloads can be promoted"
        )
    canonical_key = issue_fix_feasibility_ledger_key(payload)
    source_ref = str(source_issue_ref or "").strip()
    if not source_ref or source_ref == canonical_key["issue_ref"]:
        raise ValueError("source_issue_ref must differ from the canonical issue_ref")
    source_key = {"repo": canonical_key["repo"], "issue_ref": source_ref}
    path = Path(ledger_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path):
        rows: list[dict[str, object]] = []
        source_found = False
        canonical_found = False
        canonical_existing: dict[str, object] | None = None
        for row in _read_jsonl_dicts(path, strict_label="issue-fix feasibility ledger"):
            try:
                row_key = issue_fix_feasibility_ledger_key(row)
            except ValueError:
                rows.append(row)
                continue
            if row_key == source_key:
                source_found = True
                continue
            if row_key == canonical_key:
                canonical_found = True
                canonical_existing = row
                continue
            rows.append(row)
        projection = payload.get("domain_state_projection")
        if _is_str_dict(projection):
            projection["write_performed"] = True
        candidate: dict[str, object] = {**payload, "domain_state_key": canonical_key}
        rows.append(candidate)
        unchanged = bool(
            canonical_found and not source_found and canonical_existing == candidate
        )
        if not unchanged:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f"{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_name = tmp_file.name
                tmp_file.write(
                    "".join(
                        json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
                        for row in rows
                    )
                )
            os.replace(tmp_name, path)
    projection = payload.get("domain_state_projection")
    if _is_str_dict(projection):
        projection["write_performed"] = not unchanged
    return {
        "status": "unchanged" if unchanged else "promoted",
        "write_performed": not unchanged,
        "source_placeholder_removed": source_found,
        "canonical_row_retained": True,
        "duplicate_rows_remaining": 0,
        "row_count": len(rows),
        "path_recorded": False,
    }


def issue_fix_maintainer_correction_entries(
    row: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    """Return the row's maintainer-correction entries; legacy rows read as empty."""

    entries = row.get("maintainer_corrections") if _is_str_dict(row) else None
    return [dict(entry) for entry in _object_list(entries) if _is_str_dict(entry)]


def merge_issue_fix_maintainer_correction_entries(
    existing: Sequence[Mapping[str, object]],
    incoming: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Union entries by operation_id; incoming entries replace same-id existing ones."""

    merged: dict[str, dict[str, object]] = {}
    for entry in [*existing, *incoming]:
        operation_id = str(entry.get("operation_id") or "")
        if not operation_id:
            continue
        merged[operation_id] = {**merged.get(operation_id, {}), **entry}
    return list(merged.values())


def read_issue_fix_pr_lifecycle_ledger_row(
    ledger_path: str | Path, *, repo: str, pr_ref: str
) -> dict[str, object] | None:
    """Return the current ledger row for one PR, or None when absent."""

    key = {"repo": repo, "pr_ref": pr_ref}
    found: dict[str, object] | None = None
    for row in _read_jsonl_dicts(Path(ledger_path).expanduser()):
        row_key = row.get("domain_state_key")
        if row_key is None:
            try:
                row_key = issue_fix_pr_lifecycle_ledger_key(row)
            except ValueError:
                continue
        if row_key == key:
            found = row
    return found


def issue_fix_pr_lifecycle_ledger_key(
    payload: Mapping[str, object],
) -> dict[str, object]:
    return _ledger_key(
        payload,
        label="issue-fix PR lifecycle payload",
        ref_field="pr_ref",
        nested="observation",
    )


def _unchanged_pr_lifecycle_observation(
    existing: dict[str, object],
    incoming: dict[str, object],
) -> bool:
    existing_fingerprint = str(existing.get("observation_fingerprint") or "")
    return bool(
        existing.get("origin_request") == incoming.get("origin_request")
        and existing_fingerprint
        and existing_fingerprint == str(incoming.get("observation_fingerprint") or "")
        and str(existing.get("maintainer_correction_fingerprint") or "")
        == str(incoming.get("maintainer_correction_fingerprint") or "")
        and existing.get("reviewer_notification_receipts")
        == incoming.get("reviewer_notification_receipts")
        and existing.get("reviewer_notification_queue")
        == incoming.get("reviewer_notification_queue")
        and issue_fix_maintainer_correction_entries(existing)
        == issue_fix_maintainer_correction_entries(incoming)
    )


def _preserve_compact_lifecycle_evidence(
    existing: dict[str, object],
    incoming: dict[str, object],
) -> dict[str, object]:
    merged = dict(incoming)
    if existing.get("origin_request"):
        merged["origin_request"] = existing["origin_request"]
    for field in ("reviewer_notification_receipts", "reviewer_notification_queue"):
        prior = existing.get(field)
        if field not in merged and _is_object_list(prior) and prior:
            merged[field] = list(prior)
    first_push_ci = existing.get("first_push_ci")
    if "first_push_ci" not in merged and _is_str_dict(first_push_ci):
        merged["first_push_ci"] = dict(first_push_ci)
    existing_entries = issue_fix_maintainer_correction_entries(existing)
    if existing_entries or "maintainer_corrections" in merged:
        merged["maintainer_corrections"] = (
            merge_issue_fix_maintainer_correction_entries(
                existing_entries, issue_fix_maintainer_correction_entries(merged)
            )
        )
    # Older readers take `maintainer_correction` as the latest accepted
    # correction; a rejected or absent correction must not blank it.
    admission = merged.get("maintainer_correction_admission")
    incoming_accepted = (
        _is_str_dict(admission) and admission.get("disposition") == "accepted"
    )
    prior_correction = existing.get("maintainer_correction")
    if not incoming_accepted and _is_str_dict(prior_correction):
        merged["maintainer_correction"] = dict(prior_correction)
        merged["maintainer_correction_fingerprint"] = existing.get(
            "maintainer_correction_fingerprint"
        )
    return merged


def upsert_issue_fix_pr_lifecycle_ledger_jsonl(
    ledger_path: str | Path,
    payload: dict[str, object],
) -> dict[str, object]:
    """Upsert a compact issue-fix PR lifecycle packet into domain state."""

    if payload.get("ok") is not True:
        raise ValueError(
            "only successful issue-fix PR lifecycle payloads can be written"
        )
    _require_false_capture_flags(payload, _PR_LIFECYCLE_CAPTURE_BOUNDARY_FIELDS)
    return _upsert_issue_fix_payload(
        ledger_path,
        payload,
        key=issue_fix_pr_lifecycle_ledger_key(payload),
        existing_key_fn=issue_fix_pr_lifecycle_ledger_key,
        unchanged_fn=_unchanged_pr_lifecycle_observation,
        merge_existing_fn=_preserve_compact_lifecycle_evidence,
    )


def persist_issue_fix_reviewer_notification_receipts(
    ledger_path: str | Path,
    payload: dict[str, object],
    receipts: Sequence[str],
) -> dict[str, object]:
    """Persist only verified hashed reviewer-notification receipts in PR state."""

    return persist_issue_fix_reviewer_notification_state(
        ledger_path,
        payload,
        receipts=receipts,
        queued_receipts=[],
    )


def _validated_reviewer_notification_queue_receipt(value: object) -> dict[str, object]:
    if not _is_str_dict(value):
        raise ValueError("reviewer notification queue receipts must be objects")
    key = str(value.get("idempotency_key") or "")
    sink_kind = str(value.get("sink_kind") or "")
    reviewers = value.get("reviewer_handles")
    window = value.get("allowed_local_time")
    summary = str(value.get("message_summary") or "")
    if (
        value.get("schema_version") != REVIEWER_NOTIFICATION_QUEUE_RECEIPT_SCHEMA_VERSION
        or not REVIEWER_NOTIFICATION_RECEIPT_PATTERN.fullmatch(key)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,50}", sink_kind)
        or not _is_object_list(reviewers)
        or not reviewers
        or any(
            not REVIEWER_NOTIFICATION_HANDLE_PATTERN.fullmatch(str(handle))
            for handle in reviewers
        )
        or not _is_str_dict(window)
        or not summary.strip()
        or len(summary) > 240
        or value.get("summary_policy_status")
        not in {"reward_memory_verified", "sink_config"}
        or not REVIEWER_NOTIFICATION_LOCAL_TIME_PATTERN.fullmatch(
            str(window.get("start") or "")
        )
        or not REVIEWER_NOTIFICATION_LOCAL_TIME_PATTERN.fullmatch(
            str(window.get("end") or "")
        )
        or not str(value.get("queued_at") or "").endswith("Z")
        or not str(value.get("not_before") or "").endswith("Z")
        or not str(value.get("timezone") or "").strip()
        or value.get("status") != "queued"
    ):
        raise ValueError("reviewer notification queue receipt is invalid")
    return dict(value)


def persist_issue_fix_reviewer_notification_state(
    ledger_path: str | Path,
    payload: dict[str, object],
    *,
    receipts: Sequence[str],
    queued_receipts: Sequence[Mapping[str, object]],
    replace_queued_receipts: bool = False,
) -> dict[str, object]:
    """Persist verified receipts and restart-safe queued delivery metadata.

    The default preserves append-compatible callers. An authoritative reviewer
    route may replace the unsent queue so stale targets are cancelled when live
    GitHub coverage changes or the current target becomes unavailable.
    """

    # Rows written before a capture-boundary field was introduced may omit it.
    # Receipt persistence is a metadata-only migration, so make that historical
    # absence explicit without weakening the guard for any truthy value.
    for key in _PR_LIFECYCLE_CAPTURE_BOUNDARY_FIELDS:
        payload.setdefault(key, False)

    prior_receipts = _object_list(payload.get("reviewer_notification_receipts"))
    merged_receipts = [
        str(value)
        for value in prior_receipts
        if REVIEWER_NOTIFICATION_RECEIPT_PATTERN.fullmatch(str(value))
    ]
    for receipt in receipts:
        text = str(receipt)
        if not REVIEWER_NOTIFICATION_RECEIPT_PATTERN.fullmatch(text):
            raise ValueError("reviewer notification receipts must be sha256 keys")
        if text not in merged_receipts:
            merged_receipts.append(text)

    prior_queue = _object_list(payload.get("reviewer_notification_queue"))
    prior_queued_by_key: dict[str, dict[str, object]] = {}
    for value in prior_queue:
        try:
            queued = _validated_reviewer_notification_queue_receipt(value)
        except ValueError:
            continue
        prior_queued_by_key[str(queued["idempotency_key"])] = queued
    merged_queue: dict[str, dict[str, object]] = (
        {} if replace_queued_receipts else dict(prior_queued_by_key)
    )
    for value in queued_receipts:
        queued = _validated_reviewer_notification_queue_receipt(value)
        merged_queue[str(queued["idempotency_key"])] = queued
    for key in merged_receipts:
        merged_queue.pop(key, None)
    queue = list(merged_queue.values())
    queue_reconciliation: dict[str, object] = {
        "schema_version": "issue_fix_reviewer_notification_queue_reconciliation_v0",
        "mode": "replace_unsent" if replace_queued_receipts else "merge",
        "prior_count": len(prior_queued_by_key),
        "requested_count": len(queued_receipts),
        "result_count": len(queue),
        "cancelled_count": len(set(prior_queued_by_key) - set(merged_queue)),
    }

    if merged_receipts == prior_receipts and queue == prior_queue:
        return {
            "status": "unchanged",
            "write_performed": False,
            "row_count": None,
            "path_recorded": False,
            "queue_reconciliation": queue_reconciliation,
        }
    payload["reviewer_notification_receipts"] = merged_receipts
    payload["reviewer_notification_queue"] = queue
    result = upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger_path, payload)
    return {**result, "queue_reconciliation": queue_reconciliation}


def _issue_fix_ledger_path(project: str | Path, goal_id: str, filename: str) -> Path:
    return default_domain_state_file_path(
        project=project, goal_id=goal_id, domain_pack="issue_fix", filename=filename
    )


def default_issue_fix_domain_state_ledger_path(
    *, project: str | Path = ".", goal_id: str
) -> Path:
    """Return the project-local domain-state ledger path for issue-fix PRs."""

    return _issue_fix_ledger_path(project, goal_id, ISSUE_FIX_DOMAIN_STATE_LEDGER_FILENAME)


def default_issue_fix_feasibility_ledger_path(
    *, project: str | Path = ".", goal_id: str
) -> Path:
    """Return the project-local issue-fix feasibility ledger path."""

    return _issue_fix_ledger_path(project, goal_id, ISSUE_FIX_FEASIBILITY_LEDGER_FILENAME)


def default_issue_fix_candidate_preflight_ledger_path(
    *, project: str | Path = ".", goal_id: str
) -> Path:
    """Return the project-local issue candidate admission ledger path."""

    return _issue_fix_ledger_path(
        project, goal_id, ISSUE_FIX_CANDIDATE_PREFLIGHT_LEDGER_FILENAME
    )


def default_issue_fix_repository_snapshot_ledger_path(
    *, project: str | Path = ".", goal_id: str
) -> Path:
    return _issue_fix_ledger_path(
        project, goal_id, ISSUE_FIX_REPOSITORY_SNAPSHOT_LEDGER_FILENAME
    )


def retain_issue_fix_repository_snapshot_jsonl(
    ledger_path: str | Path, record: dict[str, object]
) -> dict[str, object]:
    path = Path(ledger_path)
    existing_rows = _read_jsonl_dicts(path)
    repo = str(record.get("repo") or "")
    fingerprint = str(record.get("material_fingerprint") or "")
    latest = next(
        (row for row in reversed(existing_rows) if row.get("repo") == repo), None
    )
    if latest and latest.get("material_fingerprint") == fingerprint:
        return {
            "status": "unchanged",
            "write_performed": False,
            "row_count": len(existing_rows),
            "path_recorded": False,
        }
    return upsert_domain_state_jsonl(
        path,
        record,
        key={"repo": repo, "snapshot_date": record.get("snapshot_date")},
        existing_key_fn=lambda row: {
            "repo": row.get("repo"),
            "snapshot_date": row.get("snapshot_date"),
        },
    )
