"""Receiver recovery through real CLI processes and identity-bound MCP stdio."""

import asyncio
import hashlib
import json
import subprocess
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from loopx.control_plane.collaboration.inbox import acknowledge
from loopx.control_plane.collaboration.peers import request


@pytest.fixture
def inbox(tmp_path):
    registry = tmp_path / "registry.json"
    config = {"goals": [
        {"id": goal, "repo": str(tmp_path),
         "coordination": {"registered_agents": ["sender", "receiver", "other"]}}
        for goal in ("delivery", "another")
    ]}
    registry.write_text(json.dumps(config))
    brief = {
        "schema_version": "collaboration_brief_v0",
        "purpose": "Review the corrected allocation",
        "context": "Proportional rounding was rejected. Reserve two units.",
        "constraints": ["Do not place orders"],
        "inputs": [],
        "acceptance": ["Check the reserve and capacity"],
        "return_requirement": "Return findings and remaining gaps",
    }

    def seed(count):
        return sorted(
            request(tmp_path, registry, "delivery", "sender", "receiver",
                    f"review-{number}", brief)["request_id"]
            for number in range(count)
        )

    return tmp_path, registry, seed, brief


def cli(root, registry, action="read", *args, agent="receiver", goal="delivery", ok=True):
    result = subprocess.run([
        sys.executable, "-m", "loopx.cli",
        "--runtime-root", str(root), "--registry", str(registry),
        "manager-inbox", action, "--goal-id", goal, "--agent-id", agent, *args,
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == (0 if ok else 1), (result.stdout, result.stderr)
    return json.loads(result.stdout)


def read_ids(root):
    return {path.stem for path in (root / ".local/manager-context/reads").glob("*.json")}


def ids(page):
    return [row["request_id"] for row in page["items"]]


def test_cli_recovers_later_context_without_concluding_deferred_requests(inbox):
    root, registry, seed, brief = inbox
    expected = seed(45)
    for request_id in expected[:20]:
        acknowledge(root, "delivery", "receiver", request_id, "defer", "Await input.")
    first = cli(root, registry)
    assert ids(first) == expected[:20] and first["has_more"]
    assert all(row["receiver_decision_recorded"] for row in first["items"])
    assert read_ids(root) == set(expected[:20])
    assert first.get("next_cursor"), "pending request 21 has no recovery cursor"

    second = cli(root, registry, "read", "--cursor", first["next_cursor"])
    assert ids(second) == expected[20:40] and second["has_more"]
    assert second["items"][0]["brief"] == brief
    assert read_ids(root) == set(expected[:40])
    # Every CLI invocation starts a fresh process; replay never consumes a page.
    assert cli(root, registry, "read", "--cursor", first["next_cursor"]) == second
    third = cli(root, registry, "read", "--cursor", second["next_cursor"])
    assert ids(third) == expected[40:]
    assert not third["has_more"] and third["next_cursor"] is None
    assert read_ids(root) == set(expected)

    chosen = expected[20]
    cli(root, registry, "acknowledge", "--request-id", chosen,
        "--decision", "adopt", "--reason", "Check the corrected reserve.")
    cli(root, registry, "report", "--request-id", chosen,
        "--reply-text", "Reserve check passed; capacity remains unverified.")
    returned = cli(root, registry, agent="sender")["peer_returns"]["items"]
    assert [(row["request_id"], row["text"]) for row in returned] == [
        (chosen, "Reserve check passed; capacity remains unverified.")]
    cli(root, registry, "acknowledge-return", "--request-id", chosen, agent="sender")
    assert "peer_returns" not in cli(root, registry, agent="sender")
    assert ids(cli(root, registry)) == expected[:20]
    assert chosen not in ids(cli(root, registry, "read", "--cursor", first["next_cursor"]))

    # Neither a removed anchor nor completed earlier rows shifts the continuation.
    anchor = next((root / ".local/manager-context/entries").glob(f"*/{expected[19]}.json"))
    anchor.unlink()
    cli(root, registry, "report", "--request-id", expected[0], "--reply-text", "Still blocked.")
    resumed = cli(root, registry, "read", "--cursor", first["next_cursor"])
    assert ids(resumed) == expected[21:41]


@pytest.mark.parametrize("count", [0, 20, 21])
def test_cli_page_boundaries(inbox, count):
    root, registry, seed, _ = inbox
    expected = seed(count)
    page = cli(root, registry)
    assert ids(page) == expected[:20]
    assert page["has_more"] is (count > 20)
    assert bool(page["next_cursor"]) is (count > 20)
    assert read_ids(root) == set(expected[:20])


def test_cli_resumes_cursor_issued_before_exact_goal_scoping(inbox):
    root, registry, seed, _ = inbox
    expected = seed(45)
    legacy_scope = hashlib.sha256(
        json.dumps(
            [
                "pending_requests_v1",
                str(root.resolve()),
                "delivery",
                "receiver",
            ],
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    cursor = f"1:{legacy_scope}:{expected[19]}"

    resumed = cli(root, registry, "read", "--cursor", cursor)

    assert ids(resumed) == expected[20:40]
    assert resumed["has_more"]


def test_cli_rejects_wrong_scope_and_malformed_cursors_before_receipts(inbox):
    root, registry, seed, _ = inbox
    expected = seed(21)
    cursor = cli(root, registry)["next_cursor"]
    for token in ("", "../entry", "2:" + "a" * 64 + ":" + "b" * 64, cursor + "x"):
        result = cli(root, registry, "read", "--cursor", token, ok=False)
        assert "cursor" in result["error"]
    for overrides in ({"agent": "other"}, {"goal": "another"}):
        assert "scope" in cli(root, registry, "read", "--cursor", cursor,
                              ok=False, **overrides)["error"]
    other_root = root / "other-runtime"
    assert "scope" in cli(other_root, registry, "read", "--cursor", cursor, ok=False)["error"]
    assert not other_root.exists()
    assert "read" in cli(root, registry, "status", "--cursor", cursor, ok=False)["error"]
    assert read_ids(root) == set(expected[:20])


def test_new_arrivals_before_cursor_are_found_by_fresh_scan(inbox):
    root, registry, seed, _ = inbox
    expected = seed(45)
    entry = next((root / ".local/manager-context/entries").glob(f"*/{expected[0]}.json"))
    original = entry.read_bytes()
    entry.unlink()
    first = cli(root, registry)
    assert ids(first) == expected[1:21]
    entry.write_bytes(original)
    second = cli(root, registry, "read", "--cursor", first["next_cursor"])
    assert ids(second) == expected[21:41]
    assert expected[0] in ids(cli(root, registry))


@pytest.mark.parametrize("damage", ["directory", "identity", "schema", "filename"])
def test_unreadable_or_conflicting_entries_fail_without_read_receipts(inbox, damage):
    root, registry, seed, _ = inbox
    expected = seed(1)
    entry = next((root / ".local/manager-context/entries").glob(f"*/{expected[0]}.json"))
    if damage == "directory":
        folder = entry.parent
        entry.unlink()
        folder.rmdir()
        folder.write_text("not a directory")
    elif damage == "filename":
        entry.rename(entry.with_name("invalid.json"))
    else:
        row = json.loads(entry.read_text())
        row["request_id" if damage == "identity" else "schema_version"] = "invalid"
        entry.write_text(json.dumps(row))
    assert not cli(root, registry, ok=False)["ok"]
    assert read_ids(root) == set()


def test_mcp_continuation_survives_restart_and_rechecks_registration(inbox):
    root, registry, seed, brief = inbox
    expected = seed(45)
    params = StdioServerParameters(command=sys.executable, args=[
        "-m", "loopx.collaboration_mcp", "--runtime-root", str(root),
        "--registry", str(registry), "--goal-id", "delivery",
        "--agent-id", "receiver", "--workspace", str(root),
    ])

    async def exercise():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            read_tool = next(tool for tool in tools.tools if tool.name == "read_context")
            assert set(read_tool.inputSchema["properties"]) == {"cursor"}
            result = await session.call_tool("read_context", {})
            assert not result.isError
            first = json.loads(result.content[0].text)
            assert ids(first) == expected[:20]
            assert read_ids(root) == set(expected[:20])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("read_context", {"cursor": first["next_cursor"]})
            assert not result.isError
            second = json.loads(result.content[0].text)
            assert ids(second) == expected[20:40]
            assert second["items"][0]["brief"] == brief
            assert read_ids(root) == set(expected[:40])
            result = await session.call_tool("read_context", {"cursor": "../invalid"})
            assert result.isError
            # Revocation affects the same running server and an otherwise valid cursor.
            config = json.loads(registry.read_text())
            config["goals"][0]["coordination"]["registered_agents"] = ["sender"]
            registry.write_text(json.dumps(config))
            result = await session.call_tool("read_context", {"cursor": second["next_cursor"]})
            assert result.isError
            assert read_ids(root) == set(expected[:40])
            config["goals"][0]["coordination"]["registered_agents"].append("receiver")
            config["goals"][0]["status"] = "stopped"
            registry.write_text(json.dumps(config))
            result = await session.call_tool("read_context", {"cursor": second["next_cursor"]})
            assert not result.isError
            third = json.loads(result.content[0].text)
            assert ids(third) == expected[40:]
            assert third["next_cursor"] is None

    asyncio.run(exercise())
