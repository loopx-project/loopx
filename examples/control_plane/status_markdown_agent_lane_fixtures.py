"""Agent-lane projection fixtures for the status markdown smoke."""

from __future__ import annotations

from loopx.cli_commands.status import (
    _status_collection_limit_for_agent_lane,
    _trim_run_history_for_status_display,
    attach_agent_lane_next_actions,
)
from loopx.control_plane.todos.quota_summary import (
    compact_agent_lane_todos_for_status_display,
)
from loopx.review_packet import build_review_packet
from loopx.presentation.renderers.status_markdown import render_status_markdown
from loopx.status import project_asset_todo_summary


def assert_status_agent_lane_next_action_projection() -> None:
    goal_id = "agent-lane-status-fixture"
    primary_action = "[P0] Continue the primary controller benchmark route."
    stale_run_action = "[P0] Monitor the primary controller route before changing Next Action."
    side_action = (
        "[P0] Codex CLI TUI continuation: prove the visible steering turn "
        "without losing user takeover."
    )
    side_todo = {
        "schema_version": "todo_item_v0",
        "todo_id": "todo_side_tui",
        "index": 2,
        "role": "agent",
        "status": "open",
        "priority": "P0",
        "task_class": "advancement_task",
        "action_kind": "codex_cli_tui_continuation",
        "claimed_by": "codex-side-bypass",
        "required_capabilities": ["shell", "filesystem_write"],
        "text": side_action,
    }
    primary_todo = {
        "schema_version": "todo_item_v0",
        "todo_id": "todo_primary_route",
        "index": 1,
        "role": "agent",
        "status": "open",
        "priority": "P0",
        "task_class": "advancement_task",
        "claimed_by": "codex-main-control",
        "text": primary_action,
    }
    agent_todos = {
        "schema_version": "todo_summary_v0",
        "open_count": 2,
        "done_count": 0,
        "total_count": 2,
        "first_open_items": [
            primary_todo,
            side_todo,
        ],
        "items": [primary_todo, side_todo],
        "first_executable_items": [side_todo],
    }
    coordination = {
        "agent_model": "peer_v1",
        "registered_agents": ["codex-main-control", "codex-side-bypass"],
        "agent_profiles": {
            "codex-side-bypass": {
                "schema_version": "agent_profile_v1",
                "agent_id": "codex-side-bypass",
                "profile_role": "product-validation",
                "scope_summary": "productization showcase docs lane",
            }
        },
    }
    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "goal_count": 1,
        "run_count": 1,
        "contract": {"ok": True, "summary": {"errors": 0, "warnings": 0, "checks": 0}},
        "global_registry": {"available": False, "summary": {}},
        "attention_queue": {
            "available": True,
            "item_count": 1,
            "items": [
                {
                    "goal_id": goal_id,
                    "status": "primary_route_active",
                    "waiting_on": "codex",
                    "severity": "action",
                    "recommended_action": primary_action,
                    "active_state_next_action": primary_action,
                    "latest_run_recommended_action": stale_run_action,
                    "next_action_projection_warning": {
                        "schema_version": "next_action_projection_warning_v0",
                        "kind": "next_action_projection_mismatch",
                        "severity": "warning",
                        "requires_state_writeback": True,
                        "active_state_next_action": primary_action,
                        "latest_run_recommended_action": stale_run_action,
                    },
                    "source": "registry",
                    "coordination": coordination,
                    "quota": {
                        "state": "eligible",
                        "compute": 1.0,
                        "window_hours": 24,
                        "slot_minutes": 1,
                        "allowed_slots": 10,
                    },
                    "agent_todos": agent_todos,
                    "project_asset": {
                        "owner": "codex-main-control",
                        "gate": "none",
                        "stop_condition": "stop on unsafe workspace or user gate",
                        "next_action": primary_action,
                        "active_state_next_action": primary_action,
                        "latest_run_recommended_action": stale_run_action,
                        "next_action_projection_warning": {
                            "schema_version": "next_action_projection_warning_v0",
                            "kind": "next_action_projection_mismatch",
                            "severity": "warning",
                            "requires_state_writeback": True,
                            "active_state_next_action": primary_action,
                            "latest_run_recommended_action": stale_run_action,
                        },
                        "agent_todos": project_asset_todo_summary(agent_todos, role="agent"),
                    },
                }
            ],
        },
        "run_history": {
            "goals": [
                {
                    "id": goal_id,
                    "registry_member": True,
                    "status": "primary_route_active",
                    "coordination": coordination,
                    "quota": {
                        "compute": 1.0,
                        "window_hours": 24,
                        "slot_minutes": 1,
                        "allowed_slots": 10,
                    },
                }
            ]
        },
    }
    attach_agent_lane_next_actions(payload, agent_id="codex-side-bypass")
    item = payload["attention_queue"]["items"][0]
    next_action = item["agent_lane_next_action"]
    assert next_action["schema_version"] == "agent_lane_next_action_v0", next_action
    assert next_action["todo_id"] == "todo_side_tui", next_action
    assert next_action["agent_id"] == "codex-side-bypass", next_action
    assert next_action["preserves_goal_next_action"] is True, next_action
    assert "latest_run_recommended_action" not in item, item
    assert "next_action_projection_warning" not in item, item
    assert "latest_run_recommended_action" not in item["project_asset"], item
    assert "next_action_projection_warning" not in item["project_asset"], item
    goal_frontier = item["goal_frontier_projection"]
    assert goal_frontier["deferred_successors"]["ready_count"] == 0, goal_frontier
    assert goal_frontier["acceptance_gaps"] == [], goal_frontier
    assert item["recommended_action"] == primary_action, item
    assert item["project_asset"]["next_action"] == primary_action, item
    member = item["agent_member"]
    assert member["schema_version"] == "agent_member_v1", member
    assert member["agent_id"] == "codex-side-bypass", member
    assert member["agent_model"] == "peer_v1", member
    assert member["profile_role"] == "product-validation", member
    assert member["profile_role_is_advisory"] is True, member
    assert member["scope_summary"] == "productization showcase docs lane", member
    assert "worktree_policy" not in member, member
    assert "requires_independent_worktree" not in member, member
    assert member["current_claims"] == ["todo_side_tui"], member
    lease_projection = member["lease_projection"]
    assert lease_projection == {
        "schema_version": "agent_lease_projection_v0",
        "source": "todo.claimed_by",
        "soft_claim_default": True,
        "hard_lease_available": True,
        "hard_lease_mode": "explicit_cli_opt_in",
        "hard_lease_enforced_by_quota": False,
    }, member
    assert member["handoff_assignment_status"] == "task_policy_selected", member
    assert "handoff_agent" not in member, member
    assert item["project_asset"]["agent_member"] == member, item
    interaction = item["agent_interaction_summary"]
    assert interaction["schema_version"] == "agent_interaction_summary_v0", interaction
    assert interaction["agent_id"] == "codex-side-bypass", interaction
    assert interaction["user_action_required"] is False, interaction
    assert interaction["user_open_count"] == 0, interaction
    assert interaction["agent_must_attempt"] is True, interaction
    assert interaction["delivery_allowed"] is True, interaction
    assert interaction["quiet_noop_allowed"] is False, interaction
    assert item["project_asset"]["agent_interaction_summary"] == interaction, item
    projection = payload["agent_member_projection"]
    assert projection["schema_version"] == "agent_member_projection_v0", projection
    assert projection["attached_count"] == 1, projection
    assert projection["projection_is_authoritative"] is False, projection
    lane_projection = payload["agent_lane_next_action_projection"]
    assert lane_projection["agent_interaction_attached_count"] == 1, lane_projection
    assert lane_projection["current_agent_todo_id"] == "todo_side_tui", lane_projection
    assert lane_projection["current_agent_action"] == side_action, lane_projection
    assert lane_projection["selected_by"] == "current_agent_claimed_todo", lane_projection
    assert lane_projection["confidence"] == "selected", lane_projection
    markdown = render_status_markdown(payload)
    assert "agent_member: agent=codex-side-bypass agent_model=peer_v1" in markdown, markdown
    assert "profile_role=product-validation" in markdown, markdown
    assert "worktree_policy=" not in markdown, markdown
    assert "claims=todo_side_tui" in markdown, markdown
    assert "current_agent_interaction: agent=codex-side-bypass mode=bounded_delivery" in markdown, markdown
    assert "action_required=False open_count=0" in markdown, markdown
    assert "must_attempt=True delivery_allowed=True quiet_noop_allowed=False" in markdown, markdown
    assert "current_agent_todo: agent=codex-side-bypass todo_id=todo_side_tui" in markdown, markdown
    assert "source=agent_lane_next_action" in markdown, markdown
    assert "next_action_projection_warning:" not in markdown, markdown
    assert "goal_frontier_projection: replan_required=False" in markdown, markdown
    assert "deferred_ready=0 acceptance_gaps=0" in markdown, markdown
    assert side_action in markdown, markdown
    assert f"next_agent_todo: {primary_action} claimed_by=codex-main-control scope=goal_all_agents" in markdown, markdown
    assert f"asset_agent_todo: {primary_action} claimed_by=codex-main-control scope=goal_all_agents" in markdown, markdown
    # The handoff reducer consumes an explicit history array even when empty.
    for goal in payload["run_history"]["goals"]:
        goal.setdefault("latest_runs", [])
    packet = build_review_packet(payload, goal_id=goal_id, action_kind="codex")
    assert packet["ok"] is True, packet
    assert packet["agent_member"]["agent_id"] == "codex-side-bypass", packet
    assert "Agent 成员：agent=codex-side-bypass agent_model=peer_v1" in packet["project_agent_handoff"], packet
    assert "profile_role=product-validation" in packet["project_agent_handoff"], packet
    assert "authority=advisory_projection" in packet["project_agent_handoff"], packet


