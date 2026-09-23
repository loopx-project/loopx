"""Provider-first complete, supersede, and archive adapters.

Python projects registry facts, serializes caller intent, executes a typed
validation effect, and drains the committed Markdown projection outbox.  The
TypeScript transaction is the sole owner of lifecycle admission and writes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from functools import wraps
from inspect import signature
from pathlib import Path
from typing import Any

from ...state_refresh import now_local
from ..coordination.local_authority import (
    LOCAL_AUTHORITY_SOURCES,
    LocalCoordinationAuthorityRejection,
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from ..coordination.local_authority_shadow_adapter import effective_runtime_root
from ..effect_runtime import effect_runtime_result
from .completion_policy import (
    build_completion_policy_request,
    linked_successor_from_todo,
)
from .completion_transaction import require_completion_successor_todo_ids
from .completion_validation import (
    resolve_private_completion_validation_declaration,
    execute_completion_validation_effects,
    completion_validation_failure,
)
from .contract import resolve_next_user_task_class
from .mutation_authority import todo_lifecycle_facts
from ..coordination.authority_source_capture import authority_registry_source
from .path_resolution import resolve_todo_state_path
from .provider_projection import projection_delivery_requires_ack, settle_canonical_todo_projection
from .successor_derivation import build_successor_intents

_TERMINAL_REQUEST_SCHEMA = "loopx_local_coordination_todo_terminal_lifecycle_request_v3"
_ARCHIVE_REQUEST_SCHEMA = "loopx_local_coordination_todo_archive_request_v0"
_ARCHIVE_ACK_REQUEST_SCHEMA = "loopx_local_coordination_todo_archive_ack_request_v0"
_ACCEPTED = {"applied", "recovered", "replayed", "no_change", "planned"}
# A promoted File authority can verify and publish a retained journal while
# holding the canonical writer fence. This is an explicit terminal-command
# budget, not a global relaxation of the Effect runtime request deadline.
_TERMINAL_RUNTIME_TIMEOUT_SECONDS = 45.0


TodoMutation = Callable[..., dict[str, Any]]


def _non_negative_integer(value: Any, label: str, *, optional: bool) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        suffix = " or None" if optional else ""
        raise ValueError(f"{label} must be a non-negative integer{suffix}")
    result: int = value
    return result


def _route_terminal_call(command: str, call: Mapping[str, Any]) -> dict[str, Any] | None:
    registry_path = Path(call["registry_path"])
    goal_id = str(call["goal_id"])
    runtime_root = effective_runtime_root(registry_path, call.get("runtime_root_arg"))
    if command == "archive":
        role = str(call["role"])
        max_active_done = _non_negative_integer(
            call["max_active_done"], "max_active_done", optional=False
        )
        assert max_active_done is not None
        if role not in {"user", "agent"}:
            raise ValueError("todo role must be one of: user, agent")
        project, state_file = resolve_todo_state_path(
            registry_path=registry_path,
            goal_id=goal_id,
            project=call.get("project"),
            state_file=call.get("state_file"),
            # Canonical archive can recover its display after committing or
            # replaying. The legacy fallback still requires an existing file.
            require_existing=False,
        )
        return archive_canonical_todos_if_promoted(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            role=role,
            max_active_done=max_active_done,
            dry_run=bool(call["dry_run"]),
            project=project,
            state_file=state_file,
        )

    next_agent_todo = call.get("next_agent_todo")
    if call.get("next_task_repository") and not next_agent_todo:
        raise ValueError("--next-task-repository requires --next-agent-todo")
    if call.get("next_required_capabilities") and not next_agent_todo:
        raise ValueError("--next-required-capability requires --next-agent-todo")
    project, state_file = resolve_todo_state_path(
        registry_path=registry_path,
        goal_id=goal_id,
        project=call.get("project"),
        state_file=call.get("state_file"),
        # Completion consumes canonical state and can rebuild its display just
        # like archive. The legacy caller below still requires its source file.
        require_existing=False,
    )
    complete = command == "complete"
    return terminal_canonical_todo_if_promoted(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        command=command,
        todo_id=str(call["todo_id"]),
        role=call.get("role"),
        actor_agent_id=call.get("agent_id"),
        authority_reason=call.get("authority_reason"),
        decision_outcome=call.get("decision_outcome") if complete else None,
        evidence=call.get("evidence") if complete else None,
        note=call.get("note") if complete else "superseded",
        reason=None if complete else call.get("reason"),
        completion_turn_key=call.get("completion_turn_key") if complete else None,
        completion_identity_source=(
            call.get("completion_identity_source") if complete else None
        ),
        review_basis=call.get("terminal_review_basis"),
        completion_delivery_workspace=(
            call.get("completion_delivery_workspace") if complete else None
        ),
        completion_validation_workspace_path=(
            call.get("completion_validation_workspace_path") if complete else None
        ),
        task_lease_idempotency_key=call.get("task_lease_idempotency_key"),
        task_lease_expected_version=_non_negative_integer(
            call.get("task_lease_expected_version"),
            "task_lease_expected_version",
            optional=True,
        ),
        no_followup=bool(call.get("no_followup")) if complete else False,
        successor_todo_ids=(
            require_completion_successor_todo_ids(call.get("successor_todo_ids"))
            if complete
            else []
        ),
        claimed_by=call.get("claimed_by") if complete else None,
        clear_claim=bool(call.get("clear_claim")) if complete else False,
        next_agent_todo=next_agent_todo,
        next_user_todo=call.get("next_user_todo"),
        next_user_task_class=resolve_next_user_task_class(
            call.get("next_user_todo"), call.get("next_user_task_class")
        ),
        next_claimed_by=call.get("next_claimed_by"),
        next_task_class=call.get("next_task_class"),
        next_action_kind=call.get("next_action_kind"),
        next_task_repository=call.get("next_task_repository"),
        next_required_capabilities=call.get("next_required_capabilities"),
        next_continuation_policy=call.get("next_continuation_policy"),
        next_excluded_agents=call.get("next_excluded_agents"),
        self_merged=bool(call.get("self_merged")) if complete else False,
        dry_run=bool(call["dry_run"]),
        project=project,
        state_file=state_file,
    )


def provider_first_terminal_lifecycle(command: str) -> Callable[[TodoMutation], TodoMutation]:
    """Route the public facade through canonical authority before legacy fallback."""
    if command not in {"complete", "supersede", "archive"}:
        raise ValueError(f"unsupported terminal lifecycle command: {command}")

    def decorate(legacy: TodoMutation) -> TodoMutation:
        call_signature = signature(legacy)

        @wraps(legacy)
        def routed(*args: Any, **kwargs: Any) -> dict[str, Any]:
            bound = call_signature.bind(*args, **kwargs)
            bound.apply_defaults()
            result = _route_terminal_call(command, bound.arguments)
            if result is None and bound.arguments.get("terminal_review_basis") is not None:
                raise ValueError("Reviewed canonical completion cannot fall back to legacy authority; regenerate preview")
            return result if result is not None else legacy(*args, **kwargs)

        return routed

    return decorate


def _todo_by_id(
    todos: Iterable[Mapping[str, Any]], todo_id: str
) -> dict[str, Any] | None:
    return next(
        (dict(todo) for todo in todos if str(todo.get("todo_id") or "") == todo_id),
        None,
    )


def _projection_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LocalCoordinationAuthorityUnavailable(
            "canonical Todo projection returned an invalid result",
            code="local_authority_todo_projection_invalid_result",
            payload={"source_authority": "file_v0"},
        )
    return dict(value)


def _terminal_operation_id(
    *,
    command: str,
    goal_id: str,
    todo_id: str,
    completion_turn_key: str | None,
) -> str:
    """Name one logical terminal operation independently of retry prose."""

    operation_identity = completion_turn_key or "unscoped"
    digest = hashlib.sha256(
        (
            "loopx-provider-terminal-operation-v0\0"
            f"{command}\0{goal_id}\0{todo_id}\0{operation_identity}"
        ).encode("utf-8")
    ).hexdigest()
    return f"todo-terminal:{digest[:32]}"


def _archive_operation_id(
    *,
    goal_id: str,
    role: str,
    max_active_done: int,
    provider_revision: str,
) -> str:
    """Bind one archive attempt to the canonical snapshot it selected from."""

    digest = hashlib.sha256(
        f"{goal_id}\0{role}\0{max_active_done}\0{provider_revision}".encode("utf-8")
    ).hexdigest()
    return f"todo-archive:{digest[:32]}"


def terminal_canonical_todo_if_promoted(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    command: str,
    todo_id: str,
    role: str | None,
    actor_agent_id: str | None,
    authority_reason: str | None,
    decision_outcome: str | None,
    evidence: str | None,
    note: str | None,
    reason: str | None,
    completion_turn_key: str | None,
    completion_identity_source: str | None,
    completion_delivery_workspace: Mapping[str, Any] | None,
    completion_validation_workspace_path: Path | None,
    task_lease_idempotency_key: str | None,
    task_lease_expected_version: int | None,
    no_followup: bool,
    successor_todo_ids: list[str],
    claimed_by: str | None,
    clear_claim: bool,
    next_agent_todo: str | None,
    next_user_todo: str | None,
    next_user_task_class: str | None,
    next_claimed_by: str | None,
    next_task_class: str | None,
    next_action_kind: str | None,
    next_task_repository: str | None,
    next_required_capabilities: list[str] | None,
    next_continuation_policy: str | None,
    next_excluded_agents: list[str] | None,
    self_merged: bool,
    dry_run: bool,
    review_basis: Mapping[str, Any] | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
) -> dict[str, Any] | None:
    try:
        canonical = read_canonical_todos_if_promoted(
            runtime_root=runtime_root, goal_id=goal_id
        )
    except LocalCoordinationAuthorityUnavailable as exc:
        payload = dict(exc.payload)
        if exc.code == "local_authority_todo_list_unavailable" and payload.get(
            "status"
        ) == "missing":
            payload["recovery"] = {
                "action": "restore_canonical_authority",
                "runtime_root": str(runtime_root.expanduser().resolve(strict=False)),
                "goal_id": goal_id,
                "legacy_markdown_fallback_allowed": False,
                "retry_after": "canonical_provider_readback_loaded",
            }
        raise LocalCoordinationAuthorityUnavailable(
            str(exc), code=exc.code, payload=payload
        ) from exc
    if canonical is None:
        return None
    todos = [dict(todo) for todo in canonical["todos"]]
    # The canonical transaction owns missing/role/archive lifecycle decisions.
    # Keep only the optional local validation facts needed by the host adapter.
    target = _todo_by_id(todos, todo_id) or {}
    with authority_registry_source(registry_path) as registry_source:
        registered, grants = todo_lifecycle_facts(registry_path, goal_id)
        successor_intents = build_successor_intents(
            next_agent_todo=next_agent_todo,
            next_user_todo=next_user_todo,
            next_user_task_class=next_user_task_class,
            next_claimed_by=next_claimed_by,
            next_task_class=next_task_class,
            next_action_kind=next_action_kind,
            next_task_repository=next_task_repository,
            next_required_capabilities=next_required_capabilities,
            next_continuation_policy=next_continuation_policy,
            next_excluded_agents=next_excluded_agents,
        )
        linked = [
            linked_successor_from_todo(todo)
            for linked_id in successor_todo_ids
            if (todo := _todo_by_id(todos, linked_id)) is not None
        ]
        completion_policy_request = (
            build_completion_policy_request(
                registry_path=registry_path,
                goal_id=goal_id,
                claimed_by=claimed_by,
                next_claimed_by=next_claimed_by,
                next_agent_todo=next_agent_todo,
                next_action_kind=next_action_kind,
                next_continuation_policy=next_continuation_policy,
                next_excluded_agents=next_excluded_agents or [],
                self_merged=self_merged,
                evidence=evidence,
                linked_successors=linked,
            )
            if command == "complete"
            else None
        )
        implicit_monitor_cycle = (
            command == "complete"
            and completion_turn_key is None
            and target.get("task_class") == "continuous_monitor"
        )
        request = {
            "schema_version": _TERMINAL_REQUEST_SCHEMA,
            **({"review_basis": dict(review_basis)} if review_basis is not None else {}),
            "validation_source_provider_revision": None,
            "runtime_root": str(runtime_root.expanduser().resolve(strict=False)),
            "goal_id": goal_id,
            "todo_id": todo_id,
            "role": role,
            "command": command,
            "actor_agent_id": actor_agent_id,
            "registered_agents": registered,
            "lifecycle_grants": grants,
            "registry_source": registry_source,
            "authority_reason": authority_reason,
            "decision_outcome": decision_outcome,
            "operation_identity": (
                {"kind": "current_monitor_cycle"}
                if implicit_monitor_cycle
                else {"kind": "explicit", "operation_id": _terminal_operation_id(
                    command=command, goal_id=goal_id, todo_id=todo_id,
                    completion_turn_key=completion_turn_key,
                )}
            ),
            "lease_idempotency_key": task_lease_idempotency_key,
            "lease_expected_version": task_lease_expected_version,
            "allow_user_gate_auto_acquire": command == "complete",
            "requested_no_followup": no_followup,
            "requested_completion_turn_key": completion_turn_key,
            "requested_completion_identity_source": completion_identity_source,
            "linked_successor_todo_ids": successor_todo_ids,
            "successor_intents": successor_intents,
            "note": note,
            "evidence": evidence,
            "reason": reason,
            "clear_claim": clear_claim,
            "validation_declaration": None,
            "validation_declaration_sha256": target.get("completion_validation_sha256") if command == "complete" else None,
            "validation_receipt": None,
            "completion_policy_request": completion_policy_request,
            "dry_run": dry_run,
            "observed_at": now_local(),
        }
    result = effect_runtime_result(
        "coordination.local_authority.todo_terminal", request,
        timeout=_TERMINAL_RUNTIME_TIMEOUT_SECONDS,
    )
    if isinstance(result, Mapping) and result.get("status") == "resolve_validation":
        # Admission and receipt recovery precede host-local declaration IO.
        # Resolving private argv grants no authority to run it; re-enter the
        # same transaction and original source witness before executing effects.
        if state_file is None:
            raise ValueError("canonical Todo completion validation requires its private state projection")
        request["validation_source_provider_revision"] = result["provider_revision"]
        request["validation_declaration"] = resolve_private_completion_validation_declaration(
            canonical_todo=target, state_file=state_file, runtime_root=runtime_root,
            registry_path=registry_path, goal_id=goal_id, todo_id=todo_id, role=role,
            persist_if_resolved=not dry_run,
        )
        result = effect_runtime_result(
            "coordination.local_authority.todo_terminal", request,
            timeout=_TERMINAL_RUNTIME_TIMEOUT_SECONDS,
        )
    completion_validation_executed = False
    if isinstance(result, Mapping) and result.get("status") == "execute_validation":
        request["validation_source_provider_revision"] = result["provider_revision"]
        request.update(execute_completion_validation_effects(
            result, registry_path=registry_path, goal_id=goal_id,
            delivery_workspace=completion_delivery_workspace,
            validation_workspace_path=completion_validation_workspace_path,
        ))
        completion_validation_executed = True
        request["observed_at"] = now_local()
        result = effect_runtime_result(
            "coordination.local_authority.todo_terminal", request,
            timeout=_TERMINAL_RUNTIME_TIMEOUT_SECONDS,
        )
    if not isinstance(result, Mapping):
        raise LocalCoordinationAuthorityUnavailable(
            "canonical Todo terminal transaction returned an invalid result",
            code="local_authority_todo_terminal_invalid_result",
            payload={"source_authority": "file_v0"},
        )
    validation_failure = completion_validation_failure(
        result, goal_id=goal_id, todo_id=todo_id, dry_run=dry_run
    )
    if validation_failure is not None:
        return validation_failure
    payload = dict(result)
    if completion_validation_executed:
        payload["completion_validation_executed"] = True
    if (
        payload.get("status") == "failed"
        and payload.get("failure_kind") == "decision_rejection"
    ):
        raise LocalCoordinationAuthorityRejection(
            str(payload.get("reason") or "canonical Todo terminal request was rejected"),
            code=str(payload.get("reason_code") or "todo_terminal_rejected"),
            payload=payload,
        )
    if (
        payload.get("status") not in _ACCEPTED
        or payload.get("source_authority") not in LOCAL_AUTHORITY_SOURCES
        or payload.get("decision_read_from_provider") is not True
        or payload.get("legacy_fallback_used") is not False
    ):
        raise LocalCoordinationAuthorityUnavailable(
            str(payload.get("reason") or "canonical Todo terminal transaction failed"),
            code=str(
                payload.get("reason_code") or "local_authority_todo_terminal_failed"
            ),
            payload=payload,
        )
    provider_status = str(payload.get("status") or "")
    terminal_decision = payload.get("terminal_decision")
    idempotent_replay = provider_status in {"replayed", "no_change"} or (
        isinstance(terminal_decision, Mapping)
        and terminal_decision.get("idempotent") is True
    )
    response = {
        **payload,
        "ok": True,
        "dry_run": dry_run,
        "completed": command == "complete",
        "superseded": command == "supersede",
        "goal_id": goal_id,
        "role": target.get("role") or role,
        "todo_id": todo_id,
        "status": "planned" if dry_run else "done",
        "provider_status": provider_status,
        "idempotent_replay": idempotent_replay,
        "state_file": str(state_file) if state_file is not None else None,
        "project": str(project) if project is not None else None,
        "updated_at": payload.get("completed_at") if payload.get("changed") else None,
        "next_todos": payload.get("generated_successors") or [],
        "mutation_authority": terminal_decision,
        "task_lease_fence": terminal_decision,
    }
    return _projection_payload(
        settle_canonical_todo_projection(
            response,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            project=project,
            state_file=state_file,
        )
    )


def archive_canonical_todos_if_promoted(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    role: str,
    max_active_done: int,
    dry_run: bool,
    project: Path | None = None,
    state_file: Path | None = None,
) -> dict[str, Any] | None:
    authority_read = read_canonical_todos_if_promoted(
        runtime_root=runtime_root, goal_id=goal_id
    )
    if authority_read is None:
        return None
    provider_revision = authority_read.get("provider_revision")
    if not isinstance(provider_revision, str) or not provider_revision:
        raise LocalCoordinationAuthorityUnavailable(
            "canonical Todo authority omitted provider revision",
            code="local_authority_todo_archive_revision_missing",
            payload=dict(authority_read),
        )
    result = effect_runtime_result(
        "coordination.local_authority.todo_archive",
        {
            "schema_version": _ARCHIVE_REQUEST_SCHEMA,
            "runtime_root": str(runtime_root.expanduser().resolve(strict=False)),
            "goal_id": goal_id,
            "role": role,
            "max_active_done": max_active_done,
            "operation_id": _archive_operation_id(
                goal_id=goal_id,
                role=role,
                max_active_done=max_active_done,
                provider_revision=provider_revision,
            ),
            "expected_provider_revision": provider_revision,
            "dry_run": dry_run,
            "observed_at": now_local(),
        },
    )
    if not isinstance(result, Mapping) or result.get("status") not in _ACCEPTED:
        payload = dict(result) if isinstance(result, Mapping) else {}
        raise LocalCoordinationAuthorityUnavailable(
            str(payload.get("reason") or "canonical Todo archive transaction failed"),
            code=str(
                payload.get("reason_code") or "local_authority_todo_archive_failed"
            ),
            payload=payload,
        )
    response = _projection_payload(
        settle_canonical_todo_projection(
            {"ok": True, "dry_run": dry_run, "goal_id": goal_id, **dict(result)},
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            project=project,
            state_file=state_file,
        )
    )
    if (
        not dry_run
        and response.get("moved_count", 0) > 0
        and projection_delivery_requires_ack(response.get("projection_delivery"))
    ):
        # The native owner retains the attempt until its external projection
        # provider succeeds. An ACK failure must preserve the committed result
        # and leave the same attempt available for the next retry.
        try:
            acknowledgement = effect_runtime_result(
                "coordination.local_authority.todo_archive_ack",
                {
                    "schema_version": _ARCHIVE_ACK_REQUEST_SCHEMA,
                    "runtime_root": str(runtime_root.expanduser().resolve(strict=False)),
                    "goal_id": goal_id,
                    "role": role,
                    "operation_id": response.get("operation_id"),
                },
            )
            response["archive_delivery_ack"] = (
                dict(acknowledgement)
                if isinstance(acknowledgement, Mapping)
                else {"status": "pending", "reason_code": "invalid_archive_ack_result"}
            )
        except Exception as error:  # noqa: BLE001 - the canonical commit already landed
            response["archive_delivery_ack"] = {
                "status": "pending", "reason_code": "archive_ack_unavailable",
                "error_class": error.__class__.__name__, "retryable": True,
            }
    return response


__all__ = [
    "archive_canonical_todos_if_promoted",
    "provider_first_terminal_lifecycle",
    "terminal_canonical_todo_if_promoted",
]
