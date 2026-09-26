"""Build the Agent-facing peer directory packet from existing LoopX state.

`peer_agent_directory_v0`
(`docs/reference/protocols/peer-agent-directory-and-observation-v0.md`) answers
what a peer Agent, or the steward Agent a person talks to, may see about the
other Agents of one Goal. This module is that contract's first local producer.
It is a re-projection: the Agent rows come from the existing agent management
projection, so identity, work, claims and staleness keep their current owners
and this module adds no second read of the registry, the Todo index or a lease
store.

What it deliberately cannot do:

- it writes nothing, grants nothing and schedules nothing;
- it reports no presence, because no presence provider is registered, and it
  says so in `presence_coverage` instead of leaving a reader to guess between
  "not running" and "this machine cannot see it";
- it does not project a lease epoch, which the current projection does not own,
  and it names that gap as a limitation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ...thread_agent_binding import summarize_agent_binding_routes
from ..runtime.public_safety import public_safe_compact_text
from ..runtime.time import now_utc_iso
from ..todos.contract import normalize_todo_id
from .management_projection import build_agent_management_projection


PEER_AGENT_DIRECTORY_SCHEMA_VERSION = "peer_agent_directory_v0"
PEER_AGENT_DIRECTORY_SCOPE = "goal_registered_agents"
MAX_DIRECTORY_ROWS = 24
MAX_OBSERVATION_REFS = 1
MAX_DELIVERY_REFS = 1
MAX_ROLLUP_AGENTS = 8

# Limitation codes are contract vocabulary, not prose: a reader switches on them.
LIMITATION_PRESENCE_PROVIDER_UNAVAILABLE = "presence_provider_unavailable"
LIMITATION_PRESENCE_IS_ADVISORY = "presence_is_advisory"
LIMITATION_LEASE_STATE_NOT_PROJECTED = "lease_state_not_projected"
LIMITATION_CALLER_IDENTITY_NOT_SUPPLIED = "caller_identity_not_supplied"
LIMITATION_ROWS_TRUNCATED = "rows_truncated_at_cap"

GAP_AUDIENCE_NOT_AUTHORIZED = "audience_not_authorized"

CLAIM_AGE_SUSPECTED_STALE = "suspected_stale"
CLAIM_AGE_FRESH = "fresh"
CLAIM_AGE_UNKNOWN = "unknown"


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _compact(value: Any, *, limit: int = 200) -> str | None:
    return public_safe_compact_text(value, limit=limit)


def _compact_refs(value: Any, *, limit: int) -> list[str]:
    refs: list[str] = []
    for item in _as_list(value):
        text = _compact(item, limit=160)
        if text and text not in refs:
            refs.append(text)
        if len(refs) >= limit:
            break
    return refs


def _work_block(agent_row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project one Agent's bounded work facts, or nothing when it holds none."""

    todo = _as_mapping(agent_row.get("current_todo"))
    todo_id = _compact(todo.get("todo_id"), limit=80)
    if not todo_id:
        return None
    claimed_by = _compact(todo.get("claimed_by"), limit=120)
    stale_hint = _as_mapping(agent_row.get("stale_claim_hint"))
    hint_state = _compact(stale_hint.get("state"), limit=60)
    claim_age_state = (
        CLAIM_AGE_SUSPECTED_STALE
        if hint_state == CLAIM_AGE_SUSPECTED_STALE
        else CLAIM_AGE_FRESH
        if claimed_by and _compact(todo.get("updated_at"), limit=60)
        else CLAIM_AGE_UNKNOWN
    )
    work: dict[str, Any] = {
        "todo_id": normalize_todo_id(todo_id) or todo_id,
        "todo_status": _compact(todo.get("status"), limit=40) or "unknown",
        "task_class": _compact(todo.get("task_class"), limit=60),
        "action_kind": _compact(todo.get("action_kind"), limit=60),
        "priority": _compact(todo.get("priority"), limit=20),
        "claimed": bool(claimed_by),
        "claimed_by": claimed_by,
        "claim_age_state": claim_age_state,
        "last_activity_at": _compact(
            agent_row.get("last_activity_at") or todo.get("updated_at"), limit=60
        ),
        "title": _compact(todo.get("title") or todo.get("text"), limit=220),
    }
    return {key: value for key, value in work.items() if value is not None and value != ""}


