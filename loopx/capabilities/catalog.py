from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ..extensions.runtime import extension_catalog_entries
from .benchmark_toolkit.catalog_entry import BENCHMARK_TOOLKIT_CATALOG_ENTRY
from .integration_branch.catalog_entry import INTEGRATION_BRANCH_CATALOG_ENTRY
from .repository_change_window.catalog_entry import (
    REPOSITORY_CHANGE_WINDOW_CATALOG_ENTRY,
)
from .change_quality.catalog_entry import CHANGE_QUALITY_CATALOG_ENTRY
from .pr_review_queue.catalog_entry import PR_REVIEW_CATALOG_ENTRY
from .issue_fix.catalog_entry import ISSUE_FIX_CATALOG_ENTRY
from .decision_context.catalog_entry import DECISION_CONTEXT_CATALOG_ENTRY
from .project_skill_delivery.catalog_entry import PROJECT_SKILL_DELIVERY_CATALOG_ENTRY
from .material_lifecycle.catalog_entry import MATERIAL_LIFECYCLE_CATALOG_ENTRY
from .agent_turn_recall.catalog_entry import AGENT_TURN_RECALL_CATALOG_ENTRY
from .semantic_preference.catalog_entry import SEMANTIC_PREFERENCE_CATALOG_ENTRY
from .reward_memory.catalog_entry import REWARD_MEMORY_CATALOG_ENTRY
from .periodic_report.catalog_entry import PERIODIC_REPORT_CATALOG_ENTRY
from .content_ops.catalog_entry import CONTENT_OPS_CATALOG_ENTRY
from .value_connectors.catalog_entry import VALUE_CONNECTORS_CATALOG_ENTRY
from .explore.catalog_entry import EXPLORE_CATALOG_ENTRY
from .deep_research.catalog_entry import DEEP_RESEARCH_CATALOG_ENTRY
from .public_safe_outbound.catalog_entry import PUBLIC_SAFE_OUTBOUND_CATALOG_ENTRY
from .connector_registry.catalog_entry import CONNECTOR_REGISTRY_CATALOG_ENTRY
from .external_research.catalog_entry import EXTERNAL_RESEARCH_CATALOG_ENTRY
from .performance_diagnosis.catalog_entry import PERFORMANCE_DIAGNOSIS_CATALOG_ENTRY
from .reliability_diagnostics.catalog_entry import RELIABILITY_DIAGNOSTICS_CATALOG_ENTRY
from .progress_review.catalog_entry import PROGRESS_REVIEW_CATALOG_ENTRY
from .goal_capability_organization.catalog_entry import GOAL_CAPABILITY_ORGANIZATION_CATALOG_ENTRY
from .registry import CapabilityRegistry

CAPABILITY_CATALOG_SCHEMA_VERSION = "loopx_capability_catalog_v0"
CAPABILITY_DETAIL_SCHEMA_VERSION = "loopx_capability_detail_v0"


BUILTIN_CAPABILITIES: tuple[dict[str, Any], ...] = (
    BENCHMARK_TOOLKIT_CATALOG_ENTRY,
    INTEGRATION_BRANCH_CATALOG_ENTRY,
    REPOSITORY_CHANGE_WINDOW_CATALOG_ENTRY,
    CHANGE_QUALITY_CATALOG_ENTRY,
    PR_REVIEW_CATALOG_ENTRY,
    ISSUE_FIX_CATALOG_ENTRY,
    DECISION_CONTEXT_CATALOG_ENTRY,
    PROJECT_SKILL_DELIVERY_CATALOG_ENTRY,
    MATERIAL_LIFECYCLE_CATALOG_ENTRY,
    AGENT_TURN_RECALL_CATALOG_ENTRY,
    SEMANTIC_PREFERENCE_CATALOG_ENTRY,
    REWARD_MEMORY_CATALOG_ENTRY,
    PERIODIC_REPORT_CATALOG_ENTRY,
    CONTENT_OPS_CATALOG_ENTRY,
    VALUE_CONNECTORS_CATALOG_ENTRY,
    EXPLORE_CATALOG_ENTRY,
    DEEP_RESEARCH_CATALOG_ENTRY,
    PUBLIC_SAFE_OUTBOUND_CATALOG_ENTRY,
    CONNECTOR_REGISTRY_CATALOG_ENTRY,
    EXTERNAL_RESEARCH_CATALOG_ENTRY,
    PERFORMANCE_DIAGNOSIS_CATALOG_ENTRY,
    RELIABILITY_DIAGNOSTICS_CATALOG_ENTRY,
    PROGRESS_REVIEW_CATALOG_ENTRY,
    GOAL_CAPABILITY_ORGANIZATION_CATALOG_ENTRY,
)
# Preserve the original import surface while routing all reads through the registry.
CAPABILITIES = BUILTIN_CAPABILITIES


