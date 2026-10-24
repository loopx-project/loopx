"""Scope comparison must preserve request identity and accepted declarations."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

REPO = Path(__file__).resolve().parents[2]
ALIASES = ["src//main.ts", "src/./main.ts"]


@pytest.fixture(params=["file", "sqlite"])
def scope_goal(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    goal = "scope-contract"
    state.write_text("# Synthetic scope contract\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": goal, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    todos = [{"schema_version": "todo_item_v0", "todo_id": "todo_scope_" + suffix,
        "role": "agent", "task_class": "advancement_task", "status": "open", "done": False,
        "text": "Synthetic scope work " + suffix, "archive_state": "active",
        "source_section": "Agent Todo", "index": index,
        "task_repository": "git:github.com/example/project",
        "required_write_scopes": ALIASES if suffix == "a" else [],
    } for index, suffix in enumerate(["a", "b"], 1)]
    projection = build_todo_runtime_shadow_projection(goal_id=goal, handoff_mode="hard_lease", todos=todos)
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=request.param)

    def cli(*args, cwd=None, expected_exit=0):
        selected_repo = Path(os.environ.get("LOOPX_LEASE_SCOPE_CONSUMER", REPO)) if cwd is None else cwd
        child = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", *args, "--goal-id", goal],
            cwd=selected_repo, capture_output=True, text=True, timeout=60, check=False)
        assert child.returncode == expected_exit, child.stdout + child.stderr
        return json.loads(child.stdout)

    def current():
        return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal, include_leases=True)

    return cli, current


def test_alias_acquire_keeps_original_request_identity_after_renewal(scope_goal):
    cli, current = scope_goal
    acquire = ("task-lease", "acquire", "--todo-id", "todo_scope_a", "--owner", "agent-a",
        "--idempotency-key", "alias-a", "--expected-version", "0", "--ttl-seconds", "600",
        "--write-scope", "src//main.ts")
    producer = Path(os.environ.get("LOOPX_LEASE_SCOPE_PRODUCER", REPO))
    first = cli(*acquire, cwd=producer)
    original = first["original_receipt"]
    before = current()
    replay = cli(*acquire)
    assert replay["idempotent"] and replay["original_receipt"] == original
    assert current() == before
    renewed = cli("task-lease", "renew", "--todo-id", "todo_scope_a", "--owner", "agent-a",
        "--idempotency-key", "alias-a", "--expected-version", "1", "--ttl-seconds", "600")
    before = current()
    replay = cli(*acquire)
    assert replay["lease"] == renewed["lease"]
    assert replay["lease"]["version"] == 2 and replay["lease"]["lease_epoch"] == 1
    assert replay["original_receipt"] == original and current() == before
    changed = (*acquire[:-1], "src/main.ts")
    rejected = cli(*changed, expected_exit=1)
    assert rejected["error_code"] == "coordination_operation_identity_mismatch"
    assert current() == before


def test_retained_alias_declaration_can_be_claimed(scope_goal):
    cli, _ = scope_goal
    claimed = cli("todo", "claim", "--todo-id", "todo_scope_a", "--claimed-by", "agent-a",
        "--agent-id", "agent-a", "--claim-operation-id", "alias-claim",
        "--task-lease-idempotency-key", "alias-claim", "--task-lease-expected-version", "0")
    assert claimed["ok"]
    assert cli("task-lease", "inspect", "--todo-id", "todo_scope_a")["lease"]["write_scopes"] == ["src/main.ts"]
    assert cli("todo", "list", "--todo-id", "todo_scope_a")["todo"]["required_write_scopes"] == ALIASES


def test_authored_alias_declaration_can_be_claimed_without_revision(scope_goal):
    cli, _ = scope_goal
    added = cli("todo", "add", "--role", "agent", "--priority", "P1", "--text", "Acquire alias work",
        "--task-repository", "git:github.com/example/project", "--required-write-scope", ALIASES[0],
        "--required-write-scope", ALIASES[1], "--operation-id", "scope-add")
    todo_id = added["todo_id"]
    claimed = cli("todo", "claim", "--todo-id", todo_id, "--claimed-by", "agent-a", "--agent-id", "agent-a",
        "--claim-operation-id", "authored-claim", "--task-lease-idempotency-key", "authored-claim",
        "--task-lease-expected-version", "0")
    assert claimed["ok"]
    assert cli("task-lease", "inspect", "--todo-id", todo_id)["lease"]["write_scopes"] == ["src/main.ts"]
    assert cli("todo", "list", "--todo-id", todo_id)["todo"]["required_write_scopes"] == ALIASES


@pytest.mark.parametrize("scope,target", [
    ("src/[]/./]x", "src/.x"),
    ("src/[]/./]x", "src/]x"),
    ("src/[!]/./]x", "src/ax"),
])
def test_glob_class_members_keep_exclusive_scope_coverage(scope_goal, scope, target):
    cli, current = scope_goal
    first = cli("task-lease", "acquire", "--todo-id", "todo_scope_a", "--owner", "agent-a",
        "--idempotency-key", "class-a", "--write-scope", scope, "--expected-version", "0")
    assert first["lease"]["write_scopes"] == [scope]
    before = current()
    rejected = cli("task-lease", "acquire", "--todo-id", "todo_scope_b", "--owner", "agent-b",
        "--idempotency-key", "class-b", "--write-scope", target, "--expected-version", "0", expected_exit=1)
    assert rejected["error_code"] == "write_scope_conflict" and current() == before
