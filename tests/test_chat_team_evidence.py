"""Owner Chat reads exactly the original delegation, with live acceptance checks."""
import json
import shutil
import subprocess
import sys
import http.client
import threading

import pytest

from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from test_local_delegation import brief, service as delegation_service, wait

service = delegation_service


def test_chat_and_cli_read_same_accepted_artifact_and_reject_stale_scope(service):
    root, runner = service
    runner.start("analysis", "analysis-1", brief())
    accepted = wait(runner)
    assert accepted["status"] == "accepted"
    repo = root / "project"
    config = repo / ".loopx/config/delegations.json"
    config.parent.mkdir(parents=True)
    shutil.copyfile(runner.config, config)
    registry = json.loads(runner.registry.read_text())
    registry["goals"][0]["spawn_policy"] = {
        "mode": "multi_subagent", "allowed": True, "max_children": 2,
        "execution_config": ".loopx/config/delegations.json",
    }
    runner.registry.write_text(json.dumps(registry))
    store = ChatSessionStore(runner.root)
    controller = ChatRuntimeController(store=store, codex_bin="codex", registry_path=runner.registry)
    sid = store.create_session(goal_id=runner.goal_id, agent_id="codex",
        channel_id="goal." + runner.goal_id, upstream_thread_id="fixture",
        upstream_mode="chat", adapter_kind="codex_app_server")["session_id"]
    mode = controller.loopx_mode
    # Configure through the production API. Viewing is available before activation.
    mode.apply(sid, {"operation": "configure", "settings": {"agent_id": "lead", "token_budget": 1000}},
               work_dir=repo, objective="Inspect existing evidence")
    before = store.load_session(sid)
    def read(body=None):
        return mode.apply(sid, body or {"operation": "read", "operation_id": "analysis-1"},
                          work_dir=repo, objective="Inspect existing evidence")
    observed = read()
    cli = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(runner.registry),
        "--runtime-root", str(runner.root), "delegation", "read", "--goal-id", runner.goal_id,
        "--agent-id", "lead", "--execution-config", str(config), "--operation-id", "analysis-1"],
        capture_output=True, text=True, check=True)
    cli_result = json.loads(cli.stdout)
    assert observed["ok"] and cli_result["ok"]
    assert observed["status"] == cli_result["status"] == "accepted"
    # The worker lock may release between observations; compare durable evidence,
    # not momentary liveness sampled at different times.
    for key in ("operation_id", "request_id", "agent_id", "todo_id", "artifacts"):
        assert observed[key] == cli_result[key] == accepted[key]
    # Every transport reruns real checks; time is fresh, the checked versions
    # and current rule definition match the report actually returned.
    for result in (observed, cli_result, accepted):
        assert result["validation"]["checked_at"].endswith("Z")
        assert result["validation"]["output_versions"] == [
            {"ref": row["ref"], "sha256": row["sha256"]} for row in result["artifacts"]]
    assert {key: value for key, value in observed["validation"].items() if key != "checked_at"} == {
        key: value for key, value in cli_result["validation"].items() if key != "checked_at"}
    assert observed["artifacts"][0]["text"] == (root / "analyst/initial/output.json").read_text()
    assert store.load_session(sid) == before
    assert (root / "analyst/initial/host-invocations").read_text() == "1"

    for body in [
        {"operation": "read"}, {"operation": "read", "operation_id": ["analysis-1"]},
        {"operation": "read", "operation_id": "../analysis-1"},
        {"operation": "read", "operation_id": "analysis-1", "agent_id": "reviewer"},
        {"operation": "read", "operation_id": "analysis-1", "path": "output.json"},
        {"operation": "read", "operation_id": "unknown"},
    ]:
        with pytest.raises(ValueError):
            read(body)
    # A once-accepted file cannot be replayed after mutation.
    (root / "analyst/initial/output.json").write_text("{}")
    with pytest.raises(ValueError, match="acceptance rejected"):
        read()
    # A revoked binding clears access even if its journal still exists.
    config.write_text(json.dumps({"schema_version": "loopx_local_delegation_v0", "bindings": []}))
    with pytest.raises(ValueError, match="bindings changed"):
        read()
    assert (root / "analyst/initial/host-invocations").read_text() == "1"


