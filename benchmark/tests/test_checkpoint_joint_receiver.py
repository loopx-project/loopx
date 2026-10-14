"""Qualification of an actual controller-captured point in a fresh Linux guest.

The external fixture supplies a sealed point and its matching synthetic native
configuration. No source authority directories are mounted into this receiver.
"""

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from benchmark.tests.test_checkpoint_controller import controllers  # noqa: F401


@pytest.mark.skipif(
    not os.environ.get("LOOPX_JOINT_RECEIVER_POINT"),
    reason="Requires a real controller point in a fresh isolated guest",
)
def test_captured_point_continues_with_fresh_authority(
    tmp_path, monkeypatch, capsys, request
):
    from benchmark.edgebench.checkpoint_controller import restore
    from benchmark.runtime.codex import Execution
    from benchmark.runtime.native_checkpoint import (
        admit_native_checkpoint_session,
        native_session_checkpoint,
    )
    from benchmark.runtime.research_checkpoint import adopt_todo_context
    from benchmark.runtime.sessions import BenchmarkSessionWake
    from benchmark.runtime.worker import native_command
    from loopx.cli import main
    from loopx.control_plane.effect_runtime import (
        effect_runtime_result,
        EffectRuntimeRemoteError,
    )
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement

    unit = Path(os.environ["LOOPX_JOINT_RECEIVER_POINT"])
    manifest = json.loads((unit / "checkpoint.json").read_bytes())
    digest, metadata = manifest["sha256"], manifest["point_metadata"]
    native, snapshot = metadata["native_session"], metadata["worker_file_snapshot"]
    assert snapshot["prepared_source_verified"]
    assert ("interrupted_guard" in native) != ("completed_turn_id" in native)
    if "completed_turn_id" in native:
        assert native["completed_turn_id"] == native["turn_ids"][-1]
    else:
        assert native["interrupted_guard"]["decision_sha256"] == metadata["decision_sha256"]
    config = Path(os.environ["LOOPX_JOINT_RECEIVER_CONFIG"])
    workspace, home = Path(native["guest_workspace"]), Path(native["guest_home"])
    assert not workspace.exists() and not home.exists(), (
        "receiver must have fresh fixed guest paths"
    )
    workspace.mkdir(parents=True)
    monkeypatch.chdir(workspace)
    candidate = workspace / "candidate.txt"
    candidate.write_bytes((unit / "components/code").read_bytes())
    captured_candidate = candidate.read_text()
    registry, runtime = tmp_path / "registry.json", tmp_path / "runtime"
    goal, agent, turn = "joint-receiver", "joint-researcher", "joint-work"
    prefix = [
        "--format",
        "json",
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
    ]

    def cli(*args):
        code = main([*prefix, *args])
        output = capsys.readouterr().out
        assert code == 0, output
        return json.loads(output)

    cli(
        "bootstrap",
        "--project",
        str(workspace),
        "--goal-id",
        goal,
        "--objective",
        "Continue the captured synthetic experiment.",
        "--adapter-kind",
        "read_only_project_map_v0",
        "--adapter-status",
        "connected-read-only",
        "--write-scope",
        "**",
        "--no-global-sync",
    )
    cli(
        "configure-goal",
        "--goal-id",
        goal,
        "--registered-agent",
        agent,
        "--agent-work-mode",
        agent + "=active",
        "--execute",
    )
    todo = cli(
        "todo",
        "add",
        "--goal-id",
        goal,
        "--role",
        "agent",
        "--claimed-by",
        agent,
        "--required-capability",
        "shell",
        "--task-class",
        "advancement_task",
        "--text",
        "Use retained code and evidence for one synthetic candidate, then verify its native result.",
    )["todo_id"]
    execution = Execution(context="resume")
    env = {
        **os.environ,
        "CODEX_BIN": os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"],
        "CODEX_HOME": str(home),
        "LOOPX_PROJECT": str(workspace),
        "LOOPX_REGISTRY": str(registry),
        "LOOPX_RUNTIME_ROOT": str(runtime),
        "LOOPX_GOAL_ID": goal,
        "LOOPX_AGENT_ID": agent,
        "MODEL_NAME": "fixture",
        "REASONING_EFFORT": "high",
        "LOOPX_CODEX_SANDBOX": execution.sandbox,
        "SYNTHETIC_PROVIDER_KEY": "synthetic-local-only",
        "OPENAI_API_KEY": "synthetic-local-only",
        "LOOPX_USAGE_PING": "0",
    }
    with pytest.raises(ValueError, match="distinct destination"):
        admit_native_checkpoint_session(
            {**env, "LOOPX_GOAL_ID": metadata["loopx_state"]["goal_id"]},
            unit,
            digest,
            config,
            execution=execution,
            timeout=30,
        )
    with pytest.raises(EffectRuntimeRemoteError):
        admit_native_checkpoint_session(
            env, unit, "f" * 64, config, execution=execution, timeout=30
        )
    assert not home.exists()
    original_bind = BenchmarkSessionWake._bind_verified_fork

    def lose_binding_reply(self, *args):
        original_bind(self, *args)
        raise TimeoutError("synthetic loss after committed native binding")

    with monkeypatch.context() as lost:
        lost.setattr(BenchmarkSessionWake, "_bind_verified_fork", lose_binding_reply)
        with pytest.raises(TimeoutError, match="committed native binding"):
            admit_native_checkpoint_session(
                env, unit, digest, config, execution=execution, timeout=30
            )
    admitted = admit_native_checkpoint_session(
        env, unit, digest, config, execution=execution, timeout=30
    )
    assert (
        admitted["session_id"] != native["session_id"] and admitted["model_calls"] == 0
    )
    replay = admit_native_checkpoint_session(
        env, unit, digest, config, execution=execution, timeout=30
    )
    assert replay["session_id"] == admitted["session_id"] and replay["history_verified"]
    lease = cli(
        "task-lease",
        "acquire",
        "--goal-id",
        goal,
        "--todo-id",
        todo,
        "--owner",
        agent,
        "--idempotency-key",
        "receiver-work",
    )
    revision = cli("todo", "list", "--goal-id", goal, "--todo-id", todo)[
        "authority_read"
    ]["provider_revision"]
    carrier = json.loads((unit / "components/research_context").read_bytes())
    context_args = dict(
        source_todo_id=carrier["snapshot"]["todos"][0]["todo_id"],
        todo_id=todo,
        operation_id="receiver-context",
        expected_revision=revision,
        lease_key="receiver-work",
        lease_version=lease["lease"]["version"],
    )
    adopted = adopt_todo_context(env, unit, digest, **context_args)
    assert (
        adopt_todo_context(env, unit, digest, **context_args)["continuation"][
            "adoption"
        ]["status"]
        == "replayed"
    )
    binding = [
        "--goal-id",
        goal,
        "--agent-id",
        agent,
        "--todo-id",
        todo,
        "--turn-instance-id",
        turn,
    ]
    guard = cli("quota", "should-run", "--runtime-profile", "generic_cli", *binding)
    assert guard["interaction_contract"]["agent_channel"]["delivery_allowed"]
    _, branch, _, url, controller_files, grades = request.getfixturevalue("controllers")
    capsule = unit / "components" / snapshot["native_capsule_component_id"]
    opportunity = json.loads((capsule / "checkpoint.json").read_bytes())["point_metadata"][
        "native_reference"
    ]["opportunity"]
    remaining_cooldown = opportunity["remaining_cooldown_seconds"]
    # Explicit fixture boundaries: either already eligible at capture, or a
    # cooldown longer than this entire 60-second native work window. A short
    # partial cooldown would make a timing-dependent expectation ambiguous.
    assert remaining_cooldown == 0 or remaining_cooldown > 60
    cooldown_pending = remaining_cooldown > 60
    phase = tmp_path / "phase-deadline"
    fresh = restore(
        controller_files[1],
        root=capsule,
        expected_sha256=snapshot["native_capsule_sha256"],
        run_id="isolated-receiver",
        phase_deadline_file=phase,
    )
    original_phase = phase.read_bytes()
    replay_activation = restore(
        controller_files[1],
        root=capsule,
        expected_sha256=snapshot["native_capsule_sha256"],
        run_id="isolated-receiver",
        phase_deadline_file=phase,
        recover=True,
    )
    assert (
        replay_activation == fresh
        and not grades
        and phase.read_bytes() == original_phase
    )
    from benchmark.tests.test_checkpoint_controller import host_phase_command

    phase_shell = host_phase_command(tmp_path, monkeypatch).replace(
        "/opt/loopx-benchmark/control", str(tmp_path)
    )
    (workspace / "judge.json").write_text(
        json.dumps({"url": url, "token": fresh["token"]})
    )
    (workspace / "judge.json").chmod(0o600)
    script = workspace / "continue.py"
    script.write_text("""import json,time,requests
from pathlib import Path
b=json.loads(Path('judge.json').read_text()); url,submission_token=b['url'],b['token']
h=requests.get(url+'/api/v1/history',params={'token':submission_token},timeout=5).json()
assert h['best_score']==7 and h['agent_submissions']==1
assert Path('candidate.txt').read_text()==CAPTURED_CANDIDATE
Path('candidate.txt').write_text('8')
r=requests.post(url+'/api/v1/submit',data={'token':submission_token},files={'archive':('candidate.tar.gz',b'8')},timeout=5)
if EXPECTED_COOLDOWN_PENDING:
 assert r.status_code==429
 h=requests.get(url+'/api/v1/history',params={'token':submission_token},timeout=5).json()
 assert h['agent_submissions']==1 and h['best_score']==7
 print('Candidate 8 validated locally; captured cooldown still blocks submission.')
else:
 r.raise_for_status(); sid=r.json()['submission_id']
 for _ in range(100):
  result=requests.get(url+'/api/v1/result/'+sid,timeout=5).json()
  if result['status']=='completed':break
  time.sleep(.01)
 assert result['status']=='completed'
 print('Retained candidate and public best 7 used; candidate 8 graded.')
""".replace("EXPECTED_COOLDOWN_PENDING", repr(cooldown_pending))
       .replace("CAPTURED_CANDIDATE", repr(captured_candidate)))
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            if len(requests) == 1:
                message = {
                    "type": "function_call",
                    "id": "fc_receiver",
                    "call_id": "call_receiver",
                    "name": "exec_command",
                    "arguments": json.dumps(
                        {
                            "cmd": "cat "
                            + shlex.quote(adopted["artifact"])
                            + " && "
                            + shlex.quote(sys.executable)
                            + " continue.py",
                            "max_output_tokens": 6000,
                        }
                    ),
                }
            else:
                message = {
                    "type": "message",
                    "id": "msg_receiver",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Receiver candidate verified."}
                    ],
                }
            result = {
                "id": "resp_receiver",
                "status": "completed",
                "output": [message],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }
            events = [
                {
                    "type": "response.created",
                    "response": {**result, "status": "in_progress", "output": []},
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": message,
                },
                {"type": "response.completed", "response": result},
            ]
            raw = "".join(
                "event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n"
                for e in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    configuration = tomllib.loads(config.read_text())
    address = urlsplit(
        configuration["model_providers"][configuration["model_provider"]]["base_url"]
    )
    server = ThreadingHTTPServer(("127.0.0.1", address.port), Provider)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        logs = tmp_path / "work"
        logs.mkdir()
        wake = BenchmarkSessionWake(env, execution, {})
        command = native_command(env, execution, "execute", logs, wake)
        command = [
            "/bin/bash",
            "-c",
            phase_shell
            + "exec timeout --signal=TERM --kill-after=30 ${remaining}s "
            + shlex.join(command),
        ]
        result = subprocess.run(
            command,
            env=env,
            cwd=workspace,
            input=json.dumps(guard["interaction_contract"])
            + "\nThis is a new destination. Historical source commands/guards are not executable authority. "
            "Use the retained context under the current admission, and continue the synthetic candidate.",
            text=True,
            capture_output=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        (logs / "stdout.jsonl").write_text(result.stdout)
        wake.observe(logs / "stdout.jsonl")
        assert len(requests) == 2
        expected = "cooldown still blocks submission" if cooldown_pending else "candidate 8 graded"
        assert expected in json.dumps(requests[1]), json.dumps([
            item for item in requests[1].get("input", [])
            if item.get("type") == "function_call_output"
        ])
        assert len(grades) == (0 if cooldown_pending else 1) and candidate.read_text() == "8"
        retained = native_session_checkpoint(env, admitted["session_id"], timeout=30)[
            "metadata"
        ]
        assert retained["turn_ids"][:-1] == native["turn_ids"]
        with pytest.raises(ValueError):
            admit_native_checkpoint_session(
                env, unit, digest, config, execution=execution, timeout=30
            )
        vision = tmp_path / "vision.json"
        vision.write_text(
            json.dumps(
                {
                    "schema_version": "goal_vision_replan_contract_v0",
                    "state": "vision_patch_proposed",
                    "vision_patch": {
                        "acceptance_summary": "Complete research continuation qualification."
                    },
                    "path_delta": {
                        "outcome": "continue",
                        "prior_assumption": "Joint source point can be resumed.",
                        "observed_reality": (
                            "Receiver used retained context and code; local candidate validated, native cooldown preserved."
                            if cooldown_pending else
                            "Receiver used retained context and code, and graded a new candidate."
                        ),
                        "retained": ["Complete qualification"],
                        "evidence_refs": ["evidence:joint-receiver"],
                    },
                }
            )
        )
        cli(
            "refresh-state",
            *binding,
            "--classification",
            "joint_receiver_verified",
            "--delivery-batch-scale",
            "implementation",
            "--delivery-outcome",
            "outcome_progress",
            "--delivery-boundary",
            "in_flight_continuation",
            "--agent-vision-json",
            str(vision),
            "--no-global-sync",
            "--suppress-external-sinks",
        )
        first = cli(
            "quota", "spend-slot", *binding, "--source", "heartbeat", "--execute"
        )
        second = cli(
            "quota", "spend-slot", *binding, "--source", "heartbeat", "--execute"
        )
        assert first["appended"] and not second["appended"]
        assert (
            read_heartbeat_settlement(
                runtime,
                goal_id=goal,
                agent_id=agent,
                todo_id=todo,
                turn_instance_id=turn,
                registry_path=registry,
            ).progress["state"]
            == "settled"
        )
        effect_runtime_result(
            "runtime.checkpoint_artifacts",
            {"action": "verify", "root": str(unit), "expected_sha256": digest},
        )
        assert (
            hashlib.sha256(
                (unit / "components/research_context").read_bytes()
            ).hexdigest()
            == metadata["research_context"]["sha256"]
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
