from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...configuration_transaction import configuration_payload_revision
from ...file_lock import exclusive_cross_runtime_file_lock
from ...global_registry import sync_project_registry_to_global
from ...history import load_registry
from ...paths import (
    GLOBAL_REGISTRY_FILENAME,
    global_registry_path,
    goal_state_file_from_record,
    resolve_runtime_root,
    select_default_runtime_root,
)
from ...registry import registry_goals
from ...registry_writability import probe_registry_write_path
from ..actor_identity import OwnerControllerActorKind, normalize_owner_controller_actor
from ..content_digest import BARE_SHA256_PATTERN
from ..effect_runtime import effect_runtime_result
from ..projects.registry_codec import (
    activation_upgrade_registry_transaction,
    project_registry_transaction,
    read_activation_upgrade_registry,
)
from ..runtime.time import now_local_iso
from .activation import (
    GoalActivationState,
    build_goal_activation,
    goal_activation_state,
    goal_owner_resume_id,
    normalize_goal_activation_state,
)
from .configure_goal_service import resolve_configure_goal_sync_target

GOAL_ACTIVATION_TRANSITION_SCHEMA_VERSION = "loopx_goal_activation_transition_v1"
GOAL_ACTIVATION_READBACK_SCHEMA_VERSION = "loopx_goal_activation_readback_v1"
GOAL_ACTIVATION_SOURCE_FINGERPRINT_SCHEMA_VERSION = (
    "loopx_goal_activation_source_fingerprint_v1"
)
GOAL_ACTIVATION_AUTHORITY_ROUTE_SCHEMA_VERSION = (
    "loopx_goal_activation_authority_route_v1"
)


class GoalActivationAuthorityRouteMode(str, Enum):
    SOURCE_TO_GLOBAL = "source_to_global"
    REQUESTED_TO_GLOBAL = "requested_to_global"
    ORPHANED_GLOBAL_STOP_FALLBACK = "orphaned_global_stop_fallback"


class GoalActivationSourceStatus(str, Enum):
    AVAILABLE = "available"
    REGISTRY_MISSING = "registry_missing"
    REGISTRY_UNREADABLE = "registry_unreadable"
    GOAL_MISSING = "goal_missing"


@dataclass(frozen=True, slots=True)
class GoalActivationAuthorityRoute:
    source_registry: Path
    target_registry: Path
    sync_runtime_root: str | None
    mode: GoalActivationAuthorityRouteMode
    source_status: GoalActivationSourceStatus

    def public_summary(self) -> dict[str, Any]:
        orphaned = (
            self.mode is GoalActivationAuthorityRouteMode.ORPHANED_GLOBAL_STOP_FALLBACK
        )
        return {
            "schema_version": GOAL_ACTIVATION_AUTHORITY_ROUTE_SCHEMA_VERSION,
            "mode": self.mode.value,
            "source_status": self.source_status.value,
            "resume_requires_source_repair": orphaned,
        }


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except OSError:
        return str(left.expanduser()) == str(right.expanduser())


def _goal_activation_source_identity(source_registry: Path) -> str:
    return configuration_payload_revision(
        {"source_registry": str(source_registry.expanduser().resolve())}
    ).removeprefix("sha256:")


def _projected_source_identity(
    target_registry: Path,
    *,
    goal_id: str,
) -> str | None:
    target_goal = _goal_or_none(load_registry(target_registry), goal_id)
    if target_goal is None:
        return None
    source_ref = str(target_goal.get("source_registry") or "").strip()
    if not source_ref:
        return None
    return _goal_activation_source_identity(Path(source_ref))


def goal_activation_source_fingerprint(
    *,
    goal_id: str,
    source_registry: Path,
    source_bytes: bytes,
) -> str:
    identity = {
        "schema_version": GOAL_ACTIVATION_SOURCE_FINGERPRINT_SCHEMA_VERSION,
        "goal_id": goal_id,
        "source_identity": _goal_activation_source_identity(source_registry),
        "source_content_sha256": hashlib.sha256(source_bytes).hexdigest(),
    }
    return configuration_payload_revision(identity).removeprefix("sha256:")