def assert_status_agent_lane_todo_summary_display_compaction() -> None:
    long_todos = [
        {
            "schema_version": "todo_item_v0",
            "todo_id": f"todo_compact_{index}",
            "index": index,
            "role": "agent",
            "status": "open",
            "task_class": "continuous_monitor" if index % 2 else "advancement_task",
            "claimed_by": "codex-product-capability",
            "text": f"[P2] Compact status todo summary item {index} " + ("detail " * 40),
        }
        for index in range(1, 8)
    ]
    agent_todos = {
        "schema_version": "todo_summary_v0",
        "source_section": "Agent Todo",
        "open_count": 7,
        "done_count": 3,
        "total_count": 10,
        "items": long_todos,
        "first_open_items": long_todos,
        "monitor_open_items": long_todos,
        "claimed_open_items": long_todos,
        "claimed_monitor_open_items": long_todos,
        "claimed_open_count": 7,
        "unclaimed_open_count": 0,
    }
    user_todos = {
        "schema_version": "todo_summary_v0",
        "source_section": "User Todo / Owner Review Reading Queue",
        "open_count": 1,
        "done_count": 1,
        "total_count": 2,
        "items": long_todos[:3],
        "first_open_items": long_todos[:1],
        "handoff_gates": long_todos,
    }
    project_asset_agent_summary = project_asset_todo_summary(agent_todos, role="agent")
    payload = {
        "attention_queue": {
            "items": [
                {
                    "goal_id": "agent-lane-display-compaction",
                    "status": "active_state_agent_todo",
                    "waiting_on": "codex",
                    "severity": "action",
                    "recommended_action": long_todos[0]["text"],
                    "agent_todos": agent_todos,
                    "user_todos": user_todos,
                    "project_asset": {
                        "agent_todos": project_asset_agent_summary,
                    },
                }
            ]
        }
    }

    compact_agent_lane_todos_for_status_display(payload)

    item = payload["attention_queue"]["items"][0]
    compact_agent = item["agent_todos"]
    compact_user = item["user_todos"]
    assert compact_agent["open_count"] == 7, compact_agent
    assert compact_agent["done_count"] == 3, compact_agent
    assert len(compact_agent["items"]) == 1, compact_agent
    assert compact_agent["payload_compaction"]["compacted_lanes"]["items"] == {
        "shown": 1,
        "total": 7,
    }, compact_agent
    assert compact_user["open_count"] == 1, compact_user
    assert "handoff_gates" not in compact_user, compact_user
    project_asset_reference = item["project_asset"]["agent_todos"]
    assert project_asset_reference["open"] == 7, project_asset_reference
    assert "items" not in project_asset_reference, project_asset_reference
    assert project_asset_reference["payload_reference"]["canonical_path"] == (
        "attention_queue.items[].agent_todos"
    ), project_asset_reference
    marker = payload["agent_lane_todo_summary_compaction"]
    assert marker["schema_version"] == "agent_lane_status_todo_summary_compaction_v1", marker
    assert marker["project_asset_reference_count"] == 1, marker

    markdown = render_status_markdown(payload)
    assert "agent_todos: open=7" in markdown, markdown
    assert "open=None" not in markdown, markdown
    assert "next_agent_todo:" in markdown, markdown


