from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from examples.control_plane.quota_plan_fixtures import (
    SCOPED_AGENT_ID,
    write_cli_fixture,
    write_codex_heartbeat_fixture,
)
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.coordination.coordination_state_contract import (
    TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
    TODO_DOMAIN_RECORD_FIELDS,
)
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_unix_launcher_process_replaces_python_for_exact_native_followup(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_node = fake_bin / "node"
    fake_node.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    fake_node.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        "LOOPX_PYTHON": str(tmp_path / "python-must-not-run"),
    }
    args = [
        "--format",
        "json",
        "--runtime-root",
        str(tmp_path / "runtime"),
        "quota",
        "scheduler-ack-current",
        "--goal-id",
        "goal-native-launcher",
        "--agent-id",
        "agent-native-launcher",
        "--scheduler-host-facts-chunk",
        "facts",
        "--turn-instance-id",
        "turn-native-launcher",
        "--execute",
    ]

    completed = subprocess.run(
        [str(REPO_ROOT / "scripts" / "loopx"), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    assert lines[:3] == [
        "--no-warnings",
        "--experimental-strip-types",
        str(
            REPO_ROOT
            / "loopx"
            / "control_plane"
            / "scheduler"
            / "heartbeat_followup_cli.ts"
        ),
    ]
    assert lines[-len(args) :] == args


def test_unix_launcher_keeps_unbound_calls_on_the_python_compatibility_route(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "loopx"),
            "quota",
            "scheduler-ack-current",
            "--scheduler-host-facts-chunk",
            "facts",
            "--execute",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "LOOPX_PYTHON": str(tmp_path / "missing-python")},
    )

    assert completed.returncode == 2
    assert "configured Python executable not found" in completed.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
