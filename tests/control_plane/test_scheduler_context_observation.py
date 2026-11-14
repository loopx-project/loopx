"""A shell scheduler observes admission; the admitted native wake owns context."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from scripts import external_scheduler_worker as worker
from loopx.control_plane.goals.activation_service import set_goal_activation_state
from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture, isolate_sqlite_runtime
from tests.control_plane.test_dated_vision_wait_cli import AGENT as WAIT_AGENT, GOAL as WAIT_GOAL, fixture as wait_fixture
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _configure_read_only_todo, _heartbeat_receipt_count,
    _initialize_fixture_authority, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("native_context", ["on", "off"])
@pytest.mark.skipif(os.name != "posix", reason="Exercises the POSIX shell scheduler and private native files")
def test_scheduler_observation_preserves_native_wake_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, native_context: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", native_context)
    monkeypatch.setenv("LOOPX_EXECUTION_MODE", "heartbeat")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "native-home"))
    repository = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("PYTHONPATH", str(repository))
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    cli = tmp_path / "loopx-cli"
    cli.write_text(f"#!{sys.executable}\nfrom loopx.entrypoint import main\nraise SystemExit(main())\n")
    cli.chmod(0o700)
    args = argparse.Namespace(cli_bin=str(cli), registry=str(registry),
        runtime_root=str(runtime), goal_id=GOAL_ID, agent_id=AGENT_ID,
        runtime_profile="generic_cli")

    # This process has no native Turn/session. Its current decision must remain
    # directly consumable by the scheduler even when its future wake uses on.
    original_environment = dict(os.environ)
    command = worker.build_should_run_command(args)
    assert command[-1] == "--scheduler-observation"
    assert "--context-delivery" not in command
    decision = worker.run_quota_should_run(command, timeout_seconds=30)
    assert decision["ok"] and worker.parse_tick(decision).should_run
    assert decision["selected_todo"] == {"todo_id": TODO_ID}
    assert set(decision["interaction_contract"]) == {"agent_channel"}
    assert not {"goal_boundary", "replan_action_packet", "reference_file"}.intersection(decision)
    legacy = worker.run_quota_should_run(
        [*command[:-1], "--include-detail", "scheduler", "--context-delivery", "off"],
        timeout_seconds=30,
    )
    assert worker.parse_tick(decision) == worker.parse_tick(legacy)
    assert worker._admission_scope(decision, args) == worker._admission_scope(legacy, args)
    assert len(json.dumps(decision).encode()) < len(json.dumps(legacy).encode())
    assert not (runtime / "context-delivery").exists()
    assert dict(os.environ) == original_environment
    assert _spend_run_count(runtime) == 0
    for extra in (
        ("--turn-instance-id", "observer-must-not-admit"),
        ("--begin-turn",),
        ("--decision-output-root", str(tmp_path)),
    ):
        rejected = subprocess.run([*command, *extra], cwd=project,
            capture_output=True, text=True, timeout=30, check=False)
        assert rejected.returncode != 0
        failure = json.loads(rejected.stdout)
        assert failure["ok"] is False
        assert "--scheduler-observation" in failure["reason"]
    for extra in (
        ("--capture-file", str(tmp_path / "capture.json")),
        ("--initial-read",),
        ("--list",),
        ("--list", "--list-depth", "2"),
        ("--select", "/interaction_contract"),
        ("--reference-file", str(tmp_path / "reader.json")),
    ):
        rejected = subprocess.run([*command, *extra], cwd=project,
            capture_output=True, text=True, timeout=30, check=False)
        assert rejected.returncode != 0
        failure = json.loads(rejected.stdout)
        assert failure["ok"] is False
        assert "selected_todo" not in failure
    assert _heartbeat_receipt_count(runtime, "observer-must-not-admit") == 0
    assert not list(tmp_path.glob("decision-*"))

    # The native wake still captures its own exact Turn. Observation
    # must never become a global opt-out or mutate the child environment.
    turn = "native-wake-after-observation"
    command = [str(cli), "--format", "json", "--registry", str(registry),
        "--runtime-root", str(runtime), "quota", "should-run", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--runtime-profile", "generic_cli", "--complete-turn", "--todo-id", TODO_ID,
        "--turn-instance-id", turn, "--context-delivery", native_context]
    if native_context == "on":
        from loopx.control_plane.turn_driver.codex_context import codex_context_hook

        (tmp_path / "captures").mkdir(mode=0o700)
        (tmp_path / "context").mkdir(mode=0o700)
        codex_context_hook(str(tmp_path / "context"), {"hook_event_name": "SessionStart",
            "source": "startup", "session_id": "native-context-wake", "cwd": str(project)})
        command += ["--decision-output-root", str(tmp_path / "captures"),
            "--codex-context-dir", str(tmp_path / "context")]
    def run(argv):
        return subprocess.run(argv, cwd=project, capture_output=True, text=True, timeout=60, check=True)

    wake_output = tmp_path / "wake.json"
    dispatches = tmp_path / "dispatches"
    worker_cli = [sys.executable, str(repository / "scripts" / "external_scheduler_worker.py"),
        "--cli-bin", str(cli), "--registry", str(registry), "--runtime-root", str(runtime),
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--once", "--state-file", str(tmp_path / "worker-state.json"),
        "--wake-cmd", f"printf '.\\n' >> {shlex.quote(str(dispatches))} && "
                    f"{shlex.join(command)} > {shlex.quote(str(wake_output))}"]
    assert "status=should_run wake_ok" in run(worker_cli).stdout
    payload = json.loads(wake_output.read_bytes())
    assert payload["ok"], json.dumps({k: payload.get(k) for k in ("reason", "error_code", "state")})
    if native_context == "on":
        assert payload["schema_version"] == "context_capture_navigation_v1"
        assert Path(payload["reference_file"]).is_file()
        reader = payload["recovery"]["command"]
        full = b""
        while reader:
            page = json.loads(run(shlex.split(reader)).stdout)
            assert page["ok"] and page["offset"] == len(full)
            full += page["content"].encode()
            reader = page.get("next_command")
        assert page["eof"] and len(full) == page["total_bytes"]
        payload = json.loads(full)
    else:
        assert not (runtime / "context-delivery").exists()
    assert payload["schema_version"] == "loopx_turn_envelope_v1"
    assert payload["selected_todo"]["todo_id"] == TODO_ID
    assert _heartbeat_receipt_count(runtime, turn) == 1
    assert _spend_run_count(runtime) == 0
    plan = payload["interaction_contract"]["cli_channel"]["settlement_plan"]
    identity = payload["heartbeat_receipt"]["settlement_identity"]
    assert plan["identity"] == identity and identity["turn_instance_id"] == turn
    writeback = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
    fill = {"<validated_progress>": "verified_native_wake", "<scale>": "implementation",
            "<outcome>": "outcome_progress", "<boundary>": "in_flight_continuation"}
    settle = [str(cli), *(fill.get(arg, arg) for arg in shlex.split(writeback["command_template"])[1:])]
    settle += ["--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks"]
    first, replay = [json.loads(run(settle).stdout) for _ in range(2)]
    assert first["settlement_progress"]["state"] == "settled"
    assert replay["idempotent_replay"] and replay["settlement_identity"] == first["settlement_identity"] == identity
    assert _spend_run_count(runtime) == 1

    stopped = set_goal_activation_state(registry_path=registry, goal_id=GOAL_ID, state="stopped",
                                       reason="Stop the shell worker fixture.", actor_kind="owner", execute=True)
    assert stopped["readback"]["verified"]
    assert "status=terminal" in run(worker_cli).stdout
    assert dispatches.read_text().splitlines() == ["."]
    assert _heartbeat_receipt_count(runtime, turn) == 1
    assert _spend_run_count(runtime) == 1


_VISIBLE_HOST_PROBE = """
import {execFile} from "node:child_process";
import {pathToFileURL} from "node:url";
import {join} from "node:path";
import {promisify} from "node:util";

