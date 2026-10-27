"""Native input observation is narrower than history, release or a future ACK."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver.codex_input_trace import codex_context_in_active_input


def fixture(root):
    bundle = root / "trace"
    (bundle / "payloads").mkdir(parents=True)
    baseline = {"schema_version": "context_delivery_v1", "mode": "full",
        "target": {"session_id": "session"}, "context": {"enabled": True, "text": "完整🙂 " * 200, "stop": "Stop before publication."}}
    raw = json.dumps(baseline, ensure_ascii=False).encode()
    path = root / "capture.json"
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    # Split at ASCII JSON separators, independently from the production reader.
    first = raw.index(b'"context"')
    second = raw.index(b'"stop"')
    pages = []
    for start, end in ((0, first), (first, second), (second, len(raw))):
        page = {"ok": True, "schema_version": "context_capture_page_v1", "sha256": digest,
            "offset": start, "next_offset": end if end < len(raw) else None, "total_bytes": len(raw),
            "eof": end == len(raw), "content": raw[start:end].decode()}
        pages.append({"type": "function_call_output", "output": "Chunk ID: fixture\nOutput:\n" + json.dumps(page)})
    request = {"input": pages}
    (bundle / "manifest.json").write_text(json.dumps({"schema_version": 1, "root_thread_id": "session", "rollout_id": "session"}))
    (bundle / "payloads/input.json").write_text(json.dumps(request))
    (bundle / "payloads/output.json").write_text(json.dumps({"output_items": [
        {"type": "function_call", "name": "exec_command", "call_id": "guard"}]}))
    payloads = [
        {"type": "inference_started", "inference_call_id": "inference", "request_payload": {"path": "payloads/input.json"}},
        {"type": "inference_completed", "inference_call_id": "inference", "response_payload": {"path": "payloads/output.json"}},
        {"type": "tool_call_started", "tool_call_id": "guard", "model_visible_call_id": "guard",
         "requester": {"type": "model"}, "kind": {"type": "exec_command"}},
    ]
    rows = [{"schema_version": 1, "seq": i, "thread_id": "session", "rollout_id": "session",
             "codex_turn_id": "turn", "payload": payload} for i, payload in enumerate(payloads)]
    (bundle / "trace.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    observation = {"trace_bundle": str(bundle), "tool_call_id": "guard", "capture_file": str(path), "capture_sha256": digest}
    return observation, baseline, request, rows


def test_only_complete_tool_output_in_current_input_is_observed_without_writes(tmp_path):
    observation, baseline, request, _ = fixture(tmp_path)
    original = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert codex_context_in_active_input(observation, baseline=baseline, session_id="session")
    assert all(path.read_bytes() == raw for path, raw in original.items())
    # Identical repeated pages are ordinary history; every byte is still needed.
    request["input"].append(request["input"][0])
    path = tmp_path / "trace/payloads/input.json"
    path.write_text(json.dumps(request))
    assert codex_context_in_active_input(observation, baseline=baseline, session_id="session")
    path.write_text(json.dumps({"input": [{"type": "function_call_output", "output": json.dumps(baseline)}]}))
    assert codex_context_in_active_input(observation, baseline=baseline, session_id="session")


def test_long_trace_uses_current_window_and_never_old_delivery(tmp_path):
    from loopx.control_plane.effect_runtime import MAX_REQUEST_BYTES

    observation, baseline, _, rows = fixture(tmp_path)
    trace = tmp_path / "trace/trace.jsonl"
    # Old history need not fit in memory or be reread to observe a live call.
    old = {**rows[0], "seq": -1, "payload": {"type": "protocol_event_observed", "text": "x" * (MAX_REQUEST_BYTES + 100)}}
    trace.write_text(json.dumps(old) + "\n" + trace.read_text())
    assert codex_context_in_active_input(observation, baseline=baseline, session_id="session")
    # If the current inference itself fell outside the bounded window, decline.
    with trace.open("a") as stream:
        stream.write(json.dumps({**old, "seq": 3}) + "\n")
    assert not codex_context_in_active_input(observation, baseline=baseline, session_id="session")


@pytest.mark.parametrize("code_mode", [False, True])
def test_smaller_complete_rereads_recover_after_valid_json_preview(tmp_path, code_mode):
    observation, baseline, request, _ = fixture(tmp_path)
    raw = Path(observation["capture_file"]).read_bytes()
    middle = json.loads(request["input"][1]["output"].split("\nOutput:\n")[1])
    # Host truncation inside a string can preserve valid JSON and its old cursor.
    preview = {**middle, "content": middle["content"][:30] + "…9 tokens truncated…"}
    request["input"][1]["output"] = json.dumps(preview)
    path = tmp_path / "trace/payloads/input.json"
    path.write_text(json.dumps(request))
    assert not codex_context_in_active_input(observation, baseline=baseline, session_id="session")
    # Repage the same capture at different boundaries, overlapping intact old
    # ranges; no new capture, fake ACK, history clearing or new admission.
    split = raw.index("🙂".encode())
    recovered = []
    for start, end in ((0, split), (split, len(raw))):
        page = {**middle, "offset": start, "next_offset": end if end < len(raw) else None,
                "eof": end == len(raw), "content": raw[start:end].decode()}
        recovered.append({"type": "function_call_output", "output": json.dumps(page)})
    request["input"].extend(recovered)
    if code_mode:
        # One code-mode cell can emit several raw stdout blocks. Every block,
        # including the old preview, is actual model-facing input.
        request["input"] = [{"type": "custom_tool_call_output", "output": [
            {"type": "input_text", "text": item["output"]} for item in request["input"]]}]
    path.write_text(json.dumps(request))
    assert codex_context_in_active_input(observation, baseline=baseline, session_id="session")
    # The bad preview never covers the gap when a corrected range is absent.
    if code_mode:
        request["input"][0]["output"].pop()
    else:
        request["input"].pop()
    path.write_text(json.dumps(request))
    assert not codex_context_in_active_input(observation, baseline=baseline, session_id="session")


def test_full_projection_read_is_retention_evidence_with_json_number_semantics(tmp_path):
    observation, baseline, _, _ = fixture(tmp_path)
    baseline["context"]["compute"] = 1
    context = {**baseline["context"], "compute": 1.0}
    raw = json.dumps(context).encode()
    Path(observation["capture_file"]).write_bytes(raw)
    observation["capture_sha256"] = hashlib.sha256(raw).hexdigest()
    path = tmp_path / "trace/payloads/input.json"
    path.write_text(json.dumps({"input": [{"type": "function_call_output", "output": raw.decode()}]}))
    assert codex_context_in_active_input(observation, baseline=baseline, session_id="session")
    context["compute"] = True
    path.write_text(json.dumps({"input": [{"type": "function_call_output", "output": json.dumps(context)}]}))
    assert not codex_context_in_active_input(observation, baseline=baseline, session_id="session")


@pytest.mark.parametrize("failure", ["middle_missing", "pruned", "preview", "quoted", "wrong_role", "server_history",
    "bad_page", "shifted_offset", "capture_changed", "other_session", "other_tool", "ended", "new_inference", "compacted",
    "child", "code_mode", "wrong_response", "partial_trace", "reordered_trace", "missing_trace", "outside_payload", "symlink", "boolean_type"])
def test_unknown_stale_lossy_or_unbound_observation_never_reuses(tmp_path, failure):
    observation, baseline, request, rows = fixture(tmp_path)
    bundle = Path(observation["trace_bundle"])
    if failure == "middle_missing":
        request["input"].pop(1)
    elif failure == "pruned":
        request["input"] = []
    elif failure == "preview":
        request["input"][1]["output"] = "Warning: truncated output\n" + request["input"][1]["output"][:100]
    elif failure == "quoted":
        request["input"] = [{"type": "function_call_output", "output": "Example: " + json.dumps(baseline)}]
    elif failure == "wrong_role":
        request["input"] = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(baseline)}]}]
    elif failure == "boolean_type":
        other = json.loads(json.dumps(baseline))
        other["context"]["enabled"] = 1
        request["input"] = [{"type": "function_call_output", "output": json.dumps(other)}]
    elif failure == "server_history":
        request["previous_response_id"] = "opaque-history"
    elif failure == "bad_page":
        request["input"][1]["output"] = request["input"][1]["output"].replace('"eof": false', '"eof": true')
    elif failure == "shifted_offset":
        page = json.loads(request["input"][1]["output"].split("\nOutput:\n")[1])
        page["offset"] -= 1
        request["input"][1]["output"] = json.dumps(page)
    elif failure == "capture_changed":
        Path(observation["capture_file"]).write_text("{}")
    elif failure == "other_session":
        baseline["target"]["session_id"] = "other"
    elif failure == "other_tool":
        observation["tool_call_id"] = "other"
    elif failure == "ended":
        rows.append({**rows[-1], "seq": 3, "payload": {"type": "tool_call_ended", "tool_call_id": "guard"}})
    elif failure in {"new_inference", "compacted"}:
        rows.append({**rows[0], "seq": 3, "payload": {**rows[0]["payload"], "inference_call_id": "later"}})
    elif failure == "child":
        rows[-1]["thread_id"] = "child"
    elif failure == "code_mode":
        rows[-1]["payload"]["code_mode_runtime_tool_id"] = "nested"
    elif failure == "wrong_response":
        (bundle / "payloads/output.json").write_text(json.dumps({"output_items": []}))
    elif failure == "reordered_trace":
        rows[0], rows[1] = rows[1], rows[0]
    elif failure == "outside_payload":
        rows[0]["payload"]["request_payload"]["path"] = "../input.json"
    (bundle / "payloads/input.json").write_text(json.dumps(request))
    trace = bundle / "trace.jsonl"
    trace.write_text("".join(json.dumps(row) + "\n" for row in rows))
    if failure == "partial_trace":
        trace.write_text(trace.read_text() + '{"partial":')
    elif failure == "missing_trace":
        trace.unlink()
    elif failure == "symlink":
        original = tmp_path / "original.jsonl"
        trace.rename(original)
        trace.symlink_to(original)
    assert not codex_context_in_active_input(observation, baseline=baseline, session_id="session")


@pytest.mark.parametrize("pragma", ["", '// @exec: {"max_output_tokens":12000}\n',
    ' \t// @exec: {"yield_time_ms":1000}\r\n'])
@pytest.mark.parametrize("failure", [None, "parent_missing", "parent_changed", "parent_ended", "parent_wrong_call",
    "child_wrong_cell", "child_wrong_tool", "output_missing", "output_preview", "output_wrong_role",
    "malformed_pragma", "nonobject_pragma", "nonfinite_pragma", "other_comment", "second_pragma"])
def test_code_mode_requires_exact_live_parent_and_complete_model_visible_output(tmp_path, failure, pragma):
    observation, baseline, request, rows = fixture(tmp_path)
    bundle = Path(observation["trace_bundle"])
    source = 'text(await tools.exec_command({cmd:"loopx quota should-run"}));'
    output = {"output_items": [{"type": "custom_tool_call", "name": "exec", "call_id": "parent", "input": pragma + source}]}
    parent = {**rows[-1], "seq": 2, "payload": {"type": "code_cell_started", "runtime_cell_id": "cell",
        "model_visible_call_id": "parent", "source_js": source}}
    rows[-1]["seq"] = 3
    rows[-1]["payload"].update(requester={"type": "code_cell", "runtime_cell_id": "cell"},
        model_visible_call_id=None, code_mode_runtime_tool_id="tool-1")
    rows.insert(2, parent)
    request["input"] = [{"type": "custom_tool_call_output", "call_id": f"read-{i}", "output": [
        {"type": "input_text", "text": "Script completed\nOutput:\n"},
        {"type": "input_text", "text": json.dumps({"chunk_id": "fixture", "exit_code": 0,
            "output": page["output"].split("\nOutput:\n")[1]})}]} for i, page in enumerate(request["input"])]
    if failure == "parent_missing":
        rows.remove(parent)
    elif failure == "parent_changed":
        parent["payload"]["source_js"] += "text('different');"
    elif failure == "parent_ended":
        rows.append({**parent, "seq": 4, "payload": {"type": "code_cell_ended", "runtime_cell_id": "cell"}})
    elif failure == "parent_wrong_call":
        output["output_items"][0]["call_id"] = "different"
    elif failure == "child_wrong_cell":
        rows[-1]["payload"]["requester"]["runtime_cell_id"] = "other-cell"
    elif failure == "child_wrong_tool":
        rows[-1]["payload"]["kind"] = {"type": "other"}
    elif failure == "output_missing":
        request["input"].pop(1)
    elif failure == "output_preview":
        request["input"][1]["output"][1]["text"] = request["input"][1]["output"][1]["text"][:200]
    elif failure == "output_wrong_role":
        for item in request["input"]:
            item["type"] = "message"
            item["role"] = "assistant"
    elif failure in ("malformed_pragma", "nonobject_pragma", "nonfinite_pragma", "other_comment", "second_pragma"):
        prefix = {"malformed_pragma": "// @exec: {broken}\n", "nonobject_pragma": "// @exec: []\n",
            "nonfinite_pragma": '// @exec: {"max_output_tokens":NaN}\n', "other_comment": "// other\n",
            "second_pragma": "// @exec: {}\n// @exec: {}\n"}[failure]
        output["output_items"][0]["input"] = prefix + source
    (bundle / "payloads/output.json").write_text(json.dumps(output))
    (bundle / "payloads/input.json").write_text(json.dumps(request))
    (bundle / "trace.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert codex_context_in_active_input(observation, baseline=baseline, session_id="session") is (failure is None)


@pytest.mark.parametrize("code_mode", [False, True])
@pytest.mark.parametrize("failure", [None, "no_runtime", "other_runtime", "duplicate_runtime",
                                    "unfinished", "late_completion", "ended", "missing_bytes", "wrong_output"])
def test_streamed_tool_needs_complete_response_before_its_live_runtime(tmp_path, code_mode, failure):
    observation, baseline, request, original = fixture(tmp_path)
    bundle = Path(observation["trace_bundle"])
    start, completion, tool = [row["payload"] for row in original]
    payloads = [start]
    if code_mode:
        source = 'text(await tools.exec_command({cmd:"loopx quota should-run"}));'
        payloads.append({"type": "code_cell_started", "runtime_cell_id": "cell",
            "model_visible_call_id": "parent", "source_js": source})
        tool.update(requester={"type": "code_cell", "runtime_cell_id": "cell"},
                    model_visible_call_id=None, code_mode_runtime_tool_id="child")
        output = {"output_items": [{"type": "custom_tool_call", "name": "exec", "call_id": "parent", "input": source}]}
        (bundle / "payloads/output.json").write_text(json.dumps(output))
    payloads.append(tool)
    runtime = {"type": "tool_call_runtime_started", "tool_call_id": "guard"}
    payloads += [completion, runtime]
    if failure == "no_runtime":
        payloads.remove(runtime)
    elif failure == "other_runtime":
        runtime["tool_call_id"] = "other"
    elif failure == "duplicate_runtime":
        payloads.append(dict(runtime))
    elif failure == "unfinished":
        payloads.remove(completion)
    elif failure == "late_completion":
        payloads[-2:] = [runtime, completion]
    elif failure == "ended":
        payloads.append({"type": "tool_call_runtime_ended", "tool_call_id": "guard"})
    elif failure == "missing_bytes":
        request["input"].pop(1)
    elif failure == "wrong_output":
        (bundle / "payloads/output.json").write_text(json.dumps({"output_items": []}))
    (bundle / "payloads/input.json").write_text(json.dumps(request))
    rows = [{**original[0], "seq": i, "payload": payload} for i, payload in enumerate(payloads)]
    (bundle / "trace.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert codex_context_in_active_input(observation, baseline=baseline, session_id="session") is (failure is None)
