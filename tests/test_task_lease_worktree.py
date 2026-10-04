"""Real sibling Git worktrees and the public lease CLI on both canonical providers."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.cli import main as cli_main
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="verified host identity currently supports macOS/Linux")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_worktree_scope_admission_and_replay(tmp_path, monkeypatch, capsys, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = tmp_path / "repo"
    project.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, check=True).stdout.strip()

    git("init")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "--allow-empty", "-m", "fixture")
    git("remote", "add", "origin", "https://github.com/example/project.git")
    a, b = tmp_path / "a", tmp_path / "b"
    git("worktree", "add", "-b", "a", str(a))
    git("worktree", "add", "-b", "b", str(b))
    alias = tmp_path / "alias"
    alias.symlink_to(a, target_is_directory=True)
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    goal = "worktree-admission"
    state.write_text("# Worktree admission\n\n## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": goal, "repo": str(project), "state_file": str(state),
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    projection = build_todo_runtime_shadow_projection(goal_id=goal, handoff_mode="hard_lease", leases=[], todos=[{
        "schema_version": "todo_item_v0", "todo_id": f"todo_worktree_{key}", "role": "agent", "status": "open", "done": False,
        "text": "Isolated code editing", "archive_state": "active", "source_section": "Agent Todo", "index": i,
        "task_class": "advancement_task", "claimed_by": owner, "task_repository": "git:github.com/example/project",
    } for i, (key, owner) in enumerate([("a", "agent-a"), ("b", "agent-b"), ("c", "agent-b"), ("d", "agent-b"), ("e", "agent-b")], 1)])
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=provider)
    state.unlink()

    def cli(action, key, *args, expected=0):
        rc = cli_main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
                       "task-lease", action, "--goal-id", goal, "--todo-id", f"todo_worktree_{key}", *args])
        output = capsys.readouterr().out
        assert rc == expected, output
        return json.loads(output)

    def acquire(key, path, expected=0, scope="src/**"):
        return cli("acquire", key, "--owner", "agent-a" if key == "a" else "agent-b",
                   "--idempotency-key", f"edit-{key}", "--ttl-seconds", "600", "--write-scope", scope,
                   *(["--write-worktree", str(path)] if path else []), expected=expected)

    invalid = acquire("a", project, expected=1)
    assert invalid["error_code"] == "invalid_worktree_lease_request"
    (a / "src").symlink_to(project, target_is_directory=True)
    assert acquire("a", a, expected=1)["error_code"] == "invalid_worktree_lease_request"
    (a / "src").unlink()
    # Ignore rules do not establish physical isolation. Check both an ignored
    # scope root and an ignored ancestor of an exact, not-yet-created file.
    (a / ".gitignore").write_text("src\noutside\n")
    (a / "src").symlink_to(project, target_is_directory=True)
    assert acquire("a", a, expected=1)["error_code"] == "invalid_worktree_lease_request"
    (a / "src").unlink()
    (a / "src").mkdir()
    (a / "src" / "redirect").symlink_to(project, target_is_directory=True)
    assert acquire("a", a, expected=1, scope="src/redirect/new.ts")["error_code"] == "invalid_worktree_lease_request"
    (a / "src" / "redirect").unlink()
    (a / "src" / "redirect").symlink_to(tmp_path / "missing", target_is_directory=True)
    assert acquire("a", a, expected=1)["error_code"] == "invalid_worktree_lease_request"
    (a / "src" / "redirect").unlink()
    # An unrelated ignored link must not prevent a narrow code-edit lease.
    (a / "outside").symlink_to(project, target_is_directory=True)
    legacy = acquire("d", None)
    assert "write_workspace" not in legacy["lease"]
    monkeypatch.chdir(a)
    first = acquire("a", Path("."))
    assert first["source_authority"] == provider + "_v0"
    assert "write_workspace" in first["lease"]
    assert first["integration_overlap_advisories"][0]["todo_id"] == "todo_worktree_d"
    assert cli("inspect", "d")["lease"] == legacy["lease"]
    assert str(tmp_path) not in json.dumps(first["lease"])
    conflict = acquire("c", alias, expected=1)
    assert conflict["error_code"] == "write_scope_conflict"
    assert conflict["conflicts"][0]["owner"] == "agent-a"
    assert "--write-worktree" in conflict["recommended_action"]
    assert acquire("e", None, expected=1)["error_code"] == "write_scope_conflict"
    second = acquire("b", Path("../b"))
    assert {row["todo_id"] for row in second["integration_overlap_advisories"]} == {"todo_worktree_a", "todo_worktree_d"}
    assert second["lease"]["write_workspace"] != first["lease"]["write_workspace"]
    replay = acquire("a", alias)
    assert replay["original_receipt"] == first["original_receipt"]
    changed = acquire("a", b, expected=1)
    assert changed["ok"] is False
    assert acquire("a", a)["original_receipt"] == first["original_receipt"]
    renewal = cli("renew", "a", "--owner", "agent-a", "--idempotency-key", "edit-a", "--expected-version", "1", "--ttl-seconds", "900")
    assert renewal["lease"]["write_workspace"] == first["lease"]["write_workspace"]
    assert acquire("a", a)["lease"]["version"] == 2
    assert cli("inspect", "a")["lease"] == renewal["lease"]
    released = cli("release", "a", "--owner", "agent-a", "--idempotency-key", "edit-a", "--expected-version", "2")
    assert released["released"]
    assert cli("inspect", "d")["lease"] == legacy["lease"]
    assert not state.exists()
