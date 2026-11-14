"""Uncomposed guard input is diagnostic evidence, never a legacy decision."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

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

LONG_PATH_ROUTES = {"long-paths", "deep-paths", "deep-launcher", "deep-installation", "reader-metadata-unavailable"}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fault,bounded_route", [
    ("action", None), ("interaction", None), ("runtime", None),
    ("interaction", "complete"), ("read_order", "explicit"), ("read_order", "prepared"),
    ("runtime", "long-paths"), ("runtime", "deep-paths"), ("runtime", "deep-launcher"),
    ("runtime", "deep-installation"),
    ("runtime", "reader-metadata-unavailable"),
])
def test_composition_failure_preserves_receipt_without_publishing_execution_source(
        tmp_path, monkeypatch, provider, fault, bounded_route):
    limit = 1700 if bounded_route else None
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
    if bounded_route in LONG_PATH_ROUTES:
        parent = tmp_path
        if bounded_route == "deep-paths":
            parent = parent / ("checkout-" + "x" * 85) / ("package-" + "x" * 85)
            parent.mkdir(parents=True)
        capture_length = {"deep-paths": 480, "deep-launcher": 271 - len("/decision.json"),
            "reader-metadata-unavailable": 271 - len("/decision.json")}.get(bounded_route, 250)
        capture = parent / ("capture-" + "x" * (capture_length - len(str(parent)) - len("/capture-")))
        launcher_parent = tmp_path
        if bounded_route == "deep-installation":
            launcher_parent = tmp_path / ("release-" + "x" * 110) / ("package-" + "x" * 110)
            launcher_parent.mkdir(parents=True)
        launcher_length = {"deep-launcher": 276, "deep-installation": 500,
            "reader-metadata-unavailable": 276}.get(bounded_route, 204)
        launcher = launcher_parent / ("launcher-" + "x" * (launcher_length - len(str(launcher_parent)) - len("/launcher-")))
        launcher.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-m", "loopx.entrypoint"]) + ' "$@"\n')
        launcher.chmod(0o700)
        prefix[:0] = ["--cli-bin", str(launcher)]
    identity = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", TURN_ID]

    output_bytes = 0

    def cli(command, *args):
        nonlocal output_bytes
        output = StringIO()
        with redirect_stdout(output):
            code = main([*prefix, command, *args])
        serialized = output.getvalue()
        output_bytes = len(serialized.encode("utf-8"))
        return code, json.loads(serialized)

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
        elif fault == "read_order":
            payload["interaction_contract"]["agent_channel"].setdefault("required_reads", []).append(
                {"command": "cat required-material.md", "ordering": "after_spend"})
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
        if bounded_route == "reader-metadata-unavailable":
            from loopx.control_plane.turn_driver import decision_capture

            def fail_reference(*_args, **_kwargs):
                raise OSError("fixture cannot publish optional reader metadata")

            injected.setattr(decision_capture, "write_initial_context_read_reference", fail_reference)
        code, failed = cli("should-run", *identity, "--todo-id", TODO_ID, "--codex-app",
            "--context-delivery", "on", "--codex-context-dir", str(native),
            "--decision-output-dir", str(capture),
            "--complete-turn",
            *(("--max-page-output-bytes", str(limit)) if limit else ()),
            *(("--defer-context-presentation", "--prepared-capture-reference") if bounded_route == "prepared" else ()))
    assert code == 1 and failed["ok"] is False
    if limit is not None:
        assert output_bytes <= limit, f"{bounded_route} refusal emitted {output_bytes} bytes"
    assert failed["error_code"] == "quota_decision_transport_failed"
    assert failed["diagnostic_code"] == ("runtime_unavailable" if fault == "runtime" else "invalid_request")
    assert "composition_error" not in failed and "source_diagnostic" not in failed
    if limit is None:
        assert failed["should_run"] is False and failed["reason"] == failed["error"]
        assert "saved composition diagnostic" in failed["recommended_action"]
        assert failed["heartbeat_receipt"] == original["heartbeat_receipt"]
    saved = capture / "decision.json"
    captured = failed["captured_decision"]
    if "path" in captured:
        assert captured["path"] == str(saved)
    else:
        assert bounded_route in LONG_PATH_ROUTES
        assert ("--reference-file" in failed["command"]) == (bounded_route != "reader-metadata-unavailable")
    digest = captured["sha256"]
    if limit is None:
        markdown = render_quota_should_run_markdown(failed)
        assert all(value in markdown for value in (str(saved), digest, TURN_ID, failed["diagnostic_code"], failed["error"]))
    raw = saved.read_bytes()
    assert digest == hashlib.sha256(raw).hexdigest()
    diagnostic = json.loads(raw)
    assert diagnostic["ok"] is False and diagnostic["should_run"] is False
    assert diagnostic["error_code"] == failed["error_code"]
    assert diagnostic["source_diagnostic"] == original
    assert diagnostic["heartbeat_receipt"] == original["heartbeat_receipt"]
    expected_error = "EffectRuntimeStartupError" if fault == "runtime" else "EffectRuntimeRejected"
    assert diagnostic["composition_error"]["type"] == expected_error
    assert diagnostic["composition_error"]["diagnostic_code"] == failed["diagnostic_code"]
    if fault == "runtime":
        assert diagnostic["composition_error"]["message"] == "synthetic composition unavailable"
        assert "synthetic composition unavailable" not in diagnostic["error"]
    else:
        assert diagnostic["composition_error"]["message"] == diagnostic["error"]
    assert "interaction_contract" not in diagnostic and "execution_preparation" not in diagnostic
    assert (native / "session.json").read_bytes() == original_journal
    assert not (native / "claim-source.json").exists()

    reference = ["--capture-file", str(saved), "--capture-sha256", digest]
    if limit is not None:
        command = failed.get("command") or captured["command"]
        tokens = shlex.split(command)
        reader_limit = failed.get("recovery_page_output_bytes", limit)
        if "--reference-file" in tokens:
            reader_ref = Path(tokens[tokens.index("--reference-file") + 1])
            reader = json.loads(reader_ref.read_bytes())
            assert reader_ref.stat().st_mode & 0o077 == 0
            if bounded_route == "deep-paths":
                assert reader_ref.parent == (runtime / "context-delivery").resolve()
            assert reader["path"] == str(saved) and reader["sha256"] == digest
            assert "/heartbeat_receipt" in reader["selection"]["select"]
            assert "/composition_error" in reader["selection"]["select"]
        else:
            assert tokens[tokens.index("--capture-sha256") + 1] == digest
        assert tokens[tokens.index("--max-page-output-bytes") + 1] == str(reader_limit)
        if bounded_route in {"deep-installation", "reader-metadata-unavailable"}:
            assert reader_limit > limit
            assert "rerun admission" in failed["instruction"]
        else:
            assert reader_limit == limit
        if bounded_route in LONG_PATH_ROUTES:
            assert tokens[:3] == [str(launcher), "--cli-bin", str(launcher)]
            selected = bytearray()
            env = {**os.environ, "LOOPX_USAGE_PING": "0"}
            if bounded_route == "reader-metadata-unavailable":
                # No selector metadata or TypeScript runtime is needed to
                # read the original failure after diagnostic publication.
                assert "--select" not in tokens and "--reference-file" not in tokens
                env["PATH"] = str(tmp_path / "no-node")
            while True:
                result = subprocess.run(tokens, cwd=project, capture_output=True, check=False,
                    env=env, timeout=30)
                assert result.returncode == 0, result.stdout
                assert len(result.stdout) <= reader_limit
                page = json.loads(result.stdout)
                assert page["ok"] and page["offset"] == len(selected)
                selected.extend(page["content"].encode())
                if bounded_route == "deep-installation":
                    assert page["eof"] is True
                if page["eof"]:
                    assert len(selected) == page["total_bytes"]
                    break
                assert len(selected) == page["next_offset"] and page["content"]
                tokens = shlex.split(page["next_command"])
            if bounded_route == "reader-metadata-unavailable":
                assert selected == raw
            else:
                details = {item["pointer"]: item["value"] for item in json.loads(selected)["selections"]}
                assert details["/heartbeat_receipt"] == original["heartbeat_receipt"]
                assert details["/composition_error"] == diagnostic["composition_error"]
                assert details["/diagnostic_code"] == diagnostic["diagnostic_code"]
        else:
            reader = tokens[tokens.index("context-read") + 1:]
            rc, first_page = cli("context-read", *reader)
            assert rc == 0 and first_page["ok"], first_page
            assert first_page["offset"] == 0 and output_bytes <= limit
    for setting in ("off", "on"):
        rc, refused = cli("context-project", *identity, *reference, "--context-delivery", setting,
            "--codex-app", "--codex-context-dir", str(native))
        assert rc == 1 and refused["ok"] is False
        assert "command" not in refused and "first_read_command" not in refused
    content = bytearray()
    eof = False
    read_args = ["context-read", *reference, "--select", "/composition_error",
        "--select", "/heartbeat_receipt", "--select", "/source_diagnostic",
        "--page-bytes", "16000", "--max-page-output-bytes", "64000"]
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
        {"pointer": "/heartbeat_receipt", "value": original["heartbeat_receipt"]},
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

    if fault == "runtime" and bounded_route is None:
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
                "--complete-turn", "--decision-output-dir", str(missing_capture))
        assert code == 1 and refused["should_run"] is False
        assert refused["heartbeat_receipt"]["event_id"] == failed["heartbeat_receipt"]["event_id"]
        assert refused["error_code"] == "quota_decision_transport_failed"
        assert "Decision capture failed" in refused["reason"]
        assert "captured_decision" not in refused and "saved" not in refused["recommended_action"]
        assert not (missing_capture / "decision.json").exists()
        assert _heartbeat_receipt_count(runtime, TURN_ID) == 1 and _spend_run_count(runtime) == 0


def test_refusal_that_fits_uses_a_reader_that_can_advance(tmp_path):
    from loopx.cli_commands.quota_failure_report import captured_quota_failure_view
    from loopx.control_plane.turn_driver.decision_capture import write_decision_capture

    directory = tmp_path / ("c" * (271 - len(str(tmp_path / "decision.json")) - 1))
    directory.mkdir()
    capture = directory / "decision.json"
    launcher = tmp_path / ("l" * (180 - len(str(tmp_path)) - 1))
    launcher.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-m", "loopx.entrypoint"]) + ' "$@"\n')
    launcher.chmod(0o700)
    failure = {
        "ok": False,
        "should_run": False,
        "error_code": "quota_decision_transport_failed",
        "diagnostic_code": "runtime_unavailable",
        "heartbeat_receipt": {"turn_instance_id": "synthetic-turn", "event_id": "synthetic-event"},
    }
    diagnostic = {**failure, "composition_error": {"message": "原始故障", "diagnostic_code": "runtime_unavailable"},
        "source_diagnostic": {"unrelated_history": "λ history " * 650}}
    digest = write_decision_capture(capture, diagnostic)
    original = capture.read_bytes()
    refusal = captured_quota_failure_view(failure, capture_directory=directory,
        max_page_output_bytes=1700, capture_sha256=digest, captured_payload=diagnostic,
        reader_argv=[str(launcher), "--cli-bin", str(launcher)], runtime_root=tmp_path)
    assert len(json.dumps(refusal, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 1700
    assert refusal["captured_decision"]["sha256"] == digest
    assert refusal["diagnostic_code"] == diagnostic["diagnostic_code"]

    selected = bytearray()
    command = refusal.get("command") or refusal["captured_decision"]["command"]
    while command:
        result = subprocess.run(shlex.split(command), capture_output=True, check=False, timeout=30)
        assert result.returncode == 0, result.stdout
        assert len(result.stdout) <= 1700
        page = json.loads(result.stdout)
        assert page["ok"] and page["offset"] == len(selected)
        selected.extend(page["content"].encode())
        assert len(selected) == (page["total_bytes"] if page["eof"] else page["next_offset"])
        command = page.get("next_command")
    assert page["eof"] is True
    details = {row["pointer"]: row["value"] for row in json.loads(selected)["selections"]}
    for key in ("heartbeat_receipt", "diagnostic_code", "composition_error"):
        assert details["/" + key] == diagnostic[key]
    assert "/source_diagnostic" not in details
    assert capture.read_bytes() == original


def test_unusable_output_limit_still_identifies_the_original_diagnostic(tmp_path):
    from loopx.cli_commands.quota_failure_report import captured_quota_failure_view
    from loopx.control_plane.turn_driver.decision_capture import write_decision_capture

    source = {
        "ok": False,
        "error_code": "quota_decision_transport_failed",
        "diagnostic_code": "runtime_unavailable",
        "composition_error": {"message": "synthetic composition unavailable"},
        "heartbeat_receipt": {"turn_instance_id": TURN_ID},
    }
    capture = tmp_path / "decision.json"
    digest = write_decision_capture(capture, source)
    original = capture.read_bytes()
    refused = captured_quota_failure_view(source, capture_directory=tmp_path,
        max_page_output_bytes=128, capture_sha256=digest)
    assert refused["ok"] is False and refused["diagnostic_code"] == "runtime_unavailable"
    assert "too small" in refused["instruction"]
    assert refused["captured_decision"]["sha256"] == digest
    command = shlex.split(refused["command"])
    reference = Path(command[command.index("--reference-file") + 1])
    assert json.loads(reference.read_bytes())["sha256"] == digest
    assert capture.read_bytes() == original
