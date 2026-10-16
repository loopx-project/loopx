"""Qualification of an actual controller-captured point in a fresh Linux guest.

The external fixture supplies a sealed point and its matching synthetic native
configuration. No source authority directories are mounted into this receiver.
"""

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
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
    assert os.getuid() != 0, "Workspace continuation must qualify the actual non-root writer"
    from benchmark.edgebench.checkpoint_controller import restore
    from benchmark.runtime.codex import Execution
    from loopx.hosts.codex_checkpoint import (
        admit_native_checkpoint_session,
        native_session_checkpoint,
    )
    from loopx.hosts.research_checkpoint import adopt_todo_context
    from benchmark.runtime.sessions import BenchmarkSessionWake
    from benchmark.runtime.worker import native_command
    from loopx.cli import main
    from loopx.control_plane.effect_runtime import (
        effect_runtime_result,
        EffectRuntimeRemoteError,
    )
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement

    # Each variant uses its own fresh guest because the native fork retains
    # fixed captured workspace/home paths. The default variant stays unchanged.
    stride_continuation = os.environ.get("LOOPX_JOINT_RECEIVER_STRIDE") == "1"
    unit = Path(os.environ["LOOPX_JOINT_RECEIVER_POINT"])
    manifest = json.loads((unit / "checkpoint.json").read_bytes())
    digest, metadata = manifest["sha256"], manifest["point_metadata"]
    native, snapshot = metadata["native_session"], metadata["worker_file_snapshot"]
    assert snapshot["prepared_source_verified"]
    assert ("interrupted_guard" in native) != ("completed_turn_id" in native)
    if "completed_turn_id" in native:
        assert native["completed_turn_id"] == native["turn_ids"][-1]
    else:
        assert (
            native["interrupted_guard"]["decision_sha256"]
            == metadata["decision_sha256"]
        )
    config = Path(os.environ["LOOPX_JOINT_RECEIVER_CONFIG"])
    workspace, home = Path(native["guest_workspace"]), Path(native["guest_home"])
    assert not workspace.exists() and not home.exists(), (
        "receiver must have fresh fixed guest paths"
    )
    # Select the sealed declared workspace by its exact source path. Actual
    # workers declare a directory with caller-owned IDs, not a single code file.
    roots = [entry for entry in snapshot["components"]
             if entry["source_path"] == str(workspace)]
    assert len(roots) == 1, "Requires one declared full native workspace"
    materialized = tmp_path / "materialized-point"
    verified = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "materialize", "root": str(unit), "expected_sha256": digest,
        "destination": str(materialized),
    })
    assert verified["materialized"] and not verified["activation_performed"]
    source_workspace = materialized / "components" / roots[0]["id"]
    assert source_workspace.is_dir()
    shutil.copytree(source_workspace, workspace)
    monkeypatch.chdir(workspace)
    candidate = workspace / "candidate.txt"
    captured_candidate = candidate.read_text()
    # Retained source registry/state files are historical inputs. Current CLI
    # authority must use a fresh registry/runtime outside this copied tree.
    historical_registry = workspace / ".loopx/registry.json"
    historical_bytes = historical_registry.read_bytes()
    historical = json.loads(historical_bytes)
    original_runtime = Path(historical["common_runtime_root"])
    assert not original_runtime.exists()
    source_goal = metadata["loopx_state"]["goal_id"]
    # A later generation may retain an even older project-local registry.
    # The source's separately sealed canonical registry owns its current Goal;
    # neither registry becomes destination authority through workspace copying.
    registry_basis = metadata["loopx_state"]
    captured_registry = unit / "components" / registry_basis["registry_component_id"]
    if registry_basis["registry_member_path"]:
        captured_registry = captured_registry / registry_basis["registry_member_path"]
    captured_registry_bytes = captured_registry.read_bytes()
    assert hashlib.sha256(captured_registry_bytes).hexdigest() == registry_basis["registry_sha256"]
    assert source_goal in {row["id"] for row in json.loads(captured_registry_bytes)["goals"]}
    assert (workspace / "evidence-6.json").read_bytes() == (source_workspace / "evidence-6.json").read_bytes()
    retained_object = workspace / ".git/objects/ab/retained-object"
    assert retained_object.read_text() == "Readonly source evidence"
    assert retained_object.stat().st_mode & 0o777 == 0o444
    registry, runtime = tmp_path / "registry.json", tmp_path / "runtime"
    goal = os.environ.get("LOOPX_JOINT_RECEIVER_GOAL", "joint-receiver")
    agent = os.environ.get("LOOPX_JOINT_RECEIVER_AGENT", "joint-researcher")
    turn = "joint-work"
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
        *(
            [
                "--execution-stride-strategy",
                "evidence_conditioned_v1",
                "--execution-replan-after-turns",
                "1",
                "--explore-mode",
                "planning",
            ]
            if stride_continuation
            else []
        ),
        "--execute",
    )
    assert historical_registry.read_bytes() == historical_bytes
    assert json.loads(registry.read_bytes())["goals"][0]["id"] == goal
    assert runtime != original_runtime and not original_runtime.exists()
    rejected = main([*prefix, "quota", "should-run", "--goal-id", source_goal,
                     "--agent-id", agent, "--turn-instance-id", "foreign-source"])
    capsys.readouterr()
    assert rejected != 0, "Source Goal must not acquire destination authority"
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
            sandbox=(execution).sandbox,
            timeout=30,
        )
    with pytest.raises(EffectRuntimeRemoteError):
        admit_native_checkpoint_session(
            env, unit, "f" * 64, config, sandbox=(execution).sandbox, timeout=30
        )
    assert not home.exists()
    from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
    original_bind = CodexAgentSession._bind_verified_fork

    def lose_binding_reply(self, *args):
        original_bind(self, *args)
        raise TimeoutError("synthetic loss after committed native binding")

    with monkeypatch.context() as lost:
        lost.setattr(CodexAgentSession, "_bind_verified_fork", lose_binding_reply)
        with pytest.raises(TimeoutError, match="committed native binding"):
            admit_native_checkpoint_session(
                env, unit, digest, config, sandbox=(execution).sandbox, timeout=30
            )
    admitted = admit_native_checkpoint_session(
        env, unit, digest, config, sandbox=(execution).sandbox, timeout=30
    )
    assert (
        admitted["session_id"] != native["session_id"] and admitted["model_calls"] == 0
    )
    replay = admit_native_checkpoint_session(
        env, unit, digest, config, sandbox=(execution).sandbox, timeout=30
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
    from loopx.capabilities.explore.result_log import explore_result_log_path, load_explore_result_events_strict
    source_events = carrier["research_evidence"]["explore_events"]
    assert len(source_events) == 2 and adopted["explore_knowledge"]["appended_event_count"] == 2
    imported_log = explore_result_log_path(runtime, goal)
    imported_bytes = imported_log.read_bytes()
    imported = load_explore_result_events_strict(imported_log, goal_id=goal)
    assert all(row["run_id"].startswith("checkpoint:" + digest + ":") for row in imported)
    assert all("research_observation" not in row for row in imported)
    assert (
        adopt_todo_context(env, unit, digest, **context_args)["continuation"][
            "adoption"
        ]["status"]
        == "replayed"
    )
    assert imported_log.read_bytes() == imported_bytes
    if stride_continuation:
        knowledge_read = cli("explore", "turn-context", "--goal-id", goal, "--agent-id", agent)
        inherited = knowledge_read["graph"]["writeback_results"][0]
        assert inherited["finding_id"] == "restart-counterexample" and inherited["status"] == "refuted"
        assert inherited["summary"] == source_events[-1]["summary"]
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
    external_path = os.environ.get("LOOPX_JOINT_RECEIVER_NATIVE")
    external = json.loads(Path(external_path).read_bytes()) if external_path else None
    if external:
        # Operator credentials and the native bank stay outside the guest.
        # Only this destination's public worker registration enters it.
        assert stride_continuation
        url, fresh, grades = external["url"], external["activation"], []
    else:
        _, branch, _, url, controller_files, grades = request.getfixturevalue("controllers")
    capsule = unit / "components" / snapshot["native_capsule_component_id"]
    opportunity = json.loads((capsule / "checkpoint.json").read_bytes())[
        "point_metadata"
    ]["native_reference"]["opportunity"]
    remaining_cooldown = opportunity["remaining_cooldown_seconds"]
    # Explicit fixture boundaries: either already eligible at capture, or a
    # cooldown longer than this entire 60-second native work window. A short
    # partial cooldown would make a timing-dependent expectation ambiguous.
    assert remaining_cooldown == 0 or remaining_cooldown > 60
    cooldown_pending = remaining_cooldown > 60
    if stride_continuation and not cooldown_pending:
        pytest.skip(
            "Stride receiver qualification requires a captured pending cooldown"
        )
    phase = tmp_path / "phase-deadline"
    if external:
        phase.write_text(external["phase_deadline"])
        original_phase = phase.read_bytes()
        actual_phase = Path("/opt/loopx-benchmark/control/phase-deadline")
        actual_phase.parent.mkdir(parents=True, exist_ok=True)
        actual_phase.write_bytes(original_phase)
    else:
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
    judge = tmp_path / "judge.json"
    judge.write_text(
        json.dumps({"url": url, "token": fresh["token"]})
    )
    judge.chmod(0o600)
    script = workspace / "continue.py"
    if stride_continuation:
        script.write_text(
            """import json,requests
from pathlib import Path
b=json.loads(Path(JUDGE).read_text()); url,submission_token=b['url'],b['token']
h=requests.get(url+'/api/v1/history',params={'token':submission_token},timeout=5).json()
assert h['best_score']==7 and h['agent_submissions']==1
assert Path('candidate.txt').read_text()==CAPTURED_CANDIDATE
source=json.loads(Path(CONTEXT).read_text())
assert source
Path('restoration-evidence.json').write_text(json.dumps({'code_unchanged':True,'public_incumbent':7,'consumed_submissions':1,'source_context_read':True}))
print('Restored state verified; source code unchanged, incumbent and consumed opportunities retained.')
""".replace("JUDGE", repr(str(judge))).replace("CAPTURED_CANDIDATE", repr(captured_candidate)).replace(
                "CONTEXT", repr(adopted["artifact"])
            )
        )
    else:
        script.write_text(
            """import json,time,requests
from pathlib import Path
b=json.loads(Path(JUDGE).read_text()); url,submission_token=b['url'],b['token']
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
""".replace("JUDGE", repr(str(judge))).replace("EXPECTED_COOLDOWN_PENDING", repr(cooldown_pending)).replace(
                "CAPTURED_CANDIDATE", repr(captured_candidate)
            )
        )
    requests = []
    provider_phase = {
        "count": 0,
        "command": "cat "
        + shlex.quote(adopted["artifact"])
        + " && "
        + shlex.quote(sys.executable)
        + " continue.py",
    }

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            provider_phase["count"] += 1
            if provider_phase["count"] == 1:
                message = {
                    "type": "function_call",
                    "id": "fc_receiver",
                    "call_id": "call_receiver",
                    "name": "exec_command",
                    "arguments": json.dumps(
                        {
                            "cmd": provider_phase["command"],
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

    def run_native_work(current_guard, logs, purpose):
        logs.mkdir()
        wake = BenchmarkSessionWake(env, execution, {})
        command = native_command(env, execution, "execute", logs, wake)
        result = subprocess.run(
            [
                "/bin/bash",
                "-c",
                phase_shell
                + "exec timeout --signal=TERM --kill-after=30 ${remaining}s "
                + shlex.join(command),
            ],
            env=env,
            cwd=workspace,
            input=json.dumps(current_guard["interaction_contract"]) + "\n" + purpose,
            text=True,
            capture_output=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        (logs / "stdout.jsonl").write_text(result.stdout)
        wake.observe(logs / "stdout.jsonl")
        return wake

    try:
        run_native_work(
            guard,
            tmp_path / "work",
            "This is a new destination. Historical source commands/guards are not executable authority. "
            "Use the retained context under the current admission, and continue the synthetic candidate.",
        )
        assert len(requests) == 2
        expected = (
            "Restored state verified"
            if stride_continuation
            else "cooldown still blocks submission"
            if cooldown_pending
            else "candidate 8 graded"
        )
        assert expected in json.dumps(requests[1]), json.dumps(
            [
                item
                for item in requests[1].get("input", [])
                if item.get("type") == "function_call_output"
            ]
        )
        assert len(grades) == (0 if stride_continuation or cooldown_pending else 1)
        assert candidate.read_text() == (
            captured_candidate if stride_continuation else "8"
        )
        retained = native_session_checkpoint(env, admitted["session_id"], timeout=30)[
            "metadata"
        ]
        assert retained["turn_ids"][:-1] == native["turn_ids"]
        with pytest.raises(ValueError):
            admit_native_checkpoint_session(
                env, unit, digest, config, sandbox=(execution).sandbox, timeout=30
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
                            "Receiver verified retained context and unchanged code; native opportunity preserved."
                            if stride_continuation
                            else "Receiver used retained context and code; local candidate validated, native cooldown preserved."
                            if cooldown_pending
                            else "Receiver used retained context and code, and graded a new candidate."
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
        if stride_continuation:
            # An explicit one-Turn preparation cadence admits a fresh periodic
            # review. Keep the original source's six work settlements inert.
            review_turn = "joint-current-review"
            from benchmark.runtime.checkpoint import capture_before_replan
            from benchmark.runtime.checkpoint_continuation import restored_capture_gate
            from benchmark.runtime.checkpoint_wake import CheckpointWake

            wakes = Path("/logs/agent/wakes") if external else tmp_path / "capture-logs" / "wakes"
            (wakes / review_turn).mkdir(parents=True)
            executable = tmp_path / "loopx-cli"
            executable.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' -m loopx.cli "$@"\n')
            executable.chmod(0o700)
            declaration = tmp_path / "capture-request.json"
            declaration.write_text(json.dumps({"trigger": "fresh_replan",
                "destination": str(wakes.parent / "checkpoints"), "native_session": True,
                "loopx_state": True, "components": [{"id": "code", "source_path": str(candidate)}],
                "point_metadata": {}}))
            env.update(LOOPX_CLI=str(executable), LOOPX_WAKE_LOG_DIR=str(wakes),
                LOOPX_CHECKPOINT_REQUEST=str(declaration))
            pending_receipt = {"mode": "heartbeat", "stage": "execute", "turn_id": review_turn}
            pending_body = capture_before_replan(env, pending_receipt, "Current destination contract.")
            assert pending_receipt["checkpoint"]["reason"] == "restored_replan_pending", pending_receipt
            assert not pending_receipt["checkpoint"]["captured"]
            assert "Original pending packet" in pending_body and "checkpoint:" + digest in pending_body
            # Both stop locations share the same gate. Repeated/restarted
            # capture attempts neither interrupt the fork nor publish a point.
            for _ in range(2):
                gate = restored_capture_gate(env)
                assert not gate["capture_allowed"]
                hook = CheckpointWake(env, dict(pending_receipt))
                assert not hook._first_replan({"capture": True, "obligation_id": "replan-new-destination"})
            assert not (wakes.parent / "checkpoints").exists()
            reviewed = cli(
                "quota",
                "should-run",
                "--goal-id",
                goal,
                "--agent-id",
                agent,
                "--turn-instance-id",
                review_turn,
                "--runtime-profile",
                "generic_cli",
                "--available-capability",
                "shell",
            )
            assert reviewed["interaction_contract"]["mode"] == "autonomous_replan"
            obligation = reviewed["autonomous_replan_obligation"]
            assert any(
                t["kind"] == "periodic_review_due" for t in obligation["triggers"]
            ), obligation
            reviewed = cli(
                "quota",
                "should-run",
                "--goal-id",
                goal,
                "--agent-id",
                agent,
                "--todo-id",
                todo,
                "--turn-instance-id",
                review_turn,
                "--runtime-profile",
                "generic_cli",
                "--available-capability",
                "shell",
            )
            control = reviewed["replan_action_packet"]["stride_control"]
            decision = json.loads(json.dumps(control["minimal_example"]))
            decision.update(
                basis="supported_progress",
                action="grow",
                axes={
                    "work_slice": {
                        "plan": "Complete candidates 9 and 10 with local evidence in one work wake.",
                        "reason": "One real recovery check verified retained code, research context and public opportunity.",
                    },
                    "route_distance": {
                        "plan": "Retain the synthetic increment method.",
                        "reason": "This qualifies restoration and decision adoption, not scientific strategy quality.",
                    },
                    "validation_scope": {
                        "plan": "Check both candidate evidence files, final value 10 and unchanged native opportunity.",
                        "reason": "A complete larger work unit still needs its full postcondition.",
                    },
                },
                next_probe={
                    "scope": "One two-candidate synthetic continuation.",
                    "prediction": "Both candidates are validated locally; captured cooldown remains binding.",
                    "adoption_criterion": "Evidence files and final value agree without resetting opportunity.",
                    "cost_bound": "One native work wake under the original remaining deadline.",
                    "minimum_evidence": "Actual native tool output, candidate bytes, current settlement and original native history.",
                },
            )
            if "explore_review" in decision:
                assert any(row["finding_id"] == "restart-counterexample" for row in control["explore_basis"]["finding_refs"])
                decision["explore_review"].update(
                    disposition="used",
                    considered_refs=["restart-counterexample"],
                    reason="The inherited scoped counterexample rules out restarting at one in this synthetic fixture; retain six and continue.",
                )
            identity = reviewed["heartbeat_receipt"]["settlement_identity"]
            review_binding = (
                [
                    "--goal-id",
                    goal,
                    "--agent-id",
                    agent,
                    "--turn-instance-id",
                    review_turn,
                    "--todo-id",
                    identity["todo_id"],
                ]
                if identity.get("todo_id")
                else [
                    "--goal-id",
                    goal,
                    "--agent-id",
                    agent,
                    "--turn-instance-id",
                    review_turn,
                    "--replan-obligation-id",
                    identity["replan_obligation_id"],
                ]
            )
            review_vision = tmp_path / "review-vision.json"
            review_vision.write_text(
                json.dumps(
                    {
                        "schema_version": "goal_vision_replan_contract_v0",
                        "state": "vision_patch_proposed",
                        "vision_patch": {
                            "acceptance_summary": "A current review must lead to a complete native experiment and preserve remaining opportunity."
                        },
                        "path_delta": {
                            "outcome": "replan",
                            "prior_assumption": "A restored local candidate is only a continuation prerequisite.",
                            "observed_reality": "An actual destination recovery check verified unchanged code, retained research and native opportunity.",
                            "changed": [
                                "Adopt a complete two-candidate work slice under current destination authority."
                            ],
                            "evidence_refs": ["evidence:restoration-check", "checkpoint:" + digest],
                        },
                        "stride_decision": decision,
                    }
                )
            )
            cli(
                "refresh-state",
                *review_binding,
                "--autonomous-replan-recorded",
                "--classification",
                "joint_stride_review",
                "--delivery-outcome",
                "outcome_progress",
                "--agent-vision-json",
                str(review_vision),
                "--no-global-sync",
                "--suppress-external-sinks",
            )
            # Interruption/lost delivery after the accepted writeback still
            # owes this original Turn's settlement, so recapture stays closed.
            unconsumed = restored_capture_gate(env)
            assert not unconsumed["capture_allowed"]
            original_packet = unit / "components" / metadata["held_decision_component_id"]
            assert str(original_packet) in unconsumed["instructions"]
            assert str(wakes / review_turn / "checkpoint-start-guard.json") not in unconsumed["instructions"]
            cli(
                "quota",
                "spend-slot",
                *review_binding,
                "--source",
                "heartbeat",
                "--execute",
            )
            reopened = restored_capture_gate(env)
            assert reopened["capture_allowed"], reopened
            from benchmark.runtime.checkpoint_continuation import restored_checkpoint_review
            review_only = restored_checkpoint_review(env)
            assert review_only["semantic_review_accepted"] and "capture_allowed" not in review_only
            assert review_only["review_basis"]["identity"]["turn_instance_id"] == review_turn
            assert not reopened["instructions"]  # Do not repeat the historical packet after consumption.
            assert not restored_capture_gate(env, obligation["obligation_id"])["capture_allowed"]
            configured = cli(
                "configure-goal",
                "--goal-id",
                goal,
                "--execution-replan-after-turns",
                "1" if external else "6",
                "--execute",
            )
            assert (
                configured["after"]["execution_profile"]["replan_after_effective_turns"]
                == (1 if external else 6)
            )
            larger = todo
            revision = cli("todo", "list", "--goal-id", goal, "--todo-id", todo)[
                "authority_read"
            ]["provider_revision"]
            cli(
                "todo",
                "update",
                "--goal-id",
                goal,
                "--todo-id",
                todo,
                "--agent-id",
                agent,
                "--text",
                "Complete synthetic candidates 9 and 10 as one larger experiment and preserve native opportunity.",
                "--update-operation-id",
                "receiver-grow-task",
                "--update-expected-provider-revision",
                revision,
                "--task-lease-idempotency-key",
                "receiver-work",
                "--task-lease-expected-version",
                str(lease["lease"]["version"]),
            )
            next_turn = "wake-joint-larger-work"
            next_binding = [
                "--goal-id",
                goal,
                "--agent-id",
                agent,
                "--todo-id",
                larger,
                "--turn-instance-id",
                next_turn,
            ]

            next_guard = cli(
                "quota",
                "should-run",
                *next_binding,
                "--runtime-profile",
                "generic_cli",
                "--available-capability",
                "shell",
            )
            assert next_guard["interaction_contract"]["agent_channel"][
                "delivery_allowed"
            ]
            assert "Complete synthetic candidates 9 and 10" in json.dumps(next_guard), (
                next_guard
            )
            grow = workspace / "grow.py"
            grow.write_text(
                """import json,requests
from pathlib import Path
assert Path('candidate.txt').read_text()==CAPTURED_CANDIDATE
b=json.loads(Path(JUDGE).read_text()); url,submission_token=b['url'],b['token']
for value in (9,10):
 Path('candidate.txt').write_text(str(value))
 assert int(Path('candidate.txt').read_text())==value
 Path('candidate-evidence-'+str(value)+'.json').write_text(json.dumps({'value':value,'locally_validated':True}))
r=requests.post(url+'/api/v1/submit',data={'token':submission_token},files={'archive':('candidate.tar.gz',b'10')},timeout=5)
assert r.status_code==429
h=requests.get(url+'/api/v1/history',params={'token':submission_token},timeout=5).json()
assert h['agent_submissions']==1 and h['best_score']==7
print('Current grow adopted: two complete candidates 9 and 10 validated; original cooldown and incumbent preserved.')
    """.replace("JUDGE", repr(str(judge))).replace("CAPTURED_CANDIDATE", repr(captured_candidate))
            )
            assert cooldown_pending, (
                "This qualification specifically requires the captured pending cooldown."
            )
            provider_phase.update(
                count=0, command=shlex.quote(sys.executable) + " grow.py"
            )
            larger_wake = run_native_work(
                next_guard,
                tmp_path / "larger-work",
                "Execute the current grow decision as one complete two-candidate experiment. Old guards are evidence only.",
            )
            assert len(requests) == 4 and "Current grow adopted" in json.dumps(
                requests[3]
            ), requests[3]
            assert (
                candidate.read_text() == "10"
                and not grades
                and phase.read_bytes() == original_phase
            )
            assert all(
                json.loads((workspace / f"candidate-evidence-{v}.json").read_text())[
                    "value"
                ]
                == v
                for v in (9, 10)
            )
            active_session_id = larger_wake.receipt["session"]["session_id"]
            grown = native_session_checkpoint(env, active_session_id, timeout=30)[
                "metadata"
            ]
            assert active_session_id == admitted["session_id"]
            assert grown["turn_ids"][:-2] == native["turn_ids"]
            assert len(grown["turn_ids"]) == len(native["turn_ids"]) + 2
            work_vision = tmp_path / "larger-vision.json"
            work_vision.write_text(
                json.dumps(
                    {
                        "schema_version": "goal_vision_replan_contract_v0",
                        "state": "vision_patch_proposed",
                        "vision_patch": {
                            "acceptance_summary": "Mechanism adoption is qualified; scientific outcome and full costs remain unproven."
                        },
                        "path_delta": {
                            "outcome": "continue",
                            "prior_assumption": "The larger decision may remain unused guidance.",
                            "observed_reality": "The continued native session executed both candidates with separate evidence and retained the pending cooldown and incumbent.",
                            "retained": [
                                "Native opportunity, immutable source and current authority separation."
                            ],
                            "evidence_refs": ["evidence:joint-larger-work"],
                        },
                    }
                )
            )
            cli(
                "refresh-state",
                *next_binding,
                "--classification",
                "joint_stride_adoption",
                "--delivery-batch-scale",
                "implementation",
                "--delivery-outcome",
                "outcome_progress",
                "--delivery-boundary",
                "in_flight_continuation",
                "--progress-result-class",
                "advanced",
                "--progress-surface-id",
                "receiver:two-candidate-work",
                "--progress-evidence-id",
                "evidence:joint-larger-work",
                "--agent-vision-json",
                str(work_vision),
                "--no-global-sync",
                "--suppress-external-sinks",
            )
            cli(
                "quota",
                "spend-slot",
                *next_binding,
                "--source",
                "heartbeat",
                "--execute",
            )
            if not external:
                final = cli(
                    "quota",
                    "should-run",
                    "--goal-id",
                    goal,
                    "--agent-id",
                    agent,
                    "--turn-instance-id",
                    "joint-next-audit",
                    "--runtime-profile",
                    "generic_cli",
                    "--available-capability",
                    "shell",
                )
                assert final["interaction_contract"]["mode"] == "bounded_delivery"
            # The next genuinely new decision boundary can capture changed
            # research/code/native history, after actual useful continuation.
            # This partial point deliberately makes no evaluator qualification.
            prior_wake = wakes / next_turn
            prior_wake.mkdir()
            (prior_wake / "receipt.json").write_text(json.dumps({"turn_id": next_turn,
                "mode": "heartbeat", "stage": "execute", "ok": True,
                "session": {"binding_scope": "agent", "action": "resume", "session_id": active_session_id}}))
            if not external:
                cli("configure-goal", "--goal-id", goal, "--execution-replan-after-turns", "1", "--execute")
            else:
                declaration.write_text(json.dumps({"trigger": "fresh_replan", "destination": str(wakes.parent / "checkpoints"),
                    "native_session": True, "loopx_state": True, "evaluation_opportunity": True, "controller_handoff": True,
                    "components": [{"id": "task_workspace", "source_path": str(workspace)}], "point_metadata": {}}))
            boundary_turn = "joint-next-capture"
            (wakes / boundary_turn).mkdir()
            boundary = {"turn_id": boundary_turn, "mode": "heartbeat", "stage": "execute", "ok": False}
            capture_before_replan(env, boundary, "Current new decision contract.")
            (tmp_path / "automatic-capture.json").write_text(json.dumps(boundary))
            assert boundary["checkpoint_restore"].get("native_continuation_cursor") == grown["completed_turn_id"], boundary
            if external:
                assert boundary["checkpoint"]["captured"], json.dumps(boundary, indent=2)
                assert not boundary["checkpoint"]["research_point_qualified"]  # Artifact integrity is not scientific qualification.
                published = Path(os.environ["LOOPX_JOINT_RECAPTURE_ROOT"])
                matching = [p for p in published.iterdir() if p.is_dir() and (p / "checkpoint.json").exists()
                    and json.loads((p / "checkpoint.json").read_bytes())["sha256"] == boundary["checkpoint"]["sha256"]]
                assert len(matching) == 1, "The sealed reply must select exactly one immutable point"
                recaptured = matching[0]
                captured = json.loads((recaptured / "checkpoint.json").read_bytes())
                cm = captured["point_metadata"]
                assert cm["held_decision_component_id"] and cm["replan"]["obligation_id"] != obligation["obligation_id"]
                assert cm["native_session"]["turn_ids"] == grown["turn_ids"]
                assert (recaptured / "components/task_workspace/candidate.txt").read_text() == "10"
                assert cm["session"]["session_id"] == admitted["session_id"]
                nested = recaptured / "components" / cm["worker_file_snapshot"]["native_capsule_component_id"]
                native_point = json.loads((nested / "checkpoint.json").read_bytes())["point_metadata"]
                assert (nested / "components/native_judge/incumbent.tar.gz").read_bytes() == b"7"
                opportunity_after = native_point["native_reference"]["opportunity"]
                assert {k: v for k, v in opportunity_after.items() if k != "remaining_cooldown_seconds"} == {k: v for k, v in opportunity.items() if k != "remaining_cooldown_seconds"}
                assert opportunity_after["remaining_cooldown_seconds"] <= opportunity["remaining_cooldown_seconds"]
                assert actual_phase.read_bytes() == original_phase
                check = effect_runtime_result("runtime.checkpoint_artifacts", {
                    "action": "verify", "root": str(recaptured), "expected_sha256": captured["sha256"]})
                assert check["integrity_verified"] and not check["research_point_qualified"]
                (tmp_path / "full-recapture.json").write_text(json.dumps(boundary["checkpoint"]))
            elif boundary["checkpoint"].get("reason") == "no_fresh_replan":
                # Capture eligibility does not manufacture a quota obligation.
                # This source may not have another due review yet.
                assert boundary["checkpoint_restore"]["capture_allowed"]
            else:
                assert boundary["checkpoint"]["captured"], json.dumps(boundary, indent=2)
                recaptured = Path(boundary["checkpoint"]["destination"])
                assert (recaptured / "components/code").read_text() == "10"
                next_metadata = json.loads((recaptured / "checkpoint.json").read_text())["point_metadata"]
                assert next_metadata["replan"]["obligation_id"] != obligation["obligation_id"]
                assert next_metadata["held_decision_component_id"]
                assert next_metadata["native_session"]["turn_ids"] == grown["turn_ids"]
            if not external:
                from benchmark.runtime.checkpoint import capture_after_wake
                manual = wakes.parent / "checkpoints" / "refreshed-continuation"
                manual_request = tmp_path / "manual-capture-request.json"
                manual_request.write_text(json.dumps({"destination": str(manual), "native_session": True,
                    "components": [{"id": "code", "source_path": str(candidate)}], "point_metadata": {}}))
                capture_env = {**env, "LOOPX_CHECKPOINT_REQUEST": str(manual_request)}
                completed = {"ok": True, "turn_id": next_turn, "mode": "heartbeat", "stage": "execute",
                    "session": {"session_id": active_session_id, "binding_scope": "agent", "action": "resume"}}
                capture_after_wake(capture_env, completed)
                assert completed["checkpoint"]["captured"], completed
                captured = json.loads((manual / "checkpoint.json").read_text())
                assert captured["point_metadata"]["native_session"]["turn_ids"] == grown["turn_ids"]
                assert (manual / "components/code").read_text() == "10"
                verified_point = effect_runtime_result("runtime.checkpoint_artifacts", {
                    "action": "verify", "root": str(manual), "expected_sha256": captured["sha256"]})
                assert verified_point["integrity_verified"] and not verified_point["research_point_qualified"]
                capture_after_wake(capture_env, completed)
                assert completed["checkpoint"]["reason"] == "destination_already_present_no_replay"
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
