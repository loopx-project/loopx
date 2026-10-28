"""Selected details stay pinned to one capture and never acknowledge the full input."""
import argparse
import hashlib
import json
import shlex

import pytest

from loopx.cli_commands.quota_capture import read_context_capture
from loopx.control_plane.turn_driver.decision_capture import capture_page_range
from test_quota_settlement_cli import _run_cli


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
