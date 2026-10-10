from __future__ import annotations

import json
from copy import deepcopy

import pytest

from loopx.control_plane.quota.cli_projection import (
    QUOTA_CLI_GOAL_BOUNDARY_DETAIL_COMMAND,
    QUOTA_CLI_TODO_SUMMARY_DETAIL_COMMAND,
    QUOTA_CLI_USER_TODO_SUMMARY_DETAIL_COMMAND,
    QUOTA_CLI_VISION_DETAIL_COMMAND,
    compact_quota_should_run_cli_payload,
)
from loopx.presentation.renderers.quota_markdown import (
    render_quota_should_run_markdown,
)


@pytest.mark.parametrize("inline", ["absent", "identical", "different", "wrong-step"])
def test_replan_authoring_is_available_without_another_read(inline: str) -> None:
    authoring = {"schema_version": "goal_vision_replan_contract_v0",
                 "minimal_example": {"state": "vision_patch_proposed"},
                 "fields": {"vision_patch": {"acceptance_summary": 420}}}
    packet = {"obligation_id": "replan-fixture", "writeback_contract": {
        "vision_authoring": authoring, "preferred_input": "evidence_linked_vision_path",
        "rule": "Retain current requirements and cite observed evidence."}}
    steps = [{"kind": "validation"}]
    if inline != "absent":
        steps.append({"kind": "validation" if inline == "wrong-step" else "durable_writeback",
                      "vision_authoring": {**authoring, **({"additional_rule": "Keep this."}
                                                          if inline == "different" else {})}})
    payload = {"replan_action_packet": packet, "heartbeat_receipt": {"turn_instance_id": "turn-one"},
               "interaction_contract": {"cli_channel": {"settlement_plan": {"ordered_steps": steps}}}}
    original = deepcopy(payload)
    compact = compact_quota_should_run_cli_payload(payload)
    writeback = compact["replan_action_packet"]["writeback_contract"]
    assert "vision_authoring_detail_ref" not in writeback
    assert writeback["rule"] == packet["writeback_contract"]["rule"]
    if inline == "identical":
        assert "vision_authoring" not in writeback
        assert writeback["vision_authoring_ref"] == (
            "$.interaction_contract.cli_channel.settlement_plan.ordered_steps[1].vision_authoring")
        assert compact["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1][
            "vision_authoring"] == authoring
    else:
        assert writeback["vision_authoring"] == authoring
        assert "vision_authoring_ref" not in writeback
    assert compact["heartbeat_receipt"] == payload["heartbeat_receipt"]
    assert compact["interaction_contract"] == payload["interaction_contract"]
    assert payload == original
    assert compact_quota_should_run_cli_payload(payload, include_vision_detail=True) == original


def test_progress_only_replan_does_not_invent_a_vision_input() -> None:
    payload = {"replan_action_packet": {"obligation_id": "progress-fixture",
                                       "writeback_contract": {}}}
    assert compact_quota_should_run_cli_payload(payload) == payload


def _items(count: int, *, prefix: str) -> list[dict[str, object]]:
    return [
        {
            "schema_version": "todo_item_v0",
            "todo_id": f"{prefix}-{index}",
            "text": f"{prefix} item {index}",
            "title": f"{prefix} item {index}",
            "status": "open",
            "priority": "P1",
            "task_class": "advancement_task",
            "action_kind": f"{prefix}-action",
            "updated_at": "2026-01-01T00:00:00Z",
            "note": "cold-path diagnostic note",
        }
        for index in range(count)
    ]


