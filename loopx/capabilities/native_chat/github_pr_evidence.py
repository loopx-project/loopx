"""Read-only GitHub PR signal summarization and target verification helpers."""

from __future__ import annotations

import configparser
from datetime import datetime, timezone
from functools import partial
import json
from pathlib import Path
import re
import subprocess
from time import monotonic
from typing import Any, Callable, Mapping, NamedTuple, Sequence

from ..issue_fix.github_public import _REPO_PATTERN as _REPO_SLUG_RE
from ..pr_review_queue.review_body import (
    author_owned_review_title_matches,
    check_review_body,
    english_review_verdict,
    reviewer_declaration_lines,
)
from ...control_plane.todos.list_readback import list_goal_todos
from ...registry import load_registry, registry_goals

GITHUB_PR_LIST_FIELDS = (
    "number,title,url,headRefOid,headRefName,baseRefName,"
    "isDraft,author,reviewDecision,mergeStateStatus,mergeable,updatedAt,"
    "reviews,statusCheckRollup"
)

_KNOWN_MERGE_STATES = {"BEHIND", "BLOCKED", "CLEAN", "DIRTY", "DRAFT", "HAS_HOOKS", "UNSTABLE"}
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
_CHECK_ACTIVE_STATUSES = frozenset({"IN_PROGRESS", "PENDING", "QUEUED", "REQUESTED", "WAITING"})
_KNOWN_BOT_LOGINS = frozenset(
    {"mergify", "codecov", "codecov-commenter", "github-actions", "dependabot"}
)
_MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_NON_MAINTAINER_ASSOCIATIONS = frozenset(
    {"NONE", "CONTRIBUTOR", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "MANNEQUIN"}
)


def _parse_iso_timestamp(value: Any) -> tuple[datetime, str] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(normalized)
        dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None
    return dt, raw


def _is_bot_login(login: str) -> bool:
    cleaned = str(login or "").strip().casefold()
    return cleaned.endswith("[bot]") or cleaned in _KNOWN_BOT_LOGINS


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


def _tally_checks(rollup: object) -> tuple[dict[str, int], bool]:
    items = [] if rollup is None else rollup
    if not isinstance(items, (list, tuple)):
        items = ()
    pass_count = fail_count = pending_count = 0
    for item in items:
        if not isinstance(item, Mapping):
            continue
        conclusion = str(item.get("conclusion") or "").strip().upper()
        status = str(item.get("status") or "").strip().upper()
        state = str(item.get("state") or "").strip().upper()
        if conclusion == "STALE" or (status and status != "COMPLETED" and status not in _CHECK_ACTIVE_STATUSES):
            continue
        if conclusion in _CHECK_FAIL_CONCLUSIONS or state in {"FAILURE", "ERROR"}:
            fail_count += 1
        elif status in _CHECK_ACTIVE_STATUSES or (not conclusion and state in {"PENDING", "EXPECTED"}):
            pending_count += 1
        elif conclusion in _CHECK_PASS_CONCLUSIONS or state == "SUCCESS":
            pass_count += 1
    counts = {
        "total": len(items),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "pending_count": pending_count,
    }
    complete = (rollup is None or isinstance(rollup, (list, tuple))) and len(items) == pass_count + fail_count + pending_count
    return counts, complete


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
) -> tuple[int, str | None] | None:
    raw_user = entry.get("author") or entry.get("user") or {}
    raw_login = raw_user.get("login") if isinstance(raw_user, Mapping) else None
    login = raw_login.strip() if isinstance(raw_login, str) else ""
    if _is_bot_actor(raw_user, login):
        return None
    if not login or not pr_author:
        return 1, None
    if login.casefold() == pr_author.casefold():
        return 1, "author"
    assoc = str(entry.get("authorAssociation") or entry.get("author_association") or "").strip().upper()
    if assoc in _MAINTAINER_ASSOCIATIONS or (
        login.casefold() in rc_reviewer_logins and assoc not in _NON_MAINTAINER_ASSOCIATIONS
    ):
        return 0, "maintainer"
    return None


class _StallEvidence(NamedTuple):
    latest_review_id: str | None = None
    stalled_since: str | None = None
    author_replied: bool = False
    comments_incomplete: bool = False
    stalled_since_unknown: bool = False


