from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result


SCHEDULER_STATE_SCHEMA_VERSION = "loopx_scheduler_state_v0"
SCHEDULER_HOST_UPDATE_FAILURE_SCHEMA_VERSION = "scheduler_host_update_failure_v0"
SCHEDULER_STATE_OPERATION_REQUEST_SCHEMA = (
    "loopx_scheduler_state_operation_request_v0"
)
SCHEDULER_STATE_OPERATION_RESULT_SCHEMA = (
    "loopx_scheduler_state_operation_result_v0"
)
SCHEDULER_STATE_STORE_REQUEST_SCHEMA = "loopx_scheduler_state_store_request_v0"
SCHEDULER_STATE_STORE_RESULT_SCHEMA = "loopx_scheduler_state_store_result_v0"
APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY = (
    "scheduler_hint.app_automation.stateful_backoff"
)
LEGACY_CODEX_APP_STATEFUL_BACKOFF_STATE_KEY = (
    "scheduler_hint.codex_app.stateful_backoff"
)
# Historical Codex callers continue to default to their legacy key. Canonical
# App packets pass APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY explicitly.
CODEX_APP_STATEFUL_BACKOFF_STATE_KEY = (
    LEGACY_CODEX_APP_STATEFUL_BACKOFF_STATE_KEY
)
CODEX_APP_SURFACE = "codex_app"


