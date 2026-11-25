"""Semantic replan behavior actor and qualification loop over the real LoopX CLI."""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from ..goals.goal_frontier.terminal import _projection_has_empty_terminal_frontier
from ..quota.effective_action import EffectiveAction
from ..work_items.progress_result import ProgressResultClass
from .model_tool_behavior import (
    _module_reader_tokens,
    DoubaoExecToolBehaviorActorBase,
    DoubaoExecToolClient,
    ExecToolCall,
    LoopxCliExecutionError,
    QuotaToolContext,
    argument_value,
    append_exec_tool_exchange,
    execute_loopx_cli,
    is_context_read_command,
    loopx_command_tokens,
    qualification_turn_instance_id,
    replan_view_command_tokens,
)
from .replan_semantic_action_fixture import (
    JsonMapping as JsonMapping,
    JsonObject as JsonObject,
    REPLAN_SEMANTIC_ACTION_BEHAVIOR_MAX_CALLS as REPLAN_SEMANTIC_ACTION_BEHAVIOR_MAX_CALLS,
    REPLAN_SEMANTIC_ACTION_BEHAVIOR_RECEIPT_SCHEMA_VERSION as REPLAN_SEMANTIC_ACTION_BEHAVIOR_RECEIPT_SCHEMA_VERSION,
    REPLAN_SEMANTIC_ACTION_NATIVE_READ_MAX_CALLS as REPLAN_SEMANTIC_ACTION_NATIVE_READ_MAX_CALLS,
    _FIXTURE_AGENT_ID as _FIXTURE_AGENT_ID,
    _FIXTURE_COMPOSITION_EXPERIMENT_ID as _FIXTURE_COMPOSITION_EXPERIMENT_ID,
    _FIXTURE_FRONTIER_FILE as _FIXTURE_FRONTIER_FILE,
    _FIXTURE_GOAL_ID as _FIXTURE_GOAL_ID,
    _FIXTURE_NEW_EVIDENCE_ID as _FIXTURE_NEW_EVIDENCE_ID,
    _FIXTURE_NEW_HYPOTHESIS_ID as _FIXTURE_NEW_HYPOTHESIS_ID,
    _FIXTURE_NEW_PROBE_KIND as _FIXTURE_NEW_PROBE_KIND,
    _FIXTURE_NEW_SURFACE_ID as _FIXTURE_NEW_SURFACE_ID,
    _FIXTURE_SOURCE_FILE as _FIXTURE_SOURCE_FILE,
    _FIXTURE_WORK_ITEM_ID as _FIXTURE_WORK_ITEM_ID,
    _FIXTURE_WORK_ITEM_OPERATION_ID as _FIXTURE_WORK_ITEM_OPERATION_ID,
    _MAX_HOST_TOOL_ERROR_OUTPUT_CHARS as _MAX_HOST_TOOL_ERROR_OUTPUT_CHARS,
    _ORDINARY_HOST_SYSTEM_PROMPT as _ORDINARY_HOST_SYSTEM_PROMPT,
    _HostToolError as _HostToolError,
    _QualificationState as _QualificationState,
    _ReplanSemanticActionFixture as _ReplanSemanticActionFixture,
    _as_mapping as _as_mapping,
    _assert_vision_trigger as _assert_vision_trigger,
    _authored_vision as _authored_vision,
    _bounded_refresh_state_help_limit as _bounded_refresh_state_help_limit,
    _bounded_workspace_read_plan as _bounded_workspace_read_plan,
    _build_fixture as _build_fixture,
    _clock_output as _clock_output,
    _exact_nonbool_int as _exact_nonbool_int,
    _execute_workspace_read as _execute_workspace_read,
    _expected_obligation_id as _expected_obligation_id,
    _expected_replan_view_commands as _expected_replan_view_commands,
    _fixture_runs as _fixture_runs,
    _is_quota_guard as _is_quota_guard,
    _is_replan_successor_create as _is_replan_successor_create,
    _json_object as _json_object,
    _optional_mapping as _optional_mapping,
    _qualification_receipt as _qualification_receipt,
    _quota_behavior_observation as _quota_behavior_observation,
    _record_tool_step as _record_tool_step,
    _require_observed_frontier as _require_observed_frontier,
    _required_explore_read_command as _required_explore_read_command,
    _resolve_shell_cli_command as _resolve_shell_cli_command,
    _successor_reentry_observation as _successor_reentry_observation,
)
from .replan_vision_closeout_behavior import (
    REQUIRED_VISION_CLOSEOUT_MAX_CALLS,
    VISION_EXEC_TOOL_DESCRIPTION,
    VISION_HOST_INSTRUCTION,
    dispatch_vision_closeout,
    observe_shell_evidence,
)
from .vision_shell_host import VisionShellHost

