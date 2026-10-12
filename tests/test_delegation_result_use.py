"""Version-bound requester decisions through real Turn acceptance and durable IO."""
import asyncio
import json
import os
import subprocess
import sys
import time

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from loopx.collaboration_mcp import Delegations
from test_local_delegation import brief, service as delegation_service, wait

service = delegation_service


def test_version_bound_retry_reads_existing_operation_after_input_changes(service):
    root, runner = service
    config = json.loads(runner.config.read_text())
    consumer = {**config["bindings"][0], "id": "synthesis", "agent_id": "reviewer",
                "todo_id": "todo_reviewer-corrected", "workspace": str(root / "reviewer/corrected")}
    config["bindings"].append(consumer)
    runner.config.write_text(json.dumps(config))

    runner.start("analysis", "analysis-1", brief())
    source = wait(runner)
    artifact = source["artifacts"][0]
    consumer_input = root / "reviewer/corrected/accepted-input.json"
    consumer_input.write_text(artifact["text"])
    dependency = {"ref": "accepted-input.json", "description": "Accepted analysis for synthesis",
                  "sha256": artifact["sha256"], "delegation": {
                      "operation_id": "analysis-1", "ref": artifact["ref"], "relation": "uses"}}
    request = {**brief(), "inputs": [dependency]}

    (root / "hold").touch()
    first = runner.start("synthesis", "synthesis-1", request)
    deadline = time.monotonic() + 45
    while not (root / "host-started").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert (root / "host-started").exists(), runner.read("synthesis-1")

    consumer_input.write_text("{}")
    replay = runner.start("synthesis", "synthesis-1", request)
    assert replay["request_id"] == first["request_id"]
    assert replay["dependencies"] == [{**dependency["delegation"], "sha256": artifact["sha256"],
                                        "input_ref": dependency["ref"], "state": "unavailable"}]
    assert (root / "reviewer/corrected/host-invocations").read_text() == "1"
    with pytest.raises(ValueError, match="operation identity conflict"):
        runner.start("synthesis", "synthesis-1", {**request, "purpose": "Changed instruction"})
    with pytest.raises(ValueError, match="input version unavailable"):
        runner.start("synthesis", "synthesis-2", request)
    assert not runner.path("synthesis-2").exists()

    (root / "release").touch()
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        final = runner.read("synthesis-1")
        if not final["worker_active"] and final.get("error"):
            break
        time.sleep(0.25)
    assert "input version unavailable" in final.get("error", ""), final
    assert final["status"] == "turn_returned"
    assert (root / "reviewer/corrected/host-invocations").read_text() == "1"


