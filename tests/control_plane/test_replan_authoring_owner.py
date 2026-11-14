"""One captured authoring owner survives display sharing and executable reads."""
from copy import deepcopy
import hashlib
import json
import subprocess
import sys

import pytest

from loopx.control_plane.quota.cli_projection import _reference_context
from loopx.control_plane.quota.turn_envelope import (
    _turn_envelope_result, build_turn_envelope, prepare_context_delivery, restore_context_delivery,
)
from test_replan_host_context_projection import _quota_payload


def packet():
    source = _quota_payload()
    authoring = {
        "schema_version": "goal_vision_replan_contract_v0",
        "rules": ["Retain the original requirements and negative evidence. " * 12,
                  "Publication and spending still require their original authority."],
        "provenance": {"ref": "fixture:original", "revision": 3},
        "required": False,
        "minimal_example": {"state": "vision_patch_proposed"},
    }
    source["replan_action_packet"]["writeback_contract"]["vision_authoring"] = deepcopy(authoring)
    source["interaction_contract"]["cli_channel"]["settlement_plan"] = {
        "identity": {"turn_instance_id": "original-turn"},
        "ordered_steps": [{"kind": "validation"},
                          {"kind": "durable_writeback", "vision_authoring": authoring}],
    }
    return source, authoring


def shared_packet(source):
    return _reference_context(source, task_title=False, explore_guidance=False, replan_authoring=True)


def test_cli_compaction_shares_authoring_in_one_runtime_call(monkeypatch):
    from loopx.control_plane.quota import turn_envelope
    from loopx.control_plane.quota.cli_projection import compact_quota_should_run_cli_payload

    source, authoring = packet()
    original = deepcopy(source)
    calls = []
    invoke = turn_envelope.effect_runtime_projection_result

    def observed(name, request):
        calls.append((name, request))
        return invoke(name, request)

    monkeypatch.setattr(turn_envelope, "effect_runtime_projection_result", observed)
    compact = compact_quota_should_run_cli_payload(source)
    projection_calls = len(calls)
    assert source == original
    assert build_turn_envelope(compact)["replan_action_packet"]["writeback_contract"]["vision_authoring"] == authoring
    assert projection_calls == 1


def test_explicit_vision_detail_keeps_authoring_inline():
    from loopx.control_plane.quota.cli_projection import compact_quota_should_run_cli_payload

    source, authoring = packet()
    compact = compact_quota_should_run_cli_payload(source, include_vision_detail=True)
    writeback = compact["replan_action_packet"]["writeback_contract"]
    assert writeback["vision_authoring"] == authoring
    assert "vision_authoring_ref" not in writeback


def source_digest(source):
    if source.get("schema_version") == "loopx_turn_envelope_v1":
        return source["contract_binding"]["content_hash"]
    # The fixture's keys use Python's declared Unicode ordering; the existing
    # source binding uses JavaScript JSON number rendering (including 1.0).
    raw = subprocess.check_output(["node", "-e",
        "process.stdout.write(JSON.stringify(JSON.parse(require('node:fs').readFileSync(0,'utf8'))))"],
        input=json.dumps(source, sort_keys=True, ensure_ascii=False).encode())
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def cli_writer(directory, source, digest):
    path = directory / "captured-authoring.json"
    path.write_text(json.dumps(source))
    return subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--format", "json",
        "quota", "replan-view", "--decision-file", str(path), "--source-hash", digest,
        "--replan-stage", "writeback"], cwd=directory, capture_output=True, text=True)


@pytest.mark.parametrize("reader", ["envelope", "staged", "cli"])
def test_ordinary_and_captured_readers_resolve_the_same_complete_authoring(tmp_path, reader):
    source, authoring = packet()
    original = deepcopy(source)
    shared = shared_packet(source)
    assert source == original
    assert shared_packet(shared) == shared
    assert "vision_authoring" not in shared["replan_action_packet"]["writeback_contract"]
    envelope = build_turn_envelope(shared)
    if reader == "envelope":
        actual = envelope["replan_action_packet"]["writeback_contract"]["vision_authoring"]
    else:
        digest = envelope["action_signature"]["source_decision_hash"]
        if reader == "staged":
            result = _turn_envelope_result("replan_view", payload=shared, source_hash=digest, stage="writeback")
        else:
            run = cli_writer(tmp_path, shared, digest)
            assert run.returncode == 0, run.stdout + run.stderr
            result = json.loads(run.stdout)
        actual = result["writeback_contract"]["vision_authoring"]
        authoring.pop("minimal_example")  # Existing staged authoring omits only this example.
    assert actual == authoring


