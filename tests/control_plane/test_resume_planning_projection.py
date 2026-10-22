"""Public wait-lane invariants, independent of the projection implementation."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.testing.canary_harness import write_fixture_registry, run_json_cli_result
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos

from loopx.control_plane.todos.resume_planning import project_todo_resume_planning
from loopx.control_plane.todos.quota_summary import select_quota_todo_summary


def _quota_goal(tmp_path, monkeypatch, todos, provider):
    """Todo cutover retains the separately declared Goal definition source."""
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    prose = "---\nstatus: active\n---\n# Goal\n## Objective\nBuild a checked change.\n\n"
    state.write_text(prose + "## Agent Todo\n" + todos, encoding="utf-8")
    write_fixture_registry(
        project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="goal-a", domain="resume-planning", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a", "agent-b"], quota_allowed_slots=None,
    )
    if provider is not None:
        if provider == "sqlite":
            isolate_sqlite_runtime(tmp_path, monkeypatch)
        goal = json.loads(registry.read_text())["goals"][0]
        fields = parse_active_state_todos(state.read_text(), goal=goal, item_limit=None)
        projection = build_todo_runtime_shadow_projection(
            goal_id="goal-a", todos=fields["agent_todos"]["items"], handoff_mode="soft_claim",
        )
        initialize_canonical_authority(runtime, "goal-a", projection, state_path=state, provider=provider)
        state.write_text(prose, encoding="utf-8")
    return runtime, registry, state


def waiting(todo_id: str, **fields: object) -> dict:
    return {
        "todo_id": todo_id, "index": 1, "text": "[P1] Wait for dependency",
        "role": "agent", "task_class": "advancement_task", "status": "open",
        "resume_when": "todo_done:todo_dependency", "resume_ready": False,
        "resume_condition": {"satisfied": False, "kind": "todo_done",
                             "target_status": "open", "target_task_class": "advancement_task"},
        **fields,
    }


def ids(items: list[dict]) -> list[str]:
    return [item["todo_id"] for item in items]


def test_claim_priority_and_exclusion_are_independent_of_display_priority() -> None:
    items = [waiting("todo_unclaimed", priority="P0"),
             waiting("todo_current", claimed_by="agent-a", priority="P4"),
             waiting("todo_other", claimed_by="agent-b"),
             waiting("todo_excluded", claimed_by="agent-a", excluded_agents=["agent-a"])]
    source = {"items": items}
    before = deepcopy(source)
    assert ids(project_todo_resume_planning(source, agent_id="agent-a")["blocked_successor_items"]) == [
        "todo_current", "todo_unclaimed",
    ]
    assert project_todo_resume_planning(source)["blocked_successor_items"] == []
    assert source == before


def test_monitor_generation_wait_is_not_the_monitor_completion_repair_lane() -> None:
    condition = {"satisfied": False, "target_status": "open",
                 "target_task_class": "continuous_monitor", "target_todo_id": "todo_monitor"}
    source = {"items": [
        waiting("todo_repair", resume_condition={**condition, "kind": "todo_done"}),
        waiting("todo_wait", resume_when="monitor_changed:todo_monitor",
                resume_condition={**condition, "kind": "monitor_changed"}),
    ]}
    result = project_todo_resume_planning(source, agent_id="agent-a", item_limit=1)
    lanes = result["resume_blocked_lanes"]
    assert lanes["resume_blocked_count"] == 2
    assert lanes["monitor_blocked_resume_count"] == 1
    assert ids(lanes["unclaimed_monitor_blocked_resume_candidates"]) == ["todo_repair"]
    assert result["blocked_successor_items"] == []


def test_legacy_monitor_lookup_is_diagnosed_once_before_agent_scope_selection() -> None:
    from loopx.control_plane.agents.agent_scope import _agent_scope_monitor_blocked_resume_candidates

    rows = [waiting(f"todo_{lane}", resume_when="todo_done:todo_monitor", **scope,
                    resume_condition={"satisfied": False, "target_status": "open", "target": "todo_monitor"})
            for lane, scope in [("current", {"claimed_by": "agent-a"}), ("unclaimed", {}),
                                ("other", {"claimed_by": "agent-b"}), ("excluded", {"excluded_agents": ["agent-a"]})]]
    result = project_todo_resume_planning({"items": rows, "monitor_open_items": [
        {"todo_id": "todo_monitor", "task_class": "continuous_monitor", "status": "open", "text": "Observe"},
    ]}, agent_id="agent-a")
    lanes = result["resume_blocked_lanes"]
    selected = _agent_scope_monitor_blocked_resume_candidates(lanes, agent_id="agent-a")
    assert ids(selected) == ["todo_current", "todo_unclaimed"]
    assert all(row["resume_condition"]["invalid_state"] == "monitor_completion_requires_replan" for row in selected)
    assert all(row["blocking_monitor_todo_id"] == "todo_monitor" for row in selected)
    assert lanes["other_agent_monitor_blocked_resume_count"] == 1
    assert lanes["executor_excluded_self_monitor_blocked_resume_count"] == 1


def test_invalid_condition_cannot_become_an_exact_pending_successor_wait() -> None:
    source = {"items": [waiting("todo_self", resume_when="todo_done:todo_self",
        resume_condition={"kind": "todo_done", "target_todo_id": "todo_self", "satisfied": False})]}
    result = project_todo_resume_planning(source, agent_id="agent-a")
    assert result["blocked_successor_items"] == []
    row = result["resume_blocked_lanes"]["resume_blocked_items"][0]
    assert row["resume_condition"]["invalid_state"] == "dependency_self_reference"


def test_invalid_deferred_condition_cannot_reuse_a_stale_ready_flag() -> None:
    row = waiting("todo_stale", status="deferred", resume_ready=True,
                  resume_when="todo_done:todo_monitor", resume_condition={
                      "kind": "todo_done", "satisfied": True, "target_status": "open",
                      "target_todo_id": "todo_monitor", "target_task_class": "continuous_monitor",
                  })
    result = project_todo_resume_planning({"deferred_items": [row], "deferred_resume_candidates": [row]}, agent_id="agent-a")
    assert result["deferred_lanes"]["unclaimed_deferred_resume_count"] == 0
    assert result["deferred_items"][0]["resume_ready"] is False
    assert row["resume_ready"] is True  # Diagnosis is a projection, not an edit.


@pytest.mark.parametrize("provider", [None, "file", "sqlite"])
@pytest.mark.parametrize("kind", ["todo_done", "monitor_changed"])
def test_public_quota_distinguishes_monitor_repair_from_generation_wait(
    tmp_path: Path, monkeypatch, provider: str | None, kind: str,
) -> None:
    runtime, registry, state = _quota_goal(
        tmp_path, monkeypatch,
        "- [ ] [P1] Continue after observation.\n"
        f"  <!-- loopx:todo todo_id=todo_waiting role=agent task_class=advancement_task status=open claimed_by=agent-a resume_when={kind}:todo_monitor resume_monitor_generation=3 -->\n"
        "- [ ] [P2] Observe dependency.\n"
        "  <!-- loopx:todo todo_id=todo_monitor role=agent task_class=continuous_monitor status=open claimed_by=agent-a material_change_generation=3 cadence=1d next_due_at=2099-01-01T00:00:00Z -->\n",
        provider,
    )
    before = state.read_bytes()
    assert (b"## Agent Todo" in before) == (provider is None)
    code, packet = run_json_cli_result("quota", "should-run", "--goal-id", "goal-a", "--agent-id", "agent-a",
        "--include-detail", "agent-todos",  registry_path=registry, runtime_root=runtime)
    assert code == 0, packet
    summary = packet["agent_todo_summary"]
    condition = summary["resume_blocked_items"][0]["resume_condition"]
    assert condition["availability_reason"] == ("resume_condition_invalid" if kind == "todo_done" else "resume_condition_pending")
    if kind == "todo_done":
        assert summary["current_agent_monitor_blocked_resume_count"] == 1
        assert packet["work_lane_contract"]["obligation"] == "repair_resume_gate_or_close_standing_monitor"
        assert packet["work_lane_contract"]["selected_todo_id"] == "todo_waiting"
    else:
        assert not summary.get("monitor_blocked_resume_count")
        assert condition["baseline_generation"] == 3
        assert condition["material_change_generation"] == 3
    assert state.read_bytes() == before


def test_ready_deferred_lanes_keep_full_counts_before_truncation() -> None:
    items = [waiting(f"todo_ready_{i}", status="deferred", resume_ready=True,
                     claimed_by="agent-a") for i in range(4)]
    items.append(waiting("todo_excluded", status="deferred", resume_ready=True,
                         excluded_agents=["agent-a"]))
    source = {"deferred_items": items, "deferred_resume_candidates": items}
    lanes = project_todo_resume_planning(source, agent_id="agent-a", item_limit=1)["deferred_lanes"]
    assert lanes["deferred_count"] == 5
    assert lanes["current_agent_deferred_resume_count"] == 4
    assert len(lanes["current_agent_deferred_resume_candidates"]) == 1
    assert lanes["executor_excluded_self_deferred_resume_count"] == 1
    assert lanes["unclaimed_deferred_resume_count"] == 0


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("independent", [False, True])
def test_public_quota_replans_completion_cycles_without_reassigning_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, independent: bool,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    state.write_text(
        "---\nstatus: active\n---\n# Goal\n## Objective\nDeliver checked work.\n\n## Agent Todo\n"
        "- [ ] [P1] Complete the first task.\n"
        "  <!-- loopx:todo todo_id=todo_alpha role=agent task_class=advancement_task status=open claimed_by=agent-a resume_when=todo_done:todo_bravo -->\n"
        "- [ ] [P1] Complete the second task.\n"
        "  <!-- loopx:todo todo_id=todo_bravo role=agent task_class=advancement_task status=open claimed_by=agent-b resume_when=todo_done:todo_alpha -->\n"
        + ("- [ ] [P2] Deliver independent work.\n"
           "  <!-- loopx:todo todo_id=todo_independent role=agent task_class=advancement_task status=open claimed_by=agent-a -->\n" if independent else ""),
        encoding="utf-8",
    )
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="goal-a", domain="resume-planning", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a", "agent-b"], quota_allowed_slots=None)
    if provider != "legacy":
        goal = json.loads(registry.read_text())["goals"][0]
        fields = parse_active_state_todos(state.read_text(), goal=goal, item_limit=None)
        initialize_canonical_authority(runtime, "goal-a", build_todo_runtime_shadow_projection(
            goal_id="goal-a", todos=fields["agent_todos"]["items"], handoff_mode="soft_claim"),
            state_path=state, provider=provider)
    before = state.read_bytes()
    code, packet = run_json_cli_result("quota", "should-run", "--goal-id", "goal-a", "--agent-id", "agent-a",
        "--include-detail", "agent-todos",  registry_path=registry, runtime_root=runtime)
    assert code == 0, packet
    summary = packet["agent_todo_summary"]
    blocked = next(row for row in summary["resume_blocked_items"] if row["todo_id"] == "todo_alpha")
    assert blocked["resume_condition"]["invalid_state"] == "dependency_cycle"
    if independent:
        assert packet["selected_todo"]["todo_id"] == "todo_independent"
    else:
        frontier = packet["agent_scope_frontier"]
        assert frontier["action"] == "successor_replan_required"
        assert frontier["quiet_noop_allowed"] is False
        assert ids(frontier["dependency_cycle_candidates"]) == ["todo_alpha"]
        assert "owner" in frontier["recommended_action"]
    assert state.read_bytes() == before


def test_ignored_rows_do_not_turn_an_explicit_lane_into_fallback_input() -> None:
    source = {"items": [waiting("todo_fallback", status="deferred")],
              "deferred_items": [None], "resume_blocked_items": [None]}
    result = project_todo_resume_planning(source, agent_id="agent-a")
    assert result["deferred_items"] == []
    assert result["resume_blocked_lanes"] == {}


def test_quota_composes_capacity_and_visibility_in_one_request_per_source(monkeypatch) -> None:
    from loopx.control_plane.todos import quota_selection

    original = quota_selection.effect_runtime_result
    calls = []

    def record(method, params):
        calls.append(method)
        return original(method, params)

    monkeypatch.setattr(quota_selection, "effect_runtime_result", record)
    summary = select_quota_todo_summary(
        {"schema_version": "todo_summary_v0", "items": [waiting("todo_capacity", status="deferred",
          resume_when="capacity_available:compiler")], "total_count": 1, "deferred_count": 1},
        None, agent_identity={"agent_id": "agent-a"}, available_capabilities=["compiler"],
    )
    assert summary["unclaimed_deferred_resume_count"] == 1
    assert calls == ["todo.quota_planning.project"]


def test_unavailable_typed_owner_does_not_fall_back_to_python_selection(monkeypatch) -> None:
    from loopx.control_plane.todos import resume_planning

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("isolated runtime unavailable")

    monkeypatch.setattr(resume_planning, "effect_runtime_result", unavailable)
    source = {"items": [waiting("todo_wait")]}
    with pytest.raises(RuntimeError, match="isolated runtime unavailable"):
        project_todo_resume_planning(source)


@pytest.mark.parametrize("provider", [None, "file", "sqlite"])
def test_public_quota_capacity_wait_uses_provider_after_cutover_without_display_write(
    tmp_path: Path, monkeypatch, provider: str | None,
) -> None:
    runtime, registry, state = _quota_goal(
        tmp_path, monkeypatch,
        "- [ ] [P1] Continue when compiler capacity returns.\n"
        "  <!-- loopx:todo todo_id=todo_capacity role=agent task_class=advancement_task "
        "status=deferred claimed_by=agent-a resume_when=capacity_available:compiler -->\n",
        provider,
    )
    before = state.read_bytes()
    assert (b"## Agent Todo" in before) == (provider is None)
    for capabilities, count in [((), 0), (("--available-capability", "compiler"), 1)]:
        code, packet = run_json_cli_result(
            "quota", "should-run", "--goal-id", "goal-a", "--agent-id", "agent-a",
             *capabilities, registry_path=registry, runtime_root=runtime,
        )
        assert code == 0, packet
        assert packet["agent_todo_summary"]["current_agent_deferred_resume_count"] == count
        if count:
            assert packet["effective_action"] == "successor_replan_required"
        assert state.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_todos_do_not_replace_a_missing_declared_goal_source(tmp_path, monkeypatch, provider):
    runtime, registry, state = _quota_goal(
        tmp_path, monkeypatch,
        "- [ ] [P1] Advance the checked work.\n"
        "  <!-- loopx:todo todo_id=todo_work role=agent task_class=advancement_task status=open claimed_by=agent-a -->\n",
        provider,
    )
    prose = state.read_bytes()
    assert b"## Agent Todo" not in prose
    state.unlink()

    code, packet = run_json_cli_result(
        "quota", "should-run", "--goal-id", "goal-a", "--agent-id", "agent-a",
        registry_path=registry, runtime_root=runtime,
    )

    assert code == 1
    assert packet["status"] == "state_file_missing"
    assert packet["should_run"] is False
    assert packet["normal_delivery_allowed"] is False
    assert packet["agent_todo_summary"]["open_count"] == 1
    assert not state.exists()

    state.write_bytes(prose)
    code, packet = run_json_cli_result(
        "quota", "should-run", "--goal-id", "goal-a", "--agent-id", "agent-a",
        registry_path=registry, runtime_root=runtime,
    )
    assert code == 0, packet
    assert packet["status_health_ok"] is True
    assert packet["agent_todo_summary"]["open_count"] == 1
    assert state.read_bytes() == prose
