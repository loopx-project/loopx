"""Selected details stay pinned to one capture and never acknowledge the full input."""
import argparse
import hashlib
import json
import shlex
from pathlib import Path

import pytest

from tests.control_plane.codex_checkpoint_fixture import NativeContextReader, last_context_page
from loopx.cli_commands.quota_capture import read_context_capture
from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError
from loopx.control_plane.turn_driver.decision_capture import capture_page_range
from test_quota_settlement_cli import _run_cli


@pytest.mark.parametrize("wrapped", [False, True])
def test_native_saved_reference_is_not_reader_coverage(wrapped):
    reference = {"ok": True, "mode": "full", "path": "saved.json", "sha256": "digest",
                 "command": "read saved.json", "target": {}, "recovery": {}}
    output = json.dumps(reference)
    if wrapped:
        output = [{"type": "input_text", "text": json.dumps({"exit_code": 0, "output": output})}]
    item = {"type": "custom_tool_call_output" if wrapped else "function_call_output", "output": output}
    request = {"input": [item]}
    assert last_context_page(request) is None
    assert last_context_page(request, allow_reference=True) == reference
    reader = NativeContextReader()
    reader.begin("admit current Turn", "synthetic")
    call = reader.follow(request)
    assert json.loads(call["arguments"])["cmd"] == reference["command"]
    assert not reader.deliveries and not reader.retained
    assert reader.pending["page_calls"] == 0
    # A damaged current response cannot borrow the earlier complete reference.
    request["input"].append({"type": "function_call_output", "output": '{"ok":true'})
    assert last_context_page(request, allow_reference=True) is None
    with pytest.raises(AssertionError, match="output must reach the model intact"):
        reader.follow(request)


def fixture_capture(tmp_path):
    original = {"rows": [{"text": "保留末尾约束🙂" * 90, "tail": "stop if unavailable"}],
        "a/b": {"~": None}, "large": 9007199254740993, "optional": "other evidence"}
    raw = (json.dumps(original, ensure_ascii=False) + "\n").encode()
    path = tmp_path / "capture with ' quote.json"
    path.write_bytes(raw)
    return path, raw, original


def args(path, raw):
    return argparse.Namespace(**dict(capture_file=str(path), capture_sha256=hashlib.sha256(raw).hexdigest(),
        offset=0, page_bytes=256, max_page_output_bytes=2000, format="json", select=["/rows/0"]))


def test_native_reference_reuses_the_exact_index_before_selecting_details(tmp_path):
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import native_context_capture_view

    original = {"requirements": {"text": "Preserve acceptance and stop conditions. " * 100},
        "a/b~": [None, True, 9007199254740993], "nullable": None}
    reference = native_context_capture_view(original, tmp_path)
    parser = build_parser()
    indexed = read_context_capture(parser.parse_args(shlex.split(reference["index_command"])[1:]))
    assert indexed["ok"] and indexed["eof"]
    assert reference["field_index"] == json.loads(indexed["content"])["selections"][0]["fields"]
    assert [item["pointer"] for item in reference["field_index"]] == ["/requirements", "/a~1b~0", "/nullable"]
    assert "Preserve acceptance" not in json.dumps(reference)
    assert "content" not in reference and "eof" not in reference
    assert capture_page_range(reference, raw=b"", digest=reference["sha256"]) is None
    # The inline index replaces only discovery, never the content read.
    selected = read_context_capture(parser.parse_args(shlex.split(reference["recovery"]["command"])[1:]
        + ["--select", "/a~1b~0"]))
    assert selected["ok"]
    assert json.loads(selected["content"])["selections"][0]["value"] == original["a/b~"]


