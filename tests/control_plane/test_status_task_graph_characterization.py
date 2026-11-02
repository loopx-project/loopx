"""Frozen migration evidence; remove after the Python status facade retires."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from loopx import status
from loopx.control_plane import effect_runtime
from loopx.control_plane.work_items import task_graph
from loopx.control_plane.runtime.public_safety import public_safe_compact_text
from loopx.control_plane.todos.contract import normalize_todo_status, todo_done_for_status

FIXTURE = Path(__file__).with_name("fixtures") / "status_task_graph_characterization.json"
NOW = "2026-11-02T12:00:00Z"


def todo(tid, **fields):
    return {"todo_id": tid, "text": f"Review {tid}", "status": "open", **fields}


def cases():
    root = todo("todo_root", claimed_by="builder")
    parent = todo("todo_parent", status="done", done=True, successor_todo_ids=["todo_root"])
    gate = todo("todo_gate", task_class="user_gate")
    rows = {
        "diamond_at_cap": [root, todo("todo_aaa", status="done", successor_todo_ids=["todo_root"]),
            todo("todo_bbb", status="done", successor_todo_ids=["todo_root"]),
            todo("todo_shared", status="done", successor_todo_ids=["todo_aaa", "todo_bbb"]),
            todo("todo_fourth", status="done", successor_todo_ids=["todo_shared"])],
        "cycle": [root, {**parent, "successor_todo_ids": ["todo_root", "todo_other"]},
            todo("todo_other", status="done", successor_todo_ids=["todo_parent"])],
        "missing_prerequisite": [{**root, "resume_when": "todo_done:todo_missing"}],
        "open_predecessor": [root, {**parent, "done": False, "status": "open", "evidence": "Unfinished evidence"},
            todo("todo_ancestor", status="done", successor_todo_ids=["todo_parent"])],
        "condition_and_lineage": [{**root, "resume_when": "todo_done:todo_parent"}, parent],
        "predecessor_overflow": [root, *[todo(f"todo_parent_{i}", status="done", successor_todo_ids=["todo_root"]) for i in range(7)]],
        "duplicate_identity": [root, {**parent, "todo_id": "todo_same!"}, {**parent, "todo_id": "todo_same?"}, root],
        "long_public_text": [{**root, "text": "Public 🧪 review " * 30, "last_actor_agent_id": "reviewer"},
            {**parent, "evidence": "Public completion evidence " * 30}],
        "redaction": [{**root, "claimed_by": "api_key=synthetic-value"},
            {**parent, "evidence": "api_key=synthetic-value", "note": "Public note survives"}],
        "handoff_evidence": [root, {**parent, "evidence": "Delivered and checked", "handoff_note": {
            "from_agent": "builder", "to_agent": "reviewer", "status": "done"}}],
        "unicode_ids": [todo("todo_世界", text="Review 世界 🧪"),
            todo("todo_前置", status="done", successor_todo_ids=["todo_世界"])],
        "all_empty": [],
    }
    result = {}
    for name, values in rows.items():
        result[name] = {"item": {"goal_id": "graph-fixture", "agent_todos": {
            "items": values, "open_count": 1, "total_count": len(values)}}, "goal": {"id": "graph-fixture"}}
    result["gate_overflow"] = {"item": {"goal_id": "graph-fixture", "agent_todos": {"items": [root]},
        "user_todos": {"items": [{**gate, "todo_id": f"todo_gate_{i}", "index": i} for i in range(5)], "open_count": 7}},
        "goal": {"id": "graph-fixture"}}
    result["no_selected_run_gate"] = {"item": {"goal_id": "graph-fixture", "user_todos": {"items": [gate]}},
        "goal": {"id": "graph-fixture"}, "goal_latest_runs": [{"run_id": "run_evidence", "classification": "audit continuation", "generated_at": NOW}]}
    result["repair_evidence"] = {"item": {"goal_id": "graph-fixture", "agent_todos": {"items": [root, parent]},
        "autonomous_replan_obligation": {"schema_version": "synthetic_replan_v0", "recommended_action": "Repair selected work"}},
        "goal": {"id": "graph-fixture"}, "goal_latest_runs": [{"run_id": "run_evidence", "classification": "audit continued", "generated_at": NOW}]}
    result["stale_source_time"] = {"item": {"goal_id": "graph-fixture", "agent_todos": {"items": [root], "total_count": 9},
        "stale_latest_run_warning": {"active_state_updated_at": "2026-11-01T00:00:00Z"}},
        "goal": {"id": "graph-fixture"}, "goal_latest_runs": [{"generated_at": "2026-10-31T00:00:00Z", "classification": "checked"}]}
    result["absent_goal"] = {"item": {}, "goal": {}}
    result["invalid_goal"] = {"item": {"goal_id": "api_key=synthetic-value"}, "goal": {}}
    return result


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def measure(monkeypatch, arguments, *, goal_map=False):
    calls = []
    real = effect_runtime.effect_runtime_result

    def observe(method, params, **kwargs):
        calls.append(method)
        return real(method, params, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", observe)
    monkeypatch.setattr(task_graph, "effect_runtime_result", observe)
    monkeypatch.setattr(task_graph, "now_utc_iso", lambda: NOW)
    before = deepcopy(arguments)
    if goal_map:
        output = task_graph.build_goal_task_map(**arguments, public_safe_compact_text=public_safe_compact_text,
            normalize_todo_status=normalize_todo_status, todo_done_for_status=todo_done_for_status,
            todo_status_open="open", monitor_task_class="continuous_monitor")
    else:
        output = status.build_task_graph_projection(**arguments)
    assert arguments == before
    return {"json": encode(output), "calls": calls}


@pytest.mark.parametrize("name", list(cases()))
def test_status_frozen_outputs_and_crossings(monkeypatch, name):
    expected = json.loads(FIXTURE.read_text())["status"][name]
    actual = measure(monkeypatch, cases()[name])
    assert actual["json"] == expected["json"]
    assert actual["calls"] == ([] if name in {"absent_goal", "invalid_goal"} else ["work_item.task_graph.status"])


def map_cases():
    return {"mixed": [todo("todo_work"), todo("todo_gate", role="user"), todo("todo_monitor", task_class="continuous_monitor")],
        "lineage": [todo("todo_root"), todo("todo_parent", status="done", successor_todo_ids=["todo_root"])],
        "unicode": [todo("todo_世界", text="Review 世界") ]}


@pytest.mark.parametrize("name", list(map_cases()))
def test_goal_map_frozen_outputs(monkeypatch, name):
    actual = measure(monkeypatch, {"goal_id": "graph-fixture", "todos": map_cases()[name],
        "source_truncated": False, "source_read_at": NOW}, goal_map=True)
    assert actual == json.loads(FIXTURE.read_text())["goal_map"][name]


def test_status_native_refusal_has_no_python_fallback(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("synthetic native refusal")
    monkeypatch.setattr(task_graph, "effect_runtime_result", refuse)
    with pytest.raises(RuntimeError, match="synthetic native refusal"):
        status.build_task_graph_projection(**cases()["repair_evidence"])


def test_status_boundary_carries_only_public_display_facts(monkeypatch):
    calls = []
    real = task_graph.effect_runtime_result

    def observe(method, params, **kwargs):
        calls.append((method, params))
        return real(method, params, **kwargs)

    monkeypatch.setattr(task_graph, "effect_runtime_result", observe)
    arguments = deepcopy(cases()["redaction"])
    arguments["item"]["agent_todos"]["items"][0]["unknown"] = {"private": "must-not-cross"}
    status.build_task_graph_projection(**arguments)
    assert len(calls) == 1
    encoded = encode(calls[0][1])
    assert "must-not-cross" not in encoded
    assert "api_key=synthetic-value" not in encoded
    assert "unknown" not in encoded


def test_empty_display_text_does_not_steal_selection(monkeypatch):
    monkeypatch.setattr(task_graph, "now_utc_iso", lambda: NOW)
    output = status.build_task_graph_projection({"goal_id": "graph-fixture", "agent_todos": {
        "items": [todo("todo_empty", text="  "), todo("todo_visible")]}}, goal={"id": "graph-fixture"})
    assert output["nodes"][0]["refs"] == {"todo_ids": ["todo_visible"]}
