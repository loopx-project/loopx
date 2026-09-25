from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .qualification_profiles import CONTROL_PLANE_QUALIFICATION_PROFILES
from .quality_surface_catalog import (
    build_quality_surface_catalog_audit as _build_quality_surface_catalog_audit,
)
from .package_profiles import PACKAGE_QUALIFICATION_PROFILES
from .release_profiles import RELEASE_PROMOTION_PROFILE


CANARY_PROFILE_SCHEMA_VERSION = "catalog_canary_profile_v0"
CANARY_DOMAIN_PROFILE_SCHEMA_VERSION = "catalog_canary_domain_profile_v0"
CANARY_PROFILES_SCHEMA_VERSION = "catalog_canary_profiles_v0"
CANARY_PLAN_SCHEMA_VERSION = "catalog_canary_plan_v0"
CANARY_COVERAGE_AUDIT_SCHEMA_VERSION = "catalog_canary_coverage_audit_v0"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = REPO_ROOT / "docs" / "concepts" / "interaction-pattern-catalog.md"


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


CURRENT_REPO_PROFILES: tuple[dict[str, Any], ...] = (
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
                "command": "python3 examples/worker-bridge-install-contract-smoke.py",
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
                    "a stale semantic inventory, or a raised collision or retirement budget"
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
                "command": "python3 examples/control_plane/run-compaction-readmodel-smoke.py",
                "tier": "default",
                "reason": "guards status wrapper parity for compact run read-model and summary projection attachment",
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
                "reason": "guards a low-risk command migration plus old version invocations",
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
            {
                "command": "python3 examples/control_plane/todo-detail-cold-path-contract-smoke.py",
                "tier": "deep",
                "reason": "checks the cold-path todo detail contract when detail surfaces are promoted",
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
    {
        "id": "product-entry-workflows",
        "title": "Product entry workflows",
        "purpose": (
            "Check user-facing product entry routes for issue-fix, content-ops, "
            "update notes, and cross-runtime demo surfaces without reading private sources."
        ),
        "catalog_families": ["Human Decision", "Evidence Lifecycle", "State And Boundary", "Work Routing"],
        "trigger_hints": (
            "product-entry",
            "product entry",
            "issue-fix",
            "issue fix",
            "content-ops",
            "content ops",
            "update-note",
            "update note",
            "update-notes",
            "update notes",
            "cross-runtime",
            "impl-review",
            "claude implements",
            "codex reviews",
            "README.md",
            "docs/product/use-cases/cross-runtime/cross-runtime-impl-review-demo.md",
            "loopx/capabilities/issue_fix",
            "loopx/capabilities/content_ops",
            "docs/update-notes",
            "loopx/cli_commands/issue_fix",
            "loopx/cli_commands/content_ops",
            "scripts/update_notes_release_job.py",
        ),
        "checks": [
            {
                "command": "python3 examples/issue-fix-workflow-contract-smoke.py",
                "tier": "default",
                "reason": "guards the public issue-fix workflow contract, gated metadata preview, and todo writeback preview",
            },
            {
                "command": "python3 examples/issue-fix-repository-context-smoke.py",
                "tier": "default",
                "reason": "guards provenance-aware repository context, advisory experts, and feasibility domain-state integration",
            },
            {
                "command": "python3 examples/issue-fix-feasibility-smoke.py",
                "tier": "default",
                "reason": "guards single-route feasibility decisions and compact issue_fix domain-state writeback",
            },
            {
                "command": "python3 examples/issue-fix-pr-lifecycle-smoke.py",
                "tier": "default",
                "reason": "guards PR lifecycle transitions, terminal-state precedence, and issue_fix domain-state writeback",
            },
            {
                "command": "python3 examples/content-ops-issue-fix-intake-smoke.py",
                "tier": "default",
                "reason": "checks content-ops issue-fix intake from public metadata without external reads or writes",
            },
            {
                "command": "python3 examples/public_entry/readme-demo-surface-smoke.py",
                "tier": "default",
                "reason": "guards the README and cross-runtime implement/review demo entry surface",
            },
            {
                "command": "python3 examples/update-notes-archive-smoke.py",
                "tier": "default",
                "reason": "checks public update-note archive, automation wiring, and private-boundary exclusions",
            },
            {
                "command": "python3 examples/issue-fix-workflow-e2e-smoke.py",
                "tier": "deep",
                "reason": "runs the fuller issue-fix metadata-to-plan-to-review-packet product path",
            },
            {
                "command": "python3 examples/issue-fix-acceptance-loop-smoke.py",
                "tier": "deep",
                "reason": "samples the heavier caller-repo branch and acceptance-loop fixture",
            },
            {
                "command": "python3 examples/content-ops-private-connector-gate-smoke.py",
                "tier": "deep",
                "reason": "checks private connector owner-gate projection for content operations",
            },
        ],
    },
    {
        "id": "issue-fix-reviewer-routing",
        "title": "Issue-fix reviewer routing and bilingual guide",
        "purpose": (
            "Check the bilingual issue-fix product entry and explainable local "
            "reviewer recommendation plus authority-gated verified notification "
            "contract."
        ),
        "catalog_families": ["Human Decision", "Evidence Lifecycle", "State And Boundary"],
        "trigger_hints": (
            "reviewer-plan",
            "reviewer recommendation",
            "issue_fix_reviewer_recommendation",
            "reviewer-request",
            "issue_fix_reviewer_request",
            "reviewer notification sink",
            "issue_fix_reviewer_notification",
            "loopx/capabilities/issue_fix/README",
            "loopx/capabilities/issue_fix/docs/protocols/issue-fix-reviewer-recommendation",
            "examples/issue-fix-capability-guide-smoke.py",
            "examples/issue-fix-reviewer-recommendation-smoke.py",
            "examples/issue-fix-reviewer-request-smoke.py",
            "examples/issue-fix-json-input-boundary-smoke.py",
            "examples/issue-fix-reviewer-notification-sink-smoke.py",
            "loopx/capabilities/issue_fix/cli.py",
            "loopx/capabilities/issue_fix/reviewer_recommendation.py",
            "loopx/capabilities/issue_fix/reviewer_request.py",
            "loopx/capabilities/issue_fix/reviewer_notification.py",
        ),
        "checks": [
            {
                "command": "python3 examples/issue-fix-json-input-boundary-smoke.py",
                "tier": "default",
                "reason": "guards bounded inline issue-fix JSON inputs and compact errors that never echo raw payloads",
            },
            {
                "command": "python3 examples/issue-fix-capability-guide-smoke.py",
                "tier": "default",
                "reason": "guards the bilingual issue-fix product entry, README links, reviewer protocol, and public-safe roadmap surface",
            },
            {
                "command": "python3 examples/issue-fix-reviewer-recommendation-smoke.py",
                "tier": "default",
                "reason": "guards explainable CODEOWNERS and base-revision changed-path/module-history reviewer recommendations",
            },
            {
                "command": "python3 examples/issue-fix-reviewer-request-smoke.py",
                "tier": "default",
                "reason": "guards author exclusion, formal-request-first notification, permission-only comment fallback, idempotency, blockers, and post-write verification",
            },
            {
                "command": "python3 examples/issue-fix-reviewer-notification-sink-smoke.py",
                "tier": "default",
                "reason": "guards project-dedicated secondary notification identity, local-private mapping, idempotent send/readback, concrete gates, and public-safety redaction",
            },
        ],
    },
    {
        "id": "issue-fix-outcome-visibility",
        "title": "Issue-fix status and output visibility",
        "purpose": (
            "Check the derived issue case read model and generic Kanban projection "
            "without adding a parallel issue-fix state machine."
        ),
        "catalog_families": ["Evidence Lifecycle", "State And Boundary", "Work Routing"],
        "trigger_hints": (
            "issue-fix outcome",
            "issue_fix_outcome",
            "outcome_projection",
            "examples/issue-fix-outcome-projection-smoke.py",
            "examples/issue-fix-validated-memory-writeback-smoke.py",
            "loopx/capabilities/issue_fix/outcome_projection.py",
            "loopx/capabilities/issue_fix/repository_memory_provider.py",
            "loopx/capabilities/context_providers/service_ownership.py",
            "loopx/extensions/lark/presentation/projection_rows.py",
            "projection_source_reconcile",
        ),
        "checks": [
            {
                "command": "python3 examples/issue-fix-outcome-projection-smoke.py",
                "tier": "default",
                "reason": "guards derived issue status/output cards, truthful delivery evidence, terminal visibility, and Lark projection reuse",
            },
            {
                "command": "python3 examples/issue-fix-validated-memory-writeback-smoke.py",
                "tier": "default",
                "reason": "guards explicit owner gating, validated distilled facts, idempotent provider writes, and unsafe-capture rejection",
            },
            {"command": "python3 examples/context-provider-service-ownership-smoke.py", "tier": "default", "reason": "guards persistent ownership, restart detection, append-attempt accounting, and public-safe receipts"},
            {"command": "python3 examples/lark-projection-source-reconcile-smoke.py",
             "tier": "default", "reason": "guards preview-first complete-source "
             "orphan and stale-mapping reconciliation"},
        ],
    },
    {
        "id": "cross-runtime-impl-review-demo",
        "title": "Cross-runtime implementation/review demo",
        "purpose": (
            "Check the Claude Code implementation + Codex review demo packet "
            "without writing state or launching either runtime."
        ),
        "catalog_families": [
            "Human Decision",
            "Work Routing",
            "Evidence Lifecycle",
            "State And Boundary",
        ],
        "trigger_hints": (
            "cross-runtime",
            "impl-review",
            "claude implements",
            "codex reviews",
            "claude-code-impl",
            "codex-review",
            "loopx demo impl-review",
            "cross_runtime_impl_review_demo_packet_v0",
            "docs/product/use-cases/cross-runtime/cross-runtime-impl-review-demo.md",
            "loopx/control_plane/handoff/cross_runtime_impl_review.py",
            "loopx/cli_commands/starter.py",
        ),
        "checks": [
            {
                "command": "python3 examples/cross-runtime-impl-review-demo-smoke.py",
                "tier": "default",
                "reason": "guards the dry-run packet, CLI route, role split, and no-write boundary",
            },
            {
                "command": "python3 examples/public_entry/readme-demo-surface-smoke.py",
                "tier": "default",
                "reason": "guards the public README and product note entry points for the demo",
            },
        ],
    },
    {
        "id": "host-command-entry",
        "title": "Host command entry and slash-command discovery",
        "purpose": (
            "Check slash-command discovery, Codex App host command routing, "
            "and global manager command entry surfaces without mutating project state."
        ),
        "catalog_families": ["Human Decision", "Work Routing", "State And Boundary"],
        "trigger_hints": (
            "slash-command",
            "slash command",
            "slash-commands",
            "/loopx-global-summary",
            "/loopx-global-gates",
            "/loopx-global-todos",
            "/loopx-global-risks",
            "global-manager",
            "global manager",
            "host command",
            "host command registry",
            "codex app host command",
            "docs/reference/protocols/codex-app-host-command-registry-v0.md",
            "docs/reference/protocols/global-manager-command-v0.md",
            "loopx/cli_commands/slash_commands.py",
            "loopx/cli_commands/summary_all.py",
        ),
        "checks": [
            {
                "command": "python3 examples/slash-command-catalog-smoke.py",
                "tier": "default",
                "reason": "guards public slash-command discovery and legacy alias visibility",
            },
            {
                "command": "python3 examples/codex-app-host-command-registry-smoke.py",
                "tier": "default",
                "reason": "guards Codex App host command routing and fail-closed slash-command help",
            },
            {
                "command": "python3 examples/project/global-manager-command-protocol-smoke.py",
                "tier": "default",
                "reason": "checks read-only global manager command protocol and aliases",
            },
        ],
    },
    {
        "id": "first-connect-contract",
        "title": "First-connect contract",
        "quality_risk": "high",
        "purpose": (
            "Check fresh connection state, absence of first-connect todos, "
            "state-gap detection, and domain-adapter routing ownership."
        ),
        "catalog_families": ["Work Routing", "State And Boundary"],
        "trigger_hints": (
            "new user onboarding",
            "first connect",
            "first-connect contract",
            "state projection gap",
            "start-goal",
            "loopx/bootstrap.py",
            "loopx/bootstrap_command_pack.py",
            "loopx/contract.py",
            "loopx/state_projection.py",
            "loopx/cli_commands/bootstrap_connect.py",
            "loopx/cli_commands/starter_bootstrap.py",
            "loopx/cli_commands/starter_bootstrap_registration.py",
        ),
        "checks": [
            {
                "command": "python3 examples/project/first-connect-contract-smoke.py",
                "tier": "default",
                "reason": (
                    "guards fresh connection state, first-connect todo absence, "
                    "state-gap warnings, and domain-adapter routing ownership"
                ),
            },
        ],
    },
    {
        "id": "runtime-connector-catalog",
        "title": "Runtime connector catalog",
        "purpose": (
            "Check Codex App heartbeat, Codex CLI TUI, Claude Code loop, "
            "and worker bridge connector contracts from the public runtime catalog."
        ),
        "catalog_families": ["Work Routing", "State And Boundary", "Planning Governance"],
        "trigger_hints": (
            "runtime connector",
            "runtime connector catalog",
            "runtime-connector-catalog",
            "docs/integrations/runtime-connector-catalog.md",
            "codex app heartbeat",
            "codex_app_heartbeat",
            "codex app ssh",
            "codex_app_ssh_goal",
            "codex cli tui",
            "codex_cli_tui",
            "claude code loop",
            "claude_code_loop",
            "shell worker",
            "http webhook",
            "worker bridge",
            "worker_bridge",
            "scheduler_hint",
            "scoped identity",
            "runtime loop",
            "host runtime",
            "computer use",
            "computer_use_runtime",
            "computer-use-runtime-v0",
        ),
        "checks": [
            {
                "command": "python3 examples/control_plane/heartbeat-prompt-smoke.py",
                "tier": "default",
                "reason": "guards Codex App heartbeat identity, scheduler hints, and no-spend cadence behavior",
            },
            {
                "command": "python3 examples/codex-cli-tui-bootstrap-smoke-bundle-smoke.py",
                "tier": "default",
                "reason": "checks Codex CLI TUI bootstrap bundle and scoped LoopX command contract",
            },
            {
                "command": "python3 examples/claude-goalmode-lifecycle-smoke.py",
                "tier": "default",
                "reason": "checks the Claude Code goal-mode loop lifecycle without production actions",
            },
            {
                "command": "python3 examples/worker-bridge-install-contract-smoke.py",
                "tier": "default",
                "reason": "checks generic worker bridge install/status contracts without private runtime material",
            },
            {
                "command": "python3 examples/computer-use-runtime-contract-smoke.py",
                "tier": "default",
                "reason": "checks the computer_use_runtime_v0 provider boundary: gate-before-write, unknown-modal handling, raw-evidence stripping, and rejection of provider-authored writeback",
            },
            {
                "command": "python3 examples/host-integration-surface-smoke.py",
                "tier": "deep",
                "reason": "samples the broader host integration surface when connector catalog changes are promoted",
            },
        ],
    },
    {
        "id": "frontstage-rollout",
        "title": "Frontstage rollout projection",
        "purpose": "Check public frontstage/showcase projection data before visual browser smokes.",
        "catalog_families": ["State And Boundary", "Evidence Lifecycle", "Human Decision"],
        "trigger_hints": ("frontstage", "showcase", "rollout", "dashboard", "visual"),
        "checks": [
            {
                "command": "python3 examples/frontstage-rollout-projections-fixture-smoke.py",
                "tier": "default",
                "reason": "checks reusable frontstage rollout projection fixtures",
            },
            {
                "command": "python3 examples/showcase-animation-prototype-smoke.py",
                "tier": "default",
                "reason": "guards showcase animation projection data without a browser",
            },
            {
                "command": "node examples/dashboard-frontstage-browser-smoke.mjs",
                "tier": "deep",
                "reason": "runs browser-level visual route validation when UI is promoted",
            },
        ],
    },
    {
        "id": "auto-research-demo",
        "title": "Auto-research demo and frontier route",
        "purpose": (
            "Check the minimal auto-research kernel and shared frontier projection; "
            "keep legacy visible/demo wrappers out of the default canary path."
        ),
        "catalog_families": ["Work Routing", "Evidence Lifecycle", "State And Boundary", "Human Decision"],
        "trigger_hints": (
            "auto-research",
            "auto research",
            "demo-supervisor",
            "demo e2e",
            "frontier",
            "visible launcher",
            "demo/auto_research",
        ),
        "checks": [
            {
                "command": "python3 examples/auto-research-minimal-kernel-smoke.py",
                "tier": "default",
                "reason": "checks the minimal evaluator-agnostic kernel and rejects public shortcut replay paths",
            },
            {
                "command": "python3 examples/decentralized-auto-research-frontier-smoke.py",
                "tier": "default",
                "reason": "checks shared frontier, evidence graph, and public boundary fixtures",
            },
            {
                "command": "python3 examples/auto-research-demo-supervisor-smoke.py",
                "tier": "deep",
                "reason": "samples the full dry-run supervisor packet and lane bootstrap contract",
            },
            {
                "command": "python3 examples/auto-research-rollout-readpath-smoke.py",
                "tier": "deep",
                "reason": "checks rollout event read-path projection into live evidence graphs",
            },
            {
                "command": "python3 examples/auto-research-live-evidence-capture-smoke.py",
                "tier": "deep",
                "reason": "checks compact live evidence capture fixtures for visible lanes",
            },
        ],
    },
    {
        "id": "explore-harness",
        "title": "Explore Harness configuration and runtime",
        "purpose": (
            "Check the deny-by-default goal boundary, planner agreement, resumable "
            "runtime state, and per-item failure isolation."
        ),
        "catalog_families": ["Work Routing", "State And Boundary", "Evidence Lifecycle"],
        "trigger_hints": (
            "explore harness",
            "explore-harness",
            "harness_runtime",
            "harness_checkpoint",
            "explore_harness",
            "loopx/capabilities/explore",
            "examples/explore-configure-goal-smoke.py",
            "examples/explore-harness-runtime-resume-smoke.py",
        ),
        "checks": [
            {
                "command": "python3 examples/explore-configure-goal-smoke.py",
                "tier": "default",
                "reason": "guards configure-goal opt-in and quota/planner boundary agreement",
            },
            {
                "command": "python3 examples/explore-harness-runtime-resume-smoke.py",
                "tier": "default",
                "reason": "guards validated resume state, novelty/variant restoration, and item failure isolation",
            },
            {
                "command": "python3 examples/explore-worker-plan-gate-smoke.py",
                "tier": "default",
                "reason": "guards profile pinning, analysis-only planning, and lane-width authority",
            },
            {
                "command": "python3 examples/explore-result-layer-smoke.py",
                "tier": "deep",
                "reason": "samples the wider Explore result and presentation contract",
            },
        ],
    },
    {
        "id": "peer-agent-runtime",
        "title": "Peer agent runtime and migration",
        "quality_risk": "high",
        "purpose": (
            "Check equal peer identity, deterministic task assignment, task-policy "
            "completion, symmetric workspace isolation, task-scoped coordination, "
            "and atomic registry/heartbeat migration."
        ),
        "catalog_families": ["Work Routing", "State And Boundary", "Evidence Lifecycle"],
        "trigger_hints": (
            "peer agent",
            "peer-agent",
            "peer_v1",
            "agent_model",
            "excluded_agents",
            "task_orchestration_contract",
            "agent_workspace_guard",
            "loopx/control_plane/quota/slot_accounting.py",
            "loopx/control_plane/agents",
            "loopx/control_plane/todos/completion_policy.py",
            "loopx/control_plane/quota/task_orchestration.py",
            "loopx/control_plane/goals/configure_goal_service.py",
            "loopx/heartbeat_prompt.py",
            "loopx/configure_goal.py",
            "examples/project/configure-goal-global-sync-smoke.py",
        ),
        "checks": [
            {
                "command": "python3 examples/project/configure-goal-global-sync-smoke.py",
                "tier": "default",
                "reason": "guards authoritative split-runtime configure-goal sync, explicit overrides, and exact readback",
            },
            {
                "command": "python3 examples/control_plane/peer-agent-runtime-v1-smoke.py",
                "tier": "default",
                "reason": "guards the complete peer identity, routing, completion, workspace, orchestration, migration, and host-loop contract",
            },
            {
                "command": "python3 examples/control_plane/agent-identity-readmodel-smoke.py",
                "tier": "deep",
                "reason": "guards rank-free peer identity and deterministic assignment",
            },
            {
                "command": "python3 examples/control_plane/quota-replan-decision-plane-smoke.py",
                "tier": "deep",
                "reason": "guards exactly-one peer ownership for unscoped replans",
            },
            {
                "command": "python3 examples/control_plane/todo-continuation-policy-smoke.py",
                "tier": "deep",
                "reason": "guards task-policy completion and independent review handoff",
            },
            {
                "command": "python3 examples/control_plane/peer-agent-migration-smoke.py",
                "tier": "deep",
                "reason": "guards atomic registry backup/cutover and peer heartbeat prompts",
            },
            {
                "command": "python3 examples/control_plane/peer-agent-workspace-guard-smoke.py",
                "tier": "deep",
                "reason": "guards symmetric write-aware worktree isolation in real git worktrees",
            },
            {
                "command": "python3 examples/control_plane/quota-spend-workspace-causality-smoke.py",
                "tier": "deep",
                "reason": "binds post-completion quota spend to the accountable delivery repository without weakening cross-repo isolation",
            },
            {
                "command": "python3 examples/control_plane/task-orchestration-smoke.py",
                "tier": "deep",
                "reason": "guards deterministic task-scoped coordination without durable leader authority",
            },
            {
                "command": "python3 examples/control_plane/agent-onboard-host-loop-activation-smoke.py",
                "tier": "deep",
                "reason": "guards rank-free host-loop identity selection and regeneration",
            },
        ],
    },
    {
        "id": "catalog-canary-contract",
        "title": "Catalog canary contract",
        "purpose": "Check catalog-to-canary planning, JSON actionability, shell-free no-write execution, and full/module smoke-suite selection.",
        "catalog_families": ["Planning Governance", "State And Boundary", "Work Routing"],
        "trigger_hints": (
            "catalog canary", "canary planner", "canary runner", "canary plan", "canary run",
            "smoke-suite", "run-smokes", "full smoke", "loopx/canary", "loopx/cli_commands/canary.py",
            "examples/canary/catalog", "examples/run-smokes.py",
        ),
        "checks": [
            {
                "command": "python3 examples/canary/catalog-planner-smoke.py",
                "tier": "default",
                "reason": "guards catalog coverage, selector routing, and actionable JSON plan commands",
            },
            {
                "command": "python3 examples/canary/catalog-run-e2e-smoke.py",
                "tier": "default",
                "reason": "guards shell-free no-write canary execution from the selected catalog plan",
            },
            {
                "command": "python3 examples/canary/smoke-suite-runner-smoke.py",
                "tier": "default",
                "reason": "guards full-public, module-filtered, and catalog-profile smoke-suite selection",
            },
            {
                "command": "python3 examples/canary/pytest-smoke-suite-facade-smoke.py",
                "tier": "deep",
                "reason": "guards optional pytest/JUnit reporting while keeping canary smoke-suite as source of truth",
            },
            {
                "command": "python3 examples/canary/quality-surface-catalog-smoke.py",
                "tier": "deep",
                "reason": "guards high-risk surface classification, independent semantic oracles, and explicit quality-layer gaps",
            },
        ],
    },
    *PACKAGE_QUALIFICATION_PROFILES,
    {
        "id": "benchmark-toolkit-boundary",
        "title": "Benchmark toolkit boundary",
        "purpose": "Check provider-neutral permission, artifact, candidate-source, and integrity boundaries without launching jobs.",
        "catalog_families": ["Evidence Lifecycle", "State And Boundary", "Work Routing"],
        "trigger_hints": (
            "benchmark",
            "benchmark toolkit",
            "benchmark research",
            "loopx/capabilities/benchmark_toolkit",
            "benchmark/",
            "examples/benchmark",
        ),
        "checks": [
            {
                "command": "python3 examples/benchmark-run-permission-policy-smoke.py",
                "tier": "default",
                "reason": "guards explicit execution and publication permission policy",
            },
            {
                "command": "python3 examples/benchmark-candidate-source-boundary-smoke.py",
                "tier": "default",
                "reason": "guards candidate selection against raw and private sources",
            },
            {
                "command": "python3 examples/benchmark-artifact-path-filter-smoke.py",
                "tier": "default",
                "reason": "guards raw/private benchmark artifact path exclusion",
            },
            {
                "command": "python3 -m pytest -q tests/capabilities/test_benchmark_toolkit.py",
                "tier": "deep",
                "reason": "checks integrity qualification and fail-closed public boundaries",
            },
        ],
    },
)


def _quality_audit_source_root(candidate: Path | None) -> Path | None:
    source_root = (candidate or REPO_ROOT).resolve()
    required_paths = (
        source_root / "pyproject.toml",
        source_root / "loopx" / "canary" / "quality_surface_catalog.py",
        source_root / "tests" / "canary" / "test_quality_surface_catalog.py",
    )
    return source_root if all(path.is_file() for path in required_paths) else None


def build_quality_surface_catalog_audit(
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Audit quality-layer ownership for every high-risk canary profile."""

    return _build_quality_surface_catalog_audit(
        CURRENT_REPO_PROFILES,
        repo_root=_quality_audit_source_root(repo_root),
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _catalog_profile_id(profile: dict[str, Any]) -> str:
    return _slug(str(profile.get("id") or profile.get("family") or ""))


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _parse_markdown_table(lines: list[str]) -> list[dict[str, str]]:
    rows = [_split_table_row(line) for line in lines if line.strip().startswith("|")]
    if len(rows) < 3:
        return []
    headers = rows[0]
    parsed: list[dict[str, str]] = []
    for row in rows[2:]:
        if len(row) != len(headers):
            continue
        parsed.append({headers[index]: value for index, value in enumerate(row)})
    return parsed


def _first_table_after(text: str, marker: str) -> list[dict[str, str]]:
    index = text.find(marker)
    if index < 0:
        return []
    table_lines: list[str] = []
    in_table = False
    for line in text[index:].splitlines():
        if line.strip().startswith("|"):
            table_lines.append(line)
            in_table = True
        elif in_table:
            break
    return _parse_markdown_table(table_lines)


def _pattern_ids(value: str) -> list[str]:
    return sorted(set(re.findall(r"\bIP-\d{3}\b", value)))


def _split_semicolon(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def _read_catalog(catalog_path: Path | None = None) -> tuple[Path, str]:
    path = (catalog_path or DEFAULT_CATALOG_PATH).expanduser()
    return path, path.read_text(encoding="utf-8")


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def build_catalog_canary_profiles(catalog_path: Path | None = None) -> dict[str, Any]:
    path, text = _read_catalog(catalog_path)
    archetype_rows = _first_table_after(text, "| Canary Archetype |")
    family_rows = _first_table_after(text, "| Family |")

    archetypes = []
    for row in archetype_rows:
        name = row.get("Canary Archetype", "")
        archetypes.append(
            {
                "id": _slug(name),
                "name": name,
                "primary_question": row.get("Primary Question", ""),
                "trigger_surface": row.get("Typical Trigger Surface", ""),
                "fixture_depth": row.get("Fixture Depth", ""),
                "cost_tier": row.get("Cost Tier", ""),
                "failure_meaning": row.get("Failure Usually Means", ""),
            }
        )

    profiles = []
    for row in family_rows:
        family = row.get("Family", "")
        archetype_names = _split_semicolon(row.get("Default Canary Archetypes", ""))
        profiles.append(
            {
                "schema_version": CANARY_PROFILE_SCHEMA_VERSION,
                "id": _slug(family),
                "family": family,
                "pattern_ids": _pattern_ids(row.get("P0/P1 Pattern Coverage", "")),
                "default_archetypes": [
                    {
                        "id": _slug(name),
                        "name": name,
                    }
                    for name in archetype_names
                ],
                "trigger_surfaces": row.get("Trigger Surfaces", ""),
                "minimum_useful_fixture": row.get("Minimum Useful Fixture", ""),
                "failure_meaning": row.get("Failure Meaning", ""),
                "candidate_checks": FAMILY_CHECKS.get(family, []),
            }
        )

    domain_profiles = []
    for profile in CURRENT_REPO_PROFILES:
        domain_profiles.append(
            {
                "schema_version": CANARY_DOMAIN_PROFILE_SCHEMA_VERSION,
                **profile,
                "checks": list(profile.get("checks", [])),
            }
        )

    return {
        "ok": bool(archetypes and profiles),
        "schema_version": CANARY_PROFILES_SCHEMA_VERSION,
        "source": _display_path(path),
        "dry_run": True,
        "executes_checks": False,
        "archetype_count": len(archetypes),
        "profile_count": len(profiles),
        "domain_profile_count": len(domain_profiles),
        "archetypes": archetypes,
        "profiles": profiles,
        "domain_profiles": domain_profiles,
    }


def _catalog_pattern_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _split_table_row(line)
        if len(cells) < 3:
            continue
        if cells[0] not in {"P0", "P1", "P2"} or not re.fullmatch(r"IP-\d{3}", cells[1]):
            continue
        rows.append(
            {
                "importance": cells[0],
                "pattern_id": cells[1],
                "name": cells[2],
            }
        )
    return rows


def _coverage_exception_rows(text: str) -> dict[str, dict[str, str]]:
    table = _first_table_after(text, "| Pattern ID | Canary Coverage Status |")
    exceptions: dict[str, dict[str, str]] = {}
    for row in table:
        pattern_id = next(iter(_pattern_ids(row.get("Pattern ID", ""))), "")
        if not pattern_id:
            continue
        status = _slug(row.get("Canary Coverage Status", ""))
        if status not in {"non-applicable", "not-applicable", "deferred"}:
            continue
        exceptions[pattern_id] = {
            "pattern_id": pattern_id,
            "status": "non-applicable" if status == "not-applicable" else status,
            "rationale": row.get("Rationale", ""),
            "owner": row.get("Owner", ""),
        }
    return exceptions


def _coverage_exception_is_valid(exception: dict[str, str]) -> bool:
    status = exception.get("status")
    rationale = bool(str(exception.get("rationale") or "").strip())
    owner = bool(str(exception.get("owner") or "").strip())
    if status == "non-applicable":
        return rationale
    if status == "deferred":
        return rationale and owner
    return False


def build_catalog_canary_coverage_audit(
    *,
    catalog_path: Path | None = None,
    priorities: list[str] | None = None,
) -> dict[str, Any]:
    path, text = _read_catalog(catalog_path)
    profile_packet = build_catalog_canary_profiles(catalog_path)
    required_priorities = tuple(priorities or ["P0", "P1"])
    pattern_rows = [
        row
        for row in _catalog_pattern_rows(text)
        if row.get("importance") in required_priorities
    ]
    profile_coverage: dict[str, list[str]] = {}
    for profile in profile_packet.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        profile_id = str(profile.get("id") or "")
        for pattern_id in profile.get("pattern_ids", []):
            if isinstance(pattern_id, str):
                profile_coverage.setdefault(pattern_id, []).append(profile_id)
    exceptions = _coverage_exception_rows(text)

    covered: list[dict[str, Any]] = []
    excepted: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    invalid_exceptions: list[dict[str, Any]] = []
    for row in pattern_rows:
        pattern_id = row["pattern_id"]
        profile_ids = profile_coverage.get(pattern_id, [])
        if profile_ids:
            covered.append({**row, "profile_ids": profile_ids})
            continue
        exception = exceptions.get(pattern_id)
        if exception and _coverage_exception_is_valid(exception):
            excepted.append({**row, **exception})
            continue
        if exception:
            invalid_exceptions.append({**row, **exception})
        else:
            missing.append(row)

    drift_count = len(missing) + len(invalid_exceptions)
    return {
        "ok": bool(profile_packet.get("ok")) and drift_count == 0,
        "schema_version": CANARY_COVERAGE_AUDIT_SCHEMA_VERSION,
        "source": _display_path(path),
        "dry_run": True,
        "executes_checks": False,
        "priorities": list(required_priorities),
        "required_pattern_count": len(pattern_rows),
        "covered_count": len(covered),
        "excepted_count": len(excepted),
        "missing_count": len(missing),
        "invalid_exception_count": len(invalid_exceptions),
        "drift_count": drift_count,
        "covered_patterns": covered,
        "excepted_patterns": excepted,
        "missing_patterns": missing,
        "invalid_exceptions": invalid_exceptions,
        "note": (
            "Coverage means each selected catalog pattern is listed in a canary "
            "family profile, or has an explicit non-applicable rationale or "
            "deferred owner in a catalog coverage exception table. This audit "
            "reports drift only; it does not force one giant canary or execute checks."
        ),
    }


def _selector_blob(changed_files: list[str], surfaces: list[str]) -> str:
    return "\n".join([*changed_files, *surfaces]).lower()


def _selection_reasons(profile: dict[str, Any], selector_blob: str) -> list[str]:
    family = str(profile.get("family") or "")
    family_lc = family.lower()
    reasons: list[str] = []
    if family_lc and family_lc in selector_blob:
        reasons.append(f"selector names catalog family `{family}`")
    for hint in FAMILY_SELECTOR_HINTS.get(family, ()):
        if hint.lower() in selector_blob:
            reasons.append(f"selector matches `{hint}`")
    trigger_surfaces = str(profile.get("trigger_surfaces") or "").lower()
    for token in re.findall(r"[a-z][a-z0-9_-]{2,}", selector_blob):
        if token in trigger_surfaces and f"trigger surface mentions `{token}`" not in reasons:
            reasons.append(f"trigger surface mentions `{token}`")
    return reasons


def _domain_selection_reasons(profile: dict[str, Any], selector_blob: str) -> list[str]:
    reasons: list[str] = []
    profile_id = str(profile.get("id") or "")
    title = str(profile.get("title") or "")
    if profile_id and profile_id in selector_blob:
        reasons.append(f"selector names profile `{profile_id}`")
    if title and title.lower() in selector_blob:
        reasons.append(f"selector names profile `{title}`")
    for hint in profile.get("trigger_hints", []):
        hint_text = str(hint or "").lower()
        if hint_text and hint_text in selector_blob:
            reasons.append(f"selector matches `{hint}`")
    for family in profile.get("catalog_families", []):
        family_text = str(family or "").lower()
        if family_text and family_text in selector_blob:
            reasons.append(f"selector matches family `{family}`")
    return reasons


def _domain_profile_with_checks(
    profile: dict[str, Any],
    *,
    include_deep_checks: bool,
    max_checks: int,
) -> dict[str, Any]:
    checks = [
        dict(check)
        for check in profile.get("checks", [])
        if include_deep_checks or check.get("tier") != "deep"
    ]
    copied = dict(profile)
    copied["checks"] = checks[: max(1, max_checks)]
    copied["deep_checks_available"] = any(
        isinstance(check, dict) and check.get("tier") == "deep"
        for check in profile.get("checks", [])
    )
    copied["deep_checks_included"] = bool(include_deep_checks)
    return copied


def flatten_catalog_canary_checks(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return selected canary checks in execution order."""

    checks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for profile in plan.get("domain_profiles", []):
        if not isinstance(profile, dict):
            continue
        for check in profile.get("checks", []):
            if not isinstance(check, dict):
                continue
            command = str(check.get("command") or "")
            if not command or command in seen:
                continue
            seen.add(command)
            checks.append(
                {
                    "source": "domain_profile",
                    "profile_id": profile.get("id"),
                    "profile_title": profile.get("title"),
                    "tier": check.get("tier") or "default",
                    **check,
                }
            )
    for profile in plan.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        for check in profile.get("candidate_checks", []):
            if not isinstance(check, dict):
                continue
            command = str(check.get("command") or "")
            if not command or command in seen:
                continue
            seen.add(command)
            checks.append(
                {
                    "source": "catalog_family",
                    "profile_id": profile.get("id"),
                    "profile_title": profile.get("family"),
                    "tier": check.get("tier") or "default",
                    **check,
                }
            )
    return checks


def build_catalog_canary_plan(
    *,
    catalog_path: Path | None = None,
    changed_files: list[str] | None = None,
    surfaces: list[str] | None = None,
    families: list[str] | None = None,
    profiles: list[str] | None = None,
    include_deep_checks: bool = False,
    max_checks_per_family: int = 3,
    max_checks_per_profile: int = 3,
) -> dict[str, Any]:
    packet = build_catalog_canary_profiles(catalog_path)
    changed_files = changed_files or []
    surfaces = surfaces or []
    requested_families = {_slug(family) for family in (families or []) if family.strip()}
    requested_profiles = {_slug(profile) for profile in (profiles or []) if profile.strip()}
    selector_blob = _selector_blob(changed_files, surfaces)
    max_checks = max(1, max_checks_per_family)
    max_profile_checks = max(1, max_checks_per_profile)
    catalog_profile_ids = {
        _catalog_profile_id(profile)
        for profile in packet["profiles"]
        if isinstance(profile, dict)
    }
    requested_catalog_profiles = requested_profiles & catalog_profile_ids
    requested_domain_profiles = requested_profiles - requested_catalog_profiles

    selected_profiles: list[dict[str, Any]] = []
    for profile in packet["profiles"]:
        profile_id = _catalog_profile_id(profile)
        if requested_domain_profiles and not requested_catalog_profiles and not requested_families and not selector_blob:
            continue
        reasons = _selection_reasons(profile, selector_blob)
        if requested_catalog_profiles and profile_id not in requested_catalog_profiles:
            continue
        if requested_families and _slug(str(profile.get("family") or "")) not in requested_families:
            continue
        if not requested_catalog_profiles and not requested_families and selector_blob and not reasons:
            continue
        profile_copy = dict(profile)
        profile_copy["candidate_checks"] = list(profile_copy.get("candidate_checks", []))[:max_checks]
        if requested_catalog_profiles and profile_id in requested_catalog_profiles:
            profile_copy["selection_reasons"] = reasons or [
                "selected because this catalog profile was explicitly requested",
            ]
        else:
            profile_copy["selection_reasons"] = reasons or [
                "selected because no narrower selector was provided",
            ]
        selected_profiles.append(profile_copy)

    selected_domain_profiles: list[dict[str, Any]] = []
    for profile in packet["domain_profiles"]:
        reasons = _domain_selection_reasons(profile, selector_blob)
        if requested_domain_profiles and _slug(str(profile.get("id") or "")) not in requested_domain_profiles:
            continue
        if requested_catalog_profiles and not requested_domain_profiles:
            continue
        if not requested_domain_profiles and selector_blob and not reasons:
            continue
        if not requested_domain_profiles and not selector_blob:
            continue
        profile_copy = _domain_profile_with_checks(
            profile,
            include_deep_checks=include_deep_checks,
            max_checks=max_profile_checks,
        )
        profile_copy["selection_reasons"] = reasons or [
            "selected because this profile was explicitly requested",
        ]
        selected_domain_profiles.append(profile_copy)

    payload = {
        "ok": True,
        "schema_version": CANARY_PLAN_SCHEMA_VERSION,
        "source": packet["source"],
        "dry_run": True,
        "executes_checks": False,
        "selection_inputs": {
            "changed_files": changed_files,
            "surfaces": surfaces,
            "families": families or [],
            "profiles": profiles or [],
            "include_deep_checks": include_deep_checks,
            "max_checks_per_family": max_checks,
            "max_checks_per_profile": max_profile_checks,
        },
        "profile_count": len(selected_profiles),
        "domain_profile_count": len(selected_domain_profiles),
        "profiles": selected_profiles,
        "domain_profiles": selected_domain_profiles,
        "note": (
            "This planner only selects and explains candidate canary checks. "
            "It does not execute smoke tests or create new runtime contracts. "
            "Plan from existing public runtime/status contracts first; when a "
            "new runtime contract seems necessary, stop at an owner-review "
            "necessity/risk packet before implementation."
        ),
    }
    suggested_checks = flatten_catalog_canary_checks(payload)
    payload["suggested_check_count"] = len(suggested_checks)
    payload["suggested_checks"] = suggested_checks
    payload["commands"] = [str(check.get("command") or "") for check in suggested_checks]
    return payload


def render_catalog_canary_profiles_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Catalog Canary Profiles",
        "",
        f"- source: `{payload.get('source')}`",
        f"- archetypes: `{payload.get('archetype_count')}`",
        f"- profiles: `{payload.get('profile_count')}`",
        "- dry_run: `true`",
        "- executes_checks: `false`",
        "",
    ]
    for profile in payload.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        lines.extend(
            [
                f"## {profile.get('family')}",
                f"- patterns: `{', '.join(profile.get('pattern_ids') or [])}`",
                "- archetypes: "
                + ", ".join(
                    f"`{item.get('name')}`"
                    for item in profile.get("default_archetypes", [])
                    if isinstance(item, dict)
                ),
                f"- trigger surfaces: {profile.get('trigger_surfaces')}",
                f"- minimum fixture: {profile.get('minimum_useful_fixture')}",
                f"- failure meaning: {profile.get('failure_meaning')}",
                "",
            ]
        )
    if payload.get("domain_profiles"):
        lines.extend(["## Current Repo Profiles", ""])
        for profile in payload.get("domain_profiles", []):
            if not isinstance(profile, dict):
                continue
            default_count = sum(
                1 for check in profile.get("checks", []) if isinstance(check, dict) and check.get("tier") != "deep"
            )
            deep_count = sum(
                1 for check in profile.get("checks", []) if isinstance(check, dict) and check.get("tier") == "deep"
            )
            lines.extend(
                [
                    f"### {profile.get('title')}",
                    f"- id: `{profile.get('id')}`",
                    f"- catalog families: `{', '.join(profile.get('catalog_families') or [])}`",
                    f"- purpose: {profile.get('purpose')}",
                    f"- default checks: `{default_count}`",
                    f"- deep checks: `{deep_count}`",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def render_catalog_canary_plan_markdown(payload: dict[str, Any]) -> str:
    inputs = payload.get("selection_inputs") if isinstance(payload.get("selection_inputs"), dict) else {}
    lines = [
        "# Catalog Canary Plan",
        "",
        f"- source: `{payload.get('source')}`",
        "- dry_run: `true`",
        "- executes_checks: `false`",
        f"- selected_profiles: `{payload.get('profile_count')}`",
        f"- selected_domain_profiles: `{payload.get('domain_profile_count')}`",
        f"- changed_files: `{', '.join(inputs.get('changed_files') or [])}`",
        f"- surfaces: `{', '.join(inputs.get('surfaces') or [])}`",
        f"- families: `{', '.join(inputs.get('families') or [])}`",
        f"- profiles: `{', '.join(inputs.get('profiles') or [])}`",
        f"- include_deep_checks: `{str(inputs.get('include_deep_checks')).lower()}`",
        "",
        str(payload.get("note") or ""),
        "",
    ]
    for profile in payload.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        lines.extend(
            [
                f"## {profile.get('family')}",
                f"- patterns: `{', '.join(profile.get('pattern_ids') or [])}`",
                "- selected because: "
                + "; ".join(str(reason) for reason in profile.get("selection_reasons", [])),
                "- archetypes: "
                + ", ".join(
                    f"`{item.get('name')}`"
                    for item in profile.get("default_archetypes", [])
                    if isinstance(item, dict)
                ),
                f"- minimum fixture: {profile.get('minimum_useful_fixture')}",
                f"- failure meaning: {profile.get('failure_meaning')}",
                "- suggested checks:",
            ]
        )
        for check in profile.get("candidate_checks", []):
            if isinstance(check, dict):
                lines.append(f"  - `{check.get('command')}` - {check.get('reason')}")
        lines.append("")
    for profile in payload.get("domain_profiles", []):
        if not isinstance(profile, dict):
            continue
        lines.extend(
            [
                f"## {profile.get('title')}",
                f"- id: `{profile.get('id')}`",
                f"- catalog families: `{', '.join(profile.get('catalog_families') or [])}`",
                "- selected because: "
                + "; ".join(str(reason) for reason in profile.get("selection_reasons", [])),
                f"- purpose: {profile.get('purpose')}",
                f"- deep checks available: `{str(profile.get('deep_checks_available')).lower()}`",
                f"- deep checks included: `{str(profile.get('deep_checks_included')).lower()}`",
                "- suggested checks:",
            ]
        )
        for check in profile.get("checks", []):
            if isinstance(check, dict):
                lines.append(
                    f"  - `{check.get('command')}` [{check.get('tier')}] - {check.get('reason')}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_catalog_canary_coverage_audit_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Catalog Canary Coverage Audit",
        "",
        f"- source: `{payload.get('source')}`",
        "- dry_run: `true`",
        "- executes_checks: `false`",
        f"- priorities: `{', '.join(payload.get('priorities') or [])}`",
        f"- required_patterns: `{payload.get('required_pattern_count')}`",
        f"- covered: `{payload.get('covered_count')}`",
        f"- excepted: `{payload.get('excepted_count')}`",
        f"- missing: `{payload.get('missing_count')}`",
        f"- invalid_exceptions: `{payload.get('invalid_exception_count')}`",
        f"- drift: `{payload.get('drift_count')}`",
        "",
        str(payload.get("note") or ""),
        "",
    ]
    if payload.get("missing_patterns"):
        lines.extend(["## Missing Coverage", ""])
        for row in payload.get("missing_patterns", []):
            if isinstance(row, dict):
                lines.append(
                    f"- `{row.get('pattern_id')}` {row.get('name')} ({row.get('importance')})"
                )
        lines.append("")
    if payload.get("invalid_exceptions"):
        lines.extend(["## Invalid Exceptions", ""])
        for row in payload.get("invalid_exceptions", []):
            if isinstance(row, dict):
                lines.append(
                    f"- `{row.get('pattern_id')}` {row.get('status')}: "
                    f"rationale={row.get('rationale')!r}; owner={row.get('owner')!r}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
