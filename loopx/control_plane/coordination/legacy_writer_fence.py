"""Cheap Python entry guard for the TypeScript-owned coordination fence.

Legacy Todo writes still happen in Python during Stage 2C.  The durable fence
itself and its validation semantics remain owned by the TypeScript control
plane; this module only avoids starting that runtime while no fence exists and
delegates every present-fence decision to the canonical handler.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
import hashlib
import re
from pathlib import Path
from typing import Any, Iterator

from ..effect_runtime import effect_runtime_result
from ...file_lock import exclusive_cross_runtime_file_lock
from ...history import load_registry
from ...paths import resolve_runtime_root
from ...registry import registry_goals, resolve_state_file
from ..goals.active_state_metadata import parse_state_frontmatter
from .shadow_management import (
    ShadowManagementError, read_shadow_bootstrap_source_path, read_shadow_management_state, require_shadow_primary_write_allowed,
)
from .coordination_state_contract_generated import (
    LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA,
    LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA,
)


LEGACY_COORDINATION_WRITE_CHECK_METHOD = (
    "coordination.local_authority.legacy_write_check"
)


# Caller adapter: remediation is rendered here, never inside the TypeScript
# write check, which owns only the stable typed reason and the fence binding
# facts.  Tokens are substituted in one pass, so a data value is never
# re-scanned for tokens.  Keep byte-identical with the TypeScript
# LEGACY_WRITER_FENCED_REMEDIATION.
LEGACY_WRITER_FENCED_REMEDIATION = (
    "legacy coordination writer is fenced; use the promoted canonical authority "
    "({authority_mode}) for goal {goal_id}; fence {fence_id}; "
    "the primary record was not changed"
)
_REMEDIATION_TOKENS = re.compile(r"\{(authority_mode|goal_id|fence_id)\}")


def _guard_text(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and value != "" else fallback


def legacy_coordination_write_remediation(goal_id: str, result: Mapping[str, Any]) -> str:
    """Operator-facing text for a non-allowed write check; ``reason_code`` stays the machine reason."""

    if result.get("status") != "blocked":
        return _guard_text(result.get("reason"), "legacy coordination writer fence check failed")
    values = {
        "authority_mode": _guard_text(result.get("authority_mode"), "unknown_fail_closed"),
        "goal_id": goal_id,
        "fence_id": _guard_text(result.get("fence_id"), "unknown"),
    }
    return _REMEDIATION_TOKENS.sub(lambda match: values[match.group(1)], LEGACY_WRITER_FENCED_REMEDIATION)


class LegacyCoordinationWriterFenced(RuntimeError):
    """Raised when a legacy writer is no longer an authority.

    ``payload`` carries the complete write-check result under ``write_check``
    so a CLI envelope can spread it without losing its own keys.
    """

    def __init__(self, message: str, *, code: str, payload: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.payload = payload


def legacy_coordination_writer_fence_path(
    *, runtime_root: Path, goal_id: str
) -> Path:
    digest = hashlib.sha256(goal_id.encode("utf-8")).hexdigest()[:16]
    return (
        runtime_root
        / "authority-transition"
        / "file-v0"
        / f"legacy-writer-fence-{digest}.json"
    )


def legacy_coordination_todo_lock_path(*, runtime_root: Path, goal_id: str) -> Path:
    digest = hashlib.sha256(goal_id.encode("utf-8")).hexdigest()[:16]
    return (
        runtime_root
        / "authority-transition"
        / "file-v0"
        / f"legacy-todo-writer-{digest}"
    )


def _require_other_goal_source_write_allowed(
    *, registry: dict[str, Any], runtime_roots: set[Path], goal_id: str,
    state_file: Path, canonical_mutation: bool,
) -> None:
    """A goal override cannot cancel the existing authority of this one source.

    The caller holds S. Reuse its frontmatter and current registry paths rather
    than persisting another source index or acquiring another goal's locks.
    Unbound legacy shared-state writes remain valid.
    """

    resolved_source = state_file.resolve(strict=False)
    try:
        owner = parse_state_frontmatter(state_file.read_text(encoding="utf-8")).get("goal_id")
    except FileNotFoundError:
        owner = None
    owners = {owner} if owner else set()
    for goal in registry_goals(registry):
        repo, path = goal.get("repo"), goal.get("state_file")
        if not isinstance(repo, str) or not isinstance(path, str):
            continue
        registered_source = resolve_state_file(Path(repo).expanduser(), path)
        if registered_source is not None and registered_source.resolve(strict=False) == resolved_source:
            owners.add(str(goal["id"]))
    for owner in sorted(owners - {goal_id}):
        for root in sorted(runtime_roots):
            binding = require_shadow_primary_write_allowed(root, owner)
            if not canonical_mutation:
                continue
            if binding is not None:
                bound_source = read_shadow_bootstrap_source_path(root, owner, binding)
                if bound_source.resolve(strict=False) == resolved_source:
                    raise ShadowManagementError(
                        "shadow_source_goal_mismatch",
                        "the state source has another goal's active capture binding; write through its goal",
                    )
            require_legacy_coordination_write_allowed(runtime_root=root, goal_id=owner)


def require_registry_source_write_allowed(
    *, registry_path: Path, runtime_root: Path, goal_id: str, state_file: Path,
    canonical_mutation: bool = True,
) -> None:
    """Check the registered source authority while its shared state lock is held.

    A runtime override changes runtime storage, not the identity of an existing
    state file. Re-read the registry and the atomic management record after S
    acquisition, without acquiring another root's M or T in reverse order.
    """

    binding = require_shadow_primary_write_allowed(runtime_root, goal_id)
    if canonical_mutation and binding is not None:
        bound_source = read_shadow_bootstrap_source_path(runtime_root, goal_id, binding)
        if bound_source.resolve(strict=False) != state_file.resolve(strict=False):
            raise ShadowManagementError(
                "shadow_source_state_path_mismatch",
                "the state file is not the source established by the active capture binding",
            )
    registry = load_registry(registry_path)
    registered_root = resolve_runtime_root(registry, None, registry_path=registry_path)
    _require_other_goal_source_write_allowed(
        registry=registry,
        runtime_roots={runtime_root.expanduser().resolve(strict=False), registered_root.expanduser().resolve(strict=False)},
        goal_id=goal_id, state_file=state_file, canonical_mutation=canonical_mutation,
    )
    if not any(isinstance(goal, dict) and goal.get("id") == goal_id for goal in registry.get("goals", [])):
        return
    if registered_root.expanduser().resolve(strict=False) == runtime_root.expanduser().resolve(strict=False):
        return
    binding = require_shadow_primary_write_allowed(registered_root, goal_id)
    if canonical_mutation:
        if binding is not None:
            raise ShadowManagementError(
                "shadow_source_runtime_root_mismatch",
                "the registered state source has an active capture binding; write through its runtime root",
            )
        require_legacy_coordination_write_allowed(runtime_root=registered_root, goal_id=goal_id)


def require_legacy_state_replacement_allowed(
    *, runtime_root: Path, goal_id: str, goal: dict[str, Any] | None,
) -> None:
    """A generic rebuild cannot retire or rebind an existing shadow lineage."""

    require_shadow_primary_write_allowed(runtime_root, goal_id)
    require_legacy_coordination_write_allowed(runtime_root=runtime_root, goal_id=goal_id)
    coordination = goal.get("coordination") if isinstance(goal, dict) else None
    config = coordination.get("runtime_shadow") if isinstance(coordination, dict) else None
    configured = config is not None and (
        not isinstance(config, dict) or config.get("enabled") is not False
    )
    digest = hashlib.sha256(goal_id.encode("utf-8")).hexdigest()[:16]
    candidates = (
        runtime_root / "authority-shadow" / "outbox" / goal_id,
        runtime_root / "authority-shadow" / "file-v0" / f"authority-store-{digest}.json",
    )
    state = read_shadow_management_state(runtime_root, goal_id)
    if configured or (state is not None and state["status"] == "active") or any(path.exists() for path in candidates):
        raise ShadowManagementError(
            "shadow_source_replacement_requires_rebootstrap",
            "active shadow source cannot be replaced or rebound by a generic state rebuild",
        )


def require_legacy_coordination_write_allowed(
    *, runtime_root: Path, goal_id: str
) -> None:
    """Allow the default path cheaply; delegate a present fence fail-closed.

    A future promotion transaction must acquire the legacy writer's existing
    mutation lock before engaging the fence.  Calling this function while that
    lock is held then closes the check/write race without introducing a second
    Python authority for fence contents.
    """

    fence_path = legacy_coordination_writer_fence_path(
        runtime_root=runtime_root,
        goal_id=goal_id,
    )
    try:
        fence_path.stat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise LegacyCoordinationWriterFenced(
            "legacy coordination writer fence cannot be inspected",
            code="legacy_writer_fence_read_failed",
            payload={
                "write_check": {
                    "schema_version": LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA,
                    "status": "failed",
                    "reason_code": "legacy_writer_fence_read_failed",
                    "reason": "legacy coordination writer fence cannot be inspected",
                    "authority_mode": "unknown_fail_closed",
                }
            },
        ) from exc

    result = effect_runtime_result(
        LEGACY_COORDINATION_WRITE_CHECK_METHOD,
        {
            "schema_version": LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA,
            "runtime_root": str(runtime_root.expanduser().resolve(strict=False)),
            "goal_id": goal_id,
        },
    )
    if not isinstance(result, dict):
        raise LegacyCoordinationWriterFenced(
            "legacy coordination writer fence returned an invalid result",
            code="legacy_writer_fence_invalid_result",
            payload={
                "write_check": {
                    "schema_version": LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA,
                    "status": "failed",
                    "reason_code": "legacy_writer_fence_invalid_result",
                    "reason": "legacy coordination writer fence returned an invalid result",
                    "authority_mode": "unknown_fail_closed",
                }
            },
        )
    if (
        result.get("status") == "allowed"
        and result.get("authority_mode") == "legacy_canonical"
    ):
        return

    code = str(result.get("reason_code") or "legacy_writer_fence_check_failed")
    raise LegacyCoordinationWriterFenced(
        legacy_coordination_write_remediation(goal_id, result),
        code=code,
        payload={"write_check": result},
    )


@contextmanager
def legacy_todo_write_transaction(
    registry_path: Path,
    goal_id: str,
    state_file: Path,
    agent_id: str | None,
    operation: str,
    dry_run: bool,
    *,
    runtime_root: Path | None = None,
) -> Iterator[None]:
    """Serialize promotion with one complete legacy Todo mutation.

    ``runtime_root`` is the effective runtime root of the CLI call (the
    ``--runtime-root`` override when given) so the mutex and the fence check
    share the same root as promotion and the observation hooks.  Callers that
    omit it keep the registry-derived root.
    """

    resolved_runtime_root = runtime_root or resolve_runtime_root(
        load_registry(registry_path),
        None,
        registry_path=registry_path,
    )
    with exclusive_cross_runtime_file_lock(
        legacy_coordination_todo_lock_path(
            runtime_root=resolved_runtime_root,
            goal_id=goal_id,
        ),
        agent_id=agent_id,
        operation="legacy_coordination_todo_write",
    ), exclusive_cross_runtime_file_lock(
        state_file,
        agent_id=agent_id,
        operation=operation,
    ):
        if not dry_run:
            require_registry_source_write_allowed(
                registry_path=registry_path, runtime_root=resolved_runtime_root, goal_id=goal_id,
                state_file=state_file,
            )
            require_shadow_primary_write_allowed(resolved_runtime_root, goal_id)
            require_legacy_coordination_write_allowed(
                runtime_root=resolved_runtime_root,
                goal_id=goal_id,
            )
        yield
