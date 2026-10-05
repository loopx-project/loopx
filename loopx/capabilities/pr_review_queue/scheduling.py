from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from collections.abc import Sequence
from typing import Any, Mapping

SCHEDULING_POLICY_SCHEMA_VERSION = "pull_request_review_scheduling_policy_v1"
COMMUNITY_BACKLOG_AGE_HOURS = 24.0


class PullRequestSchedulingLane(str, Enum):
    AUTHENTICATED_DEVELOPER_OWNED = "authenticated_developer_owned"
    COMMUNITY_FEEDBACK = "community_feedback"
    COMMUNITY_AGED_BACKLOG = "community_aged_backlog"
    COMPOSITE_REMAINING = "composite_remaining"
    CURRENT_HEAD_CONCLUDED = "current_head_concluded"
    MERGED = "merged"
    DRAFT = "draft"
    CLOSED = "closed"


class PullRequestReviewPriority(str, Enum):
    OTHER_DEVELOPERS_FIRST = "other-developers-first"
    OWNER_FIRST = "owner-first"


DEFAULT_REVIEW_PRIORITY = PullRequestReviewPriority.OTHER_DEVELOPERS_FIRST


def is_owner_authored(item: Mapping[str, Any]) -> bool:
    """Read the typed queue identity; old packets retain login-only semantics."""
    return item.get("owner_authored", item.get("author_owned")) is True


_OWNER_FIRST_LANE_TIERS = {
    PullRequestSchedulingLane.AUTHENTICATED_DEVELOPER_OWNED: 0,
    PullRequestSchedulingLane.COMMUNITY_FEEDBACK: 1,
    PullRequestSchedulingLane.COMMUNITY_AGED_BACKLOG: 1,
    PullRequestSchedulingLane.COMPOSITE_REMAINING: 2,
    PullRequestSchedulingLane.CURRENT_HEAD_CONCLUDED: 3,
    PullRequestSchedulingLane.MERGED: 4,
    PullRequestSchedulingLane.DRAFT: 5,
    PullRequestSchedulingLane.CLOSED: 6,
}

_OTHER_DEVELOPERS_FIRST_LANE_TIERS = {
    PullRequestSchedulingLane.COMMUNITY_FEEDBACK: 0,
    PullRequestSchedulingLane.COMMUNITY_AGED_BACKLOG: 0,
    PullRequestSchedulingLane.COMPOSITE_REMAINING: 1,
    PullRequestSchedulingLane.AUTHENTICATED_DEVELOPER_OWNED: 2,
    PullRequestSchedulingLane.CURRENT_HEAD_CONCLUDED: 3,
    PullRequestSchedulingLane.MERGED: 4,
    PullRequestSchedulingLane.DRAFT: 5,
    PullRequestSchedulingLane.CLOSED: 6,
}


def normalize_review_priority(value: object) -> PullRequestReviewPriority:
    if isinstance(value, PullRequestReviewPriority):
        return value
    text = str(value or DEFAULT_REVIEW_PRIORITY.value).strip()
    try:
        return PullRequestReviewPriority(text)
    except ValueError:
        allowed = ", ".join(item.value for item in PullRequestReviewPriority)
        raise ValueError(f"review priority must be one of: {allowed}") from None


def classify_scheduling_lane(
    item: Mapping[str, Any],
) -> PullRequestSchedulingLane:
    state = str(item.get("state") or "OPEN").strip().upper()
    if item.get("is_draft") is True or item.get("isDraft") is True:
        return PullRequestSchedulingLane.DRAFT
    if state == "MERGED":
        return PullRequestSchedulingLane.MERGED
    if state == "CLOSED":
        return PullRequestSchedulingLane.CLOSED
    if not str(item.get("review_action_kind") or "").strip():
        return PullRequestSchedulingLane.CURRENT_HEAD_CONCLUDED
    if is_owner_authored(item):
        return PullRequestSchedulingLane.AUTHENTICATED_DEVELOPER_OWNED
    if item.get("community_feedback_ready") is True:
        return PullRequestSchedulingLane.COMMUNITY_FEEDBACK
    try:
        ready_age_hours = float(item.get("review_ready_age_hours") or 0.0)
    except (TypeError, ValueError):
        ready_age_hours = 0.0
    if ready_age_hours >= COMMUNITY_BACKLOG_AGE_HOURS:
        return PullRequestSchedulingLane.COMMUNITY_AGED_BACKLOG
    return PullRequestSchedulingLane.COMPOSITE_REMAINING


