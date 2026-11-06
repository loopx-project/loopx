"""Same-work SQLite journey: evidence → ordinary Turn → real action → readback.

The deterministic actor proves transport/adoption attribution, not model quality
or a causal benchmark gain. No evaluator, credentials or active Goal are used.
"""
import hashlib
import json


from tests.control_plane.test_quota_settlement_cli import (
    _write_fixture, _run_cli, GOAL_ID, AGENT_ID, TODO_ID,
)
from loopx.capabilities.explore.result_log import explore_result_log_path, load_explore_result_events
from loopx.capabilities.explore.turn_context import explore_turn_context
from loopx.presentation.explore_results_api import _result_rows


def decision():
    outcome = dict(metric="quality", unit="points", higher_is_better=True,
        comparison_scope="validation:fixed", input_revision="inputs-v1", evaluation_ref="evaluator:outcome-v1")
    return dict(schema_version="work_yardstick_decision_v0",
        target=dict(summary="Improve the best accepted score", scope="Same public input and budget",
                    acceptance_ref="acceptance:task-v1"),
        authoritative=outcome, proxy={**outcome, "metric": "screen", "evaluation_ref": "evaluator:screen-v1"},
        counterevidence_refs=["result:reversal"], prior_finding_id=None,
        next_action=dict(method_id="method:direct", reason="Use one informative direct check after screening reversed the ordering",
                         cost_bound="One available evaluation, with the original correctness check"))


def attachment():
    return dict(schema_version="explore_result_attachment_v0", question="Does this screen predict the outcome?",
        applicability="Fixed inputs and budget only", input_revision="inputs-v1",
        observation="The screen ranked the better validated candidate lower",
        interpretation="Reassess the screen; retain the original correctness requirement", status="confirmed",
        evidence_refs=["result:reversal"], work_yardstick_decision=decision())


def setup(tmp_path, enabled=True):
    project, runtime, path = _write_fixture(tmp_path, provider="sqlite")
    config = json.loads(path.read_text())
    config["goals"][0]["explore_graph"] = {"enabled": True}
    config["goals"][0]["quota"]["allowed_slots"] = 4
    config["goals"][0].setdefault("execution_profile", {})["yardstick_enabled"] = enabled
    path.write_text(json.dumps(config))
    return project, runtime, path


def run(project, runtime, path, *command):
    rc, packet = _run_cli(path, runtime, *command, cwd=project)
    assert rc == 0, json.dumps({key: packet.get(key) for key in ("error", "reason", "classification", "decision", "admission", "quota")}, ensure_ascii=False, indent=2)
    return packet


def binding(turn):
    return ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", turn]


