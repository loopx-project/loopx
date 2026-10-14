from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any

import pytest

from loopx.chat_acp import ACP_INITIALIZE_TIMEOUT_DEFAULT_SEC, ACPStdioAdapter
from loopx.chat_endpoints import AgentEndpointRegistry
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore


SLOW_SESSION_ACP = r"""
import json
import sys
import time

delay = float(sys.argv[1])
session_method = sys.argv[2]
delay_method = sys.argv[3]
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        if delay_method == method:
            time.sleep(delay)
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
        if delay_method == method:
            time.sleep(delay)
        result = {"sessionId": "acp:fixture/slow-session"}
    elif method == "session/close":
        result = {}
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
"""


def _command(
    tmp_path: Path,
    *,
    name: str,
    session_method: str = "session/new",
    delay_method: str | None = None,
) -> tuple[str, ...]:
    agent = tmp_path / f"{name}-acp.py"
    agent.write_text(SLOW_SESSION_ACP, encoding="utf-8")
    return (
        sys.executable,
        str(agent),
        "0.15",
        session_method,
        delay_method or session_method,
    )


def _observe_startup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    complete_initialize: bool,
) -> tuple[list[tuple[str, float]], list[ACPStdioAdapter]]:
    requests: list[tuple[str, float]] = []
    adapters: list[ACPStdioAdapter] = []
    original_request = ACPStdioAdapter._request

    def observe(
        adapter: ACPStdioAdapter,
        method: str,
        params: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        requests.append((method, kwargs["timeout_sec"]))
        if not adapters:
            adapters.append(adapter)
        if complete_initialize and method == "initialize":
            # Isolate the session deadline from interpreter/coverage startup.
            # Record start's original limit; initialize still uses real RPC.
            kwargs["timeout_sec"] = ACP_INITIALIZE_TIMEOUT_DEFAULT_SEC
        return original_request(adapter, method, params, **kwargs)

    monkeypatch.setattr(ACPStdioAdapter, "_request", observe)
    return requests, adapters


def _assert_reaped(adapters: list[ACPStdioAdapter]) -> None:
    assert len(adapters) == 1
    process = adapters[0].process
    assert process.poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(process.pid, 0)


def test_acp_default_and_larger_session_startup_windows_accept_slow_peer(
    tmp_path: Path,
) -> None:
    default_command = _command(tmp_path, name="default")
    default_adapter = ACPStdioAdapter.start(command=default_command, work_dir=tmp_path)
    assert default_adapter.upstream_thread_id == "acp:fixture/slow-session"
    default_adapter.close_session()

    longer_command = _command(tmp_path, name="longer")
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
    monkeypatch: pytest.MonkeyPatch,
    session_method: str,
    resume_thread_id: str | None,
) -> None:
    requests, adapters = _observe_startup(monkeypatch, complete_initialize=True)
    command = _command(
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

    assert requests == [("initialize", 0.05), (session_method, 0.05)]
    _assert_reaped(adapters)


def test_chat_runtime_uses_default_acp_session_startup_window(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path / "runtime")
    registry = AgentEndpointRegistry(store.root)
    command = _command(tmp_path, name="controller-default")
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


def test_chat_runtime_preserves_explicit_acp_initialize_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path / "runtime")
    registry = AgentEndpointRegistry(store.root)
    requests, adapters = _observe_startup(monkeypatch, complete_initialize=False)
    command = _command(tmp_path, name="controller-short", delay_method="initialize")
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

    with pytest.raises(TimeoutError, match="initialize"):
        controller.open_session(
            goal_id="fixture-short-timeout",
            agent_id="slow-short-acp",
            work_dir=tmp_path,
            objective="Exercise an explicit ACP session startup limit.",
            mode="new",
        )
    assert not store.list_sessions(agent_id="slow-short-acp")
    assert requests == [("initialize", 0.05)]
    _assert_reaped(adapters)
