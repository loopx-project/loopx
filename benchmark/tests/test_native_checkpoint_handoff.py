"""Characterize live native forks before choosing a midwake capture transport.

A local deterministic provider replaces inference; the actual Host owns fork,
history and tool execution. This does not qualify a joint research checkpoint.
"""
import json
import os
import shlex
import subprocess
import sys
import time

import pytest

from benchmark.runtime.native_checkpoint import native_session_checkpoint
from benchmark.tests.test_native_checkpoint import real_native_source
from loopx.capabilities.benchmark_toolkit.native_codex_goal import (
    NativeGoalProtocolError, StdioNativeGoalTransport,
)


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
                   reason="Requires an explicit real native Host; no model service used")
def test_live_fork_preserves_context_without_quiescing_source(tmp_path):
    source_work, branch_work = tmp_path / "source", tmp_path / "branch"
    source_work.mkdir()
    branch_work.mkdir()
    requests = []
    # A bounded tool withholds its output until the test Host releases it.
    # This models transport ordering only, not a LoopX guard or writer barrier.
    code = """from pathlib import Path
import time
Path('waiting').write_text('ready')
deadline = time.monotonic() + 20
while not Path('release').exists() and time.monotonic() < deadline:
    time.sleep(.01)
assert Path('release').exists(), 'fixture release missing'
Path('source-result').write_text('one original execution')
print('WITHHELD_RESULT')
"""

    def response(request, index, message):
        requests.append(request)
        if index == 2:
            return {"type": "function_call", "id": "fc_boundary", "call_id": "call_boundary",
                    "name": "exec_command", "arguments": json.dumps({
                        "cmd": "python3 -c " + shlex.quote(code),
                        "yield_time_ms": 10000, "max_output_tokens": 100})}
        message["content"][0]["text"] = "RETAINED_RESEARCH" if index == 1 else "Continued."
        return message

    def history(host, session):
        return host.request("thread/turns/list", {"threadId": session,
            "sortDirection": "asc", "itemsView": "full", "limit": 100})["data"]

    def terminal(host, session, turn):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rows = history(host, session)
            if rows and rows[-1]["id"] == turn and rows[-1]["status"] == "completed":
                return rows
            time.sleep(.02)
        raise AssertionError("Native fixture did not complete")

    binary = os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"]
    with real_native_source(tmp_path / "native", binary, source_work, close_source=True,
            response_builder=response, sandbox="workspace-write") as (env, source, _, calls):
        with StdioNativeGoalTransport.spawn([binary, "app-server", "--listen", "stdio://"],
                cwd=str(source_work), env=env, stderr=subprocess.DEVNULL, response_timeout_sec=15) as host:
            host.request("initialize", {"clientInfo": {"name": "handoff_fixture", "version": "0.1"},
                                        "capabilities": {"experimentalApi": True}})
            host.notify("initialized", {})
            host.request("thread/resume", {"threadId": source, "cwd": str(source_work),
                "sandbox": "workspace-write", "approvalPolicy": "never", "persistExtendedHistory": True})
            turn = host.request("turn/start", {"threadId": source, "input": [
                {"type": "text", "text": "Reach the synthetic withheld-output boundary."}]})["turn"]["id"]
            try:
                deadline = time.monotonic() + 10
                while not (source_work / "waiting").exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                assert (source_work / "waiting").exists() and len(calls) == 2
                source_history = history(host, source)
                assert source_history[-1]["status"] == "inProgress"
                with pytest.raises(NativeGoalProtocolError, match="thread/fork"):
                    host.request("thread/fork", {"threadId": source, "cwd": str(branch_work),
                        "lastTurnId": turn, "persistExtendedHistory": True})
                fork = host.request("thread/fork", {"threadId": source, "cwd": str(branch_work),
                    "persistExtendedHistory": True})["thread"]["id"]
                fork_history = history(host, fork)
                assert fork != source and len(calls) == 2
                assert fork_history[:-1] == source_history[:-1]
                assert fork_history[-1]["id"] == turn and fork_history[-1]["status"] == "interrupted"
                assert history(host, source)[-1]["status"] == "inProgress"
                # Native fork success grants neither a complete cursor nor a
                # writer barrier. Keep the production capture rejection intact.
                with pytest.raises(ValueError, match="completed final cursor"):
                    native_session_checkpoint({**env, "LOOPX_PROJECT": str(branch_work)}, fork, timeout=15)
                branch_turn = host.request("turn/start", {"threadId": fork, "input": [
                    {"type": "text", "text": "Fresh fixture admission: retain context; do not replay the source tool."}]})["turn"]["id"]
                terminal(host, fork, branch_turn)
                assert len(calls) == 3
                items = requests[2]["input"]
                assert "RETAINED_RESEARCH" in json.dumps(items)
                assert any(item.get("type") == "function_call_output" and
                           item.get("call_id") == "call_boundary" and item.get("output") == "aborted"
                           for item in items)
                assert history(host, source)[-1]["status"] == "inProgress"
                assert not (source_work / "source-result").exists()
                assert not (branch_work / "waiting").exists()  # No automatic tool replay.
            finally:
                (source_work / "release").write_text("release original tool")
                terminal(host, source, turn)
            assert (source_work / "source-result").read_text() == "one original execution"
            assert not (branch_work / "source-result").exists()
            assert len(calls) == 4 and "WITHHELD_RESULT" in json.dumps(requests[3])


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN") or os.name != "posix",
                   reason="Requires a real native Host and local Unix transport")
