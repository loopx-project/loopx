"""Original worker reviews settled native output; business is never replayed."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from test_antigravity_steward_adapter import language_server as language_server
from test_native_steward_private import steward  # noqa: F401

from loopx.capabilities.native_chat import antigravity_adapter as native
from loopx.capabilities.native_chat import external_conversations
from loopx.extensions.lark.conversation_identity import identity_ref


@pytest.mark.parametrize("status, expected", [
    ("DONE", "completed"), ("ERROR", "failed"), ("RUNNING", None),
    ("GENERATING", None), ("WAITING", None), ("CANCELED", None), ("CLEARED", None),
])
def test_native_code_action_reports_only_its_observed_tool_result(tmp_path, status, expected):
    from loopx.capabilities.native_chat.antigravity_activity import AntigravityActivity

    activity = AntigravityActivity(tmp_path)
    planner = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
        "plannerResponse": {"toolCalls": [{"id": "native-call", "name": "write_file",
            "argumentsJson": "NATIVE_CODE_PRIVATE_SENTINEL"}]}}
    started = activity.observe(1, planner)
    tool_id = started[0]["step"]["id"]
    assert not any(row["method"] == "item/completed" for row in started)
    action = {"type": "CORTEX_STEP_TYPE_CODE_ACTION", "status": "CORTEX_STEP_STATUS_" + status,
        "metadata": {"toolCall": {"id": "native-call", "name": "write_file",
            "argumentsJson": "NATIVE_CODE_PRIVATE_SENTINEL", "thinkingSignature": "NATIVE_CODE_PRIVATE_SENTINEL"}},
        "codeAction": {"actionSpec": {"createFile": {"content": "NATIVE_CODE_PRIVATE_SENTINEL"}},
            "actionResult": {"edit": {"private": "NATIVE_CODE_PRIVATE_SENTINEL"}}}}
    rows = activity.observe(2, action)
    completed = [row for row in rows if row["method"] == "item/completed"]
    if expected:
        assert [(row["step"]["id"], row["step"]["state"]) for row in completed] == [(tool_id, expected)]
        assert activity.observe(2, action) == []
    else:
        assert not completed
    assert "NATIVE_CODE_PRIVATE_SENTINEL" not in json.dumps(started + rows)


@pytest.mark.parametrize("native_id", ["other-call", None, []])
def test_native_code_action_does_not_complete_an_unrelated_planner_call(tmp_path, native_id):
    from loopx.capabilities.native_chat.antigravity_activity import AntigravityActivity

    activity = AntigravityActivity(tmp_path)
    started = activity.observe(1, {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
        "plannerResponse": {"toolCalls": [{"id": "original-call", "name": "write_file"}]}})
    rows = activity.observe(2, {"type": "CORTEX_STEP_TYPE_CODE_ACTION", "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"toolCall": {"id": native_id, "name": "write_file"}}, "codeAction": {"actionResult": {"edit": {}}}})
    assert rows and all(row["step"]["id"] != started[0]["step"]["id"] for row in rows)
    assert any(row["method"] == "item/completed" for row in rows)


def test_native_code_action_ambiguous_call_identity_keeps_both_requests_open(tmp_path):
    from loopx.capabilities.native_chat.antigravity_activity import AntigravityActivity

    activity = AntigravityActivity(tmp_path)
    planner = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "plannerResponse": {"toolCalls": [
        {"id": "duplicate-call", "name": "write_file"}, {"id": "duplicate-call", "name": "other_tool"}]}}
    requested = activity.observe(1, planner)
    assert activity.observe(1, planner) == []
    completed = activity.observe(2, {"type": "CORTEX_STEP_TYPE_CODE_ACTION", "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"toolCall": {"id": "duplicate-call", "name": "write_file"}}, "codeAction": {}})
    ids = {row["step"]["id"] for row in requested}
    assert completed and all(row["step"]["id"] not in ids for row in completed)
    assert completed[-1]["step"]["state"] == "completed"


@pytest.mark.parametrize("tool_status, expected", [("DONE", "completed"), ("ERROR", "failed")])
def test_original_native_code_action_result_survives_later_timeout_without_replay(
    steward, language_server, monkeypatch, tool_status, expected,
):
    from test_antigravity_steward_adapter import _native_clock
    from loopx.extensions.lark.private_conversations import LarkPrivateConversations

    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gemini-4-argon-high")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    store, runtime, provider, transport, binding, _, _ = steward
    binding = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
        executor_endpoint_id="antigravity", context_kind="steward")
    runtime.hard_timeout_sec = None
    user = {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
            "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}}}
    planner = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}},
        "plannerResponse": {"toolCalls": [{"id": "native-call", "name": "write_file"}]}}
    action = {"type": "CORTEX_STEP_TYPE_CODE_ACTION", "status": "CORTEX_STEP_STATUS_RUNNING",
        "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 2}, "toolCall": {"id": "native-call", "name": "write_file"}},
        "codeAction": {"actionSpec": {"createFile": {}}, "actionResult": {"edit": {}}}}
    finished = {**action, "status": "CORTEX_STEP_STATUS_" + tool_status}
    error = {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 3}}, "errorMessage": {"error": {"shortError": "EOF"}}}
    frames = [{"at": at, "steps": steps, "status": "CASCADE_RUN_STATUS_RUNNING"} for at, steps in [
        (1.0, [user, planner, action]), (2.0, [user, planner, finished]), (305.0, [user, planner, finished, error])]]
    _native_clock(monkeypatch, language_server, frames)
    source = provider.event("steward-app", "native-code-action-source", "Complete the original task")
    transport.admit("steward-app", source)
    ref = identity_ref(binding["provider_ref"], source["message_id"])
    row = transport.core.read_request(ref)
    failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=8)
    assert failed["status"] == "failed" and failed["error_code"] == "response_timeout"
    events = store.events_after(row["session_id"], row["turn_id"], None)
    tool_results = [event["payload"]["step"] for event in events if event.get("kind") == "agent.phase"
        and event.get("payload", {}).get("method") == "item/completed"
        and event["payload"].get("step", {}).get("title") == "write_file"]
    assert len(tool_results) == 1 and tool_results[0]["state"] == expected
    from loopx.extensions.lark.private_progress import project_progress
    visible = project_progress({}, events, owner_private=True)
    label = "已结束当前步骤，继续处理" if expected == "completed" else "当前步骤失败，Agent 正在处理"
    assert visible and label in visible
    original = store._turn_path(row["session_id"], row["turn_id"]).read_bytes()
    assert transport.reconcile() == 1
    terminal = json.loads((transport.root / f"{ref}.json").read_text())["deliveries"]["terminal"]
    assert terminal["verified"] and "没有生成完整答复" in terminal["text"] and "不会自动重放" in terminal["text"]
    assert provider.messages[terminal["attempt"]["message_ref"]]["chat_id"] == source["chat_id"]
    restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
        runner=provider, cli_bin="lark-cli")
    writes = list(provider.writes)
    assert restarted.reconcile() == 0 and provider.writes == writes
    assert store._turn_path(row["session_id"], row["turn_id"]).read_bytes() == original
    assert [method for method, _ in language_server["calls"]].count("SendUserCascadeMessage") == 1


@pytest.mark.parametrize("hard_limit, native_error", [(None, True), (150.0, True), (None, False)])
def test_native_timeout_reports_its_clock_without_extending_native_retry(
    tmp_path, language_server, monkeypatch, hard_limit, native_error,
):
    from test_antigravity_steward_adapter import _native_clock, adapter_for
    from loopx.chat_agent import CodexChatAgentError

    user = {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
            "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}}}
    steps, frames = [user], []
    for idx, at in enumerate((1.0, 120.0, 240.0, 305.0), 1):
        if native_error:
            steps = [*steps, {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "status": "CORTEX_STEP_STATUS_DONE",
                "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": idx}},
                "errorMessage": {"error": {"shortError": f"EOF {idx} NATIVE_PRIVATE_SENTINEL /private/host"}}}]
        frames.append({"at": at, "steps": steps, "status": "CASCADE_RUN_STATUS_RUNNING"})
    _native_clock(monkeypatch, language_server, frames)
    events = []
    with pytest.raises(CodexChatAgentError) as raised:
        adapter_for(tmp_path, hard_timeout_sec=hard_limit).start_turn(
            "Original task", lambda kind, data: events.append((kind, data)))
    assert raised.value.error_code == "response_timeout"
    diagnostic = next(data["timeout"] for kind, data in events
        if kind == "agent.phase" and data.get("method") == "native/timeout")
    assert diagnostic["kind"] == ("hard" if hard_limit is not None else "idle")
    assert diagnostic["limit_seconds"] == (hard_limit or 300.0)
    assert diagnostic["native_error_count"] == ((3 if hard_limit else 4) if native_error else 0)
    assert raised.value.native_error_info == {"timeout": diagnostic}
    assert language_server["now"] == (240.0 if hard_limit else 305.0)
    assert language_server["cancelled"].is_set()
    assert [method for method, _ in language_server["calls"]].count("SendUserCascadeMessage") == 1
    assert not any(kind in {"answer.final", "turn.native_result"} for kind, _ in events)
    serialized = json.dumps(events)
    assert "NATIVE_PRIVATE_SENTINEL" not in serialized and "/private/host" not in serialized
    errors = [data for kind, data in events if data.get("method") == "native/error"]
    assert len(errors) == diagnostic["native_error_count"]


@pytest.mark.parametrize("kind, expected", [
    ("idle", "等待原生助手有效进展超时"), ("hard", "原生助手达到本次执行时限"),
    ("unknown-host-value", "等待助手响应超时"), (None, "等待助手响应超时"),
])
def test_original_lark_timeout_keeps_typed_reason_and_never_replays(
    steward, language_server, monkeypatch, kind, expected,
):
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    from loopx.chat_agent import CodexChatAgentError
    from loopx.extensions.lark.private_conversations import LarkPrivateConversations

    starts = []

    def fail_after_observed_progress(_adapter, message, event_sink):
        starts.append(message)
        event_sink("turn.started", {"upstream_turn_id": "synthetic-timeout"})
        event_sink("agent.phase", {"method": "native/timeout", "label": "private label",
            "timeout": {"kind": kind, "native_error_count": 4, "raw": "NATIVE_PRIVATE_SENTINEL"}})
        raise CodexChatAgentError("Private host diagnostic NATIVE_PRIVATE_SENTINEL", gate=None,
                                  error_code="response_timeout")

    monkeypatch.setattr(CodexAppServerAdapter, "start_turn", fail_after_observed_progress)
    store, runtime, provider, transport, binding, _, _ = steward
    native_route = kind in {"idle", "hard"}
    if native_route:
        from test_antigravity_steward_adapter import _native_clock, _native_frames
        monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gemini-4-argon-high")
        monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
        binding = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="antigravity", context_kind="steward")
        runtime.hard_timeout_sec = 150.0 if kind == "hard" else None
        frames = _native_frames(stagnant=True)
        for index, frame in enumerate(frames):
            frame["steps"] += [{"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "status": "CORTEX_STEP_STATUS_DONE",
                "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 2 + attempt}},
                "errorMessage": {"error": {"shortError": "EOF NATIVE_PRIVATE_SENTINEL"}}}
                for attempt in range(index)]
        _native_clock(monkeypatch, language_server, frames)
    source = provider.event("steward-app", "native-timeout-source", "Complete this original request")
    transport.admit("steward-app", source)
    ref = identity_ref(binding["provider_ref"], source["message_id"])
    row = transport.core.read_request(ref)
    failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=8)
    assert failed["status"] == "failed" and failed["error_code"] == "response_timeout"
    original = store._turn_path(row["session_id"], row["turn_id"]).read_bytes()
    assert transport.reconcile() == 1
    saved = json.loads((transport.root / f"{ref}.json").read_text())
    terminal = saved["deliveries"]["terminal"]
    assert terminal["verified"] and expected in terminal["text"]
    assert ("期间原生助手记录了调用错误" in terminal["text"]) is (kind in {"idle", "hard"})
    assert "不会自动重放" in terminal["text"]
    assert "NATIVE_PRIVATE_SENTINEL" not in terminal["text"] and "private label" not in terminal["text"]
    returned = provider.messages[terminal["attempt"]["message_ref"]]
    assert returned["chat_id"] == source["chat_id"]
    assert expected in json.dumps(returned["body"], ensure_ascii=False)
    restarted = LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
        runner=provider, cli_bin="lark-cli")
    before = list(provider.writes)
    assert restarted.reconcile() == 0
    assert restarted.health()[row["binding_id"]] == {"pending_count": 1, "recovery_count": 0}
    assert (not starts and [method for method, _ in language_server["calls"]].count("SendUserCascadeMessage") == 1
            if native_route else len(starts) == 1)
    assert provider.writes == before
    assert store._turn_path(row["session_id"], row["turn_id"]).read_bytes() == original


@pytest.mark.parametrize("info", [None, [], {"kind": []}, {"kind": {}}, {"kind": "idle", "native_error_count": True}])
def test_timeout_presentation_does_not_trust_unknown_diagnostic_text(info):
    from loopx.extensions.lark.manager_context import manager_failure_reply
    code, text = manager_failure_reply({"error_code": "response_timeout", "error": "NATIVE_PRIVATE_SENTINEL"},
        phase_events=[{"kind": "agent.phase", "payload": {"method": "native/timeout", "timeout": info}}])
    assert code == "processing_failed" and "超时" in text and "不会自动重放" in text
    assert "NATIVE_PRIVATE_SENTINEL" not in text and "期间原生助手记录了调用错误" not in text


@pytest.mark.parametrize("source_code", ["response_timeout", "antigravity_step_error", "unknown"])
def test_native_error_labels_preserve_shared_admission_failure_category(source_code):
    from loopx.extensions.lark.manager_context import manager_failure_reply
    code, _ = manager_failure_reply({"error_code": source_code})
    assert code == "processing_failed"


@pytest.mark.parametrize("fault, expected", [
    ("done", True), ("error", True), ("canceled", True), ("cleared", True),
    ("running", False), ("generating", False), ("waiting", False),
    ("missing-status", False), ("visible-error", False),
    ("mixed-execution", False), ("final-error", False), ("missing-stop", False),
])
def test_capacity_readback_uses_the_completed_native_turn_boundary(language_server, fault, expected):
    """Handled tool failures do not hide quota; unknown/active work stays held."""
    meta = {"createdAt": "2026-01-01T00:00:05Z", "executionId": "completed-execution"}
    steps = [
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta},
        {"type": "CORTEX_STEP_TYPE_VIEW_FILE", "status": "CORTEX_STEP_STATUS_" + fault.upper(), "metadata": meta},
        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
         "plannerResponse": {"response": "Completed after the tool attempt.", "stopReason": "STOP_REASON_STOP_PATTERN"}},
    ]
    if fault == "missing-status":
        steps[1].pop("status")
    elif fault == "visible-error":
        steps[1].update(type="CORTEX_STEP_TYPE_ERROR_MESSAGE", status="CORTEX_STEP_STATUS_DONE",
                        errorMessage={"shouldShowUser": True})
    elif fault == "mixed-execution":
        steps[1].update(status="CORTEX_STEP_STATUS_DONE", metadata={**meta, "executionId": "another-execution"})
    elif fault == "final-error":
        steps[1]["status"] = steps[2]["status"] = "CORTEX_STEP_STATUS_ERROR"
    elif fault == "missing-stop":
        steps[1]["status"] = "CORTEX_STEP_STATUS_DONE"
        steps[2]["plannerResponse"].pop("stopReason")
    language_server.update(active=True, frames=[], frame={"at": 1, "steps": steps, "status": "CASCADE_RUN_STATUS_IDLE"})
    capacity = native.AntigravityAdapter.metadata_review_capacity(cascade_id="synthetic-cascade", model_id="gemini-4-argon-high")
    if expected:
        assert capacity and capacity["normal_completion"] is True and capacity["remaining_fraction"] == 1
        assert capacity["quota_exhausted"] is False
    else:
        assert capacity is None
    assert {method for method, _ in language_server["calls"]} <= {
        "GetCascadeModelConfigData", "GetCascadeTrajectory", "GetCascadeTrajectorySteps", "GetUserStatus"}
    assert language_server["sent_count"] == 0 and not language_server["cancelled"].is_set()


@pytest.mark.parametrize("prior_status, error_status, cancellation_needed", [
    ("CORTEX_STEP_STATUS_DONE", "CORTEX_STEP_STATUS_DONE", False),
    ("CORTEX_STEP_STATUS_ERROR", "CORTEX_STEP_STATUS_DONE", False),
    ("CORTEX_STEP_STATUS_CANCELED", "CORTEX_STEP_STATUS_DONE", False),
    ("CORTEX_STEP_STATUS_CLEARED", "CORTEX_STEP_STATUS_DONE", False),
    ("CORTEX_STEP_STATUS_RUNNING", "CORTEX_STEP_STATUS_DONE", True),
    ("CORTEX_STEP_STATUS_GENERATING", "CORTEX_STEP_STATUS_DONE", True),
    ("CORTEX_STEP_STATUS_WAITING", "CORTEX_STEP_STATUS_DONE", True),
    (None, "CORTEX_STEP_STATUS_DONE", True),
    ("CORTEX_STEP_STATUS_DONE", "CORTEX_STEP_STATUS_WAITING", True),
    ("CORTEX_STEP_STATUS_DONE", None, True),
])
def test_idle_native_error_keeps_only_unsettled_work_cancellable(
    tmp_path, language_server, monkeypatch, prior_status, error_status, cancellation_needed,
):
    from test_antigravity_steward_adapter import _native_clock, _native_frames, adapter_for
    from loopx.chat_agent import CodexChatAgentError

    frame = _native_frames()[-1]
    detail = {"shortError": "stream interrupted", "userErrorMessage": "Network interrupted", "errorCode": 2}
    frame["steps"] = [frame["steps"][0],
        {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": prior_status,
         "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}},
         "runCommand": {"commandLine": "synthetic inspection", "exitCode": 0}},
        {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "status": error_status,
         "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 2}},
         "errorMessage": {"error": detail}},
    ]
    _native_clock(monkeypatch, language_server, [frame])
    language_server["cancel_failure"] = True
    adapter = adapter_for(tmp_path)
    events = []
    with pytest.raises(CodexChatAgentError) as raised:
        adapter.start_turn("Inspect existing state", lambda kind, data: events.append((kind, data)))

    error = raised.value
    waiting_for_permission = "CORTEX_STEP_STATUS_WAITING" in {prior_status, error_status}
    if waiting_for_permission:
        assert error.error_code == "host_tool_gate" and error.gate is not None
    else:
        assert error.error_code == "antigravity_step_error" and error.native_error_info == detail
        assert str(error).startswith("Network interrupted")
    methods = [method for method, _ in language_server["calls"]]
    assert methods.count("SendUserCascadeMessage") == 1
    assert methods.count("CancelCascadeInvocation") == int(cancellation_needed)
    assert ("cancellation remains unconfirmed" in str(error)) is cancellation_needed
    assert not any(kind in {"turn.native_result", "answer.final"} for kind, _ in events)


def test_settled_native_error_releases_core_for_independent_input(tmp_path, language_server):
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore

    store = ChatSessionStore(tmp_path / "runtime")
    controller = ChatRuntimeController(store=store, codex_bin="unused-codex")
    session, _ = controller.open_session(goal_id="synthetic-goal", agent_id="antigravity",
        work_dir=tmp_path, objective="Synthetic conversation", mode="new", channel_id="synthetic-argon-chat")
    sid = session["session_id"]
    language_server.update(quota_error_indices=[0], cancel_failure=True)
    try:
        original, _ = controller.submit_turn(session_id=sid, client_turn_id="original-failure",
            message="Original request", work_dir=tmp_path, objective="Synthetic conversation")
        failed = controller.wait_for_turn(session_id=sid, turn_id=original["turn_id"], timeout_sec=8)
        assert failed["status"] == "failed" and failed["error_code"] == "antigravity_step_error"
        assert "cancellation remains unconfirmed" not in failed["error"]
        assert store.load_session(sid)["active_turn_id"] is None
        retained = store._turn_path(sid, original["turn_id"]).read_bytes()

        language_server["quota_error_indices"] = []
        later, _ = controller.submit_turn(session_id=sid, client_turn_id="later-independent",
            message="A separate question", work_dir=tmp_path, objective="Synthetic conversation")
        completed = controller.wait_for_turn(session_id=sid, turn_id=later["turn_id"], timeout_sec=8)
        assert completed["status"] == "completed" and completed["response"]["message"] == "Argon answer 2"
        assert store._turn_path(sid, original["turn_id"]).read_bytes() == retained
        assert [m["text"] for m in store.messages(sid) if m["role"] == "user"] == [
            "Original request", "A separate question"]
        methods = [method for method, _ in language_server["calls"]]
        assert methods.count("SendUserCascadeMessage") == 2
        assert "CancelCascadeInvocation" not in methods
        assert not any(e["kind"] == "answer.final" for e in store.events_after(sid, original["turn_id"], None))
    finally:
        controller.close_session(sid)


@pytest.mark.parametrize("fault", [None, "stop", "source-changed", "unknown-native", "no-capacity", "native-changed",
    "review-action", "post-enqueue-crash", "image-view", "checkpoint-live", "checkpoint-cold",
    "checkpoint-before-review", "checkpoint-before-review-account-change",
    "checkpoint-injection-unknown", "checkpoint-empty", "checkpoint-settings-change"])
def test_restarted_codex_steward_gets_one_original_read_only_answer(steward, monkeypatch, fault):
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    from loopx.chat_agent import CodexChatAgentError
    from loopx.extensions.lark.private_conversations import LarkPrivateConversations
    from test_chat_manager_model_adoption import configure

    store, runtime, provider, transport, binding, capture, workspace = steward
    fake = Path(runtime.codex_bin)
    checkpoint = bool(fault and fault.startswith("checkpoint-"))
    cold_before_review = fault in {"checkpoint-before-review", "checkpoint-before-review-account-change"}
    if cold_before_review:
        from loopx.capabilities.native_chat import codex_auth
        login_epoch = ["original-login"]
        monkeypatch.setattr(codex_auth, "conversation_account_epoch",
                            lambda *_args, **_kwargs: (login_epoch[0], "subscription"))
        configure(runtime.coordination_runtime_root, model=None, effort=None, endpoint="antigravity", policy="flexible")
    original_thread = "working-thread" if checkpoint else "durable-thread"
    code = fake.read_text().replace('result = {"thread": {"id": "durable-thread"}}', '''result = {"thread": {"id": "durable-thread"}, "model": "gpt-6.1-sol"}
        permissions = request.get("params", {}).get("permissions")
        if permissions:
            result.update(activePermissionProfile={"id": permissions}, runtimeWorkspaceRoots=[request["params"]["cwd"]])''')
    if checkpoint:
        code = code.replace('"durable-thread"', 'thread_id')
        code = code.replace('active_turn = None', 'active_turn = None\nthread_id = "working-thread"', 1)
        begin = code.index('    elif method in {"thread/start", "thread/resume"}:')
        end = code.index('    elif method == "thread/goal/get":', begin)
        code = code[:begin] + '''    elif method in {"thread/start", "thread/resume", "thread/fork"}:
        params = request["params"]
        permissions = params.get("permissions")
        thread_id = ("continued-thread" if method == "thread/fork" else params.get("threadId")
            if method == "thread/resume" else "review-thread" if permissions else "working-thread")
        result = {"thread": {"id": thread_id}, "model": "gpt-6.1-sol", "modelProvider": "openai",
                  "cwd": params["cwd"], "approvalPolicy": "never"}
        if method == "thread/fork":
            result["thread"]["forkedFromId"] = params["threadId"]
            result["sandbox"] = {"type": {"danger-full-access": "dangerFullAccess",
                "workspace-write": "workspaceWrite", "read-only": "readOnly"}[params["sandbox"]]}
        if permissions:
            result.update(activePermissionProfile={"id": permissions}, runtimeWorkspaceRoots=[params["cwd"]])
''' + code[end:]
        if fault == "checkpoint-injection-unknown":
            code = code.replace('    elif method == "turn/start":', '''    elif method == "thread/inject_items" and thread_id == "continued-thread":
        sys.exit(2)
    elif method == "turn/start":''')
    if fault in {"review-action", "checkpoint-empty"}:
        start = code.index("        response = 'Visible runtime response.")
        end = code.index('        print(json.dumps({"method": "item/agentMessage/delta"', start)
        reply = '<loopx-review-json>' + json.dumps({"message": "Forbidden repeat",
            "unfinished_work": {"remaining": "Repeat business", "dependency": "owner", "effects_settled": True}}) + '</loopx-review-json>'
        code = code[:start] + f"        response = {reply!r}\n" + code[end:]
    fake.write_text(code)
    artifact = workspace / "original-output.txt"
    start_turn = CodexAppServerAdapter.start_turn

    def restart_after_output(adapter, message, sink, **kwargs):
        if adapter.session.answer_review:
            return start_turn(adapter, message, sink, **kwargs)
        def observed(kind, payload):
            sink(kind, payload)
            if kind == "answer.delta":
                artifact.write_text("Original effect, do not repeat")
                raise CodexChatAgentError("Synthetic service loss after output", error_code="server_restarted", gate=None)
        return start_turn(adapter, message, observed, **kwargs)

    monkeypatch.setattr(CodexAppServerAdapter, "start_turn", restart_after_output)
    event = provider.event("steward-app", "restart-original-question", "Explain the existing change")
    transport.admit("steward-app", event)
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    original = transport.core.read_request(ref)
    failed = runtime.wait_for_turn(session_id=original["session_id"], turn_id=original["turn_id"], timeout_sec=15)
    assert failed["status"] == "failed" and failed["error_code"] == "server_restarted" and failed["delta_count"] > 0
    path = transport.root / f"{ref}.json"
    transport.reconcile_request(path)
    failure_bytes = store._turn_path(original["session_id"], original["turn_id"]).read_bytes()
    notice = deepcopy(json.loads(path.read_text())["deliveries"])
    history_marker = "Unrelated retained conversation. " * 5000
    if fault is None:
        store.append_message(original["session_id"], role="agent", text=history_marker,
                             turn_id="unrelated-historical-turn")
    start_adapter = runtime._start_adapter
    def start_scoped_review(**kwargs):
        if kwargs.get("answer_review_context") is not None:
            assert kwargs["history"] is None, "a restricted answer must not import the whole conversation"
        if fault == "checkpoint-settings-change" and kwargs.get("working_continuation") is not None:
            store.update_session(original["session_id"], requested_agent_model_id="gpt-6-luna")
        return start_adapter(**kwargs)
    monkeypatch.setattr(runtime, "_start_adapter", start_scoped_review)
    class Ahead(datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz) + timedelta(seconds=600)
    monkeypatch.setattr(external_conversations, "datetime", Ahead)
    import loopx.chat_manager as manager_module
    import loopx.chat_runtime as runtime_module
    if not cold_before_review:
        configure(runtime.coordination_runtime_root, model=None, effort=None, endpoint="antigravity", policy="flexible")
    allocation = manager_module.manager_executor_allocation(runtime,
        endpoint_capabilities=[{"agent_id": "codex", "available": True}, {"agent_id": "antigravity", "available": False}])
    def route(*_args, **_kwargs):
        return None if fault == "no-capacity" else {"allocation": allocation, "blocked_primary": None,
            "observed_at": time.time()}
    monkeypatch.setattr(manager_module, "manager_session_quota_route", lambda *_args, **_kwargs:
        None if fault == "no-capacity" else {"allocation": allocation, "blocked_primary": None,
            "observed_at": Ahead.now().timestamp()})
    monkeypatch.setattr(runtime_module, "manager_session_quota_route", route)
    probes = []
    def observe(**kwargs):
        assert kwargs.get("read_only_review") is True
        probes.append(kwargs)
        if fault == "unknown-native":
            return None
        if fault == "image-view":
            from loopx.capabilities.native_chat.codex_adapter import interrupted_result_readback
            def items(method, params, _deadline):
                if method == "thread/turns/list":
                    return iter([{"id": failed["upstream_turn_id"], "status": "interrupted"}])
                assert method == "thread/items/list" and params == {"turnId": failed["upstream_turn_id"]}
                return iter([{"turnId": failed["upstream_turn_id"], "item": item} for item in [
                    {"id": "original-user", "type": "userMessage"},
                    {"id": "original-command", "type": "commandExecution", "status": "completed"},
                    {"id": "original-image", "type": "imageView", "path": str(artifact)}]])
            return interrupted_result_readback(SimpleNamespace(thread_id="durable-thread",
                response_timeout_sec=1, _control_items=items), failed["upstream_turn_id"])
        changed = fault == "native-changed" and bool((transport.core.read_request(ref).get("context_recovery") or {}).get("answer_review"))
        return {"thread_id": original_thread, "turn_id": failed["upstream_turn_id"],
            "read_only_review": True, "no_final_answer": True, "effects_observed": True,
            "native_inventory_sha256": ("2" if changed else "1") * 64}
    monkeypatch.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(observe))
    if fault == "stop":
        store.append_event(original["session_id"], original["turn_id"], kind="turn.interrupt_requested", payload={})
    elif fault == "source-changed":
        provider.messages[event["message_id"]]["content"] = "Revised original source"
    restarted = LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
        runner=provider, cli_bin="lark-cli")
    review_trace_start = 0
    if cold_before_review:
        runtime.adapters.pop(original["session_id"]).close_session()
        review_trace_start = len(capture.read_text().splitlines())
        if fault == "checkpoint-before-review-account-change":
            login_epoch[0] = "different-login"
    if fault == "post-enqueue-crash":
        enqueue = runtime.enqueue_turn
        def crash(**kwargs):
            enqueue(**kwargs)
            raise OSError("synthetic unknown admission response")
        monkeypatch.setattr(runtime, "enqueue_turn", crash)
        with pytest.raises(OSError, match="unknown admission response"):
            restarted.reconcile_request(path)
        monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
    restarted.reconcile_request(path)
    current = restarted.core.read_request(ref)
    if fault in {"stop", "source-changed", "unknown-native", "no-capacity"}:
        assert current["turn_id"] == original["turn_id"] and not current.get("context_recovery")
    else:
        recovery = current["context_recovery"]
        assert recovery["answer_review"] is True and recovery["read_only_review"] is True
        assert not any(recovery.get(key) for key in ["work_attempt", "quota_attempt", "routing_attempt"])
        done = runtime.wait_for_turn(session_id=current["session_id"], turn_id=current["turn_id"], timeout_sec=15)
        assert done["status"] == ("failed" if fault in {"native-changed", "review-action", "checkpoint-empty"} else "completed"), done
        restarted.reconcile_request(path)
        delivered = json.loads(path.read_text())
        terminal = delivered["deliveries"]["terminal.recovery.22.review.1"]
        assert terminal["verified"]
        actual = provider.messages[terminal["attempt"]["message_ref"]]
        assert actual["chat_id"] == event["chat_id"] and actual["sender"]["id"] == provider.profile_apps["steward-app"]
        assert json.loads(actual["body"]["content"])["zh_cn"]["content"][0][0]["text"] == terminal["text"]
        assert delivered["deliveries"]["terminal"] == notice["terminal"]
        calls = [json.loads(line) for line in capture.read_text().splitlines()]
        review_threads = [c for c in calls if c["method"] == "thread/start" and c["params"].get("permissions")]
        assert len(review_threads) == 1
        opened = review_threads[0]["params"]
        assert opened["config"]["features"]["apply_patch_freeform"] is False
        assert opened["config"]["features"]["apps"] is False
        assert opened["config"]["permissions"][opened["permissions"]]["network"]["enabled"] is False
        assert [t["name"] for t in opened["dynamicTools"]] == ["loopx_manager_read"]
        review_calls = calls[review_trace_start:]
        assert not any(c["method"] == "thread/resume" for c in review_calls), "unknown old business must not be resumed"
        assert not any(c["method"] in {"thread/inject_items", "thread/compact/start"} for c in review_calls)
        if fault is None:
            assert any(item["text"] == history_marker for item in store.messages(original["session_id"]))
            review_inputs = [c["params"] for c in calls if c["method"] == "turn/start"]
            assert original["message"] in json.dumps(review_inputs, ensure_ascii=False)
        writes = list(provider.writes)
        restarted.reconcile_request(path)
        assert provider.writes == writes
        if fault == "checkpoint-before-review-account-change":
            assert store.load_session(original["session_id"]).get("parked_native_working") is None
        elif checkpoint:
            sid = original["session_id"]
            review_session = store.load_session(sid)
            parked = review_session["parked_native_working"]
            assert parked["thread_id"] == "working-thread" and parked["review_turn_ids"] == [done["turn_id"]]
            assert review_session["upstream_thread_id"] == "review-thread"
            if fault == "checkpoint-cold":
                runtime.adapters.pop(sid).close_session()
                assert store.load_session(sid)["parked_native_working"] == parked
            monkeypatch.setattr(CodexAppServerAdapter, "start_turn", start_turn)
            later = provider.event("steward-app", "after-working-checkpoint", "Next independent question")
            restarted.admit("steward-app", later)
            next_row = restarted.core.read_request(identity_ref(binding["provider_ref"], later["message_id"]))
            next_turn = runtime.wait_for_turn(session_id=sid, turn_id=next_row["turn_id"], timeout_sec=15)
            packets = [json.loads(line) for line in capture.read_text().splitlines()][len(calls):]
            methods = [packet["method"] for packet in packets]
            assert "thread/start" not in methods and "thread/compact/start" not in methods
            if fault == "checkpoint-settings-change":
                assert next_turn["status"] == "failed"
                assert not {"thread/fork", "thread/resume", "thread/inject_items", "turn/start"}.intersection(methods)
                assert store.load_session(sid)["parked_native_working"] == parked
                assert store.load_session(sid)["upstream_thread_id"] == "review-thread"
            elif fault == "checkpoint-empty":
                assert next_turn["status"] == "completed"
                assert [p["params"]["threadId"] for p in packets if p["method"] == "thread/resume"] == ["working-thread"]
                assert "thread/inject_items" not in methods
            else:
                assert [p["params"]["threadId"] for p in packets if p["method"] == "thread/fork"] == ["working-thread"]
                injected = [p["params"] for p in packets if p["method"] == "thread/inject_items"]
                assert len(injected) == 1 and injected[0]["threadId"] == "continued-thread"
                suffix = [(item["role"], item["content"][0]["text"]) for item in injected[0]["items"]]
                assert suffix == [("assistant", done["response"]["message"])]
                assert "Next independent question" not in str(suffix)
                if fault == "checkpoint-injection-unknown":
                    assert next_turn["status"] == "failed" and "turn/start" not in methods
                    assert store.load_session(sid)["parked_native_working"] == parked
                    assert store.load_session(sid)["upstream_thread_id"] == "review-thread"
                else:
                    assert next_turn["status"] == "completed"
                    assert store.load_session(sid)["upstream_thread_id"] == "continued-thread"
            if fault not in {"checkpoint-injection-unknown", "checkpoint-settings-change"}:
                assert store.load_session(sid).get("parked_native_working") is None
    assert store._turn_path(original["session_id"], original["turn_id"]).read_bytes() == failure_bytes
    assert artifact.read_text() == "Original effect, do not repeat"


@pytest.mark.parametrize("fault", [None, "stop", "source-changed", "revoked", "unknown-native", "native-changed", "no-capacity", "review-gate", "review-action", "post-enqueue-crash"])
def test_exhausted_quota_gets_one_restricted_original_answer(steward, monkeypatch, fault):
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    store, runtime, provider, transport, binding, capture, workspace = steward
    fake = Path(runtime.codex_bin)
    code = fake.read_text().replace('"codexErrorInfo": "cyberPolicy"', '"codexErrorInfo": "usageLimitExceeded"')
    code = code.replace('if "typed terminal failure" in json.dumps(request.get("params") or {}):',
        'if "typed terminal failure" in json.dumps(request.get("params") or {}) and "Core admitted ONE" not in json.dumps(request.get("params") or {}):')
    code = code.replace('            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', '')
    code = code.replace('result = {"thread": {"id": "durable-thread"}}', '''result = {"thread": {"id": "durable-thread"}, "model": "gpt-6.1-sol"}
        permissions = request.get("params", {}).get("permissions")
        if permissions:
            result.update(activePermissionProfile={"id": permissions}, runtimeWorkspaceRoots=[request["params"]["cwd"]])''')
    if fault in {"review-gate", "review-action"}:
        response = {"message": "Existing evidence provides a useful partial answer.", "proposals": [],
            "gate": {"kind": "host_tool_gate", "summary": "Missing original evidence", "next_action": "Retain the original obligation"}}
        if fault == "review-action":
            response["gate"] = None
            response["unfinished_work"] = {"remaining": "Forbidden business retry", "dependency": "original owner", "effects_settled": True}
        start = code.index("        response = 'Visible runtime response.")
        end = code.index('        print(json.dumps({"method": "item/agentMessage/delta"', start)
        code = code[:start] + "        response = " + repr('<loopx-review-json>' + json.dumps(response) + '</loopx-review-json>') + "\n" + code[end:]
    fake.write_text(code)
    event = provider.event("steward-app", "exhausted-original-question", "typed terminal failure: Explain this change")
    transport.admit("steward-app", event)
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    path = transport.root / f"{ref}.json"
    row = transport.core.read_request(ref)
    failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=15)
    assert failed["status"] == "failed" and failed["delta_count"] == 0
    transport.reconcile_request(path)
    class Ahead(datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz) + timedelta(seconds=600)
    monkeypatch.setattr(external_conversations, "datetime", Ahead)
    def observation(**kwargs):
        return {"thread_id": "durable-thread", "turn_id": kwargs["turn_id"],
            "only_original_user_item": fault != "unknown-native" and not (fault == "native-changed"
                and (transport.core.read_request(ref).get("context_recovery") or {}).get("answer_review")),
            "codex_error_info": "usageLimitExceeded",
            "quota_available": fault != "no-capacity", "quota_observed_at": Ahead.now().timestamp()}
    monkeypatch.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(observation))
    failures = []
    for attempt in range(1, 4):
        # Build the exhaustion through the real original worker, not by resetting
        # or manufacturing an already exhausted journal reservation.
        with monkeypatch.context() as allowed:
            allowed.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(lambda **kwargs: {
                **observation(**kwargs), "only_original_user_item": True, "quota_available": True}))
            transport.reconcile_request(path)
            row = transport.core.read_request(ref)
            assert row["context_recovery"]["quota_attempt"] == attempt
            failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=15)
            assert failed["status"] == "failed" and failed["delta_count"] == 0
            failures.append((store._turn_path(row["session_id"], row["turn_id"]),
                store._turn_path(row["session_id"], row["turn_id"]).read_bytes()))
            transport.reconcile_request(path)
    exhausted = transport.core.read_request(ref)
    notice = deepcopy(json.loads(path.read_text())["deliveries"])
    import loopx.chat_manager as manager_module
    import loopx.chat_runtime as runtime_module
    from test_chat_manager_model_adoption import configure
    configure(runtime.coordination_runtime_root, model=None, effort=None, endpoint="antigravity", policy="flexible")
    allocation = manager_module.manager_executor_allocation(runtime,
        endpoint_capabilities=[{"agent_id": "codex", "available": True}, {"agent_id": "antigravity", "available": False}])
    def route(*_args, **_kwargs):
        return None if fault == "no-capacity" else {"allocation": allocation, "blocked_primary": None,
            "observed_at": Ahead.now().timestamp()}
    monkeypatch.setattr(manager_module, "manager_session_quota_route", route)
    # The adapter observes wall time, while admission above uses the fake clock.
    monkeypatch.setattr(runtime_module, "manager_session_quota_route", lambda *_args, **_kwargs:
        None if fault == "no-capacity" else {"allocation": allocation, "blocked_primary": None, "observed_at": time.time()})
    if fault == "stop":
        store.append_event(row["session_id"], row["turn_id"], kind="turn.interrupt_requested", payload={})
    elif fault == "source-changed":
        provider.messages[event["message_id"]]["content"] = "Revised original source"
    elif fault == "revoked":
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    elif fault == "post-enqueue-crash":
        enqueue = runtime.enqueue_turn
        def crash(**kwargs):
            enqueue(**kwargs)
            raise OSError("unknown enqueue response")
        monkeypatch.setattr(runtime, "enqueue_turn", crash)
        with pytest.raises(OSError, match="unknown enqueue response"):
            transport.reconcile_request(path)
        monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
    transport.reconcile_request(path)
    recovered = transport.core.read_request(ref)
    if fault in {"stop", "source-changed", "revoked", "unknown-native", "no-capacity"}:
        assert recovered["turn_id"] == exhausted["turn_id"]
        assert recovered["context_recovery"]["quota_attempt"] == 3
    else:
        assert recovered["context_recovery"]["answer_review"] is True
        assert recovered["context_recovery_history"][-1] == exhausted["context_recovery"]
        done = runtime.wait_for_turn(session_id=row["session_id"], turn_id=recovered["turn_id"], timeout_sec=15)
        assert done["status"] == ("failed" if fault in {"review-action", "native-changed"} else "completed")
        transport.reconcile_request(path)
        assert transport.core.read_request(ref)["delivery_verified"]
        assert transport.health()[binding["binding_id"]]["pending_count"] == int(fault in {"review-gate", "review-action", "native-changed"})
        packets = [json.loads(line) for line in capture.read_text().splitlines()]
        opens = [p["params"] for p in packets if p.get("method") == "thread/start" and p["params"].get("permissions")]
        assert len(opens) == 1
        config = opens[0]["config"]
        assert config["features"]["apply_patch_freeform"] is False and config["features"]["apps"] is False
        assert config["permissions"][opens[0]["permissions"]]["network"]["enabled"] is False
        assert [t["name"] for t in opens[0]["dynamicTools"]] == ["loopx_manager_read"]
        before = capture.read_bytes()
        transport.reconcile_request(path)
        assert capture.read_bytes() == before
        assert all(json.loads(path.read_text())["deliveries"][key] == value for key, value in notice.items())
    # The original failed business Turns and third-attempt counter survive.
    assert all(p.read_bytes() == content for p, content in failures)


@pytest.mark.parametrize("fault", [None, "terminal-without-execution", "foreign-terminal", "busy", "changed", "truncated", "wrong-hash", "async", "unknown", "final", "reasoning",
    "file-read", "file-only", "file-running", "file-foreign", "file-changed", "file-remote", "file-missing-content",
    "file-missing-uri", "file-unknown-field", "file-oversize"])
def test_interrupted_native_inventory_is_exact_stable_and_contains_no_reasoning(monkeypatch, fault):
    # The real matcher is exercised in the original-worker integration below;
    # this bounded native inventory fixture isolates index/effect/read stability.
    assert callable(native.manager_input_matches)
    prompt = 'original synthetic input'
    meta = {"createdAt": "2026-01-01T00:00:05Z", "executionId": "original"}
    steps = [
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
         "userInput": {"items": [{"text": prompt}]}},
        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
         "plannerResponse": {"thinking": "private reasoning must not be returned", "toolCalls": []}},
        {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
         "runCommand": {"commandLine": "read existing result", "combinedOutput": "already observed", "blocking": True, "exitCode": 0}},
        {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
         "errorMessage": {"error": {"errorCode": 429}}},
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
         "userInput": {"items": [{"text": "later independent question"}]}}]
    if isinstance(fault, str) and fault.startswith("file-"):
        view = {"type": "CORTEX_STEP_TYPE_VIEW_FILE", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
            "viewFile": {"absolutePathUri": "file:///authorized/existing-result.txt", "content": "Original visible file result"}}
        steps.insert(3, view)
        if fault == "file-only":
            steps.pop(2)
        elif fault == "file-running":
            view["status"] = "CORTEX_STEP_STATUS_RUNNING"
        elif fault == "file-foreign":
            view["metadata"] = {**meta, "executionId": "another-execution"}
        elif fault == "file-remote":
            view["viewFile"]["absolutePathUri"] = "https://untrusted.invalid/result.txt"
        elif fault == "file-missing-content":
            view["viewFile"].pop("content")
        elif fault == "file-missing-uri":
            view["viewFile"].pop("absolutePathUri")
        elif fault == "file-unknown-field":
            view["newEffect"] = {}
        elif fault == "file-oversize":
            view["viewFile"]["content"] = "x" * (256 * 1024)
    if fault == "async":
        steps[2]["runCommand"]["blocking"] = False
    elif fault == "unknown":
        steps[2]["type"] = "FUTURE_NATIVE_OPERATION"
    elif fault == "final":
        steps[1]["plannerResponse"]["response"] = "A visible answer must use original result adoption"
    elif fault == "reasoning":
        steps[2]["status"] = "CORTEX_STEP_STATUS_RUNNING"
    elif fault == "terminal-without-execution":
        steps[3]["metadata"] = {"createdAt": meta["createdAt"]}
    elif fault == "foreign-terminal":
        steps[3]["metadata"] = {**meta, "executionId": "another-execution"}
    trajectory = {"trajectory": {"cascadeId": "native"}, "numTotalSteps": len(steps),
                  "status": "RUNNING" if fault == "busy" else "CASCADE_RUN_STATUS_IDLE"}
    calls = []
    def read(_url, _token, method, _body, **_):
        calls.append(method)
        if method == "GetCascadeTrajectory":
            return deepcopy(trajectory)
        assert method == "GetCascadeTrajectorySteps"
        result = deepcopy(steps[:-1] if fault == "truncated" else steps)
        if fault == "changed" and calls.count(method) > 1:
            result[2]["runCommand"]["combinedOutput"] = "changed"
        if fault == "file-changed" and calls.count(method) > 1:
            result[3]["viewFile"]["content"] = "changed"
        return {"steps": result}
    monkeypatch.setattr(native, "discover_antigravity_language_server", lambda: {"url": "synthetic", "csrf_token": "synthetic"})
    monkeypatch.setattr(native, "_rpc_post", read)
    monkeypatch.setattr(native, "manager_input_matches", lambda text, **_: text == prompt)
    proof = native.AntigravityAdapter.interrupted_answer_observation(cascade_id="native", request_ref="f" * 24,
        message="original question", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:10Z",
        input_anchor={"thread_id": "native", "start_step_index": 0,
            "input_sha256": "0" * 64 if fault == "wrong-hash" else hashlib.sha256(prompt.encode()).hexdigest()})
    if fault in {None, "terminal-without-execution", "file-read", "file-only"}:
        assert proof["end_step_index"] == len(steps) - 1 and proof["total_steps"] == len(steps)
        if fault != "file-only":
            assert proof["visible_results"][0]["combinedOutput"] == "already observed"
        if fault in {"file-read", "file-only"}:
            assert proof["visible_results"][-1] == {"file_uri": "file:///authorized/existing-result.txt",
                "content": "Original visible file result"}
        assert "thinking" not in json.dumps(proof) and "private reasoning" not in json.dumps(proof)
    else:
        assert proof is None
    assert not set(calls) - {"GetCascadeTrajectory", "GetCascadeTrajectorySteps"}


@pytest.mark.parametrize("fault", [None, "open", "busy", "mixed", "foreign", "changed", "wrong-hash",
    "no-visible-answer", "tool-call", "unknown", "oversize"])
def test_cleared_visible_answer_is_only_stable_historical_review_evidence(monkeypatch, fault):
    prompt = "Original synthetic source"
    meta = {"createdAt": "2026-01-01T00:00:05Z", "executionId": "original"}
    steps = [
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_CLEARED", "metadata": meta,
         "userInput": {"items": [{"text": prompt}]}},
        {"type": "CORTEX_STEP_TYPE_GENERIC", "status": "CORTEX_STEP_STATUS_CLEARED", "metadata": meta,
         "generic": {}},
        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_CLEARED", "metadata": meta,
         "plannerResponse": {"response": "Existing visible draft", "thinking": "private reasoning"}},
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
         "userInput": {"items": [{"text": "Later question"}]}}]
    if fault == "open":
        steps.pop()
    elif fault == "mixed":
        steps[1]["status"] = "CORTEX_STEP_STATUS_RUNNING"
    elif fault == "foreign":
        steps[1]["metadata"] = {**meta, "executionId": "other"}
    elif fault == "no-visible-answer":
        steps[2]["plannerResponse"].pop("response")
    elif fault == "tool-call":
        steps[2]["plannerResponse"]["toolCalls"] = [{"name": "unsettled"}]
    elif fault == "unknown":
        steps[1]["type"] = "FUTURE_NATIVE_OPERATION"
    elif fault == "oversize":
        steps[2]["plannerResponse"]["response"] = "x" * (256 * 1024)
    trajectory = {"trajectory": {"cascadeId": "native"}, "numTotalSteps": len(steps),
        "status": "RUNNING" if fault == "busy" else "CASCADE_RUN_STATUS_IDLE"}
    calls = []
    def read(_url, _token, method, _body, **_):
        calls.append(method)
        if method == "GetCascadeTrajectory":
            return deepcopy(trajectory)
        current = deepcopy(steps)
        if fault == "changed" and calls.count("GetCascadeTrajectorySteps") > 1:
            current[2]["plannerResponse"]["response"] = "Changed draft"
        return {"steps": current}
    monkeypatch.setattr(native, "discover_antigravity_language_server", lambda: {"url": "local", "csrf_token": "test"})
    monkeypatch.setattr(native, "_rpc_post", read)
    monkeypatch.setattr(native, "manager_input_matches", lambda *_args, **_kwargs: True)
    proof = native.AntigravityAdapter.interrupted_answer_observation(cascade_id="native", request_ref="f" * 24,
        message="Original question", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:10Z",
        input_anchor={"thread_id": "native", "start_step_index": 0,
            "input_sha256": "0" * 64 if fault == "wrong-hash" else hashlib.sha256(prompt.encode()).hexdigest()})
    if fault is None:
        assert proof["visible_results"] == [{"visible_response": "Existing visible draft"}]
        assert proof["read_only_review"] is True and proof["no_final_answer"] is True
        assert "private reasoning" not in json.dumps(proof)
    else:
        assert proof is None
    assert not set(calls) - {"GetCascadeTrajectory", "GetCascadeTrajectorySteps"}


@pytest.mark.parametrize("fault", [None, "stop", "source-changed", "no-capacity", "review-action", "review-gate", "queue-change", "already-codex", "post-enqueue-crash", "lost-ack", "new-question-during-answer", "restricted-review", "primary-quota-unreadable", "native-tail", "authenticated-host", "file-read", "file-running", "cleared-answer"])
def test_original_worker_answers_interrupted_steward_question_once(tmp_path, language_server, monkeypatch, fault):  # noqa: F811
    import loopx.chat_manager as manager_module
    import loopx.chat_runtime as runtime_module
    from test_chat_manager_model_adoption import configure
    from loopx.chat_manager import manager_executor_allocation

    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gemini-4-argon-high")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, capture, workspace = next(fixture)
    try:
        binding = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="antigravity", context_kind="steward")
        language_server.update(quota_error_indices=[0], quota_fraction=0)
        event = provider.event("steward-app", "interrupted-answer", "Explain the existing validation results")
        transport.admit("steward-app", event)
        ref = identity_ref(binding["provider_ref"], event["message_id"])
        original = transport.core.read_request(ref)
        failed = runtime.wait_for_turn(session_id=original["session_id"], turn_id=original["turn_id"], timeout_sec=15)
        assert failed["status"] == "failed" and failed["delta_count"] == 0
        if fault == "cleared-answer":
            store.update_turn(original["session_id"], original["turn_id"], error_code="runtime_error")
        original_bytes = store._turn_path(original["session_id"], original["turn_id"]).read_bytes()
        path = transport.root / f"{ref}.json"
        transport.reconcile_request(path)
        notice = json.loads(path.read_text())["deliveries"]["terminal"]
        post = native._rpc_post
        native_tail = False
        def visible_tools(*args, **kwargs):
            method = args[2]
            if method == "GetCascadeTrajectory":
                packet = post(*args, **kwargs)
                packet["numTotalSteps"] += 4 if native_tail else 3 if fault in {"file-read", "file-running"} else 2
                return packet
            if method != "GetCascadeTrajectorySteps":
                return post(*args, **kwargs)
            args = (*args[:3], {**args[3], "startIndex": 0}, *args[4:])
            packet = post(*args, **kwargs)
            user, error = packet["steps"][:2]
            meta = deepcopy(user["metadata"])
            planner = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
                "metadata": {**meta, "sourceTrajectoryStepInfo": {"stepIndex": 1}},
                "plannerResponse": {"thinking": "never forward this", "toolCalls": []}}
            tool = {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_DONE",
                "metadata": {**meta, "sourceTrajectoryStepInfo": {"stepIndex": 2}},
                "runCommand": {"commandLine": "read existing artifact", "cwd": str(workspace),
                    "blocking": True, "exitCode": 0, "combinedOutput": "Original verified output, not a new execution"}}
            error["metadata"]["sourceTrajectoryStepInfo"] = {"stepIndex": 3}
            steps = [user, planner, tool, error]
            if fault in {"file-read", "file-running"}:
                error["metadata"]["sourceTrajectoryStepInfo"] = {"stepIndex": 4}
                steps.insert(3, {"type": "CORTEX_STEP_TYPE_VIEW_FILE",
                    "status": "CORTEX_STEP_STATUS_RUNNING" if fault == "file-running" else "CORTEX_STEP_STATUS_DONE",
                    "metadata": {**meta, "sourceTrajectoryStepInfo": {"stepIndex": 3}},
                    "viewFile": {"absolutePathUri": (workspace / "original-result.txt").as_uri(),
                        "content": "Original visible file result"}})
            if native_tail:
                for index, kind, payload in [(4, "CORTEX_STEP_TYPE_USER_INPUT", {"userInput": {"items": [{"text": "Another native question"}]}}),
                        (5, "CORTEX_STEP_TYPE_ERROR_MESSAGE", {"errorMessage": {"error": {"errorCode": 429}}})]:
                    steps.append({"type": kind, "status": "CORTEX_STEP_STATUS_DONE", "metadata": {
                        **meta, "executionId": "other-execution", "sourceTrajectoryStepInfo": {"stepIndex": index}}, **payload})
            if fault == "cleared-answer":
                # Completion metadata/tool payloads were cleared, but the
                # original input and visible response survive. A later input
                # closes this segment; no completed-result adoption is proved.
                steps = [user,
                    {"type": "CORTEX_STEP_TYPE_GENERIC", "metadata": {**meta,
                        "sourceTrajectoryStepInfo": {"stepIndex": 1}}, "generic": {}},
                    {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "metadata": {**meta,
                        "sourceTrajectoryStepInfo": {"stepIndex": 2}},
                     "plannerResponse": {"response": "Original verified output, not a new execution",
                        "thinking": "never forward this"}}]
                for step in steps:
                    step["status"] = "CORTEX_STEP_STATUS_CLEARED"
                steps.append({"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {**meta, "executionId": "later", "sourceTrajectoryStepInfo": {"stepIndex": 3}},
                    "userInput": {"items": [{"text": "Later independent question"}]}})
            return {"steps": steps}
        monkeypatch.setattr(native, "_rpc_post", visible_tools)
        if fault == "native-tail":
            observe = native.AntigravityAdapter.interrupted_answer_observation
            observed = []
            def later_native_question(**kwargs):
                nonlocal native_tail
                native_tail = bool(observed)
                result = observe(**kwargs)
                observed.append(result)
                return result
            monkeypatch.setattr(native.AntigravityAdapter, "interrupted_answer_observation", later_native_question)
        artifact = workspace / "original-result.txt"
        artifact.write_text("Original result must not change")
        configure(runtime.coordination_runtime_root, model="gemini-4-argon-high", effort="high", endpoint="antigravity", policy="flexible")
        if fault == "restricted-review":
            from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
            from loopx.capabilities.machine_configuration.store import configure_machine_configuration, read_stored_machine_configuration
            registry = build_builtin_machine_configuration_registry()
            configuration = deepcopy(read_stored_machine_configuration(runtime.coordination_runtime_root))
            configuration["namespaces"]["manager_runtime"] = {"schema_version": "manager_runtime_profile_v0", "runtime_profile": "restricted"}
            preview = configure_machine_configuration(runtime_root=runtime.coordination_runtime_root,
                registry=registry, configuration=configuration, execute=False)
            configure_machine_configuration(runtime_root=runtime.coordination_runtime_root,
                registry=registry, configuration=configuration, execute=True, expected_plan_revision=preview["plan_revision"])
        session = store.load_session(original["session_id"])
        host_reads = runtime.manager_runtime_profile(session["channel_id"], steward_context=session["steward_context"])["runtime_profile"] == "trusted_owner"
        capabilities = [{"agent_id": name, "available": True, "adapter_kind": kind} for name, kind in
            [("antigravity", "antigravity_ls"), ("codex", "codex_app_server")]]
        monkeypatch.setattr(runtime, "capabilities", lambda: capabilities)
        allocation = manager_executor_allocation(runtime, environ={}, endpoint_capabilities=[
            {**item, "available": item["agent_id"] == "codex"} for item in capabilities])
        def route(*_):
            return None if fault == "no-capacity" else {"allocation": allocation, "blocked_primary": None, "observed_at": time.time()}
        monkeypatch.setattr(manager_module, "manager_session_quota_route", route)
        monkeypatch.setattr(runtime_module, "manager_session_quota_route", route)
        if fault == "primary-quota-unreadable":
            monkeypatch.setattr(native.AntigravityAdapter, "metadata_review_capacity",
                lambda **_: pytest.fail("a verified API-primary alternate must not probe unrelated primary quota"))
        api_calls = []
        auth_before = None
        if fault == "authenticated-host":
            from loopx.capabilities.native_chat import codex_auth
            runtime.codex_home.mkdir(parents=True, exist_ok=True)
            auth = runtime.codex_home / "auth.json"
            auth.write_text('{"auth_mode":"chatgpt","fixture":"original-host-login"}')
            auth_before = auth.read_bytes()
            def api_route(runtime_root, home, *, consumer, model):
                api_calls.append((runtime_root, home, consumer, model))
                return codex_auth.CodexApiTransport("http://127.0.0.1:1/steward/v1", "synthetic-loopback-token")
            monkeypatch.setattr(codex_auth, "native_api_transport", api_route)
        fake = Path(runtime.codex_bin)
        source = fake.read_text().replace('result = {"thread": {"id": "durable-thread"}}', '''result = {"thread": {"id": "durable-thread"}, "model": "gpt-6.1-sol"}
        permissions = request.get("params", {}).get("permissions")
        if permissions:
            result.update(activePermissionProfile={"id": permissions}, runtimeWorkspaceRoots=[request["params"]["cwd"]])''')
        if fault == "authenticated-host":
            source = source.replace('"model": "gpt-6.1-sol"}', '"model": "gpt-6.1-sol", "modelProvider": request.get("params", {}).get("modelProvider")}')
        if fault in {"review-action", "review-gate"}:
            response = {"message": "The existing evidence supports only this limited answer.", "proposals": [],
                "gate": {"kind": "host_tool_gate", "summary": "Missing original validation evidence", "next_action": "Keep original question owed"} if fault == "review-gate" else None}
            if fault == "review-action":
                response["unfinished_work"] = {"remaining": "Do not admit this work", "dependency": "original owner", "effects_settled": True}
            begin = source.index("        response = 'Visible runtime response.")
            end = source.index('        print(json.dumps({"method": "item/agentMessage/delta"', begin)
            source = source[:begin] + "        response = " + repr('<loopx-review-json>' + json.dumps(response) + '</loopx-review-json>') + "\n" + source[end:]
        fake.write_text(source)
        new_question = []
        if fault == "new-question-during-answer":
            from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
            start = CodexAppServerAdapter.start_turn
            def independent_question(adapter, message, event_sink):
                result = start(adapter, message, event_sink)
                if adapter.session.answer_review:
                    later = provider.event("steward-app", "independent-during-answer", "A separate newer question")
                    transport.admit("steward-app", later)
                    new_question.append(transport.core.read_request(identity_ref(binding["provider_ref"], later["message_id"])))
                return result
            monkeypatch.setattr(CodexAppServerAdapter, "start_turn", independent_question)
        if fault == "already-codex":
            later = provider.event("steward-app", "later-normal-before-review", "A separate newer question")
            transport.admit("steward-app", later)
            later_row = transport.core.read_request(identity_ref(binding["provider_ref"], later["message_id"]))
            assert runtime.wait_for_turn(session_id=later_row["session_id"], turn_id=later_row["turn_id"], timeout_sec=15)["status"] == "completed"
            transport.reconcile_request(transport.root / f"{later_row['request_ref']}.json")
            assert store.load_session(original["session_id"])["agent_id"] == "codex"
        class Ahead(datetime):
            @classmethod
            def now(cls, tz=None):
                return super().now(tz) + timedelta(seconds=65)
        monkeypatch.setattr(external_conversations, "datetime", Ahead)
        if fault == "stop":
            store.append_event(original["session_id"], original["turn_id"], kind="turn.interrupt_requested", payload={})
        elif fault == "source-changed":
            provider.messages[event["message_id"]]["content"] = "Changed source"
        elif fault == "queue-change":
            store.create_queued_turn(original["session_id"], client_turn_id="later-correction", message="Changed requirement", origin="lark")
        elif fault == "post-enqueue-crash":
            enqueue = runtime.enqueue_turn
            def crash(**kwargs):
                enqueue(**kwargs)
                raise OSError("unknown enqueue response")
            monkeypatch.setattr(runtime, "enqueue_turn", crash)
            with pytest.raises(OSError, match="unknown enqueue response"):
                transport.reconcile_request(path)
            monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
        elif fault == "lost-ack":
            provider.lose_next_reply = True
        transport.reconcile_request(path)
        recovered = transport.core.read_request(ref)
        if fault in {"stop", "source-changed", "no-capacity", "queue-change", "file-running"}:
            assert recovered["turn_id"] == original["turn_id"] and not recovered.get("context_recovery")
        else:
            assert recovered["context_recovery"]["answer_review"] is True
            done = runtime.wait_for_turn(session_id=recovered["session_id"], turn_id=recovered["turn_id"], timeout_sec=15)
            assert done["status"] == ("failed" if fault == "review-action" else "completed"), done
            transport.reconcile_request(path)
            current = transport.core.read_request(ref)
            assert current["delivery_verified"]
            for later in new_question:
                assert runtime.wait_for_turn(session_id=later["session_id"], turn_id=later["turn_id"], timeout_sec=15)["status"] == "completed"
                transport.reconcile_request(transport.root / f"{later['request_ref']}.json")
            assert transport.health()[binding["binding_id"]]["pending_count"] == int(fault in {"review-action", "review-gate"})
            packets = [json.loads(line) for line in capture.read_text().splitlines()]
            starts = [p["params"] for p in packets if p.get("method") == "turn/start"]
            assert len(starts) == (2 if fault in {"already-codex", "new-question-during-answer"} else 1)
            reviews = [p for p in starts if "Core admitted ONE read-only answer review" in p["input"][0]["text"]]
            assert len(reviews) == 1
            profile = "loopx_steward_host_read" if host_reads else "loopx_workspace_only_read"
            assert reviews[0]["permissions"] == profile
            assert "Original verified output" in reviews[0]["input"][0]["text"]
            if fault == "file-read":
                assert "Original visible file result" in reviews[0]["input"][0]["text"]
            assert "never forward this" not in reviews[0]["input"][0]["text"]
            opens = [p["params"] for p in packets if p.get("method") == "thread/start"]
            review_opens = [p for p in opens if p.get("permissions") == profile]
            assert len(review_opens) == 1
            assert [t["name"] for t in review_opens[0]["dynamicTools"]] == ["loopx_manager_read"]
            assert review_opens[0]["config"]["features"]["shell_tool"] is host_reads
            assert review_opens[0]["config"]["features"]["apply_patch_freeform"] is False
            assert review_opens[0]["config"]["features"]["apps"] is False
            permissions = review_opens[0]["config"]["permissions"][profile]
            assert permissions["network"]["enabled"] is False
            assert permissions["filesystem"][":root"] == ("read" if host_reads else "deny")
            assert permissions["filesystem"][":workspace_roots"]["."] == "read"
            if fault == "authenticated-host":
                assert api_calls == [(runtime.coordination_runtime_root, runtime.codex_home, "steward", "gpt-6.1-sol")]
                assert review_opens[0]["modelProvider"] == "loopx_account_api"
                assert review_opens[0]["config"]["model_providers"]["loopx_account_api"]["base_url"] == "http://127.0.0.1:1/steward/v1"
                assert (runtime.codex_home / "auth.json").read_bytes() == auth_before
            after = capture.read_bytes()
            transport.reconcile_request(path)
            assert capture.read_bytes() == after
            if fault is None:
                later = provider.event("steward-app", "later-normal", "A separate normal question")
                transport.admit("steward-app", later)
                next_row = transport.core.read_request(identity_ref(binding["provider_ref"], later["message_id"]))
                assert runtime.wait_for_turn(session_id=next_row["session_id"], turn_id=next_row["turn_id"], timeout_sec=15)["status"] == "completed"
                assert runtime.adapters[original["session_id"]].session.read_only_review is False
                assert runtime.adapters[original["session_id"]].session.answer_review is False
                assert store.load_session(original["session_id"])["answer_review"] is False
        assert store._turn_path(original["session_id"], original["turn_id"]).read_bytes() == original_bytes
        assert json.loads(path.read_text())["deliveries"]["terminal"] == notice
        assert artifact.read_text() == "Original result must not change"
        assert language_server["sent_count"] == 1
    finally:
        runtime.close()
        fixture.close()
