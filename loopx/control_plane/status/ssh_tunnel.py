"""Ensure an SSH-tunneled LoopX status source is reachable.

The personal-workspace source switcher lets an owner point at a configured
SSH host that runs LoopX on its remote loopback. This module automatically
opens the local tunnel and starts the remote status service when missing, so
switching sources in the app does not require manual ssh commands.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

from .ssh_host_catalog import configured_ssh_host_aliases


REMOTE_STATUS_PORT = 8766
_MIN_TUNNEL_PORT = 1024
_MAX_TUNNEL_PORT = 65535
_DEFAULT_WAIT_SECONDS = 12.0

# Fixed remote bootstrap: resolve the loopx binary and start a loopback
# status server. Only the (already validated) SSH alias is interpolated as an
# ssh argv; this script is never built from user text.
_REMOTE_BOOTSTRAP = (
    "bin=\"$HOME/.local/bin/loopx\"; "
    "[ -x \"$bin\" ] || bin=\"$(command -v loopx || true)\"; "
    "[ -n \"$bin\" ] || { echo 'loopx not found on remote' >&2; exit 1; }; "
    "nohup \"$bin\" "
    "serve-status --global-registry --host 127.0.0.1 --port 8766 --limit 80 "
    ">/tmp/loopx-serve-status.log 2>&1 &"
)


def _loopback_status_ok(port: int, *, timeout: float = 8.0) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/status.json", timeout=timeout
        ) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _remote_status_ok(alias: str, *, timeout: float = 5.0) -> bool:
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=3",
                alias,
                "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8766/status.json",
            ],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip().endswith("200")


def _start_remote_status(alias: str) -> None:
    subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", alias, _REMOTE_BOOTSTRAP],
        check=True,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=25,
    )


def ensure_ssh_source(
    alias: str,
    local_port: int,
    *,
    ssh_config_path: Path | None = None,
    wait_seconds: float = _DEFAULT_WAIT_SECONDS,
) -> dict[str, object]:
    """Open the tunnel and remote status service for a configured SSH host."""
    if not isinstance(alias, str) or not alias:
        raise ValueError("host alias is required")
    aliases = configured_ssh_host_aliases(ssh_config_path)
    if alias not in aliases:
        raise ValueError(f"unknown SSH host alias: {alias}")
    if not isinstance(local_port, int) or not (
        _MIN_TUNNEL_PORT <= local_port <= _MAX_TUNNEL_PORT
    ):
        raise ValueError("local tunnel port must be an integer in 1024..65535")

    tunnel_required = not _loopback_status_ok(local_port)
    remote_started = False
    if tunnel_required:
        subprocess.run(
            [
                "ssh",
                "-f",
                "-N",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "ServerAliveInterval=30",
                "-L",
                f"{local_port}:127.0.0.1:{REMOTE_STATUS_PORT}",
                alias,
            ],
            check=True,
            timeout=30,
        )
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if _loopback_status_ok(local_port):
                break
            time.sleep(0.25)
        if not _loopback_status_ok(local_port):
            if not _remote_status_ok(alias):
                _start_remote_status(alias)
                remote_started = True
            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline:
                if _loopback_status_ok(local_port):
                    break
                time.sleep(0.25)

    if not _loopback_status_ok(local_port):
        raise ValueError(
            f"SSH tunnel source is not reachable: http://127.0.0.1:{local_port}/status.json"
        )

    return {
        "ok": True,
        "status_url": f"http://127.0.0.1:{local_port}/status.json",
        "tunnel_required": tunnel_required,
        "remote_started": remote_started,
    }
