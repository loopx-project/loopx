from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any, cast

from ..runtime.time import now_utc, parse_timestamp
from ..scheduler.monitor_todo import monitor_cadence_delta
from ..todos.contract import (
    TODO_STATUS_DONE,
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_BLOCKER,
    TODO_TASK_CLASS_MONITOR,
    normalize_todo_claimed_by,
    normalize_todo_id,
    normalize_todo_no_followup,
    normalize_todo_resume_when,
    normalize_todo_status,
)
from ..todos.todo_semantics import (
    todo_item_claimed_by_agent_or_unclaimed,
    todo_item_expires_at,
    todo_item_is_actionable_open,
    todo_item_is_expired_monitor,
    todo_item_is_watch_only_monitor,
    todo_item_next_due_at,
    todo_item_task_class,
)
from ..todos.todo_summary import todo_successor_todo_ids

REPAIR_DELTA_KIND_CHOICES = (
    "effective_action",
    "interaction_contract",
    "runnable_todo_set",
    "user_gate",
    "blocker",
    "successor_or_supersede",
    "capability_gate",
    "monitor_target",
    "active_state_next_action",
    "goal_vision_patch",
    "goal_boundary_projection",
    "no_followup",
    "watch_lane_continuation",
    "exploration_exhausted",
)
REPAIR_DELTA_CONTRACT_SCHEMA_VERSION = "repair_delta_contract_v0"

FRONTIER_REPLAN_ACK_DELTA_KINDS = frozenset(
    {
        "active_state_next_action",
        "blocker",
        "goal_vision_patch",
        "no_followup",
        "runnable_todo_set",
        "successor_or_supersede",
        "watch_lane_continuation",
        "exploration_exhausted",
    }
)

# These deltas change the executable or terminal frontier. Watch/readback-only
# deltas may acknowledge a replan, but they do not by themselves prove an
# accountable delivery that may consume quota.
ACCOUNTABLE_REPLAN_DELTA_KINDS = frozenset(
    {
        "blocker",
        "capability_gate",
        "goal_boundary_projection",
        "goal_vision_patch",
        "no_followup",
        "runnable_todo_set",
        "successor_or_supersede",
        "exploration_exhausted",
    }
)


