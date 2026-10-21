"""Public CLI and detached wake with the actual opt-in SDK/runtime.

The only provider is loopback Messages SSE. Fixture confirmation authenticates
a no-effect qualification operation; it is never live financial authority.
"""

import json
import hashlib
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from examples import operation_action_fixtures as fixtures
from loopx.chat_action_store import ChatActionStore
from loopx.control_plane.collaboration.operation_wake import (
    dispatch_confirmed_operation_wake,
)
from loopx.control_plane.turn_driver.codex_sessions import load_codex_cli_session
from test_local_delegation import service as delegation_service, wait
from tests.control_plane.test_dsh_operation_sdk import prepared_request_ref

service = delegation_service


@pytest.mark.parametrize("request_transport", ["inline", "frozen"])
@pytest.mark.parametrize("original_owner", [False, True], ids=["peer", "original-owner"])
def test_public_dsh_operation_entry_and_confirmed_callback(service, monkeypatch, original_owner, request_transport):
    pytest.importorskip("deepseek_harness")
    module = os.environ.get("DSH_NATIVE_OPERATION_MODULE")
    runtime = os.environ.get("DSH_NATIVE_OPERATION_RUNTIME")
    if not module or not runtime:
        pytest.skip("explicit built operation module and resume-capable runtime required")
    root, runner = service
    store = ChatActionStore(runner.root / "chat" / "actions")
    intent = fixtures.request(
        goal_id=runner.goal_id,
        payload={"schema_version": "qualification_v0", "marker": "no-external-effect"},
    )
    terms = intent["normalized_parameters"]
    terms.pop("executor")
    terms.update(agent_id="analyst", domain="qualification",
                 operation_kind="qualification.observe", operation_schema="qualification_v0")
    terms["projection"]["simulated"] = False
    steps, errors, responses = [], [], []

    def operation():
        rows = store.list(goal_id=runner.goal_id)
        assert len(rows) == 1
        return rows[0]

    def arguments(index):
        if index == 0:
            return {"action": "context"}
        if index == 1:
            if request_transport == "frozen":
                args = {"action": "prepare", "request": prepared_request_ref(responses[-1])}
                assert len(json.dumps(args)) < 150
                return args
            return {"action": "prepare", "request": intent}
        proposal = operation()
        if index == 4:
            return {"action": "inspect", "proposal_id": proposal["proposal_id"]}
        if index == 7:
            return {"action": "report", "proposal_id": proposal["proposal_id"],
                    "outcome": fixtures.agent_result(proposal, "entry-consumption", result="not_executed")}
        return {"action": "consume", "proposal_id": proposal["proposal_id"],
                "consumption_id": "entry-consumption"}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert self.path.endswith("/messages") and body["model"] == "fixture-model"
                index = len(steps)
                assert index < 9, "bounded scripted request budget exceeded"
                steps.append(index)
                responses.append(body.get("messages", []))
                terminal = index in {3, 8}
                block = ({"type": "text", "text": json.dumps({
                    "result_kind": "wait", "classification": "synthetic_native_operation",
                    "summary": "Original no-effect result retained",
                    "recommended_action": "Continue separate live qualification",
                })} if terminal else {"type": "tool_use", "id": f"entry-{index}",
                                      "name": "loopx_operation", "input": arguments(index)})
                events = [
                    {"type": "message_start", "message": {"id": f"fixture-{index}",
                     "model": "fixture-model", "usage": {"input_tokens": 1, "output_tokens": 0}}},
                    {"type": "content_block_start", "index": 0, "content_block": block},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn" if terminal else "tool_use"},
                     "usage": {"output_tokens": 1}},
                    {"type": "message_stop"},
                ]
                data = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                errors.append(repr(exc))
                self.send_error(500)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "loopback-fixture-placeholder")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    patch_module = root / "restrict.mjs"
    patch_module.write_text(
        "export const name='fixture-operation-only';\nexport function apply(ctx){"
        "ctx.on('agent/created',({agent})=>{agent.ctx.tools.presentAs('native');"
        "agent.ctx.tools.restrict({allow:['loopx_operation']})})}\n"
    )
    patch = root / "restrict.patch.yml"
    patch.write_text("- insert:\n    - id: fixture-operation-only\n      name: " + json.dumps(patch_module.as_uri()) + "\n")
    config = json.loads(runner.config.read_text())
    binding = config["bindings"][0]
    binding["timeout_seconds"] = 120
    binding["host_args"] = ["--host", "dsh", "--dsh-operation-module", module,
        "--dsh-runtime-bin", runtime, "--dsh-home", str(root / "sdk-home"),
        "--dsh-cordis", str(patch), "--dsh-provider", "deepseek-official",
        "--dsh-model", "fixture-model", "--dsh-reasoning-effort", "high", "--dsh-max-tokens", "1024"]
    if request_transport == "frozen":
        source_file = root / "frozen-request.json"
        source_file.write_text(json.dumps(intent))
        binding["host_args"] += ["--operation-request-file", str(source_file),
            "--operation-request-sha256", hashlib.sha256(source_file.read_bytes()).hexdigest()]
    runner.config.write_text(json.dumps(config))
    lineage = {"goal_id": runner.goal_id, "agent_id": binding["agent_id"], "todo_id": binding["todo_id"]}
    command = [sys.executable, "-m", "loopx.cli", "--registry", str(runner.registry),
        "--runtime-root", str(runner.root), "--format", "json", "turn", "run-once",
        "--goal-id", runner.goal_id, "--agent-id", binding["agent_id"], "--todo-id", binding["todo_id"],
        "--project", binding["workspace"],  "--no-global-sync",
        "--timeout-seconds", "120", "--execute", *binding["host_args"]]
    try:
        for option, replacement, expected in [
            ("--host", "generic-cli", "requires the dsh SDK host"),
            ("--dsh-operation-module", "relative.js", "must be an absolute"),
        ]:
            invalid = command.copy()
            invalid[invalid.index(option) + 1] = replacement
            refused = subprocess.run(invalid, capture_output=True, text=True, timeout=20)
            assert expected in refused.stdout + refused.stderr, refused
            assert not steps  # Invalid transport never reaches the provider.
        first = subprocess.run(command, capture_output=True, text=True, timeout=150)
        (root / "preparation-cli.json").write_text(first.stdout)
        (root / "preparation-cli.stderr").write_text(first.stderr)
        result = json.loads(first.stdout)
        assert first.returncode == 0 and result["ok"] and result["status"] == "stopped", (result, errors)
        assert len(steps) == 4 and not errors, (steps, errors)
        session = load_codex_cli_session(runner.root, lineage=lineage, host="dsh")
        proposal = operation()
        assert proposal["operation"].get("agent_handoff") is None
        delivered = store.record_operation_delivery(proposal["proposal_id"], delivery=fixtures.delivery(proposal))
        confirmed = store.decide_operation(proposal["proposal_id"], decision="confirm",
                                          confirmation=fixtures.confirmation(delivered))
        wake_config = {"registry_path": str(runner.registry), "goal_id": runner.goal_id,
            "requester_agent_id": runner.agent_id, "project": str(root),
            "execution_config": "delegations.json", "binding_id": binding["id"]}
        if original_owner:
            from loopx.control_plane.turn_driver.journal_store import load_turn_journal, turn_journal_path

            from loopx.collaboration_mcp import Delegations
            from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError

            # Ordinary self-delegation is still rejected, even with a grant.
            binding["requesters"] = [binding["agent_id"]]
            runner.config.write_text(json.dumps(config))
            owner = Delegations(runner.root, runner.registry, runner.goal_id,
                                binding["agent_id"], runner.config)
            with pytest.raises(EffectRuntimeRemoteError, match="caller has no delegation grant"):
                owner.binding(binding["id"])
            binding["continuation_kind"] = "original_operation"
            # This route needs canonical operation evidence, not a fabricated
            # completed Task, peer result or file validator.
            binding["output_refs"] = []
            wake_config["requester_agent_id"] = binding["agent_id"]
            runner.config.write_text(json.dumps(config))
        original_args = binding["host_args"].copy()
        binding["host_args"][binding["host_args"].index("--dsh-model") + 1] = "replacement-model"
        runner.config.write_text(json.dumps(config))
        refused = dispatch_confirmed_operation_wake(confirmed, runtime_root=runner.root, configuration=wake_config)
        assert refused["state"] == "blocked", refused
        assert len(steps) == 4
        binding["host_args"] = original_args
        runner.config.write_text(json.dumps(config))
        if original_owner:
            def dispatch():
                return dispatch_confirmed_operation_wake(confirmed,
                    runtime_root=runner.root, configuration=wake_config)
            with ThreadPoolExecutor(max_workers=2) as pool:
                receipts = list(pool.map(lambda _: dispatch(), range(2)))
            assert sorted(row["state"] for row in receipts) == ["existing_turn", "turn_requested"], receipts
            receipt = next(row for row in receipts if row["state"] == "turn_requested")
            deadline = time.monotonic() + 75
            turn_journal = None
            while time.monotonic() < deadline:
                current = operation()
                turn_key = (current["operation"].get("host_start") or {}).get("turn_key")
                if turn_key:
                    turn_journal = load_turn_journal(turn_journal_path(runner.root,
                        goal_id=runner.goal_id, turn_key=turn_key))
                if ((current["operation"].get("outcome") or {}).get("outcome") == "not_executed"
                        and len(steps) == 9 and turn_journal and turn_journal.get("status") == "stopped"):
                    break
                time.sleep(0.1)
            assert len(steps) == 9, (operation(), steps, errors)
            assert turn_journal and turn_journal["status"] == "stopped"
            assert turn_journal["result_kind"] == "wait"
            # Merely declaring this continuation did not make the peer API a
            # self-delegation route.
            with pytest.raises(EffectRuntimeRemoteError, match="caller has no delegation grant"):
                owner.binding(binding["id"])
            completion = None
        else:
            receipt = dispatch_confirmed_operation_wake(confirmed, runtime_root=runner.root, configuration=wake_config)
            assert receipt["state"] == "delegation_requested", receipt
            completion = wait(runner, receipt["operation_id"])
        stored = store.load(proposal["proposal_id"])
        resumed = load_codex_cli_session(runner.root, lineage=lineage, host="dsh")
        assert resumed["session_id"] == session["session_id"]
        assert resumed["operation_profile_digest"] == session["operation_profile_digest"]
        assert len(steps) == 9 and not errors, (completion, steps, errors)
        assert stored["operation"]["outcome"]["outcome"] == "not_executed", stored
        if original_owner:
            assert stored["operation"]["turn_launch"]["turn_instance_id"] == receipt["turn_instance_id"]
            assert stored["operation"]["turn_launch"]["schema_version"] == "operation_turn_launch_v0"
            assert not list((runner.root / "collaboration").rglob("operation-wake-*.json"))
        else:
            journal = json.loads(runner.path(receipt["operation_id"]).read_text())
            assert stored["operation"]["host_start"]["turn_key"] == journal["turn_key"]
        assert stored["operation"]["host_start"]["route"]["thread_id"] == session["session_id"]
        # Waiting is deliberately not validated Todo completion or a domain effect.
        if completion is not None:
            assert completion["status"] == "rejected", completion
        tool_results = [block for message in responses[-1]
                        for block in message.get("content", []) if isinstance(block, dict) and block.get("type") == "tool_result"]
        results = []
        for block in tool_results:
            content = block.get("content", [])
            for part in content if isinstance(content, list) else [{"text": content}]:
                try:
                    results.append(json.loads(part["text"]))
                except (KeyError, ValueError):
                    pass
        assert any(row.get("status") == "already_consumed" for row in results), results
        assert sum(row.get("execution_allowed") is True for row in results) == 1, results
        assert dispatch_confirmed_operation_wake(stored, runtime_root=runner.root,
            configuration=wake_config)["state"] == ("existing_turn" if original_owner else "existing_delegation")
        assert len(steps) == 9
        task_read = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(runner.registry),
            "--runtime-root", str(runner.root), "--format", "json", "todo", "list",
            "--goal-id", runner.goal_id, "--todo-id", binding["todo_id"]],
            capture_output=True, text=True, timeout=20, check=True)
        task = json.loads(task_read.stdout)["todo"]
        assert task["done"] is False and task["status"] == "open"
    finally:
        (root / "provider-steps.json").write_text(json.dumps({"steps": steps, "errors": errors}))
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
