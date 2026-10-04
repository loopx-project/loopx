from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...file_lock import exclusive_file_lock
from ..effect_runtime import (
    EffectRuntimeConflict,
    EffectRuntimeRejected,
    effect_runtime_result,
)
from ..runtime.time import now_local_iso
from .accounting_admission import quota_accounting_admission
from .spend_commit import quota_spend_index_digest
from .spend_sources import DEFAULT_SLOT_SPEND_SOURCE


QUOTA_VOID_COMMIT_REQUEST_SCHEMA = "loopx_quota_void_commit_request_v0"
QUOTA_VOID_COMMIT_RESULT_SCHEMA = "loopx_quota_void_commit_result_v0"
QUOTA_VOID_COMMIT_STATUSES = frozenset(
    {"preview", "not_found", "written", "replayed", "repaired", "conflict"}
)
_ECMASCRIPT_TRIM_CHARS = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def _ecmascript_trim(value: str) -> str:
    return value.strip(_ECMASCRIPT_TRIM_CHARS)


def normalize_quota_void_goal_id(value: Any) -> str:
    # Import lazily because runtime imports the quota compatibility surface
    # while the package is initializing.
    from ...runtime import validate_goal_id_path_segment

    return str(validate_goal_id_path_segment(_ecmascript_trim(str(value or ""))))


def _normalized_effect_id(value: str | None) -> str:
    if value is None:
        return f"quota-void:{uuid4().hex}"
    if not isinstance(value, str) or not _ecmascript_trim(value):
        raise ValueError("effect_id must be a non-empty string")
    normalized = _ecmascript_trim(value)
    if len(normalized.encode("utf-16-le", errors="surrogatepass")) // 2 > 256:
        raise ValueError("effect_id exceeds 256 characters")
    return normalized


def _void_result(
    params: Mapping[str, Any],
    *,
    expected_goal_id: str | None = None,
) -> Mapping[str, Any]:
    try:
        result = effect_runtime_result("quota.void.commit", dict(params))
    except (EffectRuntimeConflict, EffectRuntimeRejected) as exc:
        raise ValueError(str(exc)) from None
    if (
        not isinstance(result, Mapping)
        or result.get("schema_version") != QUOTA_VOID_COMMIT_RESULT_SCHEMA
    ):
        raise RuntimeError("TypeScript quota void commit result shape mismatch")
    if result.get("status") not in QUOTA_VOID_COMMIT_STATUSES:
        raise RuntimeError("TypeScript quota void commit result has an invalid status")
    payload = result.get("payload")
    if not isinstance(payload, Mapping):
        raise RuntimeError("TypeScript quota void commit omitted its payload")
    operation = params.get("operation")
    if operation == "project_record":
        if result.get("effect_id") is not None:
            raise RuntimeError(
                "TypeScript quota void projection returned an effect identity"
            )
    else:
        expected_effect_id = params.get("effect_id")
        if result.get("effect_id") != expected_effect_id:
            raise RuntimeError(
                "TypeScript quota void commit result effect_id mismatch"
            )
        payload_effect_id = payload.get("effect_id")
        if (
            payload_effect_id is not None
            and payload_effect_id != expected_effect_id
        ):
            raise RuntimeError(
                "TypeScript quota void commit payload effect_id mismatch"
            )
        if (
            result.get("status") in {"written", "replayed", "repaired"}
            and payload_effect_id != expected_effect_id
        ):
            raise RuntimeError(
                "TypeScript quota void commit payload omitted its effect identity"
            )
    if (
        expected_goal_id is not None
        and payload.get("goal_id") != expected_goal_id
    ):
        raise RuntimeError("TypeScript quota void commit payload goal_id mismatch")
    if result.get("status") == "conflict":
        raise ValueError(str(result.get("reason") or "quota void commit conflict"))
    return result


def _result_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    payload = result.get("payload")
    if not isinstance(payload, Mapping):
        raise RuntimeError("TypeScript quota void commit omitted its payload")
    return dict(payload)


def _runtime_root(status_payload: Mapping[str, Any]) -> Path:
    raw_runtime_root = status_payload.get("runtime_root")
    if not raw_runtime_root:
        raise ValueError("status payload does not include runtime_root")
    normalized = _ecmascript_trim(str(raw_runtime_root))
    if not normalized:
        raise ValueError("status payload does not include runtime_root")
    return Path(normalized).expanduser().resolve()