def assert_status_agent_member_selected_lane_claim_survives_truncated_claim_list() -> None:
    goal_id = "agent-lane-truncated-claims-fixture"
    selected_todo = {
        "schema_version": "todo_item_v0",
        "todo_id": "todo_selected_lane",
        "index": 116,
        "role": "agent",
        "status": "open",
        "priority": "P0",
        "task_class": "advancement_task",
        "action_kind": "rapid_self_merge_kernel_iteration",
        "claimed_by": "codex-side-bypass",
        "text": "[P0] Continue the selected side-agent lane.",
    }
    visible_stale_claims = [
        {
            "schema_version": "todo_item_v0",
            "todo_id": f"todo_visible_stale_claim_{offset}",
            "index": 56 + offset,
            "role": "agent",
            "status": "blocked",
            "priority": "P0",
            "task_class": "advancement_task",
            "action_kind": "old_side_lane",
            "claimed_by": "codex-side-bypass",
            "text": "[P0] Old side-agent claim kept in the visible status window.",
        }
        for offset in range(10)
    ]
    primary_todo = {
        "schema_version": "todo_item_v0",
        "todo_id": "todo_primary_visible",
        "index": 22,
        "role": "agent",
        "status": "open",
        "priority": "P0",
        "task_class": "blocker",
        "claimed_by": "codex-main-control",
        "text": "[P0] Primary visible item.",
    }
    agent_todos = {
        "schema_version": "todo_summary_v0",
        "open_count": 105,
        "done_count": 0,
        "total_count": 105,
        "items": [primary_todo, *visible_stale_claims, selected_todo],
        "first_open_items": [primary_todo, *visible_stale_claims[:2]],
        "first_executable_items": [selected_todo],
    }
    coordination = {
        "agent_model": "peer_v1",
        "registered_agents": ["codex-main-control", "codex-side-bypass"],
    }
    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "goal_count": 1,
        "run_count": 1,
        "contract": {"ok": True, "summary": {"errors": 0, "warnings": 0, "checks": 0}},
        "global_registry": {"available": False, "summary": {}},
        "attention_queue": {
            "available": True,
            "item_count": 1,
            "items": [
                {
                    "goal_id": goal_id,
                    "status": "primary_route_active",
                    "waiting_on": "codex",
                    "severity": "action",
                    "recommended_action": primary_todo["text"],
                    "source": "registry",
                    "coordination": coordination,
                    "quota": {
                        "state": "eligible",
                        "compute": 1.0,
                        "window_hours": 24,
                        "slot_minutes": 1,
                        "allowed_slots": 10,
                    },
                    "agent_todos": agent_todos,
                    "project_asset": {
                        "owner": "codex-main-control",
                        "gate": "none",
                        "stop_condition": "stop on unsafe workspace or user gate",
                        "next_action": primary_todo["text"],
                        "agent_todos": project_asset_todo_summary(agent_todos, role="agent"),
                    },
                }
            ],
        },
        "run_history": {
            "goals": [
                {
                    "id": goal_id,
                    "registry_member": True,
                    "status": "primary_route_active",
                    "coordination": coordination,
                    "quota": {
                        "compute": 1.0,
                        "window_hours": 24,
                        "slot_minutes": 1,
                        "allowed_slots": 10,
                    },
                }
            ]
        },
    }
    attach_agent_lane_next_actions(payload, agent_id="codex-side-bypass")
    item = payload["attention_queue"]["items"][0]
    assert item["agent_lane_next_action"]["todo_id"] == "todo_selected_lane", item
    member = item["agent_member"]
    assert member["current_claims"][0] == "todo_selected_lane", member
    assert "todo_visible_stale_claim_0" in member["current_claims"], member
    markdown = render_status_markdown(payload)
    assert "claims=todo_selected_lane,todo_visible_stale_claim_0" in markdown, markdown