def test_markdown_distinguishes_peer_blockers_from_native_admission() -> None:
    peer = {"mode": "task_scoped_peer", "execution_scope": "peer_agent_activation",
            "execution_state": "blocked", "task_selection": "canonical_claimed_candidates",
            "activation_allowed": False, "eligible_peer_lanes": [],
            "blocked_peer_lanes": [{"todo_id": "todo-peer", "reason_codes": ["peer_agent_activation_unavailable"]}]}
    for contract in [peer, {"mode": "adaptive", "execution_state": "ready", "peer_activation_diagnostic": peer}]:
        rendered = render_quota_should_run_markdown({"task_orchestration_contract": contract})
        assert "execution_scope=peer_agent_activation" in rendered
        assert "execution_state=blocked" in rendered
        assert "task_selection=canonical_claimed_candidates" in rendered
        assert "peer_agent_activation_unavailable" in rendered
        if contract["mode"] == "adaptive":
            assert "peer_activation_diagnostic_admission:" in rendered
            assert "execution_state=ready" in rendered
    projected = {**peer, "blocked_peer_lanes": [], "blocked_peer_count": 50,
                 "read_required": True, "detail_ref": "full_decision.task_orchestration_contract"}
    rendered = render_quota_should_run_markdown({"task_orchestration_contract": projected})
    assert "blocked_lanes=50" in rendered
    assert "task_orchestration_required_detail: full_decision.task_orchestration_contract" in rendered


