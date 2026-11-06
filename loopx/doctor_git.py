"""Source-owned Git identity for installation manifests and diagnostics."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .extensions.process_runtime import terminate_process_tree


# `doctor` inspects a checkout the operator may not control, so every Git read
# below runs under one budget. A repository blocked on a stale index lock, a
# network mount or a very large history must not hold the diagnostic open.
GIT_PROBE_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class GitAnswer:
    """One finished Git query: the only fields the callers below read."""

    returncode: int
    stdout: str
    stderr: str


class GitSourceState(str, Enum):
    """What one checkout says about owning a Git identity of its own."""

    WORKTREE = "worktree"
    ABSENT = "absent"
    UNCONFIRMED = "unconfirmed"


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


def _retire_owned_probe(process: subprocess.Popen[str]) -> None:
    """Stop the query's own process group, then drop its pipes unread.

    Git runs helpers -- fsmonitor hooks, pagers, credential helpers -- inside
    the group it starts in, so killing the leader alone leaves them behind and
    repeated diagnostics accumulate them. An inherited pipe also survives that
    kill, so a drained ``communicate`` would wait on work nobody owns any more;
    the pipes are closed instead.
    """

    try:
        terminate_process_tree(process, grace_seconds=0.2)
    except (OSError, RuntimeError):
        # The answer is already unknown; an unconfirmed cleanup must not turn a
        # read-only diagnostic into a traceback.
        pass
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass


def _run_git(
    source_root: Path, *arguments: str
) -> GitAnswer | None:
    """Answer one local Git query, or None when Git cannot answer in budget.

    Every caller already treats a missing answer as "unknown" rather than as
    evidence, so an exhausted budget degrades along that existing path instead
    of raising out of a read-only diagnostic. The query is launched in a
    session of its own so the budget can retire the helpers it started, not
    just the Git process this call happens to hold.
    """

    argv = ["git", "-C", str(source_root), *arguments]
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_source_git_environment(),
            start_new_session=os.name == "posix",
        )
    except OSError:
        return None
    try:
        stdout, stderr = process.communicate(timeout=GIT_PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _retire_owned_probe(process)
        return None
    return GitAnswer(
        returncode=process.returncode, stdout=stdout, stderr=stderr
    )


def _git_source_state(source_root: Path) -> GitSourceState:
    """Reject Git's upward discovery while accepting linked worktree gitfiles.

    A checkout that carries its own ``.git`` is a Git source even when this run
    cannot read it. Callers that stand something else in for a missing answer
    have to tell that case apart from "there is no Git here at all".
    """

    if not (source_root / ".git").exists():
        return GitSourceState.ABSENT
    result = _run_git(source_root, "rev-parse", "--show-toplevel")
    if result is None or result.returncode != 0:
        return GitSourceState.UNCONFIRMED
    reported = result.stdout.strip()
    if not reported:
        return GitSourceState.UNCONFIRMED
    if Path(reported).resolve() != source_root:
        return GitSourceState.ABSENT
    return GitSourceState.WORKTREE


def _is_git_worktree_root(source_root: Path) -> bool:
    return _git_source_state(source_root) is GitSourceState.WORKTREE


def git_source_metadata(
    root: Path | None,
) -> tuple[dict[str, Any], GitSourceState]:
    """Return the identity a root claims and how far this run could read it.

    The pair matters because "this checkout has no Git identity" and "this
    checkout has one that did not answer in time" call for opposite behaviour
    from callers that have something else to fall back on.
    """

    unknown = {"root": None, "git_commit": None, "git_ref": None, "git_dirty": None}
    if root is None:
        return unknown, GitSourceState.ABSENT
    try:
        source_root = root.expanduser().resolve()
    except OSError:
        source_root = root.expanduser()
    state = _git_source_state(source_root)
    if state is not GitSourceState.WORKTREE:
        return {**unknown, "root": str(source_root)}, state

    def _run(args: list[str]) -> str | None:
        result = _run_git(source_root, *args)
        if result is None or result.returncode != 0:
            return None
        return result.stdout.strip()

    commit = _run(["rev-parse", "HEAD"]) or None
    branch = _run(["symbolic-ref", "--quiet", "--short", "HEAD"]) or None
    tag = _run(["describe", "--tags", "--exact-match"]) or None
    status = _run(["status", "--porcelain"])
    # Empty successful output proves clean; a failed status leaves it unknown.
    dirty = None if status is None else bool(status)
    return (
        {
            "root": str(source_root),
            "git_commit": commit,
            "git_ref": branch or tag,
            "git_dirty": dirty,
        },
        state,
    )


def git_metadata_for_root(root: Path | None) -> dict[str, Any]:
    return git_source_metadata(root)[0]


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
        result = _run_git(
            source_root, "merge-base", "--is-ancestor", ancestor, descendant
        )
        if result is None:
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
    text = str(value or "").strip()
    if any(character.isspace() for character in text):
        return None
    if "://" in text:
        try:
            url = urlsplit(text)
            # Access validates explicit ports, which hostname alone does not.
            _ = url.port
            if (url.scheme not in {"http", "https", "git", "ssh", "git+ssh", "ssh+git"}
                    or url.hostname != "github.com" or url.query or url.fragment):
                return None
        except ValueError:
            return None
        path = url.path.removeprefix("/")
    else:
        # Git's SCP syntax has no URL authority; anchor its host separately.
        remote = re.fullmatch(
            r"(?:[^/@:\s]+@)?github\.com:(.+)", text, flags=re.IGNORECASE,
        )
        if remote is None:
            return None
        path = remote.group(1)
    match = re.fullmatch(r"([^/\s?#]+/[^/\s?#]+)", path.removesuffix(".git"))
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

    remotes = _run_git(source_root, "remote")
    if remotes is None or remotes.returncode != 0:
        return None

    for remote in remotes.stdout.splitlines():
        remote = remote.strip()
        if not remote:
            continue
        remote_url = _run_git(source_root, "remote", "get-url", remote)
        if remote_url is None:
            continue
        if (
            remote_url.returncode != 0
            or _github_repository_from_remote_url(remote_url.stdout)
            != expected_repository
        ):
            continue
        trusted_ref = f"refs/remotes/{remote}/{expected_ref}"
        resolved = _run_git(source_root, "rev-parse", "--verify", f"{trusted_ref}^{{commit}}")
        commit = (
            resolved.stdout.strip()
            if resolved is not None and resolved.returncode == 0
            else ""
        )
        if commit:
            return {
                "label": f"{expected_repository}@{expected_ref}",
                "root": str(source_root),
                "git_commit": commit,
                "git_ref": f"{remote}/{expected_ref}",
            }
    return None
