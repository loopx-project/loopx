"""A canonical completion dependency must fence execution, not only selection."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

REPO = Path(__file__).resolve().parents[2]
GOAL = "dependency-execution"
WAITING = "todo_waiting"
PREREQUISITE = "todo_prerequisite"
FALLBACK = "todo_fallback"


@pytest.fixture(params=["file", "sqlite"])
def canonical_dependency(request, tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    state.write_text("# Synthetic dependency execution\n\n## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
        "spawn_policy": {"spawn_allowed": True, "max_children": 4,
                         "explore_harness": {"enabled": True}},
    }]}))
    todos = [{
        "schema_version": "todo_item_v0", "todo_id": todo_id, "role": "agent",
        "status": "open", "done": False, "text": text, "archive_state": "active",
        "source_section": "Agent Todo", "index": index, "task_class": "advancement_task",
        "claimed_by": owner,
    } for index, (todo_id, text, owner) in enumerate([
        (PREREQUISITE, "Complete the actual prerequisite", "agent-b"),
        (WAITING, "Wait for the prerequisite before executing", "agent-a"),
        (FALLBACK, "Advance unrelated work while waiting", "agent-a"),
    ], 1)]
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL, handoff_mode="hard_lease", leases=[], todos=todos,
    )
    initialize_canonical_authority(runtime, GOAL, projection, state_path=state, provider=request.param)

    def cli(command, todo_id, *arguments, expected_exit=0):
        result = subprocess.run([
            sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json",
            *command, "--goal-id", GOAL, *(["--todo-id", todo_id] if todo_id else []), *arguments,
        ], cwd=REPO, text=True, capture_output=True, timeout=60, check=False)
        assert result.returncode == expected_exit, result.stdout + result.stderr
        return json.loads(result.stdout)

    try:
        yield cli
    finally:
        shutdown = subprocess.run([
            sys.executable, "-c",
            "from loopx.control_plane.effect_runtime import effect_runtime_result; "
            "effect_runtime_result('runtime.shutdown', {}, retry_safe=False)",
        ], cwd=REPO, text=True, capture_output=True, timeout=30, check=False)
        assert shutdown.returncode == 0, shutdown.stdout + shutdown.stderr


def _acquire(cli, todo_id, owner, key, version=0, expected_exit=0):
    return cli(["task-lease", "acquire"], todo_id, "--owner", owner, "--idempotency-key", key,
               "--expected-version", str(version), "--ttl-seconds", "600", expected_exit=expected_exit)


def _arm_wait(cli, lease_version):
    return cli(["todo", "update"], WAITING, "--agent-id", "agent-a",
               "--resume-when", f"todo_done:{PREREQUISITE}", "--successor-todo-id", FALLBACK,
               "--task-lease-idempotency-key", "waiting-original",
               "--task-lease-expected-version", str(lease_version))


def _complete(cli, todo_id, owner, key, version):
    return cli(["todo", "complete"], todo_id, "--agent-id", owner,
               "--task-lease-idempotency-key", key, "--task-lease-expected-version", str(version),
               "--evidence", "validation://synthetic-dependency-execution",
               *([] if todo_id == WAITING else ["--no-follow-up"]))


def test_wait_fences_current_proof_and_completion_but_allows_cleanup(canonical_dependency):
    cli = canonical_dependency
    first = _acquire(cli, WAITING, "agent-a", "waiting-original")
    assert first["acquired"]
    _arm_wait(cli, first["lease"]["version"])
    waiting = cli(["todo", "list"], WAITING)["todo"]
    assert waiting["resume_ready"] is False
    assert waiting["resume_condition"]["target_status"] == "open"

    # Replaying a successful receipt cannot supply executable current proof.
    assert not _acquire(cli, WAITING, "agent-a", "waiting-original", expected_exit=1)["ok"]
    assert not cli(["task-lease", "renew"], WAITING, "--owner", "agent-a",
                   "--idempotency-key", "waiting-original", "--expected-version", "1",
                   "--ttl-seconds", "600", expected_exit=1)["ok"]
    rejected = cli(["todo", "complete"], WAITING, "--agent-id", "agent-a",
                   "--task-lease-idempotency-key", "waiting-original", "--task-lease-expected-version", "1",
                   "--evidence", "validation://premature-completion", expected_exit=1)
    assert not rejected["ok"]
    assert rejected["failure_kind"] == "decision_rejection"
    inspected = cli(["task-lease", "inspect"], WAITING)
    assert inspected["lease"]["status"] == "active"
    assert inspected["lease"]["version"] == 1
    assert inspected["active"] is False
    assert cli(["todo", "list"], WAITING)["todo"]["status"] == "open"

    released = cli(["task-lease", "release"], WAITING, "--owner", "agent-a",
                   "--idempotency-key", "waiting-original", "--expected-version", "1")
    assert released["released"]
    assert not _acquire(cli, WAITING, "agent-a", "waiting-next", version=1, expected_exit=1)["ok"]

    # Unrelated work remains executable, so the dependency is not a Goal-wide stop.
    assert _acquire(cli, FALLBACK, "agent-a", "unrelated-work")["acquired"]


@pytest.mark.parametrize("archive_prerequisite", [False, True])
def test_actual_prerequisite_completion_restores_execution(canonical_dependency, archive_prerequisite):
    cli = canonical_dependency
    first = _acquire(cli, WAITING, "agent-a", "waiting-original")
    _arm_wait(cli, first["lease"]["version"])
    cli(["task-lease", "release"], WAITING, "--owner", "agent-a", "--idempotency-key", "waiting-original",
        "--expected-version", "1")

    prerequisite = _acquire(cli, PREREQUISITE, "agent-b", "prerequisite-execution")
    assert _complete(cli, PREREQUISITE, "agent-b", "prerequisite-execution", prerequisite["lease"]["version"])["completed"]
    if archive_prerequisite:
        cli(["todo", "archive-completed"], None, "--role", "agent", "--max-active-done", "0", "--execute")
    waiting = cli(["todo", "list"], WAITING)["todo"]
    assert waiting["resume_ready"] is True
    assert waiting["resume_condition"]["target_status"] == "done"
    if archive_prerequisite:
        assert waiting["resume_condition"]["target_archive_state"] == "archive"
    resumed = _acquire(cli, WAITING, "agent-a", "waiting-next", version=1)
    assert resumed["acquired"]
    assert resumed["lease"]["lease_epoch"] == 2
    assert _complete(cli, WAITING, "agent-a", "waiting-next", resumed["lease"]["version"])["completed"]


def test_wait_rejects_atomic_claim_without_changing_assignment(canonical_dependency):
    cli = canonical_dependency
    first = _acquire(cli, WAITING, "agent-a", "waiting-original")
    _arm_wait(cli, first["lease"]["version"])
    cli(["task-lease", "release"], WAITING, "--owner", "agent-a", "--idempotency-key", "waiting-original",
        "--expected-version", "1")
    request = ["--claimed-by", "agent-a", "--agent-id", "agent-a", "--claim-operation-id", "waiting-claim",
               "--task-lease-idempotency-key", "waiting-claim-execution", "--task-lease-expected-version", "1"]
    assert not cli(["todo", "claim"], WAITING, *request, expected_exit=1)["ok"]
    readback = cli(["todo", "list"], WAITING)["todo"]
    assert readback["claimed_by"] == "agent-a" and readback["resume_ready"] is False
    inspected = cli(["task-lease", "inspect"], WAITING)
    assert inspected["lease"]["status"] == "released" and inspected["lease"]["version"] == 1


def test_explore_plans_follow_dependency_through_handoff_and_completion(canonical_dependency):
    cli = canonical_dependency
    first = _acquire(cli, WAITING, "agent-a", "waiting-original")
    _arm_wait(cli, first["lease"]["version"])
    rejected = cli(["task-lease", "transfer"], WAITING, "--owner", "agent-a",
        "--idempotency-key", "waiting-original", "--expected-version", "1",
        "--new-owner", "agent-b", "--new-idempotency-key", "waiting-receiver",
        "--ttl-seconds", "600", "--transfer-claim", expected_exit=1)
    assert rejected["error_code"] == "todo_dependency_pending"
    assert cli(["todo", "list"], WAITING)["todo"]["claimed_by"] == "agent-a"
    cli(["task-lease", "release"], WAITING, "--owner", "agent-a",
        "--idempotency-key", "waiting-original", "--expected-version", "1")

    def assert_plans(*, ready):
        before = cli(["todo", "list"], WAITING)["todo"]
        for command, selected_key, rejected_key in [
            ("todo-branch-plan", "selected_branches", "rejected_candidates"),
            ("worker-branch-plan", "selected_worker_branches", "rejected_worker_branches"),
        ]:
            plan = cli(["explore", command], None, "--agent-id", "agent-a", "--scheduler-load", "0")
            selected = {todo_id for row in plan[selected_key]
                        for todo_id in row.get("todo_ids", [row["todo_id"]])}
            assert (WAITING in selected) is ready
            assert FALLBACK in selected
            if not ready:
                waiting = next(row for row in plan[rejected_key] if row["todo_id"] == WAITING)
                assert waiting["resume_ready"] is False
                assert waiting["resume_when"] == f"todo_done:{PREREQUISITE}"
                assert not waiting.get("suggested_commands")
        context = cli(["explore", "turn-context"], None, "--agent-id", "agent-a")["harness"]
        assert (WAITING in {row["todo_id"] for row in context["selected_branches"]}) is ready
        if not ready:
            waiting = next(row for row in context["rejected_candidates"] if row["todo_id"] == WAITING)
            assert waiting["resume_ready"] is False
            assert waiting["resume_when"] == f"todo_done:{PREREQUISITE}"
        assert cli(["todo", "list"], WAITING)["todo"] == before

    assert_plans(ready=False)
    prerequisite = _acquire(cli, PREREQUISITE, "agent-b", "prerequisite-original")
    transferred = cli(["task-lease", "transfer"], PREREQUISITE, "--owner", "agent-b",
        "--idempotency-key", "prerequisite-original", "--expected-version", str(prerequisite["lease"]["version"]),
        "--new-owner", "agent-a", "--new-idempotency-key", "prerequisite-receiver",
        "--ttl-seconds", "600", "--transfer-claim")
    assert transferred["transferred"]
    assert_plans(ready=False)
    assert not _acquire(cli, WAITING, "agent-a", "waiting-next", version=1, expected_exit=1)["ok"]
    assert _complete(cli, PREREQUISITE, "agent-a", "prerequisite-receiver", transferred["lease"]["version"])["completed"]
    assert_plans(ready=True)
    resumed = _acquire(cli, WAITING, "agent-a", "waiting-next", version=1)
    assert resumed["acquired"]
    assert _complete(cli, WAITING, "agent-a", "waiting-next", resumed["lease"]["version"])["completed"]
