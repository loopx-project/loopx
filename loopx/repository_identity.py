from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


CANONICAL_REPOSITORY_IDENTITY_PATTERN = re.compile(
    r"^git:(?P<host>[a-z0-9.-]+(?::[0-9]{1,5})?)/"
    r"(?P<path>[A-Za-z0-9._~+/-]+)$"
)
_SAFE_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


def normalize_repository_identity(remote_url_or_identity: str) -> str:
    """Return a credential-free identity shared by common Git transports."""

    raw = str(remote_url_or_identity or "").strip()
    if not raw:
        raise ValueError("repository remote or identity is required")

    canonical = CANONICAL_REPOSITORY_IDENTITY_PATTERN.fullmatch(raw)
    if canonical:
        path = _normalize_repository_path(canonical.group("path"))
        return f"git:{canonical.group('host').casefold()}/{path}"

    scp_match = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", raw)
    if scp_match and "://" not in raw:
        raw = f"ssh://{scp_match.group(1)}/{scp_match.group(2)}"

    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"git", "http", "https", "ssh"}
        or not parsed.hostname
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("repository remote must be a supported credential-free URL")

    host = parsed.hostname.casefold()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("repository remote has an invalid port") from exc
    default_port = {"git": 9418, "http": 80, "https": 443, "ssh": 22}[parsed.scheme]
    if port is not None and port != default_port:
        host = f"{host}:{port}"

    path = _normalize_repository_path(parsed.path)
    return f"git:{host}/{path}"


def _origin_remote(project: Path, git_bin: str) -> str:
    try:
        completed = subprocess.run(
            [git_bin, "-C", str(project), "config", "--get", "remote.origin.url"],
            # This read must not borrow a Chat/RPC caller's open input pipe.
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("project origin remote could not be read") from exc
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ValueError(
            "project has no canonical origin remote; provide a stable LoopX project id"
        )
    return completed.stdout.strip()


def resolve_project_identity(
    project: str | Path,
    *,
    loopx_project_id: str | None = None,
    remote_url: str | None = None,
    git_bin: str = "git",
) -> str:
    """Resolve a durable project identity without using a checkout path."""

    try:
        remote = remote_url or _origin_remote(Path(project), git_bin)
        return normalize_repository_identity(remote)
    except ValueError:
        project_id = str(loopx_project_id or "").strip()
        if not _SAFE_PROJECT_ID.fullmatch(project_id):
            raise
        return f"loopx:{project_id}"


def _normalize_repository_path(value: str) -> str:
    path = re.sub(r"/+", "/", str(value or "")).strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not path or any(segment in {".", ".."} for segment in path.split("/")):
        raise ValueError("repository remote must include a safe repository path")
    return path
