"""Migration history belongs to the native thread, not each subsequent prompt."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from loopx import chat_agent, chat_runtime
from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter


@pytest.mark.parametrize("fault", [None, "later-user", "live", "failed", "background", "unknown", "live-tool",
    "declined", "duplicate", "foreign", "second-user", "no-final", "legacy-prose", "moving-turn", "moving-items"])
def test_completed_work_readback_requires_latest_stable_explicit_settled_inventory(fault):
    from loopx.chat import CHAT_REVIEW_OPEN_TAG, CHAT_REVIEW_CLOSE_TAG
    from loopx.capabilities.native_chat.codex_adapter import completed_work_readback
    import json

    work = {"remaining": "Finish the remaining verified update", "dependency": "A temporary source outage", "effects_settled": True}
    final = "Source unavailable; work remains.\n" + CHAT_REVIEW_OPEN_TAG + json.dumps({
        "message": "", "unfinished_work": work}) + CHAT_REVIEW_CLOSE_TAG
    calls = []
    counts = {"thread/turns/list": 0, "thread/items/list": 0}

    def items(method, params, deadline):
        calls.append(method)
        counts[method] += 1
        if method == "thread/turns/list":
            turns = [{"id": "original-turn", "status": "failed" if fault == "failed" else "completed"}]
            if fault in {"later-user", "live"} or fault == "moving-turn" and counts[method] > 1:
                turns.insert(0, {"id": "later", "status": "inProgress" if fault == "live" else "completed"})
            return iter(turns)
        assert params == {"turnId": "original-turn"}
        effect = {"id": "effect", "type": "fileChange", "status": "completed"}
        if fault == "unknown":
            effect["type"] = "futureEffect"
        if fault in {"live-tool", "declined"}:
            effect["status"] = "inProgress" if fault == "live-tool" else "declined"
        rows = [{"turnId": "original-turn", "item": {"id": "user", "type": "userMessage"}},
                {"turnId": "foreign" if fault == "foreign" else "original-turn", "item": effect},
                {"turnId": "original-turn", "item": {"id": "final", "type": "agentMessage",
                    "phase": "commentary" if fault == "no-final" else "final_answer",
                    "text": "Unfinished, retry later" if fault == "legacy-prose" else final}}]
        if fault == "duplicate":
            rows.append(rows[1])
        if fault == "second-user" or fault == "moving-items" and counts[method] > 1:
            rows.append({"turnId": "original-turn", "item": {"id": "correction", "type": "userMessage"}})
        return iter(rows)

    native = SimpleNamespace(thread_id="native", work_dir=Path("/synthetic/notes"), response_timeout_sec=.1, _control_items=items,
        _background_terminals=lambda deadline: [{"processId": "live"}] if fault == "background" else [])
    proof = completed_work_readback(native, "original-turn")
    if fault is None:
        assert proof == {"thread_id": "native", "turn_id": "original-turn", "completed_work": True,
                         "all_effects_settled": True, "unfinished_work": work}
        assert counts == {"thread/turns/list": 2, "thread/items/list": 2}
    else:
        assert proof is None
    assert set(calls) <= {"thread/turns/list", "thread/items/list"}


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
