"""A staged review follows exactly the acceptance reference emitted by the quota writer."""
from copy import deepcopy
import hashlib
import json
import shlex
import subprocess
import sys

import pytest
from test_quota_acceptance_references import packet
from loopx.control_plane.quota.cli_projection import compact_quota_should_run_cli_payload
from loopx.control_plane.quota.turn_envelope import build_turn_envelope


def run(argv):
    result = subprocess.run(argv, capture_output=True, text=True)
    return result.returncode, json.loads(result.stdout)


def staged_capture(tmp_path):
    original = packet(with_context=False)
    core = original["planning_horizon"]["core_goal"]
    outcome = {"id": "goal", "revision": 7, "requirements": [{"id": "recover", "text": "Recover before retry."}]}
    core["acceptance_contract"]["outcome_requirements"] = {"outcomes": [outcome]}
    original["autonomous_replan_obligation"]["replan_context"]["core_goal"] = deepcopy(core)
    source = compact_quota_should_run_cli_payload(original)
    ref = source["autonomous_replan_obligation"]["replan_context"]["core_goal"]["acceptance_contract_ref"]
    assert ref == "$.planning_horizon.core_goal.acceptance_contract"
    return source, tmp_path / "decision.json", outcome


def read_review(source, capture):
    capture.write_text(json.dumps(source))
    digest = build_turn_envelope(source)["action_signature"]["source_decision_hash"]
    return run([sys.executable, "-m", "loopx.entrypoint", "--format", "json", "quota", "replan-view",
                "--decision-file", str(capture), "--source-hash", digest, "--replan-stage", "review"])


def test_staged_review_reads_the_planning_owner_advertised_by_the_quota_writer(tmp_path):
    source, capture, outcome = staged_capture(tmp_path)
    code, review = read_review(source, capture)
    assert code == 0, review
    index = review["acceptance_index"]
    entry, = [r for r in index["outcomes"] if r["id"] == outcome["id"]]
    argv = shlex.split(index["read_action_template"].replace("{outcome_ref}", entry["outcome_ref"]))
    code, detail = run(argv)
    assert code == 0 and detail["outcome"] == outcome, detail
    assert json.loads(capture.read_bytes()) == source


@pytest.mark.parametrize("fault", ["missing", "chain", "literal-and-reference", "unregistered"])
def test_staged_review_rejects_invalid_capture_reference(tmp_path, fault):
    source, capture, _ = staged_capture(tmp_path)
    target = source["planning_horizon"]["core_goal"]
    ref = source["autonomous_replan_obligation"]["replan_context"]["core_goal"]
    if fault in ("missing", "chain"):
        del target["acceptance_contract"]
    if fault in ("chain", "literal-and-reference"):
        target["acceptance_contract_ref"] = "$.agent_todo_summary.goal_acceptance_contract"
    if fault == "unregistered":
        ref["acceptance_contract_ref"] = "$.arbitrary.acceptance_contract"
    # A signature over these exact contents isolates the read from any hash failure.
    # The build path may also reject invalid planning facts, so use native JSON's
    # number rendering when independently computing the synthetic source hash.
    data = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    canonical = subprocess.check_output(["node", "-p", "JSON.stringify(JSON.parse(require('fs').readFileSync(0,'utf8')))"],
                                        input=data).rstrip(b"\n")
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    capture.write_bytes(data)
    code, error = run([sys.executable, "-m", "loopx.entrypoint", "--format", "json", "quota", "replan-view",
                       "--decision-file", str(capture), "--source-hash", digest, "--replan-stage", "review"])
    assert code != 0 and "Goal acceptance reference" in error["reason"], error
    assert capture.read_bytes() == data

def test_acceptance_sharing_keeps_unrelated_facts_outside_the_projection_request(monkeypatch):
    from loopx.control_plane.quota import turn_envelope

    source = packet(with_context=False)
    extra = {"negative_evidence": "Retain the full original observation. " * 128,
             "exact_integer": 2**60 + 1}
    source["future_observation"] = deepcopy(extra)
    calls = []
    evaluate = turn_envelope.effect_runtime_projection_result

    def observe(method, params, **kwargs):
        if params["operation"] == "reference_context":
            calls.append(params["payload"])
        return evaluate(method, params, **kwargs)

    monkeypatch.setattr(turn_envelope, "effect_runtime_projection_result", observe)
    result = compact_quota_should_run_cli_payload(source)
    assert len(calls) == 1 and "future_observation" not in calls[0]
    assert result["future_observation"] == source["future_observation"] == extra
    assert result["agent_todo_summary"]["goal_acceptance_contract_ref"] == "$.planning_horizon.core_goal.acceptance_contract"


def test_a_single_inline_owner_stays_local_when_todo_display_still_runs(monkeypatch):
    from loopx.control_plane.quota import turn_envelope

    source = compact_quota_should_run_cli_payload(packet(with_context=True))
    captured_requests = []
    evaluate = turn_envelope.effect_runtime_projection_result

    def observe(method, params, **kwargs):
        if params["operation"] == "reference_context":
            captured_requests.append(params)
        return evaluate(method, params, **kwargs)

    monkeypatch.setattr(turn_envelope, "effect_runtime_projection_result", observe)
    displayed = compact_quota_should_run_cli_payload(source)
    assert displayed == source and len(captured_requests) == 1
    request = captured_requests[0]
    assert request["goal_acceptance"] is False
    original_rows = source["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    wire_rows = request["payload"]["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    index = next(i for i, row in enumerate(original_rows) if row.get("source") == "goal_acceptance")
    assert original_rows[index]["content"]["goal_acceptance_contract"]
    assert wire_rows[index] is None
