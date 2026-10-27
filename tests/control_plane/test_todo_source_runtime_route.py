"""Global read entrypoints follow the declared source, without importing it."""
from __future__ import annotations

import json

import pytest

from canonical_authority_fixture import (
    assert_todo_read_unchanged,
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from loopx.chat_manager_details import read_manager_goal_details
from loopx.contract import check_contract
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from loopx.control_plane.todos.list_readback import list_goal_todos


GOAL = "source-route-fixture"
LONG_TEXT = "[P1] Retain the complete original requirement. " + "正文🙂 " * 240 + "Keep the final constraint."


def seed(runtime, state, provider, *, prefix="source"):
    records = [{"schema_version": "todo_item_v0", "todo_id": f"todo_{prefix}_{i}",
        "role": "agent", "task_class": "advancement_task", "claimed_by": "agent-a",
        "text": LONG_TEXT if i == 0 else f"Keep {prefix} result {i}.",
        "note": "Retain the original completion evidence.",
        "status": "open" if i == 0 else "done", "done": i != 0,
        "archive_state": "archive" if i == 5 else "active",
        "source_section": "Completed Work Archive" if i == 5 else "Agent Todo", "index": i + 1}
        for i in range(6)]
    initialize_canonical_authority(runtime, GOAL,
        build_todo_runtime_shadow_projection(goal_id=GOAL, todos=records, handoff_mode="soft_claim"),
        state_path=state, provider=provider)


@pytest.fixture(params=["file", "sqlite"])
def routed(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = tmp_path / "project"
    source = project / ".loopx" / "registry.json"
    source.parent.mkdir(parents=True)
    state = project / "state.md"
    state.write_text("# Goal\n\nKeep original work.\n\n## User Todo\n\n## Agent Todo\n\n## Completed Work Archive\n")
    goal = {"id": GOAL, "repo": str(project), "state_file": state.name,
            "domain": "generic", "adapter": {"kind": "generic"},
            "coordination": {"registered_agents": ["agent-a"]}}
    source.write_text(json.dumps({"schema_version": 1, "common_runtime_root": ".loopx/runtime", "goals": [goal]}))
    runtime, shared = project / ".loopx/runtime", tmp_path / "shared"
    global_registry = shared / "registry.global.json"
    shared.mkdir()
    global_registry.write_text(json.dumps({"schema_version": 1, "registry_role": "global-local",
        "common_runtime_root": str(shared), "goals": [{**goal, "source_registry": str(source),
            "coordination": {"registered_agents": ["stale-agent"]}}]}))
    seed(runtime, state, request.param)
    return source, runtime, global_registry, shared, state, request.param


def diagnostics(registry, *, override=None):
    return check_contract(registry_path=registry, runtime_root_override=override,
        scan_roots=[], limit=50, goal_id_filter=GOAL, include_public_boundary_scan=False)


def test_global_cli_check_and_manager_read_the_original_provider(routed):
    source, runtime, registry, shared, state, _ = routed
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    original = state.read_bytes(), source.read_bytes(), registry.read_bytes()
    expected = list_goal_todos(registry_path=source, goal_id=GOAL, json_references=True)
    code, actual = run_json_cli_result("todo", "list", "--goal-id", GOAL, registry_path=registry)
    assert code == 0, actual
    assert actual.pop("_returncode") == 0  # Test harness metadata, outside the CLI payload.
    assert_todo_read_unchanged(actual, expected)
    assert actual["todo_count"] == 5
    assert {row["todo_id"] for row in actual["todos"]} == {f"todo_source_{i}" for i in range(5)}
    code, exact = run_json_cli_result("todo", "list", "--goal-id", GOAL,
        "--todo-id", "todo_source_0", registry_path=registry)
    assert code == 0 and exact["todo"]["text"] == LONG_TEXT
    assert diagnostics(registry)["error_diagnostics"] == []
    manager = read_manager_goal_details(registry, shared, GOAL, owner_scope=True, todo_id="todo_source_0")
    assert manager["status"] == "read" and manager["authority_revision"] == before["provider_revision"]
    assert manager["todos"][0]["title"] == LONG_TEXT
    assert manager["todos"][0]["continuation"] == "Retain the original completion evidence."
    group = read_manager_goal_details(registry, shared, GOAL, owner_scope=False, todo_id="todo_source_0")
    assert group["status"] == "read" and "continuation" not in group["todos"][0]
    history = list_goal_todos(registry_path=registry, goal_id=GOAL,
        role="agent", status="done", read_scope="completed_history", todo_id="todo_source_5")
    assert history["todo"]["text"] == "Keep source result 5."
    assert (state.read_bytes(), source.read_bytes(), registry.read_bytes()) == original
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL) == before
    assert not (shared / "goals" / GOAL).exists(), "Reading must not create a second authority"


def test_explicit_runtime_override_retains_its_diagnostic_meaning(routed):
    source, _, registry, shared, state, provider = routed
    seed(shared, state, provider, prefix="explicit")
    override = str(shared)
    expected = list_goal_todos(registry_path=registry, goal_id=GOAL, runtime_root_arg=override)
    assert expected["todos"][0]["todo_id"] == "todo_explicit_0"
    code, actual = run_json_cli_result("todo", "list", "--goal-id", GOAL,
        registry_path=registry, runtime_root=shared)
    assert code == 0 and actual["todos"][0]["todo_id"] == "todo_explicit_0"
    assert list_goal_todos(registry_path=registry, goal_id=GOAL)["todos"][0]["todo_id"] == "todo_source_0"
    assert list_goal_todos(registry_path=source, goal_id=GOAL)["todos"][0]["todo_id"] == "todo_source_0"
    assert diagnostics(registry, override=override)["error_diagnostics"] == []


@pytest.mark.parametrize("failure", ["missing", "unreadable", "wrong_goal"])
def test_bad_source_never_falls_back_to_a_valid_shared_authority(routed, failure):
    source, _, registry, shared, state, provider = routed
    seed(shared, state, provider, prefix="decoy")
    if failure == "wrong_goal":
        payload = json.loads(source.read_text())
        payload["goals"][0]["id"] = "unrelated-goal"
        source.write_text(json.dumps(payload))
    else:
        source.unlink()
        if failure == "unreadable":
            source.mkdir()  # A real IO refusal, independent of the test user's privilege.
    with pytest.raises((OSError, ValueError), match="source_registry"):
        list_goal_todos(registry_path=registry, goal_id=GOAL)
    code, result = run_json_cli_result("todo", "list", "--goal-id", GOAL, registry_path=registry)
    assert code == 1 and not result["ok"] and "source_registry" in result["error"]
    errors = diagnostics(registry)["error_diagnostics"]
    assert len(errors) == 1 and errors[0]["code"] == "canonical_todo_contract_diagnostics_unavailable"
    assert "source_registry" in errors[0]["message"]
    assert read_manager_goal_details(registry, shared, GOAL, owner_scope=True)["status"] == "unavailable"
    assert list_goal_todos(registry_path=registry, goal_id=GOAL,
        runtime_root_arg=str(shared))["todos"][0]["todo_id"] == "todo_decoy_0"


def test_each_read_rechecks_the_source_registration(routed):
    source, _, registry, shared, state, provider = routed
    assert list_goal_todos(registry_path=registry, goal_id=GOAL)["todos"][0]["todo_id"] == "todo_source_0"
    replacement = state.parent / "replacement-runtime"
    seed(replacement, state, provider, prefix="replacement")
    payload = json.loads(source.read_text())
    payload["common_runtime_root"] = str(replacement)
    source.write_text(json.dumps(payload))
    assert list_goal_todos(registry_path=registry, goal_id=GOAL)["todos"][0]["todo_id"] == "todo_replacement_0"
    assert diagnostics(registry)["error_diagnostics"] == []
    manager = read_manager_goal_details(registry, shared, GOAL, owner_scope=True, todo_id="todo_replacement_1")
    assert manager["status"] == "read" and manager["todos"][0]["title"] == "Keep replacement result 1."
    payload["goals"] = []
    source.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="absent from source_registry"):
        list_goal_todos(registry_path=registry, goal_id=GOAL)
    assert "absent from source_registry" in diagnostics(registry)["error_diagnostics"][0]["message"]
    assert read_manager_goal_details(registry, shared, GOAL, owner_scope=True)["status"] == "unavailable"
