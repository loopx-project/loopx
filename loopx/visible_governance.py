"""Synthetic visible-governance slice for GH-C62.

Projects per-goal/per-agent claims, optional task leases, quota, scheduler
hints, decision scopes, and the shared-goal RFC's proposed authority/state-
provider boundary into one read-only projection.  Every section makes
proposal-versus-shipped-truth explicit; no write API, no inference from
prose, no presentation of provider observations or leases as authority.
"""

from __future__ import annotations

from typing import Any

from .control_plane.quota.decision_summary import compact_quota_decision
from .control_plane.todos.decision_scope import (
    build_required_decision_scope_consistency,
)
from .control_plane.todos.contract import (
    normalize_todo_claimed_by,
    normalize_todo_id,
)


# ── schema constants ────────────────────────────────────────────────────────
GOVERNANCE_SLICE_SCHEMA = "visible_governance_slice_v0"
LEASE_NATURE = "optional runtime fence, not authority"


# ── authority-boundary static table ─────────────────────────────────────────
def _build_authority_boundary_table() -> list[dict[str, Any]]:
    """Implementation availability, not a claim that this Goal was promoted.

    Keep the original RFC section identifiers for report consumers. The original
    prototype commands/formats are distinct from their shipped native successors.
    Per-Goal authority must still be read from its actual binding and writer fence.
    """
    return [
        {
            "rfc_section": "3 -- State Classification",
            "proposal": "Shared canonical Todo lifecycle, claims, leases and operation receipts",
            "shipped": True,
            "shipped_equivalent": (
                "Promoted Goals use native TS commands and AuthorityStore transactions. "
                "Unpromoted Goals retain the Markdown/lease adapters."
            ),
            "gap": "Implementation availability does not select the default provider or migrate this Goal.",
        },
        {
            "rfc_section": "4 -- Coordination Ledger Shape",
            "proposal": "Original coordination head aggregate with an embedded receipt index",
            "shipped": False,
            "shipped_equivalent": (
                "The Python prototype is retired. Native File/SQLite journals retain "
                "head, committed history and receipts through one AuthorityStore contract. "
                "PostgreSQL and NoKV have separate candidate qualification."
            ),
            "gap": "The retired prototype head is not a supported Goal journal or migration source.",
        },
        {
            "rfc_section": "5.1 -- claim_work command",
            "proposal": "Atomic claim, lease and receipt publication",
            "shipped": False,
            "shipped_equivalent": (
                "The experimental claim_work executor is retired. Native todo_claim.ts "
                "owns atomic claim/lease acquisition on canonical providers."
            ),
            "gap": (
                "A historical receipt is not current execution authority. Lease/effect "
                "checks and the actual Goal binding remain required; claim_work is not a public command."
            ),
        },
        {
            "rfc_section": "7 -- Receipt Retention",
            "proposal": "Retain original receipts and fail closed on unproved outcomes",
            "shipped": True,
            "shipped_equivalent": "Native AuthorityStore readReceipt and committed history preserve replay evidence.",
            "gap": "Receipt correctness does not qualify unbounded capacity or the SQLite D2 profile.",
        },
        {
            "rfc_section": "8 -- Local vs Shared Mode",
            "proposal": "Explicit provider binding, writer fencing and migration parity",
            "shipped": True,
            "shipped_equivalent": "Reviewed per-Goal promotion and fenced rollback are implemented.",
            "gap": "Whole-Goal cohort qualification and default onboarding remain separate from per-Goal opt-in.",
        },
        {
            "rfc_section": "Appendix B -- handoff_mode",
            "proposal": "Explicit legacy, soft_claim or hard_lease coordination mode",
            "shipped": True,
            "shipped_equivalent": "The native handoff-mode command owns transition admission and quiescence checks.",
            "gap": "Provider selection does not silently replace the Goal's chosen handoff mode.",
        },
        {
            "rfc_section": "9 -- Offline/Local Boundaries",
            "proposal": "Unavailable shared authority must not silently fall back to local writers",
            "shipped": False,
            "shipped_equivalent": "Promoted local providers fail closed when canonical authority cannot be read.",
            "gap": "Deployed cross-host service availability and execution-interval fencing need separate qualification.",
        },
    ]


