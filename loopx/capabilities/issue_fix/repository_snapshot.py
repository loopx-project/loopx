from __future__ import annotations

import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from .github_public import _REPO_PATTERN as _REPO_PATTERN
from .metrics_supplement import SUPPLEMENT_SCHEMA_VERSION


SNAPSHOT_SCHEMA_VERSION = "issue_fix_repository_reporting_snapshot_v0"
COLLECTION_SCHEMA_VERSION = "issue_fix_repository_snapshot_collection_v0"
RECORD_SCHEMA_VERSION = "issue_fix_repository_snapshot_record_v0"
ISSUE_CLOSE_ACTIVITY_SCHEMA_VERSION = "issue_fix_issue_close_activity_v0"

_REF_NUMBER_PATTERN = re.compile(r"(?:issues|pull)_(\d+)")
_FAILED_CHECK_CONCLUSIONS = {
    "ACTION_REQUIRED",
    "CANCELLED",
    "FAILURE",
    "TIMED_OUT",
}
_PENDING_CHECK_STATES = {"IN_PROGRESS", "PENDING", "QUEUED", "REQUESTED", "WAITING"}


def _timestamp(value: Any, *, field: str) -> tuple[str, datetime]:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from None
    if parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return text, parsed


def _run_gh_json(arguments: list[str], *, timeout_seconds: int, operation: str) -> Any:
    try:
        completed = subprocess.run(
            ["gh", *arguments],
            check=False,
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError(f"public GitHub {operation} failed") from None
    if completed.returncode != 0:
        raise ValueError(f"public GitHub {operation} failed")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise ValueError(f"public GitHub {operation} returned invalid JSON") from None


def _repo_parts(repo: str) -> tuple[str, str]:
    repo = str(repo or "").strip()
    if not _REPO_PATTERN.fullmatch(repo):
        raise ValueError("repo must use owner/name")
    owner, name = repo.split("/", 1)
    return owner, name


def _reference_numbers(
    rows: list[dict[str, Any]], *, repo: str, ref_field: str
) -> list[int]:
    numbers: set[int] = set()
    for row in rows:
        observation = row.get("observation") if isinstance(row, dict) else None
        if not isinstance(observation, dict) or observation.get("repo") != repo:
            continue
        match = _REF_NUMBER_PATTERN.fullmatch(
            str(observation.get(ref_field) or "").strip()
        )
        if match:
            numbers.add(int(match.group(1)))
    if len(numbers) > 50:
        raise ValueError(f"at most 50 {ref_field} references may be refreshed")
    return sorted(numbers)


def _supplement_issue_reference_numbers(
    metrics_supplement: dict[str, Any] | None,
) -> list[int]:
    if metrics_supplement is None:
        return []
    if metrics_supplement.get("schema_version") != SUPPLEMENT_SCHEMA_VERSION:
        raise ValueError(f"metrics supplement must use {SUPPLEMENT_SCHEMA_VERSION}")
    raw_activity = metrics_supplement.get("issue_close_activity")
    if not isinstance(raw_activity, list):
        raise ValueError("metrics supplement issue_close_activity must be a list")

    numbers: set[int] = set()
    for index, row in enumerate(raw_activity):
        if not isinstance(row, dict):
            raise ValueError(f"issue_close_activity[{index}] must be an object")
        if row.get("schema_version") != ISSUE_CLOSE_ACTIVITY_SCHEMA_VERSION:
            raise ValueError(
                f"issue_close_activity[{index}] must use "
                f"{ISSUE_CLOSE_ACTIVITY_SCHEMA_VERSION}"
            )
        match = re.fullmatch(r"issues_(\d+)", str(row.get("issue_ref") or ""))
        if match is None:
            raise ValueError(
                f"issue_close_activity[{index}].issue_ref must use issues_<number>"
            )
        numbers.add(int(match.group(1)))
    return sorted(numbers)


def _check_state(rollups: Any) -> str:
    if not isinstance(rollups, list) or not rollups:
        return "UNKNOWN"
    for item in rollups:
        if (
            isinstance(item, dict)
            and str(item.get("conclusion") or "").upper() in _FAILED_CHECK_CONCLUSIONS
        ):
            return "FAILING"
    for item in rollups:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").upper()
        if status in _PENDING_CHECK_STATES or item.get("conclusion") is None:
            return "PENDING"
    return "PASSING"


def _public_stock(repo: str, *, timeout_seconds: int) -> dict[str, int]:
    owner, name = _repo_parts(repo)
    query = (
        "query($owner:String!,$name:String!){repository(owner:$owner,name:$name){"
        "issues(states:OPEN){totalCount} pullRequests(states:OPEN){totalCount}}}"
    )
    payload = _run_gh_json(
        [
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
        ],
        timeout_seconds=timeout_seconds,
        operation="repository stock read",
    )
    repository = (payload.get("data") or {}).get("repository") or {}
    try:
        return {
            "open_issues": int(repository["issues"]["totalCount"]),
            "open_pull_requests": int(repository["pullRequests"]["totalCount"]),
        }
    except (KeyError, TypeError, ValueError):
        raise ValueError("public GitHub repository stock is incomplete") from None


def _public_flow(
    repo: str, *, baseline_at: str, baseline_time: datetime, timeout_seconds: int
) -> dict[str, int]:
    query = (
        "query($queryString:String!,$endCursor:String){search("
        "query:$queryString,type:ISSUE,first:100,after:$endCursor){"
        "pageInfo{hasNextPage endCursor} nodes{"
        "... on Issue{__typename createdAt closedAt} "
        "... on PullRequest{__typename createdAt closedAt mergedAt}}}}"
    )
    day = baseline_at[:10]
    pages = _run_gh_json(
        [
            "api",
            "graphql",
            "--paginate",
            "--slurp",
            "-f",
            f"query={query}",
            "-f",
            f"queryString=repo:{repo} updated:>={day}",
        ],
        timeout_seconds=timeout_seconds,
        operation="repository flow read",
    )
    nodes: list[dict[str, Any]] = []
    for page in pages if isinstance(pages, list) else []:
        search = (page.get("data") or {}).get("search") or {}
        nodes.extend(
            item for item in search.get("nodes") or [] if isinstance(item, dict)
        )

    def after(value: Any) -> bool:
        if not value:
            return False
        _, parsed = _timestamp(value, field="github.event_at")
        return parsed >= baseline_time

    return {
        "issues_opened": sum(
            item.get("__typename") == "Issue" and after(item.get("createdAt"))
            for item in nodes
        ),
        "issues_closed": sum(
            item.get("__typename") == "Issue" and after(item.get("closedAt"))
            for item in nodes
        ),
        "pull_requests_opened": sum(
            item.get("__typename") == "PullRequest" and after(item.get("createdAt"))
            for item in nodes
        ),
        "pull_requests_closed": sum(
            item.get("__typename") == "PullRequest" and after(item.get("closedAt"))
            for item in nodes
        ),
        "pull_requests_merged": sum(
            item.get("__typename") == "PullRequest" and after(item.get("mergedAt"))
            for item in nodes
        ),
    }


def _issue_state(repo: str, number: int, *, timeout_seconds: int) -> dict[str, Any]:
    payload = _run_gh_json(
        [
            "issue",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "number,state,url,closedAt,updatedAt",
        ],
        timeout_seconds=timeout_seconds,
        operation=f"issue {number} state read",
    )
    result = {
        "issue_ref": f"issues_{number}",
        "state": str(payload.get("state") or "").upper(),
    }
    if payload.get("closedAt"):
        result["closed_at"] = payload["closedAt"]
    return result


def _pr_state(repo: str, number: int, *, timeout_seconds: int) -> dict[str, Any]:
    payload = _run_gh_json(
        [
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "number,state,url,createdAt,updatedAt,reviewDecision,statusCheckRollup",
        ],
        timeout_seconds=timeout_seconds,
        operation=f"pull request {number} state read",
    )
    review = str(payload.get("reviewDecision") or "UNKNOWN").upper()
    if review not in {"APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        review = "UNKNOWN"
    return {
        "pr_ref": f"pull_{number}",
        "state": str(payload.get("state") or "").upper(),
        "ci": _check_state(payload.get("statusCheckRollup")),
        "review": review,
        "created_at": payload.get("createdAt"),
        "updated_at": payload.get("updatedAt"),
    }


def material_snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    material = {
        "repo": snapshot.get("repo"),
        "open_issues": snapshot.get("open_issues"),
        "open_pull_requests": snapshot.get("open_pull_requests"),
        "flow_since_baseline": snapshot.get("flow_since_baseline"),
        "issue_states": [
            {"issue_ref": item.get("issue_ref"), "state": item.get("state")}
            for item in snapshot.get("issue_states") or []
            if isinstance(item, dict)
        ],
        "pull_request_states": [
            {
                "pr_ref": item.get("pr_ref"),
                "state": item.get("state"),
                "ci": item.get("ci"),
                "review": item.get("review"),
            }
            for item in snapshot.get("pull_request_states") or []
            if isinstance(item, dict)
        ],
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def repository_snapshot_record(snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at, _ = _timestamp(
        snapshot.get("captured_at"), field="snapshot.captured_at"
    )
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "repo": snapshot.get("repo"),
        "snapshot_date": captured_at[:10],
        "captured_at": captured_at,
        "material_fingerprint": material_snapshot_fingerprint(snapshot),
        "snapshot": snapshot,
        "raw_provider_payload_captured": False,
        "credentials_captured": False,
        "local_paths_captured": False,
    }


def collect_public_github_repository_snapshot(
    *,
    repo: str,
    baseline_snapshot: dict[str, Any],
    feasibility_rows: list[dict[str, Any]],
    pr_lifecycle_rows: list[dict[str, Any]],
    metrics_supplement: dict[str, Any] | None = None,
    generated_at: str,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    _repo_parts(repo)
    if timeout_seconds < 1 or timeout_seconds > 120:
        raise ValueError("timeout_seconds must be between 1 and 120")
    if baseline_snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"baseline snapshot must use {SNAPSHOT_SCHEMA_VERSION}")
    if baseline_snapshot.get("repo") != repo:
        raise ValueError("baseline snapshot repo must match repo")
    baseline_at, baseline_time = _timestamp(
        baseline_snapshot.get("captured_at"), field="baseline.captured_at"
    )
    captured_at, captured_time = _timestamp(generated_at, field="generated_at")
    if captured_time < baseline_time:
        raise ValueError("generated_at must not predate the baseline")

    stock = _public_stock(repo, timeout_seconds=timeout_seconds)
    flow = _public_flow(
        repo,
        baseline_at=baseline_at,
        baseline_time=baseline_time,
        timeout_seconds=timeout_seconds,
    )
    expected_issues = (
        int(baseline_snapshot.get("open_issues"))
        + flow["issues_opened"]
        - flow["issues_closed"]
    )
    expected_prs = (
        int(baseline_snapshot.get("open_pull_requests"))
        + flow["pull_requests_opened"]
        - flow["pull_requests_closed"]
    )
    if (
        expected_issues != stock["open_issues"]
        or expected_prs != stock["open_pull_requests"]
    ):
        raise ValueError("public GitHub stock and flow do not reconcile")

    feasibility_issue_numbers = _reference_numbers(
        feasibility_rows, repo=repo, ref_field="issue_ref"
    )
    supplement_issue_numbers = _supplement_issue_reference_numbers(
        metrics_supplement
    )
    issue_numbers = sorted(
        set(feasibility_issue_numbers) | set(supplement_issue_numbers)
    )
    if len(issue_numbers) > 50:
        raise ValueError("at most 50 issue_ref references may be refreshed")
    pr_numbers = _reference_numbers(pr_lifecycle_rows, repo=repo, ref_field="pr_ref")
    with ThreadPoolExecutor(
        max_workers=min(8, max(1, len(issue_numbers) + len(pr_numbers)))
    ) as pool:
        issue_states = list(
            pool.map(
                lambda number: _issue_state(
                    repo, number, timeout_seconds=timeout_seconds
                ),
                issue_numbers,
            )
        )
        pr_states = list(
            pool.map(
                lambda number: _pr_state(repo, number, timeout_seconds=timeout_seconds),
                pr_numbers,
            )
        )
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "repo": repo,
        "captured_at": captured_at,
        "source_url": f"https://github.com/{repo}",
        **stock,
        "flow_since_baseline": flow,
        "issue_states": issue_states,
        "pull_request_states": pr_states,
    }
    return {
        "ok": True,
        "schema_version": COLLECTION_SCHEMA_VERSION,
        "mode": "issue-fix-repository-snapshot",
        "repo": repo,
        "baseline_at": baseline_at,
        "snapshot": snapshot,
        "material_fingerprint": material_snapshot_fingerprint(snapshot),
        "source_contract": {
            "provider": "public_github_cli",
            "reads_existing_issue_fix_domain_state": True,
            "reads_metrics_supplement": metrics_supplement is not None,
            "writes_source_state": False,
        },
        "source_summary": {
            "feasibility_issue_refs": len(feasibility_issue_numbers),
            "supplement_issue_refs": len(supplement_issue_numbers),
            "issue_refs_collected": len(issue_numbers),
            "pr_lifecycle_refs": len(pr_numbers),
        },
        "external_reads_performed": True,
        "external_writes_performed": False,
        "raw_provider_payload_captured": False,
        "credentials_captured": False,
        "local_paths_captured": False,
    }


def render_repository_snapshot_markdown(payload: dict[str, Any]) -> str:
    if payload.get("ok") is not True:
        return f"# Issue-fix repository snapshot\n\n- Error: {payload.get('error') or 'collection failed'}"
    snapshot = payload.get("snapshot") or {}
    flow = snapshot.get("flow_since_baseline") or {}
    return "\n".join(
        [
            "# Issue-fix repository snapshot",
            "",
            f"- repository: `{snapshot.get('repo')}`",
            f"- captured at: `{snapshot.get('captured_at')}`",
            f"- open issues: `{snapshot.get('open_issues')}`",
            f"- open pull requests: `{snapshot.get('open_pull_requests')}`",
            f"- issues closed since baseline: `{flow.get('issues_closed')}`",
            f"- pull requests merged since baseline: `{flow.get('pull_requests_merged')}`",
            f"- material snapshot retained: `{(payload.get('retention') or {}).get('write_performed', False)}`",
        ]
    )
