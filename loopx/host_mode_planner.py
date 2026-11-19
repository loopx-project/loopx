from __future__ import annotations

from typing import Any

from .control_plane.scheduler.execution_context import (
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    scheduler_execution_context_for_turn,
)
from .control_plane.turn_driver.driver import SUPPORTED_HOSTS
from .host_loop_activation import _identity_state
from .project_prompt import render_quota_guard_command, shell_arg


SCHEMA_VERSION = "host_mode_plan_v0"

# Host-mode selector modes. These are user/operator choices that map onto the
# shipped connector catalog and LoopX Turn contract.

MODE_VISIBLE_TUI = "visible_tui"
MODE_ISOLATED_HEADLESS_TURN = "isolated_headless_turn"
MODE_IM_GATEWAY = "im_gateway"
MODE_SHELL_SERVICE = "shell_service"
MODE_HYBRID_HANDOFF = "hybrid_handoff"

CAP_VISIBLE_SESSION = "visible_session"
CAP_LOOPX_TURN = "loopx_turn"
CAP_TYPED_HOST_ADAPTER = "typed_host_adapter"
CAP_INDEPENDENT_VALIDATOR = "independent_validator"
CAP_CHAT_GATEWAY = "chat_gateway"
CAP_SERVICE_TIMER = "service_timer"
CAP_SHELL = "shell"

INTENT_WATCH_EACH_TURN = "watch_each_turn"
INTENT_CONTINUE_WITHOUT_UI = "continue_without_ui"
INTENT_INTAKE_FROM_CHAT = "intake_from_chat"
INTENT_TIMER_KEEPALIVE = "timer_keepalive"
INTENT_ESCALATE_BETWEEN_MODES = "escalate_between_modes"

_SHARED_PROOFS = [
    "scoped_agent_identity",
    "quota_guard_before_run",
    "turn_envelope_or_connector_contract_preserved",
    "readiness_probe",
]
_TURN_PROOFS = [
    "loopx_turn_plan_preview",
    "typed_host_adapter",
    "independent_validation",
    "validated_writeback_before_quota_spend",
]

_MODE_METADATA: dict[str, dict[str, Any]] = {
    MODE_VISIBLE_TUI: {
        "intent": INTENT_WATCH_EACH_TURN,
        "connector_id": "codex_cli_tui",
        "turn_host": "codex-cli",
        "turn_execution_mode": "interactive-visible",
        "scheduler_owner": "agent_cli_loop",
        "required_capabilities": [CAP_VISIBLE_SESSION],
        "summary": "Keep work in a visible Codex/agent TUI so the user can watch or steer each turn.",
        "extra_proofs": ["visible_surface_preserved", "clear_gate_handling"],
    },
    MODE_ISOLATED_HEADLESS_TURN: {
        "intent": INTENT_CONTINUE_WITHOUT_UI,
        "connector_id": "loopx_turn",
        "turn_host": "generic-cli",
        "turn_execution_mode": "isolated-headless",
        "scheduler_owner": "outer_controller",
        "required_capabilities": [CAP_LOOPX_TURN, CAP_TYPED_HOST_ADAPTER, CAP_INDEPENDENT_VALIDATOR],
        "summary": "Use the shipped LoopX Turn path for bounded headless execution with typed results.",
        "extra_proofs": [*_TURN_PROOFS, "opaque_session_handle_not_public"],
    },
    MODE_IM_GATEWAY: {
        "intent": INTENT_INTAKE_FROM_CHAT,
        "connector_id": "http_webhook",
        "turn_host": None,
        "turn_execution_mode": None,
        "scheduler_owner": None,
        "required_capabilities": [CAP_CHAT_GATEWAY],
        "summary": "Use a chat/webhook gateway only for durable intake, then hand execution to LoopX state.",
        "extra_proofs": ["durable_todo_intake", "text_or_card_fallback", "source_boundary"],
    },
    MODE_SHELL_SERVICE: {
        "intent": INTENT_TIMER_KEEPALIVE,
        "connector_id": "shell_worker",
        "turn_host": "generic-cli",
        "turn_execution_mode": "isolated-headless",
        "scheduler_owner": "outer_controller",
        "required_capabilities": [CAP_SERVICE_TIMER, CAP_SHELL, CAP_LOOPX_TURN, CAP_TYPED_HOST_ADAPTER, CAP_INDEPENDENT_VALIDATOR],
        "summary": "Let a shell, cron, launchd, or service timer wake bounded LoopX Turn previews/runs.",
        "extra_proofs": [*_TURN_PROOFS, "scheduler_hint_backoff", "no_spend_quiet_monitor"],
    },
    MODE_HYBRID_HANDOFF: {
        "intent": INTENT_ESCALATE_BETWEEN_MODES,
        "connector_id": "hybrid_handoff",
        "turn_host": None,
        "turn_execution_mode": None,
        "scheduler_owner": None,
        "required_capabilities": [],
        "summary": "Plan an explicit transition between visible, intake, timer, and headless Turn modes.",
        "extra_proofs": [
            "explicit_transition_event",
            "target_mode_readiness",
            "shared_agent_id_writeback",
            "visible_escalation_for_user_gate",
        ],
    },
}

