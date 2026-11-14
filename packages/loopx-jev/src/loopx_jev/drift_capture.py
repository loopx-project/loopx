"""Read bounded, explicitly scoped workspace evidence without modifying Git."""

from __future__ import annotations

import difflib
import hashlib
import os
import stat
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any, BinaryIO

from .protocol import request_bytes

MAX_FILES = 32
MAX_BYTES = 32768


def digest(value: Any) -> str:
    return hashlib.sha256(request_bytes(value)).hexdigest()


def git(repo: Path, *args: str) -> str:
    # File-backed output avoids accumulating an unbounded Git response in RAM.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo), *args],
            stdout=output,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        output.seek(0)
        raw = output.read(131073)
    if result.returncode or len(raw) > 131072:
        raise ValueError("git_evidence_unavailable")
    return raw.decode("utf-8")


def validate_paths(repo: Path, paths: list[str]) -> list[str]:
    if not paths or len(paths) > MAX_FILES or len(set(paths)) != len(paths):
        raise ValueError("scope_requires_one_to_32_unique_files")
    for name in paths:
        path = Path(name)
        parts = (
            tuple(part.casefold() for part in path.parts)
            if os.name == "nt"
            else path.parts
        )
        if (
            not name
            or path.is_absolute()
            or ".." in path.parts
            or ".git" in parts
            or (
                os.name == "nt"
                and any(":" in part or part.endswith((" ", ".")) for part in path.parts)
            )
            or name != path.as_posix()
            or any(ord(char) < 32 for char in name)
        ):
            raise ValueError("invalid_scope_path")
        target = repo / path
        if target.is_symlink() or not target.resolve().is_relative_to(repo):
            raise ValueError("scope_escapes_workspace")
    return sorted(paths)


