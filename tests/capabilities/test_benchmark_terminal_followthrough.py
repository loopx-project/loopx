from __future__ import annotations

import json
from datetime import datetime

import pytest

from loopx.capabilities.benchmark_toolkit import concurrency_envelope as ce
from loopx.capabilities.benchmark_toolkit import experiment_board as eb
from loopx.capabilities.benchmark_toolkit import terminal_followthrough as tf
from tests.source_cli import run_source_cli


@pytest.fixture
def campaign(tmp_path):
    goal = "fixture-goal"
    project = tmp_path / "project"
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"goals": [{"id": goal, "objective": "Evaluate a fixture.", "repo": str(project)}]}))
    board = eb.default_benchmark_experiment_board_path(project=project, goal_id=goal)
    envelope = ce.default_benchmark_concurrency_envelope_path(project=project, goal_id=goal)
    ce.configure_benchmark_concurrency_envelope(
        envelope, ce.build_benchmark_concurrency_config(max_active_cases=4), execute=True,
    )
    row = {
        "schema_version": "benchmark_experiment_board_row_v0",
        "benchmark_id": "fixture", "study_id": "study", "case_id": "case-a",
        "run_id": "run-a", "arm_id": "official", "arm_role": "baseline", "attempt": 1,
        "status": "running", "observed_at": "2026-01-01T00:00:00Z",
        "model_id": "model", "protocol_id": "protocol", "comparison_protocol_id": "comparison",
        "claim_scope": "diagnostic_only", "primary_metric": "score", "metrics": {},
        "countability": {"integrity_qualified": False, "official_result_present": False, "score_countable": False},
        "treatment_fidelity": "not_applicable", "effort": {}, "insight": {"status": "pending"},
    }
    eb.upsert_benchmark_experiment_board_row(board, row)
    admission = ce.admit_benchmark_case(
        envelope, run_id="run-a", case_id="case-a", arm_role="baseline", execute=True,
        admitted_at="2026-01-01T00:00:00Z",
    )
    admission["goal_id"] = goal
    ce.admit_benchmark_case(envelope, run_id="other-run", case_id="other-case", arm_role="baseline", execute=True)
    identity = {k: row[k] for k in ("benchmark_id", "study_id", "case_id", "run_id", "arm_id", "arm_role", "attempt")}
    terminal = {**identity, "status": "completed", "observed_at": "2026-01-01T01:00:00Z",
                "solver_terminal": True, "grading_drained": True}
    return {"project": project, "goal_id": goal, "admission_receipt": admission,
            "identity": identity, "terminal": terminal}, board, envelope, registry


def test_real_cli_terminal_release_replay_and_default_preview(campaign, tmp_path):
    args, board, envelope, registry = campaign
    request = tmp_path / "request.json"
    request.write_text(json.dumps({k: args[k] for k in ("admission_receipt", "identity", "terminal")}))
    cli = ["--registry", str(registry), "--format", "json", "benchmark", "terminal-reconcile",
           "--goal-id", args["goal_id"], "--request-json", str(request)]
    before = (board.read_bytes(), envelope.read_bytes())
    preview = json.loads(run_source_cli(cli).stdout)
    assert preview["dry_run"] is True and preview["write_performed"] is False
    markdown = run_source_cli([*cli, "--format", "markdown"]).stdout
    assert "Run: `run-a`" in markdown and "Preview: `True`" in markdown
    assert before == (board.read_bytes(), envelope.read_bytes())
    result = json.loads(run_source_cli([*cli, "--execute"]).stdout)
    assert result["released"] and result["board_readback_verified"] and result["slot_readback_verified"]
    assert result["row"]["status"] == "completed"
    assert result["row"]["metrics"] == {} and result["row"]["countability"]["score_countable"] is False
    assert [x["run_id"] for x in ce.read_benchmark_concurrency_envelope(envelope)["active_runs"]] == ["other-run"]
    done = (board.read_bytes(), envelope.read_bytes())
    repeated = json.loads(run_source_cli([*cli, "--execute"]).stdout)
    assert repeated["already_released"] and not repeated["write_performed"]
    assert done == (board.read_bytes(), envelope.read_bytes())


