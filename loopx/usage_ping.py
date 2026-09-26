"""CLI host adapter. TypeScript owns telemetry policy, state, payloads and I/O."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .paths import DEFAULT_RUNTIME_ROOT

STATE_FILENAME = "usage-ping.json"
_ENTRY = Path(__file__).parent / "control_plane/runtime/usage_statistics_cli.ts"


def state_path(runtime_root: Path | None = None) -> Path:
    """Machine-local choice is deliberately independent of a Goal runtime root."""
    return Path(runtime_root or DEFAULT_RUNTIME_ROOT) / STATE_FILENAME


def install_channel() -> str:
    location = Path(__file__).resolve()
    parts = location.parts
    if "site-packages" in parts or "dist-packages" in parts:
        return "pip"
    if "releases" in parts:
        return "local_release"
    return "source" if (location.parent.parent / ".git").exists() else "unknown"


def _request(action: str, path: Path, **fields: Any) -> dict[str, Any]:
    return {"action": action, "path": str(path), "facts": {
        "version": __version__, "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "channel": install_channel(),
    }, **fields}


def _command() -> list[str]:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Usage settings require the supported Node.js runtime.")
    return [node, "--no-warnings", "--experimental-strip-types", str(_ENTRY)]


def control(action: str, path: Path | None = None, **fields: Any) -> dict[str, Any]:
    result = subprocess.run(_command(), input=json.dumps(_request(action, path or state_path(), **fields)),
                            capture_output=True, text=True, timeout=4, check=False)
    payload = json.loads(result.stdout)
    if result.returncode or not isinstance(payload, dict) or "error" in payload:
        raise RuntimeError("Usage settings unavailable. Inspect the local usage-ping.json; disable can repair invalid state.")
    return payload


def begin(command: str) -> tuple[str, float] | None:
    """Read a small host hint; do not start a synchronous Node process on warm commands."""
    # Negative-only scheduling hints for common unattended environments. These
    # cannot authorize collection; TS still checks every supported switch value.
    if os.environ.get("LOOPX_USAGE_PING") == "0" or os.environ.get("DO_NOT_TRACK") == "1" or os.environ.get("CI") == "true":
        return None
    if command == "usage-ping":
        return None
    try:
        path = state_path()
        state = json.loads(path.read_text()) if path.exists() else {}
        if state.get("consent") == "disabled":
            return None
        if not state.get("notice"):
            # Unattended machines remain silent until the owner sees the notice
            # or explicitly enables from CLI/settings. JSON stdout stays clean.
            if not sys.stderr.isatty():
                return None
            projection = control("status", path)
            if projection["blocked_by"] != "notice_required":
                return None
            print(projection["disclosure"], file=sys.stderr, flush=True)
            control("acknowledge", path, notice=projection["notice"])
            return None  # First invocation only discloses; no measurement/send.
        generation = state.get("generation")
        if not isinstance(generation, str) or not generation:
            return None
        # Scheduling hint only; TS atomically owns eligibility and the daily claim.
        if state.get("last_attempt_day") != datetime.now(timezone.utc).date().isoformat():
            _detach(_request("start", path, generation=generation))
        return generation, time.monotonic()
    except Exception:
        return None


def finish(ticket: tuple[str, float] | None, command: str, code: int, error: BaseException | None = None) -> None:
    """Detach bounded local observation; never read args, output or error text."""
    if ticket is None:
        return
    try:
        outcome, category = ("ok", "none") if code == 0 else ("failed", "command_failed")
        if isinstance(error, KeyboardInterrupt):
            outcome, category = "cancelled", "interrupted"
        elif isinstance(error, TimeoutError):
            outcome, category = "failed", "timeout"
        elif isinstance(error, ConnectionError):
            outcome, category = "failed", "connection"
        request = _request("observe", state_path(), generation=ticket[0], feature=command if len(command) <= 64 else "other",
                           outcome=outcome, error=category, elapsed_ms=max(0, (time.monotonic() - ticket[1]) * 1000))
        _detach(request)
    except Exception:
        pass  # Telemetry cannot replace the command's result.


def _detach(request: dict[str, Any]) -> None:
    kwargs: dict[str, Any] = {"stdin": subprocess.PIPE, "stdout": subprocess.DEVNULL,
                              "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    child = subprocess.Popen(_command(), **kwargs)
    assert child.stdin is not None
    child.stdin.write(json.dumps(request).encode())
    child.stdin.close()
