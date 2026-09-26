from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..domain_state import default_domain_state_file_path, upsert_domain_state_jsonl
from ..file_lock import exclusive_file_lock


ISSUE_FIX_DOMAIN_STATE_LEDGER_FILENAME = "pr-lifecycle.jsonl"
ISSUE_FIX_FEASIBILITY_LEDGER_FILENAME = "feasibility.jsonl"
ISSUE_FIX_CANDIDATE_PREFLIGHT_LEDGER_FILENAME = "candidate-preflight.jsonl"
ISSUE_FIX_REPOSITORY_SNAPSHOT_LEDGER_FILENAME = "repository-snapshots.jsonl"
REVIEWER_NOTIFICATION_RECEIPT_PATTERN = re.compile(r"sha256:[a-f0-9]{64}")
REVIEWER_NOTIFICATION_QUEUE_RECEIPT_SCHEMA_VERSION = (
    "issue_fix_reviewer_notification_queue_receipt_v1"
)
REVIEWER_NOTIFICATION_LOCAL_TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
REVIEWER_NOTIFICATION_HANDLE_PATTERN = re.compile(r"@?[A-Za-z0-9_.-]{1,100}")


def _upsert_issue_fix_payload(
    ledger_path: str | Path,
    payload: dict[str, Any],
    *,
    key: dict[str, Any],
    existing_key_fn: Callable[[dict[str, Any]], dict[str, Any] | None],
    unchanged_fn: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
    merge_existing_fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    | None = None,
) -> dict[str, Any]:
    projection = payload.get("domain_state_projection")
    if not isinstance(projection, dict):
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


def issue_fix_feasibility_ledger_key(payload: dict[str, Any]) -> dict[str, Any]:
    observation = payload.get("observation")
    if not isinstance(observation, dict):
        raise ValueError("issue-fix feasibility payload must include observation")
    repo = str(observation.get("repo") or "").strip()
    issue_ref = str(observation.get("issue_ref") or "").strip()
    if not repo or not issue_ref:
        raise ValueError(
            "issue-fix feasibility payload must include repo and issue_ref"
        )
    return {
        "repo": repo,
        "issue_ref": issue_ref,
    }


