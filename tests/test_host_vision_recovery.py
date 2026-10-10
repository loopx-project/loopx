"""Real CLI/MCP vision recovery: acceptance, accounting and terminal are distinct."""
import importlib.util
import json
from pathlib import Path

import pytest

from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("host_vision_fixture", REPO / "scripts/qualify-native-goal-release.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def vision(state="no_followup"):
    path_outcome = {
        "no_followup": "no_change",
        "vision_closed": "replan",
    }.get(state, "continue")
    path_change = {
        "no_followup": {"stopped": ["No further fixture delivery is claimed."]},
        "vision_closed": {"changed": ["Close the validated fixture stage."]},
    }.get(
        state,
        {"retained": ["Keep the remaining explicit Todo as the delivery frontier."]},
    )
    return {
        "schema_version": "goal_vision_replan_contract_v0", "state": state,
        "vision_patch": {
            "vision_summary": "Deliver the finite local ledger specification.",
            "acceptance_summary": "Replay, reversal and CLI atomic output verified.",
            "last_patch_summary": "Local acceptance tests passed; no external delivery is requested.",
        },
        "path_delta": {
            "schema_version": "goal_path_delta_v0",
            "outcome": path_outcome,
            "prior_assumption": "The current milestone still required validation.",
            "observed_reality": "The fixture acceptance passed with a verified Todo transition.",
            "evidence_refs": ["fixture:synthetic-lifecycle-acceptance"],
            **path_change,
        },
    }


def control_at(root):
    project, runtime, launcher = fixture.setup(root)
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(server_name="vision-test", runtime_profile="claude_code", legacy_host_surface="claude_code"),
        lambda: {"goal_id": fixture.GOAL, "agent_id": fixture.AGENT},
    )
    control.command_prefix = lambda: [str(launcher)]
    return control, project, runtime


def spends(runtime):
    rows = [json.loads(row) for row in (runtime / "goals" / fixture.GOAL / "runs/index.jsonl").read_text().splitlines()]
    return [row for row in rows if row.get("classification") == "quota_slot_spent"]


@pytest.mark.parametrize("state,terminal", [("no_followup", True), ("vision_patch_proposed", False), ("vision_closed", False)])
def test_completed_todos_require_vision_decision_not_automatic_goal_close(tmp_path, monkeypatch, state, terminal):
    control, project, runtime = control_at(tmp_path)
    monkeypatch.chdir(project)
    first = json.loads(control.complete_task("todo_reducer", fixture.AGENT, "Synthetic lifecycle acceptance", successor_todo_ids=["todo_cli"]))
    assert first["ok"] is True, first
    last = json.loads(control.complete_task("todo_cli", fixture.AGENT, "Synthetic lifecycle acceptance", no_follow_up=True))
    assert last["ok"] is True, last
    before = json.loads(control.should_run())
    assert before["should_run"] is True
    assert before["interaction_contract"]["mode"] != "terminal_no_followup"
    assert len(spends(runtime)) == 2
    context = json.loads(control.review_task_vision("todo_cli", fixture.AGENT))
    assert context["ok"] is True, context
    assert context["basis"]["todo"]["todo_id"] == "todo_cli"
    receipt = context["read_context_id"]
    repaired = json.loads(control.review_task_vision("todo_cli", fixture.AGENT, vision(state), read_context_id=receipt))
    assert repaired["ok"] is True, repaired
    assert repaired["vision_checkpoint"]["satisfied"] is True
    assert repaired["refresh_recovery"]["decision"] == "supplement_checkpoint"
    replay = json.loads(control.review_task_vision("todo_cli", fixture.AGENT, vision(state), read_context_id=receipt))
    assert replay["ok"] is True, replay
    assert replay["appended"] is False
    assert len(spends(runtime)) == 2
    after = json.loads(control.should_run())
    assert (after["interaction_contract"]["mode"] == "terminal_no_followup") is terminal, after


