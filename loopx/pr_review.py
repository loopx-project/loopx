from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capabilities.pr_review_queue import (
    DEFAULT_REVIEW_PRIORITY,
    PullRequestReviewPriority,
    PullRequestSchedulingLane,
    build_agent_response_contract,
    build_scheduling_policy,
    classify_scheduling_lane,
    community_feedback_ready,
    exact_head_key,
    materialize_review_execution,
    normalize_fresh_audit_exact_heads,
    normalize_review_priority,
    scheduling_sort_key,
    scheduling_tier,
)
from .capabilities.pr_review_queue.github_source import (
    attach_pr_review_details as _attach_pr_review_details,
)
from .capabilities.pr_review_queue.github_source import run_gh_json as _run_gh_json
from .capabilities.pr_review_queue.github_source import (
    attach_pr_review_details_concurrently as _attach_pr_review_details_concurrently,
)
from .capabilities.pr_review_queue.check_attempts import latest_check_attempts
from .control_plane.runtime.time import now_utc_iso
from .presentation.markdown import as_dict as _as_dict
from .presentation.markdown import as_list as _as_list
from .presentation.public_safety import public_safe_boundary, redact_public_text

COMMAND = "/loopx-pr-review"
SCHEMA_VERSION = "loopx_pr_review_command_response_v0"

BOUNDARY = public_safe_boundary()

SOURCE_SURFACES = [
    "GitHub pull request metadata",
    "GitHub pull request body summary",
    "GitHub pull request changed-file list",
    "GitHub pull request status check rollup",
]

REQUIRED_REVIEW_SECTION_HEADINGS = (
    "动机",
    "改动思路",
    "具体改动",
    "对主干的风险",
    "我的整体评价",
)
AUTHOR_OWNED_APPROVAL_FALLBACK_TITLE = (
    "Approval conclusion (author-owned PR; GitHub blocks formal self-approval)"
)
AUTHOR_OWNED_REQUEST_CHANGES_FALLBACK_TITLE = (
    "Request changes conclusion (author-owned PR; GitHub blocks formal self-review)"
)
REVIEW_CONCLUSION_SCHEMA_VERSION = "pull_request_review_conclusion_v0"
RUNTIME_OR_CLI_PREFIXES = (
    "src/",
    "lib/",
    "pkg/",
    "packages/",
    "cmd/",
    "internal/",
    "server/",
    "backend/",
    "app/",
    "apps/",
    "scripts/",
    "bin/",
    "tools/",
)

CODE_EXTENSIONS = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".m",
    ".mm",
)

UI_PREFIXES = (
    "apps/presentation/dashboard/",
    "apps/web/",
    "apps/frontend/",
    "apps/site/",
    "web/",
    "frontend/",
    "ui/",
    "components/",
    "pages/",
    "views/",
    "public/",
)


def _now_iso() -> str:
    return now_utc_iso()


def _redact_text(value: object, *, limit: int = 320) -> str:
    return redact_public_text(value, limit=limit)


def _join_short(items: list[str], *, limit: int = 3, fallback: str = "未提供") -> str:
    compact = [str(item).strip() for item in items if str(item).strip()]
    if not compact:
        return fallback
    return "、".join(compact[:limit])


def resolve_current_github_repository(*, cwd: Path | None = None) -> str | None:
    try:
        payload = _run_gh_json(["repo", "view", "--json", "nameWithOwner"], cwd=cwd)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return str(payload.get("nameWithOwner") or "") or None



def resolve_current_github_login(*, cwd: Path | None = None) -> str | None:
    try:
        payload = _run_gh_json(["api", "user"], cwd=cwd)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return str(payload.get("login") or "").strip() or None


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _github_search_date(value: object) -> str | None:
    parsed = _parse_timestamp(value)
    if parsed:
        return parsed.astimezone(timezone.utc).date().isoformat()
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return None


def _pr_window_timestamp(pr: dict[str, Any]) -> datetime | None:
    return (
        _parse_timestamp(pr.get("mergedAt") or pr.get("merged_at"))
        or _parse_timestamp(pr.get("closedAt") or pr.get("closed_at"))
        or _parse_timestamp(pr.get("updatedAt") or pr.get("updated_at"))
    )


def _include_pr_in_window(pr: dict[str, Any], *, since: object | None) -> bool:
    since_dt = _parse_timestamp(since)
    if since_dt is None:
        return True
    activity_dt = _pr_window_timestamp(pr)
    return bool(activity_dt and activity_dt >= since_dt)


def normalize_pr_state_filter(value: object) -> str:
    state = str(value or "all").strip().lower()
    return state if state in {"open", "merged", "all"} else "all"


def fetch_github_pull_requests(
    *,
    repo: str | None,
    limit: int,
    cwd: Path | None = None,
    state_filter: str = "all",
    since: str | None = None,
) -> list[dict[str, Any]]:
    scan = scan_github_pull_requests(
        repo=repo,
        limit=limit,
        cwd=cwd,
        state_filter=state_filter,
        since=since,
    )
    return list(scan["pull_requests"])


def scan_github_pull_requests(
    *,
    repo: str | None,
    limit: int,
    cwd: Path | None = None,
    state_filter: str = "all",
    since: str | None = None,
    wait_for_ci: bool = True,
) -> dict[str, Any]:
    repo_args = ["--repo", repo] if repo else []
    api_repository = repo or resolve_current_github_repository(cwd=cwd)
    normalized_state = normalize_pr_state_filter(state_filter)
    search_args: list[str] = []
    search_date = _github_search_date(since)
    if search_date:
        search_args = ["--search", f"updated:>={search_date}"]
    list_fields = [
        "number",
        "title",
        "url",
        "state",
        "isDraft",
        "headRefName",
        "headRefOid",
        "baseRefName",
        "author",
        "createdAt",
        "updatedAt",
        "closedAt",
        "mergedAt",
        "mergeCommit",
        "changedFiles",
        "additions",
        "deletions",
    ]
    fetch_limit = max(1, limit)
    if since:
        fetch_limit = max(fetch_limit, min(100, fetch_limit * 3))

    detailed: list[dict[str, Any]] = []
    seen_numbers: set[str] = set()
    state_scans: list[dict[str, Any]] = []

    def append_state(state: str) -> None:
        rows = _run_gh_json(
            [
                "pr",
                "list",
                "--state",
                state,
                "--limit",
                str(fetch_limit),
                "--json",
                ",".join(list_fields),
                *search_args,
                *repo_args,
            ],
            cwd=cwd,
        )
        if not isinstance(rows, list):
            state_scans.append(
                {
                    "state": state,
                    "fetch_limit": fetch_limit,
                    "fetched_count": 0,
                    "included_after_window": 0,
                    "detail_read_failures": 0,
                    "source_saturated": False,
                    "source_read_valid": False,
                }
            )
            return
        included_before = len(detailed)
        candidates: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not _include_pr_in_window(row, since=since):
                continue
            number = str(row.get("number") or row.get("url") or "")
            if number and number in seen_numbers:
                continue
            if number:
                seen_numbers.add(number)
            candidates.append(row)
        detail_results = _attach_pr_review_details_concurrently(
            candidates,
            repository=api_repository,
            cwd=cwd,
            attach=_attach_pr_review_details,
            **({"wait_for_ci": False} if not wait_for_ci else {}),
            run_gh_json=_run_gh_json,
        )
        detail_read_failures = sum(not result for result in detail_results)
        detailed.extend(candidates)
        state_scans.append(
            {
                "state": state,
                "fetch_limit": fetch_limit,
                "fetched_count": len(rows),
                "included_after_window": len(detailed) - included_before,
                "detail_read_failures": detail_read_failures,
                "source_saturated": len(rows) >= fetch_limit,
                "source_read_valid": detail_read_failures == 0,
            }
        )

    if normalized_state == "all":
        append_state("open")
        append_state("closed")
    else:
        append_state(normalized_state)
    return {
        "schema_version": "pr_review_source_scan_v0",
        "complete": all(
            item["source_read_valid"] is True
            and item["source_saturated"] is False
            for item in state_scans
        ),
        "pull_requests": detailed,
        "states": state_scans,
    }