@pytest.mark.parametrize("launcher", ["unix", "powershell"])
def test_receipt_bound_trae_hint_executes_native_without_python(
    tmp_path: Path, launcher: str,
) -> None:
    registry_path, runtime_root, project = write_cli_fixture(
        tmp_path / "fixture",
        scoped_agents=True,
    )
    goal_id = "needs-operator"
    turn_id = "turn-native-transaction"
    returncode, guard = run_json_cli_result(
        "quota",
        "should-run",
        "--goal-id",
        goal_id,
        "--agent-id",
        SCOPED_AGENT_ID,
        "--trae_app",
        "--app-automation-current-rrule",
        "FREQ=MINUTELY;INTERVAL=3",
        "--turn-instance-id",
        turn_id,
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=project,
    )
    assert returncode == 0, guard
    hint = guard["scheduler_hint"]["app_automation"]["ack_hint"]
    assert "--scheduler-host-facts-chunk" in hint["cli_args"]
    assert hint["args"]["turn_instance_id"] == turn_id

    entry, env = _launcher(tmp_path, launcher, python=str(tmp_path / "python-must-not-run"))
    completed = subprocess.run(
        [
            *entry,
            "--format",
            "json",
            *hint["cli_args"],
            "--app-automation-current-rrule", "FREQ=MINUTELY;INTERVAL=30",
        ],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["scheduler_commit"]["status"] == "written"
    assert (
        result["scheduler_ack_event"]["scheduler_state"]["last_applied_rrule"]
        == guard["scheduler_hint"]["app_automation"]["recommended_rrule"]
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
@pytest.mark.parametrize("launcher", ["unix", "powershell"])
@pytest.mark.parametrize("node_layout", ["single", "multiple"])
def test_receipt_bound_failure_hint_executes_native_without_python(
    tmp_path: Path, monkeypatch, launcher: str, node_layout: str,
) -> None:
    if node_layout == "multiple":
        real_node = Path(shutil.which("node"))
        later_bin = tmp_path / "later Node candidate"
        later_bin.mkdir()
        later_node = later_bin / real_node.name
        later_node.write_text("This later PATH candidate must not execute.\n", encoding="utf-8")
        later_node.chmod(0o755)
        monkeypatch.setenv("PATH", os.pathsep.join([
            str(real_node.parent), str(later_bin), os.environ.get("PATH", ""),
        ]))
    registry_path, runtime_root, project = write_cli_fixture(
        tmp_path / "fixture-failure",
        scoped_agents=True,
    )
    turn_id = "turn-native-failure"
    returncode, guard = run_json_cli_result(
        "quota",
        "should-run",
        "--goal-id",
        "needs-operator",
        "--agent-id",
        SCOPED_AGENT_ID,
        "--codex-app",
        "--turn-instance-id",
        turn_id,
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=project,
    )
    assert returncode == 0, guard
    hint = guard["scheduler_hint"]["codex_app"]["failure_hint"]
    assert "--scheduler-host-facts-chunk" in hint["cli_args"]

    entry, env = _launcher(tmp_path, launcher, python=str(tmp_path / "python-must-not-run"))
    completed = subprocess.run(
        [
            *entry,
            "--format",
            "json",
            *hint["cli_args"],
        ],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["mode"] == "scheduler-fail-current"
    assert result["scheduler_commit"]["status"] == "written"
    assert result["failure_count"] == 1


def test_windows_launcher_contains_the_same_exact_native_dispatch_gate() -> None:
    source = (REPO_ROOT / "scripts" / "loopx.ps1").read_text(encoding="utf-8")

    assert '"--scheduler-host-facts-chunk"' in source
    assert '"--turn-instance-id"' in source
    assert "heartbeat_followup_cli.ts" in source
    assert "Get-Command node" in source


def _launcher(
    tmp_path: Path, launcher: str, *, python: str = sys.executable,
) -> tuple[list[str], dict[str, str]]:
    env = {**os.environ, "LOOPX_PYTHON": python}
    if launcher == "unix":
        return [str(REPO_ROOT / "scripts/loopx")], env
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is required for its actual launcher")
    pointer = tmp_path / "current-release.json"
    pointer.write_text(json.dumps({"release_root": str(REPO_ROOT), "python": python}), encoding="utf-8")
    env["LOOPX_CURRENT_RELEASE_FILE"] = str(pointer)
    env["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
    return [pwsh, "-NoLogo", "-NoProfile", "-File", str(REPO_ROOT / "scripts/loopx.ps1")], env


@pytest.mark.parametrize("launcher", ["unix", "powershell"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("host", ["matches", "mismatch", "missing", "ambiguous"])
def test_launcher_binds_ack_to_current_host_readback(
    tmp_path: Path, monkeypatch, provider: str, host: str, launcher: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, project = write_cli_fixture(tmp_path / "fixture", scoped_agents=True)
    goal = "needs-operator"
    state = project / f".codex/goals/{goal}/ACTIVE_GOAL_STATE.md"
    projection = build_todo_runtime_shadow_projection(
        goal_id=goal, todos=[], handoff_mode="soft_claim",
    )
    projection["todo_read_model"] = {
        **projection["todo_read_model"],
        "schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
        "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS),
    }
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=provider)
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_THREAD_ID", "scheduler-fixture")
    write_codex_heartbeat_fixture(
        codex_home, goal_id=goal, agent_id=SCOPED_AGENT_ID, rrule="FREQ=MINUTELY;INTERVAL=3",
    )
    _, guard = run_json_cli_result(
        "quota", "should-run", "--goal-id", goal, "--agent-id", SCOPED_AGENT_ID,
        "--codex-app", "--turn-instance-id", "current-host-launcher",
        registry_path=registry, runtime_root=runtime, cwd=project,
    )
    app = guard["scheduler_hint"]["app_automation"]
    assert app["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=30"
    manifest = codex_home / "automations/scheduler-fixture/automation.toml"
    if host == "missing":
        manifest.unlink()
    else:
        write_codex_heartbeat_fixture(
            codex_home, goal_id=goal, agent_id=SCOPED_AGENT_ID,
            rrule="FREQ=MINUTELY;INTERVAL=30" if host == "matches" else "FREQ=MINUTELY;INTERVAL=60",
        )
        if host == "ambiguous":
            duplicate = codex_home / "automations/duplicate/automation.toml"
            duplicate.parent.mkdir()
            shutil.copyfile(manifest, duplicate)

    command, env = _launcher(tmp_path, launcher)
    command += ["--format", "json", *app["ack_hint"]["cli_args"]]
    result = subprocess.run(command, cwd=project, env=env, text=True, capture_output=True, timeout=45)
    payload = json.loads(result.stdout)
    commit = payload["scheduler_commit"]
    if host != "matches":
        assert result.returncode == 1, result.stdout + result.stderr
        assert payload["ok"] is False
        assert commit["reason_code"] == "rrule_mismatch"
        assert commit["written"] is False
        assert not Path(commit["path"]).exists()
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert commit["status"] == "written"
    assert payload["scheduler_ack_event"]["scheduler_state"]["last_applied_rrule"] == "FREQ=MINUTELY;INTERVAL=30"
    replay = subprocess.run(command, cwd=project, env=env, text=True, capture_output=True, timeout=45)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert json.loads(replay.stdout)["scheduler_commit"]["status"] == "replayed"
    _, stable = run_json_cli_result(
        "quota", "should-run", "--goal-id", goal, "--agent-id", SCOPED_AGENT_ID,
        "--codex-app", "--turn-instance-id", "current-host-launcher-stable",
        registry_path=registry, runtime_root=runtime, cwd=project,
    )
    assert stable["quota"]["spent_slots"] == 0
    backoff = stable["scheduler_hint"]["app_automation"]["stateful_backoff"]
    assert backoff["apply_needed"] is False
    assert backoff["ack_needed"] is False


@pytest.mark.parametrize("launcher", ["unix", "powershell"])
@pytest.mark.parametrize("command", ["scheduler-ack-current", "scheduler-fail-current"])
def test_launcher_preserves_outer_controller_write_guard(
    tmp_path: Path, command: str, launcher: str,
) -> None:
    registry, runtime, project = write_cli_fixture(tmp_path / "fixture", scoped_agents=True)
    codex_home = tmp_path / "codex-home"
    write_codex_heartbeat_fixture(
        codex_home, goal_id="needs-operator", agent_id=SCOPED_AGENT_ID,
        rrule="FREQ=MINUTELY;INTERVAL=60",
    )
    entry, env = _launcher(tmp_path, launcher)
    env.update(CODEX_HOME=str(codex_home), CODEX_THREAD_ID="scheduler-fixture")
    guard_result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
         "--runtime-root", str(runtime), "--format", "json", "quota", "should-run",
         "--goal-id", "needs-operator", "--agent-id", SCOPED_AGENT_ID,
         "--codex-app", "--turn-instance-id", "outer-controller-launcher"],
        cwd=project, env=env, text=True, capture_output=True, timeout=45,
    )
    assert guard_result.returncode == 0, guard_result.stdout + guard_result.stderr
    app = json.loads(guard_result.stdout)["scheduler_hint"]["app_automation"]
    hint = app["ack_hint" if command == "scheduler-ack-current" else "failure_hint"]
    arguments = ["--format", "json", *hint["cli_args"]]
    if command == "scheduler-ack-current":
        write_codex_heartbeat_fixture(
            codex_home, goal_id="needs-operator", agent_id=SCOPED_AGENT_ID,
            rrule="FREQ=MINUTELY;INTERVAL=30",
        )
        arguments += ["--app-automation-current-rrule", "FREQ=MINUTELY;INTERVAL=30"]
    result = subprocess.run(
        [*entry, *arguments], cwd=project,
        env={**env,
             "LOOPX_KUNLUNCODE_OUTER_CONTROLLER": "1",
             "LOOPX_KUNLUNCODE_OUTER_GOAL_ID": "needs-operator",
             "LOOPX_KUNLUNCODE_OUTER_AGENT_ID": SCOPED_AGENT_ID},
        text=True, capture_output=True, timeout=45,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert json.loads(result.stderr)["error_code"] == "kunluncode_outer_controller_write_owned"
    assert not list(runtime.glob("goals/*/scheduler-state/**/*.json"))
