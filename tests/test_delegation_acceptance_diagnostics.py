"""Public diagnostics retain the real File/SQLite validation owner's refusal."""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_delegation_cli import cli
from test_independent_delegation_validation import independent_binding
from test_local_delegation import service as delegation_service

service = delegation_service


def test_missing_validation_diagnosis_roundtrips_cli_and_mcp_without_launch(service):
    root, runner = service
    todo_id = independent_binding(service, declared=False)
    before = runner.registry.read_bytes(), runner.config.read_bytes()
    status, cli_result = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0, cli_result

    async def inspect_mcp(actor=runner.agent_id):
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "loopx.collaboration_mcp", "--registry", str(runner.registry),
            "--runtime-root", str(runner.root), "--goal-id", runner.goal_id,
            "--agent-id", actor, "--workspace", str(root / "lead"),
            "--execution-config", str(runner.config),
        ])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool("inspect_execution_binding", {"binding_id": "analysis"})
                if actor != runner.agent_id:
                    assert result.isError
                    return result.content[0].text
                assert not result.isError
                return json.loads(result.content[0].text)

    mcp_result = asyncio.run(inspect_mcp())
    denied_status, denied_cli = cli(runner, "inspect", "--binding-id", "analysis", actor="reviewer")
    assert denied_status == 1 and denied_cli["ok"] is False
    assert "caller has no delegation grant" in denied_cli["error"]
    assert "acceptance_reason_code" not in denied_cli and "executor" not in denied_cli
    denied_mcp = asyncio.run(inspect_mcp("reviewer"))
    assert "caller has no delegation grant" in denied_mcp
    assert "independent_delegation_validation_required" not in denied_mcp
    for result in [cli_result, mcp_result]:
        assert result["state"] == "acceptance_unavailable"
        assert result["binding"]["todo_id"] == todo_id
        assert result["acceptance_reason_code"] == "independent_delegation_validation_required"
        assert result["acceptance_next_action"] == "review_original_todo_validation"
        assert result["acceptance_ready"] is False
        assert result["authority_ready"] is True
        assert result["executor"]["available"] is None
        assert not any(result["effects"].values())
        assert str(root) not in json.dumps(result)
        assert "validation_argv" not in result
    assert (runner.registry.read_bytes(), runner.config.read_bytes()) == before
    assert not (root / "host-started").exists()
    assert not list(runner.path("inventory").parent.glob("*.json"))
