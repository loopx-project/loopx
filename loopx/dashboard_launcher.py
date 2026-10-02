from __future__ import annotations

import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from urllib.parse import quote
import webbrowser

from .kiro_cli_goal_mode import KIRO_CLI_BIN
from .release_manifest import release_runtime_identity


DASHBOARD_CHAT_PATH = "/chat/"
EXPECTED_CHAT_SCHEMA_VERSION = "loopx_chat_capabilities_v1"
CHAT_PROBE_TIMEOUT_SECONDS = 0.75
MAX_PROBE_RESPONSE_BYTES = 1024 * 1024


def dashboard_release_root() -> Path:
    configured_root = os.environ.get("LOOPX_RELEASE_ROOT")
    if configured_root:
        return Path(configured_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def default_packaged_assets_dir() -> Path:
    return Path(__file__).resolve().parent / "web" / "chat"


def _probe_existing_chat(
    host: str,
    port: int,
    *,
    goal_subagent_configuration_enabled: bool | None = None,
) -> str:
    """Return whether the target port already serves LoopX Chat.

    The result is one of ``matching`` (the current LoopX runtime is running),
    ``stale`` (another LoopX release owns the port), ``foreign`` (some other
    process owns the port), or ``unavailable`` (the port is free). Both the
    top-level capability fingerprint and release identity must match so a
    mismatched frontend/backend pair is never silently reused.
    """
    # Imported here rather than at module level: the launcher probes a port that
    # may hold no LoopX process at all, and the server module's import graph
    # costs ~110ms on this path.
    from .chat_server import CHAT_CAPABILITIES_PATH

    try:
        connection = http.client.HTTPConnection(
            host,
            port,
            timeout=CHAT_PROBE_TIMEOUT_SECONDS,
        )
        try:
            connection.request(
                "GET",
                CHAT_CAPABILITIES_PATH,
                headers={"Connection": "close"},
            )
            response = connection.getresponse()
            payload = response.read(MAX_PROBE_RESPONSE_BYTES)
            status = response.status
        finally:
            connection.close()
    except (ConnectionRefusedError, TimeoutError):
        # Some native Windows execution environments time out instead of
        # returning WSAECONNREFUSED for an unused loopback port. Treat that as
        # non-reusable and let the later server bind remain authoritative.
        return "unavailable"
    except (OSError, http.client.HTTPException):
        return "foreign"
    if status != 200:
        return "foreign"
    try:
        capabilities = json.loads(payload)
    except (UnicodeDecodeError, ValueError):
        return "foreign"
    if not isinstance(capabilities, dict):
        return "foreign"
    if (
        capabilities.get("ok") is not True
        or capabilities.get("schema_version") != EXPECTED_CHAT_SCHEMA_VERSION
    ):
        return "foreign"
    expected_identity = release_runtime_identity(dashboard_release_root())
    if capabilities.get("runtime_identity") != expected_identity:
        return "stale"
    if goal_subagent_configuration_enabled is not None and (
        capabilities.get("goal_subagent_configuration") == "preview_locked"
    ) != goal_subagent_configuration_enabled:
        return "configuration_mismatch"
    return "matching"


def _listener_pids(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "cannot inspect the existing LoopX Chat listener; leave the port owner untouched"
        ) from exc
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            "cannot inspect the existing LoopX Chat listener; leave the port owner untouched"
        )
    return sorted(
        {
            int(line)
            for line in result.stdout.splitlines()
            if line.strip().isdigit() and int(line) > 1
        }
    )


def _is_same_user_loopx_chat_process(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "uid=", "-o", "command="],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    fields = result.stdout.strip().split(maxsplit=1)
    if len(fields) != 2 or not fields[0].isdigit():
        return False
    command = fields[1]
    return (
        int(fields[0]) == os.getuid()
        and "loopx" in command.casefold()
        and re.search(r"(?:^|\s)chat(?:\s|$)", command, re.IGNORECASE) is not None
    )


