from __future__ import annotations

from typing import Any

from .contract import REQUEST_SCHEMA


SEMANTIC_PREFERENCE_CATALOG_ENTRY: dict[str, Any] = {
    "id": "semantic-preference",
    "origin": "builtin",
    "visibility": "public",
    "provider_id": "loopx-core",
    "documentation": {
        "source_root": "loopx/capabilities/semantic_preference",
        "site_root": "capabilities/semantic-preference",
        "canonical": "README.md",
    },
    "title": "Explicit Agent preferences and optional semantic recall",
    "status": "active-preview",
    "real_world_anchor": "provider-neutral preference recall before domain work",
    "user_value": (
        "Maintain explicit revocable Agent preferences and recall provider-owned "
        "experiences with separate source, lifecycle and application contracts."
    ),
    "entry_command": "loopx semantic-preference recall --config <ignored-config.json> --surface <module.surface> --format json",
    "commands": [
        {
            "command": "loopx semantic-preference agent read --goal-id <goal> --agent-id <agent> --format json",
            "purpose": "Read the exact current owner-local Agent preferences, including retirement and expiry markers.",
            "write_boundary": "read-only private context; not action authority or public projection",
        },
        {
            "command": "loopx semantic-preference agent remember --goal-id <goal> --agent-id <agent> --key <subject> --statement <preference> --source-ref <message> --source-quote <quote> --expected-revision <revision> --operation-id <id> --execute",
            "purpose": "Commit a source-backed explicit user preference or correction with conditional revision and same-operation replay.",
            "write_boundary": "owner-local Agent context only; no Goal/Todo/grant mutation or external delivery",
        },
        {
            "command": "loopx semantic-preference doctor --config <ignored-config.json> --execute --format json",
            "purpose": "Check a configured provider entry point and optional read-only probe, then return explicit install/config guidance when unavailable.",
            "write_boundary": "read-only discovery; never installs packages, starts services, changes config, or writes credentials",
        },
        {
            "command": "loopx semantic-preference recall --config <ignored-config.json> --surface <module.surface> --execute --format json",
            "purpose": "Send one bounded recall request to a configured command_json_v0 provider.",
            "write_boundary": "provider read only; LoopX does not persist recalled semantic content",
        },
        {
            "command": "loopx semantic-preference receipt --surface <module.surface> --application-id <id> --outcome applied --format json",
            "purpose": "Build a compact receipt with hashed preference references for existing evidence/state writeback.",
            "write_boundary": "stateless output only; no provider, file, or external write",
        },
        {
            "command": "loopx semantic-preference maintenance-receipt --trigger explicit_feedback --outcome verified --corpus-id <id> --format json",
            "purpose": "Build a compact receipt after a provider-owned corpus maintenance decision and readback closure.",
            "write_boundary": "stateless output only; scope references are hashed and semantic content is excluded",
        },
    ],
    "implemented_protocols": [
        {
            "schema_version": "semantic_preference_hook_config_v0",
            "module": "loopx.capabilities.semantic_preference.contract",
            "doc": "loopx/capabilities/semantic_preference/README.md",
        },
        {
            "schema_version": REQUEST_SCHEMA,
            "module": "loopx.capabilities.semantic_preference.contract",
            "doc": "loopx/capabilities/semantic_preference/README.md",
        },
        {
            "schema_version": "semantic_preference_provider_doctor_v0",
            "module": "loopx.capabilities.semantic_preference.contract",
            "doc": "loopx/capabilities/semantic_preference/README.md",
        },
        {
            "schema_version": "semantic_preference_application_receipt_v0",
            "module": "loopx.capabilities.semantic_preference.contract",
            "doc": "loopx/capabilities/semantic_preference/README.md",
        },
        {
            "schema_version": "semantic_preference_maintenance_guidance_v0",
            "module": "loopx.capabilities.semantic_preference.contract",
            "doc": "loopx/capabilities/semantic_preference/README.md",
        },
        {
            "schema_version": "semantic_preference_maintenance_receipt_v0",
            "module": "loopx.capabilities.semantic_preference.contract",
            "doc": "loopx/capabilities/semantic_preference/README.md",
        },
    ],
    "smokes": [
        "python3 examples/semantic-preference-hook-smoke.py",
        "python3 examples/openviking-extension-runtime-smoke.py",
    ],
    "docs": ["loopx/capabilities/semantic_preference/README.md"],
    "boundaries": [
        "External recall is disabled until configured; explicit local preferences are activated by an owner-authorized remember commit.",
        "Surface ids and queries are domain-owned configuration; the runtime has no issue-fix branch.",
        "External recall does not persist provider content; explicit Agent preferences and their source quotes remain in private runtime history.",
        "Provider failures follow explicit fail-open/fail-closed policy and do not become user gates automatically.",
        "Provider setup remains guidance-only: package, service, config, and credential writes require an explicit operator action.",
    ],
    "next_real_step": (
        "Qualify correction-to-fresh-session adoption separately from episodic recall utility and cross-host context migration."
    ),
}