def test_result_use_requires_exact_accepted_input_and_survives_reconnect(service):
    root, runner = service
    config = json.loads(runner.config.read_text())
    consumer = {**config["bindings"][0], "id": "synthesis", "agent_id": "reviewer",
                "todo_id": "todo_reviewer-corrected", "workspace": str(root / "reviewer/corrected")}
    config["bindings"].append(consumer)
    runner.config.write_text(json.dumps(config))
    runner.start("analysis", "analysis-1", brief())
    source = wait(runner)
    assert source["status"] == "accepted"
    assert "adoptions" not in source and "dependencies" not in source
    artifact = source["artifacts"][0]
    consumer_input = root / "reviewer/corrected/accepted-input.json"
    consumer_input.write_text(artifact["text"])
    dependency = {"ref": "accepted-input.json", "description": "Accepted analysis for synthesis",
                  "sha256": artifact["sha256"], "delegation": {
                      "operation_id": "analysis-1", "ref": artifact["ref"], "relation": "uses"}}
    request = {**brief(), "inputs": [dependency]}
    with pytest.raises(Exception, match="distinct"):
        runner.adopt_result("analysis-1", "analysis-1")
    for patch in [{"sha256": "a" * 64}, {"delegation": {**dependency["delegation"], "operation_id": "other"}},
                  {"delegation": {**dependency["delegation"], "ref": "not-output.json"}}]:
        with pytest.raises(ValueError, match="input version unavailable"):
            runner.start("synthesis", "synthesis-1", {**request, "inputs": [{**dependency, **patch}]})
    assert not runner.path("synthesis-1").exists(), "Invalid lineage must not dispatch a model"
    runner.start("synthesis", "synthesis-1", request)
    result = wait(runner, "synthesis-1")
    assert result["status"] == "accepted"
    assert result["dependencies"] == [{**dependency["delegation"], "sha256": artifact["sha256"],
                                       "input_ref": dependency["ref"], "state": "current"}]
    assert "adoptions" not in runner.read("analysis-1"), "Receiving/reading/completing does not adopt"
    # Public CLI writes the same requester-scoped decision, no new model turn.
    command = [sys.executable, "-m", "loopx.cli", "--registry", str(runner.registry),
               "--runtime-root", str(runner.root), "delegation", "adopt", "--goal-id", runner.goal_id,
               "--agent-id", runner.agent_id, "--execution-config", str(runner.config),
               "--operation-id", "analysis-1", "--consumer-operation-id", "synthesis-1", "--execute"]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    adopted = json.loads(completed.stdout)["adoptions"]
    assert len(adopted) == 1 and adopted[0]["state"] == "current"
    assert adopted[0]["requester_agent_id"] == "lead"
    assert adopted[0]["source_artifacts"] == [{"ref": artifact["ref"], "sha256": artifact["sha256"]}]
    assert adopted[0]["consumer_artifacts"][0]["sha256"] == result["artifacts"][0]["sha256"]
    reconnected = Delegations(runner.root, runner.registry, runner.goal_id, runner.agent_id, runner.config)
    async def reconnect_mcp():
        # Keep the MCP child on the same disposable authority runtime as its
        # parent. The SDK's default environment omits these isolation controls.
        params = StdioServerParameters(command=sys.executable, env={
            key: os.environ[key] for key in ("NODE_OPTIONS", "TMPDIR", "TEMP", "TMP")
            if key in os.environ}, args=[
            "-m", "loopx.collaboration_mcp", "--registry", str(runner.registry),
            "--runtime-root", str(runner.root), "--goal-id", runner.goal_id,
            "--agent-id", runner.agent_id, "--workspace", str(root / "lead"),
            "--execution-config", str(runner.config)])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool("adopt_delegation_result", {
                    "operation_id": "analysis-1", "consumer_operation_id": "synthesis-1"})
                assert not result.isError
                return json.loads(result.content[0].text)
    assert asyncio.run(reconnect_mcp())["adoptions"] == adopted
    assert reconnected.read("analysis-1")["adoptions"] == adopted
    assert (root / "reviewer/corrected/host-invocations").read_text() == "1"
    ungranted = Delegations(runner.root, runner.registry, runner.goal_id, "reviewer", runner.config)
    with pytest.raises(ValueError, match="unknown delegation"):
        ungranted.adopt_result("analysis-1", "synthesis-1")
    # A modified receiver input withdraws current adoption even while the output
    # still passes its domain validator. Output validity alone is insufficient.
    consumer_input.write_text("{}")
    assert runner.read("synthesis-1")["dependencies"][0]["state"] == "unavailable"
    assert runner.read("analysis-1")["adoptions"][0]["state"] == "unavailable"
    with pytest.raises(Exception, match="exact current uses input"):
        runner.adopt_result("analysis-1", "synthesis-1")
    consumer_input.write_text(artifact["text"])
    assert runner.read("analysis-1")["adoptions"][0]["state"] == "current"
    (root / "reviewer/corrected/output.json").write_text("{}")
    assert runner.read("analysis-1")["adoptions"][0]["state"] == "unavailable"
    with pytest.raises(ValueError, match="acceptance rejected"):
        runner.adopt_result("analysis-1", "synthesis-1")


def accepted_chain(root, runner):
    """Synthetic workers through production admission, validation and settlement."""
    config = json.loads(runner.config.read_text())
    template = config["bindings"][0]
    config["bindings"] += [
        {**template, "id": "middle", "agent_id": "analyst", "todo_id": "todo_analyst-corrected",
         "workspace": str(root / "analyst/corrected")},
        {**template, "id": "synthesis", "agent_id": "reviewer", "todo_id": "todo_reviewer-corrected",
         "workspace": str(root / "reviewer/corrected")},
    ]
    runner.config.write_text(json.dumps(config))
    runner.start("analysis", "analysis-1", brief())
    source = wait(runner)

    def linked_request(source, workspace):
        artifact = source["artifacts"][0]
        path = workspace / "declared-input.json"
        path.write_text(artifact["text"])
        return {**brief(), "inputs": [{"ref": path.name, "description": "Exact accepted source",
            "sha256": artifact["sha256"], "delegation": {"operation_id": source["operation_id"],
            "ref": artifact["ref"], "relation": "uses"}}]}

    middle_brief = linked_request(source, root / "analyst/corrected")
    runner.start("middle", "middle-1", middle_brief)
    middle = wait(runner, "middle-1")
    consumer_brief = linked_request(middle, root / "reviewer/corrected")
    runner.start("synthesis", "synthesis-1", consumer_brief)
    consumer = wait(runner, "synthesis-1")
    assert consumer["status"] == "accepted"
    return consumer_brief