def replace_existing_loopx_chat(
    host: str,
    port: int,
    *,
    timeout_seconds: float = 5.0,
) -> str:
    """Replace one exact same-user LoopX Chat listener for managed restart.

    This is deliberately narrower than generic port takeover: the public Chat
    capabilities fingerprint, exact listener PID, process owner, and command
    must all identify LoopX Chat before a bounded SIGTERM is sent. Foreign or
    unverifiable listeners are left untouched.
    """

    normalized_host = host.strip().strip("[]")
    try:
        loopback = normalized_host.casefold() == "localhost" or ipaddress.ip_address(
            normalized_host
        ).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError("managed LoopX Chat replacement requires an explicit loopback host")
    existing = _probe_existing_chat(host, port)
    if existing == "unavailable":
        return existing
    if existing == "foreign":
        raise RuntimeError(
            f"port {port} is owned by an unverified service; leave it running and choose another port"
        )
    pids = _listener_pids(port)
    if len(pids) != 1:
        raise RuntimeError(
            f"expected one verified LoopX Chat listener on port {port}; found {len(pids)} and stopped none"
        )
    pid = pids[0]
    if pid == os.getpid() or not _is_same_user_loopx_chat_process(pid):
        raise RuntimeError(
            f"the listener on port {port} is not a verified same-user LoopX Chat process; stopped none"
        )
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        raise RuntimeError(
            f"could not stop the verified LoopX Chat listener on port {port}"
        ) from exc
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    while time.monotonic() < deadline:
        if pid not in _listener_pids(port):
            return existing
        time.sleep(0.1)
    raise RuntimeError(
        f"the verified LoopX Chat listener on port {port} did not stop after SIGTERM; no stronger signal was sent"
    )


def launch_dashboard(
    *,
    registry_path: Path | None = None,
    runtime_root_override: Path | None = None,
    scan_roots: list[Path] | None = None,
    limit: int = 20,
    host: str = "127.0.0.1",
    port: int = 8767,
    goal_id: str | None = None,
    codex_bin: str = "codex",
    claude_bin: str = "claude",
    kiro_cli_bin: str = KIRO_CLI_BIN,
    lark_cli_bin: str | None = None,
    assets_dir: Path | None = None,
    verbose: bool = False,
    open_browser: bool = True,
    prefer_dev: bool = False,
    enable_goal_subagent_configuration: bool = False,
) -> int:
    release_root = dashboard_release_root()
    dev_launcher = release_root / "scripts" / "dashboard-dev.sh"
    if (prefer_dev or os.environ.get("LOOPX_DASHBOARD_DEV") == "1") and dev_launcher.is_file():
        environment = dict(os.environ)
        if enable_goal_subagent_configuration:
            environment["LOOPX_ENABLE_GOAL_SUBAGENT_CONFIGURATION"] = "1"
        return subprocess.call(
            ["bash", str(dev_launcher)],
            cwd=release_root,
            env=environment,
        )

    if assets_dir is None:
        from .presentation.chat_bundle import validate_bundle
        validate_bundle(default_packaged_assets_dir(), source_root=Path(__file__).resolve().parents[1])

    existing_chat = _probe_existing_chat(
        host,
        port,
        goal_subagent_configuration_enabled=(
            enable_goal_subagent_configuration
        ),
    )
    if existing_chat == "matching":
        url = f"http://{host}:{port}{DASHBOARD_CHAT_PATH}"
        if goal_id:
            url = f"{url}?goalId={quote(goal_id, safe='')}"
        print(f"Reusing existing LoopX Chat service at {url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        return 0
    if existing_chat == "foreign":
        raise RuntimeError(
            f"port {port} is already used by a service that is not LoopX Chat; "
            "stop that service or start LoopX on another port with --port."
        )
    if existing_chat == "stale":
        raise RuntimeError(
            f"port {port} is serving LoopX Chat from a different installed runtime; "
            "stop the old `loopx dashboard` or desktop app, then retry so the "
            "current release can start its matching service."
        )
    if existing_chat == "configuration_mismatch":
        raise RuntimeError(
            f"port {port} is serving LoopX Chat with a different Goal "
            "sub-agent configuration gate; restart it with the requested setting."
        )

    from .chat_server import serve_chat

    resolved_assets = assets_dir or default_packaged_assets_dir()
    if not (resolved_assets / "index.html").is_file():
        raise RuntimeError(
            f"LoopX dashboard web bundle is missing: {resolved_assets}. "
            "Please ensure the package is properly installed with package-data or run npm run build."
        )

    serve_chat(
        registry_path=registry_path,
        runtime_root_override=runtime_root_override,
        scan_roots=scan_roots,
        limit=limit,
        host=host,
        port=port,
        goal_id=goal_id,
        codex_bin=codex_bin,
        claude_bin=claude_bin,
        kiro_cli_bin=kiro_cli_bin,
        lark_cli_bin=lark_cli_bin,
        assets_dir=resolved_assets,
        verbose=verbose,
        open_browser=open_browser,
        enable_goal_subagent_configuration=(
            enable_goal_subagent_configuration
        ),
    )
    return 0
