from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import loopx.cli_commands.turn as turn_command
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
)
from loopx.cli import main as cli_main
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.turn_driver import (
    LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
    LoopXTurnRoute,
    build_loopx_turn_host_request,
    build_loopx_turn_plan,
    codex_cli_session_binding,
    loopx_turn_execution_committed,
    reward_memory_reflection_digest,
    run_loopx_turn_once,
)
from loopx.control_plane.turn_driver.codex_cli import _store_codex_cli_session
from loopx.control_plane.turn_driver.subagent_execution_topology import (
    CHILD_FALLBACK_ACTIONS,
)
from loopx.control_plane.turn_driver.executor import BuiltInHostError
from loopx.control_plane.quota.live_decision import bind_scheduler_followup_cli_routes
from loopx.todos import complete_goal_todo


def _envelope(
    *,
    should_run: bool = True,
    effective_action: str = "normal_run",
    action_required: bool = False,
    quiet_noop_allowed: bool = False,
) -> dict[str, object]:
    return {
        "ok": True,
        "schema_version": "loopx_turn_envelope_v0",
        "goal_id": "fixture-goal",
        "agent_id": "codex-fixture",
        "should_run": should_run,
        "effective_action": effective_action,
        "action": {
            "must_attempt": should_run,
            "delivery_allowed": should_run,
            "quiet_noop_allowed": quiet_noop_allowed,
            "selected_todo": {
                "todo_id": "todo_fixture0001",
                "text": "Advance one public fixture",
            },
        },
        "user": {
            "action_required": action_required,
            "open_count": 1 if action_required else 0,
            "notify": "NOTIFY" if action_required else "DONT_NOTIFY",
        },
        "writeback": {"spend_after_validation": should_run},
        "scheduler": {"action": "run_now" if should_run else "wait"},
        "action_signature": {
            "matches": True,
            "source_hash": "sha256:fixture",
            "envelope_hash": "sha256:fixture",
        },
        "compaction": {"within_budget": True},
    }


def test_turn_plan_projects_ready_route_without_side_effects() -> None:
    envelope = _envelope()

    payload = build_loopx_turn_plan(
        envelope,
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    assert payload["ok"] is True
    assert payload["schema_version"] == "loopx_turn_plan_v0"
    assert payload["mode"] == "plan"
    assert payload["route"]["kind"] == LoopXTurnRoute.READY_FOR_HOST.value
    assert payload["route"]["would_invoke_host"] is True
    assert payload["route"]["host_invocation_allowed"] is False
    assert payload["session"] == {
        "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
        "action": "start_new",
        "context_policy": {
            "schema_version": "loopx_iteration_context_policy_v0",
            "mode": "resume_if_available",
            "scope": "iteration",
        },
    }
    assert payload["transaction"]["status"] == "planned"
    assert payload["transaction"]["phases"] == [
        "host_execute",
        "typed_result",
        "validation",
        "durable_writeback",
        "quota_spend",
        "scheduler_apply",
        "scheduler_ack",
    ]
    assert payload["transaction"]["receipt_seed"]["status"] == "not_executed"
    assert payload["transaction"]["receipt_seed"]["next_phase"] == "host_execute"
    settlement_plan = payload["transaction"]["settlement_plan"]
    assert settlement_plan["identity"]["goal_id"] == "fixture-goal"
    assert settlement_plan["identity"]["agent_id"] == "codex-fixture"
    assert settlement_plan["identity"]["todo_id"] == "todo_fixture0001"
    assert [step["kind"] for step in settlement_plan["ordered_steps"]] == [
        "validation",
        "durable_writeback",
        "quota_spend",
        "terminal_closeout",
    ]
    for step in settlement_plan["ordered_steps"]:
        assert step["owner"]
        assert step["precondition"]
        assert step["idempotency_key_ref"] == "$.identity.effect_id"
        assert step["expected_receipt"]
    assert settlement_plan["host_handoff"]["inside_agent_settlement"] is False
    assert payload["turn_envelope"] == envelope
    assert payload["effects"] == {
        "host_invoked": False,
        "state_written": False,
        "scheduler_acknowledged": False,
        "quota_spent": False,
    }
    assert payload["boundary"]["read_only"] is True


def test_dsh_turn_plan_uses_the_headless_outer_controller_context() -> None:
    payload = build_loopx_turn_plan(
        _envelope(),
        host="dsh",
        execution_mode="isolated-headless",
    )

    assert payload["ok"] is True
    assert payload["route"]["kind"] == LoopXTurnRoute.READY_FOR_HOST.value
    assert payload["host"]["kind"] == "dsh"
    assert payload["host"]["explicit_isolation"] is True
    assert payload["scheduler_execution_context"] == {
        "schema_version": "scheduler_execution_context_v0",
        "host_surface": "generic_cli",
        "scheduler_owner": "outer_controller",
        "execution_mode": "isolated_headless",
        "source": "loopx_turn",
        "valid": True,
        "codex_app_applicability": "not_applicable",
    }


def _adaptive_envelope() -> dict[str, object]:
    envelope = _envelope()
    envelope["task_orchestration_contract"] = {
        "schema_version": "task_orchestration_contract_v2",
        "mode": "adaptive",
        "coordinator_agent_id": "codex-fixture",
        "child_brief_defaults": {
            "schema_version": "subagent_control_plane_handoff_v0",
            "parent_goal_id": "fixture-goal",
            "authority_artifact": "quota_should_run.goal_boundary",
            "latest_state_ref": "quota_should_run.action_signature.source_hash",
            "context_policy": {
                "selection_owner": "task_coordinator",
                "default": "fresh",
                "allowed": ["fresh", "resume"],
            },
            "expected_output": "public_safe_evidence",
            "execution_policy": {
                "timeout": "bounded_by_host_turn",
                "cancel": "task_coordinator_or_host_timeout",
            },
            "child_guard_policy": "prevention_first_v0",
            "validation_policy": "report validation commands and results",
            "acceptance": [
                "report completed scope and evidence",
                "do not write LoopX state or spend quota",
            ],
        },
        "eligible_child_lanes": [
            {
                "todo_id": "todo_child001",
                "task_domain": "validation",
                "execution_kind": "ephemeral_child",
                "child_brief": {
                    "todo_id": "todo_child001",
                    "objective": "Validate one independent fixture.",
                    "action_kind": "validate",
                    "task_domain": "validation",
                    "required_capabilities": [],
                    "task_repository": None,
                    "required_write_scopes": [],
                    "workspace_isolation": "not_required",
                },
            }
        ],
        "writeback_owner": "task_coordinator",
    }
    return envelope


def _signed_adaptive_envelope(
    *,
    stale_selected_todo_id: str,
    recommended_action: str = "Advance one public fixture",
) -> dict[str, object]:
    decision = {
        "ok": True,
        "goal_id": "fixture-goal",
        "agent_identity": {"agent_id": "codex-fixture"},
        "decision": "run",
        "should_run": True,
        "effective_action": "normal_run",
        "state": "eligible",
        "recommended_action": recommended_action,
        "selected_todo": {
            "todo_id": stale_selected_todo_id,
            "text": "A stale pre-orchestration selection",
        },
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "normal_run",
            "user_channel": {
                "action_required": False,
                "notify": "DONT_NOTIFY",
            },
            "agent_channel": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
            },
            "cli_channel": {
                "spend_after_validation": True,
            },
        },
        "task_orchestration_contract": {
            **_adaptive_envelope()["task_orchestration_contract"],
            "primary_todo_id": "todo_primary",
        },
    }
    return build_turn_envelope(decision)


