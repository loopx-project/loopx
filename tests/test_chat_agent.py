from __future__ import annotations

import io
import json
import queue
import threading
import time
from types import SimpleNamespace
from pathlib import Path

import loopx.chat_agent as chat_agent
import loopx.chat_providers as chat_providers
import loopx.chat_runtime as chat_runtime
import pytest


def test_silent_event_reader_releases_dispatch_fence_for_control_receipt(tmp_path, monkeypatch):
    session = chat_agent.CodexChatAgentSession(process=SimpleNamespace(poll=lambda: None),
        messages=queue.Queue(), thread_id="synthetic-thread", work_dir=tmp_path)
    arrived, errors = [], []

    def events():
        try:
            arrived.append(session._next_event(deadline=time.monotonic() + 3))
        except Exception as exc:
            errors.append(exc)

    # The response arrives without an agent event, just like a silent native
    # Goal stop/read. It must reach its waiter before the event stream ends.
    monkeypatch.setattr(session, "_write", lambda packet: session.messages.put(
        {"id": packet["id"], "result": {"goal": {"status": "active"}}}))
    reader = threading.Thread(target=events)
    reader.start()
    try:
        time.sleep(.05)
        started = time.monotonic()
        assert session._request("thread/goal/get", {"threadId": session.thread_id})["goal"]["status"] == "active"
        assert time.monotonic() - started < 1
    finally:
        session.messages.put({"method": "turn/completed", "params": {"turnId": "original"}})
        reader.join(timeout=4)
    assert not errors and arrived == [{"method": "turn/completed", "params": {"turnId": "original"}}]


class _FakeAppServerProcess:
    def __init__(self, *, config_response=None, model_provider=None, thread_id="thread-loopx-chat", thread_response=None) -> None:
        responses = [
            {"id": 1, "result": {"serverInfo": {"name": "fake-codex"}}},
            {"id": 2, "result": thread_response or {"thread": {"id": thread_id}}},
        ]
        if config_response is not None:
            responses.insert(1, {"id": 3, "result": config_response})
        if model_provider is not None:
            responses[-1]["result"]["modelProvider"] = model_provider
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(
            "".join(json.dumps(response) + "\n" for response in responses)
        )
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.returncode = -1


