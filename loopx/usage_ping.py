"""Opt-in anonymous usage ping.

Off unless the machine owner runs ``loopx usage-ping enable``. When enabled,
at most one small JSON document per UTC day is posted to the project
collector: a random installation id plus LoopX version, OS family, Python
minor version and install channel. Nothing about projects, goals, paths,
hostnames, accounts or command lines is read or sent.

The CLI hot path only reads one small state file; the network request runs in
a detached child process so a slow or unreachable collector never delays or
fails a LoopX command. See docs/reference/usage-ping.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .paths import DEFAULT_RUNTIME_ROOT

PAYLOAD_SCHEMA = "loopx_usage_ping_v0"
STATE_SCHEMA = "loopx_usage_ping_state_v0"
STATE_FILENAME = "usage-ping.json"
# Empty until the project collector (apps/usage-collector) is deployed; while it
# is empty an enabled ping records consent but sends nothing.
DEFAULT_ENDPOINT = ""
ENDPOINT_ENV = "LOOPX_USAGE_PING_ENDPOINT"
SWITCH_ENV = "LOOPX_USAGE_PING"
REQUEST_TIMEOUT_SECONDS = 3.0
MAX_ERROR_CHARS = 120
OS_FAMILIES = {"darwin": "darwin", "linux": "linux", "win32": "windows", "cygwin": "windows"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_LOCAL_HTTP = re.compile(r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?/")


def state_path(runtime_root: Path | None = None) -> Path:
    """Machine-level state; deliberately not scoped by a project --runtime-root."""
    return Path(runtime_root or DEFAULT_RUNTIME_ROOT) / STATE_FILENAME


def _today(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()


def load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("schema") == STATE_SCHEMA else {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def enable(path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    state = load_state(path)
    install_id = state.get("install_id") if state.get("consent") == "enabled" else None
    state = {
        "schema": STATE_SCHEMA,
        "consent": "enabled",
        "install_id": install_id or str(uuid.uuid4()),
        "decided_on": _today(now),
        "last_attempt_day": None,
        "last_sent_day": None,
    }
    save_state(path, state)
    return state


def disable(path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    # Forget the id so a later re-enable is a new, unlinkable installation.
    state = {"schema": STATE_SCHEMA, "consent": "disabled", "decided_on": _today(now)}
    save_state(path, state)
    return state


def env_block_reason(env: Mapping[str, str]) -> str | None:
    if env.get(SWITCH_ENV, "").strip().lower() in _FALSE_VALUES:
        return f"{SWITCH_ENV}={env[SWITCH_ENV]}"
    if env.get("DO_NOT_TRACK", "").strip() not in ("", "0"):
        return "DO_NOT_TRACK"
    if env.get("CI", "").strip().lower() not in ("", "0", "false"):
        return "CI"
    return None


def resolve_endpoint(env: Mapping[str, str]) -> str:
    endpoint = env.get(ENDPOINT_ENV, "").strip() or DEFAULT_ENDPOINT
    if endpoint.startswith("https://") or _LOCAL_HTTP.match(endpoint):
        return endpoint
    return ""


def install_channel(package_file: str | None = None) -> str:
    location = Path(package_file or __file__).resolve().as_posix()
    if "/site-packages/" in location or "/dist-packages/" in location:
        return "pip"
    if "/releases/" in location:
        return "local_release"
    if (Path(location).parents[1] / ".git").exists():
        return "source"
    return "unknown"


def build_payload(state: Mapping[str, Any]) -> dict[str, str]:
    return {
        "schema": PAYLOAD_SCHEMA,
        "install_id": str(state.get("install_id") or ""),
        "version": __version__,
        "os": OS_FAMILIES.get(sys.platform, "other"),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "channel": install_channel(),
    }


def status(path: Path, *, env: Mapping[str, str], now: datetime | None = None) -> dict[str, Any]:
    state = load_state(path)
    consent = state.get("consent", "undecided")
    blocked_by = env_block_reason(env)
    endpoint = resolve_endpoint(env)
    return {
        "schema": "loopx_usage_ping_status_v0",
        "consent": consent,
        "sending": consent == "enabled" and blocked_by is None and bool(endpoint),
        "blocked_by": blocked_by,
        "endpoint": endpoint or None,
        "state_path": str(path),
        "last_sent_day": state.get("last_sent_day"),
        "next_payload": build_payload(state) if consent == "enabled" else None,
        "today": _today(now),
    }


def _due(state: Mapping[str, Any], today: str) -> bool:
    return (
        state.get("consent") == "enabled"
        and bool(state.get("install_id"))
        and state.get("last_attempt_day") != today
    )


def send(
    path: Path,
    *,
    env: Mapping[str, str],
    now: datetime | None = None,
    post: Callable[[str, bytes, float], int] | None = None,
) -> dict[str, Any]:
    """Post today's ping once. Returns a small result; never raises for I/O."""
    today = _today(now)
    state = load_state(path)
    endpoint = resolve_endpoint(env)
    if env_block_reason(env) or not endpoint or state.get("consent") != "enabled":
        return {"sent": False, "reason": "not_enabled"}
    if state.get("last_sent_day") == today:
        return {"sent": False, "reason": "already_sent_today"}
    state["last_attempt_day"] = today
    save_state(path, state)
    body = json.dumps(build_payload(state), separators=(",", ":")).encode("utf-8")
    try:
        code = (post or _post)(endpoint, body, REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:  # network errors are expected and silent
        return {"sent": False, "reason": "request_failed", "error": str(exc)[:MAX_ERROR_CHARS]}
    if 200 <= code < 300:
        state["last_sent_day"] = today
        save_state(path, state)
        return {"sent": True, "status": code}
    return {"sent": False, "reason": "rejected", "status": code}


def _post(endpoint: str, body: bytes, timeout: float) -> int:
    import urllib.request

    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "loopx-usage-ping"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https or loopback only
        return int(response.status)


def maybe_schedule(
    argv: list[str],
    *,
    env: Mapping[str, str] | None = None,
    runtime_root: Path | None = None,
    now: datetime | None = None,
    spawn: Callable[..., Any] = subprocess.Popen,
) -> bool:
    """Start the daily ping in a detached child when one is due. Never raises."""
    try:
        env = os.environ if env is None else env
        if argv[:1] == ["usage-ping"] or env_block_reason(env) or not resolve_endpoint(env):
            return False
        path = state_path(runtime_root)
        if not path.exists():
            return False
        state = load_state(path)
        today = _today(now)
        if not _due(state, today):
            return False
        # Claim today's attempt before spawning so concurrent commands start one child.
        state["last_attempt_day"] = today
        save_state(path, state)
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            kwargs["start_new_session"] = True
        spawn([sys.executable, "-m", "loopx.usage_ping", "--send", str(path.parent)], **kwargs)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--send":
        send(state_path(Path(sys.argv[2])), env=os.environ)
        raise SystemExit(0)
    raise SystemExit(2)
