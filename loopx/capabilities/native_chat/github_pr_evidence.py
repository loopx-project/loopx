"""Read-only GitHub PR signal summarization and target verification helpers."""

from __future__ import annotations

import configparser
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Mapping, Sequence

from ..issue_fix.github_public import _REPO_PATTERN as _REPO_SLUG_RE
from ..pr_review_queue.review_body import (
    author_owned_review_title_matches,
    check_review_body,
    english_review_verdict,
    reviewer_declaration_lines,
)
from ...control_plane.runtime.time import chronology_key, parse_timestamp
from ...control_plane.todos.list_readback import list_goal_todos
from ...registry import load_registry, registry_goals

GITHUB_PR_LIST_FIELDS = (
    "number,title,url,headRefOid,headRefName,baseRefName,"
    "isDraft,author,reviewDecision,mergeStateStatus,mergeable,updatedAt,"
    "reviews,statusCheckRollup"
)
GITHUB_PR_VIEW_FIELDS = GITHUB_PR_LIST_FIELDS + ",state,mergedAt,mergeCommit"

_GITHUB_URL_TARGET_RE = re.compile(
    r"^https://github\.com/([^/]+/[^/]+)/pull/([1-9][0-9]*)(?:[/?#].*)?$"
)
_REPO_NUM_TARGET_RE = re.compile(
    r"^(?:git:github\.com/)?([^/#]+/[^/#]+)#([1-9][0-9]*)$"
)
_BARE_NUM_TARGET_RE = re.compile(r"^#?([1-9][0-9]*)$")
_REMOTE_GITHUB_RE = re.compile(
    r"(?:^git:github\.com/|^git@github\.com:|^ssh://git@github\.com/|^https?://github\.com/)"
    r"([^/]+/[^/]+?)(?:\.git)?/?$"
)
_CHECK_FAIL_CONCLUSIONS = frozenset(
    {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
)
_CHECK_PASS_CONCLUSIONS = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
_KNOWN_BOT_LOGINS = frozenset(
    {"mergify", "codecov", "codecov-commenter", "github-actions", "dependabot"}
)
_MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_NON_MAINTAINER_ASSOCIATIONS = frozenset(
    {"NONE", "CONTRIBUTOR", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "MANNEQUIN"}
)


def _parse_iso_timestamp(value: Any) -> tuple[datetime, str] | None:
    raw = str(value or "").strip()
    parsed = parse_timestamp(raw)
    return None if parsed is None else (parsed, raw)


def _is_bot_login(login: str) -> bool:
    cleaned = str(login or "").strip().casefold()
    return not cleaned or cleaned.endswith("[bot]") or cleaned in _KNOWN_BOT_LOGINS


def _is_bot_actor(raw_user: Any, login: str) -> bool:
    if _is_bot_login(login):
        return True
    if isinstance(raw_user, Mapping):
        actor_type = str(raw_user.get("__typename") or raw_user.get("type") or "").strip().casefold()
        if actor_type == "bot":
            return True
    return False


def normalize_github_repo_slug(value: Any) -> str | None:
    """Normalize a `git:github.com/`, HTTPS, or SSH remote string to `owner/repo`."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if _REPO_SLUG_RE.fullmatch(raw):
        return raw.removesuffix(".git")
    match = _REMOTE_GITHUB_RE.match(raw)
    if match and _REPO_SLUG_RE.fullmatch(match.group(1)):
        return match.group(1)
    return None


def parse_github_pr_target(
    target: Any, *, default_repository: str | None = None
) -> tuple[str, int]:
    """Resolve an explicit PR URL or `<owner>/<repo>#<number>` target."""
    if not isinstance(target, str):
        raise ValueError("protected merge target must be a string")
    raw = target.strip()
    url_match = _GITHUB_URL_TARGET_RE.fullmatch(raw)
    if url_match and _REPO_SLUG_RE.fullmatch(url_match.group(1)):
        return url_match.group(1), int(url_match.group(2))
    repo_match = _REPO_NUM_TARGET_RE.fullmatch(raw)
    if repo_match and _REPO_SLUG_RE.fullmatch(repo_match.group(1)):
        return repo_match.group(1), int(repo_match.group(2))
    bare_match = _BARE_NUM_TARGET_RE.fullmatch(raw)
    if bare_match and default_repository and _REPO_SLUG_RE.fullmatch(default_repository):
        return default_repository, int(bare_match.group(1))
    raise ValueError(
        "protected merge target must be an explicit GitHub PR URL or <owner>/<repo>#<number>"
    )


def _git_origin_slug(repo_path: Path) -> str | None:
    git_entry = repo_path / ".git"
    if not git_entry.exists():
        return None
    config_path: Path | None = None
    if git_entry.is_dir():
        config_path = git_entry / "config"
    elif git_entry.is_file():
        try:
            content = git_entry.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            content = ""
        for line in content.splitlines():
            if line.lower().startswith("gitdir:"):
                gitdir = Path(line.split(":", 1)[1].strip())
                if not gitdir.is_absolute():
                    gitdir = (repo_path / gitdir).resolve()
                commondir = gitdir / "commondir"
                if commondir.is_file():
                    rel = commondir.read_text(encoding="utf-8", errors="ignore").strip()
                    config_path = (gitdir / rel / "config").resolve()
                else:
                    config_path = gitdir / "config"
                break
    if config_path and config_path.is_file():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(config_path, encoding="utf-8")
            url = parser.get('remote "origin"', "url", fallback="").strip()
            slug = normalize_github_repo_slug(url)
            if slug:
                return slug
        except (configparser.Error, OSError):
            pass
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_path), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, encoding="utf-8", check=False, timeout=3,
        )
        if completed.returncode == 0:
            return normalize_github_repo_slug(completed.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def repository_slugs_for_goals(
    registry_path: Path,
    goal_ids: Sequence[str],
    *,
    runtime_root: Path | None = None,
) -> set[str]:
    """Collect normalized GitHub `owner/repo` slugs declared by authorized Goals."""
    allowed_ids = {str(gid) for gid in goal_ids if gid}
    if not allowed_ids:
        return set()
    registry = load_registry(registry_path)
    slugs: set[str] = set()
    for goal in registry_goals(registry):
        gid = str(goal.get("id") or "")
        if gid not in allowed_ids:
            continue
        for key in ("task_repository", "github_repository", "repository"):
            slug = normalize_github_repo_slug(goal.get(key))
            if slug:
                slugs.add(slug)
        repo_dir = str(goal.get("repo") or "").strip()
        if repo_dir:
            slug = _git_origin_slug(Path(repo_dir).expanduser())
            if slug:
                slugs.add(slug)
        try:
            todos = list_goal_todos(
                registry_path=registry_path,
                runtime_root_arg=str(runtime_root) if runtime_root else None,
                goal_id=gid,
                role="all",
            ).get("todos", [])
            for row in todos:
                if isinstance(row, Mapping):
                    slug = normalize_github_repo_slug(row.get("task_repository"))
                    if slug:
                        slugs.add(slug)
        except (OSError, ValueError, RuntimeError):
            continue
    return slugs


def is_steward_repository_authorized(
    registry_path: Path,
    steward_context: Mapping[str, Any],
    repository: str,
    *,
    runtime_root: Path | None = None,
) -> bool:
    """Return True when `repository` belongs to an authorized Goal in `steward_context`."""
    if not _REPO_SLUG_RE.fullmatch(repository):
        return False
    goal_ids = steward_context.get("goal_ids") or []
    if not isinstance(goal_ids, Sequence) or isinstance(goal_ids, (str, bytes)):
        return False
    allowed = repository_slugs_for_goals(
        registry_path, [str(gid) for gid in goal_ids], runtime_root=runtime_root
    )
    return repository.casefold() in {slug.casefold() for slug in allowed}


def _summarize_checks(rollup: Any) -> dict[str, int]:
    items = [row for row in (rollup or []) if isinstance(row, Mapping)]
    pass_count = fail_count = pending_count = 0
    for item in items:
        conclusion = str(item.get("conclusion") or "").strip().upper()
        status = str(item.get("status") or "").strip().upper()
        state = str(item.get("state") or "").strip().upper()
        if conclusion in _CHECK_FAIL_CONCLUSIONS or state in {"FAILURE", "ERROR"}:
            fail_count += 1
        elif (status and status != "COMPLETED") or (not conclusion and state in {"PENDING", "EXPECTED"}):
            pending_count += 1
        elif conclusion in _CHECK_PASS_CONCLUSIONS or state == "SUCCESS":
            pass_count += 1
    return {
        "total": len(items),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "pending_count": pending_count,
    }


def _extract_review_record(
    review: Mapping[str, Any], *, head_oid: str, pr_author: str
) -> dict[str, Any]:
    raw_author = review.get("author") or review.get("user") or {}
    author_login = str(
        raw_author.get("login") if isinstance(raw_author, Mapping) else ""
    ).strip()
    state = str(review.get("state") or "").strip().upper()
    body = str(review.get("body") or "")
    verdict = english_review_verdict(body)
    author_is_pr_author = bool(
        author_login and pr_author and author_login.casefold() == pr_author.casefold()
    )
    formal_github_verdict = (
        "APPROVE"
        if state == "APPROVED" and not author_is_pr_author
        else "REQUEST_CHANGES"
        if state == "CHANGES_REQUESTED" and not author_is_pr_author
        else None
    )
    author_owned_fallback = bool(
        author_is_pr_author
        and state == "COMMENTED"
        and author_owned_review_title_matches(body, verdict)
    )
    bilingual_valid = bool(
        check_review_body(body, head_oid=head_oid, behavior_bearing=True)["valid"]
        or check_review_body(body, head_oid=head_oid, behavior_bearing=False)["valid"]
    )
    formal_matches_verdict = (
        (state == "APPROVED" and verdict == "APPROVE")
        or (state == "CHANGES_REQUESTED" and verdict == "REQUEST_CHANGES")
    )
    valid_verdict_review = bool(
        bilingual_valid
        and verdict in {"APPROVE", "REQUEST_CHANGES"}
        and ((not author_is_pr_author and formal_matches_verdict) or author_owned_fallback)
    )
    decls = reviewer_declaration_lines(body)
    return {
        "id": str(review.get("id") or review.get("node_id") or "").strip() or None,
        "url": str(review.get("url") or review.get("html_url") or "").strip() or None,
        "author_login": author_login or None,
        "author_is_pr_author": author_is_pr_author,
        "state": state or None,
        "verdict": verdict,
        "formal_github_verdict": formal_github_verdict,
        "author_owned_fallback": author_owned_fallback,
        "bilingual_valid": bilingual_valid,
        "valid_verdict_review": valid_verdict_review,
        "reviewer_declaration": decls[0] if decls else None,
        "submitted_at": str(review.get("submittedAt") or review.get("submitted_at") or "").strip() or None,
    }


def _actor_actionable_verdict(record: Mapping[str, Any]) -> str | None:
    if record.get("state") == "DISMISSED":
        return "DISMISSED"
    if record.get("valid_verdict_review"):
        verdict = record.get("verdict")
        return str(verdict) if verdict in {"APPROVE", "REQUEST_CHANGES"} else None
    formal = record.get("formal_github_verdict")
    verdict = record.get("verdict")
    if formal and verdict and formal != verdict:
        return "CONFLICT"
    if formal in {"APPROVE", "REQUEST_CHANGES"}:
        return str(formal)
    return None


def _post_reply_role(
    entry: Mapping[str, Any],
    *,
    pr_author: str,
    rc_reviewer_logins: set[str],
) -> tuple[int, str] | None:
    raw_user = entry.get("author") or entry.get("user") or {}
    login = str(raw_user.get("login") if isinstance(raw_user, Mapping) else "").strip()
    if _is_bot_actor(raw_user, login):
        return None
    if pr_author and login.casefold() == pr_author.casefold():
        return 1, "author"
    assoc = str(entry.get("authorAssociation") or entry.get("author_association") or "").strip().upper()
    if assoc in _MAINTAINER_ASSOCIATIONS or (
        bool(login) and login.casefold() in rc_reviewer_logins and assoc not in _NON_MAINTAINER_ASSOCIATIONS
    ):
        return 0, "maintainer"
    return None


def _summarize_request_changes_stall(
    row: Mapping[str, Any],
    *,
    raw_reviews: Sequence[Mapping[str, Any]],
    latest_record_by_actor: Mapping[tuple[str, str], tuple[str, dict[str, Any]]],
    head_oid: str,
    pr_author: str,
) -> tuple[str | None, str | None, bool, bool]:
    rc_candidates: list[tuple[datetime, str, dict[str, Any], str]] = []
    for verdict_val, rec in latest_record_by_actor.values():
        if verdict_val != "REQUEST_CHANGES":
            continue
        parsed = _parse_iso_timestamp(rec.get("submitted_at"))
        if parsed is not None:
            rc_candidates.append((parsed[0], str(rec.get("id") or ""), rec, parsed[1]))
    if not rc_candidates:
        return None, None, False, False
    latest_rc_dt, _latest_id, latest_rec, rc_iso = max(
        rc_candidates, key=lambda item: (item[0], item[1])
    )
    latest_rc_review_id = latest_rec.get("id")
    rc_reviewer_logins = {
        str(rec.get("author_login") or "").strip().casefold()
        for _dt, _rid, rec, _iso in rc_candidates
        if rec.get("author_login") and not rec.get("author_is_pr_author")
    }
    anchors: list[tuple[datetime, str]] = [(latest_rc_dt, rc_iso)]
    for commit_key in ("headCommitPushedAt", "headCommitCommittedAt"):
        parsed_commit = _parse_iso_timestamp(row.get(commit_key))
        if parsed_commit is not None:
            anchors.append(parsed_commit)
    stalled_anchor_dt, stalled_since = max(anchors, key=lambda pair: pair[0])

    earliest_comment_dt: datetime | None = None
    has_post_anchor_comment_reply = False
    post_replies: list[tuple[datetime, int, str]] = []
    for comment in row.get("comments") or []:
        if not isinstance(comment, Mapping):
            continue
        parsed_ts = _parse_iso_timestamp(comment.get("createdAt") or comment.get("created_at"))
        if parsed_ts is None:
            continue
        if earliest_comment_dt is None or parsed_ts[0] < earliest_comment_dt:
            earliest_comment_dt = parsed_ts[0]
        if parsed_ts[0] <= stalled_anchor_dt:
            continue
        role_info = _post_reply_role(comment, pr_author=pr_author, rc_reviewer_logins=rc_reviewer_logins)
        if role_info is not None:
            has_post_anchor_comment_reply = True
            post_replies.append((parsed_ts[0], role_info[0], role_info[1]))

    for review in raw_reviews:
        if str(review.get("state") or "").strip().upper() != "COMMENTED":
            continue
        parsed_ts = _parse_iso_timestamp(review.get("submittedAt") or review.get("submitted_at"))
        if parsed_ts is None or parsed_ts[0] <= stalled_anchor_dt:
            continue
        if _extract_review_record(review, head_oid=head_oid, pr_author=pr_author)["valid_verdict_review"]:
            continue
        role_info = _post_reply_role(review, pr_author=pr_author, rc_reviewer_logins=rc_reviewer_logins)
        if role_info is not None:
            post_replies.append((parsed_ts[0], role_info[0], role_info[1]))

    post_replies.sort(key=lambda item: (item[0], item[1]))
    author_replied = bool(pr_author and post_replies and post_replies[-1][2] == "author")
    # GraphQL comments(last: N) returns a newest-last suffix, so coverage is complete when
    # there is no earlier page, the fetched suffix reaches the anchor, or a newer reply
    # from the PR author or a maintainer in the visible suffix supersedes older comments.
    comments_coverage_complete = (
        row.get("commentsHasPreviousPage") is not True
        or (earliest_comment_dt is not None and earliest_comment_dt <= stalled_anchor_dt)
        or has_post_anchor_comment_reply
    )
    return latest_rc_review_id, stalled_since, author_replied, not comments_coverage_complete


def summarize_github_pr_signal(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project compact, deterministic exact-head review, check, and gate facts."""
    head_oid = str(row.get("headRefOid") or "").strip()
    raw_author = row.get("author")
    pr_author = str(
        raw_author.get("login") if isinstance(raw_author, Mapping) else (raw_author or "")
    ).strip()
    raw_reviews = [item for item in (row.get("reviews") or []) if isinstance(item, Mapping)]
    stale_count = 0
    exact_reviews: list[dict[str, Any]] = []
    for review in raw_reviews:
        raw_commit = review.get("commit")
        commit_oid = str(
            raw_commit.get("oid") if isinstance(raw_commit, Mapping) else (review.get("commit_id") or "")
        ).strip()
        if not head_oid or commit_oid.casefold() != head_oid.casefold():
            stale_count += 1
            continue
        exact_reviews.append(_extract_review_record(review, head_oid=head_oid, pr_author=pr_author))
    timed_reviews = [(chronology_key(review["submitted_at"]), review) for review in exact_reviews]
    # An undated verdict could supersede a dated one. Preserve the legacy
    # decision order when the evidence cannot establish their chronology.
    if any(not has_instant and _actor_actionable_verdict(review) is not None
           for (has_instant, _, _), review in timed_reviews):
        exact_reviews.sort(key=lambda review: (review["submitted_at"] or "", review["id"] or ""))
    else:
        timed_reviews.sort(key=lambda entry: (entry[0], entry[1]["id"] or ""))
        exact_reviews = [review for _, review in timed_reviews]

    latest_by_actor: dict[tuple[str, str], str | None] = {}
    latest_record_by_actor: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    inconsistent_verdict = False
    for record in exact_reviews:
        actionable = _actor_actionable_verdict(record)
        if actionable is None:
            continue
        actor_key = (
            (record["author_login"] or "").casefold(),
            (record["reviewer_declaration"] or "").casefold(),
        )
        if actionable == "DISMISSED":
            latest_by_actor.pop(actor_key, None)
            latest_record_by_actor.pop(actor_key, None)
        elif actionable == "CONFLICT":
            inconsistent_verdict = True
            latest_by_actor[actor_key] = "REQUEST_CHANGES"
            latest_record_by_actor[actor_key] = ("REQUEST_CHANGES", record)
        else:
            latest_by_actor[actor_key] = actionable
            latest_record_by_actor[actor_key] = (actionable, record)

    latest_rc_review_id, stalled_since, author_replied_after_rc, comments_coverage_incomplete = (
        _summarize_request_changes_stall(
            row,
            raw_reviews=raw_reviews,
            latest_record_by_actor=latest_record_by_actor,
            head_oid=head_oid,
            pr_author=pr_author,
        )
    )

    verdicts = {val for val in latest_by_actor.values() if val}
    review_decision = str(row.get("reviewDecision") or "").strip().upper()
    has_conflict = (
        inconsistent_verdict
        or verdicts == {"APPROVE", "REQUEST_CHANGES"}
        or (review_decision == "CHANGES_REQUESTED" and "APPROVE" in verdicts)
    )
    if has_conflict or "REQUEST_CHANGES" in verdicts or review_decision == "CHANGES_REQUESTED":
        effective_verdict = "REQUEST_CHANGES"
    elif "APPROVE" in verdicts or (
        review_decision == "APPROVED" and any(r["formal_github_verdict"] == "APPROVE" for r in exact_reviews)
    ):
        effective_verdict = "APPROVE"
    else:
        effective_verdict = "NONE"

    checks_summary = _summarize_checks(row.get("statusCheckRollup"))
    merge_state = str(row.get("mergeStateStatus") or "").strip().upper()
    bypassed_gates: list[str] = []
    if review_decision == "REVIEW_REQUIRED":
        bypassed_gates.append("REVIEW_REQUIRED")
    if merge_state == "BLOCKED":
        bypassed_gates.append("BLOCKED_MERGE_STATE")
    if checks_summary["fail_count"] > 0:
        bypassed_gates.append("FAILING_CHECKS")
    if checks_summary["pending_count"] > 0:
        bypassed_gates.append("PENDING_CHECKS")
    if merge_state == "UNSTABLE":
        bypassed_gates.append("UNSTABLE_MERGE_STATE")

    merge_commit = row.get("mergeCommit")
    merge_commit_oid = (
        str(merge_commit.get("oid") or "").strip() or None
        if isinstance(merge_commit, Mapping)
        else None
    )
    summary: dict[str, Any] = {
        "number": row.get("number"),
        "title": row.get("title"),
        "url": row.get("url"),
        "headRefOid": row.get("headRefOid"),
        "headRefName": row.get("headRefName"),
        "baseRefName": row.get("baseRefName"),
        "isDraft": row.get("isDraft"),
        "author": row.get("author"),
        "reviewDecision": row.get("reviewDecision"),
        "mergeStateStatus": row.get("mergeStateStatus"),
        "mergeable": row.get("mergeable"),
        "updatedAt": row.get("updatedAt"),
        "stalled_since": stalled_since,
        "author_replied_after_request_changes": author_replied_after_rc,
        "comments_coverage_incomplete": comments_coverage_incomplete,
        "checks_summary": checks_summary,
        "review_summary": {
            "effective_verdict": effective_verdict,
            "has_review_conflict": has_conflict,
            "stale_review_count": stale_count,
            "latest_request_changes_review_id": latest_rc_review_id,
            "stalled_since": stalled_since,
            "author_replied_after_request_changes": author_replied_after_rc,
            "comments_coverage_incomplete": comments_coverage_incomplete,
            "exact_head_reviews": exact_reviews,
        },
        "bypassed_gates": bypassed_gates,
    }
    for optional_key in ("state", "mergedAt"):
        if optional_key in row:
            summary[optional_key] = row.get(optional_key)
    if "mergeCommit" in row:
        summary["merge_commit_oid"] = merge_commit_oid
    return summary


_GITHUB_PR_LIST_PAGE_SIZE = 20
_GITHUB_PR_LIST_MAX_PAGES = 5
_GITHUB_PR_COMMENT_MAX_BACKFILL_PAGES = 3
_GITHUB_PR_COMMENTS_PAGE_QUERY = """query($owner: String!, $name: String!, $number: Int!, $before: String!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      comments(last: 50, before: $before) {
        pageInfo {
          hasPreviousPage
          startCursor
        }
        nodes {
          createdAt
          authorAssociation
          author {
            __typename
            login
          }
        }
      }
    }
  }
}"""
_GITHUB_OPEN_PRS_GRAPHQL_QUERY = """query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      states: OPEN,
      first: 20,
      after: $cursor,
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        number
        title
        url
        headRefOid
        headRefName
        baseRefName
        isDraft
        reviewDecision
        mergeStateStatus
        mergeable
        updatedAt
        author {
          login
        }
        comments(last: 20) {
          pageInfo {
            hasPreviousPage
            startCursor
          }
          nodes {
            createdAt
            authorAssociation
            author {
              __typename
              login
            }
          }
        }
        reviews(last: 20) {
          nodes {
            id
            state
            submittedAt
            body
            authorAssociation
            author {
              __typename
              login
            }
            commit {
              oid
            }
          }
        }
        commits(last: 1) {
          nodes {
            commit {
              oid
              committedDate
              pushedDate
              statusCheckRollup {
                contexts(first: 30) {
                  nodes {
                    __typename
                    ... on CheckRun {
                      name
                      status
                      conclusion
                    }
                    ... on StatusContext {
                      context
                      state
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}"""


def _normalize_graphql_pr_node(node: Mapping[str, Any]) -> dict[str, Any]:
    raw_reviews = node.get("reviews")
    if isinstance(raw_reviews, Mapping) and isinstance(raw_reviews.get("nodes"), list):
        reviews = [item for item in raw_reviews["nodes"] if isinstance(item, Mapping)]
    elif isinstance(raw_reviews, list):
        reviews = [item for item in raw_reviews if isinstance(item, Mapping)]
    else:
        reviews = []

    raw_comments = node.get("comments")
    comments_page_info: Mapping[str, Any] = {}
    if isinstance(raw_comments, Mapping):
        raw_nodes = raw_comments.get("nodes")
        comments = [item for item in raw_nodes if isinstance(item, Mapping)] if isinstance(raw_nodes, list) else []
        raw_page_info = raw_comments.get("pageInfo")
        if isinstance(raw_page_info, Mapping):
            comments_page_info = raw_page_info
    elif isinstance(raw_comments, list):
        comments = [item for item in raw_comments if isinstance(item, Mapping)]
    else:
        comments = []
    comments_has_previous_page = bool(comments_page_info.get("hasPreviousPage") is True)
    comments_start_cursor = str(comments_page_info.get("startCursor") or "").strip() or None

    raw_commits = node.get("commits")
    commit_nodes = raw_commits.get("nodes") if isinstance(raw_commits, Mapping) else None
    first_commit = (
        commit_nodes[0].get("commit")
        if isinstance(commit_nodes, list) and commit_nodes and isinstance(commit_nodes[0], Mapping)
        else None
    )
    head_oid = str(node.get("headRefOid") or "").strip()
    head_committed_at = node.get("headCommitCommittedAt")
    head_pushed_at = node.get("headCommitPushedAt")
    if isinstance(first_commit, Mapping):
        commit_oid = str(first_commit.get("oid") or "").strip()
        if not commit_oid or not head_oid or commit_oid.casefold() == head_oid.casefold():
            if head_committed_at is None:
                head_committed_at = first_commit.get("committedDate")
            if head_pushed_at is None:
                head_pushed_at = first_commit.get("pushedDate")

    rollup = node.get("statusCheckRollup")
    if rollup is None:
        raw_rollup = first_commit.get("statusCheckRollup") if isinstance(first_commit, Mapping) else None
        raw_contexts = raw_rollup.get("contexts") if isinstance(raw_rollup, Mapping) else None
        context_nodes = raw_contexts.get("nodes") if isinstance(raw_contexts, Mapping) else None
        rollup = [item for item in context_nodes if isinstance(item, Mapping)] if isinstance(context_nodes, list) else []

    return {
        "number": node.get("number"),
        "title": node.get("title"),
        "url": node.get("url"),
        "headRefOid": node.get("headRefOid"),
        "headRefName": node.get("headRefName"),
        "baseRefName": node.get("baseRefName"),
        "isDraft": node.get("isDraft"),
        "author": node.get("author"),
        "reviewDecision": node.get("reviewDecision"),
        "mergeStateStatus": node.get("mergeStateStatus"),
        "mergeable": node.get("mergeable"),
        "updatedAt": node.get("updatedAt"),
        "headCommitCommittedAt": head_committed_at,
        "headCommitPushedAt": head_pushed_at,
        "comments": comments,
        "commentsHasPreviousPage": comments_has_previous_page,
        "commentsStartCursor": comments_start_cursor,
        "reviews": reviews,
        "statusCheckRollup": rollup,
    }


def _backfill_pr_comments_if_needed(
    norm_node: dict[str, Any],
    summary: dict[str, Any],
    *,
    owner: str,
    name: str,
    number: int,
    runner: Callable[..., Any],
) -> dict[str, Any]:
    """Boundedly backfill older PR comment pages when active RC reply coverage is truncated."""
    attempts = 0
    while (
        summary.get("comments_coverage_incomplete") is True
        and attempts < _GITHUB_PR_COMMENT_MAX_BACKFILL_PAGES
    ):
        comment_cursor = str(norm_node.get("commentsStartCursor") or "").strip()
        if not comment_cursor:
            break
        attempts += 1
        try:
            completed = runner(
                [
                    "gh",
                    "api",
                    "graphql",
                    "-f",
                    f"query={_GITHUB_PR_COMMENTS_PAGE_QUERY}",
                    "-f",
                    f"owner={owner}",
                    "-f",
                    f"name={name}",
                    "-F",
                    f"number={number}",
                    "-f",
                    f"before={comment_cursor}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            break
        if completed.returncode != 0:
            break
        try:
            payload = json.loads(completed.stdout or "{}")
        except (ValueError, TypeError):
            break
        if not isinstance(payload, Mapping) or payload.get("errors"):
            break
        data = payload.get("data")
        repo = data.get("repository") if isinstance(data, Mapping) else None
        pr_data = repo.get("pullRequest") if isinstance(repo, Mapping) else None
        comments_conn = pr_data.get("comments") if isinstance(pr_data, Mapping) else None
        older_nodes = comments_conn.get("nodes") if isinstance(comments_conn, Mapping) else None
        page_info = comments_conn.get("pageInfo") if isinstance(comments_conn, Mapping) else None
        if not isinstance(older_nodes, list) or not isinstance(page_info, Mapping):
            break
        older_comments = [item for item in older_nodes if isinstance(item, Mapping)]
        next_has_prev = page_info.get("hasPreviousPage") is True
        next_cursor = str(page_info.get("startCursor") or "").strip() or None
        stalled_cursor = next_has_prev and (not next_cursor or next_cursor == comment_cursor)
        norm_node["comments"] = [*older_comments, *norm_node["comments"]]
        norm_node["commentsHasPreviousPage"] = next_has_prev
        norm_node["commentsStartCursor"] = None if stalled_cursor else next_cursor
        summary = summarize_github_pr_signal(norm_node)
        if stalled_cursor:
            break
    return summary


def observe_github_repository_prs(
    repo_slug: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
    backfill_comments: bool = False,
) -> dict[str, Any]:
    """Read bounded public/authorized open PR signal metadata without side effects."""
    normalized = normalize_github_repo_slug(repo_slug)
    if not normalized or "/" not in normalized:
        return {"status": "unavailable", "repository": repo_slug}
    owner, name = normalized.split("/", 1)
    raw_nodes_by_number: dict[int, Mapping[str, Any]] = {}
    cursor: str | None = None
    try:
        for page_index in range(_GITHUB_PR_LIST_MAX_PAGES):
            cmd = [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={_GITHUB_OPEN_PRS_GRAPHQL_QUERY}",
                "-f",
                f"owner={owner}",
                "-f",
                f"name={name}",
            ]
            if cursor is not None:
                cmd.extend(["-f", f"cursor={cursor}"])
            completed = runner(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            if completed.returncode != 0:
                return {"status": "unavailable", "repository": repo_slug}
            payload = json.loads(completed.stdout or "{}")
            if not isinstance(payload, Mapping) or payload.get("errors"):
                return {"status": "unavailable", "repository": repo_slug}
            data = payload.get("data")
            repo = data.get("repository") if isinstance(data, Mapping) else None
            pull_requests = repo.get("pullRequests") if isinstance(repo, Mapping) else None
            nodes = pull_requests.get("nodes") if isinstance(pull_requests, Mapping) else None
            if not isinstance(nodes, list):
                return {"status": "unavailable", "repository": repo_slug}
            for node in nodes:
                if not isinstance(node, Mapping):
                    continue
                number = int(node.get("number") or 0)
                if number > 0:
                    raw_nodes_by_number[number] = node
            page_info = pull_requests.get("pageInfo")
            if not isinstance(page_info, Mapping) or page_info.get("hasNextPage") is not True:
                break
            next_cursor = str(page_info.get("endCursor") or "").strip()
            if not next_cursor or next_cursor == cursor:
                if page_index < _GITHUB_PR_LIST_MAX_PAGES - 1:
                    return {"status": "unavailable", "repository": repo_slug}
                break
            cursor = next_cursor
        items: list[dict[str, Any]] = []
        for number in sorted(raw_nodes_by_number):
            norm_node = _normalize_graphql_pr_node(raw_nodes_by_number[number])
            summary = summarize_github_pr_signal(norm_node)
            if backfill_comments:
                summary = _backfill_pr_comments_if_needed(
                    norm_node,
                    summary,
                    owner=owner,
                    name=name,
                    number=number,
                    runner=runner,
                )
            items.append(summary)
        return {"status": "ok", "repository": repo_slug, "items": items}
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return {"status": "unavailable", "repository": repo_slug}


def _enrich_review_urls(
    row: dict[str, Any],
    repo_slug: str,
    pr_number: int,
    *,
    runner: Callable[..., Any],
) -> None:
    reviews = row.get("reviews")
    if not isinstance(reviews, list) or not reviews:
        return
    if all(isinstance(r, Mapping) and r.get("url") for r in reviews):
        return
    try:
        completed = runner(
            ["gh", "api", f"repos/{repo_slug}/pulls/{pr_number}/reviews?per_page=100"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        if completed.returncode != 0:
            return
        rest_rows = json.loads(completed.stdout or "[]")
        if not isinstance(rest_rows, list):
            return
        by_node = {
            str(r.get("node_id")): str(r.get("html_url"))
            for r in rest_rows
            if isinstance(r, Mapping) and r.get("node_id") and r.get("html_url")
        }
        by_sig = {
            (
                str((r.get("user") or {}).get("login") or "").casefold(),
                str(r.get("state") or "").upper(),
                str(r.get("commit_id") or "").casefold(),
                str(r.get("submitted_at") or ""),
            ): str(r.get("html_url"))
            for r in rest_rows
            if isinstance(r, Mapping) and r.get("html_url")
        }
        for review in reviews:
            if not isinstance(review, dict) or review.get("url"):
                continue
            node_id = str(review.get("id") or "")
            if node_id and node_id in by_node:
                review["url"] = by_node[node_id]
                continue
            author = review.get("author") or {}
            commit = review.get("commit") or {}
            sig = (
                str(author.get("login") if isinstance(author, Mapping) else "").casefold(),
                str(review.get("state") or "").upper(),
                str(commit.get("oid") if isinstance(commit, Mapping) else "").casefold(),
                str(review.get("submittedAt") or ""),
            )
            if sig in by_sig:
                review["url"] = by_sig[sig]
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return


def inspect_github_pull_request(
    repo_slug: str, pr_number: int, *, runner: Callable[..., Any] = subprocess.run
) -> dict[str, Any] | None:
    """Inspect a single PR with exact-head review summary and review HTML URLs."""
    try:
        completed = runner(
            [
                "gh", "pr", "view", str(pr_number), "--repo", repo_slug,
                "--json", GITHUB_PR_VIEW_FIELDS,
            ],
            capture_output=True, text=True, check=False, timeout=15,
        )
        if completed.returncode != 0:
            return None
        row = json.loads(completed.stdout or "{}")
        if not isinstance(row, dict) or not row.get("number"):
            return None
        _enrich_review_urls(row, repo_slug, pr_number, runner=runner)
        return summarize_github_pr_signal(row)
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None