@pytest.mark.parametrize("grant", ["workspace_read", "workspace_write"])
@pytest.mark.parametrize("resume", [False, True])
def test_project_filesystem_scope_is_verified_on_start_resume_and_pinned_per_turn(monkeypatch, tmp_path, grant, resume):
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    context = ChatProjectContexts([tmp_path], workspace_grant=grant, filesystem_scope="workspace_only").available()[0]
    profile = "loopx_workspace_only_" + ("write" if grant == "workspace_write" else "read")
    process = _FakeAppServerProcess(config_response={"config": {"mcp_servers": {"managed_fixture": {"command": "private-command"}}}},
        thread_response={"thread": {"id": "thread-loopx-chat"},
        "activePermissionProfile": {"id": profile}, "runtimeWorkspaceRoots": [str(tmp_path)]})
    real_which, real_popen = chat_agent.shutil.which, chat_agent.subprocess.Popen
    binary = tmp_path / "native-codex"
    monkeypatch.setattr(chat_agent.shutil, "which", lambda name: str(binary) if name == "codex" else real_which(name))
    launched = []
    monkeypatch.setenv("PRIVATE_FIXTURE_TOKEN", "synthetic-account-secret")
    def popen(command, *args, **kwargs):
        if command[0] != str(binary):
            return real_popen(command, *args, **kwargs)
        launched.append(kwargs["env"])
        return process
    monkeypatch.setattr(chat_agent.subprocess, "Popen", popen)
    session = chat_agent.CodexChatAgentSession.start(codex_bin="codex", work_dir=tmp_path,
        goal_id=None, objective="project", project_context=context, codex_home=tmp_path / "account-codex",
        resume_thread_id="thread-loopx-chat" if resume else None,
        model="synthetic-model", reasoning_effort="high",
        host_config={"skills": {"include_instructions": True}, "project_doc_max_bytes": 32768,
                     "mcp_servers": {"caller_fixture": {"enabled": True}},
                     "shell_environment_policy": {"inherit": "all", "include_only": ["*"],
                                                  "set": {"PRIVATE_FIXTURE": "synthetic"}}})
    try:
        requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        method = "thread/resume" if resume else "thread/start"
        params = next(r["params"] for r in requests if r.get("method") == method)
        assert params["permissions"] == session.permissions_profile == profile
        assert "sandbox" not in params and params["approvalPolicy"] == "never"
        assert params["config"]["permissions"][profile]["network"]["enabled"] is False
        assert params["config"]["default_permissions"] == profile
        assert params["config"]["skills"]["include_instructions"] is False
        assert params["config"]["project_doc_max_bytes"] == 0
        assert params["config"]["mcp_servers"] == {
            "managed_fixture": {"enabled": False}, "caller_fixture": {"enabled": False}}
        env = launched[0]
        assert "PRIVATE_FIXTURE_TOKEN" not in env
        assert Path(env["CODEX_HOME"]).parent == Path(env["HOME"])
        assert Path(env["HOME"]) != Path.home()
        assert Path(env["CODEX_HOME"]) != tmp_path / "account-codex"
        environment = params["config"]["shell_environment_policy"]
        assert environment["inherit"] == "none" and environment["include_only"] == ["PATH"]
        assert environment["experimental_use_profile"] is False
        assert "PRIVATE_FIXTURE" not in environment.get("set", {})
        sent = []
        monkeypatch.setattr(session, "_request", lambda method, params, **kw:
            sent.append((method, params)) or {"turn": {"id": "owned-turn"}})
        monkeypatch.setattr(session, "_next_event", lambda **kw:
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
        session.send("Continue.")
        assert sent[0][1]["permissions"] == profile and "sandboxPolicy" not in sent[0][1]
    finally:
        session.close()


@pytest.mark.parametrize("response", [
    {}, {"activePermissionProfile": {"id": "other"}},
    {"activePermissionProfile": {"id": "loopx_workspace_only_write"}},
    {"activePermissionProfile": {"id": "loopx_workspace_only_write"}, "runtimeWorkspaceRoots": ["/other"]},
])
def test_project_filesystem_scope_rejects_missing_or_changed_native_readback_without_fallback(monkeypatch, tmp_path, response):
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    process = _FakeAppServerProcess(config_response={"config": {}},
        thread_response={"thread": {"id": "thread-loopx-chat"}, **response})
    real_which, real_popen = chat_agent.shutil.which, chat_agent.subprocess.Popen
    binary = tmp_path / "native-codex"
    monkeypatch.setattr(chat_agent.shutil, "which", lambda name: str(binary) if name == "codex" else real_which(name))
    monkeypatch.setattr(chat_agent.subprocess, "Popen", lambda command, *a, **k:
        process if command[0] == str(binary) else real_popen(command, *a, **k))
    with pytest.raises(chat_agent.CodexChatAgentError, match="did not apply"):
        chat_agent.CodexChatAgentSession.start(codex_bin="codex", work_dir=tmp_path, goal_id=None,
            objective="project", codex_home=tmp_path / "account-codex",
            project_context=ChatProjectContexts([tmp_path], filesystem_scope="workspace_only").available()[0],
            resume_thread_id="thread-loopx-chat", model="synthetic-model", reasoning_effort="high")
    requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert sum(r.get("method") == "thread/resume" for r in requests) == 1
    assert not any(r.get("method") in {"thread/start", "turn/start"} for r in requests)
    assert process.returncode == 0


class _FakeClaudeProcess:
    def __init__(self, stdout: str) -> None:
        self.stdout = io.StringIO(stdout)

    def wait(self) -> int:
        return 0


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "not-json\n[]\n",
        json.dumps({"type": "result", "result": ""}) + "\n",
    ],
)
def test_claude_code_rejects_successful_process_without_a_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stdout: str,
) -> None:
    monkeypatch.setattr(
        chat_providers.subprocess,
        "Popen",
        lambda *args, **kwargs: _FakeClaudeProcess(stdout),
    )
    adapter = chat_providers.ClaudeCodeAdapter(
        claude_bin="claude",
        work_dir=tmp_path,
        session_id="session-fixture",
    )
    events: list[tuple[str, dict[str, object]]] = []

    with pytest.raises(chat_agent.CodexChatAgentError) as caught:
        adapter.start_turn(
            "Reply briefly.",
            lambda kind, payload: events.append((kind, payload)),
        )

    assert caught.value.error_code == "provider_empty_response"
    assert not any(kind == "answer.final" for kind, _ in events)
    assert adapter.resumed is False


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "result", "result": "Completed."},
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Completed."},
            },
        },
    ],
)
def test_claude_code_accepts_a_nonempty_response_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    stdout = json.dumps({**payload, "session_id": "upstream-session"})
    monkeypatch.setattr(
        chat_providers.subprocess,
        "Popen",
        lambda *args, **kwargs: _FakeClaudeProcess(stdout + "\n"),
    )
    adapter = chat_providers.ClaudeCodeAdapter(
        claude_bin="claude",
        work_dir=tmp_path,
        session_id="session-fixture",
    )
    events: list[tuple[str, dict[str, object]]] = []

    response = adapter.start_turn(
        "Reply briefly.",
        lambda kind, payload: events.append((kind, payload)),
    )

    assert response["message"] == "Completed."
    assert sum(kind == "answer.final" for kind, _ in events) == 1
    assert adapter.session_id == "upstream-session"
    assert adapter.resumed is True


def test_codex_chat_app_server_stdio_uses_utf8(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launch_options: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object) -> _FakeAppServerProcess:
        assert command == ["codex", "app-server", "--listen", "stdio://"]
        launch_options.update(kwargs)
        return _FakeAppServerProcess()

    monkeypatch.setattr(chat_agent.shutil, "which", lambda _binary: "codex")
    monkeypatch.setattr(chat_agent.subprocess, "Popen", fake_popen)

    session = chat_agent.CodexChatAgentSession.start(
        codex_bin="codex",
        work_dir=tmp_path,
        goal_id="loopx-chat-smoke",
        objective="Keep multilingual chat transport stable.",
    )
    try:
        assert launch_options["encoding"] == "utf-8"
    finally:
        session.close()


