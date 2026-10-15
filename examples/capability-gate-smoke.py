#!/usr/bin/env python3
"""Smoke-test per-todo capability requirements in quota should-run."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.quota import build_quota_should_run, build_quota_slot_preview  # noqa: E402
from loopx.control_plane.testing.quota_fixtures import quota_todo_summary  # noqa: E402
from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
)


GOAL_ID = "capability-gate-goal"
AGENT_ID = "codex-main-control"
SCHEDULER_CONTEXT = GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT


def todo(
    index: int,
    priority: str,
    text: str,
    capabilities: list[str],
    *,
    action_kind: str | None = None,
    target_capabilities: list[str] | None = None,
) -> dict[str, Any]:
    item = {
        "schema_version": "todo_item_v0",
        "todo_id": f"todo_capability_{index}",
        "role": "agent",
        "status": "open",
        "done": False,
        "index": index,
        "priority": priority,
        "text": text,
        "title": text.removeprefix(f"[{priority}] "),
        "task_class": "advancement_task",
        "action_kind": action_kind or ("run_eval" if "benchmark" in text.lower() else "validate"),
        "required_capabilities": capabilities,
    }
    if target_capabilities:
        item["target_capabilities"] = target_capabilities
    return item


def status_payload(
    items: list[dict[str, Any]],
    *,
    available_capabilities: list[str] | None = None,
    registered_agent: str | None = None,
) -> dict[str, Any]:
    summary = quota_todo_summary(items)

    quota = {
        "compute": 1.0,
        "window_hours": 24,
        "slot_minutes": 1,
        "allowed_slots": 1440,
        "spent_slots": 0,
        "state": "eligible",
        "reason": "fixture eligible quota",
    }
    coordination: dict[str, Any] = {
        "available_capabilities": available_capabilities or [],
    }
    if registered_agent:
        coordination.update(
            {
                "agent_model": "peer_v1",
                "registered_agents": [registered_agent],
            }
        )
    return {
        "ok": True,
        "attention_queue": {
            "items": [
                {
                    "goal_id": GOAL_ID,
                    "status": "active",
                    "waiting_on": "codex",
                    "severity": "info",
                    "source": "project_asset",
                    "coordination": coordination,
                    "quota": quota,
                    "project_asset": {
                        "next_action": items[0]["text"] if items else "",
                        "agent_todos": summary,
                    },
                }
            ]
        },
        "run_history": {
            "goals": [
                {
                    "id": GOAL_ID,
                    "registry_member": True,
                    "coordination": coordination,
                    "quota": quota,
                    "latest_runs": [],
                }
            ]
        },
    }


def main() -> int:
    p0_benchmark = todo(
        1,
        "P0",
        "[P0] Run the benchmark runner once and write back compact result.",
        ["shell", "benchmark_runner"],
    )
    p0_validate = todo(
        2,
        "P0",
        "[P0] Validate the public control-plane docs.",
        ["shell", "filesystem_write"],
    )
    p0_network = todo(
        3,
        "P0",
        "[P0] Fetch external public data before drafting the packet.",
        ["shell", "network"],
    )
    p1_gpu = todo(
        4,
        "P1",
        "[P1] Rebuild the optional GPU-heavy visual fixture.",
        ["shell", "gpu_runner"],
    )
    p1_docs = todo(
        5,
        "P1",
        "[P1] Refresh the public docs fallback and validate it.",
        ["shell", "filesystem_write"],
    )
    p0_repair_benchmark_bridge = todo(
        7,
        "P0",
        "[P0] Repair benchmark treatment product-path parity before claiming uplift.",
        ["shell"],
        action_kind="benchmark_treatment_product_path_parity",
        target_capabilities=["benchmark_runner"],
    )

    p0_fallback = build_quota_should_run(
        status_payload([p0_benchmark, p0_validate, p1_docs]),
        goal_id=GOAL_ID,
        available_capabilities=["shell", "filesystem_write"],
        scheduler_execution_context=SCHEDULER_CONTEXT,
    )
    assert p0_fallback["should_run"] is True, p0_fallback
    assert p0_fallback["normal_delivery_allowed"] is True, p0_fallback
    assert p0_fallback["capability_gate"]["action"] == "run", p0_fallback
    assert p0_fallback["capability_gate"]["decision_owner"] == "agent", p0_fallback
    assert [
        item["todo_id"]
        for item in p0_fallback["capability_gate"]["runnable_candidates"]
    ] == ["todo_capability_2", "todo_capability_5"], p0_fallback
    assert p0_fallback["capability_gate"]["blocked_candidates"][0]["todo_id"] == "todo_capability_1", p0_fallback
    assert p0_fallback["capability_gate"]["repair_missing"] == ["benchmark_runner"], p0_fallback
    assert p0_fallback["capability_gate"]["resolution_bindings"][0]["action"] == "repair_bridge", (
        p0_fallback
    )
    assert p0_fallback["capability_gate"]["resolution_bindings"][0]["capability"] == (
        "benchmark_runner"
    ), p0_fallback
    assert p0_fallback["capability_gate"]["resolution_bindings"][0][
        "primary_blocked_todo_id"
    ] == "todo_capability_1", p0_fallback
    assert p0_fallback["recommended_action"] == p0_validate["text"], p0_fallback
    assert "choose one of 2 capability-runnable todo(s)" in p0_fallback["interaction_contract"]["agent_channel"]["primary_action"], p0_fallback
    assert "protocol_action_packet" not in p0_fallback, p0_fallback

    fallback = build_quota_should_run(
        status_payload([p0_benchmark, p0_network, p1_gpu, p1_docs]),
        goal_id=GOAL_ID,
        available_capabilities=["shell", "filesystem_write"],
        scheduler_execution_context=SCHEDULER_CONTEXT,
    )
    assert fallback["should_run"] is True, fallback
    assert fallback["normal_delivery_allowed"] is True, fallback
    assert fallback["capability_gate"]["action"] == "run", fallback
    assert [item["todo_id"] for item in fallback["capability_gate"]["runnable_candidates"]] == [
        "todo_capability_5",
    ], fallback
    assert [item["todo_id"] for item in fallback["capability_gate"]["blocked_candidates"]] == [
        "todo_capability_1",
        "todo_capability_3",
        "todo_capability_4",
    ], fallback
    assert fallback["capability_gate"]["blocked_candidates"][0]["missing_capabilities"] == ["benchmark_runner"], fallback
    assert fallback["capability_gate"]["blocked_candidates"][1]["missing_capabilities"] == ["network"], fallback
    assert fallback["capability_gate"]["blocked_candidates"][2]["missing_capabilities"] == ["gpu_runner"], fallback
    assert fallback["recommended_action"] == p1_docs["text"], fallback
    assert "choose one of 1 capability-runnable todo(s)" in fallback["interaction_contract"]["agent_channel"]["primary_action"], fallback
    assert "protocol_action_packet" not in fallback, fallback

    repair_candidate = build_quota_should_run(
        status_payload([p0_repair_benchmark_bridge, p0_benchmark, p1_docs]),
        goal_id=GOAL_ID,
        available_capabilities=["shell", "filesystem_write"],
        scheduler_execution_context=SCHEDULER_CONTEXT,
    )
    assert repair_candidate["should_run"] is True, repair_candidate
    assert repair_candidate["normal_delivery_allowed"] is True, repair_candidate
    assert repair_candidate["capability_gate"]["action"] == "run", repair_candidate
    assert [
        item["todo_id"]
        for item in repair_candidate["capability_gate"]["runnable_candidates"]
    ] == ["todo_capability_7", "todo_capability_5"], repair_candidate
    bridge_candidate = repair_candidate["capability_gate"]["runnable_candidates"][0]
    assert bridge_candidate["missing_capabilities"] == [], repair_candidate
    assert bridge_candidate["target_capabilities"] == ["benchmark_runner"], repair_candidate
    assert bridge_candidate["missing_target_capabilities"] == ["benchmark_runner"], repair_candidate
    assert bridge_candidate["capability_action"] == "repair_bridge", repair_candidate
    assert bridge_candidate["capability_repair_mode"] is True, repair_candidate
    assert repair_candidate["capability_gate"]["repair_missing"] == ["benchmark_runner"], repair_candidate
    assert repair_candidate["capability_gate"]["repair_candidate_count"] == 1, repair_candidate
    assert [
        item["todo_id"]
        for item in repair_candidate["capability_gate"]["blocked_candidates"]
    ] == ["todo_capability_1"], repair_candidate
    assert not any(
        "--action-kind materialize_capability" in action
        for action in repair_candidate["interaction_contract"]["cli_channel"]["next_cli_actions"]
    ), repair_candidate

    benchmark_ready = build_quota_should_run(
        status_payload([p0_benchmark, p0_validate, p1_docs]),
        goal_id=GOAL_ID,
        available_capabilities=["shell", "filesystem_write", "benchmark_runner"],
        scheduler_execution_context=SCHEDULER_CONTEXT,
    )
    assert benchmark_ready["capability_gate"]["action"] == "run", benchmark_ready
    assert [
        item["todo_id"]
        for item in benchmark_ready["capability_gate"]["runnable_candidates"]
    ] == ["todo_capability_1", "todo_capability_2", "todo_capability_5"], benchmark_ready
    assert benchmark_ready["capability_gate"]["blocked_candidates"] == [], benchmark_ready

    benchmark_ready_from_goal = build_quota_should_run(
        status_payload(
            [p0_benchmark, p0_validate, p1_docs],
            available_capabilities=["benchmark_runner"],
        ),
        goal_id=GOAL_ID,
        available_capabilities=["shell", "filesystem_write"],
        scheduler_execution_context=SCHEDULER_CONTEXT,
    )
    assert benchmark_ready_from_goal["capability_gate"]["action"] == "run", benchmark_ready_from_goal
    assert [
        item["todo_id"]
        for item in benchmark_ready_from_goal["capability_gate"]["runnable_candidates"]
    ] == ["todo_capability_1", "todo_capability_2", "todo_capability_5"], benchmark_ready_from_goal
    assert benchmark_ready_from_goal["goal_boundary"]["available_capabilities"] == [
        "benchmark_runner"
    ], benchmark_ready_from_goal

    spend_preview = build_quota_slot_preview(
        status_payload([p0_benchmark, p0_validate, p1_docs]),
        goal_id=GOAL_ID,
        available_capabilities=["shell", "filesystem_write", "benchmark_runner"],
    )
    assert spend_preview["ok"] is True, spend_preview
    assert spend_preview["before"]["normal_delivery_allowed"] is True, spend_preview
    assert spend_preview["before"]["capability_gate"]["runnable_candidates"][0]["todo_id"] == "todo_capability_1", spend_preview

    bridge_missing = build_quota_should_run(
        status_payload([p0_benchmark]),
        goal_id=GOAL_ID,
        available_capabilities=["shell", "filesystem_write"],
        scheduler_execution_context=SCHEDULER_CONTEXT,
    )
    assert bridge_missing["should_run"] is True, bridge_missing
    assert bridge_missing["normal_delivery_allowed"] is False, bridge_missing
    assert bridge_missing["capability_repair_allowed"] is True, bridge_missing
    assert bridge_missing["effective_action"] == "capability_bridge_repair", bridge_missing
    assert bridge_missing["capability_gate"]["action"] == "repair_bridge", bridge_missing
    assert bridge_missing["interaction_contract"]["mode"] == "capability_bridge_repair", bridge_missing
    assert bridge_missing["execution_obligation"]["kind"] == "capability_bridge_repair", bridge_missing

    network_todo = todo(
        6,
        "P0",
        "[P0] Fetch external public data before drafting the packet.",
        ["shell", "network"],
    )
    runtime_repair = build_quota_should_run(
        status_payload([network_todo]),
        goal_id=GOAL_ID,
        available_capabilities=["shell", "filesystem_write"],
        scheduler_execution_context=SCHEDULER_CONTEXT,
    )
    assert runtime_repair["should_run"] is True, runtime_repair
    assert runtime_repair["normal_delivery_allowed"] is False, runtime_repair
    assert runtime_repair["capability_repair_allowed"] is True, runtime_repair
    assert runtime_repair["effective_action"] == "capability_bridge_repair", runtime_repair
    assert runtime_repair["requires_user_action"] is False, runtime_repair
    assert runtime_repair["capability_gate"]["action"] == "repair_bridge", runtime_repair
    assert runtime_repair["capability_gate"]["owner_missing"] == [], runtime_repair
    assert runtime_repair["capability_gate"]["repair_missing"] == ["network"], runtime_repair
    assert runtime_repair["interaction_contract"]["user_channel"]["action_required"] is False

    credentials_todo = todo(
        9,
        "P0",
        "[P0] Use owner-provided credentials for the protected integration.",
        ["shell", "credentials"],
    )
    owner_gate = build_quota_should_run(
        status_payload([credentials_todo]),
        goal_id=GOAL_ID,
        available_capabilities=["shell", "filesystem_write"],
        scheduler_execution_context=SCHEDULER_CONTEXT,
    )
    assert owner_gate["should_run"] is False, owner_gate
    assert owner_gate["requires_user_action"] is True, owner_gate
    assert owner_gate["capability_gate"]["action"] == "ask_owner", owner_gate
    assert owner_gate["interaction_contract"]["user_channel"]["action_required"] is True, owner_gate
    assert owner_gate["interaction_contract"]["user_channel"]["actions"] == [
        "provide or authorize the missing owner-held capability: credentials "
        "for todo_capability_9"
    ], owner_gate
    assert any(
        "--task-class user_gate" in action
        and "--action-kind provide_capability" in action
        and "--target-capability credentials" in action
        and "--unblocks-todo-id todo_capability_9" in action
        for action in owner_gate["interaction_contract"]["cli_channel"]["next_cli_actions"]
    ), owner_gate
    assert owner_gate["heartbeat_recommendation"]["notify"] == "NOTIFY", owner_gate

    owner_with_fallback = build_quota_should_run(
        status_payload(
            [credentials_todo, p1_docs],
            registered_agent=AGENT_ID,
        ),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        available_capabilities=["shell", "filesystem_write"],
        scheduler_execution_context=SCHEDULER_CONTEXT,
    )
    assert owner_with_fallback["should_run"] is True, owner_with_fallback
    assert owner_with_fallback["normal_delivery_allowed"] is True, owner_with_fallback
    assert owner_with_fallback["requires_user_action"] is True, owner_with_fallback
    assert owner_with_fallback["capability_gate"]["action"] == "run", owner_with_fallback
    assert owner_with_fallback["capability_gate"]["owner_missing"] == [
        "credentials"
    ], owner_with_fallback
    assert owner_with_fallback["interaction_contract"]["mode"] == (
        "bounded_delivery_with_user_notice"
    ), owner_with_fallback
    assert owner_with_fallback["interaction_contract"]["agent_channel"][
        "must_attempt"
    ] is True, owner_with_fallback
    assert owner_with_fallback["interaction_contract"]["user_channel"]["actions"] == [
        "provide or authorize the missing owner-held capability: credentials "
        "for todo_capability_9"
    ], owner_with_fallback
    unselected = owner_with_fallback["interaction_contract"]["cli_channel"]
    assert unselected["selection_required"] is True, owner_with_fallback
    assert unselected["next_cli_actions"] == [], owner_with_fallback
    assert "{todo_id}" in unselected["selection_command"]["command_args_template"], unselected
    selected_fallback = build_quota_should_run(
        status_payload([credentials_todo, p1_docs], registered_agent=AGENT_ID),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        available_capabilities=["shell", "filesystem_write"],
        scheduler_execution_context=SCHEDULER_CONTEXT,
        requested_action_todo_id=p1_docs["todo_id"],
    )
    assert selected_fallback["ok"] is True, selected_fallback
    assert selected_fallback["selected_todo"]["todo_id"] == p1_docs["todo_id"], selected_fallback
    assert selected_fallback["action_selection_qualification"]["state"] == "qualified", selected_fallback
    assert selected_fallback["interaction_contract"]["cli_channel"].get("selection_required") is not True, selected_fallback
    assert any(
        "--task-class user_gate" in action
        and "--target-capability credentials" in action
        and f"--blocks-agent {AGENT_ID}" in action
        and "--unblocks-todo-id todo_capability_9" in action
        for action in selected_fallback["interaction_contract"]["cli_channel"]["next_cli_actions"]
    ), selected_fallback

    mixed_capability_todo = todo(
        8,
        "P0",
        "[P0] Observe a public external lifecycle and write back the next action.",
        ["shell", "credentials", "external_evidence_poll"],
    )
    mixed_owner_gate = build_quota_should_run(
        status_payload([mixed_capability_todo]),
        goal_id=GOAL_ID,
        available_capabilities=["shell", "filesystem_write"],
    )
    mixed_gate = mixed_owner_gate["capability_gate"]
    assert mixed_gate["action"] == "ask_owner", mixed_owner_gate
    assert mixed_gate["decision_owner"] == "user", mixed_owner_gate
    assert mixed_gate["owner_missing"] == ["credentials"], mixed_owner_gate
    assert mixed_gate["repair_missing"] == ["external_evidence_poll"], mixed_owner_gate
    assert mixed_gate["resolution_steps"] == [
        {
            "owner": "user",
            "action": "provide_or_authorize",
            "capabilities": ["credentials"],
        },
        {
            "owner": "agent",
            "action": "repair_bridge",
            "capabilities": ["external_evidence_poll"],
        },
    ], mixed_owner_gate
    assert mixed_owner_gate["interaction_contract"]["user_channel"]["action_required"] is True
    assert "credentials" in mixed_gate["owner_action"], mixed_owner_gate

    mixed_ready = build_quota_should_run(
        status_payload([mixed_capability_todo]),
        goal_id=GOAL_ID,
        available_capabilities=[
            "shell",
            "filesystem_write",
            "credentials",
            "external_evidence_poll",
        ],
    )
    assert mixed_ready["capability_gate"]["action"] == "run", mixed_ready
    assert mixed_ready["interaction_contract"]["user_channel"]["action_required"] is False

    print("capability-gate-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