def _goal(payload: dict[str, Any], goal_id: str) -> dict[str, Any]:
    goal = next(
        (
            item
            for item in registry_goals(payload)
            if str(item.get("id") or "") == goal_id
        ),
        None,
    )
    if goal is None:
        raise ValueError(f"goal id not found in registry: {goal_id}")
    return goal


def _goal_or_none(
    payload: dict[str, Any],
    goal_id: str,
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in registry_goals(payload)
            if str(item.get("id") or "") == goal_id
        ),
        None,
    )


def _is_global_registry(path: Path, payload: dict[str, Any]) -> bool:
    role = str(payload.get("registry_role") or payload.get("role") or "")
    return role == "global-local" or path.name == "registry.global.json"


def _source_status(
    source_registry: Path,
    *,
    goal_id: str,
) -> GoalActivationSourceStatus:
    if not source_registry.exists():
        return GoalActivationSourceStatus.REGISTRY_MISSING
    try:
        source_payload = load_registry(source_registry)
    except (OSError, UnicodeError, ValueError):
        return GoalActivationSourceStatus.REGISTRY_UNREADABLE
    if _goal_or_none(source_payload, goal_id) is None:
        return GoalActivationSourceStatus.GOAL_MISSING
    return GoalActivationSourceStatus.AVAILABLE


def _source_and_target(
    *,
    registry_path: Path,
    goal_id: str,
    target_state: GoalActivationState,
    runtime_root_override: str | None,
) -> GoalActivationAuthorityRoute:
    requested_registry = registry_path.expanduser().resolve()
    requested_payload = load_registry(requested_registry)
    requested_goal = _goal(requested_payload, goal_id)
    source_ref = str(requested_goal.get("source_registry") or "").strip()
    if source_ref:
        source_registry = Path(source_ref).expanduser().resolve()
        source_status = _source_status(source_registry, goal_id=goal_id)
        if source_status is GoalActivationSourceStatus.AVAILABLE:
            return GoalActivationAuthorityRoute(
                source_registry=source_registry,
                target_registry=requested_registry,
                sync_runtime_root=str(requested_registry.parent),
                mode=GoalActivationAuthorityRouteMode.SOURCE_TO_GLOBAL,
                source_status=source_status,
            )
        if target_state is GoalActivationState.STOPPED and _is_global_registry(
            requested_registry, requested_payload
        ):
            return GoalActivationAuthorityRoute(
                source_registry=requested_registry,
                target_registry=requested_registry,
                sync_runtime_root=None,
                mode=GoalActivationAuthorityRouteMode.ORPHANED_GLOBAL_STOP_FALLBACK,
                source_status=source_status,
            )
        if target_state is GoalActivationState.ACTIVE:
            raise ValueError(
                "Goal source registry is unavailable; repair the source route "
                "before resuming this Goal"
            )
        raise ValueError(
            "Goal source registry is unavailable; repair the source route "
            "before changing this Goal"
        )

    resolution = resolve_configure_goal_sync_target(
        registry_path=requested_registry,
        goal_id=goal_id,
        runtime_root_override=runtime_root_override,
    )
    target_registry = (
        Path(str(resolution["target_global_registry"])).expanduser().resolve()
    )
    return GoalActivationAuthorityRoute(
        source_registry=requested_registry,
        target_registry=target_registry,
        sync_runtime_root=str(resolution["target_runtime_root"]),
        mode=GoalActivationAuthorityRouteMode.REQUESTED_TO_GLOBAL,
        source_status=GoalActivationSourceStatus.AVAILABLE,
    )


def _project_committed_transition(
    *,
    source_registry: Path,
    runtime_root_override: str | None,
    goal_id: str,
) -> dict[str, Any]:
    """Project a transition the source registry already committed.

    The source registry owns the lifecycle transition; the shared registry is
    a repairable projection of it. A projection writer that raises is the same
    fixable fault as a failure receipt, so report it here instead of letting it
    escape and erase the committed transition receipt.
    """

    try:
        return sync_project_registry_to_global(
            registry_path=source_registry,
            runtime_root_override=runtime_root_override,
            goal_id=goal_id,
            dry_run=False,
            _global_registry_lock_held=True,
        )
    except Exception as exc:  # the committed source transition keeps its receipt
        return {
            "ok": False,
            "dry_run": False,
            "error_kind": "global_sync_failed",
            "error": f"shared registry projection failed: {exc}",
            "registry": str(source_registry),
            "recommended_action": (
                f"rerun loopx sync-global --goal-id {goal_id} "
                "after repairing the shared registry route"
            ),
        }


