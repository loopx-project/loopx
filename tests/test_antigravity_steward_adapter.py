"""Focused tests for Antigravity (Gemini 4 Argon) Steward & Chat adapter."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest
from typing import Any

from loopx.capabilities.native_chat.antigravity_adapter import (
    ANTIGRAVITY_DEFAULT_MODEL,
    AntigravityAdapter,
    probe_antigravity_endpoint,
    resolve_antigravity_model_placeholder,
)
from loopx.capabilities.steward_executor import (
    STEWARD_EXECUTOR_SCHEMA,
    effective_steward_executor_defaults,
    normalize_steward_executor_machine_defaults,
    steward_executor_endpoints,
)
from loopx.chat_manager import (
    MANAGER_MODEL_SOURCE_MACHINE_CONFIGURATION,
    MANAGER_MODEL_SOURCE_VENDOR_DEFAULT,
    manager_channel_binding,
    manager_model_resolution,
)


def test_antigravity_is_registered_in_steward_executor_vocabulary() -> None:
    assert "antigravity" in steward_executor_endpoints()

    normalized = normalize_steward_executor_machine_defaults(
        {
            "schema_version": STEWARD_EXECUTOR_SCHEMA,
            "selection_policy": "preferred",
            "executor_endpoint": "antigravity",
            "eligible_endpoints": [],
            "executor_model": "gemini-4-argon-high",
            "executor_reasoning_effort": "high",
        }
    )
    assert normalized["executor_endpoint"] == "antigravity"
    assert normalized["executor_model"] == "gemini-4-argon-high"

    model, source = manager_model_resolution({}, endpoint="antigravity")
    assert model == ANTIGRAVITY_DEFAULT_MODEL == "gemini-4-argon-high"
    assert source == MANAGER_MODEL_SOURCE_VENDOR_DEFAULT

    binding = manager_channel_binding(
        {},
        machine_defaults=effective_steward_executor_defaults(
            {
                "schema_version": "loopx_machine_configuration_v0",
                "namespaces": {"steward_executor": normalized},
            }
        ),
    )
    assert binding["executor_endpoint"] == "antigravity"
    assert binding["model"] == "gemini-4-argon-high"
    assert binding["model_source"] == MANAGER_MODEL_SOURCE_MACHINE_CONFIGURATION


def test_antigravity_model_placeholder_resolution() -> None:
    assert resolve_antigravity_model_placeholder("gemini-4-argon-high") == (
        "gemini-4-argon-high",
        "MODEL_PLACEHOLDER_M417",
    )
    assert resolve_antigravity_model_placeholder("gemini-4-argon-medium") == (
        "gemini-4-argon-medium",
        "MODEL_PLACEHOLDER_M418",
    )
    assert resolve_antigravity_model_placeholder("gemini-4-argon-low") == (
        "gemini-4-argon-low",
        "MODEL_PLACEHOLDER_M419",
    )
    assert resolve_antigravity_model_placeholder("argon", "medium") == (
        "gemini-4-argon-medium",
        "MODEL_PLACEHOLDER_M418",
    )
    assert resolve_antigravity_model_placeholder("gemini-4-argon", "low") == (
        "gemini-4-argon-low",
        "MODEL_PLACEHOLDER_M419",
    )


@pytest.fixture
def language_server(monkeypatch):
    """Actual HTTP boundary; only the external model/provider is synthetic."""
    state: dict[str, Any] = {"calls": [], "sent_count": 0, "mode": "complete", "active": False,
                             "sent": threading.Event(), "cancelled": threading.Event()}
    token = "synthetic-csrf-token"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            assert self.headers.get("x-codeium-csrf-token") == token
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = self.path.rsplit("/", 1)[-1]
            state["calls"].append((method, body))
            if state.get("redirect"):
                self.send_response(302)
                self.send_header("Location", state["redirect"])
                self.end_headers()
                return
            if method == "GetCascadeModelConfigData":
                response = {"clientModelConfigs": [
                    {"modelId": f"gemini-4-argon-{tier}",
                     "modelOrAlias": {"model": f"MODEL_PLACEHOLDER_M{number}"}}
                    for tier, number in (("high", 417), ("medium", 418), ("low", 419))]}
            elif method == "StartCascade":
                response = {"cascadeId": "synthetic-cascade"}
            elif method == "GetCascadeTrajectory":
                response = {"trajectory": {"cascadeId": "synthetic-cascade"},
                    "numTotalSteps": state["sent_count"] * 2,
                    "status": ("CASCADE_RUN_STATUS_RUNNING" if state["mode"] in {"running", "waiting"}
                               and state["active"] else "CASCADE_RUN_STATUS_IDLE")}
                if state.get("missing_resume"):
                    response.pop("trajectory")
            elif method == "SendUserCascadeMessage":
                if state.get("send_failure"):
                    self.send_response(503)
                    self.end_headers()
                    return
                state["sent_count"] += 1
                state["active"] = True
                state["cancelled"].clear()
                state["sent"].set()
                response = {}
            elif method == "GetCascadeTrajectorySteps":
                response = {"steps": []}
                for index in range(body["startIndex"] // 2, state["sent_count"]):
                    response["steps"].extend([
                        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"},
                        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                         "status": "CORTEX_STEP_STATUS_WAITING" if state["mode"] == "waiting" else "CORTEX_STEP_STATUS_DONE",
                         "plannerResponse": {"thinking": "synthetic status",
                             "response": '<loopx-review-json>' + json.dumps({"message": f"Argon answer {index + 1}",
                                 "proposals": [], "context_handoff": [], "gate": None}) + '</loopx-review-json>'}}])
            elif method == "CancelCascadeInvocation":
                state["active"] = False
                state["cancelled"].set()
                response = {}
            else:
                raise AssertionError(method)
            payload = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("ANTIGRAVITY_LS_URL", state["url"])
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", token)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def adapter_for(tmp_path, **kwargs):
    return AntigravityAdapter.start(work_dir=tmp_path, objective="Act as LoopX Steward.", **kwargs)


def test_antigravity_adapter_executes_repeated_turns_and_streams_response(tmp_path, language_server):
    available, executable, _ = probe_antigravity_endpoint()
    assert available and executable == "antigravity-ls"
    adapter = adapter_for(tmp_path)
    events = []
    for index in range(2):
        result = adapter.start_turn("Summarize synthetic goals", lambda kind, data: events.append((kind, data)))
        assert result["message"] == f"Argon answer {index + 1}"
    assert adapter.current_model_id == "gemini-4-argon-high"
    assert {kind for kind, _ in events} >= {"turn.started", "agent.phase", "answer.delta", "answer.final"}
    calls = language_server["calls"]
    assert len([1 for method, _ in calls if method == "StartCascade"]) == 1
    payload = next(body for method, body in calls if method == "SendUserCascadeMessage")
    config = payload["cascadeConfig"]["plannerConfig"]
    assert config["toolConfig"]["forceDisableToolCalling"] is True
    assert config["conversational"]["agenticMode"] is False
    assert "userAgentSpec" not in payload


@pytest.mark.parametrize("model,effort", [("unknown", None), ("gemini-4-argon-low", "high"), ("argon", "xhigh")])
def test_unsupported_model_intent_has_no_upstream_effect(tmp_path, language_server, model, effort):
    from loopx.chat_agent import CodexChatAgentError
    with pytest.raises(CodexChatAgentError) as error:
        adapter_for(tmp_path, executor_model={"model": model, "reasoning_effort": effort})
    assert error.value.error_code == "unsupported_configuration"
    assert language_server["calls"] == []


def test_canonical_model_and_effort_readback_agree(tmp_path, language_server):
    from loopx.capabilities.native_chat.model_settings import observed_model_settings
    adapter = adapter_for(tmp_path, executor_model={"model": "argon", "reasoning_effort": "low"})
    assert adapter.current_model_id == "gemini-4-argon-low"
    assert observed_model_settings(adapter, "low", "argon")["agent_reasoning_effort"] == "low"


def test_failed_resume_does_not_create_a_hidden_new_conversation(tmp_path, language_server):
    from loopx.chat_agent import CodexChatAgentError
    language_server["missing_resume"] = True
    with pytest.raises(CodexChatAgentError) as error:
        adapter_for(tmp_path, resume_thread_id="missing-cascade")
    assert error.value.error_code == "resume_failed"
    assert not any(method == "StartCascade" for method, _ in language_server["calls"])


def test_resume_retains_cascade_and_refreshes_current_guidance(tmp_path, language_server):
    adapter = adapter_for(tmp_path)
    adapter.start_turn("first")
    adapter.close_session()
    resumed = adapter_for(tmp_path, resume_thread_id="synthetic-cascade")
    assert resumed.start_turn("second")["message"] == "Argon answer 2"
    sends = [body for method, body in language_server["calls"] if method == "SendUserCascadeMessage"]
    assert "Act as LoopX Steward." in sends[1]["items"][0]["text"]
    assert len([1 for method, _ in language_server["calls"] if method == "StartCascade"]) == 1


def test_timeout_with_partial_response_never_reports_success(tmp_path, language_server):
    from loopx.chat_agent import CodexChatAgentError
    language_server["mode"] = "running"
    adapter = adapter_for(tmp_path, hard_timeout_sec=0.05)
    events = []
    with pytest.raises(CodexChatAgentError) as error:
        adapter.start_turn("long turn", lambda kind, data: events.append((kind, data)))
    assert error.value.error_code == "response_timeout"
    assert language_server["cancelled"].is_set()
    assert not any(kind == "answer.final" for kind, _ in events)


def test_pending_native_approval_is_actionable_and_not_auto_accepted(tmp_path, language_server):
    from loopx.chat_agent import CodexChatAgentError
    language_server["mode"] = "waiting"
    with pytest.raises(CodexChatAgentError) as error:
        adapter_for(tmp_path, runtime_profile="trusted_owner").start_turn("requires native approval")
    assert error.value.gate["kind"] == "host_tool_gate"
    assert language_server["cancelled"].is_set()


def test_failed_send_keeps_priming_for_the_next_turn(tmp_path, language_server):
    from loopx.chat_agent import CodexChatAgentError
    adapter = adapter_for(tmp_path)
    language_server["send_failure"] = True
    with pytest.raises(CodexChatAgentError):
        adapter.start_turn("failed")
    language_server["send_failure"] = False
    assert adapter.start_turn("retry")["message"] == "Argon answer 1"
    sends = [body for method, body in language_server["calls"] if method == "SendUserCascadeMessage"]
    assert "Act as LoopX Steward." in sends[1]["items"][0]["text"]


def test_explicit_remote_endpoint_and_redirect_never_forward_token(language_server, monkeypatch):
    from loopx.capabilities.native_chat.antigravity_adapter import discover_antigravity_language_server
    monkeypatch.setenv("ANTIGRAVITY_LS_URL", "https://example.invalid")
    assert discover_antigravity_language_server() is None
    assert language_server["calls"] == []
    monkeypatch.setenv("ANTIGRAVITY_LS_URL", language_server["url"])
    language_server["redirect"] = language_server["url"] + "/redirect-target"
    assert discover_antigravity_language_server() is None
    assert len(language_server["calls"]) == 1


def test_stop_acknowledges_worker_and_next_turn_progresses(tmp_path, language_server):
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    store = ChatSessionStore(tmp_path / "runtime" / "chat")
    controller = ChatRuntimeController(store=store, codex_bin="unused-codex", hard_timeout_sec=10)
    session, _ = controller.open_session(goal_id="synthetic-goal", agent_id="antigravity", work_dir=tmp_path,
        objective="Synthetic conversation", mode="new", channel_id="synthetic-argon-chat")
    sid = session["session_id"]
    assert session["agent_model_id"] == "gemini-4-argon-high"
    language_server["mode"] = "running"
    try:
        turn, _ = controller.submit_turn(session_id=sid, client_turn_id="first", message="long request", work_dir=tmp_path,
                                     objective="Synthetic conversation")
        assert language_server["sent"].wait(5)
        adapter = controller.adapters[sid]
        with pytest.raises(ValueError, match="Turn changed"):
            adapter.interrupt_turn("unrelated-turn")
        assert not language_server["cancelled"].is_set()
        stopped = controller.interrupt_turn(session_id=sid, turn_id=turn["turn_id"])
        assert stopped["status"] == "interrupted"
        assert language_server["cancelled"].is_set()
        language_server["mode"] = "complete"
        next_turn, _ = controller.submit_turn(session_id=sid, client_turn_id="second", message="continue", work_dir=tmp_path,
                                          objective="Synthetic conversation")
        result = controller.wait_for_turn(session_id=sid, turn_id=next_turn["turn_id"], timeout_sec=5)
        assert result["status"] == "completed"
        assert result["response"]["message"] == "Argon answer 2"
    finally:
        controller.close_session(sid)


def test_deadline_expiry_after_partial_text_fails_closed(tmp_path, language_server, monkeypatch):
    from loopx.capabilities.native_chat import antigravity_adapter as provider
    from loopx.chat_agent import CodexChatAgentError
    language_server["mode"] = "running"
    adapter = adapter_for(tmp_path)
    ticks = iter([0.0, 0.0, 301.0])
    monkeypatch.setattr(provider.time, "monotonic", lambda: next(ticks, 301.0))
    with pytest.raises(CodexChatAgentError) as error:
        adapter.start_turn("long request")
    assert error.value.error_code == "response_timeout"
    assert language_server["cancelled"].is_set()


def test_busy_native_conversation_is_not_sent_to_or_cancelled(tmp_path, language_server):
    from loopx.chat_agent import CodexChatAgentError
    language_server.update(mode="running", active=True)
    adapter = adapter_for(tmp_path, resume_thread_id="synthetic-cascade")
    with pytest.raises(CodexChatAgentError) as error:
        adapter.start_turn("must not race the native owner")
    assert error.value.error_code == "resume_failed"
    assert not any(method in {"SendUserCascadeMessage", "CancelCascadeInvocation"}
                   for method, _ in language_server["calls"])