def assert_status_agent_lane_vision_lookback_survives_display_trim() -> None:
    goal_id = "agent-lane-vision-lookback-fixture"
    agent_id = "codex-product-capability"
    side_action = "[P2] Continue the product capability audit."
    side_todo = {
        "schema_version": "todo_item_v0",
        "todo_id": "todo_product_capability",
        "index": 7,
        "role": "agent",
        "status": "open",
        "priority": "P2",
        "task_class": "advancement_task",
        "action_kind": "engineering_quality_audit",
        "claimed_by": agent_id,
        "text": side_action,
    }
    agent_todos = {
        "schema_version": "todo_summary_v0",
        "open_count": 1,
        "done_count": 0,
        "total_count": 1,
        "items": [side_todo],
        "first_open_items": [side_todo],
        "first_executable_items": [side_todo],
    }
    coordination = {
        "agent_model": "peer_v1",
        "registered_agents": ["codex-main-control", agent_id],
    }
    stale_runs = [
        {
            "goal_id": goal_id,
            "generated_at": f"2026-07-09T06:{59 - offset:02d}:00+08:00",
            "classification": f"recent_status_{offset}",
            "agent_id": agent_id,
        }
        for offset in range(10)
    ]
    vision_run = {
        "goal_id": goal_id,
        "generated_at": "2026-07-09T05:45:45+08:00",
        "classification": "product_capability_open_vision_patch",
        "agent_id": agent_id,
        "agent_vision": {
            "schema_version": "agent_vision_v0",
            "agent_id": agent_id,
            "state": "open",
            "vision_patch": {
                "replan_trigger_summary": (
                    "active agent vision remains open with acceptance evidence still required"
                ),
                "acceptance_summary": (
                    "Continue bounded public-safe code/test improvements for status and quota."
                ),
            },
        },
    }
    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "goal_count": 1,
        "run_count": 11,
        "contract": {"ok": True, "summary": {"errors": 0, "warnings": 0, "checks": 0}},
        "global_registry": {"available": False, "summary": {}},
        "attention_queue": {
            "available": True,
            "item_count": 1,
            "items": [
                {
                    "goal_id": goal_id,
                    "status": "primary_route_active",
                    "waiting_on": "codex",
                    "severity": "action",
                    "recommended_action": "[P0] Keep the durable primary route.",
                    "source": "registry",
                    "coordination": coordination,
                    "quota": {
                        "state": "eligible",
                        "compute": 1.0,
                        "window_hours": 24,
                        "slot_minutes": 1,
                        "allowed_slots": 10,
                    },
                    "agent_todos": agent_todos,
                    "project_asset": {
                        "owner": "codex-main-control",
                        "gate": "none",
                        "next_action": "[P0] Keep the durable primary route.",
                        "agent_todos": project_asset_todo_summary(agent_todos, role="agent"),
                    },
                }
            ],
        },
        "run_history": {
            "recent_runs": [*stale_runs, vision_run],
            "goals": [
                {
                    "id": goal_id,
                    "registry_member": True,
                    "status": "primary_route_active",
                    "coordination": coordination,
                    "quota": {
                        "compute": 1.0,
                        "window_hours": 24,
                        "slot_minutes": 1,
                        "allowed_slots": 10,
                    },
                    "latest_runs": [*stale_runs, vision_run],
                }
            ],
        },
    }

    assert _status_collection_limit_for_agent_lane(
        requested_limit=5,
        agent_id=agent_id,
    ) > 5
    attach_agent_lane_next_actions(payload, agent_id=agent_id)
    item = payload["attention_queue"]["items"][0]
    goal_frontier = item["goal_frontier_projection"]
    assert len(goal_frontier["acceptance_gaps"]) == 1, goal_frontier
    assert goal_frontier["vision_continuation_audit"]["required"] is True, goal_frontier
    assert item["project_asset"]["goal_frontier_projection"] == goal_frontier, item

    _trim_run_history_for_status_display(payload, display_limit=5, collection_limit=30)
    assert len(payload["run_history"]["recent_runs"]) == 5, payload
    assert len(payload["run_history"]["goals"][0]["latest_runs"]) == 5, payload
    assert payload["agent_lane_projection_lookback"]["display_limit"] == 5, payload
    assert len(item["goal_frontier_projection"]["acceptance_gaps"]) == 1, item
    markdown = render_status_markdown(payload)
    assert "acceptance_gaps=1" in markdown, markdown
    assert "vision_continuation_audit: required=True decision=acceptance_gap_open trigger_count=1" in markdown, markdown
    assert "vision_gap_judge: done=False decision=continue" in markdown, markdown