def _readback_committed_transition(
    *,
    source_registry: Path,
    target_registry: Path,
    goal_id: str,
    expected_state: GoalActivationState,
    expected_owner_resume_id: str | None,
) -> dict[str, Any]:
    """Read back a committed transition without discarding its receipt.

    A read fault after the source commit is reported as an unverified readback
    so the caller still sees ``written`` and the terminal hook still runs.
    """

    try:
        return _readback(
            source_registry=source_registry,
            target_registry=target_registry,
            goal_id=goal_id,
            expected_state=expected_state,
            expected_owner_resume_id=expected_owner_resume_id,
        )
    except Exception as exc:  # the committed source transition keeps its receipt
        return {
            "schema_version": GOAL_ACTIVATION_READBACK_SCHEMA_VERSION,
            "status": "readback_failed",
            "verified": False,
            "goal_id": goal_id,
            "expected_state": expected_state.value,
            "error": f"activation readback failed: {exc}",
        }

def _readback(
    *,
    source_registry: Path,
    target_registry: Path,
    goal_id: str,
    expected_state: GoalActivationState,
    expected_owner_resume_id: str | None,
) -> dict[str, Any]:
    source_goal = _goal(load_registry(source_registry), goal_id)
    target_goal = _goal(load_registry(target_registry), goal_id)
    source_state = goal_activation_state(source_goal)
    target_state = goal_activation_state(target_goal)
    verified = (
        source_state is expected_state
        and target_state is expected_state
        and goal_owner_resume_id(source_goal) == expected_owner_resume_id
        and goal_owner_resume_id(target_goal) == expected_owner_resume_id
    )
    return {
        "schema_version": GOAL_ACTIVATION_READBACK_SCHEMA_VERSION,
        "status": "verified" if verified else "mismatch",
        "verified": verified,
        "goal_id": goal_id,
        "expected_state": expected_state.value,
        "source_state": source_state.value,
        "target_state": target_state.value,
    }


