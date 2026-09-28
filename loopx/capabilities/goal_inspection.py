"""On-demand composition inputs, not a second configuration or memory store."""

from pathlib import Path
from typing import Any

from ..agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ..control_plane.agent_context import project_goal_agent_context
from ..control_plane.goals.configure_goal_service import (
    read_goal_configuration_with_source_route,
)
from ..control_plane.runtime.runtime_projection_route import (
    resolve_goal_source_runtime_route,
)
from .configuration_inspection import (
    inspect_machine_namespaces,
    project_goal_configuration,
)


def inspect_goal_capabilities(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None = None,
    phase: str | None = None,
    runtime_root_override: str | None = None,
) -> dict[str, Any]:
    """Compose independent owner reads without inferring applicability or use.

    Context remains the existing TS owner's bounded, guidance-only projection.
    Nothing is appended to automatic hooks; inspection is an explicit cold path.
    """
    if bool(agent_id) != bool(phase):
        raise ValueError("--agent-id and --phase must be provided together")
    if phase is not None and phase not in {
        "before_plan",
        "before_delegate",
        "after_delegate_result",
    }:
        raise ValueError("unsupported agent context phase")
    configuration = project_goal_configuration(
        read_goal_configuration_with_source_route(
            registry_path=registry_path, goal_id=goal_id
        ),
        machine_namespaces=inspect_machine_namespaces(runtime_root),
    )
    context = None
    context_status = "not_requested"
    if agent_id:
        route = resolve_goal_source_runtime_route(
            registry_path=registry_path, goal_id=goal_id
        )
        source = str(route.get("source_registry") or "")
        if not source:
            raise ValueError("Goal source registry could not be resolved")
        source_registry = Path(source)
        goal = load_goal_from_registry(source_registry, goal_id)
        if goal is None or agent_id not in registered_agent_ids_for_goal(goal):
            raise ValueError("coordinator is not registered for this Goal")
        context = project_goal_agent_context(
            phase=phase,
            scope={"goal_id": goal_id, "agent_id": agent_id, "todo_id": None},
            goal=goal,
            registry_path=source_registry,
            runtime_root=(
                Path(runtime_root_override).expanduser()
                if runtime_root_override
                else Path(str(route["source_runtime_root"]))
            ),
        )
        context_status = "projected" if context is not None else "no_contribution"
    return {
        "ok": True,
        "schema_version": "loopx_goal_capability_inspection_v0",
        "read_only": True,
        "authority": "configuration_and_guidance_only",
        "scope": {"goal_id": goal_id, "agent_id": agent_id, "phase": phase},
        "coverage": "configuration_catalog_and_coordinator_context",
        "read_consistency": "independent_owner_reads_not_an_execution_snapshot",
        "configuration": configuration,
        "agent_context_status": context_status,
        "agent_context": context,
        "not_observed": [
            "invocation_readiness",
            "execution",
            "adoption",
            "outcome_utility",
        ],
    }


def render_goal_capabilities(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return str(payload.get("error") or "Capability inspection failed")
    configuration = payload["configuration"]
    lines = [
        f"# Goal capability inspection: {configuration['goal_id']}",
        "",
        "Configuration and projected guidance only; not readiness, execution, adoption or utility.",
        "This is not an atomic execution snapshot or a complete provider inventory.",
        "",
    ]
    for entry in configuration["capability_catalog"]["capabilities"]:
        effective = entry["effective_configuration"]
        lines.append(
            f"- {entry['capability_id']}: {effective['source']} ({effective['effective_revision']})"
        )
    lines.extend(["", f"Coordinator context: {payload['agent_context_status']}"])
    context = payload.get("agent_context")
    if context:
        for contribution in context["contributions"]:
            lines.append(
                f"- {contribution['capability_id']} / {contribution['revision']}"
            )
            lines.extend(f"  - {item}" for item in contribution["guidance"])
        if context["failures"]:
            lines.append(
                "Context provider failures are present; inspect JSON diagnostics."
            )
    lines.append(
        "Use --format json for effective values, source revisions and context facts."
    )
    return "\n".join(lines) + "\n"
