"""Build the Agent-facing peer directory packet from existing LoopX state.

`peer_agent_directory_v0`
(`docs/reference/protocols/peer-agent-directory-and-observation-v0.md`) answers
what a peer Agent, or the steward Agent a person talks to, may see about the
other Agents of one Goal. TypeScript owns the directory packet; this module
adapts its existing sources.
It is a re-projection: the Agent rows come from the existing agent management
projection, so identity, work, claims and staleness keep their current owners
and this module adds no second read of the registry, the Todo index or a lease
store.

What it deliberately cannot do:

- it writes nothing, grants nothing and schedules nothing;
- it reports no presence, because no presence provider is registered, and it
  says so in `presence_coverage` instead of leaving a reader to guess between
  "not running" and "this machine cannot see it";
- it projects lease state only when the status payload carries execution
  facts; a payload without them gets `lease_state_not_projected` instead of
  a guess.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..runtime.public_safety import public_safe_compact_text
from ..runtime.time import now_utc_iso
from ..effect_runtime import effect_runtime_result
from ...thread_agent_binding import summarize_agent_binding_routes
from ..runtime.public_safety import validate_public_safe_value
from ..todos.contract import normalize_todo_id
from .management_projection import (
    build_agent_management_projection,
    registered_agent_ids,
)


PEER_AGENT_DIRECTORY_SCHEMA_VERSION = "peer_agent_directory_v0"
MAX_DIRECTORY_ROWS = 24
MAX_ROUTE_CANDIDATES = 3

# Limitation codes are contract vocabulary, not prose: a reader switches on them.
LIMITATION_PRESENCE_PROVIDER_UNAVAILABLE = "presence_provider_unavailable"
LIMITATION_PRESENCE_IS_ADVISORY = "presence_is_advisory"
LIMITATION_LEASE_STATE_NOT_PROJECTED = "lease_state_not_projected"
LIMITATION_CALLER_IDENTITY_NOT_SUPPLIED = "caller_identity_not_supplied"
LIMITATION_ROWS_TRUNCATED = "rows_truncated_at_cap"
LIMITATION_ROUTE_CANDIDATE_WITHHELD = "route_candidate_withheld"

GAP_AUDIENCE_NOT_AUTHORIZED = "audience_not_authorized"


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _compact(value: Any, *, limit: int = 200) -> str | None:
    return public_safe_compact_text(value, limit=limit)


def _safe_refs(value: Any) -> list[str]:
    refs: list[str] = []
    for item in _as_list(value):
        text = _compact(item, limit=160)
        if text:
            refs.append(text)
    return refs


def _publishable_route(route: dict[str, Any]) -> dict[str, Any]:
    """Withhold entries the existing public boundary rejects, without capping.

    Withholding is reported, never silent: `candidate_count` keeps the withheld
    entries, so a filtered candidate cannot be read as a binding that does not
    exist. Values are never truncated to make them pass, because a half a thread
    identifier is a locator that no longer locates.
    """

    visible: list[dict[str, str]] = []
    withheld = 0
    for candidate in route["candidates"]:
        try:
            validate_public_safe_value(candidate, path="peer_route.candidate")
        except ValueError:
            withheld += 1
            continue
        visible.append(candidate)
    published = {key: value for key, value in route.items() if key != "candidates"}
    published["candidates"] = visible
    if withheld:
        published["withheld_candidate_count"] = withheld
    return published


def _projected_execution_facts(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Recover the execution facts status collection projected onto agent rows.

    ``None`` when the payload's projection says no facts were collected, so an
    older or fact-less payload keeps `lease_state_not_projected` instead of
    reading as "leases projected, none found".
    """

    projection = _as_mapping(payload.get("agent_management_projection"))
    summary = _as_mapping(projection.get("source_summary"))
    if summary.get("execution_facts_collected") is not True:
        return None
    facts: dict[str, Any] = {}
    for row in _as_list(projection.get("agents")):
        row = _as_mapping(row)
        agent_id = str(row.get("agent_id") or "").strip()
        execution = row.get("execution")
        if agent_id and isinstance(execution, Mapping):
            facts[agent_id] = dict(execution)
    return facts


