"""Capability-owned catalog entry for Decision Context."""

from __future__ import annotations

from typing import Any


def decision_context_configuration_descriptor() -> dict[str, Any]:
    return {
        "feature_id": "decision_context",
        "display_name": "Decision evidence recall",
        "availability": "local_artifact_preview",
        "effect": (
            "Inspect an existing private profile and recall advisory evidence in "
            "this page. No profile, cursor, settlement, or Core writes."
        ),
        "documentation": {
            "path": "loopx/capabilities/decision_context/README.md",
        },
    }


DECISION_CONTEXT_CATALOG_ENTRY: dict[str, Any] = {
    "id": "decision-context",
    "origin": "builtin",
    "visibility": "public",
    "provider_id": "loopx-core",
    "documentation": {
        "source_root": "loopx/capabilities/decision_context",
        "site_root": "capabilities/decision-context",
        "canonical": "README.md",
    },
    "title": "Goal-scoped decision evidence, review, and outcome contract",
    "status": "experimental",
    "default_enabled": False,
    "real_world_anchor": (
        "incremental authority rebase before long-running agent decisions"
    ),
    "user_value": (
        "Separate current evidence, advisory proposals, owner review, and "
        "verified outcomes while keeping providers replaceable and Core "
        "authority unchanged."
    ),
    "entry_command": (
        "loopx decision-context inspect-profile "
        "--goal-id <goal-id> --agent-id <agent-id> --format json"
    ),
    "commands": [
        {
            "command": "loopx decision-context architecture --format json",
            "purpose": "Render the default-off packet, source, provider, and lifecycle boundaries.",
            "write_boundary": "stateless contract output only; no provider, source, goal state, or external write",
        },
        {
            "command": (
                "loopx decision-context inspect-profile "
                "--goal-id <goal-id> --agent-id <agent-id> "
                "[--profile <private-local-profile>] --format json"
            ),
            "purpose": (
                "Resolve the default-off goal and agent route while projecting "
                "only public-safe provider availability."
            ),
            "write_boundary": (
                "reads an optional private local profile; never emits source "
                "locators, provider config, raw content, cursors, or credentials"
            ),
        },
        {
            "command": (
                "loopx decision-context source-manifest "
                "--goal-id <goal-id> --agent-id <agent-id> "
                "--profile <private-local-profile> --format json"
            ),
            "purpose": (
                "Project an enabled source profile into a public-safe manifest "
                "before any provider access."
            ),
            "write_boundary": (
                "read-only local projection; no provider access, cursor write, "
                "goal mutation, or external action"
            ),
        },
        {
            "command": (
                "loopx decision-context recall-context "
                "--goal-id <goal-id> --agent-id <agent-id> "
                "--profile <private-local-profile> "
                "--context-scope-ref <one-off-scope> --query <private-query> "
                "--query-summary <public-safe-summary> --format json"
            ),
            "purpose": (
                "Return one bounded local-private advisory recall without "
                "entering the evidence-settlement workflow."
            ),
            "write_boundary": (
                "read-only provider access; no profile mutation, authority-source "
                "scan, cursor access, settlement, Core mutation, or external write"
            ),
        },
        {
            "command": (
                "loopx decision-context prepare-evidence "
                "--goal-id <goal-id> --agent-id <agent-id> "
                "--profile <private-local-profile> --decision-id <decision-id> "
                "[--cursor-state <private-cursor-json>] "
                "[--source-id <on-demand-source>] --format json"
            ),
            "purpose": (
                "Run bounded scans and exact reads and emit a public-safe "
                "evidence preparation packet for domain rebase."
            ),
            "write_boundary": (
                "read-only provider access; changed-source cursors remain "
                "preserved until semantic rebase and validated writeback"
            ),
        },
        {
            "command": (
                "loopx decision-context prepare-review "
                "--goal-id <goal-id> --agent-id <agent-id> "
                "--profile <private-local-profile> --decision-id <decision-id> "
                "--rebase-json <private-rebase> "
                "--pending-settlement <private-pending> [--execute] --format json"
            ),
            "purpose": (
                "Validate a domain semantic rebase and optionally persist an "
                "unapplied private cross-turn settlement checkpoint."
            ),
            "write_boundary": (
                "execute writes only the selected private pending checkpoint; "
                "active cursors and Core state remain unchanged"
            ),
        },
        {
            "command": (
                "loopx decision-context settle-review "
                "--goal-id <goal-id> --agent-id <agent-id> "
                "--profile <private-local-profile> --cursor-state <private-cursors> "
                "--pending-settlement <private-pending> --event-log <event-log> "
                "[--proposal-json <proposal> --source-event-id <gate-event>] "
                "--actor-ref <actor> --reason-code <reason> --summary <summary> "
                "[--execute] --format json"
            ),
            "purpose": (
                "Settle approve, reject, defer, or explicit semantic no-change "
                "and CAS-commit consumed-source cursors."
            ),
            "write_boundary": (
                "execute appends one validated local rollout event and atomically "
                "updates private cursors; no external action or authority change"
            ),
        },
    ],
    "implemented_protocols": [
        {
            "schema_version": schema_version,
            "module": module,
            "doc": "docs/reference/protocols/decision-context-architecture-v0.md",
        }
        for schema_version, module in (
            (
                "decision_context_ephemeral_recall_v0",
                "loopx.capabilities.decision_context.assembler",
            ),
            (
                "decision_context_architecture_v0",
                "loopx.capabilities.decision_context.architecture",
            ),
            (
                "decision_evidence_packet_v0",
                "loopx.capabilities.decision_context.packets",
            ),
            (
                "decision_proposal_v0",
                "loopx.capabilities.decision_context.packets",
            ),
            (
                "decision_review_receipt_v0",
                "loopx.capabilities.decision_context.packets",
            ),
            (
                "decision_outcome_receipt_v0",
                "loopx.capabilities.decision_context.packets",
            ),
            (
                "decision_source_manifest_v0",
                "loopx.capabilities.decision_context.sources",
            ),
            (
                "decision_source_scan_receipt_v0",
                "loopx.capabilities.decision_context.sources",
            ),
            (
                "decision_context_profile_v0",
                "loopx.capabilities.decision_context.profile",
            ),
            (
                "decision_context_activation_status_v0",
                "loopx.capabilities.decision_context.profile",
            ),
            (
                "decision_cursor_commit_receipt_v0",
                "loopx.capabilities.decision_context.cursor_commit",
            ),
            (
                "decision_semantic_rebase_receipt_v0",
                "loopx.capabilities.decision_context.assembler",
            ),
            (
                "decision_context_pending_settlement_v0",
                "loopx.capabilities.decision_context.private_state",
            ),
            (
                "decision_review_settlement_v0",
                "loopx.capabilities.decision_context.review_settlement",
            ),
        )
    ],
    "smokes": ["python3 examples/decision-context-contract-smoke.py"],
    "docs": [
        "docs/reference/protocols/decision-context-architecture-v0.md",
        "docs/reference/protocols/decision-context-architecture-v0.zh-CN.md",
    ],
    "boundaries": [
        "The capability is default-off and cannot create action authority or mutate Core state.",
        "Private locators, cursors, raw content, provider payloads, tool output, and credentials stay outside public packets.",
        "Recall-context returns raw hits only in an explicit local-private transient packet and never persists its one-off scope.",
        "DecisionSourceProvider rebases current authority; ContextProvider supplies advisory recall only.",
        "Prepare-evidence is read-only; prepare-review writes only private unapplied state when explicitly executed.",
        "Cursor commit requires a canonical review receipt, exact gate or semantic no-change proof, lifecycle-event readback, private-state CAS, and atomic verified write; future outcome observation is separate.",
    ],
    "next_real_step": (
        "Exercise one private source profile through approve, reject, defer, and "
        "quiet no-change settlement, then observe later outcomes."
    ),
}
