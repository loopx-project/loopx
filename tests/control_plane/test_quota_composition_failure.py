"""Uncomposed guard input is diagnostic evidence, never a legacy decision."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
from io import StringIO
import json
import shlex

import pytest

from loopx.cli import main
from loopx.cli_commands import quota
from loopx.control_plane.effect_runtime import EffectRuntimeRejected, EffectRuntimeStartupError
from loopx.control_plane.turn_driver.codex_context import codex_context_hook
from loopx.presentation.renderers.quota_markdown import render_quota_should_run_markdown
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _heartbeat_receipt_count,
    _initialize_fixture_authority, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fault", ["action", "interaction", "runtime"])
def test_composition_failure_preserves_receipt_without_publishing_execution_source(
        tmp_path, monkeypatch, provider, fault):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path, claimed=False)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace("status: active-read-only", "status: active"))
    authored = json.loads(registry.read_text())
    goal = authored["goals"][0]
    goal["status"] = "active"
    goal["adapter"] = {"kind": "generic_project_goal_v0", "status": "connected"}
    goal["coordination"]["handoff_mode"] = "hard_lease"
    registry.write_text(json.dumps(authored))
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="hard_lease")
    monkeypatch.chdir(project)
    monkeypatch.setenv("CODEX_THREAD_ID", "composition-failure-session")
    monkeypatch.delenv("CODEX_ROLLOUT_TRACE_ROOT", raising=False)
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    event = {"session_id": "composition-failure-session", "cwd": str(project),
        "hook_event_name": "SessionStart", "source": "startup"}
    assert codex_context_hook(str(native), event) == {}
    original_journal = (native / "session.json").read_bytes()
    capture = tmp_path / "capture"
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", "quota"]
    identity = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", TURN_ID]

    def cli(command, *args):
        output = StringIO()
        with redirect_stdout(output):
            code = main([*prefix, command, *args])
        return code, json.loads(output.getvalue())

    compose = quota.compose_quota_cli_source
    original = {}

    def reject_composition(payload, *args, **kwargs):
        assert payload["ok"] is True and payload["should_run"] is True
        assert payload["execution_preparation"]["state"] == "claim_required"
        assert payload["heartbeat_receipt"]["turn_instance_id"] == TURN_ID
        if fault == "action":
            payload["effective_action"] = "future_execution"
        elif fault == "interaction":
            payload["interaction_contract"]["schema_version"] = "future_interaction"
        original.update(deepcopy(payload))
        if fault == "runtime":
            raise EffectRuntimeStartupError("synthetic composition unavailable",
                diagnostic_code="runtime_unavailable")
        # Real TypeScript composition owns semantic rejection after admission.
        with pytest.raises(EffectRuntimeRejected) as rejection:
            compose(payload, *args, **kwargs)
        raise rejection.value

    with monkeypatch.context() as injected:
        injected.setattr(quota, "compose_quota_cli_source", reject_composition)
        code, failed = cli("should-run", *identity, "--todo-id", TODO_ID, "--codex-app",
            "--context-delivery", "on", "--codex-context-dir", str(native),
            "--decision-output-dir", str(capture))
    assert code == 1 and failed["ok"] is False and failed["should_run"] is False
    assert failed["error_code"] == "quota_decision_transport_failed"
    assert failed["reason"] == failed["error"]
    assert failed["diagnostic_code"] == ("runtime_unavailable" if fault == "runtime" else "invalid_request")
    assert "saved composition diagnostic" in failed["recommended_action"]
    assert "composition_error" not in failed and "source_diagnostic" not in failed
    assert failed["heartbeat_receipt"] == original["heartbeat_receipt"]
    saved = capture / "decision.json"
    captured = failed["captured_decision"]
    assert captured["path"] == str(saved)
    digest = captured["sha256"]
    markdown = render_quota_should_run_markdown(failed)
    assert all(value in markdown for value in (str(saved), digest, TURN_ID, failed["diagnostic_code"], failed["error"]))
    raw = saved.read_bytes()
    assert digest == hashlib.sha256(raw).hexdigest()
    diagnostic = json.loads(raw)
    assert diagnostic["ok"] is False and diagnostic["should_run"] is False
    assert diagnostic["error_code"] == failed["error_code"]
    assert diagnostic["source_diagnostic"] == original
    assert diagnostic["heartbeat_receipt"] == failed["heartbeat_receipt"]
    expected_error = "EffectRuntimeStartupError" if fault == "runtime" else "EffectRuntimeRejected"
    assert diagnostic["composition_error"]["type"] == expected_error
    assert diagnostic["composition_error"]["diagnostic_code"] == failed["diagnostic_code"]
    if fault == "runtime":
        assert diagnostic["composition_error"]["message"] == "synthetic composition unavailable"
        assert "synthetic composition unavailable" not in failed["error"]
    else:
        assert diagnostic["composition_error"]["message"] == failed["error"]
    assert "interaction_contract" not in diagnostic and "execution_preparation" not in diagnostic
    assert (native / "session.json").read_bytes() == original_journal
    assert not (native / "claim-source.json").exists()

    reference = ["--capture-file", captured["path"], "--capture-sha256", digest]
    for setting in ("off", "on"):
        rc, refused = cli("context-project", *identity, *reference, "--context-delivery", setting,
            "--codex-app", "--codex-context-dir", str(native))
        assert rc == 1 and refused["ok"] is False
        assert "command" not in refused and "first_read_command" not in refused
    content = bytearray()
    eof = False
    read_args = ["context-read", *reference, "--select", "/composition_error",
        "--select", "/source_diagnostic", "--page-bytes", "16000", "--max-page-output-bytes", "64000"]
    while not eof:
        rc, page = cli(*read_args)
        assert rc == 0, page
        assert page["offset"] == len(content)
        content.extend(page["content"].encode())
        eof = page["eof"]
        assert len(content) == (page["total_bytes"] if eof else page["next_offset"])
        if not eof:
            continuation = shlex.split(page["next_command"])
            read_args = continuation[continuation.index("quota") + 1:]
    assert json.loads(content)["selections"] == [
        {"pointer": "/composition_error", "value": diagnostic["composition_error"]},
        {"pointer": "/source_diagnostic", "value": original},
    ]
    saved.write_bytes(raw + b"\n")
    try:
        rc, refused = cli("context-read", *reference, "--select", "/composition_error")
        assert rc == 1 and refused["ok"] is False
        assert "capture digest mismatch" in refused["reason"] and "content" not in refused
    finally:
        saved.write_bytes(raw)
    # No callback, projection or diagnostic read acquires work or spends twice.
    assert codex_context_hook(str(native), {**event, "hook_event_name": "PostToolUse"}) == {}
    assert not (native / "claim-source.json").exists()
    assert (native / "session.json").read_bytes() == original_journal
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1 and _spend_run_count(runtime) == 0
    assert saved.read_bytes() == raw

    if fault == "runtime":
        from loopx.control_plane.turn_driver import decision_capture

        missing_capture = tmp_path / "unwritable-capture"

        def fail_write(path, *_args, **_kwargs):
            assert path == missing_capture / "decision.json"
            raise OSError("fixture cannot publish diagnostic")

        with monkeypatch.context() as unavailable:
            unavailable.setattr(quota, "compose_quota_cli_source", reject_composition)
            unavailable.setattr(decision_capture, "write_decision_capture", fail_write)
            code, refused = cli("should-run", *identity, "--todo-id", TODO_ID, "--codex-app",
                "--context-delivery", "on", "--codex-context-dir", str(native),
                "--decision-output-dir", str(missing_capture))
        assert code == 1 and refused["should_run"] is False
        assert refused["heartbeat_receipt"]["event_id"] == failed["heartbeat_receipt"]["event_id"]
        assert refused["error_code"] == "quota_decision_transport_failed"
        assert "Decision capture failed" in refused["reason"]
        assert "captured_decision" not in refused and "saved" not in refused["recommended_action"]
        assert not (missing_capture / "decision.json").exists()
        assert _heartbeat_receipt_count(runtime, TURN_ID) == 1 and _spend_run_count(runtime) == 0
