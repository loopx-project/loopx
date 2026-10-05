from __future__ import annotations

from typing import Any


EXPLORE_CATALOG_ENTRY: dict[str, Any] = {
    "id": "explore",
    "origin": "builtin",
    "visibility": "public",
    "provider_id": "loopx-core",
    "documentation": {
        "source_root": "loopx/capabilities/explore",
        "site_root": "capabilities/explore",
        "canonical": "README.md",
    },
    "title": "Explore Harness",
    "status": "active-preview",
    "real_world_anchor": (
        "goal-scoped questions, hypotheses, experiments, findings, and result projections"
    ),
    "user_value": (
        "Keep exploration outcomes queryable and attributable instead of leaving them "
        "only in chat or a presentation sink."
    ),
    "entry_command": "loopx explore summary --goal-id <goal-id> --format json",
    "commands": [
        {
            "command": "loopx configure-goal --goal-id <goal-id> --explore-mode planning",
            "purpose": "Preview evidence-and-planning mode; use evidence for graph only or off to disable.",
            "write_boundary": "preview only; --execute applies configuration without granting spawn authority",
        },
        {
            "command": "loopx explore turn-context --goal-id <goal-id> --agent-id <agent-id> --format json",
            "purpose": "Read the bounded context requested by the enabled turn-start hook.",
            "write_boundary": "read-only; no evidence, claim, lease, worker or quota writes",
        },
        {
            "command": "loopx explore finding --goal-id <goal-id> --title <finding> --evidence-ref <ref> --format json",
            "purpose": "Append one public-safe, attributable finding to the canonical result log.",
            "write_boundary": "goal-scoped local result log only; no sink or external write",
        },
        {
            "command": "loopx explore summary --goal-id <goal-id> --format json",
            "purpose": "Fold canonical result events into the bounded operator projection.",
            "write_boundary": "read-only projection over the goal result log",
        },
    ],
    "implemented_protocols": [
        {
            "schema_version": "loopx_explore_result_event_v0",
            "module": "loopx.capabilities.explore.result_log",
            "doc": "loopx/capabilities/explore/README.md",
        },
        {
            "schema_version": "loopx_explore_result_projection_v0",
            "module": "loopx.capabilities.explore.result_log",
            "doc": "loopx/capabilities/explore/README.md",
        },
    ],
    "smokes": [
        "python3 examples/explore-result-layer-smoke.py",
        "python3 examples/explore-todo-result-node-linkage-smoke.py",
    ],
    "docs": ["loopx/capabilities/explore/README.md"],
    "boundaries": [
        "Canonical result events are the source of truth; Lark and other sinks render projections only.",
        "Evidence refs must be public-safe relative refs or opaque ids, never credentials, raw private payloads, or local absolute paths.",
    ],
    "next_real_step": (
        "Register findings and terminal outcomes through the result log before refreshing any display sink."
    ),
}
