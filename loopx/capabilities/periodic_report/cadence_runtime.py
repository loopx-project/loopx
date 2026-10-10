"""Calendar admission through the existing turn-start and intent surfaces."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shlex

from ...agent_registry import registered_agent_ids_for_goal
from ...control_plane.goals.activation import goal_is_stopped
from ...control_plane.capability_hooks import (
    TURN_START_HOOK_RESULT_SCHEMA_VERSION, TurnStartHookRegistration, dispatch_turn_start_hooks,
)
from ...history import load_registry
from ...registry import find_registry_goal
from .cadence_journal import admit_cadence_window, read_cadence_journal
from .machine_defaults import (
    resolve_goal_periodic_report_subscription, select_goal_periodic_report_executor,
)
from .machine_store import read_periodic_report_machine_defaults


def periodic_report_cadence_hooks(
    *, registry_path: Path, runtime_root: Path, goal_id: str, agent_id: str | None,
    now: datetime | None = None,
) -> tuple[TurnStartHookRegistration, ...]:
    if not agent_id:
        return ()
    goal = find_registry_goal(load_registry(registry_path), goal_id)
    if (
        not isinstance(goal, dict)
        or goal_is_stopped(goal)
        or goal.get("status") in {"stopped", "paused", "archived"}
    ):
        return ()
    subscription = resolve_goal_periodic_report_subscription(
        goal, read_periodic_report_machine_defaults(runtime_root))
    if subscription.get("enabled") is not True or subscription.get("schedule") is None:
        return ()
    # Prefer the frozen reporter for unfinished work. Registration reordering
    # must not make its recovery hook disappear or elect a second reporter.
    reporters = registered_agent_ids_for_goal(goal)
    if not reporters:
        return ()
    journal = read_cadence_journal(runtime_root=runtime_root, goal_id=goal_id)
    pending_reporter = (journal["window"]["agent_id"]
        if journal and journal["publication"] is None else reporters[0])
    election = select_goal_periodic_report_executor(
        reporting_agent_id=pending_reporter, eligible_agent_ids=reporters)
    if agent_id != election["selected_agent_id"]:
        return ()

    def produce():
        current_reporters = []

        def resolve_current_subscription():
            nonlocal current_reporters
            current_goal = find_registry_goal(load_registry(registry_path), goal_id)
            if (
                not isinstance(current_goal, dict)
                or goal_is_stopped(current_goal)
                or current_goal.get("status") in {"stopped", "paused", "archived"}
            ):
                return None
            current_reporters = registered_agent_ids_for_goal(current_goal)
            if not current_reporters or agent_id not in current_reporters:
                return None
            current_journal = read_cadence_journal(runtime_root=runtime_root, goal_id=goal_id)
            reporter = (current_journal["window"]["agent_id"]
                if current_journal and current_journal["publication"] is None else current_reporters[0])
            selected = select_goal_periodic_report_executor(
                reporting_agent_id=reporter, eligible_agent_ids=current_reporters)
            if selected["selected_agent_id"] != agent_id:
                return None
            return resolve_goal_periodic_report_subscription(
                current_goal, read_periodic_report_machine_defaults(runtime_root))

        admission = admit_cadence_window(runtime_root=runtime_root, goal_id=goal_id,
            agent_id=agent_id, subscription=subscription, now=now or datetime.now(timezone.utc),
            subscription_resolver=resolve_current_subscription)
        window = admission["window"]
        error_code = (
            "cadence_pending_reporter_unavailable" if window and window["agent_id"] not in current_reporters
            else "cadence_pending_configuration_changed"
            if admission["status"] == "configuration_changed" else None
        )
        conflict = error_code is not None
        from .pending_intent import pending_periodic_report_intents
        actionable = bool(window) and not conflict and any(
            intent["source_receipt_id"] == window["window_id"]
            for intent in pending_periodic_report_intents(registry_path=registry_path,
                runtime_root=runtime_root, goal_id=goal_id, agent_id=agent_id)
        )
        return {
            "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": "periodic_report.cadence", "capability_id": "periodic-report",
            "phase": "turn_start", "status": "unavailable" if conflict else "observed" if actionable else "empty",
            "observation_count": int(actionable),
            "agent_read_required": actionable,
            "external_reads_performed": False, "external_writes_performed": False,
            "local_private_state_mutated": admission["mutated"],
            "private_content_returned": False, "provider_payload_returned": False,
            "error_code": error_code,
        }

    return (TurnStartHookRegistration(
        hook_id="periodic_report.cadence", capability_id="periodic-report",
        requested_read_scope=("periodic_report_subscription", "periodic_report_publication_cursor"),
        requested_write_scope=("owner_private_inbox",), producer=produce,
        required_read={
            "kind": "capability_intent",
            "command": shlex.join(["loopx", "--registry", str(registry_path),
                "--runtime-root", str(runtime_root), "periodic-report", "consume-pending",
                "--goal-id", goal_id, "--agent-id", agent_id]),
            "reason": "Read the due report window and its editorial contract before generating the report.",
            "ordering": "before_work",
        },
    ),)


def extend_cadence_turn_start_dispatch(dispatch: dict, **kwargs) -> dict:
    """Same optional composition for quota and the managed turn driver."""
    try:
        extra = dispatch_turn_start_hooks(periodic_report_cadence_hooks(**kwargs))
    except (OSError, TypeError, ValueError):
        extra = {"failures": [{"hook_id": "periodic_report.cadence",
            "capability_id": "periodic-report", "error_code": "cadence_configuration_unavailable"}]}
    result = dict(dispatch)
    for key in ("results", "required_reads", "failures"):
        result[key] = list(dispatch.get(key) or []) + list(extra.get(key) or [])
    for key in ("registered_count", "invoked_count"):
        result[key] = int(dispatch.get(key) or 0) + int(extra.get(key) or 0)
    return result