def scheduling_tier(
    item: Mapping[str, Any],
    *,
    review_priority: object = DEFAULT_REVIEW_PRIORITY,
) -> int:
    lane_text = str(item.get("scheduling_lane") or "").strip()
    try:
        lane = PullRequestSchedulingLane(lane_text)
    except ValueError:
        lane = classify_scheduling_lane(item)
    priority = normalize_review_priority(review_priority)
    tiers = (
        _OWNER_FIRST_LANE_TIERS
        if priority is PullRequestReviewPriority.OWNER_FIRST
        else _OTHER_DEVELOPERS_FIRST_LANE_TIERS
    )
    return tiers[lane]


def _timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _timestamp_epoch(value: object) -> float:
    parsed = _timestamp(value)
    return parsed.timestamp() if parsed is not None else 0.0


def community_feedback_ready(
    pr: Mapping[str, Any],
    *,
    review_ready_at: datetime | None,
) -> bool:
    raw_author = pr.get("author")
    author = str(
        raw_author.get("login") if isinstance(raw_author, Mapping) else raw_author or ""
    ).strip()
    raw_reviews = pr.get("reviews")
    reviews = (
        raw_reviews
        if isinstance(raw_reviews, Sequence) and not isinstance(raw_reviews, (str, bytes))
        else []
    )
    request_changes_at = [
        submitted_at
        for review in reviews
        if isinstance(review, Mapping)
        and str(review.get("state") or "").strip().upper() == "CHANGES_REQUESTED"
        and isinstance(review.get("author"), Mapping)
        and str(review["author"].get("login") or "").strip().casefold()
        != author.casefold()
        for submitted_at in (_timestamp(review.get("submittedAt")),)
        if submitted_at is not None
    ]
    return bool(
        review_ready_at is not None
        and request_changes_at
        and review_ready_at > max(request_changes_at)
    )


def scheduling_sort_key(
    item: Mapping[str, Any],
    *,
    review_priority: object = DEFAULT_REVIEW_PRIORITY,
) -> tuple[int, float, float, int]:
    return (
        scheduling_tier(item, review_priority=review_priority),
        _timestamp_epoch(item.get("review_ready_at")),
        _timestamp_epoch(item.get("created_at")),
        int(item.get("number") or 0),
    )


