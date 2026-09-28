from __future__ import annotations

import base64
import hashlib
import json
import re
import zlib
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ..quota.decision_summary import compact_quota_decision
from ..effect_runtime import effect_runtime_result
from ..runtime.time import now_utc, utc_isoformat
from ..todos.frontier_deadline import build_frontier_recheck_plan
from .arbitration import (
    SchedulerArbitration,
    SchedulerDisposition,
    build_scheduler_arbitration,
)
from .app_automation_compat import (
    CODEX_APP_SCHEDULER_ACK_HINT_SCHEMA_VERSION,
    CODEX_APP_SCHEDULER_FAILURE_HINT_SCHEMA_VERSION,
    build_codex_app_compatibility_projection,
)
from .execution_context import (
    SchedulerExecutionContextResolution,
    SchedulerOwner,
    SchedulerRuntimeProfile,
    apply_scheduler_execution_context,
    resolve_scheduler_execution_context,
)
from .monitor_wait import (
    MONITOR_WAIT_HOST_FLOOR_MINUTES,
    MONITOR_WAIT_PHASE_RANK,  # noqa: F401  # re-exported for compatibility
    MONITOR_WAIT_PROGRESSION_MINUTES,  # noqa: F401  # re-exported for compatibility
    MonitorWaitPhase,  # noqa: F401  # re-exported for compatibility
    _parse_monitor_timestamp,  # noqa: F401  # re-exported for compatibility
    build_monitor_wait_cadence_plan,
)
from .state import (
    APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
    CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
    CODEX_APP_SURFACE,
    normalize_scheduler_rrule,
    rrule_for_minutes,
    scheduler_rrule_interval_minutes,
)
from .state_transition_rules import decide_scheduler_backoff_state
from .time import parse_scheduler_timestamp

SCHEDULER_HINT_SCHEMA_VERSION = "scheduler_hint_v0"
SCHEDULER_RESET_POLICY_SCHEMA_VERSION = "scheduler_reset_policy_v0"
SCHEDULER_HINT_DETAIL_SCHEMA_VERSION = "scheduler_hint_detail_v0"
APP_AUTOMATION_STATEFUL_BACKOFF_SCHEMA_VERSION = (
    "app_automation_stateful_backoff_v0"
)
APP_AUTOMATION_SCHEDULER_ACK_HINT_SCHEMA_VERSION = (
    "app_automation_scheduler_ack_hint_v0"
)
APP_AUTOMATION_SCHEDULER_FAILURE_HINT_SCHEMA_VERSION = (
    "app_automation_scheduler_failure_hint_v0"
)
CODEX_APP_SCHEDULER_FALLBACK_HINT_SCHEMA_VERSION = (
    "codex_app_scheduler_fallback_hint_v0"
)
USER_GATE_NOTIFICATION_COOLDOWN_SCHEMA_VERSION = "user_gate_notification_cooldown_v0"
CODEX_APP_MAX_INTERVAL_MINUTES = 60
DEFAULT_ACK_CAPABILITIES = {"shell", "filesystem_read", "filesystem_write"}
SCHEDULER_HOST_FACTS_CHUNK_FLAG = "--scheduler-host-facts-chunk"
SCHEDULER_HOST_FACTS_CHUNK_CHARS = 384
SCHEDULER_HOST_FACTS_MAX_ENCODED_CHARS = 1_536
SCHEDULER_EXECUTABLE_CLI_ARGS_MAX_ITEMS = 64
SCHEDULER_EXECUTABLE_CLI_ARGS_MAX_TOTAL_CHARS = 8_192
SCHEDULER_ACK_STALE_HINT_TOLERANCE_MINUTES = 2
FALLBACK_AUTOMATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SCHEDULER_BASE_IDENTITY_KEYS = (
    "goal_id",
    "agent_identity.agent_id",
    "effective_action",
    "heartbeat_recommendation.recommended_mode",
    "interaction_contract.mode",
)


def build_projected_codex_app_automation_id(
    *,
    goal_id: Any,
    agent_id: Any,
) -> str:
    """Build a deterministic Codex App automation id when none is installed yet.

    The fallback bridge needs a stable automation id to create the first
    heartbeat automation for a goal/agent pair. The id is derived from the
    same identity keys used by the scheduler so a later run can resolve it
    back to the installed automation without guessing.
    """

    safe_goal_id = str(goal_id or "").strip()
    safe_agent_id = str(agent_id or "").strip()
    raw = (
        f"loopx-{safe_goal_id}-{safe_agent_id}"
        if safe_agent_id
        else f"loopx-{safe_goal_id}"
    )
    projected = re.sub(r"[^A-Za-z0-9._:-]", "-", raw)
    projected = re.sub(r"-+", "-", projected).strip("-")
    return projected[:128] or "loopx-fallback"


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
MONITOR_WAIT_IDENTITY_KEYS = SCHEDULER_BASE_IDENTITY_KEYS
CODEX_APP_SSH_GOAL_RUNTIME_KEY = SchedulerRuntimeProfile.CODEX_APP_SSH_VISIBLE.value
CODEX_NATIVE_GOAL_BLOCK_ACTION = "update_goal_blocked_keep_loopx_active"
CODEX_NATIVE_GOAL_RESUME_TRIGGER = "explicit_codex_goal_resume"