def _summarize_request_changes_stall(
    row: Mapping[str, Any],
    *,
    raw_reviews: Sequence[Mapping[str, Any]],
    latest_record_by_actor: Mapping[tuple[str, str], tuple[str, dict[str, Any]]],
    head_oid: str,
    pr_author: str,
) -> _StallEvidence:
    rc_candidates: list[tuple[datetime, str, dict[str, Any], str]] = []
    for verdict_val, rec in latest_record_by_actor.values():
        if verdict_val != "REQUEST_CHANGES":
            continue
        parsed = _parse_iso_timestamp(rec.get("submitted_at"))
        if parsed is not None:
            rc_candidates.append((parsed[0], str(rec.get("id") or ""), rec, parsed[1]))
    if not rc_candidates:
        return _StallEvidence()
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
    unbound_commit_at = _parse_iso_timestamp(row.get("_unbound_head_commit_at"))
    if unbound_commit_at is not None and unbound_commit_at[0] > stalled_anchor_dt:
        # Both the age and the set of post-anchor replies depend on this clock's identity.
        return _StallEvidence(latest_review_id=latest_rc_review_id, stalled_since_unknown=True)

    if row.get("_comment_readback_incomplete") is True:
        return _StallEvidence(latest_review_id=latest_rc_review_id, stalled_since=stalled_since, comments_incomplete=True)

    earliest_comment_dt: datetime | None = None
    has_post_anchor_comment_reply = False
    post_replies: list[tuple[datetime, int, str | None]] = []
    undated_reply_roles: set[str | None] = set()
    for comment in row.get("comments") or []:
        if not isinstance(comment, Mapping):
            continue
        parsed_ts = _parse_iso_timestamp(comment.get("createdAt") or comment.get("created_at"))
        if parsed_ts is not None:
            if earliest_comment_dt is None or parsed_ts[0] < earliest_comment_dt:
                earliest_comment_dt = parsed_ts[0]
            if parsed_ts[0] <= stalled_anchor_dt:
                continue
        role_info = _post_reply_role(comment, pr_author=pr_author, rc_reviewer_logins=rc_reviewer_logins)
        if role_info is None:
            continue
        if parsed_ts is None:
            undated_reply_roles.add(role_info[1])
        else:
            has_post_anchor_comment_reply |= role_info[1] is not None
            post_replies.append((parsed_ts[0], role_info[0], role_info[1]))

    for review in raw_reviews:
        if str(review.get("state") or "").strip().upper() != "COMMENTED":
            continue
        parsed_ts = _parse_iso_timestamp(review.get("submittedAt") or review.get("submitted_at"))
        if parsed_ts is not None and parsed_ts[0] <= stalled_anchor_dt:
            continue
        if _extract_review_record(review, head_oid=head_oid, pr_author=pr_author)["valid_verdict_review"]:
            continue
        role_info = _post_reply_role(review, pr_author=pr_author, rc_reviewer_logins=rc_reviewer_logins)
        if role_info is None:
            continue
        if parsed_ts is None:
            undated_reply_roles.add(role_info[1])
        else:
            post_replies.append((parsed_ts[0], role_info[0], role_info[1]))

    # An unknown actor could be the PR author. A known author at the same time settles that tie.
    post_replies.sort(key=lambda item: (item[0], item[1], item[2] is not None))
    latest_reply = post_replies[-1] if post_replies else None
    author_replied = latest_reply is not None and latest_reply[2] == "author"
    # An undated reply could precede the anchor or supersede all dated replies.
    compatible_role = "author" if author_replied else "maintainer"
    reply_role_uncertain = (
        (latest_reply is not None and latest_reply[2] is None)
        or bool(undated_reply_roles - {compatible_role})
    )
    # GraphQL comments(last: N) returns a newest-last suffix, so coverage is complete when
    # there is no earlier page, the fetched suffix reaches the anchor, or a newer reply
    # from the PR author or a maintainer in the visible suffix supersedes older comments.
    comments_coverage_complete = not reply_role_uncertain and (
        row.get("commentsHasPreviousPage") is not True
        or (earliest_comment_dt is not None and earliest_comment_dt <= stalled_anchor_dt)
        or has_post_anchor_comment_reply
    )
    return _StallEvidence(
        latest_review_id=latest_rc_review_id,
        stalled_since=stalled_since,
        author_replied=author_replied and not reply_role_uncertain,
        comments_incomplete=not comments_coverage_complete,
    )


