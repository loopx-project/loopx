"""Bounded GitHub REST collector for public repo-health metrics.

Uses only the Python standard library. Authentication is read from the
environment (`GH_TOKEN` or `GITHUB_TOKEN`); the request packet never carries
credentials.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from statistics import quantiles
from typing import Any

from .contract import RepoHealthSnapshot


API_BASE = "https://api.github.com"
USER_AGENT = "loopx-repo-health/0.2.0"
REQUEST_TIMEOUT_SECONDS = 15
_LINK_LAST_PAGE = re.compile(r'[?&]page=(\d+)>;\s*rel="last"')
_DOCS_PATH_PATTERN = re.compile(
    r"^(?:/[^/]+/[^/]+)?"
    r"(?:/docs(?:/|$)|/wiki(?:/|$)|/(?:blob|tree)/[^/]+/docs(?:/|$)|/blob/[^/]+/readme(?:\.|$)|/readme(?:\.|$))",
    re.IGNORECASE,
)


class GitHubError(RuntimeError):
    pass


def is_docs_path(path: str) -> bool:
    """Return True when a GitHub popular path is a documentation surface.

    Documentation surfaces are the README file, the ``docs/`` tree (including
    blob/tree links into it), and the ``wiki/`` tree. This is the single
    classification rule for the derived ``docs_views`` metric; changing it
    changes metric semantics and requires contract revision.
    """

    return bool(_DOCS_PATH_PATTERN.match(path))


def _token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request_json(
    path: str,
    *,
    params: dict[str, str] | None = None,
    accept: str | None = None,
) -> tuple[Any, dict[str, str]]:
    url = f"{API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=_headers())
    if accept:
        req.add_header("Accept", accept)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            body = resp.read().decode("utf-8")
            return (json.loads(body) if body else None, headers)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise GitHubError(f"GET {path} failed with {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"GET {path} unreachable: {exc.reason}") from exc


def _last_page(headers: dict[str, str]) -> int | None:
    link = headers.get("link", "")
    match = _LINK_LAST_PAGE.search(link)
    return int(match.group(1)) if match else None


def _rate_limit_remaining(headers: dict[str, str]) -> int | None:
    raw = headers.get("x-ratelimit-remaining")
    return int(raw) if raw is not None else None


def _iso(value: str | None) -> str | None:
    return value if value else None


def _minutes_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return max(0.0, (e - s).total_seconds() / 60.0)
    except ValueError:
        return None


def _percentiles(values: list[float], p25: float = 0.25, p75: float = 0.75) -> tuple[float | None, float | None]:
    if not values:
        return (None, None)
    if len(values) < 5:
        return (round(values[0], 1), round(values[-1], 1))
    q = quantiles(sorted(values), n=4, method="inclusive")
    return (round(q[0], 1), round(q[2], 1))


class GitHubRepoHealthCollector:
    def __init__(self, owner: str, repo: str) -> None:
        self.owner = owner
        self.repo = repo
        self.warnings: list[str] = []
        self._rate_limit_remaining: int | None = None

    def collect(self) -> RepoHealthSnapshot:
        captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        repo_meta, headers = _request_json(f"/repos/{self.owner}/{self.repo}")
        self._rate_limit_remaining = _rate_limit_remaining(headers)
        repo = {
            "owner": self.owner,
            "name": self.repo,
            "license": (repo_meta.get("license") or {}).get("spdx_id"),
            "created_at": _iso(repo_meta.get("created_at")),
            "pushed_at": _iso(repo_meta.get("pushed_at")),
        }
        counts = {
            "stars": int(repo_meta["stargazers_count"]),
            "forks": int(repo_meta["forks_count"]),
            "watchers": int(repo_meta.get("subscribers_count", 0)),
            "open_issues": int(repo_meta["open_issues_count"]),
            "releases": self._count_releases(),
            "contributors": self._count_contributors(),
            "prs_total": 0,
            "issues_total": 0,
        }
        traffic_14d = self._collect_traffic()
        latency = self._collect_latency(counts)
        star_timeline_weekly = self._collect_star_timeline_weekly()
        evidence = {
            "sources": ["github-rest"],
            "rate_limit_remaining": self._rate_limit_remaining,
            "warnings": self.warnings,
        }
        return RepoHealthSnapshot(
            captured_at=captured_at,
            repo=repo,
            counts=counts,
            traffic_14d=traffic_14d,
            latency=latency,
            star_timeline_weekly=star_timeline_weekly,
            evidence=evidence,
        )

    def _count_releases(self) -> int:
        _, headers = _request_json(
            f"/repos/{self.owner}/{self.repo}/releases",
            params={"per_page": "1"},
        )
        last = _last_page(headers)
        return last or 0

    def _count_contributors(self) -> int:
        _, headers = _request_json(
            f"/repos/{self.owner}/{self.repo}/contributors",
            params={"per_page": "1", "anon": "true"},
        )
        return _last_page(headers) or 0

    def _collect_traffic(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for surface in ("views", "clones"):
            try:
                payload, _ = _request_json(f"/repos/{self.owner}/{self.repo}/traffic/{surface}")
                result[surface] = {"count": int(payload.get("count", 0)), "uniques": int(payload.get("uniques", 0))}
            except GitHubError as exc:
                result[surface] = {"count": 0, "uniques": 0}
                self.warnings.append(f"traffic/{surface} unavailable: {exc}")
        try:
            payload, _ = _request_json(f"/repos/{self.owner}/{self.repo}/traffic/popular/paths")
            rows = [
                {
                    "path": str(item.get("path", "")),
                    "title": str(item.get("title", "")),
                    "count": int(item.get("count", 0)),
                    "uniques": int(item.get("uniques", 0)),
                }
                for item in payload or []
            ]
            docs_rows = [row for row in rows if is_docs_path(row["path"])]
            result["paths"] = rows
            result["docs_views"] = {
                "count": sum(row["count"] for row in docs_rows),
                "uniques": sum(row["uniques"] for row in docs_rows),
            }
        except GitHubError as exc:
            result["paths"] = []
            result["docs_views"] = {"count": 0, "uniques": 0}
            self.warnings.append(f"traffic/popular/paths unavailable: {exc}")
        return result

    def _closed_pull_requests(self, limit: int = 200) -> list[dict[str, Any]]:
        pulls: list[dict[str, Any]] = []
        page = 1
        while len(pulls) < limit and page <= 5:
            batch, headers = _request_json(
                f"/repos/{self.owner}/{self.repo}/pulls",
                params={"state": "closed", "per_page": "100", "page": str(page), "sort": "updated", "direction": "desc"},
            )
            pulls.extend(batch or [])
            self._rate_limit_remaining = _rate_limit_remaining(headers)
            if len(batch or []) < 100:
                break
            page += 1
        return pulls[:limit]

    def _issues_with_comments(self, limit: int = 100) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        page = 1
        while len(issues) < limit and page <= 3:
            batch, headers = _request_json(
                f"/repos/{self.owner}/{self.repo}/issues",
                params={"state": "all", "per_page": "100", "page": str(page), "sort": "created", "direction": "desc"},
            )
            self._rate_limit_remaining = _rate_limit_remaining(headers)
            for issue in batch or []:
                if "pull_request" in issue:
                    continue
                if int(issue.get("comments", 0)) > 0:
                    issues.append(issue)
            if len(batch or []) < 100:
                break
            page += 1
        return issues[:limit]

    def _first_comment_minutes(self, number: int, created_at: str | None) -> float | None:
        try:
            events, _ = _request_json(
                f"/repos/{self.owner}/{self.repo}/issues/{number}/timeline",
                accept="application/vnd.github+json",
            )
        except GitHubError:
            return None
        human_times = [
            event.get("created_at")
            for event in events or []
            if event.get("event") == "commented"
            and not ((event.get("actor") or {}).get("login") or "").endswith("[bot]")
            and event.get("created_at")
        ]
        if not human_times:
            return None
        return _minutes_between(created_at, min(human_times))

    def _collect_latency(self, counts: dict[str, int]) -> dict[str, float | None]:
        pulls = self._closed_pull_requests()
        counts["prs_total"] = len(pulls)
        merge_latencies = [
            minutes
            for minutes in (_minutes_between(p.get("created_at"), p.get("merged_at")) for p in pulls)
            if minutes is not None
        ]
        p25, p75 = _percentiles(merge_latencies)
        if len(pulls) >= 100:
            self.warnings.append(f"pr merge latency sampled from latest {len(pulls)} closed PRs")

        issues = self._issues_with_comments()
        counts["issues_total"] = len(issues)
        first_response_minutes: list[float] = []
        sampled = issues[:30]
        with ThreadPoolExecutor(max_workers=8) as pool:
            future_to_issue = {
                pool.submit(self._first_comment_minutes, i["number"], i.get("created_at")): i for i in sampled
            }
            for future in as_completed(future_to_issue):
                minutes = future.result()
                if minutes is not None:
                    first_response_minutes.append(minutes)
        fr_p25, fr_p75 = _percentiles(first_response_minutes)
        if sampled:
            self.warnings.append(
                f"first-response latency sampled from {len(first_response_minutes)}/{len(sampled)} newest commented issues"
            )
        else:
            self.warnings.append("no commented issues found; first-response latency unavailable")
        return {
            "pr_merge_p25_min": p25,
            "pr_merge_p75_min": p75,
            "first_response_p25_min": fr_p25,
            "first_response_p75_min": fr_p75,
        }

    def _collect_star_timeline_weekly(self) -> list[dict[str, Any]]:
        """Weekly star buckets from the authenticated stargazers feed.

        The stargazers feed is oldest-first, so this collector reads the last
        pages (newest stars) for a bounded recent-window trend.
        """

        stars: list[str] = []
        try:
            _, headers = _request_json(
                f"/repos/{self.owner}/{self.repo}/stargazers",
                params={"per_page": "100", "page": "1"},
                accept="application/vnd.github.star+json",
            )
        except GitHubError as exc:
            self.warnings.append(f"star timeline unavailable: {exc}")
            return []
        self._rate_limit_remaining = _rate_limit_remaining(headers)
        last = _last_page(headers)
        pages = [1] if not last or last <= 1 else [last, last - 1, last - 2]
        for page in pages:
            batch, headers = _request_json(
                f"/repos/{self.owner}/{self.repo}/stargazers",
                params={"per_page": "100", "page": str(page)},
                accept="application/vnd.github.star+json",
            )
            self._rate_limit_remaining = _rate_limit_remaining(headers)
            stars.extend(item["starred_at"] for item in batch or [])
        if not stars:
            self.warnings.append("star timeline unavailable (stargazers endpoint requires auth)")
            return []
        buckets: dict[str, int] = {}
        for raw in stars:
            try:
                iso = datetime.fromisoformat(raw.replace("Z", "+00:00")).isocalendar()
            except ValueError:
                continue
            week = f"{iso.year}-W{iso.week:02d}"
            buckets[week] = buckets.get(week, 0) + 1
        return [{"week": week, "stars": count} for week, count in sorted(buckets.items())]
