"""Native CLI reads use the same scoped Core handler, for one invocation."""
import asyncio
import io
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from loopx.capabilities.manager_context.inspection import CONTEXT_READ_TOOL
from loopx.chat_agent import CodexChatAgentError
from loopx.chat_native_tool_bridge import NativeChatToolBridge
from loopx.chat_providers import ClaudeCodeAdapter
from loopx.chat_manager import MANAGER_AGENT_GOAL_ID, manager_model_config
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore


def test_real_mcp_client_reads_and_revocation_use_bound_handler():
    calls = []
    granted = True

    def reader(name, arguments):
        calls.append((name, arguments))
        return {"ok": granted, "error": None if granted else "scope_revoked"}

    async def check(bridge):
        configuration = bridge.mcp_configuration()["mcpServers"]["loopx"]
        params = StdioServerParameters(**configuration)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert [tool.name for tool in tools.tools] == ["loopx_context_read"]
                result = await client.call_tool("loopx_context_read", {"view": "todos"})
                assert result.isError is False
                nonlocal granted
                granted = False
                result = await client.call_tool("loopx_context_read", {"view": "todos"})
                assert result.isError is True
                assert json.loads(result.content[0].text)["error"] == "scope_revoked"
                rejected = await client.call_tool("unoffered_write", {})
                assert rejected.isError is True

    with NativeChatToolBridge([CONTEXT_READ_TOOL], reader) as bridge:
        asyncio.run(check(bridge))
    assert calls == [("loopx_context_read", {"view": "todos"})] * 2


def test_bridge_token_and_lifetime_do_not_grant_cross_invocation_access():
    calls = []
    with NativeChatToolBridge([CONTEXT_READ_TOOL], lambda *args: calls.append(args) or {"ok": True}) as bridge:
        url = bridge.mcp_configuration()["mcpServers"]["loopx"]["env"]["LOOPX_NATIVE_CHAT_TOOL_URL"]
        request = Request(url, data=b'{"method":"tools/call","params":{"name":"loopx_context_read"}}',
                          headers={"Authorization": "Bearer wrong-invocation"})
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=2)
        assert error.value.code == 403
    assert calls == []
    with pytest.raises(URLError):
        urlopen(request, timeout=2)


@pytest.mark.parametrize("spawn_failure", [False, True])
def test_native_adapter_advertises_only_scoped_tools_and_closes_bridge(monkeypatch, tmp_path, spawn_failure):
    configuration = None
    calls = []

    def spawn(command, **kwargs):
        nonlocal configuration
        assert "--strict-mcp-config" in command
        assert command[command.index("--tools") + 1] == ""
        assert command[command.index("--allowedTools") + 1] == "mcp__loopx__loopx_context_read"
        configuration = json.loads(command[command.index("--mcp-config") + 1])["mcpServers"]["loopx"]
        assert configuration["command"] == sys.executable
        env = configuration["env"]
        with urlopen(Request(env["LOOPX_NATIVE_CHAT_TOOL_URL"], data=b'{"method":"tools/list"}',
                             headers={"Authorization": "Bearer " + env["LOOPX_NATIVE_CHAT_TOOL_TOKEN"]}), timeout=2) as response:
            assert json.load(response)["tools"][0]["annotations"]["readOnlyHint"] is True
        request = Request(env["LOOPX_NATIVE_CHAT_TOOL_URL"],
                          data=json.dumps({"method": "tools/call", "params": {
                              "name": "loopx_context_read", "arguments": {"view": "todos"},
                          }}).encode(), headers={"Authorization": "Bearer " + env["LOOPX_NATIVE_CHAT_TOOL_TOKEN"]})
        with urlopen(request, timeout=2) as response:
            assert json.load(response) == {"ok": True, "todos": []}
        if spawn_failure:
            raise OSError("synthetic unavailable host")

        class Process:
            stdout = io.StringIO('{"type":"result","result":"Read scoped evidence"}\n')

            def wait(self):
                return 0

        return Process()

    monkeypatch.setattr("loopx.chat_providers.subprocess.Popen", spawn)
    adapter = ClaudeCodeAdapter("claude", tmp_path, "fixture-session", tool_scope="disabled",
                                dynamic_tools=[CONTEXT_READ_TOOL])
    adapter.read_tool_handler = lambda *args: calls.append(args) or {"ok": True, "todos": []}
    if spawn_failure:
        with pytest.raises(CodexChatAgentError):
            adapter.start_turn("Read my Goal", lambda *_: None)
    else:
        adapter.start_turn("Read my Goal", lambda *_: None)
    assert calls == [("loopx_context_read", {"view": "todos"})]
    with pytest.raises(URLError):
        urlopen(configuration["env"]["LOOPX_NATIVE_CHAT_TOOL_URL"], timeout=2)


@pytest.mark.parametrize("profile", ["restricted", "trusted_owner"])
def test_native_manager_uses_existing_runtime_grant_and_configured_model(monkeypatch, tmp_path, profile):
    runtime = ChatRuntimeController(store=ChatSessionStore(tmp_path / "runtime"), codex_bin="codex")
    calls = []
    monkeypatch.setattr(ClaudeCodeAdapter, "start", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(runtime, "steward_executor_defaults", lambda: {
        "executor_endpoint": "claude-code", "executor_model": "fixture-native-model",
        "executor_reasoning_effort": "low",
    })
    runtime._start_adapter(agent_id="claude-code", work_dir=tmp_path, goal_id=MANAGER_AGENT_GOAL_ID,
        objective="Manager", manager_runtime={"runtime_profile": profile},
        history=[{"role": "user", "content": "Retain this correction"}])
    call = calls[0]
    assert call["model"] == "fixture-native-model" and call["reasoning_effort"] == "low"
    assert call["tool_scope"] == "disabled" and call["runtime_profile"] == profile
    assert [tool["name"] for tool in call["dynamic_tools"]] == ["loopx_manager_read"]
    assert "Retain this correction" in call["context_summary"]
    assert ("Do not inspect arbitrary repositories" in call["context_summary"]) is (profile == "restricted")


def test_native_manager_vendor_default_never_inherits_codex_model():
    assert manager_model_config({}, endpoint="claude-code")["model"] == "sonnet"
    assert manager_model_config({}, endpoint="codex")["model"] == "gpt-6-astra"