def build_scheduling_policy(
    *,
    authenticated_developer_login: str | None,
    review_priority: object = DEFAULT_REVIEW_PRIORITY,
    review_order: str | None = None,
    owner_logins: Sequence[str] = (),
) -> dict[str, Any]:
    login = str(authenticated_developer_login or "").strip() or None
    priority = normalize_review_priority(review_priority)
    if priority is PullRequestReviewPriority.OWNER_FIRST:
        ordered_actionable_tiers = [
            {
                "tier": 0,
                "id": "authenticated_developer_owned",
                "lanes": [
                    PullRequestSchedulingLane.AUTHENTICATED_DEVELOPER_OWNED.value
                ],
            },
            {
                "tier": 1,
                "id": "other_developer_feedback_and_aged_backlog",
                "lanes": [
                    PullRequestSchedulingLane.COMMUNITY_FEEDBACK.value,
                    PullRequestSchedulingLane.COMMUNITY_AGED_BACKLOG.value,
                ],
                "fast_feedback_slots_per_material_transition": 1,
                "tie_breakers": ["review_ready_at", "created_at", "number"],
            },
            {
                "tier": 2,
                "id": "other_developer_remaining",
                "lanes": [PullRequestSchedulingLane.COMPOSITE_REMAINING.value],
                "tie_breakers": ["review_ready_at", "created_at", "number"],
            },
        ]
    else:
        ordered_actionable_tiers = [
            {
                "tier": 0,
                "id": "other_developer_feedback_and_aged_backlog",
                "lanes": [
                    PullRequestSchedulingLane.COMMUNITY_FEEDBACK.value,
                    PullRequestSchedulingLane.COMMUNITY_AGED_BACKLOG.value,
                ],
                "fast_feedback_slots_per_material_transition": 1,
                "tie_breakers": ["review_ready_at", "created_at", "number"],
            },
            {
                "tier": 1,
                "id": "other_developer_remaining",
                "lanes": [PullRequestSchedulingLane.COMPOSITE_REMAINING.value],
                "tie_breakers": ["review_ready_at", "created_at", "number"],
            },
            {
                "tier": 2,
                "id": "authenticated_developer_owned",
                "lanes": [
                    PullRequestSchedulingLane.AUTHENTICATED_DEVELOPER_OWNED.value
                ],
            },
        ]
    if review_order == "reverse":
        ordered_actionable_tiers.reverse()
    if review_order is not None:
        for tier in ordered_actionable_tiers:
            tier.pop("fast_feedback_slots_per_material_transition", None)
    return {
        "schema_version": SCHEDULING_POLICY_SCHEMA_VERSION,
        "authority": "pull-request-review capability",
        "identity_basis": "request.reviewer_login + configured owner_logins" if owner_logins else "request.reviewer_login",
        **({"owner_logins": list(owner_logins)} if owner_logins else {}),
        "authenticated_developer_login": login,
        "review_priority": "owner-first" if review_order == "reverse" else priority.value,
        **({"review_order": review_order, "direction_rule": "reverse the complete forward actionable queue; retain inactive inventory order", "age_order": "newest-first" if review_order == "reverse" else "oldest-first"} if review_order is not None else {}),
        "owner_first_active": review_order == "reverse" if review_order is not None else priority is PullRequestReviewPriority.OWNER_FIRST,
        "other_developers_first_active": (
            review_order == "forward" if review_order is not None else priority is PullRequestReviewPriority.OTHER_DEVELOPERS_FIRST
        ),
        "other_developer_definition": (
            ("actionable PR whose author is outside owner_logins; " if owner_logins else
             "actionable PR whose author differs from request.reviewer_login; ") +
            "the capability does not infer organization membership or trust"
        ),
        "community_backlog_age_hours": COMMUNITY_BACKLOG_AGE_HOURS,
        "ordered_tiers": [
            *ordered_actionable_tiers,
            {
                "tier": 3,
                "id": PullRequestSchedulingLane.CURRENT_HEAD_CONCLUDED.value,
                "lanes": [PullRequestSchedulingLane.CURRENT_HEAD_CONCLUDED.value],
            },
            {
                "tier": 4,
                "id": PullRequestSchedulingLane.MERGED.value,
                "lanes": [PullRequestSchedulingLane.MERGED.value],
            },
            {
                "tier": 5,
                "id": PullRequestSchedulingLane.DRAFT.value,
                "lanes": [PullRequestSchedulingLane.DRAFT.value],
            },
            {
                "tier": 6,
                "id": PullRequestSchedulingLane.CLOSED.value,
                "lanes": [PullRequestSchedulingLane.CLOSED.value],
            },
        ],
        "manual_override_rule": (
            "Only capability configuration or an explicit request-scoped override may change this order; "
            "Todo or monitor prose and one-off author filters must not replace it."
        ),
    }


__all__ = [
    "COMMUNITY_BACKLOG_AGE_HOURS",
    "DEFAULT_REVIEW_PRIORITY",
    "PullRequestReviewPriority",
    "PullRequestSchedulingLane",
    "SCHEDULING_POLICY_SCHEMA_VERSION",
    "build_scheduling_policy",
    "classify_scheduling_lane",
    "community_feedback_ready",
    "normalize_review_priority",
    "scheduling_sort_key",
    "scheduling_tier",
]
