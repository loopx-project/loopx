from __future__ import annotations

import hashlib
import json
import os
import shlex
import signal
import stat
import subprocess
from pathlib import Path
from typing import Any

from .contract import AdapterTurn, ValidationResult
from .runtime import (
    WorkspaceChange,
    WorkspaceFileType,
    run_planner_worker_once,
)


TRAEX_PLANNER_WORKER_PROBE_SCHEMA_VERSION = "traex_planner_worker_probe_v1"
DEFAULT_TRAEX_PLANNER_MODEL = "GPT-5.5"
DEFAULT_TRAEX_WORKER_MODEL = "DeepSeek-V4-Flash"
DEFAULT_VALIDATION_EXECUTABLES = frozenset(
    {"python", "python3", "pytest", "go", "npm", "npx", "cargo"}
)


class TraexPlannerWorkerError(RuntimeError):
    """Raised when the experimental TraeX adapter cannot complete safely."""


def _assistant_text_and_usage(jsonl: str) -> tuple[str, dict[str, int], bool]:
    parts: list[str] = []
    usage: dict[str, int] = {}
    completed = False
    for line in jsonl.split("\n"):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TraexPlannerWorkerError("traex returned invalid JSONL") from exc
        if item.get("type") == "turn.completed":
            completed = True
            if isinstance(item.get("usage"), dict):
                usage = {
                    key: int(value)
                    for key, value in item["usage"].items()
                    if isinstance(value, int) and not isinstance(value, bool)
                }
        payload = item.get("item")
        if isinstance(payload, dict) and payload.get("type") == "agent_message":
            parts.append(str(payload.get("text") or ""))
    usage_complete = (
        completed
        and "input_tokens" in usage
        and "output_tokens" in usage
    )
    return "\n".join(parts).strip(), usage, usage_complete


