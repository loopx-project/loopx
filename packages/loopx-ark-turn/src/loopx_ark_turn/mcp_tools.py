"""One explicitly selected MCP tool set and process per bound Turn."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import AsyncIterator, Any, Mapping
import asyncio
import json

from arkruntime.mcp import custom_tool_items
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from loopx.extensions.turn_host import supervised_stdio_command

from .config import AdapterError, Config, tool_environment


class Tools:
    def __init__(self, session: ClientSession | None, definitions: list[types.Tool], config: Config) -> None:
        self.session = session
        self.definitions = definitions
        self.config = config
        self.names = {tool.name for tool in definitions}
        self.declarations = custom_tool_items(definitions)

    async def call(self, name: str, arguments: Any) -> dict[str, Any]:
        if self.session is None or name not in self.names or not isinstance(arguments, dict):
            raise AdapterError("tool_not_in_bound_selection_or_invalid_arguments")
        # MCP implementations own input/effect authorization. Neither a tool
        # declaration nor a cloud model's arguments confer additional authority.
        result = await asyncio.wait_for(
            self.session.call_tool(name, arguments), self.config.tool_timeout_seconds,
        )
        content = []
        for block in result.content:
            if not isinstance(block, types.TextContent):
                raise AdapterError("only_text_tool_results_are_qualified")
            content.append({"type": "text", "text": block.text})
        if not content and result.structuredContent is not None:
            content.append({"type": "text", "text": json.dumps(result.structuredContent)})
        payload = {"content": content, "is_error": bool(result.isError)}
        if len(json.dumps(payload).encode()) > 128_000:
            raise AdapterError("tool_result_exceeds_limit")
        return payload


@asynccontextmanager
async def connect(config: Config, identity: Mapping[str, str]) -> AsyncIterator[Tools]:
    if not config.mcp_command:
        yield Tools(None, [], config)
        return
    command = supervised_stdio_command(config.mcp_command)
    server = StdioServerParameters(
        command=command[0], args=command[1:],
        env=tool_environment(config, identity), cwd=str(config.workspace),
    )
    async with stdio_client(server) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=config.tool_timeout_seconds)) as session:
            await session.initialize()
            found: dict[str, types.Tool] = {}
            cursor = None
            cursors: set[str] = set()
            for _ in range(32):
                page = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor) if cursor else None)
                for tool in page.tools:
                    if tool.name in found:
                        raise AdapterError("duplicate_mcp_tool_name")
                    found[tool.name] = tool
                cursor = page.nextCursor
                if not cursor:
                    break
                if cursor in cursors:
                    raise AdapterError("mcp_tool_pagination_cycle")
                cursors.add(cursor)
            else:
                raise AdapterError("mcp_tool_pagination_limit")
            if set(config.tool_names) - found.keys():
                raise AdapterError("selected_mcp_tool_unavailable")
            yield Tools(session, [found[name] for name in config.tool_names], config)