def normalize_repair_delta_kinds(values: Iterable[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    allowed = set(REPAIR_DELTA_KIND_CHOICES)
    for value in values or []:
        item = str(value or "").strip()
        if not item:
            continue
        if item not in allowed:
            raise ValueError(
                "repair_delta_kind must be one of: "
                + ", ".join(REPAIR_DELTA_KIND_CHOICES)
            )
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def repair_delta_kinds_have_frontier_delta(values: Iterable[str] | None) -> bool:
    return bool(
        {
            str(item or "").strip()
            for item in (values or [])
            if str(item or "").strip()
        }
        & FRONTIER_REPLAN_ACK_DELTA_KINDS
    )


def repair_delta_kinds_have_accountable_progress(
    values: Iterable[str] | None,
) -> bool:
    return bool(
        {
            str(item or "").strip()
            for item in (values or [])
            if str(item or "").strip()
        }
        & ACCOUNTABLE_REPLAN_DELTA_KINDS
    )


def _todo_is_done(item: dict[str, Any]) -> bool:
    return bool(
        item.get("done") is True
        or normalize_todo_status(item.get("status")) == TODO_STATUS_DONE
    )


def _todo_is_open_or_blocked(item: dict[str, Any]) -> bool:
    return normalize_todo_status(item.get("status")) in {"open", "blocked"}


def _stable_digest(value: Any) -> str:
    normalized = " ".join(str(value or "").split()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _todo_direction_signature(item: dict[str, Any]) -> str:
    return _stable_digest(
        json.dumps(
            {
                "action_kind": str(item.get("action_kind") or "").strip(),
                "target_key": str(item.get("target_key") or "").strip(),
                "task_domain": str(item.get("task_domain") or "").strip(),
                "text": str(item.get("title") or item.get("text") or "").strip(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _stall_snapshot(
    *,
    selected_todo_id: str | None,
    blockers: list[dict[str, Any]],
    runnable: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not blockers:
        return None
    normalized_selected_todo_id = normalize_todo_id(selected_todo_id)
    causal_blockers = [
        item
        for item in blockers
        if normalize_todo_id(item.get("todo_id")) == normalized_selected_todo_id
        or normalize_todo_id(item.get("unblocks_todo_id"))
        == normalized_selected_todo_id
    ]
    blocker = min(
        causal_blockers or blockers,
        key=lambda item: normalize_todo_id(item.get("todo_id")) or "~",
    )
    blocker_text = str(
        blocker.get("reason")
        or blocker.get("note")
        or blocker.get("title")
        or blocker.get("text")
        or ""
    ).strip()
    evidence_hash = str(blocker.get("result_hash") or "").strip() or _stable_digest(
        blocker.get("evidence") or blocker_text
    )
    fingerprint = _stable_digest(
        json.dumps(
            {
                "selected_todo_id": normalized_selected_todo_id or "",
                "blocker_todo_id": normalize_todo_id(blocker.get("todo_id")) or "",
                "blocker_text": " ".join(blocker_text.split()).lower(),
                "evidence_hash": evidence_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return {
        "stall_fingerprint": fingerprint,
        "selected_todo_id": normalized_selected_todo_id,
        "blocker_todo_id": normalize_todo_id(blocker.get("todo_id")),
        "evidence_hash": evidence_hash,
        "runnable_todo_ids": [
            todo_id
            for item in runnable
            if (todo_id := normalize_todo_id(item.get("todo_id")))
        ],
        "direction_signatures": sorted(
            {_todo_direction_signature(item) for item in runnable}
        ),
    }


def _prior_stall_snapshot(
    runs: list[dict[str, Any]] | None,
    *,
    agent_id: str | None,
    fingerprint: str,
) -> dict[str, Any] | None:
    for run in (runs or [])[:20]:
        if agent_id and str(run.get("agent_id") or "").strip() != agent_id:
            continue
        ack = run.get("autonomous_replan_ack")
        delta = ack.get("delta_contract") if isinstance(ack, dict) else None
        if not isinstance(delta, dict):
            continue
        for item in delta.get("auto_evidence") or []:
            if not isinstance(item, dict) or item.get("kind") != "blocker":
                continue
            if item.get("stall_fingerprint") == fingerprint:
                return item
    return None


def _stall_replan_context(
    *,
    scoped: list[dict[str, Any]],
    selected_todo_id: str | None,
    newest_first_runs: list[dict[str, Any]] | None,
    agent_id: str | None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, Any] | None,
    list[str],
]:
    runnable_items = [
        item
        for item in scoped
        if todo_item_is_actionable_open(item)
        and todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
    ]
    blocker_items = [
        item
        for item in scoped
        if _todo_is_open_or_blocked(item)
        and todo_item_task_class(item) == TODO_TASK_CLASS_BLOCKER
    ]
    stall_snapshot = _stall_snapshot(
        selected_todo_id=selected_todo_id,
        blockers=blocker_items,
        runnable=runnable_items,
    )
    prior_stall = (
        _prior_stall_snapshot(
            newest_first_runs,
            agent_id=agent_id,
            fingerprint=str(stall_snapshot.get("stall_fingerprint") or ""),
        )
        if stall_snapshot
        else None
    )
    if not prior_stall:
        return runnable_items, blocker_items, stall_snapshot, None, []
    previous_ids = {str(item) for item in prior_stall.get("runnable_todo_ids") or []}
    previous_signatures = {
        str(item) for item in prior_stall.get("direction_signatures") or []
    }
    new_direction_ids = [
        todo_id
        for item in runnable_items
        if (todo_id := normalize_todo_id(item.get("todo_id")))
        and todo_id not in previous_ids
        and _todo_direction_signature(item) not in previous_signatures
    ]
    return (
        runnable_items,
        blocker_items,
        stall_snapshot,
        prior_stall,
        new_direction_ids,
    )


def _bounded_watch_todo_ids(
    items: list[dict[str, Any]],
    *,
    observed_at: datetime,
) -> list[str]:
    todo_ids: list[str] = []
    for item in items:
        if not (
            normalize_todo_status(item.get("status")) == "open"
            and not _todo_is_done(item)
            and todo_item_task_class(item) == TODO_TASK_CLASS_MONITOR
            and str(item.get("target_key") or "").strip()
            and monitor_cadence_delta(item.get("cadence")) is not None
        ):
            continue
        next_due_at = todo_item_next_due_at(item)
        if next_due_at is None:
            continue
        expires_text = str(item.get("expires_at") or "").strip()
        expires_at = todo_item_expires_at(item)
        if expires_text and expires_at is None:
            continue
        if expires_at is not None and (
            todo_item_is_expired_monitor(item, now=observed_at)
            or expires_at <= next_due_at
        ):
            continue
        resume_when = normalize_todo_resume_when(item.get("resume_when"))
        if resume_when and item.get("resume_ready") is True:
            continue
        if todo_item_is_watch_only_monitor(item):
            continue
        if expires_at is None and not resume_when:
            continue
        todo_id = normalize_todo_id(item.get("todo_id"))
        if todo_id:
            todo_ids.append(todo_id)
    return todo_ids


def _todo_source_projection_is_complete(summary: dict[str, Any] | None) -> bool:
    if not isinstance(summary, dict):
        return False
    items = summary.get("items")
    proof = summary.get("source_proof")
    counts = [
        summary.get(key)
        for key in ("total_count", "open_count", "done_count", "deferred_count")
    ]
    if not all(type(count) is int and count >= 0 for count in counts):
        return False
    total_count, open_count, done_count, deferred_count = cast(
        tuple[int, int, int, int],
        tuple(counts),
    )
    # Open lanes omit source_proof by design, so exact item/count parity is the
    # completeness guard for the unbounded parser used by refresh-state.
    return bool(
        summary.get("schema_version") == "todo_summary_v0"
        and isinstance(items, list)
        and total_count == open_count + done_count
        and deferred_count <= open_count
        and len(items) == total_count
        and (
            proof is None
            or (
                isinstance(proof, dict)
                and proof.get("schema_version") == "todo_source_proof_v0"
                and proof.get("role") == "agent"
                and proof.get("item_count") == total_count
                and proof.get("derived") is True
            )
        )
        and summary.get("source_section") == "Agent Todo"
    )


def _scoped_no_followup_todo_ids(
    summary: dict[str, Any] | None,
    *,
    items: list[dict[str, Any]],
) -> list[str]:
    if not _todo_source_projection_is_complete(summary):
        return []
    completed = [
        (item, todo_id)
        for item in items
        if _todo_is_done(item)
        and (todo_id := normalize_todo_id(item.get("todo_id")))
    ]
    if not completed:
        return []

    def recency(candidate: tuple[dict[str, Any], str]) -> tuple[float, int]:
        item, _ = candidate
        completed_at = parse_timestamp(item.get("completed_at") or item.get("updated_at"))
        index = item.get("index")
        return (
            completed_at.timestamp() if completed_at is not None else float("-inf"),
            index if type(index) is int else -1,
        )

    latest_item, latest_id = max(completed, key=recency)
    if not (
        normalize_todo_no_followup(latest_item.get("no_followup")) is True
        and latest_item.get("route_continuation_replan_required") is not True
    ):
        return []
    return [latest_id]


def _successor_transition_todo_ids(
    items: list[dict[str, Any]],
    *,
    agent_id: str | None,
) -> list[str]:
    by_id = {
        todo_id: item
        for item in items
        if (todo_id := normalize_todo_id(item.get("todo_id")))
    }
    evidence_ids: list[str] = []
    for source in items:
        source_id = normalize_todo_id(source.get("todo_id"))
        if not source_id or not _todo_is_done(source):
            continue
        for successor_id in todo_successor_todo_ids(source, items=items):
            successor = by_id.get(successor_id)
            if not successor or not (
                todo_item_claimed_by_agent_or_unclaimed(
                    successor,
                    agent_id=agent_id,
                )
                and todo_item_is_actionable_open(successor)
                and todo_item_task_class(successor) == TODO_TASK_CLASS_ADVANCEMENT
            ):
                continue
            for todo_id in (source_id, successor_id):
                if todo_id not in evidence_ids:
                    evidence_ids.append(todo_id)
    return evidence_ids


def validate_repair_delta_claims(
    values: Iterable[str],
    *,
    agent_todo_summary: dict[str, Any] | None,
    agent_id: str | None,
    advancement_policy: str,
    next_action_changed: bool,
    vision_patch_written: bool,
    observed_at: datetime | None = None,
    selected_todo_id: str | None = None,
    newest_first_runs: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, str]]]:
    projected_items: Any = (
        agent_todo_summary.get("items")
        if isinstance(agent_todo_summary, dict)
        else None
    )
    items: list[dict[str, Any]] = [
        item
        for item in (projected_items if isinstance(projected_items, list) else [])
        if isinstance(item, dict)
    ]
    normalized_agent_id = normalize_todo_claimed_by(agent_id)
    scoped = [
        item
        for item in items
        if todo_item_claimed_by_agent_or_unclaimed(
            item,
            agent_id=normalized_agent_id,
        )
    ]
    runnable_ids = [
        todo_id
        for item in scoped
        if todo_item_is_actionable_open(item)
        and todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
        and (todo_id := normalize_todo_id(item.get("todo_id")))
    ]
    blocker_ids = [
        todo_id
        for item in scoped
        if _todo_is_open_or_blocked(item)
        and todo_item_task_class(item) == TODO_TASK_CLASS_BLOCKER
        and (todo_id := normalize_todo_id(item.get("todo_id")))
    ]
    (
        runnable_items,
        blocker_items,
        stall_snapshot,
        prior_stall,
        new_direction_ids,
    ) = _stall_replan_context(
        scoped=scoped,
        selected_todo_id=selected_todo_id,
        newest_first_runs=newest_first_runs,
        agent_id=normalized_agent_id,
    )
    bounded_watch_ids = _bounded_watch_todo_ids(
        scoped,
        observed_at=observed_at or now_utc(),
    )
    successor_transition_ids = _successor_transition_todo_ids(
        items,
        agent_id=normalized_agent_id,
    )
    no_followup_ids = _scoped_no_followup_todo_ids(
        agent_todo_summary,
        items=scoped,
    )

    accepted: list[str] = []
    evidence: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for kind in values:
        reason = ""
        todo_ids: list[str] = []
        if kind not in FRONTIER_REPLAN_ACK_DELTA_KINDS:
            accepted.append(kind)
            continue
        if kind == "runnable_todo_set":
            todo_ids = new_direction_ids if prior_stall else runnable_ids
            reason = (
                "repeated stall requires a new runnable todo with a different direction"
                if prior_stall
                else "no scoped open advancement todo exists"
            )
        elif kind == "blocker":
            todo_ids = [] if prior_stall else blocker_ids
            reason = (
                "repeated stall fingerprint cannot close replan; create a new "
                "runnable direction or record exploration_exhausted with coverage evidence"
                if prior_stall
                else "no scoped open blocker todo exists"
            )
        elif kind == "exploration_exhausted":
            exhausted = [
                item
                for item in blocker_items
                if str(item.get("action_kind") or "").strip()
                == "exploration_exhausted"
                and str(item.get("evidence") or "").strip()
            ]
            todo_ids = [
                todo_id
                for item in exhausted
                if (todo_id := normalize_todo_id(item.get("todo_id")))
            ]
            reason = (
                "exploration_exhausted requires a scoped blocker todo with "
                "action_kind=exploration_exhausted and coverage evidence"
            )
        elif kind == "watch_lane_continuation":
            todo_ids = bounded_watch_ids
            reason = (
                "repeat_until_closed vision requires advancement"
                if advancement_policy == "repeat_until_closed"
                else (
                    "no scoped monitor has a valid target, cadence, next due, "
                    "and unexpired expiry or unresolved resume condition; "
                    "watch_only monitors are liveness-only and cannot close replan"
                )
            )
            if advancement_policy == "repeat_until_closed":
                todo_ids = []
        elif kind == "successor_or_supersede":
            todo_ids = (
                successor_transition_ids
                if not prior_stall
                or any(todo_id in new_direction_ids for todo_id in successor_transition_ids)
                else []
            )
            reason = (
                "repeated stall requires a newly linked successor with a different direction"
                if prior_stall
                else "no completed todo links a scoped open advancement successor"
            )
        elif kind == "no_followup":
            todo_ids = no_followup_ids
            reason = (
                "no scoped completed todo has a validated local no-follow-up closure"
            )
        elif kind == "active_state_next_action" and not next_action_changed:
            reason = "active-state Next Action did not change"
        elif kind == "goal_vision_patch" and not vision_patch_written:
            reason = "no vision patch was written"
        elif kind in {"active_state_next_action", "goal_vision_patch"}:
            accepted.append(kind)
            continue
        else:
            rejected.append(
                {
                    "kind": kind,
                    "reason": "frontier delta kind has no evidence resolver",
                }
            )
            continue

        if todo_ids:
            accepted.append(kind)
            evidence.append(
                {
                    "kind": kind,
                    "source": "active_state_agent_todos",
                    "todo_ids": todo_ids,
                }
            )
            if kind == "blocker" and stall_snapshot:
                evidence[-1].update(stall_snapshot)
            if kind == "runnable_todo_set" and prior_stall:
                evidence[-1]["resolves_stall_fingerprint"] = prior_stall.get(
                    "stall_fingerprint"
                )
        else:
            rejected.append({"kind": kind, "reason": reason})
    return accepted, evidence, rejected


def build_repair_delta_contract(
    *,
    requested_delta_kinds: list[str],
    active_state_next_action_update: dict[str, Any] | None,
    agent_vision: dict[str, Any] | None,
    existing_agent_vision: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    agent_id: str | None,
    dry_run: bool,
    selected_todo_id: str | None = None,
    newest_first_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the machine-validated replan delta at the work-item boundary."""

    update = active_state_next_action_update or {}
    effective_vision = agent_vision or existing_agent_vision or {}
    vision_patch = (
        cast(dict[str, Any], effective_vision.get("vision_patch"))
        if isinstance(effective_vision.get("vision_patch"), dict)
        else {}
    )
    delta_kinds, evidence, rejected_claims = validate_repair_delta_claims(
        requested_delta_kinds,
        agent_todo_summary=agent_todo_summary,
        agent_id=agent_id,
        advancement_policy=str(vision_patch.get("advancement_policy") or "").strip(),
        next_action_changed=bool(update.get("would_update")),
        vision_patch_written=agent_vision is not None,
        selected_todo_id=selected_todo_id,
        newest_first_runs=newest_first_runs,
    )
    if update.get("updated") is True:
        if "active_state_next_action" not in delta_kinds:
            delta_kinds.append("active_state_next_action")
        evidence.append(
            {
                "kind": "active_state_next_action",
                "source": "refresh_state_next_action_update",
                "updated": True,
            }
        )
    elif update.get("would_update") is True:
        evidence.append(
            {
                "kind": "active_state_next_action",
                "source": "refresh_state_next_action_update",
                "would_update": True,
                "dry_run": bool(dry_run),
            }
        )
    if agent_vision:
        if "goal_vision_patch" not in delta_kinds:
            delta_kinds.append("goal_vision_patch")
        evidence.append(
            {
                "kind": "goal_vision_patch",
                "source": "refresh_state_agent_vision",
                "state": agent_vision.get("state"),
                "agent_id": agent_vision.get("agent_id"),
                "budget_status": (
                    agent_vision.get("vision_budget", {}).get("status")
                    if isinstance(agent_vision.get("vision_budget"), dict)
                    else None
                ),
            }
        )

    contract = {
        "schema_version": REPAIR_DELTA_CONTRACT_SCHEMA_VERSION,
        "required": True,
        "delta_present": repair_delta_kinds_have_frontier_delta(delta_kinds),
        "delta_kinds": delta_kinds,
        "auto_evidence": evidence,
        "accepted_without_delta": False,
    }
    if rejected_claims:
        contract["rejected_claims"] = rejected_claims
    return contract
