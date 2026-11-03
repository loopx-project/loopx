from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from loopx.capabilities.issue_fix.pr_lifecycle import (
    build_issue_fix_pr_lifecycle_monitor_packet,
    fetch_github_pr_lifecycle_payload,
)


def _changes_requested_payload() -> dict[str, object]:
    return {
        "state": "OPEN",
        "url": "https://github.com/example/project/pull/3258",
        "reviewDecision": "CHANGES_REQUESTED",
        "mergeStateStatus": "BLOCKED",
        "headRefOid": "b" * 40,
        "statusCheckRollup": [{"name": "lint", "conclusion": "SUCCESS"}],
        "reviewResponseMetadataStatus": "available",
        "reviewThreadSummary": {
            "totalCount": 4,
            "resolvedCount": 4,
            "unresolvedCount": 0,
            "complete": True,
        },
        "latestChangesRequestedAt": "2026-07-15T12:38:20Z",
        "headCommittedAt": "2026-07-15T17:19:55Z",
    }


def test_addressed_review_changes_wait_for_rereview() -> None:
    packet = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/example/project/pull/3258",
        provider_payload=_changes_requested_payload(),
    )

    assert packet["ok"] is True
    assert packet["observation"]["review_response"]["status"] == (
        "changes_addressed_awaiting_rereview"
    )
    assert packet["transition"]["decision"] == "monitor_continuation"
    assert packet["transition"]["action_kind"] == (
        "issue_fix_review_changes_addressed_monitor"
    )
    assert packet["transition"]["material_change"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reviewThreadSummary", {"totalCount": 4, "resolvedCount": 3, "unresolvedCount": 1, "complete": True}),
        ("reviewThreadSummary", {"totalCount": 101, "resolvedCount": 100, "unresolvedCount": None, "complete": False}),
        ("headCommittedAt", "2026-07-15T12:00:00Z"),
        ("latestChangesRequestedAt", None),
    ],
)
def test_review_response_evidence_fails_closed(field: str, value: object) -> None:
    payload = _changes_requested_payload()
    payload[field] = value

    packet = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/example/project/pull/3258",
        provider_payload=payload,
    )

    assert packet["observation"]["review_response"]["status"] == (
        "changes_requested_unverified"
    )
    assert packet["transition"]["decision"] == "runnable_successor"
    assert packet["transition"]["action_kind"] == "issue_fix_review_changes_replan"


def test_fetch_metadata_reads_compact_review_response_without_bodies() -> None:
    lifecycle = {
        "state": "OPEN",
        "reviewDecision": "CHANGES_REQUESTED",
        "mergeStateStatus": "BLOCKED",
        "headRefOid": "b" * 40,
        "statusCheckRollup": [],
        "url": "https://github.com/example/project/pull/3258",
    }
    graphql = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "totalCount": 2,
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [{"isResolved": True}, {"isResolved": True}],
                    },
                    "reviews": {
                        "nodes": [
                            {
                                "state": "CHANGES_REQUESTED",
                                "submittedAt": "2026-07-15T12:38:20Z",
                            }
                        ]
                    },
                    "commits": {
                        "nodes": [
                            {
                                "commit": {
                                    "committedDate": "2026-07-15T17:19:55Z"
                                }
                            }
                        ]
                    },
                }
            }
        }
    }
    responses = [
        subprocess.CompletedProcess([], 0, json.dumps(lifecycle), ""),
        subprocess.CompletedProcess([], 0, json.dumps(graphql), ""),
    ]

    with patch(
        "loopx.capabilities.issue_fix.pr_lifecycle.subprocess.run",
        side_effect=responses,
    ) as run:
        payload = fetch_github_pr_lifecycle_payload(
            {"repo": "example/project", "number": 3258}, timeout_seconds=5
        )

    assert run.call_count == 2
    assert run.call_args_list[0].args[0][:3] == ["gh", "pr", "view"]
    assert run.call_args_list[1].args[0][:3] == ["gh", "api", "graphql"]
    assert payload["reviewThreadSummary"] == {
        "totalCount": 2,
        "resolvedCount": 2,
        "unresolvedCount": 0,
        "complete": True,
    }
    encoded = json.dumps(payload, sort_keys=True).lower()
    assert "body" not in encoded
    assert "comment" not in encoded


def test_review_response_fetch_timeout_fails_closed() -> None:
    lifecycle = _changes_requested_payload()
    lifecycle.pop("reviewResponseMetadataStatus")
    lifecycle.pop("reviewThreadSummary")
    lifecycle.pop("latestChangesRequestedAt")
    lifecycle.pop("headCommittedAt")
    responses = [
        subprocess.CompletedProcess([], 0, json.dumps(lifecycle), ""),
        subprocess.TimeoutExpired(["gh", "api", "graphql"], 5),
    ]

    with patch(
        "loopx.capabilities.issue_fix.pr_lifecycle.subprocess.run",
        side_effect=responses,
    ):
        payload = fetch_github_pr_lifecycle_payload(
            {"repo": "example/project", "number": 3258}, timeout_seconds=5
        )

    assert payload["reviewResponseMetadataStatus"] == "unavailable"
    packet = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/example/project/pull/3258",
        provider_payload=payload,
    )
    assert packet["transition"]["action_kind"] == "issue_fix_review_changes_replan"


def test_fetch_metadata_verifies_repository_redirect() -> None:
    lifecycle = {
        "state": "MERGED",
        "mergedAt": "2026-10-01T00:00:00Z",
        "url": "https://github.com/new-owner/project/pull/3258",
    }
    redirect = {"nameWithOwner": "new-owner/project"}
    responses = [
        subprocess.CompletedProcess([], 0, json.dumps(lifecycle), ""),
        subprocess.CompletedProcess([], 0, json.dumps(redirect), ""),
    ]

    with patch(
        "loopx.capabilities.issue_fix.pr_lifecycle.subprocess.run",
        side_effect=responses,
    ) as run:
        payload = fetch_github_pr_lifecycle_payload(
            {"repo": "old-owner/project", "number": 3258},
            timeout_seconds=5,
            compact=True,
        )

    assert run.call_count == 2
    assert run.call_args_list[1].args[0] == [
        "gh", "repo", "view", "old-owner/project", "--json", "nameWithOwner",
    ]
    assert payload["_loopx_verified_requested_repository"] == "new-owner/project"