@pytest.mark.parametrize(
    ("item", "expected_activity"),
    [
        ({"type": "userMessage"}, "Agent 已收到消息"),
        ({"type": "agentMessage"}, "Agent 正在生成回答"),
        ({"type": "commandExecution"}, "Agent 正在执行命令"),
        ({"type": "reasoning"}, "Agent 正在思考"),
        ({"type": "contextCompaction"}, "Agent 正在压缩会话上下文"),
        ({"type": "mcpToolCall"}, "Agent 正在调用工具"),
        ({"type": "futureItem", "text": "private-fixture-content"}, "Agent 正在处理"),
        ({}, "Agent 正在处理"),
    ],
)
def test_turn_activity_does_not_invent_goal_reads_or_successful_checks(
    monkeypatch,
    tmp_path,
    item,
    expected_activity,
):
    session = chat_agent.CodexChatAgentSession(
        process=_FakeAppServerProcess(),
        messages=queue.Queue(),
        thread_id="thread-fixture",
        work_dir=tmp_path,
    )
    upstream = iter(
        [
            {"method": "turn/started", "params": {"turn": {"id": "turn-fixture"}}},
            {"method": "item/started", "params": {"item": item}},
            # Completion can mean a failed command or receipt of a user message;
            # neither is evidence that a Goal check passed.
            {
                "method": "item/completed",
                "params": {"item": {**item, "status": "failed"}},
            },
            {"method": "item/agentMessage/delta", "params": {"delta": "Ready."}},
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
        ]
    )
    monkeypatch.setattr(
        session, "_request", lambda *a, **kw: {"turn": {"id": "turn-fixture"}}
    )
    monkeypatch.setattr(session, "_next_event", lambda **kw: next(upstream))
    events = []
    session.send(
        "Reply briefly.", on_event=lambda kind, payload: events.append((kind, payload))
    )
    phases = [p for kind, p in events if kind == "agent.phase"]
    assert phases[1]["label"] == expected_activity
    assert phases[0]["label"] == "Agent 已开始处理"
    assert phases[2]["label"] == (
        "Agent 会话上下文压缩已结束" if item.get("type") == "contextCompaction"
        else "Agent 返回了处理状态"
    )
    assert not any("检查" in p["label"] or "Goal" in p["label"] for p in phases)
    assert "private-fixture-content" not in json.dumps(phases)
    assert any(kind == "answer.delta" and p["text"] == "Ready." for kind, p in events)


def test_turn_activity_steps_merge_started_and_completed_items(monkeypatch, tmp_path):
    session = chat_agent.CodexChatAgentSession(
        process=_FakeAppServerProcess(),
        messages=queue.Queue(),
        thread_id="thread-fixture",
        work_dir=tmp_path,
    )
    command = {"type": "commandExecution", "id": "exec-1", "status": "inProgress",
               "command": f"/bin/zsh -lc 'cat {tmp_path}/notes.md'",
               "commandActions": [{"type": "read", "command": "cat notes.md", "name": "notes.md", "path": str(tmp_path / "notes.md")}]}
    upstream = iter([
        {"method": "turn/started", "params": {"turn": {"id": "turn-fixture"}}},
        {"method": "item/started", "params": {"item": {"type": "reasoning", "id": "rs_1", "summary": [], "content": []}}},
        {"method": "item/reasoning/textDelta", "params": {"itemId": "rs_1", "delta": "Read the notes first.", "contentIndex": 0}},
        {"method": "item/completed", "params": {"item": {"type": "reasoning", "id": "rs_1", "summary": [], "content": ["Read the notes first."]}}},
        {"method": "item/started", "params": {"item": command}},
        {"method": "item/completed", "params": {"item": {**command, "status": "completed", "exitCode": 0, "durationMs": 5,
                                                         "aggregatedOutput": "private-output-fixture"}}},
        {"method": "item/agentMessage/delta", "params": {"delta": "Ready."}},
        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ])
    monkeypatch.setattr(session, "_request", lambda *a, **kw: {"turn": {"id": "turn-fixture"}})
    monkeypatch.setattr(session, "_next_event", lambda **kw: next(upstream))
    events = []
    session.send("Reply briefly.", on_event=lambda kind, payload: events.append((kind, payload)))
    steps = [p["step"] for kind, p in events if kind == "agent.phase" and "step" in p]
    assert [(s["id"], s["state"]) for s in steps] == [
        ("rs_1", "running"), ("rs_1", "completed"), ("exec-1", "running"), ("exec-1", "completed")]
    assert steps[1]["detail"] == "Read the notes first."
    assert (steps[3]["verb"], steps[3]["title"], steps[3]["exit_code"]) == ("read", "notes.md", 0)
    assert steps[3]["detail"] == "cat notes.md"
    serialized = json.dumps(events)
    assert str(tmp_path) not in serialized and "private-output-fixture" not in serialized
    labels = [p["label"] for kind, p in events if kind == "agent.phase"]
    assert labels[:3] == ["Agent 已开始处理", "Agent 正在思考", "Agent 返回了处理状态"], "Legacy labels stay for older clients"


