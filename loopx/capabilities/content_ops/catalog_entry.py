from __future__ import annotations

from typing import Any


CONTENT_OPS_CATALOG_ENTRY: dict[str, Any] = {
    "id": "content-ops",
    "origin": "builtin",
    "visibility": "public",
    "provider_id": "loopx-core",
    "documentation": {
        "source_root": "loopx/capabilities/content_ops",
        "site_root": "capabilities/content-ops",
        "canonical": "README.md",
    },
    "title": "Creator/content operations loop",
    "status": "active-preview",
    "real_world_anchor": "self-media operations and public/private source intake",
    "user_value": (
        "Collect public handles and approved private-connector metadata into "
        "reviewable source, angle, draft, feedback, and publish-gate packets."
    ),
    "entry_command": "loopx content-ops aggregate-packets --format json",
    "commands": [
        {
            "command": "loopx content-ops reference search --library-json <private-catalog.json> --query <topic> --structure <structure> --format json",
            "purpose": "Retrieve source credit, version, reuse boundary, styles and dated counters from an existing caller-owned catalog.",
            "write_boundary": "local-private metadata projection; no source access or store writes",
        },
        {
            "command": "loopx content-ops reference capture --library-json <private-catalog.json> --input-json <capture-request.json> --output-json <new-private-artifact.json> --format json",
            "purpose": "Prepare a source/style append or version-checked correction while preserving stable identity and legacy backing references.",
            "write_boundary": "new mode-0600 private artifact only; existing catalog overwrite is refused",
        },
        {
            "command": "loopx content-ops reference draft --library-json <private-catalog.json> --input-json <draft-request.json> --format json",
            "purpose": "Prepare an attributed structure outline using the caller's own facts and current source revision.",
            "write_boundary": "local-private outline only; no publishing or factual certification",
        },
        {
            "command": "loopx content-ops reference inventory --library-json <private-catalog.json> --goal-id <goal> --store-id <existing-store> --observed-at <iso> --format json",
            "purpose": "Inspect original catalog bytes through the existing material-lifecycle inventory owner, preserving unknown legacy lifecycle states.",
            "write_boundary": "read-only inventory; apply/rollback provider unavailable",
        },
        {
            "command": "loopx value-connectors source-map --connector social_browser_x --format json",
            "purpose": "Before X preparation, read the provider's bundled operating experience and optional memory initialization recipe.",
            "write_boundary": "packaged seed read only; no browser or memory-provider calls",
        },
        {
            "command": "loopx content-ops exploration-plan --format json",
            "purpose": "Plan source lanes before reading connector material.",
            "write_boundary": "fixture-only; no source read",
        },
        {
            "command": "loopx content-ops observe-public-handle --url <public-url> --source-item-id <id> --format json",
            "purpose": "Create a metadata-only public source item.",
            "write_boundary": "public HEAD-only metadata read unless --no-fetch is used",
        },
        {
            "command": "loopx content-ops project-private-connector-gate --format json",
            "purpose": "Represent a private connector as an owner gate before metadata intake.",
            "write_boundary": "no private connector read",
        },
        {
            "command": "loopx content-ops project-chatview-report --format json",
            "purpose": "Summarize approved ChatView connector counts without raw chat content.",
            "write_boundary": "compact counts only; no raw message text",
        },
        {
            "command": "loopx content-ops aggregate-packets --format json",
            "purpose": "Merge public source packets and private owner gates into a control-plane surface.",
            "write_boundary": "local packet aggregation only",
        },
        {
            "command": "loopx content-ops item-create --item-id <id> --item-kind <kind> --channel <channel> --content-digest <sha256> --content-ref <ref> --created-at <iso> --format json",
            "purpose": "Create one provider-neutral content item without draft bodies.",
            "write_boundary": "local item packet only; no external write",
        },
        {
            "command": "loopx content-ops item-transition --item-json <item.json> --event-json <event.json> --format json",
            "purpose": "Apply one content item lifecycle event and emit a compact receipt.",
            "write_boundary": "local item transition only; no external write",
        },
        {
            "command": "loopx content-ops queue-status --item-json <item.json> --format json",
            "purpose": "Project caller-owned content items into one read-only managed queue surface.",
            "write_boundary": "local queue projection only; no external read or write",
        },
        {
            "command": "loopx content-ops template-list --format json",
            "purpose": "List the built-in public-safe content layout template library.",
            "write_boundary": "packaged template read only; no external read or write",
        },
        {
            "command": "loopx content-ops layout-plan --item-id <id> --template-id <id> --page <page:role:subject> --generated-at <iso> --format json",
            "purpose": "Record typed page roles and a closing obligation before rendering.",
            "write_boundary": "local plan packet only; no draft body or external write",
        },
        {
            "command": "loopx content-ops layout-check --plan-json <plan.json> --measurement-json <measurement.json> --format json",
            "purpose": "Enforce template density, visual safety, required page roles, and the final-page role.",
            "write_boundary": "local deterministic check only; never grants publish authority",
        },
    ],
    "implemented_protocols": [
        {
            "schema_version": "content_ops_reference_v0",
            "module": "loopx.control_plane.capabilities.content_reference",
            "doc": "docs/reference/protocols/content-ops-reference-v0.md",
        },
        {
            "schema_version": "content_ops_reference_result_v0",
            "module": "loopx.control_plane.capabilities.content_reference",
            "doc": "docs/reference/protocols/content-ops-reference-v0.md",
        },
        {
            "schema_version": "content_ops_reference_material_inspection_v0",
            "module": "loopx.capabilities.content_ops.reference",
            "doc": "docs/reference/protocols/content-ops-reference-v0.md",
        },
        {
            "schema_version": "content_ops_surface_v0",
            "module": "loopx.capabilities.content_ops.surface",
            "doc": "docs/reference/protocols/content-ops-surface-v0.md",
        },
        {
            "schema_version": "source_item_v0",
            "module": "loopx.capabilities.content_ops.surface",
            "doc": "docs/reference/protocols/content-ops-surface-v0.md",
        },
        {
            "schema_version": "content_ops_private_connector_owner_gate_v0",
            "module": "loopx.capabilities.content_ops.surface",
            "doc": "docs/reference/protocols/content-ops-surface-v0.md",
        },
        {
            "schema_version": "content_ops_packet_aggregation_v0",
            "module": "loopx.capabilities.content_ops.surface",
            "doc": "docs/reference/protocols/content-ops-surface-v0.md",
        },
        {
            "schema_version": "content_ops_chatview_connector_report_v0",
            "module": "loopx.capabilities.content_ops.surface",
            "doc": "docs/reference/protocols/content-ops-surface-v0.md",
        },
        {
            "schema_version": "content_ops_social_browser_x_provider_v0",
            "module": "loopx.capabilities.content_ops.social_browser_x",
            "doc": "loopx/capabilities/content_ops/README.md",
        },
        {
            "schema_version": "content_ops_item_v0",
            "module": "loopx.capabilities.content_ops.item_lifecycle",
            "doc": "docs/reference/protocols/content-ops-item-lifecycle-v0.md",
        },
        {
            "schema_version": "content_ops_queue_projection_v0",
            "module": "loopx.capabilities.content_ops.item_lifecycle",
            "doc": "docs/reference/protocols/content-ops-queue-v0.md",
        },
        {
            "schema_version": "content_ops_layout_plan_v0",
            "module": "loopx.capabilities.content_ops.layout",
            "doc": "docs/reference/protocols/content-ops-layout-v0.md",
        },
        {
            "schema_version": "content_ops_layout_check_packet_v0",
            "module": "loopx.capabilities.content_ops.layout",
            "doc": "docs/reference/protocols/content-ops-layout-v0.md",
        },
    ],
    "smokes": [
        "python3 examples/content-ops-exploration-plan-smoke.py",
        "python3 examples/content-ops-public-handle-observation-smoke.py",
        "python3 examples/content-ops-private-connector-gate-smoke.py",
        "python3 examples/content-ops-chatview-report-smoke.py",
        "python3 examples/content-ops-packet-aggregation-smoke.py",
        "python3 examples/content-ops-queue-status-smoke.py",
        "python3 examples/content-ops-layout-library-smoke.py",
    ],
    "docs": [
        "docs/reference/protocols/content-ops-reference-v0.md",
        "loopx/capabilities/content_ops/README.md",
        "docs/reference/protocols/content-ops-surface-v0.md",
        "docs/reference/protocols/content-ops-item-lifecycle-v0.md",
        "docs/reference/protocols/content-ops-queue-v0.md",
        "docs/reference/protocols/content-ops-layout-v0.md",
    ],
    "boundaries": [
        "Reference artifacts are local-private; unknown legacy fields are preserved only in the prepared artifact, never promoted to public facts or source authority.",
        "Material Lifecycle remains default-off; reference discovery/import does not activate sources or qualify a write provider.",
        "Private connectors enter as owner gates or compact approved counts first.",
        "Raw chats, transcripts, auth material, logs, and local paths are not copied into public packets.",
        "Publish remains blocked until an explicit user decision.",
        "Queue projection is read-only and never stores draft bodies or provider credentials.",
        "Layout checks consume relative asset references and compact measurements, never draft bodies or local absolute paths.",
        "Layout acceptance is deterministic and never implies creator approval or publishing authority.",
    ],
    "next_real_step": (
        "Turn the aggregated surface into a small review/feed UI where a user "
        "can score source items, angles, and drafts."
    ),
}
