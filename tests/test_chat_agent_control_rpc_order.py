"""Tools must retain the preceding Turn notification during a control RPC."""

import queue
import time
from pathlib import Path

import pytest

from loopx.chat_agent import CodexChatAgentSession


@pytest.mark.parametrize(
    "started,foreign,authorized", [(True, False, True), (True, True, True),
                                 (False, False, True), (True, False, False)]
)
def test_control_read_preserves_turn_binding_and_tool_permission(
    started, foreign, authorized
):
    messages = queue.Queue()
    notification = {
        "method": "turn/started",
        "params": {"threadId": "thread", "turnId": "new-turn"},
    }
    request = {
        "id": 200,
        "method": "item/tool/call",
        "params": {"threadId": "thread", "turnId": "foreign" if foreign else "new-turn",
                   "callId": "200", "tool": "loopx_collaboration",
                   "arguments": {"action": "bindings"}},
    }
    if started:
        messages.put(notification)
    messages.put(request)
    messages.put({"id": 1, "result": {"goal": {"status": "active"}}})
    session = CodexChatAgentSession(
        process=None, messages=messages, thread_id="thread", work_dir=Path.cwd()
    )
    writes, calls = [], []
    session._write = writes.append

    def handler(name, arguments):
        calls.append((name, arguments))
        return {"ok": authorized}

    session.read_tool_handler = handler
    assert session._request("thread/goal/get", {"threadId": "thread"}, request_id=1) == {
        "goal": {"status": "active"}
    }
    assert not calls
    if started:
        assert session._next_event(deadline=time.monotonic() + 1) == notification
        session.current_turn_id = "new-turn"
    if started and not foreign:
        assert len(writes) == 1
        event = session._next_event(deadline=time.monotonic() + 1)
        assert event == request
        assert session._check_server_gate(event)
        assert calls == [("loopx_collaboration", {"action": "bindings"})]
        assert writes[-1]["result"]["success"] is authorized
    else:
        assert not calls
        assert writes[-1]["result"]["success"] is False
