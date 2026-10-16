#!/usr/bin/env python3
"""Smoke-test quota/status degradation when git is unavailable."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402

GOAL_ID = "no-git-quota-fixture"
AGENT_ID = "codex-benchmark-agent"


def write_fixture(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "---\n\n"
        "# Active Goal State\n\n"
        "## Objective\n\n"
        "Solve this no-git fixture.\n\n"
        "## Next Action\n\n"
        "- Pick the seeded todo.\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "no-git-quota",
                        "status": "active",
                        "repo": str(project),
                        "state_file": f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md",
                        "adapter": {"kind": "generic_project_goal_v0", "status": "connected"},
                        "coordination": {
                            "registered_agents": [AGENT_ID],
                            "agent_model": "peer_v1",
                        },
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    import_fixture_authority(registry_path, GOAL_ID)
    return registry_path


def run_cli(registry_path: Path, *args: str, env: dict[str, str] | None = None) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-no-git-quota-") as tmp:
        root = Path(tmp)
        registry_path = write_fixture(root)
        todo = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Solve the no-git quota fixture.",
        )
        assert todo["ok"] is True, todo
        before = run_cli(registry_path, "todo", "list", "--goal-id", GOAL_ID)

        no_git_bin = root / "no-git-bin"
        no_git_bin.mkdir()
        env = os.environ.copy()
        env["PATH"] = str(no_git_bin)
        env["PYTHONPATH"] = str(REPO_ROOT)
        quota = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            env=env,
        )
        assert quota.get("status") != "quota_collection_failed", quota
        assert quota["should_run"] is True, quota
        assert quota["recommended_action"] == "[P1] Solve the no-git quota fixture.", quota
        assert quota["selected_todo"]["todo_id"] == todo["todo_id"], quota
        after = run_cli(registry_path, "todo", "list", "--goal-id", GOAL_ID, env=env)
        assert after["todos"] == before["todos"], after
        assert after["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"], after
        assert after["authority_read"]["legacy_fallback_used"] is False, after
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
