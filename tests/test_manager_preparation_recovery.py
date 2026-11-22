"""The original sender recovers a known pre-dispatch failure, not unknown work."""
from datetime import datetime, timedelta
import json

import pytest

from test_antigravity_steward_adapter import language_server  # noqa: F401
from test_native_steward_private import steward, finish  # noqa: F401
from loopx.chat_store import _read_json
from loopx.chat_manager_context import unavailable_manager_context


def prepare_failure(data, monkeypatch, endpoint):
    import loopx.chat_manager_context as context
    store, runtime, provider, transport, binding, capture, workspace = data
    if endpoint == "antigravity":
        from loopx.capabilities.steward_executor import effective_steward_executor_defaults, STEWARD_EXECUTOR_SCHEMA
        defaults = effective_steward_executor_defaults({"schema_version": "loopx_machine_configuration_v0",
            "namespaces": {"steward_executor": {"schema_version": STEWARD_EXECUTOR_SCHEMA, "selection_policy": "preferred",
                "executor_endpoint": "antigravity", "eligible_endpoints": [],
                "executor_model": "gemini-4-argon-high", "executor_reasoning_effort": "high"}}})
        monkeypatch.setattr(runtime, "steward_executor_defaults", lambda: defaults)
        transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="antigravity", context_kind="steward")
    collect = context.collect_manager_turn_context
    monkeypatch.setattr(context, "collect_manager_turn_context", lambda *_a, **_kw:
                        unavailable_manager_context("external_authorization_changed"))
    assert transport.admit("steward-app", provider.event("steward-app", "context-failure", "Original queued work"))["status"] == "durably_accepted"
    row = transport.core.pending()[0]
    failed = finish(runtime, row)
    assert failed["status"] == "failed" and failed["error_code"] == "manager_authorization_unavailable"
    assert failed["upstream_turn_id"] is None
    transport.reconcile()
    assert transport.core.read_request(row["request_ref"])["delivery_verified"] is True
    record = _read_json(transport.root / f"{row['request_ref']}.json")
    assert "原队列才会限次接续" in record["deliveries"]["terminal"]["text"]
    monkeypatch.setattr(context, "collect_manager_turn_context", collect)
    return row, failed


def advance_recovery_clock(monkeypatch):
    import loopx.capabilities.native_chat.external_conversations as external

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(minutes=2)

    monkeypatch.setattr(external, "datetime", Clock)


@pytest.mark.parametrize("endpoint", ["codex", "antigravity"])
def test_original_sender_retries_preparation_once_then_returns_same_source(
    steward, language_server, monkeypatch, endpoint  # noqa: F811
):
    store, runtime, provider, transport, binding, capture, workspace = steward
    row, failed = prepare_failure(steward, monkeypatch, endpoint)
    old_failure = dict(failed)
    if endpoint == "antigravity":
        assert language_server["sent_count"] == 0
    else:
        assert not any(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines())
    advance_recovery_clock(monkeypatch)
    assert transport.core.context_recovery_candidate(row["request_ref"])
    transport.reconcile()
    current = transport.core.read_request(row["request_ref"])
    assert current["turn_id"] != failed["turn_id"]
    assert current["context_recovery"]["preparation_attempt"] == 1
    assert current["context_recovery"]["failed_turn_id"] == failed["turn_id"]
    continued = runtime.wait_for_turn(session_id=row["session_id"], turn_id=current["turn_id"], timeout_sec=15)
    assert continued["status"] == "completed"
    transport.reconcile()
    record = _read_json(transport.root / f"{row['request_ref']}.json")
    assert record["status"] == "delivered"
    assert record["deliveries"]["terminal"]["verified"]  # Old failure remains.
    assert any(key.startswith("terminal.recovery.") and value.get("verified")
               for key, value in record["deliveries"].items())
    assert store.load_turn(row["session_id"], failed["turn_id"]) == old_failure
    transport.reconcile()
    if endpoint == "antigravity":
        assert language_server["sent_count"] == 1
        prompt = next(body["items"][0]["text"] for method, body in language_server["calls"]
                      if method == "SendUserCascadeMessage")
        assert "request_recovery" in prompt and "preserve completed work" in prompt
    else:
        assert sum(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines()) == 1


