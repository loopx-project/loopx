from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from loopx.chat_runtime import ChatRuntimeController


def _runtime() -> ChatRuntimeController:
    runtime = ChatRuntimeController.__new__(ChatRuntimeController)
    runtime.store = Mock()  # type: ignore[assignment]
    runtime.lock = threading.RLock()
    runtime.turn_done_events = {}
    return runtime


def test_wait_for_turn_uses_managed_completion_event() -> None:
    runtime = _runtime()
    runtime.store.load_turn.side_effect = [{"status": "running"}, {"status": "completed"}]
    completion = Mock()
    runtime.turn_done_events[("session", "turn")] = completion  # type: ignore[assignment]

    turn = runtime.wait_for_turn(session_id="session", turn_id="turn", timeout_sec=0.1)

    assert turn["status"] == "completed"
    completion.wait.assert_called_once()
    assert runtime.store.load_turn.call_count == 2


def test_wait_for_turn_performs_final_fallback_read_at_deadline(monkeypatch) -> None:
    runtime = _runtime()
    runtime.store.load_turn.side_effect = [{"status": "running"}, {"status": "completed"}]
    # The first read is before the deadline; the fallback read is exactly at it.
    clock = Mock(side_effect=[0.0, 0.0, 0.001])
    sleep = Mock()
    monkeypatch.setattr("loopx.chat_runtime.time", SimpleNamespace(monotonic=clock, sleep=sleep))

    turn = runtime.wait_for_turn(session_id="session", turn_id="turn", timeout_sec=0.001)

    assert turn["status"] == "completed"
    assert runtime.store.load_turn.call_count == 2
    sleep.assert_called_once_with(0.001)


def test_wait_for_turn_refuses_unfinished_initial_read_at_deadline(monkeypatch) -> None:
    runtime = _runtime()
    runtime.store.load_turn.return_value = {"status": "running"}
    sleep = Mock()
    monkeypatch.setattr("loopx.chat_runtime.time", SimpleNamespace(
        monotonic=Mock(side_effect=[0.0, 0.001]), sleep=sleep,
    ))

    with pytest.raises(TimeoutError, match="chat turn wait timed out"):
        runtime.wait_for_turn(session_id="session", turn_id="turn", timeout_sec=0.001)

    assert runtime.store.load_turn.call_count == 1
    sleep.assert_not_called()
