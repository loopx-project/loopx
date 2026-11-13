"""Native replay continuity: completed deliveries, real history changes, fallback."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.cli_commands.quota_capture import model_context_reference, native_context_capture_view
from loopx.control_plane.quota.turn_envelope import restore_context_delivery
from loopx.control_plane.turn_driver import codex_context as context
from tests.control_plane.test_codex_context_lifecycle import project, read_pages, setup


def assert_file_delta(wire, previous, expected):
    baseline = {"schema_version": "context_delivery_v1", "mode": "full", "ancestry": 0,
        "target": previous["target"], "context": json.loads(Path(previous["path"]).read_text())}
    assert wire["mode"] == "delta" and wire["continuity_basis"] == "captured_file"
    restored = restore_context_delivery(wire, baseline=baseline,
        current={"schema_version": "context_delivery_v1", "mode": "full", "ancestry": 0,
                 "target": wire["target"], "context": expected})
    assert restored["mode"] == "delta" and restored["context"] == expected


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
    event.update(transcript_path=str(history), model="fixture", permission_mode="bypassPermissions",
                 turn_id="read-turn")
    append(history, "event_msg", {"type": "task_started", "turn_id": event["turn_id"]})
    append(history, "turn_context", {"cwd": str(root), "model": "fixture"})
    payload = {"ok": True, "plan_summary": {"requirements": "Keep original evidence. " * 1000},
        "heartbeat_receipt": {"turn_instance_id": "first"}}
    _, first = project(root, payload=payload)
    pages = read_pages(root, first)
    emissions, parent = [], None
    for i, page in enumerate(pages if complete else pages[:-1]):
        call = f"reader-{i}"
        body = json.dumps(page)
        output = ("Chunk ID: fixture\nWall time: 0.01 seconds\nProcess exited with code 0\n"
            "Original token count: 1000\nOutput:\n" + body) if shell_wrapper else body
        if code_mode:
            # Native code-mode retains the parent emission; the nested hook
            # has a different call id. Its actual output must still cover all bytes.
            if not emissions:
                parent = f"parent-{i}"
                append(history, "response_item", {"type": "custom_tool_call", "call_id": parent, "name": "exec"})
            append(history, "event_msg", {"type": "item_completed", "turn_id": event["turn_id"],
                "item": {"type": "CommandExecution", "id": call, "exit_code": 0}})
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
            append(history, "response_item", {"type": "custom_tool_call_output", "call_id": parent,
                "output": emissions})
            emissions = []
    return event, history, payload


@pytest.mark.parametrize("version", ["0.163.0-alpha.11.2", "0.166.0-alpha.5.1", "0.167.0-alpha.3.2",
    "0.167.0-alpha.4.1"])
def test_failed_tool_result_cannot_qualify_new_native_writer(tmp_path, monkeypatch, version):
    """A successful page nested in a failed tool result is not replay evidence."""
    event = setup(tmp_path, monkeypatch)
    history = tmp_path / "rollout.jsonl"
    append(history, "session_meta", {"id": "session", "cwd": str(tmp_path),
        "cli_version": version, "history_mode": "paginated"})
    append(history, "turn_context", {"cwd": str(tmp_path), "model": "fixture"})
    history.chmod(0o600)
    event.update(transcript_path=str(history), model="fixture", permission_mode="bypassPermissions")
    _, first = project(tmp_path, payload={"ok": True,
        "plan_summary": {"requirements": "Keep original evidence. " * 1000},
        "heartbeat_receipt": {"turn_instance_id": "first"}})

    repo = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "loopx.entrypoint", "--format", "json", "quota",
        "context-hook", "--context-delivery", "on", "--codex-app",
        "--codex-context-dir", str(tmp_path)]
    environment = {**os.environ, "PYTHONPATH": str(repo), "CODEX_THREAD_ID": "session"}
    for index, page in enumerate(read_pages(tmp_path, first)):
        call = f"failed-reader-{index}"
        response = {"isError": True,
            "content": [{"type": "text", "text": json.dumps(page)}]}
        append(history, "response_item", {"type": "function_call_output", "call_id": call,
            "output": json.dumps(response)})
        result = subprocess.run(command, input=json.dumps({**event, "hook_event_name": "PostToolUse",
            "tool_use_id": call, "tool_response": response}), capture_output=True, text=True,
            cwd=tmp_path, env=environment, timeout=20, check=False)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {}

    before = context._state(tmp_path)
    assert "candidate" not in before
    resumed = subprocess.run(command, input=json.dumps({**event, "source": "resume"}),
        capture_output=True, text=True, cwd=tmp_path, env=environment, timeout=20, check=False)
    assert resumed.returncode == 0, resumed.stderr
    recovery = json.loads(resumed.stdout)
    assert "quota context-read" in recovery["hookSpecificOutput"]["additionalContext"]
    assert "candidate" not in context._state(tmp_path)


@pytest.mark.parametrize("shell_wrapper", [False, True])
@pytest.mark.parametrize("version", ["0.162.1", "0.163.0-alpha.11.1", "0.163.0-alpha.11.2",
    "0.166.0-alpha.5.1", "0.167.0-alpha.3.2", "0.167.0-alpha.4.1"])
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
    assert_file_delta(wire, before["recovery"], current)


@pytest.mark.parametrize("replay", [
    "present", "direct", "pinned", "paged", "out_of_order", "lost_page", "missing_hook", "batched",
    "lost_output", "partial", "compacted", "changed_source",
    "reread", "reread_lost_page", "reread_changed_page",
    "reread_present_unhooked", "reread_lost_unhooked", "reread_lost_verified",
])
def test_native_resume_retains_only_replayed_complete_selections(tmp_path, monkeypatch, replay):
    """A selected read can survive a qualified replay without proving full input."""
    import hashlib

    event = setup(tmp_path, monkeypatch)
    history = tmp_path / "rollout.jsonl"
    append(history, "session_meta", {"id": "session", "cwd": str(tmp_path),
        "cli_version": "0.162.1", "history_mode": "paginated"})
    history.chmod(0o600)
    event.update(transcript_path=str(history), model="fixture", permission_mode="bypassPermissions")
    append(history, "turn_context", {"cwd": str(tmp_path), "model": "fixture"})
    address = "/interaction_contract/agent_channel/work_context/sources/0/content/text"
    narrative = "Preserve the current stop. " * 320
    payload = {"ok": True, "goal_id": "fixture", "selected_todo": {"todo_id": "todo_current"},
        "goal_boundary": {"stop_condition": "Keep the current stop."},
        "interaction_contract": {"schema_version": "loopx_interaction_contract_v0",
            "agent_channel": {"work_context": {"sources": [{"source": "goal_state", "content": {
                "state_file": "fixture-goal.md", "full_source": True, "text": narrative,
                "revision": "sha256:" + hashlib.sha256(narrative.encode()).hexdigest(),
            }}]}}, "user_channel": {}, "cli_channel": {}},
        "unread": "This is a separate source field. " * 400}
    _, wire = project(tmp_path, payload=payload)
    recovery = wire["recovery"]
    repo = Path(context.__file__).resolve().parents[3]
    cli = [sys.executable, "-m", "loopx.entrypoint", "--format", "json", "quota"]
    environment = {**os.environ, "PYTHONPATH": str(repo), "CODEX_THREAD_ID": "session"}

    def command(args, event=None):
        result = subprocess.run([*cli, *args], input=json.dumps(event) if event else None,
            capture_output=True, text=True, cwd=tmp_path, env=environment, timeout=20, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    reread = replay.startswith("reread")
    paged = reread or replay in {"paged", "out_of_order", "lost_page", "missing_hook", "batched"}
    if paged or replay == "pinned":
        from loopx.control_plane.turn_driver.decision_capture import write_context_read_reference

        ref = write_context_read_reference(Path(recovery["path"]), recovery["sha256"])
        source = ["--reference-file", str(ref)]
    else:
        source = ["--capture-file", recovery["path"], "--capture-sha256", recovery["sha256"]]
    page = command(["context-read", *source, "--select", address,
        "--page-bytes", "3000" if paged or replay == "partial" else "64000",
        "--max-page-output-bytes", "64000"])
    assert page["ok"] and page["eof"] == (not paged and replay != "partial")
    hook = ["context-hook", "--context-delivery", "on", "--codex-app", "--codex-context-dir", str(tmp_path)]
    pages = [page]
    if paged:
        while not pages[-1]["eof"]:
            next_command = shlex.split(pages[-1]["next_command"])
            pages.append(command(next_command[next_command.index("context-read"):]))
        assert len(pages) > 2
    if replay == "out_of_order":
        pages = [pages[-1], *pages[:-1]]
    replay_pages = list(pages)
    extra_address = "/goal_boundary/stop_condition"
    later_turn = {**event, "turn_id": "reread-turn"}
    if reread:
        extra = command(["context-read", *source, "--select", extra_address])
        assert extra["ok"] and extra["eof"]
        replay_pages.insert(0, extra)
    emissions = []
    for index, page in enumerate(replay_pages):
        # A code-mode parent may append its actual output after the nested hook.
        reader = f"reader-{index}"
        parent = "parent-batch" if replay == "batched" else f"parent-{index}"
        if replay == "direct":
            append(history, "response_item", {"type": "function_call", "call_id": reader, "name": "exec_command"})
        elif not emissions:
            append(history, "response_item", {"type": "custom_tool_call", "call_id": parent, "name": "exec",
                "internal_chat_message_metadata_passthrough": {"turn_id": "initial-turn"}})
        append(history, "event_msg", {"type": "item_completed",
            "turn_id": "initial-turn", "item": {"type": "CommandExecution", "id": reader, "exit_code": 0}})
        if replay != "missing_hook" or index != 1:
            command(hook, {**event, "turn_id": "initial-turn", "hook_event_name": "PostToolUse", "tool_use_id": reader,
                "tool_response": json.dumps(page)})
        missing_page = ((replay == "lost_page" and index == 1)
            or (replay == "reread_lost_page" and index == 2)
            or (replay in {"reread_lost_unhooked", "reread_lost_verified"} and index == 1))
        if replay != "lost_output" and not missing_page:
            retained_page = {**page, "content": page["content"] + " changed"} \
                if replay == "reread_changed_page" and index == 2 else page
            if replay == "direct":
                append(history, "response_item", {"type": "function_call_output",
                    "call_id": reader, "output": json.dumps(retained_page)})
            else:
                emissions.append({"type": "input_text", "text": json.dumps(retained_page)})
        if emissions and (replay != "batched" or index == len(replay_pages) - 1):
            append(history, "response_item", {"type": "custom_tool_call_output",
                "call_id": parent, "output": emissions})
            emissions = []
        elif missing_page:
            # A complete parent result may omit the nested emission altogether.
            append(history, "response_item", {"type": "custom_tool_call_output",
                "call_id": parent, "output": []})
    if reread:
        again = command(["context-read", *source, "--select", address, "--page-bytes", "3000",
            "--max-page-output-bytes", "64000"])
        assert again == pages[0]
        append(history, "event_msg", {"type": "task_complete", "turn_id": "initial-turn"})
        append(history, "event_msg", {"type": "task_started", "turn_id": later_turn["turn_id"]})
        # The host may reuse the earlier parent's ID in the next Turn.
        parent = "parent-1"
        append(history, "response_item", {"type": "custom_tool_call", "call_id": parent, "name": "exec",
            "internal_chat_message_metadata_passthrough": {"turn_id": later_turn["turn_id"]}})
        append(history, "event_msg", {"type": "item_completed",
            "turn_id": later_turn["turn_id"],
            "item": {"type": "CommandExecution", "id": "reader-reread", "exit_code": 0}})
        if not replay.endswith("_unhooked"):
            command(hook, {**later_turn, "hook_event_name": "PostToolUse", "tool_use_id": "reader-reread",
                "tool_response": json.dumps(again)})
        append(history, "response_item", {"type": "custom_tool_call_output", "call_id": parent,
            "output": [{"type": "input_text", "text": json.dumps(again)}]})
    before = context._state(tmp_path)
    assert (address in before.get("consumed_pointers", {})) == (replay not in {"partial", "missing_hook"})
    if reread:
        assert extra_address in before["consumed_pointers"]
    assert "candidate" not in before
    if replay == "compacted":
        append(history, "compacted", {"message": "Earlier model context was replaced."})
    if replay == "changed_source":
        capture = Path(recovery["path"])
        changed = capture.read_bytes() + b" "
        capture.write_bytes(changed)
        assert hashlib.sha256(changed).hexdigest() != recovery["sha256"]
    command(hook, {**event, "source": "resume"})
    after = context._state(tmp_path)
    assert "candidate" not in after
    retained = replay in {"present", "direct", "pinned", "paged", "out_of_order", "batched",
                          "reread", "reread_present_unhooked", "reread_lost_verified"}
    expected = before["consumed_pointers"] if retained else (
        {extra_address: before["consumed_pointers"][extra_address]} if reread else {})
    assert after.get("consumed_pointers", {}) == expected
    if paged:
        delivered_bytes = sum(len(page["content"].encode("utf-8")) for page in pages)
        assert delivered_bytes == pages[0]["total_bytes"] > 8_000
    if paged:
        payload["goal_boundary"]["stop_condition"] = "The new stop applies before publication."
        directory = tmp_path / "next"
        directory.mkdir(mode=0o700)
        prepared = context.prepare_codex_context(payload, directory=str(tmp_path), capture_directory=directory,
            goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id="next")
        view = model_context_reference(native_context_capture_view(prepared.delivery, directory,
            context_directory=str(tmp_path), initial_read_plan=prepared.initial_read_plan))
        first_command = shlex.split(view["first_read_command"])
        opening = command(first_command[first_command.index("context-read"):])
        assert opening["eof"] and payload["goal_boundary"]["stop_condition"] in opening["content"]
        assert (address in opening.get("previously_delivered_fields", [])) is retained
        assert (narrative in opening["content"]) is not retained
        if retained:
            assert opening["total_bytes"] < len(narrative.encode("utf-8"))
        else:
            assert opening["total_bytes"] > len(narrative.encode("utf-8"))


@pytest.mark.parametrize("single_page", [False, True])
@pytest.mark.parametrize("lose_first", [False, True])
@pytest.mark.parametrize("code_mode", [False, True])
def test_compact_recovery_preserves_turn_but_next_turn_starts_new_file_baseline(tmp_path, monkeypatch, single_page, lose_first, code_mode):
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
    guidance = recovery_instruction["hookSpecificOutput"]["additionalContext"]
    assert "max_output_tokens=22000" in guidance
    assert "max_output_tokens=24000" in guidance
    assert "grants no new authority/freshness" in guidance and "retention proof" in guidance

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
    # Reading the old Turn's recovery cannot seed the new epoch. Even an exact
    # read is a recovery of prior work, not the next Turn's current baseline.
    assert wire["mode"] == "full" and wire["context"] == current


@pytest.mark.parametrize("change", ["compact", "rollback", "retained", "unknown", "truncate",
    "replace", "rewrite", "missing", "output", "duplicate", "model", "workspace", "session", "permission", "writable", "malformed"])
@pytest.mark.parametrize("version", ["0.162.1", "0.163.0-alpha.11.1", "0.163.0-alpha.11.2",
    "0.166.0-alpha.5.1", "0.167.0-alpha.3.2", "0.167.0-alpha.4.1"])
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


@pytest.mark.parametrize("version", ["0.163.0", "0.163.0-alpha.11.3", "0.166.0-alpha.5.2",
    "0.167.0", "0.167.0-alpha.3.3", "0.167.0-alpha.4.2", "unknown"])
def test_unqualified_native_version_cannot_retain_resume_baseline(tmp_path, monkeypatch, version):
    event, _, payload = observed_read(tmp_path, monkeypatch, version=version)
    before = context._state(tmp_path)
    assert "native_resume" not in before["candidate"]
    context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    assert "candidate" not in context._state(tmp_path)
    assert context._state(tmp_path)["context_epoch"] == before["context_epoch"]
    _, wire = project(tmp_path, "second", payload=payload)
    assert_file_delta(wire, before["recovery"], payload)


def test_incomplete_native_read_cannot_gain_resume_continuity(tmp_path, monkeypatch):
    event, _, payload = observed_read(tmp_path, monkeypatch, complete=False)
    before = context._state(tmp_path)
    assert context.codex_context_hook(str(tmp_path), {**event, "source": "resume"}) == {}
    assert "candidate" not in context._state(tmp_path)
    _, wire = project(tmp_path, "second", payload=payload)
    assert_file_delta(wire, before["recovery"], payload)


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
            text=True, timeout=5, cwd=Path(__file__).resolve().parents[2], check=False)
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
        if phase == "post_tool":
            # No replay checkpoint was created from the pipe. It cannot prove
            # retention, but the regular saved JSON is still a valid file base.
            assert after["context_epoch"] == before["context_epoch"] and result == {}
            _, wire = project(tmp_path, "after-fifo", payload=payload)
            assert_file_delta(wire, reference, payload)
        else:
            # A previously verified replay became unreadable: this may hide
            # compaction, so rotate conservatively without opening the pipe.
            assert after["context_epoch"] != before["context_epoch"]
            assert after["recovery"] == reference
            guidance = result["hookSpecificOutput"]["additionalContext"]
            assert reference["command"] in guidance
            assert "max_output_tokens=22000" in guidance
            assert "max_output_tokens=24000" in guidance
            assert "grants no new authority/freshness" in guidance and "retention proof" in guidance
            _, fallback = project(tmp_path, "after-fifo", payload=payload)
            assert fallback["mode"] == "full" and fallback["context"] == payload
    assert Path(reference["path"]).read_bytes() == captured
    assert not registry.exists() and not runtime.exists()


def test_complete_eof_cannot_hide_a_prior_page_missing_from_native_replay(tmp_path, monkeypatch):
    event, _, payload = observed_read(tmp_path, monkeypatch, lost_prefix=True)
    context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    _, wire = project(tmp_path, "second", payload=payload)
    assert wire["mode"] == "full" and wire["context"] == payload


@pytest.mark.parametrize(("replay", "retained"), [
    ("present", True), ("batch", True), ("missing", False),
    ("unobserved_copy", False), ("observed_reread", True),
])
def test_full_capture_resume_requires_the_observed_native_output(tmp_path, monkeypatch, replay, retained):
    event = setup(tmp_path, monkeypatch)
    history = tmp_path / "rollout.jsonl"
    append(history, "session_meta", {"id": "session", "cwd": str(tmp_path),
        "cli_version": "0.167.0-alpha.4.1", "history_mode": "paginated"})
    history.chmod(0o600)
    turn_id = "capture-turn"
    event.update(transcript_path=str(history), model="fixture", permission_mode="bypassPermissions", turn_id=turn_id)
    append(history, "event_msg", {"type": "task_started", "turn_id": turn_id})
    append(history, "turn_context", {"cwd": str(tmp_path), "model": "fixture"})
    payload = {"ok": True, "plan_summary": {"requirements": "Keep original evidence. " * 1500},
        "heartbeat_receipt": {"turn_instance_id": "first"}}
    _, first = project(tmp_path, payload=payload)
    pages = read_pages(tmp_path, first)
    assert len(pages) >= 3 and pages[-1]["eof"]

    def open_parent(parent):
        append(history, "response_item", {"type": "custom_tool_call", "call_id": parent, "name": "exec",
            "internal_chat_message_metadata_passthrough": {"turn_id": turn_id}})

    def observe(reader, page):
        append(history, "event_msg", {"type": "item_completed", "turn_id": turn_id,
            "item": {"type": "CommandExecution", "id": reader, "exit_code": 0}})
        context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PostToolUse",
            "tool_use_id": reader, "tool_response": json.dumps(page)})

    def emit(parent, contents):
        append(history, "response_item", {"type": "custom_tool_call_output", "call_id": parent,
            "output": [{"type": "input_text", "text": json.dumps(page)} for page in contents]})

    if replay == "batch":
        open_parent("batch")
        for i, page in enumerate(pages):
            observe(f"reader-{i}", page)
        emit("batch", pages)
    else:
        for i, page in enumerate(pages):
            parent = f"parent-{i}"
            open_parent(parent)
            observe(f"reader-{i}", page)
            emit(parent, [page] if i or replay == "present" else [])
            if i == 0 and replay in {"unobserved_copy", "observed_reread"}:
                open_parent("copy")
                if replay == "observed_reread":
                    observe("reader-reread", page)
                emit("copy", [page])
    before = context._state(tmp_path)
    assert before["candidate"]["capture_bytes_verified"]
    append(history, "event_msg", {"type": "task_complete", "turn_id": turn_id})
    context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    after = context._state(tmp_path)
    assert bool(after.get("candidate")) is retained
    current = {**payload, "heartbeat_receipt": {"turn_instance_id": "second"}}
    _, wire = project(tmp_path, "second", payload=current)
    if retained:
        assert_file_delta(wire, before["recovery"], current)
    else:
        assert wire["mode"] == "full" and wire["context"] == current


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
    assert peak < 1024 * 1024  # bounded by observed files, not the transport ceiling or history size
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
