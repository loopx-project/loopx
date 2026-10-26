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

from loopx.capabilities.machine_configuration.context_delivery import CONTEXT_DELIVERY_MACHINE_SCHEMA
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _heartbeat_receipt_count, _initialize_fixture_authority,
    _run_cli, _spend_run_count, _write_fixture,
)


def configure(registry, runtime, root, mode):
    config = root / "context-setting.json"
    config.write_text(json.dumps({"schema_version": CONTEXT_DELIVERY_MACHINE_SCHEMA, "mode": mode}))
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
    assert contexts[0]["schema_versions"] == [CONTEXT_DELIVERY_MACHINE_SCHEMA]
    assert contexts[0]["configuration_template"] == {
        "schema_version": CONTEXT_DELIVERY_MACHINE_SCHEMA, "mode": "off",
    }
    assert not registry.exists() and not (runtime / "machine/configuration.json").exists()
    assert not (runtime / "context-delivery").exists()


@pytest.mark.parametrize("invalid", [True, False, None, 1, "auto", "codex_app_heartbeat"])
def test_invalid_machine_mode_never_writes(tmp_path, invalid):
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    code, result = configure(registry, runtime, tmp_path, invalid)
    assert code != 0 and not result["ok"]
    assert not registry.exists() and not (runtime / "machine/configuration.json").exists()


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
def test_inherited_on_rejects_unknown_or_unsupported_before_admission(tmp_path, monkeypatch, host):
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    assert configure(registry, runtime, tmp_path, "on")[0] == 0
    code, result = _run_cli(registry, runtime, "quota", "context-config", *host,
        "--goal-id", "fixture", "--agent-id", "worker")
    assert code != 0 and not result["ok"], result
    assert not (runtime / "context-delivery").exists() and not registry.exists()
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("profile", ["codex_app", "benchmark", "codex_app_ssh_goal"])
@pytest.mark.skipif(os.name != "posix", reason="Native continuity uses POSIX private directories and locks")
def test_native_configuration_inherits_declared_scenarios_and_later_off(tmp_path, monkeypatch, profile):
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    assert configure(registry, runtime, tmp_path, "on")[0] == 0
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
@pytest.mark.skipif(os.name != "posix", reason="Native continuity uses POSIX private directories and locks")
def test_machine_on_reaches_real_guard_and_same_capture_readback_without_readmission(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_THREAD_ID", "machine-context-session")
    monkeypatch.delenv("CODEX_ROLLOUT_TRACE_ROOT", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    original_state = state.read_bytes()
    assert configure(registry, runtime, tmp_path, "on")[0] == 0
    code, setup = _run_cli(registry, runtime, "quota", "context-config", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, cwd=project)
    assert code == 0, setup
    hook = setup["native_config"]["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    event = {"session_id": "machine-context-session", "cwd": str(project), "hook_event_name": "SessionStart", "source": "startup"}
    subprocess.run(shlex.split(hook), input=json.dumps(event), text=True, cwd=project,
        capture_output=True, check=True)
    args = ["quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", "machine-original-turn"]
    code, wire = _run_cli(registry, runtime, *args, cwd=project)
    assert code == 0 and wire["mode"] == "full", wire
    recovery = wire["recovery"]
    raw = Path(recovery["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == recovery["sha256"]
    assert json.loads(raw)["goal_id"] == GOAL_ID

    def complete_reader(view):
        page = view
        while True:
            subprocess.run(shlex.split(hook), input=json.dumps({**event,
                "hook_event_name": "PostToolUse", "tool_response": json.dumps(page)}),
                text=True, cwd=project, capture_output=True, check=True)
            if page["eof"]:
                break
            code, page = _run_cli(registry, runtime, "quota", "context-read",
                "--capture-file", view["path"], "--capture-sha256", view["sha256"],
                "--offset", str(page["next_offset"]), "--page-bytes", "3072", cwd=project)
            assert code == 0 and page["ok"], page

    complete_reader(wire)
    code, warm = _run_cli(registry, runtime, *args, cwd=project)
    assert code == 0 and warm["mode"] == "delta", warm
    complete_reader(warm)
    old_generation = warm["target"]["continuity_generation"]
    old_configuration = (runtime / "machine/configuration.json").read_bytes()
    assert configure(registry, runtime, tmp_path, "off")[0] == 0
    assert configure(registry, runtime, tmp_path, "on")[0] == 0
    assert (runtime / "machine/configuration.json").read_bytes() == old_configuration
    code, fresh = _run_cli(registry, runtime, *args, cwd=project)
    assert code == 0 and fresh["mode"] == "full", fresh
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
    assert code == 0 and "--context-delivery on" in enabled["task_body"], enabled
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


def test_explicit_on_unknown_host_fails_before_first_use_directory_creation(tmp_path):
    registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
    code, result = _run_cli(registry, runtime, "quota", "context-config", "--context-delivery", "on",
        "--goal-id", "fixture", "--agent-id", "worker")
    assert code != 0 and not result["ok"]
    assert not (runtime / "context-delivery").exists() and not registry.exists()


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="Explicit native Codex; loopback inference only")
@pytest.mark.skipif(os.name != "posix", reason="Native benchmark worker qualification uses POSIX isolation")
@pytest.mark.parametrize("provider,worker_mode,machine_on", [
    ("file", None, False), ("file", None, True),
    ("sqlite", None, False), ("sqlite", None, True), ("sqlite", "off", True),
])
def test_native_worker_default_off_isolates_machine_on(tmp_path, monkeypatch, provider, worker_mode, machine_on):
    from benchmark.runtime.worker import run_once
    from tests.control_plane.codex_checkpoint_fixture import local_inference_provider
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
    observed, errors = [], []
    saved = workspace / "original-guard.json"

    def response(request, number, message):
        try:
            if number == 1:
                body = _input_text(request)
                assert "context-project" not in body
                command = next(line.strip() for line in body.splitlines() if " quota should-run " in line)
                turn_assignment = next(line.strip() for line in body.splitlines() if line.startswith("LOOPX_TURN="))
                observed.append(shlex.split(turn_assignment)[0].split("=", 1)[1])
                return {"type": "function_call", "id": "guard-call", "call_id": "guard-call",
                    "name": "exec_command", "arguments": json.dumps({
                        "cmd": turn_assignment + "\n" + command + " > " + shlex.quote(str(saved)),
                        "max_output_tokens": 1000})}
            decision = json.loads(saved.read_text())
            assert decision["ok"] and decision["should_run"], decision
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
    assert not errors and len(requests) == 2, errors
    assert env == original, "Resolved worker defaults must not mutate caller/frozen input"
    assert _heartbeat_receipt_count(runtime, observed[0]) == 1 and _spend_run_count(runtime) == 0
    assert "context_transport" not in result
    assert not (runtime / "context-delivery").exists() and not (home / "loopx-context").exists()
    assert not (home / "hooks.json").exists()