def commit_quota_slot_void(
    status_payload: Mapping[str, Any],
    *,
    goal_id: str,
    voided_run_generated_at: str,
    before: Mapping[str, Any],
    execute: bool = False,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    reason_summary: str | None = None,
    effect_id: str | None = None,
    generated_at: str | None = None,
    _operation: str = "commit",
    registry_path: Path | None = None,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one TypeScript-owned quota void transaction."""

    safe_goal_id = normalize_quota_void_goal_id(goal_id)
    normalized_effect_id = _normalized_effect_id(effect_id)
    runtime_root = _runtime_root(status_payload)
    index_path = runtime_root / "goals" / safe_goal_id / "runs" / "index.jsonl"
    params: dict[str, Any] = {
        "schema_version": QUOTA_VOID_COMMIT_REQUEST_SCHEMA,
        "operation": _operation,
        "effect_id": normalized_effect_id,
        "runtime_root": str(runtime_root),
        "goal_id": safe_goal_id,
        "voided_run_generated_at": str(voided_run_generated_at or "").strip(),
        "source": str(source or DEFAULT_SLOT_SPEND_SOURCE).strip(),
        "reason_summary": reason_summary,
        "generated_at": generated_at or now_local_iso(),
        "execute": execute,
        "expected_index_digest": None,
        "before": dict(before),
    }

    if registry_path is None and goal_ref is None:
        if execute:
            with exclusive_file_lock(index_path, operation="quota_void_commit"):
                params["expected_index_digest"] = quota_spend_index_digest(index_path)
                result = _void_result(params, expected_goal_id=safe_goal_id)
        else:
            params["expected_index_digest"] = quota_spend_index_digest(index_path)
            result = _void_result(params, expected_goal_id=safe_goal_id)
    else:
        with quota_accounting_admission(
            runtime_root=runtime_root,
            registry_path=registry_path,
            goal_id=safe_goal_id,
            goal_ref=goal_ref,
            operation="quota_void_commit",
            lock_legacy_index=execute,
        ) as source_admission:
            params["expected_index_digest"] = quota_spend_index_digest(index_path)
            if goal_ref is not None:
                params["goal_ref"] = dict(goal_ref)
            if source_admission is not None:
                params["source_admission"] = dict(source_admission)
            result = _void_result(params, expected_goal_id=safe_goal_id)
    return _result_payload(result)


def build_quota_slot_void_preview_for_decision(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    voided_run_generated_at: str,
    before: dict[str, Any],
) -> dict[str, Any]:
    return commit_quota_slot_void(
        status_payload,
        goal_id=goal_id,
        voided_run_generated_at=voided_run_generated_at,
        before=before,
        execute=False,
        _operation="preview",
    )


def build_quota_slot_void_event(
    preview: dict[str, Any],
    *,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    reason_summary: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if not preview.get("ok"):
        raise ValueError(preview.get("reason") or "quota slot void requires a valid preview")
    result = _void_result(
        {
            "schema_version": QUOTA_VOID_COMMIT_REQUEST_SCHEMA,
            "operation": "project_record",
            "preview": dict(preview),
            "source": source,
            "reason_summary": reason_summary,
            "generated_at": generated_at or now_local_iso(),
        }
    )
    record = result.get("record")
    if not isinstance(record, Mapping):
        raise RuntimeError("TypeScript quota void projection omitted its record")
    return dict(record)


def record_quota_slot_void_from_preview(
    preview: dict[str, Any],
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    render_markdown: Callable[[dict[str, Any]], str],
    execute: bool = False,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
    reason_summary: str | None = None,
    registry_path: Path | None = None,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    del render_markdown
    if not preview.get("ok"):
        return preview
    safe_goal_id = normalize_quota_void_goal_id(goal_id)
    preview_goal_id = normalize_quota_void_goal_id(
        preview.get("goal_id") or safe_goal_id
    )
    if preview_goal_id != safe_goal_id:
        raise ValueError("quota void preview goal_id does not match commit goal_id")
    before = preview.get("before")
    if not isinstance(before, Mapping):
        raise ValueError("quota void preview has no before decision")
    return commit_quota_slot_void(
        status_payload,
        goal_id=safe_goal_id,
        voided_run_generated_at=str(
            preview.get("voided_run_generated_at") or ""
        ),
        before=before,
        execute=execute,
        source=source,
        reason_summary=reason_summary,
        registry_path=registry_path,
        goal_ref=goal_ref,
    )
