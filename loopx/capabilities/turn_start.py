"""Composition of registered built-in observations at executing entrypoints.

This adapter invokes capability-owned registrations. Selection, permissions,
fact validation and cadence stay with their existing TypeScript owners.
"""
from pathlib import Path
from typing import Any

from ..control_plane.capability_hooks import (
    capability_hook_request_scope,
    dispatch_turn_start_hooks,
    merge_turn_start_dispatch,
)
from ..control_plane.todos.contract import normalize_required_capabilities


def extend_turn_start_dispatch(dispatch: dict[str, Any], *, registry_path: Path,
                               runtime_root: Path, goal_id: str, agent_id: str | None,
                               available: Any, provider_options: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    if not agent_id:
        return dispatch
    from .issue_fix.pr_wait_hook import turn_start_hook as dependencies
    from ..control_plane.agents.capability_memory import turn_start_hook as capability_memory

    factories = [("issue-fix", dependencies)]
    if normalize_required_capabilities(available):
        factories.append(("agent-capability-memory", capability_memory))
    with capability_hook_request_scope():
        registrations = [hook for capability, factory in factories if (hook := factory(
            registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
            agent_id=agent_id, available=available,
            **(provider_options or {}).get(capability, {}),
        )) is not None]
        if not registrations:
            return dispatch
        extra = dispatch_turn_start_hooks(registrations)
    return merge_turn_start_dispatch(
        dispatch,
        extra,
        include_contexts=True,
        only_nonempty_extra=True,
        include_uncertain=True,
    )


def attach_turn_start_effects(payload: dict[str, Any], dispatch: dict[str, Any]) -> None:
    """Preserve known and uncertain local effects from every registered hook."""
    if dispatch.get("registered_count") or dispatch.get("failures"):
        payload["turn_start_capability_hook_dispatch"] = dispatch
    values = [row.get("local_private_state_mutated") for row in dispatch.get("results", [])]
    if dispatch.get("uncertain_local_effects"):
        values.append(None)
    if not any(value is True or value is None for value in values):
        return
    written = True if any(value is True for value in values) else None
    effects = payload.setdefault("effects", {})
    if written is True or effects.get("state_written") is not True:
        effects["state_written"] = written
    if isinstance(payload.get("boundary"), dict):
        payload["boundary"]["read_only"] = False