def _run_traex_exec(
    *,
    traex_bin: str,
    model: str,
    prompt: str,
    cwd: Path,
    sandbox: str,
    timeout_seconds: float,
    minimal_context: bool,
) -> tuple[AdapterTurn, bool]:
    command = [
        traex_bin,
        "exec",
        "--json",
        "--ephemeral",
        "--sandbox",
        sandbox,
        "--skip-git-repo-check",
        "--model",
        model,
    ]
    if minimal_context:
        command.extend(["--ignore-rules", "--ignore-user-config"])
    command.append(prompt)
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise TraexPlannerWorkerError(
            f"traex exec failed for model={model}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    assistant_text, usage, usage_complete = _assistant_text_and_usage(result.stdout)
    return (
        AdapterTurn(
            output_text=assistant_text,
            usage=usage,
            usage_complete=usage_complete,
        ),
        bool(result.stderr.strip()),
    )


class TraexPlannerAdapter:
    def __init__(
        self,
        *,
        traex_bin: str,
        timeout_seconds: float,
    ) -> None:
        self.traex_bin = traex_bin
        self.timeout_seconds = timeout_seconds
        self.stderr_present = False

    def plan(
        self,
        *,
        prompt: str,
        model_route: dict[str, str],
        cwd: Path,
    ) -> AdapterTurn:
        turn, self.stderr_present = _run_traex_exec(
            traex_bin=self.traex_bin,
            model=model_route["model"],
            prompt=prompt,
            cwd=cwd,
            sandbox="read-only",
            timeout_seconds=self.timeout_seconds,
            minimal_context=False,
        )
        return turn


class TraexWorkerAdapter:
    def __init__(
        self,
        *,
        traex_bin: str,
        timeout_seconds: float,
        minimal_context: bool,
    ) -> None:
        self.traex_bin = traex_bin
        self.timeout_seconds = timeout_seconds
        self.minimal_context = minimal_context
        self.stderr_present = False

    def execute(
        self,
        *,
        prompt: str,
        model_route: dict[str, str],
        cwd: Path,
    ) -> AdapterTurn:
        turn, self.stderr_present = _run_traex_exec(
            traex_bin=self.traex_bin,
            model=model_route["model"],
            prompt=prompt,
            cwd=cwd,
            sandbox="workspace-write",
            timeout_seconds=self.timeout_seconds,
            minimal_context=self.minimal_context,
        )
        return turn


class SubprocessValidationRunner:
    """Run bounded validation commands without shell expansion."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        approved_commands: frozenset[str],
        allowed_executables: frozenset[str] = DEFAULT_VALIDATION_EXECUTABLES,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.approved_commands = approved_commands
        self.allowed_executables = allowed_executables

    def __call__(self, command: str, cwd: Path) -> ValidationResult:
        if command not in self.approved_commands:
            return ValidationResult(command=command, passed=False, exit_code=None)
        try:
            argv = shlex.split(command)
        except ValueError:
            return ValidationResult(command=command, passed=False, exit_code=None)
        if (
            not argv
            or argv[0] != Path(argv[0]).name
            or argv[0] not in self.allowed_executables
            or "-c" in argv[1:]
        ):
            return ValidationResult(command=command, passed=False, exit_code=None)
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONPATH": str(cwd),
                },
            )
            exit_code = process.wait(timeout=self.timeout_seconds)
        except OSError:
            return ValidationResult(command=command, passed=False, exit_code=None)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            return ValidationResult(command=command, passed=False, exit_code=None)
        return ValidationResult(
            command=command,
            passed=exit_code == 0,
            exit_code=exit_code,
        )


class GitWorkspaceObserver:
    """Require a clean git fixture and report Worker changes as relative paths."""

    def __init__(self) -> None:
        self._before: dict[str, tuple[int, str, int]] | None = None

    def _snapshot(self, cwd: Path) -> dict[str, tuple[int, str, int]]:
        snapshot: dict[str, tuple[int, str, int]] = {
            ".": (0, "", cwd.lstat().st_mode)
        }
        for root, directories, filenames in os.walk(cwd, followlinks=False):
            root_path = Path(root)
            traversable_directories: list[str] = []
            for name in directories:
                path = root_path / name
                if name == ".git":
                    continue
                relative = path.relative_to(cwd).as_posix()
                if path.is_symlink():
                    payload = os.readlink(path).encode(
                        "utf-8", errors="surrogateescape"
                    )
                    snapshot[relative] = (
                        len(payload),
                        hashlib.sha256(payload).hexdigest(),
                        path.lstat().st_mode,
                    )
                    continue
                snapshot[relative] = (0, "", path.lstat().st_mode)
                traversable_directories.append(name)
            directories[:] = traversable_directories
            for filename in filenames:
                path = root_path / filename
                relative = path.relative_to(cwd).as_posix()
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode):
                    payload = os.readlink(path).encode("utf-8", errors="surrogateescape")
                elif stat.S_ISREG(mode):
                    payload = path.read_bytes()
                else:
                    payload = b""
                snapshot[relative] = (
                    len(payload),
                    hashlib.sha256(payload).hexdigest(),
                    mode,
                )
        return snapshot

    def assert_clean(self, cwd: Path) -> None:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
        if probe.returncode != 0 or probe.stdout.strip() != "true":
            raise TraexPlannerWorkerError("planner-worker workspace must be a git worktree")
        if self.changed_files(cwd):
            raise TraexPlannerWorkerError(
                "planner-worker workspace must be clean before Planner execution"
            )
        self._before = self._snapshot(cwd)

    @staticmethod
    def _workspace_change(
        entry: tuple[int, str, int] | None,
    ) -> WorkspaceChange:
        if entry is None:
            return {"size": None, "file_type": "deleted"}
        size, _, mode = entry
        file_type: WorkspaceFileType
        if stat.S_ISREG(mode):
            file_type = "regular"
        elif stat.S_ISLNK(mode):
            file_type = "symlink"
        elif stat.S_ISDIR(mode):
            file_type = "directory"
        else:
            file_type = "special"
        return {
            "size": size if file_type in {"regular", "symlink"} else None,
            "file_type": file_type,
        }

    def changed_files(self, cwd: Path) -> dict[str, WorkspaceChange]:
        if self._before is not None:
            after = self._snapshot(cwd)
            changed_paths = {
                path
                for path in {*self._before, *after}
                if self._before.get(path) != after.get(path)
                and not (
                    path not in self._before
                    and path in after
                    and stat.S_ISDIR(after[path][2])
                )
            }
            snapshot_changes: dict[str, WorkspaceChange] = {}
            for path in sorted(changed_paths):
                snapshot_changes[path] = self._workspace_change(after.get(path))
            return snapshot_changes
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=cwd,
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            raise TraexPlannerWorkerError("unable to inspect planner-worker git changes")
        status_changes: dict[str, WorkspaceChange] = {}
        entries = [entry for entry in result.stdout.split(b"\0") if entry]
        index = 0
        while index < len(entries):
            entry = entries[index].decode("utf-8", errors="replace")
            status = entry[:2]
            path = entry[3:]
            if status[0] in {"R", "C"} and index + 1 < len(entries):
                index += 1
            file_path = cwd / path
            try:
                mode = file_path.lstat().st_mode
            except FileNotFoundError:
                status_changes[path] = self._workspace_change(None)
            else:
                if stat.S_ISREG(mode):
                    payload_size = file_path.stat().st_size
                elif stat.S_ISLNK(mode):
                    payload_size = len(
                        os.readlink(file_path).encode(
                            "utf-8",
                            errors="surrogateescape",
                        )
                    )
                else:
                    payload_size = 0
                status_changes[path] = self._workspace_change((payload_size, "", mode))
            index += 1
        return dict(sorted(status_changes.items()))


def run_traex_planner_worker_probe(
    *,
    objective: str,
    task_instruction: str,
    traex_bin: str = "traex",
    planner_model: str = DEFAULT_TRAEX_PLANNER_MODEL,
    worker_model: str = DEFAULT_TRAEX_WORKER_MODEL,
    strong_worker_model: str | None = None,
    approved_validation_commands: tuple[str, ...] | list[str] = (),
    cwd: Path | str = ".",
    worker_minimal_context: bool = True,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Run the experimental TraeX adapter through the core vertical slice."""

    workdir = Path(cwd).resolve()
    planner = TraexPlannerAdapter(
        traex_bin=traex_bin,
        timeout_seconds=timeout_seconds,
    )
    worker = TraexWorkerAdapter(
        traex_bin=traex_bin,
        timeout_seconds=timeout_seconds,
        minimal_context=worker_minimal_context,
    )
    approved_commands = frozenset(
        command.strip()
        for command in approved_validation_commands
        if command.strip()
    )
    if not approved_commands:
        raise TraexPlannerWorkerError(
            "at least one caller-approved validation command is required"
        )
    try:
        receipt = run_planner_worker_once(
            objective=objective,
            task_instruction=task_instruction,
            cwd=workdir,
            planner=planner,
            worker=worker,
            validation_runner=SubprocessValidationRunner(
                timeout_seconds=timeout_seconds,
                approved_commands=approved_commands,
            ),
            workspace_observer=GitWorkspaceObserver(),
            approved_validation_commands=approved_commands,
            model_routes={
                "planner": {"model": planner_model, "effort": "high"},
                "cheap_worker": {"model": worker_model, "effort": "medium"},
                "strong_worker": {
                    "model": strong_worker_model or planner_model,
                    "effort": "high",
                },
            },
        )
    except ValueError as exc:
        raise TraexPlannerWorkerError(
            f"invalid planner-worker contract: {exc}"
        ) from exc
    return {
        "schema_version": TRAEX_PLANNER_WORKER_PROBE_SCHEMA_VERSION,
        "runtime": "traex",
        "mode": "experimental_planner_worker",
        "receipt": receipt,
        "boundary": {
            "planner_sandbox": "read-only",
            "worker_sandbox": "workspace-write",
            "worker_minimal_context": bool(worker_minimal_context),
            "validation_shell": False,
            "raw_prompts_recorded": False,
            "raw_assistant_messages_recorded": False,
            "raw_stderr_recorded": False,
            "planner_stderr_present": planner.stderr_present,
            "worker_stderr_present": worker.stderr_present,
        },
    }


__all__ = [
    "DEFAULT_TRAEX_PLANNER_MODEL",
    "DEFAULT_TRAEX_WORKER_MODEL",
    "TRAEX_PLANNER_WORKER_PROBE_SCHEMA_VERSION",
    "SubprocessValidationRunner",
    "GitWorkspaceObserver",
    "TraexPlannerAdapter",
    "TraexPlannerWorkerError",
    "TraexWorkerAdapter",
    "run_traex_planner_worker_probe",
]
