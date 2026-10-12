from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from ...agent_registry import (
    registered_agent_ids_from_registry,
    require_registered_agent_id,
)
from ...history import load_registry
from ...paths import resolve_runtime_root
from ..runtime.time import now_utc as runtime_now_utc
from ..runtime.time import parse_timestamp, utc_isoformat
from ..coordination.authority_core import (
    write_scopes_overlap as core_write_scopes_overlap,
)
from ..todos.contract import (
    normalize_required_write_scopes,
    normalize_todo_claimed_by,
    normalize_todo_excluded_agents,
    normalize_todo_id,
)
from ..todos.handoff_mode import (
    HANDOFF_MODE_HARD_LEASE,
    HANDOFF_MODE_LEGACY,
    HandoffModeError,
    goal_handoff_mode_for_goal,
    resolve_todo_completion_handoff,
)
from .local_lease_record import (
    TASK_LEASE_SCHEMA_VERSION,
    TaskLeaseError,
    assert_expected_version as assert_expected_version,
    build_lease as build_lease,
    lease_acquire_ttl_seconds as lease_acquire_ttl_seconds,
    lease_epoch as lease_epoch,
    lease_version as lease_version,
    read_lease as read_lease,
    require_expected_version as require_expected_version,
    write_lease as write_lease,
)
from ..content_digest import BARE_SHA256_PATTERN

DEFAULT_TASK_LEASE_TTL_SECONDS = 45 * 60
MAX_TASK_LEASE_TTL_SECONDS = 24 * 60 * 60
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:@/-]{1,160}$")


def now_utc() -> datetime:
    return runtime_now_utc()


_DEFAULT_NOW_UTC = now_utc


def isoformat(value: datetime) -> str:
    """Retain the historical task-lease timestamp helper import surface."""

    return utc_isoformat(value)


