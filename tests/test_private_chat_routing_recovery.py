"""The original delivery worker, not a manual replay, recovers routing outages."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from test_native_steward_private import steward, finish  # noqa: F401
from loopx.capabilities.native_chat import external_conversations
from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
from loopx.extensions.lark.conversation_identity import identity_ref
from loopx.extensions.lark.private_conversations import LarkPrivateConversations
from loopx.extensions.lark.private_progress import presentation_phase


@pytest.mark.parametrize("profile", ["notes-app", "steward-app"])
@pytest.mark.parametrize("fault", [None, "changed-source", "revoked", "unknown-effects",
    "reservation-crash", "post-enqueue-crash"])
def test_original_scanner_recovers_across_another_failure_and_restart(steward, monkeypatch, profile, fault):  # noqa: F811
    store, runtime, provider, transport, _, capture, _ = steward
    fake = Path(runtime.codex_bin)
    source = fake.read_text().replace('"codexErrorInfo": "cyberPolicy"', '"codexErrorInfo": "other"')
    source = source.replace('"private-fixture-upstream-detail"',
        '"Error running remote compact task: workspace routing discovery timed out"')
    source = source.replace('            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', '')
    fake.write_text(source)
    binding = next(row for row in transport.bindings.read()["bindings"] if row["transport_ref"] == profile)
    event = provider.event(profile, "routing-outage", "typed terminal failure")
    transport.admit(profile, event)
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    original = transport.core.read_request(ref)
    assert finish(runtime, original)["status"] == "failed"
    failed_path = store._turn_path(original["session_id"], original["turn_id"])
    original_bytes = failed_path.read_bytes()
    transport.reconcile()
    path = transport.root / f"{ref}.json"
    old_notice = json.loads(path.read_text())["deliveries"]["terminal"]
    old_body = dict(provider.messages[old_notice["attempt"]["message_ref"]])

    clock = [datetime.now(timezone.utc) + timedelta(seconds=61)]
    class AfterBackoff(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(external_conversations, "datetime", AfterBackoff)
    monkeypatch.setattr(CodexAppServerAdapter, "failed_request_observation", staticmethod(lambda **kw: None
        if fault == "unknown-effects" else {"thread_id": "durable-thread", "turn_id": kw["turn_id"],
            "codex_error_info": "other", "pre_execution_routing_timeout": True,
            "only_pre_execution_items": True, "only_original_user_item": False}))
    if fault == "changed-source":
        provider.messages[event["message_id"]]["content"] = "Changed requirement"
    elif fault == "revoked":
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])

    def restarted_worker():
        return LarkPrivateConversations(controller=runtime, runtime_root=store.root.parent,
            runner=provider, cli_bin="lark-cli")

    first = restarted_worker()
    if fault in {"reservation-crash", "post-enqueue-crash"}:
        enqueue = runtime.enqueue_turn
        def crash(**kwargs):
            if fault == "post-enqueue-crash":
                enqueue(**kwargs)
            raise OSError("simulated admission crash")
        monkeypatch.setattr(runtime, "enqueue_turn", crash)
        with pytest.raises(OSError, match="admission crash"):
            first.reconcile()
        monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
        first = restarted_worker()
    first.reconcile()
    continued = first.core.read_request(ref)
    if fault in {"changed-source", "revoked", "unknown-effects"}:
        assert continued["turn_id"] == original["turn_id"]
        assert not continued.get("context_recovery")
        assert failed_path.read_bytes() == original_bytes
        return
    assert continued["turn_id"] != original["turn_id"]
    assert continued["context_recovery"]["routing_attempt"] == 1
    assert finish(runtime, continued)["status"] == "failed"
    first.reconcile_request(path)
    assert first.core.read_request(ref)["delivery_verified"]
    assert not first.core.context_recovery_candidate(ref)  # The next backoff survives a scanner restart.
    first_notice = json.loads(path.read_text())["deliveries"][presentation_phase(continued, "terminal")]
    second_failed = store._turn_path(continued["session_id"], continued["turn_id"])
    second_bytes = second_failed.read_bytes()
    runtime.adapters.pop(original["session_id"]).close_session()
    fake.write_text(source.replace('if "typed terminal failure" in json.dumps(request.get("params") or {}):', 'if False:'))
    clock[0] += timedelta(minutes=5)
    second = restarted_worker()
    second.reconcile()
    recovered = second.core.read_request(ref)
    assert recovered["context_recovery"]["routing_attempt"] == 2, second.core._context_recovery_plan(recovered)
    assert recovered["context_recovery_history"] == [continued["context_recovery"]]
    assert recovered["session_id"] == original["session_id"]
    assert finish(runtime, recovered)["status"] == "completed"
    second.reconcile_request(path)
    delivered = json.loads(path.read_text())
    assert delivered["deliveries"]["terminal"] == old_notice
    assert delivered["deliveries"][presentation_phase(continued, "terminal")] == first_notice
    assert delivered["deliveries"][presentation_phase(recovered, "terminal")]["verified"]
    assert provider.messages[old_notice["attempt"]["message_ref"]] == old_body
    assert failed_path.read_bytes() == original_bytes and second_failed.read_bytes() == second_bytes
    before = list(provider.writes)
    second.reconcile_request(path)
    restarted_worker().reconcile()
    assert provider.writes == before
    starts = [json.loads(line) for line in capture.read_text().splitlines()
        if json.loads(line).get("method") == "turn/start"]
    assert len(starts) == 3 and len(store.list_sessions()) == 1