CANONICAL_MODES = list(_MODE_METADATA)
SUPPORTED_INTENTS = [meta["intent"] for meta in _MODE_METADATA.values()]
SUPPORTED_HOST_CAPABILITIES = [
    CAP_VISIBLE_SESSION,
    CAP_LOOPX_TURN,
    CAP_TYPED_HOST_ADAPTER,
    CAP_INDEPENDENT_VALIDATOR,
    CAP_CHAT_GATEWAY,
    CAP_SERVICE_TIMER,
    CAP_SHELL,
]
_INTENT_PRIMARY_MODE = {meta["intent"]: mode for mode, meta in _MODE_METADATA.items()}

# The headless Turn modes preview the *shipped* host resolution instead of
# pinning one host. `loopx turn plan`/`run-once` resolve one shipped default,
# owned by `control_plane.turn_driver.host_binding.selected_turn_host`, which
# `LOOPX_TURN_HOST` or an explicit `--host` re-points; that default is itself
# resolved from the operator credential, so a preview that pinned
# `generic-cli` would quietly ask every operator for the compatibility adapter
# path instead of the host their own machine will run. The declared host stays
# in the mapping and in the rollback command, because the mode's scheduler
# context and capability requirements are still stated for it.
RESOLVED_DEFAULT_TURN_HOST_MODES = frozenset(
    {MODE_ISOLATED_HEADLESS_TURN, MODE_SHELL_SERVICE}
)
TURN_HOST_SELECTION_PINNED = "pinned"
TURN_HOST_SELECTION_RESOLVED_DEFAULT = "resolved_default"

# Typed host identity -> runtime connector catalog id. Only identities with a
# registered catalog connector may emit a host-specific visible mapping; any
# other identity fails closed instead of fabricating a connector id.
VISIBLE_HOST_CONNECTOR_IDS: dict[str, str] = {
    "codex-cli": "codex_cli_tui",
    "claude-code": "claude_code_loop",
    # OpenCode runs its visible goal loop through the generic-cli Turn host;
    # parity lives in the selector/catalog mapping, not a new Turn host kind.
    "generic-cli": "opencode_goal_loop",
}

# Host identities accepted by the public host-mode planner must own a concrete
# visible connector. This is deliberately narrower than the Turn driver's
# SUPPORTED_HOSTS: headless-only built-in hosts such as dsh do not become
# visible identities merely by registering an execution backend.
SUPPORTED_TURN_HOST_IDENTITIES = sorted(VISIBLE_HOST_CONNECTOR_IDS)

# Host identities that are valid visible selections but map to the OpenCode
# goal loop connector rather than their own Turn host kind.
VISIBLE_OPENCODE_ALIASES: dict[str, str] = {
    "opencode": "generic-cli",
    "open-code": "generic-cli",
    "opencode2": "generic-cli",
    "opencode-2": "generic-cli",
}

# Pi runs its visible goal loop through the generic-cli Turn host, same as
# OpenCode, but keeps its own goal-loop connector identity.
VISIBLE_PI_ALIASES: dict[str, str] = {
    "pi": "generic-cli",
}

# Connector id override for source host identities that alias onto the
# generic-cli Turn host. The normalized Turn host stays generic-cli while the
# visible connector keeps naming the real host loop.
VISIBLE_CONNECTOR_OVERRIDES: dict[str, str] = {
    "pi": "pi_goal_loop",
}


CAPABILITY_GUIDANCE = {
    CAP_VISIBLE_SESSION: "A visible session is required so the user can watch, steer, or take over safely.",
    CAP_LOOPX_TURN: "LoopX Turn support is required before headless execution can be trusted.",
    CAP_TYPED_HOST_ADAPTER: "A typed host adapter is required so the host returns one machine-checkable result instead of free-form prose.",
    CAP_INDEPENDENT_VALIDATOR: "An independent validator is required so LoopX does not trust the host's own completion claim.",
    CAP_CHAT_GATEWAY: "A chat or webhook gateway is required before work can be created from an external intake surface.",
    CAP_SERVICE_TIMER: "A service timer, cron, launchd, or equivalent wake mechanism is required for timer keepalive.",
    CAP_SHELL: "Shell execution is required so the host can run LoopX preview and guard commands.",
}


