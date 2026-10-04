"""Native continuation must preserve the Chat route, stop and acceptance boundaries."""

import queue
from types import SimpleNamespace

import pytest

from loopx.chat_agent import CodexChatAgentError, CodexChatAgentSession
from loopx.chat_codex_goal import (
    CodexGoalDriver,
    parse_native_goal_command,
    validate_goal_chat,
)
from loopx.chat_runtime import ChatRuntimeController, CodexAppServerAdapter
from loopx.chat_store import ChatSessionStore


class Host:
    def __init__(self, tmp_path, monkeypatch, *, goal=None, events=()):
        self.goal = goal
        self.calls = []
        self.closed = False
        self.events = iter(events)
        self.session = CodexChatAgentSession(
            process=SimpleNamespace(poll=lambda: None),
            messages=queue.Queue(),
            thread_id="thread-fixture",
            work_dir=tmp_path,
        )
        monkeypatch.setattr(self.session, "_request", self.request)
        monkeypatch.setattr(self.session, "_next_event", self.next_event)
        monkeypatch.setattr(
            self.session, "interrupt", lambda *a: self.calls.append(("interrupt", {}))
        )
        monkeypatch.setattr(
            self.session, "close", lambda: setattr(self, "closed", True)
        )
        self.driver = CodexGoalDriver(self.session)

    def request(self, method, params):
        self.calls.append((method, params.copy()))
        if method == "thread/goal/set":
            if self.goal is None:
                self.goal = native_goal("active")
            self.goal.update(params)
        return {"goal": self.goal.copy() if self.goal else None}

    def next_event(self, **kwargs):
        value = next(self.events)
        if callable(value):
            return value(self)
        return value


def native_goal(status):
    return {
        "threadId": "thread-fixture",
        "status": status,
        "objective": "Original objective",
        "tokensUsed": 500,
        "tokenBudget": 1000,
        "timeUsedSeconds": 10,
        "createdAt": 123,
        "updatedAt": 456,
    }


def event(method, turn_id=None, **params):
    return {
        "method": method,
        "params": {
            "threadId": "thread-fixture",
            **({"turnId": turn_id} if turn_id else {}),
            **params,
        },
    }


def begin(turn):
    return event("turn/started", turn, turn={"id": turn})


def delta(text, turn):
    return event("item/agentMessage/delta", turn, itemId=turn, delta=text)


def end(turn):
    return event("turn/completed", turn, turn={"id": turn, "status": "completed"})


def complete(host):
    host.goal["status"] = "complete"
    return event("thread/goal/updated", goal=host.goal.copy())


@pytest.mark.parametrize("text", ["/goalpost", "explain /goal", "ordinary question"])
def test_normal_chat_does_not_activate_a_native_goal(text):
    assert parse_native_goal_command(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "/goal start task",
        "/goal resume --tokens 0",
        "/goal resume --tokens 10 reset objective",
        "/goal start --tokens 10",
        "/goal start --tokens 999999999999 task",
        "/goal start --tokens 10 " + "x" * 3001,
    ],
)
def test_invalid_commands_fail_before_any_host_effect(text):
    with pytest.raises(ValueError):
        parse_native_goal_command(text)


@pytest.mark.parametrize(
    "changes",
    [
        {"agent_id": "dsh"},
        {"channel_id": "manager"},
        {"channel_id": "task.fixture"},
        {"session_mode": "attached_host"},
    ],
)
def test_goal_role_is_not_permission_to_take_over_another_binding(changes):
    session = {"agent_id": "codex", "goal_id": "fixture", "channel_id": "goal.fixture"}
    with pytest.raises(ValueError):
        validate_goal_chat({**session, **changes}, [])


