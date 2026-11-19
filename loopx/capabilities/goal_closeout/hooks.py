from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from ...control_plane.runtime.time import now_local_iso
from ...file_lock import LockAcquisitionPolicy, exclusive_file_lock
from ..machine_configuration.builtins import build_builtin_machine_configuration_registry
from ..machine_configuration.store import read_machine_configuration
from .machine_defaults import normalize_goal_closeout_configuration

GOAL_CLOSEOUT_PAYLOAD_SCHEMA_VERSION = "loopx_goal_closeout_v1"
GOAL_CLOSEOUT_LEDGER_SCHEMA_VERSION = "loopx_goal_closeout_invocation_v0"
GOAL_CLOSEOUT_LEDGER_FILENAME = "closeout-ledger.jsonl"
_MAX_CAPTURE_BYTES = 8192


class _Capture:
    """Read only a bounded prefix from a binary temporary file."""

    def __init__(self, handle: Any) -> None:
        self.handle = handle

    def text(self) -> str:
        self.handle.seek(0)
        return self.handle.read(_MAX_CAPTURE_BYTES).decode("utf-8", errors="replace")


def goal_closeout_ledger_path(runtime_root: Path, goal_id: str) -> Path:
    return runtime_root / "goals" / goal_id / GOAL_CLOSEOUT_LEDGER_FILENAME


def _read_configuration(runtime_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        configuration = read_machine_configuration(
            runtime_root,
            registry=build_builtin_machine_configuration_registry(),
            namespace_ids=("goal_closeout",),
        )
    except Exception as exc:  # semantically fail-open for the terminal command
        return None, f"{type(exc).__name__}: {exc}"
    if not configuration:
        return None, None
    raw = configuration.get("namespaces", {}).get("goal_closeout")
    if raw is None:
        return None, None
    try:
        return normalize_goal_closeout_configuration(raw), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """Kill the command and its descendants, then reap the direct child."""

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (AttributeError, PermissionError, ProcessLookupError, OSError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _write_ledger(
    runtime_root: Path,
    goal_id: str,
    record: dict[str, Any],
) -> tuple[Path, bool, str | None]:
    path = goal_closeout_ledger_path(runtime_root, goal_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(
            path,
            policy=LockAcquisitionPolicy.MUTATION,
            operation="goal_closeout_ledger_append",
        ):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path, True, None
    except Exception as exc:  # the terminal command already ran; never block it
        return path, False, f"{type(exc).__name__}: {exc}"


def run_goal_closeout(
    *,
    runtime_root: Path,
    goal_id: str,
    repo: str | None,
    state_file: str | None,
    terminal_reason: str,
    trigger: str,
) -> dict[str, Any]:
    """Run the configured closeout command once and record the attempt.

    The caller's terminal transition has already committed. Every failure in
    this function is returned, never raised, so the command that finished the
    Goal keeps its own result.
    """

    result: dict[str, Any] = {
        "schema_version": GOAL_CLOSEOUT_LEDGER_SCHEMA_VERSION,
        "goal_id": goal_id,
        "trigger": trigger,
        "terminal_reason": terminal_reason,
        "status": "not_configured",
        "ledger_recorded": False,
    }
    try:
        configuration, configuration_error = _read_configuration(runtime_root)
        if configuration_error is not None:
            result.update(
                {
                    "status": "configuration_error",
                    "error": configuration_error,
                }
            )
            return result
        if configuration is None:
            return result
        command = list(configuration.get("command") or [])
        result["command"] = command
        if not command:
            return result

        payload = {
            "schema_version": GOAL_CLOSEOUT_PAYLOAD_SCHEMA_VERSION,
            "goal_id": goal_id,
            "repo": repo or "",
            "state_file": state_file or "",
            "terminal_reason": terminal_reason,
            "runtime_root": str(runtime_root),
            "trigger": trigger,
        }
        started = time.monotonic()
        timed_out = False
        with tempfile.TemporaryFile(mode="w+b") as stdout_handle:
            with tempfile.TemporaryFile(mode="w+b") as stderr_handle:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    cwd=repo if repo and Path(repo).is_dir() else None,
                    start_new_session=True,
                )
                try:
                    process.communicate(
                        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                        timeout=float(configuration["timeout_seconds"]),
                    )
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _terminate(process)
                duration_ms = int((time.monotonic() - started) * 1000)
                stdout_text = _Capture(stdout_handle).text()
                stderr_text = _Capture(stderr_handle).text()
                exit_code = process.returncode
        if timed_out:
            status = "timeout"
        elif exit_code == 0:
            status = "success"
        else:
            status = "failed"
        result.update(
            {
                "status": status,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "duration_ms": duration_ms,
                "stdout": stdout_text,
                "stderr": stderr_text,
            }
        )
        record = {
            "schema_version": GOAL_CLOSEOUT_LEDGER_SCHEMA_VERSION,
            "recorded_at": now_local_iso(),
            "goal_id": goal_id,
            "trigger": trigger,
            "terminal_reason": terminal_reason,
            "command": command,
            "status": status,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
            "stdout": stdout_text,
            "stderr": stderr_text,
        }
        ledger_path, ledger_recorded, ledger_error = _write_ledger(
            runtime_root, goal_id, record
        )
        result.update(
            {
                "ledger_path": str(ledger_path),
                "ledger_recorded": ledger_recorded,
            }
        )
        if ledger_error is not None:
            result["ledger_error"] = ledger_error
    except Exception as exc:
        result.update(
            {
                "status": "internal_error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    return result

