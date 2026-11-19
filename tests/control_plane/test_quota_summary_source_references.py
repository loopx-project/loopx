"""Source copies remain Python values; planning/display rows remain JSON values."""
import json
import math
from copy import deepcopy

import pytest

from loopx.control_plane.todos import quota_selection, quota_summary
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos


def test_source_values_and_warning_fields_preserve_python_identity():
    metadata = {"revision": 9007199254740993, "ratio": -0.0, "whole": 1.0}
    proof = {"agent_id": "agent-a", "metadata": metadata}
    completed = {"index": 1.0, "text": "Completed synthetic work", "status": "done",
                 "task_class": "advancement_task", "completion_recovery": metadata}
    source = {"items": [], "total_count": 9007199254740993, "done_count": 1.0,
              "deferred_count": -0.0, "goal_acceptance_contract": metadata,
              "advancement_frontier_revision_index": metadata,
              "vision_wait_states": [proof, {"agent_id": "agent-b"}],
              "completed_without_successor_count": 9007199254740993,
              "completed_without_successor_items": [completed]}
    result = quota_summary.summarize_user_todos_for_quota(source, agent_identity={"agent_id": "agent-a"})
    assert result["total_count"] == 9007199254740993
    assert type(result["done_count"]) is float
    assert math.copysign(1, result["deferred_count"]) == -1
    assert result["goal_acceptance_contract"] is metadata
    assert result["advancement_frontier_revision_index"] is metadata
    assert result["vision_wait_states"][0] is proof
    warning = result["completed_without_successor_items"][0]
    assert type(warning["index"]) is float
    assert warning["completion_recovery"] is metadata
    assert result["completed_without_successor_count"] == 9007199254740993
    assert result["todo_succession_warning"]["count"] == 9007199254740993
    assert json.dumps(result["goal_acceptance_contract"]) == json.dumps(metadata)


def test_closure_overlay_preserves_unprojected_source_metadata():
    parsed = parse_active_state_todos(
        "## Agent Todo\n- [x] [P1] Complete bounded work.\n"
        "  <!-- loopx:todo todo_id=todo_closed status=done task_class=advancement_task no_followup=true -->\n"
    )["agent_todos"]
    metadata = {"revision": 9007199254740993, "ratio": -0.0}
    parsed["closure_intent"]["metadata"] = metadata
    result = quota_summary.summarize_user_todos_for_quota(parsed)
    assert result["closure_intent"]["metadata"] is metadata
    assert result["closure_intent"]["source"] == "todo_no_followup"


def test_planning_values_are_not_restored_from_raw_source():
    source = {"open_count": 9007199254740993, "items": [
        {"index": 1.0, "source_section": "User Todo", "text": "First synthetic work", "status": "open"},
        {"index": 2, "source_section": "User Todo", "text": "Second synthetic work", "status": "open"},
    ]}
    result = quota_summary.summarize_user_todos_for_quota(source)
    assert result["open_count"] == 9007199254740992
    assert [row["index"] for row in result["first_open_items"]] == [1, 2]
    assert type(result["first_open_items"][0]["index"]) is int
    assert type(source["items"][0]["index"]) is float


@pytest.mark.parametrize("raw,expected", [(None, 1), ("1.0", 1), (" 2_0 ", 20), (-2, 0), (True, 1)])
def test_warning_integer_codec_does_not_choose_the_count(raw, expected):
    source = {"items": [], "completed_without_successor_count": raw,
              "completed_without_successor_items": [{"text": "Completed work", "status": "done"}]}
    result = quota_summary.summarize_user_todos_for_quota(source)
    assert result["completed_without_successor_count"] == expected


def wire():
    return {"schema_version": "todo_quota_summary_wire_v1",
            "summary": {"work_counts": {}, "first_open_items": [], "first_executable_items": [],
                        "monitor_open_items": [], "backlog_items": [], "total_count": None},
            "source_copies": [{"source": ["total_count"], "target": ["total_count"]}],
            "integer_copies": []}


@pytest.mark.parametrize("patch", [
    {"source": ["missing"]}, {"target": []}, {"target": ["missing"]},
    {"source": [True]}, {"target": ["first_open_items", -1]},
])
def test_invalid_source_reference_is_rejected_without_mutating_source(patch):
    value = wire()
    value["source_copies"][0].update(patch)
    source = {"total_count": {"nested": 1}}
    before = deepcopy(source)
    with pytest.raises(RuntimeError, match="invalid TypeScript quota summary wire"):
        quota_selection._decode_quota_summary_wire(value, source)
    assert source == before


def test_source_reference_expansion_is_bounded_before_attachment(monkeypatch):
    source = {"total_count": "x" * 1000}
    value = wire()
    monkeypatch.setattr(quota_selection, "MAX_LOCAL_SNAPSHOT_BYTES", 500)
    with pytest.raises(RuntimeError, match="expanded response is oversized"):
        quota_selection._decode_quota_summary_wire(value, source)
    assert value["summary"]["total_count"] is None


def test_repeated_planning_rows_are_independent_mutable_copies():
    item = {"todo_id": "todo_mutable", "index": 1, "text": "Synthetic work",
            "status": "open", "required_capabilities": ["synthetic.read"]}
    source = {"items": [item], "open_count": 1}
    result = quota_summary.summarize_user_todos_for_quota(source)
    first = result["first_open_items"][0]
    backlog = result["backlog_items"][0]
    assert first == backlog
    assert first is not backlog
    first["required_capabilities"].append("synthetic.write")
    assert backlog["required_capabilities"] == ["synthetic.read"]
    assert item["required_capabilities"] == ["synthetic.read"]
