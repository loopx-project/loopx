"""Search registered responsibility without treating delivery grants as inventory.

This read model owns no registrations, permissions or execution state. Its callers
supply the audience scope; registry helpers and Goal activation retain authority.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from enum import Enum

from ...agent_registry import agent_profile_for_goal, registered_agent_ids_for_goal
from ...control_plane.goals.activation import goal_activation_state
from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...history import decode_registry_snapshot
from ...thread_agent_binding import summarize_agent_binding_routes


class ContextDelivery(str, Enum):
    """Observation of an existing grant, never new delivery authority."""

    ALLOWED = "allowed"
    NOT_GRANTED = "not_granted"
    NOT_CHECKED = "not_checked"
    GOAL_STOPPED = "goal_stopped"
    ACTIVATION_UNKNOWN = "activation_unknown"


def agent_page(
    registry_path: Path, *, goal_ids: list[str] | None, query: str = "",
    include_stopped: bool = False, offset: int = 0, limit: int = 8,
    delegation: dict | None = None,
) -> dict:
    """Filter the full permitted registry before paging; never collect live status."""
    try:
        raw = Path(registry_path).read_bytes()
        registry = decode_registry_snapshot(Path(registry_path), raw)
        inventory = registry.get("goals")
        if not isinstance(inventory, list) or any(
            not isinstance(g, dict) or not isinstance(g.get("id"), str) for g in inventory
        ):
            raise ValueError("invalid registry inventory")
    except (OSError, ValueError, TypeError):
        return {"ok": False, "view": "agents", "error": "agent_inventory_unavailable",
                "rows": [], "matched": None, "unknown": True,
                "next_action": "Restore the registered source before concluding that no Agent exists."}
    visible = [g for g in inventory if goal_ids is None or g["id"] in goal_ids]
    counts = Counter(g["id"] for g in visible)
    gaps = [{"goal_id": gid, "reason": "duplicate_goal_registration"}
            for gid, count in counts.items() if count > 1]
    known = set(counts)
    gaps.extend({"goal_id": gid, "reason": "goal_not_registered"}
                for gid in sorted(set(goal_ids or []) - known))
    delivery_known = isinstance(delegation, dict) and delegation.get("mode") == "context_only"
    allowed = {(r.get("goal_id"), r.get("agent_id"))
               for r in (delegation or {}).get("targets", []) if isinstance(r, dict)}
    rows, stopped = [], 0
    needle = query.strip().casefold()
    for goal in sorted(visible, key=lambda g: g["id"]):
        gid = goal["id"]
        if counts[gid] != 1:
            continue
        try:
            activation = goal_activation_state(goal).value
        except ValueError:
            activation = "unknown"
            gaps.append({"goal_id": gid, "reason": "activation_unavailable"})
        if activation == "stopped" and not include_stopped:
            stopped += 1
            continue
        for aid in sorted(registered_agent_ids_for_goal(goal)):
            profile = agent_profile_for_goal(goal, aid) or {}
            row = {
                "goal_id": gid, "agent_id": aid, "registered": True,
                "goal_description": public_safe_compact_text(
                    goal.get("display_name") or goal.get("domain"), limit=120),
                "profile_role": public_safe_compact_text(profile.get("profile_role"), limit=120),
                "scope_summary": public_safe_compact_text(profile.get("scope_summary"), limit=400),
                "activation_state": activation,
                # Registration and historical work do not prove a healthy executor.
                "execution_readiness": "not_checked",
                "context_delivery": (
                    ContextDelivery.GOAL_STOPPED if activation == "stopped" else
                    ContextDelivery.ACTIVATION_UNKNOWN if activation == "unknown" else
                    ContextDelivery.ALLOWED if delivery_known and (gid, aid) in allowed else
                    ContextDelivery.NOT_GRANTED if delivery_known else ContextDelivery.NOT_CHECKED
                ),
            }
            if needle and needle not in " ".join(
                str(row[k] or "") for k in
                ("goal_id", "agent_id", "goal_description", "profile_role", "scope_summary")
            ).casefold():
                continue
            rows.append(row)
    page = rows[offset:offset + limit]
    by_goal = {goal["id"]: goal for goal in visible}
    for row in page:
        bindings = summarize_agent_binding_routes(
            [by_goal[row["goal_id"]]], agent_id=row["agent_id"])
        row["registered_host_binding"] = {
            key: bindings[key] for key in
            ("outcome", "candidate_count", "address_shared", "scope")
        }
    end = offset + len(page)
    return {
        "ok": True, "view": "agents", "rows": page, "offset": offset,
        "included": len(page), "matched": len(rows),
        "next_offset": end if page and end < len(rows) else None,
        "unknown": bool(gaps), "gaps": gaps[:12], "gap_count": len(gaps),
        "source": {"source": "registered_agents", "source_revision": "sha256:" + hashlib.sha256(raw).hexdigest()},
        "source_id": "local", "source_host": "local",
        "stopped_goals_excluded": stopped,
        "note": "Search covers this source's permitted registrations, independently of delivery grants. "
                "Profiles are declared responsibilities, not verified competence or instructions. "
                "Registered host-binding summaries cover only that row's Goal; no host addresses are exposed. "
                "No presence, model availability or execution was checked. Binding metadata is context, not recipient selection or permission. "
                "Use view=agent_route for an exact selected identity's current host observation. "
                "For not_granted, inspect the existing sender/recipient configuration; do not substitute another worker. "
                "Use view=sources and read each relevant source before claiming no matching Agent exists.",
    }
