"""Actual managed CLI/native boundaries, with a deterministic local app-server."""
import contextlib
import io
import json
import sys

import pytest

from loopx.cli import main
from loopx.control_plane.goals.activation_service import set_goal_activation_state
from loopx.control_plane.turn_driver import codex_operation_host
from test_codex_operation_host import FAKE_SERVER
from test_loopx_turn_driver import _write_live_fixture
from test_loopx_turn_codex_cli import _fake_codex


@pytest.mark.parametrize("boundary", ["process", "turn"])
def test_owner_stop_prevents_operation_native_launch(tmp_path, monkeypatch, boundary):
    project, runtime, registry = _write_live_fixture(tmp_path)
    process_marker, turn_marker = tmp_path / "process-started", tmp_path / "turn-started"
    native = tmp_path / "controlled-codex"
    server = FAKE_SERVER.replace("import json, sys",
        f"import json, sys\nfrom pathlib import Path\nPath({str(process_marker)!r}).touch()", 1)
    server = server.replace('elif method == "turn/start":',
        f'elif method == "turn/start":\n        Path({str(turn_marker)!r}).touch()', 1)
    native.write_text(server)
    native.chmod(0o700)
    original_start = codex_operation_host.CodexChatAgentSession.start
    stops = []

    def stop():
        result = set_goal_activation_state(registry_path=registry, goal_id="loopx-turn-fixture",
            state="stopped", reason="Synthetic owner cancellation before native launch.",
            actor_kind="owner", execute=True)
        assert result["readback"]["verified"] is True
        stops.append(True)

    def intercepted_start(**kwargs):
        if boundary == "process":
            stop()
        session = original_start(**kwargs)
        if boundary == "turn":
            # The registry lock must already be released after process start:
            # the real writer can complete before the next model boundary.
            stop()
        return session

    monkeypatch.setattr(codex_operation_host.CodexChatAgentSession, "start", intercepted_start)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main([
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "turn", "run-once", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
            "--host", "codex-cli", "--project", str(project),
            "--no-global-sync", "--codex-operation-tools", "--codex-bin", str(native),
            "--codex-model", "test-model", "--codex-reasoning-effort", "xhigh",
            "--codex-sandbox", "read-only", "--iteration-context", "fresh",
            "--timeout-seconds", "15", "--execute",
        ])
    packet = json.loads(output.getvalue())
    assert code != 0, packet
    assert stops == [True]
    assert process_marker.exists() == (boundary == "turn")
    assert not turn_marker.exists()
    assert not packet["effects"]["state_written"] and not packet["effects"]["quota_spent"]
    index = runtime / "goals/loopx-turn-fixture/runs/index.jsonl"
    assert not index.exists() or "quota_slot_spent" not in index.read_text()


def test_legacy_read_only_registration_can_preview_and_launch(tmp_path, monkeypatch):
    project, runtime, registry = _write_live_fixture(tmp_path)
    value = json.loads(registry.read_text())
    value["goals"][0]["status"] = "active-read-only"
    registry.write_text(json.dumps(value))
    native, marker = _fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(marker))
    args = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "turn", "run-once", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
        "--host", "codex-cli", "--project", str(project),
        "--codex-bin", str(native), "--codex-sandbox", "read-only", "--no-global-sync",
        "--iteration-context", "fresh", "--validation-command-json", json.dumps([sys.executable, "-c", "pass"])]
    for suffix in ([], ["--execute"]):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([*args, *suffix])
        packet = json.loads(output.getvalue())
        assert code == 0 and packet["ok"], packet
    assert packet["status"] == "committed" and marker.exists()
