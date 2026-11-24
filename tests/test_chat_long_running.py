"""Long-running interactive input keeps its real lifecycle and explicit limits."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import queue

import pytest

from loopx import chat_agent
from loopx.chat import acp as chat_acp
from loopx.chat_store import ChatSessionStore
from loopx.cli import build_parser
from loopx.cli_commands.support_control_chat import chat_server_options


@pytest.mark.parametrize("hard, idle, expected", [(None, False, None), (5.0, False, "hard_timeout"), (None, True, "idle_timeout")])
def test_native_activity_can_cross_old_wall_limit_but_limits_still_apply(monkeypatch, tmp_path, hard, idle, expected):
    clock = [0.0]
    monkeypatch.setattr(chat_agent.time, "monotonic", lambda: clock[0])
    host = chat_agent.CodexChatAgentSession(process=SimpleNamespace(poll=lambda: None),
        messages=queue.Queue(), thread_id="original", work_dir=tmp_path,
        idle_timeout_sec=5, hard_timeout_sec=hard)
    monkeypatch.setattr(host, "_request", lambda *a, **kw: {"turn": {"id": "turn"}})
    count = [0]

    def event(*, deadline):
        clock[0] += 6 if idle else 2
        if clock[0] >= deadline:
            raise host._runtime_error("upstream wait elapsed")
        count[0] += 1
        return {"method": "turn/completed", "params": {"threadId": "original", "turn": {"id": "turn", "status": "completed"}}} if count[0] == 1001 else {
            "method": "item/agentMessage/delta", "params": {"threadId": "original", "turnId": "turn", "delta": "progress "}}

    monkeypatch.setattr(host, "_next_event", event)
    if expected:
        with pytest.raises(chat_agent.CodexChatTimeoutError) as caught:
            host.send("continue the original request")
        assert caught.value.error_code == expected
    else:
        host.send("continue the original request")
        assert clock[0] > 1800


def test_acp_progress_has_no_implicit_wall_limit(monkeypatch, tmp_path):
    clock = [0.0]
    monkeypatch.setattr(chat_acp.time, "monotonic", lambda: clock[0])
    host = chat_acp.ACPStdioAdapter(process=SimpleNamespace(poll=lambda: None), messages=queue.Queue(),
        session_id="original", work_dir=tmp_path, agent_work_dir=tmp_path, agent_capabilities={})
    monkeypatch.setattr(host, "_write", lambda _: None)
    count = [0]

    def message(_timeout):
        clock[0] += .25
        count[0] += 1
        return {"id": 1, "result": {"done": True}} if count[0] > 8000 else {"method": "session/update"}

    monkeypatch.setattr(host, "_next_message", message)
    assert host._request("session/prompt", {}, request_id=1, timeout_sec=None, idle_timeout_sec=5) == {"done": True}
    assert clock[0] > 1800


def test_queued_input_survives_long_wait_and_keeps_exact_identity(tmp_path, monkeypatch):
    store = ChatSessionStore(tmp_path)
    sid = store.create_session(goal_id="fixture", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="original", upstream_mode="chat")["session_id"]
    turn, created = store.create_queued_turn(sid, client_turn_id="original", message="do not drop accepted input")
    assert created and turn["expires_at"] is None
    from loopx import chat_store
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(days=2)
    monkeypatch.setattr(chat_store, "datetime", Future)
    replay, created = store.create_queued_turn(sid, client_turn_id="original", message=turn["message"])
    assert not created and replay["turn_id"] == turn["turn_id"]
    claimed = store.claim_next_queued_turn(sid, host_claim_id="host")
    assert claimed["turn_id"] == turn["turn_id"] and claimed["message"] == turn["message"]


@pytest.mark.parametrize("flag,key", [
    ("--hard-timeout-seconds", "hard_timeout_sec"),
    ("--idle-timeout-seconds", "idle_timeout_sec"),
])
@pytest.mark.parametrize("value,expected", [("0", None), ("1800", 1800.0), ("86400", 86400.0)])
def test_cli_explicit_timeout_choice_reaches_shared_owner(tmp_path, flag, key, value, expected):
    args = build_parser().parse_args(["chat", flag, value])
    assert chat_server_options(args, registry_path=tmp_path / "registry.json")[key] == expected


@pytest.mark.parametrize("flag", ["--hard-timeout-seconds", "--idle-timeout-seconds"])
@pytest.mark.parametrize("value", ["-1", "nan", "inf"])
def test_invalid_unbounded_timeout_spellings_do_not_disable_guards(tmp_path, flag, value):
    args = build_parser().parse_args(["chat", flag, value])
    with pytest.raises(ValueError, match="nonnegative finite"):
        chat_server_options(args, registry_path=tmp_path / "registry.json")


def test_default_sessions_disable_idle_timeout_across_codex_and_acp(monkeypatch, tmp_path):
    clock = [0.0]
    monkeypatch.setattr(chat_agent.time, "monotonic", lambda: clock[0])
    host = chat_agent.CodexChatAgentSession(
        process=SimpleNamespace(poll=lambda: None),
        messages=queue.Queue(),
        thread_id="original",
        work_dir=tmp_path,
    )
    assert host.idle_timeout_sec is None and host.hard_timeout_sec is None
    monkeypatch.setattr(host, "_request", lambda *a, **kw: {"turn": {"id": "turn"}})
    deadlines = []

    def event(*, deadline=None):
        deadlines.append(deadline)
        clock[0] += 900.0
        return (
            {"method": "turn/completed", "params": {"threadId": "original", "turn": {"id": "turn", "status": "completed"}}}
            if len(deadlines) == 2
            else {"method": "item/agentMessage/delta", "params": {"threadId": "original", "turnId": "turn", "delta": "ok"}}
        )

    monkeypatch.setattr(host, "_next_event", event)
    assert host.send("long silent step")["message"] == "ok"
    assert deadlines == [None, None]

    acp_clock = [0.0]
    monkeypatch.setattr(chat_acp.time, "monotonic", lambda: acp_clock[0])
    acp_host = chat_acp.ACPStdioAdapter(
        process=SimpleNamespace(poll=lambda: None),
        messages=queue.Queue(),
        session_id="original",
        work_dir=tmp_path,
        agent_work_dir=tmp_path,
        agent_capabilities={},
    )
    assert acp_host.idle_timeout_sec is None
    monkeypatch.setattr(acp_host, "_write", lambda _: None)
    acp_polls = [0]

    def acp_message(_timeout):
        acp_clock[0] += 900.0
        acp_polls[0] += 1
        if acp_polls[0] == 1:
            raise TimeoutError("still quiet")
        return {"id": 1, "result": {"done": True}}

    monkeypatch.setattr(acp_host, "_next_message", acp_message)
    assert acp_host._request("session/prompt", {}, request_id=1, timeout_sec=acp_host.hard_timeout_sec, idle_timeout_sec=acp_host.idle_timeout_sec) == {"done": True}
    assert acp_clock[0] == 1800.0