def test_delayed_terminal_and_original_running_shard_use_real_cli(campaign, tmp_path):
    args, board, envelope, registry = campaign
    running = eb.read_benchmark_experiment_board_rows(board)[0]
    running["observed_at"] = "2026-01-01T02:00:00Z"
    eb.upsert_benchmark_experiment_board_row(board, running)
    shard = tmp_path / "provider.jsonl"
    eb.upsert_benchmark_experiment_board_row(shard, running)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({k: args[k] for k in ("admission_receipt", "identity", "terminal")}))
    cli = ["--registry", str(registry), "--format", "json", "benchmark"]
    terminal = json.loads(run_source_cli([
        *cli, "terminal-reconcile", "--goal-id", args["goal_id"],
        "--request-json", str(request), "--execute",
    ]).stdout)
    assert terminal["released"]
    assert datetime.fromisoformat(terminal["row"]["observed_at"]) > datetime.fromisoformat(running["observed_at"])
    merged = json.loads(run_source_cli([
        *cli, "experiment-board-reconcile", "--goal-id", args["goal_id"],
        "--source-ledger", str(shard), "--execute",
    ]).stdout)
    assert merged["ok"] and merged["runs"][0]["status"] == "completed"
    assert [x["run_id"] for x in ce.read_benchmark_concurrency_envelope(envelope)["active_runs"]] == ["other-run"]


@pytest.mark.parametrize("field,value", [("grading_drained", False), ("solver_terminal", False),
                                         ("attempt", 2), ("run_id", "foreign"), ("case_id", "foreign")])
def test_unknown_pending_or_wrong_identity_never_writes(campaign, field, value):
    args, board, envelope, _ = campaign
    args["terminal"][field] = value
    before = (board.read_bytes(), envelope.read_bytes())
    with pytest.raises(ValueError):
        tf.reconcile_benchmark_terminal(**args, execute=True)
    assert before == (board.read_bytes(), envelope.read_bytes())


def test_runtime_unavailable_preserves_original_evidence(campaign, monkeypatch):
    args, board, envelope, _ = campaign
    before = (board.read_bytes(), envelope.read_bytes())
    def unavailable(*a, **kw):
        raise tf.EffectRuntimeStartupError("fixture unavailable", diagnostic_code="fixture_unavailable")
    monkeypatch.setattr(tf, "effect_runtime_result", unavailable)
    with pytest.raises(ValueError, match="runtime unavailable; retry original"):
        tf.reconcile_benchmark_terminal(**args, execute=True)
    assert before == (board.read_bytes(), envelope.read_bytes())


def test_old_admission_cannot_touch_replacement(campaign):
    args, board, envelope, _ = campaign
    ce.release_benchmark_case(envelope, run_id="run-a", execute=True)
    ce.admit_benchmark_case(envelope, run_id="run-a", case_id="case-a", arm_role="baseline",
                            admitted_at="2026-01-01T00:30:00Z", execute=True)
    before = (board.read_bytes(), envelope.read_bytes())
    with pytest.raises(ValueError):
        tf.reconcile_benchmark_terminal(**args, execute=True)
    assert before == (board.read_bytes(), envelope.read_bytes())


def test_same_slot_cannot_be_adopted_by_a_second_study(campaign):
    args, board, envelope, _ = campaign
    other = eb.read_benchmark_experiment_board_rows(board)[0]
    other["study_id"] = "different-study"
    eb.upsert_benchmark_experiment_board_row(board, other)
    before = (board.read_bytes(), envelope.read_bytes())
    with pytest.raises(ValueError):
        tf.reconcile_benchmark_terminal(**args, execute=True)
    assert before == (board.read_bytes(), envelope.read_bytes())


def test_idempotent_admission_is_not_original_launch_evidence(campaign):
    args, board, envelope, _ = campaign
    receipt = ce.admit_benchmark_case(envelope, run_id="run-a", case_id="case-a",
                                     arm_role="baseline", execute=True)
    assert receipt["admitted"] and receipt["idempotent"]
    args["admission_receipt"] = {**receipt, "goal_id": args["goal_id"]}
    before = (board.read_bytes(), envelope.read_bytes())
    with pytest.raises(ValueError):
        tf.reconcile_benchmark_terminal(**args, execute=True)
    assert before == (board.read_bytes(), envelope.read_bytes())


