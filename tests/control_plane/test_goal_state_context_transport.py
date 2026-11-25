"""Full and paged quota delivery preserve the same registered Goal obligations."""
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
from loopx.control_plane.testing.canary_harness import write_fixture_registry
from loopx.control_plane.work_items import context_readback
from tests.control_plane.codex_checkpoint_fixture import reference_metadata_oracle


def test_scalar_root_projection_keeps_the_complete_view_and_original_number():
    from loopx.control_plane.quota.turn_envelope import _apply_context_projection_changes

    source = {"counter": 9007199254740997}
    view = {"ok": True, "goal_id": "example", "stage": "explore", "explore_basis": None}
    actual = _apply_context_projection_changes(source, {"changes": [
        {"path": [], "value": view},
        {"path": ["counter"], "from": ["counter"]},
    ]}, base={})
    assert actual == {**view, "counter": 9007199254740997}
    assert "counter" not in view
    assert source == {"counter": 9007199254740997}


def test_root_source_copy_keeps_numeric_identity_without_aliasing():
    import math

    from loopx.control_plane.quota.turn_envelope import _apply_context_projection_changes

    source = {"facts": {"unsafe": 9007199254740997, "negative_zero": -0.0}}
    actual = _apply_context_projection_changes(source, {
        "changes": [{"path": [], "from": []}],
    }, base={})
    assert actual == source and actual is not source
    assert actual["facts"] is not source["facts"]
    assert math.copysign(1, actual["facts"]["negative_zero"]) == -1
    actual["facts"]["unsafe"] = 0
    assert source["facts"]["unsafe"] == 9007199254740997


