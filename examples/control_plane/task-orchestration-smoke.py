#!/usr/bin/env python3
"""Smoke explicitly selected task-scoped coordination among equal peers."""

from __future__ import annotations

from copy import deepcopy
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.quota.turn_envelope import build_turn_envelope  # noqa: E402
from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    SchedulerRuntimeProfile,
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.turn_driver import (  # noqa: E402
    build_loopx_turn_host_request,
    build_loopx_turn_plan,
)
from loopx.quota import (  # noqa: E402
    build_quota_should_run,
    render_quota_should_run_markdown,
)


GOAL_ID = "task-orchestration-fixture"
PEERS = ["codex-alpha", "codex-beta", "codex-gamma"]
DORMANT_PEER = "codex-dormant"


def payload(
    *,
    reverse_agents: bool = False,
    peer_task_coordinator: str | None = None,
) -> dict:
    registered_agents = [*PEERS, DORMANT_PEER]
    if reverse_agents:
        registered_agents = list(reversed(registered_agents))
    coordination = {
        "agent_model": "peer_v1",
        "registered_agents": registered_agents,
    }
    if peer_task_coordinator:
        coordination["peer_task_coordination"] = {
            "coordinator_agent_id": peer_task_coordinator,
        }
    todos = [
        {
            "index": index,
            "status": "open",
            "todo_id": f"todo_peer_{index}",
            "task_class": "advancement_task",
            "priority": "P0",
            "text": f"Advance peer lane {index}.",
            "claimed_by": agent_id,
        }
        for index, agent_id in enumerate(PEERS, start=1)
    ]
    todos.append(
        {
            "index": len(todos) + 1,
            "status": "done",
            "todo_id": "todo_dormant_done",
            "task_class": "advancement_task",
            "priority": "P0",
            "text": "A completed lane must not join the task bundle.",
            "claimed_by": DORMANT_PEER,
        }
    )
    goal = {
        "id": GOAL_ID,
        "status": "active",
        "registry_member": True,
        "adapter_kind": "read_only_project_map_v0",
        "adapter_status": "connected-read-only",
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
            "slot_minutes": 1,
            "allowed_slots": 1440,
            "spent_slots": 0,
        },
        "coordination": coordination,
        "spawn_policy": {
            "mode": "multi_subagent",
            "allowed": True,
            "max_children": 2,
        },
    }
    attention = {
        "goal_id": GOAL_ID,
        "status": "state_refreshed",
        "waiting_on": "codex",
        "severity": "action",
        "source": "fixture",
        "recommended_action": "coordinate the bounded peer task bundle",
        "coordination": coordination,
        "quota": {**goal["quota"], "state": "eligible", "reason": "eligible fixture"},
        "agent_todos": {
            "schema_version": "todo_summary_v0",
            "source_section": "Agent Todo",
            "total": len(todos),
            "open": len(todos),
            "done": 0,
            "items": todos,
        },
    }
    return {
        "ok": True,
        "attention_queue": {"items": [attention]},
        "run_history": {"goals": [goal]},
        "agent_management_projection": {
            "schema_version": "agent_management_projection_v0",
            "agents": [
                {
                    "agent_id": agent_id,
                    "state": "running",
                    "last_activity_at": "2026-08-14T12:00:00Z",
                }
                for agent_id in PEERS
            ]
            + [
                {
                    "agent_id": DORMANT_PEER,
                    "state": "waiting",
                    "last_activity_at": None,
                }
            ],
        },
    }


def adaptive_payload(
    *, allowed_domains: tuple[str, ...] = ("code", "validation")
) -> dict:
    coordinator = "codex-alpha"
    coordination = {
        "agent_model": "peer_v1",
        "registered_agents": [coordinator],
        "write_scope": ["loopx/**", "tests/**"],
    }
    todos = [
        {
            "index": 1,
            "status": "open",
            "todo_id": "todo_primary",
            "task_class": "advancement_task",
            "action_kind": "implement",
            "task_domain": "code",
            "priority": "P0",
            "text": "Implement the primary adaptive slice.",
            "required_write_scopes": ["loopx/**"],
        },
        {
            "index": 2,
            "status": "open",
            "todo_id": "todo_validation",
            "task_class": "advancement_task",
            "action_kind": "validate",
            "task_domain": "validation",
            "priority": "P1",
            "text": "Validate the independent adaptive slice.",
            "required_write_scopes": ["tests/**"],
        },
    ]
    goal = {
        "id": GOAL_ID,
        "status": "active",
        "registry_member": True,
        "adapter_kind": "read_only_project_map_v0",
        "adapter_status": "connected-read-only",
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
            "slot_minutes": 1,
            "allowed_slots": 1440,
            "spent_slots": 0,
        },
        "coordination": coordination,
        "spawn_policy": {
            "mode": "multi_subagent",
            "allowed": True,
            "max_children": 2,
            "allowed_domains": list(allowed_domains),
        },
    }
    attention = {
        "goal_id": GOAL_ID,
        "status": "state_refreshed",
        "waiting_on": "codex",
        "severity": "action",
        "source": "fixture",
        "recommended_action": "coordinate the adaptive task bundle",
        "coordination": coordination,
        "quota": {**goal["quota"], "state": "eligible", "reason": "eligible fixture"},
        "agent_todos": {
            "schema_version": "todo_summary_v0",
            "source_section": "Agent Todo",
            "total": len(todos),
            "open": len(todos),
            "done": 0,
            "items": todos,
        },
    }
    return {
        "ok": True,
        "attention_queue": {"items": [attention]},
        "run_history": {"goals": [goal]},
    }


