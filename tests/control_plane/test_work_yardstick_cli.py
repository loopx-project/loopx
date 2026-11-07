"""Same-work journey: evidence → ordinary Turn → real action → readback.

The deterministic actor proves transport/adoption attribution, not model quality
or a causal benchmark gain. No evaluator, credentials or active Goal are used.
"""
import hashlib
from itertools import groupby
import json
import shlex

import pytest

from benchmark.runtime.context_delivery import preflight_context_instruction
from tests.control_plane.test_quota_settlement_cli import (
    _heartbeat_receipt_count, _initialize_fixture_authority, _projected_cli_args,
    _spend_run_count, _write_fixture, _run_cli, GOAL_ID, AGENT_ID, TODO_ID,
)
from loopx.capabilities.explore.result_log import explore_result_log_path, load_explore_result_events
from loopx.capabilities.explore.turn_context import explore_turn_context
from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference
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


def setup(tmp_path, enabled=True, *, active=False, provider="sqlite", write_scope=None):
    project, runtime, path = _write_fixture(tmp_path)
    config = json.loads(path.read_text())
    if active:
        config["goals"][0]["status"] = "active"
        state = project / config["goals"][0]["state_file"]
        state.write_text(state.read_text().replace("status: active-read-only", "status: active", 1))
    config["goals"][0]["explore_graph"] = {"enabled": True}
    config["goals"][0]["quota"]["allowed_slots"] = 4
    config["goals"][0].setdefault("execution_profile", {})["yardstick_enabled"] = enabled
    if write_scope is not None:
        config["goals"][0]["coordination"]["write_scope"] = write_scope
    path.write_text(json.dumps(config))
    _initialize_fixture_authority(project, runtime, provider=provider)
    return project, runtime, path


def run(project, runtime, path, *command):
    rc, packet = _run_cli(path, runtime, *command, cwd=project)
    assert rc == 0, json.dumps({key: packet.get(key) for key in ("error", "reason", "classification", "decision", "admission", "quota")}, ensure_ascii=False, indent=2)
    return packet


def binding(turn):
    return ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", turn]


