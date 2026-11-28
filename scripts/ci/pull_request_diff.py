"""Verify the published GitHub path inventory against the CI Git comparison."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any
from urllib.parse import quote

from impact_plan import Comparison, git, merge_base


class DiffUnavailable(Exception):
    """Git is available, but GitHub cannot supply a complete inventory."""


def _commit_oid(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Expected a complete GitHub commit SHA")
    return value


def _pr_identity(pr: dict[str, Any]) -> tuple:
    return (
        pr["number"], pr["base"]["repo"]["id"], pr["base"]["ref"],
        pr["head"]["repo"]["id"], pr["head"]["ref"], _commit_oid(pr["head"]["sha"]),
    )


def _api(endpoint: str, *, paginate: bool = False) -> Any:
    options = ["--paginate", "--slurp"] if paginate else []
    try:
        return json.loads(subprocess.check_output(
            ["gh", "api", "--method", "GET", *options, endpoint],
            stderr=subprocess.PIPE, timeout=45,
        ))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise DiffUnavailable("GitHub PR comparison read is unavailable") from exc


def _fetch_missing_commits(*oids: str) -> None:
    shallow = git("rev-parse", "--is-shallow-repository").strip() == b"true"
    missing = list(dict.fromkeys(oids)) if shallow else []
    if not shallow:
        for oid in dict.fromkeys(oids):
            try:
                git("cat-file", "-e", f"{oid}^{{commit}}")
            except subprocess.CalledProcessError:
                missing.append(oid)
    if missing:
        history = ["--unshallow"] if shallow else []
        git(
            "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
            "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--filter=blob:none",
            *history, "origin", *missing,
        )


def _published_paths(pages: Any, expected_count: int) -> set[str]:
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise DiffUnavailable("GitHub PR file pagination is malformed")
    rows = [row for page in pages for row in page]
    if len(rows) != expected_count:
        raise DiffUnavailable("GitHub PR file pagination is incomplete")
    paths: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise DiffUnavailable("GitHub PR file row is malformed")
        path, status = row.get("filename"), row.get("status")
        names = [path, row.get("previous_filename")] if status == "renamed" else [path]
        if (status not in ("added", "removed", "modified", "renamed", "copied", "changed")
                or any(not isinstance(name, str) or not name for name in names)
                or len(names) != len(set(names)) or paths.intersection(names)):
            raise DiffUnavailable("GitHub PR file inventory has missing or duplicate path identities")
        paths.update(names)
    return paths


class PullRequestDiff:
    def __init__(self, repository: str, pr: dict[str, Any]) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Expected the workflow's GitHub owner/repository")
        try:
            number, base_repo_id, target_ref, head_repo_id, source_ref, head_sha = _pr_identity(pr)
            self.base_sha = _commit_oid(pr["base"]["sha"])
            base_repo = pr["base"]["repo"]["full_name"]
        except (KeyError, TypeError) as exc:
            raise ValueError("Workflow event lacks a complete pull request identity") from exc
        if (not all(type(field) is int and field > 0 for field in (number, base_repo_id, head_repo_id))
                or not all(isinstance(ref, str) and ref for ref in (target_ref, source_ref))
                or not isinstance(base_repo, str) or base_repo.lower() != repository.lower()
                or pr.get("state") != "open"):
            raise ValueError("Workflow event does not identify an open PR in the checkout repository")
        self.head_sha = head_sha
        self.identity = (number, base_repo_id, target_ref, head_repo_id, source_ref, head_sha)
        self.endpoint = f"repos/{repository}/pulls/{number}"
        self.target_endpoint = f"repos/{repository}/branches/{quote(target_ref, safe='')}"

    @classmethod
    def from_workflow(cls) -> PullRequestDiff:
        payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        return cls(os.environ["GITHUB_REPOSITORY"], payload["pull_request"])

    def fetch_event_commits(self) -> None:
        # Pulls from forks remain reachable from the base repository's PR refs.
        # Fetch objects by OID only; the trusted workflow keeps its own checkout.
        _fetch_missing_commits(self.base_sha, self.head_sha)

    def _read_pr(self) -> tuple[str, int]:
        pr = _api(self.endpoint)
        try:
            unchanged = _pr_identity(pr) == self.identity and pr.get("state") == "open"
            sha, count = _commit_oid(pr["base"]["sha"]), pr["changed_files"]
        except (KeyError, TypeError, ValueError) as exc:
            raise DiffUnavailable("GitHub PR comparison metadata is incomplete") from exc
        if not unchanged:
            raise ValueError("PR head, source repository, target or state changed; qualify the current PR")
        if type(count) is not int or count < 0:
            raise DiffUnavailable("GitHub PR file count is unavailable")
        return sha, count

    def verify(self, comparison: Comparison) -> str | None:
        """Reject a demonstrated mismatch or return a reason to run all jobs."""
        if (self.base_sha, self.head_sha) != (comparison.base_sha, comparison.head_sha):
            raise ValueError("CI comparison base and head must match the pull request event")
        try:
            before_base, count = self._read_pr()
            # The pull files endpoint is capped even when every page is fetched.
            if count > 3000:
                return "GitHub PR file count exceeds the published API limit"
            pages = _api(f"{self.endpoint}/files?per_page=100", paginate=True)
            after_base, after_count = self._read_pr()
            if count != after_count:
                raise ValueError("PR file count changed during pagination; qualify the current PR")
            target = _api(self.target_endpoint)
            try:
                target_sha = _commit_oid(target["commit"]["sha"])
            except (KeyError, TypeError, ValueError) as exc:
                raise DiffUnavailable("GitHub target branch tip is unavailable") from exc
            for base in sorted({before_base, after_base, target_sha} - {comparison.base_sha}):
                _fetch_missing_commits(base)
                if merge_base(base, comparison.head_sha) != comparison.merge_base_sha:
                    raise ValueError("PR comparison merge base changed; qualify the current target and head")
            published = _published_paths(pages, count)
        except DiffUnavailable as exc:
            return str(exc)
        expected = {change.path for change in comparison.changes}
        if published != expected:
            raise ValueError(
                "Published PR file inventory differs from the exact Git comparison "
                f"({len(expected - published)} omitted, {len(published - expected)} unexpected)"
            )
        return None
