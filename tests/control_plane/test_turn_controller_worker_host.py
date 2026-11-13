"""One Core admission, two native identities and independently checked work."""
import json
import os
from contextlib import nullcontext
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

import pytest

from loopx.control_plane.turn_driver import controller_worker as roles
from loopx.file_lock import try_exclusive_file_lock
from tests.control_plane.host_process_fixture import write_python_host_fixture


def test_revoked_role_drains_silent_exec_process(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    binary = write_python_host_fixture(tmp_path / "codex", f"""#!{sys.executable}
from pathlib import Path
import signal
import sys
import time
from loopx.file_lock import try_exclusive_file_lock
signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
with try_exclusive_file_lock(Path('active-role')) as lock:
    assert lock is not None
    Path('started').touch()
    time.sleep(45)
""")
    monkeypatch.setattr(roles, "role_cli_options", lambda *args: ())

    class Admission:
        def launch_lifetime(self, *, operation):
            if operation == "turn_role_continue" and (project / "started").exists():
                raise InterruptedError("operator stop")
            return nullcontext()

    host = roles.RoleHost(project, tmp_path / "runtime", tmp_path / "registry", tmp_path / "home",
                          str(binary), None, "low", "read-only", 10)
    state = {"threads": {}, "steps": []}
    native = roles.RoleTransport(host, time.monotonic() + 10, Admission(), state, lambda: None)
    with pytest.raises(InterruptedError, match="operator stop"):
        native.send("worker", "check", {})
    assert (project / "started").is_file()
    with try_exclusive_file_lock(project / "active-role") as lock:
        assert lock is not None
    assert len(state["steps"]) == 1 and state["steps"][0]["thread_token_usage_total"] is None


def test_worker_can_iterate_until_the_host_deadline(monkeypatch):
    from types import SimpleNamespace

    instructions = [f"Explore alternative {i}." for i in range(5)]
    reports = iter(instructions)
    worker_inputs = []

    def send(role, prompt, schema):
        if role == "worker":
            worker_inputs.append(prompt)
            return {"finding": "Measured another candidate.", "artifacts": []}
        instruction = next(reports, None)
        return {"instruction": instruction, "result": None if instruction else {"result_kind": "validated_progress"}}

    monkeypatch.setattr(roles, "prepare_codex_controller_input", lambda _: ("task", {}))
    monkeypatch.setattr(roles, "normalize_codex_result", lambda _, result: result)
    result = roles.run_roles(SimpleNamespace(send=send), {})
    assert result["result_kind"] == "validated_progress" and worker_inputs == instructions


@pytest.mark.parametrize("permission", ["danger-full-access", "read-only"])
def test_native_role_permissions_cap_the_callers_grant(tmp_path, permission):
    project = tmp_path / "task"
    project.mkdir()
    host = roles.RoleHost(project, tmp_path / "state", tmp_path / "registry", tmp_path / "home",
                          "codex", None, "low", permission, 10)
    from dataclasses import replace
    host = replace(host, controller_writable=True)
    for role in ("controller", "worker"):
        name, config = roles.role_policy(role, host)
        rules = config["permissions"][name]
        root = rules["filesystem"][":workspace_roots"]
        assert root["."] == ("write" if permission != "read-only" else "read")
        assert root[".loopx"] == root[".codex"] == "deny"
        assert rules["filesystem"][str(host.runtime)] == "deny"
        assert rules["filesystem"][str(host.codex_home)] == "deny"
        assert rules["network"]["enabled"] is False


def test_correction_readback_never_starts_another_uncertain_or_completed_attempt(tmp_path):
    path, journal = tmp_path / "roles.json", tmp_path / "turn.json"
    request = {"turn_key": "original", "work_contract": {"todo": "selected"}, "result_contract": {}}
    evidence, _ = roles.prepare_role_attempt(path, journal, request)
    evidence["result"] = {"result_kind": "validated_progress"}
    path.write_text(json.dumps(evidence))
    recovery = {**request, "result_contract": {"correction": "Correct the original receipt."}}
    with pytest.raises(RuntimeError, match="original role attempt"):
        roles.prepare_role_attempt(tmp_path / "missing-original.json", journal, recovery)
    journal.write_text(json.dumps({"status": "committed"}))
    with pytest.raises(RuntimeError, match="no authorized Host retry"):
        roles.prepare_role_attempt(path, journal, recovery)

    journal.write_text(json.dumps({"recovery_audit": {
        "planned": {"retry_failed": True, "reinvoke_host": True, "resume_from": "host_execute"},
        "actual": {"status": "started"},
    }}))
    with pytest.raises(RuntimeError, match="original role attempt"):
        roles.prepare_role_attempt(path, journal, {**recovery, "work_contract": {"todo": "other"}})
    historical = dict(evidence)
    del historical["transport"]
    path.write_text(json.dumps(historical))
    assert roles.prepare_role_attempt(path, journal, request)[1]["result"] == historical["result"]
    with pytest.raises(RuntimeError, match="original role transport"):
        roles.prepare_role_attempt(path, journal, recovery)
    path.write_text(json.dumps(evidence))
    evidence, correction = roles.prepare_role_attempt(path, journal, recovery)
    path.write_text(json.dumps(evidence))
    with pytest.raises(RuntimeError, match="correction requires reconciliation"):
        roles.prepare_role_attempt(path, journal, recovery)
    correction["result"] = {"result_kind": "validated_progress", "work_evidence": ["artifact"]}
    path.write_text(json.dumps(evidence))
    assert roles.prepare_role_attempt(path, journal, recovery)[1]["result"] == correction["result"]
    assert roles.prepare_role_attempt(path, journal, request)[1]["result"] == evidence["result"]


@pytest.mark.skipif(os.environ.get("LOOPX_TEST_NATIVE_ROLE_HOST") != "1",
                    reason="opt-in installed Codex with native filesystem isolation")
@pytest.mark.parametrize("entry", ["turn", "benchmark", "receipt-repair", "goal-stop"])
def test_real_native_feedback_changes_the_checked_artifact(tmp_path, entry):
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture
    from tests.control_plane.codex_checkpoint_fixture import local_inference_provider

    src = Path(__file__).resolve().parents[2]
    fixture = runpy.run_path(str(src / "examples/loopx-turn-codex-cli-e2e-smoke.py"))
    project, runtime, _, registry = fixture["_write_fixture"](tmp_path, turn_count=2)
    goal, agent = fixture["GOAL_ID"], fixture["AGENT_ID"]
    initialize_canonical_source_fixture(runtime, goal,
        state_path=project / ".codex" / "goals" / goal / "ACTIVE_GOAL_STATE.md", provider="sqlite")
    (tmp_path / "skills").mkdir()
    marker, expected = fixture["MARKER_NAME"], fixture["_marker_value"](1)
    (project / "observation").write_text(expected)
    source_task = "Inspect the observation.\nReport the checksum."
    task_file = tmp_path / "worker-task.txt"
    task_file.write_text(source_task)
    pinned_task = entry != "turn"
    if entry == "goal-stop":
        (project / "docs").mkdir(exist_ok=True)
    tool_started = project / "docs" / "tool-started"
    late_write = project / "docs" / "after-stop"
    key = None
    inputs = []
    def respond(request, index, message):
        nonlocal key
        assert not any("<permissions instructions>" in part.get("text", "")
                       for item in request["input"] if item.get("role") == "developer"
                       for part in item["content"] if isinstance(part, dict))
        text = [part["text"] for item in request["input"] if item.get("role") == "user"
                for part in item["content"] if "text" in part][-1]
        if entry == "goal-stop" and index == 2:
            command = ("/bin/sleep 6 & tool_pid=$!; "
                       "printf '%s\\n' \"$$ $tool_pid\" > docs/tool-started; "
                       "wait \"$tool_pid\"; /usr/bin/touch docs/after-stop")
            return {"type": "function_call", "id": "fc_stop", "call_id": "call_stop",
                    "name": "exec_command", "arguments": json.dumps({"cmd": command, "yield_time_ms": 1000})}
        if entry == "goal-stop" and index > 2:
            # Keep the Worker waiting for its tool while the Goal is stopped.
            time.sleep(6)
            return message
        if index == 4:
            return {"type": "function_call", "id": "fc_readonly", "call_id": "call_readonly",
                    "name": "exec_command", "arguments": json.dumps({"cmd": "cat observation > docs/controller-check; if cat .loopx/registry.json >/dev/null; then exit 17; fi; cat docs/controller-check"})}
        if index == 5:
            output = str(request["input"][-1]["output"]).lower()
            assert expected.lower() in output
            assert "denied" in output or "not permitted" in output
        if index >= 5:
            index -= 1
        inputs.append(text)
        if index == 1:
            contract, _ = json.JSONDecoder().raw_decode(text.split("Turn request:\n", 1)[1])
            key = contract["turn_key"]
            answer = {"instruction": "dispatch" if pinned_task else "Inspect the observation.", "result": None}
        elif index in (2, 5):
            command = ("if cat .loopx/registry.json >/dev/null; then exit 17; fi; cat observation"
                       if index == 2 else "tr -d '\\n' > " + marker + " <<EOF\n$(cat observation)\nEOF")
            return {"type": "function_call", "id": f"fc_{index}", "call_id": f"call_{index}",
                    "name": "exec_command", "arguments": json.dumps({"cmd": command})}
        elif index == 3:
            output = str(request["input"][-1]["output"])
            denied = any(reason in output.lower() for reason in ("denied", "not permitted"))
            answer = {"finding": expected if expected in output and denied else "unverified", "artifacts": []}
        elif index == 4:
            answer = {"instruction": "Copy the checked observation into the output file." if expected in text
                      else "Report the missing observation.", "result": None}
        elif index == 6:
            answer = {"finding": "Written", "artifacts": [marker]}
        else:
            answer = {"instruction": None, "result": {
                "schema_version": "loopx_turn_result_v0", "turn_key": key, "result_kind": "validated_progress",
                "completed_phases": ["host_execute", "typed_result"], "classification": "fixture_progress",
                "recommended_action": "Check the output.", "next_action": "Continue the work.",
                "delivery_batch_scale": "single_surface", "delivery_outcome": "outcome_progress",
                "vision_unchanged_reason": "The objective is unchanged.", "summary": "Updated the artifact.",
                "work_evidence": ["artifact:marker"], "validation_evidence": ["check:marker-readback"]}}
        if entry == "receipt-repair" and index == 7:
            answer["result"]["work_evidence"] = ["/tmp/private-marker"]
        if index == 8:
            assert "previous result failed" in text
            assert "/tmp/private-marker" not in text
            answer = answer["result"]
        message["content"][0]["text"] = json.dumps(answer)
        return message

    binary = shutil.which("codex")
    assert binary, "Install the Codex CLI before enabling native role qualification"
    invocations = tmp_path / "cli-invocations"
    wrapper = tmp_path / "observed-codex"
    wrapper.write_text(f"#!{sys.executable}\nimport os,sys\nargs = sys.argv[1:]\n"
                       f"with open({str(invocations)!r}, 'a') as f:\n"
                       "    print('exec resume ' + args[-2] if 'resume' in args else args[0], file=f)\n"
                       f"os.execv({binary!r}, [{binary!r}, *args])\n")
    wrapper.chmod(0o700)
    binary = str(wrapper)
    with local_inference_provider(respond) as (url, _, _):
        env = dict(os.environ, HOME=str(tmp_path / "home"), PYTHONPATH=str(src),
            LOOPX_CLI=str(Path(sys.executable).parent / "loopx"), CODEX_BIN=binary, MODEL_NAME="gpt-5.4",
            REASONING_EFFORT="low", OPENAI_BASE_URL=url, OPENAI_API_KEY="fixture", LOOPX_USAGE_PING="0",
            LOOPX_PROJECT=str(project), LOOPX_RUNTIME_ROOT=str(runtime), LOOPX_REGISTRY=str(registry),
            LOOPX_GOAL_ID=goal, LOOPX_AGENT_ID=agent, LOOPX_SHARED_SKILLS=str(tmp_path / "skills"),
            LOOPX_CODEX_HOME=str(tmp_path / "codex"), LOOPX_WAKE_LOG_DIR=str(tmp_path / "logs/wakes"),
            LOOPX_CODEX_SANDBOX="workspace-write", LOOPX_EXECUTION_MODE="turn", LOOPX_TASK_ENTRY="seeded-todo",
            LOOPX_TURN_TOPOLOGY="controller-worker", LOOPX_CODEX_TURN_TIMEOUT_SEC="45", LOOPX_TASK_DOC=str(task_file),
            LOOPX_VALIDATION_COMMAND_JSON=json.dumps(fixture["_validator_command"](expected)))
        if entry == "benchmark":
            from benchmark.runtime.scheduler import worker_command
            cmd = worker_command(env, python=sys.executable, source=str(src),
                                 state_file=str(tmp_path / "scheduler.json"), host_timeout=70) + ["--once"]
        else:
            # Standalone product invocation, with no benchmark variables or guest installation.
            material = task_file if pinned_task else tmp_path / "task-material.txt"
            if not pinned_task:
                material.write_text("Read the supplied observation as a checksum.")
            material_option = "--task-material" if pinned_task else "--controller-material"
            home = tmp_path / "role-home"
            home.mkdir()
            (home / "config.toml").write_text(
                f'model="gpt-5.4"\nmodel_reasoning_effort="low"\nmodel_provider="local"\n[model_providers.local]\nname="local"\nbase_url="{url}"\n'
                'wire_api="responses"\nenv_key="OPENAI_API_KEY"\n'
                '[mcp_servers."unused.fixture"]\ncommand="/bin/sh"\n'
                f'args=["-c", "touch {tmp_path / "mcp-started"}"]\n'
                '[mcp_servers.remote]\nurl="http://127.0.0.1:1/unused"\nrequired=true\n'
            )
            env = {k: v for k, v in os.environ.items() if not k.startswith("LOOPX_")}
            env.update(CODEX_HOME=str(home), OPENAI_API_KEY="fixture", PYTHONPATH=str(src), LOOPX_USAGE_PING="0")
            cmd = [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--runtime-root", str(runtime),
                   "--format", "json", "turn", "run-once", "--execute", "--no-global-sync",
                   "--goal-id", goal, "--agent-id", agent, "--project", str(project),
                   "--host", "codex-cli", "--execution-mode", "isolated-headless", "--topology", "controller-worker",
                   "--scheduler-owner", "outer_controller", "--prepare-selected-todo",
                   "--turn-instance-id", "direct-roles", "--timeout-seconds", "45", "--codex-bin", binary,
                   material_option, str(material), "--codex-sandbox", "workspace-write",
                   "--validation-command-json", json.dumps(fixture["_validator_command"](expected))]
        if entry == "goal-stop":
            from loopx.control_plane.goals.activation_service import set_goal_activation_state

            with subprocess.Popen(cmd, cwd=project, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) as process:
                try:
                    deadline = time.monotonic() + 30
                    while not tool_started.is_file() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(.025)
                    assert tool_started.is_file(), "the installed Codex never started its native tool"
                    pids = tool_started.read_text().split()
                    assert len(pids) == 2
                    stopped = set_goal_activation_state(registry_path=registry, goal_id=goal, state="stopped",
                        actor_kind="owner", reason="Stop the running test Goal",
                        runtime_root_override=str(runtime), execute=True)
                    assert stopped["ok"] and stopped["readback"]["verified"], stopped
                    output, error = process.communicate(timeout=15)
                    assert process.returncode == 1 and not json.loads(output)["ok"], (output, error)
                    states = subprocess.run(["ps", "-p", ",".join(pids), "-o", "stat="],
                        capture_output=True, text=True, timeout=5).stdout.split()
                    assert all(state.startswith("Z") for state in states), states
                    assert not late_write.exists()
                    assert fixture["_quota_spend_count"](runtime) == 0
                finally:
                    if process.poll() is None:
                        process.kill()
            return
        completed = subprocess.run(cmd, cwd=project, env=env, timeout=85, capture_output=True, text=True)
        if entry == "receipt-repair":
            first = json.loads(completed.stdout)
            assert first["validation_stage"] == "host_result_contract"
            assert fixture["_quota_spend_count"](runtime) == 0
            original_worker_write = (project / marker).stat().st_mtime_ns
            offset = cmd.index("--turn-instance-id")
            retry = cmd[:offset] + ["--resume-turn-key", first["resume_turn_key"], "--retry-failed-turn"] + cmd[offset + 2:]
            completed = subprocess.run(retry, cwd=project, env=env, timeout=85, capture_output=True, text=True)
            assert (project / marker).stat().st_mtime_ns == original_worker_write
        assert completed.returncode == 0, completed.stdout[-2200:] + completed.stderr[-500:]
    assert (project / "docs/controller-check").read_text().strip() == expected
    assert not (tmp_path / "mcp-started").exists()
    assert "For your Core receipt:" in inputs[3]
    assert not any("For your Core receipt:" in inputs[i] for i in (0, 1, 4, 6))
    assert inputs[1] == (source_task if pinned_task else "Inspect the observation.")
    assert inputs[4] == "Copy the checked observation into the output file."
    assert len(inputs) == (8 if entry == "receipt-repair" else 7) and "Copy the checked observation" in inputs[4]
    assert all("work_contract" not in inputs[i] for i in (1, 2, 4, 5))
    assert (project / marker).read_text() == expected
    if entry == "benchmark":
        wake, = (tmp_path / "logs/wakes").iterdir()
        assert json.loads((wake / "receipt.json").read_text())["turn_execution"]["status"] == "committed"
    else:
        assert (source_task if pinned_task else "Read the supplied observation as a checksum.") in inputs[0]
        result = json.loads(completed.stdout)
        assert result["status"] == "committed" and result["host"]["topology"] == "controller-worker"
        start = cmd.index("--turn-instance-id")
        recovery = cmd[:start] + ["--resume-turn-key", result["resume_turn_key"]] + cmd[start + 2:]
        replay = subprocess.run(recovery, cwd=project, env=env, capture_output=True, text=True, timeout=15)
        assert replay.returncode == 0 and not any(json.loads(replay.stdout)["effects"].values())
        for flag, value in (("--topology", "single-worker"), ("--codex-sandbox", "read-only")):
            changed = list(recovery)
            changed[changed.index(flag) + 1] = value
            if flag == "--topology":
                offset = changed.index(material_option)
                del changed[offset:offset + 2]
            held = subprocess.run(changed, cwd=project, env=env, capture_output=True, text=True, timeout=15)
            assert held.returncode != 0 and "original Turn topology" in held.stdout + held.stderr
        changed_audience = list(recovery)
        changed_audience[changed_audience.index(material_option)] = "--controller-material" if pinned_task else "--task-material"
        held = subprocess.run(changed_audience, cwd=project, env=env, capture_output=True, text=True, timeout=15)
        assert held.returncode != 0 and "original Turn topology" in held.stdout + held.stderr
        material.write_text("Changed task material.")
        held = subprocess.run(recovery, cwd=project, env=env, capture_output=True, text=True, timeout=15)
        assert held.returncode != 0 and "original Turn topology" in held.stdout + held.stderr
    record, = (runtime / "goals" / goal / "turns/controller-worker").glob("*.json")
    observation = json.loads(record.read_text())
    assert observation["transport"] == "codex-exec"
    assert len(set(observation["threads"].values())) == 2
    actual = invocations.read_text().splitlines()
    assert "app-server" not in actual
    expected_execs = ["exec", "exec"] + ["exec resume " + observation["threads"][role]
        for role in (["controller", "worker", "controller"] + (["controller"] if entry == "receipt-repair" else []))]
    assert [line for line in actual if line.startswith("exec")] == expected_execs
    expected_roles = ["controller", "worker", "controller", "worker", "controller"]
    if entry == "receipt-repair":
        expected_roles.append("controller")
    assert [s["role"] for s in observation["steps"]] == expected_roles
    assert all(s["thread_token_usage_total"]["totalTokens"] > 0 for s in observation["steps"])
    assert fixture["_quota_spend_count"](runtime) == 1

    if entry == "turn":
        from loopx.control_plane.turn_driver import build_loopx_turn_host_request

        journal = json.loads((record.parent.parent / record.name).read_text())
        original = build_loopx_turn_host_request(journal["plan"])
        capture = Path(original["detail_ref"]["captured_decision"]["path"])
        changed_source = json.loads(capture.read_text())
        changed_source["recommended_action"] = "An unrelated action."
        capture.write_text(json.dumps(changed_source))
        host = roles.RoleHost(project, runtime, registry, home, binary, None, None, "workspace-write", 5)
        with pytest.raises(ValueError, match="original decision differs"):
            roles.serve(original, host)
