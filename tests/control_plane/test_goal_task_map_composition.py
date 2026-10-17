"""Public Goal-map composition and its existing compatibility codecs."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from loopx.control_plane.runtime.public_safety import public_safe_compact_text
from loopx.control_plane.todos.contract import (
    TODO_STATUS_OPEN, TODO_TASK_CLASS_MONITOR, normalize_todo_status, todo_done_for_status,
)
from loopx.control_plane.work_items import task_graph


def project(rows, *, goal_id="graph-fixture", source_truncated=False, normalizer=normalize_todo_status,
            source_read_at=None):
    return task_graph.build_goal_task_map(
        goal_id=goal_id, todos=rows, source_truncated=source_truncated,
        public_safe_compact_text=public_safe_compact_text,
        normalize_todo_status=normalizer, todo_done_for_status=todo_done_for_status,
        todo_status_open=TODO_STATUS_OPEN, monitor_task_class=TODO_TASK_CLASS_MONITOR,
        source_read_at=source_read_at,
    )


def test_map_preserves_source_age_and_unknown_or_partial_coverage(monkeypatch):
    monkeypatch.setattr(task_graph, "now_utc_iso", lambda: "2026-10-01T00:06:00Z")
    rows = [{"todo_id": f"todo_work_{index}", "text": f"Review work {index}"} for index in range(121)]
    result = project(rows, source_read_at="2026-10-01T00:00:00Z")
    envelope = result["projection_envelope"]
    assert not envelope["fresh"] and not envelope["complete"]
    assert envelope["sources"][0]["last_read_at"] == "2026-10-01T00:00:00Z"
    assert envelope["coverage"]["expected_count"] == 121
    assert envelope["coverage"]["included_count"] == 120
    assert envelope["coverage"]["omitted"] == [{"reason": "map_node_limit", "count": 1, "refs": []}]
    unknown = project(rows[:1])["projection_envelope"]
    assert not unknown["fresh"] and unknown["alert"]
    assert unknown["sources"][0]["read_status"] == "not_read"
    partial = project(rows[:1], source_truncated=True,
                      source_read_at="2026-10-01T00:06:00Z")["projection_envelope"]
    assert partial["fresh"] and not partial["complete"]
    assert partial["coverage"]["expected_count"] is None


@pytest.mark.parametrize(("role", "task_class", "kind", "owner_visible"), [
    ("user", "continuous_monitor", "gate", False),
    ("agent", "continuous_monitor", "monitor", True),
    ("agent", "advancement_task", "deliverable", True),
    (" user ", " continuous_monitor ", "deliverable", True),
])
def test_role_controls_kind_and_public_owner_fields(role, task_class, kind, owner_visible):
    node, = project([{"todo_id": "todo_work", "text": "Review work", "role": role,
        "task_class": task_class, "claimed_by": "builder", "task_domain": "release"}])["nodes"]
    assert node["kind"] == kind
    assert ("owner_agent" in node) is owner_visible
    assert ("task_domain" in node) is owner_visible


@pytest.mark.parametrize(("status", "done", "expected"), [
    ("deferred", True, "waiting"), ("done", False, "done"),
    ("blocked", False, "blocked"), ("open", False, "open"),
    (None, ["legacy truthy marker"], "done"), ("unrecognized", False, "open"),
])
def test_existing_shared_state_codec_remains_authoritative(status, done, expected):
    node, = project([{"todo_id": "todo_work", "text": "Review work", "status": status,
                      "done": done}])["nodes"]
    assert node["state"] == expected


def test_first_renderable_compact_identity_wins_without_overwriting_metadata():
    result = project([
        {"todo_id": "todo_duplicate", "title": ""},
        {"todo_id": " todo_duplicate ", "text": "First visible", "claimed_by": "first"},
        {"todo_id": "todo_duplicate", "text": "Later duplicate", "claimed_by": "later"},
    ])
    node, = result["nodes"]
    assert (node["title"], node["owner_agent"], node["refs"]) == (
        "First visible", "first", {"todo_ids": ["todo_duplicate"]})


def test_discarded_sources_do_not_evaluate_their_status_codec():
    def normalize(value):
        if value == "discarded":
            raise ValueError("discarded status must stay unread")
        return normalize_todo_status(value)
    result = project([
        {"todo_id": "todo_missing_title", "status": "discarded"},
        {"todo_id": "todo_work", "text": "Review work", "status": "open"},
        {"todo_id": "todo_work", "text": "Discarded duplicate", "status": "discarded"},
    ], normalizer=normalize)
    assert [node["title"] for node in result["nodes"]] == ["Review work"]


def test_omitted_sources_keep_opaque_owner_metadata_unread():
    class OpaqueMetadata:
        def __str__(self):
            raise ValueError("omitted metadata must stay unread")
    rows = [{"todo_id": f"todo_{index}", "text": f"Review {index}"} for index in range(120)]
    rows.append({"todo_id": "todo_omitted", "text": "Not expanded", "claimed_by": OpaqueMetadata()})
    assert len(project(rows)["nodes"]) == 120


def test_shared_identity_codec_survives_unicode_title_and_owner_changes():
    original = {"todo_id": "todo_世界", "title": "\ufeffReview 🧪 work", "claimed_by": "builder"}
    before = deepcopy(original)
    first = project([original])["nodes"][0]
    second = project([{**original, "title": "Renamed work", "claimed_by": "reviewer"}])["nodes"][0]
    assert first["title"] == original["title"]
    assert first["node_id"] == second["node_id"]
    assert original == before


def test_no_opaque_metadata_crosses_the_map_boundary(monkeypatch):
    calls = []
    real = task_graph.effect_runtime_result
    def observe(method, params, **kwargs):
        calls.append((method, params))
        return real(method, params, **kwargs)
    monkeypatch.setattr(task_graph, "effect_runtime_result", observe)
    source = [{"todo_id": "todo_work", "text": "Review work", "claimed_by": "builder",
        "note": "must-not-cross", "evidence": "must-not-cross",
        "unknown": {"opaque_integer": 9007199254740993, "amount": 2.0}}]
    original = deepcopy(source)
    result = project(source)
    assert len(calls) == 1
    assert calls[0][0] == "work_item.task_graph.goal_topology"
    assert "must-not-cross" not in json.dumps(calls[0][1])
    assert "opaque_integer" not in json.dumps(calls[0][1])
    assert "unknown" not in result["nodes"][0]
    assert source == original


def test_unsafe_display_rows_are_omitted_without_replacing_the_source():
    private_path = "/" + "home" + "/synthetic/private.txt"
    result = project([
        {"todo_id": "todo_private", "text": private_path},
        {"todo_id": "todo_secret", "text": "api_key=synthetic-value"},
        {"todo_id": "todo_visible", "text": "Review work", "claimed_by": private_path},
    ])
    node, = result["nodes"]
    assert node["refs"] == {"todo_ids": ["todo_visible"]}
    assert "owner_agent" not in node


def test_full_source_admission_is_bounded_without_hiding_omitted_nodes():
    rows = [{"todo_id": f"todo_work_{index}", "text": f"Review work {index}"} for index in range(121)]
    result = project(rows, source_truncated=True, goal_id=" literal goal scope ")
    assert result["goal_id"] == " literal goal scope "
    assert len(result["nodes"]) == result["limits"]["node_limit"] == 120
    assert result["limits"]["omitted_node_count"] == 1
    assert result["limits"]["source_truncated"] is True
    assert result["limits"]["topology_complete"] is False


def test_complete_source_survives_the_ordinary_transport_frame():
    rows = [{"todo_id": f"todo_{index:06d}_" + "x" * 100,
             "title": "Review source " + str(index) + " public" * 30,
             "claimed_by": "b" * 80, "task_domain": "d" * 80}
            for index in range(12000)]
    result = project(rows)
    assert len(result["nodes"]) == 120
    assert result["limits"]["omitted_node_count"] == 11880
    assert result["limits"]["source_truncated"] is False
    assert result["nodes"][0]["owner_agent"] == "b" * 80
    assert result["nodes"][-1]["refs"]["todo_ids"] == [rows[119]["todo_id"]]


def test_native_refusal_has_no_python_fallback(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("synthetic native refusal")
    monkeypatch.setattr(task_graph, "effect_runtime_result", refuse)
    with pytest.raises(RuntimeError, match="synthetic native refusal"):
        project([{"todo_id": "todo_work", "text": "Review work"}])