const [root, cli, registryPath, directory, goalId, agentId, capability] = process.argv.slice(1);
const host = path => import(pathToFileURL(join(root, "loopx", path)).href);
const pi = await host("pi_goal_mode/pi-goal-loop-runtime.mjs");
const openCode = await host("opencode_goal_mode/goal-bridge-runtime.mjs");
const openCode2 = await host("opencode2_goal_mode/opencode2-goal-worker.mjs");
const binding = {registryPath, directory, goalId, agentId,
  availableCapabilities: capability ? [capability] : []};
const exec = promisify(execFile);
process.env.LOOPX_BIN = cli;
console.log(JSON.stringify({
  pi: JSON.parse((await exec(cli, pi.buildQuotaArgs(binding), {cwd: directory})).stdout),
  openCode: await openCode.probeLoopxQuota(binding, {directory}),
  openCode2: await openCode2.createQuotaProbe({bin: cli})(binding, {directory}),
}));
"""


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.skipif(os.name != "posix", reason="Exercises the CLI through a POSIX launcher")
def test_visible_hosts_keep_runnable_wait_and_stop_facts(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    repository = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("PYTHONPATH", str(repository))
    cli = tmp_path / "loopx-cli"
    cli.write_text(f"#!{sys.executable}\nfrom loopx.entrypoint import main\nraise SystemExit(main())\n")
    cli.chmod(0o700)

    def probe(registry, project, goal, agent, capability=""):
        result = subprocess.run(
            ["node", "--input-type=module", "-e", _VISIBLE_HOST_PROBE,
             str(repository), str(cli), str(registry), str(project), goal, agent, capability],
            cwd=project, capture_output=True, text=True, check=True, timeout=90,
        )
        views = json.loads(result.stdout)
        for view in views.values():
            assert view["goal_id"] == goal
            assert not {"reference_file", "goal_boundary", "heartbeat_receipt"}.intersection(view)
        return views

    project, runtime, registry = _write_fixture(tmp_path / "runnable", required_capability="network", provider=provider)
    views = probe(registry, project, GOAL_ID, AGENT_ID, "network")
    for view in views.values():
        assert view["should_run"] is True
        assert view["selected_todo"] == {"todo_id": TODO_ID}
        assert view["scheduler_hint"]["action"] == "run_now"
    assert "host_poll_receipt" not in views["pi"]
    assert [views[host]["host_poll_receipt"]["poll_count"] for host in ("openCode", "openCode2")] == [1, 2]

    wait_registry, wait_runtime, wait_state = wait_fixture(tmp_path / "wait")
    initialize_canonical_source_fixture(wait_runtime, WAIT_GOAL, state_path=wait_state, provider=provider)
    waiting = probe(wait_registry, wait_state.parents[3], WAIT_GOAL, WAIT_AGENT)
    for view in waiting.values():
        assert view["should_run"] is False
        assert view["interaction_contract"]["agent_channel"]["must_attempt"] is False
        hint = view["scheduler_hint"]
        assert hint["action"] == "backoff_until_state_change"
        assert hint["cold_path_detail"]["local_scheduler"]["example_progression_minutes"] == [10, 20, 30, 60]
        assert hint["unchanged_poll"]["after_limits"]["local_scheduler"] == "continue"

    stop = set_goal_activation_state(registry_path=wait_registry, goal_id=WAIT_GOAL, state="stopped",
                                    reason="The fixture owner stopped this Goal.", actor_kind="owner", execute=True)
    assert stop["readback"]["verified"]
    stopped = probe(wait_registry, wait_state.parents[3], WAIT_GOAL, WAIT_AGENT)
    for view in stopped.values():
        assert view["should_run"] is False
        assert view["scheduler_hint"]["action"] == "stop_until_explicit_resume"
        assert view["scheduler_hint"]["unchanged_poll"]["local_scheduler"] == "stop"
    for root in (runtime, wait_runtime):
        assert not (root / "context-delivery").exists()
        assert _spend_run_count(root) == 0