def _windows_identity(
    info: os.stat_result,
    *,
    directory: bool = False,
) -> tuple[int, int, int, int]:
    attributes = getattr(info, "st_file_attributes", None)
    if attributes is None:
        raise ValueError("unsupported_capture_platform")
    if stat.S_ISLNK(info.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("scope_escapes_workspace")
    if not (stat.S_ISDIR if directory else stat.S_ISREG)(info.st_mode):
        raise ValueError("scope_requires_regular_files")
    if not info.st_dev or not info.st_ino:
        raise ValueError("capture_identity_unavailable")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _windows_descriptor(target: Path, *, directory: bool = False) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    filename = os.path.abspath(target)
    if not filename.startswith("\\\\?\\"):
        filename = (
            "\\\\?\\UNC\\" + filename[2:]
            if filename.startswith("\\\\")
            else "\\\\?\\" + filename
        )
    # Directories deny write/delete sharing to pin traversal; OPEN_REPARSE_POINT
    # opens a raced-in link itself. BACKUP_SEMANTICS permits directory handles.
    flags = 0x00200000 | (0x02000000 if directory else 0)
    handle = create(
        filename, 0x80000000, 0x1 if directory else 0x7, None, 3, flags, None
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        kernel.CloseHandle(handle)
        raise


def _windows_open(target: Path) -> BinaryIO:
    descriptor = _windows_descriptor(target)
    try:
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _windows_final_path(descriptor: int) -> Path:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    final_path = kernel.GetFinalPathNameByHandleW
    final_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    final_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    # FILE_NAME_NORMALIZED | VOLUME_NAME_DOS keeps root and file names comparable.
    length = final_path(msvcrt.get_osfhandle(descriptor), buffer, len(buffer), 0)
    if not 0 < length < len(buffer):
        raise ValueError("capture_path_unavailable")
    return Path(buffer.value)


def _windows_capture(repo: Path, target: Path) -> tuple[bytes, int] | None:
    with ExitStack() as parents:
        current = repo
        canonical_repo = repo
        for part in ("", *target.relative_to(repo).parts[:-1]):
            current = current / part
            try:
                expected = _windows_identity(os.lstat(current), directory=True)[:2]
            except FileNotFoundError:
                if not part:
                    raise ValueError("capture_path_unavailable") from None
                return None
            descriptor = _windows_descriptor(current, directory=True)
            parents.callback(os.close, descriptor)
            if _windows_identity(os.fstat(descriptor), directory=True)[:2] != expected:
                raise ValueError("workspace_changed_during_capture")
            canonical = _windows_final_path(descriptor)
            if not part:
                canonical_repo = canonical
            # Validate the known ancestor plus the full suffix before accepting absence.
            canonical = canonical / target.relative_to(current)
            if not canonical.is_relative_to(canonical_repo):
                raise ValueError("scope_escapes_workspace")
            validate_paths(
                canonical_repo, [canonical.relative_to(canonical_repo).as_posix()]
            )
        try:
            observed = os.lstat(target)
        except FileNotFoundError:
            return None
        expected_file = _windows_identity(observed)
        with _windows_open(target) as stream:
            if _windows_identity(os.fstat(stream.fileno())) != expected_file:
                raise ValueError("workspace_changed_during_capture")
            canonical = _windows_final_path(stream.fileno())
            if not canonical.is_relative_to(canonical_repo):
                raise ValueError("scope_escapes_workspace")
            validate_paths(
                canonical_repo, [canonical.relative_to(canonical_repo).as_posix()]
            )
            raw = stream.read(MAX_BYTES + 1)
            opened = os.fstat(stream.fileno())
            if (
                _windows_identity(opened) != expected_file
                or _windows_identity(os.lstat(target)) != expected_file
            ):
                raise ValueError("workspace_changed_during_capture")
            return raw, opened.st_mode & 0o111


def _index(repo: Path, paths: list[str]) -> str:
    # A case-insensitive workspace opens RECORD.txt as the tracked record.txt;
    # read that entry the same way so its staged changes are not missed.
    try:
        icase = (
            git(repo, "config", "--type=bool", "--get", "core.ignorecase").strip()
            == "true"
        )
    except ValueError:  # unset: Git matches paths exactly
        icase = False
    if icase:
        return git(
            repo,
            "ls-files",
            "--stage",
            "-z",
            "--",
            *(f":(icase,literal){name}" for name in paths),
        )
    return git(repo, "--literal-pathspecs", "ls-files", "--stage", "-z", "--", *paths)


def capture(repo: Path, paths: list[str]) -> dict[str, Any]:
    windows = os.name == "nt" and not hasattr(os, "O_NOFOLLOW")
    if not windows and not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("unsupported_capture_platform")
    repo = repo.resolve()
    paths = validate_paths(repo, paths)
    if Path(git(repo, "rev-parse", "--show-toplevel").strip()).resolve() != repo:
        raise ValueError("workspace_must_be_git_root")
    before = git(repo, "rev-parse", "HEAD").strip()
    index = _index(repo, paths)
    files: dict[str, Any] = {}
    size = 0
    for name in paths:
        target = repo / name
        if target.is_symlink() or not target.resolve().is_relative_to(repo):
            raise ValueError("scope_escapes_workspace")
        if windows:
            captured = _windows_capture(repo, target)
            if captured is None:
                files[name] = None
                continue
            raw, mode = captured
        else:
            if not target.exists():
                files[name] = None
                continue
            if not target.is_file():
                raise ValueError("scope_requires_regular_files")
            # O_NOFOLLOW also rejects a final-component symlink introduced after check.
            with os.fdopen(
                os.open(target, os.O_RDONLY | os.O_NOFOLLOW), "rb"
            ) as stream:
                raw = stream.read(MAX_BYTES + 1)
                mode = os.fstat(stream.fileno()).st_mode & 0o111
        size += len(raw)
        if size > MAX_BYTES or b"\0" in raw:
            raise ValueError("oversized_or_binary_scope")
        files[name] = {"text": raw.decode("utf-8"), "executable": bool(mode)}
    if before != git(repo, "rev-parse", "HEAD").strip() or index != _index(repo, paths):
        raise ValueError("workspace_changed_during_capture")
    return {
        "head": before,
        "index_digest": digest(index),
        "files": files,
        "content_digest": digest(files),
    }


def stable_capture(repo: Path, paths: list[str]) -> dict[str, Any]:
    first = capture(repo, paths)
    if first != capture(repo, paths):
        raise ValueError("workspace_changed_during_capture")
    return first


def delta(previous: dict[str, Any], current: dict[str, Any]) -> str:
    """Compare effective files across checkpoints, independent of commit timing."""
    changes = []
    for name, after in current["files"].items():
        before = previous["files"].get(name)
        if before == after:
            continue
        old = before["text"] if before else ""
        new = after["text"] if after else ""
        # Keep add/delete/empty-file/mode transitions even without changed lines.
        changes.append(
            f"File {name}: present {before is not None} -> {after is not None}; "
            f"executable {bool(before and before['executable'])} -> "
            f"{bool(after and after['executable'])}; "
            f"final_newline {old.endswith(chr(10))} -> {new.endswith(chr(10))}\n"
        )
        changes.extend(
            difflib.unified_diff(
                [line + "\n" for line in old.splitlines()],
                [line + "\n" for line in new.splitlines()],
                fromfile="before/" + name,
                tofile="after/" + name,
            )
        )
    old_evidence = previous.get("external_evidence", [])
    new_evidence = current.get("external_evidence", [])
    if old_evidence != new_evidence:
        changes.append(
            "Explicit evidence files changed (contents are evidence, not verified claims):\n"
        )
        changes.extend(
            difflib.unified_diff(
                [request_bytes(old_evidence).decode() + "\n"],
                [request_bytes(new_evidence).decode() + "\n"],
                fromfile="before/explicit-evidence",
                tofile="after/explicit-evidence",
            )
        )
    text = "".join(changes)
    if len(text.encode()) > MAX_BYTES:
        raise ValueError("delta_exceeds_budget")
    return text
