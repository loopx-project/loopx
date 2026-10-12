from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..work_items.interaction_contract import INTERACTION_CONTRACT_SCHEMA_VERSION


SCHEDULER_ARBITRATION_SCHEMA_VERSION = "scheduler_arbitration_v0"


class SchedulerDisposition(str, Enum):
    TERMINAL_STOP = "terminal_stop"
    PEER_COORDINATION_WAIT = "peer_coordination_wait"
    AGENT_MONITOR_ONLY_WAIT = "agent_monitor_only_wait"
    ACTIVE_WORK = "active_work"
    SETTLED_REPLAY = "settled_replay"
    AGENT_SCOPE_WAIT = "agent_scope_wait"
    CONSISTENCY_REPAIR = "consistency_repair"
    HUMAN_GATE = "human_gate"
    MONITOR_WAIT = "monitor_wait"
    QUIET_WAIT = "quiet_wait"
    UNCHANGED_WAIT = "unchanged_wait"


@dataclass(frozen=True)
class SchedulerArbitration:
    disposition: SchedulerDisposition
    reason_code: str
    mode: str
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def consistency_error(self) -> dict[str, Any] | None:
        if self.ok:
            return None
        return {
            "schema_version": SCHEDULER_ARBITRATION_SCHEMA_VERSION,
            "reason_code": self.reason_code,
            "mode": self.mode or None,
            "errors": list(self.errors),
            "repair_action": (
                "rebuild interaction_contract from the current quota decision, "
                "then rerun quota before applying scheduler cadence"
            ),
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _required_bool(
    source: Mapping[str, Any],
    field: str,
    *,
    error_prefix: str,
    errors: list[str],
) -> bool:
    value = source.get(field)
    if type(value) is not bool:
        errors.append(f"{error_prefix}.{field}_must_be_boolean")
        return False
    return value


def _classify_disposition(
    *,
    mode: str,
    user_required: bool,
    must_attempt: bool,
    quiet_noop_allowed: bool,
    agent_scope_frontier_actions: Collection[str],
    non_gating_notice_retry: bool = False,
) -> tuple[SchedulerDisposition, str]:
    agent_scope_modes = {str(value) for value in agent_scope_frontier_actions}
    if mode == "terminal_no_followup":
        return SchedulerDisposition.TERMINAL_STOP, mode
    if mode == "peer_coordination_blocked":
        return SchedulerDisposition.PEER_COORDINATION_WAIT, mode
    if mode == "agent_monitor_only":
        return SchedulerDisposition.AGENT_MONITOR_ONLY_WAIT, mode
    if user_required and not must_attempt and non_gating_notice_retry:
        return SchedulerDisposition.QUIET_WAIT, "blocked_wait"
    if user_required and not must_attempt:
        return SchedulerDisposition.HUMAN_GATE, "interaction_blocking_user_gate"
    if mode == "monitor_quiet_skip":
        return SchedulerDisposition.MONITOR_WAIT, "interaction_monitor_quiet_wait"
    if mode == "heartbeat_settled_skip":
        return SchedulerDisposition.SETTLED_REPLAY, "interaction_heartbeat_settled_replay"
    if mode == "successor_replan_required" and must_attempt:
        return SchedulerDisposition.ACTIVE_WORK, "interaction_successor_replan_required"
    if mode in agent_scope_modes:
        return SchedulerDisposition.AGENT_SCOPE_WAIT, "interaction_agent_scope_wait"
    if mode == "mapped_noop_if_unchanged":
        return SchedulerDisposition.UNCHANGED_WAIT, "interaction_unchanged_wait"
    if must_attempt:
        return SchedulerDisposition.ACTIVE_WORK, "interaction_agent_attempt_required"
    if quiet_noop_allowed:
        return SchedulerDisposition.QUIET_WAIT, "interaction_quiet_noop_allowed"
    return SchedulerDisposition.QUIET_WAIT, "interaction_delivery_not_allowed"


def build_scheduler_arbitration(
    payload: Mapping[str, Any],
    *,
    agent_scope_frontier_actions: Collection[str] = (),
) -> SchedulerArbitration:
    """Derive scheduler authority from the final interaction contract.

    Lower-level quota fields never participate in branch selection. Structural
    contradictions inside the final contract fail closed to projection repair.
    """

    errors: list[str] = []
    contract = _mapping(payload.get("interaction_contract"))
    schema_version = contract.get("schema_version")
    if schema_version != INTERACTION_CONTRACT_SCHEMA_VERSION:
        errors.append("interaction_contract.schema_version_mismatch")

    mode_value = contract.get("mode")
    mode = str(mode_value or "").strip()
    if not mode:
        errors.append("interaction_contract.mode_missing")

    user_channel = _mapping(contract.get("user_channel"))
    agent_channel = _mapping(contract.get("agent_channel"))
    if not user_channel:
        errors.append("interaction_contract.user_channel_missing")
    if not agent_channel:
        errors.append("interaction_contract.agent_channel_missing")

    user_required = _required_bool(
        user_channel,
        "action_required",
        error_prefix="interaction_contract.user_channel",
        errors=errors,
    )
    must_attempt = _required_bool(
        agent_channel,
        "must_attempt",
        error_prefix="interaction_contract.agent_channel",
        errors=errors,
    )
    delivery_allowed = _required_bool(
        agent_channel,
        "delivery_allowed",
        error_prefix="interaction_contract.agent_channel",
        errors=errors,
    )
    quiet_noop_allowed = _required_bool(
        agent_channel,
        "quiet_noop_allowed",
        error_prefix="interaction_contract.agent_channel",
        errors=errors,
    )

    from ..effect_runtime import effect_runtime_result

    cli_channel = _mapping(contract.get("cli_channel"))
    spend_flags = {field: _required_bool(
        cli_channel, field, error_prefix="interaction_contract.cli_channel", errors=errors,
    ) if field in cli_channel else False for field in ("spend_allowed_now", "spend_after_validation")}
    validation = effect_runtime_result("work_item.interaction_consistency.validate", {
        "mode": mode, "user_required": user_required, "must_attempt": must_attempt,
        "delivery_allowed": delivery_allowed, "quiet_noop_allowed": quiet_noop_allowed,
        **spend_flags,
    })
    if not isinstance(validation, dict) or not isinstance(validation.get("errors"), list) or any(
        not isinstance(error, str) for error in validation["errors"]
    ):
        raise TypeError("interaction consistency validation returned invalid diagnostics")
    errors.extend(validation["errors"])
    disposition, reason_code = _classify_disposition(
        mode=mode,
        user_required=user_required,
        must_attempt=must_attempt,
        quiet_noop_allowed=quiet_noop_allowed,
        agent_scope_frontier_actions=agent_scope_frontier_actions,
        non_gating_notice_retry=(user_channel.get("non_gating_notice") is True and
            _mapping(agent_channel.get("wait")).get("source") == "turn_settlement"),
    )

    if errors:
        return SchedulerArbitration(
            disposition=SchedulerDisposition.CONSISTENCY_REPAIR,
            reason_code="scheduler_interaction_contract_inconsistent",
            mode=mode,
            errors=tuple(dict.fromkeys(errors)),
        )
    return SchedulerArbitration(
        disposition=disposition,
        reason_code=reason_code,
        mode=mode,
    )
