from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .scheduling import (
    DEFAULT_REVIEW_PRIORITY,
    PullRequestReviewPriority,
    build_scheduling_policy,
    classify_scheduling_lane,
    is_owner_authored,
    normalize_review_priority,
    scheduling_sort_key,
    scheduling_tier,
)

OBSERVATION_SCHEMA_VERSION = "pull_request_review_queue_observation_v1"
CANDIDATE_SCHEMA_VERSION = "pull_request_review_candidate_v0"
TODO_PREVIEW_SCHEMA_VERSION = "pull_request_review_todo_preview_v0"
REVIEW_BACKLOG_SCHEMA_VERSION = "pr_review_queue_backlog_v0"
PROJECTION_ACK_SEMANTICS = "explicit_v1"

OBSERVATION_STATES = {
    "not_observed",
    "observed_unchanged",
    "material_transition",
}


def _selection_policy_text(priority: PullRequestReviewPriority) -> str:
    if priority is PullRequestReviewPriority.OWNER_FIRST:
        return (
            "authenticated-developer-owned actionable heads first; then other "
            "developer response heads and aged backlog; otherwise use the "
            "capability-ranked unprojected queue; exact head required"
        )
    return (
        "other-developer actionable heads first; then authenticated-developer-owned "
        "heads; response and age tie-breakers remain within each lane; exact head "
        "required"
    )


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _upper(value: Any, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip().upper()
    return text or default


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _check_snapshot(value: Any) -> dict[str, Any]:
    checks = value if isinstance(value, Mapping) else {}
    counts = checks.get("counts")
    compact_counts: dict[str, int] = {}
    if isinstance(counts, Mapping):
        for key in ("success", "failure", "pending", "unknown"):
            try:
                compact_counts[key] = max(0, int(counts.get(key) or 0))
            except (TypeError, ValueError):
                compact_counts[key] = 0
    return {
        "counts": compact_counts,
        "failures": _string_list(checks.get("failures")),
        "pending": _string_list(checks.get("pending")),
    }


def _pr_snapshot(
    item: Mapping[str, Any],
    *,
    review_priority: PullRequestReviewPriority,
) -> dict[str, Any]:
    snapshot = {
        "number": item.get("number"),
        "state": _upper(item.get("state"), "OPEN"),
        "head_oid": str(item.get("head_oid") or "").strip(),
        "review_decision": _upper(item.get("review_decision")),
        "checks": _check_snapshot(item.get("checks")),
        "is_draft": item.get("is_draft") is True,
        "merge_state": _upper(item.get("merge_state")),
        "review_ready_at": item.get("review_ready_at"),
        "review_ready_age_hours": item.get("review_ready_age_hours"),
        "created_at": item.get("created_at"),
        "author_owned": item.get("author_owned") is True,
        **({"owner_authored": item["owner_authored"]} if "owner_authored" in item else {}),
        "community_feedback_ready": item.get("community_feedback_ready") is True,
        "review_conclusion_status": str(
            (item.get("review_conclusion") or {}).get("status")
            if isinstance(item.get("review_conclusion"), Mapping)
            else ""
        ),
    }
    action = _candidate_action(item)
    snapshot["review_action_kind"] = action[0] if action is not None else None
    snapshot["scheduling_lane"] = classify_scheduling_lane(snapshot).value
    snapshot["scheduling_tier"] = scheduling_tier(
        snapshot, review_priority=review_priority
    )
    snapshot["review_priority"] = review_priority.value
    fingerprint_snapshot = {
        key: value
        for key, value in snapshot.items()
        if key != "review_ready_age_hours"
    }
    return snapshot | {"fingerprint": _fingerprint(fingerprint_snapshot)}


def _previous_observation(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    nested = value.get("autonomous_review")
    return nested if isinstance(nested, Mapping) else value


def _previous_items(value: Any) -> dict[str, Mapping[str, Any]]:
    observation = _previous_observation(value)
    state = str(observation.get("observation_state") or "")
    if state not in {"observed_unchanged", "material_transition"} and not (
        state == "not_observed" and observation.get("baseline_preserved") is True
    ):
        return {}
    items = observation.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        number = str(item.get("number") or "").strip()
        if number:
            result[number] = item
    return result


def _exact_head_key(number: Any, head_oid: Any) -> str | None:
    number_text = str(number or "").strip()
    head_text = str(head_oid or "").strip().lower()
    if not number_text.isdigit() or not re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}", head_text
    ):
        return None
    return f"{int(number_text)}@{head_text}"


