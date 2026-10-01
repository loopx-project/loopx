from __future__ import annotations

import copy
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

SCHEDULER_EXECUTION_CONTEXT_SCHEMA_VERSION = "scheduler_execution_context_v0"
GOAL_RUNTIME_CONTINUATION_SCHEMA_VERSION = "goal_runtime_continuation_v0"


class HostSurface(str, Enum):
    ARK_MANAGED_AGENT = "ark_managed_agent"
    CODEX_APP = "codex_app"
    CODEX_APP_SSH = "codex_app_ssh"
    CODEX_CLI = "codex_cli"
    TRAE_APP = "trae_app"
    GENERIC_CLI = "generic_cli"
    CLAUDE_CODE = "claude_code"
    KUNLUNCODE = "kunluncode"
    KIRO_CLI = "kiro_cli"
    LOCAL_SCHEDULER = "local_scheduler"


class SchedulerOwner(str, Enum):
    HOST_AUTOMATION = "host_automation"
    AGENT_CLI_LOOP = "agent_cli_loop"
    GOAL_RUNTIME = "goal_runtime"
    OUTER_CONTROLLER = "outer_controller"
    NONE = "none"


class ExecutionMode(str, Enum):
    INTERACTIVE = "interactive"
    ISOLATED_HEADLESS = "isolated_headless"
    HOSTED_AUTOMATION = "hosted_automation"


class SchedulerRuntimeProfile(str, Enum):
    ARK_MANAGED_AGENT_GOAL = "ark_managed_agent_goal"
    CODEX_APP_HEARTBEAT = "codex_app_heartbeat"
    CODEX_APP_SSH_VISIBLE = "codex_app_ssh_goal"
    CODEX_CLI_VISIBLE = "codex_cli"
    TRAE_APP = "trae_app"
    CLAUDE_CODE_VISIBLE = "claude_code"
    KUNLUNCODE_VISIBLE = "kunluncode"
    KIRO_CLI_VISIBLE = "kiro_cli"
    GENERIC_CLI_AGENT_LOOP = "generic_cli"
    GENERIC_CLI_OUTER_CONTROLLER = "outer_controller"


class GoalRuntimeContinuationDisposition(str, Enum):
    CONTINUE_NOW = "continue_now"
    DEFER = "defer"
    COMPLETE = "complete"


GOAL_RUNTIME_DEFER_ACTIONS = frozenset(
    {
        "backoff_agent_monitor_only",
        "backoff_until_fresh_evidence",
        "backoff_until_material_transition",
        "backoff_until_reassigned",
        "backoff_until_state_change",
        "backoff_waiting_for_user",
        "repair_interaction_contract_projection",
    }
)


NATIVE_GOAL_RUNTIME_PROFILES = frozenset(
    {
        SchedulerRuntimeProfile.ARK_MANAGED_AGENT_GOAL,
        SchedulerRuntimeProfile.CODEX_APP_SSH_VISIBLE,
        SchedulerRuntimeProfile.CODEX_CLI_VISIBLE,
    }
)

# Interactive hosts in this set can carry one explicit Turn identity through
# accountable writeback and visible-Goal quota settlement. Claude's ordinary
# Todo delivery remains MCP-owned; this shared contract also permits its
# Todo-less replan re-entry to finish once a Turn has been supplied. Kiro CLI
# drives its own in-session `/goal` loop, so its spend is a visible-Goal spend
# rather than a heartbeat one. It is deliberately not a native-goal profile:
# that family renders the Codex `/goal` task body and blocked-state rules,
# which the Kiro host does not share.
VISIBLE_GOAL_SETTLEMENT_RUNTIME_PROFILES = frozenset(
    {
        *NATIVE_GOAL_RUNTIME_PROFILES,
        SchedulerRuntimeProfile.CLAUDE_CODE_VISIBLE,
        SchedulerRuntimeProfile.KIRO_CLI_VISIBLE,
    }
)