def test_write_failure_keeps_original_slot_then_replay_preserves_new_metrics(campaign, monkeypatch):
    args, board, envelope, _ = campaign
    original = tf.atomic_write_json
    def fail(*a, **kw):
        raise OSError("injected release failure")
    monkeypatch.setattr(tf, "atomic_write_json", fail)
    with pytest.raises(OSError):
        tf.reconcile_benchmark_terminal(**args, execute=True)
    assert eb.read_benchmark_experiment_board_rows(board)[0]["status"] == "completed"
    assert len(ce.read_benchmark_concurrency_envelope(envelope)["active_runs"]) == 2
    row = eb.read_benchmark_experiment_board_rows(board)[0]
    row.update(observed_at="2026-01-01T02:00:00Z", metrics={"score": {"value": 9, "higher_is_better": True}},
               insight={"status": "complete"})
    eb.upsert_benchmark_experiment_board_row(board, row)
    monkeypatch.setattr(tf, "atomic_write_json", original)
    result = tf.reconcile_benchmark_terminal(**args, execute=True)
    assert result["released"] and result["row"]["metrics"]["score"]["value"] == 9
    assert result["row"]["insight"]["status"] == "complete"


def test_board_failure_or_readback_failure_cannot_release(campaign, monkeypatch):
    args, board, envelope, _ = campaign
    before = envelope.read_bytes()
    monkeypatch.setattr(tf, "_replace_domain_state_jsonl", lambda *a: None)
    with pytest.raises(ValueError, match="board readback"):
        tf.reconcile_benchmark_terminal(**args, execute=True)
    assert envelope.read_bytes() == before


def test_concurrent_admission_between_plan_and_commit_fails_closed(campaign, monkeypatch):
    args, board, envelope, _ = campaign
    original = tf.effect_runtime_result
    def interleave(*a, **kw):
        result = original(*a, **kw)
        ce.admit_benchmark_case(envelope, run_id="parallel", case_id="parallel-case", arm_role="baseline", execute=True)
        return result
    monkeypatch.setattr(tf, "effect_runtime_result", interleave)
    before = board.read_bytes()
    with pytest.raises(ValueError, match="inputs changed"):
        tf.reconcile_benchmark_terminal(**args, execute=True)
    assert board.read_bytes() == before
    assert len(ce.read_benchmark_concurrency_envelope(envelope)["active_runs"]) == 3
    monkeypatch.setattr(tf, "effect_runtime_result", original)
    tf.reconcile_benchmark_terminal(**args, execute=True)
    assert {x["run_id"] for x in ce.read_benchmark_concurrency_envelope(envelope)["active_runs"]} == {"other-run", "parallel"}


def test_missing_ledger_is_not_successful_replay(campaign):
    args, _, envelope, _ = campaign
    envelope.unlink()
    with pytest.raises(ValueError, match="ledger unavailable"):
        tf.reconcile_benchmark_terminal(**args, execute=True)


def test_wrong_goal_never_writes(campaign):
    args, board, envelope, _ = campaign
    args["admission_receipt"] = {**args["admission_receipt"], "goal_id": "foreign"}
    before = (board.read_bytes(), envelope.read_bytes())
    with pytest.raises(ValueError):
        tf.reconcile_benchmark_terminal(**args, execute=True)
    assert before == (board.read_bytes(), envelope.read_bytes())


def test_managed_cli_cannot_route_an_unregistered_project(campaign, tmp_path):
    args, board, envelope, _ = campaign
    empty_registry = tmp_path / "empty.json"
    empty_registry.write_text('{"goals": []}')
    # Remove the fixture's local route as well as the supplied registry route.
    (args["project"] / ".loopx" / "registry.json").unlink()
    request = tmp_path / "request.json"
    request.write_text(json.dumps({k: args[k] for k in ("admission_receipt", "identity", "terminal")}))
    before = (board.read_bytes(), envelope.read_bytes())
    result = run_source_cli([
        "--registry", str(empty_registry), "--format", "json", "benchmark", "terminal-reconcile",
        "--project", str(args["project"]), "--goal-id", args["goal_id"],
        "--request-json", str(request), "--execute",
    ], check=False)
    assert result.returncode != 0
    assert before == (board.read_bytes(), envelope.read_bytes())
