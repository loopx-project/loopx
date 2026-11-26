"""Recover the original source after an account outage, without replaying work."""
import json

import pytest

from test_antigravity_steward_adapter import language_server  # noqa: F401
from test_chat_ordinary_project import ordinary  # noqa: F401
from test_chat_queue_preparation import session_runtime
from test_lark_private_conversations import connect
from test_manager_preparation_recovery import advance_recovery_clock
from test_native_steward_private import steward, finish  # noqa: F401
from loopx.chat import CHAT_RESPONSE_EXAMPLE_SUFFIX
from loopx.chat_agent import CodexChatAgentError
from loopx.chat_store import _read_json


@pytest.mark.parametrize("prior_dispatch", [None, "native-id", "started"])
def test_restart_adopts_claimed_unstarted_queue_without_duplicate_dispatch(tmp_path, monkeypatch, prior_dispatch):
    import queue
    import threading
    from types import SimpleNamespace
    from loopx.chat_agent import CodexChatAgentSession
    from loopx.chat_runtime import ChatRuntimeController, CodexAppServerAdapter
    from loopx.chat_store import ChatSessionStore

    store, sid, original_runtime = session_runtime(tmp_path)
    original_runtime.close()
    original, _ = store.create_queued_turn(sid, client_turn_id="claimed-before-crash",
        message="The same original input", origin="web")
    assert store.claim_next_queued_turn(sid)["turn_id"] == original["turn_id"]
    assert store.load_turn(sid, original["turn_id"])["started_at"] is None
    if prior_dispatch:
        store.update_turn(sid, original["turn_id"], **(
            {"upstream_turn_id": "unknown-native-dispatch"} if prior_dispatch == "native-id"
            else {"started_at": "2026-01-01T00:00:00Z"}))
    reopened = ChatSessionStore(tmp_path)
    runtime = ChatRuntimeController(store=reopened, codex_bin="unused-test-codex")
    native = CodexChatAgentSession(process=SimpleNamespace(poll=lambda: None),
        messages=queue.Queue(), thread_id="public-thread", work_dir=tmp_path)
    adapter = CodexAppServerAdapter(native)
    sent = []
    def write(packet):
        assert packet["method"] == "turn/start"
        sent.append(packet["params"])
        native.messages.put({"id": packet["id"], "result": {"turn": {"id": "original-dispatch"}}})
        native.messages.put({"method": "item/completed", "params": {"item": {
            "type": "agentMessage", "phase": "final_answer", "text": "Recovered the original queue."}}})
        native.messages.put({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
    monkeypatch.setattr(native, "_write", write)
    prepared = threading.Event()
    def ensure(*_a, **_kw):
        prepared.set()
        return adapter
    monkeypatch.setattr(runtime, "_ensure_adapter", ensure)
    try:
        runtime.resume_session_queue(session_id=sid, work_dir=tmp_path, objective="Original request")
        if prior_dispatch:
            assert prepared.wait(timeout=2)
            current = reopened.load_turn(sid, original["turn_id"])
            assert current["status"] == "queued" and not sent
            assert current.get("upstream_turn_id") == ("unknown-native-dispatch" if prior_dispatch == "native-id" else None)
            return
        result = runtime.wait_for_turn(session_id=sid, turn_id=original["turn_id"], timeout_sec=3)
        assert result["status"] == "completed"
        assert len(sent) == 1
        assert sent[0]["input"][0]["text"].endswith(original["message"] + CHAT_RESPONSE_EXAMPLE_SUFFIX)
        assert reopened.turn_for_client(sid, original["client_turn_id"])["turn_id"] == original["turn_id"]
        assert len([e for e in reopened.events_after(sid, original["turn_id"], None)
                    if e["kind"] == "turn.started"]) == 1
    finally:
        runtime.close()


def test_account_preparation_keeps_its_error_before_native_dispatch(tmp_path, monkeypatch):
    store, sid, runtime = session_runtime(tmp_path)

    def unavailable(**_kwargs):
        raise CodexChatAgentError("Native account API transport is unavailable.",
                                  error_code="model_auth_unavailable", gate=None)

    monkeypatch.setattr(runtime, "_start_adapter", unavailable)
    try:
        turn, _ = runtime.enqueue_turn(session_id=sid, client_turn_id="account-outage",
            message="Original work", work_dir=tmp_path, objective="Research")
        failed = runtime.wait_for_turn(session_id=sid, turn_id=turn["turn_id"], timeout_sec=5)
        assert failed["error_code"] == "model_auth_unavailable"
        assert failed["started_at"] is None and failed["first_event_at"] is None
        assert [e["kind"] for e in store.events_after(sid, turn["turn_id"], None)] == ["turn.queued", "turn.failed"]
    finally:
        runtime.close()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("fault", [None, "route-unavailable", "stop", "source-changed", "duplicate", "admission-crash"])
def test_original_project_worker_recovers_and_returns_exact_source(ordinary, monkeypatch, legacy, fault):  # noqa: F811
    from loopx.capabilities.native_chat import codex_auth
    store, runtime, provider, transport = connect(ordinary)
    ensure = runtime._ensure_adapter

    def unavailable(*_args, **_kwargs):
        raise CodexChatAgentError("The previous Agent conversation could not be restored." if legacy
            else "Native account API transport is unavailable.",
            error_code="resume_failed" if legacy else "model_auth_unavailable", gate=None)

    monkeypatch.setattr(runtime, "_ensure_adapter", unavailable)
    try:
        event = provider.event("notes-app", "original-account-outage", "Explain this original material")
        transport.admit("notes-app", event)
        row = transport.core.pending()[0]
        failed = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert failed["started_at"] is None
        transport.reconcile()
        path = transport.root / f"{row['request_ref']}.json"
        original_delivery = _read_json(path)["deliveries"]["terminal"]
        assert "准备执行器连接时失败" in original_delivery["text"]
        assert "剩余次数接续" in original_delivery["text"]
        original_bytes = store._turn_path(row["session_id"], row["turn_id"]).read_bytes()
        monkeypatch.setattr(runtime, "_ensure_adapter", ensure)
        monkeypatch.setattr(codex_auth, "native_api_transport", lambda *_a, **_kw: None)
        advance_recovery_clock(monkeypatch)
        if fault == "route-unavailable":
            monkeypatch.setattr(codex_auth, "native_api_transport", lambda *_a, **_kw:
                (_ for _ in ()).throw(codex_auth.CodexHostAuthUnavailable()))
        elif fault == "source-changed":
            provider.messages[event["message_id"]]["content"] = "Different original question"
        elif fault == "stop":
            transport.admit("notes-app", provider.event("notes-app", "later-stop", "/stop"))
        elif fault == "duplicate":
            transport.admit("notes-app", provider.event("notes-app", "resubmitted", row["message"]))
            later = next(x for x in transport.core.pending() if x["request_ref"] != row["request_ref"])
            runtime.wait_for_turn(session_id=later["session_id"], turn_id=later["turn_id"], timeout_sec=10)
        elif fault == "admission-crash":
            enqueue = runtime.enqueue_turn

            def committed(**kwargs):
                enqueue(**kwargs)
                monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
                raise OSError("crash after committed continuation")

            monkeypatch.setattr(runtime, "enqueue_turn", committed)
            with pytest.raises(OSError, match="committed continuation"):
                transport.reconcile_request(path)
        transport.reconcile_request(path)
        current = transport.core.read_request(row["request_ref"])
        if fault in {"route-unavailable", "stop", "source-changed", "duplicate"}:
            assert current["turn_id"] == row["turn_id"] and not current.get("context_recovery")
            return
        assert current["session_id"] == row["session_id"] and current["turn_id"] != row["turn_id"]
        assert current["context_recovery"]["preparation_attempt"] == 1
        result = runtime.wait_for_turn(session_id=row["session_id"], turn_id=current["turn_id"], timeout_sec=15)
        assert result["status"] == "completed"
        transport.reconcile()
        record = _read_json(path)
        final = next(v for k, v in record["deliveries"].items() if k.startswith("terminal.recovery."))
        assert final["verified"] and final["text"] == result["response"]["message"]
        actual = provider.messages[final["attempt"]["message_ref"]]
        assert actual["chat_id"] == event["chat_id"]
        assert actual["sender"]["id"] == provider.profile_apps["notes-app"]
        assert record["deliveries"]["terminal"] == original_delivery
        assert store._turn_path(row["session_id"], row["turn_id"]).read_bytes() == original_bytes
        before = ordinary[4].read_bytes()
        transport.reconcile()
        assert ordinary[4].read_bytes() == before
        assert sum(json.loads(line)["method"] == "turn/start" for line in before.decode().splitlines()) == 1
    finally:
        runtime.close()


def test_antigravity_steward_active_cascade_resume_failed_waits_for_idle_then_recovers(
    steward, language_server, monkeypatch,  # noqa: F811
):
    from loopx.capabilities.steward_executor import (
        STEWARD_EXECUTOR_SCHEMA,
        effective_steward_executor_defaults,
    )

    store, runtime, provider, transport, binding, _capture, _workspace = steward
    defaults = effective_steward_executor_defaults({
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {"steward_executor": {
            "schema_version": STEWARD_EXECUTOR_SCHEMA,
            "selection_policy": "preferred",
            "executor_endpoint": "antigravity",
            "eligible_endpoints": [],
            "executor_model": "gemini-4-argon-high",
            "executor_reasoning_effort": "high",
        }},
    })
    monkeypatch.setattr(runtime, "steward_executor_defaults", lambda: defaults)
    transport.bindings.configure(
        transport_ref="steward-app",
        project_ref=binding["project_ref"],
        executor_endpoint_id="antigravity",
        context_kind="steward",
    )

    assert transport.admit("steward-app", provider.event("steward-app", "warmup", "Warm up cascade"))["status"] == "durably_accepted"
    warmup = transport.core.pending()[0]
    assert finish(runtime, warmup)["status"] == "completed"
    assert transport.reconcile() >= 1

    # Simulate an active orphaned cascade while Steward's second request enters _run_turn.
    language_server["mode"] = "running"
    language_server["active"] = True
    assert transport.admit("steward-app", provider.event("steward-app", "active-msg", "Daily report and monitors"))["status"] == "durably_accepted"
    row = next(item for item in transport.core.pending() if item["request_ref"] != warmup["request_ref"])
    failed = finish(runtime, row)
    assert failed["status"] == "failed" and failed["error_code"] == "resume_failed"
    assert failed["upstream_turn_id"] is None and failed["delta_count"] == 0
    kinds = [ev["kind"] for ev in store.events_after(row["session_id"], failed["turn_id"], None)]
    assert kinds[0] == "turn.queued" and "manager.context" in kinds[1:-1] and kinds[-1] == "turn.failed"
    assert transport.reconcile() >= 1
    assert transport.core.read_request(row["request_ref"])["delivery_verified"] is True

    advance_recovery_clock(monkeypatch)
    # While the cascade remains RUNNING, auto-recovery stays candidate-only and defers admission.
    assert transport.core.context_recovery_candidate(row["request_ref"]) is True
    transport.reconcile()
    assert transport.core.read_request(row["request_ref"])["turn_id"] == failed["turn_id"]

    # Once the cascade becomes IDLE, auto-recovery admits the single continuation and delivers.
    language_server["active"] = False
    language_server["mode"] = "complete"
    transport.reconcile_request(transport.root / f"{row['request_ref']}.json")
    current = transport.core.read_request(row["request_ref"])
    assert current["turn_id"] != failed["turn_id"]
    assert current["context_recovery"]["preparation_attempt"] == 1
    assert finish(runtime, current)["status"] == "completed"
    transport.reconcile()
    record = _read_json(transport.root / f"{row['request_ref']}.json")
    assert any(key.startswith("terminal.recovery.") and value.get("verified")
               for key, value in record["deliveries"].items())


def test_antigravity_preparation_waits_while_model_catalog_unready_without_spending_attempts(
    steward, language_server, monkeypatch,  # noqa: F811
):
    from loopx.capabilities.steward_executor import (
        STEWARD_EXECUTOR_SCHEMA,
        effective_steward_executor_defaults,
    )

    store, runtime, provider, transport, binding, _capture, _workspace = steward
    defaults = effective_steward_executor_defaults({
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {"steward_executor": {
            "schema_version": STEWARD_EXECUTOR_SCHEMA,
            "selection_policy": "preferred",
            "executor_endpoint": "antigravity",
            "eligible_endpoints": [],
            "executor_model": "gemini-4-argon-high",
            "executor_reasoning_effort": "high",
        }},
    })
    monkeypatch.setattr(runtime, "steward_executor_defaults", lambda: defaults)
    transport.bindings.configure(
        transport_ref="steward-app",
        project_ref=binding["project_ref"],
        executor_endpoint_id="antigravity",
        context_kind="steward",
    )

    assert transport.admit("steward-app", provider.event("steward-app", "warmup-catalog", "Warm up cascade"))["status"] == "durably_accepted"
    warmup = transport.core.pending()[0]
    assert finish(runtime, warmup)["status"] == "completed"
    assert transport.reconcile() >= 1
    language_server["active"] = False

    # Simulate a process restart / cold open before FetchAvailableModels completes (empty catalog).
    with runtime.lock:
        cached_adapters = list(runtime.adapters.values())
        runtime.adapters.clear()
    for cached in cached_adapters:
        cached.close_session()
    language_server["model_configs"] = []
    assert transport.admit("steward-app", provider.event("steward-app", "unready-models", "Replay after startup"))["status"] == "durably_accepted"
    row = next(item for item in transport.core.pending() if item["request_ref"] != warmup["request_ref"])
    failed = finish(runtime, row)
    assert failed["status"] == "failed" and failed["error_code"] == "resume_failed"
    assert transport.reconcile() >= 1
    assert transport.core.read_request(row["request_ref"])["delivery_verified"] is True

    advance_recovery_clock(monkeypatch)
    # While model configs remain unready, auto-recovery stays candidate-only without burning attempts.
    assert transport.core.context_recovery_candidate(row["request_ref"]) is True
    transport.reconcile()
    current = transport.core.read_request(row["request_ref"])
    assert current["turn_id"] == failed["turn_id"]
    assert not current.get("context_recovery")
    assert not any(method == "SendUserCascadeMessage" and "Replay after startup" in json.dumps(body)
                   for method, body in language_server["calls"])

    # Once model catalog loads, attempt 1 succeeds and delivers.
    language_server["model_configs"] = [{"modelId": "gemini-4-argon-high", "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M417"}}]
    transport.reconcile_request(transport.root / f"{row['request_ref']}.json")
    recovered = transport.core.read_request(row["request_ref"])
    assert recovered["turn_id"] != failed["turn_id"]
    assert recovered["context_recovery"]["preparation_attempt"] == 1
    assert finish(runtime, recovered)["status"] == "completed"
    transport.reconcile()
    record = _read_json(transport.root / f"{row['request_ref']}.json")
    assert any(key.startswith("terminal.recovery.") and value.get("verified")
               for key, value in record["deliveries"].items())
