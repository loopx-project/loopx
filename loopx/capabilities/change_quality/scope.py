from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any


CHANGE_QUALITY_SCOPE_SCHEMA_VERSION = "change_quality_scope_v0"


def _git(
    repo_root: Path,
    *args: str,
    text: bool = False,
) -> subprocess.CompletedProcess[Any]:
    # git emits UTF-8 regardless of the host locale. Pin the codec only in
    # text mode: a non-None encoding would silently flip bytes-mode callers
    # into text mode because CPython treats any of text/encoding/errors as
    # text-mode requests.
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=False,
            text=text,
            encoding="utf-8" if text else None,
            errors="replace" if text else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        empty = "" if text else b""
        return subprocess.CompletedProcess(
            args=["git", "-C", str(repo_root), *args],
            returncode=124,
            stdout=empty,
            stderr="git command timed out after 30s" if text else b"git command timed out after 30s",
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
