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
