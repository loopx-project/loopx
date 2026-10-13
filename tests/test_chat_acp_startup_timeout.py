from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from loopx.chat_acp import ACPStdioAdapter
from loopx.chat_endpoints import AgentEndpointRegistry
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore


SLOW_SESSION_ACP = r'''
import json
import os
from pathlib import Path
import sys
import time

delay = float(sys.argv[1])
Path(sys.argv[2]).write_text(str(os.getpid()), encoding="utf-8")
session_method = sys.argv[3]
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        capabilities = (
            {"loadSession": True}
            if session_method == "session/load"
            else {"sessionCapabilities": {"resume": {}}}
            if session_method == "session/resume"
            else {}
        )
        result = {"protocolVersion": 1, "agentCapabilities": capabilities}
    elif method == "initialized":
        continue
    elif method == session_method:
        time.sleep(delay)
        result = {"sessionId": "acp:fixture/slow-session"}
    elif method == "session/close":
        result = {}
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
'''


def _command(
    tmp_path: Path,
    *,
    name: str,
    session_method: str = "session/new",
) -> tuple[tuple[str, ...], Path]:
    agent = tmp_path / "slow-session-acp.py"
    agent.write_text(SLOW_SESSION_ACP, encoding="utf-8")
    pid_path = tmp_path / f"{name}.pid"
    return (
        sys.executable,
        str(agent),
        "0.15",
        str(pid_path),
        session_method,
    ), pid_path


def test_acp_default_and_larger_session_startup_windows_accept_slow_peer(
    tmp_path: Path,
) -> None:
    default_command, _ = _command(tmp_path, name="default")
    default_adapter = ACPStdioAdapter.start(command=default_command, work_dir=tmp_path)
    assert default_adapter.upstream_thread_id == "acp:fixture/slow-session"
    default_adapter.close_session()

    longer_command, _ = _command(tmp_path, name="longer")
    longer_adapter = ACPStdioAdapter.start(
        command=longer_command,
        work_dir=tmp_path,
        startup_timeout_sec=0.5,
    )
    assert longer_adapter.upstream_thread_id == "acp:fixture/slow-session"
    longer_adapter.close_session()


@pytest.mark.parametrize(
    ("session_method", "resume_thread_id"),
    [
        ("session/new", None),
        ("session/load", "acp:fixture/slow-session"),
        ("session/resume", "acp:fixture/slow-session"),
    ],
)
def test_acp_explicit_short_startup_limit_rejects_and_reaps_peer(
    tmp_path: Path,
    session_method: str,
    resume_thread_id: str | None,
) -> None:
    command, pid_path = _command(
        tmp_path,
        name=session_method.replace("/", "-"),
        session_method=session_method,
    )
    with pytest.raises(TimeoutError, match=session_method):
        ACPStdioAdapter.start(
            command=command,
            work_dir=tmp_path,
            resume_thread_id=resume_thread_id,
            startup_timeout_sec=0.05,
        )

    process_id = int(pid_path.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(process_id, 0)


def test_chat_runtime_uses_default_acp_session_startup_window(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path / "runtime")
    registry = AgentEndpointRegistry(store.root)
    command, _ = _command(tmp_path, name="controller-default")
    registry.upsert(
        {
            "agent_id": "slow-default-acp",
            "display_name": "Slow default ACP",
            "command": list(command),
            "location": "local",
        }
    )
    controller = ChatRuntimeController(
        store=store,
        codex_bin="missing-codex-for-fixture",
        endpoint_registry=registry,
    )

    session, was_resumed = controller.open_session(
        goal_id="fixture-default-timeout",
        agent_id="slow-default-acp",
        work_dir=tmp_path,
        objective="Exercise the default ACP session startup window.",
        mode="new",
    )
    assert was_resumed is False
    controller.close_session(str(session["session_id"]))


def test_chat_runtime_preserves_explicit_acp_startup_limit(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path / "runtime")
    registry = AgentEndpointRegistry(store.root)
    command, _ = _command(tmp_path, name="controller-short")
    registry.upsert(
        {
            "agent_id": "slow-short-acp",
            "display_name": "Slow short ACP",
            "command": list(command),
            "location": "local",
        }
    )
    controller = ChatRuntimeController(
        store=store,
        codex_bin="missing-codex-for-fixture",
        endpoint_registry=registry,
        startup_timeout_sec=0.05,
    )

    with pytest.raises(TimeoutError, match="session/new"):
        controller.open_session(
            goal_id="fixture-short-timeout",
            agent_id="slow-short-acp",
            work_dir=tmp_path,
            objective="Exercise an explicit ACP session startup limit.",
            mode="new",
        )
    assert not store.list_sessions(agent_id="slow-short-acp")