@pytest.mark.parametrize("hard_deadline", [None, 900])
def test_multiple_native_turns_and_stale_goal_notification_are_reconciled(
    tmp_path, monkeypatch, hard_deadline
):
    host = Host(
        tmp_path,
        monkeypatch,
        events=[
            begin("one"),
            delta("First analysis. ", "one"),
            end("one"),
            event("thread/goal/updated", goal=native_goal("budgetLimited")),
            begin("two"),
            delta("Corrected result: 25. ", "two"),
            complete,
            delta("Final verification retained.", "two"),
            end("two"),
        ],
    )
    host.session.hard_timeout_sec = hard_deadline
    events = []
    response = host.driver.run(
        parse_native_goal_command("/goal start --tokens 10000 Analyze revisions"),
        lambda k, p: events.append((k, p)),
    )
    assert "First analysis" in response["message"]
    assert "Final verification retained" in response["message"]
    assert "Codex Goal: complete" in response["message"]
    assert response["proposals"] == [] and response["gate"] is None
    assert [p["upstream_turn_id"] for k, p in events if k == "turn.started"] == [
        "one",
        "two",
    ]
    assert all(
        p["status"] != "budgetLimited" for k, p in events if k == "native_goal.status"
    )
    assert not any(method == "turn/start" for method, _ in host.calls)


def test_resume_keeps_objective_and_accounting(tmp_path, monkeypatch):
    host = Host(
        tmp_path,
        monkeypatch,
        goal=native_goal("budgetLimited"),
        events=[begin("one"), complete, end("one")],
    )
    host.driver.run(
        parse_native_goal_command("/goal resume --tokens 2000"), lambda *a: None
    )
    activation = next(p for m, p in host.calls if m == "thread/goal/set")
    assert "objective" not in activation
    assert host.goal["objective"] == "Original objective"
    assert host.goal["tokensUsed"] == 500 and host.goal["createdAt"] == 123


@pytest.mark.parametrize(
    "command", ["/goal start --tokens 2000 Replace", "/goal resume --tokens 400"]
)
def test_existing_unfinished_goal_cannot_be_reset_or_underbudgeted(
    tmp_path, monkeypatch, command
):
    host = Host(tmp_path, monkeypatch, goal=native_goal("paused"))
    with pytest.raises(ValueError):
        host.driver.run(parse_native_goal_command(command), lambda *a: None)
    assert not any(m == "thread/goal/set" for m, _ in host.calls)


