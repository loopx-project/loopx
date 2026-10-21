#!/usr/bin/env python3
"""Keep scheduler ACK writes on the registry/runtime that emitted the hint."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_ID = "codex-scheduler-route-smoke"
GOAL_ID = "scheduler-route-smoke"
sys.path.insert(0, str(REPO_ROOT))
from examples.control_plane.quota_plan_fixtures import write_codex_heartbeat_fixture  # noqa: E402


def write_registry(path: Path, *, project: Path, runtime: Path) -> None:
    state_file = project / ".loopx" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        "---\nstatus: active\nupdated_at: 2026-01-01T00:00:00+00:00\n---\n\n"
        f"# {GOAL_ID}\n\n## Agent Todo\n\n- [ ] Run scheduler route smoke.\n",
        encoding="utf-8",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "scheduler-route-smoke",
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_file.relative_to(project)),
                        "adapter": {
                            "kind": "read_only_project_map_v0",
                            "status": "connected-read-only",
                        },
                        "coordination": {
                            "registered_agents": [AGENT_ID],
                            "agent_model": "peer_v1",
                        },
                        "quota": {"compute": 1.0, "window_hours": 24},
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def run_cli(*args: str, cwd: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict), payload
    return payload


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-scheduler-registry-route-") as tmp, patch.dict(os.environ):
        root = Path(tmp)
        codex_home = root / "codex-home"
        os.environ.update(CODEX_HOME=str(codex_home), CODEX_THREAD_ID="scheduler-fixture")
        write_codex_heartbeat_fixture(codex_home, goal_id=GOAL_ID, agent_id=AGENT_ID, rrule="FREQ=MINUTELY;INTERVAL=3")
        project = root / "project"
        project.mkdir()
        project_registry = project / ".loopx" / "registry.json"
        project_runtime = project / ".loopx" / "runtime"
        global_registry = root / "shared" / "registry.global.json"
        global_runtime = root / "shared" / "runtime"
        write_registry(project_registry, project=project, runtime=project_runtime)
        write_registry(global_registry, project=project, runtime=global_runtime)

        decision = run_cli(
            "--registry",
            str(global_registry),
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--codex-app",
            "--available-capability",
            "network",
            "--available-capability",
            "external_write",
            "--codex-app-current-rrule",
            "FREQ=MINUTELY;INTERVAL=3",
            "--turn-envelope",
            cwd=project,
        )
        ack_cli_args = decision["scheduler"]["codex_app"]["ack_cli_args"]
        assert ack_cli_args[:4] == [
            "--registry",
            str(global_registry.resolve()),
            "--runtime-root",
            str(global_runtime.resolve()),
        ], decision
        assert ack_cli_args.count("--available-capability") == 2, decision
        assert "--host-match-observed" in ack_cli_args, decision
        assert "--reset-token" in ack_cli_args, decision
        assert "--identity-signature" in ack_cli_args, decision
        assert ack_cli_args[-1] == "--execute", decision

        ack = run_cli(*ack_cli_args,  cwd=project)
        state_path = Path(ack["scheduler_state_path"])
        assert state_path.resolve().is_relative_to(global_runtime.resolve()), ack
        assert state_path.exists(), ack
        assert not project_runtime.exists(), project_runtime

        follow_up = run_cli(
            "--registry",
            str(global_registry),
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--codex-app",
            cwd=project,
        )
        stateful = follow_up["scheduler_hint"]["codex_app"]["stateful_backoff"]
        assert stateful["state_status"] == "same_identity", follow_up

    print("quota scheduler registry route smoke passed")


if __name__ == "__main__":
    main()