# ── claims projection ───────────────────────────────────────────────────────
def _build_claims_projection(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any]:
    """Extract per-todo soft claims from the status attention queue.

    Looks inside ``agent_todos`` summary lists (``first_open_items``,
    ``first_executable_items``) within each goal-level attention queue
    item to find todos with a ``claimed_by`` field.
    """
    attention = (
        status_payload.get("attention_queue")
        if isinstance(status_payload.get("attention_queue"), dict)
        else {}
    )
    items = attention.get("items") if isinstance(attention.get("items"), list) else []

    claims: list[dict[str, Any]] = []
    by_agent: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()

    _SUMMARY_LIST_KEYS = (
        "first_open_items",
        "first_executable_items",
        "executable_backlog_items",
        "backlog_items",
    )

    for item in items:
        if not isinstance(item, dict):
            continue
        item_goal = str(item.get("goal_id") or "")
        if item_goal and item_goal != goal_id:
            continue

        agent_todos = (
            item.get("agent_todos")
            if isinstance(item.get("agent_todos"), dict)
            else {}
        )
        for list_key in _SUMMARY_LIST_KEYS:
            todo_list = agent_todos.get(list_key)
            if not isinstance(todo_list, list):
                continue
            for td in todo_list:
                if not isinstance(td, dict):
                    continue
                todo_id = normalize_todo_id(td.get("todo_id"))
                claimed_by = normalize_todo_claimed_by(td.get("claimed_by"))
                if not claimed_by:
                    continue
                if todo_id in seen:
                    continue
                seen.add(todo_id)
                claim_entry: dict[str, Any] = {
                    "todo_id": todo_id,
                    "claimed_by": claimed_by,
                    "task_class": td.get("task_class"),
                    "status": td.get("status") or (
                        "done" if td.get("done") else "open"
                    ),
                }
                for key in ("text", "target_key", "action_kind"):
                    value = td.get(key)
                    if value:
                        claim_entry[key] = str(value)[:200]
                claims.append(claim_entry)
                by_agent.setdefault(claimed_by, []).append(claim_entry)

    normalized_agent = normalize_todo_claimed_by(agent_id)
    return {
        "total_claims": len(claims),
        "claiming_agent_count": len(by_agent),
        "current_agent_claims": len(by_agent.get(normalized_agent, []))
        if normalized_agent
        else 0,
        "by_agent": {
            agent: len(agent_claims)
            for agent, agent_claims in sorted(by_agent.items())
        },
        "claims": claims,
    }


# ── leases projection ───────────────────────────────────────────────────────
def _build_leases_projection(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
    include_task_leases: bool,
) -> dict[str, Any] | None:
    """Read active task leases for the goal (read-only, never writes)."""
    if not include_task_leases:
        return None

    from .control_plane.work_items.task_lease import (
        lease_epoch,
        lease_is_active,
        read_lease,
        task_lease_dir,
    )

    runtime_root_raw = status_payload.get("runtime_root")
    if not runtime_root_raw:
        return {"active_leases": 0, "leases": [], "nature": LEASE_NATURE}

    from pathlib import Path

    lease_dir = task_lease_dir(
        runtime_root=Path(str(runtime_root_raw)),
        goal_id=goal_id,
    )
    if not lease_dir.exists():
        return {"active_leases": 0, "leases": [], "nature": LEASE_NATURE}

    active_leases: list[dict[str, Any]] = []
    for path in sorted(lease_dir.glob("todo_*.json")):
        lease = read_lease(path)
        if not lease_is_active(lease):
            continue
        active_leases.append(
            {
                "todo_id": lease.get("todo_id"),
                "owner": lease.get("owner"),
                "expires_at": lease.get("expires_at"),
                "version": lease.get("version"),
                "lease_epoch": lease_epoch(lease),
                "write_scopes": lease.get("write_scopes") or [],
                "acquire_ttl_seconds": lease.get("acquire_ttl_seconds"),
            }
        )

    normalized_agent = normalize_todo_claimed_by(agent_id)
    return {
        "active_leases": len(active_leases),
        "current_agent_active_leases": sum(
            1
            for lease in active_leases
            if lease.get("owner") == normalized_agent
        ),
        "leases": active_leases,
        "nature": LEASE_NATURE,
    }


