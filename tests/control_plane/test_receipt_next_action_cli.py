"""A Turn can edit only its original Todo step, never a replan's display advice."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
import test_quota_settlement_cli as fixture
from canonical_authority_fixture import (
    initialize_canonical_authority, isolate_sqlite_runtime,
    without_resume_observation_time as stable,
)

from loopx.cli import main as cli_main
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.quota.settlement import read_heartbeat_settlement


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_waiting_original_turn_preserves_other_todo_step_and_settles_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = fixture._write_fixture(tmp_path)
    state = fixture._configure_read_only_todo(project)
    state.write_text(state.read_text().replace("required_capabilities=shell%2Cfilesystem_read",
        f"claimed_by={fixture.AGENT_ID} required_capabilities=shell%2Cfilesystem_read") + (
        "\n- [ ] [P2] Compare the independent artifact.\n"
        f"  <!-- loopx:todo todo_id={fixture.ALTERNATIVE_TODO_ID} status=open "
        f"task_class=advancement_task action_kind=validate claimed_by={fixture.AGENT_ID} "
        "continuation_policy=same_agent_non_delivery required_capabilities=shell%2Cfilesystem_read -->\n"
        "- [ ] [P3] Observe the original evidence window.\n"
        f"  <!-- loopx:todo todo_id=todo_dependency status=open claimed_by={fixture.AGENT_ID} "
        "task_class=continuous_monitor target_key=original-evidence-window cadence=1h "
        "material_change_generation=0 next_due_at=2099-01-01T00:00:00Z -->\n"
    ))

    def run(*args: str) -> tuple[int, dict]:
        with monkeypatch.context() as context:
            context.chdir(project)
            code = cli_main(["--registry", str(registry), "--runtime-root", str(runtime),
                "--format", "json", *args])
            return code, json.loads(capsys.readouterr().out)

    code, initial = run("todo", "list", "--goal-id", fixture.GOAL_ID)
    assert code == 0, initial
    canonical = initialize_canonical_authority(runtime, fixture.GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=fixture.GOAL_ID, todos=initial["todos"],
            handoff_mode="soft_claim", leases=[]), state_path=state, provider=provider)
    binding = ("--agent-id", fixture.AGENT_ID, "--todo-id", fixture.TODO_ID,
        "--turn-instance-id", "original-next-action-turn")
    guard_args = ("quota", "should-run", "--goal-id", fixture.GOAL_ID, "--codex-app",
         *binding)
    code, admitted = run(*guard_args)
    assert code == 0 and admitted["normal_delivery_allowed"], admitted
    identity = admitted["heartbeat_receipt"]["settlement_identity"]
    code, waiting = run("todo", "update", "--goal-id", fixture.GOAL_ID,
        "--agent-id", fixture.AGENT_ID, "--todo-id", fixture.TODO_ID,
        "--resume-when", "monitor_changed:todo_dependency",
        "--successor-todo-id", fixture.ALTERNATIVE_TODO_ID,
        "--update-operation-id", "original-wait", "--update-expected-provider-revision",
        canonical["provider_revision"])
    assert code == 0, waiting
    code, incumbent = run("refresh-state", "--goal-id", fixture.GOAL_ID,
        "--agent-id", fixture.AGENT_ID, "--next-action", "Keep B's independent experiment.",
        "--no-global-sync", "--suppress-external-sinks")
    assert code == 0, incumbent
    assert incumbent["recommended_action_resolution"]["todo_id"] == fixture.ALTERNATIVE_TODO_ID
    index = runtime / "goals" / fixture.GOAL_ID / "runs" / "index.jsonl"
    before_index = index.read_bytes()
    before_display = state.read_bytes()
    code, before = run("todo", "list", "--goal-id", fixture.GOAL_ID)
    assert code == 0, before
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0",
        "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "The bounded segment retains its independent evidence dependency."},
        "path_delta": {"outcome": "continue", "prior_assumption": "The original route is viable.",
            "observed_reality": "Independent validation accepts the bounded segment; its next stage awaits evidence.",
            "retained": ["Original task acceptance and evidence dependency"],
            "evidence_refs": ["evidence:validated-original-segment"]}}))
    refresh = ("refresh-state", "--goal-id", fixture.GOAL_ID, *binding,
        "--classification", "original_segment_verified", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(vision),
        "--no-global-sync", "--suppress-external-sinks")
    for _ in range(2):
        code, rejected = run(*refresh, "--next-action", "Await A's independent dependency.")
        assert code == 1 and rejected["error_code"] == "next_action_task_unavailable", rejected
        assert "without --next-action" in rejected["error"]
        assert index.read_bytes() == before_index and state.read_bytes() == before_display
        assert fixture._spend_run_count(runtime) == 0
    # Finish A with its original identity, without using B as a step-write target.
    code, written = run(*refresh)
    assert code == 0 and written["settlement_progress"]["state"] == "spend_required", written
    assert written["settlement_identity"] == identity
    writeback = read_heartbeat_settlement(runtime, goal_id=fixture.GOAL_ID,
        agent_id=fixture.AGENT_ID, todo_id=fixture.TODO_ID, turn_instance_id=binding[-1])
    assert writeback is not None and writeback.writeback.failure is None
    code, replay = run(*refresh)
    assert code == 0 and replay["idempotent_replay"], replay
    command = written["settlement_owed"]["command"]
    for repeated in (False, True):
        code, spent = run(*shlex.split(command)[1:])
        assert code == 0, spent
        assert spent["idempotent_replay"] is repeated
    assert fixture._spend_run_count(runtime) == 1
    code, settled = run(*guard_args)
    assert code == 0 and settled["effective_action"] == "heartbeat_settled_skip", settled
    code, after = run("todo", "list", "--goal-id", fixture.GOAL_ID)
    assert code == 0 and stable(after["todos"]) == stable(before["todos"]), after
    assert after["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"]
    final = read_heartbeat_settlement(runtime, goal_id=fixture.GOAL_ID,
        agent_id=fixture.AGENT_ID, todo_id=fixture.TODO_ID, turn_instance_id=binding[-1])
    assert final is not None and final.writeback == writeback.writeback
    code, next_guard = run("quota", "should-run", "--goal-id", fixture.GOAL_ID,
        "--agent-id", fixture.AGENT_ID, "--todo-id", fixture.ALTERNATIVE_TODO_ID,
        "--turn-instance-id", "fresh-alternative-turn", "--codex-app", )
    assert code == 0 and next_guard["normal_delivery_allowed"], next_guard
    route = next_guard["agent_lane_next_action"]
    assert route["todo_id"] == fixture.ALTERNATIVE_TODO_ID
    assert route["next_step"] == "Keep B's independent experiment."


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("recommendation_mode", ["derived", "explicit"])
def test_todoless_replan_refuses_step_and_recovers_original_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    provider: str, recommendation_mode: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = fixture._write_fixture(tmp_path)
    fixture._configure_autonomous_replan_fixture(project, runtime, registry)
    state = project / f".codex/goals/{fixture.GOAL_ID}/ACTIVE_GOAL_STATE.md"

    def run(*args: str) -> tuple[int, dict]:
        return fixture._run_cli(registry, runtime, *args, cwd=project)

    code, listed = run("todo", "list", "--goal-id", fixture.GOAL_ID)
    assert code == 0, listed
    initialize_canonical_authority(runtime, fixture.GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=fixture.GOAL_ID,
            todos=listed["todos"], handoff_mode="soft_claim", leases=[]),
        state_path=state, provider=provider)
    turn = "replan-recommendation-recovery"
    guard_args = ("quota", "should-run", "--codex-app", "--goal-id", fixture.GOAL_ID,
        "--agent-id", fixture.AGENT_ID, "--turn-instance-id", turn,
        )
    code, guard = run(*guard_args)
    assert code == 0 and guard["decision"] == "autonomous_replan_required", guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert identity["binding_kind"] == "autonomous_replan" and "todo_id" not in identity
    code, before_todos = run("todo", "list", "--goal-id", fixture.GOAL_ID)
    assert code == 0, before_todos
    index = runtime / "goals" / fixture.GOAL_ID / "runs" / "index.jsonl"
    before_index, before_display = index.read_bytes(), state.read_bytes()
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0",
        "state": "active", "vision_patch": {
            "acceptance_summary": "Observe the original monitor only when due.",
            "advancement_policy": "as_needed"},
        "path_delta": {"outcome": "no_change",
            "prior_assumption": "The monitor might need another early observation.",
            "observed_reality": "Independent evidence retains its future observation window.",
            "retained": ["Original monitor identity, state and deadline."],
            "evidence_refs": ["evidence:replan-recommendation"]}}), encoding="utf-8")
    refresh = ("refresh-state", "--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID,
        "--replan-obligation-id", identity["replan_obligation_id"], "--turn-instance-id", turn,
        "--classification", "bounded_replan_progress", "--delivery-batch-scale", "single_surface",
        "--delivery-outcome", "outcome_progress", "--progress-result-class", "advanced",
        "--progress-surface-id", "surface-new", "--progress-hypothesis-id", "hypothesis-new",
        "--progress-probe-kind", "probe-new", "--progress-evidence-id", "evidence:replan-recommendation",
        "--agent-vision-json", str(vision), "--no-global-sync", "--suppress-external-sinks")
    for _ in range(2):
        code, rejected = run(*refresh, "--next-action", "Continue the replan.", "--dry-run")
        assert code == 1 and rejected["error_code"] == "next_action_task_unavailable", rejected
        assert "--recommended-action" in rejected["error"]
        assert rejected["appended"] is False
        assert index.read_bytes() == before_index and state.read_bytes() == before_display
    explicit = ("--recommended-action", "Observe the monitor when due.") if recommendation_mode == "explicit" else ()
    code, preview = run(*refresh, *explicit, "--dry-run")
    assert code == 0 and preview["appended"] is False, preview
    code, written = run(*refresh, *explicit)
    assert code == 0 and written["settlement_identity"] == identity, written
    assert "todo_id" not in written["recommended_action_resolution"]
    spend = ("quota", "spend-slot", "--goal-id", fixture.GOAL_ID,
        "--agent-id", fixture.AGENT_ID, "--replan-obligation-id", identity["replan_obligation_id"],
        "--turn-instance-id", turn, "--source", "heartbeat", "--slots", "1", "--execute",
        )
    code, spent = run(*spend)
    assert code == 0 and spent["settlement_result"]["ok"], spent
    code, replay = run(*spend)
    assert code == 0 and replay["idempotent_replay"] and replay["appended"] is False, replay
    assert fixture._spend_run_count(runtime) == 1
    code, cold = run(*guard_args)
    assert code == 0 and cold["effective_action"] == "heartbeat_settled_skip", cold
    code, after_todos = run("todo", "list", "--goal-id", fixture.GOAL_ID)
    assert code == 0 and after_todos["todos"] == before_todos["todos"], after_todos