def without_agent_work(state: dict, *, agent_id: str) -> dict:
    result = deepcopy(state)
    summary = result["attention_queue"]["items"][0]["agent_todos"]
    for item in summary["items"]:
        if item.get("claimed_by") == agent_id:
            item["status"] = "done"
            item["done"] = True
    summary["open"] = sum(
        item.get("status") == "open" for item in summary["items"]
    )
    summary["done"] = sum(item.get("done") is True for item in summary["items"])
    return result


def main() -> int:
    decisions = {
        agent_id: build_quota_should_run(
            payload(),
            goal_id=GOAL_ID,
            agent_id=agent_id,
        )
        for agent_id in PEERS
    }
    assert all(
        "task_orchestration_contract" not in decision
        for decision in decisions.values()
    ), decisions
    assert all(
        decision["effective_action"] != "coordinate_task_bundle"
        for decision in decisions.values()
    ), decisions

    coordinator = "codex-alpha"
    blocked_decision = build_quota_should_run(
        payload(peer_task_coordinator=coordinator),
        goal_id=GOAL_ID,
        agent_id=coordinator,
    )
    blocked_contract = blocked_decision["task_orchestration_contract"]
    assert blocked_contract["execution_state"] == "blocked", blocked_contract
    assert blocked_contract["eligible_peer_lanes"] == [], blocked_contract
    assert len(blocked_contract["blocked_peer_lanes"]) == 2, blocked_contract
    assert blocked_contract["terminal_outcome"] == "blocked", blocked_contract
    assert blocked_decision["effective_action"] != "coordinate_task_bundle", (
        blocked_decision
    )
    assert blocked_decision["interaction_contract"]["mode"] == "bounded_delivery", (
        blocked_decision
    )

    blocked_without_fallback = without_agent_work(
        payload(peer_task_coordinator=coordinator),
        agent_id=coordinator,
    )
    blocked_turns = [
        build_quota_should_run(
            blocked_without_fallback,
            goal_id=GOAL_ID,
            agent_id=coordinator,
            scheduler_execution_context=(
                scheduler_execution_context_for_runtime_profile(
                    SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT
                )
            ),
        )
        for _ in range(2)
    ]
    for blocked_turn in blocked_turns:
        assert blocked_turn["decision"] == "peer_coordination_blocked", blocked_turn
        assert blocked_turn["should_run"] is False, blocked_turn
        assert blocked_turn["interaction_contract"]["mode"] == (
            "peer_coordination_blocked"
        ), blocked_turn
        assert blocked_turn["automation_liveness"]["keep_active"] is True, (
            blocked_turn
        )
        assert blocked_turn["automation_liveness"]["pause_allowed"] is False, (
            blocked_turn
        )
        assert blocked_turn["automation_liveness"]["automation_action"] == (
            "keep_active_peer_coordination_backoff"
        ), blocked_turn
        assert blocked_turn["scheduler_hint"]["action"] == (
            "backoff_until_reassigned"
        ), blocked_turn
        assert blocked_turn["scheduler_hint"]["cadence_class"] == (
            "peer_coordination_wait"
        ), blocked_turn
        assert blocked_turn["scheduler_hint"]["codex_app"][
            "recommended_interval_minutes"
        ] == 10, blocked_turn
        assert blocked_turn["scheduler_hint"]["codex_app"][
            "example_progression_minutes"
        ] == [10, 20, 30, 60], blocked_turn
        assert blocked_turn["scheduler_hint"]["unchanged_poll"]["limits"][
            "local_scheduler"
        ] == 3, blocked_turn
        assert blocked_turn["interaction_contract"]["cli_channel"][
            "spend_after_validation"
        ] is False, blocked_turn
    assert [
        turn["task_orchestration_contract"]["assignment_key"]
        for turn in blocked_turns
    ] == [
        blocked_turns[0]["task_orchestration_contract"]["assignment_key"],
        blocked_turns[0]["task_orchestration_contract"]["assignment_key"],
    ], blocked_turns

    decision = build_quota_should_run(
        payload(peer_task_coordinator=coordinator),
        goal_id=GOAL_ID,
        agent_id=coordinator,
        available_capabilities=["peer_agent_activation"],
    )
    contract = decision["task_orchestration_contract"]
    assert decision["effective_action"] == "coordinate_task_bundle", decision
    assert decision["interaction_contract"]["mode"] == "task_orchestration", decision
    assert contract["coordinator_agent_id"] == coordinator, contract
    assert contract["execution_state"] == "ready", contract
    assert coordinator in PEERS and coordinator != DORMANT_PEER, contract
    assert contract["writeback_owner"] == "task_coordinator", contract
    assert "controller_agent_id" not in contract, contract
    assert "eligible_child_lanes" not in contract, contract
    assert all(
        lane["agent_id"] != coordinator for lane in contract["eligible_peer_lanes"]
    ), contract
    assert all(
        lane["agent_id"] != DORMANT_PEER
        for lane in contract["eligible_peer_lanes"]
    ), contract
    assert len(contract["eligible_peer_lanes"]) == 2, contract
    markdown = render_quota_should_run_markdown(decision)
    assert "task_orchestration: mode=task_scoped_peer" in markdown, markdown

    reversed_decision = build_quota_should_run(
        payload(reverse_agents=True, peer_task_coordinator=coordinator),
        goal_id=GOAL_ID,
        agent_id=coordinator,
        available_capabilities=["peer_agent_activation"],
    )
    assert reversed_decision["task_orchestration_contract"][
        "coordinator_agent_id"
    ] == coordinator, reversed_decision

    adaptive_decision = build_quota_should_run(
        adaptive_payload(),
        goal_id=GOAL_ID,
        agent_id="codex-alpha",
        available_capabilities=["subagent_spawn", "subagent_resume"],
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.CODEX_CLI_VISIBLE
        ),
    )
    adaptive_contract = adaptive_decision["task_orchestration_contract"]
    assert adaptive_contract["schema_version"] == "task_orchestration_contract_v2"
    assert adaptive_contract["mode"] == "adaptive"
    assert adaptive_contract["primary_todo_id"] == "todo_primary"
    assert adaptive_contract["eligible_child_lanes"][0]["todo_id"] == (
        "todo_validation"
    )
    assert adaptive_contract["child_brief_defaults"]["context_policy"]["allowed"] == [
        "fresh",
        "resume",
    ]

    unrestricted_state = adaptive_payload(allowed_domains=())
    unrestricted_state["attention_queue"]["items"][0]["agent_todos"]["items"][
        1
    ].pop("task_domain")
    unrestricted_decision = build_quota_should_run(
        unrestricted_state,
        goal_id=GOAL_ID,
        agent_id="codex-alpha",
        available_capabilities=["subagent_spawn", "subagent_resume"],
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.CODEX_CLI_VISIBLE
        ),
    )
    unrestricted_contract = unrestricted_decision["task_orchestration_contract"]
    assert unrestricted_contract["mode"] == "adaptive"
    assert unrestricted_contract["eligible_child_lanes"][0]["todo_id"] == (
        "todo_validation"
    )

    turn_envelope = build_turn_envelope(adaptive_decision)
    assert turn_envelope["action_signature"]["matches"] is True
    codex_plan = build_loopx_turn_plan(
        turn_envelope,
        host="codex-cli",
        execution_mode="interactive-visible",
    )
    codex_request = build_loopx_turn_host_request(codex_plan)
    assert [
        item for item in codex_request["child_operations"][0]["available_contexts"]
    ] == ["fresh"]
    assert codex_request["child_operations"][0]["host_adapter"] == {
        "host": "codex-cli",
        "native_operation": "spawn_agent",
        "arguments": {"fork_turns": "none"},
        "requires_session": False,
    }

    claude_decision = build_quota_should_run(
        adaptive_payload(),
        goal_id=GOAL_ID,
        agent_id="codex-alpha",
        available_capabilities=["subagent_spawn"],
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.CLAUDE_CODE_VISIBLE
        ),
    )
    claude_plan = build_loopx_turn_plan(
        build_turn_envelope(claude_decision),
        host="claude-code",
        execution_mode="interactive-visible",
    )
    assert claude_plan["child_operations"][0]["available_contexts"] == ["fresh"]
    assert claude_plan["child_operations"][0]["host_adapter"] == {
        "host": "claude-code",
        "native_operation": "Task",
        "arguments": {},
        "requires_session": False,
    }
    print("task-orchestration-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
