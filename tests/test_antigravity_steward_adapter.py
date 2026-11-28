"""Focused tests for the Antigravity Steward & Chat adapter."""

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
from loopx.chat import CHAT_RESPONSE_EXAMPLE_SUFFIX
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
            if "raw_rpc_response" in state:
                status, raw_body = state["raw_rpc_response"]
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw_body)))
                self.end_headers()
                self.wfile.write(raw_body)
                return
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
                response = {"clientModelConfigs": state.get("model_configs", [
                    {"modelId": f"gemini-4-argon-{tier}",
                     "modelOrAlias": {"model": f"MODEL_PLACEHOLDER_M{number}"}}
                    for tier, number in (("high", 417), ("medium", 418), ("low", 419))])}
            elif method == "GetUserStatus":
                from datetime import timedelta
                reset = state.setdefault("quota_reset", (datetime.now(timezone.utc) + timedelta(days=7)).isoformat())
                response = state["user_status_response"] if "user_status_response" in state else {
                    "userStatus": {"email": state.get("quota_account", "synthetic@invalid.example"),
                    "cascadeModelConfigData": {"clientModelConfigs": [{
                    "modelId": "gemini-4-argon-high", "quotaInfo": {
                        "remainingFraction": state.get("quota_fraction", 1), "resetTime": reset}}]}}}
                if state.get("quota_unknown"):
                    response = {}
            elif method == "StartCascade":
                response = {"cascadeId": "synthetic-cascade"}
            elif method == "GetAllCascadeTrajectories":
                status = "CASCADE_RUN_STATUS_RUNNING" if state["active"] else "CASCADE_RUN_STATUS_IDLE"
                summaries = {} if state.get("missing_resume") else {"synthetic-cascade": {
                    "status": status, "stepCount": state["sent_count"] * 2,
                }}
                response = {"trajectorySummaries": summaries}
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
                    answer = (state.get("answers") or {}).get(index, f"Argon answer {index + 1}")
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
                         "userInput": {"items": [{"text": native["text"]}]}},
                        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                         "metadata": {**meta, "sourceTrajectoryStepInfo": {"stepIndex": index * 2 + 1}},
                         "status": "CORTEX_STEP_STATUS_WAITING" if state["mode"] == "waiting" else "CORTEX_STEP_STATUS_DONE",
                         "plannerResponse": {"thinking": "synthetic status", "response": answer,
                                             "stopReason": "STOP_REASON_STOP_PATTERN"}}])
                    if index in state.get("quota_error_indices", []):
                        final = response["steps"][-1]
                        final["type"] = "CORTEX_STEP_TYPE_ERROR_MESSAGE"
                        final.pop("plannerResponse")
                        final["errorMessage"] = {"error": {"userErrorMessage": "Individual quota reached. Resets in 123h.",
                            "errorCode": 429, "rpcErrorDetails": [json.dumps({
                                "@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "QUOTA_EXHAUSTED",
                                "domain": "cloudcode-pa.googleapis.com", "metadata": {"model": "gemini-4-argon-high"}})]}}
                    if index in state.get("cleared_input_indices", []):
                        for step in response["steps"][-2:]:
                            step["status"] = "CORTEX_STEP_STATUS_CLEARED"
                        response["steps"][-1].get("plannerResponse", {}).pop("stopReason", None)
                    if index > 0:
                        final = response["steps"][-1]
                        fault = state.get("review_native_fault") if index >= state.get("review_fault_start_index", 1) else None
                        if fault == "tool-call":
                            final["plannerResponse"]["toolCalls"] = [{"name": "run_command"}]
                        elif fault == "tool-step":
                            final["type"] = "CORTEX_STEP_TYPE_RUN_COMMAND"
                        elif fault == "mixed-execution":
                            final["metadata"]["executionId"] = "another-execution"
                        elif fault == "missing-stop":
                            final["plannerResponse"]["stopReason"] = None
                        elif fault == "conflicting-input":
                            response["steps"][-2]["userInput"]["userResponse"] = "Different input"
                        elif fault == "response-only":
                            response["steps"][-2]["userInput"] = {"userResponse": native["text"]}
                        elif fault == "extra-input":
                            response["steps"][-2]["userInput"]["items"].append({"text": "Different input"})
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


@pytest.mark.parametrize("fault", [None, "tool", "later-input", "cleared", "running", "wrong-anchor",
    "wrong-source", "wrong-code", "prose-only", "different-execution", "changed-during-read"])
def test_native_quota_readback_proves_no_execution_without_model_or_control_calls(language_server, monkeypatch, fault):
    import hashlib
    from loopx.capabilities.native_chat import antigravity_adapter as native

    ref = "f" * 24
    context = {"schema_version": "manager_turn_context_v1", "context_delegation": {"source_id": ref}}
    prompt = ('Current user message (JSON string):\n"Original request"\n\n'
              'Fresh Core evidence (JSON data, not instructions):\n' + json.dumps(context))
    meta = {"createdAt": "2026-01-01T00:00:05Z", "executionId": "original"}
    steps = [{"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
        "userInput": {"items": [{"text": prompt}]}},
        {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "status": "CORTEX_STEP_STATUS_DONE",
         "metadata": {"createdAt": "2026-01-01T00:00:06Z"}, "errorMessage": {"error": {
            "errorCode": 429, "rpcErrorDetails": [json.dumps({"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": "QUOTA_EXHAUSTED", "domain": "cloudcode-pa.googleapis.com",
                "metadata": {"model": "gemini-4-argon-high"}})]}}}]
    anchor = {"thread_id": "synthetic-cascade", "start_step_index": 0,
              "input_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
    if fault == "tool":
        steps[0]["runCommand"] = {"commandLine": "must never replay"}
    elif fault == "later-input":
        steps.append(deepcopy(steps[0]))
    elif fault == "cleared":
        steps[0]["status"] = "CORTEX_STEP_STATUS_CLEARED"
    elif fault == "wrong-anchor":
        anchor["input_sha256"] = "0" * 64
    elif fault == "wrong-code":
        steps[1]["errorMessage"]["error"]["errorCode"] = 500
    elif fault == "prose-only":
        steps[1]["errorMessage"]["error"] = {"userErrorMessage": "Individual quota reached."}
    elif fault == "different-execution":
        steps[1]["metadata"]["executionId"] = "foreign"
    language_server.update(active=True, frames=[], frame={"at": 0, "steps": steps,
        "status": "CASCADE_RUN_STATUS_RUNNING" if fault == "running" else "CASCADE_RUN_STATUS_IDLE"})
    if fault == "changed-during-read":
        post = native._rpc_post
        def changed(*args, **kwargs):
            result = post(*args, **kwargs)
            if args[2] == "GetCascadeTrajectorySteps":
                steps[0]["userInput"]["items"][0]["text"] += "Changed"
            return result
        monkeypatch.setattr(native, "_rpc_post", changed)
    proof = AntigravityAdapter.quota_failure_observation(cascade_id="synthetic-cascade",
        request_ref="foreign" if fault == "wrong-source" else ref, message="Original request",
        started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:10Z", input_anchor=anchor)
    assert (proof is not None) == (fault is None)
    if proof:
        assert proof["pre_execution_quota_failure"] and proof["only_original_user_item"]
        assert proof["input_sha256"] == anchor["input_sha256"]
    assert {method for method, _ in language_server["calls"]} <= {
        "GetCascadeModelConfigData", "GetCascadeTrajectory", "GetCascadeTrajectorySteps"}


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
    "old-input", "execution-changed", "incomplete", "wrong-anchor", "running", "missing-index", "changed-during-read",
    "items-only", "conflicting-response", "response-only", "extra-item", "non-text", "cleared-history"])
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
    elif change == "items-only":
        steps[0]["userInput"].pop("userResponse")
    elif change == "conflicting-response":
        steps[0]["userInput"]["userResponse"] = "Different input"
    elif change == "response-only":
        steps[0]["userInput"].pop("items")
    elif change == "extra-item":
        steps[0]["userInput"]["items"].append({"text": "Different input"})
    elif change == "non-text":
        steps[0]["userInput"]["items"] = [{"image": "unverified"}]
    elif change == "cleared-history":
        steps[0]["userInput"].pop("userResponse")
        for step in steps:
            step["status"] = "CORTEX_STEP_STATUS_CLEARED"
        steps[-1]["plannerResponse"].pop("stopReason")
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
    assert (proof is not None) == (change in {"none", "native-retry", "items-only"})
    if proof:
        assert proof["response"]["message"] == "Original completed answer"
    assert not any(method in {"StartCascade", "SendUserCascadeMessage", "CancelCascadeInvocation"}
                   for method, _ in language_server["calls"])