GUIDED_START_TURN_RUNTIME_PROFILES = frozenset(
    {
        SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT,
        SchedulerRuntimeProfile.TRAE_APP,
        SchedulerRuntimeProfile.CODEX_APP_SSH_VISIBLE,
    }
)


# Hosted App runtimes share the receipt-bound settlement lifecycle while
# retaining independent host, state, and provider identities. Keep this set as
# the typed owner so downstream work-item code does not drift through repeated
# Codex-only profile checks.
APP_HEARTBEAT_SETTLEMENT_RUNTIME_PROFILES = frozenset(
    {
        SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT,
        SchedulerRuntimeProfile.TRAE_APP,
    }
)


_SCHEDULER_RUNTIME_PROFILE_CONTEXTS = {
    SchedulerRuntimeProfile.ARK_MANAGED_AGENT_GOAL: (
        HostSurface.ARK_MANAGED_AGENT,
        SchedulerOwner.GOAL_RUNTIME,
        ExecutionMode.INTERACTIVE,
    ),
    SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT: (
        HostSurface.CODEX_APP,
        SchedulerOwner.HOST_AUTOMATION,
        ExecutionMode.HOSTED_AUTOMATION,
    ),
    SchedulerRuntimeProfile.CODEX_APP_SSH_VISIBLE: (
        HostSurface.CODEX_APP_SSH,
        SchedulerOwner.AGENT_CLI_LOOP,
        ExecutionMode.INTERACTIVE,
    ),
    SchedulerRuntimeProfile.CODEX_CLI_VISIBLE: (
        HostSurface.CODEX_CLI,
        SchedulerOwner.AGENT_CLI_LOOP,
        ExecutionMode.INTERACTIVE,
    ),
    SchedulerRuntimeProfile.TRAE_APP: (
        HostSurface.TRAE_APP,
        SchedulerOwner.HOST_AUTOMATION,
        ExecutionMode.HOSTED_AUTOMATION,
    ),
    SchedulerRuntimeProfile.CLAUDE_CODE_VISIBLE: (
        HostSurface.CLAUDE_CODE,
        SchedulerOwner.AGENT_CLI_LOOP,
        ExecutionMode.INTERACTIVE,
    ),
    SchedulerRuntimeProfile.KUNLUNCODE_VISIBLE: (
        HostSurface.KUNLUNCODE,
        SchedulerOwner.AGENT_CLI_LOOP,
        ExecutionMode.INTERACTIVE,
    ),
    SchedulerRuntimeProfile.KIRO_CLI_VISIBLE: (
        HostSurface.KIRO_CLI,
        SchedulerOwner.AGENT_CLI_LOOP,
        ExecutionMode.INTERACTIVE,
    ),
    SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP: (
        HostSurface.GENERIC_CLI,
        SchedulerOwner.AGENT_CLI_LOOP,
        ExecutionMode.INTERACTIVE,
    ),
    SchedulerRuntimeProfile.GENERIC_CLI_OUTER_CONTROLLER: (
        HostSurface.GENERIC_CLI,
        SchedulerOwner.OUTER_CONTROLLER,
        ExecutionMode.ISOLATED_HEADLESS,
    ),
}


GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT = {
    "host_surface": HostSurface.GENERIC_CLI.value,
    "scheduler_owner": SchedulerOwner.OUTER_CONTROLLER.value,
    "execution_mode": ExecutionMode.ISOLATED_HEADLESS.value,
}