def _safe_agent_facts(row: Mapping[str, Any]) -> dict[str, Any]:
    """Apply existing public safety, leaving directory rules to TypeScript.

    Preserve fallback-before-compaction: a rejected preferred title must not
    expose its fallback. This boundary supplies facts, not work classification.
    """
    todo = _as_mapping(row.get("current_todo"))
    todo_id = _compact(todo.get("todo_id"), limit=80)
    lease = _as_mapping(_as_mapping(row.get("execution")).get("lease"))
    return {
        "agent_id": _compact(row.get("agent_id"), limit=120),
        "execution_fact_agent_id": str(row.get("agent_id") or "").strip(),
        "agent_model": _compact(row.get("agent_model"), limit=60),
        "current_todo": {
            "todo_id": normalize_todo_id(todo_id) or todo_id,
            "status": _compact(todo.get("status"), limit=40),
            "task_class": _compact(todo.get("task_class"), limit=60),
            "action_kind": _compact(todo.get("action_kind"), limit=60),
            "priority": _compact(todo.get("priority"), limit=20),
            "claimed_by": _compact(todo.get("claimed_by"), limit=120),
            "updated_at": _compact(todo.get("updated_at"), limit=60),
            "title": _compact(todo.get("title") or todo.get("text"), limit=220),
        },
        "last_activity_at": _compact(
            row.get("last_activity_at") or todo.get("updated_at"), limit=60
        ),
        "stale_claim_hint": {
            "state": _compact(_as_mapping(row.get("stale_claim_hint")).get("state"), limit=60),
        },
        "execution": {"lease": {
            "status": _compact(lease.get("status"), limit=40),
            "expired": lease.get("expired") if isinstance(lease.get("expired"), bool) else None,
        }},
        "evidence_refs": _safe_refs(row.get("evidence_refs")),
        "handoff_refs": _safe_refs(row.get("handoff_refs")),
    }


def build_peer_agent_directory(
    status_payload: Mapping[str, Any],
    *,
    goal_id: str | None = None,
    caller_agent_id: str | None = None,
    available_capabilities: Any = None,
    execution_facts: Any = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Collect existing facts and invoke the sole typed directory owner once.

    Management selection and binding interpretation retain their owners. This
    adapter normalizes inputs, applies public safety, and transports facts.
    Failure never falls back to Python directory decisions.
    """
    payload = status_payload if isinstance(status_payload, Mapping) else {}
    cursor_value = _compact(cursor, limit=120)
    if cursor and not cursor_value:
        raise ValueError("cursor is not a publishable agent id")
    if execution_facts is None:
        execution_facts = _projected_execution_facts(payload)
    facts = execution_facts if isinstance(execution_facts, Mapping) else None
    status = dict(payload)
    projection = build_agent_management_projection(
        status,
        available_capabilities=available_capabilities,
        execution_facts=dict(facts) if facts is not None else None,
        after_agent_id=cursor_value,
    )
    agent_rows = [row for row in _as_list(projection.get("agents")) if isinstance(row, Mapping)]
    binding_goals = _as_list(_as_mapping(payload.get("run_history")).get("goals"))
    agents = [_safe_agent_facts(row) for row in agent_rows]
    routes = {
        row["agent_id"]: _publishable_route(
            summarize_agent_binding_routes(binding_goals, agent_id=row["agent_id"])
        )
        for row in agents if row["agent_id"]
    }
    upstream_envelopes: list[Mapping[str, Any]] = []
    for source in (payload, _as_mapping(payload.get("agent_management_projection")), projection):
        envelope = source.get("projection_envelope")
        if isinstance(envelope, Mapping) and envelope not in upstream_envelopes:
            upstream_envelopes.append(envelope)
    result = effect_runtime_result("agent.directory.project", {
        "schema_version": "peer_agent_directory_project_request_v0",
        "observed_at": now_utc_iso(),
        "goal_id": _compact(goal_id or payload.get("goal_filter"), limit=120),
        "caller_agent_id": _compact(caller_agent_id, limit=120),
        "cursor": cursor_value,
        "registered_agent_ids": sorted(registered_agent_ids(status)),
        "agents": agents,
        "source_summary": dict(_as_mapping(projection.get("source_summary"))),
        "execution_fact_agent_ids": list(facts) if facts is not None else None,
        "routes": routes,
        "upstream_envelopes": upstream_envelopes,
    })
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != PEER_AGENT_DIRECTORY_SCHEMA_VERSION
        or result.get("ok") is not True
        or not isinstance(result.get("rows"), list)
        or not isinstance(result.get("projection_envelope"), dict)
    ):
        raise TypeError("invalid peer agent directory projection result")
    return result