_NATIVE_CONTEXT_READ_KINDS = frozenset({"context_read", "replan_context_read"})

_EXPECTED_BEHAVIOR_FAILURES = frozenset(
    {
        "repeated_explore_turn_context", "required_explore_turn_context_missing",
        "repeated_quota_should_run", "repeated_clock", "semantic_action_before_quota",
        "semantic_action_before_delivery_context_read",
        "semantic_action_before_replan_context_read", "semantic_action_before_frontier_read",
        "semantic_action_before_work_source_read",
        "semantic_action_before_replan_writeback_read",
        "semantic_action_misses_uncovered_frontier", "refresh_state_help_before_quota",
        "refresh_state_help_before_frontier_read", "refresh_state_help_before_work_source_read",
        "refresh_state_help_before_replan_writeback_read", "repeated_refresh_state_help",
        "successor_create_before_quota", "successor_create_before_delivery_context_read",
        "successor_create_before_replan_context_read", "successor_create_before_frontier_read",
        "successor_create_before_work_source_read",
        "successor_create_before_replan_writeback_read",
        "successor_create_obligation_mismatch", "successor_create_turn_mismatch",
        "successor_create_composition_binding_mismatch", "repeated_successor_create",
        "successor_create_failed", "successor_transition_missing",
        "successor_reentry_quota_invalid", "successor_reentry_selected_todo_missing",
        "successor_reentry_selected_todo_mismatch", "successor_reentry_replan_not_closed",
        "successor_reentry_not_runnable", "successor_reentry_composition_not_scheduled",
        "quota_obligation_missing", "semantic_writeback_failed",
        "semantic_writeback_binding_mismatch", "semantic_writeback_requires_authored_draft",
        "semantic_writeback_draft_outside_fixture",
        "semantic_writeback_durable_receipt_missing", "semantic_writeback_settlement_missing",
        "semantic_spend_before_writeback", "semantic_spend_identity_mismatch",
        "semantic_settlement_readback_failed", "semantic_reentry_quota_invalid",
        "semantic_reentry_replan_not_closed", "semantic_reentry_not_future_monitor_wait",
        "semantic_reentry_did_not_exit", "manual_evidence_read_is_not_replan",
        "replan_context_read_before_quota", "replan_context_read_before_delivery_context",
        "replan_writeback_read_before_review", "repeated_replan_context_read",
        "repeated_replan_writeback_read", "replan_writeback_read_invalid",
        "unexpected_replan_context_read", "replan_context_read_invalid", "unexpected_command",
    }
)


def _progress_observation_from_command(command: str) -> JsonObject | None:
    tokens = loopx_command_tokens(command)
    if not tokens or "refresh-state" not in tokens:
        return None
    if (
        argument_value(tokens, "--goal-id") != _FIXTURE_GOAL_ID
        or argument_value(tokens, "--agent-id") != _FIXTURE_AGENT_ID
        or argument_value(tokens, "--progress-scope") != "agent_lane"
    ):
        raise ValueError("replan writeback must target the fixture agent lane")
    result_class = argument_value(tokens, "--progress-result-class")
    if result_class not in {item.value for item in ProgressResultClass}:
        raise ValueError("replan writeback must carry a typed result class")
    observation: JsonObject = {
        "schema_version": "typed_progress_observation_v0",
        "result_class": result_class,
        "work_item_id": _FIXTURE_WORK_ITEM_ID,
    }
    for option, field in (
        ("--progress-surface-id", "surface_id"), ("--progress-hypothesis-id", "hypothesis_id"),
        ("--progress-probe-kind", "probe_kind"), ("--progress-blocker-id", "blocker_id"),
        ("--progress-coverage-scope-id", "coverage_scope_id"),
    ):
        if value := argument_value(tokens, option):
            observation[field] = value
    evidence_ids = [
        tokens[index + 1] for index, token in enumerate(tokens[:-1]) if token == "--progress-evidence-id"
    ]
    if evidence_ids:
        observation["evidence_ids"] = evidence_ids
    if "--progress-coverage-complete" in tokens:
        observation["coverage_complete"] = True
    return observation


