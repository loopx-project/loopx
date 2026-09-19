"""The steward's managed-host transport runs one bounded, read-only segment."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from loopx import chat_dsh
from loopx.chat import CHAT_REVIEW_CLOSE_TAG, CHAT_REVIEW_OPEN_TAG
from loopx.chat_agent import CodexChatAgentError
from loopx.chat_dsh import (
    HISTORY_LIMIT,
    MANAGED_HOST_CHAT_FAILED,
    MANAGED_HOST_CHAT_SEGMENT_IN_FLIGHT,
    MANAGED_HOST_CHAT_TIMEOUT,
    STEWARD_SEGMENT_ENV,
    DshChatAdapter,
)


class _Runner:
    """Records the segment input and returns a scripted outcome."""

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.outcome


def _envelope(message: str) -> str:
    payload = {
        "message": message,
        "proposals": [],
        "protected_action": None,
        "context_handoff": None,
        "gate": None,
    }
    return (
        "visible answer\n"
        + CHAT_REVIEW_OPEN_TAG
        + json.dumps(payload)
        + CHAT_REVIEW_CLOSE_TAG
    )


def _adapter(runner: _Runner, tmp_path: Path) -> DshChatAdapter:
    return DshChatAdapter(
        objective="You are the LoopX manager.",
        work_dir=tmp_path,
        provider="deepseek-official",
        model="deepseek-v4-flash",
        reasoning_effort="high",
        runner=runner,
    )


def test_a_segment_carries_the_resolved_profile_and_a_read_only_boundary(
    tmp_path: Path,
) -> None:
    runner = _Runner({"final_response": _envelope("answer"), "finish_reason": "stop"})
    events: list[tuple[str, dict[str, Any]]] = []

    response = _adapter(runner, tmp_path).start_turn(
        "status please", lambda kind, payload: events.append((kind, payload))
    )

    call = runner.calls[0]
    assert call["provider"] == "deepseek-official"
    assert call["model"] == "deepseek-v4-flash"
    assert call["reasoning_effort"] == "high"
    assert call["max_tokens"] == 16_384
    # The channel pins its own segments read-only; dsh enforces it.
    assert call["env"] == STEWARD_SEGMENT_ENV
    assert call["session_root"] == tmp_path / ".local" / ".dsh-sessions"
    assert response["message"] == "answer"
    assert [kind for kind, _payload in events] == [
        "turn.started",
        "agent.phase",
        "answer.delta",
        "answer.final",
    ]


def test_the_segment_input_keeps_the_channel_objective_and_turn_contract(
    tmp_path: Path,
) -> None:
    runner = _Runner({"final_response": _envelope("answer")})

    _adapter(runner, tmp_path).start_turn("status please", lambda *_args: None)

    prompt = runner.calls[0]["prompt"]
    assert prompt.startswith("You are the LoopX manager.")
    assert "Operator user message:\nstatus please" in prompt
    assert CHAT_REVIEW_OPEN_TAG in prompt


def test_an_empty_final_message_fails_closed(tmp_path: Path) -> None:
    runner = _Runner({"final_response": "", "finish_reason": "error"})

    with pytest.raises(CodexChatAgentError) as raised:
        _adapter(runner, tmp_path).start_turn("status please", lambda *_args: None)

    assert raised.value.error_code == MANAGED_HOST_CHAT_FAILED


@pytest.mark.parametrize("final_response", ["", _envelope("truncated answer")])
@pytest.mark.parametrize("reason_source", ["field", "event"])
def test_token_exhaustion_never_publishes_a_segment_answer(
    tmp_path: Path, final_response: str, reason_source: str,
) -> None:
    terminal = {"finish_reason": "max-tokens"} if reason_source == "field" else {
        "events": [{"type": "turn/end", "data": {"reason": {"kind": "max-tokens"}}}],
    }
    runner = _Runner({"final_response": final_response, **terminal})
    adapter = _adapter(runner, tmp_path)
    events: list[str] = []

    with pytest.raises(CodexChatAgentError) as raised:
        adapter.start_turn("status please", lambda kind, _payload: events.append(kind))

    suffix = "partial" if final_response else "no_final"
    assert raised.value.error_code == f"dsh_output_budget_exhausted_{suffix}"
    assert adapter.history == []
    assert not any(kind.startswith("answer.") for kind in events)
    assert len(runner.calls) == 1


@pytest.mark.parametrize("limit,expected", [(None, 16_384), (5_000, 5_000)])
def test_segment_budget_reaches_the_runner(tmp_path: Path, limit: int | None, expected: int) -> None:
    runner = _Runner({"final_response": _envelope("answer"), "finish_reason": "stop"})
    adapter = _adapter(runner, tmp_path)
    adapter.max_tokens = limit
    adapter.start_turn("status please", lambda *_args: None)
    assert runner.calls[0]["max_tokens"] == expected


@pytest.mark.parametrize("limit", [0, -1, True])
def test_invalid_segment_budget_does_not_launch(tmp_path: Path, limit: object) -> None:
    runner = _Runner({"final_response": _envelope("answer")})
    adapter = _adapter(runner, tmp_path)
    adapter.max_tokens = limit  # type: ignore[assignment]
    with pytest.raises(CodexChatAgentError):
        adapter.start_turn("status please", lambda *_args: None)
    assert runner.calls == []


def test_an_unreadable_segment_result_fails_closed(tmp_path: Path) -> None:
    runner = _Runner(["not", "a", "mapping"])

    with pytest.raises(CodexChatAgentError) as raised:
        _adapter(runner, tmp_path).start_turn("status please", lambda *_args: None)

    assert raised.value.error_code == MANAGED_HOST_CHAT_FAILED


def test_a_raising_runner_surfaces_one_typed_failure(tmp_path: Path) -> None:
    def failing(**_kwargs: Any) -> Any:
        raise RuntimeError("provider detail that must not become the error code")

    adapter = DshChatAdapter(
        objective="objective",
        work_dir=tmp_path,
        provider="deepseek-official",
        model="deepseek-v4-flash",
        reasoning_effort="high",
        runner=failing,
    )

    with pytest.raises(CodexChatAgentError) as raised:
        adapter.start_turn("status please", lambda *_args: None)

    assert raised.value.error_code == MANAGED_HOST_CHAT_FAILED
    assert "provider detail" not in raised.value.error_code


def test_a_segment_that_never_returns_times_out(tmp_path: Path) -> None:
    import threading

    release = threading.Event()
    try:
        adapter = DshChatAdapter(
            objective="objective",
            work_dir=tmp_path,
            provider="deepseek-official",
            model="deepseek-v4-flash",
            reasoning_effort="high",
            timeout_sec=0.05,
            runner=lambda **_kwargs: release.wait(5),
        )

        with pytest.raises(CodexChatAgentError) as raised:
            adapter.start_turn("status please", lambda *_args: None)
    finally:
        release.set()

    assert raised.value.error_code == MANAGED_HOST_CHAT_TIMEOUT


def test_visible_history_is_bounded_and_reaches_the_next_segment(
    tmp_path: Path,
) -> None:
    runner = _Runner({"final_response": _envelope("first answer")})
    adapter = _adapter(runner, tmp_path)
    adapter.start_turn("first question", lambda *_args: None)
    runner.outcome = {"final_response": _envelope("second answer")}

    adapter.start_turn("second question", lambda *_args: None)

    assert adapter.history == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "second answer"},
    ]
    assert len(adapter.history) <= HISTORY_LIMIT


def _blocking_adapter(
    tmp_path: Path,
    *,
    first_answer: str,
    second_answer: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[DshChatAdapter, list[dict[str, Any]], threading.Event, threading.Event]:
    """Build an adapter whose first segment blocks until the test releases it."""

    started = threading.Event()
    release = threading.Event()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(chat_dsh, "SEGMENT_HANDOFF_GRACE_SEC", 0.05)

    def runner(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if len(calls) == 1:
            started.set()
            release.wait(5)
            return {"final_response": _envelope(first_answer)}
        return {"final_response": _envelope(second_answer)}

    adapter = DshChatAdapter(
        objective="You are the LoopX manager.",
        work_dir=tmp_path,
        provider="deepseek-official",
        model="deepseek-v4-flash",
        reasoning_effort="high",
        runner=runner,
    )
    return adapter, calls, started, release


def test_a_second_segment_start_is_refused_while_one_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, calls, started, release = _blocking_adapter(
        tmp_path,
        first_answer="first answer",
        second_answer="second answer",
        monkeypatch=monkeypatch,
    )
    finished: list[dict[str, Any]] = []
    worker = threading.Thread(
        target=lambda: finished.append(adapter.start_turn("first question", lambda *_a: None))
    )
    worker.start()
    try:
        assert started.wait(5)
        with pytest.raises(CodexChatAgentError) as raised:
            adapter.start_turn("second question", lambda *_a: None)
        # The refusal is the point: the second caller never reaches the host.
        assert raised.value.error_code == MANAGED_HOST_CHAT_SEGMENT_IN_FLIGHT
        assert len(calls) == 1
    finally:
        release.set()
        worker.join(5)

    assert finished[0]["message"] == "first answer"


def test_an_interrupted_segment_answer_never_becomes_visible_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, _calls, started, release = _blocking_adapter(
        tmp_path,
        first_answer="discarded answer",
        second_answer="second answer",
        monkeypatch=monkeypatch,
    )
    events: list[tuple[str, dict[str, Any]]] = []
    finished: list[dict[str, Any]] = []
    worker = threading.Thread(
        target=lambda: finished.append(
            adapter.start_turn("first question", lambda kind, payload: events.append((kind, payload)))
        )
    )
    worker.start()
    try:
        assert started.wait(5)
        adapter.interrupt_turn()
    finally:
        release.set()
        worker.join(5)

    assert finished[0]["message"] == "discarded answer"
    # The channel discarded the turn, so neither the answer events nor the
    # visible history may carry its text forward.
    assert [kind for kind, _payload in events] == ["turn.started", "agent.phase"]
    assert adapter.history == []


def test_the_next_segment_starts_once_the_previous_segment_exited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, calls, started, release = _blocking_adapter(
        tmp_path,
        first_answer="first answer",
        second_answer="second answer",
        monkeypatch=monkeypatch,
    )
    worker = threading.Thread(
        target=lambda: adapter.start_turn("first question", lambda *_a: None)
    )
    worker.start()
    assert started.wait(5)
    release.set()
    worker.join(5)

    response = adapter.start_turn("second question", lambda *_a: None)

    assert response["message"] == "second answer"
    assert len(calls) == 2
    assert adapter.history == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "second answer"},
    ]