def test_transitive_current_use_withdraws_and_recovers_without_redispatch(service, monkeypatch):
    """Real File/SQLite, workers and domain checks; historical output stays valid."""
    root, runner = service
    consumer_brief = accepted_chain(root, runner)
    historical = {operation: runner.path(operation).read_bytes()
                  for operation in ("analysis-1", "middle-1", "synthesis-1")}
    middle_input = root / "analyst/corrected/declared-input.json"
    saved = middle_input.read_bytes()
    middle_input.write_text("{}")
    reads = []
    current_read = runner._read_current
    def counted_read(operation):
        reads.append(operation)
        return current_read(operation)
    with monkeypatch.context() as spy:
        spy.setattr(runner, "_read_current", counted_read)
        unavailable = runner.read("synthesis-1")
    assert sorted(reads) == ["analysis-1", "middle-1", "synthesis-1"], "Each source is checked once per admission"
    assert unavailable["status"] == "accepted", "Historical terminal outcome must survive"
    assert unavailable["dependencies"][0]["state"] == "unavailable"
    assert unavailable["current_use"]["state"] == "unavailable"
    assert unavailable["current_use"]["blocking_operation_id"] == "middle-1"
    assert unavailable["current_use"]["reason"] == "input_unavailable"
    with pytest.raises(ValueError, match="input version unavailable"):
        runner.start("synthesis", "synthesis-2", consumer_brief)
    assert not runner.path("synthesis-2").exists()
    with pytest.raises(Exception, match="exact current uses input"):
        runner.adopt_result("middle-1", "synthesis-1")
    assert runner.start("synthesis", "synthesis-1", consumer_brief)["current_use"]["state"] == "unavailable"
    middle_input.write_bytes(saved)
    assert runner.read("synthesis-1")["current_use"]["state"] == "current"
    assert runner.adopt_result("middle-1", "synthesis-1")["adoptions"][0]["state"] == "current"
    for operation in ("analysis-1", "synthesis-1"):
        assert runner.path(operation).read_bytes() == historical[operation]
    for workspace in (root / "analyst/initial", root / "analyst/corrected", root / "reviewer/corrected"):
        assert (workspace / "host-invocations").read_text() == "1"


def test_deadline_after_link_qualification_blocks_admission_and_adoption(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from loopx.control_plane.collaboration import delegation_results as module
    version = "a" * 64
    item = {"operation_id": "source", "ref": "out.json", "sha256": version,
            "input_ref": "input.json", "input_available": True}
    request = {"inputs": [{"delegation": {"operation_id": "source"}}]}
    runner = SimpleNamespace(_read_current=lambda op: {"status": "accepted", "artifacts": [{"ref": "out.json", "sha256": version}]},
        path=lambda op: tmp_path/op, _bound=lambda row: {})
    monkeypatch.setattr(module._ResultUseRead, "inputs", lambda self, binding, brief: [item] if brief["inputs"] else [])
    # Avoid a self edge; source IO has no ancestors, consumer does.
    monkeypatch.setattr(module, "operation_brief", lambda service, row: request if row.get("consumer") else {"inputs": []})
    monkeypatch.setattr(module, "_read", lambda path: {"consumer": path.name == "consumer"})
    elapsed = [0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    qualify = module._ResultUseRead.qualify
    def expire_after_link(self, roots):
        result = qualify(self, roots)
        elapsed[0] = 16
        return result
    monkeypatch.setattr(module._ResultUseRead, "qualify", expire_after_link)
    links, aggregate = module._dependency_observation(runner, {}, request)
    assert aggregate["reason"] == "verification_budget_exhausted"
    assert links[0]["state"] == "unavailable"
    elapsed[0] = 0
    with pytest.raises(ValueError, match="unavailable"):
        module.require_dependencies(runner, {}, request)
    elapsed[0] = 0
    owner = module.effect_runtime_result
    captured = []
    def observe(method, params):
        if method == "collaboration.delegation.adoption":
            captured.append(params["inputs_current"])
            return {}
        return owner(method, params)
    monkeypatch.setattr(module, "effect_runtime_result", observe)
    module.adoption_evidence(runner, "source", "consumer")
    assert captured == [False]