@pytest.mark.parametrize("policy", ["as_needed", "repeat_until_closed", "repeat_until_closed_long"])
def test_first_delivery_can_author_vision_and_later_delivery_can_preserve_it(tmp_path, monkeypatch, policy):
    control, project, runtime = control_at(tmp_path)
    monkeypatch.chdir(project)
    authored = vision("vision_patch_proposed")
    authored["vision_patch"]["advancement_policy"] = policy.removesuffix("_long")
    if policy.endswith("_long"):
        authored["vision_patch"]["acceptance_summary"] = (
            "Reducer replay and reversal invariants have been verified against an independent oracle. "
            "Remaining CLI acceptance includes atomic output, malformed input, deterministic ordering, "
            "and documented local invocation. "
        )
        authored["vision_patch"]["vision_summary"] = (
            "Deliver a finite standard-library ledger with deterministic replay and reversal behavior. "
        ) * 4
        authored["vision_patch"]["role_scope"] = "Local implementation, tests and README; no network delivery. " * 3
    result = json.loads(control.complete_task("todo_reducer", fixture.AGENT, "Synthetic acceptance",
        successor_todo_ids=["todo_cli"], agent_vision=authored))
    assert result["ok"] is True, json.dumps(result)
    assert result["settlement"]["durable_writeback"]["vision_checkpoint"]["satisfied"] is True
    last = json.loads(control.complete_task("todo_cli", fixture.AGENT, "Synthetic acceptance",
        no_follow_up=True, vision_unchanged_reason="The same acceptance remains in scope."))
    assert last["ok"] is True, last
    assert last["settlement"]["durable_writeback"]["vision_checkpoint"]["decision"] == "unchanged_with_reason"
    assert len(spends(runtime)) == 2
    assert json.loads(control.should_run())["should_run"] is True


def test_recovery_cannot_manufacture_completion_or_override_bound_actor(tmp_path, monkeypatch):
    control, project, runtime = control_at(tmp_path)
    monkeypatch.chdir(project)
    assert json.loads(control.review_task_vision("todo_reducer", "other-agent", vision()))["ok"] is False
    result = json.loads(control.review_task_vision("todo_reducer", fixture.AGENT, vision()))
    assert result["ok"] is False, result
    assert not list(runtime.glob("goals/*/runs/index.jsonl"))


def test_authored_closure_cannot_hide_independent_runnable_work(tmp_path, monkeypatch):
    control, project, runtime = control_at(tmp_path)
    monkeypatch.chdir(project)
    result = json.loads(control.complete_task("todo_reducer", fixture.AGENT, "Synthetic acceptance",
        successor_todo_ids=["todo_cli"], agent_vision=vision("no_followup")))
    assert result["ok"] is True, result
    assert len(spends(runtime)) == 1
    quota = json.loads(control.should_run())
    assert quota["should_run"] is True
    assert quota["interaction_contract"]["mode"] != "terminal_no_followup"


def test_bad_vision_is_rejected_before_todo_completion_and_can_be_corrected(tmp_path, monkeypatch):
    control, project, runtime = control_at(tmp_path)
    monkeypatch.chdir(project)
    state = project / "ACTIVE_GOAL_STATE.md"
    before = state.read_bytes()
    invalid = vision("vision_patch_proposed")
    invalid["vision_patch"]["acceptance_summary"] = "x" * 421
    with pytest.raises(ValueError, match="vision_budget_exceeded"):
        control.complete_task("todo_reducer", fixture.AGENT, "Synthetic acceptance",
            successor_todo_ids=["todo_cli"], agent_vision=invalid)
    assert state.read_bytes() == before
    assert not list(runtime.glob("goals/*/runs/index.jsonl"))
    corrected = json.loads(control.complete_task("todo_reducer", fixture.AGENT, "Synthetic acceptance",
        successor_todo_ids=["todo_cli"], agent_vision=vision("vision_patch_proposed")))
    assert corrected["ok"] is True, corrected
    assert len(spends(runtime)) == 1