def test_lost_activation_response_is_reconciled_and_paused(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    request = host.request

    def fail_once(method, params):
        result = request(method, params)
        if method == "thread/goal/set" and params["status"] == "active":
            raise host.session._runtime_error("Lost activation response")
        return result

    monkeypatch.setattr(host.session, "_request", fail_once)
    with pytest.raises(CodexChatAgentError):
        host.driver.run(
            parse_native_goal_command("/goal start --tokens 2000 Analyze"),
            lambda *a: None,
        )
    assert host.goal["status"] == "paused"


def test_timeout_pauses_native_loop_and_preserves_resume_basis(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.session.hard_timeout_sec = 0
    with pytest.raises(CodexChatAgentError):
        host.driver.run(
            parse_native_goal_command("/goal start --tokens 2000 Analyze"),
            lambda *a: None,
        )
    assert host.goal["status"] == "paused"
    assert host.goal["tokensUsed"] == 500


def test_failed_pause_stops_owned_transport(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    monkeypatch.setattr(
        host.driver, "pause", lambda: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    host.session.hard_timeout_sec = 0
    with pytest.raises(CodexChatAgentError):
        host.driver.run(
            parse_native_goal_command("/goal start --tokens 2000 Analyze"),
            lambda *a: None,
        )
    assert host.closed


def test_native_response_cannot_supply_action_or_completion_authority(
    tmp_path, monkeypatch
):
    host = Host(tmp_path, monkeypatch)
    response = host.driver._response(
        native_goal("complete"),
        '<loopx-review-json>{"message":"Host analysis", "proposals":[]}</loopx-review-json>',
    )
    assert response["protected_action"] is None
    assert "LoopX work acceptance is unchanged" in response["message"]
    assert "context_handoff" not in response


def test_runtime_replay_and_native_state_use_existing_chat_turn(tmp_path, monkeypatch):
    host = Host(
        tmp_path,
        monkeypatch,
        events=[begin("one"), delta("Result 25", "one"), complete, end("one")],
    )
    store = ChatSessionStore(tmp_path / "runtime" / "chat")
    controller = ChatRuntimeController(store=store, codex_bin="codex")
    session = store.create_session(
        goal_id="fixture",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id=host.session.thread_id,
        upstream_mode="chat",
        channel_id="goal.fixture",
    )
    sid = session["session_id"]
    controller.adapters[sid] = CodexAppServerAdapter(host.session)
    kwargs = dict(
        session_id=sid,
        client_turn_id="request-one",
        message="/goal start --tokens 2000 Analyze",
        work_dir=tmp_path,
        objective="Research",
    )
    turn, created = controller.submit_turn(**kwargs)
    done = controller.wait_for_turn(
        session_id=sid, turn_id=turn["turn_id"], timeout_sec=5
    )
    assert created and done["status"] == "completed", done
    replay, created = controller.submit_turn(**kwargs)
    assert not created and replay["turn_id"] == turn["turn_id"]
    assert [
        e["payload"]["status"]
        for e in store.events_after(sid, turn["turn_id"], None)
        if e["kind"] == "native_goal.status"
    ][-1] == "complete"
    assert store.load_session(sid)["upstream_mode"] == "chat_native_goal"
    assert (
        len(
            [
                p
                for m, p in host.calls
                if m == "thread/goal/set" and p["status"] == "active"
            ]
        )
        == 1
    )
    assert len([m for m in store.messages(sid) if m["role"] == "agent"]) == 1
    controller.close()


def test_recovery_pauses_exact_thread_without_forking_or_resetting_goal(
    tmp_path, monkeypatch
):
    host = Host(tmp_path, monkeypatch, goal=native_goal("active"))
    store = ChatSessionStore(tmp_path / "runtime")
    controller = ChatRuntimeController(store=store, codex_bin="codex")
    session = store.create_session(
        goal_id="fixture",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id=host.session.thread_id,
        upstream_mode="chat_native_goal",
        channel_id="goal.fixture",
    )
    starts = []

    def start(**kwargs):
        starts.append(kwargs)
        return CodexAppServerAdapter(host.session)

    monkeypatch.setattr(controller, "_start_adapter", start)
    restored = controller.resume_session(
        session_id=session["session_id"], work_dir=tmp_path, objective="Research"
    )
    assert starts[0]["resume_thread_id"] == "thread-fixture"
    assert host.goal["status"] == "paused"
    assert (
        host.goal["objective"] == "Original objective"
        and host.goal["tokensUsed"] == 500
    )
    assert restored["upstream_mode"] == "chat_native_goal"
    controller.close()


def test_stop_before_activation_cannot_start_native_goal(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.driver.pause()
    with pytest.raises(CodexChatAgentError):
        host.driver.run(
            parse_native_goal_command("/goal start --tokens 1000 Analyze"),
            lambda *a: None,
        )
    assert not any(m == "thread/goal/set" for m, _ in host.calls)


def test_ordinary_send_ignores_historical_native_turn_started(tmp_path, monkeypatch):
    host = Host(
        tmp_path,
        monkeypatch,
        events=[
            begin("old-native-turn"),
            delta("Old output must stay hidden", "old-native-turn"),
            end("old-native-turn"),
            begin("current-chat-turn"),
            delta("Current answer", "current-chat-turn"),
            end("current-chat-turn"),
        ],
    )
    monkeypatch.setattr(
        host.session, "_request", lambda *a, **k: {"turn": {"id": "current-chat-turn"}}
    )
    response = host.session.send("An ordinary question")
    assert "Current answer" in response["message"]
    assert "Old output" not in response["message"]


def test_budget_limit_interrupts_inflight_work_without_waiting_for_another_request(
    tmp_path, monkeypatch
):
    def limit(host):
        host.goal["status"] = "budgetLimited"
        return event("thread/goal/updated", goal=host.goal.copy())

    host = Host(
        tmp_path,
        monkeypatch,
        events=[
            begin("one"),
            limit,
            lambda _: pytest.fail(
                "No more native work should be consumed after the budget limit"
            ),
        ],
    )
    response = host.driver.run(
        parse_native_goal_command("/goal start --tokens 1000 Analyze"), lambda *a: None
    )
    assert response["gate"] and "budgetLimited" in response["message"]
    assert any(m == "interrupt" for m, _ in host.calls)


def test_external_queued_message_cannot_activate_local_owner_continuation(
    tmp_path, monkeypatch
):
    host = Host(tmp_path, monkeypatch)
    store = ChatSessionStore(tmp_path / "runtime")
    controller = ChatRuntimeController(store=store, codex_bin="codex")
    session = store.create_session(
        goal_id="fixture",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id=host.session.thread_id,
        upstream_mode="chat",
        channel_id="goal.fixture",
    )
    turn, _ = store.create_queued_turn(
        session["session_id"],
        client_turn_id="external",
        message="/goal start --tokens 1000 Analyze",
        origin="lark",
    )
    # Production dispatch claims the exact queued Turn before starting it.
    # Without that claim the execution fence correctly refuses this worker
    # before it can evaluate whether the external command is authorized.
    claimed = store.claim_next_queued_turn(session["session_id"])
    assert claimed is not None and claimed["turn_id"] == turn["turn_id"]
    controller._run_turn(
        session_id=session["session_id"],
        turn_id=turn["turn_id"],
        message=turn["message"],
        attachments=[],
        adapter=CodexAppServerAdapter(host.session),
    )
    done = store.load_turn(session["session_id"], turn["turn_id"])
    assert done["status"] == "failed" and "local owner" in done["error"]
    assert not host.calls
    controller.close()


def test_mode_resume_injects_fresh_context_once_without_replacing_objective(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch, goal=native_goal("paused"),
                events=[begin("one"), end("one"), begin("two"), complete, end("two")])
    received = []
    monkeypatch.setattr(host.session, "steer", lambda text, **kw: received.append((text, kw)))
    host.driver.run(parse_native_goal_command("/goal resume --tokens 2000"), lambda *a: None,
                    execution_context="Fresh scoped facts and complete report requirement")
    assert received == [("Fresh scoped facts and complete report requirement", {"expected_turn_id": "one"})]
    assert host.goal["objective"] == "Original objective"


def test_structured_reports_survive_later_native_blocked_summary(tmp_path, monkeypatch):
    import json
    def answer(message):
        return message + "\n<loopx-review-json>" + json.dumps({"schema_version": "loopx_chat_agent_response_v0", "message": message, "proposals": [], "gate": None}, ensure_ascii=False) + "</loopx-review-json>"
    def block(host):
        host.goal["status"] = "blocked"
        return event("thread/goal/updated", goal=host.goal.copy())
    host = Host(tmp_path, monkeypatch, events=[
        begin("one"), delta(answer("Verified report: normalized FCF 40 → 25; source families 1."), "one"), end("one"),
        begin("two"), block, delta(answer("Report task requires separate owner action."), "two"), end("two"),
    ])
    seen = []
    response = host.driver.run(parse_native_goal_command("/goal start --tokens 10000 Verify"), lambda k, p: seen.append((k, p)))
    assert "normalized FCF 40 → 25" in response["message"]
    assert "separate owner action" in response["message"]
    streamed = "".join(p["text"] for k, p in seen if k == "answer.delta")
    assert "normalized FCF" in streamed and "separate owner action" in streamed


def test_mismatched_adapter_is_typed_rejection_and_recovery_closes_it(tmp_path, monkeypatch):
    store = ChatSessionStore(tmp_path / "runtime")
    controller = ChatRuntimeController(store=store, codex_bin="codex")
    closed = []
    adapter = SimpleNamespace(healthcheck=lambda: True, close_session=lambda: closed.append(True),
                              upstream_thread_id="thread-fixture")
    session = store.create_session(goal_id="fixture", agent_id="codex", adapter_kind="codex_app_server",
                                   upstream_thread_id="thread-fixture", upstream_mode="chat", channel_id="goal.fixture")
    sid = session["session_id"]
    controller.adapters[sid] = adapter
    turn, _ = controller.submit_turn(session_id=sid, client_turn_id="wrong-adapter", message="/goal start --tokens 1000 Analyze",
                                     work_dir=tmp_path, objective="Research")
    result = controller.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=5)
    assert result["error_code"] == "native_goal_adapter_mismatch", result.get("error")
    controller.adapters.clear()
    store.update_session(sid, upstream_mode="chat_native_goal")
    monkeypatch.setattr(controller, "_start_adapter", lambda **kw: adapter)
    with pytest.raises(CodexChatAgentError, match="could not be restored") as caught:
        controller.resume_session(session_id=sid, work_dir=tmp_path, objective="Research")
    assert caught.value.__cause__.error_code == "native_goal_adapter_mismatch"
    assert closed == [True]