def _summary(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "title": record["title"],
        "status": record["status"],
        "origin": record["origin"],
        "visibility": record["visibility"],
        "provider_id": record["provider_id"],
        "provider_state": record["provider_state"],
        "real_world_anchor": record["real_world_anchor"],
        "entry_command": record["entry_command"],
        "documentation_route": record.get("documentation_route"),
        "implemented_protocol_count": len(record.get("implemented_protocols") or []),
        "implementation_provider_count": len(
            record.get("implementation_providers") or []
        ),
        "smoke_count": len(record.get("smokes") or []),
        "next_real_step": record["next_real_step"],
    }


def _register_declared_extension_providers(
    registry: CapabilityRegistry,
    record: Mapping[str, object],
) -> None:
    """Declare extension-delivered implementations named by a builtin entry.

    A provider distributed outside the Python extension lifecycle (for example
    an npm plugin) has no manifest or state file. The owning capability
    declares it so the catalog reports `declared=true` and
    `installed=enabled=ready=false` until a real lifecycle proves otherwise.
    """

    for implementation in record.get("implementation_providers") or []:
        if not isinstance(implementation, Mapping) or implementation.get("origin") != "extension":
            continue
        provider_id = str(implementation["provider_id"])
        if not any(provider["id"] == provider_id for provider in registry.providers()):
            registry.register_provider(
                {
                    "id": provider_id,
                    "origin": "extension",
                    "declared": True,
                    "installed": False,
                    "enabled": False,
                    "ready": False,
                }
            )
        registry.register_implementation(
            {
                "capability_id": record["id"],
                "provider_id": provider_id,
                "protocol": implementation["protocol"],
                "package": implementation.get("package"),
                "status": implementation.get("status"),
            }
        )


def build_capability_registry(
    extension_manifest_paths: Iterable[str | Path] = (),
    *,
    extension_state_file: str | Path | None = None,
) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register_provider(
        {
            "id": "loopx-core",
            "origin": "builtin",
            "declared": True,
            "installed": True,
            "enabled": True,
            "ready": True,
        }
    )
    for record in BUILTIN_CAPABILITIES:
        registry.register_capability(record)
    for record in BUILTIN_CAPABILITIES:
        _register_declared_extension_providers(registry, record)
    for manifest in extension_catalog_entries(
        extension_manifest_paths,
        state_file=extension_state_file,
    ):
        registry.register_provider(manifest["provider"])
        for record in manifest["capabilities"]:
            registry.register_capability(record)
        for implementation in manifest["implementations"]:
            registry.register_implementation(implementation)
    return registry


def capability_ids(
    extension_manifest_paths: Iterable[str | Path] = (),
    *,
    include_internal: bool = False,
    extension_state_file: str | Path | None = None,
) -> list[str]:
    return build_capability_registry(
        extension_manifest_paths,
        extension_state_file=extension_state_file,
    ).capability_ids(include_internal=include_internal)


def get_capability(
    capability_id: str,
    extension_manifest_paths: Iterable[str | Path] = (),
    *,
    include_internal: bool = False,
    extension_state_file: str | Path | None = None,
) -> dict[str, Any]:
    return build_capability_registry(
        extension_manifest_paths,
        extension_state_file=extension_state_file,
    ).get(
        capability_id,
        include_internal=include_internal,
    )


def build_capability_catalog_packet(
    extension_manifest_paths: Iterable[str | Path] = (),
    *,
    extension_state_file: str | Path | None = None,
) -> dict[str, Any]:
    registry = build_capability_registry(
        extension_manifest_paths,
        extension_state_file=extension_state_file,
    )
    return {
        "ok": True,
        "schema_version": CAPABILITY_CATALOG_SCHEMA_VERSION,
        "capabilities": [_summary(record) for record in registry.records()],
        "providers": registry.providers(),
    }


