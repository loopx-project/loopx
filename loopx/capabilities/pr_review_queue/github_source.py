"""Bounded GitHub reads for the pull-request review queue."""

from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable

from .selection_execution import normalize_fresh_audit_exact_heads

GitHubJsonRunner = Callable[..., Any]

DETAIL_FIELDS = (
    "headRefOid",
    "baseRefOid",
    "body",
    "files",
    "reviewDecision",
    "mergeStateStatus",
    "mergeable",
    "createdAt",
    "commits",
    "reviews",
)

PR_LIST_FIELDS = (
    "number",
    "title",
    "url",
    "state",
    "isDraft",
    "headRefName",
    "headRefOid",
    "baseRefName",
    "baseRefOid",
    "author",
    "createdAt",
    "updatedAt",
    "closedAt",
    "mergedAt",
    "mergeCommit",
    "changedFiles",
    "additions",
    "deletions",
)


DEFAULT_GH_JSON_TIMEOUT_SECONDS: float = 60.0


def run_gh_json(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float = DEFAULT_GH_JSON_TIMEOUT_SECONDS,
) -> Any:
    proc = subprocess.run(
        ["gh", *args],
        cwd=cwd,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=max(1.0, float(timeout_seconds)),
    )
    return json.loads(proc.stdout or "null")


def _read_git(args: list[str], *, cwd: Path) -> bytes:
    # Local objects only. Lazy fetching, external diff and textconv must not turn
    # a read-only inventory into provider execution or a network operation.
    return subprocess.run(
        ["git", "--no-replace-objects", *args], cwd=cwd, check=True,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    ).stdout


def _recover_git_pr_files(
    *, repository: str, snapshot: dict[str, Any], observed: list[dict[str, Any]],
    cwd: Path | None,
) -> list[dict[str, Any]] | None:
    """Reconcile local immutable objects with API-confirmed rename pairs."""
    cwd = cwd if cwd is not None else Path.cwd()
    head, base = snapshot.get("headRefOid"), snapshot.get("baseRefOid")
    if any(not isinstance(oid, str) or not re.fullmatch(r"[0-9a-f]{40}", oid)
           for oid in (head, base)):
        return None
    try:
        remote = _read_git(["remote", "get-url", "origin"], cwd=cwd).decode().strip()
        match = re.fullmatch(
            r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
            r"([^/]+/[^/]+?)(?:\.git)?/?", remote,
        )
        if not match or match[1].casefold() != repository.casefold():
            return None
        for oid in (head, base):
            _read_git(["cat-file", "-e", f"{oid}^{{commit}}"], cwd=cwd)
        bases = _read_git(["merge-base", "--all", base, head], cwd=cwd).splitlines()
        if len(bases) != 1:
            return None
        merge_base = bases[0].decode("ascii")
        args = ["diff", "--no-ext-diff", "--no-textconv", "--no-renames"]
        stats = _read_git([*args, "--numstat", "-z", merge_base, head, "--"], cwd=cwd)
        names = _read_git([*args, "--name-status", "-z", merge_base, head, "--"], cwd=cwd)
        if not stats.endswith(b"\0") or not names.endswith(b"\0"):
            return None
        inventory: dict[str, dict[str, Any]] = {}
        for entry in stats[:-1].split(b"\0"):
            added, deleted, raw_path = entry.split(b"\t", 2)
            binary = added == deleted == b"-"
            if not binary and (not added.isdigit() or not deleted.isdigit()):
                return None
            path = raw_path.decode("utf-8", errors="strict")
            if not path or path in inventory:
                return None
            inventory[path] = {"path": path, "additions": None if binary else int(added),
                               "deletions": None if binary else int(deleted), "source": "git"}
        tokens = names[:-1].split(b"\0")
        if len(tokens) % 2:
            return None
        statuses = {tokens[i + 1].decode("utf-8", errors="strict"):
                    tokens[i].decode("ascii") for i in range(0, len(tokens), 2)}
        if len(statuses) != len(inventory) or statuses.keys() != inventory.keys():
            return None
        api_paths: set[str] = set()
        rename_endpoints: set[str] = set()
        for item in observed:
            path = item["path"]
            if path in api_paths:
                return None
            api_paths.add(path)
            if item.get("status") != "renamed":
                continue
            old = item.get("previous_filename")
            if (not isinstance(old, str) or old == path
                    or {old, path} & rename_endpoints
                    or statuses.get(old) != "D" or statuses.get(path) != "A"):
                return None
            rename_endpoints.update((old, path))
            del inventory[old]
            inventory[path] = {"path": path, "additions": item["additions"],
                               "deletions": item["deletions"], "source": "github"}
        if not api_paths <= inventory.keys():
            return None
        # Verify Git whole-diff totals with only API-confirmed rename folding.
        # Ordinary API rows may report 0/0 for generated/omitted diffs: replacing
        # their Git statistics before this check would conceal disagreements.
        # Git's valid -/- binary rows contribute no textual lines; retain None
        # for unknown per-file counts, rather than inventing API 0/0 values.
        if (len(inventory) != snapshot["changedFiles"]
                or sum(row["additions"] or 0 for row in inventory.values()) != snapshot["additions"]
                or sum(row["deletions"] or 0 for row in inventory.values()) != snapshot["deletions"]):
            return None
        for item in observed:
            inventory[item["path"]] = {key: item[key] for key in
                                      ("path", "additions", "deletions")} | {"source": "github"}
        return [inventory[path] for path in sorted(inventory)]
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError, KeyError):
        return None


