"""Core control-plane, release, status, and CLI canary domain profiles."""

from __future__ import annotations

from collections.abc import Mapping

from .qualification_profiles import CONTROL_PLANE_QUALIFICATION_PROFILES
from .release_profiles import RELEASE_PROMOTION_PROFILE


CORE_DOMAIN_PROFILES: tuple[Mapping[str, object], ...] = (
    {
        "id": "pr-review-and-merge",
        "title": "PR review and merge workflow",
        "purpose": "Check public PR review packets, public-handle evidence, and merge-gate boundaries without approving or merging anything.",
        "catalog_families": ["Human Decision", "Evidence Lifecycle", "State And Boundary"],
        "trigger_hints": ("pr-review", "pull request", "github pr", "self-merge", "merge policy", "public pr metadata"),
        "checks": [
            {
                "command": "python3 examples/pr-review-command-smoke.py",
                "tier": "default",
                "reason": "checks the public-safe /loopx-pr-review packet and review-card contract",
            },
            {
                "command": "python3 examples/value-connectors-github-public-probe-smoke.py",
                "tier": "default",
                "reason": "guards public GitHub issue/PR metadata probes without raw body capture",
            },
            {
                "command": "python3 examples/control_plane/check-public-boundary-smoke.py",
                "tier": "deep",
                "reason": "runs broader public/private boundary checks when review artifacts are promoted",
            },
        ],
    },
    RELEASE_PROMOTION_PROFILE,
    {
        "id": "install-update",
        "title": "Install and update safety",
        "quality_risk": "high",
        "purpose": "Check local install, packaged install, update planning, rollback, and uninstall safety before install/update changes ship.",
        "catalog_families": ["State And Boundary", "Work Routing", "Planning Governance"],
        "trigger_hints": (
            "install",
            "installer",
            "packaged install",
            "install-local",
            "install-from-github",
            "loopx update",
            "self-update",
            "update command",
            "upgrade",
            "rollback",
            "uninstall",
            "scripts/install-local.sh",
            "scripts/install-from-github.sh",
            "loopx/self_update.py",
            "loopx/cli_commands/update",
        ),
        "checks": [
            {
                "command": "python3 examples/install-local-smoke.py",
                "tier": "default",
                "reason": "guards the local installer wrapper, skill installation, and install freshness reporting",
            },
            {
                "command": "python3 examples/release/local-install-promotion-boundary-smoke.py",
                "tier": "deep",
                "reason": "guards explicit default promotion while keeping dirty or non-main checkouts canary-only",
            },
            {
                "command": "python3 examples/codex-cli-packaged-install-smoke.py",
                "tier": "default",
                "reason": "checks packaged GitHub/archive install behavior in a temporary home",
            },
            {
                "command": "python3 examples/loopx-update-smoke.py",
                "tier": "default",
                "reason": "checks update planning, check-only behavior, rollback planning, and rollback execution fixtures",
            },
            {
                "command": "python3 examples/install-local-overwrite-smoke.py",
                "tier": "deep",
                "reason": "guards existing loopx executable overwrite and directory-conflict safety",
            },
            {
                "command": "python3 examples/protocol/rollback-packet-protocol-smoke.py",
                "tier": "deep",
                "reason": "validates the public rollback packet protocol and fixture boundary",
            },
            {
                "command": "python3 examples/project/project-uninstall-smoke.py",
                "tier": "deep",
                "reason": "samples project-local uninstall safety with isolated fixture registries",
            },
            {
                "command": "python3 examples/cli-worker-bridge-command-modularization-smoke.py",
                "tier": "deep",
                "reason": "checks generic worker bridge install contracts without exposing private runtime material",
            },
        ],
    },
    {
        "id": "control-plane-refactor",
        "title": "Control-plane refactor safety",
        "purpose": "Sample hot-path route, policy seam, and interface budget checks for quota/status refactors.",
        "catalog_families": ["Work Routing", "State And Boundary", "Planning Governance"],
        "trigger_hints": (
            "refactor",
            "quota.py",
            "status.py",
            "control-plane",
            "policy seam",
            "work-lane policy",
            "resume_when",
            "resume_ready",
            "handoff",
            "blocks_agent",
            "unblocks_todo_id",
            "successor",
            "monitor_target",
            "monitor_poll_writeback",
            "loopx/control_plane/scheduler/monitor_todo.py",
            "loopx/control_plane/scheduler/monitor_wait.py",
            "loopx/control_plane/scheduler/monitor_poll_writeback.py",
            "loopx/control_plane/quota/monitor_poll_commit.ts",
            "loopx/control_plane/quota/monitor_poll_codec.ts",
            "loopx/control_plane/quota/monitor_poll_admission.ts",
            "loopx/control_plane/quota/monitor_poll_provider.ts",
            "loopx/control_plane/scheduler/scheduler_hint.py",
            "loopx/control_plane/quota/stall_repair.py",
            "loopx/control_plane/scheduler/arbitration.py",
            "loopx/control_plane/todos/decision_scope.py",
            "loopx/control_plane/testing/decision_replay.py",
            "loopx/control_plane/work_items/interaction_contract.py",
            "loopx/control_plane/todos/handoff_gate.py",
            "loopx/control_plane/work_items/execution_obligation.py",
            "loopx/control_plane/work_items/goal_route_hint.py",
            "loopx/control_plane/work_items/delivery_history.py",
            "loopx/control_plane/work_items/delivery_history.ts",
            "loopx/control_plane/work_items/work_lane.py",
        ),
        "checks": [
            {
                "command": "python3 examples/control_plane/bounded-context-namespace-smoke.py",
                "tier": "default",
                "reason": "prevents repo-local code and smokes from depending on legacy projection shims after bounded-context moves",
            },
            {
                "command": "python3 examples/control_plane/control-plane-risk-characterization-smoke.py",
                "tier": "default",
                "reason": "characterizes shared control-plane routing, scheduler, and review-packet risks",
            },
            {
                "command": "python3 examples/control_plane/hot-path-interface-budget-smoke.py",
                "tier": "default",
                "reason": "keeps hot-path payload and module growth bounded",
            },
            {
                "command": "python3 examples/control_plane/quota-resume-gated-open-todo-smoke.py",
                "tier": "default",
                "reason": "guards resume_when-gated open todos from entering executable quota lanes early",
            },
            {
                "command": "python3 examples/control_plane/quota-cleared-blocker-successor-gate-smoke.py",
                "tier": "default",
                "reason": "guards cleared handoff gates waking the blocked agent through a concrete successor todo",
            },
            {
                "command": "python3 examples/control_plane/work-lane-contract-smoke.py",
                "tier": "deep",
                "reason": "covers broad work-lane policy interactions after larger refactors",
            },
        ],
    },
    {
        "id": "repo-architecture-budget",
        "title": "Control-plane maintainability ratchet",
        "purpose": (
            "Report control-plane dependency debt, oversized decision functions, and "
            "compatibility facades while rejecting unreviewed debt, magnitude growth, or "
            "stale exceptions. This intentionally replaces the repository-wide exact Python "
            "line budget with semantic control-plane and public-facade checks."
        ),
        "catalog_families": ["Planning Governance", "State And Boundary"],
        "trigger_hints": (
            "architecture",
            "architecture budget",
            "maintainability",
            "dependency debt",
            "decision function",
            "compatibility facade",
            "refactor",
            "quota.py",
            "status.py",
            "benchmark/",
            "benchmark_toolkit",
            "loopx/",
            "examples/",
            "scripts/",
        ),
        "checks": [
            {
                "command": "python3 examples/control_plane/control-plane-maintainability-ratchet-smoke.py",
                "tier": "default",
                "reason": (
                    "fails on unreviewed maintainability debt or an exception whose "
                    "underlying debt has been retired"
                ),
            },
            {
                "command": "python3 examples/semantic-vocabulary-drift-smoke.py",
                "tier": "default",
                "reason": (
                    "fails on an unregistered vocabulary value, a forked owner symbol, "
                    "unclassified project-registry I/O or stale classifications, "
                    "or a raised collision or retirement budget"
                ),
            },
        ],
    },
    *CONTROL_PLANE_QUALIFICATION_PROFILES,
    {
        "id": "status-read-path",
        "title": "Status read-path contract",
        "purpose": "Check scoped status reads, markdown rendering, and goal-channel status export before status/read-path changes ship.",
        "catalog_families": ["Work Routing", "State And Boundary"],
        "trigger_hints": (
            "read-path",
            "status --goal-id",
            "status data",
            "goal status",
            "runtime handoff",
            "handoff_readiness",
            "post_handoff_run",
            "loopx/status.py",
            "loopx/cli_commands/status",
            "loopx/control_plane/handoff/project_handoff.py",
        ),
        "checks": [
            {
                "command": "python3 examples/control_plane/status-goal-filter-smoke.py",
                "tier": "default",
                "reason": "guards scoped status projection and global default status behavior",
            },
            {
                "command": "python3 examples/control_plane/status-quota-review-packet-parity-smoke.py",
                "tier": "default",
                "reason": "guards scoped status, agent quota, review-packet handoff, and scheduler_hint parity on one fixture",
            },
            {
                "command": "python3 examples/control_plane/runtime-handoff-status-read-path-smoke.py",
                "tier": "default",
                "reason": (
                    "guards compact runtime handoff history through status, quota should-run, "
                    "and review-packet handoff-only read paths"
                ),
            },
            {
                "command": "python3 examples/control_plane/run-history-readmodel-smoke.py",
                "tier": "default",
                "reason": "guards status run-history parity, compact reward/gate privacy, and bounded history projection",
            },
            {
                "command": "python3 examples/control_plane/goal-channel-readmodel-smoke.py",
                "tier": "default",
                "reason": "guards status wrapper parity for goal-channel projection attachment",
            },
            {
                "command": "python3 examples/control_plane/status-markdown-smoke.py",
                "tier": "default",
                "reason": "checks operator-facing markdown status rendering",
            },
            {
                "command": "python3 examples/project/goal-channel-status-export-smoke.py",
                "tier": "deep",
                "reason": "guards goal-channel status export consumed by non-hot-path readers",
            },
            {
                "command": "python3 examples/control_plane/status-quota-perf-budget-smoke.py",
                "tier": "deep",
                "reason": "runs the broader status/quota performance budget sample when explicitly requested",
            },
        ],
    },
    {
        "id": "status-projection-cache",
        "title": "Status projection cache contract",
        "purpose": (
            "Check opt-in status projection cache freshness, expiration fallback, "
            "and quota next-action isolation before cache-path changes ship."
        ),
        "catalog_families": ["Work Routing", "State And Boundary"],
        "trigger_hints": (
            "projection-cache",
            "status_projection_cache",
            "use-projection-cache",
            "write-projection-cache",
            "status projection cache",
            "loopx/control_plane/runtime/status_projection_cache.py",
            "examples/control_plane/status-projection-cache-smoke.py",
        ),
        "checks": [
            {
                "command": "python3 examples/control_plane/status-projection-cache-smoke.py",
                "tier": "default",
                "reason": (
                    "guards opt-in status projection cache freshness, expiration fallback, "
                    "and quota next-action read-path isolation"
                ),
            },
        ],
    },
    {
        "id": "review-packet-read-path",
        "title": "Review-packet read-path contract",
        "purpose": "Check operator review packets, handoff-only payloads, and task-graph lineage before review-packet/read-path changes ship.",
        "catalog_families": ["Work Routing", "Human Decision", "State And Boundary"],
        "trigger_hints": (
            "review-packet",
            "review packet",
            "handoff-only",
            "project-agent handoff",
            "operator packet",
            "loopx/review_packet.py",
            "loopx/cli_commands/status",
            "docs/status-data-contract.md",
        ),
        "checks": [
            {
                "command": "python3 examples/control_plane/review-packet-cli-smoke.py",
                "tier": "default",
                "reason": "guards CLI-visible review-packet and handoff-only JSON contracts",
            },
            {
                "command": "python3 examples/control_plane/review-packet-smoke.py",
                "tier": "default",
                "reason": "checks dashboard/operator packet copy and public-safe handoff text",
            },
            {
                "command": "python3 examples/control_plane/task-graph-projection-fixture-smoke.py",
                "tier": "default",
                "reason": "guards task-graph lineage consumed by review packets without private sources",
            },
            {
                "command": "python3 examples/control_plane/control-plane-integrated-canary-smoke.py",
                "tier": "deep",
                "reason": "samples the bounded status -> quota -> review-packet Todo read path",
            },
            {
                "command": "python3 examples/control_plane/hot-path-interface-budget-smoke.py",
                "tier": "deep",
                "reason": "checks review-packet handoff interface budgets after hot-path changes",
            },
        ],
    },
    {
        "id": "cli-command-contract",
        "title": "CLI command module contract",
        "purpose": "Check command-module boundaries, ownership budgets, and compatibility for LoopX CLI refactors.",
        "catalog_families": ["State And Boundary", "Planning Governance"],
        "trigger_hints": (
            "loopx/cli.py",
            "loopx/cli_commands",
            "cli command",
            "command modularization",
            "command-module",
        ),
        "checks": [
            {
                "command": "python3 examples/cli-version-command-modularization-smoke.py",
                "tier": "default",
                "reason": "guards version output parity and both JSON format option placements",
            },
            {
                "command": "python3 examples/cli-control-plane-command-modularization-smoke.py",
                "tier": "default",
                "reason": "samples todo/quota command compatibility after CLI command refactors",
            },
        ],
    },
    {
        "id": "todo-lifecycle",
        "title": "Todo lifecycle contract",
        "purpose": "Check todo metadata, lifecycle transitions, and event/list projection before todo read/write CLI changes ship.",
        "catalog_families": ["State And Boundary", "Human Decision", "Planning Governance"],
        "trigger_hints": (
            "todo lifecycle",
            "todo claim",
            "todo list",
            "todo complete",
            "todo supersede",
            "todo metadata",
            "todo detail",
            "loopx/todos.py",
            "loopx/control_plane/todos/contract.py",
            "loopx/control_plane/todos/decision_scope.py",
            "loopx/cli_commands/todo",
        ),
        "checks": [
            {
                "command": "python3 examples/control_plane/todo-contract-smoke.py",
                "tier": "default",
                "reason": "guards the public todo status and metadata helper contract",
            },
            {
                "command": "python3 examples/control_plane/todo-cli-smoke.py",
                "tier": "default",
                "reason": "checks agent-facing todo add/update/list behavior on fixture state",
            },
            {
                "command": "python3 examples/control_plane/todo-lifecycle-cli-smoke.py",
                "tier": "default",
                "reason": "exercises claim, completion, successor, and handoff lifecycle transitions by todo_id",
            },
            {
                "command": "python3 examples/control_plane/todo-deferred-capacity-cli-smoke.py",
                "tier": "default",
                "reason": "guards deferred writes, fail-closed resume kinds, and runtime capacity resume routing",
            },
            {
                "command": "python3 examples/control_plane/todo-concurrent-write-lock-smoke.py",
                "tier": "deep",
                "reason": "samples lock behavior for concurrent todo writes",
            },
        ],
    },
    {
        "id": "monitor-scheduler",
        "title": "Monitor scheduler routing",
        "purpose": "Check monitor due/quiet/replan behavior without polling external targets by default.",
        "catalog_families": ["Work Routing", "Planning Governance"],
        "trigger_hints": ("monitor", "scheduler", "next_due_at", "monitor-poll", "continuous_monitor"),
        "checks": [
            {
                "command": "python3 examples/control_plane/monitor-scheduler-contract-smoke.py",
                "tier": "default",
                "reason": "checks due monitor selection, expiry, and priority behavior",
            },
            {
                "command": "python3 examples/control_plane/monitor-poll-writeback-smoke.py",
                "tier": "default",
                "reason": "guards no-spend monitor poll writeback and replan triggers",
            },
            {
                "command": "python3 examples/control_plane/heartbeat-quota-flow-smoke.py",
                "tier": "deep",
                "reason": "runs a broader heartbeat/quota control-flow sample",
            },
        ],
    },
    {
        "id": "state-write-correctness",
        "title": "State write correctness",
        "quality_risk": "high",
        "purpose": "Check local state writes, refresh-state, and todo write paths before touching writer internals.",
        "catalog_families": ["State And Boundary", "Planning Governance", "Human Decision"],
        "trigger_hints": ("state write", "refresh-state", "todo write", "idempotency", "revision", "lease"),
        "checks": [
            {
                "command": "python3 examples/control_plane/task-lease-runtime-smoke.py",
                "tier": "default",
                "reason": "guards shipped task_lease_v0 CLI/runtime ownership, TTL, conflict, transfer, and release behavior",
            },
            {
                "command": "python3 examples/control_plane/local-state-write-correctness-contract-smoke.py",
                "tier": "default",
                "reason": "checks local state write correctness contract fixtures",
            },
            {
                "command": "python3 examples/control_plane/refresh-state-write-correctness-smoke.py",
                "tier": "default",
                "reason": "guards refresh-state update behavior and projection writes",
            },
            {
                "command": "python3 examples/control_plane/todo-write-correctness-smoke.py",
                "tier": "default",
                "reason": "guards todo dry-run write correctness and shadow revision/lease validation",
            },
            {
                "command": "python3 examples/control_plane/todo-concurrent-write-lock-smoke.py",
                "tier": "deep",
                "reason": "samples lock behavior for concurrent todo writes",
            },
        ],
    },
)
