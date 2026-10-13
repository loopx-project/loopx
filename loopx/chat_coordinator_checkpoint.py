"""Chat transport for an already committed coordinator result's native checkpoint."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .collaboration_mcp import _pinned_release_environment, _python_module_command
from .goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
from .agent_registry import load_goal_from_registry
from .todos import list_goal_todos


class _CheckpointControlPlane(GoalModeMCPControlPlane):
    def __init__(self, registry: Path, root: Path, goal_id: str, agent_id: str):
        self.registry, self.root = registry, root
        super().__init__(GoalModeMCPConfig(
            server_name="loopx-chat", runtime_profile="codex_cli",
            legacy_host_surface="codex_cli",
        ), lambda: {"goal_id": goal_id, "agent_id": agent_id, "registry": str(registry)})

    def run_cli(self, args, *, legacy_args=None):
        goal = load_goal_from_registry(self.registry, self.context()[0])
        if not goal:
            raise ValueError("Goal unavailable")
        prefix = [*_python_module_command("loopx.cli"), "--registry", str(self.registry),
                  "--runtime-root", str(self.root), "--format", "json"]
        result = subprocess.run([*prefix, *args], cwd=goal["repo"],
            env=_pinned_release_environment(), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        return result.stdout or json.dumps({"ok": False, "error": "checkpoint_command_unavailable"})


class CoordinatorCheckpoints:
    """Only resume post-result work; native owners retain all acceptance decisions."""
    def __init__(self, registry: Path, root: Path, goal_id: str, agent_id: str):
        self.registry, self.root, self.goal_id, self.agent_id = registry, root, goal_id, agent_id
        self.control = _CheckpointControlPlane(registry, root, goal_id, agent_id)

    def _task(self, todo_id: str) -> dict:
        todo = list_goal_todos(registry_path=self.registry, runtime_root_arg=str(self.root),
                              goal_id=self.goal_id, todo_id=todo_id).get("todo")
        if (not todo or todo.get("claimed_by") != self.agent_id or todo.get("status") != "done"
                or not todo.get("completion_turn_key") or not self.control._original_work_turn(todo_id)):
            raise ValueError("the coordinator has no committed original result for this task")
        return todo

    def read(self, todo_id: str) -> dict:
        self._task(todo_id)
        return json.loads(self.control.review_task_vision(todo_id, self.agent_id))

    def resume(self, todo_id: str, decision: dict) -> dict:
        if (not isinstance(decision, dict)
                or set(decision) - {"read_context_id", "agent_vision", "vision_unchanged_reason"}
                or not isinstance(decision.get("read_context_id"), str)
                or not decision["read_context_id"].strip()
                or bool(decision.get("agent_vision")) == bool(decision.get("vision_unchanged_reason"))):
            raise ValueError("resume requires the Agent's fresh checkpoint context and one vision decision")
        todo = self._task(todo_id)
        return json.loads(self.control.complete_task(todo_id, self.agent_id, todo.get("evidence") or "",
            read_context_id=decision["read_context_id"], agent_vision=decision.get("agent_vision"),
            vision_unchanged_reason=decision.get("vision_unchanged_reason") or ""))
