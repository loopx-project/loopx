from __future__ import annotations

import argparse
from collections.abc import MutableMapping
import os
from pathlib import Path
import re

_SEMVER_DIR = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_SYSTEM_BIN_DIRS = (
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
)


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _nvm_node_bin_dirs(home: Path, environ: MutableMapping[str, str]) -> list[Path]:
    dirs: list[Path] = []
    nvm_bin = str(environ.get("NVM_BIN") or "").strip()
    if nvm_bin:
        dirs.append(Path(nvm_bin).expanduser())
    nvm_root = Path(str(environ.get("NVM_DIR") or (home / ".nvm"))).expanduser() / "versions" / "node"
    versioned: list[tuple[tuple[int, int, int], Path]] = []
    try:
        children = list(nvm_root.iterdir()) if _safe_is_dir(nvm_root) else []
    except OSError:
        children = []
    for child in children:
        match = _SEMVER_DIR.fullmatch(child.name)
        if match is not None:
            versioned.append(
                ((int(match.group(1)), int(match.group(2)), int(match.group(3))), child / "bin")
            )
    dirs.extend(path for _ver, path in sorted(versioned, key=lambda item: item[0], reverse=True))
    return dirs


def ensure_agent_runtime_path(
    environ: MutableMapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> str:
    """Append standard non-login user/system CLI directories missing from PATH."""
    env = os.environ if environ is None else environ
    try:
        resolved_home = (home or Path.home()).expanduser()
    except OSError:
        resolved_home = Path("/")
    entries = [part for part in str(env.get("PATH") or "").split(os.pathsep) if part]
    seen = set(entries)
    candidates = [
        *_nvm_node_bin_dirs(resolved_home, env),
        resolved_home / ".local" / "bin",
        resolved_home / ".npm-global" / "bin",
        *_SYSTEM_BIN_DIRS,
    ]
    for candidate in candidates:
        text = str(candidate)
        if text not in seen and _safe_is_dir(candidate):
            entries.append(text)
            seen.add(text)
    updated = os.pathsep.join(entries)
    env["PATH"] = updated
    return updated


def register_agent_runtime_arguments(
    parser: argparse.ArgumentParser,
    *,
    kiro_cli_bin: str,
) -> None:
    """Register the shared Agent runtime selectors for Chat and Dashboard."""
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex CLI executable used for the read-only app-server session.",
    )
    parser.add_argument(
        "--claude-bin",
        default="claude",
        help="Claude Code CLI executable used for read-only Agent sessions.",
    )
    parser.add_argument(
        "--kiro-cli-bin",
        default=kiro_cli_bin,
        help="Kiro CLI executable used for read-only ACP Agent sessions (`<bin> acp`).",
    )
    parser.add_argument(
        "--lark-cli-bin",
        help=(
            "Optional explicit lark-cli executable. When omitted, LoopX uses its "
            "bounded runtime discovery order."
        ),
    )
