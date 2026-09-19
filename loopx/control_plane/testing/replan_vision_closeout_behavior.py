"""Hermetic file authoring and real-CLI closeout for required-vision qualification.

The executor does not author a decision or repair the actor's command. It only
confines file writes and observes the actual durable result. This is distinct
from the narrower progress-delta actor's acceptance boundary.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Callable

from .model_tool_behavior import argument_value, loopx_command_tokens
from .model_behavior_qualification import model_behavior_semantic_contract_from_packet
from ..quota.effective_action import EffectiveAction

if TYPE_CHECKING:
    from .replan_semantic_action_behavior import _QualificationState

# Full closeout includes evidence discovery, authoring, refresh and settlement;
# its resource bound is independent of the narrower single-action qualifier.
REQUIRED_VISION_CLOSEOUT_MAX_CALLS = 32


class VisionHostAdmissionRejected(ValueError):
    """Reject the current operation before execution, not prior compound steps."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.detail = detail


VISION_HOST_INSTRUCTION = (
    "You are an agent working in an isolated LoopX project. Follow the heartbeat "
    "and current control-plane packet. Use the shell normally to inspect evidence, "
    "author your decision and execute the real CLI writeback and settlement."
)

VISION_EXEC_TOOL_DESCRIPTION = (
    "Run a shell command in the project. Shell variables, pipelines, compound "
    "commands, Python and JSON editing are available. Returns stdout/stderr and "
    "exit_code, including errors so you can correct and retry. Project drafts and "
    "$TMPDIR are writable; fixture inputs and authority stores are protected. "
    "The loopx command uses the real checkout CLI for this isolated task. "
    "External networking and access to private host data are unavailable."
)


