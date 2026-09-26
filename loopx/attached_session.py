"""Provider-neutral broker contract for already-running Agent sessions."""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .chat import normalize_agent_response
from .chat_store import CHAT_SESSION_MODE_ATTACHED, ChatSessionStore
from .control_plane.effect_runtime import effect_runtime_result
from .control_plane.goals.source_session_registry_state import (
    current_goal_ref,
    guard_path,
)
from .control_plane.projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    load_project_registry,
)
from .control_plane.todos.contract import normalize_todo_claimed_by
from .file_lock import exclusive_cross_runtime_file_lock, exclusive_file_lock
from .registry import find_registry_goal
from .thread_agent_binding import resolve_thread_agent_binding

ATTACHED_SESSION_BROKER_SCHEMA_VERSION = "loopx_attached_agent_session_broker_v0"
ATTACHED_SESSION_ADAPTER_KIND = "attached_host_session"
ATTACHED_SESSION_UPSTREAM_MODE = "host_broker"
MAX_ATTACHED_SESSION_CLAIM_WAIT_SECONDS = 1_800.0
ATTACHED_SESSION_CLAIM_POLL_INTERVAL_SECONDS = 0.1


def _binding_lock_path(
    store: ChatSessionStore,
    *,
    goal_id: str,
    agent_id: str,
    channel_id: str,
) -> Path:
    material = f"{goal_id}\0{agent_id}\0{channel_id}".encode()
    digest = hashlib.sha256(material).hexdigest()
    return store.root / "attached-bindings" / f"{digest}.json"


def _registered_agent(goal: dict[str, Any], agent_id: str) -> str:
    normalized = normalize_todo_claimed_by(agent_id)
    if not normalized:
        raise ValueError("agent_id must be a public-safe registered Agent id")
    coordination = goal.get("coordination")
    coordination = coordination if isinstance(coordination, dict) else {}
    registered = coordination.get("registered_agents")
    registered_ids = {
        normalize_todo_claimed_by(item)
        for item in (registered if isinstance(registered, list) else [])
    }
    if normalized not in registered_ids:
        raise ValueError(f"agent_id={normalized!r} is not registered for this Goal")
    return normalized


def _require_bound_host(
    *,
    goal: dict[str, Any],
    agent_id: str,
    host_surface: str,
    host_session_id: str,
) -> None:
    binding = resolve_thread_agent_binding(
        goal,
        host_surface=host_surface,
        thread_id=host_session_id,
    )
    if binding.get("status") != "bound" or binding.get("agent_id") != agent_id:
        raise ValueError(
            "the host session must already be bound to the exact registered Agent"
        )


def _session_fact(session: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "session_id": str(session.get("session_id") or ""),
        "goal_id": str(session.get("goal_id") or ""),
        "goal_instance_id": session.get("goal_instance_id"),
        "updated_at": str(session.get("updated_at") or ""),
    }


