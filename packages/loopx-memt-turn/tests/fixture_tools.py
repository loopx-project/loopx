"""Synthetic scoped tool; never reads provider credentials or arbitrary paths."""
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("memt-contract-fixture")


@server.tool()
def record_observation(observation: str) -> str:
    workspace = Path(os.environ["LOOPX_TURN_WORKSPACE"])
    if "MEMT_API_KEY" in os.environ:
        raise RuntimeError("credential forwarded to tool")
    with (workspace / "observations.txt").open("a") as stream:
        stream.write(observation + "\n")
    return "observation recorded"


server.run(transport="stdio")
