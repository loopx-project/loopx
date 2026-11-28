"""Direct provider adapters keep healthy defaults while honoring overrides."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from loopx.chat_agent import CodexChatAgentError
from loopx.chat.providers import DirectModelAdapter, direct_model_from_environment
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore


PROVIDERS = (
    ("anthropic", "ANTHROPIC_API_KEY", "LOOPX_ANTHROPIC_MODEL", "claude-sonnet-5-5"),
    ("openai", "OPENAI_API_KEY", "LOOPX_OPENAI_MODEL", "gpt-6.1-sol"),
)


@pytest.mark.parametrize(("provider", "key_name", "model_name", "expected"), PROVIDERS)
def test_direct_model_uses_current_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    key_name: str,
    model_name: str,
    expected: str,
) -> None:
    monkeypatch.setenv(key_name, "fake-key")
    monkeypatch.delenv(model_name, raising=False)

    adapter = direct_model_from_environment(provider=provider, work_dir=tmp_path)

    assert adapter.model == expected


@pytest.mark.parametrize(("provider", "key_name", "model_name", "_expected"), PROVIDERS)
def test_direct_model_honors_environment_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    key_name: str,
    model_name: str,
    _expected: str,
) -> None:
    monkeypatch.setenv(key_name, "fake-key")
    monkeypatch.setenv(model_name, "operator-selected-model")

    adapter = direct_model_from_environment(provider=provider, work_dir=tmp_path)

    assert adapter.model == "operator-selected-model"


@pytest.mark.parametrize(("provider", "key_name", "model_name", "expected"), PROVIDERS)
def test_direct_model_sends_current_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    key_name: str,
    model_name: str,
    expected: str,
) -> None:
    monkeypatch.setenv(key_name, "fake-key")
    monkeypatch.delenv(model_name, raising=False)
    bodies: list[dict[str, Any]] = []

    def fake_post(
        _adapter: DirectModelAdapter,
        _url: str,
        body: dict[str, Any],
        _headers: dict[str, str],
    ) -> dict[str, Any]:
        bodies.append(body)
        if provider == "anthropic":
            return {"content": [{"type": "text", "text": "ok"}]}
        return {"output_text": "ok"}

    monkeypatch.setattr(DirectModelAdapter, "_post", fake_post)
    adapter = direct_model_from_environment(provider=provider, work_dir=tmp_path)

    adapter.start_turn("hello", lambda _kind, _payload: None)

    assert bodies[0]["model"] == expected
    if provider == "anthropic":
        assert bodies[0]["max_tokens"] == 8192


def _anthropic_adapter(tmp_path: Path) -> DirectModelAdapter:
    return DirectModelAdapter(
        provider="anthropic",
        model="claude-sonnet-5-5",
        api_key="fake-key",
        work_dir=tmp_path,
    )


def test_anthropic_thinking_only_output_limit_raises_without_final_answer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter = _anthropic_adapter(tmp_path)
    monkeypatch.setattr(
        adapter,
        "_post",
        lambda *_args: {
            "content": [{"type": "thinking", "thinking": "Working..."}],
            "stop_reason": "max_tokens",
        },
    )
    events: list[str] = []

    with pytest.raises(CodexChatAgentError, match="Anthropic is unavailable") as caught:
        adapter.start_turn("hello", lambda kind, _payload: events.append(kind))

    assert caught.value.error_code == "provider_unavailable"
    assert "output limit" in caught.value.gate["next_action"]
    assert "answer.final" not in events


def test_anthropic_partial_text_output_limit_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter = _anthropic_adapter(tmp_path)
    monkeypatch.setattr(
        adapter,
        "_post",
        lambda *_args: {
            "content": [{"type": "text", "text": "Incomplete answer"}],
            "stop_reason": "max_tokens",
        },
    )

    with pytest.raises(CodexChatAgentError, match="Anthropic is unavailable"):
        adapter.start_turn("hello", lambda _kind, _payload: None)


def test_anthropic_end_turn_returns_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter = _anthropic_adapter(tmp_path)
    monkeypatch.setattr(
        adapter,
        "_post",
        lambda *_args: {
            "content": [{"type": "text", "text": "Complete answer"}],
            "stop_reason": "end_turn",
        },
    )

    response = adapter.start_turn("hello", lambda _kind, _payload: None)

    assert response["message"] == "Complete answer"


def test_anthropic_tool_use_continues_to_end_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter = _anthropic_adapter(tmp_path)
    payloads = iter(
        [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "list_files",
                        "input": {"pattern": "*.txt"},
                    }
                ],
                "stop_reason": "tool_use",
            },
            {
                "content": [{"type": "text", "text": "Final answer"}],
                "stop_reason": "end_turn",
            },
        ]
    )
    bodies: list[dict[str, Any]] = []

    def fake_post(
        _url: str,
        body: dict[str, Any],
        _headers: dict[str, str],
    ) -> dict[str, Any]:
        bodies.append(body)
        return next(payloads)

    monkeypatch.setattr(adapter, "_post", fake_post)

    response = adapter.start_turn("hello", lambda _kind, _payload: None)

    assert response["message"] == "Final answer"
    assert len(bodies) == 2
    assert bodies[1]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_anthropic_thinking_only_output_limit_persists_failed_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from loopx import chat_runtime
    from loopx.chat import session_open
    from loopx.control_plane import chat_turn_acceptance

    def accept_fresh_turn(_method: str, request: dict[str, Any]) -> dict[str, Any]:
        candidate = request["candidate"]
        return {
            "schema_version": chat_turn_acceptance.CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA,
            "kind": "accepted",
            "disposition": "created",
            "turn_id": candidate["turn_id"],
            "message_id": candidate["message_id"],
            "request_sha256": "sha256:" + "0" * 64,
            "created": True,
            "writes": {
                "prepare_turn": True,
                "activate_session": True,
                "append_message": True,
                "append_queued_event": True,
                "settle_turn": True,
            },
            "dispatch": {"kind": "required", "reason": None},
        }

    monkeypatch.setattr(
        chat_turn_acceptance, "effect_runtime_result", accept_fresh_turn
    )
    monkeypatch.setattr(
        chat_turn_acceptance, "managed_turn_execution_allowed", lambda *_args: True
    )
    monkeypatch.setattr(
        chat_runtime,
        "conversation_scope",
        lambda *_args, **_kwargs: {"kind": "fixture"},
    )
    monkeypatch.setattr(
        session_open,
        "conversation_scope",
        lambda *_args, **_kwargs: {"kind": "fixture"},
    )
    store = ChatSessionStore(tmp_path / "runtime")
    adapter = _anthropic_adapter(tmp_path)
    monkeypatch.setattr(
        adapter,
        "_post",
        lambda *_args: {
            "content": [{"type": "thinking", "thinking": "Working..."}],
            "stop_reason": "max_tokens",
        },
    )
    session = store.create_session(
        goal_id="fixture",
        agent_id="anthropic-api",
        adapter_kind="anthropic_messages_api",
        upstream_thread_id=adapter.upstream_thread_id,
        upstream_mode="chat",
    )
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    runtime.adapters[session["session_id"]] = adapter

    try:
        turn, created = runtime.submit_turn(
            session_id=session["session_id"],
            client_turn_id="thinking-only-limit",
            message="Use the full output budget.",
            work_dir=tmp_path,
            objective="Exercise the direct Anthropic Chat path.",
        )
        assert created is True
        ended = runtime.wait_for_turn(
            session_id=session["session_id"],
            turn_id=turn["turn_id"],
            timeout_sec=10,
        )
    finally:
        runtime.close()

    reopened = ChatSessionStore(store.root.parent)
    persisted = reopened.load_turn(session["session_id"], turn["turn_id"])
    events = reopened.events_after(session["session_id"], turn["turn_id"], None)

    assert ended["status"] == "failed"
    assert persisted is not None
    assert persisted["status"] == "failed"
    assert persisted["error_code"] == "provider_unavailable"
    assert "Anthropic is unavailable" in persisted["error"]
    failed_event = next(event for event in events if event["kind"] == "turn.failed")
    assert "output limit" in failed_event["payload"]["gate"]["next_action"]
    assert not any(
        event["kind"] in {"answer.final", "turn.completed"} for event in events
    )
    assert any(
        message["role"] == "error" and "Anthropic is unavailable" in message["text"]
        for message in reopened.messages(session["session_id"])
    )
