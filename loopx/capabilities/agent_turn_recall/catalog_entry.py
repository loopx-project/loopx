from __future__ import annotations

from importlib.util import find_spec
from typing import Any


AGENT_TURN_RECALL_SCHEMA_VERSION = "agent_turn_recall_v0"
AGENT_TURN_RECALL_SURFACE_ID = "agent_workflow.turn_admission"
RECALL_DISTRIBUTION = "loopx-capability-agent-turn-recall"
RECALL_INSTALL_HINT = f"Install the matching {RECALL_DISTRIBUTION} wheel in this Core environment."


def agent_turn_recall_installed(*, builtin_cli_present: bool | None = None) -> bool:
    """Observe the built-in deployment without loading its command or provider."""
    specs = [find_spec(f"{__package__}.{name}") for name in ("cli", "core")]
    if any(spec is None or spec.origin is None for spec in specs):
        return False
    if builtin_cli_present is None:
        builtin_cli_present = find_spec("loopx.cli") is not None
    if builtin_cli_present:
        return True

    from importlib.metadata import PackageNotFoundError, distribution
    from pathlib import Path

    try:
        recall = distribution(RECALL_DISTRIBUTION)
        if recall.version != distribution("loopx-core").version:
            return False
    except PackageNotFoundError:
        return False
    recorded = {Path(recall.locate_file(path)).resolve() for path in recall.files or ()}
    return all(spec and spec.origin and Path(spec.origin).resolve() in recorded for spec in specs)


AGENT_TURN_RECALL_CATALOG_ENTRY: dict[str, Any] = {
    "id": "agent-turn-recall",
    "origin": "builtin",
    "visibility": "public",
    "provider_id": "loopx-core",
    "documentation": {
        "source_root": "loopx/capabilities/agent_turn_recall",
        "site_root": "capabilities/agent-turn-recall",
        "canonical": "README.md",
    },
    "title": "Autonomous agent turn recall",
    "status": "active-preview",
    "default_enabled": False,
    "real_world_anchor": (
        "long-running autonomous agent turns without a fresh user prompt"
    ),
    "user_value": (
        "Recall temporary agent-scoped guidance from the current Goal, Todo, "
        "phase, recent outcome, and next intent before autonomous work begins."
    ),
    "entry_command": (
        "loopx agent-turn-recall --goal-id <goal-id> --agent-id <agent-id> "
        "--turn-instance-id <id> --quota-decision-json <quota.json> "
        "--execute --format json"
    ),
    "commands": [
        {
            "command": (
                "loopx agent-turn-recall --goal-id <goal-id> --agent-id "
                "<agent-id> --turn-instance-id <id> --quota-decision-json "
                "<quota.json> --format json"
            ),
            "purpose": "Preview the bounded situation, semantic query identity, and exact opt-in route without provider access.",
            "write_boundary": "read-only status/quota/config projection; no provider, receipt, quota, or external write",
        },
        {
            "command": (
                "loopx agent-turn-recall --goal-id <goal-id> --agent-id "
                "<agent-id> --turn-instance-id <id> --quota-decision-json "
                "<quota.json> --execute --format json"
            ),
            "purpose": "Recall exact-scope guidance and inject it into one private turn context.",
            "write_boundary": "provider read plus one ignored local same-turn receipt; no quota spend or external sink delivery",
        },
    ],
    "implemented_protocols": [
        {
            "schema_version": "agent_turn_situation_v0",
            "module": "loopx.capabilities.agent_turn_recall.core",
            "doc": "loopx/capabilities/agent_turn_recall/README.md",
        },
        {
            "schema_version": "agent_turn_recall_context_v0",
            "module": "loopx.capabilities.agent_turn_recall.core",
            "doc": "loopx/capabilities/agent_turn_recall/README.md",
        },
        {
            "schema_version": AGENT_TURN_RECALL_SCHEMA_VERSION,
            "module": "loopx.capabilities.agent_turn_recall.core",
            "doc": "loopx/capabilities/agent_turn_recall/README.md",
        },
    ],
    "smokes": [
        "python -m pytest tests/capabilities/test_agent_turn_recall.py -q"
    ],
    "docs": ["loopx/capabilities/agent_turn_recall/README.md"],
    "boundaries": [
        "The capability is default-off and reuses reward-memory corpus, scope, freshness, authority, and provider guards.",
        "The semantic query is built from durable turn state; a user prompt is neither required nor copied into the situation.",
        "Provider content is injected only into the private agent context, is suppressed from external sinks, and grants no new action authority.",
        "A matching receipt deduplicates retries inside one turn; a new turn instance recalls again even when the material situation is unchanged.",
        "Provider, application, and receipt failures are fail-open, are not user gates, and never spend quota.",
        "Project-specific habits, task ids, PR rules, provider URIs, and credentials stay in ignored local config or provider-owned memory.",
    ],
    "next_real_step": (
        "Promote the dogfooded host hook after repeated turn-admission "
        "readback confirms exact scope, expiry, and retry deduplication."
    ),
}
