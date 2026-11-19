"""Complete Todo replies retain bytes and fail closed at the transport boundary."""
import gzip
import hashlib
import json
from base64 import b64encode
from copy import deepcopy

import pytest

from loopx.control_plane import effect_runtime as runtime
from loopx.control_plane.todos.goal_todo_projection import project_todo_read


def response(data, *, encoding="gzip", size=None):
    piece = {"offset": 0, "byte_count": len(data), "sha256": hashlib.sha256(data).hexdigest(),
             "data": b64encode(data).decode(), "next": None}
    if encoding is not None:
        piece.update(content_encoding=encoding, decoded_byte_count=size)
    return {"schema_version": "todo_read_result_v0", "result_piece": piece}


def test_compressed_read_preserves_complete_unknown_fields(monkeypatch):
    expected = {"schema_version": "todo_read_result_v0", "fields": {
        "agent_todos": {"items": [{"todo_id": "todo_original", "note": "完整要求🙂" * 4000,
            "required_write_scopes": ["result.json"], "extra": {"empty": [], "absent_is_not_null": None}}]}}}
    decoded = json.dumps(expected, ensure_ascii=False).encode()
    packet = response(gzip.compress(decoded), size=len(decoded))
    monkeypatch.setattr(runtime, "effect_runtime_result", lambda *args, **kwargs: packet)
    assert project_todo_read({"kind": "source_fields"}) == expected


def test_legacy_uncompressed_read_keeps_the_existing_result(monkeypatch):
    expected = {"schema_version": "todo_read_result_v0", "fields": {"extra": [True, 1, 1.0, None]}}
    data = json.dumps(expected).encode()
    monkeypatch.setattr(runtime, "effect_runtime_result", lambda *args, **kwargs: response(data, encoding=None))
    assert project_todo_read({"kind": "source_fields"}) == expected


@pytest.mark.parametrize("mutation", ["truncated", "trailing", "too_small", "too_large", "boolean", "oversized", "unknown"])
def test_compressed_reply_rejects_invalid_extent_or_encoding(monkeypatch, mutation):
    decoded = b'{"schema_version":"todo_read_result_v0","fields":{"note":"required"}}'
    data, size, encoding = gzip.compress(decoded), len(decoded), "gzip"
    if mutation == "truncated":
        data = data[:-8]
    elif mutation == "trailing":
        data += gzip.compress(b"{}")
        size += 2
    elif mutation == "too_small":
        size -= 1
    elif mutation == "too_large":
        size += 1
    elif mutation == "boolean":
        size = True
    elif mutation == "oversized":
        size = runtime.MAX_LOCAL_SNAPSHOT_BYTES + 1
    else:
        encoding = "unknown"
    packet = response(data, size=size, encoding=encoding)
    monkeypatch.setattr(runtime, "effect_runtime_result", lambda *args, **kwargs: packet)
    with pytest.raises(ValueError, match="Todo read result"):
        project_todo_read({"kind": "source_fields"})


def test_compressed_reply_identity_cannot_change_between_pages(monkeypatch):
    decoded = b'{"schema_version":"todo_read_result_v0","fields":{"note":"required"}}'
    data = gzip.compress(decoded)
    first = response(data, size=len(decoded))
    first["result_piece"].update(data=b64encode(data[:10]).decode(),
        next={"offset": 10, "sha256": first["result_piece"]["sha256"]})
    second = response(data, size=len(decoded) + 1)
    second["result_piece"].update(offset=10, data=b64encode(data[10:]).decode())
    packets = iter([first, second])
    monkeypatch.setattr(runtime, "effect_runtime_result", lambda *args, **kwargs: next(packets))
    with pytest.raises(ValueError, match="identity changed"):
        project_todo_read({"kind": "source_fields"})


@pytest.mark.parametrize("size", [8000, 15000])
def test_real_quota_bundle_keeps_complete_rows_without_response_snapshots(monkeypatch, size):
    from loopx.control_plane.testing.quota_fixtures import quota_status_payload
    from loopx.control_plane.todos import goal_todo_projection
    from loopx.control_plane.todos.summary_item import todo_planning_source_items, todo_summary_source_items
    from loopx.quota import build_quota_should_run

    snapshots, decoded = [], []
    original = runtime._read_local_snapshot_response
    def observed(envelope, sink, **kwargs):
        snapshots.append(kwargs["method"])
        return original(envelope, sink, **kwargs)
    monkeypatch.setattr(runtime, "_read_local_snapshot_response", observed)
    decode = goal_todo_projection._decode_todo_result_bytes
    def retain_decoded(*args):
        result = decode(*args)
        decoded.append(result)
        return result
    monkeypatch.setattr(goal_todo_projection, "_decode_todo_result_bytes", retain_decoded)
    rows = [{"todo_id": f"todo_build_{i:05d}", "text": "Build work", "role": "agent", "status": "open",
             "done": False, "task_class": "advancement_task", "priority": "P1", "claimed_by": "agent-a",
             "successor_todo_ids": [f"todo_build_{(i + 1) % size:05d}"]} for i in range(size)]
    rows[0].update(note="完整验收与恢复🙂", required_write_scopes=["result.json"], evidence="verifier:accepted")
    source = {"schema_version": "todo_summary_v0", "source_section": "Agent Todo", "total_count": size,
              "open_count": size, "done_count": 0, "items": rows, "first_open_items": rows[:1]}
    original_source = deepcopy(source)
    # The supported standalone transports independently define each view's
    # complete fields, including deliberate presentation differences.
    expected = {"summary": todo_summary_source_items(source), "planning": todo_planning_source_items(source),
                "terminal": todo_planning_source_items(source, include_terminal=True)}
    snapshots.clear()
    status = quota_status_payload(goal_id="goal-a", status="active_state_agent_todo", recommended_action="Build work",
                                  agent_todos=source, coordination={"registered_agents": ["agent-a"]})
    with runtime.effect_runtime_request_scope():
        result = build_quota_should_run(status, goal_id="goal-a", agent_id="agent-a", available_capabilities=["shell"])
    assert result["selected_todo"]["todo_id"] == "todo_build_00000"
    assert result["decision"] == "autonomous_replan_required"
    assert "todo.read" not in snapshots, "quota response must stay within the shared socket frame"
    wires = next(reply["quota_wires"] for reply in decoded if "quota_wires" in reply)
    for name, view in wires[-1]["display_views"].items():
        assert [row["todo_id"] for row in view] == [row["todo_id"] for row in rows]
        assert view == expected[name]
    assert source == original_source, "transport compression grants no source mutation"