def test_compact_quota_should_run_cli_payload_keeps_decision_lanes_and_counts() -> None:
    backlog = _items(40, prefix="backlog")
    first_open = _items(5, prefix="open")
    first_executable = _items(4, prefix="execute")
    unclaimed = _items(5, prefix="unclaimed")
    monitor_due = _items(2, prefix="monitor")
    monitor_capability_blocked = _items(3, prefix="monitor-capability")
    monitor_schedule_gap = _items(2, prefix="monitor-gap")
    current_agent_blockers = [
        {
            **item,
            "status": "blocked",
            "task_class": "blocker",
            "reason": f"blocker reason {index}",
        }
        for index, item in enumerate(_items(3, prefix="blocker"))
    ]
    payload = {
        "interaction_contract": {"mode": "bounded_delivery"},
        "selected_todo": first_executable[0],
        "scheduler_hint": {"action": "run_now"},
        "agent_todo_summary": {
            "schema_version": "todo_summary_v0",
            "total_count": 50,
            "open_count": 40,
            "done_count": 10,
            "first_open_items": first_open,
            "first_executable_items": first_executable,
            "unclaimed_priority_open_items": unclaimed,
            "monitor_due_items": monitor_due,
            "monitor_capability_blocked_due_items": monitor_capability_blocked,
            "monitor_schedule_gap_count": 2,
            "monitor_schedule_gap_items": monitor_schedule_gap,
            "current_agent_blocker_count": 3,
            "current_agent_blocker_items": current_agent_blockers,
            "backlog_items": backlog,
            "claimed_open_items": backlog,
            "claim_scope": {
                "schema_version": "agent_claim_scope_v0",
                "current_agent_claimed_open_count": 4,
                "other_agent_claimed_items": backlog,
            },
            "todo_succession_warning": {
                "count": 3,
                "items": backlog,
            },
        },
    }

    compact = compact_quota_should_run_cli_payload(payload)

    assert list(compact).index("interaction_contract") <= len(
        ("ok", "status_health_ok", "mode", "goal_id", "decision", "should_run")
    )

    assert compact["interaction_contract"] == payload["interaction_contract"]
    assert compact["selected_todo"] == payload["selected_todo"]
    assert compact["scheduler_hint"] == payload["scheduler_hint"]
    summary = compact["agent_todo_summary"]
    assert summary["total_count"] == 50
    assert summary["open_count"] == 40
    assert "first_open_items" not in summary
    assert len(summary["first_executable_items"]) == 3
    assert summary["first_executable_items"][0] == {
        "schema_version": "todo_item_v0",
        "todo_id": "execute-0",
        "text": "execute item 0",
        "status": "open",
        "priority": "P1",
        "task_class": "advancement_task",
        "action_kind": "execute-action",
    }
    assert [item["todo_id"] for item in summary["unclaimed_priority_open_items"]] == [
        "unclaimed-0",
        "unclaimed-1",
        "unclaimed-2",
    ]
    assert len(summary["monitor_due_items"]) == 1
    assert len(summary["monitor_capability_blocked_due_items"]) == 2
    assert [item["todo_id"] for item in summary["monitor_schedule_gap_items"]] == [
        "monitor-gap-0",
    ]
    assert summary["current_agent_blocker_count"] == 3
    assert summary["current_agent_blocker_items"] == [
        {
            "schema_version": "todo_item_v0",
            "todo_id": "blocker-0",
            "text": "blocker item 0",
            "status": "blocked",
            "priority": "P1",
            "task_class": "blocker",
            "action_kind": "blocker-action",
            "reason": "blocker reason 0",
        },
        {
            "schema_version": "todo_item_v0",
            "todo_id": "blocker-1",
            "text": "blocker item 1",
            "status": "blocked",
            "priority": "P1",
            "task_class": "blocker",
            "action_kind": "blocker-action",
            "reason": "blocker reason 1",
        },
    ]
    assert "backlog_items" not in summary
    assert "claimed_open_items" not in summary
    assert "other_agent_claimed_items" not in summary["claim_scope"]
    assert "items" not in summary["todo_succession_warning"]
    assert summary["todo_succession_warning"]["todo_ids"] == [
        "backlog-0",
        "backlog-1",
        "backlog-2",
    ]
    assert summary["payload_compaction"]["omitted_lanes"] == {
        "backlog_items": 40,
        "claim_scope.other_agent_claimed_items": 40,
        "claimed_open_items": 40,
        "current_agent_blocker_items": 1,
        "first_executable_items": 1,
        "first_open_items": 5,
        "monitor_due_items": 1,
        "monitor_capability_blocked_due_items": 1,
        "monitor_schedule_gap_items": 1,
        "todo_succession_warning.items": 40,
        "unclaimed_priority_open_items": 2,
    }
    assert summary["payload_compaction"]["full_detail_cold_path"] == (
        QUOTA_CLI_TODO_SUMMARY_DETAIL_COMMAND
    )
    assert len(payload["agent_todo_summary"]["backlog_items"]) == 40

    larger_backlog = _items(400, prefix="backlog")
    larger_payload = {
        **payload,
        "agent_todo_summary": {
            **payload["agent_todo_summary"],
            "backlog_items": larger_backlog,
            "claimed_open_items": larger_backlog,
            "claim_scope": {
                **payload["agent_todo_summary"]["claim_scope"],
                "other_agent_claimed_items": larger_backlog,
            },
            "todo_succession_warning": {
                "count": 300,
                "items": larger_backlog,
            },
        },
    }
    larger_compact = compact_quota_should_run_cli_payload(larger_payload)
    compact_chars = len(json.dumps(compact, sort_keys=True))
    larger_compact_chars = len(json.dumps(larger_compact, sort_keys=True))
    assert compact_chars < 7_000
    assert larger_compact_chars - compact_chars < 200


def test_compact_payload_promotes_committed_receipt_before_diagnostics() -> None:
    receipt = {
        "status": "replayed",
        "settlement_identity": {
            "schema_version": "quota_settlement_identity_v0",
            "effect_id": "goal:agent:todo:turn",
            "goal_id": "goal",
            "agent_id": "agent",
            "todo_id": "todo",
            "turn_instance_id": "turn",
        },
    }
    rollout_event = {
        "schema_version": "quota_rollout_event_v0",
        "status": "appended",
    }
    runtime_capability_reentry = {
        "schema_version": "runtime_capability_reentry_v0",
        "candidates": [],
    }
    payload = {
        "ok": True,
        "status_health_ok": True,
        "mode": "managed",
        "goal_id": "goal",
        "decision": "run",
        "should_run": True,
        "diagnostic_blob": "x" * 32_000,
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "cli_channel": {
                "runtime_capability_reentry": runtime_capability_reentry,
            },
        },
        "heartbeat_receipt": receipt,
        "rollout_event": rollout_event,
    }

    compact = compact_quota_should_run_cli_payload(payload)

    assert list(compact)[:10] == [
        "ok",
        "status_health_ok",
        "mode",
        "goal_id",
        "decision",
        "should_run",
        "heartbeat_receipt",
        "runtime_capability_reentry",
        "interaction_contract",
        "diagnostic_blob",
    ]
    assert compact["heartbeat_receipt"] == receipt
    assert compact["rollout_event"] == rollout_event
    assert compact["runtime_capability_reentry"] == runtime_capability_reentry