def test_large_inline_index_is_omitted_whole_without_displacing_navigation(tmp_path):
    from loopx.cli_commands.quota_capture import native_context_capture_view

    original = {"field-" + str(index): {"preserve": index} for index in range(300)}
    reference = native_context_capture_view(original, tmp_path)
    assert "field_index" not in reference
    assert "recovery" in reference and "index_command" in reference and "select_command" in reference
    assert len(json.dumps(reference, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 7900
    assert json.loads(Path(reference["recovery"]["path"]).read_text()) == original


def test_unavailable_optional_index_keeps_file_only_recovery(tmp_path, monkeypatch):
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import native_context_capture_view
    from loopx.control_plane.effect_runtime import EffectRuntimeStartupError
    from loopx.control_plane.quota import turn_envelope

    def unavailable(*args, **kwargs):
        raise EffectRuntimeStartupError("Synthetic typed runtime unavailable", diagnostic_code="fixture_unavailable")

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", unavailable)
    original = {"requirements": "Keep every requirement and stop condition."}
    reference = native_context_capture_view(original, tmp_path)
    assert reference["ok"] and "field_index" not in reference
    page = read_context_capture(build_parser().parse_args(shlex.split(reference["recovery"]["command"])[1:]))
    assert page["ok"] and json.loads(page["content"]) == original


def test_native_reference_reader_uses_real_pages_before_recording_delivery(tmp_path):
    path, raw, _ = fixture_capture(tmp_path)
    options = args(path, raw)
    options.select = None
    reference = {"ok": True, "mode": "full", "path": str(path),
        "sha256": options.capture_sha256, "command": "read saved capture",
        "target": {"capture_id": "synthetic-source"}, "recovery": {"path": str(path)}}
    request = {"input": [{"type": "function_call_output", "output": json.dumps(reference)}]}
    reader = NativeContextReader()
    reader.begin("admit current Turn", "source")
    reader.follow(request)
    page_count = 0
    while True:
        page = read_context_capture(options)
        assert page["ok"] and "mode" not in page and "target" not in page
        request["input"].append({"type": "function_call_output", "output": json.dumps(page)})
        call = reader.follow(request)
        page_count += 1
        if page["eof"]:
            assert call is None
            break
        assert not reader.deliveries and not reader.retained
        assert json.loads(call["arguments"])["cmd"] == page["next_command"]
        options.offset = page["next_offset"]
    assert page_count > 1
    assert reader.deliveries[0]["page_calls"] == page_count
    assert reader.deliveries[0]["mode"] == "full"
    assert reader.retained["synthetic-source"]["raw"] == raw.decode().rstrip("\n")


def test_actual_cli_batch_pages_preserve_values_without_live_state(tmp_path):
    path, raw, original = fixture_capture(tmp_path)
    digest = hashlib.sha256(raw).hexdigest()
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    command = ["quota", "context-read", "--capture-file", str(path), "--capture-sha256", digest,
        "--select", "/rows/0", "--select", "/large", "--select", "/a~1b/~0",
        "--page-bytes", "256", "--max-page-output-bytes", "2000"]
    chunks, offset, selection_digest = [], 0, None
    while True:
        code, page = _run_cli(registry, runtime, *command, cwd=tmp_path)
        assert code == 0 and page["ok"], page
        assert page["sha256"] == digest and page["offset"] == offset
        selection_digest = selection_digest or page["selection_sha256"]
        assert page["selection_sha256"] == selection_digest
        assert capture_page_range(page, raw=raw, digest=digest) is None
        assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 2000
        chunks.append(page["content"].encode())
        offset += len(chunks[-1])
        if page["eof"]:
            assert page["next_offset"] is None and offset == page["total_bytes"]
            break
        assert page["next_offset"] == offset
        command = shlex.split(page["next_command"])[3:]
        assert "--selection-sha256" in command
    combined = b"".join(chunks)
    assert hashlib.sha256(combined).hexdigest() == selection_digest
    assert json.loads(combined) == {"schema_version": "context_capture_selection_v1", "selections": [
        {"pointer": "/a~1b/~0", "value": None}, {"pointer": "/large", "value": original["large"]},
        {"pointer": "/rows/0", "value": original["rows"][0]}]}
    assert path.read_bytes() == raw and not registry.exists() and not runtime.exists()


@pytest.mark.parametrize("failure", ["missing", "changed_selection", "changed_capture", "wrong_digest", "no_digest", "no_selection"])
def test_selection_continuations_fail_closed(tmp_path, failure):
    path, raw, _ = fixture_capture(tmp_path)
    request = args(path, raw)
    first = read_context_capture(request)
    assert first["ok"] and not first["eof"], first
    request.offset = first["next_offset"]
    request.selection_sha256 = first["selection_sha256"]
    if failure == "missing":
        request.select = ["/rows/0", "/missing"]
    elif failure == "changed_selection":
        request.select = ["/rows"]
    elif failure == "changed_capture":
        path.write_bytes(raw.replace(b"stop if unavailable", b"different condition"))
    elif failure == "wrong_digest":
        request.selection_sha256 = "0" * 64
    elif failure == "no_digest":
        request.selection_sha256 = None
    else:
        request.select = None
    result = read_context_capture(request)
    assert result["ok"] is False and "content" not in result


def test_root_selection_is_explicit_detail_and_default_read_stays_raw(tmp_path):
    path, raw, original = fixture_capture(tmp_path)
    request = args(path, raw)
    request.select = [""]
    request.page_bytes = 64000
    request.max_page_output_bytes = 64000
    page = read_context_capture(request)
    assert page["ok"] and page["eof"] and page["selected_pointers"] == [""]
    assert json.loads(page["content"])["selections"] == [{"pointer": "", "value": original}]
    assert capture_page_range(page, raw=raw, digest=request.capture_sha256) is None
    request.select = None
    full = read_context_capture(request)
    assert full["content"].encode() == raw and "selection_sha256" not in full
    assert capture_page_range(full, raw=raw, digest=request.capture_sha256) == (0, len(raw))
    # Even a forged detail marker on byte-identical full content cannot promote it.
    assert capture_page_range({**full, "selection_sha256": "0" * 64}, raw=raw, digest=request.capture_sha256) is None


def test_native_hook_does_not_promote_selected_eof(tmp_path, monkeypatch):
    from loopx.control_plane.turn_driver import codex_context as context
    from tests.control_plane.test_codex_context_lifecycle import setup, project

    event = setup(tmp_path, monkeypatch)
    _, delivery = project(tmp_path)
    reference = delivery["recovery"]
    request = argparse.Namespace(capture_file=reference["path"], capture_sha256=reference["sha256"],
        offset=0, page_bytes=64000, max_page_output_bytes=64000, format="json", select=[""])
    page = read_context_capture(request)
    assert page["ok"] and page["eof"]
    before = context._state(tmp_path)
    context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PostToolUse",
        "tool_use_id": "selected-detail", "tool_response": json.dumps(page)})
    after = context._state(tmp_path)
    assert "candidate" not in after and after["recovery"] == before["recovery"]
    assert after["context_epoch"] == before["context_epoch"] and after["pending"] == before["pending"]


