"""The real stdio bridge binds identity and rechecks revocation per call."""

import asyncio
import json
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_serving_mcp_still_requires_an_explicit_workspace(tmp_path):
    result = subprocess.run([
        sys.executable, "-m", "loopx.collaboration_mcp",
        "--runtime-root", str(tmp_path), "--registry", str(tmp_path / "registry.json"),
        "--goal-id", "delivery", "--agent-id", "builder",
    ], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "--workspace is required when serving MCP" in result.stderr


def test_scoped_stdio_tools_do_not_offer_shell_or_sender_override(tmp_path):
    registry = tmp_path / "registry.json"
    config = {
        "goals": [
            {
                "id": "delivery",
                "repo": str(tmp_path),
                "coordination": {"registered_agents": ["builder", "reviewer"]},
            }
        ]
    }
    registry.write_text(json.dumps(config))
    brief = {
        "schema_version": "collaboration_brief_v0",
        "purpose": "Review the allocation",
        "context": "The owner rejected proportional rounding.",
        "constraints": ["No orders"],
        "inputs": [],
        "acceptance": ["Check coupled capacity limits"],
        "return_requirement": "Findings",
    }

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "loopx.collaboration_mcp",
                "--runtime-root",
                str(tmp_path),
                "--registry",
                str(registry),
                "--goal-id",
                "delivery",
                "--agent-id",
                "builder",
                "--workspace",
                str(tmp_path),
            ],
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "read_context",
                "assess_request",
                "link_work",
                "request_peer",
                "return_result",
                "consume_peer_result",
            }
            for tool in tools.tools:
                assert not {
                    "agent_id",
                    "goal_id",
                    "runtime_root",
                    "command",
                    "path",
                } & set(tool.inputSchema.get("properties", {}))
            returned = next(tool for tool in tools.tools if tool.name == "return_result")
            assert "conclusion to the original requester" in returned.description
            assert "Preserve requested substantive detail" in returned.description
            assert "State material gaps" in returned.description
            result = await session.call_tool(
                "request_peer",
                {
                    "peer_agent_id": "reviewer",
                    "operation_id": "review-1",
                    "brief": brief,
                },
            )
            assert not result.isError
            result = await session.call_tool(
                "request_peer",
                {
                    "peer_agent_id": "unknown",
                    "operation_id": "review-2",
                    "brief": brief,
                },
            )
            assert result.isError
            # Changing registration affects this already-running server.
            config["goals"][0]["coordination"]["registered_agents"] = ["reviewer"]
            registry.write_text(json.dumps(config))
            result = await session.call_tool("read_context", {})
            assert result.isError
            result = await session.call_tool(
                "request_peer",
                {
                    "peer_agent_id": "reviewer",
                    "operation_id": "review-3",
                    "brief": brief,
                },
            )
            assert result.isError

    asyncio.run(exercise())


def test_stdio_receiver_links_existing_work_and_returns_without_duplicate_tasks(tmp_path, monkeypatch):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
    from loopx.control_plane.collaboration.peers import request, read_inbox
    from loopx.control_plane.collaboration.inbox import _root

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, root, _ = promoted_create_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["goals"][0]["coordination"]["registered_agents"].append("agent-b")
    registry.write_text(json.dumps(config))

    def cli(*args):
        proc = subprocess.run([
            sys.executable, "-m", "loopx.entrypoint", "--format", "json",
            "--registry", str(registry), "--runtime-root", str(root), *args,
        ], capture_output=True, text=True, timeout=45)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        return json.loads(proc.stdout)

    def add(owner):
        return cli("todo", "add", "--goal-id", "goal-a", "--role", "agent",
                   "--text", f"Existing work for {owner}", "--claimed-by", owner)["todo_id"]

    own, foreign = add("agent-a"), add("agent-b")
    brief = {
        "schema_version": "collaboration_brief_v0", "purpose": "Finish the existing work",
        "context": "The receiver is also reviewing unrelated work.", "constraints": [],
        "inputs": [], "acceptance": ["Return the verified outcome"], "return_requirement": "Result",
    }
    rid = request(root, registry, "goal-a", "agent-b", "agent-a", "existing-work", brief)["request_id"]
    conclusion = "Existing work needs its verification; not complete.\n\n" + (
        "- [Source](https://example.org/reference)\n"
        "- Run the reproducible comparison before acceptance.\n" * 59
        + "- Run the reproducible comparison before acceptance.")

    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "loopx.collaboration_mcp", "--runtime-root", str(root),
            "--registry", str(registry), "--goal-id", "goal-a", "--agent-id", "agent-a",
            "--workspace", str(tmp_path),
        ])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()

            async def call(name, args):
                result = await session.call_tool(name, args)
                assert not result.isError, result
                return json.loads(result.content[0].text)

            first = (await call("read_context", {}))["items"][0]
            assert first["receiver_followthrough"]["step"] == "assess_request"
            assert first["receiver_followthrough"]["linked_todos"] == []
            await call("assess_request", {"request_id": rid, "decision": "adopt", "reason": "Resume my existing task"})
            result = await session.call_tool("link_work", {"request_id": rid, "todo_ids": [foreign]})
            assert result.isError
            linked = await call("link_work", {"request_id": rid, "todo_ids": [own]})
            path = _root(root) / "links" / (rid + ".json")
            original = path.read_bytes()
            assert linked["todo_ids"] == [own]
            await call("link_work", {"request_id": rid, "todo_ids": [own]})
            assert path.read_bytes() == original
            cli("todo", "update", "--goal-id", "goal-a", "--todo-id", own,
                "--agent-id", "agent-a", "--text", "Current accepted work")
            resumed = (await call("read_context", {}))["items"][0]["receiver_followthrough"]
            assert resumed["recorded_decision"] == "adopt" and resumed["answer_owed"]
            assert resumed["step"] == "review_request_work"
            assert [(t["todo_id"], t["title"], t["status"]) for t in resumed["linked_todos"]] == [
                (own, "Current accepted work", "open")
            ]
            await call("return_result", {"request_id": rid, "text": conclusion})
            assert (await call("read_context", {}))["items"] == []
            # A short factual answer requires neither a fabricated task nor a link.
            quick = request(root, registry, "goal-a", "agent-b", "agent-a", "short-answer", brief)["request_id"]
            ref = "sha256:" + "b" * 64
            await call("link_work", {"request_id": quick, "evidence_ids": [ref]})
            quick_view = (await call("read_context", {}))["items"][0]["receiver_followthrough"]
            assert quick_view["evidence_refs"] == [ref]
            assert quick_view["linked_todos"] == []
            assert quick_view["step"] == "assess_request"
            await call("assess_request", {"request_id": quick, "decision": "no_change", "reason": "Already satisfied"})
            await call("return_result", {"request_id": quick, "text": "Already satisfied; no new task."})
            config["goals"][0]["coordination"]["registered_agents"] = ["agent-b"]
            registry.write_text(json.dumps(config))
            result = await session.call_tool("link_work", {"request_id": rid, "evidence_ids": ["sha256:" + "a" * 64]})
            assert result.isError
            assert path.read_bytes() == original

    asyncio.run(exercise())
    received = read_inbox(root, registry, "goal-a", "agent-b")["peer_returns"]["items"]
    assert len(received) == 2
    assert next(item for item in received if item["request_id"] == rid)["text"] == conclusion
    assert len(cli("todo", "list", "--goal-id", "goal-a")["todos"]) == 2
