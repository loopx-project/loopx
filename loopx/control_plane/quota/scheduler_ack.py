from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..effect_runtime import _node_executable
from ..runtime.time import now_local_iso
from ..scheduler.state import (
    APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
    CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
    CODEX_APP_SURFACE,
    normalize_scheduler_rrule,
)
from ..todos.contract import normalize_todo_claimed_by

QUOTA_SCHEDULER_ACK_CLASSIFICATION = "quota_scheduler_ack"
QUOTA_SCHEDULER_FAILURE_CLASSIFICATION = "quota_scheduler_host_update_failure"
_FOLLOWUP_REQUEST_SCHEMA = "loopx_scheduler_host_followup_request_v0"
_HOST_FACTS_SCHEMA = "loopx_scheduler_heartbeat_host_facts_v0"


def _scheduler_packet(
    before: dict[str, Any],
    *,
    surface: str,
    state_key: str | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    scheduler_hint = (
        before.get("scheduler_hint")
        if isinstance(before.get("scheduler_hint"), dict)
        else {}
    )
    if surface == "trae_app":
        packet_key = "app_automation"
    elif (
        surface == CODEX_APP_SURFACE
        and (
            state_key == APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY
            or (state_key is None and isinstance(scheduler_hint.get("app_automation"), dict))
        )
    ):
        packet_key = "app_automation"
    else:
        packet_key = surface
    surface_packet = (
        scheduler_hint.get(packet_key)
        if isinstance(scheduler_hint.get(packet_key), dict)
        else {}
    )
    stateful_backoff = (
        surface_packet.get("stateful_backoff")
        if isinstance(surface_packet.get("stateful_backoff"), dict)
        else {}
    )
    return scheduler_hint, surface_packet, stateful_backoff


def _followup_state_key(
    before: dict[str, Any], *, surface: str, state_key: str | None,
) -> str:
    if state_key is not None:
        return state_key
    _, _, backoff = _scheduler_packet(before, surface=surface, state_key=None)
    return str(backoff.get("state_key") or CODEX_APP_STATEFUL_BACKOFF_STATE_KEY)


def _current_hint_identity(
    before: dict[str, Any],
    *,
    surface: str,
    state_key: str,
    applied_rrule: str | None,
    reset_token: str | None,
    identity_signature: str | None,
) -> tuple[str | None, str | None, str | None]:
    _, surface_packet, stateful_backoff = _scheduler_packet(
        before,
        surface=surface,
        state_key=state_key,
    )
    ack_hint = (
        surface_packet.get("ack_hint")
        if isinstance(surface_packet.get("ack_hint"), dict)
        else {}
    )
    ack_args = ack_hint.get("args") if isinstance(ack_hint.get("args"), dict) else {}
    return (
        str(applied_rrule or "").strip()
        or str(ack_args.get("applied_rrule") or "").strip()
        or str(surface_packet.get("recommended_rrule") or "").strip()
        or None,
        str(stateful_backoff.get("reset_token") or "").strip()
        or str(ack_args.get("reset_token") or "").strip()
        or str(reset_token or "").strip()
        or None,
        str(stateful_backoff.get("identity_signature") or "").strip()
        or str(ack_args.get("identity_signature") or "").strip()
        or str(identity_signature or "").strip()
        or None,
    )


def _host_facts(
    before: dict[str, Any],
    *,
    operation: str,
    goal_id: str,
    agent_id: str,
    surface: str,
    state_key: str,
    execute: bool,
    generated_at: str,
    applied_rrule: str | None = None,
    expected_rrule: str | None = None,
    observed_host_rrule: str | None = None,
    failure_kind: str | None = None,
    host_match_observed: bool = False,
) -> dict[str, Any]:
    scheduler_hint, surface_packet, stateful_backoff = _scheduler_packet(
        before,
        surface=surface,
        state_key=state_key,
    )
    if not stateful_backoff:
        raise ValueError(
            "current quota decision has no App automation stateful scheduler packet"
        )
    if str(stateful_backoff.get("state_key") or "") != state_key:
        raise ValueError("--state-key does not match the current scheduler hint")
    progression_minutes = stateful_backoff.get("progression_minutes")
    if not isinstance(progression_minutes, list) or not progression_minutes:
        progression_minutes = surface_packet.get("example_progression_minutes")
    if not isinstance(progression_minutes, list) or not progression_minutes:
        raise ValueError("current scheduler hint has no progression_minutes")
    reset_token = str(stateful_backoff.get("reset_token") or "").strip()
    identity_signature = str(stateful_backoff.get("identity_signature") or "").strip()
    if not reset_token or not identity_signature:
        raise ValueError("current scheduler hint has incomplete scheduler identity")
    current_rrule = normalize_scheduler_rrule(stateful_backoff.get("current_rrule"))
    prior_failures = stateful_backoff.get("host_update_failures")
    if not isinstance(prior_failures, list):
        prior_failures = []
    legacy_failure = stateful_backoff.get("host_update_failure")
    if isinstance(legacy_failure, dict) and legacy_failure not in prior_failures:
        prior_failures = [*prior_failures, legacy_failure]
    return {
        "schema_version": _HOST_FACTS_SCHEMA,
        "operation": operation,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "surface": surface,
        "state_key": state_key,
        "reset_token": reset_token,
        "identity_signature": identity_signature,
        "progression_index": stateful_backoff.get("progression_index", 0),
        "progression_minutes": progression_minutes,
        "expected_rrule": normalize_scheduler_rrule(expected_rrule) or current_rrule,
        "applied_rrule": normalize_scheduler_rrule(applied_rrule) or current_rrule,
        "observed_host_rrule": normalize_scheduler_rrule(observed_host_rrule),
        "cadence_class": str(scheduler_hint.get("cadence_class") or "default"),
        "stale_tolerance_minutes": 2,
        "generated_at": generated_at,
        "execute": execute,
        "ack_needed": stateful_backoff.get("ack_needed") is True,
        "apply_needed": stateful_backoff.get("apply_needed") is True,
        "source": (
            QUOTA_SCHEDULER_ACK_CLASSIFICATION
            if operation == "ack"
            else QUOTA_SCHEDULER_FAILURE_CLASSIFICATION
        ),
        "host_match_observed": host_match_observed,
        "failure_kind": failure_kind,
        "prior_host_update_failures": prior_failures,
    }


def _run_native_followup(
    *,
    runtime_root: Path,
    before: dict[str, Any],
    host_facts: dict[str, Any],
    use_current_hint: bool,
    reason_summary: str | None,
) -> dict[str, Any]:
    """Decision-free compatibility transport for in-process Python callers."""

    request = {
        "schema_version": _FOLLOWUP_REQUEST_SCHEMA,
        "runtime_root": str(runtime_root),
        "turn_instance_id": None,
        "require_heartbeat_receipt": False,
        "before": before,
        "host_facts": host_facts,
        "use_current_hint": use_current_hint,
        "reason_summary": str(reason_summary or "").strip() or None,
    }
    command = [
        _node_executable(),
        "--no-warnings",
        "--experimental-strip-types",
        str(
            Path(__file__).resolve().parents[1]
            / "scheduler"
            / "heartbeat_followup_cli.ts"
        ),
    ]
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("native scheduler follow-up command failed") from exc
    if not completed.stdout.strip():
        detail = (
            completed.stderr.strip() or "native scheduler follow-up returned no JSON"
        )
        raise RuntimeError(detail)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "native scheduler follow-up returned malformed JSON"
        ) from exc
    if not isinstance(result, dict):
        raise RuntimeError(  # noqa: TRY004 - this is an external protocol failure
            "native scheduler follow-up result must be an object"
        )
    if isinstance(result.get("ok"), bool):
        return result
    error = result.get("error")
    message = (
        error.get("message")
        if isinstance(error, dict)
        else result.get("message") or result.get("reason")
    )
    raise ValueError(str(message or "native scheduler follow-up rejected"))