def _semantic_reentry_observation(
    packet: JsonMapping, *, obligation_id: str, result_class: str,
) -> JsonObject:
    obligation = packet.get("autonomous_replan_obligation")
    if (
        (isinstance(obligation, Mapping) and obligation.get("obligation_id") == obligation_id)
        or packet.get("decision") == "autonomous_replan_required"
    ):
        raise ValueError("semantic_reentry_replan_not_closed")
    if packet.get("decision") != "skip":
        raise ValueError("semantic_reentry_did_not_exit")
    # Preserve strict presence/type checks on normalized_progress and monitor_only_lanes;
    # missing, None, or non-mapping fields must never be coerced into zero-open evidence.
    frontier = _optional_mapping(packet.get("goal_frontier_projection"))
    if frontier is None:
        raise ValueError("semantic_reentry_not_future_monitor_wait")
    normalized_progress = _optional_mapping(frontier.get("normalized_progress"))
    monitor_lanes = _optional_mapping(frontier.get("monitor_only_lanes"))
    if normalized_progress is None or monitor_lanes is None:
        raise ValueError("semantic_reentry_not_future_monitor_wait")
    quiet_skip = (
        packet.get("effective_action") == EffectiveAction.MONITOR_QUIET_SKIP.value
        and frontier.get("replan_required") is False
    )
    monitor_open = _exact_nonbool_int(normalized_progress, "agent_monitor_open_count")
    monitor_due = _exact_nonbool_int(normalized_progress, "agent_monitor_due_count")
    future_monitor_wait = bool(
        quiet_skip
        and monitor_lanes.get("present") is True
        and monitor_open is not None
        and monitor_open > 0
        and monitor_due == 0
    )
    terminal_coverage_exit = bool(
        quiet_skip
        and result_class in {
            ProgressResultClass.EXPLORATION_EXHAUSTED.value,
            ProgressResultClass.NO_FOLLOWUP.value,
        }
        and _projection_has_empty_terminal_frontier(dict(frontier))
    )
    if not future_monitor_wait and not terminal_coverage_exit:
        raise ValueError("semantic_reentry_not_future_monitor_wait")
    return {
        "decision": packet.get("decision"),
        "effective_action": packet.get("effective_action"),
        "replan_rule": "future_monitor_wait" if future_monitor_wait else "terminal_coverage_exhausted",
        "replan_closed": True,
        "exited": True,
    }


def _execute_loopx(
    command: str, *, fixture: _ReplanSemanticActionFixture, turn_instance_id: str,
) -> str:
    tokens = loopx_command_tokens(command)
    if tokens is None:
        raise ValueError("fixture requires one bounded LoopX command")
    # Scope adapter context even when a projected command relies on cwd defaults.
    # Never supply missing semantic/binding fields on the actor's behalf.
    if "--registry" not in tokens:
        tokens[1:1] = ["--registry", str(fixture.global_registry_path)]
    if "refresh-state" in tokens:
        tokens.extend(["--no-global-sync", "--suppress-external-sinks"])
    try:
        return execute_loopx_cli(
            shlex.join(tokens), source_root=fixture.source_root, project_root=fixture.project_root,
            argument_overrides={
                "--registry": str(fixture.global_registry_path),
                "--runtime-root": str(fixture.runtime_root),
                "--turn-instance-id": turn_instance_id,
            },
        )
    except LoopxCliExecutionError as exc:
        if not fixture.required_vision:
            raise
        # The real writer puts its refusal and recovery in stdout. Keep the
        # nonzero status and let the shell's existing feedback bound apply.
        feedback = exc.stdout.strip() if "refresh-state" in tokens else ""
        raise _HostToolError("loopx_cli_nonzero", feedback or str(exc), exc.returncode) from None


def _run_state_cli(command: str, state: _QualificationState) -> str:
    return _execute_loopx(command, fixture=state.fixture, turn_instance_id=state.turn_instance_id)


def _handle_quota_command(command: str, state: _QualificationState) -> str:
    if state.seen_quota:
        raise ValueError("repeated_quota_should_run")
    output = _run_state_cli(command, state)
    state.quota_context = QuotaToolContext.from_output(output)
    decoded = cast(JsonObject, state.quota_context.packet)
    state.quota_packet = decoded
    obligation = decoded.get("autonomous_replan_obligation")
    if isinstance(obligation, Mapping) and isinstance(obligation.get("replan_context"), Mapping):
        state.quota_observation = _quota_behavior_observation(decoded)
    elif not (
        state.quota_context.capture_path is not None
        and isinstance(obligation, Mapping)
        and isinstance(obligation.get("context_read"), str)
        and obligation.get("context_read")
    ):
        raise TypeError("quota must deliver or reference the replan coverage context")
    _assert_vision_trigger(state)
    state.seen_quota = True
    state.read_only_host_commands_executed = True
    return output


