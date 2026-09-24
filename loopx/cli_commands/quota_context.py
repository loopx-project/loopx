from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..control_plane.scheduler.provider_monitor_poll import (
    require_monitor_poll_source_available,
)
from ..control_plane.quota.error_codes import QuotaCommandValidationError
from ..control_plane.runtime.status_projection_cache import (
    cached_goal_run_index_is_current,
    load_status_projection_cache,
    resolve_status_projection_cache_runtime_root,
    write_status_projection_cache,
)
from ..control_plane.scheduler.execution_context import (
    GUIDED_START_TURN_RUNTIME_PROFILES,
    HostSurface,
    SchedulerExecutionContextResolution,
    SchedulerRuntimeProfile,
    scheduler_execution_context_for_runtime_profile,
    scheduler_runtime_profile_for_execution_context,
    resolve_scheduler_execution_context,
)
from ..control_plane.scheduler.state import (
    APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
    CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
)
from ..status import AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK, collect_status
from ..turn_identity import mint_turn_instance_id, normalize_turn_instance_id
from .quota_request import (
    QUOTA_MONITOR_POLL_DETAIL_SECTIONS,
    QUOTA_SHOULD_RUN_DETAIL_SECTIONS,
    quota_detail_sections_from_args,
    validate_quota_command_request,
)

QUOTA_SCHEDULER_COMMANDS = frozenset(
    {
        "should-run",
        "monitor-poll",
        "scheduler-ack",
        "scheduler-ack-current",
        "scheduler-fail-current",
        "spend-slot",
    }
)
QUOTA_SCHEDULER_FOLLOWUP_COMMANDS = frozenset(
    {"scheduler-ack", "scheduler-ack-current", "scheduler-fail-current"}
)


@dataclass(frozen=True, slots=True)
class QuotaCommandContext:
    runtime_root: Path
    scan_roots: list[Path]
    status_limit: int
    status_goal_id: str | None
    status_payload: dict[str, object]
    cache_metadata: dict[str, object] | None
    scheduler_context: Mapping[str, object] | SchedulerExecutionContextResolution | None
    operator_inbox_urgency_projector: Callable[..., dict[str, object]]
    detail_sections: frozenset[str]
    heartbeat_turn_id: str | None


def _scheduler_execution_context_from_args(
    args: argparse.Namespace,
) -> Mapping[str, object] | SchedulerExecutionContextResolution | None:
    explicit_scheduler_fields = (
        args.host_surface,
        args.scheduler_owner,
        args.execution_mode,
    )
    codex_app = bool(getattr(args, "codex_app", False))
    trae_app = bool(getattr(args, "trae_app", False))
    app_alias_count = int(codex_app) + int(trae_app)
    if app_alias_count > 1:
        raise QuotaCommandValidationError(
            "--codex-app and --trae_app are mutually exclusive"
        )
    if app_alias_count and (args.runtime_profile or any(explicit_scheduler_fields)):
        raise QuotaCommandValidationError(
            "app runtime aliases cannot be combined with --runtime-profile, "
            "--host-surface, --scheduler-owner, or --execution-mode"
        )
    if args.runtime_profile and any(explicit_scheduler_fields):
        raise QuotaCommandValidationError(
            "--runtime-profile cannot be combined with --host-surface, "
            "--scheduler-owner, or --execution-mode"
        )
    runtime_profile = (
        SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT.value
        if codex_app
        else SchedulerRuntimeProfile.TRAE_APP.value
        if trae_app
        else args.runtime_profile
    )
    if runtime_profile:
        return scheduler_execution_context_for_runtime_profile(runtime_profile)
    if any(explicit_scheduler_fields):
        return {
            "host_surface": args.host_surface,
            "scheduler_owner": args.scheduler_owner,
            "execution_mode": args.execution_mode,
            "source": "quota_cli_invocation",
        }
    return None


