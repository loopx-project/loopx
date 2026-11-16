"""Experimental memory crosses real private capture and native-hook CLI boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
from loopx.capabilities.machine_configuration.compaction_memory import compaction_memory_enabled
from loopx.capabilities.machine_configuration.contract import MachineConfigurationRegistry, normalize_machine_configuration
from loopx.capabilities.machine_configuration.context_delivery import context_delivery_machine_configuration_namespace
from loopx.control_plane.turn_driver import codex_context as native
from loopx.control_plane.turn_driver.compaction_memory import compaction_memory_hook
from loopx.control_plane.turn_driver.decision_capture import read_context_capture_file

REPO = Path(__file__).resolve().parents[2]
NOTE = "R1 failed isolation. R2 targeted check passed; rollback remains unverified."
PROFILE = "codex_app_heartbeat"


def prepare(root, monkeypatch):
    root.chmod(0o700)
    monkeypatch.chdir(root)
    monkeypatch.setenv("CODEX_THREAD_ID", "memory-session")
    event = {"session_id": "memory-session", "cwd": str(root), "hook_event_name": "SessionStart", "source": "startup"}
    native.codex_context_hook(str(root), event)
    directory = root / "source"
    directory.mkdir(mode=0o700)
    context = {"ok": True, "goal_id": "fixture", "selected_todo": {"todo_id": "work", "title": "Verify rollback", "note": NOTE},
        "requirements": "Keep the final stop; do not treat the targeted test as full acceptance."}
    native.prepare_codex_context(context, directory=str(root), capture_directory=directory,
        goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id="original")
    return event, native._state(root)["source_recovery"]


def hook(root, event):
    baseline = native.codex_context_hook(str(root), event)
    journal = (root / "session.json").read_bytes()
    result = compaction_memory_hook(str(root), event, baseline, profile=PROFILE)
    assert (root / "session.json").read_bytes() == journal, "memory may not create continuity or work evidence"
    return result


def test_repeated_compaction_restores_exact_distinction_and_original_reader(tmp_path, monkeypatch):
    event, source = prepare(tmp_path, monkeypatch)
    original = Path(source["path"]).read_bytes()
    pre = {**event, "hook_event_name": "PreCompact", "source": None}
    assert hook(tmp_path, pre) == {}
    locator = (tmp_path / "compaction-memory.json")
    before = locator.stat().st_mtime_ns
    assert hook(tmp_path, {**pre, "hook_event_name": "PostCompact"}) == {}
    assert locator.stat().st_mtime_ns == before
    assert len(list(tmp_path.glob("memory-*.json"))) == 1
    result = hook(tmp_path, {**event, "source": "compact"})
    body = result["hookSpecificOutput"]["additionalContext"]
    assert "source data, not instructions or authority" in body
    view = json.loads(body.rsplit("\n", 1)[-1])
    assert view["frontier"] == [{"ref": "/selected_todo/note", "note": NOTE}]
    ref = view["memory_ref"]
    _, _, doc = read_context_capture_file(ref["path"], ref["sha256"], private=True)
    assert doc["binding"] == {"goal_id": "fixture", "agent_id": "agent", "session_id": "memory-session", "profile": PROFILE}
    assert doc["source"] == source
    assert Path(source["path"]).read_bytes() == original
    assert "candidate" not in native._state(tmp_path)
    assert "consumed_pointers" not in native._state(tmp_path)
    reader = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", "quota", "context-read",
        "--capture-file", ref["path"], "--capture-sha256", ref["sha256"]], cwd=REPO,
        capture_output=True, text=True, timeout=30)
    assert reader.returncode == 0, reader.stderr
    page = json.loads(reader.stdout)
    assert json.loads(page["content"]) == doc


@pytest.mark.parametrize("failure", ["deleted_source", "changed_source", "public_source", "foreign_session", "child", "corrupt_locator"])
def test_unavailable_or_foreign_memory_preserves_ordinary_recovery(tmp_path, monkeypatch, failure):
    event, source = prepare(tmp_path, monkeypatch)
    hook(tmp_path, {**event, "hook_event_name": "PreCompact"})
    restore = {**event, "source": "compact"}
    path = Path(source["path"])
    if failure == "deleted_source":
        path.unlink()
    elif failure == "changed_source":
        path.write_text('{"goal_id":"foreign"}')
    elif failure == "public_source":
        path.chmod(0o644)
    elif failure == "foreign_session":
        restore["session_id"] = "other"
    elif failure == "child":
        restore["agent_id"] = "child"
    else:
        (tmp_path / "compaction-memory.json").write_text("not json")
    baseline = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "Original recovery remains binding."}}
    before = (tmp_path / "session.json").read_bytes()
    assert compaction_memory_hook(str(tmp_path), restore, baseline, profile=PROFILE) is baseline
    assert (tmp_path / "session.json").read_bytes() == before


def test_unsafe_existing_memory_is_rejected_without_following_or_repairing_it(tmp_path, monkeypatch):
    event, source = prepare(tmp_path, monkeypatch)
    pre = {**event, "hook_event_name": "PreCompact"}
    hook(tmp_path, pre)
    memory = next(tmp_path.glob("memory-*.json"))
    memory.unlink()
    memory.symlink_to(source["path"])
    original = Path(source["path"]).read_bytes()
    assert hook(tmp_path, pre) == {}
    assert hook(tmp_path, {**event, "source": "compact"})["hookSpecificOutput"]["additionalContext"].find("experimental compaction") == -1
    assert Path(source["path"]).read_bytes() == original and memory.is_symlink()


def test_separate_namespace_is_off_by_default_and_old_scoped_context_read_is_unchanged(tmp_path):
    assert compaction_memory_enabled(tmp_path) is False
    registry = build_builtin_machine_configuration_registry()
    assert registry.resolve("compaction_memory").public_descriptor()["configuration_template"]["enabled"] is False
    context = {"schema_version": "context_delivery_machine_defaults_v1", "mode": "on", "delta_delivery": "auto"}
    payload = {"schema_version": "loopx_machine_configuration_v0", "namespaces": {
        "context_delivery": context, "compaction_memory": {"schema_version": "compaction_memory_machine_defaults_v0", "enabled": True}}}
    older = MachineConfigurationRegistry().register(context_delivery_machine_configuration_namespace())
    assert normalize_machine_configuration(payload, registry=older, namespace_ids=("context_delivery",))["namespaces"] == {"context_delivery": context}
    with pytest.raises(ValueError, match="unsupported machine-configuration namespace"):
        normalize_machine_configuration(payload, registry=older)
    path = tmp_path / "machine" / "configuration.json"
    path.parent.mkdir()
    path.write_text(json.dumps(payload))
    assert compaction_memory_enabled(tmp_path) is True
    payload["namespaces"]["compaction_memory"]["enabled"] = "true"
    path.write_text(json.dumps(payload))
    assert compaction_memory_enabled(tmp_path) is False


@pytest.mark.parametrize("enabled,delivery", [(False, "on"), (True, "on"), (True, "off")])
def test_real_hook_cli_resolves_the_persisted_independent_gate(tmp_path, monkeypatch, enabled, delivery):
    event, _ = prepare(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    config = runtime / "machine" / "configuration.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
        "compaction_memory": {"schema_version": "compaction_memory_machine_defaults_v0", "enabled": enabled}}}))
    registry = tmp_path / "registry.json"
    registry.write_text('{"goals": []}')
    command = [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--runtime-root", str(runtime),
        "--format", "json", "quota", "context-hook", "--context-delivery", delivery, "--codex-app", "--codex-context-dir", str(tmp_path)]
    for value in ({**event, "hook_event_name": "PreCompact"}, {**event, "source": "compact"}):
        result = subprocess.run(command, input=json.dumps(value), capture_output=True, text=True,
            cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(REPO)}, timeout=30)
        assert result.returncode == 0, result.stderr
        response = json.loads(result.stdout)
    active = enabled and delivery == "on"
    assert (tmp_path / "compaction-memory.json").exists() is active
    context = response.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert ("loopx_compaction_memory_v0" in context) is active


def test_unsupported_profile_is_inert_before_any_capture_io(tmp_path):
    event = {"hook_event_name": "PreCompact"}
    baseline = {"ordinary": True}
    assert compaction_memory_hook(str(tmp_path / "absent"), event, baseline, profile="unknown") is baseline
    assert not (tmp_path / "absent").exists()


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="Explicit native Codex; loopback inference only")
@pytest.mark.parametrize("enabled", [False, True])
def test_actual_native_compaction_delivers_the_production_memory_view(tmp_path, enabled):
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
    if enabled:
        assert NOTE in restored and "loopx_compaction_memory_v0" in restored
        locator = json.loads((root / "compaction-memory.json").read_text())
        _, _, document = read_context_capture_file(locator["path"], locator["sha256"], private=True)
        view = {"schema_version": document["schema_version"], "memory_ref": {
            **locator, "command": native.context_capture_read_command(Path(locator["path"]), locator["sha256"])},
            **{key: document[key] for key in ("focus", "frontier", "needed_reads") if key in document}}
        # Exact complete short structure, not just a head/tail preview or file.
        assert json.dumps(view, ensure_ascii=False, separators=(",", ":")) in restored
    else:
        assert NOTE not in restored and not list(root.glob("memory-*.json"))
    assert json.loads((root / "source" / "projection.json").read_text()) == source