def test_compact_quota_should_run_cli_payload_keeps_active_user_work_and_gate_scope() -> None:
    active_user_actions = [
        {
            **item,
            "task_class": "user_action",
            "bound_agent": "quality-agent",
        }
        for item in _items(4, prefix="user-action")
    ]
    active_gates = [
        {
            **item,
            "task_class": "user_gate",
            "blocks_agent": "quality-agent",
            "decision_scope": "release:action:quota-output",
        }
        for item in _items(2, prefix="user-gate")
    ]
    other_agent_actions = [
        {
            **item,
            "task_class": "user_action",
            "bound_agent": "other-agent",
        }
        for item in _items(20, prefix="other-user-action")
    ]
    payload = {
        "interaction_contract": {
            "user_channel": {
                "action_required": True,
                "items": active_user_actions[:1],
            }
        },
        "selected_todo": {"todo_id": "quality-0"},
        "scheduler_hint": {"action": "wait"},
        "user_todo_summary": {
            "schema_version": "todo_summary_v0",
            "total_count": 30,
            "open_count": 6,
            "all_open_count": 26,
            "first_open_items": active_user_actions,
            "gate_open_items": active_gates,
            "active_next_action_items": active_user_actions[:2],
            "deferred_items": other_agent_actions,
            "other_agent_bound_user_action_items": other_agent_actions,
            "claim_scope": {
                "schema_version": "agent_claim_scope_v0",
                "blocked_claimed_open_count": 2,
                "blocked_claimed_items": other_agent_actions,
            },
        },
    }

    compact = compact_quota_should_run_cli_payload(
        payload,
        include_todo_summary_detail=True,
    )

    summary = compact["user_todo_summary"]
    assert summary["total_count"] == 30
    assert summary["open_count"] == 6
    assert len(summary["first_open_items"]) == 3
    assert summary["gate_open_items"] == active_gates
    assert summary["active_next_action_items"] == active_user_actions[:2]
    assert summary["gate_open_items"][0]["blocks_agent"] == "quality-agent"
    assert summary["gate_open_items"][0]["decision_scope"] == (
        "release:action:quota-output"
    )
    assert "deferred_items" not in summary
    assert "other_agent_bound_user_action_items" not in summary
    assert "blocked_claimed_items" not in summary["claim_scope"]
    assert summary["payload_compaction"]["omitted_lanes"] == {
        "claim_scope.blocked_claimed_items": 20,
        "deferred_items": 20,
        "first_open_items": 1,
        "other_agent_bound_user_action_items": 20,
    }
    assert summary["payload_compaction"]["full_detail_cold_path"] == (
        QUOTA_CLI_USER_TODO_SUMMARY_DETAIL_COMMAND
    )
    assert compact["todo_summary_projection"]["compacted_roles"] == ["user"]
    assert compact["interaction_contract"] == payload["interaction_contract"]
    assert compact["selected_todo"] == payload["selected_todo"]

    full = compact_quota_should_run_cli_payload(
        payload,
        include_todo_summary_detail=True,
        include_user_todo_summary_detail=True,
    )
    assert full == payload


