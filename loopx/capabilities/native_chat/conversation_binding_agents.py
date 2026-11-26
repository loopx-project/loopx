"""Steward executor reconciliation, binding stop/recovery, and attached-agent checks."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
from pathlib import Path

from ...chat_store import TERMINAL_TURN_STATES
from ...file_lock import exclusive_file_lock
from .conversation_binding_contracts import (
    BindingControllerProtocol,
    BindingPayload,
    BindingPayloadView,
    BindingSessionStoreProtocol,
    BindingStateDict,
    CoreEffectFn,
    LockFn,
    ObserveFn,
    ProjectContextsProtocol,
    ReadJsonFn,
    WriteJsonFn,
    as_binding_payload,
    host_audiences,
    host_ref,
    session_observation,
)


def reconcile_steward_executors_flow(
    *,
    controller: BindingControllerProtocol | None,
    read_fn: Callable[[], BindingStateDict],
    reconcile_one_fn: Callable[[BindingPayload, BindingPayloadView], None],
) -> None:
    """Publish a previously adopted machine executor across eligible steward bindings."""
    from ...chat_manager import steward_machine_defaults

    if controller is None:
        return
    defaults = steward_machine_defaults(controller)
    if (
        not defaults
        or defaults.get("status") != "ready"
        or defaults.get("source") != "machine_configuration"
        or defaults.get("selection_policy") not in {"preferred", "pinned"}
        or defaults.get("executor_endpoint") not in {"antigravity", "codex"}
    ):
        return
    try:
        rows = read_fn()["bindings"]
        if not isinstance(rows, list):
            raise ValueError("invalid conversation bindings state")
    except (OSError, ValueError, KeyError) as exc:
        logging.getLogger(__name__).warning(
            "Steward bindings unavailable for reconciliation (%s)",
            type(exc).__name__,
        )
        return
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("context_kind") != "steward"
            or row.get("audience", "owner") != "owner"
            or row.get("enabled") is not True
            or row.get("executor_endpoint_id") not in {"antigravity", "codex"}
            or row.get("executor_endpoint_id") == defaults["executor_endpoint"]
        ):
            continue
        try:
            reconcile_one_fn(row, defaults)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            logging.getLogger(__name__).warning(
                "Steward executor reconciliation held (%s)",
                type(exc).__name__,
            )


def reconcile_steward_executor_flow(
    *,
    bindings_path: Path,
    controller: BindingControllerProtocol,
    projects: ProjectContextsProtocol,
    row: BindingPayloadView,
    defaults: BindingPayloadView,
    read_fn: Callable[[], BindingStateDict],
    observe_fn: ObserveFn,
    core_fn: CoreEffectFn,
    write_json_fn: WriteJsonFn,
    goal_scope_ids_fn: Callable[[BindingPayloadView], list[str]],
    lock_fn: LockFn = exclusive_file_lock,
) -> None:
    """Reconcile one eligible owner steward binding with adopted machine defaults."""
    from ...chat_manager import manager_executor_allocation, steward_machine_defaults
    from ..machine_configuration.builtins import (
        build_builtin_machine_configuration_registry,
    )
    from ..machine_configuration.contract import machine_configuration_revision
    from ..machine_configuration.store import read_machine_configuration
    from ..steward_executor.allocation import normalize_manager_executor_allocation
    from ..steward_executor.machine_defaults import effective_steward_executor_defaults

    store = controller.store
    binding_id = str(row["binding_id"])
    endpoint = str(defaults["executor_endpoint"])
    channel_prefix = f"manager.external.native.{binding_id}."
    all_open = [
        session
        for session in store.list_sessions()
        if str(session.get("channel_id") or "").startswith(channel_prefix)
        and session.get("status") != "closed"
    ]
    candidates: list[BindingPayload] = []
    for candidate in all_open:
        steward_context = candidate.get("steward_context")
        if not (isinstance(steward_context, Mapping) and steward_context.get("topic_ref")):
            candidates.append(candidate)
    if (
        len(candidates) != 1
        or candidates[0].get("agent_id") != endpoint
        or candidates[0].get("status") != "ready"
        or any(
            open_session.get("status") != "ready"
            or open_session.get("active_turn_id") is not None
            for open_session in all_open
        )
    ):
        return  # Defaults alone do not retarget an existing session.
    observation = observe_fn(str(row["transport_ref"]))
    candidate_session_id = str(candidates[0]["session_id"])
    with lock_fn(bindings_path, operation="reconcile_steward_executor"):
        current = read_fn()
        if row not in current["bindings"] or steward_machine_defaults(controller) != defaults:
            return
        session = store.load_session(candidate_session_id)
        saved = session.get("steward_context") if session is not None else None
        stored = session.get("manager_executor_allocation") if session is not None else None
        if (
            not session
            or not isinstance(saved, dict)
            or not isinstance(stored, dict)
            or session.get("status") != "ready"
            or session.get("active_turn_id") is not None
            or session.get("agent_id") != endpoint
            or session.get("executor_endpoint_id") != endpoint
            or stored.get("selection_policy") != defaults["selection_policy"]
            or store.queued_turns(str(session["session_id"]), strict=True, include_reserved=True)
        ):
            return
        allocation: BindingPayload | None = None
        if stored.get("configuration_revision") == defaults.get("configuration_revision"):
            try:
                allocation = as_binding_payload(normalize_manager_executor_allocation(stored))
            except (TypeError, ValueError):
                pass
        else:
            # A machine configuration transaction records the revision of
            # the whole document. Match that exact basis before projecting
            # it to the narrower revision used by steward allocations.
            configuration = read_machine_configuration(
                store.root.parent,
                registry=build_builtin_machine_configuration_registry(),
            )
            if (
                not configuration
                or machine_configuration_revision(configuration)
                != stored.get("configuration_revision")
                or effective_steward_executor_defaults(configuration) != defaults
            ):
                return
        if allocation is None:
            allocation = as_binding_payload(
                manager_executor_allocation(controller, machine_defaults=dict(defaults))
            )
        expected = {
            **session_observation(session),
            "manager_executor_allocation": allocation,
            "active_turn_id": session.get("active_turn_id"),
            "agent_model_id": session.get("agent_model_id"),
            "agent_reasoning_effort": session.get("agent_reasoning_effort"),
        }
        result = core_fn(
            "collaboration.conversation.binding",
            {
                "current": current,
                "expected_revision": current["revision"],
                "operation": "reconcile_steward_executor",
                "binding_id": binding_id,
                "observation": observation,
                "available_projects": projects.available(),
                "available_goal_ids": goal_scope_ids_fn(row),
                "machine_defaults": dict(defaults),
                "session": expected,
                "queue_empty": True,
            },
        )
        upstream_thread_id = session.get("upstream_thread_id")
        if stored != allocation:
            store.update_session(
                str(session["session_id"]),
                expected_statuses={"ready"},
                expected_upstream_thread_id=(
                    upstream_thread_id if isinstance(upstream_thread_id, str) else None
                ),
                manager_executor_allocation=allocation,
            )
        fresh = store.load_session(str(session["session_id"]))
        if (
            not fresh
            or any(fresh.get(key) != value for key, value in expected.items())
            or store.queued_turns(str(session["session_id"]), strict=True, include_reserved=True)
            or steward_machine_defaults(controller) != defaults
        ):
            return
        if result["changed"]:
            write_json_fn(bindings_path, result["state"], preserve_mode=True)
        if read_fn() != result["state"]:
            raise OSError("steward executor binding publication did not verify")


def stop_revoked_binding_flow(
    controller: BindingControllerProtocol | None,
    binding_id: str,
) -> None:
    """Apply the committed grant decision through existing exact Turn control."""
    if controller is None:
        return
    store = controller.store
    failures: list[Exception] = []
    for row in store.list_sessions():
        session = store.load_session(str(row["session_id"]))
        if session is None:
            continue
        bound = any(
            isinstance(ctx := session.get(key), dict)
            and ctx.get("binding_id") == binding_id
            for key in ("project_context", "steward_context")
        )
        turns = list(store.queued_turns(str(session["session_id"])))
        active_turn_id = session.get("active_turn_id")
        active = (
            store.load_turn(str(session["session_id"]), str(active_turn_id))
            if active_turn_id
            else None
        )
        if active:
            turns.append(active)
        for turn in turns:
            raw_target = turn.get("external_agent_target")
            target_context = (
                raw_target.get("context")
                if isinstance(raw_target, Mapping)
                else None
            )
            target_binding = (
                target_context.get("binding_id")
                if isinstance(target_context, Mapping)
                else None
            )
            if (
                (not bound and target_binding != binding_id)
                or turn.get("status") in TERMINAL_TURN_STATES
            ):
                continue
            try:
                stopped = controller.interrupt_turn(
                    session_id=str(session["session_id"]),
                    turn_id=str(turn["turn_id"]),
                )
                if stopped.get("status") not in TERMINAL_TURN_STATES:
                    raise RuntimeError("Turn stop did not settle")
            except Exception as exc:
                # Revocation remains published even when a host lacks stop
                # control. Continue stopping other exact targets, then report
                # failure without exposing private transport diagnostics.
                failures.append(exc)
    if failures:
        raise OSError(
            "Conversation grant revoked; execution stop is not confirmed. "
            "Retry stop for this binding."
        ) from failures[0]


def stop_recoveries_flow(
    controller: BindingControllerProtocol | None,
    current: BindingPayloadView,
) -> list[BindingPayload]:
    """Project revoked execution counts per binding from canonical Turns."""
    if controller is None:
        return []
    raw_bindings = current["bindings"]
    if not isinstance(raw_bindings, list):
        return []
    authorized = {
        row["binding_id"]
        for row in raw_bindings
        if isinstance(row, Mapping) and "binding_id" in row
    }
    store = controller.store
    pending: dict[str, tuple[str, int]] = {}
    for row in store.list_sessions():
        session = store.load_session(str(row["session_id"]))
        if session is None:
            continue
        turns = list(store.queued_turns(str(session["session_id"])))
        active_turn_id = session.get("active_turn_id")
        active = (
            store.load_turn(str(session["session_id"]), str(active_turn_id))
            if active_turn_id
            else None
        )
        if active:
            turns.append(active)
        for turn in turns:
            if turn.get("status") in TERMINAL_TURN_STATES:
                continue
            raw_target = turn.get("external_agent_target")
            target_ctx = (
                raw_target.get("context")
                if isinstance(raw_target, Mapping)
                else None
            )
            context = (
                target_ctx
                or session.get("project_context")
                or session.get("steward_context")
                or {}
            )
            if not isinstance(context, Mapping):
                continue
            binding_id = context.get("binding_id")
            if not isinstance(binding_id, str) or not binding_id or binding_id in authorized:
                continue
            project_ref = str(context.get("project_ref") or "")
            existing_ref, count = pending.get(binding_id, (project_ref, 0))
            pending[binding_id] = (existing_ref, count + 1)
    return [
        {
            "binding_id": key,
            "project_ref": pending[key][0],
            "pending_count": pending[key][1],
        }
        for key in sorted(pending)
    ]


def agent_observation_flow(
    controller: BindingControllerProtocol | None,
    session_id: str,
) -> BindingPayload:
    """Read only the explicitly selected canonical attached Session/Goal."""
    from ...attached_session import _require_bound_host, require_current_attached_session
    from ...control_plane.projects.registry_codec import load_registry
    from ...registry import find_registry_goal

    if controller is None or controller.registry_path is None:
        raise ValueError("the attached Session registry is unavailable")
    session = require_current_attached_session(
        store=controller.store,
        registry_path=controller.registry_path,
        session_id=session_id,
    )
    registry = load_registry(controller.registry_path)
    goal = find_registry_goal(registry, str(session.get("goal_id") or ""))
    if goal is None or not goal.get("repo"):
        raise ValueError("the exact Agent workspace is unavailable")
    _require_bound_host(
        goal=goal,
        agent_id=str(session["agent_id"]),
        host_surface=str(session["host_surface"]),
        host_session_id=str(session["upstream_thread_id"]),
    )
    raw_coordination = goal.get("coordination")
    registered_agents = (
        raw_coordination.get("registered_agents", [])
        if isinstance(raw_coordination, Mapping)
        else []
    )
    return {
        "session": {
            key: session.get(key)
            for key in [
                "session_id",
                "goal_id",
                "goal_instance_id",
                "agent_id",
                "executor_endpoint_id",
                "session_mode",
                "status",
                "external_conversation_binding_id",
            ]
        },
        "goal": {
            "goal_id": goal["id"],
            "workspace_path": str(Path(str(goal["repo"])).resolve()),
            "registered_agents": registered_agents,
        },
        "host_binding_verified": True,
        "host_ref": host_ref(session),
        "host_audience_binding_ids": host_audiences(controller.store, session),
    }


def agent_candidates_flow(
    *,
    binding_id: str,
    controller: BindingControllerProtocol | None,
    projects: ProjectContextsProtocol | None,
    read_fn: Callable[[], BindingStateDict],
    observe_agent_fn: Callable[[str], BindingPayload],
) -> list[BindingPayload]:
    """Return attached host sessions eligible for external delegation under a binding."""
    row = next(
        item for item in read_fn()["bindings"] if item["binding_id"] == binding_id
    )
    if (
        row["context_kind"] != "project"
        or row.get("audience") == "group"
        or controller is None
        or projects is None
    ):
        if (
            row["context_kind"] != "project"
            or row.get("audience") == "group"
            or controller is None
        ):
            return []
        raise ValueError("bound conversation project contexts are unavailable")
    project = next(
        (
            item
            for item in projects.available()
            if item["project_ref"] == row["project_ref"]
        ),
        None,
    )
    if project is None:
        return []
    candidates: list[BindingPayload] = []
    for session in controller.store.list_sessions():
        if (
            session.get("session_mode") != "attached_host"
            or session.get("status") == "closed"
        ):
            continue
        try:
            observed = observe_agent_fn(str(session["session_id"]))
            observed_session = observed["session"]
            observed_goal = observed["goal"]
            audience_ids = observed["host_audience_binding_ids"]
            if (
                isinstance(observed_session, dict)
                and isinstance(observed_goal, Mapping)
                and isinstance(audience_ids, list)
                and observed_session["status"] in {"ready", "busy"}
                and observed_goal["workspace_path"] == project["workspace_path"]
                and observed_session.get("external_conversation_binding_id")
                in {None, binding_id}
                and all(owner == binding_id for owner in audience_ids)
            ):
                candidates.append(observed_session)
        except (ValueError, OSError, KeyError):
            continue
    return candidates[:16]


def validate_external_agent_turn_flow(
    *,
    store: BindingSessionStoreProtocol,
    session: BindingPayloadView,
    goal: BindingPayloadView | None,
    turn: BindingPayloadView,
    read_json_fn: ReadJsonFn,
    core_fn: CoreEffectFn,
) -> None:
    """Verify external attached-agent audience grant and host binding under fences."""
    saved = turn.get("external_agent_target")
    if saved is None:
        return
    from ...attached_session import _require_bound_host

    if (
        not isinstance(saved, dict)
        or turn.get("origin") not in {"lark", "external"}
        or goal is None
    ):
        raise ValueError("the attached external audience is unavailable")
    context = saved["context"]
    target = saved["target"]
    if not Path(str(goal["repo"])).is_dir():
        raise ValueError("the attached Agent workspace is unavailable")
    state = read_json_fn(store.root / "conversation-bindings.json")
    raw_bindings = state["bindings"]
    selected = (
        next(
            (
                row
                for row in raw_bindings
                if isinstance(row, Mapping)
                and row["binding_id"] == context["binding_id"]
            ),
            None,
        )
        if isinstance(raw_bindings, list)
        else None
    )
    if selected is None:
        raise ValueError("the Agent audience grant was revoked")
    _require_bound_host(
        goal=dict(goal),
        agent_id=str(session["agent_id"]),
        host_surface=str(session["host_surface"]),
        host_session_id=str(session["upstream_thread_id"]),
    )
    raw_coordination = goal.get("coordination")
    registered_agents = (
        raw_coordination.get("registered_agents", [])
        if isinstance(raw_coordination, Mapping)
        else []
    )
    core_fn(
        "collaboration.conversation.agent_target",
        {
            "binding": selected,
            "context": context,
            "target": target,
            "target_observation": {
                "session": session,
                "goal": {
                    "goal_id": goal["id"],
                    "workspace_path": str(Path(str(goal["repo"])).resolve()),
                    "registered_agents": registered_agents,
                },
                "host_binding_verified": True,
                "host_ref": host_ref(session),
                "host_audience_binding_ids": host_audiences(store, session),
            },
        },
    )
