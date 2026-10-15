"""Lark provider CLI execution and result decoding.

Presentation and conversation callers share this IO boundary. Callers still
own authority, retries, and provider readback; a successful process is not a
verified external effect. Executable discovery and Node wrapper environments
remain in cli_resolution.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_CLI_BIN = "lark-cli"
OUTPUT_LIMIT = 1800
CommandRunner = Callable[[list[str], Path | None, float | None], dict[str, Any]]

_LARK_CREDENTIAL_ARGUMENTS = frozenset(
    {
        "--access-key",
        "--access-token",
        "--ak",
        "--app-secret",
        "--authorization",
        "--bearer-token",
        "--client-secret",
        "--secret-key",
        "--sk",
        "--tenant-access-token",
        "--user-access-token",
    }
)


def default_subprocess_runner(
    args: list[str],
    cwd: Path | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "timed_out": False,
    }


def run_command(
    args: list[str],
    *,
    execute: bool,
    runner: CommandRunner = default_subprocess_runner,
    cwd: Path | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    credential_argument = next(
        (
            argument.split("=", 1)[0].lower()
            for argument in args
            if argument.split("=", 1)[0].lower() in _LARK_CREDENTIAL_ARGUMENTS
        ),
        None,
    )
    if credential_argument:
        raise ValueError(
            f"{credential_argument} must remain in lark-cli auth; "
            "the Kanban adapter never accepts inline credentials"
        )
    command = shlex.join(args)
    if not execute:
        return {
            "command": command,
            "executed": False,
            "ok": True,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "json": None,
        }
    try:
        result = runner(args, cwd, timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "executed": True,
            "ok": False,
            "returncode": None,
            "stdout": str(exc.stdout or ""),
            "stderr": str(exc.stderr or ""),
            "timed_out": True,
            "json": None,
        }
    except OSError as exc:
        return {
            "command": command,
            "executed": True,
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "json": None,
        }
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    parsed = _parse_json(stdout)
    ok = int(result.get("returncode") or 0) == 0 and _parsed_ok(parsed)
    return {
        "command": command,
        "executed": True,
        "ok": ok,
        "returncode": result.get("returncode"),
        "stdout": stdout,
        "stderr": stderr,
        "json": parsed,
    }


def _parse_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _parsed_ok(parsed: Any) -> bool:
    if parsed is None:
        return True
    if not isinstance(parsed, dict):
        return True
    if parsed.get("ok") is False:
        return False
    if parsed.get("code") not in (None, 0):
        return False
    return True


def command_error(command_result: dict[str, Any]) -> str:
    parsed = command_result.get("json")
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
        if parsed.get("msg") and parsed.get("code") not in (None, 0):
            return str(parsed.get("msg"))
    stderr = " ".join(str(command_result.get("stderr") or "").split())
    if stderr:
        return stderr[:OUTPUT_LIMIT]
    stdout = " ".join(str(command_result.get("stdout") or "").split())
    return stdout[:OUTPUT_LIMIT]