def _handle_context_read(command: str, state: _QualificationState) -> str:
    if state.quota_context is None:
        raise ValueError("context read before quota")
    context = state.replan_recovery_context or state.quota_context
    output = context.read(command, source_root=state.fixture.source_root, project_root=state.fixture.project_root)
    state.delivery_context_read = True
    state.read_only_host_commands_executed = True
    return output


def _handle_replan_context_read(command: str, state: _QualificationState) -> str:
    if state.quota_packet is None or state.quota_context is None:
        raise ValueError("replan_context_read_before_quota")
    if state.quota_context.capture_path is not None and not state.delivery_context_read:
        raise ValueError("replan_context_read_before_delivery_context")
    review_command, writeback_command = _expected_replan_view_commands(state.quota_packet)
    normalized = replan_view_command_tokens(command)
    if normalized is None or command not in {review_command, writeback_command}:
        raise ValueError("unexpected_replan_context_read")
    is_writeback = command == writeback_command and command != review_command
    if is_writeback and not state.replan_context_read:
        raise ValueError("replan_writeback_read_before_review")
    if is_writeback and state.replan_writeback_read:
        raise ValueError("repeated_replan_writeback_read")
    if not is_writeback and state.replan_context_read:
        raise ValueError("repeated_replan_context_read")
    output = execute_loopx_cli(
        shlex.join(normalized), source_root=state.fixture.source_root, project_root=state.fixture.project_root,
    )
    view = _json_object(output, error="replan_context_read_invalid")
    if is_writeback:
        expected_id = _as_mapping(
            state.quota_packet.get("autonomous_replan_obligation")
        ).get("obligation_id")
        if (
            view.get("stage") != "writeback"
            or view.get("obligation_id") != expected_id
            or not isinstance(view.get("writeback_contract"), Mapping)
        ):
            raise ValueError("replan_writeback_read_invalid")
        state.replan_writeback_read = True
    else:
        state.quota_observation = _quota_behavior_observation(state.quota_packet, replan_view=view)
        _assert_vision_trigger(state)
        state.replan_context_read = True
    state.read_only_host_commands_executed = True
    return output


def _handle_clock_command(output: str, state: _QualificationState) -> str:
    if state.seen_clock:
        raise ValueError("repeated_clock")
    state.seen_clock = True
    state.read_only_host_commands_executed = True
    return output


def _handle_workspace_read(command: str, state: _QualificationState) -> str:
    output, read_frontier, read_work_source = _execute_workspace_read(command, fixture=state.fixture)
    state.frontier_context_read = state.frontier_context_read or read_frontier
    state.work_source_read = state.work_source_read or read_work_source
    state.read_only_host_commands_executed = True
    return output


def _handle_refresh_state_help(state: _QualificationState, *, line_limit: int) -> str:
    _require_observed_frontier(state, prefix="refresh_state_help")
    if state.seen_refresh_state_help:
        raise ValueError("repeated_refresh_state_help")
    output = execute_loopx_cli(
        "loopx refresh-state --help", source_root=state.fixture.source_root, project_root=state.fixture.project_root,
    )
    state.seen_refresh_state_help = True
    state.read_only_host_commands_executed = True
    return "\n".join(output.splitlines()[:line_limit]) + "\n"


def _handle_successor_command(command: str, state: _QualificationState) -> str:
    _require_observed_frontier(state, prefix="successor_create")
    if state.created_successor_id is not None:
        raise ValueError("repeated_successor_create")
    expected_obligation_id = _expected_obligation_id(state)
    tokens = loopx_command_tokens(command) or []
    if argument_value(tokens, "--replan-obligation-id") != expected_obligation_id:
        raise ValueError("successor_create_obligation_mismatch")
    if argument_value(tokens, "--turn-instance-id") != state.turn_instance_id:
        raise ValueError("successor_create_turn_mismatch")
    if (
        state.fixture.composition_experiment_ref
        and argument_value(tokens, "--explore-result-node-ref") != state.fixture.composition_experiment_ref
    ):
        raise ValueError("successor_create_composition_binding_mismatch")
    output = _run_state_cli(command, state)
    decoded = _json_object(output, error="successor_create_failed")
    todo_id = str(decoded.get("todo_id") or "").strip()
    state.created_successor_id = todo_id
    if not (decoded.get("ok") is True and decoded.get("added") is True and todo_id):
        raise ValueError("successor_create_failed")
    transition = decoded.get("replan_transition")
    if not (
        isinstance(transition, dict)
        and transition.get("recorded") is True
        and transition.get("obligation_id") == expected_obligation_id
        and transition.get("successor_todo_id") == todo_id
        and transition.get("outcome") == "new_runnable_successor"
    ):
        raise ValueError("successor_transition_missing")
    immediate_end = (
        transition.get("turn_boundary") == "end_current_heartbeat"
        and decoded.get("host_action") == "end_current_heartbeat"
    )
    settle_then_end = (
        transition.get("turn_boundary") == "settle_original_turn_before_end"
        and isinstance(decoded.get("settlement_identity"), Mapping)
        and isinstance(decoded.get("settlement_plan"), Mapping)
    )
    if not (immediate_end or settle_then_end):
        raise ValueError("successor_create_failed")
    state.successor_transition = {
        key: cast(object, transition[key])
        for key in ("recorded", "obligation_id", "successor_todo_id", "outcome", "turn_boundary")
    }
    state.semantic_delta = {"accepted": True, "satisfying_outcomes": ["new_runnable_successor"]}
    # A created successor is not an original-Turn settlement. Return the native
    # plan to the actor; only its later writeback/spend permits fresh re-entry.
    state.read_only_host_commands_executed = True
    return output