def test_compact_quota_should_run_cli_payload_bounds_boundary_authority_entries() -> None:
    payload = {
        "goal_boundary": {
            "checkpointed_boundary_authority": {
                "schema_version": "checkpointed_boundary_authority_v0",
                "active_count": 1,
                "inactive_count": 0,
                "active_write_scope": ["docs/**"],
                "entries": [
                    {
                        "status": "active",
                        "decision": "approve",
                        "write_scope": ["docs/**"],
                        "source": "public_fixture_owner_approval",
                    }
                ],
            },
            "write_scope": ["docs/**"],
            "requires_parent_approval": ["publish"],
            "guards": ["keep private state out of public changes"],
            "stop_condition": "stop when owner approval is required",
        },
        "interaction_contract": {"mode": "bounded_delivery"},
        "scheduler_hint": {"action": "run_now"},
        "selected_todo": {"todo_id": "quality-0"},
    }

    compact = compact_quota_should_run_cli_payload(
        payload,
        include_todo_summary_detail=True,
        include_user_todo_summary_detail=True,
    )

    boundary = compact["goal_boundary"]
    authority = boundary["checkpointed_boundary_authority"]
    assert "entries" not in authority
    assert authority["active_count"] == 1
    assert authority["inactive_count"] == 0
    assert authority["active_write_scope"] == ["docs/**"]
    assert authority["payload_compaction"] == {
        "schema_version": "quota_cli_goal_boundary_compaction_v0",
        "omitted_entry_count": 1,
        "full_detail_cold_path": QUOTA_CLI_GOAL_BOUNDARY_DETAIL_COMMAND,
    }
    for key in (
        "write_scope",
        "requires_parent_approval",
        "guards",
        "stop_condition",
    ):
        assert boundary[key] == payload["goal_boundary"][key]
    for key in ("interaction_contract", "scheduler_hint", "selected_todo"):
        assert compact[key] == payload[key]

    full = compact_quota_should_run_cli_payload(
        payload,
        include_todo_summary_detail=True,
        include_user_todo_summary_detail=True,
        include_goal_boundary_detail=True,
    )
    assert full == payload


