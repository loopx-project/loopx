from __future__ import annotations

from typing import Any

PR_REVIEW_CATALOG_ENTRY: dict[str, Any] = {
    "id": "pull-request-review",
    "origin": "builtin",
    "visibility": "public",
    "provider_id": "loopx-core",
    "documentation": {
        "source_root": "loopx/capabilities/pr_review_queue",
        "site_root": "capabilities/pull-request-review",
        "canonical": "README.md",
        "aliases": [
            {
                "source": "README.md",
                "site": "reference/protocols/pr-review-command-v0.md",
            },
        ],
    },
    "title": "Autonomous pull-request review queue",
    "status": "active-preview",
    "default_enabled": False,
    "real_world_anchor": "maintainer review of a changing public GitHub PR queue",
    "user_value": (
        "Turn complete open-PR observations into policy-ranked exact-head review "
        "candidates without repeating handled work or granting external writes."
    ),
    "entry_command": (
        "loopx pr-review --repo <owner/repo> --state open "
        "--review-priority other-developers-first "
        "--autonomous-observation --format json"
    ),
    "commands": [
        {
            "command": "loopx pr-review --goal-id <goal-id> --repo <owner/repo> --check-merge-readiness NUMBER@HEAD_OID --format json",
            "purpose": "Fail closed on exact-head, approval-body, configured CI, thread, or merge-state drift immediately before merge.",
            "write_boundary": "reads live public GitHub state and writes one compact public-safe Goal observation; does not approve, merge, bypass policy, or grant merge authority",
        },
        {
            "command": "loopx pr-review --check-result <result.json> --packet <packet.json> --format json",
            "purpose": "Reject a declared approval inconsistent with the saved exact head and required evidence.",
            "write_boundary": "local read-only consistency check; does not verify evidence truth, remote freshness or grant publication/merge authority",
        },
        {
            "command": (
                "loopx pr-review --repo <owner/repo> --state open "
                "--review-priority other-developers-first "
                "--autonomous-observation --format json"
            ),
            "purpose": "Observe one complete public PR queue and emit at most one exact-head candidate.",
            "write_boundary": "public GitHub metadata read only; no review, comment, todo, push, or merge write",
        },
        {
            "command": (
                "loopx pr-review --repo <owner/repo> --state open "
                "--review-priority other-developers-first "
                "--autonomous-observation --previous-observation-json "
                "<previous.json> [--projected-exact-head NUMBER@HEAD_OID] "
                "[--handled-exact-head NUMBER@HEAD_OID] "
                "--format json"
            ),
            "purpose": "Classify a complete queue, acknowledge durable Todo projections, and advance from handled exact heads.",
            "write_boundary": "reads one caller-supplied local observation; emits a preview only and grants no external authority",
        },
        {
            "command": (
                "loopx pr-review --repo <owner/repo> --state open "
                "--review-priority other-developers-first "
                "--autonomous-observation --observation-state-file "
                "<ignored-local-checkpoint.json> "
                "[--projected-exact-head NUMBER@HEAD_OID] "
                "[--handled-exact-head NUMBER@HEAD_OID] --format json"
            ),
            "purpose": "Atomically continue policy-ranked review scheduling across Codex tasks.",
            "write_boundary": "writes only the explicit public-safe local checkpoint; grants no GitHub, Todo, push, or merge authority",
        },
    ],
    "implemented_protocols": [
        {
            "schema_version": "pull_request_merge_readiness_v0",
            "module": "loopx.capabilities.pr_review_queue.merge_readiness",
            "doc": "loopx/capabilities/pr_review_queue/README.md",
        },
        {
            "schema_version": "pull_request_merge_readiness_observation_v0",
            "module": "loopx.capabilities.pr_review_queue.readiness_observation",
            "doc": "loopx/capabilities/pr_review_queue/README.md",
        },
        {
            "schema_version": "pull_request_review_result_check_v0",
            "module": "loopx.capabilities.pr_review_queue.result_check",
            "doc": "loopx/capabilities/pr_review_queue/README.md",
        },
        {
            "schema_version": "pull_request_review_execution_contract_v2",
            "module": "loopx.capabilities.pr_review_queue.review_contract",
            "doc": "loopx/capabilities/pr_review_queue/README.md",
        },
        {
            "schema_version": "pull_request_review_plan_v1",
            "module": "loopx.capabilities.pr_review_queue.review_contract",
            "doc": "loopx/capabilities/pr_review_queue/README.md",
        },
        {
            "schema_version": "pull_request_review_result_v1",
            "module": "loopx.capabilities.pr_review_queue.review_contract",
            "doc": "loopx/capabilities/pr_review_queue/README.md",
        },
        {
            "schema_version": "pull_request_review_queue_observation_v1",
            "module": "loopx.capabilities.pr_review_queue.core",
            "doc": "loopx/capabilities/pr_review_queue/README.md",
        },
        {
            "schema_version": "pull_request_review_scheduling_policy_v1",
            "module": "loopx.capabilities.pr_review_queue.scheduling",
            "doc": "loopx/capabilities/pr_review_queue/README.md",
        },
        {
            "schema_version": "pull_request_review_machine_defaults_v0",
            "module": "loopx.capabilities.pr_review_queue.machine_defaults",
            "doc": "loopx/capabilities/pr_review_queue/README.md",
        },
        {
            "schema_version": "pull_request_review_monitor_checkpoint_v0",
            "module": "loopx.cli_commands.pr_review",
            "doc": "loopx/capabilities/pr_review_queue/README.md",
        },
        {
            "schema_version": "pull_request_review_conclusion_v0",
            "module": "loopx.pr_review",
            "doc": "loopx/capabilities/pr_review_queue/README.md",
        },
        {
            "schema_version": "pull_request_review_candidate_v0",
            "module": "loopx.capabilities.pr_review_queue.core",
            "doc": "loopx/capabilities/pr_review_queue/README.md",
        },
        {
            "schema_version": "pull_request_review_todo_preview_v0",
            "module": "loopx.capabilities.pr_review_queue.core",
            "doc": "loopx/capabilities/pr_review_queue/README.md",
        },
    ],
    "smokes": [
        "python -m pytest tests/capabilities/test_pr_review_queue.py -q",
        "python3 examples/pr-review-command-smoke.py",
    ],
    "docs": ["loopx/capabilities/pr_review_queue/README.md"],
    "boundaries": [
        "The shared execution contract owns review depth, evidence completeness, repository-reuse comparison, exact-head freshness, symbol-map, walkthrough, validation, failure, code-volume, change-proportionality, default-off isolation, and authority-semantics requirements; host skills only route and publish it.",
        "A queue is observed only when result_completeness.complete=true; partial or failed reads are not_observed and never count as unchanged.",
        "Fingerprints cover exact head, formal conclusion, next action, check state, draft state, and mergeability for every open PR.",
        "The default other-developers-first mode ranks actionable heads whose author differs from request.reviewer_login before the authenticated developer's own heads; --review-priority owner-first restores the owner-first order. Community response, 24-hour backlog, and remaining work retain their relative age/order within each selected mode, and one new head after REQUEST_CHANGES may use a bounded fast-feedback slot.",
        "Only rows with a non-null review_action_kind enter review_sequence and carry review plans, templates, or evidence commands; valid exact-head conclusions remain artifact-free inventory-only rows, and only --fresh-audit-exact-head NUMBER@HEAD_OID can explicitly reopen one.",
        "Todo prose, monitor notes, and one-off author filters are not scheduling authority.",
        "A complete exact-head conclusion requires the five Chinese sections, a state-aligned English verdict, and formal state or the verdict-specific titled author-owned fallback.",
        "Every merge must rerun the Goal-scoped merge-readiness gate for the reviewed exact head; the resulting compact observation suppresses only unchanged qualification work, and any head, base, review, CI, thread, draft, merge-state, or PR-state change reopens it.",
        "Readiness observations contain only public-safe material fingerprints and compact verdicts; they exclude review bodies, raw logs, credentials, private payloads, and local paths.",
        "One observation emits at most one exact-head advancement Todo preview; unchanged observations replay it until explicit durable Todo-projection ACK, then rotate across acknowledged exact heads.",
        "The capability reuses the existing pr-review GitHub scan and normalized packet; review bodies are inspected for format but never emitted or checkpointed.",
        "Candidate selection grants no GitHub review, comment, push, merge, quota, or Todo-write authority; those remain with their existing policy surfaces.",
    ],
    "next_real_step": (
        "Persist one observation fingerprint in a continuous-monitor Todo, "
        "observe a changed head, and materialize exactly one candidate through normal Todo authority."
    ),
}
