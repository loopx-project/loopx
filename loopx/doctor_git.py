"""Source-owned Git identity for installation manifests and diagnostics."""

from __future__ import annotations

import os
import re
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any


class GitRevisionRelation(str, Enum):
    SAME = "same"
    INSTALLED_AHEAD = "installed_ahead"
    INSTALLED_BEHIND = "installed_behind"
    DIVERGED = "diverged"
    UNKNOWN = "unknown"


def _source_git_environment() -> dict[str, str]:
    env = dict(os.environ)
    # A caller (for example a Git hook) may target an unrelated checkout.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        env.pop(key, None)
    return env


def _is_git_worktree_root(source_root: Path) -> bool:
    """Reject Git's upward discovery while accepting linked worktree gitfiles."""
    if not (source_root / ".git").exists():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "--show-toplevel"],
            check=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            env=_source_git_environment(),
        )
        return (
            result.returncode == 0
            and bool(result.stdout.strip())
            and Path(result.stdout.strip()).resolve() == source_root
        )
    except OSError:
        return False


def git_metadata_for_root(root: Path | None) -> dict[str, Any]:
    if root is None:
        return {
            "root": None,
            "git_commit": None,
            "git_ref": None,
            "git_dirty": None,
        }
    try:
        source_root = root.expanduser().resolve()
    except OSError:
        source_root = root.expanduser()
    if not _is_git_worktree_root(source_root):
        return {
            "root": str(source_root),
            "git_commit": None,
            "git_ref": None,
            "git_dirty": None,
        }

    def _run(args: list[str]) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(source_root), *args],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_source_git_environment(),
            )
        except OSError:
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    commit = _run(["rev-parse", "HEAD"])
    branch = _run(["symbolic-ref", "--quiet", "--short", "HEAD"])
    tag = _run(["describe", "--tags", "--exact-match"])
    status = _run(["status", "--porcelain"])
    dirty = (
        None
        if commit is None and branch is None and tag is None and status is None
        else bool(status)
    )
    return {
        "root": str(source_root),
        "git_commit": commit,
        "git_ref": branch or tag,
        "git_dirty": dirty,
    }


def git_revision_relation(
    root: Path | None,
    *,
    installed_commit: Any,
    comparison_commit: Any,
) -> GitRevisionRelation:
    """Classify installed vs comparison revisions in one Git object graph."""
    if not isinstance(installed_commit, str) or not installed_commit.strip():
        return GitRevisionRelation.UNKNOWN
    if not isinstance(comparison_commit, str) or not comparison_commit.strip():
        return GitRevisionRelation.UNKNOWN
    installed_commit = installed_commit.strip()
    comparison_commit = comparison_commit.strip()
    if installed_commit == comparison_commit:
        return GitRevisionRelation.SAME
    if root is None:
        return GitRevisionRelation.UNKNOWN

    try:
        source_root = root.expanduser().resolve()
    except OSError:
        source_root = root.expanduser()
    if not _is_git_worktree_root(source_root):
        return GitRevisionRelation.UNKNOWN

    def _is_ancestor(ancestor: str, descendant: str) -> bool | None:
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(source_root),
                    "merge-base",
                    "--is-ancestor",
                    ancestor,
                    descendant,
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_source_git_environment(),
            )
        except OSError:
            return None
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        return None

    comparison_is_ancestor = _is_ancestor(comparison_commit, installed_commit)
    installed_is_ancestor = _is_ancestor(installed_commit, comparison_commit)
    if comparison_is_ancestor is None or installed_is_ancestor is None:
        return GitRevisionRelation.UNKNOWN
    if comparison_is_ancestor:
        return GitRevisionRelation.INSTALLED_AHEAD
    if installed_is_ancestor:
        return GitRevisionRelation.INSTALLED_BEHIND
    return GitRevisionRelation.DIVERGED


def _github_repository_from_remote_url(value: Any) -> str | None:
    text = str(value or "").strip().removesuffix(".git")
    match = re.search(
        r"github\.com(?::|/)([^/\s]+/[^/\s]+)$", text, flags=re.IGNORECASE
    )
    return match.group(1).lower() if match else None


def trusted_release_ref_for_root(
    root: Path | None,
    *,
    repository: Any,
    ref: Any,
) -> dict[str, Any] | None:
    """Resolve the manifest repository's fetched ref without trusting canary HEAD."""
    expected_repository = _github_repository_from_remote_url(repository) or (
        str(repository or "").strip().removesuffix(".git").lower()
    )
    expected_ref = str(ref or "").strip().removeprefix("refs/heads/")
    if root is None or not expected_repository or not expected_ref:
        return None
    try:
        source_root = root.expanduser().resolve()
    except OSError:
        source_root = root.expanduser()
    if not _is_git_worktree_root(source_root):
        return None

    try:
        remotes = subprocess.run(
            ["git", "-C", str(source_root), "remote"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_source_git_environment(),
        )
    except OSError:
        return None
    if remotes.returncode != 0:
        return None

    for remote in remotes.stdout.splitlines():
        remote = remote.strip()
        if not remote:
            continue
        try:
            remote_url = subprocess.run(
                ["git", "-C", str(source_root), "remote", "get-url", remote],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_source_git_environment(),
            )
        except OSError:
            continue
        if (
            remote_url.returncode != 0
            or _github_repository_from_remote_url(remote_url.stdout)
            != expected_repository
        ):
            continue
        trusted_ref = f"refs/remotes/{remote}/{expected_ref}"
        resolved = subprocess.run(
            [
                "git",
                "-C",
                str(source_root),
                "rev-parse",
                "--verify",
                f"{trusted_ref}^{{commit}}",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_source_git_environment(),
        )
        commit = resolved.stdout.strip() if resolved.returncode == 0 else ""
        if commit:
            return {
                "label": f"{expected_repository}@{expected_ref}",
                "root": str(source_root),
                "git_commit": commit,
                "git_ref": f"{remote}/{expected_ref}",
            }
    return None
