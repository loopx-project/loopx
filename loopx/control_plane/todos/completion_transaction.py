"""Thin Python adapter for the TypeScript-owned Todo completion transaction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from .contract import normalize_todo_id_list
from ..content_digest import BARE_SHA256_PATTERN


TODO_COMPLETION_TRANSACTION_REQUEST_SCHEMA = "loopx_todo_completion_transaction_v0"
TODO_COMPLETION_TRANSACTION_RESULT_SCHEMA = (
    "loopx_todo_completion_transaction_result_v0"
)

_DECISIONS = {"execute_validation", "commit", "policy_reject", "replay", "reject"}
_IDENTITY_SOURCES = {
    "turn_settlement",
    "unscoped_completion",
    "lifecycle_reentry",
}
_CONTINUATIONS = {"active_goal", "successor", "no_followup"}
_RECOVERIES = {
    "same_turn_terminal_closeout",
    "lifecycle_reentry_terminal_closeout",
}
_SOURCE_FIELDS = (
    "status",
    "no_followup",
    "completion_continuation",
    "completion_turn_key",
    "successor_todo_ids",
    "task_repository",
    "validation_command",
    "validation_command_argv",
    "validation_label",
    "validation_timeout_seconds",
)
_COMPLETION_POLICY_RESULT_SCHEMA = "loopx_todo_completion_policy_result_v0"
_COMPLETION_POLICY_FAILURE_SCHEMA = "loopx_todo_completion_policy_failure_v0"


def _json_sequence(value: Any) -> Any:
    if isinstance(value, (tuple, set)):
        return list(value)
    return value


def todo_completion_source_snapshot(todo: Mapping[str, Any]) -> dict[str, Any]:
    """Project exactly the Todo fields owned by completion admission.

    The snapshot is a transport/CAS boundary, not a second semantic decoder.
    Python compares it after taking the mutation lock so a validation planned
    against one declaration cannot authorize a different persisted Todo.
    """

    return {
        field: (
            _json_sequence(todo.get(field))
            if field in {"successor_todo_ids", "validation_command_argv"}
            else todo.get(field)
        )
        for field in _SOURCE_FIELDS
    }


def todo_completion_source_matches(
    expected: Mapping[str, Any],
    todo: Mapping[str, Any],
) -> bool:
    return dict(expected) == todo_completion_source_snapshot(todo)


def todo_completion_source_drift_failure(
    *,
    goal_id: str,
    todo_id: str,
    dry_run: bool,
    reason: str,
) -> dict[str, Any]:
    """Project a retryable compare-and-swap miss without claiming a write."""

    return {
        "ok": False,
        "dry_run": dry_run,
        "completed": False,
        "changed": False,
        "goal_id": goal_id,
        "todo_id": todo_id,
        "completion_source_drift": True,
        "reason": reason,
    }


def locked_todo_completion_transaction(
    *,
    validation_gate: Mapping[str, Any],
    todo: Mapping[str, Any],
    goal_id: str,
    todo_id: str,
    dry_run: bool,
    require_source_match: bool,
    missing_is_drift: bool,
    current_completion_policy_source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind a pre-lock transaction to the Todo observed under the write lock."""

    transaction = validation_gate.get("transaction")
    if not isinstance(transaction, Mapping):
        if not missing_is_drift:
            raise RuntimeError("TypeScript Todo completion transaction result is missing")
        return {
            "transaction": None,
            "failure": todo_completion_source_drift_failure(
                goal_id=goal_id,
                todo_id=todo_id,
                dry_run=dry_run,
                reason=(
                    "Todo completion source appeared after validation planning; "
                    "retry against the current source"
                ),
            ),
        }
    source_snapshot = validation_gate.get("source_snapshot")
    if require_source_match and (
        not isinstance(source_snapshot, Mapping)
        or not todo_completion_source_matches(source_snapshot, todo)
    ):
        return {
            "transaction": None,
            "failure": todo_completion_source_drift_failure(
                goal_id=goal_id,
                todo_id=todo_id,
                dry_run=dry_run,
                reason=(
                    "Todo completion source changed after validation planning; "
                    "retry against the current source"
                ),
            ),
        }
    expected_policy_source = validation_gate.get("completion_policy_source")
    if current_completion_policy_source is not None or expected_policy_source is not None:
        if (
            not isinstance(expected_policy_source, Mapping)
            or current_completion_policy_source is None
            or dict(expected_policy_source) != dict(current_completion_policy_source)
        ):
            return {
                "transaction": None,
                "failure": todo_completion_source_drift_failure(
                    goal_id=goal_id,
                    todo_id=todo_id,
                    dry_run=dry_run,
                    reason=(
                        "Todo completion policy source changed after validation "
                        "planning; retry against the current source"
                    ),
                ),
            }
    return {"transaction": dict(transaction), "failure": None}


