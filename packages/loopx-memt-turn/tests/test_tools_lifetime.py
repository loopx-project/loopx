"""Real MCP stdio under the real Turn supervisor, without model inference."""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

from loopx.control_plane.turn_driver.host_process_transport import run_host_process


@pytest.mark.skipif(os.name != "posix", reason="Windows process-tree cleanup remains best effort")
@pytest.mark.parametrize("stop", ["timeout", "cancel", "hard_parent_exit"])
def test_detached_real_mcp_cannot_write_after_turn_supervision_returns(tmp_path, stop):
    ready, release, late = (tmp_path / name for name in ("ready", "release", "late"))
    server = tmp_path / "server.py"
    server.write_text(
        "from pathlib import Path\nimport time\nfrom mcp.server.fastmcp import FastMCP\n"
        "server=FastMCP('process-lifetime-fixture')\n"
        "@server.tool()\ndef delayed_effect() -> str:\n"
        f"    Path({str(ready)!r}).write_text('ready')\n"
        "    deadline=time.monotonic()+20\n"
        f"    while not Path({str(release)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        f"    if Path({str(release)!r}).exists(): Path({str(late)!r}).write_text('late')\n"
        "    return 'done'\nserver.run(transport='stdio')\n"
    )
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        "import asyncio,os\nfrom pathlib import Path\n"
        "from loopx_memt_turn.config import Config\nfrom loopx_memt_turn.tools import connect\n"
        "async def main():\n"
        f"    config=Config(workspace={str(tmp_path)!r}, state_dir={str(tmp_path.parent / (tmp_path.name + '-private'))!r}, "
        f"tokenizer={str(tmp_path)!r}, base_url='http://127.0.0.1:1', enabled=True, "
        f"mcp_command=({sys.executable!r},{str(server)!r}), tool_names=('delayed_effect',), timeout_seconds=20)\n"
        "    async with connect(config, {}) as tools:\n"
        "        pending=asyncio.create_task(tools.call({'id':'fixture', 'function':{'name':'delayed_effect','arguments':{}}}))\n"
        f"        while not Path({str(ready)!r}).exists(): await asyncio.sleep(.01)\n"
        "        print('TOOL_STARTED', flush=True)\n"
        + ("        os._exit(9)\n" if stop == "hard_parent_exit" else "        await pending\n")
        + "asyncio.run(main())\n"
    )
    observed = ""
    errors = []

    def stdout(text):
        nonlocal observed
        observed += text
        if stop == "cancel" and "TOOL_STARTED" in observed:
            raise RuntimeError("synthetic owner cancellation")

    if stop == "cancel":
        with pytest.raises(RuntimeError, match="synthetic owner cancellation"):
            run_host_process([sys.executable, str(adapter)], project=tmp_path, input_text="",
                             timeout_seconds=8, on_stdout=stdout, on_stderr=errors.append)
    else:
        result = run_host_process([sys.executable, str(adapter)], project=tmp_path, input_text="",
                                  timeout_seconds=8, on_stdout=stdout, on_stderr=errors.append)
        assert result["outcome"] == ("timeout" if stop == "timeout" else "exited"), json.dumps(result) + "".join(errors)
        assert result["output_complete"] is True
        if stop == "hard_parent_exit":
            assert result["returncode"] == 9, "".join(errors)
    assert ready.exists() and "TOOL_STARTED" in observed, "".join(errors)
    release.write_text("continue")
    time.sleep(.3)
    assert not late.exists(), "an orphan MCP tool wrote after the supervisor returned"
