from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from loopx.extensions.turn_host import supervised_stdio_command

from .config import AdapterError, Config


class SelectedTools:
    def __init__(self, session: ClientSession | None, definitions: list) -> None:
        self.session = session
        self.names = {tool.name for tool in definitions}
        self.schemas = [{"type": "function", "function": {
            "name": tool.name, "description": tool.description or "",
            "parameters": tool.inputSchema,
        }} for tool in definitions]

    async def call(self, call: dict) -> dict:
        function = call["function"]
        arguments = function["arguments"]
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if (not self.session or function["name"] not in self.names
                or not isinstance(arguments, dict)):
            raise AdapterError("unselected_tool_or_invalid_arguments")
        response = await self.session.call_tool(function["name"], arguments)
        if any(not isinstance(block, types.TextContent) for block in response.content):
            raise AdapterError("only_text_tool_results_supported")
        content = json.dumps({"content": [block.text for block in response.content],
                              "structured": response.structuredContent, "is_error": bool(response.isError)})
        if len(content.encode()) > 128_000:
            raise AdapterError("tool_response_exceeds_limit")
        return {"tool_call_id": call["id"], "content": content}


async def discover(session: ClientSession, names: tuple[str, ...]) -> list:
    found = {}
    cursor = None
    cursors = set()
    for _ in range(32):
        params: types.PaginatedRequestParams | None = types.PaginatedRequestParams(cursor=cursor) if cursor else None
        page = await session.list_tools(params=params)
        for tool in page.tools:
            if tool.name in found:
                raise AdapterError("duplicate_tool_name")
            found[tool.name] = tool
        cursor = page.nextCursor
        if not cursor:
            if set(names) - found.keys():
                raise AdapterError("selected_tool_unavailable")
            return [found[name] for name in names]
        if cursor in cursors:
            raise AdapterError("tool_pagination_cycle")
        cursors.add(cursor)
    raise AdapterError("tool_pagination_limit")


@asynccontextmanager
async def connect(config: Config, identity: dict):
    if not config.mcp_command:
        yield SelectedTools(None, [])
        return
    # Provider credentials and arbitrary parent variables never reach local tools.
    environment = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG") if name in os.environ}
    environment.update({"LOOPX_TURN_" + name: text for name, text in identity.items()})
    environment["LOOPX_TURN_WORKSPACE"] = config.workspace
    command = supervised_stdio_command(config.mcp_command)
    server = StdioServerParameters(command=command[0], args=command[1:],
                                   env=environment, cwd=config.workspace)
    async with stdio_client(server) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=config.timeout_seconds)) as session:
            await session.initialize()
            yield SelectedTools(session, await discover(session, tuple(config.tool_names)))