def test_turn_plan_maps_admitted_child_to_codex_native_operation() -> None:
    payload = build_loopx_turn_plan(
        _adaptive_envelope(),
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    orchestration = payload["turn_envelope"]["task_orchestration_contract"]
    lane_brief = orchestration["eligible_child_lanes"][0]["child_brief"]
    brief = {
        **orchestration["child_brief_defaults"],
        **lane_brief,
        "evidence_boundary": {
            "task_domain": "validation",
            "task_repository": None,
            "required_write_scopes": [],
        },
    }
    topology = payload["subagent_execution_topology"]
    lane = topology["lanes"][0]
    task_packet = lane["task_packet"]
    source_state_ref = "sha256:fixture"
    assert payload["child_operations"] == [
        {
            "schema_version": "loopx_child_host_operation_v0",
            "todo_id": "todo_child001",
            "host": "codex-cli",
            "selection_owner": "task_coordinator",
            "recommended_context": "fresh",
            "available_contexts": ["fresh"],
            "brief": brief,
            "result_channel": "public_safe_typed_evidence",
            "writeback_owner": "task_coordinator",
            "bundle_id": topology["bundle_id"],
            "lane_id": lane["lane_id"],
            "execution_kind": "ephemeral_child",
            "source_state_ref": source_state_ref,
            "task_packet_digest": lane["task_packet_digest"],
            "task_packet": task_packet,
            "effect_boundary": "held_evidence_only",
            "workspace_ref": None,
            "host_adapter": {
                "host": "codex-cli",
                "native_operation": "spawn_agent",
                "arguments": {"fork_context": False},
                "requires_session": False,
            },
        }
    ]
    assert topology == {
        "schema_version": "subagent_execution_topology_v0",
        "goal_id": "fixture-goal",
        "bundle_id": topology["bundle_id"],
        "coordinator_agent_id": "codex-fixture",
        "source_state_ref": source_state_ref,
        "topology": "ephemeral_children",
        "execution_envelope": {
            "source": "task_orchestration_contract_v2",
            "expires_with_turn": True,
            "max_children": 1,
            "allowed_effect_classes": ["local_read"],
        },
        "rationale_codes": ["admitted_ephemeral_child_lanes"],
        "lanes": [
            {
                "lane_id": lane["lane_id"],
                "todo_id": "todo_child001",
                "execution_kind": "ephemeral_child",
                "admission_ref": "task_orchestration_contract_v2:todo_child001",
                "effect_boundary": "held_evidence_only",
                "allowed_effect_classes": ["local_read"],
                "workspace_requirement": "not_required",
                "workspace_ref": None,
                "task_packet": task_packet,
                "task_packet_digest": lane["task_packet_digest"],
            }
        ],
    }
    assert task_packet["schema_version"] == "child_execution_task_packet_v0"
    assert task_packet["objective"] == "Validate one independent fixture."
    assert task_packet["action_kind"] == "validate"
    assert task_packet["deliverable"] == "public_safe_evidence"
    assert task_packet["acceptance_mode"] == "parent_review_only"
    assert task_packet["acceptance"] == [
        "report completed scope and evidence",
        "do not write LoopX state or spend quota",
    ]
    assert task_packet["context"] == {
        "mode": "fresh",
        "inheritance": "none",
    }
    assert task_packet["guard"] == {
        "schema_version": "child_execution_guard_v0",
        "pre_spawn": "require_complete_task_packet",
        "on_deviation": "project_stop_and_quarantine_child",
        "evidence_disposition": "candidate_until_parent_accepts",
        "parent_blocked": False,
        "parent_continuation": "continue",
        "fallback_actions": [
            "retry_fresh",
            "replace_child",
            "serial_takeover",
            "ignore_optional_result",
        ],
    }
    assert lane["task_packet_digest"].startswith("sha256:")


def test_turn_plan_rejects_incomplete_child_lane_without_blocking_parent() -> None:
    envelope = _adaptive_envelope()
    orchestration = envelope["task_orchestration_contract"]
    assert isinstance(orchestration, dict)
    defaults = orchestration["child_brief_defaults"]
    assert isinstance(defaults, dict)
    defaults.pop("acceptance")
    lanes = orchestration["eligible_child_lanes"]
    assert isinstance(lanes, list)
    lanes.append(
        {
            "todo_id": "todo_child002",
            "task_domain": "validation",
            "execution_kind": "ephemeral_child",
            "child_brief": {
                "todo_id": "todo_child002",
                "objective": "Validate another independent fixture.",
                "action_kind": "validate",
                "task_domain": "validation",
                "required_capabilities": [],
                "task_repository": None,
                "required_write_scopes": [],
                "workspace_isolation": "not_required",
                "acceptance": ["return one bounded validation result"],
            },
        }
    )

    payload = build_loopx_turn_plan(
        envelope,
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    assert payload["ok"] is True
    assert payload["route"]["kind"] == LoopXTurnRoute.READY_FOR_HOST.value
    assert [item["todo_id"] for item in payload["child_operations"]] == [
        "todo_child002"
    ]
    topology = payload["subagent_execution_topology"]
    assert topology["topology"] == "ephemeral_children"
    assert [lane["todo_id"] for lane in topology["lanes"]] == ["todo_child002"]
    assert topology["pre_spawn_rejections"] == [
        {
            "schema_version": "child_execution_rejection_v0",
            "todo_id": "todo_child001",
            "stage": "pre_spawn",
            "reason_codes": ["child_task_packet_incomplete"],
            "launch_allowed": False,
            "recommended_child_action": "do_not_launch",
            "parent_blocked": False,
            "parent_continuation": "continue",
            "fallback_actions": list(CHILD_FALLBACK_ACTIONS),
        }
    ]


def test_turn_plan_falls_back_to_serial_when_every_child_packet_is_incomplete() -> (
    None
):
    envelope = _adaptive_envelope()
    orchestration = envelope["task_orchestration_contract"]
    assert isinstance(orchestration, dict)
    defaults = orchestration["child_brief_defaults"]
    assert isinstance(defaults, dict)
    defaults.pop("acceptance")

    payload = build_loopx_turn_plan(
        envelope,
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    assert payload["ok"] is True
    assert payload["route"]["kind"] == LoopXTurnRoute.READY_FOR_HOST.value
    assert "child_operations" not in payload
    topology = payload["subagent_execution_topology"]
    assert topology["topology"] == "serial"
    assert topology["rationale_codes"] == [
        "parent_serial_fallback",
        "child_launch_rejected",
    ]
    assert topology["lanes"] == []
    assert topology["pre_spawn_rejections"][0]["parent_blocked"] is False


def test_turn_plan_rejects_excess_child_lane_without_blocking_admitted_sibling() -> (
    None
):
    envelope = _adaptive_envelope()
    orchestration = envelope["task_orchestration_contract"]
    assert isinstance(orchestration, dict)
    orchestration["max_children"] = 1
    lanes = orchestration["eligible_child_lanes"]
    assert isinstance(lanes, list)
    lanes.append(
        {
            "todo_id": "todo_child002",
            "task_domain": "validation",
            "execution_kind": "ephemeral_child",
            "child_brief": {
                "todo_id": "todo_child002",
                "objective": "Validate another independent fixture.",
                "task_domain": "validation",
            },
        }
    )

    payload = build_loopx_turn_plan(
        envelope,
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    assert payload["ok"] is True
    assert [item["todo_id"] for item in payload["child_operations"]] == [
        "todo_child001"
    ]
    topology = payload["subagent_execution_topology"]
    assert [lane["todo_id"] for lane in topology["lanes"]] == ["todo_child001"]
    rejection = topology["pre_spawn_rejections"][0]
    assert rejection["todo_id"] == "todo_child002"
    assert rejection["reason_codes"] == ["child_capacity_exceeded"]
    assert rejection["parent_blocked"] is False
    assert rejection["fallback_actions"] == list(CHILD_FALLBACK_ACTIONS)


def test_turn_plan_exposes_forked_snapshot_only_when_explicitly_selected() -> None:
    envelope = _adaptive_envelope()
    orchestration = envelope["task_orchestration_contract"]
    assert isinstance(orchestration, dict)
    defaults = orchestration["child_brief_defaults"]
    assert isinstance(defaults, dict)
    defaults["context_policy"] = {
        "selection_owner": "task_coordinator",
        "default": "forked_snapshot",
        "allowed": ["fresh", "forked_snapshot"],
    }

    payload = build_loopx_turn_plan(
        envelope,
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    operation = payload["child_operations"][0]
    assert operation["recommended_context"] == "forked_snapshot"
    assert operation["available_contexts"] == ["fresh", "forked_snapshot"]
    assert operation["task_packet"]["context"] == {
        "mode": "forked_snapshot",
        "inheritance": "parent_conversation_snapshot",
    }
    assert operation["host_adapter"] == {
        "host": "codex-cli",
        "native_operation": "spawn_agent",
        "arguments": {"fork_context": True},
        "requires_session": False,
    }


def test_turn_plan_keeps_declared_validation_with_registered_parent() -> None:
    envelope = _adaptive_envelope()
    orchestration = envelope["task_orchestration_contract"]
    assert isinstance(orchestration, dict)
    lanes = orchestration["eligible_child_lanes"]
    assert isinstance(lanes, list)
    child_brief = lanes[0]["child_brief"]
    assert isinstance(child_brief, dict)
    child_brief.update(
        validation_declared=True,
        validation_label="child regression",
    )

    payload = build_loopx_turn_plan(
        envelope,
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    validation = payload["child_operations"][0]["task_packet"]["validation"]
    assert validation == {
        "declared": True,
        "authority_ref": "todo:todo_child001:completion_validation",
        "execution_owner": "registered_parent",
        "command_disclosed": False,
        "label": "child regression",
        "policy": (
            "registered parent runs declared todo completion validation; "
            "child reports relevant validation evidence"
        ),
    }
    assert payload["child_operations"][0]["task_packet"]["acceptance_mode"] == (
        "parent_validation_then_review"
    )


def test_turn_plan_rejects_context_not_supported_by_selected_host() -> None:
    envelope = _adaptive_envelope()
    orchestration = envelope["task_orchestration_contract"]
    assert isinstance(orchestration, dict)
    defaults = orchestration["child_brief_defaults"]
    assert isinstance(defaults, dict)
    defaults["context_policy"] = {
        "selection_owner": "task_coordinator",
        "default": "forked_snapshot",
        "allowed": ["fresh", "forked_snapshot"],
    }

    payload = build_loopx_turn_plan(
        envelope,
        host="claude-code",
        execution_mode="interactive-visible",
    )

    assert payload["ok"] is True
    assert "child_operations" not in payload
    rejection = payload["subagent_execution_topology"][
        "pre_spawn_rejections"
    ][0]
    assert rejection["reason_codes"] == ["child_task_packet_incomplete"]
    assert rejection["parent_blocked"] is False


def test_turn_plan_rejects_resume_without_child_session_binding() -> None:
    envelope = _adaptive_envelope()
    orchestration = envelope["task_orchestration_contract"]
    assert isinstance(orchestration, dict)
    defaults = orchestration["child_brief_defaults"]
    assert isinstance(defaults, dict)
    defaults["context_policy"] = {
        "selection_owner": "task_coordinator",
        "default": "resume",
        "allowed": ["fresh", "resume"],
    }

    payload = build_loopx_turn_plan(
        envelope,
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    assert payload["ok"] is True
    assert "child_operations" not in payload
    rejection = payload["subagent_execution_topology"][
        "pre_spawn_rejections"
    ][0]
    assert rejection["reason_codes"] == ["child_task_packet_incomplete"]
    assert rejection["parent_blocked"] is False


def test_task_packet_digest_is_independent_of_host_adapter() -> None:
    envelope = _adaptive_envelope()

    codex = build_loopx_turn_plan(
        envelope,
        host="codex-cli",
        execution_mode="interactive-visible",
    )
    claude = build_loopx_turn_plan(
        envelope,
        host="claude-code",
        execution_mode="interactive-visible",
    )

    codex_operation = codex["child_operations"][0]
    claude_operation = claude["child_operations"][0]
    assert codex_operation["task_packet"] == claude_operation["task_packet"]
    assert codex_operation["task_packet_digest"] == claude_operation[
        "task_packet_digest"
    ]
    assert codex_operation["host_adapter"] != claude_operation["host_adapter"]


def test_turn_plan_uses_adaptive_primary_todo_for_bundle_lineage() -> None:
    first_envelope = _signed_adaptive_envelope(
        stale_selected_todo_id="todo_stale_selection",
    )
    second_envelope = _signed_adaptive_envelope(
        stale_selected_todo_id="todo_another_stale_selection",
    )
    assert first_envelope["action_signature"]["matches"] is True
    assert (
        first_envelope["action_signature"]["source_hash"]
        == first_envelope["action_signature"]["envelope_hash"]
    )
    assert second_envelope["action_signature"]["matches"] is True
    assert (
        second_envelope["action_signature"]["source_hash"]
        == second_envelope["action_signature"]["envelope_hash"]
    )
    assert (
        first_envelope["action_signature"]["source_hash"]
        != second_envelope["action_signature"]["source_hash"]
    )

    payload = build_loopx_turn_plan(
        first_envelope,
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    assert payload["ok"] is True
    assert payload["route"]["kind"] == LoopXTurnRoute.READY_FOR_HOST.value
    assert payload["route"]["selected_todo"] == {
        "todo_id": "todo_primary",
        "source": "task_orchestration_contract.primary_todo_id",
    }
    same_primary_plan = build_loopx_turn_plan(
        second_envelope,
        host="codex-cli",
        execution_mode="interactive-visible",
    )
    changed_action_plan = build_loopx_turn_plan(
        _signed_adaptive_envelope(
            stale_selected_todo_id="todo_stale_selection",
            recommended_action="Advance a different public fixture",
        ),
        host="codex-cli",
        execution_mode="interactive-visible",
    )
    assert (
        payload["transaction"]["turn_key"]
        == same_primary_plan["transaction"]["turn_key"]
    )
    assert (
        payload["subagent_execution_topology"]["source_state_ref"]
        != same_primary_plan["subagent_execution_topology"]["source_state_ref"]
    )
    assert (
        payload["subagent_execution_topology"]["bundle_id"]
        == same_primary_plan["subagent_execution_topology"]["bundle_id"]
    )
    assert (
        payload["transaction"]["turn_key"]
        != changed_action_plan["transaction"]["turn_key"]
    )


def test_turn_plan_exposes_only_qualified_claude_child_contexts() -> None:
    envelope = _adaptive_envelope()
    payload = build_loopx_turn_plan(
        envelope,
        host="claude-code",
        execution_mode="interactive-visible",
    )
    orchestration = envelope["task_orchestration_contract"]
    lane_brief = orchestration["eligible_child_lanes"][0]["child_brief"]
    brief = {
        **orchestration["child_brief_defaults"],
        **lane_brief,
        "evidence_boundary": {
            "task_domain": "validation",
            "task_repository": None,
            "required_write_scopes": [],
        },
    }
    topology = payload["subagent_execution_topology"]
    lane = topology["lanes"][0]
    source_state_ref = topology["source_state_ref"]

    assert payload["child_operations"][0] == {
        "schema_version": "loopx_child_host_operation_v0",
        "todo_id": "todo_child001",
        "host": "claude-code",
        "selection_owner": "task_coordinator",
        "recommended_context": "fresh",
        "available_contexts": ["fresh"],
        "brief": brief,
        "result_channel": "public_safe_typed_evidence",
        "writeback_owner": "task_coordinator",
        "bundle_id": topology["bundle_id"],
        "lane_id": lane["lane_id"],
        "execution_kind": "ephemeral_child",
        "source_state_ref": source_state_ref,
        "task_packet_digest": lane["task_packet_digest"],
        "task_packet": lane["task_packet"],
        "effect_boundary": "held_evidence_only",
        "workspace_ref": None,
        "host_adapter": {
            "host": "claude-code",
            "native_operation": "Task",
            "arguments": {},
            "requires_session": False,
        },
    }


def test_codex_session_binding_uses_adaptive_primary_todo(
    tmp_path: Path,
) -> None:
    envelope = _adaptive_envelope()
    envelope["action"]["selected_todo"] = {
        "todo_id": "todo_stale_selection",
        "text": "A stale pre-orchestration selection",
    }
    envelope["task_orchestration_contract"]["primary_todo_id"] = "todo_primary"
    lineage = {
        "goal_id": "fixture-goal",
        "agent_id": "codex-fixture",
        "todo_id": "todo_primary",
    }
    _store_codex_cli_session(
        tmp_path,
        lineage=lineage,
        session_id="session-primary",
    )

    assert codex_cli_session_binding(tmp_path, envelope) == {
        "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
        **lineage,
    }


def test_codex_session_store_falls_back_when_fchmod_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "fchmod", raising=False)
    envelope = _envelope()
    lineage = {
        "goal_id": "fixture-goal",
        "agent_id": "codex-fixture",
        "todo_id": "todo_fixture0001",
    }

    _store_codex_cli_session(
        tmp_path,
        lineage=lineage,
        session_id="session-without-fchmod",
    )

    assert codex_cli_session_binding(tmp_path, envelope) == {
        "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
        **lineage,
    }


def test_generic_host_does_not_project_unqualified_child_operations() -> None:
    payload = build_loopx_turn_plan(
        _adaptive_envelope(),
        host="generic-cli",
        execution_mode="isolated-headless",
    )

    assert payload["ok"] is True
    assert "child_operations" not in payload
    assert "subagent_execution_topology" not in payload


def test_turn_host_request_carries_typed_child_operations() -> None:
    plan = build_loopx_turn_plan(
        _adaptive_envelope(),
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    request = build_loopx_turn_host_request(plan)

    assert request["child_operations"] == plan["child_operations"]
    assert (
        request["subagent_execution_topology"]
        == plan["subagent_execution_topology"]
    )
    assert request["child_operations"][0]["available_contexts"][0] == "fresh"
    assert request["child_operations"][0]["host_adapter"] == {
        "host": "codex-cli",
        "native_operation": "spawn_agent",
        "arguments": {"fork_context": False},
        "requires_session": False,
    }
    assert request["result_contract"]["stdout"] == "one public-safe JSON object"


def test_turn_host_request_carries_reward_memory_decision_context() -> None:
    plan = build_loopx_turn_plan(
        _adaptive_envelope(),
        host="codex-cli",
        execution_mode="interactive-visible",
    )
    plan["reward_memory_recall"] = {
        "schema_version": "agent_turn_recall_v0",
        "status": "applied",
        "context": {
            "schema_version": "agent_turn_recall_context_v0",
            "guidance": [
                {
                    "candidate_ref": "candidate:reviewed",
                    "target_class": "soft_preference",
                    "content_summary": "Reuse the verified validation sequence.",
                }
            ],
        },
        "grants_new_action_authority": False,
    }

    request = build_loopx_turn_host_request(plan)

    assert request["reward_memory_recall"] == plan["reward_memory_recall"]
    assert request["reward_memory_recall"]["context"]["guidance"][0][
        "content_summary"
    ]


def test_turn_help_omits_legacy_agent_loop_entrypoint() -> None:
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(["--help"])

    assert exit_code == 0
    assert "agent-loop" not in output.getvalue()
    assert "turn" in output.getvalue()


def test_turn_plan_resumes_only_a_matching_session_binding() -> None:
    payload = build_loopx_turn_plan(
        _envelope(),
        host="codex-cli",
        execution_mode="interactive-visible",
        session_binding={
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            "goal_id": "fixture-goal",
            "agent_id": "codex-fixture",
            "todo_id": "todo_fixture0001",
        },
    )

    assert payload["ok"] is True
    assert payload["session"]["action"] == "resume"
    assert payload["boundary"]["opaque_session_handle_omitted"] is True


def test_turn_plan_rejects_session_binding_identity_drift() -> None:
    payload = build_loopx_turn_plan(
        _envelope(),
        host="codex-cli",
        execution_mode="interactive-visible",
        session_binding={
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            "goal_id": "another-goal",
            "agent_id": "codex-fixture",
            "todo_id": "todo_fixture0001",
        },
    )

    assert payload["ok"] is False
    assert payload["route"]["kind"] == LoopXTurnRoute.CONTRACT_ERROR.value
    assert payload["route"]["would_invoke_host"] is False
    assert payload["session"]["action"] == "reject"
    assert payload["session"]["binding_status"] == "identity_mismatch"
    assert payload["transaction"]["status"] == "not_applicable"
    assert payload["effects"]["host_invoked"] is False


def test_turn_plan_transaction_key_is_stable_and_todo_scoped() -> None:
    first = build_loopx_turn_plan(
        _envelope(),
        host="generic-cli",
        execution_mode="isolated-headless",
    )
    repeated = build_loopx_turn_plan(
        _envelope(),
        host="generic-cli",
        execution_mode="isolated-headless",
    )
    changed_envelope = _envelope()
    changed_envelope["action"]["selected_todo"]["todo_id"] = "todo_fixture0002"
    changed = build_loopx_turn_plan(
        changed_envelope,
        host="generic-cli",
        execution_mode="isolated-headless",
    )

    assert first["transaction"]["turn_key"] == repeated["transaction"]["turn_key"]
    assert first["transaction"]["turn_key"] != changed["transaction"]["turn_key"]
    assert (
        first["transaction"]["settlement_plan"]["identity"]["effect_id"]
        == repeated["transaction"]["settlement_plan"]["identity"]["effect_id"]
    )
    assert (
        first["transaction"]["settlement_plan"]["identity"]["effect_id"]
        != changed["transaction"]["settlement_plan"]["identity"]["effect_id"]
    )


def test_turn_plan_instance_id_distinguishes_new_turns_from_retries() -> None:
    first = build_loopx_turn_plan(
        _envelope(),
        host="generic-cli",
        execution_mode="isolated-headless",
        turn_instance_id="batch-42:turn-1",
    )
    retry = build_loopx_turn_plan(
        _envelope(),
        host="generic-cli",
        execution_mode="isolated-headless",
        turn_instance_id="batch-42:turn-1",
    )
    second = build_loopx_turn_plan(
        _envelope(),
        host="generic-cli",
        execution_mode="isolated-headless",
        turn_instance_id="batch-42:turn-2",
    )

    assert first["transaction"]["turn_instance_id"] == "batch-42:turn-1"
    assert first["transaction"]["turn_key"] == retry["transaction"]["turn_key"]
    assert first["transaction"]["turn_key"] != second["transaction"]["turn_key"]


def test_turn_plan_fresh_iteration_ignores_compatible_session_binding() -> None:
    payload = build_loopx_turn_plan(
        _envelope(),
        host="codex-cli",
        execution_mode="interactive-visible",
        session_binding={
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            "goal_id": "fixture-goal",
            "agent_id": "codex-fixture",
            "todo_id": "todo_fixture0001",
        },
        iteration_context_policy="fresh",
        turn_instance_id="cycle-2:iteration-1",
    )

    assert payload["ok"] is True
    assert payload["session"] == {
        "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
        "action": "start_new",
        "binding_status": "existing_binding_ignored",
        "context_policy": {
            "schema_version": "loopx_iteration_context_policy_v0",
            "mode": "fresh",
            "scope": "iteration",
        },
    }
    assert payload["transaction"]["turn_instance_id"] == "cycle-2:iteration-1"


def test_turn_plan_rejects_unknown_iteration_context_policy() -> None:
    with pytest.raises(ValueError, match="iteration context policy"):
        build_loopx_turn_plan(
            _envelope(),
            host="codex-cli",
            execution_mode="interactive-visible",
            iteration_context_policy="automatic",
        )


@pytest.mark.parametrize(
    "instance_id",
    ["", "contains space", "private/path", "x" * 129],
)
def test_turn_plan_rejects_unsafe_instance_id(instance_id: str) -> None:
    with pytest.raises(ValueError, match="turn_instance_id"):
        build_loopx_turn_plan(
            _envelope(),
            host="generic-cli",
            execution_mode="isolated-headless",
            turn_instance_id=instance_id,
        )


@pytest.mark.parametrize(
    ("effective_action", "expected"),
    [
        ("capability_bridge_repair", LoopXTurnRoute.REPAIR_REQUIRED),
        ("autonomous_replan", LoopXTurnRoute.REPLAN_REQUIRED),
        ("successor_replan_required", LoopXTurnRoute.REPLAN_REQUIRED),
    ],
)
def test_turn_plan_projects_typed_recovery_routes(
    effective_action: str,
    expected: LoopXTurnRoute,
) -> None:
    payload = build_loopx_turn_plan(
        _envelope(effective_action=effective_action),
        host="generic-cli",
        execution_mode="isolated-headless",
    )

    assert payload["route"]["kind"] == expected.value
    assert payload["host"]["explicit_isolation"] is True
    assert payload["host"]["scheduler_owner"] == "outer_controller"
    assert payload["scheduler_execution_context"] == {
        "schema_version": "scheduler_execution_context_v0",
        "host_surface": "generic_cli",
        "scheduler_owner": "outer_controller",
        "execution_mode": "isolated_headless",
        "source": "loopx_turn",
        "valid": True,
        "codex_app_applicability": "not_applicable",
    }


def test_turn_plan_rejects_contradictory_scheduler_owner() -> None:
    payload = build_loopx_turn_plan(
        _envelope(),
        host="generic-cli",
        execution_mode="isolated-headless",
        scheduler_owner="host_automation",
    )

    assert payload["ok"] is False
    assert payload["route"]["kind"] == LoopXTurnRoute.CONTRACT_ERROR.value
    assert payload["route"]["would_invoke_host"] is False
    assert "cannot be owned by host_automation" in payload["error"]


def test_turn_plan_preserves_safe_bypass_when_user_action_is_visible() -> None:
    payload = build_loopx_turn_plan(
        _envelope(action_required=True),
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    assert payload["route"]["kind"] == LoopXTurnRoute.READY_FOR_HOST.value


@pytest.mark.parametrize(
    ("action_required", "quiet_noop_allowed", "expected"),
    [
        (True, False, LoopXTurnRoute.USER_ACTION_REQUIRED),
        (False, True, LoopXTurnRoute.WAIT),
        (False, False, LoopXTurnRoute.BLOCKED),
    ],
)
def test_turn_plan_projects_non_run_routes(
    action_required: bool,
    quiet_noop_allowed: bool,
    expected: LoopXTurnRoute,
) -> None:
    payload = build_loopx_turn_plan(
        _envelope(
            should_run=False,
            action_required=action_required,
            quiet_noop_allowed=quiet_noop_allowed,
        ),
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    assert payload["route"]["kind"] == expected.value
    assert payload["route"]["would_invoke_host"] is False
    assert payload["session"]["action"] == "none"
    assert payload["transaction"]["status"] == "not_applicable"
    assert payload["transaction"]["phases"] == []


def test_turn_plan_fails_closed_on_action_signature_drift() -> None:
    envelope = _envelope()
    envelope["action_signature"] = {"matches": False}

    payload = build_loopx_turn_plan(
        envelope,
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    assert payload["ok"] is False
    assert payload["route"]["kind"] == LoopXTurnRoute.CONTRACT_ERROR.value
    assert payload["route"]["would_invoke_host"] is False


def test_turn_plan_preserves_route_on_budget_warning() -> None:
    envelope = _envelope()
    envelope["compaction"] = {"within_budget": False}

    payload = build_loopx_turn_plan(
        envelope,
        host="codex-cli",
        execution_mode="interactive-visible",
    )

    assert payload["ok"] is True
    assert payload["route"]["kind"] == LoopXTurnRoute.READY_FOR_HOST.value
    assert payload["turn_envelope"]["compaction"]["within_budget"] is False


def test_scheduler_followup_binding_preserves_turn_lineage(
    tmp_path: Path,
) -> None:
    payload = {
        "scheduler_hint": {
            "codex_app": {"ack_hint": {"cli_args": ["quota", "scheduler-ack-current"]}}
        }
    }

    bind_scheduler_followup_cli_routes(
        payload,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        source="loopx_turn_plan",
    )

    ack_hint = payload["scheduler_hint"]["codex_app"]["ack_hint"]
    assert ack_hint["cli_args"][:2] == ["--registry", str(tmp_path / "registry.json")]
    assert ack_hint["route_binding"]["source"] == "loopx_turn_plan"


def _write_live_fixture(
    root: Path,
    *,
    todo_metadata_extra: str = "",
    extra_agent_todo_lines: tuple[str, ...] = (),
) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    state = project / ".codex" / "goals" / "loopx-turn-fixture" / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    agent_todo_lines = [
        "- [ ] [P0] Advance one public fixture.",
        "  <!-- loopx:todo todo_id=todo_fixture0001 status=open "
        "task_class=advancement_task action_kind=fixture claimed_by=codex-fixture "
        "priority=P0"
        + (f" {todo_metadata_extra}" if todo_metadata_extra else "")
        + " -->",
        "",
    ]
    if extra_agent_todo_lines:
        agent_todo_lines.extend(extra_agent_todo_lines)
        agent_todo_lines.append("")
    state.write_text(
        "\n".join(
            [
                "---",
                "status: active",
                "updated_at: 2026-01-01T00:00:00+00:00",
                "---",
                "",
                "# LoopX Turn Fixture",
                "",
                "## Agent Todo",
                "",
                *agent_todo_lines,
            ]
        ),
        encoding="utf-8",
    )
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": "loopx-turn-fixture",
                        "domain": "loopx-turn-public-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state.relative_to(project)),
                        "adapter": {
                            "kind": "fixture_v0",
                            "status": "connected-delivery",
                        },
                        "quota": {"compute": 1.0, "window_hours": 24},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["codex-fixture"],
                            "agent_profiles": {
                                "codex-fixture": {
                                    "schema_version": "agent_profile_v1",
                                    "profile_role": "fixture",
                                    "scope": "public qualification",
                                }
                            },
                            "write_scope": ["docs/**"],
                        },
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return project, runtime, registry


def _promote_turn_fixture(project: Path, runtime: Path) -> None:
    state = (
        project
        / ".codex"
        / "goals"
        / "loopx-turn-fixture"
        / "ACTIVE_GOAL_STATE.md"
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id="loopx-turn-fixture",
        handoff_mode="soft_claim",
        todos=[
            {
                "schema_version": "todo_item_v0",
                "index": 1,
                "done": False,
                "text": "[P0] Advance one public fixture.",
                "todo_id": "todo_fixture0001",
                "role": "agent",
                "status": "open",
                "archive_state": "active",
                "source_section": "Agent Todo",
                "task_class": "advancement_task",
                "action_kind": "fixture",
                "claimed_by": "codex-fixture",
                "priority": "P0",
            }
        ],
    )
    initialize_canonical_authority(
        runtime,
        "loopx-turn-fixture",
        projection,
        state_path=state,
    )


def test_quota_cli_projects_outer_controller_without_codex_app_action(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "quota",
                "should-run",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--host-surface",
                "generic_cli",
                "--scheduler-owner",
                "outer_controller",
                "--execution-mode",
                "isolated_headless",
                "--scan-root",
                str(project),
            ]
        )

    payload = json.loads(output.getvalue())
    hint = payload["scheduler_hint"]
    assert exit_code == 0, payload
    assert hint["execution_context"]["codex_app_applicability"] == "not_applicable"
    assert hint["codex_app"]["applicability"] == "not_applicable"
    assert "stateful_backoff" not in hint["codex_app"]
    assert hint["execution_phase"]["scheduler_owner"] == "outer_controller"
    assert hint["execution_phase"]["completed"] is True
    assert hint["execution_phase"]["apply_needed"] is False
    assert hint["execution_phase"]["ack_needed"] is False


def test_quota_cli_without_scheduler_context_fails_closed(tmp_path: Path) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "quota",
                "should-run",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--scan-root",
                str(project),
            ]
        )

    payload = json.loads(output.getvalue())
    hint = payload["scheduler_hint"]
    assert exit_code == 0, payload
    assert hint["action"] == "repair_scheduler_execution_context"
    assert hint["execution_context"]["valid"] is False
    assert hint["codex_app"]["applicability"] == "blocked_invalid_context"


@pytest.mark.parametrize(
    "profile_args",
    (
        ["--runtime-profile", "codex_app_heartbeat"],
        ["--codex-app"],
    ),
)
def test_quota_cli_codex_app_profile_is_explicit_and_applicable(
    tmp_path: Path,
    profile_args: list[str],
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "quota",
                "should-run",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                *profile_args,
                "--scan-root",
                str(project),
            ]
        )

    payload = json.loads(output.getvalue())
    hint = payload["scheduler_hint"]
    assert exit_code == 0, payload
    assert "execution_context" not in hint
    assert "execution_phase" not in hint
    assert hint["codex_app"]["applicability"] == "applicable"
    assert "stateful_backoff" in hint["codex_app"]


def test_quota_cli_short_context_flags_preserve_all_typed_fields(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "quota",
                "should-run",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "-H",
                "generic_cli",
                "-O",
                "agent_cli_loop",
                "-M",
                "interactive",
                "--scan-root",
                str(project),
            ]
        )

    payload = json.loads(output.getvalue())
    context = payload["scheduler_hint"]["execution_context"]
    assert exit_code == 0, payload
    assert context["host_surface"] == "generic_cli"
    assert context["scheduler_owner"] == "agent_cli_loop"
    assert context["execution_mode"] == "interactive"
    assert context["codex_app_applicability"] == "not_applicable"


def test_heartbeat_cli_codex_app_alias_reaches_generated_quota_guard(
    tmp_path: Path,
) -> None:
    _, runtime, registry = _write_live_fixture(tmp_path)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "heartbeat-prompt",
                "--thin",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--codex-app",
            ]
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload
    assert payload["schema_version"] == "heartbeat_agent_input_v1"
    assert "runtime_profile" not in payload
    assert "quota_guard_command" not in payload
    assert "--codex-app" in payload["task_body"]


def test_turn_cli_consumes_live_state_without_writes(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "turn",
                "plan",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--scan-root",
                str(project),
                "--include-transaction-detail",
            ]
        )

    payload = json.loads(output.getvalue())
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert exit_code == 0
    assert payload["route"]["kind"] == LoopXTurnRoute.READY_FOR_HOST.value
    assert payload["session"]["action"] == "start_new"
    assert "settlement_plan" not in payload["transaction"]
    assert payload["turn_envelope"]["action_signature"]["matches"] is True
    assert payload["effects"]["state_written"] is False
    assert before == after


def test_turn_cli_resolves_its_decision_through_the_shared_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``turn`` must not re-inline the chain it shares with the managed step.

    A private copy inside ``loopx/cli_commands/turn.py`` is not a formatting
    problem: adding one decision input to the shared owner would then move only
    one subcommand, and ``run-once`` and ``managed-step`` would disagree about
    what the current Turn should do. Reading the live status through the shared
    owner is the fact that identifies a private copy.
    """

    from loopx.cli_commands import turn_decision

    project, runtime, registry = _write_live_fixture(tmp_path)
    status_reads: list[dict[str, Any]] = []
    real_collect_status = turn_decision.collect_status

    def recording_collect_status(*args: Any, **kwargs: Any) -> dict[str, Any]:
        status_reads.append(dict(kwargs))
        return real_collect_status(*args, **kwargs)

    monkeypatch.setattr(turn_decision, "collect_status", recording_collect_status)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "turn",
                "plan",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--scan-root",
                str(project),
            ]
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload
    assert payload["turn_envelope"]["schema_version"] == "loopx_turn_envelope_v0"
    assert len(status_reads) == 1, (
        "`turn` must read its live status through the shared Turn decision "
        "owner so run-once and managed-step cannot drift; shared-owner status "
        f"reads: {len(status_reads)}"
    )


def test_turn_cli_projects_explicit_fresh_iteration_context(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "turn",
                "plan",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--scan-root",
                str(project),
                "--iteration-context",
                "fresh",
                "--include-transaction-detail",
            ]
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload
    assert payload["session"]["action"] == "start_new"
    assert payload["session"]["context_policy"] == {
        "schema_version": "loopx_iteration_context_policy_v0",
        "mode": "fresh",
        "scope": "iteration",
    }


def test_turn_cli_binds_advisory_primary_without_hiding_portfolio(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_live_fixture(
        tmp_path,
        todo_metadata_extra="successor_todo_ids=todo_fixture0002",
        extra_agent_todo_lines=(
            "- [ ] [P2] Continue the public fixture.",
            "  <!-- loopx:todo todo_id=todo_fixture0002 status=open "
            "task_class=advancement_task priority=P2 -->",
        ),
    )
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "turn",
                "plan",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--scan-root",
                str(project),
            ]
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload
    assert payload["route"]["kind"] == LoopXTurnRoute.READY_FOR_HOST.value
    envelope = payload["turn_envelope"]
    assert envelope["action"]["delivery_allowed"] is True
    assert envelope["action"]["selected_todo"]["selected_by"] == (
        "turn_controller_advisory_primary"
    )
    portfolio = envelope["action"]["action_portfolio"]
    assert portfolio["schema_version"] == "quota_action_portfolio_v2"
    assert portfolio["primary"]["todo_id"] == "todo_fixture0001"
    assert portfolio["selection_policy"]["requires_explicit_turn_binding"] is True
    assert envelope["writeback"].get("selection_required") is None


@pytest.mark.parametrize("selection", [None, "todo_fixture0002", "todo_missing"])
def test_turn_cli_explicit_todo_keeps_default_and_never_falls_back(tmp_path, selection):
    project, runtime, registry = _write_live_fixture(tmp_path, extra_agent_todo_lines=(
        "- [ ] [P2] Check the second public fixture.",
        "  <!-- loopx:todo todo_id=todo_fixture0002 status=open task_class=advancement_task "
        "action_kind=fixture claimed_by=codex-fixture priority=P2 -->",
    ))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli_main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
                         "turn", "plan", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
                         "--scan-root", str(project), *(["--todo-id", selection] if selection else [])])
    result = json.loads(output.getvalue())
    if selection == "todo_missing":
        assert code == 1
        assert "no alternate task" in result["error"]
    else:
        assert code == 0, result
        selected = result["turn_envelope"]["action"]["selected_todo"]
        assert selected["todo_id"] == (selection or "todo_fixture0001")
        assert selected["selected_by"] == ("turn_explicit_todo" if selection else "turn_controller_advisory_primary")


@pytest.mark.parametrize("extra", [
    ["--resume-turn-key", "sha256:fixture"],
    ["--resume-goal-id", "loopx-turn-fixture", "--resume-agent-id", "codex-fixture",
     "--resume-todo-id", "todo_fixture0001"],
])
def test_turn_explicit_selection_cannot_retarget_resumption(tmp_path, extra):
    project, runtime, registry = _write_live_fixture(tmp_path)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli_main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
                         "turn", "run-once", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
                         "--project", str(project), "--scan-root", str(project), "--todo-id", "todo_fixture0002", *extra])
    assert code == 1
    assert "cannot retarget" in json.loads(output.getvalue())["error"]


@pytest.mark.parametrize("status,claim", [("done", "codex-fixture"), ("open", "other-agent")])
def test_explicit_turn_todo_does_not_bypass_completion_or_actor_scope(tmp_path, status, claim):
    project, runtime, registry = _write_live_fixture(tmp_path, extra_agent_todo_lines=(
        f"- [{'x' if status == 'done' else ' '}] [P2] Scoped second fixture.",
        f"  <!-- loopx:todo todo_id=todo_fixture0002 status={status} task_class=advancement_task "
        f"action_kind=fixture claimed_by={claim} priority=P2 -->",
    ))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli_main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
                         "turn", "plan", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
                         "--scan-root", str(project), "--todo-id", "todo_fixture0002"])
    result = json.loads(output.getvalue())
    assert code == 1, result
    assert "no alternate task" in result["error"]


def test_turn_cli_omits_transaction_detail_by_default(tmp_path: Path) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "turn",
                "plan",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--scan-root",
                str(project),
            ]
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 0
    assert "session" not in payload
    assert "transaction" not in payload
    assert "opaque_session_handle_omitted" not in payload["boundary"]


def test_turn_cli_requires_complete_resume_identity(tmp_path: Path) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "turn",
                "plan",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--resume-goal-id",
                "loopx-turn-fixture",
                "--scan-root",
                str(project),
            ]
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 1
    assert payload["ok"] is False
    assert "requires --resume-goal-id" in payload["error"]


def test_turn_run_once_cli_commits_validated_result_and_one_quota_slot(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    host_project = tmp_path / "isolated-host-workspace"
    host_project.mkdir()
    host_script = """
import json
import pathlib
import sys
request = json.load(sys.stdin)
pathlib.Path("fixture-artifact.txt").write_text("validated", encoding="utf-8")
json.dump({
    "schema_version": "loopx_turn_result_v0",
    "turn_key": request["turn_key"],
    "result_kind": "validated_progress",
    "completed_phases": ["host_execute", "typed_result"],
    "classification": "fixture_progress",
    "recommended_action": "Continue the public fixture",
    "next_action": "Run the next public fixture check",
    "delivery_batch_scale": "implementation",
    "delivery_outcome": "outcome_progress",
    "vision_unchanged_reason": "The fixture objective remains unchanged.",
    "summary": "One public fixture advanced."
}, sys.stdout)
"""
    validation_script = """
import json
import pathlib
import sys
json.load(sys.stdin)
artifact = pathlib.Path("fixture-artifact.txt")
raise SystemExit(0 if artifact.read_text(encoding="utf-8") == "validated" else 7)
"""
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "turn",
                "run-once",
                "--host",
                "generic-cli",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--project",
                str(host_project),
                "--host-adapter-command-json",
                json.dumps([sys.executable, "-c", host_script]),
                "--validation-command-json",
                json.dumps([sys.executable, "-c", validation_script]),
                "--scan-root",
                str(project),
                "--no-global-sync",
                "--execute",
            ]
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload
    assert payload["status"] == "committed"
    assert payload["receipt"]["status"] == "committed"
    assert payload["receipt"]["next_phase"] is None
    assert payload["validation"]["status"] == "passed"
    assert payload["validation"]["validator_kind"] == "command"
    assert payload["resume_turn_key"].startswith("sha256:")
    assert payload["scheduler"] == {
        "schema_version": "scheduler_execution_phase_v0",
        "host_surface": "generic_cli",
        "scheduler_owner": "outer_controller",
        "disposition": "outer_controller_owned",
        "completed": True,
        "apply_needed": False,
        "ack_needed": False,
        "acknowledged": False,
        "completion_reason": "selected scheduler owner requires no Codex App apply or ACK",
    }
    assert payload["effects"] == {
        "host_invoked": True,
        "state_written": True,
        "quota_spent": True,
        "scheduler_acknowledged": False,
    }
    state_path = (
        project
        / ".codex"
        / "goals"
        / "loopx-turn-fixture"
        / "ACTIVE_GOAL_STATE.md"
    )
    assert "Run the next public fixture check" in state_path.read_text(encoding="utf-8")
    index_path = runtime / "goals" / "loopx-turn-fixture" / "runs" / "index.jsonl"
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    assert [row["classification"] for row in rows] == [
        "fixture_progress",
        "quota_slot_spent",
    ]

    resumed_output = io.StringIO()
    with contextlib.redirect_stdout(resumed_output):
        resumed_exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "turn",
                "run-once",
                "--host",
                "generic-cli",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--project",
                str(host_project),
                "--host-command-json",
                json.dumps([sys.executable, "-c", host_script]),
                "--validation-command-json",
                json.dumps([sys.executable, "-c", validation_script]),
                "--scan-root",
                str(project),
                "--no-global-sync",
                "--resume-turn-key",
                payload["resume_turn_key"],
                "--execute",
            ]
        )

    resumed = json.loads(resumed_output.getvalue())
    assert resumed_exit_code == 0, resumed
    assert resumed["status"] == "committed"
    assert resumed["effects"] == {
        "host_invoked": False,
        "state_written": False,
        "quota_spent": False,
        "scheduler_acknowledged": False,
    }
    replayed_rows = [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["classification"] for row in replayed_rows] == [
        "fixture_progress",
        "quota_slot_spent",
    ]


def test_turn_run_once_cli_dsh_fresh_sessions_and_iteration_failure(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    host_project = tmp_path / "isolated-dsh-workspace"
    host_project.mkdir()
    runner = tmp_path / "recording_dsh_runner.py"
    runner.write_text(
        """
import json
from pathlib import Path

def run_dsh_turn(**kwargs):
    output = Path(kwargs["workspace"]) / "dsh-session-ids.txt"
    with output.open("a", encoding="utf-8") as stream:
        stream.write(kwargs["session_id"] + "\\n")
    return json.dumps({
        "result_kind": "iteration_failed",
        "classification": "fixture_iteration_failed",
        "summary": "The bounded fixture iteration did not complete.",
        "next_action": "Await a new controller-authorized iteration.",
    })
""",
        encoding="utf-8",
    )
    state_path = (
        project
        / ".codex"
        / "goals"
        / "loopx-turn-fixture"
        / "ACTIVE_GOAL_STATE.md"
    )
    before_state = state_path.read_text(encoding="utf-8")

    def run(turn_instance_id: str, context: str) -> dict[str, object]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = cli_main(
                [
                    "--registry",
                    str(registry),
                    "--runtime-root",
                    str(runtime),
                    "--format",
                    "json",
                    "turn",
                    "run-once",
                    "--goal-id",
                    "loopx-turn-fixture",
                    "--agent-id",
                    "codex-fixture",
                    "--host",
                    "dsh",
                    "--project",
                    str(host_project),
                    "--dsh-runner",
                    str(runner),
                    "--turn-instance-id",
                    turn_instance_id,
                    "--iteration-context",
                    context,
                    "--scan-root",
                    str(project),
                    "--no-global-sync",
                    "--execute",
                ]
            )
        payload = json.loads(output.getvalue())
        assert exit_code == 0, json.dumps(payload, indent=2)
        assert payload["result_kind"] == "iteration_failed"
        assert payload["status"] == "stopped"
        assert payload["effects"]["state_written"] is False
        assert payload["effects"]["quota_spent"] is False
        return payload

    run("fresh-fixture-1", "fresh")
    run("fresh-fixture-2", "fresh")
    run("resume-fixture-1", "resume-if-available")
    run("resume-fixture-2", "resume-if-available")

    session_ids = (host_project / "dsh-session-ids.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    assert session_ids[0].startswith("dsh-iteration-v1-")
    assert session_ids[0] != session_ids[1]
    assert session_ids[2].startswith("dsh-lineage-v1-")
    assert session_ids[2] == session_ids[3]
    assert state_path.read_text(encoding="utf-8") == before_state


def test_turn_run_once_cli_completes_selected_todo_after_validation(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    host_project = tmp_path / "isolated-host-workspace"
    host_project.mkdir()
    host_script = """
import json
import pathlib
import sys
request = json.load(sys.stdin)
pathlib.Path("completion-artifact.txt").write_text("completed", encoding="utf-8")
json.dump({
    "schema_version": "loopx_turn_result_v0",
    "turn_key": request["turn_key"],
    "result_kind": "validated_completion",
    "completed_phases": ["host_execute", "typed_result"],
    "classification": "fixture_completion",
    "recommended_action": "Refresh the active goal after completion.",
    "next_action": "Select the next Todo from a fresh decision.",
    "delivery_batch_scale": "implementation",
    "delivery_outcome": "outcome_progress",
    "vision_unchanged_reason": "The active goal may have further work.",
    "summary": "One public fixture completed."
}, sys.stdout)
"""
    validation_script = """
import json
import pathlib
import sys
json.load(sys.stdin)
artifact = pathlib.Path("completion-artifact.txt")
raise SystemExit(0 if artifact.read_text(encoding="utf-8") == "completed" else 7)
"""
    output = io.StringIO()
    argv = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "turn",
        "run-once",
        "--host",
        "generic-cli",
        "--goal-id",
        "loopx-turn-fixture",
        "--agent-id",
        "codex-fixture",
        "--project",
        str(host_project),
        "--host-adapter-command-json",
        json.dumps([sys.executable, "-c", host_script]),
        "--validation-command-json",
        json.dumps([sys.executable, "-c", validation_script]),
        "--scan-root",
        str(project),
        "--no-global-sync",
        "--execute",
    ]
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(argv)

    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload
    assert payload["status"] == "committed"
    assert payload["effects"]["state_written"] is True
    assert payload["effects"]["quota_spent"] is True
    journal_path = next(
        (runtime / "goals" / "loopx-turn-fixture" / "turns").glob("*.json")
    )
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["writeback"]["completion"] == {
        "todo_id": "todo_fixture0001",
        "continuation": "active_goal",
    }
    state_path = (
        project
        / ".codex"
        / "goals"
        / "loopx-turn-fixture"
        / "ACTIVE_GOAL_STATE.md"
    )
    state = state_path.read_text(encoding="utf-8")
    assert "todo_id=todo_fixture0001 status=done" in state
    assert "LoopX%20Turn%20validated%20completion" in state
    assert f"completion_turn_key={payload['resume_turn_key']}" in state

    next_plan_output = io.StringIO()
    with contextlib.redirect_stdout(next_plan_output):
        next_plan_exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "turn",
                "plan",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--host",
                "generic-cli",
                "--scheduler-owner",
                "outer_controller",
                "--execution-mode",
                "isolated-headless",
                "--scan-root",
                str(project),
            ]
        )
    next_plan = json.loads(next_plan_output.getvalue())
    assert next_plan_exit_code == 0, next_plan
    # The completion-created obligation is deferred only for the causal
    # closeout write.  It must be visible immediately at the next decision;
    # Turn itself stays blocked until that replan creates a runnable successor.
    assert next_plan["route"]["kind"] == "blocked"
    assert (
        next_plan["turn_envelope"]["effective_action"]
        == "autonomous_replan_required"
    )
    next_obligation = next_plan["turn_envelope"]["replan_action_packet"]
    assert next_obligation["decision"] == "replan_required"

    recovered_completion = complete_goal_todo(
        registry_path=registry,
        goal_id="loopx-turn-fixture",
        todo_id="todo_fixture0001",
        role="agent",
        agent_id="codex-fixture",
        completion_turn_key=payload["resume_turn_key"],
    )
    assert recovered_completion["idempotent_replay"] is True
    assert recovered_completion["changed"] is False

    replayed_output = io.StringIO()
    with contextlib.redirect_stdout(replayed_output):
        replayed_exit_code = cli_main(
            [
                *argv[:-1],
                "--resume-turn-key",
                payload["resume_turn_key"],
                "--execute",
            ]
        )
    replayed = json.loads(replayed_output.getvalue())
    assert replayed_exit_code == 0, replayed
    assert replayed["replayed"] is True
    assert not any(replayed["effects"].values())


def test_promoted_turn_completion_replays_after_commit_before_journal_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    _promote_turn_fixture(project, runtime)
    host_project = tmp_path / "isolated-host-workspace"
    host_project.mkdir()
    host_script, validation_script = _completion_host_and_validation_scripts()
    argv = _turn_run_once_completion_argv(
        host_project,
        runtime,
        registry,
        host_script,
        validation_script,
    )
    real_completion = turn_command.write_turn_validated_completion
    committed_results: list[dict[str, object]] = []

    def crash_after_canonical_completion(**kwargs: object) -> dict[str, object]:
        result = real_completion(**kwargs)
        committed_results.append(result)
        if len(committed_results) == 1:
            raise OSError("injected crash after canonical Todo commit")
        return result

    monkeypatch.setattr(
        turn_command,
        "write_turn_validated_completion",
        crash_after_canonical_completion,
    )
    first_output = io.StringIO()
    with contextlib.redirect_stdout(first_output):
        first_exit_code = cli_main(argv)
    first = json.loads(first_output.getvalue())

    assert first_exit_code == 1, first
    assert first["error"] == "injected crash after canonical Todo commit"
    assert committed_results[0]["status"] == "done"
    assert committed_results[0]["provider_status"] == "applied"
    assert committed_results[0]["idempotent_replay"] is False
    interrupted = _turn_journal(runtime)
    assert interrupted["effect_attempts"]["durable_writeback"]["status"] == "prepared"
    turn_key = str(interrupted["turn_key"])

    resumed_output = io.StringIO()
    with contextlib.redirect_stdout(resumed_output):
        resumed_exit_code = cli_main(
            [
                *argv[:-1],
                "--resume-turn-key",
                turn_key,
                "--execute",
            ]
        )
    resumed = json.loads(resumed_output.getvalue())

    assert resumed_exit_code == 0, json.dumps(resumed, indent=2)
    assert resumed["status"] == "committed"
    assert resumed["effects"]["host_invoked"] is False
    assert committed_results[1]["status"] == "done"
    assert committed_results[1]["provider_status"] == "replayed"
    assert committed_results[1]["idempotent_replay"] is True
    assert _turn_journal(runtime)["writeback"]["completion"] == {
        "todo_id": "todo_fixture0001",
        "continuation": "active_goal",
    }
    event_path = (
        runtime
        / "goals"
        / "loopx-turn-fixture"
        / "rollout-event-log.jsonl"
    )
    events = [
        json.loads(line)
        for line in event_path.read_text(encoding="utf-8").splitlines()
    ]
    assert sum(
        event.get("event_kind") == "todo_complete"
        and event.get("run_id") == turn_key
        for event in events
    ) == 1


def _completion_host_and_validation_scripts() -> tuple[str, str]:
    host_script = """
import json
import pathlib
import sys
request = json.load(sys.stdin)
pathlib.Path("completion-artifact.txt").write_text("completed", encoding="utf-8")
json.dump({
    "schema_version": "loopx_turn_result_v0",
    "turn_key": request["turn_key"],
    "result_kind": "validated_completion",
    "completed_phases": ["host_execute", "typed_result"],
    "classification": "fixture_completion",
    "recommended_action": "Refresh the active goal after completion.",
    "next_action": "Select the next Todo from a fresh decision.",
    "delivery_batch_scale": "implementation",
    "delivery_outcome": "outcome_progress",
    "vision_unchanged_reason": "The active goal may have further work.",
    "summary": "One public fixture completed."
}, sys.stdout)
"""
    validation_script = """
import json
import pathlib
import sys
json.load(sys.stdin)
artifact = pathlib.Path("completion-artifact.txt")
raise SystemExit(0 if artifact.read_text(encoding="utf-8") == "completed" else 7)
"""
    return host_script, validation_script


def _turn_run_once_completion_argv(
    project: Path,
    runtime: Path,
    registry: Path,
    host_script: str,
    validation_script: str,
) -> list[str]:
    return [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "turn",
        "run-once",
        "--host",
        "generic-cli",
        "--goal-id",
        "loopx-turn-fixture",
        "--agent-id",
        "codex-fixture",
        "--project",
        str(project),
        "--host-adapter-command-json",
        json.dumps([sys.executable, "-c", host_script]),
        "--validation-command-json",
        json.dumps([sys.executable, "-c", validation_script]),
        "--scan-root",
        str(project),
        "--no-global-sync",
        "--execute",
    ]


def _turn_journal(runtime: Path) -> dict[str, object]:
    journal_path = next(
        (runtime / "goals" / "loopx-turn-fixture" / "turns").glob("*.json")
    )
    return json.loads(journal_path.read_text(encoding="utf-8"))


def test_turn_run_once_cli_repairs_committed_quota_spend_after_receipt_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    host_project = tmp_path / "isolated-host-workspace"
    host_project.mkdir()
    host_script, validation_script = _completion_host_and_validation_scripts()
    argv = _turn_run_once_completion_argv(
        host_project,
        runtime,
        registry,
        host_script,
        validation_script,
    )
    append_rollout_event = turn_command.append_cli_rollout_event

    def crash_before_quota_receipt(
        payload: dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        if kwargs.get("event_kind") == "quota_spend":
            raise OSError("injected crash before quota receipt")
        return append_rollout_event(payload, **kwargs)

    monkeypatch.setattr(
        turn_command,
        "append_cli_rollout_event",
        crash_before_quota_receipt,
    )
    first_output = io.StringIO()
    with contextlib.redirect_stdout(first_output):
        first_exit_code = cli_main(argv)
    first = json.loads(first_output.getvalue())

    assert first_exit_code == 1, first
    assert first["error"] == "injected crash before quota receipt"
    interrupted = _turn_journal(runtime)
    turn_key = interrupted["turn_key"]
    effect_id = interrupted["plan"]["transaction"]["settlement_plan"][
        "identity"
    ]["effect_id"]
    assert interrupted["effect_attempts"] == {
        "quota_spend": {
            "status": "prepared",
            "effect_ref": f"{effect_id}#quota_spend",
        }
    }
    index_path = runtime / "goals" / "loopx-turn-fixture" / "runs" / "index.jsonl"
    rows = [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
    ]
    quota_rows = [row for row in rows if row.get("classification") == "quota_slot_spent"]
    assert len(quota_rows) == 1
    assert quota_rows[0]["effect_ref"] == f"{effect_id}#quota_spend"
    assert quota_rows[0]["agent_id"] == "codex-fixture"

    monkeypatch.setattr(
        turn_command,
        "append_cli_rollout_event",
        append_rollout_event,
    )
    resumed_output = io.StringIO()
    with contextlib.redirect_stdout(resumed_output):
        resumed_exit_code = cli_main(
            [
                *argv[:-1],
                "--resume-turn-key",
                turn_key,
                "--execute",
            ]
        )
    resumed = json.loads(resumed_output.getvalue())

    assert resumed_exit_code == 0, resumed
    assert resumed["status"] == "committed"
    replayed_rows = [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
    ]
    assert sum(
        row.get("classification") == "quota_slot_spent"
        for row in replayed_rows
    ) == 1
    events_path = (
        runtime / "goals" / "loopx-turn-fixture" / "rollout-event-log.jsonl"
    )
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        event.get("event_kind") == "quota_spend"
        and event.get("run_id") == turn_key
        and event.get("status") == "receipt_repaired"
        for event in events
    )


def test_turn_run_once_cli_projects_declared_successor_continuation(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_live_fixture(
        tmp_path,
        todo_metadata_extra="successor_todo_ids=todo_fixture0002",
        extra_agent_todo_lines=(
            "- [ ] [P2] Continue the public fixture.",
            "  <!-- loopx:todo todo_id=todo_fixture0002 status=open "
            "task_class=advancement_task priority=P2 -->",
        ),
    )
    host_project = tmp_path / "isolated-host-workspace"
    host_project.mkdir()
    host_script, validation_script = _completion_host_and_validation_scripts()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            _turn_run_once_completion_argv(
                host_project,
                runtime,
                registry,
                host_script,
                validation_script,
            )
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload
    assert payload["status"] == "committed"
    assert _turn_journal(runtime)["writeback"]["completion"] == {
        "todo_id": "todo_fixture0001",
        "continuation": "successor",
        "successor_todo_ids": ["todo_fixture0002"],
    }


def test_turn_run_once_cli_projects_durable_no_followup_continuation(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_live_fixture(
        tmp_path,
        todo_metadata_extra="no_followup=true",
    )
    host_project = tmp_path / "isolated-host-workspace"
    host_project.mkdir()
    host_script, validation_script = _completion_host_and_validation_scripts()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            _turn_run_once_completion_argv(
                host_project,
                runtime,
                registry,
                host_script,
                validation_script,
            )
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload
    assert payload["status"] == "committed"
    assert _turn_journal(runtime)["writeback"]["completion"] == {
        "todo_id": "todo_fixture0001",
        "continuation": "no_followup",
    }


@pytest.mark.parametrize(
    "durable_completion_turn_key",
    [None, "sha256:different-turn"],
    ids=["missing-turn-key", "different-turn-key"],
)
def test_turn_run_once_cli_terminal_recovery_rejects_unowned_completion(
    tmp_path: Path,
    durable_completion_turn_key: str | None,
) -> None:
    project, runtime, registry = _write_live_fixture(
        tmp_path,
        todo_metadata_extra="no_followup=true",
    )
    host_project = tmp_path / "isolated-host-workspace"
    host_project.mkdir()
    host_script, validation_script = _completion_host_and_validation_scripts()
    argv = _turn_run_once_completion_argv(
        host_project,
        runtime,
        registry,
        host_script,
        validation_script,
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(argv)
    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload

    turn_key = payload["resume_turn_key"]
    state_path = (
        project
        / ".codex"
        / "goals"
        / "loopx-turn-fixture"
        / "ACTIVE_GOAL_STATE.md"
    )
    state = state_path.read_text(encoding="utf-8")
    current_turn_field = f" completion_turn_key={turn_key}"
    assert current_turn_field in state
    replacement = (
        ""
        if durable_completion_turn_key is None
        else f" completion_turn_key={durable_completion_turn_key}"
    )
    state_path.write_text(
        state.replace(current_turn_field, replacement),
        encoding="utf-8",
    )

    event_path = (
        runtime / "goals" / "loopx-turn-fixture" / "rollout-event-log.jsonl"
    )
    events = [
        json.loads(line)
        for line in event_path.read_text(encoding="utf-8").splitlines()
    ]
    events_without_terminal = [
        event
        for event in events
        if not (
            event.get("event_kind") == "todo_complete"
            and event.get("run_id") == turn_key
        )
    ]
    assert len(events_without_terminal) == len(events) - 1
    event_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events_without_terminal),
        encoding="utf-8",
    )

    journal_path = next(
        (runtime / "goals" / "loopx-turn-fixture" / "turns").glob("*.json")
    )
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    identity = journal["plan"]["transaction"]["settlement_plan"]["identity"]
    journal["status"] = "in_progress"
    journal["completed_phases"] = [
        "host_execute",
        "typed_result",
        "validation",
        "durable_writeback",
        "quota_spend",
    ]
    journal["effect_attempts"] = {
        "terminal_closeout": {
            "status": "prepared",
            "effect_ref": f"{identity['effect_id']}#terminal_closeout",
        }
    }
    for key in (
        "terminal_closeout",
        "settlement_result",
        "scheduler",
        "receipt",
        "reason",
    ):
        journal.pop(key, None)
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    resumed_output = io.StringIO()
    with contextlib.redirect_stdout(resumed_output):
        resumed_exit_code = cli_main(
            [
                *argv[:-1],
                "--resume-turn-key",
                turn_key,
                "--execute",
            ]
        )
    resumed = json.loads(resumed_output.getvalue())

    assert resumed_exit_code == 1, resumed
    assert resumed["settlement_result"]["failure"] == {
        "kind": "effect_outcome_unknown",
        "step_kind": "terminal_closeout",
        "reason": "closeout readback failed",
    }
    recovered_events = [
        json.loads(line)
        for line in event_path.read_text(encoding="utf-8").splitlines()
    ]
    assert not any(
        event.get("event_kind") == "todo_complete"
        and event.get("run_id") == turn_key
        for event in recovered_events
    )


def test_turn_run_once_cli_fails_closed_on_dangling_declared_successor(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_live_fixture(
        tmp_path,
        todo_metadata_extra="successor_todo_ids=todo_missing999",
    )
    host_project = tmp_path / "isolated-host-workspace"
    host_project.mkdir()
    host_script, validation_script = _completion_host_and_validation_scripts()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            _turn_run_once_completion_argv(
                host_project,
                runtime,
                registry,
                host_script,
                validation_script,
            )
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 1, payload
    assert payload["result_kind"] == "writeback_failed"
    assert payload["receipt"]["failed_phase"] == "durable_writeback"
    failure = payload["settlement_result"]["failure"]
    assert failure["kind"] == "writeback_rejected"
    assert "missing successor Todo ids: todo_missing999" in failure["reason"]
    assert payload["effects"] == {
        "host_invoked": True,
        "state_written": False,
        "quota_spent": False,
        "scheduler_acknowledged": False,
    }


def test_turn_run_once_cli_replays_declared_successor_after_interruption(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_live_fixture(
        tmp_path,
        todo_metadata_extra="successor_todo_ids=todo_fixture0002",
        extra_agent_todo_lines=(
            "- [ ] [P2] Continue the public fixture.",
            "  <!-- loopx:todo todo_id=todo_fixture0002 status=open "
            "task_class=advancement_task priority=P2 -->",
        ),
    )
    host_project = tmp_path / "isolated-host-workspace"
    host_project.mkdir()
    host_script, validation_script = _completion_host_and_validation_scripts()
    argv = _turn_run_once_completion_argv(
        host_project,
        runtime,
        registry,
        host_script,
        validation_script,
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(argv)
    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload

    journal_path = next(
        (runtime / "goals" / "loopx-turn-fixture" / "turns").glob("*.json")
    )
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    # Simulate a process interruption after the durable writeback committed but
    # before quota spend settled: the retry must re-project the same durable
    # continuation from the persisted Todo lifecycle, not a normalized one.
    journal["status"] = "in_progress"
    journal["completed_phases"] = [
        "host_execute",
        "typed_result",
        "validation",
        "durable_writeback",
    ]
    for key in ("receipt", "quota_spend", "settlement_result", "scheduler", "reason"):
        journal.pop(key, None)
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    resumed_output = io.StringIO()
    with contextlib.redirect_stdout(resumed_output):
        resumed_exit_code = cli_main(
            [
                *argv[:-1],
                "--resume-turn-key",
                payload["resume_turn_key"],
                "--execute",
            ]
        )
    resumed = json.loads(resumed_output.getvalue())
    assert resumed_exit_code == 0, resumed
    assert resumed["status"] == "committed"
    assert _turn_journal(runtime)["writeback"]["completion"] == {
        "todo_id": "todo_fixture0001",
        "continuation": "successor",
        "successor_todo_ids": ["todo_fixture0002"],
    }


def test_turn_run_once_commits_independently_validated_progress(
    tmp_path: Path,
) -> None:
    plan = build_loopx_turn_plan(
        _envelope(),
        host="generic-cli",
        execution_mode="isolated-headless",
    )

    def host_runner(request: dict[str, object]) -> dict[str, object]:
        return {
            "schema_version": "loopx_turn_result_v0",
            "turn_key": request["turn_key"],
            "result_kind": "validated_progress",
            "completed_phases": ["host_execute", "typed_result"],
            "classification": "fixture_intermediate_progress",
            "recommended_action": "Continue the fixture",
            "next_action": "Run the next bounded fixture Turn",
            "delivery_batch_scale": "single_surface",
            "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "The fixture objective remains open.",
            "summary": "One intermediate fixture step passed validation.",
            "reward_memory_reflection_json": json.dumps(
                {
                    "schema_version": "turn_reward_memory_reflection_v0",
                    "status": "eligible",
                    "surface_id": "agent_workflow.turn_admission",
                    "outcome_kind": "engineering",
                    "content_summary": "Reuse the verified fixture sequence.",
                    "reasoning_summary": "The independent validator passed.",
                    "confidence": "high",
                    "evidence_refs": ["artifact:fixture-validation"],
                }
            ),
        }

    observed_reflections: list[str] = []
    observed_settlement_evidence: list[Mapping[str, Any]] = []

    def post_settlement(
        _plan: Mapping[str, Any],
        result: Mapping[str, Any],
        settlement_evidence: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        observed_reflections.append(str(result["reward_memory_reflection_json"]))
        observed_settlement_evidence.append(settlement_evidence)
        return {
            "ok": True,
            "schema_version": "turn_reward_memory_ingest_v0",
            "status": "activated",
            "source_event_id": "fixture-event",
        }

    execution = run_loopx_turn_once(
        plan,
        host_runner=host_runner,
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=10,
        execute=True,
        task_validator=lambda _plan, result: {
            "status": "progress",
            "validator_kind": "fixture",
            "summary": "intermediate fixture progress is independently valid",
            "exit_code": 10,
            "reward_memory_reflection_validation": {
                "schema_version": "reward_memory_reflection_validation_v0",
                "status": "validated",
                "reflection_digest": reward_memory_reflection_digest(
                    result["reward_memory_reflection_json"]
                ),
                "evidence_refs": ["artifact:fixture-validation"],
            },
        },
        writeback=lambda _result: {"ok": True, "appended": True},
        spend=lambda: {"ok": True, "appended": True, "slots": 1},
        scheduler=lambda _spend: {
            "disposition": "outer_controller_owned",
            "completed": True,
            "acknowledged": False,
            "apply_needed": False,
        },
        post_settlement=post_settlement,
    )

    assert execution["status"] == "committed"
    assert execution["validation"]["status"] == "progress"
    assert execution["validation"]["exit_code"] == 10
    assert execution["quota_slot_spend_count"] == 1
    assert execution["post_settlement"]["status"] == "activated"
    assert len(observed_reflections) == 1
    assert observed_settlement_evidence == [
        {
            "schema_version": "turn_post_settlement_evidence_v0",
            "task_validation": execution["validation"],
            "writeback": {"ok": True, "appended": True},
            "quota_spend": {"ok": True, "appended": True, "slots": 1},
        }
    ]
    assert loopx_turn_execution_committed(execution) is True

    replay = run_loopx_turn_once(
        plan,
        host_runner=host_runner,
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=10,
        execute=True,
        task_validator=lambda _plan, result: {
            "status": "progress",
            "validator_kind": "fixture",
            "summary": "intermediate fixture progress is independently valid",
            "exit_code": 10,
            "reward_memory_reflection_validation": {
                "schema_version": "reward_memory_reflection_validation_v0",
                "status": "validated",
                "reflection_digest": reward_memory_reflection_digest(
                    result["reward_memory_reflection_json"]
                ),
                "evidence_refs": ["artifact:fixture-validation"],
            },
        },
        writeback=lambda _result: {"ok": True, "appended": True},
        spend=lambda: {"ok": True, "appended": True, "slots": 1},
        scheduler=lambda _spend: {
            "disposition": "outer_controller_owned",
            "completed": True,
            "acknowledged": False,
            "apply_needed": False,
        },
        post_settlement=post_settlement,
    )
    assert replay["replayed"] is True
    assert replay["post_settlement"]["status"] == "activated"
    assert len(observed_reflections) == 1
    assert len(observed_settlement_evidence) == 1


def test_turn_run_once_cli_rejects_unproven_host_claim_before_writeback(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    host_project = tmp_path / "isolated-host-workspace"
    host_project.mkdir()
    host_script = """
import json
import sys
request = json.load(sys.stdin)
json.dump({
    "schema_version": "loopx_turn_result_v0",
    "turn_key": request["turn_key"],
    "result_kind": "validated_progress",
    "completed_phases": ["host_execute", "typed_result"],
    "classification": "unproven_fixture_progress",
    "recommended_action": "Continue the public fixture",
    "next_action": "Run the next public fixture check",
    "delivery_batch_scale": "implementation",
    "delivery_outcome": "outcome_progress",
    "vision_unchanged_reason": "The fixture objective remains unchanged.",
    "summary": "The host claims a missing artifact."
}, sys.stdout)
"""
    validation_script = """
import json
import pathlib
import sys
json.load(sys.stdin)
raise SystemExit(0 if pathlib.Path("claimed-artifact.txt").is_file() else 9)
"""
    state_path = (
        project
        / ".codex"
        / "goals"
        / "loopx-turn-fixture"
        / "ACTIVE_GOAL_STATE.md"
    )
    before_state = state_path.read_text(encoding="utf-8")
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "turn",
                "run-once",
                "--host",
                "generic-cli",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--project",
                str(host_project),
                "--host-command-json",
                json.dumps([sys.executable, "-c", host_script]),
                "--validation-command-json",
                json.dumps([sys.executable, "-c", validation_script]),
                "--validation-failure-kind",
                "replan_required",
                "--scan-root",
                str(project),
                "--no-global-sync",
                "--execute",
            ]
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 1, payload
    assert payload["status"] == "failed"
    assert payload["result_kind"] == "validation_failed"
    assert payload["validation"]["status"] == "failed"
    assert payload["validation"]["recovery_kind"] == "replan_required"
    assert payload["validation"]["exit_code"] == 9
    assert payload["effects"]["host_invoked"] is True
    assert payload["effects"]["state_written"] is False
    assert payload["effects"]["quota_spent"] is False
    assert state_path.read_text(encoding="utf-8") == before_state
    assert not (runtime / "goals" / "loopx-turn-fixture" / "runs").exists()


@pytest.mark.parametrize(
    "result_kind", ["validated_progress", "repair_required", "replan_required"]
)
def test_turn_run_once_cli_uses_built_in_codex_host_and_typed_writeback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result_kind: str,
) -> None:
    from loopx.cli_commands.turn import (
        build_turn_envelope as real_build_turn_envelope,
        refresh_state_run as real_refresh_state_run,
        spend_quota_slot as real_spend_quota_slot,
    )

    from loopx.cli_commands.turn_todo_writeback import (
        update_goal_todo as real_update_goal_todo,
    )

    project, runtime, registry = _write_live_fixture(tmp_path)
    state_path = (
        project
        / ".codex"
        / "goals"
        / "loopx-turn-fixture"
        / "ACTIVE_GOAL_STATE.md"
    )
    state_path.write_text(
        state_path.read_text(encoding="utf-8").replace(
            "priority=P0 -->",
            "task_repository=git:example.invalid/loopx/turn-fixture priority=P0 -->",
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "init", "--initial-branch", "main", str(project)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "remote",
            "add",
            "origin",
            "https://example.invalid/loopx/turn-fixture.git",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    refresh_workspace_paths: list[Path | None] = []
    spend_workspace_paths: list[Path | None] = []
    updated_todo_ids: list[str] = []

    def adaptive_turn_envelope(*args: object, **kwargs: object) -> dict[str, object]:
        envelope = real_build_turn_envelope(*args, **kwargs)
        envelope["action"]["selected_todo"] = {
            "todo_id": "todo_stale_selection",
            "text": "A stale pre-orchestration selection",
        }
        envelope["task_orchestration_contract"] = {
            "schema_version": "task_orchestration_contract_v2",
            "mode": "adaptive",
            "primary_todo_id": "todo_fixture0001",
            "eligible_child_lanes": [],
        }
        return envelope

    def recording_refresh_state_run(*args: object, **kwargs: object) -> dict[str, object]:
        refresh_workspace_paths.append(kwargs.get("delivery_workspace_path"))
        return real_refresh_state_run(*args, **kwargs)

    def recording_spend_quota_slot(*args: object, **kwargs: object) -> dict[str, object]:
        spend_workspace_paths.append(kwargs.get("workspace_path"))
        return real_spend_quota_slot(*args, **kwargs)

    def recording_update_goal_todo(*args: object, **kwargs: object) -> dict[str, object]:
        updated_todo_ids.append(str(kwargs.get("todo_id") or ""))
        return real_update_goal_todo(*args, **kwargs)

    def fake_codex_host(request: dict[str, object], **_kwargs: object) -> dict[str, object]:
        return {
            "schema_version": "loopx_turn_result_v0",
            "turn_key": request["turn_key"],
            "result_kind": result_kind,
            "completed_phases": ["host_execute", "typed_result"],
            "classification": f"fixture_{result_kind}",
            "recommended_action": "Apply the typed follow-up",
            "next_action": "Run one revised public fixture check",
            "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "The fixture objective remains unchanged.",
            "summary": "One public fixture advanced.",
        }

    monkeypatch.setattr("loopx.cli_commands.turn.run_codex_cli_host", fake_codex_host)
    monkeypatch.setattr(
        "loopx.cli_commands.turn.build_turn_envelope",
        adaptive_turn_envelope,
    )
    monkeypatch.setattr(
        "loopx.cli_commands.turn.refresh_state_run",
        recording_refresh_state_run,
    )
    monkeypatch.setattr(
        "loopx.cli_commands.turn.spend_quota_slot",
        recording_spend_quota_slot,
    )
    monkeypatch.setattr(
        "loopx.cli_commands.turn_todo_writeback.update_goal_todo",
        recording_update_goal_todo,
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "turn",
                "run-once",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--host",
                "codex-cli",
                "--project",
                str(project),
                "--validation-command-json",
                json.dumps(
                    [
                        sys.executable,
                        "-c",
                        "import json, sys; json.load(sys.stdin)",
                    ]
                ),
                "--scan-root",
                str(project),
                "--no-global-sync",
                "--execute",
            ]
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload
    assert payload["host"] == {"executable": "built-in", "kind": "codex-cli"}
    assert payload["effects"]["state_written"] is True
    assert payload["effects"]["quota_spent"] is True
    assert refresh_workspace_paths == [project]
    assert spend_workspace_paths == [project]
    assert updated_todo_ids == (
        [] if result_kind == "validated_progress" else ["todo_fixture0001"]
    )
    state = (
        project
        / ".codex"
        / "goals"
        / "loopx-turn-fixture"
        / "ACTIVE_GOAL_STATE.md"
    ).read_text(encoding="utf-8")
    assert "Run one revised public fixture check" in state
    if result_kind != "validated_progress":
        assert f"LoopX%20Turn%20{result_kind}" in state


def test_turn_run_once_codex_cli_wires_validated_reflection_post_settlement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    reflection = json.dumps(
        {
            "schema_version": "turn_reward_memory_reflection_v0",
            "status": "eligible",
            "surface_id": "agent_workflow.turn_admission",
            "outcome_kind": "engineering",
            "content_summary": "Reuse the validated Codex CLI sequence.",
            "reasoning_summary": "The independent validator passed.",
            "confidence": "high",
            "evidence_refs": ["receipt:codex-cli-validator"],
        },
        separators=(",", ":"),
    )
    validator = project / "validate_codex_cli_reflection.py"
    validator.write_text(
        "import hashlib, json, sys\n"
        "result = json.load(sys.stdin)\n"
        "reflection = json.loads(result['reward_memory_reflection_json'])\n"
        "canonical = json.dumps(reflection, sort_keys=True, separators=(',', ':'))\n"
        "json.dump({\n"
        "  'schema_version': 'reward_memory_reflection_validation_v0',\n"
        "  'status': 'validated',\n"
        "  'reflection_digest': 'sha256:' + hashlib.sha256(canonical.encode()).hexdigest(),\n"
        "  'evidence_refs': reflection['evidence_refs'],\n"
        "}, sys.stdout)\n",
        encoding="utf-8",
    )

    def fake_codex_host(
        request: Mapping[str, Any],
        **_kwargs: object,
    ) -> dict[str, object]:
        return {
            "schema_version": "loopx_turn_result_v0",
            "turn_key": request["turn_key"],
            "result_kind": "validated_progress",
            "completed_phases": ["host_execute", "typed_result"],
            "classification": "codex_cli_reward_memory_progress",
            "recommended_action": "Continue the public fixture",
            "next_action": "Run the next bounded fixture Turn",
            "delivery_batch_scale": "single_surface",
            "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "The fixture objective remains open.",
            "summary": "One Codex CLI fixture step passed validation.",
            "reward_memory_reflection_json": reflection,
        }

    observed: list[dict[str, Any]] = []

    def fake_ingest(**kwargs: object) -> dict[str, object]:
        observed.append(dict(kwargs))
        return {
            "ok": True,
            "schema_version": "turn_reward_memory_ingest_v0",
            "status": "activated",
            "host_wiring": "codex_cli_turn_post_settlement",
            "provider_sync_count": 1,
            "exact_readback_verified": True,
        }

    monkeypatch.setattr(
        "loopx.cli_commands.turn.run_codex_cli_host",
        fake_codex_host,
    )
    monkeypatch.setattr(
        "loopx.cli_commands.turn.run_configured_turn_outcome_ingest_fail_open",
        fake_ingest,
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime),
                "--format",
                "json",
                "turn",
                "run-once",
                "--goal-id",
                "loopx-turn-fixture",
                "--agent-id",
                "codex-fixture",
                "--host",
                "codex-cli",
                "--project",
                str(project),
                "--validation-command-json",
                json.dumps([sys.executable, str(validator)]),
                "--scan-root",
                str(project),
                "--no-global-sync",
                "--execute",
            ]
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 0, payload
    assert payload["post_settlement"] == {
        "ok": True,
        "schema_version": "turn_reward_memory_ingest_v0",
        "status": "activated",
        "host_wiring": "codex_cli_turn_post_settlement",
        "provider_sync_count": 1,
        "exact_readback_verified": True,
    }
    assert len(observed) == 1
    assert observed[0]["host_result"]["reward_memory_reflection_json"] == reflection
    evidence = observed[0]["settlement_evidence"]
    assert evidence["task_validation"]["reward_memory_reflection_validation"][
        "status"
    ] == "validated"
    assert evidence["writeback"]["appended"] is True
    assert evidence["quota_spend"]["appended"] is True


def test_turn_run_once_cli_resumes_session_from_recoverable_failed_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, runtime, registry = _write_live_fixture(tmp_path)
    session_available = False
    session_actions: list[str] = []

    def fake_session_binding(
        _runtime_root: Path,
        _turn_envelope: dict[str, object],
    ) -> dict[str, str] | None:
        if not session_available:
            return None
        return {
            "schema_version": LOOPX_TURN_SESSION_BINDING_SCHEMA_VERSION,
            "goal_id": "loopx-turn-fixture",
            "agent_id": "codex-fixture",
            "todo_id": "todo_fixture0001",
        }

    def fake_codex_host(
        request: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        nonlocal session_available
        session = request["session"]
        assert isinstance(session, dict)
        session_actions.append(str(session["action"]))
        if len(session_actions) == 1:
            session_available = True
            raise BuiltInHostError(
                "codex_cli_timeout",
                recovery_kind="resume_session",
            )
        return {
            "schema_version": "loopx_turn_result_v0",
            "turn_key": request["turn_key"],
            "result_kind": "wait",
            "completed_phases": ["host_execute", "typed_result"],
        }

    monkeypatch.setattr(
        "loopx.cli_commands.turn.codex_cli_session_binding",
        fake_session_binding,
    )
    monkeypatch.setattr(
        "loopx.cli_commands.turn.run_codex_cli_host",
        fake_codex_host,
    )
    argv = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "turn",
        "run-once",
        "--goal-id",
        "loopx-turn-fixture",
        "--agent-id",
        "codex-fixture",
        "--host",
        "codex-cli",
        "--project",
        str(project),
        "--scan-root",
        str(project),
        "--no-global-sync",
        "--execute",
    ]

    failed_output = io.StringIO()
    with contextlib.redirect_stdout(failed_output):
        failed_exit_code = cli_main(argv)
    failed = json.loads(failed_output.getvalue())

    recovered_output = io.StringIO()
    with contextlib.redirect_stdout(recovered_output):
        recovered_exit_code = cli_main(
            [
                *argv[:-1],
                "--resume-turn-key",
                failed["resume_turn_key"],
                "--retry-failed-turn",
                "--execute",
            ]
        )
    recovered = json.loads(recovered_output.getvalue())

    assert failed_exit_code == 1
    assert failed["reason"] == "codex_cli_timeout"
    assert recovered_exit_code == 0, recovered
    assert recovered["status"] == "stopped"
    assert recovered["quota_slot_spend_count"] == 0
    assert session_actions == ["start_new", "resume"]