def test_index_is_a_paginated_directory_not_a_read_receipt(tmp_path):
    path, raw, original = fixture_capture(tmp_path)
    request = args(path, raw)
    request.select = ["", "/rows", "/a~1b", "/large"]
    request.list_fields = True
    combined = b""
    first = None
    while True:
        page = read_context_capture(request)
        assert page["ok"] and page["offset"] == len(combined), page
        assert page["list_fields"] and capture_page_range(page, raw=raw, digest=request.capture_sha256) is None
        first = first or page
        combined += page["content"].encode()
        if page["eof"]:
            break
        assert "--list" in page["next_command"]
        request.offset, request.selection_sha256 = page["next_offset"], page["selection_sha256"]
    directory = json.loads(combined)
    assert directory["schema_version"] == "context_capture_index_v1"
    groups = {row["pointer"]: row["fields"] for row in directory["selections"]}
    assert groups["/large"] == []  # listing a scalar never leaks its value
    assert groups["/rows"][0]["pointer"] == "/rows/0"
    assert groups["/a~1b"] == [{"pointer": "/a~1b/~0", "type": "null", "bytes": 4}]
    fields = {row["pointer"]: row for row in groups[""]}
    assert fields["/large"] == {"pointer": "/large", "type": "number", "bytes": len(str(original["large"]))}
    assert fields["/rows"]["count"] == 1
    assert original["rows"][0]["text"] not in combined.decode()
    # A cursor from an index cannot be replayed as a selected-value cursor.
    request.list_fields = False
    request.offset, request.selection_sha256 = first["next_offset"], first["selection_sha256"]
    assert read_context_capture(request)["ok"] is False