@dataclass(frozen=True)
class SchedulerExecutionContext:
    host_surface: HostSurface
    scheduler_owner: SchedulerOwner
    execution_mode: ExecutionMode
    source: str

    @property
    def codex_app_applicable(self) -> bool:
        return (
            self.host_surface is HostSurface.CODEX_APP
            and self.scheduler_owner is SchedulerOwner.HOST_AUTOMATION
            and self.execution_mode is ExecutionMode.HOSTED_AUTOMATION
        )

    @property
    def app_automation_applicable(self) -> bool:
        return (
            self.host_surface in {HostSurface.CODEX_APP, HostSurface.TRAE_APP}
            and self.scheduler_owner is SchedulerOwner.HOST_AUTOMATION
            and self.execution_mode is ExecutionMode.HOSTED_AUTOMATION
        )

    def projection(self) -> dict[str, Any]:
        projection = {
            "schema_version": SCHEDULER_EXECUTION_CONTEXT_SCHEMA_VERSION,
            "host_surface": self.host_surface.value,
            "scheduler_owner": self.scheduler_owner.value,
            "execution_mode": self.execution_mode.value,
            "source": self.source,
            "valid": True,
        }
        # Keep existing non-Trae projections byte-compatible. The neutral App
        # field is needed to identify Trae's independent automation contract,
        # but a second negative applicability flag adds no information for CLI
        # and Goal runtimes.
        if self.host_surface is HostSurface.TRAE_APP:
            projection["app_automation_applicability"] = "applicable"
        else:
            projection["codex_app_applicability"] = (
                "applicable" if self.codex_app_applicable else "not_applicable"
            )
        return projection


