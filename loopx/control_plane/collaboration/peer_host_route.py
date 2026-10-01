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
    HostThreadActivity,
    HostThreadObserver,
    HostThreadState,
    HostThreadUnknownReason,
    MAX_OBSERVED_THREADS_PER_GOAL,
)
from ...control_plane.runtime.public_safety import validate_public_safe_value
from ...registry import find_registry_goal
from ...thread_agent_binding import (
    CODEX_THREAD_HOST_SURFACES,
    codex_thread_deep_link_locator,
    resolve_registry_thread_agent_binding,
    summarize_agent_binding_routes,
)
from ..effect_runtime import effect_runtime_result
from ..projects.registry_codec import load_registry


PEER_HOST_ROUTE_SCHEMA_VERSION = "loopx_peer_host_route_v0"
MAX_PUBLISHED_CANDIDATES = 3


def _matching_bindings(
    candidates: list[dict[str, str]], *, thread_id: str | None, host_surface: str | None,
) -> list[dict[str, str]]:
    return [
        item for item in candidates
        if (host_surface is None or item["host_surface"] == host_surface)
        and (thread_id is None or (
            item["thread_id"] == thread_id and item["host_surface"] in CODEX_THREAD_HOST_SURFACES
        ))
    ]


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

    thread_id = None
    if thread_link is not None:
        try:
            thread_id = codex_thread_deep_link_locator(thread_link)["thread_id"]
        except ValueError:
            result["reason"] = "invalid_thread_link"
            return result
    matching = _matching_bindings(candidates, thread_id=thread_id, host_surface=host_surface)
    if thread_link is not None and not matching:
        result.update(status="not_authorized", reason="thread_not_bound_to_peer")
        return result

    if not matching:
        return result
    if len(matching) > MAX_OBSERVED_THREADS_PER_GOAL:
        result.update(status="ambiguous", reason="multiple_binding_candidates")
        return result
    if len(matching) == 1 and matching[0] not in visible:
        result["reason"] = "route_candidate_withheld"
        return result
    available_observers = codex_thread_observers() if observers is None else observers
    # Read each host once, including every accepted alternative. A publication
    # cap or an unreadable host must not turn an unknown binding into history.
    requested: dict[str, set[str]] = {}
    for candidate in matching:
        if candidate in visible:
            requested.setdefault(candidate["host_surface"], set()).add(candidate["thread_id"])
    observed: dict[str, Mapping[str, HostThreadActivity]] = {}
    failures: dict[str, str] = {}
    for surface, ids in requested.items():
        observer = available_observers.get(surface)
        if observer is None:
            failures[surface] = "host_observer_unavailable"
            continue
        try:
            observed[surface] = observer(sorted(ids))
        except Exception:  # noqa: BLE001 - host failure remains an unknown alternative.
            failures[surface] = "host_observation_failed"
    facts: list[dict[str, str]] = []
    activity: list[HostThreadActivity | None] = []
    for candidate in matching:
        surface, thread = candidate["host_surface"], candidate["thread_id"]
        item = observed.get(surface, {}).get(thread)
        activity.append(item)
        if candidate not in visible:
            reason = "route_candidate_withheld"
        elif surface in failures:
            reason = failures[surface]
        elif item is None:
            reason = HostThreadUnknownReason.THREAD_NOT_FOUND.value
        elif item.state is HostThreadState.UNKNOWN:
            assert item.reason is not None  # HostThreadActivity's constructor invariant.
            reason = item.reason.value
        else:
            facts.append({"state": item.state.value})
            continue
        facts.append({"state": "unavailable", "reason": reason})
    if len(activity) == 1 and activity[0] is not None:
        result["host_observation"] = activity[0].to_payload()
    selection = effect_runtime_result("collaboration.peer_host_route.select", {"observations": facts})
    if selection["status"] != "resolved":
        result.update(status=selection["status"], reason=selection["reason"])
        return result
    index = selection["selected_index"]
    selected = matching[index]
    # Host I/O must not hide a newly bound alternative or a revoked registration.
    latest_goal = find_registry_goal(load_registry(registry_path), goal_id)
    if latest_goal is None or agent_id not in registered_agent_ids_for_goal(latest_goal):
        result.update(status="not_authorized", reason="peer_not_registered")
        return result
    latest = _matching_bindings(
        summarize_agent_binding_routes([latest_goal], agent_id=agent_id)["candidates"],
        thread_id=thread_id, host_surface=host_surface,
    )
    if {(item["host_surface"], item["thread_id"]) for item in latest} != {
        (item["host_surface"], item["thread_id"]) for item in matching
    }:
        result.update(status="ambiguous", reason="binding_identity_conflict")
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

    selected_activity = activity[index]
    assert selected_activity is not None  # The typed selector requires a readable observation.
    result["host_observation"] = selected_activity.to_payload()
    result.update(status="resolved", reason=None, selected_route=selected)
    return result