@pytest.mark.parametrize("mutation", ["dangling", "wrong-step", "chain", "both", "malformed"])
@pytest.mark.parametrize("captured", [False, True])
def test_invalid_authoring_reference_is_refused_by_every_reader(tmp_path, mutation, captured):
    source, _ = packet()
    source = shared_packet(source)
    if captured:
        source["replan_read_path"] = "/synthetic/captured.json"
    writeback = source["replan_action_packet"]["writeback_contract"]
    owner = source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]
    if mutation == "dangling":
        writeback["vision_authoring_ref"] = writeback["vision_authoring_ref"].replace("[1]", "[99]")
    elif mutation == "wrong-step":
        owner["kind"] = "validation"
    elif mutation == "chain":
        owner.pop("vision_authoring")
        owner["vision_authoring_ref"] = "$.replan_action_packet.writeback_contract.vision_authoring"
    elif mutation == "both":
        writeback["vision_authoring"] = {"rules": ["Conflicting obligation"]}
    else:
        writeback["vision_authoring_ref"] = {"path": "$.unknown"}
    # The composition operation validates the reference before creating an
    # envelope; the raw source hash is not a substitute for that validation.
    with pytest.raises(RuntimeError, match="authoring reference"):
        build_turn_envelope(source)
    with pytest.raises(RuntimeError, match="authoring reference"):
        shared_packet(source)
    # A matching hash for the altered source must not conceal a bad reference.
    digest = source_digest(source)
    for stage in ("review", "writeback"):
        with pytest.raises(RuntimeError, match="authoring reference"):
            _turn_envelope_result("replan_view", payload=source, source_hash=digest, stage=stage)
    run = cli_writer(tmp_path, source, digest)
    assert run.returncode != 0
    assert "authoring reference" in run.stdout + run.stderr


@pytest.mark.parametrize("left,right", [(1, 1.0), (False, 0), (-0.0, 0),
                                       (2**60, 2**60 + 1), (2**60, float(2**60))])
def test_exact_numeric_facts_choose_the_matching_later_owner(left, right):
    source, _ = packet()
    writeback = source["replan_action_packet"]["writeback_contract"]
    writeback["vision_authoring"]["future_numeric_fact"] = right
    steps = source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"]
    steps[1]["vision_authoring"]["future_numeric_fact"] = left
    steps.append({"kind": "durable_writeback", "vision_authoring": deepcopy(writeback["vision_authoring"])})
    before = json.dumps(source)
    shared = shared_packet(source)
    assert json.dumps(source) == before
    assert shared["replan_action_packet"]["writeback_contract"]["vision_authoring_ref"].endswith("[2].vision_authoring")
    assert json.dumps(shared["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"]) == json.dumps(steps)


def test_small_or_different_authoring_stays_inline():
    source, _ = packet()
    writeback = source["replan_action_packet"]["writeback_contract"]
    owner = source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]
    owner["vision_authoring"]["rules"].append("A distinct cancellation condition.")
    assert shared_packet(source)["replan_action_packet"] == source["replan_action_packet"]
    writeback["vision_authoring"] = owner["vision_authoring"] = {"required": True}
    assert shared_packet(source)["replan_action_packet"] == source["replan_action_packet"]


def test_full_delta_and_missing_baseline_keep_the_exact_authoring():
    source, authoring = packet()
    before = shared_packet(source)
    source["replan_action_packet"]["uncovered_frontier"] = ["New evidence to assess"]
    after = shared_packet(source)
    scope = dict(goal_id="fixture", agent_id="worker", session_id="session", context_epoch="one")
    baseline = prepare_context_delivery(before, scope=scope, capture_id="one", surface="quota_packet")
    full = prepare_context_delivery(after, scope=scope, capture_id="two", surface="quota_packet")
    delta = prepare_context_delivery(after, scope=scope, capture_id="two", surface="quota_packet",
        baseline=baseline, retained={"complete": True, "target": baseline["target"]})
    assert delta["mode"] == "delta"
    for retained in (baseline, None):
        restored = restore_context_delivery(delta, baseline=retained, current=full)["context"]
        assert restored == after
        assert build_turn_envelope(restored)["replan_action_packet"]["writeback_contract"]["vision_authoring"] == authoring


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_capture_authoring_read_does_not_repeat_work_or_spend(tmp_path, monkeypatch, provider):
    from test_turn_replan_writeback import _fixture
    from test_quota_settlement_cli import _run_cli, _spend_run_count, GOAL_ID, AGENT_ID, SELECTED_REPLAN_TODO_ID

    project, runtime, registry, state, turn = _fixture(tmp_path, monkeypatch, provider)
    capture = tmp_path / "decision"
    code, result = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--context-delivery", "off", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn,
        "--decision-output-dir", str(capture), cwd=project)
    assert code == 0, result
    source = json.loads((capture / "decision.json").read_text())
    steps = source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"]
    authoring = deepcopy(next(step["vision_authoring"] for step in steps
        if step["kind"] == "durable_writeback" and "vision_authoring" in step))
    authoring.pop("minimal_example", None)
    shared = shared_packet(source)
    before = state.read_bytes()
    run = cli_writer(project, shared, source_digest(shared))
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout)["writeback_contract"]["vision_authoring"] == authoring
    assert state.read_bytes() == before
    assert _spend_run_count(runtime) == 0
