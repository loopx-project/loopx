"""Native lifecycle references cannot stand in for retained model input."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.control_plane.turn_driver import codex_context as context


def test_shared_root_keeps_interleaved_native_sessions_independent(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.chdir(tmp_path)
    original = {}
    for session in ("one", "../../two"):
        directory = Path(context.codex_context_directory(root=str(tmp_path), session=session, create=True))
        assert directory.parent == tmp_path and directory.stat().st_mode & 0o077 == 0
        monkeypatch.setenv("CODEX_THREAD_ID", session)
        event = dict(session_id=session, cwd=str(tmp_path), hook_event_name="SessionStart", source="startup")
        context.codex_context_hook(str(directory), event)
        _, wire = project(directory)
        original[session] = (directory, wire["recovery"], (directory / "session.json").read_bytes())
    assert original["one"][0] != original["../../two"][0]
    for session, (directory, recovery, raw) in original.items():
        monkeypatch.setenv("CODEX_THREAD_ID", session)
        selected = context.codex_context_directory(root=str(tmp_path))
        assert selected == str(directory)
        assert context.read_codex_context_reference(selected, goal_id="fixture", agent_id="agent") == (
            recovery["path"], recovery["sha256"])
        assert (directory / "session.json").read_bytes() == raw


def test_shared_root_read_does_not_create_unknown_session_or_follow_alias(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "unknown")
    with pytest.raises(FileNotFoundError):
        context.codex_context_directory(root=str(tmp_path))
    assert list(tmp_path.iterdir()) == []
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    with pytest.raises(ValueError, match="native session"):
        context.codex_context_directory(root=str(tmp_path), create=True)
    import hashlib

    alias = tmp_path / hashlib.sha256(b"session").hexdigest()
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="private"):
        context.codex_context_directory(root=str(tmp_path), session="session", create=True)
    assert not (target / "session.json").exists()


def test_native_configuration_cli_is_read_only_and_usable_without_registry(tmp_path):
    tmp_path.chmod(0o700)
    repo = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "loopx.entrypoint", "--format", "json", "quota", "context-config",
        "--context-delivery", "on", "--codex-app", "--codex-context-root", str(tmp_path), "--goal-id", "fixture", "--agent-id", "agent"]
    result = subprocess.run(command, cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(repo)},
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    native = payload["native_config"]
    assert set(native["hooks"]) == {"SessionStart", "PreToolUse"}
    assert "--codex-context-root" in native["developer_instructions"]
    assert "context-read" in native["developer_instructions"]
    assert "hooks.state" not in native and list(tmp_path.iterdir()) == []
    missing_identity = subprocess.run(command[:-2], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(repo)}, capture_output=True, text=True, timeout=15)
    assert missing_identity.returncode == 1 and not json.loads(missing_identity.stdout)["ok"]


def test_context_transport_retains_native_auth_and_explicit_provider_ownership():
    original = {"model_provider": "operator", "model_providers": {"operator": {
        "base_url": "https://example.invalid", "supports_websockets": True}}}
    assert context.codex_context_transport_configuration(original) == {}
    assert original["model_providers"]["operator"]["supports_websockets"] is True
    native = context.codex_context_transport_configuration({"model_provider": "openai"})
    selected = native["model_provider"]
    assert native["model_providers"][selected] == {
        "name": "OpenAI", "wire_api": "responses", "requires_openai_auth": True,
        "supports_websockets": False}
    with pytest.raises(ValueError, match="conflicts"):
        context.codex_context_transport_configuration({"model_providers": {
            selected: {"base_url": "https://example.invalid"}}})


def setup(root, monkeypatch):
    root.chmod(0o700)
    monkeypatch.chdir(root)
    monkeypatch.setenv("CODEX_THREAD_ID", "session")
    monkeypatch.delenv("CODEX_ROLLOUT_TRACE_ROOT", raising=False)
    event = dict(session_id="session", cwd=str(root), hook_event_name="SessionStart", source="startup")
    assert context.codex_context_hook(str(root), event) == {}
    return event


def project(root, capture_id="one", *, payload=None):
    directory = root / capture_id
    directory.mkdir(mode=0o700)
    if payload is None:
        payload = {"ok": True, "goal_id": "fixture", "requirements": "Preserve final stop condition."}
    result = context.project_codex_context(payload, directory=str(root), capture_directory=directory,
        goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id=capture_id)
    return payload, result


@pytest.mark.parametrize("event_name,source", [("SessionStart", "compact"),
    ("SessionStart", "startup"), ("PreCompact", None)])
def test_lifecycle_invalidates_and_recovers_current_capture_without_bodies(tmp_path, monkeypatch, event_name, source):
    event = setup(tmp_path, monkeypatch)
    payload, first = project(tmp_path)
    assert first["mode"] == "full" and first["context"] == payload
    before = context._state(tmp_path)
    result = context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": event_name, "source": source})
    after = context._state(tmp_path)
    assert before["context_epoch"] != after["context_epoch"] and "candidate" not in after
    assert after["recovery"] == before["recovery"]
    if event_name == "SessionStart":
        body = result["hookSpecificOutput"]["additionalContext"]
        assert first["recovery"]["sha256"] in body and "quota context-read" in body
        assert payload["requirements"] not in body and len(body) < 2000
    else:
        assert result == {}
    _, second = project(tmp_path, "two")
    assert second["mode"] == "full" and second["target"]["context_epoch"] == after["context_epoch"]


def test_resume_keeps_only_an_unconfirmed_candidate_and_never_borrows_old_call(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    _, first = project(tmp_path)
    context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PreToolUse", "tool_use_id": "old"})
    before = context._state(tmp_path)
    result = context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    after = context._state(tmp_path)
    assert after["candidate"] == before["candidate"] and after["context_epoch"] == before["context_epoch"]
    assert after["active_call"] is None
    assert first["recovery"]["command"] in result["hookSpecificOutput"]["additionalContext"]
    assert "unconfirmed" in result["hookSpecificOutput"]["additionalContext"]
    # No current-input proof: even a complete private candidate remains full.
    _, second = project(tmp_path, "two")
    assert second["mode"] == "full"


@pytest.mark.parametrize("case", ["closed_old", "duplicate_live", "abandoned_old", "missing_input", "discovery_overflow"])
def test_resumed_bundle_needs_unique_current_input_not_latest_file(tmp_path, monkeypatch, case):
    from test_codex_input_retention import fixture

    event = setup(tmp_path, monkeypatch)
    requirements = "Preserve the whole source and final stop condition. " * 1000
    payload = {"ok": True, "goal_id": "fixture", "plan_summary": {"requirements": requirements}}
    _, first = project(tmp_path, payload=payload)
    traces = tmp_path / "traces"
    traces.mkdir()
    for number in (1, 2):
        parent = tmp_path / str(number)
        parent.mkdir()
        observation, _, _, rows = fixture(parent)
        bundle = Path(observation["trace_bundle"])
        if number == 1 and case not in {"duplicate_live", "abandoned_old"}:
            rows.append({**rows[-1], "seq": 3, "payload": {"type": "tool_call_ended", "tool_call_id": "guard"}})
        (bundle / "trace.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        missing = case == "missing_input" or (case == "abandoned_old" and number == 2)
        (bundle / "payloads/input.json").write_text(json.dumps({"input": [] if missing else [
            {"type": "function_call_output", "output": json.dumps(first)}]}))
        bundle.rename(traces / f"trace-{number}")
    if case == "discovery_overflow":
        for number in range(3, 130):
            (traces / f"trace-{number}").mkdir()
    monkeypatch.setenv("CODEX_ROLLOUT_TRACE_ROOT", str(traces))
    context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
    context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PreToolUse", "tool_use_id": "guard"})
    _, second = project(tmp_path, "two", payload=payload)
    assert second["mode"] == ("delta" if case == "closed_old" else "full")


def test_missing_observer_or_hook_never_reuses_candidate_and_other_sessions_do_not_borrow(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    _, first = project(tmp_path)
    context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PreToolUse", "tool_use_id": "current"})
    _, second = project(tmp_path, "two")
    assert second["mode"] == "full"  # a present file plus a hook is not proof
    assert second["target"]["capture_id"] != first["target"]["capture_id"]
    monkeypatch.setenv("CODEX_THREAD_ID", "another-session")
    payload, rejected = project(tmp_path, "other")
    assert rejected == payload
    assert context.codex_context_hook(str(tmp_path), {**event, "session_id": "another-session", "source": "fork"}) == {}
    assert "recovery" not in context._state(tmp_path) and "candidate" not in context._state(tmp_path)


def test_primary_write_failure_preserves_small_recovery_and_does_not_create_ack(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    _, wire = project(tmp_path)
    before = (tmp_path / "session.json").read_bytes()
    def unavailable(*args, **kwargs):
        raise OSError("synthetic journal unavailable")
    monkeypatch.setattr(context, "_save", unavailable)
    result = context.codex_context_hook(str(tmp_path), {**event, "source": "compact"})
    assert wire["recovery"]["sha256"] in result["hookSpecificOutput"]["additionalContext"]
    assert (tmp_path / "session.json").read_bytes() == before


def _assert_recovery_after_fallback(root, event, source, capture):
    import argparse
    import hashlib
    from loopx.cli_commands.quota_capture import read_context_capture

    hook = context.codex_context_hook(str(root), {**event, "source": source})
    args = argparse.Namespace(codex_context_dir=str(root), goal_id="fixture", agent_id="agent",
                              offset=0, page_bytes=8192)
    page = read_context_capture(args)
    if capture is None:
        assert hook == {} and page["ok"] is False and "content" not in page
        return
    raw = capture.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert page["ok"] is True and page["sha256"] == digest
    assert page["content"].encode() == raw[:len(page["content"].encode())]
    assert digest in hook["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("source", ["compact", "resume"])
def test_oversized_warm_request_keeps_current_full_recovery(tmp_path, monkeypatch, source):
    from test_codex_input_retention import fixture
    from loopx.control_plane.effect_runtime import EffectRuntimeRejected, MAX_REQUEST_BYTES
    from loopx.control_plane.quota.turn_envelope import prepare_context_delivery
    from loopx.control_plane.turn_driver.codex_input_trace import codex_context_in_active_input

    event = setup(tmp_path, monkeypatch)
    before = {"ok": True, "goal_id": "fixture", "stop": "Stop before publication.",
              "plan_summary": {"requirements": "x" * (MAX_REQUEST_BYTES // 2 + 8192)}}
    after = {**before, "stop": "Stop before publication or deployment."}
    _, first = project(tmp_path, payload=before)
    original = Path(first["recovery"]["path"]).read_bytes()
    assert len(original) < MAX_REQUEST_BYTES
    traces = tmp_path / "traces"
    traces.mkdir()
    observation, _, _, _ = fixture(traces)
    bundle = Path(observation["trace_bundle"])
    # Actual production verifier consumes these synthetic native trace files;
    # neither the projector nor the RPC size limit is mocked.
    request = bundle / "payloads/input.json"
    request.write_text(json.dumps({"input": [{"type": "function_call_output", "output": json.dumps(first)}]}))
    assert request.stat().st_size < MAX_REQUEST_BYTES
    current_bundle = traces / "trace-live"
    bundle.rename(current_bundle)
    observation.update(trace_bundle=str(current_bundle), capture_file=first["recovery"]["path"],
                       capture_sha256=first["recovery"]["sha256"])
    assert codex_context_in_active_input(observation, baseline=first, session_id="session")
    monkeypatch.setenv("CODEX_ROLLOUT_TRACE_ROOT", str(traces))
    context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PreToolUse", "tool_use_id": "guard"})
    scope = {key: first["target"][key] for key in ("goal_id", "agent_id", "session_id", "context_epoch")}
    with pytest.raises(EffectRuntimeRejected) as rejected:
        prepare_context_delivery(after, scope=scope, capture_id="two", surface="quota_packet",
                                 baseline=first, retained={"complete": True, "target": first["target"]})
    assert rejected.value.diagnostic_code == "request_too_large"
    _, delivered = project(tmp_path, "two", payload=after)
    assert delivered.get("context", delivered) == after
    current_capture = tmp_path / "two/projection.json"
    assert current_capture.stat().st_size < MAX_REQUEST_BYTES
    assert json.loads(current_capture.read_bytes()) == after
    _assert_recovery_after_fallback(tmp_path, event, source, current_capture)
    assert Path(first["recovery"]["path"]).read_bytes() == original


@pytest.mark.parametrize("source", ["compact", "resume"])
@pytest.mark.parametrize("failed_write", ["recovery", "candidate"])
def test_journal_failure_never_recovers_previous_capture(tmp_path, monkeypatch, source, failed_write):
    event = setup(tmp_path, monkeypatch)
    _, first = project(tmp_path)
    original = Path(first["recovery"]["path"]).read_bytes()
    current_capture = tmp_path / "two/projection.json"
    original_save, failures = context._save, []

    def fail_current_write(root, state):
        current_recovery = state.get("recovery", {}).get("sha256") not in (None, first["recovery"]["sha256"])
        current_candidate = state.get("candidate", {}).get("capture_file") == str(current_capture)
        if current_recovery and (failed_write == "recovery" or current_candidate):
            failures.append(failed_write)
            raise OSError("synthetic current publication unavailable")
        original_save(root, state)

    with monkeypatch.context() as publication:
        publication.setattr(context, "_save", fail_current_write)
        payload, delivered = project(tmp_path, "two", payload={"ok": True, "goal_id": "fixture",
            "requirements": "Preserve the changed source and stop before deployment."})
    assert failures and delivered.get("context", delivered) == payload
    assert json.loads(current_capture.read_bytes()) == payload
    _assert_recovery_after_fallback(tmp_path, event, source,
                                   current_capture if failed_write == "candidate" else None)
    assert Path(first["recovery"]["path"]).read_bytes() == original


@pytest.mark.parametrize("source", ["compact", "resume"])
@pytest.mark.parametrize("caller", ["subdirectory", "missing_session", "foreign_session"])
def test_fallback_identity_restoration_keeps_recovery_scoped(tmp_path, monkeypatch, source, caller):
    event = setup(tmp_path, monkeypatch)
    _, first = project(tmp_path)
    original_capture = Path(first["recovery"]["path"])
    original = original_capture.read_bytes()
    context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PreToolUse", "tool_use_id": "guard"})
    with monkeypatch.context() as native_tool:
        if caller == "subdirectory":
            subdirectory = tmp_path / "subdirectory"
            subdirectory.mkdir()
            native_tool.chdir(subdirectory)
        elif caller == "missing_session":
            native_tool.delenv("CODEX_THREAD_ID")
        else:
            native_tool.setenv("CODEX_THREAD_ID", "foreign-session")
        payload, delivered = project(tmp_path, "two", payload={"ok": True, "goal_id": "fixture",
            "requirements": "Changed source; stop before deployment."})
    assert delivered == payload
    _assert_recovery_after_fallback(tmp_path, event, source,
        original_capture if caller == "foreign_session" else None)
    assert original_capture.read_bytes() == original


@pytest.mark.parametrize("failure", ["invalidation", "lock"])
def test_unavailable_recovery_boundary_refuses_current_publication(tmp_path, monkeypatch, failure):
    from loopx.control_plane.quota.error_codes import QuotaDecisionTransportError

    setup(tmp_path, monkeypatch)
    _, first = project(tmp_path)
    journal = tmp_path / "session.json"
    original = journal.read_bytes()
    unlink = Path.unlink

    def unavailable(*args, **kwargs):
        raise OSError("synthetic journal publication unavailable")

    def cannot_invalidate(path, *args, **kwargs):
        if path == journal:
            raise OSError("synthetic journal invalidation unavailable")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as publication:
        publication.setattr(context, "_save", unavailable)
        publication.setattr(Path, "unlink", cannot_invalidate)
        if failure == "lock":
            publication.setattr(context, "_locked", unavailable)
        with pytest.raises(QuotaDecisionTransportError):
            project(tmp_path, "two", payload={"ok": True, "goal_id": "fixture", "requirements": "Current source."})
    assert journal.read_bytes() == original
    assert json.loads(Path(first["recovery"]["path"]).read_bytes())["requirements"] == "Preserve final stop condition."


@pytest.mark.parametrize("damage", ["missing", "corrupt", "symlink", "public", "other_workspace",
    "missing_epoch", "invalid_epoch", "read_failure", "projection_failure"])
def test_invalid_journal_falls_back_to_original_full(tmp_path, monkeypatch, damage):
    event = setup(tmp_path, monkeypatch)
    project(tmp_path)
    path = tmp_path / "session.json"
    before = path.read_bytes()
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_text("{partial")
    elif damage == "symlink":
        other = tmp_path / "other.json"
        path.rename(other)
        path.symlink_to(other)
    elif damage == "public":
        path.chmod(0o644)
    elif damage == "other_workspace":
        state = context._state(tmp_path)
        state["cwd"] = str(tmp_path.parent)
        context._save(tmp_path, state)
    elif damage in {"missing_epoch", "invalid_epoch"}:
        state = context._state(tmp_path)
        state.pop("context_epoch")
        if damage == "invalid_epoch":
            state["context_epoch"] = 3
        context._save(tmp_path, state)
    with monkeypatch.context() as failed:
        if damage in {"read_failure", "projection_failure"}:
            def unavailable(*args, **kwargs):
                raise OSError("synthetic pre-publication IO unavailable")
            failed.setattr(context, "_state" if damage == "read_failure" else "capture_context_projection", unavailable)
        payload, full = project(tmp_path, "two")
    assert full == payload
    if damage == "symlink":
        assert other.read_bytes() == before
    _assert_recovery_after_fallback(tmp_path, event, "compact", None)


def test_real_hook_cli_is_transport_only_without_registry_or_installation(tmp_path):
    directory = tmp_path / "native"
    directory.mkdir(mode=0o700)
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    cmd = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry), "--runtime-root", str(runtime),
        "quota", "context-hook", "--context-delivery", "on", "--codex-app", "--codex-context-dir", str(directory)]
    event = dict(session_id="session", cwd=str(tmp_path), hook_event_name="SessionStart", source="startup")
    result = subprocess.run(cmd, input=json.dumps(event), capture_output=True, text=True, timeout=20,
        cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 0 and json.loads(result.stdout) == {}, result.stderr
    assert context._state(directory)["session_id"] == "session"
    assert not registry.exists() and not runtime.exists()


@pytest.mark.parametrize("damage", [None, "session", "workspace", "goal", "agent", "missing_scope", "missing", "corrupt", "public", "symlink"])
def test_lost_hook_reference_is_scoped_read_only_and_pins_later_pages(tmp_path, monkeypatch, damage):
    import argparse
    from loopx.cli_commands.quota_capture import read_context_capture

    setup(tmp_path, monkeypatch)
    _, first = project(tmp_path, payload={"goal_id": "fixture", "requirements": "保留🙂" * 400})
    journal = tmp_path / "session.json"
    args = argparse.Namespace(codex_context_dir=str(tmp_path), goal_id="fixture", agent_id="agent",
                              offset=0, page_bytes=256)
    if damage == "session":
        monkeypatch.setenv("CODEX_THREAD_ID", "other")
    elif damage == "workspace":
        monkeypatch.chdir(tmp_path.parent)
    elif damage in {"goal", "agent"}:
        setattr(args, damage + "_id", "other")
    elif damage == "missing_scope":
        state = context._state(tmp_path)
        state["recovery"].pop("goal_id")
        context._save(tmp_path, state)
    elif damage == "missing":
        journal.unlink()
    elif damage == "corrupt":
        journal.write_text("{broken")
    elif damage == "public":
        journal.chmod(0o644)
    elif damage == "symlink":
        original = tmp_path / "original.json"
        journal.rename(original)
        journal.symlink_to(original)
    before = journal.read_bytes() if journal.exists() else None
    page = read_context_capture(args)
    assert (journal.read_bytes() if journal.exists() else None) == before
    assert page["ok"] is (damage is None)
    if damage:
        assert "content" not in page
        return
    assert page["sha256"] == first["recovery"]["sha256"] and page["offset"] == 0
    assert "--codex-context-dir" not in page["next_command"]
    assert first["recovery"]["sha256"] in page["next_command"]
    # Publication can move the selector; the first page's explicit next command
    # must continue the original capture, never silently switch to the new one.
    _, second = project(tmp_path, "two", payload={"goal_id": "fixture", "requirements": "changed " * 400})
    assert second["recovery"]["sha256"] != page["sha256"]
    args.capture_file = first["recovery"]["path"]
    args.capture_sha256 = first["recovery"]["sha256"]
    args.codex_context_dir = None
    args.offset = page["next_offset"]
    rest = read_context_capture(args)
    assert rest["ok"] and rest["sha256"] == page["sha256"]
    assert rest["content"] == Path(args.capture_file).read_bytes()[args.offset:args.offset + len(rest["content"].encode())].decode()
    args.codex_context_dir = str(tmp_path)
    assert not read_context_capture(args)["ok"]  # no mixed moving and pinned selectors



@pytest.mark.parametrize("setting", [None, "off", "on"])
@pytest.mark.parametrize("host_args,expected", [
    ([], None),
    (["--codex-app"], "codex_app_heartbeat"),
    (["--runtime-profile", "codex_app_heartbeat"], "codex_app_heartbeat"),
    (["--host-surface", "codex_app", "--scheduler-owner", "host_automation", "--execution-mode", "hosted_automation"], "codex_app_heartbeat"),
    (["--host-surface", "codex_cli", "--scheduler-owner", "outer_controller", "--execution-mode", "isolated_headless"], None),
    (["--runtime-profile", "generic_cli"], None),
    (["--runtime-profile", "claude_code"], None),
    (["--runtime-profile", "codex_cli"], None),
    (["--host-surface", "codex_app"], None),
    (["--codex-app", "--host-surface", "codex_cli"], None),
    (["--codex-app", "--runtime-profile", "generic_cli"], None),
    (["--codex-app", "--trae_app"], None),
])
def test_activation_reuses_declared_host_mode_and_off_is_inert(tmp_path, monkeypatch, setting, host_args, expected):
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import handle_native_context_command

    # Ambient native environment is not an independent activation or mode proof.
    monkeypatch.setenv("CODEX_THREAD_ID", "ambient-native-session")
    monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    tmp_path.chmod(0o700)
    args = ["--format", "json", "quota", "context-config", "--codex-context-dir", str(tmp_path),
            "--goal-id", "fixture", "--agent-id", "agent", *host_args]
    if setting is not None:
        args += ["--context-delivery", setting]
    results = []
    parsed = build_parser().parse_args(args)
    code = handle_native_context_command(parsed, print_payload=lambda value, *_: results.append(value))
    result = results[0]
    if setting == "on" and expected is None:
        assert code == 1 and result["ok"] is False
        assert "supported declared run mode" in result["reason"]
        assert "--context-delivery off" in result["reason"]
        assert "native_config" not in result and list(tmp_path.iterdir()) == []
        return
    assert code == 0 and result["ok"] is True
    activation = result["context_delivery"]
    expected = expected if setting == "on" else None
    assert activation["requested"] == (setting or "off")
    assert activation["enabled"] is (expected is not None) and activation["scenario"] == expected
    assert activation["supported_scenarios"] == ["codex_benchmark_heartbeat", "codex_app_heartbeat"]
    if expected is None:
        assert "native_config" not in result
    else:
        for entries in result["native_config"]["hooks"].values():
            assert "--context-delivery on" in entries[0]["hooks"][0]["command"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("setting", ["true", "generic_cli", "claude_code", "all", "codex_app_heartbeat"])
def test_context_activation_rejects_non_binary_settings(setting):
    from loopx.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["quota", "context-config", "--context-delivery", setting])


@pytest.mark.parametrize("mode,setting,expected", [("heartbeat", "on", True), ("plain", "on", False),
    (None, "on", False), ("heartbeat", "off", False)])
def test_benchmark_activation_uses_existing_explicit_worker_declaration(monkeypatch, mode, setting, expected):
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import native_context_activation

    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "on")
    if mode is not None:
        monkeypatch.setenv("LOOPX_EXECUTION_MODE", mode)
    else:
        monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    args = build_parser().parse_args(["quota", "context-config", "--runtime-profile", "generic_cli",
        "--context-delivery", setting])
    if setting == "on" and not expected:
        with pytest.raises(ValueError, match="supported declared run mode"):
            native_context_activation(args)
        return
    result = native_context_activation(args)
    assert result["enabled"] is expected
    assert result["scenario"] == ("codex_benchmark_heartbeat" if expected else None)