def test_cli_index_defaults_to_root_without_registry_or_runtime(tmp_path):
    path, raw, _ = fixture_capture(tmp_path)
    registry, runtime = tmp_path / "missing.json", tmp_path / "missing-runtime"
    code, page = _run_cli(registry, runtime, "quota", "context-read", "--capture-file", str(path),
        "--capture-sha256", hashlib.sha256(raw).hexdigest(), "--list", cwd=tmp_path)
    assert code == 0 and page["eof"], page
    assert page["selected_pointers"] == [""]
    assert not registry.exists() and not runtime.exists()


def test_cli_nested_index_preserves_depth_and_digest_across_pages(tmp_path):
    path, raw, original = fixture_capture(tmp_path)
    registry, runtime = tmp_path / "absent.json", tmp_path / "absent-runtime"
    command = ["quota", "context-read", "--capture-file", str(path),
        "--capture-sha256", hashlib.sha256(raw).hexdigest(), "--list", "--list-depth", "3",
        "--page-bytes", "256", "--max-page-output-bytes", "2000"]
    combined, first, offset = b"", None, 0
    while True:
        code, page = _run_cli(registry, runtime, *command, cwd=tmp_path)
        assert code == 0 and page["ok"], page
        assert page["offset"] == offset
        assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 2000
        assert capture_page_range(page, raw=raw, digest=page["sha256"]) is None
        first = first or page
        assert first["selection_sha256"] == page["selection_sha256"]
        combined += page["content"].encode()
        offset = len(combined)
        if page["eof"]:
            assert offset == page["total_bytes"]
            break
        command = shlex.split(page["next_command"])[3:]
        assert command[command.index("--list-depth") + 1] == "3"
    fields = {item["pointer"]: item for item in json.loads(combined)["selections"][0]["fields"]}
    assert fields["/rows/0/tail"]["type"] == "string"
    assert fields["/a~1b/~0"]["type"] == "null"
    assert original["rows"][0]["tail"] not in combined.decode()
    assert hashlib.sha256(combined).hexdigest() == first["selection_sha256"]
    # Changing traversal depth cannot reuse the prior view's continuation.
    request = args(path, raw)
    request.select, request.list_fields, request.list_depth = [""], True, 1
    request.offset, request.selection_sha256 = first["next_offset"], first["selection_sha256"]
    assert read_context_capture(request)["ok"] is False
    assert path.read_bytes() == raw and not registry.exists() and not runtime.exists()


@pytest.mark.parametrize("options", [
    ["context-read", "--list-depth", "2"],
    ["context-read", "--list", "--list-depth", "0"],
    ["context-read", "--list", "--list-depth", "9"],
    ["should-run", "--list-depth", "2"],
])
def test_cli_rejects_invalid_depth_before_authority(tmp_path, options):
    registry, runtime = tmp_path / "absent.json", tmp_path / "absent-runtime"
    code, page = _run_cli(registry, runtime, "quota", *options, cwd=tmp_path)
    assert code != 0 and page["ok"] is False
    assert "list-depth" in page["reason"]
    assert not registry.exists() and not runtime.exists()


@pytest.mark.parametrize("pointer,token", [("/rows/0/missing", 3), ("/rows/9", 2)])
def test_cli_missing_batch_pointer_identifies_original_argument_without_source_leak(tmp_path, pointer, token):
    path, raw, _ = fixture_capture(tmp_path)
    registry, runtime = tmp_path / "absent.json", tmp_path / "absent-runtime"
    code, result = _run_cli(registry, runtime, "quota", "context-read",
        "--capture-file", str(path), "--capture-sha256", hashlib.sha256(raw).hexdigest(),
        "--select", "/rows", "--select", "/rows", "--select", pointer, cwd=tmp_path)
    assert code != 0 and result["ok"] is False and "content" not in result
    assert f"--select argument 3, token {token}" in result["reason"]
    assert pointer not in result["reason"] and "stop if unavailable" not in json.dumps(result)
    assert path.read_bytes() == raw and not registry.exists() and not runtime.exists()