def test_real_quota_output_is_held_across_native_fork(tmp_path):
    """The real guard executes once; neither branch observes it before release."""
    import hashlib
    import socket
    import sys
    import tempfile
    from pathlib import Path

    from benchmark.runtime.checkpoint import held_quota_decision
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TURN_ID, _write_fixture, _configure_autonomous_replan_fixture,
        _heartbeat_receipt_count,
    )

    project, runtime, registry = _write_fixture(tmp_path / "authority")
    _configure_autonomous_replan_fixture(project, runtime, registry)
    capture_root = project / "captures"
    capture_root.mkdir(mode=0o700)
    requests = []
    with tempfile.TemporaryDirectory(prefix="handoff-", dir="/tmp") as short:
        socket_path = Path(short).resolve() / "host.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))
            listener.listen(1)
            listener.settimeout(30)
            argv = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
                    "--runtime-root", str(runtime), "quota", "should-run", "--goal-id", GOAL_ID,
                    "--agent-id", AGENT_ID, "--turn-instance-id", TURN_ID,
                    "--runtime-profile", "generic_cli", "--available-capability", "shell",
                    "--available-capability", "filesystem_read", "--decision-output-root", str(capture_root),
                    "--decision-handoff-socket", str(socket_path)]
            command = "PYTHONPATH=" + shlex.quote(str(Path(__file__).resolve().parents[2])) + " " + shlex.join(argv)

            def response(request, index, message):
                requests.append(request)
                if index == 2:
                    return {"type": "function_call", "id": "fc_guard", "call_id": "call_guard",
                            "name": "exec_command", "arguments": json.dumps({
                                "cmd": command, "yield_time_ms": 10000, "max_output_tokens": 200})}
                message["content"][0]["text"] = "RESEARCH_BEFORE_GUARD" if index == 1 else "Continued."
                return message

            binary = os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"]
            with real_native_source(tmp_path / "native", binary, project, close_source=True,
                    response_builder=response, sandbox="danger-full-access") as (env, source, _, calls):
                with StdioNativeGoalTransport.spawn([binary, "app-server", "--listen", "stdio://"],
                        cwd=str(project), env=env, stderr=subprocess.DEVNULL, response_timeout_sec=30) as host:
                    host.request("initialize", {"clientInfo": {"name": "quota_handoff_fixture", "version": "0.1"},
                                                "capabilities": {"experimentalApi": True}})
                    host.notify("initialized", {})
                    host.request("thread/resume", {"threadId": source, "cwd": str(project),
                        "sandbox": "danger-full-access", "approvalPolicy": "never", "persistExtendedHistory": True})
                    turn = host.request("turn/start", {"threadId": source, "input": [
                        {"type": "text", "text": "Execute the original synthetic guard."}]})["turn"]["id"]
                    with held_quota_decision(listener, capture_root, goal_id=GOAL_ID,
                            agent_id=AGENT_ID, turn_instance_id=TURN_ID) as held:
                        assert held["quota"]["ok"] is True and len(calls) == 2
                        assert held["intent"]["capture"] is True
                        assert held["sha256"] == hashlib.sha256(held["path"].read_bytes()).hexdigest()
                        receipts = _heartbeat_receipt_count(runtime, TURN_ID)
                        assert receipts >= 1
                        # Native events, rather than the incomplete history projection,
                        # witness the actual in-flight tool and its exact command.
                        pending = None
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            event = host.next_event(timeout_sec=.1)
                            if event and event.get("method") == "item/started":
                                params = event["params"]
                                item = params.get("item", {})
                                if item.get("type") == "commandExecution":
                                    pending = params
                                    break
                        assert pending and pending["threadId"] == source and pending["turnId"] == turn
                        assert shlex.split(pending["item"]["command"])[-1] == command
                        fork = host.request("thread/fork", {"threadId": source,
                            "cwd": str(project), "persistExtendedHistory": True})["thread"]["id"]
                        rows = host.request("thread/turns/list", {"threadId": fork,
                            "sortDirection": "asc", "itemsView": "full", "limit": 100})["data"]
                        assert rows[-1]["status"] == "interrupted" and rows[-1]["id"] == turn
                        assert len(calls) == 2  # No post-guard inference while held.
                        assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
                    deadline = time.monotonic() + 10
                    while len(calls) < 3 and time.monotonic() < deadline:
                        time.sleep(.02)
                    assert len(calls) == 3 and "heartbeat_receipt" in json.dumps(requests[2])
                    assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
                    assert held["path"].read_bytes()  # Original receipt stays recoverable.


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN") or os.name != "posix",
                   reason="Requires a real native Host and local Unix transport")