class HostModePlanError(ValueError):
    """Raised for unknown/empty intent, capability, or host selector input."""

    def __init__(self, *, reason: str, field: str, suggestions: list[str] | None = None) -> None:
        self.reason = reason
        self.field = field
        self.suggestions = suggestions or []
        super().__init__(reason)

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": False,
            "schema_version": "host_mode_plan_error_v0",
            "error_kind": "invalid_workflow_intent_or_capability",
            "field": self.field,
            "reason": self.reason,
            "suggestions": self.suggestions,
        }


def _normalize_tokens(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    tokens: list[str] = []
    for value in values:
        token = str(value or "").strip().lower().replace("-", "_")
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _normalize_supported(values: Any, *, field: str, supported: list[str], required: bool) -> list[str]:
    tokens = _normalize_tokens(values)
    if required and not tokens:
        raise HostModePlanError(reason=f"{field} is required", field=field, suggestions=supported)
    unknown = [token for token in tokens if token not in supported]
    if unknown:
        raise HostModePlanError(
            reason=f"unsupported {field}: {', '.join(unknown)}",
            field=field,
            suggestions=supported,
        )
    return tokens


def _missing_mode_capabilities(mode: str, host_capabilities: list[str]) -> list[str]:
    if mode == MODE_HYBRID_HANDOFF:
        return []
    required = _MODE_METADATA[mode]["required_capabilities"]
    return [capability for capability in required if capability not in host_capabilities]


def _ready_concrete_modes(
    host_capabilities: list[str], host_identity: str | None = None
) -> list[str]:
    return [
        candidate
        for candidate in CANONICAL_MODES
        if candidate != MODE_HYBRID_HANDOFF
        and not _missing_mode_capabilities(candidate, host_capabilities)
        and not (
            candidate == MODE_VISIBLE_TUI
            and host_identity not in VISIBLE_HOST_CONNECTOR_IDS
        )
    ]


def _mode_capability_ready(
    mode: str, host_capabilities: list[str], host_identity: str | None = None
) -> bool:
    if mode == MODE_VISIBLE_TUI and host_identity not in VISIBLE_HOST_CONNECTOR_IDS:
        # Without an explicit catalog-registered host identity, a generic
        # visible_session cannot establish which host is present; fail closed.
        return False
    if mode == MODE_HYBRID_HANDOFF:
        return len(_ready_concrete_modes(host_capabilities, host_identity)) >= 2
    return not _missing_mode_capabilities(mode, host_capabilities)


def _turn_plan_command(
    *,
    goal_id: str,
    agent_id: str | None,
    mode: str,
    cli_bin: str,
    available_capabilities: list[str] | None,
    host_identity: str | None,
    pin_host: bool = True,
) -> str | None:
    meta = _MODE_METADATA[mode]
    turn_host = meta.get("turn_host")
    execution_mode = meta.get("turn_execution_mode")
    if not turn_host or not execution_mode:
        return None
    if mode == MODE_VISIBLE_TUI:
        # Visible mode requires an explicit, catalog-registered host identity.
        # Without it, a coarse `visible_session` capability cannot distinguish
        # Codex CLI, Claude Code, or OpenCode, so claiming any concrete host
        # would fabricate attribution.
        if not host_identity:
            raise HostModePlanError(
                reason=(
                    "visible_tui requires an explicit host_identity; a generic "
                    "visible_session capability cannot identify the actual host"
                ),
                field="host_identity",
                suggestions=sorted(VISIBLE_HOST_CONNECTOR_IDS),
            )
        if host_identity not in VISIBLE_HOST_CONNECTOR_IDS:
            raise HostModePlanError(
                reason=(
                    f"host_identity {host_identity!r} has no registered catalog "
                    "connector for visible mode"
                ),
                field="host_identity",
                suggestions=sorted(VISIBLE_HOST_CONNECTOR_IDS),
            )
        turn_host = host_identity
    if turn_host not in SUPPORTED_HOSTS:
        raise HostModePlanError(
            reason=f"unsupported Turn host mapped by {mode}: {turn_host}",
            field="mode",
            suggestions=CANONICAL_MODES,
        )
    agent_arg = f" --agent-id {shell_arg(agent_id)}" if agent_id else ""
    capability_args = "".join(
        f" --available-capability {shell_arg(capability)}"
        for capability in (available_capabilities or [])
    )
    scheduler_owner = meta.get("scheduler_owner")
    scheduler_arg = f" --scheduler-owner {shell_arg(scheduler_owner)}" if scheduler_owner else ""
    host_arg = f" --host {shell_arg(turn_host)}" if pin_host else ""
    return (
        f"{shell_arg(cli_bin)} turn plan --goal-id {shell_arg(goal_id)}"
        f"{agent_arg}{host_arg} --execution-mode {shell_arg(execution_mode)}"
        f"{scheduler_arg}{capability_args}"
    )


def _scheduler_context(mode: str, host_identity: str | None = None) -> dict[str, Any] | None:
    meta = _MODE_METADATA[mode]
    turn_host = meta.get("turn_host")
    execution_mode = meta.get("turn_execution_mode")
    if mode == MODE_VISIBLE_TUI:
        if host_identity in VISIBLE_HOST_CONNECTOR_IDS:
            turn_host = host_identity
        else:
            # No honest host binding exists without an explicit registered
            # identity; do not project a fabricated host context.
            return None
    if not turn_host or not execution_mode:
        if mode == MODE_IM_GATEWAY:
            return None
        if mode == MODE_HYBRID_HANDOFF:
            return None
    resolution = scheduler_execution_context_for_turn(
        host=str(turn_host),
        execution_mode=str(execution_mode),
        scheduler_owner=meta.get("scheduler_owner"),
    )
    return resolution.projection()


def _mode_quota_guard(
    *,
    mode: str,
    goal_id: str,
    agent_id: str | None,
    cli_bin: str,
    available_capabilities: list[str] | None,
    host_identity: str | None = None,
) -> str:
    scheduler_context = _scheduler_context(mode, host_identity)
    runtime_profile = None
    if mode == MODE_IM_GATEWAY:
        runtime_profile = None
    elif scheduler_context is None and mode == MODE_HYBRID_HANDOFF:
        scheduler_context = GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
    return render_quota_guard_command(
        goal_id,
        cli_bin=cli_bin,
        agent_id=agent_id,
        available_capabilities=available_capabilities,
        runtime_profile=runtime_profile,
        scheduler_execution_context=scheduler_context,
    )


def _blocking_reasons(
    mode: str, host_capabilities: list[str], host_identity: str | None = None
) -> list[str]:
    if mode == MODE_HYBRID_HANDOFF:
        ready_modes = _ready_concrete_modes(host_capabilities, host_identity)
        if len(ready_modes) >= 2:
            return []
        return [
            "Hybrid handoff requires at least two concrete ready modes so work can move between runtime surfaces safely."
        ]
    return [
        CAPABILITY_GUIDANCE[capability]
        for capability in _missing_mode_capabilities(mode, host_capabilities)
    ]


def _recommended_next_steps(
    *,
    mode: str,
    host_capabilities: list[str],
    turn_plan_command: str | None,
    quota_guard_command: str,
    host_identity: str | None = None,
) -> list[dict[str, Any]]:
    missing = _missing_mode_capabilities(mode, host_capabilities)
    steps: list[dict[str, Any]] = []
    if mode == MODE_HYBRID_HANDOFF:
        ready_modes = _ready_concrete_modes(host_capabilities, host_identity)
        if len(ready_modes) < 2:
            steps.append(
                {
                    "step": 1,
                    "kind": "stop",
                    "action": "Do not plan a hybrid handoff yet; make at least two concrete host modes ready first.",
                    "command": None,
                    "no_spend": True,
                }
            )
        else:
            steps.append(
                {
                    "step": 1,
                    "kind": "preview",
                    "action": "Preview the target mode before changing host surfaces.",
                    "command": quota_guard_command,
                    "no_spend": True,
                }
            )
        steps.append(
            {
                "step": len(steps) + 1,
                "kind": "handoff_gate",
                "action": "Escalate back to visible_tui whenever a user gate, ambiguous decision, missing validator, or risky action appears.",
                "command": None,
                "no_spend": True,
            }
        )
        return steps

    if missing:
        steps.append(
            {
                "step": 1,
                "kind": "stop",
                "action": "Stop before attempting this host mode; fill the missing host capabilities first.",
                "command": None,
                "no_spend": True,
            }
        )
        for capability in missing:
            steps.append(
                {
                    "step": len(steps) + 1,
                    "kind": "capability_gap",
                    "capability": capability,
                    "action": CAPABILITY_GUIDANCE[capability],
                    "command": None,
                    "no_spend": True,
                }
            )
        steps.append(
            {
                "step": len(steps) + 1,
                "kind": "repreview",
                "action": "Re-run loopx host-mode-plan after the missing capabilities are configured.",
                "command": None,
                "no_spend": True,
            }
        )
        return steps

    if mode == MODE_IM_GATEWAY:
        steps.append(
            {
                "step": 1,
                "kind": "intake",
                "action": "Create durable work from the chat or webhook intake surface; the gateway itself does not execute.",
                "command": None,
                "no_spend": True,
            }
        )
        steps.append(
            {
                "step": 2,
                "kind": "state_preview",
                "action": "Confirm current LoopX state is safe to proceed after intake is recorded.",
                "command": quota_guard_command,
                "no_spend": True,
            }
        )
        steps.append(
            {
                "step": 3,
                "kind": "handoff_gate",
                "action": "Choose an execution mode for the durable work: isolated_headless_turn for unattended runs, visible_tui when a user gate or ambiguous decision appears.",
                "command": None,
                "no_spend": True,
            }
        )
        return steps

    steps.append(
        {
            "step": 1,
            "kind": "state_preview",
            "action": "Confirm current LoopX state is safe to proceed before host setup.",
            "command": quota_guard_command,
            "no_spend": True,
        }
    )
    if turn_plan_command:
        steps.append(
            {
                "step": len(steps) + 1,
                "kind": "turn_preview",
                "action": "Preview the bounded run before launching or resuming any host execution.",
                "command": turn_plan_command,
                "no_spend": True,
            }
        )
    steps.append(
        {
            "step": len(steps) + 1,
            "kind": "handoff_gate",
            "action": "Escalate back to visible_tui whenever a user gate, ambiguous decision, missing validator, or risky action appears.",
            "command": None,
            "no_spend": True,
        }
    )
    return steps


def _build_mode_option(
    *,
    mode: str,
    goal_id: str,
    agent_id: str | None,
    host_capabilities: list[str],
    cli_bin: str,
    available_capabilities: list[str] | None,
    host_identity: str | None,
    connector_override_identity: str | None = None,
) -> dict[str, Any]:
    meta = _MODE_METADATA[mode]
    visible_unresolved = (
        mode == MODE_VISIBLE_TUI and host_identity not in VISIBLE_HOST_CONNECTOR_IDS
    )
    resolves_default_host = mode in RESOLVED_DEFAULT_TURN_HOST_MODES
    turn_plan_command = (
        None
        if visible_unresolved
        else _turn_plan_command(
            goal_id=goal_id,
            agent_id=agent_id,
            mode=mode,
            cli_bin=cli_bin,
            available_capabilities=available_capabilities,
            host_identity=host_identity,
            pin_host=not resolves_default_host,
        )
    )
    # A mode that previews the shipped resolution still names the pinned host it
    # would use instead, so an operator who wants the compatibility adapter path
    # reads one command rather than re-deriving the flags.
    turn_plan_command_rollback = (
        _turn_plan_command(
            goal_id=goal_id,
            agent_id=agent_id,
            mode=mode,
            cli_bin=cli_bin,
            available_capabilities=available_capabilities,
            host_identity=host_identity,
            pin_host=True,
        )
        if resolves_default_host
        else None
    )
    quota_guard_command = _mode_quota_guard(
        mode=mode,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
        available_capabilities=available_capabilities,
        host_identity=host_identity if host_identity in VISIBLE_HOST_CONNECTOR_IDS else None,
    )
    if mode == MODE_VISIBLE_TUI:
        connector_id = VISIBLE_CONNECTOR_OVERRIDES.get(
            connector_override_identity or ""
        ) or VISIBLE_HOST_CONNECTOR_IDS.get(host_identity or "")
        effective_turn_host = host_identity if connector_id else None
        host_resolution = (
            "resolved"
            if connector_id
            else (
                "identity_required"
                if host_identity is None
                else "unregistered_host_identity"
            )
        )
    else:
        connector_id = meta["connector_id"]
        effective_turn_host = meta.get("turn_host")
        host_resolution = "resolved"
    recommended_next_steps = (
        [
            {
                "step": 1,
                "kind": "stop",
                "action": (
                    "Re-run with --host-identity set to a catalog-registered visible host "
                    "(" + ", ".join(sorted(VISIBLE_HOST_CONNECTOR_IDS)) + ")."
                ),
                "command": None,
                "no_spend": True,
            }
        ]
        if visible_unresolved
        else _recommended_next_steps(
            mode=mode,
            host_capabilities=host_capabilities,
            turn_plan_command=turn_plan_command,
            quota_guard_command=quota_guard_command,
            host_identity=host_identity,
        )
    )
    return {
        "mode": mode,
        "summary": meta["summary"],
        "connector_id": connector_id,
        "host_resolution": host_resolution,
        "host_identity": host_identity if mode == MODE_VISIBLE_TUI else None,
        "capability_ready": _mode_capability_ready(mode, host_capabilities, host_identity),
        "required_host_capabilities": list(meta["required_capabilities"]),
        "missing_host_capabilities": _missing_mode_capabilities(mode, host_capabilities),
        "blocking_reasons": (
            _blocking_reasons(mode, host_capabilities, host_identity)
            if not (mode == MODE_VISIBLE_TUI and visible_unresolved)
            else [
                (
                    "visible_tui requires an explicit host_identity "
                    "(--host-identity); a generic visible_session capability "
                    "cannot identify the actual host."
                )
                if host_identity is None
                else (
                    f"host_identity {host_identity!r} has no registered catalog "
                    "connector for visible mode."
                )
            ]
        ),
        "recommended_next_steps": recommended_next_steps,
        "turn_mapping": {
            "host": effective_turn_host,
            "host_selection": (
                TURN_HOST_SELECTION_RESOLVED_DEFAULT
                if resolves_default_host
                else TURN_HOST_SELECTION_PINNED
            ),
            "execution_mode": meta.get("turn_execution_mode"),
            "scheduler_owner": meta.get("scheduler_owner"),
            "plan_command": turn_plan_command,
            "plan_command_rollback": turn_plan_command_rollback,
        },
        "scheduler_execution_context": _scheduler_context(mode, host_identity),
        "quota_guard_command": quota_guard_command,
        "required_proofs": [*_SHARED_PROOFS, *meta["extra_proofs"]],
    }


def _transitions(
    *,
    goal_id: str,
    agent_id: str | None,
    cli_bin: str,
    available_capabilities: list[str] | None,
    host_identity: str | None,
) -> list[dict[str, Any]]:
    def target_turn_command(to_mode: str) -> str | None:
        try:
            return _turn_plan_command(
                goal_id=goal_id,
                agent_id=agent_id,
                mode=to_mode,
                cli_bin=cli_bin,
                available_capabilities=available_capabilities,
                host_identity=host_identity,
                pin_host=to_mode not in RESOLVED_DEFAULT_TURN_HOST_MODES,
            )
        except HostModePlanError:
            # A visible target without a catalog-registered identity has no
            # honest preview command; emit none rather than a fabricated host.
            return None
    def transition(
        transition_id: str,
        from_mode: str,
        to_mode: str,
        trigger: str,
        target_readiness: list[str],
    ) -> dict[str, Any]:
        return {
            "transition": transition_id,
            "from_mode": from_mode,
            "to_mode": to_mode,
            "trigger": trigger,
            "preserves_agent_id": True,
            "spends_quota": False,
            "target_readiness": target_readiness,
            "target_turn_plan_command": target_turn_command(to_mode),
            "guard_command": _mode_quota_guard(
                mode=to_mode,
                goal_id=goal_id,
                agent_id=agent_id,
                cli_bin=cli_bin,
                available_capabilities=available_capabilities,
                host_identity=host_identity if host_identity in VISIBLE_HOST_CONNECTOR_IDS else None,
            ),
        }

    return [
        transition(
            "visible_bootstrap_to_isolated_headless_turn",
            MODE_VISIBLE_TUI,
            MODE_ISOLATED_HEADLESS_TURN,
            "the user closes the visible session but wants bounded work to continue",
            [CAP_LOOPX_TURN, CAP_TYPED_HOST_ADAPTER, CAP_INDEPENDENT_VALIDATOR],
        ),
        transition(
            "isolated_headless_turn_to_visible_tui_escalation",
            MODE_ISOLATED_HEADLESS_TURN,
            MODE_VISIBLE_TUI,
            "a user gate or ambiguous decision requires visible steering",
            [CAP_VISIBLE_SESSION, "clear_gate_handling"],
        ),
        transition(
            "im_gateway_to_isolated_headless_turn",
            MODE_IM_GATEWAY,
            MODE_ISOLATED_HEADLESS_TURN,
            "chat intake has created durable work that can run through LoopX Turn",
            [CAP_LOOPX_TURN, CAP_TYPED_HOST_ADAPTER, CAP_INDEPENDENT_VALIDATOR],
        ),
        transition(
            "shell_service_to_visible_tui_escalation",
            MODE_SHELL_SERVICE,
            MODE_VISIBLE_TUI,
            "timer-owned work hits a user gate, missing validator, or risky action",
            [CAP_VISIBLE_SESSION, "clear_gate_handling"],
        ),
    ]


def build_host_mode_plan(
    *,
    goal_id: str,
    user_intent: Any,
    host_capabilities: Any = None,
    agent_id: str | None = None,
    registered_agents: list[str] | None = None,
    cli_bin: str = "loopx",
    available_capabilities: list[str] | None = None,
    host_identity: str | None = None,
) -> dict[str, Any]:
    """Build a read-only host-mode selector plan on top of LoopX Turn.

    This selector chooses the user-facing host mode and prints the matching
    connector/Turn preview commands. LoopX Turn, quota, todo projection, and
    run history remain authoritative; the selector never launches a host, writes
    state, validates work, or spends quota.
    """

    intents = _normalize_supported(
        user_intent, field="user_intent", supported=SUPPORTED_INTENTS, required=True
    )
    caps = _normalize_supported(
        host_capabilities,
        field="host_capabilities",
        supported=SUPPORTED_HOST_CAPABILITIES,
        required=False,
    )
    available = _normalize_tokens(available_capabilities)
    normalized_host_identity = None
    connector_override_identity = None
    if host_identity:
        # Host identities are Turn host kinds and already use dashes
        # (codex-cli, claude-code, generic-cli); normalize case without
        # converting dashes to underscores. OpenCode and Pi alias onto the
        # generic-cli Turn host while keeping their own connector identity.
        raw_identity = str(host_identity).strip().lower()
        candidate = VISIBLE_OPENCODE_ALIASES.get(raw_identity, raw_identity)
        candidate = VISIBLE_PI_ALIASES.get(candidate, candidate)
        if candidate not in SUPPORTED_TURN_HOST_IDENTITIES:
            raise HostModePlanError(
                reason=f"unsupported host_identity: {candidate}",
                field="host_identity",
                suggestions=SUPPORTED_TURN_HOST_IDENTITIES,
            )
        normalized_host_identity = candidate
        connector_override_identity = (
            raw_identity if raw_identity in VISIBLE_CONNECTOR_OVERRIDES else None
        )
    # Host-mode planning continues an existing goal; fresh identity creation
    # belongs to the onboarding entry points that opt into that policy.
    identity = _identity_state(
        agent_id=agent_id,
        registered_agents=registered_agents,
        fresh_agent_default=False,
    )
    raw_scoped = identity.get("selected_agent_id")
    scoped_agent_id = str(raw_scoped) if raw_scoped else None

    primary_mode = _INTENT_PRIMARY_MODE[intents[0]]
    ordered_modes = [primary_mode] + [mode for mode in CANONICAL_MODES if mode != primary_mode]
    mode_options = [
        _build_mode_option(
            mode=mode,
            goal_id=goal_id,
            agent_id=scoped_agent_id,
            host_capabilities=caps,
            cli_bin=cli_bin,
            available_capabilities=available,
            host_identity=normalized_host_identity,
            connector_override_identity=connector_override_identity,
        )
        for mode in ordered_modes
    ]
    selected = mode_options[0]

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "mode": "dry_run_host_mode_selector",
        "agent_model": "peer_v1",
        "goal_id": goal_id,
        "agent_id": scoped_agent_id,
        "user_intent": intents,
        "host_capabilities": caps,
        "host_identity": normalized_host_identity,
        "selected_mode": primary_mode,
        "selected_connector_id": selected["connector_id"],
        "selected_turn_mapping": selected["turn_mapping"],
        "selected_capability_ready": selected["capability_ready"],
        "selected_missing_host_capabilities": selected["missing_host_capabilities"],
        "selected_blocking_reasons": selected["blocking_reasons"],
        "operator_next_steps": selected["recommended_next_steps"],
        "mode_options": mode_options,
        "identity_contract": identity,
        "guard_command": selected["quota_guard_command"],
        "next_preview_command": (
            selected["turn_mapping"].get("plan_command")
            or selected["quota_guard_command"]
            if not (
                primary_mode == MODE_VISIBLE_TUI
                and normalized_host_identity not in VISIBLE_HOST_CONNECTOR_IDS
            )
            else selected["quota_guard_command"]
        ),
        "no_spend_policy": {
            "selector_preview": True,
            "turn_plan_preview": True,
            "quiet_monitor_skip": True,
            "cadence_only_change": True,
            "readiness_or_final_check": True,
            "spends_only_after_validated_delivery_writeback": True,
        },
        "turn_contract": {
            "schema_version": "loopx_turn_v0",
            "role": "execution_authority_for_headless_modes",
            "plan_command_required_before_host_launch": True,
            "host_result_must_be_typed": True,
            "independent_validation_required": True,
            "writeback_before_quota_spend": True,
            "raw_session_or_transcript_publication_allowed": False,
        },
        "transitions": _transitions(
            goal_id=goal_id,
            agent_id=scoped_agent_id,
            cli_bin=cli_bin,
            available_capabilities=available,
            host_identity=normalized_host_identity,
        ),
        "boundary": {
            "selector_is_authoritative": False,
            "turn_envelope_is_authoritative_for_execution": True,
            "starts_process": False,
            "writes_state": False,
            "spends_quota": False,
            "infers_production_permission": False,
            "infers_credential_access": False,
            "infers_destructive_authority": False,
            "adapter_neutral": True,
        },
        "truth_contract": {
            "source_of_truth": [
                "loopx_turn_v0",
                "turn_envelope",
                "registry",
                "quota_should_run",
                "todo_projection",
                "run_history",
                "runtime_connector_catalog",
            ],
            "selector_is_authoritative": False,
            "recompute_rule": "Recompute before each host setup or handoff.",
        },
    }


