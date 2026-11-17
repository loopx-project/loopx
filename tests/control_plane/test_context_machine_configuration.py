"""One machine opt-in reaches existing native callers without new authority."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
from contextlib import redirect_stdout

import pytest

from loopx.capabilities.machine_configuration.context_delivery import (
    CONTEXT_DELIVERY_MACHINE_SCHEMA, INDEPENDENT_CONTEXT_SCHEMA,
)
from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _heartbeat_receipt_count, _initialize_fixture_authority,
    _run_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("unsafe", [None, "symlink", "public"])
@pytest.mark.skipif(os.name != "posix", reason="Private POSIX file transport; other CLI hosts retain inline recovery")
def test_rejected_context_snapshot_uses_private_generic_transport_without_native_activation(tmp_path, unsafe):
    from loopx.cli_commands.context_machine import capture_context_recovery
    from loopx.control_plane.turn_driver.decision_capture import read_context_capture_file

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    root = runtime / "context-delivery"
    if unsafe == "symlink":
        target = tmp_path / "other-owner"
        target.mkdir()
        root.symlink_to(target, target_is_directory=True)
    elif unsafe == "public":
        root.mkdir()
        root.chmod(0o755)
    value = {"current": {"goal": "Retain scope", "stop": "No new authority"},
        "optional_evidence": "完整材料" * 20000}
    result = capture_context_recovery(value, runtime_root=runtime)
    if unsafe:
        assert result["available"] is False and result["error_kind"] == "ValueError"
        assert "path" not in result and "optional_evidence" not in result
        assert (root.is_symlink() if unsafe == "symlink" else root.stat().st_mode & 0o077)
        assert list((target if unsafe == "symlink" else root).iterdir()) == []
    else:
        path, raw, saved = read_context_capture_file(result["path"], result["sha256"], private=True)
        assert saved == value and len(raw) > 200000
        assert path.stat().st_mode & 0o077 == 0
        assert path.parent.stat().st_mode & 0o077 == 0
        assert "--list" in result["index_command"] and "--select" in result["select_command"]
        # One immutable observation per rejection; publication cannot replace a
        # different write-time basis or create/advance a native session baseline.
        other = capture_context_recovery({**value, "current": {"goal": "Changed scope"}}, runtime_root=runtime)
        assert other["path"] != result["path"] and other["sha256"] != result["sha256"]
        assert read_context_capture_file(result["path"], result["sha256"], private=True)[2] == value
        assert sorted(p.name for p in root.iterdir()) == sorted([path.parent.name, Path(other["path"]).parent.name])

def configure(registry, runtime, root, mode, *, delta=None):
    config = root / "context-setting.json"
    value = {"schema_version": CONTEXT_DELIVERY_MACHINE_SCHEMA, "mode": mode}
    if delta is not None:
        value.update(schema_version=INDEPENDENT_CONTEXT_SCHEMA, delta_delivery=delta)
    config.write_text(json.dumps(value))
    args = ["--namespace", "context_delivery", "--config-json", str(config)]
    code, preview = _run_cli(registry, runtime, "machine-config", "preview", *args)
    if code:
        return code, preview
    return _run_cli(registry, runtime, "machine-config", "apply", *args,
        "--expected-plan-revision", preview["plan_revision"], "--execute")


def test_machine_catalog_discovers_context_delivery_without_activation(tmp_path):
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    code, catalog = _run_cli(registry, runtime, "machine-config", "describe")
    assert code == 0 and catalog["schema_version"] == "machine_configuration_catalog_v0", catalog
    contexts = [item for item in catalog["namespaces"] if item["namespace"] == "context_delivery"]
    assert len(contexts) == 1
    assert contexts[0]["schema_versions"] == [CONTEXT_DELIVERY_MACHINE_SCHEMA, INDEPENDENT_CONTEXT_SCHEMA]
    assert contexts[0]["configuration_template"] == {
        "schema_version": INDEPENDENT_CONTEXT_SCHEMA, "mode": "on", "delta_delivery": "auto",
    }
    assert not registry.exists() and not (runtime / "machine/configuration.json").exists()
    assert not (runtime / "context-delivery").exists()


@pytest.mark.parametrize("mode", ["on", "off"])
def test_context_mode_does_not_depend_on_an_unrelated_executor(tmp_path, mode):
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    path = runtime / "machine/configuration.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
        "context_delivery": {"schema_version": CONTEXT_DELIVERY_MACHINE_SCHEMA, "mode": mode},
        "steward_executor": {"schema_version": "steward_executor_machine_defaults_v1",
                             "executor_endpoint": "future_executor"},
    }}))
    original = path.read_bytes()
    code, result = _run_cli(registry, runtime, "quota", "context-config", "--codex-app",
        "--goal-id", "fixture", "--agent-id", "worker")
    if mode == "on" and os.name != "posix":
        assert code != 0 and "unsupported" in result["reason"], result
    else:
        assert code == 0, result
        assert result["context_delivery"]["enabled"] is (mode == "on"), result
    assert path.read_bytes() == original
    assert not registry.exists()
    assert not (runtime / "goals").exists()
    if mode == "off":
        assert not (runtime / "context-delivery").exists()
    elif os.name == "posix":
        assert (runtime / "context-delivery").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("invalid", [True, False, None, 1, "auto", "codex_app_heartbeat"])
def test_invalid_machine_mode_never_writes(tmp_path, invalid):
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    code, result = configure(registry, runtime, tmp_path, invalid)
    assert code != 0 and not result["ok"]
    assert not registry.exists() and not (runtime / "machine/configuration.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="Native session continuity requires POSIX")
@pytest.mark.parametrize("mode", ["off", "on"])
@pytest.mark.parametrize("delta", ["off", "auto"])
def test_independent_machine_switches_and_explicit_cli_agree(tmp_path, monkeypatch, mode, delta):
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    args = ["quota", "context-config", "--codex-app", "--goal-id", "fixture", "--agent-id", "worker"]
    code, explicit = _run_cli(registry, runtime, *args, "--context-delivery", mode, "--delta-delivery", delta)
    assert code == 0, explicit
    assert configure(registry, runtime, tmp_path, mode, delta=delta)[1]["readback_verified"]
    code, saved = _run_cli(registry, runtime, *args)
    assert code == 0, saved
    for result in (explicit, saved):
        activation = result["context_delivery"]
        assert activation["progressive_enabled"] is (mode == "on")
        assert activation["enabled"] is (delta == "auto")
        assert activation["delta_delivery"] == delta
    if mode == "off" and delta == "auto":
        command = shlex.split(explicit["native_config"]["hooks"]["SessionStart"][0]["hooks"][0]["command"])
        assert command[command.index("--context-delivery") + 1] == "off"
        assert command[command.index("--delta-delivery") + 1] == "auto"
        assert command[command.index("--registry") + 1] == str(registry)
        assert command[command.index("--runtime-root") + 1] == str(runtime)
        monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "off")
        code, inherited = _run_cli(registry, runtime, *args)
        assert code == 0 and not inherited["context_delivery"]["enabled"]


def test_machine_on_preserves_existing_worker_environment_off(tmp_path, monkeypatch):
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    assert configure(registry, runtime, tmp_path, "on")[0] == 0
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "off")
    monkeypatch.setenv("LOOPX_EXECUTION_MODE", "heartbeat")
    code, result = _run_cli(registry, runtime, "quota", "context-config",
        "--runtime-profile", "generic_cli", "--goal-id", "fixture", "--agent-id", "worker")
    assert code == 0 and result["context_delivery"]["enabled"] is False, result
    assert not (runtime / "context-delivery").exists() and not registry.exists()


@pytest.mark.parametrize("host", [
    ["--runtime-profile", profile] for profile in
    ("trae_app", "claude_code", "kunluncode", "kiro_cli", "ark_managed_agent_goal")
] + [["--host-surface", "claude_code", "--scheduler-owner", "agent_cli_loop",
      "--execution-mode", "interactive"]])
def test_codex_machine_preference_preserves_other_declared_hosts(tmp_path, monkeypatch, host):
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    args = ["quota", "context-config", *host, "--goal-id", "fixture", "--agent-id", "worker"]
    code, original = _run_cli(registry, runtime, *args)
    assert code == 0 and original["context_delivery"]["enabled"] is False, original
    assert configure(registry, runtime, tmp_path, "on")[0] == 0
    code, inherited = _run_cli(registry, runtime, *args)
    assert code == 0 and inherited == original, inherited
    # Explicit opt-in still rejects unsupported hosts rather than ignoring it.
    code, explicit = _run_cli(registry, runtime, *args, "--context-delivery", "on")
    assert code != 0 and not explicit["ok"], explicit
    assert not registry.exists() and not (runtime / "context-delivery").exists()
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.parametrize("selected_mode,default_mode", [("on", None), ("off", "on")])
def test_native_options_share_registry_runtime_without_explicit_root(
    tmp_path, monkeypatch, relative, selected_mode, default_mode,
):
    import loopx.paths as paths
    from loopx.cli import main

    selected, default = tmp_path / "declared", tmp_path / "default"
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", default)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", tmp_path / "legacy")
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": "0.1", "goals": [],
        "common_runtime_root": "declared" if relative else str(selected)}))

    def cli(*args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--registry", str(registry), "--format", "json", *args])
        return code, json.loads(output.getvalue())

    setting = tmp_path / "setting.json"
    setting.write_text(json.dumps({"schema_version": CONTEXT_DELIVERY_MACHINE_SCHEMA, "mode": selected_mode}))
    options = ["--namespace", "context_delivery", "--config-json", str(setting)]
    code, plan = cli("machine-config", "preview", *options)
    assert code == 0, plan
    code, saved = cli("machine-config", "apply", *options,
        "--expected-plan-revision", plan["plan_revision"], "--execute")
    assert code == 0 and saved["readback_verified"], saved
    if default_mode:
        assert configure(registry, default, tmp_path, default_mode)[0] == 0
    selected_bytes = (selected / "machine/configuration.json").read_bytes()
    code, setup = cli("quota", "context-config", "--codex-app", "--goal-id", "fixture", "--agent-id", "worker")
    assert code == 0 and setup["context_delivery"]["enabled"] is (selected_mode == "on"), setup
    assert not (default / "context-delivery").exists()
    if selected_mode == "on":
        command = shlex.split(setup["native_config"]["hooks"]["SessionStart"][0]["hooks"][0]["command"])
        assert command[command.index("--runtime-root") + 1] == str(selected)
        assert (selected / "context-delivery").is_dir()
    else:
        assert not (selected / "context-delivery").exists()
    assert (selected / "machine/configuration.json").read_bytes() == selected_bytes
    # Original digest-bound pages remain independent of a now-unreadable registry.
    capture = tmp_path / "capture.json"
    raw = b'{"original":"capture"}'
    capture.write_bytes(raw)
    registry.write_text("{broken")
    code, page = cli("quota", "context-read", "--capture-file", str(capture),
        "--capture-sha256", hashlib.sha256(raw).hexdigest())
    assert code == 0 and page["content"].encode() == raw, page


@pytest.mark.parametrize("host", [[], ["--runtime-profile", "codex_cli"],
    ["--codex-app", "--runtime-profile", "codex_app_heartbeat"],
    ["--host-surface", "codex_app"], ["--runtime-profile", "generic_cli"]])
def test_inherited_progressive_does_not_invent_unknown_native_continuity(tmp_path, monkeypatch, host):
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    assert configure(registry, runtime, tmp_path, "on")[0] == 0
    code, result = _run_cli(registry, runtime, "quota", "context-config", *host,
        "--goal-id", "fixture", "--agent-id", "worker")
    assert code == 0 and result["ok"], result
    assert result["context_delivery"]["progressive_enabled"] is True
    assert result["context_delivery"]["enabled"] is False and "native_config" not in result
    assert not (runtime / "context-delivery").exists() and not registry.exists()
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("profile", ["codex_app", "benchmark", "codex_app_ssh_goal"])
@pytest.mark.skipif(os.name != "posix", reason="Native continuity uses POSIX private directories and locks")
def test_native_configuration_defaults_on_and_later_explicit_off(tmp_path, monkeypatch, profile):
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    assert not (runtime / "machine/configuration.json").exists()
    if profile == "benchmark":
        monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "on")
        monkeypatch.setenv("LOOPX_EXECUTION_MODE", "heartbeat")
    host = ["--codex-app"] if profile == "codex_app" else ["--runtime-profile", "codex_app_ssh_goal" if profile == "codex_app_ssh_goal" else "generic_cli"]
    code, result = _run_cli(registry, runtime, "quota", "context-config", *host,
        "--goal-id", "fixture", "--agent-id", "worker")
    assert code == 0 and result["context_delivery"]["enabled"], result
    expected = "codex_app_heartbeat" if profile == "codex_app" else ("codex_app_ssh_goal" if profile == "codex_app_ssh_goal" else "codex_benchmark_heartbeat")
    assert result["context_delivery"]["scenario"] == expected
    root = runtime / "context-delivery"
    assert root.stat().st_mode & 0o077 == 0
    handler = result["native_config"]["hooks"]["SessionStart"][0]["hooks"][0]
    argv = shlex.split(handler["command"])
    assert "--context-delivery" not in argv and argv[argv.index("--runtime-root") + 1] == str(runtime)
    assert not registry.exists()
    assert configure(registry, runtime, tmp_path, "off")[0] == 0
    before = list(root.iterdir())
    event = {"hook_event_name": "SessionStart", "source": "startup", "session_id": "session", "cwd": str(tmp_path)}
    completed = subprocess.run(argv, input=json.dumps(event), text=True, capture_output=True, check=True)
    assert json.loads(completed.stdout) == {} and list(root.iterdir()) == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("stored_mode", [None, "on"])
@pytest.mark.skipif(os.name != "posix", reason="Native continuity uses POSIX private directories and locks")
def test_machine_on_reaches_real_guard_and_same_capture_readback_without_readmission(tmp_path, monkeypatch, provider, stored_mode):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_THREAD_ID", "machine-context-session")
    monkeypatch.delenv("CODEX_ROLLOUT_TRACE_ROOT", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    original_state = state.read_bytes()
    if stored_mode is not None:
        assert configure(registry, runtime, tmp_path, stored_mode)[0] == 0
    else:
        assert not (runtime / "machine/configuration.json").exists()
    code, setup = _run_cli(registry, runtime, "quota", "context-config", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, cwd=project)
    assert code == 0, setup
    hook = setup["native_config"]["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    event = {"session_id": "machine-context-session", "cwd": str(project), "hook_event_name": "SessionStart", "source": "startup"}
    subprocess.run(shlex.split(hook), input=json.dumps(event), text=True, cwd=project,
        capture_output=True, check=True)
    args = ["quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", "machine-original-turn"]
    code, wire = _run_cli(registry, runtime, *args, cwd=project, raw_guard=False)
    assert code == 0 and wire["mode"] == "full", wire
    recovery = read_context_read_reference(wire["recovery"]["reference_file"])[1]
    raw = Path(recovery["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == recovery["sha256"]
    assert json.loads(raw)["goal_id"] == GOAL_ID

    def complete_reader(view):
        view = read_context_read_reference(view["reference_file"])[1]["navigation"]
        # Native delivery advertises a file; only its reader pages are content.
        observed = bytearray()
        while True:
            code, page = _run_cli(registry, runtime, "quota", "context-read",
                "--capture-file", view["path"], "--capture-sha256", view["sha256"],
                "--offset", str(len(observed)), "--page-bytes", "3072", cwd=project)
            assert code == 0 and page["ok"], page
            assert page["offset"] == len(observed)
            observed.extend(page["content"].encode())
            subprocess.run(shlex.split(hook), input=json.dumps({**event,
                "hook_event_name": "PostToolUse", "tool_response": json.dumps(page)}),
                text=True, cwd=project, capture_output=True, check=True)
            if page["eof"]:
                break
            assert page["next_offset"] == len(observed)
        assert hashlib.sha256(observed).hexdigest() == view["sha256"]

    complete_reader(wire)
    code, warm = _run_cli(registry, runtime, *args, cwd=project, raw_guard=False)
    assert code == 0 and warm["mode"] == "delta", warm
    complete_reader(warm)
    old_generation = read_context_read_reference(
        warm["reference_file"]
    )[1]["navigation"]["target"].get("continuity_generation")
    configuration_file = runtime / "machine/configuration.json"
    old_configuration = configuration_file.read_bytes() if configuration_file.exists() else None
    assert (old_generation is None) is (stored_mode is None)
    assert (old_configuration is None) is (stored_mode is None)
    assert configure(registry, runtime, tmp_path, "off")[0] == 0
    assert configure(registry, runtime, tmp_path, "on")[0] == 0
    if old_configuration is not None:
        assert configuration_file.read_bytes() == old_configuration
    else:
        assert json.loads(configuration_file.read_text())["namespaces"]["context_delivery"]["mode"] == "on"
    code, fresh = _run_cli(registry, runtime, *args, cwd=project, raw_guard=False)
    assert code == 0 and fresh["mode"] == "full", fresh
    fresh = read_context_read_reference(fresh["reference_file"])[1]["navigation"]
    assert fresh["target"]["continuity_generation"] != old_generation
    code, page = _run_cli(registry, runtime, "quota", "context-read", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, cwd=project)
    latest_raw = Path(fresh["recovery"]["path"]).read_bytes()
    assert code == 0 and page["sha256"] == fresh["recovery"]["sha256"], page
    assert page["content"].encode() == latest_raw[:page.get("next_offset") or len(latest_raw)]
    assert Path(recovery["path"]).read_bytes() == raw
    assert _heartbeat_receipt_count(runtime, "machine-original-turn") == 1
    assert _spend_run_count(runtime) == 0 and state.read_bytes() == original_state
    # Explicit off wins over machine on, including old ordinary full output.
    code, full = _run_cli(registry, runtime, *args, "--context-delivery", "off", cwd=project)
    assert code == 0 and full["ok"] and full.get("schema_version") != "context_delivery_v1"
    assert _heartbeat_receipt_count(runtime, "machine-original-turn") == 1
    # Digest-bound recovery remains usable even with broken device configuration.
    (runtime / "machine/configuration.json").write_text("{broken")
    code, page = _run_cli(registry, runtime, "quota", "context-read", "--capture-file", recovery["path"],
        "--capture-sha256", recovery["sha256"], cwd=project)
    assert code == 0 and page["sha256"] == recovery["sha256"]
    code, full = _run_cli(registry, runtime, *args, "--context-delivery", "off", cwd=project)
    # Off bypasses this transport setting, not other owners' validation of a
    # broken whole-machine document. Recovery remains independent above.
    assert code != 0 and not full["ok"], full


@pytest.mark.skipif(os.name != "posix", reason="Native continuity uses POSIX private directories and locks")
def test_bootstrap_reloads_machine_setting_and_preserves_explicit_off(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime)
    original_registry = registry.read_bytes()
    args = ["heartbeat-prompt", "--thin", "--codex-app", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID]
    assert configure(registry, runtime, tmp_path, "on")[0] == 0
    code, enabled = _run_cli(registry, runtime, *args)
    assert code == 0 and "--context-delivery auto" in enabled["task_body"], enabled
    code, bootstrap = _run_cli(registry, runtime, *args, "--bootstrap")
    assert code == 0, bootstrap
    assert "--context-delivery" not in bootstrap["task_body"]
    assert str(runtime / "context-delivery") not in bootstrap["task_body"]
    code, pinned = _run_cli(registry, runtime, *args, "--bootstrap", "--context-delivery", "off")
    assert code == 0 and "--context-delivery off" in pinned["task_body"], pinned
    assert configure(registry, runtime, tmp_path, "off")[0] == 0
    code, disabled = _run_cli(registry, runtime, *args)
    assert code == 0 and "--context-delivery on" not in disabled["task_body"], disabled
    assert registry.read_bytes() == original_registry and _spend_run_count(runtime) == 0


@pytest.mark.parametrize("kind", ["symlink", "public"])
@pytest.mark.skipif(os.name != "posix", reason="Native continuity uses POSIX private directories and locks")
def test_machine_default_never_follows_or_chmods_an_unsafe_transport_root(tmp_path, kind):
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    assert configure(registry, runtime, tmp_path, "on")[0] == 0
    root = runtime / "context-delivery"
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    if kind == "symlink":
        root.symlink_to(target, target_is_directory=True)
    else:
        root.mkdir(mode=0o755)
        root.chmod(0o755)
    before = (target if kind == "symlink" else root).stat().st_mode
    code, result = _run_cli(registry, runtime, "quota", "context-config", "--codex-app",
        "--goal-id", "fixture", "--agent-id", "worker")
    assert code != 0 and not result["ok"], result
    assert (target if kind == "symlink" else root).stat().st_mode == before
    assert not list(target.iterdir()) and not registry.exists()


@pytest.mark.skipif(os.name != "posix", reason="Native continuity uses POSIX private directories and locks")
def test_explicit_on_first_use_uses_same_private_default_without_machine_setting(tmp_path):
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    code, result = _run_cli(registry, runtime, "quota", "context-config", "--context-delivery", "on",
        "--codex-app", "--goal-id", "fixture", "--agent-id", "worker")
    assert code == 0 and result["context_delivery"]["enabled"], result
    root = runtime / "context-delivery"
    assert root.stat().st_mode & 0o077 == 0
    hook = result["native_config"]["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    argv = shlex.split(hook)
    assert argv[argv.index("--codex-context-root") + 1] == str(root)
    assert argv[argv.index("--context-delivery") + 1] == "on"
    assert not registry.exists() and not (runtime / "machine/configuration.json").exists()
    assert not list(root.iterdir()), "setup does not manufacture retained-input evidence"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("native_id", [None, "uninitialized-native-session"])
@pytest.mark.skipif(os.name != "posix", reason="Native captured delivery uses POSIX private directories")
def test_default_guard_without_native_session_delivers_current_source_once(tmp_path, monkeypatch, provider, native_id):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    if native_id is not None:
        monkeypatch.setenv("CODEX_THREAD_ID", native_id)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    original = state.read_bytes()
    turn = "sessionless-original-turn"
    code, wire = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", turn, cwd=project, raw_guard=False)
    assert code == 0 and wire["mode"] == "full", wire
    assert "baseline_reference" not in wire
    code, page = _run_cli(registry, runtime, "quota", "context-read",
        "--reference-file", wire["recovery"]["reference_file"],
        "--select", "/goal_id", "--select", "/heartbeat_receipt",
        "--select", "/interaction_contract/cli_channel", "--page-bytes", "64000",
        "--max-page-output-bytes", "64000", cwd=project)
    assert code == 0 and page["ok"] and page["eof"], page
    values = {row["pointer"]: row["value"] for row in json.loads(page["content"])["selections"]}
    assert values["/goal_id"] == GOAL_ID
    assert values["/heartbeat_receipt"]["turn_instance_id"] == turn
    assert values["/interaction_contract/cli_channel"]["settlement_plan"]
    assert _heartbeat_receipt_count(runtime, turn) == 1 and _spend_run_count(runtime) == 0
    assert state.read_bytes() == original
    assert not list((runtime / "context-delivery").rglob("session.json"))


@pytest.mark.skipif(os.name != "posix", reason="Native captured delivery checks POSIX private directories")
def test_sessionless_fallback_does_not_hide_an_unsafe_context_root(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    project, runtime, registry = _write_fixture(tmp_path, provider="sqlite")
    unsafe = tmp_path / "unsafe-context"
    unsafe.mkdir()
    unsafe.chmod(0o755)
    turn = "unsafe-sessionless-turn"
    code, failure = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", turn, "--codex-context-root", str(unsafe), cwd=project, raw_guard=False)
    assert code == 1 and failure["error_code"] == "quota_decision_transport_failed", failure
    assert _heartbeat_receipt_count(runtime, turn) == 1 and _spend_run_count(runtime) == 0
    assert not list(unsafe.iterdir())


def test_explicit_on_unknown_host_fails_before_first_use_directory_creation(tmp_path):
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    code, result = _run_cli(registry, runtime, "quota", "context-config", "--context-delivery", "on",
        "--goal-id", "fixture", "--agent-id", "worker")
    assert code != 0 and not result["ok"]
    assert not (runtime / "context-delivery").exists() and not registry.exists()


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="Explicit native Codex; loopback inference only")
@pytest.mark.skipif(os.name != "posix", reason="Native benchmark worker qualification uses POSIX isolation")
@pytest.mark.parametrize("provider,worker_mode,machine_on,transport_ready", [
    ("file", "off", False, True), ("file", "off", True, True),
    ("sqlite", "off", False, True), ("sqlite", "off", True, True),
    ("file", None, False, True), ("sqlite", None, False, True),
    ("file", None, False, False), ("sqlite", None, False, False),
])
def test_native_worker_default_on_and_explicit_off_isolation(tmp_path, monkeypatch, provider, worker_mode, machine_on, transport_ready):
    from argparse import Namespace
    from benchmark.runtime.worker import run_once
    from scripts.external_scheduler_worker import build_should_run_command, parse_tick
    from tests.control_plane.codex_checkpoint_fixture import local_inference_provider, native_process_continuation
    from tests.control_plane.test_codex_context_boundary import _input_text, _native_fixture
    import sys

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    binary, env, workspace, home = _native_fixture(tmp_path)
    project, runtime, registry = _write_fixture(workspace)
    _initialize_fixture_authority(project, runtime, provider=provider)
    if machine_on:
        assert configure(registry, runtime, workspace, "on")[0] == 0
    skills = workspace / "skills"
    skills.mkdir()
    program = str(Path(sys.executable).with_name("loopx"))
    env.update(LOOPX_EXECUTION_MODE="heartbeat", LOOPX_TASK_ENTRY="seeded-todo",
        LOOPX_TASK_STAGE="execute", LOOPX_CODEX_HOME=str(home), LOOPX_PROJECT=str(project),
        LOOPX_SHARED_SKILLS=str(skills), LOOPX_REGISTRY=str(registry), LOOPX_RUNTIME_ROOT=str(runtime),
        LOOPX_GOAL_ID=GOAL_ID, LOOPX_AGENT_ID=AGENT_ID, LOOPX_WAKE_LOG_DIR=str(workspace / "wakes"),
        LOOPX_CLI=program, CODEX_BIN=binary, MODEL_NAME="fixture", REASONING_EFFORT="high",
        OPENAI_API_KEY="synthetic-loopback-only", LOOPX_CODEX_TURN_TIMEOUT_SEC="60", LOOPX_USAGE_PING="0")
    if worker_mode is not None:
        env["LOOPX_CONTEXT_DELIVERY"] = worker_mode
    if not transport_ready:
        def unavailable(*_args):
            raise ValueError("Fixture native hooks unavailable")
        monkeypatch.setattr("benchmark.runtime.context_delivery.prepare_context_delivery", unavailable)
    # The real outer scheduler observes before the native worker prepares its
    # session. It inherits the treatment, but must consume an inline decision.
    observer_env = {**env, "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "LOOPX_CONTEXT_DELIVERY": "off" if worker_mode == "off" or not transport_ready else "on"}
    observer = build_should_run_command(Namespace(cli_bin=program, registry=str(registry),
        runtime_root=str(runtime), goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_profile="generic_cli"))
    observed_guard = subprocess.run(observer, cwd=project, env=observer_env,
        capture_output=True, text=True, timeout=30, check=False)
    assert observed_guard.returncode == 0, observed_guard.stderr or observed_guard.stdout
    assert parse_tick(json.loads(observed_guard.stdout)).should_run
    observed, errors, process_continuations = [], [], []
    saved = workspace / "original-guard.json"

    def response(request, number, message):
        try:
            if continuation := native_process_continuation(request, f"context-default-{number}"):
                process_continuations.append(number)
                return continuation
            if number == 1:
                body = _input_text(request)
                command = next(line.strip() for line in body.splitlines() if " quota should-run " in line)
                turn_assignment = next(line.strip() for line in body.splitlines() if line.startswith("LOOPX_TURN="))
                observed.append(shlex.split(turn_assignment)[0].split("=", 1)[1])
                if worker_mode is None and transport_ready:
                    assert "--context-delivery on" in command
                if not transport_ready:
                    # An ordinary guard may omit presentation flags. The actual
                    # native tool environment must still retain runtime off.
                    words = shlex.split(command)
                    if "--context-delivery" in words:
                        index = words.index("--context-delivery")
                        # Keep the original shell quoting and $LOOPX_TURN
                        # expansion while omitting only this presentation flag.
                        command = command.replace("--context-delivery " + words[index + 1], "", 1)
                return {"type": "function_call", "id": "guard-call", "call_id": "guard-call",
                    "name": "exec_command", "arguments": json.dumps({
                        "cmd": turn_assignment + "\n" + command + " > " + shlex.quote(str(saved)),
                        "max_output_tokens": 1000})}
            decision = json.loads(saved.read_text())
            if worker_mode is None and transport_ready:
                assert decision["mode"] == "full", decision
                # Independent byte readback checks the current capture. This
                # is not evidence that the model retained its whole input.
                command = decision["recovery"]["command"]
                content = ""
                while command:
                    page = json.loads(subprocess.check_output(shlex.split(command), text=True, cwd=project))
                    content += page["content"]
                    if page["eof"]:
                        break
                    command = page["next_command"]
                decision = json.loads(content)
            assert decision.get("ok") is True and decision.get("should_run") is True, json.dumps({
                key: decision.get(key) for key in ("ok", "should_run", "error", "error_code", "error_detail", "reason")
            })
            assert decision["goal_id"] == GOAL_ID
            assert decision["heartbeat_receipt"]["turn_instance_id"] == observed[0]
            return message
        except Exception as error:
            errors.append(error)
            return message

    original = dict(env)
    with local_inference_provider(response) as (url, _, requests):
        env["OPENAI_BASE_URL"] = url
        original["OPENAI_BASE_URL"] = url
        result = run_once(env)
    assert result["ok"], result
    assert not errors and len(requests) == 2 + len(process_continuations), errors
    assert env == original, "Resolved worker defaults must not mutate caller/frozen input"
    assert _heartbeat_receipt_count(runtime, observed[0]) == 1 and _spend_run_count(runtime) == 0
    if worker_mode == "off" or not transport_ready:
        if worker_mode == "off":
            assert "context_transport" not in result
        else:
            assert result["context_transport"]["available"] is False
        assert not (runtime / "context-delivery").exists() and not (home / "loopx-context").exists()
        assert not (home / "hooks.json").exists()
    else:
        assert result["context_delivery"] == "on" and result["context_transport"]["available"]
        assert (home / "hooks.json").is_file()
