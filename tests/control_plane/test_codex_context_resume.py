"""Native replay continuity: completed deliveries, real history changes, fallback."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.control_plane.turn_driver import codex_context as context
from tests.control_plane.test_codex_context_lifecycle import setup, project, read_pages


def append(path, kind, payload):
    with path.open("a") as stream:
        stream.write(json.dumps({"type": kind, "payload": payload}) + "\n")


def observed_read(root, monkeypatch, *, complete=True, shell_wrapper=False, lost_prefix=False,
                  history_prefix_mib=0, version="0.162.1", code_mode=False, parent_batch=1):
    event = setup(root, monkeypatch)
    history = root / "rollout.jsonl"
    append(history, "session_meta", {"id": "session", "cwd": str(root),
        "cli_version": version, "history_mode": "paginated"})
    if history_prefix_mib:
        with history.open("ab") as stream:
            stream.write(b'{"type":"event_msg","payload":{"type":"background_event","message":"')
            for _ in range(history_prefix_mib):
                stream.write(b'x' * (1024 * 1024))
            stream.write(b'"}}\n')
    history.chmod(0o600)
    event.update(transcript_path=str(history), model="fixture", permission_mode="bypassPermissions")
    append(history, "turn_context", {"cwd": str(root), "model": "fixture"})
    payload = {"ok": True, "plan_summary": {"requirements": "Keep original evidence. " * 1000},
        "heartbeat_receipt": {"turn_instance_id": "first"}}
    _, first = project(root, payload=payload)
    pages = read_pages(root, first)
    emissions = []
    for i, page in enumerate(pages if complete else pages[:-1]):
        call = f"reader-{i}"
        body = json.dumps(page)
        output = ("Chunk ID: fixture\nWall time: 0.01 seconds\nProcess exited with code 0\n"
            "Original token count: 1000\nOutput:\n" + body) if shell_wrapper else body
        if code_mode:
            # Native code-mode retains the parent emission; the nested hook
            # has a different call id. Its actual output must still cover all bytes.
            if parent_batch == 1:
                append(history, "response_item", {"type": "custom_tool_call_output", "call_id": f"parent-{i}",
                    "output": [{"type": "input_text", "text": output}]})
            else:
                if not emissions:
                    append(history, "response_item", {"type": "custom_tool_call", "call_id": f"parent-{i}"})
                emissions.append({"type": "input_text", "text": output})
        else:
            append(history, "response_item", {"type": "function_call_output", "call_id": call, "output": output})
        if lost_prefix and i == len(pages) - 1:
            rows = [json.loads(line) for line in history.read_text().splitlines()]
            next(row for row in rows if row["type"] == "response_item")["payload"]["output"] = "truncated"
            history.write_text("".join(json.dumps(row) + "\n" for row in rows))
        context.codex_context_hook(str(root), {**event, "hook_event_name": "PostToolUse",
            "tool_use_id": call, "tool_response": body})
        if emissions and (len(emissions) == parent_batch or i == len(pages) - 1):
            # Nested read hooks run before the enclosing Code Mode emits its
            # complete result. Preserve every emission in that native parent.
            append(history, "response_item", {"type": "custom_tool_call_output", "call_id": f"parent-{i}",
                "output": emissions})
            emissions = []
    return event, history, payload


@pytest.mark.parametrize("shell_wrapper", [False, True])
@pytest.mark.parametrize("version", ["0.162.1", "0.163.0-alpha.11.1"])
def test_completed_native_resume_keeps_delta_without_old_capture_prefetch(tmp_path, monkeypatch, shell_wrapper, version):
    event, history, payload = observed_read(tmp_path, monkeypatch, shell_wrapper=shell_wrapper, version=version)
    before = context._state(tmp_path)
    assert before["candidate"]["native_resume"]["inode"].isdigit()
    append(history, "event_msg", {"type": "task_complete"})
    append(history, "event_msg", {"type": "thread_settings_applied", "history_mode": "paginated"})
    result = context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    # No old recovery instruction or extra reader call is needed.
    assert result == {} and context._state(tmp_path)["context_epoch"] == before["context_epoch"]
    current = {**payload, "heartbeat_receipt": {"turn_instance_id": "second"}}
    _, wire = project(tmp_path, "second", payload=current)
    assert wire["mode"] == "delta"
    assert wire["changes"]["values"]["heartbeat_receipt"] == current["heartbeat_receipt"]


@pytest.mark.parametrize("single_page", [False, True])
@pytest.mark.parametrize("lose_first", [False, True])
@pytest.mark.parametrize("code_mode", [False, True])
def test_compact_full_recovery_becomes_next_resume_baseline(tmp_path, monkeypatch, single_page, lose_first, code_mode):
    from argparse import Namespace
    from loopx.cli_commands.quota_capture import read_context_capture

    event, history, payload = observed_read(tmp_path, monkeypatch)
    before = context._state(tmp_path)
    context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PreCompact"})
    append(history, "compacted", {"message": "Summary without captured source bodies."})
    context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PostCompact"})
    recovery_instruction = context.codex_context_hook(str(tmp_path), {**event, "source": "compact"})
    invalid = context._state(tmp_path)
    assert "candidate" not in invalid and "pending" not in invalid
    assert invalid["context_epoch"] != before["context_epoch"]
    assert invalid["recovery"] == before["recovery"]
    assert invalid["recovery"]["command"] in str(recovery_instruction)

    reference = invalid["recovery"]
    offset, index = 0, 0
    while True:
        page = read_context_capture(Namespace(capture_file=reference["path"], capture_sha256=reference["sha256"],
            offset=offset, page_bytes=64000 if single_page else 4096,
            max_page_output_bytes=64000 if single_page else None, format="json"))
        assert page["ok"]
        call = f"recovery-{index}"
        if code_mode:
            # The nested hook fires before its parent emits the retained output.
            append(history, "response_item", {"type": "custom_tool_call", "call_id": f"parent-{call}"})
        else:
            append(history, "response_item", {"type": "function_call", "call_id": call})
        retained = "truncated" if lose_first and index == 0 else json.dumps(page)
        if not code_mode:
            append(history, "response_item", {"type": "function_call_output", "call_id": call, "output": retained})
        context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PostToolUse",
            "tool_use_id": call, "tool_response": json.dumps(page)})
        if code_mode:
            append(history, "response_item", {"type": "custom_tool_call_output", "call_id": f"parent-{call}",
                "output": [{"type": "input_text", "text": retained}]})
        index += 1
        if page["eof"]:
            break
        offset = page["next_offset"]
    assert (index == 1) == single_page
    recovered = context._state(tmp_path)
    assert recovered["candidate"]["capture_sha256"] == reference["sha256"]
    assert recovered["candidate"]["target"]["context_epoch"] == invalid["context_epoch"]
    append(history, "event_msg", {"type": "task_complete"})
    context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    current = {**payload, "heartbeat_receipt": {"turn_instance_id": "after-compact"}}
    _, wire = project(tmp_path, "after-compact", payload=current)
    assert wire["mode"] == ("full" if lose_first else "delta")
    if not lose_first:
        assert wire["changes"]["values"]["heartbeat_receipt"] == current["heartbeat_receipt"]


@pytest.mark.parametrize("change", ["compact", "rollback", "retained", "unknown", "truncate",
    "replace", "rewrite", "missing", "output", "duplicate", "model", "workspace", "session", "permission", "writable", "malformed"])
@pytest.mark.parametrize("version", ["0.162.1", "0.163.0-alpha.11.1"])
def test_resume_loss_or_unknown_continuity_falls_back_to_current_full(tmp_path, monkeypatch, change, version):
    event, history, payload = observed_read(tmp_path, monkeypatch, version=version)
    before = context._state(tmp_path)
    if change in {"compact", "retained", "unknown"}:
        append(history, {"compact": "compacted", "retained": "retained_context", "unknown": "future_history"}[change], {})
    elif change == "rollback":
        append(history, "event_msg", {"type": "thread_rolled_back", "num_turns": 1})
    elif change == "truncate":
        history.write_bytes(history.read_bytes()[:100])
    elif change == "replace":
        replacement = tmp_path / "replacement.jsonl"
        replacement.write_bytes(history.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(history)
    elif change == "rewrite":
        history.write_bytes(history.read_bytes().replace(b"Keep original", b"Drop original"))
    elif change == "missing":
        history.unlink()
    elif change == "writable":
        history.chmod(0o664)
    elif change == "malformed":
        context._save(tmp_path, {**before, "candidate": "corrupt"})
    elif change in {"output", "duplicate"}:
        rows = [json.loads(line) for line in history.read_text().splitlines()]
        if change == "output":
            rows[-1]["payload"]["output"] = "truncated"
        else:
            rows.append(rows[-1])
        history.write_text("".join(json.dumps(row) + "\n" for row in rows))
    else:
        event[{"workspace": "cwd", "session": "session_id", "permission": "permission_mode"}.get(change, change)] = "different"
    context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    after = context._state(tmp_path)
    assert "candidate" not in after and after["context_epoch"] != before["context_epoch"]
    if change not in {"session", "workspace"}:
        assert after["recovery"] == before["recovery"]
        _, wire = project(tmp_path, "second", payload=payload)
        assert wire["mode"] == "full" and wire["context"] == payload


@pytest.mark.parametrize("version", ["0.163.0", "0.163.0-alpha.11.2", "unknown"])
def test_unqualified_native_version_cannot_retain_resume_baseline(tmp_path, monkeypatch, version):
    event, _, payload = observed_read(tmp_path, monkeypatch, version=version)
    before = context._state(tmp_path)
    assert "native_resume" not in before["candidate"]
    context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    assert context._state(tmp_path)["context_epoch"] != before["context_epoch"]
    _, wire = project(tmp_path, "second", payload=payload)
    assert wire["mode"] == "full" and wire["context"] == payload


def test_incomplete_native_read_cannot_gain_resume_continuity(tmp_path, monkeypatch):
    event, _, payload = observed_read(tmp_path, monkeypatch, complete=False)
    context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    _, wire = project(tmp_path, "second", payload=payload)
    assert wire["mode"] == "full" and wire["context"] == payload


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Named pipes require POSIX")
@pytest.mark.parametrize("phase", ["resume", "post_tool"])
@pytest.mark.parametrize("enabled", [False, True])
def test_real_hook_rejects_fifo_history_without_waiting_for_a_writer(tmp_path, monkeypatch, phase, enabled):
    event, history, payload = observed_read(tmp_path, monkeypatch)
    assert "native_resume" in context._state(tmp_path)["candidate"]
    fields = {"hook_event_name": "SessionStart", "source": "resume"}
    if phase == "post_tool":
        current = {**payload, "heartbeat_receipt": {"turn_instance_id": "second"}}
        _, wire = project(tmp_path, "second", payload=current)
        assert wire["mode"] == "delta"
        pages = read_pages(tmp_path, wire, size=8192)
        assert len(pages) == 1 and pages[0]["eof"]
        fields = {"hook_event_name": "PostToolUse", "tool_use_id": "second-reader",
                  "tool_response": json.dumps(pages[0])}

    history.unlink()
    os.mkfifo(history, 0o600)  # No writer: opening this source must not block before validation.
    journal = tmp_path / "session.json"
    original = journal.read_bytes()
    before = context._state(tmp_path)
    reference = before["recovery"]
    captured = Path(reference["path"]).read_bytes()
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    cli = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
           "--runtime-root", str(runtime), "quota", "context-hook", "--context-delivery",
           "on" if enabled else "off", "--codex-app", "--codex-context-dir", str(tmp_path)]

    def hook(values):
        result = subprocess.run(cli, input=json.dumps({**event, **values}), capture_output=True,
            text=True, timeout=5, cwd=Path(__file__).resolve().parents[2])
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    result = hook(fields)
    if not enabled:
        assert result == {} and journal.read_bytes() == original
    else:
        if phase == "post_tool":
            assert result == {}
            # Complete page delivery can still support this process, but the
            # unreadable history cannot certify a cross-process replay.
            candidate = context._state(tmp_path)["candidate"]
            assert candidate["capture_bytes_verified"] is True and "native_resume" not in candidate
            result = hook({"hook_event_name": "SessionStart", "source": "resume"})
        after = context._state(tmp_path)
        assert "candidate" not in after and "pending" not in after
        assert after["context_epoch"] != before["context_epoch"]
        assert after["recovery"] == reference
        assert reference["command"] in result["hookSpecificOutput"]["additionalContext"]
        _, fallback = project(tmp_path, "after-fifo", payload=payload)
        assert fallback["mode"] == "full" and fallback["context"] == payload
    assert Path(reference["path"]).read_bytes() == captured
    assert not registry.exists() and not runtime.exists()


def test_complete_eof_cannot_hide_a_prior_page_missing_from_native_replay(tmp_path, monkeypatch):
    event, _, payload = observed_read(tmp_path, monkeypatch, lost_prefix=True)
    context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    _, wire = project(tmp_path, "second", payload=payload)
    assert wire["mode"] == "full" and wire["context"] == payload


@pytest.mark.parametrize("prefix_mib", [0, 96])
def test_unrelated_history_size_does_not_change_replay_or_decode_cost(tmp_path, monkeypatch, prefix_mib):
    import tracemalloc
    from loopx.control_plane.turn_driver import codex_context_history as history_io

    event, history, payload = observed_read(tmp_path, monkeypatch, history_prefix_mib=prefix_mib)
    before = context._state(tmp_path)
    assert before["candidate"]["native_resume"]["reader_start"] > prefix_mib * 1024 * 1024
    append(history, "event_msg", {"type": "task_complete"})
    tracemalloc.start()
    try:
        assert history_io.native_resume_verified(before, {**event, "source": "resume"})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024  # no history-sized allocation, including above the former 64 MiB cutoff
    context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    _, wire = project(tmp_path, "second", payload=payload)
    assert wire["mode"] == "delta"


def test_old_version_can_resume_current_verified_reader_format(tmp_path, monkeypatch):
    event, _, payload = observed_read(tmp_path, monkeypatch, version="0.153.4")
    context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    _, wire = project(tmp_path, "second", payload=payload)
    assert wire["mode"] == "delta"


@pytest.mark.parametrize("lose_page", [False, True])
@pytest.mark.parametrize("parent_batch", [1, 2])
def test_code_mode_retained_emissions_need_complete_native_read_coverage(tmp_path, monkeypatch, lose_page, parent_batch):
    event, history, payload = observed_read(tmp_path, monkeypatch, code_mode=True, parent_batch=parent_batch)
    if lose_page:
        rows = [json.loads(line) for line in history.read_text().splitlines()]
        outputs = [row for row in rows if row["type"] == "response_item"
                   and row["payload"].get("type") == "custom_tool_call_output"]
        outputs[-1]["payload"]["output"] = outputs[-1]["payload"]["output"][1:]
        history.write_text("".join(json.dumps(row) + "\n" for row in rows))
    context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    _, wire = project(tmp_path, "second", payload=payload)
    assert wire["mode"] == ("full" if lose_page else "delta")
