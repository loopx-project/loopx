from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...extensions.process_runtime import terminate_process_tree

# Frozen wire id shared with capabilities/issue_fix. Kept verbatim for wire
# compatibility with existing caller-repo-branch receipts.
CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION = "issue_fix_validation_command_v0"


def run_caller_validation(
    workspace: Path,
    *,
    validation_command: str | None = None,
    validation_argv: list[str] | None = None,
    validation_label: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Run a caller-approved validation command and return a privacy-safe receipt.

    Exactly one of ``validation_command`` (shell-free ``shlex.split``) or
    ``validation_argv`` (pre-split JSON argv array, no shell parsing) selects
    the command form. The command runs with ``cwd=workspace``.
    Only ``exit_code`` and the boolean ``passed`` are recorded; command stdout,
    stderr, and local paths are deliberately not captured. A timeout raises
    ``subprocess.TimeoutExpired``; callers decide whether to convert that into
    a failure receipt. Timeout and caller cancellation terminate the command's
    owned process tree, not just its leader. Declared deadlines and receipt
    decisions remain with the caller's existing TypeScript owner.
    """
    if (validation_command is None) == (validation_argv is None):
        raise ValueError(
            "exactly one of validation_command or validation_argv is required"
        )
    argv = (
        [str(item) for item in validation_argv]
        if validation_argv is not None
        else shlex.split(validation_command or "")
    )
    if not argv:
        raise ValueError("validation command must not be empty")
    with subprocess.Popen(
        argv,
        cwd=workspace,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    ) as process:
        try:
            # Output is transient and never part of the public receipt.
            process.communicate(timeout=timeout_seconds)
        except BaseException:
            # Reuse the existing OS transport cleanup. A zero grace preserves
            # subprocess.run's immediate-kill cancellation rather than adding
            # time to the declared validation budget. It also handles an exited
            # POSIX leader whose surviving children still hold the output pipes.
            terminate_process_tree(process, grace_seconds=0)
            raise
    return {
        "schema_version": CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
        "command_label": validation_label or "caller-declared validation",
        "exit_code": process.returncode,
        "passed": process.returncode == 0,
        "stdout_captured": False,
        "stderr_captured": False,
        "local_path_captured": False,
    }


def require_validation_passed(step: Mapping[str, Any]) -> None:
    """Raise ``RuntimeError`` unless the validation step ``passed``."""
    if step.get("passed") is not True:
        raise RuntimeError(
            f"{step.get('command_label')} failed with exit code {step.get('exit_code')}"
        )