def validate_quota_command_context_request(
    args: argparse.Namespace,
) -> tuple[
    str | None,
    Mapping[str, object] | SchedulerExecutionContextResolution | None,
    bool,
]:
    """Validate the request before any provider read or local projection write."""

    command = args.quota_command
    if bool(getattr(args, "turn_envelope", False)) and command != "should-run":
        raise QuotaCommandValidationError(
            "--turn-envelope is only valid with `quota should-run`"
        )
    requested_details = set(getattr(args, "include_details", None) or ())
    if requested_details and command not in {"should-run", "monitor-poll"}:
        raise QuotaCommandValidationError(
            "--include-detail is only valid with `quota should-run` or "
            "`quota monitor-poll`"
        )
    if requested_details and "all" not in requested_details:
        allowed_details = set(
            QUOTA_MONITOR_POLL_DETAIL_SECTIONS
            if command == "monitor-poll"
            else QUOTA_SHOULD_RUN_DETAIL_SECTIONS
        )
        unsupported_details = sorted(requested_details - allowed_details)
        if unsupported_details:
            raise QuotaCommandValidationError(
                f"`quota {command}` does not accept --include-detail "
                f"{', '.join(unsupported_details)}"
            )
    if (
        bool(getattr(args, "include_scheduler_detail", False))
        and command != "should-run"
    ):
        raise QuotaCommandValidationError(
            "--include-scheduler-detail is only valid with `quota should-run`"
        )
    if bool(getattr(args, "record_host_poll", False)) and command != "should-run":
        raise QuotaCommandValidationError(
            "--record-host-poll is only valid with `quota should-run`"
        )

    begin_turn = bool(getattr(args, "begin_turn", False))
    try:
        heartbeat_turn_id = normalize_turn_instance_id(
            getattr(args, "turn_instance_id", None)
        )
    except ValueError as exc:
        raise QuotaCommandValidationError(str(exc)) from exc
    if heartbeat_turn_id and command not in {
        "should-run",
        "monitor-poll",
        "scheduler-ack",
        "scheduler-ack-current",
        "scheduler-fail-current",
        "spend-slot",
    }:
        raise QuotaCommandValidationError(
            "--turn-instance-id is only valid with `quota should-run`, "
            "`quota monitor-poll`, scheduler ACK/failure follow-ups, or "
            "`quota spend-slot`"
        )
    if getattr(args, "replan_obligation_id", None) and command != "spend-slot":
        raise QuotaCommandValidationError(
            "--replan-obligation-id is only valid with `quota spend-slot`"
        )
    if getattr(args, "replan_obligation_id", None) and getattr(args, "todo_id", None):
        raise QuotaCommandValidationError(
            "--replan-obligation-id cannot be combined with --todo-id"
        )
    if heartbeat_turn_id and not args.agent_id:
        raise QuotaCommandValidationError(
            "turn-scoped quota settlement requires --agent-id"
        )
    scheduler_context = (
        _scheduler_execution_context_from_args(args)
        if command in QUOTA_SCHEDULER_COMMANDS
        else None
    )
    resolved_scheduler_context = resolve_scheduler_execution_context(scheduler_context)
    neutral_rrule = str(
        getattr(args, "app_automation_current_rrule", None) or ""
    ).strip()
    legacy_codex_rrule = str(
        getattr(args, "codex_app_current_rrule", None) or ""
    ).strip()
    if neutral_rrule and legacy_codex_rrule and neutral_rrule != legacy_codex_rrule:
        raise QuotaCommandValidationError(
            "--app-automation-current-rrule and --codex-app-current-rrule disagree"
        )
    if (
        legacy_codex_rrule
        and resolved_scheduler_context.ok
        and resolved_scheduler_context.context is not None
        and resolved_scheduler_context.context.host_surface is HostSurface.TRAE_APP
    ):
        raise QuotaCommandValidationError(
            "Trae App uses --app-automation-current-rrule, not the Codex compatibility alias"
        )
    args.app_automation_current_rrule = neutral_rrule or legacy_codex_rrule or None
    if command in QUOTA_SCHEDULER_FOLLOWUP_COMMANDS:
        selected_surface = (
            resolved_scheduler_context.context.host_surface.value
            if resolved_scheduler_context.ok
            and resolved_scheduler_context.context is not None
            and resolved_scheduler_context.context.app_automation_applicable
            else "codex_app"
        )
        supplied_surface = str(getattr(args, "surface", None) or "").strip()
        if supplied_surface and supplied_surface != selected_surface:
            raise QuotaCommandValidationError(
                f"--surface {supplied_surface} does not match selected App runtime {selected_surface}"
            )
        args.surface = selected_surface
        supplied_state_key = str(
            getattr(args, "state_key", None) or ""
        ).strip()
        default_state_key = (
            APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY
            if selected_surface == HostSurface.TRAE_APP.value
            else CODEX_APP_STATEFUL_BACKOFF_STATE_KEY
        )
        if (
            selected_surface == HostSurface.TRAE_APP.value
            and supplied_state_key
            and supplied_state_key != APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY
        ):
            raise QuotaCommandValidationError(
                "Trae App scheduler follow-up requires the app_automation state key"
            )
        args.state_key = supplied_state_key or default_state_key
    validate_quota_command_request(args)
    if begin_turn:
        profile = scheduler_runtime_profile_for_execution_context(scheduler_context)
        if profile not in GUIDED_START_TURN_RUNTIME_PROFILES:
            raise QuotaCommandValidationError(
                "--begin-turn requires runtime-profile codex_app_heartbeat, "
                "trae_app, or codex_app_ssh_goal; every other host starts its turn by "
                "passing its own --turn-instance-id"
            )
    if (
        (heartbeat_turn_id or begin_turn)
        and command == "should-run"
        and bool(args.dry_run)
    ):
        raise QuotaCommandValidationError(
            "turn-scoped `quota should-run` cannot use --dry-run"
        )
    return heartbeat_turn_id, scheduler_context, begin_turn


