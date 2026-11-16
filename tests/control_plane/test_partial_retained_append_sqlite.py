"""SQLite result append crashes must hold the original Turn."""
import json
from pathlib import Path

import pytest

from loopx import status
from loopx.cli import main
from loopx.cli_commands import project_lifecycle_refresh_state as refresh_cli
from loopx.control_plane.goals import checkpoint_context_io as context_io
from loopx.control_plane.quota.settlement import SettlementIdentity
from tests.control_plane.checkpoint_process import start_probe
from tests.control_plane.test_default_checkpoint_continuation import _fixture
from tests.test_goal_mode_mcp_settlement import AGENT_ID, GOAL_ID, _run_cli


@pytest.mark.parametrize(
    ("window", "stage"),
    [("before_artifacts", "result_review_pending"), ("after_json", "operation_unknown"),
     ("after_markdown", "operation_unknown"), ("after_index", "direction_pending")],
)
def test_interrupted_result_append_in_sqlite(tmp_path, monkeypatch, capsys, window, stage):
    registry, state, todo = _fixture("sqlite", tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    document = json.loads(registry.read_text())
    goal = next(item for item in document["goals"] if item["id"] == GOAL_ID)
    goal["execution_profile"] = {
        **goal.get("execution_profile", {}),
        "replan_after_effective_turns": 1,
    }
    registry.write_text(json.dumps(document), encoding="utf-8")
    turn = "partial-result-original"
    identity = SettlementIdentity(GOAL_ID, AGENT_ID, todo_id=todo, turn_instance_id=turn)
    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", todo)

    def guard(turn_id):
        return _run_cli(registry, "quota", "should-run", *binding, "--codex-app",
                        "--turn-instance-id", turn_id)

    rc, admitted = guard(turn)
    assert rc == 0 and admitted["heartbeat_receipt"]["settlement_identity"] == identity.as_dict(), admitted
    candidate = state.parent / "candidate.txt"
    candidate.write_text("synthetic result", encoding="utf-8")
    barrier = tmp_path / "fault"
    barrier.mkdir()
    (barrier / "release").touch()
    actual_effect = context_io.effect_runtime_result
    intercepted = []
    returns = []

    def fault(method, params, **kwargs):
        if method != "goal.checkpoint_read_context.commit":
            return actual_effect(method, params, **kwargs)
        assert params["purpose"] == "delivery_result", params["purpose"]
        intercepted.append(params["purpose"])
        child = start_probe({"mode": "checkpoint", "provider": "sqlite",
                             "barrier": str(barrier), "params": params, "fault": window})
        stdout, stderr = child.communicate(timeout=25)
        assert child.returncode == 86, stdout + stderr
        raise OSError("injected response loss during the original result append")

    def success_return(*args, **kwargs):
        returns.append("material return")
        return {"ok": True}

    for name in ("dispatch_committed_cli_post_writeback_hooks",
                 "sync_explore_graph_after_material_refresh",
                 "sync_human_gate_after_refresh"):
        monkeypatch.setattr(refresh_cli, name, success_return)
    refresh = ("--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
               "refresh-state", *binding, "--turn-instance-id", turn,
               "--classification", "bounded_candidate", "--delivery-batch-scale", "implementation",
               "--delivery-outcome", "outcome_progress", "--delivery-boundary", "semantic_closeout",
               "--no-global-sync", "--suppress-external-sinks")

    def invoke_refresh():
        capsys.readouterr()
        rc = main(list(refresh))
        return rc, json.loads(capsys.readouterr().out)

    with monkeypatch.context() as injected:
        injected.setattr(context_io, "effect_runtime_result", fault)
        rc, interrupted = invoke_refresh()
    assert rc == 1 and interrupted["appended"] is False and len(intercepted) == 1, interrupted
    goal_root = runtime / "goals" / GOAL_ID
    index = goal_root / "runs/index.jsonl"
    receipt = context_io._receipt_path(runtime, identity, purpose="delivery_result")
    attempt = json.loads(receipt.read_text(encoding="utf-8"))["commit_attempt"]
    report = Path(attempt["json_path"])
    markdown = Path(attempt["markdown_path"])
    original = {path: path.read_bytes() if path.exists() else None
                for path in (receipt, index, candidate, report, markdown)}
    assert bool(original[report]) is (window != "before_artifacts")
    assert bool(original[markdown]) is (window in {"after_markdown", "after_index"})
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert sum(row.get("settlement_identity") == identity.as_dict() for row in rows) == (
        1 if window == "after_index" else 0
    )
    rc, observed = _run_cli(registry, "status", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    assert rc == 0, observed
    item = next(row for row in observed["attention_queue"]["items"] if row["goal_id"] == GOAL_ID)
    history = status.project_asset_handoff_state(
        ready=True, project_asset={"execution_profile": {"outcome_floor": {"outcome_markers": ["accepted"]}}},
        latest_runs=rows, settlement_source={"runtime_root": str(runtime), "goal_id": GOAL_ID,
                                              "registry_path": registry},
    )
    current_rc, current = guard(turn)
    fresh_rc, fresh = guard("different-turn")
    spend_rc, spend = _run_cli(registry, "quota", "spend-slot", *binding, "--turn-instance-id",
                               turn, "--source", "heartbeat", "--execute")
    phase = item.get("first_delivery_progress", {})
    assert spend_rc == 1 and spend["appended"] is False and not returns
    assert history.get("post_handoff_small_scale_streak", 0) == 0
    assert not any(trigger["kind"] == "periodic_review_due"
                   for obligation in item.get("autonomous_replan_obligations_by_agent", {}).values()
                   for trigger in obligation.get("triggers", []))
    assert phase.get("stage") == stage, phase
    assert phase["settlement_identity"] == identity.as_dict()
    assert phase["outcome_retained"] is (window == "after_index")
    if stage == "operation_unknown":
        read_rc, read = _run_cli(registry, "checkpoint-context", *binding, "--turn-instance-id",
                                 turn, "--purpose", "delivery_result")
        retry_rc, retry = invoke_refresh()
        assert (read_rc, read.get("error_code")) == (1, "checkpoint_commit_unknown")
        assert (retry_rc, retry.get("error_code")) == (1, "checkpoint_commit_unknown")
        assert (current_rc, current.get("error_code")) == (1, "checkpoint_commit_unknown")
        assert not current.get("should_run") and "delivery_result_context" not in current
        assert not returns
    elif window == "before_artifacts":
        assert current_rc == 0 and current["work_input_restored"], current
        assert current["delivery_result_context"]["read_context_id"] == admitted["delivery_result_context"]["read_context_id"]
    else:
        assert current_rc == 0 and "delivery_result_context" not in current, current
    assert fresh_rc == 0
    assert fresh["unsettled_host_turn_recovery"]["prior_turn_instance_id"] == turn
    assert fresh["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    assert all((path.read_bytes() if path.exists() else None) == content
               for path, content in original.items())