def user_todo_completion_metadata_updates(
    transaction: Mapping[str, Any],
    *,
    todo_already_done: bool,
) -> dict[str, Any] | None:
    """Project the metadata authorized for a direct user-Todo completion."""

    if todo_already_done:
        return None
    if transaction.get("decision") != "commit":
        raise RuntimeError(
            "TypeScript Todo completion transaction did not authorize "
            "the user Todo completion"
        )
    metadata_updates = transaction.get("metadata_updates")
    if not isinstance(metadata_updates, Mapping):
        raise RuntimeError("TypeScript Todo completion transaction metadata is missing")
    return dict(metadata_updates)


def require_completion_successor_todo_ids(
    value: list[str] | None,
) -> list[str]:
    """Normalize caller-declared successors without silently dropping bad ids."""

    normalized = normalize_todo_id_list(value)
    if value and not normalized:
        raise ValueError(
            "successor_todo_ids must contain public "
            "todo_<letters-digits-underscore-hyphen> tokens"
        )
    return normalized


def materialized_todo_completion_replay(
    *,
    transaction: Mapping[str, Any],
    todo: Mapping[str, Any],
    dry_run: bool,
    goal_id: str,
    todo_id: str,
    handoff: Mapping[str, Any],
    mutation_authority: Mapping[str, Any],
    state_file: str,
    project: str | None,
) -> dict[str, Any] | None:
    """Render the stable materialized-Todo idempotent replay response."""

    fence = transaction.get("fence")
    if not (
        transaction.get("decision") == "replay"
        and isinstance(fence, Mapping)
        and fence.get("status") == "done"
    ):
        return None
    return {
        "ok": True,
        "dry_run": dry_run,
        "completed": True,
        "idempotent_replay": True,
        "changed": False,
        "goal_id": goal_id,
        "todo_id": todo_id,
        "status": "done",
        "completion_continuation": fence.get("completion_continuation"),
        "completion_recovery": todo.get("completion_recovery"),
        "handoff_mode": handoff["handoff_mode"],
        "mutation_authority": dict(mutation_authority),
        "state_file": state_file,
        "project": project,
        "updated_at": todo.get("updated_at"),
    }


