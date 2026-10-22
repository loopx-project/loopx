from __future__ import annotations

from typing import Any


MATERIAL_LIFECYCLE_CATALOG_ENTRY: dict[str, Any] = {
    "id": "material-lifecycle",
    "origin": "builtin",
    "visibility": "public",
    "provider_id": "loopx-core",
    "documentation": {
        "source_root": "loopx/capabilities/material_lifecycle",
        "site_root": "capabilities/material-lifecycle",
        "canonical": "README.md",
    },
    "title": "Backup-safe material inventory, ranked-entry rebuild, and rerank",
    "status": "experimental",
    "default_enabled": False,
    "real_world_anchor": (
        "large local candidate and archive stores that must remain "
        "recoverable while decisions change their short-list"
    ),
    "user_value": (
        "Preserve raw source authority and stable material references while "
        "making migration, archive transitions, structural entry rebuilds, "
        "and small reranks auditable."
    ),
    "entry_command": "loopx material-lifecycle architecture --format json",
    "workflow_skill": {
        "name": "loopx-material",
        "delivery": "project_managed_copy",
        "activation": "explicit_project_install_plus_goal_authority",
        "project_copy_required": True,
        "install_command": (
            "loopx project-skill install --project . --skill "
            "loopx-material --surface codex --execute"
        ),
    },
    "commands": [
        {
            "command": "loopx material-lifecycle architecture --format json",
            "purpose": "Render the default-off Stage-0 inventory, migration, lifecycle, and bounded-rerank boundaries.",
            "write_boundary": "stateless contract output only; no source store, backup, goal state, provider, or external write",
        }
    ],
    "implemented_protocols": [
        {
            "schema_version": "material_lifecycle_architecture_v0",
            "module": "loopx.capabilities.material_lifecycle.architecture",
            "doc": "docs/reference/protocols/material-lifecycle-architecture-v0.md",
        },
        {
            "schema_version": "material_store_inventory_v0",
            "module": "loopx.capabilities.material_lifecycle.inventory",
            "doc": "docs/reference/protocols/material-lifecycle-architecture-v0.md",
        },
        {
            "schema_version": "material_migration_plan_v0",
            "module": "loopx.capabilities.material_lifecycle.inventory",
            "doc": "docs/reference/protocols/material-lifecycle-architecture-v0.md",
        },
        {
            "schema_version": "material_lifecycle_receipt_v0",
            "module": "loopx.capabilities.material_lifecycle.lifecycle",
            "doc": "docs/reference/protocols/material-lifecycle-architecture-v0.md",
        },
        {
            "schema_version": "material_rerank_proposal_v0",
            "module": "loopx.capabilities.material_lifecycle.ranking",
            "doc": "docs/reference/protocols/material-lifecycle-architecture-v0.md",
        },
        {
            "schema_version": "material_rerank_apply_receipt_v0",
            "module": "loopx.capabilities.material_lifecycle.ranking",
            "doc": "docs/reference/protocols/material-lifecycle-architecture-v0.md",
        },
        {
            "schema_version": "material_ranked_entry_rebuild_plan_v0",
            "module": "loopx.capabilities.material_lifecycle.rebuild",
            "doc": "docs/reference/protocols/material-lifecycle-architecture-v0.md",
        },
        {
            "schema_version": "material_ranked_entry_rebuild_apply_receipt_v0",
            "module": "loopx.capabilities.material_lifecycle.rebuild",
            "doc": "docs/reference/protocols/material-lifecycle-architecture-v0.md",
        },
        {
            "schema_version": "material_ranked_state_repair_plan_v0",
            "module": "loopx.capabilities.material_lifecycle.ranked_state",
            "doc": "docs/reference/protocols/material-lifecycle-architecture-v0.md",
        },
        {
            "schema_version": "material_ranked_state_repair_apply_receipt_v0",
            "module": "loopx.capabilities.material_lifecycle.ranked_state",
            "doc": "docs/reference/protocols/material-lifecycle-architecture-v0.md",
        },
        {
            "schema_version": "material_lifecycle_ranking_apply_receipt_v0",
            "module": "loopx.capabilities.material_lifecycle.ranked_state",
            "doc": "docs/reference/protocols/material-lifecycle-architecture-v0.md",
        },
    ],
    "smokes": ["python3 examples/material-lifecycle-contract-smoke.py"],
    "docs": [
        "docs/reference/protocols/material-lifecycle-architecture-v0.md",
        "docs/reference/protocols/material-lifecycle-architecture-v0.zh-CN.md",
    ],
    "boundaries": [
        "The capability is default-off and cannot create action authority or mutate Core state.",
        "Raw material, private locations, provider payloads, credentials, and source-store formats stay outside public packets.",
        "Snapshot and verified backup precede dual-read reconciliation; cutover and rollback remain owner-gated.",
        "Decision Context may supply revisioned evidence, but Material Lifecycle owns candidate, archive, and rerank receipts.",
        "Oversized ranked entries are rebuilt into independently sortable entries; overflow cannot be hidden outside the ranked set.",
        "Derived ranked summaries are repaired without changing membership or order, and every resulting authority revision has one applied ranking-lineage receipt.",
        "Archiving a ranked material must remove only that material while preserving every remaining entry identity and relative order.",
        "LoopX ships the canonical loopx-material source, but discovery uses an explicit managed project copy rather than a global skill install.",
        "A project-local skill makes the workflow discoverable; it does not replace explicit goal-scoped Material Lifecycle authority.",
        "Concrete legacy adapters, exploration providers, source profiles, and material-store writes remain deferred to private dogfood.",
    ],
    "next_real_step": (
        "Dogfood one exact-read semantic rebuild through owner-gated apply "
        "while preserving complete source bytes and canonical records."
    ),
}
