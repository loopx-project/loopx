from __future__ import annotations

from typing import Any

from .contract import (
    ANTI_ENTROPY_CAPABILITY_ID,
    ANTI_ENTROPY_RECEIPT_SCHEMA_VERSION,
    ANTI_ENTROPY_RETIREMENT_BASIS_SCHEMA_VERSION,
)

_README = "loopx/capabilities/anti_entropy/README.md"

ANTI_ENTROPY_CATALOG_ENTRY: dict[str, Any] = {
    "id": ANTI_ENTROPY_CAPABILITY_ID,
    "origin": "builtin",
    "visibility": "public",
    "provider_id": "loopx-core",
    "documentation": {
        "source_root": "loopx/capabilities/anti_entropy",
        "site_root": "capabilities/anti-entropy-governance",
        "canonical": "README.md",
    },
    "title": "Anti-entropy and code retirement governance",
    "status": "experimental",
    "default_enabled": False,
    "real_world_anchor": (
        "provider-neutral AST-based delete-first verification and diff hygiene for agent refactoring"
    ),
    "user_value": (
        "Verify that declared retirement targets are cleanly deleted without "
        "re-introducing wrappers, zombie symbols, or ghost code."
    ),
    "entry_command": (
        "loopx anti-entropy inspect --goal-id <goal-id> --diff-file <diff.patch> "
        "--basis-json <json|path> --workspace <sources> --format json"
    ),
    "workflow_skill": {
        "name": "loopx-anti-entropy",
        "delivery": "turn_context_hook_and_shared_cli",
        "activation": "explicit_task_scoped_enablement",
        "project_copy_required": False,
    },
    "commands": [
        {
            "command": "loopx anti-entropy verify --goal-id <goal-id> --diff-file <diff.patch> --workspace <sources> --format json",
            "purpose": "Verify exact diff hygiene and delete-first compliance against a declared retirement basis.",
            "write_boundary": "read-only diff verification and receipt generation; no repository mutation",
        },
        {
            "command": "loopx anti-entropy inspect --goal-id <goal-id> --diff-file <diff.patch> --workspace <sources> --format json",
            "purpose": (
                "In-flight agent check with actionable feedback; rejections are "
                "progress facts, not final delivery failures."
            ),
            "write_boundary": "read-only diff verification; no receipt store writes",
        },
        {
            "command": "loopx capability show anti-entropy-governance --format json",
            "purpose": "Inspect anti-entropy capability metadata, commands, and protocols.",
            "write_boundary": "read-only metadata projection",
        },
    ],
    "agent_tools": [
        {
            "name": "anti_entropy_inspect",
            "descriptor": "loopx.capabilities.anti_entropy.feedback.ANTI_ENTROPY_INSPECT_TOOL_DESCRIPTOR",
            "entrypoint": "loopx.capabilities.anti_entropy.agent_tool.anti_entropy_inspect",
            "purpose": (
                "Agent-callable inspection returning actionable findings as "
                "progress facts plus an immutable receipt."
            ),
            "write_boundary": "read-only verification; no host state mutation",
        },
    ],
    "implemented_protocols": [
        {
            "schema_version": ANTI_ENTROPY_RETIREMENT_BASIS_SCHEMA_VERSION,
            "module": "loopx.capabilities.anti_entropy.contract",
            "doc": _README,
        },
        {
            "schema_version": ANTI_ENTROPY_RECEIPT_SCHEMA_VERSION,
            "module": "loopx.capabilities.anti_entropy.contract",
            "doc": _README,
        },
    ],
    "smokes": [
        "python3 examples/anti-entropy-governance-smoke.py",
        "python3 -m pytest tests/capabilities/test_anti_entropy.py -q",
    ],
    "docs": [_README],
    "boundaries": [
        "The capability is default-off and task-scoped: a turn receives the retirement context only when the task explicitly declares a retirement basis or enables the capability; disabled turns get zero injection.",
        "The turn-context hook projects declared targets, the inspect descriptor, and delete-first guidance into host input via the shared attach_turn_context composition; it grants no execution authority.",
        "Task intent comes from the selected Todo's own instruction/note text or an explicit capability id in its required capabilities; the post-change workspace is the Goal repository, never the process working directory.",
        "LoopX has no capability-owned host tool registry: the inspect descriptor is projected as turn-context data, and the CLI (or the Python entry it wraps) remains the callable route.",
        "The CLI (verify/inspect) remains the shared verification entry for humans and agents; final delivery acceptance is owned by the existing change-quality prepare/record/verify owner.",
        "Delete-first requires target old logic to be cleanly decommissioned in the diff without re-introducing wrappers or zombie symbols.",
        "AST-based diff hygiene verifies exact code diffs without mutating repository code or performing speculative git writes.",
        "Target paths declared for decommissioning must be cleanly removed in the unified diff.",
        "Symbol retirement certification requires a completely scanned, explicitly bound post-change workspace; truncated scans (oversized files, unreadable files, file-count limits) block certification with AE004.",
        "Advisory findings highlight non-blocking hygiene opportunities (such as dead commented code) without causing false positive rejections of documentation or explanations.",
        "Inspired by and adopting core engineering principles of delete-first code retirement from Aegis.",
    ],
    "next_real_step": (
        "Run verification: loopx anti-entropy verify --diff-file <path> "
        "--basis-json <json|path> --workspace <post-change-sources> --format json"
    ),
}
