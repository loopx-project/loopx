from __future__ import annotations

import base64
import json
import zlib
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..quota.decision_summary import compact_quota_decision
from ..runtime.time import now_utc, utc_isoformat, parse_timestamp
from ..todos.frontier_deadline import todo_summary_frontier_deadline
from .arbitration import SchedulerArbitration
from .execution_context import (
    SchedulerExecutionContextResolution,
    resolve_scheduler_execution_context,
)
from .monitor_wait import (
    MONITOR_WAIT_PHASE_RANK as MONITOR_WAIT_PHASE_RANK,
    MonitorWaitPhase as MonitorWaitPhase,
)
from .state import APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY, CODEX_APP_SURFACE


SCHEDULER_HOST_FACTS_CHUNK_FLAG = "--scheduler-host-facts-chunk"
SCHEDULER_HOST_FACTS_CHUNK_CHARS = 384
SCHEDULER_HOST_FACTS_MAX_ENCODED_CHARS = 1_536
SCHEDULER_EXECUTABLE_CLI_ARGS_MAX_ITEMS = 64
SCHEDULER_EXECUTABLE_CLI_ARGS_MAX_TOTAL_CHARS = 8_192
SCHEDULER_BASE_IDENTITY_KEYS = (
    "goal_id",
    "agent_identity.agent_id",
    "effective_action",
    "heartbeat_recommendation.recommended_mode",
    "interaction_contract.mode",
)
SCHEDULER_FRONTIER_IDENTITY_KEYS = (
    "selected_todo.todo_id",
    "selected_todo.action_kind",
    "selected_todo.target_key",
    "selected_todo.claimed_by",
    "selected_todo.capability_binding_ref",
)
SCHEDULER_IDENTITY_KEYS = (
    *SCHEDULER_BASE_IDENTITY_KEYS,
    "recommended_action",
)