def _bound_semantic_writeback(command: str, state: _QualificationState) -> str:
    """Observe the real writer; never construct the actor's Vision or binding."""
    tokens = loopx_command_tokens(command) or []
    obligation_id = _expected_obligation_id(state)
    if (
        argument_value(tokens, "--replan-obligation-id") != obligation_id
        or argument_value(tokens, "--turn-instance-id") != state.turn_instance_id
    ):
        raise ValueError("semantic_writeback_binding_mismatch")
    _authored_vision(command, state)
    try:
        output = _run_state_cli(command, state)
    except LoopxCliExecutionError as exc:
        # Only a refusal from this bound real writer can authorize another
        # captured reader. Shell text and model-selected paths grant nothing.
        rejected = _json_object(exc.stdout)
        transition = _as_mapping(rejected.get("replan_transition"))
        recovery = _as_mapping(transition.get("context_recovery"))
        path, digest = recovery.get("path"), recovery.get("sha256")
        if (
            rejected.get("ok") is not False or rejected.get("appended") is not False
            or transition.get("obligation_id") != obligation_id
            or not isinstance(path, str) or not isinstance(digest, str)
            or not Path(path).resolve().is_relative_to(state.fixture.runtime_root.resolve())
        ):
            raise
        state.replan_recovery_context = QuotaToolContext(
            packet=rejected, output=exc.stdout, capture_path=Path(path).resolve(), capture_sha256=digest,
        )
        raise _HostToolError("loopx_cli_nonzero", exc.stdout, exc.returncode) from None
    decoded = _json_object(output, error="semantic_writeback_failed")
    if decoded.get("ok") is not True:
        raise ValueError("semantic_writeback_failed")
    row = _fixture_runs(state)[-1]
    semantic = dict(_as_mapping(_as_mapping(row.get("autonomous_replan_ack")).get("semantic_delta")))
    identity = _as_mapping(_as_mapping(state.quota_packet).get("heartbeat_receipt")).get("settlement_identity")
    if not (
        semantic.get("accepted") is True and semantic.get("obligation_id") == obligation_id
        and _as_mapping(row.get("vision_checkpoint")).get("satisfied") is True
        and row.get("settlement_identity") == identity
    ):
        raise ValueError("semantic_writeback_durable_receipt_missing")
    owed = decoded.get("settlement_owed")
    if not isinstance(owed, Mapping) or not isinstance(owed.get("command"), str):
        raise ValueError("semantic_writeback_settlement_missing")
    state.settlement_command = owed["command"]
    state.semantic_delta = semantic
    return output


def _read_quota_packet(fixture: _ReplanSemanticActionFixture, turn_instance_id: str) -> JsonObject:
    output = _execute_loopx(fixture.quota_guard_command, fixture=fixture, turn_instance_id=turn_instance_id)
    return cast(JsonObject, QuotaToolContext.from_output(output).packet)


