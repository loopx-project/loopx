"""Disposable public CLI fixtures for retained shadow management tests."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from dataclasses import dataclass

REPO = Path(__file__).resolve().parents[2]


@dataclass
class ShadowWorkspace:
    registry: Path
    runtime: Path
    state: Path
    goal: str = "goal-e2e"
    command: tuple[str, ...] = (sys.executable, "-m", "loopx.cli")
    cwd: Path = REPO

    def arguments(self, *args: str) -> list[str]:
        return [
            "--registry",
            str(self.registry),
            "--runtime-root",
            str(self.runtime),
            "--format",
            "json",
            *args,
            "--goal-id",
            self.goal,
        ]

    def cli(self, *args: str, success: bool = True) -> dict:
        result = subprocess.run(
            [*self.command, *self.arguments(*args)],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if success:
            assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert "Traceback" not in result.stderr, result.stderr
        return json.loads(result.stdout)

    def add(self, text: str) -> dict:
        return self.cli("todo", "add", "--role", "agent", "--text", text)


def workspace(path: Path, *, bootstrap: bool = True) -> ShadowWorkspace:
    path.mkdir(parents=True, exist_ok=True)
    state = path / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\ngoal_id: goal-e2e\nhandoff_mode: hard_lease\n"
        "updated_at: 2026-09-01T00:00:00+00:00\n---\n\n## Agent Todo\n\n",
        encoding="utf-8",
    )
    runtime, registry = path / "runtime", path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": "goal-e2e",
                        "status": "active",
                        "repo": str(path),
                        "state_file": state.name,
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["agent-a", "agent-b"],
                            "runtime_shadow": {
                                "schema_version": "loopx_coordination_runtime_shadow_config_v0",
                                "enabled": True,
                                "provider": "file_v0",
                            },
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = ShadowWorkspace(registry, runtime, state)
    if bootstrap:
        boot = result.cli("coordination-shadow", "bootstrap", "--execute")["bootstrap"]
        assert boot["status"] == "applied", boot
    return result