def test_compact_quota_should_run_cli_payload_bounds_repeated_vision_detail() -> None:
    judge = {
        "schema_version": "vision_gap_judge_v0",
        "done": False,
        "decision": "continue",
        "reason": "The public qualification acceptance gap remains open.",
        "agent_judge_instruction": "Judge closure from authoritative evidence. " * 12,
        "evidence_read_instruction": "Read the agent-scoped evidence log.",
        "registry_read_instruction": "Inspect permitted registered materials. " * 8,
        "external_research_instruction": "Use bounded public research. " * 8,
        "done_only_when": ["authoritative_evidence_satisfies_acceptance"],
        "continue_when": ["evidence_is_missing_weak_or_stale"],
        "otherwise": "continue",
    }
    audit = {
        "schema_version": "vision_continuation_audit_v0",
        "required": True,
        "agent_id": "quality-agent",
        "decision": "acceptance_gap_open",
        "selected_todo_is_goal_completion": False,
        "closeout_allowed_without_evidence": False,
        "trigger_count": 1,
        "trigger_kinds": ["vision_acceptance_gap"],
        "acceptance_gaps": [{"kind": "vision_acceptance_gap", "detail": "x" * 900}],
        "vision_gap_judge": judge,
        "authoritative_evidence_kinds": ["changed_files", "evaluation_outputs"],
        "not_satisfied_by": ["todo_completion_alone"],
        "required_before_closeout": [
            "derive_requirements_from_active_vision_and_current_todo",
            "name_authoritative_evidence_for_each_requirement",
        ],
        "recommended_action": "Keep the vision active until evidence closes it.",
        "acceptance_requirements": ["Public qualification remains attributable."],
    }
    selected = _items(1, prefix="selected")[0]
    payload = {
        "ok": True,
        "goal_id": "mature-goal-fixture",
        "recommended_action": selected["text"],
        "selected_todo": selected,
        "agent_lane_next_action": {**selected, "text": f"[P1] {selected['title']}"},
        "capability_gate": {
            "schema_version": "capability_gate_v0",
            "required": ["shell", "filesystem_write"],
            "available": ["shell", "filesystem_read", "filesystem_write"],
            "missing": [],
            "action": "run",
            "decision_owner": "agent",
            "candidate_order_policy": "claim_then_profile_then_priority",
            "runnable_count": 12,
            "runnable_candidates": _items(12, prefix="candidate"),
            "blocked_candidates": _items(8, prefix="blocked"),
            "resolution_bindings": _items(8, prefix="binding"),
        },
        "vision_continuation_audit": audit,
        "goal_frontier_projection": {
            "replan_required": True,
            "acceptance_gaps": audit["acceptance_gaps"],
            "vision_continuation_audit": audit,
        },
        "interaction_contract": {
            "mode": "bounded_delivery",
            "agent_channel": {"must_attempt": True, "vision_continuation_audit": audit},
            "cli_channel": {"vision_continuation_audit": audit},
        },
        "next_action_projection_warning": {
            "schema_version": "next_action_projection_warning_v0",
            "kind": "next_action_projection_mismatch",
            "severity": "info",
            "requires_state_writeback": False,
            "active_state_next_action": "Preserve the durable route.",
            "latest_run_recommended_action": "Continue the current quality lane.",
            "agent_lane_next_action": selected["text"],
            "reason": "The agent lane differs from the durable route.",
            "recommended_action": "Run the agent lane without mutating the durable route.",
        },
        "active_state_next_action": "Preserve the durable route.",
        "latest_run_recommended_action": "Continue the current quality lane.",
        "goal_route_hint": {
            "route_decision": "run_current_agent_lane",
            "counts": {"other_agent_claimed_advancement_count": 8},
            "other_agent_next_actions": _items(8, prefix="peer"),
        },
        "completed_todo_archive_warning": {
            "kind": "completed_agent_todo_archive_required",
            "requires_archive": True,
            "archive_section": "Completed Work Archive",
            "active_done_count": 778,
            "active_open_count": 58,
            "max_active_done_count": 12,
            "default_archive_keep_count": 10,
            "archive_command_template": (
                "loopx todo archive-completed --goal-id <goal-id> "
                "--max-active-done 10 --execute"
            ),
            "recommended_action": "Archive old completed todo entries.",
        },
        "stable_control_plane_context": "s" * 24_000,
    }

    compact = compact_quota_should_run_cli_payload(payload)
    detailed = compact_quota_should_run_cli_payload(
        payload,
        include_todo_summary_detail=True,
        include_vision_detail=True,
    )

    assert len(json.dumps(payload, indent=2)) > 40_000
    assert len(json.dumps(compact, indent=2)) <= 40_000
    assert compact["selected_todo"] == payload["selected_todo"]
    assert compact["interaction_contract"]["mode"] == "bounded_delivery"
    assert compact["vision_continuation_audit"]["vision_gap_judge"]["decision"] == (
        "continue"
    )
    assert compact["vision_continuation_audit"]["payload_compaction"][
        "full_detail_cold_path"
    ] == QUOTA_CLI_VISION_DETAIL_COMMAND
    assert compact["goal_frontier_projection"]["vision_continuation_audit"][
        "projection_ref"
    ] == "$.vision_continuation_audit"
    assert compact["interaction_contract"]["agent_channel"][
        "vision_continuation_audit"
    ]["vision_gap_judge"]["decision"] == "continue"
    assert "runnable_candidates" not in compact["capability_gate"]
    assert compact["next_action_projection_warning"]["projection_refs"][
        "active_state_next_action"
    ] == "$.active_state_next_action"
    assert "title" not in compact["agent_lane_next_action"]
    assert "other_agent_next_actions" not in compact["goal_route_hint"]
    assert compact["goal_route_hint"]["other_agent_next_action_count"] == 8
    assert compact["completed_todo_archive_warning"] == (
        payload["completed_todo_archive_warning"]
    )
    assert detailed["vision_continuation_audit"] == audit
    assert detailed["capability_gate"] == payload["capability_gate"]
    assert detailed["goal_route_hint"] == payload["goal_route_hint"]
    assert detailed["completed_todo_archive_warning"] == (
        payload["completed_todo_archive_warning"]
    )


