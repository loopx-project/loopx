from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from ...domain_packs.issue_fix import issue_fix_maintainer_correction_entries

from .correction_settlement import (
    MAINTAINER_CORRECTION_ADMISSION_BASES,
    MAINTAINER_CORRECTION_DISPOSITIONS,
    MAINTAINER_CORRECTION_OPERATION_ID_PREFIX,
    MAINTAINER_CORRECTION_REJECTION_REASONS,
    TERMINAL_PR_STATES as TERMINAL_PR_STATES,
    _timestamp,
    _upper_label,
    attach_correction_todo_intent as _attach_correction_todo_intent,
    build_issue_fix_transition_preview as _transition_preview,
    evaluate_issue_fix_maintainer_correction_admission,
    maintainer_correction_transition as _maintainer_correction_transition,
    normalise_issue_fix_maintainer_correction_input as normalise_issue_fix_maintainer_correction_input,
    normalise_issue_fix_pr_source_events,
)
from .github_public import (
    MAINTAINER_ASSOCIATIONS as MAINTAINER_ASSOCIATIONS,
    fetch_github_pull_request_event_metadata,
)
from .metadata_preview import (
    normalise_github_issue_link_reference,
    normalise_github_issue_reference,
)


ISSUE_FIX_PR_LIFECYCLE_MONITOR_SCHEMA_VERSION = "issue_fix_pr_lifecycle_monitor_v0"
ISSUE_FIX_PR_GROUPED_MONITOR_PROJECTION_SCHEMA_VERSION = (
    "issue_fix_pr_grouped_monitor_projection_v1"
)
FAILING_CHECK_STATES = {
    "ACTION_REQUIRED",
    "CANCELLED",
    "ERROR",
    "FAILURE",
    "FAILED",
    "STARTUP_FAILURE",
    "TIMED_OUT",
}
PENDING_CHECK_STATES = {
    "EXPECTED",
    "IN_PROGRESS",
    "PENDING",
    "QUEUED",
    "REQUESTED",
    "WAITING",
}
PASSING_CHECK_STATES = {"NEUTRAL", "SKIPPED", "SUCCESS"}
BRANCH_REPLAN_MERGE_STATES = {"BEHIND", "DIRTY"}
_VERIFIED_REQUESTED_REPOSITORY_FIELD = "_loopx_verified_requested_repository"

_REVIEW_RESPONSE_QUERY = """
query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){
      reviewThreads(first:100){
        totalCount
        pageInfo{hasNextPage}
        nodes{isResolved}
      }
      reviews(last:100){
        nodes{state submittedAt}
      }
      commits(last:1){
        nodes{commit{committedDate}}
      }
    }
  }
}
""".strip()