def set_goal_activation_state(
    *,
    registry_path: Path,
    goal_id: str,
    state: GoalActivationState | str,
    reason: str | None = None,
    runtime_root_override: str | None = None,
    expected_state_fingerprint: str | None = None,
    actor_kind: str | None = None,
    owner_resume_action: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    """Preview or apply one reversible Goal activation transition."""

    normalized_goal_id = str(goal_id or "").strip()
    if not normalized_goal_id:
        raise ValueError("goal id is required")
    actor = normalize_owner_controller_actor(actor_kind, required=execute)
    target_state = normalize_goal_activation_state(state)
    if owner_resume_action and not (
        target_state is GoalActivationState.ACTIVE
        and actor is OwnerControllerActorKind.OWNER
    ):
        raise ValueError(
            "owner resume action requires active state and the owner actor"
        )
    authority_route = _source_and_target(
        registry_path=registry_path,
        goal_id=normalized_goal_id,
        target_state=target_state,
        runtime_root_override=runtime_root_override,
    )
    source_registry = authority_route.source_registry
    target_registry = authority_route.target_registry
    sync_runtime_root = authority_route.sync_runtime_root
    registries_are_distinct = not _same_path(source_registry, target_registry)
    source_identity = _goal_activation_source_identity(source_registry)
    target_source_identity = (
        _projected_source_identity(
            target_registry,
            goal_id=normalized_goal_id,
        )
        if registries_are_distinct
        else source_identity
    )
    source_bytes = source_registry.read_bytes()
    source_goal = _goal(load_registry(source_registry), normalized_goal_id)
    normalized_fingerprint = str(expected_state_fingerprint or "").strip() or None
    if normalized_fingerprint is not None and not BARE_SHA256_PATTERN.fullmatch(
        normalized_fingerprint
    ):
        raise ValueError("expected state fingerprint must be a SHA-256 digest")
    observed_fingerprint = goal_activation_source_fingerprint(
        goal_id=normalized_goal_id,
        source_registry=source_registry,
        source_bytes=source_bytes,
    )
    before_state = goal_activation_state(source_goal)
    changed = before_state is not target_state
    write_required = changed or owner_resume_action
    actor_label = actor.value if actor is not None else "owner"
    default_reason = (
        f"Stopped by {actor_label}"
        if target_state is GoalActivationState.STOPPED
        else f"Resumed by {actor_label}"
    )
    reason_text = " ".join(str(reason or default_reason).split()).strip()
    proposed_activation = build_goal_activation(
        state=target_state,
        updated_at=now_local_iso(),
        reason=reason_text,
        actor_kind=actor.value if actor is not None else None,
    )
    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": GOAL_ACTIVATION_TRANSITION_SCHEMA_VERSION,
        "dry_run": not execute,
        "execute": execute,
        "goal_id": normalized_goal_id,
        "before_state": before_state.value,
        "after_state": target_state.value,
        "changed": changed,
        "owner_resume_action": owner_resume_action,
        "owner_resume_advanced": False,
        "written": False,
        "partial_write": False,
        "source_registry": str(source_registry),
        "source_identity": source_identity,
        "source_fingerprint_schema_version": (
            GOAL_ACTIVATION_SOURCE_FINGERPRINT_SCHEMA_VERSION
        ),
        "target_global_registry": str(target_registry),
        "authority_route": authority_route.public_summary(),
        "expected_state_fingerprint": normalized_fingerprint,
        "observed_state_fingerprint": observed_fingerprint,
        "activation": proposed_activation,
        "actor_kind": actor.value if actor is not None else None,
        "readback": {
            "schema_version": GOAL_ACTIVATION_READBACK_SCHEMA_VERSION,
            "status": "not_executed" if write_required else "not_required",
            "verified": not write_required,
        },
    }
    if (
        normalized_fingerprint is not None
        and observed_fingerprint != normalized_fingerprint
    ):
        payload.update(
            {
                "ok": False,
                "error_kind": "goal_action_stale",
                "error": "Goal state changed after action projection; refresh actions and retry",
            }
        )
        return payload
    if not execute:
        return payload

    if (
        authority_route.mode
        is GoalActivationAuthorityRouteMode.ORPHANED_GLOBAL_STOP_FALLBACK
    ):
        payload["recommended_action"] = (
            "Repair the Goal source registry route before resuming this Goal."
        )

    if registries_are_distinct:
        writability = probe_registry_write_path(target_registry, create_parent=True)
        payload["global_registry_writability"] = writability
        if not writability.get("ok"):
            payload.update(
                {
                    "ok": False,
                    "error_kind": "global_registry_write_denied",
                    "error": str(
                        writability.get("error")
                        or "authoritative shared registry is not writable"
                    ),
                    "recommended_action": writability.get("recommended_action"),
                }
            )
            return payload

    sync_payload: dict[str, Any] | None = None
    with project_registry_transaction(
        source_registry,
        operation="set_goal_activation_state",
    ) as transaction:
        target_lock = (
            exclusive_cross_runtime_file_lock(
                target_registry,
                operation="set_goal_activation_state",
            )
            if registries_are_distinct
            else nullcontext()
        )
        with target_lock:
            if registries_are_distinct:
                locked_target_source_identity = _projected_source_identity(
                    target_registry,
                    goal_id=normalized_goal_id,
                )
                route_changed = locked_target_source_identity != target_source_identity
                route_conflicts = locked_target_source_identity not in {
                    None,
                    source_identity,
                }
                if route_changed or route_conflicts:
                    route_fingerprint = configuration_payload_revision(
                        {
                            "source_fingerprint": observed_fingerprint,
                            "target_source_identity": locked_target_source_identity,
                        }
                    ).removeprefix("sha256:")
                    payload.update(
                        {
                            "ok": False,
                            "error_kind": "goal_action_stale",
                            "error": (
                                "Goal source route changed after action projection; "
                                "refresh actions and retry"
                            ),
                            "observed_state_fingerprint": route_fingerprint,
                        }
                    )
                    return payload

            source_payload = transaction.payload_copy()
            locked_fingerprint = goal_activation_source_fingerprint(
                goal_id=normalized_goal_id,
                source_registry=source_registry,
                source_bytes=source_registry.read_bytes(),
            )
            if (
                normalized_fingerprint is not None
                and locked_fingerprint != normalized_fingerprint
            ):
                payload.update(
                    {
                        "ok": False,
                        "error_kind": "goal_action_stale",
                        "error": (
                            "Goal state changed after action projection; "
                            "refresh actions and retry"
                        ),
                        "observed_state_fingerprint": locked_fingerprint,
                    }
                )
                return payload
            locked_goal = _goal(source_payload, normalized_goal_id)
            closeout_goal = dict(locked_goal)
            locked_state = goal_activation_state(locked_goal)
            if locked_state is not before_state:
                payload.update(
                    {
                        "ok": False,
                        "error_kind": "goal_activation_state_changed",
                        "error": (
                            "goal activation changed after preview; "
                            "regenerate the transition"
                        ),
                        "observed_state": locked_state.value,
                    }
                )
                return payload
            owner_resume_id = goal_owner_resume_id(locked_goal)
            if write_required:
                # Preserve the owner resume across later lifecycle writes.
                # Mint it inside the source lock for an actual owner resume,
                # including an explicit host resume while the LoopX Goal is
                # already active. Ordinary guard entry never sets this flag.
                if (
                    target_state is GoalActivationState.ACTIVE
                    and actor is OwnerControllerActorKind.OWNER
                ):
                    owner_resume_id = uuid4().hex
                    payload["owner_resume_advanced"] = True
                if owner_resume_id is not None:
                    proposed_activation["owner_resume_id"] = owner_resume_id
                locked_goal["activation"] = proposed_activation
                transaction.commit(source_payload)
                payload["written"] = True

            if registries_are_distinct:
                sync_payload = _project_committed_transition(
                    source_registry=source_registry,
                    runtime_root_override=sync_runtime_root,
                    goal_id=normalized_goal_id,
                )
                payload["global_sync"] = sync_payload

            readback = (
                {
                    "schema_version": GOAL_ACTIVATION_READBACK_SCHEMA_VERSION,
                    "status": "not_run",
                    "verified": False,
                }
                if sync_payload is not None and not sync_payload.get("ok")
                else _readback_committed_transition(
                    source_registry=source_registry,
                    target_registry=target_registry,
                    goal_id=normalized_goal_id,
                    expected_state=target_state,
                    expected_owner_resume_id=owner_resume_id,
                )
            )
    payload["readback"] = readback
    sync_ok = sync_payload is None or bool(sync_payload.get("ok"))
    payload["ok"] = bool(sync_ok and readback.get("verified"))
    payload["partial_write"] = bool(payload["written"] and not payload["ok"])
    payload["projection_reconciled"] = bool(
        not write_required
        and registries_are_distinct
        and readback.get("verified")
    )
    if not payload["ok"]:
        payload["error"] = str(
            readback.get("error")
            or (sync_payload or {}).get("error")
            or "goal activation shared registry readback did not verify"
        )
        payload["recommended_action"] = (
            (sync_payload or {}).get("recommended_action")
            or f"rerun loopx sync-global --goal-id {normalized_goal_id} after repairing the shared registry route"
        )
    # The stop itself is committed in the source registry, which owns the
    # transition. A failed shared projection is a separately reported, fixable
    # fault, and a retry cannot re-enter this branch because the source is
    # already stopped; the hook therefore runs once for the committed
    # transition even when the projection has to be repaired afterwards.
    if (
        execute
        and target_state is GoalActivationState.STOPPED
        and changed
        and payload.get("written")
        and closeout_goal is not None
    ):
        payload["closeout"] = _stop_closeout_payload(
            source_registry=source_registry,
            registry_snapshot=source_payload,
            runtime_root_override=runtime_root_override,
            goal_id=normalized_goal_id,
            closeout_goal=closeout_goal,
        )
    return payload