@pytest.mark.parametrize("outcome", ["resume", "capture_failure", "changed_packet"])
def test_native_cli_interruption_retains_guard_beyond_tool_yield(tmp_path, outcome):
    """A real native CLI stops, then explicitly resumes the one committed guard."""
    import socket
    import sys
    import tempfile
    from pathlib import Path

    from benchmark.runtime.checkpoint import held_quota_decision
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TURN_ID, _write_fixture, _configure_autonomous_replan_fixture,
        _heartbeat_receipt_count,
    )

    project, runtime, registry = _write_fixture(tmp_path / "authority")
    _configure_autonomous_replan_fixture(project, runtime, registry)
    capture_root = project / "captures"
    capture_root.mkdir(mode=0o700)
    requests = []
    with tempfile.TemporaryDirectory(prefix="handoff-", dir="/tmp") as short:
        socket_path = Path(short).resolve() / "host.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))
            listener.listen(1)
            listener.settimeout(30)
            argv = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
                    "--runtime-root", str(runtime), "quota", "should-run", "--goal-id", GOAL_ID,
                    "--agent-id", AGENT_ID, "--turn-instance-id", TURN_ID,
                    "--runtime-profile", "generic_cli", "--available-capability", "shell",
                    "--available-capability", "filesystem_read", "--decision-output-root", str(capture_root),
                    "--decision-handoff-socket", str(socket_path)]
            command = "PYTHONPATH=" + shlex.quote(str(Path(__file__).resolve().parents[2])) + " " + shlex.join(argv)

            def response(request, index, message):
                requests.append(request)
                if index == 2:
                    return {"type": "function_call", "id": "fc_guard", "call_id": "call_guard",
                            "name": "exec_command", "arguments": json.dumps({
                                "cmd": command, "yield_time_ms": 10000, "max_output_tokens": 200})}
                message["content"][0]["text"] = "RESEARCH_BEFORE_GUARD" if index == 1 else "Continued."
                return message

            binary = os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"]
            with real_native_source(tmp_path / "native", binary, project, close_source=True,
                    response_builder=response, sandbox="danger-full-access") as (env, source, _, calls):
                from types import SimpleNamespace
                from benchmark.runtime.worker import child_process, native_command
                from benchmark.runtime.codex import Execution
                env.update(MODEL_NAME="fixture", REASONING_EFFORT="high")
                wake = tmp_path / "cli-wake"
                wake.mkdir()
                cli_command = native_command(env, Execution(context="resume", sandbox="danger-full-access"),
                                         "execute", wake, SimpleNamespace(session_id=source))
                with (wake / "stdout.jsonl").open("w") as output, (wake / "stderr.log").open("w") as error:
                    with child_process(cli_command, env=env, stdout=output, stderr=error) as process:
                        process.stdin.write("Execute the original synthetic guard.\n")
                        process.stdin.flush()
                        process.stdin.close()
                        from contextlib import nullcontext
                        expected = (nullcontext() if outcome == "resume" else
                                    pytest.raises(ValueError, match="capture failed|decision changed"))
                        with expected:
                            with held_quota_decision(listener, capture_root, goal_id=GOAL_ID,
                                    agent_id=AGENT_ID, turn_instance_id=TURN_ID,
                                    native_process=process) as held:
                                assert held["intent"]["capture"] and len(calls) == 2
                                assert process.poll() is not None
                                assert "continuation_body" not in held
                                receipts = _heartbeat_receipt_count(runtime, TURN_ID)
                                if outcome == "capture_failure":
                                    raise ValueError("capture failed")
                                if outcome == "changed_packet":
                                    held["path"].write_text("{}")
                                else:
                                    time.sleep(12)  # Exceeds the real tool's 10-second yield.
                                assert len(calls) == 2
                                assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
                if outcome != "resume":
                    assert "continuation_body" not in held
                    assert process.poll() is not None and len(calls) == 2
                    assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
                    return
                original = held["path"].read_text()
                assert held["continuation_body"].endswith(original)
                result = subprocess.run(cli_command, env=env, cwd=project,
                    input=held["continuation_body"],
                    text=True,capture_output=True, timeout=30)
                assert result.returncode == 0, result.stderr
                assert len(calls) == 3 and "heartbeat_receipt" in json.dumps(requests[2])
                assert _heartbeat_receipt_count(runtime, TURN_ID) == receipts
                assert "RESEARCH_BEFORE_GUARD" in json.dumps(requests[2])
                assert any(item.get("type") == "function_call_output" and
                           item.get("call_id") == "call_guard" and
                           "aborted" in item.get("output", "")
                           for item in requests[2]["input"])


@pytest.mark.skipif(os.name != "posix", reason="Requires owned Unix process groups")
def test_checkpoint_interrupt_rejects_shared_group_and_invalid_timeout(tmp_path):
    import sys
    from benchmark.runtime.worker import interrupt_checkpoint_process

    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"]) as process:
        try:
            with pytest.raises(ValueError, match="positive timeout"):
                interrupt_checkpoint_process(process, timeout=float("nan"))
            with pytest.raises(ValueError, match="owned child process group"):
                interrupt_checkpoint_process(process)
            assert process.poll() is None  # No signal to the caller's own group.
        finally:
            process.terminate()
            process.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="Requires owned Unix process groups")
