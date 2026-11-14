"""Saved quota presentation never reevaluates the Goal or renews authority."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

import pytest

from tests.control_plane.test_quota_settlement_cli import _run_cli
from tests.control_plane.codex_checkpoint_fixture import reference_metadata_oracle as machine_reference_oracle
from tests.control_plane.codex_checkpoint_fixture import resolve_summary_row_references


@pytest.mark.parametrize("failure", [None, "digest", "goal", "agent", "turn", "symlink", "object"])
@pytest.mark.parametrize("setting", [None, "auto", "off"])
def test_saved_projection_checks_original_before_any_publication(tmp_path, failure, setting):
    original = {"ok": True, "goal_id": "saved-goal", "agent_identity": {"agent_id": "saved-agent"},
        "heartbeat_receipt": {"turn_instance_id": "saved-turn"}, "requirement": "保留完整要求🙂" * 100}
    path = tmp_path / "original.json"
    path.write_text(json.dumps(original, ensure_ascii=False))
    if failure == "object":
        path.write_text("[]")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if failure == "symlink":
        source = tmp_path / "source.json"
        path.rename(source)
        path.symlink_to(source)
    args = ["quota", "context-project", "--capture-file", str(path), "--capture-sha256", digest,
        "--goal-id", "saved-goal", "--agent-id", "saved-agent", "--turn-instance-id", "saved-turn"]
    if setting is not None:
        args.extend(("--context-delivery", setting))
    for name in ("digest", "goal", "agent", "turn"):
        if failure == name:
            option = {"digest": "--capture-sha256", "goal": "--goal-id", "agent": "--agent-id", "turn": "--turn-instance-id"}[name]
            args[args.index(option) + 1] = "0" * 64 if name == "digest" else "foreign"
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    code, result = _run_cli(registry, runtime, *args, cwd=tmp_path)
    assert not registry.exists()
    if failure or setting == "off":
        assert not runtime.exists()
    else:
        # Main's default Progressive presentation may create private transport;
        # it may not create authority, evaluate a guard or debit the saved Turn.
        if runtime.exists():
            assert {item.name for item in runtime.iterdir()} <= {"context-delivery"}
    if failure:
        assert code != 0 and not result["ok"] and "command" not in result
    else:
        assert code == 0 and result["ok"]
        if setting == "off":
            assert result["mode"] == "full"
            assert result["path"] == str(path) and result["sha256"] == digest
            assert result["recovery"]["path"] == str(path)
            command = shlex.split(result["command"])
            code, page = _run_cli(registry, runtime, *command[3:], cwd=tmp_path)
            assert code == 0 and page["content"].encode() == path.read_bytes()[:page["next_offset"]]
        else:
            from tests.control_plane.codex_checkpoint_fixture import read_cli_policy_payload

            assert read_cli_policy_payload(json.dumps(result), returncode=code) == original
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("route", ["stateless", "native", "delta-only", "raw", "pre-session"])
def test_saved_complete_quota_projection_rejects_invalid_source_without_legacy_fallback(
        tmp_path, monkeypatch, provider, route):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _heartbeat_receipt_count,
        _initialize_fixture_authority, _spend_run_count, _write_fixture,
    )
    from loopx.control_plane.turn_driver.codex_context import codex_context_hook

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", TURN_ID]
    directory = tmp_path / "capture"
    code, result = _run_cli(registry, runtime, "quota", "should-run", *binding, "--todo-id", TODO_ID,
        "--complete-turn", "--context-delivery", "off", "--decision-output-dir", str(directory), cwd=project)
    assert code == 0, result
    original = directory / "decision.json"
    raw = original.read_bytes()
    signed = json.loads(raw)
    assert signed["schema_version"] == "loopx_turn_envelope_v1"
    receipt = signed["heartbeat_receipt"]

    options = ["--context-delivery", "off" if route in {"raw", "delta-only"} else "auto"]
    if route in {"stateless", "native", "pre-session"}:
        options += ["--initial-read", "--max-page-output-bytes", "12000"]
    journal = tmp_path / "native"
    session = "source-validation"
    event = {"hook_event_name": "SessionStart", "session_id": session, "cwd": str(project)}
    active_native = route in {"native", "delta-only"}
    if route in {"native", "delta-only", "pre-session"}:
        journal.mkdir(mode=0o700)
        monkeypatch.setenv("CODEX_THREAD_ID", session)
        if active_native:
            codex_context_hook(str(journal), {**event, "source": "startup"})
        options += ["--codex-app", "--codex-context-dir", str(journal)]
    if route == "delta-only":
        options += ["--delta-delivery", "auto"]

    def project_capture(path, *, turn=TURN_ID, goal=GOAL_ID, agent=AGENT_ID, digest=None, delivery=options):
        return _run_cli(registry, runtime, "quota", "context-project", "--goal-id", goal, "--agent-id", agent,
            "--turn-instance-id", turn, *delivery, "--capture-file", str(path),
            "--capture-sha256", digest or hashlib.sha256(path.read_bytes()).hexdigest(), cwd=project)

    lookup = ("quota", "context-read", "--codex-context-dir", str(journal),
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--select", "/heartbeat_receipt")
    prior_turn = "preceding-turn"
    prior_receipt = {"turn_instance_id": prior_turn}
    prior = directory / "preceding.json"
    prior_bytes = json.dumps({"ok": True, "goal_id": GOAL_ID, "agent_identity": {"agent_id": AGENT_ID},
        "heartbeat_receipt": prior_receipt, "requirement": "This requirement belongs to the preceding Turn."}).encode()
    if active_native:
        prior.write_bytes(prior_bytes)

    def assert_preceding_recovery():
        code, page = _run_cli(registry, runtime, *lookup, cwd=project)
        assert code == 0 and page["eof"], page
        assert json.loads(page["content"])["selections"] == [
            {"pointer": "/heartbeat_receipt", "value": prior_receipt},
        ]

    code, valid = project_capture(original)
    assert code == 0 and valid["ok"], valid
    for version, reason in (
        ("loopx_turn_envelope_v2", "Unsupported"), ("loopx_turn_envelope_v0", "mixed"),
        (None, "Unsupported"), ("loopx_turn_envelope_v1", "integrity mismatch"),
    ):
        bad = {**signed, "schema_version": version}
        if version is None:
            del bad["schema_version"]  # The retained binding cannot become an unversioned packet.
        elif version == "loopx_turn_envelope_v1":
            bad["contract_binding"] = {**signed["contract_binding"], "content_hash": "sha256:" + "0" * 64}
        capture = directory / "rejected.json"
        capture.write_text(json.dumps(bad))
        digest = hashlib.sha256(capture.read_bytes()).hexdigest()
        if active_native:
            code, preceding = project_capture(prior, turn=prior_turn,
                delivery=[arg for arg in options if arg != "--initial-read"])
            assert code == 0 and preceding["ok"], preceding
            assert_preceding_recovery()
            if version == "loopx_turn_envelope_v2":
                assert project_capture(capture, digest="0" * 64)[0] == 1
                assert project_capture(capture, turn=prior_turn)[0] == 1
                disabled = ["--context-delivery", "off", "--delta-delivery", "off",
                    "--codex-app", "--codex-context-dir", str(journal)]
                assert project_capture(capture, delivery=disabled)[0] == 1
                with monkeypatch.context() as other:
                    other.setenv("CODEX_THREAD_ID", "different-session")
                    assert project_capture(capture)[0] == 1
            if version in {"loopx_turn_envelope_v2", "loopx_turn_envelope_v1"}:
                foreign = directory / "foreign.json"
                foreign.write_text(json.dumps({**bad, "goal_id": "different-goal"}))
                assert project_capture(foreign, goal="different-goal")[0] == 1
                foreign.write_text(json.dumps({**bad, "agent_identity": {"agent_id": "different-agent"}}))
                assert project_capture(foreign, agent="different-agent")[0] == 1
                assert_preceding_recovery()
        code, rejected = project_capture(capture)
        assert code == 1 and rejected["ok"] is False, rejected
        assert rejected["error_code"] == "quota_decision_transport_failed" and reason in rejected["reason"]
        assert "content" not in rejected and "first_read_command" not in rejected
        assert rejected["heartbeat_receipt"] == receipt
        assert rejected["recovery"]["path"] == str(capture) and rejected["recovery"]["sha256"] == digest
        recovery = shlex.split(rejected["recovery"]["command"])
        assert "--list" not in recovery and "--initial-read" not in recovery
        assert "diagnostic" in rejected["instruction"] and "retry context-project" in rejected["instruction"]
        rc, page = _run_cli(registry, runtime, *recovery[recovery.index("quota"):], cwd=project)
        assert rc == 0 and page["eof"]
        assert json.loads(page["content"])["selections"] == [{"pointer": "/heartbeat_receipt", "value": receipt}]
        if active_native:
            code, implicit = _run_cli(registry, runtime, *lookup, cwd=project)
            assert code == 1 and implicit["ok"] is False, implicit
            assert not codex_context_hook(str(journal), {**event, "source": "compact"})
    journal_state = journal / "session.json"
    if active_native:
        state = json.loads(journal_state.read_text())
        assert "recovery" not in state and "pending" not in state
        code, explicit = _run_cli(registry, runtime, "quota", "context-read",
            "--capture-file", str(prior), "--capture-sha256", hashlib.sha256(prior_bytes).hexdigest(), cwd=project)
        assert code == 0 and explicit["content"].encode() == prior_bytes
        assert project_capture(original)[0] == 0
    else:
        assert not journal_state.exists()
    assert original.read_bytes() == raw
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1 and _spend_run_count(runtime) == 0


@pytest.mark.skipif(os.name != "posix", reason="Requires a POSIX launcher with an isolated PATH")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("limit", [1700, 12000], ids=["tight", "ordinary"])
def test_saved_projection_can_read_original_receipt_while_runtime_is_unavailable(
        tmp_path, monkeypatch, provider, limit):
    import subprocess

    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _heartbeat_receipt_count,
        _initialize_fixture_authority, _spend_run_count, _write_fixture,
    )

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", TURN_ID]
    directory = tmp_path / ("capture-" + "x" * 115)
    code, result = _run_cli(registry, runtime, "quota", "should-run", *binding, "--todo-id", TODO_ID,
        "--complete-turn", "--context-delivery", "off", "--decision-output-dir", str(directory), cwd=project)
    assert code == 0 and result["heartbeat_receipt"]["status"] == "committed", result
    source = directory / "decision.json"
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    receipt = json.loads(raw)["heartbeat_receipt"]
    launcher = tmp_path / ("launcher-" + "x" * 120)
    core_python = os.environ.get("LOOPX_CORE_TEST_PYTHON")
    program = [core_python, "-I", "-m", "loopx.entrypoint"] if core_python else [sys.executable, "-m", "loopx.entrypoint"]
    launcher.write_text("#!/bin/sh\nexec " + shlex.join(program) + ' "$@"\n')
    launcher.chmod(0o700)
    # The admission fixture's already running Effect process must not mask
    # the recovery host's inability to start its own.
    missing = tmp_path / "no-runtime"
    missing.mkdir()
    env = {**os.environ, "PATH": str(missing), "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        **{name: str(missing) for name in ("TMPDIR", "TEMP", "TMP")}}

    def call(argv):
        return subprocess.run(argv, cwd=project, env=env, capture_output=True, check=False, timeout=30)

    command = [str(launcher), "--cli-bin", str(launcher), "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", "quota", "context-project", *binding,
        "--capture-file", str(source), "--capture-sha256", digest, "--context-delivery", "auto",
        "--initial-read", "--max-page-output-bytes", str(limit)]
    output = call(command)
    assert output.returncode == 1 and 0 < len(output.stdout) <= limit, output
    failure = json.loads(output.stdout)
    assert failure["ok"] is False and failure["error_code"] == "quota_decision_transport_failed"
    assert failure.get("diagnostic_code") == "node_unavailable"
    assert "content" not in failure and "first_read_command" not in failure
    reader = failure.get("recovery_command") or failure["recovery"]["command"]
    body = b""
    for _ in range(10):
        args = shlex.split(reader)
        assert args[:3] == [str(launcher), "--cli-bin", str(launcher)]
        page_run = call(args)
        assert page_run.returncode == 0 and 0 < len(page_run.stdout) <= limit, page_run.stdout
        page = json.loads(page_run.stdout)
        assert page["ok"] and page["offset"] == len(body)
        body += page["content"].encode()
        assert len(body) == (page["total_bytes"] if page["eof"] else page["next_offset"])
        if page["eof"]:
            break
        reader = page["next_command"]
    else:
        pytest.fail("Diagnostic receipt reader did not reach EOF")
    assert json.loads(body)["selections"] == [{"pointer": "/heartbeat_receipt", "value": receipt}]
    if limit == 1700:
        # If the host cannot fit a usable cursor at all, keep the refusal
        # bounded and typed without sending it to an unreadable command.
        tiny = call([*command[:-1], "128"])
        tiny_failure = json.loads(tiny.stdout)
        assert tiny.returncode == 1 and len(tiny.stdout) <= 128
        assert tiny_failure == {"ok": False, "error_code": "quota_decision_transport_failed",
            "diagnostic_code": "node_unavailable"}
        source.write_bytes(raw + b" ")
        try:
            refused = call(shlex.split(reader))
            damaged = json.loads(refused.stdout)
            assert refused.returncode == 1 and "capture digest mismatch" in damaged["reason"]
            assert "content" not in damaged
        finally:
            source.write_bytes(raw)
    assert source.read_bytes() == raw
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 1 and _spend_run_count(runtime) == 0


@pytest.mark.parametrize("root_kind", ["unsafe", "symlink", "missing_parent"])
@pytest.mark.parametrize(("opening_args", "recovery_limit"), [
    ([], 64000), (["--initial-read"], 64000),
    (["--initial-read", "--max-page-output-bytes", "12000"], 12000),
], ids=["linked", "attached", "explicit"])
def test_enabled_cache_preparation_failure_returns_error_with_original_reference(
        tmp_path, monkeypatch, root_kind, opening_args, recovery_limit):
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import project_saved_context_capture

    path, root, context = tmp_path / "original.json", tmp_path / "cache", tmp_path / "native"
    raw = json.dumps({"ok": True, "goal_id": "saved-goal", "agent_identity": {"agent_id": "saved-agent"},
        "heartbeat_receipt": {"turn_instance_id": "saved-turn"}}).encode()
    path.write_bytes(raw)
    context.mkdir(mode=0o700)
    stale = context / "session.json"
    stale.write_text(json.dumps({"session_id": "native-session", "cwd": str(tmp_path),
        "recovery": {"goal_id": "saved-goal", "agent_id": "saved-agent",
            "path": str(tmp_path / "prior.json"), "sha256": "a" * 64}}))
    stale_bytes = stale.read_bytes()
    monkeypatch.setenv("CODEX_THREAD_ID", "native-session")
    monkeypatch.chdir(tmp_path)
    if root_kind == "unsafe":
        root.mkdir(mode=0o755)
        root.chmod(0o755)
    elif root_kind == "symlink":
        root.symlink_to(context, target_is_directory=True)
    if root_kind == "missing_parent":
        root = root / "uncreated-parent" / "cache"
    digest = hashlib.sha256(raw).hexdigest()
    args = build_parser().parse_args(["--format", "json", "quota", "context-project",
        "--capture-file", str(path), "--capture-sha256", digest, "--goal-id", "saved-goal",
        "--agent-id", "saved-agent", "--turn-instance-id", "saved-turn",
        "--decision-output-root", str(root), "--context-delivery", "on", "--codex-app",
        "--codex-context-dir", str(context), *opening_args])
    result = project_saved_context_capture(args)
    assert result["ok"] is False and result["error_code"] == "quota_decision_transport_failed"
    assert result["recovery"]["path"] == str(path) and result["recovery"]["sha256"] == digest
    recovery = shlex.split(result["recovery"]["command"])
    assert int(recovery[recovery.index("--max-page-output-bytes") + 1]) == recovery_limit
    assert "prior.json" not in json.dumps(result)
    assert path.read_bytes() == raw and stale.read_bytes() == stale_bytes



@pytest.mark.parametrize("enabled", [None, False, True], ids=["omitted", "off", "on"])
@pytest.mark.parametrize("cache_location", ["default", "new", "existing"])
def test_saved_projection_manages_private_cache_without_authority_io(tmp_path, monkeypatch, enabled, cache_location):
    from loopx.control_plane.turn_driver.codex_context import read_codex_context_reference

    native_enabled = enabled is True or (enabled is None and os.name == "posix")
    context, root = tmp_path / "native", tmp_path / "cache"
    context.mkdir(mode=0o700)
    if cache_location == "existing":
        root.mkdir(mode=0o700)
    path = tmp_path / "original.json"
    original = {"ok": True, "goal_id": "saved-goal", "agent_identity": {"agent_id": "saved-agent"},
        "heartbeat_receipt": {"turn_instance_id": "saved-turn"},
        "requirement": "Retain every requirement and the original decision identity."}
    raw = json.dumps(original).encode()
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setenv("CODEX_THREAD_ID", "native-session")
    monkeypatch.chdir(tmp_path)
    if native_enabled:
        from loopx.control_plane.turn_driver.codex_context import codex_context_hook
        codex_context_hook(str(context), {"hook_event_name": "SessionStart", "source": "startup",
            "session_id": "native-session", "cwd": str(tmp_path)})
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    args = ["quota", "context-project", "--capture-file", str(path), "--capture-sha256", digest,
        "--goal-id", "saved-goal", "--agent-id", "saved-agent", "--turn-instance-id", "saved-turn",
        "--codex-app", "--codex-context-dir", str(context)]
    if enabled is not None:
        args += ["--context-delivery", "on" if enabled else "off"]
    if cache_location != "default":
        args += ["--decision-output-root", str(root)]
    code, result = _run_cli(registry, runtime, *args, cwd=tmp_path)
    assert code == 0 and result["ok"] and result["mode"] == "full", result
    assert path.read_bytes() == raw and not registry.exists() and not runtime.exists()
    if native_enabled:
        assert result["schema_version"] == "context_capture_navigation_v1", result
        assert "content" not in result and "offset" not in result
        machine = machine_reference_oracle(result)
        assert "index_command" in machine and "select_command" in machine
        assert json.loads(Path(machine["path"]).read_bytes()) == original
        actual_root = context if cache_location == "default" else root
        assert actual_root.stat().st_mode & 0o077 == 0
        assert list(actual_root.glob("context-*"))
        recovered_path, recovered_digest = read_codex_context_reference(str(context),
            goal_id="saved-goal", agent_id="saved-agent")
        recovered = Path(recovered_path).read_bytes()
        assert hashlib.sha256(recovered).hexdigest() == recovered_digest
        assert json.loads(recovered) == original
    else:
        assert "content" not in result and "schema_version" not in result
        assert not list(context.iterdir())
        assert root.exists() == (cache_location == "existing")
        # A generic reader has no native observer. Keep its complete-read
        # obligations instead of promising an input proof that never ran.
        instruction = result["instruction"]
        assert "Native input observation" not in instruction
        assert "Check each model-visible content's UTF-8 byte length" in instruction
        assert "complete coverage and the original SHA-256" in instruction
        assert "If incomplete, reread the affected range of this same capture" in instruction
    command = shlex.split(result["command"])
    code, page = _run_cli(registry, runtime, *command[3:], cwd=tmp_path)
    assert code == 0 and json.loads(page["content"]) == original


@pytest.mark.parametrize("body", ["Exact requirement.\n" * 7000, "完整要求与来源🙂\n" * 7000], ids=["ascii", "utf8"])
@pytest.mark.parametrize("deep_path", [False, True], ids=["short", "deep"])
def test_saved_projection_reference_allows_optional_exact_read_and_restart(tmp_path, monkeypatch, body, deep_path):
    from loopx.control_plane.turn_driver.codex_context import codex_context_hook
    from loopx.control_plane.turn_driver.codex_input_trace import _capture_page_range

    if deep_path:
        tmp_path = tmp_path / ("capture-location-" * 9) / ("original-context-" * 9)
        tmp_path.mkdir(parents=True)
    context = tmp_path / "native"
    context.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "native-session")
    monkeypatch.chdir(tmp_path)
    codex_context_hook(str(context), {"hook_event_name": "SessionStart", "source": "startup",
        "session_id": "native-session", "cwd": str(tmp_path)})
    source = tmp_path / "original.json"
    raw = json.dumps({"ok": True, "goal_id": "goal", "agent_identity": {"agent_id": "agent"},
        "heartbeat_receipt": {"turn_instance_id": "turn"}, "requirement": body}, ensure_ascii=False).encode()
    source.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    registry, runtime = tmp_path / "absent.json", tmp_path / "absent-runtime"
    code, result = _run_cli(registry, runtime, "quota", "context-project", "--capture-file", str(source),
        "--capture-sha256", digest, "--goal-id", "goal", "--agent-id", "agent", "--turn-instance-id", "turn",
        "--context-delivery", "on", "--codex-app", "--codex-context-dir", str(context), cwd=tmp_path)
    assert code == 0 and result["mode"] == "full", result
    # The default is a reference regardless of file length. An intentional full
    # read remains lossless and paginated, without changing the saved decision.
    assert "content" not in result and "offset" not in result
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 8192
    machine = machine_reference_oracle(result)
    delivered = Path(machine["path"]).read_bytes()
    delivered_digest = hashlib.sha256(delivered).hexdigest()
    assert machine["sha256"] == delivered_digest and json.loads(delivered) == json.loads(raw)
    combined = b""
    command = result["command"]
    while True:
        argv = shlex.split(command)
        code, page = _run_cli(registry, runtime, *argv[argv.index("quota"):], cwd=tmp_path)
        assert code == 0 and page["offset"] == len(combined)
        assert _capture_page_range(page, raw=delivered, digest=delivered_digest) == (len(combined), len(combined) + len(page["content"].encode()))
        assert _capture_page_range({**page, "content": page["content"][:-1]}, raw=delivered, digest=delivered_digest) is None
        assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 64000
        combined += page["content"].encode()
        if page["eof"]:
            break
        command = page["next_command"]
    assert combined == delivered and hashlib.sha256(combined).hexdigest() == delivered_digest
    argv = shlex.split(result["command"])
    code, restarted = _run_cli(registry, runtime, *argv[argv.index("quota"):], cwd=tmp_path)
    assert code == 0 and restarted["offset"] == 0
    assert restarted["content"].encode() == delivered[:restarted["next_offset"]]
    assert source.read_bytes() == raw and not registry.exists() and not runtime.exists()


@pytest.mark.parametrize("failure", ["wire", "inline_read", "reader_reference", "reader_recovery_reference"])
def test_saved_projection_wire_failure_keeps_committed_current_recovery(tmp_path, monkeypatch, failure):
    import errno

    from loopx.cli_commands import quota_capture
    from loopx.control_plane.turn_driver import codex_context, decision_capture

    path, root, context = tmp_path / "original.json", tmp_path / "cache", tmp_path / "native"
    original = {"ok": True, "goal_id": "saved-goal", "agent_identity": {"agent_id": "saved-agent"},
        "heartbeat_receipt": {"turn_instance_id": "saved-turn"}, "requirement": "Current complete requirements."}
    raw = json.dumps(original).encode()
    path.write_bytes(raw)
    root.mkdir(mode=0o700)
    context.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "native-session")
    monkeypatch.chdir(tmp_path)
    codex_context.codex_context_hook(str(context), {"hook_event_name": "SessionStart", "source": "startup",
        "session_id": "native-session", "cwd": str(tmp_path)})
    write_capture = decision_capture.write_decision_capture

    def fail_optional_wire(path, payload, **kwargs):
        if failure == "wire" and path.name == "delivery.json":
            raise OSError("optional wire unavailable")
        return write_capture(path, payload, **kwargs)

    monkeypatch.setattr(decision_capture, "write_decision_capture", fail_optional_wire)
    if failure == "inline_read":
        monkeypatch.setattr(quota_capture, "read_context_capture", lambda args, **kwargs: {"ok": False})
    elif failure.startswith("reader_"):
        publish = quota_capture.write_context_read_reference
        calls = []

        def fail_reference(*args, **kwargs):
            calls.append(args)
            if len(calls) == (2 if failure == "reader_recovery_reference" else 1):
                raise OSError(errno.ENOSPC, "synthetic reference publication failure")
            return publish(*args, **kwargs)

        monkeypatch.setattr(quota_capture, "write_context_read_reference", fail_reference)
    digest = hashlib.sha256(raw).hexdigest()
    code, wire = _run_cli(tmp_path / "absent.json", tmp_path / "absent-runtime", "quota", "context-project",
        "--capture-file", str(path), "--capture-sha256", digest, "--goal-id", "saved-goal",
        "--agent-id", "saved-agent", "--turn-instance-id", "saved-turn", "--context-delivery", "on",
        "--codex-app", "--codex-context-dir", str(context), "--decision-output-root", str(root), cwd=tmp_path)
    assert code == 0, wire  # Actual CLI must return JSON, never an IO traceback.
    result = machine_reference_oracle(wire)
    assert result["ok"] and result["mode"] == "full"
    recovered_path, recovered_digest = codex_context.read_codex_context_reference(str(context),
        goal_id="saved-goal", agent_id="saved-agent")
    assert result["capture_sha256"] == digest
    if failure == "wire":
        assert result["path"] == recovered_path and result["sha256"] == recovered_digest
    else:
        assert "content" not in result and result["recovery"]["sha256"] == recovered_digest
        argv = shlex.split(wire["command"])
        code, page = _run_cli(tmp_path / "absent.json", tmp_path / "absent-runtime",
            *argv[argv.index("quota"):], cwd=tmp_path)
        assert code == 0 and page["offset"] == 0 and json.loads(page["content"]) == original
    recovered = Path(recovered_path).read_bytes()
    assert hashlib.sha256(recovered).hexdigest() == recovered_digest
    assert json.loads(recovered) == original and path.read_bytes() == raw


@pytest.mark.parametrize("source", ["compact", "resume"])
def test_saved_projection_planner_process_exit_keeps_current_recovery(tmp_path, monkeypatch, source):
    import subprocess
    from loopx.control_plane.turn_driver import codex_context
    from tests.control_plane.test_codex_context_lifecycle import setup, project, deliver, read_pages

    native, captures = tmp_path / "native", tmp_path / "captures"
    native.mkdir(mode=0o700)
    captures.mkdir(mode=0o700)
    event = setup(native, monkeypatch)
    prior = {"ok": True, "goal_id": "fixture", "agent_identity": {"agent_id": "agent"},
        "heartbeat_receipt": {"turn_instance_id": "prior-turn"}, "requirement": "Stop before publication."}
    _, delivered = project(native, payload=prior)
    deliver(native, event, read_pages(native, delivered))
    assert codex_context._state(native)["candidate"]
    prior_file = Path(delivered["recovery"]["path"])
    prior_bytes = prior_file.read_bytes()

    current = {**prior, "heartbeat_receipt": {"turn_instance_id": "current-turn"},
        "requirement": "Retain this changed requirement and stop before deployment."}
    original = tmp_path / "current.json"
    raw = json.dumps(current).encode()
    original.write_bytes(raw)
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    script = (
        "import os\n"
        "from loopx.control_plane.quota import turn_envelope\n"
        "def interrupt(*args, **kwargs): os._exit(37)\n"
        "turn_envelope.effect_runtime_projection_result = interrupt\n"
        "from loopx.cli import main\n"
        "raise SystemExit(main())\n"
    )
    arguments = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "quota", "context-project", "--capture-file", str(original),
        "--capture-sha256", hashlib.sha256(raw).hexdigest(), "--goal-id", "fixture", "--agent-id", "agent",
        "--turn-instance-id", "current-turn", "--context-delivery", "on", "--codex-app",
        "--codex-context-dir", str(native), "--decision-output-root", str(captures)]
    stopped = subprocess.run([sys.executable, "-c", script, *arguments], cwd=native,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        capture_output=True, text=True, timeout=30)
    assert stopped.returncode == 37, stopped.stdout + stopped.stderr
    current_files = list(captures.rglob("projection.json"))
    assert len(current_files) == 1 and json.loads(current_files[0].read_bytes()) == current
    journal = codex_context._state(native)
    assert journal["recovery"]["path"] == str(current_files[0])
    assert journal["pending"]["ranges"] == {}

    resumed = codex_context.codex_context_hook(str(native), {**event, "source": source})
    assert journal["recovery"]["sha256"] in resumed["hookSpecificOutput"]["additionalContext"]
    assert "candidate" not in codex_context._state(native)
    code, page = _run_cli(registry, runtime, "quota", "context-read", "--codex-context-dir", str(native),
        "--goal-id", "fixture", "--agent-id", "agent", cwd=native)
    assert code == 0 and page["eof"] and json.loads(page["content"]) == current
    assert page["sha256"] == journal["recovery"]["sha256"]
    assert prior_file.read_bytes() == prior_bytes and original.read_bytes() == raw
    assert not registry.exists() and not runtime.exists()


def test_saved_projection_recovery_failure_returns_typed_error_and_current_reference(
    tmp_path, monkeypatch, capsys,
):
    from loopx.cli import main
    from loopx.control_plane.quota.error_codes import QuotaDecisionTransportError
    from loopx.control_plane.turn_driver import codex_context

    original = {"ok": True, "goal_id": "saved-goal", "agent_identity": {"agent_id": "saved-agent"},
        "heartbeat_receipt": {"turn_instance_id": "saved-turn", "status": "committed"},
        "requirement": "Use the current captured decision, never the prior session reference."}
    raw = json.dumps(original).encode()
    path, root, context = tmp_path / "original.json", tmp_path / "cache", tmp_path / "native"
    path.write_bytes(raw)
    root.mkdir(mode=0o700)
    context.mkdir(mode=0o700)
    stale = context / "session.json"
    stale.write_text(json.dumps({"recovery": {"path": "prior-capture.json"}}))
    stale_bytes = stale.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()

    def refuse_recovery(payload, **kwargs):
        assert payload == original
        raise QuotaDecisionTransportError(
            'Current context recovery could not be published or safely invalidated. '
            'Read the saved original decision/receipt; do not rerun admission.'
        )

    monkeypatch.setattr(codex_context, "prepare_codex_context", refuse_recovery)
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "quota", "context-project", "--capture-file", str(path), "--capture-sha256", digest,
        "--goal-id", "saved-goal", "--agent-id", "saved-agent", "--turn-instance-id", "saved-turn",
        "--decision-output-root", str(root), "--context-delivery", "on", "--codex-app",
        "--codex-context-dir", str(context)])
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result["ok"] is False
    assert result["error_code"] == "quota_decision_transport_failed"
    assert result["heartbeat_receipt"] == original["heartbeat_receipt"]
    assert result["capture_sha256"] == digest
    assert result["recovery"]["path"] == str(path) and result["recovery"]["sha256"] == digest
    assert "prior-capture" not in json.dumps(result)
    assert not list(root.glob("*/delivery.json"))
    assert path.read_bytes() == raw and stale.read_bytes() == stale_bytes
    assert not registry.exists() and not runtime.exists()
    code, recovered = _run_cli(registry, runtime, "quota", "context-read",
        "--capture-file", result["recovery"]["path"], "--capture-sha256", result["recovery"]["sha256"])
    assert code == 0 and recovered["eof"] and recovered["content"].encode() == raw
    assert not registry.exists() and not runtime.exists()


def test_preflight_instruction_pins_projection_and_diagnostic_receipt(tmp_path):
    from benchmark.runtime.context_delivery import preflight_context_instruction
    from loopx.control_plane.turn_driver.decision_capture import CONTEXT_CAPTURE_NAVIGATION_GUIDANCE

    source = tmp_path / "saved packet.json"
    env = {"LOOPX_CLI": "/fixture/bin/loopx", "LOOPX_GOAL_ID": "goal",
        "LOOPX_AGENT_ID": "agent", "LOOPX_CODEX_CONTEXT_DIR": str(tmp_path / "private native")}
    body = preflight_context_instruction(env, source, "a" * 64, "original-turn")
    # The worker must carry the product's current read obligations, rather
    # than retain its own eager-read/model-retention policy after a cutover.
    assert CONTEXT_CAPTURE_NAVIGATION_GUIDANCE in body
    commands = [shlex.split(block.split("\n```", 1)[0]) for block in body.split("```sh\n")[1:]]
    assert len(commands) == 2
    assert commands[0][6] == "context-project" and commands[1][6] == "context-read"
    for argv in commands:
        assert argv[:3] == [env["LOOPX_CLI"], "--cli-bin", env["LOOPX_CLI"]]
        assert argv[argv.index("--capture-file") + 1] == str(source)
        assert argv[argv.index("--capture-sha256") + 1] == "a" * 64
        assert "should-run" not in argv and "spend-slot" not in argv
    assert commands[0][commands[0].index("--turn-instance-id") + 1] == "original-turn"
    assert "--initial-read" in commands[0]
    assert "--decision-output-root" not in commands[0]
    assert commands[1][commands[1].index("--select") + 1:] == ["/heartbeat_receipt"]
    assert "--list" not in commands[1]
    assert "retry the same context-project" in body


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("enabled,compacted,placement,display", [
    (False, False, "physical", "ordinary"), (True, False, "physical", "ordinary"),
    (True, True, "physical", "ordinary"), (True, False, "alias-before-session", "ordinary"),
    (True, False, "explicit-before-session", "ordinary"), (True, False, "explicit-root", "ordinary"),
    (False, False, "physical", "complete"), (True, False, "physical", "complete"),
    (True, False, "physical", "envelope"),
], ids=["off", "on", "on-after-compaction", "alias-before-session", "explicit-before-session", "explicit-root",
        "complete-off", "complete-on", "envelope-on"])
def test_checkpoint_preflight_selection_inherits_native_transport(
    tmp_path, monkeypatch, provider, enabled, compacted, placement, display,
):
    """Execute same-Turn selection from the saved guard with and without SessionStart."""
    from benchmark.runtime.checkpoint import capture_before_replan
    from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.turn_driver.codex_context import codex_context_hook
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TODO_ID, _configure_selectable_alternative,
        _heartbeat_receipt_count, _spend_run_count, _write_fixture,
    )

    import subprocess

    tmp_path = tmp_path.resolve()
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path / "authority")
    _configure_selectable_alternative(project)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=read_fixture_todos(state), handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    original_state = state.read_bytes()
    native = tmp_path / "private native"
    native.mkdir(mode=0o700)
    turn = "preflight-transport"
    wake = tmp_path / "wakes" / turn
    wake.mkdir(parents=True)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"trigger": "fresh_replan", "destination": str(tmp_path / "checkpoints"),
        "components": [{"id": "task", "source_path": str(state)}], "point_metadata": {}}))
    cli = tmp_path / "loopx-cli"
    cli.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' -m loopx.entrypoint "$@"\n')
    cli.chmod(0o700)
    monkeypatch.setenv("LOOPX_EXECUTION_MODE", "heartbeat")
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "on" if enabled else "off")
    env = {**os.environ, "LOOPX_CLI": str(cli), "LOOPX_PROJECT": str(project),
        "LOOPX_REGISTRY": str(registry), "LOOPX_RUNTIME_ROOT": str(runtime),
        "LOOPX_GOAL_ID": GOAL_ID, "LOOPX_AGENT_ID": AGENT_ID,
        "LOOPX_WAKE_LOG_DIR": str(wake.parent), "LOOPX_CHECKPOINT_REQUEST": str(request),
        "LOOPX_COMPLETE_TURN": str(int(display == "complete")),
        "LOOPX_TURN_ENVELOPE": str(int(display == "envelope")),
        "LOOPX_CONTEXT_DELIVERY": "on" if enabled else "off", "LOOPX_CODEX_CONTEXT_DIR": str(native)}
    env.pop("CODEX_THREAD_ID", None)
    receipt = {"turn_id": turn, "mode": "heartbeat", "stage": "execute"}
    alias = tmp_path / "capture parent alias"
    if placement != "physical":
        run = subprocess.run

        def through_alias(argv, **kwargs):
            if "--prepared-capture-reference" in argv:
                argv = list(argv)
                i = argv.index("--decision-output-dir") + 1
                requested = Path(argv[i])
                alias.symlink_to(requested.parent, target_is_directory=True)
                argv[i] = str(alias / requested.name)
            return run(argv, **kwargs)

        # Change only how the CLI reaches the directory already allocated by
        # the checkpoint runner. Guard, capture, projection and selection are real.
        with monkeypatch.context() as scoped:
            scoped.setattr("benchmark.runtime.checkpoint.subprocess.run", through_alias)
            body = capture_before_replan(env, receipt, "Current work.")
    else:
        body = capture_before_replan(env, receipt, "Current work.")
    original_guard = (wake / "checkpoint-start-guard.json").read_bytes()
    captured_source = json.loads(original_guard)
    from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision

    assert_complete_turn_decision(captured_source)
    if enabled:
        command = shlex.split(body.split("```sh\n", 1)[1].split("\n```", 1)[0])
        source = Path(command[command.index("--capture-file") + 1])
        assert source.parent != wake and source.is_relative_to(wake)
        assert command[command.index("--capture-sha256") + 1] == hashlib.sha256(source.read_bytes()).hexdigest()
        if placement != "physical":
            command[command.index("--capture-file") + 1] = str(alias / source.relative_to(source.parent.parent))
    else:
        source, = wake.glob("checkpoint-start-*/checkpoint-start-view.json")
    raw = source.read_bytes()
    if enabled:
        assert raw == original_guard
    assert _heartbeat_receipt_count(runtime, turn) == 1 and _spend_run_count(runtime) == 0
    assert not list(native.iterdir())  # Host preflight must not publish before native SessionStart.

    event = {"session_id": "native-session", "cwd": str(project), "hook_event_name": "SessionStart", "source": "startup"}
    lifecycle = enabled and placement not in {"alias-before-session", "explicit-before-session"}
    monkeypatch.chdir(project)
    if lifecycle:
        monkeypatch.setenv("CODEX_THREAD_ID", event["session_id"])
        codex_context_hook(str(native), event)
    else:
        monkeypatch.delenv("CODEX_THREAD_ID", raising=False)

    def consume(wire):
        data = bytearray()
        argv = shlex.split(wire["command"])
        rc, page = _run_cli(registry, runtime, *argv[argv.index("quota"):], cwd=project)
        assert rc == 0, page
        while True:
            assert page["ok"] and page["offset"] == len(data)
            data.extend(page["content"].encode())
            if lifecycle:
                codex_context_hook(str(native), {**event, "hook_event_name": "PostToolUse", "tool_response": json.dumps(page)})
            if page["eof"]:
                assert hashlib.sha256(data).hexdigest() == machine_reference_oracle(wire)["sha256"]
                return json.loads(data)
            argv = shlex.split(page["next_command"])
            rc, page = _run_cli(registry, runtime, *argv[argv.index("quota"):], cwd=project)
            assert rc == 0, page

    page_limit = 8000 if placement == "explicit-before-session" else 32000
    if enabled:
        command.extend(("--max-page-output-bytes", str(page_limit)))
        expected_root = source.parent.parent
        if placement in {"explicit-root", "explicit-before-session"}:
            expected_root = tmp_path / "explicit capture root"
            expected_root.mkdir(mode=0o700)
            command.extend(("--decision-output-root", str(expected_root)))
        rc, cold = _run_cli(registry, runtime, *command[command.index("quota"):], cwd=project)
        assert rc == 0 and cold["navigation"]["mode"] == "full" and cold["offset"] == 0, cold
        assert len(json.dumps(cold, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= page_limit
        presented = consume(cold["navigation"])
        if compacted:
            codex_context_hook(str(native), {**event, "hook_event_name": "PreCompact"})
    else:
        presented = json.loads(raw)
    selection = presented["interaction_contract"]["cli_channel"]["selection_command"]
    argv = shlex.split(selection["route_prefix"] + " " + selection["command_args_template"].format(todo_id=TODO_ID))
    from loopx.cli import build_parser
    parsed = build_parser().parse_args(argv[1:])
    assert parsed.complete_turn is (display != "envelope")
    assert parsed.turn_envelope is (display == "envelope")
    assert (parsed.context_delivery == "on") is enabled
    assert parsed.codex_context_dir == (str(native) if enabled else None)
    assert parsed.max_page_output_bytes == (page_limit if enabled else None)
    assert argv.count("--decision-output-root") == 1
    if placement == "alias-before-session":
        rejected_argv = argv.copy()
        rejected_argv[rejected_argv.index("--decision-output-root") + 1] = str(alias)
        rc, rejected = _run_cli(registry, runtime, *rejected_argv[1:], cwd=project)
        assert rc == 1 and "no guard was run" in rejected["reason"], rejected
        assert list(expected_root.glob("*/decision.json")) == [source]
        assert _heartbeat_receipt_count(runtime, turn) == 1
    rc, bound = _run_cli(registry, runtime, *argv[1:], cwd=project)
    assert rc == 0, bound
    if enabled:
        assert Path(parsed.decision_output_root) == expected_root
        assert len(json.dumps(bound, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= page_limit
        [selected_capture] = set(expected_root.glob("*/decision.json")) - {source}
        digest = machine_reference_oracle(bound)["capture_sha256"]
        rc, page = _run_cli(registry, runtime, "quota", "context-read", "--capture-file", str(selected_capture),
            "--capture-sha256", digest, "--select", "/heartbeat_receipt/settlement_identity", cwd=project)
        assert rc == 0 and page["eof"], page
        [identity] = json.loads(page["content"])["selections"]
        selected_receipt = identity["value"]
        assert (selected_receipt["goal_id"], selected_receipt["agent_id"], selected_receipt["turn_instance_id"],
                selected_receipt["todo_id"]) == (GOAL_ID, AGENT_ID, turn, TODO_ID)
        if placement in {"explicit-root", "explicit-before-session"}:
            assert list(source.parent.parent.glob("*/decision.json")) == [source]
        if placement == "explicit-before-session":
            assert bound["mode"] == "full", bound
            assert machine_reference_oracle(cold)["recovery"]["sha256"] == hashlib.sha256(raw).hexdigest()
            for key in ("first_read_command", "command"):
                rc, first_page = _run_cli(registry, runtime, *shlex.split(bound[key])[1:], cwd=project)
                assert rc == 0 and first_page["ok"], first_page
                assert len(json.dumps(first_page, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= page_limit
        if lifecycle:
            assert bound["mode"] == ("full" if compacted else "delta"), bound
            current = Path(machine_reference_oracle(bound)["recovery"]["path"])
            captured = json.loads(current.with_name("decision.json").read_bytes())
            required = json.loads(current.read_bytes())
            assert json.dumps(required["quota"], sort_keys=True) == json.dumps(captured["quota"], sort_keys=True)
            delivered = consume(bound)
            if not compacted:
                assert delivered["base"]["capture_id"] == machine_reference_oracle(cold["navigation"])["target"]["capture_id"]
    else:
        assert bound["mode"] == "should-run" and not list(native.iterdir())
    assert _heartbeat_receipt_count(runtime, turn) == 2 and _spend_run_count(runtime) == 0
    assert source.read_bytes() == raw and (wake / "checkpoint-start-guard.json").read_bytes() == original_guard
    assert state.read_bytes() == original_state


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("display", ["ordinary", "complete", "envelope"])
def test_checkpoint_preflight_rejects_an_unbound_current_source(tmp_path, monkeypatch, provider, display):
    import subprocess

    from benchmark.runtime.checkpoint import capture_before_replan
    from loopx.control_plane.effect_runtime import EffectRuntimeRejected
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, _heartbeat_receipt_count, _initialize_fixture_authority, _spend_run_count, _write_fixture,
    )

    project, runtime, registry = _write_fixture(tmp_path / "authority")
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = "preflight-source-downgrade"
    wake = tmp_path / "wakes" / turn
    wake.mkdir(parents=True)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"trigger": "fresh_replan", "destination": str(tmp_path / "checkpoints"),
        "components": [{"id": "task", "source_path": str(project)}], "point_metadata": {}}))
    cli = tmp_path / "loopx-cli"
    cli.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' -m loopx.entrypoint "$@"\n')
    cli.chmod(0o700)
    env = {**os.environ, "LOOPX_CLI": str(cli), "LOOPX_PROJECT": str(project),
        "LOOPX_REGISTRY": str(registry), "LOOPX_RUNTIME_ROOT": str(runtime),
        "LOOPX_GOAL_ID": GOAL_ID, "LOOPX_AGENT_ID": AGENT_ID, "LOOPX_COMPLETE_TURN": str(int(display == "complete")),
        "LOOPX_TURN_ENVELOPE": str(int(display == "envelope")), "LOOPX_CONTEXT_DELIVERY": "off",
        "LOOPX_WAKE_LOG_DIR": str(wake.parent), "LOOPX_CHECKPOINT_REQUEST": str(request)}
    run = subprocess.run

    def substituted_capture(argv, **kwargs):
        result = run(argv, **kwargs)
        root = Path(argv[argv.index("--decision-output-root") + 1])
        [source] = root.glob("decision-*/decision.json")
        original = json.loads(source.read_bytes())
        original.pop("schema_version", None)
        original.pop("contract_binding", None)
        source.write_text(json.dumps(original))
        return result

    monkeypatch.setattr("benchmark.runtime.checkpoint.subprocess.run", substituted_capture)
    with pytest.raises(EffectRuntimeRejected, match="unsupported complete Turn decision version"):
        capture_before_replan(env, {"turn_id": turn, "mode": "heartbeat", "stage": "execute"}, "Current work.")
    assert not (wake / "checkpoint-start-guard.json").exists()
    assert _heartbeat_receipt_count(runtime, turn) == 1 and _spend_run_count(runtime) == 0


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="Explicit native Codex; loopback inference only")
@pytest.mark.parametrize("host", ["app_server", "benchmark", "benchmark_progressive", "app_machine", "app_code_mode", "app_code_mode_async", "app_progressive", "app_progressive_large", "app_program_route", "app_code_mode_program_route"])
def test_native_saved_preflight_uses_lifecycle_without_guard_replay(tmp_path, monkeypatch, host):
    from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision, restore_context_delivery
    from loopx.control_plane.turn_driver.decision_capture import read_context_capture_file
    from tests.control_plane.codex_checkpoint_fixture import (
        delivered_context, last_context_page, local_inference_provider, native_process_continuation,
        selected_context_values,
    )
    from tests.control_plane.test_codex_context_boundary import _app_server, _configure_provider, _native_fixture, _input_text
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TODO_ID, _configure_selectable_alternative,
        _write_fixture, _spend_run_count, _heartbeat_receipt_count,
    )

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    benchmark = host in ("benchmark", "benchmark_progressive")
    progressive = host in ("app_progressive", "benchmark_progressive", "app_progressive_large")
    large_capture = host == "app_progressive_large"
    routed_program = host.endswith("_program_route")
    code_mode = host.startswith("app_code_mode")
    force_async = host == "app_code_mode_async"
    if routed_program and os.name == "nt":
        pytest.skip("POSIX console alias and controlled PATH")
    unicode_requirement = "保留原始目标、范围与停止条件🙂\n" * 120
    supporting_material = "资料🙂" * 390_000 if large_capture else ""
    binary, env, workspace, home = _native_fixture(tmp_path)
    env.update(NODE_OPTIONS="--experimental-sqlite", LOOPX_USAGE_PING="0")
    env.pop("CODEX_ROLLOUT_TRACE_ROOT", None)
    project, runtime, registry = _write_fixture(workspace)
    if benchmark:
        _configure_selectable_alternative(project)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    todos = read_fixture_todos(state)
    todos[0]["text"] = todos[0]["title"] = "Exact requirement with retained provenance.\n" * 240
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos, handoff_mode="soft_claim"),
        state_path=state, provider="sqlite")
    sources = []
    guard = ("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", TODO_ID, "--turn-instance-id", "saved-native-turn", "--codex-app")
    for name in (() if benchmark else ("cold", "warm")):
        if large_capture:
            from contextlib import redirect_stdout
            from io import StringIO

            from loopx.cli import main
            from loopx.cli_commands import quota

            prepare = quota._prepare_quota_result_context

            def prepare_large_source(payload, *args, **kwargs):
                # The actual producer must bind the optional material into
                # its decision before the test saves the original capture.
                return prepare({**payload, "retained_material": {
                    "requirement": unicode_requirement, "supporting": supporting_material}},
                    *args, **kwargs)

            output = StringIO()
            with monkeypatch.context() as fixture_patch, redirect_stdout(output):
                fixture_patch.chdir(project)
                fixture_patch.setattr(quota, "_prepare_quota_result_context", prepare_large_source)
                code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
                    *guard, "--context-delivery", "off"])
            decision = json.loads(output.getvalue())
        else:
            code, decision = _run_cli(registry, runtime, *guard, cwd=project)
        assert code == 0, decision
        source = workspace / (name + "-source.json")
        source.write_text(json.dumps(decision, ensure_ascii=False))
        if large_capture:
            assert 2 * 1024 * 1024 < source.stat().st_size < 8 * 1024 * 1024
        sources.append((source, source.read_bytes()))
    receipts = _heartbeat_receipt_count(runtime, "saved-native-turn")
    context, cache = workspace / "context", workspace / "cache"
    context.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    program = os.environ.get("LOOPX_NATIVE_CONTEXT_CLI") or str(Path(sys.executable).with_name("loopx"))
    cli = [program, "--cli-bin", program]
    wrong_runtime = workspace / "wrong-runtime"
    if routed_program:
        bin_dir = workspace / "candidate bin"
        bin_dir.mkdir()
        poison = bin_dir / "loopx"
        poison.write_text(f"#!{sys.executable}\nfrom pathlib import Path\n"
                          f"Path({str(wrong_runtime)!r}).touch()\nraise SystemExit(97)\n")
        poison.chmod(0o755)
        candidate = bin_dir / "candidate's cli"
        candidate.symlink_to(Path(program))
        cli = ([sys.executable, "-m", "loopx.entrypoint"] if code_mode
               else [str(candidate), "--cli-bin", str(candidate)])
        cli += ["--registry", str(registry), "--runtime-root", str(runtime)]
        env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    from loopx.control_plane.turn_driver.codex_context import codex_context_configuration
    configuration = codex_context_configuration(str(context), goal_id=GOAL_ID, agent_id=AGENT_ID, cli_argv=cli, scenario="codex_app_heartbeat")
    if host == "app_machine":
        from tests.control_plane.test_context_machine_configuration import configure
        assert configure(registry, runtime, workspace, "on")[0] == 0
        code, setup = _run_cli(registry, runtime, "quota", "context-config", "--codex-app",
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, cwd=project)
        assert code == 0, setup
        configuration = setup["native_config"]
        cli += ["--runtime-root", str(runtime)]
    if not benchmark:
        (home / "hooks.json").write_text(json.dumps({"hooks": configuration["hooks"]}))
    phase, queue, observed, errors = "cold", [], {}, []
    read_offsets = {}
    visible_choices = {}
    actual_turn = None
    rejected_read = False
    reading_initial = False
    initial_content = {}
    read_gate = workspace / "reader-may-finish"
    read_gate_armed = False
    async_observed = False

    def response(request, number, message):
        nonlocal phase, queue, actual_turn, receipts, rejected_read, reading_initial, read_gate_armed, async_observed
        try:
            if continuation := native_process_continuation(request, f"projection_{number}",
                    code_mode=code_mode):
                if read_gate_armed:
                    # The child cannot finish before its handle reaches this
                    # next real native request. No timing luck decides success.
                    async_observed = True
                    read_gate.touch()
                    read_gate_armed = False
                return continuation
            if reading_initial:
                name = phase.removesuffix("_read")
                page = last_context_page(request, allow_selection=True)
                assert page and page["ok"] and page["offset"] == len(initial_content[name])
                initial_content[name] += page["content"].encode()
                assert len(initial_content[name]) == (page["total_bytes"] if page["eof"] else page["next_offset"])
                if page["eof"]:
                    assert selected_context_values(initial_content[name].decode())["/goal_id"] == GOAL_ID
                    reading_initial = False
                else:
                    queue.insert(0, page["next_command"])
            if rejected_read:
                output = [item["output"] for item in request["input"]
                    if item.get("type") in ("function_call_output", "custom_tool_call_output")][-1]
                rejected = json.loads(output.split("\nOutput:\n", 1)[-1])
                assert rejected["ok"] is False and "content" not in rejected
                assert "digest" in rejected["reason"]
                rejected_read = False
            if queue:
                command = queue.pop(0)
            elif phase in ("cold", "warm"):
                if benchmark:
                    from loopx.control_plane.turn_driver.decision_capture import CONTEXT_CAPTURE_NAVIGATION_GUIDANCE
                    guide = CONTEXT_CAPTURE_NAVIGATION_GUIDANCE.strip()
                    developer_text = [request.get("instructions") or ""]
                    developer_text.extend(part.get("text", "") for item in request["input"]
                        if item.get("role") == "developer" for part in item.get("content", []))
                    assert any(guide in text for text in developer_text)
                    wake_prompt = next(part["text"] for item in reversed(request["input"])
                        if item.get("role") == "user" for part in item.get("content", [])
                        if " quota context-project " in part.get("text", ""))
                    assert guide not in wake_prompt
                    command = [line for line in _input_text(request).splitlines() if " quota context-project " in line][-1]
                    argv = shlex.split(command)
                    actual_turn = argv[argv.index("--turn-instance-id") + 1]
                    source = Path(argv[argv.index("--capture-file") + 1])
                    sources.append((source, source.read_bytes()))
                    receipts = _heartbeat_receipt_count(runtime, actual_turn)
                    assert hashlib.sha256(source.read_bytes()).hexdigest() == argv[argv.index("--capture-sha256") + 1]
                    assert "--initial-read" in argv
                    if progressive:
                        command = shlex.join([word for word in argv if word != "--initial-read"] + [
                            "--select", "/goal_id", "--select", "/heartbeat_receipt",
                            "--select", "/interaction_contract/cli_channel/selection_command",
                            "--select", "/action_portfolio/suggested_actions/0/todo_id",
                            "--select", "/action_portfolio/suggested_actions/1/todo_id"])
                    command += " --max-page-output-bytes 32000"
                else:
                    source, raw = sources[0 if phase == "cold" else 1]
                    command = shlex.join([*cli, "--format", "json", "quota", "context-project",
                        "--capture-file", str(source), "--capture-sha256", hashlib.sha256(raw).hexdigest(),
                        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", "saved-native-turn",
                        "--codex-app", *([] if host == "app_machine" else [
                            "--context-delivery", "on", "--codex-context-dir", str(context), "--decision-output-root", str(cache)])])
                phase += "_ready"
            elif phase.endswith("_ready"):
                name = phase.removesuffix("_ready")
                output = [item["output"] for item in request["input"]
                    if item.get("type") in ("function_call_output", "custom_tool_call_output")][-1]
                if isinstance(output, list):
                    output = [part["text"] for part in output if part.get("type") == "input_text"][-1]
                navigation = json.loads(output.split("\nOutput:\n", 1)[-1])
                if benchmark and name in ("cold", "warm"):
                    page = navigation
                    assert page["ok"] and len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 32000
                    assert page["offset"] == 0 and page["content"]
                    navigation = page["navigation"]
                    if progressive:
                        assert page["eof"]
                        values = selected_context_values(page["content"])
                        assert values["/goal_id"] == GOAL_ID and values["/heartbeat_receipt"]["turn_instance_id"] == actual_turn
                descriptor = machine_reference_oracle(navigation)
                # A selectively read large capture cannot become a retained
                # full baseline; its next projection must remain full.
                assert descriptor["ok"] and descriptor["mode"] == (
                    "full" if name == "cold" or large_capture else "delta")
                if benchmark:
                    assert len(json.dumps(navigation, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 32000
                data = Path(descriptor["path"]).read_bytes()
                assert hashlib.sha256(data).hexdigest() == descriptor["sha256"]
                published = json.loads(data)
                if descriptor["mode"] == "delta":
                    assert descriptor["target"] == published["target"]
                observed[name] = (published, descriptor)
                read_offsets[name] = 0
                # Follow the public reader's actual page cursor rather than
                # guessing offsets from the capture size. This keeps each
                # request's real page budget and avoids overlapping reads.
                queue.append(navigation["command"])
                if routed_program and descriptor["mode"] == "full":
                    # Execute the returned initial navigation unchanged inside
                    # real native tools, then retain the existing full-source
                    # oracle and lifecycle proof. PATH's ambient CLI must fail.
                    initial_content[name] = b""
                    reading_initial = True
                    queue.insert(0, navigation["first_read_command"])
                if progressive:
                    # Exercise the actual small reference and selective public
                    # reader in native model input; never consume either file in full.
                    assert "content" not in descriptor
                    reader = navigation["recovery"]["command"]
                    select = " --select /goal_id --select /heartbeat_receipt"
                    if benchmark and name in ("cold", "warm"):
                        select += (" --select /interaction_contract/cli_channel/selection_command"
                            " --select /action_portfolio/suggested_actions/0/todo_id"
                            " --select /action_portfolio/suggested_actions/1/todo_id")
                    if large_capture:
                        select += " --select /retained_material/requirement"
                    # The benchmark receiver knows these paths. Other native
                    # scenarios exercise index discovery and digest recovery.
                    queue = ([] if benchmark else [reader + " --list"]) + [reader + select]
                    if large_capture:
                        # Reject a different digest through the real native CLI,
                        # then continue with the unchanged original reference.
                        argv = shlex.split(navigation["recovery"]["command"])
                        index = argv.index("--reference-file") + 1
                        reference = json.loads(Path(argv[index]).read_text())
                        reference["sha256"] = "0" * 64
                        damaged = workspace / (name + "-damaged.reader.json")
                        damaged.write_text(json.dumps(reference))
                        damaged.chmod(0o600)
                        argv[index] = str(damaged)
                        queue.insert(0, shlex.join(argv))
                        rejected_read = True
                # The initial explicit selection contains only Goal and receipt.
                # Read the offered choices and selection command before choosing.
                phase = name + "_read"
                if not queue:
                    return response(request, number, message)
                command = queue.pop(0)
            else:
                name = phase.removesuffix("_read")
                descriptor = observed[name][1]
                page = last_context_page(request, allow_selection=progressive)
                assert page and page["ok"]
                if progressive:
                    values = selected_context_values(page["content"])
                    assert values["/goal_id"] == GOAL_ID
                    assert values["/heartbeat_receipt"]["turn_instance_id"] == (
                        actual_turn if benchmark else "saved-native-turn")
                    if large_capture:
                        assert page["offset"] == 0 and page["eof"]
                        assert len(page["content"].encode()) == page["total_bytes"]
                        assert values["/retained_material/requirement"] == unicode_requirement
                        assert "资料🙂" not in _input_text(request)
                    if benchmark:
                        if name.endswith("_bound"):
                            bound_id = values["/heartbeat_receipt"]["settlement_identity"]["todo_id"]
                            assert bound_id == visible_choices[name.removesuffix("_bound")]
                        else:
                            slot = 0 if name == "cold" else 1
                            visible_choices[name] = values[f"/action_portfolio/suggested_actions/{slot}/todo_id"]
                    native_context = home / "loopx-context" if benchmark else context
                    assert "candidate" not in json.loads((native_context / "session.json").read_bytes())
                else:
                    assert page["offset"] == read_offsets[name]
                    read_offsets[name] += len(page["content"].encode())
                    assert read_offsets[name] == (page["total_bytes"] if page["eof"] else page["next_offset"])
                    assert page is not None and page["sha256"] == descriptor["sha256"]
                    if not page["eof"]:
                        assert page["next_offset"] > page["offset"] and page["next_command"]
                        queue.append(page["next_command"])
                        return response(request, number, message)
                    assert delivered_context(request, descriptor["sha256"]) == Path(descriptor["path"]).read_text().rstrip("\n")
                if benchmark and name in ("cold", "warm"):
                    # Consume the command actually delivered to the native
                    # worker, rather than manually supplying missing flags.
                    if progressive:
                        assert page["offset"] == 0 and page["eof"]
                        selection = values["/interaction_contract/cli_channel/selection_command"]
                        todo_id = visible_choices[name]
                    else:
                        presented = json.loads(Path(descriptor["recovery"]["path"]).read_text())
                        selection = presented["interaction_contract"]["cli_channel"]["selection_command"]
                        todo_id = TODO_ID
                    command = selection["route_prefix"] + " " + selection["command_args_template"].format(todo_id=todo_id)
                    argv = shlex.split(command)
                    assert argv[argv.index("--context-delivery") + 1] == "on"
                    assert argv[argv.index("--codex-context-dir") + 1] == str(home / "loopx-context")
                    assert argv.count("--decision-output-root") == 1
                    assert argv[argv.index("--max-page-output-bytes") + 1] == "32000"
                    phase = name + "_bound_ready"
                elif name in ("warm", "warm_bound"):
                    phase = "done"
                    return message
                else:
                    # A new worker wake owns its own original guard.
                    phase = "warm"
                    if benchmark:
                        return message
                    return response(request, number, message)
            arguments = {"cmd": command, "max_output_tokens": 22000}
            if force_async and not async_observed and " quota context-read " in command:
                wait_code = ("import pathlib,time; p=pathlib.Path(" + repr(str(read_gate)) + "); "
                    "deadline=time.monotonic()+20\n"
                    "while not p.exists() and time.monotonic()<deadline: time.sleep(.05)\n"
                    "assert p.exists(), 'native output handle was not delivered'")
                arguments["cmd"] = shlex.join([sys.executable, "-c", wait_code]) + " && " + command
                arguments["yield_time_ms"] = 1000
                read_gate_armed = True
            if routed_program:
                # A login shell may replace the controlled PATH before it
                # executes the otherwise unchanged generated reader command.
                arguments["login"] = False
            if code_mode:
                from loopx.control_plane.turn_driver.decision_capture import CODE_MODE_EXEC_RESULT_FORWARDER
                return {"type": "custom_tool_call", "id": f"fc_{number}", "call_id": f"call_{number}",
                    "name": "exec", "input": '// @exec: {"max_output_tokens":24000}\nconst result=await tools.exec_command('
                        + json.dumps(arguments) + ');' + CODE_MODE_EXEC_RESULT_FORWARDER + ';'}
            # Match the generated native profile's 64000-byte page transport;
            # the old 3000-token tool cap truncated its valid JSON first page.
            return {"type": "function_call", "id": f"fc_{number}", "call_id": f"call_{number}",
                "name": "exec_command", "arguments": json.dumps(arguments)}
        except Exception as error:
            errors.append(error)
            return message

    with local_inference_provider(response) as (url, _, provider_requests):
        if benchmark:
            from benchmark.runtime.worker import run_once
            skills = workspace / "skills"
            skills.mkdir()
            declaration = workspace / "checkpoint-request.json"
            declaration.write_text(json.dumps({"trigger": "fresh_replan", "destination": str(workspace / "checkpoints"),
                "components": [{"id": "task", "source_path": str(state)}], "point_metadata": {}}))
            env.update(LOOPX_CONTEXT_DELIVERY="on", LOOPX_EXECUTION_MODE="heartbeat", LOOPX_ITERATION_CONTEXT="resume",
                LOOPX_TASK_ENTRY="seeded-todo", LOOPX_TASK_STAGE="execute", LOOPX_CHECKPOINT_REQUEST=str(declaration),
                LOOPX_CODEX_HOME=str(home), LOOPX_PROJECT=str(project), LOOPX_SHARED_SKILLS=str(skills),
                LOOPX_REGISTRY=str(registry), LOOPX_RUNTIME_ROOT=str(runtime), LOOPX_GOAL_ID=GOAL_ID, LOOPX_AGENT_ID=AGENT_ID,
                LOOPX_WAKE_LOG_DIR=str(workspace / "wakes"), LOOPX_CLI=program, CODEX_BIN=binary,
                MODEL_NAME="fixture", REASONING_EFFORT="high", OPENAI_BASE_URL=url, OPENAI_API_KEY="synthetic-loopback-only",
                LOOPX_CODEX_TURN_TIMEOUT_SEC="60")
            result = run_once(env)
            assert result["ok"] and result["context_transport"] == {"available": True}, result
            session_id = result["session"]["session_id"]
            assert phase == "warm" and sources[0][0].read_bytes() == sources[0][1], errors
            # Settle this isolated, independently verified readback once before
            # a new wake. Presentation itself still performs no debit or guard.
            cold_turn = json.loads(sources[0][1])["heartbeat_receipt"]["turn_instance_id"]
            binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                "--todo-id", TODO_ID, "--turn-instance-id", cold_turn)
            code, written = _run_cli(registry, runtime, "refresh-state", *binding,
                "--classification", "saved_context_readback_verified", "--delivery-batch-scale", "single_surface",
                "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
                "--no-global-sync", "--suppress-external-sinks", cwd=project)
            assert code == 0, written
            code, spent = _run_cli(registry, runtime, "quota", "spend-slot", *binding,
                "--slots", "1", "--source", "heartbeat", "--execute", cwd=project)
            assert code == 0 and spent["appended"], spent
            result = run_once(env)
            assert result["ok"] and result["context_transport"] == {"available": True}, result
            assert result["session"]["session_id"] == session_id
        else:
            _configure_provider(home, url)
            config = home / "config.toml"
            overrides = "".join(f"{key}={json.dumps(value)}\n"
                for key, value in configuration["config_overrides"].items())
            config.write_text(overrides + config.read_text())
            with (home / "config.toml").open("a") as config:
                config.write("[sandbox_workspace_write]\nnetwork_access=true\n")
            if code_mode:
                config = home / "config.toml"
                config.write_text(config.read_text().replace("[features]\n", "[features]\ncode_mode=true\n"))
            _app_server(binary, env, workspace, home, True, sandbox="workspace-write")
    assert not errors and phase == "done", (errors, phase, wrong_runtime.exists())
    if large_capture:
        assert all("资料🙂" not in json.dumps(request["input"], ensure_ascii=False)
            for request in provider_requests)
    if force_async:
        assert async_observed and read_gate.is_file()
    if routed_program:
        assert set(initial_content) == {"cold"} and not wrong_runtime.exists()
    if benchmark:
        turns = [json.loads(raw)["heartbeat_receipt"]["turn_instance_id"] for _, raw in sources]
        if progressive:
            assert visible_choices["cold"] == TODO_ID and visible_choices["warm"] != TODO_ID
        assert len(set(turns)) == 2
        assert all(_heartbeat_receipt_count(runtime, turn) == 2 for turn in turns)  # original guard + selection
        assert _spend_run_count(runtime) == 1  # only the explicit inter-wake settlement above
    else:
        assert _heartbeat_receipt_count(runtime, "saved-native-turn") == receipts and _spend_run_count(runtime) == 0
    def checked_body(reference):
        return read_context_capture_file(reference["path"], reference["sha256"], private=True)[2]

    cold = observed["cold"][0]
    previous = None
    signed_sources = {}
    for name, (published, descriptor) in observed.items():
        projected = checked_body(descriptor["recovery"])
        if projected.get("schema_version") == "loopx_turn_envelope_v1":
            details = projected.get("context_details", {})
            reference = details.get("unread_context_index", {}).get("source")
            if reference is None and (reader := details.get("command_template")):
                argv = shlex.split(reader)
                reference = {"path": argv[argv.index("--capture-file") + 1],
                             "sha256": argv[argv.index("--capture-sha256") + 1]}
            signed = checked_body(reference) if reference else projected
            assert_complete_turn_decision(signed)
            assert signed["contract_binding"] == projected["contract_binding"]
            assert signed["heartbeat_receipt"] == projected["heartbeat_receipt"]
            signed_sources[name] = signed
        if large_capture:
            assert descriptor["mode"] == "full"
            if "target" in descriptor:
                assert descriptor["target"]["goal_id"] == GOAL_ID
                assert descriptor["target"]["agent_id"] == AGENT_ID
            assert published == projected
            assert projected["retained_material"] == {
                "requirement": unicode_requirement, "supporting": supporting_material}
            continue
        # The pinned full presentation has its own digest and target; a signed
        # decision authenticates the source, not the transported presentation.
        current = {"schema_version": "context_delivery_v1", "mode": "full", "target": descriptor["target"],
                   "ancestry": 0, "context": projected}
        if descriptor["mode"] == "full":
            assert published == projected
            wire, base = current, None
        else:
            wire, base = published, previous
            if wire.get("continuity_basis") == "captured_file":
                base = {**current, "target": wire["base"], "context": checked_body(descriptor["baseline_reference"])}
        restored = restore_context_delivery(wire, baseline=base, current=current)
        assert restored["target"] == descriptor["target"] and restored["context"] == projected
        assert restored["mode"] == descriptor["mode"]  # Full fallback would hide a broken delta.
        if wire["mode"] == "delta":
            corrupted = {**wire, "changes": {"value": {}}}
            for rejected, candidate in ((wire, None), (corrupted, base)):
                fallback = restore_context_delivery(rejected, baseline=candidate, current=current)
                assert fallback["mode"] == "full" and fallback["reason"] == "automatic_full_recovery"
                assert fallback["target"] == current["target"] and fallback["context"] == projected
        previous = {**current, "ancestry": 0 if wire.get("continuity_basis") == "captured_file" else wire["ancestry"]}
    if benchmark:
        for name, (source_path, raw) in zip(("cold", "warm"), sources, strict=True):
            presentation = checked_body(observed[name][1]["recovery"])
            original = json.loads(raw)
            if original.get("schema_version") == "loopx_turn_envelope_v1":
                assert signed_sources[name] == original
            selected = presentation["interaction_contract"]["cli_channel"]["selection_command"]
            original_command = original["interaction_contract"]["cli_channel"]["selection_command"]["command_args_template"]
            words = shlex.split(selected["command_args_template"])
            original_words = shlex.split(original_command)
            if original.get("schema_version") == "loopx_turn_envelope_v1":
                assert words.count("--complete-turn") == 1
                if "--complete-turn" not in original_words:
                    # Native selection inherits the signed preflight source even
                    # when the original guard came through a compatibility route.
                    words.remove("--complete-turn")
            output_root = source_path.parents[1] if "--decision-output-root" in original_words else home / "loopx-context"
            transport = {
                "--decision-output-root": str(output_root),
                "--context-delivery": "on",
                "--delta-delivery": "auto",
                "--codex-context-dir": str(home / "loopx-context"),
                "--max-page-output-bytes": "32000",
            }
            for option, value in transport.items():
                assert words.count(option) == 1
                index = words.index(option)
                assert words[index + 1] == value
                if option in original_words:
                    assert original_words.count(option) == 1
                    assert original_words[original_words.index(option) + 1] == value
                else:
                    del words[index:index + 2]
            assert words == original_words
            selected["command_args_template"] = original_command
            if "context_reference_instruction" in presentation and "context_details" not in original:
                details = presentation.pop("context_details")
                assert set(details) == {"command_template"}
                reader = shlex.split(details["command_template"])
                signed_source = Path(reader[reader.index("--capture-file") + 1]).read_bytes()
                assert hashlib.sha256(signed_source).hexdigest() == reader[reader.index("--capture-sha256") + 1]
                assert json.loads(signed_source) == original
            assert resolve_summary_row_references(presentation) == original
    else:
        assert cold == json.loads(sources[0][1]) and projected == json.loads(sources[1][1])
    assert all(path.read_bytes() == raw for path, raw in sources)


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="Explicit native Codex; loopback inference only")
def test_native_discovery_keeps_plugins_out_of_probe_without_changing_solver(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from benchmark.runtime.context_delivery import prepare_context_delivery
    from loopx.control_plane.turn_driver.codex_stdio import StdioNativeGoalTransport
    from tests.control_plane.codex_checkpoint_fixture import local_inference_provider
    from tests.control_plane.test_codex_context_boundary import _configure_provider, _native_fixture

    binary, env, workspace, home = _native_fixture(tmp_path)
    observed = []
    original_spawn = StdioNativeGoalTransport.spawn

    @contextmanager
    def inspect_probe(argv, **kwargs):
        with original_spawn(argv, **kwargs) as native:
            def request(method, params):
                result = native.request(method, params)
                if method == "hooks/list":
                    effective = native.request("config/read", {"cwd": str(workspace), "includeLayers": False})
                    observed.append(effective["config"]["features"])
                return result
            from types import SimpleNamespace
            yield SimpleNamespace(request=request, notify=native.notify)

    monkeypatch.setattr(StdioNativeGoalTransport, "spawn", inspect_probe)
    with local_inference_provider() as (url, calls, _):
        _configure_provider(home, url)
        config = home / "config.toml"
        config.write_text(config.read_text().replace("plugins=false", "plugins=true")
                          .replace("remote_plugin=false", "remote_plugin=true")
                          .replace("plugin_sharing=false", "plugin_sharing=true"))
        original_config = config.read_bytes()
        env.update(CODEX_BIN=binary, LOOPX_PROJECT=str(workspace), LOOPX_GOAL_ID="fixture",
                   LOOPX_AGENT_ID="agent", LOOPX_CLI=str(Path(sys.executable).with_name("loopx")))
        prepare_context_delivery(env, home)
        first_trust = env["LOOPX_CODEX_CONTEXT_TRUST_ARGS"]
        prepare_context_delivery(env, home)
        assert env["LOOPX_CODEX_CONTEXT_TRUST_ARGS"] == first_trust
        assert calls == []  # Hook identity lookup never starts inference.
    assert len(observed) == 2
    for features in observed:
        assert all(features[key] is False for key in ("plugins", "remote_plugin", "plugin_sharing"))
    assert config.read_bytes() == original_config
    assert not list(home.rglob("*.pack")), "discovery must not clone a plugin catalog"


@pytest.mark.parametrize("setting", [None, "off"])
def test_saved_projection_default_delta_off_ignores_available_native_cache(tmp_path, monkeypatch, setting):
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import project_saved_context_capture
    from loopx.control_plane.turn_driver import codex_context

    path, root = tmp_path / "original.json", tmp_path / "uncreated"
    raw = json.dumps({"ok": True, "goal_id": "g", "agent_identity": {"agent_id": "a"},
        "heartbeat_receipt": {"turn_instance_id": "t"}}).encode()
    path.write_bytes(raw)
    def unexpected(*args, **kwargs):
        pytest.fail("stateless/disabled presentation must not inspect/create native state")
    monkeypatch.setattr(codex_context, "codex_context_directory", unexpected)
    argv = ["quota", "context-project", "--capture-file", str(path),
        "--capture-sha256", hashlib.sha256(raw).hexdigest(), "--goal-id", "g", "--agent-id", "a",
        "--turn-instance-id", "t", "--codex-context-dir", str(root), "--decision-output-root", str(root)]
    if setting is not None:
        argv.extend(("--context-delivery", setting))
    args = build_parser().parse_args(argv)
    result = project_saved_context_capture(args)
    assert result["ok"]
    if setting == "off":
        assert result["mode"] == "full" and result["path"] == str(path)
    else:
        assert result == json.loads(raw)
    assert not root.exists() and path.read_bytes() == raw


@pytest.mark.skipif(os.name != "posix", reason="Native session continuity uses a private POSIX journal")
def test_retry_lost_navigation_uses_original_predecessor(tmp_path, monkeypatch):
    from loopx.control_plane.turn_driver.codex_context import codex_context_hook

    journal = tmp_path / "native"
    journal.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "retry-session")
    event = {"hook_event_name": "SessionStart", "source": "startup",
             "session_id": "retry-session", "cwd": str(tmp_path)}
    codex_context_hook(str(journal), event)
    registry, runtime = tmp_path / "absent-registry", tmp_path / "absent-runtime"

    def present(turn):
        body = {"ok": True, "goal_id": "g", "agent_identity": {"agent_id": "a"},
                "heartbeat_receipt": {"turn_instance_id": turn},
                "requirement": "Preserve this independently recoverable requirement. " * 1900}
        path = tmp_path / (turn + ".json")
        if not path.exists():
            path.write_text(json.dumps(body))
        rc, view = _run_cli(registry, runtime, "quota", "context-project", "--capture-file", str(path),
            "--capture-sha256", hashlib.sha256(path.read_bytes()).hexdigest(), "--goal-id", "g",
            "--agent-id", "a", "--turn-instance-id", turn, "--context-delivery", "on",
            "--codex-app", "--codex-context-dir", str(journal), cwd=tmp_path)
        assert rc == 0, view
        return machine_reference_oracle(view)

    cold = present("cold")
    assert cold["mode"] == present("cold")["mode"] == "full"
    # No model read or PostToolUse receipt: the caller may have lost the notification.
    warm = present("warm")
    retry = present("warm")
    for view in (warm, retry):
        assert view["mode"] == "delta"
        wire = json.loads(Path(view["path"]).read_bytes())
        assert wire["base"] == cold["target"] and wire["target"] != wire["base"]
        assert json.loads(Path(view["recovery"]["path"]).read_bytes())["heartbeat_receipt"] == {"turn_instance_id": "warm"}
    # Loss of the actual predecessor or a new epoch must recover this source independently.
    Path(warm["baseline_reference"]["path"]).unlink()
    assert present("warm")["mode"] == "full"
    codex_context_hook(str(journal), {**event, "hook_event_name": "PreCompact"})
    codex_context_hook(str(journal), {**event, "source": "compact"})
    assert present("warm")["mode"] == "full"
    assert not registry.exists() and not runtime.exists()


@pytest.mark.parametrize(("repeat", "limit", "staged", "page_bytes", "paged"), [
    (1, None, False, None, False), (480, None, True, None, False),
    (480, 6400, True, None, False), (480, 64000, False, None, False),
    (1100, 40000, True, 40000, False), (98, 12000, False, 900, True),
], ids=["small", "unknown", "lower", "sufficient", "explicit-reader-window", "tight-reader-window"])
def test_saved_initial_page_is_primary_and_its_recovery_replays_the_same_selection(
        tmp_path, monkeypatch, repeat, limit, staged, page_bytes, paged):
    from loopx.control_plane.turn_driver.codex_context import codex_context_hook

    runtime, registry, context = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "native"
    context.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "session")
    codex_context_hook(str(context), {"hook_event_name": "SessionStart", "source": "startup",
        "session_id": "session", "cwd": str(tmp_path)})
    receipt = {"turn_instance_id": "t"}
    text = "Keep the original requirement. 保留原要求🙂\n" * repeat
    saved_limit = 32000 if repeat > 1 else None
    selection_args = "quota should-run --todo-id {todo_id}"
    if saved_limit is not None:
        selection_args += f" --max-page-output-bytes {saved_limit}"
    # The paged case must exceed the window even before supporting context.
    stop = "Stop before publishing. " * 100 if paged else None
    original = tmp_path / "original.json"
    original.write_text(json.dumps({"ok": True, "goal_id": "g", "agent_identity": {"agent_id": "a"},
        "heartbeat_receipt": receipt, "effective_action": "normal_run",
        **({"goal_boundary": {"stop": stop}} if stop is not None else {}),
        "interaction_contract": {"schema_version": "loopx_interaction_contract_v0",
            "cli_channel": {"selection_command": {"command_args_template": selection_args}}, "agent_channel": {
            "work_context": {"sources": [{"source": "goal_state", "ordering": "before_work",
                "content": {"text": text, "state_file": "goal.md"}}]}}}}, ensure_ascii=False))
    raw = original.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    project = ["quota", "context-project", "--capture-file", str(original), "--capture-sha256", digest,
        "--goal-id", "g", "--agent-id", "a", "--turn-instance-id", "t", "--codex-app",
        "--context-delivery", "on", "--codex-context-dir", str(context)]
    if limit is not None:
        project += ["--max-page-output-bytes", str(limit)]

    def cli(*args):
        return _run_cli(registry, runtime, *args, cwd=tmp_path)

    def follow(command):
        words = shlex.split(command)
        code, page = cli(*words[words.index("quota"):])
        assert code == 0 and page["ok"], page
        return page

    code, reference = cli(*project)
    assert code == 0 and "content" not in reference
    assert reference["instruction"].startswith("Start with first_read_command")
    window = ["--page-bytes", str(page_bytes)] if page_bytes is not None else []
    code, first = cli(*project, "--initial-read", *window)
    assert code == 0 and first["offset"] == 0 and first["eof"] is not paged
    assert list(first).index("content") < list(first).index("navigation")
    navigation = first["navigation"]
    assert "top-level content" in navigation["instruction"]
    assert "first_read_command only to recover" in navigation["instruction"]
    assert "without reading an older baseline" in navigation["instruction"]
    assert "Start with first_read_command" not in navigation["instruction"]
    assert ("remaining_read_command" in navigation) == staged
    assert ("navigation.remaining_read_command" in first["read_instruction"]) == staged
    if staged:
        assert "Before work" in first["read_instruction"]
    assert ("next_command" in first) is paged
    if page_bytes is not None:
        for command in (navigation["first_read_command"], navigation.get("remaining_read_command"),
                        first.get("next_command")):
            if command is not None:
                words = shlex.split(command)
                assert int(words[words.index("--page-bytes") + 1]) == page_bytes
    # Read the saved same-Turn selection recipe from the committed projection.
    # An implicit cap for this page does not become an explicit guard option.
    operation = follow(navigation["recovery"]["command"] + " --select /interaction_contract/cli_channel/selection_command")
    assert operation["eof"]
    template = json.loads(operation["content"])["selections"][0]["value"]["command_args_template"]
    words = shlex.split(template)
    budget = int(words[words.index("--max-page-output-bytes") + 1]) if "--max-page-output-bytes" in words else None
    assert budget == (limit if limit is not None else saved_limit)
    recovery_limit = limit if limit is not None else 64000
    limit = limit if limit is not None else 12000
    assert len(json.dumps(first, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= limit

    def finish(page, *, limit=None, content_limit=None):
        body = bytearray()
        while True:
            assert page["offset"] == len(body)
            if limit is not None:
                assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= limit
            content = page["content"].encode()
            if content_limit is not None:
                assert len(content) <= content_limit
            body.extend(content)
            if page["eof"]:
                assert len(body) == page["total_bytes"]
                return bytes(body)
            assert page["next_offset"] == len(body)
            page = follow(page["next_command"])

    # A client that forwards only navigation discards the bounded first page.
    # It can recover this same selection using the separate offset-zero recipe.
    assert "content" not in navigation and "next_command" not in navigation
    if staged:
        codex_context_hook(str(context), {"hook_event_name": "PreCompact", "source": "startup",
            "session_id": "session", "cwd": str(tmp_path)})
    files = set(context.rglob("*"))
    journal = (context / "session.json").read_bytes()
    body = finish(first, content_limit=page_bytes)
    assert finish(follow(navigation["first_read_command"]), content_limit=page_bytes) == body
    selected = {item["pointer"]: item["value"] for item in json.loads(body)["selections"]}
    assert selected["/heartbeat_receipt"] == receipt
    assert selected["/effective_action"] == "normal_run"
    if stop is not None:
        assert selected["/goal_boundary"] == {"stop": stop}
    requirement = "/interaction_contract/agent_channel/work_context/sources/0/content/text"
    if staged:
        assert requirement not in selected and "Before work" in navigation["instruction"]
        remaining = finish(follow(navigation["remaining_read_command"]), content_limit=page_bytes)
        detail = {item["pointer"]: item["value"] for item in json.loads(remaining)["selections"]}
        assert not selected.keys() & detail.keys()
        assert detail[requirement] == text
    else:
        assert selected[requirement] == text
    # Clients that receive navigation before the initial read use the same
    # transport budget even when the capture has much more required content.
    recipe = shlex.split(reference["first_read_command"])
    assert int(recipe[recipe.index("--max-page-output-bytes") + 1]) == limit
    for view in (reference, navigation):
        recovery = shlex.split(view["recovery"]["command"])
        assert int(recovery[recovery.index("--max-page-output-bytes") + 1]) == recovery_limit
    linked = json.loads(finish(follow(reference["first_read_command"]), limit=limit))["selections"]
    fields = {item["pointer"]: item["value"] for item in linked}
    assert fields[requirement] == text and fields["/heartbeat_receipt"] == receipt
    assert original.read_bytes() == raw and (context / "session.json").read_bytes() == journal
    assert set(context.rglob("*")) == files
    if repeat == 1:
        code, detail = cli(*project, "--select", "/heartbeat_receipt")
        assert code == 0 and detail["eof"] and "next_command" not in detail
        assert "first_read_command only to recover" in detail["navigation"]["instruction"]
        assert json.loads(detail["content"])["selections"] == [{"pointer": "/heartbeat_receipt", "value": receipt}]
        code, rejected = cli(*project, "--select", "/missing")
        assert code and not rejected["ok"] and "recovery_command" in rejected
        assert not ({"content", "next_command", "navigation"} & rejected.keys())
        code, too_small = cli(*project, "--initial-read", "--max-page-output-bytes", "256")
        assert code and not too_small["ok"] and not ({"content", "next_command", "navigation"} & too_small.keys())
        assert too_small["error_code"] == "quota_decision_transport_failed"
        assert len(json.dumps(too_small, ensure_ascii=False, separators=(",", ":")).encode()) + len(os.linesep) <= 256
        assert "without rerunning admission" in too_small["reason"]
        assert original.read_bytes() == raw
        assert finish(follow(navigation["first_read_command"]), limit=12000) == body

        # Retry the original read with its normal budget and the same digest.
        code, retry = cli(*project, "--initial-read")
        assert code == 0 and finish(retry, limit=12000) == body
        recovery = follow(retry["navigation"]["recovery"]["command"])
        recovered = finish(recovery, limit=64000)
        assert hashlib.sha256(recovered).hexdigest() == recovery["sha256"]
        view = json.loads(recovered)
        recipe = view["interaction_contract"]["cli_channel"]["selection_command"]
        # The recovery view may bind the same-Turn command's transport. All
        # other decided values, including the original receipt, are exact.
        assert shlex.split(recipe["command_args_template"])[:4] == shlex.split(selection_args)
        recipe["command_args_template"] = selection_args
        assert view == json.loads(raw)
    assert not registry.exists() and not runtime.exists()


def test_stateless_saved_initial_read_bounds_the_default_cli_output(tmp_path):
    import subprocess

    requirement = "Preserve the original acceptance and stop. 保留原要求。\n" * 850
    receipt = {"event_id": "original-receipt", "turn_instance_id": "original-turn"}
    source = (tmp_path / "original.json").resolve()
    raw = json.dumps({"ok": True, "goal_id": "g", "agent_identity": {"agent_id": "a"},
        "heartbeat_receipt": receipt, "effective_action": "normal_run",
        "interaction_contract": {"schema_version": "loopx_interaction_contract_v0", "agent_channel": {
            "work_context": {"sources": [{"source": "goal_state", "ordering": "before_work",
                "content": {"text": requirement, "state_file": "goal.md"}}]}}}}, ensure_ascii=False).encode()
    source.write_bytes(raw)
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "runtime"
    base = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json"]
    argv = ["quota", "context-project", "--context-delivery", "auto", "--delta-delivery", "off",
        "--initial-read", "--capture-file", str(source), "--capture-sha256", hashlib.sha256(raw).hexdigest(),
        "--goal-id", "g", "--agent-id", "a", "--turn-instance-id", receipt["turn_instance_id"]]
    body = bytearray()
    for _ in range(16):
        result = subprocess.run([*base, *argv], cwd=tmp_path, capture_output=True, check=True)
        assert len(result.stdout) <= 12000
        page = json.loads(result.stdout)
        assert page["ok"] and page["offset"] == len(body)
        body.extend(page["content"].encode())
        if page["eof"]:
            assert page["total_bytes"] == len(body)
            break
        reader = shlex.split(page["next_command"])
        argv = reader[reader.index("quota"):]
    else:
        pytest.fail("stateless first read did not complete within its bounded fixture")
    chosen = {row["pointer"]: row["value"] for row in json.loads(body)["selections"]}
    assert chosen["/heartbeat_receipt"] == receipt
    assert chosen["/interaction_contract/agent_channel/work_context/sources/0/content/text"] == requirement
    assert source.read_bytes() == raw and not registry.exists() and not (runtime / "goals").exists()


def test_attached_selected_page_uses_the_same_window_as_its_reader(tmp_path, monkeypatch):
    from loopx.control_plane.turn_driver.codex_context import codex_context_hook

    context = tmp_path / "native"
    context.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "session")
    codex_context_hook(str(context), {"hook_event_name": "SessionStart", "source": "startup",
        "session_id": "session", "cwd": str(tmp_path)})
    registry, runtime = tmp_path / "registry.json", tmp_path / "runtime"
    source = tmp_path / "decision.json"
    work = {"constraint": "Keep this source verbatim. 保留原文🙂" * 75}
    raw = json.dumps({"ok": True, "goal_id": "g", "agent_identity": {"agent_id": "a"},
        "heartbeat_receipt": {"turn_instance_id": "t"}, "work": work}, ensure_ascii=False).encode()
    source.write_bytes(raw)
    project = ["quota", "context-project", "--capture-file", str(source),
        "--capture-sha256", hashlib.sha256(raw).hexdigest(), "--goal-id", "g", "--agent-id", "a",
        "--turn-instance-id", "t", "--codex-app", "--context-delivery", "on",
        "--codex-context-dir", str(context), "--select", "/work", "--max-page-output-bytes", "12000"]
    before = {path: path.read_bytes() for path in context.rglob("*") if path.is_file()}
    for invalid in ("255", "12001"):
        code, rejected = _run_cli(registry, runtime, *project, "--page-bytes", invalid, cwd=tmp_path)
        assert code and not rejected["ok"] and "--page-bytes" in rejected["reason"]
    assert {path: path.read_bytes() for path in context.rglob("*") if path.is_file()} == before

    code, page = _run_cli(registry, runtime, *project, "--page-bytes", "800", cwd=tmp_path)
    assert code == 0 and not page["eof"]
    content = bytearray()
    while True:
        assert page["ok"] and page["offset"] == len(content)
        data = page["content"].encode()
        assert 0 < len(data) <= 800
        content.extend(data)
        if page["eof"]:
            assert len(content) == page["total_bytes"]
            break
        args = shlex.split(page["next_command"])
        assert args[args.index("--page-bytes") + 1] == "800"
        code, page = _run_cli(registry, runtime, *args[args.index("quota"):], cwd=tmp_path)
        assert code == 0
    assert json.loads(content)["selections"] == [{"pointer": "/work", "value": work}]
    assert source.read_bytes() == raw and not registry.exists() and not runtime.exists()


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="Explicit native Codex; loopback inference only")
@pytest.mark.parametrize(("tool_tokens", "history_tokens", "byte_limit", "code_mode"), [
    (6000, 20000, None, False), (22000, 3000, 6400, False), (6000, 20000, None, True),
], ids=["tool-limited", "history-limited", "code-mode"])
def test_bounded_initial_read_survives_the_actual_native_tool_limit(
        tmp_path, tool_tokens, history_tokens, byte_limit, code_mode):
    from loopx.control_plane.turn_driver.codex_context import codex_context_configuration
    from loopx.control_plane.turn_driver.decision_capture import CODE_MODE_EXEC_RESULT_FORWARDER
    from tests.control_plane.codex_checkpoint_fixture import (
        last_context_page, local_inference_provider, native_process_continuation, selected_context_values,
    )
    from tests.control_plane.test_codex_context_boundary import _app_server, _configure_provider, _native_fixture

    binary, env, workspace, home = _native_fixture(tmp_path)
    env.update(NODE_OPTIONS="--experimental-sqlite", LOOPX_USAGE_PING="0")
    context = workspace / "context"
    context.mkdir(mode=0o700)
    cli = [os.environ.get("LOOPX_NATIVE_CONTEXT_CLI") or str(Path(sys.executable).with_name("loopx"))]
    cli += ["--registry", str(workspace / "absent-registry.json"), "--runtime-root", str(workspace / "absent-runtime")]
    configuration = codex_context_configuration(str(context), goal_id="g", agent_id="a", cli_argv=cli,
        scenario="codex_app_heartbeat")
    (home / "hooks.json").write_text(json.dumps({"hooks": configuration["hooks"]}))
    source = workspace / "original.json"
    requirement = "".join(f"Criterion {i:04}: preserve exact scope before touching outside paths.\n" for i in range(650))
    raw = json.dumps({"ok": True, "goal_id": "g", "agent_identity": {"agent_id": "a"},
        "heartbeat_receipt": {"turn_instance_id": "t"}, "effective_action": "normal_run",
        "goal_boundary": {"stop": "Stop before publishing."}, "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0", "agent_channel": {"work_context": {
                "instruction": "Read the exact requirement before acting.",
                "sources": [{"source": "goal_state", "ordering": "before_work",
                    "content": {"text": requirement, "state_file": "goal.md"}}]}}}}).encode()
    source.write_bytes(raw)
    project = shlex.join([*cli, "--format", "json", "quota", "context-project", "--capture-file", str(source),
        "--capture-sha256", hashlib.sha256(raw).hexdigest(), "--goal-id", "g", "--agent-id", "a",
        "--turn-instance-id", "t", "--codex-app", "--context-delivery", "on",
        "--codex-context-dir", str(context), "--initial-read"])
    phase, errors, detail = "start", [], bytearray()

    def latest_output(request):
        output = next(item["output"] for item in reversed(request["input"])
            if item.get("type") in ("function_call_output", "custom_tool_call_output"))
        return output if isinstance(output, str) else next(part["text"] for part in reversed(output)
            if part.get("type") == "input_text")

    def response(request, number, message):
        nonlocal phase
        try:
            if wait := native_process_continuation(request, str(number), code_mode=code_mode):
                if code_mode:
                    wait["input"] = wait["input"].replace('"max_output_tokens": 22000',
                        f'"max_output_tokens": {tool_tokens}')
                else:
                    wait["arguments"] = json.dumps({**json.loads(wait["arguments"]), "max_output_tokens": tool_tokens})
                return wait
            page = last_context_page(request, allow_selection=True)
            if phase == "start":
                command, phase = project + " --max-page-output-bytes 64000", "truncated"
            elif phase == "truncated":
                assert page is None and "context_capture_selection_page_v1" in latest_output(request)
                command = project + (f" --max-page-output-bytes {byte_limit}" if byte_limit else "")
                phase = "opening"  # The same original receipt and source.
            elif phase == "opening":
                assert page and page["ok"] and page["offset"] == 0 and page["eof"]
                assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= (byte_limit or 12000)
                values = selected_context_values(page["content"])
                assert values["/goal_boundary/stop"] == "Stop before publishing."
                assert values["/heartbeat_receipt/turn_instance_id"] == "t"
                command, phase = page["navigation"]["remaining_read_command"], "remaining"
            elif phase == "remaining":
                assert page and page["ok"] and page["offset"] == len(detail)
                detail.extend(page["content"].encode())
                assert len(detail) == (page["total_bytes"] if page["eof"] else page["next_offset"])
                if not page["eof"]:
                    command = page["next_command"]
                else:
                    text = selected_context_values(detail.decode())[
                        "/interaction_contract/agent_channel/work_context/sources/0/content/text"]
                    assert text == requirement
                    command, phase = shlex.join(["printf", "%s", text.splitlines()[-1]]), "work"
            else:
                assert latest_output(request).split("\nOutput:\n", 1)[-1].strip() == requirement.splitlines()[-1]
                phase = "done"
                return message
            call = {"id": f"fc_{number}", "call_id": f"call_{number}"}
            arguments = json.dumps({"cmd": command, "max_output_tokens": tool_tokens, "yield_time_ms": 10000})
            if code_mode:
                return {**call, "type": "custom_tool_call", "name": "exec",
                    "input": '// @exec: {"max_output_tokens": 12000}\nconst result=await tools.exec_command('
                        + arguments + ');' + CODE_MODE_EXEC_RESULT_FORWARDER + ';'}
            return {**call, "type": "function_call", "name": "exec_command", "arguments": arguments}
        except Exception as exc:
            errors.append(exc)
            return message

    with local_inference_provider(response) as (url, _, _):
        _configure_provider(home, url)
        config = home / "config.toml"
        native_config = {**configuration["config_overrides"], "tool_output_token_limit": history_tokens}
        overrides = "".join(f"{key}={json.dumps(value)}\n" for key, value in native_config.items())
        text = config.read_text()
        if code_mode:
            text = text.replace("[features]\n", "[features]\ncode_mode=true\n")
        config.write_text(overrides + text + "[sandbox_workspace_write]\nnetwork_access=true\n")
        _app_server(binary, env, workspace, home, True, sandbox="workspace-write", turn_timeout_sec=120)
    assert not errors and phase == "done", (errors, phase)
    assert source.read_bytes() == raw
    assert not (workspace / "absent-registry.json").exists() and not (workspace / "absent-runtime").exists()


def test_saved_page_continues_and_replays_after_compaction_without_republication(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from loopx.control_plane.turn_driver.codex_context import codex_context_hook

    runtime, registry, context = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "native"
    context.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "session")
    monkeypatch.chdir(tmp_path)
    event = {"source": "startup", "session_id": "session", "cwd": str(tmp_path)}
    codex_context_hook(str(context), {**event, "hook_event_name": "SessionStart"})
    value = "scope and stops: 保留原要求。" * 1200
    original = tmp_path / "original.json"
    original.write_text(json.dumps({"ok": True, "goal_id": "g", "agent_identity": {"agent_id": "a"},
        "heartbeat_receipt": {"turn_instance_id": "t"}, "instructions": value}, ensure_ascii=False))
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    project = ["quota", "context-project", "--capture-file", str(original), "--capture-sha256", digest,
        "--goal-id", "g", "--agent-id", "a", "--turn-instance-id", "t", "--codex-app",
        "--context-delivery", "on", "--codex-context-dir", str(context), "--max-page-output-bytes", "6400"]
    def cli(*args):
        return _run_cli(registry, runtime, *args, cwd=tmp_path)
    initial_files = set(context.rglob("*"))
    for invalid in (["--initial-read", "--select", "/instructions"], ["--offset", "1", "--select", "/instructions"],
                    ["--context-delivery", "off", "--initial-read"], ["--goal-id", "foreign", "--initial-read"],
                    ["--delta-delivery", "off", "--initial-read", "--page-bytes", "3200"]):
        code, error = cli(*project, *invalid)
        assert code and not error["ok"] and "content" not in error
    code, error = cli(*project, "--select", "/instructions", "--page-bytes", "255")
    assert code and not error["ok"] and "--page-bytes must be between 256 and" in error["reason"]
    assert set(context.rglob("*")) == initial_files
    window = 3200
    code, first = cli(*project, "--select", "/instructions", "--page-bytes", str(window))
    assert code == 0 and first["offset"] == 0 and not first["eof"]
    assert first["navigation"]["mode"] == "full"
    codex_context_hook(str(context), {**event, "hook_event_name": "PreCompact"})
    journal = (context / "session.json").read_bytes()
    files = set(context.rglob("*"))
    def read_selection(*, resume):
        result = bytearray()
        if resume:
            result.extend(first["content"].encode())
            assert len(result) <= window
            words = shlex.split(first["next_command"])
            args = words[words.index("quota"):]
        else:
            args = ["quota", "context-read", "--reference-file", first["reference_file"],
                    "--max-page-output-bytes", "6400"]
        while True:
            code, page = cli(*args)
            assert code == 0 and page["offset"] == len(result)
            content = page["content"].encode()
            if resume:
                assert len(content) <= window
            result.extend(content)
            if page["eof"]:
                assert len(result) == page["total_bytes"]
                return json.loads(result)["selections"][0]["value"]
            words = shlex.split(page["next_command"])
            args = words[words.index("quota"):]
    with ThreadPoolExecutor(2) as pool:
        replay = pool.submit(read_selection, resume=False)
        continuation = pool.submit(read_selection, resume=True)
        assert replay.result() == continuation.result() == value
    assert (context / "session.json").read_bytes() == journal and set(context.rglob("*")) == files
    assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
    assert not registry.exists() and not runtime.exists()
