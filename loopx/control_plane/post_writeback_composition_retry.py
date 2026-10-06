from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..file_lock import exclusive_file_lock
from ..history import validate_goal_id_path_segment


POST_WRITEBACK_COMPOSITION_RETRY_RECEIPT_SCHEMA_VERSION = (
    "loopx_post_writeback_composition_retry_receipt_v0"
)
POST_WRITEBACK_COMPOSITION_RETRY_LOG_NAME = "composition-retry-receipts.jsonl"
POST_WRITEBACK_COMPOSITION_RETRY_REF_PREFIX = "post-writeback-composition:"
POST_WRITEBACK_COMPOSITION_RETRY_ERROR_CODES = (
    "source_projection_failed",
    "dispatch_failed",
)
POST_WRITEBACK_COMPOSITION_RETRY_PROJECTION_SCHEMA_VERSION = (
    "loopx_post_writeback_composition_retry_projection_v0"
)
POST_WRITEBACK_COMPOSITION_RETRY_REPLAY_ACTION = (
    "Replay the committed CLI mutation that recorded this receipt with the "
    "same goal/event/todo/turn/effect identity and state_version: the primary "
    "writeback is idempotent, hook sidecars dedupe provider work, and a clean "
    "projection composition settles the receipt."
)
# Journals fold back to one row per receipt id (under the append lock) once
# they cross this bound; reads scan every row so no unresolved receipt is
# ever silently dropped from the pending view.
_COMPOSITION_RETRY_JOURNAL_COMPACT_ROW_LIMIT = 512


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def composition_retry_receipt_log_path(runtime_root: Path, goal_id: str) -> Path:
    """Resolve the per-goal append-only journal for composition retry receipts."""

    return (
        runtime_root.expanduser()
        / "goals"
        / validate_goal_id_path_segment(goal_id)
        / "post_writeback_hooks"
        / POST_WRITEBACK_COMPOSITION_RETRY_LOG_NAME
    )


