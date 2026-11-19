from __future__ import annotations

from collections.abc import Mapping, Sequence
import glob
from pathlib import Path
import re
import shlex
from typing import cast


SSH_HOST_CATALOG_SCHEMA_VERSION = "ssh_host_catalog_v0"
SSH_HOST_CATALOG_PATH = "/ssh-hosts"
_SAFE_HOST_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_MAX_CONFIG_FILES = 64
_MAX_HOST_ALIASES = 512


def _tokens(line: str) -> list[str]:
    try:
        return shlex.split(line, comments=True, posix=True)
    except ValueError:
        return []


def _include_paths(pattern: str, *, ssh_directory: Path) -> list[Path]:
    expanded = Path(pattern).expanduser()
    if not expanded.is_absolute():
        expanded = ssh_directory / expanded
    return [Path(item) for item in sorted(glob.glob(str(expanded)))]


def configured_ssh_host_aliases(config_path: Path | None = None) -> list[str]:
    """Return explicit, shell-safe OpenSSH Host aliases without exposing config details."""

    root_config = (config_path or (Path.home() / ".ssh" / "config")).expanduser()
    ssh_directory = root_config.parent
    visited: set[Path] = set()
    aliases: list[str] = []
    seen_aliases: set[str] = set()

    def visit(candidate: Path) -> None:
        if len(visited) >= _MAX_CONFIG_FILES or len(aliases) >= _MAX_HOST_ALIASES:
            return
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError):
            return
        if resolved in visited or not resolved.is_file():
            return
        visited.add(resolved)
        try:
            lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return

        for line in lines:
            parts = _tokens(line)
            if not parts:
                continue
            directive = parts[0].lower()
            if directive == "include":
                for pattern in parts[1:]:
                    for included in _include_paths(pattern, ssh_directory=ssh_directory):
                        visit(included)
                continue
            if directive != "host":
                continue
            for alias in parts[1:]:
                if (
                    not _SAFE_HOST_ALIAS.fullmatch(alias)
                    or ".." in alias
                    or alias in seen_aliases
                ):
                    continue
                seen_aliases.add(alias)
                aliases.append(alias)
                if len(aliases) >= _MAX_HOST_ALIASES:
                    return

    visit(root_config)

    return aliases


def project_ssh_host_catalog(
    configured_aliases: Sequence[str],
    observations: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Project `ssh_host_catalog_v0` with optional L0 SSH observations via the TypeScript owner."""

    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    try:
        return cast(
            dict[str, object],
            effect_runtime_result(
                "collaboration.session_fabric.project_ssh_host_catalog",
                {
                    "configured_aliases": list(configured_aliases),
                    "observations": [dict(item) for item in (observations or ())],
                },
            ),
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc


def ssh_host_catalog_payload(
    config_path: Path | None = None,
    *,
    observations: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    aliases = configured_ssh_host_aliases(config_path)
    if observations:
        return project_ssh_host_catalog(aliases, observations)
    return {
        "ok": True,
        "schema_version": SSH_HOST_CATALOG_SCHEMA_VERSION,
        "hosts": [{"alias": alias} for alias in aliases],
    }