def upsert_issue_fix_feasibility_ledger_jsonl(
    ledger_path: str | Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Upsert one compact issue-fix feasibility decision into domain state."""

    if payload.get("ok") is not True:
        raise ValueError(
            "only successful issue-fix feasibility payloads can be written"
        )
    for key in (
        "issue_body_captured",
        "comment_bodies_captured",
        "response_payloads_captured",
        "raw_logs_captured",
        "local_paths_captured",
    ):
        if payload.get(key) is not False:
            raise ValueError(f"issue-fix domain-state payload must keep {key}=false")
    return _upsert_issue_fix_payload(
        ledger_path,
        payload,
        key=issue_fix_feasibility_ledger_key(payload),
        existing_key_fn=issue_fix_feasibility_ledger_key,
    )


def issue_fix_candidate_preflight_ledger_key(
    payload: dict[str, Any],
) -> dict[str, Any]:
    repo = str(payload.get("repo") or "").strip()
    issue_ref = str(payload.get("issue_ref") or "").strip()
    if not repo or not issue_ref:
        raise ValueError(
            "issue-fix candidate preflight payload must include repo and issue_ref"
        )
    return {"repo": repo, "issue_ref": issue_ref}


def upsert_issue_fix_candidate_preflight_ledger_jsonl(
    ledger_path: str | Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
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
    payload: dict[str, Any],
    *,
    source_issue_ref: str,
) -> dict[str, Any]:
    """Atomically replace a discovered placeholder with one canonical issue row."""

    if payload.get("ok") is not True:
        raise ValueError(
            "only successful issue-fix feasibility payloads can be promoted"
        )
    canonical_key = issue_fix_feasibility_ledger_key(payload)
    source_ref = str(source_issue_ref or "").strip()
    if not source_ref or source_ref == canonical_key["issue_ref"]:
        raise ValueError("source_issue_ref must differ from the canonical issue_ref")
    path = Path(ledger_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path):
        rows: list[dict[str, Any]] = []
        source_found = False
        canonical_found = False
        canonical_existing: dict[str, Any] | None = None
        if path.exists():
            for index, line in enumerate(
                path.read_text(encoding="utf-8").split("\n"), start=1
            ):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid JSONL row {index} in issue-fix feasibility ledger"
                    ) from exc
                if not isinstance(row, dict):
                    raise ValueError(
                        f"issue-fix feasibility ledger row {index} must be an object"
                    )
                try:
                    row_key = issue_fix_feasibility_ledger_key(row)
                except ValueError:
                    rows.append(row)
                    continue
                if row_key == {
                    "repo": canonical_key["repo"],
                    "issue_ref": source_ref,
                }:
                    source_found = True
                    continue
                if row_key == canonical_key:
                    canonical_found = True
                    canonical_existing = row
                    continue
                rows.append(row)
        projection = payload.get("domain_state_projection")
        if isinstance(projection, dict):
            projection["write_performed"] = True
        candidate = {**payload, "domain_state_key": canonical_key}
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
    if isinstance(projection, dict):
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


def issue_fix_pr_lifecycle_ledger_key(payload: dict[str, Any]) -> dict[str, Any]:
    observation = payload.get("observation")
    if not isinstance(observation, dict):
        raise ValueError("issue-fix PR lifecycle payload must include observation")
    repo = str(observation.get("repo") or "").strip()
    pr_ref = str(observation.get("pr_ref") or "").strip()
    if not repo or not pr_ref:
        raise ValueError("issue-fix PR lifecycle payload must include repo and pr_ref")
    return {
        "repo": repo,
        "pr_ref": pr_ref,
    }


def upsert_issue_fix_pr_lifecycle_ledger_jsonl(
    ledger_path: str | Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Upsert a compact issue-fix PR lifecycle packet into domain state."""

    if payload.get("ok") is not True:
        raise ValueError(
            "only successful issue-fix PR lifecycle payloads can be written"
        )
    for key in (
        "issue_body_captured",
        "comment_bodies_captured",
        "maintainer_correction_body_captured",
        "response_payloads_captured",
        "raw_check_logs_captured",
        "local_paths_captured",
    ):
        if payload.get(key) is not False:
            raise ValueError(f"issue-fix domain-state payload must keep {key}=false")

    def unchanged_quiet_observation(
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> bool:
        existing_fingerprint = str(existing.get("observation_fingerprint") or "")
        incoming_fingerprint = str(incoming.get("observation_fingerprint") or "")
        existing_correction_fingerprint = str(
            existing.get("maintainer_correction_fingerprint") or ""
        )
        incoming_correction_fingerprint = str(
            incoming.get("maintainer_correction_fingerprint") or ""
        )
        existing_receipts = existing.get("reviewer_notification_receipts")
        incoming_receipts = incoming.get("reviewer_notification_receipts")
        existing_queue = existing.get("reviewer_notification_queue")
        incoming_queue = incoming.get("reviewer_notification_queue")
        return bool(
            existing_fingerprint
            and existing_fingerprint == incoming_fingerprint
            and existing_correction_fingerprint == incoming_correction_fingerprint
            and existing_receipts == incoming_receipts
            and existing_queue == incoming_queue
        )

    def preserve_compact_lifecycle_evidence(
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        merged = incoming
        receipts = existing.get("reviewer_notification_receipts")
        if (
            "reviewer_notification_receipts" not in merged
            and isinstance(receipts, list)
            and receipts
        ):
            merged = {**merged, "reviewer_notification_receipts": list(receipts)}
        queued = existing.get("reviewer_notification_queue")
        if (
            "reviewer_notification_queue" not in merged
            and isinstance(queued, list)
            and queued
        ):
            merged = {**merged, "reviewer_notification_queue": list(queued)}
        first_push_ci = existing.get("first_push_ci")
        if "first_push_ci" not in merged and isinstance(first_push_ci, dict):
            merged = {**merged, "first_push_ci": dict(first_push_ci)}
        return merged

    return _upsert_issue_fix_payload(
        ledger_path,
        payload,
        key=issue_fix_pr_lifecycle_ledger_key(payload),
        existing_key_fn=issue_fix_pr_lifecycle_ledger_key,
        unchanged_fn=unchanged_quiet_observation,
        merge_existing_fn=preserve_compact_lifecycle_evidence,
    )


def persist_issue_fix_reviewer_notification_receipts(
    ledger_path: str | Path,
    payload: dict[str, Any],
    receipts: list[str],
) -> dict[str, Any]:
    """Persist only verified hashed reviewer-notification receipts in PR state."""

    return persist_issue_fix_reviewer_notification_state(
        ledger_path,
        payload,
        receipts=receipts,
        queued_receipts=[],
    )


def _validated_reviewer_notification_queue_receipt(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("reviewer notification queue receipts must be objects")
    key = str(value.get("idempotency_key") or "")
    sink_kind = str(value.get("sink_kind") or "")
    reviewers = value.get("reviewer_handles")
    window = value.get("allowed_local_time")
    if (
        value.get("schema_version")
        != REVIEWER_NOTIFICATION_QUEUE_RECEIPT_SCHEMA_VERSION
        or not REVIEWER_NOTIFICATION_RECEIPT_PATTERN.fullmatch(key)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,50}", sink_kind)
        or not isinstance(reviewers, list)
        or not reviewers
        or any(
            not REVIEWER_NOTIFICATION_HANDLE_PATTERN.fullmatch(str(handle))
            for handle in reviewers
        )
        or not isinstance(window, dict)
        or not str(value.get("message_summary") or "").strip()
        or len(str(value.get("message_summary") or "")) > 240
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
    payload: dict[str, Any],
    *,
    receipts: list[str],
    queued_receipts: list[dict[str, Any]],
    replace_queued_receipts: bool = False,
) -> dict[str, Any]:
    """Persist verified receipts and restart-safe queued delivery metadata.

    The default preserves append-compatible callers. An authoritative reviewer
    route may replace the unsent queue so stale targets are cancelled when live
    GitHub coverage changes or the current target becomes unavailable.
    """

    # Rows written before a capture-boundary field was introduced may omit it.
    # Receipt persistence is a metadata-only migration, so make that historical
    # absence explicit without weakening the guard for any truthy value.
    for key in (
        "issue_body_captured",
        "comment_bodies_captured",
        "maintainer_correction_body_captured",
        "response_payloads_captured",
        "raw_check_logs_captured",
        "local_paths_captured",
    ):
        payload.setdefault(key, False)

    existing_receipts = payload.get("reviewer_notification_receipts")
    merged_receipts = [
        str(value)
        for value in (existing_receipts if isinstance(existing_receipts, list) else [])
        if REVIEWER_NOTIFICATION_RECEIPT_PATTERN.fullmatch(str(value))
    ]
    for receipt in receipts:
        text = str(receipt)
        if not REVIEWER_NOTIFICATION_RECEIPT_PATTERN.fullmatch(text):
            raise ValueError("reviewer notification receipts must be sha256 keys")
        if text not in merged_receipts:
            merged_receipts.append(text)

    existing_queue = payload.get("reviewer_notification_queue")
    prior_queue: dict[str, dict[str, Any]] = {}
    for value in existing_queue if isinstance(existing_queue, list) else []:
        try:
            queued = _validated_reviewer_notification_queue_receipt(value)
        except ValueError:
            continue
        prior_queue[str(queued["idempotency_key"])] = queued
    merged_queue = {} if replace_queued_receipts else dict(prior_queue)
    for value in queued_receipts:
        queued = _validated_reviewer_notification_queue_receipt(value)
        merged_queue[str(queued["idempotency_key"])] = queued
    for key in merged_receipts:
        merged_queue.pop(key, None)
    queue = list(merged_queue.values())
    queue_reconciliation = {
        "schema_version": "issue_fix_reviewer_notification_queue_reconciliation_v0",
        "mode": "replace_unsent" if replace_queued_receipts else "merge",
        "prior_count": len(prior_queue),
        "requested_count": len(queued_receipts),
        "result_count": len(queue),
        "cancelled_count": len(set(prior_queue) - set(merged_queue)),
    }

    prior_receipts = existing_receipts if isinstance(existing_receipts, list) else []
    prior_queue = existing_queue if isinstance(existing_queue, list) else []
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


def default_issue_fix_domain_state_ledger_path(
    *,
    project: str | Path = ".",
    goal_id: str,
) -> Path:
    """Return the project-local domain-state ledger path for issue-fix PRs."""

    return default_domain_state_file_path(
        project=project,
        goal_id=goal_id,
        domain_pack="issue_fix",
        filename=ISSUE_FIX_DOMAIN_STATE_LEDGER_FILENAME,
    )


def default_issue_fix_feasibility_ledger_path(
    *,
    project: str | Path = ".",
    goal_id: str,
) -> Path:
    """Return the project-local issue-fix feasibility ledger path."""

    return default_domain_state_file_path(
        project=project,
        goal_id=goal_id,
        domain_pack="issue_fix",
        filename=ISSUE_FIX_FEASIBILITY_LEDGER_FILENAME,
    )


def default_issue_fix_candidate_preflight_ledger_path(
    *,
    project: str | Path = ".",
    goal_id: str,
) -> Path:
    """Return the project-local issue candidate admission ledger path."""

    return default_domain_state_file_path(
        project=project,
        goal_id=goal_id,
        domain_pack="issue_fix",
        filename=ISSUE_FIX_CANDIDATE_PREFLIGHT_LEDGER_FILENAME,
    )


def default_issue_fix_repository_snapshot_ledger_path(
    *, project: str | Path = ".", goal_id: str
) -> Path:
    return default_domain_state_file_path(
        project=project,
        goal_id=goal_id,
        domain_pack="issue_fix",
        filename=ISSUE_FIX_REPOSITORY_SNAPSHOT_LEDGER_FILENAME,
    )


def retain_issue_fix_repository_snapshot_jsonl(
    ledger_path: str | Path, record: dict[str, Any]
) -> dict[str, Any]:
    path = Path(ledger_path)
    existing_rows: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").split("\n"):
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                existing_rows.append(value)
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