def test_compaction_preserves_nonidentical_diagnostics_instead_of_referencing_them() -> (
    None
):
    canonical_audit = {
        "schema_version": "vision_continuation_audit_v0",
        "required": True,
        "decision": "acceptance_gap_open",
    }
    channel_audit = {
        **canonical_audit,
        "decision": "channel_specific_diagnostic",
    }
    payload = {
        "vision_continuation_audit": canonical_audit,
        "goal_frontier_projection": {
            "vision_continuation_audit": channel_audit,
        },
        "interaction_contract": {
            "agent_channel": {"vision_continuation_audit": channel_audit},
        },
        "active_state_next_action": "Current durable action.",
        "next_action_projection_warning": {
            "active_state_next_action": "Older diagnostic action.",
        },
    }

    compact = compact_quota_should_run_cli_payload(payload)

    assert compact["goal_frontier_projection"]["vision_continuation_audit"] == (
        channel_audit
    )
    assert compact["interaction_contract"]["agent_channel"][
        "vision_continuation_audit"
    ] == channel_audit
    assert compact["next_action_projection_warning"][
        "active_state_next_action"
    ] == "Older diagnostic action."
    assert "projection_refs" not in compact["next_action_projection_warning"]


def test_compaction_hides_durable_actions_shadowed_by_selected_todo() -> None:
    selected = {
        "schema_version": "quota_selected_todo_v0",
        "todo_id": "todo_current",
        "text": "Continue the selected Todo.",
        "continuation_hint": "Next run the restart canary.",
    }
    payload = {
        "recommended_action": selected["text"],
        "selected_todo": selected,
        "active_state_next_action": "Inspect an obsolete branch.",
        "latest_run_recommended_action": "Continue the previous run.",
        "goal_route_hint": {
            "selected_action_differs_from_durable": True,
            "current_agent_next_action": {"todo_id": selected["todo_id"]},
        },
        "next_action_projection_warning": {
            "active_state_next_action": "Inspect an obsolete branch.",
            "latest_run_recommended_action": "Continue the previous run.",
            "agent_lane_next_action": selected["text"],
            "reason": "The selected Todo supersedes durable action prose.",
        },
    }

    compact = compact_quota_should_run_cli_payload(payload)
    detailed = compact_quota_should_run_cli_payload(
        payload,
        include_todo_summary_detail=True,
    )

    assert compact["selected_todo"] == selected
    assert "active_state_next_action" not in compact
    assert "latest_run_recommended_action" not in compact
    assert compact["next_action_projection_warning"]["shadowed_by"] == (
        "$.selected_todo"
    )
    assert detailed["active_state_next_action"] == "Inspect an obsolete branch."
    assert detailed["latest_run_recommended_action"] == (
        "Continue the previous run."
    )


def test_compact_quota_should_run_cli_payload_keeps_succession_warning_identity_in_markdown() -> None:
    payload = {
        "ok": True,
        "goal_id": "succession-warning-fixture",
        "agent_todo_summary": {
            "total_count": 5,
            "open_count": 1,
            "completed_without_successor_count": 4,
            "first_open_items": _items(1, prefix="open"),
            "todo_succession_warning": {
                "reason_code": "completed_advancement_without_successor",
                "count": 4,
                "items": _items(4, prefix="repair"),
            },
        },
    }

    compact = compact_quota_should_run_cli_payload(payload)
    warning = compact["agent_todo_summary"]["todo_succession_warning"]

    assert warning["todo_ids"] == ["repair-0", "repair-1", "repair-2"]
    assert "items" not in warning
    markdown = render_quota_should_run_markdown(compact)
    assert (
        "- agent_todo_succession_warning: "
        "reason=completed_advancement_without_successor "
        "count=4 todo_ids=repair-0,repair-1,repair-2"
    ) in markdown
    assert "todo_ids=n/a" not in markdown