def summarize_github_pr_signal(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project compact, deterministic exact-head review, check, and gate facts."""
    head_oid = str(row.get("headRefOid") or "").strip()
    raw_author = row.get("author")
    author_login = raw_author.get("login") if isinstance(raw_author, Mapping) else raw_author
    pr_author = author_login.strip() if isinstance(author_login, str) else ""
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
    exact_reviews.sort(key=lambda item: (item["submitted_at"] or "", item["id"] or ""))

    latest_by_actor: dict[tuple[str, str], str | None] = {}
    latest_record_by_actor: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    inconsistent_verdict = False
    stalled_since_unknown = False
    for record in exact_reviews:
        actionable = _actor_actionable_verdict(record)
        if actionable is None:
            continue
        # An undated Request Changes review cannot be ordered relative to dated reviews.
        if actionable in {"REQUEST_CHANGES", "CONFLICT"} and _parse_iso_timestamp(record["submitted_at"]) is None:
            stalled_since_unknown = True
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

    stall = (
        _StallEvidence(stalled_since_unknown=True)
        if stalled_since_unknown
        else _summarize_request_changes_stall(
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

    check_counts, checks_complete = _tally_checks(row.get("statusCheckRollup"))
    checks_summary = check_counts if checks_complete and row.get("checks_coverage_incomplete") is not True else None
    merge_state = str(row.get("mergeStateStatus") or "").strip().upper()
    bypassed_gates: list[str] = []
    if review_decision == "REVIEW_REQUIRED":
        bypassed_gates.append("REVIEW_REQUIRED")
    if merge_state == "BLOCKED":
        bypassed_gates.append("BLOCKED_MERGE_STATE")
    if check_counts["fail_count"] > 0:
        bypassed_gates.append("FAILING_CHECKS")
    if check_counts["pending_count"] > 0:
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
        **({"merge_state_coverage_incomplete": True} if row.get("merge_state_coverage_incomplete") is True else {}),
        "mergeable": row.get("mergeable"),
        "updatedAt": row.get("updatedAt"),
        "stalled_since": stall.stalled_since,
        **({"review_coverage_incomplete": True} if row.get("review_coverage_incomplete") is True else {}),
        **({"review_readback_incomplete": True} if row.get("review_readback_incomplete") is True else {}),
        **({"stalled_since_unknown": True} if stall.stalled_since_unknown else {}),
        "author_replied_after_request_changes": stall.author_replied,
        "comments_coverage_incomplete": stall.comments_incomplete,
        "checks_summary": checks_summary,
        **({"checks_coverage_incomplete": True} if checks_summary is None else {}),
        "review_summary": {
            "effective_verdict": effective_verdict,
            "has_review_conflict": has_conflict,
            "stale_review_count": stale_count,
            "latest_request_changes_review_id": stall.latest_review_id,
            "stalled_since": stall.stalled_since,
            "author_replied_after_request_changes": stall.author_replied,
            "comments_coverage_incomplete": stall.comments_incomplete,
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
_GITHUB_PR_LIST_OVERFLOW_PAGE_SIZE = 25
_GITHUB_PR_LIST_MAX_PAGES = 5
_GITHUB_PR_QUERY_TIMEOUT_SECONDS = 20
_GITHUB_PR_SCAN_TIMEOUT_SECONDS = _GITHUB_PR_LIST_MAX_PAGES * _GITHUB_PR_QUERY_TIMEOUT_SECONDS
_GITHUB_PR_LIST_STANDARD_CAPACITY = _GITHUB_PR_LIST_PAGE_SIZE * _GITHUB_PR_LIST_MAX_PAGES
_GITHUB_PR_LIST_MAX_CAPACITY = _GITHUB_PR_LIST_PAGE_SIZE + (
    _GITHUB_PR_LIST_MAX_PAGES - 1
) * _GITHUB_PR_LIST_OVERFLOW_PAGE_SIZE
_GITHUB_PR_MAX_BACKFILL_PAGES = 3
_GITHUB_PR_BACKFILL_BUDGET = (
    _GITHUB_PR_LIST_STANDARD_CAPACITY * _GITHUB_PR_MAX_BACKFILL_PAGES
)
_GITHUB_PR_REVIEW_PAGE_SIZE = 20
_GITHUB_PR_CHECK_CONTEXT_PAGE_SIZE = 30
_GITHUB_PR_LATEST_COMMENT_PAGE_SIZE = 20
_GITHUB_PR_OLDER_COMMENT_PAGE_SIZE = 50
_GITHUB_PR_REVIEW_PAGE_FRAGMENT = """
fragment StewardReviewPage on PullRequestReviewConnection {
  totalCount
  pageInfo { hasPreviousPage startCursor }
  nodes {
    id
    state
    submittedAt
    body
    authorAssociation
    author { __typename login }
    commit { oid }
  }
}"""


def _older_reviews_page_query(readbacks: Mapping[str, str]) -> str:
    variables = "".join(f", ${alias}: String!" for alias in readbacks)
    fields = "".join(
        f"      {alias}: reviews(last: 20, before: ${alias}) {{ ...StewardReviewPage }}\n"
        for alias in readbacks
    )
    return f"""query($owner: String!, $name: String!, $number: Int!, $before: String!{variables}) {{
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $number) {{
      number
      headRefOid
      updatedAt
      reviewDecision
      latestReviews: reviews(last: 20) {{ ...StewardReviewPage }}
      reviews(last: 20, before: $before) {{ ...StewardReviewPage }}
{fields}    }}
  }}
}}""" + _GITHUB_PR_REVIEW_PAGE_FRAGMENT


_GITHUB_PR_COMMENT_PAGE_FRAGMENT = """
fragment StewardCommentPage on IssueCommentConnection {
  pageInfo { hasPreviousPage startCursor }
  nodes {
    createdAt
    authorAssociation
    author { __typename login }
  }
}"""


def _older_comments_page_query(readbacks: Mapping[str, str]) -> str:
    variables = "".join(f", ${alias}: String!" for alias in readbacks)
    fields = "".join(
        f"      {alias}: comments(last: 50, before: ${alias}) {{ ...StewardCommentPage }}\n"
        for alias in readbacks
    )
    return f"""query($owner: String!, $name: String!, $number: Int!, $before: String!{variables}) {{
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $number) {{
      number
      headRefOid
      updatedAt
      reviewDecision
      latestComments: comments(last: 20) {{ ...StewardCommentPage }}
      comments(last: 50, before: $before) {{ ...StewardCommentPage }}
{fields}    }}
  }}
}}""" + _GITHUB_PR_COMMENT_PAGE_FRAGMENT


_GITHUB_OPEN_PR_FIELDS = """
    pullRequests(
      states: OPEN,
      first: $pageSize,
      after: $cursor,
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      totalCount
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        ...StewardPrWindowRow
        ...StewardPrScopeFields
        title
        url
        headRefName
        baseRefName
        comments(last: 20) {
          ...StewardCommentPage
        }
        reviews(last: 20) {
          ...StewardReviewPage
        }
        commits(last: 1) {
          nodes {
            commit {
              oid
              committedDate
              pushedDate
              statusCheckRollup {
                contexts(first: 30) {
                  ...StewardCheckContextWindow
                }
              }
            }
          }
        }
      }
    }"""

_GITHUB_OPEN_PR_WINDOW_FRAGMENT = """
fragment StewardPrWindowRow on PullRequest {
  number
  headRefOid
  updatedAt
  reviewDecision
}"""

_GITHUB_OPEN_PR_SCOPE_FRAGMENT = """
fragment StewardPrScopeFields on PullRequest {
  isDraft
  mergeable
  mergeStateStatus
  author { login }
}"""

_GITHUB_CHECK_CONTEXT_WINDOW_FRAGMENT = """
fragment StewardCheckContextWindow on StatusCheckRollupContextConnection {
  totalCount
  pageInfo { hasNextPage }
  nodes {
    ... on CheckRun { id status conclusion }
    ... on StatusContext { id state }
  }
}"""


def _open_pr_check_readback(limit: int) -> str:
    return f"""
        commits(last: 1) {{
          nodes {{ commit {{ oid statusCheckRollup {{
            contexts(first: {limit}) {{ ...StewardCheckContextWindow }}
          }} }} }}
        }}"""


class _OpenPrReadbackLimits(NamedTuple):
    page_size: int
    checks: int | None
    reviews: int | None
    comments: int | None


def _open_pr_readback_fields(limits: _OpenPrReadbackLimits) -> str:
    fields = _open_pr_check_readback(limits.checks) if limits.checks is not None else ""
    if limits.reviews is not None:
        fields += f"\n        reviews(last: {limits.reviews}) {{ ...StewardReviewPage }}"
    if limits.comments is not None:
        fields += f"\n        comments(last: {limits.comments}) {{ ...StewardCommentPage }}"
    return fields


def _open_prs_graphql_query(previous_limits: Sequence[_OpenPrReadbackLimits], *, terminal_scope: bool) -> str:
    variables = "".join(f", $previousCursor{index}: String" for index in range(len(previous_limits)))
    scope_fields = "\n        ...StewardPrScopeFields" if terminal_scope else ""
    readbacks = "".join(
        f"""
    previousPrs{index}: pullRequests(
      states: OPEN, first: {limits.page_size}, after: $previousCursor{index},
      orderBy: {{field: UPDATED_AT, direction: DESC}}
    ) {{
      totalCount
      pageInfo {{ hasNextPage endCursor }}
      nodes {{ ...StewardPrWindowRow{scope_fields}{_open_pr_readback_fields(limits)} }}
    }}"""
        for index, limits in enumerate(previous_limits)
    )
    return (
        f"""query($owner: String!, $name: String!, $cursor: String, $pageSize: Int!{variables}) {{
  repository(owner: $owner, name: $name) {{
{_GITHUB_OPEN_PR_FIELDS}{readbacks}
  }}
}}"""
        + _GITHUB_OPEN_PR_WINDOW_FRAGMENT + _GITHUB_OPEN_PR_SCOPE_FRAGMENT
        + _GITHUB_PR_REVIEW_PAGE_FRAGMENT + _GITHUB_PR_COMMENT_PAGE_FRAGMENT
        + _GITHUB_CHECK_CONTEXT_WINDOW_FRAGMENT
    )


class _OpenPrPage(NamedTuple):
    total_count: int
    rows: list[Mapping[str, Any]]
    next_cursor: str | None


def _read_open_pr_page(connection: object, page_size: int) -> _OpenPrPage | None:
    if not isinstance(connection, Mapping):
        return None
    total = connection.get("totalCount")
    rows = connection.get("nodes")
    if type(total) is not int or not 0 <= total <= _GITHUB_PR_LIST_MAX_CAPACITY:
        return None
    if not isinstance(rows, list) or len(rows) > page_size or any(
        not isinstance(row, Mapping) or type(row.get("number")) is not int or row["number"] <= 0
        for row in rows
    ):
        return None
    page_info = connection.get("pageInfo")
    has_next = page_info.get("hasNextPage") if isinstance(page_info, Mapping) else None
    if has_next is False:
        return _OpenPrPage(total, rows, None)
    if has_next is not True:
        return None
    cursor = page_info.get("endCursor")
    return _OpenPrPage(total, rows, cursor) if isinstance(cursor, str) and cursor.strip() else None


class _OpenPrWindow(NamedTuple):
    total_count: int
    next_cursor: str | None
    revisions: list[tuple[int, str, str, str | None]]


def _read_open_pr_window(page: _OpenPrPage | None) -> _OpenPrWindow | None:
    if page is None:
        return None
    revisions = []
    for row in page.rows:
        head = row.get("headRefOid")
        revision = _read_pr_signal_revision(row)
        if not isinstance(head, str) or not head.strip() or revision is None:
            return None
        revisions.append((row["number"], head, *revision))
    return _OpenPrWindow(page.total_count, page.next_cursor, revisions)


def _graphql_comment_page_info(page_info: object) -> tuple[bool, str | None]:
    """Return whether older comments may exist and a verified continuation cursor."""
    if not isinstance(page_info, Mapping):
        return True, None
    has_previous = page_info.get("hasPreviousPage")
    if has_previous is False:
        return False, None
    cursor = page_info.get("startCursor")
    if has_previous is True and isinstance(cursor, str) and cursor.strip():
        return True, cursor
    return True, None


def _read_graphql_comment_nodes(raw_nodes: object) -> list[Mapping[str, Any]] | None:
    # Structured rows can still establish the role of a reply with unreadable timing.
    if not isinstance(raw_nodes, list) or any(not isinstance(comment, Mapping) for comment in raw_nodes):
        return None
    return raw_nodes


class _CommentPage(NamedTuple):
    rows: list[Mapping[str, Any]]
    has_previous: bool
    previous_cursor: str | None


def _read_graphql_comment_page(connection: object, *, max_rows: int) -> _CommentPage | None:
    if not isinstance(connection, Mapping):
        return None
    rows = _read_graphql_comment_nodes(connection.get("nodes"))
    if rows is None or len(rows) > max_rows:
        return None
    has_previous, cursor = _graphql_comment_page_info(connection.get("pageInfo"))
    return _CommentPage(rows, has_previous, cursor)


def _comment_readback_limit(rows: Sequence[Mapping[str, Any]]) -> int:
    limit = 1
    for row in rows:
        page = _read_graphql_comment_page(row.get("comments"), max_rows=_GITHUB_PR_LATEST_COMMENT_PAGE_SIZE)
        if page is not None:
            limit = max(limit, len(page.rows))
    return limit


def _earlier_pr_comments_agree(original: Mapping[str, Any], readback: Mapping[str, Any]) -> bool:
    initial = _read_graphql_comment_page(original.get("comments"), max_rows=_GITHUB_PR_LATEST_COMMENT_PAGE_SIZE)
    terminal = _read_graphql_comment_page(readback.get("comments"), max_rows=_GITHUB_PR_LATEST_COMMENT_PAGE_SIZE)
    return initial is not None and initial == terminal


class _ReviewPage(NamedTuple):
    rows: list[Mapping[str, Any]]
    ids: set[str]
    total_count: int
    previous_cursor: str | None


def _read_graphql_review_page(connection: object) -> _ReviewPage | None:
    if not isinstance(connection, Mapping):
        return None
    rows = connection.get("nodes")
    page_info = connection.get("pageInfo")
    total_count = connection.get("totalCount")
    if (
        not isinstance(rows, list) or len(rows) > _GITHUB_PR_REVIEW_PAGE_SIZE
        or not isinstance(page_info, Mapping)
        or type(total_count) is not int
        or total_count < len(rows)
    ):
        return None
    has_previous = page_info.get("hasPreviousPage")
    cursor = page_info.get("startCursor") if has_previous is True else None
    if has_previous is not False and (
        has_previous is not True or not isinstance(cursor, str) or not cursor.strip()
        or not rows or total_count == len(rows)
    ):
        return None
    seen_ids: set[str] = set()
    for review in rows:
        if not isinstance(review, Mapping):
            return None
        raw_id = review.get("id")
        review_id = raw_id.strip() if isinstance(raw_id, str) else ""
        commit = review.get("commit")
        commit_oid = commit.get("oid") if isinstance(commit, Mapping) else None
        if (
            not review_id or review_id in seen_ids
            or not isinstance(commit_oid, str) or not commit_oid.strip()
        ):
            return None
        seen_ids.add(review_id)
    return _ReviewPage(rows, seen_ids, total_count, cursor)


def _review_readback_limit(rows: Sequence[Mapping[str, Any]]) -> int:
    return max(
        (max(1, len(page.rows)) for row in rows if (page := _read_graphql_review_page(row.get("reviews"))) is not None),
        default=1,
    )


def _earlier_pr_reviews_agree(original: Mapping[str, Any], readback: Mapping[str, Any]) -> bool:
    original_page = _read_graphql_review_page(original.get("reviews"))
    return original_page is not None and original_page == _read_graphql_review_page(readback.get("reviews"))


def _read_graphql_check_contexts(contexts: object) -> list[Mapping[str, Any]] | None:
    if not isinstance(contexts, Mapping):
        return None
    nodes = contexts.get("nodes")
    page_info = contexts.get("pageInfo")
    total_count = contexts.get("totalCount")
    if (
        not isinstance(nodes, list) or len(nodes) > _GITHUB_PR_CHECK_CONTEXT_PAGE_SIZE
        or not isinstance(page_info, Mapping) or page_info.get("hasNextPage") is not False
        or type(total_count) is not int or total_count != len(nodes)
    ):
        return None
    seen_ids: set[str] = set()
    for check in nodes:
        raw_id = check.get("id") if isinstance(check, Mapping) else None
        check_id = raw_id.strip() if isinstance(raw_id, str) else ""
        if not check_id or check_id in seen_ids:
            return None
        seen_ids.add(check_id)
    return nodes


def _read_direct_pr_checks(node: Mapping[str, Any]) -> list[Mapping[str, Any]] | None:
    # Some adapters provide check evidence directly on the PR row.
    direct = node.get("statusCheckRollup")
    return direct if isinstance(direct, list) and all(isinstance(check, Mapping) for check in direct) else None


def _read_graphql_head_check_rollup(
    verified_head_commit: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]] | None:
    if verified_head_commit is None or "statusCheckRollup" not in verified_head_commit:
        return None
    rollup = verified_head_commit["statusCheckRollup"]
    if rollup is None:
        return []
    return _read_graphql_check_contexts(rollup.get("contexts")) if isinstance(rollup, Mapping) else None


class _PrCommitBinding(NamedTuple):
    head_oid: str
    first_commit: Mapping[str, Any] | None
    commit_oid: str
    verified: Mapping[str, Any] | None


def _graphql_commit_binding(node: Mapping[str, Any]) -> _PrCommitBinding:
    raw_commits = node.get("commits")
    commit_nodes = raw_commits.get("nodes") if isinstance(raw_commits, Mapping) else None
    raw_commit = (
        commit_nodes[0].get("commit")
        if isinstance(commit_nodes, list) and commit_nodes and isinstance(commit_nodes[0], Mapping)
        else None
    )
    first_commit = raw_commit if isinstance(raw_commit, Mapping) else None
    raw_head_oid = node.get("headRefOid")
    head_oid = raw_head_oid.strip() if isinstance(raw_head_oid, str) else ""
    raw_commit_oid = first_commit.get("oid") if first_commit is not None else None
    commit_oid = raw_commit_oid.strip() if isinstance(raw_commit_oid, str) else ""
    verified = first_commit if head_oid and commit_oid.casefold() == head_oid.casefold() else None
    return _PrCommitBinding(head_oid, first_commit, commit_oid, verified)


def _nested_check_readback_limit(rows: Sequence[Mapping[str, Any]]) -> int | None:
    # A longer window cannot corroborate its earlier tally, so reread only the observed size.
    known_sizes = []
    for row in rows:
        if _read_direct_pr_checks(row) is None:
            checks = _read_graphql_head_check_rollup(_graphql_commit_binding(row).verified)
            if checks is not None:
                known_sizes.append(max(1, len(checks)))
    return max(known_sizes, default=None)


def _earlier_pr_checks_agree(original: Mapping[str, Any], readback: Mapping[str, Any]) -> bool:
    if _read_direct_pr_checks(original) is not None:
        return True
    original_checks = _read_graphql_head_check_rollup(_graphql_commit_binding(original).verified)
    current_checks = _read_graphql_head_check_rollup(_graphql_commit_binding(readback).verified)
    if original_checks is None or current_checks is None:
        return False
    original_counts, original_complete = _tally_checks(original_checks)
    current_counts, current_complete = _tally_checks(current_checks)
    return original_complete and current_complete and original_counts == current_counts


def _normalize_graphql_pr_node(node: Mapping[str, Any]) -> dict[str, Any]:
    raw_reviews = node.get("reviews")
    review_page = _read_graphql_review_page(raw_reviews)
    raw_review_nodes = raw_reviews.get("nodes") if isinstance(raw_reviews, Mapping) else raw_reviews
    reviews = [row for row in raw_review_nodes if isinstance(row, Mapping)] if isinstance(raw_review_nodes, list) else []
    review_coverage_complete = (
        review_page is not None and review_page.previous_cursor is None
        and len(review_page.rows) == review_page.total_count
    )

    comment_page = _read_graphql_comment_page(node.get("comments"), max_rows=_GITHUB_PR_LATEST_COMMENT_PAGE_SIZE)
    comments, comments_has_previous_page, comments_start_cursor = comment_page or _CommentPage([], True, None)

    head_oid, first_commit, commit_oid, verified_head_commit = _graphql_commit_binding(node)
    head_clocks: dict[str, Any] = {}
    unbound_clocks: list[tuple[datetime, str]] = []
    for row_key, commit_key in (("headCommitPushedAt", "pushedDate"), ("headCommitCommittedAt", "committedDate")):
        head_clocks[row_key] = node.get(row_key)
        if head_clocks[row_key] is not None or not isinstance(first_commit, Mapping):
            continue
        if verified_head_commit is not None:
            head_clocks[row_key] = verified_head_commit.get(commit_key)
        elif not commit_oid:
            unbound_clock = _parse_iso_timestamp(first_commit.get(commit_key))
            if unbound_clock is not None:
                unbound_clocks.append(unbound_clock)
    latest_unbound = max(unbound_clocks, key=lambda clock: clock[0], default=None)
    rollup = _read_direct_pr_checks(node)
    if rollup is None:
        rollup = _read_graphql_head_check_rollup(verified_head_commit)
    merge_state = node.get("mergeStateStatus")

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
        "mergeStateStatus": merge_state,
        "merge_state_coverage_incomplete": (
            not isinstance(merge_state, str) or merge_state.strip().upper() not in _KNOWN_MERGE_STATES
        ),
        "mergeable": node.get("mergeable"),
        "updatedAt": node.get("updatedAt"),
        **head_clocks,
        "_unbound_head_commit_at": latest_unbound[1] if latest_unbound is not None else None,
        "comments": comments,
        "commentsHasPreviousPage": comments_has_previous_page,
        "commentsStartCursor": comments_start_cursor,
        "reviews": reviews,
        "review_coverage_incomplete": not review_coverage_complete or not head_oid,
        "statusCheckRollup": rollup,
        "checks_coverage_incomplete": rollup is None,
    }


def _read_older_pr_page(
    number: int,
    head_oid: str,
    query: str,
    cursor: str,
    remaining_seconds: float,
    additional_variables: Mapping[str, str],
    *,
    owner: str,
    name: str,
    runner: Callable[..., Any],
    deadline: float,
) -> Mapping[str, Any] | None:
    argv = [
        "gh", "api", "graphql",
        "-f", f"query={query}", "-f", f"owner={owner}", "-f", f"name={name}",
        "-F", f"number={number}", "-f", f"before={cursor}",
    ]
    for variable, value in additional_variables.items():
        argv.extend(("-f", f"{variable}={value}"))
    try:
        completed = runner(
            argv,
            capture_output=True, text=True, check=False,
            timeout=min(_GITHUB_PR_QUERY_TIMEOUT_SECONDS, remaining_seconds),
        )
        if monotonic() > deadline or completed.returncode != 0:
            return None
        payload = json.loads(completed.stdout or "{}")
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None
    if not isinstance(payload, Mapping) or payload.get("errors"):
        return None
    data = payload.get("data")
    repo = data.get("repository") if isinstance(data, Mapping) else None
    pr_data = repo.get("pullRequest") if isinstance(repo, Mapping) else None
    if not isinstance(pr_data, Mapping):
        return None
    returned_number = pr_data.get("number")
    if type(returned_number) is not int or returned_number != number or pr_data.get("headRefOid") != head_oid:
        return None
    return pr_data


_ReadOlderPage = Callable[[int, str, str, str, float, Mapping[str, str]], Mapping[str, Any] | None]


def _read_pr_signal_revision(row: Mapping[str, Any]) -> tuple[str, str | None] | None:
    """Read the observable PR revision fields used during supplemental reads."""
    updated_at = row.get("updatedAt")
    timestamp = _parse_iso_timestamp(updated_at) if isinstance(updated_at, str) else None
    decision = row.get("reviewDecision")
    if timestamp is None or "reviewDecision" not in row:
        return None
    if decision is not None and (not isinstance(decision, str) or not decision.strip()):
        return None
    return timestamp[1], decision


def _backfill_pr_reviews(
    number: int,
    head_oid: str,
    first_page: _ReviewPage,
    *,
    revision: tuple[str, str | None],
    remaining_pages: int,
    deadline: float,
    read_older_page: _ReadOlderPage,
) -> tuple[list[Mapping[str, Any]] | None, int]:
    if first_page.total_count > len(first_page.rows) + remaining_pages * _GITHUB_PR_REVIEW_PAGE_SIZE:
        return None, remaining_pages
    rows = first_page.rows
    ids = set(first_page.ids)
    cursor = first_page.previous_cursor
    consumed_pages: dict[str, _ReviewPage] = {}
    for _ in range(remaining_pages):
        remaining_seconds = deadline - monotonic()
        if cursor is None or cursor in consumed_pages or remaining_seconds <= 0:
            break
        readbacks = {f"previousReviews{index}": previous for index, previous in enumerate(consumed_pages)}
        remaining_pages -= 1
        pr_data = read_older_page(
            number, head_oid, _older_reviews_page_query(readbacks), cursor, remaining_seconds, readbacks,
        )
        if pr_data is None or _read_pr_signal_revision(pr_data) != revision:
            break
        if _read_graphql_review_page(pr_data.get("latestReviews")) != first_page:
            break
        if any(
            _read_graphql_review_page(pr_data.get(alias)) != consumed_pages[previous]
            for alias, previous in readbacks.items()
        ):
            break
        page = _read_graphql_review_page(pr_data.get("reviews"))
        if page is None or not page.rows or page.total_count != first_page.total_count or ids & page.ids:
            break
        consumed_pages[cursor] = page
        ids.update(page.ids)
        rows = [*page.rows, *rows]
        cursor = page.previous_cursor
        if cursor is None:
            return (rows if len(ids) == first_page.total_count else None), remaining_pages
        if len(ids) >= first_page.total_count:
            break
    return None, remaining_pages


def _backfill_pr_comments_if_needed(
    norm_node: dict[str, Any],
    summary: dict[str, Any],
    *,
    number: int,
    revision: tuple[str, str | None] | None,
    remaining_pages: int,
    deadline: float,
    read_older_page: _ReadOlderPage,
) -> tuple[dict[str, Any], int]:
    """Backfill an incomplete PR and return the remaining allowance."""
    head_oid = norm_node.get("headRefOid")
    if not isinstance(head_oid, str) or not head_oid.strip() or revision is None:
        return summary, remaining_pages
    first_page = _CommentPage(norm_node["comments"], norm_node["commentsHasPreviousPage"], norm_node["commentsStartCursor"])
    consumed_pages: dict[str, _CommentPage] = {}
    for _ in range(remaining_pages):
        if summary.get("comments_coverage_incomplete") is not True:
            break
        comment_cursor = norm_node.get("commentsStartCursor")
        remaining_seconds = deadline - monotonic()
        if not comment_cursor or remaining_seconds <= 0:
            break
        readbacks = {f"previousComments{index}": cursor for index, cursor in enumerate(consumed_pages)}
        remaining_pages -= 1
        pr_data = read_older_page(
            number, head_oid, _older_comments_page_query(readbacks), comment_cursor, remaining_seconds, readbacks,
        )
        if pr_data is None or _read_pr_signal_revision(pr_data) != revision:
            break
        if _read_graphql_comment_page(pr_data.get("latestComments"), max_rows=_GITHUB_PR_LATEST_COMMENT_PAGE_SIZE) != first_page:
            break
        if any(
            _read_graphql_comment_page(pr_data.get(alias), max_rows=_GITHUB_PR_OLDER_COMMENT_PAGE_SIZE)
            != consumed_pages[cursor]
            for alias, cursor in readbacks.items()
        ):
            break
        older_page = _read_graphql_comment_page(pr_data.get("comments"), max_rows=_GITHUB_PR_OLDER_COMMENT_PAGE_SIZE)
        if older_page is None:
            break
        consumed_pages[comment_cursor] = older_page
        next_cursor = older_page.previous_cursor
        if next_cursor in consumed_pages:
            # A cycle leaves older coverage unverified.
            next_cursor = None
        norm_node["comments"] = [*older_page.rows, *norm_node["comments"]]
        norm_node["commentsHasPreviousPage"] = older_page.has_previous
        norm_node["commentsStartCursor"] = next_cursor
        summary = summarize_github_pr_signal(norm_node)
        if next_cursor is None:
            break
    return summary, remaining_pages


def observe_github_repository_prs(
    repo_slug: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
    backfill_comments: bool = False,
    select_comment_backfill: Callable[[Sequence[Mapping[str, object]], float], set[int]] | None = None,
    select_review_backfill: Callable[[Sequence[Mapping[str, object]], float], set[int]] | None = None,
) -> dict[str, Any]:
    """Read bounded public/authorized open PR signal metadata without side effects."""
    normalized = normalize_github_repo_slug(repo_slug)
    if not normalized or "/" not in normalized:
        return {"status": "unavailable", "repository": repo_slug}
    owner, name = normalized.split("/", 1)
    raw_nodes_by_number: dict[int, Mapping[str, Any]] = {}
    expected_total: int | None = None
    cursor: str | None = None
    previous_pages: dict[str | None, tuple[_OpenPrWindow, _OpenPrReadbackLimits]] = {}
    unverified_checks: set[int] = set()
    unverified_reviews: set[int] = set()
    unverified_comments: set[int] = set()
    page_size = _GITHUB_PR_LIST_PAGE_SIZE
    deadline = monotonic() + _GITHUB_PR_SCAN_TIMEOUT_SECONDS
    try:
        for _ in range(_GITHUB_PR_LIST_MAX_PAGES):
            can_finish = expected_total is not None and len(raw_nodes_by_number) + page_size >= expected_total
            previous_limits = [
                limits if can_finish else _OpenPrReadbackLimits(limits.page_size, None, None, None)
                for _window, limits in previous_pages.values()
            ]
            cmd = [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={_open_prs_graphql_query(previous_limits, terminal_scope=can_finish)}",
                "-f",
                f"owner={owner}",
                "-f",
                f"name={name}",
                "-F",
                f"pageSize={page_size}",
            ]
            if cursor is not None:
                cmd.extend(["-f", f"cursor={cursor}"])
            for index, previous_cursor in enumerate(previous_pages):
                if previous_cursor is not None:
                    cmd.extend(["-f", f"previousCursor{index}={previous_cursor}"])
            remaining_seconds = deadline - monotonic()
            if remaining_seconds <= 0:
                return {"status": "unavailable", "repository": repo_slug}
            completed = runner(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=min(_GITHUB_PR_QUERY_TIMEOUT_SECONDS, remaining_seconds),
            )
            if monotonic() > deadline or completed.returncode != 0:
                return {"status": "unavailable", "repository": repo_slug}
            payload = json.loads(completed.stdout or "{}")
            if not isinstance(payload, Mapping) or payload.get("errors"):
                return {"status": "unavailable", "repository": repo_slug}
            data = payload.get("data")
            repo = data.get("repository") if isinstance(data, Mapping) else None
            if not isinstance(repo, Mapping):
                return {"status": "unavailable", "repository": repo_slug}
            page = _read_open_pr_page(repo.get("pullRequests"), page_size)
            if page is None or (expected_total is not None and page.total_count != expected_total):
                return {"status": "unavailable", "repository": repo_slug}
            earlier_rows = {}
            for index, (expected_window, limits) in enumerate(previous_pages.values()):
                readback = _read_open_pr_page(repo.get(f"previousPrs{index}"), limits.page_size)
                if readback is None or _read_open_pr_window(readback) != expected_window:
                    return {"status": "unavailable", "repository": repo_slug}
                if page.next_cursor is None:
                    earlier_rows.update((row["number"], row) for row in readback.rows)
            expected_total = page.total_count
            if page.next_cursor is None:
                fresh_numbers = {row["number"] for row in page.rows}
                for number, original in raw_nodes_by_number.items():
                    if number in fresh_numbers:
                        continue
                    readback_row = earlier_rows.get(number, {})
                    if not _earlier_pr_checks_agree(original, readback_row):
                        unverified_checks.add(number)
                    if not _earlier_pr_reviews_agree(original, readback_row):
                        unverified_reviews.add(number)
                    if not _earlier_pr_comments_agree(original, readback_row):
                        unverified_comments.add(number)
                    author = readback_row.get("author")
                    raw_nodes_by_number[number] = {
                        **original,
                        "isDraft": readback_row.get("isDraft"),
                        "mergeable": readback_row.get("mergeable"),
                        "mergeStateStatus": readback_row.get("mergeStateStatus"),
                        "author": author if isinstance(author, Mapping) else None,
                    }
            raw_nodes_by_number.update((row["number"], row) for row in page.rows)
            if page.next_cursor is None:
                break
            window = _read_open_pr_window(page)
            if window is None or page.next_cursor == cursor or page.next_cursor in previous_pages:
                return {"status": "unavailable", "repository": repo_slug}
            limits = _OpenPrReadbackLimits(
                page_size, _nested_check_readback_limit(page.rows), _review_readback_limit(page.rows),
                _comment_readback_limit(page.rows),
            )
            previous_pages[cursor] = (window, limits)
            cursor = page.next_cursor
            if expected_total > _GITHUB_PR_LIST_STANDARD_CAPACITY:
                page_size = _GITHUB_PR_LIST_OVERFLOW_PAGE_SIZE
        else:
            # A partial list can make the monitor report PRs as removed.
            return {"status": "unavailable", "repository": repo_slug}
        if len(raw_nodes_by_number) != expected_total:
            return {"status": "unavailable", "repository": repo_slug}
        normalized_nodes = {
            number: _normalize_graphql_pr_node(raw_nodes_by_number[number])
            for number in sorted(raw_nodes_by_number)
        }
        for number in unverified_checks:
            normalized_nodes[number]["checks_coverage_incomplete"] = True
            normalized_nodes[number]["statusCheckRollup"] = None
        for number in unverified_reviews:
            normalized_nodes[number]["review_readback_incomplete"] = True
            normalized_nodes[number]["review_coverage_incomplete"] = True
        for number in unverified_comments:
            normalized_nodes[number]["_comment_readback_incomplete"] = True
        items = {number: summarize_github_pr_signal(node) for number, node in normalized_nodes.items()}
        remaining_backfill_pages = _GITHUB_PR_BACKFILL_BUDGET
        review_pages_read: dict[int, int] = {}
        read_older_page = partial(
            _read_older_pr_page, owner=owner, name=name, runner=runner, deadline=deadline,
        )
        if select_review_backfill is not None:
            review_candidates = {}
            for number, node in normalized_nodes.items():
                if number in unverified_reviews:
                    continue
                if not node["review_coverage_incomplete"] or not isinstance(node["headRefOid"], str) or not node["headRefOid"]:
                    continue
                raw_node = raw_nodes_by_number[number]
                first_page = _read_graphql_review_page(raw_node.get("reviews"))
                revision = _read_pr_signal_revision(raw_node)
                if first_page is not None and first_page.previous_cursor is not None and revision is not None:
                    review_candidates[number] = (first_page, revision)
            remaining_seconds = deadline - monotonic()
            if review_candidates and remaining_seconds > 0:
                selected = select_review_backfill([items[number] for number in review_candidates], remaining_seconds)
                for number, (first_page, revision) in review_candidates.items():
                    if number not in selected or remaining_backfill_pages <= 0 or monotonic() >= deadline:
                        continue
                    limit = min(_GITHUB_PR_MAX_BACKFILL_PAGES, remaining_backfill_pages)
                    node = normalized_nodes[number]
                    reviews, remaining = _backfill_pr_reviews(
                        number, node["headRefOid"], first_page, remaining_pages=limit,
                        revision=revision, deadline=deadline, read_older_page=read_older_page,
                    )
                    review_pages_read[number] = limit - remaining
                    remaining_backfill_pages -= review_pages_read[number]
                    if reviews is not None:
                        node["reviews"] = reviews
                        node["review_coverage_incomplete"] = False
                        items[number] = summarize_github_pr_signal(node)
        if backfill_comments:
            candidates = [
                number for number, item in items.items()
                if number not in unverified_comments
                and item["comments_coverage_incomplete"] and not item.get("review_coverage_incomplete")
            ]
            remaining_seconds = deadline - monotonic()
            if select_comment_backfill is not None and candidates and remaining_seconds > 0:
                selected = select_comment_backfill([items[number] for number in candidates], remaining_seconds)
                candidates = [number for number in candidates if number in selected]
            for number in candidates:
                if remaining_backfill_pages <= 0 or monotonic() >= deadline:
                    break
                limit = min(
                    remaining_backfill_pages, _GITHUB_PR_MAX_BACKFILL_PAGES - review_pages_read.get(number, 0),
                )
                items[number], remaining = _backfill_pr_comments_if_needed(
                    normalized_nodes[number],
                    items[number],
                    number=number,
                    revision=_read_pr_signal_revision(raw_nodes_by_number[number]),
                    remaining_pages=limit,
                    deadline=deadline,
                    read_older_page=read_older_page,
                )
                remaining_backfill_pages -= limit - remaining
        return {"status": "ok", "repository": repo_slug, "items": list(items.values())}
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