def _turn_fact(
    session: Mapping[str, Any],
    turn: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if turn is None:
        return None
    return {
        "goal_id": str(session.get("goal_id") or ""),
        "goal_instance_id": turn.get("goal_instance_id"),
        "admitted_goal_instance_id": turn.get("admitted_goal_instance_id"),
    }


def _lifecycle_decision(
    *,
    operation: str,
    registry: Mapping[str, Any],
    current_ref: Mapping[str, str],
    session: Mapping[str, Any] | None = None,
    turn: Mapping[str, Any] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "operation": operation,
        "profile_id": registry.get("profile_id"),
        "current_goal_ref": dict(current_ref),
    }
    if operation == "select":
        facts["candidates"] = [
            _session_fact(candidate) for candidate in candidates or []
        ]
    else:
        if session is None:
            raise RuntimeError("Chat lifecycle admission requires a Session")
        facts["session"] = _session_fact(session)
        facts["turn"] = _turn_fact(session, turn)
    decision = effect_runtime_result(
        "goal.chat_session.lifecycle.decide",
        facts,
    )
    if not isinstance(decision, dict):
        raise RuntimeError("Chat session lifecycle decision must be an object")
    if decision.get("kind") == "reject":
        raise ValueError(f"attached Chat session rejected: {decision.get('code')}")
    return decision


def _strict_registry(
    registry_path: Path | None,
) -> dict[str, Any] | None:
    if registry_path is None or not registry_path.exists():
        return None
    registry = load_project_registry(registry_path)
    return registry if registry.get("profile_id") == SOURCE_SESSION_PROFILE_ID else None


def load_attached_session_registry(registry_path: Path) -> dict[str, Any]:
    """Load the source-aware registry for this qualified owner only."""

    return load_project_registry(registry_path)


def _select_current_session(
    *,
    store: ChatSessionStore,
    registry: Mapping[str, Any],
    current_ref: Mapping[str, str],
    goal_id: str,
    agent_id: str,
    channel_id: str,
) -> dict[str, Any] | None:
    candidates = store.resumable_session_candidates(
        goal_id=goal_id,
        agent_id=agent_id,
        channel_id=channel_id,
    )
    decision = _lifecycle_decision(
        operation="select",
        registry=registry,
        current_ref=current_ref,
        candidates=candidates,
    )
    if decision.get("kind") == "create":
        return None
    if decision.get("kind") != "reuse":
        raise RuntimeError("Chat session selection decision is unsupported")
    session_id = str(decision.get("session_id") or "")
    selected = next(
        (
            candidate
            for candidate in candidates
            if candidate.get("session_id") == session_id
        ),
        None,
    )
    if selected is None:
        raise RuntimeError("Chat session selection omitted its selected Session")
    return selected


def _bind_source_session(
    *,
    store: ChatSessionStore,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    host_surface: str,
    host_session_id: str,
    executor_endpoint_id: str,
    channel_id: str | None,
    execute: bool,
) -> dict[str, Any]:
    guard = guard_path(registry_path, goal_id)
    with exclusive_cross_runtime_file_lock(
        guard,
        operation="source_session_goal_lifetime",
    ):
        registry = load_project_registry(registry_path)
        current_ref, goal = current_goal_ref(registry, goal_id=goal_id)
        normalized_agent = _registered_agent(goal, agent_id)
        _require_bound_host(
            goal=goal,
            agent_id=normalized_agent,
            host_surface=host_surface,
            host_session_id=host_session_id,
        )
        selected_channel = channel_id or f"goal.{goal_id}"
        lock_path = _binding_lock_path(
            store,
            goal_id=goal_id,
            agent_id=normalized_agent,
            channel_id=selected_channel,
        )
        with exclusive_file_lock(
            lock_path,
            agent_id="loopx-chat",
            operation="bind_attached_agent_session",
        ):
            latest = _select_current_session(
                store=store,
                registry=registry,
                current_ref=current_ref,
                goal_id=goal_id,
                agent_id=normalized_agent,
                channel_id=selected_channel,
            )
            if latest is not None:
                exact_match = (
                    latest.get("session_mode") == CHAT_SESSION_MODE_ATTACHED
                    and latest.get("host_surface") == host_surface
                    and latest.get("upstream_thread_id") == host_session_id
                    and latest.get("executor_endpoint_id") == executor_endpoint_id
                )
                if not exact_match:
                    raise ValueError(
                        "an active working Session already exists for this "
                        "Goal, Agent, and channel"
                    )
                return _bind_result(
                    store=store,
                    session=latest,
                    execute=execute,
                    changed=False,
                    created=False,
                )
            if not execute:
                return {
                    "ok": True,
                    "schema_version": ATTACHED_SESSION_BROKER_SCHEMA_VERSION,
                    "action": "bind",
                    "execute": False,
                    "changed": True,
                    "created": False,
                    "binding": {
                        "goal_id": goal_id,
                        "agent_id": normalized_agent,
                        "executor_endpoint_id": executor_endpoint_id,
                        "host_surface": host_surface,
                        "channel_id": selected_channel,
                        "session_mode": CHAT_SESSION_MODE_ATTACHED,
                    },
                }
            session = store.create_session(
                goal_id=goal_id,
                goal_instance_id=current_ref["goal_instance_id"],
                agent_id=normalized_agent,
                executor_endpoint_id=executor_endpoint_id,
                adapter_kind=ATTACHED_SESSION_ADAPTER_KIND,
                upstream_thread_id=host_session_id,
                upstream_mode=ATTACHED_SESSION_UPSTREAM_MODE,
                channel_id=selected_channel,
                session_mode=CHAT_SESSION_MODE_ATTACHED,
                host_surface=host_surface,
                attached_capabilities={
                    "live_steering": False,
                    "session_queue": True,
                    "claim_wait": True,
                    "reply_readback": True,
                },
            )
    return _bind_result(
        store=store,
        session=session,
        execute=True,
        changed=True,
        created=True,
    )


def _bind_result(
    *,
    store: ChatSessionStore,
    session: Mapping[str, Any],
    execute: bool,
    changed: bool,
    created: bool,
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": ATTACHED_SESSION_BROKER_SCHEMA_VERSION,
        "action": "bind",
        "execute": execute,
        "changed": changed,
        "created": created,
        "session": store.public_session(dict(session)),
    }


def bind_attached_agent_session(
    *,
    store: ChatSessionStore,
    registry: dict[str, Any],
    registry_path: Path | None = None,
    goal_id: str,
    agent_id: str,
    host_surface: str,
    host_session_id: str,
    executor_endpoint_id: str,
    channel_id: str | None = None,
    execute: bool,
) -> dict[str, Any]:
    """Bind one existing host session without starting or resuming an adapter."""

    if registry.get("profile_id") == SOURCE_SESSION_PROFILE_ID:
        if registry_path is None:
            raise ValueError("source-session attached binding requires registry_path")
        return _bind_source_session(
            store=store,
            registry_path=registry_path,
            goal_id=goal_id,
            agent_id=agent_id,
            host_surface=host_surface,
            host_session_id=host_session_id,
            executor_endpoint_id=executor_endpoint_id,
            channel_id=channel_id,
            execute=execute,
        )
    goal = find_registry_goal(registry, goal_id)
    if goal is None:
        raise ValueError(f"goal_id not found in registry: {goal_id}")
    normalized_agent = _registered_agent(goal, agent_id)
    _require_bound_host(
        goal=goal,
        agent_id=normalized_agent,
        host_surface=host_surface,
        host_session_id=host_session_id,
    )
    selected_channel = channel_id or f"goal.{goal_id}"
    lock_path = _binding_lock_path(
        store,
        goal_id=goal_id,
        agent_id=normalized_agent,
        channel_id=selected_channel,
    )
    with exclusive_file_lock(
        lock_path,
        agent_id="loopx-chat",
        operation="bind_attached_agent_session",
    ):
        latest = store.latest_session(
            goal_id=goal_id,
            agent_id=normalized_agent,
            channel_id=selected_channel,
        )
        if latest is not None:
            exact_match = (
                latest.get("session_mode") == CHAT_SESSION_MODE_ATTACHED
                and latest.get("host_surface") == host_surface
                and latest.get("upstream_thread_id") == host_session_id
                and latest.get("executor_endpoint_id") == executor_endpoint_id
            )
            if not exact_match:
                raise ValueError(
                    "an active working Session already exists for this Goal, Agent, and channel"
                )
            return {
                "ok": True,
                "schema_version": ATTACHED_SESSION_BROKER_SCHEMA_VERSION,
                "action": "bind",
                "execute": execute,
                "changed": False,
                "created": False,
                "session": store.public_session(latest),
            }
        if not execute:
            return {
                "ok": True,
                "schema_version": ATTACHED_SESSION_BROKER_SCHEMA_VERSION,
                "action": "bind",
                "execute": False,
                "changed": True,
                "created": False,
                "binding": {
                    "goal_id": goal_id,
                    "agent_id": normalized_agent,
                    "executor_endpoint_id": executor_endpoint_id,
                    "host_surface": host_surface,
                    "channel_id": selected_channel,
                    "session_mode": CHAT_SESSION_MODE_ATTACHED,
                },
            }
        session = store.create_session(
            goal_id=goal_id,
            agent_id=normalized_agent,
            executor_endpoint_id=executor_endpoint_id,
            adapter_kind=ATTACHED_SESSION_ADAPTER_KIND,
            upstream_thread_id=host_session_id,
            upstream_mode=ATTACHED_SESSION_UPSTREAM_MODE,
            channel_id=selected_channel,
            session_mode=CHAT_SESSION_MODE_ATTACHED,
            host_surface=host_surface,
            attached_capabilities={
                "live_steering": False,
                "session_queue": True,
                "claim_wait": True,
                "reply_readback": True,
            },
        )
    return {
        "ok": True,
        "schema_version": ATTACHED_SESSION_BROKER_SCHEMA_VERSION,
        "action": "bind",
        "execute": True,
        "changed": True,
        "created": True,
        "session": store.public_session(session),
    }


def _require_attached_host(
    *,
    store: ChatSessionStore,
    session_id: str,
    host_surface: str,
    host_session_id: str,
    allow_closed: bool = False,
) -> dict[str, Any]:
    session = store.load_session(session_id)
    if session is None or (session.get("status") == "closed" and not allow_closed):
        raise KeyError("attached Agent session was not found")
    if session.get("session_mode") != CHAT_SESSION_MODE_ATTACHED:
        raise ValueError("the selected Session is not an attached host session")
    if (
        session.get("host_surface") != host_surface
        or session.get("upstream_thread_id") != host_session_id
    ):
        raise ValueError("attached host identity does not match the Session binding")
    return session


def select_current_attached_session(
    *,
    store: ChatSessionStore,
    registry_path: Path | None,
    goal_id: str,
    agent_id: str,
    channel_id: str,
) -> tuple[dict[str, Any] | None, bool]:
    """Select an attached Session and report whether strict identity is active."""

    if registry_path is not None and not registry_path.exists():
        candidates = store.session_candidates(
            goal_id=goal_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        if any(candidate.get("goal_instance_id") is not None for candidate in candidates):
            raise FileNotFoundError(registry_path)
    registry = _strict_registry(registry_path)
    if registry is None:
        return (
            store.latest_session(
                goal_id=goal_id,
                agent_id=agent_id,
                channel_id=channel_id,
            ),
            False,
        )
    assert registry_path is not None
    guard = guard_path(registry_path, goal_id)
    with exclusive_cross_runtime_file_lock(
        guard,
        operation="source_session_goal_lifetime",
    ):
        registry = load_project_registry(registry_path)
        current_ref, _goal = current_goal_ref(registry, goal_id=goal_id)
        session = _select_current_session(
            store=store,
            registry=registry,
            current_ref=current_ref,
            goal_id=goal_id,
            agent_id=agent_id,
            channel_id=channel_id,
        )
        if (
            session is not None
            and session.get("session_mode") != CHAT_SESSION_MODE_ATTACHED
        ):
            raise ValueError(
                "source-session managed Chat is not qualified for execution"
            )
        return session, True


@contextmanager
def _current_attached_session_guard(
    *,
    store: ChatSessionStore,
    registry_path: Path | None,
    session_id: str,
) -> Iterator[dict[str, Any]]:
    """Hold Goal lifetime authority while an exact attached Session is used."""

    session = store.load_session(session_id)
    if session is None or session.get("status") == "closed":
        raise KeyError("attached Agent session was not found")
    if session.get("session_mode") != CHAT_SESSION_MODE_ATTACHED:
        raise ValueError("the selected Session is not an attached host session")
    if session.get("goal_instance_id") is None:
        yield session
        return
    if registry_path is None:
        raise ValueError("source-session attached operation requires registry_path")
    goal_id = str(session.get("goal_id") or "")
    guard = guard_path(registry_path, goal_id)
    with exclusive_cross_runtime_file_lock(
        guard,
        operation="source_session_goal_lifetime",
    ):
        registry = load_project_registry(registry_path)
        current_ref, _goal = current_goal_ref(registry, goal_id=goal_id)
        session = store.load_session(session_id)
        if session is None or session.get("status") == "closed":
            raise KeyError("attached Agent session was not found")
        _lifecycle_decision(
            operation="admit",
            registry=registry,
            current_ref=current_ref,
            session=session,
        )
        yield session


def require_current_attached_session(
    *,
    store: ChatSessionStore,
    registry_path: Path | None,
    session_id: str,
) -> dict[str, Any]:
    """Reject stale exact sessions before granting new authority."""

    with _current_attached_session_guard(
        store=store,
        registry_path=registry_path,
        session_id=session_id,
    ) as session:
        return session


def resume_attached_agent_session(
    *,
    store: ChatSessionStore,
    registry_path: Path | None,
    session_id: str,
) -> dict[str, Any]:
    """Resume an attached Session while its Goal lifetime remains stable."""

    with _current_attached_session_guard(
        store=store,
        registry_path=registry_path,
        session_id=session_id,
    ) as session:
        if session.get("active_turn_id"):
            return session
        return store.update_session(
            session_id,
            status="ready",
            active_turn_id=None,
            last_error_code=None,
        )


def enqueue_attached_agent_turn(
    *,
    store: ChatSessionStore,
    registry_path: Path | None,
    session_id: str,
    client_turn_id: str,
    message: str,
    origin: str,
) -> tuple[dict[str, Any], bool]:
    """Enqueue work while the exact attached Session is current."""

    session = store.load_session(session_id)
    if session is None or session.get("status") == "closed":
        raise KeyError("chat session was not found")
    goal_instance_id = session.get("goal_instance_id")
    if goal_instance_id is None:
        return store.create_queued_turn(
            session_id,
            client_turn_id=client_turn_id,
            message=message,
            origin=origin,
        )
    if registry_path is None:
        raise ValueError("source-session attached enqueue requires registry_path")
    goal_id = str(session.get("goal_id") or "")
    guard = guard_path(registry_path, goal_id)
    with exclusive_cross_runtime_file_lock(
        guard,
        operation="source_session_goal_lifetime",
    ):
        registry = load_project_registry(registry_path)
        current_ref, _goal = current_goal_ref(registry, goal_id=goal_id)
        session = store.load_session(session_id)
        if session is None or session.get("status") == "closed":
            raise KeyError("chat session was not found")
        decision = _lifecycle_decision(
            operation="admit",
            registry=registry,
            current_ref=current_ref,
            session=session,
        )
        goal_ref = decision.get("goal_ref")
        if not isinstance(goal_ref, dict):
            raise RuntimeError("Chat enqueue admission omitted its GoalRef")
        return store.create_queued_turn(
            session_id,
            client_turn_id=client_turn_id,
            message=message,
            goal_instance_id=str(goal_ref["goal_instance_id"]),
            origin=origin,
        )


def _claim_attached_turn_once(
    *,
    store: ChatSessionStore,
    registry_path: Path | None,
    session_id: str,
    host_surface: str,
    host_session_id: str,
    claim_id: str,
) -> dict[str, Any] | None:
    session = _require_attached_host(
        store=store,
        session_id=session_id,
        host_surface=host_surface,
        host_session_id=host_session_id,
    )
    goal_instance_id = session.get("goal_instance_id")
    if goal_instance_id is None:
        return store.claim_next_queued_turn(
            session_id,
            host_claim_id=claim_id,
        )
    if registry_path is None:
        raise ValueError("source-session attached claim requires registry_path")
    goal_id = str(session.get("goal_id") or "")
    guard = guard_path(registry_path, goal_id)
    with exclusive_cross_runtime_file_lock(
        guard,
        operation="source_session_goal_lifetime",
    ):
        registry = load_project_registry(registry_path)
        current_ref, _goal = current_goal_ref(registry, goal_id=goal_id)
        session = _require_attached_host(
            store=store,
            session_id=session_id,
            host_surface=host_surface,
            host_session_id=host_session_id,
        )
        active_turn_id = str(session.get("active_turn_id") or "")
        active_turn = (
            store.load_turn(session_id, active_turn_id) if active_turn_id else None
        )
        replay = bool(
            active_turn
            and active_turn.get("host_claim_id") == claim_id
            and active_turn.get("status") in {"starting", "running"}
        )
        decision = _lifecycle_decision(
            operation="replay_claim" if replay else "claim",
            registry=registry,
            current_ref=current_ref,
            session=session,
            turn=active_turn if replay else None,
        )
        goal_ref = decision.get("goal_ref")
        if not isinstance(goal_ref, dict):
            raise RuntimeError("Chat claim admission omitted its GoalRef")
        return store.claim_next_queued_turn(
            session_id,
            host_claim_id=claim_id,
            admitted_goal_instance_id=str(goal_ref["goal_instance_id"]),
        )


def claim_attached_agent_turn(
    *,
    store: ChatSessionStore,
    registry_path: Path | None = None,
    session_id: str,
    host_surface: str,
    host_session_id: str,
    claim_id: str,
    wait_seconds: float = 0.0,
) -> dict[str, Any]:
    """Claim or bounded-wait for the oldest queued message for the exact host."""

    normalized_wait = float(wait_seconds)
    if (
        not math.isfinite(normalized_wait)
        or normalized_wait < 0
        or normalized_wait > MAX_ATTACHED_SESSION_CLAIM_WAIT_SECONDS
    ):
        raise ValueError(
            "wait_seconds must be between 0 and "
            f"{int(MAX_ATTACHED_SESSION_CLAIM_WAIT_SECONDS)}"
        )
    deadline = time.monotonic() + normalized_wait
    turn = None
    while turn is None:
        turn = _claim_attached_turn_once(
            store=store,
            registry_path=registry_path,
            session_id=session_id,
            host_surface=host_surface,
            host_session_id=host_session_id,
            claim_id=claim_id,
        )
        if turn is not None or normalized_wait == 0:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(ATTACHED_SESSION_CLAIM_POLL_INTERVAL_SECONDS, remaining))
    return {
        "ok": True,
        "schema_version": ATTACHED_SESSION_BROKER_SCHEMA_VERSION,
        "action": "claim",
        "claimed": turn is not None,
        "waited": normalized_wait > 0,
        "turn": (
            {
                "session_id": session_id,
                "turn_id": str(turn.get("turn_id") or ""),
                "client_turn_id": str(turn.get("client_turn_id") or ""),
                "claim_id": str(turn.get("host_claim_id") or ""),
                "origin": str(turn.get("origin") or "external"),
                "message": str(turn.get("message") or ""),
                "created_at": turn.get("created_at"),
                "expires_at": turn.get("expires_at"),
            }
            if turn is not None
            else None
        ),
    }


def complete_attached_agent_turn(
    *,
    store: ChatSessionStore,
    registry_path: Path | None = None,
    session_id: str,
    turn_id: str,
    host_surface: str,
    host_session_id: str,
    claim_id: str,
    completion_id: str,
    response: Mapping[str, Any],
) -> dict[str, Any]:
    """Write back one attached-host response with duplicate-safe receipts."""

    session = _require_attached_host(
        store=store,
        session_id=session_id,
        host_surface=host_surface,
        host_session_id=host_session_id,
        allow_closed=True,
    )
    normalized_response = normalize_agent_response(
        response,
        protected_paths=(store.root,),
    )
    message = str(normalized_response.get("message") or "")
    if not message:
        raise ValueError("response.message is required")
    if len(message) > 200_000:
        raise ValueError("response.message is too large")
    if session.get("goal_instance_id") is None:
        _turn, created = store.complete_attached_turn(
            session_id,
            turn_id,
            claim_id=claim_id,
            completion_id=completion_id,
            response=normalized_response,
            agent_message=message,
        )
    else:
        if registry_path is None:
            raise ValueError(
                "source-session attached completion requires registry_path"
            )
        goal_id = str(session.get("goal_id") or "")
        guard = guard_path(registry_path, goal_id)
        with exclusive_cross_runtime_file_lock(
            guard,
            operation="source_session_goal_lifetime",
        ):
            registry = load_project_registry(registry_path)
            current_ref, _goal = current_goal_ref(registry, goal_id=goal_id)
            session = _require_attached_host(
                store=store,
                session_id=session_id,
                host_surface=host_surface,
                host_session_id=host_session_id,
                allow_closed=True,
            )
            turn = store.load_turn(session_id, turn_id)
            _lifecycle_decision(
                operation="complete",
                registry=registry,
                current_ref=current_ref,
                session=session,
                turn=turn,
            )
            _turn, created = store.complete_attached_turn(
                session_id,
                turn_id,
                claim_id=claim_id,
                completion_id=completion_id,
                response=normalized_response,
                agent_message=message,
            )
    return {
        "ok": True,
        "schema_version": ATTACHED_SESSION_BROKER_SCHEMA_VERSION,
        "action": "complete",
        "completed": True,
        "created": created,
        "session_id": session_id,
        "turn_id": turn_id,
        "completion_id": completion_id,
    }


def render_attached_session_broker_markdown(payload: dict[str, Any]) -> str:
    action = str(payload.get("action") or "attached-session")
    if not payload.get("ok"):
        return f"# Attached Agent Session\n\n- Action: `{action}`\n- Error: {payload.get('error')}"
    if action == "claim":
        turn = payload.get("turn")
        if not isinstance(turn, dict):
            return "# Attached Agent Session\n\nNo queued message is available."
        return (
            "# Attached Agent Session\n\n"
            f"- Turn: `{turn.get('turn_id')}`\n"
            f"- Origin: `{turn.get('origin')}`\n\n"
            f"{turn.get('message')}"
        )
    session = payload.get("session")
    session_id = session.get("session_id") if isinstance(session, dict) else payload.get("session_id")
    return (
        "# Attached Agent Session\n\n"
        f"- Action: `{action}`\n"
        f"- Session: `{session_id or 'preview'}`\n"
        f"- Changed: `{bool(payload.get('changed') or payload.get('created'))}`"
    )
