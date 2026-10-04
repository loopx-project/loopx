from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock
from ..effect_runtime import (
    EffectRuntimeConflict,
    EffectRuntimeRejected,
    effect_runtime_result,
)
from ..runtime.time import now_local_iso
from ..todos.contract import normalize_todo_claimed_by
from .decision_summary import compact_quota_decision, quota_decision_agent_id
from .accounting_admission import quota_accounting_admission
from .spend_sources import DEFAULT_SLOT_SPEND_SOURCE


QUOTA_SPEND_COMMIT_REQUEST_SCHEMA = "loopx_quota_spend_commit_request_v0"
QUOTA_SPEND_COMMIT_RESULT_SCHEMA = "loopx_quota_spend_commit_result_v0"


def quota_spend_index_digest(index_path: Path) -> str | None:
    try:
        content = index_path.read_bytes()
    except FileNotFoundError:
        return None
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _quota_spend_commit_request(
    preview: Mapping[str, Any],
    *,
    source: str,
    generated_at: str,
    execute: bool,
    runtime_root: Path | None,
    expected_index_digest: str | None,
    goal_ref: Mapping[str, Any] | None = None,
    source_admission: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # Import lazily because runtime loads history, whose quota compatibility
    # surface re-exports this module during package initialization.
    from ...runtime import validate_goal_id_path_segment

    raw_before = preview.get("before")
    before = raw_before if isinstance(raw_before, dict) else {}
    raw_after = preview.get("after")
    after = raw_after if isinstance(raw_after, dict) else {}
    goal_id = validate_goal_id_path_segment(
        str(preview.get("goal_id") or "").strip()
    )
    resolved_agent_id = (
        normalize_todo_claimed_by(preview.get("agent_id"))
        or quota_decision_agent_id(before)
    )
    request_preview = dict(preview)
    request_preview.pop("expected_index_digest", None)
    request_preview["after_recommended_action"] = after.get("recommended_action")
    request = {
        "schema_version": QUOTA_SPEND_COMMIT_REQUEST_SCHEMA,
        "runtime_root": str(runtime_root) if runtime_root is not None else None,
        "goal_id": goal_id,
        "source": source,
        "generated_at": generated_at,
        "execute": execute,
        "expected_index_digest": expected_index_digest,
        "preview": request_preview,
        "before": compact_quota_decision(before),
        "after": compact_quota_decision(after),
        "resolved_agent_id": resolved_agent_id,
    }
    if goal_ref is not None:
        request["goal_ref"] = dict(goal_ref)
    if source_admission is not None:
        request["source_admission"] = dict(source_admission)
    return request


def _quota_spend_commit_result(
    preview: Mapping[str, Any],
    *,
    source: str,
    generated_at: str | None,
    execute: bool,
    runtime_root: Path | None,
    expected_index_digest: str | None,
    goal_ref: Mapping[str, Any] | None = None,
    source_admission: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    params = _quota_spend_commit_request(
        preview,
        source=source,
        generated_at=generated_at or now_local_iso(),
        execute=execute,
        runtime_root=runtime_root,
        expected_index_digest=expected_index_digest,
        goal_ref=goal_ref,
        source_admission=source_admission,
    )
    try:
        result = effect_runtime_result("quota.spend.commit", params)
    except (EffectRuntimeConflict, EffectRuntimeRejected) as exc:
        raise ValueError(str(exc)) from None
    if (
        not isinstance(result, Mapping)
        or result.get("schema_version") != QUOTA_SPEND_COMMIT_RESULT_SCHEMA
    ):
        raise RuntimeError("TypeScript quota spend commit result shape mismatch")
    if result.get("status") == "conflict":
        raise ValueError(str(result.get("reason") or "quota spend commit conflict"))
    return result


def replay_quota_spend_by_effect_ref(
    runtime_root: Path,
    *,
    goal_id: str,
    effect_ref: str,
    agent_id: str | None,
    read_only: bool = False,
    registry_path: Path | None = None,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the native quota transaction to validate an effect replay."""

    from ...runtime import validate_goal_id_path_segment

    safe_goal_id = validate_goal_id_path_segment(str(goal_id or "").strip())
    normalized_effect_ref = str(effect_ref or "").strip()
    if not normalized_effect_ref:
        return {
            "ok": False,
            "appended": False,
            "replay_found": False,
            "reason": "effect_ref must be a non-empty string",
        }
    with quota_accounting_admission(
        runtime_root=runtime_root,
        registry_path=registry_path,
        goal_id=safe_goal_id,
        goal_ref=goal_ref,
        operation="quota_spend_replay",
        lock_legacy_index=False,
    ) as source_admission:
        request: dict[str, Any] = {
            "schema_version": QUOTA_SPEND_COMMIT_REQUEST_SCHEMA,
            "operation": "replay",
            "runtime_root": str(runtime_root.expanduser()),
            "goal_id": safe_goal_id,
            "effect_id": normalized_effect_ref,
            "resolved_agent_id": normalize_todo_claimed_by(agent_id),
            "read_only": read_only,
        }
        if goal_ref is not None:
            request["goal_ref"] = dict(goal_ref)
        if source_admission is not None:
            request["source_admission"] = dict(source_admission)
        try:
            result = effect_runtime_result("quota.spend.commit", request)
        except (EffectRuntimeConflict, EffectRuntimeRejected) as exc:
            raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping) or result.get("schema_version") != QUOTA_SPEND_COMMIT_RESULT_SCHEMA:
        raise RuntimeError("TypeScript quota spend replay result shape mismatch")
    payload = result.get("payload")
    if not isinstance(payload, Mapping):
        raise RuntimeError("TypeScript quota spend replay omitted its payload")
    return dict(payload)


def build_quota_slot_spend_event(
    preview: dict[str, Any],
    *,
    self_repair_spend_actions: set[str] | frozenset[str] | None = None,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    generated_at: str | None = None,
) -> dict[str, Any]:
    # The argument remains source-compatible while the canonical action set is
    # now owned by the typed transaction.
    del self_repair_spend_actions
    result = _quota_spend_commit_result(
        preview,
        source=source,
        generated_at=generated_at,
        execute=False,
        runtime_root=None,
        expected_index_digest=None,
    )
    record = result.get("record")
    if not isinstance(record, Mapping):
        raise RuntimeError("TypeScript quota spend preview omitted its record")
    return dict(record)


def record_quota_slot_spend_from_preview(
    preview: dict[str, Any],
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    execute: bool = False,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    registry_path: Path | None = None,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not preview.get("ok"):
        return preview
    from ...runtime import validate_goal_id_path_segment

    safe_goal_id = validate_goal_id_path_segment(str(goal_id or ""))
    preview_goal_id = validate_goal_id_path_segment(
        str(preview.get("goal_id") or safe_goal_id)
    )
    if preview_goal_id != safe_goal_id:
        raise ValueError("quota spend preview goal_id does not match commit goal_id")
    raw_runtime_root = status_payload.get("runtime_root")
    if not raw_runtime_root:
        raise ValueError("status payload does not include runtime_root")
    runtime_root = Path(str(raw_runtime_root)).expanduser()
    if execute and "expected_index_digest" not in preview:
        raise ValueError("quota spend preview does not include its index basis")
    expected_index_digest = preview.get("expected_index_digest")
    if expected_index_digest is not None and not isinstance(
        expected_index_digest, str
    ):
        raise ValueError("quota spend preview index basis must be a string or null")
    if execute:
        index_path = runtime_root / "goals" / safe_goal_id / "runs" / "index.jsonl"
        if registry_path is None and goal_ref is None:
            with exclusive_file_lock(
                index_path,
                operation="quota_spend_commit",
            ):
                result = _quota_spend_commit_result(
                    preview,
                    source=source,
                    generated_at=None,
                    execute=True,
                    runtime_root=runtime_root,
                    expected_index_digest=expected_index_digest,
                )
        else:
            with quota_accounting_admission(
                runtime_root=runtime_root,
                registry_path=registry_path,
                goal_id=safe_goal_id,
                goal_ref=goal_ref,
                operation="quota_spend_commit",
            ) as source_admission:
                result = _quota_spend_commit_result(
                    preview,
                    source=source,
                    generated_at=None,
                    execute=True,
                    runtime_root=runtime_root,
                    expected_index_digest=expected_index_digest,
                    goal_ref=goal_ref,
                    source_admission=source_admission,
                )
    else:
        result = _quota_spend_commit_result(
            preview,
            source=source,
            generated_at=None,
            execute=False,
            runtime_root=runtime_root,
            expected_index_digest=expected_index_digest,
            goal_ref=None,
            source_admission=None,
        )
    payload = result.get("payload")
    if not isinstance(payload, Mapping):
        raise RuntimeError("TypeScript quota spend commit omitted its payload")
    return dict(payload)