def _scheduler_host_followup_transport_args(
    scheduler_host_facts: Mapping[str, Any] | None,
    *,
    before: Mapping[str, Any] | None,
    use_current_hint: bool,
) -> list[str]:
    """Encode a bounded, transport-only native scheduler follow-up hint."""

    if not isinstance(scheduler_host_facts, Mapping):
        return []
    payload = {
        "schema_version": "loopx_scheduler_host_followup_hint_v0",
        "before": compact_quota_decision(dict(before or {})),
        "use_current_hint": use_current_hint,
        "host_facts": dict(scheduler_host_facts),
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.b64encode(zlib.compress(raw, level=9)).decode("ascii")
    encoded = encoded.rstrip("=")
    if len(encoded) > SCHEDULER_HOST_FACTS_MAX_ENCODED_CHARS:
        raise ValueError("scheduler host facts exceed the native CLI transport bound")
    result: list[str] = []
    for index in range(0, len(encoded), SCHEDULER_HOST_FACTS_CHUNK_CHARS):
        chunk = encoded[index : index + SCHEDULER_HOST_FACTS_CHUNK_CHARS]
        result.extend([SCHEDULER_HOST_FACTS_CHUNK_FLAG, chunk])
    return result


def _bounded_scheduler_followup_cli_args(
    cli_args: list[str],
    *,
    native_args: list[str],
) -> list[str]:
    if not native_args:
        return cli_args
    if (
        len(cli_args) <= SCHEDULER_EXECUTABLE_CLI_ARGS_MAX_ITEMS
        and sum(map(len, cli_args)) <= SCHEDULER_EXECUTABLE_CLI_ARGS_MAX_TOTAL_CHARS
    ):
        return cli_args
    raise ValueError(
        "native scheduler follow-up CLI arguments exceed the transport bound"
    )


def _encode_followup_args(
    packet: dict[str, Any], before: Mapping[str, Any] | None
) -> dict[str, Any]:
    if "cli_args" not in packet:
        return packet
    args: list[str] = []
    native_args: list[str] = []
    if not isinstance(packet["cli_args"], list):
        raise RuntimeError("TypeScript scheduler CLI arguments must be a list")
    for argument in packet["cli_args"]:
        if isinstance(argument, str):
            args.append(argument)
        elif (
            isinstance(argument, dict)
            and argument.get("kind") == "scheduler_host_followup"
            and isinstance(argument.get("host_facts"), dict)
            and type(argument.get("use_current_hint")) is bool
        ):
            encoded = _scheduler_host_followup_transport_args(
                argument["host_facts"],
                before=before,
                use_current_hint=argument["use_current_hint"],
            )
            args.extend(encoded)
            native_args.extend(encoded)
        else:
            raise RuntimeError("TypeScript scheduler CLI argument shape mismatch")
    return {
        **packet,
        "cli_args": _bounded_scheduler_followup_cli_args(args, native_args=native_args),
    }


def _finish_transport(
    packet: dict[str, Any], before: Mapping[str, Any] | None
) -> dict[str, Any]:
    completed = _encode_followup_args(packet, before)
    # Source metadata can also contain cli_args. Only these owned wire slots are executable hints.
    for view in ("app_automation", "codex_app"):
        app = completed.get(view)
        if not isinstance(app, dict):
            continue
        encoded_app = dict(app)
        for followup in ("ack_hint", "failure_hint"):
            command = app.get(followup)
            if isinstance(command, dict):
                encoded_app[followup] = _encode_followup_args(command, before)
        completed[view] = encoded_app
    return completed


def _project_hint(
    operation: str, params: dict[str, Any], before: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    request = {"operation": operation, **params}
    large_snapshot = (
        len(json.dumps(request, ensure_ascii=True).encode("utf-8")) > 64 * 1024
    )
    try:
        projected = effect_runtime_result(
            "scheduler.hint.project", request, large_local_snapshot=large_snapshot
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if (
        not isinstance(projected, dict)
        or projected.get("schema_version") != "scheduler_hint_projection_v0"
        or not isinstance(projected.get("hint"), dict)
    ):
        raise RuntimeError("TypeScript scheduler hint projection shape mismatch")
    return _finish_transport(projected["hint"], before)


def _identity_json(payload: dict[str, Any]) -> dict[str, str]:
    encoded = {}
    for path in (*SCHEDULER_IDENTITY_KEYS, *SCHEDULER_FRONTIER_IDENTITY_KEYS):
        leaf: Any = payload
        for part in path.split("."):
            leaf = leaf.get(part) if isinstance(leaf, dict) else None
        encoded[path] = json.dumps(leaf, ensure_ascii=True, sort_keys=True, default=str)
    return encoded


def _timestamp_facts(observations: list[Any], current_time: Any) -> dict[str, Any]:
    facts = {}
    for raw in observations:
        parsed = parse_timestamp(raw)
        if parsed is not None:
            facts[str(raw)] = {
                "iso": parsed.isoformat(),
                "utc": utc_isoformat(parsed),
                "after_seconds": (parsed - current_time).total_seconds(),
            }
    return facts


def _timestamp_observations(
    facts: dict[str, Any], deadlines: list[Any], state: Any
) -> list[Any]:
    summary = facts["agent_todo_summary"]
    observations = [
        deadline.get("next_due_at")
        for deadline in deadlines
        if isinstance(deadline, dict)
    ]
    for lane in ("current_agent_claimed_monitor_items", "monitor_open_items"):
        observations.extend(
            monitor.get(field)
            for monitor in summary[lane]
            for field in ("next_due_at", "expires_at", "last_checked_at")
        )
    frontier = summary.get("frontier_deadline")
    retry_sources = [
        summary.get("blocked_retry_wait"),
        frontier.get("blocked_retry") if isinstance(frontier, dict) else None,
    ]
    observations.extend(
        retry.get("due_at") for retry in retry_sources if isinstance(retry, dict)
    )
    if isinstance(state, dict):
        failures = state.get("host_update_failures")
        failures = list(failures) if isinstance(failures, list) else []
        failures.append(state.get("host_update_failure"))
        observations.extend(
            failure.get("failed_at")
            for failure in failures
            if isinstance(failure, dict)
        )
    return observations


def _fields(source: Any, names: tuple[str, ...]) -> dict[str, Any]:
    return (
        {name: source.get(name) for name in names} if isinstance(source, dict) else {}
    )


def _capability_values(source: Any) -> list[str] | None:
    if source is None:
        return None
    return (
        [str(capability or "").strip() for capability in source]
        if isinstance(source, (list, tuple, set))
        else []
    )


def _projection_facts(payload: dict[str, Any]) -> dict[str, Any]:
    facts = _fields(
        payload, ("goal_id", "effective_action", "decision", "automation_cadence")
    )
    for key, names in (
        ("agent_identity", ("agent_id",)),
        ("heartbeat_recommendation", ("recommended_mode", "spend_policy")),
        ("automation_liveness", ("spend_policy",)),
        ("execution_obligation", ("spend_policy",)),
        ("capability_gate", ("available",)),
        ("automation_cadence_readback", ("eligibility",)),
    ):
        facts[key] = _fields(payload.get(key), names)
    facts["goal_id"] = str(facts["goal_id"]) if facts["goal_id"] else None
    agent = facts["agent_identity"].get("agent_id")
    facts["agent_identity"]["agent_id"] = str(agent) if agent else None
    available = facts["capability_gate"].get("available")
    facts["capability_gate"]["available"] = (
        _capability_values(available) if isinstance(available, list) else []
    )
    contract = _fields(payload.get("interaction_contract"), ("agent_channel",))
    facts["interaction_contract"] = {
        "agent_channel": _fields(contract.get("agent_channel"), ("delivery_allowed",))
    }
    summary = payload.get("agent_todo_summary")
    projected_summary = _fields(summary, ("blocked_retry_wait", "frontier_deadline"))
    monitor_fields = (
        "todo_id",
        "target_key",
        "action_kind",
        "title",
        "index",
        "cadence",
        "next_due_at",
        "expires_at",
        "last_checked_at",
    )
    for lane in ("current_agent_claimed_monitor_items", "monitor_open_items"):
        observations = summary.get(lane) if isinstance(summary, dict) else None
        projected_summary[lane] = (
            [
                _fields(monitor, monitor_fields)
                for monitor in observations
                if isinstance(monitor, dict)
            ]
            if isinstance(observations, list)
            else []
        )
    facts["agent_todo_summary"] = projected_summary
    return facts


def build_scheduler_hint(
    payload: dict[str, Any],
    *,
    user_action_required: bool = False,
    arbitration: SchedulerArbitration | None = None,
    include_detail: bool = False,
    codex_app_scheduler_state: dict[str, Any] | None = None,
    available_capabilities: Any = None,
    codex_app_current_rrule: Any = None,
    codex_app_automation_id: Any = None,
    scheduler_execution_context: Mapping[str, Any]
    | SchedulerExecutionContextResolution
    | None = None,
) -> dict[str, Any]:
    """Read-only projection; the typed owner decides the complete scheduler hint."""
    context = resolve_scheduler_execution_context(scheduler_execution_context)
    current_time = now_utc()
    # Todo summaries retain their existing source owner. These are observations,
    # supplied before the one hint request, with no callback or second reduction.
    deadlines = [
        todo_summary_frontier_deadline(payload.get(key), current_time=current_time)
        for key in ("agent_todo_summary", "user_todo_summary")
    ]
    facts = _projection_facts(payload)
    params = {
        "payload": facts,
        "identity_json": _identity_json(payload),
        "context": context.projection(),
        "arbitration": asdict(arbitration) if arbitration is not None else None,
        "include_detail": bool(include_detail),
        "scheduler_state": codex_app_scheduler_state,
        "available_capabilities": _capability_values(available_capabilities),
        "observed_host_rrule": codex_app_current_rrule,
        "automation_id": codex_app_automation_id,
        "current_time": current_time.isoformat(),
        "generated_at": utc_isoformat(current_time),
        "frontier_deadlines": deadlines,
        "timestamps": _timestamp_facts(
            _timestamp_observations(facts, deadlines, codex_app_scheduler_state),
            current_time,
        ),
    }
    return _project_hint("hint", params, payload)


def _followup(operation: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    before = kwargs.pop("scheduler_before", None)
    if "available_capabilities" in kwargs:
        kwargs["available_capabilities"] = _capability_values(
            kwargs["available_capabilities"]
        )
    for field in (
        "goal_id",
        "agent_id",
        "automation_id",
        "turn_instance_id",
        "reset_token",
        "identity_signature",
        "surface",
        "state_key",
        "after",
    ):
        if field in kwargs:
            kwargs[field] = str(kwargs[field] or "").strip()
    if "host_match_observed" in kwargs:
        kwargs["host_match_observed"] = bool(kwargs["host_match_observed"])
    return _project_hint(operation, kwargs, before)


def build_app_automation_scheduler_ack_hint(
    *,
    goal_id: Any,
    agent_id: Any,
    applied_rrule: Any,
    reset_token: Any,
    identity_signature: Any,
    available_capabilities: Any = None,
    after: str = "automation_update_rrule_success",
    host_match_observed: bool = False,
    surface: str = CODEX_APP_SURFACE,
    state_key: str = APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
    scheduler_host_facts: Mapping[str, Any] | None = None,
    scheduler_before: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _followup("ack", dict(locals()))


def build_app_automation_scheduler_failure_hint(
    *,
    goal_id: Any,
    agent_id: Any,
    failed_rrule: Any,
    observed_host_rrule: Any = None,
    available_capabilities: Any = None,
    scheduler_host_facts: Mapping[str, Any] | None = None,
    scheduler_before: Mapping[str, Any] | None = None,
    surface: str = CODEX_APP_SURFACE,
    state_key: str = APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
) -> dict[str, Any]:
    return _followup("failure", dict(locals()))


def build_codex_app_scheduler_ack_hint(**kwargs: Any) -> dict[str, Any]:
    return _followup("legacy_ack", kwargs)


def build_codex_app_scheduler_failure_hint(**kwargs: Any) -> dict[str, Any]:
    return _followup("legacy_failure", kwargs)


def build_codex_app_scheduler_fallback_hint(
    *,
    goal_id: Any,
    agent_id: Any,
    automation_id: Any,
    turn_instance_id: Any = "${LOOPX_TURN:?}",
) -> dict[str, Any]:
    return _followup("fallback", dict(locals()))
