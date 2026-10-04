from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from ..control_plane.capability_hooks import (
    POST_WRITEBACK_HOOK_DISPATCH_SCHEMA_VERSION,
    PostWritebackHookRegistration,
    PostWritebackSourceRejected,
    dispatch_post_writeback_hooks,
)
from ..control_plane.post_writeback_composition_retry import (
    append_composition_retry_receipt,
    build_composition_retry_receipt,
    composition_retry_receipt_id,
    composition_retry_receipt_log_path,
    composition_retry_receipt_ref,
    settle_composition_retry_receipt,
)
from ..history import load_registry
from ..paths import resolve_runtime_root


PostWritebackProjectionBuilder = Callable[..., Mapping[str, object]]

def _composition_hook_identities(
    hooks: Sequence[PostWritebackHookRegistration],
) -> list[dict[str, str]]:
    return [
        {
            "hook_id": str(registration.hook_id or ""),
            "capability_id": str(registration.capability_id or ""),
            "policy_version": str(
                getattr(registration, "policy_version", "") or ""
            ),
        }
        for registration in hooks
    ]


def _composition_failure_dispatch(
    hooks: Sequence[PostWritebackHookRegistration],
    *,
    error_code: str,
    receipt_ref: str | None,
) -> dict[str, Any]:
    """Report the concrete public-safe hook identities behind one composition failure."""

    failures: list[dict[str, str]] = []
    for registration in hooks:
        failure = {
            "hook_id": str(registration.hook_id or ""),
            "capability_id": str(registration.capability_id or ""),
            "error_code": error_code,
        }
        if receipt_ref is not None:
            failure["durable_receipt_ref"] = receipt_ref
        failures.append(failure)
    return {
        "schema_version": POST_WRITEBACK_HOOK_DISPATCH_SCHEMA_VERSION,
        "phase": "post_writeback",
        "registered_count": len(hooks),
        "invoked_count": 0,
        "replayed_hooks": [],
        "retried_hooks": [],
        "intent_count": 0,
        "intents": [],
        "failures": failures,
        "primary_writeback_preserved": True,
        "external_writes_performed": False,
    }


def _recorded_composition_failure(
    journal_path: Path,
    *,
    hooks: Sequence[PostWritebackHookRegistration],
    goal_id: str,
    event_kind: str,
    identity: Mapping[str, Any],
    state_version: str,
    committed_at: str,
    error_code: str,
) -> dict[str, Any]:
    """Persist one retryable receipt, degrading to identity-only on journal errors."""

    receipt = build_composition_retry_receipt(
        goal_id=goal_id,
        event_kind=event_kind,
        identity=identity,
        state_version=state_version,
        committed_at=committed_at,
        hook_identities=_composition_hook_identities(hooks),
        error_code=error_code,
    )
    receipt_ref: str | None = composition_retry_receipt_ref(
        str(receipt["receipt_id"])
    )
    try:
        append_composition_retry_receipt(journal_path, receipt)
    except (OSError, ValueError):
        receipt_ref = None
    return _composition_failure_dispatch(
        hooks, error_code=error_code, receipt_ref=receipt_ref
    )


def _settle_composition_retry_quietly(
    journal_path: Path,
    *,
    goal_id: str,
    event_kind: str,
    identity: Mapping[str, Any],
    state_version: str,
    committed_at: str,
    hooks: Sequence[PostWritebackHookRegistration],
) -> None:
    try:
        settle_composition_retry_receipt(
            journal_path,
            goal_id=goal_id,
            event_kind=event_kind,
            identity=identity,
            state_version=state_version,
            committed_at=committed_at,
            hook_identities=_composition_hook_identities(hooks),
        )
    except (OSError, ValueError):
        return


def dispatch_committed_cli_post_writeback_hooks(
    *,
    payload: Mapping[str, Any],
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    event_kind: str,
    identity: Mapping[str, Any],
    state_version: str,
    committed_at: str,
    hooks: Sequence[PostWritebackHookRegistration],
    projection_builder: PostWritebackProjectionBuilder | None,
    receipt_id: str | None = None,
) -> dict[str, Any]:
    """Bridge one committed CLI mutation into the TS-owned hook lifecycle.

    Projection and provider failures are isolated from the primary mutation:
    the primary writeback is never rolled back or repeated, a failed
    composition records one retryable receipt bound to that writeback, and a
    later replay of the same writeback settles the receipt once the
    projection composes cleanly. The helper intentionally owns no capability
    policy and grants no effects.
    """

    try:
        runtime_root = resolve_runtime_root(
            load_registry(registry_path), runtime_root_arg
        )
        journal_path = composition_retry_receipt_log_path(runtime_root, goal_id)
        composition_retry_receipt_id(
            goal_id=goal_id,
            event_kind=event_kind,
            identity=identity,
            state_version=state_version,
            hook_identities=_composition_hook_identities(hooks),
        )
    except Exception:  # Optional hooks never alter primary truth.
        return _composition_failure_dispatch(
            hooks, error_code="source_projection_failed", receipt_ref=None
        )
    try:
        projection = (
            dict(
                projection_builder(
                    payload=payload,
                    registry_path=registry_path,
                    runtime_root=runtime_root,
                    goal_id=goal_id,
                    agent_id=str(identity.get("agent_id") or ""),
                )
            )
            if projection_builder is not None
            else {}
        )
    except Exception:  # The source projection stays retryable, never primary.
        return _recorded_composition_failure(
            journal_path,
            hooks=hooks,
            goal_id=goal_id,
            event_kind=event_kind,
            identity=identity,
            state_version=state_version,
            committed_at=committed_at,
            error_code="source_projection_failed",
        )
    try:
        result = dispatch_post_writeback_hooks(
            hooks,
            source={
                "schema_version": "loopx_post_writeback_hook_source_v0",
                "event_kind": event_kind,
                "status": "committed",
                "durable": True,
                "identity": {
                    "goal_id": goal_id,
                    "agent_id": str(identity.get("agent_id") or ""),
                    "todo_id": (
                        str(identity["todo_id"])
                        if identity.get("todo_id")
                        else None
                    ),
                    "turn_instance_id": str(
                        identity.get("turn_instance_id") or ""
                    ),
                    "effect_id": str(identity.get("effect_id") or ""),
                },
                "state_version": state_version,
                **({"receipt_id": receipt_id} if receipt_id is not None else {}),
                "committed_at": committed_at,
                "projection": projection,
            },
            runtime_root=runtime_root,
        )
    except PostWritebackSourceRejected:
        # A deterministic decoder verdict cannot succeed on replay. Preserve
        # the composition failure category without creating misleading
        # retry work; the typed runtime remains the source-field authority.
        return _composition_failure_dispatch(
            hooks, error_code="source_projection_failed", receipt_ref=None
        )
    except Exception:  # An unexpected transport collapse stays retryable.
        return _recorded_composition_failure(
            journal_path,
            hooks=hooks,
            goal_id=goal_id,
            event_kind=event_kind,
            identity=identity,
            state_version=state_version,
            committed_at=committed_at,
            error_code="dispatch_failed",
        )
    # The composition receipt tracks projection composition only: it settles
    # once the projection composed and the hook lifecycle returned, while any
    # hook-level failure keeps its own per-hook failure trail in `result`.
    _settle_composition_retry_quietly(
        journal_path,
        goal_id=goal_id,
        event_kind=event_kind,
        identity=identity,
        state_version=state_version,
        committed_at=committed_at,
        hooks=hooks,
    )
    return result


__all__ = [
    "PostWritebackProjectionBuilder",
    "dispatch_committed_cli_post_writeback_hooks",
]