def load_pr_fixture(
    path: Path,
) -> tuple[str | None, list[dict[str, Any]], str | None]:
    """Load an offline PR window, including the reviewer identity it declares.

    Author-owned conclusions depend on the reviewer identity: GitHub records an
    author-owned approval as `COMMENTED`, so offline use must be able to name
    the authenticated reviewer or it cannot represent that real case.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return None, [item for item in payload if isinstance(item, dict)], None
    if not isinstance(payload, dict):
        return None, [], None
    items = payload.get("pull_requests") or payload.get("prs") or []
    return (
        str(payload.get("repository") or "") or None,
        [item for item in _as_list(items) if isinstance(item, dict)],
        str(payload.get("reviewer_login") or "") or None,
    )


def _clean_body_lines(body: object) -> list[str]:
    lines: list[str] = []
    for raw in str(body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("<!--") or line.startswith("-->"):
            continue
        if line.startswith("#"):
            continue
        if line.startswith("- [") or line.startswith("* ["):
            continue
        if line.lower() in {"summary", "motivation", "changes", "testing"}:
            continue
        lines.append(line.strip("-* "))
    return lines


def _motivation(pr: dict[str, Any]) -> str:
    lines = _clean_body_lines(pr.get("body"))
    if lines:
        return _redact_text(" ".join(lines[:2]), limit=380)
    title = pr.get("title") or f"PR #{pr.get('number')}"
    return _redact_text(f"Review the intent described by the title: {title}", limit=220)


def _commit_headlines(pr: dict[str, Any], *, limit: int = 5) -> list[str]:
    commits: list[str] = []
    for item in _as_list(pr.get("commits")):
        if not isinstance(item, dict):
            continue
        headline = item.get("messageHeadline") or _as_dict(item.get("commit")).get("messageHeadline")
        if headline:
            commits.append(_redact_text(headline, limit=140))
        if len(commits) >= limit:
            break
    return commits


def _file_area(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    lowered = path.lower()
    if (
        path.startswith(("skills/", ".codex/", ".agents/"))
        or name in {"AGENTS.md", "SKILL.md", "CLAUDE.md"}
        or lowered.endswith((".prompt.md", ".instructions.md"))
    ):
        return "agent_instruction_surface"
    if path in {
        "README.md",
        "README.zh-CN.md",
        "AGENTS.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "LICENSE",
        "CODE_OF_CONDUCT.md",
        ".github/SECURITY.md",
    }:
        return "public_entry_or_policy"
    if path.startswith(".github/workflows/"):
        return "ci_or_release"
    if path.startswith("docs/"):
        return "public_docs"
    if path.startswith(("examples/", "fixtures/", "test/", "tests/", "spec/", "smoke/")):
        return "test_or_example"
    if path.startswith(UI_PREFIXES):
        return "app_or_ui_surface"
    if path.startswith(RUNTIME_OR_CLI_PREFIXES):
        return "product_runtime"
    if lowered.endswith(CODE_EXTENSIONS):
        return "product_runtime"
    if path.endswith((".toml", ".yaml", ".yml", ".json", ".ini")) or name in {
        "package.json",
        "pyproject.toml",
        "Cargo.toml",
        "go.mod",
        "Makefile",
    }:
        return "build_or_config"
    if lowered.endswith((".md", ".mdx", ".rst")):
        return "public_docs"
    return "other"


def _files(pr: dict[str, Any]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for item in _as_list(pr.get("files")):
        if isinstance(item, str):
            path = item
            additions = None
            deletions = None
        elif isinstance(item, dict):
            path = str(item.get("path") or item.get("filename") or "")
            additions = item.get("additions")
            deletions = item.get("deletions")
        else:
            continue
        if not path:
            continue
        files.append(
            {
                "path": _redact_text(path, limit=180),
                "area": _file_area(path),
                "additions": additions,
                "deletions": deletions,
            }
        )
    return files


def _area_counts(files: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in files:
        area = str(item.get("area") or "other")
        counts[area] = counts.get(area, 0) + 1
    return counts


AREA_LABELS = {
    "public_entry_or_policy": "README/政策入口",
    "agent_instruction_surface": "Agent 指令/Skill",
    "public_docs": "公开文档",
    "test_or_example": "smoke/示例",
    "app_or_ui_surface": "前端/展示面",
    "product_runtime": "运行时/CLI",
    "ci_or_release": "CI/发布",
    "build_or_config": "构建配置",
    "other": "其他文件",
}


RISK_LEVEL_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "unknown": "未知",
}


def _area_phrase(areas: dict[str, Any]) -> str:
    parts: list[str] = []
    for area, count in sorted(areas.items(), key=lambda item: (-int(item[1] or 0), str(item[0]))):
        label = AREA_LABELS.get(str(area), str(area))
        parts.append(f"{label} {count}")
    return _join_short(parts, limit=4, fallback="未知区域")


def _top_file_phrase(files: list[dict[str, Any]], *, limit: int = 3) -> str:
    return _join_short([str(item.get("path") or "") for item in files if item.get("path")], limit=limit, fallback="未返回关键文件")


def _int_or_zero(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _check_brief_phrase(checks: dict[str, Any]) -> str:
    counts = _as_dict(checks.get("counts"))
    if checks.get("failures"):
        return f"{len(_as_list(checks.get('failures')))} fail"
    if checks.get("pending"):
        return f"{len(_as_list(checks.get('pending')))} pending"
    if counts.get("success"):
        return f"{counts.get('success')} pass"
    if int(checks.get("total") or 0) == 0:
        return "无 checks"
    return str(checks.get("summary") or "unknown")


def _metadata_risk_hint(pr: dict[str, Any], files: list[dict[str, Any]], checks: dict[str, Any], *, wait_for_ci: bool = True) -> dict[str, Any]:
    areas = _area_counts(files)
    changed = int(pr.get("changedFiles") or len(files) or 0)
    additions = int(pr.get("additions") or 0)
    deletions = int(pr.get("deletions") or 0)
    has_runtime = any(
        str(item.get("area"))
        in {
            "product_runtime",
            "app_or_ui_surface",
            "ci_or_release",
            "build_or_config",
            "agent_instruction_surface",
        }
        for item in files
    )
    if (wait_for_ci and checks.get("failures")) or changed >= 12 or additions + deletions >= 800:
        level = "high"
    elif has_runtime or (wait_for_ci and (checks.get("pending") or not checks.get("total"))):
        level = "medium"
    else:
        level = "low"
    return {
        "schema_version": "pr_metadata_risk_hint_v0",
        "level": level,
        "basis": [
            f"areas={_area_phrase(areas)}",
            f"scale={changed} files +{additions}/-{deletions}",
            f"checks={_check_brief_phrase(checks)}" if wait_for_ci else "CI not consulted",
        ],
        "disclaimer": "Metadata-only hint for queue ordering; agentloop must read the PR diff before judging main risk.",
    }


def _main_regression_analysis(pr: dict[str, Any], files: list[dict[str, Any]], *, wait_for_ci: bool = True) -> dict[str, Any]:
    areas = _area_counts(files)
    checks = _checks(pr)
    state = str(pr.get("state") or "").upper()
    changed = int(pr.get("changedFiles") or len(files) or 0)
    additions = int(pr.get("additions") or 0)
    deletions = int(pr.get("deletions") or 0)
    churn = additions + deletions
    area_names = {str(item.get("area") or "other") for item in files}

    potential_regressions: list[str] = []
    bug_risks: list[str] = []
    verification_focus: list[str] = []

    if "product_runtime" in area_names:
        potential_regressions.append(
            "Runtime or CLI behavior can regress: command output, data parsing, scheduling/routing, or public API compatibility may change on main."
        )
        bug_risks.append("A small runtime mismatch can break automation, hide actionable work, or make existing scripts parse stale fields.")
        verification_focus.append("Run the affected CLI/runtime smoke and inspect one representative JSON or markdown packet.")
    if "app_or_ui_surface" in area_names:
        potential_regressions.append(
            "User-facing surfaces can regress: navigation, first-screen hierarchy, responsive layout, or hover/review affordances may change."
        )
        bug_risks.append("Fixture data can pass while the browser view hides important review context or renders controls poorly.")
        verification_focus.append("Run the affected UI/frontstage smoke or capture the route in a browser when presentation changes.")
    if "ci_or_release" in area_names or "build_or_config" in area_names:
        potential_regressions.append(
            "Build, install, or CI behavior can regress: workflow triggers, dependency resolution, or required checks may change for later PRs."
        )
        bug_risks.append("A config-only diff can block future validation even when product code is unchanged.")
        verification_focus.append("Run `git diff --check` and the narrow build/check command that consumes the touched config.")
    if "public_entry_or_policy" in area_names:
        potential_regressions.append(
            "Public entry or policy wording can regress: first-screen promises, maintainer rules, or contributor expectations may drift."
        )
        bug_risks.append("A prominent docs change can authorize behavior that the runtime or repository policy does not actually support.")
        verification_focus.append("Compare the public entry text with current CLI help, AGENTS policy, and any first-screen review gate.")
    if "agent_instruction_surface" in area_names:
        potential_regressions.append(
            "Automatically loaded agent instructions can change ordinary-user behavior before any runtime feature gate is evaluated."
        )
        bug_risks.append(
            "A skill or prompt can make an opt-in capability effectively default-on for agents even when runtime configuration remains false."
        )
        verification_focus.append(
            "Trace installation and automatic-loading paths, then compare pre-change, disabled, and enabled instruction surfaces for user-experience parity."
        )
    if areas and area_names <= {"public_docs", "test_or_example"}:
        potential_regressions.append(
            "Runtime regression risk is low, but public guidance or smoke expectations can drift from shipped behavior."
        )
        bug_risks.append("Docs-only or smoke-only changes can bless stale contracts if examples no longer match the real command path.")
        verification_focus.append("Run `git diff --check` and the touched smoke; compare command examples with current CLI help when syntax is involved.")
    if not potential_regressions:
        potential_regressions.append(
            "Regression shape is unclear from metadata alone; inspect the highest-churn files before approving."
        )
        bug_risks.append("Unclassified files may still affect generated assets, packaging, or reviewer workflow assumptions.")
        verification_focus.append("Review the diff for the top changed files and run the nearest project smoke.")

    if wait_for_ci:
        if checks.get("failures"):
            bug_risks.insert(0, "Failing status checks indicate the branch may already break a required validation surface.")
            verification_focus.insert(0, "Inspect failing checks before merge and rerun them after fixes.")
        elif checks.get("pending"):
            bug_risks.append("Pending checks leave merge readiness uncertain.")
            verification_focus.append("Wait for pending checks or run the equivalent local smoke before merge.")
        elif not checks.get("total"):
            bug_risks.append("No status-check rollup was available, so validation coverage must be inferred from local evidence.")
            verification_focus.append("Run at least one focused local validation command before approving.")

    has_sensitive_area = bool(
        area_names
        & {
            "product_runtime",
            "app_or_ui_surface",
            "ci_or_release",
            "build_or_config",
            "agent_instruction_surface",
        }
    )
    if (wait_for_ci and checks.get("failures")) or changed >= 12 or churn >= 800 or (state == "MERGED" and has_sensitive_area):
        level = "high"
    elif has_sensitive_area or (wait_for_ci and (checks.get("pending") or not checks.get("total"))):
        level = "medium"
    else:
        level = "low"

    return {
        "schema_version": "main_regression_analysis_v0",
        "risk_level": level,
        "risk_summary": (
            f"{RISK_LEVEL_LABELS.get(level, level)} main regression risk across {_area_phrase(areas)}; "
            f"{changed} file(s), +{additions}/-{deletions}; "
            + (f"checks={_check_brief_phrase(checks)}." if wait_for_ci else "CI not consulted.")
        ),
        "potential_regressions": potential_regressions[:5],
        "bug_risks": bug_risks[:5],
        "verification_focus": verification_focus[:5],
        "post_merge_review": state == "MERGED" or bool(pr.get("mergedAt") or pr.get("merged_at")),
    }


def _check_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or item.get("context") or item.get("workflowName") or "check")


def _check_state(item: dict[str, Any]) -> str:
    conclusion = str(item.get("conclusion") or "").upper()
    status = str(item.get("status") or "").upper()
    if conclusion in {"SUCCESS", "PASSED", "NEUTRAL", "SKIPPED"}:
        return "success"
    if conclusion in {"FAILURE", "FAILED", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}:
        return "failure"
    if status in {"COMPLETED"} and conclusion:
        return conclusion.lower()
    if status in {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING"}:
        return "pending"
    return "unknown"


def _checks(pr: dict[str, Any]) -> dict[str, Any]:
    raw_items = [
        item
        for item in _as_list(pr.get("statusCheckRollup"))
        if isinstance(item, dict)
    ]
    items, superseded = latest_check_attempts(raw_items)
    counts: dict[str, int] = {}
    failures: list[str] = []
    pending: list[str] = []
    for item in items:
        state = _check_state(item)
        counts[state] = counts.get(state, 0) + 1
        if state == "failure":
            failures.append(_redact_text(_check_name(item), limit=120))
        elif state == "pending":
            pending.append(_redact_text(_check_name(item), limit=120))
    if not items:
        return {
            "total": 0,
            "raw_total": len(raw_items),
            "superseded": superseded,
            "counts": {},
            "summary": "No status-check rollup was available from the source.",
            "failures": [],
            "pending": [],
        }
    if failures:
        summary = f"{len(failures)} failing check(s)."
    elif pending:
        summary = f"{len(pending)} pending check(s)."
    else:
        summary = f"{counts.get('success', 0)} successful check(s)."
    return {
        "total": len(items),
        "raw_total": len(raw_items),
        "superseded": superseded,
        "counts": counts,
        "summary": summary,
        "failures": failures[:5],
        "pending": pending[:5],
    }


def _risk_notes(pr: dict[str, Any], files: list[dict[str, Any]], *, wait_for_ci: bool = True) -> list[str]:
    notes: list[str] = []
    state = str(pr.get("state") or "").upper()
    if state == "MERGED" or pr.get("mergedAt") or pr.get("merged_at"):
        notes.append("Already merged: review for post-merge quality, regression risk, and follow-up work.")
    elif state in {"CLOSED"}:
        notes.append("Closed without a merge signal; review only if it still informs a replacement path.")
    if pr.get("isDraft"):
        notes.append("Draft PR: review may be advisory until it is marked ready.")
    if state != "MERGED" and str(pr.get("mergeStateStatus") or "").upper() not in {"", "CLEAN", "HAS_HOOKS", "UNKNOWN"}:
        notes.append(f"Merge state is {pr.get('mergeStateStatus')}; check conflict or branch-protection details.")
    if any(str(item.get("path") or "").startswith(RUNTIME_OR_CLI_PREFIXES) for item in files):
        notes.append("Touches runtime, app, CLI, or automation paths; review behavior and compatibility before merge.")
    changed = int(pr.get("changedFiles") or len(files) or 0)
    additions = int(pr.get("additions") or 0)
    deletions = int(pr.get("deletions") or 0)
    if changed >= 12 or additions + deletions >= 800:
        notes.append("Large review surface; split the review by area before approving.")
    checks = _checks(pr)
    if wait_for_ci and checks.get("failures"):
        notes.append("Failing status checks block a clean merge decision.")
    return notes


def _review_depth(files: list[dict[str, Any]]) -> str:
    areas = {str(item.get("area") or "") for item in files}
    if "product_runtime" in areas:
        return "runtime_behavior_review"
    if "agent_instruction_surface" in areas:
        return "agent_behavior_review"
    if "app_or_ui_surface" in areas or "public_entry_or_policy" in areas:
        return "presentation_or_policy_review"
    if areas <= {"public_docs", "test_or_example"}:
        return "docs_and_smoke_review"
    return "standard_review"


def _parse_updated_epoch(value: object) -> float:
    parsed = _parse_timestamp(value)
    return parsed.timestamp() if parsed else 0.0


def _latest_commit_timestamp(pr: Mapping[str, Any]) -> datetime | None:
    timestamps = [
        parsed
        for commit in _as_list(pr.get("commits"))
        if isinstance(commit, Mapping)
        for parsed in (
            _parse_timestamp(commit.get("committedDate"))
            or _parse_timestamp(commit.get("authoredDate")),
        )
        if parsed is not None
    ]
    return max(timestamps) if timestamps else None


def _review_ready_timestamp(pr: Mapping[str, Any]) -> datetime | None:
    commit_at = _latest_commit_timestamp(pr)
    created_at = _parse_timestamp(pr.get("createdAt") or pr.get("created_at"))
    if commit_at and created_at:
        return max(commit_at, created_at)
    return (
        commit_at
        or created_at
        or _parse_timestamp(pr.get("updatedAt") or pr.get("updated_at"))
    )


def _english_review_verdict(body: str) -> str | None:
    for line in body.splitlines():
        normalized = line.strip().replace("**", "")
        match = re.match(
            r"(?i)^english verdict\s*:\s*(APPROVE|REQUEST_CHANGES)\b",
            normalized,
        )
        if match:
            return match.group(1).upper()
    return None


def _review_body_has_required_format(body: str, *, head_oid: str) -> bool:
    return (
        bool(head_oid)
        and head_oid.lower() in body.lower()
        and all(
            re.search(rf"(?m)^#+\s*{re.escape(heading)}\s*$", body)
            for heading in REQUIRED_REVIEW_SECTION_HEADINGS
        )
        and _english_review_verdict(body) is not None
    )


def _review_conclusion(
    pr: Mapping[str, Any],
    *,
    reviewer_login: str | None,
) -> dict[str, Any]:
    head_oid = str(pr.get("headRefOid") or pr.get("head_oid") or "").strip()
    pr_author = str(
        _as_dict(pr.get("author")).get("login") or pr.get("author") or ""
    ).strip()
    reviews = [item for item in _as_list(pr.get("reviews")) if isinstance(item, dict)]
    reviews.sort(
        key=lambda item: _parse_updated_epoch(item.get("submittedAt")),
        reverse=True,
    )
    reviewer_owns_pr = bool(
        reviewer_login
        and pr_author
        and reviewer_login.casefold() == pr_author.casefold()
    )
    if not reviews:
        return {
            "schema_version": REVIEW_CONCLUSION_SCHEMA_VERSION,
            "status": "missing",
            "valid": False,
            "state": None,
            "verdict": None,
            "reviewer": None,
            "submitted_at": None,
            "invalid_reasons": ["no_review_conclusion_available"],
        }

    def evaluate(review: Mapping[str, Any]) -> dict[str, Any]:
        state = str(review.get("state") or "").strip().upper()
        body = str(review.get("body") or "")
        english_verdict = _english_review_verdict(body)
        review_author = str(_as_dict(review.get("author")).get("login") or "").strip()
        commit_oid = str(_as_dict(review.get("commit")).get("oid") or "").strip()
        review_is_by_pr_author = bool(
            review_author
            and pr_author
            and review_author.casefold() == pr_author.casefold()
        )
        author_owned_fallback = review_is_by_pr_author and reviewer_owns_pr
        reasons: list[str] = []
        if not head_oid or commit_oid.casefold() != head_oid.casefold():
            reasons.append("review_not_bound_to_current_head")
        if not _review_body_has_required_format(body, head_oid=head_oid):
            reasons.append("review_body_missing_standalone_bilingual_format")
        if author_owned_fallback:
            expected_title = {
                "APPROVE": AUTHOR_OWNED_APPROVAL_FALLBACK_TITLE,
                "REQUEST_CHANGES": AUTHOR_OWNED_REQUEST_CHANGES_FALLBACK_TITLE,
            }.get(english_verdict or "")
            if state != "COMMENTED" or not expected_title or expected_title not in body:
                reasons.append("author_owned_review_missing_titled_commented_fallback")
        elif state not in {"APPROVED", "CHANGES_REQUESTED"}:
            reasons.append("formal_review_state_required")
        elif (
            (state == "APPROVED" and english_verdict != "APPROVE")
            or (
                state == "CHANGES_REQUESTED"
                and english_verdict != "REQUEST_CHANGES"
            )
        ):
            reasons.append("review_state_verdict_mismatch")
        return {
            "schema_version": REVIEW_CONCLUSION_SCHEMA_VERSION,
            "status": "valid" if not reasons else "invalid",
            "valid": not reasons,
            "state": state or None,
            "verdict": english_verdict,
            "reviewer": review_author or reviewer_login,
            "submitted_at": review.get("submittedAt"),
            "invalid_reasons": reasons,
        }

    latest_result = evaluate(reviews[0])
    if (
        str(reviews[0].get("state") or "").upper() == "COMMENTED"
        and not latest_result["valid"]
    ):
        for earlier in reviews[1:]:
            earlier_result = evaluate(earlier)
            if earlier_result["valid"]:
                return earlier_result
    return latest_result


def _review_sequence_entry(item: dict[str, Any], *, rank: int) -> dict[str, Any]:
    main_risk = _as_dict(item.get("main_regression_analysis"))
    return {
        "rank": rank,
        "number": item.get("number"),
        "title": item.get("title"),
        "url": item.get("url"),
        "state": item.get("state"),
        "review_depth": item.get("review_depth"),
        "risk_hint_level": _as_dict(item.get("metadata_risk_hint")).get("level"),
        "main_risk_level": main_risk.get("risk_level"),
        "created_at": item.get("created_at"),
        "review_ready_at": item.get("review_ready_at"),
        "review_ready_age_hours": item.get("review_ready_age_hours"),
        "author_owned": item.get("author_owned") is True,
        "community_feedback_ready": item.get("community_feedback_ready") is True,
        "scheduling_lane": item.get("scheduling_lane"),
        "scheduling_tier": item.get("scheduling_tier"),
        "review_action_kind": item.get("review_action_kind"),
        "review_conclusion_status": _as_dict(item.get("review_conclusion")).get(
            "status"
        ),
        "why_now": _review_why_now(item),
    }


def _normalize_pr(
    pr: dict[str, Any],
    *,
    reviewer_login: str | None,
    review_priority: PullRequestReviewPriority = DEFAULT_REVIEW_PRIORITY,
    generated_at: datetime,
    fresh_audit_exact_heads: set[str],
    wait_for_ci: bool = True,
) -> dict[str, Any]:
    files = _files(pr)
    checks = _checks(pr)
    number = pr.get("number")
    url = pr.get("url") or (f"https://github.com/pull/{number}" if number else "")
    raw_state = str(pr.get("state") or "").upper()
    if not raw_state:
        raw_state = "MERGED" if pr.get("mergedAt") or pr.get("merged_at") else "OPEN"
    merge_commit = pr.get("mergeCommit") or pr.get("merge_commit")
    merge_commit_oid = (
        _as_dict(merge_commit).get("oid") if isinstance(merge_commit, dict) else None
    )
    author = _redact_text(
        _as_dict(pr.get("author")).get("login") or pr.get("author"), limit=80
    )
    ready_at = _review_ready_timestamp(pr)
    created_at = _parse_timestamp(pr.get("createdAt") or pr.get("created_at"))
    ready_age_hours = (
        max(0.0, (generated_at - ready_at).total_seconds() / 3600)
        if ready_at is not None
        else 0.0
    )
    conclusion = _review_conclusion(pr, reviewer_login=reviewer_login)
    item: dict[str, Any] = {
        "number": number,
        "title": _redact_text(pr.get("title"), limit=180),
        "url": _redact_text(url, limit=220),
        "state": raw_state,
        "author": author,
        "created_at": created_at.isoformat().replace("+00:00", "Z")
        if created_at
        else None,
        "updated_at": pr.get("updatedAt"),
        "review_ready_at": ready_at.isoformat().replace("+00:00", "Z")
        if ready_at
        else None,
        "review_ready_age_hours": round(ready_age_hours, 3),
        "author_owned": bool(
            reviewer_login and author.casefold() == reviewer_login.casefold()
        ),
        "community_feedback_ready": False,
        "closed_at": pr.get("closedAt"),
        "merged_at": pr.get("mergedAt"),
        "merge_commit": _redact_text(merge_commit_oid, limit=80)
        if merge_commit_oid
        else None,
        "base_ref": _redact_text(pr.get("baseRefName"), limit=80),
        "head_ref": _redact_text(pr.get("headRefName"), limit=120),
        "head_oid": _redact_text(pr.get("headRefOid"), limit=80),
        "is_draft": bool(pr.get("isDraft")),
        "review_decision": str(pr.get("reviewDecision") or "UNKNOWN"),
        "review_conclusion": conclusion,
        "merge_state": str(
            pr.get("mergeStateStatus") or pr.get("mergeable") or "UNKNOWN"
        ),
        "motivation": _motivation(pr),
        "scale": {
            "changed_files": int(pr.get("changedFiles") or len(files) or 0),
            "additions": int(pr.get("additions") or 0),
            "deletions": int(pr.get("deletions") or 0),
        },
        "areas": _area_counts(files),
        "key_files": files[:10],
        "commit_headlines": _commit_headlines(pr),
        "checks": checks,
        "wait_for_ci": wait_for_ci,
        "review_depth": _review_depth(files),
        "risk_notes": _risk_notes(pr, files, wait_for_ci=wait_for_ci),
        "metadata_risk_hint": _metadata_risk_hint(pr, files, checks, wait_for_ci=wait_for_ci),
        "main_regression_analysis": _main_regression_analysis(pr, files, wait_for_ci=wait_for_ci),
    }
    item.update(
        materialize_review_execution(
            item, fresh_audit_exact_heads=fresh_audit_exact_heads
        )
    )
    item["community_feedback_ready"] = bool(
        not item["author_owned"]
        and item["review_action_kind"] == "rereview_pull_request_exact_head"
        and community_feedback_ready(pr, review_ready_at=ready_at)
    )
    item["scheduling_lane"] = classify_scheduling_lane(item).value
    item["scheduling_tier"] = scheduling_tier(
        item, review_priority=review_priority
    )
    return item


def build_pr_review_packet(
    *,
    pull_requests: list[dict[str, Any]],
    repository: str | None,
    limit: int,
    source: str,
    state_filter: str = "all",
    since: str | None = None,
    source_scan: Mapping[str, Any] | None = None,
    reviewer_login: str | None = None,
    fresh_audit_exact_heads: Sequence[str] = (),
    review_priority: object = DEFAULT_REVIEW_PRIORITY,
    wait_for_ci: bool = True,
) -> dict[str, Any]:
    normalized_state_filter = normalize_pr_state_filter(state_filter)
    normalized_priority = normalize_review_priority(review_priority)
    generated_at_text = _now_iso()
    generated_at = _parse_timestamp(generated_at_text) or datetime.now(timezone.utc)
    requested_fresh_audits = normalize_fresh_audit_exact_heads(fresh_audit_exact_heads)
    normalized_all = [
        _normalize_pr(
            item,
            reviewer_login=reviewer_login,
            review_priority=normalized_priority,
            generated_at=generated_at,
            fresh_audit_exact_heads=requested_fresh_audits,
            wait_for_ci=wait_for_ci,
        )
        for item in pull_requests
    ]
    normalized_all = [
        item
        for item in normalized_all
        if (normalized_state_filter == "all" or str(item.get("state") or "").lower() == normalized_state_filter)
        and _include_pr_in_window(item, since=since)
    ]
    normalized_all.sort(
        key=lambda item: scheduling_sort_key(
            item, review_priority=normalized_priority
        )
    )
    packet_limit = max(1, limit)
    unmerged_all = [item for item in normalized_all if str(item.get("state") or "").upper() != "MERGED"]
    merged_all = [item for item in normalized_all if str(item.get("state") or "").upper() == "MERGED"]
    if normalized_state_filter == "all":
        unmerged_items = unmerged_all[:packet_limit]
        merged_items = merged_all[:packet_limit]
        normalized = unmerged_items + merged_items
    else:
        normalized = normalized_all[:packet_limit]
        unmerged_items = [item for item in normalized if str(item.get("state") or "").upper() != "MERGED"]
        merged_items = [item for item in normalized if str(item.get("state") or "").upper() == "MERGED"]
    observed_exact_heads = {
        key for item in normalized if (key := exact_head_key(item))
    }
    missing_fresh_audits = requested_fresh_audits - observed_exact_heads
    if missing_fresh_audits:
        raise ValueError(
            "fresh audit exact head is absent from the current result window: "
            + ", ".join(sorted(missing_fresh_audits))
        )
    source_scan_complete = (
        source_scan.get("complete") is True
        if isinstance(source_scan, Mapping)
        else True
    )
    group_observed_counts = {
        "unmerged": len(unmerged_all),
        "merged": len(merged_all),
    }
    group_included_counts = {
        "unmerged": len(unmerged_items),
        "merged": len(merged_items),
    }
    group_completeness = {
        key: source_scan_complete
        and group_included_counts[key] == group_observed_counts[key]
        for key in group_observed_counts
    }
    complete = all(group_completeness.values())
    recommended_limit = None
    if not complete:
        recommended_limit = max(
            packet_limit * 2,
            max(group_observed_counts.values(), default=0) + 1,
        )
    source_scan_summary = None
    if isinstance(source_scan, Mapping):
        source_scan_summary = {
            key: value
            for key, value in source_scan.items()
            if key != "pull_requests"
        }
    result_completeness = {
        "schema_version": "pr_review_result_completeness_v0",
        "complete": complete,
        "truncated": not complete,
        "limit": packet_limit,
        "limit_scope": "per_group" if normalized_state_filter == "all" else "filtered_queue",
        "source_scan_complete": source_scan_complete,
        "observed_count_is_lower_bound": not source_scan_complete,
        "observed_pr_count": len(normalized_all),
        "included_pr_count": len(normalized),
        "groups": {
            key: {
                "complete": group_completeness[key],
                "observed_count": group_observed_counts[key],
                "included_count": group_included_counts[key],
                "truncated": not group_completeness[key],
            }
            for key in ("unmerged", "merged")
        },
        "recommended_limit": recommended_limit,
        "rerun_cli_args": (
            ["--limit", str(recommended_limit)] if recommended_limit else []
        ),
        "source_scan": source_scan_summary,
    }
    actionable_items = [item for item in normalized if item.get("review_action_kind")]
    open_review_required = [
        item
        for item in actionable_items
        if str(item.get("state") or "").upper() == "OPEN"
    ]
    merged_review_required = [
        item
        for item in actionable_items
        if str(item.get("state") or "").upper() == "MERGED"
    ]
    review_sequence = [
        _review_sequence_entry(item, rank=index)
        for index, item in enumerate(actionable_items, start=1)
    ]
    closed_items = [item for item in normalized if str(item.get("state") or "").upper() == "CLOSED"]
    first = review_sequence[0] if review_sequence else None
    review_attention_count = len(actionable_items)
    open_items = [item for item in normalized if str(item.get("state") or "").upper() == "OPEN"]
    review_groups = {
        "unmerged": {
            "schema_version": "pr_review_group_v0",
            "group_id": "unmerged",
            "title": "Unmerged PRs",
            "intent": "Review before merge: decide approve, request changes, defer, or wait for checks.",
            "count": len(unmerged_items),
            "complete": group_completeness["unmerged"],
            "observed_count": group_observed_counts["unmerged"],
            "truncated": not group_completeness["unmerged"],
            "pr_numbers": [item.get("number") for item in unmerged_items],
            "actionable_count": len(open_review_required),
            "no_action_count": len(unmerged_items) - len(open_review_required),
            "review_sequence": [
                _review_sequence_entry(item, rank=index)
                for index, item in enumerate(open_review_required, start=1)
            ],
        },
        "merged": {
            "schema_version": "pr_review_group_v0",
            "group_id": "merged",
            "title": "Merged PRs",
            "intent": "Post-merge audit: check outcome, regression risk, and follow-up quality without blocking already-merged work.",
            "count": len(merged_items),
            "complete": group_completeness["merged"],
            "observed_count": group_observed_counts["merged"],
            "truncated": not group_completeness["merged"],
            "pr_numbers": [item.get("number") for item in merged_items],
            "actionable_count": len(merged_review_required),
            "no_action_count": len(merged_items) - len(merged_review_required),
            "review_sequence": [
                _review_sequence_entry(item, rank=index)
                for index, item in enumerate(merged_review_required, start=1)
            ],
        },
    }
    headline = (
        (
            f"{len(normalized)} PR(s) in review window: "
            f"{len(open_items)} open, "
            f"{len(merged_items)} merged; {review_attention_count} need review attention."
        )
        if normalized
        else "No pull requests found for the requested review window."
    )
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "request": {
            "schema_version": "loopx_pr_review_command_request_v0",
            "command": COMMAND,
            "cli_command": "loopx pr-review [--repo owner/repo] [--state open|merged|all] [--review-priority other-developers-first|owner-first] [--since ISO]",
            "repository": repository,
            "limit": max(1, limit),
            "state_filter": normalized_state_filter,
            "since": since,
            "window": {
                "since": since,
                "state_filter": normalized_state_filter,
            },
            "source": source,
            "reviewer_login": reviewer_login,
            "review_priority": normalized_priority.value,
            "fresh_audit_exact_heads": sorted(requested_fresh_audits),
            "include": [
                "pull_request_list",
                "result_completeness",
                "review_groups",
                "review_plan",
                "review_template",
                "agent_response_contract",
                "change_scope",
                "checks",
                "metadata_risk_hint",
                "main_regression_analysis",
                "risk_notes",
                "review_sequence",
                "scheduling_policy",
            ],
            "privacy_mode": "public_safe_github_metadata",
            "dry_run": True,
        },
        "generated_at": generated_at_text,
        "summary": {
            "headline": headline,
            "total_pr_count": len(normalized),
            "open_pr_count": len(open_items),
            "merged_pr_count": len(merged_items),
            "closed_pr_count": len(closed_items),
            "review_attention_count": review_attention_count,
            "open_review_attention_count": len(open_review_required),
            "post_merge_review_count": len(merged_review_required),
            "draft_count": sum(1 for item in normalized if item.get("is_draft")),
            "source_surfaces": SOURCE_SURFACES,
            "recommended_first_pr": first,
        },
        "result_completeness": result_completeness,
        "scheduling_policy": build_scheduling_policy(
            authenticated_developer_login=reviewer_login,
            review_priority=normalized_priority,
        ),
        "review_sequence": review_sequence,
        "review_groups": review_groups,
        "pull_requests": normalized,
        "agent_response_contract": build_agent_response_contract(wait_for_ci=wait_for_ci),
        "actions": [
            {
                "action_id": "act_review_next_pr",
                "kind": "review",
                "requires_user_approval": False,
                "requires_maintainer_authority": False,
                "preview": "Start with the first capability-ranked actionable PR in review_sequence, read its motivation, inspect key files, then decide approve/request changes/defer.",
            },
            {
                "action_id": "act_merge_after_review",
                "kind": "merge_or_publish",
                "requires_user_approval": False,
                "requires_maintainer_authority": True,
                "preview": "Merge only after repository policy, validation, and public/private boundary checks pass.",
            },
        ],
        "omissions": [
            "Raw logs, private connector payloads, credentials, local paths, and private source bodies were intentionally omitted.",
            "The command summarizes public PR metadata and does not post review comments, approve, merge, or spend quota.",
        ],
        "boundary": BOUNDARY,
    }


def _review_why_now(item: dict[str, Any]) -> str:
    if item.get("fresh_audit_requested") is True:
        return "A caller explicitly requested a fresh audit of this unchanged exact head."
    state = str(item.get("state") or "").upper()
    if state == "MERGED":
        return "Merged exact head lacks a valid conclusion; audit outcome, validation, and follow-up quality."
    if state == "CLOSED":
        return "Closed without a merge signal; check whether a replacement or cleanup is needed."
    if item.get("is_draft"):
        return "Draft PR; skim for early direction but do not treat as merge-ready."
    conclusion = _as_dict(item.get("review_conclusion"))
    if conclusion.get("valid") is True:
        if str(conclusion.get("verdict") or "").upper() == "APPROVE":
            return "The current exact head has a complete approval; qualify merge readiness."
        return "The current exact head already has a complete standalone conclusion."
    if item.get("author_owned"):
        return "Authenticated-developer-owned PR is in the first actionable scheduling tier."
    if item.get("community_feedback_ready"):
        return "A community contributor pushed a new exact head after an independent request-changes review."
    if (
        item.get("scheduling_lane")
        == PullRequestSchedulingLane.COMMUNITY_AGED_BACKLOG.value
    ):
        return "Community exact head has waited at least 24 hours and is in the aged-backlog tier."
    decision = str(item.get("review_decision") or "").upper()
    if decision in {"REVIEW_REQUIRED", "UNKNOWN", ""}:
        return "Open and awaiting reviewer decision."
    if decision == "CHANGES_REQUESTED":
        return "Changes were requested; verify whether the latest diff addresses them."
    if decision == "APPROVED":
        return "Approved but still open; check merge state and final validation."
    return "Open PR; confirm current merge readiness."


def render_pr_review_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return "# Project PR Review Queue\n\n- ok: `False`\n- error: " + _redact_text(payload.get("error"))

    summary = _as_dict(payload.get("summary"))
    request = _as_dict(payload.get("request"))
    completeness = _as_dict(payload.get("result_completeness"))
    lines = [
        "# Project PR Review Queue",
        "",
        f"- command: `{request.get('command')}`",
        f"- repository: `{request.get('repository') or 'current gh repository'}`",
        f"- state_filter: `{request.get('state_filter') or 'all'}`",
        f"- since: `{request.get('since') or 'not set'}`",
        f"- headline: {summary.get('headline')}",
        f"- complete: `{completeness.get('complete')}`; truncated=`{completeness.get('truncated')}`; recommended_limit=`{completeness.get('recommended_limit')}`",
        f"- counts: total=`{summary.get('total_pr_count')}`, open=`{summary.get('open_pr_count')}`, merged=`{summary.get('merged_pr_count')}`, review_attention=`{summary.get('review_attention_count')}`, draft=`{summary.get('draft_count')}`",
        "- tool contract: run `loopx pr-review` first and use its `review_groups`; use ad hoc `gh` commands only after selecting a PR from this packet.",
        "- final answer contract: queue/table is only a preface; `/loopx-pr-review` must return filled five-block review cards after reading PR evidence.",
        "- intent contract: the `/loopx-pr-review` prefix dominates; words like open/closed/merged/today are filters unless the user explicitly asks for stats/list-only.",
        "",
        "## Agent Output Contract",
        "",
        "- Do not stop at the queue/table summary.",
        "- Do not collapse this packet to `.summary` and `.review_sequence` only; preserve the paths named by `agent_response_contract.required_packet_fields_to_preserve`.",
        "- For each actionable selected PR, read PR body/files/diff/checks first, then return one review card.",
        "- Execute each non-null `pull_requests[].review_plan` against `agent_response_contract.review_execution_contract`; inventory-only rows expose no execution artifacts.",
        "- Code-changing PRs must include a `关键代码讲解` subsection under `具体改动`, grounded in exact-head symbols and short excerpts or equivalent pseudocode.",
        "- Bind the verdict to the remote head SHA and recheck it before answering.",
        "- Required card headings: `动机`, `改动思路`, `具体改动`, `对主干的风险`, `我的整体评价`.",
        "- `metadata_risk_hint` is only queue-ordering metadata; do not copy it as the final risk judgment.",
        "",
        "## Unmerged PRs",
    ]
    review_groups = _as_dict(payload.get("review_groups"))
    unmerged = _as_dict(review_groups.get("unmerged"))
    merged = _as_dict(review_groups.get("merged"))

    def append_group(group: dict[str, Any]) -> None:
        sequence = [item for item in _as_list(group.get("review_sequence")) if isinstance(item, dict)]
        if not sequence:
            lines.append("- none")
            return
        for item in sequence:
            lines.append(
                f"{item.get('rank')}. [#{item.get('number')} {item.get('title')}]({item.get('url')}) "
                f"[{item.get('state')}] - "
                f"{item.get('review_depth')} / main_risk=`{item.get('main_risk_level')}` "
                f"/ risk_hint=`{item.get('risk_hint_level')}`: {item.get('why_now')}"
            )

    append_group(unmerged)
    lines.extend(["", "## Merged PRs"])
    append_group(merged)
    lines.extend(["", "## Combined Review Sequence"])
    sequence = [item for item in _as_list(payload.get("review_sequence")) if isinstance(item, dict)]
    if not sequence:
        lines.append("- none")
    for item in sequence:
        lines.append(
            f"{item.get('rank')}. [#{item.get('number')} {item.get('title')}]({item.get('url')}) "
            f"[{item.get('state')}] - "
            f"{item.get('review_depth')} / main_risk=`{item.get('main_risk_level')}` "
            f"/ risk_hint=`{item.get('risk_hint_level')}`: {item.get('why_now')}"
        )

    for pr in [item for item in _as_list(payload.get("pull_requests")) if isinstance(item, dict)]:
        template = _as_dict(pr.get("review_template"))
        review_plan = _as_dict(pr.get("review_plan"))
        action_kind = str(pr.get("review_action_kind") or "").strip()
        row_instruction = (
            "> Agentloop should fill the five-block review after reading the PR body and diff. The template below is intentionally blank."
            if action_kind
            else "> Inventory-only exact head: read back the existing conclusion; do not run a full evidence review."
        )
        lines.extend(
            [
                "",
                f"## PR #{pr.get('number')}: {pr.get('title')}",
                "",
                row_instruction,
                "",
                f"- url: {pr.get('url')}",
                f"- state: `{pr.get('state')}`",
                f"- review action: `{action_kind or 'none'}`",
                f"- merged_at: `{pr.get('merged_at') or 'n/a'}`",
                f"- branch: `{pr.get('head_ref')}` -> `{pr.get('base_ref')}`",
                f"- status: review=`{pr.get('review_decision')}`, merge=`{pr.get('merge_state')}`, draft=`{pr.get('is_draft')}`",
                f"- scale: files=`{_as_dict(pr.get('scale')).get('changed_files')}`, +`{_as_dict(pr.get('scale')).get('additions')}`, -`{_as_dict(pr.get('scale')).get('deletions')}`",
                f"- areas: `{json.dumps(pr.get('areas') or {}, ensure_ascii=False)}`",
                f"- checks: {_as_dict(pr.get('checks')).get('summary')}",
            ]
        )
        applicability = _as_dict(review_plan.get("applicability"))
        if review_plan:
            lines.append(
                "- review plan: "
                f"exact_head=`{_as_dict(review_plan.get('target')).get('exact_head_key') or 'unresolved'}`; "
                f"code_change=`{applicability.get('code_change')}`; "
                f"symbol_map_required=`{applicability.get('symbol_map_required')}`; "
                f"negative_walkthrough_required=`{applicability.get('negative_walkthrough_required')}`"
            )
        risk_hint = _as_dict(pr.get("metadata_risk_hint"))
        if risk_hint:
            lines.append(
                f"- metadata risk hint: `{risk_hint.get('level')}` "
                f"({'; '.join(str(item) for item in _as_list(risk_hint.get('basis')))})"
            )
            lines.append(f"- risk hint disclaimer: {risk_hint.get('disclaimer')}")
        main_risk = _as_dict(pr.get("main_regression_analysis"))
        if main_risk:
            lines.append(
                f"- main regression analysis: `{main_risk.get('risk_level')}` - "
                f"{main_risk.get('risk_summary')}"
            )
            for label, key in (
                ("potential regressions", "potential_regressions"),
                ("bug risks", "bug_risks"),
                ("verification focus", "verification_focus"),
            ):
                values = [str(item) for item in _as_list(main_risk.get(key)) if item]
                if values:
                    lines.append(f"  - {label}: " + "; ".join(values[:3]))
        commits = [item for item in _as_list(pr.get("commit_headlines")) if item]
        if commits:
            lines.append("- commits: " + "; ".join(f"`{item}`" for item in commits))
        key_files = [item for item in _as_list(pr.get("key_files")) if isinstance(item, dict)]
        if key_files:
            lines.append("- key files:")
            for item in key_files[:8]:
                lines.append(
                    f"  - `{item.get('path')}` ({item.get('area')}, "
                    f"+{_int_or_zero(item.get('additions'))}/-{_int_or_zero(item.get('deletions'))})"
                )
        commands = [item for item in _as_list(pr.get("evidence_commands")) if item]
        if commands:
            lines.append("- suggested read commands:")
            for item in commands[:4]:
                lines.append(f"  - `{item}`")
        review_order = [str(item) for item in _as_list(template.get("review_order")) if item]
        if review_order:
            lines.append("- 推荐阅读顺序: " + " -> ".join(f"`{item}`" for item in review_order))
        risks = [item for item in _as_list(pr.get("risk_notes")) if item]
        lines.append("- risk notes: " + ("; ".join(str(item) for item in risks) if risks else "none"))
        sections = [item for item in _as_list(template.get("sections")) if isinstance(item, dict)]
        if sections:
            lines.append("- 五块模板（留空给 agentloop 填写）:")
            for item in sections:
                lines.append(
                    f"  - {item.get('label')}（{item.get('word_hint')}）："
                    f" {item.get('agent_instruction')}"
                )

    lines.extend(["", "## Boundary", "- Public PR metadata only; raw/private material is omitted."])
    return "\n".join(lines)
