"""Real recommendation writeback, source readback and concurrent CLI transactions."""
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys

import pytest

import loopx.state_refresh as refresh
from loopx.control_plane.work_items.recommendation_source_io import RecommendationWritebackRejected
from loopx.status import collect_status
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.todos.next_action_runtime import settle_completed_todo_next_action
from tests.control_plane import test_todo_projection_concurrency as projection_fixtures

canonical_projection = projection_fixtures.canonical_projection

STATE = """# Active Goal State

## Agent Todo

- [ ] [P1] Inspect the parser.
  <!-- loopx:todo todo_id=todo_parser status=open task_class=advancement_task claimed_by=agent-a -->
- [ ] [P1] Evaluate the current artifact.
  <!-- loopx:todo todo_id=todo_evaluate status=open task_class=advancement_task claimed_by=agent-b -->

## Next Action

- Preserve the current shared route.
"""
VISION = {"state": "vision_patch_proposed", "vision_patch": {
    "vision_summary": "Inspect current evidence before the next experiment.",
    "acceptance_summary": "Validated evidence and scoped next work remain required.",
}}


def fixture(tmp_path, agents=("agent-a",)):
    state = tmp_path / "state.md"
    state.write_text(STATE)
    registry = tmp_path / "registry.json"
    goal = {"id": "next-action-goal", "status": "active", "repo": str(tmp_path),
            "state_file": state.name, "coordination": {"registered_agents": list(agents)}}
    runtime = tmp_path / "runtime"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [goal]}))
    return registry, state, runtime, goal


def write(registry, runtime, **kwargs):
    options = dict(
        registry_path=registry, runtime_root_override=str(runtime), goal_id="next-action-goal",
        project=None, state_file=None, classification="state_refreshed", recommended_action=None,
        agent_id="agent-a", next_action="Evaluate the new artifact; preserve the incumbent.",
        dry_run=False, sync_global=False,
    )
    options.update(kwargs)
    return refresh.refresh_state_run(**options)



def routes(registry, runtime, root):
    status = collect_status(registry_path=registry, runtime_root_override=str(runtime),
        scan_roots=[root], limit=5, include_task_graph=True)
    item = next(item for item in status["attention_queue"]["items"] if item["goal_id"] == "next-action-goal")
    return item, {r["agent_id"]: r for r in item.get("agent_next_actions", [])}


@pytest.mark.parametrize("agents", [("agent-a",), ("agent-a", "agent-b")])
def test_personal_step_reuses_resolution_and_preserves_state_and_ownership(tmp_path, agents):
    registry, state, runtime, _ = fixture(tmp_path, agents)
    before, selected = routes(registry, runtime, tmp_path)
    result = write(registry, runtime, next_action_basis=selected["agent-a"]["next_action_basis"])
    receipt = result["recommended_action_resolution"]
    assert receipt["recommended_action_source"] == "agent_lane_step"
    assert receipt["todo_id"] == "todo_parser"
    assert result["progress_scope"] == "agent_lane" and result["agent_id"] == "agent-a"
    assert "active_state_next_action_update" not in result
    assert state.read_text() == STATE
    run = json.loads((runtime / "goals/next-action-goal/runs/index.jsonl").read_text())
    assert run["recommended_action_resolution"] == receipt
    _, after = routes(registry, runtime, tmp_path)
    assert after["agent-a"]["text"] == selected["agent-a"]["text"]
    assert after["agent-a"]["next_step"] == result["recommended_action"]
    assert after["agent-a"]["next_action_basis"] != selected["agent-a"]["next_action_basis"]


