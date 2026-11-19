from __future__ import annotations

import json as _json
import os
import shlex
import subprocess
import sys as _sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from shutil import which as _which
from typing import Any

# Keep the historical exports and caller patch points, but select the OS
# transport only when used. Decisions and deadlines remain with the caller.
_PROCESS_RUNTIME_EXPORTS = ("terminate_process_tree",)


def __getattr__(name: str) -> Any:
    if name not in _PROCESS_RUNTIME_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from ...extensions import process_runtime

    for export in _PROCESS_RUNTIME_EXPORTS:
        globals().setdefault(export, getattr(process_runtime, export))
    return globals()[name]


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_PROCESS_RUNTIME_EXPORTS))


def _export(name: str) -> Any:
    return getattr(_sys.modules[__name__], name)


# An already selected owner retains the former import-time binding, including
# owner replacements installed before this module was imported.
if "loopx.extensions.process_runtime" in _sys.modules:
    for _name in _PROCESS_RUNTIME_EXPORTS:
        globals()[_name] = getattr(_sys.modules["loopx.extensions.process_runtime"], _name)

# Frozen wire id shared with capabilities/issue_fix. Kept verbatim for wire
# compatibility with existing caller-repo-branch receipts.
CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION = "issue_fix_validation_command_v0"
_VALIDATION_STDIN: ContextVar[int | None] = ContextVar("validation_stdin", default=None)
_VALIDATION_EXECUTION_FD: ContextVar[int | None] = ContextVar("validation_execution_fd", default=None)


@contextmanager
def retain_validation_execution_lock(descriptor: int | None) -> Iterator[None]:
    """Bind a borrowed POSIX execution lock to validation in this context."""
    token = _VALIDATION_EXECUTION_FD.set(descriptor)
    try:
        yield
    finally:
        _VALIDATION_EXECUTION_FD.reset(token)


# Load the supervisor from the caller's exact package, even for a source
# checkout launched with PYTHONPATH. The validator keeps its original cwd/env.
_SUPERVISOR_ENTRY = (
    "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
    "runpy.run_module('loopx.control_plane.runtime.validation_supervisor',run_name='__main__')"
)


def _supervised_validation(
    argv: list[str], workspace: Path, timeout_seconds: int, lock_descriptor: int,
) -> int:
    # Closing this private pipe requests cleanup while the supervisor still
    # owns the lock. The validator receives the caller's ordinary stdin.
    control_read, control_write = os.pipe()
    process = None
    python = _which(_sys.executable) or _sys.executable
    try:
        try:
            process = subprocess.Popen(
                [python, "-I", "-S", "-c", _SUPERVISOR_ENTRY,
                 str(Path(__file__).resolve().parents[3]), str(control_read), str(timeout_seconds), *argv],
                cwd=workspace,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                stdin=_VALIDATION_STDIN.get(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                pass_fds=(control_read, lock_descriptor),
                start_new_session=True,
            )
        finally:
            os.close(control_read)
        # Bound startup and cleanup independently of the validator deadline.
        try:
            output, _ = process.communicate(timeout=timeout_seconds + 3)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("completion validation supervisor did not finish before its deadline") from error
    except BaseException:
        os.close(control_write)
        if process is not None:
            # The supervisor is the only child with the execution lock. Give
            # its cleanup bounded time without killing that lock holder.
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                assert process.stdout is not None
                process.stdout.close()
        raise
    else:
        os.close(control_write)
    if process.returncode != 0:
        raise RuntimeError("completion validation supervisor did not confirm process cleanup")
    try:
        outcome, value = _json.loads(output)
    except (TypeError, ValueError) as error:
        raise RuntimeError("completion validation supervisor returned an invalid result") from error
    if outcome == "exited":
        return int(value)
    if outcome == "timeout":
        raise subprocess.TimeoutExpired(argv, timeout_seconds)
    if outcome == "launch_failed":
        raise OSError("completion validation executable could not be launched")
    if outcome == "malformed":
        raise ValueError(value)
    raise RuntimeError("completion validation supervisor returned an invalid result")


@contextmanager
def noninteractive_validation() -> Iterator[None]:
    """Give validators EOF in this context, preserving other callers' stdin.

    Trusted noninteractive hosts use this scope instead of lending their live
    conversation input to child validators. Nested scopes and failures restore
    the previous input policy; ordinary validation keeps inheriting stdin.
    """
    token = _VALIDATION_STDIN.set(subprocess.DEVNULL)
    try:
        yield
    finally:
        _VALIDATION_STDIN.reset(token)


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
    execution_descriptor = _VALIDATION_EXECUTION_FD.get()
    if execution_descriptor is None:
        returncode = _direct_validation(argv, workspace, timeout_seconds)
    else:
        returncode = _supervised_validation(argv, workspace, timeout_seconds, execution_descriptor)
    return {
        "schema_version": CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
        "command_label": validation_label or "caller-declared validation",
        "exit_code": returncode,
        "passed": returncode == 0,
        "stdout_captured": False,
        "stderr_captured": False,
        "local_path_captured": False,
    }


def _direct_validation(argv: list[str], workspace: Path, timeout_seconds: int) -> int:
    with subprocess.Popen(
        argv,
        cwd=workspace,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdin=_VALIDATION_STDIN.get(),
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
            _export("terminate_process_tree")(process, grace_seconds=0)
            raise
    return int(process.returncode)


def require_validation_passed(step: Mapping[str, Any]) -> None:
    """Raise ``RuntimeError`` unless the validation step ``passed``."""
    if step.get("passed") is not True:
        raise RuntimeError(
            f"{step.get('command_label')} failed with exit code {step.get('exit_code')}"
        )


# Star imports retain every former public name, including the lazy reexports.
__all__ = sorted(
    {name for name in globals() if not name.startswith("_")} | set(_PROCESS_RUNTIME_EXPORTS)
)
