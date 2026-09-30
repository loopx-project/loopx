"""Transaction capture evidence, receipt-proven drain, and operator readback.

Historical observations remain readable; this owner only delivers durable
transaction-bound entries.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from ..projects.registry_codec import load_registry
from ...paths import resolve_runtime_root
from ...registry import find_registry_goal
from ..effect_runtime import effect_runtime_result
from . import local_authority_shadow_outbox as outbox
from .coordination_state_contract_generated import (
    LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_RESULT_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_TRANSACTION_EVIDENCE_SCHEMA,
)
from .local_authority_shadow_projection import (
    head_digest,
    todo_partition_projection,
)
from .runtime_shadow import resolve_coordination_runtime_shadow_config, capture_todo_archive_dependencies
from .shadow_management import read_shadow_capture_binding, shadow_management_state_path
from .shadow_goal_scope import shadow_goal_scope


from .runtime_shadow import local_authority_shadow_summary


def effective_runtime_root(
    registry_path: Path,
    runtime_root_override: str | Path | None,
) -> Path:
    """Resolve the one runtime root every writer hook of a CLI call must share.

    ``--runtime-root`` wins when given; otherwise the registry's
    ``common_runtime_root`` applies, and a relative value resolves against the
    registry's project root rather than the caller's working directory. Todo,
    follow-up, handoff-mode, and task-lease hooks all consume this value so one
    goal never splits into two candidate lineages.
    """

    registry = load_registry(registry_path)
    override = str(runtime_root_override) if runtime_root_override is not None else None
    return resolve_runtime_root(registry, override, registry_path=registry_path)


# ---------------------------------------------------------------------------
# Transaction-bound outbox drain (Stage 2C second half plumbing).
#
# Writers record per-partition outbox entries inside the primary lock (see
# ``local_authority_shadow_outbox``). The drain below runs after that lock is
# released and turns each committed entry into exactly one candidate
# transaction. It is bounded, never blocks a writer, and reports what it left
# behind instead of guessing.
# ---------------------------------------------------------------------------

LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA_V1 = (
    LOCAL_AUTHORITY_SHADOW_TRANSACTION_EVIDENCE_SCHEMA
)
INLINE_DRAIN_MAX_ENTRIES = 16
INLINE_DRAIN_BUDGET_SECONDS = 2.0
INLINE_DRAIN_LOCK_TIMEOUT_SECONDS = 0.25
CLI_DRAIN_LOCK_TIMEOUT_SECONDS = 5.0
SHADOW_DRAIN_SCHEMA = "loopx_shadow_drain_v0"
SHADOW_EXACT_DRAIN_SCHEMA = "loopx_shadow_drain_v1"
RETENTION_PRESSURE_BYTES = 8 * 1024 * 1024
_SEED_WRITE_CLASSES = {"seed", "reseed_after_crash_gap"}
_EVIDENCE_V1_OUTCOMES = {
    "delivered",
    "replayed",
    "ambiguous_reconciled",
    "pending",
    "drain_deferred",
    "no_transaction",
    "capture_failed",
    "ambiguous_unproved",
    "unavailable",
    "failed",
    "protocol_mismatch",
    "conflict_retry_required",
}


@dataclass
class DrainResult:
    """Typed outcome of one bounded drain pass."""

    goal_id: str
    outcome: str = "nothing_pending"
    config_enabled: bool = False
    delivered: int = 0
    replayed: int = 0
    reconciled: int = 0
    no_op: int = 0
    reseeded: int = 0
    reclaimed_residue: int = 0
    pending_after: int = 0
    prepared_only_after: int = 0
    in_flight_partitions: list[str] = field(default_factory=list)
    budget_exhausted: bool = False
    stopped_at: dict[str, Any] | None = None
    reason_code: str | None = None
    store_identity: str | None = None
    provider_revision: str | None = None
    last_cursor: str | None = None
    cursor_before: str | None = None
    cursor_after: str | None = None
    head_digest: str | None = None
    candidate_readback_verified: bool | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.outcome in {"drained", "nothing_pending"} and self.stopped_at is None
        )

    @property
    def drained_count(self) -> int:
        return self.delivered + self.replayed + self.reconciled

    def entry_outcome(self, entry_id: str | None) -> dict[str, Any] | None:
        if entry_id is None:
            return None
        for item in self.entries:
            if item.get("entry_id") == entry_id:
                return item
        return None

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ok"] = self.ok
        payload["drained_count"] = self.drained_count
        return payload


def todo_partition_projector(
    goal: Mapping[str, Any] | None,
    *,
    state_path: Path,
    rollout_events: list[dict[str, Any]] | None = None,
) -> outbox.TodoPartitionProjector:
    """Production projector: parse active-state text into the todos partition."""

    from ...control_plane.todos.handoff_mode import goal_handoff_mode
    from ..todos.goal_todo_projection import project_goal_todo_items

    goal_record = dict(goal) if isinstance(goal, Mapping) else None
    events = list(rollout_events or [])

    def project(state_text: str) -> dict[str, Any]:
        return todo_partition_projection(
            handoff_mode=goal_handoff_mode(state_text),
            todos=capture_todo_archive_dependencies(project_goal_todo_items(
                goal_record,
                state_text=state_text,
                state_path=state_path,
                rollout_events=events,
            ), state_text),
        )

    return project


def read_local_authority_shadow(
    *,
    runtime_root: Path,
    goal_id: str,
    store_kind: str = "runtime_shadow",
    scan_after_cursor: str | None = None,
    scan_limit: int = 0,
    receipt_operation_id: str | None = None,
    read_model: str = "full",
) -> dict[str, Any]:
    """Read-only candidate view through the TypeScript store boundary."""

    result = effect_runtime_result(
        "coordination.runtime_shadow.outbox_read",
        {
            "schema_version": LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
            "runtime_root": str(runtime_root),
            "goal_id": goal_id,
            "store_kind": store_kind,
            "scan_after_cursor": scan_after_cursor,
            "scan_limit": scan_limit,
            "receipt_operation_id": receipt_operation_id,
            "read_model": read_model,
        },
        timeout=15.0,
    )
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA
        or result.get("goal_id") != goal_id
    ):
        raise RuntimeError("local authority shadow read result is invalid")
    return dict(result)


def _drain_prelude(
    result: DrainResult,
    *,
    registry: dict[str, Any],
    registry_path: Path,
    runtime_root: Path | None,
    goal_id: str,
) -> Path | None:
    """Validate the goal id and resolve the registry and root; typed failure on error."""

    if not goal_id or goal_id in {".", ".."} or "/" in goal_id or "\\" in goal_id:
        result.outcome = "failed"
        result.reason_code = "invalid_shadow_goal_id"
        return None
    try:
        result.config_enabled = (
            resolve_coordination_runtime_shadow_config(
                find_registry_goal(registry, goal_id)
            ).enabled
        )
        resolved = (
            runtime_root
            if runtime_root is not None
            else resolve_runtime_root(registry, None, registry_path=registry_path)
        )
    except Exception:
        result.outcome = "failed"
        result.reason_code = "invalid_shadow_config"
        return None
    return resolved


def drain_local_authority_shadow_outbox(
    *,
    registry_path: Path,
    runtime_root: Path | None,
    goal_id: str,
    max_entries: int = INLINE_DRAIN_MAX_ENTRIES,
    budget_seconds: float = INLINE_DRAIN_BUDGET_SECONDS,
    lock_timeout_seconds: float = INLINE_DRAIN_LOCK_TIMEOUT_SECONDS,
) -> DrainResult:
    """Transport one native drain batch; never replay a timed-out invocation.

    A lost response leaves candidate commit/cleanup progress unknown. The next
    explicit drain recovers it from durable receipts, not Python memory.
    """
    result = DrainResult(goal_id=goal_id)
    try:
        with shadow_goal_scope(registry_path, goal_id=goal_id) as scope:
            resolved_root = _drain_prelude(
                result,
                registry=scope.registry,
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=goal_id,
            )
            if resolved_root is None:
                return result
            # No activation or persisted capture state: avoid starting the TS runtime
            # for ordinary feature-off writes. Existing state is interpreted only by TS.
            if (not result.config_enabled
                    and not shadow_management_state_path(resolved_root, goal_id).exists()
                    and not outbox.outbox_root(resolved_root, goal_id).exists()):
                return result
            schema = SHADOW_EXACT_DRAIN_SCHEMA if scope.exact else SHADOW_DRAIN_SCHEMA
            raw = effect_runtime_result(
                "coordination.runtime_shadow.drain",
                {"schema_version": schema, "runtime_root": str(resolved_root),
                 "goal_id": goal_id, "python_executable": sys.executable,
                 "config_enabled": result.config_enabled, "max_entries": max_entries,
                 "budget_seconds": budget_seconds, "lock_timeout_seconds": lock_timeout_seconds,
                 **({"goal_ref": scope.goal_ref} if scope.goal_ref is not None else {})},
                timeout=max(15.0, budget_seconds + 15.0), retry_safe=False,
            )
            if not isinstance(raw, dict) or raw.get("schema_version") != schema or raw.get("goal_id") != goal_id:
                raise ValueError("invalid native drain result")
            return DrainResult(**{item.name: raw[item.name] for item in fields(DrainResult)})
    except Exception:
        result.outcome = "stopped"
        result.reason_code = "shadow_drain_outcome_unknown"
        return result


class _CandidateMissing(Exception):
    """The candidate store directory does not exist yet."""


def _store_bytes(runtime_root: Path, goal_id: str, *, legacy_observation: bool) -> int:
    directory = (
        runtime_root / "authority-shadow" / "file" / goal_id
        if legacy_observation
        else runtime_root / "authority-shadow" / "file-v0"
    )
    if not directory.is_dir():
        return 0
    return sum(path.stat().st_size for path in directory.iterdir() if path.is_file())


def local_authority_shadow_status(
    *,
    registry_path: Path,
    runtime_root: Path | None,
    goal_id: str,
) -> dict[str, Any]:
    """Operator readback: configuration, outbox backlog, and candidate head facts."""

    registry = load_registry(registry_path)
    goal = find_registry_goal(registry, goal_id)
    if not isinstance(goal, dict):
        raise ValueError(f"goal {goal_id!r} is not registered")
    if runtime_root is None:
        runtime_root = resolve_runtime_root(registry, None, registry_path=registry_path)
    config = local_authority_shadow_summary(goal)
    runtime_config = resolve_coordination_runtime_shadow_config(goal)
    management = read_shadow_capture_binding(runtime_root, goal_id)
    legacy_observation = (
        config.get("configured") is True
        and not runtime_config.enabled
        and management["status"] == "missing"
    )
    backlog = outbox.outbox_summary(runtime_root, goal_id)
    candidate: dict[str, Any]
    try:
        directory = (
            runtime_root / "authority-shadow" / "file" / goal_id
            if legacy_observation
            else runtime_root / "authority-shadow" / "file-v0"
        )
        if not directory.is_dir() or not any(directory.glob("authority-store-*.json")):
            # Reading through the store boundary would mint a store identity;
            # a status probe must not create candidate lineage.
            raise _CandidateMissing
        view = read_local_authority_shadow(
            runtime_root=runtime_root,
            goal_id=goal_id,
            store_kind=(
                "legacy_observation" if legacy_observation else "runtime_shadow"
            ),
        )
        head = view.get("head") if isinstance(view.get("head"), dict) else None
        candidate = {
            "status": view.get("status"),
            "reason_code": view.get("reason_code"),
            "store_identity": view.get("store_identity"),
            "provider_revision": view.get("provider_revision"),
            "cursor": view.get("cursor"),
            "head_digest": view.get("head_digest"),
            "head_schema_version": head.get("schema_version") if head else None,
            "partitions": view.get("partitions"),
            "codec_agreement": (head_digest(head) == view.get("head_digest"))
            if head
            else None,
        }
    except _CandidateMissing:
        candidate = {
            "status": "missing",
            "reason_code": None,
            "store_identity": None,
            "provider_revision": None,
            "cursor": None,
            "head_digest": None,
            "head_schema_version": None,
            "partitions": None,
            "codec_agreement": None,
        }
    except Exception:
        candidate = {
            "status": "unavailable",
            "reason_code": "shadow_read_failed",
            "store_identity": None,
            "provider_revision": None,
            "cursor": None,
            "head_digest": None,
            "head_schema_version": None,
            "partitions": None,
            "codec_agreement": None,
        }
    candidate["store_kind"] = "legacy_observation" if legacy_observation else "runtime_shadow"
    candidate["historical_only"] = legacy_observation
    try:
        store_bytes = _store_bytes(
            runtime_root, goal_id, legacy_observation=legacy_observation
        )
        storage_error = None
    except OSError:
        store_bytes = None
        storage_error = "shadow_store_unavailable"
    return {
        "ok": all(item["invalid"] is None for item in backlog.values())
        and storage_error is None
        and management["status"] != "hold",
        "action": "status",
        "goal_id": goal_id,
        "config": config,
        "runtime_config": asdict(runtime_config),
        "management": management,
        "storage_error": storage_error,
        "runtime_root_digest": outbox.runtime_root_digest(runtime_root),
        "outbox": backlog,
        "candidate": candidate,
        "store_bytes": store_bytes,
        "retention_pressure": store_bytes > RETENTION_PRESSURE_BYTES
        if store_bytes is not None
        else None,
    }


def capture_evidence(
    *,
    goal_id: str,
    capture: outbox.CaptureOutcome,
    drain: DrainResult | None,
) -> dict[str, Any]:
    """Evidence v1 attached to a writer payload: capture facts plus drain facts.

    Every flag here is a measured fact of this write. ``source_candidate_compared``
    and ``parity_verdict`` stay negative until the verify step exists.
    """

    if capture.failure is not None:
        outcome = "capture_failed"
        reason_code: str | None = str(capture.failure.get("reason_code"))
    elif capture.entry_id is None:
        outcome = "no_transaction"
        reason_code = capture.skipped_reason
    elif drain is None or drain.outcome == "drain_deferred":
        outcome = "drain_deferred" if drain is not None else "pending"
        reason_code = drain.reason_code if drain is not None else None
    else:
        settled = drain.entry_outcome(capture.entry_id)
        if settled is None:
            outcome = "pending"
            reason_code = drain.reason_code
        else:
            outcome = str(settled["outcome"])
            reason_code = settled.get("reason_code")
    return {
        "schema_version": LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA_V1,
        "outcome": outcome,
        "reason_code": reason_code,
        "goal_id": goal_id,
        "entry": {
            "entry_id": capture.entry_id,
            "partition": capture.partition,
            "seq": capture.seq,
            "partition_digest": capture.partition_digest,
            "source_bytes_digest": capture.source_bytes_digest,
        },
        "drain": None
        if drain is None
        else {
            "outcome": drain.outcome,
            "delivered": drain.delivered,
            "replayed": drain.replayed,
            "reclaimed_residue": drain.reclaimed_residue,
            "pending_after": drain.pending_after,
            "prepared_only_after": drain.prepared_only_after,
            "stopped_at": drain.stopped_at,
            "last_cursor": drain.last_cursor,
            "provider_revision": drain.provider_revision,
            "candidate_readback_verified": drain.candidate_readback_verified,
        },
        "capture_kind": "source_transaction_outbox",
        # Both flags are measured facts of this write: they are true only when
        # a prepared/committed entry was actually recorded for it. A disabled
        # or unchanged capture recorded nothing and claims nothing.
        "source_transaction_correlated": capture.recorded,
        "durable_source_outbox": capture.recorded,
        "source_candidate_compared": False,
        "parity_verdict": "not_evaluated",
        "primary_authority": "legacy_local",
        "candidate_provider": "file",
        "candidate_read_for_decision": False,
        "provider_to_local_writes": False,
        "primary_writeback_preserved": True,
        "store_identity": drain.store_identity if drain is not None else None,
    }


def valid_evidence_v1(result: object, *, goal_id: str) -> bool:
    """Closed-shape check for evidence v1 as attached to writer payloads."""

    if not isinstance(result, dict):
        return False
    entry = result.get("entry")
    return (
        result.get("schema_version") == LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA_V1
        and result.get("outcome") in _EVIDENCE_V1_OUTCOMES
        and result.get("goal_id") == goal_id
        and isinstance(entry, dict)
        and result.get("capture_kind") == "source_transaction_outbox"
        and isinstance(result.get("source_transaction_correlated"), bool)
        and isinstance(result.get("durable_source_outbox"), bool)
        and result.get("source_candidate_compared") is False
        and result.get("parity_verdict") == "not_evaluated"
        and result.get("primary_authority") == "legacy_local"
        and result.get("candidate_provider") == "file"
        and result.get("candidate_read_for_decision") is False
        and result.get("provider_to_local_writes") is False
        and result.get("primary_writeback_preserved") is True
        and (
            result.get("reason_code") is None
            or isinstance(result.get("reason_code"), str)
        )
    )


__all__ = [
    "effective_runtime_root",
    "CLI_DRAIN_LOCK_TIMEOUT_SECONDS",
    "INLINE_DRAIN_BUDGET_SECONDS",
    "INLINE_DRAIN_LOCK_TIMEOUT_SECONDS",
    "INLINE_DRAIN_MAX_ENTRIES",
    "LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_RESULT_SCHEMA",
    "LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA_V1",
    "LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA",
    "LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA",
    "RETENTION_PRESSURE_BYTES",
    "DrainResult",
    "capture_evidence",
    "todo_partition_projector",
    "drain_local_authority_shadow_outbox",
    "local_authority_shadow_status",
    "read_local_authority_shadow",
    "valid_evidence_v1",
]