@dataclass(frozen=True)
class SchedulerExecutionContextResolution:
    context: SchedulerExecutionContext | None
    errors: tuple[str, ...] = ()
    supplied: Mapping[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.context is not None and not self.errors

    def projection(self) -> dict[str, Any]:
        if self.context is not None:
            return self.context.projection()
        supplied = dict(self.supplied or {})
        projection = {
            "schema_version": SCHEDULER_EXECUTION_CONTEXT_SCHEMA_VERSION,
            "host_surface": supplied.get("host_surface"),
            "scheduler_owner": supplied.get("scheduler_owner"),
            "execution_mode": supplied.get("execution_mode"),
            "source": supplied.get("source") or "explicit",
            "valid": False,
            "errors": list(self.errors),
        }
        if supplied.get("host_surface") == HostSurface.TRAE_APP.value:
            projection["app_automation_applicability"] = "blocked_invalid_context"
        else:
            projection["codex_app_applicability"] = "blocked_invalid_context"
        return projection


def _validation_errors(context: SchedulerExecutionContext) -> list[str]:
    errors: list[str] = []
    cli_surfaces = {
        HostSurface.CODEX_APP_SSH,
        HostSurface.CODEX_CLI,
        HostSurface.GENERIC_CLI,
        HostSurface.CLAUDE_CODE,
        HostSurface.KUNLUNCODE,
        HostSurface.KIRO_CLI,
    }
    if context.host_surface in {HostSurface.CODEX_APP, HostSurface.TRAE_APP}:
        host_name = context.host_surface.value
        if context.scheduler_owner is not SchedulerOwner.HOST_AUTOMATION:
            errors.append(f"{host_name} requires scheduler_owner=host_automation")
        if context.execution_mode is not ExecutionMode.HOSTED_AUTOMATION:
            errors.append(f"{host_name} requires execution_mode=hosted_automation")
    if context.host_surface is HostSurface.ARK_MANAGED_AGENT:
        if context.scheduler_owner is not SchedulerOwner.GOAL_RUNTIME:
            errors.append("ark_managed_agent requires scheduler_owner=goal_runtime")
        if context.execution_mode is not ExecutionMode.INTERACTIVE:
            errors.append("ark_managed_agent requires execution_mode=interactive")
    if context.host_surface is HostSurface.CODEX_APP_SSH:
        if context.scheduler_owner is not SchedulerOwner.AGENT_CLI_LOOP:
            errors.append("codex_app_ssh requires scheduler_owner=agent_cli_loop")
        if context.execution_mode is not ExecutionMode.INTERACTIVE:
            errors.append("codex_app_ssh requires execution_mode=interactive")
    if context.host_surface is HostSurface.LOCAL_SCHEDULER:
        if context.scheduler_owner is not SchedulerOwner.HOST_AUTOMATION:
            errors.append("local_scheduler requires scheduler_owner=host_automation")
        if context.execution_mode is not ExecutionMode.HOSTED_AUTOMATION:
            errors.append("local_scheduler requires execution_mode=hosted_automation")
    if (
        context.host_surface in cli_surfaces
        and context.scheduler_owner is SchedulerOwner.HOST_AUTOMATION
    ):
        errors.append("CLI host surfaces cannot be owned by host_automation")
    if context.scheduler_owner is SchedulerOwner.OUTER_CONTROLLER:
        if context.host_surface not in cli_surfaces:
            errors.append("outer_controller requires a CLI host surface")
        if context.execution_mode is not ExecutionMode.ISOLATED_HEADLESS:
            errors.append("outer_controller requires execution_mode=isolated_headless")
    if context.scheduler_owner is SchedulerOwner.NONE:
        if context.host_surface not in cli_surfaces:
            errors.append("scheduler_owner=none requires a CLI host surface")
        if context.execution_mode is not ExecutionMode.INTERACTIVE:
            errors.append("scheduler_owner=none requires execution_mode=interactive")
    if context.scheduler_owner is SchedulerOwner.AGENT_CLI_LOOP:
        if context.host_surface not in cli_surfaces:
            errors.append("agent_cli_loop requires a CLI host surface")
        if context.execution_mode is ExecutionMode.HOSTED_AUTOMATION:
            errors.append("agent_cli_loop cannot use execution_mode=hosted_automation")
    if context.scheduler_owner is SchedulerOwner.GOAL_RUNTIME:
        if context.host_surface is not HostSurface.ARK_MANAGED_AGENT:
            errors.append("goal_runtime requires host_surface=ark_managed_agent")
        if context.execution_mode is not ExecutionMode.INTERACTIVE:
            errors.append("goal_runtime requires execution_mode=interactive")
    if (
        context.execution_mode is ExecutionMode.HOSTED_AUTOMATION
        and context.scheduler_owner is not SchedulerOwner.HOST_AUTOMATION
    ):
        errors.append("hosted_automation requires scheduler_owner=host_automation")
    return errors


def resolve_scheduler_execution_context(
    value: Mapping[str, Any] | SchedulerExecutionContextResolution | None,
) -> SchedulerExecutionContextResolution:
    if isinstance(value, SchedulerExecutionContextResolution):
        return value
    if value is None:
        return SchedulerExecutionContextResolution(
            context=None,
            errors=(
                "missing required field: host_surface",
                "missing required field: scheduler_owner",
                "missing required field: execution_mode",
            ),
            supplied=None,
        )

    supplied = dict(value)
    missing = [
        field
        for field in ("host_surface", "scheduler_owner", "execution_mode")
        if not str(supplied.get(field) or "").strip()
    ]
    errors = [f"missing required field: {field}" for field in missing]
    try:
        host_surface = HostSurface(str(supplied.get("host_surface") or ""))
    except ValueError:
        host_surface = None
        if "host_surface" not in missing:
            errors.append("unsupported host_surface")
    try:
        scheduler_owner = SchedulerOwner(str(supplied.get("scheduler_owner") or ""))
    except ValueError:
        scheduler_owner = None
        if "scheduler_owner" not in missing:
            errors.append("unsupported scheduler_owner")
    try:
        execution_mode = ExecutionMode(str(supplied.get("execution_mode") or ""))
    except ValueError:
        execution_mode = None
        if "execution_mode" not in missing:
            errors.append("unsupported execution_mode")
    if errors:
        return SchedulerExecutionContextResolution(
            context=None,
            errors=tuple(errors),
            supplied=supplied,
        )
    context = SchedulerExecutionContext(
        host_surface=host_surface,
        scheduler_owner=scheduler_owner,
        execution_mode=execution_mode,
        source=str(supplied.get("source") or "explicit"),
    )
    errors = _validation_errors(context)
    return SchedulerExecutionContextResolution(
        context=context if not errors else None,
        errors=tuple(errors),
        supplied=supplied,
    )


def scheduler_execution_context_for_runtime_profile(
    value: str | SchedulerRuntimeProfile,
) -> SchedulerExecutionContextResolution:
    try:
        profile = SchedulerRuntimeProfile(value)
    except ValueError:
        return SchedulerExecutionContextResolution(
            context=None,
            errors=("unsupported scheduler runtime profile",),
            supplied={"source": f"runtime_profile:{value}"},
        )

    host_surface, scheduler_owner, execution_mode = (
        _SCHEDULER_RUNTIME_PROFILE_CONTEXTS[profile]
    )
    return resolve_scheduler_execution_context(
        {
            "host_surface": host_surface.value,
            "scheduler_owner": scheduler_owner.value,
            "execution_mode": execution_mode.value,
            "source": f"runtime_profile:{profile.value}",
        }
    )


def scheduler_runtime_profile_for_execution_context(
    value: Mapping[str, Any] | SchedulerExecutionContextResolution | None,
) -> SchedulerRuntimeProfile | None:
    resolution = resolve_scheduler_execution_context(value)
    if not resolution.ok or resolution.context is None:
        return None
    signature = (
        resolution.context.host_surface,
        resolution.context.scheduler_owner,
        resolution.context.execution_mode,
    )
    return next(
        (
            profile
            for profile, profile_signature in _SCHEDULER_RUNTIME_PROFILE_CONTEXTS.items()
            if signature == profile_signature
        ),
        None,
    )


def _render_scheduler_runtime_profile_args(
    profile: SchedulerRuntimeProfile,
) -> str:
    if profile is SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT:
        return " --codex-app"
    if profile is SchedulerRuntimeProfile.TRAE_APP:
        return " --trae_app"
    return f" --runtime-profile {shlex.quote(profile.value)}"


def render_scheduler_execution_args(
    *,
    runtime_profile: str | None = None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
) -> str:
    if runtime_profile and scheduler_execution_context:
        raise ValueError(
            "runtime_profile and scheduler_execution_context are mutually exclusive"
        )
    if runtime_profile:
        try:
            profile = SchedulerRuntimeProfile(runtime_profile)
        except ValueError as exc:
            raise ValueError(
                f"unsupported scheduler runtime profile: {runtime_profile}"
            ) from exc
        return _render_scheduler_runtime_profile_args(profile)
    if scheduler_execution_context is None:
        return ""
    resolution = resolve_scheduler_execution_context(scheduler_execution_context)
    if not resolution.ok or resolution.context is None:
        raise ValueError(
            "invalid scheduler_execution_context: " + "; ".join(resolution.errors)
        )
    profile = scheduler_runtime_profile_for_execution_context(resolution)
    if profile is not None:
        return _render_scheduler_runtime_profile_args(profile)
    context = resolution.context
    return "".join(
        (
            f" -H {shlex.quote(context.host_surface.value)}",
            f" -O {shlex.quote(context.scheduler_owner.value)}",
            f" -M {shlex.quote(context.execution_mode.value)}",
        )
    )


def scheduler_execution_context_for_turn(
    *,
    host: str,
    execution_mode: str,
    scheduler_owner: str | None = None,
) -> SchedulerExecutionContextResolution:
    host_surface = {
        "codex-app-ssh": HostSurface.CODEX_APP_SSH.value,
        "codex-cli": HostSurface.CODEX_CLI.value,
        "generic-cli": HostSurface.GENERIC_CLI.value,
        "claude-code": HostSurface.CLAUDE_CODE.value,
        "pi": HostSurface.GENERIC_CLI.value,
        # The built-in dsh host runs one bounded in-process attempt under the
        # generic-cli scheduling surface, same as the pi visible alias.
        "dsh": HostSurface.GENERIC_CLI.value,
    }.get(host, host)
    normalized_mode = {
        "interactive-visible": ExecutionMode.INTERACTIVE.value,
        "isolated-headless": ExecutionMode.ISOLATED_HEADLESS.value,
    }.get(execution_mode, execution_mode)
    owner = scheduler_owner or (
        SchedulerOwner.OUTER_CONTROLLER.value
        if host_surface == HostSurface.GENERIC_CLI.value
        else SchedulerOwner.AGENT_CLI_LOOP.value
    )
    return resolve_scheduler_execution_context(
        {
            "host_surface": host_surface,
            "scheduler_owner": owner,
            "execution_mode": normalized_mode,
            "source": "loopx_turn",
        }
    )


def build_goal_runtime_continuation(
    scheduler_hint: Mapping[str, Any],
    *,
    frontier_recheck_after_seconds: int | None = None,
) -> dict[str, Any]:
    action = str(scheduler_hint.get("action") or "")
    if action in {"run_now", "preserve_current_schedule"}:
        # Replay does not complete or block a native Goal. Its next iteration
        # must consult quota with a fresh identity, not repeat this closed Turn.
        disposition = GoalRuntimeContinuationDisposition.CONTINUE_NOW
    elif action == "stop_until_explicit_resume":
        disposition = GoalRuntimeContinuationDisposition.COMPLETE
    elif action in GOAL_RUNTIME_DEFER_ACTIONS:
        disposition = GoalRuntimeContinuationDisposition.DEFER
    else:
        raise ValueError(f"unsupported Goal runtime scheduler action: {action or 'missing'}")

    continuation = {
        "schema_version": GOAL_RUNTIME_CONTINUATION_SCHEMA_VERSION,
        "disposition": disposition.value,
    }
    if disposition is GoalRuntimeContinuationDisposition.DEFER:
        if (
            isinstance(frontier_recheck_after_seconds, int)
            and frontier_recheck_after_seconds > 0
        ):
            continuation["recheck_after_seconds"] = frontier_recheck_after_seconds
            continuation["recheck_source"] = "frontier_earliest_material_transition"
        else:
            host_cadence = scheduler_hint.get("app_automation")
            if not isinstance(host_cadence, Mapping):
                host_cadence = scheduler_hint.get("codex_app")
            host_cadence = host_cadence if isinstance(host_cadence, Mapping) else {}
            recommended_interval = host_cadence.get("recommended_interval_minutes")
            if not isinstance(recommended_interval, int) or recommended_interval <= 0:
                raise ValueError(
                    "deferred Goal runtime continuation requires a positive recheck interval"
                )
            continuation["recheck_after_seconds"] = recommended_interval * 60
        continuation["wake_policy"] = "state_change_or_deadline"
    return continuation


def apply_scheduler_execution_context(
    result: dict[str, Any],
    resolution: SchedulerExecutionContextResolution,
    *,
    frontier_recheck_after_seconds: int | None = None,
) -> dict[str, Any]:
    """Scope a generic cadence hint to the selected runtime owner."""

    if not resolution.ok or resolution.context is None:
        raise ValueError("cannot apply an invalid scheduler execution context")
    context = resolution.context

    app_automation = (
        result.get("app_automation")
        if isinstance(result.get("app_automation"), dict)
        else {}
    )
    if context.app_automation_applicable:
        app_automation["applicability"] = "applicable"
        app_automation["host_surface"] = context.host_surface.value
        backoff = (
            app_automation.get("stateful_backoff")
            if isinstance(app_automation.get("stateful_backoff"), dict)
            else {}
        )
        apply_needed = (
            backoff.get("apply_needed") is True
            or app_automation.get("host_action_required") is True
        )
        ack_needed = backoff.get("ack_needed") is True
        result["app_automation"] = app_automation
        if context.host_surface is HostSurface.CODEX_APP:
            codex_app = result.get("codex_app")
            if not isinstance(codex_app, dict):
                # Stateless stop/pause packets do not pass through the stateful
                # compatibility builder. Keep their legacy view independent so
                # later route binding cannot mutate the canonical packet.
                result["codex_app"] = copy.deepcopy(app_automation)
            reset_policy = result.get("reset_policy")
            if isinstance(reset_policy, dict):
                reset_policy["codex_app_initial_interval_minutes"] = (
                    reset_policy.get("app_automation_initial_interval_minutes")
                )
                reset_policy["codex_app_initial_rrule"] = reset_policy.get(
                    "app_automation_initial_rrule"
                )
            cold_path = result.get("cold_path_detail")
            reset_detail = (
                cold_path.get("reset_policy_detail")
                if isinstance(cold_path, dict)
                and isinstance(cold_path.get("reset_policy_detail"), dict)
                else None
            )
            if reset_detail is not None:
                reset_detail["codex_app_initial_interval_minutes"] = (
                    reset_detail.get("app_automation_initial_interval_minutes")
                )
                reset_detail["codex_app_initial_rrule"] = reset_detail.get(
                    "app_automation_initial_rrule"
                )
                reset_detail["codex_app_tool"] = reset_detail.get(
                    "app_automation_tool"
                )
                reset_detail["codex_app_apply"] = reset_detail.get(
                    "app_automation_apply"
                )
        else:
            result.pop("codex_app", None)
        execution_phase = {
            "schema_version": "scheduler_execution_phase_v0",
            "host_surface": context.host_surface.value,
            "scheduler_owner": context.scheduler_owner.value,
            "disposition": (
                "host_action_required" if apply_needed or ack_needed else "not_required"
            ),
            "completed": not (apply_needed or ack_needed),
            "apply_needed": apply_needed,
            "ack_needed": ack_needed,
            "acknowledged": False,
        }
        cold_path = result.get("cold_path_detail")
        if isinstance(cold_path, dict):
            cold_path["execution_context"] = resolution.projection()
            cold_path["execution_phase"] = execution_phase
        return result

    goal_runtime_continuation = (
        build_goal_runtime_continuation(
            result,
            frontier_recheck_after_seconds=frontier_recheck_after_seconds,
        )
        if context.scheduler_owner is SchedulerOwner.GOAL_RUNTIME
        else None
    )

    result["execution_context"] = resolution.projection()
    not_applicable = {
        "applicability": "not_applicable",
        "reason_code": f"cadence_owned_by_{context.scheduler_owner.value}",
        "apply": "none",
        "host_action": "none",
        "ack_required": False,
        "no_spend_for_cadence_change": True,
    }
    # Preserve the historical non-App response exactly. Provider-neutral App
    # automation is emitted only when a real App host owns the cadence; adding
    # it to unrelated CLI/Goal-runtime responses would duplicate a negative
    # projection and expand every agent-facing payload.
    result.pop("app_automation", None)
    result["codex_app"] = not_applicable
    reset_policy = result.get("reset_policy")
    if isinstance(reset_policy, dict):
        for key in tuple(reset_policy):
            if key.startswith(("app_automation_", "codex_app_")):
                reset_policy.pop(key, None)
    cold_path = result.get("cold_path_detail")
    if isinstance(cold_path, dict):
        cold_path.pop("stateful_backoff_detail", None)
        reset_detail = cold_path.get("reset_policy_detail")
        if isinstance(reset_detail, dict):
            for key in tuple(reset_detail):
                if key.startswith(("app_automation_", "codex_app_")):
                    reset_detail.pop(key, None)
    owner = context.scheduler_owner.value
    result["execution_phase"] = {
        "schema_version": "scheduler_execution_phase_v0",
        "host_surface": context.host_surface.value,
        "scheduler_owner": owner,
        "disposition": f"{owner}_owned",
        "completed": True,
        "apply_needed": False,
        "ack_needed": False,
        "acknowledged": False,
        "completion_reason": (
            "selected scheduler owner requires no Codex App apply or ACK"
        ),
    }
    if goal_runtime_continuation is not None:
        result["goal_runtime_continuation"] = goal_runtime_continuation
    return result