def test_checkpoint_interrupt_timeout_reaps_without_qualifying_capture(tmp_path):
    import sys
    from benchmark.runtime.worker import child_process, interrupt_checkpoint_process

    ready = tmp_path / "ready"
    code = ("import signal,time; from pathlib import Path; "
            "signal.signal(signal.SIGINT, signal.SIG_IGN); "
            f"Path({str(ready)!r}).touch(); time.sleep(30)")
    with child_process([sys.executable, "-c", code],
            env={**os.environ, "LOOPX_PROJECT": str(tmp_path)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        with pytest.raises(subprocess.TimeoutExpired):
            interrupt_checkpoint_process(process, timeout=.1)
        assert process.poll() is not None


@pytest.mark.parametrize("platform,native,interrupt", [
    ("darwin", True, False), ("linux", True, True), ("darwin", False, True),
])
def test_midwake_native_profile_refusal_precedes_interruption(tmp_path, monkeypatch, platform, native, interrupt):
    from benchmark.runtime.checkpoint_wake import CheckpointWake

    wake = CheckpointWake({"LOOPX_WAKE_LOG_DIR": str(tmp_path / "wakes"),
        "LOOPX_RUNTIME_ROOT": str(tmp_path / "runtime"), "LOOPX_GOAL_ID": "fixture",
        "LOOPX_AGENT_ID": "researcher"}, {"turn_id": "fixture-turn"})
    wake.request, wake.raw = {"native_session": native}, b"fixture declaration"
    monkeypatch.setattr(sys, "platform", platform)
    intent = {"capture": True, "obligation_id": "replan-0123456789abcdef"}
    assert wake._first_replan(intent) is interrupt
    assert wake._first_replan(intent) is False  # Same point never becomes a later attempt.
    events = wake.receipt.get("checkpoint_handoffs", [])
    assert len(events) == (0 if interrupt else 1)
    if events:
        assert not events[0]["captured"] and not events[0]["research_point_qualified"]


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN") or
                   os.environ.get("LOOPX_CHECKPOINT_EXTERNAL_CONTROLLER") != "1",
                   reason="Requires the real native CLI and explicitly attached checkpoint supervisor")
@pytest.mark.parametrize("explore_mode", ["off", "planning"])
def test_actual_worker_captures_pending_replan_before_native_model(tmp_path, explore_mode):
    """Six real settled work wakes -> pre-decision capture -> stride -> work.

    Inference is deterministic; native history/tools, canonical SQLite, quota,
    periodic cadence and the Host writer barrier use their production paths.
    No synthetic run-history rows are inserted to manufacture a review.
    """
    import sys
    import tomllib
    from pathlib import Path
    from benchmark.runtime.worker import run_once
    from tests.control_plane import test_quota_settlement_cli as cli
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

    project, runtime, registry = cli._write_fixture(tmp_path / "authority")
    document = json.loads(registry.read_text())
    document["goals"][0]["quota"]["allowed_slots"] = 18
    registry.write_text(json.dumps(document))
    state_path = project / document["goals"][0]["state_file"]
    logs = Path("/logs/agent/wakes")
    candidate = project / "candidate.txt"
    candidate.write_text("0")
    git_object = project / ".git" / "objects" / "ab" / "retained-object"
    git_object.parent.mkdir(parents=True)
    git_object.write_text("Readonly source evidence")
    git_object.chmod(0o444)
    executable = tmp_path / "loopx-cli"
    executable.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' -m loopx.cli "$@"\n')
    executable.chmod(0o700)
    skills = tmp_path / "skills"
    skills.mkdir()
    requests, guards, work_turns = [], [], []
    phase = {"kind": "initial", "values": [], "tool_sent": False}

    def run(*args):
        code, result = cli._run_cli(registry, runtime, *args, cwd=project)
        assert code == 0, json.dumps(result, indent=2)
        return result

    listed = run("todo", "list", "--goal-id", cli.GOAL_ID)
    initialize_canonical_authority(runtime, cli.GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=cli.GOAL_ID, todos=listed["todos"],
                                            handoff_mode="hard_lease", leases=[]),
        state_path=state_path, provider="sqlite")
    run("task-lease", "acquire", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID,
        "--owner", cli.AGENT_ID, "--idempotency-key", "source-work")
    configured = run("configure-goal", "--goal-id", cli.GOAL_ID,
        "--execution-replan-after-turns", "6", "--execution-stride-strategy", "evidence_conditioned_v1",
        "--explore-mode", explore_mode, "--execute")
    assert configured["after"]["execution_profile"]["replan_after_effective_turns"] == 6

    def response(payload, index, message):
        requests.append(payload)
        if phase["kind"] == "work" and not phase["tool_sent"]:
            phase["tool_sent"] = True
            prefix = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
                      "--runtime-root", str(runtime), "--format", "json", "quota", "should-run",
                      "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
                      "--runtime-profile", "generic_cli", "--available-capability", "shell"]
            # The native tool selects its current Turn before changing code.
            # Native work and validation precede the real CLI settlement below.
            code = ("import os,json,subprocess; from pathlib import Path; "
                    f"p=subprocess.run({prefix!r}+['--turn-instance-id',os.environ['LOOPX_TURN']],"
                    "capture_output=True,text=True,check=True); g=json.loads(p.stdout); "
                    "assert not g.get('autonomous_replan_obligation'); "
                    "assert g['interaction_contract']['agent_channel']['delivery_allowed']; "
                    "Path('work-guard.json').write_text(p.stdout); "
                    f"values={phase['values']!r}; "
                    "[(Path('candidate.txt').write_text(str(v)),"
                    "Path('evidence-'+str(v)+'.json').write_text(json.dumps({'value':v,'synthetic':True}))) for v in values]; "
                    "assert Path('candidate.txt').read_text()==str(values[-1]); print('CANDIDATE_VERIFIED')")
            return {"type": "function_call", "id": f"fc_work_{index}", "call_id": f"call_work_{index}",
                    "name": "exec_command", "arguments": json.dumps({
                        "cmd": shlex.quote(sys.executable) + " -c " + shlex.quote(code),
                        "yield_time_ms": 10000, "max_output_tokens": 100})}
        if phase["kind"] == "review" and not phase["tool_sent"]:
            phase["tool_sent"] = True
            replies = list((logs.parent / "checkpoint-controller").glob("*.json"))
            reply = next(json.loads(path.read_text()) for path in replies if path.name != "ready.json")
            assert reply["ok"] and len(reply["capture"]["sha256"]) == 64
            paths = list(logs.glob("*/checkpoint-start-guard.json"))
            assert len(paths) == 1
            guard = json.loads(paths[0].read_text())
            guards.append(guard)
            trigger = next(t for t in guard["autonomous_replan_obligation"]["triggers"]
                           if t["kind"] == "periodic_review_due")
            assert trigger["run_count"] == trigger["threshold"] == 6
            control = guard["replan_action_packet"]["stride_control"]
            assert set(control["actions"]) == {"grow", "switch", "verify"}
            basis = control["explore_basis"]
            assert bool(basis and basis["harness_enabled"]) == (explore_mode == "planning")
            assert guard["heartbeat_receipt"]["settlement_binding_owed"]
            assert "settlement_identity" not in guard["heartbeat_receipt"]
            assert not guard["interaction_contract"]["agent_channel"]["delivery_allowed"]
            # The sealed point precedes the policy decision and seventh work.
            assert candidate.read_text() == "6"
            return {"type": "function_call", "id": "fc_packet", "call_id": "call_packet",
                    "name": "exec_command", "arguments": json.dumps({
                        "cmd": "cat " + shlex.quote(str(paths[0])), "max_output_tokens": 30000})}
        message["content"][0]["text"] = "Original packet read." if phase["kind"] == "review" else "Synthetic work verified."
        return message

    def settle_work(result, expected):
        assert result["ok"], result
        assert candidate.read_text() == str(expected)
        guard = json.loads((project / "work-guard.json").read_text())
        assert guard["heartbeat_receipt"]["turn_instance_id"] == result["turn_id"]
        binding = ("--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
                   "--todo-id", cli.TODO_ID, "--turn-instance-id", result["turn_id"])
        run("refresh-state", *binding, "--classification", "synthetic_candidate_verified",
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--delivery-boundary", "in_flight_continuation", "--progress-result-class", "advanced",
            "--progress-surface-id", "fixture:candidate", "--progress-evidence-id", f"evidence:candidate-{expected}",
            "--no-global-sync", "--suppress-external-sinks")
        spent = run("quota", "spend-slot", *binding, "--source", "heartbeat", "--execute")
        assert spent["settlement_progress"]["state"] == "settled"
        work_turns.append(result["turn_id"])
        if expected == 3:
            replay = run("quota", "spend-slot", *binding, "--source", "heartbeat", "--execute")
            assert replay["idempotent_replay"] and not replay["appended"]

    with real_native_source(tmp_path / "provider", os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"],
            project, close_source=True, response_builder=response,
            sandbox="danger-full-access") as (env, _, _, calls):
        base = tomllib.loads((Path(env["CODEX_HOME"]) / "config.toml").read_text())
        env.update(LOOPX_CLI=str(executable), LOOPX_REGISTRY=str(registry),
            LOOPX_RUNTIME_ROOT=str(runtime), LOOPX_GOAL_ID=cli.GOAL_ID, LOOPX_AGENT_ID=cli.AGENT_ID,
            LOOPX_WAKE_LOG_DIR=str(logs), LOOPX_CODEX_HOME=str(tmp_path / "worker-home"),
            LOOPX_ITERATION_CONTEXT="resume", LOOPX_SHARED_SKILLS=str(skills), MODEL_NAME="fixture",
            REASONING_EFFORT="high", OPENAI_BASE_URL=base["model_providers"]["fixture"]["base_url"],
            OPENAI_API_KEY="synthetic-local-only", LOOPX_CODEX_TURN_TIMEOUT_SEC="60")
        for value in range(1, 7):
            phase.update(kind="work", values=[value], tool_sent=False)
            prior = run_once(env)
            settle_work(prior, value)
        assert len(set(work_turns)) == 6 and len(calls) == 13
        request = tmp_path / "request.json"
        request.write_text(json.dumps({"destination": str(logs.parent / "checkpoints"),
            "components": [{"id": "task_workspace", "source_path": str(project)}],
            "point_metadata": {},
            "trigger": "fresh_replan", "native_session": True, "loopx_state": True,
            "evaluation_opportunity": True, "controller_handoff": True}))
        phase.update(kind="review", tool_sent=False)
        result = run_once({**env, "LOOPX_CHECKPOINT_REQUEST": str(request)})
        assert result["ok"] and result["checkpoint"]["captured"], result
        assert result["checkpoint"]["phase"] == "before_replan"
        assert result["checkpoint"]["source_turn_id"] == prior["turn_id"]
        assert result["session"]["session_id"] == prior["session"]["session_id"]
        assert not result.get("checkpoint_handoffs") and len(calls) == 15
        assert cli._heartbeat_receipt_count(runtime, result["turn_id"]) == 1
        assert "settlement_binding_owed" in json.dumps(requests[14]) and len(guards) == 1

        # The original source now admits its review; capture has granted nothing.
        selected = run("quota", "should-run", "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
            "--todo-id", cli.TODO_ID, "--turn-instance-id", result["turn_id"], "--runtime-profile", "generic_cli")
        control = selected["replan_action_packet"]["stride_control"]
        decision = json.loads(json.dumps(control["minimal_example"]))
        decision.update(basis="supported_progress", action="grow", axes={
            "work_slice": {"plan": "Complete candidates 7 and 8 with local validation in one work wake.",
                           "reason": "Six settled candidates establish the same complete synthetic work path."},
            "route_distance": {"plan": "Retain the counter increment method.",
                               "reason": "This checks continuation, not a scientific route improvement."},
            "validation_scope": {"plan": "Verify both candidate evidence files and final value 8.",
                                 "reason": "A larger complete slice must preserve the tested postcondition."}},
            next_probe={"scope": "One two-candidate synthetic work slice.", "prediction": "Final candidate is 8.",
                        "adoption_criterion": "Both evidence files and final value agree.",
                        "cost_bound": "One native wake plus its validation and settlement.",
                        "minimum_evidence": "Native tool output, candidate bytes, evidence and accountable settlement."})
        if "explore_review" in decision:
            decision["explore_review"].update(disposition="no_applicable_evidence", considered_refs=[],
                reason="The delivered graph contains no findings; retain the tested synthetic method.")
        vision = tmp_path / "vision.json"
        vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0",
            "state": "vision_patch_proposed", "vision_patch": {"acceptance_summary": "Continuation qualification remains."},
            "path_delta": {"outcome": "continue", "prior_assumption": "One candidate per work wake.",
                "observed_reality": "Six native work wakes and their once-only settlements succeeded.",
                "changed": ["Complete two synthetic candidates in the next work wake."],
                "evidence_refs": ["evidence:candidate-6"]}, "stride_decision": decision}))
        binding = ("--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
                   "--turn-instance-id", result["turn_id"])
        identity = selected["heartbeat_receipt"]["settlement_identity"]
        # Selection may preserve a legal Todo binding. Use its actual causal
        # identity instead of assuming every periodic review is semantic-only.
        if identity.get("todo_id"):
            binding += ("--todo-id", identity["todo_id"])
        else:
            binding += ("--replan-obligation-id", identity["replan_obligation_id"])
        run("refresh-state", *binding, "--classification", "synthetic_stride_review",
            "--autonomous-replan-recorded", "--delivery-outcome", "outcome_progress",
            "--agent-vision-json", str(vision),
            "--no-global-sync", "--suppress-external-sinks")
        run("quota", "spend-slot", *binding, "--source", "heartbeat", "--execute")
        phase.update(kind="work", values=[7, 8], tool_sent=False)
        adopted = run_once(env)
        settle_work(adopted, 8)
        assert len(calls) == 17 and len(work_turns) == 7
        assert all(json.loads((project / f"evidence-{v}.json").read_text())["value"] == v for v in (7, 8))
        (tmp_path / "controller-result.json").write_text(json.dumps({**result["checkpoint"],
            "natural_work_turns": work_turns[:6], "stride_action": "grow", "explore_mode": explore_mode,
            "source_followthrough_verified": True, "adopted_turn_id": adopted["turn_id"]}))


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN") or os.name != "posix",
                   reason="Requires the real native CLI and private Unix transport")