def refresh(project, runtime, path, turn, source):
    run(project, runtime, path, "quota", "should-run", "--codex-app", *binding(turn))
    return run(project, runtime, path, "refresh-state", *binding(turn), "--classification", "validated_change",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--explore-result-json", str(source), "--no-global-sync", "--suppress-external-sinks")


def context(runtime, path, *, detail=True):
    params = dict(registry_path=path, runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID)
    compact = explore_turn_context(**params)
    cues = compact["graph"].get("work_yardsticks", [])
    if detail and cues:
        return explore_turn_context(**params, result_node=cues[0]["node_id"])
    return compact


def test_sqlite_current_work_to_ordinary_turn_to_concrete_action_and_readback(tmp_path):
    project, runtime, path = setup(tmp_path)
    source = tmp_path / "capture.json"
    source.write_text(json.dumps(attachment()))
    first = refresh(project, runtime, path, "turn-yardstick-1", source)
    assert first["explore_result_delivery"]["ok"], first
    assert not first.get("autonomous_replan_ack", {}).get("recorded")
    run(project, runtime, path, "quota", "spend-slot", *binding("turn-yardstick-1"), "--slots", "1", "--source", "heartbeat", "--execute")
    short = context(runtime, path, detail=False)["graph"]["work_yardsticks"][0]
    assert "authoritative" not in short and "measurement_feedback" not in short
    assert "result:reversal" in short["counterevidence_refs"] and short["details_command"]
    cue = context(runtime, path)["graph"]["work_yardsticks"][0]
    assert cue["binding"]["todo_id"] == TODO_ID
    assert cue["source_finding_id"] == first["explore_result_delivery"]["finding_id"]
    # The ordinary guard delivers the same current decision, without introducing
    # a replan obligation or changing its task identity.
    guard = run(project, runtime, path, "quota", "should-run", "--codex-app", *binding("turn-yardstick-2"))
    assert cue["source_finding_id"] in json.dumps(guard)
    assert "work_yardstick_decision_v0" in json.dumps(guard)
    # Independent synthetic workload: screening picked A, the accepted score
    # prefers B. The actor uses the cited action, verifies a hard constraint,
    # evaluates B and writes an actual result artifact (not an adoption boolean).
    candidate = {"correct": True, "accepted_score": 8, "screen": 3}
    assert candidate["correct"] and cue["next_action"]["method_id"] == "method:direct"
    artifact = project / "accepted-result.json"
    artifact.write_text(json.dumps(candidate, sort_keys=True))
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    follow = attachment()
    follow["evidence_refs"] = ["result:direct-1"]
    follow["observation"] = "Direct evaluation returned 8 points for the correct candidate"
    follow["work_yardstick_decision"].update(prior_finding_id=cue["source_finding_id"], counterevidence_refs=[])
    follow["work_yardstick_decision"]["proxy"] = None
    follow["work_yardstick_decision"]["next_action"] = dict(method_id="method:next", reason="Retain the direct result and compare a distinct candidate", cost_bound="One further check within the original budget")
    follow["execution_observation"] = dict(schema_version="explore_execution_observation_v0",
        candidate_sha256=digest, input_revision="inputs-v1", planned_method_id="method:direct", executed_method_id="method:direct",
        change_reason=None, elapsed_seconds=0.01, limit_seconds=1, search_iterations=1, stop_reason="evaluation returned",
        evidence_refs=["result:direct-1"], result_ref="result:direct-1", result_candidate_sha256=digest, exit_code=0)
    follow["measurements"] = [{**decision()["authoritative"], "value": 8, "candidate_ref": f"sha256:{digest}", "evidence_ref": "result:direct-1"}]
    source2 = tmp_path / "next.json"
    source2.write_text(json.dumps(follow))
    second = run(project, runtime, path, "refresh-state", *binding("turn-yardstick-2"), "--classification", "validated_change",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--explore-result-json", str(source2), "--no-global-sync", "--suppress-external-sinks")
    assert second["explore_result_delivery"]["ok"], second
    run(project, runtime, path, "quota", "spend-slot", *binding("turn-yardstick-2"), "--slots", "1", "--source", "heartbeat", "--execute")
    cue2 = context(runtime, path)["graph"]["work_yardsticks"][0]
    observed = cue2["previous_decision_followthrough"]
    assert observed["source_finding_id"] == cue["source_finding_id"]
    assert observed["executed_method_id"] == "method:direct" and observed["recorded_planned_method_matches"]
    assert observed["candidate_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert cue2["measurement_feedback"][0]["best"]["value"] == 8
    # Ordinary unchanged work has no extra finding or decision write.
    log = explore_result_log_path(runtime, GOAL_ID)
    unchanged = log.read_bytes()
    run(project, runtime, path, "quota", "should-run", "--codex-app", *binding("turn-yardstick-3"))
    third = run(project, runtime, path, "refresh-state", *binding("turn-yardstick-3"), "--classification", "validated_change",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--no-global-sync", "--suppress-external-sinks")
    assert log.read_bytes() == unchanged and "explore_result" not in third
    assert context(runtime, path)["graph"]["work_yardsticks"][0]["source_finding_id"] == cue2["source_finding_id"]
    run(project, runtime, path, "quota", "spend-slot", *binding("turn-yardstick-3"), "--slots", "1", "--source", "heartbeat", "--execute")
    # Disable removes the cue; canonical historical evidence remains readable.
    config = json.loads(path.read_text())
    config["goals"][0]["execution_profile"]["yardstick_enabled"] = False
    path.write_text(json.dumps(config))
    assert "work_yardsticks" not in context(runtime, path)["graph"]
    rows = _result_rows(runtime, GOAL_ID, path)
    assert rows[0]["work_yardstick"]["binding"]["todo_id"] == TODO_ID
    from loopx.extensions.lark.presentation.explore_results import _finding_record_values
    original = _finding_record_values({"summary": "legacy"}, goal_id=GOAL_ID, source_id="synthetic")
    assert original["Summary"] == "legacy"
    projected = _finding_record_values(rows[0], goal_id=GOAL_ID, source_id="synthetic")
    assert "Target:" in projected["Summary"] and "Acceptance source:" in projected["Summary"]
    assert "fidelity and acceptance remain independently assessed" in projected["Summary"]
    # Successful replay is exact readback even after disabling the feature.
    log = explore_result_log_path(runtime, GOAL_ID)
    before = log.read_bytes()
    replay = run(project, runtime, path, "refresh-state", *binding("turn-yardstick-2"), "--classification", "validated_change",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--explore-result-json", str(source2), "--no-global-sync", "--suppress-external-sinks")
    assert replay["explore_result_delivery"]["idempotent_replay"] and log.read_bytes() == before


def test_disabled_new_attachment_rejects_before_primary_commit(tmp_path):
    project, runtime, path = setup(tmp_path, enabled=False)
    source = tmp_path / "capture.json"
    source.write_text(json.dumps(attachment()))
    run(project, runtime, path, "quota", "should-run", "--codex-app", *binding("turn-off"))
    rc, rejected = _run_cli(path, runtime, "refresh-state", *binding("turn-off"), "--classification", "validated_change",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--explore-result-json", str(source), "--no-global-sync", "--suppress-external-sinks", cwd=project)
    assert rc == 1 and not rejected.get("appended"), rejected
    assert "yardstick_enabled" in json.dumps(rejected)
    assert not explore_result_log_path(runtime, GOAL_ID).exists()


def test_committed_pending_capture_recovers_after_disable_without_new_authority(tmp_path, monkeypatch, capsys):
    from loopx.cli import main
    import loopx.capabilities.explore.result_writeback as writer
    project, runtime, path = setup(tmp_path)
    source = tmp_path / "capture.json"
    source.write_text(json.dumps(attachment()))
    turn = "turn-interrupted"
    run(project, runtime, path, "quota", "should-run", "--codex-app", *binding(turn))
    command = ["refresh-state", *binding(turn), "--classification", "validated_change",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--explore-result-json", str(source), "--no-global-sync", "--suppress-external-sinks"]
    def unavailable(*args, **kwargs):
        raise OSError("injected graph write interruption")
    with monkeypatch.context() as patch:
        patch.chdir(project)
        patch.setattr(writer, "append_explore_result_events", unavailable)
        rc = main(["--registry", str(path), "--runtime-root", str(runtime), "--format", "json", *command])
        failed = json.loads(capsys.readouterr().out)
    assert rc == 1 and failed["appended"] and failed["explore_result_delivery"]["primary_committed"]
    config = json.loads(path.read_text())
    config["goals"][0]["execution_profile"]["yardstick_enabled"] = False
    path.write_text(json.dumps(config))
    recovered = run(project, runtime, path, *command)
    assert recovered["idempotent_replay"] and recovered["explore_result_delivery"]["ok"]
    assert len(load_explore_result_events(explore_result_log_path(runtime, GOAL_ID))) == 2
    assert "work_yardsticks" not in context(runtime, path)["graph"]