def prepare_quota_command_context(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    status_collector: Callable[..., dict[str, object]] | None = None,
    operator_inbox_urgency_projector_factory: Callable[
        ..., Callable[..., dict[str, object]]
    ],
    force_projection_refresh: bool = False,
) -> QuotaCommandContext:
    command = args.quota_command
    heartbeat_turn_id, scheduler_context, begin_turn = (
        validate_quota_command_context_request(args)
    )
    if begin_turn:
        heartbeat_turn_id = mint_turn_instance_id(prefix="guided-start")

    scan_roots = [Path(item).expanduser() for item in args.scan_path]
    if not scan_roots:
        scan_roots = [Path(args.scan_root).expanduser()]
    try:
        requested_limit = int(getattr(args, "limit", 0))
    except (TypeError, ValueError):
        requested_limit = 0
    status_limit = max(0, requested_limit)
    if command in QUOTA_SCHEDULER_COMMANDS:
        status_limit = max(status_limit, AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK)
    runtime_root = resolve_status_projection_cache_runtime_root(
        registry_path=registry_path,
        runtime_root_override=runtime_root_arg,
    )
    if command == "monitor-poll" and args.execute and (args.todo_id or args.target_key):
        # Canonical availability precedes unrelated status/quota preparation.
        # The eventual transaction repeats its fence check under the writer lock.
        require_monitor_poll_source_available(
            runtime_root=runtime_root, goal_id=args.goal_id,
        )
    status_goal_id = args.goal_id if command not in {"status", "plan"} else None
    projection_cache_ttl_seconds = int(
        getattr(args, "projection_cache_ttl_seconds", 120)
    )
    status_payload = None
    cache_metadata = None
    if (
        bool(getattr(args, "use_projection_cache", False))
        and not force_projection_refresh
    ):
        status_payload, cache_metadata = load_status_projection_cache(
            registry_path=registry_path,
            runtime_root=runtime_root,
            scan_roots=scan_roots,
            limit=status_limit,
            include_task_graph=False,
            goal_id=status_goal_id,
            max_age_seconds=projection_cache_ttl_seconds,
            available_capabilities=args.available_capabilities,
            agent_lane_id=args.agent_id,
        )
        if (
            status_payload is not None
            and command in QUOTA_SCHEDULER_COMMANDS
            and status_goal_id
            and not cached_goal_run_index_is_current(
                status_payload, runtime_root=runtime_root, goal_id=status_goal_id,
            )
        ):
            status_payload = None
            cache_metadata["hit"] = False
            cache_metadata["miss_reason"] = "run_index_changed"
    if status_payload is None:
        collector = status_collector or collect_status
        status_payload = collector(
            registry_path=registry_path,
            runtime_root_override=runtime_root_arg,
            scan_roots=scan_roots,
            limit=status_limit,
            goal_id=status_goal_id,
            available_capabilities=args.available_capabilities,
            agent_lane_id=args.agent_id,
        )
        if bool(getattr(args, "write_projection_cache", False)):
            cache_metadata = write_status_projection_cache(
                registry_path=registry_path,
                runtime_root=runtime_root,
                scan_roots=scan_roots,
                limit=status_limit,
                include_task_graph=False,
                goal_id=status_goal_id,
                payload=status_payload,
                max_age_seconds=projection_cache_ttl_seconds,
                available_capabilities=args.available_capabilities,
                agent_lane_id=args.agent_id,
            )
    elif isinstance(status_payload.get("projection_cache"), dict):
        cache_metadata = dict(status_payload["projection_cache"])

    return QuotaCommandContext(
        runtime_root=runtime_root,
        scan_roots=scan_roots,
        status_limit=status_limit,
        status_goal_id=status_goal_id,
        status_payload=status_payload,
        cache_metadata=cache_metadata,
        scheduler_context=scheduler_context,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector_factory(
            runtime_root_arg=runtime_root
        ),
        detail_sections=quota_detail_sections_from_args(args),
        heartbeat_turn_id=heartbeat_turn_id,
    )