@pytest.mark.parametrize("capture", ["files", "native", "full", "off", "controller"])
def test_actual_worker_handles_midwake_replan_and_duplicate_without_guard_replay(tmp_path, capture):
    """Use run_once, its renderer/listener/session owner and the actual quota CLI."""
    import sys
    import tomllib
    from pathlib import Path
    from benchmark.runtime.worker import run_once
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TURN_ID, _write_fixture, _configure_autonomous_replan_fixture,
        _heartbeat_receipt_count, _run_cli,
    )

    if capture == "controller" and os.environ.get("LOOPX_CHECKPOINT_EXTERNAL_CONTROLLER") != "1":
        pytest.skip("Requires an explicitly attached host checkpoint supervisor")
    project, runtime, registry = _write_fixture(tmp_path / "authority")
    logs = Path("/logs/agent/wakes") if capture == "controller" else tmp_path / "trial" / "wakes"
    candidate = project / "candidate.txt"
    candidate.write_text("original candidate")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"destination": str(logs.parent / "checkpoints"),
        "components": [{"id": "code", "source_path": str(candidate)}],
        "point_metadata": {}, "trigger": "fresh_replan", "native_session": capture in {"native", "full", "controller"},
        "loopx_state": capture in {"full", "controller"},
        "evaluation_opportunity": capture == "controller", "controller_handoff": capture == "controller"}))
    skills = tmp_path / "skills"
    skills.mkdir()
    cli = tmp_path / "loopx-cli"
    cli.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' -m loopx.cli "$@"\n')
    cli.chmod(0o700)
    requests, guards = [], []

    def texts(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for child in value:
                yield from texts(child)
        elif isinstance(value, dict):
            for child in value.values():
                yield from texts(child)

    def response(payload, index, message):
        requests.append(payload)
        if index == 2:
            # The Host preflight was ordinary. A new admitted replan arises
            # later in this same native wake; only the fixture state is synthetic.
            preflights = list(logs.glob("*/checkpoint-start-guard.json"))
            if preflights:
                packet = json.loads(preflights[0].read_text())
                identity = packet["heartbeat_receipt"]["settlement_identity"]
                rc, refreshed = _run_cli(registry, runtime, "refresh-state", "--goal-id", GOAL_ID,
                    "--agent-id", AGENT_ID, "--todo-id", identity["todo_id"],
                    "--turn-instance-id", identity["turn_instance_id"],
                    "--classification", "validated_progress", "--delivery-batch-scale", "implementation",
                    "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
                    "--no-global-sync", "--suppress-external-sinks", cwd=project)
                assert rc == 0, json.dumps(refreshed)
                rc, settled = _run_cli(registry, runtime, *shlex.split(refreshed["settlement_owed"]["command"])[1:], cwd=project)
                assert rc == 0, settled
            index_path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
            prior_runs = index_path.read_text() if index_path.exists() else ""
            _configure_autonomous_replan_fixture(project, runtime, registry)
            # Preserve the actual preflight settlement evidence. The reusable
            # fixture replaces the index; its two later synthetic observations
            # must not erase the already settled original Host Turn.
            from datetime import datetime, timezone, timedelta
            later = []
            for offset, line in enumerate(index_path.read_text().splitlines(), 1):
                row = json.loads(line)
                row["generated_at"] = (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()
                Path(row["json_path"]).write_text(json.dumps(row))
                later.append(json.dumps(row) + "\n")
            index_path.write_text(prior_runs + "".join(later))
            if capture == "controller":
                from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority
                from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
                rc, listed = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
                assert rc == 0, listed
                initialize_canonical_authority(runtime, GOAL_ID,
                    build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=listed["todos"],
                        handoff_mode="hard_lease", leases=[]),
                    state_path=project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md", provider="sqlite")
            body = next(t for t in texts(payload) if "quota should-run" in t and "```sh" in t)
            guard = next(line for line in body.splitlines() if "quota should-run" in line)
            guard = guard.replace('${LOOPX_TURN:?}', TURN_ID)
            guards.append(guard)
            rendered_guard = ("LOOPX_TURN=" + TURN_ID + "\n" + guard.replace(TURN_ID, "${LOOPX_TURN:?}")) if capture == "native" else guard
            return {"type": "function_call", "id": "fc_midwake", "call_id": "call_midwake",
                    "name": "exec_command", "arguments": json.dumps({"cmd": rendered_guard,
                        "yield_time_ms": 10000, "max_output_tokens": 100})}
        if index == 3 and capture != "off":
            # The resumed source deliberately rereads the same obligation. It
            # must be released without another capture or native interruption.
            return {"type": "function_call", "id": "fc_duplicate", "call_id": "call_duplicate",
                    "name": "exec_command", "arguments": json.dumps({"cmd": guards[0] +
                        "\nprintf continued > continued.txt", "yield_time_ms": 10000,
                        "max_output_tokens": 100})}
        message["content"][0]["text"] = "Source research." if index == 1 else "Continued research."
        return message

    with real_native_source(tmp_path / "provider", os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"], project,
            close_source=True, response_builder=response, sandbox="danger-full-access") as (env, _, _, calls):
        base_url = tomllib.loads((Path(env["CODEX_HOME"]) / "config.toml").read_text())["model_providers"]["fixture"]["base_url"]
        env.update(LOOPX_CLI=str(cli), LOOPX_REGISTRY=str(registry), LOOPX_RUNTIME_ROOT=str(runtime),
                   LOOPX_GOAL_ID=GOAL_ID, LOOPX_AGENT_ID=AGENT_ID, LOOPX_WAKE_LOG_DIR=str(logs),
                   LOOPX_CODEX_HOME=str(tmp_path / "worker-home"), LOOPX_SHARED_SKILLS=str(skills),
                   MODEL_NAME="fixture", REASONING_EFFORT="high", OPENAI_BASE_URL=base_url,
                   OPENAI_API_KEY="synthetic-local-only", LOOPX_CODEX_TURN_TIMEOUT_SEC="60")
        if capture != "off":
            env["LOOPX_CHECKPOINT_REQUEST"] = str(request)
        result = run_once(env)
        assert result["ok"] and not result["timed_out"], result
        assert _heartbeat_receipt_count(runtime, TURN_ID) == 1
        if capture == "off":
            assert "checkpoint_handoffs" not in result
            assert "--decision-handoff-socket" not in guards[0]
            assert not list(logs.glob("*/decisions"))
            return
        assert len(result["checkpoint_handoffs"]) == 1
        event = result["checkpoint_handoffs"][0]
        if json.loads(request.read_text())["native_session"] and sys.platform == "darwin":
            # Unsupported native interruption is refused before any signal;
            # the original tool result is delivered and no abort is fabricated.
            assert not event["captured"] and "not qualified on Darwin" in event["error"]
            assert len(calls) == 4 and (project / "continued.txt").read_text() == "continued"
            assert any(item.get("type") == "function_call_output" and
                       item.get("call_id") == "call_midwake" for item in requests[2]["input"])
            assert not any(item.get("type") == "function_call_output" and
                           "aborted" in item.get("output", "") for item in requests[2]["input"])
            assert not list((logs.parent / "checkpoints").glob("*/checkpoint.json"))
            return
        assert event["captured"] is (capture in {"files", "native", "controller"}), json.dumps(event)
        assert event["research_point_qualified"] is False
        assert len(calls) == 4 and (project / "continued.txt").read_text() == "continued"
        assert "heartbeat_receipt" in json.dumps(requests[2])
        assert any(item.get("type") == "function_call_output" and item.get("call_id") == "call_midwake"
                   and "aborted" in item.get("output", "") for item in requests[2]["input"])
        if capture == "controller":
            (tmp_path / "controller-result.json").write_text(json.dumps(event))
            return  # The external controller verifies the combined artifact.
        if capture in {"files", "native"}:
            point = logs.parent / "checkpoints" / event["obligation_id"]
            assert (point / "components" / "code").read_text() == "original candidate"
            if capture == "native":
                manifest = json.loads((point / "checkpoint.json").read_text())
                native = manifest["point_metadata"]["native_session"]
                assert "completed_turn_id" not in native
                assert native["interrupted_guard"]["native_call_id"] == "call_midwake"
                assert native["interrupted_guard"]["decision_sha256"] == event["decision_sha256"]
                # Reconstruct the earlier captured store after the source has
                # already continued. Fixed guest paths require an isolated home;
                # retain and restore the source bytes around this synthetic test.
                from benchmark.runtime.native_checkpoint import fork_native_checkpoint, admit_native_checkpoint_session
                from benchmark.runtime.codex import Execution
                home = Path(native["guest_home"])
                saved_home = home.with_name(home.name + "-retained")
                config = tmp_path / "captured-config.toml"
                config.write_bytes((home / "config.toml").read_bytes())
                with pytest.raises(ValueError, match="distinct destination"):
                    admit_native_checkpoint_session({**env, "CODEX_HOME": str(home)},
                        point, event["sha256"], config, execution=Execution(context="resume"), timeout=20)
                home.rename(saved_home)
                try:
                    forked = fork_native_checkpoint({**env, "CODEX_HOME": str(home)},
                        point, event["sha256"], config, timeout=20)
                    assert forked["history_verified"] and forked["model_calls"] == 0
                    assert forked["interrupted_turn_id"] == native["interrupted_turn_id"]
                    assert forked["session_id"] != native["session_id"]
                    recovered = fork_native_checkpoint({**env, "CODEX_HOME": str(home)},
                        point, event["sha256"], config, timeout=20)
                    assert recovered == forked  # No second fork after a lost reply.
                finally:
                    if home.exists():
                        home.rename(home.with_name(home.name + "-receiver"))
                    saved_home.rename(home)
        else:
            assert "full-state capture" in event["error"]
            assert not list((logs.parent / "checkpoints").glob("*/checkpoint.json"))
        # The private endpoint belongs to this wake and is gone after readback.
        argv = shlex.split(guards[0])
        assert not Path(argv[argv.index("--decision-handoff-socket") + 1]).exists()