@pytest.mark.parametrize("fault", [None, "no-anchor", "wrong-anchor", "mixed-execution", "unfinished", "stream-error"])
def test_historical_completed_result_stays_inside_its_original_anchored_input(tmp_path, language_server, fault):
    import hashlib

    ref = "f" * 24
    context = {"schema_version": "manager_turn_context_v1", "context_delegation": {"source_id": ref}}
    prompt = ('Current user message (JSON string):\n"Original request"\n\n'
              'Fresh Core evidence (JSON data, not instructions):\n' + json.dumps(context))
    steps = [
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "userInput": {"items": [{"text": prompt}]}},
        {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "runCommand": {"exitCode": 0}},
        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "plannerResponse": {
            "response": "Original complete answer", "stopReason": "STOP_REASON_STOP_PATTERN"}},
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "userInput": {"items": [{"text": "Later input"}], "userResponse": "Later input"}},
        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "plannerResponse": {
            "response": "Different later answer", "stopReason": "STOP_REASON_STOP_PATTERN"}},
    ]
    for index, step in enumerate(steps):
        step.update(status="CORTEX_STEP_STATUS_DONE", metadata={
            "createdAt": "2026-01-01T00:00:05Z" if index < 3 else "2026-01-01T00:01:00Z",
            "executionId": "original" if index < 3 else "later",
            "sourceTrajectoryStepInfo": {"stepIndex": index}})
    anchor = {"thread_id": "synthetic-cascade", "start_step_index": 0,
              "input_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
    if fault == "wrong-anchor":
        anchor["input_sha256"] = "0" * 64
    elif fault == "mixed-execution":
        steps[1]["metadata"]["executionId"] = "later"
    elif fault == "unfinished":
        steps[1]["status"] = "CORTEX_STEP_STATUS_RUNNING"
    elif fault == "stream-error":
        steps[2]["plannerResponse"]["stopReason"] = "STOP_REASON_CLIENT_STREAM_ERROR"
    language_server.update(active=True, frames=[], frame={"at": 0, "steps": steps, "status": "CASCADE_RUN_STATUS_IDLE"})
    proof = AntigravityAdapter.completed_result_observation(cascade_id="synthetic-cascade", request_ref=ref,
        message="Original request", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:10Z",
        work_dir=tmp_path, input_anchor=None if fault == "no-anchor" else anchor)
    assert (proof is not None) == (fault is None)
    if proof:
        assert proof["response"]["message"] == "Original complete answer"
        assert proof["execution_id"] == "original" and proof["end_step_index"] == 3
        assert proof["latest_input_verified"] is False and proof["total_steps"] == 5
    assert {method for method, _ in language_server["calls"]} <= {
        "GetCascadeModelConfigData", "GetCascadeTrajectory", "GetCascadeTrajectorySteps"}


@pytest.mark.parametrize("change", [
    "none", "response-example", "response-example-with-extra",
    "wrong-message", "wrong-source", "wrong-schema", "suffix", "truncated",
])
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
    elif change in {"response-example", "response-example-with-extra"}:
        prompt += CHAT_RESPONSE_EXAMPLE_SUFFIX
        if change == "response-example-with-extra":
            prompt += "\nAn unbound instruction"
    elif change == "truncated":
        prompt = prompt[:-1]
    assert manager_input_matches(prompt, message=message, request_ref="original") == (
        change in {"none", "response-example"}
    )


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
        evidence_text = suffix[end + len(core_prefix):]
        delivered, evidence_end = json.JSONDecoder().raw_decode(evidence_text)
        assert evidence_text[evidence_end:] == CHAT_RESPONSE_EXAMPLE_SUFFIX
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


@pytest.mark.parametrize("effort,placeholder", [
    ("high", "MODEL_PLACEHOLDER_M427"),
    ("medium", "MODEL_PLACEHOLDER_M437"),
    ("low", "MODEL_PLACEHOLDER_M438"),
])
def test_carbon_native_request_and_same_conversation_resume(tmp_path, language_server, effort, placeholder):
    from loopx.capabilities.native_chat.model_settings import observed_model_settings

    model = f"gemini-4-carbon-{effort}"
    language_server["model_configs"] = [{"modelId": model, "modelOrAlias": {"model": placeholder}}]
    adapter = adapter_for(tmp_path, executor_model={"model": "carbon", "reasoning_effort": effort})
    assert adapter.current_model_id == model
    assert observed_model_settings(adapter, effort, "carbon")["agent_reasoning_effort"] == effort
    adapter.start_turn("Synthetic Carbon request")
    adapter.close_session()
    resumed = adapter_for(tmp_path, resume_thread_id="synthetic-cascade",
                          executor_model={"model": model, "reasoning_effort": effort})
    resumed.start_turn("Continue the same synthetic conversation")
    sends = [body for method, body in language_server["calls"] if method == "SendUserCascadeMessage"]
    assert len(sends) == 2
    for body in sends:
        assert body["cascadeId"] == "synthetic-cascade"
        planner = body["cascadeConfig"]["plannerConfig"]
        assert planner["planModel"] == placeholder
        assert planner["requestedModel"] == {"model": placeholder}
        assert planner["toolConfig"]["forceDisableToolCalling"] is True
    assert sum(method == "StartCascade" for method, _ in language_server["calls"]) == 1


@pytest.mark.parametrize("configs", [[], [
    {"modelId": "another-model", "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M427"}},
], [{"modelId": "gemini-4-carbon-high", "modelOrAlias": "malformed"}]])
def test_carbon_not_offered_by_account_cannot_create_or_resume(tmp_path, language_server, configs):
    from loopx.chat_agent import CodexChatAgentError

    language_server["model_configs"] = configs
    with pytest.raises(CodexChatAgentError, match="did not offer"):
        adapter_for(tmp_path, resume_thread_id="original",
                    executor_model={"model": "gemini-4-carbon-high", "reasoning_effort": "high"})
    assert {method for method, _ in language_server["calls"]} == {"GetCascadeModelConfigData"}


@pytest.mark.parametrize("model,effort", [("carbon", "xhigh"), ("gemini-4-carbon-high", "low"),
                                         ("gemini-4-carbon-unknown", None)])
def test_invalid_carbon_selection_is_rejected_before_native_reads(tmp_path, language_server, model, effort):
    from loopx.chat_agent import CodexChatAgentError

    with pytest.raises(CodexChatAgentError) as error:
        adapter_for(tmp_path, executor_model={"model": model, "reasoning_effort": effort})
    assert error.value.error_code == "unsupported_configuration"
    assert language_server["calls"] == []


def test_model_discovery_http_reads_account_choices_without_execution_and_can_recover(
    tmp_path, language_server, monkeypatch,
):
    import http.client
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_store import ChatSessionStore

    store = ChatSessionStore(tmp_path / "runtime")
    runtime = ChatRuntimeController(store=store, codex_bin="unused")
    monkeypatch.setattr(runtime, "capabilities", lambda: [{"agent_id": "antigravity", "available": True}])
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.chat_store, server.runtime_controller = store, runtime
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def read():
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("POST", "/api/chat/agent-model-options", body=json.dumps({"agent_id": "antigravity"}),
                               headers={"Content-Type": "application/json",
                                        "Origin": f"http://127.0.0.1:{server.server_port}"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        status, argon = read()
        assert status == 200, argon
        assert [row["value"] for row in argon["model"]["options"]] == [
            "gemini-4-argon-high", "gemini-4-argon-medium", "gemini-4-argon-low"]
        assert argon["model"]["selected"] == "gemini-4-argon-high"
        language_server["model_configs"] = []
        status, failed = read()
        assert status == 424 and failed["error_code"] == "unsupported_configuration"
        language_server["model_configs"] = [
            {"modelId": "gemini-4-carbon-medium", "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M437"}},
            {"modelId": "unknown-model", "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M999"}},
        ]
        status, recovered = read()
        assert status == 200, recovered
        assert recovered["model"]["selected"] is None
        assert recovered["model"]["options"] == [{"value": "gemini-4-carbon-medium",
            "label": "Gemini 4 Carbon (Medium)", "description": "", "is_default": False,
            "default_reasoning_effort": "medium", "reasoning_efforts": ["medium"]}]
        assert [method for method, _ in language_server["calls"]] == ["GetCascadeModelConfigData"] * 3
        assert store.list_sessions() == []
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        runtime.close()


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


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, b'{"fixture":true}', {"fixture": True}),
        (200, b'{"fixture":[42,-7.25,5e2,"NaN","Infinity"]}', {"fixture": [42, -7.25, 500.0, "NaN", "Infinity"]}),
        (
            200,
            b'{"fixture":[1.7976931348623157e308,-1.7976931348623157e308,5e-324,-1e-400,'
            b'123456789012345678901234567890,"1e400"]}',
            {"fixture": [
                sys.float_info.max, -sys.float_info.max, 5e-324, -0.0, 123456789012345678901234567890, "1e400",
            ]},
        ),
        (200, b'{"fixture":1e400}', "antigravity_transport_error"),
        (200, b'{"fixture":{"nested":[-1e400]}}', "antigravity_transport_error"),
        (200, b'{"fixture":{"nested":[NaN]}}', "antigravity_transport_error"),
        (200, b'{"fixture":Infinity}', "antigravity_transport_error"),
        (200, b'{"fixture":-Infinity}', "antigravity_transport_error"),
        (200, '{"fixture":"before�after"}'.encode("utf-8"), {"fixture": "before�after"}),
        (200, b'{"fixture":"before\xffafter"}', "antigravity_transport_error"),
        (200, b'{"fixture":"incomplete\xe2\x82"}', "antigravity_transport_error"),
        (200, b"{}", {}),
        (200, b"", {}),
        (200, b" \n\t", {}),
        (200, b"[]", "antigravity_transport_error"),
        (200, b"null", "antigravity_transport_error"),
        (200, b"false", "antigravity_transport_error"),
        (200, b"42", "antigravity_transport_error"),
        (200, b'"fixture"', "antigravity_transport_error"),
        (200, b"{", "antigravity_transport_error"),
        (503, b"[]", "antigravity_rpc_error"),
        (503, b"\xff", "antigravity_rpc_error"),
        (503, b'{"fixture":1e400}', "antigravity_rpc_error"),
    ],
    ids=["object", "finite-numbers-and-text", "finite-float-boundaries", "float-overflow", "nested-negative-overflow",
         "nested-nan", "infinity", "negative-infinity",
         "valid-unicode", "invalid-utf8", "incomplete-utf8", "empty-object", "empty-body",
         "whitespace", "array", "null", "boolean", "number", "string", "malformed-json",
         "http-error", "http-error-invalid-utf8", "http-error-with-overflow"],
)
def test_rpc_validates_response_shape_and_keeps_typed_errors(language_server, status, body, expected):
    from loopx.capabilities.native_chat.antigravity_discovery import _rpc_post
    from loopx.chat_agent import CodexChatAgentError

    language_server["raw_rpc_response"] = (status, body)
    if isinstance(expected, str):
        with pytest.raises(CodexChatAgentError) as raised:
            _rpc_post(language_server["url"], "synthetic-csrf-token", "Fixture", {})
        assert raised.value.error_code == expected
    else:
        response = _rpc_post(language_server["url"], "synthetic-csrf-token", "Fixture", {})
        # JSON serialization also compares the sign of an underflow to zero.
        assert json.dumps(response, sort_keys=True) == json.dumps(expected, sort_keys=True)
    assert language_server["calls"] == [("Fixture", {})]


