from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.capabilities.explore.result_log import (
    append_explore_result_event,
    build_explore_result_projection,
    explore_result_log_path,
    load_explore_result_events_strict,
)
from loopx.capabilities.explore.source_history_reconcile import reconcile_explore_source_history
from loopx.control_plane.effect_runtime import effect_runtime_result


FIXTURE = Path(__file__).with_name("fixtures") / "explore_measurement_history_v0.jsonl"
GOAL = "research"
RESULT_REASON = "measurements must cite the executed candidate's result reference"
INPUT_REASON = "measurements must retain the executed candidate's input revision"


def _rehash(event):
    stable = {key: value for key, value in event.items() if key != "event_id"}
    event["event_id"] = hashlib.sha256(
        json.dumps(stable, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()[:16]
    return event


def _encoded(events):
    return "".join(json.dumps(event, sort_keys=True) + "\n" for event in events).encode()


def _history(tmp_path):
    # The existing fixture was written under the earlier canonical contract.
    node, mixed, corrected = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    valid = _rehash({**deepcopy(corrected), "result_id": "valid"})
    mixed = deepcopy(mixed)
    mixed["result_id"] = "mixed-result"
    mixed["measurements"][0]["value"] = 99
    _rehash(mixed)
    other_input = deepcopy(corrected)
    other_input["result_id"] = "mixed-input"
    other_input["measurements"][0].update(input_revision="dataset-other", value=500)
    _rehash(other_input)
    events = [node, valid, mixed, other_input]
    canonical = explore_result_log_path(tmp_path / "runtime", GOAL)
    candidate = explore_result_log_path(tmp_path / "candidate", GOAL)
    for path, rows in ((canonical, [node]), (candidate, events)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_encoded(rows))
    keys = {f"{GOAL}:nodes:question-a"} | {
        f"{GOAL}:findings:{event['result_id']}" for event in events[1:]
    }
    return canonical, candidate, events, keys


def _reconcile(canonical, candidate, registered, *, execute=False):
    return reconcile_explore_source_history(
        canonical_log_path=canonical, candidate_log_path=candidate,
        goal_id=GOAL, registered_result_keys=registered, execute=execute,
    )


@pytest.mark.parametrize("include_valid", [True, False])
def test_mixed_history_recovers_eligible_subset_and_reports_blocked_reasons(tmp_path, include_valid):
    canonical, candidate, events, registered = _history(tmp_path)
    if not include_valid:
        registered.remove(f"{GOAL}:findings:valid")
    before = canonical.read_bytes()
    original = candidate.read_bytes()

    preview = _reconcile(canonical, candidate, registered)
    assert canonical.read_bytes() == before
    assert preview["plan"]["selected_event_count"] == int(include_valid)
    assert preview["plan"]["blocked_event_count"] == 2
    assert preview["plan"]["blocked_reasons"] == {RESULT_REASON: 1, INPUT_REASON: 1}
    assert preview["classification"]["blocked_history_count"] == 2
    assert preview["classification"]["unresolved_orphan_count"] == 0
    assert preview["classification"]["remote_deletion_required"] is False
    assert preview["projection_reconciliation"]["remaining_registered_result_count"] == 2
    assert preview["status"] == ("would_reconcile_with_blocked_history" if include_valid else "blocked")

    executed = _reconcile(canonical, candidate, registered, execute=True)
    assert executed["plan"] == preview["plan"]
    assert executed["classification"] == preview["classification"]
    assert executed["status"] == ("reconciled_with_blocked_history" if include_valid else "blocked")
    assert executed["writeback"]["appended_event_count"] == int(include_valid)
    restored = load_explore_result_events_strict(canonical, goal_id=GOAL)
    assert restored == (events[:2] if include_valid else events[:1])
    assert canonical.read_bytes().startswith(before)
    assert candidate.read_bytes() == original
    # Only admitted measurements reach the real typed feedback owner.
    projection = build_explore_result_projection(restored, goal_id=GOAL)
    graph = effect_runtime_result("explore.turn_context", dict(
        goal_id=GOAL, agent_id="worker", route=["loopx"], graph_enabled=True,
        harness_gate={"enabled": True}, projection=projection, plan={},
    ))["graph"]
    feedback = graph.get("measurement_feedback", [])
    assert len(feedback) == int(include_valid)
    if include_valid:
        assert feedback[0]["best"]["value"] == 8
        assert feedback[0]["measured_count"] == 1

    recovered = canonical.read_bytes()
    repeated = _reconcile(canonical, candidate, registered, execute=True)
    assert repeated["status"] == "blocked"
    assert repeated["plan"]["selected_event_count"] == 0
    assert repeated["plan"]["blocked_reasons"] == preview["plan"]["blocked_reasons"]
    assert repeated["writeback"]["performed"] is False
    assert canonical.read_bytes() == recovered
    assert candidate.read_bytes() == original


def test_blocked_history_and_unrelated_remote_orphans_remain_distinct(tmp_path):
    canonical, candidate, _, registered = _history(tmp_path)
    registered.add(f"{GOAL}:findings:missing-source")
    preview = _reconcile(canonical, candidate, registered)
    assert preview["status"] == "would_reconcile_with_blocked_history_with_remote_orphans"
    assert preview["classification"]["blocked_history_count"] == 2
    assert preview["classification"]["unresolved_orphan_count"] == 1
    assert preview["classification"]["remote_deletion_required"] is True
    executed = _reconcile(canonical, candidate, registered, execute=True)
    assert executed["classification"] == preview["classification"]
    assert executed["writeback"]["appended_event_count"] == 1


def test_corrected_latest_history_recovers_without_rewriting_older_evidence(tmp_path):
    canonical, candidate, events, registered = _history(tmp_path)
    _reconcile(canonical, candidate, registered, execute=True)
    original = candidate.read_bytes()
    corrections = []
    for event in events[2:]:
        corrected = deepcopy(event)
        corrected["recorded_at"] = "2026-01-02T00:00:00Z"
        execution = corrected["execution_observation"]
        corrected["measurements"][0].update(
            evidence_ref=execution["result_ref"], input_revision=execution["input_revision"],
        )
        corrections.append(_rehash(corrected))
        append_explore_result_event(candidate, corrected)
    corrected_source = candidate.read_bytes()
    assert corrected_source.startswith(original)

    preview = _reconcile(canonical, candidate, registered)
    assert preview["plan"]["selected_event_count"] == 2
    assert preview["plan"]["blocked_event_count"] == 0
    executed = _reconcile(canonical, candidate, registered, execute=True)
    assert executed["plan"] == preview["plan"]
    assert executed["status"] == "reconciled"
    assert executed["writeback"]["appended_event_count"] == 2
    restored = load_explore_result_events_strict(canonical, goal_id=GOAL)
    assert {row["event_id"] for row in restored} == {
        row["event_id"] for row in [*events[:2], *corrections]
    }
    before = canonical.read_bytes()
    repeated = _reconcile(canonical, candidate, registered, execute=True)
    assert repeated["status"] == "already_reconciled"
    assert repeated["writeback"]["performed"] is False
    assert canonical.read_bytes() == before
    assert candidate.read_bytes() == corrected_source


@pytest.mark.parametrize("damage", ["forged-id", "unknown-field", "private-text"])
def test_invalid_source_is_rejected_before_recovering_any_subset(tmp_path, damage):
    canonical, candidate, events, registered = _history(tmp_path)
    invalid = events[-1]
    if damage == "forged-id":
        invalid["event_id"] = "forged"
    elif damage == "unknown-field":
        invalid["raw_transcript"] = "unsupported"
        _rehash(invalid)
    else:
        invalid["summary"] = "to" + "ken=" + "synthetic-value"
        _rehash(invalid)
    candidate.write_bytes(_encoded(events))
    before, original = canonical.read_bytes(), candidate.read_bytes()
    for execute in (False, True):
        with pytest.raises(ValueError):
            _reconcile(canonical, candidate, registered, execute=execute)
        assert canonical.read_bytes() == before
        assert candidate.read_bytes() == original


def test_runtime_failure_is_not_misreported_as_blocked_history(tmp_path, monkeypatch):
    from loopx.capabilities.explore import source_history_reconcile

    canonical, candidate, _, registered = _history(tmp_path)
    before = canonical.read_bytes()

    def unavailable(*args, **kwargs):
        raise RuntimeError("measurement runtime unavailable")

    monkeypatch.setattr(source_history_reconcile, "validate_explore_result_event_attribution", unavailable)
    for execute in (False, True):
        with pytest.raises(RuntimeError, match="runtime unavailable"):
            _reconcile(canonical, candidate, registered, execute=execute)
        assert canonical.read_bytes() == before


def test_real_cli_mixed_history_preview_execute_and_readback(tmp_path):
    from loopx.extensions import lark
    from loopx.extensions.runtime import default_extension_state_file, install_extension
    from tests.capabilities.test_explore_turn_context import registry

    registry_path = registry(tmp_path, graph=True, planning=True)
    canonical, candidate, _, registered = _history(tmp_path)
    original, before = candidate.read_bytes(), canonical.read_bytes()
    config = tmp_path / "board.json"
    config.write_text(json.dumps({"result_records": {key: "synthetic-row" for key in registered}}))
    command = [
        sys.executable, "-m", "loopx.cli", "--registry", str(registry_path),
        "--runtime-root", str(tmp_path / "runtime"), "explore", "source-history-reconcile",
        "--goal-id", GOAL, "--candidate-runtime-root", str(tmp_path / "candidate"),
        "--config-path", str(config),
    ]

    def run(*args):
        return subprocess.run([*command, *args], capture_output=True, text=True, timeout=60)

    denied = run("--format", "json", "--execute")
    assert denied.returncode != 0
    assert "not installed" in denied.stdout
    assert canonical.read_bytes() == before
    installed = install_extension(
        Path(lark.__file__).with_name("extension.toml"),
        state_file=default_extension_state_file(tmp_path / "runtime"), execute=True,
    )
    assert installed["doctor"]["verified"] is True
    preview_result = run("--format", "json")
    assert preview_result.returncode == 0, preview_result.stdout
    preview = json.loads(preview_result.stdout)
    assert preview["plan"]["selected_event_count"] == 1
    assert preview["plan"]["blocked_reasons"] == {RESULT_REASON: 1, INPUT_REASON: 1}
    markdown = run("--format", "markdown")
    assert markdown.returncode == 0, markdown.stdout
    assert RESULT_REASON in markdown.stdout and INPUT_REASON in markdown.stdout
    assert canonical.read_bytes() == before
    executed_result = run("--format", "json", "--execute")
    assert executed_result.returncode == 0, executed_result.stdout
    executed = json.loads(executed_result.stdout)
    assert executed["plan"] == preview["plan"]
    assert executed["writeback"]["appended_event_count"] == 1
    assert executed["writeback"]["readback_verified"] is True
    assert {row["result_id"] for row in load_explore_result_events_strict(canonical, goal_id=GOAL)} == {
        "question-a", "valid",
    }
    assert candidate.read_bytes() == original
    assert str(tmp_path) not in preview_result.stdout