def _stable_digest(value: Any, *, length: int) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _scheduler_profile_digest_snapshot(
    profile_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep the persisted v0 reset identity stable across the App rename.

    These legacy key names are part of the reset-token hash protocol, not the
    public scheduler projection. Changing them would reset an existing Codex
    App backoff even when the cadence semantics are unchanged.
    """

    key_aliases = {
        "app_automation_initial_interval_minutes": (
            "codex_app_initial_interval_minutes"
        ),
        "app_automation_initial_rrule": "codex_app_initial_rrule",
        "app_automation_max_interval_minutes": (
            "codex_app_max_interval_minutes"
        ),
        "app_automation_progression_minutes": (
            "codex_app_progression_minutes"
        ),
    }
    return {key_aliases.get(key, key): value for key, value in profile_snapshot.items()}


def _scheduler_reset_identity_digests(
    *,
    action: str,
    identity_snapshot: Mapping[str, Any],
    profile_snapshot: Mapping[str, Any],
    reset_profile_snapshot: Mapping[str, Any],
) -> tuple[str, str, str, str]:
    """Build the stable v0 reset token and its component signatures."""

    profile_digest_snapshot = _scheduler_profile_digest_snapshot(profile_snapshot)
    reset_profile_digest_snapshot = _scheduler_profile_digest_snapshot(
        reset_profile_snapshot
    )
    return (
        _stable_digest(
            {
                "action": action,
                "identity_snapshot": identity_snapshot,
                "profile_snapshot": reset_profile_digest_snapshot,
            },
            length=16,
        ),
        _stable_digest(identity_snapshot, length=12),
        _stable_digest(profile_digest_snapshot, length=12),
        _stable_digest(reset_profile_digest_snapshot, length=12),
    )


def _scheduler_identity_keys(
    *,
    cadence_class: str,
    execution_context: SchedulerExecutionContextResolution,
) -> tuple[str, ...]:
    base_keys = (
        MONITOR_WAIT_IDENTITY_KEYS
        if cadence_class == "monitor_wait"
        else SCHEDULER_IDENTITY_KEYS
    )
    context = execution_context.context if execution_context.ok else None
    if context is None or context.scheduler_owner is not SchedulerOwner.GOAL_RUNTIME:
        return base_keys
    if cadence_class == "monitor_wait":
        return (*base_keys, *SCHEDULER_FRONTIER_IDENTITY_KEYS)
    return (
        *base_keys[:-1],
        *SCHEDULER_FRONTIER_IDENTITY_KEYS,
        base_keys[-1],
    )


def _build_scheduler_stop_hint(
    *,
    execution_context: SchedulerExecutionContextResolution,
    action: str,
    cadence_class: str,
    reason_code: str,
    reason: str,
    spend_policy: str,
    resume_trigger: str,
    ssh_goal_runtime_action: str,
    unchanged_spend_policy: str,
) -> dict[str, Any]:
    return apply_scheduler_execution_context(
        {
            "schema_version": SCHEDULER_HINT_SCHEMA_VERSION,
            "source": "quota.should-run",
            "action": action,
            "cadence_class": cadence_class,
            "reason_code": reason_code,
            "reason": reason,
            "spend_policy": spend_policy,
            "app_automation": {
                "apply": "pause_or_delete_current_heartbeat_if_possible",
                "host_tool": "automation_update",
                "host_action": "pause_or_delete_current_heartbeat",
                "host_action_required": True,
                "attempt_limit": 1,
                "verify_host_result": True,
                "ack_required": False,
                "resume_trigger": resume_trigger,
                "no_spend_for_host_action": True,
            },
            "unchanged_poll": {
                "local_scheduler": "stop",
                "codex_cli_tui": "exit",
                CODEX_APP_SSH_GOAL_RUNTIME_KEY: ssh_goal_runtime_action,
                "claude_code_loop": "stop",
                "final_quota_replan_check_enabled": False,
                "spend_policy": unchanged_spend_policy,
            },
            "unchanged_identity_keys": list(
                _scheduler_identity_keys(
                    cadence_class=cadence_class,
                    execution_context=execution_context,
                )
            ),
        },
        execution_context,
    )


def _user_gate_notification_cooldown(
    *,
    cadence_class: str,
    host_failure_suppressed: bool,
    current_interval_minutes: int,
    effective_host_rrule: str,
    recorded_host_failure: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Bound repeat gate notices when a failed host update leaves a tight poll."""

    if cadence_class != "human_gate" or not host_failure_suppressed:
        return None
    failed_at = parse_scheduler_timestamp(
        (recorded_host_failure or {}).get("failed_at")
    )
    host_interval = scheduler_rrule_interval_minutes(effective_host_rrule)
    target_interval = max(1, int(current_interval_minutes))
    if failed_at is None or host_interval is None or host_interval >= target_interval:
        return None
    current_time = now_utc()
    elapsed_seconds = max(0.0, (current_time - failed_at).total_seconds())
    cooldown_seconds = target_interval * 60
    window_seconds = host_interval * 60
    cycle_index = int(elapsed_seconds // cooldown_seconds)
    cycle_position = elapsed_seconds - cycle_index * cooldown_seconds
    notification_due = cycle_index >= 1 and cycle_position < window_seconds
    next_reminder_at = failed_at + timedelta(
        seconds=(cycle_index + 1) * cooldown_seconds
    )
    return {
        "schema_version": USER_GATE_NOTIFICATION_COOLDOWN_SCHEMA_VERSION,
        "active": True,
        "notification_due": notification_due,
        "notification_suppressed": not notification_due,
        "policy": "failed_host_update_bounded_reminder_window",
        "cooldown_minutes": target_interval,
        "reminder_window_minutes": host_interval,
        "failed_at": utc_isoformat(failed_at),
        "next_reminder_at": utc_isoformat(next_reminder_at),
        "reason": (
            "the user gate is still pending, but the failed host cadence update "
            "left a tighter poll; suppress duplicate notices outside the bounded "
            "human-gate reminder window"
        ),
    }


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
    raise ValueError("native scheduler follow-up CLI arguments exceed the transport bound")


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
    safe_rrule = normalize_scheduler_rrule(applied_rrule)
    safe_goal_id = str(goal_id or "").strip()
    safe_agent_id = str(agent_id or "").strip()
    safe_surface = str(surface or "").strip()
    safe_state_key = str(state_key or "").strip()
    safe_reset_token = str(reset_token or "").strip()
    safe_identity_signature = str(identity_signature or "").strip()
    safe_available_capabilities: list[str] = []
    if isinstance(available_capabilities, (list, tuple, set)):
        for capability in available_capabilities:
            safe_capability = str(capability or "").strip()
            if (
                safe_capability
                and safe_capability not in DEFAULT_ACK_CAPABILITIES
                and safe_capability not in safe_available_capabilities
            ):
                safe_available_capabilities.append(safe_capability)
    runtime_alias = "--trae_app" if safe_surface == "trae_app" else "-A"
    cli_args = [
        "quota",
        "scheduler-ack-current",
        "--goal-id",
        safe_goal_id,
        "--agent-id",
        safe_agent_id,
        runtime_alias,
    ]
    for capability in safe_available_capabilities:
        cli_args.extend(["--available-capability", capability])
    native_args = _scheduler_host_followup_transport_args(
        scheduler_host_facts,
        before=scheduler_before,
        use_current_hint=True,
    )
    cli_args.extend(native_args)
    if safe_surface != CODEX_APP_SURFACE:
        cli_args.extend(["--surface", safe_surface])
    if safe_state_key != CODEX_APP_STATEFUL_BACKOFF_STATE_KEY:
        cli_args.extend(["--state-key", safe_state_key])
    cli_args.extend(["--applied-rrule", safe_rrule])
    if host_match_observed:
        cli_args.extend(
            [
                "--host-match-observed",
                "--reset-token",
                safe_reset_token,
                "--identity-signature",
                safe_identity_signature,
            ]
        )
    cli_args.append("--execute")
    cli_args = _bounded_scheduler_followup_cli_args(cli_args, native_args=native_args)
    args = {
        "goal_id": safe_goal_id,
        "agent_id": safe_agent_id,
        "surface": safe_surface,
        "state_key": safe_state_key,
        "applied_rrule": safe_rrule,
        "reset_token": safe_reset_token,
        "identity_signature": safe_identity_signature,
    }
    if safe_available_capabilities:
        args["available_capabilities"] = safe_available_capabilities
    if host_match_observed:
        args["host_match_observed"] = True
    return {
        "schema_version": APP_AUTOMATION_SCHEDULER_ACK_HINT_SCHEMA_VERSION,
        "after": str(after or "automation_update_rrule_success").strip(),
        "command": "quota scheduler-ack-current",
        "execute": True,
        "cli_args": cli_args,
        "args": args,
        "uses_current_hint": True,
        "no_spend": True,
    }


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
    safe_goal_id = str(goal_id or "").strip()
    safe_agent_id = str(agent_id or "").strip()
    safe_rrule = normalize_scheduler_rrule(failed_rrule)
    safe_observed_rrule = normalize_scheduler_rrule(observed_host_rrule)
    safe_capabilities: list[str] = []
    if isinstance(available_capabilities, (list, tuple, set)):
        for capability in available_capabilities:
            safe_capability = str(capability or "").strip()
            if (
                safe_capability
                and safe_capability not in DEFAULT_ACK_CAPABILITIES
                and safe_capability not in safe_capabilities
            ):
                safe_capabilities.append(safe_capability)
    safe_surface = str(surface or "").strip()
    safe_state_key = str(state_key or "").strip()
    runtime_alias = "--trae_app" if safe_surface == "trae_app" else "-A"
    cli_args = [
        "quota",
        "scheduler-fail-current",
        "--goal-id",
        safe_goal_id,
        "--agent-id",
        safe_agent_id,
        runtime_alias,
    ]
    for capability in safe_capabilities:
        cli_args.extend(["--available-capability", capability])
    native_args = _scheduler_host_followup_transport_args(
        scheduler_host_facts,
        before=scheduler_before,
        use_current_hint=False,
    )
    cli_args.extend(native_args)
    if safe_surface != CODEX_APP_SURFACE:
        cli_args.extend(["--surface", safe_surface])
    if safe_state_key != CODEX_APP_STATEFUL_BACKOFF_STATE_KEY:
        cli_args.extend(["--state-key", safe_state_key])
    cli_args.extend(
        [
            "--failed-rrule",
            safe_rrule,
        ]
    )
    if safe_observed_rrule:
        cli_args.extend(["--app-automation-current-rrule", safe_observed_rrule])
    cli_args.append("--execute")
    cli_args = _bounded_scheduler_followup_cli_args(cli_args, native_args=native_args)
    return {
        "schema_version": APP_AUTOMATION_SCHEDULER_FAILURE_HINT_SCHEMA_VERSION,
        "cli_args": cli_args,
    }


def build_codex_app_scheduler_ack_hint(**kwargs: Any) -> dict[str, Any]:
    """Build the historical Codex packet while the neutral packet coexists."""

    kwargs.setdefault("surface", CODEX_APP_SURFACE)
    kwargs.setdefault("state_key", CODEX_APP_STATEFUL_BACKOFF_STATE_KEY)
    hint = build_app_automation_scheduler_ack_hint(**kwargs)
    hint["schema_version"] = CODEX_APP_SCHEDULER_ACK_HINT_SCHEMA_VERSION
    return hint


def build_codex_app_scheduler_failure_hint(**kwargs: Any) -> dict[str, Any]:
    """Build the historical Codex packet while the neutral packet coexists."""

    kwargs.setdefault("surface", CODEX_APP_SURFACE)
    kwargs.setdefault("state_key", CODEX_APP_STATEFUL_BACKOFF_STATE_KEY)
    hint = build_app_automation_scheduler_failure_hint(**kwargs)
    hint["schema_version"] = CODEX_APP_SCHEDULER_FAILURE_HINT_SCHEMA_VERSION
    cli_args = hint.get("cli_args")
    if isinstance(cli_args, list):
        hint["cli_args"] = [
            "--codex-app-current-rrule"
            if item == "--app-automation-current-rrule"
            else item
            for item in cli_args
        ]
    return hint


def build_codex_app_scheduler_fallback_hint(
    *,
    goal_id: Any,
    agent_id: Any,
    automation_id: Any,
    turn_instance_id: Any = "${LOOPX_TURN:?}",
) -> dict[str, Any]:
    """Project the bounded SQLite/TOML RRULE fallback for automation_update gaps.

    The fallback is a standalone host bridge (``loopx-apply-rrule``) that backs
    up ``codex-dev.db``, syncs the automation TOML and SQLite row, and runs the
    bound scheduler ACK. It directly edits the Codex App automation store,
    bypassing the app API, so it is projected only when the host tool is
    unavailable or failed and ``apply_needed=true`` - never as the routine path.
    When no automation is installed yet, a deterministic automation id is
    projected so the fallback bridge can create the first heartbeat automation.
    """

    safe_goal_id = str(goal_id or "").strip()
    safe_agent_id = str(agent_id or "").strip()
    safe_automation_id = str(automation_id or "").strip()
    safe_turn_instance_id = str(turn_instance_id or "").strip() or "${LOOPX_TURN:?}"
    projected_automation_id = False
    if not FALLBACK_AUTOMATION_ID_PATTERN.match(safe_automation_id):
        safe_automation_id = build_projected_codex_app_automation_id(
            goal_id=safe_goal_id,
            agent_id=safe_agent_id,
        )
        projected_automation_id = True
    cli_args = [
        "loopx-apply-rrule",
        "--goal-id",
        safe_goal_id,
        "--agent-id",
        safe_agent_id,
        "--automation-id",
        safe_automation_id,
        "--turn-instance-id",
        safe_turn_instance_id,
    ]
    return {
        "schema_version": CODEX_APP_SCHEDULER_FALLBACK_HINT_SCHEMA_VERSION,
        "available": True,
        "command": "loopx-apply-rrule",
        "after": "automation_update_unavailable_or_failed",
        "cli_args": cli_args,
        "args": {
            "goal_id": safe_goal_id,
            "agent_id": safe_agent_id,
            "automation_id": safe_automation_id,
            "turn_instance_id": safe_turn_instance_id,
        },
        "no_spend": True,
        "reason": (
            "fallback only when automation_update is unavailable or failed and "
            "apply_needed=true; loopx-apply-rrule directly updates the Codex App "
            "SQLite automation store (codex-dev.db) and TOML, which bypasses the "
            "app API - use only as a bounded fallback, never as the routine path; "
            "run the bound ack_hint after it succeeds"
            + (
                "; automation_id_projected: no installed heartbeat automation "
                "matched this goal/agent, so loopx-apply-rrule will create it "
                "before applying the rrule"
                if projected_automation_id
                else ""
            )
        ),
        **(
            {
                "automation_id_projected": True,
                "action": "create_then_apply_rrule_via_fallback",
            }
            if projected_automation_id
            else {}
        ),
    }


@dataclass(frozen=True)
class _SchedulerHintBuilder:
    payload: dict[str, Any]
    execution_context: SchedulerExecutionContextResolution
    arbitration: SchedulerArbitration
    spend_policy: Any
    scheduler_ack_capabilities: Any
    codex_app_scheduler_state: dict[str, Any] | None
    codex_app_current_rrule: Any
    codex_app_automation_id: Any
    include_detail: bool

    def _identity_value(self, path: str) -> Any:
        current: Any = self.payload
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    def _cadence_projections(
        self, interval: int, maximum: int, multiplier: int, override: list[int] | None,
    ) -> dict[str, Any]:
        """Keep legacy profile selection separate from the owner constraint projection."""
        local = override or [min(interval * multiplier**step, maximum) for step in range(3)]
        app_max = min(max(1, maximum), CODEX_APP_MAX_INTERVAL_MINUTES)
        app: list[int] = []
        for value in local:
            bounded = min(max(1, int(value)), app_max)
            if not app or app[-1] != bounded:
                app.append(bounded)
        projections = {"local": local, "app": app, "app_max": app_max, "local_max": maximum, "floor": 0}
        policy = _dict_or_empty(self.payload.get("automation_cadence"))
        floor = policy.get("min_interval_minutes")
        return effect_runtime_result("quota.automation_cadence.schedule", {
            **projections, "min_interval_minutes": floor,
        }) if floor else projections

    def build(
        self,
        *,
        action: str,
        cadence_class: str,
        reason: str,
        codex_interval: int,
        codex_max: int,
        cli_limit: int | None,
        claude_limit: int | None,
        multiplier: int = 2,
        cadence_progression_override: list[int] | None = None,
        reset_profile_snapshot_override: dict[str, Any] | None = None,
        cadence_context_detail: dict[str, Any] | None = None,
        notification_cooldown_interval_minutes: int | None = None,
        advance_same_identity: bool = True,
    ) -> dict[str, Any]:
        context = self.execution_context.context
        app_surface = (
            context.host_surface.value
            if context is not None and context.app_automation_applicable
            else CODEX_APP_SURFACE
        )
        # The TS store has already validated this scope. Preserve it through
        # both host projections, including a legacy-only Codex installation.
        app_state_key = (
            (self.codex_app_scheduler_state or {}).get("state_key")
            or APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY
        )
        cadence = self._cadence_projections(codex_interval, codex_max, multiplier, cadence_progression_override)
        local_cadence_progression, app_cadence_progression = cadence["local"], cadence["app"]
        app_host_max, codex_max, floor = cadence["app_max"], cadence["local_max"], cadence["floor"]
        app_initial_interval = app_cadence_progression[0]
        local_initial_interval = local_cadence_progression[0]
        final_replan_check = {
            "enabled": cli_limit is not None or claude_limit is not None,
            "trigger": "before_unchanged_poll_after_limit",
            "action": "rerun_quota_should_run_once",
            "if_changed": "follow_new_scheduler_hint",
            "if_run_now": "execute_new_quota_contract",
            "if_unchanged": "apply_after_limit_without_spend",
            "spend_policy": "no quota spend for final replan check or loop stop",
        }
        identity_keys = list(
            _scheduler_identity_keys(
                cadence_class=cadence_class,
                execution_context=self.execution_context,
            )
        )
        identity_snapshot = {key: self._identity_value(key) for key in identity_keys}
        app_rrule = rrule_for_minutes(app_initial_interval)
        profile_snapshot = {
            "cadence_class": cadence_class,
            "app_automation_initial_interval_minutes": app_initial_interval,
            "app_automation_initial_rrule": app_rrule,
            "app_automation_max_interval_minutes": app_host_max,
            "app_automation_progression_minutes": app_cadence_progression,
            "unchanged_poll_backoff_multiplier": multiplier,
            "local_scheduler_unchanged_poll_limit": cli_limit,
            "claude_code_loop_unchanged_poll_limit": claude_limit,
        }
        reset_profile_snapshot = reset_profile_snapshot_override or profile_snapshot
        (
            reset_token,
            identity_signature,
            profile_signature,
            reset_profile_signature,
        ) = _scheduler_reset_identity_digests(
            action=action,
            identity_snapshot=identity_snapshot,
            profile_snapshot=profile_snapshot,
            reset_profile_snapshot=reset_profile_snapshot,
        )
        reset_policy_detail = {
            "schema_version": SCHEDULER_RESET_POLICY_SCHEMA_VERSION,
            "source": "quota.should-run",
            "reset_to": "profile_initial_interval",
            "profile_action": action,
            "reset_token": reset_token,
            "host_state_key": "scheduler_hint.reset_policy.reset_token",
            "app_automation_initial_interval_minutes": app_initial_interval,
            "app_automation_initial_rrule": app_rrule,
            "local_scheduler_initial_interval_minutes": local_initial_interval,
            "clear_unchanged_poll_state": True,
            "identity_key_count": len(identity_keys),
            "identity_signature": identity_signature,
            "profile_signature": profile_signature,
            "reset_profile_signature": reset_profile_signature,
            "reset_condition_summary": "token_changed|user_feedback|new_or_reassigned_todo|gate_or_material_transition|active_work_projected",
            "after_reset": "apply_initial_interval_before_backoff",
            "app_automation_tool": "automation_update",
            "app_automation_apply": "call_automation_update_to_restore_initial_rrule_on_token_change",
            "no_spend_for_reset": True,
        }
        reset_policy = {
            "reset_token": reset_token,
            "host_state_key": "scheduler_hint.reset_policy.reset_token",
            "app_automation_initial_interval_minutes": app_initial_interval,
            "app_automation_initial_rrule": app_rrule,
            "identity_signature": identity_signature,
        }
        local_scheduler = {
            "recommended_interval_minutes": local_initial_interval,
            "max_interval_minutes": codex_max,
            "unchanged_poll_backoff_multiplier": multiplier,
            "example_progression_minutes": local_cadence_progression,
            "unchanged_poll_limit": cli_limit,
            "after_limit": "stop_tick_loop" if cli_limit is not None else "continue",
            "final_quota_replan_check": final_replan_check,
            "no_spend_for_cadence_change": True,
        }
        codex_goal_loop = {
            "unchanged_poll_limit": cli_limit,
            "after_limit": (
                CODEX_NATIVE_GOAL_BLOCK_ACTION if cli_limit is not None else "continue"
            ),
            "final_quota_replan_check": final_replan_check,
            "loopx_goal_state": "remains_active",
            "resume_trigger": CODEX_NATIVE_GOAL_RESUME_TRIGGER,
            "no_spend_for_block": True,
        }
        codex_cli_tui = {
            **codex_goal_loop,
            "no_spend_for_exit": True,
        }
        codex_app_ssh_goal = dict(codex_goal_loop)
        claude_code_loop = {
            "unchanged_poll_limit": claude_limit,
            "after_limit": "stop_loop" if claude_limit is not None else "continue",
            "final_quota_replan_check": final_replan_check,
            "no_spend_for_stop": True,
        }
        scheduler_state = _dict_or_empty(self.codex_app_scheduler_state)
        scheduler_now = now_utc()
        backoff_decision = decide_scheduler_backoff_state(
            app_cadence_progression,
            scheduler_state=scheduler_state,
            reset_token=reset_token,
            identity_signature=identity_signature,
            advance_same_identity=advance_same_identity,
            current_time=scheduler_now,
            observed_host_rrule=self.codex_app_current_rrule,
            cadence_class=cadence_class,
            stale_tolerance_minutes=SCHEDULER_ACK_STALE_HINT_TOLERANCE_MINUTES,
        )
        cadence_decision = backoff_decision.cadence
        host_decision = backoff_decision.host
        current_index = cadence_decision.current_index
        state_status = cadence_decision.state_status
        current_interval = backoff_decision.current_interval_minutes
        current_rrule = backoff_decision.current_rrule
        observed_host_rrule = backoff_decision.observed_host_rrule
        effective_host_rrule = backoff_decision.effective_host_rrule
        host_update_failures = list(backoff_decision.host_update_failures)
        recorded_host_failure = backoff_decision.recorded_host_failure
        current_rrule_already_applied = backoff_decision.current_rrule_already_applied
        apply_needed = host_decision.apply_needed
        ack_needed = host_decision.ack_needed
        host_match_ack_needed = host_decision.host_match_ack_needed
        host_failure_suppressed = host_decision.host_failure_suppressed
        if host_failure_suppressed:
            state_status = "host_update_failure_suppressed"
        stateful_backoff_detail = {
            "progression_minutes": app_cadence_progression,
            "current_interval_minutes": current_interval,
            "host_max_interval_minutes": app_host_max,
            "coarser_wait_fallback": "hold_affected_automation" if floor else "local_scheduler_only",
            "host_update_failure": "cache_recent_failed_target_and_observed_host_pairs_then_suppress_each_exact_repeat_until_host_changes_ack_or_expiry",
            "ack_required_after_apply": apply_needed,
            "ack_required_from_host_match": host_match_ack_needed,
            "persist": "reset_token|identity_signature|progression_index|last_applied_rrule|host_update_failures|host_update_failure_compat",
            "same_identity_action": (
                "advance_index_after_applied_interval_elapsed"
                if advance_same_identity
                else "keep_initial_interval_while_active_work"
            ),
            "reset_action": "clear_progression_index_apply_initial_rrule",
            "automation_update_scope": "rrule_only_preserve_body_name_status",
        }
        app_automation = {
            "recommended_interval_minutes": current_interval,
            "max_interval_minutes": app_host_max,
            "unchanged_poll_backoff_multiplier": multiplier,
            "example_progression_minutes": app_cadence_progression,
            "apply": (
                "update_automation_cadence_if_possible"
                if apply_needed
                else (
                    "none_recorded_host_failure"
                    if host_failure_suppressed
                    else "none_already_applied"
                )
            ),
            "host_tool": "automation_update",
            "host_action": (
                "update_current_heartbeat_rrule"
                if apply_needed
                else (
                    "none_recorded_host_failure"
                    if host_failure_suppressed
                    else "ack_observed_rrule_without_update"
                    if host_match_ack_needed
                    else "none"
                )
            ),
            "host_action_contract": (
                "automation_update_rrule_then_quota_scheduler_ack"
                if apply_needed
                else (
                    "skip_automation_update_for_recorded_host_failure"
                    if host_failure_suppressed
                    else "quota_scheduler_ack_from_matching_host_observation"
                    if host_match_ack_needed
                    else "skip_automation_update_when_apply_needed_false"
                )
            ),
            "rrule_source": (
                "scheduler_hint.app_automation.recommended_rrule"
                if apply_needed
                else None
            ),
            "stateful_backoff": {
                "schema_version": APP_AUTOMATION_STATEFUL_BACKOFF_SCHEMA_VERSION,
                "state_key": app_state_key,
                "identity_signature": identity_signature,
                "reset_token": reset_token,
                "progression_index": current_index,
                "current_rrule": current_rrule,
                "apply_needed": apply_needed,
                "ack_needed": ack_needed,
                "state_status": state_status,
            },
            "no_spend_for_cadence_change": True,
        }
        if floor:
            app_automation["execution_interval_policy"] = self.payload["automation_cadence"]
            app_automation["guarantee"] = cadence["guarantee"]
            if host_failure_suppressed:
                app_automation.update(host_action="pause_current_heartbeat", apply="pause_affected_automation")
        stateful_backoff = app_automation["stateful_backoff"]
        if host_update_failures:
            stateful_backoff["host_update_failures"] = [
                dict(failure) for failure in host_update_failures
            ]
        if recorded_host_failure:
            stateful_backoff["host_update_failure"] = dict(recorded_host_failure)
        if observed_host_rrule:
            stateful_backoff["host_observation"] = {
                "source": "quota_should_run_host_observation",
                "current_rrule": observed_host_rrule,
                "status": (
                    "matches_recommended"
                    if current_rrule_already_applied
                    else "drift_detected"
                ),
            }
        goal_id = self.payload.get("goal_id")
        agent_id = self._identity_value("agent_identity.agent_id")
        scheduler_host_facts = (
            {
                "schema_version": "loopx_scheduler_heartbeat_host_facts_v0",
                "goal_id": str(goal_id),
                "agent_id": str(agent_id),
                "surface": app_surface,
                "state_key": app_state_key,
                "reset_token": reset_token,
                "identity_signature": identity_signature,
                "progression_index": current_index,
                "progression_minutes": app_cadence_progression,
                "expected_rrule": current_rrule,
                "cadence_class": cadence_class,
                "stale_tolerance_minutes": SCHEDULER_ACK_STALE_HINT_TOLERANCE_MINUTES,
                "generated_at": utc_isoformat(scheduler_now),
                "ack_needed": ack_needed,
                "apply_needed": apply_needed,
            }
            if goal_id and agent_id
            else None
        )
        if apply_needed:
            app_automation["recommended_rrule"] = current_rrule
            if goal_id and agent_id:
                app_automation["failure_hint"] = build_app_automation_scheduler_failure_hint(
                    state_key=app_state_key,
                    goal_id=goal_id,
                    agent_id=agent_id,
                    failed_rrule=current_rrule,
                    observed_host_rrule=effective_host_rrule,
                    available_capabilities=self.scheduler_ack_capabilities,
                    scheduler_host_facts={
                        **(scheduler_host_facts or {}),
                        "operation": "host_failure",
                        "applied_rrule": effective_host_rrule,
                        "observed_host_rrule": effective_host_rrule,
                        "failure_kind": "host_tool_failure",
                        "source": "quota_scheduler_host_update_failure",
                        "host_match_observed": False,
                    },
                    scheduler_before=self.payload,
                    surface=app_surface,
                )
                if app_surface == CODEX_APP_SURFACE and not floor:
                    app_automation["fallback_hint"] = build_codex_app_scheduler_fallback_hint(
                        goal_id=goal_id,
                        agent_id=agent_id,
                        automation_id=self.codex_app_automation_id,
                    )
        if ack_needed and goal_id and agent_id:
            app_automation["ack_hint"] = build_app_automation_scheduler_ack_hint(
                state_key=app_state_key,
                goal_id=goal_id,
                agent_id=agent_id,
                applied_rrule=current_rrule,
                reset_token=reset_token,
                identity_signature=identity_signature,
                available_capabilities=self.scheduler_ack_capabilities,
                after=(
                    ("automation_update_then_readback_actual_rrule" if floor else "automation_update_rrule_success")
                    if apply_needed
                    else "matching_host_rrule_observed"
                ),
                # Bind the host proof to the originating identity.
                host_match_observed=True,
                scheduler_host_facts={
                    **(scheduler_host_facts or {}),
                    "operation": "ack",
                    "applied_rrule": current_rrule,
                    "observed_host_rrule": effective_host_rrule,
                    "source": "quota_scheduler_ack",
                    "host_match_observed": True,
                },
                scheduler_before=self.payload,
                surface=app_surface,
            )
        unchanged_poll_limits = {
            "local_scheduler": cli_limit,
            "codex_cli_tui": cli_limit,
            "claude_code_loop": claude_limit,
        }
        unchanged_poll_after_limits = {
            "local_scheduler": local_scheduler["after_limit"],
            "codex_cli_tui": codex_cli_tui["after_limit"],
            "claude_code_loop": claude_code_loop["after_limit"],
        }
        detail_contains = [
            "local_scheduler",
            "codex_cli_tui",
            "claude_code_loop",
            "final_quota_replan_check",
            "reset_policy_detail",
            "stateful_backoff_detail",
        ]
        if cli_limit is not None:
            unchanged_poll_limits[CODEX_APP_SSH_GOAL_RUNTIME_KEY] = cli_limit
            unchanged_poll_after_limits[CODEX_APP_SSH_GOAL_RUNTIME_KEY] = (
                codex_app_ssh_goal["after_limit"]
            )
            detail_contains.insert(2, CODEX_APP_SSH_GOAL_RUNTIME_KEY)
        scheduler_hint = {
            "schema_version": SCHEDULER_HINT_SCHEMA_VERSION,
            "source": "quota.should-run",
            "action": action,
            "cadence_class": cadence_class,
            "reason_code": self.arbitration.reason_code,
            "reason": reason,
            "spend_policy": self.spend_policy,
            "app_automation": app_automation,
            "unchanged_poll": {
                "limits": unchanged_poll_limits,
                "after_limits": unchanged_poll_after_limits,
                "final_quota_replan_check_enabled": final_replan_check["enabled"],
                "final_quota_replan_check_action": (
                    final_replan_check["action"]
                    if final_replan_check["enabled"]
                    else None
                ),
                "spend_policy": final_replan_check["spend_policy"],
            },
            "unchanged_identity_keys": identity_keys,
            "reset_policy": reset_policy,
            "detail_ref": {
                "schema_version": SCHEDULER_HINT_DETAIL_SCHEMA_VERSION,
                "omitted_by_default": True,
                "execution_required": False,
                "request": "loopx quota should-run --include-detail scheduler",
                "hot_path_runtime_fields": [
                    "app_automation",
                    "unchanged_poll",
                    "reset_policy",
                ],
                "contains": detail_contains,
            },
        }
        if app_surface == CODEX_APP_SURFACE:
            scheduler_hint["codex_app"] = build_codex_app_compatibility_projection(
                app_automation,
                goal_id=goal_id,
                agent_id=agent_id,
                available_capabilities=self.scheduler_ack_capabilities,
                scheduler_host_facts=scheduler_host_facts,
                observed_host_rrule=effective_host_rrule,
                scheduler_before=self.payload,
                automation_id=self.codex_app_automation_id,
                build_ack_hint=build_codex_app_scheduler_ack_hint,
                build_failure_hint=build_codex_app_scheduler_failure_hint,
                build_fallback_hint=build_codex_app_scheduler_fallback_hint,
            )
        notification_cooldown = _user_gate_notification_cooldown(
            cadence_class=cadence_class,
            host_failure_suppressed=host_failure_suppressed,
            current_interval_minutes=(
                notification_cooldown_interval_minutes
                if notification_cooldown_interval_minutes is not None
                else current_interval
            ),
            effective_host_rrule=effective_host_rrule,
            recorded_host_failure=recorded_host_failure,
        )
        if notification_cooldown:
            scheduler_hint["user_gate_notification_cooldown"] = notification_cooldown
        frontier_recheck = build_frontier_recheck_plan(
            self.payload,
            current_time=now_utc(),
        )
        if self.include_detail:
            scheduler_hint["cold_path_detail"] = {
                "schema_version": SCHEDULER_HINT_DETAIL_SCHEMA_VERSION,
                "source": "quota.should-run",
                "local_scheduler": local_scheduler,
                "codex_cli_tui": codex_cli_tui,
                CODEX_APP_SSH_GOAL_RUNTIME_KEY: codex_app_ssh_goal,
                "claude_code_loop": claude_code_loop,
                "final_quota_replan_check": final_replan_check,
                "reset_policy_detail": reset_policy_detail,
                "stateful_backoff_detail": stateful_backoff_detail,
            }
            if cadence_context_detail:
                scheduler_hint["cold_path_detail"]["cadence_context"] = (
                    cadence_context_detail
                )
            if frontier_recheck:
                scheduler_hint["cold_path_detail"]["frontier_recheck"] = (
                    frontier_recheck
                )
        return apply_scheduler_execution_context(
            scheduler_hint,
            self.execution_context,
            frontier_recheck_after_seconds=(
                frontier_recheck.get("frontier_recheck_after_seconds")
                if frontier_recheck
                else None
            ),
        )


def _monitor_bounded_wait_profile(
    payload: dict[str, Any],
    *,
    cadence_class: str,
    default_interval_minutes: int,
    max_interval_minutes: int,
) -> dict[str, Any]:
    """Cap a wait profile to the tightest continuous-monitor wakeup."""

    monitor_plan = build_monitor_wait_cadence_plan(
        payload,
        current_time=now_utc(),
    )
    monitor_progression = (
        monitor_plan.get("progression_minutes")
        if isinstance(monitor_plan, dict)
        else None
    )
    progression = (
        monitor_progression
        if isinstance(monitor_progression, list) and monitor_progression
        else None
    )
    initial_interval = int(progression[0]) if progression else default_interval_minutes
    monitor_reset_profile = (
        {
            "cadence_class": cadence_class,
            "app_automation_initial_interval_minutes": initial_interval,
            "app_automation_initial_rrule": rrule_for_minutes(initial_interval),
            "app_automation_max_interval_minutes": max_interval_minutes,
            "unchanged_poll_backoff_multiplier": 2,
            "local_scheduler_unchanged_poll_limit": 3,
            "claude_code_loop_unchanged_poll_limit": 3,
            **monitor_plan["reset_profile"],
        }
        if isinstance(monitor_plan, dict)
        and isinstance(monitor_plan.get("reset_profile"), dict)
        else None
    )
    return {
        "codex_interval": initial_interval,
        "codex_max": max_interval_minutes,
        "cadence_progression_override": progression,
        "reset_profile_snapshot_override": monitor_reset_profile,
        "cadence_context_detail": monitor_plan,
    }


def build_scheduler_hint(
    payload: dict[str, Any],
    *,
    user_action_required: bool = False,
    agent_scope_frontier_actions: Collection[str] = (),
    include_detail: bool = False,
    codex_app_scheduler_state: dict[str, Any] | None = None,
    available_capabilities: Any = None,
    codex_app_current_rrule: Any = None,
    codex_app_automation_id: Any = None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
) -> dict[str, Any]:
    """Project host-runtime cadence/backoff policy from a quota decision.

    This helper is intentionally pure: callers provide the few quota-local
    classification facts it needs, and it returns the public scheduler contract
    without reading files, mutating state, or depending on the full quota module.
    """

    execution_context = resolve_scheduler_execution_context(scheduler_execution_context)
    if not execution_context.ok:
        blocked_app_automation = {
            "applicability": "blocked_invalid_context",
            "apply": "none",
            "host_action": "none",
            "ack_required": False,
        }
        blocked_hint = {
            "schema_version": SCHEDULER_HINT_SCHEMA_VERSION,
            "source": "quota.should-run",
            "action": "repair_scheduler_execution_context",
            "cadence_class": "control_plane_repair",
            "reason_code": "invalid_scheduler_execution_context",
            "reason": (
                "scheduler ownership is missing or contradictory; repair the "
                "typed execution context before applying cadence"
            ),
            "spend_policy": "no quota spend for scheduler context repair",
            "execution_context": execution_context.projection(),
            "execution_phase": {
                "schema_version": "scheduler_execution_phase_v0",
                "disposition": "contract_error",
                "completed": False,
                "apply_needed": False,
                "ack_needed": False,
                "acknowledged": False,
            },
            "unchanged_poll": {
                "local_scheduler": "stop_until_context_repaired",
                "codex_cli_tui": "stop_until_context_repaired",
                CODEX_APP_SSH_GOAL_RUNTIME_KEY: "stop_until_context_repaired",
                "claude_code_loop": "stop_until_context_repaired",
                "final_quota_replan_check_enabled": False,
                "spend_policy": "no quota spend for scheduler context repair",
            },
            "consistency_error": {
                "source": "scheduler_execution_context",
                "errors": list(execution_context.errors),
            },
        }
        supplied_host_surface = str(
            (execution_context.supplied or {}).get("host_surface") or ""
        ).strip()
        if supplied_host_surface == "trae_app":
            blocked_hint["app_automation"] = blocked_app_automation
        else:
            blocked_hint["codex_app"] = blocked_app_automation
        return blocked_hint

    heartbeat_recommendation = _dict_or_empty(payload.get("heartbeat_recommendation"))
    pause_mode = str(heartbeat_recommendation.get("recommended_mode") or "")
    if pause_mode in {"goal_stopped", "quota_paused"}:
        goal_stopped = pause_mode == "goal_stopped"
        cadence_class = "goal_stopped" if goal_stopped else "quota_paused"
        resume_trigger = (
            "explicit Goal lifecycle resume"
            if goal_stopped
            else "explicit quota resume with quota.compute > 0"
        )
        return apply_scheduler_execution_context(
            {
                "schema_version": SCHEDULER_HINT_SCHEMA_VERSION,
                "source": "quota.should-run",
                "action": "stop_until_explicit_resume",
                "cadence_class": cadence_class,
                "reason_code": cadence_class,
                "reason": (
                    "Goal lifecycle is stopped by owner; recurring host automation "
                    "must stop until the Goal is explicitly resumed"
                    if goal_stopped
                    else "Goal-level compute quota is paused; recurring host automation "
                    "must stop until quota.compute is explicitly raised above 0"
                ),
                "spend_policy": (
                    "no quota spend for stopped-Goal automation shutdown"
                    if goal_stopped
                    else "no quota spend for paused automation shutdown"
                ),
                "app_automation": {
                    "apply": "pause_or_delete_current_heartbeat_if_possible",
                    "host_tool": "automation_update",
                    "host_action": "pause_or_delete_current_heartbeat",
                    "host_action_required": True,
                    "attempt_limit": 1,
                    "verify_host_result": True,
                    "ack_required": False,
                    "resume_trigger": resume_trigger,
                    "no_spend_for_host_action": True,
                },
                "unchanged_poll": {
                    "local_scheduler": "stop",
                    "codex_cli_tui": "exit",
                    CODEX_APP_SSH_GOAL_RUNTIME_KEY: "complete_host_goal",
                    "claude_code_loop": "stop",
                    "final_quota_replan_check_enabled": False,
                    "spend_policy": (
                        "no quota spend while the Goal lifecycle is stopped"
                        if goal_stopped
                        else "no quota spend while compute quota is paused"
                    ),
                },
                "unchanged_identity_keys": list(
                    _scheduler_identity_keys(
                        cadence_class=cadence_class,
                        execution_context=execution_context,
                    )
                ),
            },
            execution_context,
        )

    execution_obligation = _dict_or_empty(payload.get("execution_obligation"))
    automation_liveness = _dict_or_empty(payload.get("automation_liveness"))
    spend_policy = (
        automation_liveness.get("spend_policy")
        or execution_obligation.get("spend_policy")
        or heartbeat_recommendation.get("spend_policy")
    )
    capability_gate = _dict_or_empty(payload.get("capability_gate"))
    scheduler_ack_capabilities = (
        available_capabilities
        if available_capabilities is not None
        else capability_gate.get("available")
        if isinstance(capability_gate.get("available"), list)
        else []
    )
    agent_scope_action_set = {str(value) for value in agent_scope_frontier_actions}
    arbitration = build_scheduler_arbitration(
        payload,
        agent_scope_frontier_actions=agent_scope_action_set,
    )

    if arbitration.disposition == SchedulerDisposition.TERMINAL_STOP:
        return _build_scheduler_stop_hint(
            execution_context=execution_context,
            action="stop_until_explicit_resume",
            cadence_class="terminal_no_followup",
            reason_code=arbitration.reason_code,
            reason=(
                "validated closure evidence derives no-follow-up and confirms no "
                "remaining frontier; recurring polling must stop until resume"
            ),
            spend_policy="no quota spend for terminal automation shutdown",
            resume_trigger="explicit goal resume or newly projected work",
            ssh_goal_runtime_action="complete_host_goal",
            unchanged_spend_policy="no quota spend for terminal loop stop",
        )

    builder = _SchedulerHintBuilder(
        payload=payload,
        execution_context=execution_context,
        arbitration=arbitration,
        spend_policy=spend_policy,
        scheduler_ack_capabilities=scheduler_ack_capabilities,
        codex_app_scheduler_state=codex_app_scheduler_state,
        codex_app_current_rrule=codex_app_current_rrule,
        codex_app_automation_id=codex_app_automation_id,
        include_detail=include_detail,
    )
    if arbitration.disposition == SchedulerDisposition.PEER_COORDINATION_WAIT:
        return builder.build(
            action="backoff_until_reassigned",
            cadence_class="peer_coordination_wait",
            reason=(
                "explicit peer coordination has no executable peer lane or local "
                "fallback; keep a bounded no-spend observer because peer readiness, "
                "configuration, or the local frontier can change asynchronously"
            ),
            codex_interval=10,
            codex_max=60,
            cli_limit=3,
            claude_limit=3,
            cadence_progression_override=[10, 20, 30, 60],
        )
    if arbitration.disposition == SchedulerDisposition.AGENT_MONITOR_ONLY_WAIT:
        return builder.build(
            action="backoff_agent_monitor_only",
            cadence_class="agent_monitor_only",
            reason=(
                "agent monitor-only mode blocks advancement while a quiet poll keeps "
                "due monitors and verified direct replies responsive"
            ),
            codex_interval=15,
            codex_max=60,
            cli_limit=3,
            claude_limit=3,
        )

    if arbitration.disposition == SchedulerDisposition.CONSISTENCY_REPAIR:
        result = builder.build(
            action="repair_interaction_contract_projection",
            cadence_class="control_plane_repair",
            reason=(
                "scheduler inputs disagree with the final interaction contract; "
                "repair the projection before applying delivery or wait cadence"
            ),
            codex_interval=3,
            codex_max=10,
            cli_limit=None,
            claude_limit=None,
            advance_same_identity=False,
        )
        result["consistency_error"] = arbitration.consistency_error()
        return result

    if arbitration.disposition == SchedulerDisposition.HUMAN_GATE:
        human_gate_profile = _monitor_bounded_wait_profile(
            payload,
            cadence_class="human_gate",
            default_interval_minutes=30,
            max_interval_minutes=120,
        )
        return builder.build(
            action="backoff_waiting_for_user",
            cadence_class="human_gate",
            reason=(
                "user/controller action is the next unlock; surface the concrete "
                "gate once, then stop repeating the same quiet poll"
            ),
            cli_limit=3,
            claude_limit=3,
            notification_cooldown_interval_minutes=30,
            **human_gate_profile,
        )

    if arbitration.disposition == SchedulerDisposition.ACTIVE_WORK:
        interaction_contract = payload.get("interaction_contract")
        agent_channel = (
            interaction_contract.get("agent_channel")
            if isinstance(interaction_contract, Mapping)
            else None
        )
        capability_bridge_wait = (
            arbitration.mode == "capability_bridge_repair"
            and isinstance(agent_channel, Mapping)
            and agent_channel.get("delivery_allowed") is False
        )
        return builder.build(
            action="run_now",
            cadence_class="active_work",
            reason=(
                "the interaction contract requires an agent attempt; keep the active "
                "scheduler cadence until the turn validates or blocks"
            ),
            codex_interval=3,
            codex_max=10,
            cli_limit=None,
            claude_limit=None,
            advance_same_identity=capability_bridge_wait,
        )

    if arbitration.disposition == SchedulerDisposition.UNCHANGED_WAIT:
        return builder.build(
            action="backoff_until_fresh_evidence",
            cadence_class="unchanged_noop",
            reason=(
                "the current mapped or post-handoff source is unchanged; do not "
                "keep a tight loop while waiting for fresh evidence or a concrete handoff"
            ),
            codex_interval=60,
            codex_max=240,
            cli_limit=3,
            claude_limit=3,
        )

    if arbitration.disposition == SchedulerDisposition.AGENT_SCOPE_WAIT:
        return builder.build(
            action="backoff_until_reassigned",
            cadence_class="agent_scope_wait",
            reason=(
                "this registered agent has no in-scope advancement candidate; "
                "agent-to-agent handoffs may change quickly, so stay closer to "
                "the prior scheduler cadence while waiting for handoff owner "
                "progress, reassignment, or a current-agent todo"
            ),
            codex_interval=10,
            codex_max=60,
            cli_limit=3,
            claude_limit=3,
            cadence_progression_override=[10, 20, 30, 60],
        )

    if arbitration.disposition == SchedulerDisposition.MONITOR_WAIT:
        monitor_profile = _monitor_bounded_wait_profile(
            payload,
            cadence_class="monitor_wait",
            default_interval_minutes=MONITOR_WAIT_HOST_FLOOR_MINUTES,
            max_interval_minutes=60,
        )
        return builder.build(
            action="backoff_until_material_transition",
            cadence_class="monitor_wait",
            reason=(
                "monitor-only quiet polls should remain alive but use a slower "
                "cadence until material evidence, a blocker, or replan obligation appears"
            ),
            cli_limit=3,
            claude_limit=3,
            **monitor_profile,
        )

    if arbitration.disposition == SchedulerDisposition.QUIET_WAIT:
        return builder.build(
            action="backoff_until_state_change",
            cadence_class="quiet_wait",
            reason=(
                "quota blocks delivery and no immediate user/monitor-specific path "
                "is projected; poll at a slower cadence until the status changes"
            ),
            codex_interval=30,
            codex_max=120,
            cli_limit=3,
            claude_limit=3,
        )

    raise AssertionError(f"unhandled scheduler disposition: {arbitration.disposition}")