def test_multi_agent_steps_are_independent_and_report_scope_does_not_grant_authority(tmp_path):
    registry, state, runtime, _ = fixture(tmp_path, ("agent-a", "agent-b"))
    _, before = routes(registry, runtime, tmp_path)
    write(registry, runtime, next_action_basis=before["agent-a"]["next_action_basis"])
    _, after_a = routes(registry, runtime, tmp_path)
    assert after_a["agent-b"]["next_action_basis"] == before["agent-b"]["next_action_basis"]
    write(registry, runtime, agent_id="agent-b", next_action="Evaluate the incumbent.",
        progress_scope="goal", next_action_basis=before["agent-b"]["next_action_basis"])
    _, after_b = routes(registry, runtime, tmp_path)
    assert after_b["agent-a"]["next_step"] == after_a["agent-a"]["next_step"]
    assert after_b["agent-b"]["next_step"] == "Evaluate the incumbent."
    assert state.read_text() == STATE


@pytest.mark.parametrize("agents,actor", [([], "agent-a"), (["agent-b"], "agent-a"), (["agent-a"], None)])
def test_unknown_or_unattributed_steps_do_not_append(tmp_path, agents, actor):
    registry, state, runtime, _ = fixture(tmp_path, agents)
    with pytest.raises(ValueError):
        write(registry, runtime, agent_id=actor)
    assert state.read_text() == STATE
    assert not (runtime / "goals/next-action-goal/runs/index.jsonl").exists()


def test_dry_run_checks_binding_without_writing_or_appending(tmp_path):
    registry, state, runtime, _ = fixture(tmp_path)
    result = write(registry, runtime, dry_run=True)
    assert result["recommended_action_resolution"]["todo_id"] == "todo_parser"
    assert result["appended"] is False
    assert state.read_text() == STATE
    assert not (runtime / "goals/next-action-goal/runs/index.jsonl").exists()


def test_explicit_matching_step_keeps_task_binding_and_is_not_replan_evidence(tmp_path):
    registry, state, runtime, _ = fixture(tmp_path)
    step = "Compare the current artifact."
    result = write(registry, runtime, next_action=step, recommended_action=step)
    assert result["recommended_action_resolution"]["todo_id"] == "todo_parser"
    assert result["vision_checkpoint"]["required"] is False
    assert not result.get("autonomous_replan_ack")
    with pytest.raises(ValueError, match="must agree"):
        write(registry, runtime, next_action=step, recommended_action="A different direction.")


@pytest.mark.parametrize("length", [1200, 1201])
def test_cli_next_step_budget_checks_trimmed_text_before_append(tmp_path, length):
    registry, state, runtime, goal = fixture(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
         "--runtime-root", str(runtime), "refresh-state", "--goal-id", goal["id"],
         "--agent-id", "agent-a", "--next-action", "  " + "x" * length + "  ",
         "--no-global-sync"],
        text=True, capture_output=True, timeout=30,
    )
    payload = json.loads(result.stdout)
    index = runtime / "goals/next-action-goal/runs/index.jsonl"
    if length == 1200:
        assert result.returncode == 0, payload
        assert payload["recommended_action"] == "x" * length
        assert len(index.read_text().splitlines()) == 1
    else:
        assert result.returncode == 1, payload
        assert payload["error"] == "next_action exceeds 1200 characters"
        assert not index.exists()
    assert state.read_text() == STATE