def _stop_closeout_payload(
    *,
    source_registry: Path,
    registry_snapshot: Mapping[str, Any],
    runtime_root_override: str | None,
    goal_id: str,
    closeout_goal: dict[str, Any],
) -> dict[str, Any]:
    """Run the terminal hook after a committed stop transition.

    The stop is already committed when this runs, so the whole boundary is
    best-effort: argument preparation reads the confirmed registry snapshot
    taken inside the transaction, and any preparation failure is reported in
    the hook result instead of raising past the committed receipt.
    """

    from ...lifecycle.goal_closeout import (
        GOAL_CLOSEOUT_LEDGER_SCHEMA_VERSION,
        goal_closeout_configuration,
        run_goal_closeout,
    )

    try:
        closeout_runtime_root = resolve_runtime_root(
            dict(registry_snapshot),
            runtime_root_override,
            registry_path=source_registry,
        )
        # No configured command means no hook-only preparation: resolving a
        # historical state path (say, an expired user alias) must not turn an
        # otherwise clean stop into a preparation error. Retire and archive
        # apply the same short-circuit.
        configuration, configuration_error = goal_closeout_configuration(
            closeout_runtime_root
        )
        if configuration_error is None and not (configuration or {}).get("command"):
            return {
                "schema_version": GOAL_CLOSEOUT_LEDGER_SCHEMA_VERSION,
                "goal_id": goal_id,
                "trigger": "goal-lifecycle-stop",
                "terminal_reason": "stopped",
                "status": "not_configured",
                "ledger_recorded": False,
            }
        declared = goal_state_file_from_record(
            closeout_goal,
            registry_path=source_registry,
        )
        if declared is None:
            declared = (
                source_registry.parent
                / "goals"
                / goal_id
                / "ACTIVE_GOAL_STATE.md"
            )
        state_file = declared
        return run_goal_closeout(
            runtime_root=closeout_runtime_root,
            goal_id=goal_id,
            repo=str(closeout_goal.get("repo")) if closeout_goal.get("repo") else None,
            state_file=str(state_file),
            terminal_reason="stopped",
            trigger="goal-lifecycle-stop",
        )
    except Exception as exc:  # the committed stop must keep its own receipt
        return {
            "schema_version": "loopx_goal_closeout_invocation_v0",
            "goal_id": goal_id,
            "trigger": "goal-lifecycle-stop",
            "terminal_reason": "stopped",
            "status": "preparation_error",
            "error": f"{type(exc).__name__}: {exc}",
            "ledger_recorded": False,
        }


