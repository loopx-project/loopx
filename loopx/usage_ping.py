"""CLI host adapter. TypeScript owns telemetry policy, state, payloads and I/O."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .paths import DEFAULT_RUNTIME_ROOT

STATE_FILENAME = "usage-ping.json"
# Scheduling hint only; keep aligned with the TypeScript notice revision.
_NOTICE_VERSION = 5
_ENTRY = Path(__file__).parent / "control_plane/runtime/usage_statistics_cli.ts"
_observation: ContextVar[dict[str, Any] | None] = ContextVar("usage_observation", default=None)


def select_operation(args: Any) -> None:
    """Parser-owned operation names only; never scan argument values."""
    state = _observation.get()
    if state is None:
        return
    command = getattr(args, "command", "")
    operation = getattr(args, f"{command.replace('-', '_')}_command", "default")
    if command == "pr-review":
        operation = ("merge-readiness" if getattr(args, "check_merge_readiness", None)
                     else "check-result" if getattr(args, "check_result", None) else "default")
    state["operation"] = operation


def capture_result(payload: dict[str, Any]) -> None:
    """Project booleans from existing receipts, not output text or business content."""
    state = _observation.get()
    if state is None:
        return
    try:
        facts = {key: payload[key] for key in ('ok', 'ready', 'completed', 'changed', 'dry_run')
                 if isinstance(payload.get(key), bool)}
        if payload.get('schema_version') == 'loopx_turn_execution_v0':
            from .control_plane.turn_driver import loopx_turn_execution_committed
            facts['turn_committed'] = loopx_turn_execution_committed(payload)
        validation = payload.get('validation_receipt')
        if isinstance(validation, dict) and validation.get('passed') is True:
            facts['validation_passed'] = True
        state['result_facts'] = facts
    except Exception:
        state.pop('result_facts', None)  # Optional diagnostics cannot break output.


def capture_failure(error: BaseException) -> None:
    state = _observation.get()
    if state is None:
        return
    category = ('interrupted' if isinstance(error, KeyboardInterrupt)
                else 'invalid_input' if isinstance(error, SystemExit) and error.code == 2
                else 'timeout' if isinstance(error, (TimeoutError, subprocess.TimeoutExpired))
                else 'connection' if isinstance(error, ConnectionError)
                else 'permission' if isinstance(error, PermissionError)
                else 'not_found' if isinstance(error, FileNotFoundError)
                else 'invalid_input' if isinstance(error, ValueError)
                else 'command_failed')
    state['failure'] = category


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
    # Keep the Python sender's proxy behavior when moving I/O to Node. This
    # native flag is available in the repository's supported Node versions.
    return [node, "--no-warnings", "--use-env-proxy", "--experimental-strip-types", str(_ENTRY)]


def control(action: str, path: Path | None = None, **fields: Any) -> dict[str, Any]:
    result = subprocess.run(_command(), input=json.dumps(_request(action, path or state_path(), **fields)),
                            capture_output=True, text=True, encoding="utf-8", timeout=4, check=False)
    payload = json.loads(result.stdout)
    if result.returncode or not isinstance(payload, dict) or "error" in payload:
        raise RuntimeError("Usage settings unavailable. Inspect the local usage-ping.json; disable can repair invalid state.")
    return payload


def begin(command: str) -> tuple[str, float] | None:
    """Read a small host hint; do not start a synchronous Node process on warm commands."""
    _observation.set({})
    # Negative-only scheduling hints for common unattended environments. These
    # cannot authorize collection; TS still checks every supported switch value.
    if os.environ.get("LOOPX_USAGE_PING") == "0" or os.environ.get("DO_NOT_TRACK") == "1" or os.environ.get("CI") == "true":
        return None
    if command == "usage-ping":
        return None
    try:
        path = state_path()
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if state.get("consent") == "disabled":
            return None
        if (state.get("notice") or {}).get("version") != _NOTICE_VERSION:
            # App services defer disclosure to the visible frontend. Ordinary
            # script/Agent calls disclose on stderr too; JSON stdout stays clean.
            stream = sys.stderr
            if stream is None:
                # No disclosure channel exists, so nothing was shown: never fall
                # back to stdout and never acknowledge an unseen notice.
                return None
            if command in {"chat", "serve-status"} and not stream.isatty():
                return None
            try:
                if os.path.samestat(os.fstat(stream.fileno()), os.stat(os.devnull)):
                    return None  # Discarded output cannot carry a disclosure.
            except (AttributeError, OSError, ValueError):
                # In-memory host streams have no descriptor but can still display
                # the notice, so they keep disclosing instead of being skipped.
                pass
            projection = control("status", path)
            if not projection["automatic_notice_required"]:
                return None
            print(projection["disclosure"], file=stream, flush=True)
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
    if error is not None:
        capture_failure(error)
    observation = _observation.get() or {}
    _observation.set(None)
    if ticket is None:
        return
    try:
        request = _request("observe", state_path(), generation=ticket[0], feature=command if len(command) <= 64 else "other",
                           exit_code=code, activity_day=datetime.now(timezone.utc).date().isoformat(),
                           **observation, elapsed_ms=max(0, (time.monotonic() - ticket[1]) * 1000))
        _detach(request)
    except Exception:
        pass  # Telemetry cannot replace the command's result.


def observe_verified_return() -> None:
    """An existing provider verified and durably settled a new result return."""
    try:
        state = json.loads(state_path().read_text(encoding='utf-8'))
        if state.get('consent') == 'disabled' or (state.get('notice') or {}).get('version') != _NOTICE_VERSION:
            return
        generation = state.get('generation')
        if not isinstance(generation, str) or not generation:
            return
        _detach(_request('observe', state_path(), generation=generation, feature='other', operation='result-return',
                         exit_code=0, activity_day=datetime.now(timezone.utc).date().isoformat(),
                         result_facts={'ok': True, 'changed': True, 'reply_verified': True}, elapsed_ms=0))
    except Exception:
        pass


def _detach(request: dict[str, Any], *, command: list[str] | None = None) -> None:
    kwargs: dict[str, Any] = {"stdin": subprocess.PIPE, "stdout": subprocess.DEVNULL,
                              "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    child = subprocess.Popen(command or _command(), **kwargs)
    assert child.stdin is not None
    child.stdin.write(json.dumps(request).encode())
    child.stdin.close()
