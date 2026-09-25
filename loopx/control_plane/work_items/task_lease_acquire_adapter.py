"""Decision-free authority projection and transport for native lease acquire."""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from ...history import load_registry
from ...paths import resolve_runtime_root
from ..coordination.coordination_state_contract_generated import (
    LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA,
    TASK_LEASE_ACQUIRE_REQUEST_SCHEMA,
    TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA,
    TASK_LEASE_CANONICAL_LIFECYCLE_REQUEST_SCHEMA,
    TASK_LEASE_CANONICAL_CLAIM_TRANSFER_REQUEST_SCHEMA,
    TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA,
)
from ..coordination.runtime_shadow import resolve_coordination_runtime_shadow_config
from ..goals.legacy_event_source import (
    state_event_log_candidates as _state_event_log_candidates,
)
from ..todos.contract import normalize_todo_id
from ..todos.handoff_mode import HANDOFF_MODE_LEGACY
from .local_lease_record import TASK_LEASE_SCHEMA_VERSION, TaskLeaseError

TASK_LEASE_ACQUIRE_NATIVE_SCHEMA_VERSION = TASK_LEASE_ACQUIRE_REQUEST_SCHEMA
TASK_LEASE_LIFECYCLE_NATIVE_SCHEMA_VERSION = TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA
TASK_LEASE_AUTHORITY_SNAPSHOT_ATTEMPTS = 3