def test_codex_chat_pins_explicit_home_in_child_environment(monkeypatch, tmp_path):
    options = {}

    def popen(command, **kwargs):
        options.update(kwargs)
        return _FakeAppServerProcess()

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ambient"))
    monkeypatch.setattr(chat_agent.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(chat_agent.subprocess, "Popen", popen)
    session = chat_agent.CodexChatAgentSession.start(
        codex_bin="codex",
        work_dir=tmp_path,
        goal_id="fixture",
        objective="fixture",
        codex_home=tmp_path / "bound",
    )
    try:
        assert options["env"]["CODEX_HOME"] == str((tmp_path / "bound").resolve())
        assert chat_agent.os.environ["CODEX_HOME"] == str(tmp_path / "ambient")
    finally:
        session.close()


def test_codex_chat_provider_override_resumes_original_thread_and_preserves_policy(monkeypatch, tmp_path):
    process = _FakeAppServerProcess(model_provider="fixture-http", thread_id="original-thread")
    monkeypatch.setenv("LOOPX_CHAT_CODEX_MODEL_PROVIDER", "fixture-http")
    monkeypatch.setattr(chat_agent.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(chat_agent.subprocess, "Popen", lambda *a, **k: process)
    session = chat_agent.CodexChatAgentSession.start(
        codex_bin="codex", work_dir=tmp_path, goal_id="fixture", objective="fixture",
        resume_thread_id="original-thread", model="fixture-model", reasoning_effort="high",
        codex_home=tmp_path / "original-home",
    )
    try:
        packets = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        resume = next(p for p in packets if p.get("method") == "thread/resume")
        assert resume["params"]["threadId"] == "original-thread"
        assert resume["params"]["modelProvider"] == "fixture-http"
        assert resume["params"]["model"] == "fixture-model"
        assert resume["params"]["config"]["model_reasoning_effort"] == "high"
        assert resume["params"]["sandbox"] == "read-only"
        assert resume["params"]["approvalPolicy"] == "never"
        assert not any(p.get("method") in {"thread/start", "turn/start"} for p in packets)
    finally:
        session.close()


@pytest.mark.parametrize("actual", [None, "different-provider"])
def test_codex_chat_provider_override_refuses_unconfirmed_native_selection(monkeypatch, tmp_path, actual):
    process = _FakeAppServerProcess(model_provider=actual)
    monkeypatch.setenv("LOOPX_CHAT_CODEX_MODEL_PROVIDER", "fixture-http")
    monkeypatch.setattr(chat_agent.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(chat_agent.subprocess, "Popen", lambda *a, **k: process)
    with pytest.raises(chat_agent.CodexChatAgentError, match="requested conversation provider"):
        chat_agent.CodexChatAgentSession.start(
            codex_bin="codex", work_dir=tmp_path, goal_id="fixture", objective="fixture",
            resume_thread_id="original-thread", model="fixture-model", reasoning_effort="high",
        )
    assert process.returncode == 0
    packets = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert not any(p.get("method") in {"thread/start", "turn/start"} for p in packets)


def test_chat_provider_override_does_not_select_managed_goal_executor_provider(monkeypatch, tmp_path):
    process = _FakeAppServerProcess()
    monkeypatch.setenv("LOOPX_CHAT_CODEX_MODEL_PROVIDER", "fixture-http")
    monkeypatch.setattr(chat_agent.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(chat_agent.subprocess, "Popen", lambda *a, **k: process)
    session = chat_agent.CodexChatAgentSession.start(
        codex_bin="codex", work_dir=tmp_path, goal_id="fixture", objective="fixture",
        execution_mode=True,
    )
    try:
        packets = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        start = next(p for p in packets if p.get("method") == "thread/start")
        assert "modelProvider" not in start["params"]
    finally:
        session.close()


@pytest.mark.parametrize("commentary", ["I will read the context.", '{"answer":"not-final"}'])
@pytest.mark.parametrize("final_phase", [None, "final_answer"])
def test_structured_turn_uses_completed_answer_not_commentary_or_partial_deltas(
    monkeypatch, tmp_path, commentary, final_phase,
):
    session = chat_agent.CodexChatAgentSession(
        process=_FakeAppServerProcess(), messages=queue.Queue(), thread_id="thread-fixture",
        work_dir=tmp_path, execution_mode=True,
    )
    params = {"threadId": "thread-fixture", "turnId": "turn-fixture"}
    final = {"type": "agentMessage", "text": '{"answer":"actual"}'}
    if final_phase is not None:
        final["phase"] = final_phase
    upstream = iter([
        {"method": "item/agentMessage/delta", "params": {**params, "delta": commentary}},
        {"method": "item/completed", "params": {**params, "item": {
            "type": "agentMessage", "phase": "commentary", "text": commentary}}},
        {"method": "item/agentMessage/delta", "params": {**params, "delta": '{"answer":'}},
        {"method": "item/completed", "params": {**params, "item": final}},
        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ])
    monkeypatch.setattr(session, "_request", lambda *a, **kw: {"turn": {"id": "turn-fixture"}})
    monkeypatch.setattr(session, "_next_event", lambda **kw: next(upstream))
    assert session.send("Return the structured result.", output_schema={"type": "object"}) == {"answer": "actual"}


@pytest.mark.parametrize("phase", ["commentary", "futurePhase", []])
def test_structured_turn_cannot_promote_nonfinal_json_to_a_final_result(monkeypatch, tmp_path, phase):
    session = chat_agent.CodexChatAgentSession(
        process=_FakeAppServerProcess(), messages=queue.Queue(), thread_id="thread-fixture",
        work_dir=tmp_path, execution_mode=True,
    )
    upstream = iter([
        {"method": "item/agentMessage/delta", "params": {"delta": '{"answer":"not-final"}'}},
        {"method": "item/completed", "params": {"item": {
            "type": "agentMessage", "phase": phase, "text": '{"answer":"not-final"}'}}},
        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ])
    monkeypatch.setattr(session, "_request", lambda *a, **kw: {"turn": {"id": "turn-fixture"}})
    monkeypatch.setattr(session, "_next_event", lambda **kw: next(upstream))
    with pytest.raises(chat_agent.CodexChatAgentError, match="structured output"):
        session.send("Return the structured result.", output_schema={"type": "object"})


def test_trusted_manager_profile_reaches_app_server_and_turn_prompt(
    monkeypatch,
    tmp_path,
):
    process = _FakeAppServerProcess()
    monkeypatch.setattr(chat_agent.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(chat_agent.subprocess, "Popen", lambda *a, **k: process)
    session = chat_agent.CodexChatAgentSession.start(
        codex_bin="codex",
        work_dir=tmp_path,
        goal_id="loopx-manager",
        objective="global",
        runtime_profile="trusted_owner",
    )
    try:
        requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        start = next(row for row in requests if row["method"] == "thread/start")
        assert start["params"]["sandbox"] == "danger-full-access"

        turns = []

        def request(method, params, **kwargs):
            turns.append((method, params))
            return {"turn": {"id": "fixture-turn"}}

        monkeypatch.setattr(session, "_request", request)
        monkeypatch.setattr(
            session,
            "_next_event",
            lambda **kwargs: {
                "method": "turn/completed",
                "params": {"turn": {"status": "completed"}},
            },
        )
        session.send("Inspect and repair the project.")
        prompt = turns[0][1]["input"][0]["text"]
        assert "effective runtime profile is trusted_owner" in prompt
        assert "Do not edit files" not in prompt
    finally:
        session.close()


@pytest.mark.parametrize(
    ("runtime_profile", "sandbox"),
    [
        ("restricted", "workspace-write"),
        ("restricted", "danger-full-access"),
        ("trusted_owner", "read-only"),
        ("trusted_owner", "workspace-write"),
    ],
)
def test_manager_profile_rejects_mismatched_sandbox_before_app_server_start(
    monkeypatch,
    tmp_path,
    runtime_profile,
    sandbox,
):
    started = False

    def popen(*args, **kwargs):
        nonlocal started
        started = True
        return _FakeAppServerProcess()

    monkeypatch.setattr(chat_agent.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(chat_agent.subprocess, "Popen", popen)

    with pytest.raises(ValueError, match="does not match"):
        chat_agent.CodexChatAgentSession.start(
            codex_bin="codex",
            work_dir=tmp_path,
            goal_id="loopx-manager",
            objective="global",
            runtime_profile=runtime_profile,
            sandbox=sandbox,
        )

    assert started is False


def test_app_server_adapter_cannot_bypass_manager_profile_sandbox_binding(
    monkeypatch,
    tmp_path,
):
    started = False

    def popen(*args, **kwargs):
        nonlocal started
        started = True
        return _FakeAppServerProcess()

    monkeypatch.setattr(chat_agent.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(chat_agent.subprocess, "Popen", popen)

    with pytest.raises(ValueError, match="does not match"):
        chat_runtime.CodexAppServerAdapter.start(
            codex_bin="codex",
            work_dir=tmp_path,
            goal_id="loopx-manager",
            objective="global",
            runtime_profile="restricted",
            sandbox="danger-full-access",
        )

    assert started is False


@pytest.mark.parametrize("resume_thread_id", [None, "thread-loopx-chat"])
def test_explicit_manager_model_and_effort_reach_start_resume_and_turn(
    monkeypatch, tmp_path, resume_thread_id
):
    process = _FakeAppServerProcess()
    monkeypatch.setattr(chat_agent.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(chat_agent.subprocess, "Popen", lambda *a, **k: process)
    session = chat_agent.CodexChatAgentSession.start(
        codex_bin="codex",
        work_dir=tmp_path,
        goal_id="loopx-manager",
        objective="global",
        model="gpt-6-astra",
        reasoning_effort="medium",
        resume_thread_id=resume_thread_id,
    )
    try:
        requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        start = next(
            r for r in requests if r["method"] in {"thread/start", "thread/resume"}
        )
        assert start["params"]["model"] == "gpt-6-astra"
        assert start["params"]["config"]["model_reasoning_effort"] == "medium"
        turns = []

        def request(method, params, **kwargs):
            turns.append((method, params))
            return {"turn": {"id": "fixture-turn"}}

        monkeypatch.setattr(session, "_request", request)
        monkeypatch.setattr(
            session,
            "_next_event",
            lambda **kwargs: {
                "method": "turn/completed",
                "params": {"turn": {"status": "completed"}},
            },
        )
        session.send("Which Goals?")
        assert turns[0][0] == "turn/start"
        assert turns[0][1]["model"] == "gpt-6-astra"
        assert turns[0][1]["effort"] == "medium"
    finally:
        session.close()


@pytest.mark.parametrize("grant,sandbox", [
    ("workspace_write", "workspace-write"), ("workspace_read", "read-only"),
])
@pytest.mark.parametrize("explicit", [{}, {"model": "pinned-model"}, {"reasoning_effort": "low"}])
def test_project_resume_uses_effective_host_settings_without_widening_grant(
    monkeypatch, tmp_path, grant, sandbox, explicit
):
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts

    context = ChatProjectContexts([tmp_path], workspace_grant=grant).available()[0]
    process = _FakeAppServerProcess(config_response={"config": {
        "model": "project-model", "model_reasoning_effort": "high",
        "sandbox_mode": "danger-full-access", "approval_policy": "on-request",
    }})
    real_which, real_popen = chat_agent.shutil.which, chat_agent.subprocess.Popen
    monkeypatch.setattr(chat_agent.shutil, "which", lambda binary: "codex" if binary == "codex" else real_which(binary))
    monkeypatch.setattr(chat_agent.subprocess, "Popen", lambda command, *a, **k:
        process if command[0] == "codex" else real_popen(command, *a, **k))
    session = chat_agent.CodexChatAgentSession.start(
        codex_bin="codex", work_dir=tmp_path, goal_id=None, objective="project",
        project_context=context, resume_thread_id="thread-loopx-chat", **explicit,
    )
    try:
        requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        assert next(r for r in requests if r.get("method") == "config/read")["params"] == {
            "cwd": str(tmp_path), "includeLayers": False,
        }
        resume = next(r for r in requests if r.get("method") == "thread/resume")["params"]
        assert not any(r.get("method") == "thread/start" for r in requests)
        assert resume["threadId"] == session.thread_id == "thread-loopx-chat"
        assert resume["model"] == explicit.get("model", "project-model")
        assert resume["config"] == {"model_reasoning_effort": explicit.get("reasoning_effort", "high")}
        assert resume["sandbox"] == sandbox and resume["approvalPolicy"] == "never"
        sent = []
        monkeypatch.setattr(session, "_request", lambda method, params, **kw: (
            sent.append((method, params)) or {"turn": {"id": "same-thread-turn"}}))
        monkeypatch.setattr(session, "_next_event", lambda **kw: {
            "method": "turn/completed", "params": {"turn": {"status": "completed"}},
        })
        session.send("Continue the project request.")
        assert sent[0][1]["model"] == resume["model"]
        assert sent[0][1]["effort"] == resume["config"]["model_reasoning_effort"]
    finally:
        session.close()


@pytest.mark.parametrize("config", [[], {"model": 42}, {"model_reasoning_effort": {"invalid": "high"}}])
def test_invalid_effective_project_settings_close_without_replacement_thread(monkeypatch, tmp_path, config):
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts

    process = _FakeAppServerProcess(config_response={"config": config})
    real_which, real_popen = chat_agent.shutil.which, chat_agent.subprocess.Popen
    monkeypatch.setattr(chat_agent.shutil, "which", lambda binary: "codex" if binary == "codex" else real_which(binary))
    monkeypatch.setattr(chat_agent.subprocess, "Popen", lambda command, *a, **k:
        process if command[0] == "codex" else real_popen(command, *a, **k))
    with pytest.raises(chat_agent.CodexChatAgentError, match="invalid project"):
        chat_agent.CodexChatAgentSession.start(
            codex_bin="codex", work_dir=tmp_path, goal_id=None, objective="project",
            project_context=ChatProjectContexts([tmp_path]).available()[0],
            resume_thread_id="thread-loopx-chat",
        )
    assert process.poll() is not None
    requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert not any(r.get("method") in {"thread/start", "thread/resume"} for r in requests)


def test_project_resume_config_read_failure_does_not_resume_or_replace(monkeypatch, tmp_path):
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts

    requests = []
    original = chat_agent.CodexChatAgentSession._request

    def request(self, method, params, **kwargs):
        requests.append(method)
        if method == "config/read":
            raise self._runtime_error("Codex project configuration is unavailable.")
        return original(self, method, params, **kwargs)

    process = _FakeAppServerProcess()
    real_which, real_popen = chat_agent.shutil.which, chat_agent.subprocess.Popen
    monkeypatch.setattr(chat_agent.shutil, "which", lambda binary: "codex" if binary == "codex" else real_which(binary))
    monkeypatch.setattr(chat_agent.subprocess, "Popen", lambda command, *a, **k:
        process if command[0] == "codex" else real_popen(command, *a, **k))
    monkeypatch.setattr(chat_agent.CodexChatAgentSession, "_request", request)
    with pytest.raises(chat_agent.CodexChatAgentError, match="configuration is unavailable"):
        chat_agent.CodexChatAgentSession.start(
            codex_bin="codex", work_dir=tmp_path, goal_id=None, objective="project",
            project_context=ChatProjectContexts([tmp_path]).available()[0],
            resume_thread_id="thread-loopx-chat",
        )
    assert requests == ["initialize", "config/read"] and process.poll() is not None


@pytest.mark.parametrize("terminal_method", ["error", "turn/completed"])
@pytest.mark.parametrize(
    "info,expected",
    [
        ("cyberPolicy", "cyber_policy"),
        ("misalignmentPolicyViolation", "misalignment_policy_violation"),
        ("usageLimitExceeded", "usage_limit_exceeded"),
        ("rateLimitExceeded", "rate_limit_exceeded"),
        ("serverOverloaded", "server_overloaded"),
        ("contextWindowExceeded", "context_window_exceeded"),
        ("unauthorized", "unauthorized"),
        ("futureVariant", "host_gate"),
        ({"unknown": "cyberPolicy"}, "host_gate"),
        (None, "host_gate"),
    ],
)
def test_typed_terminal_errors_preserve_category_without_promoting_partial_answer(
    monkeypatch,
    tmp_path,
    terminal_method,
    info,
    expected,
):
    session = chat_agent.CodexChatAgentSession(
        process=_FakeAppServerProcess(),
        messages=queue.Queue(),
        thread_id="thread-fixture",
        work_dir=tmp_path,
    )
    error = {
        "codexErrorInfo": info,
        "message": "private-fixture cyberPolicy",
        "additionalDetails": "private-fixture",
    }
    params = {"threadId": "thread-fixture", "turnId": "turn-fixture"}
    if terminal_method == "error":
        params.update(error=error, willRetry=False)
    else:
        params["turn"] = {"id": "turn-fixture", "status": "failed", "error": error}
    upstream = iter(
        [
            {
                "method": "item/agentMessage/delta",
                "params": {"delta": "Partial answer."},
            },
            {"method": terminal_method, "params": params},
        ]
    )
    monkeypatch.setattr(
        session, "_request", lambda *a, **kw: {"turn": {"id": "turn-fixture"}}
    )
    monkeypatch.setattr(session, "_next_event", lambda **kw: next(upstream))
    events = []
    with pytest.raises(chat_agent.CodexChatAgentError) as caught:
        session.send("Report progress.", on_event=lambda k, p: events.append((k, p)))
    assert caught.value.error_code == expected
    assert "private-fixture" not in str(caught.value) + json.dumps(caught.value.gate)
    assert not any(k == "answer.final" for k, _ in events)
    if expected in {"cyber_policy", "misalignment_policy_violation"}:
        assert caught.value.gate["kind"] == "policy_gate"
        assert "不会自动重放" in caught.value.gate["next_action"]


def test_structured_invalid_upstream_request_is_not_a_generic_host_gate() -> None:
    error = chat_agent._terminal_turn_error(
        {
            "codexErrorInfo": "other",
            "message": json.dumps(
                {
                    "type": "error",
                    "status": 400,
                    "error": {
                        "type": "invalid_request_error",
                        "message": "private upstream model detail",
                    },
                }
            ),
        },
        "generic fallback",
    )
    assert error.error_code == "upstream_invalid_request"
    assert "private upstream" not in str(error) + json.dumps(error.gate)
    assert "模型" in error.gate["next_action"]


def test_unstructured_upstream_error_stays_generic() -> None:
    error = chat_agent._terminal_turn_error(
        {"codexErrorInfo": "other", "message": "private upstream error"},
        "generic fallback",
    )
    assert error.error_code == "host_gate"
    assert "private upstream" not in str(error) + json.dumps(error.gate)


@pytest.mark.parametrize("retry_info", [
    "rateLimitExceeded", "serverOverloaded",
    {"responseStreamConnectionFailed": {"httpStatusCode": 429}},
    {"responseStreamDisconnected": {"httpStatusCode": 503}},
])
def test_retry_and_unrelated_policy_events_do_not_terminate_current_turn(
    monkeypatch, tmp_path, retry_info
):
    session = chat_agent.CodexChatAgentSession(
        process=_FakeAppServerProcess(),
        messages=queue.Queue(),
        thread_id="thread-fixture",
        work_dir=tmp_path,
    )
    upstream = iter(
        [
            {
                "method": "error",
                "params": {
                    "threadId": "other-thread",
                    "turnId": "turn-fixture",
                    "error": {"codexErrorInfo": "cyberPolicy"},
                    "willRetry": False,
                },
            },
            {
                "method": "error",
                "params": {
                    "threadId": "thread-fixture",
                    "turnId": "other-turn",
                    "error": {"codexErrorInfo": "cyberPolicy"},
                    "willRetry": False,
                },
            },
            {
                "method": "error",
                "params": {
                    "threadId": "thread-fixture",
                    "turnId": "turn-fixture",
                    "error": {
                        "codexErrorInfo": retry_info,
                        "message": "private upstream request",
                        "additionalDetails": "private upstream detail",
                    },
                    "willRetry": True,
                },
            },
            {"method": "item/agentMessage/delta", "params": {"delta": "Recovered."}},
            {
                "method": "turn/completed",
                "params": {"turn": {"id": "turn-fixture", "status": "completed"}},
            },
        ]
    )
    monkeypatch.setattr(
        session, "_request", lambda *a, **kw: {"turn": {"id": "turn-fixture"}}
    )
    monkeypatch.setattr(session, "_next_event", lambda **kw: next(upstream))
    events = []
    result = session.send(
        "Report progress.", on_event=lambda k, p: events.append((k, p))
    )
    assert result["message"] == "Recovered."
    retry_events = [
        p for k, p in events if k == "agent.phase" and p["label"] == "Codex 正在重试"
    ]
    assert retry_events == [{
        "label": "Codex 正在重试", "method": "error",
        "retry": {"codex_error_info": retry_info},
    }]
    assert "private upstream" not in json.dumps(events)
    assert sum(k == "answer.final" for k, p in events) == 1


@pytest.mark.parametrize("variant", [
    "httpConnectionFailed", "responseStreamConnectionFailed",
    "responseStreamDisconnected", "responseTooManyFailedAttempts",
])
@pytest.mark.parametrize("status", [None, 429, 503])
def test_retry_http_diagnostics_keep_only_typed_status(variant, status):
    actual = chat_agent._retry_error_details({
        "codexErrorInfo": {variant: {
            "httpStatusCode": status, "message": "private upstream request",
        }},
        "message": "private upstream request", "additionalDetails": "private detail",
    })
    assert actual == {"retry": {"codex_error_info": {variant: {"httpStatusCode": status}}}}
    assert "private" not in json.dumps(actual)


@pytest.mark.parametrize("status", [True, False, -1, 65536, "503", 503.0, {}, []])
def test_retry_http_diagnostics_do_not_coerce_untrusted_status(status):
    assert chat_agent._retry_error_details({
        "codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": status}},
    }) == {"retry": {"codex_error_info": {"httpConnectionFailed": {}}}}


@pytest.mark.parametrize("info", [
    None, [], "private-future-error", {"private-future-error": {}},
    {"responseStreamDisconnected": None},
    {"responseStreamDisconnected": {}, "httpConnectionFailed": {}},
    {"activeTurnNotSteerable": {"turnKind": []}},
    {"activeTurnNotSteerable": {"turnKind": "private-future-kind"}},
])
def test_retry_unknown_shapes_keep_generic_phase_without_private_details(info):
    assert chat_agent._retry_error_details({
        "codexErrorInfo": info, "message": "private request", "additionalDetails": "private detail",
    }) == {}


@pytest.mark.parametrize("kind", ["review", "compact"])
def test_retry_nonsteerable_details_use_existing_provider_turn_kind(kind):
    assert chat_agent._retry_error_details({
        "codexErrorInfo": {"activeTurnNotSteerable": {"turnKind": kind, "detail": "private"}},
    }) == {"retry": {"codex_error_info": {"activeTurnNotSteerable": {"turnKind": kind}}}}


def test_native_child_callback_is_scoped_to_owned_thread_and_turn(monkeypatch, tmp_path):
    session = chat_agent.CodexChatAgentSession(process=_FakeAppServerProcess(),
        messages=queue.Queue(), thread_id="thread-fixture", work_dir=tmp_path)
    item = {"type": "collabAgentToolCall", "id": "call-1", "tool": "spawnAgent"}
    events = iter([
        {"method": "item/completed", "params": {"threadId": "other", "turnId": "turn-fixture", "item": item}},
        {"method": "item/completed", "params": {"threadId": "thread-fixture", "turnId": "old-turn", "item": item}},
        {"method": "item/completed", "params": {"threadId": "thread-fixture", "turnId": "turn-fixture", "item": item}},
        {"method": "item/agentMessage/delta", "params": {"delta": "Ready."}},
        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ])
    monkeypatch.setattr(session, "_request", lambda *a, **kw: {"turn": {"id": "turn-fixture"}})
    monkeypatch.setattr(session, "_next_event", lambda **kw: next(events))
    observed = []
    session.send("Reply briefly.", on_native_item=observed.append)
    assert observed == [item]