def _operation_result(operation: str, **params: Any) -> Any:
    try:
        result = effect_runtime_result(
            "scheduler.state.evaluate",
            {
                "schema_version": SCHEDULER_STATE_OPERATION_REQUEST_SCHEMA,
                "operation": operation,
                **params,
            },
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if (
        not isinstance(result, Mapping)
        or result.get("schema_version") != SCHEDULER_STATE_OPERATION_RESULT_SCHEMA
        or result.get("operation") != operation
    ):
        raise RuntimeError("TypeScript scheduler state result shape mismatch")
    return result.get("value")


def _scope(
    *,
    goal_id: Any,
    agent_id: Any,
    surface: Any,
    state_key: Any,
) -> dict[str, Any]:
    return {
        "goal_id": goal_id,
        "agent_id": agent_id,
        "surface": surface,
        "state_key": state_key,
    }


def _reference_time(value: datetime | str | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def rrule_for_minutes(minutes: int) -> str:
    value = _operation_result("rrule_for_minutes", value=minutes)
    if not isinstance(value, str):
        raise RuntimeError("TypeScript scheduler RRULE result must be a string")
    return value


def normalize_scheduler_rrule(value: Any) -> str:
    normalized = _operation_result("normalize_rrule", value=value)
    if not isinstance(normalized, str):
        raise RuntimeError("TypeScript normalized RRULE must be a string")
    return normalized


def scheduler_rrule_interval_minutes(value: Any) -> int | None:
    interval = _operation_result("rrule_interval_minutes", value=value)
    if interval is not None and (
        isinstance(interval, bool) or not isinstance(interval, int)
    ):
        raise RuntimeError("TypeScript scheduler interval must be an integer or null")
    return interval


def normalize_scheduler_host_update_failure(value: Any) -> dict[str, Any] | None:
    failure = _operation_result("normalize_failure", value=value)
    if failure is not None and not isinstance(failure, dict):
        raise RuntimeError("TypeScript scheduler failure must be an object or null")
    return failure


def _failure_list(value: Any, *, operation: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RuntimeError(f"TypeScript scheduler {operation} must be an object list")
    return value


def normalize_scheduler_host_update_failures(
    value: Any,
    *,
    legacy_failure: Any = None,
) -> list[dict[str, Any]]:
    return _failure_list(
        _operation_result(
            "normalize_failures",
            value=value,
            legacy_failure=legacy_failure,
        ),
        operation="failure cache",
    )


def retained_scheduler_host_update_failures(
    value: Any,
    *,
    reference_time: datetime | str | None = None,
    observed_host_rrule: Any = None,
) -> list[dict[str, Any]]:
    return _failure_list(
        _operation_result(
            "retain_failures",
            value=value,
            reference_time=_reference_time(reference_time),
            observed_host_rrule=observed_host_rrule,
        ),
        operation="retained failures",
    )


def merge_scheduler_host_update_failure(
    value: Any,
    failure: dict[str, Any],
    *,
    reference_time: datetime | str | None = None,
) -> list[dict[str, Any]]:
    return _failure_list(
        _operation_result(
            "merge_failure",
            value=value,
            failure=failure,
            reference_time=_reference_time(reference_time),
        ),
        operation="merged failures",
    )


def normalize_scheduler_state(
    state: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str,
    surface: str = CODEX_APP_SURFACE,
    state_key: str = CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
) -> dict[str, Any] | None:
    normalized = _operation_result(
        "normalize_state",
        state=state,
        **_scope(
            goal_id=goal_id,
            agent_id=agent_id,
            surface=surface,
            state_key=state_key,
        ),
    )
    if normalized is not None and not isinstance(normalized, dict):
        raise RuntimeError("TypeScript scheduler state must be an object or null")
    return normalized


def build_scheduler_state(
    *,
    goal_id: Any,
    agent_id: str,
    surface: str = CODEX_APP_SURFACE,
    state_key: str = CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
    reset_token: Any,
    identity_signature: Any,
    progression_index: int,
    progression_minutes: list[Any],
    last_applied_rrule: Any,
    updated_at: str,
    source: str | None = None,
    host_update_failure: dict[str, Any] | None = None,
    host_update_failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    state = _operation_result(
        "build_state",
        reset_token=reset_token,
        identity_signature=identity_signature,
        progression_index=progression_index,
        progression_minutes=progression_minutes,
        last_applied_rrule=last_applied_rrule,
        updated_at=updated_at,
        source=source,
        host_update_failure=host_update_failure,
        host_update_failures=host_update_failures,
        **_scope(
            goal_id=goal_id,
            agent_id=agent_id,
            surface=surface,
            state_key=state_key,
        ),
    )
    if not isinstance(state, dict):
        raise RuntimeError("TypeScript built scheduler state must be an object")
    return state


def scheduler_state_path(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    surface: str = CODEX_APP_SURFACE,
    state_key: str = CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
) -> Path:
    value = _operation_result(
        "state_path",
        runtime_root=str(runtime_root),
        **_scope(
            goal_id=goal_id,
            agent_id=agent_id,
            surface=surface,
            state_key=state_key,
        ),
    )
    if not isinstance(value, str) or not value:
        raise RuntimeError("TypeScript scheduler state path must be a string")
    return Path(value)


def _store_result(
    operation: str,
    runtime_root: Path,
    *,
    state: dict[str, Any] | None,
    goal_id: str,
    agent_id: str,
    surface: str,
    state_key: str,
) -> Mapping[str, Any]:
    params: dict[str, Any] = {
        "schema_version": SCHEDULER_STATE_STORE_REQUEST_SCHEMA,
        "runtime_root": str(runtime_root),
        **_scope(
            goal_id=goal_id,
            agent_id=agent_id,
            surface=surface,
            state_key=state_key,
        ),
    }
    if state is not None:
        params["state"] = state
    try:
        result = effect_runtime_result(f"scheduler.state.{operation}", params)
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if (
        not isinstance(result, Mapping)
        or result.get("schema_version") != SCHEDULER_STATE_STORE_RESULT_SCHEMA
        or result.get("operation") != operation
        or not isinstance(result.get("path"), str)
    ):
        raise RuntimeError("TypeScript scheduler state store result shape mismatch")
    return result


def load_scheduler_state(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str | None,
    surface: str = CODEX_APP_SURFACE,
    state_key: str = CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
) -> dict[str, Any] | None:
    if not agent_id:
        return None
    result = _store_result(
        "load",
        runtime_root,
        state=None,
        goal_id=goal_id,
        agent_id=agent_id,
        surface=surface,
        state_key=state_key,
    )
    state = result.get("state")
    if state is not None and not isinstance(state, dict):
        raise RuntimeError("TypeScript loaded scheduler state must be an object or null")
    return state


def load_app_automation_scheduler_state(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str | None,
    surface: str,
) -> dict[str, Any] | None:
    current = load_scheduler_state(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        surface=surface,
        state_key=APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
    )
    if current is not None or surface != CODEX_APP_SURFACE:
        return current
    # Codex alone reads its pre-app_automation key. Follow-ups retain that
    # validated scope; copying its nonzero progression into a missing common
    # state would incorrectly turn an acknowledged continuation into a reset.
    return load_scheduler_state(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        surface=surface,
        state_key=LEGACY_CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
    )


def write_scheduler_state(
    runtime_root: Path,
    state: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str,
    surface: str = CODEX_APP_SURFACE,
    state_key: str = CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
) -> Path:
    result = _store_result(
        "write",
        runtime_root,
        state=state,
        goal_id=goal_id,
        agent_id=agent_id,
        surface=surface,
        state_key=state_key,
    )
    return Path(str(result["path"]))