def test_compact_quota_should_run_cli_payload_keeps_hot_path_identity_in_markdown() -> None:
    executable = _items(4, prefix="executable")
    payload = {
        "ok": True,
        "goal_id": "routing-identity-fixture",
        "selected_todo": executable[0],
        "agent_lane_next_action": {
            "todo_id": "executable-0",
            "selected_by": "current_agent_claimed_todo",
            "confidence": "selected",
        },
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "bounded_delivery",
            "user_channel": {"action_required": False},
            "agent_channel": {"must_attempt": True},
        },
        "agent_todo_summary": {
            "total_count": 8,
            "open_count": 8,
            "claimed_open_count": 3,
            "unclaimed_open_count": 5,
            "monitor_due_count": 2,
            "monitor_capability_blocked_due_count": 3,
            "monitor_schedule_gap_count": 2,
            "first_open_items": _items(3, prefix="claimed"),
            "first_executable_items": executable,
            "unclaimed_priority_open_items": _items(5, prefix="unclaimed"),
            "monitor_due_items": _items(2, prefix="monitor-due"),
            "monitor_capability_blocked_due_items": _items(
                3,
                prefix="monitor-capability",
            ),
            "monitor_schedule_gap_items": _items(2, prefix="monitor-gap"),
        },
    }

    compact = compact_quota_should_run_cli_payload(payload)
    summary = compact["agent_todo_summary"]

    assert [item["todo_id"] for item in summary["unclaimed_priority_open_items"]] == [
        "unclaimed-0",
        "unclaimed-1",
        "unclaimed-2",
    ]
    assert [item["todo_id"] for item in summary["monitor_schedule_gap_items"]] == [
        "monitor-gap-0",
    ]
    markdown = render_quota_should_run_markdown(compact)
    assert (
        "- agent_lane_next_action: todo_id=executable-0 "
        "selected_by=current_agent_claimed_todo confidence=selected"
    ) in markdown
    assert "- agent_todo_next: executable item 0 todo_id=executable-0" in markdown
    assert "- agent_todo_next: executable item 1 todo_id=executable-1" in markdown
    assert "- agent_todo_next: executable item 2 todo_id=executable-2" in markdown
    assert (
        "- agent_todo_unclaimed_candidates: "
        "todo_id=unclaimed-0 status=open priority=P1 "
        "task_class=advancement_task action_kind=unclaimed-action; "
        "todo_id=unclaimed-1 status=open priority=P1 "
        "task_class=advancement_task action_kind=unclaimed-action; "
        "todo_id=unclaimed-2 status=open priority=P1 "
        "task_class=advancement_task action_kind=unclaimed-action"
    ) in markdown
    assert (
        "- agent_todo_monitor_due: "
        "todo_id=monitor-due-0 status=open priority=P1 "
        "task_class=advancement_task action_kind=monitor-due-action"
    ) in markdown
    assert (
        "- agent_todo_monitor_capability_blocked_due: "
        "todo_id=monitor-capability-0 status=open priority=P1 "
        "task_class=advancement_task action_kind=monitor-capability-action; "
        "todo_id=monitor-capability-1 status=open priority=P1 "
        "task_class=advancement_task action_kind=monitor-capability-action"
    ) in markdown
    assert (
        "- agent_todo_monitor_schedule_gap: "
        "todo_id=monitor-gap-0 status=open priority=P1 "
        "task_class=advancement_task action_kind=monitor-gap-action"
    ) in markdown
    assert "- interaction_contract: " in markdown
