"""Adapt registered host-local Agent observations to the typed state owner."""

from collections.abc import Mapping
from typing import Any, TYPE_CHECKING, cast

from pathlib import Path

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ..effect_runtime import effect_runtime_result
from ..todos.contract import normalize_required_capabilities, normalize_todo_claimed_by


def canonical_capability_registry_scope(
    registry_path: Path, goal: Mapping[str, Any] | None
) -> tuple[str, list[str]]:
    """Unify synced global-registry mirrors with their canonical project source_registry."""
    resolved = str(registry_path.expanduser().resolve())
    if isinstance(goal, Mapping):
        raw_source = str(goal.get("source_registry") or "").strip()
        if raw_source:
            canonical = str(Path(raw_source).expanduser().resolve())
            if canonical != resolved:
                return canonical, [resolved]
    return resolved, []


def agent_capability_memory(
    *, registry_path: Path, runtime_root: Path, goal_id: str, agent_id: str,
    available: Any = None, unavailable: Any = None, forget: Any = None,
    execute: bool = False, preserve_unavailable: bool = False,
) -> dict[str, Any]:
    goal_id = str(goal_id).strip()
    normalized_agent = normalize_todo_claimed_by(agent_id)
    if not normalized_agent:
        raise ValueError("agent_id must be a public-safe registered agent id")
    goal = load_goal_from_registry(registry_path, goal_id)
    canonical_registry, legacy_registries = canonical_capability_registry_scope(
        registry_path, goal
    )
    request: dict[str, Any] = {
        "schema_version": "agent_runtime_capability_request_v0",
        "runtime_root": str(runtime_root.expanduser().resolve()),
        "registry": canonical_registry,
        "goal_id": goal_id, "agent_id": normalized_agent,
        "registered_agents": registered_agent_ids_for_goal(goal),
        "available": normalize_required_capabilities(available),
        "unavailable": normalize_required_capabilities(unavailable),
        "forget": normalize_required_capabilities(forget),
        "execute": execute,
    }
    if legacy_registries:
        request["legacy_registries"] = legacy_registries
    if preserve_unavailable:
        request["preserve_unavailable"] = True
    result = effect_runtime_result("agent.capability_memory", request)
    if not isinstance(result, dict) or result.get("schema_version") != "agent_runtime_capabilities_v0":
        raise TypeError("invalid agent runtime capability memory result")
    return result


def resolve_executing_registered_agent(
    *,
    goal: Mapping[str, Any] | None,
    explicit_agent_id: str | None = None,
) -> str | None:
    """Resolve the executing registered Agent without guessing across unbound peers."""
    if not isinstance(goal, Mapping):
        return None
    registered = registered_agent_ids_for_goal(dict(goal))
    if not registered:
        return None
    normalized_explicit = normalize_todo_claimed_by(explicit_agent_id) if explicit_agent_id else None
    if normalized_explicit:
        return normalized_explicit if normalized_explicit in registered else None

    from ...thread_agent_binding import (
        HOST_THREAD_ID_ENV,
        ambient_host_thread_id,
        resolve_thread_agent_binding,
    )

    bound_agents: set[str] = set()
    saw_conflict = False
    for surface in sorted(HOST_THREAD_ID_ENV):
        thread_id = ambient_host_thread_id(surface)
        if not thread_id:
            continue
        try:
            binding = resolve_thread_agent_binding(
                dict(goal), host_surface=surface, thread_id=thread_id
            )
        except Exception:  # noqa: BLE001 - ambient host lookup must never fail task commands
            continue
        status = binding.get("status")
        if status == "conflict":
            saw_conflict = True
            break
        if status == "bound":
            candidate = normalize_todo_claimed_by(binding.get("agent_id"))
            if candidate and candidate in registered:
                bound_agents.add(candidate)
    if not saw_conflict and len(bound_agents) == 1:
        return next(iter(bound_agents))
    if saw_conflict or len(bound_agents) > 1:
        return None
    if len(registered) == 1:
        return registered[0]
    return None


def record_verified_agent_capability_observation(
    *,
    registry_path: Path | None,
    runtime_root: Path | None,
    goal_id: str | None,
    available: Any,
    agent_id: str | None = None,
    goal: Mapping[str, Any] | None = None,
    preserve_unavailable: bool = True,
) -> dict[str, Any] | None:
    """Persist a verified host runtime observation for the executing registered Agent."""
    normalized_goal_id = str(goal_id or "").strip()
    normalized_available = normalize_required_capabilities(available)
    if (
        registry_path is None
        or runtime_root is None
        or not normalized_goal_id
        or not normalized_available
    ):
        return None
    try:
        resolved_goal = (
            dict(goal)
            if isinstance(goal, Mapping)
            else load_goal_from_registry(registry_path, normalized_goal_id)
        )
        target_agent = resolve_executing_registered_agent(
            goal=resolved_goal,
            explicit_agent_id=agent_id,
        )
        if not target_agent:
            return None
        return agent_capability_memory(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=normalized_goal_id,
            agent_id=target_agent,
            available=normalized_available,
            execute=True,
            preserve_unavailable=preserve_unavailable,
        )
    except Exception:  # noqa: BLE001 - optional host capability memory must fail open
        return None


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


def heartbeat_evidence_poll_possible(
    runtime_root: Path, *, goal: Mapping[str, Any], available: Any,
) -> bool:
    """Only a possible pre-quota poll needs the typed availability read."""
    from .capability_probe import host_network_sandbox_disabled, probe_host_runtime_capabilities
    from ..quota.goal_boundary import declared_available_capabilities

    if host_network_sandbox_disabled():
        return False
    if "external_evidence_poll" in (
        *normalize_required_capabilities(available),
        *declared_available_capabilities(goal),
        *probe_host_runtime_capabilities(),
    ):
        return True
    # A missing capability store rules out remembered observations. When it
    # exists or cannot be inspected, the typed owner resolves the Agent scope.
    try:
        (runtime_root / "agent-runtime-capabilities").stat()
    except FileNotFoundError:
        return False
    except OSError:
        pass
    return True


def resolve_agent_capabilities(
    status_payload: dict[str, Any], *, goal_id: str, agent_identity: dict[str, Any] | None,
    item: dict[str, Any], project_asset: dict[str, Any], available: Any,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read current scoped observations and auto-probed host readiness."""
    from .capability_gate import _evaluate
    from .capability_probe import host_network_sandbox_disabled, probe_host_runtime_capabilities
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
        runtime=normalize_required_capabilities(available),
        probed=probe_host_runtime_capabilities(),
        host_unavailable=["external_evidence_poll"] if host_network_sandbox_disabled() else [],
        agent=state or {},
    )
    if agent_identity:
        # Default status/quota surfaces need only the compact runtime input used
        # to rebuild interaction contracts. Detailed inheritance and correction
        # readback belongs to the explicit ``agent-capabilities`` command.
        agent_identity["runtime_available_capabilities"] = availability[
            "runtime_available"
        ]
    return cast(dict[str, Any], availability)