def render_goal_activation_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Goal Activation",
        "",
        f"- ok: `{str(payload.get('ok')).lower()}`",
        f"- goal: `{payload.get('goal_id')}`",
        f"- transition: `{payload.get('before_state')}` → `{payload.get('after_state')}`",
        f"- dry_run: `{str(payload.get('dry_run')).lower()}`",
        f"- changed: `{str(payload.get('changed')).lower()}`",
        f"- written: `{str(payload.get('written')).lower()}`",
    ]
    readback = payload.get("readback")
    if isinstance(readback, dict):
        lines.append(f"- readback: `{readback.get('status')}`")
    if payload.get("error"):
        lines.extend(["", f"Error: {payload.get('error')}"])
    return "\n".join(lines).rstrip() + "\n"


def authority_upgrade_roots(registry_path: Path, runtime_root_arg: str | None,
                            *, all_known: bool) -> list[str]:
    """Bounded discovery from existing registries; never scan arbitrary home paths."""
    registry = read_activation_upgrade_registry(registry_path)
    override = runtime_root_arg or os.environ.get("LOOPX_RUNTIME_ROOT")
    selected = resolve_runtime_root(registry, override, registry_path=registry_path).resolve()
    roots = {selected}
    if all_known:
        roots.add(resolve_runtime_root(registry, registry_path=registry_path).resolve())
        common = Path(override).expanduser().resolve() if override else select_default_runtime_root().resolve()
        roots.add(common)
        global_path = global_registry_path(common)
        global_registry = read_activation_upgrade_registry(global_path)
        for goal in global_registry.get("goals", []):
            source = goal.get("source_registry") if isinstance(goal, dict) else None
            if not source:
                continue
            path = Path(str(source)).expanduser()
            if not path.is_absolute():
                raise ValueError("Registered source_registry must be absolute for automatic upgrade")
            # A disconnected checkout does not hide the common runtime store.
            if path.exists():
                roots.add(resolve_runtime_root(read_activation_upgrade_registry(path), registry_path=path).resolve())
    return sorted(str(root) for root in roots)


