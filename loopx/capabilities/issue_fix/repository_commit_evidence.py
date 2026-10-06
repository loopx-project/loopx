from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .repository_snapshot import _REPO_PATTERN


ISSUE_FIX_REPOSITORY_COMMIT_EVIDENCE_SCHEMA_VERSION = (
    "issue_fix_repository_commit_evidence_v0"
)

_COMMIT_REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{3,79}")
_RECOVERY_REF_PREFIXES = ("refs/heads/", "refs/remotes/", "refs/tags/")
_GITHUB_REMOTE_PATTERNS = (
    re.compile(r"https?://github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$", re.I),
    re.compile(r"git@github\.com:(?P<repo>[^/]+/[^/]+?)(?:\.git)?$", re.I),
    re.compile(r"ssh://git@github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$", re.I),
)


def _run_git(checkout: Path, arguments: list[str], *, operation: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=checkout,
            check=False,
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError(f"repository commit evidence {operation} failed") from None
    if completed.returncode != 0:
        raise ValueError(f"repository commit evidence {operation} failed")
    return completed.stdout.strip()


def _resolved_commit(checkout: Path, value: str, *, field: str) -> str:
    if not _COMMIT_REF_PATTERN.fullmatch(value) or value.startswith("-"):
        raise ValueError(f"{field} must be a compact non-option git reference")
    resolved = _run_git(
        checkout,
        ["rev-parse", "--verify", f"{value}^{{commit}}"],
        operation=f"{field} resolution",
    )
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", resolved):
        raise ValueError(
            f"repository commit evidence {field} returned no commit object"
        )
    return resolved.lower()


def _github_repo_from_remote(value: str) -> str | None:
    for pattern in _GITHUB_REMOTE_PATTERNS:
        match = pattern.fullmatch(value.strip())
        if match:
            return match.group("repo").removesuffix(".git")
    return None


def _remote_repositories(checkout: Path) -> set[str]:
    remotes = _run_git(checkout, ["remote"], operation="remote inventory").splitlines()
    repositories: set[str] = set()
    for remote in remotes:
        if not remote.strip():
            continue
        urls = _run_git(
            checkout,
            ["remote", "get-url", "--all", remote.strip()],
            operation="remote identity read",
        ).splitlines()
        repositories.update(
            repo.lower()
            for url in urls
            if (repo := _github_repo_from_remote(url)) is not None
        )
    return repositories


def verify_issue_fix_repository_commit_evidence(
    *,
    repo_path: str | Path,
    repo: str,
    repository_revision: str,
    commit_ref: str,
    recovery_ref: str,
    verified_at: str,
) -> dict[str, Any]:
    """Resolve one delivery commit against a declared repository and stable ref.

    The returned proof is public-safe and clone-stable. It deliberately excludes
    checkout paths, remote URLs, and raw git output.
    """

    declared_repo = str(repo or "").strip()
    if not _REPO_PATTERN.fullmatch(declared_repo):
        raise ValueError("repo must use owner/name")
    revision = str(repository_revision or "").strip()
    delivery_ref = str(commit_ref or "").strip()
    stable_ref = str(recovery_ref or "").strip()
    if not stable_ref.startswith(_RECOVERY_REF_PREFIXES):
        raise ValueError(
            "repository recovery ref must be a full refs/heads, refs/remotes, or refs/tags ref"
        )
    if not _COMMIT_REF_PATTERN.fullmatch(stable_ref) or stable_ref.startswith("-"):
        raise ValueError("repository recovery ref must be a compact non-option git ref")
    timestamp = str(verified_at or "").strip()
    if not timestamp:
        raise ValueError("repository commit evidence verified_at is required")

    checkout = Path(repo_path).expanduser().resolve()
    if not checkout.is_dir():
        raise ValueError("repo_path must identify an existing caller-approved checkout")
    _run_git(
        checkout,
        ["rev-parse", "--is-inside-work-tree"],
        operation="checkout verification",
    )
    repositories = _remote_repositories(checkout)
    if declared_repo.lower() not in repositories:
        raise ValueError(
            "declared repository does not match any GitHub remote in the checkout"
        )

    revision_oid = _resolved_commit(checkout, revision, field="repository_revision")
    commit_oid = _resolved_commit(checkout, delivery_ref, field="commit_ref")
    recovery_oid = _resolved_commit(checkout, stable_ref, field="recovery_ref")
    if recovery_oid != revision_oid:
        raise ValueError(
            "repository recovery ref must resolve exactly to repository_revision"
        )
    _run_git(
        checkout,
        ["merge-base", "--is-ancestor", commit_oid, revision_oid],
        operation="commit ancestry verification",
    )
    root_oids = sorted(
        value.lower()
        for value in _run_git(
            checkout,
            ["rev-list", "--max-parents=0", recovery_oid],
            operation="repository root fingerprinting",
        ).splitlines()
        if re.fullmatch(r"[0-9a-fA-F]{40,64}", value)
    )
    if not root_oids:
        raise ValueError(
            "repository commit evidence could not fingerprint repository roots"
        )
    fingerprint_material = {
        "repo": declared_repo.lower(),
        "root_commit_oids": root_oids,
    }
    repository_fingerprint = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                fingerprint_material,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    return {
        "schema_version": ISSUE_FIX_REPOSITORY_COMMIT_EVIDENCE_SCHEMA_VERSION,
        "status": "verified",
        "repo": declared_repo,
        "repository_fingerprint": repository_fingerprint,
        "declared_repository_revision": revision,
        "repository_revision": revision_oid,
        "declared_commit_ref": delivery_ref,
        "commit_oid": commit_oid,
        "recovery_ref": stable_ref,
        "recovery_ref_oid": recovery_oid,
        "commit_is_ancestor": True,
        "verified_at": timestamp,
        "repo_path_captured": False,
        "remote_urls_captured": False,
        "raw_git_output_captured": False,
    }
