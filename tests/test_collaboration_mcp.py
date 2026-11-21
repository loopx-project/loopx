"""The real stdio bridge binds identity and rechecks revocation per call."""

import asyncio
import json
import os
import subprocess
import sys

import pytest

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


def test_stdio_lost_read_response_preserves_original_page_and_result_receipts(tmp_path, monkeypatch):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from loopx.control_plane.collaboration.inbox import _receipt, _root, acknowledge, pending
    from loopx.control_plane.collaboration.goal_instance_scope import collaboration_goal_scope
    from loopx.control_plane.collaboration.peers import request, return_result
    from loopx.control_plane.projects.registry_codec import source_session_registry_transaction

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry = tmp_path / "registry.json"
    ref = {"goal_id": "delivery", "goal_instance_id": "ginst_" + "a" * 32}
    with source_session_registry_transaction(registry, operation="create_inbox_fixture", create=lambda: {
        "profile_id": "source_session_v1", "goals": [{"id": "delivery", "repo": str(tmp_path),
        "status": "active", "goal_instance_id": ref["goal_instance_id"],
        "coordination": {"registered_agents": ["worker", "source"]}}],
        "session_bindings": [], "session_receipts": [], "lifetime_receipts": [],
    }) as transaction:
        transaction.commit(transaction.payload_copy())
    brief = {"schema_version": "collaboration_brief_v0", "purpose": "Inspect the artifact",
        "context": "Private source body", "constraints": [], "inputs": [],
        "acceptance": ["Return checked findings"], "return_requirement": "Reply to the source"}
    for number in range(22):
        request(tmp_path, registry, "delivery", "source", "worker", f"check-{number}", brief)
    # Only the transport reply is faulted: this launches the real stdio server,
    # with its native request/receipt owner and existing tool registration.
    launcher = tmp_path / "lost_response.py"
    launcher.write_text('''from loopx.control_plane.collaboration import inbox
from loopx.control_plane.effect_runtime import EffectRuntimeResponseAmbiguous
from loopx.collaboration_mcp import main
transport = inbox.read_inbox_transaction
records = 0
lost_peer_read = False
def interrupted(root, query, **kwargs):
    global records, lost_peer_read
    if query["operation"] == "returns" and not lost_peer_read:
        lost_peer_read = True
        transport(root, query, **kwargs)
        raise EffectRuntimeResponseAmbiguous("collaboration.inbox.read", timeout=10)
    if query["operation"] == "record_read":
        records += 1
        if records == 2:
            transport(root, {**query, "items": query["items"][:1], "deliveries": [],
                "work_observations": query["work_observations"][:1], "include_context": False}, **kwargs)
            raise EffectRuntimeResponseAmbiguous("collaboration.inbox.read", timeout=10)
    return transport(root, query, **kwargs)
inbox.read_inbox_transaction = interrupted
main()
''')

    async def exercise():
        turn_key = "sha256:" + "c" * 64
        params = StdioServerParameters(command=sys.executable, args=[str(launcher),
            "--runtime-root", str(tmp_path), "--registry", str(registry), "--goal-id", "delivery",
            "--agent-id", "worker", "--workspace", str(tmp_path)], env={**os.environ,
                "LOOPX_TURN_KEY": turn_key, "LOOPX_TURN_GOAL_ID": "delivery",
                "LOOPX_TURN_AGENT_ID": "worker", "LOOPX_TURN_WORKSPACE": str(tmp_path.resolve())})
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            first = await session.call_tool("read_context", {})
            assert not first.isError
            page = json.loads(first.content[0].text)
            assert len(page["items"]) == 20 and page["next_cursor"]
            cursor = page["next_cursor"]
            rid = request(tmp_path, registry, "delivery", "worker", "source", "peer-check", brief)["request_id"]
            acknowledge(tmp_path, "delivery", "source", rid, "adopt", "Check the artifact", registry=registry)
            return_result(tmp_path, "delivery", "source", rid, "Private peer conclusion", registry=registry)
            with collaboration_goal_scope(registry, goal_id="delivery", agents=("worker",)) as scope:
                tail = pending(tmp_path, "delivery", "worker", cursor=cursor, scope=scope)["items"]
            assert len(tail) == 2
            failed = await session.call_tool("read_context", {"cursor": cursor})
            assert failed.isError
            # FastMCP retains its standard error prefix; the bounded recovery
            # object is JSON rather than a traceback or successful empty page.
            error = json.loads(failed.content[0].text.split(": ", 1)[1])
            assert error["ok"] is False and error["read_complete"] is False
            recovery = error["recovery"]
            assert recovery["scope"] == {"goal_id": "delivery", "agent_id": "worker", "goal_ref": ref}
            assert recovery["read_arguments"] == {"cursor": cursor, "operation_cursor": None,
                "request_id": None, "turn_instance_id": turn_key}
            assert {row["request_id"] for row in recovery["requests"]} == {row["request_id"] for row in tail}
            assert recovery["peer_returns"] == [{"request": {"request_id": rid, "goal_id": "delivery",
                "agent_id": "source", "goal_ref": ref}, "result_key": "conclusion"}]
            assert recovery["automatic_retry_performed"] is False
            assert "Private source body" not in str(error) and "Private peer conclusion" not in str(error)
            assert "instruction" not in error and str(tmp_path) not in str(error)
            before = {path: path.read_bytes() for path in _root(tmp_path).rglob("*.json")}
            receipts = [_receipt(tmp_path, "reads", row)[0] for row in tail]
            assert sum(bool(row) for row in receipts) == 1
            assert {path: path.read_bytes() for path in before} == before
            assert not (_root(tmp_path) / "replies" / rid / "conclusion.delivery.json").exists()
            disposition = {"request_id": rid, "disposition": "accepted"}
            refused = await session.call_tool("consume_peer_result", disposition)
            assert refused.isError and "read the peer conclusion" in refused.content[0].text

            failed_peer_read = await session.call_tool("read_peer_returns", {})
            assert failed_peer_read.isError and "Repeat the same peer result read in this work Turn" in failed_peer_read.content[0].text
            assert "Private peer conclusion" not in failed_peer_read.content[0].text
            delivery = _root(tmp_path) / "replies" / rid / "conclusion.delivery.json"
            receipt = delivery.read_bytes()
            assert json.loads(receipt)["model_turn"]["turn_instance_id"] == turn_key
            refused = await session.call_tool("consume_peer_result", disposition)
            assert refused.isError and "read_token" in refused.content[0].text
            cli_acknowledge = [sys.executable, "-m", "loopx.entrypoint", "--format", "json",
                "--registry", str(registry), "--runtime-root", str(tmp_path), "manager-inbox",
                "acknowledge-return", "--goal-id", "delivery", "--agent-id", "worker",
                "--request-id", rid, "--turn-instance-id", turn_key, "--disposition", "accepted"]
            refused_cli = subprocess.run(cli_acknowledge, capture_output=True, timeout=15)
            assert refused_cli.returncode == 1 and "read_token" in json.loads(refused_cli.stdout)["error"]
            assert not list((_root(tmp_path) / "replies").rglob("*.consumed.json"))
            recovered_peer = await session.call_tool("read_peer_returns", {})
            assert not recovered_peer.isError
            result, = json.loads(recovered_peer.content[0].text)["peer_returns"]["items"]
            assert result["request_id"] == rid and result["text"] == "Private peer conclusion"
            read_token = result["read_token"]
            assert read_token not in failed_peer_read.content[0].text
            assert delivery.read_bytes() == receipt
            assert [_receipt(tmp_path, "reads", row)[0] for row in tail] == receipts
            recovered = await session.call_tool("read_context", {"cursor": cursor})
            assert not recovered.isError
            recovered_page = json.loads(recovered.content[0].text)
            assert {row["request_id"] for row in recovered_page["items"]} == {row["request_id"] for row in tail}
            full_result, = recovered_page["peer_returns"]["items"]
            assert full_result["read_token"] == read_token
            assert read_token in full_result["acknowledge_command"]
            assert all(path.read_bytes() == data for path, data in before.items())
            delivery = _root(tmp_path) / "replies" / rid / "conclusion.delivery.json"
            assert json.loads(delivery.read_text())["model_turn"]["turn_instance_id"] == turn_key
            assert not list((_root(tmp_path) / "replies").rglob("*.consumed.json"))
            consumed = await session.call_tool("consume_peer_result", {**disposition, "read_token": read_token})
            assert not consumed.isError
            assert json.loads(consumed.content[0].text)["status"] == "consumed"
            replay = subprocess.run([*cli_acknowledge, "--read-token", read_token], capture_output=True,
                                    timeout=15, check=True)
            assert json.loads(replay.stdout)["replay"] is True
            with source_session_registry_transaction(registry, operation="revoke_fixture_agent") as transaction:
                changed = transaction.payload_copy()
                changed["goals"][0]["coordination"]["registered_agents"] = ["source"]
                transaction.commit(changed)
            revoked = await session.call_tool("read_context", {"cursor": cursor})
            assert revoked.isError and "requires registered Agents" in revoked.content[0].text
            assert '"recovery"' not in revoked.content[0].text
            blocked_peer_read = await session.call_tool("read_peer_returns", {})
            assert blocked_peer_read.isError and "requires registered Agents" in blocked_peer_read.content[0].text

    asyncio.run(exercise())