def test_loopback_rpc_works_without_tls_initialization(language_server):
    probe = """
import ssl
import sys

def unavailable_context(*args, **kwargs):
    raise ssl.SSLError("TLS initialization unavailable")

ssl.create_default_context = unavailable_context
ssl._create_default_https_context = unavailable_context

from loopx.capabilities.native_chat.antigravity_discovery import _rpc_post

reply = _rpc_post(sys.argv[1], "synthetic-csrf-token", "GetUserStatus", {})
assert reply["userStatus"]["email"] == "synthetic@invalid.example"
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, language_server["url"]],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert language_server["calls"] == [("GetUserStatus", {})]


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
             "plannerResponse": {
                 "thinking": f"Inspecting {tmp_path}/source.txt before answering.",
                 "rawThinking": "hidden native reasoning",
             }}
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
    assert any(at < 4 and view and "思考摘要" in view and "Inspecting source.txt before answering." in view for at, view in views)
    assert not any(view and "最近步骤" in view for _, view in views)
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
        adapter_for(tmp_path, runtime_profile="trusted_owner", idle_timeout_sec=300.0).start_turn("finish the task")
    assert error.value.error_code == "response_timeout"
    assert language_server["cancelled"].is_set()
    assert sum(method == "SendUserCascadeMessage" for method, _ in language_server["calls"]) == 1


@pytest.mark.parametrize("kind", ["RUN_COMMAND", "PLANNER_RESPONSE", "CHECKPOINT"])
def test_settled_native_cancellation_fails_without_waiting_or_cancelling_again(
    tmp_path, language_server, monkeypatch, kind,
):
    from loopx.chat_agent import CodexChatAgentError

    user = {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"}
    cancelled = {"type": f"CORTEX_STEP_TYPE_{kind}", "status": "CORTEX_STEP_STATUS_CANCELED"}
    frames = [{"at": at, "steps": [user, cancelled], "status": "CASCADE_RUN_STATUS_IDLE"}
              for at in (1, 302)]
    _native_clock(monkeypatch, language_server, frames)
    events = []
    with pytest.raises(CodexChatAgentError) as error:
        adapter_for(tmp_path, runtime_profile="trusted_owner").start_turn(
            "one original request", lambda kind, data: events.append((kind, data)))
    assert error.value.error_code == "upstream_transport_failure"
    assert "execution may have occurred" in str(error.value)
    assert language_server["now"] == 1
    assert not language_server["cancelled"].is_set()
    assert not any(kind == "answer.final" for kind, _ in events)
    assert sum(method == "SendUserCascadeMessage" for method, _ in language_server["calls"]) == 1


def test_settled_native_cancellation_releases_core_without_replaying(tmp_path, language_server, monkeypatch):
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore

    steps = [{"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"},
             {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_CANCELED"}]
    _native_clock(monkeypatch, language_server, [
        {"at": at, "steps": steps, "status": "CASCADE_RUN_STATUS_IDLE"} for at in (1, 302)])
    store = ChatSessionStore(tmp_path / "runtime")
    controller = ChatRuntimeController(store=store, codex_bin="unused-codex")
    session, _ = controller.open_session(goal_id="synthetic-goal", agent_id="antigravity", work_dir=tmp_path,
        objective="Synthetic conversation", mode="new", channel_id="synthetic-argon-chat")
    sid = session["session_id"]
    try:
        turn, _ = controller.submit_turn(session_id=sid, client_turn_id="cancelled-original",
            message="one original request", work_dir=tmp_path, objective="Synthetic conversation")
        result = controller.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=8)
        assert result["status"] == "failed"
        assert result["error_code"] == "upstream_transport_failure"
        assert store.load_session(sid)["active_turn_id"] is None
        assert language_server["now"] == 1
        assert not language_server["cancelled"].is_set()
        assert sum(method == "SendUserCascadeMessage" for method, _ in language_server["calls"]) == 1
        assert [m["text"] for m in store.messages(sid) if m["role"] == "user"] == ["one original request"]
        assert not any(e["kind"] == "answer.final" for e in store.events_after(sid, turn["turn_id"], None))
    finally:
        controller.close_session(sid)


def test_cancelled_tail_with_running_native_work_remains_transient(tmp_path, language_server, monkeypatch):
    frames = _native_frames()
    running = deepcopy(frames[0]["steps"])
    running.append({"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_CANCELED"})
    completed = [{**step, "status": "CORTEX_STEP_STATUS_DONE"} for step in running]
    final = deepcopy(frames[-1]["steps"][-1])
    final["metadata"]["sourceTrajectoryStepInfo"]["stepIndex"] = 3
    _native_clock(monkeypatch, language_server, [
        {"at": 1, "steps": running, "status": "CASCADE_RUN_STATUS_IDLE"},
        {"at": 2, "steps": [*completed, final], "status": "CASCADE_RUN_STATUS_IDLE"}])
    result = adapter_for(tmp_path, runtime_profile="trusted_owner").start_turn("original task")
    assert result["message"] == "native final"
    assert language_server["now"] == 2
    assert not language_server["cancelled"].is_set()


@pytest.mark.parametrize("hard_limit,stagnant", [(300.0, False), (None, True)])
def test_explicit_deadline_and_default_no_progress_timeout_still_stop(tmp_path, language_server, monkeypatch, hard_limit, stagnant):
    from loopx.chat_agent import CodexChatAgentError
    _native_clock(monkeypatch, language_server, _native_frames(stagnant=stagnant))
    events = []
    with pytest.raises(CodexChatAgentError) as error:
        options = {"hard_timeout_sec": hard_limit} if hard_limit is not None else {"idle_timeout_sec": 300.0}
        adapter_for(tmp_path, **options).start_turn("long task", lambda kind, data: events.append((kind, data)))
    assert error.value.error_code == "response_timeout"
    assert language_server["cancelled"].is_set()
    assert not any(kind == "answer.final" for kind, _ in events)
    assert len([1 for method, _ in language_server["calls"] if method == "SendUserCascadeMessage"]) == 1


@pytest.mark.parametrize("field", ["hard_timeout_sec", "idle_timeout_sec"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_invalid_hard_timeout_creates_no_native_invocation(tmp_path, language_server, field, value):
    with pytest.raises(ValueError, match="finite and positive"):
        adapter_for(tmp_path, **{field: value})
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
    language_server["cancel_failure"] = True
    _native_clock(monkeypatch, language_server, [frame])
    with pytest.raises(CodexChatAgentError) as raised:
        adapter_for(tmp_path).start_turn("original task")
    assert raised.value.error_code == "antigravity_step_error"
    assert str(raised.value) == "Network interrupted"
    assert raised.value.native_error_info == detail
    assert not language_server["cancelled"].is_set()
    assert not any(method == "CancelCascadeInvocation" for method, _ in language_server["calls"])


def test_repeated_native_error_messages_do_not_reset_no_progress_deadline(tmp_path, language_server, monkeypatch):
    from loopx.chat_agent import CodexChatAgentError
    user = {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
            "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}}}
    frames = []
    steps = [user]
    for idx, at in enumerate((1.0, 120.0, 240.0, 305.0), start=1):
        steps = [
            *steps,
            {
                "type": "CORTEX_STEP_TYPE_ERROR_MESSAGE",
                "status": "CORTEX_STEP_STATUS_DONE",
                "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": idx}},
                "errorMessage": {"error": {"shortError": f"API error (attempt {idx}): EOF"}},
            },
        ]
        frames.append({"at": at, "steps": steps, "status": "CASCADE_RUN_STATUS_RUNNING"})
    _native_clock(monkeypatch, language_server, frames)
    with pytest.raises(CodexChatAgentError) as raised:
        adapter_for(tmp_path, idle_timeout_sec=300.0).start_turn("original task")
    assert raised.value.error_code == "response_timeout"
    assert language_server["now"] == 305.0
    assert language_server["cancelled"].is_set()


def test_settled_intermediate_tool_error_or_cancellation_does_not_abort_completed_turn(
    tmp_path, language_server, monkeypatch
):
    steps = [
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
         "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}}},
        {"type": "CORTEX_STEP_TYPE_VIEW_FILE", "status": "CORTEX_STEP_STATUS_ERROR",
         "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}},
         "viewFile": {"absolutePathUri": (tmp_path / "missing.txt").as_uri()}},
        {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_CANCELED",
         "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 2}},
         "runCommand": {"commandLine": "echo cancelled"}},
        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
         "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 3}},
         "plannerResponse": {"response": "Recovered after tool error.", "stopReason": "STOP_REASON_STOP_PATTERN"}},
    ]
    _native_clock(monkeypatch, language_server, [
        {"at": 1.0, "steps": steps, "status": "CASCADE_RUN_STATUS_IDLE"},
    ])
    result = adapter_for(tmp_path, runtime_profile="trusted_owner").start_turn("recover from missing file")
    assert result["message"] == "Recovered after tool error."
    assert not language_server["cancelled"].is_set()


def test_antigravity_attachment_turn_sends_inline_media(tmp_path, language_server):
    import base64
    from loopx.capabilities.native_chat.codex_adapter import start_attachment_turn

    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
    adapter = adapter_for(tmp_path, runtime_profile="trusted_owner")
    result = start_attachment_turn(
        adapter,
        {"agent_id": "antigravity", "session_mode": "managed_runtime"},
        {"origin": "lark"},
        "inspect screenshot",
        lambda *_: None,
        [{"id": "img-1", "name": "screen.png", "mime_type": "image/png", "size": len(png_bytes), "data_url": data_url}],
    )
    assert result["message"] == "Argon answer 1"
    sends = [body for method, body in language_server["calls"] if method == "SendUserCascadeMessage"]
    assert len(sends) == 1
    assert sends[0]["media"] == [{
        "mimeType": "image/png",
        "inlineData": base64.b64encode(png_bytes).decode("ascii"),
        "description": "screen.png",
    }]


def test_carbon_and_argon_offered_together_with_argon_product_default(tmp_path, language_server, monkeypatch):
    import http.client
    from loopx.capabilities.native_chat.antigravity_adapter import (
        ANTIGRAVITY_MODEL_OPTIONS,
        ANTIGRAVITY_MODEL_PLACEHOLDERS,
    )
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_store import ChatSessionStore

    ordered_ids = [item["id"] for item in ANTIGRAVITY_MODEL_OPTIONS]
    assert ordered_ids[:3] == [
        "gemini-4-argon-high",
        "gemini-4-argon-medium",
        "gemini-4-argon-low",
    ]
    assert ANTIGRAVITY_MODEL_PLACEHOLDERS["gemini-4-carbon-high"] == "MODEL_PLACEHOLDER_M427"

    language_server["model_configs"] = [
        {"modelOrAlias": {"model": "MODEL_PLACEHOLDER_M417"}},
        {"modelOrAlias": {"model": "MODEL_PLACEHOLDER_M427"}},
    ]
    store = ChatSessionStore(tmp_path / "runtime")
    runtime = ChatRuntimeController(store=store, codex_bin="unused")
    monkeypatch.setattr(runtime, "capabilities", lambda: [{"agent_id": "antigravity", "available": True}])
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.chat_store, server.runtime_controller = store, runtime
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request(
                "POST",
                "/api/chat/agent-model-options",
                body=json.dumps({"agent_id": "antigravity"}),
                headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{server.server_port}"},
            )
            response = connection.getresponse()
            assert response.status == 200
            payload = json.loads(response.read())
        finally:
            connection.close()
        assert [row["value"] for row in payload["model"]["options"]] == [
            "gemini-4-argon-high",
            "gemini-4-carbon-high",
        ]
        assert payload["model"]["selected"] == "gemini-4-argon-high"
        assert payload["model"]["options"][0]["is_default"] is True
        assert payload["model"]["options"][1]["is_default"] is False
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        runtime.close()


def test_capacity_observation_matches_model_or_alias_without_model_id(tmp_path, language_server):
    from datetime import timedelta
    from loopx.capabilities.native_chat.antigravity_adapter import AntigravityAdapter

    language_server["model_configs"] = [
        {
            "label": "Gemini 4 Carbon (High)",
            "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M427"},
        }
    ]
    language_server["steps"] = [
        {
            "type": "CORTEX_STEP_TYPE_USER_INPUT",
            "status": "CORTEX_STEP_STATUS_DONE",
            "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}, "executionId": "exec-carbon"},
            "userInput": {"items": [{"text": "probe"}]},
        },
        {
            "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
            "status": "CORTEX_STEP_STATUS_DONE",
            "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}, "executionId": "exec-carbon"},
            "plannerResponse": {"response": "Carbon answer", "stopReason": "STOP_REASON_STOP_PATTERN"},
        },
    ]
    reset = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    language_server["user_status_response"] = {
        "userStatus": {
            "email": "synthetic@invalid.example",
            "cascadeModelConfigData": {
                "clientModelConfigs": [
                    {
                        "label": "Gemini 4 Carbon (High)",
                        "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M427"},
                        "quotaInfo": {"remainingFraction": 0.99, "resetTime": reset},
                    }
                ]
            },
        }
    }
    adapter = adapter_for(
        tmp_path,
        executor_model={"model": "gemini-4-carbon-high", "reasoning_effort": "high"},
    )
    adapter.start_turn("probe")
    reviewed = AntigravityAdapter.metadata_review_capacity(
        cascade_id="synthetic-cascade",
        model_id="gemini-4-carbon-high",
    )
    assert reviewed is not None and reviewed["model_id"] == "gemini-4-carbon-high"
    assert reviewed["remaining_fraction"] == 0.99
    endpoint_cap = AntigravityAdapter.endpoint_model_capacity(model_id="gemini-4-carbon-high")
    assert endpoint_cap is not None and endpoint_cap["model_id"] == "gemini-4-carbon-high"
    assert endpoint_cap["remaining_fraction"] == 0.99 and endpoint_cap["normal_completion"] is True


def test_antigravity_launch_guard_runs_pre_publication_once(tmp_path, language_server):
    from contextlib import contextmanager

    adapter = adapter_for(tmp_path)
    events = []

    @contextmanager
    def guard():
        events.append(("guard_enter", len([m for m, _ in language_server["calls"] if m == "SendUserCascadeMessage"])))
        yield
        events.append(("guard_exit", len([m for m, _ in language_server["calls"] if m == "SendUserCascadeMessage"])))

    adapter._launch_guard = guard
    result = adapter.start_turn("guarded turn")
    assert result["message"] == "Argon answer 1"
    assert events == [("guard_enter", 0), ("guard_exit", 1)]


def test_antigravity_endpoint_model_capacity_resolves_short_aliases_and_rejects_mismatches(
    language_server,
) -> None:
    from datetime import timedelta

    reset = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    language_server["user_status_response"] = {
        "userStatus": {
            "email": "synthetic@invalid.example",
            "cascadeModelConfigData": {
                "clientModelConfigs": [
                    {
                        "label": "Gemini 4 Argon (High)",
                        "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M417"},
                        "quotaInfo": {"remainingFraction": 0.42, "resetTime": reset},
                    },
                    {
                        "label": "Gemini 4 Carbon (High)",
                        "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M427"},
                        "quotaInfo": {"remainingFraction": 0.85, "resetTime": reset},
                    },
                    {
                        "label": "Gemini 4 Carbon (Medium)",
                        "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M437"},
                        "quotaInfo": {"remainingFraction": 0.0, "resetTime": reset},
                    },
                ]
            },
        }
    }

    for alias, effort, expected_canonical, expected_fraction, expected_exhausted in [
        ("carbon", None, "gemini-4-carbon-high", 0.85, False),
        ("CARBON", None, "gemini-4-carbon-high", 0.85, False),
        ("gemini-4-carbon", None, "gemini-4-carbon-high", 0.85, False),
        ("argon", None, "gemini-4-argon-high", 0.42, False),
        ("gemini-4-argon", None, "gemini-4-argon-high", 0.42, False),
        ("carbon", "medium", "gemini-4-carbon-medium", 0.0, True),
    ]:
        observed = AntigravityAdapter.endpoint_model_capacity(
            model_id=alias,
            reasoning_effort=effort,
        )
        assert observed is not None, (alias, effort)
        assert observed["model_id"] == expected_canonical
        assert observed["remaining_fraction"] == expected_fraction
        assert observed["quota_exhausted"] is expected_exhausted
        assert observed["normal_completion"] is (not expected_exhausted)

    calls_before = len(language_server["calls"])
    assert AntigravityAdapter.endpoint_model_capacity(model_id="unknown-model") is None
    assert AntigravityAdapter.endpoint_model_capacity(
        model_id="gemini-4-carbon-high", reasoning_effort="medium",
    ) is None
    assert AntigravityAdapter.endpoint_model_capacity(
        model_id="carbon", reasoning_effort="xhigh",
    ) is None
    assert len(language_server["calls"]) == calls_before


def test_discover_antigravity_ls_address_with_explicit_token(language_server, monkeypatch) -> None:
    from loopx.capabilities.native_chat.antigravity_adapter import discover_antigravity_language_server

    monkeypatch.delenv("ANTIGRAVITY_LS_URL", raising=False)
    monkeypatch.setenv("ANTIGRAVITY_LS_ADDRESS", language_server["url"].removeprefix("http://"))
    found = discover_antigravity_language_server()
    assert found is not None
    assert found["url"] == language_server["url"]
    assert found["source"] == "environment"


def test_repeated_native_error_plus_thinking_only_planner_retry_times_out_and_cancels(
    tmp_path, language_server, monkeypatch
) -> None:
    from loopx.chat_agent import CodexChatAgentError

    user = {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
            "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}}}
    frames = []
    steps = [user]
    step_idx = 1
    for at in (1.0, 120.0, 240.0, 305.0):
        steps = [
            *steps,
            {
                "type": "CORTEX_STEP_TYPE_ERROR_MESSAGE",
                "status": "CORTEX_STEP_STATUS_DONE",
                "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": step_idx}},
                "errorMessage": {"error": {"shortError": "The stream was interrupted."}},
            },
            {
                "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                "status": "CORTEX_STEP_STATUS_DONE",
                "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": step_idx + 1}},
                "plannerResponse": {"thinking": f"retrying thinking attempt {step_idx}"},
            },
        ]
        step_idx += 2
        frames.append({"at": at, "steps": steps, "status": "CASCADE_RUN_STATUS_RUNNING"})
    _native_clock(monkeypatch, language_server, frames)
    with pytest.raises(CodexChatAgentError) as raised:
        adapter_for(tmp_path, idle_timeout_sec=300.0).start_turn("original task")
    assert raised.value.error_code == "response_timeout"
    assert language_server["now"] == 305.0
    assert language_server["cancelled"].is_set()


def test_default_antigravity_adapter_has_no_idle_timeout_across_silent_gaps(
    tmp_path, language_server, monkeypatch
) -> None:
    user = {
        "type": "CORTEX_STEP_TYPE_USER_INPUT",
        "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}},
    }
    silent = {
        "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
        "status": "CORTEX_STEP_STATUS_GENERATING",
        "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}},
        "plannerResponse": {"thinking": "initial thought"},
    }
    final = {
        "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
        "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}},
        "plannerResponse": {
            "response": "completed after long quiet tool wait",
            "stopReason": "STOP_REASON_STOP_PATTERN",
        },
    }
    _native_clock(
        monkeypatch,
        language_server,
        [
            {"at": 1.0, "steps": [user, silent], "status": "CASCADE_RUN_STATUS_RUNNING"},
            {"at": 450.0, "steps": [user, silent], "status": "CASCADE_RUN_STATUS_RUNNING"},
            {"at": 900.0, "steps": [user, final], "status": "CASCADE_RUN_STATUS_IDLE"},
        ],
    )
    adapter = adapter_for(tmp_path)
    assert adapter.idle_timeout_sec is None
    result = adapter.start_turn("wait for quiet work")
    assert result["message"] == "completed after long quiet tool wait"
    assert language_server["now"] == 900.0
    assert not language_server["cancelled"].is_set()


def test_explicit_idle_timeout_allows_active_recovery_and_resets_error_fence_after_substantive_progress(
    tmp_path, language_server, monkeypatch
) -> None:
    from loopx.capabilities.native_chat.antigravity_adapter import _turn_progress_snapshot
    from loopx.chat_agent import CodexChatAgentError

    user = {
        "type": "CORTEX_STEP_TYPE_USER_INPUT",
        "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}},
    }
    err1 = {
        "type": "CORTEX_STEP_TYPE_ERROR_MESSAGE",
        "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}},
        "errorMessage": {"error": {"shortError": "The stream was interrupted."}},
    }
    retry1_done = {
        "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
        "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 2}},
        "plannerResponse": {"thinking": "interrupted retry 1"},
    }
    err2 = {
        "type": "CORTEX_STEP_TYPE_ERROR_MESSAGE",
        "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 3}},
        "errorMessage": {"error": {"shortError": "The stream was interrupted again."}},
    }
    # CORTEX_STEP_TYPE_GENERIC (tool result) resets the error fence, whereas SYSTEM_MESSAGE does not.
    sys_msg = {"type": "CORTEX_STEP_TYPE_SYSTEM_MESSAGE", "status": "CORTEX_STEP_STATUS_DONE"}
    generic_tool = {"type": "CORTEX_STEP_TYPE_GENERIC", "status": "CORTEX_STEP_STATUS_DONE"}
    post_think = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_GENERATING",
                  "plannerResponse": {"thinking": "resumed thinking"}}
    assert len(_turn_progress_snapshot(list(enumerate([user, err1, retry1_done, err2, sys_msg, post_think])))) == 2
    assert len(_turn_progress_snapshot(list(enumerate([user, err1, retry1_done, err2, generic_tool, post_think])))) == 4
    tool_planner = {
        "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
        "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 4}},
        "plannerResponse": {"thinking": "calling tool", "toolCalls": [{"id": "c1", "name": "run_command"}]},
    }
    tool_step = {
        "type": "CORTEX_STEP_TYPE_RUN_COMMAND",
        "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 5}},
        "runCommand": {"commandLine": "echo ok", "combinedOutput": "ok\n", "exitCode": 0},
    }
    frames = [
        {"at": 10.0, "steps": [user, err1, retry1_done, err2], "status": "CASCADE_RUN_STATUS_RUNNING"},
        {"at": 100.0, "steps": [user, err1, retry1_done, err2, tool_planner, tool_step], "status": "CASCADE_RUN_STATUS_RUNNING"},
        {"at": 280.0, "steps": [user, err1, retry1_done, err2, tool_planner, tool_step, {
            "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
            "status": "CORTEX_STEP_STATUS_GENERATING",
            "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 6}},
            "plannerResponse": {"thinking": "post-recovery synthesis part 1"},
        }], "status": "CASCADE_RUN_STATUS_RUNNING"},
        {"at": 460.0, "steps": [user, err1, retry1_done, err2, tool_planner, tool_step, {
            "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
            "status": "CORTEX_STEP_STATUS_GENERATING",
            "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 6}},
            "plannerResponse": {"thinking": "post-recovery synthesis part 2"},
        }], "status": "CASCADE_RUN_STATUS_RUNNING"},
        {"at": 640.0, "steps": [user, err1, retry1_done, err2, tool_planner, tool_step, {
            "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
            "status": "CORTEX_STEP_STATUS_DONE",
            "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 6}},
            "plannerResponse": {
                "thinking": "post-recovery synthesis complete",
                "response": "recovered after transient error",
                "stopReason": "STOP_REASON_STOP_PATTERN",
            },
        }], "status": "CASCADE_RUN_STATUS_IDLE"},
    ]
    _native_clock(monkeypatch, language_server, frames)
    recovered = adapter_for(
        tmp_path, runtime_profile="trusted_owner", idle_timeout_sec=300.0
    ).start_turn("recover after stream error")
    assert recovered["message"] == "recovered after transient error"
    assert language_server["now"] == 640.0
    assert not language_server["cancelled"].is_set()

    # Repeated ERROR -> GENERATING thinking -> DONE thinking without substantive output still bounds at 300s.
    language_server["active"] = False
    loop_steps = [user, err1, retry1_done, err2]
    loop_frames = [
        {"at": 10.0, "steps": loop_steps, "status": "CASCADE_RUN_STATUS_RUNNING"},
        {"at": 150.0, "steps": [*loop_steps, {
            "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
            "status": "CORTEX_STEP_STATUS_GENERATING",
            "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 4}},
            "plannerResponse": {"thinking": "looping generating thought"},
        }], "status": "CASCADE_RUN_STATUS_RUNNING"},
        {"at": 260.0, "steps": [*loop_steps, {
            "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
            "status": "CORTEX_STEP_STATUS_DONE",
            "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 4}},
            "plannerResponse": {"thinking": "looping settled thought"},
        }], "status": "CASCADE_RUN_STATUS_RUNNING"},
        {"at": 315.0, "steps": [*loop_steps, {
            "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
            "status": "CORTEX_STEP_STATUS_DONE",
            "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 4}},
            "plannerResponse": {"thinking": "looping settled thought"},
        }], "status": "CASCADE_RUN_STATUS_RUNNING"},
    ]
    _native_clock(monkeypatch, language_server, loop_frames)
    with pytest.raises(CodexChatAgentError) as loop_err:
        adapter_for(tmp_path, idle_timeout_sec=300.0).start_turn("looping error task")
    assert loop_err.value.error_code == "response_timeout"
    assert language_server["now"] == 315.0


def test_isolated_cascade_preserves_main_cascade_and_pending_history(tmp_path, monkeypatch) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state: dict[str, Any] = {
        "calls": [],
        "cascade_seq": 0,
        "prompts_by_cascade": {},
    }
    token = "synthetic-csrf-token"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = self.path.rsplit("/", 1)[-1]
            state["calls"].append((method, body))
            if method == "GetCascadeModelConfigData":
                resp = {"clientModelConfigs": [{"modelId": "gemini-4-argon-high", "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M417"}}]}
            elif method == "StartCascade":
                state["cascade_seq"] += 1
                resp = {"cascadeId": f"cascade-{state['cascade_seq']}"}
            elif method == "GetCascadeTrajectory":
                cid = body["cascadeId"]
                prompts = state["prompts_by_cascade"].get(cid, [])
                resp = {"trajectory": {"cascadeId": cid}, "numTotalSteps": len(prompts) * 2, "status": "CASCADE_RUN_STATUS_IDLE"}
            elif method == "SendUserCascadeMessage":
                cid = body["cascadeId"]
                state["prompts_by_cascade"].setdefault(cid, []).append(body["items"][0]["text"])
                resp = {}
            elif method == "GetCascadeTrajectorySteps":
                cid = body["cascadeId"]
                prompts = state["prompts_by_cascade"].get(cid, [])
                answer = "<loopx-review-json>" + json.dumps({"message": f"Answer from {cid}", "proposals": [], "gate": None}) + "</loopx-review-json>"
                steps = []
                for idx, text in enumerate(prompts):
                    meta = {"createdAt": "2026-11-22T01:00:00Z", "executionId": f"exec-{cid}-{idx}"}
                    steps.extend([
                        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
                         "metadata": {**meta, "sourceTrajectoryStepInfo": {"stepIndex": idx * 2}},
                         "userInput": {"items": [{"text": text}]}},
                        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
                         "metadata": {**meta, "sourceTrajectoryStepInfo": {"stepIndex": idx * 2 + 1}},
                         "plannerResponse": {"response": answer, "stopReason": "STOP_REASON_STOP_PATTERN"}},
                    ])
                resp = {"steps": steps[body["startIndex"]:body["endIndex"]]}
            elif method == "CancelCascadeInvocation":
                resp = {}
            else:
                raise AssertionError(method)
            payload = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("ANTIGRAVITY_LS_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", token)
    try:
        adapter = adapter_for(tmp_path, history=[{"role": "user", "content": "keep prior constraint"}])
        assert adapter.cascade_id == "cascade-1"
        events: list[tuple[str, dict[str, Any]]] = []
        isolated = adapter.start_turn("Run isolated report", lambda k, p: events.append((k, p)), isolated_cascade=True)
        assert isolated["message"] == "Answer from cascade-2"
        assert adapter.cascade_id == "cascade-1" and adapter.upstream_thread_id == "cascade-1"
        assert adapter._pending_history == [{"role": "user", "content": "keep prior constraint"}]
        assert "keep prior constraint" not in state["prompts_by_cascade"]["cascade-2"][0]
        assert next(p["threadId"] for k, p in events if k == "turn.started") == "cascade-2"
        assert next(p["thread_id"] for k, p in events if k == "turn.native_result") == "cascade-2"

        main_resp = adapter.start_turn("Main conversation request")
        assert main_resp["message"] == "Answer from cascade-1"
        assert "keep prior constraint" in state["prompts_by_cascade"]["cascade-1"][0]
        assert adapter._pending_history == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_raw_prompt_isolated_cascade_omits_chat_wrapper_and_preserves_default_parity(
    tmp_path, monkeypatch,
) -> None:
    """raw_prompt=True sends exact Turn text without Chat wrapper; default raw_prompt=False stays wrapped."""
    import hashlib
    from loopx.chat import CHAT_REVIEW_OPEN_TAG

    state: dict[str, Any] = {"cascade_seq": 0, "prompts_by_cascade": {}}
    token = "synthetic-csrf-token"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = self.path.rsplit("/", 1)[-1]
            if method == "GetCascadeModelConfigData":
                resp = {"clientModelConfigs": [{"modelId": "gemini-4-argon-high", "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M417"}}]}
            elif method == "StartCascade":
                state["cascade_seq"] += 1
                resp = {"cascadeId": f"cascade-{state['cascade_seq']}"}
            elif method == "GetCascadeTrajectory":
                cid = body["cascadeId"]
                prompts = state["prompts_by_cascade"].get(cid, [])
                resp = {"trajectory": {"cascadeId": cid}, "numTotalSteps": len(prompts) * 2, "status": "CASCADE_RUN_STATUS_IDLE"}
            elif method == "SendUserCascadeMessage":
                cid = body["cascadeId"]
                state["prompts_by_cascade"].setdefault(cid, []).append(body["items"][0]["text"])
                resp = {}
            elif method == "GetCascadeTrajectorySteps":
                cid = body["cascadeId"]
                prompts = state["prompts_by_cascade"].get(cid, [])
                plain_json = json.dumps({"result_kind": "validated_completion", "summary": "ok"})
                steps = []
                for idx, text in enumerate(prompts):
                    meta = {"createdAt": "2026-11-22T01:00:00Z", "executionId": f"exec-{cid}-{idx}"}
                    answer = plain_json if cid == "cascade-2" else f"{CHAT_REVIEW_OPEN_TAG}{{\"message\":\"wrapped\",\"proposals\":[],\"gate\":null}}</loopx-review-json>"
                    steps.extend([
                        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
                         "metadata": {**meta, "sourceTrajectoryStepInfo": {"stepIndex": idx * 2}},
                         "userInput": {"items": [{"text": text}]}},
                        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
                         "metadata": {**meta, "sourceTrajectoryStepInfo": {"stepIndex": idx * 2 + 1}},
                         "plannerResponse": {"response": answer, "stopReason": "STOP_REASON_STOP_PATTERN"}},
                    ])
                resp = {"steps": steps[body["startIndex"]:body["endIndex"]]}
            elif method == "CancelCascadeInvocation":
                resp = {}
            else:
                raise AssertionError(method)
            payload = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("ANTIGRAVITY_LS_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", token)
    try:
        adapter = adapter_for(tmp_path, history=[{"role": "user", "content": "keep prior constraint"}])
        events: list[tuple[str, dict[str, Any]]] = []
        raw_message = "Turn request:\n{\"goal_id\":\"g1\"}\n\nThis Turn's task is to author the original steward's due report."
        raw_resp = adapter.start_turn(
            raw_message, lambda k, p: events.append((k, p)),
            isolated_cascade=True, raw_prompt=True,
        )
        sent_raw = state["prompts_by_cascade"]["cascade-2"][0]
        assert sent_raw == raw_message
        assert "Act as LoopX Steward." not in sent_raw
        assert CHAT_REVIEW_OPEN_TAG not in sent_raw
        assert "keep prior constraint" not in sent_raw
        started = next(p for k, p in events if k == "turn.started")
        assert started["native_input"]["input_sha256"] == hashlib.sha256(raw_message.encode()).hexdigest()
        assert json.loads(raw_resp["message"]) == {"result_kind": "validated_completion", "summary": "ok"}
        assert adapter.cascade_id == "cascade-1"
        assert adapter._pending_history == [{"role": "user", "content": "keep prior constraint"}]

        # Default raw_prompt=False on isolated_cascade retains existing Chat objective and review envelope framing.
        wrapped_resp = adapter.start_turn("Run isolated report", isolated_cascade=True)
        sent_wrapped = state["prompts_by_cascade"]["cascade-3"][0]
        assert "Act as LoopX Steward." in sent_wrapped
        assert CHAT_REVIEW_OPEN_TAG in sent_wrapped
        assert wrapped_resp["message"] == "wrapped"

        # Fail closed when combining raw_prompt with read_only_review.
        with pytest.raises(ValueError, match="raw_prompt cannot be combined with read_only_review"):
            adapter.start_turn(raw_message, isolated_cascade=True, raw_prompt=True, read_only_review=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_step_offset_pagination_and_transient_running_lag_across_long_cascades(
    tmp_path, monkeypatch
) -> None:
    """LS honors stepOffset while ignoring startIndex and capping each page."""
    import hashlib
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from loopx.chat_agent import CodexChatAgentError

    prior_steps: list[dict[str, Any]] = []
    for idx in range(6):
        prior_steps.append(
            {
                "type": "CORTEX_STEP_TYPE_GENERIC" if idx % 2 else "CORTEX_STEP_TYPE_CHECKPOINT",
                "status": "CORTEX_STEP_STATUS_DONE",
                "metadata": {
                    "createdAt": "2026-11-22T01:00:00Z",
                    "executionId": "exec-prior",
                    **({"sourceTrajectoryStepInfo": {"stepIndex": idx}} if idx > 0 else {}),
                },
            }
        )

    state: dict[str, Any] = {
        "steps": prior_steps,
        "status": "CASCADE_RUN_STATUS_IDLE",
        "step_calls": [],
        "running_empty_polls": 1,
        "quota_reset": "2099-01-01T00:00:00Z",
        "truncate_unpaginated": False,
        "force_incomplete_after_send": False,
    }
    token = "synthetic-csrf-token"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = self.path.rsplit("/", 1)[-1]
            if method == "GetCascadeModelConfigData":
                resp: dict[str, Any] = {
                    "clientModelConfigs": [
                        {
                            "modelId": "gemini-4-argon-high",
                            "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M417"},
                        }
                    ]
                }
            elif method == "GetUserStatus":
                resp = {
                    "userStatus": {
                        "email": "synthetic@invalid.example",
                        "cascadeModelConfigData": {
                            "clientModelConfigs": [
                                {
                                    "modelId": "gemini-4-argon-high",
                                    "quotaInfo": {
                                        "remainingFraction": 1.0,
                                        "resetTime": state["quota_reset"],
                                    },
                                }
                            ]
                        },
                    }
                }
            elif method == "StartCascade":
                resp = {"cascadeId": "long-cascade"}
            elif method == "GetCascadeTrajectory":
                resp = {
                    "trajectory": {"cascadeId": "long-cascade"},
                    "numTotalSteps": len(state["steps"]),
                    "status": state["status"],
                }
            elif method == "SendUserCascadeMessage":
                base_idx = len(state["steps"])
                prompt_text = body["items"][0]["text"]
                if state["force_incomplete_after_send"]:
                    state["steps"].append(
                        {
                            "type": "CORTEX_STEP_TYPE_USER_INPUT",
                            "status": "CORTEX_STEP_STATUS_DONE",
                            "metadata": {
                                "createdAt": "2026-11-22T02:00:00Z",
                                "executionId": "exec-fail",
                                "sourceTrajectoryStepInfo": {"stepIndex": base_idx},
                            },
                            "userInput": {"items": [{"text": prompt_text}]},
                        }
                    )
                    state["status"] = "CASCADE_RUN_STATUS_IDLE"
                else:
                    answer = (
                        "<loopx-review-json>"
                        + json.dumps(
                            {
                                "message": "Paginated answer",
                                "proposals": [],
                                "gate": None,
                            }
                        )
                        + "</loopx-review-json>"
                    )
                    exec_id = f"exec-{base_idx}"
                    extra = [
                        {
                            "type": "CORTEX_STEP_TYPE_USER_INPUT",
                            "status": "CORTEX_STEP_STATUS_DONE",
                            "metadata": {
                                "createdAt": "2026-11-22T02:00:00Z",
                                "executionId": exec_id,
                                "sourceTrajectoryStepInfo": {"stepIndex": base_idx},
                            },
                            "userInput": {"items": [{"text": prompt_text}]},
                        },
                        {
                            "type": "CORTEX_STEP_TYPE_GENERIC",
                            "status": "CORTEX_STEP_STATUS_DONE",
                            "metadata": {
                                "createdAt": "2026-11-22T02:00:01Z",
                                "executionId": exec_id,
                                "sourceTrajectoryStepInfo": {"stepIndex": base_idx + 1},
                            },
                        },
                        {
                            "type": "CORTEX_STEP_TYPE_CHECKPOINT",
                            "status": "CORTEX_STEP_STATUS_DONE",
                            "metadata": {
                                "createdAt": "2026-11-22T02:00:02Z",
                                "executionId": exec_id,
                                "sourceTrajectoryStepInfo": {"stepIndex": base_idx + 2},
                            },
                        },
                        {
                            "type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                            "status": "CORTEX_STEP_STATUS_DONE",
                            "metadata": {
                                "createdAt": "2026-11-22T02:00:03Z",
                                "executionId": exec_id,
                                "sourceTrajectoryStepInfo": {"stepIndex": base_idx + 3},
                            },
                            "plannerResponse": {
                                "response": answer,
                                "stopReason": "STOP_REASON_STOP_PATTERN",
                            },
                        },
                    ]
                    state["steps"].extend(extra)
                    state["status"] = "CASCADE_RUN_STATUS_RUNNING"
                resp = {}
            elif method == "GetCascadeTrajectorySteps":
                state["step_calls"].append(body)
                if state["force_incomplete_after_send"]:
                    resp = {"steps": state["steps"][: len(state["steps"]) - 1]}
                elif state["running_empty_polls"] > 0 and state["status"] == "CASCADE_RUN_STATUS_RUNNING":
                    state["running_empty_polls"] -= 1
                    state["status"] = "CASCADE_RUN_STATUS_IDLE"
                    resp = {"steps": []}
                else:
                    # Simulate LS: ignore startIndex/endIndex, only honor stepOffset, max 2 steps/page.
                    offset = 0 if state["truncate_unpaginated"] else int(body.get("stepOffset") or 0)
                    resp = {"steps": state["steps"][offset : offset + 2]}
            elif method == "CancelCascadeInvocation":
                state["status"] = "CASCADE_RUN_STATUS_IDLE"
                resp = {}
            else:
                raise AssertionError(method)
            payload = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("ANTIGRAVITY_LS_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", token)
    try:
        from loopx.capabilities.native_chat.manager_input import manager_turn_input

        adapter = AntigravityAdapter.start(
            work_dir=tmp_path, objective="Test", resume_thread_id="long-cascade"
        )
        # 1. Recover from one transient RUNNING step flush lag + paginate across 2-step pages.
        events: list[tuple[str, dict[str, Any]]] = []
        turn_message = manager_turn_input(
            adapter,
            "Paginated user ask",
            json.dumps({"schema_version": "manager_turn_context_v1", "context_delegation": {"source_id": "a" * 24}}),
        )
        result = adapter.start_turn(turn_message, lambda k, p: events.append((k, p)))
        assert result["message"] == "Paginated answer"
        assert [c.get("stepOffset") for c in state["step_calls"]] == [6, 6, 8]

        # 2. Anchored completed_result_observation paginates from anchor without fetching step 0.
        started_event = next(p for k, p in events if k == "turn.started")
        state["step_calls"].clear()
        proof = AntigravityAdapter.completed_result_observation(
            cascade_id="long-cascade",
            request_ref="a" * 24,
            message="Paginated user ask",
            started_at="2026-11-22T01:59:00Z",
            completed_at="2026-11-22T02:01:00Z",
            work_dir=tmp_path,
            input_anchor=started_event["native_input"],
        )
        assert proof is not None and proof["response"]["message"] == "Paginated answer"
        assert [c.get("stepOffset") for c in state["step_calls"]] == [6, 8]

        # 3. Unanchored completed_result_observation paginates all the way from step 0 across pages.
        state["step_calls"].clear()
        unanchored = AntigravityAdapter.completed_result_observation(
            cascade_id="long-cascade",
            request_ref="a" * 24,
            message="Paginated user ask",
            started_at="2026-11-22T01:59:00Z",
            completed_at="2026-11-22T02:01:00Z",
            work_dir=tmp_path,
            input_anchor=None,
        )
        assert unanchored is not None and unanchored["response"]["message"] == "Paginated answer"
        assert [c.get("stepOffset") for c in state["step_calls"]] == [0, 2, 4, 6, 8]

        # 4. metadata_review_capacity paginates too.
        state["step_calls"].clear()
        cap = AntigravityAdapter.metadata_review_capacity(
            cascade_id="long-cascade", model_id="gemini-4-argon-high"
        )
        assert cap is not None and cap["normal_completion"] is True
        assert [c.get("stepOffset") for c in state["step_calls"]] == [0, 2, 4, 6, 8]

        # 5. interrupted_answer_observation paginates cleanly with stepOffset across 2-step pages.
        err_base = len(state["steps"])
        err_prompt = "Original interrupted ask"
        err_framed = manager_turn_input(
            adapter,
            err_prompt,
            json.dumps({"schema_version": "manager_turn_context_v1", "context_delegation": {"source_id": "b" * 24}}),
        )
        err_meta = {"createdAt": "2026-11-22T03:00:00Z", "executionId": "exec-err"}
        target_file = (tmp_path / "note.txt").as_uri()
        state["steps"].extend(
            [
                {
                    "type": "CORTEX_STEP_TYPE_USER_INPUT",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {**err_meta, "sourceTrajectoryStepInfo": {"stepIndex": err_base}},
                    "userInput": {"items": [{"text": err_framed}]},
                },
                {
                    "type": "CORTEX_STEP_TYPE_VIEW_FILE",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {**err_meta, "sourceTrajectoryStepInfo": {"stepIndex": err_base + 1}},
                    "viewFile": {"absolutePathUri": target_file, "content": "Visible file"},
                },
                {
                    "type": "CORTEX_STEP_TYPE_ERROR_MESSAGE",
                    "status": "CORTEX_STEP_STATUS_DONE",
                    "metadata": {**err_meta, "sourceTrajectoryStepInfo": {"stepIndex": err_base + 2}},
                    "errorMessage": {"error": {"errorCode": 500}},
                },
            ]
        )
        state["step_calls"].clear()
        interrupted = AntigravityAdapter.interrupted_answer_observation(
            cascade_id="long-cascade",
            request_ref="b" * 24,
            message=err_prompt,
            started_at="2026-11-22T02:59:00Z",
            completed_at="2026-11-22T03:01:00Z",
            input_anchor={
                "thread_id": "long-cascade",
                "start_step_index": err_base,
                "input_sha256": hashlib.sha256(err_framed.encode()).hexdigest(),
            },
        )
        assert interrupted is not None and interrupted["visible_results"] == [
            {"file_uri": target_file, "content": "Visible file"}
        ]
        assert [c.get("stepOffset") for c in state["step_calls"]] == [10, 12, 10, 12]

        # 6. Non-advancing truncated response fails fast instead of looping.
        state["truncate_unpaginated"] = True
        assert (
            AntigravityAdapter.completed_result_observation(
                cascade_id="long-cascade",
                request_ref="a" * 24,
                message="Paginated user ask",
                started_at="2026-11-22T01:59:00Z",
                completed_at="2026-11-22T02:01:00Z",
                work_dir=tmp_path,
                input_anchor=started_event["native_input"],
            )
            is None
        )
        state["truncate_unpaginated"] = False
        state["force_incomplete_after_send"] = True
        with pytest.raises(CodexChatAgentError) as exc:
            adapter.start_turn("Fail fast on incomplete idle read")
        assert exc.value.error_code == "antigravity_rpc_error"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    ("variant", "should_succeed"),
    [("pre_error_pending", True), ("post_error_pending", False), ("no_error_pending", False)],
)
def test_pre_error_orphaned_pending_steps_do_not_reject_recovered_final_answer(
    tmp_path, language_server, monkeypatch, variant, should_succeed
) -> None:
    from loopx.chat_agent import CodexChatAgentError

    exec_meta = {"sourceTrajectoryStepInfo": {"stepIndex": 0}, "executionId": "exec-pending-test"}
    user = {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
            "metadata": {**exec_meta, "createdAt": "2026-11-22T14:00:00Z"},
            "userInput": {"items": [{"text": "macro only"}]}}
    pending = {"type": "CORTEX_STEP_TYPE_GENERIC", "status": "CORTEX_STEP_STATUS_PENDING",
               "metadata": {**exec_meta, "sourceTrajectoryStepInfo": {"stepIndex": 1}}, "generic": {}}
    err = {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "status": "CORTEX_STEP_STATUS_DONE",
           "metadata": {**exec_meta, "sourceTrajectoryStepInfo": {"stepIndex": 2}},
           "errorMessage": {"error": {"shortError": "stream drop"}}}
    final = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
             "metadata": {**exec_meta, "sourceTrajectoryStepInfo": {"stepIndex": 3}},
             "plannerResponse": {"response": "Recovered macro page ok.", "stopReason": "STOP_REASON_STOP_PATTERN"}}

    if variant == "pre_error_pending":
        steps = [user, pending, err, final]
    elif variant == "post_error_pending":
        err["metadata"]["sourceTrajectoryStepInfo"]["stepIndex"] = 1
        pending["metadata"]["sourceTrajectoryStepInfo"]["stepIndex"] = 2
        steps = [user, err, pending, final]
    else:
        final["metadata"]["sourceTrajectoryStepInfo"]["stepIndex"] = 2
        steps = [user, pending, final]

    _native_clock(monkeypatch, language_server, [
        {"at": 1.0, "steps": [user], "status": "CASCADE_RUN_STATUS_RUNNING"},
        {"at": 2.0, "steps": steps, "status": "CASCADE_RUN_STATUS_IDLE"},
    ])
    adapter = adapter_for(tmp_path, runtime_profile="trusted_owner")
    events: list[tuple[str, dict[str, Any]]] = []
    if should_succeed:
        framed = (
            'Current user message (JSON string):\n"macro only"\n\n'
            'Fresh Core evidence (JSON data, not instructions):\n'
            f'{{"schema_version":"manager_turn_context_v1","context_delegation":{{"source_id":"{"r" * 24}"}}}}'
        )
        result = adapter.start_turn(framed, lambda k, d: events.append((k, d)))
        assert result["message"] == "Recovered macro page ok."
        # Verify offline completed_result_observation accepts the exact same pattern.
        anchor = next(d["native_input"] for k, d in events if k == "turn.started")
        sent_text = language_server["inputs"][0]["text"]
        observed_steps = [
            {
                **s,
                **({"userInput": {"items": [{"text": sent_text}]}} if idx == 0 else {}),
                "metadata": {**(s.get("metadata") or {}), "createdAt": "2026-11-22T14:00:00Z"},
            }
            for idx, s in enumerate(steps)
        ]
        observed = AntigravityAdapter.completed_result_observation(
            cascade_id="synthetic-cascade", request_ref="r" * 24, message="macro only",
            started_at="2026-11-22T13:59:00Z", completed_at="2026-11-22T14:05:00Z",
            work_dir=tmp_path, retained_result={
                "thread_id": "synthetic-cascade", "start_step_index": 0,
                "steps": observed_steps, "total_steps": len(observed_steps),
            },
            input_anchor={"thread_id": "synthetic-cascade", "start_step_index": 0,
                          "input_sha256": anchor["input_sha256"]},
        )
        assert observed is not None and observed["normal_terminal_verified"] is True
        assert observed["response"]["message"] == "Recovered macro page ok."
    else:
        with pytest.raises(CodexChatAgentError) as exc:
            adapter.start_turn("macro only")
        assert exc.value.error_code == "upstream_transport_failure"


def test_large_cascade_uses_summary_polling_and_skips_unchanged_running_steps(
    tmp_path, monkeypatch
) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    counts: dict[str, int] = {}
    polls = {"n": 0, "prompt": ""}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
            body = json.loads(raw or "{}")
            method = self.path.rsplit("/", 1)[-1]
            counts[method] = counts.get(method, 0) + 1
            if method == "GetCascadeModelConfigData":
                resp = {"clientModelConfigs": [{"modelId": "gemini-4-argon-high",
                                                "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M417"}}]}
            elif method == "SendUserCascadeMessage":
                polls["prompt"] = body["items"][0]["text"]
                resp = {}
            elif method == "GetAllCascadeTrajectories":
                polls["n"] += 1
                if polls["n"] <= 2:
                    summary = {"status": "CASCADE_RUN_STATUS_RUNNING", "stepCount": 252, "lastModifiedTime": "t1"}
                else:
                    summary = {"status": "CASCADE_RUN_STATUS_IDLE", "stepCount": 252, "lastModifiedTime": "t2"}
                resp = {"trajectorySummaries": {"large-cascade": summary}}
            elif method == "GetCascadeTrajectory":
                st = "CASCADE_RUN_STATUS_IDLE"
                resp = {"trajectory": {"cascadeId": "large-cascade"}, "numTotalSteps": 250 if polls["n"] == 0 else 252, "status": st}
            elif method == "GetCascadeTrajectorySteps":
                resp = {"steps": [
                    {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
                     "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 250}},
                     "userInput": {"items": [{"text": polls["prompt"]}]}},
                    {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
                     "status": "CORTEX_STEP_STATUS_GENERATING" if polls["n"] <= 2 else "CORTEX_STEP_STATUS_DONE",
                     "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 251}},
                     "plannerResponse": {"thinking": "analyzing", **({"response": "Macro summary ok", "stopReason": "STOP_REASON_STOP_PATTERN"} if polls["n"] > 2 else {})}},
                ]}
            else:
                raise AssertionError(method)
            data = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thr = threading.Thread(target=srv.serve_forever, daemon=True)
    thr.start()
    monkeypatch.setenv("ANTIGRAVITY_LS_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", "tok")
    try:
        adapter = AntigravityAdapter.start(work_dir=tmp_path, objective="Test", resume_thread_id="large-cascade")
        res = adapter.start_turn("macro query")
        assert res["message"] == "Macro summary ok"
        assert counts["GetAllCascadeTrajectories"] == 3
        # Once on start(), once before SendUserCascadeMessage, once at final IDLE completion (0 while RUNNING).
        assert counts["GetCascadeTrajectory"] == 3
        # Poll 2 skipped GetCascadeTrajectorySteps because summary (status, stepCount, lastModifiedTime) was unchanged.
        assert counts["GetCascadeTrajectorySteps"] == 2
    finally:
        srv.shutdown()
        srv.server_close()
        thr.join(timeout=2)


def test_overfull_antigravity_cascade_rotates_on_start_and_between_turns_preserving_history(
    tmp_path, monkeypatch
) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    monkeypatch.setenv("LOOPX_ANTIGRAVITY_MAX_CASCADE_STEPS", "10")
    cascades = {"seq": 0, "steps": {"old-cascade": 12, "rot-1": 0, "rot-2": 0}, "sent": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0") or 0)) or "{}")
            method = self.path.rsplit("/", 1)[-1]
            if method == "GetCascadeModelConfigData":
                resp = {"clientModelConfigs": [{"modelId": "gemini-4-argon-high",
                                                "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M417"}}]}
            elif method == "StartCascade":
                cascades["seq"] += 1
                resp = {"cascadeId": f"rot-{cascades['seq']}"}
            elif method == "GetCascadeTrajectory":
                cid = body["cascadeId"]
                total = cascades["steps"][cid]
                resp = {"trajectory": {"cascadeId": cid}, "numTotalSteps": total, "status": "CASCADE_RUN_STATUS_IDLE"}
            elif method == "SendUserCascadeMessage":
                cid = body["cascadeId"]
                cascades["sent"].append((cid, body["items"][0]["text"]))
                cascades["steps"][cid] = 2
                resp = {}
            elif method == "GetCascadeTrajectorySteps":
                cid, prompt = cascades["sent"][-1]
                answer = f"Answer on {cid}"
                resp = {"steps": [
                    {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
                     "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}}, "userInput": {"items": [{"text": prompt}]}},
                    {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
                     "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 1}},
                     "plannerResponse": {"response": answer, "stopReason": "STOP_REASON_STOP_PATTERN"}},
                ]}
            else:
                raise AssertionError(method)
            data = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thr = threading.Thread(target=srv.serve_forever, daemon=True)
    thr.start()
    monkeypatch.setenv("ANTIGRAVITY_LS_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", "tok")
    try:
        adapter = AntigravityAdapter.start(
            work_dir=tmp_path, objective="Steward", resume_thread_id="old-cascade",
            history=[{"role": "user", "content": "keep constraint X"}, {"role": "assistant", "content": "noted X"}],
        )
        assert adapter.upstream_thread_id == "rot-1"
        r1 = adapter.start_turn("first turn")
        assert r1["message"] == "Answer on rot-1"
        assert "keep constraint X" in cascades["sent"][0][1]

        # Simulate rot-1 reaching rollover step ceiling before next turn.
        cascades["steps"]["rot-1"] = 10
        r2 = adapter.start_turn("second turn")
        assert adapter.upstream_thread_id == "rot-2"
        assert r2["message"] == "Answer on rot-2"
        assert "Answer on rot-1" in cascades["sent"][1][1]
        assert "keep constraint X" in cascades["sent"][1][1]
    finally:
        srv.shutdown()
        srv.server_close()
        thr.join(timeout=2)


def test_unbounded_idle_still_bounds_stuck_idle_cascade_and_dead_ls_transport(
    tmp_path, language_server, monkeypatch
) -> None:
    from loopx.capabilities.native_chat import antigravity_adapter as provider
    from loopx.chat_agent import CodexChatAgentError

    # Subcase A: CASCADE_RUN_STATUS_IDLE without planner answer stalls and stops after idle_grace (300s).
    user = {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE",
            "metadata": {"sourceTrajectoryStepInfo": {"stepIndex": 0}}}
    frames = [
        {"at": 1.0, "steps": [user], "status": "CASCADE_RUN_STATUS_IDLE"},
        {"at": 305.0, "steps": [user], "status": "CASCADE_RUN_STATUS_IDLE"},
    ]
    _native_clock(monkeypatch, language_server, frames)
    with pytest.raises(CodexChatAgentError) as raised_idle:
        adapter_for(tmp_path).start_turn("stuck idle task")
    assert raised_idle.value.error_code == "response_timeout"
    assert language_server["cancelled"].is_set()

    # Subcase B: Consecutive transport failures under idle_timeout_sec=None raise after 60s.
    now_box = [0.0]
    monkeypatch.setattr(provider, "time", SimpleNamespace(monotonic=lambda: now_box[0], sleep=time.sleep))
    calls = {"traj": 0}
    real_rpc = provider._rpc_post

    def failing_rpc(base_url, csrf_token, method, payload, *, timeout=15.0):
        if method == "GetCascadeTrajectory":
            calls["traj"] += 1
            if calls["traj"] >= 2:
                raise CodexChatAgentError("LS dead", gate=None, error_code="antigravity_transport_error")
        return real_rpc(base_url, csrf_token, method, payload, timeout=timeout)

    language_server.update(frames=None, active=False)
    monkeypatch.setattr(provider, "_rpc_post", failing_rpc)
    adapter = adapter_for(tmp_path)
    monkeypatch.setattr(adapter._interrupted, "wait", lambda _t: now_box.__setitem__(0, now_box[0] + 20.0) or False)
    with pytest.raises(CodexChatAgentError) as raised_tx:
        adapter.start_turn("dead ls task")
    assert raised_tx.value.error_code == "antigravity_transport_error"
    assert now_box[0] >= 60.0


def test_daemon_discovery_connects_standalone_hub_without_ps_token_and_rejects_unsafe_files(
    tmp_path, language_server, monkeypatch
) -> None:
    from urllib.parse import urlsplit
    from loopx.capabilities.native_chat import antigravity_adapter as provider

    port = urlsplit(language_server["url"]).port
    assert port is not None
    home_dir = tmp_path / "home"
    daemon_dir = home_dir / ".gemini" / "antigravity" / "daemon"
    daemon_dir.mkdir(parents=True)
    # Stale PID and symlinked discovery metadata must be ignored.
    (daemon_dir / "hub_99999.json").write_text(
        json.dumps({"pid": 99999, "httpPort": port, "csrfToken": "synthetic-csrf-token"}),
        encoding="utf-8",
    )
    target_meta = tmp_path / "external.json"
    target_meta.write_text(
        json.dumps({"pid": 81414, "httpPort": port, "csrfToken": "synthetic-csrf-token"}),
        encoding="utf-8",
    )
    (daemon_dir / "hub_symlink.json").symlink_to(target_meta)
    (daemon_dir / f"hub_{port}.json").write_text(
        json.dumps({"pid": 81414, "httpPort": port, "csrfToken": "synthetic-csrf-token"}),
        encoding="utf-8",
    )

    calls: list[str] = []
    ps_stdout = (
        "81414 /Applications/Antigravity.app/Contents/Resources/bin/language_server "
        "--standalone --override_ide_name antigravity --app_data_dir antigravity\n"
    )

    def fake_check_output(command, **kwargs):
        calls.append(command[0])
        if command[0] == "ps":
            return ps_stdout
        raise AssertionError(f"unexpected subprocess {command}")

    monkeypatch.setattr(provider.subprocess, "check_output", fake_check_output)
    discovered = provider.discover_antigravity_language_server(env={"HOME": str(home_dir)})
    assert discovered == {
        "url": language_server["url"],
        "csrf_token": "synthetic-csrf-token",
        "pid": 81414,
        "source": "daemon_discovery",
    }
    assert calls == ["ps"]

    # Path traversal app_data_dir values in ps are ignored safely.
    ps_stdout = (
        "81414 /Applications/Antigravity.app/Contents/Resources/bin/language_server "
        "--standalone --app_data_dir ../escape\n"
    )
    assert provider.discover_antigravity_language_server(env={"HOME": str(home_dir)}) is None


def test_probe_cascade_idle_uses_summary_and_falls_back_on_rpc_error(monkeypatch) -> None:
    from loopx.capabilities.native_chat import antigravity_adapter as provider
    from loopx.chat_agent import CodexChatAgentError

    seen_methods: list[tuple[str, float]] = []
    mode = {"behavior": "summary_idle"}

    def fake_rpc(_url, _token, method, _payload, *, timeout=15.0):
        seen_methods.append((method, timeout))
        if method == "GetAllCascadeTrajectories":
            if mode["behavior"] == "rpc_unsupported":
                raise CodexChatAgentError("unsupported", gate=None, error_code="antigravity_rpc_error")
            if mode["behavior"] == "summary_idle":
                return {"trajectorySummaries": {"c-1": {"status": "CASCADE_RUN_STATUS_IDLE"}}}
            return {"trajectorySummaries": {"c-1": {"status": "CASCADE_RUN_STATUS_RUNNING"}}}
        if method == "GetCascadeTrajectory":
            return {"status": "CASCADE_RUN_STATUS_IDLE"}
        raise AssertionError(method)

    monkeypatch.setattr(provider, "_rpc_post", fake_rpc)
    assert provider.probe_cascade_idle("http://127.0.0.1:1", "tok", "c-1") is True
    assert seen_methods == [("GetAllCascadeTrajectories", 2.5)]

    seen_methods.clear()
    mode["behavior"] = "summary_running"
    assert provider.probe_cascade_idle("http://127.0.0.1:1", "tok", "c-1") is False
    assert seen_methods == [("GetAllCascadeTrajectories", 2.5)]

    seen_methods.clear()
    mode["behavior"] = "rpc_unsupported"
    assert provider.probe_cascade_idle("http://127.0.0.1:1", "tok", "c-1", timeout=2.5) is True
    assert seen_methods == [("GetAllCascadeTrajectories", 2.5), ("GetCascadeTrajectory", 5.0)]


def test_antigravity_start_rolls_over_via_summary_when_trajectory_read_times_out(tmp_path, monkeypatch) -> None:
    from loopx.capabilities.native_chat import antigravity_adapter as provider
    from loopx.chat_agent import CodexChatAgentError

    monkeypatch.setenv("ANTIGRAVITY_LS_URL", "http://127.0.0.1:55999")
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", "tok")
    monkeypatch.setenv("LOOPX_ANTIGRAVITY_MAX_CASCADE_STEPS", "500")
    summary_state = {"status": "CASCADE_RUN_STATUS_IDLE", "stepCount": 600}

    def fake_rpc(_url, _token, method, _payload, *, timeout=15.0):
        if method == "GetCascadeModelConfigData":
            return {"clientModelConfigs": [{"modelId": "gemini-4-argon-high", "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M417"}}]}
        if method == "GetCascadeTrajectory":
            raise CodexChatAgentError("timed out", gate=None, error_code="antigravity_transport_error")
        if method == "GetAllCascadeTrajectories":
            return {"trajectorySummaries": {"old-c": dict(summary_state)}}
        if method == "StartCascade":
            return {"cascadeId": "new-rotated-c"}
        raise AssertionError(method)

    monkeypatch.setattr(provider, "_rpc_post", fake_rpc)
    adapter = provider.AntigravityAdapter.start(
        work_dir=tmp_path,
        objective="Steward",
        resume_thread_id="old-c",
        history=[{"role": "user", "content": "prior req"}],
    )
    assert adapter.upstream_thread_id == "new-rotated-c"

    # Running or undersized cascades still fail closed instead of rotating.
    summary_state.update(status="CASCADE_RUN_STATUS_RUNNING", stepCount=600)
    with pytest.raises(CodexChatAgentError) as running_err:
        provider.AntigravityAdapter.start(
            work_dir=tmp_path, objective="Steward", resume_thread_id="old-c",
            history=[{"role": "user", "content": "prior req"}],
        )
    assert running_err.value.error_code == "resume_failed"

    summary_state.update(status="CASCADE_RUN_STATUS_IDLE", stepCount=120)
    with pytest.raises(CodexChatAgentError) as small_err:
        provider.AntigravityAdapter.start(
            work_dir=tmp_path, objective="Steward", resume_thread_id="old-c",
            history=[{"role": "user", "content": "prior req"}],
        )
    assert small_err.value.error_code == "resume_failed"
