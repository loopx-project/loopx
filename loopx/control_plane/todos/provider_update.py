"""Input transport for the native update transaction, never a Markdown editor.

The provider owns target lookup, planning, authority and CAS at one revision.
This adapter preserves CLI text encoding and drains the committed projection.
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from ...state_refresh import now_local
from ..coordination.local_authority import (
    LOCAL_AUTHORITY_SOURCES,
    LocalCoordinationAuthorityUnavailable,
    local_authority_is_promoted,
    read_canonical_todos_if_promoted,
)
from ..effect_runtime import (
    CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
    effect_runtime_result,
)
from .contract import compact_todo_text
from .provider_projection import settle_canonical_todo_projection
from .text import normalize_new_todo
from .mutation_authority import todo_lifecycle_facts
from ..coordination.authority_source_capture import authority_registry_source
from .completion_validation import (
    completion_validation_failure,
    execute_completion_validation_effects,
    resolve_private_completion_validation_declaration,
)
from .completion_validation_store import (
    persist_completion_validation_declaration,
    prepare_completion_validation_declaration,
    read_completion_validation_declaration,
)
from .completion_validation_projection import (
    completion_validation_declaration_sha256,
)
from .path_resolution import resolve_todo_state_path
from .monitor_metadata import MonitorPollObservation


def _completion_validation_revision_request(
    *,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    operation_id: str | None,
    declaration: dict[str, Any],
) -> dict[str, Any]:
    if not operation_id:
        raise ValueError("completion validation revision requires an operation id")
    canonical = read_canonical_todos_if_promoted(
        runtime_root=runtime_root, goal_id=goal_id
    )
    if canonical is None:
        raise ValueError("Canonical authority changed during validation revision; retry")
    target = next(
        (todo for todo in canonical["todos"] if todo.get("todo_id") == todo_id),
        {},
    )
    expected_digest = target.get("completion_validation_sha256")
    # An idempotent retry observes the post-commit Todo. Reconstruct the
    # original CAS witness from its durable revision receipt so the same
    # operation id hashes to the same request instead of looking like a
    # conflicting mutation.
    for receipt in target.get("completion_validation_revision_history") or []:
        if (
            isinstance(receipt, dict)
            and receipt.get("operation_id") == operation_id
            and "previous_declaration_sha256" in receipt
        ):
            expected_digest = receipt["previous_declaration_sha256"]
            break
    # Serialize canonical absence; the typed transaction alone decides whether
    # this is a legal first binding or an inconsistent/missing declaration.
    return {
        "schema_version": (
            "loopx_todo_completion_validation_revision_v1"
            if expected_digest is None
            else "loopx_todo_completion_validation_revision_v0"
        ),
        "expected_declaration_sha256": expected_digest,
        "declaration": declaration,
    }


def _publish_completion_validation_revision(
    *,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    operation_id: str,
    declaration: dict[str, Any],
    result: dict[str, Any],
) -> None:
    expected_digest = completion_validation_declaration_sha256(declaration)
    canonical = read_canonical_todos_if_promoted(
        runtime_root=runtime_root, goal_id=goal_id
    )
    target = next(
        (
            todo
            for todo in (canonical or {}).get("todos", [])
            if todo.get("todo_id") == todo_id
        ),
        {},
    )
    revision_history = target.get("completion_validation_revision_history")
    if (
        target.get("completion_validation_sha256") != expected_digest
        or not isinstance(revision_history, list)
        or not any(
            isinstance(receipt, dict)
            and receipt.get("operation_id") == operation_id
            and receipt.get("declaration_sha256") == expected_digest
            for receipt in revision_history
        )
    ):
        raise LocalCoordinationAuthorityUnavailable(
            "completion validation revision receipt does not match canonical readback",
            code="completion_validation_revision_publication_mismatch",
            payload=dict(result),
        )
    persist_completion_validation_declaration(
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=todo_id,
        declaration=declaration,
    )
    if read_completion_validation_declaration(
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=todo_id,
        expected_digest=expected_digest,
    ) != declaration:
        raise LocalCoordinationAuthorityUnavailable(
            "completion validation revision committed but private declaration readback failed",
            code="completion_validation_revision_readback_failed",
            payload=dict(result),
        )


def update_canonical_todo_if_promoted(
    *, registry_path: Path, runtime_root: Path, goal_id: str, todo_id: str,
    actor_agent_id: str | None, role: str | None, text: str | None,
    note: str | None, dry_run: bool,
    project: Path | None = None, state_file: Path | None = None,
    operation_id: str | None = None, task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
    planning_intent: dict[str, Any] | None = None,
    authority_reason: str | None = None,
    expected_provider_revision: str | None = None,
    expected_registry_sha256: str | None = None,
    monitor_observation: MonitorPollObservation | None = None,
    completion_validation_revision: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not local_authority_is_promoted(runtime_root=runtime_root, goal_id=goal_id):
        return None
    # The witness brackets fact projection and is rechecked by the native
    # transaction. Reviewed edits additionally bind the preview's registry hash.
    with authority_registry_source(registry_path) as registry_source:
        registered, grants = todo_lifecycle_facts(registry_path, goal_id)
    patch: dict[str, Any] = {}
    if text is not None:
        patch["text"] = normalize_new_todo(text)
    if note is not None:
        # Empty notes have always meant omission at the public update boundary.
        # Clearing a persisted note requires a future explicit contract; never
        # reinterpret an empty CLI/Python value as an implicit clear here.
        normalized_note = compact_todo_text(note)
        if normalized_note:
            patch["note"] = normalized_note
    completion = None
    validation_revision = None
    if completion_validation_revision is not None:
        validation_revision = _completion_validation_revision_request(
            runtime_root=runtime_root,
            goal_id=goal_id,
            todo_id=todo_id,
            operation_id=operation_id,
            declaration=completion_validation_revision,
        )
    if str((planning_intent or {}).get("status", "")).strip().lower() == "done":
        project, state_file = resolve_todo_state_path(registry_path=registry_path, goal_id=goal_id,
            project=project, state_file=state_file, require_existing=False)
        canonical = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id)
        if canonical is None:
            raise ValueError("Canonical authority changed during Todo completion update; retry")
        target = next((todo for todo in canonical["todos"] if todo.get("todo_id") == todo_id), {})
        declaration = None
        if target.get("status") != "done" and target.get("completion_validation_required") is True:
            declaration = resolve_private_completion_validation_declaration(
                canonical_todo=target, state_file=state_file, runtime_root=runtime_root,
                registry_path=registry_path, goal_id=goal_id, todo_id=todo_id, role=role,
                persist_if_resolved=not dry_run)
        completion = {"validation_declaration": declaration}
    request = {
        "schema_version": ("loopx_local_coordination_todo_update_request_v5" if validation_revision is not None
                           else "loopx_local_coordination_todo_update_request_v4" if monitor_observation is not None
                           else "loopx_local_coordination_todo_update_request_v3" if completion is not None
                           else "loopx_local_coordination_todo_update_request_v2"),
        "runtime_root": str(runtime_root.resolve()), "goal_id": goal_id,
        "todo_id": todo_id, "role": role, "actor_agent_id": actor_agent_id,
        "registered_agents": registered, "lifecycle_grants": grants,
        "authority_reason": authority_reason,
        "registry_source": registry_source,
        "expected_provider_revision": expected_provider_revision,
        "expected_registry_sha256": expected_registry_sha256,
        "operation_id": (operation_id if operation_id is not None else
                         (monitor_observation.monitor_effect_id if monitor_observation else None)
                         or f"todo-update:{uuid4().hex}"),
        "lease_idempotency_key": task_lease_idempotency_key,
        "lease_expected_version": task_lease_expected_version,
        "patch": patch, "clear_fields": [], "dry_run": dry_run,
        "planning_intent": planning_intent or {},
        "observed_at": now_local(),
        **({"completion": completion} if completion is not None else {}),
        **({"completion_validation_revision": validation_revision}
           if validation_revision is not None else {}),
        **({"monitor_observation": asdict(monitor_observation)} if monitor_observation is not None else {}),
    }
    if completion_validation_revision is not None and not dry_run:
        prepare_completion_validation_declaration(
            runtime_root=runtime_root, goal_id=goal_id, declaration=completion_validation_revision
        )
    result = effect_runtime_result(
        "coordination.local_authority.todo_update", request,
        timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
    )
    completion_validation_executed = False
    if isinstance(result, dict) and result.get("status") == "execute_validation":
        if completion is None:
            raise RuntimeError("Ordinary Todo update cannot issue completion validation")
        completion["source_provider_revision"] = result["provider_revision"]
        completion.update(execute_completion_validation_effects(
            result, registry_path=registry_path, goal_id=goal_id))
        completion_validation_executed = True
        request["observed_at"] = now_local()
        result = effect_runtime_result(
            "coordination.local_authority.todo_update", request,
            timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
        )
    if isinstance(result, dict):
        failure = completion_validation_failure(result, goal_id=goal_id, todo_id=todo_id, dry_run=dry_run)
        if failure is not None:
            return failure
    # Keep the public lookup-error contract, without a second pre-transaction read.
    if isinstance(result, dict) and result.get("status") == "failed":
        if result.get("reason_code") == "todo_not_found":
            raise ValueError("Todo is missing from canonical authority")
        if result.get("reason_code") == "todo_role_mismatch":
            raise ValueError("Todo does not have the requested role")
    if isinstance(result, dict) and (
        result.get("status") == "missing"
        and result.get("source_authority") in LOCAL_AUTHORITY_SOURCES
        and result.get("decision_read_from_provider") is True
        and result.get("legacy_fallback_used") is False
    ):
        payload = dict(result)
        payload["recovery"] = {
            "action": "restore_canonical_authority",
            "runtime_root": str(runtime_root.expanduser().resolve(strict=False)),
            "goal_id": goal_id,
            "legacy_markdown_fallback_allowed": False,
            "retry_after": "canonical_provider_readback_loaded",
        }
        raise LocalCoordinationAuthorityUnavailable(
            "canonical Todo authority is unavailable",
            code="local_authority_todo_list_unavailable",
            payload=payload,
        )
    if not isinstance(result, dict) or result.get("status") not in {
        "applied", "recovered", "replayed", "no_change", "planned",
    } or result.get("source_authority") not in LOCAL_AUTHORITY_SOURCES or (
        result.get("decision_read_from_provider") is not True
        or result.get("legacy_fallback_used") is not False
    ):
        payload = dict(result) if isinstance(result, dict) else {}
        if completion_validation_executed:
            payload["completion_validation_executed"] = True
        raise LocalCoordinationAuthorityUnavailable(
            str(payload.get("reason") or "canonical Todo update failed; reread before retry"),
            code=str(payload.get("reason_code") or payload.get("conflict_kind")
                     or "local_authority_todo_update_failed"), payload=payload,
        )
    if validation_revision is not None and not dry_run:
        declaration = dict(validation_revision["declaration"])
        _publish_completion_validation_revision(
            runtime_root=runtime_root,
            goal_id=goal_id,
            todo_id=todo_id,
            operation_id=str(request["operation_id"]),
            declaration=declaration,
            result=result,
        )
    return settle_canonical_todo_projection(
        {"ok": True, "goal_id": goal_id, "todo_id": todo_id,
         "role": role, "dry_run": dry_run, **result},
        registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
        project=project, state_file=state_file,
    )