def build_capability_detail_packet(
    capability_id: str,
    extension_manifest_paths: Iterable[str | Path] = (),
    *,
    extension_state_file: str | Path | None = None,
) -> dict[str, Any]:
    record = get_capability(
        capability_id,
        extension_manifest_paths,
        extension_state_file=extension_state_file,
    )
    return {
        "ok": True,
        "schema_version": CAPABILITY_DETAIL_SCHEMA_VERSION,
        "capability": record,
    }


def render_capability_catalog_markdown(payload: dict[str, Any]) -> str:
    lines = ["# LoopX Capabilities", ""]
    for item in payload.get("capabilities") or []:
        if not isinstance(item, Mapping):
            continue
        lines.extend(
            [
                f"## {item.get('id')}: {item.get('title')}",
                "",
                f"- status: `{item.get('status')}`",
                f"- provider: `{item.get('provider_id')}` ({item.get('origin')})",
                f"- provider_state: `{item.get('provider_state')}`",
                f"- visibility: `{item.get('visibility')}`",
                f"- anchor: {item.get('real_world_anchor')}",
                f"- entry: `{item.get('entry_command')}`",
                f"- implemented_protocol_count: `{item.get('implemented_protocol_count')}`",
                f"- implementation_provider_count: `{item.get('implementation_provider_count')}`",
                f"- smoke_count: `{item.get('smoke_count')}`",
                f"- next: {item.get('next_real_step')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_capability_detail_markdown(payload: dict[str, Any]) -> str:
    record = payload.get("capability")
    if not isinstance(record, Mapping):
        return "# LoopX Capability\n\nNo capability found.\n"
    lines = [
        f"# LoopX Capability: {record.get('title')}",
        "",
        f"- id: `{record.get('id')}`",
        f"- status: `{record.get('status')}`",
        f"- provider: `{record.get('provider_id')}` ({record.get('origin')})",
        f"- provider_state: `{record.get('provider_state')}`",
        f"- visibility: `{record.get('visibility')}`",
        f"- anchor: {record.get('real_world_anchor')}",
        f"- value: {record.get('user_value')}",
        f"- entry: `{record.get('entry_command')}`",
        "",
        "## Commands",
        "",
    ]
    for item in record.get("commands") or []:
        if not isinstance(item, Mapping):
            continue
        lines.extend(
            [
                f"- `{item.get('command')}`",
                f"  - purpose: {item.get('purpose')}",
                f"  - boundary: {item.get('write_boundary')}",
            ]
        )
    agent_usage = record.get("agent_usage")
    if isinstance(agent_usage, Mapping):
        lines.extend(["", "## Agent Usage", ""])
        start_hint = agent_usage.get("benchmark_start_hint") or agent_usage.get(
            "start_hint"
        )
        if start_hint:
            lines.extend([str(start_hint), ""])
        for step in agent_usage.get("required_sequence") or []:
            lines.append(f"- `{step}`")
        board_commands = agent_usage.get("board_commands")
        if isinstance(board_commands, Mapping):
            lines.append("")
            for name, command in board_commands.items():
                lines.append(f"- {name}: `{command}`")
        selection_rule = agent_usage.get("selection_rule")
        if selection_rule:
            lines.extend(["", f"- selection rule: {selection_rule}"])
    lines.extend(["", "## Implemented Protocols", ""])
    for item in record.get("implemented_protocols") or []:
        if not isinstance(item, Mapping):
            continue
        lines.append(
            f"- `{item.get('schema_version')}` in `{item.get('module')}` "
            f"([{item.get('doc')}]({item.get('doc')}))"
        )
    lines.extend(["", "## Smokes", ""])
    for smoke in record.get("smokes") or []:
        lines.append(f"- `{smoke}`")
    lines.extend(["", "## Boundaries", ""])
    for boundary in record.get("boundaries") or []:
        lines.append(f"- {boundary}")
    lines.extend(["", "## Next Real Step", "", str(record.get("next_real_step"))])
    return "\n".join(lines).rstrip() + "\n"
