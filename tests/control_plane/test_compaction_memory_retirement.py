"""Retired settings cannot revive compaction work; original recovery stays intact."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.control_plane.turn_driver import codex_context as native

REPO = Path(__file__).resolve().parents[2]
NOTE = "R1 failed isolation. R2 targeted check passed; rollback remains unverified."


def test_retired_memory_reference_remains_readable_with_the_original_digest(tmp_path):
    from loopx.control_plane.turn_driver.decision_capture import write_decision_capture

    document = {"schema_version": "loopx_compaction_memory_v0",
        "binding": {"goal_id": "fixture", "agent_id": "agent", "session_id": "original-session", "profile": "codex_app_heartbeat"},
        "frontier": [{"ref": "/selected_todo/note", "note": NOTE}]}
    memory = tmp_path / "memory-original.json"
    digest = write_decision_capture(memory, document)
    before = memory.read_bytes()
    command = [sys.executable, "-m", "loopx.cli", "--format", "json", "quota", "context-read",
        "--capture-file", str(memory), "--capture-sha256", digest]
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(json.loads(result.stdout)["content"]) == document
    assert memory.read_bytes() == before
    rejected = subprocess.run([*command[:-1], "0" * 64], cwd=REPO, capture_output=True, text=True, timeout=30)
    assert rejected.returncode != 0
    assert memory.read_bytes() == before


@pytest.mark.parametrize("delivery,delta", [("on", None), ("off", None), ("off", "auto")])
def test_real_hook_cli_has_parity_with_absent_false_and_true_retired_values(tmp_path, monkeypatch, delivery, delta):
    monkeypatch.setenv("CODEX_THREAD_ID", "recovery-session")
    monkeypatch.chdir(tmp_path)
    tmp_path.chmod(0o700)
    event = {"session_id": "recovery-session", "cwd": str(tmp_path), "hook_event_name": "SessionStart", "source": "startup"}
    native.codex_context_hook(str(tmp_path), event)
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    context = {"ok": True, "goal_id": "fixture", "selected_todo": {"todo_id": "work", "title": "Verify rollback", "note": NOTE}}
    native.prepare_codex_context(context, directory=str(tmp_path), capture_directory=source,
        goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id="original")
    capture = (source / "projection.json").read_bytes()
    journal = tmp_path / "session.json"
    initial = journal.read_bytes()
    runtime = tmp_path / "runtime"
    configuration = runtime / "machine" / "configuration.json"
    configuration.parent.mkdir(parents=True)
    registry = tmp_path / "registry.json"
    registry.write_text('{"goals": []}')
    command = [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--runtime-root", str(runtime),
        "--format", "json", "quota", "context-hook", "--context-delivery", delivery, "--codex-app", "--codex-context-dir", str(tmp_path)]
    if delta is not None:
        command.extend(["--delta-delivery", delta])
    baseline = None
    for enabled in (None, False, True):
        journal.write_bytes(initial)
        if enabled is not None:
            configuration.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
                "compaction_memory": {"schema_version": "compaction_memory_machine_defaults_v0", "enabled": enabled}}}))
        outputs = []
        for hook_event in ({**event, "hook_event_name": "PreCompact"}, {**event, "hook_event_name": "PostCompact"}, {**event, "source": "compact"}):
            result = subprocess.run(command, input=json.dumps(hook_event), capture_output=True, text=True,
                cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(REPO)}, timeout=30)
            assert result.returncode == 0, result.stderr
            outputs.append(json.loads(result.stdout))
        if baseline is None:
            baseline = outputs
        assert outputs == baseline
        assert not (tmp_path / "compaction-memory.json").exists()
        assert not list(tmp_path.glob("memory-*.json"))
        assert (source / "projection.json").read_bytes() == capture


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="Explicit native Codex; loopback inference only")
@pytest.mark.parametrize("enabled", [False, True])
def test_actual_native_compaction_ignores_retired_opt_in(tmp_path, enabled):
    """Only initial work data is a fixture; post-compaction delivery is production IO."""
    from tests.control_plane.codex_checkpoint_fixture import local_inference_provider
    from tests.control_plane.test_codex_context_boundary import (
        _app_server, _configure_provider, _input_text, _native_fixture, _response, _usage,
    )

    binary, env, workspace, home = _native_fixture(tmp_path)
    root, runtime = tmp_path / "native-context", tmp_path / "runtime"
    root.mkdir(mode=0o700)
    configuration = runtime / "machine" / "configuration.json"
    configuration.parent.mkdir(parents=True)
    configuration.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
        "compaction_memory": {"schema_version": "compaction_memory_machine_defaults_v0", "enabled": enabled}}}))
    registry = tmp_path / "registry.json"
    registry.write_text('{"goals": []}')
    source = {"ok": True, "goal_id": "fixture", "selected_todo": {"todo_id": "work", "title": "Verify rollback", "note": NOTE},
        "requirements": "Preserve the original stop and unfinished rollback check."}
    command = [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--runtime-root", str(runtime),
        "--format", "json", "quota", "context-hook", "--context-delivery", "on", "--codex-app", "--codex-context-dir", str(root)]
    wrapper = home / "hook.py"
    wrapper.write_text(f'''import json, os, pathlib, subprocess, sys
event = json.load(sys.stdin)
env = {{**os.environ, "PYTHONPATH": {str(REPO)!r}, "CODEX_THREAD_ID": event["session_id"]}}
result = subprocess.run({command!r}, input=json.dumps(event), capture_output=True, text=True, env=env, timeout=20)
assert result.returncode == 0, result.stderr
payload = json.loads(result.stdout)
if event["hook_event_name"] == "SessionStart" and event.get("source") == "startup":
    os.environ.update(env)
    sys.path.insert(0, {str(REPO)!r})
    from loopx.control_plane.turn_driver import codex_context as native
    directory = pathlib.Path({str(root / 'source')!r})
    directory.mkdir(mode=0o700)
    native.prepare_codex_context({source!r}, directory={str(root)!r}, capture_directory=directory,
        goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id="original")
    payload = {{"hookSpecificOutput": {{"hookEventName": "SessionStart",
        "additionalContext": "Synthetic initial work data: " + json.dumps({source!r})}}}}
print(json.dumps(payload))
''')
    import shlex

    handler = {"type": "command", "command": shlex.join([sys.executable, str(wrapper)]), "timeout": 10}
    # Keep production defaults; do not bypass a lossy native output limit.
    (home / "hooks.json").write_text(json.dumps({"hooks": {
        name: [{"hooks": [handler]}] for name in native.CONTEXT_LIFECYCLE_HOOKS}}))
    with local_inference_provider(_response, _usage) as (url, calls, requests):
        _configure_provider(home, url)
        _app_server(binary, env, workspace, home, True, turn_timeout_sec=60, allow_managed_hooks=True)
    assert len(calls) == 4
    first, warm, _, restored = map(_input_text, requests)
    assert NOTE in first and NOTE in warm
    assert NOTE not in restored and "loopx_compaction_memory_v0" not in restored
    assert not (root / "compaction-memory.json").exists()
    assert not list(root.glob("memory-*.json"))
    assert json.loads((root / "source" / "projection.json").read_text()) == source
