"""Strict Codex results retain evidence obligations from the complete source."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver.codex_cli import normalize_codex_result, run_codex_cli_host
from loopx.control_plane.turn_driver.codex_operation_host import run_codex_operation_host
from loopx.control_plane.turn_driver.host_candidate import complete_host_input
from loopx.control_plane.turn_driver.host_failure import BuiltInHostError
from tests.control_plane.test_turn_host_decision_capture import (
    complete_host_request as complete_host_request,
)


@pytest.fixture(params=["executor", "wire"])
def codex_request(complete_host_request, request):
    internal, source = complete_host_request
    assert isinstance(source["execution_profile"], dict) and source["execution_profile"]
    value = deepcopy(internal) if request.param == "executor" else complete_host_input(internal)
    assert isinstance(value, dict) and value["work_contract"] == source
    assert ("turn_envelope" in value) is (request.param == "executor")
    return value, source


def _result(turn_key, kind="validated_progress"):
    material = kind in {"validated_progress", "repair_required", "replan_required"}
    return {
        "schema_version": "loopx_turn_result_v0", "turn_key": turn_key,
        "result_kind": kind, "completed_phases": ["host_execute", "typed_result"],
        "classification": "synthetic_complete_result", "recommended_action": "Continue the fixture",
        "next_action": "Inspect the fixture result", "summary": "One public fixture result",
        "delivery_batch_scale": "implementation" if material else "",
        "delivery_outcome": "outcome_progress" if material else "",
        "vision_unchanged_reason": "The fixture objective remains unchanged." if material else "",
        "path_delta_mode": "", "agent_vision_json": "", "progress_observation_json": "",
        "work_evidence": ["artifact:fixture"] if material else None,
        "validation_evidence": ["check:fixture"] if material else None,
    }


@pytest.mark.parametrize("kind", ["validated_progress", "repair_required", "replan_required"])
@pytest.mark.parametrize("evidence", ["null", "omitted"])
def test_complete_material_result_cannot_lose_its_evidence_obligation(codex_request, kind, evidence):
    request, _source = codex_request
    original = deepcopy(request)
    result = _result(request["turn_key"], kind)
    for field in ("work_evidence", "validation_evidence"):
        if evidence == "null":
            result[field] = None
        else:
            result.pop(field)
    with pytest.raises(BuiltInHostError, match="codex_material_result_missing_evidence"):
        normalize_codex_result(request, result)
    assert request == original


@pytest.mark.parametrize("fault", ["null", "changed", "future"])
def test_material_result_refuses_a_bad_supplied_complete_source(codex_request, fault):
    request, _source = codex_request
    damaged = deepcopy(request)
    if fault == "null":
        damaged["work_contract"] = None
    elif fault == "future":
        damaged["work_contract"]["schema_version"] = "unsupported_complete_source"
    else:
        damaged["work_contract"]["reason"] = "changed after launch"
    before = deepcopy(damaged)
    with pytest.raises((ValueError, RuntimeError)):
        normalize_codex_result(damaged, _result(request["turn_key"]))
    assert damaged == before


@pytest.mark.parametrize("kind", ["validated_progress", "wait", "user_action_required", "iteration_failed"])
def test_complete_result_preserves_material_evidence_and_stop_absence(codex_request, kind):
    request, _source = codex_request
    original = deepcopy(request)
    result = _result(request["turn_key"], kind)
    normalized = normalize_codex_result(request, result)
    if kind == "validated_progress":
        assert normalized == result
    else:
        assert normalized == {k: v for k, v in result.items()
                              if k not in {"work_evidence", "validation_evidence"}}
    assert request == original


def test_non_null_evidence_content_stays_with_the_executor_validator(codex_request):
    request, _source = codex_request
    result = _result(request["turn_key"])
    result.update(work_evidence=[], validation_evidence=[])
    assert normalize_codex_result(request, result) == result


@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("kind", ["validated_progress", "wait"])
def test_historical_result_normalization_keeps_its_original_profile_rule(profile, kind):
    request = {"schema_version": "loopx_turn_host_request_v0", "turn_envelope": {
        "contract_capsule": {"execution_profile": {"minimum_scale": "implementation"}} if profile else {}}}
    result = _result("sha256:" + "a" * 64, kind)
    result.update(work_evidence=None, validation_evidence=None)
    if profile and kind == "validated_progress":
        with pytest.raises(BuiltInHostError, match="codex_material_result_missing_evidence"):
            normalize_codex_result(request, result)
    elif kind == "wait":
        assert "work_evidence" not in normalize_codex_result(request, result)
    else:
        assert normalize_codex_result(request, result) == result


@pytest.mark.parametrize("host", ["cli", "operation"])
@pytest.mark.parametrize("outcome", ["material", "missing_evidence", "stop"])
def test_actual_codex_boundaries_apply_the_complete_result_rule(
    codex_request, tmp_path, monkeypatch, host, outcome,
):
    from test_loopx_turn_codex_cli import _fake_codex
    from test_codex_operation_host import FAKE_SERVER

    request, source = codex_request
    result = _result(request["turn_key"], "wait" if outcome == "stop" else "validated_progress")
    if outcome == "missing_evidence":
        result.update(work_evidence=None, validation_evidence=None)
    if host == "cli":
        executable, log = _fake_codex(tmp_path)
        monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
        monkeypatch.setenv("FAKE_CODEX_RESULT_FIELDS", json.dumps(result))
        run = run_codex_cli_host
    else:
        executable = tmp_path / "fake-complete-codex-operation"
        executable.write_text(FAKE_SERVER)
        executable.chmod(0o700)
        monkeypatch.setenv("FAKE_OPERATION_RESULT_FIELDS", json.dumps(result))
        run = run_codex_operation_host
    registry = Path(source["registry"])
    goal = next(g for g in json.loads(registry.read_text())["goals"]
                if g.get("id", g.get("goal_id")) == source["goal_id"])
    original = deepcopy(request)
    options = dict(runtime_root=Path(source["runtime_root"]), registry_path=registry,
                   project=Path(goal["repo"]), codex_bin=str(executable),
                   model="test-model", reasoning_effort="xhigh", timeout_seconds=5)
    if outcome == "missing_evidence":
        with pytest.raises(BuiltInHostError, match="codex_material_result_missing_evidence"):
            run(request, **options)
    else:
        observed = run(request, **options)
        assert observed["turn_key"] == request["turn_key"]
        assert observed["result_kind"] == result["result_kind"]
        if outcome == "stop":
            assert "work_evidence" not in observed and "validation_evidence" not in observed
        else:
            assert observed["work_evidence"] == result["work_evidence"]
            assert observed["validation_evidence"] == result["validation_evidence"]
    assert request == original