def _valid_fence(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    return (
        value.get("schema_version") == "loopx_todo_completion_fence_result_v0"
        and value.get("outcome") in {"continue", "replay"}
        and isinstance(value.get("reason"), str)
        and isinstance(value.get("status"), str)
        and isinstance(value.get("terminal_before_request"), bool)
        and (
            value.get("completion_continuation") is None
            or value.get("completion_continuation") in _CONTINUATIONS
        )
    )


def _valid_receipt(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    return (
        value.get("schema_version") == "issue_fix_validation_command_v0"
        and isinstance(value.get("command_label"), str)
        and bool(str(value.get("command_label") or "").strip())
        and (
            value.get("exit_code") is None
            or (
                isinstance(value.get("exit_code"), int)
                and not isinstance(value.get("exit_code"), bool)
            )
        )
        and isinstance(value.get("passed"), bool)
        and (
            (value.get("passed") is True and value.get("exit_code") == 0)
            or (
                value.get("passed") is False
                and value.get("exit_code") != 0
            )
        )
        and (
            value.get("status") is None
            or isinstance(value.get("status"), str)
        )
        and (
            value.get("summary") is None
            or isinstance(value.get("summary"), str)
        )
        and value.get("stdout_captured") is False
        and value.get("stderr_captured") is False
        and value.get("local_path_captured") is False
        and (
            value.get("validation_declaration_sha256") is None
            or (
                isinstance(value.get("validation_declaration_sha256"), str)
                and BARE_SHA256_PATTERN.fullmatch(
                    value.get("validation_declaration_sha256")
                )
                is not None
            )
        )
    )


def _valid_common(result: Mapping[str, Any]) -> bool:
    return (
        result.get("schema_version") == TODO_COMPLETION_TRANSACTION_RESULT_SCHEMA
        and result.get("decision") in _DECISIONS
        and (
            result.get("completion_identity_key") is None
            or isinstance(result.get("completion_identity_key"), str)
        )
        and (
            result.get("completion_identity_source") is None
            or result.get("completion_identity_source") in _IDENTITY_SOURCES
        )
        and _valid_fence(result.get("fence"))
    )


def _valid_completion_policy(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    required_fields = {
        "schema_version",
        "effective_claimed_by",
        "registered_agents",
        "effective_next_claimed_by",
        "effective_next_excluded_agents",
        "self_merged",
        "linked_successor_id",
    }
    return (
        required_fields.issubset(value)
        and value.get("schema_version") == _COMPLETION_POLICY_RESULT_SCHEMA
        and isinstance(value.get("registered_agents"), list)
        and all(isinstance(item, str) for item in value.get("registered_agents") or [])
        and isinstance(value.get("effective_next_excluded_agents"), list)
        and all(
            isinstance(item, str)
            for item in value.get("effective_next_excluded_agents") or []
        )
        and all(
            item is None or isinstance(item, str)
            for item in (
                value.get("effective_claimed_by"),
                value.get("effective_next_claimed_by"),
                value.get("linked_successor_id"),
            )
        )
        and isinstance(value.get("self_merged"), bool)
    )


def _valid_completion_policy_failure(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("schema_version") == _COMPLETION_POLICY_FAILURE_SCHEMA
        and value.get("kind") == "completion_policy_rejected"
        and isinstance(value.get("diagnostic_code"), str)
        and bool(value.get("diagnostic_code"))
        and isinstance(value.get("summary"), str)
        and bool(value.get("summary"))
    )


def _valid_execute_validation_result(result: Mapping[str, Any]) -> bool:
    effect = result.get("validation_effect")
    return (
        isinstance(effect, Mapping)
        and effect.get("kind") == "caller_validation"
        and (
            effect.get("validation_command") is None
            or isinstance(effect.get("validation_command"), str)
        )
        and (
            effect.get("validation_argv") is None
            or (
                isinstance(effect.get("validation_argv"), list)
                and bool(effect.get("validation_argv"))
                and all(
                    isinstance(item, str) and item
                    for item in effect.get("validation_argv") or []
                )
            )
        )
        and (
            (effect.get("validation_command") is None)
            != (effect.get("validation_argv") is None)
        )
        and (
            effect.get("validation_label") is None
            or isinstance(effect.get("validation_label"), str)
        )
        and (
            effect.get("validation_timeout_seconds") is None
            or (
                isinstance(effect.get("validation_timeout_seconds"), int)
                and not isinstance(effect.get("validation_timeout_seconds"), bool)
                and 1 <= int(effect["validation_timeout_seconds"]) <= 29
            )
        )
        and (
            effect.get("task_repository") is None
            or isinstance(effect.get("task_repository"), str)
        )
        and (
            effect.get("validation_declaration_sha256") is None
            or (
                isinstance(effect.get("validation_declaration_sha256"), str)
                and BARE_SHA256_PATTERN.fullmatch(
                    effect.get("validation_declaration_sha256")
                )
                is not None
            )
        )
    )


def _valid_completion_settlement(result: Mapping[str, Any]) -> bool:
    state = result.get("completion_state")
    updates = result.get("metadata_updates")
    receipt = result.get("validation_receipt")
    return (
        isinstance(state, Mapping)
        and state.get("continuation") in _CONTINUATIONS
        and (state.get("recovery") is None or state.get("recovery") in _RECOVERIES)
        and isinstance(updates, Mapping)
        and all(
            key in {"completion_continuation", "completion_recovery"}
            and isinstance(value, str)
            for key, value in updates.items()
        )
        and updates.get("completion_continuation") == state.get("continuation")
        and updates.get("completion_recovery") == state.get("recovery")
        and (receipt is None or _valid_receipt(receipt))
    )


def _valid_commit_result(
    result: Mapping[str, Any],
    *,
    completion_policy_required: bool,
) -> bool:
    policy = result.get("completion_policy")
    return (
        _valid_completion_settlement(result)
        and result.get("completion_policy_failure") is None
        and (
            _valid_completion_policy(policy)
            if completion_policy_required
            else policy is None
        )
    )


def _valid_policy_reject_result(
    result: Mapping[str, Any],
    *,
    completion_policy_required: bool,
) -> bool:
    if not completion_policy_required:
        return False
    return (
        _valid_completion_settlement(result)
        and result.get("completion_policy") is None
        and _valid_completion_policy_failure(
            result.get("completion_policy_failure")
        )
    )


def _valid_reject_result(result: Mapping[str, Any]) -> bool:
    failure = result.get("failure")
    return (
        isinstance(failure, Mapping)
        and failure.get("kind")
        in {"validation_declaration_invalid", "validation_failed"}
        and isinstance(failure.get("summary"), str)
        and _valid_receipt(failure.get("validation_receipt"))
        and failure["validation_receipt"].get("passed") is False
    )


def _valid_result(
    result: Mapping[str, Any],
    *,
    completion_policy_required: bool,
) -> bool:
    if not _valid_common(result):
        return False
    decision = result.get("decision")
    if decision == "execute_validation":
        return _valid_execute_validation_result(result)
    if decision == "commit":
        return _valid_commit_result(
            result,
            completion_policy_required=completion_policy_required,
        )
    if decision == "policy_reject":
        return _valid_policy_reject_result(
            result,
            completion_policy_required=completion_policy_required,
        )
    if decision == "replay":
        return result["fence"].get("outcome") == "replay"
    return _valid_reject_result(result)


def reduce_todo_completion_transaction(
    *,
    todo: Mapping[str, Any],
    projection_source: str,
    goal_id: str,
    todo_id: str,
    completion_turn_key: str | None,
    completion_identity_source: str | None,
    no_followup: bool,
    requested_has_successor: bool,
    dry_run: bool,
    validation_receipt: Mapping[str, Any] | None = None,
    completion_policy_request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reduce one coarse completion request through the TS authority."""

    try:
        params: dict[str, Any] = {
            "schema_version": TODO_COMPLETION_TRANSACTION_REQUEST_SCHEMA,
            "goal_id": goal_id,
            "todo_id": todo_id,
            "projection_source": projection_source,
            "todo": todo_completion_source_snapshot(todo),
            "requested_no_followup": no_followup,
            "requested_completion_turn_key": completion_turn_key,
            "requested_completion_identity_source": completion_identity_source,
            "requested_has_successor": requested_has_successor,
            "dry_run": dry_run,
            "validation_receipt": (
                dict(validation_receipt)
                if isinstance(validation_receipt, Mapping)
                else None
            ),
        }
        if completion_policy_request is not None:
            params["completion_policy_request"] = dict(completion_policy_request)
        result = effect_runtime_result("todo.completion.reduce", params)
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping):
        raise RuntimeError(
            "TypeScript Todo completion transaction result must be an object"
        )
    if not _valid_result(
        result,
        completion_policy_required=(
            completion_policy_request is not None
            and result.get("decision") in {"commit", "policy_reject"}
        ),
    ):
        raise RuntimeError(
            "TypeScript Todo completion transaction result shape mismatch"
        )
    return dict(result)
