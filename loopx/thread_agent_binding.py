"""Validated, project-local bindings between host threads and agent lanes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .control_plane.projects.registry_codec import mutate_project_registry
from .control_plane.runtime.public_safety import public_safe_compact_text
from .control_plane.todos.contract import normalize_todo_claimed_by
from .history import load_registry
from .registry import find_registry_goal, registry_goals

THREAD_ID_MAX_LENGTH = 128
THREAD_BINDING_SCHEMA_VERSION = "loopx_thread_agent_binding_v0"
THREAD_BINDING_RESOLUTION_SCHEMA_VERSION = "loopx_thread_agent_binding_resolution_v0"
HOST_SESSION_LOCATOR_SCHEMA_VERSION = "loopx_host_session_locator_v0"
AGENT_BINDING_ROUTE_SUMMARY_SCHEMA_VERSION = "loopx_agent_binding_route_summary_v0"
AGENT_BINDING_ROUTE_SCOPE_GOALS_SUPPLIED = "goals_supplied"
# Bounded display budget: a row reports a readable candidate list and the true
# distinct total, so a shorter list reads as a cap rather than as a remainder.
AGENT_BINDING_ROUTE_CANDIDATE_LIMIT = 3
AGENT_BINDING_ROUTE_OUTCOME_SINGLE = "single_candidate"
AGENT_BINDING_ROUTE_OUTCOME_MULTIPLE = "multiple_candidates"
AGENT_BINDING_ROUTE_OUTCOME_NONE = "no_candidate"
AGENT_BINDING_ROUTE_LIMITATION_WITHHELD = "candidate_withheld_by_public_boundary"
AGENT_BINDING_ROUTE_LIMITATION_TRUNCATED = "candidates_truncated_at_cap"
CODEX_THREAD_HOST_SURFACES = frozenset(
    {
        "codex-app",
        "codex-app-ssh",
        "codex-ide-plugin",
        "codex-cli-tui",
    }
)
_CODEX_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ThreadBindingRequestError(ValueError):
    """The caller supplied an invalid host-thread identity."""


@dataclass(frozen=True)
class _RegistryThreadBindingRequest:
    host_surface: str | None
    thread_id: str
    session_locator: dict[str, str] | None
    candidate_surfaces: tuple[str, ...]


def normalize_thread_id(value: Any) -> str | None:
    """Return a bounded opaque host token, or None for an omitted token."""

    if value is None:
        return None
    token = str(value).strip()
    if not token:
        return None
    if len(token) > THREAD_ID_MAX_LENGTH:
        raise ValueError(f"thread_id must be at most {THREAD_ID_MAX_LENGTH} characters")
    if any(char.isspace() or ord(char) < 32 for char in token):
        raise ValueError(
            "thread_id must be a public-safe opaque token without whitespace"
        )
    if any(char in token for char in ("/", "\\", '"', "'")):
        raise ValueError("thread_id must not contain path or quoting characters")
    return token


def codex_thread_deep_link_locator(value: Any) -> dict[str, str]:
    """Parse one canonical Codex task link without treating it as authority."""

    raw_link = str(value or "").strip()
    try:
        parsed = urlsplit(raw_link)
    except ValueError as exc:
        raise ValueError("thread_link must be a canonical Codex task deep link") from exc
    if (
        parsed.scheme.lower() != "codex"
        or parsed.netloc.lower() != "threads"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("thread_link must use codex://threads/<thread-id>")
    path_parts = parsed.path.split("/")
    if len(path_parts) != 2 or path_parts[0] or not path_parts[1] or "%" in path_parts[1]:
        raise ValueError("thread_link must address exactly one Codex thread")
    if path_parts[1].lower() == "new":
        raise ValueError("thread_link must address an existing Codex thread")
    thread_id = normalize_thread_id(path_parts[1])
    if thread_id is None or _CODEX_THREAD_ID_RE.fullmatch(thread_id) is None:
        raise ValueError("thread_link must include a Codex thread id")
    return {
        "schema_version": HOST_SESSION_LOCATOR_SCHEMA_VERSION,
        "kind": "codex_deep_link",
        "status": "parsed",
        "host_family": "codex",
        "thread_id": thread_id,
        "deep_link": f"codex://threads/{thread_id}",
        "context_scope_ref": f"host-session:codex:{thread_id}",
        "authority": "locator_only",
    }


def _normalized_host_surface(value: Any) -> str:
    surface = str(value or "").strip()
    if not surface:
        raise ValueError("host_surface is required for a thread binding")
    if len(surface) > 64 or any(char.isspace() for char in surface):
        raise ValueError("host_surface must be a compact public-safe token")
    return surface


def _bindings_for_goal(goal: dict[str, Any]) -> list[dict[str, str]]:
    coordination = goal.get("coordination")
    if not isinstance(coordination, dict):
        return []
    raw = coordination.get("thread_agent_bindings")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            thread_id = normalize_thread_id(item.get("thread_id"))
            host_surface = _normalized_host_surface(item.get("host_surface"))
            agent_id = normalize_todo_claimed_by(item.get("agent_id"))
        except ValueError:
            continue
        if thread_id and agent_id:
            result.append(
                {
                    "thread_id": thread_id,
                    "host_surface": host_surface,
                    "agent_id": agent_id,
                }
            )
    return result


def resolve_thread_agent_binding(
    goal: dict[str, Any] | None,
    *,
    host_surface: str,
    thread_id: str | None,
) -> dict[str, Any]:
    """Resolve a thread binding without guessing from registry order."""

    normalized_thread_id = normalize_thread_id(thread_id)
    normalized_surface = _normalized_host_surface(host_surface)
    base: dict[str, Any] = {
        "schema_version": THREAD_BINDING_SCHEMA_VERSION,
        "host_surface": normalized_surface,
        "thread_id": normalized_thread_id,
        "status": "unavailable" if not normalized_thread_id else "missing",
        "agent_id": None,
        "matches": [],
    }
    if not normalized_thread_id:
        return base
    matches = [
        item
        for item in _bindings_for_goal(goal or {})
        if item["host_surface"] == normalized_surface
        and item["thread_id"] == normalized_thread_id
    ]
    base["matches"] = matches
    agent_ids = sorted({item["agent_id"] for item in matches})
    if len(agent_ids) == 1:
        base["status"] = "bound"
        base["agent_id"] = agent_ids[0]
    elif len(agent_ids) > 1:
        base["status"] = "conflict"
        base["reason"] = "one thread is bound to multiple agent lanes"
    return base


def summarize_agent_binding_routes(goals: Any, *, agent_id: Any) -> dict[str, Any]:
    """Summarize the recorded bindings that address one Agent.

    This is the reverse direction of :func:`resolve_thread_agent_binding`, and it
    keeps the same discipline: it reports every binding the supplied Goals record
    for the Agent, in first-seen order, and never lets array order or recency
    stand in for "the route that should receive new work". It selects no
    execution route and grants no lease, capability or cross-host resume
    authority; this module remains the owner of exact-link resolution.

    `scope` is `goals_supplied`, so a single candidate here is a statement about
    these Goals only, not a project-level uniqueness claim. An unknown, empty or
    malformed `agent_id` returns `no_candidate` instead of matching loosely.
    """

    requested = normalize_todo_claimed_by(agent_id)
    goals_supplied = 0
    candidates: list[tuple[tuple[str, str], dict[str, str]]] = []
    seen_addresses: set[tuple[str, str]] = set()
    other_agent_addresses: set[tuple[str, str]] = set()
    withheld = 0

    for goal in goals if isinstance(goals, list) else []:
        if not isinstance(goal, dict):
            continue
        goals_supplied += 1
        if not requested:
            continue
        for binding in _bindings_for_goal(goal):
            address = (binding["host_surface"], binding["thread_id"])
            if binding["agent_id"] != requested:
                other_agent_addresses.add(address)
                continue
            if address in seen_addresses:
                # A binding republished across Goals is one candidate.
                continue
            seen_addresses.add(address)
            thread_id = public_safe_compact_text(
                binding["thread_id"], limit=THREAD_ID_MAX_LENGTH
            )
            host_surface = public_safe_compact_text(binding["host_surface"], limit=64)
            if not thread_id or not host_surface:
                # Withheld by the public boundary, still counted: the row must not
                # present a boundary as a disproved remainder.
                withheld += 1
                continue
            candidates.append(
                (address, {"thread_id": thread_id, "host_surface": host_surface})
            )

    if not seen_addresses:
        outcome = AGENT_BINDING_ROUTE_OUTCOME_NONE
    elif len(seen_addresses) == 1:
        outcome = AGENT_BINDING_ROUTE_OUTCOME_SINGLE
    else:
        outcome = AGENT_BINDING_ROUTE_OUTCOME_MULTIPLE

    limitations: list[str] = []
    if withheld:
        limitations.append(AGENT_BINDING_ROUTE_LIMITATION_WITHHELD)
    if len(candidates) > AGENT_BINDING_ROUTE_CANDIDATE_LIMIT:
        limitations.append(AGENT_BINDING_ROUTE_LIMITATION_TRUNCATED)

    return {
        "schema_version": AGENT_BINDING_ROUTE_SUMMARY_SCHEMA_VERSION,
        "agent_id": requested,
        "outcome": outcome,
        "address_shared": any(
            address in other_agent_addresses for address, _ in candidates
        ),
        "candidate_count": len(seen_addresses),
        "candidates": [
            candidate
            for _, candidate in candidates[:AGENT_BINDING_ROUTE_CANDIDATE_LIMIT]
        ],
        "scope": AGENT_BINDING_ROUTE_SCOPE_GOALS_SUPPLIED,
        "limitations": limitations,
        "provenance": {
            "source": "coordination.thread_agent_bindings",
            "goals_supplied": goals_supplied,
            "selects_route": False,
        },
    }


def _registry_thread_binding_request(
    *,
    host_surface: str | None,
    thread_id: str | None,
    thread_link: str | None,
) -> _RegistryThreadBindingRequest:
    if bool(thread_id) == bool(thread_link):
        raise ValueError("provide exactly one thread reference")
    normalized_surface = (
        _normalized_host_surface(host_surface)
        if host_surface is not None
        else None
    )
    session_locator = None
    if thread_link:
        if (
            normalized_surface is not None
            and normalized_surface not in CODEX_THREAD_HOST_SURFACES
        ):
            raise ValueError("Codex task deep links require a Codex host surface")
        session_locator = codex_thread_deep_link_locator(thread_link)
        normalized_thread_id = session_locator["thread_id"]
    else:
        if normalized_surface is None:
            raise ValueError("host_surface is required for a thread id")
        normalized_thread_id = normalize_thread_id(thread_id)
    if normalized_thread_id is None:
        raise ValueError("thread_id is required")
    candidate_surfaces = (
        (normalized_surface,)
        if normalized_surface is not None
        else tuple(sorted(CODEX_THREAD_HOST_SURFACES))
    )
    return _RegistryThreadBindingRequest(
        host_surface=normalized_surface,
        thread_id=normalized_thread_id,
        session_locator=session_locator,
        candidate_surfaces=candidate_surfaces,
    )


def _goal_registry_binding_matches(
    goal: dict[str, Any],
    *,
    thread_id: str,
    candidate_surfaces: tuple[str, ...],
) -> list[dict[str, str]]:
    raw_goal_id = goal.get("id")
    if (
        not isinstance(raw_goal_id, str)
        or not raw_goal_id
        or raw_goal_id.strip() != raw_goal_id
    ):
        return []
    matches: list[dict[str, str]] = []
    for candidate_surface in candidate_surfaces:
        binding = resolve_thread_agent_binding(
            goal,
            host_surface=candidate_surface,
            thread_id=thread_id,
        )
        if binding["status"] == "conflict":
            agent_ids = [item["agent_id"] for item in binding["matches"]]
        elif binding["status"] == "bound":
            agent_ids = [binding["agent_id"]]
        else:
            agent_ids = []
        matches.extend(
            {
                "goal_id": raw_goal_id,
                "agent_id": agent_id,
                "host_surface": candidate_surface,
            }
            for agent_id in agent_ids
        )
    return matches


def _registry_binding_resolution(
    request: _RegistryThreadBindingRequest,
    raw_matches: list[dict[str, str]],
) -> dict[str, Any]:
    unique_matches = sorted(
        {
            (item["goal_id"], item["agent_id"], item["host_surface"])
            for item in raw_matches
        }
    )
    matches = [
        {
            "goal_id": goal_id,
            "agent_id": agent_id,
            **(
                {"host_surface": matched_surface}
                if request.session_locator is not None
                else {}
            ),
        }
        for goal_id, agent_id, matched_surface in unique_matches
    ]
    identities = sorted({(item["goal_id"], item["agent_id"]) for item in matches})
    result: dict[str, Any] = {
        "ok": True,
        "schema_version": THREAD_BINDING_RESOLUTION_SCHEMA_VERSION,
        "host_surface": request.host_surface,
        "thread_id": request.thread_id,
        "status": "missing",
        "goal_id": None,
        "agent_id": None,
        "matches": matches,
    }
    if request.session_locator is not None:
        result["session_locator"] = request.session_locator
        result["host_family"] = "codex"
    if len(identities) == 1:
        goal_id, agent_id = identities[0]
        result.update(
            {
                "status": "bound",
                "goal_id": goal_id,
                "agent_id": agent_id,
            }
        )
        if request.session_locator is not None:
            result["matched_host_surfaces"] = sorted(
                item["host_surface"]
                for item in matches
                if item["goal_id"] == goal_id and item["agent_id"] == agent_id
            )
    elif len(identities) > 1:
        result.update(
            {
                "ok": False,
                "status": "ambiguous",
                "error_kind": "thread_agent_binding_ambiguous",
                "error": "host thread is bound to more than one Goal or Agent",
            }
        )
    return result


def resolve_registry_thread_agent_binding(
    *,
    registry_path: Path,
    host_surface: str | None = None,
    thread_id: str | None = None,
    thread_link: str | None = None,
) -> dict[str, Any]:
    """Resolve one exact host thread across every Goal in a project registry."""

    try:
        request = _registry_thread_binding_request(
            host_surface=host_surface,
            thread_id=thread_id,
            thread_link=thread_link,
        )
    except ValueError as exc:
        raise ThreadBindingRequestError("thread binding request is invalid") from exc
    payload = load_registry(registry_path)
    raw_matches = [
        match
        for goal in registry_goals(payload)
        for match in _goal_registry_binding_matches(
            goal,
            thread_id=request.thread_id,
            candidate_surfaces=request.candidate_surfaces,
        )
    ]
    return _registry_binding_resolution(request, raw_matches)


def _merge_thread_binding_entries(
    current: list[dict[str, str]], entry: dict[str, str]
) -> list[dict[str, str]]:
    merged = [
        item
        for item in current
        if not (
            item["thread_id"] == entry["thread_id"]
            and item["host_surface"] == entry["host_surface"]
        )
    ]
    merged.append(entry)
    merged.sort(
        key=lambda item: (item["host_surface"], item["thread_id"], item["agent_id"])
    )
    return merged


def _binding_context(
    payload: dict[str, Any],
    *,
    goal_id: str,
    host_surface: str,
    thread_id: str,
    agent_id: str,
) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, Any]]:
    goal = find_registry_goal(payload, goal_id)
    if goal is None:
        raise ValueError(f"goal_id not found in registry: {goal_id}")
    coordination = goal.get("coordination")
    coordination = coordination if isinstance(coordination, dict) else {}
    registered = coordination.get("registered_agents")
    registered_ids = {
        normalize_todo_claimed_by(item)
        for item in (registered if isinstance(registered, list) else [])
    }
    if agent_id not in registered_ids:
        raise ValueError(
            f"agent_id={agent_id!r} is not registered for goal {goal_id!r}"
        )
    current = _bindings_for_goal(goal)
    existing = resolve_thread_agent_binding(
        goal,
        host_surface=host_surface,
        thread_id=thread_id,
    )
    return goal, current, existing


def _prepare_binding(
    payload: dict[str, Any],
    *,
    goal_id: str,
    host_surface: str,
    thread_id: str,
    agent_id: str,
    execute: bool,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    goal, current, existing = _binding_context(
        payload,
        goal_id=goal_id,
        host_surface=host_surface,
        thread_id=thread_id,
        agent_id=agent_id,
    )
    if existing["status"] == "conflict":
        return (
            {
                "ok": False,
                "changed": False,
                "written": False,
                "error_kind": "thread_agent_binding_conflict",
                "binding": existing,
            },
            goal,
            current,
        )
    if existing["status"] == "bound" and existing["agent_id"] != agent_id:
        return (
            {
                "ok": False,
                "changed": False,
                "written": False,
                "error_kind": "thread_agent_binding_conflict",
                "error": "thread is already bound to a different agent; explicit unbind is required",
                "binding": existing,
            },
            goal,
            current,
        )

    entry = {"thread_id": thread_id, "host_surface": host_surface, "agent_id": agent_id}
    merged = _merge_thread_binding_entries(current, entry)
    result: dict[str, Any] = {
        "ok": True,
        "dry_run": not execute,
        "execute": execute,
        "goal_id": goal_id,
        "registry": "",
        "thread_id": thread_id,
        "host_surface": host_surface,
        "agent_id": agent_id,
        "changed": merged != current,
        "written": False,
        "binding": {
            "schema_version": THREAD_BINDING_SCHEMA_VERSION,
            **entry,
            "status": "bound",
        },
    }
    return result, goal, merged


def _prepare_unbinding(
    payload: dict[str, Any],
    *,
    goal_id: str,
    host_surface: str,
    thread_id: str,
    agent_id: str,
    execute: bool,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    goal, current, existing = _binding_context(
        payload,
        goal_id=goal_id,
        host_surface=host_surface,
        thread_id=thread_id,
        agent_id=agent_id,
    )
    result: dict[str, Any] = {
        "ok": True,
        "dry_run": not execute,
        "execute": execute,
        "goal_id": goal_id,
        "registry": "",
        "thread_id": thread_id,
        "host_surface": host_surface,
        "agent_id": agent_id,
        "changed": False,
        "written": False,
        "binding": {
            "schema_version": THREAD_BINDING_SCHEMA_VERSION,
            "thread_id": thread_id,
            "host_surface": host_surface,
            "agent_id": None,
            "status": "missing",
        },
    }
    if existing["status"] == "conflict":
        result.update(
            {
                "ok": False,
                "error_kind": "thread_agent_binding_conflict",
                "binding": existing,
            }
        )
        return result, goal, current
    if existing["status"] == "missing":
        return result, goal, current
    if existing["agent_id"] != agent_id:
        result.update(
            {
                "ok": False,
                "error_kind": "thread_agent_binding_agent_mismatch",
                "error": (
                    "thread binding does not match expected agent: "
                    f"{existing['agent_id']} != {agent_id}"
                ),
                "binding": existing,
            }
        )
        return result, goal, current

    remaining = [
        item
        for item in current
        if not (
            item["thread_id"] == thread_id
            and item["host_surface"] == host_surface
        )
    ]
    result["changed"] = remaining != current
    return result, goal, remaining


def bind_thread_agent_in_registry(
    *,
    registry_path: Path,
    goal_id: str,
    host_surface: str,
    thread_id: str,
    agent_id: str,
    execute: bool,
) -> dict[str, Any]:
    """Preview or atomically bind an already registered agent to a host thread."""

    normalized_thread_id = normalize_thread_id(thread_id)
    if normalized_thread_id is None:
        raise ValueError("thread_id is required")
    normalized_surface = _normalized_host_surface(host_surface)
    normalized_agent = normalize_todo_claimed_by(agent_id)
    if not normalized_agent:
        raise ValueError("agent_id must be a public-safe registered agent id")

    if execute:
        def reduce(latest: dict[str, Any]) -> dict[str, Any]:
            result, latest_goal, merged = _prepare_binding(
                latest,
                goal_id=goal_id,
                host_surface=normalized_surface,
                thread_id=normalized_thread_id,
                agent_id=normalized_agent,
                execute=True,
            )
            result["registry"] = str(registry_path)
            if not result["ok"] or not result["changed"]:
                return result
            coordination = latest_goal.get("coordination")
            coordination = coordination if isinstance(coordination, dict) else {}
            coordination["thread_agent_bindings"] = merged
            latest_goal["coordination"] = coordination
            result["written"] = True
            return result

        return mutate_project_registry(
            registry_path,
            agent_id=normalized_agent,
            operation="bind_agent_thread",
            reducer=reduce,
        )

    payload = load_registry(registry_path)
    result, _goal, _merged = _prepare_binding(
        payload,
        goal_id=goal_id,
        host_surface=normalized_surface,
        thread_id=normalized_thread_id,
        agent_id=normalized_agent,
        execute=False,
    )
    result["registry"] = str(registry_path)
    return result


def unbind_thread_agent_in_registry(
    *,
    registry_path: Path,
    goal_id: str,
    host_surface: str,
    thread_id: str,
    agent_id: str,
    execute: bool,
) -> dict[str, Any]:
    """Preview or atomically remove one exact thread-to-agent binding."""

    normalized_thread_id = normalize_thread_id(thread_id)
    if normalized_thread_id is None:
        raise ValueError("thread_id is required")
    normalized_surface = _normalized_host_surface(host_surface)
    normalized_agent = normalize_todo_claimed_by(agent_id)
    if not normalized_agent:
        raise ValueError("agent_id must be a public-safe registered agent id")

    if execute:
        def reduce(latest: dict[str, Any]) -> dict[str, Any]:
            result, latest_goal, remaining = _prepare_unbinding(
                latest,
                goal_id=goal_id,
                host_surface=normalized_surface,
                thread_id=normalized_thread_id,
                agent_id=normalized_agent,
                execute=True,
            )
            result["registry"] = str(registry_path)
            if not result["ok"] or not result["changed"]:
                return result
            coordination = latest_goal.get("coordination")
            coordination = coordination if isinstance(coordination, dict) else {}
            coordination["thread_agent_bindings"] = remaining
            latest_goal["coordination"] = coordination
            result["written"] = True
            return result

        return mutate_project_registry(
            registry_path,
            agent_id=normalized_agent,
            operation="unbind_agent_thread",
            reducer=reduce,
        )

    payload = load_registry(registry_path)
    result, _goal, _remaining = _prepare_unbinding(
        payload,
        goal_id=goal_id,
        host_surface=normalized_surface,
        thread_id=normalized_thread_id,
        agent_id=normalized_agent,
        execute=False,
    )
    result["registry"] = str(registry_path)
    return result
