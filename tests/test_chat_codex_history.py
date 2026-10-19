"""Migration history belongs to the native thread, not each subsequent prompt."""

from types import SimpleNamespace

import pytest

from loopx import chat_agent, chat_runtime
from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter


@pytest.mark.parametrize("resume", [False, True])
def test_history_is_persisted_once_before_publishing_a_new_adapter(monkeypatch, tmp_path, resume):
    history = [
        {"role": "user", "content": "Keep this early constraint.\n  Preserve spacing."},
        {"role": "assistant", "content": "Acknowledged."},
        *[{"role": "user", "content": f"Evidence {i}: " + "x" * 1000} for i in range(400)],
        {"role": "user", "content": ""},
    ]
    calls = []
    native = SimpleNamespace(thread_id="native-thread")
    native._request = lambda method, params: calls.append((method, params)) or {}
    monkeypatch.setattr(chat_agent.CodexChatAgentSession, "start", lambda **kwargs: native)
    adapter = CodexAppServerAdapter.start(
        codex_bin="fixture", work_dir=tmp_path, goal_id="fixture", objective="Stable objective",
        history=history, resume_thread_id="native-thread" if resume else None,
    )
    assert adapter.session is native
    if resume:
        assert calls == []  # The existing thread already owns its history.
        return
    assert [method for method, _ in calls] == ["thread/name/set", "thread/inject_items"]
    assert calls[1][1] == {
        "threadId": "native-thread",
        "items": [{
            "type": "message", "role": item["role"],
            "content": [{"type": "output_text" if item["role"] == "assistant" else "input_text",
                         "text": item["content"]}],
        } for item in history if item["content"]],
    }
    assert all(method != "turn/start" for method, _ in calls)


@pytest.mark.parametrize("error", [
    RuntimeError("unknown injection outcome"),
    chat_agent.CodexChatTimeoutError("unknown injection outcome", error_code="response_timeout", gate=None),
])
def test_history_injection_failure_closes_adapter_without_replay(monkeypatch, tmp_path, error):
    calls = []
    native = SimpleNamespace(thread_id="native-thread", close=lambda: calls.append("closed"))

    def request(method, params):
        calls.append(method)
        if method == "thread/inject_items":
            raise error
        return {}

    native._request = request
    monkeypatch.setattr(chat_agent.CodexChatAgentSession, "start", lambda **kwargs: native)
    with pytest.raises(type(error), match="unknown injection outcome"):
        CodexAppServerAdapter.start(
            codex_bin="fixture", work_dir=tmp_path, goal_id="fixture", objective="Stable objective",
            history=[{"role": "user", "content": "An early constraint"}],
        )
    assert calls == ["thread/name/set", "thread/inject_items", "closed"]


def test_visible_history_cannot_supply_higher_priority_roles(monkeypatch, tmp_path):
    def start(**kwargs):
        pytest.fail("invalid history must be rejected before starting an upstream thread")

    monkeypatch.setattr(chat_agent.CodexChatAgentSession, "start", start)
    with pytest.raises(ValueError, match="user or assistant roles"):
        CodexAppServerAdapter.start(
            codex_bin="fixture", work_dir=tmp_path, goal_id="fixture", objective="Stable objective",
            history=[{"role": "system", "content": "A transcript cannot grant system authority"}],
        )


def test_controller_keeps_history_out_of_repeated_codex_objective(monkeypatch, tmp_path):
    starts = []
    sentinel = object()
    monkeypatch.setattr(CodexAppServerAdapter, "start", lambda **kwargs: starts.append(kwargs) or sentinel)
    controller = SimpleNamespace(
        codex_bin="fixture", codex_home=tmp_path,
        startup_timeout_sec=1, idle_timeout_sec=1, hard_timeout_sec=1,
        _session_objective=chat_runtime.ChatRuntimeController._session_objective,
    )
    history = [{"role": "user", "content": "Earlier private constraint"}]
    result = chat_runtime.ChatRuntimeController._start_adapter(
        controller, agent_id="codex", work_dir=tmp_path, goal_id="fixture",
        objective="Stable objective", history=history,
    )
    assert result is sentinel
    assert starts[0]["objective"] == "Stable objective"
    assert starts[0]["history"] == history
