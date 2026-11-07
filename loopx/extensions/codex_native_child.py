"""Transient Codex host events adapted to native-child receipts.

Only opaque identities and typed outcomes reach the existing multi_subagent
log. Prompts, child messages and raw tool output are never retained.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..capabilities.multi_subagent.native_child_receipts import (
    _load_native_child_events, record_native_child,
)


def configured_native_child_limit(request: Mapping[str, Any]) -> int | None:
    envelope = request.get("turn_envelope")
    context = envelope.get("agent_context") if isinstance(envelope, Mapping) else None
    contributions = context.get("contributions") if isinstance(context, Mapping) else None
    if not isinstance(contributions, list):
        return None
    for contribution in contributions:
        if not isinstance(contribution, Mapping) or contribution.get("capability_id") != "multi_subagent":
            continue
        facts = contribution.get("facts")
        count = facts.get("max_children") if isinstance(facts, Mapping) else None
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            return count
    return None


class CodexNativeChildObserver:
    """Bound to one owned host invocation and one admitted LoopX Turn.

    The public recorder cannot select host provenance. Codex exec and app-server
    use different field casing; both are normalized here at the provider seam.
    Failed collab items lack a typed capacity error, so they remain host_failed.
    A host retry is recorded as observed fact, never authorized by this adapter.
    """

    def __init__(self, *, runtime_root: Path, lineage: Mapping[str, str],
                 turn_instance_id: str, configured_limit: int,
                 goal_ref: Mapping[str, Any] | None = None, registry_path: Path | None = None):
        self.runtime_root = runtime_root
        self.lineage = lineage
        self.turn_instance_id = turn_instance_id
        self.configured_limit = configured_limit
        self.goal_ref = goal_ref
        self.registry_path = registry_path

    def _record(self, *, stage: str, **record: Any) -> None:
        record_native_child(
            runtime_root=self.runtime_root, goal_id=self.lineage["goal_id"],
            agent_id=self.lineage["agent_id"], turn_instance_id=self.turn_instance_id,
            configured_limit=self.configured_limit, stage=stage,
            entrypoint_id="codex_native_tools" if stage == "decision" else None,
            execute=True, _host_observed=True, goal_ref=self.goal_ref,
            registry_path=self.registry_path, **record,
        )

    def _restore_operation(self, child: str, *, wait_ref: str) -> str | None:
        # Restore the latest successful host decision for this opaque child,
        # including followups and rows outside the presentation window. The
        # existing log supplies order and exact Goal/agent/Turn ownership.
        child_ref = "codex-child-" + hashlib.sha256(child.encode()).hexdigest()[:32]
        legacy_spawn = "codex-" + hashlib.sha256(child.encode()).hexdigest()[:32]
        events = _load_native_child_events(
            self.runtime_root, goal_id=self.lineage["goal_id"], agent_id=self.lineage["agent_id"],
            turn_instance_id=self.turn_instance_id, goal_ref=self.goal_ref,
            registry_path=self.registry_path,
        )
        # The first terminal observation owns this wait identity permanently.
        # Replayed waits must never reinterpret a later child association.
        for event in events:
            details = event.get("details")
            if (event.get("event_kind") == "native_child_result"
                    and isinstance(details, Mapping)
                    and details.get("observation_source") == "host_observed"
                    and details.get("host_wait_ref") == wait_ref):
                return str(event["case_id"])
        for event in reversed(events):
            details = event.get("details")
            if not isinstance(details, Mapping):
                continue
            if (event.get("event_kind") == "native_child_decision"
                    and details.get("operation") in {"spawn", "followup"}
                    and details.get("outcome") == "started"
                    and details.get("observation_source") == "host_observed"
                    and details.get("entrypoint_id") == "codex_native_tools"):
                if (details.get("host_child_ref_" + child_ref) is True
                        or (details.get("operation") == "spawn"
                            and event.get("case_id") == legacy_spawn)):
                    return str(event["case_id"])
        return None

    def observe(self, item: Mapping[str, Any], *, session_id: str, invocation_id: str) -> None:
        item_type = item.get("type")
        if item_type not in {"collab_tool_call", "collabAgentToolCall"}:
            return
        snake = item_type == "collab_tool_call"
        sender = item.get("sender_thread_id" if snake else "senderThreadId")
        status = item.get("status")
        native_id = item.get("id")
        if sender != session_id or not isinstance(native_id, str) or not native_id or status not in {"completed", "failed"}:
            return
        tool = {"spawn_agent": "spawn", "spawnAgent": "spawn", "send_input": "followup",
                "sendInput": "followup", "resumeAgent": "followup", "wait": "wait"}.get(item.get("tool"))
        if tool is None:
            return
        receivers = item.get("receiver_thread_ids" if snake else "receiverThreadIds")
        decision_id: str | None = None
        if tool != "wait":
            started = status == "completed" and isinstance(receivers, list) and bool(receivers)
            if started and any(not isinstance(child, str) or not child for child in receivers):
                return
            # Successful spawn identity survives host event replay/restart; host
            # exec display-item counters alone are not globally unique.
            if not invocation_id:
                raise ValueError("native child decision requires its owned host invocation")
            identity = (receivers[0] if tool == "spawn" and started else
                        json.dumps([session_id, invocation_id, native_id], separators=(",", ":")))
            operation_id = "codex-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
            self._record(stage="decision", operation_id=operation_id, operation=tool,
                         outcome="started" if started else "host_failed",
                         **({"reason_code": "host_failed"} if not started else {
                             "_host_child_refs": sorted({"codex-child-" + hashlib.sha256(child.encode()).hexdigest()[:32]
                                                         for child in receivers})}))
            if started:
                decision_id = operation_id
        states = item.get("agents_states" if snake else "agentsStates")
        if not isinstance(states, Mapping):
            return
        for child, state in states.items():
            if not isinstance(child, str) or not child or not isinstance(state, Mapping):
                continue
            # A spawn/followup snapshot belongs to that stable decision, even
            # when replayed after later work. A wait first restores its own
            # consumed binding, then falls back to the latest child operation.
            if tool != "wait" and (not isinstance(receivers, list) or child not in receivers):
                continue
            outcome = {"completed": "completed", "errored": "failed", "shutdown": "cancelled"}.get(state.get("status"))
            if outcome is None:
                continue
            wait_ref = None
            if tool == "wait":
                if not invocation_id:
                    raise ValueError("native child wait requires its owned host invocation")
                identity = json.dumps([session_id, invocation_id, native_id, child], separators=(",", ":"))
                wait_ref = "codex-wait-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
            operation_id = self._restore_operation(child, wait_ref=wait_ref) if wait_ref else decision_id
            if operation_id is not None:
                self._record(stage="result", operation_id=operation_id, outcome=outcome,
                             _host_wait_ref=wait_ref)


def native_child_observer(request: Mapping[str, Any], *, runtime_root: Path,
                          lineage: Mapping[str, str],
                          registry_path: Path | None = None) -> CodexNativeChildObserver | None:
    limit = configured_native_child_limit(request)
    turn = request.get("turn_instance_id")
    if limit is None or not isinstance(turn, str) or not turn:
        return None
    goal_ref = request.get("goal_ref")
    return CodexNativeChildObserver(runtime_root=runtime_root, lineage=lineage,
                                    turn_instance_id=turn, configured_limit=limit,
                                    goal_ref=goal_ref if isinstance(goal_ref, Mapping) else None,
                                    registry_path=registry_path)