@pytest.mark.parametrize("host_turn_key", [None, "", " \t "])
def test_collaboration_mcp_accepts_unbound_turn(tmp_path, monkeypatch, host_turn_key):
    from loopx.collaboration_mcp import _bound_work_turn_identity

    if host_turn_key is None:
        monkeypatch.delenv("LOOPX_TURN_KEY", raising=False)
    else:
        monkeypatch.setenv("LOOPX_TURN_KEY", host_turn_key)

    assert _bound_work_turn_identity("delivery", "worker", tmp_path) is None


def test_collaboration_mcp_rejects_mismatched_host_bound_turn_scope(tmp_path, monkeypatch):
    from loopx.collaboration_mcp import _bound_work_turn_identity

    monkeypatch.setenv("LOOPX_TURN_KEY", "sha256:" + "d" * 64)
    monkeypatch.setenv("LOOPX_TURN_GOAL_ID", "other")
    monkeypatch.setenv("LOOPX_TURN_AGENT_ID", "worker")
    monkeypatch.setenv("LOOPX_TURN_WORKSPACE", str(tmp_path.resolve()))
    with pytest.raises(ValueError, match="scope does not match"):
        _bound_work_turn_identity("delivery", "worker", tmp_path)


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
                "original_conversation_collaboration",
            }
            read_tool = next(tool for tool in tools.tools if tool.name == "read_context")
            consume_tool = next(tool for tool in tools.tools if tool.name == "consume_peer_result")
            assert set(read_tool.inputSchema["properties"]) == {"cursor"}
            assert set(consume_tool.inputSchema["properties"]) == {"request_id", "disposition", "result_key", "read_token"}
            assert set(consume_tool.inputSchema["required"]) == {"request_id", "disposition"}
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
            assert "unknown costs or missing evidence" in returned.description
            assert "Do not add a second model rewrite" in returned.description
            peer = next(tool for tool in tools.tools if tool.name == "request_peer")
            assert "complete goal, relevant corrections, intended reader, language and original result destination" in peer.description
            assert "negative results and unknowns" in peer.description
            # A legacy Goal alias cannot authorize original-session IO.
            result = await session.call_tool(
                "original_conversation_collaboration",
                {"operation": "discover", "parameters": {"parent_request_id": "a" * 64}},
            )
            assert result.isError
            assert "exact Goal instance" in " ".join(
                block.text for block in result.content if block.type == "text"
            )
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
                "original_conversation_collaboration",
                {"operation": "discover", "parameters": {"parent_request_id": "a" * 64}},
            )
            assert result.isError
            assert "requires registered Agents" in " ".join(
                block.text for block in result.content if block.type == "text"
            )
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_bound_peer_read_preserves_result_without_resupplying_inbox(tmp_path, monkeypatch, provider):
    from loopx.control_plane.collaboration.inbox import _root, acknowledge
    from loopx.control_plane.collaboration.peers import request, return_result
    from loopx.control_plane.turn_driver.codex_cli import (
        LOOPX_DELEGATION_CODEX_MCP_SERVER_NAME, collaboration_mcp_turn_environment,
    )
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, root, _ = promoted_create_fixture(tmp_path, provider=provider)
    config = json.loads(registry.read_text())
    config["goals"][0]["coordination"]["registered_agents"].append("agent-b")
    registry.write_text(json.dumps(config))
    brief = {"schema_version": "collaboration_brief_v0", "purpose": "Inspect the artifact",
        "context": "Review the evidence.", "constraints": [], "inputs": [],
        "acceptance": ["Return the verified finding"], "return_requirement": "Report the result"}
    unrelated = {**brief, "context": "Unrelated Inbox evidence. " * 150}
    incoming = {
        request(root, registry, "goal-a", "agent-b", "agent-a", f"inbox-{index}", unrelated)["request_id"]
        for index in range(6)
    }
    rid = request(root, registry, "goal-a", "agent-a", "agent-b", "peer-review", brief)["request_id"]
    acknowledge(root, "goal-a", "agent-b", rid, "adopt", "Review the artifact", registry=registry)
    return_result(root, "goal-a", "agent-b", rid, "One finding needs a follow-up.", registry=registry)
    delivery = _root(root) / "replies" / rid / "conclusion.delivery.json"

    opening = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--format", "json",
        "--registry", str(registry), "--runtime-root", str(root), "manager-inbox", "read",
        "--goal-id", "goal-a", "--agent-id", "agent-a"], capture_output=True, timeout=30, check=True)
    opening_view = json.loads(opening.stdout)
    assert {row["request_id"] for row in opening_view["items"]} == incoming
    assert "read_peer_returns" in opening_view["followthrough"]
    assert "model_turn" not in json.loads(delivery.read_text())

    turn = "sha256:" + "e" * 64
    bound_environment = collaboration_mcp_turn_environment(
        {"name": LOOPX_DELEGATION_CODEX_MCP_SERVER_NAME}, turn_key=turn,
        lineage={"goal_id": "goal-a", "agent_id": "agent-a"}, project=tmp_path,
    )
    params = StdioServerParameters(command=sys.executable, args=[
        "-m", "loopx.collaboration_mcp", "--runtime-root", str(root),
        "--registry", str(registry), "--goal-id", "goal-a", "--agent-id", "agent-a", "--workspace", str(tmp_path),
    ], env={**os.environ, **bound_environment})

    async def exercise():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            args = {"request_id": rid, "disposition": "accepted"}
            unproved = await session.call_tool("consume_peer_result", args)
            assert unproved.isError and "in this work Turn" in unproved.content[0].text

            result = await session.call_tool("read_peer_returns", {})
            assert not result.isError
            narrow = json.loads(result.content[0].text)
            assert set(narrow) == {"peer_returns"}
            peer, = narrow["peer_returns"]["items"]
            assert {key: value for key, value in peer.items() if key != "read_token"} == opening_view["peer_returns"]["items"][0]
            assert peer["request_id"] == rid and peer["text"] == "One finding needs a follow-up."
            assert json.loads(delivery.read_text())["model_turn"]["turn_instance_id"] == turn
            assert not list((_root(root) / "replies" / rid).glob("*.consumed.json"))

            full = await session.call_tool("read_context", {})
            assert not full.isError
            full_view = json.loads(full.content[0].text)
            assert {row["request_id"] for row in full_view["items"]} == incoming
            previous_peer, = full_view["peer_returns"]["items"]
            assert {key: value for key, value in previous_peer.items() if key != "acknowledge_command"} == peer

            consumed = await session.call_tool("consume_peer_result", {**args, "read_token": peer["read_token"]})
            assert not consumed.isError and json.loads(consumed.content[0].text)["status"] == "consumed"
            empty = await session.call_tool("read_peer_returns", {})
            assert not empty.isError
            assert json.loads(empty.content[0].text) == {"peer_returns": {"items": [], "has_more": False}}

    asyncio.run(exercise())


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_bound_peer_return_pages_keep_deferred_results_for_the_next_turn(tmp_path, monkeypatch, provider):
    from loopx.control_plane.collaboration.inbox import _root, acknowledge
    from loopx.control_plane.collaboration.peers import request, return_result
    from loopx.control_plane.turn_driver.codex_cli import (
        LOOPX_DELEGATION_CODEX_MCP_SERVER_NAME, collaboration_mcp_turn_environment,
    )
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, root, _ = promoted_create_fixture(tmp_path, provider=provider)
    config = json.loads(registry.read_text())
    config["goals"][0]["coordination"]["registered_agents"].append("agent-b")
    registry.write_text(json.dumps(config))
    brief = {"schema_version": "collaboration_brief_v0", "purpose": "Inspect the artifact",
        "context": "Review the evidence.", "constraints": [], "inputs": [],
        "acceptance": ["Return the verified finding"], "return_requirement": "Report the result"}
    results = {}
    for index, count in enumerate((21, 2)):
        rid = request(root, registry, "goal-a", "agent-a", "agent-b", f"review-{index}", brief)["request_id"]
        acknowledge(root, "goal-a", "agent-b", rid, "adopt", "Review the artifact", registry=registry)
        for number in range(count):
            reply = f"Finding {index}-{number} needs independent validation."
            result = return_result(root, "goal-a", "agent-b", rid, reply, registry=registry,
                                   update_id=f"finding-{number}" if number else None)
            results[rid, result.get("result_key", "conclusion")] = reply

    def launch(turn):
        env = collaboration_mcp_turn_environment(
            {"name": LOOPX_DELEGATION_CODEX_MCP_SERVER_NAME}, turn_key=turn,
            lineage={"goal_id": "goal-a", "agent_id": "agent-a"}, project=tmp_path,
        )
        return StdioServerParameters(command=sys.executable, args=[
            "-m", "loopx.collaboration_mcp", "--runtime-root", str(root), "--registry", str(registry),
            "--goal-id", "goal-a", "--agent-id", "agent-a", "--workspace", str(tmp_path),
        ], env={**os.environ, **env})

    def result_id(row):
        return row["request_id"], row.get("result_key", "conclusion")

    async def page(session, **args):
        response = await session.call_tool("read_peer_returns", args)
        assert not response.isError, response
        return json.loads(response.content[0].text)["peer_returns"]

    async def exercise():
        first_turn, later_turn = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        async with stdio_client(launch(first_turn)) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            first = await page(session)
            assert first["has_more"] and len(first["items"]) == 20
            first_ids = {result_id(row) for row in first["items"]}
            cursor = first.get("next_cursor")
            assert cursor, "the pending tail has no continuation"

            for row in first["items"]:
                request_id, key = result_id(row)
                disposition = {"request_id": request_id, "result_key": key, "disposition": "deferred",
                    "read_token": row["read_token"]}
                reply = await session.call_tool("consume_peer_result", disposition)
                assert not reply.isError and json.loads(reply.content[0].text)["status"] == "deferred"
            assert await page(session) == first

            later_id, later_key = min(results.keys() - first_ids)
            disposition = {"request_id": later_id, "result_key": later_key, "disposition": "accepted"}
            assert (await session.call_tool("consume_peer_result", disposition)).isError
            delivery = _root(root) / "replies" / later_id / (later_key + ".delivery.json")
            assert not delivery.exists()
            tail = await page(session, cursor=cursor)
            assert await page(session, cursor=cursor) == tail
            assert not tail["has_more"]
            assert {result_id(row) for row in tail["items"]} == results.keys() - first_ids
            assert {result_id(row): row["text"] for row in first["items"] + tail["items"]} == results
            assert json.loads(delivery.read_text())["model_turn"]["turn_instance_id"] == first_turn
            foreign = await session.call_tool("consume_peer_result", {**disposition, "read_token": first["items"][0]["read_token"]})
            assert foreign.isError and "read_token" in foreign.content[0].text
            read_token = next(row["read_token"] for row in tail["items"] if result_id(row) == (later_id, later_key))
            accepted = await session.call_tool("consume_peer_result", {**disposition, "read_token": read_token})
            assert not accepted.isError and json.loads(accepted.content[0].text)["status"] == "consumed"

        async with stdio_client(launch(later_turn)) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            rejected = await session.call_tool("read_peer_returns", {"cursor": cursor})
            assert rejected.isError and "cursor scope mismatch" in rejected.content[0].text
            reopened = await page(session)
            assert {result_id(row) for row in reopened["items"]} == first_ids
            prior = first["items"][0]
            stale = await session.call_tool("consume_peer_result", {"request_id": prior["request_id"],
                "result_key": prior.get("result_key", "conclusion"), "disposition": "accepted",
                "read_token": prior["read_token"]})
            assert stale.isError and "read_token" in stale.content[0].text
            remaining = await page(session, cursor=reopened["next_cursor"])
            assert {result_id(row) for row in remaining["items"]} == results.keys() - first_ids - {(later_id, later_key)}

    asyncio.run(exercise())


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_stdio_receiver_links_existing_work_and_returns_without_duplicate_tasks(tmp_path, monkeypatch, provider):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
    from loopx.control_plane.collaboration.peers import request, read_inbox
    from loopx.control_plane.collaboration.inbox import _root

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, root, _ = promoted_create_fixture(tmp_path, provider=provider)
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

            cli_view = cli("manager-inbox", "read", "--goal-id", "goal-a", "--agent-id", "agent-a")
            assert "Preserve the original author, audience and facts" in cli_view["instruction"]
            assert "never arbitrary length caps, truncation" in cli_view["instruction"]
            first_view = await call("read_context", {})
            assert "Explain unfamiliar terms on first use" in first_view["instruction"]
            assert "Keep typed remaining_work semantics intact" in first_view["instruction"]
            first = first_view["items"][0]
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
                (own, "[P1] Current accepted work", "open")
            ]
            cli("todo", "complete", "--goal-id", "goal-a", "--todo-id", own,
                "--agent-id", "agent-a", "--evidence", "Verified result", "--no-follow-up")
            cli("todo", "archive-completed", "--goal-id", "goal-a",
                "--max-active-done", "0", "--execute")
            resumed = (await call("read_context", {}))["items"][0]["receiver_followthrough"]
            assert resumed["step"] == "review_request_work" and resumed["answer_owed"]
            assert [(t["todo_id"], t["status"]) for t in resumed["linked_todos"]] == [(own, "done")]
            assert path.read_bytes() == original
            remainder = {"summary": "Check the installed repair", "resume_when": "The isolated package is ready"}
            await call("return_result", {"request_id": rid, "text": "Implementation ready", "remaining_work": remainder})
            pending = (await call("read_context", {}))["items"][0]
            assert pending["remaining_work"] == remainder
            assert pending["receiver_followthrough"]["step"] == "review_request_work"
            await call("return_result", {"request_id": rid, "text": conclusion, "update_id": "checked-repair"})
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
    assert len(received) == 3
    assert next(item for item in received if item.get("result_key") == "conclusion-00000001")["text"] == conclusion
    assert [row["todo_id"] for row in cli("todo", "list", "--goal-id", "goal-a")["todos"]] == [foreign]
    assert cli("todo", "list", "--goal-id", "goal-a", "--todo-id", own)["todo"]["archive_state"] == "archive"
