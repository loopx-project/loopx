"""Narrow LoopX CLI seam for the native KunlunCode Goal controller."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from loopx.control_plane.agents.workspace_guard import capture_delivery_workspace


AVAILABLE_CAPABILITIES = ("shell", "filesystem_write")
VERIFIED_CLASSIFICATIONS = {
    "goal": "kunluncode_native_goal_verified",
    "goal-pro": "kunluncode_native_goal_pro_verified",
}


class KunlunNativeGoalRuntimeError(RuntimeError):
    """Raised when native Goal execution or LoopX reconciliation fails closed."""


def _default_loopx_prefix() -> list[str]:
    executable = shutil.which("loopx")
    return [executable] if executable else [sys.executable, "-m", "loopx.cli"]


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _default_command_runner(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


class LoopXControlPlane:
    """JSON CLI operations used by the native KunlunCode controller."""

    def __init__(
        self,
        project: Path,
        context: Mapping[str, Any],
        *,
        command_prefix: list[str] | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self.project = project.resolve()
        self.registry = str(context.get("registry") or "")
        self.goal_id = str(context.get("goal_id") or "")
        self.agent_id = str(context.get("agent_id") or "")
        if not self.registry or not self.goal_id or not self.agent_id:
            raise KunlunNativeGoalRuntimeError(
                "KunlunCode binding is missing registry, goal, or agent identity"
            )
        self.command_prefix = list(command_prefix or _default_loopx_prefix())
        self.runner = runner or _default_command_runner

    def _run_json(self, arguments: list[str], *, timeout: int = 120) -> dict[str, Any]:
        command = [
            *self.command_prefix,
            "--registry",
            self.registry,
            "--format",
            "json",
            *arguments,
        ]
        result = self.runner(command, cwd=self.project, timeout=timeout)
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            diagnostic = (result.stderr or result.stdout or "")[-1200:]
            raise KunlunNativeGoalRuntimeError(
                "LoopX returned non-JSON output"
                + (f": {diagnostic}" if diagnostic else "")
            ) from exc
        if not isinstance(payload, dict):
            raise KunlunNativeGoalRuntimeError(
                "LoopX returned a non-object JSON payload"
            )
        if result.returncode != 0 or payload.get("ok") is False:
            reason = payload.get("error") or payload.get("reason") or result.stderr
            raise KunlunNativeGoalRuntimeError(
                "LoopX control-plane command failed: "
                + str(reason or "unknown error")[-1200:]
            )
        return payload

    @staticmethod
    def _capability_args() -> list[str]:
        return [
            argument
            for capability in AVAILABLE_CAPABILITIES
            for argument in ("--available-capability", capability)
        ]

    def should_run(self) -> dict[str, Any]:
        return self._run_json(
            [
                "quota",
                "should-run",
                "--goal-id",
                self.goal_id,
                "--agent-id",
                self.agent_id,
                "--runtime-profile",
                "kunluncode",
                *self._capability_args(),
            ]
        )

    def claim(self, todo_id: str) -> dict[str, Any]:
        return self._run_json(
            [
                "todo",
                "claim",
                "--goal-id",
                self.goal_id,
                "--todo-id",
                todo_id,
                "--claimed-by",
                self.agent_id,
                "--agent-id",
                self.agent_id,
            ]
        )

    def evidence_since(self, since: str, *, todo_id: str) -> dict[str, Any]:
        # Recovery reads its owning persisted event source directly. An agent-facing
        # diagnostic command is not a dependency of native writeback reconciliation.
        from loopx.history import load_registry
        from loopx.paths import resolve_runtime_root
        from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path
        from loopx.control_plane.runtime.agent_evidence_history import build_agent_scoped_evidence_log

        try:
            registry_path = Path(self.registry).expanduser()
            if not registry_path.is_absolute():
                registry_path = self.project / registry_path
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(registry, None, registry_path=registry_path)
            events = load_rollout_events(rollout_event_log_path(runtime_root, self.goal_id), limit=400)
            return build_agent_scoped_evidence_log(
                goal_id=self.goal_id, agent_id=self.agent_id, todo_id=todo_id,
                since=since, rollout_events=events, history_runs=[], limit=128,
            )
        except (OSError, ValueError, TypeError) as exc:
            raise KunlunNativeGoalRuntimeError(f"writeback evidence unavailable: {exc}") from exc

    def record_verified_delivery(self, *, mode: str, todo_id: str) -> dict[str, Any]:
        workspace_arguments = (
            ["--delivery-workspace-path", str(self.project)]
            if capture_delivery_workspace(self.project)
            else []
        )
        return self._run_json(
            [
                "refresh-state",
                "--goal-id",
                self.goal_id,
                "--todo-id",
                todo_id,
                "--project",
                str(self.project),
                "--classification",
                VERIFIED_CLASSIFICATIONS[mode],
                "--recommended-action",
                "Reconcile the verifier-approved KunlunCode todo and continue from LoopX quota.",
                "--delivery-batch-scale",
                "single_surface",
                "--delivery-outcome",
                "outcome_progress",
                *workspace_arguments,
                "--agent-id",
                self.agent_id,
                "--progress-scope",
                "goal",
                *self._capability_args(),
                "--suppress-external-sinks",
            ],
            timeout=300,
        )

    def complete(self, todo_id: str, *, evidence: str) -> dict[str, Any]:
        return self._run_json(
            [
                "todo",
                "complete",
                "--goal-id",
                self.goal_id,
                "--todo-id",
                todo_id,
                "--claimed-by",
                self.agent_id,
                "--agent-id",
                self.agent_id,
                "--evidence",
                evidence,
                "--no-follow-up",
            ]
        )

    def spend(self, *, todo_id: str) -> dict[str, Any]:
        return self._run_json(
            [
                "quota",
                "spend-slot",
                "--goal-id",
                self.goal_id,
                "--todo-id",
                todo_id,
                "--slots",
                "1",
                "--source",
                "adapter",
                "--execute",
                "--agent-id",
                self.agent_id,
                *self._capability_args(),
            ],
            timeout=300,
        )
