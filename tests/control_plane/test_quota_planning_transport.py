from __future__ import annotations

import json
import tempfile

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.todos import quota_selection
from loopx.control_plane.todos.quota_selection import (
    _decode_quota_planning_wire,
    project_quota_planning,
)


@pytest.fixture
def owned_runtime(tmp_path, monkeypatch):
    for variable in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(variable, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    yield
    assert effect_runtime._runtime_dir().is_relative_to(tmp_path)
    effect_runtime.restart_effect_runtime(wait_for_exit=True)


def test_large_planning_returns_all_lanes_without_exceeding_inline_reply(owned_runtime):
    items = [{
        "schema_version": "todo_item_v0", "todo_id": f"todo_wire_{index:04d}",
        "index": index + 1, "role": "agent", "status": "open", "done": False,
        "text": f"Deliver artifact {index}: " + "bounded work " * 12,
        "priority": "P0", "task_class": "advancement_task", "claimed_by": "worker",
        "archive_state": "active", "source_section": "Agent Todo",
        "evidence_refs": ["fixture-evidence"],
    } for index in range(1000)]
    result = project_quota_planning(
        {"items": items[:12], "work_counts": {"complete": True}},
        all_open_items=items, source_open_count=len(items),
        agent_identity={"agent_id": "worker"}, filter_user_gate_blocks_agent=False,
        available_capabilities=[], current_time="2026-10-15T00:00:00Z",
    )
    assert result["work_counts"]["open"] == 1000
    lanes = result["lanes"]
    for name in ("all_open_items", "blocking_open_items", "open_items",
                 "executable_items", "claimed_open_items", "display_open_items"):
        assert [item["todo_id"] for item in lanes[name]] == [item["todo_id"] for item in items]
    # Callers retain the full read model, including independent mutable records.
    assert len(json.dumps(result, separators=(",", ":")).encode()) > 2 * 1024 * 1024
    lanes["all_open_items"][0]["evidence_refs"].append("local-only")
    assert lanes["open_items"][0]["evidence_refs"] == ["fixture-evidence"]
    assert items[0]["evidence_refs"] == ["fixture-evidence"]


def test_current_wire_keeps_legacy_full_source_decisions(owned_runtime, monkeypatch):
    from loopx.control_plane.quota.should_run import build_quota_should_run
    from loopx.control_plane.testing.action_portfolio_scenarios import (
        ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
        ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_GOAL_ID,
        planning_horizon_strategic_context_status,
    )

    native = quota_selection.effect_runtime_result

    def compare(method, params, **options):
        wire = native(method, params, **options)
        legacy = native(method, {**params, "schema_version": "todo_quota_planning_request_v3"}, **options)
        assert _decode_quota_planning_wire(wire) == legacy
        return wire

    monkeypatch.setattr(quota_selection, "effect_runtime_result", compare)
    payload = build_quota_should_run(
        planning_horizon_strategic_context_status(),
        goal_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_GOAL_ID,
        agent_id=ACTUAL_DEFAULT_MODEL_BEHAVIOR_FIXTURE_AGENT_ID,
    )
    assert payload["selected_todo"]["todo_id"] == "todo_regression_gate"


def wire_response(**overrides):
    return {
        "schema_version": "todo_quota_planning_wire_v1",
        "planning": {"schema_version": "todo_quota_planning_v0", "future": {"keep": None},
                     "lanes": {"open_items": [0, 1, 0], "gate_items": [], "claim_scope": None}},
        "records": [{"todo_id": "same", "text": "first", "details": {"refs": []}},
                    {"todo_id": "same", "text": "different"}],
        "record_lanes": ["open_items", "gate_items"],
        **overrides,
    }


def test_decoding_retains_distinct_facts_duplicates_metadata_and_isolation():
    result = _decode_quota_planning_wire(wire_response())
    items = result["lanes"]["open_items"]
    assert [item["text"] for item in items] == ["first", "different", "first"]
    assert result["lanes"]["gate_items"] == [] and result["lanes"]["claim_scope"] is None
    assert result["future"] == {"keep": None}
    items[0]["details"]["refs"].append("local")
    assert items[2]["details"]["refs"] == []


def test_deep_json_records_keep_the_original_roundtrip_and_mutation_isolation():
    record = {}
    for _ in range(600):
        record = {"nested": record}
    wire = wire_response(records=[record])
    wire["planning"]["lanes"]["open_items"] = [0, 0]
    restored = _decode_quota_planning_wire(json.loads(json.dumps(wire)))
    first, second = restored["lanes"]["open_items"]
    for _ in range(600):
        first, second = first["nested"], second["nested"]
    first["local"] = True
    assert "local" not in second


@pytest.mark.parametrize("overrides", [
    {"schema_version": "unknown"}, {"planning": None}, {"planning": {"lanes": []}},
    {"records": [None]}, {"records": []}, {"record_lanes": "open_items"},
    {"record_lanes": [[]]}, {"record_lanes": ["open_items", "open_items"]},
    {"record_lanes": ["missing"]}, {"record_lanes": []},
])
def test_malformed_wire_is_rejected(overrides):
    with pytest.raises(RuntimeError, match="quota planning wire"):
        _decode_quota_planning_wire(wire_response(**overrides))


@pytest.mark.parametrize("reference", [True, -1, 2, 1.0, "0", None, {}])
def test_record_references_must_be_in_range_integers(reference):
    wire = wire_response()
    wire["planning"]["lanes"]["open_items"] = [reference]
    with pytest.raises(RuntimeError, match="quota planning wire"):
        _decode_quota_planning_wire(wire)


def test_reference_expansion_is_bounded_before_records_are_copied():
    wire = wire_response(records=[{"text": "x" * 2048}])
    wire["planning"]["lanes"]["open_items"] = [0] * 32769
    with pytest.raises(RuntimeError, match="expanded response is oversized"):
        _decode_quota_planning_wire(wire)