def test_same_actor_concurrent_cli_writers_cannot_commit_one_old_basis_twice(tmp_path):
    registry, state, runtime, goal = fixture(tmp_path)
    _, selected = routes(registry, runtime, tmp_path)
    basis = selected["agent-a"]["next_action_basis"]
    def run(action):
        command = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
                   "--runtime-root", str(runtime), "refresh-state", "--goal-id", goal["id"],
                   "--agent-id", "agent-a", "--next-action", action, "--next-action-basis", basis, "--no-global-sync"]
        result = subprocess.run(command, text=True, capture_output=True, timeout=30)
        assert result.stdout, result.stderr
        return result.returncode, json.loads(result.stdout)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(run, ["Inspect new evidence.", "Evaluate the artifact."]))
    assert sorted(code for code, _ in results) == [0, 1], results
    assert next(payload for code, payload in results if code)["error_code"] == "next_action_basis_conflict"
    assert len((runtime / "goals/next-action-goal/runs/index.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("change", ["task", "roster", "intent"])
def test_final_commit_rechecks_relevant_source_facts(tmp_path, monkeypatch, change):
    registry, state, runtime, _ = fixture(tmp_path)
    original = refresh.qualify_refresh_replan_writeback
    def concurrent_change(**kwargs):
        result = original(**kwargs)
        if change == "roster":
            data = json.loads(registry.read_text())
            data["goals"][0]["coordination"]["registered_agents"].append("offline-peer")
            registry.write_text(json.dumps(data))
        elif change == "task":
            state.write_text(STATE.replace("Inspect the parser.", "Inspect the changed parser contract."))
        else:
            state.write_text("# A changed acceptance basis\n" + STATE)
        return result
    monkeypatch.setattr(refresh, "qualify_refresh_replan_writeback", concurrent_change)
    with pytest.raises(RecommendationWritebackRejected) as caught:
        write(registry, runtime)
    assert caught.value.code == "next_action_basis_conflict"
    assert not (runtime / "goals/next-action-goal/runs/index.jsonl").exists()


def test_step_does_not_destroy_binding_or_block_completion_projection(tmp_path):
    registry, state, runtime, _ = fixture(tmp_path, ("agent-a", "agent-b"))
    bound = STATE.replace("- Preserve the current shared route.",
        "- [P1] Inspect the parser.\n<!-- loopx:next-action schema=loopx_next_action_binding_v0 todo_id=todo_parser -->")
    state.write_text(bound)
    write(registry, runtime)
    assert state.read_text() == bound
    completed = bound.replace("todo_parser status=open", "todo_parser status=done").splitlines()
    assert settle_completed_todo_next_action(completed, completed_todo_id="todo_parser")
    assert "todo_id=todo_evaluate" in "\n".join(completed)
    state.write_text("\n".join(completed))
    _, after = routes(registry, runtime, tmp_path)
    assert "agent-a" not in after
    assert "next_step" not in after["agent-b"]


def test_task_change_invalidates_step_and_no_eligible_task_cannot_accept_one(tmp_path):
    registry, state, runtime, _ = fixture(tmp_path)
    write(registry, runtime)
    state.write_text(STATE.replace("Inspect the parser.", "Inspect another parser interface."))
    _, changed = routes(registry, runtime, tmp_path)
    assert "next_step" not in changed["agent-a"]
    state.write_text(STATE.replace("todo_parser status=open", "todo_parser status=blocked"))
    with pytest.raises(RecommendationWritebackRejected, match="No selected eligible"):
        write(registry, runtime)


def test_missing_goal_fails_at_the_next_action_boundary(tmp_path):
    registry, state, runtime, _ = fixture(tmp_path)
    registry.write_text(json.dumps({"goals": []}))
    with pytest.raises(ValueError, match="requires a registry Goal"):
        write(registry, runtime, project=tmp_path, state_file=state)


def shared_fixture(tmp_path):
    registry, state, runtime, goal = fixture(tmp_path, ("agent-a", "agent-b"))
    mirror = tmp_path / "shared-registry.json"
    stale = {**goal, "source_registry": str(registry), "state_file": "stale-state.md",
             "coordination": {"registered_agents": ["agent-a"]}}
    (tmp_path / "stale-state.md").write_text(STATE.replace("current shared route", "stale mirror route"))
    mirror.write_text(json.dumps({"registry_role": "global-local", "common_runtime_root": str(runtime), "goals": [stale]}))
    return registry, mirror, state, runtime, goal


def test_shared_reads_and_writes_use_source_roster_tasks_and_route(tmp_path):
    registry, mirror, state, runtime, _ = shared_fixture(tmp_path)
    _, selected = routes(mirror, runtime, tmp_path)
    assert set(selected) == {"agent-a", "agent-b"}
    result = write(mirror, runtime, next_action_basis=selected["agent-a"]["next_action_basis"])
    assert result["recommended_action_resolution"]["todo_id"] == "todo_parser"
    assert state.read_text() == STATE
    assert "stale mirror route" in (tmp_path / "stale-state.md").read_text()


def test_missing_shared_source_never_mints_a_basis_or_allows_write(tmp_path):
    registry, mirror, state, runtime, _ = shared_fixture(tmp_path)
    registry.unlink()
    with pytest.raises(ValueError, match="source_registry is missing"):
        write(mirror, runtime)
    item, _ = routes(mirror, runtime, tmp_path)
    assert "recommendation_context" not in item


def test_step_preserves_real_canonical_authority(canonical_projection):
    args, state, _, _ = canonical_projection
    registry = args["registry_path"]
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"] = {"registered_agents": ["agent-a"]}
    registry.write_text(json.dumps(data))
    before = read_canonical_todos_if_promoted(runtime_root=args["runtime_root"], goal_id=args["goal_id"])
    state_before = state.read_text()
    result = refresh.refresh_state_run(
        registry_path=registry, runtime_root_override=str(args["runtime_root"]), goal_id=args["goal_id"],
        project=None, state_file=None, classification="state_refreshed", recommended_action=None,
        agent_id="agent-a", next_action="Validate the canonical work.", dry_run=False, sync_global=False)
    assert result["recommended_action_resolution"]["recommended_action_source"] == "agent_lane_step"
    after = read_canonical_todos_if_promoted(runtime_root=args["runtime_root"], goal_id=args["goal_id"])
    assert (after["provider_revision"], after["cursor"], after["todos"]) == (
        before["provider_revision"], before["cursor"], before["todos"])
    from loopx.control_plane.todos.projection_document import TodoProjectionDocument
    assert TodoProjectionDocument.parse(state.read_text()).narrative.strip() == TodoProjectionDocument.parse(state_before).narrative.strip()
    from loopx.status import active_state_todo_fields as read_fields
    fields = read_fields(data["goals"][0], runtime_root=args["runtime_root"], registry_path=registry, include_agent_next_actions=True)
    assert fields["agent_next_actions"][0]["next_step"] == "Validate the canonical work."


def test_canonical_step_uses_tasks_without_a_markdown_display(canonical_projection):
    args, state, _, _ = canonical_projection
    registry = args["registry_path"]
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"] = {"registered_agents": ["agent-a"]}
    registry.write_text(json.dumps(data))
    state.unlink()
    result = refresh.refresh_state_run(
        registry_path=registry, runtime_root_override=str(args["runtime_root"]), goal_id=args["goal_id"],
        project=None, state_file=None, classification="state_refreshed", recommended_action=None,
        agent_id="agent-a", next_action="Read canonical work.", dry_run=False, sync_global=False)
    assert result["recommended_action_resolution"]["todo_id"] == "todo_work"
    from loopx.status import active_state_todo_fields as read_fields
    fields = read_fields(data["goals"][0], runtime_root=args["runtime_root"],
        registry_path=registry, include_agent_next_actions=True)
    assert fields["agent_next_actions"][0]["next_step"] == "Read canonical work."


def test_unavailable_canonical_reader_never_uses_stale_markdown(tmp_path, monkeypatch):
    from loopx.control_plane.work_items import refresh_recommendation
    registry, state, runtime, _ = fixture(tmp_path)
    def unavailable(**kwargs):
        raise RuntimeError("canonical source is unavailable")
    monkeypatch.setattr(refresh_recommendation, "read_canonical_todos_if_promoted", unavailable)
    with pytest.raises(RuntimeError, match="canonical source is unavailable"):
        write(registry, runtime)
    assert state.read_text() == STATE
    assert not (runtime / "goals/next-action-goal/runs/index.jsonl").exists()