def normalize_idempotency_key(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate or not IDEMPOTENCY_KEY_PATTERN.match(candidate):
        raise TaskLeaseError(
            "idempotency key must be a public-safe token",
            code="invalid_idempotency_key",
        )
    return candidate


def normalize_owner(value: Any) -> str:
    owner = normalize_todo_claimed_by(value)
    if not owner:
        raise TaskLeaseError("owner must be a public-safe agent id", code="invalid_owner")
    return owner


def normalize_ttl_seconds(value: int | None) -> int:
    ttl = DEFAULT_TASK_LEASE_TTL_SECONDS if value is None else int(value)
    if ttl <= 0 or ttl > MAX_TASK_LEASE_TTL_SECONDS:
        raise TaskLeaseError(
            f"ttl seconds must be between 1 and {MAX_TASK_LEASE_TTL_SECONDS}",
            code="invalid_ttl",
        )
    return ttl


def normalize_goal_id(value: Any) -> str:
    goal_id = str(value or "").strip()
    if not goal_id or goal_id in {".", ".."} or "/" in goal_id or "\\" in goal_id:
        raise TaskLeaseError("goal id must be a single path segment", code="invalid_goal_id")
    if Path(goal_id).name != goal_id:
        raise TaskLeaseError("goal id must not include path traversal", code="invalid_goal_id")
    return goal_id


def normalize_lease_todo_id(value: Any) -> str:
    todo_id = normalize_todo_id(value)
    if not todo_id:
        raise TaskLeaseError("todo id must use the todo_<token> shape", code="invalid_todo_id")
    return todo_id


def task_lease_dir(*, runtime_root: Path, goal_id: str) -> Path:
    return runtime_root / "goals" / normalize_goal_id(goal_id) / "task-leases"


def task_lease_path(*, runtime_root: Path, goal_id: str, todo_id: str) -> Path:
    return task_lease_dir(runtime_root=runtime_root, goal_id=goal_id) / f"{normalize_lease_todo_id(todo_id)}.json"


def task_lease_lock_path(*, runtime_root: Path, goal_id: str) -> Path:
    return task_lease_dir(runtime_root=runtime_root, goal_id=goal_id) / ".task-leases"


class _VerifiedTaskLeaseFence(dict):
    """Key-verified fence payload with its release hook held out-of-band.

    The hook lives on the instance attribute, never inside the mapping, so
    the payload stays JSON-serializable for every consumer at all times.
    """

    release_hook: Callable[[bool, bool], dict[str, Any] | None] | None = None
    abandon_hook: Callable[[], bool] | None = None
    # The fence receipt id is an internal bridge detail.  Keep it out of the
    # historical JSON mapping while carrying it across the one-shot close RPC.
    fence_operation_id: str | None = None


def _native_fence_payload(
    result: dict[str, Any],
    *,
    operation: str,
) -> tuple[dict[str, Any], str | None, str | None]:
    raw = result.get("fence")
    if not isinstance(raw, dict):
        raise TaskLeaseError(
            f"native task-lease {operation} result omitted fence",
            code="invalid_fence_result",
        )
    token = raw.get("lock_token")
    operation_id = raw.get("fence_operation_id")
    if operation_id is not None and (
        not isinstance(operation_id, str)
        or not BARE_SHA256_PATTERN.fullmatch(operation_id)
    ):
        raise TaskLeaseError(
            f"native task-lease {operation} result has an invalid fence operation id",
            code="invalid_fence_result",
        )
    if raw.get("required") is False:
        return {
            key: value
            for key, value in raw.items()
            if key not in {"lock_token", "fence_operation_id"}
        }, None, operation_id
    if not isinstance(token, str) or not token:
        raise TaskLeaseError(
            f"native task-lease {operation} result omitted lock token",
            code="invalid_fence_result",
        )
    public = {
        key: value
        for key, value in raw.items()
        if key not in {"lock_token", "fence_operation_id"}
    }
    return public, token, operation_id


def _close_native_fence(
    fence: dict[str, Any] | None,
    *,
    committed: bool,
    release_lease: bool,
    suppress_errors: bool = False,
) -> dict[str, Any] | None:
    hook = getattr(fence, "release_hook", None)
    if hook is None or not isinstance(fence, dict):
        return None
    try:
        result = hook(committed, release_lease)
    except (OSError, TaskLeaseError, RuntimeError):
        abandon = getattr(fence, "abandon_hook", None)
        abandoned = False
        if abandon is not None:
            try:
                abandoned = abandon()
            except OSError:
                abandoned = False
        if abandoned:
            # A token claim proved that no native closer was in its critical
            # section, so this caller safely retired only its own held lock.
            # Disarm the semantic close hook; the lease intentionally remains
            # active for explicit release or TTL expiry.
            fence.release_hook = None  # type: ignore[attr-defined]
            fence.abandon_hook = None  # type: ignore[attr-defined]
        if not suppress_errors:
            raise
        fence["released"] = False
        return None
    # Only disarm after the close RPC has completed.  A failed close remains
    # retryable by the context manager's cleanup path.
    fence.release_hook = None  # type: ignore[attr-defined]
    fence.abandon_hook = None  # type: ignore[attr-defined]
    if isinstance(result, dict) and result.get("released") is True:
        fence["released"] = True
    elif committed and release_lease:
        fence["released"] = False
    return result


def _execute_native_task_lease_lifecycle(**kwargs: Any) -> dict[str, Any]:
    from .task_lease_acquire_adapter import execute_native_task_lease_lifecycle

    # Existing Python parity tests monkeypatch this module's clock. Production
    # calls leave time ownership in TypeScript; only the replaced test clock is
    # forwarded through the internal adapter seam.
    if now_utc is not _DEFAULT_NOW_UTC:
        kwargs.setdefault("_now", now_utc())
    return execute_native_task_lease_lifecycle(**kwargs)


def goal_handoff_mode_for_lease(registry_path: Path, goal_id: str) -> str:
    """Resolve the goal handoff mode for lease verbs.

    An invalid ``handoff_mode`` value is re-typed as a TaskLeaseError. A goal
    or state file that cannot be resolved falls back to legacy: each lease
    verb's own todo-projection checks already own that failure surface.
    """

    try:
        return goal_handoff_mode_for_goal(registry_path=registry_path, goal_id=goal_id)
    except HandoffModeError as exc:
        raise TaskLeaseError(str(exc), code=exc.code, payload=exc.payload) from exc
    except (OSError, ValueError):
        return HANDOFF_MODE_LEGACY


def _optional_handoff_mode(registry_path: Path | None, goal_id: str) -> str | None:
    if registry_path is None:
        return None
    try:
        return goal_handoff_mode_for_lease(registry_path, goal_id)
    except (TaskLeaseError, OSError, ValueError):
        return None


@contextmanager
def hold_handoff_lease_holder_gate(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    actor_agent_id: str | None,
    runtime_root: Path | None = None,
) -> Iterator[dict[str, Any]]:
    """Hold the per-goal lease lock while proving the actor owns the lease.

    hard_lease handoff mode only: an ownership mutation (claim, claimed_by
    update, clear) on an existing todo requires the acting agent to hold that
    todo's time-active lease. Identity is compared with
    normalize_todo_claimed_by on both sides. Callers must already hold the
    markdown state lock; this guard then takes the per-goal lease lock, the
    same markdown-lock-then-lease-lock order used by the completion fence,
    and the caller keeps it held until the markdown write commits.
    """

    normalized_goal_id = normalize_goal_id(goal_id)
    normalized_todo_id = normalize_lease_todo_id(todo_id)
    actor = normalize_todo_claimed_by(actor_agent_id)
    if not actor:
        raise TaskLeaseError(
            "hard_lease handoff mode requires an attributed actor for "
            "ownership changes; provide --agent-id",
            code="handoff_mode_requires_lease",
            payload={
                "goal_id": normalized_goal_id,
                "todo_id": normalized_todo_id,
                "handoff_mode": HANDOFF_MODE_HARD_LEASE,
                "reason": "missing_actor",
            },
        )
    if runtime_root is None:
        runtime_root = runtime_root_from_registry(registry_path, None)
    result = _execute_native_task_lease_lifecycle(
        runtime_root=runtime_root,
        registry_path=registry_path,
        goal_id=normalized_goal_id,
        todo_id=normalized_todo_id,
        operation="holder_verify",
        owner=actor,
    )
    public, token, operation_id = _native_fence_payload(
        result,
        operation="holder_verify",
    )
    fence = _VerifiedTaskLeaseFence(public)
    fence.fence_operation_id = operation_id

    def close(committed: bool, release_lease: bool) -> dict[str, Any] | None:
        return _execute_native_task_lease_lifecycle(
            runtime_root=runtime_root,
            registry_path=registry_path,
            goal_id=normalized_goal_id,
            todo_id=normalized_todo_id,
            operation="fence_close",
            lock_token=token,
            committed=committed,
            release_lease=release_lease,
            fence_owner=actor,
            fence_idempotency_key=None,
            fence_expected_version=public.get("version")
            if isinstance(public.get("version"), int)
            else None,
            fence_expected_lease_epoch=public.get("lease_epoch")
            if isinstance(public.get("lease_epoch"), int)
            else None,
            fence_operation_id=operation_id,
            owner_pid=os.getpid(),
        )

    fence.release_hook = close
    from ...file_lock import release_cross_runtime_mutation_lock

    fence.abandon_hook = lambda: release_cross_runtime_mutation_lock(
        task_lease_lock_path(
            runtime_root=runtime_root,
            goal_id=normalized_goal_id,
        ),
        token=token,
    )
    try:
        yield fence
    finally:
        # A holder gate is only a lock-scoped proof; it never releases the
        # lease.  Always close it on exceptional caller paths.
        _close_native_fence(
            fence,
            committed=False,
            release_lease=False,
            suppress_errors=True,
        )


@contextmanager
def hold_task_lease_mutation_fence(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    todo: dict[str, Any],
    actor_agent_id: str | None,
    idempotency_key: str | None,
    expected_version: int | None = None,
    require_active_when_key_supplied: bool = True,
    handoff: dict[str, Any] | None = None,
    runtime_root: Path | None = None,
) -> Iterator[dict[str, Any]]:
    """Hold the per-goal lease lock while one todo lifecycle write commits.

    Task leases are optional in legacy and soft_claim handoff modes. Once an
    effective lease exists, however, the lifecycle writer must prove that it
    is the execution instance that acquired the lease. The idempotency key is
    the execution-instance fence; agent ids alone are insufficient because
    multiple host processes may share one peer identity.

    ``handoff`` carries the resolved goal handoff mode plus the delegated
    authority door marker. In hard_lease mode (without the door) the fence is
    mandatory: no effective lease is a typed error instead of a silent
    ``{"required": False}``, and a time-active lease whose owner constraint is
    no longer effective (the legacy self-disarm state) fails loudly instead of
    letting a keyless completion through. For a user-role ``user_gate``
    completion that supplies no explicit lease credentials, the fence mints
    the key itself under the same per-goal lease lock; an existing
    time-active lease is never displaced.
    """

    normalized_goal_id = normalize_goal_id(goal_id)
    normalized_todo_id = normalize_lease_todo_id(todo_id)
    if runtime_root is None:
        runtime_root = runtime_root_from_registry(registry_path, None)
    handoff = handoff or {}
    result = _execute_native_task_lease_lifecycle(
        runtime_root=runtime_root,
        registry_path=registry_path,
        goal_id=normalized_goal_id,
        todo_id=normalized_todo_id,
        operation="terminal_verify",
        owner=normalize_todo_claimed_by(actor_agent_id),
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        todo=todo,
        delegated_authority=handoff.get("handoff_gate_overridden") is True,
        allow_user_gate_auto_acquire=not require_active_when_key_supplied,
        require_active_when_fence_supplied=require_active_when_key_supplied,
    )
    public, token, operation_id = _native_fence_payload(
        result,
        operation="terminal_verify",
    )
    if token is None:
        yield public
        return
    fence = _VerifiedTaskLeaseFence(public)
    fence.fence_operation_id = operation_id
    native_fence = result.get("fence")
    if not isinstance(native_fence, dict):
        raise TaskLeaseError("native terminal fence result is malformed", code="invalid_fence_result")
    verified_owner = native_fence.get("owner")
    verified_key = idempotency_key or (
        f"auto-{normalized_todo_id}"
        if public.get("auto_acquired") is True
        else None
    )
    verified_version = native_fence.get("version")
    verified_lease_epoch = native_fence.get("lease_epoch")

    def close(committed: bool, release_lease: bool) -> dict[str, Any] | None:
        return _execute_native_task_lease_lifecycle(
            runtime_root=runtime_root,
            registry_path=registry_path,
            goal_id=normalized_goal_id,
            todo_id=normalized_todo_id,
            operation="fence_close",
            lock_token=token,
            committed=committed,
            release_lease=release_lease,
            fence_owner=verified_owner if isinstance(verified_owner, str) else None,
            fence_idempotency_key=verified_key if isinstance(verified_key, str) else None,
            fence_expected_version=verified_version if isinstance(verified_version, int) else None,
            fence_expected_lease_epoch=verified_lease_epoch
            if isinstance(verified_lease_epoch, int)
            else None,
            fence_operation_id=operation_id,
            owner_pid=os.getpid(),
        )

    fence.release_hook = close
    from ...file_lock import release_cross_runtime_mutation_lock

    fence.abandon_hook = lambda: release_cross_runtime_mutation_lock(
        task_lease_lock_path(
            runtime_root=runtime_root,
            goal_id=normalized_goal_id,
        ),
        token=token,
    )
    try:
        yield fence
    finally:
        _close_native_fence(
            fence,
            committed=False,
            release_lease=False,
            suppress_errors=True,
        )


def enter_terminal_todo_lease_fence(
    stack: ExitStack,
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    todo: dict[str, Any],
    actor_agent_id: str | None,
    state_text: str,
    mutation_authority: dict[str, Any],
    idempotency_key: str | None = None,
    expected_version: int | None = None,
    runtime_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve the goal handoff and hold the lease fence for one terminal write.

    Every transition that moves an existing todo to ``done`` (complete and
    supersede alike) is a completion for the lease contract: it must cross
    the same per-goal lease fence with the same typed outcomes, so a leased
    todo cannot be retired keylessly through a sibling verb. The caller keeps
    the state-file lock and ``stack`` open across its write and then calls
    ``release_verified_task_lease_fence`` with the commit outcome.
    """

    handoff = resolve_todo_completion_handoff(
        state_text=state_text,
        mutation_authority=mutation_authority,
    )
    fence = stack.enter_context(
        hold_task_lease_mutation_fence(
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=todo_id,
            todo=todo,
            actor_agent_id=actor_agent_id,
            idempotency_key=idempotency_key,
            expected_version=expected_version,
            require_active_when_key_supplied=(
                idempotency_key is not None or expected_version is not None
            ),
            handoff=handoff,
            runtime_root=runtime_root,
        )
    )
    return handoff, fence


def release_verified_task_lease_fence(
    fence: dict[str, Any] | None,
    *,
    committed: bool,
) -> None:
    """Release the lease behind a key-verified fence once its write committed.

    Must run while the hold_task_lease_mutation_fence context is still open so
    the per-goal lease lock it acquired is still held; the lease therefore
    cannot have been renewed, transferred, or re-acquired since the fence
    verified the owner, idempotency key, and version. The release reuses the
    CLI release semantics and persists the terminal record that fences the
    next lease generation.

    The private release hook rides on the fence object's attribute, not inside
    the payload mapping, and is disarmed here on every call. Only a committed,
    key-verified fence releases the lease; non-verified fences carry no hook
    and are never touched. A release failure never unwinds the committed
    lifecycle write: it is surfaced additively as fence["released"] = False
    and the active record is left for an explicit `loopx task-lease release`
    or TTL expiry.
    """

    if not isinstance(fence, dict):
        return
    if fence.get("execution_instance_verified") is not True:
        _close_native_fence(
            fence,
            committed=False,
            release_lease=False,
            suppress_errors=True,
        )
        return
    _close_native_fence(
        fence,
        committed=committed,
        release_lease=committed,
        suppress_errors=True,
    )


def runtime_root_from_registry(
    registry_path: Path,
    runtime_root_override: str | None,
) -> Path:
    """Effective runtime root for lease state: override, else registry root.

    A relative ``common_runtime_root`` resolves against the registry's project
    root, matching the observation hooks, so the lease files and every
    candidate observation of one goal share a single root.
    """

    registry = load_registry(registry_path)
    return resolve_runtime_root(
        registry,
        runtime_root_override,
        registry_path=registry_path,
    )


def task_lease_todo_projection(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
) -> dict[str, Any] | None:
    """Resolve the canonical Markdown/event todo projection for lease guards."""

    from ...control_plane.todos.list_readback import list_goal_todos

    try:
        payload = list_goal_todos(
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=todo_id,
        )
    except (OSError, ValueError) as exc:
        raise TaskLeaseError(
            "cannot resolve todo projection for task lease",
            code="todo_projection_unavailable",
            payload={"goal_id": goal_id, "todo_id": todo_id, "error": str(exc)},
        ) from exc
    todo = payload.get("todo")
    return dict(todo) if isinstance(todo, dict) else None


def task_lease_owner_constraint(
    todo: dict[str, Any] | None,
    *,
    owner: Any,
    registered_agents: list[str] | None = None,
) -> dict[str, Any]:
    from ..effect_runtime import effect_runtime_result

    normalized_owner = normalize_todo_claimed_by(owner)
    registered = registered_agents if registered_agents is not None else ([normalized_owner] if normalized_owner else [])
    result = effect_runtime_result("task_lease.owner_eligibility", {
        "todo": None if todo is None else {
            "status": str(todo.get("status") or "").strip().lower(),
            "claimed_by": normalize_todo_claimed_by(todo.get("claimed_by")),
            "excluded_agents": normalize_todo_excluded_agents(todo.get("excluded_agents")),
        },
        "owner": normalized_owner, "registered_agents": registered,
    })
    if not isinstance(result, dict) or result.get("schema_version") != "task_lease_owner_eligibility_v0":
        raise RuntimeError("typed lease owner constraint is missing; update the runtime")
    constraint = result.get("constraint")
    if not isinstance(constraint, dict) or not isinstance(constraint.get("effective"), bool):
        raise RuntimeError("typed lease owner constraint shape mismatch")
    if constraint["effective"] is False and not isinstance(constraint.get("reason"), str):
        raise RuntimeError("typed lease owner rejection omitted its reason")
    return constraint


def require_registered_task_lease_owner(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    owner: str,
) -> str:
    try:
        return require_registered_agent_id(
            registry_path=registry_path,
            goal_id=goal_id,
            agent_id=owner,
            field="task lease owner",
        )
    except ValueError as exc:
        raise TaskLeaseError(
            str(exc),
            code="owner_not_registered",
            payload={"goal_id": goal_id, "todo_id": todo_id, "owner": owner},
        ) from exc


def require_task_lease_owner_allowed(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    owner: str,
) -> dict[str, Any]:
    owner = require_registered_task_lease_owner(
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=todo_id,
        owner=owner,
    )
    todo = task_lease_todo_projection(
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=todo_id,
    )
    constraint = task_lease_owner_constraint(
        todo,
        owner=owner,
        registered_agents=registered_agent_ids_from_registry(registry_path, goal_id),
    )
    if constraint.get("effective") is not True:
        reason = str(constraint.get("reason") or "owner_not_allowed")
        if reason == "todo_not_found":
            message = "todo is missing from the canonical projection"
        elif reason == "todo_not_open":
            message = (
                f"task lease requires an open todo; "
                f"{todo_id!r} is {constraint.get('todo_status')!r}"
            )
        elif reason == "owner_excluded_from_todo":
            message = f"task lease owner {owner!r} is excluded from todo {todo_id!r}"
        elif reason == "owner_conflicts_with_claim":
            message = (
                f"task lease owner {owner!r} conflicts with todo claim "
                f"{constraint.get('claimed_by')!r}"
            )
        elif reason == "owner_not_registered":
            message = f"task lease owner {owner!r} is not registered for goal {goal_id!r}"
        else:
            message = f"task lease owner {owner!r} is not eligible for todo {todo_id!r}"
        raise TaskLeaseError(
            message,
            code=reason,
            payload={
                "goal_id": goal_id,
                "todo_id": todo_id,
                "owner": owner,
                **constraint,
            },
        )
    return todo


def lease_expires_at(lease: dict[str, Any] | None) -> datetime | None:
    return parse_timestamp((lease or {}).get("expires_at"))


def lease_is_active(lease: dict[str, Any] | None, *, at: datetime | None = None) -> bool:
    if not lease or lease.get("schema_version") != TASK_LEASE_SCHEMA_VERSION:
        return False
    if lease.get("status") != "active":
        return False
    expires_at = lease_expires_at(lease)
    return bool(expires_at and expires_at > (at or now_utc()))


def scope_root(scope: str) -> str:
    value = scope.strip()
    if value in {"*", "**", "./"}:
        return ""
    wildcard_indexes = [index for index in (value.find("*"), value.find("?"), value.find("[")) if index >= 0]
    if wildcard_indexes:
        value = value[: min(wildcard_indexes)]
    if "/" in value:
        value = value[: value.rfind("/") + 1]
    return value.rstrip("/")


def write_scopes_overlap(left: list[str], right: list[str]) -> bool:
    left_scopes = normalize_required_write_scopes(left)
    right_scopes = normalize_required_write_scopes(right)
    return core_write_scopes_overlap(left_scopes, right_scopes)


def acquire_task_lease(
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
) -> dict[str, Any]:
    from .task_lease_acquire_adapter import execute_native_task_lease_acquire

    payload = execute_native_task_lease_acquire(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=todo_id,
        owner=owner,
        idempotency_key=idempotency_key,
        ttl_seconds=ttl_seconds,
        write_scopes=write_scopes,
        expected_version=expected_version,
        _legacy_provider_projection=True,
    )
    if payload.get("ok") is not True:
        error_payload = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "ok",
                "schema_version",
                "action",
                "error",
                "error_code",
                "settlement",
            }
        }
        raise TaskLeaseError(
            str(payload.get("error") or "native task lease acquire rejected"),
            code=str(payload.get("error_code") or "task_lease_acquire_rejected"),
            payload=error_payload,
        )
    return payload


def renew_task_lease(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    owner: str,
    idempotency_key: str,
    ttl_seconds: int | None = None,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """Renew a local lease through the native TypeScript lifecycle owner.

    This function is intentionally a compatibility facade: argument
    normalization and the historical Python import/return surface remain, but
    lifecycle authority, locking, CAS, and persistence live in the native
    handler.
    """

    normalized_goal_id = normalize_goal_id(goal_id)
    normalized_todo_id = normalize_lease_todo_id(todo_id)
    normalized_owner = normalize_owner(owner)
    normalized_key = normalize_idempotency_key(idempotency_key)
    normalized_ttl = normalize_ttl_seconds(ttl_seconds)
    # Keep version validation and handoff/error ordering in the native owner;
    # the adapter performs only the legacy numeric coercion at the transport
    # edge.  In particular, soft_claim must report its mode gate before a
    # missing expected version, matching the historical writer.
    normalized_expected_version = expected_version

    return _execute_native_task_lease_lifecycle(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=normalized_goal_id,
        todo_id=normalized_todo_id,
        operation="renew",
        owner=normalized_owner,
        idempotency_key=normalized_key,
        ttl_seconds=normalized_ttl,
        expected_version=normalized_expected_version,
    )


def transfer_task_lease(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    owner: str,
    idempotency_key: str,
    new_owner: str,
    new_idempotency_key: str,
    ttl_seconds: int | None = None,
    expected_version: int | None = None,
    transfer_claim: bool = False,
) -> dict[str, Any]:
    """Transfer a local lease, optionally with its canonical Todo claim."""

    if not isinstance(transfer_claim, bool):
        raise TaskLeaseError("transfer_claim must be a boolean", code="invalid_claim_transfer_request")

    normalized_goal_id = normalize_goal_id(goal_id)
    normalized_todo_id = normalize_lease_todo_id(todo_id)
    normalized_owner = normalize_owner(owner)
    normalized_key = normalize_idempotency_key(idempotency_key)
    normalized_new_owner = normalize_owner(new_owner)
    normalized_new_key = normalize_idempotency_key(new_idempotency_key)
    normalized_ttl = normalize_ttl_seconds(ttl_seconds)
    normalized_expected_version = expected_version

    return _execute_native_task_lease_lifecycle(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=normalized_goal_id,
        todo_id=normalized_todo_id,
        operation="transfer",
        owner=normalized_owner,
        idempotency_key=normalized_key,
        new_owner=normalized_new_owner,
        new_idempotency_key=normalized_new_key,
        **({"transfer_claim": True} if transfer_claim else {}),
        ttl_seconds=normalized_ttl,
        expected_version=normalized_expected_version,
    )


def release_task_lease(
    *,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    owner: str,
    idempotency_key: str,
    expected_version: int | None = None,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    """Release a local lease through the native TypeScript lifecycle owner."""

    normalized_goal_id = normalize_goal_id(goal_id)
    normalized_todo_id = normalize_lease_todo_id(todo_id)
    normalized_owner = normalize_owner(owner)
    normalized_key = normalize_idempotency_key(idempotency_key)
    normalized_expected_version = expected_version

    return _execute_native_task_lease_lifecycle(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=normalized_goal_id,
        todo_id=normalized_todo_id,
        operation="release",
        owner=normalized_owner,
        idempotency_key=normalized_key,
        expected_version=normalized_expected_version,
    )


def inspect_task_lease(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
) -> dict[str, Any]:
    from .task_lease_acquire_adapter import inspect_native_task_lease

    return inspect_native_task_lease(
        registry_path=registry_path, runtime_root=runtime_root,
        goal_id=normalize_goal_id(goal_id), todo_id=normalize_lease_todo_id(todo_id),
    )