def _handle_semantic_spend(command: str, state: _QualificationState) -> str:
    if not state.settlement_command:
        raise ValueError("semantic_spend_before_writeback")
    if loopx_command_tokens(command) != loopx_command_tokens(state.settlement_command):
        raise ValueError("semantic_spend_identity_mismatch")
    output = _run_state_cli(command, state)
    replay = _read_quota_packet(state.fixture, state.turn_instance_id)
    spends = [row for row in _fixture_runs(state) if row.get("classification") == "quota_slot_spent"]
    if replay.get("effective_action") != EffectiveAction.HEARTBEAT_SETTLED_SKIP.value or len(spends) != 1:
        raise ValueError("semantic_settlement_readback_failed")
    following = _read_quota_packet(state.fixture, f"{state.turn_instance_id}-readback")
    obligation_id = _expected_obligation_id(state)
    if _as_mapping(following.get("autonomous_replan_obligation")).get("obligation_id") == obligation_id:
        raise ValueError("semantic_reentry_replan_not_closed")
    if state.created_successor_id:
        state.successor_reentry_observation = _successor_reentry_observation(
            following, successor_todo_id=state.created_successor_id,
            composition_experiment_ref=state.fixture.composition_experiment_ref,
        )
    elif state.semantic_result_class in {
        ProgressResultClass.EXPLORATION_EXHAUSTED.value,
        ProgressResultClass.NO_FOLLOWUP.value,
    }:
        state.semantic_reentry_observation = _semantic_reentry_observation(
            following, obligation_id=obligation_id, result_class=state.semantic_result_class,
        )
    state.vision_closeout = {
        "checkpoint_satisfied": True, "bound_writeback": True,
        "settled": True, "spend_count": 1, "original_obligation_closed": True,
    }
    return output


def _handle_semantic_writeback(command: str, observation: JsonMapping, state: _QualificationState) -> str:
    result_class = str(observation.get("result_class") or "")
    terminal_result = result_class in {
        ProgressResultClass.BLOCKED.value,
        ProgressResultClass.EXPLORATION_EXHAUSTED.value,
        ProgressResultClass.NO_FOLLOWUP.value,
    }
    _require_observed_frontier(state, prefix="semantic_action", work_source_required=not terminal_result)
    if not terminal_result:
        raw_evidence = observation.get("evidence_ids")
        evidence_ids = set(raw_evidence) if isinstance(raw_evidence, list) else set()
        if not (
            observation.get("surface_id") == _FIXTURE_NEW_SURFACE_ID
            and observation.get("hypothesis_id") == _FIXTURE_NEW_HYPOTHESIS_ID
            and observation.get("probe_kind") == _FIXTURE_NEW_PROBE_KIND
            and _FIXTURE_NEW_EVIDENCE_ID in evidence_ids
        ):
            raise ValueError("semantic_action_misses_uncovered_frontier")
    # The writer owns semantic acceptance and recovery for the original Turn.
    # A compact admission packet may omit the review basis that it resolves.
    state.semantic_result_class = result_class
    return _bound_semantic_writeback(command, state)


def _behavior_command_kind(command: str, state: _QualificationState) -> str:
    """Classify a proposed command before its handler can fail."""
    if _is_quota_guard(command):
        return "quota_should_run"
    if is_context_read_command(command):
        return "replan_context_recovery" if state.replan_recovery_context else "context_read"
    if replan_view_command_tokens(command) is not None:
        return "replan_context_read"
    if (
        state.quota_packet is not None
        and (required := _required_explore_read_command(state.quota_packet))
        and shlex.split(command) == shlex.split(required)
    ):
        return "explore_turn_context"
    if _clock_output(command) is not None:
        return "clock"
    if _bounded_workspace_read_plan(command, fixture=state.fixture) is not None:
        return "workspace_read"
    if _bounded_refresh_state_help_limit(command) is not None:
        return "refresh_state_help"
    if _is_replan_successor_create(command):
        return "replan_successor_create"
    tokens = loopx_command_tokens(command) or []
    if "spend-slot" in tokens:
        return "quota_spend_slot"
    if "refresh-state" in tokens:
        return "semantic_replan_writeback"
    return "unexpected_command"


def _dispatch_behavior_command(command: str, state: _QualificationState) -> tuple[str, str, bool]:
    kind = _behavior_command_kind(command, state)
    if kind == "quota_should_run":
        return _handle_quota_command(command, state), kind, False
    if kind in {"context_read", "replan_context_recovery"}:
        return _handle_context_read(command, state), kind, False
    if kind == "replan_context_read":
        return _handle_replan_context_read(command, state), kind, False
    if state.quota_packet is not None and _required_explore_read_command(state.quota_packet):
        observed = any(step["kind"] == "explore_turn_context" for step in state.steps)
        if kind == "explore_turn_context":
            if observed:
                raise ValueError("repeated_explore_turn_context")
            output = _run_state_cli(command, state)
            context = _json_object(output)
            if context.get("goal_id") != _FIXTURE_GOAL_ID or context.get("agent_id") != _FIXTURE_AGENT_ID:
                raise ValueError("unexpected_command")
            return output, kind, False
        if not observed:
            raise ValueError("required_explore_turn_context_missing")
    if kind == "clock" and (clock_output := _clock_output(command)) is not None:
        return _handle_clock_command(clock_output, state), kind, False
    if kind == "workspace_read":
        return _handle_workspace_read(command, state), kind, False
    if kind == "refresh_state_help" and (limit := _bounded_refresh_state_help_limit(command)) is not None:
        return _handle_refresh_state_help(state, line_limit=limit), kind, False
    if kind == "replan_successor_create":
        return _handle_successor_command(command, state), kind, False
    if kind == "quota_spend_slot":
        return _handle_semantic_spend(command, state), kind, True
    if kind == "semantic_replan_writeback":
        if state.created_successor_id:
            return _bound_semantic_writeback(command, state), kind, False
        if (observation := _progress_observation_from_command(command)) is not None:
            return _handle_semantic_writeback(command, observation, state), kind, False
    if "history" in shlex.split(command):
        raise ValueError("manual_evidence_read_is_not_replan")
    raise ValueError("unexpected_command")


