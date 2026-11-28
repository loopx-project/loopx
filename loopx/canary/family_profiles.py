"""Catalog family canary checks and selector hints."""

from __future__ import annotations


FAMILY_CHECKS: dict[str, list[dict[str, str]]] = {
    "Work Routing": [
        {
            "command": "python3 examples/control_plane/quota-plan-smoke.py",
            "reason": "exercises quota should-run routing, fallback, and execution obligation projection",
        },
        {
            "command": "python3 examples/control_plane/quota-work-lane-policy-smoke.py",
            "reason": "checks advancement, monitor, and no-work lane policy selection",
        },
        {
            "command": "python3 examples/control_plane/monitor-scheduler-contract-smoke.py",
            "reason": "guards due-monitor routing without requiring real external polling",
        },
        {
            "command": "python3 examples/control_plane/heartbeat-prompt-smoke.py",
            "reason": "checks heartbeat prompt and scheduler guidance for controller loops",
        },
    ],
    "Human Decision": [
        {
            "command": "python3 examples/control_plane/todo-user-gate-scope-smoke.py",
            "reason": "checks scoped user-gate projection instead of global prose gates",
        },
        {
            "command": "python3 examples/project/operator-gate-resume-contract-smoke.py",
            "reason": "exercises operator gate preview and resume behavior",
        },
        {
            "command": "python3 examples/reward-gate-direct-write-contract-smoke.py",
            "reason": "guards reward/gate write boundaries before agent continuation",
        },
        {
            "command": "python3 examples/control_plane/quota-agent-scoped-user-gate-smoke.py",
            "reason": "checks that agent-scoped user gates do not block unrelated lanes",
        },
    ],
    "State And Boundary": [
        {
            "command": "python3 examples/control_plane/todo-contract-smoke.py",
            "reason": "checks todo metadata shape consumed by status and quota",
        },
        {
            "command": "python3 examples/control_plane/todo-readmodel-boundary-smoke.py",
            "reason": "guards status wrapper parity for todo read-model extraction",
        },
        {
            "command": "python3 examples/control_plane/active-state-structured-projection-smoke.py",
            "reason": "guards active-state structured projection from Markdown drift",
        },
        {
            "command": "python3 examples/control_plane/task-graph-projection-fixture-smoke.py",
            "reason": "checks task graph projection and lineage without private sources",
        },
        {
            "command": "python3 examples/control_plane/check-public-boundary-smoke.py",
            "reason": "guards public/private boundary scanning for touched files",
        },
    ],
    "Evidence Lifecycle": [
        {
            "command": "python3 examples/benchmark-candidate-source-boundary-smoke.py",
            "reason": "guards raw/private candidate source exclusion",
        },
        {
            "command": "python3 examples/benchmark-artifact-path-filter-smoke.py",
            "reason": "guards raw/private artifact path exclusion",
        },
        {
            "command": "python3 examples/content-ops-public-handle-observation-smoke.py",
            "reason": "checks public-handle evidence observation without private connector reads",
        },
    ],
    "Planning Governance": [
        {
            "command": "python3 examples/autonomous-replan-obligation-smoke.py",
            "reason": "guards stalled-turn autonomous replan obligations",
        },
        {
            "command": "python3 examples/control_plane/monitor-poll-writeback-smoke.py",
            "reason": "checks monitor poll writeback and no-spend replan triggers",
        },
        {
            "command": "python3 examples/control_plane/refresh-state-write-correctness-smoke.py",
            "reason": "checks refresh-state writeback correctness and projection updates",
        },
        {
            "command": "python3 examples/dreaming-dry-run-proposal-smoke.py",
            "reason": "checks planning proposal preview remains dry-run and machine-visible",
        },
    ],
}


FAMILY_SELECTOR_HINTS: dict[str, tuple[str, ...]] = {
    "Work Routing": (
        "quota",
        "should-run",
        "status",
        "review-packet",
        "heartbeat",
        "scheduler",
        "work-lane",
        "monitor",
        "handoff",
        "loopx/quota.py",
        "loopx/status.py",
        "loopx/review_packet.py",
        "loopx/heartbeat_prompt.py",
    ),
    "Human Decision": (
        "user todo",
        "operator-gate",
        "reward",
        "decision-scope",
        "deferred",
        "gate",
        "loopx/operator_gate.py",
        "loopx/control_plane/todos/decision_scope.py",
        "loopx/feedback.py",
    ),
    "State And Boundary": (
        "active state",
        "todo",
        "task graph",
        "authority",
        "boundary",
        "connector",
        "public/private",
        "loopx/todos.py",
        "loopx/control_plane/todos/contract.py",
        "loopx/status.py",
        "loopx/state_projection.py",
        "loopx/boundary_authority.py",
        "loopx/authority.py",
    ),
    "Evidence Lifecycle": (
        "benchmark",
        "evidence",
        "ledger",
        "artifact",
        "public handle",
        "ci",
        "content-ops",
        "worker_bridge",
        "loopx/benchmark",
        "loopx/worker_bridge.py",
        "loopx/capabilities/content_ops",
    ),
    "Planning Governance": (
        "replan",
        "repair",
        "cadence",
        "dreaming",
        "plan-to-todo",
        "refresh-state",
        "monitor-poll",
        "loopx/dreaming.py",
        "loopx/state_refresh.py",
        "loopx/long_task_cadence.py",
    ),
}
