"""Legacy Agent classification survives archive/capture without inventing authority."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.authority_source_capture import (
    build_runtime_shadow_source_snapshot, capture_todo_archive_dependencies,
)
from loopx.control_plane.effect_runtime import EffectRuntimeRejected
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
from loopx.control_plane.todos.completed_archive import archive_completed_todo_lines
from loopx.control_plane.todos.todo_summary import structured_todo_item
from loopx.control_plane.testing.canary_harness import run_json_cli_result


def workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    runtime = tmp_path / "runtime"
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text("---\ngoal_id: goal-a\nhandoff_mode: legacy\n---\n")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({
        "common_runtime_root": str(runtime),
        "goals": [{
            "id": "goal-a",
            "repo": str(tmp_path),
            "state_file": state.name,
            "coordination": {"registered_agents": ["agent-a"]},
        }],
    }))
    return registry, runtime, state


def row(todo_id: str, *, text: str = "Delivered result", metadata: str = "", marker: str = "x") -> str:
    return f"- [{marker}] {text}\n  <!-- loopx:todo todo_id={todo_id} {metadata} -->\n"


@pytest.mark.parametrize("text,metadata,expected", [
    ("Delivered result", "", "advancement_task"),
    ("Observe the remote job", "action_kind=monitor", "continuous_monitor"),
    ("Monitor the remote job", "action_kind=implement", "advancement_task"),
    ("Delivered result", "task_class=blocker", "blocker"),
])
def test_archive_and_capture_preserve_existing_read_class(text, metadata, expected):
    original = "## Agent Todo\n" + row("todo_prior", text=text, metadata=metadata + " receipt_extension=v1")
    raw = parse_todo_source(original)[0]["agent"][0]
    assert structured_todo_item(raw, role="agent", source_section="Agent Todo")["task_class"] == expected
    moved = archive_completed_todo_lines(original.splitlines(), max_active_done=0)
    source = "\n".join(moved["lines"])
    assert original.splitlines()[-1] in source  # Preserve unknown receipt metadata verbatim.
    archived = parse_todo_source(source)[1][0]
    assert archived["role"] == "agent"
    assert archived["task_class"] == expected
    active = [{"todo_id": "todo_next", "resume_when": "todo_done:todo_prior"}]
    record = capture_todo_archive_dependencies(active, source)[1]
    assert (record["role"], record["task_class"], record["archive_state"]) == ("agent", expected, "archive")
    # Older archives retained role alone. Capture must have the same class.
    old = "## Completed Work Archive\n" + row("todo_prior", text=text, metadata=f"role=agent {metadata}")
    assert capture_todo_archive_dependencies(active, old)[1]["task_class"] == expected


@pytest.mark.parametrize("metadata", ["", "task_class=user_gate", "role=user",
    "role=agent global_gate=true", "role=agent decision_outcome=approve"])
def test_missing_authority_stays_rejected_without_mutating_input(metadata):
    source = "## Completed Work Archive\n" + row("todo_prior", metadata=metadata)
    active = [{"todo_id": "todo_next", "resume_when": "todo_done:todo_prior"}]
    before = json.dumps(active)
    with pytest.raises(EffectRuntimeRejected, match="archive dependency capture"):
        capture_todo_archive_dependencies(active, source)
    assert json.dumps(active) == before


def test_capture_transport_separates_classification_from_private_prose(monkeypatch):
    from loopx.control_plane.coordination import authority_source_capture as runtime_shadow
    original = runtime_shadow.effect_runtime_result
    requests = []
    def record(method, params):
        requests.append(params)
        return original(method, params)
    monkeypatch.setattr(runtime_shadow, "effect_runtime_result", record)
    source = "## Completed Work Archive\n" + row("todo_prior", text="Private source description",
        metadata="role=agent action_kind=implement")
    captured = capture_todo_archive_dependencies([{"todo_id": "todo_next", "resume_when": "todo_done:todo_prior"}], source)
    assert captured[1]["task_class"] == "advancement_task"
    assert len(requests) == 1
    transported = requests[0]["archived"][0]
    assert transported["legacy_task_class"] == "advancement_task"
    assert "task_class" not in transported and "text" not in transported
    assert "Private source description" not in json.dumps(requests)


def source_graph() -> str:
    return ("## Agent Todo\n" + row("todo_next", marker=" ", metadata="task_class=advancement_task resume_when=todo_done:todo_prior")
        + "\n## Completed Work Archive\n"
        + row("todo_prior", metadata="role=agent resume_when=todo_done:todo_root")
        + row("todo_root", metadata="task_class=advancement_task")
        + row("todo_continuation", metadata="role=agent unblocks_todo_id=todo_next")
        + row("todo_deferred", marker="-", metadata="role=agent superseded_by=todo_root")
        + row("todo_unrelated", metadata=""))


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_complete_snapshot_and_real_provider_cli_keep_dependency_semantics(tmp_path: Path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = workspace(tmp_path)
    state.write_text("---\ngoal_id: goal-a\nhandoff_mode: legacy\n---\n" + source_graph())
    goal = json.loads(registry.read_text())["goals"][0]
    before = state.read_bytes()
    projection, snapshot = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=runtime,
        state_path=state, registry_path=registry)
    records = {r["todo_id"]: r for r in projection["todos"]}
    assert set(records) == {"todo_next", "todo_prior", "todo_root", "todo_continuation"}
    assert all(records[k]["archive_state"] == "archive" for k in records if k != "todo_next")
    assert records["todo_prior"]["task_class"] == "advancement_task"
    assert snapshot["state_bytes_sha256"].startswith("sha256:")
    assert state.read_bytes() == before
    initialize_canonical_authority(runtime, "goal-a", projection, state_path=state, provider=provider)
    state.unlink()  # Canonical reads cannot recover from the original Markdown.
    code, result = run_json_cli_result("todo", "list", "--goal-id", "goal-a", "--todo-id", "todo_next",
        registry_path=registry, runtime_root=runtime)
    assert code == 0, result
    assert result["todo"]["resume_ready"] is True
    code, listed = run_json_cli_result("todo", "list", "--goal-id", "goal-a", "--role", "agent",
        registry_path=registry, runtime_root=runtime)
    assert code == 0, listed
    assert [r["todo_id"] for r in listed["todos"]] == ["todo_next"]
    assert not state.exists()
