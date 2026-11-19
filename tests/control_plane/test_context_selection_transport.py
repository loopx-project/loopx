"""Selection planning may omit scalar prose; captured values and navigation stay exact."""
import argparse
import hashlib
import json

import pytest

from loopx.cli_commands.quota_capture import read_context_capture
from loopx.control_plane.quota import turn_envelope


def captured_reader(tmp_path, value, **options):
    raw = json.dumps(value, ensure_ascii=False).encode()
    path = tmp_path / "capture.json"
    path.write_bytes(raw)
    args = argparse.Namespace(capture_file=str(path), capture_sha256=hashlib.sha256(raw).hexdigest(),
        offset=0, page_bytes=64000, max_page_output_bytes=64000, format="json",
        select=options.pop("select", None), **options)
    return path, raw, args


@pytest.fixture
def transported_contexts(monkeypatch):
    observed = []
    original = turn_envelope._turn_envelope_result

    def record(operation, **request):
        if operation == "context_select":
            observed.append(request["context"])
        return original(operation, **request)

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", record)
    return observed


def test_explicit_reader_transports_less_prose_and_returns_original_values(tmp_path, transported_contexts):
    text = "Keep the complete final stop. 保留末尾约束🙂 " * 90
    value = {"rows": [{"text": text, "small": "ab", "empty": {}, "array": []}],
        "a/b": {"~": None}, "numbers": [0, False, None, -0.0, 9007199254740993],
        "optional": "Unselected historical supporting material. " * 200}
    path, raw, args = captured_reader(tmp_path, value, select=["/rows/0", "/a~1b/~0", "/numbers"])
    page = read_context_capture(args)
    assert page["ok"] and page["eof"], page
    selected = {row["pointer"]: row["value"] for row in json.loads(page["content"])["selections"]}
    assert selected == {"/rows/0": value["rows"][0], "/a~1b/~0": None, "/numbers": value["numbers"]}
    assert "-0.0" in page["content"] and "9007199254740993" in page["content"]
    assert path.read_bytes() == raw
    assert len(transported_contexts) == 1
    transported = transported_contexts[0]
    assert len(json.dumps(transported, ensure_ascii=False).encode()) < len(raw) // 2
    # The planning transport carries addresses; exact scalar values above come
    # from the independently verified original, including non-JS-safe numbers.
    assert text not in json.dumps(transported, ensure_ascii=False)


@pytest.mark.parametrize("mode", ["initial", "root_index", "nested_index"])
def test_navigation_preserves_original_values_and_index_metadata(tmp_path, transported_contexts, mode):
    instruction = "Preserve all current requirements and stop before publication. " * 10
    value = {"ok": True, "goal_id": "synthetic",
        "interaction_contract": {"schema_version": "loopx_interaction_contract_v0",
            "agent_channel": {"work_context": {"instruction": instruction, "sources": []}}}}
    options = {"initial_read": True} if mode == "initial" else {
        "list_fields": True, "select": [""] if mode == "root_index" else ["/interaction_contract"]}
    path, raw, args = captured_reader(tmp_path, value, **options)
    page = read_context_capture(args)
    assert page["ok"] and page["eof"], page
    assert path.read_bytes() == raw
    if mode == "initial":
        assert transported_contexts == [value]
        assert instruction in page["content"]
    else:
        parent = value if mode == "root_index" else value["interaction_contract"]
        prefix = "" if mode == "root_index" else "/interaction_contract"
        fields = json.loads(page["content"])["selections"][0]["fields"]
        expected = {}
        for key, item in parent.items():
            pointer = prefix + "/" + key
            field = {"pointer": pointer,
                     "type": "object" if isinstance(item, dict) else "boolean" if isinstance(item, bool) else "string",
                     "bytes": len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode())}
            if isinstance(item, dict):
                field["count"] = len(item)
            expected[pointer] = field
        assert {field["pointer"]: field for field in fields} == expected
        if mode == "root_index":
            assert transported_contexts == [value]
        else:
            assert instruction not in json.dumps(transported_contexts, ensure_ascii=False)


def test_short_scalar_arrays_do_not_grow_the_planning_payload(tmp_path, transported_contexts):
    value = {"values": [0, 1, False, True, None, "", "a", "ab"] * 60}
    _, raw, args = captured_reader(tmp_path, value, select=["/values"])
    page = read_context_capture(args)
    assert page["ok"] and page["eof"], page
    assert len(transported_contexts) == 1
    assert len(json.dumps(transported_contexts[0], ensure_ascii=False).encode()) <= len(raw)
    assert json.loads(page["content"])["selections"][0]["value"] == value["values"]


def test_deep_selection_keeps_the_existing_json_reader_depth(tmp_path, transported_contexts):
    text = "Preserve the original deep tail constraint. " * 20
    value = text
    for _ in range(650):
        value = {"v": value}
    _, _, args = captured_reader(tmp_path, value, select=["/v" * 650])
    page = read_context_capture(args)
    assert page["ok"] and page["eof"], page
    assert json.loads(page["content"])["selections"][0]["value"] == text
    assert len(transported_contexts) == 1


def test_full_reader_stays_independent_of_selection_runtime(tmp_path, transported_contexts):
    value = {"stop": "Keep the complete original document.", "unknown": {"enabled": False}}
    _, raw, args = captured_reader(tmp_path, value)
    page = read_context_capture(args)
    assert page["ok"] and page["eof"] and page["content"].encode() == raw
    assert transported_contexts == []
