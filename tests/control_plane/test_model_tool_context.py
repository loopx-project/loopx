"""Native model stdout and private semantic grading must remain distinct."""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

import pytest

from loopx.control_plane.testing.model_tool_behavior import QuotaToolContext, snapshot_disposable_fixture
from loopx.control_plane.turn_driver.decision_capture import (
    capture_context_projection,
    write_context_read_reference,
)

NATIVE_CAPTURE_TEST = pytest.mark.skipif(
    os.name != "posix", reason="Native captured-input transport is POSIX-only; other hosts retain inline delivery")


def _captured_output(root: Path, *, mode: str = "full") -> tuple[str, dict]:
    root.mkdir(parents=True, exist_ok=True)
    packet = {"interaction_contract": {"stop": "Do not publish without acceptance."},
        "selected_todo": {"id": "selected-work", "text": "Read the selected proof."},
        "unchanged": "完整来源 / complete source"}
    capture = capture_context_projection(packet, directory=root)
    reference = write_context_read_reference(Path(capture["path"]), capture["sha256"])
    output = json.dumps({"ok": True, "schema_version": "context_capture_navigation_v1",
        "mode": mode, "reference_file": "not-the-current-source.reader.json",
        "recovery": {"reference_file": str(reference)},
        "baseline_reference": {"reference_file": "never-prefetch-this-baseline.reader.json"}})
    return output, packet


@pytest.mark.parametrize("mode", ["full", "delta"])
@NATIVE_CAPTURE_TEST
def test_oracle_uses_current_recovery_without_replacing_model_stdout(tmp_path, mode):
    output, packet = _captured_output(tmp_path, mode=mode)
    context = QuotaToolContext.from_output(output)
    assert context.packet == packet
    assert context.output == output
    assert "interaction_contract" not in json.loads(context.output)
    assert context.capture_path is not None


def test_inline_output_retains_the_explicit_opt_out_contract():
    packet = {"ok": True, "interaction_contract": {"must_stop": True}}
    output = json.dumps(packet)
    context = QuotaToolContext.from_output(output)
    assert context.packet == packet and context.output == output
    assert context.capture_path is None


@pytest.mark.parametrize("attack", ["other_capture", "other_digest", "session_lookup", "shell_chain"])
@NATIVE_CAPTURE_TEST
def test_model_read_cannot_escape_the_current_capture(tmp_path, attack):
    output, _ = _captured_output(tmp_path / "current")
    context = QuotaToolContext.from_output(output)
    command = ["loopx", "--format", "json", "quota", "context-read",
        "--capture-file", str(context.capture_path), "--capture-sha256", context.capture_sha256]
    if attack == "other_capture":
        other_output, _ = _captured_output(tmp_path / "other")
        other = QuotaToolContext.from_output(other_output)
        command[6] = str(other.capture_path)
        command[8] = other.capture_sha256
    elif attack == "other_digest":
        command[8] = "0" * 64
    elif attack == "session_lookup":
        command = command[:5] + ["--codex-context-dir", str(tmp_path)]
    shell = shlex.join(command)
    if attack == "shell_chain":
        shell += " && loopx quota should-run --goal-id another-goal"

    before = snapshot_disposable_fixture(tmp_path)
    with pytest.raises(ValueError):
        context.read(shell, source_root=tmp_path, project_root=tmp_path)
    assert snapshot_disposable_fixture(tmp_path) == before


@NATIVE_CAPTURE_TEST
def test_real_reader_supports_batched_progressive_fields_without_a_guard(tmp_path):
    output, packet = _captured_output(tmp_path)
    context = QuotaToolContext.from_output(output)
    command = shlex.join(["loopx", "--format", "json", "quota", "context-read",
        "--capture-file", str(context.capture_path), "--capture-sha256", context.capture_sha256,
        "--select", "/selected_todo", "--select", "/interaction_contract"])
    page = json.loads(context.read(command, source_root=Path(__file__).resolve().parents[2],
        project_root=tmp_path))
    selected = {row["pointer"]: row["value"] for row in json.loads(page["content"])["selections"]}
    assert selected == {"/selected_todo": packet["selected_todo"],
        "/interaction_contract": packet["interaction_contract"]}
    assert page["eof"]
    assert not (tmp_path / ".loopx").exists()


@NATIVE_CAPTURE_TEST
def test_changed_current_capture_is_not_graded_as_its_previous_decision(tmp_path):
    output, _ = _captured_output(tmp_path)
    context = QuotaToolContext.from_output(output)
    assert context.capture_path is not None
    context.capture_path.write_text('{"interaction_contract":{"must_stop":false}}')
    with pytest.raises(ValueError, match="digest mismatch"):
        QuotaToolContext.from_output(output)


@NATIVE_CAPTURE_TEST
def test_actor_receives_native_navigation_then_executes_its_progressive_reader(tmp_path, monkeypatch):
    from loopx.control_plane.testing.model_tool_behavior import scripted_exec_tool_response
    from loopx.control_plane.testing.selected_todo_tool_behavior import (
        DoubaoSelectedTodoToolBehaviorActor, _build_fixture,
    )

    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    fixture = _build_fixture(tmp_path / "oracle")
    requests = []

    def choose_response(**kwargs):
        request = json.loads(kwargs["body"])
        requests.append(request)
        if len(requests) == 1:
            command = fixture.quota_guard_command.replace('"${LOOPX_TURN:?}"', "native-reader-turn")
        elif len(requests) == 2:
            navigation = json.loads(request["messages"][-1]["content"])
            assert navigation["schema_version"] == "context_capture_navigation_v1"
            assert "interaction_contract" not in navigation
            command = navigation["recovery"]["command"] + (
                " --select /selected_todo --select /interaction_contract/cli_channel")
        else:
            page = json.loads(request["messages"][-1]["content"])
            assert page["ok"] and page["eof"]
            values = {row["pointer"]: row["value"] for row in json.loads(page["content"])["selections"]}
            assert values["/selected_todo"]["todo_id"] == "todo_portfolio001"
            assert values["/interaction_contract/cli_channel"].get("selection_required", False) is False
            command = "cat fixture/selected-lane.json"
        return scripted_exec_tool_response(f"read-{len(requests)}", command)

    def transport(**kwargs):
        try:
            return choose_response(**kwargs)
        except Exception as exc:
            pytest.fail(f"scripted native response: {type(exc).__name__}: {exc}")

    result = DoubaoSelectedTodoToolBehaviorActor(api_key="test-only-placeholder", transport=transport).qualify(
        qualification_id="native-progressive-selected-action", fixture_root=tmp_path / "actor")
    assert result["qualification_passed"] is True, result
    assert result["observed_tool_sequence"] == ["quota_should_run", "context_read", "selected_action"]
    assert len(requests) == 3
