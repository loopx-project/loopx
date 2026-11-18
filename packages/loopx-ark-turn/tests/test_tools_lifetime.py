"""Real Ark MCP stdio under the Turn supervisor, without model inference."""
from __future__ import annotations

import json
import os
import signal
import sys
import time

import pytest

from loopx.control_plane.turn_driver.host_process_transport import run_host_process


def process_present(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(os.name != "posix", reason="Windows process-tree cleanup remains best effort")
@pytest.mark.parametrize("stop", ["completed", "timeout", "cancel", "hard_parent_exit"])
def test_real_mcp_is_gone_when_turn_supervision_returns(tmp_path, stop):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ready, release, late = (workspace / name for name in ("ready", "release", "late"))
    server = tmp_path / "server.py"
    server.write_text(
        "from pathlib import Path\nimport os,time\n"
        "os.environ['LOOPX_USAGE_PING']='0'\n"
        "from mcp.server.fastmcp import FastMCP\n"
        "server=FastMCP('process-lifetime-fixture')\n"
        "@server.tool()\ndef delayed_effect(wait: bool) -> str:\n"
        f"    Path({str(ready)!r}).write_text(str(os.getpid()))\n"
        "    if not wait: return 'done'\n"
        "    deadline=time.monotonic()+30\n"
        f"    while not Path({str(release)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        f"    if Path({str(release)!r}).exists(): Path({str(late)!r}).write_text('late')\n"
        "    return 'done'\nserver.run(transport='stdio')\n",
        encoding="utf-8",
    )
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        "import asyncio,os\nfrom pathlib import Path\n"
        "os.environ['LOOPX_USAGE_PING']='0'\n"
        "from loopx_ark_turn.config import Config\n"
        "from loopx_ark_turn.mcp_tools import connect\n"
        "async def main():\n"
        f"    config=Config(model='public-model', environment_id='env-fixture', workspace=Path({str(workspace)!r}), "
        f"state_dir=Path({str(tmp_path / 'receipts')!r}), "
        f"mcp_command=({sys.executable!r},{str(server)!r}), tool_names=('delayed_effect',), tool_timeout_seconds=30)\n"
        "    async with connect(config, {}) as tools:\n"
        f"        pending=asyncio.create_task(tools.call('delayed_effect', {{'wait': {stop != 'completed'!r}}}))\n"
        f"        while not Path({str(ready)!r}).exists(): await asyncio.sleep(.01)\n"
        "        print('TOOL_STARTED', flush=True)\n"
        + ("        os._exit(9)\n" if stop == "hard_parent_exit" else
           "        result=await pending\n"
           "        assert result == {'content': [{'type': 'text', 'text': 'done'}], 'is_error': False}\n"
           "        print('TOOL_COMPLETED', flush=True)\n")
        + "asyncio.run(main())\n",
        encoding="utf-8",
    )
    observed = ""
    errors = []

    def stdout(text):
        nonlocal observed
        observed += text
        if stop == "cancel" and "TOOL_STARTED" in observed:
            raise RuntimeError("synthetic owner cancellation")

    try:
        kwargs = dict(project=workspace, input_text="", timeout_seconds=8,
                      on_stdout=stdout, on_stderr=errors.append,
                      environment={**os.environ, "LOOPX_USAGE_PING": "0"})
        if stop == "cancel":
            with pytest.raises(RuntimeError, match="synthetic owner cancellation"):
                run_host_process([sys.executable, str(adapter)], **kwargs)
        else:
            result = run_host_process([sys.executable, str(adapter)], **kwargs)
            assert result["outcome"] == ("timeout" if stop == "timeout" else "exited"), json.dumps(result) + "".join(errors)
            assert result["output_complete"] is True
            if stop != "timeout":
                assert result["returncode"] == (9 if stop == "hard_parent_exit" else 0), "".join(errors)
            if stop == "completed":
                assert "TOOL_COMPLETED" in observed
        assert ready.exists() and "TOOL_STARTED" in observed, "".join(errors)
        child_present_at_return = process_present(int(ready.read_text()))
        release.write_text("continue", encoding="utf-8")
        time.sleep(.3)
        assert not late.exists(), "an orphan MCP tool wrote after the Turn supervisor returned"
        assert not child_present_at_return, "the MCP server survived the Turn supervisor"
    finally:
        # A failing regression must not leave the demonstrated orphan running.
        if ready.exists():
            try:
                os.kill(int(ready.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
