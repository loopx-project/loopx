"""Real Explore capture, strict read, CLI and next-turn score feedback."""
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from tests.control_plane.test_explore_result_writeback import fixture, payload
from loopx.capabilities.explore.result_log import (
    explore_history_rows,
    explore_result_log_path,
    load_explore_result_events_strict,
)
from loopx.capabilities.explore.result_writeback import deliver_result_attachment, prepare_result_attachment
from loopx.capabilities.explore.turn_context import explore_turn_context
from loopx.control_plane.effect_runtime import EffectRuntimeRejected


def measurement(candidate, value, **changes):
    return dict(metric="quality", value=value, unit="points", higher_is_better=True,
                comparison_scope="validation:fixed-budget-v1", input_revision="fixture-v1",
                evaluation_ref="evaluator:v1", candidate_ref=f"candidate:{candidate}",
                evidence_ref="validation:counterexample-1", **changes)


def context(args):
    return explore_turn_context(**{key: args[key] for key in
        ("registry_path", "runtime_root", "goal_id", "agent_id")})


def test_ordinary_capture_keeps_best_candidate_after_regression_and_pages(tmp_path):
    args = fixture(tmp_path)
    receipts = []
    for index, score in enumerate([8, 9, 6, None], start=1):
        current = {**args, "turn_instance_id": f"turn-{index}"}
        result = payload()
        result["settlement_identity"]["effect_id"] = f"research:worker:turn-{index}"
        result["generated_at"] = f"2026-01-0{index}T00:00:00Z"
        result["explore_result"]["status"] = "tentative"
        result["explore_result"]["measurements"] = [measurement(str(index), score)]
        prepare_result_attachment(result["explore_result"], **current)
        receipt = deliver_result_attachment(payload=result, **current)
        assert receipt["ok"], receipt
        receipts.append(receipt)
        repeated = deliver_result_attachment(payload=result, **current)
        assert repeated["ok"] and repeated["idempotent_replay"]
        feedback = context(args)["graph"]["measurement_feedback"][0]
        assert feedback["latest"]["measurement"]["value"] == score
        if index == 3:
            assert feedback["improvement"] == -3
            assert feedback["best"]["candidate_ref"] == "candidate:2"
        if index == 4:
            assert feedback["improvement"] is None and feedback["unknown_count"] == 1
    assert feedback["best"]["value"] == 9
    assert feedback["best"]["finding_id"] == receipts[1]["finding_id"]
    assert feedback["best"]["evidence_ref"] == "validation:counterexample-1"
    assert len(context(args)["graph"]["writeback_results"]) == 3
    log = explore_result_log_path(args["runtime_root"], "research")
    events = load_explore_result_events_strict(log, goal_id="research")
    rows = [row for row in explore_history_rows(events, goal_id="research") if "measurements" in row]
    assert [row["measurements"][0]["value"] for row in rows] == [8, 9, 6, None]
    assert all(row["status"] == "tentative" for row in rows)
    # The history best must survive even when the visible page contains only the latest result.
    page = explore_turn_context(**{key: args[key] for key in
        ("registry_path", "runtime_root", "goal_id", "agent_id")}, result_limit=1)
    assert page["graph"]["measurement_feedback"][0]["best"]["value"] == 9


@pytest.mark.parametrize("field,value", [("input_revision", "foreign-v2"),
    ("evidence_ref", "validation:unrelated"), ("value", "9"), ("value", True)])
def test_invalid_measurement_preflight_cannot_append_partial_results(tmp_path, field, value):
    args = fixture(tmp_path)
    attachment = deepcopy(payload()["explore_result"])
    item = measurement("invalid", 9)
    item[field] = value
    attachment["measurements"] = [item]
    with pytest.raises((ValueError, EffectRuntimeRejected)):
        prepare_result_attachment(attachment, **args)
    assert not explore_result_log_path(args["runtime_root"], "research").exists()


def test_cli_capture_readback_and_malformed_history_recovery(tmp_path):
    args = fixture(tmp_path)
    document = tmp_path / "measurement.json"
    document.write_text(json.dumps([measurement("cli", 0)]))
    command = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry",
        str(args["registry_path"]), "--runtime-root", str(args["runtime_root"]), "explore", "finding",
        "--goal-id", "research", "--agent-id", "worker", "--node", "prefix-bound",
        "--title", "Measured candidate", "--finding-id", "cli-score", "--status", "tentative",
        "--evidence-ref", "validation:counterexample-1", "--measurements-json", str(document)]
    captured = subprocess.run(command, capture_output=True, text=True)
    assert captured.returncode == 0, captured.stdout + captured.stderr
    assert context(args)["graph"]["measurement_feedback"][0]["best"]["value"] == 0
    log = explore_result_log_path(args["runtime_root"], "research")
    healthy = log.read_bytes()
    row = json.loads(healthy)
    row["measurements"][0]["value"] = "invented"
    log.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="invalid Explore result"):
        context(args)
    log.write_bytes(healthy)
    assert context(args)["graph"]["measurement_feedback"][0]["best"]["value"] == 0
    document.write_text(json.dumps([{**measurement("invalid", 9), "evidence_ref": "validation:unrelated"}]))
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert log.read_bytes() == healthy


def test_disabled_explore_does_not_offer_score_feedback(tmp_path):
    args = fixture(tmp_path, enabled=False)
    assert context(args)["graph"] is None


@pytest.mark.parametrize("clock", ["tied", "backfilled"])
def test_recorded_measurement_order_is_independent_of_source_clock(tmp_path, clock):
    args = fixture(tmp_path)
    for index, score in enumerate([8, 9, 6], start=1):
        current = {**args, "turn_instance_id": f"turn-{index}"}
        result = payload()
        result["settlement_identity"]["effect_id"] = f"research:worker:turn-{index}"
        result["generated_at"] = ("2026-01-01T00:00:00Z" if clock == "tied"
                                  else f"2026-01-0{4-index}T00:00:00Z")
        result["explore_result"]["measurements"] = [measurement(str(index), score)]
        prepare_result_attachment(result["explore_result"], **current)
        assert deliver_result_attachment(payload=result, **current)["ok"]
    feedback = context(args)["graph"]["measurement_feedback"][0]
    assert feedback["latest"]["measurement"]["value"] == 6
    assert feedback["best"]["value"] == 9
    assert feedback["improvement"] == -3
    # The ordering ordinal is derived from the source log, not another stored fact.
    log = explore_result_log_path(args["runtime_root"], "research")
    assert all("recorded_order" not in json.loads(line) for line in log.read_text().splitlines())
