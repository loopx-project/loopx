"""Complete non-generated Goal text reaches ordinary guards and signed Hosts."""
from __future__ import annotations

from copy import deepcopy
import base64
from hashlib import sha256
import json
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry
from loopx.control_plane.work_items.context_readback import _source_content


def fixture(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    state.write_text("---\nstatus: active\n---\n# Goal\n")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="context-goal", domain="software", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a"], quota_allowed_slots=None)
    todo = {"schema_version": "todo_item_v0", "todo_id": "todo_work", "role": "agent",
        "text": "Keep the complete work. " * 80 + "Stop before publication.", "status": "open",
        "done": False, "task_class": "advancement_task", "action_kind": "implement",
        "index": 1, "source_section": "Agent Todo", "archive_state": "active", "claimed_by": "agent-a"}
    initialize_canonical_authority(runtime, "context-goal",
        build_todo_runtime_shadow_projection(goal_id="context-goal", todos=[todo], handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    return runtime, registry, state, todo


def source(runtime, registry):
    return _source_content({"source": "goal_state"}, registry_path=registry,
        runtime_root=runtime, goal_id="context-goal", todo_id="todo_work")


def guard(runtime, registry):
    code, packet = run_json_cli_result("quota", "should-run", "--goal-id", "context-goal",
        "--agent-id", "agent-a", registry_path=registry, runtime_root=runtime)
    assert code == 0, packet
    return packet


def section(role, heading, text):
    # Explicit public writer grammar, not implementation-derived expectations.
    return (f"## {heading}\n<!-- loopx:todo-region-v0 role={role} begin -->\n"
        f"- [x] {text}\n<!-- loopx:todo-region-v0 role={role} end -->\n")


def monitor_receipt(*, operation="monitor:previous", goal="context-goal"):
    receipt = {"schema_version": "loopx_monitor_batch_receipt_v0", "goal_id": goal,
        "operation_id": operation, "request_sha256": "a" * 64,
        "writeback": {"schema_version": "monitor_poll_todo_writeback_v0", "goal_id": goal,
            "monitor_effect_id": operation, "next_todos": [], "successor_receipts": [],
            "todo_update": {"note": "Previous observation. " * 30_000}}}
    encoded = base64.b64encode(json.dumps(receipt).encode()).decode()
    return receipt, f"<!-- loopx:monitor-batch:{sha256(operation.encode()).hexdigest()} {encoded} -->\n"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_historical_monitor_receipts_keep_recovery_without_repeating_encoded_bodies(tmp_path, monkeypatch, provider):
    from loopx.control_plane.scheduler.monitor_batch_receipts import _read_receipt

    runtime, registry, state, _todo = fixture(tmp_path, monkeypatch, provider)
    receipt, marker = monitor_receipt()
    before = "# Goal\nKeep every requirement.\n"
    after = "<!-- Keep this authored comment. -->\nFinal stop: do not publish.\n"
    document = before + marker + section("archive", "Completed Work Archive", "Old observation.") + after
    state.write_bytes(document.encode())
    packet = guard(runtime, registry)
    channel = packet["interaction_contract"]["agent_channel"]
    context = next(r["content"] for r in channel["work_context"]["sources"] if r.get("source") == "goal_state")
    assert context["text"] == before + after
    assert context["revision"] == "sha256:" + sha256(document.encode()).hexdigest()
    assert context["excluded_monitor_receipts"] == [{"start_line": 3, "end_line": 3,
        "revision": "sha256:" + sha256(marker.encode()).hexdigest()}]
    assert len(json.dumps(context).encode()) < 1500 < len(document.encode())
    assert channel["work_context"]["complete"] and channel["delivery_allowed"]
    assert not any(r.get("source") == "goal_state" for r in channel["required_reads"])
    assert state.read_bytes() == document.encode()
    assert _read_receipt(state.read_text(), receipt["operation_id"]) == receipt


@pytest.mark.parametrize("change", ["foreign_goal", "wrong_identity", "unknown_schema", "invalid_body",
    "extra_field", "duplicate_field", "trailing_prose", "fenced", "nested_comment"])
def test_unowned_monitor_like_text_remains_verbatim(tmp_path, monkeypatch, change):
    runtime, registry, state, _todo = fixture(tmp_path, monkeypatch, "sqlite")
    receipt, marker = monitor_receipt(goal="different-goal" if change == "foreign_goal" else "context-goal")
    if change == "wrong_identity":
        marker = marker.replace(sha256(receipt["operation_id"].encode()).hexdigest(), "0" * 64)
    elif change in {"unknown_schema", "extra_field", "duplicate_field"}:
        if change == "unknown_schema":
            receipt["schema_version"] = "future_receipt_v9"
        if change == "extra_field":
            receipt["authored_requirement"] = "Preserve this requirement."
        body = json.dumps(receipt)
        if change == "duplicate_field":
            body = body[:-1] + ', "goal_id": "context-goal"}'
        marker = marker[:marker.index(" ", 5) + 1] + base64.b64encode(body.encode()).decode() + " -->\n"
    elif change == "invalid_body":
        marker = f"<!-- loopx:monitor-batch:{'0' * 64} e30= -->\n"
    elif change == "trailing_prose":
        marker = marker.rstrip("\n") + " Keep the final requirement.\n"
    elif change == "fenced":
        marker = "```md\n" + marker + "```\n"
    elif change == "nested_comment":
        marker = "<!-- Authored example:\n" + marker + "-->\n"
    state.write_text("# Goal\n" + marker + section("agent", "Agent Todo", "Old task.") + "Final requirement.\n")
    content = source(runtime, registry)
    assert content["text"] == "# Goal\n" + marker + "Final requirement.\n"
    assert "excluded_monitor_receipts" not in content


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_complete_narrative_and_canonical_work_reach_signed_host(tmp_path, monkeypatch, provider):
    runtime, registry, state, todo = fixture(tmp_path, monkeypatch, provider)
    before = "---\nstatus: active\n---\n# Goal\n任意目标文字；保留全部原始要求。\n"
    middle = ("## Agent Todo discussion\nThis is narrative, not a generated section.\n"
        "```md\n## Agent Todo\n<!-- loopx:todo-region-v0 role=agent begin -->\n```\n")
    after = "<!-- an ordinary retained comment -->\n## Recovery\nKeep the final stop and recovery condition.\n"
    generated = section("archive", "Completed Work Archive", "Historical observation. " * 100_000)
    document = before + section("user", "User Todo", "Past decision.") + middle + generated + after
    state.write_bytes(document.encode())
    original = state.read_bytes()
    packet = guard(runtime, registry)
    channel = packet["interaction_contract"]["agent_channel"]
    assert channel["delivery_allowed"] and channel["work_context"]["complete"]
    assert not any(r.get("source") == "goal_state" for r in channel["required_reads"])
    context = next(r["content"] for r in channel["work_context"]["sources"] if r.get("source") == "goal_state")
    assert context["text"] == before + middle + after
    assert context["revision"] == "sha256:" + sha256(original).hexdigest()
    assert [r["role"] for r in context["excluded_todo_regions"]] == ["user", "archive"]
    assert len(json.dumps(context).encode()) < 2000 < len(original)
    exact = next(r["content"]["todo"] for r in channel["work_context"]["sources"] if r.get("source") == "selected_todo")
    assert exact["text"] == todo["text"]

    envelope = build_turn_envelope(packet)
    runner = tmp_path / "runner.py"
    runner.write_text("import json\nfrom pathlib import Path\n"
        "def run_dsh_turn(*, prompt, workspace, **kwargs):\n"
        "    Path(workspace, 'received.txt').write_text(prompt)\n"
        "    return json.dumps({'result_kind':'wait','summary':'Protocol input recorded.'})\n")
    command = [sys.executable, "-m", "loopx.dsh_goal_mode", "--workspace", str(tmp_path),
        "--dsh-home", str(tmp_path / "host-home"), "--dsh-runner", str(runner)]
    request = {"schema_version": "loopx_turn_host_request_v0", "turn_key": "sha256:" + "0" * 64,
        "route": "primary_delivery", "turn_envelope": envelope}
    result = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    prompt = (tmp_path / "received.txt").read_text()
    authority = json.loads(prompt.split("Turn authority JSON:\n", 1)[1].split("\n\nWhen finished", 1)[0])
    received = next(r["content"] for r in authority["work_context"]["sources"] if r.get("source") == "goal_state")
    assert received == context
    assert state.read_bytes() == original
    for field in ("text", "revision", "excluded_todo_regions"):
        changed = deepcopy(request)
        target = next(r["content"] for r in changed["turn_envelope"]["work_context"]["sources"] if r.get("source") == "goal_state")
        target[field] = "changed"
        rejected = subprocess.run(command, input=json.dumps(changed), capture_output=True, text=True, timeout=30)
        assert rejected.returncode != 0 and "action signature" in rejected.stderr
        assert (tmp_path / "received.txt").read_text() == prompt
    # A change even in an excluded archive changes the source revision.
    state.write_bytes(original.replace(b"Historical observation.", b"Changed observation.", 1))
    refreshed = source(runtime, registry)
    assert refreshed["text"] == context["text"] and refreshed["revision"] != context["revision"]
    state.write_bytes((document + "Extra final requirement.\n").encode())
    refreshed_packet = guard(runtime, registry)
    refreshed_context = next(r["content"] for r in refreshed_packet["interaction_contract"]["agent_channel"]["work_context"]["sources"] if r.get("source") == "goal_state")
    assert refreshed_context["text"] == before + middle + after + "Extra final requirement.\n"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("body", ["## Arbitrary Goal\nRead everything.\n",
    "## Agent Todo\n- [ ] Legacy work.\n\nKeep this final condition.\n",
    section("user", "User Todo", "Old decision.") + "## Agent Todo\n- [ ] Unmarked work.\n"])
def test_unmarked_or_mixed_ownership_keeps_full_progressive_read(tmp_path, monkeypatch, provider, body):
    runtime, registry, state, _todo = fixture(tmp_path, monkeypatch, provider)
    state.write_text("---\nstatus: active\n---\n# Goal\n" + body)
    content = source(runtime, registry)
    assert "text" not in content and "excluded_todo_regions" not in content
    assert content["revision"] == "sha256:" + sha256(state.read_bytes()).hexdigest()
    channel = guard(runtime, registry)["interaction_contract"]["agent_channel"]
    assert any(r.get("source") == "goal_state" for r in channel["required_reads"])
    assert not any(r.get("source") == "goal_state" for r in channel["work_context"]["sources"])


@pytest.mark.parametrize("damage", ["missing_end", "mismatched", "narrative", "invalid_utf8", "missing_file"])
def test_invalid_source_fails_and_recovers_without_dropping_requirements(tmp_path, monkeypatch, damage):
    runtime, registry, state, _todo = fixture(tmp_path, monkeypatch, "sqlite")
    original = "# Goal\nKeep all text.\n" + section("agent", "Agent Todo", "Past work.") + "Final stop.\n"
    damaged = original
    if damage == "missing_end":
        damaged = damaged.replace("<!-- loopx:todo-region-v0 role=agent end -->", "")
    elif damage == "mismatched":
        damaged = damaged.replace("role=agent end", "role=user end")
    elif damage == "narrative":
        damaged = damaged.replace("- [x] Past work.", "Do not silently discard this paragraph.")
    state.write_bytes(damaged.encode() + (b"\xff" if damage == "invalid_utf8" else b""))
    if damage == "missing_file":
        state.unlink()
    with pytest.raises((OSError, ValueError)):
        source(runtime, registry)
    code, failed = run_json_cli_result("quota", "should-run", "--goal-id", "context-goal",
        "--agent-id", "agent-a", registry_path=registry, runtime_root=runtime)
    if code == 0:
        channel = failed["interaction_contract"]["agent_channel"]
        assert channel["delivery_allowed"] is False
        assert channel["work_context"]["complete"] is False
        assert any(r.get("source") == "goal_state" for r in channel["required_reads"])
    else:
        assert failed.get("ok") is False
    state.write_bytes(original.encode())
    assert source(runtime, registry)["text"] == "# Goal\nKeep all text.\nFinal stop.\n"
    assert guard(runtime, registry)["interaction_contract"]["agent_channel"]["delivery_allowed"]


def test_non_generated_text_is_never_shortened(tmp_path, monkeypatch):
    runtime, registry, state, _todo = fixture(tmp_path, monkeypatch, "sqlite")
    narrative = "---\r\nstatus: active\r\n---\r\n" + "保留原文\r\n" * 100_000
    state.write_bytes((narrative + section("archive", "Completed Work Archive", "History.") + "TAIL\r\n").encode())
    content = source(runtime, registry)
    assert content["text"] == narrative + "TAIL\r\n"
