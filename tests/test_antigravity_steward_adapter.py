"""Focused tests for Antigravity (Gemini 4 Argon) Steward & Chat adapter."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from copy import deepcopy
import json
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

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
            failures = state.get("read_failures", {})
            if state["active"] and failures.get(method, 0):
                failures[method] -= 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"incomplete observation")
                return
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
                if state.get("frames") is not None:
                    if state["active"]:
                        if state["frames"]:
                            state["frame"] = state["frames"].pop(0)
                        frame = state["frame"]
                        state["now"] = frame["at"]
                    else:
                        frame = {"steps": [], "status": "CASCADE_RUN_STATUS_IDLE"}
                    response = {"trajectory": {"cascadeId": "synthetic-cascade"},
                                "numTotalSteps": frame.get("total", len(frame["steps"])), "status": frame["status"]}
                else:
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
                state.setdefault("inputs", []).append({"text": body["items"][0]["text"],
                    "created_at": datetime.now(timezone.utc).isoformat()})
                state["active"] = True
                state["cancelled"].clear()
                state["sent"].set()
                response = {}
            elif method == "GetCascadeTrajectorySteps":
                response = {"steps": state["frame"]["steps"] if state.get("frames") is not None else []}
                start = 0 if state.get("ignore_step_range") else body["startIndex"] // 2
                for index in (() if state.get("frames") is not None else range(start, state["sent_count"])):
                    answer = f"Argon answer {index + 1}"
                    if not state.get("plain_text"):
                        answer = '<loopx-review-json>' + json.dumps({"message": answer,
                            "proposals": [], "context_handoff": (state.get("original_handoffs") or [{"goal_id": "work", "agent_id": "worker", "brief": "invalid"}]
                                if state.get("invalid_handoff") else []), "gate": None,
                            **({"unfinished_work": {"remaining": "Original work still owed", "dependency": "Original worker",
                                "effects_settled": True}} if state.get("unfinished") else {})}) + '</loopx-review-json>'
                    if index > 0 and state.get("review_response") is not None:
                        answer = '<loopx-review-json>' + json.dumps(state["review_response"]) + '</loopx-review-json>'
                    native = state["inputs"][index]
                    meta = {"createdAt": native["created_at"], "executionId": f"execution-{index}"}
                    response["steps"].extend([
                        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
                         "metadata": {**meta, "sourceTrajectoryStepInfo": {"stepIndex": index * 2}},
                         "userInput": {"items": [{"text": native["text"]}], "userResponse": native["text"]}},
                        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                         "metadata": {**meta, "sourceTrajectoryStepInfo": {"stepIndex": index * 2 + 1}},
                         "status": "CORTEX_STEP_STATUS_WAITING" if state["mode"] == "waiting" else "CORTEX_STEP_STATUS_DONE",
                         "plannerResponse": {"thinking": "synthetic status", "response": answer,
                                             "stopReason": "STOP_REASON_STOP_PATTERN"}}])
                    if index > 0:
                        final = response["steps"][-1]
                        fault = state.get("review_native_fault")
                        if fault == "tool-call":
                            final["plannerResponse"]["toolCalls"] = [{"name": "run_command"}]
                        elif fault == "tool-step":
                            final["type"] = "CORTEX_STEP_TYPE_RUN_COMMAND"
                        elif fault == "mixed-execution":
                            final["metadata"]["executionId"] = "another-execution"
                        elif fault == "missing-stop":
                            final["plannerResponse"]["stopReason"] = None
                if state.get("native_indices"):
                    for offset, step in enumerate(response["steps"]):
                        step["metadata"] = {"sourceTrajectoryStepInfo": {"stepIndex": offset}}
            elif method == "CancelCascadeInvocation":
                if state.get("cancel_failure"):
                    self.send_response(500)
                    self.end_headers()
                    return
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


def _visible_history():
    return [{"role": "user" if index % 2 == 0 else "assistant",
             "content": ("  EARLIEST-CONSTRAINT\r\n不要重复已完成的动作\t  " if index == 0 else
                         " \t\r\n " if index == 19 else
                         f"Message {index}: 中文🚀\nassistant: quoted role\n" * 40)}
            for index in range(20)]


def _read_visible_history(prompt):
    start = prompt.index("Previous visible Chat messages")
    return json.JSONDecoder().raw_decode(prompt[prompt.index("\n", start) + 1:])[0]


@pytest.mark.parametrize("profile", ["restricted", "trusted_owner"])
@pytest.mark.parametrize("resumed", [False, True])
def test_native_history_is_complete_exact_data_once_on_a_new_thread(
    tmp_path, language_server, profile, resumed,
):
    history = _visible_history()
    history[0]["transport_metadata"] = "DO-NOT-SEND-TRANSPORT-METADATA"
    original = deepcopy(history)
    adapter = adapter_for(tmp_path, runtime_profile=profile, history=history,
                          **({"resume_thread_id": "synthetic-cascade"} if resumed else {}))
    try:
        adapter.start_turn("CURRENT-REQUEST", lambda *_: None)
        first = next(body for method, body in language_server["calls"] if method == "SendUserCascadeMessage")
        prompt = first["items"][0]["text"]
        if resumed:
            assert "Previous visible Chat messages" not in prompt
        else:
            assert "EARLIEST-CONSTRAINT" in prompt
            assert _read_visible_history(prompt) == [
                {"role": item["role"], "content": item["content"]} for item in original]
        assert "DO-NOT-SEND-TRANSPORT-METADATA" not in prompt
        assert prompt.count("CURRENT-REQUEST") == 1
        assert first["cascadeConfig"]["plannerConfig"]["conversational"]["agenticMode"] == (profile == "trusted_owner")
        adapter.start_turn("NEXT-REQUEST", lambda *_: None)
        sends = [body for method, body in language_server["calls"] if method == "SendUserCascadeMessage"]
        assert len(sends) == 2
        assert "Previous visible Chat messages" not in sends[1]["items"][0]["text"]
        assert sum(method == "StartCascade" for method, _ in language_server["calls"]) == (0 if resumed else 1)
        assert history == original
    finally:
        adapter.close_session()


@pytest.mark.parametrize("profile", ["restricted", "trusted_owner"])
def test_manager_context_migration_keeps_all_stored_visible_history(
    tmp_path, monkeypatch, language_server, profile,
):
    from loopx import chat_manager_context
    from loopx.chat_manager import MANAGER_AGENT_GOAL_ID, MANAGER_CONTEXT_VERSION
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
    from loopx.capabilities.machine_configuration.store import configure_machine_configuration

    root = tmp_path / "runtime"
    configuration = {"schema_version": "loopx_machine_configuration_v0", "namespaces": {
        "manager_runtime": {"schema_version": "manager_runtime_profile_v0", "runtime_profile": profile}}}
    registry = build_builtin_machine_configuration_registry()
    plan = configure_machine_configuration(runtime_root=root, registry=registry,
                                            configuration=configuration, execute=False)
    configure_machine_configuration(runtime_root=root, registry=registry, configuration=configuration,
                                    execute=True, expected_plan_revision=plan["plan_revision"])
    monkeypatch.setattr(chat_manager_context, "collect_manager_turn_context", lambda *_a, **_kw: {
        "schema_version": "manager_turn_context_v1", "goals": [], "coverage": {"complete": False}})
    store = ChatSessionStore(root)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"goals": []}))
    runtime = ChatRuntimeController(store=store, codex_bin="unused", registry_path=registry_path)
    session = store.create_session(goal_id=MANAGER_AGENT_GOAL_ID, agent_id="antigravity",
        adapter_kind="antigravity_ls", upstream_thread_id="old-context", channel_id="manager",
        agent_model_id="gemini-4-argon-high", agent_reasoning_effort="high",
        requested_agent_model_id="gemini-4-argon-high", requested_agent_reasoning_effort="high")
    sid = session["session_id"]
    store.update_session(sid, manager_context_version=MANAGER_CONTEXT_VERSION - 1)
    history = _visible_history()
    for item in history:
        store.append_message(sid, role="agent" if item["role"] == "assistant" else "user", text=item["content"])
    store.append_message(sid, role="error", text="Retain this error in the store, not as a user instruction.")
    before = store.messages(sid)
    try:
        for index in range(2):
            turn, _ = runtime.submit_turn(session_id=sid, client_turn_id=f"migration-{index}",
                message=f"Continue request {index}", work_dir=tmp_path, objective="manager")
            done = runtime.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=20)
            assert done["status"] == "completed", done
        sends = [body for method, body in language_server["calls"] if method == "SendUserCascadeMessage"]
        assert len(sends) == 2
        assert "EARLIEST-CONSTRAINT" in sends[0]["items"][0]["text"]
        assert _read_visible_history(sends[0]["items"][0]["text"]) == history
        assert "Previous visible Chat messages" not in sends[1]["items"][0]["text"]
        assert sum(method == "StartCascade" for method, _ in language_server["calls"]) == 1
        assert store.messages(sid)[:len(before)] == before
        assert len(store.messages(sid)) == len(before) + 4
        current = store.load_session(sid)
        assert current["manager_context_version"] == MANAGER_CONTEXT_VERSION
        assert current["manager_runtime_profile"] == profile
        assert current["upstream_thread_id"] == "synthetic-cascade"
    finally:
        runtime.close()


@pytest.mark.parametrize(("unicode_source", "malformed"), [("ps", False), ("lsof", False), ("ps", True), ("lsof", True)])
def test_process_discovery_decodes_utf8_before_verifying_the_local_endpoint(
    monkeypatch, language_server, unicode_source, malformed,
):
    from loopx.capabilities.native_chat import antigravity_adapter as provider

    real_check_output = subprocess.check_output
    calls = []
    port = language_server["url"].rsplit(":", 1)[1]
    outputs = {
        "ps": "123 /Applications/Antigravity.app/language_server --csrf_token synthetic-csrf-token\n",
        "lsof": f"language_server 123 user 10u IPv4 0 TCP 127.0.0.1:{port} (LISTEN)\n",
    }
    outputs[unicode_source] = outputs[unicode_source].replace("language_server", "中文🚀/language_server", 1)
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "gbk")
    # The process/port discovery is POSIX-owned; simulate its listings on
    # Windows while retaining actual child pipes and the loopback HTTP owner.
    monkeypatch.setattr(provider.os, "getuid", lambda: 501, raising=False)

    def listing(command, **kwargs):
        calls.append(command[0])
        output = outputs[command[0]].encode("utf-8")
        if malformed and command[0] == unicode_source:
            output += b"\xff\n"
        return real_check_output(
            [sys.executable, "-c", f"import sys; sys.stdout.buffer.write({output!r})"],
            **kwargs,
        )

    monkeypatch.setattr(provider.subprocess, "check_output", listing)
    discovered = provider.discover_antigravity_language_server(env={})
    assert discovered == {
        "url": language_server["url"], "csrf_token": "synthetic-csrf-token",
        "pid": 123, "source": "process_discovery",
    }
    assert calls == ["ps", "lsof"]
    assert [method for method, _ in language_server["calls"]] == ["GetCascadeModelConfigData"]
    assert language_server["sent_count"] == 0


def test_readiness_budget_includes_process_listing_but_execution_discovery_stays_fresh(monkeypatch):
    from loopx.capabilities.native_chat import antigravity_adapter as provider

    clock = [0.0]
    calls = []
    monkeypatch.setattr(provider.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(provider.os, "getuid", lambda: 501, raising=False)

    def listing(command, **kwargs):
        calls.append(command[0])
        duration = 2.8 if command[0] == "ps" else 1.4
        clock[0] += min(duration, kwargs["timeout"])
        if duration > kwargs["timeout"]:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return ("123 /Applications/Antigravity.app/language_server --csrf_token synthetic-token\n"
                if command[0] == "ps" else "server 123 user TCP 127.0.0.1:12345 (LISTEN)\n")

    def rpc(*args, **kwargs):
        calls.append("rpc")
        return {}

    monkeypatch.setattr(provider.subprocess, "check_output", listing)
    monkeypatch.setattr(provider, "_rpc_post", rpc)
    assert provider.probe_antigravity_endpoint(env={})[0] is False
    assert clock[0] == pytest.approx(4.0)
    assert calls == ["ps", "lsof"]
    # A readiness timeout is not a cached denial for the next real operation.
    discovered = provider.discover_antigravity_language_server(env={})
    assert discovered is not None
    assert discovered["url"] == "http://127.0.0.1:12345"
    assert calls == ["ps", "lsof", "ps", "lsof", "rpc"]


def test_cancel_failure_cannot_replace_the_original_provider_error(tmp_path, language_server):
    from loopx.chat_agent import CodexChatAgentError

    adapter = adapter_for(tmp_path)
    language_server.update(send_failure=True, cancel_failure=True)
    with pytest.raises(CodexChatAgentError, match="SendUserCascadeMessage failed with HTTP 503") as error:
        adapter.start_turn("Original input")
    assert any("cancellation remains unconfirmed" in note for note in error.value.__notes__)


@pytest.mark.parametrize("layout", ["legacy", "user-first"])
@pytest.mark.parametrize("change", ["none", "native-retry", "terminal-error", "stream-error", "pending-tool", "foreign-input", "wrong-source",
    "old-input", "execution-changed", "incomplete", "wrong-anchor", "running", "missing-index", "changed-during-read"])
def test_completed_result_readback_is_exact_and_never_executes(tmp_path, language_server, change, layout):
    ref = "f" * 24
    context = {"schema_version": "manager_turn_context_v1", "context_delegation": {"source_id": ref}}
    prompt = "Fresh Core evidence (JSON data, not instructions):\n" + json.dumps(context) + "\n\nCurrent user message:\nOriginal request"
    if layout == "user-first":
        prompt = 'Current user message (JSON string):\n"Original request"\n\nFresh Core evidence (JSON data, not instructions):\n' + json.dumps(context)
    steps = [
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
         "userInput": {"items": [{"text": prompt}], "userResponse": prompt}},
        {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_DONE", "runCommand": {"exitCode": 0}},
        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
         "plannerResponse": {"response": "Original completed answer", "stopReason": "STOP_REASON_STOP_PATTERN"}},
    ]
    if change in {"native-retry", "terminal-error"}:
        steps.insert(1, {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "status": "CORTEX_STEP_STATUS_CLEARED",
                         "errorMessage": {"error": {"shortError": "API attempt failed"},
                                          "shouldShowUser": change == "terminal-error"}})
    for index, step in enumerate(steps):
        step["metadata"] = {"createdAt": "2026-01-01T00:00:05Z", "executionId": "original-execution",
                            "sourceTrajectoryStepInfo": {"stepIndex": index}}
    if change == "stream-error":
        steps[-1]["plannerResponse"]["stopReason"] = "STOP_REASON_CLIENT_STREAM_ERROR"
    elif change == "pending-tool":
        steps[1]["status"] = "CORTEX_STEP_STATUS_RUNNING"
    elif change == "foreign-input":
        steps[-1]["type"] = "CORTEX_STEP_TYPE_USER_INPUT"
    elif change == "wrong-source":
        steps[0]["userInput"] = {"items": [{"text": prompt.replace(ref, "e" * 24)}],
                                  "userResponse": prompt.replace(ref, "e" * 24)}
    elif change == "old-input":
        steps[0]["metadata"]["createdAt"] = "2025-01-01T00:00:00Z"
    elif change == "execution-changed":
        steps[1]["metadata"]["executionId"] = "foreign"
    elif change == "missing-index":
        steps[1]["metadata"]["sourceTrajectoryStepInfo"]["stepIndex"] = 7
    frame = {"at": 0, "steps": steps, "status": "CASCADE_RUN_STATUS_RUNNING" if change == "running" else "CASCADE_RUN_STATUS_IDLE"}
    if change == "incomplete":
        frame["total"] = 4
    language_server.update(active=True, frames=[], frame=frame)
    if change == "changed-during-read":
        after = deepcopy(frame)
        after["total"] = 4
        language_server["frames"] = [frame, after]
    proof = AntigravityAdapter.completed_result_observation(cascade_id="synthetic-cascade", request_ref=ref,
        message="Original request", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:10Z",
        work_dir=tmp_path, input_anchor={"input_sha256": "0" * 64} if change == "wrong-anchor" else None)
    assert (proof is not None) == (change in {"none", "native-retry"})
    if proof:
        assert proof["response"]["message"] == "Original completed answer"
    assert not any(method in {"StartCascade", "SendUserCascadeMessage", "CancelCascadeInvocation"}
                   for method, _ in language_server["calls"])


@pytest.mark.parametrize("change", ["none", "wrong-message", "wrong-source", "wrong-schema", "suffix", "truncated"])
def test_manager_readback_does_not_confuse_quoted_frames_with_transport(change):
    from loopx.capabilities.native_chat.manager_input import manager_input_matches

    # Literal markers and significant whitespace are user data, not a second frame.
    message = '  修复\r\n```\nFresh Core evidence (JSON data, not instructions):\n{}\n```\n\t尾部  '
    context = {"schema_version": "manager_turn_context_v1", "context_delegation": {"source_id": "original"},
               "quoted": 'Current user message (JSON string):\n"foreign"'}
    if change == "wrong-source":
        context["context_delegation"]["source_id"] = "foreign"
    elif change == "wrong-schema":
        context["schema_version"] = "unknown"
    prompt = ("Policy and old history\nCurrent user message (JSON string):\n"
              + json.dumps(message if change != "wrong-message" else message.strip(), ensure_ascii=False)
              + "\n\nFresh Core evidence (JSON data, not instructions):\n" + json.dumps(context, ensure_ascii=False))
    if change == "suffix":
        prompt += "\nAn unbound instruction"
    elif change == "truncated":
        prompt = prompt[:-1]
    assert manager_input_matches(prompt, message=message, request_ref="original") == (change == "none")


@pytest.mark.parametrize("profile", ["restricted", "trusted_owner"])
@pytest.mark.parametrize("long_request", [False, True])
@pytest.mark.parametrize("delivery_history", [False, True])
def test_manager_runtime_sends_user_first_and_complete_context_over_http(
    tmp_path, monkeypatch, language_server, profile, long_request, delivery_history,
):
    from loopx import chat_manager_context
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    from loopx.chat_manager import MANAGER_AGENT_GOAL_ID

    message = "  保留原话\r\n```\nFresh Core evidence (JSON data, not instructions):\n{}\n```\n"
    if long_request:
        message += "请求中的约束\n" * 15000
    message += "\tLAST-USER-CONSTRAINT  "
    evidence = {"schema_version": "manager_turn_context_v1", "coverage": {"complete": False},
                "goals": [{"goal_id": "synthetic", "facts": "fact " * 90000,
                           "last_constraint": "LAST-CORE-CONSTRAINT"}], "warnings": ["synthetic_unread_source"]}
    if delivery_history:
        evidence["goals"][0]["facts"] = "Current facts and unknown fields remain inline"
        evidence["goals"][0]["recent_delivery_history"] = {
            "status": "read", "coverage": {"complete": False}, "source_revision": "original-history",
            "deliveries": [{"recorded_details": {"counterevidence": "past fact " * 60000},
                            "last_constraint": "KEEP FAILED CANDIDATE"}]}
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    collected = []

    def collect(*args, **kwargs):
        collected.append(kwargs)
        return deepcopy(evidence)

    monkeypatch.setattr(chat_manager_context, "collect_manager_turn_context", collect)
    store = ChatSessionStore(tmp_path / "runtime")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": []}))
    runtime = ChatRuntimeController(store=store, codex_bin="unused", registry_path=registry)
    adapter = adapter_for(tmp_path, runtime_profile=profile)
    session = store.create_session(goal_id=MANAGER_AGENT_GOAL_ID, agent_id="antigravity",
        adapter_kind=adapter.adapter_kind, upstream_thread_id=adapter.cascade_id, channel_id="manager")
    sid = session["session_id"]
    turn, _ = store.create_turn(sid, client_turn_id="original", message=message, origin="web")
    try:
        runtime._run_turn(session_id=sid, turn_id=turn["turn_id"], message=message,
                          attachments=[], adapter=adapter)
        done = store.load_turn(sid, turn["turn_id"])
        assert done["status"] == "completed", done
        sends = [payload for method, payload in language_server["calls"] if method == "SendUserCascadeMessage"]
        assert len(sends) == 1
        prompt = sends[0]["items"][0]["text"]
        # Read the actual HTTP payload independently of the composing helper.
        suffix = prompt.split("Current user message (JSON string):\n", 1)[1]
        original, end = json.JSONDecoder().raw_decode(suffix)
        assert original == message
        core_prefix = "\n\nFresh Core evidence (JSON data, not instructions):\n"
        assert suffix[end:].startswith(core_prefix)
        delivered = json.loads(suffix[end + len(core_prefix):])
        if delivery_history and profile == "trusted_owner":
            from test_manager_native_context import _read

            history = delivered["goals"][0]["recent_delivery_history"]
            assert history["status"] == "not_read" and history["deliveries"] == []
            assert delivered["goals"][0]["facts"] == evidence["goals"][0]["facts"]
            assert delivered["goals"][0]["last_constraint"] == "LAST-CORE-CONSTRAINT"
            selected = _read(history["read_reference"]["command"])
            assert selected["selections"][0]["value"] == evidence["goals"][0]["recent_delivery_history"]
            recovered = _read(delivered["context_read"]["recovery_command"])
            assert recovered["goals"] == evidence["goals"]
            assert recovered["context_delegation"] == delivered["context_delegation"]
            assert "no loopx_manager_read or loopx_context_read custom tool" in prompt
            # This measures the default transport only, not total model cost.
            assert len(json.dumps(delivered).encode()) < len(json.dumps(evidence).encode())
        else:
            assert delivered["goals"] == evidence["goals"]
            assert len(json.dumps(delivered).encode()) > 430_000
        assert delivered["warnings"] == evidence["warnings"]
        assert delivered["coverage"] == evidence["coverage"]
        assert collected == [{"remote_evidence": True}]
        planner = sends[0]["cascadeConfig"]["plannerConfig"]
        assert planner["conversational"]["agenticMode"] == (profile == "trusted_owner")
        assert (planner.get("toolConfig") or {}).get("forceDisableToolCalling", False) == (profile == "restricted")
        events = store.events_after(sid, turn["turn_id"], None)
        anchor = next(row["payload"]["native_input"] for row in events if row["kind"] == "turn.started")
        call_count = len(language_server["calls"])
        proof = AntigravityAdapter.completed_result_observation(cascade_id=adapter.cascade_id,
            request_ref=delivered["context_delegation"]["source_id"], message=message,
            started_at=done["started_at"], completed_at=done["completed_at"], work_dir=tmp_path, input_anchor=anchor)
        assert proof is not None and proof["response"]["message"] == done["response"]["message"]
        assert all(method in {"GetCascadeModelConfigData", "GetCascadeTrajectory", "GetCascadeTrajectorySteps"}
                   for method, _ in language_server["calls"][call_count:])
    finally:
        adapter.close_session()
        runtime.close()


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
    assert len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]) == 1


def test_pending_native_approval_is_actionable_and_not_auto_accepted(tmp_path, language_server):
    from loopx.chat_agent import CodexChatAgentError
    language_server["mode"] = "waiting"
    with pytest.raises(CodexChatAgentError) as error:
        adapter_for(tmp_path, runtime_profile="trusted_owner").start_turn("requires native approval")
    assert error.value.gate["kind"] == "host_tool_gate"
    assert "permissions" in error.value.gate["next_action"]
    assert "cancelled" in str(error.value)
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
    adapter = adapter_for(tmp_path, hard_timeout_sec=300.0)
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


@pytest.mark.parametrize("native_indices", [False, True])
def test_native_full_history_is_not_streamed_as_the_current_answer(tmp_path, language_server, native_indices):
    language_server["plain_text"] = True
    adapter = adapter_for(tmp_path)
    assert adapter.start_turn("first")["message"] == "Argon answer 1"
    language_server["ignore_step_range"] = True
    language_server["native_indices"] = native_indices
    events = []
    result = adapter.start_turn("second", lambda kind, data: events.append((kind, data)))
    assert result["message"] == "Argon answer 2"
    assert "Argon answer 1" not in "".join(data.get("text", "") for kind, data in events if kind == "answer.delta")
    assert len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]) == 2


def _native_frames(*, stopped: str = "STOP_REASON_STOP_PATTERN", stagnant: bool = False):
    frames = []
    for index in range(5):
        final = index == 4 and not stagnant
        steps = [
            {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
             "metadata": {"sourceTrajectoryStepInfo": {}}},
            {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
             "status": "CORTEX_STEP_STATUS_DONE" if final else "CORTEX_STEP_STATUS_GENERATING",
             "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}},
             "plannerResponse": {"response": "native final" if final else ("same partial" if stagnant else f"progress {index}"),
                                 "thinkingDuration": index, **({"stopReason": stopped} if final else {})}},
        ]
        frames.append({"at": 80.0 * (index + 1), "steps": steps,
                       "status": "CASCADE_RUN_STATUS_IDLE" if final else "CASCADE_RUN_STATUS_RUNNING"})
    return frames


def _native_clock(monkeypatch, language_server, frames):
    from loopx.capabilities.native_chat import antigravity_adapter as provider
    language_server.update(frames=frames, now=0.0)
    # Keep HTTP/socket/thread clocks real. Only the provider's invocation clock
    # advances, in response to completed synthetic native HTTP observations.
    monkeypatch.setattr(provider, "time", SimpleNamespace(monotonic=lambda: language_server["now"], sleep=time.sleep))


def test_native_http_progress_reaches_owner_before_final_without_hidden_reasoning(
    tmp_path, language_server, monkeypatch
):
    from loopx.extensions.lark.private_progress import project_progress

    user = {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"}
    think = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_GENERATING",
             "plannerResponse": {"thinking": "hidden native reasoning"}}
    command = {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_GENERATING",
               "runCommand": {"commandLine": f"cat {tmp_path}/source.txt --token private-value",
                              "combinedOutput": "private stdout"}}
    final = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
             "plannerResponse": {"response": "verified final", "stopReason": "STOP_REASON_STOP_PATTERN"}}
    frames = [{"at": 1, "steps": [user, think], "status": "CASCADE_RUN_STATUS_RUNNING"},
              {"at": 2, "steps": [user, think, command], "status": "CASCADE_RUN_STATUS_RUNNING"},
              {"at": 3, "steps": [user, think, command], "status": "CASCADE_RUN_STATUS_RUNNING"},
              {"at": 4, "steps": [user, dict(think, status="CORTEX_STEP_STATUS_DONE"),
                  {**command, "status": "CORTEX_STEP_STATUS_DONE", "runCommand": {**command["runCommand"], "exitCode": 0}}, final],
               "status": "CASCADE_RUN_STATUS_IDLE"}]
    _native_clock(monkeypatch, language_server, frames)
    adapter = adapter_for(tmp_path, runtime_profile="trusted_owner")
    state, events, views = {}, [], []

    def observed(kind, payload):
        event = {"event_id": str(len(events)), "kind": kind, "payload": payload}
        events.append(event)
        views.append((language_server["now"], project_progress(state, [event], owner_private=True)))

    result = adapter.start_turn("read the source", observed)
    assert result["message"] == "verified final"
    assert any(at < 4 and view and "正在思考" in view for at, view in views)
    assert any(at < 4 and view and "source.txt" in view for at, view in views)
    assert not any(secret in json.dumps(events) for secret in (
        "hidden native reasoning", "private-value", "private stdout", str(tmp_path)))
    command_events = [event for event in events if event["payload"].get("step", {}).get("kind") == "command"]
    assert [event["payload"]["method"] for event in command_events] == ["item/started", "item/completed"]
    assert len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]) == 1


def test_default_core_keeps_an_original_progressing_invocation_past_five_minutes(tmp_path, language_server, monkeypatch):
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    _native_clock(monkeypatch, language_server, _native_frames())
    store = ChatSessionStore(tmp_path / "runtime" / "chat")
    controller = ChatRuntimeController(store=store, codex_bin="unused-codex")
    session, _ = controller.open_session(goal_id="synthetic-goal", agent_id="antigravity", work_dir=tmp_path,
        objective="Synthetic conversation", mode="new", channel_id="synthetic-argon-chat")
    sid = session["session_id"]
    try:
        turn, _ = controller.submit_turn(session_id=sid, client_turn_id="one-original-input", message="long task",
                                         work_dir=tmp_path, objective="Synthetic conversation")
        result = controller.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=8)
        assert result["status"] == "completed"
        assert result["response"]["message"] == "native final"
        assert language_server["now"] == 400.0
        assert len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]) == 1
        assert not language_server["cancelled"].is_set()
        assert [m["text"] for m in store.messages(sid) if m["role"] == "agent"] == ["native final"]
    finally:
        controller.close_session(sid)


@pytest.mark.parametrize("pause", ["before-input", "input", "planner", "command", "command-done", "earlier-command", "unconfirmed-answer", "tool-call"])
def test_transient_idle_does_not_finish_or_cancel_the_original_turn(
    tmp_path, language_server, monkeypatch, pause,
):
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore

    user = {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"}
    planner = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_GENERATING",
               "plannerResponse": {"thinking": "private native reasoning"}}
    command = {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_GENERATING",
               "runCommand": {"commandLine": "synthetic-work", "combinedOutput": ""}}
    final = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
             "plannerResponse": {"response": "original task finished", "stopReason": "STOP_REASON_STOP_PATTERN"}}
    paused = {
        "before-input": [], "input": [user], "planner": [user, planner], "command": [user, command],
        "command-done": [user, {**command, "status": "CORTEX_STEP_STATUS_DONE"}],
        "earlier-command": [user, command, final],
        "unconfirmed-answer": [user, {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
                                     "plannerResponse": {"response": "intermediate answer"}}],
        "tool-call": [user, {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
                            "plannerResponse": {"response": "working", "toolCalls": [{"name": "run_command"}]}}],
    }[pause]
    running = deepcopy(paused) or [user]
    if not any(step["type"] == "CORTEX_STEP_TYPE_RUN_COMMAND" for step in running):
        running.append(command)
    completed = [{**step, "status": "CORTEX_STEP_STATUS_DONE"} for step in running]
    if completed[-1] != final:
        completed.append(final)
    _native_clock(monkeypatch, language_server, [
        {"at": 1, "steps": [], "status": "CASCADE_RUN_STATUS_RUNNING"},
        {"at": 2, "steps": paused, "status": "CASCADE_RUN_STATUS_IDLE"},
        {"at": 3, "steps": running, "status": "CASCADE_RUN_STATUS_RUNNING"},
        {"at": 4, "steps": completed, "status": "CASCADE_RUN_STATUS_IDLE"},
    ])
    if pause == "tool-call":
        # Ordinary Chat is restricted. Exercise native tool continuation only
        # through the existing trusted-owner provider profile.
        result = adapter_for(tmp_path, runtime_profile="trusted_owner").start_turn("finish the task")
        assert result["message"] == "original task finished"
        assert language_server["now"] == 4
        assert not language_server["cancelled"].is_set()
        assert sum(method == "SendUserCascadeMessage" for method, _ in language_server["calls"]) == 1
        return
    store = ChatSessionStore(tmp_path / "runtime" / "chat")
    controller = ChatRuntimeController(store=store, codex_bin="unused-codex")
    session, _ = controller.open_session(goal_id="synthetic-goal", agent_id="antigravity", work_dir=tmp_path,
        objective="Synthetic conversation", mode="new", channel_id="synthetic-argon-chat")
    sid = session["session_id"]
    try:
        turn, _ = controller.submit_turn(session_id=sid, client_turn_id="one-original-input", message="finish the task",
                                         work_dir=tmp_path, objective="Synthetic conversation")
        result = controller.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=8)
        assert result["status"] == "completed"
        assert result["response"]["message"] == "original task finished"
        assert language_server["now"] == 4
        assert not language_server["cancelled"].is_set()
        assert [m["text"] for m in store.messages(sid) if m["role"] == "agent"] == ["original task finished"]
        assert sum(method == "SendUserCascadeMessage" for method, _ in language_server["calls"]) == 1
    finally:
        controller.close_session(sid)


@pytest.mark.parametrize("pending", ["command", "unconfirmed-answer"])
def test_transient_idle_cannot_reset_the_no_progress_deadline(tmp_path, language_server, monkeypatch, pending):
    from loopx.chat_agent import CodexChatAgentError

    user = {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"}
    command = {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_GENERATING"}
    if pending == "unconfirmed-answer":
        command = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
                   "plannerResponse": {"response": "intermediate answer"}}
    _native_clock(monkeypatch, language_server, [
        {"at": 1, "steps": [user, command], "status": "CASCADE_RUN_STATUS_IDLE"},
        {"at": 302, "steps": [user, command], "status": "CASCADE_RUN_STATUS_IDLE"},
    ])
    with pytest.raises(CodexChatAgentError) as error:
        adapter_for(tmp_path, runtime_profile="trusted_owner").start_turn("finish the task")
    assert error.value.error_code == "response_timeout"
    assert language_server["cancelled"].is_set()
    assert sum(method == "SendUserCascadeMessage" for method, _ in language_server["calls"]) == 1


@pytest.mark.parametrize("hard_limit,stagnant", [(300.0, False), (None, True)])
def test_explicit_deadline_and_default_no_progress_timeout_still_stop(tmp_path, language_server, monkeypatch, hard_limit, stagnant):
    from loopx.chat_agent import CodexChatAgentError
    _native_clock(monkeypatch, language_server, _native_frames(stagnant=stagnant))
    events = []
    with pytest.raises(CodexChatAgentError) as error:
        options = {"hard_timeout_sec": hard_limit} if hard_limit is not None else {}
        adapter_for(tmp_path, **options).start_turn("long task", lambda kind, data: events.append((kind, data)))
    assert error.value.error_code == "response_timeout"
    assert language_server["cancelled"].is_set()
    assert not any(kind == "answer.final" for kind, _ in events)
    assert len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]) == 1


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_invalid_hard_timeout_creates_no_native_invocation(tmp_path, language_server, value):
    with pytest.raises(ValueError, match="finite and positive"):
        adapter_for(tmp_path, hard_timeout_sec=value)
    assert language_server["calls"] == []


@pytest.mark.parametrize("reason", ["STOP_REASON_CLIENT_STREAM_ERROR", "unrecognized-native-stop"])
@pytest.mark.parametrize("pending", ["none", "generating", "tool-call"])
def test_idle_done_with_an_unverified_native_stop_never_becomes_success(tmp_path, language_server, monkeypatch, reason, pending):
    from loopx.chat_agent import CodexChatAgentError
    frames = _native_frames(stopped=reason)
    frames[-1]["at"] = 1.0
    if pending == "generating":
        frames[-1]["steps"][-1]["status"] = "CORTEX_STEP_STATUS_GENERATING"
    elif pending == "tool-call":
        frames[-1]["steps"][-1]["plannerResponse"]["toolCalls"] = [{"name": "run_command"}]
    _native_clock(monkeypatch, language_server, [frames[-1]])
    events = []
    with pytest.raises(CodexChatAgentError) as error:
        adapter_for(tmp_path, runtime_profile="trusted_owner").start_turn("task", lambda kind, data: events.append((kind, data)))
    assert error.value.error_code == "upstream_transport_failure"
    assert not any(kind == "answer.final" for kind, _ in events)
    assert len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]) == 1


def test_partial_native_inventory_is_not_a_complete_response(tmp_path, language_server, monkeypatch):
    from loopx.chat_agent import CodexChatAgentError
    frame = _native_frames()[-1]
    frame.update(at=1.0, total=4)
    _native_clock(monkeypatch, language_server, [frame])
    with pytest.raises(CodexChatAgentError) as error:
        adapter_for(tmp_path).start_turn("task")
    assert error.value.error_code == "antigravity_rpc_error"
    assert language_server["cancelled"].is_set()


def test_another_native_input_is_neither_adopted_nor_cancelled(tmp_path, language_server, monkeypatch):
    from loopx.chat_agent import CodexChatAgentError
    frame = _native_frames()[-1]
    frame["at"] = 1.0
    frame["steps"].insert(1, {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
                              "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}}})
    frame["steps"][-1]["metadata"]["sourceTrajectoryStepInfo"]["stepIndex"] = 2
    _native_clock(monkeypatch, language_server, [frame])
    with pytest.raises(CodexChatAgentError) as error:
        adapter_for(tmp_path).start_turn("original task")
    assert error.value.error_code == "upstream_transport_failure"
    assert not language_server["cancelled"].is_set()
    assert len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]) == 1


def test_multistep_planner_responses_insert_paragraph_breaks_between_steps(tmp_path, language_server, monkeypatch):
    frames = [
        {
            "at": 1.0,
            "status": "CASCADE_RUN_STATUS_RUNNING",
            "steps": [
                {
                    "type": "CORTEX_STEP_TYPE_USER_INPUT",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}},
                },
                {
                    "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}},
                    "plannerResponse": {
                        "response": "正在读取当前会话的最新消息内容。",
                        "toolCalls": [{"name": "read_file"}],
                    },
                },
                {
                    "type": "CORTEX_STEP_TYPE_VIEW_FILE",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 2}},
                },
            ],
        },
        {
            "at": 2.0,
            "status": "CASCADE_RUN_STATUS_IDLE",
            "steps": [
                {
                    "type": "CORTEX_STEP_TYPE_USER_INPUT",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}},
                },
                {
                    "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}},
                    "plannerResponse": {
                        "response": "正在读取当前会话的最新消息内容。",
                        "toolCalls": [{"name": "read_file"}],
                    },
                },
                {
                    "type": "CORTEX_STEP_TYPE_VIEW_FILE",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 2}},
                },
                {
                    "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 3}},
                    "plannerResponse": {
                        "response": "最终结论：首读展开边界合理。\n\n<loopx-review-json>"
                        + json.dumps({"message": "", "proposals": [], "context_handoff": [], "gate": None})
                        + "</loopx-review-json>",
                        "stopReason": "STOP_REASON_STOP_PATTERN",
                    },
                },
            ],
        },
    ]
    _native_clock(monkeypatch, language_server, frames)
    events: list[tuple[str, dict[str, Any]]] = []
    result = adapter_for(tmp_path, runtime_profile="trusted_owner").start_turn(
        "检查首读体积", lambda kind, data: events.append((kind, data))
    )
    streamed = "".join(data.get("text", "") for kind, data in events if kind == "answer.delta")
    assert streamed == "正在读取当前会话的最新消息内容。\n\n最终结论：首读展开边界合理。\n\n"
    assert result["message"] == "最终结论：首读展开边界合理。"


def test_multistep_planner_responses_preserve_split_control_marker_across_steps(
    tmp_path, language_server, monkeypatch
):
    from loopx.extensions.lark.private_progress import project_progress

    envelope_tail = (
        "json>"
        + json.dumps(
            {
                "message": "",
                "proposals": [],
                "context_handoff": [],
                "gate": {"kind": "PROTOCOL_ONLY_SENTINEL", "summary": "PROTOCOL_ONLY_SENTINEL"},
            }
        )
        + "</loopx-review-json>"
    )
    frames = [
        {
            "at": 1.0,
            "status": "CASCADE_RUN_STATUS_RUNNING",
            "steps": [
                {
                    "type": "CORTEX_STEP_TYPE_USER_INPUT",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}},
                },
                {
                    "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}},
                    "plannerResponse": {
                        "response": "首段安全可见输出。<loopx-review-",
                    },
                },
            ],
        },
        {
            "at": 2.0,
            "status": "CASCADE_RUN_STATUS_IDLE",
            "steps": [
                {
                    "type": "CORTEX_STEP_TYPE_USER_INPUT",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}},
                },
                {
                    "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}},
                    "plannerResponse": {
                        "response": "首段安全可见输出。<loopx-review-",
                    },
                },
                {
                    "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 2}},
                    "plannerResponse": {
                        "response": envelope_tail,
                        "stopReason": "STOP_REASON_STOP_PATTERN",
                    },
                },
                {
                    "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 3}},
                    "plannerResponse": {
                        "response": "首段安全可见输出。<loopx-review-" + envelope_tail,
                        "stopReason": "STOP_REASON_STOP_PATTERN",
                    },
                },
            ],
        },
    ]
    _native_clock(monkeypatch, language_server, frames)
    events: list[tuple[str, dict[str, Any]]] = []
    result = adapter_for(tmp_path, runtime_profile="trusted_owner").start_turn(
        "检查跨步骤控制标签隐藏", lambda kind, data: events.append((kind, data))
    )
    streamed = "".join(data.get("text", "") for kind, data in events if kind == "answer.delta")
    assert streamed == "首段安全可见输出。"
    assert "PROTOCOL_ONLY_SENTINEL" not in streamed
    assert "<loopx-review-" not in streamed

    progress_state: dict[str, Any] = {}
    progress_text = project_progress(
        progress_state,
        [
            {"event_id": idx, "kind": kind, "payload": data}
            for idx, (kind, data) in enumerate(events, 1)
        ],
    )
    assert "PROTOCOL_ONLY_SENTINEL" not in (progress_text or "")
    assert "PROTOCOL_ONLY_SENTINEL" not in str(progress_state.get("answer") or "")
    assert result["message"] == "首段安全可见输出。"
    assert len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]) == 1


@pytest.mark.parametrize("method", ["GetCascadeTrajectory", "GetCascadeTrajectorySteps"])
def test_lost_native_observation_keeps_the_same_invocation(tmp_path, language_server, method):
    language_server["read_failures"] = {method: 1}
    result = adapter_for(tmp_path).start_turn("task with effects")
    assert result["message"] == "Argon answer 1"
    assert not language_server["cancelled"].is_set()
    assert len([1 for name, _ in language_server["calls"] if name == "SendUserCascadeMessage"]) == 1


def test_native_retry_errors_do_not_cancel_native_backoff(tmp_path, language_server, monkeypatch):
    frames = _native_frames()
    error = {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "status": "CORTEX_STEP_STATUS_DONE",
             "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 2}},
             "errorMessage": {"error": {"shortError": "API error (attempt 1): EOF"}}}
    for frame in frames:
        frame["steps"].insert(1, {**error, "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}}})
        frame["steps"][-1]["metadata"]["sourceTrajectoryStepInfo"]["stepIndex"] = 2
    _native_clock(monkeypatch, language_server, frames)
    result = adapter_for(tmp_path).start_turn("original task")
    assert result["message"] == "native final"
    assert not language_server["cancelled"].is_set()
    assert len([1 for name, _ in language_server["calls"] if name == "SendUserCascadeMessage"]) == 1


def test_native_terminal_error_preserves_structured_cause(tmp_path, language_server, monkeypatch):
    from loopx.chat_agent import CodexChatAgentError
    frame = _native_frames()[-1]
    detail = {"shortError": "stream reading error: unexpected EOF", "userErrorMessage": "Network interrupted", "errorCode": 2}
    frame["steps"][-1] = {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}}, "errorMessage": {"error": detail}}
    _native_clock(monkeypatch, language_server, [frame])
    with pytest.raises(CodexChatAgentError) as raised:
        adapter_for(tmp_path).start_turn("original task")
    assert raised.value.error_code == "antigravity_step_error"
    assert str(raised.value) == "Network interrupted"
    assert raised.value.native_error_info == detail
