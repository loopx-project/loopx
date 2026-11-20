"""Pull-request scope-budget adapter over the single-owner TypeScript contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from ...control_plane.effect_runtime import effect_runtime_result
from .review_body import AUTHOR_OWNED_REQUEST_CHANGES_FALLBACK_TITLE


def _is_merge_commit(commit: Mapping[str, object]) -> bool:
    nested = commit.get("commit")
    nested_map = nested if isinstance(nested, Mapping) else None
    parents = commit.get("parents")
    if parents is None and nested_map is not None:
        parents = nested_map.get("parents")
    if isinstance(parents, Mapping):
        total = parents.get("totalCount")
        if type(total) is int:
            return total > 1
    elif isinstance(parents, Sequence) and not isinstance(parents, (str, bytes)):
        if len(parents) > 0:
            return len(parents) > 1
    headline = str(
        commit.get("messageHeadline")
        or (nested_map.get("messageHeadline") if nested_map is not None else "")
        or ""
    )
    return headline.startswith("Merge ")


def _extract_commit_counts(pr: Mapping[str, object]) -> tuple[int | None, int | None]:
    raw_commits = pr.get("commits")
    if raw_commits is None:
        return None, None
    if isinstance(raw_commits, Mapping):
        total = raw_commits.get("totalCount")
        nodes = raw_commits.get("nodes")
        if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes)):
            commit_nodes = [item for item in nodes if isinstance(item, Mapping)]
            commit_count = (
                int(total)
                if type(total) is int and total >= len(commit_nodes)
                else len(commit_nodes)
            )
            merge_count = sum(1 for item in commit_nodes if _is_merge_commit(item))
            return commit_count, merge_count
        if type(total) is int and total >= 0:
            return total, None
        return None, None
    if isinstance(raw_commits, Sequence) and not isinstance(raw_commits, (str, bytes)):
        commit_count = len(raw_commits)
        merge_count = sum(
            1
            for item in raw_commits
            if isinstance(item, Mapping) and _is_merge_commit(item)
        )
        return commit_count, merge_count
    return None, None


def _extract_request_changes_rounds(pr: Mapping[str, object]) -> int | None:
    raw_reviews = pr.get("reviews")
    if raw_reviews is None:
        return None
    if isinstance(raw_reviews, Mapping):
        nodes = raw_reviews.get("nodes")
        if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes)):
            reviews = nodes
        else:
            return None
    elif isinstance(raw_reviews, Sequence) and not isinstance(
        raw_reviews, (str, bytes)
    ):
        reviews = raw_reviews
    else:
        return None
    rounds = 0
    for review in reviews:
        if not isinstance(review, Mapping):
            continue
        state = str(review.get("state") or "").strip().upper()
        body = str(review.get("body") or "")
        if (
            state == "CHANGES_REQUESTED"
            or AUTHOR_OWNED_REQUEST_CHANGES_FALLBACK_TITLE in body
        ):
            rounds += 1
    return rounds


def assess_pr_scope_budget(
    pr: Mapping[str, object],
    *,
    changed_files: int,
    additions: int,
    deletions: int,
) -> dict[str, object]:
    """Classify PR scale, commit churn, merges, and review rounds via TS owner."""

    commit_count, merge_commit_count = _extract_commit_counts(pr)
    request_changes_rounds = _extract_request_changes_rounds(pr)
    payload = effect_runtime_result(
        "capabilities.change_scope_budget.assess",
        {
            "changed_files": max(0, int(changed_files)),
            "additions": max(0, int(additions)),
            "deletions": max(0, int(deletions)),
            "commit_count": commit_count,
            "merge_commit_count": merge_commit_count,
            "request_changes_rounds": request_changes_rounds,
        },
    )
    return cast(dict[str, object], payload)


def is_large_scope(item: Mapping[str, object]) -> bool:
    scope_budget = item.get("scope_budget")
    return isinstance(scope_budget, Mapping) and scope_budget.get("status") == "large_scope"


def pr_scope_budget_risk_note(
    scope_budget: Mapping[str, object] | None,
) -> str | None:
    if not (
        isinstance(scope_budget, Mapping)
        and scope_budget.get("status") == "large_scope"
    ):
        return None
    signals = scope_budget.get("triggered_signals")
    signal_text = (
        f" ({', '.join(str(item) for item in signals)})"
        if isinstance(signals, Sequence)
        and not isinstance(signals, (str, bytes))
        and signals
        else ""
    )
    return (
        f"Large scope / high churn{signal_text}: cohesive large features are allowed "
        "when proportionate to the goal; verify change_proportionality distinguishes "
        "legitimate feature breadth from accidental shims, duplicate paths, or unrelated scope drift."
    )