def refresh(project, runtime, path, turn, source, *options):
    run(project, runtime, path, "quota", "should-run", "--codex-app", *binding(turn))
    return run(project, runtime, path, "refresh-state", *binding(turn), "--classification", "validated_change",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--explore-result-json", str(source), "--no-global-sync", "--suppress-external-sinks", *options)


def context(runtime, path, *, detail=True):
    params = dict(registry_path=path, runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID)
    compact = explore_turn_context(**params)
    cues = compact["graph"].get("work_yardsticks", [])
    if detail and cues:
        return explore_turn_context(**params, result_node=cues[0]["node_id"])
    return compact


def starting_page(project, runtime, path, turn, saved, monkeypatch):
    data = saved.read_bytes()
    assert json.loads(data)["effective_action"] == "normal_run"
    native = saved.parent / "native"
    native.mkdir(mode=0o700)
    env = {"LOOPX_CLI": "loopx", "LOOPX_GOAL_ID": GOAL_ID, "LOOPX_AGENT_ID": AGENT_ID,
           "LOOPX_CODEX_CONTEXT_DIR": str(native)}
    prompt = preflight_context_instruction(env, saved, hashlib.sha256(data).hexdigest(), turn[-1])
    argv = shlex.split(prompt.split("```sh\n", 1)[1].split("\n```", 1)[0])
    with monkeypatch.context() as patch:
        patch.setenv("LOOPX_EXECUTION_MODE", "heartbeat")
        patch.setenv("LOOPX_CONTEXT_DELIVERY", "on")
        page = run(project, runtime, path, *argv[argv.index("quota"):])
    assert saved.read_bytes() == data
    return page


@pytest.mark.parametrize("enabled", [False, True])
def test_yardstick_cue_reaches_the_starting_read_before_candidate_selection(tmp_path, enabled, monkeypatch):
    project, runtime, path = setup(tmp_path, enabled, active=True)
    turn = binding("yardstick-before-choice")
    capture = tmp_path / "capture"
    guard = run(project, runtime, path, "quota", "should-run", "--codex-app", *turn,
                "--decision-output-dir", str(capture), "--context-delivery", "on", "--delta-delivery", "off")
    assert guard["schema_version"] == "context_capture_navigation_v1"
    opening = starting_page(project, runtime, path, turn, capture / "decision.json", monkeypatch)["content"]
    # The independently readable page carries the decision cue. The complete
    # attachment template stays available on the existing Explore reader.
    cue_path = "/content/graph/work_yardstick_capture/guidance"
    assert (cue_path in opening) is enabled
    assert "/content/graph/work_yardstick_capture/template" not in opening
    graph = context(runtime, path, detail=False)["graph"]
    if enabled:
        assert "use one bounded accepted-outcome comparison" in opening
        assert graph["work_yardstick_capture"]["optional"] is True
        assert graph["work_yardstick_capture"]["template"]["proxy"] is None
    else:
        assert "work_yardstick_capture" not in graph


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_yardstick_opening_keeps_the_current_cue_with_a_multi_page_write_scope(tmp_path, provider, monkeypatch):
    scope = [
        f"modules/component-{i:03d}/experiments/artifact-encodings/public-validation/"
        "accepted-input-compatibility/fixture-baseline/ordered-results/recorded-comparison/**"
        for i in range(100)
    ]
    project, runtime, path = setup(tmp_path, active=True, provider=provider, write_scope=scope)
    source = tmp_path / "finding.json"
    source.write_text(json.dumps(attachment()))
    receipt = refresh(project, runtime, path, "prior-finding", source, "--delivery-boundary", "in_flight_continuation")
    assert receipt["explore_result_delivery"]["ok"]
    run(project, runtime, path, *_projected_cli_args(receipt["settlement_owed"]["command"], turn_instance_id="prior-finding"))

    turn = binding("wide-goal-before-choice")
    capture = tmp_path / "capture"
    run(project, runtime, path, "quota", "should-run", "--codex-app", *turn,
        "--decision-output-dir", str(capture), "--context-delivery", "on", "--delta-delivery", "off")
    saved = capture / "decision.json"
    original = saved.read_bytes()
    opening = starting_page(project, runtime, path, turn, saved, monkeypatch)
    assert opening["offset"] == 0 and not opening["eof"]
    assert len(json.dumps(opening, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 12000
    content = opening["content"]
    graph = context(runtime, path, detail=False)["graph"]
    guidance_at = content.index(',"value":', content.index('/graph/work_yardstick_capture/guidance"'))
    cue_at = content.index(',"value":', content.index('/graph/work_yardsticks/0"'))
    guidance, _ = json.JSONDecoder().raw_decode(content, guidance_at + len(',"value":'))
    cue, _ = json.JSONDecoder().raw_decode(content, cue_at + len(',"value":'))
    assert guidance == graph["work_yardstick_capture"]["guidance"]
    assert cue == graph["work_yardsticks"][0] and cue["todo_id"] == TODO_ID

    _, reference = read_context_read_reference(opening["reference_file"])
    assert reference["sha256"] == hashlib.sha256(original).hexdigest()
    chunks = [content]
    offset = len(content.encode())
    page = opening
    while not page["eof"]:
        assert page["next_offset"] == offset
        words = shlex.split(page["next_command"])
        page = run(project, runtime, path, *words[words.index("quota"):])
        _, follow = read_context_read_reference(page["reference_file"])
        assert follow["path"] == reference["path"] and follow["sha256"] == reference["sha256"]
        assert page["offset"] == offset
        chunks.append(page["content"])
        offset += len(page["content"].encode())
    assert offset == page["total_bytes"]
    values = {row["pointer"]: row["value"] for row in json.loads("".join(chunks))["selections"]}
    assert values["/effective_action"] == "normal_run"
    assert values["/goal_boundary"] == json.loads(original)["goal_boundary"]
    assert values["/goal_boundary"]["write_scope"] == scope
    assert saved.read_bytes() == original
    assert _heartbeat_receipt_count(runtime, turn[-1]) == _spend_run_count(runtime) == 1


def test_measured_yardstick_restarts_comparison_when_the_acceptance_basis_changes(tmp_path):
    project, runtime, path = setup(tmp_path, active=True)
    source = tmp_path / "finding.json"

    def record(turn, acceptance, candidates, previous=None):
        finding = attachment()
        choice = finding["work_yardstick_decision"]
        choice["target"]["acceptance_ref"] = acceptance
        choice["counterevidence_refs"] = []
        choice["prior_finding_id"] = previous
        finding["measurements"] = [
            {**choice["authoritative"], "candidate_ref": f"candidate:{name}",
             "evidence_ref": f"result:{name}", "value": value}
            for name, value in candidates
        ]
        finding["evidence_refs"] = [measurement["evidence_ref"] for measurement in finding["measurements"]]
        source.write_text(json.dumps(finding))
        receipt = refresh(project, runtime, path, turn, source, "--delivery-boundary", "in_flight_continuation")
        assert receipt["explore_result_delivery"]["ok"], receipt
        run(project, runtime, path, *_projected_cli_args(receipt["settlement_owed"]["command"], turn_instance_id=turn))
        return receipt["explore_result_delivery"]["finding_id"]

    old = record("turn-old-basis", "acceptance:task-v1", [("old-a", 10), ("old-b", 20)])
    assert context(runtime, path)["graph"]["work_yardsticks"][0]["search_comparison"]["goal"][
        "preferred_candidate_ref"] == "candidate:old-b"
    first = record("turn-first-new-basis", "acceptance:task-v2", [("new-c", 6)], old)
    compact = context(runtime, path, detail=False)["graph"]["work_yardsticks"][0]
    assert compact["source_finding_id"] == first and "comparison_candidate_ref" not in compact
    assert "search_comparison" not in context(runtime, path)["graph"]["work_yardsticks"][0]
    latest = record("turn-second-new-basis", "acceptance:task-v2", [("new-d", 9)], first)
    cue = context(runtime, path)["graph"]["work_yardsticks"][0]
    comparison = cue["search_comparison"]["goal"]
    assert cue["target"]["acceptance_ref"] == "acceptance:task-v2"
    assert comparison["previous_best"]["finding_id"] == first and comparison["latest"]["finding_id"] == latest
    assert comparison["preferred_candidate_ref"] == "candidate:new-d"


def test_measured_yardstick_changes_next_work_from_local_screening_to_a_whole_goal_comparison(tmp_path, monkeypatch):
    project, runtime, path = setup(tmp_path, active=True)
    acceptance = tmp_path / "acceptance.json"
    acceptance.write_text(json.dumps(dict(outcome_id="encoded-artifact", owner_agent_id=AGENT_ID,
        objective="Reduce the encoded artifact size", todo_ids=[TODO_ID],
        requirements=[{"id": "round-trip", "description": "Decoding must preserve every original entry in order"}],
        source={"ref": "message:encoding-request", "summary": "Preserve the complete input"})))
    before = run(project, runtime, path, "goal-acceptance", "inspect", "--goal-id", GOAL_ID)
    run(project, runtime, path, "goal-acceptance", "refine", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--document", str(acceptance), "--operation-id", "declare-encoding", "--expected-provider-revision",
        before["provider_revision"], "--execute")
    run(project, runtime, path, "configure-goal", "--goal-id", GOAL_ID, "--execution-replan-after-turns", "2",
        "--execution-stride-strategy", "evidence_conditioned_v1", "--execute")
    payload = ["repeated-entry"] * 24 + ["other-entry"] * 24

    def encode(value):
        return json.dumps(value, separators=(",", ":")).encode()

    def dictionary_encode(items):
        keys = list(dict.fromkeys(items))
        return {"keys": keys, "indices": [keys.index(item) for item in items]}

    base = dictionary_encode(payload)
    trial = attachment()
    choice = trial["work_yardstick_decision"]
    choice["authoritative"].update(metric="size", unit="bytes", higher_is_better=False)
    choice["proxy"].update(metric="size", unit="bytes", higher_is_better=False, comparison_scope="validation:single-entry")
    choice["counterevidence_refs"] = []
    choice["next_action"] = dict(method_id="method:plain", reason="Try another plain encoding using the tiny screen",
                                 cost_bound="One artifact and one bounded end-to-end check")
    items = [(choice["authoritative"], payload, "goal"), (choice["proxy"], payload[:1], "screen")]
    trial["measurements"] = [
        {**profile, "candidate_ref": f"candidate:{name}", "evidence_ref": f"result:{source}-{name}",
         "value": len(encode(candidate))}
        for profile, data, source in items
        for name, candidate in [("dictionary", dictionary_encode(data)), ("plain", data)]
    ]
    assert trial["measurements"][0]["value"] < trial["measurements"][1]["value"]
    assert trial["measurements"][2]["value"] > trial["measurements"][3]["value"]
    trial["evidence_refs"] = [row["evidence_ref"] for row in trial["measurements"]]
    source = tmp_path / "measured-pair.json"
    source.write_text(json.dumps(trial))
    first = refresh(project, runtime, path, "turn-pair", source, "--delivery-boundary", "in_flight_continuation")
    assert first["explore_result_delivery"]["ok"]
    run(project, runtime, path, *_projected_cli_args(first["settlement_owed"]["command"], turn_instance_id="turn-pair"))

    compare_turn = binding("turn-compare")
    capture = tmp_path / "compare"
    guard = run(project, runtime, path, "quota", "should-run", "--codex-app", *compare_turn,
                "--decision-output-dir", str(capture))
    compact = run(project, runtime, path, "explore", "turn-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                  "--todo-id", TODO_ID)["graph"]["work_yardsticks"][0]
    assert compact["comparison_candidate_ref"] == "candidate:dictionary"
    assert compact["cue"] == choice["next_action"]["reason"]
    assert "different mechanism or a compatible combination" in compact["comparison_cue"]
    assert "full implementation and validation cost" in compact["comparison_cue"]
    assert compact["comparison_cue"] in json.dumps(guard)
    opening = starting_page(project, runtime, path, compare_turn, capture / "decision.json", monkeypatch)["content"]
    # A page may end in a later entry. Decode the complete cue before that
    # boundary without relying on any subsequent context read.
    cue_start = opening.index(',"value":', opening.index("/content/graph/work_yardsticks/"))
    delivered, _ = json.JSONDecoder().raw_decode(opening, cue_start + len(',"value":'))
    assert delivered["todo_id"] == TODO_ID
    assert delivered["comparison_candidate_ref"] == "candidate:dictionary"
    detailed = run(project, runtime, path, "explore", "turn-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                   "--todo-id", TODO_ID, "--result-node", compact["node_id"])["graph"]["work_yardsticks"][0]
    assert detailed["next_action"] == choice["next_action"]
    comparison = detailed["search_comparison"]
    assert comparison["goal"]["preferred_candidate_ref"] == "candidate:dictionary"
    assert comparison["proxy"]["preferred_candidate_ref"] == "candidate:plain"
    assert comparison["proxy_order_reversed"] is True

    # Deterministic consumer. Combine the evidenced baseline with an independent
    # representation change and verify the original end-to-end outcome.
    selected_base = {"candidate:dictionary": base}[delivered["comparison_candidate_ref"]]
    runs = [[index, len(list(group))] for index, group in groupby(selected_base["indices"])]
    artifact = project / "combined-encoding.json"
    artifact.write_bytes(encode({"keys": selected_base["keys"], "runs": runs}))
    result = json.loads(artifact.read_bytes())
    assert [result["keys"][i] for i, count in result["runs"] for _ in range(count)] == payload
    assert artifact.stat().st_size < len(encode(base))
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    follow = attachment()
    follow["evidence_refs"] = ["result:combined"]
    follow["work_yardstick_decision"] = {**choice, "prior_finding_id": detailed["source_finding_id"]}
    follow["execution_observation"] = dict(schema_version="explore_execution_observation_v0", candidate_sha256=digest,
        input_revision="inputs-v1", planned_method_id="method:plain", executed_method_id="method:dictionary-runs",
        change_reason="The matched outcome favored the dictionary; test a different representation on that base",
        elapsed_seconds=None, limit_seconds=None, search_iterations=1, stop_reason="complete",
        evidence_refs=["result:combined"], result_ref="result:combined", result_candidate_sha256=digest, exit_code=0)
    follow["measurements"] = [{**choice["authoritative"], "value": artifact.stat().st_size,
                               "candidate_ref": f"sha256:{digest}", "evidence_ref": "result:combined"}]
    source.write_text(json.dumps(follow))
    saved = run(project, runtime, path, "refresh-state", *binding("turn-compare"), "--classification", "validated_change",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--explore-result-json", str(source), "--no-global-sync", "--suppress-external-sinks")
    assert saved["explore_result_delivery"]["ok"]
    cue = context(runtime, path)["graph"]["work_yardsticks"][0]
    assert cue["previous_decision_followthrough"]["executed_method_id"] == "method:dictionary-runs"
    assert cue["search_comparison"]["goal"]["preferred_candidate_ref"] == f"sha256:{digest}"
    assert "proxy" not in cue["search_comparison"]  # The old proxy did not measure this new pair.
    run(project, runtime, path, *_projected_cli_args(saved["settlement_owed"]["command"], turn_instance_id="turn-compare"))
    review = run(project, runtime, path, "quota", "should-run", "--codex-app",
                 "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", "turn-review")
    assert "periodic_review_due" in {trigger["kind"] for trigger in review["autonomous_replan_obligation"]["triggers"]}
    assert cue["search_comparison"]["guidance"] in json.dumps(review)
    assert "comparison_candidate_ref" in json.dumps(review)
    before = context(runtime, path, detail=False)["graph"]["measurement_feedback"]
    run(project, runtime, path, "configure-goal", "--goal-id", GOAL_ID, "--no-execution-yardstick-enabled", "--execute")
    disabled = run(project, runtime, path, "explore", "turn-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                   "--todo-id", TODO_ID)["graph"]
    assert "work_yardsticks" not in disabled and disabled["measurement_feedback"] == before


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
    run(project, runtime, path, "quota", "spend-slot", *binding(turn), "--slots", "1", "--source", "heartbeat", "--execute")
    # Work continues while the old graph delivery is pending. Its later exact
    # replay must retain history without replacing this newer target.
    newer_source = tmp_path / "newer.json"
    newer = attachment()
    newer["work_yardstick_decision"]["target"]["summary"] = "Use the updated current target"
    newer_source.write_text(json.dumps(newer))
    latest = refresh(project, runtime, path, "turn-newer", newer_source)
    assert latest["explore_result_delivery"]["ok"]
    config = json.loads(path.read_text())
    config["goals"][0]["execution_profile"]["yardstick_enabled"] = False
    path.write_text(json.dumps(config))
    recovered = run(project, runtime, path, *command)
    assert recovered["idempotent_replay"] and recovered["explore_result_delivery"]["ok"]
    assert len(load_explore_result_events(explore_result_log_path(runtime, GOAL_ID))) == 3
    assert "work_yardsticks" not in context(runtime, path)["graph"]
    config["goals"][0]["execution_profile"]["yardstick_enabled"] = True
    path.write_text(json.dumps(config))
    cue = context(runtime, path)["graph"]["work_yardsticks"][0]
    assert cue["source_finding_id"] == latest["explore_result_delivery"]["finding_id"]
    assert cue["target"]["summary"] == "Use the updated current target"
    # Exact replay is not permission to substitute an older timestamp or a
    # different method into the canonical committed attachment.
    changed = attachment()
    changed["work_yardstick_decision"]["next_action"]["method_id"] = "method:substituted"
    source.write_text(json.dumps(changed))
    rc, rejected = _run_cli(path, runtime, *command, cwd=project)
    assert rc == 1 and not rejected.get("explore_result_delivery", {}).get("ok"), rejected


def test_predecessor_validation_and_append_cas_share_the_same_snapshot(tmp_path, monkeypatch):
    from tests.control_plane.test_explore_result_writeback import fixture, payload
    import loopx.capabilities.explore.result_writeback as writer
    args = fixture(tmp_path)
    config = json.loads(args["registry_path"].read_text())
    config["goals"][0].setdefault("execution_profile", {})["yardstick_enabled"] = True
    args["registry_path"].write_text(json.dumps(config))

    def capture(turn, when):
        value = payload()
        value["generated_at"] = when
        value["settlement_identity"]["effect_id"] = f"research:worker:{turn}"
        value["explore_result"].update(evidence_refs=["result:reversal"], work_yardstick_decision=decision())
        return value

    first = capture("turn-one", "2026-01-02T00:00:00Z")
    concurrent = capture("turn-two", "2026-01-01T00:00:00Z")
    prepare = writer.prepare_result_attachment
    inserted = False

    def insert_after_validation(*a, **kw):
        nonlocal inserted
        result = prepare(*a, **kw)
        if not inserted:
            inserted = True
            other = writer.deliver_result_attachment(payload=concurrent,
                **{**args, "turn_instance_id": "turn-two"})
            assert other["ok"], other
        return result

    with monkeypatch.context() as patch:
        patch.setattr(writer, "prepare_result_attachment", insert_after_validation)
        result = writer.deliver_result_attachment(payload=first, **args)
    assert not result["ok"] and result["primary_committed"] and result["retryable"], result
    assert "graph changed" in result["error"]
    events = load_explore_result_events(explore_result_log_path(args["runtime_root"], args["goal_id"]))
    assert len([row for row in events if row["event_kind"] == "finding"]) == 1
