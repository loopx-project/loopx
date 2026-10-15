"""Public CLI and detached wake with the actual opt-in SDK/runtime.

The only provider is loopback Messages SSE. Fixture confirmation authenticates
a no-effect qualification operation; it is never live financial authority.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from examples import operation_action_fixtures as fixtures
from loopx.chat_action_store import ChatActionStore
from loopx.control_plane.collaboration.operation_wake import (
    dispatch_confirmed_operation_wake,
)
from loopx.control_plane.turn_driver.codex_sessions import load_codex_cli_session
from test_local_delegation import service as delegation_service, wait

service = delegation_service


def test_public_dsh_operation_entry_and_confirmed_callback(service, monkeypatch):
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
    runner.config.write_text(json.dumps(config))
    lineage = {"goal_id": runner.goal_id, "agent_id": binding["agent_id"], "todo_id": binding["todo_id"]}
    command = [sys.executable, "-m", "loopx.cli", "--registry", str(runner.registry),
        "--runtime-root", str(runner.root), "--format", "json", "turn", "run-once",
        "--goal-id", runner.goal_id, "--agent-id", binding["agent_id"], "--todo-id", binding["todo_id"],
        "--project", binding["workspace"], "--scan-root", binding["workspace"], "--no-global-sync",
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
        original_args = binding["host_args"].copy()
        binding["host_args"][binding["host_args"].index("--dsh-model") + 1] = "replacement-model"
        runner.config.write_text(json.dumps(config))
        refused = dispatch_confirmed_operation_wake(confirmed, runtime_root=runner.root, configuration=wake_config)
        assert refused["state"] == "blocked", refused
        assert len(steps) == 4
        binding["host_args"] = original_args
        runner.config.write_text(json.dumps(config))
        receipt = dispatch_confirmed_operation_wake(confirmed, runtime_root=runner.root, configuration=wake_config)
        assert receipt["state"] == "delegation_requested", receipt
        completion = wait(runner, receipt["operation_id"])
        stored = store.load(proposal["proposal_id"])
        resumed = load_codex_cli_session(runner.root, lineage=lineage, host="dsh")
        assert resumed["session_id"] == session["session_id"]
        assert resumed["operation_profile_digest"] == session["operation_profile_digest"]
        assert len(steps) == 9 and not errors, (completion, steps, errors)
        assert stored["operation"]["outcome"]["outcome"] == "not_executed", stored
        journal = json.loads(runner.path(receipt["operation_id"]).read_text())
        assert stored["operation"]["host_start"]["turn_key"] == journal["turn_key"]
        assert stored["operation"]["host_start"]["route"]["thread_id"] == session["session_id"]
        # Waiting is deliberately not validated Todo completion or a domain effect.
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
            configuration=wake_config)["state"] == "existing_delegation"
        assert len(steps) == 9
    finally:
        (root / "provider-steps.json").write_text(json.dumps({"steps": steps, "errors": errors}))
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