def required_vision_scenario_contract(
    source_packet: Mapping[str, Any], contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the source scenario and bind its full closeout acceptance."""
    semantics = model_behavior_semantic_contract_from_packet(source_packet, arm="full_packet")
    vision = semantics["vision_continuation"]
    trigger_kinds = set(vision.get("trigger_kinds", []))
    required = {
        "selected_todo_id": None,
        "user_action_required": False,
        "must_attempt_work": True,
        "quiet_noop_allowed": False,
    }
    if any(contract.get(field) != value for field, value in required.items()):
        raise ValueError("required-vision scenario must execute before quiet wait")
    if vision.get("required") is not True or "required_agent_vision_missing" not in trigger_kinds:
        raise ValueError("required-vision scenario must preserve the profile gap")
    if semantics["required_reads"]:
        raise ValueError("required-vision replan must not require a model read ritual")
    action_packet = source_packet.get("replan_action_packet")
    obligation = source_packet.get("autonomous_replan_obligation")
    if not (
        isinstance(action_packet, Mapping)
        and isinstance(obligation, Mapping)
        and action_packet.get("decision") == "replan_required"
        and action_packet.get("obligation_id") == obligation.get("obligation_id")
        and dict(obligation.get("replan_context") or {}).get("delivery") == "host_projected"
    ):
        raise ValueError("required-vision scenario must preserve host-delivered replan context")
    if semantics["scheduler_action"].get("action") != "run_now":
        raise ValueError("required-vision scenario must remain immediately runnable")
    return {
        "qualification_scope": "required_vision_closeout",
        "trigger_kinds": sorted({item["kind"] for item in obligation["triggers"]}),
        "required_semantic_outcomes": list(action_packet["uncovered_frontier"]["required_any_of"]),
        "vision_closeout": {
            "checkpoint_satisfied": True, "bound_writeback": True,
            "settled": True, "spend_count": 1, "original_obligation_closed": True,
        },
    }


def observe_shell_evidence(output: str, state: _QualificationState) -> None:
    """Observe returned source data, independent of the command used to read it."""
    expected = json.loads(state.fixture.work_source_target.read_text(encoding="utf-8"))
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if value == expected:
            state.work_source_read = True
            return


def _rows(state: _QualificationState) -> list[dict[str, Any]]:
    goal_id = str((state.quota_packet or {})["goal_id"])
    index = state.fixture.runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    return [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines() if line]


def dispatch_vision_closeout(
    command: str, state: _QualificationState, *, execute: Callable[..., str],
) -> tuple[str, str, bool] | None:
    tokens = loopx_command_tokens(command) or []
    if "refresh-state" not in tokens and "spend-slot" not in tokens:
        return None
    packet = state.quota_packet or {}
    binding = dict(dict(dict(packet.get("interaction_contract") or {}).get("cli_channel") or {}).get("replan_settlement_contract") or {}).get("settlement_binding") or {}
    if not binding or argument_value(tokens, binding["cli_argument"]) != binding["id"]:
        raise ValueError("vision_closeout_binding_mismatch")
    if argument_value(tokens, "--turn-instance-id") != state.turn_instance_id:
        raise ValueError("vision_closeout_turn_mismatch")
    if "refresh-state" in tokens:
        path = argument_value(tokens, "--agent-vision-json")
        if not path or not state.work_source_read:
            raise ValueError("vision_closeout_requires_observed_source_and_authored_decision")
        target = (state.fixture.project_root / path).resolve()
        if not target.is_relative_to(state.fixture.project_root.resolve()):
            raise ValueError("vision_authoring_path_outside_fixture")
        vision = json.loads(target.read_text(encoding="utf-8"))
        source_evidence = json.loads(state.fixture.frontier_target.read_text(encoding="utf-8"))["uncovered"]
        source_ref = state.fixture.work_source_target.relative_to(state.fixture.project_root).as_posix()
        observed_refs = {ref for item in source_evidence if item.get("source_ref") == source_ref
                         for ref in (item["evidence_id"], source_ref)}
        path_delta = vision.get("path_delta")
        refs = path_delta.get("evidence_refs") if isinstance(path_delta, dict) else None
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise ValueError("vision_closeout_evidence_not_observed")
        refs = set(refs)
        if not refs.intersection(observed_refs):
            raise VisionHostAdmissionRejected("vision_closeout_evidence_not_observed", "No refresh was executed: path_delta.evidence_refs must identify the source evidence actually read. Accepted references: " + json.dumps(sorted(observed_refs)))
        output = execute(command, fixture=state.fixture, turn_instance_id=state.turn_instance_id)
        row = _rows(state)[-1]
        semantic = dict(row.get("autonomous_replan_ack") or {}).get("semantic_delta") or {}
        checkpoint = dict(row.get("vision_checkpoint") or {})
        identity = dict(row.get("settlement_identity") or {})
        expected_identity = dict(packet.get("heartbeat_receipt") or {}).get("settlement_identity")
        if not (semantic.get("accepted") is True and semantic.get("obligation_id") == binding["id"]
                and checkpoint.get("satisfied") is True and identity == expected_identity):
            raise ValueError("vision_closeout_durable_writeback_incomplete")
        state.semantic_delta = semantic
        state.vision_closeout = {"checkpoint_satisfied": True, "bound_writeback": True, "settled": False}
        return output, "semantic_replan_writeback", False
    if state.vision_closeout is None:
        raise ValueError("vision_closeout_spend_before_writeback")
    output = execute(command, fixture=state.fixture, turn_instance_id=state.turn_instance_id)
    replay = json.loads(execute(state.fixture.quota_guard_command, fixture=state.fixture,
                                turn_instance_id=state.turn_instance_id))
    following = json.loads(execute(state.fixture.quota_guard_command, fixture=state.fixture,
                                   turn_instance_id=f"{state.turn_instance_id}-readback"))
    spends = [row for row in _rows(state) if row.get("classification") == "quota_slot_spent"]
    if replay.get("effective_action") != EffectiveAction.HEARTBEAT_SETTLED_SKIP.value or len(spends) != 1:
        raise ValueError("vision_closeout_settlement_readback_failed")
    remaining = dict(following.get("autonomous_replan_obligation") or {})
    remaining_kinds = {item["kind"] for item in remaining.get("triggers", [])}
    if remaining.get("obligation_id") == binding["id"] or remaining_kinds.intersection({"required_agent_vision_missing", "vision_checkpoint_missing"}):
        raise ValueError("vision_closeout_rearmed_after_settlement")
    # A real successor requirement may follow a newly authored continuing
    # vision. Do not force the model to declare as_needed or no_followup just
    # to obtain a quiet next wake.
    state.vision_closeout.update(settled=True, spend_count=1, original_obligation_closed=True)
    state.semantic_reentry_observation = {"effective_action": following.get("effective_action"),
                                          "trigger_kinds": sorted(remaining_kinds)}
    return output, "quota_spend_slot", True