def _behavior_failure_code(exc: Exception, *, kind: str) -> str:
    failure = str(exc)
    return failure if failure in _EXPECTED_BEHAVIOR_FAILURES else f"{kind}_execution_failed"


def _tool_call_budget_exhausted(state: _QualificationState) -> bool:
    if state.fixture.required_vision:
        return len(state.steps) >= REQUIRED_VISION_CLOSEOUT_MAX_CALLS
    ordinary_calls = sum(step.get("kind") not in _NATIVE_CONTEXT_READ_KINDS for step in state.steps)
    return (
        ordinary_calls >= REPLAN_SEMANTIC_ACTION_BEHAVIOR_MAX_CALLS
        or len(state.steps) >= state.maximum_tool_call_limit
    )


def _standalone_cli(command: str) -> bool:
    """Do not discard a requested shell prefix when extracting a CLI suffix."""
    if "\n" in command:
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    parsed = loopx_command_tokens(command)
    return parsed is not None and (
        tokens == parsed or _module_reader_tokens(tokens) is not None
        or (tokens[0].startswith("LOOPX_TURN=") and tokens[1:] == parsed)
    )


def _execute_isolated_shell_step(
    tool_call: ExecToolCall, state: _QualificationState, shell: VisionShellHost,
) -> tuple[str, bool]:
    # A captured module reader uses the same supervised CLI bridge,
    # without allowing the shell to import the source checkout.
    reader = replan_view_command_tokens(tool_call.command)
    if reader is None:
        try:
            reader = _module_reader_tokens(shlex.split(tool_call.command))
        except ValueError:
            pass  # Let the shell report its original syntax error.
    output, exit_code = shell.execute(shlex.join(reader) if reader is not None else tool_call.command)
    observe_shell_evidence(output, state)
    state.read_only_host_commands_executed = True
    if exit_code:
        raise _HostToolError("shell_nonzero", output, exit_code)
    return output, bool((state.vision_closeout or {}).get("settled"))


def _run_qualification_loop(
    client: DoubaoExecToolClient,
    state: _QualificationState,
    *,
    qualification_id: str,
    shell: VisionShellHost | None = None,
    author_shell: Callable[[str], tuple[str, int]] | None = None,
) -> JsonObject:
    def receipt(*, passed: bool, failure_code: str | None) -> JsonObject:
        return _qualification_receipt(
            state, qualification_id=qualification_id, actor_ref=client.actor_ref,
            passed=passed, failure_code=failure_code,
        )

    for _ in range(state.maximum_tool_call_limit):
        if _tool_call_budget_exhausted(state):
            break
        tool_call = client.next_tool_call(
            state.messages,
            tool_description=VISION_EXEC_TOOL_DESCRIPTION if state.fixture.required_vision else None,
        )
        if tool_call is None:
            return receipt(
                passed=False,
                failure_code=(
                    "semantic_closeout_incomplete"
                    if state.settlement_command or state.created_successor_id
                    else "model_returned_without_semantic_action"
                ),
            )
        kind = "shell" if shell else _behavior_command_kind(tool_call.command, state)
        if not shell and loopx_command_tokens(tool_call.command) is not None and not _standalone_cli(tool_call.command):
            kind = "draft_shell"
        try:
            if shell is not None:
                output, completed = _execute_isolated_shell_step(tool_call, state, shell)
            elif author_shell and (
                kind == "draft_shell"
                or (kind == "unexpected_command" and loopx_command_tokens(tool_call.command) is None)
            ):
                _require_observed_frontier(
                    state, prefix="semantic_action", work_source_required=not state.fixture.frontier_exhausted,
                )
                kind = "draft_shell"
                output, exit_code = author_shell(tool_call.command)
                state.draft_shell_executed = True
                if exit_code:
                    raise _HostToolError("shell_nonzero", output, exit_code)
                completed = bool((state.vision_closeout or {}).get("settled"))
            else:
                output, kind, completed = _dispatch_behavior_command(tool_call.command, state)
        except _HostToolError as exc:
            _record_tool_step(state, kind=kind, command=tool_call.command, exit_code=exc.exit_code, error_code=exc.code)
            append_exec_tool_exchange(
                state.messages, tool_call,
                json.dumps({
                    "error_code": exc.code, "exit_code": exc.exit_code,
                    "output": exc.output[:_MAX_HOST_TOOL_ERROR_OUTPUT_CHARS],
                    "truncated": len(exc.output) > _MAX_HOST_TOOL_ERROR_OUTPUT_CHARS,
                }),
                include_assistant_content=False,
            )
            continue
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            _record_tool_step(state, kind=kind, command=tool_call.command)
            return receipt(passed=False, failure_code=_behavior_failure_code(exc, kind=kind))
        _record_tool_step(state, kind=kind, command=tool_call.command)
        if completed:
            return receipt(passed=True, failure_code=None)
        append_exec_tool_exchange(state.messages, tool_call, output, include_assistant_content=False)
    return receipt(passed=False, failure_code="tool_call_budget_exhausted")


