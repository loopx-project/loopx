"""Product-entry, runtime, connector, and package canary domain profiles."""

from __future__ import annotations

from collections.abc import Mapping

from .package_profiles import PACKAGE_QUALIFICATION_PROFILES


WORKFLOW_DOMAIN_PROFILES: tuple[Mapping[str, object], ...] = (
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
            "examples/issue-fix-reviewer-notification-drain-postwrite-smoke.py",
            "loopx/capabilities/issue_fix/reviewer_notification_drain.py",
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
                "command": "python3 examples/issue-fix-reviewer-notification-drain-postwrite-smoke.py",
                "tier": "default",
                "reason": "guards restart-safe drain writeback, unverified-send suppression, and semantic notification deduplication",
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
            "/loopx-global-lifecycle",
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
                "command": "python3 examples/codex-cli-bootstrap-message-smoke.py",
                "tier": "default",
                "reason": "checks Codex CLI TUI bootstrap message and scoped LoopX command contract",
            },
            {
                "command": "python3 examples/claude-goalmode-lifecycle-smoke.py",
                "tier": "default",
                "reason": "checks the Claude Code goal-mode loop lifecycle without production actions",
            },
            {
                "command": "python3 examples/cli-worker-bridge-command-modularization-smoke.py",
                "tier": "default",
                "reason": "checks the worker bridge install and active-user observation contracts without private runtime material",
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
