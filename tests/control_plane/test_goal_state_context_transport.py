"""Progressive quota and complete host delivery retain their distinct IO contracts."""
from __future__ import annotations

import contextlib
from copy import deepcopy
from hashlib import sha256
import io
import json
import shlex

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.cli import main
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import MAX_REQUEST_BYTES
from loopx.control_plane.testing.canary_harness import write_fixture_registry
from loopx.control_plane.work_items import context_readback


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_goal_availability_transport_preserves_full_read_and_recovery(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    goal, todo_id = "goal-context", "todo_export"
    initial = b"---\nstatus: active\n---\n# Goal\n## Objective\nPreserve the export contract.\n"
    initial += "\n<!-- Original source comment. -->\n## Unknown section\n停止条件：不得发布。\n".encode()
    state.write_bytes(initial)
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id=goal, domain="software", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a"], quota_allowed_slots=None)
    todo = {"schema_version": "todo_item_v0", "todo_id": todo_id, "role": "agent",
        "text": "Preserve every export row and cancellation. Stop before publication.",
        "status": "open", "done": False, "task_class": "advancement_task", "action_kind": "implement",
        "index": 1, "source_section": "Agent Todo", "archive_state": "active", "claimed_by": "agent-a"}
    initialize_canonical_authority(runtime, goal,
        build_todo_runtime_shadow_projection(goal_id=goal, todos=[todo], handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    source_args = dict(registry_path=registry, runtime_root=runtime, goal_id=goal, todo_id=todo_id)
    original_effect = context_readback.effect_runtime_result
    requests, plans = [], []
    omit_delivery_profile = [False]

    def observe(operation, request, **kwargs):
        if operation == "work_item.context.plan":
            plans.append(deepcopy(request["packet"]))
        if operation == "work_item.context.project":
            requests.append(deepcopy(request))
        result = original_effect(operation, request, **kwargs)
        if operation == "work_item.context.project" and omit_delivery_profile[0]:
            result.pop("source_delivery_profile", None)
        return result

    monkeypatch.setattr(context_readback, "effect_runtime_result", observe)

    def guard():
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
                "quota", "should-run", "--goal-id", goal, "--agent-id", "agent-a"])
        packet = json.loads(output.getvalue())
        assert code == 0 and packet["should_run"] is True, packet.get("reason")
        return packet["interaction_contract"]["agent_channel"]

    small_channel = guard()
    small_request = requests[-1]
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import read_context_capture
    from loopx.control_plane.turn_driver import codex_context

    monkeypatch.setenv("CODEX_THREAD_ID", "complete-goal-native")
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    captures = tmp_path / "captures"
    captures.mkdir()
    codex_context.codex_context_hook(str(native), {"hook_event_name": "SessionStart",
        "source": "startup", "session_id": "complete-goal-native", "cwd": str(tmp_path)})
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "quota", "should-run", "--goal-id", goal, "--agent-id", "agent-a", "--codex-app",
            "--todo-id", todo_id, "--turn-instance-id", "native-goal-material",
            "--context-delivery", "on", "--codex-context-dir", str(native),
            "--decision-output-root", str(captures)])
    page, raw = json.loads(output.getvalue()), bytearray()
    assert code == 0 and page["ok"], (page.get("reason"), page.get("error"), page.get("error_code"))
    digest = page["sha256"]
    if page.get("schema_version") != "context_capture_page_v1":
        page = read_context_capture(build_parser().parse_args([
            "--format", "json", "quota", "context-read", "--capture-file", page["path"],
            "--capture-sha256", digest, "--offset", "0", "--page-bytes", "3072"]))
        assert page["ok"], page
    while True:
        assert page["offset"] == len(raw) and page["sha256"] == digest
        raw.extend(page["content"].encode())
        if page["eof"]:
            assert len(raw) == page["total_bytes"]
            break
        page = read_context_capture(build_parser().parse_args(shlex.split(page["next_command"])[1:]))
    assert sha256(raw).hexdigest() == digest
    native_channel = json.loads(raw)["interaction_contract"]["agent_channel"]
    delivered = [row["content"] for row in native_channel["work_context"]["sources"]
        if row.get("source") == "goal_state"]
    assert len(delivered) == 1, "Native delivery must include the complete registered Goal"
    assert delivered[0] == {"state_file": str(state), "revision": "sha256:" + sha256(initial).hexdigest(),
        "text": initial.decode(), "full_source": True}
    assert not any(row.get("source") == "goal_state" for row in native_channel["required_reads"])
    assert state.read_bytes() == initial
    large = initial + b"\n## Evidence\n" + b"Prior evidence remains available. " * 100_000
    large += "\n## Stop Conditions\n未经授权不得发布。\n".encode()
    assert len(large) > MAX_REQUEST_BYTES
    state.write_bytes(large)
    large_channel = guard()
    large_request = requests[-1]
    captured_plan = plans[-1]
    assert state.read_bytes() == large
    # Source growth changes neither the complete Agent contract nor transport
    # size. The full Goal read remains pending; availability is not consumption.
    assert small_channel == large_channel
    assert large_channel["delivery_allowed"] is True
    assert large_channel["work_context"]["complete"] is True
    assert large_channel["work_context"]["selected_todo_text_verified"] is True
    assert any(row.get("source") == "goal_state" for row in large_channel["required_reads"])
    assert not any(row.get("source") == "goal_state" for row in large_channel["work_context"]["sources"])
    assert len(json.dumps(small_request).encode()) == len(json.dumps(large_request).encode())
    content = context_readback._source_content({"source": "goal_state"}, **source_args)
    assert content == {"state_file": str(state), "revision": "sha256:" + sha256(large).hexdigest()}
    full = context_readback._source_content(
        {"source": "goal_state"}, **source_args, inline_goal_source=True)
    assert full == {**content, "text": large.decode("utf-8"), "full_source": True}
    omit_delivery_profile[0] = True
    try:
        with pytest.raises(RuntimeError, match="unsupported by this runtime"):
            context_readback.attach_work_context(deepcopy(captured_plan), registry_path=registry,
                runtime_root=runtime, hook_dispatch=None, inline_goal_source=True)
    finally:
        omit_delivery_profile[0] = False
    # Independently compare the current typed projector with the former full
    # source input. Removing the transport must not weaken its obligations.
    legacy_request = deepcopy(large_request)
    source = next(row for row in legacy_request["source_results"]
        if row.get("content", {}).get("state_file") == str(state))
    source["content"]["text"] = large.decode()
    assert original_effect("work_item.context.project", legacy_request, large_local_snapshot=True) == \
        original_effect("work_item.context.project", large_request, large_local_snapshot=True)

    changed = large + b"\nAdditional stop at the final byte.\n"
    state.write_bytes(changed)
    revised = context_readback._source_content({"source": "goal_state"}, **source_args)
    assert revised["revision"] == "sha256:" + sha256(changed).hexdigest()
    assert revised["revision"] != content["revision"]

    # Fault the disposable source after admission: exercise actual file reads
    # and the real TS context projection, not a mocked failure result. Then
    # recover through the complete native CLI guard.
    for fault in ("invalid_utf8_tail", "missing"):
        if fault == "missing":
            state.unlink()
            error = FileNotFoundError
        else:
            state.write_bytes(large + b"\xff")
            error = UnicodeDecodeError
        with pytest.raises(error):
            context_readback._source_content({"source": "goal_state"}, **source_args)
        failed = deepcopy(captured_plan)
        context_readback.attach_work_context(failed, registry_path=registry,
            runtime_root=runtime, hook_dispatch=None)
        channel = failed["interaction_contract"]["agent_channel"]
        assert channel["delivery_allowed"] is False
        assert channel["work_context"]["complete"] is False
        assert any(row.get("source") == "goal_state" for row in channel["required_reads"])
        with pytest.raises(RuntimeError, match="Goal source"):
            context_readback.attach_work_context(deepcopy(captured_plan), registry_path=registry,
                runtime_root=runtime, hook_dispatch=None, inline_goal_source=True)
        state.write_bytes(large)
        assert guard() == large_channel