class DoubaoReplanSemanticActionBehaviorActor(DoubaoExecToolBehaviorActorBase):
    """Qualify a model-selected semantic replan through the real CLI boundary."""

    def qualify(
        self, *, qualification_id: str, fixture_root: Path,
        composition_frontier: bool = False, exhausted_frontier: bool = False, required_vision: bool = False,
    ) -> JsonObject:
        fixture = _build_fixture(
            fixture_root, composition_frontier=composition_frontier,
            exhausted_frontier=exhausted_frontier, required_vision=required_vision,
        )
        messages: list[JsonObject] = [
            {"role": "system", "content": VISION_HOST_INSTRUCTION if required_vision else _ORDINARY_HOST_SYSTEM_PROMPT},
            {"role": "user", "content": fixture.task_body},
        ]
        state = _QualificationState(
            fixture=fixture, messages=messages, steps=[],
            turn_instance_id=qualification_turn_instance_id(qualification_id),
        )
        if required_vision:
            def invoke(argv: list[str], cwd: Path, output: str) -> str:
                observe_shell_evidence(output, state)
                command = _resolve_shell_cli_command(argv, cwd)
                if "--help" in argv or "-h" in argv:
                    return _run_state_cli(command, state)
                if _is_quota_guard(command):
                    return _run_state_cli(command, state) if state.seen_quota else _handle_quota_command(command, state)
                if is_context_read_command(command):
                    return _handle_context_read(command, state)
                if (reader := replan_view_command_tokens(command)) is not None:
                    for expected in _expected_replan_view_commands(state.quota_packet or {}):
                        if expected and replan_view_command_tokens(expected) == reader:
                            return _handle_replan_context_read(expected, state)
                    raise ValueError("unexpected_replan_context_read")
                result = dispatch_vision_closeout(command, state, execute=_execute_loopx)
                if result is None:
                    raise ValueError(
                        "This fixture grants CLI authority for quota, help, "
                        "vision refresh and settlement only"
                    )
                return result[0]

            shell = VisionShellHost(fixture.project_root, invoke, turn_instance_id=state.turn_instance_id)
            try:
                return _run_qualification_loop(self._client, state, qualification_id=qualification_id, shell=shell)
            finally:
                shell.close()
        draft_hosts: list[VisionShellHost] = []

        def draft_cli(argv: list[str], cwd: Path, _output: str) -> str:
            if (state.vision_closeout or {}).get("settled"):
                raise ValueError("The original Turn is settled; no later CLI action is admitted")
            output, _kind, _completed = _dispatch_behavior_command(_resolve_shell_cli_command(argv, cwd), state)
            return output

        def author_shell(command: str) -> tuple[str, int]:
            if not draft_hosts:
                draft_hosts.append(
                    VisionShellHost(
                        fixture.project_root,
                        draft_cli,
                        turn_instance_id=state.turn_instance_id,
                    )
                )
            return draft_hosts[0].execute(command)

        try:
            return _run_qualification_loop(
                self._client,
                state,
                qualification_id=qualification_id,
                author_shell=author_shell,
            )
        finally:
            for host in draft_hosts:
                host.close()