def assert_status_agent_lane_frontier_hint_projection() -> None:
    goal_id = "agent-lane-frontier-status-fixture"
    primary_action = "[P0] Continue the primary controller benchmark route."
    primary_todo = {
        "schema_version": "todo_item_v0",
        "todo_id": "todo_primary_route",
        "index": 1,
        "role": "agent",
        "status": "open",
        "priority": "P0",
        "task_class": "advancement_task",
        "claimed_by": "codex-main-control",
        "text": primary_action,
    }
    agent_todos = {
        "schema_version": "todo_summary_v0",
        "open_count": 1,
        "done_count": 0,
        "total_count": 1,
        "first_open_items": [primary_todo],
        "items": [primary_todo],
    }
    coordination = {
        "agent_model": "peer_v1",
        "registered_agents": ["codex-main-control", "codex-side-bypass"],
    }
    payload = {
        "ok": True,
        "registry": "./fixtures/registry.json",
        "runtime_root": "./fixtures/runtime",
        "goal_count": 1,
        "run_count": 1,
        "contract": {"ok": True, "summary": {"errors": 0, "warnings": 0, "checks": 0}},
        "global_registry": {"available": False, "summary": {}},
        "attention_queue": {
            "available": True,
            "item_count": 1,
            "items": [
                {
                    "goal_id": goal_id,
                    "status": "primary_route_active",
                    "waiting_on": "codex",
                    "severity": "action",
                    "recommended_action": primary_action,
                    "source": "registry",
                    "coordination": coordination,
                    "quota": {
                        "state": "eligible",
                        "compute": 1.0,
                        "window_hours": 24,
                        "slot_minutes": 1,
                        "allowed_slots": 10,
                    },
                    "agent_todos": agent_todos,
                    "project_asset": {
                        "owner": "codex-main-control",
                        "gate": "none",
                        "stop_condition": "stop on unsafe workspace or user gate",
                        "next_action": primary_action,
                        "agent_todos": project_asset_todo_summary(agent_todos, role="agent"),
                    },
                }
            ],
        },
        "run_history": {
            "goals": [
                {
                    "id": goal_id,
                    "registry_member": True,
                    "status": "primary_route_active",
                    "coordination": coordination,
                    "quota": {
                        "compute": 1.0,
                        "window_hours": 24,
                        "slot_minutes": 1,
                        "allowed_slots": 10,
                    },
                }
            ]
        },
    }
    attach_agent_lane_next_actions(payload, agent_id="codex-side-bypass")
    item = payload["attention_queue"]["items"][0]
    assert "agent_lane_next_action" not in item, item
    frontier = item["agent_scope_frontier"]
    assert frontier["action"] == "reassignment_required", frontier
    goal_frontier = item["goal_frontier_projection"]
    assert goal_frontier["remaining_advancement_frontier"] == {
        "current_agent_claimed_advancement_count": 0,
        "unclaimed_advancement_count": 0,
        "other_agent_claimed_advancement_count": 1,
    }, goal_frontier
    assert goal_frontier["deferred_successors"]["ready_count"] == 0, goal_frontier
    assert goal_frontier["acceptance_gaps"] == [], goal_frontier
    hint = item["agent_lane_frontier_hint"]
    assert hint["schema_version"] == "agent_lane_frontier_hint_v0", hint
    assert hint["decision"] == "quiet_noop_blocker", hint
    assert hint["source"] == "agent_scope_frontier", hint
    assert hint["target_todo_id"] == "todo_primary_route", hint
    projection = payload["agent_lane_next_action_projection"]
    assert projection["attached_count"] == 0, projection
    assert projection["frontier_attached_count"] == 1, projection
    assert projection["frontier_hint_attached_count"] == 1, projection
    markdown = render_status_markdown(payload)
    assert "agent_lane_frontier_hint: agent=codex-side-bypass" in markdown, markdown
    assert "decision=quiet_noop_blocker" in markdown, markdown
    assert "target_todo_id=todo_primary_route" in markdown, markdown
    assert "goal_frontier_projection: replan_required=False" in markdown, markdown
    assert "other_agent_advancement=1 deferred_ready=0 acceptance_gaps=0" in markdown, markdown
