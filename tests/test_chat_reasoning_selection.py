"""Advertised reasoning choices reach native Chat without rewriting old work."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from loopx.chat_acp import ACPStdioAdapter
from loopx.chat_agent import CodexChatAgentError
from loopx.chat_providers import codex_model_options
from loopx.chat_endpoints import AgentEndpointRegistry
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from test_chat_ordinary_project import ordinary  # noqa: F401


def test_codex_catalog_exposes_only_advertised_reasoning_levels(tmp_path, monkeypatch):
    binary = tmp_path / "codex"
    binary.touch()
    catalog = {"models": [{"slug": "fixture-model", "display_name": "Fixture",
        "base_instructions": "must not reach the browser",
        "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"},
                                       {"effort": "bad\nvalue"}, {"effort": 12}]},
        {"slug": "other-model"}]}
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs:
                        SimpleNamespace(returncode=0, stdout=json.dumps(catalog)))
    result = codex_model_options(str(binary))
    assert result["selected"] == ""
    assert result["options"][0] == {"value": "fixture-model", "label": "Fixture",
                                    "reasoning_efforts": ["low", "high"]}
    assert "reasoning_efforts" not in result["options"][1]
    assert "must not reach" not in json.dumps(result)


ACP_CONFIG = r'''
import json, sys
from pathlib import Path
capture, behavior = Path(sys.argv[1]), sys.argv[2]
selected = {"model": "first", "depth": "low"}
def options():
    return [{"id": "model", "category": "model", "type": "select",
             "name": "Model", "currentValue": selected["model"],
             "options": [{"value": "first", "name": "First"}, {"value": "second", "name": "Second"}]},
            {"id": "depth", "category": "thought_level", "type": "select",
             "name": "Depth", "currentValue": selected["depth"],
             "options": [{"value": "low", "name": "Low"}, {"value": "high", "name": "High"}]}]
for line in sys.stdin:
    request = json.loads(line)
    with capture.open("a") as out: out.write(json.dumps(request)+"\n")
    method = request.get("method")
    if method == "initialize": result = {"protocolVersion": 1, "agentCapabilities": {}}
    elif method == "session/new": result = {"sessionId": "fixture-session", "configOptions": options()}
    elif method == "session/set_config_option":
        if behavior != "ignore" or request["params"]["configId"] != "depth":
            selected[request["params"]["configId"]] = request["params"]["value"]
        result = {"configOptions": options()}
    elif method == "session/close": result = {}
    else: continue
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
'''


@pytest.mark.parametrize("behavior", ["apply", "ignore"])
def test_acp_reasoning_category_is_applied_and_verified_over_stdio(tmp_path: Path, behavior):
    script, capture = tmp_path / "agent.py", tmp_path / "capture.jsonl"
    script.write_text(ACP_CONFIG)
    kwargs = dict(command=(sys.executable, str(script), str(capture), behavior),
                  work_dir=tmp_path, config_values={"model": "second", "reasoning_effort": "high"})
    if behavior == "ignore":
        with pytest.raises(CodexChatAgentError) as error:
            ACPStdioAdapter.start(**kwargs)
        assert error.value.error_code == "unsupported_configuration"
    else:
        adapter = ACPStdioAdapter.start(**kwargs)
        try:
            assert adapter.reasoning_options()["selected"] == "high"
            assert adapter.model_settings["agent_reasoning_effort"] == "high"
        finally:
            adapter.close_session()
        calls = [json.loads(line) for line in capture.read_text().splitlines()]
        sets = [row["params"] for row in calls if row["method"] == "session/set_config_option"]
        assert [(row["configId"], row["value"]) for row in sets] == [("model", "second"), ("depth", "high")]


def test_http_reasoning_selection_reaches_real_native_turn_and_restart(ordinary):  # noqa: F811
    store, runtime, contexts, request, capture, _fake, _workspace = ordinary
    body = {"context_kind": "project", "project_ref": contexts.available()[0]["project_ref"],
            "agent_reasoning_effort": "high"}
    code, opened = request("/api/chat/sessions", body)
    assert code == 201, opened
    sid = opened["session_id"]
    assert opened["session"]["agent_reasoning_effort"] == "high"
    for ingress in ["first", "after-restart"]:
        code, turn = request(f"/api/chat/sessions/{sid}/turns", {"client_turn_id": ingress, "message": "Inspect."})
        assert code == 202, turn
        assert runtime.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=10)["status"] == "completed"
        if ingress == "first":
            runtime.adapters.pop(sid).close_session()
            code, resumed = request("/api/chat/sessions", body)
            assert code == 200 and resumed["session_id"] == sid
    packets = [json.loads(line) for line in capture.read_text().splitlines()]
    starts = [row["params"] for row in packets if row["method"] == "thread/start"]
    resumes = [row["params"] for row in packets if row["method"] == "thread/resume"]
    turns = [row["params"] for row in packets if row["method"] == "turn/start"]
    assert len(starts) == len(resumes) == 1 and len(turns) == 2
    assert starts[0]["config"]["model_reasoning_effort"] == "high"
    assert resumes[0]["config"]["model_reasoning_effort"] == "high"
    assert all(row["effort"] == "high" for row in turns)
    assert len(store.list_sessions()) == 1


@pytest.mark.parametrize("value", [12, True, "", "bad\nvalue", [], "x" * 161])
def test_http_rejects_invalid_reasoning_before_opening_session(ordinary, value):  # noqa: F811
    store, _runtime, contexts, request, capture, _fake, _workspace = ordinary
    code, failed = request("/api/chat/sessions", {"context_kind": "project",
        "project_ref": contexts.available()[0]["project_ref"], "agent_reasoning_effort": value})
    assert code == 400 and "agent_reasoning_effort" in failed["error"]
    assert not store.list_sessions() and not capture.exists()


def test_http_missing_native_effort_readback_is_not_reported_as_applied(ordinary):  # noqa: F811
    store, _runtime, contexts, request, _capture, fake, _workspace = ordinary
    source = fake.read_text()
    expected = '"reasoningEffort": request["params"].get("config", {}).get("model_reasoning_effort", "xhigh")'
    assert expected in source
    fake.write_text(source.replace(expected, '"reasoningEffort": None'))
    code, failed = request("/api/chat/sessions", {"context_kind": "project",
        "project_ref": contexts.available()[0]["project_ref"], "agent_reasoning_effort": "high"})
    assert code == 424 and failed["error_code"] == "unsupported_configuration"
    assert not store.list_sessions()


def test_explicit_new_settings_preserve_the_original_running_conversation(ordinary):  # noqa: F811
    store, runtime, contexts, request, _capture, _fake, _workspace = ordinary
    body = {"context_kind": "project", "project_ref": contexts.available()[0]["project_ref"],
            "agent_reasoning_effort": "high"}
    first = request("/api/chat/sessions", body)[1]["session_id"]
    turn = request(f"/api/chat/sessions/{first}/turns", {"client_turn_id": "working", "message": "wait for steer"})[1]
    before = store.load_session(first)
    code, refused = request("/api/chat/sessions", {**body, "mode": "resume_latest",
        "agent_model_id": "other-model", "agent_reasoning_effort": "low"})
    assert code == 424 and refused["error_code"] == "unsupported_configuration"
    assert len(store.list_sessions()) == 1
    code, selected = request("/api/chat/sessions", {**body, "mode": "new", "agent_reasoning_effort": "low"})
    assert code == 201 and selected["session_id"] != first
    assert selected["session"]["agent_reasoning_effort"] == "low"
    retained = store.load_session(first)
    assert retained["active_turn_id"] == before["active_turn_id"] == turn["turn_id"]
    assert retained["upstream_thread_id"] == before["upstream_thread_id"]
    assert retained["agent_reasoning_effort"] == "high"
    assert store.load_turn(first, turn["turn_id"])["status"] in {"starting", "running"}
    runtime.steer_active_turn(session_id=first, expected_turn_id=turn["turn_id"],
                              client_ingress_id="release-fixture", message="Finish.")
    assert runtime.wait_for_turn(session_id=first, turn_id=turn["turn_id"], timeout_sec=10)["status"] == "completed"
    assert not store.messages(selected["session_id"])


def test_registered_acp_discovery_and_selection_share_native_configuration(tmp_path):
    script, capture = tmp_path / "agent.py", tmp_path / "capture.jsonl"
    script.write_text(ACP_CONFIG)
    store = ChatSessionStore(tmp_path / "runtime")
    endpoints = AgentEndpointRegistry(store.root)
    endpoints.upsert({"agent_id": "fixture-acp", "display_name": "Fixture ACP", "location": "local",
                      "command": [sys.executable, str(script), str(capture), "apply"]})
    runtime = ChatRuntimeController(store=store, codex_bin="unavailable-fixture-codex", endpoint_registry=endpoints)
    try:
        discovery = runtime.inspect_agent_model_options("fixture-acp", work_dir=tmp_path, agent_model_id="second")
        assert discovery["model"]["selected"] == "second"
        assert discovery["reasoning_effort"]["id"] == "depth"
        assert not store.list_sessions(), "Discovery must not publish a conversation or run a prompt"
        session, _ = runtime.open_session(goal_id="fixture", agent_id="fixture-acp", work_dir=tmp_path,
            objective="Inspect.", mode="new", agent_model_id="second", agent_reasoning_effort="high")
        assert store.public_session(session)["agent_reasoning_effort"] == "high"
        assert store.public_session(session)["agent_model_id"] == "second"
        assert not store.messages(session["session_id"])
        packets = [json.loads(line) for line in capture.read_text().splitlines()]
        assert not any(row["method"] == "session/prompt" for row in packets)
    finally:
        runtime.close()


def test_codex_catalog_failure_is_retryable_without_inventing_current_model(tmp_path, monkeypatch):
    runtime = ChatRuntimeController(store=ChatSessionStore(tmp_path / "runtime"), codex_bin="fixture")
    monkeypatch.setattr(runtime, "capabilities", lambda: [{"agent_id": "codex", "available": True}])
    monkeypatch.setattr("loopx.chat_runtime.codex_model_options", lambda *_args, **_kwargs: None)
    try:
        with pytest.raises(CodexChatAgentError) as error:
            runtime.inspect_agent_model_options("codex", work_dir=tmp_path)
        assert error.value.error_code == "provider_unavailable"
        assert "account" in str(error.value) and "retry" in str(error.value)
        assert not runtime.store.list_sessions()
    finally:
        runtime.close()


@pytest.mark.parametrize("field", ["requested_agent_model_id", "requested_agent_reasoning_effort", "agent_reasoning_effort"])
def test_invalid_model_settings_create_no_partial_session_directory(tmp_path, field):
    store = ChatSessionStore(tmp_path / "runtime")
    with pytest.raises(ValueError):
        store.create_session(goal_id="fixture", agent_id="codex", adapter_kind="codex_app_server",
                             upstream_thread_id="fixture-thread", **{field: "invalid\nvalue"})
    assert not list(store.sessions_root.iterdir())


def test_absent_requested_settings_remain_distinct_from_explicit_inheritance(tmp_path):
    store = ChatSessionStore(tmp_path / "runtime")
    args = dict(goal_id="fixture", agent_id="codex", adapter_kind="codex_app_server", upstream_thread_id="fixture")
    legacy = store.create_session(**args)
    explicit = store.create_session(**args, requested_agent_model_id=None, requested_agent_reasoning_effort=None)
    assert "requested_agent_model_id" not in legacy and "requested_agent_reasoning_effort" not in legacy
    assert explicit["requested_agent_model_id"] is None and explicit["requested_agent_reasoning_effort"] is None
    assert legacy["agent_model_id"] is None and legacy["agent_reasoning_effort"] is None


@pytest.mark.parametrize("field", ["agent_model_id", "agent_reasoning_effort"])
def test_unsupported_executor_settings_fail_before_native_start(tmp_path, monkeypatch, field):
    store = ChatSessionStore(tmp_path / "runtime")
    runtime = ChatRuntimeController(store=store, codex_bin="unavailable-fixture-codex")
    monkeypatch.setattr(runtime, "capabilities", lambda: [{"agent_id": "fixture-direct", "adapter_kind": "direct", "available": True}])
    monkeypatch.setattr(runtime, "_start_adapter", lambda **_kwargs: pytest.fail("Unsupported setting must fail before starting a host"))
    try:
        with pytest.raises(CodexChatAgentError) as error:
            runtime.open_session(goal_id="fixture", agent_id="fixture-direct", work_dir=tmp_path,
                                 objective="Inspect.", mode="new", **{field: "high"})
        assert error.value.error_code == "unsupported_configuration"
        assert not store.list_sessions()
    finally:
        runtime.close()


def test_setting_validation_preserves_the_real_endpoint_readiness_error(tmp_path, monkeypatch):
    runtime = ChatRuntimeController(store=ChatSessionStore(tmp_path / "runtime"), codex_bin="unavailable-fixture-codex")
    monkeypatch.setattr(runtime, "capabilities", lambda: [{"agent_id": "fixture-direct", "adapter_kind": "direct",
                                                         "available": False, "unavailable_reason": "api_key_not_set"}])
    try:
        with pytest.raises(CodexChatAgentError) as error:
            runtime.open_session(goal_id="fixture", agent_id="fixture-direct", work_dir=tmp_path,
                objective="Inspect.", mode="new", agent_model_id="fixture-model", agent_reasoning_effort="high")
        assert error.value.error_code == "agent_endpoint_unavailable"
        assert "api_key_not_set" in str(error.value)
        assert not runtime.store.list_sessions()
    finally:
        runtime.close()
