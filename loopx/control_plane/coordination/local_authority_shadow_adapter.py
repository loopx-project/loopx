"""Transaction capture evidence, receipt-proven drain, and operator readback.

Historical observations remain readable; this owner only delivers durable
transaction-bound entries.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from ...paths import effective_runtime_root as effective_runtime_root, resolve_runtime_root
from ...registry import find_registry_goal
from ..effect_runtime import effect_runtime_result
from . import local_authority_shadow_outbox as outbox
from .coordination_state_contract_generated import (
    LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_RESULT_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA,
)
from .configuration import resolve_coordination_runtime_shadow_config
from .shadow_management import shadow_management_state_path
from .shadow_goal_scope import shadow_goal_scope
from .local_authority_shadow_readback import (
    RETENTION_PRESSURE_BYTES as RETENTION_PRESSURE_BYTES,
    read_local_authority_shadow as read_local_authority_shadow,
    local_authority_shadow_status as local_authority_shadow_status,
)




# ---------------------------------------------------------------------------
# Transaction-bound outbox drain (Stage 2C second half plumbing).
#
# Writers record per-partition outbox entries inside the primary lock (see
# ``local_authority_shadow_outbox``). The drain below runs after that lock is
# released and turns each committed entry into exactly one candidate
# transaction. It is bounded, never blocks a writer, and reports what it left
# behind instead of guessing.
# ---------------------------------------------------------------------------

INLINE_DRAIN_MAX_ENTRIES = 16
INLINE_DRAIN_BUDGET_SECONDS = 2.0
INLINE_DRAIN_LOCK_TIMEOUT_SECONDS = 0.25
SHADOW_DRAIN_SCHEMA = "loopx_shadow_drain_v0"
SHADOW_EXACT_DRAIN_SCHEMA = "loopx_shadow_drain_v1"


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


__all__ = [
    "effective_runtime_root",
    "INLINE_DRAIN_BUDGET_SECONDS",
    "INLINE_DRAIN_LOCK_TIMEOUT_SECONDS",
    "INLINE_DRAIN_MAX_ENTRIES",
    "LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_RESULT_SCHEMA",
    "LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA",
    "LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA",
    "RETENTION_PRESSURE_BYTES",
    "DrainResult",
    "drain_local_authority_shadow_outbox",
    "local_authority_shadow_status",
    "read_local_authority_shadow",
]
