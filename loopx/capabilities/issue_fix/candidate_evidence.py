from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from typing import Any

from .candidate_preflight import ISSUE_FIX_CANDIDATE_PREFLIGHT_INPUT_SCHEMA_VERSION
from .github_public import MAINTAINER_ASSOCIATIONS
from .metadata_preview import normalise_github_issue_reference

_CLOSING_PULL_REQUESTS_QUERY = """
query($owner:String!,$name:String!,$number:Int!,$endCursor:String) {
  repository(owner:$owner,name:$name) {
    issue(number:$number) {
      state
      closedByPullRequestsReferences(
        first:100,
        after:$endCursor,
        includeClosedPrs:true
      ) {
        nodes { number state url headRefOid }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

_CROSS_REFERENCED_PULL_REQUESTS_QUERY = """
query($owner:String!,$name:String!,$number:Int!,$endCursor:String) {
  repository(owner:$owner,name:$name) {
    issue(number:$number) {
      state
      timelineItems(
        first:100,
        after:$endCursor,
        itemTypes:[CROSS_REFERENCED_EVENT]
      ) {
        nodes {
          ... on CrossReferencedEvent {
            source {
              __typename
              ... on PullRequest { number state url headRefOid }
            }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

_MAINTAINER_COMMENTS_QUERY = """
query($owner:String!,$name:String!,$number:Int!,$endCursor:String) {
  repository(owner:$owner,name:$name) {
    issue(number:$number) {
      state
      comments(first:100,after:$endCursor) {
        nodes { authorAssociation url updatedAt }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

_QUERY_SPECS = (
    (
        "closing",
        _CLOSING_PULL_REQUESTS_QUERY,
        "closedByPullRequestsReferences",
        "candidate closing pull request read",
    ),
    (
        "cross_refs",
        _CROSS_REFERENCED_PULL_REQUESTS_QUERY,
        "timelineItems",
        "candidate cross-reference read",
    ),
    (
        "comments",
        _MAINTAINER_COMMENTS_QUERY,
        "comments",
        "candidate maintainer disposition read",
    ),
)


def _query_fingerprint(query: str) -> str:
    canonical = " ".join(query.split())
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def _run_graphql_pages(
    *,
    query: str,
    owner: str,
    name: str,
    number: int,
    timeout_seconds: float,
    operation: str,
) -> list[dict[str, Any]]:
    gh = shutil.which("gh")
    if not gh:
        raise ValueError("public GitHub candidate evidence requires GitHub CLI `gh`")
    try:
        completed = subprocess.run(
            [
                gh,
                "api",
                "graphql",
                "--paginate",
                "--slurp",
                "-f",
                f"query={query}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"number={number}",
            ],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError(f"public GitHub {operation} failed") from None
    if completed.returncode != 0:
        raise ValueError(f"public GitHub {operation} failed")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise ValueError(f"public GitHub {operation} returned invalid JSON") from None
    pages = payload if isinstance(payload, list) else [payload]
    if not pages or any(not isinstance(page, Mapping) for page in pages):
        raise ValueError(f"public GitHub {operation} returned invalid pages")
    return [dict(page) for page in pages]


def _connection_projection(
    pages: Sequence[Mapping[str, Any]],
    *,
    connection: str,
    operation: str,
) -> tuple[str, list[dict[str, Any]]]:
    issue_state: str | None = None
    rows: list[dict[str, Any]] = []
    final_page_info: Mapping[str, Any] | None = None
    for page in pages:
        issue = ((page.get("data") or {}).get("repository") or {}).get("issue")
        if issue is None:
            raise ValueError(f"public GitHub {operation} did not find the issue")
        if not isinstance(issue, Mapping):
            raise TypeError(f"public GitHub {operation} returned invalid issue")
        state = str(issue.get("state") or "").strip().upper()
        if state not in {"OPEN", "CLOSED"}:
            raise ValueError(f"public GitHub {operation} returned invalid issue state")
        if issue_state not in {None, state}:
            raise ValueError(f"public GitHub {operation} changed issue state mid-read")
        issue_state = state
        projection = issue.get(connection)
        if not isinstance(projection, Mapping):
            raise TypeError(f"public GitHub {operation} omitted {connection}")
        nodes = projection.get("nodes")
        if not isinstance(nodes, list):
            raise TypeError(f"public GitHub {operation} returned invalid nodes")
        if any(not isinstance(node, Mapping) for node in nodes):
            raise TypeError(f"public GitHub {operation} returned an unparseable node")
        rows.extend(dict(node) for node in nodes)
        page_info = projection.get("pageInfo")
        if not isinstance(page_info, Mapping):
            raise TypeError(f"public GitHub {operation} omitted pageInfo")
        final_page_info = page_info
    if final_page_info is None or final_page_info.get("hasNextPage") is not False:
        raise ValueError(f"public GitHub {operation} was truncated")
    return issue_state or "CLOSED", rows


def _source_projection(
    *,
    query: str,
    observed_at: str,
    page_count: int,
    result_count: int,
) -> dict[str, Any]:
    return {
        "provider": "github_graphql",
        "observed_at": observed_at,
        "query_fingerprint": _query_fingerprint(query),
        "page_count": page_count,
        "result_count": result_count,
        "complete": True,
        "truncated": False,
        "raw_provider_payload_captured": False,
    }


def _validated_source_receipt(
    raw: object,
    *,
    field: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"{field} source receipt must be an object")
    if raw.get("complete") is not True or raw.get("truncated") is not False:
        raise ValueError(f"{field} source receipt must be complete and non-truncated")
    if raw.get("raw_provider_payload_captured") is not False:
        raise ValueError(f"{field} source receipt must not capture raw payloads")
    for required in (
        "provider",
        "observed_at",
        "query_fingerprint",
        "page_count",
        "result_count",
    ):
        if required not in raw:
            raise ValueError(f"{field} source receipt requires {required}")
    return dict(raw)


def _required_mapping(raw: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"{field} must be an object")
    return raw


def _required_pr_projection(
    raw: Mapping[str, Any],
    *,
    field: str,
) -> tuple[int, str, str, str]:
    pr_number = raw.get("number")
    if not isinstance(pr_number, int) or pr_number <= 0:
        raise ValueError(f"{field}.number must be a positive integer")
    state = str(raw.get("state") or "").strip().upper()
    if state not in {"OPEN", "CLOSED", "MERGED"}:
        raise ValueError(f"{field}.state must be OPEN, CLOSED, or MERGED")
    url = str(raw.get("url") or "").strip()
    if not url:
        raise ValueError(f"{field}.url is required")
    revision = str(raw.get("headRefOid") or "").strip()
    if not revision:
        raise ValueError(f"{field}.headRefOid is required")
    return pr_number, state, url, revision


def build_public_github_candidate_preflight_input(
    *,
    repo: str,
    issue_ref: str,
    issue_state: str,
    closing_pull_requests: Sequence[Mapping[str, Any]],
    cross_referenced_pull_requests: Sequence[Mapping[str, Any]],
    maintainer_comments: Sequence[Mapping[str, Any]],
    generated_at: str,
    source_receipts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    reference = normalise_github_issue_reference(
        repo=repo,
        issue_ref=issue_ref,
        url=None,
    )
    canonical_repo = str(reference["repo"])
    canonical_issue = str(reference["issue_ref"])
    number = reference.get("number")
    if not isinstance(number, int):
        raise TypeError("candidate evidence requires a numeric GitHub issue")
    state = str(issue_state or "").strip().upper()
    if state not in {"OPEN", "CLOSED"}:
        raise ValueError("candidate evidence issue_state must be OPEN or CLOSED")

    numeric_by_number: dict[int, dict[str, Any]] = {}
    for index, candidate in enumerate(closing_pull_requests):
        raw = _required_mapping(
            candidate,
            field=f"closing_pull_requests[{index}]",
        )
        pr_number, pr_state, url, revision = _required_pr_projection(
            raw,
            field=f"closing_pull_requests[{index}]",
        )
        numeric_by_number[pr_number] = {
            "repo": canonical_repo,
            "pr_ref": f"#{pr_number}",
            "state": pr_state,
            "url": url,
            "closing_issue_refs": [canonical_issue],
            "revision": revision,
        }

    semantic_by_number: dict[int, dict[str, Any]] = {}
    for index, candidate in enumerate(cross_referenced_pull_requests):
        raw = _required_mapping(
            candidate,
            field=f"cross_referenced_pull_requests[{index}]",
        )
        source = _required_mapping(
            raw.get("source"),
            field=f"cross_referenced_pull_requests[{index}].source",
        )
        source_type = str(source.get("__typename") or "").strip()
        if not source_type:
            raise ValueError(
                f"cross_referenced_pull_requests[{index}].source.__typename is required"
            )
        if source_type != "PullRequest":
            continue
        pr_number, pr_state, url, revision = _required_pr_projection(
            source,
            field=f"cross_referenced_pull_requests[{index}].source",
        )
        if pr_number in numeric_by_number:
            continue
        semantic_by_number[pr_number] = {
            "repo": canonical_repo,
            "pr_ref": f"#{pr_number}",
            "state": pr_state,
            "url": url,
            "related_issue_refs": [canonical_issue],
            "relation": "fix_candidate",
            # A cross-reference and a current head OID prove that the candidate
            # exists, not that its current revision implements this issue.
            "current_revision_verified": False,
            "revision": revision,
        }

    maintainer_comments_by_ref: dict[str, dict[str, str]] = {}
    for index, candidate in enumerate(maintainer_comments):
        comment = _required_mapping(
            candidate,
            field=f"maintainer_comments[{index}]",
        )
        association = str(comment.get("authorAssociation") or "").strip().upper()
        if not association:
            raise ValueError(
                f"maintainer_comments[{index}].authorAssociation is required"
            )
        url = str(comment.get("url") or "").strip()
        if not url:
            raise ValueError(f"maintainer_comments[{index}].url is required")
        if association in MAINTAINER_ASSOCIATIONS:
            revision = str(comment.get("updatedAt") or "").strip()
            if not revision:
                raise ValueError(
                    f"maintainer_comments[{index}].updatedAt is required"
                )
            if url in maintainer_comments_by_ref:
                raise ValueError(
                    f"maintainer_comments contains duplicate comment {url}"
                )
            maintainer_comments_by_ref[url] = {
                "ref": url,
                "revision": revision,
            }
    maintainer_comment_rows = [
        maintainer_comments_by_ref[ref] for ref in sorted(maintainer_comments_by_ref)
    ]
    numeric_source = _validated_source_receipt(
        source_receipts.get("numeric_pr_evidence"),
        field="numeric_pr_evidence",
    )
    semantic_source = _validated_source_receipt(
        source_receipts.get("semantic_pr_evidence"),
        field="semantic_pr_evidence",
    )
    comment_source = _validated_source_receipt(
        source_receipts.get("maintainer_comment_evidence"),
        field="maintainer_comment_evidence",
    )
    return {
        "schema_version": ISSUE_FIX_CANDIDATE_PREFLIGHT_INPUT_SCHEMA_VERSION,
        "domain_state": {
            "repo": canonical_repo,
            "issue_ref": canonical_issue,
            "status": state.lower(),
            "terminal": state == "CLOSED",
            "route": None,
            "maintainer_comment_refs": [
                row["ref"] for row in maintainer_comment_rows
            ],
        },
        "numeric_pr_evidence": {
            "repo": canonical_repo,
            "issue_ref": canonical_issue,
            "query_scope": "issue_specific_all_states",
            "complete": True,
            "truncated": False,
            "source": numeric_source,
            "rows": list(numeric_by_number.values()),
        },
        "semantic_pr_evidence": {
            "repo": canonical_repo,
            "issue_ref": canonical_issue,
            "query_scope": "issue_specific_current_revision",
            "complete": True,
            "truncated": False,
            "source": semantic_source,
            "rows": list(semantic_by_number.values()),
        },
        "maintainer_comment_evidence": {
            "repo": canonical_repo,
            "issue_ref": canonical_issue,
            "query_scope": "issue_specific_comment_metadata",
            "complete": True,
            "truncated": False,
            "source": comment_source,
            "rows": maintainer_comment_rows,
        },
    }


def collect_public_github_candidate_preflight_input(
    *,
    repo: str,
    issue_ref: str,
    generated_at: str,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    if timeout_seconds < 1 or timeout_seconds > 120:
        raise ValueError("timeout_seconds must be between 1 and 120")
    deadline = time.monotonic() + timeout_seconds

    def remaining_timeout() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("public GitHub candidate evidence timed out")
        return remaining

    reference = normalise_github_issue_reference(
        repo=repo,
        issue_ref=issue_ref,
        url=None,
    )
    canonical_repo = str(reference["repo"])
    number = reference.get("number")
    if not isinstance(number, int):
        raise TypeError("candidate evidence requires a numeric GitHub issue")
    if "/" not in canonical_repo:
        raise ValueError("candidate evidence repo must use owner/name")
    owner, name = canonical_repo.split("/", 1)

    observations: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    states: set[str] = set()
    for key, query, connection, operation in _QUERY_SPECS:
        pages = _run_graphql_pages(
            query=query,
            owner=owner,
            name=name,
            number=number,
            timeout_seconds=remaining_timeout(),
            operation=operation,
        )
        state, rows = _connection_projection(
            pages,
            connection=connection,
            operation=operation,
        )
        observations[key] = (pages, rows)
        states.add(state)
    if len(states) != 1:
        raise ValueError("public GitHub candidate evidence returned inconsistent state")

    closing_pages, closing = observations["closing"]
    cross_ref_pages, cross_refs = observations["cross_refs"]
    comment_pages, comments = observations["comments"]
    source_receipts = {
        "numeric_pr_evidence": _source_projection(
            query=_CLOSING_PULL_REQUESTS_QUERY,
            observed_at=generated_at,
            page_count=len(closing_pages),
            result_count=len(closing),
        ),
        "semantic_pr_evidence": _source_projection(
            query=_CROSS_REFERENCED_PULL_REQUESTS_QUERY,
            observed_at=generated_at,
            page_count=len(cross_ref_pages),
            result_count=len(cross_refs),
        ),
        "maintainer_comment_evidence": _source_projection(
            query=_MAINTAINER_COMMENTS_QUERY,
            observed_at=generated_at,
            page_count=len(comment_pages),
            result_count=len(comments),
        ),
    }
    return build_public_github_candidate_preflight_input(
        repo=canonical_repo,
        issue_ref=str(reference["issue_ref"]),
        issue_state=states.pop(),
        closing_pull_requests=closing,
        cross_referenced_pull_requests=cross_refs,
        maintainer_comments=comments,
        generated_at=generated_at,
        source_receipts=source_receipts,
    )
