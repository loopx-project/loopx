"""Real CLI admission with dependency links and independent Git checkouts."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="worktree lease fixture requires symlinks; Windows needs Developer Mode")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_dependency_links_replay_and_recovery_preserve_real_lease_authority(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = (tmp_path / "project").resolve()
    first, second = (tmp_path / "worker a").resolve(), (tmp_path / "worker b").resolve()
    runtime, registry = tmp_path / "runtime", tmp_path / "registry.json"

    def git(*args):
        subprocess.run(["git", *map(str, args)], capture_output=True, check=True)

    git("init", project)
    git("-C", project, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
        "commit", "--allow-empty", "-m", "fixture")
    git("-C", project, "config", "remote.origin.url", "https://github.com/example/project.git")
    for workspace, branch in [(first, "worker-a"), (second, "worker-b")]:
        git("-C", project, "worktree", "add", "-b", branch, workspace)
        (workspace / ".gitignore").write_text("node_modules/\n")
        (workspace / "src/node_modules/.bin").mkdir(parents=True)
        (workspace / "src/node_modules/tool").mkdir()
        (workspace / "src/node_modules/tool/bin.mjs").write_text("export {};\n")
    goal, task_a, task_b = "checkout-admission", "todo_worker_a", "todo_worker_b"
    state = project / "state.md"
    state.write_text("# Synthetic worktree admission\n\n## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": goal, "repo": str(project), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    projection = build_todo_runtime_shadow_projection(goal_id=goal, handoff_mode="hard_lease", leases=[], todos=[{
        "schema_version": "todo_item_v0", "todo_id": task, "role": "agent", "status": "open", "done": False,
        "text": "Edit isolated source", "archive_state": "active", "source_section": "Agent Todo",
        "index": index, "task_class": "advancement_task", "claimed_by": owner,
        "task_repository": "git:github.com/example/project", "required_write_scopes": ["src/**"],
    } for index, task, owner in [(1, task_a, "agent-a"), (2, task_b, "agent-b")]])
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=provider)

    def cli(action, task, *flags, exit_code=0):
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", "task-lease", action,
            "--goal-id", goal, "--todo-id", task, *flags], cwd=ROOT,
            capture_output=True, text=True, timeout=60)
        assert result.returncode == exit_code, result.stdout + result.stderr
        return json.loads(result.stdout)

    def acquire(task, owner, key, version, workspace, exit_code=0):
        return cli("acquire", task, "--owner", owner, "--idempotency-key", key,
            "--expected-version", str(version), "--write-scope", "src/**",
            "--write-worktree", str(workspace), exit_code=exit_code)

    def release(task, owner, key, version):
        return cli("release", task, "--owner", owner, "--idempotency-key", key, "--expected-version", str(version))

    try:
        clean = acquire(task_a, "agent-a", "clean", 0, first)
        released = release(task_a, "agent-a", "clean", clean["lease"]["version"])
        for workspace in (first, second):
            (workspace / "src/node_modules/.bin/tool").symlink_to("../tool/bin.mjs")
        admitted = acquire(task_a, "agent-a", "with-dependencies", released["lease"]["version"], first)
        assert admitted["lease"]["write_workspace"] == clean["lease"]["write_workspace"]
        assert admitted["lease"]["write_scopes"] == ["src/**"]
        assert admitted["source_authority"] == provider + "_v0"
        renewed = cli("renew", task_a, "--owner", "agent-a", "--idempotency-key", "with-dependencies",
                      "--expected-version", str(admitted["lease"]["version"]), "--ttl-seconds", "600")
        current = renewed["lease"]
        assert current["version"] == admitted["lease"]["version"] + 1
        assert current["write_workspace"] == admitted["lease"]["write_workspace"]
        assert current["write_scopes"] == ["src/**"]

        # Original receipt does not authorize a now redirected workspace.
        link = first / "src/node_modules/.bin/tool"
        link.unlink()
        outside = tmp_path / "outside.mjs"
        outside.write_text("export {};\n")
        link.symlink_to(outside)
        denied = acquire(task_a, "agent-a", "with-dependencies", released["lease"]["version"], first, exit_code=1)
        assert denied["error_code"] == "invalid_worktree_lease_request"
        assert "file links" in denied["error"] and str(tmp_path) not in denied["error"]
        assert cli("inspect", task_a)["lease"] == current
        link.unlink()
        link.symlink_to("../tool/bin.mjs")
        replay = acquire(task_a, "agent-a", "with-dependencies", released["lease"]["version"], first)
        assert replay["idempotent"] and replay["original_receipt"] == admitted["original_receipt"]
        assert replay["lease"] == current

        denied = acquire(task_b, "agent-b", "same-checkout", 0, first, exit_code=1)
        assert denied["error_code"] == "write_scope_conflict"
        independent = acquire(task_b, "agent-b", "separate-checkout", 0, second)
        assert any(row["todo_id"] == task_a for row in independent["integration_overlap_advisories"])
        assert independent["lease"]["write_workspace"]["worktree"] != admitted["lease"]["write_workspace"]["worktree"]
        denied = acquire(task_a, "agent-b", "wrong-owner", 0, first, exit_code=1)
        assert not denied["ok"]
        denied = cli("renew", task_a, "--owner", "agent-a", "--idempotency-key", "with-dependencies",
                     "--expected-version", "0", exit_code=1)
        assert denied["error_code"] == "version_mismatch"
        assert cli("inspect", task_a)["lease"] == current
        assert release(task_a, "agent-a", "with-dependencies", current["version"])["released"]
        assert release(task_b, "agent-b", "separate-checkout", independent["lease"]["version"])["released"]
        assert not cli("inspect", task_a)["active"] and not cli("inspect", task_b)["active"]
    finally:
        subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import effect_runtime_result; "
            "effect_runtime_result('runtime.shutdown', {}, retry_safe=False)"],
            cwd=ROOT, capture_output=True, timeout=30, check=True)