def test_later_human_resubmission_prevents_duplicate_preparation_work(steward, monkeypatch):  # noqa: F811
    store, runtime, provider, transport, *_ = steward
    row, failed = prepare_failure(steward, monkeypatch, "codex")
    assert transport.admit("steward-app", provider.event("steward-app", "later-resubmission", "Original queued work"))["status"] == "durably_accepted"
    later = next(item for item in transport.core.pending() if item["request_ref"] != row["request_ref"])
    assert finish(runtime, later)["status"] == "completed"
    advance_recovery_clock(monkeypatch)
    assert not transport.core.context_recovery_candidate(row["request_ref"])
    transport.reconcile()
    assert transport.core.read_request(row["request_ref"])["turn_id"] == failed["turn_id"]


@pytest.mark.parametrize("fault", ["missing-events", "extra-event", "source-changed", "revoked", "unavailable", "stop"])
def test_original_sender_never_continues_without_exact_proof_and_current_authority(steward, monkeypatch, fault):  # noqa: F811
    store, runtime, provider, transport, binding, capture, _ = steward
    row, failed = prepare_failure(steward, monkeypatch, "codex")
    before = capture.read_bytes()
    advance_recovery_clock(monkeypatch)
    if fault in {"missing-events", "extra-event"}:
        events_after = store.events_after

        def observed(session, turn, cursor, **kwargs):
            events = events_after(session, turn, cursor, **kwargs)
            if turn == failed["turn_id"]:
                return ([] if fault == "missing-events" else events + [
                    {"event_id": "contradictory-native-start", "kind": "turn.started", "payload": {}}])
            return events

        monkeypatch.setattr(store, "events_after", observed)
    elif fault == "source-changed":
        record = _read_json(transport.root / f"{row['request_ref']}.json")
        provider.messages[record["event"]["message_id"]]["content"] = "A newer correction"
    elif fault == "revoked":
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    elif fault == "unavailable":
        provider.owner_ready = False
    else:
        assert transport.admit("steward-app", provider.event("steward-app", "stop-recovery", "/stop"))["status"] == "command_recorded"
    if fault == "unavailable":
        with pytest.raises(ValueError, match="owner|binding|authorized"):
            transport.reconcile()
    else:
        transport.reconcile()
    current = transport.core.read_request(row["request_ref"])
    assert current["turn_id"] == failed["turn_id"]
    assert not current.get("context_recovery")
    assert store.load_turn(row["session_id"], failed["turn_id"]) == failed
    assert capture.read_bytes() == before, "Unproven or revoked recovery cannot call the model"


def test_preparation_notice_is_conditional_and_does_not_promise_unknown_replays():
    from loopx.extensions.lark.manager_context import manager_failure_reply
    failure = {"error_code": "manager_authorization_unavailable"}
    assert "不会自动重放" in manager_failure_reply(failure)[1]
    assert "核验为模型调用前失败" in manager_failure_reply(failure, preparation_recovery=True)[1]
    assert "不会自动重放" in manager_failure_reply({"error_code": "host_gate"}, preparation_recovery=True)[1]