def _normalize_exact_heads(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    normalized: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if "@" not in text:
            raise ValueError(
                f"{label} must use NUMBER@HEAD_OID with a 40- or 64-hex head"
            )
        number, head_oid = text.split("@", 1)
        key = _exact_head_key(number, head_oid)
        if key is None:
            raise ValueError(
                f"{label} must use NUMBER@HEAD_OID with a 40- or 64-hex head"
            )
        normalized.add(key)
    return sorted(normalized, key=lambda item: (int(item.split("@", 1)[0]), item))


def _previous_handled_exact_heads(value: Any, *, repository: str) -> list[str]:
    observation = _previous_observation(value)
    if str(observation.get("repository") or "").strip() != repository:
        return []
    return _normalize_exact_heads(
        observation.get("handled_exact_heads"), label="handled exact head"
    )


def _previous_candidate_exact_head(value: Any, *, repository: str) -> str | None:
    observation = _previous_observation(value)
    if str(observation.get("repository") or "").strip() != repository:
        return None
    candidate = observation.get("candidate")
    if isinstance(candidate, Mapping):
        key = _exact_head_key(candidate.get("number"), candidate.get("head_oid"))
        if key is not None:
            return key
    pending = str(observation.get("pending_candidate_exact_head") or "").strip()
    if "@" not in pending:
        return None
    number, head_oid = pending.split("@", 1)
    return _exact_head_key(number, head_oid)


def _previous_projected_exact_heads(value: Any, *, repository: str) -> list[str]:
    observation = _previous_observation(value)
    if str(observation.get("repository") or "").strip() != repository:
        return []
    if (
        observation.get("schema_version") != OBSERVATION_SCHEMA_VERSION
        or observation.get("candidate_projection_ack_semantics")
        != PROJECTION_ACK_SEMANTICS
    ):
        # v0 recorded emission as projection. Replaying those heads is safer than
        # stranding candidates whose Todo write never completed.
        return []
    return _normalize_exact_heads(
        observation.get("projected_candidate_exact_heads"),
        label="projected exact head",
    )


def _candidate_action(item: Mapping[str, Any]) -> tuple[str, str] | None:
    if item.get("is_draft") is True or _upper(item.get("state"), "OPEN") != "OPEN":
        return None
    head_oid = str(item.get("head_oid") or "").strip()
    if not item.get("number") or not re.fullmatch(
        r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", head_oid
    ):
        return None
    if "review_action_kind" in item:
        action_kind = str(item.get("review_action_kind") or "").strip()
        if not action_kind:
            return None
        if action_kind not in {
            "audit_pull_request_exact_head",
            "rereview_pull_request_exact_head",
            "qualify_pull_request_merge_readiness",
            "review_pull_request_exact_head",
        }:
            return None
        return (
            action_kind,
            "P0" if action_kind == "rereview_pull_request_exact_head" else "P1",
        )
    decision = _upper(item.get("review_decision"))
    if decision == "CHANGES_REQUESTED":
        return "rereview_pull_request_exact_head", "P0"
    if decision == "APPROVED":
        return "qualify_pull_request_merge_readiness", "P1"
    return "review_pull_request_exact_head", "P1"


def _candidate_packet(
    item: Mapping[str, Any],
    *,
    repository: str,
) -> dict[str, Any] | None:
    action = _candidate_action(item)
    if action is None:
        return None
    action_kind, priority = action
    number = item.get("number")
    head_oid = str(item.get("head_oid") or "").strip()
    url = str(item.get("url") or "").strip()
    task_repository = f"git:github.com/{repository}" if repository else None
    verb = {
        "audit_pull_request_exact_head": "Fresh-audit",
        "rereview_pull_request_exact_head": "Re-review",
        "qualify_pull_request_merge_readiness": "Qualify merge readiness for",
        "review_pull_request_exact_head": "Review",
    }[action_kind]
    text = (
        f"[{priority}] {verb} PR #{number} at exact head {head_oid}; "
        "read the diff and checks, publish a review state matching the evidence, "
        "and route any merge through repository policy."
    )
    return {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "repository": repository or None,
        "number": number,
        "url": url or None,
        "head_oid": head_oid,
        "review_decision": _upper(item.get("review_decision")),
        "merge_state": _upper(item.get("merge_state")),
        "checks": _check_snapshot(item.get("checks")),
        "fingerprint": item.get("fingerprint"),
        "todo_preview": {
            "schema_version": TODO_PREVIEW_SCHEMA_VERSION,
            "role": "agent",
            "priority": priority,
            "task_class": "advancement_task",
            "action_kind": action_kind,
            "task_repository": task_repository,
            "target_key": f"github-pr-review:{repository}#{number}@{head_oid}",
            "required_capabilities": ["network", "external_evidence_poll"],
            "text": text,
        },
    }


def _review_backlog(
    items: Sequence[Mapping[str, Any]],
    *,
    handled_set: set[str],
    pending_candidate_exact_head: str | None,
) -> dict[str, Any]:
    actionable_unhandled_count = 0
    for item in items:
        exact_head_key = _exact_head_key(item.get("number"), item.get("head_oid"))
        if exact_head_key is None or exact_head_key in handled_set:
            continue
        if _candidate_action(item) is None:
            continue
        actionable_unhandled_count += 1
    active = actionable_unhandled_count > 0 or bool(pending_candidate_exact_head)
    return {
        "schema_version": REVIEW_BACKLOG_SCHEMA_VERSION,
        "actionable_unhandled_count": actionable_unhandled_count,
        "pending_candidate_exact_head": pending_candidate_exact_head,
        "recommended_poll_interval_minutes": 3 if active else 15,
        "recommended_cadence": "active_review" if active else "quiet_wait",
    }


def build_pull_request_review_queue_observation(
    *,
    repository: str | None,
    pull_requests: Sequence[Mapping[str, Any]],
    result_completeness: Mapping[str, Any],
    previous_observation: Mapping[str, Any] | None = None,
    handled_exact_heads: Sequence[str] = (),
    projected_exact_heads: Sequence[str] = (),
    authenticated_developer_login: str | None = None,
    review_priority: object = DEFAULT_REVIEW_PRIORITY,
    review_order: str | None = None,
    owner_logins: Sequence[str] = (),
) -> dict[str, Any]:
    """Build one read-only observation and at most one exact-head candidate."""

    normalized_repository = str(repository or "").strip()
    normalized_priority = normalize_review_priority(review_priority)
    previous = _previous_observation(previous_observation)
    previous_handled = _previous_handled_exact_heads(
        previous_observation, repository=normalized_repository
    )
    supplied_handled = _normalize_exact_heads(
        handled_exact_heads, label="handled exact head"
    )
    supplied_projected = _normalize_exact_heads(
        projected_exact_heads, label="projected exact head"
    )
    allowed_supplied = set(previous_handled)
    previous_candidate = _previous_candidate_exact_head(
        previous_observation, repository=normalized_repository
    )
    previous_projected = set(
        _previous_projected_exact_heads(
            previous_observation,
            repository=normalized_repository,
        )
    )
    projected_set = set(previous_projected)
    allowed_supplied.update(previous_projected)
    if previous_candidate:
        allowed_supplied.add(previous_candidate)
    unexpected_handled = [
        item for item in supplied_handled if item not in allowed_supplied
    ]
    if unexpected_handled:
        raise ValueError(
            "handled exact head must match the prior candidate or a persisted "
            f"handled cursor: {', '.join(unexpected_handled)}"
        )
    unexpected_projected = [
        item for item in supplied_projected if item not in allowed_supplied
    ]
    if unexpected_projected:
        raise ValueError(
            "projected exact head must match the prior candidate or a persisted "
            f"projection cursor: {', '.join(unexpected_projected)}"
        )
    handled = sorted(
        {
            *previous_handled,
            *supplied_handled,
        },
        key=lambda item: (int(item.split("@", 1)[0]), item),
    )
    handled_set = set(handled)
    projected_set.update(supplied_projected)
    projected_set = {key for key in projected_set if key not in handled_set}
    projected_sorted = sorted(
        projected_set,
        key=lambda item: (int(item.split("@", 1)[0]), item),
    )
    previous_fingerprint = (
        str(
            previous.get("queue_fingerprint")
            or previous.get("previous_queue_fingerprint")
            or ""
        )
        or None
    )
    previous_items = list(_previous_items(previous_observation).values())
    if result_completeness.get("complete") is not True:
        pending_candidate_exact_head = (
            previous_candidate
            if previous_candidate and previous_candidate not in handled_set
            else None
        )
        return {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "repository": normalized_repository or None,
            "observation_state": "not_observed",
            "reason": "complete_open_queue_required",
            "queue_fingerprint": None,
            "previous_queue_fingerprint": previous_fingerprint,
            "baseline_preserved": previous_fingerprint is not None,
            "items": previous_items,
            "queue_size": None,
            "changed_pr_numbers": [],
            "removed_pr_numbers": [],
            "candidate": None,
            "candidate_count": 0,
            "candidate_selection_reason": None,
            "pending_candidate_exact_head": pending_candidate_exact_head,
            "review_backlog": _review_backlog(
                pull_requests,
                handled_set=handled_set,
                pending_candidate_exact_head=pending_candidate_exact_head,
            ),
            "handled_exact_heads": handled,
            "handled_exact_head_count": len(handled),
            "projected_candidate_exact_heads": projected_sorted,
            "projected_candidate_count": len(projected_sorted),
            "candidate_projection_ack_semantics": PROJECTION_ACK_SEMANTICS,
            "selection_policy": f"first unhandled/unprojected actionable head in the configured {review_order} queue" if review_order is not None else _selection_policy_text(normalized_priority),
            "scheduling_policy": build_scheduling_policy(
                authenticated_developer_login=authenticated_developer_login,
                review_priority=normalized_priority,
                review_order=review_order,
                owner_logins=owner_logins,
            ),
            "write_authority_granted": False,
            "external_write_performed": False,
        }

    normalized_ranked_items: list[dict[str, Any]] = []
    for item in pull_requests:
        if _upper(item.get("state"), "OPEN") != "OPEN":
            continue
        snapshot = _pr_snapshot(item, review_priority=normalized_priority)
        snapshot.update(
            {
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("url") or "").strip(),
            }
        )
        normalized_ranked_items.append(snapshot)
    normalized_ranked_items.sort(
        key=lambda item: scheduling_sort_key(
            item, review_priority=normalized_priority
        )
    )
    from .order import order_queue
    normalized_ranked_items = order_queue(normalized_ranked_items, review_order)
    ranked_items = [
        {**item, "rank": rank}
        for rank, item in enumerate(normalized_ranked_items, start=1)
    ]

    current_exact_heads = {
        key
        for item in ranked_items
        if (key := _exact_head_key(item.get("number"), item.get("head_oid")))
        is not None
    }
    actionable_exact_heads = {
        key
        for item in ranked_items
        if _candidate_action(item) is not None
        and (key := _exact_head_key(item.get("number"), item.get("head_oid")))
        is not None
    }
    active_handled = [item for item in handled if item in current_exact_heads]
    handled_set = set(active_handled)
    projected_set = {
        key
        for key in projected_set
        if key in current_exact_heads and key not in handled_set
    }
    projected_sorted = sorted(
        projected_set,
        key=lambda item: (int(item.split("@", 1)[0]), item),
    )

    queue_items = [
        {
            key: item[key]
            for key in (
                "number",
                "fingerprint",
                "head_oid",
                "review_decision",
                "review_action_kind",
            )
            if key in item
        }
        for item in sorted(ranked_items, key=lambda row: str(row.get("number")))
    ]
    queue_fingerprint = _fingerprint(
        {
            "repository": normalized_repository,
            "review_priority": normalized_priority.value,
            **({"review_order": review_order} if review_order is not None else {}),
            "items": queue_items,
        }
    )
    previous_repository = str(previous.get("repository") or "").strip()
    prior_items = (
        _previous_items(previous_observation)
        if previous_repository == normalized_repository
        else {}
    )
    changed = [
        item
        for item in ranked_items
        if str(prior_items.get(str(item.get("number")), {}).get("fingerprint") or "")
        != str(item.get("fingerprint") or "")
    ]
    removed_numbers = sorted(
        number
        for number in prior_items
        if number not in {str(item.get("number")) for item in ranked_items}
    )
    unchanged = previous_fingerprint == queue_fingerprint
    observation_state = "observed_unchanged" if unchanged else "material_transition"

    candidate = None
    candidate_selection_reason = None
    for item in ranked_items:
        if review_order is None and ((
            normalized_priority is PullRequestReviewPriority.OWNER_FIRST
            and not is_owner_authored(item)
        ) or (
            normalized_priority is PullRequestReviewPriority.OTHER_DEVELOPERS_FIRST
            and is_owner_authored(item)
        )):
            continue
        exact_head_key = _exact_head_key(item.get("number"), item.get("head_oid"))
        if exact_head_key in handled_set or exact_head_key in projected_set:
            continue
        candidate = _candidate_packet(item, repository=normalized_repository)
        if candidate is not None:
            candidate_selection_reason = (
                "configured_queue_order" if review_order is not None else
                "authenticated_developer_owned_first"
                if normalized_priority is PullRequestReviewPriority.OWNER_FIRST
                else "other_developer_owned_first"
            )
            break
    if observation_state == "material_transition":
        for item in changed if candidate is None else []:
            prior = prior_items.get(str(item.get("number")), {})
            action = _candidate_action(item)
            became_approved = (
                action is not None
                and action[0] == "qualify_pull_request_merge_readiness"
                and _upper(prior.get("review_decision")) != "APPROVED"
            )
            is_author_response = (
                not is_owner_authored(item)
                and bool(prior)
                and _upper(prior.get("review_decision")) == "CHANGES_REQUESTED"
                and str(prior.get("head_oid") or "").strip().lower()
                != str(item.get("head_oid") or "").strip().lower()
            )
            if not is_author_response and not became_approved:
                continue
            exact_head_key = _exact_head_key(item.get("number"), item.get("head_oid"))
            if exact_head_key in handled_set:
                continue
            candidate = _candidate_packet(item, repository=normalized_repository)
            if candidate is not None:
                candidate_selection_reason = (
                    "author_response_fast_feedback"
                    if is_author_response
                    else "approval_merge_readiness_transition"
                )
                break
    if candidate is None:
        for item in ranked_items:
            exact_head_key = _exact_head_key(item.get("number"), item.get("head_oid"))
            if exact_head_key in handled_set or exact_head_key in projected_set:
                continue
            candidate = _candidate_packet(item, repository=normalized_repository)
            if candidate is not None:
                candidate_selection_reason = "age_fair_backlog_progression"
                break
    candidate_exact_head = (
        _exact_head_key(candidate.get("number"), candidate.get("head_oid"))
        if candidate
        else None
    )
    pending_candidate_exact_head = candidate_exact_head
    if (
        pending_candidate_exact_head is None
        and previous_candidate in actionable_exact_heads
        and previous_candidate not in handled_set
    ):
        pending_candidate_exact_head = previous_candidate
    projected_sorted = sorted(
        projected_set,
        key=lambda item: (int(item.split("@", 1)[0]), item),
    )

    review_backlog = _review_backlog(
        ranked_items,
        handled_set=handled_set,
        pending_candidate_exact_head=pending_candidate_exact_head,
    )

    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "repository": normalized_repository or None,
        "observation_state": observation_state,
        "reason": (
            "queue_fingerprint_unchanged"
            if unchanged
            else "initial_complete_observation"
            if not previous_fingerprint
            else "review_material_fingerprint_changed"
        ),
        "queue_fingerprint": queue_fingerprint,
        "previous_queue_fingerprint": previous_fingerprint,
        "baseline_preserved": True,
        "items": queue_items,
        "queue_size": len(queue_items),
        "changed_pr_numbers": [item.get("number") for item in changed],
        "removed_pr_numbers": removed_numbers,
        "candidate": candidate,
        "candidate_count": 1 if candidate else 0,
        "candidate_selection_reason": candidate_selection_reason,
        "pending_candidate_exact_head": pending_candidate_exact_head,
        "review_backlog": review_backlog,
        "handled_exact_heads": active_handled,
        "handled_exact_head_count": len(active_handled),
        "projected_candidate_exact_heads": projected_sorted,
        "projected_candidate_count": len(projected_sorted),
        "candidate_projection_ack_semantics": PROJECTION_ACK_SEMANTICS,
        "selection_policy": f"first unhandled/unprojected actionable head in the configured {review_order} queue" if review_order is not None else _selection_policy_text(normalized_priority),
        "scheduling_policy": build_scheduling_policy(
            authenticated_developer_login=authenticated_developer_login,
            review_priority=normalized_priority,
            review_order=review_order,
            owner_logins=owner_logins,
        ),
        "write_authority_granted": False,
        "external_write_performed": False,
    }


__all__ = [
    "CANDIDATE_SCHEMA_VERSION",
    "OBSERVATION_SCHEMA_VERSION",
    "OBSERVATION_STATES",
    "TODO_PREVIEW_SCHEMA_VERSION",
    "build_pull_request_review_queue_observation",
]
