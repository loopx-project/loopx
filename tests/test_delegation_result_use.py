"""Version-bound requester decisions through real Turn acceptance and durable IO."""
import asyncio
import json
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
        params = StdioServerParameters(command=sys.executable, args=[
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
