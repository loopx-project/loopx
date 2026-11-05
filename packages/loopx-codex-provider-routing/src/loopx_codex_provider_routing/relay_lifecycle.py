"""Local transport lifecycle. Never selects accounts or replays model work."""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import secrets
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from .native import NativeAccounts, atomic
from .native_host import HostError, process_identity
from .openai_keys import NoRedirect


def runtime(value: dict, *, consumer="worker") -> dict | None:
    from .openai_relay import consumer_token, RUNTIME_SCHEMA
    prefix = "/steward" if consumer == "steward" else ""
    request = urllib.request.Request(f"http://127.0.0.1:{value['port']}{prefix}/runtime",
        headers={"Authorization": "Bearer " + consumer_token(value, consumer)})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=1) as response:
            data = response.read(4097)
        row = json.loads(data) if len(data) <= 4096 else None
        if (isinstance(row, dict) and row.get("schema_version") == RUNTIME_SCHEMA
                and isinstance(row.get("build"), str) and len(row["build"]) == 64
                and isinstance(row.get("instance_id"), str) and len(row["instance_id"]) == 32
                and type(row.get("pid")) is int and row["pid"] > 0
                and type(row.get("accepting")) is bool
                and type(row.get("active_requests")) is int and row["active_requests"] >= 0):
            return {key: row[key] for key in ("schema_version", "build", "instance_id", "pid", "accepting", "active_requests")}
    except (OSError, ValueError, HostError):
        pass
    return None


def status(owner: NativeAccounts, *, consumer="worker") -> dict:
    from .openai_relay import route, healthy, BUILD
    if consumer not in ("worker", "steward"):
        raise HostError("invalid_policy")
    try:
        value = route(owner)
    except FileNotFoundError:
        return {"state": "not_started", "ready": False, "expected_build": BUILD}
    live = runtime(value, consumer=consumer)
    state = ("ready" if live["build"] == BUILD and live["accepting"] else
             "draining" if not live["accepting"] else "upgrade_required") if live else (
             "legacy_upgrade_required" if healthy(value, consumer) else "unavailable")
    return {"state": state, "ready": state == "ready", "expected_build": BUILD,
            "runtime": live}


def _owner_released(owner: NativeAccounts) -> bool:
    try:
        with owner._lock(name="api-relay.lock"):
            return True
    except BlockingIOError:
        return False


def _legacy_identity(owner: NativeAccounts, value: dict, pid: int):
    """Explicit migration only: same UID/kernel birth, module, root, lock, port."""
    identity = process_identity(pid)
    if identity is None:
        raise HostError("api_relay_owner_unverified")
    def output(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        if result.returncode:
            raise HostError("api_relay_owner_unverified")
        return result.stdout.strip()
    argv = shlex.split(output("ps", "-p", str(pid), "-o", "command="))
    expected = ["-m", "loopx_codex_provider_routing.openai_relay", "--runtime-root", str(owner.root.parents[1])]
    if len(argv) != 5 or argv[1:] != expected or Path(argv[0]).resolve() != Path(identity[1]).resolve():
        raise HostError("api_relay_owner_unverified")
    if (output("lsof", "-t", str(owner.root / "api-relay.lock")).splitlines() != [str(pid)]
            or output("lsof", "-a", "-p", str(pid), f"-iTCP:{value['port']}", "-sTCP:LISTEN", "-t") != str(pid)):
        raise HostError("api_relay_owner_unverified")
    return identity


def _drain(value: dict, live: dict):
    token = value.get("control_token")
    if not isinstance(token, str) or len(token) < 32:
        raise HostError("api_relay_upgrade_required")
    request = urllib.request.Request(f"http://127.0.0.1:{value['port']}/control/drain", data=b"",
        headers={"Authorization": "Bearer " + token, "X-LoopX-Instance": live["instance_id"]}, method="POST")
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=2) as response:
        if response.status != 202:
            raise HostError("api_relay_instance_changed")


def reconcile(owner: NativeAccounts, value: dict, *, consumer="worker", legacy_pid=None, timeout=10):
    """Called under the existing state lock; retain the server lock through drain."""
    from .openai_relay import healthy, BUILD
    live = runtime(value)
    if (live and live["build"] == BUILD and live["accepting"]
            and (consumer == "worker" or healthy(value, consumer))):
        return
    deadline = time.monotonic() + timeout
    if live and live["accepting"]:
        _drain(value, live)
    elif live is None and healthy(value):
        if type(legacy_pid) is not int or legacy_pid <= 0:
            raise HostError("api_relay_legacy_migration_required")
        identity = _legacy_identity(owner, value, legacy_pid)
        if _legacy_identity(owner, value, legacy_pid) != identity:
            raise HostError("api_relay_owner_changed")
        os.kill(legacy_pid, signal.SIGINT)  # normal server_close joins admitted handlers
    while not _owner_released(owner):
        if time.monotonic() >= deadline:
            raise HostError("api_relay_drain_pending")
        time.sleep(.1)
    # Only an unoccupied owner can be started. Unknown listeners are never killed.
    if healthy(value):
        raise HostError("api_relay_owner_unverified")
    if "control_token" not in value:
        value["control_token"] = secrets.token_urlsafe(48)
        atomic(owner.root / "api-relay.json", value)
    subprocess.Popen([sys.executable, "-m", "loopx_codex_provider_routing.openai_relay",
                      "--runtime-root", str(owner.root.parents[1])], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    while time.monotonic() < deadline:
        current = runtime(value, consumer=consumer)
        if current and hmac.compare_digest(current["build"], BUILD) and current["accepting"]:
            return
        time.sleep(.1)
    raise HostError("api_relay_unavailable")
