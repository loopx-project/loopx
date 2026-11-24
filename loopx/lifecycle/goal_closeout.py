from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..capabilities.goal_closeout.machine_defaults import (
    normalize_goal_closeout_configuration,
)
from ..capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from ..capabilities.machine_configuration.store import read_machine_configuration
from ..control_plane.runtime.time import now_local_iso
from ..extensions.process_runtime import run_capped_process
from ..file_lock import LockAcquisitionPolicy, exclusive_file_lock
from ..paths import validate_goal_id_path_segment

GOAL_CLOSEOUT_PAYLOAD_SCHEMA_VERSION = "loopx_goal_closeout_v1"
GOAL_CLOSEOUT_LEDGER_SCHEMA_VERSION = "loopx_goal_closeout_invocation_v0"
GOAL_CLOSEOUT_LEDGER_FILENAME = "closeout-ledger.jsonl"
_MAX_CAPTURE_BYTES = 8192


def goal_closeout_ledger_path(runtime_root: Path, goal_id: str) -> Path:
    safe_goal_id = validate_goal_id_path_segment(goal_id)
    return runtime_root / "goals" / safe_goal_id / GOAL_CLOSEOUT_LEDGER_FILENAME


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


def goal_closeout_configuration(
    runtime_root: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    """Report the configured terminal hook without running it.

    Callers use this to skip hook-only argument preparation when no command is
    configured, so an operator's stale path can never fail a committed
    terminal transition.
    """

    return _read_configuration(runtime_root)


def _write_ledger(
    ledger_dir: Path,
    record: dict[str, Any],
) -> tuple[Path, bool, str | None]:
    path = ledger_dir / GOAL_CLOSEOUT_LEDGER_FILENAME
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


def _decode_capture(value: bytes) -> str:
    return value[:_MAX_CAPTURE_BYTES].decode("utf-8", errors="replace")


def run_goal_closeout(
    *,
    runtime_root: Path,
    goal_id: str,
    repo: str | None,
    state_file: str | None,
    terminal_reason: str,
    trigger: str,
    ledger_dir: Path | None = None,
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
        safe_goal_id = validate_goal_id_path_segment(goal_id)
    except ValueError as exc:
        result.update({"status": "invalid_goal_id", "error": str(exc)})
        return result
    result["goal_id"] = safe_goal_id
    selected_ledger_dir = (
        ledger_dir
        if ledger_dir is not None
        else goal_closeout_ledger_path(runtime_root, safe_goal_id).parent
    )
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
            "goal_id": safe_goal_id,
            "repo": repo or "",
            "state_file": state_file or "",
            "terminal_reason": terminal_reason,
            "runtime_root": str(runtime_root),
            "trigger": trigger,
        }
        started = time.monotonic()
        exit_code: int | None = None
        timed_out = False
        failure_kind: str | None = None
        stdout_text = ""
        stderr_text = ""
        launch_error: str | None = None
        try:
            completed = run_capped_process(
                command,
                stdin=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout_seconds=float(configuration["timeout_seconds"]),
                output_limit_bytes=_MAX_CAPTURE_BYTES,
                # Never hold a directory handle inside a worktree that the
                # archive transition is deleting.
                cwd=None,
            )
        except OSError as exc:
            launch_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            launch_error = f"{type(exc).__name__}: {exc}"
        else:
            exit_code = completed.returncode
            failure_kind = completed.failure_kind
            timed_out = failure_kind == "timeout"
            stdout_text = _decode_capture(completed.stdout)
            stderr_text = _decode_capture(completed.stderr)
        duration_ms = int((time.monotonic() - started) * 1000)
        if launch_error is not None:
            status = "launch_error"
        elif timed_out:
            status = "timeout"
        elif failure_kind is not None:
            status = "output_limit"
        elif exit_code == 0:
            status = "success"
        else:
            status = "failed"
        result.update(
            {
                "status": status,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "failure_kind": failure_kind,
                "duration_ms": duration_ms,
                "stdout": stdout_text,
                "stderr": stderr_text,
            }
        )
        if launch_error is not None:
            result["error"] = launch_error
        record = {
            "schema_version": GOAL_CLOSEOUT_LEDGER_SCHEMA_VERSION,
            "recorded_at": now_local_iso(),
            "goal_id": safe_goal_id,
            "trigger": trigger,
            "terminal_reason": terminal_reason,
            "command": command,
            "status": status,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "failure_kind": failure_kind,
            "duration_ms": duration_ms,
            "stdout": stdout_text,
            "stderr": stderr_text,
            **({"error": launch_error} if launch_error is not None else {}),
        }
        ledger_path, ledger_recorded, ledger_error = _write_ledger(
            selected_ledger_dir, record
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
