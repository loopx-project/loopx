"""Transport lifecycle inputs to the typed capability context owner."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .effect_runtime import effect_runtime_result
from ..orchestration import compact_orchestration_policy


def project_agent_context(
    *,
    phase: str,
    scope: Mapping[str, Any],
    orchestration: Mapping[str, Any],
    observations: Mapping[str, Any] | None = None,
):
    return effect_runtime_result(
        "capability_hook.agent_context.project",
        {
            "phase": phase,
            "scope": dict(scope),
            "orchestration": dict(orchestration),
            "observations": dict(observations or {}),
        },
    )


def project_goal_agent_context(
    *,
    phase: str,
    scope: Mapping[str, Any],
    goal: Mapping[str, Any],
    registry_path: Path,
    runtime_root: Path,
    observations: Mapping[str, Any] | None = None,
):
    """Project the Goal policy plus its public-safe delegated runtime facts."""

    orchestration = compact_orchestration_policy(goal.get("spawn_policy"))
    projected_observations = dict(observations or {})
    enabled = (
        orchestration.get("mode") == "multi_subagent"
        and orchestration.get("spawn_allowed") is True
        and int(orchestration.get("max_children") or 0) > 0
    )
    execution_config = str(orchestration.get("execution_config") or "") or None
    if enabled and execution_config:
        project = Path(str(goal.get("repo") or ".")).expanduser()
        # Import lazily: the delegation projection reuses the Turn host owner,
        # whose package also imports envelope_agent_context for later lifecycle
        # phases.
        from .collaboration.delegation_context import project_delegation_context

        projected_observations.setdefault(
            "delegation_context",
            project_delegation_context(
                runtime_root=runtime_root,
                registry_path=registry_path,
                goal_id=str(scope.get("goal_id") or ""),
                agent_id=str(scope.get("agent_id") or ""),
                project=project,
                execution_config=execution_config,
                include_operation_receipts=phase == "after_delegate_result",
            ),
        )
    return project_agent_context(
        phase=phase,
        scope=scope,
        orchestration=orchestration,
        observations=projected_observations,
    )


def _signed_delegation_observation(context: Mapping[str, Any]) -> Mapping[str, Any] | None:
    contributions = context.get("contributions")
    if not isinstance(contributions, list):
        return None
    for contribution in contributions:
        if not isinstance(contribution, Mapping):
            continue
        if contribution.get("capability_id") != "multi_subagent":
            continue
        facts = contribution.get("facts")
        if not isinstance(facts, Mapping):
            continue
        value = facts.get("delegation_context")
        if isinstance(value, Mapping):
            return value
    return None


def envelope_agent_context(
    envelope: Mapping[str, Any],
    *,
    phase: str,
    observations: Mapping[str, Any] | None = None,
):
    """The signed planning contribution binds later phases to the coordinator."""
    context = envelope.get("agent_context")
    if not isinstance(context, Mapping):
        return None
    boundary = envelope.get("boundary") or {}
    projected_observations = dict(observations or {})
    signed_delegation = _signed_delegation_observation(context)
    if signed_delegation is not None and phase != "after_delegate_result":
        projected_observations.setdefault("delegation_context", signed_delegation)
    return project_agent_context(
        phase=phase,
        scope=context["scope"],
        orchestration=boundary.get("orchestration") or {},
        observations=projected_observations,
    )


def agent_context_descriptor():
    return effect_runtime_result("capability_hook.agent_context.describe", {})
