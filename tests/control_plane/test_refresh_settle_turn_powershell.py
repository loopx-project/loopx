import json
import os
import shlex
import shutil
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, REPO_ROOT, TODO_ID, TURN_ID, _initialize_fixture_authority,
    _run_cli, _spend_run_count, _write_fixture,
)

@pytest.mark.skipif(os.name != "nt", reason="Exercises the native Windows PowerShell launcher")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_legacy_posix_quoted_powershell_launcher_fails_before_settlement(
    tmp_path, monkeypatch, provider,
):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell is not None
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    marker = tmp_path / "launcher-started.txt"
    launcher = tmp_path / "candidate's launcher" / "loopx.ps1"
    launcher.parent.mkdir()
    python = sys.executable.replace("'", "''")
    marker_arg = str(marker).replace("'", "''")
    launcher.write_text(
        f"Set-Content -LiteralPath '{marker_arg}' -Value started\n"
        f"& '{python}' -m loopx.entrypoint @args\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding)
    assert rc == 0, guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    prefix = ["-File", str(launcher), "--cli-bin", str(launcher), "--format", "json",
              "--registry", str(registry), "--runtime-root", str(runtime)]
    refresh = ["refresh-state", *binding, "--classification", "verified",
               "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
               "--delivery-boundary", "in_flight_continuation", "--no-global-sync",
               "--suppress-external-sinks"]
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    prepared = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", *prefix, *refresh], cwd=project,
        env=environment, capture_output=True, timeout=60,
    )
    assert prepared.returncode == 0, (prepared.stdout, prepared.stderr)
    owed = json.loads(prepared.stdout)["settlement_owed"]
    assert marker.exists() and _spend_run_count(runtime) == 0
    marker.unlink()

    # Immutable baseline oracle: this is the exact POSIX rendering used by the
    # parent commit before host-aware PowerShell rendering was introduced.
    legacy_command = shlex.join(owed["command_argv"])
    legacy = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", legacy_command],
        cwd=project, env=environment, capture_output=True, timeout=60,
    )
    assert legacy.returncode != 0, (legacy.stdout, legacy.stderr)
    assert not marker.exists() and _spend_run_count(runtime) == 0

    repaired = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", owed["command"]],
        cwd=project, env=environment, capture_output=True, timeout=60,
    )
    assert repaired.returncode == 0, (repaired.stdout, repaired.stderr)
    result = json.loads(repaired.stdout)
    assert marker.exists()
    assert result["settlement_progress"]["state"] == "settled"
    assert result["settlement_identity"] == identity
    assert _spend_run_count(runtime) == 1


@pytest.mark.skipif(os.name != "nt", reason="Exercises the native Windows PowerShell launcher")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("composed", [False, True])
@pytest.mark.parametrize("relative_launcher", [False, True], ids=["quoted-launcher", "relative-launcher"])
def test_pinned_powershell_launcher_settles_original_turn_once(
    tmp_path, monkeypatch, provider, composed, relative_launcher,
):
    powershell = shutil.which("pwsh")
    assert powershell is not None
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    if relative_launcher:
        # A later argument needs quoting even though the executable does not.
        registry = registry.rename(registry.with_name("operator's O\u2019Neil registry.json"))
        launcher_arg = "./bin/loopx.ps1"
        launcher = project / launcher_arg
    else:
        launcher = tmp_path / "candidate's O\u2019Neil launcher" / "loopx.ps1"
        launcher_arg = str(launcher)
    _initialize_fixture_authority(project, runtime, provider=provider)
    launcher.parent.mkdir()
    shutil.copyfile(REPO_ROOT / "scripts/loopx.ps1", launcher)
    pointer = tmp_path / "current-release.json"
    pointer.write_text(
        json.dumps({"release_root": str(REPO_ROOT), "python": sys.executable}), encoding="utf-8",
    )
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *binding)
    assert rc == 0, guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    prefix = ["-File", launcher_arg, "--cli-bin", launcher_arg, "--format", "json",
              "--registry", str(registry), "--runtime-root", str(runtime)]
    refresh = ["refresh-state", *binding, "--classification", "verified", "--delivery-batch-scale", "implementation",
               "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
               "--no-global-sync", "--suppress-external-sinks"]

    def run(args):
        process = subprocess.run([powershell, "-NoProfile", "-NonInteractive", *args], cwd=project,
            env={**os.environ, "LOOPX_CURRENT_RELEASE_FILE": str(pointer)}, capture_output=True, timeout=60)
        assert process.returncode == 0, (process.stdout, process.stderr)
        result = json.loads(process.stdout)
        assert result["ok"] and result["settlement_identity"] == identity
        return result

    if composed:
        action = [*prefix, *refresh, "--settle-turn"]
    else:
        owed = run([*prefix, *refresh])["settlement_owed"]
        assert _spend_run_count(runtime) == 0
        action = ["-Command", owed["command"]]
    first = run(action)
    assert first["settlement_progress"]["state"] == "settled"
    assert _spend_run_count(runtime) == 1
    if not composed:
        argv = owed["command_argv"]
        assert argv[:3] == [launcher_arg, "--cli-bin", launcher_arg]
        assert argv[argv.index("--registry") + 1] == str(registry)
    replay = run(action)
    assert replay["idempotent_replay"]
    assert _spend_run_count(runtime) == 1
