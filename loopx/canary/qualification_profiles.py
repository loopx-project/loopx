from __future__ import annotations

from typing import Any

CONTROL_PLANE_QUALIFICATION_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "id": "lark-goal-channel-human-gate-delivery",
        "title": "Lark Goal Channel human-gate delivery",
        "quality_risk": "high",
        "purpose": (
            "Qualify opt-in user-gate delivery from canonical active state and "
            "quota through the verified Lark Goal Channel transport boundary."
        ),
        "catalog_families": [
            "Work Routing",
            "State And Boundary",
        ],
        "trigger_hints": (
            "lark goal channel",
            "human gate delivery",
            "goal-channel",
            "loopx/cli_commands/project_lifecycle.py",
            "loopx/extensions/lark/goal_channel_lifecycle.py",
            "loopx/extensions/lark/goal_channel_runtime.py",
            "examples/lark-goal-channel-human-gate-delivery-smoke.py",
            "docs/architecture/rfcs/goal-channel-collaboration-v0.md",
        ),
        "checks": [
            {
                "command": (
                    "python3 "
                    "examples/lark-goal-channel-human-gate-delivery-smoke.py"
                ),
                "tier": "default",
                "reason": (
                    "guards canonical user-gate selection, stable gate identity, "
                    "bot send/readback, receipt persistence, and duplicate suppression"
                ),
            },
        ],
    },
    {
        "id": "change-quality-exact-receipt",
        "title": "Change-quality exact-scope receipt integrity",
        "quality_risk": "high",
        "purpose": (
            "Qualify default-off policy, exact diff identity, bounded safe-fix "
            "authority, stale receipt rejection, and strict premerge enforcement."
        ),
        "catalog_families": [
            "State And Boundary",
            "Planning Governance",
        ],
        "trigger_hints": (
            "change quality",
            "change-quality",
            "change_quality_qualification",
            "loopx/capabilities/change_quality/",
            "skills/loopx-change-quality/",
            "tests/capabilities/test_change_quality.py",
            "examples/change-quality-qualification-smoke.py",
        ),
        "checks": [
            {
                "command": "python3 examples/change-quality-qualification-smoke.py",
                "tier": "default",
                "reason": (
                    "guards default-off activation, exact-scope receipt readback, "
                    "and stale-diff rejection through the public capability contract"
                ),
            },
        ],
    },
    {
        "id": "control-plane-state-machine",
        "title": "Control-plane state-machine composition",
        "quality_risk": "high",
        "purpose": (
            "Run a bounded cross-state-machine canary when interaction, work-lane, "
            "scheduler, frontier, writeback, or quota-spend transitions change together."
        ),
        "catalog_families": [
            "Work Routing",
            "State And Boundary",
            "Planning Governance",
        ],
        "trigger_hints": (
            "state-machine",
            "state machine",
            "interaction_contract",
            "work_lane_contract",
            "scheduler_hint",
            "goal_frontier",
            "automation_liveness",
            "spend-slot",
            "scheduler-ack",
            "monitor_target",
            "monitor_poll_writeback",
            "interaction_contract.py",
            "loopx/control_plane/quota/stall_repair.py",
            "loopx/control_plane/scheduler/arbitration.py",
            "loopx/control_plane/scheduler/monitor_poll_writeback.py",
            "loopx/control_plane/quota/monitor_poll_commit.ts",
            "loopx/control_plane/todos/decision_scope.py",
            "loopx/control_plane/testing/decision_replay.py",
            "loopx/control_plane/work_items/interaction_contract.py",
            "loopx/control_plane/work_items/execution_obligation.py",
            "loopx/control_plane/work_items/goal_route_hint.py",
            "loopx/control_plane/work_items/delivery_history.py",
            "loopx/control_plane/work_items/delivery_history.ts",
            "loopx/control_plane/work_items/work_lane.py",
            "control-plane-integrated-canary-smoke.py",
            "interaction-contract-state-machine-smoke.py",
            "interaction-scheduler-authority-smoke.py",
            "docs/product/core-control-plane/state-machine.md",
        ),
        "checks": [
            {
                "command": "python3 examples/control_plane/control-plane-integrated-canary-smoke.py",
                "tier": "deep",
                "reason": (
                    "samples the Markdown Todo projection, status, quota interaction contract, "
                    "work-lane contract, scheduler ack, refresh-state, spend-slot, and review-packet handoff path; "
                    "kept deep because it is a slow end-to-end fixture"
                ),
            },
            {
                "command": "python3 examples/control_plane/peer-agent-continuation-state-machine-smoke.py",
                "tier": "default",
                "reason": (
                    "guards peer self-merge continuation through successor todo selection, "
                    "agent-lane refresh, scheduler ack, quota spend, and preserved goal next action"
                ),
            },
            {
                "command": "python3 examples/control_plane/interaction-contract-state-machine-smoke.py",
                "tier": "default",
                "reason": (
                    "guards interaction/protocol state-machine modes across active work, user notice, "
                    "monitor quiet, autonomous replan, agent-scope wait, and successor replan"
                ),
            },
            {
                "command": "python3 examples/control_plane/interaction-scheduler-authority-smoke.py",
                "tier": "default",
                "reason": (
                    "replays compact real quota shapes so blocking gates, non-blocking "
                    "user actions, decision scopes, and scheduler cadence stay aligned"
                ),
            },
            {
                "command": "python3 examples/control_plane/heartbeat-quota-flow-smoke.py",
                "tier": "default",
                "reason": "guards the heartbeat writeback then spend lifecycle that the composition canary executes",
            },
            {
                "command": "python3 examples/control_plane/quota-scheduler-state-ack-smoke.py",
                "tier": "default",
                "reason": "guards scheduler_hint stateful ack progression and no-spend cadence transitions",
            },
            {
                "command": "python3 examples/control_plane/work-lane-contract-smoke.py",
                "tier": "deep",
                "reason": "covers additional work-lane policy branches outside the integrated active-work path",
            },
        ],
    },
    {
        "id": "goal-frontier-replan-rules",
        "title": "Goal-frontier replan rule ordering",
        "quality_risk": "high",
        "purpose": (
            "Qualify ordered goal-frontier replan precedence independently from "
            "payload rendering and broader scheduler composition."
        ),
        "catalog_families": [
            "Work Routing",
            "Planning Governance",
            "State And Boundary",
        ],
        "trigger_hints": (
            "goal frontier replan",
            "goal_frontier_replan_rules",
            "loopx/control_plane/goals/goal_frontier/__init__.py",
            "loopx/control_plane/goals/goal_frontier/terminal.py",
            "loopx/control_plane/goals/goal_frontier/replan_rules.py",
            "goal-frontier-replan-rules-smoke.py",
        ),
        "checks": [
            {
                "command": "python3 examples/control_plane/goal-frontier-replan-rules-smoke.py",
                "tier": "default",
                "reason": (
                    "guards ordered replan precedence, peer-lane isolation, and "
                    "monitor-frontier exhaustion through quota routing"
                ),
            },
            {
                "command": "python3 examples/control_plane/quota-replan-decision-plane-smoke.py",
                "tier": "deep",
                "reason": "covers the wider replan decision plane and peer ownership contract",
            },
        ],
    },
    {
        "id": "scheduler-ack-route",
        "title": "Scheduler ACK state and route binding",
        "quality_risk": "high",
        "purpose": (
            "Qualify scheduler ACK state progression and originating registry/runtime "
            "routing as independent contracts without nested smoke execution."
        ),
        "catalog_families": [
            "Work Routing",
            "State And Boundary",
        ],
        "trigger_hints": (
            "scheduler-ack",
            "scheduler ack",
            "scheduler_hint",
            "ack_cli_args",
            "route_binding",
            "loopx/control_plane/scheduler/ack.py",
            "loopx/control_plane/scheduler/scheduler_hint.py",
            "loopx/control_plane/scheduler/monitor_wait.py",
            "loopx/control_plane/scheduler/state.py",
            "loopx/control_plane/scheduler/state_transition_rules.py",
            "loopx/cli_commands/quota",
            "quota-scheduler-state-ack-smoke.py",
            "quota-scheduler-registry-route-smoke.py",
            "monitor-scheduler-contract-smoke.py",
        ),
        "checks": [
            {
                "command": "python3 examples/control_plane/quota-scheduler-state-ack-smoke.py",
                "tier": "default",
                "reason": "guards scheduler_hint stateful ACK progression and no-spend cadence transitions",
            },
            {
                "command": "python3 examples/control_plane/quota-scheduler-registry-route-smoke.py",
                "tier": "default",
                "reason": "guards ACK writes on the registry/runtime route that emitted the hint",
            },
            {
                "command": "python3 examples/control_plane/monitor-scheduler-contract-smoke.py",
                "tier": "default",
                "reason": "guards multi-monitor cadence selection and current-agent lane independence",
            },
        ],
    },
    {
        "id": "agent-facing-cli-output-budget",
        "title": "Agent-facing CLI output qualification",
        "quality_risk": "high",
        "purpose": (
            "Run exact stdout budgets and fail-closed command classification when "
            "recurring agent-facing CLI surfaces or their qualification contract change."
        ),
        "catalog_families": [
            "Work Routing",
            "State And Boundary",
            "Planning Governance",
        ],
        "trigger_hints": (
            "agent-facing cli",
            "cli output budget",
            "cli output qualification",
            "loopx/cli.py",
            "loopx/help_surface.py",
            "loopx/cli_commands/",
            "loopx/project_prompt.py",
            "loopx/quota.py",
            "loopx/status.py",
            "loopx/control_plane/effect_program.py",
            "loopx/review_packet.py",
            "loopx/heartbeat_prompt.py",
            "loopx/todos.py",
            "loopx/run_history.py",
            "loopx/evidence_log.py",
            "loopx/control_plane/testing/cli_output_budget.py",
            "tests/control_plane/test_cli_output_budget.py",
            "tests/control_plane/test_cli_output_differential.py",
            "loopx/control_plane/testing/cli_output_differential.py",
            "loopx/control_plane/testing/cli_output_semantics.py",
            "examples/control_plane/cli-output-probe-runner.py",
            "examples/control_plane/cli-output-base-head-differential-smoke.py",
            "examples/control_plane/cli-output-budget-regression-smoke.py",
            ".github/workflows/python-tests.yml",
            "docs/reference/contracts/interface-budget-contract.md",
        ),
        "checks": [
            {
                "command": "python3 examples/control_plane/cli-output-budget-regression-smoke.py",
                "tier": "default",
                "reason": (
                    "invokes the real CLI across declared JSON/Markdown surfaces, modes, "
                    "fixture scales, semantic anchors, command classifications, and a "
                    "same-fixture base/head structural and growth differential"
                ),
            },
            {
                "command": "python3 examples/control_plane/hot-path-interface-budget-smoke.py",
                "tier": "deep",
                "reason": "pairs emitted stdout qualification with compact in-memory hot-path budgets",
            },
        ],
    },
)
