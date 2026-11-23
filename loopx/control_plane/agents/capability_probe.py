"""Automatic host runtime capability readiness probes.

Deterministic local readiness checks only: probes never grant owner-held
permissions (`credentials`, `production_access`), never mutate Goal policy, and
never override explicit `unavailable` revocations recorded in capability memory.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import os
from pathlib import Path
import shutil
import subprocess

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


def host_network_sandbox_disabled(env: Mapping[str, str] | None = None) -> bool:
    """Return True when the host environment explicitly disables network access."""
    environment = os.environ if env is None else env
    for key in ("CODEX_SANDBOX_NETWORK_DISABLED", "LOOPX_OFFLINE"):
        if str(environment.get(key) or "").strip().lower() in _TRUTHY:
            return True
    return False


def auto_capability_probe_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Allow explicit opt-out via LOOPX_AUTO_CAPABILITY_PROBE=0 while on by default."""
    environment = os.environ if env is None else env
    raw = str(environment.get("LOOPX_AUTO_CAPABILITY_PROBE") or "").strip().lower()
    if raw in _FALSY:
        return False
    return True


def _gh_hosts_config_paths(env: Mapping[str, str]) -> tuple[Path, ...]:
    configured = str(env.get("GH_CONFIG_DIR") or "").strip()
    if configured:
        return (Path(configured).expanduser() / "hosts.yml",)
    candidates: list[Path] = []
    home_raw = str(env.get("HOME") or "").strip()
    home = Path(home_raw).expanduser() if home_raw else Path.home()
    xdg = str(env.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        candidates.append(Path(xdg).expanduser() / "gh" / "hosts.yml")
    candidates.append(home / ".config" / "gh" / "hosts.yml")
    appdata = str(env.get("APPDATA") or "").strip()
    if appdata:
        candidates.append(Path(appdata).expanduser() / "GitHub CLI" / "hosts.yml")
    return tuple(candidates)


@lru_cache(maxsize=8)
def _probe_gh_auth_subprocess(gh_executable: str, config_dir: str) -> bool:
    env = dict(os.environ)
    if config_dir:
        env["GH_CONFIG_DIR"] = config_dir
    try:
        completed = subprocess.run(
            [gh_executable, "auth", "status"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=1.5,
            check=False,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def probe_github_cli_evidence_ready(
    *,
    allow_subprocess: bool = False,
    env: Mapping[str, str] | None = None,
) -> tuple[bool, str | None]:
    """Check whether `gh` CLI can poll external GitHub PR/evidence metadata."""
    environment = os.environ if env is None else env
    if host_network_sandbox_disabled(environment):
        return False, "sandbox_network_disabled"
    path_env = environment.get("PATH")
    gh_executable = shutil.which("gh", path=path_env)
    if not gh_executable:
        return False, "gh_cli_not_installed"
    for token_key in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GH_ENTERPRISE_TOKEN",
    ):
        if str(environment.get(token_key) or "").strip():
            return True, None
    for hosts_file in _gh_hosts_config_paths(environment):
        try:
            if hosts_file.is_file() and hosts_file.stat().st_size > 0:
                return True, None
        except OSError:
            continue
    if allow_subprocess:
        config_dir = str(environment.get("GH_CONFIG_DIR") or "").strip()
        if _probe_gh_auth_subprocess(gh_executable, config_dir):
            return True, None
    return False, "gh_auth_not_configured"


def probe_host_runtime_capabilities(
    *,
    allow_subprocess: bool = False,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Auto-detect default host runtime capabilities without mutating durable state."""
    environment = os.environ if env is None else env
    if not auto_capability_probe_enabled(environment):
        return []
    probed: list[str] = []
    gh_ready, _ = probe_github_cli_evidence_ready(
        allow_subprocess=allow_subprocess,
        env=environment,
    )
    if gh_ready:
        probed.append("external_evidence_poll")
    return probed
