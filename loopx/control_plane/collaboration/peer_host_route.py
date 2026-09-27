"""Resolve a named peer to one observed host task without granting delivery.

The thread-binding owner decides identity. A host observer can only establish
that its local record for that exact task is currently readable; sending work
and receiver adoption remain separate effects with their own receipts.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...agent_registry import registered_agent_ids_for_goal
from ...codex_app_thread_activity import codex_thread_observers
from ...control_plane.agents.host_thread_activity import (
    HostThreadObserver,
    HostThreadState,
    HostThreadUnknownReason,
)
from ...control_plane.runtime.public_safety import validate_public_safe_value
from ...history import load_registry
from ...registry import find_registry_goal
from ...thread_agent_binding import (
    CODEX_THREAD_HOST_SURFACES,
    codex_thread_deep_link_locator,
    resolve_registry_thread_agent_binding,
    summarize_agent_binding_routes,
)


PEER_HOST_ROUTE_SCHEMA_VERSION = "loopx_peer_host_route_v0"
MAX_PUBLISHED_CANDIDATES = 3


def resolve_peer_host_route(
    registry_path: Path,
    *,
    goal_id: str,
    agent_id: str,
    thread_link: str | None = None,
    host_surface: str | None = None,
    observers: Mapping[str, HostThreadObserver] | None = None,
) -> dict[str, Any]:
    """Preview an exact same-Goal peer route, failing closed on ambiguity.

    A `resolved` route means registry identity and a local host record agree.
    It does not attest that the host accepted a new message, that the receiver
    read it, or that this caller has permission to submit to that host.
    """

    result: dict[str, Any] = {
        "ok": True,
        "schema_version": PEER_HOST_ROUTE_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "status": "unavailable",
        "reason": "no_binding",
        "candidate_count": 0,
        "candidates": [],
        "selected_route": None,
        "host_delivery": "not_attempted",
        "authority": "locator_only",
    }
    registry = load_registry(registry_path)
    goal = find_registry_goal(registry, goal_id)
    if goal is None or agent_id not in registered_agent_ids_for_goal(goal):
        result.update(status="not_authorized", reason="peer_not_registered")
        return result

    summary = summarize_agent_binding_routes([goal], agent_id=agent_id)
    candidates = summary["candidates"]
    visible: list[dict[str, str]] = []
    withheld = 0
    for candidate in candidates:
        try:
            validate_public_safe_value(candidate, path="peer_host_route.candidate")
        except ValueError:
            withheld += 1
        else:
            visible.append(candidate)
    result["candidate_count"] = len(candidates)
    result["candidates"] = visible[:MAX_PUBLISHED_CANDIDATES]
    if withheld:
        result["withheld_candidate_count"] = withheld

    if thread_link is not None:
        try:
            thread_id = codex_thread_deep_link_locator(thread_link)["thread_id"]
        except ValueError:
            result["reason"] = "invalid_thread_link"
            return result
        matching = [
            item
            for item in candidates
            if item["thread_id"] == thread_id
            and item["host_surface"] in CODEX_THREAD_HOST_SURFACES
            and (host_surface is None or item["host_surface"] == host_surface)
        ]
        if not matching:
            result.update(status="not_authorized", reason="thread_not_bound_to_peer")
            return result
    else:
        matching = [
            item
            for item in candidates
            if host_surface is None or item["host_surface"] == host_surface
        ]

    if not matching:
        return result
    if len(matching) != 1:
        result.update(status="ambiguous", reason="multiple_binding_candidates")
        return result
    selected = matching[0]
    if selected not in visible:
        result["reason"] = "route_candidate_withheld"
        return result
    exact = resolve_registry_thread_agent_binding(
        registry_path=registry_path,
        host_surface=selected["host_surface"],
        thread_id=selected["thread_id"],
    )
    if exact["status"] != "bound" or (exact["goal_id"], exact["agent_id"]) != (
        goal_id,
        agent_id,
    ):
        result.update(status="ambiguous", reason="binding_identity_conflict")
        return result

    available_observers = codex_thread_observers() if observers is None else observers
    observer = available_observers.get(selected["host_surface"])
    if observer is None:
        result["reason"] = "host_observer_unavailable"
        return result
    try:
        observation = observer([selected["thread_id"]]).get(selected["thread_id"])
    except Exception:  # noqa: BLE001 - host read failure cannot authorize delivery.
        result["reason"] = "host_observation_failed"
        return result
    if observation is None:
        result["reason"] = HostThreadUnknownReason.THREAD_NOT_FOUND.value
        return result
    result["host_observation"] = observation.to_payload()
    if observation.state is HostThreadState.ARCHIVED:
        result["reason"] = "host_thread_archived"
    elif observation.state is HostThreadState.UNKNOWN:
        result["reason"] = (
            observation.reason.value if observation.reason else "host_unknown"
        )
    else:
        result.update(status="resolved", reason=None, selected_route=selected)
    return result
