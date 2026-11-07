"""Adapt registered host-local Agent observations to the typed state owner."""

from typing import Any, TYPE_CHECKING, cast

from pathlib import Path

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ..effect_runtime import effect_runtime_result
from ..todos.contract import normalize_required_capabilities, normalize_todo_claimed_by


def agent_capability_memory(
    *, registry_path: Path, runtime_root: Path, goal_id: str, agent_id: str,
    available: Any = None, unavailable: Any = None, forget: Any = None,
    execute: bool = False,
) -> dict[str, Any]:
    goal_id = str(goal_id).strip()
    normalized_agent = normalize_todo_claimed_by(agent_id)
    if not normalized_agent:
        raise ValueError("agent_id must be a public-safe registered agent id")
    goal = load_goal_from_registry(registry_path, goal_id)
    result = effect_runtime_result("agent.capability_memory", {
        "schema_version": "agent_runtime_capability_request_v0",
        "runtime_root": str(runtime_root.expanduser().resolve()),
        "registry": str(registry_path.expanduser().resolve()),
        "goal_id": goal_id, "agent_id": normalized_agent,
        "registered_agents": registered_agent_ids_for_goal(goal),
        "available": normalize_required_capabilities(available),
        "unavailable": normalize_required_capabilities(unavailable),
        "forget": normalize_required_capabilities(forget),
        "execute": execute,
    })
    if not isinstance(result, dict) or result.get("schema_version") != "agent_runtime_capabilities_v0":
        raise TypeError("invalid agent runtime capability memory result")
    return result


if TYPE_CHECKING:
    from ..capability_hooks import TurnStartHookRegistration


def turn_start_hook(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    available: Any,
) -> "TurnStartHookRegistration":
    """Persist invocation observations through the admitted turn-start hook."""

    from ..capability_hooks import (
        TURN_START_HOOK_RESULT_SCHEMA_VERSION,
        TurnStartHookRegistration,
    )

    normalized = normalize_required_capabilities(available)

    def produce() -> dict[str, Any]:
        state = agent_capability_memory(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            available=normalized,
            execute=True,
        )
        return {
            "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": "agent.capability_memory",
            "capability_id": "agent-capability-memory",
            "phase": "turn_start",
            "status": "empty",
            "observation_count": 0,
            "agent_read_required": False,
            "external_reads_performed": False,
            "external_writes_performed": False,
            "local_private_state_mutated": state.get("written") is True,
            "private_content_returned": False,
            "provider_payload_returned": False,
            "error_code": None,
        }

    return TurnStartHookRegistration(
        hook_id="agent.capability_memory",
        capability_id="agent-capability-memory",
        requested_read_scope=("goal_agent_registration",),
        requested_write_scope=("agent_private_capability_memory",),
        producer=produce,
    )


def extend_turn_start_dispatch(
    dispatch: dict[str, Any],
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
    available: Any,
) -> dict[str, Any]:
    """Compose capability memory without putting provider writes in quota core."""

    normalized = normalize_required_capabilities(available)
    if not agent_id or not normalized:
        return dispatch
    from ..capability_hooks import dispatch_turn_start_hooks

    extra = dispatch_turn_start_hooks(
        (
            turn_start_hook(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=goal_id,
                agent_id=agent_id,
                available=normalized,
            ),
        )
    )
    result = dict(dispatch)
    for key in ("results", "required_reads", "failures"):
        result[key] = list(dispatch.get(key) or []) + list(extra.get(key) or [])
    for key in ("registered_count", "invoked_count"):
        result[key] = int(dispatch.get(key) or 0) + int(extra.get(key) or 0)
    return result


def resolve_agent_capabilities(
    status_payload: dict[str, Any], *, goal_id: str, agent_identity: dict[str, Any] | None,
    item: dict[str, Any], project_asset: dict[str, Any], available: Any,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read current scoped observations even when the caller's status is cached."""
    from .capability_gate import _evaluate
    from ..quota.goal_boundary import declared_available_capabilities

    root, registry = status_payload.get("runtime_root"), status_payload.get("registry")
    if state is None and agent_identity and root and registry:
        try:
            state = agent_capability_memory(
                registry_path=Path(str(registry)), runtime_root=Path(str(root)),
                goal_id=goal_id, agent_id=agent_identity["agent_id"],
            )
        except Exception:  # noqa: BLE001 - optional private projection must fail open
            # Status/quota projection is still useful when a cached or fixture
            # status packet cannot reach the host-local capability store.  Do
            # not drop the entire agent lane; the invocation capabilities and
            # typed goal declarations remain authoritative for this read.
            state = {}
    availability = _evaluate(
        "availability",
        goal=[*declared_available_capabilities(item), *declared_available_capabilities(project_asset)],
        runtime=normalize_required_capabilities(available), agent=state or {},
    )
    if agent_identity:
        # Default status/quota surfaces need only the compact runtime input used
        # to rebuild interaction contracts. Detailed inheritance and correction
        # readback belongs to the explicit ``agent-capabilities`` command.
        agent_identity["runtime_available_capabilities"] = availability[
            "runtime_available"
        ]
    return cast(dict[str, Any], availability)