def _normalized_hook_set_digest(
    hook_identities: Sequence[Mapping[str, str]],
) -> str:
    """Render the order-insensitive hook set so identities stay comparable."""

    pairs = sorted(
        {
            (
                str(item.get("hook_id") or "")[:200],
                str(item.get("capability_id") or "")[:200],
                str(item.get("policy_version") or "")[:64],
            )
            for item in hook_identities
            if str(item.get("hook_id") or "")
        }
    )
    encoded = json.dumps(pairs, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def composition_retry_receipt_id(
    *,
    goal_id: str,
    event_kind: str,
    identity: Mapping[str, Any],
    state_version: str,
    hook_identities: Sequence[Mapping[str, str]],
) -> str:
    """Bind one receipt to the exact primary writeback and hook set it observes.

    The hook set is part of the identity: a composition that later succeeds
    with a different registered hook set settles a different receipt (or no
    receipt at all), so a replaced or removed hook can never mark the original
    failure resolved.
    """

    stable = {
        "goal_id": str(goal_id or ""),
        "event_kind": str(event_kind or ""),
        "agent_id": str(identity.get("agent_id") or ""),
        "todo_id": str(identity.get("todo_id") or ""),
        "turn_instance_id": str(identity.get("turn_instance_id") or ""),
        "effect_id": str(identity.get("effect_id") or ""),
        "state_version": str(state_version or ""),
        "hook_set_digest": _normalized_hook_set_digest(hook_identities),
    }
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return "pwcr_" + hashlib.sha256(encoded).hexdigest()[:16]


def composition_retry_receipt_ref(receipt_id: str) -> str:
    return f"{POST_WRITEBACK_COMPOSITION_RETRY_REF_PREFIX}{receipt_id}"


def build_composition_retry_receipt(
    *,
    goal_id: str,
    event_kind: str,
    identity: Mapping[str, Any],
    state_version: str,
    committed_at: str,
    hook_identities: Sequence[Mapping[str, str]],
    error_code: str | None = None,
    status: str = "retryable",
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Build one public-safe retry receipt bound to a committed primary writeback.

    The receipt records lifecycle identity only: no projection payload, task
    text, local paths, or provider output is ever embedded.
    """

    if status not in {"retryable", "settled"}:
        raise ValueError("composition retry receipt status is invalid")
    if status == "retryable" and error_code not in (
        POST_WRITEBACK_COMPOSITION_RETRY_ERROR_CODES
    ):
        raise ValueError("composition retry receipt error_code is invalid")
    bounded_identities: list[dict[str, str]] = []
    seen_hook_ids: set[str] = set()
    for raw_identity in hook_identities:
        hook_id = str(raw_identity.get("hook_id") or "")[:200]
        capability_id = str(raw_identity.get("capability_id") or "")[:200]
        policy_version = str(raw_identity.get("policy_version") or "")[:64]
        if not hook_id or hook_id in seen_hook_ids:
            continue
        seen_hook_ids.add(hook_id)
        bounded_identities.append(
            {
                "hook_id": hook_id,
                "capability_id": capability_id,
                "policy_version": policy_version,
            }
        )
    return {
        "schema_version": POST_WRITEBACK_COMPOSITION_RETRY_RECEIPT_SCHEMA_VERSION,
        "receipt_id": composition_retry_receipt_id(
            goal_id=goal_id,
            event_kind=event_kind,
            identity=identity,
            state_version=state_version,
            hook_identities=bounded_identities,
        ),
        "status": status,
        "error_code": error_code,
        "event_kind": str(event_kind or ""),
        "identity": {
            "goal_id": str(goal_id or ""),
            "agent_id": str(identity.get("agent_id") or ""),
            "todo_id": str(identity.get("todo_id") or ""),
            "turn_instance_id": str(identity.get("turn_instance_id") or ""),
            "effect_id": str(identity.get("effect_id") or ""),
        },
        "state_version": str(state_version or ""),
        "committed_at": str(committed_at or ""),
        "hooks": bounded_identities,
        "primary_writeback_preserved": True,
        "external_writes_performed": False,
        "recorded_at": recorded_at or _now_iso(),
    }


def _iter_composition_retry_rows(
    log_path: Path, *, row_limit: int | None = None
) -> list[dict[str, Any]]:
    """Read every valid journal row, oldest first.

    The read is untruncated on purpose: folding to the newest row per receipt
    id must observe the latest state of *every* receipt, so a bounded suffix
    that silently drops an unresolved row is never acceptable here. Storage
    governance happens at append time via compaction instead.
    """

    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    selected = lines if row_limit is None else lines[-max(0, row_limit) :]
    rows: list[dict[str, Any]] = []
    for line in selected:
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if (
            row.get("schema_version")
            != POST_WRITEBACK_COMPOSITION_RETRY_RECEIPT_SCHEMA_VERSION
            or not isinstance(row.get("receipt_id"), str)
            or row.get("status") not in {"retryable", "settled"}
        ):
            continue
        rows.append(row)
    return rows


def _current_composition_retry_row(
    handle: Any, receipt_id: str
) -> dict[str, Any] | None:
    handle.seek(0)
    current: dict[str, Any] | None = None
    for line in handle:
        text = line.strip()
        if not text or receipt_id not in text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(row, dict)
            and row.get("schema_version")
            == POST_WRITEBACK_COMPOSITION_RETRY_RECEIPT_SCHEMA_VERSION
            and row.get("receipt_id") == receipt_id
            and row.get("status") in {"retryable", "settled"}
        ):
            current = row
    return current


def _separate_unterminated_jsonl_tail(handle: Any) -> None:
    handle.seek(0)
    content = handle.read()
    if content and not content.endswith("\n"):
        handle.write("\n")


def append_composition_retry_receipt(
    log_path: Path, receipt: Mapping[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Append one receipt row once, never regressing a settled receipt.

    Returns the durable row and whether this call appended it. Re-recording a
    still-retryable receipt appends the newest observation; a settled receipt
    is terminal and is returned unchanged.
    """

    payload = dict(receipt)
    if (
        payload.get("schema_version")
        != POST_WRITEBACK_COMPOSITION_RETRY_RECEIPT_SCHEMA_VERSION
    ):
        raise ValueError("unsupported composition retry receipt schema")
    receipt_id = str(payload.get("receipt_id") or "")
    if not receipt_id:
        raise ValueError("composition retry receipt_id is required")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(log_path):
        with log_path.open("a+", encoding="utf-8") as handle:
            current = _current_composition_retry_row(handle, receipt_id)
            if current is not None and current.get("status") == "settled":
                return current, False
            _separate_unterminated_jsonl_tail(handle)
            handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
            _compact_composition_retry_journal(log_path, handle)
    return payload, True


def _compact_composition_retry_journal(log_path: Path, handle: Any) -> None:
    """Fold the journal back to one row per receipt id once it grows.

    Runs under the append lock, so compaction never races another writer.
    The folded replacement is materialized, synced, and verified in a
    sibling temporary file before one atomic replace swaps it in: a
    process exit or I/O failure at any point leaves the previous journal
    fully intact instead of truncating the only durable copy, and
    lock-free readers only ever observe the complete old or the complete
    folded file. Compaction failures propagate; the journal is never
    recovered by swallowing the error or truncating again.
    """

    handle.flush()
    handle.seek(0)
    lines = handle.read().splitlines()
    if len(lines) <= _COMPOSITION_RETRY_JOURNAL_COMPACT_ROW_LIMIT:
        handle.seek(0, 2)
        return
    latest: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue
        if (
            not isinstance(row, dict)
            or row.get("schema_version")
            != POST_WRITEBACK_COMPOSITION_RETRY_RECEIPT_SCHEMA_VERSION
            or not isinstance(row.get("receipt_id"), str)
            or row.get("status") not in {"retryable", "settled"}
        ):
            continue
        receipt_id = str(row["receipt_id"])
        if receipt_id not in latest:
            order.append(receipt_id)
        latest[receipt_id] = row
    replacement = "".join(
        json.dumps(latest[receipt_id], sort_keys=True, ensure_ascii=False) + "\n"
        for receipt_id in order
    )
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=log_path.parent,
            prefix=f"{log_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_handle:
            temporary_name = temporary_handle.name
            temporary_handle.write(replacement)
            temporary_handle.flush()
            os.fsync(temporary_handle.fileno())
        verified = _iter_composition_retry_rows(Path(temporary_name))
        if [str(row.get("receipt_id")) for row in verified] != order:
            raise OSError("composition retry journal replacement is unverifiable")
        os.replace(temporary_name, log_path)
    except BaseException:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
        raise


def settle_composition_retry_receipt(
    log_path: Path,
    *,
    goal_id: str,
    event_kind: str,
    identity: Mapping[str, Any],
    state_version: str,
    committed_at: str,
    hook_identities: Sequence[Mapping[str, str]],
) -> tuple[dict[str, Any], bool]:
    """Supersede one retryable receipt after its projection composed cleanly."""

    settled = build_composition_retry_receipt(
        goal_id=goal_id,
        event_kind=event_kind,
        identity=identity,
        state_version=state_version,
        committed_at=committed_at,
        hook_identities=hook_identities,
        error_code=None,
        status="settled",
    )
    receipt_id = str(settled["receipt_id"])
    if not log_path.is_file():
        return {}, False
    with exclusive_file_lock(log_path):
        with log_path.open("r+", encoding="utf-8") as handle:
            current = _current_composition_retry_row(handle, receipt_id)
            if current is not None and current.get("status") == "settled":
                return current, False
            if current is None:
                return {}, False
            _separate_unterminated_jsonl_tail(handle)
            handle.write(json.dumps(settled, sort_keys=True, ensure_ascii=False) + "\n")
    return settled, True


def pending_composition_retry_receipts(
    runtime_root: Path, goal_id: str
) -> list[dict[str, Any]]:
    """Read unconsumed retryable receipts for one goal, newest row per receipt."""

    return pending_composition_retry_receipts_for_path(
        composition_retry_receipt_log_path(runtime_root, goal_id)
    )


__all__ = [
    "POST_WRITEBACK_COMPOSITION_RETRY_ERROR_CODES",
    "POST_WRITEBACK_COMPOSITION_RETRY_LOG_NAME",
    "POST_WRITEBACK_COMPOSITION_RETRY_RECEIPT_SCHEMA_VERSION",
    "POST_WRITEBACK_COMPOSITION_RETRY_REF_PREFIX",
    "append_composition_retry_receipt",
    "build_composition_retry_receipt",
    "composition_retry_receipt_id",
    "composition_retry_receipt_log_path",
    "composition_retry_receipt_ref",
    "collect_pending_composition_retry_projection",
    "pending_composition_retry_receipts",
    "pending_composition_retry_receipts_for_path",
    "settle_composition_retry_receipt",
]


def pending_composition_retry_receipts_for_path(
    journal_path: Path,
) -> list[dict[str, Any]]:
    """Read unconsumed retryable receipts from one journal path."""

    latest: dict[str, dict[str, Any]] = {}
    for row in _iter_composition_retry_rows(journal_path):
        latest[str(row.get("receipt_id"))] = row
    return sorted(
        (dict(row) for row in latest.values() if row.get("status") == "retryable"),
        key=lambda row: str(row.get("receipt_id") or ""),
    )


def collect_pending_composition_retry_projection(
    runtime_root: Path,
    goal_id: str | None,
    *,
    agent_id: str | None = None,
    max_items: int = 20,
) -> dict[str, Any] | None:
    """Collect pending composition retry receipts for status/doctor readback.

    Returns None when nothing is pending so the status output stays unchanged
    on healthy goals; otherwise returns a bounded public-safe projection with
    the replay guidance, letting a later turn or operator discover and clear
    outstanding composition retries through the normal read model.
    """

    root = runtime_root.expanduser()
    goals_root = root / "goals"
    if goal_id:
        journal_paths = [composition_retry_receipt_log_path(root, goal_id)]
    elif goals_root.is_dir():
        journal_paths = sorted(path for path in goals_root.iterdir() if path.is_dir())
        journal_paths = [
            path / "post_writeback_hooks" / POST_WRITEBACK_COMPOSITION_RETRY_LOG_NAME
            for path in journal_paths
        ]
    else:
        journal_paths = []
    selector_agent_id = str(agent_id or "") if agent_id else ""
    pending: list[dict[str, Any]] = []
    total_matching = 0
    for journal_path in journal_paths:
        if not journal_path.is_file():
            continue
        for row in pending_composition_retry_receipts_for_path(journal_path):
            if (
                selector_agent_id
                and str((row.get("identity") or {}).get("agent_id") or "")
                != selector_agent_id
            ):
                continue
            total_matching += 1
            if len(pending) < max_items:
                pending.append(row)
    if not total_matching:
        return None
    if not pending:
        return None
    return {
        "schema_version": POST_WRITEBACK_COMPOSITION_RETRY_PROJECTION_SCHEMA_VERSION,
        "pending_count": total_matching,
        "pending": pending,
        "replay_action": POST_WRITEBACK_COMPOSITION_RETRY_REPLAY_ACTION,
    }