@pytest.mark.parametrize("case", ["before-dispatch", "after-dispatch", "stop", "revoked", "source-changed"])
def test_steward_worker_loss_only_continues_verified_undispatched_input(steward, monkeypatch, case):  # noqa: F811
    import errno
    import threading
    from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
    from loopx.chat import coordination

    store, runtime, provider, transport, binding, capture, _ = steward
    original_context = coordination.prepare_turn_context
    original_execute = ChatExternalConversations.execute_turn
    failed_writer = runtime._fail_turn
    run_turn = runtime._run_turn
    worker_lost = threading.Event()

    def observe_injected_exit(**kwargs):
        try:
            run_turn(**kwargs)
        except OSError as exc:
            assert exc.errno == errno.ENOSPC
            worker_lost.set()

    monkeypatch.setattr(runtime, "_run_turn", observe_injected_exit)

    def unavailable(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "synthetic private path")

    def prepare_then_lose(*args, **kwargs):
        # Retain the real preparation events; they are not provider dispatch.
        original_context(*args, **kwargs)
        unavailable()

    if case == "after-dispatch":
        # Simulate a lost worker at the dispatch boundary. There is no native
        # receipt and no delta, but they cannot prove that the request did not run.
        monkeypatch.setattr(ChatExternalConversations, "execute_turn", staticmethod(unavailable))
        monkeypatch.setattr(runtime, "_fail_turn", unavailable)
    else:
        monkeypatch.setattr(coordination, "prepare_turn_context", prepare_then_lose)

    event = provider.event("steward-app", "original-worker-loss", "Original queued work")
    assert transport.admit("steward-app", event)["status"] == "durably_accepted"
    row = transport.core.pending()[0]
    key = row["session_id"], row["turn_id"]
    # Wait for the worker's kernel fence to be released, before recovery.
    assert worker_lost.wait(timeout=12)
    monkeypatch.setattr(runtime, "_run_turn", run_turn)
    if case == "after-dispatch":
        monkeypatch.setattr(runtime, "_fail_turn", failed_writer)
        monkeypatch.setattr(ChatExternalConversations, "execute_turn", staticmethod(original_execute))
    else:
        monkeypatch.setattr(coordination, "prepare_turn_context", original_context)
    runtime.recover_exited_turn(*key)
    failed = finish(runtime, row)
    assert failed["status"] == "failed" and failed["upstream_turn_id"] is None
    expected = "execution_worker_exited" if case == "after-dispatch" else "execution_worker_exited_before_dispatch"
    assert failed["error_code"] == expected
    transport.reconcile()
    path = transport.root / f"{row['request_ref']}.json"
    notice = _read_json(path)["deliveries"]["terminal"]["text"]
    assert "synthetic private path" not in notice
    assert ("原队列会限次接续" in notice) is (case != "after-dispatch")
    assert not any(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines())
    advance_recovery_clock(monkeypatch)
    if case == "stop":
        assert transport.admit("steward-app", provider.event("steward-app", "stop-worker-loss", "/stop"))["status"] == "command_recorded"
    elif case == "revoked":
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    elif case == "source-changed":
        provider.messages[event["message_id"]]["content"] = "Edited input"
    if case == "revoked":
        with pytest.raises(ValueError, match="no longer authorized"):
            transport.reconcile_request(path)
    elif case == "before-dispatch":
        # Exercise the whole pending-delivery scan, not a direct invocation of
        # the recovery function that would also work if the scheduler missed it.
        transport._context_recovery_probe_at.clear()
        transport.reconcile()
    else:
        transport.reconcile_request(path)
    resumed = transport.core.read_request(row["request_ref"])
    if case == "before-dispatch":
        assert resumed["session_id"] == key[0] and resumed["turn_id"] != key[1]
        assert resumed["context_recovery"]["preparation_attempt"] == 1
        assert finish(runtime, resumed)["status"] == "completed"
        transport.reconcile_request(path)
        transport.reconcile_request(path)
        deliveries = _read_json(path)["deliveries"]
        assert sum(key.startswith("terminal.recovery.") and value.get("verified") is True
                   for key, value in deliveries.items()) == 1
        assert sum(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines()) == 1
    else:
        assert resumed["turn_id"] == key[1] and not resumed.get("context_recovery")
        assert not any(json.loads(line)["method"] == "turn/start" for line in capture.read_text().splitlines())
    assert store.load_turn(*key) == failed