@pytest.mark.parametrize("length", [240, 241])
def test_unchanged_reason_preflight_precedes_all_completion_effects(tmp_path, monkeypatch, length):
    control, project, runtime = control_at(tmp_path)
    monkeypatch.chdir(project)
    first = json.loads(control.complete_task("todo_reducer", fixture.AGENT, "Synthetic acceptance",
        successor_todo_ids=["todo_cli"], agent_vision=vision("vision_patch_proposed")))
    assert first["ok"] is True, first
    state = project / "ACTIVE_GOAL_STATE.md"
    before = state.read_bytes()
    index = runtime / "goals" / fixture.GOAL / "runs/index.jsonl"
    before_index = index.read_bytes()
    calls = []
    original = control.run_cli

    def recorded(args, **kwargs):
        calls.append(args)
        return original(args, **kwargs)

    monkeypatch.setattr(control, "run_cli", recorded)
    if length == 241:
        with pytest.raises(ValueError, match="vision_unchanged_reason exceeds 240 chars"):
            control.complete_task("todo_cli", fixture.AGENT, "Synthetic acceptance",
                no_follow_up=True, vision_unchanged_reason="x" * length)
        assert calls == []  # No lifecycle, writeback or spend command was executed.
        assert state.read_bytes() == before
        assert index.read_bytes() == before_index
        assert len(spends(runtime)) == 1
    # Both valid initial authoring and correcting a rejected request take the
    # real CLI/MCP path. Whitespace is normalized by the same TS owner as refresh.
    result = json.loads(control.complete_task("todo_cli", fixture.AGENT, "Synthetic acceptance",
        no_follow_up=True, vision_unchanged_reason="  " + "x" * 240 + "  "))
    assert result["ok"] is True, result
    checkpoint = result["settlement"]["durable_writeback"]["vision_checkpoint"]
    assert checkpoint["decision"] == "unchanged_with_reason"
    assert checkpoint["unchanged_reason"] == "x" * 240
    assert len(spends(runtime)) == 2
    replay = json.loads(control.complete_task("todo_cli", fixture.AGENT, "Synthetic acceptance",
        no_follow_up=True, vision_unchanged_reason="x" * 240))
    assert replay["ok"] is True, replay
    assert len(spends(runtime)) == 2


def test_legacy_partial_completion_retries_corrected_vision_without_extra_spend(tmp_path, monkeypatch):
    import loopx.control_plane.host_adapter_settlement as adapter

    control, project, runtime = control_at(tmp_path)
    monkeypatch.chdir(project)
    invalid = vision("vision_patch_proposed")
    invalid["vision_patch"]["acceptance_summary"] = "x" * 421
    # Emulate an older host without input preflight. Lifecycle and the rejecting
    # writeback still execute through the real CLI and disposable runtime.
    with monkeypatch.context() as old_host:
        old_host.setattr(adapter, "prepare_vision_refresh", lambda *_args, **_kwargs: {})
        partial = json.loads(control.complete_task("todo_reducer", fixture.AGENT, "Synthetic acceptance",
            successor_todo_ids=["todo_cli"], agent_vision=invalid))
    assert partial["completed"] is True and partial["ok"] is False
    assert partial["settlement"]["failed_stage"] == "durable_writeback"
    assert partial["recovery"]["tool"] == "complete_task"
    assert partial["recovery"]["settlement_identity"]["todo_id"] == "todo_reducer"
    corrected = json.loads(control.complete_task("todo_reducer", fixture.AGENT, "Synthetic acceptance",
        successor_todo_ids=["todo_cli"], agent_vision=vision("vision_patch_proposed")))
    assert corrected["ok"] is True, corrected.get("settlement")
    assert len(spends(runtime)) == 1
    replay = json.loads(control.complete_task("todo_reducer", fixture.AGENT, "Synthetic acceptance",
        successor_todo_ids=["todo_cli"], agent_vision=vision("vision_patch_proposed")))
    assert replay["ok"] is True, replay.get("settlement")
    assert len(spends(runtime)) == 1