def authority_upgrade_registries(registry_path: Path, runtime_root_arg: str | None,
                                *, all_known: bool) -> list[Path]:
    """Use the same registered routes as store discovery, including the selection."""
    roots = authority_upgrade_roots(registry_path, runtime_root_arg, all_known=all_known)
    paths = {registry_path.expanduser().resolve()}
    for root in roots:
        path = global_registry_path(Path(root))
        paths.add(path)
        for goal in read_activation_upgrade_registry(path).get("goals", []):
            source = goal.get("source_registry") if isinstance(goal, dict) else None
            if source:
                source_path = Path(str(source)).expanduser()
                if not source_path.is_absolute():
                    raise ValueError("Registered source_registry must be absolute for automatic upgrade")
                paths.add(source_path.resolve())
    return sorted(path for path in paths if path.exists())


# Upgrade-only IO reuses the lifecycle service and codec transactions.

def upgrade_goal_activation_registries(
    paths: list[Path], *, execute: bool,
) -> dict[str, Any]:
    initial_snapshots = {path.resolve(): read_activation_upgrade_registry(path)
                         for path in paths if path.exists()}
    paths = sorted(initial_snapshots, key=lambda path: (
        path.name == GLOBAL_REGISTRY_FILENAME
        or (initial_snapshots[path].get("registry_role") or initial_snapshots[path].get("role")) == "global-local",
        str(path),
    ))
    with ExitStack() as locks:
        transactions = {}
        payloads = {}
        snapshots = []
        for path in paths:
            initial = initial_snapshots[path]
            if execute:
                transaction = locks.enter_context(activation_upgrade_registry_transaction(path))
                transactions[str(path)] = transaction
                initial = transaction.payload_copy()
            payloads[str(path)] = initial
            # Send only migration facts, never complete Goal configuration or
            # business state. Normalized source paths are transport identity.
            goals = []
            for goal in initial.get("goals", []):
                if not isinstance(goal, dict):
                    raise ValueError("Registry Goal must be an object")
                facts = {key: goal[key] for key in (
                    "id", "goal_instance_id", "status", "activation_state",
                ) if key in goal}
                if "activation" in goal:
                    activation = goal["activation"]
                    facts["activation"] = ({key: activation[key] for key in ("schema_version", "state")
                                             if key in activation}
                                            if isinstance(activation, dict) else activation)
                if goal.get("source_registry"):
                    source = Path(goal["source_registry"]).expanduser().resolve()
                    if source.exists() and source not in initial_snapshots:
                        raise ValueError("Registered source route changed during activation upgrade; rediscover and retry")
                    facts["source_registry"] = str(source)
                goals.append(facts)
            snapshots.append({"path": str(path), "payload": {"goals": goals,
                              "profile_id": initial.get("profile_id")}})
        plan = effect_runtime_result("goal.activation.migrate", {"registries": snapshots})
        rows = plan["registries"]
        if {row["path"] for row in rows} != set(payloads) or len(rows) != len(payloads):
            raise ValueError("Goal activation migration returned different registry routes")
        results = []
        # Verify every required backup before committing any registry. A later
        # commit failure fails the upgrade; completed stopped metadata is safe
        # to retain, and a retry converges without replaying business effects.
        for row in rows:
            changed = bool(row["changes"])
            payload = payloads[row["path"]]
            for change in row["changes"]:
                goal = payload["goals"][change["index"]]
                if goal.get("id") != change["goal_id"]:
                    raise ValueError("Goal activation upgrade changed the Goal identity")
                prior_activation = goal.get("activation")
                goal["activation"] = {**(prior_activation if isinstance(prior_activation, dict) else {}),
                                      **change["activation"]}
                goal.pop("activation_state", None)
                if "status" in change:
                    goal["status"] = change["status"]
            result = {"registry": row["path"], "changed_goal_ids": [change["goal_id"] for change in row["changes"]],
                      "status": "planned" if changed else "current"}
            if changed and execute:
                result["backup"] = str(transactions[row["path"]].backup_preimage())
            results.append(result)
        if execute:
            for row, result in zip(rows, results, strict=True):
                if result["status"] == "planned":
                    transactions[row["path"]].commit(payloads[row["path"]])
                    result["status"] = "upgraded"
        return {"status": "upgraded" if execute else "planned", "results": results}
