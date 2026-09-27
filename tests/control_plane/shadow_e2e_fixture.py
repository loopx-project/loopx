"""Disposable public CLI fixtures and scheduling-only process crash seams."""

from __future__ import annotations

import json
from pathlib import Path
import select
import subprocess
import sys
from dataclasses import dataclass

from loopx.control_plane.testing.authority_e2e_rows_stage2c2 import CRASH_WORKER

REPO = Path(__file__).resolve().parents[2]


@dataclass
class ShadowWorkspace:
    registry: Path
    runtime: Path
    state: Path
    goal: str = "goal-e2e"

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
            [sys.executable, "-m", "loopx.cli", *self.arguments(*args)],
            cwd=REPO,
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

    def drain(self, **limits: str) -> dict:
        args = [
            item
            for key, value in limits.items()
            for item in ("--" + key.replace("_", "-"), value)
        ]
        return self.cli("authority-shadow", "drain", *args, success=False)

    def crash(self, window: str, *args: str) -> dict:
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                CRASH_WORKER,
                window,
                str(self.state),
                *self.arguments(*args),
            ],
            cwd=REPO,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert child.stdout is not None
        try:
            readable, _, _ = select.select([child.stdout], [], [], 30)
            assert readable, "public CLI did not reach the requested persistence window"
            line = child.stdout.readline()
            if not line.startswith("BARRIER "):
                child.kill()
                stdout, stderr = child.communicate(timeout=10)
                raise AssertionError(f"No process barrier: {line}{stdout}\n{stderr}")
            payload = json.loads(line.removeprefix("BARRIER "))
            if payload.get("native_pid"):
                assert child.stdin is not None
                child.stdin.write("terminate_native\n")
                child.stdin.flush()
                readable, _, _ = select.select([child.stdout], [], [], 10)
                assert readable and child.stdout.readline().strip() == "REAPED", "native owner must be reaped"
            child.kill()
            child.communicate(timeout=10)
            assert child.returncode == -9
            return payload
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)


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