def _fetch_complete_pr_files(
    *,
    repository: str,
    number: str,
    expected_count: int,
    cwd: Path | None,
    run_gh_json: GitHubJsonRunner,
    snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    # The ordinary REST path and closeout callers retain their existing contract.
    # Only a declared API-cap-sized PR with a versioned snapshot may use Git.
    recover = snapshot is not None and expected_count > 3000
    version_fields = ("headRefOid", "baseRefOid", "changedFiles", "additions", "deletions")

    def same_snapshot() -> bool:
        current = run_gh_json(["pr", "view", number, "--json", ",".join(version_fields),
                               "--repo", repository], cwd=cwd)
        return isinstance(current, dict) and all(
            current.get(key) == snapshot.get(key) for key in version_fields
        )

    try:
        if recover and (any(type(snapshot.get(key)) is not int or snapshot[key] < 0
                            for key in version_fields[2:]) or not same_snapshot()):
            return None
        payload = run_gh_json(
            ["api", "--paginate", "--slurp",
             f"repos/{repository}/pulls/{number}/files?per_page=100"], cwd=cwd,
        )
        if not isinstance(payload, list):
            return None
        pages = payload if all(isinstance(page, list) for page in payload) else [payload]
        files: list[dict[str, Any]] = []
        observed: list[dict[str, Any]] = []
        for page in pages:
            for item in page:
                if not isinstance(item, dict):
                    return None
                path = item.get("filename") or item.get("path")
                if not isinstance(path, str) or not path:
                    return None
                if recover and any(type(item.get(key)) is not int for key in ("additions", "deletions")):
                    return None
                additions = int(item.get("additions") or 0)
                deletions = int(item.get("deletions") or 0)
                if additions < 0 or deletions < 0:
                    return None
                row = {"path": path, "additions": additions, "deletions": deletions}
                files.append(row)
                observed.append(row | {"status": item.get("status"),
                                       "previous_filename": item.get("previous_filename")})
        if len(files) == expected_count:
            return files if not recover or same_snapshot() else None
        if not recover or not files or len(files) > expected_count:
            return None
        recovered = _recover_git_pr_files(
            repository=repository, snapshot=snapshot, observed=observed, cwd=cwd,
        )
        return recovered if recovered is not None and same_snapshot() else None
    except Exception:
        # This provider's failed read remains an explicit incomplete source.
        return None


def attach_pr_review_details(
    row: dict[str, Any],
    *,
    repository: str | None,
    cwd: Path | None = None,
    run_gh_json: GitHubJsonRunner = run_gh_json,
    wait_for_ci: bool = True,
) -> bool:
    """Attach complete per-PR details after the lightweight list scan."""

    detail_fields = DETAIL_FIELDS + (("statusCheckRollup",) if wait_for_ci else ())
    number = str(row.get("number") or "").strip()
    if not number or not repository:
        return False
    try:
        details = run_gh_json(
            [
                "pr",
                "view",
                number,
                "--json",
                ",".join(detail_fields),
                "--repo",
                repository,
            ],
            cwd=cwd,
        )
    except Exception:
        return False
    try:
        expected_file_count = int(row["changedFiles"])
    except (KeyError, TypeError, ValueError):
        return False
    if not isinstance(details, dict) or any(
        key not in details for key in detail_fields
    ):
        return False
    # Query the version alongside computed merge fields: an unversioned
    # GitHub detail query can return UNKNOWN while readiness has known state.
    # Never combine details from a different head/base with the list snapshot.
    if any(
        not row.get(key) or details[key] != row[key]
        for key in ("headRefOid", "baseRefOid")
    ):
        return False
    detail_files = details["files"]
    if not isinstance(detail_files, list):
        return False
    if len(detail_files) != expected_file_count:
        detail_files = _fetch_complete_pr_files(
            repository=repository,
            number=number,
            expected_count=expected_file_count,
            snapshot=row,
            cwd=cwd,
            run_gh_json=run_gh_json,
        )
        if detail_files is None:
            return False
        details["files"] = detail_files
    for key in detail_fields:
        row[key] = details[key]
    return True


def scan_github_pull_request_targets(
    *,
    repository: str,
    exact_heads: Sequence[str],
    cwd: Path | None = None,
    run_gh_json: GitHubJsonRunner = run_gh_json,
    wait_for_ci: bool = True,
) -> dict[str, Any]:
    """Read only explicitly requested exact heads, without scanning a queue."""

    targets = normalize_fresh_audit_exact_heads(exact_heads)
    if not targets:
        raise ValueError("at least one target exact head is required")

    pull_requests: list[dict[str, Any]] = []
    for target in sorted(
        targets, key=lambda item: (int(item.split("@", 1)[0]), item)
    ):
        number, expected_head = target.split("@", 1)
        try:
            row = run_gh_json(
                [
                    "pr",
                    "view",
                    number,
                    "--json",
                    ",".join(PR_LIST_FIELDS),
                    "--repo",
                    repository,
                ],
                cwd=cwd,
            )
        except Exception as exc:
            raise RuntimeError(f"target PR #{number} metadata read failed") from exc
        if not isinstance(row, dict):
            raise RuntimeError(f"target PR #{number} metadata read was not an object")
        actual_head = str(row.get("headRefOid") or "").strip().lower()
        if actual_head != expected_head:
            raise ValueError(
                f"target PR #{number} head changed: expected {expected_head}, "
                f"remote is {actual_head or 'unavailable'}"
            )
        details_ok = attach_pr_review_details(
            row,
            repository=repository,
            cwd=cwd,
            **({"wait_for_ci": False} if not wait_for_ci else {}),
            run_gh_json=run_gh_json,
        )
        if not details_ok:
            raise RuntimeError(f"target PR #{number} detail read was incomplete")
        pull_requests.append(row)

    return {
        "schema_version": "pr_review_source_scan_v0",
        "complete": True,
        "mode": "exact_targets",
        "requested_exact_heads": sorted(targets),
        "observed_exact_heads": sorted(targets),
        "pull_requests": pull_requests,
        "states": [
            {
                "state": "exact_targets",
                "fetch_limit": len(targets),
                "fetched_count": len(pull_requests),
                "included_after_window": len(pull_requests),
                "detail_read_failures": 0,
                "source_saturated": False,
                "source_read_valid": True,
            }
        ],
    }


PR_REVIEW_DETAIL_MAX_WORKERS = 8


def attach_pr_review_details_concurrently(
    rows: Sequence[dict[str, Any]],
    *,
    repository: str | None,
    cwd: Path | None = None,
    attach: Callable[..., bool] = attach_pr_review_details,
    run_gh_json: GitHubJsonRunner = run_gh_json,
    wait_for_ci: bool = True,
) -> list[bool]:
    """Read per-PR details concurrently while preserving queue order."""

    if not rows:
        return []
    worker_count = min(PR_REVIEW_DETAIL_MAX_WORKERS, len(rows))

    def read(row: dict[str, Any]) -> bool:
        return attach(
            row,
            repository=repository,
            cwd=cwd,
            run_gh_json=run_gh_json,
            **({"wait_for_ci": False} if not wait_for_ci else {}),
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(executor.map(read, rows))
