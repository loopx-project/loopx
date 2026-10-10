"""List and quota address the same Agent lane without granting execution."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from loopx.control_plane.testing.canary_harness import write_fixture_registry, run_json_cli_result
from loopx.control_plane.todos.goal_todo_projection import filtered_todo_summary
from loopx.control_plane.todos.quota_summary import summarize_user_todos_for_quota
from loopx.control_plane.todos.todo_summary import compact_todo_group


@pytest.mark.parametrize("task_class,scope,visible", [
    ("user_gate", {"claimed_by": "agent-b"}, False),
    ("user_action", {"claimed_by": "agent-b"}, False),
    ("user_gate", {"claimed_by": "agent-a"}, True),
    ("user_action", {"claimed_by": "agent-a"}, True),
    ("user_gate", {"claimed_by": "agent-b", "blocks_agent": "agent-a"}, True),
    ("user_gate", {"claimed_by": "agent-a", "blocks_agent": "agent-b"}, False),
    ("user_gate", {"claimed_by": "agent-b", "global_gate": True, "excluded_agents": ["agent-a"]}, True),
    ("user_action", {"claimed_by": "agent-b", "bound_agent": "agent-a"}, True),
    ("user_action", {"claimed_by": "agent-a", "bound_agent": "agent-b"}, False),
    ("user_action", {}, True),
    ("advancement_task", {"bound_agent": "agent-b"}, False),
    ("user_gate", {}, True),
])
def test_list_and_quota_share_addressed_scope(task_class, scope, visible):
    source = compact_todo_group([{"todo_id": "todo_scoped", "text": "Review the result",
        "role": "user", "status": "open", "task_class": task_class, **scope}],
        role="user", source_section="User Todo", item_limit=None)
    selected = filtered_todo_summary(source, role="user", agent_id="agent-a")
    quota = summarize_user_todos_for_quota(source, agent_identity={"agent_id": "agent-a"},
        filter_user_gate_blocks_agent=True)
    assert bool(selected["items"]) is visible
    assert quota["open_count"] == int(visible)
    assert selected["open_count"] == int(visible)
    # Full Goal read remains diagnostic, including work addressed to other Agents.
    assert filtered_todo_summary(source, role="user")["total_count"] == 1


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_real_cli_scope_survives_missing_display_and_limits(tmp_path: Path, provider: str):
    state, registry, runtime = tmp_path / "STATE.md", tmp_path / "registry.json", tmp_path / "runtime"
    user_rows = [
        ("todo_peer_gate", "user_gate", "claimed_by=agent-b"),
        ("todo_peer_action", "user_action", "claimed_by=agent-b"),
        ("todo_explicit_gate", "user_gate", "claimed_by=agent-b blocks_agent=agent-a"),
        ("todo_explicit_action", "user_action", "claimed_by=agent-b bound_agent=agent-a"),
        ("todo_global", "user_gate", "claimed_by=agent-b global_gate=true"),
    ]
    text = "---\nstatus: active\n---\n# Synthetic Goal\n\n## User Todo\n"
    for todo_id, task_class, scope in user_rows:
        text += f"- [ ] [P1] Review {todo_id}\n  <!-- loopx:todo todo_id={todo_id} status=open task_class={task_class} {scope} -->\n"
    text += "\n## Agent Todo\n- [ ] [P1] Continue after the archived dependency\n"
    text += "  <!-- loopx:todo todo_id=todo_successor status=open task_class=advancement_task claimed_by=agent-a resume_when=todo_done:todo_dependency -->\n"
    text += "\n## Completed Work Archive\n- [x] Prior result\n"
    text += "  <!-- loopx:todo todo_id=todo_dependency status=done task_class=advancement_task -->\n"
    state.write_text(text)
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="scope-goal", domain="consumer-scope", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a", "agent-b"], quota_allowed_slots=None)
    if provider != "legacy":
        from loopx.control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot
        goal = json.loads(registry.read_text())["goals"][0]
        projection, _ = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=runtime,
            state_path=state, registry_path=registry)
        initialize_canonical_authority(runtime, "scope-goal", projection, state_path=state, provider=provider)
        state.unlink()
    before = state.read_bytes() if state.exists() else None
    def read(*args):
        code, result = run_json_cli_result("todo", "list", "--goal-id", "scope-goal", *args,
            registry_path=registry, runtime_root=runtime)
        assert code == 0, result
        return result
    unfiltered = read("--role", "user")
    assert unfiltered["todo_count"] == 5
    selected = read("--role", "user", "--agent-id", "agent-a")
    assert {row["todo_id"] for row in selected["todos"]} == {"todo_explicit_gate", "todo_explicit_action", "todo_global"}
    limited = read("--role", "user", "--agent-id", "agent-a", "--limit", "1", "--thin")
    assert limited["todo_list_projection"]["matched_todo_count"] == 3
    assert limited["returned_todo_count"] == 1
    for todo_id in ["todo_peer_gate", "todo_peer_action"]:
        assert read("--todo-id", todo_id, "--agent-id", "agent-a")["not_found"] is True
    successor = read("--todo-id", "todo_successor", "--agent-id", "agent-a")
    assert successor["todo"]["resume_ready"] is True
    assert (state.read_bytes() if state.exists() else None) == before


def test_filter_composes_with_existing_lane_call_and_rejects_downgraded_response(monkeypatch):
    from loopx.control_plane import effect_runtime
    source = compact_todo_group([{"todo_id": "todo_one", "text": "Review result", "role": "user",
        "status": "open", "task_class": "user_action", "bound_agent": "agent-a"}],
        role="user", source_section="User Todo", item_limit=None)
    original = effect_runtime.effect_runtime_result
    requests = []
    def track(method, request, **kwargs):
        if method == "todo.summary.project":
            requests.append(request)
        return original(method, request, **kwargs)
    monkeypatch.setattr(effect_runtime, "effect_runtime_result", track)
    assert filtered_todo_summary(source, role="user", agent_id="agent-a")["total_count"] == 1
    assert len(requests) == 1
    assert requests[0]["schema_version"] == "todo_summary_projection_request_v3"
    # One whole-source batch stays columnar, so adding fields cannot silently
    # push a long-history request past the runtime request budget.
    assert requests[0]["columns"][:3] == ["status", "done", "task_class"]
    assert all(len(cells) == len(requests[0]["columns"]) for cells in requests[0]["rows"])
    def downgrade(method, request, **kwargs):
        result = original(method, request, **kwargs)
        if method == "todo.summary.project":
            result.pop("source_indices", None)
        return result
    monkeypatch.setattr(effect_runtime, "effect_runtime_result", downgrade)
    with pytest.raises(ValueError, match="selection ordinals"):
        filtered_todo_summary(source, role="user", agent_id="agent-a")


@pytest.mark.parametrize("corruption", ["duplicate", "boolean", "outside_selection"])
def test_typed_lane_response_cannot_alias_or_escape_selected_source(monkeypatch, corruption):
    from loopx.control_plane import effect_runtime
    source = compact_todo_group([
        {"todo_id": "todo_owned", "text": "Review result", "role": "user", "status": "open",
            "task_class": "user_action", "bound_agent": "agent-a"},
        {"todo_id": "todo_other", "text": "Review peer result", "role": "user", "status": "open",
            "task_class": "user_action", "bound_agent": "agent-b"}],
        role="user", source_section="User Todo", item_limit=None)
    outside = next(index for index, item in enumerate(source["items"]) if item["todo_id"] == "todo_other")
    original = effect_runtime.effect_runtime_result
    def corrupt(method, request, **kwargs):
        result = original(method, request, **kwargs)
        if method == "todo.summary.project":
            result["lanes"]["first_open_items"]["indices"] = {"duplicate": [0, 0], "boolean": [False], "outside_selection": [outside]}[corruption]
        return result
    monkeypatch.setattr(effect_runtime, "effect_runtime_result", corrupt)
    with pytest.raises(ValueError, match="source ordinal|escaped the selected source"):
        filtered_todo_summary(source, role="user", agent_id="agent-a")