def test_antigravity_steward_recovers_pre_dispatch_resume_failed_after_lost_notice_receipt_without_disk_churn(
    steward, language_server, monkeypatch  # noqa: F811
):
    from loopx.capabilities.steward_executor import STEWARD_EXECUTOR_SCHEMA, effective_steward_executor_defaults
    from loopx.chat_agent import CodexChatAgentError

    store, runtime, provider, transport, binding, capture, _ = steward
    defaults = effective_steward_executor_defaults({"schema_version": "loopx_machine_configuration_v0",
        "namespaces": {"steward_executor": {"schema_version": STEWARD_EXECUTOR_SCHEMA, "selection_policy": "preferred",
            "executor_endpoint": "antigravity", "eligible_endpoints": [],
            "executor_model": "gemini-4-argon-high", "executor_reasoning_effort": "high"}}})
    monkeypatch.setattr(runtime, "steward_executor_defaults", lambda: defaults)
    transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
        executor_endpoint_id="antigravity", context_kind="steward")

    ensure = runtime._ensure_adapter

    def resume_unavailable(*_args, **_kwargs):
        raise CodexChatAgentError("The previous Agent conversation could not be restored.",
            error_code="resume_failed", gate=None)

    monkeypatch.setattr(runtime, "_ensure_adapter", resume_unavailable)
    assert transport.admit("steward-app", provider.event("steward-app", "antigravity-resume-loss", "Analyze repo execution value"))["status"] == "durably_accepted"
    row = transport.core.pending()[0]
    failed = finish(runtime, row)
    assert failed["status"] == "failed" and failed["error_code"] == "resume_failed"
    assert failed["started_at"] is None and failed["upstream_turn_id"] is None

    provider.lose_next_reply = True
    transport.reconcile()
    path = transport.root / f"{row['request_ref']}.json"
    record = _read_json(path)
    assert record["status"] == "accepted"
    assert record["deliveries"]["terminal"]["started"] is True
    assert not record["deliveries"]["terminal"].get("attempt")
    assert not transport.core.read_request(row["request_ref"]).get("delivery_verified")

    # Second reconcile records provider_delivery_receipt_unknown without resending;
    # subsequent ticks before cooldown neither queue it as an unsent body nor rewrite disk.
    writes_before = list(provider.writes)
    transport.reconcile()
    assert provider.writes == writes_before
    record = _read_json(path)
    assert record["deliveries"]["terminal"]["blocker"] == "provider_delivery_receipt_unknown"
    bytes_before = path.read_bytes()
    mtime_before = path.stat().st_mtime_ns
    assert transport.pending_body_paths() == []
    transport._context_recovery_probe_at.clear()
    transport.reconcile()
    assert provider.writes == writes_before
    assert path.read_bytes() == bytes_before
    assert path.stat().st_mtime_ns == mtime_before

    monkeypatch.setattr(runtime, "_ensure_adapter", ensure)
    advance_recovery_clock(monkeypatch)
    transport._context_recovery_probe_at.clear()
    transport.reconcile()

    resumed = transport.core.read_request(row["request_ref"])
    assert resumed["turn_id"] != failed["turn_id"]
    assert resumed["context_recovery"]["preparation_attempt"] == 1
    assert resumed["context_recovery"]["failure_notice_delivery_verified"] is False
    continued = finish(runtime, resumed)
    assert continued["status"] == "completed"
    transport.reconcile()

    final_record = _read_json(path)
    assert final_record["status"] == "delivered"
    assert final_record["deliveries"]["terminal"] == record["deliveries"]["terminal"]
    assert any(key.startswith("terminal.recovery.") and key.endswith(".preparation.1") and value.get("verified") is True
               for key, value in final_record["deliveries"].items())
    assert language_server["sent_count"] == 1
    assert store.load_turn(row["session_id"], failed["turn_id"]) == failed