@pytest.mark.parametrize("change", [
    {"path": [], "remove": True},
    {"path": [], "value": None},
    {"path": [], "value": []},
    {"path": [], "value": False},
])
def test_projection_root_must_remain_an_object(change):
    from loopx.control_plane.quota.turn_envelope import _apply_context_projection_changes

    with pytest.raises(RuntimeError, match="Invalid TypeScript context projection changes"):
        _apply_context_projection_changes({}, {"changes": [change]}, base={})


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
    requests = []
    omit_delivery_profile = [False]

    def observe(operation, request, **kwargs):
        if operation == "work_item.context.project":
            requests.append(deepcopy(request))
        result = original_effect(operation, request, **kwargs)
        if operation == "work_item.context.project" and omit_delivery_profile[0]:
            result.pop("source_delivery_profile", None)
        return result

    monkeypatch.setattr(context_readback, "effect_runtime_result", observe)

    def run_full_guard():
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
                "quota", "should-run", "--goal-id", goal, "--agent-id", "agent-a",
                "--runtime-profile", "generic_cli", "--context-delivery", "off"])
        return code, json.loads(output.getvalue())

    def assert_complete_goal(channel, expected_bytes):
        goals = [row["content"] for row in channel["work_context"]["sources"] if row["source"] == "goal_state"]
        assert goals == [{"state_file": str(state), "revision": "sha256:" + sha256(expected_bytes).hexdigest(),
                          "text": expected_bytes.decode(), "full_source": True}]
        assert not any(row["source"] == "goal_state" for row in channel["required_reads"])

    code, small = run_full_guard()
    assert code == 0 and small["should_run"] is True, small.get("reason")
    assert small["schema_version"] == "loopx_turn_envelope_v1"
    small_request = requests[-1]
    small_channel = small["interaction_contract"]["agent_channel"]
    assert_complete_goal(small_channel, initial)
    large = initial + b"\n## Evidence\n" + b"Prior evidence remains available. " * 100_000
    large += "\n## Stop Conditions\n未经授权不得发布。\n".encode()
    assert len(large) > 2 * 1024 * 1024  # Preserve the original source regression.
    state.write_bytes(large)
    code, large_packet = run_full_guard()
    assert code == 0 and large_packet["should_run"] is True, large_packet.get("reason")
    assert large_packet["schema_version"] == "loopx_turn_envelope_v1"
    large_request = requests[-1]
    large_channel = large_packet["interaction_contract"]["agent_channel"]
    assert state.read_bytes() == large
    # Explicit Off returns the complete source, including a large Goal.
    assert_complete_goal(large_channel, large)
    assert large_channel["delivery_allowed"] is True
    assert large_channel["work_context"]["complete"] is True
    assert large_channel["work_context"]["selected_todo_text_verified"] is True
    assert small_channel["required_reads"] == large_channel["required_reads"]

    goal_read = next(row for row in large_request["required_reads"] if row["source"] == "goal_state")
    goal_read_probe = {"goal_id": goal, "interaction_contract": {
        "agent_channel": {"required_reads": [goal_read]},
    }}
    content = context_readback._source_content({"source": "goal_state"}, **source_args)
    assert content == {"state_file": str(state), "revision": "sha256:" + sha256(large).hexdigest()}
    full = context_readback._source_content(
        {"source": "goal_state"}, **source_args, inline_goal_source=True)
    assert full == {**content, "text": large.decode("utf-8"), "full_source": True}
    omit_delivery_profile[0] = True
    try:
        with pytest.raises(RuntimeError, match="unsupported by this runtime"):
            context_readback.attach_work_context(deepcopy(goal_read_probe), registry_path=registry,
                runtime_root=runtime, hook_dispatch=None, inline_goal_source=True)
    finally:
        omit_delivery_profile[0] = False

    def metadata_projection(request):
        projected = deepcopy(request)
        projected.pop("require_full_goal_source")
        result = next(row for row in projected["source_results"] if row["command"] == goal_read["command"])
        result["content"].pop("text")
        result["content"].pop("full_source")
        return projected, result

    # The metadata-only projector still preserves a pending read at constant
    # transport size. A body without the complete-source marker cannot fulfill it.
    small_meta, _ = metadata_projection(small_request)
    large_meta, legacy_goal = metadata_projection(large_request)
    assert len(json.dumps(small_meta).encode()) == len(json.dumps(large_meta).encode())
    pending = original_effect("work_item.context.project", large_meta, large_local_snapshot=True)
    assert goal_read in pending["required_reads"]
    assert not any(row["source"] == "goal_state" for row in pending["work_context"].get("sources", []))
    legacy_goal["content"]["text"] = large.decode()
    assert original_effect("work_item.context.project", large_meta, large_local_snapshot=True) == pending

    changed = large + b"\nAdditional stop at the final byte.\n"
    state.write_bytes(changed)
    revised = context_readback._source_content({"source": "goal_state"}, **source_args)
    assert revised["revision"] == "sha256:" + sha256(changed).hexdigest()
    assert revised["revision"] != content["revision"]

    # Fault the disposable source after admission: exercise actual file reads
    # and the real TS context projection, not a mocked failure result. Then
    # recover through the public full-source CLI guard.
    for fault in ("invalid_utf8_tail", "missing"):
        if fault == "missing":
            state.unlink()
            error = FileNotFoundError
        else:
            state.write_bytes(large + b"\xff")
            error = UnicodeDecodeError
        with pytest.raises(error):
            context_readback._source_content({"source": "goal_state"}, **source_args)
        failed = deepcopy(goal_read_probe)
        context_readback.attach_work_context(failed, registry_path=registry,
            runtime_root=runtime, hook_dispatch=None)
        channel = failed["interaction_contract"]["agent_channel"]
        assert channel["delivery_allowed"] is False
        assert channel["work_context"]["complete"] is False
        assert any(row.get("source") == "goal_state" for row in channel["required_reads"])
        with pytest.raises(RuntimeError, match="Goal source"):
            context_readback.attach_work_context(deepcopy(goal_read_probe), registry_path=registry,
                runtime_root=runtime, hook_dispatch=None, inline_goal_source=True)
        _, refused = run_full_guard()
        assert refused.get("normal_delivery_allowed") is not True
        state.write_bytes(large)
        code, recovered = run_full_guard()
        assert code == 0 and recovered["normal_delivery_allowed"] is True
        assert_complete_goal(recovered["interaction_contract"]["agent_channel"], large)

    # Open the native Turn after the unbound guards finish. Its durable
    # receipt would otherwise route the next guard to original-Turn recovery.
    state.write_bytes(initial)
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
    digest = reference_metadata_oracle(page)["sha256"]
    if page.get("schema_version") != "context_capture_page_v1":
        from test_context_delivery_cli import _native_reader_args

        args = _native_reader_args(page["command"])
        args.page_bytes = 3072
        page = read_context_capture(args)
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
    assert_complete_goal(native_channel, initial)
    assert state.read_bytes() == initial