def _safe_bool(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else False


def _safe_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _review_response_observation(
    provider_payload: Mapping[str, Any], *, review_decision: str
) -> dict[str, Any]:
    summary = provider_payload.get("reviewThreadSummary")
    thread_summary = summary if isinstance(summary, Mapping) else {}
    total = _safe_count(thread_summary.get("totalCount"))
    resolved = _safe_count(thread_summary.get("resolvedCount"))
    unresolved = _safe_count(thread_summary.get("unresolvedCount"))
    complete = thread_summary.get("complete") is True
    changes_requested_at = provider_payload.get("latestChangesRequestedAt")
    head_committed_at = provider_payload.get("headCommittedAt")
    requested_time = _timestamp(changes_requested_at)
    head_time = _timestamp(head_committed_at)
    addressed = bool(
        review_decision == "CHANGES_REQUESTED"
        and complete
        and total is not None
        and total > 0
        and resolved == total
        and unresolved == 0
        and requested_time is not None
        and head_time is not None
        and head_time > requested_time
    )
    if addressed:
        status = "changes_addressed_awaiting_rereview"
    elif review_decision == "CHANGES_REQUESTED":
        status = "changes_requested_unverified"
    else:
        status = "not_applicable"
    return {
        "schema_version": "issue_fix_pr_review_response_v0",
        "status": status,
        "metadata_status": str(
            provider_payload.get("reviewResponseMetadataStatus") or "not_fetched"
        ),
        "thread_count": total,
        "resolved_thread_count": resolved,
        "unresolved_thread_count": unresolved,
        "thread_page_complete": complete,
        "latest_changes_requested_at": changes_requested_at,
        "head_committed_at": head_committed_at,
        "raw_reviews_captured": False,
        "raw_thread_comments_captured": False,
    }


def _normalise_check_item(item: Mapping[str, Any]) -> str:
    state = (
        item.get("conclusion")
        or item.get("state")
        or item.get("status")
        or item.get("workflowState")
    )
    status = _upper_label(state)
    if status in {"COMPLETED", "COMPLETED_SUCCESSFULLY"}:
        conclusion = _upper_label(item.get("conclusion"), "")
        return conclusion or "SUCCESS"
    return status


def _check_rollup(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_checks = payload.get("statusCheckRollup")
    failing = 0
    pending = 0
    passing = 0
    unknown = 0
    names: list[str] = []
    if isinstance(raw_checks, Sequence) and not isinstance(raw_checks, (str, bytes)):
        for item in raw_checks:
            if not isinstance(item, Mapping):
                unknown += 1
                continue
            state = _normalise_check_item(item)
            name = str(item.get("name") or item.get("context") or "").strip()
            if name:
                names.append(name[:80])
            if state in FAILING_CHECK_STATES:
                failing += 1
            elif state in PENDING_CHECK_STATES:
                pending += 1
            elif state in PASSING_CHECK_STATES:
                passing += 1
            else:
                unknown += 1
    else:
        failing = _safe_count(payload.get("failing_checks")) or 0
        pending = _safe_count(payload.get("pending_checks")) or 0
        passing = _safe_count(payload.get("passing_checks")) or 0
        unknown = _safe_count(payload.get("unknown_checks")) or 0

    total = failing + pending + passing + unknown
    if failing:
        aggregate = "FAILING"
    elif pending:
        aggregate = "PENDING"
    elif unknown and not passing:
        aggregate = "UNKNOWN"
    elif total:
        aggregate = "PASSING"
    else:
        aggregate = "NO_CHECKS"
    return {
        "schema_version": "issue_fix_pr_check_rollup_v0",
        "aggregate": aggregate,
        "failing_count": failing,
        "pending_count": pending,
        "passing_count": passing,
        "unknown_count": unknown,
        "check_name_sample": names[:5],
        "raw_status_captured": False,
        "log_output_captured": False,
    }


def _build_observation(
    *,
    repo: str,
    pr_ref: str,
    issue_ref: str | None,
    reference: Mapping[str, Any],
    provider_payload: Mapping[str, Any],
) -> dict[str, Any]:
    state = _upper_label(provider_payload.get("state"), "OPEN")
    if state == "OPEN":
        merged_at = provider_payload.get("mergedAt") or provider_payload.get(
            "merged_at"
        )
        merged = provider_payload.get("merged")
        if merged_at or merged is True:
            state = "MERGED"
    review_decision = _upper_label(provider_payload.get("reviewDecision"))
    merge_state = _upper_label(provider_payload.get("mergeStateStatus"))
    linked_issue_ref = (
        normalise_github_issue_link_reference(issue_ref) if issue_ref else ""
    )
    raw_commits = provider_payload.get("commits")
    commit_count = _safe_count(provider_payload.get("commitCount"))
    if (
        commit_count is None
        and isinstance(raw_commits, Sequence)
        and not isinstance(raw_commits, (str, bytes))
    ):
        commit_count = len(raw_commits)
    return {
        "schema_version": "issue_fix_pr_lifecycle_observation_v0",
        "repo": repo,
        "pr_ref": pr_ref,
        "issue_ref": linked_issue_ref or None,
        "kind": "pull_request",
        "number": reference.get("number"),
        "state": state,
        "review_decision": review_decision,
        "merge_state_status": merge_state,
        "is_draft": _safe_bool(provider_payload.get("isDraft")),
        "created_at": provider_payload.get("createdAt")
        or provider_payload.get("created_at"),
        "updated_at": provider_payload.get("updatedAt")
        or provider_payload.get("updated_at"),
        "merged_at": provider_payload.get("mergedAt")
        or provider_payload.get("merged_at"),
        "closed_at": provider_payload.get("closedAt")
        or provider_payload.get("closed_at"),
        "permalink": reference.get("permalink") or provider_payload.get("url"),
        "head_commit_ref": provider_payload.get("headRefOid")
        or provider_payload.get("head_commit_ref"),
        "commit_count": commit_count,
        "checks": _check_rollup(provider_payload),
        "review_response": _review_response_observation(
            provider_payload, review_decision=review_decision
        ),
        "body_captured": False,
        "comment_bodies_captured": False,
        "timeline_captured": False,
        "log_output_captured": False,
        "response_payload_captured": False,
    }


def _first_push_ci_evidence(
    observation: Mapping[str, Any], *, observed_at: str | None
) -> dict[str, Any] | None:
    checks = observation.get("checks")
    rollup = checks if isinstance(checks, Mapping) else {}
    aggregate = str(rollup.get("aggregate") or "").upper()
    if observation.get("commit_count") != 1 or aggregate not in {
        "PASSING",
        "FAILING",
    }:
        return None
    evidence = {
        "schema_version": "issue_fix_first_push_ci_evidence_v0",
        "pr_ref": observation.get("pr_ref"),
        "status": aggregate,
        "observed_at": observed_at,
        "source": "single_commit_terminal_check_rollup",
        "raw_check_status_captured": False,
        "check_logs_captured": False,
    }
    head_commit_ref = str(observation.get("head_commit_ref") or "").strip()
    if head_commit_ref:
        evidence["head_commit_ref"] = head_commit_ref
    return evidence


def _observation_fingerprint(observation: Mapping[str, Any]) -> str:
    text = json.dumps(observation, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def issue_fix_grouped_monitor_identity(
    *, repository: str, state_bucket: str
) -> tuple[str, str]:
    """Return stable repository-scoped monitor target and action identities."""

    repository_key = repository.casefold().replace("/", "--")
    bucket_key = state_bucket.replace("_", "-")
    return (
        f"github-pr-state-{repository_key}-{bucket_key}",
        f"issue_fix_pr_state_{state_bucket}_monitor",
    )


def _grouped_monitor_projection(
    observation: Mapping[str, Any], transition: Mapping[str, Any]
) -> dict[str, Any]:
    state = str(observation.get("state") or "UNKNOWN")
    review_decision = str(observation.get("review_decision") or "UNKNOWN")
    merge_state = str(observation.get("merge_state_status") or "UNKNOWN")
    checks = observation.get("checks")
    check_rollup = checks if isinstance(checks, Mapping) else {}
    review_response = observation.get("review_response")
    review_response_status = (
        str(review_response.get("status") or "")
        if isinstance(review_response, Mapping)
        else ""
    )

    if state in TERMINAL_PR_STATES:
        state_bucket = "terminal"
    elif int(check_rollup.get("failing_count") or 0):
        state_bucket = "checks_failed"
    elif review_decision == "CHANGES_REQUESTED":
        state_bucket = (
            "changes_addressed_awaiting_rereview"
            if review_response_status == "changes_addressed_awaiting_rereview"
            else "changes_requested"
        )
    elif merge_state in BRANCH_REPLAN_MERGE_STATES:
        state_bucket = "branch_blocked"
    elif bool(observation.get("is_draft")):
        state_bucket = "draft"
    elif int(check_rollup.get("pending_count") or 0):
        state_bucket = "checks_pending"
    elif review_decision == "APPROVED":
        state_bucket = "ready_to_merge"
    else:
        state_bucket = "review_required"

    terminal = state_bucket == "terminal"
    member_key = f"{observation.get('repo')}#{observation.get('number')}"
    repository = str(observation.get("repo") or "unknown")
    target_key, action_kind = issue_fix_grouped_monitor_identity(
        repository=repository,
        state_bucket=state_bucket,
    )
    return {
        "schema_version": ISSUE_FIX_PR_GROUPED_MONITOR_PROJECTION_SCHEMA_VERSION,
        "scope": "repository_pr_lifecycle_state",
        "repository": repository,
        "state_bucket": state_bucket,
        "target_key": None if terminal else target_key,
        "action_kind": None if terminal else action_kind,
        "member_key": member_key,
        "member_operation": "remove" if terminal else "upsert",
        "materialize_nonempty_bucket_monitor": not terminal,
        "creates_per_pr_continuous_monitor_todo": False,
        "per_pr_material_action": "one_shot_advancement_todo",
        "external_notification_granularity": "one_pr_per_message",
        "empty_bucket_policy": "complete_monitor",
        "transition_decision": transition.get("decision"),
        "todo_write_performed": False,
    }


def _decide_transition(observation: Mapping[str, Any]) -> dict[str, Any]:
    state = str(observation.get("state") or "UNKNOWN")
    review_decision = str(observation.get("review_decision") or "UNKNOWN")
    merge_state = str(observation.get("merge_state_status") or "UNKNOWN")
    checks = observation.get("checks")
    check_rollup = checks if isinstance(checks, Mapping) else {}
    failing_checks = int(check_rollup.get("failing_count") or 0)
    pending_checks = int(check_rollup.get("pending_count") or 0)
    review_response = observation.get("review_response")
    review_response_status = (
        str(review_response.get("status") or "")
        if isinstance(review_response, Mapping)
        else ""
    )
    is_draft = bool(observation.get("is_draft"))

    if state == "MERGED":
        return _transition_preview(
            decision="no_followup",
            action_kind="issue_fix_pr_merged_no_followup",
            priority="P1",
            task_class="terminal_transition",
            reason=(
                "PR is merged; close the monitor with no follow-up even if stale "
                "review metadata still says review is required."
            ),
            terminal_state_precedence=True,
            material_change=True,
        )
    if state == "CLOSED":
        return _transition_preview(
            decision="no_followup",
            action_kind="issue_fix_pr_closed_no_followup",
            priority="P1",
            task_class="terminal_transition",
            reason="PR is closed without an open continuation; close the monitor.",
            terminal_state_precedence=True,
            material_change=True,
        )
    if failing_checks:
        return _transition_preview(
            decision="runnable_successor",
            action_kind="issue_fix_ci_failure_replan",
            priority="P0",
            task_class="advancement_task",
            reason="PR checks are failing; inspect public check summary and plan a fix.",
            material_change=True,
        )
    if review_decision == "CHANGES_REQUESTED":
        if review_response_status == "changes_addressed_awaiting_rereview":
            return _transition_preview(
                decision="monitor_continuation",
                action_kind="issue_fix_review_changes_addressed_monitor",
                priority="P2",
                reason=(
                    "Review changes have a newer repair commit and every fetched "
                    "review thread is resolved; wait for reviewer re-approval."
                ),
            )
        return _transition_preview(
            decision="runnable_successor",
            action_kind="issue_fix_review_changes_replan",
            priority="P0",
            task_class="advancement_task",
            reason="Maintainer review requested changes; derive a follow-up patch or blocker.",
            material_change=True,
        )
    if merge_state in BRANCH_REPLAN_MERGE_STATES:
        return _transition_preview(
            decision="runnable_successor",
            action_kind="issue_fix_branch_or_merge_blocker_replan",
            priority="P1",
            task_class="advancement_task",
            reason="PR branch is stale or conflicted; decide rebase, blocker note, or no-follow-up.",
            material_change=True,
        )
    if is_draft:
        return _transition_preview(
            decision="user_gate",
            action_kind="approve_pr_ready_for_review_or_keep_draft",
            priority="P1",
            role="user",
            task_class="user_gate",
            reason="PR is still draft; owner must approve marking ready or keep monitoring.",
            material_change=True,
        )
    if pending_checks:
        return _transition_preview(
            decision="monitor_continuation",
            action_kind="issue_fix_pr_checks_pending_monitor",
            priority="P2",
            reason="PR checks are still pending; continue monitor without spawning work.",
        )
    return _transition_preview(
        decision="monitor_continuation",
        action_kind="issue_fix_pr_wait_for_review_or_merge_monitor",
        priority="P2",
        reason="No actionable PR state change; keep watching for CI, review, merge, or maintainer signal.",
    )


def _fetch_github_review_response_metadata(
    *, owner: str, repo_name: str, number: int, timeout_seconds: int
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "graphql",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={repo_name}",
                "-F",
                f"number={number}",
                "-f",
                f"query={_REVIEW_RESPONSE_QUERY}",
            ],
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"reviewResponseMetadataStatus": "unavailable"}
    if result.returncode != 0:
        return {"reviewResponseMetadataStatus": "unavailable"}
    try:
        response = json.loads(result.stdout)
        pull_request = response["data"]["repository"]["pullRequest"]
        threads = pull_request["reviewThreads"]
        thread_nodes = threads["nodes"]
        reviews = pull_request["reviews"]["nodes"]
        commit_nodes = pull_request["commits"]["nodes"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return {"reviewResponseMetadataStatus": "unavailable"}
    if not all(isinstance(items, list) for items in (thread_nodes, reviews, commit_nodes)):
        return {"reviewResponseMetadataStatus": "unavailable"}

    total = _safe_count(threads.get("totalCount"))
    page_info = threads.get("pageInfo")
    page_complete = bool(
        isinstance(page_info, Mapping)
        and page_info.get("hasNextPage") is False
        and total == len(thread_nodes)
    )
    resolved = sum(
        1
        for item in thread_nodes
        if isinstance(item, Mapping) and item.get("isResolved") is True
    )
    changes_requested_at = max(
        (
            str(item.get("submittedAt"))
            for item in reviews
            if isinstance(item, Mapping)
            and _upper_label(item.get("state"), "") == "CHANGES_REQUESTED"
            and _timestamp(item.get("submittedAt")) is not None
        ),
        default="",
    )
    head_committed_at = ""
    if commit_nodes:
        head = commit_nodes[-1]
        commit = head.get("commit") if isinstance(head, Mapping) else None
        if isinstance(commit, Mapping) and _timestamp(commit.get("committedDate")):
            head_committed_at = str(commit.get("committedDate"))
    return {
        "reviewResponseMetadataStatus": "available",
        "reviewThreadSummary": {
            "totalCount": total,
            "resolvedCount": resolved,
            "unresolvedCount": (
                total - resolved if total is not None and page_complete else None
            ),
            "complete": page_complete,
        },
        "latestChangesRequestedAt": changes_requested_at or None,
        "headCommittedAt": head_committed_at or None,
    }


def fetch_github_pr_lifecycle_payload(
    reference: Mapping[str, Any],
    *,
    timeout_seconds: int = 10,
    compact: bool = False,
) -> dict[str, Any]:
    repo = str(reference.get("repo") or "")
    number = reference.get("number")
    if "/" not in repo or not isinstance(number, int):
        raise ValueError("--fetch-metadata requires a numeric GitHub pull request URL")
    owner, repo_name = repo.split("/", 1)
    repo_slug = f"{quote(owner, safe='')}/{quote(repo_name, safe='')}"
    result = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            repo_slug,
            "--json",
            ",".join(
                ["state", "url", "mergedAt", "closedAt"] if compact else [
                    "baseRefName",
                    "closingIssuesReferences",
                    "closedAt",
                    "commits",
                    "createdAt",
                    "headRefName",
                    "headRefOid",
                    "isDraft",
                    "mergeStateStatus",
                    "mergedAt",
                    "reviewDecision",
                    "state",
                    "statusCheckRollup",
                    "updatedAt",
                    "url",
                ]
            ),
        ],
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "GitHub PR lifecycle fetch failed; install/authenticate gh or use --metadata-json"
        )
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise ValueError("GitHub PR lifecycle fetch must return a JSON object")
    provider_reference = normalise_github_issue_reference(
        repo=repo,
        issue_ref=f"pull_{number}",
        url=str(payload.get("url") or ""),
    )
    if (
        provider_reference.get("kind") != "pull_request"
        or provider_reference.get("number") != number
    ):
        raise ValueError("GitHub PR lifecycle provider identity mismatch")
    if str(provider_reference["repo"]).lower() != repo.lower():
        redirect = subprocess.run(
            ["gh", "repo", "view", repo, "--json", "nameWithOwner"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
        if redirect.returncode != 0:
            raise RuntimeError("GitHub repository redirect verification failed")
        redirect_payload = json.loads(redirect.stdout)
        canonical_repo = (
            str(redirect_payload.get("nameWithOwner") or "").strip().lower()
            if isinstance(redirect_payload, dict)
            else ""
        )
        if canonical_repo != str(provider_reference["repo"]).lower():
            raise ValueError("GitHub PR lifecycle provider identity mismatch")
        payload[_VERIFIED_REQUESTED_REPOSITORY_FIELD] = canonical_repo
    if _upper_label(payload.get("reviewDecision")) == "CHANGES_REQUESTED":
        payload.update(
            _fetch_github_review_response_metadata(
                owner=owner,
                repo_name=repo_name,
                number=number,
                timeout_seconds=timeout_seconds,
            )
        )
    return payload


def validate_github_pr_provider_identity(
    reference: Mapping[str, Any], provider_payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate one exact PR response, including a provider-verified repo rename."""

    expected_repo = str(reference.get("repo") or "").strip().lower()
    expected_number = reference.get("number")
    provider_reference = normalise_github_issue_reference(
        repo=expected_repo,
        issue_ref=f"pull_{expected_number}",
        url=str(provider_payload.get("url") or ""),
    )
    if (
        provider_reference.get("kind") != "pull_request"
        or provider_reference.get("number") != expected_number
    ):
        raise ValueError("PR dependency provider identity mismatch")
    provider_repo = str(provider_reference.get("repo") or "").strip().lower()
    if provider_repo != expected_repo and str(
        provider_payload.get(_VERIFIED_REQUESTED_REPOSITORY_FIELD) or ""
    ).strip().lower() != provider_repo:
        raise ValueError("PR dependency provider identity mismatch")
    return provider_reference


def _fetch_pr_source_events(
    reference: Mapping[str, Any], *, timeout_seconds: int
) -> dict[str, Any]:
    repo = str(reference.get("repo") or "")
    number = reference.get("number")
    if "/" not in repo or not isinstance(number, int):
        return {"status": "unavailable", "events": [], "body_captured": False}
    owner, repo_name = repo.split("/", 1)
    try:
        payload = fetch_github_pull_request_event_metadata(
            owner=owner,
            repo=repo_name,
            number=number,
            timeout_seconds=timeout_seconds,
        )
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        return {"status": "unavailable", "events": [], "body_captured": False}
    return normalise_issue_fix_pr_source_events(payload)


def build_issue_fix_pr_lifecycle_monitor_packet(
    *,
    repo: str = "public_repo_fixture",
    pr_ref: str = "pull_123_public_metadata_fixture",
    issue_ref: str | None = None,
    url: str | None = None,
    provider_payload: Mapping[str, Any] | None = None,
    fetch_metadata: bool = False,
    fetch_timeout_seconds: int = 10,
    maintainer_correction_input: Mapping[str, Any] | None = None,
    prior_maintainer_corrections: Sequence[Mapping[str, Any]] | None = None,
    generated_at: str | None = "2026-06-23T00:00:00Z",
) -> dict[str, Any]:
    reference = normalise_github_issue_reference(repo=repo, issue_ref=pr_ref, url=url)
    if not url:
        reference = {**reference, "kind": "pull_request"}
    if reference.get("kind") != "pull_request":
        raise ValueError("PR lifecycle projection requires a GitHub /pull/<number> URL")
    payload = dict(provider_payload or {})
    if fetch_metadata:
        if provider_payload:
            raise ValueError("--fetch-metadata cannot be combined with --metadata-json")
        payload = fetch_github_pr_lifecycle_payload(
            reference,
            timeout_seconds=fetch_timeout_seconds,
        )
    canonical_reference = reference
    provider_url = payload.get("url")
    if isinstance(provider_url, str) and provider_url.strip():
        provider_reference = normalise_github_issue_reference(
            repo=repo,
            issue_ref=pr_ref,
            url=provider_url,
        )
        if (
            provider_reference.get("kind") == "pull_request"
            and provider_reference.get("number") == reference.get("number")
        ):
            canonical_reference = provider_reference
    if not issue_ref:
        raw_linked_issues = payload.get("closingIssuesReferences") or payload.get(
            "closing_issues_references"
        )
        if isinstance(raw_linked_issues, Sequence) and not isinstance(
            raw_linked_issues, (str, bytes)
        ):
            for item in raw_linked_issues:
                number = item.get("number") if isinstance(item, Mapping) else None
                if isinstance(number, int):
                    issue_ref = f"issues_{number}"
                    break
    observation = _build_observation(
        repo=str(canonical_reference["repo"]),
        pr_ref=str(canonical_reference["issue_ref"]),
        issue_ref=issue_ref,
        reference=canonical_reference,
        provider_payload=payload,
    )
    requested_repo = str(reference["repo"])
    canonical_repo = str(canonical_reference["repo"])
    if canonical_repo != requested_repo:
        observation["requested_repo"] = requested_repo
        observation["repository_aliases"] = [requested_repo]
    transition = _decide_transition(observation)
    maintainer_correction = (
        normalise_issue_fix_maintainer_correction_input(maintainer_correction_input)
        if maintainer_correction_input is not None
        else None
    )
    maintainer_correction_admission: dict[str, Any] | None = None
    source_events: dict[str, Any] | None = None
    pending_source_event_recheck = any(
        entry.get("disposition") == "accepted"
        and not entry.get("todo_id")
        and isinstance(entry.get("source_event"), Mapping)
        for entry in issue_fix_maintainer_correction_entries(
            {"maintainer_corrections": prior_maintainer_corrections}
        )
    )
    if pending_source_event_recheck or (
        maintainer_correction is not None
        and isinstance(maintainer_correction.get("source_event"), Mapping)
    ):
        if fetch_metadata:
            source_events = _fetch_pr_source_events(
                reference, timeout_seconds=fetch_timeout_seconds
            )
        else:
            source_events = normalise_issue_fix_pr_source_events(payload)
    if maintainer_correction is not None:
        maintainer_correction_admission = (
            evaluate_issue_fix_maintainer_correction_admission(
                maintainer_correction,
                observation=observation,
                source_events=source_events,
                prior_entries=prior_maintainer_corrections,
                prior_ledger_read=prior_maintainer_corrections is not None,
                recorded_at=generated_at,
            )
        )
        if (
            maintainer_correction_admission["disposition"] == "accepted"
            and observation["state"] not in TERMINAL_PR_STATES
        ):
            transition = _maintainer_correction_transition(
                maintainer_correction,
                pr_permalink=str(
                    observation.get("permalink")
                    or reference.get("permalink")
                    or observation["pr_ref"]
                ),
            )
            if (
                transition["decision"] in {"runnable_successor", "user_gate"}
                and not maintainer_correction_admission["replay"]
            ):
                # The entry carries its own Todo intent so a later run can
                # finish an interrupted write without the original input.
                _attach_correction_todo_intent(
                    maintainer_correction_admission, transition
                )
    packet: dict[str, Any] = {
        "ok": True,
        "schema_version": ISSUE_FIX_PR_LIFECYCLE_MONITOR_SCHEMA_VERSION,
        "mode": "issue-fix-pr-lifecycle",
        "generated_at": generated_at,
        "observation": observation,
        "observation_fingerprint": _observation_fingerprint(observation),
        "maintainer_correction": maintainer_correction,
        "maintainer_correction_fingerprint": (
            maintainer_correction.get("correction_fingerprint")
            if maintainer_correction is not None
            else None
        ),
        "transition": transition,
        "grouped_monitor_projection": _grouped_monitor_projection(
            observation, transition
        ),
        "first_screen": {
            "waiting_on": transition["role"],
            "user_action_required": transition["role"] == "user",
            "agent_can_continue": transition["decision"] != "user_gate",
            "current_pr_actionable": transition["decision"] == "runnable_successor",
            "immediate_repoll_required": False,
            "next_safe_action": transition["reason"],
        },
        "writeback_contract": {
            "schema_version": "issue_fix_pr_lifecycle_writeback_contract_v0",
            "allowed_decisions": [
                "runnable_successor",
                "monitor_continuation",
                "user_gate",
                "no_followup",
            ],
            "monitor_quiet_skip_allowed": transition["material_change"] is False,
            "successor_or_terminal_required": transition["material_change"] is True,
            "external_comment_performed": False,
            "external_pr_created": False,
            "merge_performed": False,
            "todo_write_performed": False,
        },
        "domain_state_projection": {
            "schema_version": "issue_fix_pr_lifecycle_domain_state_projection_v0",
            "domain_pack": "issue_fix",
            "default_filename": "pr-lifecycle.jsonl",
            "row_key": {
                "repo": observation["repo"],
                "pr_ref": observation["pr_ref"],
            },
            "write_performed": False,
            "path_recorded": False,
        },
        "external_reads_performed": bool(fetch_metadata),
        "external_writes_performed": False,
        "issue_body_captured": False,
        "comment_bodies_captured": False,
        "maintainer_correction_body_captured": False,
        "response_payloads_captured": False,
        "raw_check_logs_captured": False,
        "local_paths_captured": False,
        "private_repo_state_read": False,
        "todo_write_performed": False,
        "destructive_git_used": False,
    }
    first_push_ci = _first_push_ci_evidence(observation, observed_at=generated_at)
    if first_push_ci is not None:
        packet["first_push_ci"] = first_push_ci
    if maintainer_correction_admission is not None:
        entries = maintainer_correction_admission.pop("entries")
        packet["maintainer_correction_admission"] = maintainer_correction_admission
        packet["maintainer_corrections"] = entries
    if source_events is not None:
        packet["source_events_observed"] = {
            "status": source_events.get("status"),
            "event_count": len(source_events.get("events") or []),
            "body_captured": False,
        }
        packet["_maintainer_correction_source_events"] = source_events
    validation = validate_issue_fix_pr_lifecycle_monitor_packet(packet)
    packet["ok"] = bool(validation["ok"])
    packet["validation"] = validation
    return packet


def _require_false_flags(
    mapping: Mapping[str, Any], prefix: str, keys: Sequence[str], errors: list[str]
) -> None:
    for key in keys:
        if mapping.get(key) is not False:
            errors.append(f"{prefix} {key} must be false")


def validate_issue_fix_pr_lifecycle_monitor_packet(
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if packet.get("schema_version") != ISSUE_FIX_PR_LIFECYCLE_MONITOR_SCHEMA_VERSION:
        errors.append("packet schema_version must be issue_fix_pr_lifecycle_monitor_v0")
    _require_false_flags(
        packet,
        "packet",
        (
            "external_writes_performed", "issue_body_captured", "comment_bodies_captured",
            "maintainer_correction_body_captured", "response_payloads_captured",
            "raw_check_logs_captured", "local_paths_captured", "private_repo_state_read",
            "destructive_git_used",
        ),
        errors,
    )
    observation = packet.get("observation")
    if not isinstance(observation, Mapping):
        errors.append("observation is required")
        observation = {}
    if observation.get("kind") != "pull_request":
        errors.append("observation kind must be pull_request")
    _require_false_flags(
        observation,
        "observation",
        ("body_captured", "comment_bodies_captured", "timeline_captured", "log_output_captured", "response_payload_captured"),
        errors,
    )
    checks = observation.get("checks")
    if isinstance(checks, Mapping):
        _require_false_flags(checks, "checks", ("raw_status_captured", "log_output_captured"), errors)
    else:
        errors.append("observation checks are required")
    review_response = observation.get("review_response")
    if isinstance(review_response, Mapping):
        _require_false_flags(review_response, "review_response", ("raw_reviews_captured", "raw_thread_comments_captured"), errors)
    else:
        errors.append("observation review_response is required")
    transition = packet.get("transition")
    if not isinstance(transition, Mapping):
        errors.append("transition is required")
        transition = {}
    decision = transition.get("decision")
    if decision not in {"runnable_successor", "monitor_continuation", "user_gate", "no_followup"}:
        errors.append("transition decision is invalid")
    if transition.get("would_write") is not False:
        errors.append("transition would_write must be false")
    if transition.get("requires_execute_flag") is not True:
        errors.append("transition must require execute flag")
    state = observation.get("state")
    if state in TERMINAL_PR_STATES and decision != "no_followup":
        errors.append("terminal PR state must choose no_followup")
    if state in TERMINAL_PR_STATES and transition.get("terminal_state_precedence") is not True:
        errors.append("terminal PR state must record terminal_state_precedence")
    if transition.get("material_change") is True and decision == "monitor_continuation":
        errors.append("material PR change must not choose monitor_continuation")
    correction = packet.get("maintainer_correction")
    if correction is not None:
        if not isinstance(correction, Mapping):
            errors.append("maintainer_correction must be an object")
        else:
            _require_false_flags(
                correction,
                "maintainer_correction",
                ("raw_body_captured", "raw_comment_captured", "raw_provider_payload_captured", "local_paths_captured"),
                errors,
            )
            if packet.get("maintainer_correction_fingerprint") != correction.get(
                "correction_fingerprint"
            ):
                errors.append(
                    "maintainer correction fingerprint must match normalized input"
                )
            if state in TERMINAL_PR_STATES and decision != "no_followup":
                errors.append("terminal PR state must supersede maintainer correction")
    admission = packet.get("maintainer_correction_admission")
    if correction is not None and admission is None:
        errors.append("maintainer_correction requires maintainer_correction_admission")
    if admission is not None:
        if not isinstance(admission, Mapping):
            errors.append("maintainer_correction_admission must be an object")
        else:
            if admission.get("body_captured") is not False:
                errors.append("maintainer_correction_admission body_captured must be false")
            if admission.get("admission_basis") not in MAINTAINER_CORRECTION_ADMISSION_BASES:
                errors.append("maintainer_correction_admission admission_basis is invalid")
            if admission.get("disposition") not in MAINTAINER_CORRECTION_DISPOSITIONS:
                errors.append("maintainer_correction_admission disposition is invalid")
            if admission.get("disposition") == "context_only" and (
                admission.get("reason") not in MAINTAINER_CORRECTION_REJECTION_REASONS
            ):
                errors.append("context_only maintainer correction must record a reason")
            if not str(admission.get("operation_id") or "").startswith(
                MAINTAINER_CORRECTION_OPERATION_ID_PREFIX
            ):
                errors.append("maintainer_correction_admission operation_id is invalid")
    entries = packet.get("maintainer_corrections")
    if entries is not None:
        if not isinstance(entries, list):
            errors.append("maintainer_corrections must be a list")
        else:
            for entry in entries:
                if not isinstance(entry, Mapping):
                    errors.append("maintainer_corrections entries must be objects")
                    continue
                if entry.get("body_captured") is not False:
                    errors.append("maintainer_corrections entry body_captured must be false")
                if entry.get("disposition") not in MAINTAINER_CORRECTION_DISPOSITIONS:
                    errors.append("maintainer_corrections entry disposition is invalid")
                event = entry.get("source_event")
                if event is not None and (
                    not isinstance(event, Mapping)
                    or event.get("body_captured") is not False
                    or any(str(key).lower() in {"body", "body_text", "body_html"} for key in event)
                ):
                    errors.append("maintainer_corrections source_event must stay body-free")
    contract = packet.get("writeback_contract")
    if not isinstance(contract, Mapping):
        errors.append("writeback_contract is required")
    elif contract.get("todo_write_performed") is not packet.get("todo_write_performed"):
        errors.append("writeback_contract todo_write_performed must match packet")
    grouped_monitor = packet.get("grouped_monitor_projection")
    if not isinstance(grouped_monitor, Mapping):
        errors.append("grouped_monitor_projection is required")
    else:
        if (
            grouped_monitor.get("schema_version")
            != ISSUE_FIX_PR_GROUPED_MONITOR_PROJECTION_SCHEMA_VERSION
        ):
            errors.append(
                "grouped_monitor_projection schema_version must be issue_fix_pr_grouped_monitor_projection_v1"
            )
        if grouped_monitor.get("creates_per_pr_continuous_monitor_todo") is not False:
            errors.append("grouped monitor projection must not create per-PR monitors")
        if grouped_monitor.get("per_pr_material_action") != "one_shot_advancement_todo":
            errors.append("grouped monitor projection must use one-shot per-PR actions")
        if grouped_monitor.get("external_notification_granularity") != "one_pr_per_message":
            errors.append("grouped monitor projection must preserve one-PR messages")
        if state in TERMINAL_PR_STATES:
            if grouped_monitor.get("member_operation") != "remove":
                errors.append("terminal PR must be removed from grouped monitor membership")
            if grouped_monitor.get("target_key") is not None:
                errors.append("terminal PR must not retain a grouped monitor target")
            if grouped_monitor.get("materialize_nonempty_bucket_monitor") is not False:
                errors.append("terminal PR must not materialize a grouped monitor")
        else:
            target_key = str(grouped_monitor.get("target_key") or "")
            if not target_key.startswith("github-pr-state-"):
                errors.append("open PR grouped monitor target_key is invalid")
            if grouped_monitor.get("member_operation") != "upsert":
                errors.append("open PR must upsert grouped monitor membership")
            if grouped_monitor.get("materialize_nonempty_bucket_monitor") is not True:
                errors.append("open PR must materialize its nonempty grouped monitor")
    todo_write_performed = packet.get("todo_write_performed")
    if not isinstance(todo_write_performed, bool):
        errors.append("todo_write_performed must be boolean")
    todo_write = packet.get("todo_write")
    if todo_write_performed is True:
        if (
            not isinstance(todo_write, Mapping)
            or todo_write.get("write_performed") is not True
        ):
            errors.append("performed todo write requires a compact todo_write receipt")
        elif todo_write.get("path_recorded") is not False:
            errors.append("todo_write path_recorded must be false")
    domain_state = packet.get("domain_state_projection")
    if not isinstance(domain_state, Mapping):
        errors.append("domain_state_projection is required")
    else:
        if domain_state.get("domain_pack") != "issue_fix":
            errors.append("domain_state_projection domain_pack must be issue_fix")
        if domain_state.get("path_recorded") is not False:
            errors.append("domain_state_projection path_recorded must be false")
    return {
        "ok": not errors,
        "schema_version": "issue_fix_pr_lifecycle_monitor_validation_v0",
        "errors": errors,
        "decision": decision,
        "terminal_state_precedence": transition.get("terminal_state_precedence"),
    }


def _kv_bullets(mapping: Mapping[str, Any], keys: Sequence[str]) -> list[str]:
    return [f"- {key}: `{mapping.get(key)}`" for key in keys]


def _append_section(lines: list[str], title: str, rows: Sequence[str]) -> None:
    lines.extend(["", f"## {title}", "", *rows])


def render_issue_fix_pr_lifecycle_monitor_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Issue Fix PR Lifecycle",
        "",
        *_kv_bullets(
            payload,
            (
                "ok", "schema_version", "external_reads_performed",
                "external_writes_performed", "todo_write_performed", "observation_fingerprint",
            ),
        ),
    ]
    observation = payload.get("observation")
    if isinstance(observation, Mapping):
        raw_checks = observation.get("checks")
        checks: Mapping[str, Any] = raw_checks if isinstance(raw_checks, Mapping) else {}
        _append_section(
            lines,
            "Observation",
            [*_kv_bullets(observation, ("repo", "pr_ref", "state", "review_decision", "merge_state_status")), f"- checks: `{checks.get('aggregate')}`"],
        )
    transition = payload.get("transition")
    if isinstance(transition, Mapping):
        _append_section(
            lines,
            "Transition",
            [*_kv_bullets(transition, ("decision", "action_kind", "material_change", "terminal_state_precedence")), f"- reason: {transition.get('reason')}"],
        )
    for key, title, fields in (
        ("grouped_monitor_projection", "Grouped Monitor Projection", ("state_bucket", "target_key", "member_operation", "creates_per_pr_continuous_monitor_todo", "per_pr_material_action")),
        ("maintainer_correction", "Maintainer Correction", ("correction_kind", "source_kind", "source_ref", "correction_fingerprint")),
        ("maintainer_correction_admission", "Maintainer Correction Admission", ("admission_basis", "disposition", "reason", "operation_id")),
        ("domain_state_projection", "Domain State Projection", ("domain_pack", "default_filename", "write_performed", "path_recorded")),
    ):
        section = payload.get(key)
        if isinstance(section, Mapping):
            _append_section(lines, title, _kv_bullets(section, fields))
    validation = payload.get("validation")
    if isinstance(validation, Mapping):
        raw_errors = validation.get("errors")
        errors = raw_errors if isinstance(raw_errors, list) else []
        _append_section(lines, "Validation", (f"- validation_ok: `{validation.get('ok')}`", f"- error_count: `{len(errors)}`"))
    if payload.get("error"):
        _append_section(lines, "Error", (str(payload.get("error")),))
    return "\n".join(lines) + "\n"
