from __future__ import annotations

from typing import Any

from .command_templates import build_issue_fix_pr_lifecycle_command


ISSUE_FIX_CATALOG_ENTRY: dict[str, Any] = {
    "id": "issue-fix",
    "origin": "builtin",
    "visibility": "public",
    "provider_id": "loopx-core",
    "documentation": {
        "source_root": "loopx/capabilities/issue_fix",
        "site_root": "capabilities/issue-fix",
        "canonical": "README.md",
    },
    "title": "Repo issue-fix loop",
    "status": "active-preview",
    "default_enabled": False,
    "real_world_anchor": "open-source issue/PR solver",
    "user_value": (
        "Turn a public GitHub issue or PR signal into a caller-approved "
        "local issue branch with validation evidence and a PR-review packet."
    ),
    "entry_command": "loopx issue-fix workflow-plan --url <github-issue-url> --format json",
    "commands": [
        {
            "command": "loopx content-ops issue-fix-metadata-preview --url <github-issue-url> --fetch-metadata --format json",
            "purpose": "Fetch body-free public GitHub issue/PR metadata.",
            "write_boundary": "read-only external metadata; no issue comment, PR, or todo write",
        },
        {
            "command": "loopx value-connectors github-public-probe --url <github-issue-or-pr-url> --fetch-metadata --format json",
            "purpose": "Use the compatibility CLI to fetch the issue-fix-owned public GitHub probe packet.",
            "write_boundary": "public metadata read only; no issue bodies, comments, PRs, account changes, or writes",
        },
        {
            "command": "loopx value-connectors github-reply-monitor --issue-url <github-issue-or-pr-url> --after-comment-url <github-issue-comment-url> --fetch-metadata --format json",
            "purpose": "Use the compatibility CLI to detect public maintainer replies through the issue-fix provider.",
            "write_boundary": "public comment metadata read only; no comment bodies, thread bump, or external write",
        },
        {
            "command": "loopx content-ops issue-fix-intake --format json",
            "purpose": "Project public issue metadata into an issue-fix intake packet.",
            "write_boundary": "fixture-only; no external read or write",
        },
        {
            "command": "loopx issue-fix workflow-plan --url <github-issue-url> --repo-path <repo> --repository-context-json <context.json> --format json",
            "purpose": "Compose metadata, repository context, intake, feasibility, validation labels, and PR review readiness blockers.",
            "write_boundary": "preview-only; no todo write, repo execution, external comment, PR creation, merge, or publish",
        },
        {
            "command": "loopx issue-fix feasibility --url <github-issue-url> --reproduction-status <state> --scope-class <scope> --repository-context-json <context.json> --goal-id <goal-id> --format json",
            "purpose": "Select one route and persist its compact repository evidence basis with feasibility domain state.",
            "write_boundary": "writes compact project-local domain state with goal or ledger context; no raw issue/comment/log capture or external write",
        },
        {
            "command": "loopx issue-fix promote-discovered-issue --goal-id <goal-id> --project <repo> --promotion-json <promotion.json> --execute --format json",
            "purpose": "Create or reuse one canonical public issue for a reproducible discovered defect and replace its local placeholder.",
            "write_boundary": "active publish authority only; bounded issue create/reuse and optional PR closing-reference verification plus compact issue-fix domain-state reconciliation; no merge or production action",
        },
        {
            "command": "loopx issue-fix reviewer-plan --repo-path <repo> --repo <owner/repo> --base-ref <base-ref> --execute --format json",
            "purpose": "Rank explainable reviewer candidates from CODEOWNERS and changed-path/module history.",
            "write_boundary": "approved local repo read only; no external review request",
        },
        {
            "command": "loopx issue-fix reviewer-request --url <github-pr-url> --repo-path <repo> --base-ref <base-ref> [--notification-sinks-json <local-private.json>] --execute --format json",
            "purpose": "Under reviewer-notification authority, exclude the live PR author, establish canonical GitHub coverage, and optionally deliver the same reviewer through verified project-dedicated sinks.",
            "write_boundary": "one formal GitHub review request, or one reviewer-tagging comment only after confirmed permission denial; optional configured secondary sends consume local-private identity/destination data without copying it; no arbitrary comment, push, merge, or publish",
        },
        {
            "command": "loopx issue-fix reviewer-notification-drain --goal-id <goal-id> --project <project> --execute --format json",
            "purpose": "Drain one bounded review-required state bucket after live PR verification while preserving one PR per group message.",
            "write_boundary": "verified configured secondary sends plus compact receipt or stale-queue state writeback; no per-PR continuous monitor, arbitrary comment, push, merge, or publish",
        },
        {
            "command": build_issue_fix_pr_lifecycle_command(
                cli_bin="loopx",
                goal_id="<goal-id>",
                agent_id="<agent-id>",
                project="<repo>",
            ),
            "purpose": "Project public PR lifecycle state and reconcile its grouped monitor, successor, user gate, or no-follow-up transition.",
            "write_boundary": "reads compact public PR metadata and writes compact project-local domain state plus generic LoopX todos; no external comment, PR creation, merge, raw logs, or body/comment capture",
        },
        {
            "command": "loopx issue-fix outcome --goal-id <goal-id> --repo <owner/repo> --issue-ref <issue-ref> --pr-ref <pr-ref> --format json",
            "purpose": "Compose one operator-facing issue status/output card from existing feasibility, repository context, optional delivery evidence, and PR lifecycle state.",
            "write_boundary": "read-only derived projection; writes no source ledger and performs no external action",
        },
        {
            "command": "loopx issue-fix acceptance-fixture --format json",
            "purpose": "Prove the failure-before/fix-after acceptance loop on a deterministic fixture.",
            "write_boundary": "temporary local fixture only",
        },
        {
            "command": "loopx issue-fix repo-branch-fixture --format json",
            "purpose": "Exercise the same loop through a temporary git issue branch.",
            "write_boundary": "temporary local git fixture only",
        },
        {
            "command": "loopx issue-fix caller-repo-branch --repo-path <repo> --validation-command <cmd> --execute --format json",
            "purpose": "Create or claim a caller-approved local issue branch and run caller-declared validation.",
            "write_boundary": "approved local repo only; no external comment, PR creation, merge, or publish",
        },
    ],
    "implemented_protocols": [
        {
            "schema_version": "github_public_channel_probe_packet_v0",
            "module": "loopx.capabilities.issue_fix.github_public",
            "doc": "docs/reference/protocols/value-connector-plan-v0.md",
        },
        {
            "schema_version": "github_public_reply_monitor_packet_v0",
            "module": "loopx.capabilities.issue_fix.github_public",
            "doc": "docs/reference/protocols/value-connector-plan-v0.md",
        },
        {
            "schema_version": "github_issue_metadata_preview_v0",
            "module": "loopx.capabilities.issue_fix.metadata_preview",
            "doc": "docs/reference/protocols/content-ops-surface-v0.md",
        },
        {
            "schema_version": "content_ops_issue_fix_metadata_preview_packet_v0",
            "module": "loopx.capabilities.issue_fix.intake_surface",
            "doc": "docs/reference/protocols/content-ops-surface-v0.md",
        },
        {
            "schema_version": "content_ops_issue_fix_intake_packet_v0",
            "module": "loopx.capabilities.issue_fix.intake_surface",
            "doc": "docs/reference/protocols/content-ops-surface-v0.md",
        },
        {
            "schema_version": "issue_fix_intake_v0",
            "module": "loopx.capabilities.issue_fix.intake_surface",
            "doc": "docs/reference/protocols/content-ops-surface-v0.md",
        },
        {
            "schema_version": "issue_fix_workflow_plan_packet_v0",
            "module": "loopx.capabilities.issue_fix.workflow_plan",
            "doc": "loopx/capabilities/issue_fix/docs/protocols/issue-fix-workflow-contract-v0.md",
        },
        {
            "schema_version": "issue_fix_repository_context_v0",
            "module": "loopx.capabilities.issue_fix.repository_context",
            "doc": "loopx/capabilities/issue_fix/docs/protocols/issue-fix-workflow-contract-v0.md",
        },
        {
            "schema_version": "issue_fix_feasibility_v0",
            "module": "loopx.capabilities.issue_fix.feasibility",
            "doc": "loopx/capabilities/issue_fix/docs/protocols/issue-fix-workflow-contract-v0.md",
        },
        {
            "schema_version": "issue_fix_discovered_issue_promotion_v0",
            "module": "loopx.capabilities.issue_fix.discovered_issue_promotion",
            "doc": "loopx/capabilities/issue_fix/docs/protocols/issue-fix-discovered-issue-promotion-v0.md",
        },
        {
            "schema_version": "issue_fix_pr_lifecycle_monitor_v0",
            "module": "loopx.capabilities.issue_fix.pr_lifecycle",
            "doc": "loopx/capabilities/issue_fix/docs/protocols/issue-fix-workflow-contract-v0.md",
        },
        {
            "schema_version": "issue_fix_maintainer_correction_input_v0",
            "module": "loopx.capabilities.issue_fix.pr_lifecycle",
            "doc": "loopx/capabilities/issue_fix/README.md",
        },
        {
            "schema_version": "issue_fix_reviewer_recommendation_v0",
            "module": "loopx.capabilities.issue_fix.reviewer_recommendation",
            "doc": "loopx/capabilities/issue_fix/docs/protocols/issue-fix-reviewer-recommendation-v0.md",
        },
        {
            "schema_version": "issue_fix_reviewer_request_v0",
            "module": "loopx.capabilities.issue_fix.reviewer_request",
            "doc": "loopx/capabilities/issue_fix/docs/protocols/issue-fix-reviewer-request-v0.md",
        },
        {
            "schema_version": "issue_fix_reviewer_notification_sinks_result_v0",
            "module": "loopx.capabilities.issue_fix.reviewer_notification",
            "doc": "loopx/capabilities/issue_fix/docs/protocols/issue-fix-reviewer-notification-sinks-v0.md",
        },
        {
            "schema_version": "issue_fix_reviewer_notification_drain_v0",
            "module": "loopx.capabilities.issue_fix.reviewer_notification_drain",
            "doc": "loopx/capabilities/issue_fix/docs/protocols/issue-fix-reviewer-notification-sinks-v0.md",
        },
        {
            "schema_version": "issue_fix_outcome_projection_v0",
            "module": "loopx.capabilities.issue_fix.outcome_projection",
            "doc": "loopx/capabilities/issue_fix/docs/protocols/issue-fix-workflow-contract-v0.md",
        },
        {
            "schema_version": "issue_fix_validated_outcome_memory_writeback_v0",
            "module": "loopx.capabilities.issue_fix.repository_memory_provider",
            "doc": "loopx/capabilities/issue_fix/README.md",
        },
        {
            "schema_version": "issue_fix_acceptance_loop_v0",
            "module": "loopx.capabilities.issue_fix.acceptance_loop",
            "doc": "loopx/capabilities/issue_fix/docs/protocols/issue-fix-acceptance-loop-v0.md",
        },
        {
            "schema_version": "issue_fix_validated_fix_artifact_v0",
            "module": "loopx.capabilities.issue_fix.acceptance_loop",
            "doc": "loopx/capabilities/issue_fix/docs/protocols/issue-fix-acceptance-loop-v0.md",
        },
        {
            "schema_version": "issue_fix_caller_repo_branch_packet_v0",
            "module": "loopx.capabilities.issue_fix.acceptance_loop",
            "doc": "loopx/capabilities/issue_fix/docs/protocols/issue-fix-acceptance-loop-v0.md",
        },
        {
            "schema_version": "issue_fix_pr_review_binding_v0",
            "module": "loopx.capabilities.issue_fix.pr_review_ack",
            "doc": "docs/project-agent-todo-contract.md",
        },
        {
            "schema_version": "issue_fix_pr_review_ack_receipt_v0",
            "module": "loopx.capabilities.issue_fix.pr_review_ack",
            "doc": "docs/project-agent-todo-contract.md",
        },
        {
            "schema_version": "issue_fix_pr_review_acked_reconciliation_v0",
            "module": "loopx.capabilities.issue_fix.pr_gate_reconcile",
            "doc": "docs/project-agent-todo-contract.md",
        },
    ],
    "smokes": [
        "python3 examples/value-connectors-github-public-probe-smoke.py",
        "python3 examples/issue-fix-workflow-plan-smoke.py",
        "python3 examples/issue-fix-repository-context-smoke.py",
        "python3 examples/issue-fix-feasibility-smoke.py",
        "python3 examples/issue-fix-discovered-issue-promotion-smoke.py",
        "python3 examples/issue-fix-pr-lifecycle-smoke.py",
        "python3 examples/issue-fix-pr-review-reconcile-smoke.py",
        "python3 examples/issue-fix-maintainer-correction-smoke.py",
        "python3 examples/issue-fix-outcome-projection-smoke.py",
        "python3 examples/issue-fix-validated-memory-writeback-smoke.py",
        "python3 examples/issue-fix-reviewer-recommendation-smoke.py",
        "python3 examples/issue-fix-reviewer-request-smoke.py",
        "python3 examples/issue-fix-json-input-boundary-smoke.py",
        "python3 examples/issue-fix-reviewer-notification-sink-smoke.py",
        "python3 examples/content-ops-issue-fix-metadata-preview-smoke.py",
        "python3 examples/content-ops-issue-fix-intake-smoke.py",
        "python3 examples/issue-fix-acceptance-loop-smoke.py",
    ],
    "docs": [
        "loopx/capabilities/issue_fix/README.md",
        "docs/reference/protocols/value-connector-plan-v0.md",
        "loopx/capabilities/issue_fix/docs/openviking-pilot-handoff.md",
        "loopx/capabilities/issue_fix/docs/protocols/issue-fix-workflow-contract-v0.md",
        "loopx/capabilities/issue_fix/docs/protocols/issue-fix-discovered-issue-promotion-v0.md",
        "loopx/capabilities/issue_fix/docs/protocols/issue-fix-reviewer-recommendation-v0.md",
        "loopx/capabilities/issue_fix/docs/protocols/issue-fix-reviewer-request-v0.md",
        "loopx/capabilities/issue_fix/docs/protocols/issue-fix-reviewer-notification-sinks-v0.md",
        "loopx/capabilities/issue_fix/docs/protocols/issue-fix-acceptance-loop-v0.md",
        "docs/reference/protocols/content-ops-surface-v0.md",
        "docs/reference/protocols/issue-fix-acceptance-loop-v0.md",
    ],
    "boundaries": [
        "GitHub issue body, comments, timeline, and raw provider payloads are gated and not copied.",
        "Caller repo mode reads and writes only the explicitly approved local git repo.",
        "Arbitrary external comments, PR creation, merge, publish, and destructive git remain separately gated; reviewer-request may use one reviewer-tagging comment only as a verified permission-denial fallback under the same narrow authority.",
        "Secondary reviewer sinks require an explicit project-dedicated identity and local-private destination/member mapping; no credential, raw roster, provider response, or private identifier enters public state.",
    ],
    "next_real_step": (
        "Exercise route selection and continuation on a public issue-fix pilot, "
        "while keeping external PR/comment actions explicit."
    ),
}