def test_cli_repairs_missing_selection_using_typed_error_and_same_capture(tmp_path):
    path, raw, original = fixture_capture(tmp_path)
    registry, runtime = tmp_path / "absent.json", tmp_path / "absent-runtime"
    command = ["quota", "context-read", "--capture-file", str(path),
        "--capture-sha256", hashlib.sha256(raw).hexdigest()]
    code, failure = _run_cli(registry, runtime, *command,
        "--select", "/large", "--select", "/rows/0/missing", cwd=tmp_path)
    assert code != 0 and failure["ok"] is False and "content" not in failure
    assert failure["error_kind"] == "request_rejected"
    assert failure["diagnostic_code"] == "context_selection_missing_pointer"
    assert "--list" in failure["recovery"] and "Restore" not in failure["recovery"]
    # Branch on the typed cause, not wording in reason; discover and retry
    # against the original capture without another guard or admission.
    code, index = _run_cli(registry, runtime, *command, "--select", "/rows/0", "--list", cwd=tmp_path)
    assert code == 0 and index["ok"] and index["eof"], index
    fields = json.loads(index["content"])["selections"][0]["fields"]
    pointer = next(field["pointer"] for field in fields if field["pointer"] == "/rows/0/tail")
    code, selected = _run_cli(registry, runtime, *command, "--select", pointer, cwd=tmp_path)
    assert code == 0 and selected["ok"] and selected["eof"], selected
    assert json.loads(selected["content"])["selections"] == [
        {"pointer": pointer, "value": original["rows"][0]["tail"]}]
    assert capture_page_range(selected, raw=raw, digest=selected["sha256"]) is None
    assert path.read_bytes() == raw and not registry.exists() and not runtime.exists()


@pytest.mark.parametrize("kind,code", [
    ("request_rejected", "request_too_large"),
    ("io_transient", "io_busy"),
    ("io_permanent", "io_permission_denied"),
    ("internal_failure", "unexpected_handler_error"),
])
def test_reader_preserves_runtime_diagnostics_without_parsing_reason(tmp_path, monkeypatch, kind, code):
    from loopx.control_plane.quota import turn_envelope

    path, raw, _ = fixture_capture(tmp_path)

    def fail(*_args, **_kwargs):
        raise EffectRuntimeRemoteError("same opaque reason", error_kind=kind, diagnostic_code=code)

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", fail)
    result = read_context_capture(args(path, raw))
    assert result["ok"] is False and "content" not in result and "next_command" not in result
    assert result["error_kind"] == kind and result["diagnostic_code"] == code
    assert result["reason"] == "same opaque reason"
    assert "Restore" not in result["recovery"]
    assert ("--list" in result["recovery"]) is (kind == "request_rejected")
    assert path.read_bytes() == raw


def test_runtime_startup_failure_does_not_request_capture_restoration(tmp_path, monkeypatch):
    from loopx.control_plane.quota import turn_envelope

    path, raw, _ = fixture_capture(tmp_path)

    def fail(*_args, **_kwargs):
        raise EffectRuntimeStartupError("runtime unavailable", diagnostic_code="runtime_startup_timeout")

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", fail)
    result = read_context_capture(args(path, raw))
    assert result["ok"] is False and "content" not in result
    assert result["diagnostic_code"] == "runtime_startup_timeout"
    assert "runtime" in result["recovery"] and "Restore" not in result["recovery"]
    assert path.read_bytes() == raw


@pytest.mark.parametrize("damage", ["missing", "digest", "json"])
def test_capture_failure_cannot_be_repaired_as_a_selector_error(tmp_path, monkeypatch, damage):
    from loopx.control_plane.quota import turn_envelope

    path, raw, _ = fixture_capture(tmp_path)
    request = args(path, raw)
    if damage == "missing":
        path.unlink()
    elif damage == "digest":
        path.write_bytes(b"{}")
    else:
        path.write_bytes(b"{broken")
        request.capture_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    def unexpected(*_args, **_kwargs):
        pytest.fail("invalid captures must not reach selection")

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", unexpected)
    result = read_context_capture(request)
    assert result["ok"] is False and "content" not in result and "next_command" not in result
    assert "diagnostic_code" not in result and "error_kind" not in result
    assert "Restore the original digest-bound capture" in result["recovery"]