def test_owner_http_adopts_exact_result_without_starting_or_resuming_work(service):
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"].append({**config["bindings"][0], "id": "synthesis", "agent_id": "reviewer",
        "todo_id": "todo_reviewer-corrected", "workspace": str(root / "reviewer/corrected")})
    runner.config.write_text(json.dumps(config))
    runner.start("analysis", "analysis-1", brief())
    source = wait(runner)
    artifact = source["artifacts"][0]
    input_file = root / "reviewer/corrected/accepted-input.json"
    input_file.write_text(artifact["text"])
    runner.start("synthesis", "synthesis-1", {**brief(), "inputs": [{
        "ref": "accepted-input.json", "description": "Accepted input", "sha256": artifact["sha256"],
        "delegation": {"operation_id": "analysis-1", "ref": artifact["ref"], "relation": "uses"}}]})
    consumer = wait(runner, "synthesis-1")
    assert consumer["status"] == "accepted"
    repo = root / "project"
    executor = repo / ".loopx/config/delegations.json"
    executor.parent.mkdir(parents=True)
    executor.write_bytes(runner.config.read_bytes())
    registry = json.loads(runner.registry.read_text())
    registry["goals"][0]["spawn_policy"] = {"mode": "multi_subagent", "allowed": True,
        "max_children": 2, "execution_config": ".loopx/config/delegations.json"}
    runner.registry.write_text(json.dumps(registry))
    store = ChatSessionStore(runner.root)
    controller = ChatRuntimeController(store=store, codex_bin="unused", registry_path=runner.registry)
    sid = store.create_session(goal_id=runner.goal_id, agent_id="codex",
        channel_id="goal." + runner.goal_id, upstream_thread_id="fixture",
        upstream_mode="chat", adapter_kind="codex_app_server")["session_id"]
    controller.loopx_mode.apply(sid, {"operation": "configure", "settings": {
        "agent_id": "lead", "token_budget": 1000}}, work_dir=repo, objective="Read completed work")
    # Pausing execution does not prevent this explicit decision on completed work.
    mode = store.load_session(sid)["loopx_mode"]
    store.update_session(sid, loopx_mode={**mode, "enabled": True, "paused": True})
    before = store.load_session(sid)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.registry_path, server.chat_store, server.runtime_controller = runner.registry, store, controller
    server.runtime_root_override, server.scan_roots, server.limit = str(runner.root), [], 20
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(body, *, origin=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=60)
        try:
            connection.request("POST", f"/api/chat/sessions/{sid}/loopx", json.dumps(body), {
                "Content-Type": "application/json",
                "Origin": origin or f"http://127.0.0.1:{server.server_port}"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    adoption = {"operation": "adopt", "operation_id": "analysis-1", "consumer_operation_id": "synthesis-1"}
    try:
        assert "adoptions" not in request({"operation": "read", "operation_id": "analysis-1"})[1]
        for body in [
            {**adoption, "agent_id": "reviewer"}, {**adoption, "settings": {"agent_id": "reviewer"}},
            {**adoption, "consumer_operation_id": "analysis-1"},
            {**adoption, "consumer_operation_id": "../synthesis-1"},
            {"operation": "adopt", "operation_id": "analysis-1"},
        ]:
            assert request(body)[0] == 409
        assert request(adoption, origin="https://untrusted.example")[0] == 403
        input_file.write_text("{}")
        assert request(adoption)[0] == 409
        assert "adoptions" not in request({"operation": "read", "operation_id": "analysis-1"})[1]
        input_file.write_text(artifact["text"])
        registry["goals"][0]["status"] = "stopped"
        runner.registry.write_text(json.dumps(registry))
        assert request(adoption)[0] == 409
        registry["goals"][0].pop("status")
        runner.registry.write_text(json.dumps(registry))
        status, recorded = request(adoption)
        assert status == 200, recorded
        assert len(recorded["adoptions"]) == 1
        receipt = recorded["adoptions"][0]
        assert receipt["state"] == "current" and receipt["requester_agent_id"] == "lead"
        assert receipt["source_artifacts"] == [{"ref": artifact["ref"], "sha256": artifact["sha256"]}]
        assert receipt["consumer_artifacts"] == [{"ref": row["ref"], "sha256": row["sha256"]}
            for row in consumer["artifacts"]]
        assert request(adoption)[1]["adoptions"] == recorded["adoptions"]
        cli = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(runner.registry),
            "--runtime-root", str(runner.root), "delegation", "read", "--goal-id", runner.goal_id,
            "--agent-id", "lead", "--execution-config", str(executor), "--operation-id", "analysis-1"],
            capture_output=True, text=True, check=True)
        assert json.loads(cli.stdout)["adoptions"] == recorded["adoptions"]
        assert store.load_session(sid) == before
        for workspace in ("analyst/initial", "reviewer/corrected"):
            assert (root / workspace / "host-invocations").read_text() == "1"
        executor.write_text(json.dumps({"schema_version": "loopx_local_delegation_v0", "bindings": []}))
        assert request(adoption)[0] == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