def _authority_source_receipt(source_id: str, path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve(strict=False)
    try:
        content = resolved.read_bytes()
    except FileNotFoundError:
        return {
            "source_id": source_id,
            "path": str(resolved),
            "state": "missing",
            "sha256": None,
        }
    return {
        "source_id": source_id,
        "path": str(resolved),
        "state": "file",
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _registry_goal(registry: dict[str, Any], goal_id: str) -> dict[str, Any] | None:
    from ...registry import registry_goals

    return next(
        (
            dict(goal)
            for goal in registry_goals(registry)
            if str(goal.get("id") or "") == goal_id
        ),
        None,
    )


def _task_lease_authority_source_paths(
    *,
    registry_path: Path,
    registry: dict[str, Any],
    goal_id: str,
) -> tuple[dict[str, Any] | None, Path | None, list[tuple[str, Path]]]:
    from ...rollout_event_log import rollout_event_log_path
    from ...state_refresh import resolve_goal_state
    sources: list[tuple[str, Path]] = [("registry", registry_path)]
    goal = _registry_goal(registry, goal_id)
    if goal is None:
        return None, None, sources
    try:
        _goal, _project, state_file = resolve_goal_state(
            registry=registry,
            goal_id=goal_id,
            project_override=None,
            state_file_override=None,
        )
    except (OSError, ValueError):
        return goal, None, sources
    sources.append(("active_state", state_file))
    for index, path in enumerate(
        _state_event_log_candidates(
            goal,
            state_path=state_file,
        )
    ):
        sources.append((f"state_event_{index}", path))
    projection_runtime_root = resolve_runtime_root(
        registry,
        None,
        registry_path=registry_path,
    )
    sources.append(
        (
            "rollout_events",
            rollout_event_log_path(projection_runtime_root, goal_id),
        )
    )
    unique: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for source_id, path in sources:
        key = str(path.expanduser().resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        unique.append((source_id, path))
    return goal, state_file, unique


def _raw_registered_agent_candidates(goal: dict[str, Any]) -> list[Any]:
    coordination = goal.get("coordination")
    spawn_policy = goal.get("spawn_policy")
    return [
        coordination.get("registered_agents")
        if isinstance(coordination, dict)
        else None,
        goal.get("registered_agents"),
        spawn_policy.get("registered_agents")
        if isinstance(spawn_policy, dict)
        else None,
    ]


def _compact_task_lease_todo_fact(todo: dict[str, Any]) -> dict[str, Any] | None:
    todo_id = normalize_todo_id(todo.get("todo_id"))
    if not todo_id:
        return None
    compacted = {
        "todo_id": todo_id,
        "status": todo.get("status"),
        "claimed_by": todo.get("claimed_by"),
        "excluded_agents": todo.get("excluded_agents"),
    }
    for field in ("role", "task_class", "bound_agent", "blocks_agent"):
        if field in todo:
            compacted[field] = todo.get(field)
    return compacted


def _compact_task_lease_todos(projection: dict[str, Any]) -> list[dict[str, Any]]:
    todos: list[dict[str, Any]] = []
    for raw_todo in projection.get("todos") or []:
        if not isinstance(raw_todo, dict):
            continue
        compact_todo = _compact_task_lease_todo_fact(raw_todo)
        if compact_todo is not None:
            todos.append(compact_todo)
    return todos


def _task_lease_authority_projection(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    goal: dict[str, Any] | None,
    state_file: Path | None,
    project_todos: bool = True,
) -> tuple[Any, list[Any], list[dict[str, Any]], dict[str, Any] | None]:
    from ...todos import list_goal_todos
    from ..goals.active_state_metadata import parse_state_frontmatter

    if goal is None:
        return HANDOFF_MODE_LEGACY, [], [], None

    handoff_mode: Any = HANDOFF_MODE_LEGACY
    if state_file is not None and state_file.exists():
        handoff_mode = parse_state_frontmatter(
            state_file.read_text(encoding="utf-8")
        ).get("handoff_mode")
    if not project_todos:
        return handoff_mode, _raw_registered_agent_candidates(goal), [], None
    try:
        projection = list_goal_todos(
            registry_path=registry_path,
            goal_id=goal_id,
        )
    except (OSError, ValueError) as exc:
        projection_error = {
            "code": "todo_projection_unavailable",
            "message": "cannot resolve todo projection for task lease",
            "payload": {
                "goal_id": goal_id,
                "todo_id": todo_id,
                "error": str(exc),
            },
        }
        return (
            handoff_mode,
            _raw_registered_agent_candidates(goal),
            [],
            projection_error,
        )
    return (
        handoff_mode,
        _raw_registered_agent_candidates(goal),
        _compact_task_lease_todos(projection),
        None,
    )


def _authority_source_identity(
    sources: list[tuple[str, Path]],
) -> list[tuple[str, str]]:
    return [
        (source_id, str(path.expanduser().resolve(strict=False)))
        for source_id, path in sources
    ]


def _task_lease_authority_snapshot_attempt(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    project_todos: bool = True,
) -> dict[str, Any] | None:
    registry_receipt_before = _authority_source_receipt("registry", registry_path)
    registry = load_registry(registry_path)
    goal, state_file, sources = _task_lease_authority_source_paths(
        registry_path=registry_path,
        registry=registry,
        goal_id=goal_id,
    )
    receipts_before = [
        _authority_source_receipt(source_id, path) for source_id, path in sources
    ]
    if receipts_before[0] != registry_receipt_before:
        return None

    handoff_mode, registered_candidates, todos, projection_error = (
        _task_lease_authority_projection(
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=todo_id,
            goal=goal,
            state_file=state_file,
            project_todos=project_todos,
        )
    )

    registry_after = load_registry(registry_path)
    _goal_after, _state_file_after, sources_after = _task_lease_authority_source_paths(
        registry_path=registry_path,
        registry=registry_after,
        goal_id=goal_id,
    )
    if _authority_source_identity(sources_after) != _authority_source_identity(sources):
        return None
    receipts_after = [
        _authority_source_receipt(source_id, path) for source_id, path in sources_after
    ]
    if receipts_before != receipts_after:
        return None
    return {
        "handoff_mode": handoff_mode,
        "registered_agent_candidates": registered_candidates,
        "todos": todos,
        "todo_projection_error": projection_error,
        "source_receipts": receipts_after,
    }


def task_lease_acquire_authority_facts(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    project_todos: bool = True,
) -> dict[str, Any]:
    """Project source-stable facts; inspection can defer Todo parsing to TS demand."""

    for _attempt in range(TASK_LEASE_AUTHORITY_SNAPSHOT_ATTEMPTS):
        facts = _task_lease_authority_snapshot_attempt(
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=todo_id,
            project_todos=project_todos,
        )
        if facts is not None:
            return facts
    raise TaskLeaseError(
        "task-lease authority sources changed while preparing acquire; retry",
        code="authority_source_changed",
        payload={"goal_id": goal_id, "todo_id": todo_id},
    )


def _require_native_acquire_shape(payload: object) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != TASK_LEASE_SCHEMA_VERSION
        or payload.get("action") != "acquire"
        or not isinstance(payload.get("ok"), bool)
    ):
        raise RuntimeError("native task-lease acquire result shape mismatch")
    return payload


def _canonical_lease_authority_facts(registry_path: Path, goal_id: str) -> dict[str, Any]:
    """Only registration is external to the canonical head; bind its source."""
    for _attempt in range(TASK_LEASE_AUTHORITY_SNAPSHOT_ATTEMPTS):
        before = _authority_source_receipt("registry", registry_path)
        goal = _registry_goal(load_registry(registry_path), goal_id)
        after = _authority_source_receipt("registry", registry_path)
        if before != after:
            continue
        if goal is None:
            raise TaskLeaseError("canonical lease lifecycle goal is not registered", code="goal_not_found")
        return {
            # This neutral transport value never decides a canonical mode.
            "handoff_mode": HANDOFF_MODE_LEGACY,
            "registered_agent_candidates": _raw_registered_agent_candidates(goal),
            "todos": [],
            "todo_projection_error": None,
            "source_receipts": [after],
        }
    raise TaskLeaseError("canonical lease lifecycle registry changed; retry", code="authority_source_changed")


def _finalize_native_acquire_result(
    payload: dict[str, Any],
    *,
    authority: dict[str, Any],
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    legacy_provider_projection: bool,
) -> dict[str, Any]:
    result = dict(payload)
    if legacy_provider_projection and result.get("ok") is True:
        result["handoff_mode"] = authority.get("handoff_mode") or HANDOFF_MODE_LEGACY
        result.pop("settlement", None)
    if result.get("ok") is True and result.get("acquired") is True:
        from ..coordination.runtime_shadow_writer_adapter import (
            settle_lease_runtime_shadow_capture,
        )

        result = settle_lease_runtime_shadow_capture(
            result,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
        )
    return result


def execute_native_task_lease_acquire(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    owner: str,
    idempotency_key: str,
    ttl_seconds: int | None = None,
    write_scopes: list[str] | None = None,
    expected_version: int | None = None,
    _legacy_provider_projection: bool = False,
) -> dict[str, Any]:
    """Transport one compact acquire request to the native TypeScript owner."""

    from ..effect_runtime import effect_runtime_result

    from ..coordination.local_authority import local_authority_is_promoted

    canonical = local_authority_is_promoted(runtime_root=runtime_root, goal_id=str(goal_id))
    for attempt in range(TASK_LEASE_AUTHORITY_SNAPSHOT_ATTEMPTS):
        authority = _canonical_lease_authority_facts(registry_path, str(goal_id)) if canonical else task_lease_acquire_authority_facts(
            registry_path=registry_path,
            goal_id=str(goal_id or ""),
            todo_id=str(todo_id or ""),
        )
        request = {
            "schema_version": TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA if canonical else TASK_LEASE_ACQUIRE_NATIVE_SCHEMA_VERSION,
            "runtime_root": str(runtime_root),
            "goal_id": goal_id,
            "todo_id": todo_id,
            "owner": owner,
            "idempotency_key": idempotency_key,
            "ttl_seconds": ttl_seconds,
            "write_scopes": list(write_scopes or []),
            "expected_version": expected_version,
            "authority": authority,
        }
        registry = load_registry(registry_path)
        goal = _registry_goal(registry, str(goal_id))
        if not canonical and resolve_coordination_runtime_shadow_config(goal).enabled:
            request["runtime_shadow"] = {
                "schema_version": LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA,
                "provider": "file_v0",
            }
        payload = _require_native_acquire_shape(
            effect_runtime_result("task_lease.acquire.native", request, timeout=15.0)
        )
        if (
            payload.get("error_code") == "authority_source_changed"
            and attempt + 1 < TASK_LEASE_AUTHORITY_SNAPSHOT_ATTEMPTS
        ):
            continue
        if canonical:
            if payload.get("ok") is True and (
                payload.get("source_authority") not in ("file_v0", "sqlite_v0")
                or payload.get("decision_read_from_provider") is not True
                or payload.get("legacy_fallback_used") is not False
            ):
                raise RuntimeError("canonical acquire omitted valid provider evidence")
            return payload
        return _finalize_native_acquire_result(
            payload,
            authority=authority,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=str(goal_id),
            todo_id=str(todo_id),
            legacy_provider_projection=_legacy_provider_projection,
        )
    raise RuntimeError("native task-lease acquire exhausted source-CAS retries")


def _compact_lifecycle_todo(
    todo: dict[str, Any] | None,
    *,
    todo_id: str,
) -> dict[str, Any] | None:
    if todo is None:
        return None
    value = dict(todo)
    value["todo_id"] = todo_id
    # Active-state parser rows historically expose ``done`` while the compact
    # authority projection exposes ``status``.  Normalize that compatibility
    # shape at the transport edge; the TS owner consumes one typed field.
    if "status" not in value and "done" in value:
        value["status"] = "done" if value.get("done") is True else "open"
    value.setdefault("excluded_agents", [])
    return {
        key: value[key]
        for key in (
            "todo_id",
            "status",
            "claimed_by",
            "excluded_agents",
            "role",
            "task_class",
            "bound_agent",
            "blocks_agent",
        )
        if key in value
    }


def _native_lifecycle_failure(
    payload: dict[str, Any],
    *,
    operation: str,
    authority: dict[str, Any] | None,
    owner: str | None,
) -> None:
    error_payload = {
        key: value
        for key, value in payload.items()
        if key not in {
            "ok",
            "schema_version",
            "action",
            "error",
            "error_code",
            "settlement",
        }
    }
    handoff_mode = (
        authority.get("handoff_mode") if isinstance(authority, dict) else None
    )
    if handoff_mode and "handoff_mode" not in error_payload:
        error_payload["handoff_mode"] = handoff_mode
    if (
        operation == "holder_verify"
        and owner
        and payload.get("error_code") == "handoff_mode_requires_lease"
        and "actor_agent_id" not in error_payload
    ):
        error_payload["actor_agent_id"] = owner
    raise TaskLeaseError(
        str(payload.get("error") or "native task-lease lifecycle rejected"),
        code=str(payload.get("error_code") or "task_lease_lifecycle_rejected"),
        payload=error_payload,
    )


def _normalize_lifecycle_expected_version(
    value: Any,
    *,
    operation: str,
) -> int | None:
    """Keep the legacy Python facade's expected-version coercion at the edge.

    The native decoder intentionally accepts only JSON numbers.  Historical
    Python callers, however, passed argparse/MCP values through ``int`` and
    therefore accepted numeric strings and integral-looking floats.  Coerce
    those values before transport, while retaining the old typed rejection for
    bool (which must never silently become version 1 or 0).
    """

    if value is None:
        return None
    if isinstance(value, bool):
        raise TaskLeaseError(
            f"task lease {operation} requires an integer lease version",
            code="version_required",
            payload={"action": operation, "expected_version": value},
        )
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TaskLeaseError(
            f"task lease {operation} requires an integer lease version",
            code="version_required",
            payload={"action": operation, "expected_version": value},
        ) from exc


def _settle_canonical_lifecycle_result(
    result: dict[str, Any], *, request: dict[str, Any], registry_path: Path | None,
) -> dict[str, Any]:
    """Validate provider provenance and deliver the committed Todo projection."""
    if (
        request["operation"] not in {"renew", "transfer", "release"}
        or result.get("source_authority") not in ("file_v0", "sqlite_v0")
        or result.get("decision_read_from_provider") is not True
        or result.get("legacy_fallback_used") is not False
    ):
        raise RuntimeError("canonical task-lease result has invalid provider evidence")
    # The provider already owns its lease/event/receipt. No legacy shadow write
    # is allowed here, and lease-only commands require no Todo display delivery.
    if request.get("transfer_claim") is not True:
        return result
    if result.get("transfer_claim") is not True or result.get("claimed_by") != request["new_owner"]:
        raise RuntimeError("canonical claim transfer omitted its committed result")
    from ..todos.provider_projection import settle_canonical_todo_projection

    assert registry_path is not None
    # Display failure cannot undo the joint authority commit. The existing
    # outbox reports pending; retries render current head, not the old receipt.
    return settle_canonical_todo_projection(
        result, registry_path=registry_path, runtime_root=Path(request["runtime_root"]),
        goal_id=request["goal_id"],
    )


def execute_native_task_lease_lifecycle(
    *,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    operation: str,
    registry_path: Path | None = None,
    owner: str | None = None,
    idempotency_key: str | None = None,
    expected_version: int | None = None,
    ttl_seconds: int | None = None,
    new_owner: str | None = None,
    new_idempotency_key: str | None = None,
    transfer_claim: bool = False,
    todo: dict[str, Any] | None = None,
    delegated_authority: bool = False,
    allow_user_gate_auto_acquire: bool = False,
    require_active_when_fence_supplied: bool = True,
    lock_token: str | None = None,
    committed: bool = False,
    release_lease: bool = False,
    fence_owner: str | None = None,
    fence_idempotency_key: str | None = None,
    fence_expected_version: int | None = None,
    fence_expected_lease_epoch: int | None = None,
    fence_operation_id: str | None = None,
    owner_pid: int | None = None,
    _legacy_provider_projection: bool = False,
    _now: datetime | None = None,
) -> dict[str, Any]:
    """Call the native local task-lease lifecycle owner.

    This adapter deliberately contains no lease decision or persistence rule.
    It projects the current authority sources once, sends one versioned request
    to the managed runtime, and translates the public v0 envelope back to the
    Python compatibility surface.
    """

    normalized_operation = str(operation or "").strip()
    if not isinstance(transfer_claim, bool) or (transfer_claim and normalized_operation != "transfer"):
        raise TaskLeaseError("transfer_claim is valid only for transfer", code="invalid_claim_transfer_request")
    normalized_expected_version = _normalize_lifecycle_expected_version(
        expected_version,
        operation=normalized_operation or "lifecycle",
    )
    needs_authority = normalized_operation in {
        "renew",
        "transfer",
        "terminal_verify",
        "holder_verify",
    }
    # Keyless fences have no caller-supplied execution identity. Give each
    # bridge invocation a stable receipt id so a transport retry can adopt the
    # same held lock, while an independent invocation gets a fresh id and
    # cannot be mistaken for a replay after a prior close.
    if normalized_operation in {"holder_verify", "terminal_verify"} and (
        fence_operation_id is None and idempotency_key is None
    ):
        fence_operation_id = secrets.token_hex(32)
    for attempt in range(TASK_LEASE_AUTHORITY_SNAPSHOT_ATTEMPTS):
        authority: dict[str, Any] | None = None
        canonical_lifecycle = False
        if normalized_operation in {"renew", "transfer", "release"}:
            from ..coordination.local_authority import local_authority_is_promoted

            canonical_lifecycle = local_authority_is_promoted(
                runtime_root=runtime_root, goal_id=goal_id
            )
        if transfer_claim and not canonical_lifecycle:
            raise TaskLeaseError(
                "atomic claim transfer requires canonical authority; inspect provider readiness first",
                code="claim_transfer_requires_canonical_authority",
            )
        if registry_path is not None and (needs_authority or (normalized_operation == "release" and not canonical_lifecycle)):
            try:
                authority = _canonical_lease_authority_facts(registry_path, goal_id) if canonical_lifecycle else task_lease_acquire_authority_facts(
                    registry_path=registry_path,
                    goal_id=str(goal_id or ""),
                    todo_id=str(todo_id or ""),
                )
            except FileNotFoundError:
                # Release is a cleanup operation and may run without its
                # optional authority projection.  Every operation that can
                # authorize a lease mutation remains fail-closed when the
                # canonical registry is unavailable.
                if needs_authority:
                    raise
                authority = None
            except TaskLeaseError:
                if needs_authority:
                    raise
                # Release is intentionally usable for cleanup even when its
                # optional source projection is unavailable.
                authority = None
        request: dict[str, Any] = {
            "schema_version": (
                TASK_LEASE_CANONICAL_LIFECYCLE_REQUEST_SCHEMA
                if canonical_lifecycle else TASK_LEASE_LIFECYCLE_NATIVE_SCHEMA_VERSION
            ),
            "operation": normalized_operation,
            "runtime_root": str(runtime_root),
            "goal_id": goal_id,
            "todo_id": todo_id,
            "owner": owner,
            "idempotency_key": idempotency_key,
            "expected_version": normalized_expected_version,
            "ttl_seconds": ttl_seconds,
            "new_owner": new_owner,
            "new_idempotency_key": new_idempotency_key,
            "authority": authority,
            "delegated_authority": delegated_authority,
            "allow_user_gate_auto_acquire": allow_user_gate_auto_acquire,
            "require_active_when_fence_supplied": require_active_when_fence_supplied,
            "lock_token": lock_token,
            "committed": committed,
            "release_lease": release_lease,
            "fence_owner": fence_owner,
            "fence_idempotency_key": fence_idempotency_key,
            "fence_expected_version": fence_expected_version,
            "fence_expected_lease_epoch": fence_expected_lease_epoch,
            "fence_operation_id": fence_operation_id,
            # A held fence outlives this one-shot managed-runtime request.  Its
            # liveness owner is the Python caller, so a crashed caller can be
            # reclaimed even though the shared Node server remains alive.
            "owner_pid": owner_pid
            if owner_pid is not None
            else (
                os.getpid()
                if normalized_operation in {"terminal_verify", "holder_verify"}
                else None
            ),
            # The normal production path leaves the clock to the managed
            # runtime.  A caller-provided instant is an internal deterministic
            # test seam used by Python parity tests; it is never exposed as a
            # CLI argument or persisted authority fact.
            "current_time": _now.isoformat() if _now is not None else None,
        }
        if canonical_lifecycle:
            request = {key: request[key] for key in (
                "schema_version", "operation", "runtime_root", "goal_id", "todo_id",
                "owner", "idempotency_key", "expected_version", "ttl_seconds", "authority", "current_time",
            )}
            if normalized_operation == "transfer":
                request.update(new_owner=new_owner, new_idempotency_key=new_idempotency_key)
                if transfer_claim:
                    request.update(schema_version=TASK_LEASE_CANONICAL_CLAIM_TRANSFER_REQUEST_SCHEMA,
                                   transfer_claim=True)
        if registry_path is not None and not canonical_lifecycle:
            registry = load_registry(registry_path)
            goal = _registry_goal(registry, str(goal_id))
            if resolve_coordination_runtime_shadow_config(goal).enabled:
                request["runtime_shadow"] = {
                    "schema_version": LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA,
                    "provider": "file_v0",
                }
                if normalized_operation == "fence_close" and committed and release_lease:
                    # The preceding Todo write may have changed the source.
                    # Capture the current graph, not the terminal-verify snapshot
                    # or the append-retained lease directory. Default-off cleanup
                    # still needs no authority read.
                    request["authority"] = task_lease_acquire_authority_facts(
                        registry_path=registry_path, goal_id=goal_id, todo_id=todo_id,
                    )
        compacted_todo = _compact_lifecycle_todo(todo, todo_id=str(todo_id))
        if compacted_todo is not None and not canonical_lifecycle:
            request["todo"] = compacted_todo
        from ..effect_runtime import effect_runtime_result

        payload = effect_runtime_result(
            "task_lease.lifecycle.native",
            request,
            timeout=15.0,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("native task-lease lifecycle result shape mismatch")
        if payload.get("schema_version") != TASK_LEASE_SCHEMA_VERSION:
            raise RuntimeError("native task-lease lifecycle schema mismatch")
        if payload.get("action") != normalized_operation:
            raise RuntimeError("native task-lease lifecycle action mismatch")
        if payload.get("error_code") == "authority_source_changed" and attempt + 1 < TASK_LEASE_AUTHORITY_SNAPSHOT_ATTEMPTS:
            continue
        if payload.get("ok") is not True:
            _native_lifecycle_failure(
                payload,
                operation=normalized_operation,
                authority=authority,
                owner=owner,
            )
        result = dict(payload)
        if canonical_lifecycle and "source_authority" not in result:
            raise RuntimeError("canonical lease lifecycle omitted provider evidence")
        if "source_authority" in result:
            return _settle_canonical_lifecycle_result(result, request=request, registry_path=registry_path)
        if authority is not None and authority.get("handoff_mode") and "handoff_mode" not in result:
            result["handoff_mode"] = authority["handoff_mode"]
        if _legacy_provider_projection:
            result.pop("settlement", None)
        if "coordination_runtime_shadow_capture" in result:
            from ..coordination.runtime_shadow_writer_adapter import (
                settle_lease_runtime_shadow_capture,
            )

            result = settle_lease_runtime_shadow_capture(
                result,
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=str(goal_id),
            )
        # lock_token is an internal bridge value.  Callers that need a held
        # fence read it from the nested native payload before redacting it.
        return result
    raise RuntimeError("native task-lease lifecycle exhausted source-CAS retries")


def inspect_native_task_lease(
    *, registry_path: Path, runtime_root: Path, goal_id: str, todo_id: str,
) -> dict[str, Any]:
    """Project source facts; the typed reader owns lease interpretation."""
    from ..coordination.local_authority import (
        LOCAL_AUTHORITY_SOURCES, LocalCoordinationAuthorityUnavailable,
        local_authority_is_promoted,
    )
    from ..effect_runtime import effect_runtime_result

    for attempt in range(TASK_LEASE_AUTHORITY_SNAPSHOT_ATTEMPTS):
        canonical = local_authority_is_promoted(runtime_root=runtime_root, goal_id=goal_id)
        # TS alone decides whether the retained lease needs current Todo facts.
        # Inactive legacy inspection must not parse the full work document.
        authority = _canonical_lease_authority_facts(registry_path, goal_id) if canonical else task_lease_acquire_authority_facts(
            registry_path=registry_path, goal_id=goal_id, todo_id=todo_id, project_todos=False,
        )
        request = {
            "schema_version": "loopx_task_lease_inspect_request_v0",
            "source": "canonical" if canonical else "legacy",
            "phase": "effective_lease" if canonical else "lease_record",
            "runtime_root": str(runtime_root.resolve()), "goal_id": goal_id,
            "todo_id": todo_id, "authority": authority,
        }
        result = effect_runtime_result("task_lease.inspect.native", request)
        if isinstance(result, dict) and result.get("todo_projection_required") is True:
            if canonical or result.get("schema_version") != TASK_LEASE_SCHEMA_VERSION or result.get("ok") is not True or result.get("action") != "inspect":
                raise RuntimeError("native lease inspection requested an invalid source projection")
            request["phase"] = "effective_lease"
            request["authority"] = task_lease_acquire_authority_facts(
                registry_path=registry_path, goal_id=goal_id, todo_id=todo_id,
            )
            # Re-read the lease and fence: neither the record nor its expiry is
            # assumed unchanged while Python prepares the Todo projection.
            result = effect_runtime_result("task_lease.inspect.native", request)
        if not isinstance(result, dict) or result.get("schema_version") != TASK_LEASE_SCHEMA_VERSION or result.get("action") != "inspect" or not isinstance(result.get("ok"), bool):
            raise RuntimeError("native lease inspection result shape mismatch")
        if result.get("error_code") == "authority_source_changed" and attempt + 1 < TASK_LEASE_AUTHORITY_SNAPSHOT_ATTEMPTS:
            continue
        if result.get("ok") is not True:
            code = str(result.get("error_code") or "task_lease_inspection_unavailable")
            exception = LocalCoordinationAuthorityUnavailable if canonical and code != "corrupt_lease" else TaskLeaseError
            raise exception(str(result.get("error") or "lease inspection unavailable"), code=code, payload=result)
        if not isinstance(result.get("active"), bool) or (canonical and (
            result.get("source_authority") not in LOCAL_AUTHORITY_SOURCES
            or not isinstance(result.get("provider_revision"), str)
            or result.get("legacy_fallback_used") is not False
        )):
            raise RuntimeError("native lease inspection omitted source evidence")
        return result
    raise RuntimeError("native lease inspection exhausted source retries")