def render_host_mode_plan_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return "\n".join(
            [
                "# LoopX Host Mode Plan Error",
                "",
                f"- ok: `{payload.get('ok')}`",
                f"- error_kind: `{payload.get('error_kind')}`",
                f"- field: `{payload.get('field')}`",
                f"- reason: {payload.get('reason')}",
                f"- suggestions: `{', '.join(payload.get('suggestions') or [])}`",
            ]
        )
    identity = payload.get("identity_contract") if isinstance(payload.get("identity_contract"), dict) else {}
    lines = [
        "# LoopX Host Mode Plan",
        "",
        "This is a read-only selector on top of LoopX Turn and the runtime connector catalog.",
        "It chooses a host mode and prints preview commands; it does not execute work or write state.",
        "",
        f"- schema_version: `{payload.get('schema_version')}`",
        f"- mode: `{payload.get('mode')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- agent_id: `{payload.get('agent_id')}`",
        f"- selected_mode: `{payload.get('selected_mode')}`",
        f"- selected_connector_id: `{payload.get('selected_connector_id') or 'null'}`",
        f"- selected_capability_ready: `{payload.get('selected_capability_ready')}`",
        f"- host_identity: `{payload.get('host_identity')}`",
        f"- selected_missing_host_capabilities: `{', '.join(payload.get('selected_missing_host_capabilities') or [])}`",
        f"- user_intent: `{', '.join(payload.get('user_intent') or [])}`",
        f"- host_capabilities: `{', '.join(payload.get('host_capabilities') or [])}`",
        "",
    ]
    selected_ready = payload.get("selected_capability_ready") is True
    if selected_ready:
        lines.extend(
            [
                "## Next Preview Command",
                "",
                "```bash",
                str(payload.get("next_preview_command") or ""),
                "```",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Not Ready Yet",
                "",
                "Do not run this host mode yet. Fill the gaps below first; the preview command becomes meaningful only after the mode is ready.",
                "",
            ]
        )
    lines.extend(
        [
            "## Runtime Modes",
            "",
            "| mode | connector | ready | Turn host | execution mode | scheduler owner | summary |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for option in payload.get("mode_options") or []:
        if not isinstance(option, dict):
            continue
        turn = option.get("turn_mapping") if isinstance(option.get("turn_mapping"), dict) else {}
        lines.append(
            "| "
            f"`{option.get('mode')}` | "
            f"`{option.get('connector_id') or 'null'}` | "
            f"`{option.get('capability_ready')}` | "
            f"`{turn.get('host')}` | "
            f"`{turn.get('execution_mode')}` | "
            f"`{turn.get('scheduler_owner')}` | "
            f"{option.get('summary')} |"
        )
    selected_mode = payload.get("selected_mode")
    selected_option = next(
        (
            option
            for option in payload.get("mode_options") or []
            if isinstance(option, dict) and option.get("mode") == selected_mode
        ),
        None,
    )
    if selected_option:
        lines.extend(["", "## Selected Mode Required Proofs", ""])
        lines.extend(f"- {proof}" for proof in selected_option.get("required_proofs") or [])
    if payload.get("selected_blocking_reasons"):
        lines.extend(["", "## Blocking Reasons", ""])
        lines.extend(f"- {reason}" for reason in payload.get("selected_blocking_reasons") or [])
    if payload.get("operator_next_steps"):
        lines.extend(["", "## Operator Next Steps", ""])
        for step in payload.get("operator_next_steps") or []:
            if not isinstance(step, dict):
                continue
            line = f"{step.get('step')}. [{step.get('kind')}] {step.get('action')}"
            if step.get("command"):
                line += f" — `{step.get('command')}`"
            lines.append(line)
    lines.extend(["", "## Handoffs", ""])
    for transition in payload.get("transitions") or []:
        if isinstance(transition, dict):
            lines.append(
                f"- `{transition.get('transition')}`: "
                f"`{transition.get('from_mode')}` -> `{transition.get('to_mode')}`; "
                f"target readiness: `{', '.join(transition.get('target_readiness') or [])}`"
            )
    if identity.get("action_required"):
        lines.extend(
            [
                "",
                "## Identity Gate",
                "",
                f"- reason: {identity.get('reason')}",
                f"- required_cli_arg: `{identity.get('required_cli_arg')}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Why This Helps",
            "",
            "- makes visible, headless, intake, timer, and hybrid host choices explicit;",
            "- maps headless execution to `loopx turn plan` instead of inventing a parallel runner;",
            "- keeps scoped identity, no-spend previews, independent validation, and quota spend order visible before setup;",
            "- gives operators a safe escalation path back to a visible session for user gates.",
        ]
    )
    return "\n".join(lines)