# ── quota projection ────────────────────────────────────────────────────────
def _build_quota_projection(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Compact quota decision via ``build_quota_should_run``."""
    from .quota import build_quota_should_run

    try:
        decision = build_quota_should_run(
            status_payload,
            goal_id=goal_id,
            agent_id=agent_id,
        )
    except (ValueError, KeyError, OSError):
        return None
    return compact_quota_decision(decision)


# ── scheduler hints projection ──────────────────────────────────────────────
def _build_scheduler_hints_projection(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Extract scheduler hints from the quota should-run packet."""
    from .quota import build_quota_should_run

    try:
        decision = build_quota_should_run(
            status_payload,
            goal_id=goal_id,
            agent_id=agent_id,
            include_scheduler_detail=True,
        )
    except (ValueError, KeyError, OSError):
        return None

    scheduler = decision.get("scheduler_hint")
    if not isinstance(scheduler, dict):
        return None

    return {
        "action": scheduler.get("action"),
        "cadence_class": scheduler.get("cadence_class"),
        "recommended_interval_minutes": scheduler.get(
            "recommended_interval_minutes"
        ),
        "max_interval_minutes": scheduler.get("max_interval_minutes"),
        "reset_policy": scheduler.get("reset_policy"),
        "unchanged_poll": {
            k: scheduler.get("unchanged_poll", {}).get(k)
            for k in ("backoff_multiplier", "max_interval_minutes")
            if k in scheduler.get("unchanged_poll", {})
        }
        if isinstance(scheduler.get("unchanged_poll"), dict)
        else None,
        "arbitration": scheduler.get("arbitration_disposition"),
    }


# ── decision scopes projection ──────────────────────────────────────────────
def _build_decision_scopes_projection(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any]:
    """Project decision scopes from quota should-run summaries.

    Uses ``build_quota_should_run`` to obtain agent/user todo summaries
    and standing decision authority, then runs the consistency check.
    """
    from .quota import build_quota_should_run

    try:
        decision = build_quota_should_run(
            status_payload,
            goal_id=goal_id,
            agent_id=agent_id,
        )
    except (ValueError, KeyError, OSError):
        return {
            "standing_decision_authority": None,
            "required_scope_consistency": {
                "ok": None,
                "status": "unavailable",
            },
        }

    # standing authority already computed by the quota guard
    standing = decision.get("standing_decision_authority")

    # Build consistency from the summaries in the should_run payload
    agent_summary = decision.get("agent_todo_summary")
    user_summary = decision.get("user_todo_summary")
    consistency = build_required_decision_scope_consistency(
        agent_todo_summary=agent_summary if isinstance(agent_summary, dict) else None,
        user_todo_summary=user_summary if isinstance(user_summary, dict) else None,
        agent_id=agent_id,
        registered_agent_ids=None,
        standing_decision_authority=standing,
    )

    return {
        "standing_decision_authority": standing,
        "required_scope_consistency": {
            "ok": consistency.get("ok"),
            "status": consistency.get("status"),
            "checked_agent_todo_count": consistency.get(
                "checked_agent_todo_count"
            ),
            "checked_required_scope_count": consistency.get(
                "checked_required_scope_count"
            ),
            "standing_authority_match_count": consistency.get(
                "standing_authority_match_count"
            ),
            "terminal_outcome_count": consistency.get("terminal_outcome_count"),
        },
    }


# ── main builder ────────────────────────────────────────────────────────────
def build_visible_governance_slice(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None = None,
    include_task_leases: bool = True,
) -> dict[str, Any]:
    """Synthesize a read-only visible-governance slice for one goal.

    Args:
        status_payload: Full ``collect_status(...)`` result.
        goal_id: Goal to project governance for.
        agent_id: Optional agent lane to scope per-agent projections.
        include_task_leases: When True, read active lease files (read-only).

    Returns:
        ``{"schema_version": "visible_governance_slice_v0", "ok": True, ...}``
        with sections: claims, leases, quota, scheduler_hints,
        decision_scopes, authority_boundary.
    """
    safe_goal_id = str(goal_id or "").strip()
    if not safe_goal_id:
        return {
            "schema_version": GOVERNANCE_SLICE_SCHEMA,
            "ok": False,
            "error": "goal_id is required",
        }

    claims = _build_claims_projection(
        status_payload, goal_id=safe_goal_id, agent_id=agent_id
    )
    leases = _build_leases_projection(
        status_payload,
        goal_id=safe_goal_id,
        agent_id=agent_id,
        include_task_leases=include_task_leases,
    )
    quota = _build_quota_projection(
        status_payload, goal_id=safe_goal_id, agent_id=agent_id
    )
    scheduler_hints = _build_scheduler_hints_projection(
        status_payload, goal_id=safe_goal_id, agent_id=agent_id
    )
    decision_scopes = _build_decision_scopes_projection(
        status_payload, goal_id=safe_goal_id, agent_id=agent_id
    )
    authority_boundary = _build_authority_boundary_table()

    return {
        "schema_version": GOVERNANCE_SLICE_SCHEMA,
        "ok": True,
        "goal_id": safe_goal_id,
        "agent_id": normalize_todo_claimed_by(agent_id),
        "claims": claims,
        "leases": leases,
        "quota": quota,
        "scheduler_hints": scheduler_hints,
        "decision_scopes": decision_scopes,
        "authority_boundary": authority_boundary,
    }


# ── markdown renderer ───────────────────────────────────────────────────────
def render_visible_governance_markdown(slice_payload: dict[str, Any]) -> str:
    """Render a visible-governance slice as public-safe markdown."""

    goal_id = str(slice_payload.get("goal_id") or "unknown")
    agent_id = str(slice_payload.get("agent_id") or "")

    lines: list[str] = []
    lines.append("# Visible Governance Slice")
    lines.append("")
    lines.append(f"- **Goal**: `{goal_id}`")
    if agent_id:
        lines.append(f"- **Agent**: `{agent_id}`")
    lines.append(f"- **Schema**: `{slice_payload.get('schema_version')}`")
    lines.append("")

    # ── Claims ──
    claims = slice_payload.get("claims")
    if isinstance(claims, dict):
        lines.append("## Claims (Soft Ownership)")
        lines.append("")
        lines.append(
            f"- Total claims: **{claims.get('total_claims', 0)}** "
            f"across {claims.get('claiming_agent_count', 0)} agent(s)"
        )
        current = claims.get("current_agent_claims", 0)
        if current:
            lines.append(f"- Current agent claims: **{current}**")
        by_agent = claims.get("by_agent")
        if isinstance(by_agent, dict) and by_agent:
            lines.append("")
            lines.append("| Agent | Claim Count |")
            lines.append("|-------|-------------|")
            for agent, count in sorted(by_agent.items()):
                lines.append(f"| `{agent}` | {count} |")
        lines.append("")

    # ── Leases ──
    leases = slice_payload.get("leases")
    if isinstance(leases, dict):
        lines.append("## Task Leases (Optional Runtime Fence)")
        lines.append("")
        lines.append(f"- Nature: *{leases.get('nature', LEASE_NATURE)}*")
        lines.append(
            f"- Active leases: **{leases.get('active_leases', 0)}**"
        )
        lease_list = leases.get("leases")
        if isinstance(lease_list, list) and lease_list:
            lines.append("")
            lines.append(
                "| Todo ID | Owner | Expires | Version | TTL (s) |"
            )
            lines.append(
                "|---------|-------|---------|---------|---------|"
            )
            for lease in lease_list:
                lines.append(
                    f"| `{lease.get('todo_id', '')}` "
                    f"| `{lease.get('owner', '')}` "
                    f"| {lease.get('expires_at', '')} "
                    f"| {lease.get('version', '')} "
                    f"| {lease.get('acquire_ttl_seconds', '')} |"
                )
        lines.append("")

    # ── Quota ──
    quota = slice_payload.get("quota")
    if isinstance(quota, dict):
        lines.append("## Quota Decision")
        lines.append("")
        lines.append(f"- Should run: `{quota.get('should_run')}`")
        lines.append(
            f"- Effective action: `{quota.get('effective_action', '')}`"
        )
        lines.append(f"- State: `{quota.get('state', '')}`")
        compute = quota.get("compute")
        if compute is not None:
            lines.append(f"- Compute: {compute}")
        slots_info = []
        for key in ("spent_slots", "allowed_slots", "slot_minutes"):
            value = quota.get(key)
            if value is not None:
                slots_info.append(f"{key}: {value}")
        if slots_info:
            lines.append(f"- Slots: {', '.join(slots_info)}")
        lines.append("")

    # ── Scheduler Hints ──
    sched = slice_payload.get("scheduler_hints")
    if isinstance(sched, dict):
        lines.append("## Scheduler Hints")
        lines.append("")
        lines.append(f"- Action: `{sched.get('action', '')}`")
        lines.append(
            f"- Cadence class: `{sched.get('cadence_class', '')}`"
        )
        interval = sched.get("recommended_interval_minutes")
        if interval is not None:
            lines.append(f"- Recommended interval: {interval} min")
        lines.append(
            f"- Arbitration: `{sched.get('arbitration', '')}`"
        )
        lines.append("")

    # ── Decision Scopes ──
    scopes = slice_payload.get("decision_scopes")
    if isinstance(scopes, dict):
        lines.append("## Decision Scopes")
        lines.append("")
        standing = scopes.get("standing_decision_authority")
        if isinstance(standing, dict):
            lines.append(
                f"- Standing authority receipts: "
                f"**{standing.get('active_count', 0)}** active, "
                f"{standing.get('inactive_count', 0)} inactive"
            )
        consistency = scopes.get("required_scope_consistency")
        if isinstance(consistency, dict):
            lines.append(
                f"- Scope consistency: "
                f"`{consistency.get('status', 'unknown')}` "
                f"(checked {consistency.get('checked_agent_todo_count', 0)} "
                f"todos, {consistency.get('checked_required_scope_count', 0)} "
                f"scopes)"
            )
            matches = consistency.get("standing_authority_match_count", 0)
            if matches:
                lines.append(
                    f"- Standing authority matches: **{matches}**"
                )
        lines.append("")

    # ── Authority Boundary ──
    boundary = slice_payload.get("authority_boundary")
    if isinstance(boundary, list):
        lines.append("## Authority Boundary: Proposal vs Shipped")
        lines.append("")
        lines.append(
            "| RFC Section | Shipped | Gap |"
        )
        lines.append(
            "|-------------|---------|-----|"
        )
        for entry in boundary:
            shipped = "Yes" if entry.get("shipped") else "**No**"
            gap = str(entry.get("gap") or "")[:120]
            lines.append(
                f"| {entry.get('rfc_section', '')} "
                f"| {shipped} "
                f"| {gap} |"
            )
        lines.append("")

    # ── Summary ──
    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"- **Proposals shipped**: "
        f"{sum(1 for e in (boundary or []) if e.get('shipped'))} / "
        f"{len(boundary) if boundary else 0}"
    )
    lines.append(
        "- **Key takeaway**: Stage-2 ships an additive coordination head, "
        "file provider, and claim_work executor plus handoff_mode; default "
        "runtime still uses Markdown soft claims, file-backed task leases "
        "(optional fence, not write authority), and turn-scoped settlement. "
        "Shared-mode promotion remains Stage 3."
    )
    lines.append("")

    return "\n".join(lines)