def _failure_payload(
    *,
    mode: str,
    before: dict[str, Any],
    goal_id: str,
    agent_id: str | None,
    execute: bool,
    surface: str,
    state_key: str,
    rrule: str | None,
    reason: str,
    use_current_hint: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "mode": mode,
        "dry_run": not execute,
        "goal_id": goal_id,
        "agent_id": normalize_todo_claimed_by(agent_id),
        "surface": surface,
        "state_key": state_key,
        "appended": False,
        "registry_mutated": False,
        "reason": reason,
        "before": before,
        "after": None,
    }
    if mode == "scheduler-fail-current":
        payload["failed_rrule"] = rrule
    else:
        payload["applied_rrule"] = rrule
        if use_current_hint:
            payload["used_current_hint"] = True
            payload["current_hint_source"] = "quota.should-run.scheduler_hint"
    return payload


def record_quota_scheduler_ack_for_decision(
    before: dict[str, Any],
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
    execute: bool = False,
    surface: str = CODEX_APP_SURFACE,
    state_key: str | None = None,
    applied_rrule: str | None = None,
    reset_token: str | None = None,
    identity_signature: str | None = None,
    reason_summary: str | None = None,
    generated_at: str | None = None,
    use_current_hint: bool = False,
    host_match_observed: bool = False,
) -> dict[str, Any]:
    state_key = _followup_state_key(before, surface=surface, state_key=state_key)
    safe_agent_id = normalize_todo_claimed_by(agent_id)
    if host_match_observed and (
        not str(applied_rrule or "").strip()
        or not str(reset_token or "").strip()
        or not str(identity_signature or "").strip()
    ):
        return _failure_payload(
            mode="scheduler-ack",
            before=before,
            goal_id=goal_id,
            agent_id=safe_agent_id,
            execute=execute,
            surface=surface,
            state_key=state_key,
            rrule=applied_rrule,
            reason=(
                "host-match scheduler ACK requires applied RRULE, reset token, "
                "and identity signature"
            ),
            use_current_hint=use_current_hint,
        )
    if use_current_hint and not host_match_observed:
        applied_rrule, reset_token, identity_signature = _current_hint_identity(
            before,
            surface=surface,
            state_key=state_key,
            applied_rrule=applied_rrule,
            reset_token=reset_token,
            identity_signature=identity_signature,
        )
    try:
        if not safe_agent_id:
            raise ValueError("`loopx quota scheduler-ack` requires --agent-id")
        _, _, stateful_backoff = _scheduler_packet(
            before, surface=surface, state_key=state_key
        )
        if reset_token and str(reset_token).strip() != str(
            stateful_backoff.get("reset_token") or ""
        ):
            raise ValueError("--reset-token does not match the current scheduler hint")
        if identity_signature and str(identity_signature).strip() != str(
            stateful_backoff.get("identity_signature") or ""
        ):
            raise ValueError(
                "--identity-signature does not match the current scheduler hint"
            )
        facts = _host_facts(
            before,
            operation="ack",
            goal_id=goal_id,
            agent_id=safe_agent_id,
            surface=surface,
            state_key=state_key,
            execute=execute,
            generated_at=generated_at or now_local_iso(),
            applied_rrule=applied_rrule,
            host_match_observed=host_match_observed,
        )
        return _run_native_followup(
            runtime_root=runtime_root,
            before=before,
            host_facts=facts,
            use_current_hint=use_current_hint,
            reason_summary=reason_summary,
        )
    except ValueError as exc:
        return _failure_payload(
            mode="scheduler-ack",
            before=before,
            goal_id=goal_id,
            agent_id=safe_agent_id,
            execute=execute,
            surface=surface,
            state_key=state_key,
            rrule=normalize_scheduler_rrule(applied_rrule),
            reason=str(exc),
            use_current_hint=use_current_hint,
        )