def _rollup(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """A typed "who needs a decision" ordering; it assigns nothing."""

    needs_decision: list[str] = []
    stale_claims: list[str] = []
    unclaimed: list[str] = []
    for row in rows:
        agent_id = _compact(row.get("agent_id"), limit=120)
        if not agent_id:
            continue
        work = _as_mapping(row.get("work"))
        if str(work.get("todo_status") or "") == "blocked":
            needs_decision.append(agent_id)
        if str(work.get("claim_age_state") or "") == CLAIM_AGE_SUSPECTED_STALE:
            stale_claims.append(agent_id)
        if not work.get("claimed"):
            unclaimed.append(agent_id)
    return {
        "basis": "typed_work_state_only",
        "assigns_work": False,
        "needs_decision": needs_decision[:MAX_ROLLUP_AGENTS],
        "stale_claims": stale_claims[:MAX_ROLLUP_AGENTS],
        "without_claim": unclaimed[:MAX_ROLLUP_AGENTS],
        "counts": {
            "needs_decision": len(needs_decision),
            "stale_claims": len(stale_claims),
            "without_claim": len(unclaimed),
        },
    }


def build_peer_agent_directory(
    status_payload: Mapping[str, Any],
    *,
    goal_id: str | None = None,
    caller_agent_id: str | None = None,
    available_capabilities: Any = None,
) -> dict[str, Any]:
    """Return a bounded `peer_agent_directory_v0` packet for one Goal.

    `caller_agent_id` is optional. When it is supplied the caller must be a
    registered Agent of the Goal, since membership in this space is proven
    against the registry rather than asserted by the caller; when it is absent
    the packet records that the caller identity was not supplied instead of
    inventing one.
    """

    payload = status_payload if isinstance(status_payload, Mapping) else {}
    resolved_goal = _compact(goal_id or payload.get("goal_filter"), limit=120)
    caller = _compact(caller_agent_id, limit=120)
    projection = build_agent_management_projection(
        dict(payload), available_capabilities=available_capabilities
    )
    agent_rows = [row for row in _as_list(projection.get("agents")) if isinstance(row, Mapping)]
    registered_agent_ids = [
        agent_id
        for agent_id in (_compact(row.get("agent_id"), limit=120) for row in agent_rows)
        if agent_id
    ]
    # The bindings live on the same run history the rows are projected from.
    goals = _as_mapping(payload.get("run_history")).get("goals")

    limitations = [
        LIMITATION_PRESENCE_PROVIDER_UNAVAILABLE,
        LIMITATION_PRESENCE_IS_ADVISORY,
        LIMITATION_LEASE_STATE_NOT_PROJECTED,
    ]
    gaps: list[dict[str, Any]] = []
    if caller and caller not in registered_agent_ids:
        # An unregistered caller gets a scope gap, never a listing it has no
        # scope over, and never a silent empty directory either.
        gaps.append(
            {
                "kind": GAP_AUDIENCE_NOT_AUTHORIZED,
                "detail": "caller_agent_id_is_not_registered_for_this_goal",
                "caller_agent_id": caller,
            }
        )
    if not caller:
        limitations.append(LIMITATION_CALLER_IDENTITY_NOT_SUPPLIED)

    rows: list[dict[str, Any]] = []
    dropped_at_cap = 0
    for row in agent_rows:
        agent_id = _compact(row.get("agent_id"), limit=120)
        if not agent_id:
            continue
        if gaps:
            break
        if len(rows) >= MAX_DIRECTORY_ROWS:
            dropped_at_cap += 1
            continue
        work = _work_block(row)
        directory_row: dict[str, Any] = {
            "agent_id": agent_id,
            "registered": True,
            "agent_model": _compact(row.get("agent_model"), limit=60),
            "work": work,
            # What the recorded bindings say about addressing this peer: a bounded
            # candidate summary plus the true distinct count, never one binding
            # silently selected for the row. It selects no route either.
            "peer_route": summarize_agent_binding_routes(goals, agent_id=agent_id),
            "observation_refs": _compact_refs(
                row.get("evidence_refs"), limit=MAX_OBSERVATION_REFS
            ),
            "delivery_refs": _compact_refs(
                row.get("handoff_refs"), limit=MAX_DELIVERY_REFS
            ),
        }
        rows.append(
            {
                key: value
                for key, value in directory_row.items()
                if key in {"agent_id", "registered", "work"} or value not in (None, [], {})
            }
        )
    # The projection publishes how many Agents the Goal registers, so a cap
    # applied upstream is reported as omitted rows rather than vanishing: a
    # reader must not read a truncated directory as "these are all the Agents".
    source_summary = _as_mapping(projection.get("source_summary"))
    registered_count = source_summary.get("registered_agent_count")
    omitted = (
        max(0, int(registered_count) - len(rows))
        if isinstance(registered_count, int) and not gaps
        else dropped_at_cap
    )
    if omitted:
        limitations.append(LIMITATION_ROWS_TRUNCATED)

    packet: dict[str, Any] = {
        "ok": True,
        "schema_version": PEER_AGENT_DIRECTORY_SCHEMA_VERSION,
        "goal_id": resolved_goal,
        "collected_at": now_utc_iso(),
        "scope": {
            "mode": PEER_AGENT_DIRECTORY_SCOPE,
            "caller_agent_id": caller,
            "caller_membership": (
                "registered_agent"
                if caller and caller in registered_agent_ids
                else "unregistered_agent"
                if caller
                else "local_surface"
            ),
            "scope_basis": "goal_registry",
            "gaps": gaps,
        },
        "rows": rows,
        "row_count": len(rows),
        "omitted_row_count": omitted,
        "registered_agent_count": (
            int(registered_count) if isinstance(registered_count, int) else len(rows)
        ),
        "presence_coverage": {
            "provider": None,
            "state": "unavailable",
            "note": (
                "presence answers 'runnable now'; no presence provider is "
                "registered for this Goal, so rows carry registry identity and "
                "durable work state only"
            ),
        },
        "authority": {
            "observation_grants": [],
            "writes": False,
            "scheduler": False,
            "note": "observation and delivery grant no claim, lease, priority or work edit",
        },
        "limitations": limitations,
    }
    if rows:
        packet["rollup"] = _rollup(rows)
    return packet
