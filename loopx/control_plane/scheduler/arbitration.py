from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..goals.goal_vision_wait import exact_blocked_successor_wait_state

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
    scheduled_recheck_after_seconds: int | None = None
    pending_dependency_wait: bool = False

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

    @classmethod
    def from_projection(cls, projection: Any) -> SchedulerArbitration:
        if (not isinstance(projection, Mapping)
                or any(not isinstance(projection.get(key), str) for key in ("disposition", "reason_code", "mode"))
                or not isinstance(projection.get("errors"), list)
                or any(not isinstance(error, str) for error in projection["errors"])):
            raise TypeError("TypeScript scheduler arbitration shape mismatch")
        scheduled = projection.get("scheduled_recheck_after_seconds")
        if scheduled is not None and (type(scheduled) is not int or scheduled <= 0):
            raise TypeError("TypeScript scheduler scheduled recheck must be positive integer seconds")
        pending_dependency = projection.get("pending_dependency_wait", False)
        if type(pending_dependency) is not bool:
            raise TypeError("TypeScript scheduler pending dependency wait must be boolean")
        return cls(SchedulerDisposition(projection["disposition"]), projection["reason_code"],
                   projection["mode"], tuple(projection["errors"]), scheduled, pending_dependency)


def _scheduler_arbitration_facts(
    contract: Any, agent_scope_frontier_actions: Collection[str] = (),
    *, payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep legacy Python scalar/Mapping codecs; transport only consumed facts."""
    contract = contract if isinstance(contract, Mapping) else {}
    channels = {}
    for name, fields in (
        ("user_channel", ("action_required", "non_gating_notice")),
        ("agent_channel", ("must_attempt", "delivery_allowed", "quiet_noop_allowed")),
        ("cli_channel", ("spend_allowed_now", "spend_after_validation")),
    ):
        source = contract.get(name)
        source = source if isinstance(source, Mapping) else {}
        channels[name] = {"present": bool(source), **{
            field: source[field] if type(source[field]) is bool else None
            for field in fields if field in source
        }}
    agent = contract.get("agent_channel")
    wait = agent.get("wait") if isinstance(agent, Mapping) else None
    source = wait.get("source") if isinstance(wait, Mapping) else None
    channels["agent_channel"]["wait_source"] = source if isinstance(source, str) and len(source) <= 64 else None
    facts = {"schema_version": contract.get("schema_version"),
            "mode": str(contract.get("mode") or "").strip(), **channels,
            "agent_scope_frontier_actions": [str(action) for action in agent_scope_frontier_actions]}
    wait = exact_blocked_successor_wait_state(payload)
    if wait:
        identity = payload.get("agent_identity")
        facts.update({"effective_action": payload.get("effective_action"),
                      "agent_id": identity.get("agent_id") if isinstance(identity, Mapping) else None,
                      "vision_wait_state": wait,
                      "heartbeat_receipt": payload.get("heartbeat_receipt")})
    return facts
