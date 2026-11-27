"""Read-only scheduler hint projection and bounded native transport adapter."""
from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import asdict
import json
from typing import TypedDict, cast
import zlib

from ...command_invocation import loopx_cli_argv as loopx_cli_argv
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result as effect_runtime_result
from ..quota.decision_summary import compact_quota_decision
from ..runtime.time import (
    now_utc as now_utc,
    parse_timestamp as parse_timestamp,
    utc_isoformat as utc_isoformat,
)
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


class SchedulerHostFollowupGoalRef(TypedDict):
    """Exact Goal identity scope encoded in transport-only scheduler follow-up args."""

    goal_id: str
    goal_instance_id: str


class SchedulerTimestampFact(TypedDict):
    """Normalized timestamp observation relative to the current scheduler instant."""

    iso: str
    utc: str
    after_seconds: float

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
_MONITOR_ITEM_FIELDS = (
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
_FOLLOWUP_STRING_FIELDS = (
    "goal_id",
    "agent_id",
    "automation_id",
    "turn_instance_id",
    "reset_token",
    "identity_signature",
    "surface",
    "state_key",
    "after",
)


def _scheduler_host_followup_goal_ref(
    goal_ref: Mapping[str, object] | None,
) -> SchedulerHostFollowupGoalRef | None:
    """Carry one exact Goal instance as transport data, never as an authority claim."""
    if not isinstance(goal_ref, Mapping):
        return None
    goal_id = str(goal_ref.get("goal_id") or "").strip()
    goal_instance_id = str(goal_ref.get("goal_instance_id") or "").strip()
    if not goal_id or not goal_instance_id:
        return None
    return {"goal_id": goal_id, "goal_instance_id": goal_instance_id}


def _scheduler_host_followup_transport_args(
    scheduler_host_facts: Mapping[str, object] | None,
    *,
    before: Mapping[str, object] | None,
    use_current_hint: bool,
    goal_ref: Mapping[str, object] | None = None,
) -> list[str]:
    """Encode a bounded, transport-only native scheduler follow-up hint."""
    if not isinstance(scheduler_host_facts, Mapping):
        return []
    payload = {
        "schema_version": "loopx_scheduler_host_followup_hint_v0",
        "before": compact_quota_decision(dict(before or {})),
        "use_current_hint": use_current_hint,
        "goal_ref": _scheduler_host_followup_goal_ref(goal_ref),
        "host_facts": dict(scheduler_host_facts),
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.b64encode(zlib.compress(raw, level=9)).decode("ascii").rstrip("=")
    if len(encoded) > SCHEDULER_HOST_FACTS_MAX_ENCODED_CHARS:
        raise ValueError("scheduler host facts exceed the native CLI transport bound")
    return [
        entry
        for index in range(0, len(encoded), SCHEDULER_HOST_FACTS_CHUNK_CHARS)
        for entry in (
            SCHEDULER_HOST_FACTS_CHUNK_FLAG,
            encoded[index : index + SCHEDULER_HOST_FACTS_CHUNK_CHARS],
        )
    ]


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
    packet: dict[str, object],
    before: Mapping[str, object] | None,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if "cli_args" not in packet:
        return packet
    raw_cli_args = packet["cli_args"]
    if not isinstance(raw_cli_args, list):
        raise RuntimeError("TypeScript scheduler CLI arguments must be a list")
    args: list[str] = []
    native_args: list[str] = []
    for argument in raw_cli_args:
        if isinstance(argument, str):
            args.append(argument)
        elif (
            isinstance(argument, dict)
            and argument.get("kind") == "scheduler_host_followup"
            and isinstance(argument.get("host_facts"), dict)
            and type(argument.get("use_current_hint")) is bool
        ):
            encoded = _scheduler_host_followup_transport_args(
                cast(Mapping[str, object], argument["host_facts"]),
                before=before,
                use_current_hint=cast(bool, argument["use_current_hint"]),
                goal_ref=goal_ref,
            )
            args.extend(encoded)
            native_args.extend(encoded)
        else:
            raise RuntimeError("TypeScript scheduler CLI argument shape mismatch")
    route = loopx_cli_argv()
    if len(route) > 1:
        args = [*route[1:], *args]
    return {
        **packet,
        **({"cli_bin": route[0]} if len(route) > 1 else {}),
        "cli_args": _bounded_scheduler_followup_cli_args(
            args, native_args=native_args
        ),
    }


def _finish_transport(
    packet: dict[str, object],
    before: Mapping[str, object] | None,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, object]:
    completed = _encode_followup_args(packet, before, goal_ref)
    # Source metadata can also contain cli_args. Only these owned wire slots are executable hints.
    app = completed.get("app_automation")
    if isinstance(app, dict):
        encoded_app = dict(app)
        for followup in ("ack_hint", "failure_hint"):
            command = app.get(followup)
            if isinstance(command, dict):
                encoded_app[followup] = _encode_followup_args(
                    cast(dict[str, object], command), before, goal_ref
                )
        completed["app_automation"] = encoded_app
    return completed


def _project_hint(
    operation: str,
    params: Mapping[str, object],
    before: Mapping[str, object] | None = None,
    *,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, object]:
    request: dict[str, object] = {"operation": operation, **params}
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
    return _finish_transport(
        cast(dict[str, object], projected["hint"]), before, goal_ref
    )


def _identity_json(payload: Mapping[str, object]) -> dict[str, str]:
    encoded: dict[str, str] = {}
    for path in (*SCHEDULER_IDENTITY_KEYS, *SCHEDULER_FRONTIER_IDENTITY_KEYS):
        leaf: object = payload
        for part in path.split("."):
            leaf = leaf.get(part) if isinstance(leaf, dict) else None
        encoded[path] = json.dumps(leaf, ensure_ascii=True, sort_keys=True, default=str)
    return encoded


def _timestamp_facts(
    observations: Sequence[object], current_time: object
) -> dict[str, SchedulerTimestampFact]:
    from datetime import datetime

    if not isinstance(current_time, datetime):
        raise TypeError("current_time must be a datetime")
    facts: dict[str, SchedulerTimestampFact] = {}
    seen_texts: set[str] = set()
    for raw in observations:
        if not raw:
            continue
        text = str(raw)
        if text in seen_texts:
            continue
        seen_texts.add(text)
        parsed = parse_timestamp(raw)
        if parsed is not None:
            facts[text] = {
                "iso": parsed.isoformat(),
                "utc": utc_isoformat(parsed),
                "after_seconds": (parsed - current_time).total_seconds(),
            }
    return facts


def _timestamp_observations(
    facts: Mapping[str, object],
    deadlines: Sequence[dict[str, object] | None],
    state: object,
) -> list[object]:
    raw_summary = facts.get("agent_todo_summary")
    summary = raw_summary if isinstance(raw_summary, Mapping) else {}
    observations: list[object] = [
        deadline.get("next_due_at")
        for deadline in deadlines
        if isinstance(deadline, dict)
    ]
    for lane in ("current_agent_claimed_monitor_items", "monitor_open_items"):
        raw_monitors = summary.get(lane)
        if isinstance(raw_monitors, Sequence) and not isinstance(
            raw_monitors, (str, bytes)
        ):
            for monitor in raw_monitors:
                if isinstance(monitor, Mapping):
                    for field in ("next_due_at", "expires_at", "last_checked_at"):
                        observations.append(monitor.get(field))
    frontier = summary.get("frontier_deadline")
    retry_sources = (
        summary.get("blocked_retry_wait"),
        frontier.get("blocked_retry") if isinstance(frontier, dict) else None,
    )
    observations.extend(
        retry.get("due_at") for retry in retry_sources if isinstance(retry, dict)
    )
    if isinstance(state, dict):
        raw_failures = state.get("host_update_failures")
        failures: list[object] = (
            list(raw_failures) if isinstance(raw_failures, list) else []
        )
        failures.append(state.get("host_update_failure"))
        observations.extend(
            failure.get("failed_at")
            for failure in failures
            if isinstance(failure, dict)
        )
    return observations


def _fields(source: object, names: tuple[str, ...]) -> dict[str, object]:
    return (
        {name: source.get(name) for name in names} if isinstance(source, dict) else {}
    )


def _capability_values(source: object) -> list[str] | None:
    if source is None:
        return None
    return (
        [str(capability or "").strip() for capability in source]
        if isinstance(source, (list, tuple, set))
        else []
    )


def _projection_facts(payload: Mapping[str, object]) -> dict[str, object]:
    base = _fields(
        payload, ("goal_id", "effective_action", "decision", "automation_cadence")
    )
    goal_id = base.get("goal_id")
    agent_identity = _fields(payload.get("agent_identity"), ("agent_id",))
    agent = agent_identity.get("agent_id")
    agent_identity["agent_id"] = str(agent) if agent else None
    capability_gate = _fields(payload.get("capability_gate"), ("available",))
    available = capability_gate.get("available")
    capability_gate["available"] = (
        _capability_values(available) if isinstance(available, list) else []
    )
    contract = _fields(payload.get("interaction_contract"), ("agent_channel",))
    summary = payload.get("agent_todo_summary")
    projected_summary = _fields(summary, ("blocked_retry_wait", "frontier_deadline"))
    for lane in ("current_agent_claimed_monitor_items", "monitor_open_items"):
        observations = summary.get(lane) if isinstance(summary, dict) else None
        projected_summary[lane] = (
            [
                _fields(monitor, _MONITOR_ITEM_FIELDS)
                for monitor in observations
                if isinstance(monitor, dict)
            ]
            if isinstance(observations, list)
            else []
        )
    return {
        "goal_id": str(goal_id) if goal_id else None,
        "effective_action": base.get("effective_action"),
        "decision": base.get("decision"),
        "automation_cadence": base.get("automation_cadence"),
        "agent_identity": agent_identity,
        "heartbeat_recommendation": _fields(
            payload.get("heartbeat_recommendation"), ("recommended_mode", "spend_policy")
        ),
        "automation_liveness": _fields(
            payload.get("automation_liveness"), ("spend_policy",)
        ),
        "execution_obligation": _fields(
            payload.get("execution_obligation"), ("spend_policy",)
        ),
        "capability_gate": capability_gate,
        "automation_cadence_readback": _fields(
            payload.get("automation_cadence_readback"), ("eligibility",)
        ),
        "interaction_contract": {
            "agent_channel": _fields(
                contract.get("agent_channel"), ("delivery_allowed",)
            )
        },
        "agent_todo_summary": projected_summary,
    }


def build_scheduler_hint(
    payload: Mapping[str, object],
    *,
    user_action_required: bool = False,
    arbitration: SchedulerArbitration | None = None,
    include_detail: bool = False,
    codex_app_scheduler_state: Mapping[str, object] | None = None,
    available_capabilities: object = None,
    codex_app_current_rrule: object = None,
    codex_app_automation_id: object = None,
    scheduler_execution_context: Mapping[str, object]
    | SchedulerExecutionContextResolution
    | None = None,
) -> dict[str, object]:
    """Read-only projection; the typed owner decides the complete scheduler hint."""
    del user_action_required
    context = resolve_scheduler_execution_context(scheduler_execution_context)
    current_time = now_utc()
    # Todo summaries retain their existing source owner. These are observations,
    # supplied before the one hint request, with no callback or second reduction.
    deadlines: list[dict[str, object] | None] = [
        todo_summary_frontier_deadline(payload.get(key), current_time=current_time)
        for key in ("agent_todo_summary", "user_todo_summary")
    ]
    facts = _projection_facts(payload)
    raw_goal_ref = payload.get("goal_ref")
    goal_ref = cast(Mapping[str, object], raw_goal_ref) if isinstance(raw_goal_ref, Mapping) else None
    params: dict[str, object] = {
        "payload": facts,
        "identity_json": _identity_json(payload),
        "context": context.projection(),
        "arbitration": asdict(arbitration) if arbitration is not None else None,
        "include_detail": bool(include_detail),
        "scheduler_state": dict(codex_app_scheduler_state)
        if isinstance(codex_app_scheduler_state, Mapping)
        else codex_app_scheduler_state,
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
    return _project_hint("hint", params, payload, goal_ref=goal_ref)


def _followup(operation: str, kwargs: dict[str, object]) -> dict[str, object]:
    raw_before = kwargs.pop("scheduler_before", None)
    before = (
        cast(Mapping[str, object], raw_before)
        if isinstance(raw_before, Mapping)
        else None
    )
    raw_goal_ref = kwargs.pop("goal_ref", None)
    goal_ref = (
        cast(Mapping[str, object], raw_goal_ref)
        if isinstance(raw_goal_ref, Mapping)
        else None
    )
    if "available_capabilities" in kwargs:
        kwargs["available_capabilities"] = _capability_values(
            kwargs["available_capabilities"]
        )
    for field in _FOLLOWUP_STRING_FIELDS:
        if field in kwargs:
            kwargs[field] = str(kwargs[field] or "").strip()
    if "host_match_observed" in kwargs:
        kwargs["host_match_observed"] = bool(kwargs["host_match_observed"])
    return _project_hint(operation, kwargs, before, goal_ref=goal_ref)


def build_app_automation_scheduler_ack_hint(
    *,
    goal_id: object,
    agent_id: object,
    applied_rrule: object,
    reset_token: object,
    identity_signature: object,
    available_capabilities: object = None,
    after: str = "automation_update_rrule_success",
    host_match_observed: bool = False,
    surface: str = CODEX_APP_SURFACE,
    state_key: str = APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
    scheduler_host_facts: Mapping[str, object] | None = None,
    scheduler_before: Mapping[str, object] | None = None,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the post-success follow-up scheduler hint payload."""
    return _followup(
        "ack",
        {
            "goal_id": goal_id,
            "agent_id": agent_id,
            "applied_rrule": applied_rrule,
            "reset_token": reset_token,
            "identity_signature": identity_signature,
            "available_capabilities": available_capabilities,
            "after": after,
            "host_match_observed": host_match_observed,
            "surface": surface,
            "state_key": state_key,
            "scheduler_host_facts": scheduler_host_facts,
            "scheduler_before": scheduler_before,
            "goal_ref": goal_ref,
        },
    )


def build_app_automation_scheduler_failure_hint(
    *,
    goal_id: object,
    agent_id: object,
    failed_rrule: object,
    observed_host_rrule: object = None,
    available_capabilities: object = None,
    scheduler_host_facts: Mapping[str, object] | None = None,
    scheduler_before: Mapping[str, object] | None = None,
    surface: str = CODEX_APP_SURFACE,
    state_key: str = APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the failure follow-up scheduler hint payload."""
    return _followup(
        "failure",
        {
            "goal_id": goal_id,
            "agent_id": agent_id,
            "failed_rrule": failed_rrule,
            "observed_host_rrule": observed_host_rrule,
            "available_capabilities": available_capabilities,
            "scheduler_host_facts": scheduler_host_facts,
            "scheduler_before": scheduler_before,
            "surface": surface,
            "state_key": state_key,
            "goal_ref": goal_ref,
        },
    )
