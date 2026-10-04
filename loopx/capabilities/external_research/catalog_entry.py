from __future__ import annotations

from typing import Any


EXTERNAL_RESEARCH_CATALOG_ENTRY: dict[str, Any] = {
    "id": "external-evidence-research",
    "origin": "builtin",
    "visibility": "public",
    "provider_id": "loopx-core",
    "documentation": {
        "source_root": "loopx/capabilities/external_research",
        "site_root": "capabilities/external-evidence-research",
        "canonical": "README.md",
    },
    "title": "Auditable external evidence research",
    "status": "active-preview",
    "real_world_anchor": (
        "a decision-bound research question executed through either a host research "
        "method or a connector provider, with source-level provenance"
    ),
    "user_value": (
        "Discover method and connector inventory, select only a currently ready provider, "
        "execute explicitly selected public GitHub sources, bind a provider receipt to its exact plan, and admit or reject compact "
        "provenance without copying raw provider content."
    ),
    "next_real_step": (
        "run `loopx external-evidence plan --public-github --help`, then inspect the returned "
        "receipt before explicit admission and downstream ledger readback"
    ),
    "entry_command": "loopx external-evidence discover --help",
    "commands": [
        {
            "command": "loopx external-evidence discover --connector-registry",
            "purpose": "Project method and connector inventory without claiming readiness or execution.",
            "write_boundary": "read-only",
        },
        {
            "command": "loopx external-evidence plan ... --provider-inventory-json providers.json",
            "purpose": "Bind object, user activity, decision, and evidence kinds to one ready provider.",
            "write_boundary": "read-only",
        },
        {
            "command": "loopx external-evidence execute --plan-json plan.json --execute",
            "purpose": "Read explicit pinned public GitHub sources; parent admission remains separate.",
            "write_boundary": "anonymous public HTTPS reads; no raw persistence",
        },
        {
            "command": "loopx external-evidence readback --plan-json plan.json --receipt-json execution.json ...",
            "purpose": "Show the same source lineage, parent decision and actual downstream coverage.",
            "write_boundary": "read-only by default; --execute projects admitted sources to the existing research ledger",
        },
        {
            "command": "loopx external-evidence receipt --plan-json plan.json --receipt-json receipt.json",
            "purpose": (
                "Bind a caller-presented provider receipt to the exact plan without claiming "
                "provider execution, coverage, admission, or promotion."
            ),
            "write_boundary": "read-only typed reduction; provider owner performs execution",
        },
        {
            "command": "loopx external-evidence admit --plan-json plan.json --receipt-json receipt.json ...",
            "purpose": "Validate source provenance and record the parent admit/reject decision.",
            "write_boundary": "read-only typed reduction; caller owns durable writeback",
        },
        {
            "command": "loopx external-evidence retire --admission-json admission.json ...",
            "purpose": "Prove admitted sources reached a downstream projection before retirement.",
            "write_boundary": "read-only",
        },
    ],
    "implemented_protocols": [
        {
            "schema_version": "external_evidence_research_v0",
            "module": "loopx.control_plane.capabilities.external_evidence",
            "doc": "loopx/capabilities/external_research/README.md",
        }
    ],
    "smokes": [
        "node --no-warnings --experimental-strip-types --test tests/control_plane_ts/external_evidence_research.test.ts",
        "python -m pytest tests/capabilities/test_external_evidence_cli.py -q",
    ],
}
