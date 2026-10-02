"""Transport adapter for the TypeScript-owned host Todo transaction."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from enum import StrEnum
from typing import Any, Protocol

from .effect_program import SettlementIdentity
from .effect_runtime import EffectRuntimeRejected, effect_runtime_result
from .goals.vision_checkpoint import prepare_vision_refresh


HOST_ADAPTER_SETTLEMENT_SCHEMA_VERSION = "host_adapter_todo_settlement_v0"
HOST_TODO_COMPLETION_TRANSACTION_SCHEMA_VERSION = (
    "loopx_host_todo_completion_transaction_v0"
)
HOST_TODO_COMPLETION_REDUCTION_SCHEMA_VERSION = (
    "loopx_host_todo_completion_reduction_v0"
)
_RUNTIME_METHOD = "turn.host_todo_completion.evaluate"
_STEP_KINDS = (
    "guard",
    "lifecycle_completion",
    "durable_writeback",
    "quota_spend",
    "terminal_closeout",
)
_MISSING = object()


class HostGuardState(StrEnum):
    SELECTED = "selected"
    TERMINAL_NO_SELECTION = "terminal_no_selection"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class HostGuardSelection:
    state: HostGuardState
    todo_id: str | None = None
    reason: str | None = None
    settlement_identity: SettlementIdentity | None = None


@dataclass(frozen=True, slots=True)
class HostTodoSettlementRequest:
    goal_id: str
    agent_id: str
    todo_id: str
    runtime_profile: str
    legacy_host_surface: str
    scheduler_owner: str
    execution_mode: str
    completion_args: tuple[str, ...]
    no_follow_up: bool = False
    vision_path: str | None = None
    vision_unchanged_reason: str | None = None
    checkpoint_read_context_id: str | None = None
    goal_ref: Mapping[str, str] | None = None


class HostCliRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        *,
        legacy_args: list[str] | None = None,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class _ProviderStep:
    step_kind: str
    args: tuple[str, ...]
    legacy_args: tuple[str, ...] | None
    continue_when: Mapping[str, Any] | None


def _request_payload(
    request: HostTodoSettlementRequest,
    *,
    phase: str,
    provider_outcomes: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": HOST_TODO_COMPLETION_TRANSACTION_SCHEMA_VERSION,
        "phase": phase,
        "goal_id": request.goal_id,
        "agent_id": request.agent_id,
        "todo_id": request.todo_id,
        "runtime_profile": request.runtime_profile,
        "legacy_host_surface": request.legacy_host_surface,
        "scheduler_owner": request.scheduler_owner,
        "execution_mode": request.execution_mode,
        "completion_args": list(request.completion_args),
        "no_follow_up": request.no_follow_up,
    }
    if provider_outcomes is not None:
        payload["provider_outcomes"] = provider_outcomes
    if request.goal_ref is not None:
        payload["goal_ref"] = dict(request.goal_ref)
    if request.vision_path or request.vision_unchanged_reason or phase in {"vision_refresh", "vision_context"}:
        payload.update(
            schema_version="loopx_host_todo_completion_transaction_v1",
            vision_path=request.vision_path,
            vision_unchanged_reason=request.vision_unchanged_reason,
            checkpoint_read_context_id=request.checkpoint_read_context_id,
        )
    return payload


@contextmanager
def host_vision_request(request: HostTodoSettlementRequest, vision: dict | None, unchanged: str):
    """Materialize authored JSON for the existing CLI codec, never as public state."""
    if vision is not None and unchanged:
        raise ValueError("choose a vision patch or an unchanged reason, not both")
    if vision is not None and not isinstance(vision, dict):
        raise ValueError("agent_vision must be a JSON object")
    if vision is None:
        yield replace(request, vision_unchanged_reason=unchanged or None)
        return
    # Reject malformed, misbound or oversized authoring before lifecycle writes.
    # This is syntax/budget preflight only: refresh-state still validates against
    # the real baseline and current replan/settlement state at writeback time.
    prepare_vision_refresh(vision, goal_id=request.goal_id, agent_id=request.agent_id,
        existing_agent_vision=None, merge_patch=False, require_path_delta_for_durable_change=False)
    with tempfile.TemporaryDirectory(prefix="loopx-host-vision-") as directory:
        path = Path(directory) / "vision.json"
        path.write_text(json.dumps(vision, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        yield replace(request, vision_path=str(path))


def refresh_host_todo_vision(request: HostTodoSettlementRequest, *, run_cli: HostCliRunner) -> str:
    """Repair the original checkpoint; no lifecycle operation, new Turn or spend."""
    phase = "vision_refresh" if (
        request.vision_path or request.vision_unchanged_reason or request.checkpoint_read_context_id
    ) else "vision_context"
    plan = _runtime_reduction(_request_payload(request, phase=phase), phase=phase)
    _runtime_identity(plan.get("identity"))
    args = plan.get("args")
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise RuntimeError("TypeScript host vision command shape mismatch")
    return run_cli(args)


def project_host_interaction(output: str) -> str:
    """Keep admission facts; let the typed host lens choose the transport instructions."""
    try:
        packet = json.loads(output)
    except ValueError:
        return output
    if not isinstance(packet, dict):
        return output
    projection = _runtime_reduction({
        "schema_version": "loopx_host_todo_completion_transaction_v1",
        "phase": "project_guard", "packet": packet,
    }, phase="project_guard")
    if not isinstance(projection.get("packet"), dict):
        raise RuntimeError("TypeScript host interaction projection shape mismatch")
    return json.dumps(projection["packet"], ensure_ascii=False)


def _runtime_reduction(params: Mapping[str, Any], *, phase: str) -> dict[str, Any]:
    try:
        value = effect_runtime_result(_RUNTIME_METHOD, params)
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(value, Mapping):
        raise RuntimeError("TypeScript host Todo completion result must be an object")
    result = dict(value)
    if (
        result.get("schema_version") != HOST_TODO_COMPLETION_REDUCTION_SCHEMA_VERSION
        or result.get("phase") != phase
    ):
        raise RuntimeError("TypeScript host Todo completion result shape mismatch")
    return result


def _runtime_identity(value: Any) -> SettlementIdentity:
    try:
        return SettlementIdentity.from_runtime_payload(value)
    except (RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "TypeScript host Todo completion identity shape mismatch"
        ) from exc


def _prepare(request: HostTodoSettlementRequest) -> dict[str, Any]:
    result = _runtime_reduction(
        _request_payload(request, phase="prepare"),
        phase="prepare",
    )
    if (
        result.get("decision") != "execute"
        or not isinstance(result.get("identity"), Mapping)
        or not isinstance(result.get("provider_effect"), Mapping)
        or result.get("result") is not None
    ):
        raise RuntimeError("TypeScript host Todo completion prepare shape mismatch")
    _runtime_identity(result["identity"])
    return result


def host_adapter_turn_instance_id(request: HostTodoSettlementRequest) -> str:
    """Read the retry-stable host Turn identity from the TypeScript authority."""

    prepared = _prepare(request)
    identity = _runtime_identity(prepared["identity"])
    return identity.turn_instance_id


def classify_host_guard_snapshot(value: str) -> HostGuardSelection:
    """Project the TypeScript guard classification into the legacy host facade."""

    result = _runtime_reduction(
        {
            "schema_version": HOST_TODO_COMPLETION_TRANSACTION_SCHEMA_VERSION,
            "phase": "classify_guard",
            "guard_output": value,
        },
        phase="classify_guard",
    )
    selection = result.get("selection")
    if result.get("decision") != "complete" or not isinstance(selection, Mapping):
        raise RuntimeError("TypeScript host guard classification shape mismatch")
    try:
        state = HostGuardState(str(selection.get("state") or ""))
    except ValueError as exc:
        raise RuntimeError(
            "TypeScript host guard classification shape mismatch"
        ) from exc
    todo_id = selection.get("todo_id")
    reason = selection.get("reason")
    identity = selection.get("settlement_identity")
    if (
        not (todo_id is None or isinstance(todo_id, str))
        or not (reason is None or isinstance(reason, str))
        or not (identity is None or isinstance(identity, Mapping))
    ):
        raise RuntimeError("TypeScript host guard classification shape mismatch")
    return HostGuardSelection(
        state=state,
        todo_id=todo_id,
        reason=reason,
        settlement_identity=_runtime_identity(identity)
        if identity is not None
        else None,
    )


def _string_tuple(value: Any, *, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) for item in value)
    ):
        raise RuntimeError(f"TypeScript host provider {label} shape mismatch")
    return tuple(value)


def _decode_provider_steps(value: Any) -> tuple[_ProviderStep, ...]:
    if not isinstance(value, Mapping):
        raise RuntimeError("TypeScript host provider plan shape mismatch")
    raw_steps = value.get("steps")
    if (
        value.get("provider_id") != "loopx_cli"
        or value.get("kind") != "ordered_cli_sequence"
        or not isinstance(raw_steps, list)
        or len(raw_steps) not in {4, 5}
    ):
        raise RuntimeError("TypeScript host provider plan shape mismatch")
    expected = _STEP_KINDS[: len(raw_steps)]
    steps: list[_ProviderStep] = []
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, Mapping) or raw.get("step_kind") != expected[index]:
            raise RuntimeError("TypeScript host provider step order mismatch")
        legacy_value = raw.get("legacy_args")
        if legacy_value is not None and not isinstance(legacy_value, list):
            raise RuntimeError("TypeScript host provider legacy_args shape mismatch")
        condition = raw.get("continue_when")
        condition_required = index < len(raw_steps) - 1
        if condition_required != isinstance(condition, Mapping):
            raise RuntimeError("TypeScript host provider condition shape mismatch")
        steps.append(
            _ProviderStep(
                step_kind=expected[index],
                args=_string_tuple(raw.get("args"), label="args"),
                legacy_args=(
                    _string_tuple(legacy_value, label="legacy_args")
                    if legacy_value is not None
                    else None
                ),
                continue_when=dict(condition) if condition is not None else None,
            )
        )
    return tuple(steps)


def _path_value(payload: Mapping[str, Any], path: Any) -> Any:
    if not isinstance(path, list) or any(not isinstance(part, str) for part in path):
        raise RuntimeError("TypeScript host provider condition path shape mismatch")
    value: Any = payload
    for part in path:
        if not isinstance(value, Mapping) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if left is _MISSING:
        return False
    return type(left) is type(right) and left == right


def _condition_matches(
    payload: Mapping[str, Any], condition: Mapping[str, Any]
) -> bool:
    kind = condition.get("kind")
    if kind in {"all", "any"}:
        children = condition.get("conditions")
        if not isinstance(children, list) or any(
            not isinstance(child, Mapping) for child in children
        ):
            raise RuntimeError("TypeScript host provider condition shape mismatch")
        matches = [_condition_matches(payload, child) for child in children]
        return all(matches) if kind == "all" else any(matches)

    value = _path_value(payload, condition.get("path"))
    if kind == "equals":
        return _json_equal(value, condition.get("value"))
    if kind == "nullish":
        return value is _MISSING or value is None
    if kind == "object":
        return isinstance(value, Mapping)
    if kind == "not_object":
        return not isinstance(value, Mapping)
    if kind == "normalized_string_equals":
        normalization = condition.get("normalization")
        if normalization not in {"trim", "trim_lowercase"}:
            raise RuntimeError("TypeScript host provider normalization is unsupported")
        candidate = str(value or "").strip()
        if normalization == "trim_lowercase":
            candidate = candidate.lower()
        expected = condition.get("value")
        return isinstance(expected, str) and candidate == expected
    raise RuntimeError("TypeScript host provider condition kind is unsupported")


def _provider_condition_matches(output: str, condition: Mapping[str, Any]) -> bool:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, Mapping) and _condition_matches(payload, condition)


def _run_provider_plan(
    steps: tuple[_ProviderStep, ...],
    *,
    run_cli: HostCliRunner,
) -> list[dict[str, str]]:
    outcomes: list[dict[str, str]] = []
    for step in steps:
        output = run_cli(
            list(step.args),
            legacy_args=(list(step.legacy_args) if step.legacy_args else None),
        )
        outcomes.append({"step_kind": step.step_kind, "output": output})
        if step.continue_when is not None and not _provider_condition_matches(
            output, step.continue_when
        ):
            break
    return outcomes


def settle_host_todo_completion(
    request: HostTodoSettlementRequest,
    *,
    run_cli: HostCliRunner,
) -> str:
    """Execute one TypeScript-planned host provider sequence and finalize it."""

    prepared = _prepare(request)
    steps = _decode_provider_steps(prepared["provider_effect"])
    outcomes = _run_provider_plan(steps, run_cli=run_cli)
    finalized = _runtime_reduction(
        _request_payload(
            request,
            phase="finalize",
            provider_outcomes=outcomes,
        ),
        phase="finalize",
    )
    result = finalized.get("result")
    if (
        finalized.get("decision") not in {"complete", "blocked", "provider_result"}
        or finalized.get("provider_effect") is not None
        or not isinstance(finalized.get("identity"), Mapping)
        or not isinstance(result, Mapping)
        or dict(finalized["identity"]) != dict(prepared["identity"])
    ):
        raise RuntimeError("TypeScript host Todo completion finalize shape mismatch")
    return json.dumps(dict(result), ensure_ascii=False, sort_keys=True)
