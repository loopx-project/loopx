from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

from ...extensions.process_runtime import retire_owned_process

CHANGE_QUALITY_SCOPE_SCHEMA_VERSION = "change_quality_scope_v0"


# The scope reads a checkout the operator may not control. A repository blocked
# on a stale index lock, a network mount or an oversized history must not hold
# the receipt open with no answer, so every read shares one budget.
GIT_PROBE_TIMEOUT_SECONDS = 10.0


class GitProbeUnavailable(ValueError):
    """Git did not answer inside the read budget, or could not start."""


def _retire_owned_probe(process: subprocess.Popen[Any]) -> None:
    """Stop the read's own session, then drop its pipes unread.

    Git runs helpers -- fsmonitor hooks, pagers -- inside the session it starts
    in, so killing the leader alone leaves them behind. An inherited pipe also
    outlives the leader, so the pipes are closed instead of drained: a drain
    would wait on work this call no longer owns.
    """

    try:
        retire_owned_process(process, process_tree_owned=True)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        # Cleanup is best effort. The read's own timeout or cancellation is the
        # result the caller must receive, even when termination is unconfirmed.
        pass


def _git(
    repo_root: Path,
    *args: str,
    text: bool = False,
) -> subprocess.CompletedProcess[Any]:
    # git emits UTF-8 regardless of the host locale. Pin the codec only in
    # text mode: a non-None encoding would silently flip bytes-mode callers
    # into text mode because CPython treats any of text/encoding/errors as
    # text-mode requests.
    argv = ["git", "-C", str(repo_root), *args]
    budget = GIT_PROBE_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            encoding="utf-8" if text else None,
            errors="replace" if text else None,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        raise GitProbeUnavailable(f"git {' '.join(args)} could not start: {exc}") from exc
    try:
        stdout, stderr = process.communicate(timeout=budget)
    except subprocess.TimeoutExpired:
        _retire_owned_probe(process)
        raise GitProbeUnavailable(
            f"git {' '.join(args)} did not answer within {budget:.1f}s"
        ) from None
    except BaseException:
        # A terminal Ctrl-C reaches this process's group, not the session the
        # read started, so the cancellation retires its helpers here and then
        # keeps travelling to its own handler.
        _retire_owned_probe(process)
        raise
    return subprocess.CompletedProcess(
        args=argv, returncode=process.returncode, stdout=stdout, stderr=stderr
    )


def resolve_git_root(repo_path: Path) -> Path:
    candidate = repo_path.expanduser().resolve()
    result = _git(candidate, "rev-parse", "--show-toplevel", text=True)
    root = str(result.stdout or "").strip()
    if result.returncode != 0 or not root:
        detail = str(result.stderr or "").strip()
        raise ValueError(f"repo_path is not a git worktree: {candidate}; {detail}")
    return Path(root).resolve()


def _required_git_text(repo_root: Path, *args: str) -> str:
    result = _git(repo_root, *args, text=True)
    value = str(result.stdout or "").strip()
    if result.returncode != 0 or not value:
        detail = str(result.stderr or "").strip()
        raise ValueError(f"git {' '.join(args)} failed: {detail}")
    return value


def _name_only(repo_root: Path, *args: str) -> list[str]:
    result = _git(repo_root, *args, text=True)
    if result.returncode != 0:
        detail = str(result.stderr or "").strip()
        raise ValueError(f"git {' '.join(args)} failed: {detail}")
    return [
        line.strip().replace("\\", "/")
        for line in str(result.stdout or "").splitlines()
        if line.strip()
    ]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _hash_command_output(
    digest: Any,
    repo_root: Path,
    label: str,
    args: tuple[str, ...],
) -> None:
    result = _git(repo_root, *args)
    if result.returncode != 0:
        detail = bytes(result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise ValueError(f"git {' '.join(args)} failed: {detail}")
    digest.update(label.encode("utf-8"))
    digest.update(b"\0")
    digest.update(bytes(result.stdout or b""))
    digest.update(b"\0")


def _hash_untracked_file(digest: Any, repo_root: Path, relative_path: str) -> None:
    path = repo_root / relative_path
    digest.update(relative_path.encode("utf-8"))
    digest.update(b"\0")
    if path.is_symlink():
        digest.update(b"symlink\0")
        digest.update(path.readlink().as_posix().encode("utf-8"))
        digest.update(b"\0")
        return
    digest.update(b"file\0")
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    digest.update(b"\0")


def build_change_quality_scope(
    *,
    repo_path: Path,
    base_ref: str = "origin/main",
) -> dict[str, Any]:
    repo_root = resolve_git_root(repo_path)
    normalized_base_ref = str(base_ref or "origin/main").strip() or "origin/main"
    if normalized_base_ref.startswith("-"):
        raise ValueError("base_ref cannot start with '-'")
    base_commit = _required_git_text(
        repo_root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{normalized_base_ref}^{{commit}}",
    )
    head_commit = _required_git_text(repo_root, "rev-parse", "--verify", "HEAD^{commit}")
    committed_range = f"{base_commit}...{head_commit}"
    committed = _name_only(
        repo_root,
        "diff",
        "--name-only",
        committed_range,
        "--",
    )
    staged = _name_only(repo_root, "diff", "--name-only", "--cached")
    unstaged = _name_only(repo_root, "diff", "--name-only")
    untracked = _name_only(repo_root, "ls-files", "--others", "--exclude-standard")
    changed_files = _dedupe([*committed, *staged, *unstaged, *untracked])

    digest = hashlib.sha256()
    digest.update(CHANGE_QUALITY_SCOPE_SCHEMA_VERSION.encode("utf-8"))
    digest.update(b"\0")
    digest.update(base_commit.encode("utf-8"))
    digest.update(b"\0")
    digest.update(head_commit.encode("utf-8"))
    digest.update(b"\0")
    _hash_command_output(
        digest,
        repo_root,
        "committed",
        ("diff", "--binary", "--full-index", committed_range, "--"),
    )
    _hash_command_output(
        digest,
        repo_root,
        "staged",
        ("diff", "--binary", "--full-index", "--cached"),
    )
    _hash_command_output(
        digest,
        repo_root,
        "unstaged",
        ("diff", "--binary", "--full-index"),
    )
    for relative_path in sorted(untracked):
        _hash_untracked_file(digest, repo_root, relative_path)

    return {
        "schema_version": CHANGE_QUALITY_SCOPE_SCHEMA_VERSION,
        "base_ref": normalized_base_ref,
        "base_commit": base_commit,
        "head_commit": head_commit,
        "scope_fingerprint": digest.hexdigest(),
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        "sources": {
            "committed": committed,
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
        },
    }
