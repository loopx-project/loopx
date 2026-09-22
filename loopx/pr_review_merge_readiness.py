"""GitHub readback adapter for exact-head pull-request merge readiness."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capabilities.pr_review_queue import build_merge_readiness
from .pr_review import (
    _as_dict,
    _as_list,
    _normalize_pr,
    _now_iso,
    _parse_timestamp,
    _run_gh_json,
)


def fetch_github_pull_request(
    *,
    repo: str,
    number: int,
    cwd: Path | None = None,
    wait_for_ci: bool = True,
) -> dict[str, Any]:
    fields = (
        "number,title,url,state,isDraft,reviewDecision,mergeStateStatus,"
        "headRefName,headRefOid,baseRefName,baseRefOid,author,createdAt,updatedAt,"
        "closedAt,mergedAt,mergeCommit,body,files,changedFiles,additions,"
        "deletions,commits,reviews"
    )
    if wait_for_ci:
        fields += ",statusCheckRollup"
    payload = _run_gh_json(
        ["pr", "view", str(number), "--json", fields, "--repo", repo],
        cwd=cwd,
    )
    if not isinstance(payload, dict) or payload.get("number") != number:
        raise RuntimeError("GitHub returned an invalid pull-request readback")
    return payload


def fetch_github_review_thread_summary(
    *,
    repo: str,
    number: int,
    cwd: Path | None = None,
) -> dict[str, Any]:
    owner, separator, name = repo.partition("/")
    if not separator or not owner or not name:
        raise ValueError("repository must use owner/name form")
    query = """
query($owner:String!,$name:String!,$number:Int!,$cursor:String) {
  repository(owner:$owner,name:$name) {
    pullRequest(number:$number) {
      reviewThreads(first:100,after:$cursor) {
        nodes { isResolved }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()
    cursor: str | None = None
    total_count = 0
    unresolved_count = 0
    try:
        for _ in range(10):
            args = [
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"number={number}",
            ]
            if cursor:
                args.extend(["-F", f"cursor={cursor}"])
            payload = _run_gh_json(args, cwd=cwd)
            threads = _as_dict(
                _as_dict(_as_dict(_as_dict(payload).get("data")).get("repository")).get(
                    "pullRequest"
                )
            ).get("reviewThreads")
            if not isinstance(threads, dict):
                raise RuntimeError("review-thread readback is incomplete")
            nodes = _as_list(threads.get("nodes"))
            if any(not isinstance(item, dict) for item in nodes):
                raise RuntimeError("review-thread readback is malformed")
            total_count += len(nodes)
            unresolved_count += sum(
                1 for item in nodes if item.get("isResolved") is not True
            )
            page_info = _as_dict(threads.get("pageInfo"))
            if page_info.get("hasNextPage") is not True:
                return {
                    "schema_version": "github_review_thread_summary_v0",
                    "complete": True,
                    "total_count": total_count,
                    "unresolved_count": unresolved_count,
                }
            cursor = str(page_info.get("endCursor") or "").strip() or None
            if cursor is None:
                raise RuntimeError("review-thread cursor is missing")
    except Exception:
        return {
            "schema_version": "github_review_thread_summary_v0",
            "complete": False,
            "total_count": total_count,
            "unresolved_count": unresolved_count,
            "failure_code": "github_review_thread_read_failed",
        }
    return {
        "schema_version": "github_review_thread_summary_v0",
        "complete": False,
        "total_count": total_count,
        "unresolved_count": unresolved_count,
        "failure_code": "github_review_thread_pagination_limit",
    }


def build_pr_merge_readiness_packet(
    *,
    pull_request: dict[str, Any],
    repository: str,
    expected_exact_head: str,
    reviewer_login: str | None,
    review_threads: Mapping[str, Any],
    source: str,
    wait_for_ci: bool = True,
) -> dict[str, Any]:
    generated_at_text = _now_iso()
    generated_at = _parse_timestamp(generated_at_text) or datetime.now(timezone.utc)
    item = _normalize_pr(
        pull_request,
        reviewer_login=reviewer_login,
        generated_at=generated_at,
        fresh_audit_exact_heads=set(),
        wait_for_ci=wait_for_ci,
    )
    payload = build_merge_readiness(
        repository=repository,
        expected_exact_head=expected_exact_head,
        item=item,
        review_threads=review_threads,
        source=source,
        wait_for_ci=wait_for_ci,
    )
    payload["generated_at"] = generated_at_text
    return payload