def record_quota_scheduler_failure_for_decision(
    before: dict[str, Any],
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
    execute: bool = False,
    surface: str = CODEX_APP_SURFACE,
    state_key: str | None = None,
    failed_rrule: str | None = None,
    observed_host_rrule: str | None = None,
    failure_kind: str = "host_tool_failure",
    generated_at: str | None = None,
) -> dict[str, Any]:
    state_key = _followup_state_key(before, surface=surface, state_key=state_key)
    safe_agent_id = normalize_todo_claimed_by(agent_id)
    target_rrule = normalize_scheduler_rrule(failed_rrule)
    try:
        if not safe_agent_id:
            raise ValueError(
                "quota scheduler-fail-current requires a scoped --agent-id"
            )
        _, surface_packet, stateful_backoff = _scheduler_packet(
            before,
            surface=surface,
            state_key=state_key,
        )
        target_rrule = normalize_scheduler_rrule(
            target_rrule
            or surface_packet.get("recommended_rrule")
            or stateful_backoff.get("current_rrule")
        )
        host_observation = (
            stateful_backoff.get("host_observation")
            if isinstance(stateful_backoff.get("host_observation"), dict)
            else {}
        )
        observed_rrule = normalize_scheduler_rrule(
            observed_host_rrule or host_observation.get("current_rrule")
        )
        facts = _host_facts(
            before,
            operation="host_failure",
            goal_id=goal_id,
            agent_id=safe_agent_id,
            surface=surface,
            state_key=state_key,
            execute=execute,
            generated_at=generated_at or now_local_iso(),
            applied_rrule=observed_rrule,
            expected_rrule=target_rrule,
            observed_host_rrule=observed_rrule,
            failure_kind=str(failure_kind or "host_tool_failure").strip(),
        )
        return _run_native_followup(
            runtime_root=runtime_root,
            before=before,
            host_facts=facts,
            use_current_hint=False,
            reason_summary=None,
        )
    except ValueError as exc:
        return _failure_payload(
            mode="scheduler-fail-current",
            before=before,
            goal_id=goal_id,
            agent_id=safe_agent_id,
            execute=execute,
            surface=surface,
            state_key=state_key,
            rrule=target_rrule,
            reason=str(exc),
        )