def test_checkpoint_recovery_missing_baseline_conflict_and_lost_response(tmp_path, monkeypatch):
    control, project, runtime = control_at(tmp_path)
    monkeypatch.chdir(project)
    result = json.loads(control.complete_task("todo_reducer", fixture.AGENT, "Synthetic acceptance",
        successor_todo_ids=["todo_cli"]))
    assert result["ok"] is True
    context = json.loads(control.review_task_vision("todo_reducer", fixture.AGENT))
    assert context["ok"] is True, context
    receipt = context["read_context_id"]
    unchanged = json.loads(control.review_task_vision("todo_reducer", fixture.AGENT,
        vision_unchanged_reason="Still correct", read_context_id=receipt))
    assert unchanged.get("vision_checkpoint", {}).get("satisfied") is not True
    assert len(spends(runtime)) == 1
    original = control.run_cli

    def response_lost(args, **kwargs):
        payload = original(args, **kwargs)
        assert json.loads(payload)["ok"] is True, payload
        raise TimeoutError("synthetic response loss after commit")

    monkeypatch.setattr(control, "run_cli", response_lost)
    with pytest.raises(TimeoutError):
        control.review_task_vision("todo_reducer", fixture.AGENT, vision("vision_patch_proposed"), read_context_id=receipt)
    monkeypatch.setattr(control, "run_cli", original)
    replay = json.loads(control.review_task_vision("todo_reducer", fixture.AGENT, vision("vision_patch_proposed"), read_context_id=receipt))
    assert replay["ok"] is True, replay
    assert replay["appended"] is False
    conflict = json.loads(control.review_task_vision("todo_reducer", fixture.AGENT, vision("no_followup"), read_context_id=receipt))
    assert conflict["ok"] is False
    assert len(spends(runtime)) == 1
    # A valid vision decision never consumes the independent open successor.
    assert json.loads(control.should_run())["should_run"] is True


def test_native_outer_controller_owns_new_vision_tool(monkeypatch):
    from loopx.kunluncode_goal_mode.guards import guard_native_controller_writeback
    from types import SimpleNamespace

    monkeypatch.setenv("LOOPX_KUNLUNCODE_OUTER_CONTROLLER", "1")
    control = SimpleNamespace()
    guard_native_controller_writeback(control)
    assert json.loads(control.review_task_vision("todo_any", "agent", vision()))["ok"] is False


def test_host_recovery_requires_its_explicit_fresh_read_context(tmp_path, monkeypatch):
    control, project, runtime = control_at(tmp_path)
    monkeypatch.chdir(project)
    completed = json.loads(control.complete_task("todo_reducer", fixture.AGENT, "Synthetic acceptance",
        successor_todo_ids=["todo_cli"]))
    assert completed["ok"] is True, completed
    index = runtime / "goals" / fixture.GOAL / "runs/index.jsonl"
    before = index.read_bytes()
    missing = json.loads(control.review_task_vision("todo_reducer", fixture.AGENT, vision()))
    assert missing["error_code"] == "checkpoint_read_context_required", missing
    old = json.loads(control.review_task_vision("todo_reducer", fixture.AGENT))
    current = json.loads(control.review_task_vision("todo_reducer", fixture.AGENT))
    replaced = json.loads(control.review_task_vision("todo_reducer", fixture.AGENT, vision(),
        read_context_id=old["read_context_id"]))
    assert replaced["error_code"] == "checkpoint_read_context_unknown_or_replaced", replaced
    # A synthetic owner acceptance edit between the read and the decision.
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text(encoding="utf-8") + "\n## Acceptance\n\nVerify revised output.\n", encoding="utf-8")
    stale = json.loads(control.review_task_vision("todo_reducer", fixture.AGENT, vision(),
        read_context_id=current["read_context_id"]))
    assert stale["error_code"] == "checkpoint_read_context_stale", stale
    assert index.read_bytes() == before
    fresh = json.loads(control.review_task_vision("todo_reducer", fixture.AGENT))
    assert "Verify revised output." in json.dumps(fresh["basis"])
    corrected = vision("vision_patch_proposed")
    corrected["vision_patch"]["acceptance_summary"] = "Verify revised output."
    result = json.loads(control.review_task_vision("todo_reducer", fixture.AGENT, corrected,
        read_context_id=fresh["read_context_id"]))
    assert result["ok"] is True and result["vision_checkpoint"]["satisfied"], result
    assert len(spends(runtime)) == 1
